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


def _session_aware_close(market: Mapping[str, Any], minutes: int) -> pd.Series | None:
    """Aggregate timestamped candles into true higher-timeframe closes.

    Positional resampling silently merges bars across session and day
    boundaries, which distorts higher-timeframe direction after every gap.
    When the payload carries timestamped candles (Angel One always does) the
    closes are bucketed by wall-clock time within each trading day instead.
    """
    candles = market.get("candles")
    if not isinstance(candles, (list, tuple)) or len(candles) < 40:
        return None
    stamps: list[Any] = []
    closes: list[float] = []
    for candle in candles:
        if not isinstance(candle, Mapping):
            return None
        stamp = candle.get("timestamp")
        value = candle.get("close")
        if stamp is None or value is None:
            return None
        stamps.append(stamp)
        closes.append(float(value))
    try:
        index = pd.to_datetime(pd.Series(stamps), utc=True, errors="coerce")
    except (TypeError, ValueError):
        return None
    if index.isna().any():
        return None
    frame = pd.Series(closes, index=pd.DatetimeIndex(index)).sort_index()
    if minutes <= 1:
        return frame.reset_index(drop=True)
    # label the last close of each bucket, dropping empty buckets (gaps/holidays)
    aggregated = frame.resample(f"{int(minutes)}min", label="right", closed="right").last().dropna()
    if len(aggregated) < 20:
        return None
    return aggregated.reset_index(drop=True)


def analyze_timeframes(market: Mapping[str, Any]) -> dict[str, Any]:
    close = pd.Series(market.get("close", []), dtype="float64").dropna().reset_index(drop=True)
    base = int(market.get("interval_minutes", 5) or 5)
    if len(close) < 40 or base > 15:
        return {"5m": "UNKNOWN", "15m": "UNKNOWN", "30m": "UNKNOWN", "60m": "UNKNOWN", "aligned": False, "entry_timing": False}
    factors = {minutes: max(1, minutes // base) for minutes in (5, 15, 30, 60)}
    result: dict[str, Any] = {}
    for minutes, factor in factors.items():
        if minutes < base:
            result[f"{minutes}m"] = "UNKNOWN"
            continue
        series = _session_aware_close(market, minutes)
        if series is None:
            series = _resample(close, factor)
        result[f"{minutes}m"] = _direction(series)
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
