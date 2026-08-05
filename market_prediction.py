import time

from gift_nifty import (
    get_gift_nifty_confidence,
    get_gift_nifty_direction,
    get_gift_nifty_strength,
    is_gift_nifty_tradeable,
)
from gift_nifty_prediction import predict_opening
from market_brain import (
    _get_market_analysis,
    get_market_confidence as get_broad_market_confidence,
    get_market_direction as get_broad_market_direction,
    get_market_strength as get_broad_market_strength,
    is_market_tradeable,
)
from chandelier_exit import calculate_timeframe_chandelier
from data import get_market_data


_market_prediction_cache = None
_market_prediction_time = 0.0
_market_prediction_ttl = 300


def _chandelier_context():
    result = {}
    for label, symbol in (("nifty", "NIFTY FUT"), ("banknifty", "BANKNIFTY FUT")):
        try:
            market = get_market_data(symbol)
            if not market:
                result[label] = {"available": False}
                continue
            thirty = calculate_timeframe_chandelier(market, 30)
            sixty = calculate_timeframe_chandelier(market, 60)
            result[label] = {"available": bool(thirty.get("valid") or sixty.get("valid")), "30m": thirty, "60m": sixty}
        except Exception:
            result[label] = {"available": False}
    return result


def _get_prediction():
    global _market_prediction_cache
    global _market_prediction_time

    now = time.time()

    if (
        _market_prediction_cache is not None
        and now - _market_prediction_time < _market_prediction_ttl
    ):
        return _market_prediction_cache

    market_analysis = _get_market_analysis()
    nifty = market_analysis.get("nifty", {})
    bank_nifty = market_analysis.get("bank_nifty", {})
    opening = predict_opening()
    chandelier = _chandelier_context()

    gift_strength = float(get_gift_nifty_strength())
    gift_confidence = float(get_gift_nifty_confidence())
    gift_direction = str(get_gift_nifty_direction())
    gift_tradeable = bool(is_gift_nifty_tradeable())

    broad_strength = float(get_broad_market_strength())
    broad_confidence = float(get_broad_market_confidence())
    broad_direction = str(get_broad_market_direction())
    broad_tradeable = bool(is_market_tradeable())

    nifty_strength = float(nifty.get("strength", 0))
    bank_strength = float(bank_nifty.get("strength", 0))
    nifty_adx = float(nifty.get("adx", 0))
    bank_adx = float(bank_nifty.get("adx", 0))
    nifty_rsi = float(nifty.get("rsi", 50))
    bank_rsi = float(bank_nifty.get("rsi", 50))
    nifty_direction = str(nifty.get("direction", "SIDEWAYS"))
    bank_direction = str(bank_nifty.get("direction", "SIDEWAYS"))
    nifty_volume = bool(nifty.get("volume_confirmed", False))
    bank_volume = bool(bank_nifty.get("volume_confirmed", False))
    nifty_normal_volatility = bool(nifty.get("volatility_normal", False))
    bank_normal_volatility = bool(bank_nifty.get("volatility_normal", False))

    bullish_score = 20.0
    bearish_score = 20.0
    sideways_score = 35.0

    if nifty_direction == "BULLISH":
        bullish_score += 18
        sideways_score -= 6
    elif nifty_direction == "BEARISH":
        bearish_score += 18
        sideways_score -= 6

    if bank_direction == "BULLISH":
        bullish_score += 18
        sideways_score -= 6
    elif bank_direction == "BEARISH":
        bearish_score += 18
        sideways_score -= 6

    if gift_direction == "BULLISH":
        bullish_score += 12
    elif gift_direction == "BEARISH":
        bearish_score += 12

    if opening["market_bias"] == "BULLISH":
        bullish_score += opening["bullish_probability"] * 0.20
    elif opening["market_bias"] == "BEARISH":
        bearish_score += opening["bearish_probability"] * 0.20
    else:
        sideways_score += 10

    if nifty_direction == bank_direction and nifty_direction != "SIDEWAYS":
        if nifty_direction == "BULLISH":
            bullish_score += 12
        else:
            bearish_score += 12
        market_breadth = 100
    elif nifty_direction == "SIDEWAYS" and bank_direction == "SIDEWAYS":
        sideways_score += 20
        market_breadth = 0
    else:
        sideways_score += 15
        market_breadth = 50

    chandelier_directions = [
        value.get(timeframe, {}).get("trend")
        for value in chandelier.values() if value.get("available")
        for timeframe in ("60m", "30m")
    ]
    if chandelier_directions and all(value == "BULLISH" for value in chandelier_directions):
        bullish_score += 8
    elif chandelier_directions and all(value == "BEARISH" for value in chandelier_directions):
        bearish_score += 8
    elif chandelier_directions:
        sideways_score += 5

    average_adx = (nifty_adx + bank_adx) / 2.0

    if average_adx >= 30:
        if nifty_direction == bank_direction == "BULLISH":
            bullish_score += 10
        elif nifty_direction == bank_direction == "BEARISH":
            bearish_score += 10
    elif average_adx < 22:
        sideways_score += 18

    if 55 <= nifty_rsi <= 70 and 55 <= bank_rsi <= 70:
        bullish_score += 6
    elif 30 <= nifty_rsi <= 45 and 30 <= bank_rsi <= 45:
        bearish_score += 6
    elif nifty_rsi >= 76 or bank_rsi >= 76:
        bullish_score -= 8
        sideways_score += 5
    elif nifty_rsi <= 24 or bank_rsi <= 24:
        bearish_score -= 8
        sideways_score += 5

    if nifty_volume and bank_volume:
        if nifty_direction == bank_direction == "BULLISH":
            bullish_score += 6
        elif nifty_direction == bank_direction == "BEARISH":
            bearish_score += 6
    else:
        sideways_score += 8

    normal_volatility = (
        nifty_normal_volatility
        and bank_normal_volatility
    )

    if not normal_volatility:
        sideways_score += 8

    gap_trap_probability = float(opening["gap_trap_probability"])

    if gap_trap_probability >= 35:
        sideways_score += 15
        bullish_score -= 5
        bearish_score -= 5

    bullish_score = max(5.0, bullish_score)
    bearish_score = max(5.0, bearish_score)
    sideways_score = max(10.0, sideways_score)

    total_score = bullish_score + bearish_score + sideways_score
    bullish_probability = round((bullish_score / total_score) * 100, 1)
    bearish_probability = round((bearish_score / total_score) * 100, 1)
    sideways_probability = round(
        100.0 - bullish_probability - bearish_probability,
        1,
    )

    trend_confidence = int(
        max(
            0,
            min(
                100,
                round(
                    (broad_strength * 0.35)
                    + (average_adx * 1.25)
                    + (market_breadth * 0.20)
                    + (gift_strength * 0.15)
                    - (gap_trap_probability * 0.20)
                ),
            ),
        )
    )

    market_confidence = int(
        max(
            0,
            min(
                100,
                round(
                    (broad_confidence * 0.45)
                    + (float(opening["market_confidence"]) * 0.30)
                    + (gift_confidence * 0.15)
                    + (trend_confidence * 0.10)
                ),
            ),
        )
    )

    prediction_score = int(
        max(
            bullish_probability,
            bearish_probability,
            sideways_probability,
        )
    )

    if sideways_probability >= max(bullish_probability, bearish_probability):
        expected_direction = "SIDEWAYS"
    elif bullish_probability > bearish_probability:
        expected_direction = "UP"
    else:
        expected_direction = "DOWN"

    trade_confidence = int(
        max(
            0,
            min(
                100,
                round(
                    (prediction_score * 0.35)
                    + (trend_confidence * 0.35)
                    + (market_confidence * 0.30)
                    - (gap_trap_probability * 0.20)
                ),
            ),
        )
    )

    if average_adx >= 30 and market_breadth == 100:
        expected_trend_duration = "EXTENDED"
    elif average_adx >= 22 and market_breadth >= 50:
        expected_trend_duration = "MEDIUM"
    else:
        expected_trend_duration = "SHORT"

    if (
        gap_trap_probability >= 40
        or not normal_volatility
        or expected_direction == "SIDEWAYS"
        or trade_confidence < 70
    ):
        market_risk = "HIGH"
    elif trade_confidence >= 82 and trend_confidence >= 75:
        market_risk = "LOW"
    else:
        market_risk = "MODERATE"

    tradeable = (
        expected_direction in ("UP", "DOWN")
        and prediction_score >= 55
        and trend_confidence >= 70
        and market_confidence >= 70
        and trade_confidence >= 75
        and market_breadth == 100
        and average_adx >= 22
        and normal_volatility
        and gap_trap_probability < 30
        and gift_tradeable
        and broad_tradeable
    )

    if tradeable:
        market_bias = "BULLISH" if expected_direction == "UP" else "BEARISH"
        prediction_quality = "HIGH"
    else:
        market_bias = "NO TRADE"
        prediction_quality = "LOW" if trade_confidence < 65 else "MODERATE"

    _market_prediction_cache = {
        "bullish_probability": bullish_probability,
        "bearish_probability": bearish_probability,
        "sideways_probability": sideways_probability,
        "trend_confidence": trend_confidence,
        "market_confidence": market_confidence,
        "trade_confidence": trade_confidence,
        "prediction_score": prediction_score,
        "prediction_quality": prediction_quality,
        "expected_trend_duration": expected_trend_duration,
        "market_risk": market_risk,
        "market_bias": market_bias,
        "expected_direction": expected_direction,
        "market_breadth": market_breadth,
        "gift_nifty_direction": gift_direction,
        "market_direction": broad_direction,
        "tradeable": tradeable,
        "chandelier_confirmation": chandelier,
    }
    _market_prediction_time = now

    return _market_prediction_cache


def predict_market():
    return _get_prediction().copy()


def get_market_prediction():
    return predict_market()


def get_prediction_score():
    return _get_prediction()["prediction_score"]


def get_prediction_confidence():
    return _get_prediction()["market_confidence"]


def get_market_bias():
    return _get_prediction()["market_bias"]


def get_expected_direction():
    return _get_prediction()["expected_direction"]


def is_prediction_tradeable():
    return bool(_get_prediction()["tradeable"])
