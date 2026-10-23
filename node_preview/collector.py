"""Incremental POWER collector for a separate, private preview database.

The confirmed-block checkpoint advances atomically with all affected users,
including their two explicit uplines. RPC errors preserve both old values and a
retry queue. Calibration reads current state only; it never replays history.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    from .chain import RPC, ChainError, ReconcileRequired, UserInfo, address, quantity
except ImportError:  # Supports python node_preview/collector.py --help.
    from chain import RPC, ChainError, ReconcileRequired, UserInfo, address, quantity


def meta_get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM preview_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT INTO preview_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))))


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS addresses(address TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS powers(address TEXT PRIMARY KEY,power TEXT NOT NULL,
        total_burned TEXT,burned TEXT,fetched_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS preview_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS preview_power_state(
        address TEXT PRIMARY KEY,power TEXT NOT NULL,burned TEXT NOT NULL,total_burned TEXT NOT NULL,
        upline1 TEXT,upline2 TEXT,fetched_block INTEGER NOT NULL,fetched_at INTEGER NOT NULL,
        last_claim_time INTEGER NOT NULL,bound_nft TEXT NOT NULL,last_settle_day INTEGER NOT NULL,
        can_mint INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS preview_power_rank ON preview_power_state(length(power) DESC,power DESC,address);
    CREATE TABLE IF NOT EXISTS preview_pending(address TEXT PRIMARY KEY,requested_block INTEGER NOT NULL,
        reason TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS preview_blocks(number INTEGER PRIMARY KEY,block_hash TEXT NOT NULL,
        parent_hash TEXT NOT NULL,timestamp INTEGER NOT NULL,tx_count INTEGER NOT NULL,value_wei TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS preview_blocks_time ON preview_blocks(timestamp,number);
    CREATE TABLE IF NOT EXISTS preview_block_addresses(block_number INTEGER NOT NULL,address TEXT NOT NULL,
        PRIMARY KEY(block_number,address));
    CREATE TABLE IF NOT EXISTS preview_power_events(block_number INTEGER NOT NULL,tx_hash TEXT NOT NULL,
        log_index INTEGER NOT NULL,kind TEXT NOT NULL,user_address TEXT,amount_wei TEXT,power_gained TEXT,
        data_json TEXT NOT NULL,PRIMARY KEY(tx_hash,log_index));
    CREATE TABLE IF NOT EXISTS preview_log_scan_ranges(start_block INTEGER PRIMARY KEY,end_block INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS preview_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,started_at INTEGER NOT NULL,
        finished_at INTEGER NOT NULL,state TEXT NOT NULL,stats_json TEXT NOT NULL);
    """)
    if meta_get(conn, "schema_version") is None:
        for key, value in {"schema_version": 1, "calibration_done": False, "calibration_cursor": "",
                           "calibration_count": 0, "state": "new"}.items():
            meta_set(conn, key, value)
        meta_set(conn, "calibration_total", conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0])
    conn.commit()


def read_status(conn: sqlite3.Connection) -> dict[str, Any]:
    data = {key: json.loads(value) for key, value in conn.execute("SELECT key,value FROM preview_meta")}
    data.update(known_addresses=conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0],
                calibrated_addresses=conn.execute("SELECT COUNT(*) FROM preview_power_state").fetchone()[0],
                pending_count=conn.execute("SELECT COUNT(*) FROM preview_pending").fetchone()[0])
    return data


def _seed_checkpoint(conn: sqlite3.Connection, safe_head: int) -> int:
    # Existing address census is a seed, not a request to scan from genesis.
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "meta" in tables:
        for key in ("live_next_block", "tx_next_block", "next_block"):
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if row:
                return max(0, int(json.loads(row[0])) - 1)
    return safe_head


def _queue(conn: sqlite3.Connection, accounts: set[str], block: int, reason: str) -> None:
    conn.executemany("""INSERT INTO preview_pending(address,requested_block,reason,updated_at)
        VALUES(?,?,?,?) ON CONFLICT(address) DO UPDATE SET
        requested_block=MAX(preview_pending.requested_block,excluded.requested_block),
        reason=CASE WHEN preview_pending.reason='change' THEN 'change' ELSE excluded.reason END,
        updated_at=excluded.updated_at""", ((item, block, reason, int(time.time())) for item in sorted(accounts)))
    conn.commit()


