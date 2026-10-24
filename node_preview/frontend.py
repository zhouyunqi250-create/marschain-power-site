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


def _strip_private(value: Any) -> Any:
    """Public metadata must never acquire raw exports, keys, or wallet balances."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(word in normalized for word in (
                "balance", "private_key", "secret", "credential", "access_key",
                "rpc_url", "rpc_endpoint", "authorization", "password", "csv",
            )):
                continue
            if normalized in {"addresses", "all_addresses", "raw_rows", "all_rows"}:
                continue
            result[key] = _strip_private(item)
        return result
    if isinstance(value, list):
        return [_strip_private(item) for item in value]
    return value


def public_payload(payload: dict) -> dict:
    """Return meta + at most 100 public rank rows + compact presentation data."""
    meta = _strip_private(copy.deepcopy(payload.get("meta") or {}))
    # A legacy target_met flag belongs to its old snapshot. Recompute it from
    # the same live ratio shown beside this warning.
    coverage = dashboard._as_float(meta.get("discovered_power_coverage"))
    target = dashboard._as_float(meta.get("coverage_target"), 0.8)
    meta["target_met"] = math.isfinite(coverage) and coverage >= target
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
            "metrics": _metric_views(desktop["metric_cards"]),
            "growth": desktop["growth_cards"],
            "timeline": desktop["timeline_rows"],
            "marquee": desktop["marquee_html"],
            "trends": json.loads(desktop["metric_trend_payload"]),
            "warning": desktop["warning_html"],
        },
        "mobile": {
            "metrics": _metric_views(mobile["key_cards"]),
            "timeline": mobile["meta_rows"],
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
    if source.get("source") == "local_rpc_power_contract":
        label = "自建节点 POWER 合约" if meta.get("node_preview", {}).get("mode") == "node_production" else "本机节点 POWER 合约"
        return label + (" · 最近成功值" if source.get("status") != "ok" else "")
    if source.get("source") and "explorer.marschain.net" in str(source.get("source")):
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
.node-preview-page-buttons{display:contents}
@media(max-width:760px){.node-preview-info{margin:8px 0 14px;padding:10px 12px}}
</style>"""

_PREVIEW_BANNER = """
<aside class="node-preview-info" data-node-preview aria-live="polite">
 <p><strong>节点排行榜 · 预览版</strong><span data-preview-state>正在读取最新采集状态…</span></p>
 <p data-preview-data-time>正在读取数据版本…</p>
 <p data-preview-scope>当前名单为已发现地址集合，尚未覆盖全部链上地址。</p>
 <p data-preview-error hidden></p>
</aside>"""


def _adapt_html(html: str, *, mobile: bool, power_note: str, production: bool = False) -> str:
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
    banner = _PREVIEW_BANNER.replace("节点排行榜 · 预览版", "节点排行榜 · 每分钟更新") if production else _PREVIEW_BANNER
    html = _once(html, shell_marker, shell_marker + banner)
    # Apply copy changes only to visible markup. The production language bundle
    # and JSON source payload remain intact for compatibility.
    markup, scripts = html.split('<script id="rankData"', 1)
    markup = markup.replace("扫描覆盖率", "算力覆盖率")
    markup = markup.replace("<span class=\"btn hot\">覆盖率 ", "<span class=\"btn hot\">算力覆盖率 ")
    markup = markup.replace("<span>覆盖率<b", "<span>算力覆盖率<b")
    markup = markup.replace("本轮覆盖率", "本轮算力覆盖率")
    # Wrap seed warnings with a stable slot, including when they are empty.
    warning_pattern = r'<div class="(?:alert|m-note)">本轮算力覆盖率.*?</div>'
    markup = re.sub(warning_pattern, lambda match: '<div data-preview-coverage-warning>' + match.group(0) + '</div>', markup, flags=re.S)
    metric_pattern = r'(<article\b[^>]*data-label="全网总算力"[^>]*>)(.*?)(</article>)'
    markup = re.sub(metric_pattern, lambda match: match.group(1) + _replace_power_note(match.group(2), power_note) + match.group(3), markup, flags=re.S)
    risk_prefix = "榜单基于公开区块浏览器接口、RPC 与 POWER 合约日志生成。"
    risk_source = ("本排行榜从自建节点" if production else "本预览版从本机节点") + " POWER 合约读取算力和累计销毁，价格、全网地址数及流通量来自官方浏览器统计。"
    markup = markup.replace(risk_prefix, risk_source)
    markup = markup.replace("榜单基于公开区块浏览器接口、RPC 与 POWER 合约日志生成，是 best effort 结果。", risk_source + "榜单仅覆盖已发现地址，是 best effort 结果。")
    markup = markup.replace("公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，都可能造成与官方后台的差异。", "节点同步延迟、未收录地址、合约事件遗漏或官方接口缓存回退，都可能造成与官方后台的差异。")
    markup = markup.replace("公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，都可能造成与官方后台存在差异。", "节点同步延迟、未收录地址、合约事件遗漏或官方接口缓存回退，都可能造成与官方后台存在差异。")
    html = markup + '<script id="rankData"' + scripts
    language_marker = "  const zhToEn = {"
    extra_translations = {
        "算力覆盖率": "Power Coverage",
        "本机节点 POWER 合约": "Local node POWER contract",
        "本机节点 POWER 合约 · 最近成功值": "Local node POWER contract · Last successful value",
        "自建节点 POWER 合约": "Self-hosted node POWER contract",
        "自建节点 POWER 合约 · 最近成功值": "Self-hosted node POWER contract · Last successful value",
        "官方浏览器 POWER 统计（回退）": "Official explorer POWER statistics (fallback)",
        "官网已发布算力快照（校准中）": "Published ranking snapshot (calibrating)",
    }
    language_entries = "\n" + "\n".join(json.dumps(key, ensure_ascii=False) + ": " + json.dumps(value) + "," for key, value in extra_translations.items())
    html = _once(html, language_marker, language_marker + language_entries)
    html = html.replace(r"/^覆盖率\s+(.+)$/", r"/^(?:算力)?覆盖率\s+(.+)$/")
    html = html.replace("return `Coverage ${match[1]}`;", "return `Power coverage ${match[1]}`;")
    html = html.replace("数据已加载 · 北京时间 08:00 每日采集", "节点预览 · 每分钟增量采集 · 08:00 日结")
    if production:
        html = html.replace("节点预览 · 每分钟增量采集 · 08:00 日结", "节点更新 · 每分钟增量采集 · 08:00 日结")
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
        "index.html": _adapt_html(dashboard.build_html(public), mobile=False, power_note=_power_source_note(public["meta"]), production=production),
        "m/index.html": _adapt_html(dashboard.build_mobile_html(public), mobile=True, power_note=_power_source_note(public["meta"]), production=production),
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
