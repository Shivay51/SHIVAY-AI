import yfinance as yf

SYMBOLS = {
    "NIFTY FUT": "^NSEI",
    "BANKNIFTY FUT": "^NSEBANK",
    "RELIANCE FUT": "RELIANCE.NS",
    "ICICIBANK FUT": "ICICIBANK.NS",
    "SBIN FUT": "SBIN.NS",
    "TCS FUT": "TCS.NS",
    "TVSMOTOR FUT": "TVSMOTOR.NS",
    "INFY FUT": "INFY.NS",
    "LT FUT": "LT.NS",
    "HDFCBANK FUT": "HDFCBANK.NS",
}


def get_market_data(symbol):

    ticker = SYMBOLS.get(symbol)

    if ticker is None:
        return None

    df = yf.download(
        ticker,
        period="5d",
        interval="5m",
        progress=False,
        auto_adjust=False,
        group_by="column"
    )

    if df.empty:
        return None

    # Fix MultiIndex Columns
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    return {
        "symbol": symbol,
        "price": float(df["Close"].iloc[-1]),
        "close": df["Close"],
        "open": df["Open"],
        "high": df["High"],
        "low": df["Low"],
        "volume": df["Volume"],
    }