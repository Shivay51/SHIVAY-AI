import pandas as pd
import math
from typing import Any, Mapping, Sequence


def _ema(series, length):
    return pd.Series(series, dtype="float64").ewm(span=length, adjust=False, min_periods=length).mean()


def ema20(close):
    close = pd.Series(close, dtype="float64").dropna()

    if close.empty:
        return 0.0

    value = _ema(close, 20)

    if value is None or value.dropna().empty:
        return float(close.iloc[-1])

    return float(value.ffill().iloc[-1])


def ema50(close):
    close = pd.Series(close, dtype="float64").dropna()

    if close.empty:
        return 0.0

    value = _ema(close, 50)

    if value is None or value.dropna().empty:
        return float(close.iloc[-1])

    return float(value.ffill().iloc[-1])


def ema200(close):
    close = pd.Series(close, dtype="float64").dropna()

    if close.empty:
        return 0.0

    value = _ema(close, 200)

    if value is None or value.dropna().empty:
        return float(close.iloc[-1])

    return float(value.ffill().iloc[-1])


def atr_series(high, low, close, period=14):
    """Return Wilder ATR values; invalid rows remain NaN for safe alignment."""
    try:
        period = max(2, int(period))
    except (TypeError, ValueError, OverflowError):
        period = 14
    data = pd.DataFrame(
        {
            "high": pd.Series(high, dtype="float64"),
            "low": pd.Series(low, dtype="float64"),
            "close": pd.Series(close, dtype="float64"),
        }
    ).dropna()

    if len(data) < 2:
        return pd.Series(dtype="float64")

    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - data["close"].shift(1)).abs(),
            (data["low"] - data["close"].shift(1)).abs(),
        ], axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def atr(high, low, close, period=14):
    """Return the latest finite Wilder ATR value (backward compatible)."""
    values = atr_series(high, low, close, period)
    if values.empty:
        return 0.0
    value = values.dropna()
    if value.dropna().empty:
        data = pd.DataFrame({"high": high, "low": low, "close": close}, dtype="float64").dropna()
        if len(data) < 2:
            return 0.0
        true_range = pd.concat(
            [data["high"] - data["low"], (data["high"] - data["close"].shift()).abs(),
             (data["low"] - data["close"].shift()).abs()], axis=1
        ).max(axis=1)
        result = float(true_range.tail(min(max(2, int(period)), len(true_range))).mean())
        return result if pd.notna(result) and result > 0 else 0.0

    result = float(value.iloc[-1])
    return result if pd.notna(result) and result > 0 else 0.0


