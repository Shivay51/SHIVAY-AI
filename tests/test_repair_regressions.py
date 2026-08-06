from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import admin
import commands


class AdminNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_header_is_not_double_prefixed(self) -> None:
        sent: list[str] = []

        class Bot:
            async def send_message(self, chat_id: int, text: str) -> None:
                sent.append(text)

        class Application:
            bot = Bot()

        await admin.notify_admins(Application(), "🔐 SHIVAY AI ADMIN\n\n🟢 SHIVAY AI ACTIVE")
        self.assertEqual(sent[0], "🔐 SHIVAY AI ADMIN\n\n🟢 SHIVAY AI ACTIVE")


class CommandsTests(unittest.TestCase):
    def test_datastatus_text_is_meaningful(self) -> None:
        text = commands.build_data_status_text({"provider_status": "READY", "tradingview": {"state": "CONNECTED"}})
        lowered = text.lower()
        self.assertIn("data status", lowered)
        self.assertIn("ready", lowered)
        self.assertIn("connected", lowered)


class PineScriptTests(unittest.TestCase):
    def test_bridge_script_has_single_structural_header_and_alert_payload(self) -> None:
        text = (commands.Path(__file__).resolve().parents[1] / "SHIVAY_AI_TV_BRIDGE.pine").read_text(encoding="utf-8")
        self.assertEqual(text.count("//@version=6"), 1)
        self.assertEqual(text.count("indicator("), 1)
        self.assertIn('alert(message, alert.freq_once_per_bar_close)', text)
        self.assertIn('event_id', text)


if __name__ == "__main__":
    unittest.main()