def _write_users(conn: sqlite3.Connection, users: dict[str, UserInfo], block: int) -> None:
    now = int(time.time())
    conn.executemany("INSERT OR IGNORE INTO addresses(address) VALUES(?)", ((account,) for account in users))
    conn.executemany("""INSERT INTO preview_power_state(address,power,burned,total_burned,upline1,upline2,
        fetched_block,fetched_at,last_claim_time,bound_nft,last_settle_day,can_mint)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(address) DO UPDATE SET
        power=excluded.power,burned=excluded.burned,total_burned=excluded.total_burned,
        upline1=excluded.upline1,upline2=excluded.upline2,fetched_block=excluded.fetched_block,
        fetched_at=excluded.fetched_at,last_claim_time=excluded.last_claim_time,
        bound_nft=excluded.bound_nft,last_settle_day=excluded.last_settle_day,can_mint=excluded.can_mint
        WHERE excluded.fetched_block>=preview_power_state.fetched_block""",
        ((account, str(user.power), str(user.burned), str(user.total_burned), user.upline1, user.upline2,
          block, now, user.last_claim_time, str(user.bound_nft), user.last_settle_day, int(user.can_mint))
         for account, user in users.items()))
    # Compatibility for existing analysis tools. Only node-confirmed values enter this table.
    conn.executemany("""INSERT INTO powers(address,power,total_burned,burned,fetched_at) VALUES(?,?,?,?,?)
        ON CONFLICT(address) DO UPDATE SET power=excluded.power,total_burned=excluded.total_burned,
        burned=excluded.burned,fetched_at=excluded.fetched_at""",
        ((account, str(user.power), str(user.total_burned), str(user.burned), now) for account, user in users.items()))
    conn.executemany("DELETE FROM preview_pending WHERE address=? AND requested_block<=?",
                     ((account, block) for account in users))


def _write_block(conn: sqlite3.Connection, block: dict[str, Any]) -> None:
    number = quantity(block["number"])
    transactions = block["transactions"]
    value = 0
    participants = set()
    for tx in transactions:
        if not isinstance(tx, dict):
            raise ChainError(f"Block {number} transaction objects missing")
        value += quantity(tx.get("value", "0x0"))
        for field in ("from", "to"):
            candidate = address(tx.get(field))
            # Same user-address rule as the existing site's daily metrics.
            if candidate and int(candidate, 16) >= 2**64:
                participants.add(candidate)
    conn.execute("""INSERT INTO preview_blocks(number,block_hash,parent_hash,timestamp,tx_count,value_wei)
        VALUES(?,?,?,?,?,?) ON CONFLICT(number) DO NOTHING""",
        (number, block["hash"].lower(), block["parentHash"].lower(), quantity(block["timestamp"]),
         len(transactions), str(value)))
    conn.executemany("INSERT OR IGNORE INTO preview_block_addresses(block_number,address) VALUES(?,?)",
                     ((number, account) for account in participants))


def _fetch_changes(rpc: RPC, start: int, end: int, previous_hash: str) -> tuple[list[dict], set[str], list[dict], int]:
    blocks = rpc.blocks(start, end)
    if blocks[0]["parentHash"].lower() != previous_hash.lower():
        raise ReconcileRequired("Next block parent does not match checkpoint hash")
    touched: set[str] = set()
    creations = []
    for block in blocks:
        for field in ("miner", "author"):
            candidate = address(block.get(field))
            if candidate:
                touched.add(candidate)
        for tx in block["transactions"]:
            if not isinstance(tx, dict):
                raise ChainError("Transaction objects missing from full block response")
            for field in ("from", "to"):
                candidate = address(tx.get(field))
                if candidate:
                    touched.add(candidate)
            if tx.get("to") is None:
                creations.append(tx["hash"])
    if creations:
        receipts = rpc.batch([("eth_getTransactionReceipt", [tx_hash]) for tx_hash in creations])
        hashes = {quantity(block["number"]): block["hash"].lower() for block in blocks}
        for receipt in receipts:
            if not isinstance(receipt, dict):
                raise ChainError("Missing contract creation receipt")
            if receipt.get("blockHash", "").lower() != hashes.get(quantity(receipt.get("blockNumber"))):
                raise ReconcileRequired("Creation receipt no longer belongs to the pinned block batch")
            candidate = address(receipt.get("contractAddress"))
            if candidate:
                touched.add(candidate)
    block_hashes = {quantity(block["number"]): block["hash"].lower() for block in blocks}
    events, unknown = [], 0
    for log in rpc.logs(start, end):
        if log.get("blockHash", "").lower() != block_hashes.get(quantity(log.get("blockNumber"))):
            raise ReconcileRequired("POWER log no longer belongs to the pinned block batch")
        found, event = rpc.decode_log(log)
        touched.update(found)
        if event:
            events.append(event)
        else:
            unknown += 1
    return blocks, touched, events, unknown


