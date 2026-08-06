from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from groww_provider import GrowwProvider
from provider_manager import ProviderManager


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.paths: list[str] = []

    def get(self, url, **kwargs):
        self.paths.append(url)
        now = datetime.now(timezone.utc)
        if url.endswith("/user/detail"):
            return _Response({"status": "SUCCESS", "payload": {"active_segments": ["FNO", "COMMODITY"]}})
        if url.endswith("/live-data/quote"):
            return _Response({"status": "SUCCESS", "payload": {
                "last_price": 25000, "last_trade_time": int(now.timestamp() * 1000),
                "ohlc": {"open": 24900, "high": 25100, "low": 24850, "close": 24880},
                "volume": 1200, "open_interest": 500, "previous_open_interest": 450,
                "oi_day_change": 50, "bid_price": 24999, "offer_price": 25001,
            }})
        if url.endswith("/historical/candles"):
            start = now - timedelta(hours=5)
            candles = []
            for index in range(20):
                close = 24900 + index
                candles.append([
                    int((start + timedelta(minutes=15 * index)).timestamp()),
                    close - 2, close + 10, close - 10, close, 100 + index, 500 + index,
                ])
            return _Response({"status": "SUCCESS", "payload": {"candles": candles}})
        raise AssertionError(f"Unexpected read-only endpoint: {url}")

    def close(self):
        return None


class GrowwProviderTests(unittest.TestCase):
    def _provider(self):
        session = _Session()
        with patch.dict(os.environ, {"ENABLE_GROWW": "true", "GROWW_ACCESS_TOKEN": "test-token"}, clear=False):
            provider = GrowwProvider(session)
        expiry = (date.today() + timedelta(days=20)).isoformat()
        provider._master = [{
            "exchange": "NSE", "exchange_token": "58072", "trading_symbol": "NIFTY26AUGFUT",
            "groww_symbol": "NSE-NIFTY-25Aug26-FUT", "instrument_type": "FUT", "segment": "FNO",
            "underlying_symbol": "NIFTY", "expiry_date": expiry, "lot_size": "65", "tick_size": "0.1",
        }, {
            "exchange": "MCX", "exchange_token": "483079", "trading_symbol": "GOLD05OCT26FUT",
            "groww_symbol": "MCX-GOLD-05Oct26-FUT", "instrument_type": "FUT", "segment": "COMMODITY",
            "underlying_symbol": "GOLD", "expiry_date": expiry, "lot_size": "100", "tick_size": "100",
        }]
        provider.available = True
        return provider, session

    def test_read_only_nse_quote_and_candles(self):
        provider, session = self._provider()
        self.assertTrue(provider.authenticate())
        contract = provider.resolve_current_contract("NIFTY FUT")
        self.assertEqual(contract["trading_symbol"], "NIFTY26AUGFUT")
        quote = provider.get_quote("NIFTY FUT")
        self.assertEqual(quote["last_price"], 25000)
        self.assertEqual(quote["bid"], 24999)
        self.assertEqual(len(provider.get_historical_candles("NIFTY FUT", "15m")), 20)
        self.assertFalse(any(word in path.lower() for path in session.paths for word in ("order", "modify", "cancel")))

    def test_mcx_contract_is_exact_but_unsupported_history_fails_closed(self):
        provider, _ = self._provider()
        contract = provider.resolve_current_contract("GOLD")
        self.assertEqual(contract["exchange"], "MCX")
        self.assertEqual(contract["instrument_type"], "FUTCOM")
        self.assertEqual(provider.get_historical_candles("GOLD", "15m"), [])

    def test_groww_is_supported_and_unconfigured_adapter_is_unavailable(self):
        self.assertIn("groww_primary", ProviderManager.DEFAULT_PRIORITY)
        with patch.dict(os.environ, {"ENABLE_GROWW": "false", "GROWW_ACCESS_TOKEN": ""}, clear=False):
            provider = GrowwProvider(_Session())
        self.assertFalse(provider.available)
        self.assertFalse(provider.authenticate())


if __name__ == "__main__":
    unittest.main()
