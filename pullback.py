import pandas as pd

from indicators import (
    ema20,
    ema50,
    ema200,
    atr,
    supertrend,
    adx,
    macd,
)


def pullback_filter(high, low, close):
    data = pd.DataFrame(
        {
            "high": pd.Series(high, dtype="float64"),
            "low": pd.Series(low, dtype="float64"),
            "close": pd.Series(close, dtype="float64"),
        }
    ).dropna()

    if len(data) < 200:
        return False

    high_values = data["high"].tolist()
    low_values = data["low"].tolist()
    close_values = data["close"].tolist()

    price = float(close_values[-1])
    previous_price = float(close_values[-2])
    current_high = float(high_values[-1])
    current_low = float(low_values[-1])

    if price < 200:
        return False

    e20 = ema20(close_values)
    e50 = ema50(close_values)
    e200 = ema200(close_values)
    atr_value = atr(high_values, low_values, close_values)
    adx_value = adx(high_values, low_values, close_values)
    macd_buy = macd(close_values)
    supertrend_buy = supertrend(
        high_values,
        low_values,
        close_values,
    )

    if atr_value <= 0 or adx_value < 22:
        return False

    recent_low = min(low_values[-5:])
    recent_high = max(high_values[-5:])
    candle_range = current_high - current_low
    candle_body = abs(price - previous_price)
    candle_strength = (
        candle_body / candle_range
        if candle_range > 0
        else 0.0
    )

    bullish_trend = e20 > e50 > e200
    bearish_trend = e20 < e50 < e200

    buy_pullback = (
        bullish_trend
        and supertrend_buy
        and macd_buy
        and price > e20
        and price > previous_price
        and recent_low <= e20 + atr_value * 0.30
        and recent_low >= e50 - atr_value * 0.25
        and price - e20 <= atr_value * 0.80
        and candle_strength >= 0.45
    )

    sell_pullback = (
        bearish_trend
        and not supertrend_buy
        and not macd_buy
        and price < e20
        and price < previous_price
        and recent_high >= e20 - atr_value * 0.30
        and recent_high <= e50 + atr_value * 0.25
        and e20 - price <= atr_value * 0.80
        and candle_strength >= 0.45
    )

    return buy_pullback or sell_pullback
