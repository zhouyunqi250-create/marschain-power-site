#!/usr/bin/env python3
"""Build a best-effort MarsChain power ranking from public explorer APIs.

The explorer exposes per-address power but not a public leaderboard endpoint.
This script samples recent blocks and transactions, extracts candidate
addresses, queries each address's power, and ranks addresses by discovered
power. The result is a "discovered addresses" leaderboard, not a guaranteed
full-network ranking.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_URL = "https://explorer.marschain.net/api"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://explorer.marschain.net/",
}


@dataclass
class RankedAddress:
    address: str
    power: int
    total_burned_amount: int
    burned_amount: int
    tx_seen: int
    miner_seen: int
    upline_seen: int
    source_score: int
    upline1: str | None
    upline2: str | None
    nodes_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "power": str(self.power),
            "power_display": format_units(self.power),
            "total_burned_amount": str(self.total_burned_amount),
            "total_burned_amount_display": format_token(self.total_burned_amount),
            "burned_amount": str(self.burned_amount),
            "burned_amount_display": format_token(self.burned_amount),
            "tx_seen": self.tx_seen,
            "miner_seen": self.miner_seen,
            "upline_seen": self.upline_seen,
            "source_score": self.source_score,
            "upline1": self.upline1,
            "upline2": self.upline2,
            "nodes_count": self.nodes_count,
        }


def request_json(path: str, params: dict[str, Any] | None = None, retries: int = 3) -> Any:
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        url = BASE_URL + path
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            last_error = RuntimeError(f"HTTP {exc.code} for {url}: {body[:200]}")
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(0.6 * attempt)
                continue
            raise last_error
        except Exception as exc:  # pragma: no cover - network variability
            last_error = exc
            if attempt < retries:
                time.sleep(0.6 * attempt)
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError(f"Request failed for {url}")


def normalize_address(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value.startswith("0x") or len(value) != 42:
        return None
    return value


def is_probably_user_address(value: str | None) -> bool:
    address = normalize_address(value)
    if not address:
        return False
    lower = address.lower()
    if lower == "0x0000000000000000000000000000000000000000":
        return False
    if lower.startswith("0x0000000000000000000000000000000000001"):
        return False
    return True


def format_units(raw: int) -> str:
    if raw >= 10**12:
        return f"{raw / 10**12:.2f}T"
    if raw >= 10**9:
        return f"{raw / 10**9:.2f}B"
    if raw >= 10**6:
        return f"{raw / 10**6:.2f}M"
    if raw >= 10**3:
        return f"{raw / 10**3:.2f}K"
    return str(raw)


def format_token(raw: int) -> str:
    return f"{raw / 10**18:.6f}"


def format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def load_cache(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def save_cache(path: Path | None, cache: dict[str, dict[str, Any]]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n")


def collect_candidates(
    tx_pages: int,
    tx_limit: int,
    block_pages: int,
    block_limit: int,
    include_to: bool,
    workers: int,
    progress: bool,
) -> tuple[Counter[str], Counter[str]]:
    tx_counter: Counter[str] = Counter()
    miner_counter: Counter[str] = Counter()

    def fetch_tx_page(page: int) -> tuple[int, dict[str, Any]]:
        payload = request_json("/transactions", {"page": page, "limit": tx_limit})
        return page, payload

    def fetch_block_page(page: int) -> tuple[int, dict[str, Any]]:
        payload = request_json("/blocks", {"page": page, "limit": block_limit})
        return page, payload

    tx_workers = max(1, min(workers, 32))
    block_workers = max(1, min(workers, 16))

    if tx_pages:
        with concurrent.futures.ThreadPoolExecutor(max_workers=tx_workers) as pool:
            future_map = {pool.submit(fetch_tx_page, page): page for page in range(1, tx_pages + 1)}
            for idx, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
                payload = future.result()[1]
                for tx in payload.get("transactions", []):
                    sender = normalize_address(tx.get("from"))
                    if is_probably_user_address(sender):
                        tx_counter[sender] += 1
                    if include_to:
                        receiver = normalize_address(tx.get("to"))
                        if is_probably_user_address(receiver):
                            tx_counter[receiver] += 1
                if progress and idx % 100 == 0:
                    print(f"[info] tx-page scan: {idx}/{tx_pages}", file=sys.stderr)

    if block_pages:
        with concurrent.futures.ThreadPoolExecutor(max_workers=block_workers) as pool:
            future_map = {pool.submit(fetch_block_page, page): page for page in range(1, block_pages + 1)}
            for idx, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
                payload = future.result()[1]
                for block in payload.get("blocks", []):
                    miner = normalize_address(block.get("miner"))
                    if is_probably_user_address(miner):
                        miner_counter[miner] += 1
                if progress and idx % 100 == 0:
                    print(f"[info] block-page scan: {idx}/{block_pages}", file=sys.stderr)

    return tx_counter, miner_counter


def build_row_from_payload(
    address: str,
    payload: dict[str, Any],
    tx_seen: int = 0,
    miner_seen: int = 0,
    upline_seen: int = 0,
) -> RankedAddress | None:
    power = int(payload.get("power", "0") or 0)
    total_burned_amount = int(payload.get("totalBurnedAmount", "0") or 0)
    burned_amount = int(payload.get("burnedAmount", "0") or 0)
    if power <= 0:
        return None
    return RankedAddress(
        address=address,
        power=power,
        total_burned_amount=total_burned_amount,
        burned_amount=burned_amount,
        tx_seen=tx_seen,
        miner_seen=miner_seen,
        upline_seen=upline_seen,
        source_score=tx_seen * 10 + miner_seen * 20 + upline_seen * 5,
        upline1=normalize_address(payload.get("upline1")),
        upline2=normalize_address(payload.get("upline2")),
    )


def fetch_power_payload(address: str, cache: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any], bool]:
    cached = cache.get(address)
    if cached is not None:
        return address, cached, True
    payload = request_json(f"/power/{address}")
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["cached_at"] = int(time.time())
    return address, payload, False


def enrich_nodes(address: str) -> int | None:
    payload = request_json(f"/nodes/{address}")
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        return len(nodes)
    return 0 if nodes is None else None


def fetch_address_transactions_page(address: str, page: int, limit: int) -> tuple[str, int, dict[str, Any]]:
    payload = request_json(f"/addresses/{address}/transactions", {"page": page, "limit": limit})
    return address, page, payload


def collect_history_counter(
    seed_addresses: list[str],
    pages: int,
    limit: int,
    workers: int,
    progress: bool,
) -> Counter[str]:
    counter: Counter[str] = Counter()
    if not seed_addresses or pages <= 0 or limit <= 0:
        return counter

    tasks: list[tuple[str, int]] = [(address, page) for address in seed_addresses for page in range(1, pages + 1)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
        future_map = {
            pool.submit(fetch_address_transactions_page, address, page, limit): (address, page)
            for address, page in tasks
        }
        for idx, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
            address, page = future_map[future]
            try:
                _, _, payload = future.result()
            except Exception as exc:
                print(f"[warn] address tx lookup failed for {address} page {page}: {exc}", file=sys.stderr)
                continue
            for tx in payload.get("transactions", []):
                sender = normalize_address(tx.get("from"))
                receiver = normalize_address(tx.get("to"))
                if sender == address and is_probably_user_address(receiver):
                    counter[receiver] += 1
                elif receiver == address and is_probably_user_address(sender):
                    counter[sender] += 1
            if progress and idx % 100 == 0:
                print(
                    f"[info] history tx scan: {idx}/{len(tasks)} seed-pages checked",
                    file=sys.stderr,
                )
    return counter


def write_html(path: Path, rows: list[RankedAddress], meta: dict[str, Any]) -> None:
    summary_items = [
        ("生成时间", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta["generated_at"]))),
        ("候选地址", f"{meta['candidate_count']:,}"),
        ("发现正算力地址", f"{meta['positive_power_count']:,}"),
        ("榜单行数", f"{meta['ranked_count']:,}"),
        ("全网总算力", format_units(meta["network_total_power"])),
        ("已发现覆盖", format_percent(meta["discovered_power_coverage"])),
    ]
    summary_html = "\n".join(
        f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div></div>'
        for label, value in summary_items
    )
    row_html = "\n".join(
        (
            "<tr>"
            f"<td>{idx}</td>"
            f"<td class='mono'>{row.address}</td>"
            f"<td data-sort='{row.power}'>{format_units(row.power)}</td>"
            f"<td data-sort='{row.total_burned_amount}'>{format_token(row.total_burned_amount)}</td>"
            f"<td>{row.tx_seen}</td>"
            f"<td>{row.miner_seen}</td>"
            f"<td>{row.upline_seen}</td>"
            f"<td>{row.nodes_count if row.nodes_count is not None else ''}</td>"
            f"<td class='mono'>{row.upline1 or ''}</td>"
            f"<td class='mono'>{row.upline2 or ''}</td>"
            "</tr>"
        )
        for idx, row in enumerate(rows, start=1)
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MarsChain 算力排行榜</title>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: #121a31;
      --panel-2: #182342;
      --line: #2b3b6b;
      --text: #e9eefc;
      --muted: #9fb0d9;
      --accent: #67d4ff;
      --accent-2: #8b5cf6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 32px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(103, 212, 255, 0.18), transparent 22%),
        linear-gradient(180deg, #0b1020 0%, #0f1730 100%);
      color: var(--text);
    }}
    .wrap {{ max-width: 1440px; margin: 0 auto; }}
    .hero {{
      background: linear-gradient(135deg, rgba(24, 35, 66, 0.98), rgba(16, 24, 48, 0.92));
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 28px 30px;
      margin-bottom: 22px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.35);
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    h1 {{ margin: 0; font-size: 36px; line-height: 1.15; }}
    .sub {{
      margin-top: 10px;
      color: var(--muted);
      max-width: 920px;
      line-height: 1.6;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 14px;
      margin: 18px 0 26px;
    }}
    .card {{
      background: linear-gradient(180deg, rgba(24, 35, 66, 0.95), rgba(15, 23, 48, 0.95));
      border: 1px solid rgba(103, 212, 255, 0.15);
      border-radius: 18px;
      padding: 16px;
    }}
    .label {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
    .value {{ font-size: 24px; font-weight: 700; }}
    .panel {{
      background: rgba(18, 26, 49, 0.96);
      border: 1px solid var(--line);
      border-radius: 24px;
      overflow: hidden;
    }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      padding: 14px 16px;
      border-bottom: 1px solid rgba(43, 59, 107, 0.55);
      vertical-align: top;
      text-align: left;
      font-size: 14px;
    }}
    th {{
      position: sticky;
      top: 0;
      background: rgba(24, 35, 66, 0.98);
      color: #d7e5ff;
      z-index: 1;
    }}
    tbody tr:nth-child(odd) {{ background: rgba(255, 255, 255, 0.01); }}
    tbody tr:hover {{ background: rgba(103, 212, 255, 0.06); }}
    .mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      word-break: break-all;
      color: #dce7ff;
    }}
    .foot {{
      margin-top: 16px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }}
    @media (max-width: 1180px) {{ .grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }} }}
    @media (max-width: 780px) {{
      body {{ padding: 18px; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      h1 {{ font-size: 28px; }}
      .panel {{ overflow: auto; }}
      table {{ min-width: 1100px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">MarsChain Power Ranking</div>
      <h1>MarsChain 已发现地址算力榜</h1>
      <div class="sub">
        这份榜单基于 explorer 的公开接口生成，不是官方全网榜。
        它通过扫描近期区块、交易、以及正算力地址的上级关系，拼出一份更接近全网的 best effort 排行。
      </div>
      <div class="grid">{summary_html}</div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>Top {len(rows)} 地址</div>
        <div>扫描范围: {meta['tx_pages']} 页交易, {meta['block_pages']} 页区块, upline 深度 {meta['upline_depth']}</div>
      </div>
      <table>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Address</th>
            <th>Power</th>
            <th>Total Burned</th>
            <th>Tx Seen</th>
            <th>Miner Seen</th>
            <th>Upline Seen</th>
            <th>Nodes</th>
            <th>Upline 1</th>
            <th>Upline 2</th>
          </tr>
        </thead>
        <tbody>
          {row_html}
        </tbody>
      </table>
    </section>
    <div class="foot">
      说明: 站点公开提供了单地址算力接口，但没有公开榜单接口。覆盖率表示当前已发现正算力地址的算力总和占 explorer 公布全网总算力的比例。
    </div>
  </div>
</body>
</html>
"""
    path.write_text(html)


