"""Step 8 — the security audit and the end-to-end dry run must stay green."""
from __future__ import annotations

import unittest

import config
import dry_run
import security_audit


class SecretScanTests(unittest.TestCase):
    def test_repository_has_no_committed_secrets(self) -> None:
        findings = security_audit.scan_secrets()
        self.assertEqual(findings, [], f"secret-shaped values found: {findings}")

    def test_env_file_is_not_tracked_and_is_ignored(self) -> None:
        self.assertEqual(security_audit.scan_env_file(), [])

    def test_no_order_capability_anywhere_in_the_codebase(self) -> None:
        self.assertEqual(security_audit.scan_order_capability(), [])

    def test_detector_actually_detects_a_planted_secret(self) -> None:
        # assembled at runtime so this test file never contains a secret-shaped literal
        planted = "bot" + "_token = \"9876543210" + ":" + "Z" * 34 + "\""
        matched = [label for label, pattern in security_audit.SECRET_RULES if pattern.search(planted)]
        self.assertTrue(matched, "the secret detector is not detecting anything")

    def test_signals_only_state_is_enforced(self) -> None:
        state = security_audit.signals_only_state()
        self.assertTrue(state["SIGNALS_ONLY"])
        self.assertFalse(state["LIVE_ORDER_PLACEMENT_ENABLED"])
        self.assertTrue(state["PAPER_MONITORING"])
        self.assertTrue(state["SIGNAL_ADMIN_ONLY"])

    def test_full_audit_is_clean(self) -> None:
        report = security_audit.audit()
        self.assertTrue(report["clean"], report)


class DryRunTests(unittest.TestCase):
    def test_every_dry_run_check_passes(self) -> None:
        failed = [(name, detail) for name, ok, detail in dry_run.RESULTS if not ok]
        self.assertEqual(failed, [], f"dry-run failures: {failed}")

    def test_dry_run_covers_the_critical_surfaces(self) -> None:
        names = " | ".join(name for name, _, _ in dry_run.RESULTS)
        for surface in ("providers", "instruments", "freshness", "classification",
                        "duplicate memory", "telegram", "scheduler", "safety", "security audit"):
            self.assertIn(surface, names, surface)
        self.assertGreaterEqual(len(dry_run.RESULTS), 15)

    def test_live_order_placement_stays_disabled(self) -> None:
        self.assertFalse(config.LIVE_ORDER_PLACEMENT_ENABLED)
        self.assertTrue(config.SIGNALS_ONLY)


if __name__ == "__main__":
    unittest.main()
