"""Append-only signal, rejection, and outcome journal for live observation."""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import config

LOGGER = logging.getLogger("shivay.performance.journal")
ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "storage" / "trade_journal.jsonl"
_LOCK = threading.RLock()
_ALLOWED = (
    "signal_id", "symbol", "side", "decision", "market_category", "setup", "setup_type",
    "setup_time", "telegram_time", "data_timestamp", "market_regime", "regime",
    "nifty_direction", "banknifty_direction", "sector", "sector_strength", "score",
    "confidence", "instrument_type", "entry", "exit", "exit_price", "delivery_price", "sl", "target1",
    "target2", "target3", "risk_reward", "valid_until", "adx", "rsi", "atr", "vwap",
    "relative_volume", "support", "resistance", "result", "pnl", "mfe", "mae",
    "holding_seconds", "opened_at", "closed_at", "time_slot", "time_to_sl", "time_to_t1", "time_to_t2", "time_to_t3",
    "failure_cause", "reason", "reasons", "strategy", "market_condition", "bot_version",
    "signal_context_score", "relative_strength", "sector_momentum", "timeframe_5m",
    "timeframe_15m", "timeframe_30m", "timeframe_60m",
    "entry_confirmed", "cancelled", "cancel_reason", "smart_money_activity",
    "relative_weakness",
    "signal_candle_high", "signal_candle_low", "signal_candle_close",
    "signal_candle_timestamp", "entry_trigger_status", "entry_confirmation_reason",
    "active_chandelier_stop", "chandelier_15m", "chandelier_30m", "chandelier_60m",
    "config_version",
)


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None: return value
    if isinstance(value, (list, tuple)): return [str(item)[:120] for item in value[:20]]
    return str(value)[:300]


def _normalized(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload)
    market = value.get("market_data") if isinstance(value.get("market_data"), Mapping) else {}
    validity = value.get("entry_validity") if isinstance(value.get("entry_validity"), Mapping) else {}
    value.setdefault("signal_id", value.get("event_id") or uuid.uuid4().hex)
    value.setdefault("side", "SELL" if "SELL" in str(value.get("decision", "")).upper() else "BUY")
    value.setdefault("setup_type", value.get("setup"))
    value.setdefault("setup_time", value.get("data_timestamp") or market.get("timestamp"))
    value.setdefault("telegram_time", datetime.now(timezone.utc).isoformat())
    value.setdefault("data_timestamp", market.get("timestamp"))
    value.setdefault("market_regime", value.get("regime") or value.get("market"))
    value.setdefault("delivery_price", value.get("price"))
    value.setdefault("exit", value.get("exit_price"))
    value.setdefault("risk_reward", validity.get("current_risk_reward"))
    value.setdefault("bot_version", getattr(config, "BOT_VERSION", "UNKNOWN"))
    value.setdefault("config_version", getattr(config, "SIGNAL_CONFIG_VERSION", "UNVERSIONED"))
    if not value.get("time_slot"):
        try:
            hour = datetime.fromisoformat(str(value["setup_time"]).replace("Z", "+00:00")).astimezone().hour
            value["time_slot"] = "OPENING" if hour < 11 else "MIDDAY" if hour < 14 else "CLOSING"
        except (KeyError, TypeError, ValueError):
            value["time_slot"] = "UNKNOWN"
    return value


def record_event(event_type: str, payload: Mapping[str, Any]) -> str | None:
    normalized = _normalized(payload)
    entry = {
        "event_id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": str(event_type)[:40],
    }
    entry.update({key: _safe(normalized.get(key)) for key in _ALLOWED if key in normalized})
    try:
        with _LOCK:
            JOURNAL.parent.mkdir(parents=True, exist_ok=True)
            with JOURNAL.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return entry["event_id"]
    except OSError as error:
        LOGGER.warning("Journal write failed: %s", type(error).__name__)
        return None


def record_signal(signal: Mapping[str, Any]) -> str | None: return record_event("SIGNAL", signal)
def record_rejection(symbol: str, reasons: list[str], details: Mapping[str, Any] | None = None) -> str | None: return record_event("REJECTION", {"symbol": symbol, "reasons": reasons, **dict(details or {})})
def record_outcome(trade: Mapping[str, Any]) -> str | None: return record_event("OUTCOME", trade)


def load_events(limit: int | None = None) -> list[dict[str, Any]]:
    if not JOURNAL.is_file(): return []
    result: list[dict[str, Any]] = []
    try:
        with _LOCK: lines = JOURNAL.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-limit:] if limit else lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict): result.append(value)
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return result