def _refresh_with_uplines(conn: sqlite3.Connection, rpc: RPC, accounts: set[str], block: int,
                          reason: str = "change", expand: bool = True) -> dict[str, UserInfo]:
    _queue(conn, accounts, block, reason)
    users = rpc.users(sorted(accounts), block) if accounts else {}
    if expand:
        uplines = {parent for user in users.values() for parent in (user.upline1, user.upline2) if parent}
        # Old relationships may also have received effects earlier in this batch.
        for account in accounts:
            row = conn.execute("SELECT upline1,upline2 FROM preview_power_state WHERE address=?", (account,)).fetchone()
            if row:
                uplines.update(parent for parent in row if parent)
        missing = uplines - users.keys()
        if missing:
            _queue(conn, missing, block, reason)
            users.update(rpc.users(sorted(missing), block))
    return users


def _verify_hash(rpc: RPC, number: int, expected: str) -> None:
    if rpc.block(number)["hash"].lower() != expected.lower():
        raise ReconcileRequired(f"Confirmed block {number} hash changed; manual reconciliation required")


def _calibrate(conn: sqlite3.Connection, rpc: RPC, block: int, block_hash: str, deadline: float,
               maximum: int, batch_size: int, stats: dict[str, Any], sweep_interval: int) -> None:
    done = bool(meta_get(conn, "calibration_done", False))
    if done:
        if time.time() < meta_get(conn, "sweep_due", 0):
            return
        cursor_key = "sweep_cursor"
    else:
        cursor_key = "calibration_cursor"
    processed = 0
    while processed < maximum and time.monotonic() < deadline:
        cursor = meta_get(conn, cursor_key, "")
        accounts = [row[0] for row in conn.execute("SELECT address FROM addresses WHERE address>? ORDER BY address LIMIT ?",
                     (cursor, min(batch_size, maximum - processed)))]
        if not accounts:
            with conn:
                if done:
                    meta_set(conn, "sweep_cursor", "")
                    meta_set(conn, "sweep_completed_at", int(time.time()))
                    meta_set(conn, "sweep_due", int(time.time()) + sweep_interval)
                else:
                    # New addresses discovered during the pass are already refreshed by change batches.
                    missing = conn.execute("SELECT a.address FROM addresses a LEFT JOIN preview_power_state p "
                        "ON p.address=a.address WHERE p.address IS NULL LIMIT 1").fetchone()
                    if missing:
                        meta_set(conn, "calibration_cursor", "")
                        continue
                    meta_set(conn, "calibration_done", True)
                    meta_set(conn, "calibration_completed_at", int(time.time()))
                    meta_set(conn, "calibration_count", conn.execute("SELECT COUNT(*) FROM preview_power_state").fetchone()[0])
                    meta_set(conn, "sweep_due", int(time.time()) + sweep_interval)
            break
        users = _refresh_with_uplines(conn, rpc, set(accounts), block, "calibration", expand=False)
        _verify_hash(rpc, block, block_hash)
        with conn:
            _write_users(conn, users, block)
            meta_set(conn, cursor_key, accounts[-1])
            if not done:
                meta_set(conn, "calibration_count", meta_get(conn, "calibration_count", 0) + len(accounts))
        processed += len(accounts)
        stats["calibration_updated"] += len(accounts)
    # Reaching the exact last address should not require another full cycle to become ready.
    if not done and not conn.execute("SELECT 1 FROM addresses WHERE address>? LIMIT 1", (meta_get(conn, cursor_key, ""),)).fetchone():
        missing = conn.execute("SELECT 1 FROM addresses a LEFT JOIN preview_power_state p ON p.address=a.address "
                               "WHERE p.address IS NULL LIMIT 1").fetchone()
        if not missing:
            with conn:
                meta_set(conn, "calibration_done", True)
                meta_set(conn, "calibration_completed_at", int(time.time()))
                meta_set(conn, "calibration_count", conn.execute("SELECT COUNT(*) FROM preview_power_state").fetchone()[0])
                meta_set(conn, "sweep_due", int(time.time()) + sweep_interval)


