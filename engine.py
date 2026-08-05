"""Balanced directional signal engine for SHIVAY AI."""

import logging

from breakout import breakout_filter
from config import MIN_SCORE
from market_cache import get_market_direction, get_market_strength
from pullback import pullback_filter
from score import calculate_score
from market_brain import assess_signal_context


LOGGER = logging.getLogger("shivay.engine")


def run_engine(market):
    try:
        if not market:
            return None
        price = float(market.get("price", 0))
        close = [float(value) for value in market.get("close", [])]
        high = [float(value) for value in market.get("high", [])]
        low = [float(value) for value in market.get("low", [])]
        volume = [float(value) for value in market.get("volume", [])]
        if price < 200 or min(map(len, (close, high, low, volume))) < 200:
            return None

        score_data = calculate_score(market)
        if not score_data:
            return None
        side = str(score_data.get("signal_side", "NO TRADE"))
        if side not in ("BUY", "SELL"):
            return None
        symbol = str(market.get("symbol", market.get("requested_symbol", market.get("trading_symbol", "UNKNOWN"))))
        context = assess_signal_context(market, symbol, side)
        if not context["valid"]:
            return None
        e20, e50, e200 = (float(score_data[name]) for name in ("ema20", "ema50", "ema200"))
        rsi = float(score_data.get("rsi", 50))
        atr_value = float(score_data.get("atr", 0))
        adx_value = float(score_data.get("adx", 0))
        vwap_value = float(score_data.get("vwap", 0))
        macd_bullish = bool(score_data.get("macd"))
        st_bullish = bool(score_data.get("supertrend"))
        volume_ok = bool(score_data.get("volume_spike"))
        if atr_value <= 0 or adx_value < 22 or vwap_value <= 0 or not volume_ok:
            return None

        if side == "BUY":
            technical = e20 > e50 > e200 and price > e20 and price > vwap_value and 52 <= rsi <= 75 and macd_bullish and st_bullish
        else:
            technical = e20 < e50 < e200 and price < e20 and price < vwap_value and 25 <= rsi <= 48 and not macd_bullish and not st_bullish
        confirmation_5 = str(score_data.get("timeframe_5m", "UNKNOWN"))
        confirmation_15 = str(score_data.get("timeframe_15m", "UNKNOWN"))
        confirmation_30 = str(score_data.get("timeframe_30m", "UNKNOWN"))
        confirmation_60 = str(score_data.get("timeframe_60m", "UNKNOWN"))
        expected = "BULLISH" if side == "BUY" else "BEARISH"
        technical = (
            technical
            and confirmation_60 == expected
            and confirmation_30 == expected
            and confirmation_15 == expected
            and confirmation_5 in {expected, "SIDEWAYS"}
        )
        if not technical:
            return None

        market_direction = str(get_market_direction()).upper()
        market_strength = float(get_market_strength())
        if market_strength >= 65 and ((side == "BUY" and "BEARISH" in market_direction) or (side == "SELL" and "BULLISH" in market_direction)):
            return None

        breakout_ok = breakout_filter(high, low, close, volume)
        pullback_ok = pullback_filter(high, low, close)
        prior_high, prior_low = max(high[-21:-1]), min(low[-21:-1])
        buffer = max(atr_value * 0.10, price * 0.0005)
        if side == "BUY":
            directional_breakout = breakout_ok and close[-1] > prior_high + buffer and close[-1] > close[-2]
            directional_pullback = pullback_ok and abs(close[-1] - e20) <= atr_value and close[-1] > close[-2]
        else:
            directional_breakout = breakout_ok and close[-1] < prior_low - buffer and close[-1] < close[-2]
            directional_pullback = pullback_ok and abs(close[-1] - e20) <= atr_value and close[-1] < close[-2]
        if not (directional_breakout or directional_pullback):
            return None

        required = max(72, int(MIN_SCORE))
        if "SIDEWAYS" in market_direction:
            required += 4
        if adx_value < 26:
            required += 3
        if int(score_data.get("score", 0)) < required:
            return None

        score_data.update({
            "setup": "🚀 Confirmed Breakout" if directional_breakout else "🔄 Confirmed Pullback",
            "market": market_direction,
            "market_strength": market_strength,
            "required_score": required,
            "engine_confidence": f"{min(99, int(score_data['score']) + (3 if adx_value >= 30 else 0))}%",
            "engine": "SHIVAY AI PRO v3",
            "market_brain": context,
            "signal_context_score": context["signal_context_score"],
            "relative_strength": context["relative_strength"],
            "sector_momentum": context["sector_momentum"],
        })
        return score_data
    except Exception as error:
        LOGGER.warning("Signal engine recovered from %s", type(error).__name__)
        return None
