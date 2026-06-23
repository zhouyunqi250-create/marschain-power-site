#!/usr/bin/env python3
"""Helpers for the lightweight MarsChain live price file."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from marschain_power_rank import BASE_URL, format_price, request_json

DEFAULT_SITE_BASE_URL = "https://www.marschainrank.com"
PRICE_SOURCE_PATH = "/stats"
PRICE_SOURCE_URL = f"{BASE_URL}{PRICE_SOURCE_PATH}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_price_text(value: object) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    if not number.is_finite():
        return text
    return format(number.normalize(), "f")


def display_price(value: object) -> str:
    return format_price(value) or normalize_price_text(value) or "待刷新"


def half_price(value: object) -> str:
    text = normalize_price_text(value)
    if not text:
        return ""
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return ""
    if not number.is_finite():
        return ""
    return format(number * Decimal("0.5"), "f")


def comparable_price(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    values = [
        payload.get("price_display") or payload.get("price"),
        payload.get("highest_price_display") or payload.get("highest_price"),
        payload.get("oracle_trigger_price_display") or payload.get("oracle_trigger_price"),
    ]
    return "|".join(str(value or "").strip() for value in values)


def price_is_unchanged(existing: object, next_payload: object) -> bool:
    return bool(comparable_price(existing)) and comparable_price(existing) == comparable_price(next_payload)


def build_price_payload(
    price: object,
    highest_price: object = None,
    existing: dict[str, Any] | None = None,
    checked_at: str | None = None,
) -> dict[str, str]:
    checked_at = checked_at or utc_now_iso()
    display = display_price(price)
    highest_display = display_price(highest_price)
    oracle_trigger = half_price(highest_price)
    oracle_display = display_price(oracle_trigger)
    payload = {
        "price": normalize_price_text(price) or display,
        "price_display": display,
        "highest_price": normalize_price_text(highest_price) or highest_display,
        "highest_price_display": highest_display,
        "oracle_trigger_price": oracle_trigger or oracle_display,
        "oracle_trigger_price_display": oracle_display,
        "oracle_trigger_formula": "highest_price * 50%",
        "checked_at": checked_at,
        "changed_at": checked_at,
        "source": "explorer",
        "source_url": PRICE_SOURCE_URL,
    }
    if existing and price_is_unchanged(existing, payload) and existing.get("changed_at"):
        payload["changed_at"] = str(existing["changed_at"])
    return payload


def build_price_payload_from_meta(meta: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, str]:
    price = meta.get("network_current_price")
    if price is None or price == "":
        price = meta.get("network_current_price_display")
    highest_price = meta.get("network_highest_price")
    if highest_price is None or highest_price == "":
        highest_price = meta.get("network_highest_price_display")
    return build_price_payload(price, highest_price, existing)


def fetch_explorer_price_payload(existing: dict[str, Any] | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    stats = request_json(PRICE_SOURCE_PATH)
    if not isinstance(stats, dict):
        raise RuntimeError("Explorer /stats did not return a JSON object.")
    if "currentPrice" not in stats:
        raise RuntimeError("Explorer /stats response did not include currentPrice.")
    return build_price_payload(stats.get("currentPrice"), stats.get("highestPrice"), existing), stats


def load_price_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_price_url(base_url: str, timeout: int = 30) -> dict[str, Any]:
    canonical = base_url.rstrip("/") + "/"
    query = urllib.parse.urlencode({"v": str(int(time.time()))})
    url = urllib.parse.urljoin(canonical, f"data/price.json?{query}")
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
