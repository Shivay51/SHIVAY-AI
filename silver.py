"""Silver analysis and balanced BUY/SELL signal generation for SHIVAY AI."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import logging
import math
import os
import threading
import time
from copy import deepcopy
from datetime import date, datetime, time as clock_time, timedelta
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import config
from audience_router import recipients
from multitimeframe import analyze_timeframes
from chandelier_exit import calculate_timeframe_chandelier, evaluate_chandelier_entry_state


LOGGER = logging.getLogger("shivay.silver")
IST = ZoneInfo("Asia/Kolkata")
SILVER_SYMBOL = str(getattr(config, "SILVER_SYMBOL", "SILVER FUT")).strip() or "SILVER FUT"
YAHOO_FALLBACK_SYMBOL = str(getattr(config, "SILVER_YAHOO_SYMBOL", "SI=F")).strip() or "SI=F"
CACHE_SECONDS = max(180, int(getattr(config, "SILVER_CACHE_SECONDS", 300)))
STALE_MINUTES = max(20, int(getattr(config, "SILVER_STALE_MINUTES", 90)))
DATA_RETRIES = max(1, min(int(getattr(config, "SILVER_DATA_RETRIES", 2)), 3))
RETRY_DELAY = max(0.0, min(float(getattr(config, "SILVER_RETRY_DELAY", 0.5)), 2.0))
MCX_OPEN = clock_time(
    int(getattr(config, "MCX_START_HOUR", 9)),
    int(getattr(config, "MCX_START_MINUTE", 0)),
)
MCX_CLOSE = clock_time(
    int(getattr(config, "MCX_END_HOUR", 23)),
    int(getattr(config, "MCX_END_MINUTE", 30)),
)
_CACHE_LOCK = threading.RLock()
_SCAN_LOCK = threading.Lock()
_analysis_cache: dict[str, Any] | None = None
_analysis_cache_time = 0.0
_analysis_cache_day: date | None = None
_sent_state: dict[date, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(IST)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _market_open(now: datetime) -> bool:
    return now.weekday() < 5 and MCX_OPEN <= now.time() <= MCX_CLOSE


def _direction(value: Any) -> str:
    text = str(value or "").upper()
    if any(item in text for item in ("BULL", "BUY", "UP", "POSITIVE", "LONG")):
        return "BULLISH"
    if any(item in text for item in ("BEAR", "SELL", "DOWN", "NEGATIVE", "SHORT")):
        return "BEARISH"
    if any(item in text for item in ("FLAT", "SIDEWAYS", "NEUTRAL", "NO TRADE")):
        return "NEUTRAL"
    return "UNAVAILABLE"


def _ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    multiplier = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append((float(value) - result[-1]) * multiplier + result[-1])
    return result


def _rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-(period + 1):-1], values[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative))


def _true_ranges(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    ranges = []
    for index in range(len(close)):
        previous = close[index - 1] if index else close[index]
        ranges.append(max(high[index] - low[index], abs(high[index] - previous), abs(low[index] - previous)))
    return ranges


def _atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14) -> float:
    ranges = _true_ranges(high, low, close)
    return sum(ranges[-period:]) / period if len(ranges) >= period else 0.0


def _adx(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14) -> float:
    if len(close) < period * 2 + 1:
        return 0.0
    tr = _true_ranges(high, low, close)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for index in range(1, len(close)):
        upward = high[index] - high[index - 1]
        downward = low[index - 1] - low[index]
        plus_dm.append(upward if upward > downward and upward > 0 else 0.0)
        minus_dm.append(downward if downward > upward and downward > 0 else 0.0)
    dx_values = []
    start = len(close) - period
    for end in range(start, len(close)):
        begin = max(1, end - period + 1)
        tr_sum = sum(tr[begin:end + 1])
        if tr_sum <= 0:
            continue
        plus_di = 100.0 * sum(plus_dm[begin:end + 1]) / tr_sum
        minus_di = 100.0 * sum(minus_dm[begin:end + 1]) / tr_sum
        denominator = plus_di + minus_di
        if denominator > 0:
            dx_values.append(100.0 * abs(plus_di - minus_di) / denominator)
    return sum(dx_values) / len(dx_values) if dx_values else 0.0


def _macd(values: Sequence[float]) -> tuple[float, float, bool]:
    if len(values) < 35:
        return 0.0, 0.0, False
    fast = _ema_series(values, 12)
    slow = _ema_series(values, 26)
    line = [left - right for left, right in zip(fast, slow)]
    signal = _ema_series(line, 9)
    return line[-1], signal[-1], line[-1] > signal[-1]


def _supertrend(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 10, multiplier: float = 3.0) -> bool:
    if len(close) < period + 2:
        return False
    ranges = _true_ranges(high, low, close)
    atr_values = []
    for index in range(len(close)):
        atr_values.append(sum(ranges[max(0, index - period + 1):index + 1]) / min(index + 1, period))
    upper = [(high[i] + low[i]) / 2.0 + multiplier * atr_values[i] for i in range(len(close))]
    lower = [(high[i] + low[i]) / 2.0 - multiplier * atr_values[i] for i in range(len(close))]
    final_upper, final_lower = upper[0], lower[0]
    trend_up = True
    for index in range(1, len(close)):
        final_upper = upper[index] if upper[index] < final_upper or close[index - 1] > final_upper else final_upper
        final_lower = lower[index] if lower[index] > final_lower or close[index - 1] < final_lower else final_lower
        if trend_up and close[index] < final_lower:
            trend_up = False
            final_upper = upper[index]
        elif not trend_up and close[index] > final_upper:
            trend_up = True
            final_lower = lower[index]
    return trend_up


def _vwap(high: Sequence[float], low: Sequence[float], close: Sequence[float], volume: Sequence[float]) -> float:
    total_volume = sum(max(value, 0.0) for value in volume)
    if total_volume <= 0:
        return 0.0
    weighted = sum(
        ((high[index] + low[index] + close[index]) / 3.0) * max(volume[index], 0.0)
        for index in range(len(close))
    )
    return weighted / total_volume


def _timestamp(value: Any) -> datetime | None:
    try:
        result = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if not isinstance(result, datetime):
            return None
        if result.tzinfo is None:
            result = result.replace(tzinfo=IST)
        return result.astimezone(IST)
    except Exception:
        return None


def _project_silver_data() -> tuple[Any, str] | tuple[None, None]:
    """Use a project-native Silver mapping when a current/future feed provides one."""
    try:
        manager = importlib.import_module("provider_manager").get_provider_manager()
        value = manager.get_verified_market_data("SILVER FUT", period="15d", interval="5m")
        verified = (
            isinstance(value, Mapping) and value.get("verified") is True
            and str(value.get("exchange", "")).upper() == "MCX"
            and str(value.get("segment", "")).upper() == "MCX_COMM"
            and str(value.get("instrument_type", "")).upper() == "FUTCOM"
            and bool(value.get("expiry")) and value.get("is_live") is True
            and not value.get("is_delayed")
        )
        return (value, "VERIFIED_MCX:SILVER") if verified else (None, None)
    except Exception:
        return None, None


def _download_yahoo_silver() -> tuple[Any, str] | tuple[None, None]:
    """Use the project's established Yahoo provider as a labeled global proxy."""
    try:
        import yfinance as yf
        from yahoo_runtime import configure_yfinance
        configure_yfinance(yf)
    except Exception:
        return None, None
    for attempt in range(1, DATA_RETRIES + 1):
        try:
            frame = yf.download(
                YAHOO_FALLBACK_SYMBOL,
                period="10d",
                interval="15m",
                auto_adjust=True,
                progress=False,
                threads=False,
                timeout=8,
            )
            if frame is not None and not frame.empty:
                if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
                    frame.columns = frame.columns.get_level_values(0)
                required = {"Open", "High", "Low", "Close", "Volume"}
                if required.issubset(set(frame.columns)):
                    return frame[list(required)].dropna(subset=["Open", "High", "Low", "Close"]), f"YAHOO_GLOBAL_FUTURES:{YAHOO_FALLBACK_SYMBOL}"
                return None, None
        except Exception:
            LOGGER.warning("Temporary Silver data failure (%s/%s)", attempt, DATA_RETRIES)
        if attempt < DATA_RETRIES and RETRY_DELAY:
            time.sleep(RETRY_DELAY * attempt)
    return None, None


