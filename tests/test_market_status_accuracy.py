from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from live_data_status import classify_market_state
from mcx_temporary_provider import MCXTemporaryProvider


class MarketStatusAccuracyTests(unittest.TestCase):
    def test_comex_open_but_delayed_is_not_closed(self):
        now = datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc)
        value = {"price": 4300, "market_timestamp": now - timedelta(minutes=10)}
        self.assertEqual(classify_market_state("COMEX GOLD", value, now=now)["status"], "DELAYED")

    def test_comex_closed_is_separate_from_delayed(self):
        now = datetime(2026, 8, 8, 16, 0, tzinfo=timezone.utc)
        value = {"price": 4300, "market_timestamp": now - timedelta(minutes=10)}
        self.assertEqual(classify_market_state("COMEX GOLD", value, now=now)["status"], "CLOSED")

    def test_fresh_open_quote_is_live(self):
        now = datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc)
        value = {"price": 4300, "market_timestamp": now - timedelta(seconds=20)}
        self.assertEqual(classify_market_state("COMEX GOLD", value, now=now)["status"], "LIVE")

    def test_zero_price_is_unavailable(self):
        now = datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(classify_market_state("COMEX SILVER", {"price": 0}, now=now)["status"], "UNAVAILABLE")

    def test_missing_exchange_timestamp_is_delayed_while_open(self):
        now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
        value = {"price": 148500, "market_timestamp": None}
        self.assertEqual(classify_market_state("MCX GOLD", value, now=now)["status"], "DELAYED")


class MCXContractSelectionTests(unittest.TestCase):
    def test_selects_nearest_unexpired_contract_and_rejects_wrong_exchange(self):
        provider = MCXTemporaryProvider()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [
            {"s": "MCX:GOLDQ2026", "d": ["GOLDQ2026", "expired", 140000, 1, 1, "streaming", "MCX", 20260801]},
            {"s": "OTHER:GOLDU2026", "d": ["GOLDU2026", "wrong exchange", 145000, 1, 1, "streaming", "OTHER", 20260904]},
            {"s": "MCX:GOLDV2026", "d": ["GOLDV2026", "active", 148500, 10, 20, "streaming", "MCX", 20261005]},
            {"s": "MCX:GOLDZ2026", "d": ["GOLDZ2026", "later", 150000, 5, 10, "streaming", "MCX", 20261204]},
        ]}
        provider._session.post = Mock(return_value=response)
        selected = provider.resolve_active_contract("GOLD")
        self.assertEqual(selected["trading_symbol"], "MCX:GOLDV2026")
        self.assertEqual(selected["expiry"], "2026-10-05")


if __name__ == "__main__":
    unittest.main()
