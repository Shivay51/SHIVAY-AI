"""Runtime data-plane architecture tests.

Enforces: Angel One SmartAPI -> authenticated TradingView backup -> NO SIGNAL.
No other provider may be registered, and degraded/delayed data may never be
promoted into a signal.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
import provider_health
from provider_failover import ProviderUnavailable
from provider_manager import ProviderManager, reset_provider_manager

RETIRED = (
    "nse_temporary", "tvkit_ohlcv", "mcx_temporary", "groww_primary",
    "upstox_primary", "dhan_primary", "shoonya_primary", "truedata_primary",
    "gdfl_primary", "fyers_primary", "market_hub", "yahoo_emergency",
)


class _FakeProvider:
    signal_capable = True

    def __init__(self, name: str, available: bool = True, payload: dict | None = None,
                 error: Exception | None = None) -> None:
        self.name = name
        self.available = available
        self.payload = payload
        self.error = error
        self.calls = 0

    def get_market_data(self, symbol: str, **kwargs) -> dict:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.payload or {})

    def get_many(self, symbols: list[str], **kwargs) -> dict:
        return {symbol: self.get_market_data(symbol, **kwargs) for symbol in symbols}

    def get_live_price(self, symbol: str) -> float:
        return float(self.get_market_data(symbol)["price"])


def _verified_payload(provider: str, price: float = 25000.0) -> dict:
    return {
        "symbol": "NIFTY FUT", "trading_symbol": "NIFTY28AUG2026FUT",
        "exchange": "NSE", "segment": "NSE_FNO", "instrument_type": "FUTIDX",
        "expiry": (datetime.now(timezone.utc) + timedelta(days=15)).date().isoformat(),
        "lot_size": 75, "tick_size": 0.05, "security_id": "58001",
        "instrument_key": "NFO|58001", "underlying": "NIFTY",
        "price": price, "open_value": price * 0.99, "day_high": price * 1.01,
        "day_low": price * 0.98, "latest_volume": 10000,
        "timestamp": datetime.now(timezone.utc), "provider": provider,
        "is_live": True, "is_delayed": False, "is_stale": False, "verified": True,
    }


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        provider_health.reset_provider_health() if hasattr(provider_health, "reset_provider_health") else None
        self.addCleanup(reset_provider_manager)

    def test_only_two_providers_are_permitted(self) -> None:
        self.assertEqual(ProviderManager.RUNTIME_PROVIDERS,
                         ("angelone_primary", "tradingview_alert_bridge"))

    def test_every_other_provider_is_disabled(self) -> None:
        for name in RETIRED:
            self.assertIn(name, ProviderManager.DISABLED_PROVIDERS, name)

    def test_manager_never_instantiates_retired_providers(self) -> None:
        manager = ProviderManager()
        for provider in manager.providers:
            self.assertIn(provider.name, ProviderManager.RUNTIME_PROVIDERS)
        self.assertFalse(hasattr(manager, "groww"))
        self.assertFalse(hasattr(manager, "upstox"))
        self.assertFalse(hasattr(manager, "yahoo"))
        self.assertFalse(hasattr(manager, "market_hub"))

    def test_config_default_priority_is_angel_then_tradingview(self) -> None:
        self.assertEqual(tuple(config.PROVIDER_PRIORITY)[:2],
                         ("angelone_primary", "tradingview_alert_bridge"))

    def test_angel_outranks_tradingview_even_if_config_inverts_it(self) -> None:
        original = config.PROVIDER_PRIORITY
        config.PROVIDER_PRIORITY = ("tradingview_alert_bridge", "angelone_primary")
        try:
            ranked = ProviderManager._base_priority()
            self.assertGreater(ranked["angelone_primary"], ranked["tradingview_alert_bridge"])
        finally:
            config.PROVIDER_PRIORITY = original

    def test_retired_provider_keys_in_config_are_ignored(self) -> None:
        original = config.PROVIDER_PRIORITY
        config.PROVIDER_PRIORITY = ("yahoo_emergency", "groww_primary", "angelone_primary")
        try:
            ranked = ProviderManager._base_priority()
            self.assertEqual(set(ranked), set(ProviderManager.RUNTIME_PROVIDERS))
        finally:
            config.PROVIDER_PRIORITY = original


class FailoverChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(reset_provider_manager)
        self.manager = ProviderManager()

    def _install(self, providers: list[_FakeProvider]) -> None:
        self.manager.providers = providers
        for provider in providers:
            provider_health.record_success(provider.name, 10.0)

    def test_angel_is_used_when_healthy(self) -> None:
        angel = _FakeProvider("angelone_primary", payload=_verified_payload("angelone_primary"))
        bridge = _FakeProvider("tradingview_alert_bridge", payload=_verified_payload("tradingview_alert_bridge"))
        self._install([angel, bridge])
        data = self.manager.get_verified_market_data("NIFTY FUT")
        self.assertEqual(data["provider"], "angelone_primary")
        self.assertEqual(bridge.calls, 0)

    def test_tradingview_takes_over_when_angel_fails(self) -> None:
        angel = _FakeProvider("angelone_primary", error=ProviderUnavailable("angel_down"))
        bridge = _FakeProvider("tradingview_alert_bridge", payload=_verified_payload("tradingview_alert_bridge"))
        self._install([angel, bridge])
        data = self.manager.get_verified_market_data("NIFTY FUT")
        self.assertEqual(data["provider"], "tradingview_alert_bridge")

    def test_no_signal_when_both_providers_fail(self) -> None:
        angel = _FakeProvider("angelone_primary", error=ProviderUnavailable("angel_down"))
        bridge = _FakeProvider("tradingview_alert_bridge", error=ProviderUnavailable("bridge_down"))
        self._install([angel, bridge])
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("NIFTY FUT")

    def test_delayed_data_is_never_promoted_to_a_signal(self) -> None:
        payload = _verified_payload("angelone_primary")
        payload.update(is_live=False, is_delayed=True)
        angel = _FakeProvider("angelone_primary", payload=payload)
        self._install([angel])
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("NIFTY FUT")

    def test_stale_data_is_rejected(self) -> None:
        payload = _verified_payload("angelone_primary")
        payload["timestamp"] = datetime.now(timezone.utc) - timedelta(hours=4)
        angel = _FakeProvider("angelone_primary", payload=payload)
        self._install([angel])
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("NIFTY FUT")

    def test_comex_gold_cannot_masquerade_as_mcx(self) -> None:
        payload = _verified_payload("angelone_primary")
        payload.update(symbol="MCX GOLD", exchange="COMEX", segment="COMMODITY",
                       instrument_type="GLOBAL_FUTURE", trading_symbol="GC=F")
        angel = _FakeProvider("angelone_primary", payload=payload)
        self._install([angel])
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("MCX GOLD")


class StatusReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(reset_provider_manager)
        self.manager = ProviderManager()

    def test_status_declares_architecture_and_no_emergency_provider(self) -> None:
        status = self.manager.status()
        self.assertEqual(status["architecture"],
                         "angelone_primary -> tradingview_alert_bridge -> NO_SIGNAL")
        self.assertFalse(status["emergency_delayed_provider_registered"])
        self.assertEqual(status["fallback"], "none_no_signal_when_no_fresh_data")
        self.assertEqual(status["tradingview_role"], "authenticated_emergency_backup_only")

    def test_status_without_credentials_reports_no_signal(self) -> None:
        self.manager.providers = []
        status = self.manager.status()
        self.assertEqual(status["mode"], "NO_FRESH_DATA_NO_SIGNAL")
        self.assertFalse(status["signal_allowed"])

    def test_status_reports_angel_health_and_instrument_master(self) -> None:
        status = self.manager.status()
        self.assertIn("angel", status)
        self.assertIn("totp_automation", status["angel"])
        self.assertIn("angel_instrument_master", status)

    def test_status_lists_disabled_providers(self) -> None:
        status = self.manager.status()
        for name in RETIRED:
            self.assertIn(name, status["disabled_providers"])


if __name__ == "__main__":
    unittest.main()
