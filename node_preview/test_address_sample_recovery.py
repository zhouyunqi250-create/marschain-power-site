"""Offline regressions for recovering official address samples from archives.

All archives and databases are temporary.  No chain scan, RPC request, official
HTTP request, production file, or external service is used by these checks.
"""

import copy
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from node_preview.frontend import public_payload
from node_preview.metrics import EXPLORER_API, MetricStore
from node_preview.official_address_sample import capture


class AddressSampleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state" / "preview.sqlite3"
        self.fetch_calls = []
        self.start = int(datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp())
        self.end = self.start + 86400
        self.store = self.reopen()

    def tearDown(self):
        self.assertEqual(self.fetch_calls, [], "archive recovery must not use HTTP or RPC")
        self.temporary.cleanup()

    def no_network(self, *args, **kwargs):
        self.fetch_calls.append((args, kwargs))
        raise AssertionError("archive recovery must not use HTTP or RPC")

    def reopen(self):
        return MetricStore(self.database, fetcher=self.no_network)

    def write_archive(self, cutoff, total, *, offset=25, source="official",
                      source_update=None, meta_update=None, manifest_update=None):
        """Write the same integrity/provenance envelope as a private daily freeze."""
        directory = self.root / "archives" / time.strftime("%Y%m%d-0800", time.gmtime(cutoff))
        directory.mkdir(parents=True, exist_ok=True)
        provenance = {"source": source, "status": "ok", "fetched_at": cutoff + offset}
        provenance.update(source_update or {})
        meta = {"explorer_total_addresses": total,
                "generated_at": cutoff + 25,
                "preview_metric_sources": {"explorer_total_addresses": provenance}}
        meta.update(meta_update or {})
        encoded = json.dumps({"meta": meta, "rows": []}, sort_keys=True).encode("utf-8")
        (directory / "public.json").write_bytes(encoded)
        manifest = {"schema": 1, "state": "complete", "target_timestamp": cutoff,
                    "frozen_at": cutoff + 25, "known_count": 280000,
                    "processed_count": 280000, "complete_for_known_addresses": True,
                    "files": {"public.json": {"sha256": hashlib.sha256(encoded).hexdigest(),
                                               "bytes": len(encoded)}}}
        manifest.update(manifest_update or {})
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        # Each rewritten fixture is a separate candidate, including negative
        # subtests.  Exercise its validation rather than the retry backoff.
        self.store._put("official_sample_archive_recovery", {})
        return directory

    def write_comparable_archives(self):
        self.write_archive(self.start, 285485, offset=43, source=EXPLORER_API + "/stats")
        self.write_archive(self.end, 285598, offset=25)

    def sample_rows(self):
        with sqlite3.connect(self.database) as connection:
            return connection.execute(
                "SELECT kind,cutoff,received_at,payload,provenance "
                "FROM preview_metric_cutoff_samples ORDER BY kind,cutoff"
            ).fetchall()

    def public_meta(self):
        return {"generated_at": self.end + 3600,
                "statistics_window_end_timestamp": self.end,
                "statistics_window_start_timestamp": self.start,
                "statistics_window_end_local": "2026-10-25 08:00:00",
                "statistics_window_start_local": "2026-10-24 08:00:00",
                "statistics_window_label": "2026-10-24 08:00:00 至 2026-10-25 08:00:00",
                "explorer_total_addresses": 285598,
                "network_total_power": "100000000000",
                "candidate_count": 280000, "positive_power_count": 120000}

    def test_missing_sample_table_recovers_archive_pair_and_persists_idempotently(self):
        self.write_comparable_archives()
        # An old database can predate the sample table.  Opening MetricStore
        # upgrades that schema; the first read must then recover prior samples.
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE preview_metric_cutoff_samples")
        self.store = self.reopen()
        meta = self.public_meta()
        self.store._apply_sampled_growth(meta)
        self.assertEqual(meta["statistics_window_new_candidate_address_count"], 113)
        self.assertEqual(meta["today_new_wallet_count"], 113)
        provenance = meta["statistics_window_new_candidate_address_sample"]
        self.assertEqual(provenance["status"], "sampled")
        self.assertFalse(provenance["exact_cutoff"])
        self.assertEqual(provenance["start"]["received_at"], self.start + 43)
        self.assertEqual(provenance["end"]["received_at"], self.end + 25)
        self.assertEqual(provenance["start"]["offset_seconds"], 43)
        self.assertEqual(provenance["end"]["offset_seconds"], 25)
        first_rows = self.sample_rows()
        self.assertEqual(len(first_rows), 2)
        self.store._apply_sampled_growth(meta)
        self.assertEqual(self.sample_rows(), first_rows)
        # Recovery is durable, rather than a transient lookup needed every
        # minute.  It still works after restart without the archive directory.
        (self.root / "archives").rename(self.root / "detached-archives")
        self.store = self.reopen()
        self.assertEqual(self.store._sampled_address_growth(self.start, self.end)[0], 113)
        self.assertEqual(self.sample_rows(), first_rows)

    def test_recovered_growth_reaches_both_public_wallet_cards(self):
        self.write_comparable_archives()
        meta = self.public_meta()
        self.store._apply_sampled_growth(meta)
        public = public_payload({"meta": meta, "rows": []})
        self.assertEqual(public["meta"]["statistics_window_new_candidate_address_count"], 113)
        for device in ("desktop", "mobile"):
            with self.subTest(device=device):
                html = public["view"][device]["metrics"]["钱包地址"]["html"]
                self.assertIn("113", html)
                self.assertIn("近截点采样", html)
                self.assertNotIn("暂无可比快照", html)

    def test_official_source_alias_and_full_url_are_both_accepted(self):
        self.write_comparable_archives()
        for cutoff, expected in ((self.start, 285485), (self.end, 285598)):
            with self.subTest(cutoff=cutoff):
                value, source = self.store._cutoff_sample("official_stats", cutoff)
                self.assertEqual(value["total_wallets"], expected)
                self.assertEqual(source["timing_status"], "sampled_near_cutoff")
                self.assertFalse(source["exact_cutoff"])

    def test_archive_file_hash_and_byte_count_are_required(self):
        for fault in ("hash", "size", "missing_integrity"):
            with self.subTest(fault=fault):
                directory = self.write_archive(self.start, 285485, offset=43)
                path = directory / "manifest.json"
                manifest = json.loads(path.read_text())
                if fault == "hash":
                    manifest["files"]["public.json"]["sha256"] = "0" * 64
                elif fault == "size":
                    manifest["files"]["public.json"]["bytes"] += 1
                else:
                    del manifest["files"]["public.json"]
                path.write_text(json.dumps(manifest), encoding="utf-8")
                self.assertEqual(self.store._cutoff_sample("official_stats", self.start), ({}, {}))
                self.assertEqual(self.sample_rows(), [])

    def test_incomplete_or_wrong_target_archive_cannot_supply_cutoff(self):
        for update in ({"state": "running"}, {"target_timestamp": self.start + 86400}):
            with self.subTest(update=update):
                self.write_archive(self.start, 285485, manifest_update=update)
                self.assertEqual(self.store._cutoff_sample("official_stats", self.start), ({}, {}))
                self.assertEqual(self.sample_rows(), [])

    def test_out_of_tolerance_upstream_receipt_is_not_a_cutoff_sample(self):
        for offset in (-91, 91, 3600):
            with self.subTest(offset=offset):
                self.write_archive(self.start, 285485, offset=offset)
                self.assertEqual(self.store._cutoff_sample("official_stats", self.start), ({}, {}))
                self.assertEqual(self.sample_rows(), [])

    def test_stale_or_untrusted_source_cannot_be_recovered(self):
        for update in ({"status": "stale"}, {"status": "stale_cache"},
                       {"status": "unavailable"}, {"source": "snapshot"},
                       {"source": "https://explorer.marschain.net.invalid/api/stats"}):
            with self.subTest(update=update):
                self.write_archive(self.start, 285485, source_update=update)
                self.assertEqual(self.store._cutoff_sample("official_stats", self.start), ({}, {}))
                self.assertEqual(self.sample_rows(), [])

    def test_archive_generation_time_cannot_replace_missing_real_fetched_at(self):
        for update in ({"fetched_at": None}, {"fetched_at": ""},
                       {"fetched_at": None, "checked_at": self.start + 10}):
            with self.subTest(update=update):
                self.write_archive(self.start, 285485, source_update=update)
                self.assertEqual(self.store._cutoff_sample("official_stats", self.start), ({}, {}))
                self.assertEqual(self.sample_rows(), [])

    def test_invalid_total_or_partial_official_payload_is_not_a_sample(self):
        for total in (None, -1, 285485.5, True, "bad-count"):
            with self.subTest(total=total):
                self.write_archive(self.start, total, meta_update={"network_current_price": "0.01"})
                self.assertEqual(self.store._cutoff_sample("official_stats", self.start), ({}, {}))
                self.assertEqual(self.sample_rows(), [])

    def test_nearer_partial_http_sample_does_not_erase_valid_address_total(self):
        self.store._sample_official_stats({"totalAddresses": 285485},
                                         {"status": "ok", "fetched_at": self.start + 43})
        before, before_source = self.store._cutoff_sample("official_stats", self.start)
        self.assertEqual(before["total_wallets"], 285485)
        # A successful HTTP response can have only price/circulation fields.
        # Its closer receipt must not evict the previous real address sample.
        self.store._sample_official_stats({"currentPrice": "0.012", "totalCirculation": "123"},
                                         {"status": "ok", "fetched_at": self.start + 1})
        after, after_source = self.store._cutoff_sample("official_stats", self.start)
        self.assertEqual(after["total_wallets"], 285485)
        self.assertEqual(after_source["received_at"], before_source["received_at"])

    def test_valid_archive_replaces_closer_invalid_existing_sample(self):
        self.write_archive(self.start, 285485, offset=43)
        invalid = {"total_wallets": None, "network_current_price": 0.01}
        provenance = {"source": EXPLORER_API + "/stats", "received_at": self.start + 1,
                      "cutoff_timestamp": self.start, "offset_seconds": 1,
                      "timing_status": "sampled_near_cutoff", "exact_cutoff": False}
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO preview_metric_cutoff_samples VALUES (?,?,?,?,?)",
                               ("official_stats", self.start, self.start + 1,
                                json.dumps(invalid), json.dumps(provenance)))
        recovered, source = self.store._cutoff_sample("official_stats", self.start)
        self.assertEqual(recovered["total_wallets"], 285485)
        self.assertEqual(source["received_at"], self.start + 43)
        self.assertEqual(len(self.sample_rows()), 1)

    def test_symlinked_archive_components_are_not_trusted(self):
        for component in ("public.json", "manifest.json", "directory"):
            with self.subTest(component=component):
                # Each case uses a fresh cutoff, so accepted samples from one
                # case could not mask another filesystem validation failure.
                cutoff = self.start - (1 + ("public.json", "manifest.json", "directory").index(component)) * 86400
                directory = self.write_archive(cutoff, 285000)
                target = directory if component == "directory" else directory / component
                moved = self.root / (component.replace(".", "-") + "-symlink-target")
                target.rename(moved)
                target.symlink_to(moved, target_is_directory=component == "directory")
                self.assertEqual(self.store._cutoff_sample("official_stats", cutoff), ({}, {}))
                self.assertEqual(self.sample_rows(), [])

    def test_missing_historical_endpoint_does_not_invent_daily_or_period_growth(self):
        self.write_archive(self.end, 285598)
        meta = self.public_meta()
        for days in (7, 30):
            meta[f"period_{days}d_end_timestamp"] = self.end
        original = copy.deepcopy(meta)
        self.store._apply_sampled_growth(meta)
        self.assertIsNone(meta["statistics_window_new_candidate_address_count"])
        self.assertEqual(meta["statistics_window_new_candidate_address_sample"]["status"], "unavailable")
        for days in (7, 30):
            self.assertIsNone(meta[f"period_{days}d_new_candidate_address_count"])
        self.assertEqual(meta["explorer_total_addresses"], original["explorer_total_addresses"])

    def test_incomplete_http_stats_preserves_old_payload_and_real_receipt(self):
        full = {"totalAddresses": 285485, "totalCirculation": "1230000000000000000",
                "currentPrice": "0.01", "highestPrice": "0.02"}
        responses = [full, {"currentPrice": "0.012"},
                     {"totalAddresses": 285486, "currentPrice": "0.013"}]
        calls = []

        def fake_fetch(url, timeout=10, payload=None):
            calls.append((url, payload))
            self.assertEqual(url, EXPLORER_API + "/stats")
            self.assertIsNone(payload, "this path must not issue RPC calls")
            return copy.deepcopy(responses.pop(0))

        store = MetricStore(self.database, fetcher=fake_fetch, cache_seconds=0)
        original_receipt = self.start + 43
        with mock.patch("node_preview.metrics.time.monotonic", return_value=100.0):
            actual, source = store._cached_external("/stats", original_receipt)
            self.assertEqual(actual, full)
            self.assertEqual(source["status"], "ok")
            self.assertEqual(source["fetched_at"], original_receipt)
            for now in (original_receipt + 1, original_receipt + 2):
                with self.subTest(now=now):
                    actual, source = store._cached_external("/stats", now)
                    self.assertEqual(actual, full)
                    self.assertEqual(source["status"], "stale_cache")
                    self.assertEqual(source["fetched_at"], original_receipt)
                    self.assertEqual(source["checked_at"], now)
                    self.assertIn("incomplete official stats", source["error"])
        with sqlite3.connect(self.database) as connection:
            payload, fetched_at, checked_at = connection.execute(
                "SELECT payload,fetched_at,checked_at FROM preview_metric_cache WHERE key=?",
                (EXPLORER_API + "/stats",)
            ).fetchone()
        self.assertEqual(json.loads(payload), full)
        self.assertEqual(fetched_at, original_receipt)
        self.assertEqual(checked_at, original_receipt + 2)
        self.assertEqual(len(calls), 3)

    def test_independent_sampler_outside_window_does_not_request_http(self):
        now = self.end + 91
        report = capture(self.root, now=now, store=self.store)
        self.assertEqual(report["state"], "outside_sampling_window")
        self.assertEqual(report["cutoff_timestamp"], self.end)
        self.assertEqual(self.fetch_calls, [])
        self.assertEqual(self.sample_rows(), [])
        saved = json.loads((self.root / "state/official-address-sampler-status.json").read_text())
        self.assertEqual(saved, report)

    def test_independent_sampler_records_actual_http_receipt_without_rpc(self):
        calls = []
        stats = {"totalAddresses": 285598, "totalCirculation": "1230000000000000000",
                 "currentPrice": "0.01"}

        def fake_fetch(url, timeout=10, payload=None):
            calls.append((url, payload))
            self.assertEqual(url, EXPLORER_API + "/stats")
            self.assertIsNone(payload, "address sampling must not depend on chain RPC")
            return copy.deepcopy(stats)

        store = MetricStore(self.database, fetcher=fake_fetch, cache_seconds=0)
        now = self.end + 25
        # The response arrives two seconds after the request started.  Keep
        # that real receipt instead of stamping the scheduled start time.
        with mock.patch("node_preview.metrics.time.monotonic", side_effect=[100.0, 102.25]):
            report = capture(self.root, now=now, store=store)
        self.assertEqual(calls, [(EXPLORER_API + "/stats", None)])
        self.assertEqual(report["state"], "sample_available")
        self.assertEqual(report["address_total"], 285598)
        self.assertEqual(report["fetch_status"], "ok")
        self.assertEqual(report["fetched_at"], now + 2)
        self.assertEqual(report["last_success"]["received_at"], now + 2)
        sample, source = store._cutoff_sample("official_stats", self.end)
        self.assertEqual(sample["total_wallets"], 285598)
        self.assertEqual(source["received_at"], now + 2)
        self.assertEqual(source["offset_seconds"], 27)

    def test_history_recovery_retains_prior_verified_creation_and_other_facts(self):
        self.write_comparable_archives()
        previous = {"generated_at": self.start + 600,
                    "statistics_window_end_timestamp": self.start,
                    "source": "verified_node_window", "source_block": 100,
                    "values": {"total_wallets": None, "daily_new_power": 7,
                               "daily_burned": 77},
                    "missing_fields": ["total_wallets", "daily_new_addresses"],
                    "field_sources": {"daily_burned": {"source": "verified_boundary_state_difference"}}}
        self.store._put("history", [previous])
        meta = self.public_meta()
        meta.update(statistics_window_end_total_power=130,
                    statistics_window_new_power=9, statistics_window_burned_tokens=88,
                    preview_daily_status={"state": "verified_node_window", "source_block": 200,
                                          "field_sources": {"power": {"end": {"burned": 12345}}}})
        self.store._enrich_daily_history(meta, self.end + 3600)
        snapshots = {item["statistics_window_end_timestamp"]: item for item in self.store.history()}
        restored = snapshots[self.start]
        self.assertEqual(restored["values"]["total_wallets"], 285485)
        self.assertEqual(restored["generated_at"], previous["generated_at"])
        self.assertEqual(restored["source_block"], previous["source_block"])
        self.assertEqual(restored["values"]["daily_new_power"], 7)
        self.assertEqual(restored["values"]["daily_burned"], 77)
        self.assertEqual(restored["field_sources"]["daily_burned"], previous["field_sources"]["daily_burned"])
        self.assertNotIn("total_wallets", restored["missing_fields"])
        self.assertIn("daily_new_addresses", restored["missing_fields"])
        self.assertEqual(snapshots[self.end]["values"]["daily_new_addresses"], 113)
        before = self.store._get("history")
        self.store._enrich_daily_history(meta, self.end + 3660)
        self.assertEqual(self.store._get("history"), before)


if __name__ == "__main__":
    unittest.main()
