"""Build the small public share-report bundle from one verified daily archive.

This adapter is read-only. A missing or invalid settlement disables only report
generation; it must never prevent the ordinary minute ranking publication.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import stat
import time


DAY = 86400
MAX_SAFE_INTEGER = 2**53 - 1
MAX_PUBLIC_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BLOCK_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_INTEGER = re.compile(r"-?\d+\Z")

# These are the only live scalar inputs read by report_daily_live_data.js.
LIVE_FIELDS = (
    "generated_at", "network_total_power", "network_total_burned_tokens",
    "explorer_total_addresses", "network_current_price", "network_highest_price",
    "network_total_circulation_tokens", "power_required_per_mars_daily",
    "emission_model_status", "emission_reference_block", "emission_reference_timestamp",
)
DAILY_FIELDS = (
    "statistics_window_start_timestamp", "statistics_window_end_timestamp",
    "statistics_window_start_local", "statistics_window_end_local",
    "statistics_window_new_power", "statistics_window_burned_tokens",
    "statistics_window_active_wallet_address_count",
    "statistics_window_new_candidate_address_count",
    "statistics_window_active_transactions_seen", "statistics_window_transaction_volume_wei",
    "statistics_window_end_total_power", "network_total_power", "network_total_burned_tokens",
    *(
        f"period_{days}d_{field}"
        for days in (7, 30)
        for field in (
            "start_timestamp", "end_timestamp", "start_local", "end_local",
            "new_power", "burned_tokens", "new_candidate_address_count",
            "new_candidate_address_estimated", "new_candidate_address_display", "complete",
        )
    ),
)
NODE_FIELDS = (
    "state", "source_block", "source_block_hash", "source_block_timestamp",
    "generated_at", "version", "target_timestamp",
)
STATUS_FIELDS = ("state", "frozen_end_timestamp", "expected_end_timestamp")
SOURCE_FIELDS = ("source", "status", "fetched_at", "block")
SOURCE_KEYS = (
    "network_total_power", "network_total_burned_tokens", "explorer_total_addresses",
    "network_current_price", "network_highest_price", "network_total_circulation_tokens", "emission",
)

# Only compact, already-whitelisted archive data is cached. File identities are
# part of the key, so a changed manifest or public file requires a fresh hash.
_ARCHIVE_CACHE: dict[tuple, tuple[dict, dict]] = {}


def _integer(value: object) -> int | None:
    if type(value) is int:
        return value
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        return int(value)
    return None


def _scalars(value: object, fields: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in fields:
        if key not in value:
            continue
        item = value[key]
        if (item is None or type(item) in (str, int, bool)
                or type(item) is float and math.isfinite(item)):
            result[key] = item
    return result


def _browser_integers(value: object) -> object:
    """Preserve exact wei without changing the main ranking payload's types."""
    if type(value) is int and abs(value) > MAX_SAFE_INTEGER:
        return str(value)
    if isinstance(value, dict):
        return {key: _browser_integers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_browser_integers(item) for item in value]
    return value


def _identity(path: Path, maximum: int) -> tuple:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
        raise ValueError("Invalid archive artifact")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _json(raw: bytes) -> dict:
    def reject_constant(_value: str) -> None:
        raise ValueError("Non-finite JSON constant")
    value = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Archive document is not an object")
    return value


def _daily_status(value: object, cutoff: int) -> dict:
    if not isinstance(value, dict) or value.get("state") != "verified_node_window":
        raise ValueError("Daily window is not verified")
    if _integer(value.get("frozen_end_timestamp")) != cutoff:
        raise ValueError("Daily status belongs to another cutoff")
    if "expected_end_timestamp" in value and _integer(value["expected_end_timestamp"]) != cutoff:
        raise ValueError("Daily status has not reached its expected cutoff")
    return _scalars(value, STATUS_FIELDS)


