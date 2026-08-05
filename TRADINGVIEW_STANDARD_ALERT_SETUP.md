# SHIVAY AI — TradingView Standard Alert Setup

This uses normal TradingView chart alerts and official placeholders. It requires no Pine Script, scraping, browser automation, or order access.

## Category values

- Indian index and stock Futures: `NSE_FO`
- Indian Gold and Silver contracts: `MCX`
- GIFT NIFTY context: `CONTEXT`
- COMEX/global confirmation only: `GLOBAL_CONTEXT`

Never use a cash index as a Futures contract or COMEX as an Indian MCX price.

## Checklist for each exact symbol

1. Open the exact TradingView chart.
2. Confirm the contract and expiry.
3. Select the 5-minute timeframe.
4. Create Alert.
5. Select the normal chart price/condition supported by TradingView.
6. Set frequency to Once Per Bar Close where appropriate.
7. Enable Webhook URL.
8. Paste `https://app.vanraj.co.in/tradingview-webhook`.
9. Copy the 5m JSON template from `TRADINGVIEW_ALERT_MESSAGES.json`.
10. Replace `<CATEGORY>`, `<EXACT_CONTRACT>`, and `<PASTE_LOCAL_WEBHOOK_SECRET>` with the category above, the exact chart contract, and the local `.env` secret.
11. Save.
12. Repeat for 15m.
13. Repeat for 30m.
14. Repeat for 60m.
15. Verify every required alert is enabled.
16. Send one genuine completed-bar test.
17. Verify TradingView reports HTTP 202.
18. Verify the candle appears in the SHIVAY AI admin health/cache view.

Before step 16, copy the exact TradingView symbol, contract, exchange, category, expiry metadata, and supported timeframes into the matching canonical entry in `tradingview_symbols.json`, then enable it. Do not guess an expiry. Leave every unconfigured entry disabled.

Test in this order: MCX Gold, MCX Silver, NIFTY Futures, BANKNIFTY Futures, GIFT NIFTY, COMEX context, selected stock Futures. Status progresses through NO DATA, WARMING UP, PARTIAL, then READY. STALE or DEGRADED data cannot create a new exact trade.

Local `http://127.0.0.1:8765/health` success does not prove the public endpoint works. Complete `DNS_SETUP_GUIDE.md` and confirm `https://app.vanraj.co.in/health` externally before creating production alerts.

## Start

```powershell
cd C:\SHIVAY_AI
python bot.py
```
