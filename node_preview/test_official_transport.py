"""Offline checks for per-attempt official IPv4 failover, SNI and CA checks."""
import io
import json
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request

from node_preview.metrics import EXPLORER_API, MetricStore
from node_preview.official_transport import (
    OFFICIAL_HOST, OfficialGetRoute, _PinnedOfficialHTTPSHandler,
    direct_official_route_allowed,
)
from node_preview.test_official_get_retry import QueueFetcher, STATS


FIRST = "192.0.2.10"
SECOND = "192.0.2.20"
ANSWERS = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (FIRST, 443)),
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (FIRST, 443)),
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (SECOND, 443)),
]


class OfficialTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.proxy = mock.patch("node_preview.official_transport.urllib.request.getproxies", return_value={})
        self.resolver = mock.patch("node_preview.official_transport.socket.getaddrinfo", return_value=ANSWERS)
        self.clock = mock.patch("node_preview.metrics.time.monotonic", return_value=100.0)
        self.proxy.start()
        self.dns = self.resolver.start()
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.resolver.stop()
        self.proxy.stop()
        self.temporary.cleanup()

    def store(self, name="metrics.sqlite3", **options):
        return MetricStore(Path(self.temporary.name) / name, **options)

    def opener(self, replies, calls):
        replies = list(replies)

        def build(*handlers):
            pinned = next(handler.ipv4 for handler in handlers if isinstance(handler, _PinnedOfficialHTTPSHandler))

            class Opener:
                def open(self, request, timeout):
                    calls.append({"ipv4": pinned, "url": request.full_url, "host": request.host,
                                  "method": request.get_method(), "body": request.data, "timeout": timeout})
                    if not replies:
                        raise AssertionError("Unexpected extra official HTTP attempt")
                    result = replies.pop(0)
                    if isinstance(result, Exception):
                        raise result
                    return io.BytesIO(json.dumps(result).encode())

            return Opener()

        return build

    def test_handshake_or_read_failure_switches_to_the_second_dns_ipv4(self):
        failures = [
            urllib.error.URLError(TimeoutError("The handshake operation timed out")),
            TimeoutError("The read operation timed out"),
        ]
        for index, error in enumerate(failures):
            with self.subTest(stage=str(error)):
                self.dns.reset_mock()
                calls = []
                with mock.patch("node_preview.official_transport.urllib.request.build_opener",
                                side_effect=self.opener([error, STATS], calls)):
                    payload, status = self.store(f"retry-{index}.sqlite3")._cached_external("/stats", 1000)
                self.assertEqual(payload, STATS)
                self.assertEqual(status["status"], "ok")
                self.assertEqual([call["ipv4"] for call in calls], [FIRST, SECOND])
                self.assertEqual([call["url"] for call in calls], [EXPLORER_API + "/stats"] * 2)
                self.assertTrue(all(call["host"] == OFFICIAL_HOST and call["method"] == "GET"
                                    and call["body"] is None and call["timeout"] == 10 for call in calls))
                self.dns.assert_called_once_with(OFFICIAL_HOST, 443, socket.AF_INET, socket.SOCK_STREAM)

    def test_two_route_failures_do_not_add_nested_attempts_or_fake_freshness(self):
        calls = []
        with mock.patch("node_preview.official_transport.urllib.request.build_opener",
                        side_effect=self.opener([STATS, TimeoutError("first"), TimeoutError("second")], calls)):
            store = self.store()
            original, first = store._cached_external("/stats", 1000)
            payload, status = store._cached_external("/stats", 1060)
        self.assertEqual([call["ipv4"] for call in calls], [FIRST, FIRST, SECOND])
        self.assertEqual(payload, original)
        self.assertEqual(status["status"], "stale_cache")
        self.assertEqual(status["fetched_at"], first["fetched_at"])
        self.assertEqual(status["error"], "second")

    def test_http_and_certificate_failures_do_not_switch_addresses(self):
        errors = [
            urllib.error.HTTPError(EXPLORER_API + "/stats", 429, "rate limit", None, None),
            urllib.error.HTTPError(EXPLORER_API + "/stats", 503, "unavailable", None, None),
            urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
        ]
        for index, error in enumerate(errors):
            with self.subTest(error=type(error).__name__):
                calls = []
                with mock.patch("node_preview.official_transport.urllib.request.build_opener",
                                side_effect=self.opener([error], calls)):
                    payload, status = self.store(f"no-retry-{index}.sqlite3")._cached_external("/stats", 1000)
                self.assertEqual(len(calls), 1)
                self.assertEqual(payload, {})
                self.assertEqual(status["status"], "unavailable")

    def test_pinned_tcp_destination_keeps_domain_host_sni_and_default_ca_validation(self):
        handler = _PinnedOfficialHTTPSHandler(FIRST)
        request = urllib.request.Request(EXPLORER_API + "/stats")
        with mock.patch.object(handler, "do_open", return_value=object()) as opening:
            handler.https_open(request)
        factory, original_request = opening.call_args.args
        context = opening.call_args.kwargs["context"]
        self.assertIs(original_request, request)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        connection = factory(request.host, timeout=10, context=context)
        self.assertEqual(connection.host, OFFICIAL_HOST)
        connection.putrequest("GET", "/api/stats")
        self.assertIn(b"Host: explorer.marschain.net", connection._buffer)
        tcp, tls = mock.Mock(), mock.Mock()
        with mock.patch("node_preview.official_transport.socket.create_connection", return_value=tcp) as create:
            with mock.patch.object(context, "wrap_socket", return_value=tls) as wrapping:
                connection.connect()
        create.assert_called_once_with((FIRST, 443), 10, None)
        self.assertEqual(wrapping.call_args.kwargs["server_hostname"], OFFICIAL_HOST)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_transport_has_no_redirect_or_third_attempt(self):
        calls = []
        with mock.patch("node_preview.official_transport.urllib.request.build_opener",
                        side_effect=self.opener([STATS, STATS], calls)) as opening:
            route = OfficialGetRoute(EXPLORER_API + "/stats")
            route.fetch(10, 0)
            route.fetch(10, 1)
            with self.assertRaises(ValueError):
                route.fetch(10, 2)
        self.assertEqual(len(calls), 2)
        redirect = next(handler for handler in opening.call_args.args
                        if isinstance(handler, urllib.request.HTTPRedirectHandler))
        self.assertIsNone(redirect.redirect_request(None, None, 302, "move", {}, "https://other.example/"))

    def test_existing_fetcher_injection_and_rpc_behavior_stay_unchanged(self):
        fetcher = QueueFetcher([TimeoutError("GET timeout"), STATS, TimeoutError("RPC timeout")])
        with mock.patch("node_preview.metrics.OfficialGetRoute") as route:
            store = self.store(fetcher=fetcher, timeout=60)
            payload, status = store._cached_external("/stats", 1000)
            with self.assertRaisesRegex(TimeoutError, "RPC timeout"):
                store._rpc("eth_blockNumber", [])
        route.assert_not_called()
        self.assertEqual(payload, STATS)
        self.assertEqual(status["status"], "ok")
        self.assertEqual([call[1] for call in fetcher.calls], [10, 10, 60])
        self.assertEqual(len(fetcher.calls), 3)
        self.assertIsNotNone(fetcher.calls[-1][2])

    def test_active_nonbypassed_https_proxy_is_respected(self):
        with mock.patch("node_preview.official_transport.urllib.request.getproxies",
                        return_value={"https": "http://proxy.invalid:8080"}):
            with mock.patch("node_preview.official_transport.urllib.request.proxy_bypass", return_value=False):
                self.assertFalse(direct_official_route_allowed())
                with self.assertRaises(ValueError):
                    OfficialGetRoute(EXPLORER_API + "/stats")
        self.dns.assert_not_called()

    def test_transport_rejects_other_domains_and_non_get_endpoint_paths(self):
        for url in ("https://other.example/api/stats", "http://explorer.marschain.net/api/stats",
                    "https://explorer.marschain.net/api/transactions", EXPLORER_API + "/stats?override=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OfficialGetRoute(url)
        self.dns.assert_not_called()


if __name__ == "__main__":
    unittest.main()
