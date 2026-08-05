"""Deterministic 5/15/30/60-minute analysis using one cached candle stream."""
from __future__ import annotations

from typing import Any, Mapping

import pandas as pd


def _direction(close: pd.Series) -> str:
    values = close.dropna().astype("float64")
    if len(values) < 20:
        return "UNKNOWN"
    fast = values.ewm(span=9, adjust=False, min_periods=9).mean().iloc[-1]
    slow = values.ewm(span=20, adjust=False, min_periods=20).mean().iloc[-1]
    slope = float(values.iloc[-1] - values.iloc[-3]) if len(values) >= 3 else 0.0
    return "BULLISH" if values.iloc[-1] > fast > slow and slope > 0 else "BEARISH" if values.iloc[-1] < fast < slow and slope < 0 else "SIDEWAYS"


def _resample(close: pd.Series, factor: int) -> pd.Series:
    if factor <= 1:
        return close
    return close.iloc[factor - 1 :: factor].reset_index(drop=True)


def analyze_timeframes(market: Mapping[str, Any]) -> dict[str, Any]:
    close = pd.Series(market.get("close", []), dtype="float64").dropna().reset_index(drop=True)
    base = int(market.get("interval_minutes", 5) or 5)
    if len(close) < 40 or base > 15:
        return {"5m": "UNKNOWN", "15m": "UNKNOWN", "30m": "UNKNOWN", "60m": "UNKNOWN", "aligned": False, "entry_timing": False}
    factors = {minutes: max(1, minutes // base) for minutes in (5, 15, 30, 60)}
    result = {f"{minutes}m": _direction(_resample(close, factor)) if minutes >= base else "UNKNOWN" for minutes, factor in factors.items()}
    directional = result["60m"] in {"BULLISH", "BEARISH"}
    result["aligned"] = directional and result["30m"] == result["60m"] and result["15m"] == result["60m"]
    result["entry_timing"] = result["5m"] in {result["15m"], "SIDEWAYS"} if result["5m"] != "UNKNOWN" else False
    return result


def timeframe_trend(symbol):
    """Legacy boolean interface: true only for fully aligned bullish timeframes."""
    try:
        from data import get_market_data
        result = analyze_timeframes(get_market_data(symbol) or {})
        return bool(result["aligned"] and result["60m"] == "BULLISH" and result["entry_timing"])
    except Exception:
        return False
