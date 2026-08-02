def create_trade_plan(price, atr_value, decision):

    if "BUY" in decision:

        entry = round(price, 2)

        stop_loss = round(price - (atr_value * 1.2), 2)

        target1 = round(price + (atr_value * 1.5), 2)

        target2 = round(price + (atr_value * 2.5), 2)

        target3 = round(price + (atr_value * 3.5), 2)

    else:

        entry = round(price, 2)

        stop_loss = round(price + (atr_value * 1.2), 2)

        target1 = round(price - (atr_value * 1.5), 2)

        target2 = round(price - (atr_value * 2.5), 2)

        target3 = round(price - (atr_value * 3.5), 2)

    return {

        "entry": entry,

        "sl": stop_loss,

        "target1": target1,

        "target2": target2,

        "target3": target3,

    }