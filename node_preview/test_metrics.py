"""Behavioral checks for cutoff isolation, provenance and outage recovery."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from node_preview.metrics import MetricStore, POWER_CONTRACT, TOTAL_BURNED, VIEW_POWER


def abi(*words):
    return "0x" + "".join(f"{word:064x}" for word in words)


class FakeNetwork:
    def __init__(self):
        self.calls = []
        self.offline = False
        self.contract_states = {200: (100, 10**30), 99: (60, 10**30 - 10**19), 101: (90, 10**30 - 3 * 10**18)}
        self.block_timestamps = {99: 86399, 100: 86400, 101: 172799, 102: 172800, 200: 172820}
        # The contract view contains completed days and its mutable current
        # day; refresh filters the latter out at the verified block timestamp.
        self.history = abi(64, 64 + 4 * 32, 3, 0, 1, 2, 3, 60, 90, 999)

    def __call__(self, url, timeout=10, payload=None):
        self.calls.append((url, payload))
        if self.offline:
            raise OSError("intentional offline")
        if payload:
            if payload["method"] == "eth_getBlockByNumber":
                number = int(payload["params"][0], 16)
                return {"result": {"number": hex(number), "timestamp": hex(self.block_timestamps[number])}}
            self.assert_rpc(payload)
            block = payload["params"][1]
            data = payload["params"][0]["data"]
            if data == "0x1c1b5cdf":
                return {"result": self.history}
            number = int(block, 16)
            if number not in self.contract_states:
                return {"error": {"message": "missing trie node"}}
            power, burned = self.contract_states[number]
            return {"result": abi(0, power) if data == VIEW_POWER else abi(burned)}
        if url.endswith("/power/stats"):
            return {"totalPower": "101", "totalBurnedTokens": str(10**30 + 1)}
        if url.endswith("/stats"):
            return {"totalAddresses": 285406, "totalCirculation": str(19550113806410662313873549610),
                    "currentPrice": "0.01047", "highestPrice": "0.02254", "lowestPrice": "0.00236"}
        if url.endswith("data/latest.json"):
            return {"meta": {"generated_at": 100, "statistics_window_end_timestamp": 86400, "today_new_power": 7},
                    "rows": [{"address": "0x" + "a" * 40, "power": "100"}]}
        if url.endswith("data/metric-history.json"):
            return {"snapshots": [{"statistics_window_end_timestamp": 86400, "values": {"total_burned": 10**30 + 123}}]}
        if url.endswith("build-meta.json"):
            return {"generated_at": 100}
        raise AssertionError(url)

    @staticmethod
    def assert_rpc(payload):
        assert payload["method"] == "eth_call"
        assert payload["params"][0]["to"] == POWER_CONTRACT


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.net = FakeNetwork()
        self.store = MetricStore(self.root / "preview.sqlite3", seed_dir=self.root / "seed", fetcher=self.net, cache_seconds=50)
        self.base = {"generated_at": 100, "network_total_power": 7, "network_total_burned_tokens": 11,
                     "statistics_window_end_timestamp": 86400, "statistics_window_new_power": 7, "today_new_power": 7,
                     "period_7d_end_timestamp": 86400, "period_7d_new_power": 50, "emission_genesis_timestamp": 0}

    def tearDown(self):
        self.temp.cleanup()

    def test_minute_refresh_keeps_daily_cutoff_and_history_immutable(self):
        seed = self.store.bootstrap()
        before = self.store.history()
        result = self.store.refresh(self.base, 200, now=170000)
        self.assertEqual(result["network_total_power"], 100)
        self.assertEqual(result["network_total_burned_tokens"], 10**30)
        self.assertEqual(result["statistics_window_end_timestamp"], 86400)
        self.assertEqual(result["today_new_power"], seed["payload"]["meta"]["today_new_power"])
        self.assertEqual(self.store.history(), before)
        self.assertEqual(before[0]["values"]["total_burned"], 10**30 + 123)
        self.assertNotIn("preview_metric_sources", self.base)
        self.assertEqual(result["preview_metric_sources"]["network_total_power"]["block"], 200)

    def test_stats_cache_survives_service_restart_and_outage(self):
        # The publisher passes the collector's confirmed timestamp even when
        # RPC is down; cached metric recovery should use that same envelope.
        block = {"number": 200, "timestamp": self.net.block_timestamps[200]}
        first = self.store.refresh(self.base, block, now=170000)
        self.net.offline = True
        reopened = MetricStore(self.root / "preview.sqlite3", fetcher=self.net)
        result = reopened.refresh(self.base, block, now=170100)
        self.assertEqual(result["explorer_total_addresses"], first["explorer_total_addresses"])
        self.assertEqual(result["network_total_power"], 100)
        self.assertEqual(result["preview_metric_sources"]["network_total_power"]["status"], "stale_cache")
        self.assertIn("intentional offline", result["preview_official_stats_status"]["stats"]["error"])
        self.assertEqual(result["preview_official_stats_status"]["stats"]["fetched_at"], 170000)

    def test_bootstrap_offline_from_disk_and_no_empty_rank_fallback(self):
        payload = {"meta": self.base, "rows": [{"address": "real", "power": "4"}]}
        self.store.seed_dir.mkdir()
        (self.store.seed_dir / "latest.json").write_text(json.dumps(payload))
        self.net.offline = True
        restored = self.store.bootstrap()
        self.assertEqual(restored["payload"], payload)
        self.assertIn("history", restored["bootstrap_errors"])
        missing = MetricStore(self.root / "missing.sqlite3", seed_dir=self.root / "missing", fetcher=self.net)
        with self.assertRaisesRegex(RuntimeError, "real published ranking"):
            missing.bootstrap()

    def test_minute_refresh_does_not_retrieve_origin_seed(self):
        self.store.bootstrap()
        self.net.calls.clear()
        self.store.refresh(self.base, 200, now=170000)
        self.assertTrue(all("aliyuncs.com" not in url for url, _ in self.net.calls))
        first_count = len([url for url, payload in self.net.calls if payload is None])
        self.store.refresh(self.base, 200, now=170020)
        self.assertEqual(len([url for url, payload in self.net.calls if payload is None]), first_count)

    def observations(self, gap=False, reorg=False):
        with sqlite3.connect(self.store.db_path) as conn:
            conn.executescript("""
                CREATE TABLE preview_blocks(number INTEGER PRIMARY KEY,block_hash TEXT,parent_hash TEXT,timestamp INTEGER,tx_count INTEGER,value_wei TEXT);
                CREATE TABLE preview_block_addresses(block_number INTEGER,address TEXT,PRIMARY KEY(block_number,address));
                CREATE TABLE preview_log_scan_ranges(start_block INTEGER PRIMARY KEY,end_block INTEGER);
                CREATE TABLE preview_power_events(block_number INTEGER,kind TEXT,amount_wei TEXT);
            """)
            # Sparse time is intentional, but numbered blocks cover every block
            # in this synthetic chain, including an empty block.
            blocks = [(99, "h99", "h98", 86399, 0, "0"),
                      (100, "h100", "h99", 86400, 2, str(2**90)),
                      (101, "h101", "wrong" if reorg else "h100", 172799, 0, "0"),
                      (102, "h102", "h101", 172800, 1, "5")]
            conn.executemany("INSERT INTO preview_blocks VALUES (?,?,?,?,?,?)", [b for b in blocks if not gap or b[0] != 100])
            conn.executemany("INSERT INTO preview_block_addresses VALUES (?,?)", [(100, "wallet-a"), (100, "wallet-b"), (101, "wallet-a")])
            conn.execute("INSERT INTO preview_log_scan_ranges VALUES (100,101)")
            conn.execute("INSERT INTO preview_power_events VALUES (100,'TokensBurned',?)", (str(7 * 10**18),))

    def test_verified_whole_window_freezes_once_with_exact_integer_volume(self):
        self.observations()
        result = self.store.refresh(self.base, 200, now=172820)
        self.assertEqual(result["preview_daily_status"]["state"], "verified_node_window")
        self.assertEqual(result["statistics_window_end_timestamp"], 172800)
        self.assertEqual(result["statistics_window_new_power"], 30)
        self.assertEqual(result["statistics_window_burned_tokens"], 7 * 10**18)
        self.assertEqual(result["statistics_window_active_wallet_address_count"], 2)
        self.assertEqual(result["statistics_window_transaction_volume_wei"], 2**90)
        self.assertIsNone(result["today_new_wallet_count"])
        # Period windows now advance independently; an unavailable historical
        # endpoint must not pin the card to the previous daily cutoff.
        self.assertEqual(result["period_7d_end_timestamp"], 172800)
        self.assertIsNone(result["period_7d_new_power"])
        self.assertFalse(result["period_7d_complete"])
        self.assertEqual(len(self.store.history()), 1)
        count = len(self.net.calls)
        result2 = self.store.refresh(result, 200, now=172850)
        self.assertEqual(self.store.history()[0]["values"]["daily_transaction_volume"], 2**90)
        self.assertEqual(len(self.store.history()), 1)
        # Current totals are rechecked, but daily boundaries are frozen.
        self.assertEqual(result2["statistics_window_new_power"], 30)
        self.assertEqual(result2["preview_daily_status"]["frozen_end_timestamp"], 172800)

    def test_verified_freeze_refreshes_curves_and_overrides_retained_power_history(self):
        self.observations()
        base = dict(self.base)
        base["daily_total_power_history"] = [{"day": 1, "value": 60}, {"day": 2, "value": 999}]
        base["metric_trends"] = {"network_total_power": {"source": "old seed", "values": [
            {"label": "1970-01-02", "value": 60}, {"label": "1970-01-03", "value": 999}]}}
        self.store._put("seed_history", [{"statistics_window_end_timestamp": 86400, "values": {
            "network_total_power": 60, "daily_active_addresses": 1, "daily_transaction_volume": 2**89}}])
        result = self.store.refresh(base, 200, now=172820)
        self.assertEqual(result["metric_trends"]["network_total_power"]["values"], [
            {"label": "1970-01-02", "value": 60}, {"label": "1970-01-03", "value": 90}])
        self.assertEqual(result["metric_trends"]["daily_active_addresses"]["values"][-1],
                         {"label": "1970-01-03", "value": 2})
        self.assertEqual(result["metric_trends"]["daily_transaction_volume"]["values"][-1]["value"], 2**90)
        self.assertIn("节点核验日结", result["metric_trends"]["network_total_power"]["source"])
        self.assertEqual(base["metric_trends"]["network_total_power"]["values"][-1]["value"], 999)

    def test_minute_changes_do_not_append_curve_or_replace_frozen_point(self):
        self.observations()
        first = self.store.refresh(self.base, 200, now=172820)
        frozen_history = self.store.history()
        frozen_curves = first["metric_trends"]
        self.net.contract_states[200] = (9999, 10**30 + 999)
        next_minute = self.store.refresh(first, 200, now=172880)
        self.assertEqual(next_minute["network_total_power"], 9999)
        self.assertEqual(next_minute["metric_trends"], frozen_curves)
        self.assertEqual(self.store.history(), frozen_history)
        self.assertEqual(next_minute["metric_trends"]["network_total_power"]["values"][-1]["value"], 90)

    def test_unverified_minute_points_cannot_enter_daily_curves(self):
        self.store._put("seed_history", [
            {"statistics_window_end_timestamp": 86400, "values": {"network_total_power": 60}},
            {"statistics_window_end_timestamp": 86400, "source": "minute_preview", "values": {"network_total_power": 999}},
            {"statistics_window_end_timestamp": 86460, "source": "verified_node_window", "values": {"network_total_power": 9999}},
        ])
        result = self.store.refresh(self.base, 200, now=170000)
        self.assertEqual(result["metric_trends"]["network_total_power"]["values"], [{"label": "1970-01-02", "value": 60}])

    def test_partial_window_cannot_advance_cutoff(self):
        self.observations(gap=True)
        result = self.store.refresh(self.base, 200, now=172820)
        self.assertEqual(result["statistics_window_end_timestamp"], 86400)
        self.assertIn("gaps", result["preview_daily_status"]["detail"])
        self.assertEqual(self.store.history(), [])

    def test_reorg_cannot_be_published_as_complete_day(self):
        self.observations(reorg=True)
        result = self.store.refresh(self.base, 200, now=172820)
        self.assertEqual(result["statistics_window_end_timestamp"], 86400)
        self.assertIn("reorg", result["preview_daily_status"]["detail"])

    def test_burn_disagreement_blocks_daily_freeze(self):
        self.observations()
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute("UPDATE preview_power_events SET amount_wei='1'")
        result = self.store.refresh(self.base, 200, now=172820)
        self.assertEqual(result["statistics_window_end_timestamp"], 86400)
        self.assertIn("disagrees", result["preview_daily_status"]["detail"])

    def test_completed_contract_day_fallback_excludes_mutable_current_day(self):
        self.observations()
        self.net.contract_states.pop(99)
        self.net.history = abi(64, 64 + 4 * 32, 3, 0, 1, 2, 3, 60, 90, 999)
        result = self.store.refresh(self.base, 200, now=172820)
        self.assertEqual(result["statistics_window_new_power"], 30)
        self.assertEqual(result["statistics_window_burned_tokens"], 7 * 10**18)
        self.assertEqual(result["preview_daily_status"]["field_sources"]["burned"]["source"], "complete_power_burn_events")
        self.assertEqual(self.store._historical_power_at(172800), 90)

    def test_incomplete_event_coverage_cannot_replace_pruned_burn_state(self):
        self.observations()
        self.net.contract_states.pop(99)
        self.net.history = abi(64, 64 + 4 * 32, 3, 0, 1, 2, 3, 60, 90, 999)
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute("DELETE FROM preview_log_scan_ranges")
        result = self.store.refresh(self.base, 200, now=172820)
        self.assertEqual(result["statistics_window_end_timestamp"], 86400)
        self.assertIn("complete burn log coverage", result["preview_daily_status"]["detail"])


if __name__ == "__main__":
    unittest.main()