def run_once(db_path: str | Path, rpc_url: str = "http://127.0.0.1:18555", *,
             confirmations: int = 6, block_batch: int = 40, rpc_batch: int = 100,
             max_seconds: float = 45, max_blocks: int = 1200, calibration_batch: int = 100,
             calibration_limit: int = 3000, sweep_interval: int = 86400,
             rpc_client: RPC | None = None) -> dict[str, Any]:
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    stats: dict[str, Any] = {"started_at": int(time.time()), "blocks": 0, "transactions": 0,
        "users_updated": 0, "calibration_updated": 0, "unknown_power_events": 0}
    with open(str(db_path) + ".collector.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "busy", "message": "Previous collector is still running"}
        conn = sqlite3.connect(db_path, timeout=15)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        initialize(conn)
        rpc = rpc_client or RPC(rpc_url, batch_size=rpc_batch)
        deadline = start + max_seconds
        try:
            if meta_get(conn, "state") == "needs_reconcile":
                raise ReconcileRequired(meta_get(conn, "last_error", "Manual reconciliation is required"))
            head = rpc.head()
            safe_head = max(0, head - confirmations)
            checkpoint = meta_get(conn, "checkpoint_block")
            if checkpoint is None:
                checkpoint = _seed_checkpoint(conn, safe_head)
                anchor = rpc.block(checkpoint, True)
                with conn:
                    meta_set(conn, "checkpoint_block", checkpoint)
                    meta_set(conn, "checkpoint_hash", anchor["hash"].lower())
                    meta_set(conn, "collection_start_block", checkpoint)
                    meta_set(conn, "collection_start_timestamp", quantity(anchor["timestamp"]))
                    _write_block(conn, anchor)
            checkpoint_hash = meta_get(conn, "checkpoint_hash")
            _verify_hash(rpc, checkpoint, checkpoint_hash)
            prior_state_block = meta_get(conn, "power_state_block")
            prior_state_hash = meta_get(conn, "power_state_hash")
            if prior_state_block is not None and prior_state_hash:
                _verify_hash(rpc, prior_state_block, prior_state_hash)
            # Full/pruned nodes retain blocks and logs much longer than EVM
            # state. Recover missed *changes* from old blocks, but query their
            # affected accounts at this run's confirmed tip. The publisher
            # treats catching_up as incomplete until the discovery checkpoint
            # reaches this tip; fetched_block always identifies the real read.
            state_block = max(checkpoint, safe_head)
            state_hash = rpc.block(state_block)["hash"].lower()
            with conn:
                meta_set(conn, "state", "collecting")
                meta_set(conn, "head", head)
                meta_set(conn, "safe_head", safe_head)
                meta_set(conn, "last_run", stats["started_at"])
            stats.update(from_block=checkpoint + 1, head=head, safe_head=safe_head, state_block=state_block)
            active_start = time.monotonic()
            while checkpoint < safe_head and stats["blocks"] < max_blocks and time.monotonic() < deadline:
                end = min(safe_head, checkpoint + block_batch, checkpoint + max_blocks - stats["blocks"])
                blocks, touched, events, unknown = _fetch_changes(rpc, checkpoint + 1, end, checkpoint_hash)
                users = _refresh_with_uplines(conn, rpc, touched, state_block)
                total_power = rpc.global_power(state_block)
                end_hash = blocks[-1]["hash"].lower()
                _verify_hash(rpc, end, end_hash)
                _verify_hash(rpc, state_block, state_hash)
                # No public checkpoint can claim success while any power lookup failed.
                with conn:
                    _write_users(conn, users, state_block)
                    for block in blocks:
                        _write_block(conn, block)
                    conn.executemany("""INSERT OR REPLACE INTO preview_power_events
                        (block_number,tx_hash,log_index,kind,user_address,amount_wei,power_gained,data_json)
                        VALUES(:block_number,:tx_hash,:log_index,:kind,:user_address,:amount_wei,:power_gained,:data_json)""", events)
                    conn.execute("INSERT OR REPLACE INTO preview_log_scan_ranges(start_block,end_block) VALUES(?,?)",
                                 (checkpoint + 1, end))
                    meta_set(conn, "checkpoint_block", end)
                    meta_set(conn, "checkpoint_hash", end_hash)
                    meta_set(conn, "checkpoint_timestamp", quantity(blocks[-1]["timestamp"]))
                    meta_set(conn, "global_power", str(total_power))
                    meta_set(conn, "global_power_block", state_block)
                    meta_set(conn, "power_state_block", state_block)
                    meta_set(conn, "power_state_hash", state_hash)
                checkpoint, checkpoint_hash = end, end_hash
                stats["blocks"] += len(blocks)
                stats["transactions"] += sum(len(item["transactions"]) for item in blocks)
                stats["users_updated"] += len(users)
                stats["unknown_power_events"] += unknown
            stats["active_seconds"] = round(time.monotonic() - active_start, 3)
            if meta_get(conn, "global_power") is None:
                total_power = rpc.global_power(state_block)
                _verify_hash(rpc, state_block, state_hash)
                with conn:
                    meta_set(conn, "global_power", str(total_power))
                    meta_set(conn, "global_power_block", state_block)
                    meta_set(conn, "power_state_block", state_block)
                    meta_set(conn, "power_state_hash", state_hash)
            # Catch-up has priority; do not spend calibration budget while behind.
            calibration_start = time.monotonic()
            if checkpoint >= safe_head and calibration_limit > 0:
                _calibrate(conn, rpc, checkpoint, checkpoint_hash, deadline,
                           calibration_limit, calibration_batch, stats, sweep_interval)
            stats["calibration_seconds"] = round(time.monotonic() - calibration_start, 3)
            stats["state"] = "ready" if meta_get(conn, "calibration_done", False) else "warming"
            stats["through_block"] = checkpoint
            stats["lag_blocks"] = max(0, safe_head - checkpoint)
            if stats["lag_blocks"]:
                stats["state"] = "catching_up"
            with conn:
                meta_set(conn, "state", stats["state"])
                meta_set(conn, "last_success", int(time.time()))
                meta_set(conn, "last_error", None)
        except Exception as exc:
            conn.rollback()
            stats["state"] = "needs_reconcile" if isinstance(exc, ReconcileRequired) else "error"
            stats["error"] = f"{type(exc).__name__}: {exc}"
            with conn:
                conn.execute("UPDATE preview_pending SET attempts=attempts+1,last_error=?,updated_at=?",
                             (stats["error"][:2000], int(time.time())))
                meta_set(conn, "state", stats["state"])
                meta_set(conn, "last_error", stats["error"])
                meta_set(conn, "last_run", stats["started_at"])
        finally:
            stats["finished_at"] = int(time.time())
            stats["elapsed_seconds"] = round(time.monotonic() - start, 3)
            with conn:
                meta_set(conn, "last_statistics", stats)
                conn.execute("INSERT INTO preview_runs(started_at,finished_at,state,stats_json) VALUES(?,?,?,?)",
                             (stats["started_at"], stats["finished_at"], stats["state"], json.dumps(stats)))
                conn.execute("DELETE FROM preview_runs WHERE id < (SELECT MAX(id)-2880 FROM preview_runs)")
            stats["status"] = read_status(conn)
            conn.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("MARS_PREVIEW_DB", "preview.sqlite3"))
    parser.add_argument("--rpc", default=os.environ.get("MARS_PREVIEW_RPC", "http://127.0.0.1:18555"))
    parser.add_argument("--confirmations", type=int, default=6)
    parser.add_argument("--block-batch", type=int, default=40)
    parser.add_argument("--rpc-batch", type=int, default=100)
    parser.add_argument("--max-seconds", type=float, default=45)
    parser.add_argument("--max-blocks", type=int, default=1200)
    parser.add_argument("--calibration-batch", type=int, default=100)
    parser.add_argument("--calibration-limit", type=int, default=3000)
    parser.add_argument("--sweep-interval", type=int, default=86400)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=60)
    args = parser.parse_args(argv)
    if args.confirmations < 0 or min(args.block_batch, args.rpc_batch, args.max_blocks,
        args.calibration_batch, args.sweep_interval, args.interval, args.max_seconds) <= 0 or args.calibration_limit < 0:
        parser.error("Batch sizes, intervals and budgets must be positive; confirmations/calibration limit cannot be negative")
    while True:
        started = time.monotonic()
        result = run_once(args.db, args.rpc, confirmations=args.confirmations, block_batch=args.block_batch,
            rpc_batch=args.rpc_batch, max_seconds=args.max_seconds, max_blocks=args.max_blocks,
            calibration_batch=args.calibration_batch, calibration_limit=args.calibration_limit,
            sweep_interval=args.sweep_interval)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not args.loop:
            return 1 if result.get("state") in ("error", "needs_reconcile") else 0
        time.sleep(max(1, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
