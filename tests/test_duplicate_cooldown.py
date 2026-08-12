"""Step 6 — duplicate suppression, cooldown, persistence, rejection reporting."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import rejection_log
import rejection_report
import signal_memory

NOW = datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc)


class SignalMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        signal_memory.reset_for_tests(Path(self._directory.name) / "signal_memory.json")

    def tearDown(self) -> None:
        signal_memory.reset_for_tests(Path(self._directory.name) / "signal_memory.json")
        self._directory.cleanup()

    def test_first_signal_allowed_then_duplicate_suppressed(self) -> None:
        allowed, reason = signal_memory.can_send("NIFTY FUT", "BUY", now=NOW)
        self.assertTrue(allowed)
        self.assertEqual(reason, "no_previous_signal")
        signal_memory.add_signal("NIFTY FUT", "BUY", now=NOW)
        allowed, reason = signal_memory.can_send("NIFTY FUT", "BUY", now=NOW + timedelta(minutes=1))
        self.assertFalse(allowed)
        self.assertEqual(reason, "cooldown_active")

    def test_cooldown_expires_then_still_blocks_until_expiry(self) -> None:
        signal_memory.add_signal("SBIN FUT", "BUY", now=NOW)
        after_cooldown = NOW + timedelta(minutes=config.SIGNAL_COOLDOWN_MINUTES + 1)
        allowed, reason = signal_memory.can_send("SBIN FUT", "BUY", now=after_cooldown)
        self.assertFalse(allowed)
        self.assertEqual(reason, "duplicate_signal_suppressed")

    def test_signal_expires_and_stops_blocking(self) -> None:
        signal_memory.add_signal("SBIN FUT", "BUY", now=NOW)
        later = NOW + timedelta(minutes=config.SIGNAL_EXPIRY_MINUTES + 1)
        allowed, reason = signal_memory.can_send("SBIN FUT", "BUY", now=later)
        self.assertTrue(allowed)
        self.assertEqual(reason, "previous_signal_expired")

    def test_direction_reversal_allowed_only_after_minimum_hold(self) -> None:
        signal_memory.add_signal("RELIANCE FUT", "BUY", now=NOW)
        early = signal_memory.can_send("RELIANCE FUT", "SELL", now=NOW + timedelta(minutes=2))
        self.assertEqual(early, (False, "minimum_hold_not_elapsed"))
        late = signal_memory.can_send(
            "RELIANCE FUT", "SELL", now=NOW + timedelta(minutes=config.MIN_HOLD_MINUTES + 1))
        self.assertEqual(late, (True, "direction_reversal_allowed"))

    def test_state_survives_restart_during_cooldown(self) -> None:
        signal_memory.add_signal("TCS FUT", "SELL", now=NOW)
        restored = signal_memory.reload_from_disk()
        self.assertEqual(restored, 1)
        allowed, reason = signal_memory.can_send("TCS FUT", "SELL", now=NOW + timedelta(minutes=1))
        self.assertFalse(allowed)
        self.assertEqual(reason, "cooldown_active")
        self.assertEqual(signal_memory.get_signal("TCS FUT")["side"], "SELL")

    def test_purge_expired_removes_only_expired_entries(self) -> None:
        signal_memory.add_signal("A FUT", "BUY", now=NOW - timedelta(minutes=config.SIGNAL_EXPIRY_MINUTES + 5))
        signal_memory.add_signal("B FUT", "BUY", now=NOW)
        removed = signal_memory.purge_expired(now=NOW)
        self.assertEqual(removed, 1)
        self.assertEqual(signal_memory.get_all_signals(), ["B FUT"])

    def test_delivery_marker_is_idempotent(self) -> None:
        key = signal_memory.delivery_key("NIFTY FUT", "BUY", "2026-08-12T09:30:00+05:30")
        self.assertTrue(signal_memory.mark_delivered(key, now=NOW))
        self.assertFalse(signal_memory.mark_delivered(key, now=NOW))
        self.assertTrue(signal_memory.already_delivered(key))
        signal_memory.reload_from_disk()
        self.assertTrue(signal_memory.already_delivered(key))

    def test_failover_cannot_duplicate_the_same_signal(self) -> None:
        # Angel delivered the signal; the TradingView backup path must not repeat it.
        signal_memory.add_signal("GOLD FUT", "BUY", now=NOW)
        allowed, reason = signal_memory.can_send("GOLD FUT", "BUY", now=NOW + timedelta(seconds=30))
        self.assertFalse(allowed)
        self.assertEqual(reason, "cooldown_active")

    def test_status_reports_persistence(self) -> None:
        state = signal_memory.status()
        self.assertTrue(state["persistent"])
        self.assertEqual(state["cooldown_minutes"], int(config.SIGNAL_COOLDOWN_MINUTES))
        self.assertEqual(state["minimum_hold_minutes"], int(config.MIN_HOLD_MINUTES))

    def test_scheduler_repetition_does_not_resend(self) -> None:
        signal_memory.add_signal("INFY FUT", "BUY", now=NOW)
        for minute in range(1, 6):
            allowed, _ = signal_memory.can_send("INFY FUT", "BUY", now=NOW + timedelta(minutes=minute * 5))
            self.assertFalse(allowed)


class RejectionLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.path = Path(self._directory.name) / "rejections.jsonl"
        rejection_log.reset_for_tests(self.path, max_bytes=65536)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_record_is_structured_and_loadable(self) -> None:
        rejection_log.record("NIFTY FUT", "stale_data", {"age_seconds": 400}, now=NOW)
        rows = rejection_log.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "NIFTY FUT")
        self.assertEqual(rows[0]["reasons"], ["stale_data"])
        self.assertEqual(rows[0]["details"]["age_seconds"], 400)

    def test_secrets_are_redacted(self) -> None:
        rejection_log.record("NIFTY FUT", "auth_failed", {
            "api_key": "abcd1234secretkey",
            "totp": "123456",
            "message": "login failed for token 123456789:AAExampleTelegramBotTokenValue1234567",
            "totp_secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
            "nested": {"password": "hunter2"},
        }, now=NOW)
        raw = self.path.read_text(encoding="utf-8")
        for secret in ("abcd1234secretkey", "hunter2", "GEZDGNBVGY3TQOJQ", "AAExampleTelegramBotTokenValue"):
            self.assertNotIn(secret, raw)
        details = json.loads(raw.splitlines()[0])["details"]
        self.assertEqual(details["api_key"], rejection_log.REDACTED)
        self.assertEqual(details["totp"], rejection_log.REDACTED)
        self.assertEqual(details["nested"]["password"], rejection_log.REDACTED)
        self.assertNotIn("123456", details["message"])

    def test_rotation_keeps_history(self) -> None:
        rejection_log.reset_for_tests(self.path, max_bytes=65536)
        rejection_log.MAX_BYTES = 1024
        for index in range(200):
            rejection_log.record(f"SYM{index} FUT", "engine_filters_not_met", {"index": index}, now=NOW)
        archives = list(self.path.parent.glob(self.path.name + ".*"))
        self.assertTrue(archives, "expected at least one rotated archive")
        self.assertLessEqual(self.path.stat().st_size, 4096)
        rows = rejection_log.load()
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["symbol"], "SYM199 FUT")

    def test_status(self) -> None:
        rejection_log.record("NIFTY FUT", "market_closed", {}, now=NOW)
        state = rejection_log.status()
        self.assertTrue(state["exists"])
        self.assertEqual(state["records"], 1)


class RejectionReportTests(unittest.TestCase):
    def _rows(self, days: int) -> list[dict]:
        rows = []
        day = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        added = 0
        while added < days:
            if rejection_report.is_trading_day(day.date()):
                rows.append({"timestamp": day.isoformat(), "symbol": "NIFTY FUT",
                             "reasons": ["engine_filters_not_met"], "details": {}})
                rows.append({"timestamp": day.isoformat(), "symbol": "SBIN FUT",
                             "reasons": ["stale_data"], "details": {}})
                added += 1
            day += timedelta(days=1)
        return rows

    def test_report_is_marked_incomplete_when_data_is_short(self) -> None:
        data = rejection_report.collect(6, rows=self._rows(2))
        self.assertEqual(data["available_days"], 2)
        self.assertFalse(data["complete"])
        text = rejection_report.render(6, rows=self._rows(2))
        self.assertIn("INCOMPLETE", text)

    def test_report_is_complete_with_six_trading_days(self) -> None:
        data = rejection_report.collect(6, rows=self._rows(6))
        self.assertEqual(data["available_days"], 6)
        self.assertTrue(data["complete"])
        self.assertEqual(data["reason_totals"]["engine_filters_not_met"], 6)
        self.assertEqual(data["reason_totals"]["stale_data"], 6)

    def test_empty_log_never_fabricates(self) -> None:
        data = rejection_report.collect(6, rows=[])
        self.assertEqual(data["days"], [])
        self.assertEqual(data["available_days"], 0)
        text = rejection_report.render(6, rows=[])
        self.assertIn("NO REJECTION DATA RECORDED YET", text)

    def test_weekend_records_are_not_counted_as_trading_days(self) -> None:
        saturday = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)
        data = rejection_report.collect(6, rows=[
            {"timestamp": saturday.isoformat(), "symbol": "NIFTY FUT", "reasons": ["x"], "details": {}}])
        self.assertEqual(data["available_days"], 0)


class SingleInstanceTests(unittest.TestCase):
    def test_startup_uses_a_single_instance_lock(self) -> None:
        source = Path("startup.py").read_text(encoding="utf-8")
        self.assertIn("LOCK_FILE", source)
        self.assertIn("os.getpid()", source)

    def test_scheduler_has_one_scan_lock(self) -> None:
        source = Path("scheduler.py").read_text(encoding="utf-8")
        self.assertIn("_SCAN_LOCK.locked()", source)
        self.assertIn("already_delivered", source)
        self.assertIn("can_send", source)

    def test_scanner_records_market_closed_rejections(self) -> None:
        source = Path("scanner.py").read_text(encoding="utf-8")
        self.assertIn("market_closed", source)
        self.assertIn("rejection_log.record", source)


if __name__ == "__main__":
    unittest.main()
