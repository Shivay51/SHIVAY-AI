"""Deterministic pre-alert validation for SHIVAY.

This module never places orders. It accepts or rejects a prepared signal only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class GuardDecision:
    accepted: bool
    score: float
    reasons: tuple[str, ...]


def load_policy(path: str | Path = "alert_quality_policy.json") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        policy = json.load(handle)
    if policy.get("mode") != "ALERTS_ONLY" or policy.get("orders_enabled") is not False:
        raise ValueError("Alert guard requires ALERTS_ONLY mode with orders disabled")
    return policy


def _as_positive_float(value: Any, field: str, reasons: list[str]) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        reasons.append(f"{field}_missing_or_invalid")
        return 0.0
    if number <= 0:
        reasons.append(f"{field}_must_be_positive")
    return number


def evaluate_signal(signal: Mapping[str, Any], policy: Mapping[str, Any]) -> GuardDecision:
    reasons: list[str] = []
    score = _as_positive_float(signal.get("score"), "score", reasons)
    if score < float(policy["minimum_score"]):
        reasons.append("score_below_threshold")

    required_frames = {str(frame).lower() for frame in policy["required_timeframes"]}
    confirmed_frames = {str(frame).lower() for frame in signal.get("confirmed_timeframes", [])}
    missing_frames = sorted(required_frames - confirmed_frames)
    if missing_frames:
        reasons.append("missing_timeframe_confirmation:" + ",".join(missing_frames))

    checks = (
        ("require_fresh_verified_price", "price_verified", "price_not_verified"),
        ("require_closed_candle", "candle_closed", "candle_not_closed"),
        ("require_volume_confirmation", "volume_confirmed", "volume_not_confirmed"),
    )
    for policy_key, signal_key, reason in checks:
        if policy.get(policy_key) and signal.get(signal_key) is not True:
            reasons.append(reason)

    if policy.get("require_risk_reward_validation"):
        risk_reward = _as_positive_float(signal.get("risk_reward"), "risk_reward", reasons)
        if risk_reward < float(policy.get("minimum_risk_reward", 1.0)):
            reasons.append("risk_reward_below_threshold")

    if str(signal.get("market", "")) not in set(policy["markets"]):
        reasons.append("market_not_allowed")
    if str(signal.get("side", "")).upper() not in {"BUY", "SELL"}:
        reasons.append("invalid_side")
    if not str(signal.get("symbol", "")).strip():
        reasons.append("symbol_missing")

    return GuardDecision(not reasons, score, tuple(reasons))
