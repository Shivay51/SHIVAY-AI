import time

from gift_nifty import (
    get_gift_nifty_confidence,
    get_gift_nifty_direction,
    get_gift_nifty_strength,
    is_gift_nifty_tradeable,
)
from market_brain import (
    _get_market_analysis,
    get_market_confidence as get_broad_market_confidence,
    get_market_strength as get_broad_market_strength,
    is_market_tradeable as is_broad_market_tradeable,
)
from market_prediction import predict_market
from market_sentiment import (
    can_trade_by_sentiment,
    get_market_sentiment,
    get_sentiment_confidence,
    get_sentiment_score,
)


_strength_cache = None
_strength_cache_time = 0.0
_strength_cache_ttl = 300


def _get_strength_analysis():
    global _strength_cache
    global _strength_cache_time

    now = time.time()

    if (
        _strength_cache is not None
        and now - _strength_cache_time < _strength_cache_ttl
    ):
        return _strength_cache

    analysis = _get_market_analysis()
    nifty = analysis.get("nifty", {})
    bank_nifty = analysis.get("bank_nifty", {})
    prediction = predict_market()

    gift_strength = float(get_gift_nifty_strength())
    gift_confidence = float(get_gift_nifty_confidence())
    gift_direction = str(get_gift_nifty_direction())
    gift_tradeable = bool(is_gift_nifty_tradeable())

    broad_strength = float(get_broad_market_strength())
    broad_confidence = float(get_broad_market_confidence())
    broad_tradeable = bool(is_broad_market_tradeable())

    sentiment = str(get_market_sentiment())
    sentiment_score = float(get_sentiment_score())
    sentiment_confidence = float(get_sentiment_confidence())
    sentiment_tradeable = bool(can_trade_by_sentiment())

    nifty_strength = float(nifty.get("strength", 0))
    bank_strength = float(bank_nifty.get("strength", 0))
    nifty_adx = float(nifty.get("adx", 0))
    bank_adx = float(bank_nifty.get("adx", 0))
    nifty_rsi = float(nifty.get("rsi", 50))
    bank_rsi = float(bank_nifty.get("rsi", 50))
    nifty_direction = str(nifty.get("direction", "SIDEWAYS"))
    bank_direction = str(bank_nifty.get("direction", "SIDEWAYS"))
    nifty_macd = bool(nifty.get("macd", False))
    bank_macd = bool(bank_nifty.get("macd", False))
    nifty_supertrend = bool(nifty.get("supertrend", False))
    bank_supertrend = bool(bank_nifty.get("supertrend", False))
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

    average_adx = (nifty_adx + bank_adx) / 2.0
    index_alignment = (
        nifty_direction == bank_direction
        and nifty_direction in ("BULLISH", "BEARISH")
    )
    volume_confirmed = nifty_volume and bank_volume
    normal_volatility = nifty_volatility and bank_volatility
    overextended = nifty_extended or bank_extended

    trend_strength = (
        ((nifty_strength + bank_strength) * 0.45)
        + (min(100.0, average_adx * 2.5) * 0.35)
        + (100.0 if index_alignment else 0.0) * 0.20
    )

    momentum_strength = 0.0

    if nifty_macd == bank_macd:
        momentum_strength += 30

    if nifty_supertrend == bank_supertrend:
        momentum_strength += 20

    if 55 <= nifty_rsi <= 70 and 55 <= bank_rsi <= 70:
        momentum_strength += 25
    elif 30 <= nifty_rsi <= 45 and 30 <= bank_rsi <= 45:
        momentum_strength += 25

    if volume_confirmed:
        momentum_strength += 15

    if normal_volatility:
        momentum_strength += 10

    bull_strength = 0.0
    bear_strength = 0.0

    if nifty_direction == "BULLISH":
        bull_strength += nifty_strength * 0.40
    elif nifty_direction == "BEARISH":
        bear_strength += nifty_strength * 0.40

    if bank_direction == "BULLISH":
        bull_strength += bank_strength * 0.40
    elif bank_direction == "BEARISH":
        bear_strength += bank_strength * 0.40

    if gift_direction == "BULLISH":
        bull_strength += gift_strength * 0.20
    elif gift_direction == "BEARISH":
        bear_strength += gift_strength * 0.20

    if sentiment == "BULLISH":
        bull_strength += 10
    elif sentiment == "BEARISH":
        bear_strength += 10

    trend_strength = max(0, min(100, int(round(trend_strength))))
    momentum_strength = max(0, min(100, int(round(momentum_strength))))
    bull_strength = max(0, min(100, int(round(bull_strength))))
    bear_strength = max(0, min(100, int(round(bear_strength))))

    market_strength = int(
        max(
            0,
            min(
                100,
                round(
                    (broad_strength * 0.25)
                    + (trend_strength * 0.30)
                    + (momentum_strength * 0.20)
                    + (max(bull_strength, bear_strength) * 0.15)
                    + (gift_strength * 0.10)
                ),
            ),
        )
    )

    strength_confidence = int(
        max(
            0,
            min(
                100,
                round(
                    (broad_confidence * 0.30)
                    + (float(prediction["market_confidence"]) * 0.25)
                    + (sentiment_confidence * 0.25)
                    + (gift_confidence * 0.10)
                    + (trend_strength * 0.10)
                ),
            ),
        )
    )

    if (
        market_strength >= 80
        and strength_confidence >= 80
        and average_adx >= 30
        and index_alignment
        and volume_confirmed
        and not overextended
    ):
        trade_quality = "INSTITUTIONAL"
        market_rating = "VERY STRONG"
    elif (
        market_strength >= 70
        and strength_confidence >= 75
        and average_adx >= 22
        and index_alignment
        and volume_confirmed
        and not overextended
    ):
        trade_quality = "HIGH"
        market_rating = "STRONG"
    elif market_strength >= 60:
        trade_quality = "MODERATE"
        market_rating = "MODERATE"
    else:
        trade_quality = "LOW"
        market_rating = "WEAK"

    if (
        market_strength < 70
        or average_adx < 22
        or not volume_confirmed
        or strength_confidence < 75
        or not normal_volatility
        or overextended
        or not index_alignment
    ):
        risk_level = "HIGH"
    elif market_strength >= 80 and strength_confidence >= 82:
        risk_level = "LOW"
    else:
        risk_level = "MODERATE"

    tradeable = (
        market_strength >= 70
        and trend_strength >= 70
        and momentum_strength >= 60
        and strength_confidence >= 75
        and average_adx >= 22
        and volume_confirmed
        and normal_volatility
        and not overextended
        and index_alignment
        and sentiment_tradeable
        and broad_tradeable
        and gift_tradeable
        and bool(prediction["tradeable"])
    )

    _strength_cache = {
        "market_strength": market_strength,
        "trend_strength": trend_strength,
        "momentum_strength": momentum_strength,
        "bull_strength": bull_strength,
        "bear_strength": bear_strength,
        "strength_confidence": strength_confidence,
        "trade_quality": trade_quality,
        "risk_level": risk_level,
        "market_rating": market_rating,
        "tradeable": tradeable,
    }
    _strength_cache_time = now

    return _strength_cache


def get_market_strength():
    return _get_strength_analysis()["market_strength"]


def get_trend_strength():
    return _get_strength_analysis()["trend_strength"]


def get_strength_score():
    return _get_strength_analysis()["market_strength"]


def get_strength_confidence():
    return _get_strength_analysis()["strength_confidence"]


def is_strong_market():
    return get_market_strength() >= 70 and is_market_tradeable()


def is_weak_market():
    return get_market_strength() < 70


def is_market_tradeable():
    return bool(_get_strength_analysis()["tradeable"])
