"""Offline report retention checks; no RPC, HTTP, browser, or formal-state writes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from node_preview import share_report as report


NOW = 1793296080
CUTOFF = NOW // report.DAY * report.DAY
HASH = "0x" + "a" * 64


def fixture():
    node = {"state": "ready", "source_block": 10000, "source_block_hash": HASH,
            "source_block_timestamp": NOW - 30, "generated_at": NOW,
            "version": "test-snapshot"}
    meta = {"generated_at": NOW, "node_preview": node,
            "statistics_window_end_timestamp": CUTOFF,
            "network_total_power": 100000, "network_total_burned_tokens": 2000000000000000000,
            "network_current_price": 0.01, "network_highest_price": 0.02,
            "network_total_circulation_tokens": 3000000000000000000,
            "explorer_total_addresses": 300000, "power_required_per_mars_daily": 3000000.5,
            "emission_model_status": "verified", "emission_reference_block": 10000,
            "emission_reference_timestamp": NOW - 30,
            "preview_metric_sources": {}}
    for field in report._OFFICIAL_FIELDS:
        meta["preview_metric_sources"][field] = {"source": "official", "status": "ok", "fetched_at": NOW - 20}
    for field in ("network_total_power", "network_total_burned_tokens", "emission"):
        meta["preview_metric_sources"][field] = {"source": "node", "status": "ok", "block": 10000}
    payload = {"meta": meta, "rows": [{"power": str(1000 - index), "address": "PRIVATE"} for index in range(100)]}
    status = {"state": "verified_node_window", "frozen_end_timestamp": CUTOFF, "expected_end_timestamp": CUTOFF}
    frozen = {**node, "state": "frozen", "source_block": 9999,
              "source_block_timestamp": CUTOFF - 2, "target_timestamp": CUTOFF}
    dm = {"node_preview": frozen, "preview_daily_status": status,
          "statistics_window_start_timestamp": CUTOFF - report.DAY,
          "statistics_window_end_timestamp": CUTOFF,
          "statistics_window_new_power": -10, "statistics_window_burned_tokens": 3,
          "statistics_window_active_wallet_address_count": 10,
          "statistics_window_new_candidate_address_count": 1,
          "statistics_window_active_transactions_seen": 10,
          "statistics_window_transaction_volume_wei": 5,
          "statistics_window_end_total_power": 99990,
          "network_total_power": 99990, "network_total_burned_tokens": 10,
          # Incomplete optional historic comparisons must not freeze the report.
          "period_7d_new_candidate_address_count": None,
          "period_30d_new_candidate_address_display": "暂无可比采样",
          "preview_metric_sources": {key: {"source": "node", "status": "ok", "block": 9999}
                                     for key in ("network_total_power", "network_total_burned_tokens")}}
    for name, stamp in (("start", CUTOFF - report.DAY), ("end", CUTOFF)):
        dm[f"statistics_window_{name}_local"] = report.datetime.fromtimestamp(
            stamp + 8 * 3600, report.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    manifest = {"state": "complete", "complete_for_known_addresses": True,
                "target_timestamp": CUTOFF, "daily_statistics_cutoff": CUTOFF,
                "daily_statistics_status": status, "source_block": 9999,
                "source_block_hash": HASH, "source_block_timestamp": CUTOFF - 2}
    return payload, {"meta": dm}, manifest


class ReportCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "state").mkdir()
        self.payload, self.public, self.manifest = fixture()
        self.archive = self.root / "archives" / report.datetime.fromtimestamp(
            CUTOFF, report.timezone.utc).strftime("%Y%m%d-0800")
        self.archive.mkdir(parents=True)
        raw = json.dumps(self.public, ensure_ascii=False).encode()
        (self.archive / "public.json").write_bytes(raw)
        self.manifest["files"] = {"public.json": {"sha256": hashlib.sha256(raw).hexdigest()}}
        (self.archive / "manifest.json").write_text(json.dumps(self.manifest))
        self.cache = self.root / "state" / report._REPORT_CACHE_NAME
        self.clock = patch.object(report.time, "time", return_value=NOW)
        self.clock.start()
        report._ARCHIVE_CACHE.clear()

    def tearDown(self):
        self.clock.stop()
        self.temporary.cleanup()
        report._ARCHIVE_CACHE.clear()

    def build(self, payload=None, now=NOW):
        with patch.object(report.time, "time", return_value=now):
            return report.build_share_report(self.root, self.payload if payload is None else payload)

    def assertRetained(self, result, original, now):
        expected = copy.deepcopy(original)
        expected.update(state="retained", retained_at=now)
        self.assertEqual(result, expected)

    def stale(self):
        payload = copy.deepcopy(self.payload)
        payload["meta"]["preview_metric_sources"]["network_current_price"]["status"] = "stale"
        payload["meta"]["network_total_power"] = 123456789
        return payload

    def rewrite_cache(self, bundle):
        self.cache.write_bytes(report._canonical({"schema": 1,
            "sha256": hashlib.sha256(report._canonical(bundle)).hexdigest(), "bundle": bundle}))

    def test_complete_saved_and_failure_preserves_the_entire_original(self):
        first = self.build()
        self.assertEqual(first["state"], "ready")
        self.assertTrue(self.cache.is_file())
        self.assertNotIn("PRIVATE", self.cache.read_text())
        self.assertEqual(first["live"]["meta"]["network_total_burned_tokens"], "2000000000000000000")
        self.assertRetained(self.build(self.stale(), NOW + 60), first, NOW + 60)

    def test_restart_cross_day_and_missing_archive_retain_original_clock_and_window(self):
        first = self.build()
        report._ARCHIVE_CACHE.clear()
        (self.archive / "manifest.json").unlink()
        self.assertRetained(self.build(self.stale(), NOW + report.DAY), first, NOW + report.DAY)

    def test_recovery_replaces_cache_with_complete_fresh_report(self):
        first = self.build()
        next_payload = copy.deepcopy(self.payload)
        next_payload["meta"]["generated_at"] = NOW + 60
        next_payload["meta"]["network_current_price"] = 0.011
        recovered = self.build(next_payload, NOW + 60)
        self.assertEqual(recovered["state"], "ready")
        self.assertNotEqual(recovered["retrieved_at"], first["retrieved_at"])
        self.assertEqual(recovered["live"]["meta"]["network_current_price"], 0.011)
        self.assertRetained(self.build(self.stale(), NOW + 120), recovered, NOW + 120)

    def test_no_cache_and_bad_current_bundle_waits(self):
        self.assertEqual(self.build(self.stale())["state"], "waiting_for_settlement")
        self.assertFalse(self.cache.exists())

    def test_zero_official_values_and_missing_optional_period_addresses_are_valid(self):
        for field in report._OFFICIAL_FIELDS:
            self.payload["meta"][field] = 0
        fresh = self.build()
        self.assertEqual(fresh["state"], "ready")
        self.assertIsNone(fresh["daily"]["meta"]["period_7d_new_candidate_address_count"])

    def test_bad_current_source_node_model_rank_and_value_keep_old_complete(self):
        first = self.build()
        cases = [
            ("source", lambda p: p["meta"]["preview_metric_sources"]["network_highest_price"].update(source="snapshot")),
            ("nan", lambda p: p["meta"].update(network_current_price="nan")),
            ("negative", lambda p: p["meta"].update(explorer_total_addresses=-1)),
            ("future", lambda p: p["meta"]["preview_metric_sources"]["network_highest_price"].update(fetched_at=NOW + 99)),
            ("old official", lambda p: p["meta"]["preview_metric_sources"]["network_highest_price"].update(fetched_at=NOW - 301)),
            ("emission", lambda p: p["meta"].update(emission_reference_block=9999)),
            ("rank", lambda p: p["rows"][0].update(power="999999999")),
            ("node", lambda p: p["meta"]["node_preview"].update(source_block_timestamp=NOW - 301)),
            ("node source", lambda p: p["meta"]["preview_metric_sources"]["network_total_power"].update(block=9999)),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                invalid = copy.deepcopy(self.payload)
                mutate(invalid)
                self.assertRetained(self.build(invalid), first, NOW)

    def test_old_ok_source_without_complete_cache_is_not_publishable(self):
        self.payload["meta"]["preview_metric_sources"]["network_current_price"]["fetched_at"] = NOW - 301
        self.assertEqual(self.build()["state"], "waiting_for_settlement")
        self.assertFalse(self.cache.exists())

    def test_cache_bad_hash_truncated_or_oversized_never_returns_partial(self):
        first = self.build()
        for content in (b'{"schema":1}', b'{"schema":1,', b'x' * (report.MAX_PUBLIC_BYTES + 1)):
            with self.subTest(length=len(content)):
                self.cache.write_bytes(content)
                self.assertEqual(self.build(self.stale())["state"], "waiting_for_settlement")
        self.rewrite_cache(first)
        doc = json.loads(self.cache.read_text())
        doc["bundle"]["live"]["meta"]["network_current_price"] = 9
        self.cache.write_text(json.dumps(doc))
        self.assertEqual(self.build(self.stale())["state"], "waiting_for_settlement")

    def test_cache_semantics_and_public_whitelist_checked_even_when_hash_matches(self):
        first = self.build()
        for mutate in (
            lambda b: b["live"]["meta"]["preview_metric_sources"]["network_current_price"].update(source="snapshot"),
            lambda b: b["live"]["rows"][0].update(address="PRIVATE"),
            lambda b: b["daily"]["meta"].update(private_database="PRIVATE"),
            lambda b: b["daily_evidence"].update(target_timestamp=CUTOFF - report.DAY),
        ):
            invalid = copy.deepcopy(first)
            mutate(invalid)
            self.rewrite_cache(invalid)
            self.assertEqual(self.build(self.stale())["state"], "waiting_for_settlement")

    def test_cache_symlink_not_read_or_overwritten(self):
        original = self.build()
        external = self.root / "external.json"
        self.cache.rename(external)
        before = external.read_bytes()
        self.cache.symlink_to(external)
        self.assertEqual(self.build(self.stale())["state"], "waiting_for_settlement")
        self.assertEqual(self.build(), original)
        self.assertTrue(self.cache.is_symlink())
        self.assertEqual(external.read_bytes(), before)

    def test_write_failure_does_not_block_fresh_complete_report_or_main_ranking(self):
        with patch.object(report, "_save_complete", side_effect=OSError("disk full")):
            self.assertEqual(self.build()["state"], "ready")

    def test_cold_start_imports_existing_public_latest_without_changing_its_contents(self):
        first = self.build()
        self.cache.unlink()
        data = self.root / "site/data"
        data.mkdir(parents=True)
        latest = data / "latest.json"
        latest.write_text(json.dumps({"share_report": first}))
        before = latest.read_bytes()
        self.assertRetained(self.build(self.stale(), NOW + 60), first, NOW + 60)
        self.assertEqual(latest.read_bytes(), before)
        self.assertTrue(self.cache.exists())

    def test_isolated_preview_cache_stays_under_own_root(self):
        preview = self.root / "isolated"
        (preview / "state").mkdir(parents=True)
        result = report.build_share_report(self.root, self.payload, cache_root=preview)
        self.assertEqual(result["state"], "ready")
        self.assertFalse(self.cache.exists())
        self.assertTrue((preview / "state" / report._REPORT_CACHE_NAME).is_file())


if __name__ == "__main__":
    unittest.main()
