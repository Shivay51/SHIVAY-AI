# SHIVAY AI PRO — WORK STATUS

Branch: `fix/angel-production-completion` (from `main` @ `f4b2baa`)
Mode: SIGNALS ONLY · paper monitoring · no live order capability
Provider architecture: `angelone_primary` → `tradingview_alert_bridge` → `NO FRESH DATA / NO SIGNAL`

## Step status

| # | Step | Status |
|---|------|--------|
| 1 | Repository & baseline | DONE |
| 2 | Angel One primary provider | DONE (unit-verified, live credentials pending) |
| 3 | TradingView as only backup | DONE |
| 4 | Provider manager / cache / freshness | DONE |
| 5 | Scanner / Chandelier / BUY-SELL | DONE |
| 6 | Duplicate / cooldown / rejection report | DONE |
| 7 | Scanner → Telegram full path | DONE |
| 8 | All tests + security audit | PENDING |
| 9 | Windows one-click runtime | PENDING |
| 10 | Deployment / live readiness | PENDING |

## Tests

| Run | Result |
|-----|--------|
| Baseline (`f4b2baa`) | 118 passed, 2 failed |
| After Step 2 + architecture rewire | 149 passed, 0 failed |
| After Step 3 (backup role hardening) | 162 passed, 0 failed |
| After Step 4 (session/cache/freshness gate) | 186 passed, 0 failed |
| After Step 5 (scanner / Chandelier / symmetry) | 211 passed, 0 failed |
| After Step 6 (duplicate / cooldown / rejection log) | 232 passed, 0 failed |
| After Step 7 (Telegram delivery path) | **245 passed, 0 failed, 0 skipped** |

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


## Step 5 — scanner / Chandelier / BUY-SELL safety

- `signal_classification.py` (new) — score >= 70 actionable, 80+ SAFE, 90+ SAFE + PREMIUM tier, below 70 IGNORE. IGNORE-class signals are never delivered (filtered in both `scanner.scan_market` and `scheduler._rank`). Identical thresholds for BUY and SELL.
- `chandelier_exit.py` — confirmation window now has an upper bound (`CHANDELIER_CONFIRMATION_MAX_SECONDS`, late confirmation rejected), over-travelled entries rejected against ATR (`CHANDELIER_MAX_ENTRY_TRAVEL_ATR`), signal-candle hard invalidation enforced at confirmation, minimum hold minutes attached to every confirmed state. Non-adjacent next candle is still refused.
- `strategy.py` — BUY/SELL symmetry repaired: SELL RSI band mirrored to 28–46 (was 25–48), SELL now requires `market_strength <= 45` mirroring BUY's `>= 55`, and SELL rejects SIDEWAYS markets like BUY does.
- `scanner.py` — market-session gate (no scan output when the market is closed), 15m primary timeframe recorded on every candidate, classification annotated, SAFE/RISKY/PREMIUM counters in scan diagnostics.
- `data.py` — scan universe requested at the 15m primary timeframe instead of 5m; universe is NSE F&O + MCX futures contracts only.
- `angel_instruments.py` — legacy `GOLD FUT` / `SILVER FUT` / `CRUDEOIL FUT` / `NATURALGAS FUT` / `COPPER FUT` aliases resolve to the correct MCX futures contracts.
- `config.py` — `CHANDELIER_CONFIRMATION_MAX_SECONDS`, `CHANDELIER_MAX_ENTRY_TRAVEL_ATR`, `MIN_HOLD_MINUTES=10`, `ACTIONABLE_SCORE=70`, `SAFE_SCORE=80`, `PREMIUM_SCORE=90`.
- `tests/test_scanner_symmetry.py` (new, 25 tests) — timeframe/cadence, classification thresholds mirrored across BUY and SELL, BUY and SELL signal detection + confirmation, pending confirmation, late rejection, over-travel rejection on both sides, signal-candle invalidation, non-adjacent candle refusal, minimum hold, trailing-stop mirroring, strategy gate symmetry assertions, market-closed scan gate, futures-only universe, delivery-path IGNORE suppression.

## Step 6 — duplicate / cooldown / persistence / rejection report

