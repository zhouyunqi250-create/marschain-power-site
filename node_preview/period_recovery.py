"""Private, resumable recovery of completed POWER burn days from local RPC logs.

This is an explicit one-off maintenance command, never a minute-publisher task.
It reads only filtered POWER burn logs and block headers, and never changes the
collector or its database.  A separate private checkpoint and an import bundle
are written.  Hashes in the receipts detect accidental corruption; they are not
independent signatures of the trusted local node's answers.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .chain import ChainError, HASH_RE, POWER_CONTRACT, RPC, quantity, topic_address, words

DAY = 86400
AUDIT_VERSION = 1
BURN_TOPICS = {
    "0xccbea4088a3b7ae9ca2d15fab9a9742a4075b4d7247768a1eecea917565aba00": "TokensBurned",
    "0xe825177798460a5386d6eb0183b3fdfda68e9be6d613a07b5486c6bee235c40d": "ChristmasTokensBurned",
}
SOURCE = "verified_power_burn_logs"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _seal(value: dict, field: str) -> dict:
    return {**value, field: _sha(value)}


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"Invalid nonnegative integer {name}")
    return value


def _amount(value: Any) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("Burn amount must be a decimal integer string")
    result = int(value)
    if result >= 2**256:
        raise ValueError("Burn amount exceeds uint256")
    return result


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise ValueError("Invalid canonical block hash")
    return value.lower()


def _receipt(value: dict, field: str) -> None:
    if not isinstance(value, dict) or value.get(field) != _sha({k: v for k, v in value.items() if k != field}):
        raise ValueError(f"Invalid {field} receipt")


def _header(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Missing block header")
    _integer(value.get("number"), "header.number")
    _integer(value.get("timestamp"), "header.timestamp")
    _hash(value.get("hash"))
    _hash(value.get("parentHash"))
    return value


def _cutoff(before: dict, marker: dict, cutoff: int) -> None:
    _header(before)
    _header(marker)
    if (before["number"] + 1 != marker["number"]
            or marker["parentHash"].lower() != before["hash"].lower()
            or not before["timestamp"] < cutoff <= marker["timestamp"]):
        raise ValueError("Cutoff is not bracketed by adjacent canonical headers")


def _crosschecks(values: Any) -> None:
    if not isinstance(values, list) or len(values) < 2:
        raise ValueError("Two recent independent exact-counter day checks are required")
    seen = set()
    for item in values:
        _receipt(item, "receipt_sha256")
        start, end = item.get("start_timestamp"), item.get("end_timestamp")
        if type(start) is not int or type(end) is not int or end - start != DAY or start % DAY or end in seen:
            raise ValueError("Invalid reference comparison window")
        seen.add(end)
        before, after = _amount(item.get("start_burned_wei")), _amount(item.get("end_burned_wei"))
        if after < before or after - before != _amount(item.get("log_amount_wei")):
            raise ValueError("Burn logs disagree with exact cumulative counter")
        if item.get("verified") is not True or item.get("canonical_headers_checked") is not True:
            raise ValueError("Unverified reference comparison")
        _hash(item.get("start_block_hash"))
        _hash(item.get("end_block_hash"))


def validate_recovered_burn_day(value: dict) -> dict:
    """Validate a private recovery record without RPC; return its stable fields.

    Only records produced by an audited local recovery run should be imported.
    Self-contained checksums provide integrity, not a third-party trust root.
    """
    if not isinstance(value, dict) or value.get("audit_version") != AUDIT_VERSION:
        raise ValueError("Unsupported burn-day audit version")
    _receipt(value, "proof_sha256")
    if (value.get("source") != SOURCE or value.get("verified") is not True
            or value.get("contract") != POWER_CONTRACT
            or value.get("topics") != sorted(BURN_TOPICS)):
        raise ValueError("Unexpected burn recovery source/filter")
    start = _integer(value.get("start_timestamp"), "start_timestamp")
    end = _integer(value.get("end_timestamp"), "end_timestamp")
    if start % DAY or end - start != DAY:
        raise ValueError("Burn recovery must cover exactly one Beijing 08:00 day")
    before, first = value.get("start_boundary"), value.get("start_marker")
    last, after = value.get("end_boundary"), value.get("end_marker")
    _cutoff(before, first, start)
    _cutoff(last, after, end)
    head = _header(value.get("confirmed_head"))
    seen_head = _header(value.get("observed_head"))
    confirmations = _integer(value.get("confirmations"), "confirmations")
    if confirmations < 12 or seen_head["number"] - head["number"] < confirmations:
        raise ValueError("Recovery head lacks required confirmation depth")
    if head["number"] < after["number"] or head["timestamp"] < end:
        raise ValueError("Recovery window extends beyond fixed confirmed head")
    if (value.get("canonical_head_verified") is not True
            or value.get("start_block") != first["number"]
            or value.get("end_block") != last["number"]
            or first["number"] > last["number"]):
        raise ValueError("Recovery block interval is incomplete")
    ranges = value.get("covered_ranges")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("No successful log-range coverage")
    cursor, amount, count = first["number"], 0, 0
    digests = []
    prior_last = None
    for item in ranges:
        _receipt(item, "receipt_sha256")
        left, right = item.get("start_block"), item.get("end_block")
        if (type(left) is not int or type(right) is not int or left != cursor or right < left
                or right > last["number"] or item.get("query_succeeded") is not True
                or item.get("contract") != POWER_CONTRACT or item.get("topics") != sorted(BURN_TOPICS)
                or item.get("canonical_event_headers_checked") is not True
                or item.get("confirmed_head_hash") != head["hash"]):
            raise ValueError("Missing or incompatible burn-log range coverage")
        left_header, right_header = _header(item.get("first_header")), _header(item.get("last_header"))
        if left_header["number"] != left or right_header["number"] != right:
            raise ValueError("Log coverage header numbers disagree")
        if prior_last is not None and left_header["parentHash"] != prior_last["hash"]:
            raise ValueError("Log coverage ranges do not join canonically")
        if prior_last is None and left_header != first:
            raise ValueError("First coverage header differs from cutoff marker")
        requests = item.get("successful_queries")
        position = left
        if not isinstance(requests, list) or not requests:
            raise ValueError("Missing successful eth_getLogs query receipts")
        for request in requests:
            if (not isinstance(request, list) or len(request) != 2 or request[0] != position
                    or type(request[1]) is not int or request[1] < position or request[1] > right):
                raise ValueError("Query receipts leave a range gap")
            position = request[1] + 1
        if position != right + 1:
            raise ValueError("Query receipts do not cover whole range")
        log_digest = item.get("log_digest")
        if not isinstance(log_digest, str) or len(log_digest) != 64 or any(c not in "0123456789abcdef" for c in log_digest):
            raise ValueError("Malformed validated-event digest")
        amount += _amount(item.get("amount_wei"))
        count += _integer(item.get("log_count"), "log_count")
        if item["log_count"] == 0 and (item["amount_wei"] != "0" or log_digest != _sha([])):
            raise ValueError("Empty log response has a nonempty amount or digest")
        digests.append([left, right, log_digest, item["receipt_sha256"]])
        cursor, prior_last = right + 1, right_header
    if cursor != last["number"] + 1 or prior_last != last:
        raise ValueError("Last coverage header differs from cutoff boundary")
    if (amount != _amount(value.get("amount_wei")) or count != _integer(value.get("log_count"), "day.log_count")
            or value.get("log_digest") != _sha(digests)):
        raise ValueError("Burn-day aggregate does not match coverage receipts")
    _crosschecks(value.get("counter_crosschecks"))
    return {"start_timestamp": start, "end_timestamp": end,
            "start_block": first["number"], "end_block": last["number"],
            "start_block_hash": first["hash"], "start_anchor_hash": before["hash"],
            "end_block_hash": last["hash"], "amount_wei": amount,
            "source": SOURCE, "proof_sha256": value["proof_sha256"]}


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(_json(value) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, path)


class BurnRecovery:
    def __init__(self, directory: Path, rpc: RPC, *, days: int = 30,
                 end: int | None = None, confirmations: int = 12,
                 chunk_blocks: int = 4096, delay: float = .1,
                 reference_database: Path):
        url = urlsplit(rpc.url)
        try:
            local = ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            local = url.hostname == "localhost"
        if url.scheme != "http" or not local or url.username or url.password:
            raise ValueError("Recovery only accepts an unauthenticated local loopback HTTP RPC")
        if not 2 <= days <= 90 or confirmations < 12 or not 1 <= chunk_blocks <= 16384 or delay < 0:
            raise ValueError("Unsafe recovery range, confirmations or resource limits")
        self.directory, self.rpc = directory, rpc
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        self.lock = (directory / "recovery.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("A recovery is already using this checkpoint") from exc
        self.db_path = directory / "recovery.sqlite3"
        self.db = sqlite3.connect(self.db_path)
        self.db.execute("CREATE TABLE IF NOT EXISTS recovery_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS recovery_boundaries (timestamp INTEGER PRIMARY KEY,value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS recovery_chunks (day_end INTEGER,start_block INTEGER,end_block INTEGER,value TEXT NOT NULL,events TEXT NOT NULL,PRIMARY KEY(day_end,start_block))")
        self.db.commit()
        os.chmod(self.db_path, 0o600)
        self.chunk_blocks, self.delay = chunk_blocks, delay
        self.reference_database = reference_database
        prior = self.db.execute("SELECT value FROM recovery_meta WHERE key='run'").fetchone()
        if prior:
            self.run = json.loads(prior[0])
            if (self.run.get("audit_version") != AUDIT_VERSION
                    or self.run.get("contract") != POWER_CONTRACT or self.run.get("topics") != sorted(BURN_TOPICS)
                    or self.run.get("confirmations") != confirmations
                    or self.run["days"] != days or (end is not None and self.run["end_timestamp"] != end)):
                raise ValueError("Checkpoint belongs to a different requested recovery window")
        else:
            observed = self.header(rpc.head())
            head = self.header(observed["number"] - confirmations)
            end = min(int(time.time()), head["timestamp"]) // DAY * DAY if end is None else end
            if end % DAY or end > head["timestamp"] or end < days * DAY:
                raise ValueError("Requested cutoff is not a completed confirmed day")
            self.run = {"audit_version": AUDIT_VERSION, "days": days,
                        "start_timestamp": end - days * DAY, "end_timestamp": end,
                        "chain_id": rpc.call("eth_chainId", []), "confirmed_head": head,
                        "observed_head": observed, "confirmations": confirmations,
                        "created_at": int(time.time()), "contract": POWER_CONTRACT,
                        "topics": sorted(BURN_TOPICS)}
            self.db.execute("INSERT INTO recovery_meta VALUES ('run',?)", (_json(self.run),))
            self.db.commit()
        self.headers: dict[int, dict] = {}
        self.verify_head()

    def header(self, number: int) -> dict:
        raw = self.rpc.block(number)
        return {"number": quantity(raw["number"]), "hash": raw["hash"].lower(),
                "parentHash": raw["parentHash"].lower(), "timestamp": quantity(raw["timestamp"])}

    def verify_head(self) -> None:
        original = self.run["confirmed_head"]
        if self.header(original["number"]) != original or self.rpc.call("eth_chainId", []) != self.run["chain_id"]:
            raise ChainError("Fixed recovery head is no longer canonical; checkpoint must be audited")

    def boundary(self, cutoff: int) -> dict:
        cached = self.db.execute("SELECT value FROM recovery_boundaries WHERE timestamp=?", (cutoff,)).fetchone()
        if cached:
            value = json.loads(cached[0])
            _cutoff(value["before"], value["marker"], cutoff)
            if any(self.header(value[k]["number"]) != value[k] for k in ("before", "marker")):
                raise ChainError("A checkpoint cutoff is no longer canonical")
            return value
        low, high = 0, self.run["confirmed_head"]["number"]
        if self.header(low)["timestamp"] >= cutoff or self.header(high)["timestamp"] < cutoff:
            raise ChainError("Node headers do not span requested cutoff")
        while low + 1 < high:
            middle = (low + high) // 2
            if self.header(middle)["timestamp"] < cutoff:
                low = middle
            else:
                high = middle
        value = {"before": self.header(low), "marker": self.header(high)}
        _cutoff(value["before"], value["marker"], cutoff)
        self.verify_head()
        self.db.execute("INSERT INTO recovery_boundaries VALUES (?,?)", (cutoff, _json(value)))
        self.db.commit()
        return value

    def filtered_logs(self, start: int, end: int) -> tuple[list, list]:
        try:
            logs = self.rpc.call("eth_getLogs", [{"address": POWER_CONTRACT,
                "fromBlock": hex(start), "toBlock": hex(end), "topics": [sorted(BURN_TOPICS)]}])
            if not isinstance(logs, list):
                raise ChainError("Invalid burn-log response")
            return logs, [[start, end]]
        except ChainError as exc:
            message = str(exc).lower()
            if start >= end or not any(term in message for term in (
                    "too many results", "response size", "limit exceeded", "block range", "exceeds", "-32005")):
                raise
            middle = (start + end) // 2
            left, lproof = self.filtered_logs(start, middle)
            right, rproof = self.filtered_logs(middle + 1, end)
            return left + right, lproof + rproof

    def segment(self, start: int, end: int) -> tuple[dict, list]:
        self.verify_head()
        logs, queries = self.filtered_logs(start, end)
        events = {}
        for log in logs:
            if not isinstance(log, dict) or log.get("address", "").lower() != POWER_CONTRACT or log.get("removed") is not False:
                raise ChainError("Wrong-contract or removed burn log")
            topics = log.get("topics")
            if not isinstance(topics, list) or not topics or str(topics[0]).lower() not in BURN_TOPICS:
                raise ChainError("Unrequested event returned in burn-log range")
            name = BURN_TOPICS[topics[0].lower()]
            if len(topics) != (2 if name == "TokensBurned" else 3) or not topic_address(topics[1]):
                raise ChainError("Wrong burn-event ABI topics")
            if any(not HASH_RE.fullmatch(str(topic)) for topic in topics):
                raise ChainError("Malformed burn-event indexed ABI value")
            data = words(log.get("data"), 2)
            block, index = quantity(log.get("blockNumber")), quantity(log.get("logIndex"))
            tx_hash, block_hash = _hash(log.get("transactionHash")), _hash(log.get("blockHash"))
            if not start <= block <= end:
                raise ChainError("Burn event lies outside requested range")
            event = {"block": block, "block_hash": block_hash, "tx_hash": tx_hash,
                     "transaction_index": quantity(log.get("transactionIndex")), "log_index": index,
                     "topics": [topic.lower() for topic in topics], "data": log["data"].lower(),
                     "amount_wei": str(data[0])}
            identity = (tx_hash, index)
            if identity in events and events[identity] != event:
                raise ChainError("Conflicting duplicate burn-event identity")
            events[identity] = event
        ordered = sorted(events.values(), key=lambda row: (row["block"], row["log_index"]))
        # A canonical block has only one event at each global log index.
        if len({(item["block"], item["log_index"]) for item in ordered}) != len(ordered):
            raise ChainError("Duplicate canonical block/log index")
        numbers = sorted({start, end, *(item["block"] for item in ordered)})
        raw_headers = self.rpc.batch([("eth_getBlockByNumber", [hex(number), False]) for number in numbers])
        headers = {}
        for number, raw in zip(numbers, raw_headers):
            self.rpc.validate_block(raw, number)
            headers[number] = {"number": number, "hash": raw["hash"].lower(), "parentHash": raw["parentHash"].lower(),
                               "timestamp": quantity(raw["timestamp"])}
        if any(headers[item["block"]]["hash"] != item["block_hash"] for item in ordered):
            raise ChainError("Burn log block hash disagrees with canonical header")
        for left, right in zip(numbers, numbers[1:]):
            if headers[right]["timestamp"] < headers[left]["timestamp"]:
                raise ChainError("Nonmonotonic canonical header timestamps")
            if right == left + 1 and headers[right]["parentHash"] != headers[left]["hash"]:
                raise ChainError("Adjacent event headers do not join")
        self.verify_head()
        receipt = _seal({"start_block": start, "end_block": end, "contract": POWER_CONTRACT,
            "topics": sorted(BURN_TOPICS), "successful_queries": queries, "query_succeeded": True,
            "canonical_event_headers_checked": True, "confirmed_head_hash": self.run["confirmed_head"]["hash"],
            "first_header": headers[start], "last_header": headers[end], "log_count": len(ordered),
            "amount_wei": str(sum(int(item["amount_wei"]) for item in ordered)), "log_digest": _sha(ordered),
            "checked_at": int(time.time())}, "receipt_sha256")
        return receipt, ordered

    def day(self, end: int, completed: int) -> dict:
        start = end - DAY
        before, after = self.boundary(start), self.boundary(end)
        first, last = before["marker"]["number"], after["before"]["number"]
        if first > last:
            raise ChainError("No confirmed block interval for the requested day")
        existing = {row[0]: (row[1], json.loads(row[2]), json.loads(row[3])) for row in self.db.execute(
            "SELECT start_block,end_block,value,events FROM recovery_chunks WHERE day_end=? ORDER BY start_block", (end,))}
        receipts, cursor = [], first
        while cursor <= last:
            right = min(cursor + self.chunk_blocks - 1, last)
            cached = existing.get(cursor)
            if cached:
                if cached[0] > last or cached[0] < cursor:
                    raise ChainError("Cached recovery segment has incompatible bounds")
                receipt, right = cached[1], cached[0]
                _receipt(receipt, "receipt_sha256")
                if (receipt.get("start_block") != cursor or receipt.get("end_block") != right
                        or receipt.get("log_digest") != _sha(cached[2])
                        or receipt.get("log_count") != len(cached[2])
                        or _amount(receipt.get("amount_wei")) != sum(_amount(item.get("amount_wei")) for item in cached[2])):
                    raise ChainError("Saved event audit does not match cached log receipt")
                for key in ("first_header", "last_header"):
                    if self.header(receipt[key]["number"]) != receipt[key]:
                        raise ChainError("Cached log segment is no longer canonical")
            else:
                receipt, events = self.segment(cursor, right)
                self.db.execute("INSERT INTO recovery_chunks VALUES (?,?,?,?,?)", (end, cursor, right, _json(receipt), _json(events)))
                self.db.commit()
                time.sleep(self.delay)
            receipts.append(receipt)
            cursor = right + 1
            _atomic(self.directory / "status.json", {"state": "running", "day_end": end,
                "days_complete": completed, "days_total": self.run["days"], "last_block": right,
                "target_last_block": last, "updated_at": int(time.time())})
        self.verify_head()
        return {"audit_version": AUDIT_VERSION, "source": SOURCE, "verified": True,
                "contract": POWER_CONTRACT, "topics": sorted(BURN_TOPICS),
                "start_timestamp": start, "end_timestamp": end, "start_block": first, "end_block": last,
                "start_boundary": before["before"], "start_marker": before["marker"],
                "end_boundary": after["before"], "end_marker": after["marker"],
                "confirmed_head": self.run["confirmed_head"], "observed_head": self.run["observed_head"],
                "confirmations": self.run["confirmations"], "canonical_head_verified": True,
                "covered_ranges": receipts, "amount_wei": str(sum(int(item["amount_wei"]) for item in receipts)),
                "log_count": sum(item["log_count"] for item in receipts),
                "log_digest": _sha([[item["start_block"], item["end_block"], item["log_digest"], item["receipt_sha256"]] for item in receipts])}

    def crosschecks(self, days: list[dict]) -> list[dict]:
        if not self.reference_database.is_file():
            raise ValueError("Required exact-counter reference database is missing")
        with sqlite3.connect(self.reference_database.resolve().as_uri() + "?mode=ro", uri=True) as reference:
            reference.row_factory = sqlite3.Row
            rows = reference.execute("SELECT timestamp,block_number,block_hash,burned FROM preview_metric_boundaries WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
                (self.run["start_timestamp"], self.run["end_timestamp"])).fetchall()
        boundaries = {row["timestamp"]: dict(row) for row in rows}
        comparisons = []
        for item in days:
            start, end = item["start_timestamp"], item["end_timestamp"]
            if start not in boundaries or end not in boundaries:
                continue
            left, right = boundaries[start], boundaries[end]
            for row, recovered in ((left, item["start_boundary"]), (right, item["end_boundary"])):
                if (row["block_number"] != recovered["number"] or str(row["block_hash"]).lower() != recovered["hash"]
                        or self.header(row["block_number"]) != recovered):
                    raise ChainError("Stored exact-counter reference is not at recovered canonical cutoff")
            amount = _amount(str(right["burned"])) - _amount(str(left["burned"]))
            if amount < 0 or amount != int(item["amount_wei"]):
                raise ChainError(f"Burn-log/cumulative-counter mismatch for day ending {end}; no bundle published")
            comparisons.append(_seal({"start_timestamp": start, "end_timestamp": end,
                "start_block_hash": left["block_hash"].lower(), "end_block_hash": right["block_hash"].lower(),
                "start_burned_wei": str(left["burned"]), "end_burned_wei": str(right["burned"]),
                "log_amount_wei": item["amount_wei"], "canonical_headers_checked": True,
                "verified": True}, "receipt_sha256"))
        _crosschecks(comparisons)
        return comparisons

    def recover(self) -> dict:
        all_days = [self.day(end, index) for index, end in enumerate(range(
            self.run["start_timestamp"] + DAY, self.run["end_timestamp"] + DAY, DAY))]
        checks = self.crosschecks(all_days)
        states = {}
        for item in all_days:
            item = _seal({**item, "counter_crosschecks": checks}, "proof_sha256")
            validate_recovered_burn_day(item)
            states[f"period_burn_day:{item['end_timestamp']}"] = item
        self.verify_head()
        bundle = _seal({"audit_version": AUDIT_VERSION, "kind": "private_power_burn_recovery_bundle",
            "verified": True, "created_at": int(time.time()), "run": self.run,
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "counter_crosschecks": checks, "metric_state": states}, "bundle_sha256")
        _atomic(self.directory / "bundle.json", bundle)
        _atomic(self.directory / "status.json", {"state": "complete", "days_complete": len(all_days),
            "days_total": self.run["days"], "bundle_sha256": bundle["bundle_sha256"],
            "log_count": sum(item["log_count"] for item in all_days), "updated_at": int(time.time())})
        return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="Dedicated private checkpoint/output directory")
    parser.add_argument("--reference-database", type=Path, required=True, help="Existing exact cutoff totals, opened read-only")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:18555")
    parser.add_argument("--end-timestamp", type=int)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--confirmations", type=int, default=12)
    parser.add_argument("--chunk-blocks", type=int, default=4096)
    parser.add_argument("--delay", type=float, default=.1)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        recovery = BurnRecovery(args.directory, RPC(args.rpc_url, timeout=30, batch_size=100),
            days=args.days, end=args.end_timestamp, confirmations=args.confirmations,
            chunk_blocks=args.chunk_blocks, delay=args.delay, reference_database=args.reference_database)
        bundle = recovery.recover()
        print(_json({"state": "complete", "days": len(bundle["metric_state"]), "bundle_sha256": bundle["bundle_sha256"]}), flush=True)
    except Exception as exc:
        _atomic(args.directory / "status.json", {"state": "failed", "error": str(exc), "updated_at": int(time.time())})
        raise


if __name__ == "__main__":
    main()
