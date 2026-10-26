"""Build the isolated node preview using the production dashboard's presentation.

The static pages are deployment artifacts. ``public_payload`` is called for each
data publication and reuses the builder's calculation body, stopping before its
large HTML return expression. It neither writes the pages nor performs I/O.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import math
import re
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path
from typing import Any

import build_frontend_dashboard as dashboard
from node_preview.metrics import PUBLIC_HISTORY_NOTES
from node_preview.christmas import christmas_forecast

VIEW_SCHEMA = 1
RUNTIME = Path(__file__).with_name("live_preview.js")
OPENING_SOURCES = tuple(Path(__file__).with_name(name) for name in
                        ("intro_orbit_resonance.js", "rank_opening.js"))


def _presentation_function(function, names: tuple[str, ...]):
    """Adapt only the final HTML return; fail loudly if the upstream shape changes."""
    source = inspect.getsource(function)
    module = ast.parse(source)
    definition = module.body[0]
    if not isinstance(definition, ast.FunctionDef):
        raise RuntimeError("Dashboard presentation is no longer a function")
    last = definition.body[-1]
    if not isinstance(last, ast.Return) or not isinstance(last.value, ast.JoinedStr):
        raise RuntimeError("Dashboard HTML return changed; preview adapter needs review")
    # This source is an installed, version-controlled local module, not input data.
    # Keep the original global scope (formatters, helpers and constants) intact.
    definition.name = "_node_preview_view"
    definition.decorator_list = []
    definition.body[-1] = ast.Return(
        value=ast.Dict(
            keys=[ast.Constant(value=name) for name in names],
            values=[ast.Name(id=name, ctx=ast.Load()) for name in names],
        )
    )
    ast.fix_missing_locations(module)
    scope = dict(function.__globals__)
    exec(compile(module, function.__code__.co_filename, "exec"), scope)
    return scope[definition.name]


_desktop_view = _presentation_function(
    dashboard.build_html,
    ("metric_cards", "growth_cards", "timeline_rows", "marquee_html",
     "metric_trend_payload", "burn_estimate", "coverage_label", "generated_at",
     "statistics_window_label", "candidate_count", "positive_ratio", "uncovered_power", "warning_html"),
)
_mobile_view = _presentation_function(
    dashboard.build_mobile_html,
    ("key_cards", "growth_cards", "meta_rows", "metric_trend_payload", "warning_html"),
)


def _metric_views(markup: str) -> dict[str, dict[str, Any]]:
    cards = {}
    for match in re.finditer(r'<article\b([^>]*)>(.*?)</article>', markup, re.S):
        attributes, inner = match.groups()
        label = re.search(r'data-label="([^"]+)"', attributes)
        index = re.search(r'data-trend-index="(\d+)"', attributes)
        if not label or not index:
            raise RuntimeError("Dashboard metric card changed; missing stable label/index")
        cards[label.group(1)] = {"html": inner, "trend_index": int(index.group(1))}
    if not cards:
        raise RuntimeError("Dashboard metric cards are empty")
    return cards


# Public data is a product contract, not a copy of collector configuration. New
# database/diagnostic fields stay private unless explicitly added here.
PUBLIC_META_FIELDS = frozenset({
    "generated_at", "generated_at_local", "latest_block", "candidate_count",
    "positive_power_count", "discovered_total_power", "discovered_power_coverage",
    "network_total_power", "explorer_total_addresses", "public_rank_limit", "ranked_count",
    "network_current_price", "network_current_price_display", "network_highest_price",
    "network_highest_price_display", "network_total_burned_tokens", "network_total_burned_display",
    "network_total_circulation_tokens", "network_total_circulation_display",
    "statistics_window_label", "statistics_window_start_timestamp", "statistics_window_end_timestamp",
    "statistics_window_start_local", "statistics_window_end_local", "statistics_day_start_hour",
    "statistics_timezone", "statistics_timezone_label", "statistics_window_start_total_power",
    "statistics_window_end_total_power", "statistics_window_new_power",
    "statistics_window_new_candidate_address_count", "statistics_window_new_candidate_address_display",
    "statistics_window_active_wallet_address_count", "statistics_window_active_transactions_seen",
    "statistics_window_transaction_volume_wei", "statistics_window_transaction_volume_display",
    "statistics_window_burned_tokens", "statistics_window_burned_display",
    "power_required_per_mars_daily", "power_required_per_mars_daily_display",
    "emission_daily_total_tokens", "emission_daily_total_display", "emission_daily_miner_tokens",
    "emission_daily_miner_display", "emission_daily_node_tokens", "emission_daily_node_display",
    "emission_total_supply_cap_tokens", "emission_total_supply_cap_display", "emission_miner_share",
    "emission_node_share", "emission_genesis_timestamp",
    "period_statistics_schema",
    *{f"period_{days}d_{field}" for days in (7, 30) for field in (
        "label", "start_timestamp", "end_timestamp", "start_local", "end_local", "start_total_power",
        "end_total_power", "new_power", "new_candidate_address_count", "new_candidate_address_display",
        "burned_tokens", "burned_display", "complete")},
})
PUBLIC_TREND_KEYS = frozenset({
    "network_total_power", "daily_new_power", "total_wallets", "daily_active_addresses",
    "daily_new_addresses", "positive_power_addresses", "network_total_circulation", "total_burned",
    "daily_burned", "daily_emission", "power_per_coin", "one_yi_power_output", "daily_transaction_volume",
    "daily_transaction_count", "network_current_price", "period_7d_new_power", "period_7d_new_addresses",
    "period_7d_burned", "period_30d_new_power", "period_30d_new_addresses", "period_30d_burned",
})
PUBLIC_SOURCE_KEYS = frozenset({
    "network_total_power", "network_total_burned_tokens", "explorer_total_addresses",
    "network_total_circulation_tokens", "network_current_price", "network_highest_price",
})


def _public_meta(value: dict) -> dict:
    scalar = (str, int, float, bool, type(None))
    meta = {key: copy.deepcopy(item) for key, item in value.items()
            if key in PUBLIC_META_FIELDS and isinstance(item, scalar)}
    if meta.get("period_statistics_schema") == 2:
        # A display string is not evidence of an available numeric value. The
        # independent period refresh may recover power before burns/addresses.
        # Never let a retained formatted seed disguise a missing current field.
        for days in (7, 30):
            prefix = f"period_{days}d"
            if meta.get(f"{prefix}_burned_tokens") is None:
                meta[f"{prefix}_burned_display"] = "待补齐"
            if meta.get(f"{prefix}_new_candidate_address_count") is None:
                meta[f"{prefix}_new_candidate_address_display"] = "暂无可比采样"
    meta["paid_download"] = {"enabled": False}
    meta["public_rank_limit"] = 100
    raw = value.get("node_preview") or {}
    node = {key: raw[key] for key in (
        "mode", "source_block", "source_block_hash", "source_block_timestamp", "generated_at",
        "version", "known_addresses", "target_timestamp") if isinstance(raw.get(key), scalar) and key in raw}
    state = raw.get("state")
    node["state"] = state if state in {"ready", "frozen"} else "waiting"
    meta["node_preview"] = node
    daily = value.get("preview_daily_status") or {}
    meta["preview_daily_status"] = {key: daily[key] for key in (
        "state", "frozen_end_timestamp", "expected_end_timestamp") if isinstance(daily.get(key), scalar) and key in daily}
    sources = {}
    for key in PUBLIC_SOURCE_KEYS:
        entry = (value.get("preview_metric_sources") or {}).get(key)
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        label = "node" if source in {"local_rpc_power_contract", "node"} else "official" if "explorer.marschain.net" in source or source == "official" else "snapshot"
        status = str(entry.get("status") or "unavailable")
        sources[key] = {"source": label, "status": "ok" if status == "ok" else "stale" if entry.get("fetched_at") else "unavailable"}
        for field in ("fetched_at", "block"):
            if isinstance(entry.get(field), (int, float)):
                sources[key][field] = entry[field]
    meta["preview_metric_sources"] = sources
    trends = {}
    for key in PUBLIC_TREND_KEYS:
        entry = (value.get("metric_trends") or {}).get(key)
        points = entry.get("values") if isinstance(entry, dict) else entry
        if not isinstance(points, list):
            continue
        clean = []
        for point in points[-120:]:
            item = point.get("value") if isinstance(point, dict) else point
            if item is None or isinstance(item, bool):
                continue
            try:
                if not math.isfinite(float(item)):
                    continue
            except (ValueError, TypeError):
                continue
            label = point.get("label", "") if isinstance(point, dict) else ""
            clean.append({"label": str(label)[:80], "value": item})
        trends[key] = {"values": clean}
        if isinstance(entry, dict):
            if isinstance(entry.get("history_note"), str) and entry["history_note"] in PUBLIC_HISTORY_NOTES:
                trends[key]["history_note"] = entry["history_note"]
            if isinstance(entry.get("exact_cutoff"), bool):
                trends[key]["exact_cutoff"] = entry["exact_cutoff"]
            if entry.get("timing_status") in {"exact", "near_cutoff", "sampled", "near_cutoff_sample"}:
                trends[key]["timing_status"] = entry["timing_status"]
    meta["metric_trends"] = trends
    return meta


def _beijing_stamp(stamp: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(stamp), timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _window_note(meta: dict, prefix: str) -> str:
    start = _beijing_stamp(meta.get(f"{prefix}_start_timestamp"))
    end = _beijing_stamp(meta.get(f"{prefix}_end_timestamp"))
    if start and end:
        return f"{start} 至 {end}"
    return f"截至 {end}" if end else "统计时间待核验"


def _period_state(meta: dict, days: int) -> str:
    end = meta.get(f"period_{days}d_end_timestamp")
    if not _beijing_stamp(end):
        return "统计时间待核验"
    daily_end = meta.get("statistics_window_end_timestamp")
    try:
        if daily_end and int(end) < int(daily_end):
            return "保留上次统计"
    except (TypeError, ValueError):
        return "统计时间待核验"
    return "" if meta.get(f"period_{days}d_complete") is not False else "部分数据待补齐"


def _period_note(meta: dict, days: int) -> str:
    return _window_note(meta, f"period_{days}d") + "（北京时间）"


def _period_missing_copy(markup: str, meta: dict, days: int | None = None) -> str:
    """Apply missingness per field, including the compact secondary figures."""
    if meta.get("period_statistics_schema") != 2:
        return markup
    for period in ((days,) if days is not None else (7, 30)):
        prefix = f"period_{period}d"
        fields = (
            ("new_power", ("新增算力",), "待补齐"),
            ("new_candidate_address_count", ("新增地址",), "暂无可比采样"),
            ("burned_tokens", ("销毁数量", "销毁"), "待补齐"),
        )
        for field, labels, replacement in fields:
            if meta.get(f"{prefix}_{field}") is not None:
                continue
            all_labels = [f"{period} 天{label}" for label in labels]
            all_labels += [f"{period}天{label}" for label in labels]
            if days is not None:
                all_labels += list(labels)
            names = "|".join(re.escape(label) for label in all_labels)
            # Desktop lines, mobile secondary rows and timeline labels.
            markup = re.sub(
                rf'(<span>(?:{names})</span>\s*<(?:strong|b)>)[^<]*(</(?:strong|b)>)',
                lambda match: match[1] + replacement + match[2], markup,
            )
            # The footer marquee keeps its label and number in one span.
            markup = re.sub(
                rf'(<span>(?:{names})\s*<b>)[^<]*(</b>)',
                lambda match: match[1] + replacement + match[2], markup,
            )
    return markup


def _annotate_period_cards(markup: str, meta: dict) -> str:
    def card(match):
        opening, inner, closing = match.groups()
        if "data-node-period-cutoff" in inner:
            return match.group(0)
        period = re.search(r'(?:data-label="(7|30) 天|<h3>(7|30) 天新增</h3>)', opening + inner)
        if not period:
            return match.group(0)
        days = int(next(group for group in period.groups() if group))
        inner = _period_missing_copy(inner, meta, days)
        state = _period_state(meta, days)
        badge = (f'<div class="node-period-state" data-node-period-state="{days}"'
                 + ("" if state else " hidden") + f'>{escape(state)}</div>')
        note = f'<small class="node-period-cutoff" data-node-period-cutoff="{days}">{escape(_period_note(meta, days))}</small>'
        return opening + badge + inner + note + closing
    return re.sub(r'(<article\b[^>]*>)(.*?)(</article>)', card, markup, flags=re.S)


_CARD_DAILY_FIELDS = {
    "全网总算力": ("每日新增算力", ("statistics_window_new_power",)),
    "全网流通量": ("每日销毁量", ("statistics_window_burned_tokens",)),
    "钱包地址": ("每日新增 / 活跃地址", ("statistics_window_new_candidate_address_count", "statistics_window_active_wallet_address_count")),
    "每日交易币量": ("每日交易量 / 笔数", ("statistics_window_transaction_volume_wei", "statistics_window_active_transactions_seen")),
}

# These are the builder's stable lookup labels, not the visible card titles.
# Both the static page and each latest.json update use this same adapter.
_CORE_CARD_LABELS = frozenset({
    "全网总算力", "全网流通量", "当前价格", "每日产币量", "钱包地址", "每日交易币量",
})
_DAILY_NAMES = {
    "统计日新增总算力": "每日新增算力",
    "统计日新增算力": "每日新增算力",
    "统计日新增地址数量": "每日新增地址",
    "统计日新增地址": "每日新增地址",
    "统计日活跃地址数量": "每日活跃地址",
    "统计日活跃地址": "每日活跃地址",
    "日销毁币量": "每日销毁量",
    "每日交易币量": "每日交易量",
    "每日产币量": "预计日产量",
}
_DAILY_TREND_LABELS = {
    "daily_new_power": "每日新增算力",
    "daily_new_addresses": "每日新增地址",
    "daily_burned": "每日销毁量",
    "daily_transaction_volume": "每日交易量",
    "daily_transaction_count": "每日交易笔数",
    "daily_active_addresses": "每日活跃地址",
    "daily_emission": "预计日产量",
}


def _daily_text(text: str) -> str:
    for old, new in _DAILY_NAMES.items():
        text = text.replace(old, new)
    return text


def _daily_markup(markup: str) -> str:
    """Change visible wording without changing data-label, keys or attributes."""
    return re.sub(r"(?<=>)[^<>]+(?=<)", lambda match: _daily_text(match.group(0)), markup)


def _daily_trend_copy(trends: list[dict], meta: dict | None = None) -> list[dict]:
    """Relabel the six core popup series; dates, numbers and keys stay intact."""
    for trend in trends:
        if str(trend.get("key") or "").startswith("period_"):
            if meta and meta.get("period_statistics_schema") == 2:
                match = re.fullmatch(r"period_(7|30)d_new_power", str(trend.get("key")))
                if match:
                    days = int(match[1])
                    prefix = f"period_{days}d"
                    if meta.get(f"{prefix}_new_power") is None:
                        trend["value"] = "待补齐"
                    trend["note"] = ("新增算力、新增地址与销毁数量按日期联动展示。"
                                     "当前窗口缺失项单独标示；历史曲线按各自实际统计日期展示，缺失不补值。")
            continue
        for name in ("label", "note"):
            if isinstance(trend.get(name), str):
                trend[name] = _daily_text(trend[name])
        if trend.get("key") == "total_wallets":
            trend["note"] = trend.get("note", "").replace(
                "总钱包、新增地址、活跃地址、正算力地址按日期联动展示。",
                "总钱包、每日新增地址、每日活跃地址、正算力地址按日期联动展示。", 1,
            )
        for series in trend.get("series") or []:
            if series.get("key") in _DAILY_TREND_LABELS:
                series["label"] = _DAILY_TREND_LABELS[series["key"]]
    return trends


def _remove_card_chart(inner: str) -> str:
    """Remove only the outer mini chart, including its nested div panels."""
    chart = re.search(r'<div\b[^>]*class="(?:metric-trend(?:\s[^"]*)?|power-mini-pair)"[^>]*>', inner)
    if chart is None:
        return inner
    depth = 0
    for tag in re.finditer(r'</?div\b[^>]*>', inner[chart.start():]):
        depth += -1 if tag.group(0).startswith("</") else 1
        if depth == 0:
            return inner[:chart.start()] + inner[chart.start() + tag.end():]
    raise RuntimeError("Core metric mini chart has an unclosed div")


def _daily_state(meta: dict, fields: tuple[str, ...]) -> str:
    daily = meta.get("preview_daily_status") or {}
    try:
        if int(daily.get("expected_end_timestamp") or 0) > int(meta.get("statistics_window_end_timestamp") or 0):
            return "保留上次日统计"
    except (TypeError, ValueError):
        pass
    return "部分日统计待补齐" if any(meta.get(field) is None for field in fields) else ""


def _annotate_metric_cards(markup: str, meta: dict) -> str:
    def card(match):
        opening, inner, closing = match.groups()
        label = re.search(r'data-label="([^"]+)"', opening)
        if not label:
            return match.group(0)
        name = label.group(1)
        if name not in _CORE_CARD_LABELS:
            return match.group(0)
        inner = _daily_markup(_remove_card_chart(inner))
        inner = inner.replace(">新增地址<", ">每日新增地址<").replace(">活跃地址<", ">每日活跃地址<")
        inner = inner.replace("08:00统计窗口内交易 value 汇总", "统计区间内交易币量 · MARS")
        # Fixed-window values share the actual interval below; avoid repeating
        # several different descriptions of the same 08:00 close in each row.
        inner = re.sub(
            r'<em>(?:北京时间 08:00 统计日新增|08:00 统计日新增|同一统计窗口内活跃|'
            r'08:00 统计日口径|08:00 统计窗口内交易数量)</em>', "", inner,
        )
        if "data-core-compact" not in opening:
            opening = opening[:-1] + " data-core-compact>"
        opening = opening.replace('aria-label="查看每日交易币量趋势"', 'aria-label="查看每日交易量趋势"')
        opening = opening.replace('aria-label="查看每日产币量趋势"', 'aria-label="查看预计日产量趋势"')
        if name in _CARD_DAILY_FIELDS:
            _caption, fields = _CARD_DAILY_FIELDS[name]
            state = _daily_state(meta, fields)
            note = ('<div class="node-daily-window" data-node-daily-window>'
                    + '<span>统计区间 · 北京时间</span>'
                    + f'<span>{escape(_window_note(meta, "statistics_window"))}</span>'
                    + (f'<em data-node-daily-state>{escape(state)}</em>' if state else '') + '</div>')
            inner += note
        inner += '<div class="node-card-trend-entry" aria-hidden="true"><span>查看趋势</span><i>↗</i></div>'
        return opening + inner + closing
    return re.sub(r'(<article\b[^>]*>)(.*?)(</article>)', card, markup, flags=re.S)


def _address_missing_copy(markup: str, meta: dict) -> str:
    count = meta.get("statistics_window_new_candidate_address_count")
    display = "暂无可比快照" if count is None else meta.get("statistics_window_new_candidate_address_display")
    if display:
        # Sampling qualifications remain in public meta and data notes. The
        # card number is concise and never fabricates a missing comparison.
        note = escape(re.sub(r"（近截点采样）$", "", str(display)))
        for pattern in (
            r'(<span>(?:统计日|每日)新增地址</span>\s*<(?:strong|b)>)[^<]*(</(?:strong|b)>)',
            r'(<span>新增地址</span>\s*<strong>)[^<]*(</strong>)',
            r'(<span>(?:统计日|每日)新增地址\s*<b>)[^<]*(</b>)',
        ):
            markup = re.sub(pattern, lambda match: match[1] + note + match[2], markup)
    return markup


def public_payload(payload: dict) -> dict:
    """Return meta + at most 100 public rank rows + compact presentation data."""
    meta = _public_meta(payload.get("meta") or {})
    # Rows are allowlisted; no incidental database column can leak to the browser.
    rows = []
    for index, row in enumerate((payload.get("rows") or [])[:100], start=1):
        rows.append({
            "rank": row.get("rank", index),
            "address": str(row.get("address") or ""),
            "power": row.get("power"),
        })
    public = {"meta": meta, "rows": rows}
    desktop = _desktop_view(public)
    mobile = _mobile_view(public)
    view = {
        "schema": VIEW_SCHEMA,
        "desktop": {
            "metrics": _metric_views(_annotate_metric_cards(_address_missing_copy(desktop["metric_cards"], meta), meta)),
            "growth": _annotate_period_cards(desktop["growth_cards"], meta),
            "timeline": _daily_markup(_period_missing_copy(_address_missing_copy(desktop["timeline_rows"], meta), meta)),
            "marquee": _daily_markup(_period_missing_copy(_address_missing_copy(desktop["marquee_html"], meta), meta)),
            "trends": _daily_trend_copy(json.loads(desktop["metric_trend_payload"]), meta),
            "warning": desktop["warning_html"],
        },
        "mobile": {
            "metrics": _metric_views(_annotate_metric_cards(_annotate_period_cards(_address_missing_copy(mobile["key_cards"] + mobile["growth_cards"], meta), meta), meta)),
            "timeline": _daily_markup(_period_missing_copy(_address_missing_copy(mobile["meta_rows"], meta), meta)),
            "trends": _daily_trend_copy(json.loads(mobile["metric_trend_payload"]), meta),
            "warning": mobile["warning_html"],
        },
        "burn": desktop["burn_estimate"],
        "christmas": dashboard._christmas_content(christmas_forecast(meta)),
        "coverage": desktop["coverage_label"],
        "candidate_count": dashboard._fmt_chinese_number(desktop["candidate_count"]),
        "positive_ratio": dashboard._fmt_percent(desktop["positive_ratio"]),
        "uncovered_power": dashboard._fmt_power(desktop["uncovered_power"]),
        "generated_at": desktop["generated_at"],
        "statistics_window_label": desktop["statistics_window_label"],
    }
    # The preview has minute collection, not the production daily-fetch label.
    for device in ("desktop", "mobile"):
        view[device]["timeline"] = view[device]["timeline"].replace(
            "每日 08:00 官方快照", "每分钟处理已确认新区块"
        ).replace("北京时间 08:00</b>", "北京时间 08:00 日结</b>")
        view[device]["warning"] = view[device]["warning"].replace("本轮覆盖率", "本轮算力覆盖率")
        card = view[device]["metrics"].get("全网总算力")
        if card:
            card["html"] = _replace_power_note(card["html"], _power_source_note(meta))
    view["desktop"]["marquee"] = view["desktop"]["marquee"].replace("<span>覆盖率<b", "<span>算力覆盖率<b")
    public["view"] = view
    return public


def _power_source_note(meta: dict) -> str:
    source = (meta.get("preview_metric_sources") or {}).get("network_total_power") or {}
    if source.get("source") in {"local_rpc_power_contract", "node"}:
        label = "自建节点 POWER 合约"
        return label + (" · 最近成功值" if source.get("status") != "ok" else "")
    if source.get("source") == "official" or "explorer.marschain.net" in str(source.get("source")):
        return "官方浏览器 POWER 统计（回退）"
    return "官网已发布算力快照（校准中）"


def _replace_power_note(markup: str, note: str) -> str:
    replaced, count = re.subn(r'<small>(?:区块浏览器公开统计|公开接口统计)</small>',
                              "<small>" + escape(note) + "</small>", markup)
    if count != 1:
        raise RuntimeError("Total power metric source marker changed")
    return replaced


def _once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Preview adapter expected one marker ({old[:65]!r}), got {count}")
    return text.replace(old, new, 1)


_PREVIEW_STYLE = """
<style>
.node-preview-info{position:relative;z-index:10;margin:10px auto 16px;padding:12px 16px;
  max-width:1380px;border:1px solid rgba(86,239,255,.3);border-radius:14px;
  color:#bcd3e9;background:rgba(9,23,43,.95);font:12px/1.7 var(--font,sans-serif)}
.node-preview-info strong{color:#81f5b2;margin-right:10px}
.node-preview-info p{margin:2px 0}.node-preview-info.is-error{border-color:#f2a763;color:#ffd7a1}
.node-preview-info.is-stale{border-color:#f2a763}
.node-preview-info a{color:#56efff}
.node-data-notes{margin:0 auto 16px;max-width:1380px;padding:0 16px;color:#90abc4;font:12px/1.7 var(--font,sans-serif)}
.node-data-notes summary{cursor:pointer;color:#afc9df;width:max-content}.node-data-notes p{margin:7px 0}
.node-period-cutoff{display:block!important;font-size:11px!important;line-height:1.65!important;color:#b0bed1!important;margin-top:12px!important;overflow-wrap:anywhere}
.node-period-state{display:block;width:fit-content;max-width:100%;margin:0 0 10px;padding:5px 9px;border:1px solid rgba(255,190,100,.35);border-radius:8px;background:rgba(255,169,65,.09);color:#ffd39a;font:600 11px/1.5 var(--font,sans-serif);letter-spacing:0;overflow-wrap:anywhere}
.node-period-state[hidden]{display:none!important}
.node-daily-window{margin:12px 0 0;padding:9px 0 0;border-top:1px solid rgba(143,183,216,.15);font:11px/1.65 var(--font,sans-serif);letter-spacing:0;color:#b0bed1}
.node-daily-window span{display:block!important;margin:0!important;font:inherit!important;letter-spacing:0!important;color:inherit!important}
.node-daily-window span:first-child{font-size:10px!important;color:#91a9bf!important}
.node-daily-window em{display:block;margin-top:4px;font-size:10px;font-style:normal;color:#ffd39a}
.node-rank-actions{display:flex;align-items:center;gap:12px;margin:10px 0 2px;font:11px/1.5 var(--font,sans-serif)}
.node-rank-actions a,.node-rank-actions button{color:#9dcbd8;background:none;border:0;padding:2px 0;font:inherit;cursor:pointer;text-decoration:none}
.node-rank-actions a:hover,.node-rank-actions button:hover{color:#68f6e8}
.m-card[data-label="全网总算力"] small{white-space:normal;line-height:1.6}
.radar .mini b{white-space:nowrap;font-size:clamp(13px,1.15vw,16px);letter-spacing:-.03em;word-break:normal}
.trend-stat-row strong{white-space:nowrap;word-break:normal;font-size:clamp(11px,1.2vw,16px);letter-spacing:-.035em}
.node-preview-page-buttons{display:contents}
@media(max-width:760px){.node-preview-info{margin:8px 0 14px;padding:10px 12px}}

.m-card .metric-secondary-list{display:grid;gap:11px;margin-top:12px}
.m-card .metric-secondary-row,.m-card .metric-secondary{display:grid;gap:3px}
.m-card .metric-secondary-row span,.m-card .metric-secondary span{font-size:11px;line-height:1.4;color:#b0c7dc}
.m-card .metric-secondary-row strong,.m-card .metric-secondary strong{display:block;font-size:15px;line-height:1.3;font-weight:850;margin:0;color:#eef8ff}
.m-card .metric-secondary-row em,.m-card .metric-secondary em{display:block;font-size:10px;line-height:1.5;font-style:normal;color:#94abc1}
.m-card .metric-trend{margin-top:17px}

/* Core cards show the figures; the existing full charts open on activation. */
.metric[data-core-compact]{min-height:0;padding:19px 20px}
.metric[data-core-compact]>b{margin-top:16px;font-size:clamp(27px,2.35vw,37px);line-height:1.1}
.metric[data-core-compact] .metric-secondary-list{gap:6px;margin-top:13px}
.metric[data-core-compact] .metric-secondary,.metric[data-core-compact] .metric-secondary-row{padding:8px 10px;border-radius:10px;gap:3px 9px}
.metric[data-core-compact] .metric-secondary{margin-top:13px}
.metric[data-core-compact] .metric-secondary-row em,.metric[data-core-compact] .metric-secondary em{font-size:10px;line-height:1.4}
.metric[data-core-compact] .price-stack{gap:6px;margin-top:13px}
.metric[data-core-compact] .price-stack span{padding:8px 10px;border-radius:10px}
[data-core-compact] .node-daily-window{margin-top:11px;padding-top:8px}
.node-card-trend-entry{position:relative;z-index:1;display:flex;align-items:center;justify-content:flex-end;gap:6px;margin-top:auto;padding-top:14px;color:#8eb7cc;font:11px/1.4 var(--font,sans-serif)}
.node-card-trend-entry span{display:inline!important;margin:0!important;padding:0!important;border:0!important;background:none!important;max-width:none!important;font:inherit!important;color:inherit!important;opacity:1!important}
.node-card-trend-entry i{font-size:15px;line-height:1;font-style:normal;transition:transform .18s ease}
[data-core-compact]:hover .node-card-trend-entry{color:#83e5df}
[data-core-compact]:hover .node-card-trend-entry i{transform:translate(2px,-2px)}
.node-core-daily-caption{color:#9bb2c8!important;font-size:12px!important;line-height:1.65!important}
.m-section-head .node-core-daily-caption{max-width:180px;font-size:11px!important}
.m-card[data-core-compact]{min-height:0;padding:14px 12px}
.m-card[data-core-compact]>b{margin-top:11px;font-size:clamp(20px,5.2vw,25px);line-height:1.1;white-space:nowrap}
.m-card[data-core-compact] .metric-secondary-list{gap:6px;margin-top:11px}
.m-card[data-core-compact] .metric-secondary,.m-card[data-core-compact] .metric-secondary-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:3px 5px;padding:7px 0}
.m-card[data-core-compact] .metric-secondary{margin-top:11px}
.m-card[data-core-compact] .metric-secondary-row span,.m-card[data-core-compact] .metric-secondary span{font-size:10px;line-height:1.5}
.m-card[data-core-compact] .metric-secondary-row strong,.m-card[data-core-compact] .metric-secondary strong{font-size:13px;line-height:1.35;text-align:right;white-space:nowrap}
.m-card[data-core-compact] .metric-secondary-row em,.m-card[data-core-compact] .metric-secondary em{grid-column:1/-1;font-size:9px;line-height:1.5}
.m-card[data-core-compact] .node-daily-window{font-size:10px;line-height:1.65}
.m-card[data-core-compact] .node-card-trend-entry{padding-top:12px;font-size:10px}
@media(max-width:620px){
 .metrics{grid-template-columns:minmax(0,1fr)}
 .metric[data-core-compact]>span{max-width:calc(100% - 40px);white-space:nowrap}
 .metric[data-core-compact]>b{font-size:30px}
}
@media(prefers-reduced-motion:reduce){.node-card-trend-entry i{transition:none}}

/* Quiet, legible trend preview. */
.trend-modal{justify-content:center!important;align-items:center!important;place-items:center!important}
.trend-panel{width:min(760px,calc(100vw - 28px))!important}
.trend-panel{background:#0c1524!important;border-color:#26364d!important;border-radius:18px!important}
.trend-title h3{font-size:25px!important}.trend-title p{font-size:12px!important;line-height:1.7!important;color:#8d9cb0!important;max-width:720px}
.trend-head{padding-bottom:8px!important}.trend-controls{margin-bottom:10px!important}
.trend-legend{display:flex!important;gap:7px;flex-wrap:wrap;padding:0 24px 12px}
.trend-legend button{border:1px solid #29384d;background:transparent;color:#96a7bd;padding:7px 12px;border-radius:7px;cursor:pointer;font-size:12px}
.trend-legend button.is-active{color:#65d8df;border-color:#40828e;background:#142d38}
.trend-chart{position:relative!important;background:#0a1220!important;border-color:#243348!important;border-radius:10px!important;overflow:visible!important}
.trend-chart svg{height:auto!important;min-height:220px;display:block;width:100%}
.trend-chart .line{stroke:#5dcdd8!important;stroke-width:2!important;filter:none!important;fill:none!important}
.trend-chart .bar{fill:#438b9d!important}.trend-chart .cursor-dot{fill:#8de0df!important;filter:none!important}
.trend-chart .cursor-line{stroke:#778da8!important;stroke-width:1!important;stroke-dasharray:3 5!important}
.trend-chart .date-label{font-size:12px!important;fill:#8c9db4!important;font-weight:400!important}
.trend-axis-title{padding:12px 16px 0;color:#9badc2;font-size:12px}
.trend-tooltip{position:absolute;right:16px;top:36px;background:#1c2b40;border:1px solid #445a74;color:#e3edf8;padding:8px 12px;border-radius:7px;font-size:12px;pointer-events:none;box-shadow:0 4px 16px #0005}
.trend-stats{gap:9px!important}.trend-stat{background:#111e2f!important;border-color:#243348!important;border-radius:9px!important}.trend-stat b{font-size:21px!important}
.trend-foot{color:#8596ad!important;line-height:1.7}
@media(max-width:700px){.trend-modal{align-items:center!important;place-items:center!important;padding:12px!important}.trend-panel{width:100%!important;max-height:92vh!important}.trend-head{padding:18px 16px 8px!important}.trend-title h3{font-size:22px!important}.trend-legend{padding:0 16px 10px}.trend-chart{margin-left:12px!important;margin-right:12px!important}.trend-chart svg{min-height:235px}.trend-stats{grid-template-columns:repeat(2,minmax(0,1fr))!important}.trend-tooltip{right:6px;top:30px;font-size:11px}.trend-chart .date-label{font-size:15px!important}}


.power-mini-pair{margin-top:18px;display:grid;gap:10px}
.power-mini{border-top:1px solid #24364b;padding-top:9px}
.power-mini>span{font-size:11px!important;color:#9cb1c9!important;display:block;line-height:1.5}
.power-mini svg{display:block;width:100%;height:50px;overflow:visible}
.power-mini svg path{fill:none;stroke:#66d0dc;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.power-mini svg rect{fill:#79baad}
.trend-chart.power-pair-chart{background:transparent!important;border:0!important;padding:0!important;margin-top:4px!important}
.power-pair-panel{border:1px solid #243348;border-radius:10px;background:#0a1220;overflow:hidden}
.power-pair-panel+.power-pair-panel{margin-top:10px}
.power-pair-panel h4{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0;padding:12px 14px 0;color:#c1d5ea;font-size:13px;font-weight:600}
.power-pair-panel h4 small{font-weight:400;font-size:10px;color:#8a9bb0}
.power-pair-chart svg{min-height:0!important;height:auto!important;width:100%!important}
.power-pair-panel .bar{fill:#79baad!important}
.is-power-pair .trend-stats{grid-template-columns:1fr!important}
.is-power-pair .trend-stat:not(:first-child){display:none}
.is-power-pair .trend-stat-row{display:flex;align-items:center;justify-content:space-between;gap:10px;line-height:1.8}
.is-power-pair .trend-stat-row i{font-size:12px;font-style:normal;color:#99adc5}
.is-power-pair .trend-stat-row strong{font-size:19px;font-variant-numeric:tabular-nums;color:#e1edf8}
.is-power-pair .trend-stat b{display:block}
.is-power-pair .trend-tooltip{max-width:90%;white-space:normal;top:40px;font-size:11px;line-height:1.6}
@media(max-width:700px){.is-power-pair .trend-head{padding-top:14px!important}.is-power-pair .trend-title p{margin-bottom:4px!important}.is-power-pair .trend-stat-row strong{font-size:17px}}


.power-mini>span{display:flex!important;align-items:center;justify-content:space-between;gap:8px}
.power-mini>span em{font-size:10px;color:#7488a2;font-style:normal;font-weight:400}
.power-mini svg circle{fill:#66d0dc}
.power-pair-panel .group-empty{padding:16px;margin:0;color:#8093ac;font-size:12px}
.is-power-pair .trend-panel{overscroll-behavior:contain}

.m-section{scroll-margin-top:130px}

.christmas-forecast{margin-top:24px;padding:26px;border:1px solid #35574f;border-radius:18px;background:linear-gradient(145deg,#10242b,#101b2c 60%,#1e2332);color:#dce8f6;scroll-margin-top:130px}
.christmas-head{display:flex;justify-content:space-between;align-items:center;gap:14px}
.christmas-kicker{color:#a7ccb6;font-size:11px;letter-spacing:.12em;font-weight:700}
.christmas-head h3{margin:8px 0 0;font-size:26px;line-height:1.25;letter-spacing:-.03em}
.christmas-date{font-size:11px;white-space:nowrap;color:#c7bda1;background:#c8b4780c;border:1px solid #716957;padding:7px 9px;border-radius:7px}
.christmas-intro{color:#99afc3;font-size:13px;line-height:1.7;margin:14px 0 18px}
.christmas-components{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:center;background:#0c1823;border-radius:10px;padding:16px}
.christmas-components span,.christmas-results span,.christmas-total>span{display:block;color:#9bb0c4;font-size:12px;margin-bottom:8px}
.christmas-components b{display:block;font-size:19px;font-variant-numeric:tabular-nums}
.christmas-components>i{font-size:22px;color:#7f9b91;font-style:normal}
.christmas-cycle{display:grid;gap:10px;padding:15px 0 0;font-size:12px;color:#9bb0c4}.christmas-cycle>div{display:flex;justify-content:space-between;gap:10px;align-items:baseline}.christmas-cycle strong{color:#c4d6df;font-size:15px;font-variant-numeric:tabular-nums;text-align:right}
.christmas-forecast em{font-size:10px;font-weight:400;font-style:normal;color:#8399af}
.christmas-total{padding:22px 0;border-bottom:1px solid #2b3e48}
.christmas-total>b{display:block;color:#c7e9db;font-size:34px;font-variant-numeric:tabular-nums;letter-spacing:-.035em}
.christmas-total>small{display:block;color:#7f95a9;font-size:11px;margin-top:8px}
.christmas-results{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:20px 0}
.christmas-results>div{border-left:2px solid #4b8774;padding-left:14px}
.christmas-results b{display:block;color:#e4e6ce;font-size:25px;font-variant-numeric:tabular-nums}
.christmas-model{font-size:11px;color:#8ca1b5;line-height:1.8}.christmas-model strong{color:#b5c9da;font-weight:500}
.christmas-method{border-top:1px solid #2b3e48;padding-top:12px;margin-top:12px;color:#869db1;font-size:11px;line-height:1.8}
.christmas-method summary{cursor:pointer;color:#a9c0c9}.christmas-method p{margin:7px 0}.christmas-empty{color:#9db3c6;font-size:13px;line-height:1.8}
.m-shell .christmas-forecast{padding:20px 16px}.m-shell .christmas-head h3{font-size:23px}.m-shell .christmas-date{font-size:10px;padding:6px}.m-shell .christmas-components{padding:12px;gap:7px}.m-shell .christmas-components b{font-size:17px}.m-shell .christmas-total>b{font-size:31px}.m-shell .christmas-results b{font-size:22px}.m-shell .christmas-results>div{padding-left:9px}
@media(max-width:400px){.christmas-head{align-items:flex-start}.christmas-date{max-width:88px;white-space:normal;line-height:1.5}.christmas-results{gap:8px}.christmas-results span{font-size:11px}.christmas-results b{font-size:20px}}


.forecast-member-note{font-size:12px;color:#9eb3c5;line-height:1.8;margin:14px 0}.forecast-facts{margin:12px 0;padding:12px;background:#0c1823;border-radius:9px}.forecast-facts>div{display:flex;justify-content:space-between;gap:14px;align-items:baseline;padding:5px 0}.forecast-facts dt{color:#8ea6ba}.forecast-facts dd{margin:0;text-align:right;color:#c5d6df;font-variant-numeric:tabular-nums}.burn-member-method{font-size:11px}.christmas-total>small{line-height:1.7}.christmas-forecast .forecast-member-note{margin-bottom:10px}.m-shell .forecast-facts>div{gap:10px;flex-wrap:wrap}.m-shell .forecast-facts dd{margin-left:auto}

.christmas-stale{color:#ceb584;font-size:11px;line-height:1.7}
.m-burn-sample{grid-column:1/-1}.m-burn-sample b{color:#c7e9db}.burn-method-note{font-size:11px;color:#8ca1b5;line-height:1.7;margin:12px 0 0}
</style>"""

_PREVIEW_BANNER = """
<aside class="node-preview-info" data-node-preview aria-live="polite">
 <p><strong>排行榜 · 预览版</strong><span data-preview-state>正在读取最新数据…</span></p>
 <p data-preview-data-time>正在读取数据版本…</p>
</aside>"""

_DATA_NOTES = """
<details class="node-data-notes" data-node-data-notes>
 <summary>数据说明</summary>
 <p data-preview-scope>榜单展示已发现地址中的算力前 100 名。算力覆盖率不代表地址名单完整。</p>
 <p data-preview-frequency>榜单约每分钟刷新，已确认交易可能稍后显示；每日统计按北京时间 08:00 日结。</p>
 <p data-preview-daily></p>
 <p data-preview-sources>算力和累计销毁来自自建节点；价格、全网地址总数、流通量来自官方浏览器。</p>
 <p data-preview-sampling>官方价格、地址的日历史取自北京时间 08:00 附近采样；新增地址按各统计区间起止采样差额计算，可能与精确截点值存在差异。缺少可比地址采样时，仅该项暂缺，算力和销毁独立更新。</p>
 <p data-preview-source-notes hidden></p>
</details>"""


def _adapt_html(html: str, *, mobile: bool, power_note: str, public_meta: dict, production: bool = False) -> str:
    # Original pagination captures the first cards forever. Use one runtime-owned
    # paginator so replacements preserve the reader's current page.
    if mobile:
        start, end = "const mobileRankList =", "const setupMobilePaidDownloadPanel ="
    else:
        start, end = "const rankGrid =", "const setupPaidDownloadPanel ="
    if html.count(start) != 1 or html.count(end) != 1:
        raise RuntimeError("Dashboard pagination markers changed")
    left = html.index(start)
    right = html.index(end, left)
    html = html[:left] + "/* Node preview owns the live rank paginator. */\n" + html[right:]

    # The price collector and publisher own a coherent latest.json version.
    # Disable the independent price.json interval in this isolated preview.
    html = _once(html, dashboard.LIVE_PRICE_JS, "/* Price is supplied by latest.json. */")
    # Read calculator rate after a live update; keep the visitor's input intact.
    html = _once(html, "  const rate = Number(panel.dataset.burnRate || 0);", "")
    html = _once(html, "    const power = parsePower(input.value);", "    const rate = Number(panel.dataset.burnRate || 0);\n    const power = parsePower(input.value);")
    # Keep the existing chart popup and period selection; replace its model only.
    trend_marker = "  const periods = ["
    trend_update = """
  window.addEventListener('mars-preview-data', (event) => {
    if (!Array.isArray(event.detail && event.detail.trends)) return;
    metricTrends = event.detail.trends;
    if (activeMetric) {
      activeMetric = metricTrends.find((entry) => entry.key === activeMetric.key) || null;
      if (activeMetric && modal && !modal.hidden) {
        modal.querySelector('[data-trend-note]').textContent = activeMetric.note || '';
        redrawVisibleChart();
      }
    }
  });
"""
    html = _once(html, trend_marker, trend_update + trend_marker)
    # Avoid analytics and any existing production account bootstrap in the preview.
    analytics = dashboard.build_analytics_head()
    if analytics and not production:
        html = _once(html, analytics, "")
    html = _once(html, "</head>", _PREVIEW_STYLE + "\n</head>")
    # JSON script bodies need case-insensitive HTML parser safety, including
    # provider error strings containing an unusual </ScRiPt> spelling.
    def safe_json(match):
        content = json.loads(match.group(2))
        if match.group(1).startswith('<script id="metricTrendData"'):
            content = _daily_trend_copy(content, public_meta)
        safe = json.dumps(content, ensure_ascii=False).replace("<", "\\u003c")
        return match.group(1) + safe + match.group(3)
    html = re.sub(r'(<script id="(?:rankData|metricTrendData)" type="application/json">)(.*?)(</script>)', safe_json, html, flags=re.S)
    shell_marker = '<div class="m-shell">' if mobile else '<div class="shell">'
    banner = _PREVIEW_BANNER.replace("排行榜 · 预览版", "排行榜 · 每分钟更新") if production else _PREVIEW_BANNER
    html = _once(html, shell_marker, shell_marker + banner + _DATA_NOTES)
    # Apply copy changes only to visible markup. The production language bundle
    # and JSON source payload remain intact for compatibility.
    markup, scripts = html.split('<script id="rankData"', 1)
    markup = markup.replace("扫描覆盖率", "算力覆盖率")
    markup = markup.replace("<span class=\"btn hot\">覆盖率 ", "<span class=\"btn hot\">算力覆盖率 ")
    markup = markup.replace("<span>覆盖率<b", "<span>算力覆盖率<b")
    markup = markup.replace("本轮覆盖率", "本轮算力覆盖率")
    markup = _annotate_metric_cards(_annotate_period_cards(_address_missing_copy(markup, public_meta), public_meta), public_meta)
    markup = _period_missing_copy(markup, public_meta)
    markup = _daily_markup(markup)
    core_caption = '<p class="node-core-daily-caption">每日统计 · 北京时间 08:00 结算</p>'
    old_caption = ('<p>先看结果，再看口径。</p>' if mobile else
                   '<p>展示最近一次刷新得到的全网算力、产币模型、地址规模与统计日新增数据。</p>')
    markup = _once(markup, old_caption, core_caption)
    # Wrap seed warnings with a stable slot, including when they are empty.
    warning_pattern = r'<div class="(?:alert|m-note)">本轮算力覆盖率.*?</div>'
    markup = re.sub(warning_pattern, lambda match: '<div data-preview-coverage-warning>' + match.group(0) + '</div>', markup, flags=re.S)
    metric_pattern = r'(<article\b[^>]*data-label="全网总算力"[^>]*>)(.*?)(</article>)'
    markup = re.sub(metric_pattern, lambda match: match.group(1) + _replace_power_note(match.group(2), power_note) + match.group(3), markup, flags=re.S)
    risk_prefix = "榜单基于公开区块浏览器接口、RPC 与 POWER 合约日志生成。"
    risk_source = "算力与累计销毁读取自建节点的 POWER 合约，价格、全网地址数与流通量来自官方浏览器。"
    risk_scope = "榜单仅展示已发现地址中的前 100 名；算力覆盖率不代表地址名单完整。"
    markup = markup.replace("榜单基于公开区块浏览器接口、RPC 与 POWER 合约日志生成，是 best effort 结果。", risk_source + risk_scope)
    markup = markup.replace(risk_prefix, risk_source)
    markup = markup.replace("公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，都可能造成与官方后台的差异。", "节点同步延迟、未收录地址、合约事件遗漏或官方接口缓存回退，都可能造成与官方后台的差异。")
    markup = markup.replace("公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，都可能造成与官方后台存在差异。", "节点同步延迟、未收录地址、合约事件遗漏或官方接口缓存回退，都可能造成与官方后台存在差异。")
    html = markup + '<script id="rankData"' + scripts
    language_marker = "  const zhToEn = {"
    extra_translations = {
        "每日统计 · 北京时间 08:00 结算": "Daily statistics · Close at 08:00 Beijing time",
        "统计区间 · 北京时间": "Statistics window · Beijing time",
        "查看趋势": "View trend",
        "统计区间内交易币量 · MARS": "Transaction volume in this window · MARS",
        "每日新增算力": "Daily New Power",
        "每日新增地址": "Daily New Addresses",
        "每日销毁量": "Daily Burned Amount",
        "每日交易量": "Daily Transaction Volume",
        "每日交易笔数": "Daily Transaction Count",
        "每日活跃地址": "Daily Active Addresses",
        "预计日产量": "Estimated Daily Emission",
        "矿工 75% 产量口径": "Based on the miners' 75% emission share",
        "1亿算力 ÷ 单币日需算力": "100M power / power required per daily MARS",
        "流通量、累计销毁、每日销毁量按日期联动展示。": "Separate charts for circulation, cumulative burns and daily burns.",
        "总钱包、每日新增地址、每日活跃地址、正算力地址按日期联动展示。": "Separate charts for total wallets, daily new addresses, daily active addresses and positive-power addresses.",
        "每日交易量与每日交易笔数按日期联动展示。": "Separate charts for daily transaction volume and count.",
        "预计日产量、单币日需算力、1亿算力产出按日期联动展示。": "Separate charts for estimated daily emission and modeled power output.",
        "保留上次统计": "Previous statistics retained",
        "保留上次日统计": "Previous daily statistics retained",
        "部分数据待补齐": "Some statistics are pending",
        "待补齐": "Pending recovery",
        "暂无可比采样": "No comparable samples",
        "新增算力、新增地址与销毁数量按日期联动展示。当前窗口缺失项单独标示；历史曲线按各自实际统计日期展示，缺失不补值。": "Separate charts for new power, new addresses and burns. Missing current-window figures are marked individually; history keeps its actual statistics dates without filling gaps.",
        "部分日统计待补齐": "Some daily statistics are pending",
        "统计时间待核验": "Statistics time pending verification",
        "每日新增算力 · 北京时间": "Daily power growth · Beijing time",
        "日销毁统计 · 北京时间": "Daily burns · Beijing time",
        "新增 / 活跃地址统计 · 北京时间": "New / active addresses · Beijing time",
        "交易日统计 · 北京时间": "Daily transactions · Beijing time",
        "12.25 · 场景参考": "12.25 · Scenario reference",
        "圣诞情景测算": "Christmas Scenario Estimate",
        "参考时点 · 12月25日": "Reference · Dec 25",
        "本期测算已结束": "This scenario period has ended",
        "测算暂不可用，等待数据更新。": "Estimate unavailable. Waiting for updated data.",
        "数据更新延迟，当前使用最近记录估算。": "Data is delayed. Estimates use the latest available record.",
        "预测圣诞流通量，再计入 8 天预计产量。": "Projected Christmas circulation plus 8 days of estimated emission.",
        "1 亿算力对应参考销毁量": "Reference burn for 100M power",
        "按现有 1 亿算力占当前全网算力的比例测算": "Based on the share of existing 100M power in current global power",
        "8 天预计产量": "Estimated 8-day emission",
        "参考销毁规模 · 35%": "Reference burn pool · 35%",
        "已计入该轮 8 天预计产量，按当前全网算力测算；未来新增销毁暂按零计算，结果随数据更新，仅供参考。": "Includes the round’s estimated 8-day emission at current global power. Future burns are assumed zero. Estimates update with the data and are for reference.",
        "已计入未来 8 天预计产量，按当前全网算力测算；未来新增销毁暂按零计算，结果随数据更新，仅供参考。": "Includes the next 8 days of estimated emission at current global power. Future burns are assumed zero. Estimates update with the data and are for reference.",
        "数据更新延迟，当前使用最近记录估算；未来新增销毁暂按零计算。": "Data is delayed. Estimates use the latest record and assume no future burns.",
        "距参考日": "Until reference date",
        "· 数据更新": "· Data updated",
        "查看测算说明": "View calculation details",
        "当前全网算力": "Current global power",
        "每亿算力测算值（未取整）": "Estimate per 100M power (unrounded)",
        "参考销毁规模 =（预计圣诞流通量 + 8 天预计产量）× 35%。": "Reference burn pool = (projected Christmas circulation + estimated 8-day emission) × 35%.",
        "每亿算力参考销毁量 = 参考销毁规模 × 1 亿 ÷ 当前全网总算力。": "Reference burn per 100M power = reference burn pool × 100M ÷ current global power.",
        "这里的 1 亿指参与测算的现有算力，结果不代表新增 1 亿算力的确定成本。": "100M means existing power used for this calculation. The result is not a guaranteed cost of adding 100M power.",
        "产量按现有经济模型估算，跨减半周期分段计算；已发生销毁由最新流通量体现。": "Emission is estimated using the existing model across halving cycles. Observed burns are already reflected in circulation.",
        "起算时间、35% 与 8 天均为本页测算假设，非已确认的官方活动安排。": "The start time, 35% and 8 days are scenario assumptions, not confirmed official event arrangements.",
        "方程式参考测算": "Equation Reference Estimate",
        "输入算力，查看含未来 8 天产量的销毁参考。": "Enter power to estimate a burn including the next 8 days of emission.",
        "参与测算的算力": "Power used for calculation",
        "对应参考销毁量": "Reference burn amount",
        "按现有 1 亿算力占比测算。": "Based on the share of existing 100M power.",
        "参考销毁规模 =（当前流通量 + 未来 8 天预计产量）× 35%。": "Reference burn pool = (current circulation + estimated next 8-day emission) × 35%.",
        "参考销毁量 = 参考销毁规模 × 输入算力 ÷ 当前全网总算力。": "Reference burn = reference burn pool × entered power ÷ current global power.",
        "输入值表示参与测算的现有算力，结果不代表新增同等算力的确定成本。": "The input is existing power used for this calculation. The result is not a guaranteed cost of adding that amount of power.",
        "8 天从当前数据时点起算；按现有经济模型估算产量，跨减半周期分段计算。": "The 8-day window starts at the current data timestamp. Emission uses the existing model across halving cycles.",
        "本页为参考测算，活动参数及执行时间以官方公告为准。": "This is a reference estimate. Official announcements determine event parameters and execution times.",
        "膨胀 1 亿算力预计需销毁": "Estimated MARS burn for 100M power inflation",
        "膨胀 1 亿算力所需 MARS": "MARS needed for 100M power inflation",
        "膨胀所需销毁（估算）": "Estimated burn for power inflation",
        "先预测圣诞起算流通量，再加上该轮 8 天产量，测算所需销毁。": "Forecast circulation at Christmas, add the round’s 8-day emission, then estimate the required burn.",
        "距圣诞预计产量": "Modeled emission until Christmas",
        "预计圣诞起算流通量": "Projected circulation at Christmas start",
        "＋ 圣诞轮次 8 天产量": "+ Christmas round 8-day emission",
        "含 8 天产量的测算基数": "Calculation basis including 8-day emission",
        "未来 8 天产量": "Next 8 days of modeled emission",
        "沿用原有算力换算口径 · 已计入该轮 8 天产量": "Uses the existing power conversion · Includes the round’s 8-day emission",
        "MARS · 距圣诞": "MARS · Until Christmas",
        "测算基数 = 当前流通量 + 距圣诞预计产量 + 圣诞起算后的 8 天产量。": "Calculation basis = current circulation + emission until Christmas + the following 8 days of emission.",
        "膨胀 1 亿算力所需 MARS = 测算基数 × 35% ÷ 当前全网总算力 × 1 亿。": "MARS needed for 100M power inflation = calculation basis × 35% ÷ current global power × 100M.",
        "8 天产量从 12 月 25 日 08:00 至 1 月 2 日 08:00（北京时间）计算，与距圣诞产量不重叠。": "The 8-day emission runs from Dec 25 at 08:00 to Jan 2 at 08:00 Beijing time, without overlap with the earlier emission.",
        "35% 与 8 天沿用本次测算参数，不代表已确认的圣诞节活动安排。": "35% and 8 days are scenario parameters, not a confirmed Christmas event rule.",
        "已计入未来 8 天模型产量；未来未知销毁按 0 估算，实际所需数量随流通量与算力更新。": "Includes 8 days of modeled future emission. Future burns are assumed zero; the amount updates with circulation and power.",
        "流通量记录有延迟，已按模型产量补算至当前；期间未知销毁按 0 处理。": "The circulation record is delayed and projected to now using modeled emissions. Unknown burns during the gap are assumed zero.",
        "12.25 · 情景预测": "12.25 \u00b7 Scenario estimate",
        "圣诞节销毁预测": "Christmas Burn Forecast",
        "12月25日 · 08:00": "Dec 25 \u00b7 08:00",
        "以最新流通量为起点，累加至圣诞节的模型产量。": "Latest circulation plus modeled emissions until Christmas.",
        "当前流通量": "Current circulation",
        "剩余预计产量": "Remaining modeled emissions",
        "预计圣诞流通量": "Projected Christmas circulation",
        "按 35% 预计销毁总量": "Estimated burn at 35%",
        "1亿算力预计需销毁": "Estimated burn per 100M power",
        "模型日产量": "Modeled daily emission",
        "MARS · 剩余": "MARS \u00b7 Remaining",
        "计算口径": "Calculation method",
        "未来尚未发生的销毁按 0 测算；实际销毁通过最新流通量体现，不重复扣除。": "Future burns are assumed to be zero. Burns already reflected in circulation are not deducted again.",
        "产量沿用现有经济模型，跨产量周期分段累加，未满一天按剩余时间折算。": "Uses the existing emission model, integrated across cycles and prorated for partial days.",
        "35% 为沿用当前测算参数，不代表已确认的圣诞节活动安排。": "35% is the current calculation parameter, not a confirmed Christmas event rule.",
        "本期预测已结束": "This forecast period has ended",
        "预测数据暂不可用，等待最新数据。": "Forecast unavailable. Waiting for fresh data.",
        "算力覆盖率": "Power Coverage",
        "自建节点 POWER 合约": "Self-hosted node POWER contract",
        "自建节点 POWER 合约 · 最近成功值": "Self-hosted node POWER contract · Last successful value",
        "官方浏览器 POWER 统计（回退）": "Official explorer POWER statistics (fallback)",
        "官网已发布算力快照（校准中）": "Published ranking snapshot (calibrating)",
        "数据说明": "Data notes",
        "暂无可比快照": "No comparable snapshots",
        "复制地址": "Copy address",
        "查看地址": "View address",
        "已复制": "Copied",
        "请手动复制地址": "Copy the address manually",
        "排行榜 · 预览版": "Ranking · Preview",
        "排行榜 · 每分钟更新": "Ranking · Minute updates",
        "榜单约每分钟刷新，已确认交易可能稍后显示；每日统计按北京时间 08:00 日结。": "The ranking refreshes about every minute. Confirmed transactions may appear later; daily statistics close at 08:00 Beijing time.",
        "算力和累计销毁来自自建节点；价格、全网地址总数、流通量来自官方浏览器。": "Power and cumulative burns come from the self-hosted node. Prices, total addresses and circulation come from the official explorer.",
        "官方价格、地址的日历史取自北京时间 08:00 附近采样；标有“近截点采样”的数据不是精确截点值。": "Daily price and address history is sampled near 08:00 Beijing time. Values marked as near-cutoff samples are not exact cutoff measurements.",
        "官方价格、地址的日历史取自北京时间 08:00 附近采样；每日新增地址为相邻统计日采样差额，可能与精确截点值存在差异。": "Daily price and address history is sampled near 08:00 Beijing time. Daily new addresses are the difference between adjacent statistics-day samples and may differ from exact cutoff values.",
        "每分钟处理已确认新区块": "Confirmed blocks processed every minute",
        "北京时间 08:00 日结": "Daily close at 08:00 Beijing time",
        "每分钟刷新 · 每日 08:00 统计": "Minute updates · Daily statistics at 08:00 Beijing time",
    }
    language_entries = "\n" + "\n".join(json.dumps(key, ensure_ascii=False) + ": " + json.dumps(value) + "," for key, value in extra_translations.items())
    html = _once(html, language_marker, language_marker + language_entries)
    html = html.replace(r"/^覆盖率\s+(.+)$/", r"/^(?:算力)?覆盖率\s+(.+)$/")
    html = html.replace("return `Coverage ${match[1]}`;", "return `Power coverage ${match[1]}`;")
    # Translate adapter-owned cutoff labels while preserving the original date.
    dynamic_marker = "  const translateDynamic = (text) => {"
    dynamic_extra = """
    const dailyWindow = text.match(/^(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}) 至 (\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2})(（北京时间）)?$/);
    if (dailyWindow) return `${dailyWindow[1]} to ${dailyWindow[2]}${dailyWindow[3] ? ' (Beijing time)' : ''}`;
    const periodCutoff = text.match(/^截至 (.+?)（北京时间）$/);
    if (periodCutoff) return `Through ${periodCutoff[1]} (Beijing time)`;
    if (text === '统计时间待核验（北京时间）') return 'Statistics time pending verification (Beijing time)';
    const approxBurn = text.match(/^(当前)?约\s+([\d,.]+)(?:\s+(MARS))?$/);
    if (approxBurn) return `${approxBurn[1] ? 'Currently approx.' : 'Approx.'} ${approxBurn[2]}${approxBurn[3] ? ' MARS' : ''}`;
    const inputShare = text.match(/^按现有 (.+) 算力占比测算。$/);
    if (inputShare) return `Based on the share of existing ${translateChineseNumericUnit(inputShare[1]) || inputShare[1]} power.`;
    const nodeCutoff = text.match(/^截至 (.+?) 北京时间( · 沿用历史快照)?$/);
    if (nodeCutoff) return `Through ${nodeCutoff[1]} Beijing time${nodeCutoff[2] ? ' · Previous snapshot retained' : ''}`;
    const sampledAddress = text.match(/^(.+?)（近截点采样）$/);
    if (sampledAddress) return `${translateChineseNumericUnit(sampledAddress[1].replace(/个$/, '')) || sampledAddress[1]} (near-cutoff sample)`;
    if (text.startsWith('算力与累计销毁读取自建节点的 POWER 合约，价格、全网地址数与流通量来自官方浏览器。')) {
      let note = 'Power and cumulative burns are read from the self-hosted POWER contract. Prices, total addresses and circulation come from the official explorer. ';
      const supply = text.match(/总产量采用官方经济模型口径：(.+?) 枚永不增发/);
      if (supply) note += `The official supply cap is ${translateChineseNumericUnit(supply[1]) || supply[1]} coins, with no additional issuance. Daily emission follows the official formula and chain age, allocated 75% to miners and 25% to nodes. Daily power per coin is estimated as total power divided by miners' daily emission. `;
      note += 'The ranking shows the top 100 discovered addresses; power coverage does not mean the address list is complete. Node synchronization, undiscovered addresses, contract event changes or delayed sources may cause differences from official data.';
      return note;
    }
"""
    html = _once(html, dynamic_marker, dynamic_marker + dynamic_extra)
    html = html.replace("数据已加载 · 北京时间 08:00 每日采集", "每分钟刷新 · 每日 08:00 统计")
    html = html.replace("全网算力、钱包地址", "全网算力指标、已发现地址")
    # Accurate expired state: do not claim a new cycle occurred from a clock alone.
    html = _once(html, "算力新周期启动时间已到", "本期倒计时已结束 · 等待官方公布下一期")
    html = _once(html, "The next power cycle is starting", "This countdown has ended; awaiting the next official cycle")
    html = _once(html, "</body>", '<script src="/live_preview.js" defer></script>\n</body>')
    if 'data-paid-panel' in html:
        # Selector references in old JS are harmless; an actual panel is forbidden.
        if re.search(r'<[^>]+\sdata-paid-panel(?:\s|=|>)', html):
            raise RuntimeError("Paid download panel unexpectedly present in preview")
    return html


def build_preview(payload: dict, output_dir: str | Path, *, production: bool = False) -> dict[str, str]:
    """Write deployment artifacts once; leave latest.json publication to caller."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    sources = (Path(__file__), RUNTIME, Path(dashboard.__file__), Path(__file__).with_name("christmas.py"),
               *OPENING_SOURCES)
    code_version = hashlib.sha256(b"".join(path.read_bytes() for path in sources)
        + str(production).encode() + (dashboard.build_analytics_head().encode() if production else b"")).hexdigest()
    manifest_path = root / ".preview-build.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        hashes = manifest.get("artifacts", {})
        if (manifest.get("code_version") == code_version
                and hashes and all((root / name).exists() for name in hashes)):
            return hashes
    public = public_payload(payload)
    (root / "m").mkdir(exist_ok=True)
    # Keep the approved opening in the same content-addressed runtime. It needs
    # no additional public objects, permissions, requests or data dependencies.
    opening = "\n".join(path.read_text(encoding="utf-8") for path in OPENING_SOURCES) if production else ""
    runtime = (opening + "\n" if opening else "") + RUNTIME.read_text(encoding="utf-8")
    artifacts = {
        "index.html": _adapt_html(dashboard.build_html(public), mobile=False, power_note=_power_source_note(public["meta"]), public_meta=public["meta"], production=production),
        "m/index.html": _adapt_html(dashboard.build_mobile_html(public), mobile=True, power_note=_power_source_note(public["meta"]), public_meta=public["meta"], production=production),
        "live_preview.js": runtime,
    }
    result = {}
    for name, content in artifacts.items():
        target = root / name
        # Repeated service starts do not rewrite identical artifacts.
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
        result[name] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    manifest_path.write_text(json.dumps({"code_version": code_version, "artifacts": result}), encoding="utf-8")
    return result
