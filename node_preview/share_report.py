"""Build the small public share-report bundle from one verified daily archive.

Archive and published-data inputs are read-only. A complete public report is
atomically cached in private state so an upstream interruption can retain that
exact snapshot without inventing data or blocking the ordinary minute ranking.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time


DAY = 86400
MAX_SAFE_INTEGER = 2**53 - 1
MAX_PUBLIC_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BLOCK_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_INTEGER = re.compile(r"-?\d+\Z")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?\Z", re.IGNORECASE)

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
_REPORT_CACHE_NAME = "share-report-last-complete.json"
_OFFICIAL_FIELDS = (
    "network_current_price", "network_highest_price",
    "network_total_circulation_tokens", "explorer_total_addresses",
)


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


def _stamp(value: object) -> int:
    parsed = _integer(value)
    if parsed is None or not 0 < parsed <= MAX_SAFE_INTEGER:
        raise ValueError("Invalid report time")
    # Match the JS model's representable date range, as well as integer safety.
    datetime.fromtimestamp(parsed, timezone.utc)
    return parsed


def _nonnegative(value: object) -> int:
    parsed = _integer(value)
    if parsed is None or parsed < 0:
        raise ValueError("Invalid report amount")
    return parsed


def _number(value: object) -> float:
    if type(value) not in (str, int, float):
        raise ValueError("Invalid report number")
    if isinstance(value, str) and not _NUMBER.fullmatch(value.strip()):
        raise ValueError("Invalid report number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("Invalid report number")
    return parsed


def _keys(value: object, permitted: tuple[str, ...] | set[str]) -> dict:
    if not isinstance(value, dict) or not set(value).issubset(permitted):
        raise ValueError("Report contains non-public fields")
    return value


def _scalar_keys(value: object, permitted: tuple[str, ...]) -> dict:
    result = _keys(value, permitted)
    if _scalars(result, permitted) != result:
        raise ValueError("Report scalar fields contain nested or invalid values")
    return result


def _complete(bundle: dict, now: int) -> dict:
    """Validate the whole original snapshot, never against a new data clock.

    The cache is public-safe by construction: arbitrary metadata and addresses
    cannot be reintroduced via a cache or an existing latest.json file. Missing
    historical 7/30-day comparisons are intentionally not a completeness gate.
    """
    _keys(bundle, {"schema", "state", "retrieved_at", "live", "daily", "daily_evidence"})
    if type(bundle.get("schema")) is not int or bundle["schema"] != 1 or bundle.get("state") != "ready":
        raise ValueError("Report is not a ready public snapshot")
    retrieved = _stamp(bundle.get("retrieved_at"))
    if retrieved > now + 60:
        raise ValueError("Report was retrieved in the future")
    live = _keys(bundle.get("live"), {"meta", "rows"})
    meta = _keys(live.get("meta"), set(LIVE_FIELDS) | {"node_preview", "preview_metric_sources"})
    _scalar_keys({key: value for key, value in meta.items() if key in LIVE_FIELDS}, LIVE_FIELDS)
    node = _scalar_keys(meta.get("node_preview"), NODE_FIELDS)
    block = _nonnegative(node.get("source_block"))
    node_time = _stamp(node.get("source_block_timestamp"))
    generated = _stamp(meta.get("generated_at"))
    if (node.get("state") != "ready" or block <= 0 or not _BLOCK_HASH.fullmatch(str(node.get("source_block_hash", "")))
            or not retrieved - 300 <= node_time <= retrieved + 60
            or not node_time <= generated <= retrieved + 60):
        raise ValueError("Live report fixed block is invalid or delayed")
    sources = _keys(meta.get("preview_metric_sources"), SOURCE_KEYS)
    for source in sources.values():
        _scalar_keys(source, SOURCE_FIELDS)
    for field in ("network_total_power", "network_total_burned_tokens", "emission"):
        source = sources.get(field) or {}
        if source.get("source") != "node" or source.get("status") != "ok" or _integer(source.get("block")) != block:
            raise ValueError("Live report node provenance is invalid")
    total = _nonnegative(meta.get("network_total_power"))
    _nonnegative(meta.get("network_total_burned_tokens"))
    if (total <= 0 or meta.get("emission_model_status") != "verified"
            or _integer(meta.get("emission_reference_block")) != block
            or _stamp(meta.get("emission_reference_timestamp")) != node_time
            or _number(meta.get("power_required_per_mars_daily")) <= 0):
        raise ValueError("Report emission model is not verified at the same block")
    for field in _OFFICIAL_FIELDS:
        source = sources.get(field) or {}
        fetched = _stamp(source.get("fetched_at"))
        if (source.get("source") != "official" or source.get("status") != "ok"
                or not retrieved - 300 <= fetched <= retrieved + 60 or fetched > generated):
            raise ValueError("Report official provenance is invalid")
        if field in ("network_current_price", "network_highest_price"):
            _number(meta.get(field))
        else:
            _nonnegative(meta.get(field))
    rows = live.get("rows")
    if not isinstance(rows, list) or len(rows) != 100:
        raise ValueError("Report does not contain its complete top 100")
    powers = []
    for row in rows:
        _keys(row, {"power"})
        amount = _nonnegative(row.get("power"))
        if amount <= 0:
            raise ValueError("Report rank power is not positive")
        powers.append(amount)
    if powers != sorted(powers, reverse=True) or sum(powers) > total:
        raise ValueError("Report top 100 does not agree with its total")

    daily = _keys(bundle.get("daily"), {"meta"})
    dm = _keys(daily.get("meta"), set(DAILY_FIELDS) | {"node_preview", "preview_daily_status"})
    _scalar_keys({key: value for key, value in dm.items() if key in DAILY_FIELDS}, DAILY_FIELDS)
    evidence = _keys(bundle.get("daily_evidence"), {
        "target_timestamp", "source_block", "sha256", "daily_statistics_status"})
    cutoff = _stamp(evidence.get("target_timestamp"))
    frozen = _scalar_keys(dm.get("node_preview"), NODE_FIELDS)
    frozen_block = _nonnegative(evidence.get("source_block"))
    if (cutoff != retrieved // DAY * DAY or _integer(dm.get("statistics_window_end_timestamp")) != cutoff
            or _integer(dm.get("statistics_window_start_timestamp")) != cutoff - DAY
            or frozen.get("state") != "frozen" or frozen_block <= 0 or frozen_block > block
            or _integer(frozen.get("source_block")) != frozen_block
            or _integer(frozen.get("target_timestamp")) != cutoff
            or not _BLOCK_HASH.fullmatch(str(frozen.get("source_block_hash", "")))
            or not 0 < _stamp(frozen.get("source_block_timestamp")) < cutoff <= node_time
            or not _SHA256.fullmatch(str(evidence.get("sha256", "")))):
        raise ValueError("Report daily archive evidence is invalid")
    for status in (dm.get("preview_daily_status"), evidence.get("daily_statistics_status")):
        _scalar_keys(status, STATUS_FIELDS)
        _daily_status(status, cutoff)
    for name, stamp in (("start", cutoff - DAY), ("end", cutoff)):
        expected = datetime.fromtimestamp(stamp + 8 * 3600, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if dm.get(f"statistics_window_{name}_local") not in (expected, expected[:16]):
            raise ValueError("Report daily range text is inconsistent")
    if _integer(dm.get("statistics_window_new_power")) is None:
        raise ValueError("Report daily power change is missing")
    for field in ("statistics_window_burned_tokens", "statistics_window_active_wallet_address_count",
                  "statistics_window_new_candidate_address_count", "statistics_window_active_transactions_seen",
                  "statistics_window_transaction_volume_wei", "statistics_window_end_total_power",
                  "network_total_power", "network_total_burned_tokens"):
        _nonnegative(dm.get(field))
    if _integer(dm["statistics_window_end_total_power"]) != _integer(dm["network_total_power"]):
        raise ValueError("Report daily total powers disagree")
    return bundle


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _read_regular(path: Path, maximum: int) -> dict:
    # O_NOFOLLOW closes the lstat/open symlink race. Keep input bounded even if
    # a different publisher atomically replaces the file while it is being read.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as file:
        before = os.fstat(file.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ValueError("Invalid report cache file")
        raw = file.read(maximum + 1)
        after = os.fstat(file.fileno())
        if len(raw) > maximum or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Report cache changed while reading")
    return _json(raw)


def _cache_directory(root: Path) -> Path:
    state = root / "state"
    for path in (root, state):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Invalid report cache directory")
    return state


def _save_complete(root: Path, bundle: dict) -> None:
    state = _cache_directory(root)
    destination = state / _REPORT_CACHE_NAME
    if destination.is_symlink():
        raise ValueError("Report cache destination is a symlink")
    envelope = {"schema": 1, "sha256": hashlib.sha256(_canonical(bundle)).hexdigest(), "bundle": bundle}
    raw = _canonical(envelope)
    if len(raw) > MAX_PUBLIC_BYTES:
        raise ValueError("Report cache is too large")
    descriptor, temporary = tempfile.mkstemp(prefix=".share-report-", suffix=".tmp", dir=state)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _last_complete(root: Path, now: int) -> dict | None:
    try:
        state = _cache_directory(root)
        envelope = _read_regular(state / _REPORT_CACHE_NAME, MAX_PUBLIC_BYTES)
        _keys(envelope, {"schema", "sha256", "bundle"})
        bundle = envelope.get("bundle")
        if (type(envelope.get("schema")) is not int or envelope["schema"] != 1
                or not isinstance(bundle, dict)
                or hashlib.sha256(_canonical(bundle)).hexdigest() != envelope.get("sha256")):
            raise ValueError("Report cache checksum is invalid")
        return copy.deepcopy(_complete(bundle, now))
    except Exception:
        pass
    # Cold-start migration uses only an existing complete, public ready bundle.
    # Never derive a report by combining current metrics with an older archive.
    try:
        site = root / "site"
        data = site / "data"
        if any(path.is_symlink() or not path.is_dir() for path in (root, site, data)):
            return None
        latest = _read_regular(data / "latest.json", MAX_PUBLIC_BYTES)
        bundle = _complete(latest.get("share_report"), now)
        try:
            _save_complete(root, bundle)
        except Exception:
            pass
        return copy.deepcopy(bundle)
    except Exception:
        return None


def build_share_report(root: Path, payload: dict, *, cache_root: Path | None = None) -> dict:
    """Return a JS-safe bundle, or a non-blocking settlement-waiting marker.

    Fresh reports use this minute's exact verified cutoff. On failure only a
    previously complete whole report can be retained, with its original clocks
    and evidence. The main ranking and its time are not modified by this cache.
    """
    waiting = {"schema": 1, "state": "waiting_for_settlement"}
    now = int(time.time())
    cache = Path(cache_root if cache_root is not None else root).absolute()
    try:
        if not isinstance(payload, dict):
            raise ValueError("Live payload is missing")
        meta = payload.get("meta") or {}
        cutoff = _integer(meta.get("statistics_window_end_timestamp"))
        if cutoff is None or cutoff <= 0 or cutoff % DAY or cutoff > now // DAY * DAY:
            raise ValueError("Live cutoff is invalid")
        live = _live(payload)
        daily, evidence = _archive(Path(root).absolute(), cutoff)
        live_block = _integer(live["meta"]["node_preview"].get("source_block"))
        if live_block is None or live_block < evidence["source_block"]:
            raise ValueError("Live block predates its daily archive")
        bundle = {"schema": 1, "state": "ready", "retrieved_at": now,
                  "live": live, "daily": daily, "daily_evidence": evidence}
        public = _complete(_browser_integers(bundle), now)
        try:
            _save_complete(cache, public)
        except Exception:
            # Disk permissions/full disks must not block a fresh valid report.
            pass
        return public
    except Exception:
        # Archive/JSON/I/O problems must not interrupt the minute publisher.
        # Exception strings can contain server paths or other private context.
        retained = _last_complete(cache, now)
        if retained is None:
            return waiting
        retained["state"] = "retained"
        retained["retained_at"] = now
        return retained
