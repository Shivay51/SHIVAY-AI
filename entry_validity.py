"""Late-entry and delivery-time validation for confirmed 15-minute setups."""
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


def _timestamp(value: Any) -> datetime | None:
    try:
        return parse_timestamp(value)
    except Exception:
        return None


def evaluate_entry_validity(market: Mapping[str, Any], side: str, trade_plan: Mapping[str, Any], score_data: Mapping[str, Any], current_price: float | None = None, now: datetime | None = None) -> dict[str, Any]:
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
    highs = [_number(value) for value in market.get("high", [])]
    lows = [_number(value) for value in market.get("low", [])]
    day_high = _number(market.get("day_high"), max(highs[-75:] or [price]))
    day_low = _number(market.get("day_low"), min(lows[-75:] or [price]))
    swing_high = max(highs[-21:-1] or [day_high])
    swing_low = min(lows[-21:-1] or [day_low])
    breakout_level = swing_high if side == "BUY" else swing_low
    setup = str(score_data.get("setup") or "")
    references = [value for value in (ema20, vwap) if value > 0]
    if "Pullback" not in setup and breakout_level > 0:
        references.append(breakout_level)
    ideal_entry = (max(references) if side == "BUY" else min(references)) if references else price
    quality = assess_market_data(market, now=now)
    candles = market.get("candles")
    candle_time = _timestamp(candles[-1].get("timestamp")) if isinstance(candles, list) and candles and isinstance(candles[-1], Mapping) else None
    candle_time = candle_time or _timestamp(market.get("timestamp"))
    age_seconds = (now - candle_time.astimezone(timezone.utc)).total_seconds() if candle_time else float("inf")
    timeframe = max(15, int(getattr(config, "PRIMARY_TIMEFRAME_MINUTES", 15)))
    max_age = max(1, int(getattr(config, "MAX_SETUP_AGE_CANDLES", 2))) * timeframe * 60
    risk = abs(price - stop)
    reward = target2 - price if side == "BUY" else price - target2
    rr = reward / risk if risk > 0 else 0.0
    entry_move = price - entry if side == "BUY" else entry - price
    t1_distance = target1 - entry if side == "BUY" else entry - target1
    t1_travelled = entry_move / t1_distance if t1_distance > 0 else 1.0
    atr_distance = abs(price - ideal_entry) / atr_value if atr_value > 0 else float("inf")
    near_extreme = day_high - price <= atr_value * .25 if side == "BUY" else price - day_low <= atr_value * .25
    min_rr = max(1.5, float(getattr(config, "MIN_RISK_REWARD", 1.5)))
    reasons: list[str] = []
    if side not in {"BUY", "SELL"}: reasons.append("invalid_direction")
    if not quality.get("valid"): reasons.append("unsafe_market_data")
    if market.get("is_delayed") or not market.get("is_live"): reasons.append("delayed_data")
    if age_seconds > max_age: reasons.append("setup_expired")
    if min(price, entry, stop, target1, target2, atr_value) <= 0: reasons.append("invalid_trade_plan")
    if entry_move > atr_value * .65: reasons.append("price_moved_from_entry")
    if atr_distance > 1.35: reasons.append("overextended_from_ideal_entry")
    if t1_travelled >= .65: reasons.append("target1_substantially_travelled")
    if rr < min_rr: reasons.append("risk_reward_deteriorated")
    continuation = "Breakout" in setup and atr_distance <= .8 and rr >= min_rr
    if near_extreme and not continuation: reasons.append("too_close_to_day_extreme")
    if side == "BUY" and swing_high > price and swing_high - price < risk * 1.25: reasons.append("resistance_too_close")
    if side == "SELL" and swing_low < price and price - swing_low < risk * 1.25: reasons.append("support_too_close")
    valid_until = now + timedelta(minutes=max(5, int(getattr(config, "ENTRY_VALIDITY_MINUTES", 18))))
    return {"valid": not reasons, "side": side, "reasons": reasons, "current_risk_reward": round(rr, 2), "valid_until": valid_until.isoformat(), "ideal_entry": round(ideal_entry, 2), "atr_distance_from_ideal": round(atr_distance, 2) if atr_distance != float("inf") else None, "target1_travelled_percent": round(max(0.0, t1_travelled) * 100, 1), "setup_age_seconds": round(age_seconds, 1) if age_seconds != float("inf") else None, "remaining_realistic_reward": round(max(0.0, reward), 2), "near_day_extreme": near_extreme, "continuation_exception": continuation, "invalidation_condition": f"{side} setup invalid if price crosses stop {stop:.2f} or data/remaining reward deteriorates"}


def revalidate_signal(signal: Mapping[str, Any], current_price: float, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    market = signal.get("market_data") if isinstance(signal.get("market_data"), Mapping) else signal
    side = "SELL" if "SELL" in str(signal.get("decision", signal.get("side", ""))).upper() else "BUY"
    result = evaluate_entry_validity(market, side, signal, signal, current_price=current_price, now=now)
    expiry = _timestamp(signal.get("valid_until"))
    if expiry is not None:
        result["valid_until"] = expiry.isoformat()
        if now.astimezone(timezone.utc) > expiry.astimezone(timezone.utc):
            result["reasons"] = list(dict.fromkeys([*result["reasons"], "signal_expired"]))
            result["valid"] = False
    return result
