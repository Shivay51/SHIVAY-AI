from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from index_outlook import decide_outlook, format_market_outlook


def item(change_percent: float, freshness: str = "LIVE") -> dict:
    return {"available": True, "ltp": 100.0, "change": change_percent,
            "change_percent": change_percent, "timestamp": datetime.now(timezone.utc),
            "freshness": freshness, "direction_usable": freshness != "UNAVAILABLE"}


class OutlookFreshnessTests(unittest.TestCase):
    def test_negative_gift_and_global_majority_cannot_be_gap_up(self):
        values = {"GIFT NIFTY": item(-0.39), "DOW": item(-0.90), "S&P 500": item(-0.22),
                  "NASDAQ": item(-0.06), "NIKKEI": item(0.10), "HANG SENG": item(-1.40),
                  "BANKNIFTY": item(-0.30)}
        result = decide_outlook(values)
        self.assertEqual(result["decision"], "MILD BEARISH")
        self.assertNotIn("GAP-UP", result["expected_open"])

    def test_change_percentage_not_unrelated_price_drives_direction(self):
        values = {"GIFT NIFTY": item(-0.40), "DOW": item(-0.30), "S&P 500": item(-0.20),
                  "NASDAQ": item(-0.10), "NIKKEI": item(0.01), "HANG SENG": item(-0.50)}
        values["GIFT NIFTY"]["ltp"] = 999999.0
        self.assertEqual(decide_outlook(values)["decision"], "MILD BEARISH")

    def test_unavailable_gift_cannot_drive_direction(self):
        values = {"GIFT NIFTY": {"available": False, "freshness": "UNAVAILABLE"},
                  "DOW": item(-1), "S&P 500": item(-1), "NASDAQ": item(-1)}
        self.assertEqual(decide_outlook(values)["decision"], "WAIT")

    def test_clean_telegram_format(self):
        report = {"generated_at": datetime.now(timezone.utc),
                  "inputs": {name: item(-0.2) for name in ("GIFT NIFTY", "DOW", "S&P 500", "NASDAQ")},
                  "nifty_levels": {"s1": 1, "s2": 2, "r1": 3, "r2": 4},
                  "banknifty_levels": {"s1": 5, "s2": 6, "r1": 7, "r2": 8},
                  "nifty_bias": "MILD BEARISH", "expected_open": "NEGATIVE BIAS",
                  "banknifty_bias": "SIDEWAYS", "bull_probability": 40,
                  "bear_probability": 60, "decision": "MILD BEARISH"}
        text = format_market_outlook(report)
        self.assertTrue(text.startswith("🔱 SHIVAY AI PRO\n📊 MARKET OUTLOOK"))
        for required in ("GIFT NIFTY", "DOW", "S&P 500", "NASDAQ", "S1:", "R2:", "BULL: 40%", "BEAR: 60%", "DECISION:"):
            self.assertIn(required, text)
        for forbidden in ("source", "provider", "api", "tradingview", "disclaimer"):
            self.assertNotIn(forbidden, text.lower())

    def test_stale_gift_must_be_marked_unavailable_by_caller(self):
        stale = item(-0.5, "UNAVAILABLE")
        stale["timestamp"] = datetime.now(timezone.utc) - timedelta(hours=2)
        stale["available"] = False
        values = {"GIFT NIFTY": stale, "DOW": item(-1), "S&P 500": item(-1), "NASDAQ": item(-1)}
        self.assertEqual(decide_outlook(values)["expected_open"], "UNCERTAIN")

    def test_mcx_without_real_candles_is_not_trade_ready(self):
        from gold import _empty_analysis as empty_gold
        from silver import _empty_analysis as empty_silver
        self.assertFalse(empty_gold("Verified MCX candles are unavailable")["tradeable"])
        self.assertFalse(empty_silver("Verified MCX candles are unavailable")["tradeable"])


if __name__ == "__main__":
    unittest.main()