def _records_from_project(market: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = {name: market.get(name) for name in ("open", "high", "low", "close", "volume")}
    if any(value is None for value in fields.values()):
        return []
    records = []
    try:
        for position, index in enumerate(fields["close"].index):
            records.append({
                "timestamp": _timestamp(index),
                **{name: _number(series.iloc[position]) for name, series in fields.items()},
            })
    except Exception:
        return []
    return records


def _records_from_frame(frame: Any) -> list[dict[str, Any]]:
    records = []
    try:
        for position, index in enumerate(frame.index):
            records.append({
                "timestamp": _timestamp(index),
                "open": _number(frame["Open"].iloc[position]),
                "high": _number(frame["High"].iloc[position]),
                "low": _number(frame["Low"].iloc[position]),
                "close": _number(frame["Close"].iloc[position]),
                "volume": max(_number(frame["Volume"].iloc[position]), 0.0),
            })
    except Exception:
        return []
    return records


def _load_records() -> tuple[list[dict[str, Any]], str | None]:
    native, source = _project_silver_data()
    if native is not None:
        records = _records_from_project(native)
        if records:
            return records, source
    frame, source = _download_yahoo_silver()
    if frame is not None:
        return _records_from_frame(frame), source
    return [], None


def _gold_confirmation() -> dict[str, Any]:
    try:
        if importlib.util.find_spec("gold") is None:
            return {"available": False, "direction": "UNAVAILABLE"}
        module = importlib.import_module("gold")
    except Exception:
        return {"available": False, "direction": "UNAVAILABLE"}
    try:
        cached = getattr(module, "_analysis_cache", None)
        if isinstance(cached, Mapping):
            raw = cached.get("trend", cached.get("signal", cached.get("direction")))
            direction = _direction(raw)
            if direction != "UNAVAILABLE" and not cached.get("stale", True):
                return {"available": True, "direction": direction, "source": "gold_cache"}
    except Exception:
        LOGGER.warning("Gold confirmation unavailable")
    return {"available": False, "direction": "UNAVAILABLE"}


def _empty_analysis(reason: str, busy: bool = False) -> dict[str, Any]:
    now = _now()
    return {
        "generated_at": now.isoformat(),
        "symbol": SILVER_SYMBOL,
        "instrument": "SILVER",
        "data_source": None,
        "data_available": False,
        "stale": True,
        "market_open": _market_open(now),
        "signal": "NO TRADE",
        "decision": "NO TRADE",
        "trend": "UNKNOWN",
        "regime": "UNKNOWN",
        "price": 0.0,
        "entry": 0.0,
        "entry_zone": (0.0, 0.0),
        "sl": 0.0,
        "target1": 0.0,
        "target2": 0.0,
        "target3": 0.0,
        "risk_reward": 0.0,
        "confidence": 0,
        "trade_quality": "NO TRADE",
        "signal_grade": "C",
        "risk_level": "HIGH",
        "tradeable": False,
        "degraded": True,
        "scan_busy": busy,
        "reasons": [reason],
        "inputs": {
            "gold": {"available": False, "direction": "UNAVAILABLE"},
            "usd": {"available": False},
            "global_markets": {"available": False},
        },
    }


def _trade_plan(price: float, atr_value: float, side: str) -> dict[str, float]:
    try:
        module = importlib.import_module("core.risk_engine")
        function = getattr(module, "calculate_risk", None)
        if callable(function):
            plan = function(price, atr_value, side)
            if isinstance(plan, Mapping):
                return {key: _number(plan.get(key)) for key in ("entry", "sl", "target1", "target2", "target3")}
    except Exception:
        LOGGER.warning("Project risk engine unavailable for Silver")
    direction = 1.0 if side == "BUY" else -1.0
    return {
        "entry": round(price, 2),
        "sl": round(price - direction * atr_value * 1.25, 2),
        "target1": round(price + direction * atr_value * 2.0, 2),
        "target2": round(price + direction * atr_value * 3.25, 2),
        "target3": round(price + direction * atr_value * 5.0, 2),
    }


def _build_analysis() -> dict[str, Any]:
    now = _now()
    records, source = _load_records()
    indian_mcx_verified = str(source or "").startswith("VERIFIED_MCX:")
    records = [item for item in records if min(item["open"], item["high"], item["low"], item["close"]) > 0]
    if len(records) < 240:
        return _empty_analysis("Silver data is unavailable or has insufficient history")
    records = records[-350:]
    latest_timestamp = records[-1].get("timestamp")
    age_minutes = (
        max((now - latest_timestamp).total_seconds() / 60.0, 0.0)
        if isinstance(latest_timestamp, datetime) else None
    )
    stale = age_minutes is None or age_minutes > STALE_MINUTES
    open_values = [item["open"] for item in records]
    high = [item["high"] for item in records]
    low = [item["low"] for item in records]
    close = [item["close"] for item in records]
    volume = [item["volume"] for item in records]
    timeframes = analyze_timeframes({"close": close, "interval_minutes": 5})
    chandelier_market = {
        "open": open_values, "high": high, "low": low, "close": close,
        "interval_minutes": 5,
        "candles": [{
            "timestamp": item.get("timestamp"), "open": item["open"], "high": item["high"],
            "low": item["low"], "close": item["close"], "volume": item.get("volume", 0.0),
            "completed": not isinstance(item.get("timestamp"), datetime)
            or item["timestamp"] + timedelta(minutes=5) <= now,
        } for item in records],
    }
    chandelier_entry = evaluate_chandelier_entry_state(chandelier_market, 15)
    chandelier_15m = calculate_timeframe_chandelier(chandelier_market, 15)
    chandelier_30m = calculate_timeframe_chandelier(chandelier_market, 30)
    chandelier_60m = calculate_timeframe_chandelier(chandelier_market, 60)
    price = close[-1]
    e20, e50, e200 = _ema(close, 20), _ema(close, 50), _ema(close, 200)
    atr_value = _atr(high, low, close)
    adx_value = _adx(high, low, close)
    rsi_value = _rsi(close)
    macd_line, macd_signal, macd_bullish = _macd(close)
    supertrend_bullish = _supertrend(high, low, close)
    session_day = latest_timestamp.date() if isinstance(latest_timestamp, datetime) else None
    session_indexes = [
        index for index, item in enumerate(records)
        if isinstance(item.get("timestamp"), datetime) and item["timestamp"].date() == session_day
    ]
    if not session_indexes:
        session_indexes = list(range(max(0, len(records) - 50), len(records)))
    prior_session_indexes = [index for index in range(session_indexes[0])]
    previous_session_close = (
        close[prior_session_indexes[-1]] if prior_session_indexes else None
    )
    current_session_open = open_values[session_indexes[0]]
    overnight_change = (
        ((current_session_open - previous_session_close) / previous_session_close) * 100.0
        if previous_session_close and previous_session_close > 0 else None
    )
    session_high = [high[index] for index in session_indexes]
    session_low = [low[index] for index in session_indexes]
    session_close = [close[index] for index in session_indexes]
    session_volume = [volume[index] for index in session_indexes]
    vwap_value = _vwap(session_high, session_low, session_close, session_volume)
    average_volume = sum(volume[-21:-1]) / 20.0
    relative_volume = volume[-1] / average_volume if average_volume > 0 else None
    support = min(low[-21:-1])
    resistance = max(high[-21:-1])
    prior_support = min(low[-41:-21])
    prior_resistance = max(high[-41:-21])
    breakout_buffer = max(atr_value * 0.10, price * 0.0004)
    buy_breakout = price > resistance + breakout_buffer
    sell_breakout = price < support - breakout_buffer
    buy_retest = low[-1] <= resistance + atr_value * 0.25 and price > resistance
    sell_retest = high[-1] >= support - atr_value * 0.25 and price < support
    buy_pullback = e20 > e50 > e200 and low[-1] <= e20 + atr_value * 0.35 and price > e20 and price > close[-2]
    sell_pullback = e20 < e50 < e200 and high[-1] >= e20 - atr_value * 0.35 and price < e20 and price < close[-2]
    candle_range = high[-1] - low[-1]
    candle_body = abs(close[-1] - open_values[-1])
    candle_strength = candle_body / candle_range if candle_range > 0 else 0.0
    upper_wick = high[-1] - max(open_values[-1], close[-1])
    lower_wick = min(open_values[-1], close[-1]) - low[-1]
    momentum_short = ((close[-1] - close[-4]) / close[-4]) * 100.0 if close[-4] > 0 else 0.0
    momentum_medium = ((close[-1] - close[-7]) / close[-7]) * 100.0 if close[-7] > 0 else 0.0
    atr_percent = (atr_value / price) * 100.0 if price > 0 else 0.0
    extension = abs(price - e20) / atr_value if atr_value > 0 else 99.0
    bullish_alignment = e20 > e50 > e200
    bearish_alignment = e20 < e50 < e200
    fake_buy_risk = buy_breakout and (upper_wick > candle_body * 1.2 or price <= resistance)
    fake_sell_risk = sell_breakout and (lower_wick > candle_body * 1.2 or price >= support)
    late_entry = extension > 1.8 or (buy_breakout and price - resistance > atr_value * 1.25) or (sell_breakout and support - price > atr_value * 1.25)
    exhausted = rsi_value >= 76 or rsi_value <= 24 or extension > 2.3
    normal_volatility = 0.08 <= atr_percent <= 2.5
    volume_confirmed = relative_volume is not None and relative_volume >= 0.80
    gold = _gold_confirmation()
    try:
        global_context = importlib.import_module("global_metals_context").get_global_metals_context()
    except Exception:
        global_context = {"silver_bias": "SIDEWAYS", "silver_confidence": 0, "degraded": True}

    buy_score = sell_score = 0.0
    reasons: list[str] = []
    if bullish_alignment:
        buy_score += 22
    elif e20 > e50 and price > e200:
        buy_score += 11
    if bearish_alignment:
        sell_score += 22
    elif e20 < e50 and price < e200:
        sell_score += 11
    if vwap_value > 0 and price > vwap_value:
        buy_score += 9
    elif vwap_value > 0 and price < vwap_value:
        sell_score += 9
    if macd_bullish:
        buy_score += 9
    else:
        sell_score += 9
    if supertrend_bullish:
        buy_score += 9
    else:
        sell_score += 9
    if 52 <= rsi_value <= 70:
        buy_score += 8
    elif 30 <= rsi_value <= 48:
        sell_score += 8
    if adx_value >= 25:
        if bullish_alignment:
            buy_score += 12
        elif bearish_alignment:
            sell_score += 12
    elif adx_value >= 18:
        if bullish_alignment:
            buy_score += 7
        elif bearish_alignment:
            sell_score += 7
    if volume_confirmed:
        if momentum_short > 0:
            buy_score += 9
        elif momentum_short < 0:
            sell_score += 9
    if buy_breakout or buy_retest:
        buy_score += 15
    elif buy_pullback:
        buy_score += 12
    if sell_breakout or sell_retest:
        sell_score += 15
    elif sell_pullback:
        sell_score += 12
    if momentum_short > 0 and momentum_medium > 0:
        buy_score += 6
    elif momentum_short < 0 and momentum_medium < 0:
        sell_score += 6
    if candle_strength >= 0.50:
        if close[-1] > open_values[-1]:
            buy_score += 5
        else:
            sell_score += 5
    if gold.get("available"):
        if gold["direction"] == "BULLISH":
            buy_score += 3
        elif gold["direction"] == "BEARISH":
            sell_score += 3
    if global_context.get("silver_bias") == "BULLISH":
        buy_score += min(10, _number(global_context.get("silver_confidence")) / 10)
    elif global_context.get("silver_bias") == "BEARISH":
        sell_score += min(10, _number(global_context.get("silver_confidence")) / 10)

    buy_score = _clamp(buy_score)
    sell_score = _clamp(sell_score)
    leading_side = str(chandelier_entry.get("side") or "NO TRADE").upper()
    leading_score = buy_score if leading_side == "BUY" else sell_score if leading_side == "SELL" else 0.0
    score_margin = abs(buy_score - sell_score)
    setup_valid = ((buy_breakout or buy_retest or buy_pullback) if leading_side == "BUY"
                   else (sell_breakout or sell_retest or sell_pullback) if leading_side == "SELL" else False)
    fake_risk = fake_buy_risk if leading_side == "BUY" else fake_sell_risk
    directional_momentum = momentum_short > 0 if leading_side == "BUY" else momentum_short < 0 if leading_side == "SELL" else False
    expected_timeframe = "BULLISH" if leading_side == "BUY" else "BEARISH" if leading_side == "SELL" else "UNAVAILABLE"
    timeframe_confirmed = bool(
        timeframes.get("60m") == expected_timeframe
        and timeframes.get("30m") == expected_timeframe
        and timeframes.get("15m") == expected_timeframe
        and timeframes.get("5m") in {expected_timeframe, "SIDEWAYS"}
    )
    chandelier_confirmed = bool(
        chandelier_entry.get("confirmed")
        and chandelier_entry.get("status") == "CONFIRMED"
        and chandelier_15m.get("trend") == expected_timeframe
        and chandelier_30m.get("trend") == expected_timeframe
        and chandelier_15m.get("valid") and chandelier_30m.get("valid")
    )
    # Global-macro directional veto: a silver trade must not run directly
    # against a fresh, confident global backdrop (XAGUSD/COMEX/DXY/USDINR).
    # Confidence only reaches the threshold when the primary feed is fresh, so a
    # stale or degraded macro read never blocks a signal.
    macro_bias = str(global_context.get("silver_bias") or "SIDEWAYS").upper()
    macro_confidence = _number(global_context.get("silver_confidence"))
    macro_opposes = (
        macro_confidence >= config.METALS_MACRO_VETO_MIN_CONFIDENCE
        and (
            (leading_side == "BUY" and macro_bias == "BEARISH")
            or (leading_side == "SELL" and macro_bias == "BULLISH")
        )
    )
    signal_valid = (
        indian_mcx_verified
        and
        leading_score >= 66
        and score_margin >= 10
        and setup_valid
        and directional_momentum
        and timeframe_confirmed
        and chandelier_confirmed
        and adx_value >= 18
        and volume_confirmed
        and normal_volatility
        and not fake_risk
        and not late_entry
        and not exhausted
        and not stale
        and not macro_opposes
        and _market_open(now)
    )
    signal = leading_side if signal_valid else "NO TRADE"
    if macro_opposes:
        reasons.append(f"Global silver macro is {macro_bias.title()} ({int(macro_confidence)}%) against a {leading_side.title()}")
    if stale:
        reasons.append("Silver quote is stale")
    if not _market_open(now):
        reasons.append("MCX session is closed")
    if not setup_valid:
        reasons.append("No fresh breakout, retest, or pullback setup")
    if not volume_confirmed:
        reasons.append("Volume confirmation is weak or unavailable")
    if adx_value < 18:
        reasons.append("Trend strength is weak")
    if fake_risk:
        reasons.append("Fake-breakout risk is elevated")
    if late_entry:
        reasons.append("Entry is late or overextended")
    if exhausted:
        reasons.append("Trend exhaustion risk is elevated")
    if not normal_volatility:
        reasons.append("Volatility is outside the normal range")
    if not timeframe_confirmed:
        reasons.append("60m, 30m, 15m and 5m timing are not aligned")
    if not chandelier_confirmed:
        reasons.append(str(chandelier_entry.get("reason") or "15m Chandelier signal and next-candle confirmation are unavailable"))
    if signal_valid:
        reasons.extend([
            f"{leading_side} trend alignment is confirmed",
            "Momentum and volume support the setup",
            "Entry timing remains within the ATR risk envelope",
        ])
    reasons = list(dict.fromkeys(reasons))
    confidence = int(round(leading_score if signal_valid else min(leading_score, 59)))
    if signal_valid and score_margin >= 20:
        confidence = min(98, confidence + 3)
    plan = _trade_plan(price, atr_value, signal) if signal_valid else {
        "entry": 0.0, "sl": 0.0, "target1": 0.0, "target2": 0.0, "target3": 0.0
    }
    if signal_valid:
        signal_candle = chandelier_entry.get("signal_candle") or {}
        hard_invalidation = _number(signal_candle.get("low" if leading_side == "BUY" else "high"))
        chandelier_stop = _number(chandelier_15m.get("current_stop"))
        if leading_side == "BUY" and 0 < chandelier_stop < plan["entry"]:
            plan["sl"] = round(max(plan["sl"], chandelier_stop), 2)
            plan["sl"] = min(plan["sl"], round(plan["entry"] - atr_value * 0.75, 2))
        elif leading_side == "SELL" and chandelier_stop > plan["entry"]:
            plan["sl"] = round(min(plan["sl"], chandelier_stop), 2)
            plan["sl"] = max(plan["sl"], round(plan["entry"] + atr_value * 0.75, 2))
        if leading_side == "BUY" and 0 < hard_invalidation < plan["entry"]:
            plan["sl"] = max(plan["sl"], hard_invalidation)
        elif leading_side == "SELL" and hard_invalidation > plan["entry"]:
            plan["sl"] = min(plan["sl"], hard_invalidation)
        adjusted_risk = abs(plan["entry"] - plan["sl"])
        direction = 1 if leading_side == "BUY" else -1
        plan["target1"], plan["target2"], plan["target3"] = (
            round(plan["entry"] + direction * adjusted_risk * multiple, 2) for multiple in (1.5, 2.25, 3.25)
        )
    entry_zone = (
        (round(price - atr_value * 0.15, 2), round(price + atr_value * 0.15, 2))
        if signal_valid else (0.0, 0.0)
    )
    risk = abs(plan["entry"] - plan["sl"])
    reward = abs(plan["target2"] - plan["entry"])
    risk_reward = reward / risk if risk > 0 else 0.0
    if confidence >= 88:
        quality, grade = "HIGH", "A"
    elif confidence >= 78:
        quality, grade = "GOOD", "B+"
    elif signal_valid:
        quality, grade = "MODERATE", "B"
    else:
        quality, grade = "NO TRADE", "C"
    risk_level = "LOW" if signal_valid and confidence >= 85 and score_margin >= 18 else "MODERATE" if signal_valid else "HIGH"
    trend = "BULLISH" if bullish_alignment else "BEARISH" if bearish_alignment else "SIDEWAYS"
    regime = (
        "STRONG BULL" if trend == "BULLISH" and adx_value >= 30
        else "BULL" if trend == "BULLISH"
        else "STRONG BEAR" if trend == "BEARISH" and adx_value >= 30
        else "BEAR" if trend == "BEARISH"
        else "SIDEWAYS"
    )
    return {
        "generated_at": now.isoformat(),
        "symbol": SILVER_SYMBOL,
        "instrument": "SILVER",
        "data_source": source,
        "indian_mcx_verified": indian_mcx_verified,
        "global_proxy": None if indian_mcx_verified else "COMEX Silver",
        "data_available": True,
        "last_data_time": latest_timestamp.isoformat() if isinstance(latest_timestamp, datetime) else None,
        "data_age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "stale": stale,
        "market_open": _market_open(now),
        "signal": signal,
        "decision": signal,
        "trend": trend,
        "regime": regime,
        "price": round(price, 2),
        **plan,
        "entry_zone": entry_zone,
        "risk_reward": round(risk_reward, 2),
        "confidence": confidence,
        "buy_score": round(buy_score, 1),
        "sell_score": round(sell_score, 1),
        "trade_quality": quality,
        "signal_grade": grade,
        "risk_level": risk_level,
        "tradeable": signal_valid,
        "timeframes": timeframes,
        "chandelier": {"15m": chandelier_15m, "30m": chandelier_30m, "60m": chandelier_60m},
        "chandelier_entry_state": chandelier_entry,
        "signal_candle": chandelier_entry.get("signal_candle"),
        "confirmation_candle": chandelier_entry.get("confirmation_candle"),
        "hard_invalidation_level": chandelier_entry.get("hard_invalidation_level"),
        "entry_confirmed": signal_valid,
        "entry_trigger_status": "CONFIRMED" if signal_valid else chandelier_entry.get("status", "INACTIVE"),
        "degraded": stale,
        "reasons": reasons,
        "indicators": {
            "ema20": round(e20, 2), "ema50": round(e50, 2), "ema200": round(e200, 2),
            "vwap": round(vwap_value, 2), "rsi": round(rsi_value, 2), "adx": round(adx_value, 2),
            "macd": round(macd_line, 4), "macd_signal": round(macd_signal, 4),
            "macd_bullish": macd_bullish, "supertrend_bullish": supertrend_bullish,
            "atr": round(atr_value, 4), "atr_percent": round(atr_percent, 3),
            "relative_volume": round(relative_volume, 2) if relative_volume is not None else None,
            "support": round(support, 2), "resistance": round(resistance, 2),
            "prior_support": round(prior_support, 2), "prior_resistance": round(prior_resistance, 2),
            "candle_strength": round(candle_strength, 2),
            "momentum_short_percent": round(momentum_short, 3),
            "momentum_medium_percent": round(momentum_medium, 3),
        },
        "setups": {
            "breakout": buy_breakout if leading_side == "BUY" else sell_breakout,
            "breakout_retest": buy_retest if leading_side == "BUY" else sell_retest,
            "pullback": buy_pullback if leading_side == "BUY" else sell_pullback,
            "fake_breakout_risk": fake_risk,
            "late_entry_risk": late_entry,
            "trend_exhaustion": exhausted,
        },
        "inputs": {
            "gold": gold,
            "overnight_silver_movement": {
                "available": overnight_change is not None,
                "change_percent": round(overnight_change, 3) if overnight_change is not None else None,
                "direction": _direction(
                    "UP" if overnight_change is not None and overnight_change > 0.03
                    else "DOWN" if overnight_change is not None and overnight_change < -0.03
                    else "FLAT" if overnight_change is not None
                    else "UNAVAILABLE"
                ),
            },
            "usd": {"available": False, "reason": "No project USD data source configured"},
            "global_markets": {"available": False, "reason": "No project global-market data source configured"},
        },
    }


def analyze_silver(force_refresh: bool = False) -> dict[str, Any]:
    """Generate a cached Silver analysis without blocking concurrent scans."""
    global _analysis_cache
    global _analysis_cache_time
    global _analysis_cache_day
    now = _now()
    monotonic_now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force_refresh
            and _analysis_cache is not None
            and _analysis_cache_day == now.date()
            and monotonic_now - _analysis_cache_time < CACHE_SECONDS
        ):
            return deepcopy(_analysis_cache)
        quote = None
        try:
            quote = importlib.import_module("mcx_temporary_provider").MCXTemporaryProvider().get_quote("MCX SILVER")
        except Exception:
            pass
        authenticated_mcx = any(os.getenv(name, "").strip() for name in (
            "TVKIT_AUTH_TOKEN", "GROWW_ACCESS_TOKEN", "UPSTOX_ACCESS_TOKEN", "DHAN_ACCESS_TOKEN",
            "SHOONYA_API_KEY", "TRUEDATA_USERNAME", "GDFL_API_KEY",
        ))
        try:
            result = _build_analysis() if authenticated_mcx or not quote else _empty_analysis("Verified MCX candles are unavailable")
        except Exception:
            LOGGER.exception("Silver analysis degraded safely")
            result = _empty_analysis("Silver analysis failed because one or more inputs are unavailable")
        if not result.get("data_available"):
            try:
                if quote and _number(quote.get("price")) > 0:
                    try:
                        context = importlib.import_module("global_metals_context").get_global_metals_context()
                    except Exception:
                        context = {"silver_bias": "SIDEWAYS"}
                    result.update(data_available=True, indian_mcx_quote=True, price=_number(quote["price"]),
                                  data_source="TradingView MCX scanner", last_data_time=quote.get("retrieved_at"),
                                  trading_symbol=quote.get("trading_symbol"), expiry=quote.get("expiry"),
                                  volume=quote.get("volume"), open_interest=quote.get("open_interest"),
                                  stale=False, trend=context.get("silver_bias", "SIDEWAYS"), regime=context.get("silver_bias", "SIDEWAYS"), degraded=True,
                                  global_confirmation=context,
                                  reasons=["Live MCX quote received; exchange timestamp and candles are unavailable"])
            except Exception:
                LOGGER.warning("Silver quote-only fallback unavailable")
        _analysis_cache = result
        _analysis_cache_time = monotonic_now
        _analysis_cache_day = now.date()
        return deepcopy(result)


