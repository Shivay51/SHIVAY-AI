import pandas as pd
import pandas_ta as ta


def pullback_filter(high, low, close):

    close = pd.Series(close)

    if len(close) < 50:
        return False

    # EMA
    ema20 = ta.ema(close, length=20).iloc[-1]
    ema50 = ta.ema(close, length=50).iloc[-1]

    price = close.iloc[-1]
    previous = close.iloc[-2]

    # Strong Trend
    trend = ema20 > ema50

    # Price EMA20 નજીક (0.75%)
    near_ema20 = abs(price - ema20) <= (price * 0.0075)

    # Bullish Momentum
    bullish = price > previous

    # EMA20 ઉપર અથવા બહુ નજીક
    support_hold = price >= (ema20 * 0.995)

    return (
        trend
        and near_ema20
        and bullish
        and support_hold
    )