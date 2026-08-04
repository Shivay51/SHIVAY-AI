# ==========================================
# SHIVAY AI PRO v3
# Risk Engine
# ==========================================


def calculate_risk(price, atr_value, side):

    price = float(price)
    atr_value = max(float(atr_value), 0.01)

    if side == "BUY":

        sl = round(price - (atr_value * 1.25), 2)

        t1 = round(price + (atr_value * 2.00), 2)

        t2 = round(price + (atr_value * 3.25), 2)

        t3 = round(price + (atr_value * 5.00), 2)

    elif side == "SELL":

        sl = round(price + (atr_value * 1.25), 2)

        t1 = round(price - (atr_value * 2.00), 2)

        t2 = round(price - (atr_value * 3.25), 2)

        t3 = round(price - (atr_value * 5.00), 2)

    else:

        return None

    return {

        "entry": round(price, 2),

        "sl": sl,

        "target1": t1,

        "target2": t2,

        "target3": t3,

    }