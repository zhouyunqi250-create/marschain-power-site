#!/usr/bin/env python3
"""Provision one object-scoped RAM key and seal it to the Ubuntu-only certificate.

This script is intended for the manual GitHub Actions bootstrap, where the
existing administrator key stays in Actions secrets. It never publishes a site,
prints an SDK exception, writes plaintext credentials, or retrieves an old key.

API reference: https://www.alibabacloud.com/help/en/ram/developer-reference/api-ram-2015-05-01-overview
Policy reference: https://www.alibabacloud.com/help/en/oss/user-guide/ram-policy/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
USERNAME = "marschain-node-publisher"
POLICY_NAME = "MarsChainNodeSiteObjects"
OWNER_NOTE = "Managed by marschain-power-site provision_node_publisher.py; programmatic site objects only"
OBJECT_PATHS = ("index.html", "m/index.html", "m/", "live_preview.js", "assets/node-*", "data/latest.json")
SAFE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,96}\Z")


class ProvisioningError(RuntimeError):
    def __init__(self, action: str, code: str):
        self.action, self.code = action, code
        super().__init__(action + ": " + code)


def error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and SAFE_CODE.fullmatch(code) else type(exc).__name__


def atomic_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as out:
        os.chmod(temporary, 0o600)
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def encrypt(certificate: Path, plaintext: bytes) -> bytes:
    # Only the public certificate path appears in argv; plaintext uses stdin.
    process = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-aes-256-cbc", "-outform", "DER", str(certificate)],
        input=plaintext, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False,
    )
    if process.returncode or not process.stdout:
        raise ProvisioningError("EncryptCredentials", "OpenSSLEncryptionFailed")
    return process.stdout


def certificate_fingerprint(certificate: Path) -> str:
    if not certificate.is_file():
        raise ProvisioningError("ValidateCertificate", "ServerEncryptionCertificateMissing")
    if b"PRIVATE KEY" in certificate.read_bytes():
        raise ProvisioningError("ValidateCertificate", "PrivateKeyMustNotBeInRepository")
    check = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-noout", "-checkend", "86400"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False,
    )
    der = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-outform", "DER"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False,
    )
    if check.returncode or der.returncode or not der.stdout:
        raise ProvisioningError("ValidateCertificate", "InvalidOrExpiringServerCertificate")
    # Check algorithm support before creating any cloud resource or access key.
    encrypt(certificate, b"MarsChain node publisher envelope preflight")
    return hashlib.sha256(der.stdout).hexdigest()


def configuration() -> tuple[str, str, str, dict]:
    bucket = os.getenv("ALIYUN_OSS_BUCKET", "").strip()
    endpoint = os.getenv("ALIYUN_OSS_ENDPOINT", "").strip().rstrip("/")
    prefix = os.getenv("ALIYUN_OSS_PREFIX", "").strip().strip("/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
        raise ProvisioningError("ValidateConfiguration", "InvalidOrMissingOSSBucket")
    if not endpoint.startswith(("https://", "http://")):
        endpoint = "https://" + endpoint
    url = urlsplit(endpoint)
    if (url.scheme != "https" or url.username or url.password or url.port or url.path
            or url.query or url.fragment or not re.fullmatch(r"oss-[a-z0-9-]+\.aliyuncs\.com", url.hostname or "")):
        raise ProvisioningError("ValidateConfiguration", "InvalidOrMissingOSSEndpoint")
    if prefix and any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..") for part in prefix.split("/")):
        raise ProvisioningError("ValidateConfiguration", "InvalidOSSPrefix")
    keys = [prefix + "/" + key if prefix else key for key in OBJECT_PATHS]
    policy = {"Version": "1", "Statement": [{"Effect": "Allow", "Action": ["oss:PutObject", "oss:GetObject"],
              "Resource": ["acs:oss:*:*:" + bucket + "/" + key for key in keys]}]}
    return bucket, endpoint, prefix, policy


class Provisioner:
    def __init__(self, output: Path, certificate: Path):
        self.output, self.certificate = output, certificate
        self.status_path = output / "provision-result.json"
        self.envelope_path = output / "node-publisher-credentials.cms"
        self.client = self.models = self.runtime = None
        self.result = {"schema": 1, "state": "running", "action": "Initialize", "code": "Pending",
                       "ram_user": USERNAME, "policy_name": POLICY_NAME, "started_at": int(time.time()),
                       "user_created": False, "policy_created": False, "access_key_creation_requested": False,
                       "access_key_created": False, "credentials_encrypted": False, "steps": []}

    def save(self) -> None:
        self.result["updated_at"] = int(time.time())
        atomic_bytes(self.status_path, (json.dumps(self.result, sort_keys=True, indent=2) + "\n").encode())

    def api(self, action: str, method: str, request, *, missing: tuple[str, ...] = ()) -> dict | None:
        self.result.update(action=action, code="Pending")
        self.save()
        try:
            response = getattr(self.client, method + "_with_options")(request, self.runtime)
        except Exception as exc:
            code = error_code(exc)
            if code in missing:
                self.result["steps"].append({"action": action, "code": "NotPresent"})
                self.save()
                return None
            raise ProvisioningError(action, code) from None
        self.result["steps"].append({"action": action, "code": "Succeeded"})
        self.save()
        return response.body.to_map()

    def get_policy(self) -> dict | None:
        return self.api("GetPolicy", "get_policy", self.models.GetPolicyRequest(
            policy_name=POLICY_NAME, policy_type="Custom"), missing=("EntityNotExist.Policy",))

    def verify_policy(self, body: dict, expected: dict) -> None:
        self.result["action"] = "VerifyPolicyScope"
        descriptor = body.get("Policy") or {}
        version = body.get("DefaultPolicyVersion") or {}
        document = version.get("PolicyDocument")
        if descriptor.get("PolicyName") != POLICY_NAME or descriptor.get("PolicyType") != "Custom":
            raise ProvisioningError("VerifyPolicyScope", "UnexpectedPolicyIdentity")
        try:
            actual = json.loads(unquote(document)) if isinstance(document, str) else None
        except ValueError:
            actual = None
        if actual != expected:
            raise ProvisioningError("VerifyPolicyScope", "ExistingPolicyScopeMismatchRefusingChange")

    def user_is_isolated(self) -> bool:
        def entries(body: dict, container: str, item: str) -> list[dict]:
            values = (body.get(container) or {}).get(item)
            if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
                raise ProvisioningError("VerifyUserScope", "MalformedAuthorizationResponse")
            return values

        groups = self.api("ListGroupsForUser", "list_groups_for_user", self.models.ListGroupsForUserRequest(user_name=USERNAME))
        if entries(groups, "Groups", "Group"):
            raise ProvisioningError("VerifyUserScope", "ExistingUserHasGroupMembership")
        policies = self.api("ListPoliciesForUser", "list_policies_for_user", self.models.ListPoliciesForUserRequest(user_name=USERNAME))
        attached = entries(policies, "Policies", "Policy")
        if any(row.get("PolicyName") != POLICY_NAME or row.get("PolicyType") != "Custom" for row in attached):
            raise ProvisioningError("VerifyUserScope", "ExistingUserHasOtherPolicies")
        keys = self.api("ListAccessKeys", "list_access_keys", self.models.ListAccessKeysRequest(user_name=USERNAME))
        if entries(keys, "AccessKeys", "AccessKey"):
            raise ProvisioningError("ListAccessKeys", "ExistingKeyUsePreviousEncryptedArtifactNoNewKeyCreated")
        return bool(attached)

    def run(self) -> None:
        bucket, endpoint, prefix, policy = configuration()
        fingerprint = certificate_fingerprint(self.certificate)
        self.result.update(certificate_sha256=fingerprint,
                           policy_document_sha256=hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest())
        self.result["action"] = "LoadSDK"
        self.save()
        from alibabacloud_ram20150501 import models
        from alibabacloud_ram20150501.client import Client
        from alibabacloud_tea_openapi import utils_models
        from darabonba.runtime import RuntimeOptions
        old_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "").strip()
        old_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "").strip()
        if not old_id or not old_secret:
            raise ProvisioningError("LoadActionsCredential", "ExistingActionsCredentialMissing")
        # Do not retry a non-idempotent CreateAccessKey after an ambiguous timeout.
        self.models, self.runtime = models, RuntimeOptions(autoretry=False, max_attempts=1, connect_timeout=10000, read_timeout=20000)
        self.client = Client(utils_models.Config(access_key_id=old_id, access_key_secret=old_secret, endpoint="ram.aliyuncs.com"))
        body = self.get_policy()
        if body is None:
            self.api("CreatePolicy", "create_policy", models.CreatePolicyRequest(
                policy_name=POLICY_NAME, policy_document=json.dumps(policy, separators=(",", ":")),
                description="MarsChain Ubuntu publisher: only designated public site objects; no listing or deletion"))
            self.result["policy_created"] = True
            self.save()
            body = self.get_policy()
        if body is None:
            raise ProvisioningError("GetPolicy", "PolicyMissingAfterCreation")
        self.verify_policy(body, policy)
        body = self.api("GetUser", "get_user", models.GetUserRequest(user_name=USERNAME), missing=("EntityNotExist.User",))
        if body is None:
            self.api("CreateUser", "create_user", models.CreateUserRequest(user_name=USERNAME,
                display_name="MarsChain node site publisher", comments=OWNER_NOTE))
            self.result["user_created"] = True
            self.save()
        else:
            user = body.get("User") or {}
            if user.get("UserName") != USERNAME or user.get("Comments") != OWNER_NOTE:
                raise ProvisioningError("VerifyUserIdentity", "ExistingUserNotOwnedByThisProvisioner")
            profile = self.api("GetLoginProfile", "get_login_profile", models.GetLoginProfileRequest(user_name=USERNAME),
                missing=("EntityNotExist.User.LoginProfile", "EntityNotExist.LoginProfile"))
            if profile is not None:
                raise ProvisioningError("VerifyUserScope", "ExistingUserHasConsoleLogin")
        attached = self.user_is_isolated()
        if not attached:
            self.api("AttachPolicyToUser", "attach_policy_to_user", models.AttachPolicyToUserRequest(
                policy_name=POLICY_NAME, policy_type="Custom", user_name=USERNAME))
        if not self.user_is_isolated():
            raise ProvisioningError("VerifyUserScope", "ScopedPolicyNotAttached")
        # Recheck the actual default policy immediately before issuing credentials.
        self.verify_policy(self.get_policy() or {}, policy)
        self.result["access_key_creation_requested"] = True
        self.save()
        body = self.api("CreateAccessKey", "create_access_key", models.CreateAccessKeyRequest(user_name=USERNAME))
        key = body.get("AccessKey") or {}
        new_id, new_secret = key.get("AccessKeyId"), key.get("AccessKeySecret")
        # GitHub runner commands register masks; they never show the values in logs.
        if os.getenv("GITHUB_ACTIONS") == "true":
            for secret in (new_id, new_secret):
                if isinstance(secret, str) and secret:
                    escaped = secret.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                    print("::add-mask::" + escaped, flush=True)
        self.result["access_key_created"] = True
        self.save()
        if (not isinstance(new_id, str) or not new_id or not isinstance(new_secret, str) or not new_secret
                or key.get("Status") != "Active" or new_id == old_id or new_secret == old_secret):
            raise ProvisioningError("CreateAccessKey", "MalformedAccessKeyResponse")
        credentials = {"schema": 1, "ram_user": USERNAME, "policy_name": POLICY_NAME,
            "created_at": int(time.time()), "certificate_sha256": fingerprint,
            "ALIBABA_CLOUD_ACCESS_KEY_ID": new_id, "ALIBABA_CLOUD_ACCESS_KEY_SECRET": new_secret,
            "ALIYUN_OSS_BUCKET": bucket, "ALIYUN_OSS_ENDPOINT": endpoint, "ALIYUN_OSS_PREFIX": prefix,
            "MARSCHAIN_PUBLISHER_MODE": "node"}
        self.result["action"] = "EncryptCredentials"
        self.save()
        ciphertext = encrypt(self.certificate, json.dumps(credentials, separators=(",", ":")).encode())
        atomic_bytes(self.envelope_path, ciphertext)
        self.result.update(state="complete", action="EncryptCredentials", code="Succeeded", credentials_encrypted=True,
            encrypted_file=self.envelope_path.name, encrypted_sha256=hashlib.sha256(ciphertext).hexdigest(),
            finished_at=int(time.time()))
        self.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, default=ROOT / "node_preview/provisioning/node-encryption-cert.pem")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/node-publisher-provisioning")
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    provisioner = Provisioner(args.output_dir.resolve(), args.certificate.resolve())
    # Never replace a previously sealed result or silently issue a second key.
    if provisioner.envelope_path.exists():
        print(json.dumps({"state": "blocked", "action": "Initialize", "code": "EncryptedEnvelopeAlreadyExists"}))
        return 1
    provisioner.save()
    try:
        provisioner.run()
    except Exception as exc:
        action = exc.action if isinstance(exc, ProvisioningError) else provisioner.result["action"]
        code = exc.code if isinstance(exc, ProvisioningError) else error_code(exc)
        provisioner.result.update(state="failed", action=action, code=code, finished_at=int(time.time()))
        provisioner.save()
        print(json.dumps({"state": "failed", "action": action, "code": code}))
        return 1
    print(json.dumps({"state": "complete", "action": "EncryptCredentials", "code": "Succeeded"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
