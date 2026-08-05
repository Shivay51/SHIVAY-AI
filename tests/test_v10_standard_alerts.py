from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import unittest

from indicators import calculate_indicator_snapshot
from tradingview_cache import TradingViewCache
from tradingview_payload import PayloadError, parse_payload
from tradingview_security import SecurityError, TradingViewSecurity

ROOT = Path(__file__).resolve().parents[1]
SECRET = "v10-test-secret-abcdefghijklmnopqrstuvwxyz"


def standard(index: int = 0, timeframe: int = 15, trend: str = "UP") -> dict:
    stamp = datetime.now(timezone.utc) - timedelta(minutes=(220 - index) * timeframe)
    base = 100 + index * 0.25 if trend == "UP" else 200 - index * 0.25
    return {
        "secret": SECRET, "source": "TRADINGVIEW_STANDARD_ALERT",
        "event_id": f"TEST:V10-{timeframe}-{int(stamp.timestamp())}",
        "symbol": "TEST:V10", "exchange": "NSE", "category": "NSE_FO",
        "contract": "VERIFIED TEST CONTRACT", "timeframe": timeframe,
        "open": base - 0.1, "high": base + 0.5, "low": base - 0.5,
        "close": base, "volume": 1000 + index * 10, "bar_time": stamp.isoformat(),
    }


class StandardPayloadTests(unittest.TestCase):
    def test_minimal_standard_payload(self):
        value = parse_payload(standard(220))
        self.assertEqual((value.event_type, value.source, value.instrument), ("candle", "TRADINGVIEW_STANDARD_ALERT", "TEST:V10"))
        self.assertFalse(value.indicators_supplied)

    def test_negative_volume_rejected(self):
        value = standard(220); value["volume"] = -1
        with self.assertRaises(PayloadError): parse_payload(value)

    def test_templates_are_secret_free_and_complete(self):
        document = json.loads((ROOT / "TRADINGVIEW_ALERT_MESSAGES.json").read_text(encoding="utf-8"))
        self.assertEqual(set(document["templates"]), {"5_minute", "15_minute", "30_minute", "60_minute"})
        text = json.dumps(document)
        self.assertIn("<PASTE_LOCAL_WEBHOOK_SECRET>", text)
        self.assertNotIn(SECRET, text)
        for placeholder in ("{{ticker}}", "{{exchange}}", "{{open}}", "{{high}}", "{{low}}", "{{close}}", "{{volume}}", "{{time}}"):
            self.assertIn(placeholder, text)


class PythonIndicatorWarmupTests(unittest.TestCase):
    def test_warmup_then_ready(self):
        cache = TradingViewCache()
        for index in range(199): cache.put(parse_payload(standard(index)))
        self.assertEqual(cache.warmup_state("TEST:V10", 15, "VERIFIED TEST CONTRACT", "NSE_FO")["state"], "PARTIAL")
        # Directly test warm-up readiness independent of historic timestamp age.
        before = calculate_indicator_snapshot([parse_payload(standard(i)).to_dict() for i in range(199)])
        after = calculate_indicator_snapshot([parse_payload(standard(i)).to_dict() for i in range(201)])
        self.assertFalse(before["ready"]); self.assertTrue(after["ready"])
        self.assertGreater(after["ema20"], after["ema50"]); self.assertGreater(after["ema50"], after["ema200"])
        for key in ("rsi", "adx", "plus_di", "minus_di", "macd", "macd_signal", "macd_histogram", "atr", "vwap", "relative_volume"):
            self.assertIn(key, after)

    def test_bull_and_bear_conditions_are_separate(self):
        up = calculate_indicator_snapshot([parse_payload(standard(i, trend="UP")).to_dict() for i in range(201)])
        down = calculate_indicator_snapshot([parse_payload(standard(i, trend="DOWN")).to_dict() for i in range(201)])
        self.assertEqual(up["trend_state"], "BULLISH")
        self.assertEqual(down["trend_state"], "BEARISH")
        self.assertFalse(up["bearish_pullback"] and up["bullish_pullback"])
        self.assertFalse(down["bearish_breakdown"] and down["bullish_breakout"])

    def test_persistence_survives_restart(self):
        path = ROOT / "storage" / ".test_v10_candles.json"
        try:
            path.unlink(missing_ok=True)
            first = TradingViewCache(persistence_path=path)
            first.put(parse_payload(standard(220)))
            second = TradingViewCache(persistence_path=path)
            self.assertEqual(len(second.bars("TEST:V10", 15, "VERIFIED TEST CONTRACT", "NSE_FO")), 1)
        finally:
            path.unlink(missing_ok=True)


class PersistentReplayTests(unittest.TestCase):
    def test_replay_survives_restart(self):
        current = standard(220)
        current["bar_time"] = datetime.now(timezone.utc).isoformat()
        current["event_id"] = "restart-replay-test"
        payload = parse_payload(current)
        path = ROOT / "storage" / ".test_v10_replay.json"
        try:
            path.unlink(missing_ok=True)
            old = os.environ.get("TRADINGVIEW_WEBHOOK_SECRET")
            os.environ["TRADINGVIEW_WEBHOOK_SECRET"] = SECRET
            try:
                TradingViewSecurity(path).validate(payload, {"TEST:V10"}, {15})
                with self.assertRaises(SecurityError): TradingViewSecurity(path).validate(payload, {"TEST:V10"}, {15})
            finally:
                if old is None: os.environ.pop("TRADINGVIEW_WEBHOOK_SECRET", None)
                else: os.environ["TRADINGVIEW_WEBHOOK_SECRET"] = old
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
