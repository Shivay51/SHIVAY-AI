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
| 3 | TradingView-only emergency backup | IN PROGRESS |
| 4 | Provider manager, instruments, freshness and cache | PENDING |
| 5 | Scanner, Chandelier and BUY/SELL rules | PENDING |
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
