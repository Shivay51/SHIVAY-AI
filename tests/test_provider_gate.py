"""Step 4: provider manager priority, cache keys, freshness and session gating."""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import market_session
import provider_manager
from provider_failover import ProviderUnavailable
from provider_manager import CACHE_TTL_SECONDS, MAX_CLOCK_SKEW_SECONDS, ProviderManager

IST = ZoneInfo("Asia/Kolkata")


def _snapshot(**overrides) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    value = {
        "symbol": "NIFTY FUT",
        "underlying": "NIFTY",
        "exchange": "NSE",
        "segment": "NSE_FNO",
        "instrument_type": "FUTIDX",
        "expiry": (date.today() + timedelta(days=7)).isoformat(),
        "lot_size": 75,
        "tick_size": 0.05,
        "security_id": "1002",
        "instrument_key": "NFO:1002",
        "interval_minutes": 15,
        "price": 24700.0,
        "exchange_timestamp": now.isoformat(),
        "received_at": now.isoformat(),
        "timestamp": now.isoformat(),
        "is_stale": False,
        "candles": [{"timestamp": now.isoformat(), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "completed": True}],
    }
    value.update(overrides)
    return value


class CacheKeyTests(unittest.TestCase):
    def test_key_includes_provider_contract_exchange_and_timeframe(self) -> None:
        value = _snapshot()
        key = ProviderManager._cache_key("angelone_primary", "NIFTY FUT", value)
        for part in ("angelone_primary", "NIFTY FUT", "NFO:1002", "NSE", "NSE_FNO", value["expiry"], "15"):
            self.assertIn(str(part), key)

    def test_different_timeframe_and_provider_never_share_a_key(self) -> None:
        base = _snapshot()
        other_timeframe = _snapshot(interval_minutes=5)
        other_contract = _snapshot(expiry=(date.today() + timedelta(days=40)).isoformat(), instrument_key="NFO:1003")
        keys = {
            ProviderManager._cache_key("angelone_primary", "NIFTY FUT", base),
            ProviderManager._cache_key("tradingview_alert_bridge", "NIFTY FUT", base),
            ProviderManager._cache_key("angelone_primary", "NIFTY FUT", other_timeframe),
            ProviderManager._cache_key("angelone_primary", "NIFTY FUT", other_contract),
        }
        self.assertEqual(len(keys), 4)


class FreshnessGateTests(unittest.TestCase):
    def test_fresh_entry_is_usable(self) -> None:
        self.assertTrue(ProviderManager._cache_entry_usable(_snapshot()))

    def test_stale_entry_is_rejected(self) -> None:
        self.assertFalse(ProviderManager._cache_entry_usable(_snapshot(is_stale=True)))

    def test_old_exchange_timestamp_is_rejected(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=max(CACHE_TTL_SECONDS * 4, 180) + 60)).isoformat()
        self.assertFalse(ProviderManager._cache_entry_usable(_snapshot(exchange_timestamp=old, timestamp=old)))

    def test_future_timestamp_beyond_clock_skew_is_rejected(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS + 60)).isoformat()
        self.assertFalse(ProviderManager._cache_entry_usable(_snapshot(exchange_timestamp=future, timestamp=future)))

    def test_missing_candles_are_rejected(self) -> None:
        self.assertFalse(ProviderManager._cache_entry_usable(_snapshot(candles=[])))

    def test_missing_timestamp_is_rejected(self) -> None:
        value = _snapshot()
        value.pop("exchange_timestamp")
        value.pop("timestamp")
        self.assertFalse(ProviderManager._cache_entry_usable(value))

    def test_received_before_exchange_time_is_rejected(self) -> None:
        exchange_time = datetime.now(timezone.utc)
        received = exchange_time - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS + 30)
        self.assertFalse(ProviderManager._cache_entry_usable(_snapshot(exchange_timestamp=exchange_time.isoformat(), received_at=received.isoformat())))


