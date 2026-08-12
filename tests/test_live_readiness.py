"""Step 10 — the readiness verifier must never overclaim."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["SHIVAY_SKIP_NESTED_TESTS"] = "1"

import live_readiness


class HonestyTests(unittest.TestCase):
    def test_credential_dependent_rows_are_not_verified_without_credentials(self) -> None:
        with patch.object(live_readiness, "_credentials_present", return_value=False):
            for check in (live_readiness._angel_authentication,
                          live_readiness._live_quotes,
                          live_readiness._futures_candles,
                          live_readiness._market_observation):
                status, evidence = check()
                self.assertEqual(status, live_readiness.NOT_VERIFIED, check.__name__)
                self.assertTrue(evidence)

    def test_live_rows_are_still_not_verified_when_credentials_exist_but_no_session_observed(self) -> None:
        with patch.object(live_readiness, "_credentials_present", return_value=True):
            self.assertEqual(live_readiness._angel_authentication()[0], live_readiness.NOT_VERIFIED)
            self.assertEqual(live_readiness._live_quotes()[0], live_readiness.NOT_VERIFIED)
            self.assertEqual(live_readiness._market_observation()[0], live_readiness.NOT_VERIFIED)

    def test_decision_is_never_ready_while_any_row_is_unverified(self) -> None:
        report = live_readiness.verify()
        if report["not_verified"] or report["blocked"]:
            self.assertNotEqual(report["decision"], "READY")

    def test_decision_is_blocked_when_a_row_is_blocked(self) -> None:
        rows = [{"item": "a", "status": live_readiness.VERIFIED, "evidence": ""},
                {"item": "b", "status": live_readiness.BLOCKED, "evidence": ""}]
        with patch.object(live_readiness, "_row", side_effect=lambda name, check: rows.pop(0)), \
             patch.object(live_readiness, "CHECKS", (("a", lambda: ("", "")), ("b", lambda: ("", "")))):
            report = live_readiness.verify()
        self.assertEqual(report["decision"], "BLOCKED")

    def test_table_has_a_row_for_every_required_verification(self) -> None:
        items = " | ".join(name for name, _ in live_readiness.CHECKS).lower()
        for required in ("branch", "authentication", "quotes", "candles", "backup", "third",
                         "scheduler", "telegram", "duplicate", "stale", "order", "restart",
                         "test suite", "market-session"):
            self.assertIn(required, items, required)

    def test_rendered_table_carries_a_final_decision(self) -> None:
        text = live_readiness.render(live_readiness.verify())
        self.assertIn("FINAL DECISION:", text)
        self.assertIn("| # | ITEM | STATUS | EVIDENCE |", text)

    def test_offline_structural_rows_are_verified(self) -> None:
        for check in (live_readiness._provider_topology,
                      live_readiness._no_third_provider,
                      live_readiness._scheduler,
                      live_readiness._admin_only_telegram,
                      live_readiness._single_instance,
                      live_readiness._no_stale_signal,
                      live_readiness._no_live_orders,
                      live_readiness._restart_recovery):
            status, evidence = check()
            self.assertEqual(status, live_readiness.VERIFIED, f"{check.__name__}: {evidence}")


if __name__ == "__main__":
    unittest.main()
