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

VIEW_SCHEMA = 1
RUNTIME = Path(__file__).with_name("live_preview.js")


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
    ("key_cards", "meta_rows", "metric_trend_payload", "warning_html"),
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
            if isinstance(entry.get("exact_cutoff"), bool):
                trends[key]["exact_cutoff"] = entry["exact_cutoff"]
            if entry.get("timing_status") in {"exact", "near_cutoff", "sampled", "near_cutoff_sample"}:
                trends[key]["timing_status"] = entry["timing_status"]
    meta["metric_trends"] = trends
    return meta


def _period_note(meta: dict, days: int) -> str:
    stamp = meta.get(f"period_{days}d_end_timestamp")
    try:
        cutoff = int(stamp)
        label = datetime.fromtimestamp(cutoff, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "截止时间待核验"
    behind = cutoff < int(meta.get("statistics_window_end_timestamp") or cutoff)
    return f"截至 {label} 北京时间" + (" · 沿用历史快照" if behind else "")


def _annotate_period_cards(markup: str, meta: dict) -> str:
    def card(match):
        opening, inner, closing = match.groups()
        if "data-node-period-cutoff" in inner:
            return match.group(0)
        period = re.search(r'(?:data-label="(7|30) 天|<h3>(7|30) 天新增</h3>)', opening + inner)
        if not period:
            return match.group(0)
        days = int(next(group for group in period.groups() if group))
        note = f'<small class="node-period-cutoff" data-node-period-cutoff="{days}">{escape(_period_note(meta, days))}</small>'
        return opening + inner + note + closing
    return re.sub(r'(<article\b[^>]*>)(.*?)(</article>)', card, markup, flags=re.S)


def _address_missing_copy(markup: str, meta: dict) -> str:
    count = meta.get("statistics_window_new_candidate_address_count")
    display = "暂无可比快照" if count is None else meta.get("statistics_window_new_candidate_address_display")
    if display:
        # Preserve timing qualifications such as “近截点采样”, not just the
        # numeric count that the original dashboard formatter would render.
        note = escape(str(display))
        for pattern in (
            r'(<span>(?:统计日)?新增地址</span>\s*<(?:strong|b)>)[^<]*(</(?:strong|b)>)',
            r'(<span>(?:统计日)?新增地址\s*<b>)[^<]*(</b>)',
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
            "metrics": _metric_views(_address_missing_copy(desktop["metric_cards"], meta)),
            "growth": _annotate_period_cards(desktop["growth_cards"], meta),
            "timeline": _address_missing_copy(desktop["timeline_rows"], meta),
            "marquee": _address_missing_copy(desktop["marquee_html"], meta),
            "trends": json.loads(desktop["metric_trend_payload"]),
            "warning": desktop["warning_html"],
        },
        "mobile": {
            "metrics": _metric_views(_annotate_period_cards(_address_missing_copy(mobile["key_cards"], meta), meta)),
            "timeline": _address_missing_copy(mobile["meta_rows"], meta),
            "trends": json.loads(mobile["metric_trend_payload"]),
            "warning": mobile["warning_html"],
        },
        "burn": desktop["burn_estimate"],
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
 <p data-preview-sampling>官方价格、地址的日历史取自北京时间 08:00 附近采样；标有“近截点采样”的数据不是精确截点值。</p>
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
      if (activeMetric && modal && !modal.hidden) renderChart();
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
    markup = _annotate_period_cards(_address_missing_copy(markup, public_meta), public_meta)
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
    sources = (Path(__file__), RUNTIME, Path(dashboard.__file__))
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
    artifacts = {
        "index.html": _adapt_html(dashboard.build_html(public), mobile=False, power_note=_power_source_note(public["meta"]), public_meta=public["meta"], production=production),
        "m/index.html": _adapt_html(dashboard.build_mobile_html(public), mobile=True, power_note=_power_source_note(public["meta"]), public_meta=public["meta"], production=production),
        "live_preview.js": RUNTIME.read_text(encoding="utf-8"),
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
