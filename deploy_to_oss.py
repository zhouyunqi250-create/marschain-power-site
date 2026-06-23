#!/usr/bin/env python3
"""Sync the generated static site to Alibaba Cloud OSS and optionally refresh CDN."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import urljoin


TEXT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
}

NO_CACHE_KEYS = {
    "build-meta.json",
    "data/latest.json",
    "data/metric-history.json",
    "data/price.json",
    "index.html",
    "m/",
    "m/index.html",
}


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"https://{endpoint}"


def normalize_prefix(prefix: str) -> str:
    value = prefix.strip().strip("/")
    return value


def derive_paid_bucket(site_bucket: str) -> str:
    configured = os.getenv("ALIYUN_PAID_OSS_BUCKET", "").strip()
    if configured:
        return configured
    base = re.sub(r"[^a-z0-9-]", "-", site_bucket.lower()).strip("-")
    name = f"{base}-paid"
    if len(name) <= 63:
        return name
    return f"{base[:58].rstrip('-')}-paid"


def resolve_setting(cli_value: str | None, env_name: str, *, required: bool = True, default: str | None = None) -> str:
    value = cli_value or os.getenv(env_name) or default
    if required and not value:
        raise SystemExit(f"Missing required setting: {env_name}")
    return value or ""


def iter_site_files(site_dir: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for path in sorted(site_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(site_dir).as_posix()
        files.append((rel, path))
    return files


def build_remote_key(prefix: str, rel_path: str) -> str:
    if prefix:
        return f"{prefix}/{rel_path}"
    return rel_path


def guess_headers(rel_path: str) -> dict[str, str]:
    suffix = Path(rel_path).suffix.lower()
    content_type = TEXT_TYPES.get(suffix)
    if not content_type:
        guessed, _ = mimetypes.guess_type(rel_path)
        content_type = guessed or "application/octet-stream"

    headers = {"Content-Type": content_type}
    if rel_path in NO_CACHE_KEYS:
        headers["Cache-Control"] = "no-store, no-cache, max-age=0, must-revalidate"
    else:
        headers["Cache-Control"] = "public, max-age=900"
    return headers


def paid_download_headers(rel_path: str) -> dict[str, str]:
    headers = guess_headers(rel_path)
    headers["Cache-Control"] = "private, no-store, max-age=0"
    headers["Content-Disposition"] = f'attachment; filename="{Path(rel_path).name}"'
    return headers


def refresh_urls(base_url: str) -> list[str]:
    canonical = base_url.rstrip("/") + "/"
    return [
        canonical,
        urljoin(canonical, "index.html"),
        urljoin(canonical, "m/"),
        urljoin(canonical, "m/index.html"),
        urljoin(canonical, "build-meta.json"),
        urljoin(canonical, "data/latest.json"),
        urljoin(canonical, "data/metric-history.json"),
        urljoin(canonical, "data/price.json"),
        urljoin(canonical, "downloads/latest.csv"),
        urljoin(canonical, "downloads/latest.xlsx"),
        urljoin(canonical, "robots.txt"),
    ]


def sync_site(
    site_dir: Path,
    bucket_name: str,
    endpoint: str,
    prefix: str,
    dry_run: bool,
) -> dict[str, list[str]]:
    files = iter_site_files(site_dir)
    desired_keys = {build_remote_key(prefix, rel): (rel, path) for rel, path in files}
    mobile_index = site_dir / "m" / "index.html"
    if mobile_index.exists():
        desired_keys[build_remote_key(prefix, "m/")] = ("m/index.html", mobile_index)
    summary = {"upload": [], "delete": []}

    if dry_run:
        summary["upload"].extend(sorted(desired_keys))
        return summary

    import oss2

    access_key_id = resolve_setting(None, "ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = resolve_setting(None, "ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)

    existing_keys: set[str] = set()
    iterator_cls = getattr(oss2, "ObjectIteratorV2", oss2.ObjectIterator)
    for obj in iterator_cls(bucket, prefix=f"{prefix}/" if prefix else ""):
        key = getattr(obj, "key", None)
        if key:
            existing_keys.add(key)

    for remote_key, (rel, path) in desired_keys.items():
        with path.open("rb") as fh:
            bucket.put_object(remote_key, fh, headers=guess_headers(rel))
        summary["upload"].append(remote_key)

    stale_keys = sorted(existing_keys - set(desired_keys))
    for key in stale_keys:
        bucket.delete_object(key)
        summary["delete"].append(key)

    return summary


def sync_paid_downloads(
    paid_download_dir: Path,
    bucket_name: str,
    endpoint: str,
    prefix: str,
    dry_run: bool,
) -> dict[str, list[str]]:
    source_files = [
        ("latest.csv", paid_download_dir / "latest.csv"),
        ("latest.xlsx", paid_download_dir / "latest.xlsx"),
    ]
    desired_files = [(rel, path) for rel, path in source_files if path.exists()]
    summary = {"upload": []}
    if not desired_files:
        return summary

    if dry_run:
        summary["upload"].extend(build_remote_key(prefix, rel) for rel, _ in desired_files)
        return summary

    import oss2

    access_key_id = resolve_setting(None, "ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = resolve_setting(None, "ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)

    for rel, path in desired_files:
        remote_key = build_remote_key(prefix, rel)
        with path.open("rb") as fh:
            bucket.put_object(remote_key, fh, headers=paid_download_headers(rel))
        summary["upload"].append(remote_key)
    return summary


def refresh_cdn(base_url: str, dry_run: bool) -> list[str]:
    urls = refresh_urls(base_url)
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
    parser = argparse.ArgumentParser(description="Deploy the static site to Alibaba Cloud OSS.")
    parser.add_argument("--site-dir", default="site", help="Static site directory to upload.")
    parser.add_argument("--bucket", help="OSS bucket name. Falls back to ALIYUN_OSS_BUCKET.")
    parser.add_argument("--endpoint", help="OSS endpoint. Falls back to ALIYUN_OSS_ENDPOINT.")
    parser.add_argument("--prefix", help="Optional remote key prefix. Falls back to ALIYUN_OSS_PREFIX.")
    parser.add_argument("--base-url", help="Public site base URL used for CDN refresh. Falls back to ALIYUN_SITE_BASE_URL.")
    parser.add_argument("--paid-download-dir", default="output/latest", help="Directory containing private paid download files.")
    parser.add_argument("--skip-paid-download-upload", action="store_true", help="Do not upload paid download files to the private OSS bucket.")
    parser.add_argument("--allow-bucket-root", action="store_true", help="Allow syncing to the bucket root when no prefix is set.")
    parser.add_argument("--skip-cdn-refresh", action="store_true", help="Upload to OSS but do not call CDN refresh.")
    parser.add_argument("--dry-run", action="store_true", help="Print intended uploads and refresh URLs without calling Alibaba Cloud.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    site_dir = Path(args.site_dir)
    if not site_dir.exists():
        raise SystemExit(f"Site directory not found: {site_dir}")

    bucket_name = resolve_setting(args.bucket, "ALIYUN_OSS_BUCKET")
    endpoint = normalize_endpoint(resolve_setting(args.endpoint, "ALIYUN_OSS_ENDPOINT"))
    prefix = normalize_prefix(resolve_setting(args.prefix, "ALIYUN_OSS_PREFIX", required=False, default=""))
    base_url = resolve_setting(args.base_url, "ALIYUN_SITE_BASE_URL", required=False, default="")
    paid_bucket_name = derive_paid_bucket(bucket_name)
    paid_endpoint = normalize_endpoint(resolve_setting(None, "ALIYUN_PAID_OSS_ENDPOINT", required=False, default=endpoint))
    paid_prefix = normalize_prefix(resolve_setting(None, "ALIYUN_PAID_OSS_PREFIX", required=False, default="paid-downloads"))

    if not prefix and not args.allow_bucket_root:
        raise SystemExit("Refusing to sync to OSS bucket root without --allow-bucket-root.")

    sync_summary = sync_site(site_dir, bucket_name, endpoint, prefix, args.dry_run)
    paid_sync_summary = {"upload": []}
    paid_upload_skipped = args.skip_paid_download_upload or not paid_bucket_name
    if not paid_upload_skipped:
        paid_sync_summary = sync_paid_downloads(
            Path(args.paid_download_dir),
            paid_bucket_name,
            paid_endpoint,
            paid_prefix,
            args.dry_run,
        )
    refreshed = []
    cdn_refresh_error = ""
    cdn_refresh_skipped = args.skip_cdn_refresh or not base_url
    if not cdn_refresh_skipped:
        try:
            refreshed = refresh_cdn(base_url, args.dry_run)
        except Exception as exc:  # pragma: no cover - depends on Alibaba Cloud account state
            cdn_refresh_error = str(exc)

    print(
        json.dumps(
            {
                "bucket": bucket_name,
                "endpoint": endpoint,
                "prefix": prefix,
                "uploaded_count": len(sync_summary["upload"]),
                "deleted_count": len(sync_summary["delete"]),
                "paid_bucket": paid_bucket_name,
                "paid_uploaded_count": len(paid_sync_summary["upload"]),
                "paid_upload_skipped": paid_upload_skipped,
                "cdn_refresh_skipped": cdn_refresh_skipped,
                "cdn_refresh_error": cdn_refresh_error,
                "refreshed_urls": refreshed,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