- `signal_memory.py` (rewritten) — persistent JSON store (`storage/signal_memory.json`, atomic replace) so duplicate suppression survives a restart. Adds per-symbol side/score/sent_at, `can_send()` returning an explicit reason (`cooldown_active`, `duplicate_signal_suppressed`, `previous_signal_expired`, `direction_reversal_allowed`, `minimum_hold_not_elapsed`), `cooldown_remaining_seconds`, `purge_expired`, idempotent delivery markers (`delivery_key` / `already_delivered` / `mark_delivered`), `status()`, and restart simulation via `reload_from_disk()`. Legacy API (`signal_exists`, `add_signal`, `remove_signal`, `clear_signals`, `total_signals`, `get_all_signals`) preserved.
- `scheduler.py` — scan job purges expired entries, uses `can_send(symbol, side)` instead of a bare existence check, and wraps delivery in an idempotency key built from symbol + side + signal-candle timestamp so a Telegram retry cannot double-send. Memory is written only after a confirmed delivery.
- `tradingview_bridge.py` — backup delivery path uses the same shared memory and delivery keys, so an Angel→TradingView failover cannot repeat a signal.
- `autoscan.py` — no longer records signals before delivery (that caused signals to be swallowed); it now only filters with `can_send`.
- `rejection_log.py` (new) — structured JSONL rejection log with size-based rotation (`REJECTION_LOG_MAX_BYTES`, `REJECTION_LOG_BACKUPS`) and recursive secret redaction (key-name based plus OTP / base32 TOTP / Telegram-token / JWT patterns).
- `rejection_report.py` (new) — truthful report grouped by IST trading day from real records only; marks itself INCOMPLETE when fewer trading days exist and states `NO REJECTION DATA RECORDED YET` for an empty log. Never estimates.
- `scanner.py` — every rejection is also written to the structured rejection log; a closed market records one `market_closed` rejection per watchlist symbol.
- `market_session.py` — `is_trading_day()` now accepts a plain date as well as a datetime.
- `config.py` / `.env.example` — `SIGNAL_COOLDOWN_MINUTES=30`, `SIGNAL_EXPIRY_MINUTES=120` plus the Step 5 thresholds documented.
- `tests/test_duplicate_cooldown.py` (new, 21 tests) — duplicate suppression, cooldown window, expiry release, reversal only after minimum hold, restart during cooldown, purge, delivery idempotency across restart, failover duplicate prevention, repeated scheduler passes, log structure, secret redaction, rotation, report incompleteness/completeness/empty/weekend handling, single-instance lock and scan-lock assertions.

Real rejection report status: only 1 trading day of real records exists in this workspace (all `market_closed`, because no live market session has been observed here). The 5–6 trading-day report will remain marked INCOMPLETE until the bot runs through real sessions — no data has been invented.

## Step 7 — scanner → Telegram full path

- `telegram_service.py` — new `sanitize()` strips URLs, `t.me` links, stack-trace fragments and credential-shaped values (OTP, base32 TOTP, bot token, JWT) from every outgoing message; applied inside `_send` so nothing bypasses it. New `authorized_recipients()` enforces admin-only signal delivery (`SIGNAL_ADMIN_ONLY`, default on) and logs every rejected recipient to the structured rejection log. New `_deliver_once()` retries with linear backoff (`TELEGRAM_SEND_ATTEMPTS`, `TELEGRAM_RETRY_BACKOFF_SECONDS`) and the message key is only acknowledged after a real delivery, so a retry cannot duplicate a message.
- `telegram_service._signal_text` — final message now carries every required field: SHIVAY AI PRO header, market, futures contract, BUY/SELL direction, verified current price, entry zone, stop loss, T1/T2/T3, score, SAFE/RISKY (from the Step 5 classifier, with a PREMIUM 90+ marker), Chandelier signal + confirmation, signal-candle high/low, volume, OI, risk:reward, trade type (INTRADAY/OVERNIGHT), signal timestamp, data freshness with age, valid-until, contract expiry and the top three short reasons.
- `config.py` / `.env.example` — `SIGNAL_ADMIN_ONLY=true`, `TELEGRAM_SEND_ATTEMPTS=3`, `TELEGRAM_RETRY_BACKOFF_SECONDS=1.5`.
- `tests/test_telegram_path.py` (new, 13 tests) — required-field coverage, PREMIUM/RISKY honesty, no URLs/credentials/traces, admin-only gating, unauthorized rejection logging, retry-then-success, attempt budget exhaustion, acknowledged messages never resent, sanitisation of transmitted text, 5-minute non-overlapping scheduler assertions, and an end-to-end `_scan_job` idempotency test proving the same signal candle is delivered exactly once across two scheduler passes.

Current step: 8 — fix all tests + security audit.
Blockers: no live Angel credentials in the workspace (Step 2/10 evidence is unit-level); GitHub push authorization unconfirmed; no Windows/VPS access for Step 9/10 live verification.
