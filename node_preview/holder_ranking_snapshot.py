"""One isolated, block-pinned native MARS holder preview (public schema 2).

Sources are read-only. Verified systems are identity-only: their balances are
never queried, ranked, published or included in the share denominator.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable

ADDRESS = re.compile(r"0x[0-9a-f]{40}\Z")
QUANTITY = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z")
HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
CODE = re.compile(r"0x(?:[0-9a-fA-F]{2})*\Z")
ZERO = "0x" + "0" * 40
DEAD = "0x" + "0" * 36 + "dead"
POWER = "0x" + "0" * 36 + "1001"
# Verified POWER contract; never infer a system from its address range.
SYSTEM_ADDRESSES = frozenset({POWER})
CONFIRMATIONS = 6


def validate_options(max_seconds: float, batch_size: int, workers: int) -> None:
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds) or not 0 < max_seconds <= 180:
        raise ValueError("max_seconds must be finite and between 0 and 180")
    if type(batch_size) is not int or not 1 <= batch_size <= 200:
        raise ValueError("batch_size must be an integer between 1 and 200")
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("workers must be an integer between 1 and 4")


def quantity(value: object, name: str) -> int:
    if not isinstance(value, str) or not QUANTITY.fullmatch(value):
        raise RuntimeError(f"Invalid {name} hex quantity")
    number = int(value, 16)
    if number.bit_length() > 256:
        raise RuntimeError(f"{name} exceeds uint256")
    return number


def decode_rpc_batch(data: object, expected_count: int) -> list:
    if not isinstance(data, list) or len(data) != expected_count:
        raise RuntimeError("Incomplete JSON-RPC batch")
    indexed = {}
    for item in data:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
            raise RuntimeError("Invalid JSON-RPC response")
        identifier = item.get("id")
        if type(identifier) is not int or not 0 <= identifier < expected_count or identifier in indexed:
            raise RuntimeError("Unexpected or duplicate JSON-RPC response id")
        if "error" in item or "result" not in item:
            raise RuntimeError("JSON-RPC response failed or lacks a result")
        indexed[identifier] = item["result"]
    if len(indexed) != expected_count:
        raise RuntimeError("Missing JSON-RPC response id")
    return [indexed[index] for index in range(expected_count)]


class JsonRpcClient:
    def __init__(self, deadline: float, *, clock: Callable = time.monotonic,
                 opener: Callable = urllib.request.urlopen):
        self.deadline, self.clock, self.opener = deadline, clock, opener

    def __call__(self, calls: list[tuple[str, list]]) -> list:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("Snapshot exceeded the block-age budget")
        payload = [{"jsonrpc": "2.0", "id": index, "method": method, "params": params}
                   for index, (method, params) in enumerate(calls)]
        request = urllib.request.Request("http://127.0.0.1:18555", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        with self.opener(request, timeout=min(20, remaining)) as response:
            result = decode_rpc_batch(json.load(response), len(calls))
        if self.clock() >= self.deadline:
            raise TimeoutError("Snapshot exceeded the block-age budget")
        return result


def read_addresses(sources: list[Path]) -> tuple[set[str], list[dict]]:
    addresses = {ZERO, DEAD, *SYSTEM_ADDRESSES}
    counts = []
    for source in sources:
        source = Path(source).resolve()
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=4) as database:
            database.execute("PRAGMA query_only=ON")
            found, rejected = set(), 0
            for (value,) in database.execute("SELECT address FROM addresses"):
                if isinstance(value, str) and ADDRESS.fullmatch(value.lower()):
                    found.add(value.lower())
                else:
                    rejected += 1
        addresses.update(found)
        counts.append({"path": str(source), "address_count": len(found), "invalid_rows": rejected})
    return addresses, counts


def block_header(value: object, expected_number: int) -> dict:
    if not isinstance(value, dict) or quantity(value.get("number"), "block number") != expected_number:
        raise RuntimeError("Pinned block header unavailable or has a different number")
    if not isinstance(value.get("hash"), str) or not HASH.fullmatch(value["hash"]):
        raise RuntimeError("Invalid pinned block hash")
    quantity(value.get("timestamp"), "block timestamp")
    return value


def collect_snapshot(sources: list[Path], *, rpc: Callable | None = None,
                     max_seconds: float = 180, batch_size: int = 200, workers: int = 4,
                     clock: Callable = time.monotonic, wall_clock: Callable = time.time,
                     progress: Callable | None = None) -> dict:
    """Acquire every address at one confirmed block before returning artifacts.

    Inject rpc and clocks for deterministic offline audits. The normal CLI uses
    only the loopback node and the existing read-only address sources.
    """
    validate_options(max_seconds, batch_size, workers)
    started = clock()
    deadline = started + max_seconds

    def check_budget() -> None:
        if clock() >= deadline:
            raise TimeoutError("Snapshot exceeded the block-age budget; prior output is unchanged")

    transport = rpc if rpc is not None else JsonRpcClient(deadline, clock=clock)

    def call(calls: list[tuple[str, list]]) -> list:
        check_budget()
        result = transport(calls)
        check_budget()
        if not isinstance(result, list) or len(result) != len(calls):
            raise RuntimeError("Incomplete RPC result batch")
        return result

    addresses, source_counts = read_addresses(sources)
    check_budget()
    syncing, head = call([("eth_syncing", []), ("eth_blockNumber", [])])
    if syncing is not False:
        raise RuntimeError("Node is syncing; snapshot aborted")
    block_number = quantity(head, "chain head") - CONFIRMATIONS
    if block_number < 0:
        raise RuntimeError("Chain has insufficient confirmed blocks")
    block_tag = hex(block_number)
    header = block_header(call([("eth_getBlockByNumber", [block_tag, False])])[0], block_number)
    address_list = sorted(addresses - SYSTEM_ADDRESSES)
    batches = iter(address_list[i:i + batch_size] for i in range(0, len(address_list), batch_size))
    balances: dict[str, int] = {}

    def balance_batch(batch: list[str]) -> list[tuple[str, int]]:
        values = call([("eth_getBalance", [address, block_tag]) for address in batch])
        return [(address, quantity(value, "native MARS balance")) for address, value in zip(batch, values)]

    if progress:
        progress({"phase": "start", "addresses": len(address_list), "block": block_number})
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    pending = set()
    try:
        for _ in range(workers):
            batch = next(batches, None)
            if batch:
                pending.add(executor.submit(balance_batch, batch))
        last_log = clock()
        while pending:
            check_budget()
            done, pending = concurrent.futures.wait(pending, timeout=min(1, max(0, deadline - clock())),
                                                   return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                balances.update(future.result())
                batch = next(batches, None)
                if batch:
                    pending.add(executor.submit(balance_batch, batch))
            if progress and clock() - last_log >= 10:
                progress({"phase": "balances", "done": len(balances), "total": len(address_list),
                          "seconds": round(clock() - started, 2)})
                last_log = clock()
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    if set(balances) != set(address_list):
        raise RuntimeError("Address snapshot is incomplete")
    check_budget()
    ranked = sorted(balances, key=lambda address: (-balances[address], address))
    selected = ranked[:100]
    kinds = {address: "system" for address in SYSTEM_ADDRESSES}
    code_evidence = {}
    code_addresses = []
    for address in selected:
        if address in (ZERO, DEAD):
            kinds[address] = "burn"
        else:
            code_addresses.append(address)
    for offset in range(0, len(code_addresses), batch_size):
        batch = code_addresses[offset:offset + batch_size]
        codes = call([("eth_getCode", [address, block_tag]) for address in batch])
        for address, code in zip(batch, codes):
            if not isinstance(code, str) or not CODE.fullmatch(code):
                raise RuntimeError("Invalid contract-code response")
            kinds[address] = "wallet" if code == "0x" else "contract"
            code_evidence[address] = code
    rows = [{"address": address, "balance_wei": str(balances[address]), "kind": kinds[address], "rank": index}
            for index, address in enumerate(selected, 1)]
    systems = [{"address": address, "kind": "system", "name": "POWER"} for address in sorted(SYSTEM_ADDRESSES)]
    final_header = block_header(call([("eth_getBlockByNumber", [block_tag, False])])[0], block_number)
    if final_header["hash"].lower() != header["hash"].lower() or quantity(final_header["timestamp"], "block timestamp") != quantity(header["timestamp"], "block timestamp"):
        raise RuntimeError("Pinned block changed; snapshot rejected")
    total = sum(balances.values())
    check_budget()
    meta = {"block_number": block_number, "block_hash": header["hash"].lower(),
            "block_timestamp": quantity(header["timestamp"], "block timestamp"), "address_count": len(addresses),
            "ranking_address_count": len(address_list), "system_balances_excluded": True,
            "share_denominator_scope": "indexed_non_system_addresses", "public_schema": 2,
            "scope_label": "已收录地址", "complete": True, "network_complete": False,
            "snapshot_label": "同一区块余额快照",
            "share_denominator_label": "已收录非系统地址余额合计（含普通钱包、非系统合约及已识别销毁地址，非流通量）",
            "share_denominator_wei": str(total), "ordinary_count": None, "contracts_count": None,
            "excluded_count": None, "classified_candidate_count": len(kinds),
            "classification_basis": "zero/dead burn markers; verified POWER system contract; other candidates classified by eth_getCode at the snapshot block",
            "ranking_scope": "all_indexed_addresses_except_separately_listed_systems",
            "created_at": int(wall_clock()), "elapsed_seconds": round(clock() - started, 3)}
    return {"public": {"meta": meta, "systems": systems, "rows": rows},
            "private": {"meta": meta, "sources": source_counts,
                        "balances": [{"address": address, "balance_wei": str(balances[address])} for address in address_list],
                        "classified_candidates": kinds,
                        "classification_evidence": {"block_number": block_number, "block_hash": header["hash"].lower(),
                                                    "eth_getCode": code_evidence,
                                                    "burn_markers": sorted({ZERO, DEAD} & set(selected)),
                                                    "system_identity_allowlist": sorted(SYSTEM_ADDRESSES)}},
            "receipt": {"meta": meta, "sources": source_counts, "public_systems": len(systems),
                        "public_rows": len(rows), "system_balance_query_count": 0}}


def atomic_json(path: Path, value: object) -> None:
    """A failed serialization/write/replace preserves the previous file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_snapshot(root: Path, artifacts: dict, *, writer: Callable = atomic_json) -> None:
    """Stage the complete set, publish JSON last, and roll back commit failures."""
    root = Path(root)
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    targets = [(state / "snapshot-private.json", artifacts["private"]),
               (state / "snapshot-receipt.json", artifacts["receipt"]),
               (root / "site/data/holders.json", artifacts["public"])]
    with tempfile.TemporaryDirectory(prefix=".holder-write-", dir=state) as directory:
        staging = Path(directory)
        committed = []
        backups = {}
        for index, (target, value) in enumerate(targets):
            writer(staging / f"new-{index}.json", value)
            if target.exists():
                backup = staging / f"old-{index}.json"
                shutil.copy2(target, backup)
                backups[index] = backup
            target.parent.mkdir(parents=True, exist_ok=True)
        try:
            for index, (target, _) in enumerate(targets):
                os.replace(staging / f"new-{index}.json", target)
                committed.append(index)
        except BaseException:
            for index in reversed(committed):
                target = targets[index][0]
                if index in backups:
                    os.replace(backups[index], target)
                else:
                    target.unlink(missing_ok=True)
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        validate_options(args.max_seconds, args.batch_size, args.workers)
    except ValueError as exc:
        parser.error(str(exc))
    sources = [Path.home() / "marschain-node/full-power-export/scan.sqlite3",
               Path.home() / "marschain-rank-preview/state/preview.sqlite3"]
    progress = lambda value: print(json.dumps(value), flush=True)
    artifacts = collect_snapshot(sources, max_seconds=args.max_seconds, batch_size=args.batch_size,
                                 workers=args.workers, progress=progress)
    write_snapshot(args.root.resolve(), artifacts)
    progress({"phase": "complete", **artifacts["public"]["meta"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