class _StubProvider:
    signal_capable = True
    available = True

    def __init__(self, name: str, value: dict | None, error: Exception | None = None) -> None:
        self.name = name
        self.value = value
        self.error = error
        self.calls = 0

    def get_market_data(self, symbol, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return dict(self.value or {})

    def get_many(self, symbols, **kwargs):
        return {symbol: self.get_market_data(symbol, **kwargs) for symbol in symbols}

    def get_live_price(self, symbol):
        return float(self.get_market_data(symbol)["price"])

    def health_check(self):
        return {"provider": self.name}

    def close(self):
        return None


class VerifiedReadTests(unittest.TestCase):
    def setUp(self) -> None:
        from provider_health import reset_provider_health

        reset_provider_health()
        provider_manager.reset_provider_manager()
        self.addCleanup(provider_manager.reset_provider_manager)
        self.manager = ProviderManager()

    def _install(self, primary: _StubProvider, backup: _StubProvider) -> None:
        self.manager.angel = primary
        self.manager.tradingview = backup
        self.manager.providers = [primary, backup]

    def test_verified_read_is_cached_and_not_refetched(self) -> None:
        primary = _StubProvider("angelone_primary", _snapshot(verified=True, is_live=True, is_delayed=False))
        backup = _StubProvider("tradingview_alert_bridge", None, ProviderUnavailable("empty"))
        self._install(primary, backup)
        first = self.manager.get_verified_market_data("NIFTY FUT", interval="15m")
        second = self.manager.get_verified_market_data("NIFTY FUT", interval="15m")
        self.assertEqual(first["provider_market_state"] if "provider_market_state" in first else first["market_state"], second["market_state"])
        self.assertEqual(primary.calls, 1)
        self.assertIn("market_state", first)
        self.assertIn("signals_allowed", first)

    def test_stale_provider_data_is_never_cached_or_returned(self) -> None:
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        primary = _StubProvider("angelone_primary", _snapshot(verified=True, is_live=True, exchange_timestamp=stale_time, timestamp=stale_time))
        backup = _StubProvider("tradingview_alert_bridge", None, ProviderUnavailable("empty"))
        self._install(primary, backup)
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("NIFTY FUT", interval="15m")
        self.assertEqual(self.manager.cache.status()["entries"], 0)

    def test_expired_contract_is_rejected(self) -> None:
        expired = _snapshot(verified=True, is_live=True, expiry=(date.today() - timedelta(days=2)).isoformat())
        primary = _StubProvider("angelone_primary", expired)
        backup = _StubProvider("tradingview_alert_bridge", None, ProviderUnavailable("empty"))
        self._install(primary, backup)
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("NIFTY FUT", interval="15m")

    def test_cash_segment_cannot_satisfy_a_futures_request(self) -> None:
        cash = _snapshot(verified=True, is_live=True, segment="CASH", instrument_type="EQUITY")
        primary = _StubProvider("angelone_primary", cash)
        backup = _StubProvider("tradingview_alert_bridge", None, ProviderUnavailable("empty"))
        self._install(primary, backup)
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("NIFTY FUT", interval="15m")

    def test_exchange_mismatch_is_rejected(self) -> None:
        wrong = _snapshot(verified=True, is_live=True, exchange="MCX", segment="MCX_COMM", instrument_type="FUTCOM")
        primary = _StubProvider("angelone_primary", wrong)
        backup = _StubProvider("tradingview_alert_bridge", None, ProviderUnavailable("empty"))
        self._install(primary, backup)
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("NIFTY FUT", interval="15m")

    def test_failover_to_backup_then_no_hidden_third_provider(self) -> None:
        primary = _StubProvider("angelone_primary", None, ProviderUnavailable("angel_down"))
        backup = _StubProvider("tradingview_alert_bridge", _snapshot(verified=True, is_live=True, provider="tradingview_alert_bridge"))
        self._install(primary, backup)
        value = self.manager.get_verified_market_data("NIFTY FUT", interval="15m")
        self.assertEqual(value["security_id"], "1002")
        self.assertEqual(len(self.manager.providers), 2)

    def test_both_providers_down_raises_no_fresh_data(self) -> None:
        primary = _StubProvider("angelone_primary", None, ProviderUnavailable("angel_down"))
        backup = _StubProvider("tradingview_alert_bridge", None, ProviderUnavailable("cache_empty"))
        self._install(primary, backup)
        with self.assertRaises(ProviderUnavailable):
            self.manager.get_verified_market_data("NIFTY FUT", interval="15m")
        self.assertEqual(self.manager.status()["no_data_decision"], "NO FRESH DATA / NO SIGNAL")

    def test_cache_invalidation_forces_a_refetch(self) -> None:
        primary = _StubProvider("angelone_primary", _snapshot(verified=True, is_live=True))
        backup = _StubProvider("tradingview_alert_bridge", None, ProviderUnavailable("empty"))
        self._install(primary, backup)
        self.manager.get_verified_market_data("NIFTY FUT", interval="15m")
        self.manager.invalidate_cache()
        self.manager.get_verified_market_data("NIFTY FUT", interval="15m")
        self.assertEqual(primary.calls, 2)


class MarketSessionTests(unittest.TestCase):
    def test_nse_session_window(self) -> None:
        moment = datetime(2026, 8, 12, 10, 30, tzinfo=IST)
        self.assertTrue(market_session.is_market_open("NSE_FNO", moment))
        self.assertEqual(market_session.market_state("NSE_FNO", moment), "OPEN")
        self.assertTrue(market_session.signals_allowed("NSE_FNO", moment))

    def test_before_open_and_after_close_are_not_tradeable(self) -> None:
        self.assertEqual(market_session.market_state("NSE_FNO", datetime(2026, 8, 12, 9, 5, tzinfo=IST)), "PRE_OPEN")
        self.assertEqual(market_session.market_state("NSE_FNO", datetime(2026, 8, 12, 16, 0, tzinfo=IST)), "CLOSED")
        self.assertFalse(market_session.signals_allowed("NSE_FNO", datetime(2026, 8, 12, 16, 0, tzinfo=IST)))

    def test_weekend_is_closed(self) -> None:
        self.assertEqual(market_session.market_state("NSE_FNO", datetime(2026, 8, 15, 11, 0, tzinfo=IST)), "CLOSED_WEEKEND")

    def test_holiday_is_closed(self) -> None:
        with patch.dict("os.environ", {"MARKET_HOLIDAYS": "2026-08-12"}):
            self.assertEqual(market_session.market_state("NSE_FNO", datetime(2026, 8, 12, 11, 0, tzinfo=IST)), "CLOSED_HOLIDAY")
            self.assertFalse(market_session.signals_allowed("NSE_FNO", datetime(2026, 8, 12, 11, 0, tzinfo=IST)))

    def test_mcx_evening_session_is_open_when_nse_is_closed(self) -> None:
        moment = datetime(2026, 8, 12, 21, 0, tzinfo=IST)
        self.assertTrue(market_session.is_market_open("MCX_COMM", moment))
        self.assertFalse(market_session.is_market_open("NSE_FNO", moment))

    def test_next_open_is_a_future_trading_day(self) -> None:
        value = market_session.next_open("NSE_FNO", datetime(2026, 8, 14, 16, 0, tzinfo=IST))
        self.assertEqual(value.weekday(), 0)
        self.assertEqual((value.hour, value.minute), (9, 15))

    def test_status_payload_is_complete(self) -> None:
        value = market_session.status("NSE_FNO", datetime(2026, 8, 12, 10, 0, tzinfo=IST))
        for field in ("segment", "state", "is_open", "signals_allowed", "session_start", "session_end", "next_open", "trading_day"):
            self.assertIn(field, value)


if __name__ == "__main__":
    unittest.main()
