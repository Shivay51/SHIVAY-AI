# gift_nifty.py

import time

import pandas as pd
import yfinance as yf
from yahoo_runtime import configure_yfinance

configure_yfinance(yf)

from indicators import (
    adx,
    atr,
    ema20,
    ema50,
    ema200,
    macd,
    supertrend,
    vwap,
    volume_spike,
)
from rsi import calculate_rsi


_gift_nifty_cache = None
_gift_nifty_cache_time = 0.0
_gift_nifty_cache_ttl = 300
_gift_nifty_symbols = (
    "^NSEI",
    "NIFTY50.NS",
)


def _load_gift_nifty_data():
    try:
        from tvkit_provider import TVKitProvider
        candles = TVKitProvider().get_historical_candles("GIFT NIFTY", "5m", 5)
        if len(candles) >= 200:
            frame = pd.DataFrame({
                "High": [row["high"] for row in candles], "Low": [row["low"] for row in candles],
                "Close": [row["close"] for row in candles], "Volume": [row["volume"] for row in candles],
            }, index=[row["timestamp"] for row in candles])
            return "NSEIX:NIFTY1!", frame
    except Exception:
        pass
    for symbol in _gift_nifty_symbols:
        try:
            data = yf.download(
                symbol,
                period="5d",
                interval="5m",
                auto_adjust=True,
                progress=False,
                threads=False,
                timeout=8,
            )

            if data is None or data.empty:
                continue

            if hasattr(data.columns, "nlevels") and data.columns.nlevels > 1:
                data.columns = data.columns.get_level_values(0)

            if not {"High", "Low", "Close", "Volume"}.issubset(data.columns):
                continue

            data = data[["High", "Low", "Close", "Volume"]].dropna()

            if len(data) >= 200:
                return symbol, data
        except Exception:
            continue

    return None, None


