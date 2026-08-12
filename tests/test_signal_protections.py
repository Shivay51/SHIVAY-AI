"""Duplicate-cooldown and pre-delivery protection tests."""
from __future__ import annotations

import importlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import signal_memory


def _fresh_market(delay_seconds: float = 5.0, delayed: bool = False) -> dict:
    stamp = datetime.now(timezone.utc) - timedelta(seconds=delay_seconds)
    price = 100.0
    return {
        "price": price,
        "open_value": price,
        "day_high": price,
        "day_low": price,
        "latest_volume": 1000,
        "timestamp": stamp.isoformat(),
        "exchange": "NSE",
        "segment": "NSE_FNO",
        "instrument_type": "FUTIDX",
        "provider": "angelone_primary",
        "is_delayed": delayed,
        "verified": True,
    }


class DuplicateCooldownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = ROOT / "signal_memory_state.json"
        self._existing = self.state.read_text(encoding="utf-8") if self.state.is_file() else None
        signal_memory._loaded = True
        signal_memory._sent_signals.clear()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        signal_memory._sent_signals.clear()
        if self._existing is None:
            self.state.unlink(missing_ok=True)
        else:
            self.state.write_text(self._existing, encoding="utf-8")

    def test_symbol_is_blocked_inside_cooldown(self) -> None:
        with patch.dict("os.environ", {"SIGNAL_REPEAT_COOLDOWN_MINUTES": "45"}):
            signal_memory.add_signal("RELIANCE")
            self.assertTrue(signal_memory.signal_exists("RELIANCE"))
            self.assertGreater(signal_memory.cooldown_remaining("RELIANCE"), 0)

    def test_symbol_is_released_after_cooldown_expires(self) -> None:
        with patch.dict("os.environ", {"SIGNAL_REPEAT_COOLDOWN_MINUTES": "0.001"}):
            signal_memory.add_signal("TCS")
            signal_memory._sent_signals["TCS"] -= 10.0
            self.assertFalse(signal_memory.signal_exists("TCS"))
            self.assertEqual(signal_memory.cooldown_remaining("TCS"), 0.0)

    def test_symbol_matching_is_case_and_space_insensitive(self) -> None:
        with patch.dict("os.environ", {"SIGNAL_REPEAT_COOLDOWN_MINUTES": "45"}):
            signal_memory.add_signal(" infy ")
            self.assertTrue(signal_memory.signal_exists("INFY"))

    def test_cooldown_survives_a_restart(self) -> None:
        with patch.dict("os.environ", {"SIGNAL_REPEAT_COOLDOWN_MINUTES": "45"}):
            signal_memory.add_signal("HDFCBANK")
            self.assertTrue(self.state.is_file())
            signal_memory._sent_signals.clear()
            signal_memory._loaded = False
            self.assertTrue(signal_memory.signal_exists("HDFCBANK"))

    def test_snapshot_reports_remaining_seconds_without_secrets(self) -> None:
        with patch.dict("os.environ", {"SIGNAL_REPEAT_COOLDOWN_MINUTES": "45"}):
            signal_memory.add_signal("NIFTY")
            report = signal_memory.snapshot()
            self.assertIn("NIFTY", report)
            self.assertGreater(report["NIFTY"], 0)

    def test_clear_signals_empties_memory(self) -> None:
        signal_memory.add_signal("SBIN")
        signal_memory.clear_signals()
        self.assertEqual(signal_memory.total_signals(), 0)


class PreDeliveryGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = importlib.import_module("scheduler")
        signal_memory._loaded = True
        signal_memory._sent_signals.clear()
        self.addCleanup(signal_memory._sent_signals.clear)

    def test_fresh_signal_is_allowed(self) -> None:
        allowed, reason = self.scheduler._delivery_allowed(
            {"symbol": "NIFTY", "market_data": _fresh_market()}
        )
        self.assertTrue(allowed, reason)
        self.assertEqual(reason, "ok")

    def test_stale_signal_is_blocked_at_delivery(self) -> None:
        allowed, reason = self.scheduler._delivery_allowed(
            {"symbol": "NIFTY", "market_data": _fresh_market(delay_seconds=6000)}
        )
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("stale_at_delivery"))

    def test_delayed_provider_data_can_never_deliver(self) -> None:
        allowed, reason = self.scheduler._delivery_allowed(
            {"symbol": "NIFTY", "market_data": _fresh_market(delayed=True)}
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "delayed_data_not_signal_capable")

    def test_missing_snapshot_is_blocked(self) -> None:
        allowed, reason = self.scheduler._delivery_allowed({"symbol": "NIFTY"})
        self.assertFalse(allowed)
        self.assertEqual(reason, "market_snapshot_missing")

    def test_cooldown_blocks_delivery(self) -> None:
        with patch.dict("os.environ", {"SIGNAL_REPEAT_COOLDOWN_MINUTES": "45"}):
            signal_memory.add_signal("BANKNIFTY")
            allowed, reason = self.scheduler._delivery_allowed(
                {"symbol": "BANKNIFTY", "market_data": _fresh_market()}
            )
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("repeat_cooldown_active"))


if __name__ == "__main__":
    unittest.main()
