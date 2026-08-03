def analyze_trade(symbol, score):

    if score >= 95:

        decision = "🔥 STRONG BUY"
        risk = "VERY LOW"
        confidence = "98%"

    elif score >= 90:

        decision = "🔥 STRONG BUY"
        risk = "LOW"
        confidence = "95%"

    elif score >= 85:

        decision = "✅ BUY"
        risk = "LOW"
        confidence = "90%"

    elif score >= 80:

        decision = "✅ BUY"
        risk = "MEDIUM"
        confidence = "85%"

    elif score >= 75:

        decision = "👀 WATCH"
        risk = "MEDIUM"
        confidence = "78%"

    elif score >= 65:

        decision = "👀 WATCH"
        risk = "HIGH"
        confidence = "68%"

    else:

        decision = "❌ AVOID"
        risk = "VERY HIGH"
        confidence = "50%"

    return {

        "symbol": symbol,

        "decision": decision,

        "risk": risk,

        "confidence": confidence,

    }