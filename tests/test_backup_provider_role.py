"""Step 3: the authenticated TradingView bridge is the only emergency backup."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tradingview_bridge
from provider_failover import ProviderUnavailable
from provider_manager import ProviderManager
from tradingview_bridge import TradingViewBridgeProvider, assert_single_provider_dataset
from tradingview_cache import TradingViewCache
from tradingview_payload import parse_payload

SECRET = "synthetic-test-secret-1234567890"
SYMBOL = "TEST:NIFTYCURRENT"
MAPPING = str(ROOT / "tests" / "tv_mapping.json")

from test_tradingview_bridge import raw  # noqa: E402  (shared synthetic payload builder)

ENVIRONMENT = {
    "TRADINGVIEW_WEBHOOK_ENABLED": "true",
    "TRADINGVIEW_WEBHOOK_SECRET": SECRET,
    "TRADINGVIEW_SYMBOL_MAP_FILE": MAPPING,
    "TRADINGVIEW_ALLOWED_TIMEFRAMES": "5,15,30,60",
    "TRADINGVIEW_REPLAY_STORE": "",
}


def _filled_cache(timeframe: int = 15, bars: int = 30) -> TradingViewCache:
    cache = TradingViewCache()
    now = datetime.now(timezone.utc)
    for index in range(bars):
        value = raw(timeframe, event=f"bar-{timeframe}-{index}", price=100.0 + index * 0.25)
        stamp = now - timedelta(minutes=timeframe * (bars - index - 1))
        value["bar_timestamp"] = stamp.isoformat()
        value["generated_at"] = stamp.isoformat()
        cache.put(parse_payload(value))
    return cache


class BackupProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, ENVIRONMENT)
        self.env.start()
        self.addCleanup(self.env.stop)

    def _provider(self, cache: TradingViewCache | None = None) -> TradingViewBridgeProvider:
        with patch.object(tradingview_bridge, "get_tradingview_cache", return_value=cache or _filled_cache()):
            return TradingViewBridgeProvider()

    def test_backup_requires_webhook_secret_and_mapping(self) -> None:
        self.assertTrue(self._provider().available)
        with patch.dict(os.environ, {"TRADINGVIEW_WEBHOOK_SECRET": ""}):
            self.assertFalse(self._provider().available)
        with patch.dict(os.environ, {"TRADINGVIEW_WEBHOOK_ENABLED": "false"}):
            self.assertFalse(self._provider().available)

    def test_backup_serves_verified_15m_futures_snapshot(self) -> None:
        provider = self._provider()
        value = provider.get_market_data("NIFTY FUT", interval="15m")
        self.assertEqual(value["provider"], "tradingview_alert_bridge")
        self.assertEqual(value["interval_minutes"], 15)
        self.assertEqual(value["segment"], "NSE_FNO")
        self.assertEqual(value["instrument_type"], "FUTIDX")
        self.assertTrue(value["candles"])
        self.assertTrue(all(row["completed"] for row in value["candles"]))
        self.assertFalse(value["is_stale"])
        self.assertIn("received_at", value)
        self.assertIn("exchange_timestamp", value)

    def test_only_completed_candles_are_served(self) -> None:
        provider = self._provider()
        value = provider.get_market_data("NIFTY FUT", interval="15m")
        self.assertTrue(all(row.get("provider") == "tradingview_alert_bridge" for row in value["candles"]))

    def test_unsupported_timeframe_is_rejected(self) -> None:
        provider = self._provider()
        with self.assertRaises(ProviderUnavailable):
            provider.get_market_data("NIFTY FUT", interval="7m")

    def test_unmapped_symbol_fails_safely(self) -> None:
        provider = self._provider()
        with self.assertRaises(ProviderUnavailable):
            provider.get_market_data("UNMAPPED FUT", interval="15m")

    def test_context_only_symbols_are_never_signal_contracts(self) -> None:
        provider = self._provider()
        with self.assertRaises(ProviderUnavailable):
            provider.get_market_data("GIFT NIFTY", interval="15m")

    def test_empty_cache_fails_closed(self) -> None:
        provider = self._provider(TradingViewCache())
        with self.assertRaises(ProviderUnavailable):
            provider.get_market_data("NIFTY FUT", interval="15m")

    def test_health_status_declares_backup_role(self) -> None:
        health = self._provider().health_check()
        self.assertEqual(health["provider"], "tradingview_alert_bridge")
        self.assertEqual(health["role"], "EMERGENCY_BACKUP")
        self.assertFalse(health["outranks_primary"])
        self.assertTrue(health["signal_capable"])
        self.assertGreaterEqual(health["mapped_contracts"], 1)

    def test_backup_is_last_in_production_priority(self) -> None:
        self.assertEqual(ProviderManager.DEFAULT_PRIORITY[-1], "tradingview_alert_bridge")
        self.assertEqual(ProviderManager.DEFAULT_PRIORITY[0], "angelone_primary")
        self.assertNotIn("tradingview_alert_bridge", ProviderManager.ARCHIVED_PROVIDERS)

    def test_backup_has_no_order_surface(self) -> None:
        provider = self._provider()
        for forbidden in ("place_order", "modify_order", "cancel_order", "square_off"):
            self.assertFalse(hasattr(provider, forbidden))


class MixedProviderTests(unittest.TestCase):
    def test_mixed_provider_candles_are_rejected(self) -> None:
        payload = {
            "provider": "angelone_primary",
            "candles": [
                {"timestamp": "2026-08-12T09:15:00+00:00", "close": 100, "provider": "angelone_primary"},
                {"timestamp": "2026-08-12T09:30:00+00:00", "close": 101, "provider": "tradingview_alert_bridge"},
            ],
        }
        with self.assertRaises(ProviderUnavailable):
            assert_single_provider_dataset(payload)

    def test_candles_from_a_different_provider_are_rejected(self) -> None:
        payload = {
            "provider": "angelone_primary",
            "candles": [{"timestamp": "2026-08-12T09:15:00+00:00", "close": 100, "provider": "tradingview_alert_bridge"}],
        }
        with self.assertRaises(ProviderUnavailable):
            assert_single_provider_dataset(payload)

    def test_single_provider_dataset_is_accepted(self) -> None:
        payload = {
            "provider": "angelone_primary",
            "candles": [{"timestamp": "2026-08-12T09:15:00+00:00", "close": 100, "provider": "angelone_primary"}],
        }
        self.assertEqual(assert_single_provider_dataset(payload), "angelone_primary")


if __name__ == "__main__":
    unittest.main()
