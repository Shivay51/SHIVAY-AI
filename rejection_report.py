"""Multi-day (5-6 session) rejection reporting for SHIVAY AI.

The journal already records every rejected candidate, but nothing summarised
those records across the observation window. Without that view it is
impossible to tell whether "no signals today" means the market was quiet or
that one filter is silently rejecting everything.

This module answers exactly that: it groups journalled ``REJECTION`` events by
IST session date and by reason, so the operator can see which filter dominates
and whether the pipeline is reaching the final delivery gate at all.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from trade_journal import load_events

LOGGER = logging.getLogger("shivay.rejection_report")
IST = ZoneInfo("Asia/Kolkata")

DEFAULT_WINDOW_DAYS = 6
MIN_WINDOW_DAYS = 5
MAX_WINDOW_DAYS = 30

# Reason prefixes grouped into operator-meaningful stages.
_STAGE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DATA", ("no_market_data", "market_data_rejected", "price_below_100", "scanner_exception")),
    ("FRESHNESS", ("stale_at_delivery", "freshness_check_failed", "market_snapshot_missing",
                   "delayed_data_not_signal_capable", "stale_data", "missing_timestamp",
                   "frozen_feed")),
    ("STRATEGY", ("engine_filters_not_met", "strategy_not_tradeable")),
    ("CHANDELIER", ("chandelier_confirmation_missing", "signal_candle_missing")),
    ("RISK", ("trade_plan_invalid", "buy_invalidation_invalid", "sell_invalidation_invalid",
              "entry_validity_failed")),
    ("COOLDOWN", ("repeat_cooldown_active", "duplicate_signal")),
)


def _session_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(IST).date()


def classify_reason(reason: Any) -> str:
    """Map a raw rejection reason onto a pipeline stage."""
    text = str(reason or "").strip().lower()
    if not text:
        return "OTHER"
    for stage, prefixes in _STAGE_RULES:
        if any(text.startswith(prefix) for prefix in prefixes):
            return stage
    return "OTHER"


def _reasons(row: Mapping[str, Any]) -> list[str]:
    raw = row.get("reasons")
    if isinstance(raw, str):
        candidates: Iterable[Any] = [raw]
    elif isinstance(raw, (list, tuple)):
        candidates = raw
    else:
        candidates = [row.get("reason")] if row.get("reason") else []
    output: list[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if text:
            # Trim the ":detail" suffix so counts group by cause, not by value.
            output.append(text.split(":", 1)[0])
    return output or ["unspecified"]


def build_rejection_report(
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarise rejections over the trailing observation window."""
    days = max(MIN_WINDOW_DAYS, min(int(window_days or DEFAULT_WINDOW_DAYS), MAX_WINDOW_DAYS))
    end = today or datetime.now(IST).date()
    start = end - timedelta(days=days - 1)
    rows = list(events) if events is not None else load_events()

    per_day: dict[str, Counter[str]] = defaultdict(Counter)
    per_day_stage: dict[str, Counter[str]] = defaultdict(Counter)
    per_symbol: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    signals_per_day: Counter[str] = Counter()
    rejections = 0
    signals = 0

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        event_type = str(row.get("event_type", "")).upper()
        if event_type not in {"REJECTION", "SIGNAL"}:
            continue
        session = _session_date(row.get("timestamp"))
        if session is None or not (start <= session <= end):
            continue
        key = session.isoformat()
        if event_type == "SIGNAL":
            signals += 1
            signals_per_day[key] += 1
            continue
        rejections += 1
        symbol = str(row.get("symbol", "")).strip().upper() or "UNKNOWN"
        per_symbol[symbol] += 1
        for reason in _reasons(row):
            totals[reason] += 1
            per_day[key][reason] += 1
            stage = classify_reason(reason)
            stages[stage] += 1
            per_day_stage[key][stage] += 1

    session_keys = [(start + timedelta(days=offset)).isoformat() for offset in range(days)]
    dominant = totals.most_common(1)[0] if totals else None
    dominant_share = round(dominant[1] / rejections * 100, 1) if dominant and rejections else 0.0

    return {
        "window_days": days,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "sessions": session_keys,
        "total_rejections": rejections,
        "total_signals": signals,
        "reason_totals": dict(totals.most_common()),
        "stage_totals": dict(stages.most_common()),
        "per_day": {key: dict(per_day.get(key, Counter()).most_common()) for key in session_keys},
        "per_day_stage": {key: dict(per_day_stage.get(key, Counter()).most_common()) for key in session_keys},
        "per_day_rejection_count": {key: sum(per_day.get(key, Counter()).values()) for key in session_keys},
        "per_day_signal_count": {key: signals_per_day.get(key, 0) for key in session_keys},
        "top_symbols": dict(per_symbol.most_common(10)),
        "dominant_reason": dominant[0] if dominant else None,
        "dominant_reason_count": dominant[1] if dominant else 0,
        "dominant_reason_share_percent": dominant_share,
        "silent_pipeline": bool(rejections and not signals),
        "no_activity": not rejections and not signals,
        "generated_at": datetime.now(IST).isoformat(),
    }


def format_rejection_report(report: Mapping[str, Any] | None = None, limit: int = 6) -> str:
    """Render the report as a compact, secret-free Telegram message."""
    data = dict(report or build_rejection_report())
    lines = [
        "🧾 REJECTION REPORT",
        f"WINDOW: {data.get('from')} → {data.get('to')} ({data.get('window_days')} sessions)",
        f"SIGNALS: {data.get('total_signals', 0)} | REJECTED: {data.get('total_rejections', 0)}",
    ]
    if data.get("no_activity"):
        lines.append("No scanner activity was journalled in this window.")
        return "\n".join(lines)
    if data.get("silent_pipeline"):
        lines.append("⚠️ Every candidate was rejected; no signal reached Telegram.")

    stage_totals = data.get("stage_totals") or {}
    if stage_totals:
        lines.append("")
        lines.append("BY STAGE")
        for stage, count in list(stage_totals.items())[:limit]:
            lines.append(f"• {stage}: {count}")

    reason_totals = data.get("reason_totals") or {}
    if reason_totals:
        lines.append("")
        lines.append("TOP REASONS")
        for reason, count in list(reason_totals.items())[:limit]:
            lines.append(f"• {reason}: {count}")

    per_day = data.get("per_day_rejection_count") or {}
    per_day_signals = data.get("per_day_signal_count") or {}
    if per_day:
        lines.append("")
        lines.append("PER SESSION (signals/rejections)")
        for session in data.get("sessions") or sorted(per_day):
            lines.append(f"• {session}: {per_day_signals.get(session, 0)}/{per_day.get(session, 0)}")

    dominant = data.get("dominant_reason")
    if dominant:
        lines.append("")
        lines.append(
            f"DOMINANT: {dominant} ({data.get('dominant_reason_share_percent', 0)}% of rejections)"
        )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "build_rejection_report",
    "classify_reason",
    "format_rejection_report",
]
