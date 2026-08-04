# ==========================================
# SHIVAY AI PRO v3
# SELL Engine
# ==========================================


def check_sell(score_data):

    # ==========================================
    # Market Regime
    # ==========================================

    if score_data["regime"] not in (
        "🔴 STRONG BEAR",
        "🔴 BEAR",
    ):
        return False

    # ==========================================
    # Score
    # ==========================================

    if score_data["score"] < 80:
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

    if score_data["rsi"] > 45:
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

    return True