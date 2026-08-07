import unittest

from shivay_alert_guard import evaluate_signal


POLICY = {
    "mode": "ALERTS_ONLY",
    "orders_enabled": False,
    "minimum_score": 75,
    "minimum_risk_reward": 1.5,
    "required_timeframes": ["15m", "30m", "1h"],
    "require_fresh_verified_price": True,
    "require_closed_candle": True,
    "require_volume_confirmation": True,
    "require_risk_reward_validation": True,
    "markets": ["NSE_FNO", "MCX_GOLD", "MCX_SILVER"],
}


class AlertGuardTests(unittest.TestCase):
    def test_accepts_complete_quality_signal(self):
        decision = evaluate_signal({
            "symbol": "NIFTY", "market": "NSE_FNO", "side": "BUY",
            "score": 86, "risk_reward": 2.0,
            "confirmed_timeframes": ["15m", "30m", "1h"],
            "price_verified": True, "candle_closed": True, "volume_confirmed": True,
        }, POLICY)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reasons, ())

    def test_rejects_incomplete_signal(self):
        decision = evaluate_signal({
            "symbol": "NIFTY", "market": "NSE_FNO", "side": "BUY",
            "score": 70, "risk_reward": 1.0,
            "confirmed_timeframes": ["15m"],
            "price_verified": False, "candle_closed": False, "volume_confirmed": False,
        }, POLICY)
        self.assertFalse(decision.accepted)
        self.assertIn("score_below_threshold", decision.reasons)
        self.assertIn("risk_reward_below_threshold", decision.reasons)


if __name__ == "__main__":
    unittest.main()
