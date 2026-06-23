#!/usr/bin/env python3
"""Build a standalone frontend-style MarsChain ranking dashboard."""

from __future__ import annotations

import argparse
import json
import os
import time
from html import escape
from pathlib import Path


MARS_INITIAL_CYCLE_OUTPUT_TOKENS = 100_000_000_000
MARS_HALVING_PERIOD_DAYS = 448
MARS_MINER_SHARE = 0.75
MARS_PAYMENT_ADDRESS_DISPLAY = "0M0fD038365577215292B44F89C92695C7AC8C3363"
MARS_PAYMENT_ADDRESS_VERIFY = "0x0fD038365577215292B44F89C92695C7AC8C3363"
MARS_PAID_DOWNLOAD_PRICE = "1000"
MARS_PAID_DOWNLOAD_EXPIRES_SECONDS = 3600

_OLD_STATISTICS_TIME = f"{8:02d}:00"
_OLD_STATISTICS_TIME_SECONDS = f"{8:02d}:00:00"
_NEW_STATISTICS_TIME = "00:00"
_NEW_STATISTICS_TIME_SECONDS = "00:00:00"


def _normalize_statistics_time_value(value):
    if isinstance(value, str):
        return value.replace(_OLD_STATISTICS_TIME_SECONDS, _NEW_STATISTICS_TIME_SECONDS).replace(
            _OLD_STATISTICS_TIME,
            _NEW_STATISTICS_TIME,
        )
    return value


def _normalize_statistics_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return payload
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return payload

    normalized_meta = dict(meta)
    for key, value in meta.items():
        if key == "statistics_day_start_hour":
            normalized_meta[key] = 0
            continue
        is_statistics_key = (
            key.startswith("statistics_")
            or key.startswith("rpc_log_statistics_")
            or key in {"today_new_wallet_basis", "today_burned_basis", "today_new_power_basis"}
            or (key.startswith("period_") and (key.endswith("_label") or key.endswith("_basis") or key.endswith("_local")))
        )
        if is_statistics_key:
            normalized_meta[key] = _normalize_statistics_time_value(value)

    normalized_payload = dict(payload)
    normalized_payload["meta"] = normalized_meta
    return normalized_payload


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


def load_paid_download_config() -> dict[str, str]:
    return {
        "api_base": os.getenv("MARSCHAIN_PAID_DOWNLOAD_API_BASE", "").strip().rstrip("/"),
        "pay_to_display": os.getenv("MARS_PAYMENT_ADDRESS_DISPLAY", MARS_PAYMENT_ADDRESS_DISPLAY).strip(),
        "pay_to_verify": os.getenv("MARS_PAYMENT_ADDRESS_VERIFY", MARS_PAYMENT_ADDRESS_VERIFY).strip(),
        "price_mars": os.getenv("MARS_PAID_DOWNLOAD_PRICE", MARS_PAID_DOWNLOAD_PRICE).strip() or MARS_PAID_DOWNLOAD_PRICE,
        "expires_label": "1 小时",
    }


