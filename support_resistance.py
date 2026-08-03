import pandas as pd


def support_resistance(high, low, close):

    high = pd.Series(high)
    low = pd.Series(low)
    close = pd.Series(close)

    resistance = high.tail(20).max()
    support = low.tail(20).min()

    current = close.iloc[-1]

    return {

        "support": float(support),

        "resistance": float(resistance),

        "distance_support": float(current - support),

        "distance_resistance": float(resistance - current),

    }


def trade_allowed(price, atr, support, resistance):

    risk = price - support
    reward = resistance - price

    if risk <= 0:
        return False

    if reward <= 0:
        return False

    rr = reward / risk

    # Intraday માટે Relax Rule
    if rr < 1.2:
        return False

    # Resistance બહુ નજીક ન હોવી જોઈએ
    if reward < atr * 0.75:
        return False

    return True