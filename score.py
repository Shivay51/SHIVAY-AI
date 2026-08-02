from indicators import ema20, ema50, ema200, atr
from rsi import calculate_rsi


def calculate_score(market):

    close = market["close"]
    price = market["price"]

    score = 0

    e20 = ema20(close)
    e50 = ema50(close)
    e200 = ema200(close)

    atr_value = atr(
        market["high"],
        market["low"],
        market["close"]
    )

    rsi = calculate_rsi(close)

    # EMA Trend
    if e20 > e50 > e200:
        score += 40
    elif e20 > e50:
        score += 25
    elif e20 > e200:
        score += 15

    # RSI
    if 55 <= rsi <= 70:
        score += 20
    elif 45 <= rsi < 55:
        score += 10

    # Price Position
    if price > e20:
        score += 20
    elif price > e50:
        score += 10

    # ATR (Volatility)
    if atr_value > (price * 0.01):
        score += 10

    return {
        "score": score,
        "ema20": round(e20, 2),
        "ema50": round(e50, 2),
        "ema200": round(e200, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr_value, 2),
    }