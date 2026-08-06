from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from provider_manager import ProviderManager
from upstox_provider import UpstoxProvider


class _Response:
    status_code = 200

    def __init__(self, value):
        self._value = value

    def raise_for_status(self):
        return None

    def json(self):
        return self._value


class _Session:
    def __init__(self, instrument_key: str):
        self.instrument_key = instrument_key
        self.paths: list[str] = []

    def get(self, url, **kwargs):
        self.paths.append(url)
        now = datetime.now(timezone.utc)
        if url.endswith("/v2/user/profile"):
            return _Response({"status": "success", "data": {"user_id": "masked"}})
        if url.endswith("/v2/market-quote/quotes"):
            return _Response({"status": "success", "data": {self.instrument_key: {
                "last_price": 25000, "timestamp": now.isoformat(), "volume": 1200, "oi": 500,
                "ohlc": {"open": 24900, "high": 25100, "low": 24850, "close": 24880},
                "depth": {"buy": [{"price": 24999}], "sell": [{"price": 25001}]},
            }}})
        if "/v3/historical-candle/" in url:
            candles = []
            start = now - timedelta(hours=3)
            for index in range(20):
                close = 24900 + index
                candles.append([(start + timedelta(minutes=5 * index)).isoformat(), close - 2, close + 10, close - 10, close, 100 + index, 500])
            return _Response({"status": "success", "data": {"candles": candles}})
        raise AssertionError(f"Unexpected read-only endpoint: {url}")

    def close(self):
        return None


class UpstoxProviderTests(unittest.TestCase):
    def _provider(self):
        key = "NSE_FO|99999"
        session = _Session(key)
        with patch.dict(os.environ, {"ENABLE_UPSTOX": "true", "UPSTOX_ACCESS_TOKEN": "test-token"}, clear=False):
            provider = UpstoxProvider(session)
        expiry = int((datetime.now(timezone.utc) + timedelta(days=20)).timestamp() * 1000)
        provider._master = [{
            "segment": "NSE_FO", "instrument_type": "FUT", "underlying_symbol": "NIFTY",
            "instrument_key": key, "exchange_token": "99999", "trading_symbol": "NIFTY FUT",
            "expiry": expiry, "lot_size": 65, "tick_size": 0.05,
        }]
        provider.available = True
        return provider, session

    def test_read_only_contract_resolution_quote_and_candles(self):
        provider, session = self._provider()
        self.assertTrue(provider.authenticate())
        contract = provider.resolve_current_contract("NIFTY FUT")
        self.assertEqual(contract["instrument_key"], "NSE_FO|99999")
        self.assertEqual(provider.get_quote("NIFTY FUT")["last_price"], 25000)
        for interval in (5, 15, 30, 60):
            self.assertEqual(len(provider.get_historical_candles("NIFTY FUT", interval)), 20)
        self.assertFalse(any(word in path.lower() for path in session.paths for word in ("order", "modify", "cancel")))

    def test_upstox_is_supported(self):
        self.assertIn("upstox_primary", ProviderManager.DEFAULT_PRIORITY)

    def test_unconfigured_provider_fails_closed(self):
        with patch.dict(os.environ, {"ENABLE_UPSTOX": "false", "UPSTOX_ACCESS_TOKEN": ""}, clear=False):
            provider = UpstoxProvider(_Session("unused"))
        self.assertFalse(provider.available)
        self.assertFalse(provider.authenticate())


if __name__ == "__main__":
    unittest.main()
