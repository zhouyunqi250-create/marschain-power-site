#!/usr/bin/env python3
"""Backfill public metric history from recent GitHub Actions artifacts."""

from __future__ import annotations

import argparse
import io
import json
import os
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from refresh_site import build_metric_snapshot, load_metric_history, merge_metric_history, normalize_metric_history


ARTIFACT_NAME = "marschain-site-build"
LATEST_JSON_CANDIDATES = (
    "site/data/latest.json",
    "output/latest/latest.json",
)


def request_bytes(url: str, token: str) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "marschain-rank-backfill",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def request_json(url: str, token: str) -> dict:
    return json.loads(request_bytes(url, token).decode("utf-8"))


def latest_json_from_artifact(artifact_url: str, token: str) -> dict | None:
    try:
        raw = request_bytes(artifact_url, token)
    except urllib.error.HTTPError as exc:
        print(f"artifact download skipped: HTTP {exc.code}")
        return None
    except Exception as exc:
        print(f"artifact download skipped: {exc}")
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = set(archive.namelist())
            candidates = [name for name in LATEST_JSON_CANDIDATES if name in names]
            if not candidates:
                candidates = [
                    name
                    for name in archive.namelist()
                    if name.endswith("/latest.json") and ("/data/" in name or "/latest/" in name)
                ]
            for name in candidates:
                try:
                    return json.loads(archive.read(name).decode("utf-8"))
                except Exception:
                    continue
    except zipfile.BadZipFile as exc:
        print(f"artifact is not a zip: {exc}")
    return None


def select_artifact(artifacts_url: str, token: str) -> dict | None:
    try:
        payload = request_json(artifacts_url, token)
    except Exception as exc:
        print(f"artifact list skipped: {exc}")
        return None
    for artifact in payload.get("artifacts", []):
        if artifact.get("name") == ARTIFACT_NAME and not artifact.get("expired"):
            return artifact
    return None


def backfill(site_dir: Path, repo: str, token: str, max_runs: int) -> int:
    history = load_metric_history(site_dir)
    try:
        payload = request_json(
            f"https://api.github.com/repos/{repo}/actions/runs?per_page=50&status=success",
            token,
        )
    except Exception as exc:
        print(f"run list skipped: {exc}")
        return len(history)

    runs = [
        run
        for run in payload.get("workflow_runs", [])
        if run.get("name") == "Update MarsChain Site" and run.get("conclusion") == "success"
    ][:max_runs]
    for run in reversed(runs):
        artifact = select_artifact(run.get("artifacts_url", ""), token)
        if not artifact:
            continue
        latest_payload = latest_json_from_artifact(artifact.get("archive_download_url", ""), token)
        if not latest_payload:
            continue
        snapshot = build_metric_snapshot(latest_payload.get("meta", {}))
        if snapshot.get("values"):
            history = merge_metric_history(history, snapshot)

    history = normalize_metric_history(history)
    target = site_dir / "data" / "metric-history.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"snapshots": history}, ensure_ascii=False, indent=2) + "\n")
    return len(history)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN", ""))
    parser.add_argument("--max-runs", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.repo:
        raise SystemExit("repo is required")
    count = backfill(args.site_dir, args.repo, args.token.strip(), max(1, args.max_runs))
    print(f"metric history snapshots: {count}")


if __name__ == "__main__":
    main()
