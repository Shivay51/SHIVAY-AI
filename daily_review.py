"""Evidence-based end-of-day review. This module never mutates strategy rules."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from accuracy_analyzer import analyze_accuracy
from failure_analyzer import analyze_failures
from strategy_tuner import recommend_tuning
from trade_journal import load_events


def _number(value: Any) -> float:
    try: return float(value or 0)
    except (TypeError, ValueError): return 0.0


def _brief(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"symbol": row.get("symbol", "UNKNOWN"), "side": row.get("side", "UNKNOWN"), "pnl": round(_number(row.get("pnl")), 2), "setup": row.get("setup", row.get("setup_type", "UNKNOWN")), "reason": row.get("reason", row.get("failure_cause"))}


def generate_daily_review(review_date: date | None = None) -> dict[str, Any]:
    target = review_date or date.today()
    rows = load_events()
    daily = [row for row in rows if str(row.get("timestamp", ""))[:10] == target.isoformat()]
    outcomes = [row for row in daily if row.get("event_type") == "OUTCOME"]
    rejections = [row for row in daily if row.get("event_type") == "REJECTION"]
    ordered = sorted(outcomes, key=lambda row: _number(row.get("pnl")), reverse=True)
    fake = [row for row in rejections if any("fake" in str(reason).lower() or "trap" in str(reason).lower() for reason in row.get("reasons", []))]
    late = [row for row in rejections if any("late" in str(reason).lower() or "travelled" in str(reason).lower() or "expired" in str(reason).lower() for reason in row.get("reasons", []))]
    wrong_entries = [row for row in outcomes if str(row.get("result", "")).upper() == "LOSS" and str(row.get("failure_cause", "")) in {"stop_loss_hit", "strategy_exit"}]
    trend_errors = [row for row in wrong_entries if "trend" in str(row.get("failure_cause", "")).lower() or "SIDEWAYS" in str(row.get("market_regime", "")).upper()]
    accuracy = analyze_accuracy(rows)
    failures = analyze_failures(rows)
    tuning = recommend_tuning(accuracy)
    suggestions = list(tuning.get("recommendations", []))
    if fake: suggestions.append("review_fake_breakout_confirmation")
    if late: suggestions.append("review_entry_timing_and_validity")
    if wrong_entries: suggestions.append("review_stop_structure_and_entry_location")
    return {"date": target.isoformat(), "best_trades": [_brief(row) for row in ordered[:3] if _number(row.get("pnl")) > 0], "worst_trades": [_brief(row) for row in reversed(ordered[-3:]) if _number(row.get("pnl")) < 0], "missed_trades": len(rejections), "fake_signals": len(fake), "late_signals": len(late), "wrong_entries": len(wrong_entries), "trend_errors": len(trend_errors), "suggestions": sorted(set(suggestions)), "accuracy": accuracy, "failures": failures, "tuning": tuning, "strategy_changed": False, "statistical_evidence_required": True}
