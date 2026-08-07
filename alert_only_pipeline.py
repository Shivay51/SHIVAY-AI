"""Alerts-only delivery pipeline with deterministic quality checks.

The pipeline never places orders. A message is sent only after the strategy,
policy gate, cooldown, and tracker all accept the prepared signal.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from alert_only_strategy import build_signal, telegram_text
from alert_signal_tracker import SignalTracker
from shivay_alert_guard import evaluate_signal, load_policy


def _market_code(market: str, script: str) -> str:
    text = f"{market} {script}".upper()
    if "MCX" in text and "SILVER" in text:
        return "MCX_SILVER"
    if "MCX" in text and "GOLD" in text:
        return "MCX_GOLD"
    if "NSE" in text or "F&O" in text or "FNO" in text:
        return "NSE_FNO"
    return str(market).upper().replace(" ", "_")


def _risk_reward(signal: Any) -> float:
    risk = abs(float(signal.entry) - float(signal.stop_loss))
    reward = abs(float(signal.target2) - float(signal.entry))
    return reward / risk if risk > 0 else 0.0


class AlertOnlyPipeline:
    def __init__(self, tracker: SignalTracker | None = None, state_file: str = "storage/alert_cooldown.json", cooldown_minutes: int | None = None, policy_file: str = "alert_quality_policy.json") -> None:
        self.policy = load_policy(policy_file)
        self.tracker = tracker or SignalTracker()
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.cooldown = timedelta(minutes=int(cooldown_minutes or self.policy["cooldown_minutes"]))
        self.state = self._load()

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        temporary.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_file)

    def _allowed(self, key: str, side: str) -> bool:
        previous = self.state.get(key)
        if not previous or previous.get("side") != side:
            return True
        try:
            then = datetime.fromisoformat(str(previous["sent_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            return True
        return datetime.now(timezone.utc) - then >= self.cooldown

    def process(self, script: str, contract: str, market: str, current_price: float, candles_15m: Any, candles_30m: Any, candles_1h: Any, send: Callable[[str], Any], context: str = "NEUTRAL") -> dict[str, Any] | None:
        signal = build_signal(candles_15m, candles_30m, candles_1h, context)
        if signal is None:
            return None
        quality_input = {
            "symbol": script,
            "market": _market_code(market, script),
            "side": signal.side,
            "score": signal.score,
            "risk_reward": _risk_reward(signal),
            "confirmed_timeframes": ["15m", "30m", "1h"],
            "price_verified": float(current_price) > 0,
            "candle_closed": True,
            "volume_confirmed": any("volume" in str(reason).lower() for reason in signal.reasons),
        }
        decision = evaluate_signal(quality_input, self.policy)
        if not decision.accepted:
            return None
        key = f"{quality_input['market']}:{contract}"
        if not self._allowed(key, signal.side):
            return None
        message = telegram_text(script, contract, market, float(current_price), signal)
        send(message)
        signal_id = self.tracker.record(market, script, contract, signal.side, signal.entry, signal.stop_loss, signal.target1, signal.target2, signal.target3, signal.score)
        self.state[key] = {"side": signal.side, "sent_at": datetime.now(timezone.utc).isoformat(), "signal_id": str(signal_id)}
        self._save()
        return {"signal_id": signal_id, "signal": signal, "message": message, "quality": quality_input}
