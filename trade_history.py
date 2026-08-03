import csv
import os
from datetime import datetime

FILE_NAME = "trade_history.csv"


def create_file():

    if os.path.exists(FILE_NAME):
        return

    with open(FILE_NAME, "w", newline="") as f:

        writer = csv.writer(f)

        writer.writerow([
            "Date",
            "Symbol",
            "Entry",
            "Exit",
            "Result",
            "PnL %",
            "Reason"
        ])


def save_trade(
    symbol,
    entry,
    exit_price,
    result,
    pnl,
    reason
):

    create_file()

    with open(FILE_NAME, "a", newline="") as f:

        writer = csv.writer(f)

        writer.writerow([

            datetime.now().strftime("%Y-%m-%d %H:%M"),

            symbol,

            entry,

            exit_price,

            result,

            pnl,

            reason

        ])