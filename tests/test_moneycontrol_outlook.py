from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from index_outlook import rank_confluence_levels, recalculate_post_open_levels, sanity_check_outlook
from moneycontrol_outlook import parse_market_table, parse_next_data, parse_stock_snapshot


def next_html(stock: dict) -> str:
    payload = {"props": {"pageProps": {"consumptionData": {"stockData": stock}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"


class MoneycontrolOutlookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.gift = {"current_price": "24,654.50", "prev_close": "24,748.00",
                     "net_change": "-93.50", "percent_change": "-0.38",
                     "open": "24,720", "high": "24,730", "low": "24,620",
                     "lastupd_epoch": int(self.now.timestamp() * 1000), "market_state": "OPEN"}

    def test_gift_parser_and_previous_close_arithmetic(self):
        item = parse_stock_snapshot("GIFT NIFTY", parse_next_data(next_html(self.gift)), self.now)
        self.assertTrue(item["validated"])
        self.assertEqual(item["previous_close"], 24748.0)
        self.assertEqual(item["change"], -93.5)
        self.assertAlmostEqual(item["change_percent"], -0.38)

    def test_negative_gift_cannot_be_interpreted_as_positive(self):
        corrupt = dict(self.gift, net_change="7.50", percent_change="0.03")
        item = parse_stock_snapshot("GIFT NIFTY", corrupt, self.now)
        self.assertFalse(item["validated"])
        self.assertIn("change arithmetic mismatch", item["validation_errors"])

    def test_stale_market_timestamp_is_rejected(self):
        stale = dict(self.gift, lastupd_epoch=int((self.now - timedelta(hours=2)).timestamp()))
        item = parse_stock_snapshot("GIFT NIFTY", stale, self.now)
        self.assertFalse(item["validated"])
        self.assertIn("stale market timestamp", item["validation_errors"])

    def test_global_table_parser(self):
        html = "<table><tr><td>DOW JONES (Aug 07)</td><td>53,905.36</td><td>-443.76</td><td>-0.82</td></tr>" \
               "<tr><td>S&amp;P 500 (Aug 07)</td><td>7,711.60</td><td>-11.95</td><td>-0.15</td></tr></table>"
        values = parse_market_table(html, self.now)
        self.assertLess(values["DOW"]["change_percent"], 0)
        self.assertLess(values["S&P 500"]["change"], 0)

    def test_level_confluence_and_post_open_recalculation(self):
        levels = rank_confluence_levels(24700, 24820, 24580, [25000], [24500])
        self.assertLess(levels["s2"], levels["s1"])
        self.assertLess(levels["s1"], levels["r1"])
        self.assertLess(levels["r1"], levels["r2"])
        revised = recalculate_post_open_levels(24710, 24780, 24640, 24720)
        self.assertTrue(all(revised.values()))

    def test_sanity_blocks_invalid_core_snapshot(self):
        inputs = {name: {"validated": True, "change": -1, "change_percent": -.1}
                  for name in ("GIFT NIFTY", "DOW", "S&P 500", "NASDAQ")}
        inputs["GIFT NIFTY"]["validated"] = False
        report = {"inputs": inputs, "nifty_levels": {"s1": 2, "s2": 1, "r1": 3, "r2": 4},
                  "banknifty_levels": {"s1": 6, "s2": 5, "r1": 7, "r2": 8},
                  "bull_probability": 40, "bear_probability": 60, "decision": "WAIT"}
        valid, errors = sanity_check_outlook(report)
        self.assertFalse(valid)
        self.assertTrue(any("GIFT NIFTY" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
