from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import config
import commands
import market_data_provider as facade
from data_quality import assess_market_data, validate_instrument_contract
from market_hub_client import MarketHubClient
from market_hub_probe import run_probe
from truedata_provider import TrueDataProvider


class ProviderFoundationTests(unittest.TestCase):
    def test_permanent_safety_flags(self):
        self.assertFalse(config.ENABLE_LIVE_ORDER_PLACEMENT)
        self.assertTrue(config.SIGNALS_ONLY)
        self.assertTrue(config.PAPER_MONITORING)

    def test_market_hub_probe_never_invents_market_capabilities(self):
        result = run_probe(MarketHubClient(), perform_login=False)
        by_name = {row["capability"]: row for row in result["results"]}
        for name in ("instrument_master", "symbol_search", "quote", "ohlc", "historical_candles", "market_websocket", "nse_futures_market_data", "mcx_gold_market_data", "mcx_silver_market_data"):
            self.assertFalse(by_name[name]["supported"])
            self.assertFalse(by_name[name]["verified"])

    def test_facade_exposes_complete_read_only_contract(self):
        for name in ("get_quote", "get_ohlc", "get_historical_candles", "search_instrument", "get_instrument_master", "health_check", "freshness_status"):
            self.assertTrue(callable(getattr(facade, name)))

    def test_no_verified_data_is_wait(self):
        with patch.object(facade._FACADE, "get_quote", return_value=None):
            self.assertEqual(facade.freshness_status("MCX GOLD")["decision"], "WAIT")
            self.assertEqual(facade.freshness_status("MCX GOLD")["status"], "NO VERIFIED DATA")

    def test_comex_and_cash_cannot_pass_as_indian_futures(self):
        comex = {"symbol": "MCX GOLD", "exchange": "COMEX", "segment": "COMMODITY", "instrument_type": "GLOBAL_FUTURE", "provider": "yahoo_emergency", "trading_symbol": "GC=F"}
        cash = {"symbol": "NIFTY FUT", "exchange": "NSE", "segment": "INDEX", "instrument_type": "INDEX_PROXY"}
        self.assertIn("comex_is_not_mcx", validate_instrument_contract(comex, "MCX GOLD"))
        self.assertIn("cash_futures_mismatch", validate_instrument_contract(cash, "NIFTY FUT"))

    def test_stale_or_zero_data_is_rejected(self):
        bad = {"price": 0, "open_value": 0, "day_high": 0, "day_low": 0, "latest_volume": 0, "timestamp": "2000-01-01T00:00:00Z"}
        quality = assess_market_data(bad)
        self.assertFalse(quality["valid"])
        self.assertIn("stale_data", quality["errors"])

    def test_truedata_normalized_interface_with_provider_responses(self):
        class Client:
            configured = True
            def connect(self): return True
            def health(self): return {"configured": True, "authenticated": True}
            def quote(self, identifier):
                return {"ltp": 25000, "timestamp": datetime.now(timezone.utc).isoformat(), "open": 24900, "high": 25100, "low": 24850, "previous_close": 24880, "volume": 1200, "oi": 500, "bid": 24999, "ask": 25001}
            def history(self, identifier, interval, bars):
                now = datetime.now(timezone.utc) - timedelta(minutes=interval * 30)
                return [{"timestamp": now + timedelta(minutes=interval * index), "open": 24900 + index, "high": 24920 + index, "low": 24880 + index, "close": 24910 + index, "volume": 100 + index, "oi": 500} for index in range(20)]
            def close(self): pass

        provider = TrueDataProvider(Client())
        provider._catalog = [{"symbol": "NIFTY FUT", "trading_symbol": "NIFTY2099FUT", "instrument_id": "verified-id", "security_id": "verified-id", "exchange": "NSE", "segment": "NSE_FNO", "instrument_type": "FUTIDX", "underlying": "NIFTY", "expiry": "2099-12-31", "lot_size": 65, "tick_size": 0.05}]
        provider.available = True
        self.assertTrue(provider.authenticate())
        contract = provider.resolve_current_contract("NIFTY FUT")
        self.assertEqual(contract["instrument_id"], "verified-id")
        quote = provider.get_quote("NIFTY FUT")
        required = {"symbol", "exchange", "segment", "instrument_id", "trading_symbol", "expiry", "timestamp", "last_price", "open", "high", "low", "previous_close", "volume", "open_interest", "bid", "ask", "source", "freshness"}
        self.assertFalse(required - set(quote))
        for interval in (5, 15, 30, 60):
            self.assertEqual(len(provider.get_historical_candles("NIFTY FUT", interval)), 20)

    def test_data_status_lists_each_required_instrument(self):
        text = commands.build_data_status_text({"provider_status": "NO VERIFIED DATA", "instruments": {"NIFTY FUT": {"status": "NO_DATA"}}})
        for label in ("NIFTY FUT", "BANKNIFTY FUT", "GOLD", "SILVER", "SIGNALS ONLY"):
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()
