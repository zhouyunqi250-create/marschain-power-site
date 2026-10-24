"""Freeze all known address powers at a single confirmed Beijing 08:00 cutoff.

Archives remain private, outside the web document root. This module never
uploads files, writes production, queries balances or modifies working powers.
"""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import copy
import fcntl
import gzip
import hashlib
import io
import json
import math
import os
import re
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


DAY = 86400
DAILY_NAME = re.compile(r"\d{8}-0800\Z")


def daily_inventory(root: Path | str, *, now: int | None = None, grace_seconds: int = 1800) -> dict:
    """Describe expected dates without treating absent archives as success.

    The first scheduled run is a durable coverage anchor. Existing archives can
    extend that anchor backwards, but a new installation does not invent a duty
    to recreate dates before it existed. Grace applies only to today's cutoff.
    This function is read-only and does not query the chain or any remote store.
    """
    root = Path(root)
    now = int(time.time()) if now is None else int(now)
    if now < 0 or not 0 <= grace_seconds <= 21600:
        raise ValueError("Daily generation grace must be 0..21600 seconds")
    current = now // DAY * DAY
    completed, invalid, anchors = set(), [], []
    archives = root / "archives"
    for path in sorted(archives.iterdir()) if archives.exists() else []:
        if not DAILY_NAME.fullmatch(path.name):
            continue
        try:
            target = calendar.timegm(time.strptime(path.name[:8], "%Y%m%d"))
            if target > current:
                raise ValueError("A future date is not a completed daily obligation")
            anchors.append(target)
            if path.is_symlink() or not path.is_dir():
                raise ValueError("Archive path is not a regular directory")
            manifest = json.loads((path / "manifest.json").read_text())
            if (not isinstance(manifest, dict)
                    or manifest.get("target_timestamp") != target or manifest.get("state") != "complete"
                    or manifest.get("complete_for_known_addresses") is not True
                    or not all((path / name).is_file() and not (path / name).is_symlink()
                               for name in ("public.json", "power.jsonl.gz"))):
                raise ValueError("Archive manifest or required files are incomplete")
            completed.add(target)
        except (OSError, ValueError, TypeError):
            invalid.append(path.name)
    schedule = root / "state/daily-freeze-status.json"
    try:
        first = json.loads(schedule.read_text()).get("first_target_timestamp")
        if type(first) is int and 0 <= first <= current and first % DAY == 0:
            anchors.append(first)
    except (OSError, ValueError, AttributeError):
        pass
    first = min([current, *anchors])
    missing = [target for target in range(first, current + 1, DAY) if target not in completed]
    within_grace = now < current + grace_seconds
    return {"first_target_timestamp": first, "expected_target_timestamp": current,
            "expected_archive": archive_name(current), "grace_until": current + grace_seconds,
            "within_grace": within_grace, "latest_complete_target": max(completed, default=None),
            "expected_local_complete": current in completed, "invalid_archives": invalid,
            "missing_targets": missing,
            "overdue_missing_targets": [target for target in missing if target != current or not within_grace]}


