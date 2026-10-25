"""Focused regressions for compact cards and the reviewed display wording."""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from node_preview.frontend import build_preview, public_payload
from node_preview.metrics import LEGACY_MODEL_NOTE, LEGACY_SAMPLE_NOTE


CORE_LABELS = (
    "全网总算力", "全网流通量", "当前价格", "每日产币量", "钱包地址", "每日交易币量",
)


def fixture() -> dict:
    meta = {
        "generated_at": 1792886525,
        "network_total_power": "100000000000",
        "network_total_circulation_tokens": "100000000",
        "network_total_circulation_display": "1亿枚",
        "network_total_burned_tokens": "10000",
        "network_total_burned_display": "1万枚",
        "network_current_price": 0.02,
        "network_current_price_display": "0.02",
        "candidate_count": 203000,
        "positive_power_count": 120000,
        "explorer_total_addresses": 285598,
        "statistics_window_start_timestamp": 1792800000,
        "statistics_window_end_timestamp": 1792886400,
        "statistics_window_label": "2026-10-24 08:00 至 2026-10-25 08:00",
        "statistics_window_new_power": "123456",
        "statistics_window_new_candidate_address_count": 113,
        "statistics_window_new_candidate_address_display": "113个（近截点采样）",
        "statistics_window_active_wallet_address_count": 99,
        "statistics_window_active_transactions_seen": 123,
        "statistics_window_transaction_volume_wei": "2000000000000000000000",
        "statistics_window_transaction_volume_display": "2,000枚",
        "statistics_window_burned_tokens": "123",
        "statistics_window_burned_display": "123枚",
        "emission_daily_total_tokens": "1000000",
        "emission_daily_total_display": "100万枚",
        "emission_daily_miner_tokens": "750000",
        "emission_daily_node_tokens": "250000",
        "power_required_per_mars_daily": 100000,
        "node_preview": {
            "version": "compact-fixture-v1", "source_block": 6932474,
            "source_block_timestamp": 1792886500,
            "known_addresses": 203000, "state": "ready",
            "generated_at": "2026-10-25T00:02:05+00:00",
        },
        "metric_trends": {},
    }
    for days, count in ((7, 777), (30, 3333)):
        prefix = f"period_{days}d"
        meta.update({
            f"{prefix}_start_timestamp": 1792886400 - days * 86400,
            f"{prefix}_end_timestamp": 1792886400,
            f"{prefix}_complete": True,
            f"{prefix}_new_power": str(count * 100),
            f"{prefix}_new_candidate_address_count": count,
            f"{prefix}_new_candidate_address_display": f"{count}个",
            f"{prefix}_burned_tokens": str(count),
            f"{prefix}_burned_display": f"{count}枚",
        })
    for key, values in {
        "network_total_power": (90000000000, 100000000000),
        "total_wallets": (285485, 285598),
        "daily_new_addresses": (100, 113),
        "daily_active_addresses": (88, 99),
        "daily_new_power": (100000, 123456),
        "network_total_circulation": (99000000, 100000000),
        "total_burned": (9877, 10000),
        "daily_burned": (100, 123),
        "daily_emission": (1000000, 1000000),
        "daily_transaction_volume": (1000000000000000000000, 2000000000000000000000),
        "daily_transaction_count": (111, 123),
        "network_current_price": (0.01, 0.02),
    }.items():
        meta["metric_trends"][key] = {"values": [
            {"label": label, "value": value}
            for label, value in zip(("2026-10-24", "2026-10-25"), values)
        ]}
    meta["metric_trends"]["total_wallets"]["history_note"] = LEGACY_SAMPLE_NOTE
    meta["metric_trends"]["daily_emission"]["history_note"] = LEGACY_MODEL_NOTE
    return {"meta": meta, "rows": [{"rank": 1, "address": "0x" + "a" * 40, "power": "1000000"}]}


def metric_cards(markup: str) -> dict[str, tuple[str, str]]:
    result = {}
    for match in re.finditer(r"<article\b([^>]*)>(.*?)</article>", markup, re.S):
        label = re.search(r'data-label="([^"]+)"', match.group(1))
        if label:
            result[label.group(1)] = (match.group(1), match.group(2))
    return result


class CompactCoreCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = fixture()
        cls.public = public_payload(cls.raw)
        with tempfile.TemporaryDirectory() as temporary:
            build_preview(cls.raw, temporary)
            cls.pages = {device: (Path(temporary) / filename).read_text()
                         for device, filename in (("desktop", "index.html"), ("mobile", "m/index.html"))}

    def test_static_and_live_core_cards_have_no_outer_charts(self):
        for device, html in self.pages.items():
            static = metric_cards(html)
            live = self.public["view"][device]["metrics"]
            for label in CORE_LABELS:
                attributes, inner = static[label]
                with self.subTest(device=device, label=label):
                    self.assertIn("data-core-compact", attributes)
                    self.assertIn('role="button"', attributes)
                    self.assertIn('tabindex="0"', attributes)
                    for markup in (inner, live[label]["html"]):
                        self.assertNotIn('class="metric-trend', markup)
                        self.assertNotIn('class="power-mini-pair', markup)
                        self.assertIn("查看趋势", markup)
                    index = re.search(r'data-trend-index="(\d+)"', attributes)
                    self.assertEqual(int(index.group(1)), live[label]["trend_index"])

    def test_period_address_values_are_not_replaced_with_daily_addresses(self):
        for days, count in ((7, 777), (30, 3333)):
            expected = f"{count}个"
            self.assertIn(expected, self.public["view"]["desktop"]["growth"])
            mobile_key = f"{days} 天增长"
            live = self.public["view"]["mobile"]["metrics"][mobile_key]["html"]
            static = metric_cards(self.pages["mobile"])[mobile_key][1]
            for markup in (live, static):
                self.assertIn(expected, markup)
                self.assertNotIn("113个", markup)
                self.assertIn('class="power-mini-pair', markup)
            desktop_periods = re.findall(r'<article class="growth-card[^>]*>(.*?)</article>',
                                         self.pages["desktop"], re.S)
            card = next(card for card in desktop_periods if f"{days} 天新增" in card)
            self.assertIn(f"<span>新增地址</span><b>{expected}</b>", card)
            self.assertNotIn("113个", card)

    def test_wallet_history_method_note_is_retained_in_static_and_live_popup(self):
        for device, html in self.pages.items():
            static = json.loads(re.search(r'<script id="metricTrendData" type="application/json">(.*?)</script>',
                                          html, re.S).group(1))
            for trends in (static, self.public["view"][device]["trends"]):
                wallets = next(trend for trend in trends if trend["key"] == "total_wallets")
                self.assertIn("每日新增地址", wallets["note"])
                self.assertIn(LEGACY_SAMPLE_NOTE, wallets["note"])
                self.assertEqual(wallets["note"].count(LEGACY_SAMPLE_NOTE), 1)

    def test_estimated_emission_name_keeps_stable_key_and_english_copy(self):
        for device, html in self.pages.items():
            attrs, inner = metric_cards(html)["每日产币量"]
            self.assertIn('aria-label="查看预计日产量趋势"', attrs)
            self.assertIn(">预计日产量<", inner)
            self.assertNotIn(">每日产币量<", inner)
            live = self.public["view"][device]["metrics"]["每日产币量"]["html"]
            self.assertIn(">预计日产量<", live)
            self.assertIn('"预计日产量": "Estimated Daily Emission"', html)
            output = next(t for t in self.public["view"][device]["trends"] if t["key"] == "daily_emission")
            series = next(s for s in output["series"] if s["key"] == "daily_emission")
            self.assertEqual(series["label"], "预计日产量")
            self.assertIn(LEGACY_MODEL_NOTE, output["note"])

    def test_daily_dates_names_sampling_disclosure_and_units(self):
        interval = "2026-10-24 08:00 至 2026-10-25 08:00"
        self.assertEqual(self.public["meta"]["statistics_window_new_candidate_address_display"],
                         "113个（近截点采样）")
        for device, html in self.pages.items():
            self.assertIn("每日统计 · 北京时间 08:00 结算", html)
            self.assertIn("相邻统计日采样差额", html)
            live = self.public["view"][device]["metrics"]
            for label in ("全网总算力", "全网流通量", "钱包地址", "每日交易币量"):
                self.assertIn(interval, live[label]["html"])
            self.assertIn("每日新增地址", live["钱包地址"]["html"])
            self.assertIn("每日活跃地址", live["钱包地址"]["html"])
            self.assertIn("113个", live["钱包地址"]["html"])
            self.assertNotIn("近截点采样", live["钱包地址"]["html"])
            self.assertIn("每日销毁量", live["全网流通量"]["html"])
            self.assertIn("每日交易量", live["每日交易币量"]["html"])
            self.assertIn("每日交易笔数", live["每日交易币量"]["html"])
            self.assertIn("统计区间内交易币量 · MARS", live["每日交易币量"]["html"])
            self.assertIn("activePeriod = '7'", html)

    def test_public_facts_and_input_are_unchanged(self):
        canonical = copy.deepcopy(self.public)
        before = copy.deepcopy(canonical)
        second = public_payload(canonical)
        self.assertEqual(canonical, before)
        self.assertEqual(second["meta"], before["meta"])
        self.assertEqual(second["rows"], before["rows"])
        self.assertEqual(second["meta"]["metric_trends"], self.public["meta"]["metric_trends"])
        self.assertEqual(self.raw, fixture())


if __name__ == "__main__":
    unittest.main()
