from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from node_preview import exchange_quotes as quotes


def observation(market, host, source, *, volume="123", turnover="2.45"):
    return {"exchange": market, "symbol": "MARS/USDT", "status": "ok", "price_usdt": "0.0084",
            "base_volume_24h": volume, "quote_turnover_24h_usdt": turnover,
            "source_at": source.isoformat(timespec="milliseconds"), "source_time_kind": quotes.MARKETS[market][0],
            "received_at": source.isoformat(timespec="milliseconds"), "source_host": host}


class ExchangeQuotesTests(unittest.TestCase):
    def test_depth_totals_group_to_one_hundredth(self):
        total, levels = quotes._depth_total([["0.00791", "2"], ["0.00799", "3"], ["0.011", "4"]])
        self.assertEqual(total, "9")
        self.assertEqual(levels, 2)

    def test_depth_snapshot_keeps_public_fields_and_zero_levels(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        quote = observation("KTX", "api.ktx.news", now)
        quote.update({"depth_status": "ok", "buy_depth_mars": "0", "sell_depth_mars": "4",
                      "buy_depth_levels": 0, "sell_depth_levels": 1, "depth_group": "0.01",
                      "depth_source_at": now.isoformat(timespec="milliseconds"),
                      "depth_received_at": now.isoformat(timespec="milliseconds")})
        other = observation("LBank", "api.lbkex.com", now)
        other.update({"depth_status": "unavailable", "buy_depth_mars": None, "sell_depth_mars": None,
                      "buy_depth_levels": None, "sell_depth_levels": None, "depth_group": "0.01",
                      "depth_source_at": None, "depth_received_at": None})
        value = {"schema": 1, "checked_at": now.isoformat(timespec="milliseconds"), "quotes": []}
        for item in (quote, other):
            value["quotes"].append({key: item.get(key) for key in quotes.PUBLIC_QUOTE_FIELDS})
        checked = quotes.validate_public_snapshot(value, now=now)
        self.assertEqual(checked["quotes"][0]["buy_depth_mars"], "0")

    def test_missing_routes_retry_by_venue_and_prefer_complete_amounts(self):
        stamp = datetime.now(timezone.utc)
        complete = observation("KTX", "api.ktx.news", stamp)
        incomplete = observation("KTX", "api.ktx.exchange", stamp + timedelta(seconds=1), turnover=None)
        backup = observation("LBank", "api.lbkex.com", stamp)
        initial = {"api.ktx.news": complete, "api.ktx.exchange": incomplete}

        def fetch(market, host, path, *, timeout=None):
            return backup if timeout is not None and host == "api.lbkex.com" else initial.get(host)

        with patch.object(quotes, "_fetch", side_effect=fetch) as mocked:
            result = quotes.collect_quotes()
        self.assertEqual(result["quotes"], [complete, backup])
        self.assertEqual(sum("timeout" in call.kwargs for call in mocked.call_args_list), 1)

    def test_each_market_keeps_original_good_quote_when_its_fetch_fails(self):
        stamp = datetime.now(timezone.utc) - timedelta(minutes=1)
        newer = stamp + timedelta(seconds=45)
        ktx = observation("KTX", "api.ktx.news", stamp)
        old_lbank = observation("LBank", "api.lbkex.com", stamp)
        new_lbank = observation("LBank", "api.lbkex.com", newer)
        rounds = [
            {"checked_at": ktx["received_at"], "quotes": [ktx, old_lbank]},
            {"checked_at": new_lbank["received_at"], "quotes": [quotes._unavailable("KTX"), new_lbank]},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(quotes, "collect_quotes", side_effect=rounds):
                first = quotes.update_quotes(root)
                second = quotes.update_quotes(root)
            self.assertEqual(quotes.load_public_snapshot(root), second)
        self.assertEqual(second["quotes"][0], {**first["quotes"][0], "status": "retained"})
        self.assertEqual(second["quotes"][1]["source_at"], new_lbank["source_at"])
        self.assertNotIn("source_host", second["quotes"][1])

        for field, value in (("exchange", "Other"), ("price_usdt", "0"), ("source_at", "9999-01-01T00:00:00.000+00:00")):
            invalid = deepcopy(second)
            invalid["quotes"][1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                quotes.validate_public_snapshot(invalid)


if __name__ == "__main__":
    unittest.main()