def write_xlsx(path: Path, rows: list[RankedAddress], meta: dict[str, Any]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Ranking"
    header = [
        "Rank",
        "Address",
        "Power",
        "Power Display",
        "Total Burned",
        "Total Burned Display",
        "Burned",
        "Burned Display",
        "Tx Seen",
        "Miner Seen",
        "Upline Seen",
        "Source Score",
        "Nodes Count",
        "Upline 1",
        "Upline 2",
    ]
    ws.append(header)
    for idx, row in enumerate(rows, start=1):
        ws.append(
            [
                idx,
                row.address,
                row.power,
                row.to_dict()["power_display"],
                row.total_burned_amount,
                row.to_dict()["total_burned_amount_display"],
                row.burned_amount,
                row.to_dict()["burned_amount_display"],
                row.tx_seen,
                row.miner_seen,
                row.upline_seen,
                row.source_score,
                row.nodes_count,
                row.upline1,
                row.upline2,
            ]
        )
    fill = PatternFill("solid", fgColor="1F4B99")
    font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    widths = {
        1: 8,
        2: 46,
        3: 16,
        4: 14,
        5: 22,
        6: 18,
        7: 22,
        8: 18,
        9: 10,
        10: 12,
        11: 12,
        12: 12,
        13: 12,
        14: 46,
        15: 46,
    }
    for idx, width in widths.items():
        ws.column_dimensions[get_column_letter(idx)].width = width

    summary = wb.create_sheet("Summary")
    summary.append(["Metric", "Value"])
    for key, value in [
        ("Generated At", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta["generated_at"]))),
        ("Ranking Type", meta["ranking_type"]),
        ("Tx Pages", meta["tx_pages"]),
        ("Block Pages", meta["block_pages"]),
        ("Upline Depth", meta["upline_depth"]),
        ("Candidate Count", meta["candidate_count"]),
        ("Positive Power Count", meta["positive_power_count"]),
        ("Ranked Count", meta["ranked_count"]),
        ("Network Total Power", meta["network_total_power"]),
        ("Discovered Total Power", meta["discovered_total_power"]),
        ("Discovered Coverage", format_percent(meta["discovered_power_coverage"])),
    ]:
        summary.append([key, value])
    for cell in summary[1]:
        cell.fill = fill
        cell.font = font
    summary.column_dimensions["A"].width = 24
    summary.column_dimensions["B"].width = 28
    wb.save(path)


