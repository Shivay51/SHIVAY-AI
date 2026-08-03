import logging
import yfinance as yf

# Yahoo Finance Log Hide
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

SYMBOLS = {

    # INDEX
    "NIFTY FUT": "^NSEI",
    "BANKNIFTY FUT": "^NSEBANK",

    # BANKS
    "HDFCBANK FUT": "HDFCBANK.NS",
    "ICICIBANK FUT": "ICICIBANK.NS",
    "SBIN FUT": "SBIN.NS",
    "AXISBANK FUT": "AXISBANK.NS",
    "KOTAKBANK FUT": "KOTAKBANK.NS",
    "INDUSINDBK FUT": "INDUSINDBK.NS",
    "PNB FUT": "PNB.NS",

    # IT
    "TCS FUT": "TCS.NS",
    "INFY FUT": "INFY.NS",
    "HCLTECH FUT": "HCLTECH.NS",
    "TECHM FUT": "TECHM.NS",
    "WIPRO FUT": "WIPRO.NS",
    "PERSISTENT FUT": "PERSISTENT.NS",

    # AUTO
    "MARUTI FUT": "MARUTI.NS",
    "TVSMOTOR FUT": "TVSMOTOR.NS",
    "EICHERMOT FUT": "EICHERMOT.NS",

    # ENERGY
    "RELIANCE FUT": "RELIANCE.NS",
    "ONGC FUT": "ONGC.NS",
    "BPCL FUT": "BPCL.NS",
    "IOC FUT": "IOC.NS",
    "GAIL FUT": "GAIL.NS",
    "NTPC FUT": "NTPC.NS",
    "POWERGRID FUT": "POWERGRID.NS",
    "TATAPOWER FUT": "TATAPOWER.NS",

    # METALS
    "TATASTEEL FUT": "TATASTEEL.NS",
    "JSWSTEEL FUT": "JSWSTEEL.NS",
    "HINDALCO FUT": "HINDALCO.NS",
    "VEDL FUT": "VEDL.NS",

    # CAPITAL GOODS
    "LT FUT": "LT.NS",
    "SIEMENS FUT": "SIEMENS.NS",
    "ABB FUT": "ABB.NS",
    "BHEL FUT": "BHEL.NS",
    "CUMMINSIND FUT": "CUMMINSIND.NS",

    # DEFENCE
    "HAL FUT": "HAL.NS",
    "BEL FUT": "BEL.NS",

    # FMCG
    "ITC FUT": "ITC.NS",
    "HINDUNILVR FUT": "HINDUNILVR.NS",
    "NESTLEIND FUT": "NESTLEIND.NS",

    # RETAIL
    "TRENT FUT": "TRENT.NS",

    # TELECOM
    "BHARTIARTL FUT": "BHARTIARTL.NS",
}


def get_market_data(symbol):

    ticker = SYMBOLS.get(symbol)

    if ticker is None:
        return None

    try:

        df = yf.download(
            ticker,
            period="1mo",
            interval="15m",
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        if df.empty:
            return None

        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)

        return {

            "symbol": symbol,

            "price": float(df["Close"].iloc[-1]),

            "open": df["Open"],

            "high": df["High"],

            "low": df["Low"],

            "close": df["Close"],

            "volume": df["Volume"],

            "day_high": float(df["High"].tail(26).max()),

            "day_low": float(df["Low"].tail(26).min()),

        }

    except Exception:

        return None