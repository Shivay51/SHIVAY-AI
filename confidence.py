# ==========================================
# SHIVAY AI PRO v3.0
# AI Confidence Engine
# ==========================================

def calculate_confidence(data):

    confidence = 0

    # EMA Alignment
    if (
        data["ema20"] >
        data["ema50"] >
        data["ema200"]
    ):
        confidence += 20

    # RSI
    if 55 <= data["rsi"] <= 68:
        confidence += 10

    # ADX
    if data["adx"] >= 30:
        confidence += 15

    elif data["adx"] >= 25:
        confidence += 10

    # Supertrend
    if data["supertrend"]:
        confidence += 10

    # MACD
    if data["macd"]:
        confidence += 10

    # VWAP
    if data["price"] > data["vwap"]:
        confidence += 10

    # Volume

    if data["volume_spike"]:
        confidence += 10

    # Market

    if data["market_strength"] >= 80:
        confidence += 10

    elif data["market_strength"] >= 70:
        confidence += 5

    # Score

    if data["score"] >= 90:
        confidence += 5

    return min(confidence, 100)


# ==========================================
# STAR RATING
# ==========================================

def confidence_star(value):

    if value >= 95:

        return "★★★★★"

    elif value >= 90:

        return "★★★★☆"

    elif value >= 80:

        return "★★★☆☆"

    elif value >= 70:

        return "★★☆☆☆"

    return "★☆☆☆☆"


# ==========================================
# AI Decision
# ==========================================

def confidence_level(value):

    if value >= 95:

        return "ULTRA HIGH"

    elif value >= 90:

        return "VERY HIGH"

    elif value >= 80:

        return "HIGH"

    elif value >= 70:

        return "MEDIUM"

    return "LOW"