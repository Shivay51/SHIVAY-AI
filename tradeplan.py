def create_trade_plan(price, atr_value, decision):

    price = float(price)
    atr_value = max(float(atr_value), 0.01)

    # ==========================
    # STRONG BUY
    # ==========================

    if decision == "🔥 STRONG BUY":

        entry = round(price, 2)

        stop_loss = round(price - (atr_value * 1.25), 2)

        target1 = round(price + (atr_value * 2.00), 2)

        target2 = round(price + (atr_value * 3.25), 2)

        target3 = round(price + (atr_value * 5.00), 2)

    # ==========================
    # BUY
    # ==========================

    elif decision == "✅ BUY":

        entry = round(price, 2)

        stop_loss = round(price - atr_value, 2)

        target1 = round(price + (atr_value * 1.75), 2)

        target2 = round(price + (atr_value * 2.75), 2)

        target3 = round(price + (atr_value * 4.00), 2)

    # ==========================
    # SELL
    # ==========================

    elif "SELL" in decision:

        entry = round(price, 2)

        stop_loss = round(price + (atr_value * 1.25), 2)

        target1 = round(price - (atr_value * 2.00), 2)

        target2 = round(price - (atr_value * 3.25), 2)

        target3 = round(price - (atr_value * 5.00), 2)

    # ==========================
    # WATCH / AVOID
    # ==========================

    else:

        entry = round(price, 2)

        stop_loss = 0.0

        target1 = 0.0

        target2 = 0.0

        target3 = 0.0

    return {

        "entry": entry,

        "sl": stop_loss,

        "target1": target1,

        "target2": target2,

        "target3": target3,

    }