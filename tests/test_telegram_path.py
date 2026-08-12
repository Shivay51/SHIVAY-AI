"""Step 7 — scanner → Telegram delivery path."""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import config
import telegram_service

IST_NOW = datetime(2026, 8, 12, 10, 15, tzinfo=timezone.utc)


class _Bot:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.sent: list[tuple[int, str]] = []
        self.attempts = 0

    async def send_message(self, chat_id, text):  # noqa: ANN001
        self.attempts += 1
        if self.attempts <= self.failures:
            raise RuntimeError("telegram temporarily unavailable")
        self.sent.append((int(chat_id), str(text)))
        return True


class _App:
    def __init__(self, bot: _Bot) -> None:
        self.bot = bot


def _trade(side: str = "BUY") -> dict:
    return {
        "symbol": "NIFTY FUT",
        "trading_symbol": "NIFTY28AUG26FUT",
        "market_category": "NSE F&O",
        "segment": "NSE_FNO",
        "side": side,
        "decision": "✅ BUY" if side == "BUY" else "🔻 SELL",
        "price": 24500.0,
        "delivery_price": 24505.0,
        "entry": 24500.0,
        "entry_zone": [24490.0, 24510.0],
        "sl": 24400.0,
        "target1": 24600.0,
        "target2": 24700.0,
        "target3": 24800.0,
        "score": 92,
        "risk_reward": 2.4,
        "atr": 60.0,
        "signal_candle_high": 24520.0,
        "signal_candle_low": 24460.0,
        "signal_candle": {"timestamp": "2026-08-12T09:45:00+05:30"},
        "confirmation_candle": {"timestamp": "2026-08-12T10:00:00+05:30"},
        "signal_class": {"classification": "SAFE", "premium": True},
        "signal_classification": "SAFE",
        "holding_type": "INTRADAY",
        "expiry": "2026-08-27",
        "valid_until": "2026-08-12T10:33:00+05:30",
        "signal_time": "2026-08-12T10:01:00+05:30",
        "market_data": {"data_age_seconds": 12, "data_status": "FRESH",
                        "timestamp": "2026-08-12T10:01:00+05:30"},
        "volume": 145000,
        "open_interest": 980000,
        "reasons": ["15m Chandelier flip confirmed", "Next-candle confirmation passed", "Volume expansion"],
    }


class MessageContentTests(unittest.TestCase):
    def test_message_contains_every_required_field(self) -> None:
        text = telegram_service._signal_text(_trade("BUY"), "BUY")
        for fragment in ("SHIVAY AI PRO", "MARKET:", "NSE F&O", "NIFTY28AUG26FUT", "BUY",
                         "CURRENT PRICE:", "ENTRY ZONE:", "STOP LOSS:", "TARGET 1:", "TARGET 2:",
                         "TARGET 3:", "SHIVAY SCORE:", "DECISION:", "SAFE", "TRADE TYPE:",
                         "INTRADAY", "SIGNAL TIME:", "DATA FRESHNESS:", "CHANDELIER SIGNAL:",
                         "CONFIRMATION:", "TOP REASONS:", "VALID UNTIL:", "CONTRACT EXPIRY:"):
            self.assertIn(fragment, text, fragment)

    def test_premium_class_is_shown_and_risky_is_honest(self) -> None:
        safe = telegram_service._signal_text(_trade("BUY"), "BUY")
        self.assertIn("PREMIUM 90+", safe)
        risky = _trade("SELL")
        risky["signal_class"] = {"classification": "RISKY", "premium": False}
        risky["signal_classification"] = "RISKY"
        text = telegram_service._signal_text(risky, "SELL")
        self.assertIn("RISKY", text)
        self.assertNotIn("PREMIUM", text)

    def test_message_never_contains_urls_credentials_or_traces(self) -> None:
        dirty = _trade("BUY")
        dirty["reasons"] = [
            "check https://example.com/secret",
            "bot token 123456789:AAExampleTelegramBotTokenValue1234567",
            "Traceback (most recent call last): File \"x.py\", line 3, in run",
        ]
        text = telegram_service.sanitize(telegram_service._signal_text(dirty, "BUY"))
        self.assertNotIn("http", text)
        self.assertNotIn("AAExampleTelegramBotTokenValue", text)
        self.assertNotIn("Traceback", text)
        self.assertIn("[link removed]", text)


