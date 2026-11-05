"""Collect native balances once per minute, independently of power publication.

Only ``state/holder-ranking-public.json`` is eligible for inclusion in the
website.  The complete proof is overwritten in a private last-good directory;
there is no per-minute archive growth and no cloud access in this process.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import time
from pathlib import Path

from node_preview.holder_ranking import PUBLIC_FILE, load_public_snapshot, validate_public_snapshot
from node_preview.holder_ranking_snapshot import atomic_json, collect_snapshot, validate_options, write_snapshot


def run_once(root: Path, *, max_seconds: float = 45, batch_size: int = 200,
             workers: int = 4) -> dict:
    """Commit a verified full-census snapshot, preserving the old file on error."""
    validate_options(max_seconds, batch_size, workers)
    root = Path(root).resolve()
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    status_path = state / "holder-ranking-status.json"
    public_path = state / PUBLIC_FILE
    prior = load_public_snapshot(root)
    began, attempted_at = time.monotonic(), int(time.time())
    try:
        sources = [Path.home() / "marschain-node/full-power-export/scan.sqlite3",
                   root / "state/preview.sqlite3"]
        artifacts = collect_snapshot(sources, max_seconds=max_seconds,
                                     batch_size=batch_size, workers=workers)
        public = validate_public_snapshot(artifacts["public"], max_age=180)
        meta = public["meta"]
        if prior:
            previous = prior["meta"]
            if (meta["block_number"] < previous["block_number"]
                    or meta["block_timestamp"] < previous["block_timestamp"]
                    or meta["block_number"] == previous["block_number"]
                    and meta["block_hash"] != previous["block_hash"]):
                raise RuntimeError("Refusing a regressed or conflicting holder snapshot")
        # This entire directory is private and bounded to one complete proof.
        work = state / "holders"
        work.mkdir(parents=True, exist_ok=True)
        os.chmod(work, 0o700)
        write_snapshot(work, artifacts)
        encoded = json.dumps(public, ensure_ascii=False, separators=(",", ":")).encode()
        # The only producer-consumer handoff; a reader sees either complete file.
        atomic_json(public_path, public)
        report = {"state": "ready", "attempted_at": attempted_at, "last_success": int(time.time()),
                  "block_number": meta["block_number"], "block_hash": meta["block_hash"],
                  "block_timestamp": meta["block_timestamp"], "address_count": meta["address_count"],
                  "ranking_address_count": meta["ranking_address_count"], "public_rows": len(public["rows"]),
                  "system_balance_query_count": 0, "bytes": len(encoded),
                  "public_sha256": hashlib.sha256(encoded).hexdigest(),
                  "elapsed_seconds": round(time.monotonic() - began, 3),
                  "last_good_snapshot_preserved": False}
    except Exception as exc:
        # Errors/status are private; no failure message or proof enters latest.json.
        report = {"state": "error", "attempted_at": attempted_at,
                  "error": str(exc)[:500], "last_good_snapshot_preserved": True,
                  "last_good_block": prior["meta"]["block_number"] if prior else None,
                  "elapsed_seconds": round(time.monotonic() - began, 3)}
    atomic_json(status_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=45)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        validate_options(args.max_seconds, args.batch_size, args.workers)
    except ValueError as exc:
        parser.error(str(exc))
    root = args.root.resolve()
    (root / "state").mkdir(parents=True, exist_ok=True)
    lock_path = root / "state/holder-ranking.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"state": "already_running"}), flush=True)
            return 75
        def interrupted(_signum, _frame):
            # systemd's timeout must unwind the staged proof writer, so its
            # rollback and TemporaryDirectory cleanup get a chance to finish.
            raise TimeoutError("Holder collection interrupted before completion")

        previous_handler = signal.signal(signal.SIGTERM, interrupted)
        try:
            result = run_once(root, max_seconds=args.max_seconds, batch_size=args.batch_size, workers=args.workers)
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0 if result["state"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
