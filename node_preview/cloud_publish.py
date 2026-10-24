"""Publish the checked, compact node ranking to OSS without third-party packages.

Only explicit public objects are supported. No directory walk, bucket listing,
deletion, database upload, archive upload, or full-address export is performed.
Compatible updates commit JSON last with one atomic OSS PutObject request.
Initial migration installs the runtime and verified JSON before entry pages.

Configuration belongs in a private EnvironmentFile (mode 0600), never Git:
  ALIBABA_CLOUD_ACCESS_KEY_ID, ALIBABA_CLOUD_ACCESS_KEY_SECRET
  OSS_BUCKET, OSS_ENDPOINT, OSS_REGION, OSS_PREFIX
The existing ALIYUN_OSS_* names are also accepted. Optional temporary credentials
use ALIBABA_CLOUD_SECURITY_TOKEN. Bucket-root writes require an explicit flag.

Signing follows the OSS V4 documentation and the official Python V2 SDK:
https://www.alibabacloud.com/help/en/oss/developer-reference/recommend-to-use-signature-version-4
https://github.com/aliyun/alibabacloud-oss-python-sdk-v2/blob/master/alibabacloud_oss_v2/signer/v4.py
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import random
import re
import shlex
import stat
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

from node_preview.bootstrap import atomic_json


STATIC_NAMES = ("index.html", "m/index.html", "live_preview.js")
MAX_OBJECT_BYTES = 4 * 1024 * 1024
NO_CACHE = "no-store, no-cache, max-age=0, must-revalidate"
ENV_NAMES = frozenset({
    "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "ALIBABA_CLOUD_SECURITY_TOKEN", "ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET",
    "ALIYUN_SECURITY_TOKEN", "OSS_BUCKET", "OSS_ENDPOINT", "OSS_REGION", "OSS_PREFIX",
    "ALIYUN_OSS_BUCKET", "ALIYUN_OSS_ENDPOINT", "ALIYUN_OSS_REGION", "ALIYUN_OSS_PREFIX",
})


class SnapshotUnavailable(RuntimeError):
    """A checked, fresh publisher snapshot is not available yet."""


class OSSFailure(RuntimeError):
    def __init__(self, status: int, code: str = "", request_id: str = ""):
        # Do not include the XML Message/StringToSign or any signed headers.
        self.status = status
        super().__init__(f"OSS HTTP {status}; code={code or 'unknown'}; request_id={request_id or 'unavailable'}")


def load_env_file(path: Path) -> None:
    """Read simple KEY=value settings without executing shell text."""
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
            or info.st_uid != os.getuid()):
        raise ValueError("Publish environment file must be an owned regular file with mode 0600")
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip() not in ENV_NAMES:
            raise ValueError(f"Unsupported publish environment setting at line {number}")
        values = shlex.split(value, comments=True, posix=True)
        if len(values) > 1:
            raise ValueError(f"Quote environment values containing spaces at line {number}")
        os.environ[key.strip()] = values[0] if values else ""


def setting(*names: str) -> str:
    return next((os.environ[name].strip() for name in names if os.environ.get(name, "").strip()), "")


@dataclass(frozen=True)
class Config:
    bucket: str
    endpoint: str
    region: str
    prefix: str
    access_id: str = field(repr=False)
    access_secret: str = field(repr=False)
    security_token: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, *, allow_root: bool, credentials: bool = True) -> Config:
        bucket = setting("OSS_BUCKET", "ALIYUN_OSS_BUCKET")
        endpoint = setting("OSS_ENDPOINT", "ALIYUN_OSS_ENDPOINT")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
            raise ValueError("OSS_BUCKET must be a valid bucket name")
        parsed = urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
        host = parsed.hostname or ""
        if host.startswith(bucket + "."):
            host = host[len(bucket) + 1:]
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or not re.fullmatch(r"oss-[a-z0-9-]+\.aliyuncs\.com", host)):
            raise ValueError("OSS_ENDPOINT must be a regional HTTPS aliyuncs.com OSS endpoint")
        match = re.fullmatch(r"oss-([a-z]+-[a-z0-9-]+?)(?:-internal)?\.aliyuncs\.com", host)
        region = setting("OSS_REGION", "ALIYUN_OSS_REGION") or (match.group(1) if match else "")
        if not re.fullmatch(r"[a-z]+-[a-z0-9-]+", region):
            raise ValueError("Set OSS_REGION to the bucket region, e.g. cn-hangzhou")
        # An explicitly empty OSS_PREFIX means the bucket root, even when a
        # legacy ALIYUN_OSS_PREFIX happens to exist in the process environment.
        prefix = os.environ.get("OSS_PREFIX", os.environ.get("ALIYUN_OSS_PREFIX", "")).strip().strip("/")
        if prefix and (not re.fullmatch(r"[A-Za-z0-9._/-]+", prefix)
                       or any(part in ("", ".", "..") for part in prefix.split("/"))):
            raise ValueError("OSS_PREFIX must contain plain, safe object path components")
        if not prefix and not allow_root:
            raise ValueError("Bucket-root publishing requires --allow-bucket-root")
        access_id = setting("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_ID")
        secret = setting("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ALIYUN_ACCESS_KEY_SECRET")
        token = setting("ALIBABA_CLOUD_SECURITY_TOKEN", "ALIYUN_SECURITY_TOKEN")
        if credentials and (not access_id or not secret):
            raise ValueError("Publish credentials are missing from the private environment")
        if any(any(char in value for char in "\r\n") for value in (access_id, secret, token)):
            raise ValueError("Invalid publish credential format")
        return cls(bucket, host, region, prefix, access_id, secret, token)

    def key(self, name: str) -> str:
        if name not in {"index.html", "m/index.html", "m/", "data/latest.json"} and not re.fullmatch(
                r"assets/node-[0-9a-f]{64}/live_preview\.js", name):
            raise ValueError("Object is outside the explicit public publishing allowlist")
        return f"{self.prefix}/{name}" if self.prefix else name

    @property
    def target(self) -> str:
        return f"https://{self.bucket}.{self.endpoint}/" + (self.prefix + "/" if self.prefix else "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # An endpoint/region error must not forward credentials to another host.
        return None


class OSS:
    def __init__(self, config: Config, timeout: int = 20):
        self.config = config
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def sign(self, method: str, key: str, headers: dict[str, str]) -> dict[str, str]:
        """Sign a query-free object request using OSS4-HMAC-SHA256."""
        config = self.config
        signed = {name.lower(): value.strip() for name, value in headers.items()}
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        signed.update({"x-oss-date": stamp, "x-oss-content-sha256": "UNSIGNED-PAYLOAD"})
        if config.security_token:
            signed["x-oss-security-token"] = config.security_token
        additional = sorted(name for name in ("cache-control", "content-length") if name in signed)
        mandatory = {name for name in signed if name.startswith("x-oss-") or name in ("content-type", "content-md5")}
        canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in sorted(mandatory | set(additional)))
        uri = quote(f"/{config.bucket}/{key}", safe="/")
        canonical = f"{method}\n{uri}\n\n{canonical_headers}\n{';'.join(additional)}\nUNSIGNED-PAYLOAD"
        scope = f"{stamp[:8]}/{config.region}/oss/aliyun_v4_request"
        string_to_sign = f"OSS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        signing_key = ("aliyun_v4" + config.access_secret).encode()
        for component in (stamp[:8], config.region, "oss", "aliyun_v4_request"):
            signing_key = hmac.new(signing_key, component.encode(), hashlib.sha256).digest()
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = f"OSS4-HMAC-SHA256 Credential={config.access_id}/{scope}"
        if additional:
            authorization += ",AdditionalHeaders=" + ";".join(additional)
        signed["authorization"] = authorization + ",Signature=" + signature
        return signed

    def request(self, method: str, name: str, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> tuple[dict[str, str], bytes]:
        key = self.config.key(name)
        url = f"https://{self.config.bucket}.{self.config.endpoint}/{quote(key, safe='/')}"
        for attempt in range(3):
            request = urllib.request.Request(url, data=body, method=method,
                                            headers=self.sign(method, key, headers or {}))
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    result = response.read(MAX_OBJECT_BYTES + 1)
                    if len(result) > MAX_OBJECT_BYTES:
                        raise ValueError("OSS object exceeds the compact public object size limit")
                    return {name.lower(): value for name, value in response.headers.items()}, result
            except urllib.error.HTTPError as exc:
                code = request_id = ""
                try:
                    details = ET.fromstring(exc.read(16384))
                    for tag in ("Code", "RequestId"):
                        value = details.findtext(tag) or ""
                        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                            if tag == "Code":
                                code = value
                            else:
                                request_id = value
                except (ET.ParseError, OSError):
                    pass
                finally:
                    exc.close()
                failure = OSSFailure(exc.code, code, request_id)
                if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                    raise failure from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise RuntimeError("OSS network request failed after bounded retries") from None
            time.sleep((2 ** attempt) + random.random())
        raise RuntimeError("OSS retry limit reached")

    def head_matches(self, item: PublicObject) -> bool:
        try:
            headers, _ = self.request("HEAD", item.name)
        except OSSFailure as exc:
            if exc.status == 404:
                return False
            raise
        return (headers.get("x-oss-meta-sha256") == item.sha256
                and headers.get("content-length") == str(len(item.body))
                and headers.get("cache-control") == item.cache_control)

    def put_verified(self, item: PublicObject) -> None:
        self.request("PUT", item.name, item.body, {
            "content-type": item.content_type,
            "content-length": str(len(item.body)),
            "content-md5": base64.b64encode(hashlib.md5(item.body).digest()).decode(),
            "cache-control": item.cache_control,
            "x-oss-meta-sha256": item.sha256,
        })
        if not self.head_matches(item):
            raise RuntimeError(f"Remote object metadata verification failed: {item.name}")
        _, downloaded = self.request("GET", item.name)
        if hashlib.sha256(downloaded).hexdigest() != item.sha256:
            raise RuntimeError(f"Remote object body verification failed: {item.name}")


@dataclass(frozen=True)
class PublicObject:
    name: str
    body: bytes = field(repr=False)
    content_type: str
    cache_control: str = NO_CACHE

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


@dataclass(frozen=True)
class Bundle:
    assets: tuple[PublicObject, ...]
    latest: PublicObject
    preview: dict

    def manifest(self, config: Config) -> dict:
        return {"target": config.target, "version": self.preview["version"],
                "source_block": self.preview["source_block"],
                "source_block_timestamp": self.preview["source_block_timestamp"],
                "generated_at": self.preview["generated_at"],
                "public_rows": len(json.loads(self.latest.body)["rows"]),
                "objects": [{"key": config.key(item.name), "bytes": len(item.body),
                             "sha256": item.sha256, "cache_control": item.cache_control}
                            for item in (*self.assets, self.latest)],
                "commit_object": config.key("data/latest.json"),
                "compatible_update_order": [config.key(item.name) for item in (*self.assets, self.latest)],
                "initial_migration_order": [config.key(item.name) for item in (
                    self.assets[0], self.latest, *self.assets[1:])], "deletes": []}


def read_public(site: Path, name: str) -> bytes:
    path = site / name
    if any(part.is_symlink() for part in (site, *path.parents[:len(path.parts) - len(site.parts)], path)):
        raise ValueError("Public deployment files must not be symbolic links")
    if path.resolve() != site.resolve() / name or not path.is_file() or path.stat().st_size > MAX_OBJECT_BYTES:
        raise ValueError(f"Missing, oversized, or unsafe public artifact: {name}")
    return path.read_bytes()


def validate_fresh(preview: dict, max_age: int) -> None:
    now = time.time()
    for name in ("generated_at", "source_block_timestamp", "last_collection_success"):
        stamp = preview.get(name)
        if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                or stamp > now + 60 or now - stamp > max_age):
            raise SnapshotUnavailable(f"Snapshot {name} is missing or older than {max_age} seconds")


def forbid_private(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key = str(key).lower()
            if (any(word in key for word in ("balance", "private_key", "secret", "credential", "access_key",
                                             "rpc_url", "rpc_endpoint", "authorization", "password", "csv"))
                    or key in {"addresses", "all_addresses", "raw_rows", "all_rows"}):
                raise ValueError("Private or full-address data found in public snapshot")
            forbid_private(item)
    elif isinstance(value, list):
        for item in value:
            forbid_private(item)


def load_bundle(root: Path, config: Config, max_age: int) -> Bundle:
    site = root / "site"
    status_path = root / "state/publisher-status.json"
    build_path = site / ".preview-build.json"
    # The producer writes latest.json then its matching report; avoid that short
    # interval and a concurrent shell rebuild without taking its database lock.
    for attempt in range(5):
        before = status_path.read_bytes()
        build_before = build_path.read_bytes()
        latest = read_public(site, "data/latest.json")
        static = {name: read_public(site, name) for name in STATIC_NAMES}
        if before == status_path.read_bytes() and build_before == build_path.read_bytes():
            report = json.loads(before)
            payload = json.loads(latest)
            manifest = json.loads(build_before)
            preview = payload.get("meta", {}).get("node_preview", {})
            if (report.get("state") == "ready" and all(report.get(key) == preview.get(key)
                    for key in ("version", "source_block", "generated_at"))):
                break
        if attempt == 4:
            raise SnapshotUnavailable("Waiting for a ready, matching publisher report; cloud snapshot preserved")
        time.sleep(.1)
    else:
        raise SnapshotUnavailable("Publisher changed while preparing public bundle")
    expected = manifest.get("artifacts") or {}
    if set(expected) != set(STATIC_NAMES) or any(hashlib.sha256(static[name]).hexdigest() != expected[name] for name in STATIC_NAMES):
        raise SnapshotUnavailable("Static build is incomplete or does not match its build manifest")
    if (preview.get("state") != "ready" or report.get("calibration_done") is not True
            or report.get("ranking_source") != "local_node_contract" or report.get("error")
            or report.get("power_sum_verified") is not True
            or not re.fullmatch(r"[0-9a-f]{20}", str(preview.get("version", "")))
            or not isinstance(preview.get("source_block"), int) or preview["source_block"] <= 0
            or not re.fullmatch(r"0x[0-9a-fA-F]{64}", str(preview.get("source_block_hash", "")))):
        raise SnapshotUnavailable("Local-node snapshot has not completed validation")
    # Collector heartbeat is deployment evidence, kept outside public latest.json.
    validated_preview = {**preview, "last_collection_success": report.get("last_collection_success")}
    validate_fresh(validated_preview, max_age)
    meta = payload["meta"]
    if meta.get("paid_download", {}).get("enabled") is not False or meta.get("public_rank_limit") != 100:
        raise ValueError("The public top-100 boundary or disabled download setting is missing")
    if payload.get("view", {}).get("schema") != 1:
        raise ValueError("Unsupported live frontend schema; review before deployment")
    if any(meta.get("preview_metric_sources", {}).get(key, {}).get("status") != "ok"
           for key in ("network_total_power", "network_total_burned_tokens")):
        raise SnapshotUnavailable("Node global values were not verified at the ranking snapshot")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not 0 < len(rows) <= 100 or report.get("public_rows") != len(rows):
        raise ValueError("Public ranking rows are incomplete or exceed 100")
    seen = set()
    previous_power = None
    for rank, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or set(row) != {"rank", "address", "power"}
                or row.get("rank") != rank or not re.fullmatch(r"0x[0-9a-f]{40}", str(row.get("address", "")))
                or not re.fullmatch(r"[0-9]+", str(row.get("power", "")))):
            raise ValueError("Public ranking row is invalid or contains additional private fields")
        power = int(row["power"])
        if row["address"] in seen or power <= 0 or (previous_power is not None and power > previous_power):
            raise ValueError("Public ranking is duplicated or not sorted by positive power")
        seen.add(row["address"])
        previous_power = power
    forbid_private(payload)
    # Content-address the runtime so a cached fixed JS path cannot pair an old
    # program with a newly deployed shell. Only this narrow asset path is allowed.
    runtime = static["live_preview.js"].decode("utf-8")
    base = "/" + (config.prefix + "/" if config.prefix else "")
    if config.prefix:
        runtime = runtime.replace("`/data/latest.json?", "`" + base + "data/latest.json?")
    runtime_bytes = runtime.encode()
    runtime_name = "assets/node-" + hashlib.sha256(runtime_bytes).hexdigest() + "/live_preview.js"
    assets = [PublicObject(runtime_name, runtime_bytes, "application/javascript; charset=utf-8", "public, max-age=31536000, immutable")]
    for name in ("m/index.html", "index.html"):
        html = static[name].decode("utf-8")
        marker = 'src="/live_preview.js"'
        if html.count(marker) != 1:
            raise ValueError("The live runtime script marker changed; review the deployment adapter")
        html = html.replace(marker, 'src="/' + runtime_name + '"')
        if config.prefix:
            # Apply the known dashboard paths without rewriting protocol-relative
            # external URLs or unrelated JavaScript strings and regular expressions.
            html = re.sub(r'((?:href|src)=[\"\x27])/(?!/)', lambda match: match[1] + base, html)
            for path in ("/m/", "/m/index.html", "/index.html", "/"):
                html = html.replace('"' + path + '"', '"' + base + path.lstrip("/") + '"')
        if re.search(r'<[^>]+\sdata-paid-panel(?:\s|=|>)', html):
            raise ValueError("A paid download panel is present in the deployment shell")
        item = PublicObject(name, html.encode(), "text/html; charset=utf-8")
        assets.append(item)
        if name == "m/index.html":
            assets.append(PublicObject("m/", item.body, item.content_type))
    return Bundle(tuple(assets), PublicObject("data/latest.json", latest, "application/json; charset=utf-8"), validated_preview)


def read_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    if not isinstance(result, dict):
        raise ValueError("Publish state file is malformed")
    return result


def publish(root: Path, config: Config, max_age: int, *, dry_run: bool,
            manifest_path: Path | None = None, progress: dict | None = None) -> dict:
    bundle = load_bundle(root, config, max_age)
    manifest = bundle.manifest(config)
    if manifest_path:
        atomic_json(manifest_path, manifest)
    if dry_run:
        return {"state": "dry_run", "production_writes": False, **manifest}
    if progress is None:
        progress = {}
    progress.update({"phase": "checking_remote", "target": config.target,
                     "version": bundle.preview["version"],
                     "latest_json_write_attempted": False, "latest_json_commit": "not_attempted"})

    def record_phase(phase: str, **details: object) -> None:
        progress.update({"phase": phase, "updated_at": int(time.time()), **details})
        atomic_json(root / "state/cloud-publish-progress.json", progress)

    record_phase("checking_remote")
    state_path = root / "state/cloud-publish-state.json"
    saved = read_json(state_path)
    if saved.get("target") != config.target:
        saved = {}
    if int(saved.get("source_block") or 0) > bundle.preview["source_block"]:
        raise SnapshotUnavailable("Refusing to replace a newer cloud block with an older local snapshot")
    client = OSS(config)
    remote = None
    remote_document = {}
    try:
        _, remote = client.request("GET", "data/latest.json")
    except OSSFailure as exc:
        if exc.status != 404:
            raise
    if remote:
        remote_document = json.loads(remote)
        if not isinstance(remote_document, dict):
            raise ValueError("Existing remote snapshot is not a JSON object")
        remote_preview = remote_document.get("meta", {}).get("node_preview", {})
        remote_block = remote_preview.get("source_block")
        if remote_block is not None and (int(remote_block) > bundle.preview["source_block"] or (
                int(remote_block) == bundle.preview["source_block"]
                and int(remote_preview.get("generated_at") or 0) > bundle.preview["generated_at"])):
            raise SnapshotUnavailable("Remote ranking is newer; preserving the cloud snapshot")
    remote_view = remote_document.get("view")
    remote_schema = remote_view.get("schema") if isinstance(remote_view, dict) else None
    first_migration = remote is None or remote_schema != 1
    uploaded, reused = [], []
    objects = dict(saved.get("objects") or {})
    record_phase("preparing_runtime", initial_migration=first_migration,
                 uploaded=uploaded, reused=reused)

    def ensure_asset(item: PublicObject) -> None:
        key = config.key(item.name)
        # Local receipts cannot detect a different writer overwriting OSS. Always
        # inspect the remote object even when this exact digest was uploaded before.
        if client.head_matches(item):
            reused.append(key)
        else:
            client.put_verified(item)
            uploaded.append(key)
        objects[key] = item.sha256
        record_phase(progress["phase"], last_verified_object=key)

    def commit_latest() -> None:
        # A slow asset upload must not turn an old snapshot into a new one.
        validate_fresh(bundle.preview, max_age)
        latest_key = config.key(bundle.latest.name)
        record_phase("checking_latest_json")
        if (remote is not None and hashlib.sha256(remote).hexdigest() == bundle.latest.sha256
                and client.head_matches(bundle.latest)):
            reused.append(latest_key)
            record_phase("latest_json_verified", latest_json_commit="unchanged_verified")
        else:
            # A failed request/verification can still have reached OSS. Record the
            # uncertain phase before sending it; never claim the old JSON survived.
            record_phase("committing_latest_json", latest_json_write_attempted=True,
                         latest_json_commit="attempted_unverified")
            client.put_verified(bundle.latest)
            uploaded.append(latest_key)
            record_phase("latest_json_verified", latest_json_commit="verified")
        objects[latest_key] = bundle.latest.sha256

    # A new entry page immediately runs the new runtime against latest.json.
    # On initial migration its legacy/missing JSON must be seeded first. Old
    # entry pages remain usable: the new JSON retains the legacy meta/rows fields.
    ensure_asset(bundle.assets[0])
    if first_migration:
        commit_latest()
    record_phase("updating_entry_pages")
    for item in bundle.assets[1:]:
        ensure_asset(item)
    if not first_migration:
        commit_latest()
    receipt = {"target": config.target, "version": bundle.preview["version"],
               "source_block": bundle.preview["source_block"],
               "source_block_hash": bundle.preview["source_block_hash"],
               "source_block_timestamp": bundle.preview["source_block_timestamp"],
               "snapshot_generated_at": bundle.preview["generated_at"],
               "verified_at": int(time.time()), "objects": objects,
               "initial_migration": first_migration, "phase": "complete",
               "latest_json_commit": progress["latest_json_commit"]}
    atomic_json(state_path, receipt)
    record_phase("complete")
    return {"state": "published", "production_writes": True, **receipt,
            "uploaded": uploaded, "reused": reused, "remote_body_verified": True,
            "verification_scope": "authenticated OSS origin; CDN caching is configured separately"}


def attempt(root: Path, config: Config, args: argparse.Namespace) -> int:
    status_path = root / "state/cloud-publish-status.json"
    old = read_json(status_path) if not args.dry_run else {}
    now = int(time.time())
    if not args.dry_run and int(old.get("next_attempt_at") or 0) > now:
        print(json.dumps({"state": "backoff", "next_attempt_at": old["next_attempt_at"]}), flush=True)
        return 0
    progress = {"phase": "preparing", "attempted_at": now,
                "latest_json_write_attempted": False, "latest_json_commit": "not_attempted"}
    try:
        result = publish(root, config, args.max_age, dry_run=args.dry_run,
                         manifest_path=args.manifest, progress=progress)
        result.update({"attempted_at": now, "consecutive_failures": 0})
        code = 0
    except SnapshotUnavailable as exc:
        result = {"state": "waiting_for_publisher", "detail": str(exc), "attempted_at": now,
                  "latest_cloud_snapshot_preserved": not progress["latest_json_write_attempted"],
                  "progress": progress, "consecutive_failures": 0}
        code = 75
    except Exception as exc:
        failures = int(old.get("consecutive_failures") or 0) + 1
        delay = min(900, max(15, args.interval) * (2 ** min(failures - 1, 4)))
        # Exceptions created here are controlled errors. Never serialize a
        # credential-bearing Request or Config, or raw provider response bodies.
        result = {"state": "error", "error": str(exc)[:500], "attempted_at": now,
                  "consecutive_failures": failures, "next_attempt_at": now + delay,
                  "progress": progress,
                  "last_good_publish": read_json(root / "state/cloud-publish-state.json").get("version")}
        code = 1
    if not args.dry_run:
        atomic_json(status_path, result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, help="Owned mode-0600 KEY=value file; optional with systemd EnvironmentFile")
    parser.add_argument("--allow-bucket-root", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Publish once (default)")
    mode.add_argument("--loop", action="store_true", help="Publish independently in a bounded-retry loop")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--max-age", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true", help="Print the explicit manifest without network calls or credentials")
    parser.add_argument("--manifest", type=Path, help="Optional private plan JSON path, never uploaded")
    args = parser.parse_args()
    if not 5 <= args.interval <= 3600 or not 30 <= args.max_age <= 300:
        parser.error("interval must be 5..3600 seconds and max-age must be 30..300 seconds")
    os.umask(0o077)
    if args.env_file:
        load_env_file(args.env_file.expanduser())
    config = Config.from_environment(allow_root=args.allow_bucket_root, credentials=not args.dry_run)
    root = args.root.expanduser().resolve()
    if not (root / "state").is_dir():
        raise ValueError("The configured ranking root has not been initialized")
    with (root / "state/cloud-publish.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"state":"already_running"}', flush=True)
            return 75
        while True:
            began = time.monotonic()
            code = attempt(root, config, args)
            if not args.loop or args.dry_run:
                return code
            time.sleep(max(1, args.interval - (time.monotonic() - began)))


if __name__ == "__main__":
    raise SystemExit(main())
