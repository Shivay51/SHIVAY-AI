# SHIVAY AI V10 Final Release Notes

## Added

- Authenticated standard TradingView-alert webhook flow using official placeholders.
- Persistent 5m/15m/30m/60m candle streams with per-contract readiness states.
- Python indicators, Chandelier exits, multi-timeframe F&O/MCX signal decisions, entry validity, predictions, and paper trade monitoring.
- Exact-contract allowlisting, replay protection, rate limiting, atomic storage, admin routing, recovery, cleanup, and performance analytics.
- Compact Telegram BUY/SELL/WAIT/outlook formats with private diagnostics restricted to administrators.

## Safety and acceptance

Live order execution remains permanently disabled. No exact Futures or MCX levels are produced from Yahoo or COMEX substitutes. Unconfigured instruments remain disabled. Real-market connectivity and signal readiness require genuine accepted TradingView candles and are not inferred from local synthetic tests.
