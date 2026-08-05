"""Shared, candle-close Chandelier Exit confirmation and trailing logic."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import pandas as pd

import config
from indicators import atr_series


def _settings(period: int | None = None, multiplier: float | None = None) -> tuple[int, float]:
    try:
        lookback = max(2, int(period if period is not None else getattr(config, "CHANDELIER_ATR_PERIOD", 22)))
    except (TypeError, ValueError, OverflowError):
        lookback = 22
    try:
        factor = float(multiplier if multiplier is not None else getattr(config, "CHANDELIER_ATR_MULTIPLIER", 3.0))
    except (TypeError, ValueError, OverflowError):
        factor = 3.0
    if not math.isfinite(factor) or factor <= 0:
        factor = 3.0
    return min(lookback, 500), min(factor, 20.0)


def _empty(period: int, multiplier: float, reason: str) -> dict[str, Any]:
    return {
        "valid": False, "reason": reason, "trend": "UNKNOWN", "direction": 0,
        "long_stop": None, "short_stop": None, "previous_stop": None,
        "current_stop": None, "stop_changed": False, "atr": None,
        "atr_period": period, "atr_multiplier": multiplier, "completed_candles": 0,
    }


def calculate_chandelier_exit(
    high: Sequence[Any], low: Sequence[Any], close: Sequence[Any],
    period: int | None = None, multiplier: float | None = None,
) -> dict[str, Any]:
    """Calculate a close-confirmed Chandelier state without inventing values."""
    lookback, factor = _settings(period, multiplier)
    frame = pd.DataFrame({"high": high, "low": low, "close": close}, dtype="float64").replace([math.inf, -math.inf], pd.NA).dropna()
    if len(frame) < lookback + 2:
        return _empty(lookback, factor, "insufficient_data")
    if ((frame[["high", "low", "close"]] <= 0).any().any()
            or (frame["high"] < frame[["low", "close"]].max(axis=1)).any()
            or (frame["low"] > frame[["high", "close"]].min(axis=1)).any()):
        return _empty(lookback, factor, "invalid_ohlc")

    atr_values = atr_series(frame["high"], frame["low"], frame["close"], lookback)
    raw_long = frame["high"].rolling(lookback, min_periods=lookback).max() - factor * atr_values
    raw_short = frame["low"].rolling(lookback, min_periods=lookback).min() + factor * atr_values
    direction = 0
    long_stops: list[float | None] = [None] * len(frame)
    short_stops: list[float | None] = [None] * len(frame)
    active_stops: list[float | None] = [None] * len(frame)
    for index in range(lookback - 1, len(frame)):
        long_value, short_value = float(raw_long.iloc[index]), float(raw_short.iloc[index])
        if not (math.isfinite(long_value) and math.isfinite(short_value) and long_value > 0 and short_value > 0):
            continue
        close_value = float(frame["close"].iloc[index])
        if direction == 0:
            direction = 1 if close_value >= (long_value + short_value) / 2.0 else -1
        elif direction > 0:
            previous = active_stops[index - 1]
            long_value = max(long_value, float(previous)) if previous else long_value
            if close_value < long_value:
                direction = -1
        else:
            previous = active_stops[index - 1]
            short_value = min(short_value, float(previous)) if previous else short_value
            if close_value > short_value:
                direction = 1
        long_stops[index], short_stops[index] = long_value, short_value
        active_stops[index] = long_value if direction > 0 else short_value

    current = active_stops[-1]
    if current is None or not math.isfinite(current) or current <= 0:
        return _empty(lookback, factor, "indicator_unavailable")
    previous = next((value for value in reversed(active_stops[:-1]) if value is not None), None)
    latest_atr = float(atr_values.dropna().iloc[-1]) if not atr_values.dropna().empty else 0.0
    if not math.isfinite(latest_atr) or latest_atr <= 0:
        return _empty(lookback, factor, "invalid_atr")
    return {
        "valid": True, "reason": None, "trend": "BULLISH" if direction > 0 else "BEARISH",
        "direction": direction, "long_stop": round(float(long_stops[-1]), 4),
        "short_stop": round(float(short_stops[-1]), 4),
        "previous_stop": round(float(previous), 4) if previous else None,
        "current_stop": round(float(current), 4),
        "stop_changed": previous is not None and not math.isclose(float(previous), float(current), rel_tol=1e-9, abs_tol=1e-9),
        "atr": round(latest_atr, 6), "atr_period": lookback, "atr_multiplier": factor,
        "completed_candles": len(frame), "latest_close": round(float(frame["close"].iloc[-1]), 4),
    }


def _timeframe_ohlc(market: Mapping[str, Any], minutes: int) -> tuple[list[float], list[float], list[float]]:
    base = max(1, int(market.get("interval_minutes", 5) or 5))
    if minutes < base or minutes % base:
        return [], [], []
    factor = max(1, minutes // base)
    frame = pd.DataFrame({"high": market.get("high", []), "low": market.get("low", []), "close": market.get("close", [])}, dtype="float64").dropna()
    usable = len(frame) - (len(frame) % factor)
    if usable <= 0:
        return [], [], []
    frame = frame.iloc[:usable]
    group = pd.Series(range(usable), index=frame.index) // factor
    return (frame["high"].groupby(group).max().tolist(), frame["low"].groupby(group).min().tolist(), frame["close"].groupby(group).last().tolist())


def calculate_timeframe_chandelier(market: Mapping[str, Any], minutes: int, **kwargs: Any) -> dict[str, Any]:
    high, low, close = _timeframe_ohlc(market, minutes)
    result = calculate_chandelier_exit(high, low, close, **kwargs)
    result["timeframe"] = f"{minutes}m"
    return result


def get_completed_signal_candle(market: Mapping[str, Any], minutes: int = 15) -> dict[str, Any] | None:
    """Return the latest explicitly aggregated completed candle."""
    base = max(1, int(market.get("interval_minutes", 5) or 5))
    if minutes < base or minutes % base:
        return None
    factor = max(1, minutes // base)
    candles = market.get("candles")
    if isinstance(candles, list) and candles:
        rows = [row for row in candles if isinstance(row, Mapping)]
        if rows and rows[-1].get("complete") is False:
            rows = rows[:-1]
        usable = len(rows) - (len(rows) % factor)
        if usable < factor:
            return None
        group = rows[usable - factor:usable]
        try:
            high = max(float(row["high"]) for row in group)
            low = min(float(row["low"]) for row in group)
            close = float(group[-1]["close"])
            opened = float(group[0].get("open", group[0]["close"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if min(high, low, close, opened) <= 0 or high < max(low, close, opened) or low > min(high, close, opened):
            return None
        return {"open": opened, "high": high, "low": low, "close": close,
                "timestamp": group[-1].get("timestamp"), "timeframe": f"{minutes}m", "complete": True}
    high, low, close = _timeframe_ohlc(market, minutes)
    if not close:
        return None
    return {"open": close[-2] if len(close) > 1 else close[-1], "high": high[-1], "low": low[-1],
            "close": close[-1], "timestamp": market.get("timestamp"), "timeframe": f"{minutes}m", "complete": True}


def get_chandelier_trend(*args: Any, **kwargs: Any) -> str:
    return str(calculate_chandelier_exit(*args, **kwargs)["trend"])


def get_long_chandelier_stop(*args: Any, **kwargs: Any) -> float | None:
    return calculate_chandelier_exit(*args, **kwargs)["long_stop"]


def get_short_chandelier_stop(*args: Any, **kwargs: Any) -> float | None:
    return calculate_chandelier_exit(*args, **kwargs)["short_stop"]


def is_chandelier_buy_confirmed(result: Mapping[str, Any], close: float | None = None) -> bool:
    value = float(close if close is not None else result.get("latest_close", 0) or 0)
    stop = float(result.get("long_stop") or 0)
    return bool(result.get("valid") and result.get("trend") == "BULLISH" and value > stop > 0)


def is_chandelier_sell_confirmed(result: Mapping[str, Any], close: float | None = None) -> bool:
    value = float(close if close is not None else result.get("latest_close", 0) or 0)
    stop = float(result.get("short_stop") or 0)
    return bool(result.get("valid") and result.get("trend") == "BEARISH" and 0 < value < stop)


def update_chandelier_trailing_stop(side: str, current_stop: float, chandelier_stop: float) -> float:
    """Tighten only: BUY stops rise, SELL stops fall."""
    side = str(side).upper()
    try:
        current, candidate = float(current_stop), float(chandelier_stop)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if side not in {"BUY", "SELL"} or min(current, candidate) <= 0 or not all(map(math.isfinite, (current, candidate))):
        return 0.0
    return round(max(current, candidate) if side == "BUY" else min(current, candidate), 4)
