"""Session-aware higher-timeframe aggregation tests."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import multitimeframe


def _session_candles(days: int = 4, bars_per_day: int = 75, start: float = 100.0) -> list[dict]:
    """Build 5-minute candles across several trading days with overnight gaps."""
    candles: list[dict] = []
    price = start
    day = datetime(2026, 8, 3, 3, 45, tzinfo=timezone.utc)  # 09:15 IST
    for index in range(days):
        session_start = day + timedelta(days=index)
        for bar in range(bars_per_day):
            stamp = session_start + timedelta(minutes=5 * bar)
            price += 0.5
            candles.append(
                {
                    "timestamp": stamp,
                    "open": price - 0.2,
                    "high": price + 0.3,
                    "low": price - 0.4,
                    "close": price,
                    "volume": 1000 + bar,
                }
            )
    return candles


def _market(candles: list[dict]) -> dict:
    return {
        "interval_minutes": 5,
        "candles": candles,
        "close": [candle["close"] for candle in candles],
        "high": [candle["high"] for candle in candles],
        "low": [candle["low"] for candle in candles],
        "volume": [candle["volume"] for candle in candles],
        "price": candles[-1]["close"],
    }


class SessionAwareAggregationTests(unittest.TestCase):
    def test_timestamped_candles_are_bucketed_by_clock_time(self) -> None:
        candles = _session_candles()
        series = multitimeframe._session_aware_close(_market(candles), 60)
        self.assertIsNotNone(series)
        # Overnight gaps must not create synthetic bars: an hourly series over
        # four 6h15m sessions stays well under a naive positional count.
        self.assertLessEqual(len(series), len(candles) // 4)
        self.assertGreaterEqual(len(series), 20)

    def test_last_close_of_each_bucket_is_used(self) -> None:
        candles = _session_candles(days=4)
        series = multitimeframe._session_aware_close(_market(candles), 60)
        self.assertAlmostEqual(float(series.iloc[-1]), candles[-1]["close"], places=6)

    def test_missing_timestamps_fall_back_to_positional_resampling(self) -> None:
        candles = _session_candles()
        for candle in candles:
            candle.pop("timestamp")
        market = _market(candles)
        self.assertIsNone(multitimeframe._session_aware_close(market, 60))
        result = multitimeframe.analyze_timeframes(market)
        self.assertIn(result["60m"], {"BULLISH", "BEARISH", "SIDEWAYS", "UNKNOWN"})

    def test_rising_market_is_reported_bullish_on_every_timeframe(self) -> None:
        result = multitimeframe.analyze_timeframes(_market(_session_candles(days=5)))
        self.assertEqual(result["60m"], "BULLISH")
        self.assertEqual(result["15m"], "BULLISH")
        self.assertTrue(result["aligned"])

    def test_short_history_is_unknown_and_never_aligned(self) -> None:
        result = multitimeframe.analyze_timeframes(_market(_session_candles(days=1, bars_per_day=20)))
        self.assertFalse(result["aligned"])


if __name__ == "__main__":
    unittest.main()
