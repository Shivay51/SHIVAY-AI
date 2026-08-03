# ==========================================
# SHIVAY AI PRO v2.5
# Trade History
# ==========================================

import csv
import os
from datetime import datetime

FILE_NAME = "trade_history.csv"

HEADER = [

    "Date",

    "Symbol",

    "Entry",

    "Exit",

    "Result",

    "PnL %",

    "Reason",

]


# ==========================================
# CREATE FILE
# ==========================================

def create_file():

    if os.path.exists(FILE_NAME):
        return

    with open(FILE_NAME, "w", newline="", encoding="utf-8") as file:

        writer = csv.writer(file)

        writer.writerow(HEADER)


# ==========================================
# SAVE TRADE
# ==========================================

def save_trade(

    symbol,

    entry,

    exit_price,

    result,

    pnl,

    reason,

):

    create_file()

    with open(FILE_NAME, "a", newline="", encoding="utf-8") as file:

        writer = csv.writer(file)

        writer.writerow([

            datetime.now().strftime("%Y-%m-%d %H:%M"),

            symbol,

            round(float(entry), 2),

            round(float(exit_price), 2),

            result,

            round(float(pnl), 2),

            reason,

        ])


# ==========================================
# TOTAL TRADES
# ==========================================

def total_trades():

    create_file()

    with open(FILE_NAME, "r", encoding="utf-8") as file:

        return max(sum(1 for _ in file) - 1, 0)


# ==========================================
# RESET HISTORY
# ==========================================

def clear_history():

    if os.path.exists(FILE_NAME):

        os.remove(FILE_NAME)

    create_file()