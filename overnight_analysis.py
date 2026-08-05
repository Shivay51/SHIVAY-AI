"""Probability-based post-close analysis for the next NSE session."""

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


LOGGER = logging.getLogger("shivay.overnight_analysis")
IST = ZoneInfo("Asia/Kolkata")
CACHE_SECONDS = max(180, int(getattr(config, "OVERNIGHT_ANALYSIS_CACHE", 300)))
DATA_RETRY_ATTEMPTS = max(1, min(int(getattr(config, "OVERNIGHT_DATA_RETRIES", 2)), 3))
DATA_RETRY_DELAY = max(0.0, min(float(getattr(config, "OVERNIGHT_RETRY_DELAY", 0.25)), 1.0))
MARKET_CLOSE = clock_time(
    int(getattr(config, "MARKET_END_HOUR", 15)),
    int(getattr(config, "MARKET_END_MINUTE", 30)),
)
_CACHE_LOCK = threading.RLock()
_analysis_cache: dict[str, Any] | None = None
_analysis_cache_time = 0.0
_analysis_cache_day: date | None = None
_sent_state: dict[date, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(IST)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(str(value).replace("%", "").strip())
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _direction(value: Any) -> str:
    text = str(value or "").upper()
    if any(item in text for item in ("BULL", "BUY", "UP", "POSITIVE", "LONG")):
        return "BULLISH"
    if any(item in text for item in ("BEAR", "SELL", "DOWN", "NEGATIVE", "SHORT")):
        return "BEARISH"
    if any(item in text for item in ("FLAT", "SIDEWAYS", "NEUTRAL", "NO TRADE")):
        return "NEUTRAL"
    return "UNAVAILABLE"


def _safe_module(name: str) -> tuple[Any | None, bool]:
    try:
        return importlib.import_module(name), True
    except Exception:
        LOGGER.warning("Overnight dependency unavailable: %s", name)
        return None, False


def _module_call(module: Any, name: str, default: Any) -> Any:
    function = getattr(module, name, None) if module is not None else None
    if not callable(function):
        return deepcopy(default)
    return function()


def _safe_source(label: str, function: Callable[[], Any], default: Any) -> tuple[Any, bool]:
    try:
        value = function()
        if value is None:
            return deepcopy(default), False
        return value, True
    except Exception:
        LOGGER.warning("Overnight input unavailable: %s", label)
        return deepcopy(default), False


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


def _last_completed_session(symbol: str, now: datetime) -> dict[str, Any]:
    """Read one completed NSE session from the existing shared bulk cache."""
    try:
        data_module = importlib.import_module("data")
        getter = getattr(data_module, "get_market_data", None)
        if not callable(getter):
            return {"available": False}
    except Exception:
        LOGGER.warning("Shared market cache unavailable for %s", symbol)
        return {"available": False}
    market = None
    for attempt in range(1, DATA_RETRY_ATTEMPTS + 1):
        try:
            market = getter(symbol)
            if market:
                break
        except Exception:
            LOGGER.warning(
                "Temporary shared-cache read failure for %s (%s/%s)",
                symbol,
                attempt,
                DATA_RETRY_ATTEMPTS,
            )
        if attempt < DATA_RETRY_ATTEMPTS and DATA_RETRY_DELAY:
            time.sleep(DATA_RETRY_DELAY * attempt)
    if not market:
        return {"available": False}
    series = {
        "open": market.get("open"),
        "high": market.get("high"),
        "low": market.get("low"),
        "close": market.get("close"),
        "volume": market.get("volume"),
    }
    if any(value is None for value in series.values()):
        return {"available": False}
    include_today = now.time() >= MARKET_CLOSE
    sessions: dict[date, list[tuple[float, float, float, float, float]]] = {}
    try:
        close_series = series["close"]
        for position, index in enumerate(close_series.index):
            session_day = _timestamp_date(index)
            if session_day is None:
                continue
            if session_day > now.date() or (session_day == now.date() and not include_today):
                continue
            values = tuple(
                _number(series[field].iloc[position])
                for field in ("open", "high", "low", "close", "volume")
            )
            if min(values[:4]) <= 0:
                continue
            sessions.setdefault(session_day, []).append(values)
    except Exception:
        LOGGER.warning("Completed-session extraction failed for %s", symbol)
        return {"available": False}
    if not sessions:
        return {"available": False}
    session_day = max(sessions)
    age_days = (now.date() - session_day).days
    stale = (
        (include_today and session_day != now.date())
        or (not include_today and age_days > 4)
    )
    candles = sessions[session_day]
    session_open = candles[0][0]
    session_close = candles[-1][3]
    session_high = max(item[1] for item in candles)
    session_low = min(item[2] for item in candles)
    change_percent = ((session_close - session_open) / session_open) * 100.0
    lookback = min(6, len(candles))
    momentum_base = candles[-lookback][3]
    closing_momentum = (
        ((session_close - momentum_base) / momentum_base) * 100.0
        if momentum_base > 0 else 0.0
    )
    volumes = [max(item[4], 0.0) for item in candles]
    recent_volume = sum(volumes[-min(3, len(volumes)):]) / min(3, len(volumes))
    earlier = volumes[-23:-3] if len(volumes) > 3 else []
    average_volume = sum(earlier) / len(earlier) if earlier else 0.0
    closing_volume_ratio = recent_volume / average_volume if average_volume > 0 else None
    trend = "BULLISH" if change_percent > 0.20 else "BEARISH" if change_percent < -0.20 else "SIDEWAYS"
    range_percent = ((session_high - session_low) / session_close) * 100.0
    trend_strength = _clamp(
        abs(change_percent) * 24.0
        + abs(closing_momentum) * 22.0
        + (min(closing_volume_ratio or 1.0, 2.0) * 12.0)
    )
    if trend == "SIDEWAYS":
        regime = "SIDEWAYS"
    elif trend_strength >= 72:
        regime = "STRONG BULL" if trend == "BULLISH" else "STRONG BEAR"
    else:
        regime = "BULL" if trend == "BULLISH" else "BEAR"
    return {
        "available": True,
        "stale": stale,
        "age_days": age_days,
        "date": session_day.isoformat(),
        "open": round(session_open, 2),
        "close": round(session_close, 2),
        "high": round(session_high, 2),
        "low": round(session_low, 2),
        "change_percent": round(change_percent, 2),
        "range_percent": round(range_percent, 2),
        "trend": trend,
        "regime": regime,
        "trend_strength": round(trend_strength, 1),
        "closing_momentum_percent": round(closing_momentum, 2),
        "closing_volume_ratio": round(closing_volume_ratio, 2) if closing_volume_ratio is not None else None,
        "closing_volume_available": closing_volume_ratio is not None,
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


def _optional_trend(kind: str) -> dict[str, Any]:
    if kind == "gold":
        modules = ("gold", "mcx", "commodity_scanner", "gold_silver", "metal_scanner")
    elif kind == "silver":
        modules = ("silver", "mcx", "commodity_scanner", "gold_silver", "metal_scanner")
    else:
        modules = ("mcx", "mcx_scanner", "commodity_scanner", "gold_silver", "metal_scanner")
    names = (
        f"get_{kind}_analysis", f"analyze_{kind}", f"scan_{kind}",
        f"get_{kind}_direction", f"get_{kind}_trend",
    )
    for module_name in modules:
        try:
            if importlib.util.find_spec(module_name) is None:
                continue
            module = importlib.import_module(module_name)
        except Exception:
            LOGGER.warning("Optional overnight module unavailable: %s", module_name)
            continue
        for name in names:
            function = getattr(module, name, None)
            if not callable(function):
                continue
            try:
                value = _call_optional(function)
                if isinstance(value, Mapping):
                    raw_direction = value.get("direction", value.get("trend", value.get("bias")))
                    change = value.get("change_percent", value.get("change"))
                    strength = value.get("strength", value.get("confidence"))
                else:
                    raw_direction, change, strength = value, None, None
                direction = _direction(raw_direction)
                if direction != "UNAVAILABLE":
                    return {
                        "available": True,
                        "direction": direction,
                        "change_percent": _number(change) if change is not None else None,
                        "strength": _number(strength) if strength is not None else None,
                        "source": module_name,
                    }
            except Exception:
                LOGGER.warning("Optional %s trend failed", kind)
    return {
        "available": False,
        "direction": "UNAVAILABLE",
        "change_percent": None,
        "strength": None,
        "source": None,
    }


def _probabilities(bull: float, bear: float, sideways: float) -> tuple[float, float, float]:
    values = (max(bull, 1.0), max(bear, 1.0), max(sideways, 1.0))
    total = sum(values)
    bullish = round(values[0] / total * 100.0, 1)
    bearish = round(values[1] / total * 100.0, 1)
    return bullish, bearish, round(100.0 - bullish - bearish, 1)


def _degraded(now: datetime, reason: str) -> dict[str, Any]:
    return {
        "generated_at": now.isoformat(),
        "analysis_date": now.date().isoformat(),
        "session_state": "WEEKEND" if now.weekday() >= 5 else "DEGRADED",
        "overnight_bias": "NO TRADE",
        "market_bias": "NO TRADE",
        "next_session_opening_bias": "NO TRADE",
        "preferred_next_session_direction": "NO TRADE",
        "recommendation": "NO TRADE",
        "bullish_probability": 25.0,
        "bearish_probability": 25.0,
        "sideways_probability": 50.0,
        "gap_up_probability": 25.0,
        "gap_down_probability": 25.0,
        "flat_opening_probability": 50.0,
        "trend_continuation_probability": 40.0,
        "trend_reversal_probability": 60.0,
        "overnight_confidence": 0,
        "market_confidence": 0,
        "overnight_risk_level": "HIGH",
        "risk_level": "HIGH",
        "gap_trap_probability": 50.0,
        "gap_trap_warning": True,
        "tradeable": False,
        "degraded": True,
        "reason": reason,
        "inputs": {
            "gift_nifty": {"available": False},
            "nifty_session": {"available": False},
            "banknifty_session": {"available": False},
            "gold": {"available": False},
            "silver": {"available": False},
            "mcx": {"available": False},
            "global_markets": {"available": False},
        },
    }


def _build_analysis(now: datetime) -> dict[str, Any]:
    if now.weekday() >= 5:
        return _degraded(now, "NSE is closed on weekends")

    gift_module, gift_module_ok = _safe_module("gift_nifty")
    opening_module, opening_module_ok = _safe_module("gift_nifty_prediction")
    prediction_module, prediction_module_ok = _safe_module("market_prediction")
    sentiment_module, sentiment_module_ok = _safe_module("market_sentiment")
    strength_module, strength_module_ok = _safe_module("market_strength")
    brain_module, brain_module_ok = _safe_module("market_brain")

    gift, gift_ok = _safe_source(
        "GIFT NIFTY",
        lambda: dict(_module_call(gift_module, "_get_gift_nifty_analysis", {})),
        {},
    )
    opening, opening_ok = _safe_source(
        "opening probabilities",
        lambda: dict(_module_call(opening_module, "predict_opening", {})),
        {},
    )
    prediction, prediction_ok = _safe_source(
        "market prediction",
        lambda: dict(_module_call(prediction_module, "predict_market", {})),
        {},
    )
    sentiment, sentiment_ok = _safe_source(
        "market sentiment",
        lambda: _module_call(sentiment_module, "get_market_sentiment", "NO TRADE"),
        "NO TRADE",
    )
    sentiment_confidence, sentiment_confidence_ok = _safe_source(
        "sentiment confidence",
        lambda: _module_call(sentiment_module, "get_sentiment_confidence", 0),
        0,
    )
    market_strength, market_strength_ok = _safe_source(
        "market strength",
        lambda: _module_call(strength_module, "get_market_strength", 0),
        0,
    )
    strength_confidence, strength_confidence_ok = _safe_source(
        "strength confidence",
        lambda: _module_call(strength_module, "get_strength_confidence", 0),
        0,
    )
    trend_quality, trend_quality_ok = _safe_source(
        "trend quality",
        lambda: _module_call(strength_module, "get_trend_strength", 0),
        0,
    )
    brain, brain_ok = _safe_source(
        "market brain",
        lambda: dict(_module_call(brain_module, "_get_market_analysis", {})),
        {},
    )

    gift_ok = gift_module_ok and gift_ok and bool(gift)
    opening_ok = opening_module_ok and opening_ok and bool(opening)
    prediction_ok = prediction_module_ok and prediction_ok and bool(prediction)
    sentiment_ok = sentiment_module_ok and sentiment_ok
    sentiment_confidence_ok = sentiment_module_ok and sentiment_confidence_ok
    market_strength_ok = strength_module_ok and market_strength_ok
    strength_confidence_ok = strength_module_ok and strength_confidence_ok
    trend_quality_ok = strength_module_ok and trend_quality_ok
    brain_ok = brain_module_ok and brain_ok and bool(brain)

    nifty = _last_completed_session("NIFTY FUT", now)
    banknifty = _last_completed_session("BANKNIFTY FUT", now)
    gold = _optional_trend("gold")
    silver = _optional_trend("silver")
    mcx = _optional_trend("mcx")

    gift_price = _number(gift.get("price"))
    gift_direction = str(gift.get("direction", "UNKNOWN"))
    gift_regime = str(gift.get("regime", "UNKNOWN"))
    gift_strength = _number(gift.get("strength"))
    gift_confidence = _number(gift.get("confidence"))
    gift_ok = gift_ok and gift_price > 0
    gift_cache_time = _number(getattr(gift_module, "_gift_nifty_cache_time", 0.0))
    gift_stale = (
        gift_ok
        and gift_cache_time > 0
        and time.time() - gift_cache_time > max(CACHE_SECONDS * 2, 1800)
    )
    if gift_stale:
        gift_ok = False
    nifty_close = _number(nifty.get("close"))
    gift_change = (
        ((gift_price - nifty_close) / nifty_close) * 100.0
        if gift_price > 0 and nifty_close > 0 else None
    )
    if gift_change is None:
        overnight_movement = gift_direction if gift_ok else "UNAVAILABLE"
    elif gift_change > 0.12:
        overnight_movement = "BULLISH"
    elif gift_change < -0.12:
        overnight_movement = "BEARISH"
    else:
        overnight_movement = "NEUTRAL"

    gap_up = _number(opening.get("gap_up_probability"), 25.0)
    gap_down = _number(opening.get("gap_down_probability"), 25.0)
    flat_open = _number(opening.get("flat_opening_probability"), 50.0)
    bull, bear, sideways = max(gap_up, 10.0), max(gap_down, 10.0), max(flat_open, 15.0)
    evidence: list[tuple[str, str]] = []

    def apply(value: Any, weight: float, label: str) -> None:
        nonlocal bull, bear, sideways
        direction = _direction(value)
        if direction == "BULLISH":
            bull += weight
            evidence.append((label, direction))
        elif direction == "BEARISH":
            bear += weight
            evidence.append((label, direction))
        elif direction == "NEUTRAL":
            sideways += weight
            evidence.append((label, direction))

    if gift_ok:
        apply(gift_direction, 10.0, "GIFT NIFTY")
        apply(overnight_movement, min(10.0, 4.0 + abs(gift_change or 0.0) * 5.0), "Overnight movement")
    if nifty.get("available") and not nifty.get("stale"):
        apply(nifty.get("trend"), 7.0, "NIFTY close")
        apply("BULLISH" if _number(nifty.get("closing_momentum_percent")) > 0.1 else "BEARISH" if _number(nifty.get("closing_momentum_percent")) < -0.1 else "NEUTRAL", 5.0, "NIFTY closing momentum")
    if banknifty.get("available") and not banknifty.get("stale"):
        apply(banknifty.get("trend"), 7.0, "BANKNIFTY close")
        apply("BULLISH" if _number(banknifty.get("closing_momentum_percent")) > 0.1 else "BEARISH" if _number(banknifty.get("closing_momentum_percent")) < -0.1 else "NEUTRAL", 5.0, "BANKNIFTY closing momentum")
    if opening_ok:
        apply(opening.get("market_bias"), 8.0, "Opening model")
    if prediction_ok:
        apply(prediction.get("market_bias"), 8.0, "Market prediction")
    if sentiment_ok:
        apply(sentiment, 7.0, "Market sentiment")
    for label, item in (("Gold", gold), ("Silver", silver), ("MCX", mcx)):
        if item.get("available"):
            apply(item.get("direction"), 1.5, label)

    bullish_probability, bearish_probability, sideways_probability = _probabilities(bull, bear, sideways)
    if gift_change is not None:
        gap_adjustment = min(10.0, abs(gift_change) * 8.0)
        if gift_change > 0:
            gap_up += gap_adjustment
        elif gift_change < 0:
            gap_down += gap_adjustment
        flat_open -= gap_adjustment * 0.35
    gap_up, gap_down, flat_open = _probabilities(max(gap_up, 5.0), max(gap_down, 5.0), max(flat_open, 10.0))

    leader = max(bullish_probability, bearish_probability)
    margin = abs(bullish_probability - bearish_probability)
    directional_bias = "BULLISH" if bullish_probability > bearish_probability else "BEARISH"
    directional_evidence = [direction for _, direction in evidence if direction in {"BULLISH", "BEARISH"}]
    conflicts = sum(direction != directional_bias for direction in directional_evidence)
    conflict_ratio = conflicts / len(directional_evidence) if directional_evidence else 1.0

    previous_direction = (
        _direction(nifty.get("trend"))
        if nifty.get("available") and not nifty.get("stale")
        else "UNAVAILABLE"
    )
    aligned_next = sum(
        direction == previous_direction
        for direction in directional_evidence
        if previous_direction in {"BULLISH", "BEARISH"}
    )
    opposed_next = sum(
        direction != previous_direction
        for direction in directional_evidence
        if direction in {"BULLISH", "BEARISH"} and previous_direction in {"BULLISH", "BEARISH"}
    )
    continuation_score = 50.0 + aligned_next * 6.0 - opposed_next * 5.0
    if previous_direction == "UNAVAILABLE" or previous_direction == "NEUTRAL":
        continuation_score = 45.0
    if _number(nifty.get("closing_volume_ratio"), 1.0) >= 1.2:
        continuation_score += 4.0
    reversal_score = 100.0 - continuation_score
    continuation_probability = round(_clamp(continuation_score, 15.0, 85.0), 1)
    reversal_probability = round(100.0 - continuation_probability, 1)

    source_gap_trap = _number(opening.get("gap_trap_probability"), 38.0)
    gap_trap_probability = source_gap_trap + conflict_ratio * 22.0
    if gift_change is not None and abs(gift_change) >= 1.0:
        gap_trap_probability += 8.0
    if margin >= 12 and conflict_ratio <= 0.25:
        gap_trap_probability -= 6.0
    gap_trap_probability = round(_clamp(gap_trap_probability, 5.0, 80.0), 1)

    market_strength = _number(market_strength)
    strength_confidence = _number(strength_confidence)
    trend_quality = _number(trend_quality)
    sentiment_confidence = _number(sentiment_confidence)
    overnight_confidence = int(round(_clamp(
        gift_confidence * 0.20
        + _number(opening.get("market_confidence")) * 0.20
        + _number(prediction.get("market_confidence")) * 0.17
        + sentiment_confidence * 0.13
        + strength_confidence * 0.12
        + trend_quality * 0.10
        + leader * 0.08
        - conflict_ratio * 17.0
    )))

    ranges = [
        _number(item.get("range_percent"))
        for item in (nifty, banknifty)
        if item.get("available") and not item.get("stale")
    ]
    overnight_volatility = (
        (sum(ranges) / len(ranges)) + abs(gift_change or 0.0)
        if ranges else None
    )
    high_risk = (
        str(brain.get("risk_mode", "HIGH")) == "HIGH"
        or gap_trap_probability >= 55
        or (overnight_volatility is not None and overnight_volatility >= 2.5)
    )
    if not high_risk and overnight_confidence >= 72 and gap_trap_probability < 35:
        risk_level = "LOW"
    elif not high_risk and overnight_confidence >= 56:
        risk_level = "MODERATE"
    else:
        risk_level = "HIGH"

    usable_bias = leader >= 38 and margin >= 6
    overnight_bias = directional_bias if usable_bias else "NEUTRAL"
    tradeable = (
        overnight_bias in {"BULLISH", "BEARISH"}
        and overnight_confidence >= 58
        and margin >= 6
        and gap_trap_probability < 55
        and risk_level != "HIGH"
    )
    core_sources = sum(
        (
            gift_ok,
            bool(nifty.get("available")) and not bool(nifty.get("stale")),
            bool(banknifty.get("available")) and not bool(banknifty.get("stale")),
        )
    )
    model_sources = sum((opening_ok, prediction_ok, sentiment_ok))
    degraded = core_sources < 2 or model_sources < 1
    if degraded:
        overnight_confidence = min(overnight_confidence, 55)
        tradeable = False
    preferred = "BUY" if tradeable and overnight_bias == "BULLISH" else "SELL" if tradeable else "NO TRADE"
    published_bias = overnight_bias if tradeable else "NO TRADE"

    return {
        "generated_at": now.isoformat(),
        "analysis_date": now.date().isoformat(),
        "session_state": "POST_MARKET" if now.time() >= MARKET_CLOSE else "PRE_CLOSE_SNAPSHOT",
        "overnight_bias": published_bias,
        "market_bias": published_bias,
        "next_session_opening_bias": published_bias,
        "preferred_next_session_direction": preferred,
        "recommendation": preferred,
        "bullish_probability": bullish_probability,
        "bearish_probability": bearish_probability,
        "sideways_probability": sideways_probability,
        "gap_up_probability": gap_up,
        "gap_down_probability": gap_down,
        "flat_opening_probability": flat_open,
        "trend_continuation_probability": continuation_probability,
        "trend_reversal_probability": reversal_probability,
        "overnight_confidence": overnight_confidence,
        "market_confidence": overnight_confidence,
        "overnight_risk_level": risk_level,
        "risk_level": risk_level,
        "gap_trap_probability": gap_trap_probability,
        "gap_trap_warning": gap_trap_probability >= 40,
        "overnight_volatility_percent": round(overnight_volatility, 2) if overnight_volatility is not None else None,
        "market_strength": round(market_strength, 1),
        "trend_quality": round(trend_quality, 1),
        "tradeable": tradeable,
        "degraded": degraded,
        "reason": "Overnight evidence is aligned" if tradeable else "Evidence is incomplete, conflicting, or below the safe confidence threshold",
        "evidence": [{"source": source, "direction": direction} for source, direction in evidence],
        "index_outlooks": {
            "NIFTY": build_index_outlook("NIFTY", nifty, {"bullish": bullish_probability, "bearish": bearish_probability, "sideways": sideways_probability, "gap_up": gap_up, "gap_down": gap_down, "gap_trap": gap_trap_probability, "continuation": continuation_probability, "reversal": reversal_probability}, str(brain.get("market_regime", "UNKNOWN")), market_strength, risk_level, preferred),
            "BANKNIFTY": build_index_outlook("BANKNIFTY", banknifty, {"bullish": bullish_probability, "bearish": bearish_probability, "sideways": sideways_probability, "gap_up": gap_up, "gap_down": gap_down, "gap_trap": gap_trap_probability, "continuation": continuation_probability, "reversal": reversal_probability}, str(brain.get("market_regime", "UNKNOWN")), market_strength, risk_level, preferred),
        },
        "inputs": {
            "gift_nifty": {
                "available": gift_ok,
                "stale": gift_stale,
                "price": round(gift_price, 2) if gift_ok else None,
                "direction": gift_direction,
                "regime": gift_regime,
                "overnight_change_percent": round(gift_change, 2) if gift_change is not None else None,
                "overnight_movement": overnight_movement,
                "strength": round(gift_strength, 1),
                "confidence": round(gift_confidence, 1),
            },
            "nifty_session": nifty,
            "banknifty_session": banknifty,
            "market_regime": brain.get("market_regime", nifty.get("regime", "UNKNOWN")),
            "market_sentiment": {"available": sentiment_ok and sentiment_confidence_ok, "value": str(sentiment), "confidence": round(sentiment_confidence, 1)},
            "market_prediction": {"available": prediction_ok, "bias": prediction.get("market_bias", "NO TRADE")},
            "market_strength": {"available": market_strength_ok, "confidence_available": strength_confidence_ok},
            "trend_quality": {"available": trend_quality_ok},
            "market_brain": {"available": brain_ok},
            "gold": gold,
            "silver": silver,
            "mcx": mcx,
            "global_markets": {"available": False, "reason": "No project data source configured"},
        },
    }


def analyze_overnight(force_refresh: bool = False) -> dict[str, Any]:
    """Generate or return a request-efficient overnight analysis."""
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
        try:
            result = _build_analysis(now)
        except Exception:
            LOGGER.exception("Overnight analysis degraded after an input failure")
            result = _degraded(now, "One or more overnight inputs are unavailable")
        _analysis_cache = result
        _analysis_cache_time = monotonic_now
        _analysis_cache_day = now.date()
        return deepcopy(result)


def get_overnight_bias() -> str:
    return str(analyze_overnight().get("overnight_bias", "NO TRADE"))


def get_overnight_probabilities() -> dict[str, float]:
    result = analyze_overnight()
    return {
        "bullish": _number(result.get("bullish_probability")),
        "bearish": _number(result.get("bearish_probability")),
        "sideways": _number(result.get("sideways_probability")),
        "gap_up": _number(result.get("gap_up_probability")),
        "gap_down": _number(result.get("gap_down_probability")),
        "flat": _number(result.get("flat_opening_probability")),
    }


def get_overnight_confidence() -> int:
    return int(_number(analyze_overnight().get("overnight_confidence")))


def get_overnight_risk() -> str:
    return str(analyze_overnight().get("overnight_risk_level", "HIGH"))


def get_trend_continuation_probability() -> float:
    return _number(analyze_overnight().get("trend_continuation_probability"))


def get_trend_reversal_probability() -> float:
    return _number(analyze_overnight().get("trend_reversal_probability"))


def is_overnight_tradeable() -> bool:
    return bool(analyze_overnight().get("tradeable", False))


def _display(value: Any, suffix: str = "") -> str:
    return "Unavailable" if value is None else f"{value}{suffix}"


def _freshness(value: Mapping[str, Any]) -> str:
    if not value.get("available"):
        return "UNAVAILABLE"
    if value.get("stale"):
        return "STALE"
    return "AVAILABLE"


def format_overnight_report(analysis: Mapping[str, Any] | None = None) -> str:
    data = dict(analysis or analyze_overnight())
    from telegram_service import format_index_outlook_report
    return format_index_outlook_report(data, night=True)
    # Normal delivery is compact; detailed fields remain available to admins.
    inputs = data.get("inputs", {}) if isinstance(data.get("inputs"), Mapping) else {}
    gift = inputs.get("gift_nifty", {}) if isinstance(inputs.get("gift_nifty"), Mapping) else {}
    nifty = inputs.get("nifty_session", {}) if isinstance(inputs.get("nifty_session"), Mapping) else {}
    bank = inputs.get("banknifty_session", {}) if isinstance(inputs.get("banknifty_session"), Mapping) else {}
    gold = inputs.get("gold", {}) if isinstance(inputs.get("gold"), Mapping) else {}
    silver = inputs.get("silver", {}) if isinstance(inputs.get("silver"), Mapping) else {}
    mcx = inputs.get("mcx", {}) if isinstance(inputs.get("mcx"), Mapping) else {}
    warning = "YES — confirm the opening range" if data.get("gap_trap_warning") else "No material warning"
    return (
        "🌙 SHIVAY AI — OVERNIGHT MARKET OUTLOOK\n\n"
        f"Overnight Bias: {data.get('overnight_bias', 'NO TRADE')}\n"
        f"Preferred Next Session: {data.get('preferred_next_session_direction', 'NO TRADE')}\n"
        f"Confidence: {data.get('overnight_confidence', 0)}/100 | Risk: {data.get('overnight_risk_level', 'HIGH')}\n\n"
        "PROBABILITIES\n"
        f"Bullish: {data.get('bullish_probability', 0)}% | Bearish: {data.get('bearish_probability', 0)}%\n"
        f"Sideways: {data.get('sideways_probability', 0)}%\n"
        f"Gap Up: {data.get('gap_up_probability', 0)}% | Gap Down: {data.get('gap_down_probability', 0)}%\n"
        f"Flat Open: {data.get('flat_opening_probability', 0)}%\n"
        f"Trend Continuation: {data.get('trend_continuation_probability', 0)}%\n"
        f"Trend Reversal: {data.get('trend_reversal_probability', 0)}%\n\n"
        "CLOSING & OVERNIGHT INPUTS\n"
        f"GIFT NIFTY [{_freshness(gift)}]: {gift.get('direction', 'Unavailable') if gift.get('available') else 'Unavailable'} | Movement: {_display(gift.get('overnight_change_percent'), '%')}\n"
        f"NIFTY [{_freshness(nifty)}] Close: {_display(nifty.get('close'))} | High/Low: {_display(nifty.get('high'))}/{_display(nifty.get('low'))}\n"
        f"BANKNIFTY [{_freshness(bank)}] Close: {_display(bank.get('close'))} | High/Low: {_display(bank.get('high'))}/{_display(bank.get('low'))}\n"
        f"Gold: {gold.get('direction', 'UNAVAILABLE')} | Silver: {silver.get('direction', 'UNAVAILABLE')} | MCX: {mcx.get('direction', 'UNAVAILABLE')}\n\n"
        f"Gap-trap Probability: {data.get('gap_trap_probability', 0)}%\n"
        f"Gap-trap Warning: {warning}\n\n"
        f"Recommendation: {data.get('recommendation', 'NO TRADE')}\n"
        f"Reason: {data.get('reason', 'Insufficient evidence')}\n\n"
        "Probabilities are estimates, not guarantees. Reassess fresh data before the next session."
    )


def _meaningful_change(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool:
    if not previous:
        return True
    for field in ("overnight_bias", "preferred_next_session_direction", "overnight_risk_level", "gap_trap_warning"):
        if previous.get(field) != current.get(field):
            return True
    for field in ("bullish_probability", "bearish_probability", "gap_up_probability", "gap_down_probability"):
        if abs(_number(current.get(field)) - _number(previous.get(field))) >= 7.0:
            return True
    return abs(_number(current.get("overnight_confidence")) - _number(previous.get("overnight_confidence"))) >= 8.0


async def send_overnight_report(
    application: Any,
    analysis: Mapping[str, Any] | None = None,
    force_refresh: bool = False,
) -> bool:
    """Send once after close, allowing one materially changed refresh."""
    if application is None or not hasattr(application, "bot"):
        return False
    now = _now()
    if now.weekday() >= 5 or now.time() < MARKET_CLOSE:
        return False
    data = dict(analysis) if analysis is not None else await asyncio.to_thread(analyze_overnight, force_refresh)
    with _CACHE_LOCK:
        state = dict(_sent_state.get(now.date(), {"count": 0, "analysis": None, "in_flight": False}))
        if state.get("in_flight") or state["count"] >= 2:
            return False
        if state["count"] >= 1 and not _meaningful_change(state.get("analysis"), data):
            return False
        state["in_flight"] = True
        _sent_state[now.date()] = state
    try:
        users = await asyncio.to_thread(recipients, "ALL")
    except Exception:
        LOGGER.exception("Overnight-report recipients are unavailable")
        with _CACHE_LOCK:
            _sent_state[now.date()]["in_flight"] = False
        return False
    delivered = False
    text = format_overnight_report(data)
    for user in users:
        try:
            await application.bot.send_message(chat_id=int(user["id"]), text=text)
            delivered = True
        except asyncio.CancelledError:
            with _CACHE_LOCK:
                _sent_state[now.date()]["in_flight"] = False
            raise
        except Exception:
            LOGGER.warning("Overnight report delivery failed")
    with _CACHE_LOCK:
        if delivered:
            _sent_state[now.date()] = {"count": state["count"] + 1, "analysis": deepcopy(data), "in_flight": False}
            for day in list(_sent_state):
                if day < now.date():
                    _sent_state.pop(day, None)
        else:
            _sent_state[now.date()]["in_flight"] = False
    return delivered
