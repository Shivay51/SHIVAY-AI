import pandas as pd
import pandas_ta as ta


def pullback_filter(high, low, close):

    close = pd.Series(close)

    if len(close) < 50:
        return False

    ema20 = ta.ema(close, length=20).iloc[-1]
    ema50 = ta.ema(close, length=50).iloc[-1]

    price = close.iloc[-1]
    previous = close.iloc[-2]

    # Trend (BUY અથવા SELL)
    bullish_trend = ema20 > ema50
    bearish_trend = ema20 < ema50

    # EMA નજીક (2%)
    near_ema20 = abs(price - ema20) <= (price * 0.02)

    # Momentum
    bullish = price > previous
    bearish = price < previous

    # Support / Resistance Hold
    support_hold = price >= (ema20 * 0.99)
    resistance_hold = price <= (ema20 * 1.01)

    buy_pullback = (
        bullish_trend
        and near_ema20
        and bullish
        and support_hold
    )

    sell_pullback = (
        bearish_trend
        and near_ema20
        and bearish
        and resistance_hold
    )

    return buy_pullback or sell_pullback