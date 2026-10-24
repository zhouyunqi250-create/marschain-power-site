"""Produce small public preview snapshots independently of chain collection."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from marschain_power_rank import format_beijing_datetime
from node_preview.bootstrap import atomic_json
from node_preview.chain import RPC
from node_preview.collector import read_status
from node_preview.frontend import build_preview, public_payload
from node_preview.metrics import MetricStore


class SnapshotPending(RuntimeError):
    pass


def ranking(db: sqlite3.Connection) -> tuple[list[dict], dict]:
    values = db.execute("SELECT address,power,burned,total_burned,upline1,upline2,fetched_block,fetched_at FROM preview_power_state").fetchall()
    positive = [row for row in values if int(row[1]) > 0]
    positive.sort(key=lambda row: (-int(row[1]), row[0]))
    rows = [{"rank": rank, "address": row[0], "power": str(int(row[1])),
             "burned_amount": row[2], "total_burned_amount": row[3],
             "upline1": row[4], "upline2": row[5]}
            for rank, row in enumerate(positive[:100], 1)]
    return rows, {"positive_power_count": len(positive), "full_ranked_count": len(positive),
                  "discovered_total_power": sum(int(row[1]) for row in values),
                  "oldest_checked_block": min((row[6] for row in values), default=None),
                  "last_checked_block": max((row[6] for row in values), default=None)}


def compose(root: Path, seeds: dict, metrics: MetricStore, *, production: bool = False) -> tuple[dict, dict]:
    start = time.monotonic()
    database = root / "state/preview.sqlite3"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=15) as db:
        db.execute("BEGIN")
        status = read_status(db)
        known = db.execute("SELECT COUNT(*) FROM addresses").fetchone()[0]
        source_block = int(status.get("checkpoint_block") or 0)
        collection_start = int(status.get("collection_start_block") or source_block)
        event_count = db.execute("SELECT COUNT(*) FROM preview_power_events WHERE block_number BETWEEN ? AND ?",
                                 (collection_start, source_block)).fetchone()[0]
        warmed = bool(status.get("calibration_done"))
        if status.get("state") in {"error", "needs_reconcile"}:
            raise RuntimeError(status.get("last_error") or status["state"])
        if warmed and (source_block < int(status.get("safe_head") or 0)
                       or source_block < int(status.get("power_state_block") or 0)
                       or source_block < int(status.get("global_power_block") or 0)):
            raise SnapshotPending(f"Collector has not completed discovery through its power read block ({source_block}); preserving last good preview")
        if warmed:
            rows, counts = ranking(db)
        else:
            rows = copy.deepcopy(seeds["payload"]["rows"][:100])
            counts = {}
    meta = copy.deepcopy(seeds["payload"]["meta"])
    # Current global values and daily facts keep their own provenance and clocks.
    if source_block:
        reader = RPC(metrics.rpc_url, timeout=10)
        if reader.block(source_block)["hash"].lower() != str(status.get("checkpoint_hash", "")).lower():
            raise RuntimeError("Snapshot checkpoint is no longer canonical")
        meta = metrics.refresh(meta, {"number": source_block, "hash": status.get("checkpoint_hash"),
                                      "timestamp": status.get("checkpoint_timestamp")})
        if reader.block(source_block)["hash"].lower() != str(status.get("checkpoint_hash", "")).lower():
            raise RuntimeError("Snapshot checkpoint changed while preparing metrics")
        if warmed and any(meta.get("preview_metric_sources", {}).get(key, {}).get("status") != "ok"
                          for key in ("network_total_power", "network_total_burned_tokens")):
            raise SnapshotPending("Node global values could not be verified at the snapshot block")
    now = int(time.time())
    if warmed:
        last_success = int(status.get("last_success") or 0)
        block_time = int(status.get("checkpoint_timestamp") or 0)
        if not last_success or now - last_success > 180 or not block_time or now - block_time > 240:
            raise SnapshotPending("Chain collection is delayed; preserving the last successful block and data time")
    state = str(status.get("state") or "calibrating")
    # A reader can observe the final committed batch in the few milliseconds
    # before the collector writes its end-of-run label. Structural checks above
    # already establish that discovery and state reads cover the same tip.
    if warmed and state in {"ready", "running", "live", "collecting", "catching_up"}:
        state = "ready"
    source_error = status.get("last_error")
    preview = {"state": state, "mode": "node_production" if production else "local_preview", "source_block": source_block,
               "source_block_hash": status.get("checkpoint_hash"),
               "source_block_timestamp": status.get("checkpoint_timestamp"),
               "generated_at": now, "known_addresses": known,
               "calibration_done": warmed,
               "calibration_count": status.get("calibrated_addresses", status.get("calibration_count", 0)),
               "calibration_total": status.get("calibration_total", known),
               "source_error": source_error, "last_collection_success": status.get("last_success"),
               "scope": "已发现地址集合；尚未覆盖全部链上地址",
               "ranking_source": "local_node_contract" if warmed else "public_seed_until_calibration_complete",
               "seed_generated_at": seeds["payload"]["meta"].get("generated_at"),
               "production_writes": False}
    if warmed:
        meta.update(counts)
        meta.update({"candidate_count": known, "address_pool_count": known,
                     "ranking_type": "node_verified_discovered_addresses", "refresh_mode": "node_incremental",
                     "generated_at": now, "generated_at_local": format_beijing_datetime(now),
                     "latest_block": source_block})
        total = int(meta.get("network_total_power") or 0)
        meta["discovered_power_coverage"] = counts["discovered_total_power"] / total if total else None
        meta["target_met"] = bool(total and meta["discovered_power_coverage"] >= float(meta.get("coverage_target") or .8))
        meta["rpc_logs_seen"] = event_count
        meta["rpc_log_blocks_scanned"] = max(0, source_block - collection_start)
        meta["rpc_log_start_block"] = collection_start
        meta["rpc_log_end_block"] = source_block
        meta["power_cache_refreshed"] = int(status.get("last_statistics", {}).get("users_updated") or 0)
        meta["power_cache_hits"] = 0
        meta["power_cache_stale_fallbacks"] = 0
    meta["ranked_count"] = len(rows)
    meta["public_rank_limit"] = 100
    meta["paid_download"] = {"enabled": False}
    meta.pop("paid_download_api_base", None)
    preview["version"] = hashlib.sha256(json.dumps({"rows": rows, "block": source_block,
        "state": state, "calibration": preview["calibration_count"], "metrics": meta,
        "time": now // 60}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    meta["node_preview"] = preview
    payload = public_payload({"meta": meta, "rows": rows})
    report = {"state": state, "version": preview["version"], "source_block": source_block,
              "generated_at": now, "ranking_source": preview["ranking_source"], "known_addresses": known,
              "public_rows": len(payload["rows"]), "production_writes": False,
              "elapsed_seconds": round(time.monotonic() - start, 3),
              "bytes": len(json.dumps(payload, ensure_ascii=False).encode()),
              "daily": meta.get("preview_daily_status"), "error": source_error}
    return payload, report


def run(root: Path, rpc_url: str, origin: str, interval: int, loop: bool, rebuild: bool, *, production: bool = False) -> None:
    store = MetricStore(root / "state/preview.sqlite3", rpc_url=rpc_url, seed_dir=root / "state")
    seeds = store.bootstrap(origin=origin)
    built = False
    while True:
        began = time.monotonic()
        try:
            payload, report = compose(root, seeds, store, production=production)
            site = root / "site"
            if not built and (rebuild or not (site / "index.html").exists()):
                build_preview(payload, site, production=production)
            built = True
            # Only this compact file changes on ordinary publication cycles.
            atomic_json(site / "data/latest.json", payload)
            atomic_json(root / "state/publisher-status.json", report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
        except SnapshotPending as exc:
            pending = {"state": "waiting_for_collector", "detail": str(exc), "attempted_at": int(time.time()),
                       "production_writes": False, "last_good_snapshot_preserved": True}
            atomic_json(root / "state/publisher-status.json", pending)
            print(json.dumps(pending, ensure_ascii=False), flush=True)
        except Exception as exc:
            failure = {"state": "error", "error": str(exc), "attempted_at": int(time.time()),
                       "production_writes": False, "last_good_snapshot_preserved": True}
            atomic_json(root / "state/publisher-status.json", failure)
            print(json.dumps(failure, ensure_ascii=False), flush=True)
            if not loop:
                raise
        if not loop:
            return
        time.sleep(max(1, interval - (time.monotonic() - began)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--rpc", default="http://127.0.0.1:18555")
    parser.add_argument("--origin", default="https://marschain-power-site-chu.oss-cn-hangzhou.aliyuncs.com")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--production", action="store_true", help="Build the approved public node-powered site shell")
    args = parser.parse_args()
    run(args.root.resolve(), args.rpc, args.origin, args.interval, args.loop, args.rebuild, production=args.production)


if __name__ == "__main__":
    main()
