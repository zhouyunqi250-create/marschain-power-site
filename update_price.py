#!/usr/bin/env python3
"""Update only the public MarsChain price JSON when the price has changed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urljoin

from deploy_to_oss import build_remote_key, normalize_endpoint, normalize_prefix, resolve_setting
from price_data import (
    DEFAULT_SITE_BASE_URL,
    comparable_price,
    fetch_explorer_price_payload,
    load_price_url,
    price_is_unchanged,
)

PRICE_REL_PATH = "data/price.json"
PRICE_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
}


def price_log_fields(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {
            "price": None,
            "highest_price": None,
            "oracle_trigger_price": None,
        }
    return {
        "price": payload.get("price_display") or payload.get("price"),
        "highest_price": payload.get("highest_price_display") or payload.get("highest_price"),
        "oracle_trigger_price": payload.get("oracle_trigger_price_display") or payload.get("oracle_trigger_price"),
    }


def upload_price_json(path: Path, bucket_name: str, endpoint: str, prefix: str, dry_run: bool) -> str:
    remote_key = build_remote_key(prefix, PRICE_REL_PATH)
    if dry_run:
        return remote_key

    import oss2

    access_key_id = resolve_setting(None, "ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = resolve_setting(None, "ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    with path.open("rb") as handle:
        bucket.put_object(remote_key, handle, headers=PRICE_HEADERS)
    return remote_key


def refresh_price_cdn(base_url: str, dry_run: bool) -> list[str]:
    if not base_url:
        return []
    canonical = base_url.rstrip("/") + "/"
    urls = [urljoin(canonical, PRICE_REL_PATH)]
    if dry_run:
        return urls

    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_cdn20180510 import models as cdn_models
    from alibabacloud_cdn20180510.client import Client as CdnClient

    access_key_id = resolve_setting(None, "ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = resolve_setting(None, "ALIBABA_CLOUD_ACCESS_KEY_SECRET")

    config = open_api_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        endpoint="cdn.aliyuncs.com",
    )
    client = CdnClient(config)
    request = cdn_models.RefreshObjectCachesRequest(
        object_path="\n".join(urls),
        object_type="File",
    )
    client.refresh_object_caches(request)
    return urls


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh only data/price.json when explorer price changes.")
    parser.add_argument("--site-dir", default="site", help="Local static site directory used to stage data/price.json.")
    parser.add_argument("--site-base-url", default=None, help="Public site base URL. Falls back to ALIYUN_SITE_BASE_URL.")
    parser.add_argument("--bucket", help="OSS bucket name. Falls back to ALIYUN_OSS_BUCKET.")
    parser.add_argument("--endpoint", help="OSS endpoint. Falls back to ALIYUN_OSS_ENDPOINT.")
    parser.add_argument("--prefix", help="Optional remote key prefix. Falls back to ALIYUN_OSS_PREFIX.")
    parser.add_argument("--allow-bucket-root", action="store_true", help="Allow uploading to the bucket root when no prefix is set.")
    parser.add_argument("--skip-cdn-refresh", action="store_true", help="Upload to OSS but do not call CDN refresh.")
    parser.add_argument("--dry-run", action="store_true", help="Print intended upload and refresh without calling Alibaba Cloud.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    site_base_url = resolve_setting(
        args.site_base_url,
        "ALIYUN_SITE_BASE_URL",
        required=False,
        default=DEFAULT_SITE_BASE_URL,
    )
    existing = load_price_url(site_base_url)
    next_payload, _stats = fetch_explorer_price_payload(existing)

    if price_is_unchanged(existing, next_payload):
        print(
            json.dumps(
                {
                    "status": "unchanged",
                    **price_log_fields(next_payload),
                    "public_checked_at": existing.get("checked_at") if isinstance(existing, dict) else None,
                },
                ensure_ascii=False,
            )
        )
        return 0

    site_dir = Path(args.site_dir)
    price_path = site_dir / PRICE_REL_PATH
    price_path.parent.mkdir(parents=True, exist_ok=True)
    price_path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    bucket_name = resolve_setting(args.bucket, "ALIYUN_OSS_BUCKET")
    endpoint = normalize_endpoint(resolve_setting(args.endpoint, "ALIYUN_OSS_ENDPOINT"))
    prefix = normalize_prefix(resolve_setting(args.prefix, "ALIYUN_OSS_PREFIX", required=False, default=""))
    if not prefix and not args.allow_bucket_root:
        raise SystemExit("Refusing to upload to OSS bucket root without --allow-bucket-root.")

    remote_key = upload_price_json(price_path, bucket_name, endpoint, prefix, args.dry_run)
    refreshed = [] if args.skip_cdn_refresh else refresh_price_cdn(site_base_url, args.dry_run)
    print(
        json.dumps(
            {
                "status": "updated",
                **price_log_fields(next_payload),
                "previous": price_log_fields(existing),
                "comparison": comparable_price(next_payload),
                "uploaded": remote_key,
                "refreshed": refreshed,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
