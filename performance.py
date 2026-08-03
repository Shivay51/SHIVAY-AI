import pandas as pd

FILE = "trade_history.csv"


def get_report():

    try:

        df = pd.read_csv(FILE)

    except Exception:

        return None

    total = len(df)

    wins = len(df[df["Result"] == "WIN"])

    loss = len(df[df["Result"] == "LOSS"])

    if total == 0:

        return None

    win_rate = round((wins / total) * 100, 2)

    total_pnl = round(df["PnL %"].sum(), 2)

    return {

        "total": total,

        "wins": wins,

        "loss": loss,

        "win_rate": win_rate,

        "pnl": total_pnl

    }