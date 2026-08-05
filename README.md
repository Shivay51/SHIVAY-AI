# SHIVAY AI V10

SHIVAY AI is a signals-only Indian market-intelligence bot for Telegram. It accepts authenticated, completed-candle alerts from standard TradingView alerts, validates and stores multi-timeframe data, calculates indicators and Chandelier exits in Python, ranks F&O/MCX setups, and paper-monitors their lifecycle.

## Safety

The application never places, modifies, or cancels orders. Production startup fails closed unless live ordering is disabled, signals-only mode is enabled, and paper monitoring is enabled.

## Quick start

1. Install Python 3.11 or newer.
2. Run `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and supply local secrets without committing it.
4. Configure exact, user-verified contracts in `tradingview_symbols.json`.
5. Follow `TRADINGVIEW_STANDARD_ALERT_SETUP.md`.
6. Run `python bot.py`.

Local health is available at `http://127.0.0.1:8765/health`. Public availability depends on the DNS/TLS/reverse-proxy steps in `DNS_SETUP_GUIDE.md`.

## Status meanings

- **CODE READY**: local implementation and tests pass.
- **INTEGRATION READY**: receiver and templates are ready.
- **CONNECTED**: a genuine recent TradingView alert was accepted.
- **DATA WARMING**: genuine candles are accumulating.
- **SIGNAL READY**: required histories and timeframes are ready.
- **PRODUCTION TESTING**: real-market observation is underway.

See `PROJECT_STATUS.md`, `SECURITY_CHECKLIST.md`, and `TROUBLESHOOTING.md` before operation.
