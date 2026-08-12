# SHIVAY AI PRO — Work Status

Branch: `fix/angel-production-completion`
Baseline main commit: `f4b2baa` (118 passed, 2 failed)

## Required final architecture
`Angel One SmartAPI` → `authenticated TradingView backup` → `NO FRESH DATA / NO SIGNAL`

## Step log

| # | Step | Status |
|---|------|--------|
| 1 | Repository baseline and GitHub Actions runtime | DONE |
| 2 | Angel provider completion | DONE |
| 3 | TradingView-only emergency backup | DONE |
| 4 | Provider manager, instruments, freshness and cache | DONE |
| 5 | Scanner, Chandelier and BUY/SELL rules | IN PROGRESS |
| 6 | Score, risk, duplicate, cooldown and stale protections | PENDING |
| 7 | Five-to-six-day rejection reporting/logging | PENDING |
| 8 | Scheduler-to-Telegram full-path audit | PENDING |
| 9 | Tests, security checks and one-click Windows scripts | PENDING |
| 10 | Final deployment-readiness audit and pull request | PENDING |

## Step 1 — GitHub Actions runtime (DONE)
* `.github/workflows/tests.yml` — installs requirements, `compileall`, runs `pytest tests`.
* `tools/security_audit.py` — fails the build on committed secrets or any
  live order-placement code path in runtime modules.

## Step 2 — Angel provider completion (DONE)
* `angel_totp.py` — pure-stdlib RFC 6238 TOTP from `ANGEL_TOTP_SECRET`, so login
  no longer depends on a manually pasted 30-second code.
* `angel_instruments.py` — Angel scrip-master download + disk cache (6h TTL),
  resolves nearest non-expired contracts: NIFTY FUT / BANKNIFTY FUT (NFO FUTIDX),
  MCX GOLD / MCX SILVER (MCX FUTCOM, full-size series only), stock futures
  (FUTSTK) and NSE cash. Expired contracts are never returned.
* `angel_provider.py` — automatic login + `generateTokens` session refresh,
  FULL quotes (LTP/OHLC/volume/OI/circuits), 5m/15m/30m/60m candles,
  freshness/timestamp validation, capped retry with re-auth, safe failure.
* `tests/test_angel_production.py` — 28 offline tests covering TOTP, instrument
  resolution, live/stale data, timeframes, retry, and read-only guarantees.

## Safety invariants (enforced by CI)
* No order placement/modification/cancellation anywhere in runtime code.
* No secrets committed; `.env` is git-ignored and audited.
* Signals only; no live trades are ever placed.

## Step 3 — TradingView-only emergency backup (DONE)
The authenticated TradingView alert bridge (`tradingview_bridge.py` +
`tradingview_security.py`: HMAC secret, replay defense, rate limiting, bar
freshness) is now the **only** backup provider. It is registered strictly below
Angel and is never used while Angel is healthy.

## Step 4 — Provider manager, instruments, freshness and cache (DONE)
* `provider_manager.py` rewritten: registers **only** `angelone_primary` and
  `tradingview_alert_bridge`. Groww, Upstox, NSE/MCX temporary, tvkit, Dhan,
  Shoonya, TrueData, GDFL, Fyers, market hub and Yahoo are unregistered at
  runtime and exposed in `status()["disabled_providers"]`.
* No delayed/emergency provider is registered any more: when neither Angel nor
  the bridge supplies fresh verified data the manager raises and the engine
  emits **NO SIGNAL** (`mode = NO_FRESH_DATA_NO_SIGNAL`).
* Angel can never be demoted below the backup, even if `PROVIDER_PRIORITY`
  is inverted; retired keys in configuration are ignored.
* `config.py` / `.env.example` default priority is now
  `angelone_primary,tradingview_alert_bridge`, plus documented `ANGEL_*` keys.
* Instrument cache: `angel_instruments_cache.json` (6h TTL, git-ignored) with a
  stale-cache fallback when Angel's scrip master is briefly unreachable.
* `tests/test_runtime_architecture.py` — 16 tests covering registration,
  failover order, no-signal behaviour, stale/delayed rejection and COMEX-as-MCX
  contract spoofing.
