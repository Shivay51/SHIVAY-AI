from core.decision_engine import get_decision
from config import MIN_SCORE, MIN_RISK_REWARD


def analyze_trade(symbol, score_data):
    side = get_decision(score_data)

    score = int(score_data.get("score", 0))
    price = float(score_data.get("price", 0))
    ema20 = float(score_data.get("ema20", 0))
    ema50 = float(score_data.get("ema50", 0))
    ema200 = float(score_data.get("ema200", 0))
    rsi = float(score_data.get("rsi", 0))
    adx_value = float(score_data.get("adx", 0))
    vwap_value = float(score_data.get("vwap", 0))
    support = float(score_data.get("support", 0))
    resistance = float(score_data.get("resistance", 0))
    market_strength = float(score_data.get("market_strength", 0))
    market_direction = str(score_data.get("market", "UNKNOWN"))
    setup = str(score_data.get("setup", ""))

    volume_ok = bool(score_data.get("volume_spike", False))
    relative_volume = float(score_data.get("relative_volume", 0) or 0)
    timeframe_aligned = bool(score_data.get("timeframe_aligned", False))
    entry_timing = bool(score_data.get("entry_timing_confirmed", False))
    context_score = float(score_data.get("signal_context_score", 0) or 0)
    macd_ok = bool(score_data.get("macd", False))
    supertrend_ok = bool(score_data.get("supertrend", False))
    chandelier_buy = bool(score_data.get("chandelier_buy_confirmed", False))
    chandelier_sell = bool(score_data.get("chandelier_sell_confirmed", False))

    atr_value = float(score_data.get("atr", 0))
    extension = abs(price - ema20) / atr_value if atr_value > 0 else 99.0

    buy_quality = (
        side == "BUY"
        and price >= 200
        and score >= max(72, int(MIN_SCORE))
        and ema20 > ema50 > ema200
        and price > vwap_value > 0
        and adx_value >= 22
        and 54 <= rsi <= 72
        and macd_ok
        and supertrend_ok
        and volume_ok
        and relative_volume >= 1.0
        and timeframe_aligned
        and entry_timing
        and context_score >= 75
        and market_strength >= 55
        and "SIDEWAYS" not in market_direction
        and "BEARISH" not in market_direction
        and atr_value > 0
        and extension <= 2.2
        and chandelier_buy
        and (
            "Confirmed Breakout" in setup
            or "Confirmed Pullback" in setup
        )
    )

    sell_quality = (
        side == "SELL"
        and price >= 200
        and score >= max(72, int(MIN_SCORE))
        and ema20 < ema50 < ema200
        and price < vwap_value
        and adx_value >= 22
        and 25 <= rsi <= 48
        and not macd_ok
        and not supertrend_ok
        and volume_ok
        and relative_volume >= 1.0
        and timeframe_aligned
        and entry_timing
        and context_score >= 75
        and "BULLISH" not in market_direction
        and atr_value > 0
        and extension <= 2.2
        and chandelier_sell
        and (
            "Confirmed Breakout" in setup
            or "Confirmed Pullback" in setup
        )
    )

    if buy_quality:
        confidence_value = score

        if market_strength >= 80:
            confidence_value += 3

        if adx_value >= 30:
            confidence_value += 3

        if "Confirmed Breakout" in setup:
            confidence_value += 2

        confidence_value = min(99, confidence_value)

        strong = confidence_value >= 95 and relative_volume >= 1.5 and timeframe_aligned and market_strength >= 80 and context_score >= 90
        if strong:
            decision = "🔥 STRONG BUY"
            risk = "VERY LOW"
        elif confidence_value >= 90:
            decision = "✅ BUY"
            risk = "LOW"
        else:
            decision = "✅ BUY"
            risk = "MEDIUM"

        confidence = f"{confidence_value}%"

    elif sell_quality:
        confidence_value = score

        if adx_value >= 30:
            confidence_value += 3

        if market_strength <= 40:
            confidence_value += 3

        confidence_value = min(99, confidence_value)

        strong = confidence_value >= 95 and relative_volume >= 1.5 and timeframe_aligned and market_strength <= 20 and context_score >= 90
        if strong:
            decision = "🔥 STRONG SELL"
            risk = "VERY LOW"
        elif confidence_value >= 90:
            decision = "🔻 SELL"
            risk = "LOW"
        else:
            decision = "🔻 SELL"
            risk = "MEDIUM"

        confidence = f"{confidence_value}%"

    else:
        decision = "👀 WATCH"
        risk = "HIGH"
        confidence = "0%"

    return {
        "symbol": symbol,
        "decision": decision,
        "risk": risk,
        "confidence": confidence,
    }
