import pandas as pd


def calculate_rsi(close, period=14):

    close = pd.Series(close, dtype="float64").dropna()

    if len(close) < period + 1:
        return 50.0

    delta = close.diff()

    gain = delta.where(delta > 0, 0)

    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(period).mean()

    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))

    rsi = 100 - (100 / (1 + rs))

    value = rsi.dropna()
    if value.empty:
        if float(avg_gain.iloc[-1]) > 0 and float(avg_loss.iloc[-1]) == 0:
            return 100.0
        return 50.0
    return round(float(value.iloc[-1]), 2)
