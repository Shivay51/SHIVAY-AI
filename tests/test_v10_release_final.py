from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

import performance_report
import telegram_service
from tradingview_cache import TradingViewCache


ROOT = Path(__file__).resolve().parents[1]


class FinalReleaseTests(unittest.TestCase):
    def test_startup_has_one_admin_card_and_no_metal_report(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_startup")
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        notify_calls = [node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr == "notify_admins"]
        self.assertEqual(len(notify_calls), 1)
        self.assertNotIn("send_gold", source[source.index("async def on_startup"):source.index("async def on_shutdown")])
        self.assertNotIn("send_silver", source[source.index("async def on_startup"):source.index("async def on_shutdown")])

    def test_command_surface_contains_no_order_commands(self):
        source = (ROOT / "startup.py").read_text(encoding="utf-8")
        for command in ("status", "scan", "market", "prediction", "gold", "silver", "performance", "systemhealth"):
            self.assertIn(f'"{command}"', source)
        for unsafe in ("placeorder", "cancelorder", "modifyorder", "squareoff"):
            self.assertNotIn(f'"{unsafe}"', source.lower())

    def test_wait_format_has_no_private_or_zero_fields(self):
        text = telegram_service.format_wait_status({"status": "NO DATA", "market_regime": "SIDEWAYS"})
        lowered = text.lower()
        self.assertNotIn("provider", lowered)
        self.assertNotIn("webhook", lowered)
        self.assertNotIn("entry: 0", lowered)
        self.assertNotIn("target 1: 0", lowered)

    def test_readiness_includes_partial(self):
        cache = TradingViewCache(persistence_path=None)
        self.assertIn("PARTIAL", cache.status()["states"])

    def test_templates_use_string_timeframes_and_exact_contract(self):
        data = json.loads((ROOT / "TRADINGVIEW_ALERT_MESSAGES.json").read_text(encoding="utf-8"))
        for timeframe, template in (("5", "5_minute"), ("15", "15_minute"), ("30", "30_minute"), ("60", "60_minute")):
            self.assertIn(f'"timeframe":"{timeframe}"', data["templates"][template])
            self.assertIn("<EXACT_CONTRACT>", data["templates"][template])

    def test_performance_metrics_include_release_fields(self):
        metrics = performance_report._calculate_metrics([])
        for key in ("t1_hit_rate", "t2_hit_rate", "t3_hit_rate", "stop_loss_rate", "average_mfe", "average_mae", "time_slot_performance"):
            self.assertIn(key, metrics)


if __name__ == "__main__":
    unittest.main()