def scan_silver(force_refresh: bool = False) -> dict[str, Any]:
    """Run one non-overlapping Silver scan."""
    if not _SCAN_LOCK.acquire(blocking=False):
        return _empty_analysis("Another Silver scan is already running", busy=True)
    try:
        return analyze_silver(force_refresh)
    finally:
        _SCAN_LOCK.release()


def get_silver_signal() -> str:
    return str(analyze_silver().get("signal", "NO TRADE"))


def get_silver_trend() -> str:
    return str(analyze_silver().get("trend", "UNKNOWN"))


def get_silver_regime() -> str:
    return str(analyze_silver().get("regime", "UNKNOWN"))


def get_silver_confidence() -> int:
    return int(_number(analyze_silver().get("confidence")))


def get_silver_trade_plan() -> dict[str, Any]:
    result = analyze_silver()
    return {
        "signal": result.get("signal", "NO TRADE"),
        "entry": result.get("entry", 0.0),
        "entry_zone": result.get("entry_zone", (0.0, 0.0)),
        "sl": result.get("sl", 0.0),
        "target1": result.get("target1", 0.0),
        "target2": result.get("target2", 0.0),
        "target3": result.get("target3", 0.0),
        "risk_reward": result.get("risk_reward", 0.0),
    }


def is_silver_tradeable() -> bool:
    return bool(analyze_silver().get("tradeable", False))


