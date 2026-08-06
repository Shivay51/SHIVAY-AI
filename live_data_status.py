"""Cached, read-only per-market status built only from numeric provider responses."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as wall_time, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from mcx_temporary_provider import MCXTemporaryProvider
from nse_temporary_provider import NSETemporaryProvider
from tvkit_provider import TVKitProvider

_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
IST = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def _age(value: dict[str, Any] | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    stamp = value.get("market_timestamp")
    if stamp is None and value.get("timestamp_kind") != "retrieval_time":
        stamp = value.get("timestamp")
    if not isinstance(stamp, datetime):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    instant = now or datetime.now(timezone.utc)
    return max(0.0, (instant.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())


def exchange_is_open(market: str, now: datetime | None = None) -> bool:
    """Return exchange-session state independently from quote freshness."""
    instant = now or datetime.now(timezone.utc)
    key = str(market).upper()
    if "COMEX" in key:
        local = instant.astimezone(NEW_YORK)
        weekday, clock = local.weekday(), local.time()
        if weekday == 5 or (weekday == 6 and clock < wall_time(18, 0)) or (weekday == 4 and clock >= wall_time(17, 0)):
            return False
        return not (wall_time(17, 0) <= clock < wall_time(18, 0))
    if key in {"DOW", "S&P 500", "NASDAQ"}:
        local = instant.astimezone(NEW_YORK)
        return local.weekday() < 5 and wall_time(9, 30) <= local.time() <= wall_time(16, 0)
    if "NIKKEI" in key:
        local = instant.astimezone(TOKYO)
        return local.weekday() < 5 and wall_time(9, 0) <= local.time() <= wall_time(15, 30)
    if "HANG SENG" in key:
        local = instant.astimezone(HONG_KONG)
        return local.weekday() < 5 and wall_time(9, 30) <= local.time() <= wall_time(16, 0)
    if key in {"DXY", "USDINR"}:
        return instant.astimezone(NEW_YORK).weekday() < 5
    local = instant.astimezone(IST)
    if local.weekday() >= 5:
        return False
    clock = local.time()
    if "MCX" in key:
        return wall_time(9, 0) <= clock <= wall_time(23, 55)
    if "GIFT" in key:
        return clock >= wall_time(16, 35) or clock <= wall_time(2, 45) or wall_time(6, 30) <= clock <= wall_time(15, 40)
    return wall_time(9, 15) <= clock <= wall_time(15, 30)


def classify_market_state(market: str, value: dict[str, Any] | None, *, now: datetime | None = None,
                          fresh_seconds: float = 180.0) -> dict[str, Any]:
    price = float((value or {}).get("price", (value or {}).get("last_price", 0)) or 0)
    age = _age(value, now)
    opened = exchange_is_open(market, now)
    if price <= 0:
        status = "UNAVAILABLE"
    elif not opened:
        status = "CLOSED"
    elif age is not None and age <= fresh_seconds:
        status = "LIVE"
    else:
        status = "DELAYED"
    return {"status": status, "price": price or None, "timestamp": (value or {}).get("market_timestamp") or (value or {}).get("timestamp"),
            "data_age_seconds": round(age, 1) if age is not None else None,
            "market_timestamp_available": age is not None, "exchange_open": opened,
            "feed_fresh": bool(age is not None and age <= fresh_seconds)}


def get_live_data_status(force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    with _LOCK:
        if not force and _CACHE is not None and time.monotonic() - _CACHE_AT < 55:
            return dict(_CACHE)
    nse, mcx, tvkit = NSETemporaryProvider(), MCXTemporaryProvider(), TVKitProvider()
    calls: dict[str, Callable[[], Any]] = {
        "nse": lambda: nse.get_quote("NIFTY FUT"), "mcx_gold": lambda: mcx.get_quote("MCX GOLD"),
        "mcx_silver": lambda: mcx.get_quote("MCX SILVER"), "gift": lambda: tvkit.get_quote("GIFT NIFTY"),
        "comex_gold": lambda: tvkit.get_quote("COMEX GOLD"), "comex_silver": lambda: tvkit.get_quote("COMEX SILVER"),
    }
    values: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="shivay-data-status") as pool:
        futures = {name: pool.submit(function) for name, function in calls.items()}
        for name, future in futures.items():
            try:
                values[name] = future.result(timeout=45)
            except Exception:
                values[name] = None
    result = {
        "NSE F&O": classify_market_state("NSE F&O", values["nse"]),
        "MCX GOLD": classify_market_state("MCX GOLD", values["mcx_gold"]),
        "MCX SILVER": classify_market_state("MCX SILVER", values["mcx_silver"]),
        "GIFT NIFTY": classify_market_state("GIFT NIFTY", values["gift"]),
        "COMEX GOLD": classify_market_state("COMEX GOLD", values["comex_gold"]),
        "COMEX SILVER": classify_market_state("COMEX SILVER", values["comex_silver"]), "checked_at": datetime.now(timezone.utc),
    }
    with _LOCK:
        _CACHE, _CACHE_AT = result, time.monotonic()
    return dict(result)


__all__ = ["classify_market_state", "exchange_is_open", "get_live_data_status"]
