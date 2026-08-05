from confidence_engine import (
    get_confidence,
    get_risk_score,
    get_trade_quality,
    is_trade_allowed as is_confidence_trade_allowed,
)
from gift_nifty import get_gift_nifty_direction, is_gift_nifty_tradeable
from market_brain import get_market_regime, is_market_tradeable as is_brain_tradeable
from market_prediction import get_market_bias, is_prediction_tradeable
from market_sentiment import can_trade_by_sentiment, get_market_sentiment
from market_strength import is_market_tradeable as is_strength_tradeable
from config import MIN_RISK_REWARD


def validate_entry(score_data, side):
    price = float(score_data.get("price", 0))
    e20 = float(score_data.get("ema20", 0))
    e50 = float(score_data.get("ema50", 0))
    e200 = float(score_data.get("ema200", 0))
    vwap_value = float(score_data.get("vwap", 0))
    rsi = float(score_data.get("rsi", 0))
    adx_value = float(score_data.get("adx", 0))
    atr_value = float(score_data.get("atr", 0))
    setup = str(score_data.get("setup", ""))

    if price < 200 or atr_value <= 0 or adx_value < 25:
        return False

    if "Confirmed Breakout" not in setup and "Confirmed Pullback" not in setup:
        return False

    if side == "BUY":
        return (
            e20 > e50 > e200
            and price > vwap_value > 0
            and 54 <= rsi <= 72
            and price - e20 <= atr_value * 1.50
            and bool(score_data.get("macd", False))
            and bool(score_data.get("supertrend", False))
            and bool(score_data.get("volume_spike", False))
        )

    if side == "SELL":
        return (
            e20 < e50 < e200
            and price < vwap_value
            and 28 <= rsi <= 46
            and e20 - price <= atr_value * 1.50
            and not bool(score_data.get("macd", True))
            and not bool(score_data.get("supertrend", True))
            and bool(score_data.get("volume_spike", False))
        )

    return False


def validate_stoploss(score_data, trade_plan, side):
    if not trade_plan:
        return False

    entry = float(trade_plan.get("entry", score_data.get("price", 0)))
    stop_loss = float(trade_plan.get("sl", 0))
    atr_value = float(score_data.get("atr", 0))

    if entry <= 0 or stop_loss <= 0 or atr_value <= 0:
        return False

    risk = abs(entry - stop_loss)

    if risk < atr_value * 0.75 or risk > atr_value * 1.75:
        return False

    if side == "BUY":
        return stop_loss < entry

    if side == "SELL":
        return stop_loss > entry

    return False


def validate_target(score_data, trade_plan, side):
    if not trade_plan:
        return False

    entry = float(trade_plan.get("entry", score_data.get("price", 0)))
    stop_loss = float(trade_plan.get("sl", 0))
    target = float(trade_plan.get("target2", 0))

    risk = abs(entry - stop_loss)
    reward = abs(target - entry)

    if entry <= 0 or risk <= 0 or reward <= 0:
        return False

    if reward / risk < float(MIN_RISK_REWARD):
        return False

    if side == "BUY":
        return target > entry

    if side == "SELL":
        return target < entry

    return False


def validate_risk(score_data, trade_plan, side):
    if not validate_stoploss(score_data, trade_plan, side):
        return False

    if not validate_target(score_data, trade_plan, side):
        return False

    support = float(score_data.get("support", 0))
    resistance = float(score_data.get("resistance", 0))
    price = float(score_data.get("price", 0))
    atr_value = float(score_data.get("atr", 0))

    if side == "BUY":
        return resistance > price and resistance - price >= atr_value * 2.0

    if side == "SELL":
        return support < price and price - support >= atr_value * 2.0

    return False


def validate_buy(score_data, trade_plan=None):
    if not validate_entry(score_data, "BUY"):
        return False

    if trade_plan is not None and not validate_risk(score_data, trade_plan, "BUY"):
        return False

    return (
        int(score_data.get("score", 0)) >= 85
        and "BULL" in str(score_data.get("regime", ""))
        and get_market_regime() in ("STRONG BULL", "BULL")
        and get_gift_nifty_direction() == "BULLISH"
        and get_market_sentiment() == "BULLISH"
        and get_market_bias() == "BULLISH"
    )


def validate_sell(score_data, trade_plan=None):
    if not validate_entry(score_data, "SELL"):
        return False

    if trade_plan is not None and not validate_risk(score_data, trade_plan, "SELL"):
        return False

    return (
        int(score_data.get("score", 0)) >= 85
        and "BEAR" in str(score_data.get("regime", ""))
        and get_market_regime() in ("STRONG BEAR", "BEAR")
        and get_gift_nifty_direction() == "BEARISH"
        and get_market_sentiment() == "BEARISH"
        and get_market_bias() == "BEARISH"
    )


def validate_trade(score_data, trade_plan=None):
    base_approval = (
        get_confidence() >= 80
        and get_risk_score() <= 30
        and get_trade_quality() in ("INSTITUTIONAL", "HIGH")
        and is_confidence_trade_allowed()
        and is_brain_tradeable()
        and is_prediction_tradeable()
        and can_trade_by_sentiment()
        and is_strength_tradeable()
        and is_gift_nifty_tradeable()
    )

    if not base_approval:
        return {
            "valid": False,
            "decision": "NO TRADE",
            "reason": "Market confirmation failed",
        }

    buy_valid = validate_buy(score_data, trade_plan)
    sell_valid = validate_sell(score_data, trade_plan)

    if buy_valid and not sell_valid:
        return {
            "valid": True,
            "decision": "BUY",
            "reason": "Institutional long confirmation",
        }

    if sell_valid and not buy_valid:
        return {
            "valid": True,
            "decision": "SELL",
            "reason": "Institutional short confirmation",
        }

    return {
        "valid": False,
        "decision": "NO TRADE",
        "reason": "No directional confluence",
    }


def is_trade_valid(score_data, trade_plan=None):
    return bool(validate_trade(score_data, trade_plan)["valid"])
