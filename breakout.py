import pandas as pd


def breakout_filter(high, low, close, volume):

    high = pd.Series(high)
    close = pd.Series(close)
    volume = pd.Series(volume)

    # Minimum Candles
    if len(close) < 25:
        return False

    # Last 20 Candle Resistance
    resistance = high.iloc[-21:-1].max()

    current_close = close.iloc[-1]

    current_volume = volume.iloc[-1]

    avg_volume = volume.iloc[-20:].mean()

    # Resistance Break
    breakout = current_close >= resistance * 0.999

    # Volume Confirmation
    volume_ok = current_volume >= avg_volume * 1.20

    # Momentum Confirmation
    momentum = close.iloc[-1] > close.iloc[-2]

    return breakout and volume_ok and momentum