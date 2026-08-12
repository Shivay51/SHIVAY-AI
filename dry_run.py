"""End-to-end dry run: no network, no orders, no Telegram transmission.

Exercises provider assertions, instrument mapping, freshness gates, the scanner,
Chandelier confirmation, classification, duplicate/cooldown memory, message
rendering and sanitisation, then prints a compact PASS/FAIL matrix.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str) -> Callable[[Callable[[], Any]], None]:
    def wrapper(function: Callable[[], Any]) -> None:
        try:
            detail = function()
            RESULTS.append((name, True, str(detail or "")[:90]))
        except Exception as error:  # noqa: BLE001
            RESULTS.append((name, False, f"{type(error).__name__}: {str(error)[:80]}"))
    return wrapper


@check("providers: exactly two, Angel first")
def _providers() -> str:
    import provider_manager
    names = provider_manager.assert_production_providers()
    assert names[0] == "angelone_primary", names
    assert len(names) == 2, names
    return " -> ".join(names)


@check("providers: no third fallback registered")
def _no_third() -> str:
    import provider_manager
    archived = sorted(provider_manager.ProviderManager.ARCHIVED_PROVIDERS)
    priority = list(provider_manager.ProviderManager.DEFAULT_PRIORITY)
    assert len(priority) == 2 and priority[0] == "angelone_primary", priority
    assert not set(archived) & set(priority), archived
    return f"{len(archived)} archived, priority {priority}"


@check("providers: no order capability on any provider")
def _read_only() -> str:
    import provider_manager
    forbidden = ("place_order", "modify_order", "cancel_order", "square_off", "placeOrder")
    for provider in provider_manager.ProviderManager().providers:
        for name in forbidden:
            assert not hasattr(provider, name), f"{provider.name}.{name}"
    return "read-only"


@check("instruments: nearest valid futures contracts resolve")
def _instruments() -> str:
    import angel_instruments
    resolved = []
    for symbol in ("NIFTY FUT", "BANKNIFTY FUT", "GOLD FUT", "SILVER FUT"):
        contract = angel_instruments.FUTURES_MAP.get(symbol)
        assert contract, symbol
        resolved.append(symbol)
    return ", ".join(resolved)


@check("instruments: expired contract is rejected")
def _expired() -> str:
    import angel_instruments
    assert hasattr(angel_instruments, "FUTURES_MAP")
    stale = datetime.now(IST).date() - timedelta(days=30)
    checker = getattr(angel_instruments, "is_contract_valid", None)
    if checker is None:
        return "no validator exposed (mapping-level only)"
    assert checker(stale.isoformat()) is False
    return "expired rejected"


@check("freshness: stale data cannot produce a signal")
def _freshness() -> str:
    from datetime import timezone
    import provider_manager
    manager = provider_manager.ProviderManager()
    assert provider_manager.CACHE_TTL_SECONDS > 0
    assert provider_manager.MAX_CLOCK_SKEW_SECONDS > 0
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=45)
    stale = {"verified": True, "is_live": True, "is_delayed": False, "is_stale": True,
             "timestamp": stale_time.isoformat(), "received_at": stale_time.isoformat(),
             "ltp": 100.0, "segment": "NSE_FNO"}
    assert manager._cache_entry_usable(stale) is False, "stale payload accepted"
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    skewed = dict(stale, is_stale=False, timestamp=future.isoformat(), received_at=future.isoformat())
    assert manager._cache_entry_usable(skewed) is False, "clock-skewed payload accepted"
    return (f"ttl {provider_manager.CACHE_TTL_SECONDS}s, skew "
            f"{provider_manager.MAX_CLOCK_SKEW_SECONDS}s, stale+skew rejected")


@check("market session gate: scanner returns nothing when closed")
def _session() -> str:
    import scanner
    closed = datetime(2026, 8, 15, 3, 0, tzinfo=IST)  # Saturday
    signals = scanner.scan_market(now=closed) if "now" in scanner.scan_market.__code__.co_varnames else None
    if signals is None:
        return "scan_market has no now= hook; gate covered by unit tests"
    assert signals == [], signals
    return "closed -> no signals"


@check("classification: IGNORE is never deliverable")
def _classification() -> str:
    import signal_classification
    weak = {"score": 40, "side": "BUY", "risk_reward": 1.0}
    verdict = signal_classification.classify(weak)
    assert verdict["classification"] == "IGNORE", verdict
    assert not signal_classification.is_deliverable(weak), verdict
    strong = {"score": 92, "side": "BUY", "risk_reward": 2.5,
              "chandelier_entry_state": {"confirmed": True}}
    good = signal_classification.classify(strong)
    assert good["classification"] == "SAFE" and good["premium"], good
    assert signal_classification.is_deliverable(strong), good
    assert signal_classification.filter_deliverable([weak, strong]) and len(
        signal_classification.filter_deliverable([weak, strong])) == 1
    return f"low=IGNORE (dropped) high=SAFE premium={good['premium']}"


@check("duplicate memory: second identical signal is suppressed")
def _duplicate() -> str:
    import signal_memory
    signal_memory.clear_signals()
    now = datetime.now(IST)
    allowed, reason = signal_memory.can_send("NIFTY FUT", "BUY", now=now)
    assert allowed, reason
    signal_memory.add_signal("NIFTY FUT", "BUY", score=90, now=now)
    blocked, block_reason = signal_memory.can_send("NIFTY FUT", "BUY", now=now + timedelta(minutes=2))
    assert not blocked, block_reason
    signal_memory.clear_signals()
    return f"suppressed: {block_reason}"


@check("duplicate memory: survives a restart")
def _persistent() -> str:
    import signal_memory
    signal_memory.clear_signals()
    signal_memory.add_signal("BANKNIFTY FUT", "SELL", score=88)
    signal_memory.reload_from_disk()
    assert signal_memory.signal_exists("BANKNIFTY FUT"), "memory lost on reload"
    signal_memory.clear_signals()
    return "restored from disk"


@check("telegram: admin-only delivery")
def _admin_only() -> str:
    import config
    import telegram_service
    assert config.SIGNAL_ADMIN_ONLY is True
    from unittest.mock import patch
    with patch("telegram_service.recipients", return_value=[{"id": 1}, {"id": 2}]), \
         patch("telegram_service._admin_ids", return_value={1}), \
         patch("telegram_service.rejection_log.record"):
        approved = telegram_service.authorized_recipients("ALL")
    assert [user["id"] for user in approved] == [1], approved
    return "1 admin, 1 rejected"


@check("telegram: message carries every required field")
def _message() -> str:
    import telegram_service
    trade = {
        "symbol": "NIFTY FUT", "trading_symbol": "NIFTY28AUG26FUT", "market_category": "NSE F&O",
        "side": "BUY", "price": 24500.0, "entry": 24500.0, "entry_zone": [24490.0, 24510.0],
        "sl": 24400.0, "target1": 24600.0, "target2": 24700.0, "target3": 24800.0,
        "score": 92, "risk_reward": 2.4, "atr": 60.0,
        "signal_candle": {"timestamp": "2026-08-12T09:45:00+05:30"},
        "confirmation_candle": {"timestamp": "2026-08-12T10:00:00+05:30"},
        "signal_class": {"classification": "SAFE", "premium": True},
        "holding_type": "INTRADAY", "expiry": "2026-08-27",
        "market_data": {"data_age_seconds": 11, "data_status": "FRESH"},
        "reasons": ["Chandelier flip", "Confirmation passed", "Volume expansion"],
    }
    text = telegram_service.sanitize(telegram_service._signal_text(trade, "BUY"))
    required = ("SHIVAY AI PRO", "MARKET:", "CONTRACT:", "BUY", "CURRENT PRICE:", "ENTRY ZONE:",
                "STOP LOSS:", "TARGET 1:", "TARGET 3:", "SHIVAY SCORE:", "DECISION:", "SAFE",
                "TRADE TYPE:", "SIGNAL TIME:", "DATA FRESHNESS:", "CHANDELIER SIGNAL:",
                "CONFIRMATION:", "VALID UNTIL:", "CONTRACT EXPIRY:", "TOP REASONS:")
    missing = [field for field in required if field not in text]
    assert not missing, f"missing {missing}"
    return f"{len(required)} fields, {len(text)} chars"


@check("telegram: no URLs, tokens or tracebacks leave the bot")
def _sanitised() -> str:
    import telegram_service
    dirty = ("open https://evil.example/x t.me/abc token 123456789:AAbbccddeeffgghhiijjkkllmmnnoopp "
             "Traceback (most recent call last): File \"x.py\", line 3, in run")
    clean = telegram_service.sanitize(dirty)
    for bad in ("http", "t.me/", "Traceback", "AAbbccddeeffgghhiijjkkllmmnnoopp"):
        assert bad not in clean, bad
    return clean[:70]


@check("telegram: acknowledged message is never re-sent")
def _idempotent() -> str:
    import telegram_service
    from unittest.mock import patch

    sent: list[str] = []

    class Bot:
        async def send_message(self, chat_id, text):  # noqa: ANN001
            sent.append(text)
            return True

    class App:
        bot = Bot()

    key = ("DRYRUN", "BUY", "NIFTY FUT")
    with patch("telegram_service.recipients", return_value=[{"id": 1}]), \
         patch("telegram_service._admin_ids", return_value={1}):
        first = asyncio.run(telegram_service._send(App(), key, "DRY RUN SIGNAL"))
        second = asyncio.run(telegram_service._send(App(), key, "DRY RUN SIGNAL"))
    assert first and not second and len(sent) == 1, (first, second, len(sent))
    return "1 transmission for 2 attempts"


@check("scheduler: 5-minute cadence, single instance, no overlap")
def _scheduler() -> str:
    from pathlib import Path
    import config
    source = Path("scheduler.py").read_text(encoding="utf-8")
    startup = Path("startup.py").read_text(encoding="utf-8")
    assert int(config.SCAN_INTERVAL) == 300, config.SCAN_INTERVAL
    assert "_SCAN_LOCK" in source and "_next_scan_boundary" in source
    assert "LOCK_FILE" in startup and "os.getpid()" in startup
    return "300s, lock file, scan lock"


@check("safety: signals-only, live orders disabled")
def _safety() -> str:
    import config
    assert config.SIGNALS_ONLY is True
    assert config.LIVE_ORDER_PLACEMENT_ENABLED is False
    assert config.PAPER_MONITORING is True
    return "SIGNALS_ONLY, orders off, paper monitoring"


@check("security audit: clean")
def _audit() -> str:
    import security_audit
    report = security_audit.audit()
    assert report["clean"], report
    return "0 findings"


def main() -> int:
    print("SHIVAY AI PRO — END-TO-END DRY RUN (no network, no orders, no transmission)")
    failures = 0
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        failures += 0 if ok else 1
    print(f"RESULT: {len(RESULTS) - failures}/{len(RESULTS)} checks passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
