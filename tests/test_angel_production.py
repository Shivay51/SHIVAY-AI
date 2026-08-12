"""Angel One production-completion tests (offline, no network, no live orders)."""
from __future__ import annotations

import ast
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import angel_instruments
import angel_provider
from angel_provider import AngelProviderError, AngelReadOnlyProvider
from angel_totp import generate_totp, is_valid_secret, seconds_remaining

SECRET = "JBSWY3DPEHPK3PXP"


def _scrip_rows(expiry: str, far_expiry: str) -> list[dict]:
    return [
        {"token": "58001", "symbol": f"NIFTY{expiry}FUT", "name": "NIFTY", "expiry": expiry,
         "lotsize": "75", "tick_size": "5", "instrumenttype": "FUTIDX", "exch_seg": "NFO"},
        {"token": "58002", "symbol": f"NIFTY{far_expiry}FUT", "name": "NIFTY", "expiry": far_expiry,
         "lotsize": "75", "tick_size": "5", "instrumenttype": "FUTIDX", "exch_seg": "NFO"},
        {"token": "58101", "symbol": f"BANKNIFTY{expiry}FUT", "name": "BANKNIFTY", "expiry": expiry,
         "lotsize": "35", "tick_size": "5", "instrumenttype": "FUTIDX", "exch_seg": "NFO"},
        {"token": "42001", "symbol": f"GOLD{expiry}FUT", "name": "GOLD", "expiry": expiry,
         "lotsize": "100", "tick_size": "1", "instrumenttype": "FUTCOM", "exch_seg": "MCX"},
        {"token": "42002", "symbol": f"GOLDM{expiry}FUT", "name": "GOLD", "expiry": expiry,
         "lotsize": "10", "tick_size": "1", "instrumenttype": "FUTCOM", "exch_seg": "MCX"},
        {"token": "43001", "symbol": f"SILVER{expiry}FUT", "name": "SILVER", "expiry": expiry,
         "lotsize": "30", "tick_size": "1", "instrumenttype": "FUTCOM", "exch_seg": "MCX"},
        {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "expiry": "",
         "lotsize": "1", "tick_size": "5", "instrumenttype": "", "exch_seg": "NSE"},
    ]


class TOTPTests(unittest.TestCase):
    def test_known_vector_is_stable(self) -> None:
        # RFC 6238 style check: code is deterministic for a fixed timestamp.
        first = generate_totp(SECRET, timestamp=59)
        self.assertEqual(first, generate_totp(SECRET, timestamp=59))
        self.assertEqual(len(first), 6)
        self.assertTrue(first.isdigit())

    def test_code_rotates_across_windows(self) -> None:
        self.assertNotEqual(generate_totp(SECRET, timestamp=0), generate_totp(SECRET, timestamp=90))

    def test_invalid_secret_is_rejected(self) -> None:
        self.assertFalse(is_valid_secret("not base32 !!"))
        self.assertFalse(is_valid_secret(""))
        self.assertTrue(is_valid_secret(SECRET))

    def test_window_countdown_is_bounded(self) -> None:
        self.assertTrue(1 <= seconds_remaining(timestamp=10) <= 30)


