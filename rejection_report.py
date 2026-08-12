"""Truthful rejection report built only from real recorded rejections.

If the log does not contain the requested number of trading days, the report
says so explicitly. Nothing is estimated, extrapolated or invented.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import rejection_log
from market_session import is_trading_day

IST = ZoneInfo("Asia/Kolkata")


def _day(value: Any) -> date | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(IST).date()


def collect(days: int = 6, *, rows: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Group real rejection records by IST trading day."""
    requested = max(1, int(days))
    records = list(rows) if rows is not None else rejection_log.load()
    grouped: dict[date, Counter] = defaultdict(Counter)
    symbols: dict[date, Counter] = defaultdict(Counter)
    for record in records:
        day = _day(record.get("timestamp"))
        if day is None or not is_trading_day(day):
            continue
        reasons = record.get("reasons")
        for reason in (reasons if isinstance(reasons, list) else [reasons]):
            grouped[day][str(reason)] += 1
        symbol = str(record.get("symbol") or "").strip()
        if symbol:
            symbols[day][symbol] += 1
    ordered = sorted(grouped)[-requested:]
    return {
        "requested_days": requested,
        "available_days": len(ordered),
        "complete": len(ordered) >= requested,
        "total_records": len(records),
        "days": [
            {
                "date": day.isoformat(),
                "rejections": sum(grouped[day].values()),
                "reasons": dict(grouped[day].most_common()),
                "top_symbols": dict(symbols[day].most_common(5)),
            }
            for day in ordered
        ],
        "reason_totals": dict(sum((grouped[day] for day in ordered), Counter()).most_common()),
    }


def render(days: int = 6, *, rows: Iterable[Mapping[str, Any]] | None = None) -> str:
    """Render a plain-text report; never fabricates missing days."""
    data = collect(days, rows=rows)
    lines = ["SHIVAY AI PRO — REJECTION REPORT",
             f"Requested trading days: {data['requested_days']}",
             f"Trading days with real data: {data['available_days']}"]
    if not data["days"]:
        lines.append("")
        lines.append("NO REJECTION DATA RECORDED YET — report cannot be produced.")
        return "\n".join(lines)
    if not data["complete"]:
        lines.append(f"INCOMPLETE: only {data['available_days']} trading day(s) of real data exist.")
    lines.append("")
    for day in data["days"]:
        lines.append(f"{day['date']} — {day['rejections']} rejections")
        for reason, count in day["reasons"].items():
            lines.append(f"  - {reason}: {count}")
    lines.append("")
    lines.append("TOTALS")
    for reason, count in data["reason_totals"].items():
        lines.append(f"  - {reason}: {count}")
    return "\n".join(lines)


def trading_days_available() -> int:
    return int(collect(999)["available_days"])


def missing_trading_days(days: int = 6) -> int:
    data = collect(days)
    return max(0, int(data["requested_days"]) - int(data["available_days"]))


def coverage_window() -> tuple[date | None, date | None]:
    data = collect(999)
    if not data["days"]:
        return None, None
    return date.fromisoformat(data["days"][0]["date"]), date.fromisoformat(data["days"][-1]["date"])


def next_report_ready_on(days: int = 6) -> date | None:
    """Earliest date on which a complete report can exist (no guessing)."""
    missing = missing_trading_days(days)
    if missing == 0:
        return None
    cursor = datetime.now(IST).date()
    while missing:
        cursor += timedelta(days=1)
        if is_trading_day(cursor):
            missing -= 1
    return cursor
