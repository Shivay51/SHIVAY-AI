import pandas as pd


def ema20(close):
    s = pd.Series(close)
    return s.ewm(span=20, adjust=False).mean().iloc[-1]


def ema50(close):
    s = pd.Series(close)
    return s.ewm(span=50, adjust=False).mean().iloc[-1]


def ema200(close):
    s = pd.Series(close)
    return s.ewm(span=200, adjust=False).mean().iloc[-1]


def atr(high, low, close, period=14):

    high = pd.Series(high)
    low = pd.Series(low)
    close = pd.Series(close)

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_value = tr.rolling(period).mean()

    return atr_value.iloc[-1]