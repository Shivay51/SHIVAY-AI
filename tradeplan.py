# ==========================================
# SHIVAY AI PRO v3
# Trade Plan Generator
# ==========================================

from core.risk_engine import calculate_risk


def create_trade_plan(price, atr_value, decision):

    if "BUY" in decision:

        return calculate_risk(
            price,
            atr_value,
            "BUY",
        )

    elif "SELL" in decision:

        return calculate_risk(
            price,
            atr_value,
            "SELL",
        )

    return {

        "entry": round(float(price), 2),

        "sl": 0.0,

        "target1": 0.0,

        "target2": 0.0,

        "target3": 0.0,

    }