def _get_gift_nifty_analysis():
    global _gift_nifty_cache
    global _gift_nifty_cache_time

    now = time.time()

    if (
        _gift_nifty_cache is not None
        and now - _gift_nifty_cache_time < _gift_nifty_cache_ttl
    ):
        return _gift_nifty_cache

    symbol, data = _load_gift_nifty_data()

    if data is None:
        return {
            "symbol": "GIFT NIFTY",
            "price": 0.0,
            "direction": "UNKNOWN",
            "regime": "UNKNOWN",
            "strength": 0,
            "confidence": 0,
            "tradeable": False,
            "volume_confirmed": False,
            "trend_quality": 0,
            "risk_mode": "HIGH",
        }

    high = data["High"].astype("float64").tolist()
    low = data["Low"].astype("float64").tolist()
    close = data["Close"].astype("float64").tolist()
    volume = data["Volume"].astype("float64").tolist()

    price = float(close[-1])
    e20 = ema20(close)
    e50 = ema50(close)
    e200 = ema200(close)
    atr_value = atr(high, low, close)
    adx_value = adx(high, low, close)
    rsi_value = float(calculate_rsi(pd.Series(close, dtype="float64")))
    vwap_value = vwap(high, low, close, volume)
    macd_bullish = macd(close)
    supertrend_bullish = supertrend(high, low, close)

    average_volume = sum(volume[-21:-1]) / 20
    volume_confirmed = (
        volume_spike(volume)
        if average_volume > 0
        else True
    )
    atr_percent = (atr_value / price) * 100 if price > 0 else 0.0
    normal_volatility = 0.10 <= atr_percent <= 1.80
    bullish_alignment = e20 > e50 > e200
    bearish_alignment = e20 < e50 < e200
    bullish_vwap = price > vwap_value > 0
    bearish_vwap = price < vwap_value and vwap_value > 0

    bullish_score = 0
    bearish_score = 0

    if bullish_alignment:
        bullish_score += 30
    elif e20 > e50 and price > e200:
        bullish_score += 15

    if bearish_alignment:
        bearish_score += 30
    elif e20 < e50 and price < e200:
        bearish_score += 15

    if bullish_vwap:
        bullish_score += 12

    if bearish_vwap:
        bearish_score += 12

    if adx_value >= 30:
        if bullish_alignment:
            bullish_score += 15
        elif bearish_alignment:
            bearish_score += 15
    elif adx_value >= 22:
        if bullish_alignment:
            bullish_score += 8
        elif bearish_alignment:
            bearish_score += 8

    if macd_bullish:
        bullish_score += 10
    else:
        bearish_score += 10

    if supertrend_bullish:
        bullish_score += 10
    else:
        bearish_score += 10

    if 55 <= rsi_value <= 70:
        bullish_score += 10
    elif 30 <= rsi_value <= 45:
        bearish_score += 10
    elif rsi_value >= 76:
        bullish_score -= 12
    elif rsi_value <= 24:
        bearish_score -= 12

    if volume_confirmed:
        if bullish_alignment:
            bullish_score += 8
        elif bearish_alignment:
            bearish_score += 8

    if normal_volatility:
        if bullish_alignment:
            bullish_score += 5
        elif bearish_alignment:
            bearish_score += 5

    extension = abs(price - e20) / atr_value if atr_value > 0 else 0.0
    overextended = extension > 2.0

    if overextended:
        bullish_score -= 15
        bearish_score -= 15

    bullish_score = max(0, min(100, bullish_score))
    bearish_score = max(0, min(100, bearish_score))

    if bullish_score > bearish_score:
        strength = int(bullish_score)
        if strength >= 80 and adx_value >= 30:
            regime = "STRONG BULL"
            direction = "BULLISH"
        elif strength >= 65 and adx_value >= 22:
            regime = "BULL"
            direction = "BULLISH"
        else:
            regime = "SIDEWAYS"
            direction = "SIDEWAYS"
    elif bearish_score > bullish_score:
        strength = int(bearish_score)
        if strength >= 80 and adx_value >= 30:
            regime = "STRONG BEAR"
            direction = "BEARISH"
        elif strength >= 65 and adx_value >= 22:
            regime = "BEAR"
            direction = "BEARISH"
        else:
            regime = "SIDEWAYS"
            direction = "SIDEWAYS"
    else:
        strength = 0
        regime = "SIDEWAYS"
        direction = "SIDEWAYS"

    confidence = strength

    if volume_confirmed:
        confidence += 5

    if normal_volatility:
        confidence += 5

    if overextended:
        confidence -= 15

    if regime == "SIDEWAYS":
        confidence -= 20

    confidence = max(0, min(100, int(confidence)))
    trend_quality = max(0, min(100, int(adx_value * 2.5)))

    if not normal_volatility or overextended:
        risk_mode = "HIGH"
    elif confidence >= 80:
        risk_mode = "LOW"
    else:
        risk_mode = "MODERATE"

    tradeable = (
        price >= 200
        and regime in ("STRONG BULL", "BULL", "STRONG BEAR", "BEAR")
        and strength >= 70
        and confidence >= 70
        and adx_value >= 22
        and volume_confirmed
        and normal_volatility
        and not overextended
    )

    _gift_nifty_cache = {
        "symbol": symbol,
        "price": round(price, 2),
        "direction": direction,
        "regime": regime,
        "strength": strength,
        "confidence": confidence,
        "tradeable": tradeable,
        "volume_confirmed": volume_confirmed,
        "trend_quality": trend_quality,
        "risk_mode": risk_mode,
    }
    _gift_nifty_cache_time = now

    return _gift_nifty_cache


def get_gift_nifty_strength():
    return _get_gift_nifty_analysis()["strength"]


def get_gift_nifty_confidence():
    return _get_gift_nifty_analysis()["confidence"]


def get_gift_nifty_direction():
    return _get_gift_nifty_analysis()["direction"]


def get_gift_nifty_regime():
    return _get_gift_nifty_analysis()["regime"]


def is_gift_nifty_bullish():
    return get_gift_nifty_direction() == "BULLISH"


def is_gift_nifty_bearish():
    return get_gift_nifty_direction() == "BEARISH"


def is_gift_nifty_tradeable():
    return bool(_get_gift_nifty_analysis()["tradeable"])
