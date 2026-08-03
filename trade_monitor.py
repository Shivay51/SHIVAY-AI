# ==========================================
# SHIVAY AI PRO v2.2
# Professional Trade Monitor
# ==========================================

from data import get_live_price

_active_trades = {}


# ==========================================
# SAVE TRADE
# ==========================================

def add_trade(trade):

    symbol = trade["symbol"]

    _active_trades[symbol] = {

        "symbol": symbol,

        "entry": trade["entry"],

        "sl": trade["sl"],

        "target1": trade["target1"],

        "target2": trade["target2"],

        "target3": trade["target3"],

        "t1_hit": False,

        "t2_hit": False,

        "t3_hit": False,

        "closed": False,

    }


# ==========================================
# REMOVE TRADE
# ==========================================

def remove_trade(symbol):

    if symbol in _active_trades:

        del _active_trades[symbol]


# ==========================================
# ACTIVE TRADES
# ==========================================

def get_all_trades():

    return _active_trades


# ==========================================
# CHECK TRADES
# ==========================================

def check_trades():

    alerts = []

    for symbol, trade in list(_active_trades.items()):

        if trade["closed"]:
            continue

        price = get_live_price(symbol)

        if price is None:
            continue

        # ==================================
        # TARGET 1
        # ==================================

        if (not trade["t1_hit"]) and price >= trade["target1"]:

            trade["t1_hit"] = True

            # Break-even
            trade["sl"] = trade["entry"]

            alerts.append({

                "type": "TARGET1",

                "symbol": symbol,

                "price": price,

                "new_sl": trade["sl"]

            })

        # ==================================
        # TARGET 2
        # ==================================

        if trade["t1_hit"] and (not trade["t2_hit"]) and price >= trade["target2"]:

            trade["t2_hit"] = True

            # Trail SL to Target1
            trade["sl"] = trade["target1"]

            alerts.append({

                "type": "TARGET2",

                "symbol": symbol,

                "price": price,

                "new_sl": trade["sl"]

            })

        # ==================================
        # TARGET 3
        # ==================================

        if trade["t2_hit"] and (not trade["t3_hit"]) and price >= trade["target3"]:

            trade["t3_hit"] = True
            trade["closed"] = True

            alerts.append({

                "type": "TARGET3",

                "symbol": symbol,

                "price": price

            })

            continue

        # ==================================
        # STOP LOSS
        # ==================================

        if price <= trade["sl"]:

            trade["closed"] = True

            alerts.append({

                "type": "STOPLOSS",

                "symbol": symbol,

                "price": price

            })

    return alerts