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
| 5 | Scanner, Chandelier and BUY/SELL rules | DONE |
| 6 | Score, risk, duplicate, cooldown and stale protections | DONE |
| 7 | Five-to-six-day rejection reporting/logging | DONE |
| 8 | Scheduler-to-Telegram full-path audit | DONE |
| 9 | Tests, security checks and one-click Windows scripts | DONE |
| 10 | Final deployment-readiness audit and pull request | IN PROGRESS |

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

## Step 5 — Scanner, Chandelier and BUY/SELL rules (DONE)
* Verified the Angel payload satisfies the engine contract end to end: FULL
  quotes plus 5m candle series supply `open/high/low/close/volume`, `candles`
  and `interval_minutes`, so `score.py`, `chandelier_exit.py`,
  `core/buy_engine.py` and `core/sell_engine.py` receive every field they read.
* `multitimeframe.py` now aggregates higher timeframes from candle timestamps
  instead of positional slicing. Positional resampling merged bars across
  overnight and holiday gaps, which distorted 15m/30m/60m direction — the exact
  inputs the BUY/SELL engines gate on. Positional resampling remains as a
  fallback when a payload has no timestamped candles.
* `tests/test_timeframe_aggregation.py` — 5 tests covering bucket counts across
  multi-session data, bucket-close correctness, and the fallback path.

## Step 6 — Duplicate, cooldown and stale protections (DONE)
* `signal_memory.py` rewritten. It was a bare in-process `set`, so a symbol
  could never re-signal until the daily cleanup ran, and every entry vanished on
  restart (allowing an immediate duplicate after a crash). It now stores
  per-symbol timestamps, honours `SIGNAL_REPEAT_COOLDOWN_MINUTES` (default 45),
  normalises symbol case/whitespace, persists to a git-ignored JSON side-file,
  and exposes `cooldown_remaining()` and `snapshot()`.
* `scheduler._delivery_allowed()` — final gate immediately before delivery,
  re-checking freshness, rejecting any non-signal-capable/delayed payload, and
  enforcing the repeat cooldown. Every block is journalled with its reason.
* `tests/test_signal_protections.py` — 11 tests.

## Step 7 — Five-to-six-session rejection reporting (DONE)
* `rejection_report.py` — groups journalled `REJECTION` events by IST session
  date and by pipeline stage (DATA / FRESHNESS / STRATEGY / CHANDELIER / RISK /
  COOLDOWN / OTHER) over a 5–30 session window (default 6). Reports the dominant
  reason and its share, per-session signal-versus-rejection counts, top rejected
  symbols, and flags a silent pipeline where every candidate was rejected.
* `/rejections [days]` and `/cooldowns` Telegram commands, registered in
  `startup.py` and listed in `/help`.
* The end-of-day scheduler job delivers the digest to administrators.
* `tests/test_rejection_report.py` — 20 tests.

## Step 8 — Scheduler-to-Telegram full-path audit (DONE)
* `tests/test_scheduler_telegram_path.py` — walks the real `_scan_job` path:
  scan → rank → delivery gate → Telegram sender. Proves fresh BUY and SELL
  candidates are delivered through the correct sender, that delivery arms the
  cooldown, and that stale, delayed-provider, duplicate and missing-snapshot
  candidates are all blocked with a journalled reason. A source-level assertion
  keeps the gate ahead of sender selection on the only send path.

## Step 9 — Tests, security checks and Windows scripts (DONE)
* Both baseline failures fixed at the root cause:
  `admin.notify_admins()` silently delivered nothing when no admin was
  configured, so startup and recovery alerts were lost. It now resolves
  recipients via `ADMIN_IDS` → `ADMIN_ID`/`CHAT_ID` → `TELEGRAM_CHAT_ID`, queues
  the message instead of dropping it when nothing is configured, and warns.
  `bot.on_startup` sends exactly one startup card reporting BOT / DATA ENGINE /
  SCANNER / SCHEDULER state and `MODE: SIGNALS ONLY`, with no provider or API
  internals; `startup.py` exposes the `data_engine_ready` flag it reads.
* `SHIVAY_CONTROL.ps1` gained `Stop` and `Logs` actions, a `.env` and dependency
  precheck before launch, and stops only the lock-file PID — never by process
  name. `STOP_SHIVAY.bat` and `LOGS_SHIVAY.bat` added.
* `SIGNAL_REPEAT_COOLDOWN_MINUTES` documented in `.env.example`.
* Security audit passes on every commit: no committed secrets, no live-order
  code path in any runtime module.
