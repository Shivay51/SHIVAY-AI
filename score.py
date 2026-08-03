from indicators import (
    ema20,
    ema50,
    ema200,
    atr,
    supertrend,
    adx,
    macd,
    vwap,
    volume_spike,
)

from rsi import calculate_rsi
from entryfilter import late_entry_filter
from support_resistance import support_resistance, trade_allowed


def calculate_score(market):

    close = market["close"]
    high = market["high"]
    low = market["low"]
    volume = market["volume"]

    price = market["price"]
    day_high = market["day_high"]
    day_low = market["day_low"]

    score = 0

    e20 = ema20(close)
    e50 = ema50(close)
    e200 = ema200(close)

    rsi = calculate_rsi(close)

    atr_value = atr(high, low, close)

    st = supertrend(high, low, close)

    adx_value = adx(high, low, close)

    macd_buy = macd(close)

    vwap_value = vwap(high, low, close, volume)

    vol_spike = volume_spike(volume)

    # ===================================
    # Support / Resistance
    # ===================================

    sr = support_resistance(high, low, close)

    support = sr["support"]
    resistance = sr["resistance"]

    trade_ok = trade_allowed(
        price,
        atr_value,
        support,
        resistance
    )

    # ===================================
    # EMA (25)
    # ===================================

    if e20 > e50 > e200:
        score += 25
    elif e20 > e50:
        score += 18
    elif e20 > e200:
        score += 10

    # ===================================
    # RSI (15)
    # ===================================

    if 55 <= rsi <= 68:
        score += 15
    elif 50 <= rsi < 55:
        score += 10
    elif 68 < rsi <= 75:
        score += 8
    elif rsi > 80:
        score -= 10

    # ===================================
    # SUPERTREND (15)
    # ===================================

    if st:
        score += 15

    # ===================================
    # VWAP (10)
    # ===================================

    if price > vwap_value:
        score += 10

    # ===================================
    # ADX (10)
    # ===================================

    if adx_value >= 30:
        score += 10
    elif adx_value >= 25:
        score += 7
    elif adx_value >= 20:
        score += 4

    # ===================================
    # MACD (10)
    # ===================================

    if macd_buy:
        score += 10

    # ===================================
    # Volume (5)
    # ===================================

    if vol_spike:
        score += 5

    # ===================================
    # ATR (5)
    # ===================================

    atr_percent = (atr_value / price) * 100

    if 0.20 <= atr_percent <= 3:
        score += 5

    # ===================================
    # Price Above EMA20 (5)
    # ===================================

    if price > e20:
        score += 5

    # ===================================
    # Late Entry (Penalty)
    # ===================================

    if not late_entry_filter(
        price,
        day_high,
        day_low,
        atr_value
    ):
        score -= 10

    # ===================================
    # Risk Reward (Penalty)
    # ===================================

    if not trade_ok:
        score -= 10

    score = max(0, min(100, score))

    return {

        "score": score,

        "ema20": round(e20, 2),

        "ema50": round(e50, 2),

        "ema200": round(e200, 2),

        "rsi": round(rsi, 2),

        "atr": round(atr_value, 2),

        "adx": round(adx_value, 2),

        "supertrend": st,

        "macd": macd_buy,

        "vwap": round(vwap_value, 2),

        "volume_spike": vol_spike,

        "support": round(support, 2),

        "resistance": round(resistance, 2),

    }