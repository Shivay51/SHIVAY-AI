"""Low-noise F&O scan diagnostics for SHIVAY AI."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from audience_router import recipients

IST = ZoneInfo("Asia/Kolkata")
_INTERVAL = timedelta(minutes=30)


def _market_open(now: datetime) -> bool:
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (15, 30)


def _message(delivered: int) -> str:
    now = datetime.now(IST).strftime("%I:%M %p")
    if delivered:
        status = f"{delivered} fresh signal delivered"
    else:
        status = "No fresh F&O entry passed the live validation"
    return (
        "📡 SHIVAY AI PRO — F&O SCAN STATUS\n\n"
        f"Time: {now} IST\n"
        "Scanner: ACTIVE ✅\n"
        f"Status: {status}\n\n"
        "Rules: Chandelier confirmation, multi-timeframe alignment, data freshness and risk validation remain active.\n"
        "Action: Wait for a verified BUY/SELL alert."
    )


async def publish_if_due(app: Any, delivered: int) -> None:
    if app is None or not hasattr(app, "bot"):
        return
    now = datetime.now(IST)
    if not _market_open(now):
        return
    state = getattr(app, "bot_data", None)
    if not isinstance(state, dict):
        return
    previous = state.get("fno_status_sent_at")
    if isinstance(previous, datetime) and now - previous < _INTERVAL:
        return
    text = _message(delivered)
    users = await asyncio.to_thread(recipients, "ALL")
    sent = False
    for user in users or []:
        try:
            await app.bot.send_message(chat_id=int(user["id"]), text=text)
            sent = True
        except asyncio.CancelledError:
            raise
        except Exception:
            continue
    if sent:
        state["fno_status_sent_at"] = now
