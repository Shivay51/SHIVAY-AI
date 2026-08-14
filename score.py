"""Symmetric BUY/SELL technical scoring for SHIVAY AI."""

from indicators import adx, atr, ema20, ema50, ema200, macd, session_vwap_from_market, supertrend, volume_spike, vwap
from rsi import calculate_rsi
from support_resistance import support_resistance
from multitimeframe import analyze_timeframes
from chandelier_exit import calculate_timeframe_chandelier, evaluate_chandelier_entry_state


def _higher_timeframe_direction(close, factor):
    values = [close[index] for index in range(factor - 1, len(close), factor)]
    if len(values) < 20:
        return "UNKNOWN"
    fast = sum(values[-10:]) / 10
    slow = sum(values[-20:]) / 20
    slope = values[-1] - values[-3]
    return "BULLISH" if fast > slow and slope > 0 else "BEARISH" if fast < slow and slope < 0 else "SIDEWAYS"


def calculate_score(market):
    close = [float(value) for value in market["close"]]
    high = [float(value) for value in market["high"]]
    low = [float(value) for value in market["low"]]
    volume = [float(value) for value in market["volume"]]
    price = float(market.get("price") or close[-1])
    if len(close) < 50 or price <= 0:
        return None

    e20, e50, e200 = ema20(close), ema50(close), ema200(close)
    rsi = calculate_rsi(close)
    atr_value = atr(high, low, close)
    adx_value = adx(high, low, close)
    # Session-anchored VWAP (resets each trading day) when the payload carries
    # timestamped candles; falls back to the legacy cumulative VWAP otherwise.
    vwap_value = session_vwap_from_market(market) or vwap(high, low, close, volume)
    st_bullish = supertrend(high, low, close)
    macd_bullish = macd(close)
    volume_ok = volume_spike(volume)
    average_volume = sum(volume[-21:-1]) / min(20, len(volume[-21:-1])) if volume[-21:-1] else 0.0
    relative_volume = volume[-1] / average_volume if average_volume > 0 else 0.0
    levels = support_resistance(high[:-1], low[:-1], close[:-1])
    support = float(levels["support"])
    resistance = float(levels["resistance"])
    atr_percent = atr_value / price * 100 if price else 0.0

    bullish = e20 > e50 > e200 and price > e20
    bearish = e20 < e50 < e200 and price < e20
    buy_score = sell_score = 0

    if bullish:
        buy_score += 25
    elif e20 > e50 and price > e200:
        buy_score += 12
    if bearish:
        sell_score += 25
    elif e20 < e50 and price < e200:
        sell_score += 12

    if vwap_value > 0 and price > vwap_value:
        buy_score += 12
    elif vwap_value > 0 and price < vwap_value:
        sell_score += 12
    if 54 <= rsi <= 72:
        buy_score += 12
    elif 28 <= rsi <= 46:
        sell_score += 12
    if st_bullish:
        buy_score += 12
    else:
        sell_score += 12
    if macd_bullish:
        buy_score += 10
    else:
        sell_score += 10
    if adx_value >= 28:
        (buy_score := buy_score + 12) if bullish else None
        (sell_score := sell_score + 12) if bearish else None
    elif adx_value >= 22:
        (buy_score := buy_score + 7) if bullish else None
        (sell_score := sell_score + 7) if bearish else None
    if volume_ok:
        buy_score += 8 if bullish else 0
        sell_score += 8 if bearish else 0
    if 0.10 <= atr_percent <= 3.5:
        buy_score += 5 if bullish else 0
        sell_score += 5 if bearish else 0

    buffer = max(atr_value * 0.10, price * 0.0005)
    buy_breakout = price > resistance + buffer and close[-1] > close[-2]
    sell_breakdown = price < support - buffer and close[-1] < close[-2]
    if buy_breakout and bullish:
        buy_score += 10
    if sell_breakdown and bearish:
        sell_score += 10
    extension = abs(price - e20) / atr_value if atr_value > 0 else 99.0
    timeframes = analyze_timeframes(market)
    chandelier_15m = calculate_timeframe_chandelier(market, 15)
    chandelier_30m = calculate_timeframe_chandelier(market, 30)
    chandelier_60m = calculate_timeframe_chandelier(market, 60)
    chandelier_entry = evaluate_chandelier_entry_state(market, 15)
    chandelier_buy_distance = ((price - float(chandelier_15m.get("long_stop") or 0)) / atr_value) if atr_value > 0 and chandelier_15m.get("long_stop") else 99.0
    chandelier_sell_distance = ((float(chandelier_15m.get("short_stop") or 0) - price) / atr_value) if atr_value > 0 and chandelier_15m.get("short_stop") else 99.0
    direction_30m = timeframes["30m"]
    direction_60m = timeframes["60m"]
    if direction_30m == "BULLISH": buy_score += 6
    elif direction_30m == "BEARISH": sell_score += 6
    if direction_60m == "BULLISH": buy_score += 6
    elif direction_60m == "BEARISH": sell_score += 6
    if extension > 2.0:
        buy_score -= 12
        sell_score -= 12
    if rsi >= 78:
        buy_score -= 12
    if rsi <= 22:
        sell_score -= 12

    if bullish and adx_value >= 28:
        regime = "🟢 STRONG BULL"
    elif bullish:
        regime = "🟢 BULL"
    elif bearish and adx_value >= 28:
        regime = "🔴 STRONG BEAR"
    elif bearish:
        regime = "🔴 BEAR"
    else:
        regime = "🟡 SIDEWAYS"

    side = str(chandelier_entry.get("side") or "NO TRADE") if chandelier_entry.get("confirmed") else "NO TRADE"
    directional_score = buy_score if side == "BUY" else sell_score if side == "SELL" else max(buy_score, sell_score)
    opposite_score = sell_score if side == "BUY" else buy_score if side == "SELL" else 0
    if side in {"BUY", "SELL"} and opposite_score > directional_score:
        directional_score -= min(20, int((opposite_score - directional_score) / 2) + 5)
    score = max(0, min(100, int(round(directional_score))))
    if side == "NO TRADE" or adx_value < 18:
        score = min(score, 69)

    return {
        "price": round(price, 2), "regime": regime, "score": score, "signal_side": side,
        "buy_score": max(0, min(100, int(buy_score))),
        "sell_score": max(0, min(100, int(sell_score))),
        "ema20": round(e20, 2), "ema50": round(e50, 2), "ema200": round(e200, 2),
        "rsi": round(rsi, 2), "atr": round(atr_value, 2), "adx": round(adx_value, 2),
        "supertrend": st_bullish, "macd": macd_bullish, "vwap": round(vwap_value, 2),
        "volume_spike": volume_ok, "support": round(support, 2), "resistance": round(resistance, 2),
        "relative_volume": round(relative_volume, 2),
        "timeframe_5m": timeframes["5m"], "timeframe_15m": timeframes["15m"],
        "timeframe_30m": direction_30m, "timeframe_60m": direction_60m,
        "timeframe_aligned": timeframes["aligned"], "entry_timing_confirmed": timeframes["entry_timing"],
        "chandelier_15m": chandelier_15m, "chandelier_30m": chandelier_30m,
        "chandelier_60m": chandelier_60m,
        "chandelier_entry_state": chandelier_entry,
        "chandelier_primary_side": chandelier_entry.get("side"),
        "chandelier_buy_confirmed": bool(chandelier_entry.get("confirmed") and chandelier_entry.get("side") == "BUY" and 0 <= chandelier_buy_distance <= 3.75),
        "chandelier_sell_confirmed": bool(chandelier_entry.get("confirmed") and chandelier_entry.get("side") == "SELL" and 0 <= chandelier_sell_distance <= 3.75),
        "chandelier_distance_atr": round(chandelier_buy_distance if side == "BUY" else chandelier_sell_distance, 2),
    }
