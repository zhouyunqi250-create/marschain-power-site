"""Publication must not relabel unfinished or stopped collection as fresh data."""
import copy
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from node_preview.collector import initialize, meta_set
from node_preview.publisher import SnapshotPending, compose
from node_preview.test_frontend import fixture

HASH = "0x" + "a" * 64


class Node:
    def __init__(self, *args, **kwargs):
        pass

    def block(self, number):
        return {"hash": HASH}


class Metrics:
    rpc_url = "mock-node"
    stale = False

    def refresh(self, meta, block):
        result = copy.deepcopy(meta)
        result.update(network_total_power=100, network_total_burned_tokens=0)
        result["preview_metric_sources"] = {key: {"status": "stale_cache" if self.stale else "ok"}
            for key in ("network_total_power", "network_total_burned_tokens")}
        return result


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "state").mkdir()
        self.db = sqlite3.connect(self.root / "state/preview.sqlite3")
        initialize(self.db)
        now = int(time.time())
        with self.db:
            for key, value in {"checkpoint_block": 500, "checkpoint_hash": HASH, "checkpoint_timestamp": now - 20,
                "safe_head": 500, "power_state_block": 500, "global_power_block": 500,
                "last_success": now, "calibration_done": True, "state": "ready"}.items():
                meta_set(self.db, key, value)
            account = "0x" + "a" * 40
            self.db.execute("INSERT INTO addresses VALUES (?)", (account,))
            self.db.execute("INSERT INTO preview_power_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (account, "100", "0", "0", None, None, 500, now, 0, "0", 0, 0))
        self.seeds = {"payload": fixture()}

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def test_collecting_partial_future_state_is_not_publishable(self):
        with self.db:
            for key, value in {"state": "collecting", "safe_head": 700, "power_state_block": 700}.items():
                meta_set(self.db, key, value)
        with self.assertRaises(SnapshotPending):
            compose(self.root, self.seeds, Metrics())

    @patch("node_preview.publisher.RPC", Node)
    def test_stopped_collector_cannot_be_given_a_fresh_publish_time(self):
        with self.db:
            meta_set(self.db, "last_success", int(time.time()) - 240)
        with self.assertRaises(SnapshotPending):
            compose(self.root, self.seeds, Metrics())

    @patch("node_preview.publisher.RPC", Node)
    def test_cached_node_global_values_do_not_mix_with_fresh_ranking(self):
        metrics = Metrics()
        metrics.stale = True
        with self.assertRaises(SnapshotPending):
            compose(self.root, self.seeds, metrics)

    @patch("node_preview.publisher.RPC", Node)
    def test_ready_snapshot_uses_verified_values_and_public_boundary(self):
        payload, report = compose(self.root, self.seeds, Metrics())
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["power"], "100")
        self.assertEqual(payload["meta"]["node_preview"]["source_block"], 500)
        self.assertEqual(payload["meta"]["node_preview"]["calibration_count"], 1)
        self.assertTrue(payload["meta"]["target_met"])
        self.assertEqual(report["state"], "ready")
        self.assertFalse(payload["meta"]["paid_download"]["enabled"])
        self.assertNotIn("PRIVATE_MARS_BALANCE", str(payload))

    @patch("node_preview.publisher.RPC", Node)
    def test_fully_committed_collecting_label_is_reported_as_ready(self):
        with self.db:
            meta_set(self.db, "state", "collecting")
        payload, report = compose(self.root, self.seeds, Metrics())
        self.assertEqual(report["state"], "ready")
        self.assertEqual(payload["meta"]["node_preview"]["state"], "ready")


if __name__ == "__main__":
    unittest.main()
