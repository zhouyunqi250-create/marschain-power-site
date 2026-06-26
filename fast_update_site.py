#!/usr/bin/env python3
"""Fast official-snapshot refresh for the MarsChain site."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

from refresh_site import (
    apply_official_delta_meta,
    build_metric_snapshot,
    build_metric_trends,
    load_metric_history,
    merge_metric_history,
    normalize_metric_history,
    write_site_bundle,
)
from marschain_power_rank import (
    BASE_URL,
    DEFAULT_RPC_URL,
    build_mars_emission_meta,
    build_statistics_window_meta,
    fetch_daily_total_power_history,
    format_beijing_datetime,
    format_price,
    format_token_chinese,
    hex_to_int,
    request_json,
    rpc_call,
)

DEFAULT_PUBLIC_ORIGIN = "https://marschain-power-site-chu.oss-cn-hangzhou.aliyuncs.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a fast official-snapshot refresh for the MarsChain site.")
    parser.add_argument("--site-dir", default="site", help="Directory for deployable static site output.")
    parser.add_argument("--output-dir", default="output", help="Directory for generated ranking files.")
    parser.add_argument("--origin", default=DEFAULT_PUBLIC_ORIGIN, help="Public site origin used to restore the last published data.")
    parser.add_argument("--rpc-url", default=DEFAULT_RPC_URL, help="Public JSON-RPC endpoint.")
    return parser.parse_args()


def load_json_from_url(url: str, timeout: int = 60) -> object | None:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def load_public_payload(origin: str) -> dict:
    candidates = [
        f"{origin.rstrip('/')}/data/latest.json?v={int(time.time())}",
        "site/data/latest.json",
        "output/latest/latest.json",
    ]
    for candidate in candidates:
        if candidate.startswith("http"):
            payload = load_json_from_url(candidate)
        else:
            path = Path(candidate)
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = None
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("Unable to load public latest.json from origin or local fallbacks.")


def load_public_metric_history(origin: str, site_dir: Path) -> list[dict]:
    candidates = [
        f"{origin.rstrip('/')}/data/metric-history.json?v={int(time.time())}",
        site_dir / "data" / "metric-history.json",
        Path("output/latest/metric-history.json"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            payload = load_json_from_url(candidate)
        else:
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                payload = None
        if payload is not None:
            return normalize_metric_history(payload)
    return []


def _as_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def fetch_latest_block(rpc_url: str, network_stats: dict | None = None) -> int | None:
    if isinstance(network_stats, dict):
        for key in ("latestBlockNumber", "chainBlockNumber", "blockNumber"):
            value = _as_int(network_stats.get(key))
            if value and value > 0:
                return value
    try:
        blocks = request_json("/blocks", {"page": 1, "limit": 1})
        if isinstance(blocks, dict):
            block_rows = blocks.get("blocks")
            if isinstance(block_rows, list) and block_rows:
                value = _as_int(block_rows[0].get("number"))
                if value and value > 0:
                    return value
    except Exception:
        pass
    try:
        value = rpc_call(rpc_url, "eth_blockNumber")
    except Exception:
        return None
    return hex_to_int(value) if isinstance(value, str) else None


def build_fast_meta(base_meta: dict, origin_history: list[dict], rpc_url: str) -> dict:
    meta = dict(base_meta)
    now = int(time.time())
    window_meta = build_statistics_window_meta(now)
    reference_timestamp = int(window_meta["statistics_window_end_timestamp"])
    network_stats = request_json("/stats")
    power_stats = request_json("/power/stats")
    latest_block = fetch_latest_block(rpc_url, network_stats)

    total_power = int(power_stats.get("totalPower", "0") or 0) if isinstance(power_stats, dict) else 0
    total_burned = int(power_stats.get("totalBurnedTokens", "0") or 0) if isinstance(power_stats, dict) else 0
    total_wallets = int(network_stats.get("totalAddresses", 0) or 0) if isinstance(network_stats, dict) else 0
    total_circulation = int(network_stats.get("totalCirculation", "0") or 0) if isinstance(network_stats, dict) else 0
    current_price = network_stats.get("currentPrice") if isinstance(network_stats, dict) else None
    highest_price = network_stats.get("highestPrice") if isinstance(network_stats, dict) else None
    lowest_price = network_stats.get("lowestPrice") if isinstance(network_stats, dict) else None

    history_days = 0
    daily_power_history: list[dict] = []
    start_total_power = None
    end_total_power = None
    try:
        days, powers = fetch_daily_total_power_history(rpc_url)
        history_days = len(days)
        history_map = dict(zip(days, powers))

        start_day = int(window_meta["statistics_window_start_timestamp"] // 86_400)
        end_day = int(window_meta["statistics_window_end_timestamp"] // 86_400)
        start_total_power = history_map.get(start_day)
        end_total_power = history_map.get(end_day, total_power)
        daily_power_history = [
            {
                "day": int(day),
                "date": time.strftime("%Y-%m-%d", time.gmtime(int(day) * 86_400)),
                "value": int(power),
            }
            for day, power in sorted(history_map.items())[-90:]
        ]
        if start_total_power is not None and end_total_power is not None:
            meta["today_new_power"] = int(end_total_power) - int(start_total_power)
            meta["statistics_window_new_power"] = meta["today_new_power"]
            meta["today_new_power_basis"] = "official 08:00 totalPower minus previous completed 08:00 totalPower"
            meta["statistics_window_new_power_basis"] = meta["today_new_power_basis"]
    except Exception:
        history_map = {}

    meta.update(window_meta)
    emission_meta = build_mars_emission_meta(rpc_url, reference_timestamp, total_power)
    meta.update(
        {
            "generated_at": now,
            "generated_at_local": format_beijing_datetime(now),
            "base_url": BASE_URL,
            "ranking_type": "fast_official_snapshot",
            "fast_update_only": True,
            "fast_metrics_ready": True,
            "fast_metrics_generated_at": now,
            "rpc_url": rpc_url,
            "rpc_latest_block": latest_block,
            "latest_block": latest_block,
            "network_total_power": total_power,
            "network_total_burned_tokens": total_burned,
            "network_total_burned_display": format_token_chinese(total_burned),
            "explorer_total_addresses": total_wallets,
            "network_total_circulation_tokens": total_circulation,
            "network_total_circulation_display": format_token_chinese(total_circulation),
            "network_current_price": current_price,
            "network_current_price_display": format_price(current_price),
            "network_highest_price": highest_price,
            "network_highest_price_display": format_price(highest_price),
            "network_lowest_price": lowest_price,
            "network_lowest_price_display": format_price(lowest_price),
            "statistics_window_start_total_power": start_total_power,
            "statistics_window_end_total_power": end_total_power or total_power,
            "statistics_window_start_day": int(window_meta["statistics_window_start_timestamp"] // 86_400),
            "statistics_window_end_day": int(window_meta["statistics_window_end_timestamp"] // 86_400),
            "today_local_date": window_meta["statistics_day_label"],
            "today_chain_day": int(window_meta["statistics_window_start_timestamp"] // 86_400),
            "daily_power_history_days": history_days,
            "daily_total_power_history": daily_power_history,
        }
    )
    meta.update(emission_meta)
    meta = apply_official_delta_meta(meta, origin_history, base_meta)
    meta["statistics_window_end_total_power"] = meta.get("statistics_window_end_total_power") or total_power
    meta["full_scan_statistics_window_end_timestamp"] = None
    meta["full_scan_statistics_window_end_local"] = None
    if start_total_power is not None and end_total_power is not None:
        meta["statistics_window_new_power"] = int(end_total_power) - int(start_total_power)
        meta["today_new_power"] = meta["statistics_window_new_power"]
    meta["metric_trends"] = build_metric_trends(meta, merge_metric_history(origin_history, build_metric_snapshot(meta)))
    return meta


def main() -> int:
    args = parse_args()
    site_dir = Path(args.site_dir)
    output_dir = Path(args.output_dir)
    data_dir = site_dir / "data"
    mobile_dir = site_dir / "m"
    output_latest_dir = output_dir / "latest"
    output_history_dir = output_dir / "history"

    site_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    mobile_dir.mkdir(parents=True, exist_ok=True)
    output_latest_dir.mkdir(parents=True, exist_ok=True)
    output_history_dir.mkdir(parents=True, exist_ok=True)

    base_payload = load_public_payload(args.origin)
    base_meta = dict(base_payload.get("meta") or {})
    public_history = load_public_metric_history(args.origin, site_dir)

    meta = build_fast_meta(base_meta, public_history, args.rpc_url)
    payload = {"meta": meta, "rows": list(base_payload.get("rows") or [])}

    metric_history = merge_metric_history(public_history, build_metric_snapshot(meta))
    payload["meta"]["metric_trends"] = build_metric_trends(payload["meta"], metric_history)

    site_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    mobile_dir.mkdir(parents=True, exist_ok=True)

    write_site_bundle(site_dir, payload, metric_history)
    (output_latest_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_latest_dir / "build-meta.json").write_text(
        json.dumps(
            {
                "generated_at": meta.get("generated_at"),
                "generated_at_local": meta.get("generated_at_local"),
                "fast_update_only": True,
                "fast_metrics_ready": True,
                "full_scan_statistics_window_end_timestamp": None,
                "statistics_window_end_timestamp": meta.get("statistics_window_end_timestamp"),
                "statistics_window_end_local": meta.get("statistics_window_end_local"),
                "network_total_power": meta.get("network_total_power"),
                "network_total_burned_tokens": meta.get("network_total_burned_tokens"),
                "explorer_total_addresses": meta.get("explorer_total_addresses"),
                "latest_block": meta.get("latest_block"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (site_dir / "build-meta.json").write_text(
        json.dumps(
            {
                "generated_at": meta.get("generated_at"),
                "generated_at_local": meta.get("generated_at_local"),
                "fast_update_only": True,
                "fast_metrics_ready": True,
                "full_scan_statistics_window_end_timestamp": None,
                "statistics_window_end_timestamp": meta.get("statistics_window_end_timestamp"),
                "statistics_window_end_local": meta.get("statistics_window_end_local"),
                "network_total_power": meta.get("network_total_power"),
                "network_total_burned_tokens": meta.get("network_total_burned_tokens"),
                "explorer_total_addresses": meta.get("explorer_total_addresses"),
                "latest_block": meta.get("latest_block"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_history_dir / ".fast-official-snapshot").write_text("fast official snapshot build\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
