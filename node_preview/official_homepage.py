"""Read the daily-candle high displayed by the official explorer homepage."""
from __future__ import annotations

from decimal import Decimal
from html.parser import HTMLParser
import re
import time
import urllib.request


OFFICIAL_HOMEPAGE = "https://explorer.marschain.net/"
DAILY_CANDLE_BASIS = "daily_candle_high"
_ENTRY_PATH = re.compile(r"/assets/index[-.][A-Za-z0-9_.-]+\.js\Z")
_PRICE = r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,18})?"
_DISPLAYED_HIGH = re.compile(
    r"\$t\(\s*['\"]stats\.highestPrice['\"]\s*\)\s*\)\s*\+\s*"
    rf"(?P<quote>['\"]):\s*(?P<price>{_PRICE})\s*(?P=quote)"
)


class _EntryScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        src = attributes.get("src")
        if tag == "script" and attributes.get("type") == "module" and src and _ENTRY_PATH.fullmatch(src):
            self.paths.append(src)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def parse_homepage_entry(html: str) -> str:
    """Require one same-origin entry asset with the explorer's public layout."""
    scripts = _EntryScripts()
    scripts.feed(html)
    scripts.close()
    if len(scripts.paths) != 1:
        raise ValueError("Official homepage entry script could not be identified")
    return OFFICIAL_HOMEPAGE.rstrip("/") + scripts.paths[0]


def valid_candle_high(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(_PRICE, value) is not None and Decimal(value) > 0


def valid_homepage_asset(value: object) -> bool:
    return (isinstance(value, str) and value.startswith(OFFICIAL_HOMEPAGE)
            and _ENTRY_PATH.fullmatch(value[len(OFFICIAL_HOMEPAGE) - 1:]) is not None)


def parse_daily_candle_high(bundle: str) -> str:
    matches = list(_DISPLAYED_HIGH.finditer(bundle))
    if len(matches) != 1 or not valid_candle_high(matches[0]["price"]):
        raise ValueError("Official displayed daily-candle high could not be verified")
    return matches[0]["price"]


def _read(opener, url: str, accept: str, maximum: int, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"Accept": accept, "Cache-Control": "no-cache",
                                                  "User-Agent": "MarsChain-rank-official-source/1"})
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("Unexpected official homepage response")
        body = response.read(maximum + 1)
    if not body or len(body) > maximum:
        raise ValueError("Official homepage document is empty or too large")
    return body.decode("utf-8-sig")


def fetch_daily_candle_high(timeout: float = 10) -> dict:
    opener = urllib.request.build_opener(_NoRedirect())
    homepage = _read(opener, OFFICIAL_HOMEPAGE, "text/html", 128 * 1024, timeout)
    asset = parse_homepage_entry(homepage)
    script = _read(opener, asset, "application/javascript, text/javascript, */*;q=0.5", 2 * 1024 * 1024, timeout)
    return {"value": parse_daily_candle_high(script), "source": OFFICIAL_HOMEPAGE,
            "asset": asset, "fetched_at": int(time.time())}
