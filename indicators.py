import pandas as pd
import pandas_ta as ta


# ==========================================
# EMA
# ==========================================

def ema20(close):
    ema = ta.ema(pd.Series(close), length=20)
    return float(ema.ffill().iloc[-1])


def ema50(close):
    ema = ta.ema(pd.Series(close), length=50)
    return float(ema.ffill().iloc[-1])


def ema200(close):
    ema = ta.ema(pd.Series(close), length=200)
    return float(ema.ffill().iloc[-1])


# ==========================================
# ATR
# ==========================================

def atr(high, low, close):

    value = ta.atr(
        pd.Series(high),
        pd.Series(low),
        pd.Series(close),
        length=14,
    )

    return float(value.ffill().iloc[-1])


# ==========================================
# SUPERTREND
# ==========================================

def supertrend(high, low, close):

    st = ta.supertrend(
        pd.Series(high),
        pd.Series(low),
        pd.Series(close),
        length=10,
        multiplier=3,
    )

    direction = [c for c in st.columns if c.startswith("SUPERTd")]

    if not direction:
        return False

    return int(st[direction[0]].iloc[-1]) == 1


# ==========================================
# ADX
# ==========================================

def adx(high, low, close):

    a = ta.adx(
        pd.Series(high),
        pd.Series(low),
        pd.Series(close),
        length=14,
    )

    adx_col = [c for c in a.columns if c.startswith("ADX")]

    if not adx_col:
        return 0.0

    return float(a[adx_col[0]].ffill().iloc[-1])


# ==========================================
# MACD
# ==========================================

def macd(close):

    m = ta.macd(pd.Series(close))

    macd_col = [c for c in m.columns if c.startswith("MACD_")]
    signal_col = [c for c in m.columns if c.startswith("MACDs_")]

    if not macd_col or not signal_col:
        return False

    return (
        float(m[macd_col[0]].iloc[-1])
        >
        float(m[signal_col[0]].iloc[-1])
    )


# ==========================================
# VWAP
# ==========================================

def vwap(high, low, close, volume):

    df = pd.DataFrame({
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })

    vw = ta.vwap(
        df["high"],
        df["low"],
        df["close"],
        df["volume"],
    )

    return float(vw.ffill().iloc[-1])


# ==========================================
# VOLUME SPIKE
# ==========================================

def volume_spike(volume):

    volume = pd.Series(volume)

    if len(volume) < 20:
        return False

    avg_volume = volume.tail(20).mean()

    latest_volume = volume.iloc[-1]

    return latest_volume > avg_volume * 1.5