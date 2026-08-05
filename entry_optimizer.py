from breakout import breakout_filter
from confidence_engine import get_confidence, is_trade_allowed as is_confidence_trade_allowed
from indicators import atr, volume_spike
from market_sentiment import can_trade_by_sentiment
from market_strength import get_market_strength, is_market_tradeable
from pullback import pullback_filter
from trade_validator import validate_buy, validate_sell, validate_trade


_entry_analysis = {
    "best_entry": 0.0,
    "entry_zone": (0.0, 0.0),
    "entry_confidence": 0,
    "entry_probability": 0.0,
    "entry_valid": False,
}


def optimize_entry(market, score_data, trade_plan=None):
    global _entry_analysis

    close = [float(value) for value in market.get("close", [])]
    high = [float(value) for value in market.get("high", [])]
    low = [float(value) for value in market.get("low", [])]
    volume = [float(value) for value in market.get("volume", [])]
    price = float(score_data.get("price", market.get("price", 0)))

    if (
        len(close) < 200
        or len(high) < 200
        or len(low) < 200
        or len(volume) < 200
        or price < 200
    ):
        _entry_analysis = {
            "best_entry": 0.0,
            "entry_zone": (0.0, 0.0),
            "entry_confidence": 0,
            "entry_probability": 0.0,
            "entry_valid": False,
        }
        return _entry_analysis.copy()

    atr_value = float(score_data.get("atr", atr(high, low, close)))
    e20 = float(score_data.get("ema20", 0))
    e50 = float(score_data.get("ema50", 0))
    e200 = float(score_data.get("ema200", 0))
    support = float(score_data.get("support", 0))
    resistance = float(score_data.get("resistance", 0))
    adx_value = float(score_data.get("adx", 0))
    setup = str(score_data.get("setup", ""))

    if atr_value <= 0 or adx_value < 25 or not volume_spike(volume):
        _entry_analysis = {
            "best_entry": 0.0,
            "entry_zone": (0.0, 0.0),
            "entry_confidence": 0,
            "entry_probability": 0.0,
            "entry_valid": False,
        }
        return _entry_analysis.copy()

    bullish = e20 > e50 > e200 and price > e20
    bearish = e20 < e50 < e200 and price < e20
    breakout_ok = breakout_filter(high, low, close, volume)
    pullback_ok = pullback_filter(high, low, close)
    market_ok = (
        get_market_strength() >= 70
        and is_market_tradeable()
        and can_trade_by_sentiment()
        and is_confidence_trade_allowed()
        and get_confidence() >= 80
    )

    if not market_ok or (bullish == bearish):
        _entry_analysis = {
            "best_entry": 0.0,
            "entry_zone": (0.0, 0.0),
            "entry_confidence": 0,
            "entry_probability": 0.0,
            "entry_valid": False,
        }
        return _entry_analysis.copy()

    if bullish:
        side = "BUY"
        trend_valid = validate_buy(score_data, trade_plan)
        recent_level = max(high[-21:-1])
        retest_valid = low[-1] <= recent_level + atr_value * 0.25 and price > recent_level
        if "Confirmed Pullback" in setup or pullback_ok:
            best_entry = max(e20, price - atr_value * 0.25)
        else:
            best_entry = max(recent_level + atr_value * 0.10, e20)
        stop_reference = max(support, best_entry - atr_value * 1.25)
        target_reference = resistance
        late_entry = price - best_entry > atr_value * 0.75
    else:
        side = "SELL"
        trend_valid = validate_sell(score_data, trade_plan)
        recent_level = min(low[-21:-1])
        retest_valid = high[-1] >= recent_level - atr_value * 0.25 and price < recent_level
        if "Confirmed Pullback" in setup or pullback_ok:
            best_entry = min(e20, price + atr_value * 0.25)
        else:
            best_entry = min(recent_level - atr_value * 0.10, e20)
        stop_reference = min(resistance, best_entry + atr_value * 1.25)
        target_reference = support
        late_entry = best_entry - price > atr_value * 0.75

    risk = abs(best_entry - stop_reference)
    reward = abs(target_reference - best_entry)
    risk_reward = reward / risk if risk > 0 else 0.0
    fresh_setup = (
        (breakout_ok and retest_valid)
        or pullback_ok
        or "Confirmed Pullback" in setup
    )
    validator_result = validate_trade(score_data, trade_plan)
    validator_ok = bool(validator_result["valid"]) if trade_plan is not None else trend_valid

    entry_valid = (
        trend_valid
        and validator_ok
        and fresh_setup
        and not late_entry
        and risk_reward >= 2.0
    )

    entry_confidence = int(
        min(
            100,
            max(
                0,
                round(
                    (float(score_data.get("score", 0)) * 0.35)
                    + (get_confidence() * 0.35)
                    + (min(100.0, adx_value * 2.5) * 0.15)
                    + (get_market_strength() * 0.15)
                    - (15 if late_entry else 0)
                ),
            ),
        )
    )
    entry_probability = round(entry_confidence * 0.85 if entry_valid else 0.0, 1)
    zone_width = atr_value * 0.20

    _entry_analysis = {
        "side": side if entry_valid else "NO TRADE",
        "best_entry": round(best_entry, 2) if entry_valid else 0.0,
        "entry_zone": (
            round(best_entry - zone_width, 2),
            round(best_entry + zone_width, 2),
        ) if entry_valid else (0.0, 0.0),
        "entry_confidence": entry_confidence if entry_valid else 0,
        "entry_probability": entry_probability,
        "entry_timing": "IMMEDIATE" if entry_valid and retest_valid else "WAIT FOR RETEST",
        "entry_delay": 0 if entry_valid and retest_valid else 1,
        "confirmation_candle": bool(retest_valid),
        "entry_valid": entry_valid,
    }

    return _entry_analysis.copy()


def get_best_entry():
    return _entry_analysis["best_entry"]


def get_entry_zone():
    return _entry_analysis["entry_zone"]


def get_entry_confidence():
    return _entry_analysis["entry_confidence"]


def get_entry_probability():
    return _entry_analysis["entry_probability"]


def is_entry_valid():
    return bool(_entry_analysis["entry_valid"])
