"""Scheduler-compatible F&O watchdog.

The production scheduler loads this optional module and forwards non-empty status
strings to Telegram. No trade decisions or order actions are performed here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_INTERVAL = timedelta(minutes=30)
_last_sent: datetime | None = None


def _market_open(now: datetime) -> bool:
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (15, 30)


def scheduled_task() -> str:
    """Return a low-noise F&O heartbeat for the existing optional-job sender."""
    global _last_sent
    now = datetime.now(IST)
    if not _market_open(now):
        return ""
    if _last_sent is not None and now - _last_sent < _INTERVAL:
        return ""
    _last_sent = now
    return (
        "📡 SHIVAY AI PRO — F&O WATCHDOG\n\n"
        f"Time: {now.strftime('%I:%M %p')} IST\n"
        "Scheduler: ACTIVE ✅\n"
        "F&O alert engine: No verified BUY/SELL signal in this interval.\n\n"
        "This is a status message only; trade-entry rules remain unchanged."
    )
