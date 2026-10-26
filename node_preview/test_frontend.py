"""Checks for the compatibility adapter and public-data boundary."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from node_preview.frontend import build_preview, public_payload


def fixture():
    return {
        "meta": {
            "generated_at": 1792771200,
            "network_total_power": "100000000000",
            "candidate_count": 203000,
            "positive_power_count": 120000,
            "explorer_total_addresses": 285398,
            "rpc_url": "PRIVATE_RPC_URL",
            "mars_balance": "PRIVATE_MARS_BALANCE",
            "node_preview": {
                "version": "fixture-v1", "source_block": 6849000,
                "known_addresses": 203000, "state": "calibrating",
                "calibration_done": 100000, "calibration_total": 203000,
                "generated_at": "2026-10-23T16:00:00+00:00",
            },
        },
        "rows": [
            {"rank": index + 1, "address": "0x" + format(index, "040x"),
             "power": str(10000000000 - index * 100000),
             "mars_balance": "PRIVATE_MARS_BALANCE", "wei": "PRIVATE_WEI"}
            for index in range(120)
        ],
    }


class FrontendTests(unittest.TestCase):
    def test_public_data_boundary(self):
        public = public_payload(fixture())
        encoded = json.dumps(public)
        self.assertEqual(len(public["rows"]), 100)
        self.assertEqual(set(public["rows"][0]), {"rank", "address", "power"})
        for private in ("PRIVATE_RPC_URL", "PRIVATE_MARS_BALANCE", "PRIVATE_WEI"):
            self.assertNotIn(private, encoded)
        self.assertEqual(public["view"]["schema"], 1)
        self.assertEqual(len(public["view"]["desktop"]["metrics"]), 6)
        mobile_metrics = public["view"]["mobile"]["metrics"]
        core_labels = {"全网总算力", "全网流通量", "当前价格", "每日产币量", "钱包地址", "每日交易币量"}
        period_labels = {"7 天增长", "30 天增长"}
        self.assertEqual(len(mobile_metrics), 8)
        self.assertEqual({label for label, card in mobile_metrics.items() if card["trend_index"] < 6},
                         core_labels)
        self.assertEqual({label for label, card in mobile_metrics.items() if card["trend_index"] >= 6},
                         period_labels)

    def test_layout_artifacts_stay_static_until_code_changes(self):
        payload = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            initial = build_preview(payload, temporary)
            path = Path(temporary) / "index.html"
            previous_mtime = path.stat().st_mtime_ns
            payload["rows"][0]["power"] = "999999999999"
            payload["meta"]["node_preview"]["version"] = "fixture-v2"
            second = build_preview(payload, temporary)
            self.assertEqual(initial, second)
            self.assertEqual(previous_mtime, path.stat().st_mtime_ns)

    def test_generated_pages_have_live_hooks_and_no_paid_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            build_preview(fixture(), temporary)
            for filename in ("index.html", "m/index.html"):
                html = (Path(temporary) / filename).read_text(encoding="utf-8")
                self.assertIn('src="/live_preview.js"', html)
                self.assertIn("mars-preview-data", html)
                self.assertIn("本期倒计时已结束", html)
                self.assertNotIn("const cards = Array.from(", html)
                self.assertNotIn("PRIVATE_MARS_BALANCE", html)
                self.assertFalse(re.search(r'<[^>]+\sdata-paid-panel(?:\s|=|>)', html))
                match = re.search(r'<script id="rankData" type="application/json">(.*?)</script>', html, re.S)
                self.assertEqual(len(json.loads(match.group(1))["rows"]), 100)

    def test_json_script_html_safety(self):
        payload = fixture()
        private_error = "PRIVATE_SOURCE_ERROR_MUST_NOT_LEAK"
        public_version = "</ScRiPt><script>public_probe()</script>"
        payload["meta"]["node_preview"]["source_error"] = private_error
        payload["meta"]["node_preview"]["version"] = public_version
        public = public_payload(payload)
        self.assertNotIn("source_error", public["meta"]["node_preview"])
        self.assertNotIn(private_error, json.dumps(public))
        self.assertEqual(public["meta"]["node_preview"]["version"], public_version)
        with tempfile.TemporaryDirectory() as temporary:
            build_preview(payload, temporary)
            for filename in ("index.html", "m/index.html"):
                with self.subTest(filename=filename):
                    html = (Path(temporary) / filename).read_text(encoding="utf-8")
                    self.assertNotIn("</ScRiPt>", html)
                    self.assertNotIn(private_error, html)
                    match = re.search(r'<script id="rankData" type="application/json">(.*?)</script>', html, re.S)
                    self.assertIsNotNone(match)
                    self.assertIn("\\u003c/ScRiPt>", match.group(1))
                    node = json.loads(match.group(1))["meta"]["node_preview"]
                    self.assertNotIn("source_error", node)
                    self.assertEqual(node["version"], public_version)


if __name__ == "__main__":
    unittest.main()
