from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import admin
import commands


class AdminNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Delivery needs a configured recipient; arrange one for the test.
        self._recipients = admin.notification_recipients
        admin.notification_recipients = lambda: [4242]
        self.addCleanup(setattr, admin, "notification_recipients", self._recipients)

    def _application(self, sent: list[str]):
        class Bot:
            async def send_message(self, chat_id: int, text: str) -> None:
                sent.append(text)

        class Application:
            bot = Bot()

        return Application()

    async def test_existing_header_is_not_double_prefixed(self) -> None:
        sent: list[str] = []
        delivered = await admin.notify_admins(
            self._application(sent), "🔐 SHIVAY AI ADMIN\n\n🟢 SHIVAY AI ACTIVE"
        )
        self.assertEqual(delivered, 1)
        self.assertEqual(sent[0], "🔐 SHIVAY AI ADMIN\n\n🟢 SHIVAY AI ACTIVE")

    async def test_missing_header_is_prefixed_once(self) -> None:
        sent: list[str] = []
        await admin.notify_admins(self._application(sent), "🟢 SHIVAY AI ACTIVE")
        self.assertEqual(sent[0], "🔐 SHIVAY AI ADMIN\n\n🟢 SHIVAY AI ACTIVE")

    async def test_bot_tokens_are_redacted_before_delivery(self) -> None:
        sent: list[str] = []
        # Built at runtime so no token-shaped literal is ever committed.
        fake_secret = "A" * 35
        fake_token = "123456789" + ":" + fake_secret
        await admin.notify_admins(self._application(sent), f"leak {fake_token}")
        self.assertIn("[REDACTED]", sent[0])
        self.assertNotIn(fake_secret, sent[0])

    async def test_unconfigured_recipient_queues_message_instead_of_losing_it(self) -> None:
        admin.notification_recipients = lambda: []
        sent: list[str] = []
        delivered = await admin.notify_admins(self._application(sent), "queued alert")
        self.assertEqual(delivered, 0)
        self.assertEqual(sent, [])
        admin.notification_recipients = lambda: [4242]
        replayed = await admin.notify_admins(self._application(sent))
        self.assertGreaterEqual(replayed, 1)
        self.assertTrue(any("queued alert" in item for item in sent))


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
