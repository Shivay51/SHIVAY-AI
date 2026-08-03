# ==========================================
# SHIVAY AI PRO v2.5
# Performance Report
# ==========================================

import pandas as pd

FILE = "trade_history.csv"


def get_report():

    try:

        df = pd.read_csv(FILE)

    except Exception:

        return None

    if df.empty:
        return None

    total = len(df)

    wins = len(df[df["Result"] == "WIN"])

    loss = len(df[df["Result"] == "LOSS"])

    open_trade = total - wins - loss

    win_rate = 0.0

    if (wins + loss) > 0:

        win_rate = round(

            (wins / (wins + loss)) * 100,

            2

        )

    total_pnl = round(

        float(df["PnL %"].sum()),

        2

    )

    avg_pnl = round(

        float(df["PnL %"].mean()),

        2

    )

    best_trade = round(

        float(df["PnL %"].max()),

        2

    )

    worst_trade = round(

        float(df["PnL %"].min()),

        2

    )

    return {

        "total": total,

        "wins": wins,

        "loss": loss,

        "open": open_trade,

        "win_rate": win_rate,

        "total_pnl": total_pnl,

        "average_pnl": avg_pnl,

        "best_trade": best_trade,

        "worst_trade": worst_trade,

    }