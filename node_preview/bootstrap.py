"""Create a consistent, isolated copy of the existing address database."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def bootstrap(root: Path, source: Path, origin: str, rpc_url: str) -> dict:
    from node_preview.metrics import MetricStore

    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    database = state / "preview.sqlite3"
    copied = False
    if not database.exists():
        temporary = database.with_suffix(".initializing")
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as src:
            with sqlite3.connect(temporary) as dest:
                src.backup(dest, pages=1024, sleep=0.01)
                if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Initial database copy failed integrity check")
        os.chmod(temporary, 0o600)
        os.replace(temporary, database)
        copied = True
    seeds = MetricStore(database, rpc_url=rpc_url, seed_dir=state).bootstrap(origin=origin)
    payload = seeds.get("payload") or {}
    if not isinstance(payload.get("meta"), dict) or not payload.get("rows"):
        raise RuntimeError("A valid current public ranking is required to preserve existing panels")
    # Keep every old public leader in the calibration address set; never overwrite
    # a newer saved value with a public seed's older data.
    with sqlite3.connect(database, timeout=30) as db:
        for row in payload["rows"]:
            address = str(row.get("address", "")).lower()
            if len(address) == 42 and address.startswith("0x"):
                try:
                    int(address[2:], 16)
                except ValueError:
                    continue
                db.execute("INSERT OR IGNORE INTO addresses(address) VALUES (?)", (address,))
        count = db.execute("SELECT COUNT(*) FROM addresses").fetchone()[0]
    result = {"database": str(database), "copied": copied, "addresses": count,
              "seed_rows": len(payload["rows"]), "seed_generated_at": payload["meta"].get("generated_at"),
              "initialized_at": int(time.time()), "production_writes": False}
    atomic_json(state / "bootstrap-status.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path.home() / "marschain-node/full-power-export/scan.sqlite3")
    parser.add_argument("--origin", default="https://marschain-power-site-chu.oss-cn-hangzhou.aliyuncs.com")
    parser.add_argument("--rpc", default="http://127.0.0.1:18555")
    args = parser.parse_args()
    print(json.dumps(bootstrap(args.root.resolve(), args.source.resolve(), args.origin, args.rpc), ensure_ascii=False))


if __name__ == "__main__":
    main()
