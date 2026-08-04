# ==========================================
# SHIVAY AI PRO v3
# Strategy Engine
# ==========================================

from core.decision_engine import get_decision


def analyze_trade(symbol, score_data):

    side = get_decision(score_data)

    score = score_data["score"]

    # ==========================================
    # BUY
    # ==========================================

    if side == "BUY":

        if score >= 95:

            decision = "🔥 STRONG BUY"
            risk = "VERY LOW"
            confidence = "98%"

        elif score >= 85:

            decision = "✅ BUY"
            risk = "LOW"
            confidence = "92%"

        else:

            decision = "👀 WATCH"
            risk = "MEDIUM"
            confidence = "75%"

    # ==========================================
    # SELL
    # ==========================================

    elif side == "SELL":

        if score >= 95:

            decision = "🔥 STRONG SELL"
            risk = "VERY LOW"
            confidence = "98%"

        elif score >= 85:

            decision = "🔻 SELL"
            risk = "LOW"
            confidence = "92%"

        else:

            decision = "👀 WATCH"
            risk = "MEDIUM"
            confidence = "75%"

    # ==========================================
    # NO TRADE
    # ==========================================

    else:

        decision = "👀 WATCH"
        risk = "HIGH"
        confidence = "60%"

    return {

        "symbol": symbol,

        "decision": decision,

        "risk": risk,

        "confidence": confidence,

    }