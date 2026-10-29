"""Direct, certificate-verified IPv4 routing for two official read-only GETs.

The caller owns the two-attempt retry policy. This transport performs exactly
one HTTP attempt per fetch and never changes global DNS, sockets, or proxies.
"""
from __future__ import annotations

from functools import partial
import http.client
import json
import socket
import ssl
import urllib.request
from urllib.parse import urlsplit


OFFICIAL_HOST = "explorer.marschain.net"
OFFICIAL_PATHS = frozenset(("/api/stats", "/api/power/stats"))


def direct_official_route_allowed() -> bool:
    """Respect an active HTTPS proxy rather than silently bypassing it."""
    proxy = urllib.request.getproxies().get("https")
    return not proxy or urllib.request.proxy_bypass(OFFICIAL_HOST)


class _PinnedOfficialHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *args, pinned_ipv4: str, **kwargs):
        super().__init__(host, *args, **kwargs)

        def connect_ipv4(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                         source_address=None, **_options):
            # Only the TCP destination is selected. self.host stays the original
            # domain, so HTTPSConnection.connect retains its domain SNI and
            # certificate hostname verification.
            return socket.create_connection((pinned_ipv4, address[1]), timeout, source_address)

        self._create_connection = connect_ipv4


class _PinnedOfficialHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, ipv4: str):
        super().__init__()
        self.ipv4 = ipv4

    def https_open(self, request):
        connection = partial(_PinnedOfficialHTTPSConnection, pinned_ipv4=self.ipv4)
        return self.do_open(connection, request, context=ssl.create_default_context())


class _NoOfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        # A redirect must not add hidden HTTP attempts or move this narrowly
        # scoped route to another URL/host.
        return None


class OfficialGetRoute:
    """Resolve IPv4 once; attempt 1 selects a different address when available."""

    def __init__(self, url: str):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != OFFICIAL_HOST
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or parsed.path not in OFFICIAL_PATHS or parsed.query or parsed.fragment):
            raise ValueError("Not an allowlisted official GET")
        if not direct_official_route_allowed():
            raise ValueError("The official request uses the configured HTTPS proxy")
        self.url = url
        answers = socket.getaddrinfo(OFFICIAL_HOST, 443, socket.AF_INET, socket.SOCK_STREAM)
        self.ipv4 = tuple(dict.fromkeys(answer[4][0] for answer in answers if answer[0] == socket.AF_INET))
        if not self.ipv4:
            raise OSError("No official IPv4 route is available")

    def fetch(self, timeout: float, attempt: int):
        if type(attempt) is not int or attempt not in (0, 1):
            raise ValueError("Official GET permits only two caller-owned attempts")
        timeout = min(float(timeout), 10.0)
        if not 0 < timeout <= 10:
            raise ValueError("Invalid official GET socket timeout")
        ipv4 = self.ipv4[attempt % len(self.ipv4)]
        request = urllib.request.Request(self.url, headers={
            "Accept": "application/json", "User-Agent": "MarsChain-node-preview/1",
            "Cache-Control": "no-cache",
        }, method="GET")
        # This route is selected only when ordinary urllib would not use a
        # proxy (or explicitly bypasses it). No system/environment setting is
        # changed, and a configured non-bypassed proxy stays on the old path.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _PinnedOfficialHTTPSHandler(ipv4), _NoOfficialRedirect(),
        )
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
