import time

from gift_nifty import (
    get_gift_nifty_confidence,
    get_gift_nifty_direction,
    is_gift_nifty_tradeable,
)
from gift_nifty_prediction import predict_opening
from market_brain import (
    _get_market_analysis,
    get_market_confidence as get_broad_market_confidence,
    get_market_regime,
    is_market_tradeable as is_broad_market_tradeable,
)
from market_prediction import predict_market
from market_sentiment import (
    can_trade_by_sentiment,
    get_market_sentiment,
    get_sentiment_confidence,
)
from market_strength import (
    get_market_strength,
    get_strength_confidence,
    get_trend_strength,
    is_market_tradeable as is_strength_tradeable,
)


_confidence_cache = None
_confidence_cache_time = 0.0
_confidence_cache_ttl = 300


def _get_confidence_analysis():
    global _confidence_cache
    global _confidence_cache_time

    now = time.time()

    if (
        _confidence_cache is not None
        and now - _confidence_cache_time < _confidence_cache_ttl
    ):
        return _confidence_cache

    market_analysis = _get_market_analysis()
    nifty = market_analysis.get("nifty", {})
    bank_nifty = market_analysis.get("bank_nifty", {})
    prediction = predict_market()
    opening = predict_opening()

    gift_direction = str(get_gift_nifty_direction())
    gift_confidence = float(get_gift_nifty_confidence())
    gift_tradeable = bool(is_gift_nifty_tradeable())

    market_regime = str(get_market_regime())
    market_confidence = float(get_broad_market_confidence())
    market_tradeable = bool(is_broad_market_tradeable())

    sentiment = str(get_market_sentiment())
    sentiment_confidence = float(get_sentiment_confidence())
    sentiment_tradeable = bool(can_trade_by_sentiment())

    strength = float(get_market_strength())
    trend_confidence = float(get_trend_strength())
    strength_confidence = float(get_strength_confidence())
    strength_tradeable = bool(is_strength_tradeable())

    nifty_direction = str(nifty.get("direction", "SIDEWAYS"))
    bank_direction = str(bank_nifty.get("direction", "SIDEWAYS"))
    nifty_adx = float(nifty.get("adx", 0))
    bank_adx = float(bank_nifty.get("adx", 0))
    nifty_volume = bool(nifty.get("volume_confirmed", False))
    bank_volume = bool(bank_nifty.get("volume_confirmed", False))
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
    overextended = nifty_extended or bank_extended
    gap_trap_probability = float(opening["gap_trap_probability"])

    buy_confidence = 0.0
    sell_confidence = 0.0

    if gift_direction == "BULLISH":
        buy_confidence += 12
    elif gift_direction == "BEARISH":
        sell_confidence += 12

    if prediction["expected_direction"] == "UP":
        buy_confidence += prediction["bullish_probability"] * 0.25
    elif prediction["expected_direction"] == "DOWN":
        sell_confidence += prediction["bearish_probability"] * 0.25

    if sentiment == "BULLISH":
        buy_confidence += sentiment_confidence * 0.20
    elif sentiment == "BEARISH":
        sell_confidence += sentiment_confidence * 0.20

    if index_alignment and nifty_direction == "BULLISH":
        buy_confidence += 15
    elif index_alignment and nifty_direction == "BEARISH":
        sell_confidence += 15

    if average_adx >= 30:
        if nifty_direction == "BULLISH":
            buy_confidence += 10
        elif nifty_direction == "BEARISH":
            sell_confidence += 10
    elif average_adx >= 22:
        if nifty_direction == "BULLISH":
            buy_confidence += 5
        elif nifty_direction == "BEARISH":
            sell_confidence += 5

    if volume_confirmed:
        if nifty_direction == "BULLISH":
            buy_confidence += 8
        elif nifty_direction == "BEARISH":
            sell_confidence += 8

    if market_regime == "STRONG BULL":
        buy_confidence += 10
    elif market_regime == "BULL":
        buy_confidence += 5
    elif market_regime == "STRONG BEAR":
        sell_confidence += 10
    elif market_regime == "BEAR":
        sell_confidence += 5

    buy_confidence += trend_confidence * 0.10
    sell_confidence += trend_confidence * 0.10
    buy_confidence += strength_confidence * 0.10
    sell_confidence += strength_confidence * 0.10

    if overextended:
        buy_confidence -= 12
        sell_confidence -= 12

    if gap_trap_probability >= 30:
        buy_confidence -= 10
        sell_confidence -= 10

    buy_confidence = max(0, min(100, int(round(buy_confidence))))
    sell_confidence = max(0, min(100, int(round(sell_confidence))))

    conflict = (
        not index_alignment
        or sentiment == "NO TRADE"
        or prediction["market_bias"] == "NO TRADE"
        or (
            sentiment == "BULLISH"
            and prediction["expected_direction"] != "UP"
        )
        or (
            sentiment == "BEARISH"
            and prediction["expected_direction"] != "DOWN"
        )
    )

    final_confidence = max(buy_confidence, sell_confidence)

    if conflict:
        final_confidence = min(final_confidence, 69)

    risk_score = int(
        max(
            0,
            min(
                100,
                round(
                    (gap_trap_probability * 0.45)
                    + (0 if volume_confirmed else 20)
                    + (0 if average_adx >= 22 else 20)
                    + (15 if overextended else 0)
                    + (20 if conflict else 0)
                ),
            ),
        )
    )

    if (
        final_confidence >= 85
        and risk_score <= 20
        and strength >= 75
        and market_confidence >= 75
    ):
        trade_quality = "INSTITUTIONAL"
    elif final_confidence >= 80 and risk_score <= 30:
        trade_quality = "HIGH"
    else:
        trade_quality = "NO TRADE"

    trade_allowed = (
        final_confidence >= 80
        and risk_score <= 30
        and not conflict
        and average_adx >= 22
        and volume_confirmed
        and not overextended
        and gift_tradeable
        and market_tradeable
        and sentiment_tradeable
        and strength_tradeable
        and bool(prediction["tradeable"])
    )

    if not trade_allowed:
        trade_quality = "NO TRADE"

    _confidence_cache = {
        "final_confidence": final_confidence,
        "buy_confidence": buy_confidence,
        "sell_confidence": sell_confidence,
        "trend_confidence": int(round(trend_confidence)),
        "entry_confidence": int(round(prediction["trade_confidence"])),
        "risk_score": risk_score,
        "trade_quality": trade_quality,
        "final_decision_score": final_confidence,
        "trade_allowed": trade_allowed,
    }
    _confidence_cache_time = now

    return _confidence_cache


def get_confidence():
    return _get_confidence_analysis()["final_confidence"]


def get_buy_confidence():
    return _get_confidence_analysis()["buy_confidence"]


def get_sell_confidence():
    return _get_confidence_analysis()["sell_confidence"]


def get_trade_quality():
    return _get_confidence_analysis()["trade_quality"]


def get_risk_score():
    return _get_confidence_analysis()["risk_score"]


def is_high_confidence():
    return get_confidence() >= 80


def is_trade_allowed():
    return bool(_get_confidence_analysis()["trade_allowed"])
