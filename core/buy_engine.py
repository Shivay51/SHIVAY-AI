# ==========================================

from config import MIN_SCORE
# SHIVAY AI PRO v3
# BUY Engine
# ==========================================


def check_buy(score_data):

    chandelier = score_data.get("chandelier_entry_state", {})
    if not isinstance(chandelier, dict) or not chandelier.get("confirmed") or chandelier.get("side") != "BUY":
        return False

    # ==========================================
    # Market Regime
    # ==========================================

    if "BULL" not in str(score_data.get("regime", "")).upper():
        return False

    # ==========================================
    # Score
    # ==========================================

    if score_data["score"] < max(72, int(MIN_SCORE)):
        return False

    # ==========================================
    # Trend
    # ==========================================

    if score_data["ema20"] <= score_data["ema50"]:
        return False

    if score_data["ema50"] <= score_data["ema200"]:
        return False

    # ==========================================
    # RSI
    # ==========================================

    if score_data["rsi"] < 52:
        return False

    if score_data["rsi"] > 75:
        return False

    # ==========================================
    # ADX
    # ==========================================

    if score_data["adx"] < 20:
        return False

    # ==========================================
    # VWAP
    # ==========================================

    if score_data["price"] < score_data["vwap"]:
        return False

    # ==========================================
    # MACD
    # ==========================================

    if not score_data["macd"]:
        return False

    # ==========================================
    # Supertrend
    # ==========================================

    if not score_data["supertrend"]:
        return False

    if score_data.get("timeframe_60m") != "BULLISH":
        return False
    if score_data.get("timeframe_30m") != "BULLISH":
        return False
    if score_data.get("timeframe_15m") != "BULLISH":
        return False
    if score_data.get("timeframe_5m") not in {"BULLISH", "SIDEWAYS"}:
        return False
    if not bool(score_data.get("volume_spike", False)):
        return False
    if float(score_data.get("relative_volume", 0) or 0) < 1.0:
        return False

    return True
