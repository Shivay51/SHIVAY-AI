"""Automatic MCX continuous-contract advisory scanner.

Alerts are informational only and must be verified against the active MCX contract.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from alert_only_strategy import build_signal, telegram_text
from mcx_temporary_provider import MCXTemporaryProvider

_STATE = Path("storage/mcx_continuous_cooldown.json")
_COOLDOWN = timedelta(minutes=25)


def _frame(candles):
    return pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _load_state():
    try:
        value = json.loads(_STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(value):
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(_STATE)


def _allowed(key, side):
    state = _load_state()
    previous = state.get(key)
    if previous and previous.get("side") == side:
        try:
            sent = datetime.fromisoformat(previous["sent_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - sent < _COOLDOWN:
                return False
        except (KeyError, TypeError, ValueError):
            pass
    state[key] = {"side": side, "sent_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    _save_state(state)
    return True


def _signal(provider, underlying):
    candles = {}
    for timeframe in ("15m", "30m", "60m"):
        rows = provider.get_historical_candles(f"MCX {underlying}", timeframe, 5)
        if len(rows) < 60:
            return None
        candles[timeframe] = _frame(rows)
    signal = build_signal(candles["15m"], candles["30m"], candles["60m"], "NEUTRAL")
    if signal is None or signal.score < 75:
        return None
    price = float(candles["15m"].iloc[-1]["close"])
    risk = abs(signal.entry - signal.stop_loss)
    reward = abs(signal.target2 - signal.entry)
    if risk <= 0 or reward / risk < 2.0 or not any("volume" in str(reason).lower() for reason in signal.reasons):
        return None
    if not _allowed(underlying, signal.side):
        return None
    body = telegram_text(underlying, f"MCX:{underlying}1!", "MCX CONTINUOUS", price, signal)
    return "⚠️ CONTINUOUS CONTRACT ADVISORY — Verify active MCX expiry before acting.\n\n" + body


def scan_mcx():
    provider = MCXTemporaryProvider()
    if not provider.available:
        return ""
    messages = [message for underlying in ("GOLD", "SILVER") if (message := _signal(provider, underlying))]
    return "\n\n━━━━━━━━━━━━━━━━\n\n".join(messages)


def scheduled_task():
    return scan_mcx()