def _ranking_metadata(meta: dict, *, total: int, global_power: int, positive: int,
                      known: int, block: int, generated_at: int) -> None:
    """Replace all ranking-derived seed values with this frozen block's values."""
    try:
        target = float(meta.get("coverage_target", .8))
    except (TypeError, ValueError):
        target = .8
    if not math.isfinite(target) or not 0 <= target <= 1:
        target = .8
    coverage = total / global_power if global_power > 0 else None
    meta.update(network_total_power=str(global_power), discovered_total_power=total,
                discovered_power_coverage=coverage, coverage_target=target,
                target_met=coverage is not None and coverage >= target,
                uncovered_power=max(0, global_power - total), positive_ratio=positive / known if known else None,
                positive_power_count=positive, full_ranked_count=positive,
                candidate_count=known, address_pool_count=known, ranked_count=min(100, positive),
                oldest_checked_block=block, last_checked_block=block,
                generated_at=generated_at,
                generated_at_local=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(generated_at + 28800)))


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
            # A full/pruned node may have lost this cutoff's state during an
            # outage. Probe one account before starting the full-address pass.
            probe = rpc.users(accounts[:1], number)
            if set(probe) != set(accounts[:1]):
                raise ChainError("Fixed-cutoff state preflight returned an incomplete account")
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
            _ranking_metadata(meta, total=total, global_power=global_power, positive=positive,
                              known=len(accounts), block=number, generated_at=frozen_at)
            meta.update(latest_block=number, rpc_latest_block=number, public_rank_limit=100,
                        ranking_type="node_verified_discovered_addresses",
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
            # Seed metadata may describe another block. Sources and counts above
            # now belong to the same immutable address pass, including coverage.
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


def _historical_state_unavailable(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in (
        "missing trie node", "historical state unavailable", "historical state is not available",
        "required historical state unavailable", "state is pruned", "state has been pruned",
        "state not available for block",
    ))


def reconcile_daily(root: Path | str, rpc_url: str = "http://127.0.0.1:18555", *,
                    max_snapshots: int = 1, grace_seconds: int = 1800,
                    workers: int = 4, batch_size: int = 100) -> dict:
    """Retry the current cutoff with an explicit inventory of missing dates.

    Earlier dates require historical recovery rather than querying today's
    official price, circulation or address figures under an older archive name.
    This scheduler does not restart the user's paused historical scan/replay.
    A proven unavailable state is cooled down rather than repeatedly scanning
    every account. Generic RPC
    failures remain eligible on the next timer run. No latest-state substitute is
    ever written under an old cutoff's archive name.
    """
    if not 1 <= max_snapshots <= 7:
        raise ValueError("Daily catch-up limit must be 1..7 snapshots")
    root = Path(root).expanduser().resolve()
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    schedule_path = state / "daily-freeze-status.json"
    with (state / "daily-freeze.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "busy", "production_writes": False}
        now = int(time.time())
        try:
            previous = json.loads(schedule_path.read_text())
        except (OSError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        plan = daily_inventory(root, now=now, grace_seconds=grace_seconds)
        archive_results = previous.get("archive_results", {})
        if not isinstance(archive_results, dict):
            archive_results = {}
        report = {"state": "running", **plan, "started_at": now, "archive_results": archive_results,
                  "production_writes": False}
        # Persist the first required date before doing RPC work or a shutdown.
        atomic_json(schedule_path, report)
        missing = plan["missing_targets"]
        ordered = sorted(missing, reverse=True)
        attempted = 0
        for target in ordered:
            name = archive_name(target)
            if target < plan["expected_target_timestamp"]:
                archive_results[name] = {
                    "state": "requires_historical_recovery", "checked_at": now,
                    "detail": "Missing original fixed-cutoff snapshot. Historical official data and address scope must be validated before a rebuild; no full-address query or current-value substitution was attempted.",
                    "automatic_replay_started": False, "latest_state_substitution_allowed": False,
                }
                continue
            old = archive_results.get(name, {})
            if (old.get("state") == "unavailable_historical_state"
                    and int(old.get("retry_after") or 0) > now):
                continue
            if attempted >= max_snapshots:
                break
            attempted += 1
            try:
                result = freeze(root, rpc_url, at=target, workers=workers, batch_size=batch_size)
                if result.get("state") == "busy":
                    entry = {"state": "busy", "checked_at": int(time.time())}
                else:
                    entry = {"state": "complete", "checked_at": int(time.time()),
                             "source_block": result["source_block"], "frozen_at": result["frozen_at"]}
            except Exception as exc:
                unavailable = _historical_state_unavailable(exc)
                entry = {"state": "unavailable_historical_state" if unavailable else
                                   "waiting_for_confirmations" if isinstance(exc, CutoffNotReady) else "error",
                         "checked_at": int(time.time()), "detail": str(exc)[:500],
                         "latest_state_substitution_allowed": False}
                if unavailable:
                    entry.update(retry_after=int(time.time()) + DAY,
                                 recovery="Restore historical state or an original fixed-cutoff archive; this gap remains missing")
            archive_results[name] = entry
            atomic_json(schedule_path, {**report, "archive_results": archive_results})
        current = daily_inventory(root, grace_seconds=grace_seconds)
        missing_names = {archive_name(target) for target in current["missing_targets"]}
        unresolved = {name: entry for name, entry in archive_results.items() if name in missing_names}
        if current["overdue_missing_targets"]:
            result_state = "missing_daily_snapshot"
        elif current["missing_targets"]:
            result_state = "waiting_for_daily_snapshot"
        else:
            result_state = "complete"
        report = {**report, **current, "state": result_state, "archive_results": unresolved,
                  "attempted_snapshots": attempted, "checked_at": int(time.time())}
        atomic_json(schedule_path, report)
        # Existing status consumers see gaps, rather than the final (possibly
        # older) freeze attempt masking them with a simple complete flag.
        latest = current["latest_complete_target"]
        success = None
        if latest is not None:
            path = root / "archives" / archive_name(latest)
            manifest = json.loads((path / "manifest.json").read_text())
            success = {"archive": str(path), "target_timestamp": latest, "frozen_at": manifest["frozen_at"]}
        atomic_json(state / "freezer-status.json", {
            **report, "target_timestamp": current["expected_target_timestamp"], "last_success": success,
            "daily_schedule": True,
        })
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--rpc", default="http://127.0.0.1:18555")
    parser.add_argument("--at", type=int, help="UTC Unix cutoff timestamp; default current UTC midnight / Beijing 08:00")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-snapshots", type=int, default=1, help="Bound automatic freeze attempts; historical gaps are reported")
    parser.add_argument("--grace-seconds", type=int, default=1800, help="Generation allowance after Beijing 08:00")
    args = parser.parse_args(argv)
    if (args.at is not None and args.at < 0) or not 1 <= args.workers <= 64 or not 1 <= args.batch_size <= 1000:
        parser.error("Cutoff cannot be negative; workers must be 1..64 and batch size 1..1000")
    try:
        if args.at is not None:
            if args.at < int(time.time()) // DAY * DAY:
                raise ChainError("Historical archive recovery is not enabled by this CLI: restore original cutoff data before rebuilding; current official values cannot substitute for an older date")
            result = freeze(args.root, args.rpc, at=args.at, workers=args.workers, batch_size=args.batch_size)
        else:
            result = reconcile_daily(args.root, args.rpc, workers=args.workers, batch_size=args.batch_size,
                                     max_snapshots=args.max_snapshots, grace_seconds=args.grace_seconds)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 1 if result["state"] == "missing_daily_snapshot" else 0
    except Exception as exc:
        print(json.dumps({"state": "error", "error": str(exc), "production_writes": False}), flush=True)
        return 2 if isinstance(exc, CutoffNotReady) else 1


if __name__ == "__main__":
    raise SystemExit(main())
