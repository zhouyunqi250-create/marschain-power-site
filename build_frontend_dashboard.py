#!/usr/bin/env python3
"""Build a standalone frontend-style MarsChain ranking dashboard."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def format_generated_at(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def build_html(payload: dict) -> str:
    meta = payload["meta"]
    rows = payload["rows"]
    title = "MarsChain 算力排行榜"
    coverage_target = float(meta.get("coverage_target", 0.80))
    target_met = bool(meta.get("target_met", meta.get("discovered_power_coverage", 0) >= coverage_target))
    threshold_label = f"{coverage_target * 100:.0f}%"
    subtitle = (
        f"基于公开 explorer API 的深度扫描结果，当前覆盖率已达到 {threshold_label} 发布阈值。"
        if target_met
        else f"基于公开 explorer API 的深度扫描结果，本轮覆盖率暂未达到 {threshold_label} 发布阈值。"
    )
    embedded = json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    generated_at = format_generated_at(int(meta["generated_at"]))
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
    .stat-card .label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .stat-card .value {{
      font-size: clamp(20px, 2vw, 30px);
      line-height: 1.1;
      letter-spacing: -0.03em;
      font-weight: 700;
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
            <span>生成时间：{generated_at}</span>
            <span>交易扫描：{meta["tx_pages"]} 页</span>
            <span>区块扫描：{meta["block_pages"]} 页</span>
            <span>上级递归深度：{meta["upline_depth"]}</span>
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
          <h2 class="section-title">完整榜单</h2>
          <div class="section-note">支持搜索地址、快速筛选和列排序。默认按算力从高到低排序。</div>
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
              <tr>
                <th data-key="rank">排名</th>
                <th data-key="address">地址</th>
                <th data-key="power">算力</th>
                <th data-key="total_burned_amount">累计燃烧</th>
                <th data-key="tx_seen">交易命中</th>
                <th data-key="upline_seen">上级命中</th>
                <th data-key="upline1">一级上级</th>
                <th data-key="upline2">二级上级</th>
              </tr>
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

    const formatUnits = (raw) => {{
      if (raw >= 1e12) return (raw / 1e12).toFixed(2) + 'T';
      if (raw >= 1e9) return (raw / 1e9).toFixed(2) + 'B';
      if (raw >= 1e6) return (raw / 1e6).toFixed(2) + 'M';
      if (raw >= 1e3) return (raw / 1e3).toFixed(2) + 'K';
      return String(raw);
    }};
    const formatCoverage = (value) => (value * 100).toFixed(2) + '%';
    const formatGeneratedAt = (ts) => new Date(ts * 1000).toLocaleString('zh-CN', {{ hour12: false }});

    function renderHero() {{
      const coverage = meta.discovered_power_coverage;
      document.getElementById('coverageValue').textContent = formatCoverage(coverage);
      document.getElementById('coverageRing').style.setProperty('--pct', `${{coverage * 360}}deg`);
    }}

    function renderStats() {{
      const cards = [
        ['覆盖率', formatCoverage(meta.discovered_power_coverage)],
        ['已发现总算力', formatUnits(meta.discovered_total_power)],
        ['全网总算力', formatUnits(meta.network_total_power)],
        ['正算力地址', meta.positive_power_count.toLocaleString()],
        ['候选地址', meta.candidate_count.toLocaleString()],
        ['前 100 名总算力', formatUnits(rows.slice(0, 100).reduce((sum, row) => sum + row.power_num, 0))],
        ['交易扫描页', meta.tx_pages.toLocaleString()],
        ['区块扫描页', meta.block_pages.toLocaleString()]
      ];
      document.getElementById('statGrid').innerHTML = cards.map(([label, value]) => `
        <div class="stat-card">
          <div class="label">${{label}}</div>
          <div class="value">${{value}}</div>
        </div>
      `).join('');
    }}

    function renderTopCards() {{
      const topFive = rows.slice(0, 5);
      document.getElementById('topGrid').innerHTML = topFive.map((row) => `
        <article class="top-card">
          <div class="top-rank">第 ${{row.rank}} 名</div>
          <div class="top-power">${{row.power_display}}</div>
          <div class="top-address">${{row.address}}</div>
          <div class="top-sub">总燃烧 ${{row.total_burned_amount_display}} | 交易命中 ${{row.tx_seen}}</div>
        </article>
      `).join('');
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
        ['over10b', '≥ 10B'],
        ['withUpline', '有上级'],
        ['activeTx', '高频交易']
      ];
      const row = document.getElementById('chipRow');
      row.innerHTML = chips.map(([key, label]) => `
        <button class="chip ${{state.filter === key ? 'active' : ''}}" data-filter="${{key}}">${{label}}</button>
      `).join('');
      row.querySelectorAll('[data-filter]').forEach((button) => {{
        button.addEventListener('click', () => {{
          state.filter = button.dataset.filter;
          renderTable();
          renderChips();
        }});
      }});
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
      const list = getFilteredRows();
      document.getElementById('tableBody').innerHTML = list.map((row) => `
        <tr>
          <td>${{row.rank}}</td>
          <td class="mono">${{row.address}}</td>
          <td><span class="pill">${{row.power_display}}</span></td>
          <td>${{row.total_burned_amount_display}}</td>
          <td>${{row.tx_seen}}</td>
          <td>${{row.upline_seen}}</td>
          <td class="mono">${{row.upline1 || '—'}}</td>
          <td class="mono">${{row.upline2 || '—'}}</td>
        </tr>
      `).join('');

      document.getElementById('footerText').textContent =
        `当前显示 ${{list.length}} / ${{rows.length}} 行。最近更新时间：${{formatGeneratedAt(meta.generated_at)}}。` +
        `本轮覆盖率 ${{formatCoverage(meta.discovered_power_coverage)}}，目标阈值 ${{formatCoverage(coverageTarget)}}，` +
        `${{targetMet ? '已达标' : '未达标'}}。说明：这是一份基于公开 explorer API 深度扫描得到的 best effort 榜单，不是官方后端直接导出的全量榜。`;
    }}

    function bindEvents() {{
      document.getElementById('searchInput').addEventListener('input', (event) => {{
        state.query = event.target.value;
        renderTable();
      }});
      document.querySelectorAll('th[data-key]').forEach((cell) => {{
        cell.addEventListener('click', () => {{
          const key = cell.dataset.key;
          if (state.sortKey === key) {{
            state.sortDir = state.sortDir === 'desc' ? 'asc' : 'desc';
          }} else {{
            state.sortKey = key;
            state.sortDir = key === 'address' || key.startsWith('upline') ? 'asc' : 'desc';
          }}
          renderTable();
        }});
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
