import time
import yfinance as yf
import pandas as pd
import pandas_ta as ta

# ==========================================
# CACHE
# ==========================================

_market_cache = None
_last_update = 0

CACHE_TIME = 300  # 5 Minutes


# ==========================================
# LOAD MARKET
# ==========================================

def load_market_cache():

    global _market_cache
    global _last_update

    now = time.time()

    if _market_cache is not None and (now - _last_update) < CACHE_TIME:
        return _market_cache

    try:

        nifty = yf.download(
            "^NSEI",
            period="5d",
            interval="15m",
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        bank = yf.download(
            "^NSEBANK",
            period="5d",
            interval="15m",
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        if nifty.empty or bank.empty:
            return None

        if hasattr(nifty.columns, "nlevels") and nifty.columns.nlevels > 1:
            nifty.columns = nifty.columns.get_level_values(0)

        if hasattr(bank.columns, "nlevels") and bank.columns.nlevels > 1:
            bank.columns = bank.columns.get_level_values(0)

        nifty_close = pd.Series(nifty["Close"])
        bank_close = pd.Series(bank["Close"])

        nifty_price = float(nifty_close.iloc[-1])
        bank_price = float(bank_close.iloc[-1])

        nifty_ema20 = float(ta.ema(nifty_close, length=20).iloc[-1])
        nifty_ema50 = float(ta.ema(nifty_close, length=50).iloc[-1])

        bank_ema20 = float(ta.ema(bank_close, length=20).iloc[-1])
        bank_ema50 = float(ta.ema(bank_close, length=50).iloc[-1])

        nifty_bullish = nifty_price > nifty_ema20
        bank_bullish = bank_price > bank_ema20

        # ==========================================
        # MARKET DIRECTION
        # ==========================================

        if nifty_bullish and bank_bullish:
            direction = "🟢 BULLISH"
        elif (not nifty_bullish) and (not bank_bullish):
            direction = "🔴 BEARISH"
        else:
            direction = "🟡 SIDEWAYS"

        # ==========================================
        # MARKET STRENGTH (0–100)
        # ==========================================

        strength = 0

        if nifty_price > nifty_ema20:
            strength += 25

        if nifty_price > nifty_ema50:
            strength += 25

        if bank_price > bank_ema20:
            strength += 25

        if bank_price > bank_ema50:
            strength += 25

        _market_cache = {

            "market_direction": direction,

            "market_strength": strength,

            "nifty_price": nifty_price,
            "bank_price": bank_price,

            "nifty_ema20": nifty_ema20,
            "nifty_ema50": nifty_ema50,

            "bank_ema20": bank_ema20,
            "bank_ema50": bank_ema50,

            "nifty_bullish": nifty_bullish,
            "bank_bullish": bank_bullish,

        }

        _last_update = now

        return _market_cache

    except Exception as e:

        print(f"❌ Market Cache Error : {e}")
        return None


# ==========================================
# MARKET FILTER
# ==========================================

def is_market_bullish():

    data = load_market_cache()

    if data is None:
        return True

    return data["market_strength"] >= 50


# ==========================================
# MARKET DIRECTION
# ==========================================

def get_market_direction():

    data = load_market_cache()

    if data is None:
        return "UNKNOWN"

    return data["market_direction"]


# ==========================================
# MARKET STRENGTH
# ==========================================

def get_market_strength():

    data = load_market_cache()

    if data is None:
        return 0

    return data["market_strength"]