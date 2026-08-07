import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from alert_only_pipeline import AlertOnlyPipeline


@dataclass
class Signal:
    side: str = "BUY"
    score: int = 90
    entry: float = 100.0
    stop_loss: float = 95.0
    target1: float = 110.0
    target2: float = 115.0
    target3: float = 120.0
    reasons: list[str] = None

    def __post_init__(self):
        self.reasons = self.reasons or ["volume confirmation"]


class Tracker:
    def record(self, *args):
        return 1


class PipelineTests(unittest.TestCase):
    def test_quality_approved_signal_is_sent_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "cooldown.json"
            messages = []
            pipeline = AlertOnlyPipeline(tracker=Tracker(), state_file=str(state))
            with patch("alert_only_pipeline.build_signal", return_value=Signal()), patch("alert_only_pipeline.telegram_text", return_value="ok"):
                result = pipeline.process("NIFTY", "NIFTY FUT", "NSE F&O", 100, None, None, None, messages.append)
            self.assertIsNotNone(result)
            self.assertEqual(messages, ["ok"])


if __name__ == "__main__":
    unittest.main()