def build_html(payload: dict) -> str:
    payload = _normalize_statistics_payload(payload)
    meta = payload["meta"]
    rows = payload["rows"]
    title = "MarsChain 算力排行榜"
    coverage_target = float(meta.get("coverage_target", 0.80))
    target_met = bool(meta.get("target_met", meta.get("discovered_power_coverage", 0) >= coverage_target))
    threshold_label = f"{coverage_target * 100:.0f}%"
    rpc_blocks_scanned = int(meta.get("rpc_blocks_scanned", 0) or 0)
    rpc_log_blocks_scanned = int(meta.get("rpc_log_blocks_scanned", 0) or 0)
    rpc_logs_seen = int(meta.get("rpc_logs_seen", 0) or 0)
    statistics_window_label = meta.get("statistics_window_label") or "北京时间 00:00:00 至次日 00:00:00"
    subtitle = "追踪链上算力分布、头部地址变化与北京时间统计日内新增趋势。"
    embedded = json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    generated_at = meta.get("generated_at_local") or format_generated_at(int(meta["generated_at"]))
    analytics_head = build_analytics_head()
    hero_meta_items = [
        f"最近刷新：{generated_at}",
        f"统计周期：{statistics_window_label}",
        "采集频率：每 24 小时一次",
        "抓取时间：每日 00:00（北京时间，夜里 24:00）",
    ]
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
        f'<span>本榜单基于公开区块浏览器、官方 RPC 与 POWER 合约日志生成，统计周期为 {statistics_window_label}。'
        '它是 best effort 数据看板，可能因公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，与官方后台存在差异。</span>'
        "</div>"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <meta name="description" content="{subtitle}">
  <meta name="theme-color" content="#07080d">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{subtitle}">
  <meta property="og:type" content="website">
{analytics_head}
  <style>
    :root {{
      --bg: #07080d;
      --bg-2: #0b0d14;
      --surface: rgba(16, 18, 26, 0.72);
      --surface-strong: rgba(23, 26, 36, 0.88);
      --surface-soft: rgba(255, 255, 255, 0.035);
      --line: rgba(255, 255, 255, 0.09);
      --line-strong: rgba(255, 255, 255, 0.16);
      --text: #f7f8fb;
      --muted: #9aa0ad;
      --muted-2: #6f7685;
      --accent: #8f92ff;
      --accent-2: #7dd3fc;
      --good: #64d98a;
      --warn: #f4c06a;
      --glow: rgba(125, 211, 252, 0.72);
      --glow-2: rgba(143, 146, 255, 0.86);
      --glow-soft: rgba(125, 211, 252, 0.18);
      --motion-fast: 180ms;
      --motion-med: 320ms;
      --motion-slow: 12s;
      --hover-lift: -7px;
      --sheen-opacity: 0.62;
      --shadow: 0 22px 70px rgba(0, 0, 0, 0.38);
      --radius: 20px;
      --font: "Avenir Next", "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif;
      --mono: "SFMono-Regular", "JetBrains Mono", ui-monospace, Menlo, monospace;
    }}
    * {{ box-sizing: border-box; }}
    html {{ color-scheme: dark; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: var(--font);
      color: var(--text);
      background:
        radial-gradient(circle at 50% -10%, rgba(143, 146, 255, 0.18), transparent 34%),
        radial-gradient(circle at 8% 22%, rgba(125, 211, 252, 0.08), transparent 28%),
        radial-gradient(circle at 92% 12%, rgba(255, 255, 255, 0.08), transparent 20%),
        linear-gradient(180deg, var(--bg) 0%, var(--bg-2) 68%, #07080d 100%);
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px);
      background-size: 56px 56px;
      mask-image: linear-gradient(180deg, rgba(0,0,0,0.7), transparent 72%);
      animation: grid-drift 24s linear infinite;
    }}
    body::after {{
      content: "";
      position: fixed;
      inset: -35% -15% auto;
      height: 70vh;
      pointer-events: none;
      background:
        radial-gradient(circle at 22% 42%, rgba(125,211,252,0.16), transparent 34%),
        radial-gradient(circle at 72% 18%, rgba(143,146,255,0.18), transparent 32%);
      filter: blur(18px);
      opacity: 0.78;
      transform: translate3d(0, 0, 0);
      animation: aurora-flow 18s ease-in-out infinite alternate;
    }}
    .wrap {{
      position: relative;
      z-index: 1;
      width: min(1360px, calc(100vw - 36px));
      margin: 0 auto;
      padding: 32px 0 44px;
    }}
    .hero {{
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: 38px;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.075), rgba(255,255,255,0.025)),
        radial-gradient(circle at 80% 5%, rgba(143, 146, 255, 0.22), transparent 30%),
        rgba(10, 11, 16, 0.82);
      box-shadow: var(--shadow), 0 0 90px rgba(125,211,252,0.08), inset 0 1px 0 rgba(255,255,255,0.08);
      backdrop-filter: blur(20px);
    }}
    .hero::before {{
      content: "";
      position: absolute;
      inset: -1px;
      background:
        linear-gradient(112deg, transparent 0%, rgba(125,211,252,0.08) 28%, rgba(255,255,255,0.18) 48%, rgba(143,146,255,0.08) 64%, transparent 100%),
        linear-gradient(90deg, transparent, rgba(255,255,255,0.08), transparent);
      background-size: 240% 100%, 100% 100%;
      opacity: 0.42;
      pointer-events: none;
      animation: hero-sheen var(--motion-slow) ease-in-out infinite;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      right: -130px;
      top: -130px;
      width: 320px;
      height: 320px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.08);
      background: radial-gradient(circle at center, rgba(143, 146, 255, 0.28), rgba(143, 146, 255, 0) 68%);
      pointer-events: none;
      animation: pulse-orb 7s ease-in-out infinite;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 14px;
      padding: 7px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255,255,255,0.035);
      color: #c5c8ff;
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      box-shadow: 0 0 22px rgba(143,146,255,0.1);
    }}
    .eyebrow::before {{
      content: "";
      width: 6px;
      height: 6px;
      border-radius: 999px;
      background: var(--good);
      box-shadow: 0 0 18px rgba(100, 217, 138, 0.8);
      animation: signal-pulse 2.4s ease-in-out infinite;
    }}
    .hero-grid {{
      position: relative;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      gap: 28px;
      align-items: end;
    }}
    h1 {{
      margin: 0;
      max-width: 820px;
      font-size: clamp(38px, 5.3vw, 72px);
      line-height: 0.96;
      letter-spacing: -0.07em;
      text-wrap: balance;
    }}
    .subtitle {{
      margin: 18px 0 0;
      max-width: 820px;
      color: #b4bac8;
      font-size: 15px;
      line-height: 1.8;
    }}
    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 24px;
      color: var(--muted);
      font-size: 12px;
    }}
    .hero-meta span {{
      padding: 7px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255,255,255,0.035);
      font-family: var(--mono);
      transition: border-color var(--motion-fast) ease, box-shadow var(--motion-fast) ease, transform var(--motion-fast) ease;
    }}
    .hero-meta span:hover {{
      transform: translateY(-2px);
      border-color: rgba(125,211,252,0.32);
      box-shadow: 0 0 26px rgba(125,211,252,0.08);
    }}
    .status-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 9px;
      margin-top: 18px;
    }}
    .status-item {{
      min-height: 58px;
      padding: 11px 13px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,0.035);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.045);
      transition: transform var(--motion-med) ease, border-color var(--motion-med) ease, box-shadow var(--motion-med) ease;
    }}
    .status-item:hover {{
      transform: translateY(-3px);
      border-color: rgba(125,211,252,0.22);
      box-shadow: 0 14px 38px rgba(0,0,0,0.24), 0 0 24px rgba(125,211,252,0.08), inset 0 1px 0 rgba(255,255,255,0.07);
    }}
    .status-item > span {{
      display: block;
      color: var(--muted-2);
      font-size: 11px;
      letter-spacing: 0.08em;
    }}
    .status-item strong {{
      display: flex;
      align-items: center;
      gap: 7px;
      margin-top: 7px;
      color: #f4f6ff;
      font-size: 13px;
      line-height: 1.35;
    }}
    .status-dot {{
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--good);
      box-shadow: 0 0 18px rgba(100, 217, 138, 0.8);
      animation: signal-pulse 2.4s ease-in-out infinite;
    }}
    .coverage {{
      justify-self: end;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 18px;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.03)),
        rgba(7, 8, 13, 0.55);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
      transition: transform var(--motion-med) ease, border-color var(--motion-med) ease, box-shadow var(--motion-med) ease;
    }}
    .coverage:hover {{
      transform: translateY(var(--hover-lift));
      border-color: rgba(143,146,255,0.35);
      box-shadow: 0 24px 80px rgba(0,0,0,0.34), 0 0 42px rgba(143,146,255,0.12), inset 0 1px 0 rgba(255,255,255,0.1);
    }}
    .coverage-ring {{
      --pct: 0deg;
      width: 146px;
      height: 146px;
      margin: 0 auto 16px;
      border-radius: 999px;
      background:
        radial-gradient(circle at center, #0a0c12 0 57%, transparent 58%),
        conic-gradient(var(--accent) 0 var(--pct), rgba(255,255,255,0.08) var(--pct) 360deg);
      display: grid;
      place-items: center;
      position: relative;
      box-shadow: 0 0 50px rgba(143, 146, 255, 0.12);
      animation: ring-breathe 4.8s ease-in-out infinite;
    }}
    .coverage-ring::before {{
      content: "";
      position: absolute;
      inset: 13px;
      border-radius: inherit;
      border: 1px solid rgba(255,255,255,0.08);
      background: radial-gradient(circle, rgba(255,255,255,0.04), transparent 66%);
    }}
    .coverage-value {{
      position: relative;
      z-index: 1;
      text-align: center;
    }}
    .coverage-value strong {{
      display: block;
      font-size: 30px;
      line-height: 1;
      letter-spacing: -0.06em;
    }}
    .coverage-value span {{
      display: block;
      margin-top: 7px;
      color: var(--muted-2);
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .stat-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}
    .alert {{
      display: flex;
      gap: 12px;
      align-items: flex-start;
      margin-top: 12px;
      padding: 13px 15px;
      border-radius: 16px;
      border: 1px solid rgba(244, 192, 106, 0.2);
      background: rgba(244, 192, 106, 0.075);
      color: #f5d79a;
      line-height: 1.6;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
    }}
    .alert.info {{
      border-color: rgba(143, 146, 255, 0.2);
      background: rgba(143, 146, 255, 0.075);
      color: #d7dafd;
    }}
    .alert.info strong {{
      color: #cbcfff;
    }}
    .alert strong {{
      flex: 0 0 auto;
      color: #ffe0a3;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .alert span {{
      font-size: 12px;
    }}
    .stat-card, .section, .top-card, .table-shell {{
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.055);
      backdrop-filter: blur(18px);
      transform: translate3d(0, 0, 0);
    }}
    .stat-card {{
      min-height: 150px;
      padding: 15px;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.065), rgba(255,255,255,0.025)),
        rgba(13, 15, 22, 0.72);
      transition:
        transform var(--motion-med) cubic-bezier(.2,.8,.2,1),
        border-color var(--motion-med) ease,
        background var(--motion-med) ease,
        box-shadow var(--motion-med) ease;
    }}
    .stat-card::before, .section::before, .table-shell::before {{
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      opacity: 0;
      background:
        radial-gradient(circle at var(--spot-x, 78%) var(--spot-y, 0%), rgba(125,211,252,0.18), transparent 34%),
        linear-gradient(115deg, transparent 0%, rgba(255,255,255,0.12) 46%, transparent 58%);
      background-size: 100% 100%, 240% 100%;
      background-position: 0 0, 130% 0;
      transition: opacity var(--motion-med) ease, background-position 720ms ease;
    }}
    .stat-card:hover {{
      transform: translateY(var(--hover-lift)) scale(1.012);
      border-color: rgba(125,211,252,0.38);
      background: rgba(18, 20, 30, 0.9);
      box-shadow: 0 24px 70px rgba(0,0,0,0.34), 0 0 36px rgba(125,211,252,0.13), inset 0 1px 0 rgba(255,255,255,0.09);
    }}
    .stat-card:hover::before, .section:hover::before, .table-shell:hover::before {{
      opacity: var(--sheen-opacity);
      background-position: 0 0, -80% 0;
    }}
    .stat-card .label-row {{
      display: flex;
      align-items: center;
      gap: 7px;
      color: var(--muted);
      font-size: 11px;
      margin-bottom: 10px;
      letter-spacing: 0.03em;
    }}
    .info-dot {{
      display: inline-grid;
      place-items: center;
      width: 15px;
      height: 15px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.16);
      color: #c5c8ff;
      background: rgba(255,255,255,0.04);
      font-size: 10px;
      line-height: 1;
      cursor: help;
      transition: border-color var(--motion-fast) ease, box-shadow var(--motion-fast) ease, color var(--motion-fast) ease;
    }}
    .info-dot:hover {{
      color: white;
      border-color: rgba(125,211,252,0.42);
      box-shadow: 0 0 18px rgba(125,211,252,0.18);
    }}
    .stat-card .value {{
      font-size: clamp(22px, 2.2vw, 34px);
      line-height: 1;
      letter-spacing: -0.055em;
      font-weight: 700;
    }}
    .stat-card .help {{
      margin-top: 12px;
      color: var(--muted-2);
      font-size: 11px;
      line-height: 1.55;
    }}
    .section {{
      margin-top: 14px;
      padding: 20px;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.052), rgba(255,255,255,0.02)),
        rgba(11, 13, 20, 0.66);
      transition: transform var(--motion-med) ease, border-color var(--motion-med) ease, box-shadow var(--motion-med) ease;
    }}
    .section:hover {{
      transform: translateY(-4px);
      border-color: rgba(143,146,255,0.24);
      box-shadow: 0 22px 70px rgba(0,0,0,0.28), 0 0 36px rgba(143,146,255,0.1), inset 0 1px 0 rgba(255,255,255,0.08);
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 20px;
      margin-bottom: 16px;
      padding-bottom: 14px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
    }}
    .section-title {{
      margin: 0;
      font-size: 20px;
      line-height: 1.1;
      letter-spacing: -0.045em;
    }}
    .section-note {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.65;
      max-width: 760px;
    }}
    .top-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }}
    .top-card {{
      position: relative;
      overflow: hidden;
      padding: 16px;
      background:
        radial-gradient(circle at 100% 0%, rgba(143,146,255,0.14), transparent 42%),
        linear-gradient(180deg, rgba(255,255,255,0.065), rgba(255,255,255,0.024));
      transition: transform var(--motion-med) cubic-bezier(.2,.8,.2,1), border-color var(--motion-med) ease, box-shadow var(--motion-med) ease;
    }}
    .top-card::before {{
      content: "";
      position: absolute;
      inset: 0 0 auto;
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.28), transparent);
      animation: card-line-flow 5s linear infinite;
    }}
    .top-card::after {{
      content: "";
      position: absolute;
      inset: -60% -30%;
      pointer-events: none;
      opacity: 0;
      background: linear-gradient(115deg, transparent 35%, rgba(125,211,252,0.2) 48%, rgba(255,255,255,0.22) 50%, transparent 62%);
      transform: translateX(-28%) rotate(6deg);
      transition: opacity var(--motion-med) ease, transform 900ms ease;
    }}
    .top-card:hover {{
      transform: translateY(var(--hover-lift)) scale(1.018);
      border-color: rgba(125,211,252,0.4);
      box-shadow: 0 22px 64px rgba(0,0,0,0.34), 0 0 34px rgba(125,211,252,0.13), inset 0 1px 0 rgba(255,255,255,0.1);
    }}
    .top-card:hover::after {{
      opacity: 1;
      transform: translateX(34%) rotate(6deg);
    }}
    .top-rank {{
      color: var(--accent-2);
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .top-power {{
      margin-top: 14px;
      font-size: 28px;
      font-weight: 700;
      line-height: 1;
      letter-spacing: -0.055em;
    }}
    .top-address {{
      margin-top: 14px;
      font-family: var(--mono);
      font-size: 11px;
      color: #d8dafe;
      word-break: break-all;
    }}
    .top-sub {{
      margin-top: 10px;
      font-size: 11px;
      line-height: 1.5;
      color: var(--muted-2);
    }}
    .bar-list {{
      display: grid;
      gap: 11px;
    }}
    .bar-row {{
      display: grid;
      grid-template-columns: 26px minmax(170px, 1.1fr) minmax(180px, 3fr) 96px;
      gap: 12px;
      align-items: center;
      padding: 8px 10px;
      border: 1px solid transparent;
      border-radius: 12px;
      transition: transform var(--motion-fast) ease, border-color var(--motion-fast) ease, background var(--motion-fast) ease;
    }}
    .bar-row:hover {{
      transform: translateX(5px);
      border-color: rgba(125,211,252,0.18);
      background: rgba(125,211,252,0.045);
    }}
    .bar-rank {{
      color: var(--muted-2);
      font-family: var(--mono);
      font-size: 12px;
    }}
    .bar-label {{
      font-family: var(--mono);
      font-size: 11px;
      color: #d8dafe;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .bar-track {{
      position: relative;
      height: 8px;
      border-radius: 999px;
      background: rgba(255,255,255,0.07);
      overflow: hidden;
    }}
    .bar-fill {{
      position: absolute;
      inset: 0 auto 0 0;
      width: 0%;
      border-radius: inherit;
      overflow: hidden;
      background: linear-gradient(90deg, var(--accent), var(--accent-2), #a7f3d0);
      box-shadow: 0 0 22px rgba(143,146,255,0.35), 0 0 18px rgba(125,211,252,0.18);
    }}
    .bar-fill::after {{
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.76), transparent);
      transform: translateX(-100%);
      animation: bar-scan 2.7s ease-in-out infinite;
    }}
    .bar-value {{
      text-align: right;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--text);
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 14px;
    }}
    .action-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 9px;
      margin-top: 24px;
    }}
    .action-btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      position: relative;
      isolation: isolate;
      overflow: hidden;
      min-height: 38px;
      padding: 0 13px;
      border-radius: 11px;
      border: 1px solid rgba(255,255,255,0.1);
      background:
        linear-gradient(rgba(255,255,255,0.055), rgba(255,255,255,0.03)) padding-box,
        linear-gradient(120deg, rgba(125,211,252,0.28), rgba(143,146,255,0.2), rgba(255,255,255,0.08)) border-box;
      color: var(--text);
      text-decoration: none;
      font-size: 12px;
      box-shadow: 0 0 0 rgba(125,211,252,0);
      transition: transform var(--motion-fast) ease, border-color var(--motion-fast) ease, box-shadow var(--motion-fast) ease, color var(--motion-fast) ease;
    }}
    .action-btn::before, .chip::before {{
      content: "";
      position: absolute;
      inset: -2px;
      z-index: -1;
      opacity: 0;
      background: linear-gradient(115deg, transparent 24%, rgba(125,211,252,0.32), rgba(255,255,255,0.26), rgba(143,146,255,0.28), transparent 76%);
      transform: translateX(-70%);
      transition: opacity var(--motion-fast) ease, transform 720ms ease;
    }}
    .action-btn::after {{
      content: "";
      position: absolute;
      inset: 1px;
      z-index: -1;
      border-radius: 10px;
      background: radial-gradient(circle at 50% 0%, rgba(125,211,252,0.16), transparent 55%);
      opacity: 0;
      transition: opacity var(--motion-fast) ease;
    }}
    .action-btn:hover {{
      transform: translateY(-4px);
      border-color: rgba(125, 211, 252, 0.48);
      box-shadow: 0 16px 38px rgba(0,0,0,0.3), 0 0 26px rgba(125,211,252,0.16);
    }}
    .action-btn:hover::before, .chip:hover::before, .chip.active::before {{
      opacity: 1;
      transform: translateX(70%);
    }}
    .action-btn:hover::after {{
      opacity: 1;
    }}
    .action-btn:active, .chip:active {{
      transform: translateY(-1px) scale(0.985);
    }}
    .action-btn:focus-visible, .chip:focus-visible, .search:focus-visible {{
      outline: none;
      border-color: rgba(125,211,252,0.68);
      box-shadow: 0 0 0 3px rgba(125,211,252,0.16), 0 0 34px rgba(125,211,252,0.18);
    }}
    .search {{
      flex: 1 1 360px;
      min-width: 260px;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.1);
      background: rgba(7, 8, 13, 0.65);
      color: var(--text);
      font: inherit;
      outline: none;
      transition: border-color var(--motion-fast) ease, box-shadow var(--motion-fast) ease, background var(--motion-fast) ease;
    }}
    .search:focus {{
      border-color: rgba(125, 211, 252, 0.55);
      background: rgba(9, 12, 20, 0.82);
      box-shadow: 0 0 0 3px rgba(125, 211, 252, 0.12), 0 0 32px rgba(125,211,252,0.1);
    }}
    .chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }}
    .chip {{
      position: relative;
      isolation: isolate;
      overflow: hidden;
      border: 1px solid rgba(255,255,255,0.1);
      background: rgba(255,255,255,0.035);
      color: var(--muted);
      border-radius: 999px;
      padding: 9px 12px;
      cursor: pointer;
      font: inherit;
      font-size: 12px;
      transition: transform var(--motion-fast) ease, border-color var(--motion-fast) ease, color var(--motion-fast) ease, box-shadow var(--motion-fast) ease, background var(--motion-fast) ease;
    }}
    .chip:hover {{
      transform: translateY(-2px);
      color: var(--text);
      border-color: rgba(125,211,252,0.34);
      box-shadow: 0 12px 26px rgba(0,0,0,0.24), 0 0 18px rgba(125,211,252,0.08);
    }}
    .chip.active {{
      color: white;
      border-color: rgba(125,211,252,0.52);
      background: rgba(143,146,255,0.16);
      box-shadow: 0 0 24px rgba(143,146,255,0.14), inset 0 1px 0 rgba(255,255,255,0.08);
    }}
    .table-shell {{
      overflow: hidden;
      background: rgba(9, 10, 15, 0.72);
      transition: border-color var(--motion-med) ease, box-shadow var(--motion-med) ease;
    }}
    .table-shell:hover {{
      border-color: rgba(125,211,252,0.22);
      box-shadow: 0 24px 70px rgba(0,0,0,0.28), 0 0 34px rgba(125,211,252,0.08), inset 0 1px 0 rgba(255,255,255,0.07);
    }}
    .table-wrap {{
      overflow: auto;
      max-height: 72vh;
    }}
    table {{
      width: 100%;
      min-width: 980px;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 13px 14px;
      border-bottom: 1px solid rgba(255,255,255,0.065);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }}
    th {{
      position: sticky;
      top: 0;
      z-index: 2;
      background: rgba(15, 17, 24, 0.96);
      color: #dfe1ff;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      backdrop-filter: blur(14px);
      font-size: 12px;
      font-weight: 600;
      transition: color var(--motion-fast) ease, background var(--motion-fast) ease;
    }}
    th:hover {{
      color: white;
      background: rgba(24, 28, 42, 0.98);
    }}
    tbody tr {{
      transition: background var(--motion-fast) ease, transform var(--motion-fast) ease;
    }}
    tbody tr:hover {{
      transform: translateX(3px);
      background: rgba(143,146,255,0.075);
    }}
    .mono {{
      font-family: var(--mono);
      font-size: 11px;
      color: #d8dafe;
      word-break: break-all;
    }}
    .pill {{
      display: inline-block;
      min-width: 56px;
      padding: 5px 9px;
      border: 1px solid rgba(100, 217, 138, 0.18);
      border-radius: 999px;
      background: rgba(100, 217, 138, 0.105);
      color: #b6f5c8;
      text-align: center;
      font-family: var(--mono);
      font-size: 11px;
      box-shadow: 0 0 18px rgba(100,217,138,0.08);
    }}
    @keyframes grid-drift {{
      from {{ background-position: 0 0, 0 0; }}
      to {{ background-position: 56px 56px, 56px 56px; }}
    }}
    @keyframes aurora-flow {{
      0% {{ transform: translate3d(-2%, -1%, 0) scale(1); opacity: 0.6; }}
      50% {{ transform: translate3d(3%, 2%, 0) scale(1.06); opacity: 0.86; }}
      100% {{ transform: translate3d(-1%, 3%, 0) scale(1.02); opacity: 0.72; }}
    }}
    @keyframes hero-sheen {{
      0%, 100% {{ background-position: 160% 0, 0 0; }}
      45%, 55% {{ background-position: -80% 0, 0 0; }}
    }}
    @keyframes pulse-orb {{
      0%, 100% {{ transform: scale(1); opacity: 0.72; }}
      50% {{ transform: scale(1.08); opacity: 0.96; }}
    }}
    @keyframes signal-pulse {{
      0%, 100% {{ box-shadow: 0 0 14px rgba(100,217,138,0.62); }}
      50% {{ box-shadow: 0 0 26px rgba(100,217,138,0.95), 0 0 44px rgba(100,217,138,0.18); }}
    }}
    @keyframes ring-breathe {{
      0%, 100% {{ box-shadow: 0 0 42px rgba(143,146,255,0.12); }}
      50% {{ box-shadow: 0 0 66px rgba(143,146,255,0.22), 0 0 32px rgba(125,211,252,0.12); }}
    }}
    @keyframes card-line-flow {{
      from {{ background-position: -180px 0; }}
      to {{ background-position: 180px 0; }}
    }}
    @keyframes bar-scan {{
      0% {{ transform: translateX(-100%); opacity: 0; }}
      32% {{ opacity: 1; }}
      68% {{ opacity: 1; }}
      100% {{ transform: translateX(130%); opacity: 0; }}
    }}
    .muted {{
      color: var(--muted);
    }}
    .footer {{
      margin-top: 16px;
      color: var(--muted-2);
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
      .wrap {{ width: min(100vw - 20px, 1360px); padding-top: 12px; }}
      .hero, .section {{ padding: 18px; border-radius: 20px; }}
      h1 {{ font-size: clamp(34px, 12vw, 54px); }}
      .stat-grid, .top-grid {{ grid-template-columns: 1fr; }}
      .status-strip {{ grid-template-columns: 1fr; }}
      .alert {{ flex-direction: column; }}
      .bar-row {{ grid-template-columns: 24px 1fr; }}
      .bar-track, .bar-value {{ grid-column: 2; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        animation-duration: 1ms !important;
        animation-iteration-count: 1 !important;
        scroll-behavior: auto !important;
        transition-duration: 1ms !important;
      }}
      .stat-card:hover, .top-card:hover, .section:hover, .coverage:hover, .action-btn:hover, .chip:hover, tbody tr:hover, .bar-row:hover {{
        transform: none !important;
      }}
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
          <div class="status-strip">
            <div class="status-item">
              <span>数据状态</span>
              <strong><i class="status-dot"></i><span id="dataLoadStatus">数据加载中</span></strong>
            </div>
            <div class="status-item">
              <span>最近刷新</span>
              <strong>{generated_at}</strong>
            </div>
            <div class="status-item">
              <span>统计周期</span>
              <strong>{statistics_window_label}</strong>
            </div>
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
          <h2 class="section-title">头部算力地址</h2>
          <div class="section-note">展示当前已发现正算力地址的头部集中度，首页默认压缩地址显示，明细表保留完整地址方便核验。</div>
        </div>
      </div>
      <div class="top-grid" id="topGrid"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2 class="section-title">前 15 名横向分布</h2>
          <div class="section-note">按当前算力排序展示头部地址断层，快速判断头部集中度变化。</div>
        </div>
      </div>
      <div class="bar-list" id="barList"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2 class="section-title">榜单明细（前 100）</h2>
          <div class="section-note">支持搜索地址、快速筛选和列排序。页面展示前 100 名，候选钱包和正算力钱包为本轮扫描口径。</div>
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

    const formatChineseAmount = (raw, decimals = 3) => {{
      raw = Number(raw || 0);
      if (raw >= 1e12) return (raw / 1e12).toFixed(decimals) + '万亿';
      if (raw >= 1e8) return (raw / 1e8).toFixed(decimals) + '亿';
      if (raw >= 1e4) return (raw / 1e4).toFixed(decimals) + '万';
      return Number.isInteger(raw) ? String(raw) : raw.toFixed(decimals);
    }};
    const formatUnits = (raw) => formatChineseAmount(raw);
    const formatCoverage = (value) => (value * 100).toFixed(2) + '%';
    const formatGeneratedAt = (ts) => new Date(ts * 1000).toLocaleString('zh-CN', {{ hour12: false }});
    const formatCount = (value) => formatChineseAmount(value, 3);
    const formatMaybeUnits = (value) => (value === null || value === undefined) ? '—' : formatUnits(value);
    const displayAddress = (address) => address ? `${{address.slice(0, 8)}}...${{address.slice(-6)}}` : '—';
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
          label: '总产量',
          value: meta.emission_total_supply_cap_display || '2000亿',
          help: '官网经济模型口径：总量 2000 亿枚，永不增发。'
        }},
        {{
          label: '每日总产币量',
          value: meta.emission_daily_total_display || '2.232亿/日',
          help: '按官网半衰期公式计算：当前周期产量 ÷ 448 天。'
        }},
        {{
          label: '矿工日产币量',
          value: meta.emission_daily_miner_display || '1.674亿/日',
          help: '官网规则产量分配为矿工 75%、节点 25%，这里展示矿工侧每日产出。'
        }},
        {{
          label: '节点日产币量',
          value: meta.emission_daily_node_display || '0.558亿/日',
          help: '官网规则产量分配为矿工 75%、节点 25%，这里展示节点侧每日产出。'
        }},
        {{
          label: '单币日需算力',
          value: meta.power_required_per_mars_daily_display || '—',
          help: '全网总算力 ÷ 矿工日产币量，表示每天产出 1 枚 MARS 所需的估算算力。'
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
          label: `统计日新增地址数量${{meta.statistics_day_label ? ' · ' + meta.statistics_day_label : ''}}`,
          value: meta.statistics_window_new_candidate_address_count === null || meta.statistics_window_new_candidate_address_count === undefined ? '—' : `${{formatCount(meta.statistics_window_new_candidate_address_count)}} 个`,
          help: `按北京时间统计日（${{meta.statistics_window_label || '00:00 至次日 00:00'}}）统计：首次出现在 POWER 合约日志中的候选地址数量。`
        }},
        {{
          label: `统计日活跃地址数量${{meta.statistics_day_label ? ' · ' + meta.statistics_day_label : ''}}`,
          value: meta.statistics_window_active_wallet_address_count === null || meta.statistics_window_active_wallet_address_count === undefined ? '—' : `${{formatCount(meta.statistics_window_active_wallet_address_count)}} 个`,
          help: `按北京时间统计日（${{meta.statistics_window_label || '00:00 至次日 00:00'}}）统计：该窗口内在 POWER 合约日志中出现过的钱包地址数量。`
        }},
        {{
          label: `统计日新增总算力${{meta.statistics_day_label ? ' · ' + meta.statistics_day_label : ''}}`,
          value: formatMaybeUnits(meta.today_new_power),
          help: `按北京时间统计日（${{meta.statistics_window_label || '00:00 至次日 00:00'}}）统计：当前全网总算力减去上一统计日合约历史总算力。`
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
          <div class="top-power">${{formatUnits(row.power_num)}}</div>
          <div class="top-address" title="${{row.address}}">${{displayAddress(row.address)}}</div>
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
          <div class="bar-label" title="${{row.address}}">${{displayAddress(row.address)}}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${{(row.power_num / maxPower) * 100}}%"></div></div>
          <div class="bar-value">${{formatUnits(row.power_num)}}</div>
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
        ['over10b', '≥ 100亿']
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
        power: `<span class="pill">${{formatUnits(row.power_num)}}</span>`,
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
        `统计周期：${{meta.statistics_window_label || '北京时间 00:00 至次日 00:00'}}。` +
        `本轮覆盖率 ${{formatCoverage(meta.discovered_power_coverage)}}，目标阈值 ${{formatCoverage(coverageTarget)}}，` +
        `${{targetMet ? '已达标' : '未达标'}}。说明：候选钱包 ${{formatCount(meta.candidate_count)}} 个，正算力钱包 ${{formatCount(meta.positive_power_count)}} 个；` +
        `这是一份基于公开 explorer API、官方 RPC 和合约日志生成的 best effort 榜单，不是官方后端直接导出的全量榜。`;
      const loadStatus = document.getElementById('dataLoadStatus');
      if (loadStatus) loadStatus.textContent = '数据已加载';
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


SCROLL_DASHBOARD_CSS = r"""
:root {
  --bg: #030612;
  --bg2: #071124;
  --panel: rgba(9, 16, 31, 0.68);
  --panel2: rgba(14, 24, 46, 0.86);
  --line: rgba(125, 225, 255, 0.16);
  --line2: rgba(125, 225, 255, 0.32);
  --text: #f7fbff;
  --muted: #91a2bd;
  --muted2: #64728b;
  --cyan: #52efff;
  --blue: #7c88ff;
  --green: #75f3a9;
  --amber: #ffd37e;
  --pink: #ff79c7;
  --font: "Avenir Next", "SF Pro Display", "PingFang SC", "Microsoft YaHei", sans-serif;
  --mono: "SFMono-Regular", "JetBrains Mono", monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--bg); color: var(--text); }
body {
  margin: 0;
  min-height: 100vh;
  font-family: var(--font);
  background:
    radial-gradient(circle at 20% 7%, rgba(82,239,255,.18), transparent 28%),
    radial-gradient(circle at 88% 0%, rgba(124,136,255,.22), transparent 29%),
    linear-gradient(180deg, #030612, #071124 48%, #030612);
  overflow-x: hidden;
}
body:before {
  content: "";
  position: fixed;
  inset: -12%;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(125,225,255,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(125,225,255,.05) 1px, transparent 1px);
  background-size: 76px 76px;
  transform: perspective(760px) rotateX(61deg) translateY(-150px);
  animation: grid 18s linear infinite;
  opacity: .72;
}
body:after {
  content: "";
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(620px circle at var(--mx,50%) var(--my,20%), rgba(82,239,255,.14), transparent 42%),
    radial-gradient(760px circle at calc(100% - var(--mx,50%)) 18%, rgba(124,136,255,.11), transparent 46%);
  mix-blend-mode: screen;
  opacity: .86;
  transition: opacity .3s ease;
}
@keyframes grid { to { background-position: 0 76px, 76px 0; } }
.scroll-progress {
  position: fixed;
  left: 0;
  top: 0;
  z-index: 80;
  width: 100%;
  height: 3px;
  background: linear-gradient(90deg, var(--cyan), var(--blue), var(--pink));
  transform: scaleX(var(--progress, 0));
  transform-origin: left center;
  box-shadow: 0 0 24px rgba(82,239,255,.75);
}
.shell { position: relative; z-index: 1; width: min(1440px, calc(100vw - 44px)); margin: 0 auto; }
.topbar {
  position: sticky;
  top: 14px;
  z-index: 20;
  margin: 14px auto 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border: 1px solid rgba(125,225,255,.12);
  border-radius: 999px;
  background: rgba(5,10,22,.58);
  backdrop-filter: blur(18px);
  padding: 10px 12px 10px 16px;
  box-shadow: 0 18px 50px rgba(0,0,0,.28);
}
.topbar:after {
  content: "";
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  padding: 1px;
  background: linear-gradient(120deg, transparent, rgba(82,239,255,.45), rgba(124,136,255,.36), transparent);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  opacity: .42;
  pointer-events: none;
  animation: borderFlow 7s linear infinite;
}
.brand { display: flex; gap: 11px; align-items: center; font-weight: 900; }
.mark {
  width: 30px;
  height: 30px;
  border-radius: 11px;
  background: conic-gradient(from 210deg, var(--cyan), var(--blue), var(--pink), var(--cyan));
  box-shadow: 0 0 30px rgba(82,239,255,.4);
  animation: markSpin 10s linear infinite;
}
.top-actions { display: flex; align-items: center; gap: 8px; }
.nav { display: flex; gap: 8px; }
.nav a, .lang-toggle, .share-trigger {
  text-decoration: none;
  color: #adbad1;
  border: 1px solid transparent;
  border-radius: 999px;
  padding: 9px 12px;
  font-size: 13px;
  font-weight: 850;
  font-family: inherit;
  line-height: 1;
  transition: transform .24s ease, color .24s ease, border-color .24s ease, background .24s ease, box-shadow .24s ease;
}
.lang-toggle {
  min-width: 52px;
  cursor: pointer;
  color: #d9faff;
  background: rgba(82,239,255,.075);
  border-color: rgba(82,239,255,.20);
}
.share-trigger {
  cursor: pointer;
  color: #03121b;
  border-color: transparent;
  background: linear-gradient(135deg, var(--cyan), var(--blue));
}
.nav a:hover, .lang-toggle:hover, .share-trigger:hover {
  color: white;
  border-color: var(--line2);
  background: rgba(82,239,255,.08);
  transform: translateY(-2px);
  box-shadow: 0 12px 30px rgba(82,239,255,.12);
}
.hero {
  position: relative;
  min-height: calc(100vh - 72px);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 520px;
  gap: 46px;
  align-items: center;
  padding: 46px 0 74px;
}
.hero:before {
  content: "";
  position: absolute;
  z-index: -1;
  left: -8%;
  top: 8%;
  width: 44vw;
  height: 44vw;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(82,239,255,.15), rgba(82,239,255,.04) 38%, transparent 64%);
  filter: blur(4px);
  animation: orbDrift 13s ease-in-out infinite;
}
.hero-copy { position: relative; }
.chip {
  display: inline-flex;
  border: 1px solid rgba(82,239,255,.28);
  background: rgba(82,239,255,.08);
  color: #b7f8ff;
  border-radius: 999px;
  padding: 8px 13px;
  font-size: 12px;
  font-weight: 950;
  letter-spacing: .08em;
}
h1 {
  font-size: clamp(64px, 7.4vw, 112px);
  line-height: .86;
  letter-spacing: -.078em;
  margin: 24px 0 18px;
  background: linear-gradient(110deg, #fff 0%, #dff8ff 30%, #8ef7ff 46%, #ffffff 58%, #b8c1ff 78%, #fff 100%);
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  text-shadow: 0 20px 70px rgba(82,239,255,.18);
  animation: titleShine 8s ease-in-out infinite;
}
.lead { font-size: 19px; line-height: 1.7; color: #c0cadf; max-width: 760px; margin: 0; }
.hero-actions { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-top: 30px; }
.hero-note {
  color: #91a5c0;
  line-height: 1.65;
  max-width: 520px;
  margin-left: 6px;
}
.btn {
  height: 50px;
  padding: 0 18px;
  border-radius: 16px;
  border: 1px solid var(--line2);
  display: inline-flex;
  align-items: center;
  background: rgba(255,255,255,.055);
  font-weight: 950;
  color: #eef7ff;
  text-decoration: none;
  font-family: inherit;
  cursor: pointer;
}
.btn.hot {
  border: 0;
  color: #03111a;
  background: linear-gradient(135deg, var(--cyan), var(--blue));
  background-size: 220% 100%;
  box-shadow: 0 18px 48px rgba(82,239,255,.24);
  animation: buttonFlow 5.5s ease-in-out infinite;
}
.chip, .btn, .track span, .metric, .fcard, .rank-card, .panel, .command { isolation: isolate; }
.chip, .btn {
  position: relative;
  overflow: hidden;
  transition: transform .28s cubic-bezier(.2,.85,.2,1), border-color .28s ease, box-shadow .28s ease, background .28s ease;
}
.chip:before, .btn:before {
  content: "";
  position: absolute;
  inset: -2px;
  background: linear-gradient(110deg, transparent 0 30%, rgba(255,255,255,.55) 48%, transparent 66%);
  transform: translateX(-120%);
  opacity: .55;
  pointer-events: none;
}
.chip:hover:before, .btn:hover:before { animation: sheenPass 1.05s ease; }
.btn:hover, .chip:hover {
  transform: translateY(-3px);
  border-color: rgba(82,239,255,.62);
  box-shadow: 0 18px 44px rgba(82,239,255,.18), inset 0 1px 0 rgba(255,255,255,.14);
}
.btn:active { transform: translateY(1px) scale(.985); }
.scroll-hint { margin-top: 56px; display: flex; align-items: center; gap: 12px; color: #73839c; font-size: 13px; }
.mouse { width: 26px; height: 42px; border: 1px solid var(--line2); border-radius: 999px; position: relative; }
.mouse:after {
  content: "";
  position: absolute;
  left: 50%;
  top: 8px;
  width: 4px;
  height: 8px;
  border-radius: 99px;
  background: var(--cyan);
  transform: translateX(-50%);
  animation: wheel 1.5s ease infinite;
}
@keyframes wheel { to { top: 22px; opacity: 0; } }
.command {
  position: relative;
  min-height: 560px;
  border: 1px solid var(--line);
  border-radius: 34px;
  background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.025)), rgba(7,13,27,.74);
  box-shadow: 0 28px 100px rgba(0,0,0,.42), inset 0 1px 0 rgba(255,255,255,.08);
  overflow: hidden;
  backdrop-filter: blur(22px);
}
.command:before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(110deg, transparent 0 44%, rgba(82,239,255,.10) 50%, transparent 58%);
  animation: scanLight 7s ease-in-out infinite;
}
.command:after {
  content: "";
  position: absolute;
  inset: 12px;
  border-radius: 28px;
  border: 1px solid rgba(82,239,255,.13);
  box-shadow: inset 0 0 40px rgba(82,239,255,.06);
  pointer-events: none;
}
@keyframes scanLight { 0%,35% { transform: translateX(-70%); opacity: 0; } 58% { opacity: 1; } 100% { transform: translateX(70%); opacity: 0; } }
.radar {
  position: absolute;
  inset: 36px;
  border: 1px solid rgba(125,225,255,.14);
  border-radius: 28px;
  background: radial-gradient(circle at center, rgba(82,239,255,.16), rgba(82,239,255,.035) 46%, transparent);
}
.radar:before {
  content: "";
  position: absolute;
  inset: 52px;
  border-radius: 50%;
  border: 1px solid rgba(82,239,255,.22);
  box-shadow: 0 0 0 58px rgba(82,239,255,.035), 0 0 0 120px rgba(124,136,255,.032);
}
.radar:after {
  content: "";
  position: absolute;
  left: 50%;
  top: 50%;
  width: 47%;
  height: 2px;
  transform-origin: left center;
  background: linear-gradient(90deg, var(--cyan), transparent);
  filter: drop-shadow(0 0 12px var(--cyan));
  animation: radar 5.4s linear infinite;
}
@keyframes radar { to { transform: rotate(360deg); } from { transform: rotate(0deg); } }
.core { position: absolute; left: 50%; top: 47%; transform: translate(-50%, -50%); text-align: center; }
.core b { font-size: 58px; letter-spacing: -.07em; }
.core span { display: block; color: #a0f8c4; font-size: 12px; font-weight: 950; }
.node { position: absolute; width: 10px; height: 10px; border-radius: 50%; box-shadow: 0 0 24px currentColor; animation: float 4s ease-in-out infinite; }
.n1 { left: 22%; top: 31%; color: var(--cyan); }
.n2 { right: 23%; top: 26%; color: var(--blue); }
.n3 { right: 18%; bottom: 27%; color: var(--green); }
@keyframes float { 50% { transform: translateY(-8px) scale(1.18); } }
.mini { position: absolute; left: 28px; right: 28px; bottom: 28px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.mini div { border: 1px solid var(--line); border-radius: 18px; background: rgba(4,9,20,.64); padding: 14px; }
.mini span { display: block; color: #8e9db7; font-size: 12px; }
.mini b { font-size: 24px; letter-spacing: -.03em; }
.marquee {
  position: sticky;
  top: 76px;
  z-index: 15;
  margin-top: -52px;
  height: 54px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(10,18,34,.76);
  backdrop-filter: blur(16px);
  overflow: hidden;
  display: flex;
  align-items: center;
  box-shadow: 0 18px 48px rgba(0,0,0,.24);
}
.track { display: flex; gap: 14px; white-space: nowrap; animation: slide 34s linear infinite; }
.marquee:hover .track { animation-play-state: paused; }
.track span {
  display: inline-flex;
  gap: 10px;
  align-items: center;
  border: 1px solid rgba(125,225,255,.16);
  background: rgba(255,255,255,.045);
  border-radius: 999px;
  padding: 9px 14px;
  color: #9badc8;
  font-size: 12px;
  font-weight: 950;
  letter-spacing: .06em;
}
.track b { color: white; font-family: var(--mono); letter-spacing: 0; }
@keyframes slide { to { transform: translateX(-50%); } }
.section { position: relative; padding: 118px 0; }
.section:before {
  content: "";
  position: absolute;
  left: 50%;
  top: 10%;
  width: 58vw;
  height: 58vw;
  transform: translateX(-50%);
  z-index: -1;
  background: radial-gradient(circle, rgba(124,136,255,.09), transparent 62%);
  filter: blur(12px);
  opacity: .7;
  pointer-events: none;
}
.section-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 26px; }
.kicker { color: #9cf6ff; font-weight: 950; letter-spacing: .12em; font-size: 12px; text-transform: uppercase; }
h2 { font-size: clamp(38px, 4.4vw, 70px); line-height: .92; letter-spacing: -.065em; margin: 10px 0 0; }
.section-head p { max-width: 470px; color: #9dafc9; line-height: 1.65; margin: 0; }
.panel {
  border: 1px solid var(--line);
  border-radius: 30px;
  background: rgba(8,15,30,.72);
  backdrop-filter: blur(20px);
  box-shadow: 0 24px 70px rgba(0,0,0,.28);
}
.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.metric {
  min-height: 214px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 24px;
  background: linear-gradient(180deg, rgba(30,46,76,.9), rgba(8,15,30,.92));
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  cursor: pointer;
}
.metric:focus-visible {
  outline: 2px solid rgba(82,239,255,.72);
  outline-offset: 4px;
}
.metric:after {
  content: "";
  position: absolute;
  right: -32px;
  top: -32px;
  width: 102px;
  height: 102px;
  border-radius: 50%;
  background: rgba(82,239,255,.11);
}
.metric > span { color: #aebbd2; font-size: 13px; }
.metric b { display: block; font-size: 34px; margin-top: 20px; letter-spacing: -.055em; }
.price-stack {
  display: grid;
  gap: 6px;
  margin-top: 10px;
  color: #9eb0cb;
  font-size: 12px;
  line-height: 1.25;
}
.price-stack span {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  border-bottom: 1px solid rgba(125,225,255,.10);
  padding-bottom: 5px;
}
.price-stack strong {
  color: #f5fbff;
  font-family: var(--mono);
  font-size: 13px;
}
.metric small { display: block; color: #8292ad; font-size: 12px; margin-top: 12px; }
.metric-trend {
  position: relative;
  z-index: 1;
  margin-top: auto;
  padding-top: 14px;
}
.metric-trend svg {
  display: block;
  width: 100%;
  height: 48px;
  overflow: visible;
}
.metric-trend path.area { fill: rgba(82,239,255,.16); opacity: .9; }
.metric-trend path.line {
  fill: none;
  stroke: var(--cyan);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
  filter: drop-shadow(0 0 10px rgba(82,239,255,.35));
}
.metric-trend circle { fill: var(--green); filter: drop-shadow(0 0 9px rgba(129,245,178,.55)); }
.metric-trend-label {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 4px;
  color: #71839f;
  font-size: 11px;
  line-height: 1.25;
}
.metric-trend.is-sampling path.line { stroke-dasharray: 5 7; opacity: .72; }
.metric-trend.is-sampling circle { display: none; }
.trend-modal[hidden] { display: none; }
.trend-modal {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(2, 7, 18, .72);
  backdrop-filter: blur(18px);
}
.trend-panel {
  width: min(920px, calc(100vw - 38px));
  max-height: calc(100vh - 48px);
  overflow: auto;
  border: 1px solid rgba(82,239,255,.28);
  border-radius: 28px;
  background:
    radial-gradient(circle at 82% 0%, rgba(82,239,255,.16), transparent 34%),
    linear-gradient(180deg, rgba(15,27,50,.97), rgba(5,12,26,.98));
  box-shadow: 0 34px 120px rgba(0,0,0,.58), inset 0 1px 0 rgba(255,255,255,.08);
}
.trend-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  padding: 24px 24px 16px;
}
.trend-title span {
  color: var(--amber);
  font-size: 12px;
  font-weight: 950;
  letter-spacing: .12em;
}
.trend-title h3 {
  margin: 7px 0 8px;
  font-size: 38px;
  line-height: 1;
  letter-spacing: -.055em;
}
.trend-title p { margin: 0; color: #91a4bf; line-height: 1.6; }
.trend-close {
  width: 38px;
  height: 38px;
  border: 1px solid rgba(82,239,255,.25);
  border-radius: 999px;
  color: #ddf8ff;
  background: rgba(255,255,255,.055);
  cursor: pointer;
  font-size: 22px;
  line-height: 1;
}
.trend-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 0 24px 16px;
}
.trend-controls button {
  min-height: 34px;
  padding: 0 13px;
  border: 1px solid rgba(82,239,255,.22);
  border-radius: 999px;
  color: #bfefff;
  background: rgba(255,255,255,.055);
  font: inherit;
  font-size: 12px;
  font-weight: 900;
  cursor: pointer;
}
.trend-controls button.is-active {
  color: #04111a;
  border-color: transparent;
  background: linear-gradient(135deg, var(--cyan), var(--blue));
}
.trend-chart {
  position: relative;
  margin: 0 24px;
  min-height: 300px;
  border: 1px solid rgba(82,239,255,.16);
  border-radius: 22px;
  background:
    linear-gradient(rgba(82,239,255,.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(82,239,255,.045) 1px, transparent 1px),
    rgba(4, 12, 27, .68);
  background-size: 100% 25%, 12.5% 100%, auto;
  overflow: hidden;
  cursor: crosshair;
}
.trend-chart svg {
  display: block;
  width: 100%;
  height: 300px;
}
.trend-chart .bar { fill: rgba(214, 86, 255, .55); }
.trend-chart .area { fill: rgba(82,239,255,.16); }
.trend-chart .line {
  fill: none;
  stroke: var(--cyan);
  stroke-width: 4;
  stroke-linecap: round;
  stroke-linejoin: round;
  filter: drop-shadow(0 0 14px rgba(82,239,255,.38));
}
.trend-chart .cursor-line { stroke: rgba(255,255,255,.66); stroke-width: 1.5; stroke-dasharray: 5 6; }
.trend-chart .cursor-dot { fill: var(--green); filter: drop-shadow(0 0 12px rgba(129,245,178,.64)); }
.trend-chart .axis-line { stroke: rgba(255,255,255,.12); stroke-width: 1; }
.trend-chart .value-label {
  fill: #eaf7ff;
  font-size: 13px;
  font-weight: 900;
  paint-order: stroke;
  stroke: rgba(4, 12, 27, .92);
  stroke-width: 5px;
  stroke-linejoin: round;
}
.trend-chart .date-label {
  fill: #7f91aa;
  font-size: 12px;
  font-weight: 850;
}
.trend-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #9fb0c9;
  font-weight: 900;
  text-align: center;
  padding: 20px;
}
.trend-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  padding: 16px 24px 24px;
}
.trend-stat {
  min-width: 0;
  padding: 14px;
  border: 1px solid rgba(82,239,255,.14);
  border-radius: 16px;
  background: rgba(255,255,255,.045);
}
.trend-stat span {
  display: block;
  color: #91a4bf;
  font-size: 12px;
  font-weight: 900;
}
.trend-stat b {
  display: block;
  margin-top: 8px;
  font-size: 22px;
  letter-spacing: -.035em;
  word-break: break-word;
}
.trend-stat small {
  display: block;
  margin-top: 4px;
  color: #72849d;
  font-size: 11px;
  line-height: 1.35;
}
.trend-foot {
  margin: -8px 24px 24px;
  color: #899bb6;
  font-size: 12px;
  line-height: 1.55;
}
.growth-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.growth-card {
  padding: 26px;
  border: 1px solid var(--line);
  border-radius: 30px;
  background:
    radial-gradient(circle at 88% 12%, rgba(82,239,255,.18), transparent 34%),
    linear-gradient(180deg, rgba(22,38,68,.9), rgba(8,15,30,.92));
  overflow: hidden;
}
.growth-card h3 { margin: 10px 0 24px; font-size: 38px; letter-spacing: -.055em; }
.growth-card .line { border-color: rgba(82,239,255,.14); }
.growth-card .line b { font-size: 20px; }
.equation-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.equation-grid .fcard {
  min-height: 222px;
  background:
    radial-gradient(circle at 86% 10%, rgba(255,211,126,.18), transparent 34%),
    linear-gradient(180deg, rgba(22,38,68,.88), rgba(8,15,30,.92));
}
.equation-grid .fcard strong { font-size: 46px; margin: 36px 0 10px; }
.burn-calculator {
  margin-top: 18px;
  display: grid;
  grid-template-columns: minmax(0, .95fr) minmax(0, 1.25fr);
  gap: 18px;
  padding: 24px;
  border: 1px solid rgba(255,211,126,.22);
  border-radius: 28px;
  background:
    radial-gradient(circle at 10% 0%, rgba(255,211,126,.18), transparent 34%),
    linear-gradient(135deg, rgba(26,34,54,.94), rgba(9,15,29,.94));
  overflow: hidden;
}
.burn-copy h3 { margin: 10px 0 12px; font-size: 34px; letter-spacing: -.045em; }
.burn-copy p { margin: 0; color: #92a4bf; line-height: 1.65; }
.burn-form { display: grid; gap: 14px; }
.burn-stats { display: grid; gap: 8px; }
.burn-stats .line { padding: 10px 0; }
.burn-stats .line b { font-size: 16px; text-align: right; overflow-wrap: anywhere; }
.burn-input-row { display: grid; gap: 8px; }
.burn-input-row label { color: #becbe0; font-weight: 950; font-size: 13px; }
.burn-input-row input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid rgba(255,211,126,.26);
  border-radius: 14px;
  padding: 14px 16px;
  color: #fff8e1;
  background: rgba(255,255,255,.05);
  font: 900 18px/1 var(--mono);
  outline: none;
}
.burn-input-row input:focus {
  border-color: rgba(255,211,126,.72);
  box-shadow: 0 0 0 3px rgba(255,211,126,.12);
}
.burn-result {
  border: 1px solid rgba(82,239,255,.18);
  border-radius: 18px;
  padding: 16px;
  background: rgba(82,239,255,.07);
}
.burn-result span { display: block; color: #9fddec; font-size: 12px; font-weight: 950; }
.burn-result b { display: block; margin-top: 8px; font-size: 30px; color: #fff; letter-spacing: -.04em; overflow-wrap: anywhere; }
.burn-result small { display: block; margin-top: 8px; color: #89a0bb; line-height: 1.45; }
.equation-note {
  margin: 18px 0 0;
  color: #8fa2bd;
  font-size: 13px;
  line-height: 1.65;
}
.funnel { display: grid; grid-template-columns: 1fr 72px 1fr 72px 1fr; align-items: center; gap: 12px; }
.fcard { min-height: 230px; padding: 24px; border: 1px solid var(--line); border-radius: 26px; background: rgba(14,24,46,.82); }
.fcard label { display: flex; justify-content: space-between; color: #abb9d0; font-weight: 950; }
.fcard strong { display: block; font-size: 56px; letter-spacing: -.065em; margin: 42px 0 10px; }
.fcard small { display: block; color: #8292ad; line-height: 1.55; }
.arrow { height: 2px; background: linear-gradient(90deg, var(--cyan), transparent); position: relative; overflow: hidden; }
.arrow:after { content: ""; position: absolute; right: 0; top: -4px; border-left: 8px solid var(--cyan); border-top: 5px solid transparent; border-bottom: 5px solid transparent; }
.arrow:before { content: ""; position: absolute; inset: -8px; background: linear-gradient(90deg, transparent, rgba(255,255,255,.7), transparent); animation: arrowEnergy 2.4s ease-in-out infinite; }
.rank-grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
.rank-section { padding-top: 48px; }
.rank-card.is-page-hidden { display: none; }
.rank-card {
  min-height: auto;
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr) 220px;
  align-items: center;
  gap: 18px;
  padding: 16px 20px;
  border: 1px solid var(--line);
  border-radius: 22px;
  background: rgba(14,24,46,.82);
}
.rank-top { display: contents; }
.rank-top em { font-style: normal; font-family: var(--mono); color: #cbd8ef; }
.rank-top strong { grid-column: 3; grid-row: 1; justify-self: end; font-size: 24px; }
.rank-card code {
  display: block;
  grid-column: 2;
  grid-row: 1;
  margin: 0;
  color: #dfe8ff;
  font-family: var(--mono);
  font-size: 15px;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.rank-bar { display: block; height: 8px; border-radius: 999px; background: rgba(255,255,255,.07); overflow: hidden; }
.rank-bar i { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--cyan), var(--blue)); box-shadow: 0 0 18px rgba(82,239,255,.36); position: relative; overflow: hidden; }
.rank-bar i:after { content: ""; position: absolute; inset: 0; background: linear-gradient(90deg, transparent, rgba(255,255,255,.78), transparent); transform: translateX(-120%); animation: barPulse 2.8s ease-in-out infinite; }
.rank-bar { display: none; }
.rank-pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
  flex-wrap: wrap;
}
.rank-pages { display: flex; gap: 6px; flex-wrap: wrap; justify-content: center; }
.rank-page-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 42px;
  min-width: 42px;
  border: 1px solid rgba(82,239,255,.38);
  border-radius: 999px;
  padding: 0 18px;
  color: #cfefff;
  background: rgba(82,239,255,.08);
  box-shadow: 0 18px 42px rgba(82,239,255,.16);
  font: 950 13px var(--font);
  cursor: pointer;
}
.rank-page-button.is-active {
  color: #03121b;
  background: linear-gradient(135deg, var(--cyan), var(--blue));
}
.rank-page-button:disabled { cursor: default; opacity: .42; box-shadow: none; }
.rank-page-button:not(:disabled):hover { filter: brightness(1.06); transform: translateY(-1px); }
.rank-count { flex: 0 0 100%; color: #95a8c4; font-size: 13px; font-weight: 850; text-align: center; }
.paid-download {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 520px;
  gap: 18px;
  margin-top: 22px;
  padding: 22px;
  border: 1px solid rgba(255,211,126,.28);
  border-radius: 26px;
  background:
    radial-gradient(circle at 8% 0%, rgba(255,211,126,.16), transparent 34%),
    linear-gradient(180deg, rgba(25,35,58,.9), rgba(8,15,30,.9));
  box-shadow: 0 24px 70px rgba(0,0,0,.28);
}
.paid-copy span { color: var(--amber); font-size: 12px; font-weight: 950; letter-spacing: .12em; }
.paid-copy h3 { margin: 10px 0 12px; font-size: 36px; line-height: 1; letter-spacing: -.045em; }
.paid-copy p { margin: 0; max-width: 620px; color: #b7c4d9; line-height: 1.75; }
.paid-rules {
  display: grid;
  gap: 9px;
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
  color: #d2def0;
  font-size: 13px;
  line-height: 1.65;
}
.paid-rules li { position: relative; padding-left: 18px; }
.paid-rules li:before {
  content: "";
  position: absolute;
  left: 0;
  top: .72em;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--amber);
  box-shadow: 0 0 14px rgba(255,211,126,.7);
}
.paid-box { display: grid; gap: 12px; }
.paid-amount, .paid-address, .paid-controls, .paid-tx {
  display: grid;
  grid-template-columns: 116px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}
.paid-amount span, .paid-address span { color: #9fb0c9; font-size: 13px; font-weight: 850; }
.paid-amount b { color: var(--amber); font-size: 28px; letter-spacing: -.035em; }
.paid-address code {
  min-width: 0;
  color: #f4f8ff;
  font-family: var(--mono);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.paid-address button, .paid-controls button, .paid-tx button, .paid-controls select, .paid-tx input {
  min-height: 42px;
  border: 1px solid rgba(82,239,255,.28);
  border-radius: 14px;
  color: #eaf7ff;
  background: rgba(255,255,255,.06);
  font: 900 13px var(--font);
}
.paid-address button, .paid-controls button, .paid-tx button { cursor: pointer; padding: 0 15px; }
.paid-controls button, .paid-tx button {
  color: #03121b;
  border: 0;
  background: linear-gradient(135deg, var(--cyan), var(--blue));
}
.paid-controls select, .paid-tx input { padding: 0 12px; width: 100%; }
.paid-controls { grid-template-columns: 116px minmax(0, 1fr); }
.paid-tx { grid-template-columns: minmax(0, 1fr) auto; }
.paid-status { color: #95a8c4; font-size: 13px; line-height: 1.55; }
.paid-status.is-error { color: #ffb7b7; }
.paid-status.is-ok { color: #9df4c3; }
.paid-link { display: none; color: var(--cyan); font-size: 13px; font-weight: 950; text-decoration: none; }
.paid-link.is-ready { display: inline-flex; }
.paid-download button:disabled, .paid-download input:disabled { opacity: .45; cursor: default; }
.telemetry { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.timeline { padding: 24px; }
.line { display: flex; justify-content: space-between; gap: 14px; padding: 16px 0; border-bottom: 1px solid var(--line); }
.line:last-child { border-bottom: 0; }
.line span { color: #aebbd1; }
.line b { font-family: var(--mono); text-align: right; }
.risk { padding: 24px; }
.risk h3 { font-size: 26px; margin: 0 0 14px; }
.risk p { color: #aab8d0; line-height: 1.85; margin: 0; }
.alert {
  margin-top: 18px;
  padding: 15px 17px;
  border: 1px solid rgba(255,211,126,.28);
  border-radius: 20px;
  background: rgba(255,211,126,.08);
  color: #ffe4ad;
  line-height: 1.65;
}
.footer { padding: 70px 0 54px; color: #6f7e96; text-align: center; font-size: 13px; }
.metric, .growth-card, .fcard, .rank-card, .panel, .command {
  position: relative;
  transition: transform .34s cubic-bezier(.2,.85,.2,1), box-shadow .34s ease, border-color .34s ease, background .34s ease;
  transform-style: preserve-3d;
}
.metric:before, .growth-card:before, .fcard:before, .rank-card:before, .panel:before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: -1;
  border-radius: inherit;
  background: radial-gradient(460px circle at var(--spotX,50%) var(--spotY,0%), rgba(82,239,255,.20), transparent 45%), linear-gradient(115deg, transparent 30%, rgba(255,255,255,.13), transparent 62%);
  opacity: 0;
  transition: opacity .28s ease;
  pointer-events: none;
}
.metric:hover, .growth-card:hover, .fcard:hover, .rank-card:hover, .panel:hover {
  transform: perspective(900px) rotateX(var(--tiltX,0deg)) rotateY(var(--tiltY,0deg)) translateY(-8px);
  border-color: rgba(82,239,255,.48);
  box-shadow: 0 26px 70px rgba(0,0,0,.42), 0 0 0 1px rgba(82,239,255,.16), 0 0 48px rgba(82,239,255,.12);
}
.metric:hover:before, .growth-card:hover:before, .fcard:hover:before, .rank-card:hover:before, .panel:hover:before { opacity: 1; }
.command:hover {
  transform: perspective(900px) rotateX(var(--tiltX,0deg)) rotateY(var(--tiltY,0deg)) translateY(-6px);
  border-color: rgba(82,239,255,.48);
  box-shadow: 0 34px 110px rgba(0,0,0,.5), 0 0 80px rgba(82,239,255,.14), inset 0 1px 0 rgba(255,255,255,.12);
}
.reveal {
  opacity: 0;
  transform: translateY(34px);
  transition: opacity .75s ease, transform .75s cubic-bezier(.2,.85,.2,1);
  transition-delay: var(--delay, 0ms);
}
.reveal.visible { opacity: 1; transform: none; }
.stagger > * { opacity: 0; transform: translateY(26px); transition: .7s ease; transition-delay: var(--delay, 0ms); }
.stagger.visible > * { opacity: 1; transform: none; }
@keyframes titleShine { 0%,100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }
@keyframes buttonFlow { 0%,100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }
@keyframes sheenPass { to { transform: translateX(120%); } }
@keyframes barPulse { 0%,35% { transform: translateX(-120%); opacity: 0; } 50% { opacity: .9; } 82%,100% { transform: translateX(120%); opacity: 0; } }
@keyframes arrowEnergy { 0% { transform: translateX(-120%); opacity: 0; } 45% { opacity: .85; } 100% { transform: translateX(120%); opacity: 0; } }
@keyframes orbDrift { 0%,100% { transform: translate3d(0,0,0) scale(1); } 50% { transform: translate3d(8%,7%,0) scale(1.08); } }
@keyframes borderFlow { to { filter: hue-rotate(360deg); } }
@keyframes markSpin { to { filter: hue-rotate(360deg); transform: rotate(360deg); } }
@media (max-width: 1120px) {
  .hero, .telemetry { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: repeat(2, 1fr); }
  .rank-grid { grid-template-columns: 1fr; }
  .paid-download { grid-template-columns: 1fr; }
  .burn-calculator { grid-template-columns: 1fr; }
  .funnel { grid-template-columns: 1fr; }
  .arrow { height: 36px; width: 2px; justify-self: center; background: linear-gradient(180deg, var(--cyan), transparent); }
  .arrow:after { right: -4px; top: auto; bottom: 0; transform: rotate(90deg); }
}
@media (max-width: 720px) {
  html, body { max-width: 100%; overflow-x: hidden; }
  body {
    background:
      radial-gradient(circle at 12% 0%, rgba(82,239,255,.20), transparent 34%),
      radial-gradient(circle at 96% 10%, rgba(124,136,255,.18), transparent 32%),
      linear-gradient(180deg, #030612, #071124 52%, #030612);
    -webkit-text-size-adjust: 100%;
  }
  body:before {
    background-size: 44px 44px;
    transform: perspective(640px) rotateX(64deg) translateY(-118px);
    opacity: .48;
  }
  .shell { width: min(calc(100vw - 28px), 360px); }
  .shell, .topbar, .hero, .hero-copy, .hero-actions, .metrics, .funnel, .rank-grid, .telemetry, .burn-calculator {
    max-width: 100%;
    min-width: 0;
  }
  .topbar {
    position: sticky;
    top: 10px;
    align-items: flex-start;
    border-radius: 22px;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
  }
  .brand { width: 100%; font-size: 16px; }
  .mark { width: 28px; height: 28px; border-radius: 10px; }
  .top-actions {
    width: 100%;
    align-items: center;
    gap: 8px;
  }
  .nav {
    flex: 1 1 auto;
    width: auto;
    gap: 8px;
    overflow-x: auto;
    flex-wrap: nowrap;
    padding-bottom: 2px;
    scrollbar-width: none;
  }
  .nav::-webkit-scrollbar { display: none; }
  .nav a {
    flex: 0 0 auto;
    padding: 8px 11px;
    font-size: 12px;
    background: rgba(255,255,255,.045);
    border-color: rgba(125,225,255,.12);
  }
  .lang-toggle, .share-trigger {
    flex: 0 0 auto;
    min-width: 48px;
    padding: 8px 10px;
    font-size: 12px;
    align-self: flex-start;
  }
  .hero {
    min-height: auto;
    grid-template-columns: 1fr;
    gap: 22px;
    padding: 32px 0 42px;
  }
  .chip { padding: 7px 11px; font-size: 11px; letter-spacing: .05em; }
  h1 {
    font-size: clamp(44px, 15vw, 58px);
    line-height: .9;
    letter-spacing: -.07em;
    margin: 18px 0 16px;
  }
  .lead {
    font-size: 16px;
    line-height: 1.72;
    width: 100%;
    max-width: none;
    color: #d0dcf0;
    overflow-wrap: anywhere;
  }
  .hero-actions {
    width: 100%;
    display: flex;
    align-items: stretch;
    flex-direction: column;
    gap: 10px;
    margin-top: 22px;
  }
  .hero-note {
    margin-left: 0;
    font-size: 12px;
    line-height: 1.55;
  }
  .hero-actions .btn {
    width: 100%;
    min-width: 0;
    height: 44px;
    justify-content: center;
    padding: 0 10px;
    border-radius: 15px;
    font-size: 12px;
    letter-spacing: -.02em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .hero-actions .btn.hot { grid-column: auto; }
  .hero-actions .mobile-secondary { display: none; }
  .scroll-hint { margin-top: 28px; font-size: 12px; align-items: flex-start; }
  .mouse { width: 22px; height: 34px; flex: 0 0 auto; }
  .command {
    min-height: 310px;
    border-radius: 26px;
    order: 2;
  }
  .radar { inset: 18px; border-radius: 22px; }
  .radar:before { inset: 42px; box-shadow: 0 0 0 34px rgba(82,239,255,.035), 0 0 0 78px rgba(124,136,255,.03); }
  .core { top: 44%; }
  .core b { font-size: 44px; }
  .mini { left: 14px; right: 14px; bottom: 14px; gap: 8px; }
  .mini div { padding: 10px; border-radius: 14px; }
  .mini span { font-size: 10px; }
  .mini b { font-size: 16px; }
  .marquee {
    position: relative;
    top: auto;
    margin-top: 0;
    height: 48px;
    border-radius: 18px;
  }
  .track { gap: 8px; animation-duration: 42s; }
  .track span { padding: 8px 11px; font-size: 11px; letter-spacing: .03em; }
  .section { padding: 72px 0; }
  .rank-section { padding-top: 34px; }
  .section-head {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
    margin-bottom: 18px;
  }
  .kicker { font-size: 11px; }
  h2 { font-size: clamp(34px, 11vw, 44px); line-height: .96; letter-spacing: -.055em; }
  .section-head p { font-size: 14px; line-height: 1.7; max-width: none; }
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }
  .metric {
    min-height: 132px;
    padding: 15px;
    border-radius: 19px;
  }
  .metric:after { width: 72px; height: 72px; right: -24px; top: -24px; }
  .metric span { font-size: 12px; line-height: 1.35; }
  .metric b { font-size: 25px; margin-top: 18px; line-height: 1.05; }
  .metric small { font-size: 11px; line-height: 1.45; margin-top: 9px; }
  .trend-modal { padding: 12px; align-items: end; }
  .trend-panel {
    width: calc(100vw - 24px);
    max-width: 366px;
    max-height: calc(100vh - 24px);
    border-radius: 22px;
    justify-self: start;
  }
  .trend-head {
    padding: 18px 16px 12px;
    gap: 12px;
  }
  .trend-title h3 { font-size: 29px; }
  .trend-title p { font-size: 12px; }
  .trend-controls {
    padding: 0 16px 12px;
    gap: 7px;
  }
  .trend-controls button {
    flex: 1 1 calc(50% - 7px);
  }
  .trend-chart {
    margin: 0 16px;
    min-height: 230px;
    border-radius: 17px;
  }
  .trend-chart svg { height: 230px; }
  .trend-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    padding: 12px 16px 18px;
  }
  .trend-stat { padding: 12px; border-radius: 14px; }
  .trend-stat b { font-size: 18px; }
  .trend-foot { margin: -4px 16px 18px; }
  .growth-grid { grid-template-columns: 1fr; gap: 12px; }
  .equation-grid { grid-template-columns: 1fr; gap: 10px; }
  .growth-card { padding: 18px; border-radius: 22px; }
  .growth-card h3 { font-size: 28px; margin-bottom: 12px; }
  .growth-card .line b { font-size: 16px; }
  .funnel { gap: 10px; }
  .fcard {
    min-height: auto;
    padding: 18px;
    border-radius: 21px;
  }
  .fcard label { font-size: 13px; }
  .fcard strong { font-size: 40px; margin: 26px 0 10px; }
  .equation-grid .fcard strong { font-size: 40px; margin: 26px 0 10px; }
  .fcard small { font-size: 12px; }
  .arrow { height: 28px; opacity: .75; }
  .rank-grid { grid-template-columns: 1fr; gap: 10px; }
  .rank-card {
    min-height: auto;
    padding: 15px;
    border-radius: 19px;
  }
  .rank-card {
    grid-template-columns: 54px minmax(0, 1fr);
    gap: 10px;
    padding: 14px;
  }
  .rank-top em { grid-column: 1; }
  .rank-top strong { grid-column: 2; justify-self: end; }
  .rank-card code { grid-column: 1 / -1; grid-row: 2; font-size: 12px; }
  .rank-top strong { font-size: 21px; }
  .rank-card code { margin: 0; font-size: 12px; }
  .rank-pagination {
    align-items: stretch;
    flex-direction: column;
    margin-top: 14px;
  }
  .rank-page-button { width: 100%; }
  .rank-count { text-align: center; }
  .paid-download {
    padding: 16px;
    border-radius: 21px;
    gap: 15px;
  }
  .paid-copy h3 { font-size: 28px; }
  .paid-copy p { font-size: 13px; line-height: 1.7; }
  .paid-amount, .paid-address, .paid-controls, .paid-tx {
    grid-template-columns: 1fr;
    gap: 8px;
  }
  .paid-amount b { font-size: 24px; }
  .paid-address button, .paid-controls button, .paid-tx button, .paid-controls select, .paid-tx input {
    width: 100%;
  }
  .telemetry { grid-template-columns: 1fr; gap: 12px; }
  .timeline, .risk { padding: 18px; border-radius: 22px; }
  .line {
    align-items: flex-start;
    padding: 13px 0;
    font-size: 13px;
  }
  .line b { max-width: 58%; word-break: break-word; }
  .risk h3 { font-size: 22px; }
  .risk p { font-size: 13px; line-height: 1.8; }
  .footer { padding: 44px 14px 40px; line-height: 1.65; }
}
@media (max-width: 380px) {
  .shell { width: calc(100vw - 18px); }
  h1 { font-size: 42px; }
  .hero-actions, .metrics { grid-template-columns: 1fr; }
  .hero-actions .btn.hot { grid-column: auto; }
  .command { min-height: 288px; }
  .mini { grid-template-columns: 1fr; }
  .mini div { display: flex; justify-content: space-between; align-items: center; }
}
@media (prefers-reduced-motion: reduce) {
  *, *:before, *:after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
"""


SHARE_POSTER_CSS = r"""
.poster-modal[hidden] { display: none; }
.poster-modal {
  position: fixed;
  inset: 0;
  z-index: 180;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(1, 6, 16, .78);
  backdrop-filter: blur(18px);
}
.poster-panel {
  width: min(980px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  overflow: auto;
  display: grid;
  grid-template-columns: minmax(0, 360px) minmax(0, 1fr);
  gap: 18px;
  border: 1px solid rgba(86,239,255,.28);
  border-radius: 24px;
  padding: 18px;
  background:
    radial-gradient(circle at 88% 0%, rgba(86,239,255,.16), transparent 34%),
    linear-gradient(180deg, rgba(15,27,50,.98), rgba(5,12,26,.99));
  box-shadow: 0 34px 120px rgba(0,0,0,.62), inset 0 1px 0 rgba(255,255,255,.08);
}
.poster-preview {
  display: grid;
  place-items: center;
  min-height: 430px;
  border: 1px solid rgba(86,239,255,.18);
  border-radius: 18px;
  background:
    linear-gradient(rgba(86,239,255,.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(86,239,255,.045) 1px, transparent 1px),
    rgba(4, 12, 27, .72);
  background-size: 22px 22px;
  overflow: hidden;
}
.poster-preview canvas {
  display: block;
  width: min(100%, 320px);
  height: auto;
  border-radius: 10px;
  box-shadow: 0 20px 70px rgba(0,0,0,.42);
}
.poster-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 14px;
}
.poster-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: flex-start;
}
.poster-head span {
  color: var(--amber, #ffd37e);
  font-size: 12px;
  font-weight: 950;
  letter-spacing: .12em;
}
.poster-head h3 {
  margin: 8px 0 0;
  font-size: 34px;
  line-height: 1;
  letter-spacing: -.045em;
}
.poster-close {
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  border: 1px solid rgba(86,239,255,.25);
  border-radius: 999px;
  color: #ddf8ff;
  background: rgba(255,255,255,.055);
  cursor: pointer;
  font: 900 22px/1 var(--font, sans-serif);
}
.poster-copy p {
  margin: 0;
  color: #aabbd3;
  line-height: 1.75;
}
.poster-meta {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.poster-meta li {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 10px 0;
  border-bottom: 1px solid rgba(86,239,255,.12);
  color: #aebbd1;
}
.poster-meta b {
  color: #f5fbff;
  font-family: var(--mono, monospace);
  text-align: right;
}
.poster-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: auto;
}
.poster-actions button {
  min-height: 44px;
  border: 1px solid rgba(86,239,255,.28);
  border-radius: 14px;
  padding: 0 16px;
  color: #dff8ff;
  background: rgba(255,255,255,.06);
  cursor: pointer;
  font: 900 13px var(--font, sans-serif);
}
.poster-actions [data-poster-download],
.poster-actions [data-poster-share] {
  color: #03121b;
  border-color: transparent;
  background: linear-gradient(135deg, var(--cyan, #56efff), var(--blue, #7e8cff));
}
.poster-status {
  min-height: 20px;
  color: #91a4bf;
  font-size: 13px;
  line-height: 1.55;
}
.poster-status.is-error { color: #ffb7b7; }
.poster-status.is-ok { color: #9df4c3; }
@media (max-width: 720px) {
  .poster-modal { padding: 12px; place-items: end center; }
  .poster-panel {
    width: calc(100vw - 24px);
    max-height: calc(100vh - 24px);
    grid-template-columns: 1fr;
    gap: 14px;
    padding: 14px;
    border-radius: 22px;
  }
  .poster-preview { min-height: 0; padding: 12px; }
  .poster-preview canvas { width: min(100%, 250px); }
  .poster-head h3 { font-size: 26px; }
  .poster-copy p { font-size: 13px; line-height: 1.65; }
  .poster-meta li { font-size: 12px; }
  .poster-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .poster-actions button { width: 100%; padding: 0 10px; }
}
"""


SCROLL_DASHBOARD_JS = r"""
const io = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) entry.target.classList.add('visible');
  });
}, { threshold: .16 });

document.querySelectorAll('.reveal,.stagger').forEach((el) => io.observe(el));

const root = document.documentElement;
const setProgress = () => {
  const max = Math.max(1, root.scrollHeight - window.innerHeight);
  root.style.setProperty('--scroll', String(window.scrollY || 0));
  root.style.setProperty('--progress', Math.min(1, (window.scrollY || 0) / max).toFixed(4));
};
setProgress();
window.addEventListener('scroll', setProgress, { passive: true });
window.addEventListener('pointermove', (event) => {
  root.style.setProperty('--mx', `${event.clientX}px`);
  root.style.setProperty('--my', `${event.clientY}px`);
}, { passive: true });

document.querySelectorAll('.metric,.fcard,.rank-card,.panel,.command').forEach((el) => {
  el.addEventListener('pointermove', (event) => {
    const rect = el.getBoundingClientRect();
    const px = (event.clientX - rect.left) / rect.width;
    const py = (event.clientY - rect.top) / rect.height;
    el.style.setProperty('--spotX', `${px * 100}%`);
    el.style.setProperty('--spotY', `${py * 100}%`);
    el.style.setProperty('--tiltX', `${(0.5 - py) * 7}deg`);
    el.style.setProperty('--tiltY', `${(px - 0.5) * 8}deg`);
  }, { passive: true });
  el.addEventListener('pointerleave', () => {
    el.style.setProperty('--tiltX', '0deg');
    el.style.setProperty('--tiltY', '0deg');
    el.style.setProperty('--spotX', '50%');
    el.style.setProperty('--spotY', '0%');
  }, { passive: true });
});

const rankGrid = document.getElementById('rankGrid');
const rankPagination = document.querySelector('[data-rank-pagination]');
if (rankGrid && rankPagination) {
  const pageSize = Number(rankGrid.dataset.pageSize || 10);
  const cards = Array.from(rankGrid.querySelectorAll('.rank-card'));
  const totalCount = Number(rankGrid.dataset.totalCount || cards.length);
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  const buttons = Array.from(rankPagination.querySelectorAll('[data-rank-page]'));
  const prev = rankPagination.querySelector('[data-rank-prev]');
  const next = rankPagination.querySelector('[data-rank-next]');
  const count = rankPagination.querySelector('[data-rank-count]');
  let currentPage = 1;
  const renderRankPage = (page) => {
    currentPage = Math.max(1, Math.min(totalPages, page));
    const start = (currentPage - 1) * pageSize;
    const end = Math.min(start + pageSize, totalCount);
    cards.forEach((card, index) => {
      const visible = index >= start && index < end;
      card.classList.toggle('is-page-hidden', !visible);
      if (visible) card.classList.add('visible');
    });
    buttons.forEach((button) => {
      const active = Number(button.dataset.rankPage) === currentPage;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-current', active ? 'page' : 'false');
    });
    if (prev) prev.disabled = currentPage === 1;
    if (next) next.disabled = currentPage === totalPages;
    if (count) count.textContent = `第 ${currentPage} / ${totalPages} 页 · 当前显示 ${start + 1}-${end} / 共 ${totalCount} 名`;
    if (window.applyMarsLanguage) window.applyMarsLanguage();
  };
  buttons.forEach((button) => {
    button.addEventListener('click', () => renderRankPage(Number(button.dataset.rankPage || 1)));
  });
  if (prev) prev.addEventListener('click', () => renderRankPage(currentPage - 1));
  if (next) next.addEventListener('click', () => renderRankPage(currentPage + 1));
  renderRankPage(1);
}

const setupPaidDownloadPanel = (panel) => {
  const apiBase = (panel.dataset.apiBase || '').replace(/\/+$/, '');
  const createButton = panel.querySelector('[data-paid-create]');
  const verifyButton = panel.querySelector('[data-paid-verify]');
  const copyButton = panel.querySelector('[data-paid-copy]');
  const addressNode = panel.querySelector('[data-paid-address]');
  const txInput = panel.querySelector('[data-paid-tx]');
  const formatSelect = panel.querySelector('[data-paid-format]');
  const statusNode = panel.querySelector('[data-paid-status]');
  const linkNode = panel.querySelector('[data-paid-link]');
  let orderId = '';

  const setStatus = (text, mode = '') => {
    if (!statusNode) return;
    statusNode.textContent = text;
    statusNode.classList.toggle('is-error', mode === 'error');
    statusNode.classList.toggle('is-ok', mode === 'ok');
    if (window.applyMarsLanguage) window.applyMarsLanguage();
  };
  const postJson = async (path, body) => {
    const response = await fetch(`${apiBase}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (error) {}
    if (!response.ok) {
      throw new Error(payload.message || payload.error || '请求失败');
    }
    return payload;
  };

  if (!apiBase) {
    if (createButton) createButton.disabled = true;
    if (verifyButton) verifyButton.disabled = true;
    if (txInput) txInput.disabled = true;
    setStatus('付费下载接口待接入', 'error');
    return;
  }

  if (copyButton && addressNode) {
    copyButton.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(addressNode.textContent.trim());
        setStatus('收款地址已复制', 'ok');
      } catch (error) {
        setStatus(addressNode.textContent.trim(), 'ok');
      }
    });
  }

  if (createButton) {
    createButton.addEventListener('click', async () => {
      createButton.disabled = true;
      setStatus('正在生成付款订单...');
      try {
        const result = await postJson('/orders', { format: formatSelect ? formatSelect.value : 'xlsx' });
        orderId = result.orderId || result.order_id || '';
        if (txInput) txInput.disabled = false;
        if (verifyButton) verifyButton.disabled = false;
        setStatus(`订单已生成：支付 ${result.amountMars || '1000'} MARS 后提交交易哈希。`, 'ok');
      } catch (error) {
        setStatus(error.message || '订单生成失败', 'error');
      } finally {
        createButton.disabled = false;
      }
    });
  }

  if (verifyButton) {
    verifyButton.addEventListener('click', async () => {
      const txHash = txInput ? txInput.value.trim() : '';
      if (!orderId) {
        setStatus('请先生成付款订单', 'error');
        return;
      }
      if (!/^0x[a-fA-F0-9]{64}$/.test(txHash)) {
        setStatus('交易哈希格式不正确', 'error');
        return;
      }
      verifyButton.disabled = true;
      setStatus('正在核销链上付款...');
      try {
        const result = await postJson(`/orders/${encodeURIComponent(orderId)}/verify`, {
          txHash,
          format: formatSelect ? formatSelect.value : 'xlsx',
        });
        if (result.status === 'WAITING_CONFIRMATIONS') {
          setStatus(`交易已找到，等待确认数 ${result.confirmations || 0}/${result.requiredConfirmations || 3}`);
          return;
        }
        if (result.downloadUrl && linkNode) {
          linkNode.href = result.downloadUrl;
          linkNode.classList.add('is-ready');
          setStatus('核销成功，下载链接 1 小时内有效。', 'ok');
        } else {
          setStatus('核销成功，请重新打开下载链接。', 'ok');
        }
      } catch (error) {
        setStatus(error.message || '核销失败', 'error');
      } finally {
        verifyButton.disabled = false;
      }
    });
  }
};

const setupBurnCalculator = (panel) => {
  const rate = Number(panel.dataset.burnRate || 0);
  const input = panel.querySelector('[data-burn-power]');
  const result = panel.querySelector('[data-burn-result]');
  const note = panel.querySelector('[data-burn-note]');
  if (!input || !result) return;
  const trimZeros = (value) => value.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  const parsePower = (value) => {
    let text = String(value || '').replace(/,/g, '').replace(/\s+/g, '').toLowerCase();
    let multiplier = 1;
    if (text.includes('万亿')) {
      multiplier = 1_0000_0000_0000;
      text = text.replace(/万亿/g, '');
    } else if (text.includes('亿')) {
      multiplier = 1_0000_0000;
      text = text.replace(/亿/g, '');
    } else if (text.includes('万')) {
      multiplier = 1_0000;
      text = text.replace(/万/g, '');
    } else if (text.endsWith('b')) {
      multiplier = 1_000_000_000;
      text = text.slice(0, -1);
    } else if (text.endsWith('m')) {
      multiplier = 1_000_000;
      text = text.slice(0, -1);
    } else if (text.endsWith('k')) {
      multiplier = 1_000;
      text = text.slice(0, -1);
    }
    const match = text.match(/-?\d+(?:\.\d+)?/);
    if (!match) return NaN;
    return Number(match[0]) * multiplier;
  };
  const formatChinese = (number, suffix = '') => {
    if (!Number.isFinite(number)) return '待刷新';
    const abs = Math.abs(number);
    if (abs >= 1_0000_0000_0000) return `${trimZeros((number / 1_0000_0000_0000).toFixed(3))}万亿${suffix}`;
    if (abs >= 1_0000_0000) return `${trimZeros((number / 1_0000_0000).toFixed(3))}亿${suffix}`;
    if (abs >= 1_0000) return `${trimZeros((number / 1_0000).toFixed(3))}万${suffix}`;
    return `${trimZeros(number.toLocaleString('zh-CN', { maximumFractionDigits: 3 }))}${suffix}`;
  };
  const update = () => {
    const power = parsePower(input.value);
    if (!Number.isFinite(power) || power <= 0 || rate <= 0) {
      result.textContent = '请输入有效算力';
      if (note) note.textContent = '支持 1亿、5亿、1000万 或纯数字。';
      return;
    }
    const burned = power * rate;
    result.textContent = formatChinese(burned, '枚');
    if (note) note.textContent = `输入算力 ${formatChinese(power)}，按当前比例估算。`;
  };
  input.addEventListener('input', update);
  update();
};

document.querySelectorAll('[data-burn-calculator]').forEach(setupBurnCalculator);
document.querySelectorAll('[data-paid-panel]').forEach(setupPaidDownloadPanel);

document.querySelectorAll('[data-track]').forEach((node) => {
  node.addEventListener('click', () => {
    window._hmt = window._hmt || [];
    window._hmt.push(['_trackEvent', 'marschain', node.dataset.track || 'click', node.dataset.label || '']);
    if (window.clarity) window.clarity('event', node.dataset.track || 'click');
  });
});
"""


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _fmt_decimal(value: object, digits: int = 3) -> str:
    return f"{_as_float(value):,.{digits}f}"


def _fmt_price_value(value: object, fallback: str = "待刷新") -> str:
    if value is None or value == "":
        return fallback
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or fallback
    if number < 1:
        return f"{number:.6f}"
    return f"{number:.3f}"


def _oracle_trigger_price_display(meta: dict) -> str:
    highest = meta.get("network_highest_price")
    if highest is None or highest == "":
        highest = meta.get("network_highest_price_display")
    try:
        number = float(str(highest))
    except (TypeError, ValueError):
        return "待刷新"
    return _fmt_price_value(number * 0.5)


def _latest_block_value(meta: dict) -> int:
    for key in ("latest_block", "rpc_latest_block", "rpc_log_latest_block", "rpc_log_start_block", "rpc_log_end_block"):
        value = _as_int(meta.get(key))
        if value > 0:
            return value
    return 0


def _fmt_chinese_number(value: object, digits: int = 3, fallback: str = "待刷新") -> str:
    if value is None or value == "":
        return fallback
    number = _as_float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_0000_0000_0000:
        return f"{sign}{number / 1_0000_0000_0000:.{digits}f}万亿"
    if number >= 1_0000_0000:
        return f"{sign}{number / 1_0000_0000:.{digits}f}亿"
    if number >= 1_0000:
        return f"{sign}{number / 1_0000:.{digits}f}万"
    return f"{sign}{number:,.0f}"


def _fmt_count_unit(value: object, unit: str = "个") -> str:
    formatted = _fmt_chinese_number(value)
    if formatted == "待刷新":
        return formatted
    return f"{formatted}{unit}"


def _fmt_token_daily_output(value: object) -> str:
    number = _as_float(value)
    if number <= 0:
        return "待刷新"
    if number >= 10_000:
        formatted = _fmt_chinese_number(number, digits=3)
    elif number >= 100:
        formatted = f"{number:,.1f}".rstrip("0").rstrip(".")
    elif number >= 1:
        formatted = f"{number:,.3f}".rstrip("0").rstrip(".")
    else:
        formatted = f"{number:.6f}".rstrip("0").rstrip(".")
    return f"{formatted}枚/日"


def _fmt_one_yi_power_output(meta: dict) -> str:
    power_required = _as_float(meta.get("power_required_per_mars_daily"))
    if power_required <= 0:
        network_power = _as_float(meta.get("network_total_power"))
        miner_daily_tokens = _as_float(meta.get("emission_daily_miner_tokens"))
        if miner_daily_tokens <= 0:
            daily_total_tokens = _as_float(meta.get("emission_daily_total_tokens"))
            miner_share = _as_float(meta.get("emission_miner_share"), MARS_MINER_SHARE)
            if daily_total_tokens > 0:
                miner_daily_tokens = daily_total_tokens * miner_share
        if miner_daily_tokens <= 0:
            miner_daily_tokens = (MARS_INITIAL_CYCLE_OUTPUT_TOKENS / MARS_HALVING_PERIOD_DAYS) * MARS_MINER_SHARE
        if network_power > 0 and miner_daily_tokens > 0:
            power_required = network_power / miner_daily_tokens
    if power_required <= 0:
        return "待刷新"
    return _fmt_token_daily_output(100_000_000 / power_required)


def _build_burn_estimate(meta: dict) -> dict[str, object]:
    circulation_tokens = _as_float(meta.get("network_total_circulation_tokens")) / 1_000_000_000_000_000_000
    network_power = _as_float(meta.get("network_total_power"))
    burn_ratio = 0.35
    burn_pool = circulation_tokens * burn_ratio if circulation_tokens > 0 else 0.0
    burn_per_power = burn_pool / network_power if burn_pool > 0 and network_power > 0 else 0.0
    sample_power = 100_000_000
    sample_burn = sample_power * burn_per_power
    return {
        "burn_ratio_percent": "35%",
        "burn_pool": burn_pool,
        "burn_pool_display": f"{_fmt_chinese_number(burn_pool, digits=3)}枚" if burn_pool > 0 else "待刷新",
        "burn_per_power": burn_per_power,
        "burn_per_power_attr": f"{burn_per_power:.18f}",
        "sample_power": sample_power,
        "sample_burn": sample_burn,
        "sample_burn_display": f"{_fmt_chinese_number(sample_burn, digits=3)}枚" if sample_burn > 0 else "待刷新",
    }


def _build_burn_calculator(estimate: dict[str, object], *, mobile: bool = False) -> str:
    burn_rate = str(estimate.get("burn_per_power_attr") or "0")
    sample_power = int(_as_float(estimate.get("sample_power"), 100_000_000))
    sample_burn = str(estimate.get("sample_burn_display") or "待刷新")
    burn_pool = str(estimate.get("burn_pool_display") or "待刷新")
    ratio = str(estimate.get("burn_ratio_percent") or "35%")
    if mobile:
        return f"""
      <article class="m-burn-calculator" data-burn-calculator data-burn-rate="{escape(burn_rate)}">
        <div>
          <span class="m-kicker">BURN CALC</span>
          <h3>算力销毁测算</h3>
          <p>按当前全网流通量 × {escape(ratio)} ÷ 全网总算力计算。</p>
        </div>
        <div class="m-burn-stats">
          <div class="m-burn-stat"><span>35%流通量</span><b>{escape(burn_pool)}</b></div>
          <div class="m-burn-stat"><span>1亿算力算例</span><b>{escape(sample_burn)}</b></div>
        </div>
        <div class="m-burn-input">
          <label for="mBurnPowerInput">输入算力</label>
          <input id="mBurnPowerInput" data-burn-power type="text" inputmode="decimal" value="1亿" placeholder="例如 1亿、5亿、1000万">
        </div>
        <div class="m-burn-result">
          <span>需要销毁</span>
          <b data-burn-result>{escape(sample_burn)}</b>
          <small data-burn-note>按输入算力实时换算。</small>
        </div>
      </article>"""
    return f"""
    <div class="burn-calculator reveal" data-burn-calculator data-burn-rate="{escape(burn_rate)}">
      <div class="burn-copy">
        <span class="kicker">BURN CALC</span>
        <h3>算力销毁测算</h3>
        <p>按“全网流通量 × {escape(ratio)} ÷ 全网总算力”计算，得到每 1 算力对应的销毁数量；输入任意算力即可换算需要销毁多少 MARS。</p>
      </div>
      <div class="burn-form">
        <div class="burn-stats">
          <div class="line"><span>计算公式</span><b>流通量 × {escape(ratio)} ÷ 总算力</b></div>
          <div class="line"><span>35%流通量</span><b>{escape(burn_pool)}</b></div>
          <div class="line"><span>1亿算力算例</span><b>{escape(sample_burn)}</b></div>
        </div>
        <div class="burn-input-row">
          <label for="burnPowerInput">输入算力</label>
          <input id="burnPowerInput" data-burn-power type="text" inputmode="decimal" value="1亿" placeholder="例如 1亿、5亿、1000万">
        </div>
        <div class="burn-result">
          <span>需要销毁</span>
          <b data-burn-result>{escape(sample_burn)}</b>
          <small data-burn-note>当前算例：{sample_power:,} 算力。</small>
        </div>
      </div>
    </div>"""


def _fmt_power(value: object) -> str:
    return _fmt_chinese_number(value, digits=3)


def _fmt_percent(value: object) -> str:
    number = _as_float(value)
    if abs(number) <= 1:
        number *= 100
    return f"{number:.3f}%"


def _safe_text(value: object, fallback: str = "待刷新") -> str:
    if value is None or value == "":
        return fallback
    return escape(str(value))


def _short_address(address: object) -> str:
    raw = str(address or "")
    if len(raw) <= 16:
        return escape(raw)
    return escape(f"{raw[:8]}...{raw[-6:]}")


def _format_generated_at_from_meta(meta: dict) -> str:
    if meta.get("generated_at_local"):
        return str(meta["generated_at_local"])
    return format_generated_at(_as_int(meta.get("generated_at"), int(time.time())))


def _clean_trend_values(values: object, limit: int = 30) -> list[float]:
    if not isinstance(values, list):
        return []
    cleaned: list[float] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("value")
        try:
            if item is None or item == "":
                continue
            value = float(item)
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        cleaned.append(value)
    return cleaned[-limit:]


def _clean_trend_points(values: object, limit: int = 120) -> list[dict[str, object]]:
    if not isinstance(values, list):
        return []
    points: list[dict[str, object]] = []
    for index, item in enumerate(values, start=1):
        label = ""
        value: object = item
        if isinstance(item, dict):
            value = item.get("value")
            label = str(
                item.get("label")
                or item.get("date")
                or item.get("day")
                or item.get("time")
                or item.get("created_at")
                or ""
            )
        try:
            if value is None or value == "":
                continue
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number:
            continue
        points.append({"label": label or f"采样 {index}", "value": number})
    return points[-limit:]


def _trend_points(meta: dict, key: str, fallback: list[object] | None = None, limit: int = 120) -> list[dict[str, object]]:
    fallback_points = _clean_trend_points(list(fallback or []), limit=limit)
    trends = meta.get("metric_trends")
    trend_points: list[dict[str, object]] = []
    if isinstance(trends, dict):
        entry = trends.get(key)
        if isinstance(entry, dict):
            trend_points = _clean_trend_points(entry.get("values"), limit=limit)
        elif isinstance(entry, list):
            trend_points = _clean_trend_points(entry, limit=limit)
    if len(trend_points) >= 2:
        return trend_points
    if len(fallback_points) >= 2:
        return fallback_points
    return trend_points or fallback_points


def _trend_values(meta: dict, key: str, fallback: list[object] | None = None, limit: int = 30) -> list[float]:
    fallback_values = _clean_trend_values(list(fallback or []), limit=limit)
    trends = meta.get("metric_trends")
    trend_values: list[float] = []
    if isinstance(trends, dict):
        entry = trends.get(key)
        if isinstance(entry, dict):
            trend_values = _clean_trend_values(entry.get("values"), limit=limit)
        elif isinstance(entry, list):
            trend_values = _clean_trend_values(entry, limit=limit)
    if len(trend_values) >= 2:
        return trend_values
    if len(fallback_values) >= 2:
        return fallback_values
    return trend_values or fallback_values


def _trend_from_cumulative(current: object, *increments: object) -> list[float]:
    current_value = _as_float(current)
    if current_value <= 0:
        return []
    points = [current_value]
    running = current_value
    for increment in increments:
        inc_value = max(0.0, _as_float(increment))
        if inc_value <= 0:
            continue
        running = max(0.0, running - inc_value)
        points.insert(0, running)
    return points


def _trend_average_points(day_value: object, period_7d_value: object, period_30d_value: object) -> list[float]:
    values: list[object] = []
    period_30 = _as_float(period_30d_value)
    period_7 = _as_float(period_7d_value)
    current = _as_float(day_value)
    if period_30 > 0:
        values.append(period_30 / 30)
    if period_7 > 0:
        values.append(period_7 / 7)
    if current > 0:
        values.append(current)
    return _clean_trend_values(values)


def _build_sparkline(values: list[float], *, label: str = "近 30 次趋势") -> str:
    clean = [value for value in values if value == value]
    sampling = len(clean) < 2
    if not clean:
        clean = [0.0, 0.0]
    elif len(clean) == 1:
        clean = [clean[0], clean[0]]
    width = 220.0
    height = 48.0
    pad_x = 2.0
    pad_y = 5.0
    min_value = min(clean)
    max_value = max(clean)
    span = max(max_value - min_value, max(abs(max_value), 1.0) * 0.08)
    points: list[tuple[float, float]] = []
    for index, value in enumerate(clean):
        x = pad_x + (width - pad_x * 2) * (index / max(1, len(clean) - 1))
        y = height - pad_y - ((value - min_value) / span) * (height - pad_y * 2)
        points.append((x, y))
    line_path = " ".join(("M" if index == 0 else "L") + f"{x:.2f},{y:.2f}" for index, (x, y) in enumerate(points))
    area_path = f"{line_path} L {points[-1][0]:.2f},{height:.2f} L {points[0][0]:.2f},{height:.2f} Z"
    start_label = "首个采样点" if sampling else "低"
    end_label = "等待下次刷新" if sampling else label
    css_class = "metric-trend is-sampling" if sampling else "metric-trend"
    last_x, last_y = points[-1]
    return (
        f'<div class="{css_class}" aria-label="{escape(end_label)}">'
        '<svg viewBox="0 0 220 48" role="img" aria-hidden="true" focusable="false">'
        f'<path class="area" d="{area_path}"></path>'
        f'<path class="line" d="{line_path}"></path>'
        f'<circle cx="{last_x:.2f}" cy="{last_y:.2f}" r="3.2"></circle>'
        "</svg>"
        f'<span class="metric-trend-label"><i>{escape(start_label)}</i><i>{escape(end_label)}</i></span>'
        "</div>"
    )


def _build_metric_cards(items: list[tuple]) -> str:
    cards = []
    for index, item in enumerate(items):
        if len(item) >= 5:
            metric_key, label, value, note, trend_points = item[:5]
            extra = item[5] if len(item) > 5 and isinstance(item[5], dict) else {}
            trend_values = [point.get("value") for point in trend_points if isinstance(point, dict)]
        else:
            metric_key = ""
            label, value, note = item[:3]
            extra = {}
            trend_values = item[3] if len(item) > 3 else []
        live_price = str(metric_key) == "network_current_price"
        value_html = f'<b{" data-live-price" if live_price else ""}>{escape(value)}</b>'
        if live_price:
            highest = str(extra.get("highest_price") or "待刷新")
            trigger = str(extra.get("oracle_trigger_price") or "待刷新")
            value_html = (
                f'<b data-live-price>{escape(value)}</b>'
                '<div class="price-stack">'
                f'<span>最高价 <strong data-live-highest-price>{escape(highest)}</strong></span>'
                f'<span>预言机触发价 <strong data-live-oracle-trigger-price>{escape(trigger)}</strong></span>'
                '</div>'
            )
        cards.append(
            '<article class="metric" style="--delay:%sms" role="button" tabindex="0" %s'
            'data-trend-index="%d" data-track="metric_trend" data-label="%s" aria-label="查看%s趋势">'
            "<span>%s</span>%s<small%s>%s</small>%s</article>"
            % (
                index * 70,
                'data-price-card ' if live_price else "",
                index,
                escape(str(label), quote=True),
                escape(str(label), quote=True),
                escape(label),
                value_html,
                ' data-live-price-note' if live_price else "",
                escape(note),
                _build_sparkline(_clean_trend_values(trend_values)),
            )
        )
    return "\n".join(cards)


def _build_metric_trend_payload(items: list[tuple]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for index, item in enumerate(items):
        if len(item) >= 5:
            key, label, value, note, points = item[:5]
            clean_points = _clean_trend_points(points, limit=120)
        else:
            key = f"metric_{index}"
            label, value, note = item[:3]
            clean_points = _clean_trend_points(item[3] if len(item) > 3 else [], limit=120)
        payload.append(
            {
                "key": str(key),
                "label": str(label),
                "value": str(value),
                "note": str(note),
                "points": clean_points,
            }
        )
    return payload


def _build_rank_cards(rows: list[dict], limit: int = 100, page_size: int = 10) -> str:
    top_rows = rows[:limit]
    max_power = max([_as_float(row.get("power")) for row in top_rows] or [1.0]) or 1.0
    cards: list[str] = []
    for index, row in enumerate(top_rows):
        power = _as_float(row.get("power"))
        rank_number = _as_int(row.get("rank"), index + 1)
        width = max(4.0, min(100.0, (power / max_power) * 100))
        page_class = " is-page-hidden" if index >= page_size else ""
        cards.append(
            '<article class="rank-card reveal%s" style="--delay:%sms">'
            '<div class="rank-top"><em>%02d</em><strong>%s</strong></div>'
            "<code>%s</code>"
            '<span class="rank-bar"><i style="width:%.3f%%"></i></span>'
            "</article>"
            % (
                page_class,
                (index + 1) * 55,
                rank_number,
                escape(_fmt_power(power)),
                escape(str(row.get("address") or "")),
                width,
            )
        )
    return "\n".join(cards)


def _build_timeline(items: list[tuple[str, str]]) -> str:
    live_attrs = {
        "当前价格": " data-live-price",
        "最高价格": " data-live-highest-price",
        "预言机触发价": " data-live-oracle-trigger-price",
    }
    return "\n".join(
        '<div class="line"><span>%s</span><b%s>%s</b></div>'
        % (escape(label), live_attrs.get(label, ""), escape(value))
        for label, value in items
    )


def _build_marquee(items: list[tuple[str, str]]) -> str:
    live_attrs = {
        "当前价格": " data-live-price",
        "最高价格": " data-live-highest-price",
        "预言机触发价": " data-live-oracle-trigger-price",
    }
    doubled = items + items
    return "".join(
        "<span>%s<b%s>%s</b></span>"
        % (escape(label), live_attrs.get(label, ""), escape(value))
        for label, value in doubled
    )


def _build_warning(meta: dict, threshold_label: str) -> str:
    coverage = _as_float(meta.get("discovered_power_coverage"))
    target_met = bool(meta.get("target_met", coverage >= _as_float(meta.get("coverage_target"), 0.8)))
    if target_met:
        return ""
    return (
        '<div class="alert">'
        f"本轮覆盖率为 {_fmt_percent(coverage)}，低于目标 {escape(threshold_label)}。"
        "页面仍发布当轮最佳扫描结果，请结合风险说明理解数据边界。"
        "</div>"
    )


def _build_paid_download_panel(config: dict[str, str], *, mobile: bool = False) -> str:
    prefix = "m-paid" if mobile else "paid"
    copy_label = "复制"
    return f"""
    <section class="{prefix}-download" data-paid-panel data-api-base="{escape(config["api_base"])}">
      <div class="{prefix}-copy">
        <span>{'PAID DOWNLOAD' if not mobile else 'DOWNLOAD'}</span>
        <h3>全球排行榜下载</h3>
        <p>前 100 名免费查看，全量文件需支付 {escape(config["price_mars"])} MARS；核销成功后下载链接 {escape(config["expires_label"])} 内有效。</p>
        <ul class="{prefix}-rules">
          <li>先生成付款订单，再用 MarsChain 钱包向收款地址转账 MARS。</li>
          <li>{escape(config["price_mars"])} MARS 需单笔一次性支付，拆分多笔无法自动核销。</li>
          <li>链上手续费由付款方承担，实际转账金额需不少于 {escape(config["price_mars"])} MARS。</li>
          <li>转账确认后复制交易哈希，回到本页提交核销。</li>
        </ul>
      </div>
      <div class="{prefix}-box">
        <div class="{prefix}-amount"><span>收款金额</span><b>{escape(config["price_mars"])} MARS</b></div>
        <div class="{prefix}-address">
          <span>收款地址</span>
          <code data-paid-address>{escape(config["pay_to_display"])}</code>
          <button type="button" data-paid-copy>{copy_label}</button>
        </div>
        <div class="{prefix}-controls">
          <select data-paid-format aria-label="下载格式">
            <option value="xlsx">Excel</option>
            <option value="csv">CSV</option>
          </select>
          <button type="button" data-paid-create>生成付款订单</button>
        </div>
        <div class="{prefix}-tx">
          <input data-paid-tx type="text" inputmode="text" placeholder="输入交易哈希" disabled>
          <button type="button" data-paid-verify disabled>核销下载</button>
        </div>
        <div class="{prefix}-status" data-paid-status>{'付费下载接口待接入' if not config["api_base"] else '生成订单后提交交易哈希'}</div>
        <a class="{prefix}-link" data-paid-link href="#" target="_blank" rel="noopener">打开下载链接</a>
      </div>
    </section>"""


def build_html(payload: dict) -> str:  # type: ignore[no-redef]
    """Render the new continuous-scroll MarsChain site."""
    payload = _normalize_statistics_payload(payload)
    meta = payload.get("meta", {})
    rows = payload.get("rows", [])
    title = "MarsChain 算力排行榜"
    subtitle = "追踪链上算力分布、头部地址变化与北京时间统计日内新增趋势。"
    analytics_head = build_analytics_head()
    paid_download_config = load_paid_download_config()

    generated_at = _format_generated_at_from_meta(meta)
    statistics_window_label = str(meta.get("statistics_window_label") or "北京时间 00:00 至次日 00:00")
    coverage = _as_float(meta.get("discovered_power_coverage"))
    coverage_label = _fmt_percent(coverage)
    coverage_target = _as_float(meta.get("coverage_target"), 0.8)
    threshold_label = _fmt_percent(coverage_target)

    network_total_power = meta.get("network_total_power")
    discovered_total_power = _as_float(meta.get("discovered_total_power"))
    uncovered_power = max(0.0, _as_float(network_total_power) - discovered_total_power)
    candidate_count = meta.get("candidate_count")
    positive_power_count = meta.get("positive_power_count")
    explorer_total_addresses = meta.get("explorer_total_addresses")
    active_wallet_count = meta.get("statistics_window_active_wallet_address_count")
    new_address_count = meta.get("statistics_window_new_candidate_address_count")
    if new_address_count is None:
        new_address_count = meta.get("today_new_wallet_count")
    new_power = meta.get("statistics_window_new_power")
    if new_power is None:
        new_power = meta.get("today_new_power")
    circulation = str(meta.get("network_total_circulation_display") or "待刷新")
    current_price = str(meta.get("network_current_price_display") or "待刷新")
    highest_price = str(meta.get("network_highest_price_display") or _fmt_price_value(meta.get("network_highest_price")))
    oracle_trigger_price = _oracle_trigger_price_display(meta)
    total_burned = str(meta.get("network_total_burned_display") or "待刷新")
    daily_burned = str(meta.get("statistics_window_burned_display") or meta.get("today_burned_display") or "待刷新")
    period_7d_new_power = meta.get("period_7d_new_power")
    period_7d_new_address_count = meta.get("period_7d_new_candidate_address_count")
    period_7d_burned = str(meta.get("period_7d_burned_display") or "待刷新")
    period_30d_new_power = meta.get("period_30d_new_power")
    period_30d_new_address_count = meta.get("period_30d_new_candidate_address_count")
    period_30d_burned = str(meta.get("period_30d_burned_display") or "待刷新")
    positive_ratio = 0.0
    if _as_float(candidate_count) > 0:
        positive_ratio = _as_float(positive_power_count) / _as_float(candidate_count)

    total_supply = str(meta.get("emission_total_supply_cap_display") or "2000亿")
    daily_total = str(meta.get("emission_daily_total_display") or "待刷新")
    daily_miner = str(meta.get("emission_daily_miner_display") or "待刷新")
    daily_node = str(meta.get("emission_daily_node_display") or "待刷新")
    power_per_coin = str(meta.get("power_required_per_mars_daily_display") or "待刷新")
    one_yi_power_output = _fmt_one_yi_power_output(meta)
    power_required_value = _as_float(meta.get("power_required_per_mars_daily"))
    one_yi_power_output_value = 100_000_000 / power_required_value if power_required_value > 0 else None
    total_burned_tokens = meta.get("network_total_burned_tokens")
    total_circulation_tokens = meta.get("network_total_circulation_tokens")
    daily_total_tokens = meta.get("emission_daily_total_tokens")

    metric_items = [
        (
            "network_total_power",
            "全网总算力",
            _fmt_power(network_total_power),
            "区块浏览器公开统计",
            _trend_points(
                meta,
                "network_total_power",
                [
                    meta.get("period_30d_start_total_power"),
                    meta.get("period_7d_start_total_power"),
                    meta.get("statistics_window_start_total_power"),
                    network_total_power,
                ],
            ),
        ),
        ("network_total_circulation", "全网流通量", circulation, "区块浏览器公开统计", _trend_points(meta, "network_total_circulation", [total_circulation_tokens])),
        (
            "network_current_price",
            "当前价格",
            current_price,
            "预言机触发价为最高价的 50%",
            _trend_points(meta, "network_current_price", [meta.get("network_current_price")]),
            {"highest_price": highest_price, "oracle_trigger_price": oracle_trigger_price},
        ),
        ("daily_emission", "每日产币量", daily_total, "官方经济模型口径", _trend_points(meta, "daily_emission", [daily_total_tokens, daily_total_tokens])),
        (
            "total_burned",
            "累计销毁",
            total_burned,
            "POWER 合约累计燃烧",
            _trend_points(
                meta,
                "total_burned",
                _trend_from_cumulative(
                    total_burned_tokens,
                    meta.get("period_30d_burned_tokens"),
                    meta.get("period_7d_burned_tokens"),
                    meta.get("statistics_window_burned_tokens"),
                ),
            ),
        ),
        ("total_wallets", "总钱包数量", _fmt_chinese_number(explorer_total_addresses), "公开地址规模", _trend_points(meta, "total_wallets", [explorer_total_addresses])),
        ("daily_active_addresses", "统计日活跃地址数量", _fmt_count_unit(active_wallet_count), "北京时间 00:00 至次日 00:00", _trend_points(meta, "daily_active_addresses", [active_wallet_count])),
        (
            "daily_new_addresses",
            "统计日新增地址数量",
            _fmt_count_unit(new_address_count),
            "北京时间 00:00 至次日 00:00",
            _trend_points(meta, "daily_new_addresses", _trend_average_points(new_address_count, period_7d_new_address_count, period_30d_new_address_count)),
        ),
        (
            "daily_new_power",
            "统计日新增总算力",
            _fmt_power(new_power),
            "同一统计日口径",
            _trend_points(meta, "daily_new_power", _trend_average_points(new_power, period_7d_new_power, period_30d_new_power)),
        ),
        (
            "daily_burned",
            "日销毁币量",
            daily_burned,
            "北京时间统计日口径",
            _trend_points(meta, "daily_burned", _trend_average_points(meta.get("statistics_window_burned_tokens"), meta.get("period_7d_burned_tokens"), meta.get("period_30d_burned_tokens"))),
        ),
        ("power_per_coin", "单币日需算力", power_per_coin, "按矿工 75% 产量估算", _trend_points(meta, "power_per_coin", [power_required_value])),
        ("one_yi_power_output", "1亿算力产出", one_yi_power_output, "按矿工 75% 日产币口径估算：1亿算力 ÷ 单币日需算力。", _trend_points(meta, "one_yi_power_output", [one_yi_power_output_value])),
    ]
    latest_block_value = _latest_block_value(meta)
    marquee_items = [
        ("覆盖率", coverage_label),
        ("流通量", circulation),
        ("当前价格", current_price),
        ("最高价格", highest_price),
        ("预言机触发价", oracle_trigger_price),
        ("总产量", total_supply),
        ("每日产币量", daily_total),
        ("最新区块", f"{latest_block_value:,}"),
        ("算力日志", f"{_as_int(meta.get('rpc_logs_seen')):,}"),
        ("候选地址", _fmt_chinese_number(candidate_count)),
        ("正算力地址", _fmt_chinese_number(positive_power_count)),
        ("统计日活跃地址", _fmt_count_unit(active_wallet_count)),
        ("统计日新增地址", _fmt_count_unit(new_address_count)),
        ("统计日新增算力", _fmt_power(new_power)),
        ("日销毁币量", daily_burned),
        ("7天新增算力", _fmt_power(period_7d_new_power)),
        ("7天新增地址", _fmt_count_unit(period_7d_new_address_count)),
        ("7天销毁", period_7d_burned),
        ("30天新增算力", _fmt_power(period_30d_new_power)),
        ("30天新增地址", _fmt_count_unit(period_30d_new_address_count)),
        ("30天销毁", period_30d_burned),
        ("单币日需算力", power_per_coin),
        ("1亿算力产出", one_yi_power_output),
        ("缓存刷新", f"{_as_int(meta.get('power_cache_refreshed')):,}"),
    ]
    timeline_items = [
        ("最近刷新", generated_at),
        ("统计周期", statistics_window_label),
        ("采集频率", "每 24 小时一次"),
        ("抓取时间", "每日 00:00（北京时间，夜里 24:00）"),
        ("全网流通量", circulation),
        ("当前价格", current_price),
        ("最高价格", highest_price),
        ("预言机触发价", oracle_trigger_price),
        ("累计销毁", total_burned),
        ("全网总算力", _fmt_power(network_total_power)),
        ("统计日活跃地址", _fmt_count_unit(active_wallet_count)),
        ("统计日新增地址", _fmt_count_unit(new_address_count)),
        ("日销毁币量", daily_burned),
        ("7 天新增算力", _fmt_power(period_7d_new_power)),
        ("7 天新增地址", _fmt_count_unit(period_7d_new_address_count)),
        ("7 天销毁", period_7d_burned),
        ("30 天新增算力", _fmt_power(period_30d_new_power)),
        ("30 天新增地址", _fmt_count_unit(period_30d_new_address_count)),
        ("30 天销毁", period_30d_burned),
        ("矿工日产币量", daily_miner),
        ("节点日产币量", daily_node),
        ("单币日需算力", power_per_coin),
        ("1亿算力产出", one_yi_power_output),
        ("合约日志命中", f"{_as_int(meta.get('rpc_logs_seen')):,}"),
        ("算力缓存刷新", f"{_as_int(meta.get('power_cache_refreshed')):,}"),
        ("覆盖目标线", threshold_label),
    ]

    warning_html = _build_warning(meta, threshold_label)
    metric_cards = _build_metric_cards(metric_items)
    growth_cards = f"""
      <article class="growth-card reveal">
        <span class="kicker">7 DAYS</span>
        <h3>7 天新增</h3>
        <div class="line"><span>新增算力</span><b>{escape(_fmt_power(period_7d_new_power))}</b></div>
        <div class="line"><span>新增地址</span><b>{escape(_fmt_count_unit(period_7d_new_address_count))}</b></div>
        <div class="line"><span>销毁数量</span><b>{escape(period_7d_burned)}</b></div>
      </article>
      <article class="growth-card reveal">
        <span class="kicker">30 DAYS</span>
        <h3>30 天新增</h3>
        <div class="line"><span>新增算力</span><b>{escape(_fmt_power(period_30d_new_power))}</b></div>
        <div class="line"><span>新增地址</span><b>{escape(_fmt_count_unit(period_30d_new_address_count))}</b></div>
        <div class="line"><span>销毁数量</span><b>{escape(period_30d_burned)}</b></div>
      </article>
    """
    equation_cards = """
      <article class="fcard reveal"><label>当前实际系数<span>算力倍增</span></label><strong>20x</strong><small>当前 04 方程采用的实际膨胀倍数。</small></article>
      <article class="fcard reveal"><label>最高膨胀系数<span>上限</span></label><strong>160x</strong><small>方程膨胀系数逐级放大时的公开说明上限。</small></article>
      <article class="fcard reveal"><label>执行周期<span>单轮</span></label><strong>8 天</strong><small>圣诞方程与预言机方程按 8 天窗口执行。</small></article>
      <article class="fcard reveal"><label>销毁比例<span>流通量</span></label><strong>35%</strong><small>机制说明中每轮方程触发的流通量销毁比例。</small></article>
    """
    burn_estimate = _build_burn_estimate(meta)
    burn_calculator = _build_burn_calculator(burn_estimate)
    rank_total_count = min(100, len(rows))
    rank_page_size = 10
    rank_total_pages = max(1, (rank_total_count + rank_page_size - 1) // rank_page_size)
    rank_first_page_end = min(rank_page_size, rank_total_count)
    rank_cards = _build_rank_cards(rows, limit=100, page_size=rank_page_size)
    paid_download_panel = _build_paid_download_panel(paid_download_config)
    rank_page_buttons = "".join(
        '<button class="rank-page-button%s" type="button" data-rank-page="%d" aria-current="%s">%d</button>'
        % (" is-active" if page == 1 else "", page, "page" if page == 1 else "false", page)
        for page in range(1, rank_total_pages + 1)
    )
    rank_controls_html = ""
    if rank_total_count > rank_page_size:
        rank_controls_html = f"""
    <div class="rank-pagination" data-rank-pagination>
      <button class="rank-page-button" type="button" data-rank-prev disabled>上一页</button>
      <div class="rank-pages">{rank_page_buttons}</div>
      <button class="rank-page-button" type="button" data-rank-next>下一页</button>
      <span class="rank-count" data-rank-count>第 1 / {rank_total_pages} 页 · 当前显示 1-{rank_first_page_end} / 共 {rank_total_count} 名</span>
    </div>"""
    timeline_rows = _build_timeline(timeline_items)
    marquee_html = _build_marquee(marquee_items)
    embedded_payload = json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    metric_trend_payload = json.dumps(_build_metric_trend_payload(metric_items), ensure_ascii=False).replace("</script>", "<\\/script>")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)}</title>
  <meta name="description" content="{escape(subtitle)}">
  <meta name="theme-color" content="#030612">
{analytics_head}
  <script>
    (function() {{
      var path = window.location.pathname;
      if (path === "/m/") {{
        window.location.replace("/m/index.html");
        return;
      }}
      var params = new URLSearchParams(window.location.search);
      if (params.get("desktop") === "1") return;
      if (path !== "/" && path !== "/index.html") return;
      var ua = navigator.userAgent || "";
      var isMobileUa = /Android|iPhone|iPod|Mobile|Windows Phone/i.test(ua);
      var isNarrowTouch = window.matchMedia && window.matchMedia("(max-width: 760px)").matches && navigator.maxTouchPoints > 0;
      if (isMobileUa || isNarrowTouch) {{
        window.location.replace("/m/index.html");
      }}
    }})();
  </script>
  <style>{SCROLL_DASHBOARD_CSS}
{SHARE_POSTER_CSS}</style>
</head>
<body>
<div class="scroll-progress" aria-hidden="true"></div>
<div class="shell">
  <header class="topbar">
    <div class="brand"><span class="mark"></span>MarsChain Rank</div>
    <div class="top-actions">
      <nav class="nav">
        <a href="#rank">算力排行</a>
        <a href="#pulse">核心数据</a>
        <a href="#growth">周期增长</a>
        <a href="#equation">方程系数</a>
        <a href="#risk">数据说明</a>
      </nav>
      <button class="share-trigger" type="button" data-share-poster data-track="share_poster" data-label="topbar">生成战报</button>
      <button class="lang-toggle" type="button" data-lang-toggle aria-label="Switch language">EN</button>
    </div>
  </header>
  <section class="hero">
    <div class="hero-copy reveal visible">
      <span class="chip">数据已加载 · 北京时间 00:00 每日采集</span>
      <h1>MarsChain<br>算力指挥舱</h1>
      <p class="lead">基于公开区块浏览器、RPC 与 POWER 合约日志，展示全网算力、钱包地址、北京时间统计日新增和头部地址排行。</p>
      <div class="hero-actions">
        <span class="btn hot">覆盖率 {escape(coverage_label)}</span>
        <button class="btn" type="button" data-share-poster data-track="share_poster" data-label="hero">生成战报</button>
        <span class="hero-note">下方优先展示前 100 名算力地址，默认先看前 10。</span>
      </div>
      {warning_html}
      <div class="scroll-hint"><span class="mouse"></span><span>继续查看核心数据、增长趋势与数据说明</span></div>
    </div>
    <aside class="command reveal visible">
      <div class="radar">
        <div class="core"><b>{escape(coverage_label)}</b><span>扫描覆盖率</span></div>
        <i class="node n1"></i><i class="node n2"></i><i class="node n3"></i>
        <div class="mini">
          <div><span>候选地址</span><b>{_fmt_chinese_number(candidate_count)}</b></div>
          <div><span>正算力占比</span><b>{_fmt_percent(positive_ratio)}</b></div>
          <div><span>未覆盖算力</span><b>{_fmt_power(uncovered_power)}</b></div>
        </div>
      </div>
    </aside>
  </section>
  <section id="rank" class="section rank-section">
    <div class="section-head reveal visible">
      <div><span class="kicker">01 / 算力排行</span><h2>头部算力地址排行</h2></div>
      <p>按当前查询到的算力降序展示前 100 名，每页 10 名，共 10 页。</p>
    </div>
    <div class="rank-grid" id="rankGrid" data-page-size="{rank_page_size}" data-total-count="{rank_total_count}">{rank_cards}</div>
    {rank_controls_html}
    {paid_download_panel}
  </section>
  <div class="marquee"><div class="track">{marquee_html}</div></div>
  <section id="pulse" class="section">
    <div class="section-head reveal">
      <div><span class="kicker">02 / 核心数据</span><h2>当前算力概览</h2></div>
      <p>展示最近一次刷新得到的全网算力、产币模型、地址规模与统计日新增数据。</p>
    </div>
    <div class="metrics stagger">{metric_cards}</div>
  </section>
  <section id="growth" class="section">
    <div class="section-head reveal">
      <div><span class="kicker">03 / 周期增长</span><h2>7 天与 30 天新增</h2></div>
      <p>按最近完整北京时间统计日汇总新增算力、新增地址和 TokensBurned 销毁事件。</p>
    </div>
    <div class="growth-grid">{growth_cards}</div>
  </section>
  <section id="equation" class="section">
    <div class="section-head reveal">
      <div><span class="kicker">04 / 方程系数</span><h2>方程膨胀系数</h2></div>
      <p>展示 MarsChain 双方程机制中的公开膨胀参数，帮助理解算力倍增、周期与销毁比例。</p>
    </div>
    <div class="equation-grid">{equation_cards}</div>
    {burn_calculator}
    <p class="equation-note">当前 04 方程实际系数为 20x，后续最高可按机制说明逐级放大至 160x；本区块属于机制口径展示，不等同于当前实时收益承诺。</p>
  </section>
  <section id="risk" class="section">
    <div class="section-head reveal">
      <div><span class="kicker">05 / 数据说明</span><h2>数据来源与准确性说明</h2></div>
      <p>说明公开接口、RPC 节点和合约日志可能带来的延迟、遗漏与统计偏差。</p>
    </div>
    <div class="telemetry">
      <div class="timeline panel reveal">{timeline_rows}</div>
      <article class="risk panel reveal">
        <h3>公开口径说明</h3>
        <p>榜单基于公开区块浏览器接口、RPC 与 POWER 合约日志生成。总产量采用官方经济模型口径：{escape(total_supply)} 枚永不增发；每日产币量按官方公式与当前链龄计算；产量分配采用矿工 75%、节点 25%，所以单币日需算力按“全网总算力 ÷ 矿工日产币量”估算。公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，都可能造成与官方后台的差异。</p>
      </article>
    </div>
  </section>
  <footer class="footer">基于公开 API、RPC 与合约日志生成的 best effort 榜单 · 最近刷新：{escape(generated_at)} · 统计周期：{escape(statistics_window_label)}</footer>
</div>
<script id="rankData" type="application/json">{embedded_payload}</script>
<script id="metricTrendData" type="application/json">{metric_trend_payload}</script>
<script>{SCROLL_DASHBOARD_JS}
{LIVE_PRICE_JS}
{SHARE_POSTER_JS}
{METRIC_TREND_JS}
{LANGUAGE_TOGGLE_JS}</script>
</body>
</html>
"""


MOBILE_DASHBOARD_CSS = r"""
:root {
  --bg: #030712;
  --panel: rgba(12, 23, 44, .88);
  --panel2: rgba(19, 35, 64, .78);
  --line: rgba(121, 225, 255, .18);
  --line2: rgba(121, 225, 255, .34);
  --text: #f5fbff;
  --muted: #9aabc5;
  --cyan: #56efff;
  --blue: #7e8cff;
  --green: #81f5b2;
  --amber: #ffd37e;
  --font: "Avenir Next", "PingFang SC", "Microsoft YaHei", sans-serif;
  --mono: "SFMono-Regular", "JetBrains Mono", monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--bg); color: var(--text); }
body {
  margin: 0;
  min-height: 100vh;
  overflow-x: hidden;
  font-family: var(--font);
  background:
    radial-gradient(circle at 12% 0%, rgba(86,239,255,.24), transparent 34%),
    radial-gradient(circle at 94% 9%, rgba(126,140,255,.24), transparent 36%),
    linear-gradient(180deg, #030712 0%, #071429 48%, #030712 100%);
}
body:before {
  content: "";
  position: fixed;
  inset: -20% -35% auto;
  height: 520px;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(121,225,255,.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(121,225,255,.06) 1px, transparent 1px);
  background-size: 44px 44px;
  transform: perspective(620px) rotateX(62deg) translateY(-120px);
  opacity: .55;
}
a { color: inherit; }
.m-shell { width: calc(100% - 28px); max-width: 420px; margin: 0 auto; position: relative; z-index: 1; }
.m-top {
  position: sticky;
  top: 10px;
  z-index: 30;
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 24px;
  background: rgba(6, 14, 30, .72);
  backdrop-filter: blur(18px);
  box-shadow: 0 18px 48px rgba(0,0,0,.34);
}
.m-brand { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.m-brand-main { display: flex; align-items: center; gap: 10px; min-width: 0; font-weight: 950; }
.m-mark { width: 30px; height: 30px; border-radius: 12px; background: conic-gradient(from 210deg, var(--cyan), var(--blue), #ff7ac6, var(--cyan)); box-shadow: 0 0 30px rgba(86,239,255,.35); }
.m-brand-actions { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; }
.m-desktop-link, .m-lang-toggle, .m-share-button { color: #bfefff; text-decoration: none; border: 1px solid var(--line); border-radius: 999px; padding: 7px 10px; font-size: 12px; font-weight: 900; font-family: inherit; line-height: 1; background: rgba(255,255,255,.045); }
.m-lang-toggle { min-width: 42px; cursor: pointer; color: #d9faff; background: rgba(86,239,255,.075); }
.m-share-button { cursor: pointer; color: #03121b; border-color: transparent; background: linear-gradient(135deg, var(--cyan), var(--blue)); }
.m-nav { display: flex; gap: 8px; margin-top: 12px; overflow-x: auto; scrollbar-width: none; }
.m-nav::-webkit-scrollbar { display: none; }
.m-nav a { flex: 0 0 auto; text-decoration: none; border: 1px solid rgba(121,225,255,.14); border-radius: 999px; padding: 8px 11px; color: #bfcee4; background: rgba(255,255,255,.045); font-size: 12px; font-weight: 900; }
.m-hero { padding: 34px 0 36px; }
.m-chip { display: inline-flex; align-items: center; gap: 8px; border: 1px solid rgba(86,239,255,.28); border-radius: 999px; padding: 8px 12px; color: #c5fbff; background: rgba(86,239,255,.08); font-size: 12px; font-weight: 950; }
.m-chip:before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 16px var(--green); }
.m-hero h1 { margin: 18px 0 14px; font-size: clamp(46px, 15vw, 60px); line-height: .9; letter-spacing: -.075em; }
.m-hero h1 span { display: block; background: linear-gradient(110deg, #fff, #bff8ff 48%, #cbd2ff); -webkit-background-clip: text; background-clip: text; color: transparent; }
.m-lead { margin: 0; color: #c6d4ea; font-size: 15px; line-height: 1.72; }
.m-hero-grid { display: grid; gap: 10px; margin-top: 22px; }
.m-primary { border: 0; border-radius: 22px; padding: 18px; color: #03111a; background: linear-gradient(135deg, var(--cyan), var(--blue)); box-shadow: 0 22px 56px rgba(86,239,255,.20); }
.m-primary span, .m-card span { display: block; font-size: 12px; font-weight: 950; opacity: .82; }
.m-primary b { display: block; margin-top: 8px; font-size: 42px; letter-spacing: -.07em; }
.m-card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.m-card { min-width: 0; border: 1px solid var(--line); border-radius: 20px; padding: 15px; background: linear-gradient(180deg, var(--panel2), rgba(8, 16, 32, .86)); box-shadow: inset 0 1px 0 rgba(255,255,255,.06); display: flex; flex-direction: column; min-height: 182px; cursor: pointer; }
.m-card:focus-visible { outline: 2px solid rgba(86,239,255,.72); outline-offset: 3px; }
.m-card b { display: block; margin-top: 12px; font-size: 25px; line-height: 1; letter-spacing: -.055em; overflow-wrap: anywhere; }
.m-price-stack { margin-top: 9px; font-size: 11px; }
.m-price-stack strong { font-size: 11px; }
.m-card small { display: block; margin-top: 8px; color: #8394ad; font-size: 11px; line-height: 1.45; }
.m-card .metric-trend { margin-top: auto; padding-top: 12px; }
.m-card .metric-trend svg { height: 38px; }
.m-card .metric-trend path.area { fill: rgba(86,239,255,.14); opacity: .9; }
.m-card .metric-trend path.line { fill: none; stroke: var(--cyan); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
.m-card .metric-trend circle { fill: var(--green); }
.m-card .metric-trend-label { display: flex; justify-content: space-between; gap: 6px; margin-top: 3px; color: #788aa4; font-size: 10px; line-height: 1.25; }
.m-card .metric-trend.is-sampling path.line { stroke-dasharray: 5 7; opacity: .72; }
.m-card .metric-trend.is-sampling circle { display: none; }
.trend-modal[hidden] { display: none; }
.trend-modal {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  align-items: end;
  padding: 12px;
  background: rgba(2, 7, 18, .72);
  backdrop-filter: blur(18px);
}
.trend-panel {
  width: calc(100vw - 24px);
  max-width: 366px;
  max-height: calc(100vh - 24px);
  justify-self: start;
  overflow: auto;
  border: 1px solid rgba(86,239,255,.28);
  border-radius: 22px;
  background: linear-gradient(180deg, rgba(15,27,50,.98), rgba(5,12,26,.99));
  box-shadow: 0 26px 90px rgba(0,0,0,.62), inset 0 1px 0 rgba(255,255,255,.08);
}
.trend-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 18px 16px 12px;
}
.trend-title span { color: var(--amber); font-size: 11px; font-weight: 950; letter-spacing: .12em; }
.trend-title h3 { margin: 7px 0 8px; font-size: 29px; line-height: 1; letter-spacing: -.055em; }
.trend-title p { margin: 0; color: #91a4bf; font-size: 12px; line-height: 1.55; }
.trend-close {
  flex: 0 0 auto;
  width: 36px;
  height: 36px;
  border: 1px solid rgba(86,239,255,.25);
  border-radius: 999px;
  color: #ddf8ff;
  background: rgba(255,255,255,.055);
  font-size: 22px;
  line-height: 1;
}
.trend-controls { display: flex; flex-wrap: wrap; gap: 7px; padding: 0 16px 12px; }
.trend-controls button {
  flex: 1 1 calc(50% - 7px);
  min-height: 34px;
  border: 1px solid rgba(86,239,255,.22);
  border-radius: 999px;
  color: #bfefff;
  background: rgba(255,255,255,.055);
  font: 900 12px var(--font);
}
.trend-controls button.is-active {
  color: #04111a;
  border-color: transparent;
  background: linear-gradient(135deg, var(--cyan), var(--blue));
}
.trend-chart {
  position: relative;
  margin: 0 16px;
  min-height: 230px;
  border: 1px solid rgba(86,239,255,.16);
  border-radius: 17px;
  background:
    linear-gradient(rgba(86,239,255,.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(86,239,255,.045) 1px, transparent 1px),
    rgba(4, 12, 27, .68);
  background-size: 100% 25%, 12.5% 100%, auto;
  overflow: hidden;
  cursor: crosshair;
}
.trend-chart svg { display: block; width: 100%; height: 230px; }
.trend-chart .bar { fill: rgba(214,86,255,.55); }
.trend-chart .area { fill: rgba(86,239,255,.16); }
.trend-chart .line { fill: none; stroke: var(--cyan); stroke-width: 4; stroke-linecap: round; stroke-linejoin: round; filter: drop-shadow(0 0 14px rgba(86,239,255,.38)); }
.trend-chart .cursor-line { stroke: rgba(255,255,255,.66); stroke-width: 1.5; stroke-dasharray: 5 6; }
.trend-chart .cursor-dot { fill: var(--green); filter: drop-shadow(0 0 12px rgba(129,245,178,.64)); }
.trend-chart .axis-line { stroke: rgba(255,255,255,.12); stroke-width: 1; }
.trend-chart .value-label { fill: #eaf7ff; font-size: 12px; font-weight: 900; paint-order: stroke; stroke: rgba(4,12,27,.92); stroke-width: 5px; stroke-linejoin: round; }
.trend-chart .date-label { fill: #7f91aa; font-size: 11px; font-weight: 850; }
.trend-empty { position: absolute; inset: 0; display: grid; place-items: center; color: #9fb0c9; font-weight: 900; text-align: center; padding: 20px; }
.trend-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 12px 16px 18px; }
.trend-stat { min-width: 0; padding: 12px; border: 1px solid rgba(86,239,255,.14); border-radius: 14px; background: rgba(255,255,255,.045); }
.trend-stat span { display: block; color: #91a4bf; font-size: 12px; font-weight: 900; }
.trend-stat b { display: block; margin-top: 8px; font-size: 18px; letter-spacing: -.035em; word-break: break-word; }
.trend-stat small { display: block; margin-top: 4px; color: #72849d; font-size: 11px; line-height: 1.35; }
.trend-foot { margin: -4px 16px 18px; color: #899bb6; font-size: 12px; line-height: 1.55; }
.m-section { padding: 34px 0; }
.m-section-head { display: flex; justify-content: space-between; gap: 14px; align-items: flex-end; margin-bottom: 14px; }
.m-kicker { color: #9cf7ff; font-size: 11px; font-weight: 950; letter-spacing: .12em; }
.m-section h2 { margin: 7px 0 0; font-size: 30px; line-height: 1; letter-spacing: -.06em; }
.m-section-head p { max-width: 145px; margin: 0; color: #8fa2bd; font-size: 12px; line-height: 1.55; text-align: right; }
.m-list { display: grid; gap: 10px; }
.m-flow-card { border: 1px solid var(--line); border-radius: 22px; padding: 17px; background: var(--panel); }
.m-flow-card label { display: flex; justify-content: space-between; gap: 10px; color: #b8c8df; font-size: 13px; font-weight: 950; }
.m-flow-card strong { display: block; margin-top: 20px; font-size: 38px; line-height: 1; letter-spacing: -.06em; }
.m-flow-card small { display: block; margin-top: 10px; color: #8798b2; font-size: 12px; line-height: 1.55; }
.m-burn-calculator {
  display: grid;
  gap: 12px;
  margin-top: 12px;
  padding: 16px;
  border: 1px solid rgba(255,211,126,.22);
  border-radius: 22px;
  background:
    radial-gradient(circle at 0% 0%, rgba(255,211,126,.18), transparent 40%),
    linear-gradient(180deg, rgba(24,35,56,.94), rgba(8,15,29,.94));
}
.m-burn-calculator h3 { margin: 4px 0 6px; font-size: 23px; line-height: 1.05; letter-spacing: -.035em; }
.m-burn-calculator p { margin: 0; color: #91a2ba; font-size: 12px; line-height: 1.55; }
.m-burn-stats { display: grid; gap: 8px; }
.m-burn-stat { display: flex; justify-content: space-between; gap: 10px; padding: 10px 0; border-bottom: 1px solid rgba(121,225,255,.10); font-size: 12px; }
.m-burn-stat span { color: #91a2ba; font-weight: 900; }
.m-burn-stat b { color: #f4f8ff; text-align: right; overflow-wrap: anywhere; }
.m-burn-input { display: grid; gap: 8px; }
.m-burn-input label { color: #c3d0e4; font-size: 12px; font-weight: 950; }
.m-burn-input input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid rgba(255,211,126,.26);
  border-radius: 14px;
  padding: 13px 14px;
  color: #fff8e1;
  background: rgba(255,255,255,.06);
  font: 900 16px/1 var(--mono);
  outline: none;
}
.m-burn-result { border: 1px solid rgba(86,239,255,.18); border-radius: 16px; padding: 13px; background: rgba(86,239,255,.07); }
.m-burn-result span { display: block; color: #9bddeb; font-size: 11px; font-weight: 950; }
.m-burn-result b { display: block; margin-top: 7px; color: #fff; font-size: 24px; letter-spacing: -.035em; overflow-wrap: anywhere; }
.m-burn-result small { display: block; margin-top: 7px; color: #8396b1; font-size: 11px; line-height: 1.45; }
.m-rank-card.is-page-hidden { display: none; }
.m-rank-card { border: 1px solid var(--line); border-radius: 21px; padding: 15px; background: linear-gradient(180deg, rgba(20,36,66,.88), rgba(8,16,32,.92)); }
.m-rank-top { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.m-rank-top em { font-style: normal; font-family: var(--mono); color: #a9b9d2; }
.m-rank-top strong { font-size: 25px; letter-spacing: -.045em; }
.m-rank-card code { display: block; margin: 13px 0 12px; color: #e5efff; font-family: var(--mono); font-size: 12px; word-break: break-all; }
.m-bar { display: block; height: 7px; border-radius: 999px; background: rgba(255,255,255,.08); overflow: hidden; }
.m-bar i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--cyan), var(--blue)); box-shadow: 0 0 18px rgba(86,239,255,.34); }
.m-rank-card { padding: 14px; }
.m-rank-card code { font-size: 12px; margin: 11px 0 0; }
.m-bar { display: none; }
.m-rank-pagination { display: grid; gap: 9px; margin-top: 12px; }
.m-rank-pages { display: grid; grid-template-columns: repeat(5, 1fr); gap: 7px; }
.m-rank-page-button {
  min-height: 42px;
  border: 1px solid rgba(86,239,255,.35);
  border-radius: 999px;
  color: #ccefff;
  background: rgba(86,239,255,.08);
  font: 950 13px var(--font);
}
.m-rank-page-button.is-active {
  color: #03121b;
  background: linear-gradient(135deg, var(--cyan), var(--blue));
}
.m-rank-page-button:disabled { opacity: .42; }
.m-rank-count { color: #91a4bf; text-align: center; font-size: 12px; font-weight: 900; }
.m-paid-download {
  display: grid;
  gap: 13px;
  margin-top: 13px;
  padding: 15px;
  border: 1px solid rgba(255,211,126,.26);
  border-radius: 22px;
  background:
    radial-gradient(circle at 14% 0%, rgba(255,211,126,.15), transparent 38%),
    linear-gradient(180deg, rgba(20,34,60,.9), rgba(8,16,32,.92));
}
.m-paid-copy span { color: var(--amber); font-size: 11px; font-weight: 950; letter-spacing: .12em; }
.m-paid-copy h3 { margin: 7px 0 8px; font-size: 25px; line-height: 1; letter-spacing: -.045em; }
.m-paid-copy p { margin: 0; color: #b4c4da; font-size: 12px; line-height: 1.65; }
.m-paid-rules { display: grid; gap: 7px; margin: 11px 0 0; padding: 0; list-style: none; color: #d1def0; font-size: 12px; line-height: 1.55; }
.m-paid-rules li { position: relative; padding-left: 16px; }
.m-paid-rules li:before { content: ""; position: absolute; left: 0; top: .68em; width: 5px; height: 5px; border-radius: 999px; background: var(--amber); }
.m-paid-box { display: grid; gap: 9px; }
.m-paid-amount span, .m-paid-address span { display: block; color: #9fb0c9; font-size: 12px; font-weight: 900; }
.m-paid-amount b { display: block; margin-top: 6px; color: var(--amber); font-size: 25px; }
.m-paid-address code { display: block; margin: 6px 0; color: #f2f8ff; font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.m-paid-address button, .m-paid-controls button, .m-paid-tx button, .m-paid-controls select, .m-paid-tx input {
  width: 100%;
  min-height: 42px;
  border: 1px solid rgba(86,239,255,.28);
  border-radius: 14px;
  color: #eaf7ff;
  background: rgba(255,255,255,.06);
  font: 900 13px var(--font);
}
.m-paid-address button, .m-paid-controls button, .m-paid-tx button { padding: 0 12px; }
.m-paid-controls, .m-paid-tx { display: grid; gap: 8px; }
.m-paid-controls button, .m-paid-tx button { color: #03121b; border: 0; background: linear-gradient(135deg, var(--cyan), var(--blue)); }
.m-paid-controls select, .m-paid-tx input { padding: 0 12px; }
.m-paid-status { color: #91a4bf; font-size: 12px; line-height: 1.55; }
.m-paid-status.is-error { color: #ffb7b7; }
.m-paid-status.is-ok { color: #9df4c3; }
.m-paid-link { display: none; color: var(--cyan); font-size: 13px; font-weight: 950; text-decoration: none; }
.m-paid-link.is-ready { display: inline-flex; }
.m-paid-download button:disabled, .m-paid-download input:disabled { opacity: .45; }
.m-note { border: 1px solid rgba(255,211,126,.24); border-radius: 22px; padding: 17px; background: rgba(255,211,126,.07); color: #ffe7b8; font-size: 13px; line-height: 1.75; }
.m-meta { border: 1px solid var(--line); border-radius: 22px; padding: 15px; background: var(--panel); }
.m-meta-row { display: flex; justify-content: space-between; gap: 14px; padding: 12px 0; border-bottom: 1px solid rgba(121,225,255,.10); font-size: 13px; }
.m-meta-row:last-child { border-bottom: 0; }
.m-meta-row span { color: #9cafc9; }
.m-meta-row b { text-align: right; font-family: var(--mono); word-break: break-word; }
.m-footer { padding: 34px 8px 42px; text-align: center; color: #74849b; font-size: 12px; line-height: 1.7; }
.m-reveal { opacity: 0; transform: translateY(18px); transition: opacity .52s ease, transform .52s ease; }
.m-reveal.visible { opacity: 1; transform: none; }
@media (max-width: 360px) {
  .m-shell { width: calc(100% - 20px); }
  .m-card-grid { grid-template-columns: 1fr; }
  .m-section-head { display: block; }
  .m-section-head p { max-width: none; text-align: left; margin-top: 8px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *:before, *:after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
"""


MOBILE_DASHBOARD_JS = r"""
const mobileObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) entry.target.classList.add('visible');
  });
}, { threshold: .12 });

document.querySelectorAll('.m-reveal').forEach((node) => mobileObserver.observe(node));

const mobileRankList = document.getElementById('mobileRankList');
const mobileRankPagination = document.querySelector('[data-mobile-rank-pagination]');
if (mobileRankList && mobileRankPagination) {
  const pageSize = Number(mobileRankList.dataset.pageSize || 10);
  const cards = Array.from(mobileRankList.querySelectorAll('.m-rank-card'));
  const totalCount = Number(mobileRankList.dataset.totalCount || cards.length);
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  const buttons = Array.from(mobileRankPagination.querySelectorAll('[data-mobile-rank-page]'));
  const prev = mobileRankPagination.querySelector('[data-mobile-rank-prev]');
  const next = mobileRankPagination.querySelector('[data-mobile-rank-next]');
  const count = mobileRankPagination.querySelector('[data-mobile-rank-count]');
  let currentPage = 1;
  const renderMobileRankPage = (page) => {
    currentPage = Math.max(1, Math.min(totalPages, page));
    const start = (currentPage - 1) * pageSize;
    const end = Math.min(start + pageSize, totalCount);
    cards.forEach((card, index) => {
      const visible = index >= start && index < end;
      card.classList.toggle('is-page-hidden', !visible);
      if (visible) card.classList.add('visible');
    });
    buttons.forEach((button) => {
      const active = Number(button.dataset.mobileRankPage) === currentPage;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-current', active ? 'page' : 'false');
    });
    if (prev) prev.disabled = currentPage === 1;
    if (next) next.disabled = currentPage === totalPages;
    if (count) count.textContent = `第 ${currentPage} / ${totalPages} 页 · 当前显示 ${start + 1}-${end} / 共 ${totalCount} 名`;
    if (window.applyMarsLanguage) window.applyMarsLanguage();
  };
  buttons.forEach((button) => {
    button.addEventListener('click', () => renderMobileRankPage(Number(button.dataset.mobileRankPage || 1)));
  });
  if (prev) prev.addEventListener('click', () => renderMobileRankPage(currentPage - 1));
  if (next) next.addEventListener('click', () => renderMobileRankPage(currentPage + 1));
  renderMobileRankPage(1);
}

const setupMobilePaidDownloadPanel = (panel) => {
  const apiBase = (panel.dataset.apiBase || '').replace(/\/+$/, '');
  const createButton = panel.querySelector('[data-paid-create]');
  const verifyButton = panel.querySelector('[data-paid-verify]');
  const copyButton = panel.querySelector('[data-paid-copy]');
  const addressNode = panel.querySelector('[data-paid-address]');
  const txInput = panel.querySelector('[data-paid-tx]');
  const formatSelect = panel.querySelector('[data-paid-format]');
  const statusNode = panel.querySelector('[data-paid-status]');
  const linkNode = panel.querySelector('[data-paid-link]');
  let orderId = '';

  const setStatus = (text, mode = '') => {
    if (!statusNode) return;
    statusNode.textContent = text;
    statusNode.classList.toggle('is-error', mode === 'error');
    statusNode.classList.toggle('is-ok', mode === 'ok');
    if (window.applyMarsLanguage) window.applyMarsLanguage();
  };
  const postJson = async (path, body) => {
    const response = await fetch(`${apiBase}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (error) {}
    if (!response.ok) {
      throw new Error(payload.message || payload.error || '请求失败');
    }
    return payload;
  };

  if (!apiBase) {
    if (createButton) createButton.disabled = true;
    if (verifyButton) verifyButton.disabled = true;
    if (txInput) txInput.disabled = true;
    setStatus('付费下载接口待接入', 'error');
    return;
  }

  if (copyButton && addressNode) {
    copyButton.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(addressNode.textContent.trim());
        setStatus('收款地址已复制', 'ok');
      } catch (error) {
        setStatus(addressNode.textContent.trim(), 'ok');
      }
    });
  }

  if (createButton) {
    createButton.addEventListener('click', async () => {
      createButton.disabled = true;
      setStatus('正在生成付款订单...');
      try {
        const result = await postJson('/orders', { format: formatSelect ? formatSelect.value : 'xlsx' });
        orderId = result.orderId || result.order_id || '';
        if (txInput) txInput.disabled = false;
        if (verifyButton) verifyButton.disabled = false;
        setStatus(`订单已生成：支付 ${result.amountMars || '1000'} MARS 后提交交易哈希。`, 'ok');
      } catch (error) {
        setStatus(error.message || '订单生成失败', 'error');
      } finally {
        createButton.disabled = false;
      }
    });
  }

  if (verifyButton) {
    verifyButton.addEventListener('click', async () => {
      const txHash = txInput ? txInput.value.trim() : '';
      if (!orderId) {
        setStatus('请先生成付款订单', 'error');
        return;
      }
      if (!/^0x[a-fA-F0-9]{64}$/.test(txHash)) {
        setStatus('交易哈希格式不正确', 'error');
        return;
      }
      verifyButton.disabled = true;
      setStatus('正在核销链上付款...');
      try {
        const result = await postJson(`/orders/${encodeURIComponent(orderId)}/verify`, {
          txHash,
          format: formatSelect ? formatSelect.value : 'xlsx',
        });
        if (result.status === 'WAITING_CONFIRMATIONS') {
          setStatus(`交易已找到，等待确认数 ${result.confirmations || 0}/${result.requiredConfirmations || 3}`);
          return;
        }
        if (result.downloadUrl && linkNode) {
          linkNode.href = result.downloadUrl;
          linkNode.classList.add('is-ready');
          setStatus('核销成功，下载链接 1 小时内有效。', 'ok');
        } else {
          setStatus('核销成功，请重新打开下载链接。', 'ok');
        }
      } catch (error) {
        setStatus(error.message || '核销失败', 'error');
      } finally {
        verifyButton.disabled = false;
      }
    });
  }
};

const setupMobileBurnCalculator = (panel) => {
  const rate = Number(panel.dataset.burnRate || 0);
  const input = panel.querySelector('[data-burn-power]');
  const result = panel.querySelector('[data-burn-result]');
  const note = panel.querySelector('[data-burn-note]');
  if (!input || !result) return;
  const trimZeros = (value) => value.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  const parsePower = (value) => {
    let text = String(value || '').replace(/,/g, '').replace(/\s+/g, '').toLowerCase();
    let multiplier = 1;
    if (text.includes('万亿')) {
      multiplier = 1_0000_0000_0000;
      text = text.replace(/万亿/g, '');
    } else if (text.includes('亿')) {
      multiplier = 1_0000_0000;
      text = text.replace(/亿/g, '');
    } else if (text.includes('万')) {
      multiplier = 1_0000;
      text = text.replace(/万/g, '');
    } else if (text.endsWith('b')) {
      multiplier = 1_000_000_000;
      text = text.slice(0, -1);
    } else if (text.endsWith('m')) {
      multiplier = 1_000_000;
      text = text.slice(0, -1);
    } else if (text.endsWith('k')) {
      multiplier = 1_000;
      text = text.slice(0, -1);
    }
    const match = text.match(/-?\d+(?:\.\d+)?/);
    if (!match) return NaN;
    return Number(match[0]) * multiplier;
  };
  const formatChinese = (number, suffix = '') => {
    if (!Number.isFinite(number)) return '待刷新';
    const abs = Math.abs(number);
    if (abs >= 1_0000_0000_0000) return `${trimZeros((number / 1_0000_0000_0000).toFixed(3))}万亿${suffix}`;
    if (abs >= 1_0000_0000) return `${trimZeros((number / 1_0000_0000).toFixed(3))}亿${suffix}`;
    if (abs >= 1_0000) return `${trimZeros((number / 1_0000).toFixed(3))}万${suffix}`;
    return `${trimZeros(number.toLocaleString('zh-CN', { maximumFractionDigits: 3 }))}${suffix}`;
  };
  const update = () => {
    const power = parsePower(input.value);
    if (!Number.isFinite(power) || power <= 0 || rate <= 0) {
      result.textContent = '请输入有效算力';
      if (note) note.textContent = '支持 1亿、5亿、1000万 或纯数字。';
      return;
    }
    const burned = power * rate;
    result.textContent = formatChinese(burned, '枚');
    if (note) note.textContent = `输入算力 ${formatChinese(power)}，按当前比例估算。`;
  };
  input.addEventListener('input', update);
  update();
};

document.querySelectorAll('[data-burn-calculator]').forEach(setupMobileBurnCalculator);
document.querySelectorAll('[data-paid-panel]').forEach(setupMobilePaidDownloadPanel);

document.querySelectorAll('[data-track]').forEach((node) => {
  node.addEventListener('click', () => {
    window._hmt = window._hmt || [];
    window._hmt.push(['_trackEvent', 'marschain_mobile', node.dataset.track || 'click', node.dataset.label || '']);
    if (window.clarity) window.clarity('event', 'mobile_' + (node.dataset.track || 'click'));
  });
});
"""


LIVE_PRICE_JS = r"""
(() => {
  const targets = () => Array.from(document.querySelectorAll('[data-live-price]'));
  if (!targets().length) return;
  const intervalMs = 10 * 60 * 1000;
  const pricePath = () => {
    const path = window.location.pathname || '';
    const prefix = /\/m(?:\/|\/index\.html)?$/.test(path) || path.includes('/m/') ? '../' : '';
    return `${prefix}data/price.json?v=${Date.now()}`;
  };
  const applyPrice = (payload) => {
    if (!payload || typeof payload !== 'object') return;
    const price = String(payload.price_display || payload.price || '').trim();
    const highest = String(payload.highest_price_display || payload.highest_price || '').trim();
    const trigger = String(payload.oracle_trigger_price_display || payload.oracle_trigger_price || '').trim();
    const checked = payload.changed_at || payload.checked_at;
    if (price) {
      targets().forEach((node) => {
        node.textContent = price;
        if (checked) node.setAttribute('title', `价格变动时间：${checked}`);
      });
    }
    if (highest) {
      document.querySelectorAll('[data-live-highest-price]').forEach((node) => {
        node.textContent = highest;
        if (checked) node.setAttribute('title', `最高价检查时间：${checked}`);
      });
    }
    if (trigger) {
      document.querySelectorAll('[data-live-oracle-trigger-price]').forEach((node) => {
        node.textContent = trigger;
        if (checked) node.setAttribute('title', `预言机触发价：最高价的50%`);
      });
    }
  };
  const refresh = async () => {
    if (document.hidden) return;
    try {
      const response = await fetch(pricePath(), { cache: 'no-store' });
      if (!response.ok) return;
      applyPrice(await response.json());
    } catch (error) {}
  };
  refresh();
  window.setInterval(refresh, intervalMs);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
})();
"""


SHARE_POSTER_JS = r"""
(() => {
  const buttons = Array.from(document.querySelectorAll('[data-share-poster]'));
  if (!buttons.length) return;

  const SITE_URL = 'https://www.marschainrank.com/';
  const POSTER_WIDTH = 1024;
  const POSTER_HEIGHT = 1536;
  let modal = null;
  let canvas = null;

  const readJson = (id, fallback) => {
    const node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent || '');
    } catch (error) {
      return fallback;
    }
  };
  const rankPayload = () => readJson('rankData', { meta: {}, rows: [] }) || { meta: {}, rows: [] };
  const trendPayload = () => readJson('metricTrendData', []) || [];
  const trimZeros = (value) => value.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  const asNumber = (value) => {
    if (value === null || value === undefined || value === '') return NaN;
    const number = Number(String(value).replace(/,/g, ''));
    return Number.isFinite(number) ? number : NaN;
  };
  const compactNumber = (value, digits = 3) => {
    const number = asNumber(value);
    if (!Number.isFinite(number)) return '待刷新';
    const sign = number < 0 ? '-' : '';
    const abs = Math.abs(number);
    if (abs >= 1_0000_0000_0000) return `${sign}${trimZeros((abs / 1_0000_0000_0000).toFixed(digits))}万亿`;
    if (abs >= 1_0000_0000) return `${sign}${trimZeros((abs / 1_0000_0000).toFixed(digits))}亿`;
    if (abs >= 1_0000) return `${sign}${trimZeros((abs / 1_0000).toFixed(digits))}万`;
    if (abs > 0 && abs < 1) return `${sign}${trimZeros(abs.toFixed(6))}`;
    return `${sign}${trimZeros(abs.toLocaleString('zh-CN', { maximumFractionDigits: digits }))}`;
  };
  const formatCount = (value) => {
    const text = compactNumber(value);
    return text === '待刷新' ? text : `${text}个`;
  };
  const formatToken = (value, display) => {
    if (display) return String(display);
    const number = asNumber(value);
    if (!Number.isFinite(number)) return '待刷新';
    const scaled = Math.abs(number) > 1e12 ? number / 1e18 : number;
    return `${compactNumber(scaled)}枚`;
  };
  const formatPrice = (meta) => {
    const live = document.querySelector('[data-live-price]');
    const text = live ? live.textContent.trim() : '';
    return text || String(meta.network_current_price_display || meta.network_current_price || '待刷新');
  };
  const formatRawPrice = (value) => {
    if (value === null || value === undefined || value === '') return '待刷新';
    const number = asNumber(value);
    if (!Number.isFinite(number)) {
      const text = String(value || '').trim();
      return text || '待刷新';
    }
    return number < 1 ? number.toFixed(6) : number.toFixed(3);
  };
  const formatHighestPrice = (meta) => {
    const live = document.querySelector('[data-live-highest-price]');
    const text = live ? live.textContent.trim() : '';
    return text || String(meta.network_highest_price_display || formatRawPrice(meta.network_highest_price));
  };
  const formatOracleTriggerPrice = (meta) => {
    const live = document.querySelector('[data-live-oracle-trigger-price]');
    const text = live ? live.textContent.trim() : '';
    if (text) return text;
    const highest = asNumber(meta.network_highest_price || meta.network_highest_price_display);
    return Number.isFinite(highest) ? formatRawPrice(highest * 0.5) : '待刷新';
  };
  const formatPercent = (value) => {
    let number = asNumber(value);
    if (!Number.isFinite(number)) return '待刷新';
    if (Math.abs(number) <= 1) number *= 100;
    return `${number.toFixed(2)}%`;
  };
  const metricSeries = (key) => {
    const item = trendPayload().find((entry) => entry && entry.key === key);
    const points = item && Array.isArray(item.points) ? item.points : [];
    return points.map((point) => asNumber(point && point.value)).filter(Number.isFinite);
  };
  const metricDelta = (key, current, formatter) => {
    const series = metricSeries(key);
    const currentNumber = asNumber(current);
    let delta = NaN;
    if (Number.isFinite(currentNumber) && series.length) {
      let previous = series[series.length - 1];
      if (Math.abs(previous - currentNumber) <= Math.max(Math.abs(currentNumber), 1) * 1e-12 && series.length >= 2) {
        previous = series[series.length - 2];
      }
      delta = currentNumber - previous;
    } else if (series.length >= 2) {
      delta = series[series.length - 1] - series[series.length - 2];
    }
    if (!Number.isFinite(delta)) return '等待下一期';
    if (Math.abs(delta) <= Math.max(Math.abs(currentNumber), 1) * 1e-12) return '较上一期 持平';
    const label = delta >= 0 ? '新增' : '减少';
    return `${label} ${formatter(Math.abs(delta))}`;
  };
  const daysOnline = (meta) => {
    const generated = asNumber(meta.generated_at) || Math.floor(Date.now() / 1000);
    const genesis = asNumber(meta.emission_genesis_timestamp);
    if (!Number.isFinite(genesis) || genesis <= 0) return '上线天数待刷新';
    return `上线第 ${Math.max(1, Math.floor((generated - genesis) / 86400) + 1)} 天`;
  };
  const christmasCountdown = () => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    let target = new Date(now.getFullYear(), 11, 25);
    if (today > target) target = new Date(now.getFullYear() + 1, 11, 25);
    const days = Math.max(0, Math.ceil((target - today) / 86400000));
    return `距离圣诞方程 ${days} 天`;
  };
  const generatedLabel = (meta) => String(meta.generated_at_local || '').slice(0, 16) || new Date().toLocaleString('zh-CN', { hour12: false }).slice(0, 16);
  const reportDateLabel = (meta) => {
    const raw = String(meta.generated_at_local || meta.statistics_window_end_local || '').slice(0, 10);
    const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (match) return `${match[1]}年${match[2]}月${match[3]}日`;
    const now = new Date();
    return `${now.getFullYear()}年${String(now.getMonth() + 1).padStart(2, '0')}月${String(now.getDate()).padStart(2, '0')}日`;
  };

  const roundedRect = (ctx, x, y, w, h, r) => {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  };
  const fillRound = (ctx, x, y, w, h, r, fill, stroke) => {
    roundedRect(ctx, x, y, w, h, r);
    if (fill) {
      ctx.fillStyle = fill;
      ctx.fill();
    }
    if (stroke) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  };
  const fitText = (ctx, text, x, y, maxWidth, font, minSize = 22, align = 'left') => {
    const match = String(font).match(/(\d+)px/);
    const startSize = match ? Number(match[1]) : 32;
    let size = startSize;
    do {
      ctx.font = font.replace(/\d+px/, `${size}px`);
      if (ctx.measureText(text).width <= maxWidth || size <= minSize) break;
      size -= 2;
    } while (size > minSize);
    ctx.textAlign = align;
    ctx.fillText(text, x, y);
  };
  const drawMetric = (ctx, x, y, w, h, label, value, delta, accent = '#56efff') => {
    const gradient = ctx.createLinearGradient(x, y, x, y + h);
    gradient.addColorStop(0, 'rgba(27, 42, 74, .94)');
    gradient.addColorStop(1, 'rgba(7, 15, 32, .94)');
    fillRound(ctx, x, y, w, h, 24, gradient, 'rgba(122, 225, 255, .18)');
    ctx.fillStyle = accent;
    ctx.font = '900 25px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(label, x + 24, y + 38);
    ctx.fillStyle = '#f7fbff';
    fitText(ctx, value, x + 24, y + 92, w - 48, '950 42px "Microsoft YaHei", sans-serif', 24);
    ctx.fillStyle = delta && delta.startsWith('减少') ? '#ffb7b7' : '#9df4c3';
    fitText(ctx, delta || '等待下一期', x + 24, y + 132, w - 48, '850 24px "Microsoft YaHei", sans-serif', 18);
  };
  const drawSmallStat = (ctx, x, y, w, label, value, accent) => {
    fillRound(ctx, x, y, w, 112, 20, 'rgba(255,255,255,.055)', 'rgba(122,225,255,.16)');
    ctx.fillStyle = accent;
    ctx.font = '900 24px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(label, x + 20, y + 34);
    ctx.fillStyle = '#f6fbff';
    fitText(ctx, value, x + 20, y + 78, w - 40, '950 34px "Microsoft YaHei", sans-serif', 20);
  };
  const drawCompactStat = (ctx, x, y, w, h, label, sub, value, delta, accent = '#56efff') => {
    fillRound(ctx, x, y, w, h, 18, 'rgba(13,28,55,.88)', 'rgba(122,225,255,.14)');
    ctx.fillStyle = '#aebdd7';
    ctx.font = '850 22px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(label, x + 20, y + 32);
    ctx.fillStyle = '#61728e';
    ctx.font = '800 15px "Microsoft YaHei", sans-serif';
    fitText(ctx, sub || '公开统计', x + 20, y + 57, w * 0.46, '800 15px "Microsoft YaHei", sans-serif', 12);
    ctx.fillStyle = '#f6f8ff';
    fitText(ctx, value, x + w - 18, y + 36, w * 0.48, '950 32px "Microsoft YaHei", sans-serif', 18, 'right');
    const deltaText = delta || '等待下一期';
    ctx.fillStyle = deltaText.startsWith('减少') ? '#ff687c' : (deltaText.includes('持平') ? '#7788a5' : accent);
    fitText(ctx, deltaText, x + w - 18, y + 69, w * 0.5, '900 16px "Microsoft YaHei", sans-serif', 12, 'right');
  };
  const drawPeriodStat = (ctx, x, y, w, h, label, value, accent) => {
    const gradient = ctx.createLinearGradient(x, y, x + w, y + h);
    gradient.addColorStop(0, 'rgba(82,242,255,.12)');
    gradient.addColorStop(1, 'rgba(255,232,106,.07)');
    fillRound(ctx, x, y, w, h, 16, gradient, 'rgba(255,255,255,.12)');
    ctx.fillStyle = '#8fa6c7';
    ctx.font = '850 18px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(label, x + 18, y + 30);
    ctx.fillStyle = accent;
    fitText(ctx, value, x + 18, y + 70, w - 36, '950 29px "Microsoft YaHei", sans-serif', 18);
  };
  const drawSparkline = (ctx, x, y, w, h, values) => {
    const clean = Array.isArray(values) ? values.map(asNumber).filter(Number.isFinite).slice(-10) : [];
    const points = clean.length >= 2 ? clean : [0, 0, 1, 1];
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = Math.max(max - min, Math.max(Math.abs(max), 1) * 0.08);
    ctx.beginPath();
    points.forEach((value, index) => {
      const px = x + (w * index) / Math.max(1, points.length - 1);
      const py = y + h - ((value - min) / span) * h;
      if (index === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.strokeStyle = '#58f2ff';
    ctx.lineWidth = 5;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();
    ctx.strokeStyle = '#aaff81';
    ctx.lineWidth = 2;
    ctx.stroke();
  };

  const QR_ROWS = [
    '11111110000001001011001111111',
    '10000010100010111101001000001',
    '10111010010111101001101011101',
    '10111010001101110011101011101',
    '10111010101100100111001011101',
    '10000010011001000011001000001',
    '11111110101010101010101111111',
    '00000000000111101100100000000',
    '10101010011000110101100010010',
    '11100000110110110000111001001',
    '10110110100001000110010000111',
    '11101100001000010111000010010',
    '01001011110110001111111001011',
    '11000001111001011000111101001',
    '01110111110110111100001101011',
    '11001000000111101101000001010',
    '01101011010110110100111101011',
    '00100101110010110100101001101',
    '10000011110001000110101100011',
    '01111001001010010111011001010',
    '10011011001010001101111110000',
    '00000000110111011001100010111',
    '11111110010100111101101011011',
    '10000010001101101110100011011',
    '10111010100100111100111110010',
    '10111010001001110000100010100',
    '10111010101000100000110111001',
    '10000010011001001111110010010',
    '11111110111100011111110111011',
  ];
  const drawQr = (ctx, x, y, sizePx) => {
    const matrix = QR_ROWS.map((row) => row.split('').map((bit) => bit === '1'));
    const moduleCount = matrix.length;
    const quiet = 4;
    const step = sizePx / (moduleCount + quiet * 2);
    ctx.fillStyle = '#ffffff';
    fillRound(ctx, x, y, sizePx, sizePx, 18, '#ffffff');
    ctx.fillStyle = '#071124';
    matrix.forEach((row, r) => {
      row.forEach((dark, c) => {
        if (!dark) return;
        ctx.fillRect(x + (c + quiet) * step, y + (r + quiet) * step, Math.ceil(step), Math.ceil(step));
      });
    });
  };

  const collectPosterData = () => {
    const payload = rankPayload();
    const meta = payload.meta || {};
    const totalPower = meta.network_total_power;
    const highestPrice = formatHighestPrice(meta);
    const oracleTriggerPrice = formatOracleTriggerPrice(meta);
    const latestBlock = asNumber(meta.latest_block) || asNumber(meta.rpc_latest_block) || asNumber(meta.rpc_log_latest_block) || asNumber(meta.rpc_log_start_block) || asNumber(meta.rpc_log_end_block) || 0;
    const cards = [
      ['区块高度', '最新扫描区块', `${latestBlock.toLocaleString('zh-CN')}`, metricDelta('latest_block', latestBlock, compactNumber), '#63ee91'],
      ['当前价格', '区块浏览器公开报价', formatPrice(meta), metricDelta('network_current_price', meta.network_current_price, compactNumber), '#ffe86a'],
      ['最高价格', '官网最高价格', highestPrice, '触发价计算基准', '#ffe86a'],
      ['预言机触发价', '触发预言机价格', oracleTriggerPrice, '最高价的 50%', '#63ee91'],
      ['总地址数', '公开地址规模', formatCount(meta.explorer_total_addresses), metricDelta('total_wallets', meta.explorer_total_addresses, formatCount), '#56efff'],
      ['正算力地址', '进入排行榜统计', formatCount(meta.positive_power_count), metricDelta('positive_power_addresses', meta.positive_power_count, formatCount), '#7e8cff'],
      ['流通总量', '公开流通口径', String(meta.network_total_circulation_display || formatToken(meta.network_total_circulation_tokens)), metricDelta('network_total_circulation', meta.network_total_circulation_tokens, (value) => formatToken(value)), '#9df4c3'],
      ['全网总销毁', 'POWER 合约累计', String(meta.network_total_burned_display || formatToken(meta.network_total_burned_tokens)), metricDelta('total_burned', meta.network_total_burned_tokens, (value) => formatToken(value)), '#ff9fb7'],
      ['统计日活跃地址', '00:00 至次日 00:00', formatCount(meta.statistics_window_active_wallet_address_count), metricDelta('daily_active_addresses', meta.statistics_window_active_wallet_address_count, formatCount), '#56efff'],
      ['统计日新增算力', '同一统计日口径', compactNumber(meta.statistics_window_new_power), metricDelta('daily_new_power', meta.statistics_window_new_power, compactNumber), '#7e8cff'],
      ['1亿算力日产出', '矿工 75% 产量估算', String(document.querySelector('[data-label="1亿算力产出"] b')?.textContent || '待刷新'), metricDelta('one_yi_power_output', null, (value) => `${compactNumber(value)}枚/日`), '#ffd37e'],
      ['产1币需算力', '每日产币模型估算', String(meta.power_required_per_mars_daily_display || compactNumber(meta.power_required_per_mars_daily)), metricDelta('power_per_coin', meta.power_required_per_mars_daily, compactNumber), '#9df4c3'],
    ];
    return {
      meta,
      generatedAt: generatedLabel(meta),
      reportDate: reportDateLabel(meta),
      daysOnline: daysOnline(meta),
      countdown: christmasCountdown(),
      coverage: formatPercent(meta.discovered_power_coverage),
      totalPower: compactNumber(totalPower),
      totalPowerDelta: metricDelta('network_total_power', totalPower, compactNumber),
      totalPowerTrend: metricSeries('network_total_power'),
      cards,
      period7: [
        ['7天新增地址', formatCount(meta.period_7d_new_candidate_address_count)],
        ['7天新增算力', compactNumber(meta.period_7d_new_power)],
        ['7天销毁', String(meta.period_7d_burned_display || formatToken(meta.period_7d_burned_tokens))],
      ],
      period30: [
        ['30天新增地址', formatCount(meta.period_30d_new_candidate_address_count)],
        ['30天新增算力', compactNumber(meta.period_30d_new_power)],
        ['30天销毁', String(meta.period_30d_burned_display || formatToken(meta.period_30d_burned_tokens))],
      ],
    };
  };

  const renderPoster = () => {
    const data = collectPosterData();
    const ctx = canvas.getContext('2d');
    canvas.width = POSTER_WIDTH;
    canvas.height = POSTER_HEIGHT;
    ctx.clearRect(0, 0, POSTER_WIDTH, POSTER_HEIGHT);

    const bg = ctx.createLinearGradient(0, 0, 0, POSTER_HEIGHT);
    bg.addColorStop(0, '#061022');
    bg.addColorStop(.54, '#071225');
    bg.addColorStop(1, '#030714');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, POSTER_WIDTH, POSTER_HEIGHT);

    ctx.strokeStyle = 'rgba(92,218,255,.055)';
    ctx.lineWidth = 1;
    for (let x = -120; x < POSTER_WIDTH + 160; x += 54) {
      ctx.beginPath();
      ctx.moveTo(x + 160, 0);
      ctx.lineTo(x - 120, POSTER_HEIGHT);
      ctx.stroke();
    }
    for (let y = 40; y < POSTER_HEIGHT; y += 54) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(POSTER_WIDTH, y);
      ctx.stroke();
    }
    const glow = ctx.createRadialGradient(690, 350, 12, 690, 350, 260);
    glow.addColorStop(0, 'rgba(82,242,255,.18)');
    glow.addColorStop(1, 'rgba(82,242,255,0)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, POSTER_WIDTH, POSTER_HEIGHT);

    ctx.fillStyle = 'rgba(255,255,255,.035)';
    ctx.font = '950 90px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('M', POSTER_WIDTH / 2, 98);

    ctx.textAlign = 'left';
    ctx.fillStyle = '#a7b8d8';
    ctx.font = '850 25px "Microsoft YaHei", sans-serif';
    ctx.fillText(data.daysOnline, 54, 71);

    const chips = [[data.countdown, '#ffe86a'], [`覆盖率 ${data.coverage}`, '#71f4ff']];
    let chipX = 506;
    chips.forEach(([label, color], index) => {
      ctx.font = '900 23px "Microsoft YaHei", sans-serif';
      const width = ctx.measureText(label).width + 38;
      fillRound(ctx, chipX, 38, width, 50, 25, index === 0 ? 'rgba(255,232,106,.10)' : 'rgba(11,31,57,.72)', index === 0 ? 'rgba(255,232,106,.34)' : 'rgba(92,242,255,.35)');
      ctx.fillStyle = color;
      ctx.fillText(label, chipX + 19, 71);
      chipX += width + 12;
    });

    ctx.fillStyle = '#ffe86a';
    ctx.font = '950 52px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('MarsChain 今日算力战报', POSTER_WIDTH / 2, 132);
    ctx.fillStyle = '#f5fbff';
    ctx.font = '950 52px "Microsoft YaHei", sans-serif';
    ctx.fillText('全网排行榜实时公开', POSTER_WIDTH / 2, 192);
    ctx.fillStyle = '#9fb0ce';
    ctx.font = '800 25px "Microsoft YaHei", sans-serif';
    ctx.fillText(`${data.reportDate} · 00:00 至次日 00:00 · 北京时间`, POSTER_WIDTH / 2, 234);
    ctx.textAlign = 'left';

    const rule = ctx.createLinearGradient(58, 266, 966, 266);
    rule.addColorStop(0, 'rgba(82,220,255,0)');
    rule.addColorStop(.35, 'rgba(82,220,255,.68)');
    rule.addColorStop(.5, 'rgba(255,232,106,.78)');
    rule.addColorStop(.65, 'rgba(82,220,255,.68)');
    rule.addColorStop(1, 'rgba(82,220,255,0)');
    ctx.fillStyle = rule;
    ctx.fillRect(58, 266, 908, 3);

    const heroY = 300;
    const heroH = 230;
    const bigW = 620;
    const mainGradient = ctx.createLinearGradient(54, heroY, 54 + bigW, heroY + heroH);
    mainGradient.addColorStop(0, 'rgba(15,32,62,.97)');
    mainGradient.addColorStop(1, 'rgba(7,13,29,.95)');
    fillRound(ctx, 54, heroY, bigW, heroH, 24, mainGradient, 'rgba(94,211,255,.20)');
    const heroGlow = ctx.createRadialGradient(600, heroY + 34, 10, 600, heroY + 34, 160);
    heroGlow.addColorStop(0, 'rgba(82,242,255,.20)');
    heroGlow.addColorStop(1, 'rgba(82,242,255,0)');
    ctx.fillStyle = heroGlow;
    ctx.fillRect(54, heroY, bigW, heroH);
    ctx.fillStyle = '#7cecff';
    ctx.font = '950 22px "Microsoft YaHei", sans-serif';
    ctx.fillText('NETWORK POWER', 84, heroY + 42);
    ctx.fillStyle = '#c2d0e9';
    ctx.font = '900 29px "Microsoft YaHei", sans-serif';
    ctx.fillText('全网总算力', 84, heroY + 88);
    ctx.fillStyle = '#ffffff';
    fitText(ctx, data.totalPower, 84, heroY + 162, 520, '950 74px "Microsoft YaHei", sans-serif', 46);
    ctx.fillStyle = data.totalPowerDelta.startsWith('减少') ? '#ff687c' : '#80f2a1';
    fitText(ctx, `较上一期 ${data.totalPowerDelta}`, 84, heroY + 202, 390, '900 24px "Microsoft YaHei", sans-serif', 18);
    drawSparkline(ctx, 370, heroY + 142, 250, 58, data.totalPowerTrend);

    fillRound(ctx, 704, heroY, 266, heroH, 24, 'rgba(255,232,106,.08)', 'rgba(255,232,106,.25)');
    drawQr(ctx, 748, heroY + 24, 178);
    ctx.fillStyle = '#ffe86a';
    ctx.font = '950 24px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('扫码看实时榜', 837, heroY + 216);
    ctx.textAlign = 'left';

    const cardW = 450;
    const cardH = 86;
    const startX = 54;
    const gapX = 16;
    const gapY = 12;
    data.cards.forEach((card, index) => {
      const row = Math.floor(index / 2);
      const col = index % 2;
      const y = 552 + row * (cardH + gapY);
      const x = startX + col * (cardW + gapX);
      drawCompactStat(ctx, x, y, cardW, cardH, card[0], card[1], card[2], card[3], card[4]);
    });

    const periodY = 1160;
    const periodW = 298;
    const periodH = 78;
    data.period7.forEach((item, index) => drawPeriodStat(ctx, 54 + index * (periodW + 10), periodY, periodW, periodH, item[0], item[1], index === 1 ? '#80f2a1' : '#f6fbff'));
    data.period30.forEach((item, index) => drawPeriodStat(ctx, 54 + index * (periodW + 10), periodY + 90, periodW, periodH, item[0], item[1], index === 1 ? '#80f2a1' : '#f6fbff'));

    ctx.fillStyle = '#7f90ad';
    ctx.font = '800 18px "Microsoft YaHei", sans-serif';
    ctx.fillText('数据来源：MarsChain Rank / explorer.marschain.net', 54, 1390);
    ctx.fillText(`最近刷新：${data.generatedAt}`, 54, 1422);
    ctx.fillStyle = '#dce9ff';
    ctx.font = '950 25px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(SITE_URL.replace(/^https:\/\//, ''), 860, 1422);
    ctx.textAlign = 'left';
    const logo = ctx.createConicGradient(3.8, 894, 1378);
    logo.addColorStop(0, '#7bffcb');
    logo.addColorStop(.26, '#52f2ff');
    logo.addColorStop(.55, '#f58aff');
    logo.addColorStop(.78, '#ffe86a');
    logo.addColorStop(1, '#7bffcb');
    fillRound(ctx, 892, 1344, 78, 78, 22, logo);
  };

  const setStatus = (text, mode = '') => {
    const status = modal && modal.querySelector('[data-poster-status]');
    if (!status) return;
    status.textContent = text;
    status.classList.toggle('is-error', mode === 'error');
    status.classList.toggle('is-ok', mode === 'ok');
  };
  const canvasBlob = () => new Promise((resolve) => canvas.toBlob(resolve, 'image/png', 0.96));
  const downloadPoster = () => {
    const link = document.createElement('a');
    link.download = `marschain-rank-${new Date().toISOString().slice(0, 10)}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
    setStatus('图片已生成，可以直接转发。', 'ok');
  };
  const sharePoster = async () => {
    try {
      const blob = await canvasBlob();
      if (!blob) throw new Error('图片生成失败');
      const file = new File([blob], 'marschain-rank-report.png', { type: 'image/png' });
      if (navigator.canShare && navigator.canShare({ files: [file] }) && navigator.share) {
        await navigator.share({ files: [file], title: 'MarsChain 算力战报', text: 'MarsChain 算力排行榜战报' });
        setStatus('已打开系统分享面板。', 'ok');
      } else {
        downloadPoster();
        setStatus('当前浏览器不支持直接分享文件，已改为下载图片。', 'ok');
      }
    } catch (error) {
      setStatus(error.message || '分享失败，请下载图片后转发。', 'error');
    }
  };

  const ensureModal = () => {
    if (modal) return modal;
    modal = document.createElement('div');
    modal.className = 'poster-modal';
    modal.hidden = true;
    modal.innerHTML = `
      <section class="poster-panel" role="dialog" aria-modal="true" aria-labelledby="posterTitle">
        <div class="poster-preview"><canvas data-poster-canvas aria-label="MarsChain 战报预览"></canvas></div>
        <div class="poster-copy">
          <div class="poster-head">
            <div><span>SHARE POSTER</span><h3 id="posterTitle">生成战报图片</h3></div>
            <button class="poster-close" type="button" data-poster-close aria-label="关闭">×</button>
          </div>
          <p>图片会读取当前页面数据，在本地生成 1024×1536 战报图，适合朋友圈、社群和私聊转发。</p>
          <ul class="poster-meta">
            <li><span>二维码</span><b>官网榜单</b></li>
            <li><span>价格</span><b>读取实时小文件</b></li>
            <li><span>周期数据</span><b>7天 / 30天两行</b></li>
          </ul>
          <div class="poster-actions">
            <button type="button" data-poster-download>下载图片</button>
            <button type="button" data-poster-share>分享图片</button>
            <button type="button" data-poster-refresh>重新生成</button>
          </div>
          <div class="poster-status" data-poster-status>点击下载或分享即可转发。</div>
        </div>
      </section>
    `;
    document.body.appendChild(modal);
    canvas = modal.querySelector('[data-poster-canvas]');
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.closest('[data-poster-close]')) {
        modal.hidden = true;
        document.body.style.overflow = '';
      }
      if (event.target.closest('[data-poster-download]')) downloadPoster();
      if (event.target.closest('[data-poster-share]')) sharePoster();
      if (event.target.closest('[data-poster-refresh]')) {
        renderPoster();
        setStatus('已按当前页面数据重新生成。', 'ok');
      }
    });
    document.addEventListener('keydown', (event) => {
      if (!modal.hidden && event.key === 'Escape') {
        modal.hidden = true;
        document.body.style.overflow = '';
      }
    });
    return modal;
  };
  const openPoster = () => {
    ensureModal();
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    renderPoster();
    setStatus('战报已生成。', 'ok');
  };
  buttons.forEach((button) => button.addEventListener('click', openPoster));
})();
"""


METRIC_TREND_JS = r"""
(() => {
  const dataNode = document.getElementById('metricTrendData');
  if (!dataNode) return;
  let metricTrends = [];
  try {
    metricTrends = JSON.parse(dataNode.textContent || '[]');
  } catch (error) {
    metricTrends = [];
  }
  if (!Array.isArray(metricTrends) || !metricTrends.length) return;

  const periods = [
    ['7', '一周', 7],
    ['30', '一个月', 30],
    ['90', '一季度', 90],
    ['all', '全部', Infinity],
  ];
  let activeMetric = null;
  let activePeriod = '30';
  let activePoints = [];
  let focusIndex = 0;
  let modal = null;

  const TOKEN_WEI_METRICS = new Set([
    'network_total_circulation',
    'total_burned',
    'daily_burned',
    'period_7d_burned',
    'period_30d_burned',
  ]);
  const trimZeros = (value) => value.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  const displayNumber = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return NaN;
    return activeMetric && TOKEN_WEI_METRICS.has(activeMetric.key) ? number / 1e18 : number;
  };
  const formatTrendValue = (value) => {
    const number = displayNumber(value);
    if (!Number.isFinite(number)) return '待刷新';
    const abs = Math.abs(number);
    if (abs >= 1_0000_0000_0000) return `${trimZeros((number / 1_0000_0000_0000).toFixed(3))}万亿`;
    if (abs >= 1_0000_0000) return `${trimZeros((number / 1_0000_0000).toFixed(3))}亿`;
    if (abs >= 1_0000) return `${trimZeros((number / 1_0000).toFixed(3))}万`;
    if (abs > 0 && abs < 1) return trimZeros(number.toFixed(6));
    if (Number.isInteger(number)) return number.toLocaleString('zh-CN');
    return trimZeros(number.toLocaleString('zh-CN', { maximumFractionDigits: 3 }));
  };
  const formatDateLabel = (value) => {
    const text = String(value || '');
    const match = text.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (match) return `${match[2]}-${match[3]}`;
    return text.replace(/^采样\s*/, '#');
  };
  const cleanPoints = (points) => Array.isArray(points)
    ? points.map((point, index) => ({
        label: String(point && point.label ? point.label : `采样 ${index + 1}`),
        value: Number(point && point.value),
      })).filter((point) => Number.isFinite(point.value))
    : [];
  const periodPoints = () => {
    const all = cleanPoints(activeMetric ? activeMetric.points : []);
    const period = periods.find((item) => item[0] === activePeriod) || periods[1];
    return Number.isFinite(period[2]) ? all.slice(-period[2]) : all;
  };
  const escapeHtml = (value) => String(value || '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char]));
  const chartLabelIndexes = (count) => {
    if (count <= 0) return new Set();
    if (count <= 16) return new Set(Array.from({ length: count }, (_, index) => index));
    const maxLabels = 6;
    const indexes = new Set([0, count - 1]);
    for (let i = 1; i < maxLabels - 1; i += 1) {
      indexes.add(Math.round((count - 1) * (i / (maxLabels - 1))));
    }
    return indexes;
  };

  const ensureModal = () => {
    if (modal) return modal;
    modal = document.createElement('div');
    modal.className = 'trend-modal';
    modal.hidden = true;
    modal.dataset.trendModal = 'true';
    modal.innerHTML = `
      <section class="trend-panel" role="dialog" aria-modal="true" aria-labelledby="trendTitle">
        <div class="trend-head">
          <div class="trend-title">
            <span>数据趋势</span>
            <h3 id="trendTitle" data-trend-title></h3>
            <p data-trend-note></p>
          </div>
          <button class="trend-close" type="button" data-trend-close aria-label="关闭">×</button>
        </div>
        <div class="trend-controls" data-trend-periods></div>
        <div class="trend-chart" data-trend-chart></div>
        <div class="trend-stats">
          <div class="trend-stat"><span>光标日期</span><b data-trend-focus-value>待刷新</b><small data-trend-focus-label></small></div>
          <div class="trend-stat"><span>最新值</span><b data-trend-latest>待刷新</b><small data-trend-latest-label></small></div>
          <div class="trend-stat"><span>最低</span><b data-trend-min>待刷新</b><small data-trend-min-label></small></div>
          <div class="trend-stat"><span>最高</span><b data-trend-max>待刷新</b><small data-trend-max-label></small></div>
        </div>
        <p class="trend-foot" data-trend-foot>拖动图表查看具体日期。</p>
      </section>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.closest('[data-trend-close]')) closeModal();
    });
    document.addEventListener('keydown', (event) => {
      if (!modal.hidden && event.key === 'Escape') closeModal();
    });
    return modal;
  };

  const closeModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = '';
  };

  const setFocus = (index) => {
    if (!activePoints.length || !modal) return;
    focusIndex = Math.max(0, Math.min(activePoints.length - 1, index));
    const point = activePoints[focusIndex];
    const values = activePoints.map((item) => item.value);
    const minPoint = activePoints.reduce((best, item) => item.value < best.value ? item : best, activePoints[0]);
    const maxPoint = activePoints.reduce((best, item) => item.value > best.value ? item : best, activePoints[0]);
    const latest = activePoints[activePoints.length - 1];
    modal.querySelector('[data-trend-focus-value]').textContent = formatTrendValue(point.value);
    modal.querySelector('[data-trend-focus-label]').textContent = point.label;
    modal.querySelector('[data-trend-latest]').textContent = formatTrendValue(latest.value);
    modal.querySelector('[data-trend-latest-label]').textContent = latest.label;
    modal.querySelector('[data-trend-min]').textContent = formatTrendValue(Math.min(...values));
    modal.querySelector('[data-trend-min-label]').textContent = minPoint.label;
    modal.querySelector('[data-trend-max]').textContent = formatTrendValue(Math.max(...values));
    modal.querySelector('[data-trend-max-label]').textContent = maxPoint.label;
    modal.querySelectorAll('[data-cursor-index]').forEach((node) => {
      node.style.opacity = Number(node.dataset.cursorIndex) === focusIndex ? '1' : '0';
    });
  };

  const renderChart = () => {
    if (!modal || !activeMetric) return;
    activePoints = periodPoints();
    focusIndex = Math.max(0, activePoints.length - 1);
    const chart = modal.querySelector('[data-trend-chart]');
    const controls = modal.querySelector('[data-trend-periods]');
    controls.innerHTML = periods.map(([key, label]) => (
      `<button type="button" data-trend-period="${key}" class="${key === activePeriod ? 'is-active' : ''}">${label}</button>`
    )).join('');
    controls.querySelectorAll('[data-trend-period]').forEach((button) => {
      button.addEventListener('click', () => {
        activePeriod = button.dataset.trendPeriod || '30';
        renderChart();
        if (window.applyMarsLanguage) window.applyMarsLanguage();
      });
    });

    if (!activePoints.length) {
      chart.innerHTML = '<div class="trend-empty">趋势采样中，等待下次刷新。</div>';
      modal.querySelector('[data-trend-foot]').textContent = '趋势采样中，等待下次刷新。';
      setFocus(0);
      return;
    }

    const width = 720;
    const height = 300;
    const pad = { left: 38, right: 20, top: 44, bottom: 50 };
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const values = activePoints.map((point) => point.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(max - min, Math.max(Math.abs(max), 1) * 0.08);
    const xy = (point, index) => {
      const x = pad.left + plotWidth * (index / Math.max(1, activePoints.length - 1));
      const y = pad.top + plotHeight - ((point.value - min) / span) * plotHeight;
      return [x, y];
    };
    const coords = activePoints.map(xy);
    const linePath = coords.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
    const areaPath = `${linePath} L ${coords[coords.length - 1][0].toFixed(2)},${(height - pad.bottom).toFixed(2)} L ${coords[0][0].toFixed(2)},${(height - pad.bottom).toFixed(2)} Z`;
    const barWidth = Math.max(2, Math.min(20, plotWidth / Math.max(1, activePoints.length) * 0.58));
    const bars = coords.map(([x, y]) => {
      const base = height - pad.bottom;
      return `<rect class="bar" x="${(x - barWidth / 2).toFixed(2)}" y="${y.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${Math.max(1, base - y).toFixed(2)}" rx="2"></rect>`;
    }).join('');
    const labelIndexes = chartLabelIndexes(activePoints.length);
    const topLabels = coords.map(([x, y], index) => {
      if (!labelIndexes.has(index)) return '';
      const anchor = index === 0 ? 'start' : index === activePoints.length - 1 ? 'end' : 'middle';
      return `<text class="value-label" x="${x.toFixed(2)}" y="${Math.max(17, y - 10).toFixed(2)}" text-anchor="${anchor}">${escapeHtml(formatTrendValue(activePoints[index].value))}</text>`;
    }).join('');
    const dateLabels = coords.map(([x], index) => {
      if (!labelIndexes.has(index)) return '';
      const anchor = index === 0 ? 'start' : index === activePoints.length - 1 ? 'end' : 'middle';
      return `<text class="date-label" x="${x.toFixed(2)}" y="${(height - 14).toFixed(2)}" text-anchor="${anchor}">${escapeHtml(formatDateLabel(activePoints[index].label))}</text>`;
    }).join('');
    const cursors = coords.map(([x, y], index) => (
      `<g data-cursor-index="${index}" style="opacity:${index === focusIndex ? 1 : 0}">
        <line class="cursor-line" x1="${x.toFixed(2)}" y1="${pad.top}" x2="${x.toFixed(2)}" y2="${height - pad.bottom}"></line>
        <circle class="cursor-dot" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="5"></circle>
      </g>`
    )).join('');
    chart.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(activeMetric.label)}趋势图">
        <line class="axis-line" x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}"></line>
        ${bars}
        <path class="area" d="${areaPath}"></path>
        <path class="line" d="${linePath}"></path>
        ${topLabels}
        ${dateLabels}
        ${cursors}
      </svg>
    `;
    const pickIndex = (event) => {
      const rect = chart.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
      setFocus(Math.round(pct * (activePoints.length - 1)));
    };
    chart.onpointermove = pickIndex;
    chart.onpointerdown = (event) => {
      chart.setPointerCapture?.(event.pointerId);
      pickIndex(event);
    };
    const foot = activePoints.length < 7
      ? `采样中：当前只有 ${activePoints.length} 个采样点，等待更多刷新形成完整趋势。`
      : `每根柱子对应一个自然日。当前周期共 ${activePoints.length} 天。`;
    modal.querySelector('[data-trend-foot]').textContent = foot;
    setFocus(focusIndex);
  };

  const openMetric = (index) => {
    activeMetric = metricTrends[index];
    if (!activeMetric) return;
    const node = ensureModal();
    node.querySelector('[data-trend-title]').textContent = activeMetric.label || '数据趋势';
    node.querySelector('[data-trend-note]').textContent = activeMetric.note || '';
    activePeriod = '30';
    renderChart();
    node.hidden = false;
    document.body.style.overflow = 'hidden';
    node.querySelector('[data-trend-close]')?.focus();
    if (window.applyMarsLanguage) window.applyMarsLanguage();
  };

  document.querySelectorAll('[data-trend-index]').forEach((card) => {
    card.addEventListener('click', () => openMetric(Number(card.dataset.trendIndex || 0)));
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openMetric(Number(card.dataset.trendIndex || 0));
      }
    });
  });
})();
"""


LANGUAGE_TOGGLE_JS = r"""
(function() {
  const STORAGE_KEY = 'marschain_lang';
  const zhToEn = {
    'MarsChain 算力排行榜': 'MarsChain Power Ranking',
    '追踪链上算力分布、头部地址变化与北京时间统计日内新增趋势。': 'Track on-chain power distribution, top address changes, and Beijing-day growth trends.',
    '算力排行': 'Power Rank',
    '核心数据': 'Core Data',
    '周期增长': 'Growth',
    '方程系数': 'Equation Factor',
    '数据说明': 'Data Notes',
    '排行': 'Rank',
    '核心': 'Core',
    '方程': 'Equation',
    '说明': 'Notes',
    '电脑版': 'Desktop',
    '数据已加载 · 北京时间 00:00 每日采集': 'Data loaded · collected daily at 00:00 Beijing time',
    '算力指挥舱': 'Power Command Center',
    '算力榜': 'Power Rank',
    '基于公开区块浏览器、RPC 与 POWER 合约日志，展示全网算力、钱包地址、北京时间统计日新增和头部地址排行。': 'Based on public explorer data, RPC, and POWER contract logs, showing total network power, wallet addresses, Beijing-day growth, and top address rankings.',
    '下方先看前 100 名算力地址，再查看覆盖率、活跃地址和新增数据。': 'Start with the top 100 power addresses, then review coverage, active wallets, and new growth data.',
    '下方优先展示前 100 名算力地址，默认先看前 10。': 'Top 100 power addresses are shown below, with the first 10 visible by default.',
    '继续查看核心数据、增长趋势与数据说明': 'Continue to core data, growth trends, and data notes',
    '扫描覆盖率': 'Scan Coverage',
    '候选地址': 'Candidate Addresses',
    '正算力占比': 'Positive Power Share',
    '未覆盖算力': 'Uncovered Power',
    '01 / 算力排行': '01 / Power Rank',
    '头部算力地址排行': 'Top Power Address Ranking',
    '按当前查询到的算力降序展示前 100 名，每页 10 名，共 10 页。': 'Shows the top 100 addresses by current queried power, 10 per page across 10 pages.',
    '全球排行榜下载': 'Global Ranking Download',
    '前 100 名免费查看，全量文件需支付 1000 MARS；核销成功后下载链接 1 小时 内有效。': 'Top 100 are free to view. The full file costs 1000 MARS; after verification, the download link is valid for 1 hour.',
    '先生成付款订单，再用 MarsChain 钱包向收款地址转账 MARS。': 'Create a payment order first, then transfer MARS from a MarsChain wallet to the payment address.',
    '1000 MARS 需单笔一次性支付，拆分多笔无法自动核销。': '1000 MARS must be paid in one transaction; split payments cannot be verified automatically.',
    '链上手续费由付款方承担，实际转账金额需不少于 1000 MARS。': 'On-chain fees are paid by the sender, and the received transfer amount must be at least 1000 MARS.',
    '转账确认后复制交易哈希，回到本页提交核销。': 'After the transfer is confirmed, copy the transaction hash and submit it here for verification.',
    '收款金额': 'Payment Amount',
    '收款地址': 'Payment Address',
    '复制': 'Copy',
    '生成付款订单': 'Create Order',
    '核销下载': 'Verify Download',
    '付费下载接口待接入': 'Paid download API is not connected',
    '生成订单后提交交易哈希': 'Create an order, then submit the transaction hash',
    '打开下载链接': 'Open Download Link',
    '上一页': 'Previous',
    '下一页': 'Next',
    '02 / 核心数据': '02 / Core Data',
    '当前算力概览': 'Current Power Overview',
    '展示最近一次刷新得到的全网算力、产币模型、地址规模与统计日新增数据。': 'Shows the latest network power, emission model, address scale, and Beijing-day growth data.',
    '03 / 周期增长': '03 / Growth',
    '7 天与 30 天新增': '7-Day and 30-Day Growth',
    '按最近完整北京时间统计日汇总新增算力、新增地址和 TokensBurned 销毁事件。': 'Summarizes new power, new addresses, and TokensBurned events by complete Beijing-day windows.',
    '7 天新增': '7-Day Growth',
    '30 天新增': '30-Day Growth',
    '新增算力': 'New Power',
    '新增地址': 'New Addresses',
    '销毁数量': 'Burned Amount',
    '04 / 方程系数': '04 / Equation Factor',
    '方程膨胀系数': 'Equation Expansion Factor',
    '展示 MarsChain 双方程机制中的公开膨胀参数，帮助理解算力倍增、周期与销毁比例。': 'Shows public expansion parameters in the MarsChain dual-equation mechanism, including power multiplier, period, and burn ratio.',
    '当前起始系数': 'Starting Factor',
    '当前实际系数': 'Current Actual Factor',
    '算力倍增': 'Power Multiplier',
    '官方机制说明中的方程膨胀起始倍数。': 'Starting expansion multiplier in the official mechanism notes.',
    '当前 04 方程采用的实际膨胀倍数。': 'Actual expansion multiplier used by Equation 04.',
    '最高膨胀系数': 'Maximum Expansion Factor',
    '上限': 'Cap',
    '方程膨胀系数逐级放大时的公开说明上限。': 'Published cap for the stepped equation expansion factor.',
    '执行周期': 'Execution Period',
    '单轮': 'Per Round',
    '8 天': '8 Days',
    '圣诞方程与预言机方程按 8 天窗口执行。': 'The Christmas Equation and Oracle Equation run in an 8-day window.',
    '销毁比例': 'Burn Ratio',
    '机制说明中每轮方程触发的流通量销毁比例。': 'Circulation burn ratio described for each equation round.',
    '当前 04 方程实际系数为 20x，后续最高可按机制说明逐级放大至 160x；本区块属于机制口径展示，不等同于当前实时收益承诺。': 'The current actual factor for Equation 04 is 20x, with a mechanism cap that can step up to 160x. This is a mechanism note, not a real-time earnings promise.',
    '算力销毁测算': 'Power Burn Calculator',
    '按当前全网流通量 × 35% ÷ 全网总算力计算。': 'Calculated as current global circulation x 35% / total network power.',
    '按“全网流通量 × 35% ÷ 全网总算力”计算，得到每 1 算力对应的销毁数量；输入任意算力即可换算需要销毁多少 MARS。': 'Calculated as global circulation x 35% / total network power to get burn amount per unit of power. Enter any power amount to estimate required MARS burn.',
    '计算公式': 'Formula',
    '流通量 × 35% ÷ 总算力': 'Circulation x 35% / Total Power',
    '35%流通量': '35% Circulation',
    '1亿算力算例': '100M Power Example',
    '输入算力': 'Enter Power',
    '需要销毁': 'Required Burn',
    '按输入算力实时换算。': 'Recalculates from the entered power.',
    '按当前比例估算。': 'Estimated at the current ratio.',
    '请输入有效算力': 'Enter a valid power amount',
    '支持 1亿、5亿、1000万 或纯数字。': 'Supports 100M, 500M, 10M, or plain numbers.',
    '总钱包数量': 'Total Wallets',
    '地址总量': 'Total Addresses',
    '公开接口返回的地址规模，不代表所有地址都参与挖矿或拥有算力。': 'Address scale returned by public APIs; not every address mines or has power.',
    '日志发现': 'Log Discovery',
    '从 POWER 合约日志发现的相关地址，仍需要逐个查询当前算力。': 'Related addresses found from POWER contract logs; each still needs a current power query.',
    '算力 > 0': 'Power > 0',
    '候选地址中当前算力大于 0 的钱包地址。': 'Wallet addresses whose current power is greater than 0 among candidates.',
    '05 / 数据说明': '05 / Data Notes',
    '数据来源与准确性说明': 'Data Sources and Accuracy',
    '说明公开接口、RPC 节点和合约日志可能带来的延迟、遗漏与统计偏差。': 'Explains delays, omissions, and statistical bias from public APIs, RPC nodes, and contract logs.',
    '公开口径说明': 'Public Methodology Notes',
    '全网总算力': 'Global Network Power',
    '全网流通量': 'Global Circulation',
    '当前价格': 'Current Price',
    '总产量': 'Total Supply',
    '每日产币量': 'Daily Emission',
    '累计销毁': 'Total Burned',
    '正算力地址': 'Positive-Power Addresses',
    '统计日活跃地址数量': 'Daily Active Addresses',
    '统计日新增地址数量': 'Daily New Addresses',
    '统计日新增总算力': 'Daily New Power',
    '日销毁币量': 'Daily Burned',
    '单币日需算力': 'Daily Power per Coin',
    '1亿算力产出': 'Output per 100M Power',
    '统计日活跃地址': 'Daily Active Addresses',
    '统计日新增地址': 'Daily New Addresses',
    '统计日新增算力': 'Daily New Power',
    '7天新增算力': '7-Day New Power',
    '7天新增地址': '7-Day New Addresses',
    '7天销毁': '7-Day Burned',
    '30天新增算力': '30-Day New Power',
    '30天新增地址': '30-Day New Addresses',
    '30天销毁': '30-Day Burned',
    '7 天新增算力': '7-Day New Power',
    '7 天新增地址': '7-Day New Addresses',
    '7 天销毁': '7-Day Burned',
    '30 天新增算力': '30-Day New Power',
    '30 天新增地址': '30-Day New Addresses',
    '30 天销毁': '30-Day Burned',
    '矿工日产币量': 'Miner Daily Emission',
    '节点日产币量': 'Node Daily Emission',
    '合约日志命中': 'Contract Log Hits',
    '算力缓存刷新': 'Power Cache Refreshes',
    '覆盖目标线': 'Coverage Target',
    '覆盖率': 'Coverage',
    '流通量': 'Circulation',
    '最新区块': 'Latest Block',
    '算力日志': 'Power Logs',
    '缓存刷新': 'Cache Refreshes',
    '最近刷新': 'Last Refresh',
    '统计周期': 'Statistics Window',
    '采集频率': 'Collection Frequency',
    '每 24 小时一次': 'Every 24 Hours',
    '抓取时间': 'Collection Time',
    '每日 00:00（北京时间，夜里 24:00）': 'Daily 00:00 Beijing Time (midnight)',
    '区块浏览器公开统计': 'Public explorer statistics',
    '区块浏览器公开报价': 'Public explorer quote',
    '官网口径：永不增发': 'Official rule: no additional issuance',
    '官方经济模型口径': 'Official economic model',
    'POWER 合约累计燃烧': 'Cumulative POWER contract burns',
    '公开地址规模': 'Public address scale',
    '算力大于 0': 'Power greater than 0',
    '北京时间 00:00 至次日 00:00': 'Beijing time 00:00 to next-day 00:00',
    '北京 00:00 至次日 00:00': 'Beijing 00:00 to next-day 00:00',
    '同一统计日口径': 'Same statistics-day method',
    '北京时间统计日口径': 'Beijing-day method',
    '按矿工 75% 产量估算': 'Estimated with the 75% miner emission share',
    '按矿工 75% 日产币口径估算': 'Estimated with the 75% miner daily-emission method',
    '按矿工 75% 日产币口径估算：1亿算力 ÷ 单币日需算力。': 'Estimated as 100M power divided by daily power per coin, using the 75% miner-emission method.',
    '公开接口统计': 'Public API statistics',
    '同一统计窗口内活跃': 'Active within the same statistics window',
    '首次出现在合约日志': 'First appeared in contract logs',
    '最近 7 个完整统计日': 'Latest 7 complete statistics days',
    '最近 30 个完整统计日': 'Latest 30 complete statistics days',
    '首次进入 POWER 日志': 'First entered POWER logs',
    'TokensBurned 汇总': 'TokensBurned total',
    '公开接口返回的地址规模，不代表全部参与挖矿。': 'Public API address scale; not all addresses participate in mining.',
    '从 POWER 合约日志识别出的相关地址。': 'Related addresses identified from POWER contract logs.',
    '当前查询到算力大于 0 的钱包地址。': 'Wallet addresses currently queried with power above 0.',
    '01 / RANK': '01 / RANK',
    '头部排行': 'Top Ranking',
    '每页 10 名，共 10 页。': '10 entries per page, 10 pages total.',
    '02 / CORE': '02 / CORE',
    '核心数据': 'Core Data',
    '先看结果，再看口径。': 'Read the results first, then the methodology.',
    '03 / EQUATION': '03 / EQUATION',
    '机制参数展示。': 'Mechanism parameters.',
    '04 / NOTE': '04 / NOTE',
    '公开数据存在延迟。': 'Public data may lag.',
    '低': 'Low',
    '首个采样点': 'First Sample',
    '近 30 次趋势': 'Last 30 Samples',
    '等待下次刷新': 'Next Refresh Builds Trend',
    '榜单基于公开区块浏览器接口、RPC 与 POWER 合约日志生成，是 best effort 结果。公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，都可能造成与官方后台存在差异。': 'The ranking is a best-effort result generated from public explorer APIs, RPC, and POWER contract logs. Public API delays, missing RPC responses, contract-log methodology changes, or cache fallback may create differences from official back-office data.',
    '待刷新': 'Pending refresh',
    '收款地址已复制': 'Payment address copied',
    '正在生成付款订单...': 'Creating payment order...',
    '订单生成失败': 'Order creation failed',
    '请先生成付款订单': 'Create a payment order first',
    '交易哈希格式不正确': 'Invalid transaction hash format',
    '正在核销链上付款...': 'Verifying on-chain payment...',
    '核销成功，下载链接 1 小时内有效。': 'Verified. The download link is valid for 1 hour.',
    '核销成功，请重新打开下载链接。': 'Verified. Please reopen the download link.',
    '核销失败': 'Verification failed',
    '请求失败': 'Request failed',
    '数据趋势': 'Data Trend',
    '一周': 'Week',
    '一个月': 'Month',
    '一季度': 'Quarter',
    '全部': 'All',
    '光标日期': 'Cursor Date',
    '最新值': 'Latest',
    '最低': 'Low',
    '最高': 'High',
    '拖动图表查看具体日期。': 'Drag across the chart to inspect each date.',
    '趋势采样中，等待下次刷新。': 'Trend sampling, waiting for the next refresh.'
  };

  const attrToEn = {
    placeholder: {
      '输入交易哈希': 'Enter transaction hash'
    },
    'aria-label': {
      '下载格式': 'Download format',
      '关闭': 'Close'
    }
  };

  const textOriginals = new WeakMap();

  const trimZeros = (value) => value.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');

  const formatCompactNumber = (value) => {
    const abs = Math.abs(value);
    if (abs >= 1_000_000_000_000) return `${trimZeros((value / 1_000_000_000_000).toFixed(3))}T`;
    if (abs >= 1_000_000_000) return `${trimZeros((value / 1_000_000_000).toFixed(3))}B`;
    if (abs >= 1_000_000) return `${trimZeros((value / 1_000_000).toFixed(3))}M`;
    if (abs >= 1_000) return `${trimZeros((value / 1_000).toFixed(3))}K`;
    if (Number.isInteger(value)) return value.toLocaleString('en-US');
    return trimZeros(value.toLocaleString('en-US', { maximumFractionDigits: 6 }));
  };

  const translateChineseNumericUnit = (text) => {
    let match = text.match(/^(-?[\d,]+(?:\.\d+)?)(万亿|亿|万)(个|枚\/日|\/日)?$/);
    if (match) {
      const raw = Number(match[1].replace(/,/g, ''));
      if (!Number.isFinite(raw)) return '';
      const scale = match[2] === '万亿' ? 1_000_000_000_000 : match[2] === '亿' ? 100_000_000 : 10_000;
      const suffix = match[3] || '';
      if (suffix === '枚/日' || suffix === '/日') return `${formatCompactNumber(raw * scale)}/day`;
      return formatCompactNumber(raw * scale);
    }
    match = text.match(/^(-?[\d,]+(?:\.\d+)?)个$/);
    if (match) return Number(match[1].replace(/,/g, '')).toLocaleString('en-US');
    match = text.match(/^(-?[\d,]+(?:\.\d+)?)枚\/日$/);
    if (match) return `${match[1]} coins/day`;
    return '';
  };

  const translateDynamic = (text) => {
    const numericUnit = translateChineseNumericUnit(text);
    if (numericUnit) return numericUnit;
    let match = text.match(/^第\s*(\d+)\s*\/\s*(\d+)\s*页\s*·\s*当前显示\s*(\d+)-(\d+)\s*\/\s*共\s*(\d+)\s*名$/);
    if (match) return `Page ${match[1]} / ${match[2]} · Showing ${match[3]}-${match[4]} of ${match[5]}`;
    match = text.match(/^采样\s*(\d+)$/);
    if (match) return `Sample ${match[1]}`;
    match = text.match(/^采样中：当前只有\s*(\d+)\s*个采样点，等待更多刷新形成完整趋势。$/);
    if (match) return `Sampling: only ${match[1]} samples are available. More refreshes will build a complete trend.`;
    match = text.match(/^拖动图表查看具体日期。当前周期共\s*(\d+)\s*个采样点。$/);
    if (match) return `Drag across the chart to inspect each date. This period has ${match[1]} samples.`;
    match = text.match(/^每根柱子对应一个自然日。当前周期共\s*(\d+)\s*天。$/);
    if (match) return `Each bar represents one calendar day. This period has ${match[1]} days.`;
    match = text.match(/^覆盖率\s+(.+)$/);
    if (match) return `Coverage ${match[1]}`;
    match = text.match(/^订单已生成：支付\s*(.+?)\s*MARS 后提交交易哈希。$/);
    if (match) return `Order created. Pay ${match[1]} MARS, then submit the transaction hash.`;
    match = text.match(/^交易已找到，等待确认数\s*(.+)$/);
    if (match) return `Transaction found. Waiting for confirmations ${match[1]}`;
    match = text.match(/^本轮覆盖率为\s*(.+?)，低于目标\s*(.+?)。页面仍发布当轮最佳扫描结果，请结合风险说明理解数据边界。$/);
    if (match) return `This scan coverage is ${match[1]}, below the ${match[2]} target. The page still publishes the best available scan results; read the data notes for boundaries.`;
    match = text.match(/^榜单基于公开区块浏览器接口、RPC 与 POWER 合约日志生成。总产量采用官方经济模型口径：(.+?) 枚永不增发；每日产币量按官方公式与当前链龄计算；产量分配采用矿工 75%、节点 25%，所以单币日需算力按“全网总算力 ÷ 矿工日产币量”估算。公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，都可能造成与官方后台的差异。$/);
    if (match) return `The ranking is generated from public explorer APIs, RPC, and POWER contract logs. Total supply follows the official economic model: ${translateChineseNumericUnit(match[1]) || match[1]} coins with no additional issuance. Daily emission follows the official formula and current chain age. Distribution uses 75% for miners and 25% for nodes, so daily power per coin is estimated as global network power divided by miner daily emission. Public API delays, missing RPC responses, contract-log methodology changes, or cache fallback may create differences from official back-office data.`;
    match = text.match(/^基于公开 API、RPC 与合约日志生成的 best effort 榜单 · 最近刷新：(.+) · 统计周期：(.+)$/);
    if (match) return `Best-effort ranking generated from public APIs, RPC, and contract logs · Last refresh: ${match[1]} · Statistics window: ${match[2]}`;
    match = text.match(/^MarsChain Rank 手机版 · 最近刷新：(.+) · 统计周期：(.+)$/);
    if (match) return `MarsChain Rank Mobile · Last refresh: ${match[1]} · Statistics window: ${match[2]}`;
    return '';
  };

  const shouldSkipTextNode = (node) => {
    const parent = node.parentElement;
    if (!parent) return true;
    if (parent.closest('[data-lang-toggle]')) return true;
    return Boolean(parent.closest('script, style, noscript, code, pre, input, textarea, select'));
  };

  const translateTextNodes = (lang) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      if (shouldSkipTextNode(node)) return;
      if (!textOriginals.has(node)) textOriginals.set(node, node.nodeValue || '');
      const original = textOriginals.get(node) || '';
      const trimmed = original.replace(/\s+/g, ' ').trim();
      if (!trimmed) {
        node.nodeValue = original;
        return;
      }
      if (lang === 'zh') {
        node.nodeValue = original;
        return;
      }
      const replacement = translateDynamic(trimmed) || zhToEn[trimmed];
      if (!replacement) {
        node.nodeValue = original;
        return;
      }
      const leading = (original.match(/^\s*/) || [''])[0];
      const trailing = (original.match(/\s*$/) || [''])[0];
      node.nodeValue = `${leading}${replacement}${trailing}`;
    });
  };

  const translateAttributes = (lang) => {
    Object.entries(attrToEn).forEach(([attr, table]) => {
      document.querySelectorAll(`[${attr}]`).forEach((node) => {
        const originalAttr = `data-i18n-original-${attr}`;
        if (!node.hasAttribute(originalAttr)) {
          node.setAttribute(originalAttr, node.getAttribute(attr) || '');
        }
        const original = node.getAttribute(originalAttr) || '';
        node.setAttribute(attr, lang === 'en' ? (table[original] || original) : original);
      });
    });
  };

  const setToggleLabels = (lang) => {
    document.querySelectorAll('[data-lang-toggle]').forEach((button) => {
      button.textContent = lang === 'en' ? '中文' : 'EN';
      button.setAttribute('aria-label', lang === 'en' ? 'Switch to Chinese' : 'Switch to English');
    });
  };

  const applyLanguage = (lang) => {
    const nextLang = lang === 'en' ? 'en' : 'zh';
    document.documentElement.lang = nextLang === 'en' ? 'en' : 'zh-CN';
    document.title = nextLang === 'en' ? 'MarsChain Power Ranking' : 'MarsChain 算力排行榜';
    translateTextNodes(nextLang);
    translateAttributes(nextLang);
    setToggleLabels(nextLang);
  };

  window.applyMarsLanguage = () => applyLanguage(localStorage.getItem(STORAGE_KEY) === 'en' ? 'en' : 'zh');

  document.querySelectorAll('[data-lang-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const nextLang = localStorage.getItem(STORAGE_KEY) === 'en' ? 'zh' : 'en';
      localStorage.setItem(STORAGE_KEY, nextLang);
      window.applyMarsLanguage();
    });
  });

  window.applyMarsLanguage();
})();
"""


def _build_mobile_metric_cards(items: list[tuple]) -> str:
    cards: list[str] = []
    for index, item in enumerate(items):
        if len(item) >= 5:
            metric_key, label, value, note, trend_points = item[:5]
            extra = item[5] if len(item) > 5 and isinstance(item[5], dict) else {}
            trend_values = [point.get("value") for point in trend_points if isinstance(point, dict)]
        else:
            metric_key = ""
            label, value, note = item[:3]
            extra = {}
            trend_values = item[3] if len(item) > 3 else []
        live_price = str(metric_key) == "network_current_price"
        value_html = f'<b{" data-live-price" if live_price else ""}>{escape(value)}</b>'
        if live_price:
            highest = str(extra.get("highest_price") or "待刷新")
            trigger = str(extra.get("oracle_trigger_price") or "待刷新")
            value_html = (
                f'<b data-live-price>{escape(value)}</b>'
                '<div class="price-stack m-price-stack">'
                f'<span>最高价 <strong data-live-highest-price>{escape(highest)}</strong></span>'
                f'<span>预言机触发价 <strong data-live-oracle-trigger-price>{escape(trigger)}</strong></span>'
                '</div>'
            )
        cards.append(
            '<article class="m-card m-reveal" role="button" tabindex="0" %s'
            'data-trend-index="%d" data-track="metric_trend" data-label="%s" aria-label="查看%s趋势">'
            "<span>%s</span>%s<small%s>%s</small>%s</article>"
            % (
                'data-price-card ' if live_price else "",
                index,
                escape(str(label), quote=True),
                escape(str(label), quote=True),
                escape(label),
                value_html,
                ' data-live-price-note' if live_price else "",
                escape(note),
                _build_sparkline(_clean_trend_values(trend_values)),
            )
        )
    return "\n".join(cards)


def _build_mobile_flow_cards(items: list[tuple[str, str, str, str]]) -> str:
    return "\n".join(
        '<article class="m-flow-card m-reveal"><label>%s<span>%s</span></label><strong>%s</strong><small>%s</small></article>'
        % (escape(label), escape(tag), escape(value), escape(note))
        for label, tag, value, note in items
    )


def _build_mobile_rank_cards(rows: list[dict], limit: int = 100, page_size: int = 10) -> str:
    top_rows = rows[:limit]
    max_power = max([_as_float(row.get("power")) for row in top_rows] or [1.0]) or 1.0
    cards: list[str] = []
    for index, row in enumerate(top_rows):
        power = _as_float(row.get("power"))
        page_class = " is-page-hidden" if index >= page_size else ""
        width = max(4.0, min(100.0, (power / max_power) * 100))
        cards.append(
            '<article class="m-rank-card m-reveal%s">'
            '<div class="m-rank-top"><em>#%02d</em><strong>%s</strong></div>'
            "<code>%s</code>"
            '<span class="m-bar"><i style="width:%.3f%%"></i></span>'
            "</article>"
            % (page_class, _as_int(row.get("rank"), index + 1), escape(_fmt_power(power)), escape(str(row.get("address") or "")), width)
        )
    return "\n".join(cards)


def build_mobile_html(payload: dict) -> str:
    """Render the mobile-first MarsChain site at /m/."""
    payload = _normalize_statistics_payload(payload)
    meta = payload.get("meta", {})
    rows = payload.get("rows", [])
    title = "MarsChain 算力排行榜 · 手机版"
    subtitle = "手机端查看 MarsChain 全网算力、统计日新增地址和头部排行。"
    analytics_head = build_analytics_head()
    paid_download_config = load_paid_download_config()

    generated_at = _format_generated_at_from_meta(meta)
    statistics_window_label = str(meta.get("statistics_window_label") or "北京时间 00:00 至次日 00:00")
    coverage_label = _fmt_percent(meta.get("discovered_power_coverage"))
    network_total_power = meta.get("network_total_power")
    candidate_count = meta.get("candidate_count")
    positive_power_count = meta.get("positive_power_count")
    explorer_total_addresses = meta.get("explorer_total_addresses")
    active_wallet_count = meta.get("statistics_window_active_wallet_address_count")
    new_address_count = meta.get("statistics_window_new_candidate_address_count")
    if new_address_count is None:
        new_address_count = meta.get("today_new_wallet_count")
    new_power = meta.get("statistics_window_new_power")
    if new_power is None:
        new_power = meta.get("today_new_power")
    circulation = str(meta.get("network_total_circulation_display") or "待刷新")
    current_price = str(meta.get("network_current_price_display") or "待刷新")
    highest_price = str(meta.get("network_highest_price_display") or _fmt_price_value(meta.get("network_highest_price")))
    oracle_trigger_price = _oracle_trigger_price_display(meta)
    total_burned = str(meta.get("network_total_burned_display") or "待刷新")
    daily_burned = str(meta.get("statistics_window_burned_display") or meta.get("today_burned_display") or "待刷新")
    period_7d_new_power = meta.get("period_7d_new_power")
    period_7d_new_address_count = meta.get("period_7d_new_candidate_address_count")
    period_7d_burned = str(meta.get("period_7d_burned_display") or "待刷新")
    period_30d_new_power = meta.get("period_30d_new_power")
    period_30d_new_address_count = meta.get("period_30d_new_candidate_address_count")
    period_30d_burned = str(meta.get("period_30d_burned_display") or "待刷新")

    total_supply = str(meta.get("emission_total_supply_cap_display") or "2000亿")
    daily_total = str(meta.get("emission_daily_total_display") or "待刷新")
    daily_miner = str(meta.get("emission_daily_miner_display") or "待刷新")
    daily_node = str(meta.get("emission_daily_node_display") or "待刷新")
    power_per_coin = str(meta.get("power_required_per_mars_daily_display") or "待刷新")
    one_yi_power_output = _fmt_one_yi_power_output(meta)
    power_required_value = _as_float(meta.get("power_required_per_mars_daily"))
    one_yi_power_output_value = 100_000_000 / power_required_value if power_required_value > 0 else None
    total_burned_tokens = meta.get("network_total_burned_tokens")
    total_circulation_tokens = meta.get("network_total_circulation_tokens")
    daily_total_tokens = meta.get("emission_daily_total_tokens")

    mobile_metric_items = [
        (
            "network_total_power",
            "全网总算力",
            _fmt_power(network_total_power),
            "公开接口统计",
            _trend_points(
                meta,
                "network_total_power",
                [
                    meta.get("period_30d_start_total_power"),
                    meta.get("period_7d_start_total_power"),
                    meta.get("statistics_window_start_total_power"),
                    network_total_power,
                ],
            ),
        ),
        ("network_total_circulation", "全网流通量", circulation, "区块浏览器公开统计", _trend_points(meta, "network_total_circulation", [total_circulation_tokens])),
        (
            "network_current_price",
            "当前价格",
            current_price,
            "预言机触发价为最高价的 50%",
            _trend_points(meta, "network_current_price", [meta.get("network_current_price")]),
            {"highest_price": highest_price, "oracle_trigger_price": oracle_trigger_price},
        ),
        ("daily_emission", "每日产币量", daily_total, "官方经济模型口径", _trend_points(meta, "daily_emission", [daily_total_tokens, daily_total_tokens])),
        (
            "total_burned",
            "累计销毁",
            total_burned,
            "POWER 合约累计燃烧",
            _trend_points(
                meta,
                "total_burned",
                _trend_from_cumulative(
                    total_burned_tokens,
                    meta.get("period_30d_burned_tokens"),
                    meta.get("period_7d_burned_tokens"),
                    meta.get("statistics_window_burned_tokens"),
                ),
            ),
        ),
        ("daily_active_addresses", "统计日活跃地址", _fmt_count_unit(active_wallet_count), "同一统计窗口内活跃", _trend_points(meta, "daily_active_addresses", [active_wallet_count])),
        ("daily_new_addresses", "统计日新增地址", _fmt_count_unit(new_address_count), "首次出现在合约日志", _trend_points(meta, "daily_new_addresses", _trend_average_points(new_address_count, period_7d_new_address_count, period_30d_new_address_count))),
        ("daily_new_power", "统计日新增算力", _fmt_power(new_power), "北京时间统计日口径", _trend_points(meta, "daily_new_power", _trend_average_points(new_power, period_7d_new_power, period_30d_new_power))),
        ("daily_burned", "日销毁币量", daily_burned, "北京时间统计日口径", _trend_points(meta, "daily_burned", _trend_average_points(meta.get("statistics_window_burned_tokens"), meta.get("period_7d_burned_tokens"), meta.get("period_30d_burned_tokens")))),
        ("period_7d_new_power", "7 天新增算力", _fmt_power(period_7d_new_power), "最近 7 个完整统计日", _trend_points(meta, "period_7d_new_power", [period_7d_new_power])),
        ("period_7d_new_addresses", "7 天新增地址", _fmt_count_unit(period_7d_new_address_count), "首次进入 POWER 日志", _trend_points(meta, "period_7d_new_addresses", [period_7d_new_address_count])),
        ("period_7d_burned", "7 天销毁", period_7d_burned, "TokensBurned 汇总", _trend_points(meta, "period_7d_burned", [meta.get("period_7d_burned_tokens")])),
        ("period_30d_new_power", "30 天新增算力", _fmt_power(period_30d_new_power), "最近 30 个完整统计日", _trend_points(meta, "period_30d_new_power", [period_30d_new_power])),
        ("period_30d_new_addresses", "30 天新增地址", _fmt_count_unit(period_30d_new_address_count), "首次进入 POWER 日志", _trend_points(meta, "period_30d_new_addresses", [period_30d_new_address_count])),
        ("period_30d_burned", "30 天销毁", period_30d_burned, "TokensBurned 汇总", _trend_points(meta, "period_30d_burned", [meta.get("period_30d_burned_tokens")])),
        ("power_per_coin", "单币日需算力", power_per_coin, "按矿工 75% 产量估算", _trend_points(meta, "power_per_coin", [power_required_value])),
        ("one_yi_power_output", "1亿算力产出", one_yi_power_output, "按矿工 75% 日产币口径估算", _trend_points(meta, "one_yi_power_output", [one_yi_power_output_value])),
    ]
    hero_metric_cards = _build_mobile_metric_cards(mobile_metric_items[:8])
    key_cards = _build_mobile_metric_cards(mobile_metric_items)
    equation_cards = _build_mobile_flow_cards(
        [
            ("当前实际系数", "算力倍增", "20x", "当前 04 方程采用的实际膨胀倍数。"),
            ("最高膨胀系数", "上限", "160x", "方程膨胀系数逐级放大时的公开说明上限。"),
            ("执行周期", "单轮", "8 天", "圣诞方程与预言机方程按 8 天窗口执行。"),
            ("销毁比例", "流通量", "35%", "机制说明中每轮方程触发的流通量销毁比例。"),
        ]
    )
    burn_estimate = _build_burn_estimate(meta)
    burn_calculator = _build_burn_calculator(burn_estimate, mobile=True)
    rank_total_count = min(100, len(rows))
    rank_page_size = 10
    rank_total_pages = max(1, (rank_total_count + rank_page_size - 1) // rank_page_size)
    rank_first_page_end = min(rank_page_size, rank_total_count)
    rank_cards = _build_mobile_rank_cards(rows, limit=100, page_size=rank_page_size)
    paid_download_panel = _build_paid_download_panel(paid_download_config, mobile=True)
    rank_page_buttons = "".join(
        '<button class="m-rank-page-button%s" type="button" data-mobile-rank-page="%d" aria-current="%s">%d</button>'
        % (" is-active" if page == 1 else "", page, "page" if page == 1 else "false", page)
        for page in range(1, rank_total_pages + 1)
    )
    rank_controls_html = ""
    if rank_total_count > rank_page_size:
        rank_controls_html = f"""
      <div class="m-rank-pagination" data-mobile-rank-pagination>
        <button class="m-rank-page-button" type="button" data-mobile-rank-prev disabled>上一页</button>
        <div class="m-rank-pages">{rank_page_buttons}</div>
        <button class="m-rank-page-button" type="button" data-mobile-rank-next>下一页</button>
        <span class="m-rank-count" data-mobile-rank-count>第 1 / {rank_total_pages} 页 · 当前显示 1-{rank_first_page_end} / 共 {rank_total_count} 名</span>
      </div>"""
    meta_rows = _build_timeline(
        [
            ("最近刷新", generated_at),
            ("统计周期", statistics_window_label),
            ("采集频率", "每 24 小时一次"),
            ("抓取时间", "每日 00:00（北京时间，夜里 24:00）"),
            ("全网流通量", circulation),
            ("当前价格", current_price),
            ("最高价格", highest_price),
            ("预言机触发价", oracle_trigger_price),
            ("累计销毁", total_burned),
            ("日销毁币量", daily_burned),
            ("7 天新增算力", _fmt_power(period_7d_new_power)),
            ("7 天新增地址", _fmt_count_unit(period_7d_new_address_count)),
            ("7 天销毁", period_7d_burned),
            ("30 天新增算力", _fmt_power(period_30d_new_power)),
            ("30 天新增地址", _fmt_count_unit(period_30d_new_address_count)),
            ("30 天销毁", period_30d_burned),
            ("总产量", total_supply),
            ("矿工日产币量", daily_miner),
            ("节点日产币量", daily_node),
            ("1亿算力产出", one_yi_power_output),
        ]
    ).replace('class="line"', 'class="m-meta-row"')
    warning_html = _build_warning(meta, _fmt_percent(meta.get("coverage_target", 0.8))).replace('class="alert"', 'class="m-note"')
    embedded_payload = json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    metric_trend_payload = json.dumps(_build_metric_trend_payload(mobile_metric_items), ensure_ascii=False).replace("</script>", "<\\/script>")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>{escape(title)}</title>
  <meta name="description" content="{escape(subtitle)}">
  <meta name="theme-color" content="#030712">
  <link rel="preload" href="/data/latest.json" as="fetch" crossorigin>
{analytics_head}
  <style>{MOBILE_DASHBOARD_CSS}
{SHARE_POSTER_CSS}</style>
</head>
<body>
<div class="m-shell">
  <header class="m-top">
    <div class="m-brand">
      <div class="m-brand-main"><span class="m-mark"></span><span>MarsChain Rank</span></div>
      <div class="m-brand-actions">
        <a class="m-desktop-link" href="/?desktop=1" data-track="desktop_link" data-label="desktop">电脑版</a>
        <button class="m-share-button" type="button" data-share-poster data-track="share_poster" data-label="mobile">战报</button>
        <button class="m-lang-toggle" type="button" data-lang-toggle aria-label="Switch language">EN</button>
      </div>
    </div>
    <nav class="m-nav">
      <a href="#rank">排行</a>
      <a href="#core">核心</a>
      <a href="#equation">方程</a>
      <a href="#risk">说明</a>
    </nav>
  </header>
  <main>
    <section class="m-hero">
      <span class="m-chip">数据已加载 · 北京时间 00:00 每日采集</span>
      <h1><span>MarsChain</span><span>算力榜</span></h1>
      <p class="m-lead">下方先看前 100 名算力地址，再查看覆盖率、活跃地址和新增数据。</p>
      <div class="m-hero-grid">
        <article class="m-primary"><span>扫描覆盖率</span><b>{escape(coverage_label)}</b></article>
        <div class="m-card-grid">
          {hero_metric_cards}
        </div>
      </div>
      {warning_html}
    </section>
    <section id="rank" class="m-section m-rank-section">
      <div class="m-section-head"><div><span class="m-kicker">01 / RANK</span><h2>头部排行</h2></div><p>每页 10 名，共 10 页。</p></div>
      <div class="m-list m-rank-list" id="mobileRankList" data-page-size="{rank_page_size}" data-total-count="{rank_total_count}">{rank_cards}</div>
      {rank_controls_html}
      {paid_download_panel}
    </section>
    <section id="core" class="m-section">
      <div class="m-section-head"><div><span class="m-kicker">02 / CORE</span><h2>核心数据</h2></div><p>先看结果，再看口径。</p></div>
      <div class="m-card-grid">{key_cards}</div>
    </section>
    <section id="equation" class="m-section">
      <div class="m-section-head"><div><span class="m-kicker">03 / EQUATION</span><h2>方程膨胀系数</h2></div><p>机制参数展示。</p></div>
      <div class="m-list">{equation_cards}</div>
      {burn_calculator}
      <p class="m-note">当前 04 方程实际系数为 20x，后续最高可按机制说明逐级放大至 160x；本区块属于机制口径展示，不等同于当前实时收益承诺。</p>
    </section>
    <section id="risk" class="m-section">
      <div class="m-section-head"><div><span class="m-kicker">04 / NOTE</span><h2>数据说明</h2></div><p>公开数据存在延迟。</p></div>
      <div class="m-meta">{meta_rows}</div>
      <p class="m-note">榜单基于公开区块浏览器接口、RPC 与 POWER 合约日志生成，是 best effort 结果。公开接口延迟、RPC 节点漏返回、合约日志口径变化或缓存回退，都可能造成与官方后台存在差异。</p>
    </section>
  </main>
  <footer class="m-footer">MarsChain Rank 手机版 · 最近刷新：{escape(generated_at)} · 统计周期：{escape(statistics_window_label)}</footer>
</div>
<script id="rankData" type="application/json">{embedded_payload}</script>
<script id="metricTrendData" type="application/json">{metric_trend_payload}</script>
<script>{MOBILE_DASHBOARD_JS}
{LIVE_PRICE_JS}
{SHARE_POSTER_JS}
{METRIC_TREND_JS}
{LANGUAGE_TOGGLE_JS}</script>
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
