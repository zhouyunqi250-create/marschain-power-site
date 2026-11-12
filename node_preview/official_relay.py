"""Read or publish a verified public Explorer receipt for the live metrics."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
from decimal import Decimal
import http.client
import json
from pathlib import Path
import re
import sys
import time
import urllib.request

from node_preview.bootstrap import atomic_json
from node_preview.official_homepage import fetch_daily_candle_high, valid_candle_high, valid_homepage_asset


STATS_URL = "https://explorer.marschain.net/api/stats"
RELAY_URL = (
    "https://api.github.com/repos/zhouyunqi250-create/marschain-power-site"
    "/contents/official-browser.json?ref=official-browser-cache"
)
RELAY_MAX_AGE = 30 * 60
RELAY_INTERVAL = 180
MAX_BYTES = 8192
MAX_SAFE_INTEGER = 2**53 - 1
_UINT = re.compile(r"(?:0|[1-9][0-9]{0,95})\Z")
_PRICE = re.compile(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,40})?\Z")
_RECEIPT_ERRORS = (OSError, ValueError, http.client.HTTPException)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def _read_body(file) -> dict:
    raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Explorer receipt exceeds the expected response size")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Explorer receipt must be a JSON object")
    return value


def _fetch(url: str, accept: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={
        "Accept": accept,
        "Cache-Control": "no-cache",
        "User-Agent": "MarsChain-rank/1",
    })
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("Unexpected Explorer receipt response")
        return _read_body(response)


def _positive_int(value: object) -> bool:
    return type(value) is int and 0 < value <= MAX_SAFE_INTEGER


def _price(value: object) -> bool:
    return isinstance(value, str) and _PRICE.fullmatch(value) is not None and Decimal(value) > 0


def _quantity(value: object) -> bool:
    return isinstance(value, str) and _UINT.fullmatch(value) is not None and int(value) > 0


def _stats(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Official stats payload is missing")
    fields = {
        "latestBlockNumber": _positive_int,
        "totalAddresses": _positive_int,
        "totalCirculation": _quantity,
        "currentPrice": _price,
    }
    optional = {
        "chainBlockNumber": _positive_int,
        "highestPrice": _price,
        "lowestPrice": _price,
        "totalPower": _quantity,
        "totalTransactions": _positive_int,
    }
    if any(not validate(value.get(field)) for field, validate in fields.items()):
        raise ValueError("Official stats payload is incomplete")
    return {**{field: value[field] for field in fields},
            **{field: value[field] for field, validate in optional.items() if validate(value.get(field))}}


def _timestamp(value: dict, now: int) -> int:
    stamp = value.get("fetched_at")
    if not _positive_int(stamp) or stamp > now:
        raise ValueError("Invalid Explorer receipt timestamp")
    return stamp


def _stats_receipt(value: dict, now: int) -> dict:
    return {"fetched_at": _timestamp(value, now), "payload": _stats(value.get("payload"))}


def _highest_receipt(value: dict, now: int) -> dict:
    price, asset = value.get("value"), value.get("asset")
    if not valid_candle_high(price) or not valid_homepage_asset(asset):
        raise ValueError("Official daily high or homepage asset is invalid")
    return {"fetched_at": _timestamp(value, now), "value": price, "asset": asset}


_VALIDATE = {"stats": _stats_receipt, "highest": _highest_receipt}


def _validated(receipt: dict, now: int) -> dict:
    result = {"schema": 1}
    if type(receipt.get("schema")) is not int or receipt["schema"] != 1:
        raise ValueError("Unrecognized Explorer receipt schema")
    for name, validator in _VALIDATE.items():
        entry = receipt.get(name)
        if not isinstance(entry, dict):
            continue
        try:
            result[name] = validator(entry, now)
        except ValueError:
            continue
    return result


def _merge(previous: dict, current: dict) -> dict:
    result = previous.copy()
    for name in _VALIDATE:
        new = current.get(name)
        old = previous.get(name)
        if new is None or (old is not None and new["fetched_at"] <= old["fetched_at"]):
            continue
        if name == "stats" and old is not None:
            if new["payload"]["latestBlockNumber"] < old["payload"]["latestBlockNumber"]:
                continue
        result[name] = new
    return result


class OfficialRelay:
    def __init__(self) -> None:
        self._receipt: dict = {"schema": 1}
        self._next_check = 0.0
        self.last_error: str | None = None

    def read(self, now: int, *, timeout: float = 5) -> dict:
        checked = time.monotonic()
        if checked >= self._next_check:
            self._next_check = checked + RELAY_INTERVAL
            try:
                current = _validated(_fetch(RELAY_URL, "application/vnd.github.raw+json", timeout), int(time.time()))
                if len(current) == 1:
                    raise ValueError("No verified fields in Explorer relay")
                self._receipt = _merge(self._receipt, current)
                self.last_error = None
            except _RECEIPT_ERRORS as error:
                self.last_error = str(error)
        # The publisher can persist these values with their original receipt
        # times. An expired relay record cannot masquerade as a live read.
        return {"schema": 1, **{name: copy.deepcopy(entry) for name, entry in self._receipt.items()
                               if name in _VALIDATE and 0 <= now - entry["fetched_at"] <= RELAY_MAX_AGE}}


def _capture_stats() -> dict:
    value = _fetch(STATS_URL, "application/json", 10)
    return {"fetched_at": int(time.time()), "payload": value}


def write_receipt(output: Path) -> bool:
    previous = {"schema": 1}
    try:
        with output.open("rb") as file:
            previous = _validated(_read_body(file), int(time.time()))
    except FileNotFoundError:
        pass

    current = {"schema": 1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = {"stats": pool.submit(_capture_stats), "highest": pool.submit(fetch_daily_candle_high)}
        for name, request in requests.items():
            try:
                value = request.result()
                current[name] = _VALIDATE[name](value, int(time.time()))
            except _RECEIPT_ERRORS as error:
                print(f"Official {name} collection failed: {error}", file=sys.stderr)
    if len(current) == 1:
        raise RuntimeError("Neither official source produced a verified receipt")
    latest = _merge(previous, current)
    if latest == previous:
        return False
    atomic_json(output, latest)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        updated = write_receipt(args.output)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print("Verified Explorer receipt updated" if updated else "Verified Explorer receipt unchanged")


if __name__ == "__main__":
    main()
