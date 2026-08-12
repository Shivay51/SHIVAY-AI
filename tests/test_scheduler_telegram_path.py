"""End-to-end audit: scanner output must reach Telegram, or be blocked loudly."""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scheduler
import signal_memory


def _market(delay_seconds: float = 5.0, delayed: bool = False) -> dict:
    stamp = datetime.now(timezone.utc) - timedelta(seconds=delay_seconds)
    price = 25000.0
    return {
        "price": price,
        "open_value": price,
        "day_high": price,
        "day_low": price,
        "latest_volume": 50000,
        "timestamp": stamp.isoformat(),
        "exchange": "NSE",
        "segment": "NSE_FNO",
        "instrument_type": "FUTIDX",
        "provider": "angelone_primary",
        "is_live": not delayed,
        "is_delayed": delayed,
        "verified": True,
    }


def _candidate(symbol: str = "NIFTY FUT", decision: str = "🔥 STRONG BUY", **overrides) -> dict:
    trade = {
        "symbol": symbol,
        "decision": decision,
        "side": "BUY" if "BUY" in decision else "SELL",
        "price": 25000.0,
        "entry": 25000.0,
        "sl": 24900.0,
        "target1": 25100.0,
        "target2": 25200.0,
        "target3": 25300.0,
        "score": 88,
        "confidence": 82,
        "signal_score": 88,
        "valid": True,
        "entry_confirmed": True,
        "requires_entry_confirmation": False,
        "market_data": _market(),
    }
    trade.update(overrides)
    return trade


class FullPathTests(unittest.TestCase):
    def setUp(self) -> None:
        signal_memory._loaded = True
        signal_memory._sent_signals.clear()
        self.addCleanup(signal_memory._sent_signals.clear)
        self.app = object()

    def _run_scan(self, candidates: list[dict]):
        buy = AsyncMock(return_value=True)
        sell = AsyncMock(return_value=True)
        with patch.object(scheduler, "scan_market", return_value=candidates), \
             patch.object(scheduler, "rank_trade", side_effect=lambda trade: {"valid": True, "signal_score": trade.get("score", 0)}), \
             patch.object(scheduler, "send_buy_signal", new=buy), \
             patch.object(scheduler, "send_sell_signal", new=sell), \
             patch.object(scheduler, "add_trade"), \
             patch.object(scheduler, "record_signal"), \
             patch.object(scheduler, "record_event"), \
             patch.object(scheduler, "record_rejection") as rejection, \
             patch.object(scheduler, "get_all_trades", return_value={}):
            delivered = asyncio.run(scheduler._scan_job(self.app))
        return delivered, buy, sell, rejection

    def test_fresh_buy_candidate_reaches_telegram(self) -> None:
        delivered, buy, sell, _ = self._run_scan([_candidate()])
        self.assertEqual(delivered, 1)
        self.assertEqual(buy.await_count, 1)
        self.assertEqual(sell.await_count, 0)

    def test_fresh_sell_candidate_uses_the_sell_sender(self) -> None:
        delivered, buy, sell, _ = self._run_scan([_candidate(decision="🔻 SELL")])
        self.assertEqual(delivered, 1)
        self.assertEqual(sell.await_count, 1)
        self.assertEqual(buy.await_count, 0)

    def test_delivered_symbol_enters_cooldown(self) -> None:
        with patch.dict("os.environ", {"SIGNAL_REPEAT_COOLDOWN_MINUTES": "45"}):
            self._run_scan([_candidate()])
            self.assertTrue(signal_memory.signal_exists("NIFTY FUT"))

    def test_stale_candidate_is_blocked_and_journalled(self) -> None:
        candidate = _candidate(market_data=_market(delay_seconds=9000))
        delivered, buy, sell, rejection = self._run_scan([candidate])
        self.assertEqual(delivered, 0)
        self.assertEqual(buy.await_count, 0)
        self.assertEqual(rejection.call_count, 1)
        self.assertIn("stale_at_delivery", rejection.call_args.args[1][0])

    def test_delayed_provider_candidate_is_blocked(self) -> None:
        candidate = _candidate(market_data=_market(delayed=True))
        delivered, buy, _, rejection = self._run_scan([candidate])
        self.assertEqual(delivered, 0)
        self.assertEqual(buy.await_count, 0)
        self.assertEqual(rejection.call_args.args[1], ["delayed_data_not_signal_capable"])

    def test_second_scan_inside_cooldown_does_not_resend(self) -> None:
        with patch.dict("os.environ", {"SIGNAL_REPEAT_COOLDOWN_MINUTES": "45"}):
            self._run_scan([_candidate()])
            delivered, buy, _, _ = self._run_scan([_candidate()])
        self.assertEqual(delivered, 0)
        self.assertEqual(buy.await_count, 0)

    def test_missing_snapshot_is_blocked_before_delivery(self) -> None:
        candidate = _candidate()
        candidate.pop("market_data")
        delivered, buy, _, rejection = self._run_scan([candidate])
        self.assertEqual(delivered, 0)
        self.assertEqual(buy.await_count, 0)
        self.assertEqual(rejection.call_args.args[1], ["market_snapshot_missing"])

    def test_scan_of_an_empty_market_delivers_nothing_and_raises_nothing(self) -> None:
        delivered, buy, sell, _ = self._run_scan([])
        self.assertEqual(delivered, 0)
        self.assertEqual(buy.await_count, 0)
        self.assertEqual(sell.await_count, 0)


class ScheduledJobRegistrationTests(unittest.TestCase):
    def test_scan_and_monitor_intervals_are_sane(self) -> None:
        self.assertGreaterEqual(scheduler.SCAN_SECONDS, 60)
        self.assertGreaterEqual(scheduler.MONITOR_SECONDS, 15)
        self.assertLessEqual(scheduler.MAX_SIGNALS, 5)

    def test_delivery_gate_exists_on_the_only_send_path(self) -> None:
        source = (ROOT / "scheduler.py").read_text(encoding="utf-8")
        segment = source[source.index("async def _scan_job"):source.index("async def _monitor_job")]
        self.assertIn("_delivery_allowed", segment)
        # The gate must precede the sender selection.
        self.assertLess(segment.index("_delivery_allowed"), segment.index("sender = send_buy_signal"))


if __name__ == "__main__":
    unittest.main()
