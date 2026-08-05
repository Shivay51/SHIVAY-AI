"""Late-entry, overextension, and delivery-time validation for 15-minute setups."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import config
from data_quality import assess_market_data, parse_timestamp


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _pct(distance: float, price: float) -> float:
    return round(distance / price * 100.0, 3) if price > 0 else 0.0


def evaluate_entry_validity(
    market: Mapping[str, Any],
    side: str,
    trade_plan: Mapping[str, Any],
    score_data: Mapping[str, Any],
    current_price: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a complete, balanced entry-validity assessment."""
    now = now or datetime.now(timezone.utc)
    side = str(side).upper()
    price = _number(current_price, _number(market.get("price")))
    entry = _number(trade_plan.get("entry"), price)
    stop = _number(trade_plan.get("sl"))
    target1 = _number(trade_plan.get("target1"))
    target2 = _number(trade_plan.get("target2"))
    atr_value = _number(score_data.get("atr"))
    ema20 = _number(score_data.get("ema20"))
    vwap = _number(score_data.get("vwap"))
    setup = str(score_data.get("setup", ""))
    highs = [_number(value) for value in market.get("high", [])]
    lows = [_number(value) for value in market.get("low", [])]
    day_high = _number(market.get("day_high"), max(highs[-75:], default=price))
    day_low = _number(market.get("day_low"), min(lows[-75:], default=price))
    swing_high = max(highs[-21:-1], default=day_high)
    swing_low = min(lows[-21:-1], default=day_low)
    breakout_level = swing_high if side == "BUY" else swing_low
    if "Pullback" in setup:
        references = [value for value in (ema20, vwap) if value > 0]
    else:
        references = [value for value in (ema20, vwap, breakout_level) if value > 0]
    ideal_entry = (max(references) if side == "BUY" else min(references)) if references else price

    quality = assess_market_data(market, now=now)
    candle_stamp = None
    candles = market.get("candles")
    if isinstance(candles, list) and candles:
        candle_stamp = parse_timestamp(candles[-1].get("timestamp"))
    candle_stamp = candle_stamp or parse_timestamp(market.get("timestamp"))
    setup_age_seconds = (now - candle_stamp.astimezone(timezone.utc)).total_seconds() if candle_stamp else float("inf")
    timeframe = max(15, int(getattr(config, "PRIMARY_TIMEFRAME_MINUTES", 15)))
    max_setup_age = max(1, int(getattr(config, "MAX_SETUP_AGE_CANDLES", 2))) * timeframe * 60

    risk = abs(price - stop)
    reward = (target2 - price) if side == "BUY" else (price - target2)
    current_rr = reward / risk if risk > 0 else 0.0
    entry_move = (price - entry) if side == "BUY" else (entry - price)
    t1_distance = (target1 - entry) if side == "BUY" else (entry - target1)
    t1_travelled = entry_move / t1_distance if t1_distance > 0 else 1.0
    atr_from_ideal = abs(price - ideal_entry) / atr_value if atr_value > 0 else 99.0
    near_extreme = (day_high - price <= atr_value * 0.25) if side == "BUY" else (price - day_low <= atr_value * 0.25)
    continuation = (
        "Breakout" in setup
        and ((side == "BUY" and price > swing_high) or (side == "SELL" and price < swing_low))
        and atr_from_ideal <= 0.80
        and current_rr >= max(1.5, float(getattr(config, "MIN_RISK_REWARD", 1.2)))
    )

    reasons: list[str] = []
    if side not in {"BUY", "SELL"}: reasons.append("invalid_direction")
    if not quality.get("valid"): reasons.append("unsafe_market_data")
    if market.get("is_delayed") or not market.get("is_live"): reasons.append("delayed_data")
    if setup_age_seconds > max_setup_age: reasons.append("setup_expired")
    if min(price, entry, stop, target1, target2, atr_value) <= 0: reasons.append("invalid_trade_plan")
    if entry_move > atr_value * 0.65: reasons.append("price_moved_from_entry")
    if atr_from_ideal > 1.35: reasons.append("overextended_from_ideal_entry")
    if t1_travelled >= 0.65: reasons.append("target1_substantially_travelled")
    if current_rr < max(1.5, float(getattr(config, "MIN_RISK_REWARD", 1.2))): reasons.append("risk_reward_deteriorated")
    if near_extreme and not continuation: reasons.append("too_close_to_day_extreme")
    if side == "BUY" and swing_high > price and swing_high - price < risk * 1.25: reasons.append("resistance_too_close")
    if side == "SELL" and swing_low < price and price - swing_low < risk * 1.25: reasons.append("support_too_close")

    valid_minutes = max(5, int(getattr(config, "ENTRY_VALIDITY_MINUTES", 18)))
    valid_until = now + timedelta(minutes=valid_minutes)
    return {
        "valid": not reasons, "side": side, "reasons": reasons,
        "distance_from_day_high": round(day_high - price, 2), "distance_from_day_low": round(price - day_low, 2),
        "distance_from_swing_high": round(swing_high - price, 2), "distance_from_swing_low": round(price - swing_low, 2),
        "distance_from_vwap": round(price - vwap, 2), "distance_from_ema20": round(price - ema20, 2),
        "distance_from_breakout_level": round(price - breakout_level, 2), "distance_travelled_since_confirmation": round(max(0.0, entry_move), 2),
        "atr_distance_from_ideal": round(atr_from_ideal, 2), "remaining_realistic_reward": round(max(0.0, reward), 2),
        "current_risk_reward": round(current_rr, 2), "target1_travelled_percent": round(max(0.0, t1_travelled) * 100, 1),
        "near_day_extreme": near_extreme, "continuation_exception": continuation,
        "setup_age_seconds": None if setup_age_seconds == float("inf") else round(setup_age_seconds, 1),
        "valid_until": valid_until.isoformat(), "ideal_entry": round(ideal_entry, 2),
        "invalidation_condition": f"{side} setup invalid if price crosses stop {stop:.2f} or freshness/remaining reward deteriorates",
        "distance_from_day_high_percent": _pct(day_high - price, price), "distance_from_day_low_percent": _pct(price - day_low, price),
    }


def revalidate_signal(signal: Mapping[str, Any], current_price: float, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    market = signal.get("market_data") if isinstance(signal.get("market_data"), Mapping) else signal
    side = "SELL" if "SELL" in str(signal.get("decision", signal.get("side", ""))).upper() else "BUY"
    result = evaluate_entry_validity(market, side, signal, signal, current_price=current_price, now=now)
    original_expiry = parse_timestamp(signal.get("valid_until"))
    if original_expiry is not None:
        result["valid_until"] = original_expiry.isoformat()
        if now.astimezone(timezone.utc) > original_expiry.astimezone(timezone.utc):
            result["reasons"] = list(dict.fromkeys([*result.get("reasons", []), "signal_expired"]))
            result["valid"] = False
    return result
