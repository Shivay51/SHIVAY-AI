# MCX Continuous Advisory Mode

The optional `mcx.py` scheduler module checks MCX Gold and Silver continuous charts at the normal scheduler cadence. It sends an alert only when the 15m, 30m, and 1h strategy confirms a quality setup, score is at least 75, volume is confirmed, risk-reward to Target 2 is at least 2.0, and the same direction is outside the 25-minute cooldown.

Every alert is labelled `CONTINUOUS CONTRACT ADVISORY`. It is not an order instruction and the active MCX expiry/price must be verified before acting.
