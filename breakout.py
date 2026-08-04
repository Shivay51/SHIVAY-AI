import pandas as pd


def breakout_filter(high, low, close, volume):

    high = pd.Series(high)
    low = pd.Series(low)
    close = pd.Series(close)
    volume = pd.Series(volume)

    if len(close) < 25:
        return False

    resistance = high.iloc[-21:-1].max()
    support = low.iloc[-21:-1].min()

    current_close = close.iloc[-1]
    previous_close = close.iloc[-2]

    current_volume = volume.iloc[-1]
    avg_volume = volume.iloc[-20:].mean()

    # BUY Breakout
    buy_breakout = (
        current_close >= resistance * 0.998
        and current_volume >= avg_volume * 1.10
        and current_close > previous_close
    )

    # SELL Breakdown
    sell_breakdown = (
        current_close <= support * 1.002
        and current_volume >= avg_volume * 1.10
        and current_close < previous_close
    )

    return buy_breakout or sell_breakdown