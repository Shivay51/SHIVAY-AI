from confidence_engine import get_confidence
from indicators import atr, macd, supertrend, vwap, volume_spike
from market_prediction import get_market_bias
from market_sentiment import get_market_sentiment
from market_strength import get_market_strength


_exit_analysis = {
    "best_exit": 0.0,
    "exit_zone": (0.0, 0.0),
    "exit_confidence": 0,
    "partial_exit": False,
    "full_exit": False,
    "trail_stop": False,
}


def optimize_exit(market, score_data, trade_plan, side):
    global _exit_analysis

    close = [float(value) for value in market.get("close", [])]
    high = [float(value) for value in market.get("high", [])]
    low = [float(value) for value in market.get("low", [])]
    volume = [float(value) for value in market.get("volume", [])]

    if (
        side not in ("BUY", "SELL")
        or len(close) < 35
        or len(high) < 35
        or len(low) < 35
        or len(volume) < 35
        or not trade_plan
    ):
        _exit_analysis = {
            "best_exit": 0.0,
            "exit_zone": (0.0, 0.0),
            "exit_confidence": 0,
            "partial_exit": False,
            "full_exit": True,
            "trail_stop": False,
        }
        return _exit_analysis.copy()

    current_price = float(close[-1])
    entry = float(trade_plan.get("entry", 0))
    stop_loss = float(trade_plan.get("sl", 0))
    target1 = float(trade_plan.get("target1", 0))
    target2 = float(trade_plan.get("target2", 0))
    target3 = float(trade_plan.get("target3", 0))
    atr_value = float(score_data.get("atr", atr(high, low, close)))
    e20 = float(score_data.get("ema20", 0))
    vwap_value = float(score_data.get("vwap", vwap(high, low, close, volume)))
    rsi = float(score_data.get("rsi", 50))
    adx_value = float(score_data.get("adx", 0))
    macd_bullish = macd(close)
    supertrend_bullish = supertrend(high, low, close)
    volume_confirmed = volume_spike(volume)

    if entry <= 0 or stop_loss <= 0 or atr_value <= 0:
        _exit_analysis = {
            "best_exit": 0.0,
            "exit_zone": (0.0, 0.0),
            "exit_confidence": 0,
            "partial_exit": False,
            "full_exit": True,
            "trail_stop": False,
        }
        return _exit_analysis.copy()

    initial_risk = abs(entry - stop_loss)
    profit = (
        current_price - entry
        if side == "BUY"
        else entry - current_price
    )
    profit_r = profit / initial_risk if initial_risk > 0 else 0.0
    market_strength = get_market_strength()
    market_sentiment = get_market_sentiment()
    market_bias = get_market_bias()
    confidence = get_confidence()

    if side == "BUY":
        reversal = (
            current_price < e20
            and current_price < vwap_value
            and not macd_bullish
            and not supertrend_bullish
        )
        exhaustion = (
            rsi >= 76
            or (profit_r >= 2.0 and adx_value < 22)
            or (current_price >= target2 > 0 and not volume_confirmed)
        )
        market_conflict = (
            market_sentiment == "BEARISH"
            or market_bias == "BEARISH"
        )
        dynamic_trail = max(e20 - atr_value * 0.35, current_price - atr_value * 1.50)
        best_exit = target3 if profit_r >= 2.5 and not exhaustion else target2
        zone = (max(target1, current_price - atr_value * 0.20), current_price + atr_value * 0.30)
    else:
        reversal = (
            current_price > e20
            and current_price > vwap_value
            and macd_bullish
            and supertrend_bullish
        )
        exhaustion = (
            rsi <= 24
            or (profit_r >= 2.0 and adx_value < 22)
            or (current_price <= target2 > 0 and not volume_confirmed)
        )
        market_conflict = (
            market_sentiment == "BULLISH"
            or market_bias == "BULLISH"
        )
        dynamic_trail = min(e20 + atr_value * 0.35, current_price + atr_value * 1.50)
        best_exit = target3 if profit_r >= 2.5 and not exhaustion else target2
        zone = (current_price - atr_value * 0.30, min(target1, current_price + atr_value * 0.20))

    full_exit = (
        profit <= -initial_risk
        or reversal
        or (market_conflict and profit_r > 0)
        or (exhaustion and profit_r >= 1.5)
    )
    partial_exit = (
        not full_exit
        and (
            profit_r >= 1.5
            or exhaustion
            or (target1 > 0 and ((side == "BUY" and current_price >= target1) or (side == "SELL" and current_price <= target1)))
        )
    )
    trail_stop = (
        not full_exit
        and profit_r >= 1.0
        and adx_value >= 22
        and market_strength >= 70
        and confidence >= 75
    )

    if full_exit:
        best_exit = current_price
    elif not trail_stop and not partial_exit:
        best_exit = 0.0

    exit_confidence = int(
        min(
            100,
            max(
                0,
                round(
                    (40 if reversal else 0)
                    + (25 if exhaustion else 0)
                    + (15 if market_conflict else 0)
                    + (15 if profit_r >= 1.5 else 0)
                    + (10 if not volume_confirmed else 0)
                ),
            ),
        )
    )

    _exit_analysis = {
        "best_exit": round(best_exit, 2) if best_exit > 0 else 0.0,
        "exit_zone": (round(zone[0], 2), round(zone[1], 2)),
        "exit_confidence": exit_confidence,
        "exit_timing": "NOW" if full_exit else "PARTIAL" if partial_exit else "TRAIL",
        "partial_exit": partial_exit,
        "full_exit": full_exit,
        "trail_stop": trail_stop,
        "trailing_stop_price": round(dynamic_trail, 2) if trail_stop else 0.0,
        "profit_protection": profit_r >= 1.0,
        "trend_exhaustion": exhaustion,
    }

    return _exit_analysis.copy()


def get_best_exit():
    return _exit_analysis["best_exit"]


def get_exit_zone():
    return _exit_analysis["exit_zone"]


def get_exit_confidence():
    return _exit_analysis["exit_confidence"]


def should_partial_exit():
    return bool(_exit_analysis["partial_exit"])


def should_full_exit():
    return bool(_exit_analysis["full_exit"])


def should_trail_stop():
    return bool(_exit_analysis["trail_stop"])
