#!/usr/bin/env python3
"""Alibaba Cloud timer function that dispatches MarsChain GitHub workflows."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


PUBLIC_ORIGIN = os.getenv(
    "MARSCHAIN_PUBLIC_ORIGIN",
    "https://marschain-power-site-chu.oss-cn-hangzhou.aliyuncs.com",
).rstrip("/")
REPO = os.getenv("MARSCHAIN_GITHUB_REPO", "zhouyunqi250-create/marschain-power-site")
BRANCH = os.getenv("MARSCHAIN_GITHUB_BRANCH", "main")
FAST_WORKFLOW = os.getenv("MARSCHAIN_FAST_WORKFLOW", "fast-update-marschain-site.yml")
FULL_WORKFLOW = os.getenv("MARSCHAIN_FULL_WORKFLOW", "update-marschain-site.yml")
GITHUB_TOKEN = os.getenv("MARSCHAIN_GITHUB_TOKEN", "").strip()


def json_request(url: str, *, timeout: int = 45, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def github_headers() -> dict[str, str]:
    if not GITHUB_TOKEN:
        raise RuntimeError("Missing MARSCHAIN_GITHUB_TOKEN")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "User-Agent": "MarsChainCloudUpdateTrigger/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def parse_event(event: Any) -> dict[str, Any]:
    if isinstance(event, bytes):
        event = event.decode("utf-8", "replace")
    if isinstance(event, str):
        try:
            event = json.loads(event)
        except json.JSONDecodeError:
            event = {"payload": event}
    if not isinstance(event, dict):
        event = {}
    payload = event.get("payload")
    if isinstance(payload, str) and payload.strip():
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                event.update(parsed)
        except json.JSONDecodeError:
            event["mode"] = payload
    return event


def expected_window_end_ts(now: dt.datetime | None = None) -> int:
    now_utc = now or dt.datetime.now(dt.timezone.utc)
    now_beijing = now_utc.astimezone(dt.timezone(dt.timedelta(hours=8)))
    expected_end = now_beijing.replace(hour=8, minute=0, second=0, microsecond=0)
    return int(expected_end.timestamp())


def load_latest_meta() -> dict[str, Any]:
    url = f"{PUBLIC_ORIGIN}/data/latest.json?v=cloud-trigger-{int(time.time())}"
    try:
        payload = json_request(
            url,
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        )
        return payload.get("meta") or {}
    except Exception as exc:
        print(f"latest.json check failed; continue recovery: {exc}")
        return {}


def fresh_timestamp(value: Any, expected_ts: int) -> bool:
    return isinstance(value, int) and value >= expected_ts


def workflow_active(workflow_file: str) -> bool:
    url = (
        f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow_file}/runs"
        f"?branch={BRANCH}&per_page=10"
    )
    payload = json_request(url, headers=github_headers())
    active_statuses = {"queued", "in_progress", "waiting", "requested", "pending"}
    return any(run.get("status") in active_statuses for run in payload.get("workflow_runs", []))


def dispatch(workflow_file: str, *, inputs: dict[str, str] | None = None) -> dict[str, Any]:
    if workflow_active(workflow_file):
        return {"workflow": workflow_file, "status": "active_skip"}
    body: dict[str, Any] = {"ref": BRANCH}
    if inputs:
        body["inputs"] = inputs
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow_file}/dispatches",
        data=json.dumps(body).encode("utf-8"),
        headers={**github_headers(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"dispatch {workflow_file} failed: HTTP {exc.code} {detail}") from exc
    return {"workflow": workflow_file, "status": "dispatched"}


def run(mode: str) -> dict[str, Any]:
    expected_ts = expected_window_end_ts()
    meta = load_latest_meta()
    result: dict[str, Any] = {
        "mode": mode,
        "repo": REPO,
        "branch": BRANCH,
        "expected_window_end_timestamp": expected_ts,
        "statistics_window_end_timestamp": meta.get("statistics_window_end_timestamp"),
        "full_scan_statistics_window_end_timestamp": meta.get("full_scan_statistics_window_end_timestamp"),
        "actions": [],
    }

    if mode in {"fast", "both"}:
        if fresh_timestamp(meta.get("statistics_window_end_timestamp"), expected_ts):
            result["actions"].append({"workflow": FAST_WORKFLOW, "status": "fresh_skip"})
        else:
            result["actions"].append(dispatch(FAST_WORKFLOW))

    if mode in {"full", "both"}:
        if fresh_timestamp(meta.get("full_scan_statistics_window_end_timestamp"), expected_ts):
            result["actions"].append({"workflow": FULL_WORKFLOW, "status": "fresh_skip"})
        else:
            result["actions"].append(dispatch(FULL_WORKFLOW, inputs={"site_only": "false"}))

    return result


def handler(event: Any, context: Any = None) -> dict[str, Any]:
    event_data = parse_event(event)
    mode = str(event_data.get("mode") or "both").strip().lower()
    if mode not in {"fast", "full", "both"}:
        mode = "both"
    result = run(mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result

