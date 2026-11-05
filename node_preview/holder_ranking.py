"""Small, explicit public boundary for the independently collected holder list.

The address census and full balance proof stay in private state.  Both the
minute publisher and cloud adapter can use this module without querying RPC or
waiting for the holder collector.  Old valid snapshots retain their real block
time; callers may request an age limit when checking a newly collected result.
"""
from __future__ import annotations

import json
import re
import stat
import time
from pathlib import Path

SYSTEM_ADDRESS = "0x0000000000000000000000000000000000001001"
PUBLIC_FILE = "holder-ranking-public.json"
MAX_PUBLIC_BYTES = 128 * 1024
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_BALANCE = (1 << 256) - 1
ADDRESS = re.compile(r"0x[0-9a-f]{40}\Z")
HASH = re.compile(r"0x[0-9a-f]{64}\Z")
DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,95})\Z")


def _integer(value: object, name: str, *, positive: bool = True) -> int:
    if type(value) is not int or not (1 if positive else 0) <= value <= MAX_SAFE_INTEGER:
        raise ValueError(f"Invalid holder {name}")
    return value


def _decimal(value: object, name: str) -> int:
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise ValueError(f"Invalid holder {name}")
    return int(value)


def validate_public_snapshot(value: object, *, max_age: int | None = None,
                             now: int | None = None) -> dict:
    """Validate and return a whitelist-only schema-2 public snapshot.

    Errors never include untrusted values, source paths, or proof contents.
    ``max_age=None`` deliberately allows a last-good, older snapshot.  It does
    not refresh any timestamp or imply that an older balance is current.
    """
    if max_age is not None and (type(max_age) is not int or max_age <= 0):
        raise ValueError("Invalid holder age limit")
    now = int(time.time()) if now is None else _integer(now, "validation time")
    if not isinstance(value, dict) or set(value) != {"meta", "systems", "rows"}:
        raise ValueError("Invalid holder public envelope")
    meta = value["meta"]
    if (not isinstance(meta, dict) or type(meta.get("public_schema")) is not int or meta.get("public_schema") != 2
            or meta.get("complete") is not True or meta.get("network_complete") is not False
            or meta.get("system_balances_excluded") is not True
            or meta.get("share_denominator_scope") != "indexed_non_system_addresses"):
        raise ValueError("Invalid holder public metadata")
    block = _integer(meta.get("block_number"), "block number")
    block_time = _integer(meta.get("block_timestamp"), "block timestamp")
    created_at = _integer(meta.get("created_at"), "collection timestamp")
    address_count = _integer(meta.get("address_count"), "address count")
    ranking_count = _integer(meta.get("ranking_address_count"), "ranking count")
    if (not isinstance(meta.get("block_hash"), str) or not HASH.fullmatch(meta["block_hash"])
            or address_count != ranking_count + 1 or block_time >= 100_000_000_000
            or created_at >= 100_000_000_000 or block_time > now + 30
            or created_at > now + 30 or created_at < block_time):
        raise ValueError("Invalid holder block or address boundary")
    if max_age is not None and (now - block_time > max_age or now - created_at > max_age):
        raise ValueError("Holder node snapshot is too old")
    denominator = _decimal(meta.get("share_denominator_wei"), "balance denominator")
    if denominator <= 0 or denominator > MAX_BALANCE * ranking_count:
        raise ValueError("Invalid holder balance denominator")
    systems = value["systems"]
    if (not isinstance(systems, list) or len(systems) != 1
            or not isinstance(systems[0], dict)
            or set(systems[0]) - {"address", "kind", "name"}
            or systems[0].get("address") != SYSTEM_ADDRESS
            or systems[0].get("kind") != "system"
            or systems[0].get("name", "POWER") != "POWER"):
        raise ValueError("Invalid holder system identity")
    raw_rows = value["rows"]
    if not isinstance(raw_rows, list) or len(raw_rows) != min(100, ranking_count):
        raise ValueError("Incomplete holder top 100")
    rows, seen, previous, top_total = [], {SYSTEM_ADDRESS}, None, 0
    for rank, raw in enumerate(raw_rows, 1):
        if (not isinstance(raw, dict) or set(raw) != {"rank", "address", "balance_wei", "kind"}
                or type(raw["rank"]) is not int or raw["rank"] != rank
                or not isinstance(raw["address"], str) or not ADDRESS.fullmatch(raw["address"])
                or raw["address"] in seen or not isinstance(raw["kind"], str)
                or raw["kind"] not in {"wallet", "contract", "burn"}):
            raise ValueError("Invalid holder ranking row")
        balance = _decimal(raw["balance_wei"], "native balance")
        order = (-balance, raw["address"])
        if balance > MAX_BALANCE or previous is not None and order < previous:
            raise ValueError("Invalid holder ranking order")
        previous = order
        seen.add(raw["address"])
        top_total += balance
        rows.append({"rank": rank, "address": raw["address"],
                     "balance_wei": raw["balance_wei"], "kind": raw["kind"]})
    if top_total > denominator or ranking_count <= 100 and top_total != denominator:
        raise ValueError("Holder total does not reconcile")
    # Do not copy arbitrary metadata from an input file into public latest.json.
    public_meta = {
        "public_schema": 2, "source": "local_node_native_balance",
        "block_number": block, "block_hash": meta["block_hash"], "block_timestamp": block_time,
        "created_at": created_at, "address_count": address_count,
        "ranking_address_count": ranking_count, "complete": True, "network_complete": False,
        "system_balances_excluded": True,
        "share_denominator_scope": "indexed_non_system_addresses",
        "share_denominator_wei": meta["share_denominator_wei"],
        "ranking_scope": "all_indexed_addresses_except_separately_listed_systems",
    }
    return {"meta": public_meta, "systems": [{"address": SYSTEM_ADDRESS, "kind": "system", "name": "POWER"}],
            "rows": rows}


def load_public_snapshot(root: Path, *, max_age: int | None = None,
                         now: int | None = None) -> dict | None:
    """Read a small valid last-good file; missing/bad holders never block power."""
    path = Path(root) / "state" / PUBLIC_FILE
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_PUBLIC_BYTES:
            return None
        content = path.read_bytes()
        if len(content) > MAX_PUBLIC_BYTES:
            return None
        return validate_public_snapshot(json.loads(content), max_age=max_age, now=now)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return None
