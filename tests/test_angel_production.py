"""Angel One primary-provider and two-provider architecture tests."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import angel_instruments
import provider_manager
from angel_provider import AngelProviderError, AngelRateLimited, AngelReadOnlyProvider, generate_totp
from provider_failover import ProviderUnavailable

CREDENTIALS = {
    "ANGEL_API_KEY": "test-key",
    "ANGEL_CLIENT_CODE": "T1234",
    "ANGEL_MPIN": "1234",
    "ANGEL_TOTP_SECRET": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
}


def _master_rows(reference: date) -> list[dict[str, str]]:
    expired = reference - timedelta(days=10)
    near = reference + timedelta(days=7)
    far = reference + timedelta(days=40)

    def stamp(value: date) -> str:
        return value.strftime("%d%b%Y").upper()

    return [
        {"token": "1001", "symbol": f"NIFTY{stamp(expired)}FUT", "name": "NIFTY", "expiry": stamp(expired), "lotsize": "75", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
        {"token": "1002", "symbol": f"NIFTY{stamp(near)}FUT", "name": "NIFTY", "expiry": stamp(near), "lotsize": "75", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
        {"token": "1003", "symbol": f"NIFTY{stamp(far)}FUT", "name": "NIFTY", "expiry": stamp(far), "lotsize": "75", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
        {"token": "2002", "symbol": f"BANKNIFTY{stamp(near)}FUT", "name": "BANKNIFTY", "expiry": stamp(near), "lotsize": "30", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
        {"token": "3002", "symbol": f"GOLD{stamp(near)}FUT", "name": "GOLD", "expiry": stamp(near), "lotsize": "100", "instrumenttype": "FUTCOM", "exch_seg": "MCX", "tick_size": "100.000000"},
        {"token": "3003", "symbol": f"SILVER{stamp(near)}FUT", "name": "SILVER", "expiry": stamp(near), "lotsize": "30", "instrumenttype": "FUTCOM", "exch_seg": "MCX", "tick_size": "100.000000"},
        {"token": "4001", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "expiry": "", "lotsize": "1", "instrumenttype": "", "exch_seg": "nse_cm", "tick_size": "5.000000"},
    ]


IST = ZoneInfo("Asia/Kolkata")


def _quote(price: float = 24700.0) -> dict[str, object]:
    return {
        "ltp": price,
        "open": price - 40,
        "high": price + 60,
        "low": price - 70,
        "close": price - 20,
        "tradeVolume": 152000,
        "opnInterest": 480000,
        "prevOpnInterest": 470000,
        "exchFeedTime": datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S"),
        "depth": {"buy": [{"price": price - 0.5}], "sell": [{"price": price + 0.5}]},
        "upperCircuit": price * 1.1,
        "lowerCircuit": price * 0.9,
    }


def _candles(count: int = 40, price: float = 24700.0) -> list[list[object]]:
    now = datetime.now(IST)
    rows = []
    for index in range(count):
        stamp = now - timedelta(minutes=15 * (count - index))
        close = price - (count - index) * 2
        rows.append([stamp.isoformat(), close - 5, close + 12, close - 12, close, 1000 + index])
    rows[-1][4] = price
    return rows


class _Transport:
    """Deterministic SmartAPI transport replacement."""

    def __init__(self, price: float = 24700.0, fail: str | None = None) -> None:
        self.price = price
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, provider, path, payload, authenticated=True):
        self.calls.append(path)
        if self.fail == "network":
            raise AngelProviderError("angel_network_error:URLError")
        if self.fail == "rate":
            raise AngelRateLimited("angel_rate_limited:429")
        if path.endswith("loginByPassword"):
            assert payload["totp"].isdigit() and len(payload["totp"]) == 6
            return {"status": True, "data": {"jwtToken": "jwt-1", "refreshToken": "refresh-1", "feedToken": "feed-1"}}
        if path.endswith("generateTokens"):
            return {"status": True, "data": {"jwtToken": "jwt-2", "refreshToken": "refresh-2", "feedToken": "feed-2"}}
        if path.endswith("quote/"):
            return {"status": True, "data": {"fetched": [_quote(self.price)], "unfetched": []}}
        if path.endswith("getCandleData"):
            return {"status": True, "data": _candles(price=self.price)}
        if path.endswith("logout"):
            return {"status": True, "data": {}}
        raise AssertionError(f"unexpected read-only path: {path}")


def _provider(transport: _Transport | None = None) -> tuple[AngelReadOnlyProvider, _Transport]:
    transport = transport or _Transport()
    with patch.dict("os.environ", CREDENTIALS, clear=False):
        provider = AngelReadOnlyProvider()
    provider._request = lambda path, payload, authenticated=True: transport(provider, path, payload, authenticated)  # type: ignore[assignment]
    return provider, transport


class TotpTests(unittest.TestCase):
    def test_rfc6238_reference_vector(self) -> None:
        # RFC 4226/6238 reference secret "12345678901234567890" -> base32 GEZD...
        self.assertEqual(generate_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59), "287082")
        self.assertEqual(generate_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=1111111109), "081804")

    def test_code_is_generated_fresh_and_never_stored(self) -> None:
        provider, _ = _provider()
        self.assertEqual(len(generate_totp(provider.totp_secret)), 6)
        self.assertNotIn("totp_code", vars(provider))

    def test_invalid_secret_fails_closed(self) -> None:
        with self.assertRaises(AngelProviderError):
            generate_totp("!!!not-base32!!!")


class InstrumentMasterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = datetime.now(timezone.utc).date()
        self.rows = _master_rows(self.today)
        patcher = patch.object(angel_instruments, "load_instruments", return_value=self.rows)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_nearest_valid_nifty_future_is_selected(self) -> None:
        contract = angel_instruments.resolve("NIFTY FUT")
        self.assertEqual(contract["angel_token"], "1002")
        self.assertEqual(contract["exchange"], "NSE")
        self.assertEqual(contract["segment"], "NSE_FNO")
        self.assertEqual(contract["instrument_type"], "FUTIDX")
        self.assertGreaterEqual(date.fromisoformat(contract["expiry"]), self.today)
        self.assertEqual(contract["lot_size"], 75)
        self.assertEqual(contract["tick_size"], 0.05)
        self.assertTrue(contract["verified"])

    def test_banknifty_and_mcx_contracts_resolve_to_correct_exchanges(self) -> None:
        bank = angel_instruments.resolve("BANKNIFTY FUT")
        gold = angel_instruments.resolve("MCX GOLD")
        silver = angel_instruments.resolve("MCX SILVER")
        self.assertEqual((bank["exchange"], bank["angel_exchange"]), ("NSE", "NFO"))
        self.assertEqual((gold["exchange"], gold["segment"], gold["instrument_type"]), ("MCX", "MCX_COMM", "FUTCOM"))
        self.assertEqual(silver["underlying"], "SILVER")

    def test_equity_resolution_uses_cash_segment(self) -> None:
        equity = angel_instruments.resolve("RELIANCE")
        self.assertEqual(equity["trading_symbol"], "RELIANCE-EQ")
        self.assertEqual(equity["instrument_type"], "EQUITY")

    def test_expired_contract_is_never_returned(self) -> None:
        contract = angel_instruments.resolve_futures_contract("NIFTY FUT", on_date=self.today)
        self.assertNotEqual(contract["angel_token"], "1001")

    def test_unknown_future_returns_none(self) -> None:
        self.assertIsNone(angel_instruments.resolve("NOSUCHSCRIP FUT"))


class AngelMarketDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = datetime.now(timezone.utc).date()
        patcher = patch.object(angel_instruments, "load_instruments", return_value=_master_rows(self.today))
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_login_generates_session_tokens(self) -> None:
        provider, transport = _provider()
        provider.connect()
        self.assertTrue(provider.session_valid)
        self.assertEqual(provider.feed_token, "feed-1")
        self.assertIn("/rest/auth/angelbroking/user/v1/loginByPassword", transport.calls)

    def test_session_refresh_renews_without_relogin(self) -> None:
        provider, transport = _provider()
        provider.connect()
        provider.refresh_session()
        self.assertEqual(provider.jwt_token, "jwt-2")
        self.assertEqual(transport.calls.count("/rest/auth/angelbroking/user/v1/loginByPassword"), 1)

    def test_verified_futures_snapshot_is_complete_and_live(self) -> None:
        provider, _ = _provider()
        value = provider.get_market_data("NIFTY FUT", period="5d", interval="15m")
        for field in ("price", "open_value", "day_high", "day_low", "latest_volume", "open_interest",
                      "exchange_timestamp", "received_at", "candles", "interval_minutes", "lot_size",
                      "tick_size", "security_id", "instrument_key", "expiry", "data_quality"):
            self.assertIn(field, value)
        self.assertTrue(value["is_live"])
        self.assertFalse(value["is_delayed"])
        self.assertFalse(value["is_stale"])
        self.assertTrue(value["verified"])
        self.assertEqual(value["provider"], "angelone_primary")
        self.assertEqual(value["interval_minutes"], 15)
        self.assertTrue(value["data_quality"]["valid"], value["data_quality"]["errors"])

    def test_all_required_timeframes_are_supported(self) -> None:
        provider, _ = _provider()
        for minutes in (5, 15, 30, 60):
            candles = provider.get_candles("NIFTY FUT", minutes, "5d")
            self.assertTrue(candles)
            self.assertTrue(all(item["completed"] for item in candles))

    def test_unsupported_timeframe_is_rejected(self) -> None:
        provider, _ = _provider()
        with self.assertRaises(AngelProviderError):
            provider.get_candles("NIFTY FUT", 7, "5d")

    def test_mcx_contract_passes_contract_validation(self) -> None:
        from data_quality import validate_instrument_contract

        provider, _ = _provider(_Transport(price=71500.0))
        value = provider.get_market_data("MCX GOLD", interval="15m")
        self.assertEqual(validate_instrument_contract(value, "MCX GOLD"), [])

    def test_live_price_and_ohlc_helpers(self) -> None:
        provider, _ = _provider()
        self.assertAlmostEqual(provider.get_live_price("NIFTY FUT"), 24700.0, places=2)
        ohlc = provider.get_ohlc("NIFTY FUT")
        self.assertEqual(set(ohlc) >= {"open", "high", "low", "close", "volume", "open_interest"}, True)

    def test_freshness_status_reports_exchange_and_receive_time(self) -> None:
        provider, _ = _provider()
        status = provider.freshness_status("NIFTY FUT")
        self.assertEqual(status["status"], "GOOD")
        self.assertFalse(status["is_stale"])

    def test_network_failure_is_reported_not_faked(self) -> None:
        provider, _ = _provider(_Transport(fail="network"))
        with self.assertRaises(AngelProviderError):
            provider.get_market_data("NIFTY FUT")

    def test_rate_limit_is_surfaced(self) -> None:
        provider, _ = _provider(_Transport(fail="rate"))
        with self.assertRaises(AngelRateLimited):
            provider.get_market_data("NIFTY FUT")

    def test_unconfigured_provider_fails_closed(self) -> None:
        with patch.dict("os.environ", {"ANGEL_API_KEY": "", "ANGEL_CLIENT_CODE": "", "ANGEL_CLIENT_ID": "", "ANGEL_MPIN": "", "ANGEL_PIN": "", "ANGEL_PASSWORD": "", "ANGEL_TOTP_SECRET": "", "ANGEL_TOTP": ""}, clear=False):
            provider = AngelReadOnlyProvider()
        self.assertFalse(provider.configured)
        self.assertFalse(provider.available)
        self.assertFalse(provider.authenticate())
        self.assertIn("ANGEL_API_KEY", provider.missing_credentials())

    def test_provider_is_signal_capable_and_read_only(self) -> None:
        provider, _ = _provider()
        self.assertTrue(provider.signal_capable)
        for forbidden in ("place_order", "modify_order", "cancel_order", "square_off", "exit_position"):
            self.assertFalse(hasattr(provider, forbidden), forbidden)


class _FailingProvider:
    name = "angelone_primary"
    signal_capable = True
    available = True
    configured = True

    def health_check(self):
        return {"provider": self.name, "configured": True, "connected": False}

    def get_market_data(self, symbol, **kwargs):
        raise ProviderUnavailable("angel_unavailable")

    def get_many(self, symbols, **kwargs):
        raise ProviderUnavailable("angel_unavailable")

    def get_live_price(self, symbol):
        raise ProviderUnavailable("angel_unavailable")

    def close(self):
        return None


class _BackupProvider:
    name = "tradingview_alert_bridge"
    signal_capable = True
    available = True

    def __init__(self, payload=None):
        self.payload = payload

    def get_market_data(self, symbol, **kwargs):
        if self.payload is None:
            raise ProviderUnavailable("tradingview_cache_empty")
        return dict(self.payload)

    def get_many(self, symbols, **kwargs):
        return {symbol: self.get_market_data(symbol) for symbol in symbols}

    def get_live_price(self, symbol):
        return float(self.get_market_data(symbol)["price"])

    def close(self):
        return None


class ProviderArchitectureTests(unittest.TestCase):
    def setUp(self) -> None:
        from provider_health import reset_provider_health

        reset_provider_health()
        provider_manager.reset_provider_manager()
        self.addCleanup(provider_manager.reset_provider_manager)
        patcher = patch.object(angel_instruments, "load_instruments", return_value=_master_rows(datetime.now(timezone.utc).date()))
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_exactly_two_providers_with_angel_first(self) -> None:
        manager = provider_manager.ProviderManager()
        self.assertEqual(manager.active_provider_names(), ["angelone_primary", "tradingview_alert_bridge"])
        self.assertEqual(len(manager.providers), 2)
        self.assertEqual([p.name for p in manager._ranked()][0], "angelone_primary")

    def test_startup_assertion_passes_for_production_architecture(self) -> None:
        self.assertEqual(provider_manager.assert_production_providers(), ["angelone_primary", "tradingview_alert_bridge"])

    def test_archived_providers_are_never_registered(self) -> None:
        manager = provider_manager.ProviderManager()
        registered = set(manager.active_provider_names())
        for name in manager.ARCHIVED_PROVIDERS:
            self.assertNotIn(name, registered)

    def test_status_reports_two_providers_and_no_third_fallback(self) -> None:
        manager = provider_manager.ProviderManager()
        status = manager.status()
        self.assertEqual(status["providers"], ["angelone_primary", "tradingview_alert_bridge"])
        self.assertEqual(status["active_provider_count"], 2)
        self.assertEqual(status["fallback"], "tradingview_alert_bridge_only")
        self.assertEqual(status["no_data_decision"], "NO FRESH DATA / NO SIGNAL")

    def test_failover_uses_backup_then_recovers_to_angel(self) -> None:
        manager = provider_manager.ProviderManager()
        payload = {"symbol": "NIFTY FUT", "price": 24700.0, "provider": "tradingview_alert_bridge"}
        manager.angel = _FailingProvider()
        manager.tradingview = _BackupProvider(payload)
        manager.providers = [manager.angel, manager.tradingview]
        value = manager.get_market_data("NIFTY FUT")
        self.assertEqual(value["provider"], "tradingview_alert_bridge")

        healthy, _ = _provider()
        manager.angel = healthy
        manager.providers = [healthy, manager.tradingview]
        recovered = manager.get_market_data("NIFTY FUT", interval="15m")
        self.assertEqual(recovered["provider"], "angelone_primary")

    def test_total_failure_raises_no_fresh_data(self) -> None:
        manager = provider_manager.ProviderManager()
        manager.angel = _FailingProvider()
        manager.tradingview = _BackupProvider(None)
        manager.providers = [manager.angel, manager.tradingview]
        with self.assertRaises(ProviderUnavailable):
            manager.get_market_data("NIFTY FUT")

    def test_backup_data_cannot_pass_verification_without_contract_fields(self) -> None:
        manager = provider_manager.ProviderManager()
        manager.angel = _FailingProvider()
        manager.tradingview = _BackupProvider({"symbol": "NIFTY FUT", "price": 24700.0, "is_live": True, "verified": True})
        manager.providers = [manager.angel, manager.tradingview]
        with self.assertRaises(ProviderUnavailable):
            manager.get_verified_market_data("NIFTY FUT")

    def test_unsafe_registration_is_rejected(self) -> None:
        manager = provider_manager.ProviderManager()
        manager.providers = [manager.tradingview, manager.angel]
        with self.assertRaises(RuntimeError):
            manager._assert_production_architecture()


if __name__ == "__main__":
    unittest.main()
