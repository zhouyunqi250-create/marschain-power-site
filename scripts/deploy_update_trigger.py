#!/usr/bin/env python3
"""Deploy the MarsChain daily GitHub workflow trigger to Alibaba Cloud FC."""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

import oss2
from Tea.exceptions import TeaException
from alibabacloud_fc20230330 import models as fc_models
from alibabacloud_fc20230330.client import Client as FcClient
from alibabacloud_sts20150401.client import Client as StsClient
from alibabacloud_tea_openapi import models as open_api_models


ROOT = Path(__file__).resolve().parents[1]
FUNCTION_NAME = os.getenv("MARS_UPDATE_TRIGGER_FUNCTION_NAME", "marschain-daily-update-trigger")
DEFAULT_REPO = "zhouyunqi250-create/marschain-power-site"

TIMER_TRIGGERS = [
    ("fast-0758", "0 58 23 * * *", {"mode": "fast"}),
    ("fast-0802", "0 2 0 * * *", {"mode": "fast"}),
    ("fast-0806", "0 6 0 * * *", {"mode": "fast"}),
    ("fast-0810", "0 10 0 * * *", {"mode": "fast"}),
    ("full-0806", "0 6 0 * * *", {"mode": "full"}),
    ("full-0816", "0 16 0 * * *", {"mode": "full"}),
    ("full-0831", "0 31 0 * * *", {"mode": "full"}),
    ("full-0846", "0 46 0 * * *", {"mode": "full"}),
    ("full-0901", "0 1 1 * * *", {"mode": "full"}),
]


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


def fc_client(region_id: str, account_id: str, access_key_id: str, access_key_secret: str) -> FcClient:
    if not account_id or account_id == "unknown":
        raise SystemExit("Cannot determine Alibaba Cloud account id for Function Compute endpoint")
    return FcClient(
        open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            region_id=region_id,
            endpoint=f"{account_id}.{region_id}.fc.aliyuncs.com",
        )
    )


def package_function() -> bytes:
    source = ROOT / "marschain_update_trigger.py"
    if not source.exists():
        raise SystemExit(f"Missing function source: {source}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(source, "marschain_update_trigger.py")
    return buffer.getvalue()


def upload_code(bucket_name: str, endpoint: str, auth: oss2.Auth, zip_bytes: bytes) -> str:
    object_name = f"function-code/{FUNCTION_NAME}-{int(time.time())}.zip"
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    bucket.put_object(
        object_name,
        zip_bytes,
        headers={"Content-Type": "application/zip", "Cache-Control": "no-store"},
    )
    return object_name


def deploy_function(client: FcClient, body: fc_models.CreateFunctionInput) -> None:
    try:
        client.create_function(fc_models.CreateFunctionRequest(body=body))
        return
    except TeaException as exc:
        if "already" not in (exc.code or "").lower() and "exist" not in (exc.message or "").lower():
            raise

    update_body = fc_models.UpdateFunctionInput(
        code=body.code,
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


def trigger_config(cron_expression: str, payload: dict[str, str]) -> str:
    return json.dumps(
        {
            "cronExpression": cron_expression,
            "enable": True,
            "payload": json.dumps(payload, separators=(",", ":")),
        },
        separators=(",", ":"),
    )


def deploy_timer_trigger(client: FcClient, name: str, cron_expression: str, payload: dict[str, str]) -> None:
    config = trigger_config(cron_expression, payload)
    body = fc_models.CreateTriggerInput(
        trigger_name=name,
        trigger_type="timer",
        trigger_config=config,
        description=f"MarsChain daily update dispatcher {name}",
    )
    try:
        client.create_trigger(FUNCTION_NAME, fc_models.CreateTriggerRequest(body=body))
    except TeaException as exc:
        if "already" not in (exc.code or "").lower() and "exist" not in (exc.message or "").lower():
            raise
        update_body = fc_models.UpdateTriggerInput(trigger_config=config)
        client.update_trigger(FUNCTION_NAME, name, fc_models.UpdateTriggerRequest(body=update_body))


def main() -> int:
    access_key_id = require_env("ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = require_env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    github_token = require_env("MARSCHAIN_GITHUB_TOKEN")
    site_bucket = require_env("ALIYUN_OSS_BUCKET")
    site_endpoint = normalize_endpoint(require_env("ALIYUN_OSS_ENDPOINT"))
    region_id = os.getenv("ALIYUN_REGION_ID", "").strip() or region_from_oss_endpoint(site_endpoint)
    account_id = caller_account_id(access_key_id, access_key_secret)

    auth = oss2.Auth(access_key_id, access_key_secret)
    code_object = upload_code(site_bucket, site_endpoint, auth, package_function())
    client = fc_client(region_id, account_id, access_key_id, access_key_secret)
    code = fc_models.InputCodeLocation(oss_bucket_name=site_bucket, oss_object_name=code_object)
    env = {
        "MARSCHAIN_GITHUB_TOKEN": github_token,
        "MARSCHAIN_GITHUB_REPO": os.getenv("MARSCHAIN_GITHUB_REPO", DEFAULT_REPO),
        "MARSCHAIN_GITHUB_BRANCH": os.getenv("MARSCHAIN_GITHUB_BRANCH", "main"),
        "MARSCHAIN_PUBLIC_ORIGIN": os.getenv(
            "MARSCHAIN_PUBLIC_ORIGIN",
            "https://marschain-power-site-chu.oss-cn-hangzhou.aliyuncs.com",
        ),
    }
    body = fc_models.CreateFunctionInput(
        function_name=FUNCTION_NAME,
        description="MarsChain daily GitHub Actions dispatcher",
        code=code,
        handler="marschain_update_trigger.handler",
        runtime=os.getenv("MARS_UPDATE_TRIGGER_RUNTIME", "python3.10"),
        memory_size=int(os.getenv("MARS_UPDATE_TRIGGER_MEMORY", "128")),
        timeout=int(os.getenv("MARS_UPDATE_TRIGGER_TIMEOUT", "120")),
        cpu=float(os.getenv("MARS_UPDATE_TRIGGER_CPU", "0.1")),
        disk_size=int(os.getenv("MARS_UPDATE_TRIGGER_DISK_SIZE", "512")),
        instance_concurrency=int(os.getenv("MARS_UPDATE_TRIGGER_CONCURRENCY", "1")),
        internet_access=True,
        environment_variables=env,
    )
    deploy_function(client, body)
    for name, cron_expression, payload in TIMER_TRIGGERS:
        deploy_timer_trigger(client, name, cron_expression, payload)

    print(
        json.dumps(
            {
                "account_id": account_id,
                "region_id": region_id,
                "function_name": FUNCTION_NAME,
                "code_object": code_object,
                "triggers": [
                    {"name": name, "cron_utc": cron_expression, "payload": payload}
                    for name, cron_expression, payload in TIMER_TRIGGERS
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