class DeliveryGateTests(unittest.TestCase):
    def test_only_admins_receive_signals(self) -> None:
        users = [{"id": 1, "plan": "ADMIN"}, {"id": 2, "plan": "ALL"}, {"id": 3, "plan": "NSE_FO"}]
        with patch("telegram_service.recipients", return_value=users), \
             patch("telegram_service._admin_ids", return_value={1}):
            approved = telegram_service.authorized_recipients("NSE_FO")
        self.assertEqual([user["id"] for user in approved], [1])

    def test_unauthorized_recipient_is_rejected_and_logged(self) -> None:
        with patch("telegram_service.recipients", return_value=[{"id": 99, "plan": "ALL"}]), \
             patch("telegram_service._admin_ids", return_value={1}), \
             patch("telegram_service.rejection_log.record") as recorded:
            approved = telegram_service.authorized_recipients("ALL")
        self.assertEqual(approved, [])
        recorded.assert_called_once()
        self.assertEqual(recorded.call_args[0][1], "unauthorized_recipient")

    def test_admin_only_mode_is_enabled_by_default(self) -> None:
        self.assertTrue(config.SIGNAL_ADMIN_ONLY)

    def test_send_retries_with_backoff_then_succeeds(self) -> None:
        bot = _Bot(failures=2)
        with patch("telegram_service.recipients", return_value=[{"id": 1}]), \
             patch("telegram_service._admin_ids", return_value={1}), \
             patch("telegram_service.asyncio.sleep", new=AsyncMock()) as slept:
            sent = asyncio.run(telegram_service._send(_App(bot), ("K", 1), "🔱 SHIVAY AI PRO\n\nSIGNAL"))
        self.assertTrue(sent)
        self.assertEqual(bot.attempts, 3)
        self.assertEqual(slept.await_count, 2)

    def test_send_gives_up_after_attempt_budget(self) -> None:
        bot = _Bot(failures=99)
        with patch("telegram_service.recipients", return_value=[{"id": 1}]), \
             patch("telegram_service._admin_ids", return_value={1}), \
             patch("telegram_service.asyncio.sleep", new=AsyncMock()):
            sent = asyncio.run(telegram_service._send(_App(bot), ("K", 2), "SIGNAL"))
        self.assertFalse(sent)
        self.assertEqual(bot.attempts, int(config.TELEGRAM_SEND_ATTEMPTS))

    def test_acknowledged_message_is_never_resent(self) -> None:
        bot = _Bot()
        key = ("SIGNAL", "BUY", "NIFTY FUT", 24500.0)
        with patch("telegram_service.recipients", return_value=[{"id": 1}]), \
             patch("telegram_service._admin_ids", return_value={1}):
            first = asyncio.run(telegram_service._send(_App(bot), key, "SIGNAL ONE"))
            second = asyncio.run(telegram_service._send(_App(bot), key, "SIGNAL ONE"))
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(bot.sent), 1)

    def test_sanitisation_applies_to_the_transmitted_text(self) -> None:
        bot = _Bot()
        with patch("telegram_service.recipients", return_value=[{"id": 1}]), \
             patch("telegram_service._admin_ids", return_value={1}):
            asyncio.run(telegram_service._send(_App(bot), ("K", 3), "open https://evil.example/x now"))
        self.assertIn("[link removed]", bot.sent[0][1])
        self.assertNotIn("https", bot.sent[0][1])


class SchedulerPathTests(unittest.TestCase):
    def test_scheduler_runs_every_five_minutes_without_overlap(self) -> None:
        source = Path("scheduler.py").read_text(encoding="utf-8")
        self.assertIn("_next_scan_boundary", source)
        self.assertIn("_SCAN_LOCK", source)
        self.assertIn("Scan skipped because the previous scan is still running", source)
        self.assertIn("_safe_call", source)

    def test_scan_interval_config(self) -> None:
        self.assertEqual(int(config.SCAN_INTERVAL), 300)

    def test_scan_job_is_idempotent_on_retry(self) -> None:
        import scheduler
        import signal_memory

        signal_memory.clear_signals()
        trade = _trade("BUY")
        calls: list[dict] = []

        async def sender(app, payload):  # noqa: ANN001
            calls.append(payload)
            return True

        with patch("scheduler.scan_market", return_value=[trade]), \
             patch("scheduler._rank", side_effect=lambda signals: [{**trade, "_side": "BUY"}]), \
             patch("scheduler.send_buy_signal", new=sender), \
             patch("scheduler.add_trade"), patch("scheduler.record_signal"):
            first = asyncio.run(scheduler._scan_job(_App(_Bot())))
            second = asyncio.run(scheduler._scan_job(_App(_Bot())))
        self.assertEqual(first, 1)
        self.assertEqual(second, 0, "the same signal candle must never be delivered twice")
        self.assertEqual(len(calls), 1)
        signal_memory.clear_signals()


if __name__ == "__main__":
    unittest.main()
