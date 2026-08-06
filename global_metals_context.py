"""Fresh global metals/FX confirmation context; never substitutes for an MCX price."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from tvkit_provider import TVKitProvider

_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0


def _series(symbol: str) -> dict[str, Any]:
    candles = TVKitProvider().get_historical_candles(symbol, "1m", 1)
    if not candles:
        return {"available": False, "symbol": symbol}
    latest = candles[-1]
    age = max(0.0, (datetime.now(timezone.utc) - latest["timestamp"]).total_seconds())
    closes = [row["close"] for row in candles]
    baseline = closes[-4] if len(closes) >= 4 else closes[0]
    change = ((closes[-1] - baseline) / baseline) * 100 if baseline > 0 else 0.0
    direction = "BULLISH" if change > 0.015 else "BEARISH" if change < -0.015 else "SIDEWAYS"
    return {"available": True, "symbol": symbol, "price": latest["close"], "timestamp": latest["timestamp"],
            "age_seconds": round(age, 1), "fresh": age <= 180, "delayed": age > 180,
            "change_percent": round(change, 4), "direction": direction}


def get_global_metals_context(force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    with _LOCK:
        if not force and _CACHE is not None and time.monotonic() - _CACHE_AT < 55:
            return dict(_CACHE)
    symbols = ("XAUUSD", "XAGUSD", "COMEX GOLD", "COMEX SILVER", "USDINR", "DXY")
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="shivay-global-metals") as pool:
        futures = {symbol: pool.submit(_series, symbol) for symbol in symbols}
        values = {symbol: future.result(timeout=45) for symbol, future in futures.items()}

    def bias(metal: str) -> tuple[str, int]:
        primary = values["XAUUSD" if metal == "GOLD" else "XAGUSD"]
        comex = values["COMEX GOLD" if metal == "GOLD" else "COMEX SILVER"]
        score = 0
        if primary.get("fresh"):
            score += 3 if primary.get("direction") == "BULLISH" else -3 if primary.get("direction") == "BEARISH" else 0
        if comex.get("available") and float(comex.get("age_seconds", 99999)) <= 900:
            score += 2 if comex.get("direction") == "BULLISH" else -2 if comex.get("direction") == "BEARISH" else 0
        dxy = values["DXY"]
        if dxy.get("fresh"):
            score += -1 if dxy.get("direction") == "BULLISH" else 1 if dxy.get("direction") == "BEARISH" else 0
        usd = values["USDINR"]
        if usd.get("fresh"):
            score += 1 if usd.get("direction") == "BULLISH" else -1 if usd.get("direction") == "BEARISH" else 0
        direction = "BULLISH" if score >= 2 else "BEARISH" if score <= -2 else "SIDEWAYS"
        confidence = min(90, 45 + abs(score) * 8)
        if not primary.get("fresh"):
            confidence = min(confidence, 40)
        return direction, confidence

    gold_bias, gold_confidence = bias("GOLD")
    silver_bias, silver_confidence = bias("SILVER")
    result = {**values, "gold_bias": gold_bias, "gold_confidence": gold_confidence,
              "silver_bias": silver_bias, "silver_confidence": silver_confidence,
              "degraded": not values["XAUUSD"].get("fresh") or not values["XAGUSD"].get("fresh")}
    with _LOCK:
        _CACHE, _CACHE_AT = result, time.monotonic()
    return dict(result)


__all__ = ["get_global_metals_context"]
