"""Offline regression checks for the official block-based emission model."""

from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from node_preview.christmas import (
    DAY_SECONDS, EVENT_SECONDS, TARGET_TIMESTAMP,
    christmas_forecast, oracle_burn_forecast,
)
from node_preview.emission import (
    BASE_REWARD_WEI, BLOCKS_PER_DAY, HALVING_BLOCKS, HALVING_OFFSET,
    MAX_REWARD_BLOCK, MODEL_VERSION, WEI_PER_TOKEN,
    block_reward_wei, build_emission_meta, next_halving_block,
    projected_emission_wei, sum_block_rewards_wei,
)
from node_preview.metrics import MetricStore, POWER_CONTRACT


FIRST_HALVING = 12_823_208
POWER = 10**15
NOW = TARGET_TIMESTAMP - 30 * DAY_SECONDS


def verified_meta(block=FIRST_HALVING - 2 * BLOCKS_PER_DAY - 1):
    meta = build_emission_meta(block, NOW, POWER,
                               observed_reward_wei=block_reward_wei(block))
    meta.update(
        emission_model_status="verified", generated_at=NOW,
        network_total_power=POWER,
        network_total_circulation_tokens=str(19_000_000_000 * WEI_PER_TOKEN),
        preview_metric_sources={
            "network_total_circulation_tokens": {"status": "ok", "fetched_at": NOW},
        },
        # Deliberately leave a plausible legacy model available. Invalid new
        # evidence must not recover to these date-based values.
        emission_genesis_timestamp=NOW - 500 * DAY_SECONDS,
        chain_days_online=500,
    )
    return meta