def _archive(root: Path, cutoff: int) -> tuple[dict, dict]:
    date_name = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y%m%d-0800")
    directory = root / "archives" / date_name
    if any(path.is_symlink() or not path.is_dir() for path in (root, root / "archives", directory)):
        raise ValueError("Archive directory is not a regular directory")
    manifest_path, public_path = directory / "manifest.json", directory / "public.json"
    manifest_identity = _identity(manifest_path, MAX_MANIFEST_BYTES)
    public_identity = _identity(public_path, MAX_PUBLIC_BYTES)
    key = (str(root), cutoff, manifest_identity, public_identity)
    cached = _ARCHIVE_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)

    manifest = _json(manifest_path.read_bytes())
    raw = public_path.read_bytes()
    if (manifest_identity != _identity(manifest_path, MAX_MANIFEST_BYTES)
            or public_identity != _identity(public_path, MAX_PUBLIC_BYTES)):
        raise ValueError("Archive changed while being read")
    if (manifest.get("state") != "complete" or manifest.get("complete_for_known_addresses") is not True
            or _integer(manifest.get("target_timestamp")) != cutoff
            or _integer(manifest.get("daily_statistics_cutoff")) != cutoff):
        raise ValueError("Archive settlement is incomplete")
    proof_status = _daily_status(manifest.get("daily_statistics_status"), cutoff)
    digest = hashlib.sha256(raw).hexdigest()
    recorded_digest = ((manifest.get("files") or {}).get("public.json") or {}).get("sha256")
    if not isinstance(recorded_digest, str) or not _SHA256.fullmatch(recorded_digest) or digest != recorded_digest:
        raise ValueError("Archive public hash does not match")

    public = _json(raw)
    meta = public.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("Archive public metadata is missing")
    if (_integer(meta.get("statistics_window_end_timestamp")) != cutoff
            or _integer(meta.get("statistics_window_start_timestamp")) != cutoff - DAY):
        raise ValueError("Archive daily window does not match its target")
    for name, stamp in (("start", cutoff - DAY), ("end", cutoff)):
        expected = datetime.fromtimestamp(stamp + 8 * 3600, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        actual = meta.get(f"statistics_window_{name}_local")
        if actual not in (expected, expected[:16]):
            raise ValueError("Archive local window text is inconsistent")
    public_status = _daily_status(meta.get("preview_daily_status"), cutoff)
    node = meta.get("node_preview")
    block = _integer(manifest.get("source_block"))
    block_hash = manifest.get("source_block_hash")
    block_time = _integer(manifest.get("source_block_timestamp"))
    if (not isinstance(node, dict) or node.get("state") != "frozen"
            or block is None or block <= 0 or _integer(node.get("source_block")) != block
            or _integer(node.get("target_timestamp")) != cutoff
            or not isinstance(block_hash, str) or not _BLOCK_HASH.fullmatch(block_hash)
            or not isinstance(node.get("source_block_hash"), str)
            or node["source_block_hash"].lower() != block_hash.lower()
            or block_time is None or not 0 < block_time < cutoff
            or _integer(node.get("source_block_timestamp")) != block_time):
        raise ValueError("Archive fixed-block identities are inconsistent")

    for field in ("network_total_power", "network_total_burned_tokens"):
        origin = (meta.get("preview_metric_sources") or {}).get(field) or {}
        if origin.get("source") != "node" or origin.get("status") != "ok" or _integer(origin.get("block")) != block:
            raise ValueError("Archive global values are not verified at the frozen block")
    total, end_total = (_integer(meta.get(field)) for field in
                        ("network_total_power", "statistics_window_end_total_power"))
    if total is not None and end_total is not None and total != end_total:
        raise ValueError("Archive daily and fixed-block powers disagree")

    daily_meta = _scalars(meta, DAILY_FIELDS)
    daily_meta["node_preview"] = _scalars(node, NODE_FIELDS)
    daily_meta["preview_daily_status"] = public_status
    evidence = {"target_timestamp": cutoff, "source_block": block, "sha256": digest,
                "daily_statistics_status": proof_status}
    result = ({"meta": daily_meta}, evidence)
    # Bound memory across dates and remove superseded identities for this target.
    for old in list(_ARCHIVE_CACHE):
        if old[:2] == key[:2]:
            del _ARCHIVE_CACHE[old]
    if len(_ARCHIVE_CACHE) >= 8:
        _ARCHIVE_CACHE.pop(next(iter(_ARCHIVE_CACHE)))
    _ARCHIVE_CACHE[key] = copy.deepcopy(result)
    return result


def _live(payload: dict) -> dict:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("Live public metadata is missing")
    node = meta.get("node_preview")
    if not isinstance(node, dict) or node.get("state") != "ready":
        raise ValueError("Live public snapshot is not ready")
    result = _scalars(meta, LIVE_FIELDS)
    result["node_preview"] = _scalars(node, NODE_FIELDS)
    sources = meta.get("preview_metric_sources") or {}
    result["preview_metric_sources"] = {
        key: _scalars(sources[key], SOURCE_FIELDS)
        for key in SOURCE_KEYS if isinstance(sources.get(key), dict)
    }
    rows = payload.get("rows")
    if not isinstance(rows, list) or not 0 < len(rows) <= 100:
        raise ValueError("Live public rank boundary is invalid")
    projected = []
    for row in rows:
        amount = _integer(row.get("power")) if isinstance(row, dict) else None
        if amount is None or amount <= 0:
            raise ValueError("Live rank power is invalid")
        projected.append({"power": str(amount)})
    return {"meta": result, "rows": projected}


def build_share_report(root: Path, payload: dict) -> dict:
    """Return a JS-safe bundle, or a non-blocking settlement-waiting marker.

    The daily target must be the exact settled cutoff carried by this minute's
    payload. No previous-day fallback or latest-state substitution is allowed.
    Only this report branch converts integers beyond the browser's safe range.
    """
    waiting = {"schema": 1, "state": "waiting_for_settlement"}
    try:
        if not isinstance(payload, dict):
            return waiting
        meta = payload.get("meta") or {}
        cutoff = _integer(meta.get("statistics_window_end_timestamp"))
        now = int(time.time())
        if cutoff is None or cutoff <= 0 or cutoff % DAY or cutoff > now // DAY * DAY:
            return waiting
        live = _live(payload)
        daily, evidence = _archive(Path(root).absolute(), cutoff)
        live_block = _integer(live["meta"]["node_preview"].get("source_block"))
        if live_block is None or live_block < evidence["source_block"]:
            return waiting
        bundle = {"schema": 1, "state": "ready", "retrieved_at": now,
                  "live": live, "daily": daily, "daily_evidence": evidence}
        return _browser_integers(bundle)
    except Exception:
        # Archive/JSON/I/O problems must not interrupt the minute publisher.
        # Exception strings can contain server paths or other private context.
        return waiting
