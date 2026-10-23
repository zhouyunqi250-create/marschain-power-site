"""Freeze all known address powers at a single confirmed Beijing 08:00 cutoff.

Archives remain private, outside the web document root. This module never
uploads files, writes production, queries balances or modifies working powers.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import fcntl
import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from node_preview.bootstrap import atomic_json
from node_preview.chain import RPC, ChainError, ReconcileRequired, address, quantity


class CutoffNotReady(ChainError):
    """No confirmed block at or after the requested cutoff is available yet."""


def archive_name(target: int) -> str:
    if target % 86400 == 0:
        return time.strftime("%Y%m%d-0800", time.gmtime(target))
    # Manual drills cannot occupy or replace a daily 08:00 archive.
    return time.strftime("%Y%m%d-%H%M%S-manual", time.gmtime(target + 28800))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cutoff_block(rpc: RPC, target: int, confirmations: int = 6) -> tuple[dict[str, Any], int]:
    safe = max(0, rpc.head() - confirmations)
    marker = rpc.block(safe)
    if quantity(marker["timestamp"]) < target:
        raise CutoffNotReady("Waiting for a confirmed block at or after the requested cutoff")
    low, high = 0, safe
    while low < high:
        middle = (low + high) // 2
        if quantity(rpc.block(middle)["timestamp"]) >= target:
            high = middle
        else:
            low = middle + 1
    if low == 0:
        raise ChainError("Requested cutoff precedes the first available chain block")
    result = rpc.block(low - 1)
    following = rpc.block(low)
    if quantity(result["timestamp"]) >= target or quantity(following["timestamp"]) < target:
        raise ReconcileRequired("Could not establish adjacent blocks around the fixed cutoff")
    if following["parentHash"].lower() != result["hash"].lower():
        raise ReconcileRequired("Cutoff blocks disagree on their parent hash")
    return result, safe


def _last_success(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text()).get("last_success")
    except (OSError, ValueError):
        return None


def _existing(archive: Path, target: int) -> dict | None:
    if not archive.exists():
        return None
    try:
        manifest = json.loads((archive / "manifest.json").read_text())
        if manifest.get("state") != "complete" or manifest.get("target_timestamp") != target:
            raise ValueError("Archive is not a complete snapshot of the same cutoff")
        if manifest.get("complete_for_known_addresses") is not True:
            raise ValueError("Existing archive was not complete for its known-address set")
        for name in ("public.json", "power.jsonl.gz"):
            if sha256(archive / name) != manifest["files"][name]["sha256"]:
                raise ValueError(f"Existing archive integrity check failed: {name}")
        return manifest
    except (OSError, ValueError, KeyError) as exc:
        raise ChainError(f"Refusing to replace existing archive {archive.name}: {exc}") from exc


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def freeze(root: Path | str, rpc_url: str = "http://127.0.0.1:18555", *, at: int | None = None,
           workers: int = 4, batch_size: int = 100, rpc_client: RPC | None = None,
           metric_store: Any = None) -> dict:
    root = Path(root).expanduser().resolve()
    target = int(time.time()) // 86400 * 86400 if at is None else int(at)
    if target < 0 or not 1 <= workers <= 64 or not 1 <= batch_size <= 1000:
        raise ValueError("Invalid cutoff, worker count or batch size")
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    archives = root / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    os.chmod(archives, 0o700)
    status_path = state / "freezer-status.json"
    archive = archives / archive_name(target)
    previous_success = _last_success(status_path)
    with (state / "freezer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "busy", "target_timestamp": target, "production_writes": False}
        began = time.monotonic()
        staging: Path | None = None
        try:
            existing = _existing(archive, target)
            if existing:
                success = {"archive": str(archive), "target_timestamp": target, "frozen_at": existing["frozen_at"]}
                atomic_json(status_path, {"state": "complete", "already_complete": True,
                    "target_timestamp": target, "archive": str(archive), "last_success": success,
                    "checked_at": int(time.time()), "production_writes": False})
                return {**existing, "archive": str(archive), "already_complete": True}
            rpc = rpc_client or RPC(rpc_url, timeout=30, batch_size=batch_size)
            pinned, safe = cutoff_block(rpc, target)
            number, block_hash = quantity(pinned["number"]), pinned["hash"].lower()
            # Materialize only the address keyset under a short read transaction.
            # The working collector remains free to write throughout the RPC pass.
            database = root / "state/preview.sqlite3"
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=15) as db:
                db.execute("BEGIN")
                accounts = [row[0] for row in db.execute("SELECT address FROM addresses ORDER BY address")]
            if not accounts:
                raise ChainError("Cannot freeze an empty known-address set")
            if any(address(account, allow_zero=True) != account for account in accounts):
                raise ChainError("Known-address set contains a noncanonical or invalid address")
            address_digest = hashlib.sha256(("\n".join(accounts) + "\n").encode()).hexdigest()
            staging = Path(tempfile.mkdtemp(prefix=f".{archive.name}-", dir=archives))
            gzip_path = staging / "power.jsonl.gz"
            total, positive, completed = 0, 0, 0
            top: list[tuple[str, int]] = []
            report = {"state": "running", "target_timestamp": target, "source_block": number,
                "source_block_hash": block_hash, "known_count": len(accounts), "processed_count": 0,
                "started_at": int(time.time()), "production_writes": False, "last_success": previous_success}
            atomic_json(status_path, report)
            # Keep at most workers batches in flight; failed calls do not queue
            # the remainder of the address set against a pruned/unavailable node.
            with gzip_path.open("wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as zipped:
                    with io.TextIOWrapper(zipped, encoding="utf-8", newline="\n") as out:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                            width = workers * batch_size
                            for offset in range(0, len(accounts), width):
                                wave = accounts[offset:offset + width]
                                chunks = [wave[i:i + batch_size] for i in range(0, len(wave), batch_size)]
                                results = list(pool.map(lambda chunk: rpc.users(chunk, number), chunks))
                                for chunk, users in zip(chunks, results):
                                    if set(users) != set(chunk):
                                        raise ChainError("Freeze RPC returned an incomplete or mismatched address batch")
                                    for account in chunk:
                                        user = users[account]
                                        if user.address != account:
                                            raise ChainError("Freeze RPC address identity mismatch")
                                        record = {"address": account, "power": str(user.power),
                                            "burned": str(user.burned), "total_burned": str(user.total_burned),
                                            "upline1": user.upline1, "upline2": user.upline2,
                                            "fetched_block": number}
                                        out.write(json.dumps(record, separators=(",", ":")) + "\n")
                                        total += user.power
                                        if user.power > 0:
                                            positive += 1
                                            top.append((account, user.power))
                                        completed += 1
                                top.sort(key=lambda row: (-row[1], row[0]))
                                del top[100:]
                                report.update(processed_count=completed, updated_at=int(time.time()),
                                              elapsed_seconds=round(time.monotonic() - began, 3))
                                atomic_json(status_path, report)
                raw.flush()
                os.fsync(raw.fileno())
            if completed != len(accounts):
                raise ChainError("Freeze address count changed while serializing")
            global_power = rpc.global_power(number)
            if rpc.block(number)["hash"].lower() != block_hash:
                raise ReconcileRequired("Frozen block hash changed during the address pass")
            if metric_store is None:
                from node_preview.metrics import MetricStore
                metric_store = MetricStore(database, rpc_url=rpc_url, seed_dir=state)
            seeds = metric_store.bootstrap()
            # The confirmed post-cutoff marker lets daily statistics finish
            # independently; B itself is necessarily before that marker.
            metric_store.refresh(copy.deepcopy(seeds["payload"]["meta"]), safe)
            meta = metric_store.refresh(copy.deepcopy(seeds["payload"]["meta"]),
                {"number": number, "hash": block_hash, "timestamp": quantity(pinned["timestamp"])})
            frozen_at = int(time.time())
            meta.update(network_total_power=str(global_power), generated_at=frozen_at,
                        latest_block=number, rpc_latest_block=number, candidate_count=len(accounts),
                        address_pool_count=len(accounts), ranked_count=min(100, positive), public_rank_limit=100,
                        positive_power_count=positive, full_ranked_count=positive,
                        discovered_total_power=total, ranking_type="node_verified_discovered_addresses",
                        refresh_mode="fixed_cutoff_snapshot", paid_download={"enabled": False})
            sources = copy.deepcopy(meta.get("preview_metric_sources") or {})
            sources["network_total_power"] = {"source": "local_rpc_power_contract", "block": number,
                "block_hash": block_hash, "fetched_at": frozen_at, "status": "ok"}
            meta["preview_metric_sources"] = sources
            meta["node_preview"] = {"state": "frozen", "mode": "local_archive",
                "source_block": number, "source_block_hash": block_hash,
                "source_block_timestamp": quantity(pinned["timestamp"]),
                "target_timestamp": target, "generated_at": frozen_at,
                "known_addresses": len(accounts), "calibration_done": True,
                "scope": "已发现地址集合；尚未覆盖全部链上地址",
                "ranking_source": "local_node_fixed_block", "production_writes": False,
                "version": f"frozen-{target}-{block_hash[2:14]}"}
            rows = [{"rank": rank, "address": account, "power": str(power)}
                    for rank, (account, power) in enumerate(top, 1)]
            from node_preview.frontend import public_payload
            public = public_payload({"meta": meta, "rows": rows})
            atomic_json(staging / "public.json", public)
            if rpc.block(number)["hash"].lower() != block_hash:
                raise ReconcileRequired("Frozen block hash changed before finalization")
            manifest = {"schema": 1, "state": "complete", "target_timestamp": target,
                "target_beijing": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(target + 28800)),
                "source_block": number, "source_block_hash": block_hash,
                "source_block_timestamp": quantity(pinned["timestamp"]), "safe_head_at_start": safe,
                "frozen_at": frozen_at, "known_count": len(accounts), "processed_count": completed,
                "address_set_sha256": address_digest, "complete_for_known_addresses": True,
                "full_chain_census": False, "positive_power_count": positive,
                "discovered_total_power": str(total), "network_total_power": str(global_power),
                "public_rows": len(rows), "production_writes": False,
                "daily_statistics_cutoff": public["meta"].get("statistics_window_end_timestamp"),
                "daily_statistics_status": public["meta"].get("preview_daily_status"),
                "files": {name: {"sha256": sha256(staging / name), "bytes": (staging / name).stat().st_size}
                          for name in ("public.json", "power.jsonl.gz")},
                "elapsed_seconds": round(time.monotonic() - began, 3)}
            atomic_json(staging / "manifest.json", manifest)
            for path in staging.iterdir():
                os.chmod(path, 0o600)
            _sync_directory(staging)
            # The final directory appears only after every address, hash and file
            # has passed. Existing completed targets are never replaced.
            os.rename(staging, archive)
            staging = None
            _sync_directory(archives)
            success = {"archive": str(archive), "target_timestamp": target, "frozen_at": frozen_at}
            atomic_json(status_path, {"state": "complete", "target_timestamp": target,
                "archive": str(archive), "known_count": len(accounts), "processed_count": completed,
                "source_block": number, "source_block_hash": block_hash, "last_success": success,
                "finished_at": frozen_at, "production_writes": False})
            return {**manifest, "archive": str(archive), "already_complete": False}
        except Exception as exc:
            atomic_json(status_path, {"state": "waiting" if isinstance(exc, CutoffNotReady) else "error",
                "target_timestamp": target, "error": f"{type(exc).__name__}: {exc}",
                "failed_at": int(time.time()), "last_success": previous_success,
                "production_writes": False})
            raise
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--rpc", default="http://127.0.0.1:18555")
    parser.add_argument("--at", type=int, help="UTC Unix cutoff timestamp; default current UTC midnight / Beijing 08:00")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    if (args.at is not None and args.at < 0) or not 1 <= args.workers <= 64 or not 1 <= args.batch_size <= 1000:
        parser.error("Cutoff cannot be negative; workers must be 1..64 and batch size 1..1000")
    try:
        result = freeze(args.root, args.rpc, at=args.at, workers=args.workers, batch_size=args.batch_size)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"state": "error", "error": str(exc), "production_writes": False}), flush=True)
        return 2 if isinstance(exc, CutoffNotReady) else 1


if __name__ == "__main__":
    raise SystemExit(main())