class BlockRewardTests(unittest.TestCase):
    def test_official_first_boundary_before_at_after(self):
        self.assertEqual(HALVING_BLOCKS - HALVING_OFFSET, FIRST_HALVING)
        self.assertEqual(block_reward_wei(FIRST_HALVING - 1), 7_750_496_031_750_000_000_000)
        self.assertEqual(block_reward_wei(FIRST_HALVING), 3_875_248_015_875_000_000_000)
        self.assertEqual(block_reward_wei(FIRST_HALVING + 1), 3_875_248_015_875_000_000_000)

    def test_second_boundary_and_zero_reward_follow_integer_shift(self):
        boundary = FIRST_HALVING + HALVING_BLOCKS
        self.assertEqual(block_reward_wei(boundary - 1), BASE_REWARD_WEI >> 1)
        self.assertEqual(block_reward_wei(boundary), BASE_REWARD_WEI >> 2)
        zero_boundary = BASE_REWARD_WEI.bit_length() * HALVING_BLOCKS - HALVING_OFFSET
        self.assertEqual(block_reward_wei(zero_boundary), 0)

    def test_invalid_blocks_are_rejected(self):
        for value in (-1, True, 1.5, "1", MAX_REWARD_BLOCK + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                block_reward_wei(value)

    def test_next_boundary_is_strictly_after_current_block(self):
        self.assertEqual(next_halving_block(FIRST_HALVING - 1), FIRST_HALVING)
        self.assertEqual(next_halving_block(FIRST_HALVING), FIRST_HALVING + HALVING_BLOCKS)

    def test_reward_sum_splits_exactly_at_boundary(self):
        self.assertEqual(sum_block_rewards_wei(FIRST_HALVING - 2, 5),
                         2 * BASE_REWARD_WEI + 3 * (BASE_REWARD_WEI >> 1))
        self.assertEqual(sum_block_rewards_wei(FIRST_HALVING, 0), 0)

    def test_observed_contract_reward_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_emission_meta(FIRST_HALVING, NOW, POWER,
                                observed_reward_wei=BASE_REWARD_WEI)

    def test_daily_rates_and_shares_change_at_same_boundary(self):
        old = build_emission_meta(FIRST_HALVING - 1, NOW, POWER)
        new = build_emission_meta(FIRST_HALVING, NOW, POWER)
        self.assertEqual(old["emission_daily_total_tokens"], 223_214_285.7144)
        self.assertEqual(new["emission_daily_total_tokens"], 111_607_142.8572)
        for key in ("emission_daily_total_tokens", "emission_daily_miner_tokens",
                    "emission_daily_node_tokens"):
            self.assertEqual(old[key], 2 * new[key])
        self.assertAlmostEqual(new["emission_daily_miner_tokens"],
                               new["emission_daily_total_tokens"] * 0.75)
        self.assertAlmostEqual(new["emission_daily_node_tokens"],
                               new["emission_daily_total_tokens"] * 0.25)


class EmissionProjectionTests(unittest.TestCase):
    def setUp(self):
        # The first future slot is B + 1: exactly two full model days precede
        # the boundary; the remaining six days receive half the reward.
        self.anchor = FIRST_HALVING - 2 * BLOCKS_PER_DAY - 1

    def test_eight_days_are_two_old_plus_six_new_days(self):
        projected = projected_emission_wei(NOW, NOW + EVENT_SECONDS, self.anchor, NOW)
        expected = (2 * BLOCKS_PER_DAY * BASE_REWARD_WEI
                    + 6 * BLOCKS_PER_DAY * (BASE_REWARD_WEI >> 1))
        self.assertEqual(projected, Decimal(expected))
        self.assertLess(projected, BASE_REWARD_WEI * BLOCKS_PER_DAY * 8)

    def test_adjacent_intervals_are_additive_across_halving(self):
        split = NOW + 2 * DAY_SECONDS
        left = projected_emission_wei(NOW, split, self.anchor, NOW)
        right = projected_emission_wei(split, NOW + EVENT_SECONDS, self.anchor, NOW)
        whole = projected_emission_wei(NOW, NOW + EVENT_SECONDS, self.anchor, NOW)
        self.assertEqual(left + right, whole)

    def test_partial_model_slots_do_not_overlap_or_drop_emission(self):
        # An offset that splits a three-second slot also splits its reward.
        with localcontext() as context:
            context.prec = 120
            start, split, end = NOW - 7, NOW + 2 * DAY_SECONDS + 1, NOW + EVENT_SECONDS + 2
            parts = (projected_emission_wei(start, split, self.anchor, NOW)
                     + projected_emission_wei(split, end, self.anchor, NOW))
            whole = projected_emission_wei(start, end, self.anchor, NOW)
            self.assertLessEqual(abs(parts - whole), Decimal("1e-80"))

    def test_empty_interval_and_invalid_order(self):
        self.assertEqual(projected_emission_wei(NOW, NOW, self.anchor, NOW), 0)
        with self.assertRaises(ValueError):
            projected_emission_wei(NOW + 1, NOW, self.anchor, NOW)


class ForecastReferenceTests(unittest.TestCase):
    forecasts = (oracle_burn_forecast, christmas_forecast)

    def test_both_forecasts_accept_verified_reward_anchor(self):
        meta = verified_meta()
        original = deepcopy(meta)
        for forecast in self.forecasts:
            with self.subTest(forecast=forecast.__name__):
                result = forecast(meta)
                self.assertEqual(result["state"], "ready")
                self.assertEqual(result["current_daily_emission"], 223_214_285.7144)
                self.assertGreater(result["per_100m_power"], 0)
        self.assertEqual(meta, original)

    def test_oracle_eight_day_estimate_uses_segmented_rewards(self):
        meta = verified_meta()
        result = oracle_burn_forecast(meta)
        expected = (2 * BLOCKS_PER_DAY * BASE_REWARD_WEI
                    + 6 * BLOCKS_PER_DAY * (BASE_REWARD_WEI >> 1)) / WEI_PER_TOKEN
        self.assertEqual(result["event_emission"], expected)
        self.assertEqual(result["source_gap_emission"], 0)

    def test_source_gap_and_christmas_event_use_adjacent_intervals(self):
        meta = verified_meta()
        fetched_at = NOW - 5 * DAY_SECONDS - 1
        meta["preview_metric_sources"]["network_total_circulation_tokens"]["fetched_at"] = fetched_at
        block, stamp = meta["emission_reference_block"], meta["emission_reference_timestamp"]
        oracle, christmas = oracle_burn_forecast(meta), christmas_forecast(meta)
        self.assertEqual(oracle["source_timestamp"], fetched_at)
        self.assertAlmostEqual(
            oracle["source_gap_emission"] + oracle["event_emission"],
            float(projected_emission_wei(fetched_at, NOW + EVENT_SECONDS, block, stamp)
                  / WEI_PER_TOKEN), delta=1e-5)
        self.assertAlmostEqual(
            christmas["remaining_emission"] + christmas["event_emission"],
            float(projected_emission_wei(fetched_at, TARGET_TIMESTAMP + EVENT_SECONDS, block, stamp)
                  / WEI_PER_TOKEN), delta=1e-5)

    def test_missing_anchor_does_not_fall_back_to_legacy_date_model(self):
        for key in ("emission_model_version", "emission_model_status", "emission_reference_block",
                    "emission_reference_timestamp", "emission_block_reward_wei"):
            for forecast in self.forecasts:
                with self.subTest(key=key, forecast=forecast.__name__):
                    meta = verified_meta()
                    del meta[key]
                    self.assertEqual(forecast(meta)["state"], "unavailable")

    def test_unavailable_inconsistent_or_future_anchor_is_rejected(self):
        changes = (
            {"emission_model_status": "unavailable"},
            {"emission_model_version": "old-date-model"},
            {"emission_block_reward_wei": "1"},
            {"emission_reference_timestamp": NOW + 1},
        )
        for change in changes:
            for forecast in self.forecasts:
                with self.subTest(change=change, forecast=forecast.__name__):
                    meta = verified_meta()
                    meta.update(change)
                    result = forecast(meta)
                    self.assertEqual(result["state"], "unavailable")
                    self.assertNotIn("event_emission", result)
                    self.assertTrue(all(value == "—" for value in result["display"].values()))


class MetricEmissionRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # This sentinel ensures an accidental unmocked transport fails offline.
        def no_network(*args, **kwargs):
            raise AssertionError("network access is not permitted in this test")
        self.store = MetricStore(Path(self.temp.name) / "metrics.sqlite3", fetcher=no_network)
        self.block = FIRST_HALVING - 1
        self.block_info = {"number": self.block, "hash": "0xabc", "timestamp": NOW}

    def rpc(self, method, params):
        if method == "eth_getBlockByNumber":
            self.assertEqual(params, [hex(self.block), False])
            return {"number": hex(self.block), "hash": "0xabc", "timestamp": hex(NOW)}
        self.assertEqual(method, "eth_call")
        self.assertEqual(params, [
            {"to": POWER_CONTRACT, "data": "0x94f7f62b" + f"{self.block:064x}"},
            hex(self.block),
        ])
        return "0x" + f"{block_reward_wei(self.block):064x}"

    def assert_unavailable_and_cleared(self, meta, sources):
        self.assertEqual(meta["emission_model_status"], "unavailable")
        self.assertEqual(meta["emission_model_version"], MODEL_VERSION)
        self.assertEqual(sources["emission"]["status"], "unavailable")
        kept = {"emission_model_version", "emission_total_supply_cap_tokens",
                "emission_total_supply_cap_display", "emission_miner_share", "emission_node_share"}
        for key in build_emission_meta(0, 1, 0):
            if key in kept:
                continue
            with self.subTest(key=key):
                self.assertEqual(meta[key], "待刷新" if key.endswith("_display") else None)
        self.assertEqual(meta["network_total_power"], POWER)
        self.assertEqual(meta["emission_total_supply_cap_tokens"], 200_000_000_000)
        self.assertEqual(oracle_burn_forecast(meta)["state"], "unavailable")
        self.assertEqual(christmas_forecast(meta)["state"], "unavailable")

    def test_refresh_verifies_reward_at_the_pinned_block(self):
        meta, sources = verified_meta(self.block), {}
        with patch.object(self.store, "_rpc", side_effect=self.rpc) as rpc:
            self.store._refresh_emission(meta, NOW, sources, block_info=self.block_info)
        self.assertEqual(rpc.call_count, 2)
        self.assertEqual(meta["emission_model_status"], "verified")
        self.assertEqual(meta["emission_reference_block"], self.block)
        self.assertEqual(sources["emission"]["block_hash"], "0xabc")

    def test_rpc_failure_clears_previously_verified_fields(self):
        meta, sources = verified_meta(), {"emission": {"status": "ok"}}
        with patch.object(self.store, "_rpc", side_effect=OSError("intentional offline")):
            self.store._refresh_emission(meta, NOW, sources, block_info=self.block_info)
        self.assert_unavailable_and_cleared(meta, sources)

    def test_malformed_or_mismatching_reward_clears_old_fields(self):
        for invalid in ("0x", "0x" + "f" * 63, "0x" + "z" * 64,
                        "0x" + f"{block_reward_wei(self.block) - 1:064x}"):
            with self.subTest(reward=invalid):
                meta, sources = verified_meta(), {}
                def fake_rpc(method, params):
                    return self.rpc(method, params) if method == "eth_getBlockByNumber" else invalid
                with patch.object(self.store, "_rpc", side_effect=fake_rpc):
                    self.store._refresh_emission(meta, NOW, sources, block_info=self.block_info)
                self.assert_unavailable_and_cleared(meta, sources)

    def test_wrong_block_hash_or_future_timestamp_is_rejected(self):
        for header in ({"timestamp": hex(NOW), "hash": "0xwrong"},
                       {"timestamp": hex(NOW + 1), "hash": "0xabc"}):
            with self.subTest(header=header):
                meta, sources = verified_meta(), {}
                with patch.object(self.store, "_rpc", return_value=header) as rpc:
                    self.store._refresh_emission(meta, NOW, sources, block_info=self.block_info)
                self.assertEqual(rpc.call_count, 1)
                self.assert_unavailable_and_cleared(meta, sources)


if __name__ == "__main__":
    unittest.main()
