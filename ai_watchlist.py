# ==========================================
# SHIVAY AI PRO v2.5
# AI Watchlist
# ==========================================

from watchlist import WATCHLIST
from sector_strength import sector_priority


def get_ai_watchlist():

    ranked = []

    for symbol in WATCHLIST:

        ranked.append({

            "symbol": symbol,

            "priority": sector_priority(symbol)

        })

    ranked.sort(

        key=lambda x: x["priority"],

        reverse=True

    )

    watchlist = []

    for item in ranked:

        watchlist.append(item["symbol"])

    return watchlist