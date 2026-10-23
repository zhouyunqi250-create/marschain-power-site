"""Read-only, block-pinned MarsChain contract and event access (standard library).

ABI layouts are from marschain-protocol/mars_contracts PowerContractUpgradeable.sol.
No wallet keys, transactions, explorer requests or balance queries are used.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

POWER_CONTRACT = "0x0000000000000000000000000000000000001001"
ZERO = "0x" + "0" * 40
GET_USER_INFO = "0x6386c1c7"
VIEW_POWER = "0xb2dbd84e"
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}\Z")
HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")


class ChainError(RuntimeError):
    pass


class ReconcileRequired(ChainError):
    """The observed confirmed chain no longer matches the durable checkpoint."""


class BatchError(ChainError):
    def __init__(self, message: str, *, failures: dict[int, str] | None = None):
        super().__init__(message)
        self.failures = failures or {}


def address(value: Any, *, allow_zero: bool = False) -> str | None:
    if isinstance(value, str) and ADDRESS_RE.fullmatch(value):
        result = value.lower()
        if allow_zero or result != ZERO:
            return result
    return None


def quantity(value: Any) -> int:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) < 3:
        raise ChainError(f"Invalid RPC hex quantity: {value!r}")
    try:
        result = int(value, 16)
    except ValueError as exc:
        raise ChainError(f"Invalid RPC hex quantity: {value!r}") from exc
    if result < 0:
        raise ChainError("Negative RPC quantity")
    return result


def words(value: Any, count: int | None = None) -> list[int]:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ChainError("Missing ABI result")
    body = value[2:]
    if not body or len(body) % 64 or (count is not None and len(body) != count * 64):
        raise ChainError(f"Unexpected ABI result length {len(body)}; expected {count} words")
    try:
        return [int(body[i:i + 64], 16) for i in range(0, len(body), 64)]
    except ValueError as exc:
        raise ChainError("Non-hex ABI result") from exc


def word_address(value: int) -> str | None:
    if not 0 <= value < 2**160:
        raise ChainError("ABI address has nonzero padding")
    return address("0x" + f"{value:040x}")


def topic_address(value: Any) -> str | None:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise ChainError("Invalid ABI address topic")
    return word_address(int(value, 16))


@dataclass(frozen=True)
class UserInfo:
    address: str
    power: int
    burned: int
    total_burned: int
    upline1: str | None
    upline2: str | None
    last_claim_time: int
    bound_nft: int
    last_settle_day: int
    can_mint: bool

    @classmethod
    def decode(cls, account: str, value: Any) -> "UserInfo":
        data = words(value, 9)
        if data[8] not in (0, 1):
            raise ChainError("Invalid ABI boolean")
        return cls(account, data[0], data[1], data[3], word_address(data[5]),
                   word_address(data[6]), data[2], data[4], data[7], bool(data[8]))


# Signature, address topic positions, non-indexed address word positions.
# Integers (Christmas year, NFT id, node index) deliberately never become addresses.
EVENTS = {
    "TokensBurned": ("TokensBurned(address,uint256,uint256)", (1,), ()),
    "ChristmasTokensBurned": ("ChristmasTokensBurned(address,uint256,uint256,uint256)", (1,), ()),
    "PowerUpdated": ("PowerUpdated(address,uint256,uint256)", (1,), ()),
    "NFTMinted": ("NFTMinted(address,uint256)", (1,), ()),
    "NFTBurnedAndCleaned": ("NFTBurnedAndCleaned(address,uint256)", (1,), ()),
    "RelationEstablished": ("RelationEstablished(uint256,address,address,address)", (2,), (0, 1)),
    "TokensClaimed": ("TokensClaimed(address,uint256)", (1,), ()),
    "NodeAdminUpdated": ("NodeAdminUpdated(uint256,address)", (2,), ()),
    "NodeRewardClaimed": ("NodeRewardClaimed(address,uint256)", (1,), ()),
    "RewardsSettled": ("RewardsSettled(address,uint256,uint256)", (1,), ()),
    "OperatorUpdated": ("OperatorUpdated(address,address)", (1, 2), ()),
    "PowerAddedByOperator": ("PowerAddedByOperator(address,address,uint256,uint256,uint256)", (1, 2), ()),
    "RelationAndPowerInitialized": ("RelationAndPowerInitialized(address,address,address,uint256,uint256)", (1, 2, 3), ()),
}


class RPC:
    def __init__(self, url: str = "http://127.0.0.1:18555", timeout: float = 20,
                 batch_size: int = 100):
        self.url, self.timeout, self.batch_size = url, timeout, batch_size
        self._event_topics: dict[str, str] | None = None

    def _request(self, payload: Any) -> Any:
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.load(response)
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            raise ChainError(f"Node RPC request failed: {exc}") from exc

    def call(self, method: str, params: list[Any]) -> Any:
        result = self._request({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(result, dict) or result.get("id") != 1:
            raise ChainError(f"{method}: malformed RPC response")
        if result.get("error") or "result" not in result:
            raise ChainError(f"{method}: {result.get('error', 'missing result')}")
        return result["result"]

    def batch(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        results = []
        for offset in range(0, len(calls), self.batch_size):
            chunk = calls[offset:offset + self.batch_size]
            payload = [{"jsonrpc": "2.0", "id": i, "method": method, "params": params}
                       for i, (method, params) in enumerate(chunk, 1)]
            response = self._request(payload)
            if not isinstance(response, list):
                raise BatchError("Node batch response is not a list")
            by_id = {}
            for row in response:
                if not isinstance(row, dict) or row.get("id") in by_id:
                    raise BatchError("Node batch response has malformed or duplicate ids")
                by_id[row.get("id")] = row
            failures = {offset + i - 1: str(by_id.get(i, {}).get("error", "missing result"))
                        for i in range(1, len(chunk) + 1)
                        if by_id.get(i, {}).get("error") or "result" not in by_id.get(i, {})}
            if failures:
                raise BatchError(f"Node batch failed for {len(failures)} calls: {failures}", failures=failures)
            results.extend(by_id[i]["result"] for i in range(1, len(chunk) + 1))
        return results

    def head(self) -> int:
        return quantity(self.call("eth_blockNumber", []))

    def block(self, number: int, transactions: bool = False) -> dict[str, Any]:
        data = self.call("eth_getBlockByNumber", [hex(number), transactions])
        self.validate_block(data, number)
        return data

    @staticmethod
    def validate_block(data: Any, number: int) -> None:
        if not isinstance(data, dict) or quantity(data.get("number")) != number:
            raise ChainError(f"Missing or mismatched block {number}")
        if not HASH_RE.fullmatch(str(data.get("hash", ""))) or not HASH_RE.fullmatch(str(data.get("parentHash", ""))):
            raise ChainError(f"Block {number} has invalid hash")
        quantity(data.get("timestamp"))
        if not isinstance(data.get("transactions"), list):
            raise ChainError(f"Block {number} has invalid transactions")

    def blocks(self, start: int, end: int) -> list[dict[str, Any]]:
        results = self.batch([("eth_getBlockByNumber", [hex(number), True])
                              for number in range(start, end + 1)])
        for number, result in zip(range(start, end + 1), results):
            self.validate_block(result, number)
        for left, right in zip(results, results[1:]):
            if left["hash"].lower() != right["parentHash"].lower():
                raise ReconcileRequired("Block batch crosses inconsistent parent hashes")
        return results

    def users(self, accounts: list[str], block: int) -> dict[str, UserInfo]:
        data = self.batch([("eth_call", [{"to": POWER_CONTRACT,
            "data": GET_USER_INFO + account[2:].rjust(64, "0")}, hex(block)]) for account in accounts])
        return {account: UserInfo.decode(account, value) for account, value in zip(accounts, data)}

    def global_power(self, block: int) -> int:
        value = self.call("eth_call", [{"to": POWER_CONTRACT, "data": VIEW_POWER + "0" * 64}, hex(block)])
        return words(value, 2)[1]

    def logs(self, start: int, end: int) -> list[dict[str, Any]]:
        try:
            result = self.call("eth_getLogs", [{"address": POWER_CONTRACT,
                "fromBlock": hex(start), "toBlock": hex(end)}])
            if not isinstance(result, list):
                raise ChainError("Invalid logs response")
            return result
        except ChainError as exc:
            # Splitting a network outage can turn one timeout into hundreds.
            # Only shrink ranges when the node explicitly rejects result size.
            message = str(exc).lower()
            range_error = any(part in message for part in (
                "too many results", "more than", "response size", "limit exceeded",
                "block range", "exceeds", "-32005"))
            if start >= end or not range_error:
                raise
            midpoint = (start + end) // 2
            return self.logs(start, midpoint) + self.logs(midpoint + 1, end)

    def event_topics(self) -> dict[str, str]:
        if self._event_topics is None:
            signatures = list(EVENTS.items())
            results = self.batch([("web3_sha3", ["0x" + info[0].encode().hex()]) for _, info in signatures])
            if any(not HASH_RE.fullmatch(str(result)) for result in results):
                raise ChainError("Node could not hash ABI event signatures")
            self._event_topics = {result.lower(): name for (name, _), result in zip(signatures, results)}
        return self._event_topics

    def decode_log(self, log: dict[str, Any]) -> tuple[set[str], dict[str, Any] | None]:
        if address(log.get("address")) != POWER_CONTRACT or log.get("removed"):
            raise ReconcileRequired("Unexpected or removed POWER log")
        topics = log.get("topics")
        if not isinstance(topics, list) or not topics:
            raise ChainError("Malformed POWER log")
        name = self.event_topics().get(str(topics[0]).lower())
        if name is None:
            return set(), None
        _, positions, data_addresses = EVENTS[name]
        if len(topics) <= max(positions, default=0):
            raise ChainError(f"Incomplete {name} topics")
        data = words(log["data"]) if log.get("data") != "0x" else []
        found = {topic_address(topics[pos]) for pos in positions}
        for pos in data_addresses:
            if pos >= len(data):
                raise ChainError(f"Incomplete {name} event data")
            found.add(word_address(data[pos]))
        found.discard(None)
        user_position = 2 if name in ("RelationEstablished", "PowerAddedByOperator", "NodeAdminUpdated") else 1
        event = {"kind": name, "user_address": topic_address(topics[user_position]),
            "block_number": quantity(log["blockNumber"]), "tx_hash": log["transactionHash"].lower(),
            "log_index": quantity(log["logIndex"]), "amount_wei": None, "power_gained": None,
            "data_json": json.dumps({"topics": topics, "data": log["data"]}, separators=(",", ":"))}
        if name in ("TokensBurned", "ChristmasTokensBurned"):
            if len(data) != 2:
                raise ChainError(f"Incomplete {name} values")
            event["amount_wei"], event["power_gained"] = str(data[0]), str(data[1])
        elif name in ("TokensClaimed", "NodeRewardClaimed", "RewardsSettled"):
            if not data:
                raise ChainError(f"Incomplete {name} amount")
            event["amount_wei"] = str(data[0])
        return found, event
