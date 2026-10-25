"""Lightweight preview metrics, with immutable Beijing 08:00 statistics.

Only this module's ``preview_metric_*`` tables are written here.  Existing daily
figures stay at their published timestamp until a whole observed window and both
boundary contract states can be verified.  Minute refreshes never append a daily
history point.  No historical block or log scan is performed by this module.
"""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import http.client
import json
import math
import socket
import sqlite3
import ssl
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

PUBLIC_ORIGIN = "https://marschain-power-site-chu.oss-cn-hangzhou.aliyuncs.com"
EXPLORER_API = "https://explorer.marschain.net/api"
POWER_CONTRACT = "0x0000000000000000000000000000000000001001"
# Checked against official PowerContractUpgradeable.sol and local web3_sha3.
VIEW_POWER = "0xb2dbd84e" + "0" * 64
TOTAL_BURNED = "0x555f323a"
CONTRACT_SOURCE = "https://github.com/marschain-protocol/mars_contracts/blob/main/contracts/PowerContractUpgradeable.sol"
TREND_KEYS = (
    "network_total_power", "network_total_circulation", "network_current_price", "latest_block",
    "total_supply", "daily_emission", "total_burned", "total_wallets", "positive_power_addresses",
    "daily_active_addresses", "daily_new_addresses", "daily_new_power", "daily_burned",
    "daily_transaction_volume", "daily_transaction_count", "period_7d_new_power", "period_7d_new_addresses",
    "period_7d_burned", "period_30d_new_power", "period_30d_new_addresses", "period_30d_burned",
    "power_per_coin", "one_yi_power_output",
)

# These reviewed old site observations may supplement charts, never current
# cards or exact boundary calculations. Legacy address counts and increments
# used different coverage/cutoffs and must not enter a verified daily series.
LEGACY_SAMPLE_KEYS = frozenset((
    "network_total_circulation", "network_current_price", "total_burned", "total_wallets",
    "total_supply", "daily_emission", "power_per_coin", "one_yi_power_output",
))
LEGACY_WINDOW_KEYS = frozenset(("daily_active_addresses", "daily_transaction_volume", "daily_transaction_count"))
LEGACY_SAMPLE_NOTE = "旧站历史记录按记录日期展示，采样时间不固定；新记录按北京时间08:00日结，跨期变化需留意口径差异。"
LEGACY_MODEL_NOTE = "旧记录为当时模型的采样估算；新记录按08:00日结估算，均不代表实测产量或收益。"
LEGACY_WINDOW_NOTE = "已补入旧站完整统计窗口的历史记录，按北京时间08:00截止日期展示。"
LEGACY_WINDOW_SAMPLE_NOTE = "旧记录按当时实际区块统计区间展示；新记录按北京时间08:00日结，跨期变化需留意口径差异。"
PUBLIC_HISTORY_NOTES = frozenset((LEGACY_SAMPLE_NOTE, LEGACY_MODEL_NOTE, LEGACY_WINDOW_NOTE, LEGACY_WINDOW_SAMPLE_NOTE))


def _ipv4_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    """Prefer IPv4 for this connection only; do not patch global socket state."""
    host, port = address
    try:
        choices = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return socket.create_connection(address, timeout, source_address)
    last_error = None
    deadline = time.monotonic() + float(timeout) if isinstance(timeout, (int, float)) else None
    for family, socktype, proto, _, sockaddr in choices:
        sock = socket.socket(family, socktype, proto)
        try:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("IPv4 connection timed out")
                sock.settimeout(remaining)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    raise last_error or OSError("no usable address")


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _ipv4_connection


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_IPv4HTTPSConnection, request, context=ssl.create_default_context())


def fetch_json(url: str, timeout: float = 10, payload: Any = None) -> Any:
    headers = {"Accept": "application/json", "User-Agent": "MarsChain-node-preview/1", "Cache-Control": "no-cache"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.build_opener(_IPv4HTTPSHandler()).open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str) and value.startswith("0x"):
            return int(value, 16)
        number = Decimal(str(value))
        if number.is_finite() and number == number.to_integral_value():
            return int(number)
    except (ValueError, TypeError, InvalidOperation):
        pass
    return None


def _local(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(timestamp + 28800))


def _window(end: int) -> dict:
    start = max(0, end - 86400)
    return {
        "statistics_timezone": "Asia/Shanghai", "statistics_timezone_label": "北京时间", "statistics_day_start_hour": 8,
        "statistics_window_start_timestamp": start, "statistics_window_end_timestamp": end,
        "statistics_window_start_local": _local(start), "statistics_window_end_local": _local(end),
        "statistics_window_label": f"{_local(start)} 至 {_local(end)}", "statistics_day_label": _local(start)[:10],
        "statistics_window_start_day": start // 86400, "statistics_window_end_day": end // 86400,
        "today_local_date": _local(start)[:10], "today_utc_date": time.strftime("%Y-%m-%d", time.gmtime(start)),
        "today_chain_day": start // 86400,
    }


def _token(value: Any) -> str | None:
    number = _integer(value)
    if number is None:
        return None
    tokens = Decimal(number) / 10 ** 18
    for threshold, unit in ((10**12, "万亿"), (10**8, "亿"), (10**4, "万")):
        if tokens >= threshold:
            return f"{tokens / threshold:.3f}{unit}"
    return f"{tokens:.3f}".rstrip("0").rstrip(".")


def _amount(number: float) -> str:
    for threshold, unit in ((10**12, "万亿"), (10**8, "亿"), (10**4, "万")):
        if number >= threshold:
            return f"{number / threshold:.3f}{unit}"
    return str(int(number)) if number.is_integer() else f"{number:.3f}"


def _price(value: Any) -> str | None:
    try:
        number = Decimal(str(value))
        return f"{number:.6f}" if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _is_daily(key: str) -> bool:
    return key.startswith(("statistics_", "today_", "period_7d_", "period_30d_", "daily_total_power_history", "daily_power_history", "blackhole_", "full_scan_statistics_"))


