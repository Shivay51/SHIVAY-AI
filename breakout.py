import pandas as pd

from indicators import (
    ema20,
    ema50,
    ema200,
    atr,
    supertrend,
    adx,
    macd,
    vwap,
    volume_spike,
)


def breakout_filter(high, low, close, volume):
    data = pd.DataFrame(
        {
            "high": pd.Series(high, dtype="float64"),
            "low": pd.Series(low, dtype="float64"),
            "close": pd.Series(close, dtype="float64"),
            "volume": pd.Series(volume, dtype="float64"),
        }
    ).dropna()

    if len(data) < 200:
        return False

    high_values = data["high"].tolist()
    low_values = data["low"].tolist()
    close_values = data["close"].tolist()
    volume_values = data["volume"].tolist()

    current_close = float(close_values[-1])
    previous_close = float(close_values[-2])
    current_high = float(high_values[-1])
    current_low = float(low_values[-1])

    e20 = ema20(close_values)
    e50 = ema50(close_values)
    e200 = ema200(close_values)
    atr_value = atr(high_values, low_values, close_values)
    adx_value = adx(high_values, low_values, close_values)
    vwap_value = vwap(
        high_values,
        low_values,
        close_values,
        volume_values,
    )
    macd_buy = macd(close_values)
    supertrend_buy = supertrend(
        high_values,
        low_values,
        close_values,
    )
    volume_ok = volume_spike(volume_values)

    if atr_value <= 0 or vwap_value <= 0:
        return False

    resistance = max(high_values[-21:-1])
    support = min(low_values[-21:-1])
    consolidation_high = max(high_values[-31:-1])
    consolidation_low = min(low_values[-31:-1])
    consolidation_range = consolidation_high - consolidation_low

    candle_range = current_high - current_low
    candle_body = abs(current_close - previous_close)
    candle_strength = (
        candle_body / candle_range
        if candle_range > 0
        else 0.0
    )

    breakout_buffer = max(atr_value * 0.10, current_close * 0.0005)
    maximum_extension = atr_value * 1.25
    consolidation_ok = consolidation_range <= atr_value * 8.0

    bullish_trend = (
        e20 > e50 > e200
        and current_close > e20
        and current_close > vwap_value
        and supertrend_buy
    )

    bearish_trend = (
        e20 < e50 < e200
        and current_close < e20
        and current_close < vwap_value
        and not supertrend_buy
    )

    buy_breakout = (
        bullish_trend
        and adx_value >= 22
        and macd_buy
        and volume_ok
        and consolidation_ok
        and current_close > resistance + breakout_buffer
        and current_close > previous_close
        and candle_strength >= 0.55
        and current_close - resistance <= maximum_extension
    )

    sell_breakdown = (
        bearish_trend
        and adx_value >= 22
        and not macd_buy
        and volume_ok
        and consolidation_ok
        and current_close < support - breakout_buffer
        and current_close < previous_close
        and candle_strength >= 0.55
        and support - current_close <= maximum_extension
    )

    return buy_breakout or sell_breakdown