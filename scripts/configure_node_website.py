#!/usr/bin/env python3
"""Preserve OSS website configuration while enabling subdirectory index pages.

Manual bootstrap only: the old Actions credential needs GetBucketWebsite and
PutBucketWebsite. This does not change the node publisher's object-only key.

The oss2 BucketWebsite model does not preserve every website XML field, so read
the raw GET ?website response and pass the minimally edited XML to the SDK's
raw-XML put_bucket_website input. Preserve error pages, Type, and routing rules.

https://www.alibabacloud.com/help/en/oss/developer-reference/putbucketwebsite
https://github.com/aliyun/aliyun-oss-python-sdk/blob/master/oss2/api.py
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BUCKET = "marschain-power-site-chu"
MAX_CONFIG_BYTES = 1024 * 1024
SAFE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


class WebsiteError(RuntimeError):
    def __init__(self, action: str, code: str):
        self.action, self.code = action, code
        super().__init__(action + ": " + code)


def safe_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and SAFE_CODE.fullmatch(code) else type(exc).__name__


def atomic_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as out:
        os.chmod(temporary, 0o600)
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def encrypted_backup(certificate: Path, content: bytes, destination: Path) -> None:
    if not certificate.is_file() or b"PRIVATE KEY" in certificate.read_bytes():
        raise WebsiteError("EncryptBackup", "MissingPublicServerCertificate")
    process = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-aes-256-cbc", "-outform", "DER", str(certificate)],
        input=content, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False,
    )
    if process.returncode or not process.stdout:
        raise WebsiteError("EncryptBackup", "OpenSSLFailedNoWebsiteChange")
    atomic_bytes(destination, process.stdout)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if local_name(child.tag) == name]


def parse(content: bytes) -> ET.Element:
    if len(content) > MAX_CONFIG_BYTES or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise WebsiteError("ParseWebsite", "UnsafeOrOversizedWebsiteXML")
    root = ET.fromstring(content)
    if local_name(root.tag) != "WebsiteConfiguration":
        raise WebsiteError("ParseWebsite", "UnexpectedWebsiteRoot")
    return root


def semantic(element: ET.Element):
    # Discard indentation only. Preserve meaningful whitespace in header values
    # and every unknown node/attribute, as well as the order of routing rules.
    def text(value):
        return value if value and value.strip() else None
    return (element.tag, tuple(sorted(element.attrib.items())), text(element.text),
            tuple((semantic(child), text(child.tail)) for child in element))


def preserved_configuration(root: ET.Element):
    root = copy.deepcopy(root)
    for index in children(root, "IndexDocument"):
        for setting in children(index, "SupportSubDir"):
            index.remove(setting)
    return semantic(root)


def prepare(before: bytes) -> tuple[bytes, str]:
    original = parse(before)
    edited = copy.deepcopy(original)
    indexes = children(edited, "IndexDocument")
    if len(indexes) != 1:
        raise WebsiteError("PrepareWebsite", "ExistingIndexDocumentRequired")
    index = indexes[0]
    suffix = children(index, "Suffix")
    if len(suffix) != 1 or (suffix[0].text or "").strip() != "index.html":
        raise WebsiteError("PrepareWebsite", "UnexpectedIndexSuffixNoWebsiteChange")
    settings = children(index, "SupportSubDir")
    if len(settings) > 1 or any(item.attrib or len(item) for item in settings):
        raise WebsiteError("PrepareWebsite", "UnexpectedSupportSubDirStructure")
    current = (settings[0].text or "").strip().lower() if settings else "false"
    if current not in {"true", "false"}:
        raise WebsiteError("PrepareWebsite", "UnexpectedSupportSubDirValue")
    if settings:
        settings[0].text = "true"
    else:
        namespace = index.tag[:-len("IndexDocument")]
        setting = ET.Element(namespace + "SupportSubDir")
        setting.text = "true"
        index.insert(list(index).index(suffix[0]) + 1, setting)
    if preserved_configuration(original) != preserved_configuration(edited):
        raise WebsiteError("PrepareWebsite", "UnrelatedWebsiteConfigurationChanged")
    return ET.tostring(edited, encoding="utf-8", xml_declaration=True), current


class Configurator:
    def __init__(self, output: Path, certificate: Path):
        self.output, self.certificate = output, certificate
        self.bucket = None
        self.result = {"schema": 1, "state": "preparing", "action": "Initialize", "code": "Pending",
                       "started_at": int(time.time()), "website_put_attempted": False,
                       "website_put_verified": False, "server_key_permissions_changed": False}

    def save(self, **values: object) -> None:
        self.result.update(values, updated_at=int(time.time()))
        atomic_bytes(self.output / "website-result.json", (json.dumps(self.result, indent=2) + "\n").encode())

    def raw_website(self) -> bytes:
        self.save(action="GetBucketWebsite", code="Pending")
        try:
            # _do is the same signed transport used by get_bucket_website, but
            # leaves XML intact instead of parsing it into an incomplete model.
            response = self.bucket._do("GET", self.bucket.bucket_name, "", params={"website": ""})
            chunks, length = [], 0
            try:
                for chunk in response:
                    length += len(chunk)
                    if length > MAX_CONFIG_BYTES:
                        raise WebsiteError("GetBucketWebsite", "OversizedWebsiteXML")
                    chunks.append(chunk)
            finally:
                response.response.close()
            body = b"".join(chunks)
            parse(body)
            return body
        except WebsiteError:
            raise
        except Exception as exc:
            raise WebsiteError("GetBucketWebsite", safe_code(exc)) from None

    def run(self, apply: bool) -> None:
        name = os.environ.get("ALIYUN_OSS_BUCKET", "").strip()
        endpoint = os.environ.get("ALIYUN_OSS_ENDPOINT", "").strip().rstrip("/")
        if name != EXPECTED_BUCKET:
            raise WebsiteError("ValidateBucket", "UnexpectedBucketNoWebsiteChange")
        parsed = urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
        if (parsed.scheme != "https" or parsed.hostname != "oss-cn-hangzhou.aliyuncs.com"
                or parsed.username or parsed.password or parsed.port or parsed.path or parsed.query or parsed.fragment):
            raise WebsiteError("ValidateEndpoint", "UnexpectedEndpointNoWebsiteChange")
        access_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "").strip()
        secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "").strip()
        if not access_id or not secret:
            raise WebsiteError("LoadActionsCredential", "ExistingActionsCredentialMissing")
        self.save(action="LoadSDK", bucket=name, endpoint="https://" + parsed.hostname)
        import oss2
        auth = oss2.AuthV4(access_id, secret)
        self.bucket = oss2.Bucket(auth, "https://" + parsed.hostname, name, region="cn-hangzhou", connect_timeout=20)
        before = self.raw_website()
        planned, current = prepare(before)
        # Website rules can contain private mirror headers. Plaintext XML stays
        # on the ephemeral runner; only its server-encrypted envelope is uploaded.
        atomic_bytes(self.output / "website-before.xml", before)
        atomic_bytes(self.output / "website-planned.xml", planned)
        encrypted_backup(self.certificate, before, self.output / "website-before.xml.cms")
        self.save(action="PlanWebsite", code="Ready", support_subdir_before=current,
                  support_subdir_after="true", only_changed_path="IndexDocument/SupportSubDir",
                  before_sha256=hashlib.sha256(before).hexdigest(), planned_sha256=hashlib.sha256(planned).hexdigest(),
                  prior_configuration_encrypted=True)
        if current == "true":
            self.save(state="already_configured", action="VerifyWebsite", code="Success",
                      website_put_verified=True, unrelated_configuration_preserved=True)
            return
        if not apply:
            self.save(state="plan_ready", action="PlanWebsite", code="ApplyNotRequested")
            return
        # Detect a change made since the first GET. OSS does not document a CAS
        # token for this configuration; coordinate any other website administrators.
        if semantic(parse(self.raw_website())) != semantic(parse(before)):
            raise WebsiteError("CheckConcurrentWebsiteChange", "RemoteConfigurationChangedNoWebsiteChange")
        self.save(action="PutBucketWebsite", code="Pending", website_put_attempted=True)
        try:
            self.bucket.put_bucket_website(planned)
        except Exception as exc:
            # A timeout is ambiguous: don't blindly retry or restore stale XML.
            raise WebsiteError("PutBucketWebsite", safe_code(exc)) from None
        after = self.raw_website()
        atomic_bytes(self.output / "website-after.xml", after)
        encrypted_backup(self.certificate, after, self.output / "website-after.xml.cms")
        if semantic(parse(after)) != semantic(parse(planned)):
            raise WebsiteError("VerifyWebsite", "WebsiteReadbackMismatchNoAutomaticRollback")
        if preserved_configuration(parse(after)) != preserved_configuration(parse(before)):
            raise WebsiteError("VerifyWebsite", "UnrelatedWebsiteConfigurationMismatch")
        self.save(state="configured", action="VerifyWebsite", code="Success", website_put_verified=True,
                  after_sha256=hashlib.sha256(after).hexdigest(), unrelated_configuration_preserved=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Set only IndexDocument/SupportSubDir to true")
    parser.add_argument("--output", type=Path, default=ROOT / "output/node-website-migration")
    parser.add_argument("--certificate", type=Path, default=ROOT / "node_preview/provisioning/node-encryption-cert.pem")
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    args.output.mkdir(parents=True, exist_ok=True)
    os.chmod(args.output, 0o700)
    configurator = Configurator(args.output, args.certificate)
    configurator.save()
    code = 0
    try:
        configurator.run(args.apply)
    except WebsiteError as exc:
        configurator.save(state="error", action=exc.action, code=exc.code)
        code = 1
    except Exception as exc:
        configurator.save(state="error", code=type(exc).__name__)
        code = 1
    print(json.dumps(configurator.result, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
