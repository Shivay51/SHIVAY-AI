import yfinance as yf
from yahoo_runtime import configure_yfinance

configure_yfinance(yf)
import pandas as pd

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
            timeout=8,
        )

        if df.empty:
            return False

        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)

        close = pd.Series(df["Close"])

        ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean().iloc[-1]

        return ema20 > ema50

    except Exception:
        return False
