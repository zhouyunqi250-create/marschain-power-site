"""Back up completed Beijing 08:00 public snapshots on a dedicated GitHub branch.

Only the exact frozen public.json (at most 100 ranking rows) and manifest.json
are allowed out. Full address archives, databases, CSVs and working minute data
are never traversed or uploaded. Git over SSH is the default; REST is optional.
Retries always reuse the original archive bytes and never recalculate a cutoff.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from node_preview.bootstrap import atomic_json

ARCHIVE_NAME = re.compile(r"\d{8}-0800\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MANIFEST_KEYS = {
    "schema", "state", "target_timestamp", "target_beijing", "source_block",
    "source_block_hash", "source_block_timestamp", "safe_head_at_start", "frozen_at",
    "known_count", "processed_count", "address_set_sha256", "complete_for_known_addresses",
    "full_chain_census", "positive_power_count", "discovered_total_power",
    "network_total_power", "public_rows", "production_writes", "daily_statistics_cutoff",
    "daily_statistics_status", "files", "elapsed_seconds",
}


class BackupError(RuntimeError):
    """A safe, credential-free diagnostic suitable for status files and journals."""


class APIError(BackupError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"GitHub API returned HTTP {status}; retry keeps the same frozen snapshot")


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise BackupError("GitHub returned an invalid Git object ID")
    return value


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def _json_bytes(path: Path, limit: int) -> tuple[bytes, dict]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise BackupError(f"Invalid or oversized allowlisted snapshot file: {path.name}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise BackupError(f"Invalid snapshot JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise BackupError(f"Snapshot JSON must be an object: {path.name}")
    return raw, value


def _public_boundary(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(word in normalized for word in (
                "balance", "private_key", "secret", "credential", "access_key",
                "rpc_url", "rpc_endpoint", "authorization", "password", "csv",
            )) or normalized in {"addresses", "all_addresses", "raw_rows", "all_rows", "token",
                                 "github_token", "access_token", "api_token", "auth_token", "bearer_token"}:
                raise BackupError("Frozen public snapshot contains a disallowed private field")
            _public_boundary(item)
    elif isinstance(value, list):
        for item in value:
            _public_boundary(item)


@dataclass(frozen=True)
class Snapshot:
    name: str
    target: int
    source_block: int
    source_hash: str
    files: dict[str, bytes]

    def inventory(self) -> dict:
        return {
            "archive": self.name, "target_timestamp": self.target,
            "source_block": self.source_block, "source_block_hash": self.source_hash,
            "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                              "git_blob": _blob_sha(raw)} for name, raw in self.files.items()},
        }


def read_snapshot(path: Path) -> Snapshot:
    """Validate an immutable freeze archive without opening its private power file."""
    if path.is_symlink() or not path.is_dir() or not ARCHIVE_NAME.fullmatch(path.name):
        raise BackupError("Only completed daily 08:00 archives can be backed up")
    manifest_raw, manifest = _json_bytes(path / "manifest.json", 128 * 1024)
    if set(manifest) - MANIFEST_KEYS:
        raise BackupError("Snapshot manifest has unknown fields; public boundary requires review")
    target = manifest.get("target_timestamp")
    if (type(target) is not int or target < 0 or target % 86400 != 0
            or time.strftime("%Y%m%d-0800", time.gmtime(target)) != path.name):
        raise BackupError("Daily archive name and fixed Beijing cutoff disagree")
    if (manifest.get("schema") != 1 or manifest.get("state") != "complete"
            or manifest.get("complete_for_known_addresses") is not True
            or manifest.get("full_chain_census") is not False
            or manifest.get("known_count", 0) <= 0
            or manifest.get("processed_count") != manifest.get("known_count")):
        raise BackupError("Snapshot is incomplete or its declared address scope is invalid")
    number, block_hash = manifest.get("source_block"), manifest.get("source_block_hash")
    timestamp = manifest.get("source_block_timestamp")
    if (type(number) is not int or number < 0 or not isinstance(block_hash, str)
            or not re.fullmatch(r"0x[0-9a-f]{64}", block_hash)
            or type(timestamp) is not int or not 0 <= timestamp < target):
        raise BackupError("Snapshot lacks a valid pre-cutoff source block and timestamp")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"public.json", "power.jsonl.gz"}:
        raise BackupError("Snapshot manifest file inventory is not the reviewed freeze schema")
    for information in files.values():
        if (not isinstance(information, dict) or set(information) != {"sha256", "bytes"}
                or not isinstance(information["sha256"], str)
                or not SHA256.fullmatch(information["sha256"])
                or type(information["bytes"]) is not int or information["bytes"] < 0):
            raise BackupError("Snapshot file inventory has invalid checksums or sizes")
    public_raw, public = _json_bytes(path / "public.json", 2 * 1024 * 1024)
    if (len(public_raw) != files["public.json"]["bytes"]
            or hashlib.sha256(public_raw).hexdigest() != files["public.json"]["sha256"]):
        raise BackupError("Frozen public.json no longer matches its manifest")
    _public_boundary(public)
    rows, meta = public.get("rows"), public.get("meta")
    if (set(public) != {"meta", "rows", "view"}
            or not isinstance(rows, list) or len(rows) > 100 or not isinstance(meta, dict)
            or manifest.get("public_rows") != len(rows)):
        raise BackupError("Frozen public payload violates the 100-row boundary")
    previous_power = None
    seen = set()
    for rank, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or set(row) != {"rank", "address", "power"}
                or row.get("rank") != rank
                or not re.fullmatch(r"0x[0-9a-f]{40}", str(row.get("address", "")))
                or not re.fullmatch(r"[0-9]+", str(row.get("power", "")))):
            raise BackupError("Frozen public ranking contains a non-allowlisted row")
        power = int(row["power"])
        if row["address"] in seen or (previous_power is not None and power > previous_power):
            raise BackupError("Frozen public ranking is duplicated or not sorted by power")
        seen.add(row["address"])
        previous_power = power
    source = meta.get("node_preview") or {}
    if (source.get("target_timestamp") != target or source.get("source_block") != number
            or source.get("source_block_hash") != block_hash
            or source.get("source_block_timestamp") != timestamp or source.get("state") != "frozen"):
        raise BackupError("Frozen public payload and manifest source blocks disagree")
    return Snapshot(path.name, target, number, block_hash,
                    {"public.json": public_raw, "manifest.json": manifest_raw})


@dataclass(frozen=True)
class Config:
    repository: str
    branch: str = "node-snapshots"
    prefix: str = "snapshots"
    transport: str = "git"

    @classmethod
    def environment(cls, transport: str | None = None) -> Config:
        config = cls(os.environ.get("GITHUB_REPOSITORY", ""),
                     os.environ.get("GITHUB_BACKUP_BRANCH", "node-snapshots"),
                     os.environ.get("GITHUB_BACKUP_PREFIX", "snapshots"),
                     transport or os.environ.get("GITHUB_BACKUP_TRANSPORT", "git"))
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.repository):
            raise BackupError("Set GITHUB_REPOSITORY to the dedicated owner/repository")
        # Restrict names before inserting them into Git refs, paths or API URLs.
        for item in (config.branch, config.prefix):
            if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,150}", item)
                    or any(part in {"", ".", ".."} or part.startswith(".") or part.endswith(".lock")
                           for part in item.split("/")) or ".." in item or item.endswith(".")):
                raise BackupError("Invalid dedicated backup branch or path prefix")
        if config.branch in {"main", "master", "gh-pages"} or config.prefix == ".github":
            raise BackupError("Backups must use a dedicated snapshot branch and directory")
        if config.transport not in {"git", "rest"}:
            raise BackupError("GITHUB_BACKUP_TRANSPORT must be git or rest")
        return config

    def paths(self, snapshot: Snapshot) -> dict[str, bytes]:
        return {f"{self.prefix}/{snapshot.name}/{name}": data for name, data in snapshot.files.items()}

    def identity(self) -> str:
        return hashlib.sha256(f"{self.repository}\n{self.branch}\n{self.prefix}".encode()).hexdigest()[:20]


class GitTransport:
    """Bare Git store; a repo-only SSH deploy key stays on the Ubuntu server."""
    def __init__(self, config: Config, state: Path):
        self.config = config
        self.git = os.environ.get("GIT_EXECUTABLE", "git")
        if not shutil.which(self.git):
            raise BackupError("Git is unavailable; configure GIT_EXECUTABLE with the installed server Git")
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"):
            self.env.pop(key, None)
        self.location = state / f"{config.identity()}.git"
        self.origin = f"git@github.com:{config.repository}.git"
        if not self.location.exists():
            self._git(["init", "--bare", str(self.location)], bare=False)
        if self.location.is_symlink():
            raise BackupError("Backup Git repository must not be a symlink")
        os.chmod(self.location, 0o700)
        current = self._git(["remote", "get-url", "origin"], allow_failure=True)
        if current is None:
            self._git(["remote", "add", "origin", self.origin])
        elif current.decode().strip() != self.origin:
            raise BackupError("Backup Git repository origin differs from configured GitHub repository")
        # Do not accept arbitrary push URL overrides from an old local config.
        self._git(["config", "--local", "remote.origin.pushurl", self.origin])

    def _git(self, args: list[str], data: bytes | None = None, *, bare: bool = True,
             allow_failure: bool = False, env: dict | None = None) -> bytes | None:
        command = [self.git] + (["--git-dir", str(self.location)] if bare else []) + args
        try:
            result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env or self.env, check=False, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackupError("Git command could not finish; check the server SSH key and network") from exc
        if result.returncode:
            if allow_failure:
                return None
            # Git stderr may include account/SSH diagnostics. Do not persist raw
            # subprocess output or any environment value in journals/status JSON.
            raise BackupError("Git command failed; check repository deploy-key permission and branch policy")
        return result.stdout

    def _head(self) -> str | None:
        listing = self._git(["ls-remote", "--symref", "origin", "HEAD", f"refs/heads/{self.config.branch}"])
        parent = None
        default = None
        for line in listing.decode().splitlines():
            value, ref = line.split("\t", 1)
            if value.startswith("ref: refs/heads/") and ref == "HEAD":
                default = value.removeprefix("ref: refs/heads/")
            elif ref == f"refs/heads/{self.config.branch}":
                parent = _sha(value)
        if default is None:
            raise BackupError("Cannot establish the remote default branch; refusing a snapshot push")
        if default == self.config.branch:
            raise BackupError("Configured backup branch is the repository default branch")
        if parent is None:
            return None
        self._git(["fetch", "--no-tags", "--depth=1", "origin", f"refs/heads/{self.config.branch}"])
        # FETCH_HEAD is authoritative if the branch advanced after ls-remote.
        return _sha(self._git(["rev-parse", "FETCH_HEAD"]).decode().strip())

    def _existing(self, commit: str | None, paths: dict[str, bytes]) -> bool:
        if commit is None:
            return False
        listing = self._git(["ls-tree", "-r", "-z", commit, "--", *paths])
        found = {}
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            info, name = entry.split(b"\t", 1)
            mode, kind, sha = info.split()
            if mode != b"100644" or kind != b"blob":
                raise BackupError("A snapshot path on GitHub is not a regular file")
            found[name.decode()] = _sha(sha.decode())
        if not found:
            return False
        if set(found) != set(paths):
            raise BackupError("GitHub contains a partial snapshot; immutable archive requires review")
        for path, raw in paths.items():
            if found[path] != _blob_sha(raw) or self._git(["cat-file", "blob", found[path]]) != raw:
                raise BackupError("GitHub already contains different bytes for this fixed daily snapshot")
        return True

    def publish(self, snapshot: Snapshot) -> dict:
        paths = self.config.paths(snapshot)
        parent = self._head()
        if self._existing(parent, paths):
            return {"commit": parent, "already_uploaded": True, "verified": True}
        with tempfile.TemporaryDirectory(prefix="index-", dir=self.location.parent) as temporary:
            env = dict(self.env, GIT_INDEX_FILE=str(Path(temporary) / "index"),
                       GIT_AUTHOR_NAME=os.environ.get("GITHUB_BACKUP_AUTHOR_NAME", "MarsChain snapshot backup"),
                       GIT_AUTHOR_EMAIL=os.environ.get("GITHUB_BACKUP_AUTHOR_EMAIL", "snapshots@marschainrank.com"),
                       GIT_COMMITTER_NAME=os.environ.get("GITHUB_BACKUP_AUTHOR_NAME", "MarsChain snapshot backup"),
                       GIT_COMMITTER_EMAIL=os.environ.get("GITHUB_BACKUP_AUTHOR_EMAIL", "snapshots@marschainrank.com"))
            self._git(["read-tree", parent] if parent else ["read-tree", "--empty"], env=env)
            for path, raw in paths.items():
                sha = _sha(self._git(["hash-object", "-w", "--stdin"], raw).decode().strip())
                if sha != _blob_sha(raw):
                    raise BackupError("Local Git blob hash does not match frozen file bytes")
                self._git(["update-index", "--add", "--cacheinfo", f"100644,{sha},{path}"], env=env)
            tree = _sha(self._git(["write-tree"], env=env).decode().strip())
            message = f"Snapshot {snapshot.name} at block {snapshot.source_block} [skip ci]\n".encode()
            commit = _sha(self._git(["commit-tree", tree] + (["-p", parent] if parent else []), message, env=env).decode().strip())
            # No force flag or leading '+': concurrent writers are rejected.
            self._git(["push", "--porcelain", "origin", f"{commit}:refs/heads/{self.config.branch}"])
        remote = self._head()
        if not self._existing(remote, paths):
            raise BackupError("GitHub branch did not retain the uploaded snapshot; retry without replacing it")
        return {"commit": commit, "verified_head": remote, "already_uploaded": False, "verified": True}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BackupError("GitHub API redirect refused; check the repository owner/name")


class RestTransport:
    """Optional Contents-write, repository-scoped token transport; no Git needed."""
    def __init__(self, config: Config, state: Path):
        self.config = config
        self.token = os.environ.get("GITHUB_TOKEN", "")
        if not self.token:
            raise BackupError("REST backup requires a repository-scoped GITHUB_TOKEN")
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.prefix = f"https://api.github.com/repos/{config.repository}"
        repository = self._call("GET", "")
        if (repository.get("full_name", "").lower() != config.repository.lower()
                or repository.get("default_branch") == config.branch):
            raise BackupError("Backup must target the configured repository and a non-default branch")

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        raw = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(self.prefix + path, data=raw, method=method, headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "MarsChain-Snapshot-Backup",
            "Content-Type": "application/json",
        })
        try:
            with self.opener.open(request, timeout=40) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            # Never echo request headers, token, or untrusted response body.
            raise APIError(exc.code) from None
        except (OSError, ValueError) as exc:
            raise BackupError("GitHub API request failed; frozen snapshot remains queued") from exc
        if not isinstance(result, dict):
            raise BackupError("GitHub API returned an unexpected JSON shape")
        return result

    def _head(self) -> str | None:
        try:
            result = self._call("GET", "/git/ref/heads/" + urllib.parse.quote(self.config.branch, safe="/"))
        except APIError as exc:
            if exc.status == 404:
                return None
            raise
        if result.get("ref") != "refs/heads/" + self.config.branch or result.get("object", {}).get("type") != "commit":
            raise BackupError("GitHub returned a different backup reference")
        return _sha(result["object"]["sha"])

    def _tree(self, commit: str) -> str:
        return _sha(self._call("GET", f"/git/commits/{commit}")["tree"]["sha"])

    def _existing(self, tree: str | None, paths: dict[str, bytes]) -> bool:
        if tree is None:
            return False
        cache, found = {}, {}
        for path in paths:
            current = tree
            parts = path.split("/")
            for index, part in enumerate(parts):
                if current not in cache:
                    listing = self._call("GET", f"/git/trees/{current}")
                    if listing.get("truncated"):
                        raise BackupError("GitHub tree is truncated; cannot verify a snapshot safely")
                    cache[current] = {entry["path"]: entry for entry in listing["tree"]}
                entry = cache[current].get(part)
                if entry is None:
                    break
                if index == len(parts) - 1:
                    if entry.get("type") != "blob" or entry.get("mode") != "100644":
                        raise BackupError("A snapshot path on GitHub is not a regular file")
                    found[path] = _sha(entry["sha"])
                else:
                    if entry.get("type") != "tree":
                        raise BackupError("A snapshot directory on GitHub is not a tree")
                    current = _sha(entry["sha"])
        if not found:
            return False
        if set(found) != set(paths):
            raise BackupError("GitHub contains a partial snapshot; immutable archive requires review")
        for path, raw in paths.items():
            if found[path] != _blob_sha(raw):
                raise BackupError("GitHub already contains different bytes for this fixed daily snapshot")
            blob = self._call("GET", f"/git/blobs/{found[path]}")
            if blob.get("encoding") != "base64":
                raise BackupError("Cannot verify uploaded GitHub blob encoding")
            try:
                recovered = base64.b64decode(blob["content"])
            except (ValueError, KeyError) as exc:
                raise BackupError("Cannot decode uploaded GitHub blob") from exc
            if recovered != raw:
                raise BackupError("Uploaded GitHub blob bytes differ from the frozen snapshot")
        return True

    def publish(self, snapshot: Snapshot) -> dict:
        paths = self.config.paths(snapshot)
        parent = self._head()
        base = self._tree(parent) if parent else None
        if self._existing(base, paths):
            return {"commit": parent, "already_uploaded": True, "verified": True}
        entries = []
        for path, raw in paths.items():
            blob = self._call("POST", "/git/blobs", {"encoding": "base64", "content": base64.b64encode(raw).decode()})
            sha = _sha(blob["sha"])
            if sha != _blob_sha(raw):
                raise BackupError("Uploaded GitHub blob hash differs from frozen bytes")
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
        body = {"tree": entries}
        if base:
            body["base_tree"] = base
        tree = _sha(self._call("POST", "/git/trees", body)["sha"])
        commit = _sha(self._call("POST", "/git/commits", {
            "message": f"Snapshot {snapshot.name} at block {snapshot.source_block} [skip ci]",
            "tree": tree, "parents": [parent] if parent else [],
        })["sha"])
        if parent:
            self._call("PATCH", "/git/refs/heads/" + urllib.parse.quote(self.config.branch, safe="/"),
                       {"sha": commit, "force": False})
        else:
            self._call("POST", "/git/refs", {"ref": "refs/heads/" + self.config.branch, "sha": commit})
        remote = self._head()
        if remote is None or not self._existing(self._tree(remote), paths):
            raise BackupError("GitHub branch verification failed; frozen snapshot remains queued")
        return {"commit": commit, "verified_head": remote, "already_uploaded": False, "verified": True}


def _receipt_matches(path: Path, expected: dict) -> bool:
    if not path.exists():
        return False
    try:
        receipt = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise BackupError("Backup receipt is unreadable; original archive remains queued") from exc
    if receipt.get("inventory") != expected or receipt.get("verified") is not True:
        raise BackupError("Previously backed-up archive bytes changed; refusing an immutable snapshot replacement")
    _sha(receipt.get("commit"))
    return True


def backup(root: Path | str, *, dry_run: bool = False, transport: str | None = None,
           max_snapshots: int = 7) -> dict:
    root = Path(root).expanduser().resolve()
    config = Config.environment(transport)
    if not 1 <= max_snapshots <= 100:
        raise BackupError("max_snapshots must be 1..100")
    archives = root / "archives"
    candidates = sorted(path for path in archives.iterdir() if ARCHIVE_NAME.fullmatch(path.name)) if archives.exists() else []
    if dry_run:
        return {"state": "dry_run", "repository": config.repository, "branch": config.branch,
                "transport": config.transport, "network_calls": 0, "remote_writes": False,
                "snapshots": [read_snapshot(path).inventory() for path in candidates],
                "private_exports_uploaded": False}
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    private = state / "github-backup"
    private.mkdir(mode=0o700, exist_ok=True)
    receipts = private / config.identity()
    receipts.mkdir(mode=0o700, exist_ok=True)
    status_path = state / "github-backup-status.json"
    with (private / "backup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "busy", "private_exports_uploaded": False}
        uploaded, failures, skipped, attempted = [], [], 0, 0
        client = None
        for archive in candidates:
            if attempted >= max_snapshots:
                break
            try:
                snapshot = read_snapshot(archive)
                inventory = snapshot.inventory()
                receipt = receipts / f"{snapshot.name}.json"
                if _receipt_matches(receipt, inventory):
                    skipped += 1
                    continue
                attempted += 1
                if client is None:
                    client = (GitTransport if config.transport == "git" else RestTransport)(config, private)
                result = client.publish(snapshot)
                completed = {**result, "inventory": inventory, "repository": config.repository,
                             "branch": config.branch, "uploaded_at": int(time.time()),
                             "private_exports_uploaded": False}
                atomic_json(receipt, completed)
                uploaded.append({"archive": snapshot.name, "source_block": snapshot.source_block,
                                 "commit": result["commit"], "already_uploaded": result["already_uploaded"]})
            except Exception as exc:
                # Only intentional safe diagnostics are emitted. Unknown library
                # exceptions might carry headers or process output: use type only.
                safe = str(exc) if isinstance(exc, BackupError) else f"{type(exc).__name__} while backing up frozen archive"
                failures.append({"archive": archive.name, "error": safe})
        status = {"state": "error" if failures else "complete", "repository": config.repository,
                  "branch": config.branch, "transport": config.transport,
                  "uploaded": uploaded, "already_backed_up": skipped, "failures": failures,
                  "checked_at": int(time.time()), "private_exports_uploaded": False}
        if uploaded:
            status["last_success"] = {**uploaded[-1], "uploaded_at": int(time.time())}
        elif status_path.exists():
            try:
                last = json.loads(status_path.read_text()).get("last_success")
                if last:
                    status["last_success"] = last
            except (ValueError, OSError):
                pass
        atomic_json(status_path, status)
        return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Validate local daily archives; no network calls or writes")
    parser.add_argument("--transport", choices=("git", "rest"))
    parser.add_argument("--max-snapshots", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        result = backup(args.root, dry_run=args.dry_run, transport=args.transport, max_snapshots=args.max_snapshots)
    except Exception as exc:
        result = {"state": "error", "error": str(exc) if isinstance(exc, BackupError) else type(exc).__name__,
                  "private_exports_uploaded": False}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if result["state"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
