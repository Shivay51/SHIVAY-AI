import pandas as pd
import yfinance as yf
import pandas_ta as ta


def get_market_trend():

    try:

        nifty = yf.download(
            "^NSEI",
            period="5d",
            interval="15m",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        banknifty = yf.download(
            "^NSEBANK",
            period="5d",
            interval="15m",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        if nifty.empty or banknifty.empty:
            return False

        # MultiIndex Fix
        if hasattr(nifty.columns, "nlevels") and nifty.columns.nlevels > 1:
            nifty.columns = nifty.columns.get_level_values(0)

        if hasattr(banknifty.columns, "nlevels") and banknifty.columns.nlevels > 1:
            banknifty.columns = banknifty.columns.get_level_values(0)

        # EMA 20
        nifty_ema20 = ta.ema(pd.Series(nifty["Close"]), length=20).iloc[-1]
        bank_ema20 = ta.ema(pd.Series(banknifty["Close"]), length=20).iloc[-1]

        nifty_price = float(nifty["Close"].iloc[-1])
        bank_price = float(banknifty["Close"].iloc[-1])

        nifty_up = nifty_price > nifty_ema20
        bank_up = bank_price > bank_ema20

        # બંને Bullish હોય તો જ Market OK
        return nifty_up and bank_up

    except Exception:
        return False