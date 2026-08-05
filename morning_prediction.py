"""Probability-based pre-market outlook for SHIVAY AI.

The module describes evidence and uncertainty; it never predicts an exact price
or guarantees a direction.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import logging
import math
import threading
import time
from copy import deepcopy
from datetime import date, datetime, time as clock_time
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo
from index_outlook import build_index_outlook

import config
from audience_router import recipients


LOGGER = logging.getLogger("shivay.morning_prediction")
IST = ZoneInfo("Asia/Kolkata")
CACHE_SECONDS = max(120, int(getattr(config, "MORNING_PREDICTION_CACHE", 300)))
MARKET_OPEN = clock_time(
    int(getattr(config, "MARKET_START_HOUR", 9)),
    int(getattr(config, "MARKET_START_MINUTE", 15)),
)
_CACHE_LOCK = threading.RLock()
_prediction_cache: dict[str, Any] | None = None
_prediction_cache_time = 0.0
_prediction_cache_day: date | None = None
_sent_state: dict[date, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(IST)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(str(value).replace("%", "").strip())
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _direction(value: Any) -> str:
    text = str(value or "").upper()
    if any(word in text for word in ("BULL", "BUY", "UP", "POSITIVE")):
        return "BULLISH"
    if any(word in text for word in ("BEAR", "SELL", "DOWN", "NEGATIVE")):
        return "BEARISH"
    if any(word in text for word in ("FLAT", "SIDEWAYS", "NEUTRAL", "NO TRADE")):
        return "NEUTRAL"
    return "UNAVAILABLE"


def _timestamp_date(value: Any) -> date | None:
    try:
        timestamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if not isinstance(timestamp, datetime):
            return None
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(IST)
        return timestamp.date()
    except Exception:
        return None


def _previous_session(symbol: str, today: date) -> dict[str, Any]:
    """Extract the last completed session from the shared bulk data cache."""
    try:
        data_module = importlib.import_module("data")
        get_market_data = getattr(data_module, "get_market_data", None)
        if not callable(get_market_data):
            return {"available": False}
        market = get_market_data(symbol)
    except Exception:
        LOGGER.warning("Shared market cache is unavailable for %s", symbol)
        return {"available": False}
    if not market:
        return {"available": False}
    close_series = market.get("close")
    high_series = market.get("high")
    low_series = market.get("low")
    open_series = market.get("open")
    if any(series is None for series in (close_series, high_series, low_series, open_series)):
        return {"available": False}
    sessions: dict[date, list[tuple[float, float, float, float]]] = {}
    try:
        indexes = list(close_series.index)
        for index in indexes:
            session_day = _timestamp_date(index)
            if session_day is None or session_day >= today:
                continue
            opened = _number(open_series.loc[index])
            high = _number(high_series.loc[index])
            low = _number(low_series.loc[index])
            close = _number(close_series.loc[index])
            if min(opened, high, low, close) <= 0:
                continue
            sessions.setdefault(session_day, []).append((opened, high, low, close))
    except Exception:
        LOGGER.warning("Previous-session extraction failed for %s", symbol)
        return {"available": False}
    if not sessions:
        return {"available": False}
    session_day = max(sessions)
    candles = sessions[session_day]
    session_open = candles[0][0]
    previous_close = candles[-1][3]
    previous_high = max(item[1] for item in candles)
    previous_low = min(item[2] for item in candles)
    change_percent = ((previous_close - session_open) / session_open) * 100.0
    trend = "BULLISH" if change_percent > 0.20 else "BEARISH" if change_percent < -0.20 else "SIDEWAYS"
    return {
        "available": True,
        "date": session_day.isoformat(),
        "open": round(session_open, 2),
        "close": round(previous_close, 2),
        "high": round(previous_high, 2),
        "low": round(previous_low, 2),
        "change_percent": round(change_percent, 2),
        "trend": trend,
        "range_percent": round(((previous_high - previous_low) / previous_close) * 100.0, 2),
    }


def _call_optional(function: Callable[..., Any]) -> Any:
    if inspect.iscoroutinefunction(function):
        return None
    result = function()
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        return None
    return result


def _safe_source(label: str, function: Callable[[], Any], default: Any) -> tuple[Any, bool]:
    """Read one input without allowing it to invalidate other available evidence."""
    try:
        value = function()
        if value is None:
            return deepcopy(default), False
        return value, True
    except Exception:
        LOGGER.warning("Morning input unavailable: %s", label)
        return deepcopy(default), False


def _safe_module(name: str) -> tuple[Any | None, bool]:
    try:
        return importlib.import_module(name), True
    except Exception:
        LOGGER.warning("Morning dependency unavailable: %s", name)
        return None, False


def _module_call(module: Any, name: str, default: Any) -> Any:
    function = getattr(module, name, None) if module is not None else None
    if not callable(function):
        return deepcopy(default)
    return function()


def _commodity_input(metal: str) -> dict[str, Any]:
    module_names = ("mcx", "mcx_scanner", "commodity_scanner", "gold_silver", "metal_scanner")
    function_names = (f"get_{metal}_analysis", f"analyze_{metal}", f"scan_{metal}", f"get_{metal}_direction")
    for module_name in module_names:
        try:
            if importlib.util.find_spec(module_name) is None:
                continue
            module = importlib.import_module(module_name)
        except Exception:
            LOGGER.warning("Optional commodity module %s is unavailable", module_name)
            continue
        for function_name in function_names:
            function = getattr(module, function_name, None)
            if not callable(function):
                continue
            try:
                value = _call_optional(function)
                if isinstance(value, Mapping):
                    raw_direction = value.get("direction", value.get("trend", value.get("bias")))
                    change = value.get("change_percent", value.get("change"))
                else:
                    raw_direction = value
                    change = None
                direction = _direction(raw_direction)
                if direction != "UNAVAILABLE":
                    return {
                        "available": True,
                        "direction": direction,
                        "change_percent": _number(change) if change is not None else None,
                        "source": module_name,
                    }
            except Exception:
                LOGGER.warning("Optional %s analysis failed", metal)
    return {"available": False, "direction": "UNAVAILABLE", "change_percent": None, "source": None}


def _normalize_probabilities(bull: float, bear: float, sideways: float) -> tuple[float, float, float]:
    bull = max(bull, 1.0)
    bear = max(bear, 1.0)
    sideways = max(sideways, 1.0)
    total = bull + bear + sideways
    bullish = round((bull / total) * 100.0, 1)
    bearish = round((bear / total) * 100.0, 1)
    neutral = round(100.0 - bullish - bearish, 1)
    return bullish, bearish, neutral


def _normalize_gap_probabilities(up: float, down: float, flat: float) -> tuple[float, float, float]:
    return _normalize_probabilities(max(up, 5.0), max(down, 5.0), max(flat, 10.0))


def _degraded_prediction(now: datetime, reason: str) -> dict[str, Any]:
    weekend = now.weekday() >= 5
    return {
        "generated_at": now.isoformat(),
        "market_date": now.date().isoformat(),
        "session_state": "WEEKEND" if weekend else "DEGRADED",
        "expected_opening_bias": "NO TRADE",
        "market_bias": "NO TRADE",
        "preferred_trade_direction": "NO TRADE",
        "recommendation": "NO TRADE",
        "bullish_probability": 25.0,
        "bearish_probability": 25.0,
        "sideways_probability": 50.0,
        "gap_up_probability": 25.0,
        "gap_down_probability": 25.0,
        "flat_opening_probability": 50.0,
        "opening_confidence": 0,
        "market_confidence": 0,
        "opening_strength": 0,
        "risk_level": "HIGH",
        "gap_trap_probability": 50.0,
        "gap_trap_warning": True,
        "opening_range_risk": "HIGH",
        "tradeable": False,
        "degraded": True,
        "reason": reason,
        "inputs": {
            "gift_nifty": {"available": False},
            "nifty_previous_session": {"available": False},
            "banknifty_previous_session": {"available": False},
            "gold": {"available": False},
            "silver": {"available": False},
            "global_markets": {"available": False},
        },
    }


def _build_prediction(now: datetime) -> dict[str, Any]:
    if now.weekday() >= 5:
        return _degraded_prediction(now, "NSE is closed on weekends")

    gift_module, gift_module_available = _safe_module("gift_nifty")
    opening_module, opening_module_available = _safe_module("gift_nifty_prediction")
    prediction_module, prediction_module_available = _safe_module("market_prediction")
    sentiment_module, sentiment_module_available = _safe_module("market_sentiment")
    strength_module, strength_module_available = _safe_module("market_strength")
    brain_module, brain_module_available = _safe_module("market_brain")

    opening, opening_available = _safe_source(
        "opening model",
        lambda: _module_call(opening_module, "predict_opening", {}),
        {
            "market_bias": "NEUTRAL",
            "gap_up_probability": 25.0,
            "gap_down_probability": 25.0,
            "flat_opening_probability": 50.0,
            "market_confidence": 0,
            "gap_trap_probability": 45.0,
        },
    )
    session_prediction, prediction_available = _safe_source(
        "market prediction",
        lambda: _module_call(prediction_module, "predict_market", {}),
        {"market_bias": "NO TRADE", "market_confidence": 0},
    )
    gift, gift_available = _safe_source(
        "GIFT NIFTY",
        lambda: dict(_module_call(gift_module, "_get_gift_nifty_analysis", {})),
        {},
    )
    gift_direction = str(gift.get("direction", "UNKNOWN"))
    gift_strength = _number(gift.get("strength"))
    gift_confidence = _number(gift.get("confidence"))
    gift_regime = str(gift.get("regime", "UNKNOWN"))
    gift_tradeable = bool(gift.get("tradeable", False))
    sentiment, sentiment_available = _safe_source(
        "market sentiment",
        lambda: _module_call(sentiment_module, "get_market_sentiment", "NO TRADE"),
        "NO TRADE",
    )
    sentiment_confidence, sentiment_confidence_available = _safe_source(
        "sentiment confidence",
        lambda: _module_call(sentiment_module, "get_sentiment_confidence", 0),
        0,
    )
    market_strength, market_strength_available = _safe_source(
        "market strength",
        lambda: _module_call(strength_module, "get_market_strength", 0),
        0,
    )
    strength_confidence, strength_confidence_available = _safe_source(
        "strength confidence",
        lambda: _module_call(strength_module, "get_strength_confidence", 0),
        0,
    )
    trend_quality, trend_available = _safe_source(
        "trend quality",
        lambda: _module_call(strength_module, "get_trend_strength", 0),
        0,
    )
    brain, brain_available = _safe_source(
        "market brain",
        lambda: _module_call(brain_module, "_get_market_analysis", {}),
        {},
    )
    opening_available = opening_module_available and opening_available and bool(opening)
    prediction_available = prediction_module_available and prediction_available and bool(session_prediction)
    sentiment_available = sentiment_module_available and sentiment_available
    sentiment_confidence_available = sentiment_module_available and sentiment_confidence_available
    market_strength_available = strength_module_available and market_strength_available
    strength_confidence_available = strength_module_available and strength_confidence_available
    trend_available = strength_module_available and trend_available
    brain_available = brain_module_available and brain_available
    gift_available = gift_module_available and gift_available
    sentiment = str(sentiment)
    sentiment_confidence = _number(sentiment_confidence)
    market_strength = _number(market_strength)
    strength_confidence = _number(strength_confidence)
    trend_quality = _number(trend_quality)

    nifty = _previous_session("NIFTY FUT", now.date())
    banknifty = _previous_session("BANKNIFTY FUT", now.date())
    gold = _commodity_input("gold")
    silver = _commodity_input("silver")

    gift_price = _number(gift.get("price"))
    gift_available = gift_available and gift_price > 0
    nifty_close = _number(nifty.get("close"))
    overnight_change = (
        ((gift_price - nifty_close) / nifty_close) * 100.0
        if gift_price > 0 and nifty_close > 0
        else None
    )
    if overnight_change is None:
        overnight_direction = gift_direction if gift_direction in {"BULLISH", "BEARISH"} else "UNAVAILABLE"
    elif overnight_change > 0.12:
        overnight_direction = "BULLISH"
    elif overnight_change < -0.12:
        overnight_direction = "BEARISH"
    else:
        overnight_direction = "NEUTRAL"

    gap_up = _number(opening.get("gap_up_probability"), 25.0)
    gap_down = _number(opening.get("gap_down_probability"), 25.0)
    flat_open = _number(opening.get("flat_opening_probability"), 50.0)
    bull = max(gap_up, 10.0)
    bear = max(gap_down, 10.0)
    sideways = max(flat_open, 15.0)

    evidence: list[tuple[str, str]] = []

    def apply(direction: str, weight: float, label: str) -> None:
        nonlocal bull, bear, sideways
        normalized = _direction(direction)
        if normalized == "BULLISH":
            bull += weight
            evidence.append((label, "BULLISH"))
        elif normalized == "BEARISH":
            bear += weight
            evidence.append((label, "BEARISH"))
        elif normalized == "NEUTRAL":
            sideways += weight
            evidence.append((label, "NEUTRAL"))

    if gift_available:
        apply(gift_direction, 12.0, "GIFT NIFTY")
        apply(overnight_direction, min(10.0, 4.0 + abs(overnight_change or 0.0) * 5.0), "Overnight momentum")
    if opening_available:
        apply(str(opening.get("market_bias", "NEUTRAL")), 9.0, "Opening model")
    if prediction_available:
        apply(str(session_prediction.get("market_bias", "NO TRADE")), 8.0, "Market model")
    if sentiment_available:
        apply(sentiment, 8.0, "Market sentiment")
    if nifty.get("available"):
        apply(str(nifty.get("trend")), 5.0, "NIFTY previous day")
    if banknifty.get("available"):
        apply(str(banknifty.get("trend")), 5.0, "BANKNIFTY previous day")
    if gold.get("available"):
        apply(str(gold.get("direction")), 1.5, "Gold")
    if silver.get("available"):
        apply(str(silver.get("direction")), 1.5, "Silver")

    bullish_probability, bearish_probability, sideways_probability = _normalize_probabilities(
        bull, bear, sideways
    )
    if overnight_change is not None:
        adjustment = min(10.0, abs(overnight_change) * 8.0)
        if overnight_change > 0:
            gap_up += adjustment
            flat_open -= adjustment * 0.35
        elif overnight_change < 0:
            gap_down += adjustment
            flat_open -= adjustment * 0.35
    gap_up, gap_down, flat_open = _normalize_gap_probabilities(gap_up, gap_down, flat_open)

    directional_leader = max(bullish_probability, bearish_probability)
    directional_margin = abs(bullish_probability - bearish_probability)
    directional_bias = "BULLISH" if bullish_probability > bearish_probability else "BEARISH"
    conflicts = sum(
        1
        for _, direction in evidence
        if direction in {"BULLISH", "BEARISH"} and direction != directional_bias
    )
    directional_evidence = sum(1 for _, direction in evidence if direction in {"BULLISH", "BEARISH"})
    conflict_ratio = conflicts / directional_evidence if directional_evidence else 1.0

    base_confidence = (
        gift_confidence * 0.22
        + _number(opening.get("market_confidence")) * 0.24
        + _number(session_prediction.get("market_confidence")) * 0.18
        + sentiment_confidence * 0.14
        + strength_confidence * 0.12
        + directional_leader * 0.10
    )
    opening_confidence = int(round(_clamp(base_confidence - conflict_ratio * 18.0)))
    opening_strength = int(
        round(
            _clamp(
                directional_leader * 0.32
                + gift_strength * 0.20
                + market_strength * 0.20
                + trend_quality * 0.18
                + directional_margin * 0.10
            )
        )
    )

    previous_ranges = [
        _number(item.get("range_percent"))
        for item in (nifty, banknifty)
        if item.get("available")
    ]
    premarket_volatility = (
        sum(previous_ranges) / len(previous_ranges) if previous_ranges else None
    )
    source_gap_trap = _number(opening.get("gap_trap_probability"), 35.0)
    gap_trap_probability = source_gap_trap + conflict_ratio * 22.0
    if overnight_change is not None and abs(overnight_change) >= 1.0:
        gap_trap_probability += 8.0
    if gift_regime == "SIDEWAYS":
        gap_trap_probability += 7.0
    if directional_margin >= 12 and conflict_ratio <= 0.25:
        gap_trap_probability -= 6.0
    gap_trap_probability = round(_clamp(gap_trap_probability, 5.0, 80.0), 1)

    volatility_mode = str(brain.get("volatility_mode", "UNKNOWN"))
    if volatility_mode == "HIGH RISK" or (premarket_volatility is not None and premarket_volatility >= 2.0):
        opening_range_risk = "HIGH"
    elif premarket_volatility is None:
        opening_range_risk = "UNKNOWN"
    elif premarket_volatility >= 1.2:
        opening_range_risk = "MODERATE"
    else:
        opening_range_risk = "LOW"

    strong_direction = directional_leader >= 38.0 and directional_margin >= 5.0
    expected_bias = directional_bias if strong_direction else "NEUTRAL"
    risk_level = "HIGH"
    if gap_trap_probability < 35 and opening_confidence >= 72 and opening_range_risk != "HIGH":
        risk_level = "LOW"
    elif gap_trap_probability < 52 and opening_confidence >= 56:
        risk_level = "MODERATE"
    tradeable = (
        expected_bias in {"BULLISH", "BEARISH"}
        and opening_confidence >= 58
        and opening_strength >= 50
        and directional_margin >= 6
        and gap_trap_probability < 55
        and opening_range_risk != "HIGH"
    )
    if not gift_tradeable and opening_confidence < 66:
        tradeable = False
    preferred_direction = "BUY" if tradeable and expected_bias == "BULLISH" else "SELL" if tradeable else "NO TRADE"

    available_core = sum(
        (gift_available, bool(nifty.get("available")), bool(banknifty.get("available")))
    )
    model_sources = sum((opening_available, prediction_available, sentiment_available))
    degraded = available_core < 2 or model_sources < 1
    if degraded:
        opening_confidence = min(opening_confidence, 55)
        tradeable = False
        preferred_direction = "NO TRADE"

    return {
        "generated_at": now.isoformat(),
        "market_date": now.date().isoformat(),
        "session_state": "PRE_MARKET" if now.time() < MARKET_OPEN else "MARKET_OPEN_OR_LATER",
        "expected_opening_bias": expected_bias if tradeable else "NO TRADE",
        "market_bias": expected_bias if tradeable else "NO TRADE",
        "preferred_trade_direction": preferred_direction,
        "recommendation": preferred_direction,
        "bullish_probability": bullish_probability,
        "bearish_probability": bearish_probability,
        "sideways_probability": sideways_probability,
        "gap_up_probability": gap_up,
        "gap_down_probability": gap_down,
        "flat_opening_probability": flat_open,
        "opening_confidence": opening_confidence,
        "market_confidence": opening_confidence,
        "opening_strength": opening_strength,
        "trend_quality": round(trend_quality, 1),
        "market_strength": round(market_strength, 1),
        "risk_level": risk_level,
        "gap_trap_probability": gap_trap_probability,
        "gap_trap_warning": gap_trap_probability >= 40,
        "opening_range_risk": opening_range_risk,
        "premarket_volatility_percent": round(premarket_volatility, 2) if premarket_volatility is not None else None,
        "tradeable": tradeable,
        "degraded": degraded,
        "reason": (
            "Directional evidence is aligned"
            if tradeable
            else "Evidence is incomplete, conflicting, or below the safe confidence threshold"
        ),
        "index_outlooks": {
            "NIFTY": build_index_outlook("NIFTY", nifty, {"bullish": bullish_probability, "bearish": bearish_probability, "sideways": sideways_probability, "gap_up": gap_up, "gap_down": gap_down, "gap_trap": gap_trap_probability}, str(brain.get("market_regime", "UNKNOWN")), market_strength, risk_level, preferred_direction),
            "BANKNIFTY": build_index_outlook("BANKNIFTY", banknifty, {"bullish": bullish_probability, "bearish": bearish_probability, "sideways": sideways_probability, "gap_up": gap_up, "gap_down": gap_down, "gap_trap": gap_trap_probability}, str(brain.get("market_regime", "UNKNOWN")), market_strength, risk_level, preferred_direction),
        },
        "evidence": [{"source": source, "direction": direction} for source, direction in evidence],
        "inputs": {
            "gift_nifty": {
                "available": gift_available,
                "price": round(gift_price, 2) if gift_price > 0 else None,
                "direction": gift_direction,
                "regime": gift_regime,
                "strength": round(gift_strength, 1),
                "confidence": round(gift_confidence, 1),
                "overnight_change_percent": round(overnight_change, 2) if overnight_change is not None else None,
                "overnight_momentum": overnight_direction,
            },
            "nifty_previous_session": nifty,
            "banknifty_previous_session": banknifty,
            "market_sentiment": {
                "available": sentiment_available and sentiment_confidence_available,
                "value": sentiment,
                "confidence": round(sentiment_confidence, 1),
            },
            "market_prediction": {
                "available": prediction_available,
                "bias": session_prediction.get("market_bias", "NO TRADE"),
                "confidence": session_prediction.get("market_confidence", 0),
            },
            "opening_model": {"available": opening_available},
            "market_brain": {"available": brain_available},
            "market_strength": {
                "available": market_strength_available,
                "confidence_available": strength_confidence_available,
            },
            "trend_quality": {"available": trend_available},
            "gold": gold,
            "silver": silver,
            "global_markets": {"available": False, "reason": "No project data source configured"},
        },
    }


def generate_morning_prediction(force_refresh: bool = False) -> dict[str, Any]:
    """Generate or return a short-lived, request-efficient morning outlook."""
    global _prediction_cache
    global _prediction_cache_time
    global _prediction_cache_day
    now = _now()
    monotonic_now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force_refresh
            and _prediction_cache is not None
            and _prediction_cache_day == now.date()
            and monotonic_now - _prediction_cache_time < CACHE_SECONDS
        ):
            return deepcopy(_prediction_cache)
        try:
            prediction = _build_prediction(now)
        except Exception:
            LOGGER.exception("Morning prediction degraded after an input failure")
            prediction = _degraded_prediction(now, "One or more pre-market inputs are unavailable")
        _prediction_cache = prediction
        _prediction_cache_time = monotonic_now
        _prediction_cache_day = now.date()
        return deepcopy(prediction)


def get_morning_bias() -> str:
    return str(generate_morning_prediction().get("market_bias", "NO TRADE"))


def get_opening_probabilities() -> dict[str, float]:
    prediction = generate_morning_prediction()
    return {
        "bullish": _number(prediction.get("bullish_probability")),
        "bearish": _number(prediction.get("bearish_probability")),
        "sideways": _number(prediction.get("sideways_probability")),
        "gap_up": _number(prediction.get("gap_up_probability")),
        "gap_down": _number(prediction.get("gap_down_probability")),
        "flat": _number(prediction.get("flat_opening_probability")),
    }


def get_opening_confidence() -> int:
    return int(_number(generate_morning_prediction().get("opening_confidence")))


def get_opening_risk() -> str:
    return str(generate_morning_prediction().get("risk_level", "HIGH"))


def get_gap_trap_probability() -> float:
    return _number(generate_morning_prediction().get("gap_trap_probability"))


def is_morning_tradeable() -> bool:
    return bool(generate_morning_prediction().get("tradeable", False))


def _value(value: Any, suffix: str = "") -> str:
    return "Unavailable" if value is None else f"{value}{suffix}"


def format_morning_report(prediction: Mapping[str, Any] | None = None) -> str:
    data = dict(prediction or generate_morning_prediction())
    from telegram_service import format_index_outlook_report
    return format_index_outlook_report(data, night=False)
    # Legacy detailed formatting remains below for compatibility reference but
    # normal scheduled delivery always uses the compact report above.
    inputs = data.get("inputs", {}) if isinstance(data.get("inputs"), Mapping) else {}
    gift = inputs.get("gift_nifty", {}) if isinstance(inputs.get("gift_nifty"), Mapping) else {}
    nifty = inputs.get("nifty_previous_session", {}) if isinstance(inputs.get("nifty_previous_session"), Mapping) else {}
    bank = inputs.get("banknifty_previous_session", {}) if isinstance(inputs.get("banknifty_previous_session"), Mapping) else {}
    warning = "YES — wait for opening confirmation" if data.get("gap_trap_warning") else "No material warning"
    return (
        "☀️ SHIVAY AI — MORNING MARKET OUTLOOK\n\n"
        f"Opening Bias: {data.get('market_bias', 'NO TRADE')}\n"
        f"Preferred Direction: {data.get('preferred_trade_direction', 'NO TRADE')}\n"
        f"Confidence: {data.get('opening_confidence', 0)}/100 | Strength: {data.get('opening_strength', 0)}/100\n"
        f"Risk: {data.get('risk_level', 'HIGH')} | Opening-range Risk: {data.get('opening_range_risk', 'UNKNOWN')}\n\n"
        "PROBABILITIES\n"
        f"Bullish: {data.get('bullish_probability', 0)}% | Bearish: {data.get('bearish_probability', 0)}%\n"
        f"Sideways: {data.get('sideways_probability', 0)}%\n"
        f"Gap Up: {data.get('gap_up_probability', 0)}% | Gap Down: {data.get('gap_down_probability', 0)}%\n"
        f"Flat Open: {data.get('flat_opening_probability', 0)}%\n\n"
        "PRE-MARKET INPUTS\n"
        f"GIFT NIFTY: {gift.get('direction', 'Unavailable')} | Overnight: {_value(gift.get('overnight_change_percent'), '%')}\n"
        f"NIFTY Previous Close: {_value(nifty.get('close'))} | High/Low: {_value(nifty.get('high'))}/{_value(nifty.get('low'))}\n"
        f"BANKNIFTY Previous Close: {_value(bank.get('close'))} | High/Low: {_value(bank.get('high'))}/{_value(bank.get('low'))}\n\n"
        f"Gap-trap Probability: {data.get('gap_trap_probability', 0)}%\n"
        f"Gap-trap Warning: {warning}\n\n"
        f"Recommendation: {data.get('recommendation', 'NO TRADE')}\n"
        f"Reason: {data.get('reason', 'Insufficient evidence')}\n\n"
        "Probabilities are estimates, not guarantees. Confirm the opening range before trading."
    )


def _send_signature(prediction: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        prediction.get("market_bias"),
        round(_number(prediction.get("bullish_probability")) / 7.0),
        round(_number(prediction.get("bearish_probability")) / 7.0),
        round(_number(prediction.get("opening_confidence")) / 8.0),
        bool(prediction.get("gap_trap_warning")),
    )


def _meaningful_change(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool:
    if not previous:
        return True
    if previous.get("market_bias") != current.get("market_bias"):
        return True
    if previous.get("preferred_trade_direction") != current.get("preferred_trade_direction"):
        return True
    if previous.get("risk_level") != current.get("risk_level"):
        return True
    if bool(previous.get("gap_trap_warning")) != bool(current.get("gap_trap_warning")):
        return True
    probability_fields = (
        "bullish_probability",
        "bearish_probability",
        "gap_up_probability",
        "gap_down_probability",
    )
    if any(
        abs(_number(current.get(field)) - _number(previous.get(field))) >= 7.0
        for field in probability_fields
    ):
        return True
    return abs(
        _number(current.get("opening_confidence"))
        - _number(previous.get("opening_confidence"))
    ) >= 8.0


async def send_morning_prediction(
    application: Any,
    prediction: Mapping[str, Any] | None = None,
    force_refresh: bool = False,
) -> bool:
    """Send once before open, with one refresh allowed after meaningful change."""
    if application is None or not hasattr(application, "bot"):
        return False
    now = _now()
    if now.weekday() >= 5 or now.time() >= MARKET_OPEN:
        return False
    data = dict(prediction) if prediction is not None else await asyncio.to_thread(
        generate_morning_prediction, force_refresh
    )
    with _CACHE_LOCK:
        state = dict(
            _sent_state.get(
                now.date(),
                {"count": 0, "signature": None, "prediction": None, "in_flight": False},
            )
        )
        if state.get("in_flight") or state["count"] >= 2:
            return False
        if state["count"] >= 1 and not _meaningful_change(state.get("prediction"), data):
            return False
        state["in_flight"] = True
        _sent_state[now.date()] = state
    try:
        users = await asyncio.to_thread(recipients, "ALL")
    except Exception:
        LOGGER.exception("Morning-report recipients are unavailable")
        with _CACHE_LOCK:
            current = _sent_state.get(now.date())
            if current is not None:
                current["in_flight"] = False
        return False
    text = format_morning_report(data)
    delivered = False
    for user in users:
        try:
            await application.bot.send_message(chat_id=int(user["id"]), text=text)
            delivered = True
        except asyncio.CancelledError:
            with _CACHE_LOCK:
                current = _sent_state.get(now.date())
                if current is not None:
                    current["in_flight"] = False
            raise
        except Exception:
            LOGGER.warning("Morning report delivery failed")
    if delivered:
        with _CACHE_LOCK:
            _sent_state[now.date()] = {
                "count": state["count"] + 1,
                "signature": _send_signature(data),
                "prediction": deepcopy(data),
                "in_flight": False,
            }
            for day in list(_sent_state):
                if day < now.date():
                    _sent_state.pop(day, None)
    else:
        with _CACHE_LOCK:
            current = _sent_state.get(now.date())
            if current is not None:
                current["in_flight"] = False
    return delivered