def _daily_trends(meta: dict, history: list[dict], reviewed_legacy: list[dict] | None = None) -> dict:
    """Use one cutoff clock; legacy partial-day snapshots are not daily facts.

    A contract entry for UTC day D is the *end* of D, i.e. Beijing 08:00
    on D+1. Verified node snapshots already identify that cutoff directly.
    Reviewed legacy observations are displayed with a separate timing note;
    unknown historical fields remain gaps instead of synthetic averages.
    """
    cutoff = _integer(meta.get("statistics_window_end_timestamp"))
    points: dict[str, dict[int, dict]] = {key: {} for key in TREND_KEYS}
    sampled_keys: set[str] = set()
    legacy_notes: dict[str, str] = {}

    def number(value):
        if isinstance(value, bool):
            return None
        exact = _integer(value)
        if exact is not None:
            return exact
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            return None

    # Import only explicitly reviewed fields; merely having an old value in a
    # seed is insufficient. Preserve its original record date/time rather than
    # relabelling an 08:40 record as an exact 08:00 measurement. Old generated_at
    # is a build completion time, not an HTTP receipt time.
    for snapshot in reviewed_legacy or []:
        if not isinstance(snapshot, dict) or snapshot.get("source") != "legacy_site_capture":
            continue
        end = _integer(snapshot.get("statistics_window_end_timestamp"))
        captured = _integer(snapshot.get("generated_at"))
        values, sources = snapshot.get("values"), snapshot.get("field_sources")
        if (end is None or end % 86400 or captured is None or not isinstance(values, dict)
                or not isinstance(sources, dict) or (cutoff is not None and end >= cutoff)):
            continue
        if not end <= captured < end + 86400:
            continue
        for key, raw_value in values.items():
            provenance = sources.get(key) or {}
            if not isinstance(provenance, dict):
                continue
            kind = provenance.get("timing_status")
            value = number(raw_value)
            window = key in LEGACY_WINDOW_KEYS and kind in {"verified_legacy_window", "legacy_window_sample"}
            sample = key in LEGACY_SAMPLE_KEYS and kind in {"legacy_sample", "legacy_model_sample"}
            if value is None or value < 0 or not (window or sample):
                continue
            timestamp = end if window else captured
            # UTC day buckets correspond to the site's Beijing 08:00 day. A
            # verified new point at the same cutoff takes precedence below.
            points[key][end] = {"label": _local(timestamp)[:10], "value": value,
                                "recorded_at": captured, "timing_status": kind}
            note = (LEGACY_WINDOW_SAMPLE_NOTE if kind == "legacy_window_sample" else LEGACY_WINDOW_NOTE if window
                    else LEGACY_MODEL_NOTE if kind == "legacy_model_sample" else LEGACY_SAMPLE_NOTE)
            # One approximate legacy interval makes the whole series require
            # that warning, even if its newest old record has exact boundaries.
            if legacy_notes.get(key) != LEGACY_WINDOW_SAMPLE_NOTE:
                legacy_notes[key] = note

    power_points = points["network_total_power"]
    for row in meta.get("daily_total_power_history") or []:
        if not isinstance(row, dict):
            continue
        day, value = _integer(row.get("day")), number(row.get("value"))
        if day is None or value is None:
            continue
        timestamp = (day + 1) * 86400
        # A seed saved during D must never be mistaken for its completed value.
        completed_through = _integer(meta.get("daily_power_history_completed_through"))
        if completed_through is None or timestamp > completed_through or (cutoff is not None and timestamp > cutoff):
            continue
        power_points[timestamp] = {"label": _local(timestamp)[:10], "value": value}

    for snapshot in sorted(history, key=lambda item: _integer(item.get("statistics_window_end_timestamp")) or 0):
        timestamp = _integer(snapshot.get("statistics_window_end_timestamp"))
        source = snapshot.get("source")
        if timestamp is None or timestamp % 86400 or (cutoff is not None and timestamp > cutoff):
            continue
        if source != "verified_node_window":
            continue
        values = snapshot.get("values")
        if not isinstance(values, dict):
            continue
        field_sources = snapshot.get("field_sources") or {}
        for key, raw_value in values.items():
            value = number(raw_value)
            if value is None:
                continue
            if key not in points:
                continue
            metric_points = points[key]
            point = {"label": _local(timestamp)[:10], "value": value}
            provenance = field_sources.get(key) or {}
            if provenance.get("timing_status") == "sampled_near_cutoff":
                sampled_keys.add(key)
                point["sample"] = provenance
            metric_points[timestamp] = point

    # Derive daily growth from the same adjacent completed cutoffs as the total
    # curve. Old seed 'daily_new_power' values used partial days and are excluded.
    for timestamp, point in power_points.items():
        for days, key in ((1, "daily_new_power"), (7, "period_7d_new_power"), (30, "period_30d_new_power")):
            previous = power_points.get(timestamp - days * 86400)
            if previous is not None:
                points[key][timestamp] = {"label": point["label"], "value": point["value"] - previous["value"]}

    return {key: {"source": "官方 08:00 附近实际采样（非精确截点）" if key in sampled_keys else
                         "已完成合约日与节点核验日结" if key in ("network_total_power", "daily_new_power") else "已核验或注明采样时间的日结",
                  "values": [dated[timestamp] for timestamp in sorted(dated)[-90:]],
                  "available": bool(dated), "allow_synthetic_fallback": False,
                  **({"history_note": legacy_notes[key]} if key in legacy_notes and any(
                      point.get("timing_status", "").startswith(("legacy_", "verified_legacy_"))
                      for point in [dated[stamp] for stamp in sorted(dated)[-90:]]) else {}),
                  "missing_reason": None if dated else "历史缺少同一截止口径的可靠采样，保留缺失；不以均值补造趋势"}
            for key, dated in points.items()}