def supertrend(high, low, close):
    data = pd.DataFrame(
        {
            "high": pd.Series(high, dtype="float64"),
            "low": pd.Series(low, dtype="float64"),
            "close": pd.Series(close, dtype="float64"),
        }
    ).dropna()

    if len(data) < 11:
        return False

    true_range = pd.concat([
        data["high"] - data["low"],
        (data["high"] - data["close"].shift()).abs(),
        (data["low"] - data["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr_values = true_range.ewm(alpha=0.1, adjust=False, min_periods=10).mean()
    middle = (data["high"] + data["low"]) / 2
    upper = middle + 3 * atr_values
    lower = middle - 3 * atr_values
    if atr_values.dropna().empty:
        return False
    direction = 1
    final_upper = float(upper.dropna().iloc[0])
    final_lower = float(lower.dropna().iloc[0])
    start = int(atr_values.first_valid_index() or 0) + 1
    for index in range(start, len(data)):
        previous_close = float(data["close"].iloc[index - 1])
        final_upper = min(float(upper.iloc[index]), final_upper) if previous_close <= final_upper else float(upper.iloc[index])
        final_lower = max(float(lower.iloc[index]), final_lower) if previous_close >= final_lower else float(lower.iloc[index])
        close_value = float(data["close"].iloc[index])
        if direction < 0 and close_value > final_upper:
            direction = 1
        elif direction > 0 and close_value < final_lower:
            direction = -1
    return direction == 1


def adx(high, low, close):
    data = pd.DataFrame(
        {
            "high": pd.Series(high, dtype="float64"),
            "low": pd.Series(low, dtype="float64"),
            "close": pd.Series(close, dtype="float64"),
        }
    ).dropna()

    if len(data) < 15:
        return 0.0

    up = data["high"].diff()
    down = -data["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    true_range = pd.concat([
        data["high"] - data["low"],
        (data["high"] - data["close"].shift()).abs(),
        (data["low"] - data["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr_values = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_values.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_values.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    result = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().dropna()

    if result.empty:
        return 0.0

    return float(result.iloc[-1])


def macd(close):
    close = pd.Series(close, dtype="float64").dropna()

    if len(close) < 35:
        return False

    macd_line = close.ewm(span=12, adjust=False, min_periods=26).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
    signal_line = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
    histogram = macd_line - signal_line
    result = pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram}).dropna()
    if len(result) < 2:
        return False
    current_macd = float(result["macd"].iloc[-1])
    current_signal = float(result["signal"].iloc[-1])
    current_histogram = float(result["histogram"].iloc[-1])
    previous_histogram = float(result["histogram"].iloc[-2])

    return (
        current_macd > current_signal
        and current_histogram > 0
        and current_histogram >= previous_histogram
    )


def vwap(high, low, close, volume):
    data = pd.DataFrame(
        {
            "high": pd.Series(high, dtype="float64"),
            "low": pd.Series(low, dtype="float64"),
            "close": pd.Series(close, dtype="float64"),
            "volume": pd.Series(volume, dtype="float64"),
        }
    ).dropna()

    data = data[data["volume"] >= 0]

    if data.empty:
        return 0.0

    cumulative_volume = data["volume"].cumsum()

    if float(cumulative_volume.iloc[-1]) <= 0:
        return float(data["close"].iloc[-1])

    typical_price = (
        data["high"] + data["low"] + data["close"]
    ) / 3.0

    value = (
        typical_price * data["volume"]
    ).cumsum() / cumulative_volume.replace(0, pd.NA)

    value = value.ffill().dropna()

    if value.empty:
        return float(data["close"].iloc[-1])

    return float(value.iloc[-1])


def volume_spike(volume):
    volume = pd.Series(volume, dtype="float64").dropna()

    if len(volume) < 21:
        return False

    latest_volume = float(volume.iloc[-1])
    average_volume = float(volume.iloc[-21:-1].mean())
    median_volume = float(volume.iloc[-21:-1].median())

    if latest_volume <= 0 or average_volume <= 0:
        return False

    relative_volume = latest_volume / average_volume
    median_relative_volume = (
        latest_volume / median_volume
        if median_volume > 0
        else relative_volume
    )

    return relative_volume >= 1.5 and median_relative_volume >= 1.25


def calculate_indicator_snapshot(candles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate the complete bridge snapshot from accepted completed candles.

    No unavailable value is fabricated. ``ready`` becomes true only after the
    EMA200 warm-up requirement is met.
    """
    frame = pd.DataFrame(list(candles))
    required = ["open", "high", "low", "close", "volume"]
    if frame.empty or any(name not in frame for name in required):
        return {"ready": False, "state": "NO DATA", "usable_candles": 0}
    frame = frame[required].apply(pd.to_numeric, errors="coerce").replace([math.inf, -math.inf], pd.NA).dropna()
    frame = frame[(frame["open"] > 0) & (frame["high"] > 0) & (frame["low"] > 0) & (frame["close"] > 0) & (frame["volume"] >= 0)]
    if frame.empty:
        return {"ready": False, "state": "NO DATA", "usable_candles": 0}
    high, low, close, volume = frame["high"], frame["low"], frame["close"], frame["volume"]
    count = len(frame)
    result: dict[str, Any] = {"ready": count >= 200, "state": "READY" if count >= 200 else "WARMING UP", "usable_candles": count}

    ema_values = {period: _ema(close, period) for period in (20, 50, 200)}
    for period, values in ema_values.items():
        result[f"ema{period}"] = float(values.dropna().iloc[-1]) if not values.dropna().empty else 0.0

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi_values = 100 - 100 / (1 + rs)
    rsi_values = rsi_values.mask((loss == 0) & (gain > 0), 100.0).mask((gain == 0) & (loss > 0), 0.0).mask((gain == 0) & (loss == 0), 50.0).dropna()
    result["rsi"] = float(rsi_values.iloc[-1]) if not rsi_values.empty else 0.0

    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr_values = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    plus_di_values = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_values.replace(0, pd.NA)
    minus_di_values = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_values.replace(0, pd.NA)
    dx = 100 * (plus_di_values - minus_di_values).abs() / (plus_di_values + minus_di_values).replace(0, pd.NA)
    adx_values = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    result.update(
        atr=float(atr_values.dropna().iloc[-1]) if not atr_values.dropna().empty else 0.0,
        plus_di=float(plus_di_values.dropna().iloc[-1]) if not plus_di_values.dropna().empty else 0.0,
        minus_di=float(minus_di_values.dropna().iloc[-1]) if not minus_di_values.dropna().empty else 0.0,
        adx=float(adx_values.dropna().iloc[-1]) if not adx_values.dropna().empty else 0.0,
    )

    macd_line = close.ewm(span=12, adjust=False, min_periods=26).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
    signal_line = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
    histogram = macd_line - signal_line
    result.update(
        macd=float(macd_line.dropna().iloc[-1]) if not macd_line.dropna().empty else 0.0,
        macd_signal=float(signal_line.dropna().iloc[-1]) if not signal_line.dropna().empty else 0.0,
        macd_histogram=float(histogram.dropna().iloc[-1]) if not histogram.dropna().empty else 0.0,
        vwap=vwap(high, low, close, volume),
    )
    average_volume = volume.tail(20).mean() if count >= 20 else 0.0
    result["relative_volume"] = float(volume.iloc[-1] / average_volume) if average_volume > 0 else 0.0
    result["volume_confirmed"] = result["relative_volume"] >= 0.75
    result["supertrend_direction"] = "BULLISH" if supertrend(high, low, close) else "BEARISH"

    lookback = min(20, max(2, count - 1))
    prior_high = high.iloc[-lookback - 1:-1] if count > lookback else high.iloc[:-1]
    prior_low = low.iloc[-lookback - 1:-1] if count > lookback else low.iloc[:-1]
    swing_high = float(prior_high.max()) if not prior_high.empty else 0.0
    swing_low = float(prior_low.min()) if not prior_low.empty else 0.0
    result.update(swing_high=swing_high, swing_low=swing_low, breakout_level=swing_high, breakdown_level=swing_low,
                  signal_candle_high=float(high.iloc[-1]), signal_candle_low=float(low.iloc[-1]))
    previous_high = float(high.iloc[-2]) if count > 1 else float(high.iloc[-1])
    previous_low = float(low.iloc[-2]) if count > 1 else float(low.iloc[-1])
    result.update(higher_high=float(high.iloc[-1]) > previous_high, higher_low=float(low.iloc[-1]) > previous_low,
                  lower_high=float(high.iloc[-1]) < previous_high, lower_low=float(low.iloc[-1]) < previous_low)
    latest_close, latest_open = float(close.iloc[-1]), float(frame["open"].iloc[-1])
    buffer = result["atr"] * 0.05
    result.update(
        bullish_breakout=swing_high > 0 and latest_close > swing_high + buffer,
        bearish_breakdown=swing_low > 0 and latest_close < swing_low - buffer,
        bullish_retest=swing_high > 0 and float(low.iloc[-1]) <= swing_high + buffer and latest_close > swing_high and latest_close > latest_open,
        bearish_retest=swing_low > 0 and float(high.iloc[-1]) >= swing_low - buffer and latest_close < swing_low and latest_close < latest_open,
        bullish_pullback=result["ema20"] > 0 and float(low.iloc[-1]) <= result["ema20"] + buffer and latest_close > result["ema20"] and latest_close > latest_open and result["ema20"] > result["ema50"],
        bearish_pullback=result["ema20"] > 0 and float(high.iloc[-1]) >= result["ema20"] - buffer and latest_close < result["ema20"] and latest_close < latest_open and result["ema20"] < result["ema50"],
    )
    bullish = result["ema20"] > result["ema50"] > result["ema200"] > 0
    bearish = 0 < result["ema20"] < result["ema50"] < result["ema200"]
    result["trend_state"] = "BULLISH" if bullish else "BEARISH" if bearish else "SIDEWAYS"
    result["momentum_state"] = "BULLISH" if result["macd_histogram"] > 0 and result["plus_di"] > result["minus_di"] else "BEARISH" if result["macd_histogram"] < 0 and result["minus_di"] > result["plus_di"] else "NEUTRAL"
    result["structure_state"] = "BULLISH" if result["higher_high"] and result["higher_low"] else "BEARISH" if result["lower_high"] and result["lower_low"] else "MIXED"
    return result
