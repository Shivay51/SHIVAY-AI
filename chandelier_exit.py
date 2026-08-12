"""Shared, candle-close Chandelier Exit confirmation and trailing logic."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import pandas as pd

import config
from indicators import atr_series


def _settings(period: int | None = None, multiplier: float | None = None) -> tuple[int, float]:
    try:
        lookback = max(2, int(period if period is not None else getattr(config, "CHANDELIER_ATR_PERIOD", 7)))
    except (TypeError, ValueError, OverflowError):
        lookback = 7
    try:
        factor = float(multiplier if multiplier is not None else getattr(config, "CHANDELIER_ATR_MULTIPLIER", 2.0))
    except (TypeError, ValueError, OverflowError):
        factor = 2.0
    if not math.isfinite(factor) or factor <= 0:
        factor = 2.0
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


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value) / (1000 if float(value) > 10_000_000_000 else 1), timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def get_timeframe_candles(market: Mapping[str, Any], minutes: int = 15) -> list[dict[str, Any]]:
    """Aggregate base candles while preserving the currently forming timeframe candle."""
    base = max(1, int(market.get("interval_minutes", 5) or 5))
    if minutes < base or minutes % base:
        return []
    factor = max(1, minutes // base)
    raw = market.get("candles")
    rows = []
    if isinstance(raw, list):
        rows = [dict(row) for row in raw if isinstance(row, Mapping)]
    if not rows:
        try:
            highs = list(market.get("high", [])); lows = list(market.get("low", [])); closes = list(market.get("close", []))
            opens = list(market.get("open", []))
            size = min(len(highs), len(lows), len(closes))
            rows = [{"open": opens[index] if index < len(opens) else (closes[index - 1] if index else closes[index]),
                     "high": highs[index], "low": lows[index], "close": closes[index],
                     "timestamp": None, "completed": True}
                    for index in range(size)]
        except (TypeError, ValueError):
            return []
    if len(rows) < factor:
        return []
    aggregated = []
    for start in range(0, len(rows), factor):
        group = rows[start:start + factor]
        if len(group) < factor and all(row.get("completed", row.get("complete", True)) is not False for row in group):
            continue
        try:
            candle = {
                "open": float(group[0].get("open", group[0]["close"])),
                "high": max(float(row["high"]) for row in group),
                "low": min(float(row["low"]) for row in group),
                "close": float(group[-1]["close"]),
                "timestamp": group[0].get("timestamp"), "timeframe": f"{minutes}m",
                "complete": len(group) == factor and all(
                    row.get("completed", row.get("complete", True)) is not False for row in group
                ),
            }
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if min(candle["open"], candle["high"], candle["low"], candle["close"]) <= 0:
            continue
        if candle["high"] < max(candle["open"], candle["low"], candle["close"]):
            continue
        if candle["low"] > min(candle["open"], candle["high"], candle["close"]):
            continue
        aggregated.append(candle)
    return aggregated


def get_completed_timeframe_candles(market: Mapping[str, Any], minutes: int = 15) -> list[dict[str, Any]]:
    """Return only fully closed timeframe candles."""
    return [candle for candle in get_timeframe_candles(market, minutes) if candle.get("complete")]


def calculate_timeframe_chandelier(market: Mapping[str, Any], minutes: int, **kwargs: Any) -> dict[str, Any]:
    high, low, close = _timeframe_ohlc(market, minutes)
    result = calculate_chandelier_exit(high, low, close, **kwargs)
    result["timeframe"] = f"{minutes}m"
    return result


def get_completed_signal_candle(market: Mapping[str, Any], minutes: int = 15) -> dict[str, Any] | None:
    """Return the latest explicitly aggregated completed candle."""
    candles = get_completed_timeframe_candles(market, minutes)
    return dict(candles[-1]) if candles else None


def evaluate_chandelier_entry_state(
    market: Mapping[str, Any], minutes: int = 15, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Confirm a closed Chandelier signal from the next candle's first live minute."""
    all_candles = get_timeframe_candles(market, minutes)
    candles = [candle for candle in all_candles if candle.get("complete")]
    live = next((candle for candle in reversed(all_candles) if not candle.get("complete")), None)
    period, multiplier = _settings()
    empty = {
        "valid": False, "confirmed": False, "status": "NO_SIGNAL", "side": None,
        "reason": "insufficient_completed_candles", "signal_candle": None,
        "confirmation_candle": None, "chandelier_level": None,
        "hard_invalidation_level": None, "timeframe": f"{minutes}m",
    }
    if len(candles) < period + 2:
        return empty

    def state(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return calculate_chandelier_exit(
            [row["high"] for row in rows], [row["low"] for row in rows], [row["close"] for row in rows],
            period=period, multiplier=multiplier,
        )

    before_signal, at_signal = state(candles[:-1]), state(candles)
    changed = (before_signal.get("valid") and at_signal.get("valid")
               and before_signal.get("direction") != at_signal.get("direction"))
    if not changed:
        return {**empty, "reason": "latest_completed_candle_has_no_chandelier_direction_change", "chandelier": at_signal}
    side = "BUY" if at_signal["direction"] > 0 else "SELL"
    signal = dict(candles[-1])
    signal["chandelier_level"] = at_signal.get("current_stop")
    base = {
        **empty, "valid": True, "status": "PENDING_CONFIRMATION", "side": side,
        "reason": "waiting_for_next_candle_first_minute", "signal_candle": signal,
        "chandelier_level": at_signal.get("current_stop"), "chandelier": at_signal,
        "hard_invalidation_level": signal["low" if side == "BUY" else "high"],
    }
    if not live:
        return base
    confirmation = dict(live)
    signal_started = _timestamp(signal.get("timestamp"))
    confirmation_started = _timestamp(confirmation.get("timestamp"))
    if signal_started is not None and confirmation_started is not None:
        seconds_after_signal_start = (confirmation_started - signal_started).total_seconds()
        if not (minutes * 60 - 5 <= seconds_after_signal_start < (minutes + 2) * 60):
            return {**base, "reason": "waiting_for_immediate_next_15m_candle"}
    live_price = market.get("price", market.get("last_price"))
    try:
        if live_price is not None and float(live_price) > 0:
            confirmation["close"] = float(live_price)
    except (TypeError, ValueError, OverflowError):
        pass
    evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    started_at = confirmation_started
    elapsed = float(market.get("confirmation_elapsed_seconds", 0) or 0)
    if started_at is not None:
        elapsed = max(elapsed, (evaluated_at - started_at).total_seconds())
    minimum = max(45, int(getattr(config, "CHANDELIER_CONFIRMATION_SECONDS", 60)))
    if elapsed < minimum:
        return {**base, "confirmation_candle": confirmation, "confirmation_elapsed_seconds": round(max(0, elapsed), 1)}
    maximum = max(minimum + 30, int(getattr(config, "CHANDELIER_CONFIRMATION_MAX_SECONDS", 180)))
    if elapsed > maximum:
        return {**base, "status": "REJECTED", "confirmation_candle": confirmation,
                "confirmation_elapsed_seconds": round(elapsed, 1),
                "reason": "confirmation_window_expired"}
    signal_close = float(signal["close"])
    atr_value = float(at_signal.get("atr") or 0)
    travel_limit = atr_value * max(0.1, float(getattr(config, "CHANDELIER_MAX_ENTRY_TRAVEL_ATR", 0.75)))
    travel = confirmation["close"] - signal_close if side == "BUY" else signal_close - confirmation["close"]
    if atr_value > 0 and travel > travel_limit:
        return {**base, "status": "REJECTED", "confirmation_candle": confirmation,
                "confirmation_elapsed_seconds": round(elapsed, 1),
                "entry_travel": round(travel, 4), "entry_travel_limit": round(travel_limit, 4),
                "reason": "entry_over_travelled"}
    invalidation = float(base["hard_invalidation_level"])
    breached = confirmation["close"] <= invalidation if side == "BUY" else confirmation["close"] >= invalidation
    if breached:
        return {**base, "status": "REJECTED", "confirmation_candle": confirmation,
                "confirmation_elapsed_seconds": round(elapsed, 1),
                "reason": "signal_candle_invalidated"}
    favourable = confirmation["close"] > confirmation["open"] if side == "BUY" else confirmation["close"] < confirmation["open"]
    confirmed = bool(favourable)
    return {
        **base, "confirmed": confirmed,
        "status": "CONFIRMED" if confirmed else "REJECTED", "side": side,
        "reason": "next_candle_first_minute_confirmed" if confirmed else "next_candle_first_minute_moved_against_signal",
        "entry_travel": round(travel, 4), "entry_travel_limit": round(travel_limit, 4),
        "minimum_hold_minutes": int(getattr(config, "MIN_HOLD_MINUTES", 10)),
        "signal_candle": signal, "confirmation_candle": confirmation,
        "confirmation_elapsed_seconds": round(elapsed, 1), "evaluated_at": evaluated_at,
    }


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
