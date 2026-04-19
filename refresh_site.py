#!/usr/bin/env python3
"""Refresh the MarsChain ranking site until the coverage target is reached."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from argparse import Namespace
from pathlib import Path

from build_frontend_dashboard import build_html
from marschain_power_rank import build_ranking, write_csv, write_html, write_json, write_xlsx


SCAN_TIERS = [
    {
        "tx_pages": 1200,
        "block_pages": 200,
        "max_candidates": 30000,
        "upline_limit": 20000,
        "upline_depth": 6,
        "workers": 32,
        "history_depth": 1,
        "history_pages": 2,
        "history_seed_limit": 150,
        "history_candidate_limit": 20000,
    },
    {
        "tx_pages": 3000,
        "block_pages": 1000,
        "max_candidates": 0,
        "upline_limit": 80000,
        "upline_depth": 6,
        "workers": 32,
        "history_depth": 1,
        "history_pages": 3,
        "history_seed_limit": 250,
        "history_candidate_limit": 40000,
    },
    {
        "tx_pages": 5000,
        "block_pages": 3000,
        "max_candidates": 0,
        "upline_limit": 160000,
        "upline_depth": 6,
        "workers": 40,
        "history_depth": 2,
        "history_pages": 3,
        "history_seed_limit": 300,
        "history_candidate_limit": 60000,
    },
    {
        "tx_pages": 8000,
        "block_pages": 8000,
        "max_candidates": 0,
        "upline_limit": 300000,
        "upline_depth": 8,
        "workers": 40,
        "history_depth": 2,
        "history_pages": 4,
        "history_seed_limit": 400,
        "history_candidate_limit": 120000,
    },
    {
        "tx_pages": 12000,
        "block_pages": 15000,
        "max_candidates": 0,
        "upline_limit": 500000,
        "upline_depth": 10,
        "workers": 48,
        "history_depth": 3,
        "history_pages": 5,
        "history_seed_limit": 500,
        "history_candidate_limit": 200000,
    },
]


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
    cache_file: Path,
) -> Namespace:
    return Namespace(
        tx_pages=tx_pages,
        tx_limit=100,
        block_pages=block_pages,
        block_limit=100,
        max_candidates=max_candidates,
        top=100,
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
        progress=True,
    )


def write_site_bundle(site_dir: Path, payload: dict, csv_path: Path, xlsx_path: Path) -> None:
    data_dir = site_dir / "data"
    downloads_dir = site_dir / "downloads"
    site_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    (site_dir / "index.html").write_text(build_html(payload))
    (data_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    shutil.copy2(csv_path, downloads_dir / "latest.csv")
    shutil.copy2(xlsx_path, downloads_dir / "latest.xlsx")
    (site_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the MarsChain ranking site with an 80% coverage target.")
    parser.add_argument("--coverage-target", type=float, default=0.80, help="Stop once discovered coverage reaches this threshold.")
    parser.add_argument("--output-dir", default="output", help="Directory for generated ranking files.")
    parser.add_argument("--site-dir", default="site", help="Directory for deployable static site output.")
    parser.add_argument("--cache-file", default="output/marschain_power_cache.json", help="Shared cache file for power lookups.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    history_dir = output_dir / "history"
    latest_dir = output_dir / "latest"
    site_dir = Path(args.site_dir)
    cache_file = Path(args.cache_file)

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
            cache_file=cache_file,
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
        raise RuntimeError(
            f"Coverage target not met after deepest scan tier {chosen_label}: "
            f"{chosen_meta['discovered_power_coverage']:.4%} < {args.coverage_target:.2%}. "
            "Refusing to publish a below-target site."
        )

    chosen_meta = dict(chosen_meta)
    chosen_meta["coverage_target"] = args.coverage_target
    chosen_meta["target_met"] = target_met
    chosen_meta["tier_label"] = chosen_label

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

    (latest_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    (latest_dir / "build-meta.json").write_text(
        json.dumps(
            {
                "generated_at": chosen_meta["generated_at"],
                "coverage": chosen_meta["discovered_power_coverage"],
                "coverage_target": chosen_meta["coverage_target"],
                "target_met": chosen_meta["target_met"],
                "candidate_count": chosen_meta["candidate_count"],
                "positive_power_count": chosen_meta["positive_power_count"],
                "tx_pages": chosen_meta["tx_pages"],
                "block_pages": chosen_meta["block_pages"],
                "tier_label": chosen_meta["tier_label"],
                "history_json": str(json_path),
                "history_csv": str(csv_path),
                "history_html": str(html_path),
                "history_xlsx": str(xlsx_path),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    shutil.copy2(csv_path, latest_dir / "latest.csv")
    shutil.copy2(html_path, latest_dir / "latest.html")
    shutil.copy2(xlsx_path, latest_dir / "latest.xlsx")

    write_site_bundle(site_dir, payload, csv_path, xlsx_path)

    summary = {
        "generated_at": chosen_meta["generated_at"],
        "coverage": chosen_meta["discovered_power_coverage"],
        "coverage_target": chosen_meta["coverage_target"],
        "target_met": chosen_meta["target_met"],
        "candidate_count": chosen_meta["candidate_count"],
        "positive_power_count": chosen_meta["positive_power_count"],
        "tx_pages": chosen_meta["tx_pages"],
        "block_pages": chosen_meta["block_pages"],
        "tier_label": chosen_meta["tier_label"],
        "history_json": str(json_path),
    }
    (site_dir / "build-meta.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    print(
        f"[done] latest coverage={chosen_meta['discovered_power_coverage']:.4%} "
        f"target={chosen_meta['coverage_target']:.2%} target_met={chosen_meta['target_met']}"
    )
    print(f"[done] site={site_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