def _format_silver_report_legacy(analysis: Mapping[str, Any] | None = None) -> str:
    data = dict(analysis or analyze_silver())
    indicators = data.get("indicators", {}) if isinstance(data.get("indicators"), Mapping) else {}
    reasons = data.get("reasons", []) if isinstance(data.get("reasons"), list) else []
    reason_text = "\n".join(f"â€¢ {str(reason)[:180]}" for reason in reasons[:6]) or "â€¢ No additional detail"
    freshness = "STALE" if data.get("stale") else "LIVE/CURRENT" if data.get("data_available") else "UNAVAILABLE"
    return (
        "ðŸŸ¨ SHIVAY AI â€” SILVER ANALYSIS\n\n"
        f"Signal: {data.get('signal', 'NO TRADE')}\n"
        f"Trend: {data.get('trend', 'UNKNOWN')} | Regime: {data.get('regime', 'UNKNOWN')}\n"
        f"Confidence: {data.get('confidence', 0)}/100 | Grade: {data.get('signal_grade', 'C')}\n"
        f"Quality: {data.get('trade_quality', 'NO TRADE')} | Risk: {data.get('risk_level', 'HIGH')}\n\n"
        f"Price: {data.get('price', 0)}\n"
        f"Entry Zone: {data.get('entry_zone', (0, 0))[0]} â€” {data.get('entry_zone', (0, 0))[1]}\n"
        f"Stop Loss: {data.get('sl', 0)}\n"
        f"Targets: {data.get('target1', 0)} / {data.get('target2', 0)} / {data.get('target3', 0)}\n"
        f"Risk:Reward: 1:{data.get('risk_reward', 0)}\n\n"
        "INDICATORS\n"
        f"EMA 20/50/200: {indicators.get('ema20', 'N/A')} / {indicators.get('ema50', 'N/A')} / {indicators.get('ema200', 'N/A')}\n"
        f"RSI: {indicators.get('rsi', 'N/A')} | ADX: {indicators.get('adx', 'N/A')} | ATR: {indicators.get('atr', 'N/A')}\n"
        f"VWAP: {indicators.get('vwap', 'N/A')} | Relative Volume: {indicators.get('relative_volume', 'N/A')}\n\n"
        f"DATA: {freshness}\n"
        f"MCX Session: {'OPEN' if data.get('market_open') else 'CLOSED'}\n\n"
        f"REASONS\n{reason_text}\n\n"
        "Silver signals are probability-based, not guaranteed. Unverified values never authorize a trade."
    )


