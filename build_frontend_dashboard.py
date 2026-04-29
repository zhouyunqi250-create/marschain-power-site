#!/usr/bin/env python3
"""Build a standalone frontend-style MarsChain ranking dashboard."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def format_generated_at(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def load_analytics_config() -> dict[str, str]:
    baidu_site_id = os.getenv("BAIDU_TONGJI_SITE_ID", "").strip()
    clarity_project_id = (
        os.getenv("MICROSOFT_CLARITY_PROJECT_ID", "").strip()
        or os.getenv("CLARITY_PROJECT_ID", "").strip()
    )
    return {
        "baidu_site_id": baidu_site_id,
        "clarity_project_id": clarity_project_id,
    }


def build_analytics_head() -> str:
    config = load_analytics_config()
    scripts: list[str] = []

    if config["baidu_site_id"]:
        scripts.append(
            """
  <script>
    window._hmt = window._hmt || [];
    (function() {
      var hm = document.createElement("script");
      hm.src = "https://hm.baidu.com/hm.js?%s";
      hm.defer = true;
      var s = document.getElementsByTagName("script")[0];
      s.parentNode.insertBefore(hm, s);
    })();
  </script>
"""
            % config["baidu_site_id"]
        )

    if config["clarity_project_id"]:
        scripts.append(
            """
  <script type="text/javascript">
    (function(c,l,a,r,i,t,y){
      c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};
      t=l.createElement(r); t.async=1; t.src="https://www.clarity.ms/tag/" + i;
      y=l.getElementsByTagName(r)[0]; y.parentNode.insertBefore(t,y);
    })(window, document, "clarity", "script", "%s");
  </script>
