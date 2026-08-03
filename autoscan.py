from scanner import scan_market
from signal_memory import (
    signal_exists,
    add_signal,
)


# ==========================================
# AUTO SCAN
# ==========================================

def auto_scan():

    try:

        signals = scan_market()

        if not signals:
            return []

        fresh_signals = []

        for trade in signals:

            symbol = trade.get("symbol")

            if not symbol:
                continue

            # પહેલેથી મોકલાયેલ Signal Skip
            if signal_exists(symbol):
                continue

            # Save Signal
            add_signal(symbol)

            fresh_signals.append(trade)

        # Highest Score First
        fresh_signals.sort(
            key=lambda x: (
                x["score"],
                float(str(x["confidence"]).replace("%", "")),
            ),
            reverse=True,
        )

        return fresh_signals

    except Exception as e:

        print(f"❌ Auto Scan Error : {e}")

        return []