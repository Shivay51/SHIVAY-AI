from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from chandelier_exit import evaluate_chandelier_entry_state, update_chandelier_trailing_stop
from trade_monitor import add_trade, get_all_trades, monitor_trade, remove_trade


def _market(closes, live_last=False):
    rows = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index, close in enumerate(closes):
        opened = closes[index - 1] if index else close
        rows.append({
            "timestamp": start + timedelta(minutes=15 * index), "open": opened,
            "high": max(opened, close) + 1, "low": min(opened, close) - 1,
            "close": close, "volume": 1000 + index,
            "completed": not (live_last and index == len(closes) - 1),
        })
    return {"interval_minutes": 15, "candles": rows}


class ChandelierPrimaryEngineTests(unittest.TestCase):
    def test_buy_requires_next_completed_confirmation_candle(self):
        signal_only = _market([120 - index for index in range(25)] + [115])
        pending = evaluate_chandelier_entry_state(signal_only)
        self.assertEqual((pending["status"], pending["side"]), ("PENDING_CONFIRMATION", "BUY"))
        market = _market([120 - index for index in range(25)] + [115, 117], live_last=True)
        confirmed = evaluate_chandelier_entry_state(
            market, now=market["candles"][-1]["timestamp"] + timedelta(seconds=61)
        )
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["side"], "BUY")
        self.assertEqual(confirmed["signal_candle"]["close"], 115)
        self.assertEqual(confirmed["confirmation_candle"]["close"], 117)

    def test_failed_confirmation_is_no_trade(self):
        market = _market([120 - index for index in range(25)] + [115, 110], live_last=True)
        state = evaluate_chandelier_entry_state(
            market, now=market["candles"][-1]["timestamp"] + timedelta(seconds=61)
        )
        self.assertFalse(state["confirmed"])
        self.assertEqual(state["status"], "REJECTED")

    def test_sell_requires_next_completed_confirmation_candle(self):
        market = _market([80 + index for index in range(25)] + [89, 87], live_last=True)
        state = evaluate_chandelier_entry_state(
            market, now=market["candles"][-1]["timestamp"] + timedelta(seconds=61)
        )
        self.assertTrue(state["confirmed"])
        self.assertEqual(state["side"], "SELL")

    def test_does_not_wait_for_confirmation_candle_close(self):
        market = _market([120 - index for index in range(25)] + [115, 117], live_last=True)
        live = market["candles"][-1]
        state = evaluate_chandelier_entry_state(market, now=live["timestamp"] + timedelta(seconds=61))
        self.assertFalse(live["completed"])
        self.assertEqual((state["status"], state["side"]), ("CONFIRMED", "BUY"))
        self.assertEqual(state["reason"], "next_candle_first_minute_confirmed")

    def test_signal_candle_invalidation_closes_trade(self):
        trade = {"symbol": "TEST CHANDELIER FUT", "decision": "BUY", "entry": 110, "sl": 95,
                 "target1": 120, "target2": 130, "target3": 140, "entry_confirmed": True,
                 "requires_entry_confirmation": False, "signal_candle_low": 100,
                 "signal_candle_high": 116, "hard_invalidation_level": 100}
        add_trade(trade)
        tracked = get_all_trades()[trade["symbol"]]
        result = monitor_trade(tracked, 99)
        self.assertEqual(result["type"], "SIGNAL CANDLE INVALIDATION")
        self.assertTrue(result["closed"])
        remove_trade(trade["symbol"])

    def test_chandelier_stop_never_widens(self):
        self.assertEqual(update_chandelier_trailing_stop("BUY", 100, 98), 100)
        self.assertEqual(update_chandelier_trailing_stop("BUY", 100, 103), 103)
        self.assertEqual(update_chandelier_trailing_stop("SELL", 100, 102), 100)
        self.assertEqual(update_chandelier_trailing_stop("SELL", 100, 97), 97)

    def test_sell_signal_candle_invalidation_closes_trade(self):
        trade = {"symbol": "TEST SELL CHANDELIER FUT", "decision": "SELL", "entry": 100, "sl": 115,
                 "target1": 90, "target2": 85, "target3": 80, "entry_confirmed": True,
                 "requires_entry_confirmation": False, "signal_candle_low": 94,
                 "signal_candle_high": 110, "hard_invalidation_level": 110}
        add_trade(trade)
        result = monitor_trade(get_all_trades()[trade["symbol"]], 111)
        self.assertEqual(result["type"], "SIGNAL CANDLE INVALIDATION")
        self.assertTrue(result["closed"])
        remove_trade(trade["symbol"])

    def test_secondary_engine_cannot_reverse_chandelier_side(self):
        from core.buy_engine import check_buy
        secondary_bullish = {
            "chandelier_entry_state": {"confirmed": True, "status": "CONFIRMED", "side": "SELL"},
            "regime": "STRONG BULL", "score": 99, "ema20": 110, "ema50": 105, "ema200": 100,
            "rsi": 60, "adx": 35, "price": 120, "vwap": 115, "macd": True, "supertrend": True,
            "timeframe_60m": "BULLISH", "timeframe_30m": "BULLISH", "timeframe_15m": "BULLISH",
            "timeframe_5m": "BULLISH", "volume_spike": True, "relative_volume": 2.0,
        }
        self.assertFalse(check_buy(secondary_bullish))


if __name__ == "__main__":
    unittest.main()
