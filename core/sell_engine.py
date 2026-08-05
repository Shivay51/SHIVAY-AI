# ==========================================

from config import MIN_SCORE
# SHIVAY AI PRO v3
# SELL Engine
# ==========================================


def check_sell(score_data):

    # ==========================================
    # Market Regime
    # ==========================================

    if "BEAR" not in str(score_data.get("regime", "")).upper():
        return False

    # ==========================================
    # Score
    # ==========================================

    if score_data["score"] < max(72, int(MIN_SCORE)):
        return False

    # ==========================================
    # Trend
    # ==========================================

    if score_data["ema20"] >= score_data["ema50"]:
        return False

    if score_data["ema50"] >= score_data["ema200"]:
        return False

    # ==========================================
    # RSI
    # ==========================================

    if score_data["rsi"] > 48:
        return False

    if score_data["rsi"] < 20:
        return False

    # ==========================================
    # ADX
    # ==========================================

    if score_data["adx"] < 20:
        return False

    # ==========================================
    # VWAP
    # ==========================================

    if score_data["price"] > score_data["vwap"]:
        return False

    # ==========================================
    # MACD
    # ==========================================

    if score_data["macd"]:
        return False

    # ==========================================
    # Supertrend
    # ==========================================

    if score_data["supertrend"]:
        return False

    if score_data.get("timeframe_60m") != "BEARISH":
        return False
    if score_data.get("timeframe_30m") != "BEARISH":
        return False
    if score_data.get("timeframe_15m") != "BEARISH":
        return False
    if score_data.get("timeframe_5m") not in {"BEARISH", "SIDEWAYS"}:
        return False
    if not bool(score_data.get("volume_spike", False)):
        return False
    if float(score_data.get("relative_volume", 0) or 0) < 1.0:
        return False

    return True
