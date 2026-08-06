from __future__ import annotations

import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path

from market_data_provider import normalize_market_snapshot

ROOT = Path(__file__).resolve().parents[1]


class MasterCompletionTests(unittest.TestCase):
    def test_normalized_market_snapshot_contract(self):
        stamp = datetime.now(timezone.utc)
        value = normalize_market_snapshot({
            "symbol": "NIFTY FUT", "exchange": "NSE", "trading_symbol": "NIFTY26AUGFUT",
            "price": 24738, "open_value": 24700, "day_high": 24780, "day_low": 24680,
            "previous_close": 24690, "latest_volume": 1000, "open_interest": 500,
            "bid": 24737.5, "ask": 24738.5, "timestamp": stamp, "received_at": stamp,
            "is_live": True, "is_delayed": False, "is_stale": False, "provider": "internal_test",
        })
        required = {"symbol", "exchange", "contract", "ltp", "open", "high", "low", "close",
                    "volume", "oi", "bid", "ask", "exchange_timestamp", "retrieval_timestamp",
                    "status", "provider_internal"}
        self.assertEqual(set(value), required)
        self.assertEqual(value["status"], "LIVE")
        self.assertEqual(value["contract"], "NIFTY26AUGFUT")

    def test_startup_card_is_single_clean_signals_only_message(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_startup")
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        notify = [node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr == "notify_admins"]
        self.assertEqual(len(notify), 1)
        segment = source[source.index("async def on_startup"):source.index("async def on_shutdown")]
        for text in ("BOT ACTIVE", "DATA ENGINE: ACTIVE", "SCANNER: ACTIVE", "SCHEDULER: ACTIVE", "MODE: SIGNALS ONLY"):
            self.assertIn(text, segment)
        for private in ("Provider:", "Source:", "API:"):
            self.assertNotIn(private, segment)

    def test_windows_helpers_are_narrow_and_complete(self):
        for name in ("START_SHIVAY.bat", "RESTART_SHIVAY.bat", "STATUS_SHIVAY.bat", "SHIVAY_CONTROL.ps1"):
            self.assertTrue((ROOT / name).is_file(), name)
        control = (ROOT / "SHIVAY_CONTROL.ps1").read_text(encoding="utf-8")
        self.assertIn(".shivay_ai.lock", control)
        self.assertIn("Stop-Process -Id", control)
        self.assertNotIn("Stop-Process -Name python", control)
        self.assertNotIn("Get-Process python | Stop-Process", control)

    def test_secret_files_are_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").lower()
        for entry in (".env", "*.key", "tokens/", "credentials/", "secrets/"):
            self.assertIn(entry, ignored)


if __name__ == "__main__":
    unittest.main()
