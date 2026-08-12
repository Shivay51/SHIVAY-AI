# SHIVAY AI PRO — WORK STATUS

Branch: `fix/angel-production-completion` (from `main` @ `f4b2baa`)
Mode: SIGNALS ONLY · paper monitoring · no live order capability
Provider architecture: `angelone_primary` → `tradingview_alert_bridge` → `NO FRESH DATA / NO SIGNAL`

## Step status

| # | Step | Status |
|---|------|--------|
| 1 | Repository & baseline | DONE |
| 2 | Angel One primary provider | DONE (unit-verified, live credentials pending) |
| 3 | TradingView as only backup | IN PROGRESS |
| 4 | Provider manager / cache / freshness | IN PROGRESS |
| 5 | Scanner / Chandelier / BUY-SELL | PENDING |
| 6 | Duplicate / cooldown / rejection report | PENDING |
| 7 | Scanner → Telegram full path | PENDING |
| 8 | All tests + security audit | PENDING |
| 9 | Windows one-click runtime | PENDING |
| 10 | Deployment / live readiness | PENDING |

## Tests

| Run | Result |
|-----|--------|
| Baseline (`f4b2baa`) | 118 passed, 2 failed |
| After Step 2 + architecture rewire | **149 passed, 0 failed, 0 skipped** |

Both baseline failures fixed:
- `tests/test_master_completion.py::test_startup_card_is_single_clean_signals_only_message`
- `tests/test_repair_regressions.py::AdminNotificationTests`

## Files changed so far

- `angel_instruments.py` (new) — official Angel OpenAPI scrip master download, disk cache, daily refresh, nearest valid futures contract resolution (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY FUT, NSE stock futures, MCX GOLD/SILVER/CRUDEOIL/NATURALGAS/COPPER), NFO/MCX/NSE exchange mapping, lot size, tick size, expiry.
- `angel_provider.py` (rewritten) — read-only `AngelReadOnlyProvider`: secure login, runtime RFC-6238 TOTP from `ANGEL_TOTP_SECRET`, JWT/refresh/feed token handling, session expiry + refresh, retry/timeout/rate-limit cooldown/reconnect, LTP, FULL quote, 5m/15m/30m/60m candles, volume, OI, exchange timestamp, received timestamp, data age, fresh/stale status. Import-time assertion that no order surface exists.
- `provider_manager.py` (rewritten) — exactly two production providers with Angel always first, archived-provider registry, health-ranked failover that can never demote a usable Angel session, verified-data gate, `assert_production_providers()` startup assertion, `NO FRESH DATA / NO SIGNAL` decision.
- `market_data_provider.py` — emergency Yahoo context path removed; backup reported as TradingView only.
- `instrument_master.py` — Angel scrip master is now the only verified contract source; archived adapters no longer imported.
- `config.py` — `PROVIDER_PRIORITY` default is `angelone_primary,tradingview_alert_bridge`; added `PRODUCTION_PROVIDERS`, `NO_FRESH_DATA_DECISION`, `ANGEL_INSTRUMENT_REFRESH_HOUR`.
- `bot.py` — startup card now reports BOT / DATA ENGINE / SCANNER / SCHEDULER / MODE without leaking provider or API names; startup asserts the two-provider architecture.
- `.env.example` — canonical Angel variables, production provider priority.
- `tests/test_angel_production.py` (new, 28 tests) — TOTP RFC vectors, instrument resolution and expiry rejection, quote/candle normalization, timeframe support, freshness, failure/rate-limit propagation, read-only surface, two-provider assertion, failover and failback, total-failure behaviour.
- `tests/test_tradingview_bridge.py`, `tests/test_groww_provider.py`, `tests/test_upstox_provider.py`, `tests/test_repair_regressions.py` — updated to the two-provider production architecture.

## Blockers

- No live Angel One credentials in this workspace, so Step 2/10 live-session evidence is unit-level only.
- GitHub push authorization for `fix/angel-production-completion` not yet confirmed.

## Next action

Step 3 — TradingView backup hardening (webhook auth, replay protection, completed-candle validation, exact futures mapping, persistent candle store, never outranks Angel, no mixed-provider candles).
