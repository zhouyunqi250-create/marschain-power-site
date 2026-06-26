#!/usr/bin/env python3
"""Refresh the MarsChain ranking site, aiming for the coverage target."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from argparse import Namespace
from pathlib import Path

from build_frontend_dashboard import build_html, build_mobile_html
from marschain_power_rank import DEFAULT_CACHE_TTL_SECONDS, build_ranking, format_token_chinese, write_csv, write_html, write_json, write_xlsx
from price_data import build_price_payload_from_meta, load_price_file

PUBLIC_RANK_LIMIT = 100
METRIC_HISTORY_LIMIT = 90

OFFICIAL_FAST_META_KEYS = (
    "network_total_power",
    "network_total_burned_tokens",
    "network_total_burned_display",
    "explorer_total_addresses",
    "network_total_circulation_tokens",
    "network_total_circulation_display",
    "network_current_price",
    "network_current_price_display",
    "network_highest_price",
    "network_highest_price_display",
    "network_lowest_price",
    "network_lowest_price_display",
    "latest_block",
    "rpc_latest_block",
    "statistics_window_start_total_power",
    "statistics_window_end_total_power",
    "statistics_window_new_power",
    "statistics_window_new_power_basis",
    "today_new_power",
    "today_new_power_basis",
    "statistics_window_new_candidate_address_count",
    "statistics_window_new_candidate_address_basis",
    "today_new_wallet_count",
    "today_new_wallet_basis",
    "statistics_window_burned_tokens",
    "statistics_window_burned_display",
    "statistics_window_burned_basis",
    "today_burned_tokens",
    "today_burned_display",
    "today_burned_basis",
    "statistics_window_new_blocks",
    "statistics_window_new_blocks_basis",
    "today_new_blocks",
    "today_new_blocks_basis",
)

METRIC_SNAPSHOT_FIELDS = {
    "network_total_power": "network_total_power",
    "network_total_circulation": "network_total_circulation_tokens",
    "network_current_price": "network_current_price",
    "latest_block": "latest_block",
    "total_supply": "emission_total_supply_cap_tokens",
    "daily_emission": "emission_daily_total_tokens",
    "total_burned": "network_total_burned_tokens",
    "total_wallets": "explorer_total_addresses",
    "positive_power_addresses": "positive_power_count",
    "daily_active_addresses": "statistics_window_active_wallet_address_count",
    "daily_new_addresses": "statistics_window_new_candidate_address_count",
    "daily_new_power": "statistics_window_new_power",
    "daily_burned": "statistics_window_burned_tokens",
    "daily_transaction_volume": "statistics_window_transaction_volume_wei",
    "daily_transaction_count": "statistics_window_active_transactions_seen",
    "period_7d_new_power": "period_7d_new_power",
    "period_7d_new_addresses": "period_7d_new_candidate_address_count",
    "period_7d_burned": "period_7d_burned_tokens",
    "period_30d_new_power": "period_30d_new_power",
    "period_30d_new_addresses": "period_30d_new_candidate_address_count",
    "period_30d_burned": "period_30d_burned_tokens",
    "power_per_coin": "power_required_per_mars_daily",
}


SCAN_TIERS = [
    {
        "tx_pages": 0,
        "block_pages": 0,
        "max_candidates": 3000,
        "upline_limit": 0,
        "upline_depth": 0,
        "workers": 16,
        "history_depth": 0,
        "history_pages": 0,
        "history_seed_limit": 0,
        "history_candidate_limit": 0,
        "rpc_blocks": 0,
        "rpc_log_blocks": 10000000,
    },
]

EXTRA_BUILD_META_KEYS = [
    "generated_at_local",
    "statistics_timezone",
    "statistics_timezone_label",
    "statistics_day_start_hour",
    "statistics_window_start_timestamp",
    "statistics_window_end_timestamp",
    "statistics_window_start_local",
    "statistics_window_end_local",
    "statistics_window_label",
    "statistics_day_label",
    "statistics_window_start_day",
    "statistics_window_end_day",
    "statistics_window_active_address_basis",
    "statistics_window_active_wallet_address_count",
    "statistics_window_active_blocks_scanned",
    "statistics_window_active_transactions_seen",
    "statistics_window_transaction_volume_wei",
    "statistics_window_transaction_volume_display",
    "statistics_window_transaction_volume_basis",
    "statistics_window_active_start_block",
    "statistics_window_active_end_block",
    "statistics_window_new_candidate_address_count",
    "statistics_window_new_candidate_address_basis",
    "statistics_window_new_power",
    "statistics_window_new_power_basis",
    "statistics_window_burned_tokens",
    "statistics_window_burned_display",
    "statistics_window_burned_basis",
    "statistics_window_start_total_power",
    "statistics_window_end_total_power",
    "today_burned_tokens",
    "today_burned_display",
    "today_burned_basis",
    "today_new_blocks",
    "today_new_blocks_basis",
    "statistics_window_new_blocks",
    "statistics_window_new_blocks_basis",
    "today_local_date",
    "fast_update_only",
    "fast_metrics_ready",
    "fast_metrics_generated_at",
    "fast_metrics_cutoff_locked",
    "full_scan_statistics_window_end_timestamp",
    "full_scan_statistics_window_end_local",
    "emission_basis",
    "emission_reference_timestamp",
    "emission_genesis_timestamp",
    "emission_current_cycle",
    "emission_cycle_elapsed_days",
    "emission_total_supply_cap_tokens",
    "emission_total_supply_cap_display",
    "emission_initial_cycle_output_tokens",
    "emission_halving_period_days",
    "emission_miner_share",
    "emission_node_share",
    "emission_cycle_output_tokens",
    "emission_daily_total_tokens",
    "emission_daily_total_display",
    "emission_daily_miner_tokens",
    "emission_daily_miner_display",
    "emission_daily_node_tokens",
    "emission_daily_node_display",
    "power_required_per_mars_daily",
    "power_required_per_mars_daily_display",
    "network_total_circulation_tokens",
    "network_total_circulation_display",
    "network_current_price",
    "network_current_price_display",
    "network_highest_price",
    "network_highest_price_display",
    "network_lowest_price",
    "network_lowest_price_display",
    "latest_block",
    "network_total_burned_display",
    "period_7d_label",
    "period_7d_start_timestamp",
    "period_7d_end_timestamp",
    "period_7d_start_local",
    "period_7d_end_local",
    "period_7d_start_block",
    "period_7d_end_block",
    "period_7d_complete",
    "period_7d_start_day",
    "period_7d_end_day",
    "period_7d_start_total_power",
    "period_7d_end_total_power",
    "period_7d_new_power",
    "period_7d_new_power_basis",
    "period_7d_new_candidate_address_count",
    "period_7d_new_candidate_address_basis",
    "period_7d_burned_tokens",
    "period_7d_burned_display",
    "period_7d_burned_basis",
    "period_30d_label",
    "period_30d_start_timestamp",
    "period_30d_end_timestamp",
    "period_30d_start_local",
    "period_30d_end_local",
    "period_30d_start_block",
    "period_30d_end_block",
    "period_30d_complete",
    "period_30d_start_day",
    "period_30d_end_day",
    "period_30d_start_total_power",
    "period_30d_end_total_power",
    "period_30d_new_power",
    "period_30d_new_power_basis",
    "period_30d_new_candidate_address_count",
    "period_30d_new_candidate_address_basis",
    "period_30d_burned_tokens",
    "period_30d_burned_display",
    "period_30d_burned_basis",
]


def add_extra_build_meta(summary: dict, meta: dict) -> dict:
    for key in EXTRA_BUILD_META_KEYS:
        if key in meta:
            summary[key] = meta.get(key)
    return summary


def make_args(
    tx_pages: int,
    block_pages: int,
    max_candidates: int,
    upline_limit: int,
    upline_depth: int,
    workers: int,
    history_depth: int,
    history_pages: int,
    history_seed_limit: int,
    history_candidate_limit: int,
    rpc_blocks: int,
    rpc_log_blocks: int,
    cache_file: Path,
    address_pool_file: Path,
    cache_ttl_seconds: int,
) -> Namespace:
    return Namespace(
        tx_pages=tx_pages,
        tx_limit=100,
        block_pages=block_pages,
        block_limit=100,
        rpc_url="https://rpcs.marschain.net",
        rpc_blocks=rpc_blocks,
        rpc_start_block=None,
        rpc_batch_size=100,
        rpc_workers=6,
        rpc_log_blocks=rpc_log_blocks,
        rpc_log_start_block=None,
        rpc_log_chunk_size=100000,
        rpc_log_workers=4,
        max_candidates=max_candidates,
        top=0,
        workers=workers,
        include_to=True,
        include_nodes=False,
        upline_depth=upline_depth,
        upline_limit=upline_limit,
        history_depth=history_depth,
        history_pages=history_pages,
        history_tx_limit=100,
        history_seed_limit=history_seed_limit,
        history_candidate_limit=history_candidate_limit,
        output_dir="output",
        prefix="marschain_power_rank",
        cache_file=str(cache_file),
        address_pool_file=str(address_pool_file),
        cache_ttl_seconds=cache_ttl_seconds,
        progress=True,
    )


def build_public_payload(payload: dict, public_rank_limit: int = PUBLIC_RANK_LIMIT) -> dict:
    rows = payload.get("rows", [])
    meta = dict(payload.get("meta", {}))
    public_rows = rows[:public_rank_limit]
    meta["full_ranked_count"] = len(rows)
    meta["public_rank_limit"] = public_rank_limit
    meta["ranked_count"] = len(public_rows)
    meta["paid_download"] = {
        "enabled": True,
        "free_rank_limit": public_rank_limit,
        "price_mars": "1000",
        "asset": "MARS",
        "download_expires_seconds": 3600,
    }
    return {"meta": meta, "rows": public_rows}


def _as_metric_number(value: object) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    if number.is_integer():
        return int(number)
    return number


def build_metric_snapshot(meta: dict) -> dict:
    values: dict[str, float | int] = {}
    for metric_key, meta_key in METRIC_SNAPSHOT_FIELDS.items():
        value = _as_metric_number(meta.get(meta_key))
        if value is not None:
            values[metric_key] = value
    power_required = _as_metric_number(meta.get("power_required_per_mars_daily"))
    if power_required and power_required > 0:
        values["one_yi_power_output"] = 100_000_000 / float(power_required)
    return {
        "generated_at": meta.get("generated_at"),
        "generated_at_local": meta.get("generated_at_local"),
        "statistics_window_end_timestamp": meta.get("statistics_window_end_timestamp"),
        "statistics_window_end_local": meta.get("statistics_window_end_local"),
        "statistics_window_label": meta.get("statistics_window_label"),
        "values": values,
    }


def normalize_metric_history(raw: object) -> list[dict]:
    if isinstance(raw, dict):
        raw = raw.get("snapshots") or []
    if not isinstance(raw, list):
        return []
    normalized: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        values = item.get("values")
        if not isinstance(values, dict):
            continue
        cleaned_values = {
            key: value
            for key, value in ((key, _as_metric_number(value)) for key, value in values.items())
            if value is not None
        }
        if not cleaned_values:
            continue
        normalized.append(
            {
                "generated_at": item.get("generated_at"),
                "generated_at_local": item.get("generated_at_local"),
                "statistics_window_end_timestamp": item.get("statistics_window_end_timestamp"),
                "statistics_window_end_local": item.get("statistics_window_end_local"),
                "statistics_window_label": item.get("statistics_window_label"),
                "values": cleaned_values,
            }
        )
    return normalized[-METRIC_HISTORY_LIMIT:]


def load_metric_history(site_dir: Path) -> list[dict]:
    path = site_dir / "data" / "metric-history.json"
    if not path.exists():
        return []
    try:
        return normalize_metric_history(json.loads(path.read_text()))
    except Exception:
        return []


def merge_metric_history(existing: list[dict], snapshot: dict) -> list[dict]:
    merged = normalize_metric_history(existing)
    identity = snapshot.get("statistics_window_end_timestamp") or snapshot.get("generated_at")
    if identity:
        merged = [
            item
            for item in merged
            if (item.get("statistics_window_end_timestamp") or item.get("generated_at")) != identity
        ]
    if snapshot.get("values"):
        merged.append(snapshot)
    merged.sort(key=lambda item: item.get("generated_at") or item.get("statistics_window_end_timestamp") or 0)
    return merged[-METRIC_HISTORY_LIMIT:]


def metric_snapshot_value(meta: dict, metric_key: str) -> float | int | None:
    meta_key = METRIC_SNAPSHOT_FIELDS.get(metric_key)
    if not meta_key:
        return None
    return _as_metric_number(meta.get(meta_key))


def previous_metric_value(
    metric_history: list[dict],
    metric_key: str,
    current_end_timestamp: float | int | None,
    fallback_meta: dict | None = None,
) -> float | int | None:
    current_end = _as_metric_number(current_end_timestamp)
    history = normalize_metric_history(metric_history)
    for item in reversed(history):
        end_ts = _as_metric_number(item.get("statistics_window_end_timestamp"))
        if current_end is not None and end_ts is not None and end_ts >= current_end:
            continue
        value = item.get("values", {}).get(metric_key)
        if value is not None:
            return value
    if fallback_meta is not None:
        fallback_end = _as_metric_number(fallback_meta.get("statistics_window_end_timestamp"))
        if current_end is None or fallback_end is None or fallback_end < current_end:
            value = metric_snapshot_value(fallback_meta, metric_key)
            if value is not None:
                return value
    return None


def apply_official_delta_meta(meta: dict, metric_history: list[dict], fallback_meta: dict | None = None) -> dict:
    updated = dict(meta)
    current_end = _as_metric_number(updated.get("statistics_window_end_timestamp"))

    current_power = _as_metric_number(updated.get("statistics_window_end_total_power"))
    previous_power = previous_metric_value(metric_history, "network_total_power", current_end, fallback_meta)
    if current_power is not None:
        start_power = _as_metric_number(updated.get("statistics_window_start_total_power"))
        if start_power is not None:
            delta_power = current_power - start_power
        elif previous_power is not None:
            delta_power = current_power - previous_power
        else:
            delta_power = None
        if delta_power is not None:
            updated["today_new_power"] = delta_power
            updated["statistics_window_new_power"] = delta_power
            updated["today_new_power_basis"] = f"official 08:00 totalPower minus previous completed 08:00 totalPower"
            updated["statistics_window_new_power_basis"] = updated["today_new_power_basis"]

    current_wallets = _as_metric_number(updated.get("explorer_total_addresses"))
    previous_wallets = previous_metric_value(metric_history, "total_wallets", current_end, fallback_meta)
    if current_wallets is not None:
        if previous_wallets is not None:
            delta_wallets = current_wallets - previous_wallets
        else:
            delta_wallets = None
        if delta_wallets is not None:
            updated["statistics_window_new_candidate_address_count"] = delta_wallets
            updated["today_new_wallet_count"] = delta_wallets
            updated["statistics_window_new_candidate_address_basis"] = "official explorer totalAddresses minus previous completed 08:00 totalAddresses"
            updated["today_new_wallet_basis"] = updated["statistics_window_new_candidate_address_basis"]

    current_burned = _as_metric_number(updated.get("network_total_burned_tokens"))
    previous_burned = previous_metric_value(metric_history, "total_burned", current_end, fallback_meta)
    if current_burned is not None:
        if previous_burned is not None:
            delta_burned = current_burned - previous_burned
        else:
            delta_burned = None
        if delta_burned is not None:
            updated["statistics_window_burned_tokens"] = delta_burned
            updated["statistics_window_burned_display"] = format_token_chinese(delta_burned)
            updated["today_burned_tokens"] = delta_burned
            updated["today_burned_display"] = format_token_chinese(delta_burned)
            updated["statistics_window_burned_basis"] = "official /power/stats totalBurnedTokens minus previous completed 08:00 totalBurnedTokens"
            updated["today_burned_basis"] = updated["statistics_window_burned_basis"]

    current_block = _as_metric_number(updated.get("latest_block"))
    previous_block = previous_metric_value(metric_history, "latest_block", current_end, fallback_meta)
    if current_block is not None and previous_block is not None:
        delta_blocks = current_block - previous_block
        updated["statistics_window_new_blocks"] = delta_blocks
        updated["today_new_blocks"] = delta_blocks
        updated["statistics_window_new_blocks_basis"] = "official explorer latestBlockNumber minus previous completed 08:00 latestBlockNumber"
        updated["today_new_blocks_basis"] = updated["statistics_window_new_blocks_basis"]

    return updated


def preserve_fast_official_meta(scan_meta: dict, fast_meta: dict | None) -> dict:
    if not isinstance(fast_meta, dict):
        return scan_meta
    if not fast_meta.get("fast_metrics_ready"):
        return scan_meta
    if fast_meta.get("statistics_window_end_timestamp") != scan_meta.get("statistics_window_end_timestamp"):
        return scan_meta
    updated = dict(scan_meta)
    for key in OFFICIAL_FAST_META_KEYS:
        if key in fast_meta:
            updated[key] = fast_meta.get(key)
    updated["fast_metrics_ready"] = True
    updated["fast_metrics_generated_at"] = fast_meta.get("fast_metrics_generated_at") or fast_meta.get("generated_at")
    updated["fast_metrics_cutoff_locked"] = True
    return updated


def build_metric_trends(meta: dict, metric_history: list[dict]) -> dict:
    trends: dict[str, dict] = {}
    daily_power_history = meta.get("daily_total_power_history")
    if isinstance(daily_power_history, list):
        values = [
            {"label": item.get("date") or item.get("day"), "value": _as_metric_number(item.get("value"))}
            for item in daily_power_history
            if isinstance(item, dict) and _as_metric_number(item.get("value")) is not None
        ]
        if values:
            trends["network_total_power"] = {"source": "POWER 合约日趋势", "values": values[-30:]}
    def day_label(item: dict) -> str:
        raw = str(item.get("statistics_window_end_local") or item.get("generated_at_local") or "")
        if len(raw) >= 10:
            return raw[:10]
        return raw or "unknown"

    history = normalize_metric_history(metric_history)
    for metric_key in sorted(set(METRIC_SNAPSHOT_FIELDS) | {"one_yi_power_output"}):
        if metric_key == "network_total_power" and metric_key in trends:
            continue
        daily_values: dict[str, dict] = {}
        for item in history:
            value = item.get("values", {}).get(metric_key)
            if value is None:
                continue
            daily_values[day_label(item)] = {"label": day_label(item), "value": value}
        values = list(daily_values.values())
        if values:
            trends[metric_key] = {"source": "站点刷新趋势", "values": values[-30:]}
    return trends


def write_site_bundle(site_dir: Path, payload: dict, metric_history: list[dict] | None = None) -> None:
    data_dir = site_dir / "data"
    mobile_dir = site_dir / "m"
    site_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    mobile_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir = site_dir / "downloads"
    if downloads_dir.exists():
        shutil.rmtree(downloads_dir)

    existing_price = load_price_file(data_dir / "price.json")
    (site_dir / "index.html").write_text(build_html(payload), encoding="utf-8")
    (mobile_dir / "index.html").write_text(build_mobile_html(payload), encoding="utf-8")
    (data_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (data_dir / "price.json").write_text(
        json.dumps(build_price_payload_from_meta(payload.get("meta", {}), existing_price), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    if metric_history is not None:
        (data_dir / "metric-history.json").write_text(
            json.dumps({"snapshots": normalize_metric_history(metric_history)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (site_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the MarsChain ranking site while trying to reach the coverage target.")
    parser.add_argument("--coverage-target", type=float, default=0.80, help="Preferred coverage target before stopping early.")
    parser.add_argument("--output-dir", default="output", help="Directory for generated ranking files.")
    parser.add_argument("--site-dir", default="site", help="Directory for deployable static site output.")
    parser.add_argument("--cache-file", default="output/marschain_power_cache.json", help="Shared cache file for power lookups.")
    parser.add_argument(
        "--cache-ttl-seconds",
        type=int,
        default=DEFAULT_CACHE_TTL_SECONDS,
        help="Power lookup cache TTL. The default is shorter than the 24-hour schedule so each scheduled build refreshes power data.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    history_dir = output_dir / "history"
    latest_dir = output_dir / "latest"
    site_dir = Path(args.site_dir)
    cache_file = Path(args.cache_file)
    address_pool_file = Path("output/marschain_address_pool.json")

    history_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    site_dir.mkdir(parents=True, exist_ok=True)

    chosen_rows = None
    chosen_meta = None
    chosen_label = None
    target_met = False

    for tier in SCAN_TIERS:
        run_args = make_args(
            tx_pages=tier["tx_pages"],
            block_pages=tier["block_pages"],
            max_candidates=tier["max_candidates"],
            upline_limit=tier["upline_limit"],
            upline_depth=tier["upline_depth"],
            workers=tier["workers"],
            history_depth=tier["history_depth"],
            history_pages=tier["history_pages"],
            history_seed_limit=tier["history_seed_limit"],
            history_candidate_limit=tier["history_candidate_limit"],
            rpc_blocks=tier["rpc_blocks"],
            rpc_log_blocks=tier["rpc_log_blocks"],
            cache_file=cache_file,
            address_pool_file=address_pool_file,
            cache_ttl_seconds=args.cache_ttl_seconds,
        )
        rows, meta = build_ranking(run_args)
        coverage = meta["discovered_power_coverage"]
        chosen_rows = rows
        chosen_meta = meta
        chosen_label = f'tx{tier["tx_pages"]}_blk{tier["block_pages"]}'
        target_met = coverage >= args.coverage_target
        print(
            f"[info] tier {chosen_label}: "
            f'coverage={coverage:.4%} candidates={meta["candidate_count"]} positive={meta["positive_power_count"]}'
        )
        if target_met:
            break

    if chosen_rows is None or chosen_meta is None or chosen_label is None:
        raise RuntimeError("No ranking results were generated.")

    if not target_met:
        print(
            f"[warn] coverage target not met after deepest scan tier {chosen_label}: "
            f"{chosen_meta['discovered_power_coverage']:.4%} < {args.coverage_target:.2%}. "
            "Publishing the deepest completed result anyway.",
        )

    chosen_meta = dict(chosen_meta)
    chosen_meta["coverage_target"] = args.coverage_target
    chosen_meta["target_met"] = target_met
    chosen_meta["tier_label"] = chosen_label
    existing_metric_history = load_metric_history(site_dir)
    fast_meta: dict | None = None
    latest_public_path = site_dir / "data" / "latest.json"
    if latest_public_path.exists():
        try:
            fast_meta = json.loads(latest_public_path.read_text(encoding="utf-8")).get("meta")
        except Exception:
            fast_meta = None
    chosen_meta = preserve_fast_official_meta(chosen_meta, fast_meta)
    chosen_meta = apply_official_delta_meta(chosen_meta, existing_metric_history)
    chosen_meta["fast_update_only"] = False
    chosen_meta["full_scan_statistics_window_end_timestamp"] = chosen_meta.get("statistics_window_end_timestamp")
    chosen_meta["full_scan_statistics_window_end_local"] = chosen_meta.get("statistics_window_end_local")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    payload = {"meta": chosen_meta, "rows": [row.to_dict() for row in chosen_rows]}

    json_path = history_dir / f"marschain_power_rank_{chosen_label}_{stamp}.json"
    csv_path = history_dir / f"marschain_power_rank_{chosen_label}_{stamp}.csv"
    html_path = history_dir / f"marschain_power_rank_{chosen_label}_{stamp}.html"
    xlsx_path = history_dir / f"marschain_power_rank_{chosen_label}_{stamp}.xlsx"

    write_json(json_path, chosen_rows, chosen_meta)
    write_csv(csv_path, chosen_rows)
    write_html(html_path, chosen_rows, chosen_meta)
    write_xlsx(xlsx_path, chosen_rows, chosen_meta)

    (latest_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (latest_dir / "build-meta.json").write_text(
        json.dumps(
            add_extra_build_meta(
                {
                "generated_at": chosen_meta["generated_at"],
                "coverage": chosen_meta["discovered_power_coverage"],
                "coverage_target": chosen_meta["coverage_target"],
                "target_met": chosen_meta["target_met"],
                "candidate_count": chosen_meta["candidate_count"],
                "positive_power_count": chosen_meta["positive_power_count"],
                "tx_pages": chosen_meta["tx_pages"],
                "block_pages": chosen_meta["block_pages"],
                "rpc_blocks_scanned": chosen_meta.get("rpc_blocks_scanned", 0),
                "rpc_transactions_seen": chosen_meta.get("rpc_transactions_seen", 0),
                "rpc_start_block": chosen_meta.get("rpc_start_block"),
                "rpc_end_block": chosen_meta.get("rpc_end_block"),
                "rpc_log_blocks_effective": chosen_meta.get("rpc_log_blocks_effective", 0),
                "rpc_log_blocks_scanned": chosen_meta.get("rpc_log_blocks_scanned", 0),
                "rpc_logs_seen": chosen_meta.get("rpc_logs_seen", 0),
                "rpc_log_addresses_seen": chosen_meta.get("rpc_log_addresses_seen", 0),
                "rpc_log_first_seen_tracked": chosen_meta.get("rpc_log_first_seen_tracked", 0),
                "rpc_log_start_block": chosen_meta.get("rpc_log_start_block"),
                "rpc_log_end_block": chosen_meta.get("rpc_log_end_block"),
                "rpc_log_today_start_block": chosen_meta.get("rpc_log_today_start_block"),
                "today_chain_day": chosen_meta.get("today_chain_day"),
                "today_utc_date": chosen_meta.get("today_utc_date"),
                "today_new_wallet_count": chosen_meta.get("today_new_wallet_count"),
                "today_new_power": chosen_meta.get("today_new_power"),
                "previous_day_total_power": chosen_meta.get("previous_day_total_power"),
                "daily_power_history_days": chosen_meta.get("daily_power_history_days", 0),
                "explorer_total_addresses": chosen_meta.get("explorer_total_addresses", 0),
                "network_total_power": chosen_meta.get("network_total_power", 0),
                "discovered_total_power": chosen_meta.get("discovered_total_power", 0),
                "network_total_burned_tokens": chosen_meta.get("network_total_burned_tokens", 0),
                "power_cache_ttl_seconds": chosen_meta.get("power_cache_ttl_seconds"),
                "power_cache_hits": chosen_meta.get("power_cache_hits", 0),
                "power_cache_refreshed": chosen_meta.get("power_cache_refreshed", 0),
                "power_cache_stale_fallbacks": chosen_meta.get("power_cache_stale_fallbacks", 0),
                "tier_label": chosen_meta["tier_label"],
                "history_json": str(json_path),
                "history_csv": str(csv_path),
                "history_html": str(html_path),
                "history_xlsx": str(xlsx_path),
                },
                chosen_meta,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copy2(csv_path, latest_dir / "latest.csv")
    shutil.copy2(html_path, latest_dir / "latest.html")
    shutil.copy2(xlsx_path, latest_dir / "latest.xlsx")

    metric_history = merge_metric_history(existing_metric_history, build_metric_snapshot(chosen_meta))
    public_payload = build_public_payload(payload)
    public_payload["meta"]["metric_trends"] = build_metric_trends(public_payload["meta"], metric_history)
    write_site_bundle(site_dir, public_payload, metric_history)

    summary = add_extra_build_meta({
        "generated_at": chosen_meta["generated_at"],
        "coverage": chosen_meta["discovered_power_coverage"],
        "coverage_target": chosen_meta["coverage_target"],
        "target_met": chosen_meta["target_met"],
        "candidate_count": chosen_meta["candidate_count"],
        "positive_power_count": chosen_meta["positive_power_count"],
        "tx_pages": chosen_meta["tx_pages"],
        "block_pages": chosen_meta["block_pages"],
        "rpc_blocks_scanned": chosen_meta.get("rpc_blocks_scanned", 0),
        "rpc_transactions_seen": chosen_meta.get("rpc_transactions_seen", 0),
        "rpc_start_block": chosen_meta.get("rpc_start_block"),
        "rpc_end_block": chosen_meta.get("rpc_end_block"),
        "rpc_log_blocks_effective": chosen_meta.get("rpc_log_blocks_effective", 0),
        "rpc_log_blocks_scanned": chosen_meta.get("rpc_log_blocks_scanned", 0),
        "rpc_logs_seen": chosen_meta.get("rpc_logs_seen", 0),
        "rpc_log_addresses_seen": chosen_meta.get("rpc_log_addresses_seen", 0),
        "rpc_log_first_seen_tracked": chosen_meta.get("rpc_log_first_seen_tracked", 0),
        "rpc_log_start_block": chosen_meta.get("rpc_log_start_block"),
        "rpc_log_end_block": chosen_meta.get("rpc_log_end_block"),
        "rpc_log_today_start_block": chosen_meta.get("rpc_log_today_start_block"),
        "today_chain_day": chosen_meta.get("today_chain_day"),
        "today_utc_date": chosen_meta.get("today_utc_date"),
        "today_new_wallet_count": chosen_meta.get("today_new_wallet_count"),
        "today_new_power": chosen_meta.get("today_new_power"),
        "previous_day_total_power": chosen_meta.get("previous_day_total_power"),
        "daily_power_history_days": chosen_meta.get("daily_power_history_days", 0),
        "explorer_total_addresses": chosen_meta.get("explorer_total_addresses", 0),
        "network_total_power": chosen_meta.get("network_total_power", 0),
        "discovered_total_power": chosen_meta.get("discovered_total_power", 0),
        "network_total_burned_tokens": chosen_meta.get("network_total_burned_tokens", 0),
        "power_cache_ttl_seconds": chosen_meta.get("power_cache_ttl_seconds"),
        "power_cache_hits": chosen_meta.get("power_cache_hits", 0),
        "power_cache_refreshed": chosen_meta.get("power_cache_refreshed", 0),
        "power_cache_stale_fallbacks": chosen_meta.get("power_cache_stale_fallbacks", 0),
        "tier_label": chosen_meta["tier_label"],
        "history_json": str(json_path),
    }, chosen_meta)
    (site_dir / "build-meta.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"[done] latest coverage={chosen_meta['discovered_power_coverage']:.4%} "
        f"target={chosen_meta['coverage_target']:.2%} target_met={chosen_meta['target_met']}"
    )
    print(f"[done] site={site_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
