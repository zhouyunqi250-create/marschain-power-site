"""Collect and retain independent public KTX and LBank spot market snapshots."""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
from http.client import HTTPException
import json
from pathlib import Path
import re
import stat
from time import monotonic
from urllib.request import HTTPRedirectHandler, Request, build_opener

from node_preview.bootstrap import atomic_json


MARKETS = {
    "KTX": ("ticker", ("api.ktx.com", "api.ktx.news", "api.ktx.exchange"),
            "/api/v1/ticker?market=spot&symbol=MARS_USDT"),
    "LBank": ("last_trade", ("api.lbank.info", "api.lbkex.com"),
              "/v2/ticker/24hr.do?symbol=marschain_usdt"),
}
RETRY_HOSTS = {"KTX": "api.ktx.exchange", "LBank": "api.lbkex.com"}
REQUEST_TIMEOUT = 6
COLLECTION_TIMEOUT = 10
MAX_BODY_BYTES = 65536
MAX_SNAPSHOT_BYTES = 8192
SNAPSHOT_FILE = "exchange-quotes.json"
STALE_AFTER = timedelta(minutes=3)
MAX_CLOCK_SKEW = timedelta(minutes=1)
DECIMAL_TEXT = re.compile(r"[0-9]{1,24}(?:\.[0-9]{1,24})?\Z")
MILLISECONDS = re.compile(r"[0-9]{13}\Z")
UTC_TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00\Z", re.ASCII)
AMOUNT_FIELDS = ("price_usdt", "base_volume_24h", "quote_turnover_24h_usdt")
PUBLIC_QUOTE_FIELDS = frozenset({"exchange", "symbol", "status", "source_at", "received_at",
                                 "source_time_kind", *AMOUNT_FIELDS})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _amount(value: object, *, positive: bool = False) -> str | None:
    if not isinstance(value, (str, Decimal)) and type(value) is not int:
        return None
    raw = str(value)
    if not DECIMAL_TEXT.fullmatch(raw):
        return None
    amount = Decimal(raw)
    if positive and amount == 0:
        return None
    text = format(amount, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _market_row(payload: dict, status_key: str, rows_key: str) -> dict:
    status = payload.get(status_key)
    rows = payload.get(rows_key)
    if type(status) is not int or status != 0 or not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("Unexpected market response")
    if not isinstance(rows[0], dict):
        raise ValueError("Missing market row")
    return rows[0]


def _normalize(exchange: str, host: str, payload: object, received: datetime) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Expected a market response object")
    received = received.replace(microsecond=received.microsecond // 1000 * 1000)
    if exchange == "KTX":
        row = _market_row(payload, "state", "result")
        if (row.get("product") != "MARS_USDT" or row.get("base", "MARS") != "MARS"
                or row.get("quote", "USDT") != "USDT"):
            raise ValueError("Unexpected KTX trading pair")
        last = row.get("last", row.get("Last"))
        volume, turnover, stamp = row.get("volume"), row.get("amount"), row.get("time")
    else:
        row = _market_row(payload, "error_code", "data")
        ticker = row.get("ticker")
        if row.get("symbol") != "marschain_usdt" or not isinstance(ticker, dict):
            raise ValueError("Unexpected LBank trading pair")
        last, volume, turnover = ticker.get("latest"), ticker.get("vol"), ticker.get("turnover")
        stamp = row.get("timestamp")
    price = _amount(last, positive=True)
    if price is None or not MILLISECONDS.fullmatch(str(stamp)):
        raise ValueError("Invalid market price or timestamp")
    source = datetime.fromtimestamp(int(stamp) / 1000, timezone.utc)
    if not 2010 <= source.year <= 2100 or source > received + MAX_CLOCK_SKEW:
        raise ValueError("Market timestamp is outside the supported time range")
    return {
        "exchange": exchange,
        "symbol": "MARS/USDT",
        "status": "stale" if received - source > STALE_AFTER else "ok",
        "price_usdt": price,
        "base_volume_24h": _amount(volume),
        "quote_turnover_24h_usdt": _amount(turnover),
        "source_at": source.isoformat(timespec="milliseconds"),
        "source_time_kind": MARKETS[exchange][0],
        "received_at": received.isoformat(timespec="milliseconds"),
        "source_host": host,
    }


def _fetch(exchange: str, host: str, path: str, *, timeout: float = REQUEST_TIMEOUT) -> dict | None:
    url = f"https://{host}{path}"
    request = Request(url, headers={"Accept": "application/json", "Cache-Control": "no-cache",
                                    "User-Agent": "MarsChainRank/1 market quote check"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if response.status != 200 or response.geturl() != url:
                return None
            body = response.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            return None
        received = datetime.now(timezone.utc)
        return _normalize(exchange, host, json.loads(body, parse_float=Decimal), received)
    except (OSError, ValueError, OverflowError, RecursionError, HTTPException):
        return None


def _unavailable(exchange: str) -> dict:
    return {"exchange": exchange, "symbol": "MARS/USDT", "status": "unavailable",
            "price_usdt": None, "base_volume_24h": None, "quote_turnover_24h_usdt": None,
            "source_at": None, "source_time_kind": MARKETS[exchange][0],
            "received_at": None, "source_host": None}


def collect_quotes() -> dict:
    """Check the documented endpoints concurrently and keep each market separate."""
    checked_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    deadline = monotonic() + COLLECTION_TIMEOUT
    targets = [(exchange, host, path) for exchange, (_, hosts, path) in MARKETS.items() for host in hosts]
    observations: dict[str, list[dict]] = {exchange: [] for exchange in MARKETS}
    can_retry = set(MARKETS)
    pool = ThreadPoolExecutor(max_workers=len(targets))
    try:
        pending = {pool.submit(_fetch, *target): target[0] for target in targets}
        while pending:
            complete, _ = wait(pending, timeout=max(0, deadline - monotonic()), return_when=FIRST_COMPLETED)
            if not complete:
                break
            for future in complete:
                exchange = pending.pop(future)
                quote = future.result()
                if quote is not None:
                    observations[exchange].append(quote)
            remaining = deadline - monotonic()
            if remaining >= 2:
                for exchange in can_retry.difference(pending.values()):
                    if not observations[exchange]:
                        path = MARKETS[exchange][2]
                        future = pool.submit(_fetch, exchange, RETRY_HOSTS[exchange], path,
                                             timeout=min(REQUEST_TIMEOUT, remaining))
                        pending[future] = exchange
                        can_retry.remove(exchange)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    quotes = []
    for exchange in MARKETS:
        best = max(observations[exchange], default=None, key=lambda item: (
            item["status"] == "ok", sum(item[key] is not None for key in AMOUNT_FIELDS), item["source_at"],
            -MARKETS[exchange][1].index(item["source_host"])))
        quotes.append(best or _unavailable(exchange))
    return {"checked_at": checked_at, "quotes": quotes}


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        raise ValueError("Invalid exchange snapshot timestamp")
    timestamp = datetime.fromisoformat(value)
    if timestamp.year < 2010 or timestamp.year > 2100:
        raise ValueError("Exchange snapshot timestamp is outside the supported time range")
    return timestamp


def validate_public_snapshot(value: object, *, now: datetime | None = None) -> dict:
    """Validate the exact public schema before loading or publishing a snapshot."""
    if (not isinstance(value, dict) or set(value) != {"schema", "checked_at", "quotes"}
            or type(value["schema"]) is not int or value["schema"] != 1):
        raise ValueError("Invalid exchange snapshot envelope")
    checked_at = _timestamp(value["checked_at"])
    current = datetime.now(timezone.utc) if now is None else now
    if checked_at > current + MAX_CLOCK_SKEW:
        raise ValueError("Exchange collection time is in the future")
    quotes = value["quotes"]
    if not isinstance(quotes, list) or len(quotes) != len(MARKETS):
        raise ValueError("Invalid exchange snapshot markets")
    for exchange, quote in zip(MARKETS, quotes):
        if (not isinstance(quote, dict) or set(quote) != PUBLIC_QUOTE_FIELDS
                or quote["exchange"] != exchange or quote["symbol"] != "MARS/USDT"
                or quote["source_time_kind"] != MARKETS[exchange][0]):
            raise ValueError("Invalid exchange quote identity")
        status = quote["status"]
        if status == "unavailable":
            if any(quote[key] is not None for key in (*AMOUNT_FIELDS, "source_at", "received_at")):
                raise ValueError("Unavailable exchange quote carries prices or times")
            continue
        if status not in ("ok", "stale", "retained"):
            raise ValueError("Invalid exchange quote status")
        source = _timestamp(quote["source_at"])
        received = _timestamp(quote["received_at"])
        if (received > min(current, checked_at + timedelta(seconds=COLLECTION_TIMEOUT)) + MAX_CLOCK_SKEW
                or source > received + MAX_CLOCK_SKEW):
            raise ValueError("Exchange quote clocks are inconsistent")
        if status != "retained" and (received < checked_at or (status == "stale") != (received - source > STALE_AFTER)):
            raise ValueError("Exchange quote age and status disagree")
        for key in AMOUNT_FIELDS:
            amount = quote[key]
            price = key == "price_usdt"
            if amount is None and not price:
                continue
            if not isinstance(amount, str) or amount != _amount(amount, positive=price):
                raise ValueError("Invalid exchange quote amount")
    return {"schema": 1, "checked_at": value["checked_at"], "quotes": [dict(quote) for quote in quotes]}


def load_public_snapshot(root: Path) -> dict | None:
    path = root / "state" / SNAPSHOT_FILE
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_SNAPSHOT_BYTES:
            return None
        with path.open("rb") as file:
            data = file.read(MAX_SNAPSHOT_BYTES + 1)
        if len(data) <= MAX_SNAPSHOT_BYTES:
            return validate_public_snapshot(json.loads(data))
    except (OSError, ValueError, RecursionError):
        pass
    return None


def update_quotes(root: Path) -> dict:
    """Write one collection result while preserving each venue's last valid receipt."""
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "exchange-quotes.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = load_public_snapshot(root)
        result = collect_quotes()
        if previous and result["checked_at"] < previous["checked_at"]:
            return previous
        prior_quotes = {quote["exchange"]: quote for quote in previous["quotes"]} if previous else {}
        quotes = []
        for item in result["quotes"]:
            quote = {key: item[key] for key in PUBLIC_QUOTE_FIELDS}
            prior = prior_quotes.get(quote["exchange"])
            if prior and prior["source_at"] and (quote["source_at"] is None or quote["source_at"] < prior["source_at"]):
                quote = {**prior, "status": "retained"}
            quotes.append(quote)
        snapshot = validate_public_snapshot({"schema": 1, "checked_at": result["checked_at"], "quotes": quotes})
        atomic_json(state / SNAPSHOT_FILE, snapshot)
        return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--once", action="store_true", required=True, help="Run one collection and exit")
    args = parser.parse_args()
    try:
        snapshot = update_quotes(args.root.expanduser().resolve())
    except BlockingIOError:
        print(json.dumps({"state": "busy"}))
        return
    markets = {
        quote["exchange"]: {key: quote[key] for key in ("status", "source_at", "received_at")}
        for quote in snapshot["quotes"]
    }
    print(json.dumps({"checked_at": snapshot["checked_at"], "markets": markets}), flush=True)


if __name__ == "__main__":
    main()
