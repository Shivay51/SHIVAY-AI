# ==========================================
# SHIVAY AI PRO v3
# Decision Engine
# ==========================================

from core.buy_engine import check_buy
from core.sell_engine import check_sell


def get_decision(score_data):

    buy = check_buy(score_data)
    sell = check_sell(score_data)

    if buy and not sell:
        return "BUY"

    if sell and not buy:
        return "SELL"

    return "HOLD"