"""Step 9 — one-click Windows update/start runtime."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import runtime_check

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / "SHIVAY_CONTROL.ps1"
LAUNCHERS = {
    "START_SHIVAY.bat": "Start",
    "RESTART_SHIVAY.bat": "Restart",
    "STATUS_SHIVAY.bat": "Status",
    "STOP_SHIVAY.bat": "Stop",
    "UPDATE_SHIVAY.bat": "Update",
    "CHECK_SHIVAY.bat": "Check",
}
FAKE_ENVIRONMENT = {
    "TELEGRAM_BOT_TOKEN": "1234567890:FAKEtokenFORtestingONLYnotREAL0000000",
    "ADMIN_ID": "424242",
    "ANGEL_API_KEY": "fake-api-key-for-tests",
    "ANGEL_CLIENT_CODE": "T00000",
    "ANGEL_MPIN": "0000",
    "ANGEL_TOTP_SECRET": "AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH",
}


class LauncherTests(unittest.TestCase):
    def test_every_launcher_exists_and_targets_the_control_script(self) -> None:
        for name, action in LAUNCHERS.items():
            path = ROOT / name
            self.assertTrue(path.is_file(), name)
            body = path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("SHIVAY_CONTROL.ps1", body, name)
            self.assertIn(f"-Action {action}", body, name)
            self.assertIn("%~dp0", body, f"{name} must be path-independent")
            self.assertIn("ExecutionPolicy Bypass", body, name)

    def test_control_script_supports_all_actions(self) -> None:
        body = CONTROL.read_text(encoding="utf-8", errors="replace")
        for action in ("Start", "Stop", "Restart", "Status", "Update", "Check"):
            self.assertIn(f'"{action}"', body, action)

    def test_start_is_idempotent_and_prevents_duplicate_processes(self) -> None:
        body = CONTROL.read_text(encoding="utf-8", errors="replace")
        self.assertIn("is already running", body)
        self.assertIn("Get-ShivayProcess", body)
        self.assertIn(".shivay_ai.lock", body)

    def test_stop_only_targets_the_verified_shivay_process(self) -> None:
        body = CONTROL.read_text(encoding="utf-8", errors="replace")
        self.assertIn("bot\\.py", body, "process match must be pinned to bot.py")
        self.assertIn("python(?:w)?\\.exe", body)
        self.assertIn("Stop-Process -Id $running.ProcessId", body)
        self.assertNotIn("Stop-Process -Name python", body)
        self.assertNotIn("taskkill", body.lower())

    def test_update_preserves_local_changes_and_runs_tests(self) -> None:
        body = CONTROL.read_text(encoding="utf-8", errors="replace")
        self.assertIn("git stash push --include-untracked", body)
        self.assertIn("stash pop", body)
        self.assertIn("pip install -q -r requirements.txt", body)
        self.assertIn("pytest tests -q", body)
        self.assertIn("--ff-only", body, "update must never rewrite local history")

    def test_update_provides_rollback_instructions(self) -> None:
        body = CONTROL.read_text(encoding="utf-8", errors="replace")
        self.assertIn("ROLLBACK", body)
        self.assertIn("reset --hard", body)
        self.assertIn(".shivay_last_good_sha", body)

    def test_control_script_performs_a_health_check_before_starting(self) -> None:
        body = CONTROL.read_text(encoding="utf-8", errors="replace")
        self.assertIn("runtime_check.py", body)
        self.assertIn("was not started", body)
        self.assertIn("-Health", body)


class RuntimeCheckTests(unittest.TestCase):
    def test_missing_settings_are_reported_as_not_ready(self) -> None:
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(runtime_check, "_load_dotenv", lambda: None):
            report = runtime_check.variable_report()
        self.assertFalse(report["ready"])
        self.assertEqual(sorted(report["missing"]), sorted(runtime_check.REQUIRED_VARIABLES))

    def test_complete_settings_are_reported_as_ready(self) -> None:
        with patch.dict("os.environ", FAKE_ENVIRONMENT, clear=True), \
             patch.object(runtime_check, "_load_dotenv", lambda: None):
            report = runtime_check.variable_report()
        self.assertTrue(report["ready"])
        self.assertEqual(report["missing"], [])

    def test_values_are_never_printed(self) -> None:
        buffer = io.StringIO()
        with patch.dict("os.environ", FAKE_ENVIRONMENT, clear=True), \
             patch.object(runtime_check, "_load_dotenv", lambda: None), \
             redirect_stdout(buffer):
            runtime_check.main(["--health"])
        output = buffer.getvalue()
        for value in FAKE_ENVIRONMENT.values():
            self.assertNotIn(value, output, "a setting value leaked into the output")
        self.assertIn("SET (value hidden)", output)

    def test_json_output_never_contains_values(self) -> None:
        buffer = io.StringIO()
        with patch.dict("os.environ", FAKE_ENVIRONMENT, clear=True), \
             patch.object(runtime_check, "_load_dotenv", lambda: None), \
             redirect_stdout(buffer):
            runtime_check.main(["--json", "--health"])
        output = buffer.getvalue()
        for value in FAKE_ENVIRONMENT.values():
            self.assertNotIn(value, output)

    def test_safety_report_blocks_unsafe_configuration(self) -> None:
        state = runtime_check.safety_report()
        self.assertTrue(state["signals_only"])
        self.assertFalse(state["live_orders_enabled"])
        self.assertTrue(state["safe"])

    def test_process_report_handles_a_missing_lock_file(self) -> None:
        with patch.object(runtime_check, "LOCK_FILE", ROOT / ".no_such_lock"):
            state = runtime_check.process_report()
        self.assertFalse(state["lock_file"])
        self.assertFalse(state["running"])
        self.assertIsNone(state["pid"])


if __name__ == "__main__":
    unittest.main()
