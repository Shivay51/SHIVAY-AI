import logging

from scanner import scan_market
from signal_memory import can_send, purge_expired

LOGGER = logging.getLogger("shivay.autoscan")


# ==========================================
# AUTO SCAN
# ==========================================

def auto_scan():

    try:

        purge_expired()

        signals = scan_market()

        if not signals:
            return []

        fresh_signals = []

        for trade in signals:

            symbol = trade.get("symbol")

            if not symbol:
                continue

            # પહેલેથી મોકલાયેલ / cooldown માં હોય તે Signal Skip.
            # Delivery પછી જ signal memory માં નોંધાય છે, અહીં નહીં.
            allowed, reason = can_send(symbol, trade.get("side"))
            if not allowed:
                LOGGER.info("Auto scan skipped %s: %s", symbol, reason)
                continue

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

        LOGGER.warning("Auto scan recovered from %s", type(e).__name__)

        return []
