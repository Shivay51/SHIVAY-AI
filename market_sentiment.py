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
from market_prediction import predict_market


_sentiment_cache = None
_sentiment_cache_time = 0.0
_sentiment_cache_ttl = 300


def _get_sentiment():
    global _sentiment_cache
    global _sentiment_cache_time

    now = time.time()

    if (
        _sentiment_cache is not None
        and now - _sentiment_cache_time < _sentiment_cache_ttl
    ):
        return _sentiment_cache

    market_analysis = _get_market_analysis()
    nifty = market_analysis.get("nifty", {})
    bank_nifty = market_analysis.get("bank_nifty", {})
    prediction = predict_market()
    opening = predict_opening()

    gift_direction = str(get_gift_nifty_direction())
    gift_strength = float(get_gift_nifty_strength())
    gift_confidence = float(get_gift_nifty_confidence())
    gift_tradeable = bool(is_gift_nifty_tradeable())

    market_direction = str(get_broad_market_direction())
    market_strength = float(get_broad_market_strength())
    market_confidence = float(get_broad_market_confidence())
    market_tradeable = bool(is_market_tradeable())

    nifty_direction = str(nifty.get("direction", "SIDEWAYS"))
    bank_direction = str(bank_nifty.get("direction", "SIDEWAYS"))
    nifty_strength = float(nifty.get("strength", 0))
    bank_strength = float(bank_nifty.get("strength", 0))
    nifty_adx = float(nifty.get("adx", 0))
    bank_adx = float(bank_nifty.get("adx", 0))
    nifty_rsi = float(nifty.get("rsi", 50))
    bank_rsi = float(bank_nifty.get("rsi", 50))
    nifty_volume = bool(nifty.get("volume_confirmed", False))
    bank_volume = bool(bank_nifty.get("volume_confirmed", False))
    nifty_volatility = bool(nifty.get("volatility_normal", False))
    bank_volatility = bool(bank_nifty.get("volatility_normal", False))
    nifty_extended = bool(nifty.get("overextended_bullish", False)) or bool(
        nifty.get("overextended_bearish", False)
    )
    bank_extended = bool(bank_nifty.get("overextended_bullish", False)) or bool(
        bank_nifty.get("overextended_bearish", False)
    )

    bullish_sentiment = 15.0
    bearish_sentiment = 15.0
    neutral_sentiment = 35.0

    if gift_direction == "BULLISH":
        bullish_sentiment += 12
        neutral_sentiment -= 4
    elif gift_direction == "BEARISH":
        bearish_sentiment += 12
        neutral_sentiment -= 4

    if market_direction == "BULLISH":
        bullish_sentiment += 18
        neutral_sentiment -= 6
    elif market_direction == "BEARISH":
        bearish_sentiment += 18
        neutral_sentiment -= 6

    if nifty_direction == "BULLISH":
        bullish_sentiment += 12
    elif nifty_direction == "BEARISH":
        bearish_sentiment += 12

    if bank_direction == "BULLISH":
        bullish_sentiment += 12
    elif bank_direction == "BEARISH":
        bearish_sentiment += 12

    if prediction["market_bias"] == "BULLISH":
        bullish_sentiment += prediction["bullish_probability"] * 0.18
    elif prediction["market_bias"] == "BEARISH":
        bearish_sentiment += prediction["bearish_probability"] * 0.18
    else:
        neutral_sentiment += 15

    if opening["market_bias"] == "BULLISH":
        bullish_sentiment += opening["bullish_probability"] * 0.10
    elif opening["market_bias"] == "BEARISH":
        bearish_sentiment += opening["bearish_probability"] * 0.10
    else:
        neutral_sentiment += 8

    index_alignment = (
        nifty_direction == bank_direction
        and nifty_direction in ("BULLISH", "BEARISH")
    )

    if index_alignment:
        if nifty_direction == "BULLISH":
            bullish_sentiment += 10
        else:
            bearish_sentiment += 10
        sector_confirmation = 100
    elif nifty_direction == "SIDEWAYS" and bank_direction == "SIDEWAYS":
        neutral_sentiment += 20
        sector_confirmation = 0
    else:
        neutral_sentiment += 12
        sector_confirmation = 50

    average_adx = (nifty_adx + bank_adx) / 2.0

    if average_adx < 22:
        neutral_sentiment += 18
    elif average_adx >= 30 and index_alignment:
        if nifty_direction == "BULLISH":
            bullish_sentiment += 8
        else:
            bearish_sentiment += 8

    if 55 <= nifty_rsi <= 70 and 55 <= bank_rsi <= 70:
        bullish_sentiment += 5
    elif 30 <= nifty_rsi <= 45 and 30 <= bank_rsi <= 45:
        bearish_sentiment += 5
    elif nifty_rsi >= 76 or bank_rsi >= 76:
        bullish_sentiment -= 8
        neutral_sentiment += 6
    elif nifty_rsi <= 24 or bank_rsi <= 24:
        bearish_sentiment -= 8
        neutral_sentiment += 6

    if not (nifty_volume and bank_volume):
        neutral_sentiment += 10

    if not (nifty_volatility and bank_volatility):
        neutral_sentiment += 8

    if nifty_extended or bank_extended:
        bullish_sentiment -= 7
        bearish_sentiment -= 7
        neutral_sentiment += 12

    gap_trap_probability = float(opening["gap_trap_probability"])

    if gap_trap_probability >= 35:
        bullish_sentiment -= 5
        bearish_sentiment -= 5
        neutral_sentiment += 15

    bullish_sentiment = max(5.0, bullish_sentiment)
    bearish_sentiment = max(5.0, bearish_sentiment)
    neutral_sentiment = max(10.0, neutral_sentiment)

    total_sentiment = (
        bullish_sentiment
        + bearish_sentiment
        + neutral_sentiment
    )
    bullish_probability = round(
        (bullish_sentiment / total_sentiment) * 100,
        1,
    )
    bearish_probability = round(
        (bearish_sentiment / total_sentiment) * 100,
        1,
    )
    neutral_probability = round(
        100.0 - bullish_probability - bearish_probability,
        1,
    )

    if neutral_probability >= max(bullish_probability, bearish_probability):
        sentiment = "NO TRADE"
    elif bullish_probability > bearish_probability:
        sentiment = "BULLISH"
    else:
        sentiment = "BEARISH"

    sentiment_score = int(
        max(
            bullish_probability,
            bearish_probability,
            neutral_probability,
        )
    )

    sentiment_confidence = int(
        max(
            0,
            min(
                100,
                round(
                    (market_confidence * 0.30)
                    + (float(prediction["market_confidence"]) * 0.30)
                    + (gift_confidence * 0.15)
                    + (average_adx * 0.80)
                    + (sector_confirmation * 0.10)
                    - (gap_trap_probability * 0.20)
                ),
            ),
        )
    )

    if (
        sentiment == "NO TRADE"
        or sentiment_confidence < 70
        or average_adx < 22
        or not index_alignment
        or gap_trap_probability >= 30
    ):
        risk_level = "HIGH"
    elif sentiment_confidence >= 82 and market_strength >= 75:
        risk_level = "LOW"
    else:
        risk_level = "MODERATE"

    trade_permission = (
        sentiment in ("BULLISH", "BEARISH")
        and sentiment_confidence >= 75
        and sentiment_score >= 55
        and average_adx >= 22
        and index_alignment
        and nifty_volume
        and bank_volume
        and nifty_volatility
        and bank_volatility
        and not nifty_extended
        and not bank_extended
        and gap_trap_probability < 30
        and gift_tradeable
        and market_tradeable
        and bool(prediction["tradeable"])
    )

    if not trade_permission:
        sentiment = "NO TRADE"

    _sentiment_cache = {
        "market_sentiment": sentiment,
        "bullish_sentiment": bullish_probability,
        "bearish_sentiment": bearish_probability,
        "neutral_sentiment": neutral_probability,
        "sentiment_score": sentiment_score,
        "market_confidence": sentiment_confidence,
        "risk_level": risk_level,
        "trade_permission": trade_permission,
        "sector_confirmation": sector_confirmation,
        "market_breadth": prediction["market_breadth"],
    }
    _sentiment_cache_time = now

    return _sentiment_cache


def get_market_sentiment():
    return _get_sentiment()["market_sentiment"]


def get_sentiment_score():
    return _get_sentiment()["sentiment_score"]


def get_sentiment_confidence():
    return _get_sentiment()["market_confidence"]


def is_bullish_sentiment():
    return get_market_sentiment() == "BULLISH"


def is_bearish_sentiment():
    return get_market_sentiment() == "BEARISH"


def is_neutral_sentiment():
    return get_market_sentiment() == "NO TRADE"


def can_trade_by_sentiment():
    return bool(_get_sentiment()["trade_permission"])