class MetricStore:
    """Persist seeds/cache and return meta compatible with the existing website.

    ``bootstrap`` returns ``payload``, normalized ``history`` and ``build_meta``.
    ``refresh(base_meta, source_block)`` returns a fresh meta dictionary, keeping
    frozen daily fields isolated from realtime values. ``source_block`` is an
    integer or a dict with ``number``/``hash``/``timestamp``.
    ``history()`` returns only imported or completely verified daily snapshots.
    """

    def __init__(self, db_path: str | Path, rpc_url: str = "http://127.0.0.1:18555", seed_dir: str | Path | None = None,
                 fetcher: Callable = fetch_json, timeout: float = 10, cache_seconds: int = 50,
                 observation_database: str | Path | None = None, boundary_sample_tolerance_seconds: int = 90):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.observation_database = Path(observation_database) if observation_database is not None else self.db_path
        if not 0 <= boundary_sample_tolerance_seconds <= 300:
            raise ValueError("Boundary sample tolerance must be between 0 and 300 seconds")
        self.boundary_sample_tolerance_seconds = boundary_sample_tolerance_seconds
        self.rpc_url, self.fetcher, self.timeout, self.cache_seconds = rpc_url, fetcher, timeout, cache_seconds
        self.seed_dir = Path(seed_dir) if seed_dir is not None else self.db_path.parent / "seed"
        with self._db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS preview_metric_state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preview_metric_cache (key TEXT PRIMARY KEY,payload TEXT,fetched_at INTEGER,checked_at INTEGER,error TEXT);
                CREATE TABLE IF NOT EXISTS preview_metric_boundaries (timestamp INTEGER PRIMARY KEY,block_number INTEGER NOT NULL,block_hash TEXT,power TEXT NOT NULL,burned TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS preview_metric_cutoff_samples (kind TEXT NOT NULL,cutoff INTEGER NOT NULL,received_at INTEGER NOT NULL,payload TEXT NOT NULL,provenance TEXT NOT NULL,PRIMARY KEY(kind,cutoff));
            """)

    def _db(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _observations(self):
        """Never acquire a writable connection to the production collector DB."""
        conn = sqlite3.connect(self.observation_database.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _get(self, key: str, default=None):
        with self._db() as conn:
            row = conn.execute("SELECT value FROM preview_metric_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _put(self, key: str, value: Any):
        with self._db() as conn:
            conn.execute("INSERT OR REPLACE INTO preview_metric_state VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    @staticmethod
    def _valid_seed(key: str, value: Any) -> bool:
        if key == "payload":
            return isinstance(value, dict) and isinstance(value.get("meta"), dict) and isinstance(value.get("rows"), list) and bool(value["rows"])
        if key == "history":
            return isinstance(value, list) or (isinstance(value, dict) and isinstance(value.get("snapshots"), list))
        return isinstance(value, dict)

    def bootstrap(self, origin: str = PUBLIC_ORIGIN) -> dict:
        specs = {"payload": "data/latest.json", "history": "data/metric-history.json", "build_meta": "build-meta.json"}
        recovered, missing = {}, {}
        for key, relative in specs.items():
            value = self._get("seed_" + key)
            if not self._valid_seed(key, value):
                for path in (self.seed_dir / relative, self.seed_dir / Path(relative).name, self.seed_dir / "site" / relative):
                    try:
                        candidate = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if self._valid_seed(key, candidate):
                        value = candidate
                        break
            if self._valid_seed(key, value):
                recovered[key] = value
            else:
                missing[key] = relative
        def restore(item):
            key, relative = item
            try:
                value = self.fetcher(origin.rstrip("/") + "/" + relative, max(20, self.timeout), None)
                if not self._valid_seed(key, value):
                    raise ValueError("invalid or empty published seed")
                return key, value, None
            except Exception as exc:
                return key, None, str(exc)
        errors = {}
        if missing:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                for key, value, error in pool.map(restore, missing.items()):
                    if error:
                        errors[key] = error
                    else:
                        recovered[key] = value
        if "payload" not in recovered:
            raise RuntimeError("Cannot bootstrap a real published ranking: " + errors.get("payload", "no valid local seed"))
        recovered.setdefault("history", [])
        recovered.setdefault("build_meta", {})
        if isinstance(recovered["history"], dict):
            recovered["history"] = recovered["history"]["snapshots"]
        recovered["history"] = [s for s in recovered["history"] if isinstance(s, dict) and isinstance(s.get("values"), dict)][-90:]
        self.seed_dir.mkdir(parents=True, exist_ok=True)
        for key, value in recovered.items():
            self._put("seed_" + key, value)
            path = self.seed_dir / Path(specs[key]).name
            if not path.exists():
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
        self._put("seed_source", {"source": origin.rstrip("/") + "/data/latest.json", "fetched_at": recovered["payload"]["meta"].get("generated_at")})
        if self._get("daily_meta") is None:
            meta = recovered["payload"]["meta"]
            self._put("daily_meta", {k: v for k, v in meta.items() if _is_daily(k)})
            self._put("daily_status", {"state": "preserved_published_snapshot", "source": origin.rstrip("/") + "/data/latest.json",
                      "frozen_end_timestamp": meta.get("statistics_window_end_timestamp"), "source_generated_at": meta.get("generated_at"),
                      "detail": "Published daily/7d/30d data retained at its original cutoff until a complete observed node window is available."})
            if self.observation_database.resolve() != self.db_path.resolve():
                # Copy only already verified aggregate metadata, never collector
                # tables or a write connection to the live database.
                try:
                    with self._observations() as source:
                        rows = source.execute("SELECT key,value FROM preview_metric_state WHERE key IN ('daily_meta','daily_status')").fetchall()
                    daily = {row["key"]: json.loads(row["value"]) for row in rows}
                    end = _integer(daily.get("daily_meta", {}).get("statistics_window_end_timestamp"))
                    if (daily.get("daily_status", {}).get("state") == "verified_node_window" and end is not None
                            and daily["daily_status"].get("frozen_end_timestamp") == end):
                        self._put("daily_meta", daily["daily_meta"])
                        self._put("daily_status", daily["daily_status"])
                except (sqlite3.Error, OSError, ValueError):
                    pass
        recovered["bootstrap_errors"] = errors
        return recovered

    def history(self) -> list[dict]:
        inherited = []
        if self.observation_database.resolve() != self.db_path.resolve():
            try:
                with self._observations() as conn:
                    row = conn.execute("SELECT value FROM preview_metric_state WHERE key='history'").fetchone()
                inherited = json.loads(row[0]) if row else []
            except (sqlite3.Error, OSError, ValueError):
                pass
        own = self._get("history", self._get("seed_history", []))
        merged = {}
        for snapshot in [*inherited, *own]:
            if not isinstance(snapshot, dict):
                continue
            stamp = _integer(snapshot.get("statistics_window_end_timestamp"))
            if stamp is not None and not (merged.get(stamp, {}).get("source") == "verified_node_window"
                                          and snapshot.get("source") != "verified_node_window"):
                merged[stamp] = snapshot
        return copy.deepcopy([merged[stamp] for stamp in sorted(merged)[-90:]])

    def _rpc(self, method: str, params: list):
        result = self.fetcher(self.rpc_url, self.timeout, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(result, dict) or result.get("error") or "result" not in result:
            raise RuntimeError(f"{method}: {result.get('error') if isinstance(result, dict) else 'invalid response'}")
        return result["result"]

    def _node_totals(self, block: int) -> dict:
        def call(data):
            raw = self._rpc("eth_call", [{"to": POWER_CONTRACT, "data": data}, hex(block)])
            if not isinstance(raw, str) or not raw.startswith("0x") or len(raw[2:]) % 64:
                raise ValueError("invalid ABI uint256 response")
            return [int(raw[i:i+64], 16) for i in range(2, len(raw), 64)]
        power = call(VIEW_POWER)
        burned = call(TOTAL_BURNED)
        if len(power) != 2 or len(burned) != 1:
            raise ValueError("unexpected POWER contract return word count")
        return {"power": power[1], "burned": burned[0]}

    def _contract_power_days(self, block: int | None = None) -> list[tuple[int, int]]:
        raw = self._rpc("eth_call", [{"to": POWER_CONTRACT, "data": "0x1c1b5cdf"}, hex(block) if block is not None else "latest"])
        if not isinstance(raw, str) or not raw.startswith("0x") or len(raw[2:]) % 64:
            raise ValueError("invalid daily power history ABI response")
        words = [int(raw[i:i+64], 16) for i in range(2, len(raw), 64)]
        if len(words) < 4:
            raise ValueError("daily power history ABI too short")
        def array(offset):
            if offset % 32:
                raise ValueError("unaligned daily power history ABI offset")
            index = offset // 32
            if index < 2 or index >= len(words) or words[index] > len(words) - index - 1:
                raise ValueError("daily power history ABI offset out of range")
            return words[index+1:index+1+words[index]]
        days, powers = array(words[0]), array(words[1])
        if len(days) != len(powers) or len(set(days)) != len(days):
            raise ValueError("daily power history length or day identity mismatch")
        return sorted(zip(days, powers))

    def _refresh_contract_history(self, meta: dict, block_info: dict, now: int):
        """One small contract view per completed day; never a block/log replay."""
        block = _integer(block_info.get("number"))
        stamp = _integer(block_info.get("timestamp"))
        if stamp is None and block is not None:
            stamp = _integer(self._rpc("eth_getBlockByNumber", [hex(block), False]).get("timestamp"))
        completed_through = min(now, stamp or 0) // 86400 * 86400
        cached = self._get("completed_contract_power_days", {})
        attempt = self._get("completed_contract_power_days_attempt", {})
        if (block is not None and completed_through > 0
                and int(cached.get("completed_through") or 0) < completed_through
                and now - int(attempt.get("checked_at") or 0) >= 300):
            try:
                days = self._contract_power_days(block)
                cached = {"completed_through": completed_through, "source_block": block,
                          "source_block_hash": block_info.get("hash"), "fetched_at": now,
                          "values": [{"day": day, "value": power} for day, power in days
                                     if (day + 1) * 86400 <= completed_through][-91:]}
                self._put("completed_contract_power_days", cached)
                self._put("completed_contract_power_days_attempt", {"checked_at": now, "status": "ok"})
            except Exception as exc:
                self._put("completed_contract_power_days_attempt", {"checked_at": now, "status": "unavailable", "error": str(exc)})
        # Old public seed histories are deliberately replaced, even on failure.
        meta["daily_total_power_history"] = cached.get("values", [])
        meta["daily_power_history_completed_through"] = cached.get("completed_through")
        meta["daily_power_history_source"] = {key: value for key, value in cached.items() if key != "values"}

    def _record_cutoff_sample(self, kind: str, values: dict, received_at: int, provenance: dict):
        cutoff = (received_at + 43200) // 86400 * 86400
        if abs(received_at - cutoff) > self.boundary_sample_tolerance_seconds:
            return
        provenance = {**provenance, "timing_status": "sampled_near_cutoff", "exact_cutoff": False,
                      "cutoff_timestamp": cutoff, "received_at": received_at,
                      "offset_seconds": received_at - cutoff,
                      "tolerance_seconds": self.boundary_sample_tolerance_seconds}
        with self._db() as conn:
            # Both the minute publisher and the independent cutoff sampler use
            # this table. Compare and replace under the same write transaction.
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT received_at,payload,provenance FROM preview_metric_cutoff_samples WHERE kind=? AND cutoff=?", (kind, cutoff)).fetchone()
            if prior:
                prior_values, prior_source = self._decode_cutoff_sample(prior)
                complete = self._valid_official_sample(values, provenance, cutoff, received_at) if kind == "official_stats" else True
                prior_complete = self._valid_official_sample(prior_values, prior_source, cutoff, prior["received_at"]) if kind == "official_stats" else True
                # A closer response without totalAddresses must never erase a
                # usable census sample. A complete recovery may replace such an
                # incomplete row even when its receipt is slightly farther away.
                if prior_complete and not complete:
                    return
                if prior_complete == complete and (abs(prior["received_at"] - cutoff), prior["received_at"]) <= (abs(received_at - cutoff), received_at):
                    return
            conn.execute("INSERT OR REPLACE INTO preview_metric_cutoff_samples VALUES (?,?,?,?,?)",
                         (kind, cutoff, received_at, json.dumps(values), json.dumps(provenance)))

    @staticmethod
    def _decode_cutoff_sample(row) -> tuple[dict, dict]:
        if row is not None:
            try:
                values, provenance = json.loads(row["payload"]), json.loads(row["provenance"])
                if isinstance(values, dict) and isinstance(provenance, dict):
                    return values, provenance
            except (ValueError, TypeError):
                pass
        return {}, {}

    def _valid_official_sample(self, values: dict, provenance: dict, cutoff: int, received_at: Any) -> bool:
        total, stamp = _integer(values.get("total_wallets")), _integer(received_at)
        return (type(cutoff) is int and cutoff >= 0 and cutoff % 86400 == 0
                and not isinstance(values.get("total_wallets"), bool) and total is not None and total >= 0
                and stamp is not None and not isinstance(received_at, bool)
                and abs(stamp - cutoff) <= self.boundary_sample_tolerance_seconds
                and provenance.get("source") == EXPLORER_API + "/stats"
                and provenance.get("received_at") == stamp and provenance.get("cutoff_timestamp") == cutoff
                and provenance.get("timing_status") == "sampled_near_cutoff"
                and provenance.get("exact_cutoff") is False)

    def _recover_official_cutoff_archive(self, cutoff: int) -> tuple[dict, dict]:
        """Recover one original HTTP sample, never a build time or old 08:10 row.

        Only the small frozen public payload is read; no address file, chain
        replay, or mutable archive is touched. Recovery receipts remain private.
        """
        if cutoff < 0 or cutoff % 86400:
            return {}, {}
        now = int(time.time())
        attempts = self._get("official_sample_archive_recovery", {})
        prior = attempts.get(str(cutoff), {}) if isinstance(attempts, dict) else {}
        if not isinstance(prior, dict):
            prior = {}
        if prior.get("status") != "recovered" and now - (_integer(prior.get("checked_at")) or 0) < 300:
            return {}, {}
        if not isinstance(attempts, dict):
            attempts = {}
        name = time.strftime("%Y%m%d-0800", time.gmtime(cutoff))
        archives = self.observation_database.parent.parent / "archives"
        archive = archives / name
        manifest_path, public_path = archive / "manifest.json", archive / "public.json"
        receipt = {"checked_at": now, "cutoff_timestamp": cutoff, "archive": name, "status": "unavailable"}
        values, provenance = {}, {}
        try:
            if (archives.is_symlink() or archive.is_symlink() or not archive.is_dir()
                    or manifest_path.is_symlink() or public_path.is_symlink()):
                raise ValueError("archive path is missing or uses a symlink")
            if not 0 < manifest_path.stat().st_size <= 128 * 1024:
                raise ValueError("archive manifest size is invalid")
            manifest_raw = manifest_path.read_bytes()
            manifest = json.loads(manifest_raw)
            if (not isinstance(manifest, dict) or manifest.get("schema") != 1
                    or manifest.get("state") != "complete" or type(manifest.get("target_timestamp")) is not int
                    or manifest["target_timestamp"] != cutoff or manifest.get("complete_for_known_addresses") is not True):
                raise ValueError("archive is not a completed freeze for this cutoff")
            information = (manifest.get("files") or {}).get("public.json")
            if (not isinstance(information, dict) or type(information.get("bytes")) is not int
                    or not 0 < information["bytes"] <= 2 * 1024 * 1024
                    or public_path.stat().st_size != information["bytes"]):
                raise ValueError("frozen public payload size does not match manifest")
            public_raw = public_path.read_bytes()
            digest = hashlib.sha256(public_raw).hexdigest()
            if len(public_raw) != information["bytes"] or digest != information.get("sha256"):
                raise ValueError("frozen public payload checksum does not match manifest")
            public = json.loads(public_raw)
            meta = public.get("meta") if isinstance(public, dict) else None
            if not isinstance(meta, dict):
                raise ValueError("frozen public payload has no metadata")
            source = (meta.get("preview_metric_sources") or {}).get("explorer_total_addresses")
            total = _integer(meta.get("explorer_total_addresses"))
            stamp = _integer(source.get("fetched_at")) if isinstance(source, dict) else None
            # Public freezes may contain the frontend's reviewed "official"
            # alias. It denotes this same stats endpoint for this exact field.
            if (not isinstance(source, dict) or source.get("source") not in {EXPLORER_API + "/stats", "official"}
                    or source.get("status") != "ok" or stamp is None or isinstance(source.get("fetched_at"), bool)
                    or abs(stamp - cutoff) > self.boundary_sample_tolerance_seconds
                    or total is None or total < 0 or isinstance(meta.get("explorer_total_addresses"), bool)):
                raise ValueError("archive lacks a valid official totalAddresses HTTP receipt near cutoff")
            values = {"total_wallets": total}
            provenance = {"source": EXPLORER_API + "/stats", "timing_status": "sampled_near_cutoff", "exact_cutoff": False,
                          "cutoff_timestamp": cutoff, "received_at": stamp, "offset_seconds": stamp - cutoff,
                          "tolerance_seconds": self.boundary_sample_tolerance_seconds,
                          "basis": "original official HTTP receipt recovered from checksum-verified private daily freeze; upstream may cache data",
                          "recovery_source": "verified_private_freeze_archive", "archive": name,
                          "public_sha256": digest, "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest()}
            self._record_cutoff_sample("official_stats", values, stamp, provenance)
            receipt.update(status="recovered", total_wallets=total, received_at=stamp,
                           public_sha256=digest, manifest_sha256=provenance["manifest_sha256"])
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            receipt["reason"] = str(exc)
        attempts[str(cutoff)] = receipt
        # This report describes at most the retained 90 daily cutoffs, not an
        # ever-growing duplicate of the immutable archive inventory.
        attempts = {key: attempts[key] for key in sorted((key for key in attempts if str(key).isdigit()), key=int)[-90:]}
        self._put("official_sample_archive_recovery", attempts)
        return values, provenance

    def _cutoff_sample(self, kind: str, cutoff: int) -> tuple[dict, dict]:
        with self._db() as conn:
            row = conn.execute("SELECT received_at,payload,provenance FROM preview_metric_cutoff_samples WHERE kind=? AND cutoff=?", (kind, cutoff)).fetchone()
        values, provenance = self._decode_cutoff_sample(row)
        if kind != "official_stats":
            return values, provenance
        if row is not None and self._valid_official_sample(values, provenance, cutoff, row["received_at"]):
            return values, provenance
        if self.observation_database.resolve() != self.db_path.resolve():
            try:
                with self._observations() as conn:
                    inherited = conn.execute("SELECT received_at,payload,provenance FROM preview_metric_cutoff_samples WHERE kind=? AND cutoff=?", (kind, cutoff)).fetchone()
                inherited_values, inherited_source = self._decode_cutoff_sample(inherited)
                if inherited is not None and self._valid_official_sample(inherited_values, inherited_source, cutoff, inherited["received_at"]):
                    self._record_cutoff_sample(kind, inherited_values, inherited["received_at"], inherited_source)
                    return inherited_values, inherited_source
            except (sqlite3.Error, OSError, ValueError):
                pass
        recovered = self._recover_official_cutoff_archive(cutoff)
        if recovered[0]:
            # A concurrent direct sampler may have won with an even closer
            # complete receipt. Return the row that actually won, not our input.
            with self._db() as conn:
                row = conn.execute("SELECT received_at,payload,provenance FROM preview_metric_cutoff_samples WHERE kind=? AND cutoff=?", (kind, cutoff)).fetchone()
            values, provenance = self._decode_cutoff_sample(row)
            if row is not None and self._valid_official_sample(values, provenance, cutoff, row["received_at"]):
                return values, provenance
        return {}, {}

    def _sample_official_stats(self, stats: dict, source: dict):
        if source.get("status") != "ok" or not source.get("fetched_at"):
            return
        values = {"total_wallets": _integer(stats.get("totalAddresses")),
                  "network_total_circulation": _integer(stats.get("totalCirculation"))}
        for key, field in (("network_current_price", "currentPrice"), ("network_highest_price", "highestPrice"), ("network_lowest_price", "lowestPrice")):
            value = _price(stats.get(field))
            values[key] = float(value) if value is not None else None
        self._record_cutoff_sample("official_stats", values, int(source["fetched_at"]),
                                   {"source": EXPLORER_API + "/stats",
                                    "basis": "HTTP response receipt near cutoff; upstream data may have its own cache delay"})

    def _sample_counts(self, counts: dict | None, block_info: dict, now: int):
        if not counts:
            return
        values = {"positive_power_addresses": _integer(counts.get("positive_power_count")),
                  "known_addresses": _integer(counts.get("candidate_count"))}
        if values["positive_power_addresses"] is None:
            return
        cutoff = (now + 43200) // 86400 * 86400
        stamp = _integer(block_info.get("timestamp"))
        if stamp is None or abs(stamp - cutoff) > self.boundary_sample_tolerance_seconds:
            return
        self._record_cutoff_sample("known_address_counts", values, now,
                                  {"source": "collector_snapshot_known_addresses", "source_block": block_info.get("number"),
                                   "source_block_timestamp": block_info.get("timestamp"),
                                   "scope": "known addresses only; not a complete chain census"})

    def _sampled_address_growth(self, start: int, end: int) -> tuple[int | None, dict]:
        before, before_source = self._cutoff_sample("official_stats", start)
        after, after_source = self._cutoff_sample("official_stats", end)
        left, right = _integer(before.get("total_wallets")), _integer(after.get("total_wallets"))
        if left is None or right is None or right < left:
            return None, {"status": "unavailable", "exact_cutoff": False,
                          "reason": "missing two reliable official near-08:00 samples; historical gaps are not inferred"}
        return right - left, {"status": "sampled", "timing_status": "sampled_near_cutoff", "exact_cutoff": False,
                              "source": "official_totalAddresses_sample_difference", "start": before_source, "end": after_source}

    def _cutoff_address_counts(self, cutoff: int) -> tuple[dict, dict]:
        """Reuse an existing private freeze manifest; do not query every wallet."""
        name = time.strftime("%Y%m%d-0800", time.gmtime(cutoff))
        path = self.observation_database.parent.parent / "archives" / name / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            known, positive = _integer(manifest.get("known_count")), _integer(manifest.get("positive_power_count"))
            if (manifest.get("state") == "complete" and manifest.get("target_timestamp") == cutoff
                    and manifest.get("complete_for_known_addresses") is True
                    and known is not None and positive is not None and 0 <= positive <= known):
                return {"positive_power_addresses": positive, "known_addresses": known}, {
                    "source": "verified_private_freeze_manifest", "cutoff_timestamp": cutoff,
                    "source_block": manifest.get("source_block"), "source_block_hash": manifest.get("source_block_hash"),
                    "exact_cutoff": True, "scope": "all known addresses; not a complete chain address census"}
        except (OSError, ValueError, TypeError):
            pass
        return self._cutoff_sample("known_address_counts", cutoff)

    def _enrich_daily_history(self, meta: dict, now: int):
        """Persist each available cutoff field with its own real provenance."""
        daily_status = meta.get("preview_daily_status") or {}
        end = _integer(meta.get("statistics_window_end_timestamp"))
        if daily_status.get("state") != "verified_node_window" or end is None:
            return
        official, official_source = self._cutoff_sample("official_stats", end)
        counts, count_source = self._cutoff_address_counts(end)
        power = _integer(meta.get("statistics_window_end_total_power"))
        endpoints = daily_status.get("field_sources", {}).get("power", {})
        burned = _integer((endpoints.get("end") or {}).get("burned"))
        daily_emission = None
        genesis = _integer(meta.get("emission_genesis_timestamp"))
        if genesis is not None:
            cycle = max(0, (end - genesis) // 86400) // 448
            daily_emission = 100_000_000_000 * math.pow(.5, cycle) / 448
        required = power / (daily_emission * .75) if power and daily_emission else None
        values = {key: None for key in TREND_KEYS}
        values.update(network_total_power=power, total_burned=burned,
                      latest_block=daily_status.get("source_block"),
                      daily_active_addresses=meta.get("statistics_window_active_wallet_address_count"),
                      daily_new_power=meta.get("statistics_window_new_power"), daily_burned=meta.get("statistics_window_burned_tokens"),
                      daily_transaction_volume=meta.get("statistics_window_transaction_volume_wei"),
                      daily_transaction_count=meta.get("statistics_window_active_transactions_seen"),
                      total_supply=meta.get("emission_total_supply_cap_tokens"), daily_emission=daily_emission,
                      power_per_coin=required, one_yi_power_output=100_000_000 / required if required else None)
        sources = {key: {"source": "verified_node_window", "cutoff_timestamp": end} for key, value in values.items() if value is not None}
        for key in ("daily_emission", "power_per_coin", "one_yi_power_output", "total_supply"):
            if values.get(key) is not None:
                sources[key] = {"source": "existing_site_economic_model_at_verified_cutoff", "estimated": True,
                                "cutoff_timestamp": end, "basis": "448-day model; not measured emission/reward"}
        for key in ("total_wallets", "network_total_circulation", "network_current_price"):
            values[key] = official.get(key)
            if values[key] is not None:
                sources[key] = official_source
        values["positive_power_addresses"] = counts.get("positive_power_addresses")
        if values["positive_power_addresses"] is not None:
            sources["positive_power_addresses"] = count_source
        growth, growth_source = self._sampled_address_growth(end - 86400, end)
        values["daily_new_addresses"] = growth
        if growth is not None:
            sources["daily_new_addresses"] = growth_source
        for days in (7, 30):
            prefix = f"period_{days}d"
            if _integer(meta.get(f"{prefix}_end_timestamp")) == end:
                for suffix, field in (("new_power", "new_power"), ("new_addresses", "new_candidate_address_count"), ("burned", "burned_tokens")):
                    key = f"{prefix}_{suffix}"
                    values[key] = meta.get(f"{prefix}_{field}")
                    if values[key] is not None:
                        sources[key] = (meta.get(f"{prefix}_new_candidate_address_sample") or {}) if suffix == "new_addresses" else {
                            "source": "verified_node_cutoff_difference", "start_timestamp": meta.get(f"{prefix}_start_timestamp"),
                            "end_timestamp": end}
        existing = next((item for item in self.history() if item.get("statistics_window_end_timestamp") == end), {})
        created = int(existing.get("generated_at") or now)
        snapshot = {"generated_at": created, "generated_at_local": _local(created), "statistics_window_end_timestamp": end,
                    "statistics_window_end_local": _local(end), "statistics_window_label": meta.get("statistics_window_label"),
                    "source": "verified_node_window", "source_block": daily_status.get("source_block"), "schema": 2,
                    "values": values, "field_sources": sources,
                    "missing_fields": [key for key, value in values.items() if value is None]}
        history = [item for item in self.history() if item.get("statistics_window_end_timestamp") != end]
        history.append(snapshot)
        history.sort(key=lambda item: item.get("statistics_window_end_timestamp") or 0)
        # The current day's recovery can also repair an already verified prior
        # cutoff (e.g. yesterday's total). Only address fields are enriched; an
        # archive never invents a node window or rewrites unrelated daily facts.
        for item in history[-90:]:
            stamp = _integer(item.get("statistics_window_end_timestamp"))
            if item.get("source") != "verified_node_window" or stamp is None or stamp > end:
                continue
            official_values, official_provenance = self._cutoff_sample("official_stats", stamp)
            total = official_values.get("total_wallets")
            if total is None:
                continue
            item_values = item.get("values")
            if not isinstance(item_values, dict):
                continue
            item_sources = item.get("field_sources")
            if not isinstance(item_sources, dict):
                item_sources = item["field_sources"] = {}
            item_values["total_wallets"], item_sources["total_wallets"] = total, official_provenance
            recovered_growth, recovered_source = self._sampled_address_growth(stamp - 86400, stamp)
            if recovered_growth is not None:
                item_values["daily_new_addresses"] = recovered_growth
                item_sources["daily_new_addresses"] = recovered_source
            if isinstance(item.get("missing_fields"), list):
                item["missing_fields"] = [key for key in item["missing_fields"] if item_values.get(key) is None]
        if self._get("history") != history[-90:]:
            self._put("history", history[-90:])

    def _cached_external(self, path: str, now: int) -> tuple[dict, dict]:
        url = EXPLORER_API + path
        with self._db() as conn:
            row = conn.execute("SELECT * FROM preview_metric_cache WHERE key=?", (url,)).fetchone()
        cached = dict(row) if row else {}
        payload = json.loads(cached["payload"]) if cached.get("payload") else {}
        checked = _integer(cached.get("checked_at")) or 0
        if now - checked >= self.cache_seconds:
            try:
                began = time.monotonic()
                value = self.fetcher(url, self.timeout, None)
                expected = ("totalAddresses", "totalCirculation", "currentPrice") if path == "/stats" else ("totalPower", "totalBurnedTokens")
                if not isinstance(value, dict) or not any(key in value for key in expected):
                    raise ValueError("unexpected official stats response")
                if path == "/stats":
                    addresses, circulation = _integer(value.get("totalAddresses")), _integer(value.get("totalCirculation"))
                    current_price = _price(value.get("currentPrice"))
                    if (addresses is None or addresses < 0 or circulation is None or circulation < 0
                            or current_price is None or Decimal(str(value["currentPrice"])) < 0):
                        raise ValueError("incomplete official stats response; preserving previous payload and its original receipt time")
                payload = value
                cached = {"fetched_at": now + int(time.monotonic() - began), "checked_at": now, "error": None}
            except Exception as exc:
                cached.update(checked_at=now, error=str(exc))
            with self._db() as conn:
                conn.execute("INSERT OR REPLACE INTO preview_metric_cache VALUES (?,?,?,?,?)",
                             (url, json.dumps(payload), cached.get("fetched_at"), cached.get("checked_at"), cached.get("error")))
        status = {"source": url, "fetched_at": cached.get("fetched_at"), "checked_at": cached.get("checked_at"),
                  "status": "stale_cache" if cached.get("error") and payload else "unavailable" if not payload else "ok",
                  "error": cached.get("error"), "age_seconds": max(0, now - int(cached["fetched_at"])) if cached.get("fetched_at") else None}
        return payload, status

    def refresh(self, base_meta: dict, source_block: int | dict, now: int | None = None,
                observed_counts: dict | None = None) -> dict:
        now = int(time.time()) if now is None else int(now)
        block_info = source_block if isinstance(source_block, dict) else {"number": source_block}
        block = _integer(block_info.get("number"))
        if block is None or block < 0:
            raise ValueError("source_block must identify a nonnegative confirmed block")
        meta = copy.deepcopy(base_meta)
        if self._get("daily_meta") is None:
            self._put("daily_meta", {k: v for k, v in meta.items() if _is_daily(k)})
            self._put("daily_status", {"state": "preserved_published_snapshot", "frozen_end_timestamp": meta.get("statistics_window_end_timestamp"),
                      "source_generated_at": meta.get("generated_at"), "source": "provided_base_meta"})
        sources = copy.deepcopy(meta.get("preview_metric_sources", {}))
        seed_source = self._get("seed_source", {"source": "provided_base_meta", "fetched_at": base_meta.get("generated_at")})
        def preserved(field, failure):
            prior = sources.get(field) or seed_source
            return {**prior, "status": "preserved_seed", "checked_at": now, "error": failure}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            stats_task = pool.submit(self._cached_external, "/stats", now)
            power_task = pool.submit(self._cached_external, "/power/stats", now)
            totals_task = pool.submit(self._node_totals, block)
            stats, stats_source = stats_task.result()
            official_power, power_source = power_task.result()
            try:
                totals = totals_task.result()
                node_source = {"source": "local_rpc_power_contract", "contract": POWER_CONTRACT, "contract_source": CONTRACT_SOURCE,
                               "block": block, "block_hash": block_info.get("hash"), "checked_at": now, "fetched_at": now, "status": "ok"}
                self._put("last_node_totals", {"totals": totals, "source": node_source})
            except Exception as exc:
                prior = self._get("last_node_totals", {})
                totals = prior.get("totals", {})
                node_source = {**prior.get("source", {}), "checked_at": now, "status": "stale_cache" if totals else "unavailable", "error": str(exc)}
        for output, input_key in (("network_total_power", "power"), ("network_total_burned_tokens", "burned")):
            value = _integer(totals.get(input_key))
            if value is not None:
                meta[output], sources[output] = value, dict(node_source)
            else:
                official_key = "totalPower" if input_key == "power" else "totalBurnedTokens"
                external_value = _integer(official_power.get(official_key))
                if external_value is not None:
                    meta[output], sources[output] = external_value, {**power_source, "fallback_reason": node_source.get("error")}
                else:
                    sources[output] = preserved(output, node_source.get("error"))
        meta["network_total_burned_display"] = _token(meta.get("network_total_burned_tokens"))
        for output, input_key in (("explorer_total_addresses", "totalAddresses"), ("network_total_circulation_tokens", "totalCirculation")):
            value = _integer(stats.get(input_key))
            if value is not None:
                meta[output], sources[output] = value, dict(stats_source)
            else:
                sources[output] = preserved(output, stats_source.get("error") or "official field unavailable")
        meta["network_total_circulation_display"] = _token(meta.get("network_total_circulation_tokens"))
        for tag, key in (("current", "currentPrice"), ("highest", "highestPrice"), ("lowest", "lowestPrice")):
            output = f"network_{tag}_price"
            if _price(stats.get(key)) is not None:
                meta[output], meta[output + "_display"] = stats[key], _price(stats[key])
                sources[output] = dict(stats_source)
            else:
                sources[output] = preserved(output, stats_source.get("error") or "official field unavailable")
        meta.update(generated_at=now, generated_at_local=_local(now), rpc_latest_block=block, latest_block=block,
                    rpc_url=self.rpc_url, preview_metrics_checked_at=now)
        sources["latest_block"] = {"source": "collector_checkpoint", "block": block, "checked_at": now, "status": "ok"}
        self._refresh_emission(meta, now, sources)
        self._sample_official_stats(stats, stats_source)
        self._sample_counts(observed_counts, block_info, now)
        self._capture_boundary(now // 86400 * 86400, block, now)
        daily_status = self._advance_daily(meta, now, block)
        meta.update(self._get("daily_meta", {}))
        meta["preview_daily_status"] = daily_status
        meta["preview_metric_sources"] = sources
        meta["preview_official_stats_status"] = {"stats": stats_source, "power_stats": power_source}
        self._refresh_contract_history(meta, block_info, now)
        self._apply_sampled_growth(meta)
        self._enrich_daily_history(meta, now)
        legacy = self._get("reviewed_legacy_history", {})
        meta["metric_trends"] = _daily_trends(meta, self.history(), legacy.get("snapshots", []) if isinstance(legacy, dict) else [])
        return meta

    def _apply_sampled_growth(self, meta: dict):
        end = _integer(meta.get("statistics_window_end_timestamp"))
        if end is None:
            return
        value, provenance = self._sampled_address_growth(end - 86400, end)
        meta.update(statistics_window_new_candidate_address_count=value, today_new_wallet_count=value,
                    statistics_window_new_candidate_address_display=f"{value:,}个（近截点采样）" if value is not None else "缺少08:00地址采样",
                    statistics_window_new_candidate_address_basis="official totalAddresses difference between near-08:00 response samples; not exact boundary state",
                    today_new_wallet_basis="official near-08:00 sample difference; unavailable historical samples remain missing",
                    statistics_window_new_candidate_address_sample=provenance)
        status = meta.setdefault("preview_daily_status", {})
        status.setdefault("field_sources", {})["new_addresses"] = provenance
        if status.get("state") == "verified_node_window":
            status["detail"] = ("Daily tx/activity/power/burn verified. Address growth uses two official near-cutoff HTTP samples; not exact boundary state."
                                if value is not None else
                                "Daily tx/activity/power/burn verified. Address growth unavailable without two reliable official near-cutoff HTTP samples.")
        # Keep the private persisted status consistent with the displayed value;
        # previously it continued claiming that growth was unavailable forever.
        if status.get("frozen_end_timestamp") == end and self._get("daily_status") != status:
            self._put("daily_status", status)
        for days in (7, 30):
            prefix = f"period_{days}d"
            if _integer(meta.get(f"{prefix}_end_timestamp")) == end:
                value, provenance = self._sampled_address_growth(end - days * 86400, end)
                meta.update({f"{prefix}_new_candidate_address_count": value,
                             f"{prefix}_new_candidate_address_display": f"{value:,}个（近截点采样）" if value is not None else "缺少08:00地址采样",
                             f"{prefix}_new_candidate_address_basis": "official near-08:00 sample difference; not exact boundary state",
                             f"{prefix}_new_candidate_address_sample": provenance})

    def _refresh_emission(self, meta: dict, now: int, sources: dict):
        genesis = _integer(meta.get("emission_genesis_timestamp"))
        if genesis is None:
            return
        elapsed = max(0, (now - genesis) // 86400)
        cycle = elapsed // 448 + 1
        cycle_total = 100_000_000_000 * math.pow(0.5, cycle - 1)
        daily = cycle_total / 448
        required = int(meta.get("network_total_power") or 0) / (daily * .75) if daily else None
        meta.update(emission_reference_timestamp=now, emission_current_cycle=cycle, emission_cycle_elapsed_days=elapsed % 448,
                    emission_cycle_output_tokens=cycle_total, emission_daily_total_tokens=daily,
                    emission_daily_total_display=f"{daily / 10**8:.3f}亿/日", emission_daily_miner_tokens=daily * .75,
                    emission_daily_miner_display=f"{daily * .75 / 10**8:.3f}亿/日", emission_daily_node_tokens=daily * .25,
                    emission_daily_node_display=f"{daily * .25 / 10**8:.3f}亿/日", power_required_per_mars_daily=required,
                    power_required_per_mars_daily_display=_amount(required) if required is not None else None)
        sources["emission"] = {"source": "existing_site_emission_formula", "genesis_timestamp": genesis, "checked_at": now,
                               "basis": "448-day halving, 75% miner and 25% node allocation; same formula as original site"}

    def _capture_boundary(self, cutoff: int, source_block: int, now: int) -> dict | None:
        """Save cutoff state while it is still recent, even before a day is complete."""
        with self._db() as conn:
            cached = conn.execute("SELECT * FROM preview_metric_boundaries WHERE timestamp=?", (cutoff,)).fetchone()
        if cached is None and self.observation_database.resolve() != self.db_path.resolve():
            try:
                with self._observations() as source:
                    cached = source.execute("SELECT * FROM preview_metric_boundaries WHERE timestamp=?", (cutoff,)).fetchone()
            except sqlite3.Error:
                pass
            if cached is not None:
                with self._db() as conn:
                    conn.execute("INSERT OR IGNORE INTO preview_metric_boundaries VALUES (?,?,?,?,?,?)", tuple(cached))
        if cached:
            try:
                with self._observations() as conn:
                    observed = conn.execute("SELECT block_hash FROM preview_blocks WHERE number=?", (cached["block_number"],)).fetchone()
            except sqlite3.Error:
                observed = None
            if observed and observed["block_hash"] != cached["block_hash"]:
                with self._db() as conn:
                    conn.execute("DELETE FROM preview_metric_boundaries WHERE timestamp=?", (cutoff,))
            elif cached["block_number"] <= source_block:
                return {"timestamp": cutoff, "number": cached["block_number"], "hash": cached["block_hash"],
                        "power": int(cached["power"]), "burned": int(cached["burned"]), "source": "verified_local_rpc_cutoff"}
            else:
                return None
        try:
            with self._observations() as conn:
                marker = conn.execute("SELECT * FROM preview_blocks WHERE timestamp>=? AND number<=? ORDER BY number LIMIT 1",
                                      (cutoff, source_block)).fetchone()
                block = conn.execute("SELECT * FROM preview_blocks WHERE number=?", (marker["number"] - 1,)).fetchone() if marker else None
        except sqlite3.Error:
            return None
        if not block or block["timestamp"] >= cutoff or marker["parent_hash"] != block["block_hash"]:
            return None
        prior = self._get(f"boundary_attempt:{cutoff}", {})
        if now - int(prior.get("checked_at") or 0) < 300:
            return None
        try:
            totals = self._node_totals(block["number"])
        except Exception as exc:
            self._put(f"boundary_attempt:{cutoff}", {"checked_at": now, "error": str(exc), "block": block["number"]})
            return None
        with self._db() as conn:
            conn.execute("INSERT OR IGNORE INTO preview_metric_boundaries VALUES (?,?,?,?,?,?)",
                         (cutoff, block["number"], block["block_hash"], str(totals["power"]), str(totals["burned"]), now))
        return {"timestamp": cutoff, "number": block["number"], "hash": block["block_hash"], **totals, "source": "verified_local_rpc_cutoff"}

    def _historical_power_at(self, cutoff: int) -> int:
        """Completed UTC day entries are final; a current day entry is mutable.

        The contract records final power under the day on which it changed.
        Therefore a cutoff at midnight uses the most recent entry strictly
        *before* cutoff_day, never the mutable cutoff_day value.
        """
        cached = self._get(f"historical_power:{cutoff}")
        if cached is not None:
            return int(cached)
        completed = [(day, power) for day, power in self._contract_power_days() if day < cutoff // 86400]
        if not completed:
            raise RuntimeError("no completed contract power history before cutoff")
        result = max(completed)[1]
        self._put(f"historical_power:{cutoff}", str(result))
        return result

    def _advance_daily(self, meta: dict, now: int, source_block: int) -> dict:
        end = now // 86400 * 86400  # UTC midnight == Beijing 08:00.
        existing = self._get("daily_meta", {})
        prior_end = _integer(existing.get("statistics_window_end_timestamp")) or 0
        status = dict(self._get("daily_status", {}))
        status.update(checked_at=now, expected_end_timestamp=end)
        if prior_end >= end:
            return status
        try:
            result = self._complete_window(end, source_block)
        except Exception as exc:
            status.update(state="preserved_published_snapshot" if str(status.get("source", "")).startswith("http") else "preserved_previous_snapshot", detail=str(exc))
            self._put("daily_status", status)
            return status
        # A complete new window can be committed atomically as one daily meta.
        daily, boundaries = result
        with self._db() as conn:
            for boundary in boundaries:
                if boundary.get("burned") is not None:
                    conn.execute("INSERT OR IGNORE INTO preview_metric_boundaries VALUES (?,?,?,?,?,?)",
                                 (boundary["timestamp"], boundary["number"], boundary["hash"], str(boundary["power"]), str(boundary["burned"]), now))
        for days in (7, 30):
            prefix = f"period_{days}d"
            start = end - days * 86400
            with self._db() as conn:
                point = conn.execute("SELECT * FROM preview_metric_boundaries WHERE timestamp=?", (start,)).fetchone()
            if point:
                daily.update({f"{prefix}_start_timestamp": start, f"{prefix}_end_timestamp": end, f"{prefix}_start_local": _local(start),
                              f"{prefix}_end_local": _local(end), f"{prefix}_label": f"{_local(start)} 至 {_local(end)}",
                              f"{prefix}_start_day": start // 86400, f"{prefix}_end_day": end // 86400,
                              f"{prefix}_start_total_power": int(point["power"]), f"{prefix}_end_total_power": boundaries[1]["power"],
                              f"{prefix}_new_power": boundaries[1]["power"] - int(point["power"]),
                              f"{prefix}_new_power_basis": "exact local RPC state at confirmed block immediately before each Beijing 08:00 cutoff",
                              f"{prefix}_burned_tokens": boundaries[1]["burned"] - int(point["burned"]),
                              f"{prefix}_burned_display": _token(boundaries[1]["burned"] - int(point["burned"])),
                              f"{prefix}_burned_basis": "exact totalBurnedTokens difference between verified cutoff blocks",
                              f"{prefix}_new_candidate_address_count": None,
                              f"{prefix}_new_candidate_address_display": "待日结地址快照",
                              f"{prefix}_new_candidate_address_basis": "exact official totalAddresses snapshots unavailable; not inferred from discovered addresses"})
            else:
                daily.update({k: v for k, v in existing.items() if k.startswith(prefix + "_")})
        status = {"state": "verified_node_window", "source": "local_rpc_and_contiguous_observed_blocks", "checked_at": now,
                  "frozen_end_timestamp": end, "expected_end_timestamp": end, "source_block": boundaries[1]["number"],
                  "detail": "Daily tx/activity/power/burn verified. Address growth unavailable without exact official boundary snapshots.",
                  "field_sources": daily.pop("_field_sources", {}),
                  "periods": {str(days): {"end_timestamp": daily.get(f"period_{days}d_end_timestamp"),
                             "state": "verified_node_boundaries" if daily.get(f"period_{days}d_end_timestamp") == end else "preserved_previous_snapshot"} for days in (7, 30)}}
        self._put("daily_meta", daily)
        self._put("daily_status", status)
        # Exact integers are preserved; do not use float normalizers for wei.
        snapshot = {"generated_at": now, "generated_at_local": _local(now), "statistics_window_end_timestamp": end,
                    "statistics_window_end_local": _local(end), "statistics_window_label": daily["statistics_window_label"],
                    "source": "verified_node_window", "source_block": boundaries[1]["number"],
                    "values": {"network_total_power": boundaries[1]["power"], "total_burned": boundaries[1]["burned"],
                               "latest_block": boundaries[1]["number"], "daily_active_addresses": daily["statistics_window_active_wallet_address_count"],
                               "daily_new_power": daily["statistics_window_new_power"], "daily_burned": daily["statistics_window_burned_tokens"],
                               "daily_transaction_volume": daily["statistics_window_transaction_volume_wei"],
                               "daily_transaction_count": daily["statistics_window_active_transactions_seen"]}}
        snapshot["values"] = {k: v for k, v in snapshot["values"].items() if v is not None}
        history = [s for s in self.history() if s.get("statistics_window_end_timestamp") != end]
        history.append(snapshot)
        history.sort(key=lambda s: s.get("statistics_window_end_timestamp") or 0)
        self._put("history", history[-90:])
        return status

    def _complete_window(self, end: int, source_block: int) -> tuple[dict, list[dict]]:
        start = end - 86400
        with self._observations() as conn:
            anchor = conn.execute("SELECT * FROM preview_blocks WHERE timestamp<? AND number<=? ORDER BY number DESC LIMIT 1", (start, source_block)).fetchone()
            marker = conn.execute("SELECT * FROM preview_blocks WHERE timestamp>=? AND number<=? ORDER BY number LIMIT 1", (end, source_block)).fetchone()
            if not anchor or not marker:
                raise RuntimeError("Waiting for a full observed 08:00 window including start anchor and confirmed end marker; published daily figures retained.")
            blocks = conn.execute("SELECT * FROM preview_blocks WHERE number BETWEEN ? AND ? ORDER BY number", (anchor["number"], marker["number"])).fetchall()
            if len(blocks) != marker["number"] - anchor["number"] + 1:
                raise RuntimeError("Observed statistics window has block gaps; published daily figures retained.")
            if any(blocks[i]["parent_hash"] != blocks[i - 1]["block_hash"] or blocks[i]["timestamp"] < blocks[i-1]["timestamp"] for i in range(1, len(blocks))):
                raise RuntimeError("Observed window has a reorg or timestamp inconsistency; published daily figures retained.")
            observed = blocks[1:-1]
            if any(not start <= b["timestamp"] < end for b in observed):
                raise RuntimeError("Observed blocks do not match the complete cutoff window")
            first, last = anchor["number"] + 1, marker["number"] - 1
            active = conn.execute("SELECT count(DISTINCT address) FROM preview_block_addresses WHERE block_number BETWEEN ? AND ?", (first, last)).fetchone()[0]
        # Read both boundary states when available. The history contract can
        # recover completed-day power even when Geth pruned an older state.
        boundaries = []
        now = int(time.time())
        for cutoff, block in ((start, anchor), (end, blocks[-2] if len(blocks) > 1 else anchor)):
            boundary = self._capture_boundary(cutoff, source_block, now)
            if boundary is None:
                boundary = {"timestamp": cutoff, "number": block["number"], "hash": block["block_hash"],
                            "power": self._historical_power_at(cutoff), "burned": None,
                            "source": "completed_contract_day_power_history"}
            boundaries.append(boundary)
        delta_power = boundaries[1]["power"] - boundaries[0]["power"]
        state_burned = None
        if boundaries[0].get("burned") is not None and boundaries[1].get("burned") is not None:
            state_burned = boundaries[1]["burned"] - boundaries[0]["burned"]
        logs_burned = self._complete_burn_sum(first, last)
        if state_burned is not None and logs_burned is not None and state_burned != logs_burned:
            raise RuntimeError("Burn event sum disagrees with boundary totalBurnedTokens; retaining previous daily snapshot")
        delta_burned = state_burned if state_burned is not None else logs_burned
        if delta_burned is None:
            raise RuntimeError("Neither both burned cutoff states nor complete burn log coverage are available; retaining published daily snapshot")
        if delta_burned < 0:
            raise RuntimeError("Cumulative burned amount decreased; refusing a new daily snapshot")
        burned_basis = ("totalBurnedTokens at verified window end minus window start" if state_burned is not None else
                        "sum TokensBurned and ChristmasTokensBurned amounts across proven complete POWER log coverage")
        volume = sum(int(b["value_wei"]) for b in observed)
        tx_count = sum(int(b["tx_count"]) for b in observed)
        day = _window(end)
        basis = "POWER contract totalPower at completed-day cutoffs; exact node cutoff states preferred, immutable completed-day history fallback"
        day.update(statistics_window_active_start_block=first, statistics_window_active_end_block=last,
                   statistics_window_active_end_marker_block=marker["number"], statistics_window_active_blocks_scanned=len(observed),
                   statistics_window_active_transactions_seen=tx_count, statistics_window_active_wallet_address_count=active,
                   statistics_window_active_error_count=0,
                   statistics_window_active_address_basis="unique top-level transaction sender/receiver wallet addresses; original site system-address filter",
                   statistics_window_transaction_volume_wei=volume, statistics_window_transaction_volume_display=_token(volume),
                   statistics_window_transaction_volume_basis="sum all top-level native MARS transaction value in the complete confirmed Beijing 08:00 window",
                   statistics_window_start_total_power=boundaries[0]["power"], statistics_window_end_total_power=boundaries[1]["power"],
                   statistics_window_new_power=delta_power, statistics_window_new_power_basis=basis,
                   today_new_power=delta_power, today_new_power_basis=basis,
                   statistics_window_burned_tokens=delta_burned, statistics_window_burned_display=_token(delta_burned),
                   statistics_window_burned_basis=burned_basis, today_burned_tokens=delta_burned,
                   today_burned_display=_token(delta_burned), today_burned_basis=burned_basis,
                   statistics_window_new_blocks=len(observed), statistics_window_new_blocks_basis="contiguous confirmed blocks inside the complete statistics window",
                   today_new_blocks=len(observed), today_new_blocks_basis="contiguous confirmed blocks inside the complete statistics window",
                   statistics_window_new_candidate_address_count=None, statistics_window_new_candidate_address_display="待日结地址快照",
                   statistics_window_new_candidate_address_basis="exact official totalAddresses at cutoff unavailable; not inferred from newly discovered addresses",
                   today_new_wallet_count=None, today_new_wallet_basis="exact official totalAddresses boundary snapshots unavailable")
        day["_field_sources"] = {
            "power": {"start": boundaries[0], "end": boundaries[1]},
            "burned": {"source": "verified_boundary_state_difference" if state_burned is not None else "complete_power_burn_events",
                       "event_cross_checked": logs_burned is not None, "start_block": first, "end_block": last},
            "activity_and_volume": {"source": "contiguous_local_rpc_blocks", "start_block": first, "end_block": last},
            "new_addresses": {"status": "unavailable", "reason": "no exact official totalAddresses at both cutoffs"},
        }
        return day, boundaries

    def _complete_burn_sum(self, first: int, last: int) -> int | None:
        if last < first:
            return 0
        with self._observations() as conn:
            try:
                ranges = conn.execute("SELECT start_block,end_block FROM preview_log_scan_ranges WHERE end_block>=? AND start_block<=? ORDER BY start_block",
                                      (first, last)).fetchall()
                cursor = first
                for row in ranges:
                    if row["start_block"] > cursor:
                        return None
                    cursor = max(cursor, row["end_block"] + 1)
                    if cursor > last:
                        break
                if cursor <= last:
                    return None
                events = conn.execute("SELECT amount_wei FROM preview_power_events WHERE block_number BETWEEN ? AND ? AND kind IN ('TokensBurned','ChristmasTokensBurned')",
                                      (first, last)).fetchall()
            except sqlite3.Error:
                return None
        amounts = [_integer(row[0]) for row in events]
        if any(value is None or value < 0 for value in amounts):
            raise ValueError("Invalid POWER burn event amount in completed window")
        return sum(amounts)
