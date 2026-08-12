"""Actionability and safety classification for scanner signals.

Rules (identical for BUY and SELL):

* score below ACTIONABLE_SCORE (70)  -> IGNORE, never delivered
* ACTIONABLE_SCORE .. SAFE_SCORE     -> RISKY, delivered with a risk label
* SAFE_SCORE and above               -> SAFE
* PREMIUM_SCORE (90+) and above      -> SAFE + PREMIUM tier
"""
from __future__ import annotations

from typing import Any, Mapping

import config

IGNORE = "IGNORE"
RISKY = "RISKY"
SAFE = "SAFE"


def thresholds() -> dict[str, int]:
    return {
        "actionable": int(config.ACTIONABLE_SCORE),
        "safe": int(config.SAFE_SCORE),
        "premium": int(config.PREMIUM_SCORE),
    }


def _score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def classify(signal: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a scanner result without favouring either direction."""
    limits = thresholds()
    score = _score(signal.get("score"))
    side = str(signal.get("side", "")).upper()
    risk_reward = _score(signal.get("risk_reward") or signal.get("rr"))
    confirmed = bool((signal.get("chandelier_entry_state") or {}).get("confirmed") or signal.get("entry_confirmed"))
    reasons: list[str] = []

    if side not in {"BUY", "SELL"}:
        reasons.append("side_not_actionable")
    if score < limits["actionable"]:
        reasons.append("score_below_actionable_threshold")
    if not confirmed:
        reasons.append("chandelier_not_confirmed")
    if risk_reward and risk_reward < float(config.MIN_RISK_REWARD):
        reasons.append("risk_reward_below_minimum")

    if reasons:
        classification = IGNORE
    elif score >= limits["safe"]:
        classification = SAFE
    else:
        classification = RISKY

    premium = classification != IGNORE and score >= limits["premium"]
    return {
        "classification": classification,
        "class": classification,
        "actionable": classification != IGNORE,
        "deliverable": classification != IGNORE,
        "premium": premium,
        "tier": "PREMIUM_90_PLUS" if premium else ("STANDARD" if classification != IGNORE else "NOT_DELIVERED"),
        "score": round(score, 2),
        "thresholds": limits,
        "reasons": reasons,
        "minimum_hold_minutes": int(config.MIN_HOLD_MINUTES),
    }


def is_deliverable(signal: Mapping[str, Any]) -> bool:
    """IGNORE-class signals are never delivered to any chat."""
    value = signal.get("signal_class") if isinstance(signal.get("signal_class"), Mapping) else None
    return bool((value or classify(signal))["deliverable"])


def annotate(signal: dict[str, Any]) -> dict[str, Any]:
    value = classify(signal)
    signal["signal_class"] = value
    signal["signal_classification"] = value["classification"]
    signal["actionable"] = value["actionable"]
    signal["premium_signal"] = value["premium"]
    signal["minimum_hold_minutes"] = value["minimum_hold_minutes"]
    return signal


def filter_deliverable(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [annotate(dict(item)) for item in signals if classify(item)["deliverable"]]
