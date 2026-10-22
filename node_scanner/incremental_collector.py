#!/usr/bin/env python3
"""Incrementally refresh address power and MARS balances from the local node.

This collector advances from the last durable block checkpoint. It indexes
transaction senders/recipients, block producers, contract creations and indexed
POWER log topics. It does not recover historical internal-call participants;
that requires a separate historical trace/replay pass.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path.home() / "marschain-node"
OUT = ROOT / "full-power-export"
DB_PATH = OUT / "scan.sqlite3"
RPC = "http://127.0.0.1:18555"
EXPLORER = "https://explorer.marschain.net/api"
POWER_CONTRACT = "0x0000000000000000000000000000000000001001"
BLOCKS_PER_BATCH = 100
CONFIRMATIONS = 6
POWER_WORKERS = 24
RPC_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
EXPLORER_HEADERS = {
    "User-Agent": "Mozilla/5.0 MarsChainIncrementalCollector/1.0",
    "Accept": "application/json",
    "Referer": "https://explorer.marschain.net/",
}

# Prefer IPv4 on this host; its IPv6 route to the explorer has intermittent TLS hangs.
_getaddrinfo = socket.getaddrinfo


def _prefer_ipv4(*args: Any, **kwargs: Any) -> list[Any]:
    results = _getaddrinfo(*args, **kwargs)
    ipv4 = [item for item in results if item[0] == socket.AF_INET]
    return ipv4 or results


socket.getaddrinfo = _prefer_ipv4


def rpc(method: str, params: list[Any]) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=payload, headers=RPC_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=120) as response:
        data = json.loads(response.read())
    if data.get("error"):
        raise RuntimeError(f"{method}: {data['error']}")
    return data.get("result")


def rpc_batch(calls: list[tuple[str, list[Any]]]) -> list[Any]:
    payload = [
        {"jsonrpc": "2.0", "id": index, "method": method, "params": params}
        for index, (method, params) in enumerate(calls, 1)
    ]
    req = urllib.request.Request(RPC, data=json.dumps(payload).encode(), headers=RPC_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=180) as response:
        data = json.loads(response.read())
    if not isinstance(data, list):
        raise RuntimeError("RPC batch returned a non-list response")
    by_id = {item.get("id"): item for item in data if isinstance(item, dict)}
    results = []
    for index, _ in enumerate(calls, 1):
        item = by_id.get(index, {})
        if item.get("error"):
            raise RuntimeError(str(item["error"]))
        results.append(item.get("result"))
    return results


def valid_address(value: Any) -> str | None:
    if isinstance(value, str) and len(value) == 42 and value[:2].lower() == "0x":
        addr = value.lower()
        if addr != "0x" + "0" * 40:
            return addr
    return None


def topic_address(topic: Any) -> str | None:
    if not isinstance(topic, str) or len(topic) != 66 or not topic.startswith("0x"):
        return None
    if topic[2:26] != "0" * 24:
        return None
    return valid_address("0x" + topic[-40:])


def get_logs(start: int, end: int, depth: int = 0) -> list[dict[str, Any]]:
    try:
        result = rpc("eth_getLogs", [{"fromBlock": hex(start), "toBlock": hex(end), "address": POWER_CONTRACT}])
        return result or []
    except Exception:
        if start >= end or depth >= 12:
            raise
        mid = (start + end) // 2
        return get_logs(start, mid, depth + 1) + get_logs(mid + 1, end, depth + 1)


def fetch_range(start: int, end: int) -> tuple[set[str], list[str], int]:
    calls = [("eth_getBlockByNumber", [hex(number), True]) for number in range(start, end + 1)]
    blocks = rpc_batch(calls)
    touched: set[str] = set()
    creations: list[str] = []
    transactions = 0
    for number, block in zip(range(start, end + 1), blocks):
        if not isinstance(block, dict):
            raise RuntimeError(f"missing block {number}")
        touched.update(filter(None, (valid_address(block.get("miner")), valid_address(block.get("author")))))
        txs = block.get("transactions") or []
        transactions += len(txs)
        for tx in txs:
            touched.update(filter(None, (valid_address(tx.get("from")), valid_address(tx.get("to")))))
            if tx.get("to") is None and isinstance(tx.get("hash"), str):
                creations.append(tx["hash"].lower())
    for log in get_logs(start, end):
        for topic in (log.get("topics") or [])[1:]:
            address = topic_address(topic)
            if address:
                touched.add(address)
    return touched, creations, transactions


def creation_addresses(hashes: list[str]) -> set[str]:
    created: set[str] = set()
    for offset in range(0, len(hashes), 100):
        batch = hashes[offset : offset + 100]
        receipts = rpc_batch([("eth_getTransactionReceipt", [tx_hash]) for tx_hash in batch])
        for receipt in receipts:
            if isinstance(receipt, dict):
                address = valid_address(receipt.get("contractAddress"))
                if address:
                    created.add(address)
    return created


def explorer_power(address: str) -> tuple[str, str | None, str | None, str | None]:
    url = EXPLORER + "/power/" + urllib.parse.quote(address, safe="")
    error = "unknown error"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=EXPLORER_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read())
            if not isinstance(data, dict) or "power" not in data:
                raise RuntimeError("unexpected power API response")
            return (
                address,
                str(data.get("power", "0") or "0"),
                str(data.get("totalBurnedAmount", "0") or "0"),
                str(data.get("burnedAmount", "0") or "0"),
            )
        except Exception as exc:
            error = str(exc)
            time.sleep(min(8, 2**attempt))
    return address, None, None, error


def update_balances(conn: sqlite3.Connection, addresses: list[str], block: int) -> int:
    updated = 0
    for offset in range(0, len(addresses), 100):
        batch = addresses[offset : offset + 100]
        try:
            results = rpc_batch([("eth_getBalance", [address, hex(block)]) for address in batch])
        except Exception as exc:
            now = int(time.time())
            for address in batch:
                conn.execute(
                    "INSERT OR REPLACE INTO balance_failures(address,attempts,error,updated_at) "
                    "VALUES (?,COALESCE((SELECT attempts+1 FROM balance_failures WHERE address=?),1),?,?)",
                    (address, address, str(exc), now),
                )
            continue
        now = int(time.time())
        for address, value in zip(batch, results):
            if not isinstance(value, str) or not value.startswith("0x"):
                raise RuntimeError(f"invalid balance response for {address}")
            conn.execute(
                "INSERT OR REPLACE INTO balances(address,balance_wei,block_number,fetched_at) VALUES (?,?,?,?)",
                (address, str(int(value, 16)), block, now),
            )
            conn.execute("DELETE FROM balance_failures WHERE address=?", (address,))
            updated += 1
    return updated


def export_csv(conn: sqlite3.Connection, block: int) -> None:
    import csv

    rows = conn.execute(
        "SELECT a.address,p.power,b.balance_wei FROM addresses a "
        "LEFT JOIN powers p ON p.address=a.address LEFT JOIN balances b ON b.address=a.address"
    ).fetchall()
    rows.sort(key=lambda row: (row[1] is None, -int(row[1] or 0), row[0]))
    path = OUT / "marschain_full_power_rank.csv"
    temp = path.with_suffix(".csv.tmp")

    def mars(wei: str | None) -> str:
        if wei is None:
            return ""
        whole, fraction = divmod(int(wei), 10**18)
        return str(whole) if fraction == 0 else f"{whole}.{fraction:018d}".rstrip("0")

    with temp.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["排名", "地址", "算力（原始值）", "MARS余额（MARS）"])
        for rank, (address, power, balance) in enumerate(rows, 1):
            writer.writerow([rank, address, power or "", mars(balance)])
    os.replace(temp, path)
    meta_set(conn, "incremental_export_block", block)


def meta_get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, json.dumps(value)))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS pending_updates (address TEXT PRIMARY KEY, block_number INTEGER NOT NULL)")
    conn.commit()

    head = int(rpc("eth_blockNumber", []), 16)
    safe_head = max(0, head - CONFIRMATIONS)
    next_block = meta_get(conn, "live_next_block")
    if next_block is None:
        # Resume immediately after the last historical transaction-scan checkpoint.
        next_block = int(meta_get(conn, "tx_next_block", 0))
        meta_set(conn, "live_next_block", next_block)
        conn.commit()
    touched: set[str] = set()
    total_transactions = 0
    first = next_block
    while next_block <= safe_head:
        end = min(safe_head, next_block + BLOCKS_PER_BATCH - 1)
        started = time.time()
        found, creations, transactions = fetch_range(next_block, end)
        found.update(creation_addresses(creations))
        touched.update(found)
        now = time.time()
        conn.execute("BEGIN")
        conn.executemany("INSERT OR IGNORE INTO addresses(address) VALUES (?)", ((address,) for address in found))
        conn.executemany(
            "INSERT OR REPLACE INTO pending_updates(address,block_number) VALUES (?,?)",
            ((address, end) for address in found),
        )
        conn.execute(
            "INSERT OR REPLACE INTO tx_scan_ranges(start_block,end_block,transactions,elapsed) VALUES (?,?,?,?)",
            (next_block, end, transactions, now - started),
        )
        total_transactions += transactions
        next_block = end + 1
        meta_set(conn, "live_next_block", next_block)
        meta_set(conn, "live_safe_head", safe_head)
        conn.commit()

    touched.update(row[0] for row in conn.execute("SELECT address FROM pending_updates"))
    retry_after = int(time.time()) - 60
    retry_power = {row[0] for row in conn.execute("SELECT address FROM lookup_failures WHERE updated_at<=?", (retry_after,))}
    retry_balances = {row[0] for row in conn.execute("SELECT address FROM balance_failures WHERE updated_at<=?", (retry_after,))}
    touched.update(retry_power)
    touched.update(retry_balances)
    if not touched and first > safe_head:
        print(f"[idle] head={head} safe_head={safe_head} next={next_block}", flush=True)
        conn.close()
        return 0

    addresses = sorted(touched)
    power_failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=POWER_WORKERS) as pool:
        for address, power, total_burned, burned_or_error in pool.map(explorer_power, addresses):
            if power is None:
                power_failures += 1
                conn.execute(
                    "INSERT OR REPLACE INTO lookup_failures(address,attempts,error,updated_at) "
                    "VALUES (?,COALESCE((SELECT attempts+1 FROM lookup_failures WHERE address=?),1),?,?)",
                    (address, address, burned_or_error, int(time.time())),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO powers(address,power,total_burned,burned,fetched_at) VALUES (?,?,?,?,?)",
                    (address, power, total_burned, burned_or_error, int(time.time())),
                )
                conn.execute("DELETE FROM lookup_failures WHERE address=?", (address,))
    conn.commit()

    balance_block = max(0, int(rpc("eth_blockNumber", []), 16) - CONFIRMATIONS)
    balance_count = update_balances(conn, addresses, balance_block) if addresses else 0
    meta_set(conn, "live_last_run", int(time.time()))
    meta_set(conn, "live_last_processed_block", safe_head)
    conn.executemany("DELETE FROM pending_updates WHERE address=?", ((address,) for address in addresses))
    conn.commit()
    export_csv(conn, balance_block)
    conn.commit()

    print(
        json.dumps(
            {
                "from_block": first,
                "through_block": safe_head,
                "head": head,
                "transactions": total_transactions,
                "touched_addresses": len(addresses),
                "balances_updated": balance_count,
                "power_failures": power_failures,
                "known_addresses": conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[fatal] {exc}", file=sys.stderr, flush=True)
        raise
