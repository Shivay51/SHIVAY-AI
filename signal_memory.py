"""Persistent duplicate-signal memory for alerts-only delivery."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = Path("storage/signal_memory.json")
_LOCK = threading.RLock()
_TTL = timedelta(hours=24)


def _load() -> dict[str, str]:
    try:
        value = json.loads(_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(value: dict[str, str]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(_PATH)


def _active(value: dict[str, str]) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    kept = {}
    for symbol, stamp in value.items():
        try:
            if now - datetime.fromisoformat(stamp.replace("Z", "+00:00")) < _TTL:
                kept[symbol] = stamp
        except (TypeError, ValueError):
            pass
    return kept


def signal_exists(symbol):
    if not symbol:
        return False
    with _LOCK:
        values = _active(_load())
        _save(values)
        return str(symbol).upper() in values


def add_signal(symbol):
    if not symbol:
        return
    with _LOCK:
        values = _active(_load())
        values[str(symbol).upper()] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _save(values)


def remove_signal(symbol):
    if not symbol:
        return
    with _LOCK:
        values = _active(_load())
        values.pop(str(symbol).upper(), None)
        _save(values)


def clear_signals():
    with _LOCK:
        _save({})


def total_signals():
    with _LOCK:
        return len(_active(_load()))


def get_all_signals():
    with _LOCK:
        return sorted(_active(_load()))
