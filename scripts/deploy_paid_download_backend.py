#!/usr/bin/env python3
"""Deploy the paid download verifier to Alibaba Cloud Function Compute."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import oss2
from Tea.exceptions import TeaException
from alibabacloud_fc20230330.client import Client as FcClient
from alibabacloud_fc20230330 import models as fc_models
from alibabacloud_sts20150401.client import Client as StsClient
from alibabacloud_tea_openapi import models as open_api_models


ROOT = Path(__file__).resolve().parents[1]
FUNCTION_NAME = os.getenv("MARS_PAID_FUNCTION_NAME", "marschain-paid-download")
TRIGGER_NAME = os.getenv("MARS_PAID_TRIGGER_NAME", "http")
DEFAULT_PAID_PREFIX = "paid-downloads"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"https://{endpoint}"


def endpoint_host(endpoint: str) -> str:
    return normalize_endpoint(endpoint).replace("https://", "").replace("http://", "").strip("/")


def region_from_oss_endpoint(endpoint: str) -> str:
    host = endpoint_host(endpoint)
    match = re.search(r"oss-([a-z0-9-]+)\.aliyuncs\.com", host)
    if not match:
        raise SystemExit(f"Cannot infer region from OSS endpoint: {endpoint}")
    return match.group(1)


def derive_paid_bucket(site_bucket: str) -> str:
    configured = os.getenv("ALIYUN_PAID_OSS_BUCKET", "").strip()
    if configured:
        return configured
    base = re.sub(r"[^a-z0-9-]", "-", site_bucket.lower()).strip("-")
    name = f"{base}-paid"
    if len(name) <= 63:
        return name
    return f"{base[:58].rstrip('-')}-paid"


def create_private_bucket(bucket_name: str, endpoint: str, auth: oss2.Auth) -> None:
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    try:
        bucket.get_bucket_info()
        bucket.put_bucket_acl(oss2.BUCKET_ACL_PRIVATE)
        return
    except oss2.exceptions.NoSuchBucket:
        pass

    bucket.create_bucket(permission=oss2.BUCKET_ACL_PRIVATE)
    # Make an immediately repeated info call less likely to race on new buckets.
    time.sleep(2)


def package_function() -> bytes:
    source = ROOT / "paid_download_service.py"
    if not source.exists():
        raise SystemExit(f"Missing service source: {source}")

    with tempfile.TemporaryDirectory(prefix="mars-paid-fc-") as tmp:
        build_dir = Path(tmp) / "code"
        build_dir.mkdir(parents=True)
        shutil.copy2(source, build_dir / "paid_download_service.py")

        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "oss2>=2.19,<3", "cryptography>=3.4,<3.5", "-t", str(build_dir)],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in build_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(build_dir).as_posix())
        return buffer.getvalue()


def fc_client(region_id: str, account_id: str, access_key_id: str, access_key_secret: str) -> FcClient:
    if not account_id or account_id == "unknown":
        raise SystemExit("Cannot determine Alibaba Cloud account id for Function Compute endpoint")

    config = open_api_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        region_id=region_id,
        endpoint=f"{account_id}.{region_id}.fc.aliyuncs.com",
    )
    return FcClient(config)


def caller_account_id(access_key_id: str, access_key_secret: str) -> str:
    configured = os.getenv("ALIBABA_CLOUD_ACCOUNT_ID", "").strip()
    if configured:
        return configured

    client = StsClient(
        open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint="sts.aliyuncs.com",
        )
    )
    try:
        response = client.get_caller_identity()
        account_id = getattr(response.body, "account_id", None) or response.body.to_map().get("AccountId")
        return str(account_id or "unknown")
    except Exception:
        return "unknown"


def code_input(bucket_name: str, object_name: str) -> fc_models.InputCodeLocation:
    return fc_models.InputCodeLocation(oss_bucket_name=bucket_name, oss_object_name=object_name)


def upload_function_code(bucket_name: str, endpoint: str, auth: oss2.Auth, zip_bytes: bytes) -> str:
    object_name = f"paid-download-code/{FUNCTION_NAME}-{int(time.time())}.zip"
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    bucket.put_object(
        object_name,
        zip_bytes,
        headers={
            "Content-Type": "application/zip",
            "Cache-Control": "no-store",
        },
    )
    return object_name


def function_env(
    *,
    access_key_id: str,
    access_key_secret: str,
    paid_bucket: str,
    paid_endpoint: str,
    paid_prefix: str,
) -> dict[str, str]:
    return {
        "ALIBABA_CLOUD_ACCESS_KEY_ID": access_key_id,
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET": access_key_secret,
        "ALIYUN_PAID_OSS_BUCKET": paid_bucket,
        "ALIYUN_PAID_OSS_ENDPOINT": endpoint_host(paid_endpoint),
        "ALIYUN_PAID_OSS_PREFIX": paid_prefix,
        "MARSCHAIN_RPC_URL": os.getenv("MARSCHAIN_RPC_URL", "https://rpcs.marschain.net"),
        "MARS_PAYMENT_ADDRESS_DISPLAY": os.getenv("MARS_PAYMENT_ADDRESS_DISPLAY", "0M0fD038365577215292B44F89C92695C7AC8C3363"),
        "MARS_PAYMENT_ADDRESS_VERIFY": os.getenv("MARS_PAYMENT_ADDRESS_VERIFY", "0x0fD038365577215292B44F89C92695C7AC8C3363"),
        "MARS_PAID_DOWNLOAD_PRICE": os.getenv("MARS_PAID_DOWNLOAD_PRICE", "1000"),
        "MARS_PAID_DOWNLOAD_PRICE_WEI": os.getenv("MARS_PAID_DOWNLOAD_PRICE_WEI", "1000000000000000000000"),
        "MARS_PAID_DOWNLOAD_EXPIRES_SECONDS": os.getenv("MARS_PAID_DOWNLOAD_EXPIRES_SECONDS", "3600"),
        "MARS_PAYMENT_CONFIRMATIONS": os.getenv("MARS_PAYMENT_CONFIRMATIONS", "3"),
        "MARS_PAID_ROUTE_PREFIX": "/api",
        "MARS_PAID_CORS_ORIGIN": os.getenv("MARS_PAID_CORS_ORIGIN", "https://www.marschainrank.com"),
    }


def deploy_function(client: FcClient, body: fc_models.CreateFunctionInput, code: fc_models.InputCodeLocation) -> None:
    try:
        client.create_function(fc_models.CreateFunctionRequest(body=body))
        return
    except TeaException as exc:
        if "already" not in (exc.code or "").lower() and "exist" not in (exc.message or "").lower():
            raise

    update_body = fc_models.UpdateFunctionInput(
        code=code,
        environment_variables=body.environment_variables,
        handler=body.handler,
        internet_access=body.internet_access,
        memory_size=body.memory_size,
        runtime=body.runtime,
        timeout=body.timeout,
        cpu=body.cpu,
        disk_size=body.disk_size,
        instance_concurrency=body.instance_concurrency,
    )
    client.update_function(FUNCTION_NAME, fc_models.UpdateFunctionRequest(body=update_body))


def trigger_config() -> str:
    config = fc_models.HTTPTriggerConfig(
        auth_type="anonymous",
        methods=["GET", "POST", "OPTIONS"],
        disable_urlinternet=False,
    )
    return json.dumps(config.to_map(), separators=(",", ":"))


def deploy_trigger(client: FcClient) -> str:
    body = fc_models.CreateTriggerInput(
        trigger_name=TRIGGER_NAME,
        trigger_type="http",
        trigger_config=trigger_config(),
        description="MarsChain paid leaderboard download API",
    )
    try:
        response = client.create_trigger(FUNCTION_NAME, fc_models.CreateTriggerRequest(body=body))
    except TeaException as exc:
        if "already" not in (exc.code or "").lower() and "exist" not in (exc.message or "").lower():
            raise
        update_body = fc_models.UpdateTriggerInput(trigger_config=trigger_config())
        client.update_trigger(FUNCTION_NAME, TRIGGER_NAME, fc_models.UpdateTriggerRequest(body=update_body))
        response = client.get_trigger(FUNCTION_NAME, TRIGGER_NAME)

    body_map = response.body.to_map()
    url = (body_map.get("httpTrigger") or {}).get("urlInternet")
    if not url:
        raise SystemExit(f"HTTP trigger did not return a public URL: {body_map}")
    return url.rstrip("/")


def check_health(api_base: str) -> dict[str, Any]:
    url = f"{api_base}/health"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def main() -> int:
    access_key_id = require_env("ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = require_env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    site_bucket = require_env("ALIYUN_OSS_BUCKET")
    site_endpoint = normalize_endpoint(require_env("ALIYUN_OSS_ENDPOINT"))
    paid_endpoint = normalize_endpoint(os.getenv("ALIYUN_PAID_OSS_ENDPOINT", "").strip() or site_endpoint)
    paid_bucket = derive_paid_bucket(site_bucket)
    paid_prefix = os.getenv("ALIYUN_PAID_OSS_PREFIX", DEFAULT_PAID_PREFIX).strip().strip("/") or DEFAULT_PAID_PREFIX
    region_id = os.getenv("ALIYUN_REGION_ID", "").strip() or region_from_oss_endpoint(paid_endpoint)
    account_id = caller_account_id(access_key_id, access_key_secret)

    auth = oss2.Auth(access_key_id, access_key_secret)
    create_private_bucket(paid_bucket, paid_endpoint, auth)

    zip_bytes = package_function()
    code_object = upload_function_code(paid_bucket, paid_endpoint, auth, zip_bytes)
    code = code_input(paid_bucket, code_object)
    client = fc_client(region_id, account_id, access_key_id, access_key_secret)
    env = function_env(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        paid_bucket=paid_bucket,
        paid_endpoint=paid_endpoint,
        paid_prefix=paid_prefix,
    )
    body = fc_models.CreateFunctionInput(
        function_name=FUNCTION_NAME,
        description="MarsChain paid leaderboard download verifier",
        code=code,
        handler="paid_download_service.handler",
        runtime=os.getenv("MARS_PAID_FUNCTION_RUNTIME", "python3.10"),
        memory_size=int(os.getenv("MARS_PAID_FUNCTION_MEMORY", "512")),
        timeout=int(os.getenv("MARS_PAID_FUNCTION_TIMEOUT", "60")),
        cpu=float(os.getenv("MARS_PAID_FUNCTION_CPU", "0.5")),
        disk_size=int(os.getenv("MARS_PAID_FUNCTION_DISK_SIZE", "512")),
        instance_concurrency=int(os.getenv("MARS_PAID_FUNCTION_CONCURRENCY", "20")),
        internet_access=True,
        environment_variables=env,
    )
    deploy_function(client, body, code)
    api_base = deploy_trigger(client)
    health = check_health(api_base)

    print(
        json.dumps(
            {
                "account_id": account_id,
                "region_id": region_id,
                "function_name": FUNCTION_NAME,
                "trigger_name": TRIGGER_NAME,
                "api_base": api_base,
                "paid_bucket": paid_bucket,
                "paid_endpoint": endpoint_host(paid_endpoint),
                "paid_prefix": paid_prefix,
                "code_object": code_object,
                "health": health,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