"""
            % config["clarity_project_id"]
        )

    if not scripts:
        return ""

    return "".join(scripts)


def build_html(payload: dict) -> str:
    meta = payload["meta"]
    rows = payload["rows"]
    title = "MarsChain 算力排行榜"
    coverage_target = float(meta.get("coverage_target", 0.80))
    target_met = bool(meta.get("target_met", meta.get("discovered_power_coverage", 0) >= coverage_target))
    threshold_label = f"{coverage_target * 100:.0f}%"
    rpc_blocks_scanned = int(meta.get("rpc_blocks_scanned", 0) or 0)
    rpc_log_blocks_scanned = int(meta.get("rpc_log_blocks_scanned", 0) or 0)
    rpc_logs_seen = int(meta.get("rpc_logs_seen", 0) or 0)
    subtitle = (
        f"基于公开 explorer API、官方 RPC 与 POWER 合约日志生成，当前扫描覆盖率已达到 {threshold_label} 目标线。"
        if target_met
        else f"基于公开 explorer API、官方 RPC 与 POWER 合约日志生成，本轮扫描覆盖率暂未达到 {threshold_label} 目标线。"
    )
    embedded = json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    generated_at = format_generated_at(int(meta["generated_at"]))
    analytics_head = build_analytics_head()
    hero_meta_items = [f"生成时间：{generated_at}"]
    if int(meta.get("tx_pages", 0) or 0) > 0:
        hero_meta_items.append(f'交易扫描：{int(meta.get("tx_pages", 0))} 页')
    if int(meta.get("block_pages", 0) or 0) > 0:
        hero_meta_items.append(f'区块扫描：{int(meta.get("block_pages", 0))} 页')
    if rpc_blocks_scanned > 0:
        hero_meta_items.append(f"RPC 深扫：{rpc_blocks_scanned:,} 块")
    if rpc_log_blocks_scanned > 0:
        hero_meta_items.append(f"合约日志：{rpc_log_blocks_scanned:,} 块 / {rpc_logs_seen:,} 条")
    if int(meta.get("upline_depth", 0) or 0) > 0:
        hero_meta_items.append(f'上级递归深度：{int(meta.get("upline_depth", 0))}')
    hero_meta_html = "\n".join(f"            <span>{item}</span>" for item in hero_meta_items)
    warning_html = (
        ""
        if target_met
        else (
            '<div class="alert warn">'
            f'<strong>本轮覆盖率未达标</strong><span>当前覆盖率仅为 '
            f'{meta["discovered_power_coverage"] * 100:.2f}% ，低于 {threshold_label} 发布阈值。'
            "这版结果仍已发布，方便你继续查看，但需要按页面提示理解覆盖范围。</span>"
            "</div>"
        )
    )
    risk_html = (
        '<div class="alert info">'
        '<strong>口径与风险</strong>'
        '<span>本页不是 MarsChain 官方后台导出的排行榜，而是基于公开 explorer API、官方 RPC 与 POWER 合约日志生成的 best effort 看板。'
        '全网总算力来自浏览器公开统计，候选钱包来自 POWER 合约日志，单地址算力来自公开地址接口。'
        '如果公开 API 延迟、RPC 节点漏返回、合约日志解析口径变化或缓存 fallback，榜单可能与官方最终口径存在偏差。</span>'
        "</div>"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <meta name="description" content="{subtitle}">
  <meta name="theme-color" content="#08111f">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{subtitle}">
  <meta property="og:type" content="website">
{analytics_head}
  <style>
    :root {{
      --bg: #08111f;
      --bg-2: #0c1830;
      --panel: rgba(255, 255, 255, 0.065);
      --panel-strong: rgba(255, 255, 255, 0.1);
      --line: rgba(150, 190, 255, 0.18);
      --text: #eef4ff;
      --muted: #96a8ca;
      --accent: #4da3ff;
      --accent-2: #f59e0b;
      --good: #22c55e;
      --shadow: 0 30px 80px rgba(0, 0, 0, 0.35);
      --radius: 24px;
      --font: "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif;
      --mono: ui-monospace, "SFMono-Regular", Menlo, monospace;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--font);
      color: var(--text);
      background:
        radial-gradient(circle at 15% 10%, rgba(77, 163, 255, 0.18), transparent 24%),
        radial-gradient(circle at 88% 0%, rgba(245, 158, 11, 0.16), transparent 22%),
        linear-gradient(180deg, var(--bg) 0%, var(--bg-2) 100%);
    }}
    .wrap {{
      width: min(1440px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    .hero {{
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 32px;
      padding: 34px;
      background:
        linear-gradient(135deg, rgba(13, 27, 53, 0.95), rgba(8, 17, 31, 0.98)),
        linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0));
      box-shadow: var(--shadow);
    }}
    .hero::after {{
      content: "";
      position: absolute;
      right: -60px;
      top: -60px;
      width: 220px;
      height: 220px;
      border-radius: 999px;
      background: radial-gradient(circle at center, rgba(77, 163, 255, 0.45), rgba(77, 163, 255, 0) 70%);
      pointer-events: none;
    }}
    .eyebrow {{
      margin: 0 0 10px;
      color: #7db8ff;
      font-size: 12px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) 320px;
      gap: 24px;
      align-items: end;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(34px, 5vw, 56px);
      line-height: 1.04;
      letter-spacing: -0.03em;
      max-width: 11ch;
    }}
    .subtitle {{
      margin: 14px 0 0;
      max-width: 760px;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.7;
    }}
    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      margin-top: 22px;
      color: var(--muted);
      font-size: 13px;
    }}
    .coverage {{
      justify-self: end;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: 20px;
      background: rgba(255, 255, 255, 0.04);
    }}
    .coverage-ring {{
      --pct: 0deg;
      width: 160px;
      height: 160px;
      margin: 0 auto 18px;
      border-radius: 999px;
      background:
        radial-gradient(circle at center, rgba(8, 17, 31, 1) 0 58%, transparent 59%),
        conic-gradient(var(--accent) 0 var(--pct), rgba(255,255,255,0.08) var(--pct) 360deg);
      display: grid;
      place-items: center;
      position: relative;
    }}
    .coverage-ring::before {{
      content: "";
      position: absolute;
      inset: 14px;
      border-radius: inherit;
      border: 1px solid rgba(255,255,255,0.08);
    }}
    .coverage-value {{
      position: relative;
      z-index: 1;
      text-align: center;
    }}
    .coverage-value strong {{
      display: block;
      font-size: 34px;
      line-height: 1;
    }}
    .coverage-value span {{
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .stat-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .alert {{
      display: flex;
      gap: 12px;
      align-items: flex-start;
      margin-top: 18px;
      padding: 16px 18px;
      border-radius: 18px;
      border: 1px solid rgba(245, 158, 11, 0.22);
      background: rgba(245, 158, 11, 0.1);
      color: #fde7ba;
      line-height: 1.6;
      box-shadow: var(--shadow);
    }}
    .alert.info {{
      border-color: rgba(77, 163, 255, 0.22);
      background: rgba(77, 163, 255, 0.1);
      color: #dbeafe;
    }}
    .alert.info strong {{
      color: #9ed0ff;
    }}
    .alert strong {{
      flex: 0 0 auto;
      color: #ffd484;
      font-size: 13px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .alert span {{
      font-size: 13px;
    }}
    .stat-card, .section, .top-card, .table-shell {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    .stat-card {{
      padding: 18px;
    }}
    .stat-card .label-row {{
      display: flex;
      align-items: center;
      gap: 7px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .info-dot {{
      display: inline-grid;
      place-items: center;
      width: 16px;
      height: 16px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.2);
      color: #9ed0ff;
      font-size: 11px;
      line-height: 1;
      cursor: help;
    }}
    .stat-card .value {{
      font-size: clamp(20px, 2vw, 30px);
      line-height: 1.1;
      letter-spacing: -0.03em;
      font-weight: 700;
    }}
    .stat-card .help {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }}
    .section {{
      margin-top: 18px;
      padding: 24px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 20px;
      margin-bottom: 18px;
    }}
    .section-title {{
      margin: 0;
      font-size: 24px;
      line-height: 1.1;
      letter-spacing: -0.03em;
    }}
    .section-note {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
      max-width: 760px;
    }}
    .top-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
    }}
    .top-card {{
      padding: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.07), rgba(255,255,255,0.03));
    }}
    .top-rank {{
      color: var(--accent-2);
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .top-power {{
      margin-top: 12px;
      font-size: 30px;
      font-weight: 700;
      line-height: 1;
      letter-spacing: -0.04em;
    }}
    .top-address {{
      margin-top: 12px;
      font-family: var(--mono);
      font-size: 12px;
      color: #d5e6ff;
      word-break: break-all;
    }}
    .top-sub {{
      margin-top: 10px;
      font-size: 12px;
      color: var(--muted);
    }}
    .bar-list {{
      display: grid;
      gap: 14px;
    }}
    .bar-row {{
      display: grid;
      grid-template-columns: 28px 1.3fr minmax(180px, 3fr) 110px;
      gap: 12px;
      align-items: center;
    }}
    .bar-rank {{
      color: var(--muted);
      font-size: 13px;
    }}
    .bar-label {{
      font-family: var(--mono);
      font-size: 12px;
      color: #d9e7ff;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .bar-track {{
      position: relative;
      height: 12px;
      border-radius: 999px;
      background: rgba(255,255,255,0.08);
      overflow: hidden;
    }}
    .bar-fill {{
      position: absolute;
      inset: 0 auto 0 0;
      width: 0%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), #7cc4ff);
    }}
    .bar-value {{
      text-align: right;
      font-size: 13px;
      color: var(--text);
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 16px;
    }}
    .action-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}
    .action-btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 44px;
      padding: 0 16px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(255,255,255,0.06);
      color: var(--text);
      text-decoration: none;
      font-size: 13px;
      transition: 0.2s ease;
    }}
    .action-btn:hover {{
      transform: translateY(-1px);
      border-color: rgba(77, 163, 255, 0.45);
      background: rgba(77, 163, 255, 0.14);
    }}
    .search {{
      flex: 1 1 360px;
      min-width: 260px;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(8, 17, 31, 0.6);
      color: var(--text);
      font: inherit;
    }}
    .chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .chip {{
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(255,255,255,0.05);
      color: var(--muted);
      border-radius: 999px;
      padding: 10px 14px;
      cursor: pointer;
      font: inherit;
      font-size: 12px;
    }}
    .chip.active {{
      color: white;
      border-color: rgba(77,163,255,0.5);
      background: rgba(77,163,255,0.16);
    }}
    .table-shell {{
      overflow: hidden;
    }}
    .table-wrap {{
      overflow: auto;
      max-height: 70vh;
    }}
    table {{
      width: 100%;
      min-width: 1100px;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 14px 16px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      position: sticky;
      top: 0;
      z-index: 2;
      background: #12203d;
      color: #d8e7ff;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}
    tbody tr:hover {{
      background: rgba(77,163,255,0.08);
    }}
    .mono {{
      font-family: var(--mono);
      font-size: 12px;
      color: #d8e7ff;
      word-break: break-all;
    }}
    .pill {{
      display: inline-block;
      min-width: 56px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(34, 197, 94, 0.14);
      color: #98f0b0;
      text-align: center;
      font-size: 12px;
    }}
    .muted {{
      color: var(--muted);
    }}
    .footer {{
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.7;
    }}
    @media (max-width: 1180px) {{
      .hero-grid {{ grid-template-columns: 1fr; }}
      .coverage {{ justify-self: stretch; }}
      .stat-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .top-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 760px) {{
      .wrap {{ width: min(100vw - 20px, 1440px); padding-top: 14px; }}
      .hero, .section {{ padding: 18px; border-radius: 22px; }}
      .stat-grid, .top-grid {{ grid-template-columns: 1fr; }}
      .bar-row {{ grid-template-columns: 24px 1fr; }}
      .bar-track, .bar-value {{ grid-column: 2; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <p class="eyebrow">MarsChain 算力前端看板</p>
          <h1>{title}</h1>
          <p class="subtitle">{subtitle}</p>
          <div class="hero-meta">
{hero_meta_html}
          </div>
          <div class="action-row">
            <a class="action-btn" href="./downloads/latest.csv" download data-track="download_csv" data-label="latest.csv">下载 CSV</a>
            <a class="action-btn" href="./downloads/latest.xlsx" download data-track="download_xlsx" data-label="latest.xlsx">下载 Excel</a>
            <a class="action-btn" href="./data/latest.json" target="_blank" rel="noopener" data-track="open_json" data-label="latest.json">查看 JSON</a>
          </div>
        </div>
        <div class="coverage">
          <div class="coverage-ring" id="coverageRing">
            <div class="coverage-value">
              <strong id="coverageValue"></strong>
              <span>覆盖率</span>
            </div>
          </div>
          <div class="muted" style="text-align:center; font-size:13px; line-height:1.6;">
            已发现地址算力 / 浏览器公布总算力
          </div>
        </div>
      </div>
    </section>
    {warning_html}
    {risk_html}

    <section class="stat-grid" id="statGrid"></section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2 class="section-title">头部地址概览</h2>
          <div class="section-note">先看头部集中度，再看完整榜单。这个区块更适合做快速判断，不用一下子扎进长表。</div>
        </div>
      </div>
      <div class="top-grid" id="topGrid"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2 class="section-title">前 15 名横向分布</h2>
          <div class="section-note">用横向条形图看头部断层最直观。这里按当前榜单默认排序展示。</div>
        </div>
      </div>
      <div class="bar-list" id="barList"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2 class="section-title">榜单明细（前 100）</h2>
          <div class="section-note">支持搜索地址、快速筛选和列排序。页面展示前 100 名，统计卡片中的候选钱包和正算力钱包为本轮全量扫描口径。</div>
        </div>
      </div>
      <div class="toolbar">
        <input id="searchInput" class="search" type="search" placeholder="搜索地址 / 上级地址 / 关键字段">
        <div class="chip-row" id="chipRow"></div>
      </div>
      <div class="table-shell">
        <div class="table-wrap">
          <table>
            <thead>
              <tr id="tableHead"></tr>
            </thead>
            <tbody id="tableBody"></tbody>
          </table>
        </div>
      </div>
      <div class="footer" id="footerText"></div>
    </section>
  </div>

  <script id="rankData" type="application/json">{embedded}</script>
  <script>
    const analytics = {{
      track(eventName, detail = {{}}) {{
        try {{
          if (window._hmt && typeof window._hmt.push === 'function') {{
            const label = detail.label ? String(detail.label) : '';
            window._hmt.push(['_trackEvent', 'marschain_site', eventName, label]);
          }}
        }} catch (error) {{
          console.warn('Baidu analytics track failed:', error);
        }}
        try {{
          if (typeof window.clarity === 'function') {{
            window.clarity('event', eventName);
            if (detail.label) {{
              window.clarity('set', 'last_event_label', String(detail.label).slice(0, 120));
            }}
          }}
        }} catch (error) {{
          console.warn('Clarity analytics track failed:', error);
        }}
      }}
    }};

    const payload = JSON.parse(document.getElementById('rankData').textContent);
    const meta = payload.meta;
    const coverageTarget = Number(meta.coverage_target || 0.8);
    const targetMet = Boolean(meta.target_met ?? (meta.discovered_power_coverage >= coverageTarget));
    const rows = payload.rows.map((row, index) => ({{
      ...row,
      rank: index + 1,
      power_num: Number(row.power),
      burned_num: Number(row.total_burned_amount),
      tx_seen_num: Number(row.tx_seen),
      log_seen_num: Number(row.log_seen || 0),
      upline_seen_num: Number(row.upline_seen),
      search_blob: [
        row.address,
        row.upline1 || '',
        row.upline2 || '',
        row.power_display,
        row.total_burned_amount_display
      ].join(' ').toLowerCase()
    }}));

    const state = {{
      query: '',
      filter: 'all',
      sortKey: 'power',
      sortDir: 'desc'
    }};
    let searchTrackTimer = null;
    let lastTrackedQuery = '';

    const formatUnits = (raw) => {{
      raw = Number(raw || 0);
      if (raw >= 1e12) return (raw / 1e12).toFixed(2) + 'T';
      if (raw >= 1e9) return (raw / 1e9).toFixed(2) + 'B';
      if (raw >= 1e6) return (raw / 1e6).toFixed(2) + 'M';
      if (raw >= 1e3) return (raw / 1e3).toFixed(2) + 'K';
      return String(raw);
    }};
    const formatCoverage = (value) => (value * 100).toFixed(2) + '%';
    const formatGeneratedAt = (ts) => new Date(ts * 1000).toLocaleString('zh-CN', {{ hour12: false }});
    const formatCount = (value) => Number(value || 0).toLocaleString();
    const formatMaybeUnits = (value) => (value === null || value === undefined) ? '—' : formatUnits(value);
    const escapeAttr = (value) => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

    function renderHero() {{
      const coverage = meta.discovered_power_coverage;
      document.getElementById('coverageValue').textContent = formatCoverage(coverage);
      document.getElementById('coverageRing').style.setProperty('--pct', `${{coverage * 360}}deg`);
    }}

    function renderStats() {{
      const top100Power = rows.slice(0, 100).reduce((sum, row) => sum + row.power_num, 0);
      const cards = [
        {{
          label: '全网总算力',
          value: formatUnits(meta.network_total_power),
          help: '来自 explorer /power/stats 的公开总算力，是覆盖率计算的分母。'
        }},
        {{
          label: '已发现总算力',
          value: formatUnits(meta.discovered_total_power),
          help: '本轮扫描到的正算力钱包算力合计，是覆盖率计算的分子。'
        }},
        {{
          label: '覆盖率',
          value: formatCoverage(meta.discovered_power_coverage),
          help: '已发现总算力 ÷ 全网总算力。它不是官方完整率，只代表公开数据下的扫描覆盖程度。'
        }},
        {{
          label: '全链地址总数',
          value: formatCount(meta.explorer_total_addresses),
          help: '浏览器统计的链上地址总数，包含不一定参与算力系统的地址。',
          hidden: !Number(meta.explorer_total_addresses || 0)
        }},
        {{
          label: '算力候选钱包',
          value: formatCount(meta.candidate_count),
          help: '从 POWER 合约日志中识别出的候选钱包地址总数，包含当前算力为 0 的地址。'
        }},
        {{
          label: '正算力钱包',
          value: formatCount(meta.positive_power_count),
          help: '候选钱包里当前 power > 0 的地址数量，也就是实际进入榜单计算的地址。'
        }},
        {{
          label: `链上今日新增钱包${{meta.today_utc_date ? ' · ' + meta.today_utc_date + ' UTC' : ''}}`,
          value: meta.today_new_wallet_count === null || meta.today_new_wallet_count === undefined ? '—' : formatCount(meta.today_new_wallet_count),
          help: '按链上 UTC 日统计：今天第一次出现在 POWER 合约日志里的候选钱包地址数。'
        }},
        {{
          label: `链上今日新增总算力${{meta.today_utc_date ? ' · ' + meta.today_utc_date + ' UTC' : ''}}`,
          value: formatMaybeUnits(meta.today_new_power),
          help: '按链上 UTC 日统计：当前全网总算力减去上一 UTC 日合约日历史总算力。'
        }},
        {{
          label: '前 100 名总算力',
          value: formatUnits(top100Power),
          help: '当前榜单前 100 个地址的算力合计，用来观察头部集中度。'
        }},
        {{
          label: '合约日志扫描',
          value: `${{formatCount(meta.rpc_log_blocks_scanned)}} 块`,
          help: `本轮从 POWER 合约日志扫描候选地址，共读取 ${{formatCount(meta.rpc_logs_seen)}} 条日志。`
        }}
      ].filter((card) => !card.hidden);
      document.getElementById('statGrid').innerHTML = cards.map((card) => `
        <div class="stat-card">
          <div class="label-row">
            <span>${{card.label}}</span>
            <span class="info-dot" title="${{escapeAttr(card.help)}}">!</span>
          </div>
          <div class="value">${{card.value}}</div>
          <div class="help">${{card.help}}</div>
        </div>
      `).join('');
    }}

    function renderTopCards() {{
      const topFive = rows.slice(0, 5);
      document.getElementById('topGrid').innerHTML = topFive.map((row) => {{
        const parts = [`总燃烧 ${{row.total_burned_amount_display}}`];
        if (row.tx_seen_num > 0) parts.push(`交易命中 ${{row.tx_seen}}`);
        if (row.log_seen_num > 0) parts.push(`日志命中 ${{row.log_seen}}`);
        return `
        <article class="top-card">
          <div class="top-rank">第 ${{row.rank}} 名</div>
          <div class="top-power">${{row.power_display}}</div>
          <div class="top-address">${{row.address}}</div>
          <div class="top-sub">${{parts.join(' | ')}}</div>
        </article>
      `;
      }}).join('');
    }}

    function renderBars() {{
      const list = rows.slice(0, 15);
      const maxPower = list[0]?.power_num || 1;
      document.getElementById('barList').innerHTML = list.map((row) => `
        <div class="bar-row">
          <div class="bar-rank">${{row.rank}}</div>
          <div class="bar-label" title="${{row.address}}">${{row.address}}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${{(row.power_num / maxPower) * 100}}%"></div></div>
          <div class="bar-value">${{row.power_display}}</div>
        </div>
      `).join('');
    }}

    const filters = {{
      all: () => true,
      top20: (row) => row.rank <= 20,
      over10b: (row) => row.power_num >= 10_000_000_000,
      withUpline: (row) => Boolean(row.upline1 || row.upline2),
      activeTx: (row) => row.tx_seen_num >= 10
    }};

    function renderChips() {{
      const chips = [
        ['all', '全部'],
        ['top20', '前 20 名'],
        ['over10b', '≥ 10B']
      ];
      if (rows.some((row) => row.upline1 || row.upline2)) chips.push(['withUpline', '有上级']);
      if (rows.some((row) => row.tx_seen_num >= 10)) chips.push(['activeTx', '高频交易']);
      const row = document.getElementById('chipRow');
      row.innerHTML = chips.map(([key, label]) => `
        <button class="chip ${{state.filter === key ? 'active' : ''}}" data-filter="${{key}}">${{label}}</button>
      `).join('');
      row.querySelectorAll('[data-filter]').forEach((button) => {{
        button.addEventListener('click', () => {{
          state.filter = button.dataset.filter;
          analytics.track('filter_change', {{ label: state.filter }});
          renderTable();
          renderChips();
        }});
      }});
    }}

    function getTableColumns() {{
      const hasTxSeen = rows.some((row) => row.tx_seen_num > 0);
      const hasLogSeen = rows.some((row) => row.log_seen_num > 0);
      const hasUplineSeen = rows.some((row) => row.upline_seen_num > 0);
      const hasUpline1 = rows.some((row) => Boolean(row.upline1));
      const hasUpline2 = rows.some((row) => Boolean(row.upline2));
      return [
        {{ key: 'rank', label: '排名', help: '当前按算力排序后的名次。' }},
        {{ key: 'address', label: '地址', help: '候选钱包地址。' }},
        {{ key: 'power', label: '算力', help: '地址当前公开算力。' }},
        {{ key: 'total_burned_amount', label: '累计燃烧', help: '地址历史累计燃烧数量，来自公开地址接口。' }},
        {{ key: 'log_seen', label: '日志命中', help: '该地址在 POWER 合约日志中出现的次数。', visible: hasLogSeen }},
        {{ key: 'tx_seen', label: '交易命中', help: '仅在启用交易页扫描时显示；当前全链日志模式通常不需要。', visible: hasTxSeen }},
        {{ key: 'upline_seen', label: '上级命中', help: '仅在启用上级递归扫描时显示。', visible: hasUplineSeen }},
        {{ key: 'upline1', label: '一级上级', help: '公开接口返回的一级上级地址。', visible: hasUpline1 }},
        {{ key: 'upline2', label: '二级上级', help: '公开接口返回的二级上级地址。', visible: hasUpline2 }}
      ].filter((column) => column.visible !== false);
    }}

    function renderTableHead(columns) {{
      const head = document.getElementById('tableHead');
      head.innerHTML = columns.map((column) => `
        <th data-key="${{column.key}}">
          ${{column.label}}
          <span class="info-dot" title="${{escapeAttr(column.help)}}">!</span>
        </th>
      `).join('');
      head.querySelectorAll('th[data-key]').forEach((cell) => {{
        cell.addEventListener('click', () => {{
          const key = cell.dataset.key;
          if (state.sortKey === key) {{
            state.sortDir = state.sortDir === 'desc' ? 'asc' : 'desc';
          }} else {{
            state.sortKey = key;
            state.sortDir = key === 'address' || key.startsWith('upline') ? 'asc' : 'desc';
          }}
          analytics.track('table_sort', {{ label: `${{state.sortKey}}:${{state.sortDir}}` }});
          renderTable();
        }});
      }});
    }}

    function renderCell(row, key) {{
      const cells = {{
        rank: row.rank,
        address: `<span class="mono">${{row.address}}</span>`,
        power: `<span class="pill">${{row.power_display}}</span>`,
        total_burned_amount: row.total_burned_amount_display,
        tx_seen: row.tx_seen,
        log_seen: row.log_seen || 0,
        upline_seen: row.upline_seen,
        upline1: `<span class="mono">${{row.upline1 || '—'}}</span>`,
        upline2: `<span class="mono">${{row.upline2 || '—'}}</span>`
      }};
      return cells[key] ?? '';
    }}

    function getFilteredRows() {{
      const query = state.query.trim().toLowerCase();
      const filterFn = filters[state.filter] || filters.all;
      const list = rows.filter((row) => filterFn(row) && (!query || row.search_blob.includes(query)));
      const dir = state.sortDir === 'asc' ? 1 : -1;
      list.sort((a, b) => {{
        const key = state.sortKey;
        const map = {{
          rank: [a.rank, b.rank],
          address: [a.address, b.address],
          power: [a.power_num, b.power_num],
          total_burned_amount: [a.burned_num, b.burned_num],
          tx_seen: [a.tx_seen_num, b.tx_seen_num],
          log_seen: [a.log_seen_num, b.log_seen_num],
          upline_seen: [a.upline_seen_num, b.upline_seen_num],
          upline1: [a.upline1 || '', b.upline1 || ''],
          upline2: [a.upline2 || '', b.upline2 || '']
        }};
        const [left, right] = map[key] || [a.power_num, b.power_num];
        if (typeof left === 'number' && typeof right === 'number') return (left - right) * dir;
        return String(left).localeCompare(String(right)) * dir;
      }});
      return list;
    }}

    function renderTable() {{
      const columns = getTableColumns();
      renderTableHead(columns);
      const list = getFilteredRows();
      document.getElementById('tableBody').innerHTML = list.map((row) => `
        <tr>
          ${{columns.map((column) => `<td>${{renderCell(row, column.key)}}</td>`).join('')}}
        </tr>
      `).join('');

      document.getElementById('footerText').textContent =
        `当前显示 ${{list.length}} / ${{rows.length}} 行。最近更新时间：${{formatGeneratedAt(meta.generated_at)}}。` +
        `本轮覆盖率 ${{formatCoverage(meta.discovered_power_coverage)}}，目标阈值 ${{formatCoverage(coverageTarget)}}，` +
        `${{targetMet ? '已达标' : '未达标'}}。说明：候选钱包 ${{formatCount(meta.candidate_count)}} 个，正算力钱包 ${{formatCount(meta.positive_power_count)}} 个；` +
        `这是一份基于公开 explorer API、官方 RPC 和合约日志生成的 best effort 榜单，不是官方后端直接导出的全量榜。`;
    }}

    function bindEvents() {{
      document.querySelectorAll('[data-track]').forEach((node) => {{
        node.addEventListener('click', () => {{
          analytics.track(node.dataset.track || 'click', {{ label: node.dataset.label || '' }});
        }});
      }});
      document.getElementById('searchInput').addEventListener('input', (event) => {{
        state.query = event.target.value;
        if (searchTrackTimer) {{
          clearTimeout(searchTrackTimer);
        }}
        searchTrackTimer = setTimeout(() => {{
          const normalized = state.query.trim().toLowerCase();
          if (normalized.length >= 2 && normalized !== lastTrackedQuery) {{
            lastTrackedQuery = normalized;
            analytics.track('search_used', {{ label: normalized.slice(0, 60) }});
          }}
        }}, 600);
        renderTable();
      }});
    }}

    renderHero();
    renderStats();
    renderTopCards();
    renderBars();
    renderChips();
    renderTable();
    bindEvents();
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a standalone frontend dashboard from a ranking JSON file.")
    parser.add_argument("input", help="Ranking JSON path.")
    parser.add_argument("output", help="Output HTML path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    payload = json.loads(input_path.read_text())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_html(payload))
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
