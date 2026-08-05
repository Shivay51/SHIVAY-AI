# Troubleshooting

## Telegram does not connect

Confirm `BOT_TOKEN` is present only in `.env`, internet access is available, and no second bot process is polling the same token.

## Local health fails

Confirm one bot instance is running and port 8765 is not occupied by another process. Open `http://127.0.0.1:8765/health` locally.

## Public webhook fails

Local health does not prove public reachability. Verify DNS, TLS, reverse proxy, firewall, and process supervision using `DNS_SETUP_GUIDE.md`. TradingView must receive HTTP 202 from the public URL.

## Alert is rejected

Check the exact enabled symbol and contract, category, completed-bar timestamp, timeframe, JSON content type, and local secret. Do not paste secrets into logs or support messages.

## No signal appears

This is normal while data is NO DATA, WARMING UP, PARTIAL, STALE, or conflicting. Confirm all required timeframes are fresh. A valid WAIT is expected when no quality setup exists.

## Bot will not start

Verify the three safety flags and run `python -m unittest discover -s tests -v`. Review rotated local logs without sharing credentials.