def write_json(path: Path, rows: list[RankedAddress], meta: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(
            {
                "meta": meta,
                "rows": [row.to_dict() for row in rows],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def write_csv(path: Path, rows: list[RankedAddress]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "rank",
                "address",
                "power",
                "power_display",
                "total_burned_amount",
                "total_burned_amount_display",
                "burned_amount",
                "burned_amount_display",
                "tx_seen",
                "miner_seen",
                "upline_seen",
                "source_score",
                "nodes_count",
                "upline1",
                "upline2",
            ],
        )
        writer.writeheader()
        for idx, row in enumerate(rows, start=1):
            out = row.to_dict()
            out["rank"] = idx
            writer.writerow(out)


def lookup_power_rows(
    addresses: list[str],
    tx_counter: Counter[str],
    miner_counter: Counter[str],
    upline_counter: Counter[str],
    args: argparse.Namespace,
    cache: dict[str, dict[str, Any]],
    progress_label: str,
) -> tuple[list[RankedAddress], dict[str, dict[str, Any]]]:
    rows: list[RankedAddress] = []
    cache_updates: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_map = {pool.submit(fetch_power_payload, address, cache): address for address in addresses}
        for idx, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
            address = future_map[future]
            try:
                returned_address, payload, _ = future.result()
            except Exception as exc:
                print(f"[warn] power lookup failed for {address}: {exc}", file=sys.stderr)
                continue
            cache_updates[returned_address] = payload
            row = build_row_from_payload(
                returned_address,
                payload,
                tx_seen=tx_counter[returned_address],
                miner_seen=miner_counter[returned_address],
                upline_seen=upline_counter[returned_address],
            )
            if row:
                rows.append(row)
            if args.progress and idx % 100 == 0:
                print(f"[info] {progress_label}: checked {idx}/{len(addresses)} candidates", file=sys.stderr)
    return rows, cache_updates


def build_ranking(args: argparse.Namespace) -> tuple[list[RankedAddress], dict[str, Any]]:
    tx_counter, miner_counter = collect_candidates(
        tx_pages=args.tx_pages,
        tx_limit=args.tx_limit,
        block_pages=args.block_pages,
        block_limit=args.block_limit,
        include_to=args.include_to,
        workers=args.workers,
        progress=args.progress,
    )

    candidates = set(tx_counter) | set(miner_counter)
    ordered_candidates = sorted(
        candidates,
        key=lambda addr: (tx_counter[addr] * 10 + miner_counter[addr] * 20, tx_counter[addr], miner_counter[addr], addr),
        reverse=True,
    )
    if args.max_candidates:
        ordered_candidates = ordered_candidates[: args.max_candidates]

    cache = load_cache(Path(args.cache_file) if args.cache_file else None)
    upline_counter: Counter[str] = Counter()
    rows_map: dict[str, RankedAddress] = {}

    initial_rows, cache_updates = lookup_power_rows(
        ordered_candidates,
        tx_counter,
        miner_counter,
        upline_counter,
        args,
        cache,
        "initial",
    )
    cache.update(cache_updates)
    for row in initial_rows:
        rows_map[row.address] = row

    seen_addresses = set(ordered_candidates)
    for depth in range(1, args.upline_depth + 1):
        next_counter: Counter[str] = Counter()
        for row in rows_map.values():
            for candidate in (row.upline1, row.upline2):
                if is_probably_user_address(candidate) and candidate not in seen_addresses:
                    next_counter[candidate] += 1
        if not next_counter:
            break
        next_addresses = [address for address, _ in next_counter.most_common(args.upline_limit)]
        if not next_addresses:
            break
        seen_addresses.update(next_addresses)
        upline_counter.update(next_counter)
        depth_rows, cache_updates = lookup_power_rows(
            next_addresses,
            tx_counter,
            miner_counter,
            upline_counter,
            args,
            cache,
            f"upline-depth-{depth}",
        )
        cache.update(cache_updates)
        for row in depth_rows:
            rows_map[row.address] = row

    expanded_history_addresses: set[str] = set()
    for depth in range(1, args.history_depth + 1):
        seed_rows = [
            row
            for row in sorted(rows_map.values(), key=lambda row: (row.power, row.total_burned_amount), reverse=True)
            if row.address not in expanded_history_addresses
        ][: args.history_seed_limit]
        if not seed_rows:
            break
        seed_addresses = [row.address for row in seed_rows]
        expanded_history_addresses.update(seed_addresses)

        history_counter = collect_history_counter(
            seed_addresses=seed_addresses,
            pages=args.history_pages,
            limit=args.history_tx_limit,
            workers=args.workers,
            progress=args.progress,
        )
        if not history_counter:
            break

        next_addresses = [
            address
            for address, _ in history_counter.most_common(args.history_candidate_limit)
            if address not in seen_addresses
        ]
        if not next_addresses:
            continue

        seen_addresses.update(next_addresses)
        tx_counter.update(history_counter)
        depth_rows, cache_updates = lookup_power_rows(
            next_addresses,
            tx_counter,
            miner_counter,
            upline_counter,
            args,
            cache,
            f"history-depth-{depth}",
        )
        cache.update(cache_updates)
        for row in depth_rows:
            rows_map[row.address] = row

    save_cache(Path(args.cache_file) if args.cache_file else None, cache)

    all_rows = list(rows_map.values())
    all_rows.sort(
        key=lambda row: (
            row.power,
            row.total_burned_amount,
            row.source_score,
            row.tx_seen,
            row.miner_seen,
            row.upline_seen,
            row.address,
        ),
        reverse=True,
    )
    rows = all_rows[: args.top]

    if args.include_nodes and rows:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.workers, 8)) as pool:
            future_map = {pool.submit(enrich_nodes, row.address): row for row in rows}
            for future in concurrent.futures.as_completed(future_map):
                row = future_map[future]
                try:
                    row.nodes_count = future.result()
                except Exception as exc:
                    print(f"[warn] nodes lookup failed for {row.address}: {exc}", file=sys.stderr)

    network_total_power = int(request_json("/power/stats").get("totalPower", "0") or 0)
    discovered_total_power = sum(row.power for row in all_rows)
    meta = {
        "generated_at": int(time.time()),
        "base_url": BASE_URL,
        "ranking_type": "best_effort_discovered_addresses",
        "tx_pages": args.tx_pages,
        "tx_limit": args.tx_limit,
        "block_pages": args.block_pages,
        "block_limit": args.block_limit,
        "include_to": args.include_to,
        "upline_depth": args.upline_depth,
        "upline_limit": args.upline_limit,
        "history_depth": args.history_depth,
        "history_pages": args.history_pages,
        "history_tx_limit": args.history_tx_limit,
        "history_seed_limit": args.history_seed_limit,
        "history_candidate_limit": args.history_candidate_limit,
        "seed_candidate_count": len(ordered_candidates),
        "candidate_count": len(seen_addresses),
        "positive_power_count": len(all_rows),
        "discovered_total_power": discovered_total_power,
        "network_total_power": network_total_power,
        "discovered_power_coverage": (discovered_total_power / network_total_power) if network_total_power else 0.0,
        "ranked_count": len(rows),
    }
    return rows, meta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a best-effort MarsChain power ranking.")
    parser.add_argument("--tx-pages", type=int, default=60, help="Recent transaction pages to scan.")
    parser.add_argument("--tx-limit", type=int, default=100, help="Transactions per page.")
    parser.add_argument("--block-pages", type=int, default=20, help="Recent block pages to scan.")
    parser.add_argument("--block-limit", type=int, default=100, help="Blocks per page.")
    parser.add_argument("--max-candidates", type=int, default=3000, help="Maximum candidate addresses to power-check.")
    parser.add_argument("--top", type=int, default=100, help="Number of ranked rows to output.")
    parser.add_argument("--workers", type=int, default=16, help="Concurrent power lookups.")
    parser.add_argument("--include-to", action="store_true", help="Also include transaction recipient addresses as candidates.")
    parser.add_argument("--include-nodes", action="store_true", help="Fetch /nodes/{address} for final ranked rows.")
    parser.add_argument("--upline-depth", type=int, default=2, help="How many rounds of discovered uplines to follow.")
    parser.add_argument("--upline-limit", type=int, default=2500, help="Maximum upline candidates per depth.")
    parser.add_argument("--history-depth", type=int, default=0, help="How many rounds of address-history expansion to run.")
    parser.add_argument("--history-pages", type=int, default=2, help="Transaction pages to scan per history seed address.")
    parser.add_argument("--history-tx-limit", type=int, default=100, help="Transactions per page for address-history expansion.")
    parser.add_argument("--history-seed-limit", type=int, default=200, help="How many top power addresses to expand per history round.")
    parser.add_argument("--history-candidate-limit", type=int, default=50000, help="Maximum counterparties to power-check per history round.")
    parser.add_argument("--output-dir", default="output", help="Directory for JSON and CSV outputs.")
    parser.add_argument("--prefix", default="marschain_power_rank", help="Output filename prefix.")
    parser.add_argument("--cache-file", default="output/marschain_power_cache.json", help="Power lookup cache JSON path.")
    parser.add_argument("--progress", action="store_true", help="Print progress to stderr.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, meta = build_ranking(args)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{args.prefix}_{stamp}.json"
    csv_path = output_dir / f"{args.prefix}_{stamp}.csv"
    html_path = output_dir / f"{args.prefix}_{stamp}.html"
    xlsx_path = output_dir / f"{args.prefix}_{stamp}.xlsx"
    write_json(json_path, rows, meta)
    write_csv(csv_path, rows)
    write_html(html_path, rows, meta)
    write_xlsx(xlsx_path, rows, meta)

    print(f"Generated {len(rows)} rows from {meta['candidate_count']} candidates.")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    print(f"HTML: {html_path}")
    print(f"XLSX: {xlsx_path}")
    if rows:
        print("\nTop 10")
        for idx, row in enumerate(rows[:10], start=1):
            print(
                f"{idx:>2}. {row.address}  power={row.power} ({format_units(row.power)})  "
                f"burned={format_token(row.total_burned_amount)}  tx_seen={row.tx_seen} "
                f"miner_seen={row.miner_seen} upline_seen={row.upline_seen}"
            )
    else:
        print("No ranked rows found. Try scanning more tx pages or using --include-to.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
