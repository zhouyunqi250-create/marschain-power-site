"""Bounded recent-day transaction aggregates, not a historical address scan."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import time
from pathlib import Path

from node_preview.bootstrap import atomic_json
from node_preview.chain import RPC, ReconcileRequired, address, quantity
from node_preview.collector import initialize


def first_block_at(rpc: RPC, timestamp: int, head: int) -> int:
    low, high = 0, head + 1
    while low < high:
        middle = (low + high) // 2
        if middle > head or quantity(rpc.block(middle)["timestamp"]) >= timestamp:
            high = middle
        else:
            low = middle + 1
    return low


def backfill(root: Path, rpc_url: str, days: int = 2, workers: int = 3) -> dict:
    rpc = RPC(rpc_url, timeout=30, batch_size=100)
    head = max(0, rpc.head() - 6)
    head_timestamp = quantity(rpc.block(head)["timestamp"])
    cutoff = head_timestamp // 86400 * 86400 - days * 86400
    start = max(0, first_block_at(rpc, cutoff, head) - 1)
    database = root / "state/preview.sqlite3"
    with sqlite3.connect(database, timeout=30) as db:
        initialize(db)
        existing = {row[0] for row in db.execute("SELECT number FROM preview_blocks WHERE number BETWEEN ? AND ?", (start, head))}
        log_ranges = db.execute("SELECT start_block,end_block FROM preview_log_scan_ranges WHERE end_block>=? AND start_block<=? ORDER BY start_block", (start, head)).fetchall()
    def logs_cover(left, right):
        next_required = left
        for lower, upper in log_ranges:
            if upper < next_required:
                continue
            if lower > next_required:
                return False
            next_required = max(next_required, upper + 1)
            if next_required > right:
                return True
        return False
    ranges = []
    position = start
    while position <= head:
        end = min(head, position + 99)
        if not logs_cover(position, end) or any(number not in existing for number in range(position, end + 1)):
            ranges.append((position, end))
        position = end + 1
    began = time.monotonic()
    done = 0
    report = {"state": "running", "from_block": start, "through_block": head,
              "window_start_timestamp": cutoff, "range_count": len(ranges),
              "purpose": "Recent daily transaction statistics only; no address discovery"}
    path = root / "state/observations-status.json"
    atomic_json(path, report)

    def fetch(pair):
        client = RPC(rpc_url, timeout=30, batch_size=100)
        blocks = client.blocks(*pair)
        hashes = {quantity(block["number"]): block["hash"].lower() for block in blocks}
        events = []
        for log in client.logs(*pair):
            if log.get("blockHash", "").lower() != hashes.get(quantity(log["blockNumber"])):
                raise ReconcileRequired("Observed log has a conflicting block hash")
            _, event = client.decode_log(log)
            if event:
                events.append(event)
        return blocks, events

    # Work in small waves so outstanding RPC requests and memory stay bounded.
    with sqlite3.connect(database, timeout=60) as db:
        db.execute("PRAGMA busy_timeout=60000")
        for offset in range(0, len(ranges), workers):
            wave = ranges[offset:offset + workers]
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(fetch, wave))
            with db:
                for blocks, events in results:
                    for block in blocks:
                        number = quantity(block["number"])
                        saved = db.execute("SELECT block_hash FROM preview_blocks WHERE number=?", (number,)).fetchone()
                        if saved and saved[0].lower() != block["hash"].lower():
                            raise ReconcileRequired(f"Conflicting aggregate block {number}")
                        txs = block["transactions"]
                        participants = {candidate for tx in txs for raw in (tx.get("from"), tx.get("to"))
                                        if (candidate := address(raw)) and int(candidate, 16) >= 2**64}
                        db.execute("INSERT OR IGNORE INTO preview_blocks(number,block_hash,parent_hash,timestamp,tx_count,value_wei) VALUES (?,?,?,?,?,?)",
                                   (number, block["hash"].lower(), block["parentHash"].lower(), quantity(block["timestamp"]), len(txs),
                                    str(sum(quantity(tx.get("value", "0x0")) for tx in txs))))
                        db.executemany("INSERT OR IGNORE INTO preview_block_addresses(block_number,address) VALUES (?,?)",
                                       ((number, account) for account in participants))
                        done += 1
                    db.executemany("INSERT OR IGNORE INTO preview_power_events(block_number,tx_hash,log_index,kind,user_address,amount_wei,power_gained,data_json) VALUES (?,?,?,?,?,?,?,?)",
                                   ((item["block_number"], item["tx_hash"], item["log_index"], item["kind"], item["user_address"],
                                     item["amount_wei"], item["power_gained"], item["data_json"]) for item in events))
                    db.execute("INSERT OR REPLACE INTO preview_log_scan_ranges(start_block,end_block) VALUES (?,?)",
                               (quantity(blocks[0]["number"]), quantity(blocks[-1]["number"])))
            report.update({"processed_blocks": done, "last_block": quantity(results[-1][0][-1]["number"]),
                           "elapsed_seconds": round(time.monotonic() - began, 2), "updated_at": int(time.time())})
            atomic_json(path, report)
    report.update({"state": "complete", "elapsed_seconds": round(time.monotonic() - began, 2)})
    atomic_json(path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--rpc", default="http://127.0.0.1:18555")
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if not 0 <= args.days <= 2:
        parser.error("Only the current day and up to two recent days may be aggregated")
    print(json.dumps(backfill(args.root.resolve(), args.rpc, args.days, args.workers)), flush=True)


if __name__ == "__main__":
    main()
