import time

from gift_nifty import (
    get_gift_nifty_confidence,
    get_gift_nifty_direction,
    get_gift_nifty_regime,
    get_gift_nifty_strength,
    is_gift_nifty_tradeable,
)
from market_brain import (
    _get_market_analysis,
    get_market_confidence as get_broad_market_confidence,
    get_market_direction as get_broad_market_direction,
    get_market_regime,
    get_market_strength as get_broad_market_strength,
    is_market_tradeable,
)
from market_cache import load_market_cache


_prediction_cache = None
_prediction_cache_time = 0.0
_prediction_cache_ttl = 300


def _get_prediction():
    global _prediction_cache
    global _prediction_cache_time

    now = time.time()

    if (
        _prediction_cache is not None
        and now - _prediction_cache_time < _prediction_cache_ttl
    ):
        return _prediction_cache

    gift_strength = int(get_gift_nifty_strength())
    gift_confidence = int(get_gift_nifty_confidence())
    gift_direction = str(get_gift_nifty_direction())
    gift_regime = str(get_gift_nifty_regime())
    gift_tradeable = bool(is_gift_nifty_tradeable())

    market_strength = int(get_broad_market_strength())
    market_confidence = int(get_broad_market_confidence())
    market_direction = str(get_broad_market_direction())
    market_regime = str(get_market_regime())
    market_tradeable = bool(is_market_tradeable())
    market_analysis = _get_market_analysis()
    volatility = str(market_analysis.get("volatility_mode", "UNKNOWN"))

    cache = load_market_cache() or {}
    cache_direction = str(cache.get("market_direction", "UNKNOWN"))
    cache_strength = int(cache.get("market_strength", 0))

    gap_up_score = 25.0
    gap_down_score = 25.0
    flat_score = 50.0

    if gift_direction == "BULLISH":
        gap_up_score += 20
        flat_score -= 8
    elif gift_direction == "BEARISH":
        gap_down_score += 20
        flat_score -= 8

    if market_direction == "BULLISH":
        gap_up_score += 12
        flat_score -= 5
    elif market_direction == "BEARISH":
        gap_down_score += 12
        flat_score -= 5

    if "BULLISH" in cache_direction:
        gap_up_score += 6
    elif "BEARISH" in cache_direction:
        gap_down_score += 6

    if gift_regime in ("STRONG BULL", "STRONG BEAR"):
        flat_score -= 8
    elif gift_regime == "SIDEWAYS":
        flat_score += 18

    if market_regime == "SIDEWAYS":
        flat_score += 12

    strength_factor = min(20.0, gift_strength * 0.12)
    confidence_factor = min(15.0, gift_confidence * 0.10)

    if gift_direction == "BULLISH":
        gap_up_score += strength_factor + confidence_factor
    elif gift_direction == "BEARISH":
        gap_down_score += strength_factor + confidence_factor

    if market_strength >= 70 and market_confidence >= 70:
        if market_direction == "BULLISH":
            gap_up_score += 8
        elif market_direction == "BEARISH":
            gap_down_score += 8

    if cache_strength < 50:
        flat_score += 6

    direction_conflict = (
        gift_direction in ("BULLISH", "BEARISH")
        and market_direction in ("BULLISH", "BEARISH")
        and gift_direction != market_direction
    )

    low_quality = (
        gift_confidence < 65
        or market_confidence < 65
        or not gift_tradeable
        or not market_tradeable
    )

    high_volatility = volatility == "HIGH RISK"

    gap_trap_probability = 8.0

    if direction_conflict:
        gap_trap_probability += 28

    if low_quality:
        gap_trap_probability += 20

    if high_volatility:
        gap_trap_probability += 15

    if gift_regime == "SIDEWAYS" or market_regime == "SIDEWAYS":
        gap_trap_probability += 12

    if gift_strength >= 85 and market_strength >= 75 and not direction_conflict:
        gap_trap_probability -= 8

    gap_trap_probability = max(5.0, min(75.0, gap_trap_probability))

    if gap_trap_probability >= 40:
        flat_score += 15
        gap_up_score -= 6
        gap_down_score -= 6

    gap_up_score = max(5.0, gap_up_score)
    gap_down_score = max(5.0, gap_down_score)
    flat_score = max(10.0, flat_score)

    total_score = gap_up_score + gap_down_score + flat_score
    gap_up_probability = round((gap_up_score / total_score) * 100, 1)
    gap_down_probability = round((gap_down_score / total_score) * 100, 1)
    flat_probability = round(100.0 - gap_up_probability - gap_down_probability, 1)

    if gap_up_probability > gap_down_probability and gap_up_probability > flat_probability:
        market_bias = "BULLISH"
        bullish_probability = gap_up_probability
        bearish_probability = gap_down_probability
    elif gap_down_probability > gap_up_probability and gap_down_probability > flat_probability:
        market_bias = "BEARISH"
        bullish_probability = gap_up_probability
        bearish_probability = gap_down_probability
    else:
        market_bias = "NEUTRAL"
        bullish_probability = gap_up_probability
        bearish_probability = gap_down_probability

    opening_strength = int(
        max(
            0,
            min(
                100,
                round(
                    max(bullish_probability, bearish_probability)
                    + (gift_strength * 0.20)
                    + (market_strength * 0.15)
                    - (gap_trap_probability * 0.25)
                ),
            ),
        )
    )

    prediction_confidence = int(
        max(
            0,
            min(
                100,
                round(
                    (gift_confidence * 0.45)
                    + (market_confidence * 0.35)
                    + (opening_strength * 0.20)
                    - (gap_trap_probability * 0.20)
                ),
            ),
        )
    )

    if (
        gap_trap_probability >= 40
        or prediction_confidence < 65
        or market_bias == "NEUTRAL"
    ):
        risk_level = "HIGH"
    elif prediction_confidence >= 80 and opening_strength >= 75:
        risk_level = "LOW"
    else:
        risk_level = "MODERATE"

    _prediction_cache = {
        "market_bias": market_bias,
        "gap_up_probability": gap_up_probability,
        "gap_down_probability": gap_down_probability,
        "flat_opening_probability": flat_probability,
        "bullish_probability": bullish_probability,
        "bearish_probability": bearish_probability,
        "market_confidence": prediction_confidence,
        "opening_strength": opening_strength,
        "risk_level": risk_level,
        "volatility": volatility,
        "gap_trap_probability": round(gap_trap_probability, 1),
        "gift_nifty_direction": gift_direction,
        "gift_nifty_regime": gift_regime,
        "gift_nifty_strength": gift_strength,
        "market_direction": market_direction,
        "market_regime": market_regime,
    }
    _prediction_cache_time = now

    return _prediction_cache


def predict_opening():
    return _get_prediction().copy()


def get_open_probability():
    prediction = _get_prediction()

    return {
        "gap_up": prediction["gap_up_probability"],
        "gap_down": prediction["gap_down_probability"],
        "flat": prediction["flat_opening_probability"],
    }


def get_market_bias():
    return _get_prediction()["market_bias"]


def get_market_confidence():
    return _get_prediction()["market_confidence"]


def get_gap_probability():
    prediction = _get_prediction()

    return {
        "gap_up": prediction["gap_up_probability"],
        "gap_down": prediction["gap_down_probability"],
        "gap_trap": prediction["gap_trap_probability"],
    }


def get_risk_level():
    return _get_prediction()["risk_level"]
