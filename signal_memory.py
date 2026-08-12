"""Persistent signal memory: duplicate suppression, cooldown, expiry, reversal.

Guarantees required by the SHIVAY production specification:

* the same symbol is never signalled twice inside the cooldown window
* the memory survives a process restart (state is written to disk)
* a signal expires after ``SIGNAL_EXPIRY_MINUTES`` and stops blocking
* a genuine direction reversal is allowed once the minimum hold has elapsed
* Telegram retries are idempotent through explicit delivery keys
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config

LOGGER = logging.getLogger("shivay.signal_memory")
ROOT = Path(__file__).resolve().parent
STORE = Path(os.getenv("SIGNAL_MEMORY_FILE", str(ROOT / "storage" / "signal_memory.json")))
_LOCK = threading.RLock()
_STATE: dict[str, dict[str, Any]] = {}
_DELIVERIES: dict[str, str] = {}
_LOADED = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _minutes(name: str, default: int) -> int:
    try:
        return max(1, int(getattr(config, name, default)))
    except (TypeError, ValueError):
        return default


def cooldown_minutes() -> int:
    return _minutes("SIGNAL_COOLDOWN_MINUTES", 30)


def expiry_minutes() -> int:
    return _minutes("SIGNAL_EXPIRY_MINUTES", 120)


def hold_minutes() -> int:
    return _minutes("MIN_HOLD_MINUTES", 10)


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    if not STORE.is_file():
        return
    try:
        payload = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Signal memory file unreadable; starting empty")
        return
    signals = payload.get("signals") if isinstance(payload, dict) else None
    if isinstance(signals, dict):
        for symbol, entry in signals.items():
            if isinstance(entry, dict):
                _STATE[str(symbol)] = dict(entry)
    deliveries = payload.get("deliveries") if isinstance(payload, dict) else None
    if isinstance(deliveries, dict):
        _DELIVERIES.update({str(key): str(value) for key, value in deliveries.items()})
    LOGGER.info("Signal memory restored: %s symbols", len(_STATE))


def _save() -> None:
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STORE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"signals": _STATE, "deliveries": _DELIVERIES}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(STORE)
    except OSError as error:
        LOGGER.warning("Signal memory write failed: %s", type(error).__name__)


def _expired(entry: dict[str, Any], now: datetime) -> bool:
    sent_at = _parse(entry.get("sent_at"))
    if sent_at is None:
        return True
    limit = int(entry.get("expiry_minutes") or expiry_minutes())
    return now - sent_at >= timedelta(minutes=limit)


def purge_expired(now: datetime | None = None) -> int:
    """Drop expired entries so they no longer block fresh signals."""
    moment = now or _now()
    with _LOCK:
        _load()
        stale = [symbol for symbol, entry in _STATE.items() if _expired(entry, moment)]
        for symbol in stale:
            _STATE.pop(symbol, None)
        if stale:
            _save()
        return len(stale)


def signal_exists(symbol: Any) -> bool:
    """True while a live, non-expired signal for the symbol is remembered."""
    if not symbol:
        return False
    with _LOCK:
        _load()
        entry = _STATE.get(str(symbol))
        if not entry:
            return False
        if _expired(entry, _now()):
            _STATE.pop(str(symbol), None)
            _save()
            return False
        return True


def cooldown_remaining_seconds(symbol: Any, now: datetime | None = None) -> float:
    if not symbol:
        return 0.0
    moment = now or _now()
    with _LOCK:
        _load()
        entry = _STATE.get(str(symbol))
        if not entry:
            return 0.0
        sent_at = _parse(entry.get("sent_at"))
        if sent_at is None:
            return 0.0
        window = timedelta(minutes=int(entry.get("cooldown_minutes") or cooldown_minutes()))
        remaining = (sent_at + window - moment).total_seconds()
        return round(max(0.0, remaining), 1)


def can_send(symbol: Any, side: Any = None, now: datetime | None = None) -> tuple[bool, str]:
    """Decide whether a signal may be delivered; returns (allowed, reason)."""
    name = str(symbol or "").strip()
    if not name:
        return False, "missing_symbol"
    direction = str(side or "").upper() or None
    moment = now or _now()
    with _LOCK:
        _load()
        entry = _STATE.get(name)
        if not entry:
            return True, "no_previous_signal"
        if _expired(entry, moment):
            _STATE.pop(name, None)
            _save()
            return True, "previous_signal_expired"
        previous = str(entry.get("side") or "").upper() or None
        sent_at = _parse(entry.get("sent_at")) or moment
        age = moment - sent_at
        if direction and previous and direction != previous:
            if age >= timedelta(minutes=hold_minutes()):
                return True, "direction_reversal_allowed"
            return False, "minimum_hold_not_elapsed"
        if age < timedelta(minutes=int(entry.get("cooldown_minutes") or cooldown_minutes())):
            return False, "cooldown_active"
        return False, "duplicate_signal_suppressed"


def add_signal(symbol: Any, side: Any = None, *, score: Any = None,
               cooldown: int | None = None, expiry: int | None = None,
               now: datetime | None = None) -> dict[str, Any] | None:
    """Remember a delivered signal (persisted immediately)."""
    name = str(symbol or "").strip()
    if not name:
        return None
    moment = now or _now()
    entry = {
        "symbol": name,
        "side": str(side or "").upper() or None,
        "score": score,
        "sent_at": moment.isoformat(),
        "cooldown_minutes": int(cooldown or cooldown_minutes()),
        "expiry_minutes": int(expiry or expiry_minutes()),
    }
    with _LOCK:
        _load()
        _STATE[name] = entry
        _save()
    return dict(entry)


def remove_signal(symbol: Any) -> None:
    if not symbol:
        return
    with _LOCK:
        _load()
        if _STATE.pop(str(symbol), None) is not None:
            _save()


def clear_signals() -> None:
    with _LOCK:
        _load()
        _STATE.clear()
        _DELIVERIES.clear()
        _save()


def total_signals() -> int:
    with _LOCK:
        _load()
        return len(_STATE)


def get_all_signals() -> list[str]:
    with _LOCK:
        _load()
        return list(_STATE)


def get_signal(symbol: Any) -> dict[str, Any] | None:
    with _LOCK:
        _load()
        entry = _STATE.get(str(symbol or ""))
        return dict(entry) if entry else None


def delivery_key(symbol: Any, side: Any, candle: Any = None) -> str:
    return "|".join(str(part or "") for part in (symbol, str(side or "").upper(), candle))


def already_delivered(key: str) -> bool:
    with _LOCK:
        _load()
        return str(key) in _DELIVERIES


def mark_delivered(key: str, now: datetime | None = None) -> bool:
    """Idempotent delivery marker: False if this exact delivery already happened."""
    text = str(key)
    with _LOCK:
        _load()
        if text in _DELIVERIES:
            return False
        _DELIVERIES[text] = (now or _now()).isoformat()
        if len(_DELIVERIES) > 2000:
            for stale in list(_DELIVERIES)[:500]:
                _DELIVERIES.pop(stale, None)
        _save()
        return True


def status() -> dict[str, Any]:
    with _LOCK:
        _load()
        return {
            "symbols": len(_STATE),
            "deliveries": len(_DELIVERIES),
            "cooldown_minutes": cooldown_minutes(),
            "expiry_minutes": expiry_minutes(),
            "minimum_hold_minutes": hold_minutes(),
            "persistent": True,
            "store": str(STORE),
        }


def reset_for_tests(path: Path | None = None) -> None:
    global STORE, _LOADED
    with _LOCK:
        _STATE.clear()
        _DELIVERIES.clear()
        if path is not None:
            STORE = Path(path)
        _LOADED = False


def reload_from_disk() -> int:
    """Simulate a restart: forget in-memory state and re-read the store."""
    global _LOADED
    with _LOCK:
        _STATE.clear()
        _DELIVERIES.clear()
        _LOADED = False
        _load()
        return len(_STATE)