class InstrumentMasterTests(unittest.TestCase):
    def setUp(self) -> None:
        today = angel_instruments.today_ist()
        near = (today + timedelta(days=10)).strftime("%d%b%Y").upper()
        far = (today + timedelta(days=40)).strftime("%d%b%Y").upper()
        self.rows = _scrip_rows(near, far)
        angel_instruments._STATE.update(rows=self.rows, loaded_at=9e18, source="test")
        self.addCleanup(angel_instruments.reset_cache)

    def test_nifty_future_resolves_to_nearest_expiry(self) -> None:
        contract = angel_instruments.resolve_contract("NIFTY FUT")
        self.assertIsNotNone(contract)
        self.assertEqual(contract["instrument_type"], "FUTIDX")
        self.assertEqual(contract["exchange"], "NSE")
        self.assertEqual(contract["segment"], "NSE_FNO")
        self.assertEqual(contract["angel_exchange"], "NFO")
        self.assertEqual(contract["angel_token"], "58001")
        self.assertEqual(contract["underlying"], "NIFTY")
        self.assertTrue(contract["lot_size"])
        self.assertTrue(contract["tick_size"])
        self.assertTrue(contract["security_id"])
        self.assertTrue(contract["instrument_key"])

    def test_banknifty_future_resolves(self) -> None:
        contract = angel_instruments.resolve_contract("BANKNIFTY FUT")
        self.assertEqual(contract["underlying"], "BANKNIFTY")
        self.assertEqual(contract["instrument_type"], "FUTIDX")

    def test_mcx_gold_prefers_full_size_contract(self) -> None:
        contract = angel_instruments.resolve_contract("MCX GOLD")
        self.assertEqual(contract["exchange"], "MCX")
        self.assertEqual(contract["segment"], "MCX_COMM")
        self.assertEqual(contract["instrument_type"], "FUTCOM")
        self.assertNotIn("GOLDM", contract["trading_symbol"])

    def test_mcx_silver_resolves(self) -> None:
        contract = angel_instruments.resolve_contract("MCX SILVER")
        self.assertEqual(contract["instrument_type"], "FUTCOM")
        self.assertEqual(contract["underlying"], "SILVER")

    def test_expired_contracts_are_never_returned(self) -> None:
        expired = (angel_instruments.today_ist() - timedelta(days=5)).strftime("%d%b%Y").upper()
        angel_instruments._STATE.update(rows=_scrip_rows(expired, expired), loaded_at=9e18)
        self.assertIsNone(angel_instruments.resolve_contract("NIFTY FUT"))

    def test_equity_resolves_to_eq_series(self) -> None:
        contract = angel_instruments.resolve_contract("RELIANCE")
        self.assertEqual(contract["instrument_type"], "EQUITY")
        self.assertEqual(contract["trading_symbol"], "RELIANCE-EQ")

    def test_unknown_symbol_returns_none(self) -> None:
        self.assertIsNone(angel_instruments.resolve_contract("NOTREAL FUT"))

    def test_expiry_parsing_handles_angel_formats(self) -> None:
        self.assertEqual(angel_instruments.parse_expiry("28AUG2026"), date(2026, 8, 28))
        self.assertEqual(angel_instruments.parse_expiry("2026-08-28"), date(2026, 8, 28))
        self.assertIsNone(angel_instruments.parse_expiry(""))


class AngelProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        today = angel_instruments.today_ist()
        near = (today + timedelta(days=10)).strftime("%d%b%Y").upper()
        far = (today + timedelta(days=40)).strftime("%d%b%Y").upper()
        angel_instruments._STATE.update(rows=_scrip_rows(near, far), loaded_at=9e18, source="test")
        self.addCleanup(angel_instruments.reset_cache)

        self.provider = AngelReadOnlyProvider()
        self.provider.api_key = "key"
        self.provider.client_id = "CLIENT"
        self.provider.pin = "1234"
        self.provider.totp_secret = SECRET
        self.calls: list[tuple[str, dict]] = []

    def _install(self, handler) -> None:
        def request(path, payload, authenticated=True):
            self.calls.append((path, payload))
            return handler(path, payload)

        self.provider._request = request  # type: ignore[assignment]

    def _candle_rows(self, count: int = 30) -> list[list]:
        base = datetime.now(angel_provider.IST).replace(second=0, microsecond=0) - timedelta(minutes=15 * count)
        rows = []
        for index in range(count):
            stamp = base + timedelta(minutes=15 * index)
            price = 100.0 + index
            rows.append([stamp.isoformat(), price, price + 2, price - 1, price + 1, 1000 + index])
        return rows

    def _quote(self, price: float = 130.0) -> dict:
        return {
            "ltp": price, "open": price * 0.98, "high": price * 1.05, "low": price * 0.95,
            "close": price * 0.97, "tradeVolume": 12345, "opnInterest": 5000,
            "netChangeopnInterest": 25,
            "exchFeedTime": datetime.now(timezone.utc).isoformat(),
        }

    def test_configured_requires_totp_automation_or_static_code(self) -> None:
        self.assertTrue(self.provider.configured)
        self.provider.totp_secret = ""
        self.provider.static_totp = ""
        self.assertFalse(self.provider.configured)
        self.assertFalse(self.provider.available)

    def test_login_generates_totp_automatically(self) -> None:
        def handler(path, payload):
            if "loginByPassword" in path:
                self.assertEqual(len(payload["totp"]), 6)
                self.assertTrue(payload["totp"].isdigit())
                return {"status": True, "data": {"jwtToken": "JWT1", "refreshToken": "R1", "feedToken": "F1"}}
            raise AssertionError(path)

        self._install(handler)
        self.provider.connect()
        self.assertEqual(self.provider.jwt_token, "JWT1")
        self.assertTrue(self.provider.session_valid)
        self.assertEqual(self.provider.login_count, 1)

    def test_session_refresh_uses_refresh_token(self) -> None:
        self.provider.jwt_token = "OLD"
        self.provider.refresh_token = "R1"

        def handler(path, payload):
            if "generateTokens" in path:
                return {"status": True, "data": {"jwtToken": "JWT2", "refreshToken": "R2"}}
            raise AssertionError(path)

        self._install(handler)
        self.assertTrue(self.provider.refresh_session())
        self.assertEqual(self.provider.jwt_token, "JWT2")
        self.assertEqual(self.provider.refresh_token, "R2")

    def test_expired_session_triggers_relogin_and_succeeds(self) -> None:
        self.provider.jwt_token = "STALE"
        self.provider.session_started_at = 1.0
        state = {"logins": 0}

        def handler(path, payload):
            if "loginByPassword" in path:
                state["logins"] += 1
                return {"status": True, "data": {"jwtToken": "JWT3"}}
            if "quote/" in path:
                return {"status": True, "data": {"fetched": [self._quote()]}}
            if "getCandleData" in path:
                return {"status": True, "data": self._candle_rows()}
            raise AssertionError(path)

        self._install(handler)
        data = self.provider.get_market_data("NIFTY FUT")
        self.assertGreaterEqual(state["logins"], 1)
        self.assertEqual(data["provider"], "angelone_primary")

    def test_market_data_is_verified_live_and_contract_correct(self) -> None:
        def handler(path, payload):
            if "loginByPassword" in path:
                return {"status": True, "data": {"jwtToken": "JWT"}}
            if "quote/" in path:
                return {"status": True, "data": {"fetched": [self._quote()]}}
            if "getCandleData" in path:
                return {"status": True, "data": self._candle_rows()}
            raise AssertionError(path)

        self._install(handler)
        data = self.provider.get_market_data("NIFTY FUT", interval="15m")
        self.assertTrue(data["is_live"])
        self.assertFalse(data["is_delayed"])
        self.assertFalse(data["is_stale"])
        self.assertTrue(data["verified"])
        self.assertTrue(data["read_only"])
        self.assertEqual(data["instrument_type"], "FUTIDX")
        self.assertEqual(data["exchange"], "NSE")
        self.assertEqual(data["open_interest"], 5000)
        self.assertTrue(data["candles"])
        self.assertTrue(data["data_quality"]["valid"], data["data_quality"]["errors"])

    def test_mcx_market_data_passes_contract_validation(self) -> None:
        def handler(path, payload):
            if "loginByPassword" in path:
                return {"status": True, "data": {"jwtToken": "JWT"}}
            if "quote/" in path:
                return {"status": True, "data": {"fetched": [self._quote(105000.0)]}}
            if "getCandleData" in path:
                return {"status": True, "data": self._candle_rows()}
            raise AssertionError(path)

        self._install(handler)
        data = self.provider.get_market_data("MCX GOLD")
        self.assertEqual(data["exchange"], "MCX")
        self.assertEqual(data["segment"], "MCX_COMM")
        self.assertTrue(data["data_quality"]["valid"], data["data_quality"]["errors"])

    def test_all_signal_timeframes_are_supported(self) -> None:
        def handler(path, payload):
            if "loginByPassword" in path:
                return {"status": True, "data": {"jwtToken": "JWT"}}
            if "getCandleData" in path:
                self.assertIn(payload["interval"], {"FIVE_MINUTE", "FIFTEEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR"})
                return {"status": True, "data": self._candle_rows()}
            raise AssertionError(path)

        self._install(handler)
        frames = self.provider.get_multi_timeframe("NIFTY FUT")
        self.assertEqual(set(frames), {"5m", "15m", "30m", "60m"})
        for candles in frames.values():
            self.assertTrue(candles)

    def test_unsupported_interval_is_rejected(self) -> None:
        with self.assertRaises(AngelProviderError):
            self.provider.get_candles("NIFTY FUT", interval="7m")

    def test_stale_quote_is_rejected(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

        def handler(path, payload):
            if "loginByPassword" in path:
                return {"status": True, "data": {"jwtToken": "JWT"}}
            if "quote/" in path:
                quote = self._quote()
                quote["exchFeedTime"] = old
                return {"status": True, "data": {"fetched": [quote]}}
            if "getCandleData" in path:
                return {"status": True, "data": self._candle_rows()}
            raise AssertionError(path)

        self._install(handler)
        with self.assertRaises(AngelProviderError):
            self.provider.get_market_data("NIFTY FUT")

    def test_unknown_instrument_fails_safely(self) -> None:
        self._install(lambda path, payload: {"status": True, "data": {"jwtToken": "JWT"}})
        with self.assertRaises(AngelProviderError):
            self.provider.get_market_data("FAKESYMBOL FUT")

    def test_get_many_skips_failures_without_raising(self) -> None:
        def handler(path, payload):
            if "loginByPassword" in path:
                return {"status": True, "data": {"jwtToken": "JWT"}}
            if "quote/" in path:
                return {"status": True, "data": {"fetched": [self._quote()]}}
            if "getCandleData" in path:
                return {"status": True, "data": self._candle_rows()}
            raise AssertionError(path)

        self._install(handler)
        result = self.provider.get_many(["NIFTY FUT", "FAKESYMBOL FUT"])
        self.assertIn("NIFTY FUT", result)
        self.assertNotIn("FAKESYMBOL FUT", result)

    def test_retry_recovers_from_transient_failure(self) -> None:
        state = {"quote_calls": 0}

        def handler(path, payload):
            if "loginByPassword" in path:
                return {"status": True, "data": {"jwtToken": "JWT"}}
            if "quote/" in path:
                state["quote_calls"] += 1
                if state["quote_calls"] == 1:
                    raise AngelProviderError("angel_request_failed: timeout")
                return {"status": True, "data": {"fetched": [self._quote()]}}
            if "getCandleData" in path:
                return {"status": True, "data": self._candle_rows()}
            raise AssertionError(path)

        self._install(handler)
        data = self.provider.get_market_data("NIFTY FUT")
        self.assertGreaterEqual(state["quote_calls"], 2)
        self.assertTrue(data["is_live"])

    def test_health_check_reports_totp_automation(self) -> None:
        health = self.provider.health_check()
        self.assertTrue(health["configured"])
        self.assertTrue(health["totp_automation"])
        self.assertTrue(health["read_only"])
        self.assertEqual(health["provider"], "angelone_primary")
        self.assertEqual(list(health["supported_intervals"]), ["5m", "15m", "30m", "60m"])


class ReadOnlyGuaranteeTests(unittest.TestCase):
    FORBIDDEN = {
        "place_order", "placeorder", "modify_order", "modifyorder",
        "cancel_order", "cancelorder", "buy", "sell", "square_off",
    }

    def test_angel_modules_expose_no_order_methods(self) -> None:
        for module in ("angel_provider.py", "angel_instruments.py", "angel_totp.py"):
            tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertNotIn(node.name.lower(), self.FORBIDDEN, f"{module}:{node.name}")

    def test_angel_provider_never_calls_order_endpoints(self) -> None:
        source = (ROOT / "angel_provider.py").read_text(encoding="utf-8")
        for path in ("placeOrder", "modifyOrder", "cancelOrder"):
            self.assertNotIn(path, source)

    def test_provider_is_marked_read_only(self) -> None:
        self.assertTrue(AngelReadOnlyProvider().health_check()["read_only"])


if __name__ == "__main__":
    unittest.main()
