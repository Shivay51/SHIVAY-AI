"""Five-to-six session rejection reporting tests."""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rejection_report

IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 8, 12)


def _event(day_offset: int, event_type: str, reasons=None, symbol="NIFTY FUT", hour=11):
    stamp = datetime(TODAY.year, TODAY.month, TODAY.day, hour, 0, tzinfo=IST) - timedelta(days=day_offset)
    payload = {
        "event_type": event_type,
        "timestamp": stamp.astimezone(ZoneInfo("UTC")).isoformat(),
        "symbol": symbol,
    }
    if reasons is not None:
        payload["reasons"] = reasons
    return payload


class ReportWindowTests(unittest.TestCase):
    def test_window_is_clamped_to_at_least_five_sessions(self) -> None:
        report = rejection_report.build_rejection_report(window_days=1, today=TODAY, events=[])
        self.assertEqual(report["window_days"], 5)
        self.assertEqual(len(report["sessions"]), 5)

    def test_default_window_is_six_sessions(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=[])
        self.assertEqual(report["window_days"], 6)
        self.assertEqual(report["from"], (TODAY - timedelta(days=5)).isoformat())
        self.assertEqual(report["to"], TODAY.isoformat())

    def test_events_outside_the_window_are_excluded(self) -> None:
        events = [
            _event(0, "REJECTION", ["stale_data"]),
            _event(20, "REJECTION", ["stale_data"]),
        ]
        report = rejection_report.build_rejection_report(today=TODAY, events=events)
        self.assertEqual(report["total_rejections"], 1)


class ReportContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            _event(0, "REJECTION", ["chandelier_confirmation_missing"]),
            _event(0, "REJECTION", ["chandelier_confirmation_missing"]),
            _event(1, "REJECTION", ["stale_at_delivery:stale_data,frozen_feed"]),
            _event(2, "REJECTION", ["engine_filters_not_met"], symbol="TCS FUT"),
            _event(3, "REJECTION", ["repeat_cooldown_active:900s"]),
            _event(1, "SIGNAL"),
        ]

    def test_reason_suffixes_are_grouped_by_cause(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=self.events)
        self.assertIn("stale_at_delivery", report["reason_totals"])
        self.assertIn("repeat_cooldown_active", report["reason_totals"])
        self.assertNotIn("repeat_cooldown_active:900s", report["reason_totals"])

    def test_stage_classification_groups_pipeline_phases(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=self.events)
        stages = report["stage_totals"]
        self.assertEqual(stages.get("CHANDELIER"), 2)
        self.assertEqual(stages.get("FRESHNESS"), 1)
        self.assertEqual(stages.get("STRATEGY"), 1)
        self.assertEqual(stages.get("COOLDOWN"), 1)

    def test_dominant_reason_and_share_are_reported(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=self.events)
        self.assertEqual(report["dominant_reason"], "chandelier_confirmation_missing")
        self.assertEqual(report["dominant_reason_count"], 2)
        self.assertGreater(report["dominant_reason_share_percent"], 0)

    def test_signals_are_counted_separately_from_rejections(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=self.events)
        self.assertEqual(report["total_signals"], 1)
        self.assertEqual(report["total_rejections"], 5)
        self.assertFalse(report["silent_pipeline"])

    def test_silent_pipeline_is_flagged_when_nothing_is_delivered(self) -> None:
        events = [_event(0, "REJECTION", ["engine_filters_not_met"])]
        report = rejection_report.build_rejection_report(today=TODAY, events=events)
        self.assertTrue(report["silent_pipeline"])

    def test_no_activity_is_flagged_for_an_empty_journal(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=[])
        self.assertTrue(report["no_activity"])

    def test_per_session_counts_cover_every_day_in_the_window(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=self.events)
        self.assertEqual(len(report["per_day_rejection_count"]), 6)
        self.assertEqual(report["per_day_rejection_count"][TODAY.isoformat()], 2)

    def test_top_symbols_are_ranked(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=self.events)
        self.assertEqual(max(report["top_symbols"], key=report["top_symbols"].get), "NIFTY FUT")

    def test_unknown_reasons_fall_into_other(self) -> None:
        self.assertEqual(rejection_report.classify_reason("some_new_filter"), "OTHER")
        self.assertEqual(rejection_report.classify_reason(""), "OTHER")


class ReportFormattingTests(unittest.TestCase):
    def test_message_is_readable_and_secret_free(self) -> None:
        events = [
            _event(0, "REJECTION", ["chandelier_confirmation_missing"]),
            _event(1, "SIGNAL"),
        ]
        report = rejection_report.build_rejection_report(today=TODAY, events=events)
        text = rejection_report.format_rejection_report(report)
        self.assertIn("REJECTION REPORT", text)
        self.assertIn("BY STAGE", text)
        self.assertIn("PER SESSION", text)
        for forbidden in ("API", "token", "secret", "password"):
            self.assertNotIn(forbidden.lower(), text.lower())

    def test_empty_window_message_is_explicit(self) -> None:
        report = rejection_report.build_rejection_report(today=TODAY, events=[])
        text = rejection_report.format_rejection_report(report)
        self.assertIn("No scanner activity", text)

    def test_silent_pipeline_warning_is_visible(self) -> None:
        events = [_event(0, "REJECTION", ["stale_data"])]
        report = rejection_report.build_rejection_report(today=TODAY, events=events)
        self.assertIn("no signal reached Telegram", rejection_report.format_rejection_report(report))


if __name__ == "__main__":
    unittest.main()
