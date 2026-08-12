"""Indian market session windows for NSE F&O and MCX (IST, holiday aware)."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

WINDOWS: dict[str, tuple[time, time]] = {
    "NSE_FNO": (time(9, 15), time(15, 30)),
    "CASH": (time(9, 15), time(15, 30)),
    "MCX_COMM": (time(9, 0), time(23, 30)),
}
PRE_OPEN = {
    "NSE_FNO": (time(9, 0), time(9, 15)),
    "CASH": (time(9, 0), time(9, 15)),
    "MCX_COMM": (time(8, 45), time(9, 0)),
}


def _segment(value: str | None) -> str:
    text = str(value or "").strip().upper()
    if text in WINDOWS:
        return text
    if "MCX" in text or "COMM" in text:
        return "MCX_COMM"
    return "NSE_FNO"


def holidays() -> set[date]:
    """Trading holidays supplied through MARKET_HOLIDAYS as ISO dates."""
    result: set[date] = set()
    for item in os.getenv("MARKET_HOLIDAYS", "").split(","):
        text = item.strip()
        if not text:
            continue
        try:
            result.add(date.fromisoformat(text))
        except ValueError:
            continue
    return result


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_day(moment: datetime | date | None = None) -> bool:
    if isinstance(moment, date) and not isinstance(moment, datetime):
        day = moment
    else:
        day = (moment or now_ist()).astimezone(IST).date()
    return day.weekday() < 5 and day not in holidays()


def is_market_open(segment: str | None = None, moment: datetime | None = None) -> bool:
    moment = (moment or now_ist()).astimezone(IST)
    if not is_trading_day(moment):
        return False
    start, end = WINDOWS[_segment(segment)]
    return start <= moment.timetz().replace(tzinfo=None) <= end


def market_state(segment: str | None = None, moment: datetime | None = None) -> str:
    moment = (moment or now_ist()).astimezone(IST)
    key = _segment(segment)
    if not is_trading_day(moment):
        return "CLOSED_HOLIDAY" if moment.weekday() < 5 else "CLOSED_WEEKEND"
    clock = moment.timetz().replace(tzinfo=None)
    if is_market_open(key, moment):
        return "OPEN"
    pre_start, pre_end = PRE_OPEN[key]
    if pre_start <= clock < pre_end:
        return "PRE_OPEN"
    return "CLOSED"


def next_open(segment: str | None = None, moment: datetime | None = None) -> datetime:
    moment = (moment or now_ist()).astimezone(IST)
    key = _segment(segment)
    start = WINDOWS[key][0]
    candidate = moment.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if candidate <= moment or not is_trading_day(candidate):
        candidate += timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def signals_allowed(segment: str | None = None, moment: datetime | None = None) -> bool:
    """New signals are produced only inside a live trading session."""
    return market_state(segment, moment) == "OPEN"


def status(segment: str | None = None, moment: datetime | None = None) -> dict[str, Any]:
    key = _segment(segment)
    moment = (moment or now_ist()).astimezone(IST)
    state = market_state(key, moment)
    return {
        "segment": key,
        "state": state,
        "is_open": state == "OPEN",
        "signals_allowed": state == "OPEN",
        "session_start": WINDOWS[key][0].isoformat(),
        "session_end": WINDOWS[key][1].isoformat(),
        "checked_at": moment.isoformat(),
        "next_open": next_open(key, moment).isoformat(),
        "trading_day": is_trading_day(moment),
    }
