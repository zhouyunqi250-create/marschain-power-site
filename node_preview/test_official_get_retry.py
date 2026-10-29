"""Offline regressions for bounded official GET retries and honest cache age."""
import copy
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest import mock
import urllib.error

from node_preview.metrics import EXPLORER_API, MetricStore


STATS = {
    "totalAddresses": 286323,
    "totalCirculation": "20186328235149745380965338281",
    "currentPrice": "0.01035",
    "highestPrice": "0.02254",
}
POWER = {"totalPower": "575944585920388", "totalBurnedTokens": "35079944045411562904803005969"}


class QueueFetcher:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, timeout, payload):
        self.calls.append((url, timeout, payload))
        if not self.replies:
            raise AssertionError("Unexpected extra fetch")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)


class OfficialGetRetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "metrics.sqlite3"
        # All clock effects are deterministic and all network calls are doubles.
        self.monotonic = mock.patch("node_preview.metrics.time.monotonic", return_value=100.0)
        self.monotonic.start()

    def tearDown(self):
        self.monotonic.stop()
        self.temporary.cleanup()

    def store(self, fetcher, **options):
        return MetricStore(self.database, fetcher=fetcher, **options)

    def test_handshake_timeout_retries_once_and_recovers_real_receipt(self):
        fetcher = QueueFetcher([
            urllib.error.URLError(TimeoutError("The handshake operation timed out")), STATS,
        ])
        store = self.store(fetcher)
        with mock.patch("node_preview.metrics.time.monotonic", side_effect=[100.0, 110.2]):
            payload, source = store._cached_external("/stats", 1000)
        self.assertEqual(payload, STATS)
        self.assertEqual(source["status"], "ok")
        self.assertEqual(source["fetched_at"], 1010)
        self.assertIsNone(source["error"])
        self.assertEqual(fetcher.calls, [(EXPLORER_API + "/stats", 10, None)] * 2)

    def test_two_failures_preserve_payload_and_time_then_retry_after_ten_seconds(self):
        fetcher = QueueFetcher([STATS, TimeoutError("handshake timeout"), TimeoutError("read timeout"), STATS])
        store = self.store(fetcher, cache_seconds=50)
        original, first = store._cached_external("/stats", 1000)
        payload, failed = store._cached_external("/stats", 1060)
        self.assertEqual(payload, original)
        self.assertEqual(failed["status"], "stale_cache")
        self.assertEqual(failed["fetched_at"], first["fetched_at"])
        self.assertEqual(failed["checked_at"], 1060)
        self.assertEqual(failed["age_seconds"], 60)
        self.assertEqual(failed["error"], "read timeout")
        self.assertEqual(len(fetcher.calls), 3)
        store._cached_external("/stats", 1069)
        self.assertEqual(len(fetcher.calls), 3)
        _, recovered = store._cached_external("/stats", 1070)
        self.assertEqual(len(fetcher.calls), 4)
        self.assertEqual(recovered["status"], "ok")
        self.assertEqual(recovered["fetched_at"], 1070)
        self.assertIsNone(recovered["error"])
        # A successful response still has the original fifty-second interval.
        store._cached_external("/stats", 1080)
        self.assertEqual(len(fetcher.calls), 4)

    def test_both_failures_without_a_prior_response_remain_unavailable(self):
        fetcher = QueueFetcher([TimeoutError("first timeout"), TimeoutError("second timeout")])
        payload, source = self.store(fetcher)._cached_external("/stats", 1000)
        self.assertEqual(payload, {})
        self.assertEqual(source["status"], "unavailable")
        self.assertIsNone(source["fetched_at"])
        self.assertIsNone(source["age_seconds"])
        self.assertEqual(source["error"], "second timeout")
        self.assertEqual(len(fetcher.calls), 2)

    def test_http_certificate_and_json_errors_are_not_retried(self):
        errors = [
            urllib.error.HTTPError(EXPLORER_API + "/stats", code, "HTTP error", None, None)
            for code in (401, 429, 503)
        ] + [
            urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
            json.JSONDecodeError("invalid response", "x", 0),
        ]
        for index, error in enumerate(errors):
            with self.subTest(error=type(error).__name__, code=getattr(error, "code", None)):
                fetcher = QueueFetcher([error])
                store = MetricStore(Path(self.temporary.name) / f"error-{index}.sqlite3", fetcher=fetcher)
                payload, source = store._cached_external("/stats", 1000)
                self.assertEqual(len(fetcher.calls), 1)
                self.assertEqual(payload, {})
                self.assertEqual(source["status"], "unavailable")
                self.assertIsNone(source["fetched_at"])

    def test_incomplete_stats_do_not_retry_or_relabel_the_previous_response(self):
        fetcher = QueueFetcher([STATS, {"totalAddresses": 999999, "totalCirculation": "1"}])
        store = self.store(fetcher)
        original, first = store._cached_external("/stats", 1000)
        payload, source = store._cached_external("/stats", 1060)
        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(payload, original)
        self.assertEqual(source["status"], "stale_cache")
        self.assertEqual(source["fetched_at"], first["fetched_at"])
        self.assertIn("incomplete official stats response", source["error"])

    def test_power_endpoint_failure_does_not_taint_successful_stats(self):
        fetcher = QueueFetcher([STATS, POWER, STATS, TimeoutError("first timeout"), TimeoutError("second timeout")])
        store = self.store(fetcher)
        store._cached_external("/stats", 1000)
        store._cached_external("/power/stats", 1000)
        _, stats_source = store._cached_external("/stats", 1060)
        power, power_source = store._cached_external("/power/stats", 1060)
        self.assertEqual(stats_source["status"], "ok")
        self.assertEqual(stats_source["fetched_at"], 1060)
        self.assertEqual(power_source["status"], "stale_cache")
        self.assertEqual(power_source["fetched_at"], 1000)
        self.assertEqual(power, POWER)
        self.assertEqual([url.rsplit("/api", 1)[1] for url, _, _ in fetcher.calls],
                         ["/stats", "/power/stats", "/stats", "/power/stats", "/power/stats"])

    def test_timeout_cap_is_local_to_official_get_and_rpc_does_not_retry(self):
        fetcher = QueueFetcher([TimeoutError("GET timeout"), STATS, TimeoutError("RPC timeout")])
        store = self.store(fetcher, timeout=60)
        payload, source = store._cached_external("/stats", 1000)
        self.assertEqual(payload, STATS)
        self.assertEqual(source["status"], "ok")
        self.assertEqual([call[1] for call in fetcher.calls], [10, 10])
        with self.assertRaisesRegex(TimeoutError, "RPC timeout"):
            store._rpc("eth_blockNumber", [])
        self.assertEqual(len(fetcher.calls), 3)
        self.assertEqual(fetcher.calls[-1][1], 60)
        self.assertEqual(fetcher.calls[-1][2]["method"], "eth_blockNumber")

    def test_zero_cache_interval_still_forces_a_fresh_official_response(self):
        fetcher = QueueFetcher([TimeoutError("first timeout"), TimeoutError("second timeout"), STATS])
        store = self.store(fetcher, cache_seconds=0)
        _, first = store._cached_external("/stats", 1000)
        payload, second = store._cached_external("/stats", 1001)
        self.assertEqual(first["status"], "unavailable")
        self.assertEqual(payload, STATS)
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["fetched_at"], 1001)
        self.assertEqual(len(fetcher.calls), 3)


if __name__ == "__main__":
    unittest.main()
