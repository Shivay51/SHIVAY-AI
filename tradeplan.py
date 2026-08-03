# ==========================================
# SHIVAY AI PRO v2.1
# Trade Plan Generator
# ==========================================

def create_trade_plan(price, atr_value, decision):

    price = float(price)
    atr_value = max(float(atr_value), 0.01)

    # ==========================================
    # STRONG BUY
    # ==========================================

    if decision == "🔥 STRONG BUY":

        entry = round(price, 2)

        stop_loss = round(price - (atr_value * 1.25), 2)

        target1 = round(price + (atr_value * 2.00), 2)

        target2 = round(price + (atr_value * 3.25), 2)

        target3 = round(price + (atr_value * 5.00), 2)

    # ==========================================
    # BUY
    # ==========================================

    elif decision == "✅ BUY":

        entry = round(price, 2)

        stop_loss = round(price - atr_value, 2)

        target1 = round(price + (atr_value * 1.75), 2)

        target2 = round(price + (atr_value * 2.75), 2)

        target3 = round(price + (atr_value * 4.00), 2)

    # ==========================================
    # SELL
    # ==========================================

    elif "SELL" in decision:

        entry = round(price, 2)

        stop_loss = round(price + (atr_value * 1.25), 2)

        target1 = round(price - (atr_value * 2.00), 2)

        target2 = round(price - (atr_value * 3.25), 2)

        target3 = round(price - (atr_value * 5.00), 2)

    # ==========================================
    # WATCH
    # ==========================================

    else:

        entry = round(price, 2)

        stop_loss = 0.0

        target1 = 0.0

        target2 = 0.0

        target3 = 0.0

    # ==========================================
    # RISK / REWARD
    # ==========================================

    risk = abs(entry - stop_loss)

    reward = abs(target1 - entry)

    rr_ratio = round(reward / risk, 2) if risk else 0

    risk_percent = round((risk / entry) * 100, 2) if entry else 0

    reward_percent = round((reward / entry) * 100, 2) if entry else 0

    return {

        "entry": entry,

        "sl": stop_loss,

        "target1": target1,

        "target2": target2,

        "target3": target3,

        "risk": round(risk, 2),

        "reward": round(reward, 2),

        "rr_ratio": rr_ratio,

        "risk_percent": risk_percent,

        "reward_percent": reward_percent,

        # Future Use
        "trail_sl": stop_loss,

        "status": "OPEN",

        "t1_hit": False,

        "t2_hit": False,

        "t3_hit": False,

    }