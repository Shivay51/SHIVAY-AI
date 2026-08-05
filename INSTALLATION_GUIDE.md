# Installation Guide

1. Install 64-bit Python 3.11+ and open PowerShell in `C:\SHIVAY_AI`.
2. Run `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`; enter the Telegram token, admin ID, and a strong unique webhook secret locally.
4. Keep `ENABLE_LIVE_ORDER_PLACEMENT=false`, `SIGNALS_ONLY=true`, and `PAPER_MONITORING=true`.
5. Configure exact chart symbols, contracts, exchanges, categories, and timeframes in `tradingview_symbols.json`. Never guess an expiry.
6. Configure the HTTPS reverse proxy with `DNS_SETUP_GUIDE.md`.
7. Create the alerts using `TRADINGVIEW_STANDARD_ALERT_SETUP.md` and `TRADINGVIEW_ALERT_MESSAGES.json`.
8. Start with `cd C:\SHIVAY_AI` then `python bot.py`.
9. Check `/systemhealth` as an administrator and `http://127.0.0.1:8765/health` locally.

The first genuine candles produce NO DATA, WARMING UP, and PARTIAL states. Exact signals remain blocked until the required fresh multi-timeframe history is READY.
