"""Capture the official near-08:00 address total independently of chain jobs."""
from __future__ import annotations

import argparse
import fcntl
import json
import time
from pathlib import Path

from node_preview.bootstrap import atomic_json
from node_preview.metrics import MetricStore


def capture(root: Path, *, now: int | None = None, store: MetricStore | None = None) -> dict:
    now = int(time.time()) if now is None else int(now)
    cutoff = (now + 43200) // 86400 * 86400
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    status_path = state / "official-address-sampler-status.json"
    try:
        previous = json.loads(status_path.read_text())
    except (OSError, ValueError):
        previous = {}
    report = {"checked_at": now, "cutoff_timestamp": cutoff,
              "production_writes": False, "last_success": previous.get("last_success")}
    tolerance = store.boundary_sample_tolerance_seconds if store is not None else 90
    if abs(now - cutoff) > tolerance:
        report.update(state="outside_sampling_window", detail="No HTTP request made outside the real cutoff window")
        atomic_json(status_path, report)
        return report
    # These five dedicated daily attempts each request a fresh HTTP response.
    # A cached receipt is never relabelled as an on-time retry.
    store = store or MetricStore(state / "preview.sqlite3", seed_dir=state, cache_seconds=0)
    stats, source = store._cached_external("/stats", now)
    store._sample_official_stats(stats, source)
    sample, provenance = store._cutoff_sample("official_stats", cutoff)
    value = sample.get("total_wallets")
    available = type(value) is int and value >= 0
    report.update(state="sample_available" if available else "sample_unavailable",
                  address_total=value if available else None, fetched_at=source.get("fetched_at"),
                  fetch_status=source.get("status"), error=source.get("error"),
                  sample_provenance=provenance if available else None)
    if available:
        report["last_success"] = {"cutoff_timestamp": cutoff, "total_addresses": value,
                                  "received_at": provenance.get("received_at")}
    atomic_json(status_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    (root / "state").mkdir(parents=True, exist_ok=True)
    with (root / "state/official-address-sampler.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"state": "busy", "production_writes": False}))
            return
        report = capture(root)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if report["state"] == "sample_unavailable":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