def format_silver_details(analysis: Mapping[str, Any] | None = None) -> str:
    data = dict(analysis or analyze_silver())
    indicators = data.get("indicators", {}) if isinstance(data.get("indicators"), Mapping) else {}
    reasons = data.get("reasons", []) if isinstance(data.get("reasons"), list) else []
    reason_text = "\n".join(f"- {str(reason)[:180]}" for reason in reasons[:6]) or "- No additional detail"
    chandelier = data.get("chandelier", {}) if isinstance(data.get("chandelier"), Mapping) else {}
    chandelier_15 = chandelier.get("15m", {}) if isinstance(chandelier.get("15m"), Mapping) else {}
    chandelier_30 = chandelier.get("30m", {}) if isinstance(chandelier.get("30m"), Mapping) else {}
    freshness = "STALE" if data.get("stale") else "LIVE/CURRENT" if data.get("data_available") else "UNAVAILABLE"
    zone = data.get("entry_zone", (0, 0))
    if not isinstance(zone, (tuple, list)) or len(zone) < 2:
        zone = (0, 0)
    return (
        "SHIVAY AI - SILVER ANALYSIS\n\n"
        f"Signal: {data.get('signal', 'NO TRADE')}\n"
        f"Trend: {data.get('trend', 'UNKNOWN')} | Regime: {data.get('regime', 'UNKNOWN')}\n"
        f"Confidence: {data.get('confidence', 0)}/100 | Grade: {data.get('signal_grade', 'C')}\n"
        f"Quality: {data.get('trade_quality', 'NO TRADE')} | Risk: {data.get('risk_level', 'HIGH')}\n\n"
        f"Price: {data.get('price', 0)}\n"
        f"Entry Zone: {zone[0]} - {zone[1]}\n"
        f"Stop Loss: {data.get('sl', 0)}\n"
        f"Targets: {data.get('target1', 0)} / {data.get('target2', 0)} / {data.get('target3', 0)}\n"
        f"Risk:Reward: 1:{data.get('risk_reward', 0)}\n\n"
        "INDICATORS\n"
        f"EMA 20/50/200: {indicators.get('ema20', 'N/A')} / {indicators.get('ema50', 'N/A')} / {indicators.get('ema200', 'N/A')}\n"
        f"RSI: {indicators.get('rsi', 'N/A')} | ADX: {indicators.get('adx', 'N/A')} | ATR: {indicators.get('atr', 'N/A')}\n"
        f"VWAP: {indicators.get('vwap', 'N/A')} | Relative Volume: {indicators.get('relative_volume', 'N/A')}\n\n"
        f"CHANDELIER 15m: {chandelier_15.get('trend', 'UNAVAILABLE')} | Stop: {chandelier_15.get('current_stop', 'N/A')}\n"
        f"CHANDELIER 30m: {chandelier_30.get('trend', 'UNAVAILABLE')} | ATR: {chandelier_30.get('atr_period', 7)} x {chandelier_30.get('atr_multiplier', 2.0)}\n"
        f"Entry Trigger: {data.get('entry_trigger_status', 'INACTIVE')}\n\n"
        f"DATA: {freshness}\n"
        f"MCX Session: {'OPEN' if data.get('market_open') else 'CLOSED'}\n\n"
        f"REASONS\n{reason_text}\n\n"
        "Silver signals are probability-based, not guaranteed. Unverified values never authorize a trade."
    )


