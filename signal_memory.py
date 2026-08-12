"""Duplicate-signal memory with per-symbol cooldown and crash persistence.

The previous implementation was a bare in-process ``set``. That gave two
production problems:

* a symbol that signalled once could never signal again until the daily
  cleanup ran, even hours later; and
* every entry was lost on restart, so a process restart could immediately
  re-deliver a signal that had just been sent.

This module keeps the original public API (``signal_exists``, ``add_signal``,
``remove_signal``, ``clear_signals``, ``total_signals``, ``get_all_signals``)
and adds timestamped cooldown handling plus a small JSON side-file so the
cooldown survives a restart.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

LOGGER = logging.getLogger("shivay.signal_memory")

STATE_FILE = Path(__file__).resolve().parent / "signal_memory_state.json"

_LOCK = threading.RLock()
_sent_signals: dict[str, float] = {}
_loaded = False


def _cooldown_seconds() -> float:
    """Minutes a symbol stays blocked after a delivered signal."""
    raw = os.getenv("SIGNAL_REPEAT_COOLDOWN_MINUTES")
    if raw is None:
        try:
            import config

            raw = getattr(config, "SIGNAL_REPEAT_COOLDOWN_MINUTES", 45)
        except Exception:
            raw = 45
    try:
        minutes = float(str(raw).strip())
    except (TypeError, ValueError):
        minutes = 45.0
    return max(0.0, minutes) * 60.0


def _clean(symbol) -> str:
    return str(symbol or "").strip().upper()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if STATE_FILE.is_file():
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                now = time.time()
                for symbol, stamp in payload.items():
                    try:
                        value = float(stamp)
                    except (TypeError, ValueError):
                        continue
                    # Ignore clock-skewed future stamps and anything already expired.
                    if 0 < value <= now:
                        _sent_signals[_clean(symbol)] = value
    except (OSError, ValueError, json.JSONDecodeError):
        LOGGER.warning("Signal memory state could not be restored; starting empty")


def _persist() -> None:
    try:
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(_sent_signals), encoding="utf-8")
        temporary.replace(STATE_FILE)
    except OSError:
        LOGGER.warning("Signal memory state could not be saved")


def _prune(now: float | None = None) -> None:
    now = time.time() if now is None else now
    cooldown = _cooldown_seconds()
    if cooldown <= 0:
        return
    expired = [symbol for symbol, stamp in _sent_signals.items() if now - stamp >= cooldown]
    for symbol in expired:
        _sent_signals.pop(symbol, None)


def signal_exists(symbol) -> bool:
    """True when the symbol is still inside its repeat cooldown."""
    key = _clean(symbol)
    if not key:
        return False
    with _LOCK:
        _load()
        stamp = _sent_signals.get(key)
        if stamp is None:
            return False
        cooldown = _cooldown_seconds()
        if cooldown <= 0:
            return True
        if time.time() - stamp >= cooldown:
            _sent_signals.pop(key, None)
            _persist()
            return False
        return True


def cooldown_remaining(symbol) -> float:
    """Seconds left before the symbol may signal again (0 when free)."""
    key = _clean(symbol)
    if not key:
        return 0.0
    with _LOCK:
        _load()
        stamp = _sent_signals.get(key)
        if stamp is None:
            return 0.0
        remaining = _cooldown_seconds() - (time.time() - stamp)
        return max(0.0, remaining)


def seconds_since_signal(symbol) -> float | None:
    key = _clean(symbol)
    with _LOCK:
        _load()
        stamp = _sent_signals.get(key)
    return None if stamp is None else max(0.0, time.time() - stamp)


def add_signal(symbol) -> None:
    key = _clean(symbol)
    if not key:
        return
    with _LOCK:
        _load()
        _sent_signals[key] = time.time()
        _prune()
        _persist()


def remove_signal(symbol) -> None:
    key = _clean(symbol)
    if not key:
        return
    with _LOCK:
        _load()
        if _sent_signals.pop(key, None) is not None:
            _persist()


def clear_signals() -> None:
    with _LOCK:
        _load()
        _sent_signals.clear()
        _persist()


def total_signals() -> int:
    with _LOCK:
        _load()
        _prune()
        return len(_sent_signals)


def get_all_signals() -> list[str]:
    with _LOCK:
        _load()
        _prune()
        return sorted(_sent_signals)


def snapshot() -> dict[str, float]:
    """Non-secret cooldown report: symbol -> seconds remaining."""
    with _LOCK:
        _load()
        _prune()
        cooldown = _cooldown_seconds()
        now = time.time()
        return {
            symbol: round(max(0.0, cooldown - (now - stamp)), 1)
            for symbol, stamp in sorted(_sent_signals.items())
        }
