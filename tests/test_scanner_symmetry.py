"""Step 5 — scanner, Chandelier confirmation and BUY/SELL symmetry tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import config
import chandelier_exit
import scanner
import signal_classification
from chandelier_exit import (
    evaluate_chandelier_entry_state,
    get_completed_timeframe_candles,
    is_chandelier_buy_confirmed,
    is_chandelier_sell_confirmed,
    update_chandelier_trailing_stop,
)

BASE = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)


def _bars(closes: list[float], *, minutes: int = 5, live_close: float | None = None,
          live_open: float | None = None, live_offset: int = 0) -> list[dict]:
    rows = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close
        rows.append({
            "open": open_price,
            "high": max(open_price, close) + 1.0,
            "low": min(open_price, close) - 1.0,
            "close": close,
            "timestamp": (BASE + timedelta(minutes=minutes * index)).isoformat(),
            "completed": True,
        })
    if live_close is not None:
        start = BASE + timedelta(minutes=minutes * len(closes) + live_offset)
        open_price = live_open if live_open is not None else closes[-1]
        rows.append({
            "open": open_price,
            "high": max(open_price, live_close) + 0.5,
            "low": min(open_price, live_close) - 0.5,
            "close": live_close,
            "timestamp": start.isoformat(),
            "completed": False,
        })
    return rows


def _market(rows: list[dict], *, price: float | None = None, elapsed: float = 70.0) -> dict:
    return {
        "interval_minutes": 15,
        "candles": rows,
        "high": [row["high"] for row in rows],
        "low": [row["low"] for row in rows],
        "close": [row["close"] for row in rows],
        "open": [row["open"] for row in rows],
        "price": price if price is not None else rows[-1]["close"],
        "confirmation_elapsed_seconds": elapsed,
        "segment": "NSE_FNO",
    }


def _rising_then_falling() -> list[float]:
    # long uptrend then a decisive break -> BEARISH flip on the last candle
    return [100 + index * 2.0 for index in range(20)] + [96.0]


def _falling_then_rising() -> list[float]:
    return [140 - index * 2.0 for index in range(20)] + [118.0]


class TimeframeTests(unittest.TestCase):
    def test_primary_timeframe_is_fifteen_minutes(self) -> None:
        self.assertEqual(int(config.PRIMARY_TIMEFRAME_MINUTES), 15)

    def test_scan_interval_is_five_minutes(self) -> None:
        self.assertEqual(int(config.SCAN_INTERVAL), 300)

    def test_only_completed_candles_are_used_for_signals(self) -> None:
        rows = _bars([100.0 + index for index in range(6)], minutes=15, live_close=110.0)
        completed = get_completed_timeframe_candles(_market(rows), 15)
        self.assertEqual(len(completed), 6)
        self.assertTrue(all(candle.get("complete") for candle in completed))


class ClassificationTests(unittest.TestCase):
    def test_thresholds(self) -> None:
        limits = signal_classification.thresholds()
        self.assertEqual(limits["actionable"], 70)
        self.assertEqual(limits["premium"], 90)
        self.assertGreaterEqual(limits["safe"], limits["actionable"])
        self.assertLessEqual(limits["safe"], limits["premium"])

    def _signal(self, score: int, side: str = "BUY") -> dict:
        return {"score": score, "side": side, "risk_reward": 2.5,
                "chandelier_entry_state": {"confirmed": True, "side": side}}

    def test_below_seventy_is_ignored_for_both_sides(self) -> None:
        for side in ("BUY", "SELL"):
            verdict = signal_classification.classify(self._signal(69, side))
            self.assertEqual(verdict["classification"], "IGNORE")
            self.assertFalse(verdict["deliverable"])

    def test_seventy_is_actionable_and_risky(self) -> None:
        for side in ("BUY", "SELL"):
            verdict = signal_classification.classify(self._signal(72, side))
            self.assertEqual(verdict["classification"], "RISKY")
            self.assertTrue(verdict["actionable"])
            self.assertFalse(verdict["premium"])

    def test_high_score_is_safe_and_ninety_plus_is_premium(self) -> None:
        for side in ("BUY", "SELL"):
            self.assertEqual(signal_classification.classify(self._signal(84, side))["classification"], "SAFE")
            premium = signal_classification.classify(self._signal(93, side))
            self.assertEqual(premium["classification"], "SAFE")
            self.assertTrue(premium["premium"])
            self.assertEqual(premium["tier"], "PREMIUM_90_PLUS")

    def test_unconfirmed_and_bad_rr_are_ignored(self) -> None:
        unconfirmed = dict(self._signal(95), chandelier_entry_state={"confirmed": False})
        self.assertEqual(signal_classification.classify(unconfirmed)["classification"], "IGNORE")
        poor = dict(self._signal(95), risk_reward=0.4)
        self.assertEqual(signal_classification.classify(poor)["classification"], "IGNORE")

    def test_ignore_class_is_never_deliverable(self) -> None:
        kept = signal_classification.filter_deliverable([
            self._signal(95, "BUY"), self._signal(50, "SELL"), self._signal(75, "SELL"),
        ])
        self.assertEqual([item["score"] for item in kept], [95, 75])
        self.assertTrue(all(signal_classification.is_deliverable(item) for item in kept))

    def test_scores_classify_identically_for_buy_and_sell(self) -> None:
        for score in (60, 70, 79, 80, 89, 90, 99):
            buy = signal_classification.classify(self._signal(score, "BUY"))
            sell = signal_classification.classify(self._signal(score, "SELL"))
            self.assertEqual(buy["classification"], sell["classification"])
            self.assertEqual(buy["premium"], sell["premium"])


class ChandelierConfirmationTests(unittest.TestCase):
    def _state(self, closes: list[float], **kwargs) -> dict:
        elapsed = kwargs.pop("elapsed", 70.0)
        rows = _bars(closes, minutes=15, **kwargs)
        live_start = chandelier_exit._timestamp(rows[-1]["timestamp"])
        return evaluate_chandelier_entry_state(
            _market(rows, elapsed=elapsed), 15,
            now=(live_start + timedelta(seconds=elapsed)) if live_start else None,
        )

    def test_sell_signal_detected_and_confirmed(self) -> None:
        closes = _rising_then_falling()
        state = self._state(closes, live_close=94.0, live_open=96.0)
        self.assertEqual(state["side"], "SELL")
        self.assertTrue(state["valid"])
        self.assertEqual(state["status"], "CONFIRMED")
        self.assertEqual(state["hard_invalidation_level"], state["signal_candle"]["high"])

    def test_buy_signal_detected_and_confirmed(self) -> None:
        closes = _falling_then_rising()
        state = self._state(closes, live_close=119.0, live_open=118.0)
        self.assertEqual(state["side"], "BUY")
        self.assertEqual(state["status"], "CONFIRMED")
        self.assertEqual(state["hard_invalidation_level"], state["signal_candle"]["low"])

    def test_confirmation_waits_for_first_minute(self) -> None:
        rows = _bars(_falling_then_rising(), minutes=15, live_close=119.0, live_open=118.0)
        market = _market(rows, elapsed=5.0)
        state = evaluate_chandelier_entry_state(market, 15, now=chandelier_exit._timestamp(rows[-1]["timestamp"]))
        self.assertEqual(state["status"], "PENDING_CONFIRMATION")
        self.assertFalse(state["confirmed"])

    def test_late_confirmation_is_rejected(self) -> None:
        rows = _bars(_falling_then_rising(), minutes=15, live_close=119.0, live_open=118.0)
        elapsed = float(config.CHANDELIER_CONFIRMATION_MAX_SECONDS) + 60
        market = _market(rows, elapsed=elapsed)
        state = evaluate_chandelier_entry_state(
            market, 15, now=chandelier_exit._timestamp(rows[-1]["timestamp"]) + timedelta(seconds=elapsed))
        self.assertEqual(state["status"], "REJECTED")
        self.assertEqual(state["reason"], "confirmation_window_expired")

    def test_over_travelled_entry_is_rejected_on_both_sides(self) -> None:
        buy = self._state(_falling_then_rising(), live_close=190.0, live_open=118.0)
        self.assertEqual(buy["reason"], "entry_over_travelled")
        sell = self._state(_rising_then_falling(), live_close=20.0, live_open=96.0)
        self.assertEqual(sell["reason"], "entry_over_travelled")

    def test_signal_candle_invalidation_rejects_entry(self) -> None:
        rows = _bars(_falling_then_rising(), minutes=15, live_close=119.0, live_open=118.0)
        signal_low = rows[-2]["low"]
        rows[-1].update({"open": signal_low, "close": signal_low - 0.2, "low": signal_low - 1.0})
        state = evaluate_chandelier_entry_state(
            _market(rows, elapsed=70.0, price=signal_low - 0.2), 15,
            now=chandelier_exit._timestamp(rows[-1]["timestamp"]) + timedelta(seconds=70),
        )
        self.assertEqual(state["reason"], "signal_candle_invalidated")

    def test_non_adjacent_candle_is_not_confirmation(self) -> None:
        state = self._state(_falling_then_rising(), live_close=119.0, live_open=118.0, live_offset=60)
        self.assertEqual(state["status"], "PENDING_CONFIRMATION")
        self.assertEqual(state["reason"], "waiting_for_immediate_next_15m_candle")

    def test_minimum_hold_is_ten_minutes(self) -> None:
        self.assertGreaterEqual(int(config.MIN_HOLD_MINUTES), 10)
        state = self._state(_falling_then_rising(), live_close=119.0, live_open=118.0)
        self.assertEqual(state["minimum_hold_minutes"], int(config.MIN_HOLD_MINUTES))


class SymmetryTests(unittest.TestCase):
    def test_trailing_stop_tightens_only_and_mirrors(self) -> None:
        self.assertEqual(update_chandelier_trailing_stop("BUY", 100.0, 105.0), 105.0)
        self.assertEqual(update_chandelier_trailing_stop("BUY", 100.0, 95.0), 100.0)
        self.assertEqual(update_chandelier_trailing_stop("SELL", 100.0, 95.0), 95.0)
        self.assertEqual(update_chandelier_trailing_stop("SELL", 100.0, 105.0), 100.0)

    def test_confirmation_helpers_are_mirrored(self) -> None:
        bullish = {"valid": True, "trend": "BULLISH", "long_stop": 95.0, "short_stop": 105.0}
        bearish = {"valid": True, "trend": "BEARISH", "long_stop": 95.0, "short_stop": 105.0}
        self.assertTrue(is_chandelier_buy_confirmed(bullish, 100.0))
        self.assertFalse(is_chandelier_buy_confirmed(bullish, 90.0))
        self.assertTrue(is_chandelier_sell_confirmed(bearish, 100.0))
        self.assertFalse(is_chandelier_sell_confirmed(bearish, 110.0))

    def test_strategy_gates_are_symmetric(self) -> None:
        source = open("strategy.py", encoding="utf-8").read()
        # mirrored RSI bands around 50 and mirrored market-strength gates
        self.assertIn("54 <= rsi <= 72", source)
        self.assertIn("28 <= rsi <= 46", source)
        self.assertIn("market_strength >= 55", source)
        self.assertIn("market_strength <= 45", source)
        self.assertEqual(source.count('"SIDEWAYS" not in market_direction'), 2)
        self.assertEqual(source.count("extension <= 2.2"), 2)
        self.assertEqual(source.count("adx_value >= 22"), 2)
        self.assertEqual(source.count("relative_volume >= 1.0"), 2)
        self.assertEqual(source.count("context_score >= 75"), 2)
        self.assertEqual(source.count("confidence_value >= 95"), 2)
        self.assertEqual(source.count("confidence_value >= 90"), 2)


class ScannerGateTests(unittest.TestCase):
    def test_scan_returns_nothing_when_market_closed(self) -> None:
        original = scanner.signals_allowed
        scanner.signals_allowed = lambda segment=None: False
        try:
            self.assertEqual(scanner.scan_market(), [])
            diagnostics = scanner.get_last_scan_diagnostics()
            self.assertEqual(diagnostics["eligible_alerts"], 0)
            self.assertIn("market_closed", diagnostics["rejection_reasons"])
        finally:
            scanner.signals_allowed = original

    def test_scanner_universe_is_futures_only(self) -> None:
        import data
        self.assertTrue(all(name.endswith(" FUT") for name in data.SYMBOLS))

    def test_scanner_requests_primary_timeframe(self) -> None:
        import data
        source = open("data.py", encoding="utf-8").read()
        self.assertIn("PRIMARY_TIMEFRAME_MINUTES", source)
        self.assertNotIn('interval="5m"', source)
        self.assertTrue(hasattr(data, "refresh_market"))


if __name__ == "__main__":
    unittest.main()


class SchedulerDeliveryGateTests(unittest.TestCase):
    def test_rank_drops_ignore_class_signals(self) -> None:
        import scheduler

        original_exists = scheduler.signal_exists
        original_rank = scheduler.rank_trade
        scheduler.signal_exists = lambda symbol: False
        scheduler.rank_trade = lambda signal: {"valid": True, "signal_score": signal.get("score", 0)}
        try:
            confirmed = {"confirmed": True, "side": "BUY"}
            ranked = scheduler._rank([
                {"symbol": "NIFTY FUT", "side": "BUY", "score": 92, "risk_reward": 2.5,
                 "chandelier_entry_state": confirmed},
                {"symbol": "SBIN FUT", "side": "BUY", "score": 55, "risk_reward": 2.5,
                 "chandelier_entry_state": confirmed},
            ])
            self.assertEqual([item["symbol"] for item in ranked], ["NIFTY FUT"])
            self.assertEqual(ranked[0]["signal_classification"], "SAFE")
            self.assertTrue(ranked[0]["premium_signal"])
        finally:
            scheduler.signal_exists = original_exists
            scheduler.rank_trade = original_rank
