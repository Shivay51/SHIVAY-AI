import tempfile
import unittest
from pathlib import Path

from alert_runtime import process_signals


POLICY = {
    "minimum_score": 75, "minimum_risk_reward": 1.5,
    "required_timeframes": ["15m", "30m", "1h"],
    "require_fresh_verified_price": True, "require_closed_candle": True,
    "require_volume_confirmation": True, "require_risk_reward_validation": True,
    "markets": ["NSE_FNO"], "cooldown_minutes": 25,
}
SIGNAL = {
    "symbol": "NIFTY", "market": "NSE_FNO", "side": "BUY",
    "score": 85, "risk_reward": 2,
    "confirmed_timeframes": ["15m", "30m", "1h"],
    "price_verified": True, "candle_closed": True, "volume_confirmed": True,
}


class AlertRuntimeTests(unittest.TestCase):
    def test_accepts_once_then_applies_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            self.assertEqual(len(process_signals([SIGNAL], POLICY, state)), 1)
            self.assertEqual(process_signals([SIGNAL], POLICY, state), [])


if __name__ == "__main__":
    unittest.main()
