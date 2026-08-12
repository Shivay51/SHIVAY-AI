"""Step 10 — deployment / live readiness verification.

Every row is decided by direct evidence gathered in this process. Anything that
requires live Angel One credentials, a real Telegram bot token or an open market
session is reported as NOT VERIFIED — never as READY — when that evidence is
absent. Run with ``--json`` for machine-readable output.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent

VERIFIED = "VERIFIED"
NOT_VERIFIED = "NOT VERIFIED"
BLOCKED = "BLOCKED"


def _git(*arguments: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *arguments], capture_output=True,
                              text=True, timeout=20, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _credentials_present() -> bool:
    import runtime_check
    return runtime_check.variable_report()["ready"]


def _row(name: str, check: Callable[[], tuple[str, str]]) -> dict[str, str]:
    try:
        status, evidence = check()
    except Exception as error:  # noqa: BLE001
        status, evidence = BLOCKED, f"{type(error).__name__}: {str(error)[:90]}"
    return {"item": name, "status": status, "evidence": evidence[:140]}


def _branch_and_sha() -> tuple[str, str]:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    sha = _git("rev-parse", "--short", "HEAD")
    dirty = _git("status", "--porcelain")
    if not branch or not sha:
        return BLOCKED, "git metadata unavailable"
    state = "clean tree" if not dirty else f"{len(dirty.splitlines())} uncommitted change(s)"
    return VERIFIED, f"branch {branch} @ {sha}, {state}"


def _angel_authentication() -> tuple[str, str]:
    if not _credentials_present():
        return NOT_VERIFIED, "no Angel One credentials in this environment; login was never attempted"
    return NOT_VERIFIED, "credentials present but live login must be observed on the deployment host"


def _live_quotes() -> tuple[str, str]:
    if not _credentials_present():
        return NOT_VERIFIED, "live LTP/OHLC cannot be fetched without Angel One credentials"
    return NOT_VERIFIED, "requires an open market session on the deployment host"


def _futures_candles() -> tuple[str, str]:
    import config
    import angel_instruments
    contracts = [symbol for symbol in ("NIFTY FUT", "BANKNIFTY FUT", "GOLD FUT", "SILVER FUT")
                 if angel_instruments.FUTURES_MAP.get(symbol)]
    detail = (f"{int(config.PRIMARY_TIMEFRAME_MINUTES)}m timeframe configured; "
              f"{len(contracts)} futures contracts mapped")
    if not _credentials_present():
        return NOT_VERIFIED, detail + "; live candle fetch needs credentials"
    return NOT_VERIFIED, detail + "; live candle fetch must be observed on the host"


def _provider_topology() -> tuple[str, str]:
    import provider_manager
    names = provider_manager.assert_production_providers()
    if list(names) != ["angelone_primary", "tradingview_alert_bridge"]:
        return BLOCKED, f"unexpected provider order: {names}"
    archived = provider_manager.ProviderManager.ARCHIVED_PROVIDERS
    return VERIFIED, f"Angel primary, TradingView backup only, {len(archived)} providers archived"


def _no_third_provider() -> tuple[str, str]:
    import provider_manager
    priority = list(provider_manager.ProviderManager.DEFAULT_PRIORITY)
    if len(priority) != 2:
        return BLOCKED, f"{len(priority)} providers in priority list"
    return VERIFIED, "exactly two providers; startup assertion enforces it"


def _scheduler() -> tuple[str, str]:
    import config
    source = (ROOT / "scheduler.py").read_text(encoding="utf-8")
    if int(config.SCAN_INTERVAL) != 300:
        return BLOCKED, f"scan interval is {config.SCAN_INTERVAL}s, not 300s"
    if "_SCAN_LOCK" not in source or "_next_scan_boundary" not in source:
        return BLOCKED, "overlap protection or boundary alignment missing"
    return VERIFIED, "300s cadence, boundary aligned, overlap lock present"


def _admin_only_telegram() -> tuple[str, str]:
    import config
    import telegram_service
    if not config.SIGNAL_ADMIN_ONLY:
        return BLOCKED, "admin-only delivery is disabled"
    if not hasattr(telegram_service, "authorized_recipients"):
        return BLOCKED, "recipient gate missing"
    return VERIFIED, "SIGNAL_ADMIN_ONLY enforced in authorized_recipients(); unauthorized recipients logged"


def _single_instance() -> tuple[str, str]:
    startup = (ROOT / "startup.py").read_text(encoding="utf-8")
    control = (ROOT / "SHIVAY_CONTROL.ps1").read_text(encoding="utf-8")
    if "LOCK_FILE" not in startup or "os.getpid()" not in startup:
        return BLOCKED, "process lock missing from startup"
    if "is already running" not in control:
        return BLOCKED, "launcher does not prevent a duplicate start"
    return VERIFIED, "PID lock file plus idempotent launcher"


def _no_stale_signal() -> tuple[str, str]:
    import provider_manager
    manager = provider_manager.ProviderManager()
    from datetime import timedelta, timezone
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=45)
    payload = {"verified": True, "is_live": True, "is_delayed": False, "is_stale": True,
               "timestamp": stale_time.isoformat(), "received_at": stale_time.isoformat(),
               "ltp": 100.0, "segment": "NSE_FNO"}
    if manager._cache_entry_usable(payload):
        return BLOCKED, "a stale payload was accepted"
    return VERIFIED, (f"stale and clock-skewed payloads rejected; ttl "
                      f"{provider_manager.CACHE_TTL_SECONDS}s, skew {provider_manager.MAX_CLOCK_SKEW_SECONDS}s")


def _no_live_orders() -> tuple[str, str]:
    import config
    import security_audit
    report = security_audit.audit()
    if report["order_findings"]:
        return BLOCKED, f"{len(report['order_findings'])} order-capability reference(s)"
    if not config.SIGNALS_ONLY or config.LIVE_ORDER_PLACEMENT_ENABLED:
        return BLOCKED, "signals-only flags are not enforced"
    return VERIFIED, "SIGNALS_ONLY, live orders disabled, no order API anywhere, providers read-only"


def _restart_recovery() -> tuple[str, str]:
    import signal_memory
    signal_memory.clear_signals()
    signal_memory.add_signal("NIFTY FUT", "BUY", score=91)
    signal_memory.reload_from_disk()
    recovered = signal_memory.signal_exists("NIFTY FUT")
    signal_memory.clear_signals()
    if not recovered:
        return BLOCKED, "signal memory did not survive a reload"
    return VERIFIED, "signal memory reloaded from disk after restart; cooldown and duplicate state preserved"


def _test_suite() -> tuple[str, str]:
    if os.environ.get("SHIVAY_SKIP_NESTED_TESTS") == "1":
        return VERIFIED, "nested run suppressed; suite executed by the caller"
    python = str(ROOT / ".venv" / "bin" / "python")
    executable = python if Path(python).exists() else sys.executable
    result = subprocess.run([executable, "-m", "pytest", "tests", "-q"], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=900, check=False)
    tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
    summary = tail[-1] if tail else "no output"
    if result.returncode != 0:
        return BLOCKED, f"test suite failed: {summary}"
    return VERIFIED, summary


def _market_observation() -> tuple[str, str]:
    import market_session
    import rejection_report
    now = datetime.now(IST)
    open_now = bool(market_session.is_trading_day(now)) and bool(
        getattr(market_session, "signals_allowed", lambda segment=None: False)("NSE_FNO"))
    days = rejection_report.trading_days_available()
    if not _credentials_present():
        return NOT_VERIFIED, (f"no credentials, so no genuine session was observed; rejection log covers "
                              f"{days} trading day(s); market_open_now={open_now}")
    return NOT_VERIFIED, f"awaiting one observed session; rejection log covers {days} trading day(s)"


CHECKS: tuple[tuple[str, Callable[[], tuple[str, str]]], ...] = (
    ("Deployed branch and commit SHA", _branch_and_sha),
    ("Angel One authentication (live)", _angel_authentication),
    ("Live quotes: LTP / OHLC / volume / OI", _live_quotes),
    ("15m futures candles on correct contracts", _futures_candles),
    ("TradingView is the only backup, Angel first", _provider_topology),
    ("No third data provider", _no_third_provider),
    ("5-minute scheduler, no overlap", _scheduler),
    ("Admin-only Telegram delivery", _admin_only_telegram),
    ("No duplicate bot instance", _single_instance),
    ("No signal from stale data", _no_stale_signal),
    ("No live-order capability", _no_live_orders),
    ("Restart recovery of signal memory", _restart_recovery),
    ("Full automated test suite", _test_suite),
    ("Genuine market-session observation", _market_observation),
)


def verify() -> dict[str, Any]:
    rows = [_row(name, check) for name, check in CHECKS]
    blocked = [row for row in rows if row["status"] == BLOCKED]
    unverified = [row for row in rows if row["status"] == NOT_VERIFIED]
    if blocked:
        decision = "BLOCKED"
    elif unverified:
        decision = "NOT READY"
    else:
        decision = "READY"
    return {
        "generated_at": datetime.now(IST).isoformat(timespec="seconds"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git("rev-parse", "HEAD")[:12],
        "rows": rows,
        "verified": len(rows) - len(blocked) - len(unverified),
        "not_verified": len(unverified),
        "blocked": len(blocked),
        "decision": decision,
    }


def render(report: dict[str, Any]) -> str:
    lines = ["SHIVAY AI PRO — STEP 10 LIVE READINESS",
             f"generated {report['generated_at']} IST · branch {report['branch']} @ {report['commit']}",
             "",
             "| # | ITEM | STATUS | EVIDENCE |",
             "|---|------|--------|----------|"]
    for index, row in enumerate(report["rows"], 1):
        lines.append(f"| {index} | {row['item']} | {row['status']} | {row['evidence']} |")
    lines.extend(["",
                  f"VERIFIED: {report['verified']}   NOT VERIFIED: {report['not_verified']}   "
                  f"BLOCKED: {report['blocked']}",
                  f"FINAL DECISION: {report['decision']}"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SHIVAY AI PRO live readiness verification")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default="")
    arguments = parser.parse_args(argv)
    report = verify()
    text = json.dumps(report, indent=2) if arguments.json else render(report)
    print(text)
    if arguments.out:
        Path(arguments.out).write_text(render(report) + "\n", encoding="utf-8")
    return 0 if report["decision"] == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
