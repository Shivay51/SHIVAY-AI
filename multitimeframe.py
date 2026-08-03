import yfinance as yf
import pandas as pd
import pandas_ta as ta


def timeframe_trend(symbol):

    try:

        # 15 Minute
        df15 = yf.download(
            symbol,
            period="5d",
            interval="15m",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        # 1 Hour
        df1h = yf.download(
            symbol,
            period="3mo",
            interval="1h",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        # 4 Hour (Yahoo પાસે સીધો 4h નથી, એટલે Daily EMA વડે Major Trend)
        dfd = yf.download(
            symbol,
            period="6mo",
            interval="1d",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        if df15.empty or df1h.empty or dfd.empty:
            return False

        # MultiIndex Fix
        for df in (df15, df1h, dfd):
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)

        # EMA20
        ema15 = ta.ema(pd.Series(df15["Close"]), length=20).iloc[-1]
        ema1h = ta.ema(pd.Series(df1h["Close"]), length=20).iloc[-1]
        emaD = ta.ema(pd.Series(dfd["Close"]), length=20).iloc[-1]

        price15 = float(df15["Close"].iloc[-1])
        price1h = float(df1h["Close"].iloc[-1])
        priceD = float(dfd["Close"].iloc[-1])

        trend15 = price15 > ema15
        trend1h = price1h > ema1h
        trendD = priceD > emaD

        # ત્રણેય Bullish હોવા જોઈએ
        return trend15 and trend1h and trendD

    except Exception:
        return False