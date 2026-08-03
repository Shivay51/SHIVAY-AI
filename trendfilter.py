import yfinance as yf
import pandas as pd
import pandas_ta as ta

from data import SYMBOLS


def higher_timeframe_trend(symbol):

    ticker = SYMBOLS.get(symbol)

    if not ticker:
        return False

    try:

        df = yf.download(
            ticker,
            period="3mo",
            interval="1h",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        if df.empty:
            return False

        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)

        close = pd.Series(df["Close"])

        ema20 = ta.ema(close, length=20).iloc[-1]
        ema50 = ta.ema(close, length=50).iloc[-1]

        return ema20 > ema50

    except Exception:
        return False