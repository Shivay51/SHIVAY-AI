def analyze_trade(symbol, score):

    if score >= 90:
        decision = "🔥 STRONG BUY"
        risk = "LOW"
        confidence = "95%"

    elif score >= 80:
        decision = "✅ BUY"
        risk = "MEDIUM"
        confidence = "85%"

    elif score >= 70:
        decision = "👀 WATCH"
        risk = "MEDIUM"
        confidence = "70%"

    else:
        decision = "❌ AVOID"
        risk = "HIGH"
        confidence = "40%"

    return {
        "symbol": symbol,
        "decision": decision,
        "risk": risk,
        "confidence": confidence
    }