def format_silver_report(analysis: Mapping[str, Any] | None = None) -> str:
    from telegram_service import format_commodity_signal
    return format_commodity_signal(dict(analysis or analyze_silver()), "SILVER")


def _meaningful_change(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool:
    if not previous:
        return True
    for field in ("signal", "trend", "regime", "risk_level", "stale"):
        if previous.get(field) != current.get(field):
            return True
    if abs(_number(current.get("confidence")) - _number(previous.get("confidence"))) >= 8:
        return True
    previous_price = _number(previous.get("price"))
    current_price = _number(current.get("price"))
    return previous_price > 0 and abs(current_price - previous_price) / previous_price >= 0.003


def _monitor_supported(symbol: str) -> bool:
    try:
        module = importlib.import_module("data")
        return symbol in getattr(module, "SYMBOLS", {})
    except Exception:
        return False


async def send_silver_report(
    application: Any,
    analysis: Mapping[str, Any] | None = None,
    force_refresh: bool = False,
) -> bool:
    """Send a deduplicated professional report and register monitorable trades."""
    if application is None or not hasattr(application, "bot"):
        return False
    data = dict(analysis) if analysis is not None else await asyncio.to_thread(scan_silver, force_refresh)
    today = _now().date()
    with _CACHE_LOCK:
        state = dict(_sent_state.get(today, {"count": 0, "analysis": None, "in_flight": False}))
        if state.get("in_flight") or state["count"] >= 2:
            return False
        if state["count"] >= 1 and not _meaningful_change(state.get("analysis"), data):
            return False
        state["in_flight"] = True
        _sent_state[today] = state
    try:
        users = await asyncio.to_thread(recipients, "MCX")
    except Exception:
        LOGGER.exception("Silver-report recipients are unavailable")
        with _CACHE_LOCK:
            _sent_state[today]["in_flight"] = False
        return False
    text = format_silver_report(data)
    delivered = False
    for user in users:
        try:
            await application.bot.send_message(chat_id=int(user["id"]), text=text)
            delivered = True
        except asyncio.CancelledError:
            with _CACHE_LOCK:
                _sent_state[today]["in_flight"] = False
            raise
        except Exception:
            LOGGER.warning("Silver report delivery failed")
    if delivered and data.get("tradeable") and _monitor_supported(str(data.get("symbol"))):
        try:
            monitor = importlib.import_module("trade_monitor")
            add_trade = getattr(monitor, "add_trade", None)
            if callable(add_trade):
                await asyncio.to_thread(add_trade, data)
        except Exception:
            LOGGER.warning("Silver trade could not be registered with trade monitor")
    with _CACHE_LOCK:
        if delivered:
            _sent_state[today] = {"count": state["count"] + 1, "analysis": deepcopy(data), "in_flight": False}
            for day in list(_sent_state):
                if day < today:
                    _sent_state.pop(day, None)
        else:
            _sent_state[today]["in_flight"] = False
    return delivered
