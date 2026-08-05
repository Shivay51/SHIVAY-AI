"""Strict normalization for official TradingView standard-alert payloads.

The legacy custom-Pine payload remains accepted during migration, but all
indicator fields are optional and are recomputed from accepted OHLCV candles.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
import re
from typing import Any, Mapping


class PayloadError(ValueError):
    pass


CORE_NUMBERS = ("open", "high", "low", "close", "volume")
NUMBERS = (
    *CORE_NUMBERS, "previous_close", "day_open", "day_high", "day_low",
    "previous_day_high", "previous_day_low", "ema20", "ema50", "ema200",
    "vwap", "rsi", "adx", "plus_di", "minus_di", "macd", "macd_signal",
    "macd_histogram", "atr", "relative_volume", "chandelier_long_stop",
    "chandelier_short_stop", "swing_high", "swing_low", "breakout_level",
    "breakdown_level", "signal_candle_high", "signal_candle_low",
)
BOOLEANS = (
    "higher_high", "higher_low", "lower_high", "lower_low", "bullish_breakout",
    "bearish_breakdown", "bullish_retest", "bearish_retest", "bullish_pullback",
    "bearish_pullback", "chandelier_direction_changed", "preliminary_buy",
    "preliminary_sell", "volume_confirmed", "setup_valid",
)
EVENT_TYPES = {"timeframe_update", "preliminary_buy", "preliminary_sell", "chandelier_direction_change", "test", "candle"}
CATEGORIES = {"NSE_FO", "MCX", "CONTEXT", "GIFT", "GLOBAL_CONTEXT"}
SOURCES = {"TRADINGVIEW_STANDARD_ALERT", "TRADINGVIEW_STANDARD_ALERT_BRIDGE", "TRADINGVIEW_PINE_ALERT"}
_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")


def _number(data: Mapping[str, Any], name: str, *, required: bool = False, default: float = 0.0) -> float:
    if name not in data or data.get(name) in (None, ""):
        if required:
            raise PayloadError(f"invalid_{name}")
        return default
    try:
        value = float(data[name])
    except (TypeError, ValueError, OverflowError):
        raise PayloadError(f"invalid_{name}") from None
    if not math.isfinite(value):
        raise PayloadError(f"invalid_{name}")
    return value


def _stamp(value: Any, name: str) -> datetime:
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            result = datetime.fromtimestamp(number / (1000 if number > 10_000_000_000 else 1), timezone.utc)
        else:
            text = str(value).strip().replace("Z", "+00:00")
            result = datetime.fromisoformat(text)
        return (result if result.tzinfo else result.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        raise PayloadError(f"invalid_{name}") from None


def _text(value: Any, name: str, *, optional: bool = False) -> str:
    result = str(value or "").strip()
    if not result and optional:
        return ""
    if not _TEXT_RE.fullmatch(result):
        raise PayloadError(f"invalid_{name}")
    return result


@dataclass(frozen=True)
class TradingViewPayload:
    event_id: str
    event_type: str
    script_version: str
    source: str
    symbol: str
    ticker_id: str
    exchange: str
    instrument: str
    category: str
    contract_text: str
    timeframe: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_timestamp: datetime
    generated_at: datetime
    bar_confirmed: bool = True
    indicators_supplied: bool = False
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update(value.pop("extra", {}))
        value["bar_timestamp"] = self.bar_timestamp
        value["generated_at"] = self.generated_at
        return value


def parse_payload(data: Any) -> TradingViewPayload:
    if not isinstance(data, Mapping):
        raise PayloadError("payload_must_be_object")
    standard = str(data.get("source", "")).strip().upper() in {"TRADINGVIEW_STANDARD_ALERT", "TRADINGVIEW_STANDARD_ALERT_BRIDGE"}
    allowed = {
        "secret", "source", "event_id", "event_type", "script_version", "symbol",
        "ticker_id", "exchange", "instrument", "category", "contract", "contract_text",
        "timeframe", "interval", *NUMBERS, *BOOLEANS, "chandelier_direction",
        "supertrend_direction", "trend_state", "momentum_state", "structure_state",
        "bar_time", "bar_timestamp", "generated_at", "bar_confirmed",
    }
    if set(data) - allowed:
        raise PayloadError("unknown_fields")

    symbol = _text(data.get("symbol"), "symbol")
    exchange = _text(data.get("exchange"), "exchange")
    category = _text(data.get("category"), "category").upper()
    if category not in CATEGORIES:
        raise PayloadError("invalid_category")
    contract = _text(data.get("contract", data.get("contract_text", "")), "contract", optional=standard)
    interval = data.get("timeframe", data.get("interval", ""))
    try:
        timeframe = int(str(interval).lower().replace("minutes", "").replace("minute", "").replace("m", "").strip())
    except (TypeError, ValueError):
        raise PayloadError("invalid_timeframe") from None
    if timeframe <= 0:
        raise PayloadError("invalid_timeframe")

    numbers = {name: _number(data, name, required=name in CORE_NUMBERS) for name in NUMBERS}
    if min(numbers[name] for name in ("open", "high", "low", "close")) <= 0:
        raise PayloadError("invalid_price")
    if numbers["volume"] < 0:
        raise PayloadError("invalid_volume")
    if numbers["high"] < max(numbers["open"], numbers["low"], numbers["close"]) or numbers["low"] > min(numbers["open"], numbers["high"], numbers["close"]):
        raise PayloadError("impossible_ohlc")

    bar_timestamp = _stamp(data.get("bar_time", data.get("bar_timestamp")), "bar_time")
    generated_at = _stamp(data.get("generated_at", datetime.now(timezone.utc)), "generated_at")
    event_id = _text(data.get("event_id") or f"{symbol}-{timeframe}-{int(bar_timestamp.timestamp())}", "event_id")
    event_type = str(data.get("event_type") or "candle").strip().lower()
    if event_type not in EVENT_TYPES:
        raise PayloadError("invalid_event_type")
    source = str(data.get("source") or "TRADINGVIEW_PINE_ALERT").strip().upper()
    if source not in SOURCES:
        raise PayloadError("invalid_source")
    ticker_id = _text(data.get("ticker_id") or symbol, "ticker_id")
    if ticker_id != symbol:
        raise PayloadError("ticker_symbol_mismatch")
    instrument = _text(data.get("instrument") or symbol, "instrument")
    confirmed = data.get("bar_confirmed", True)
    if confirmed is not True:
        raise PayloadError("bar_must_be_confirmed")

    extras = {name: numbers[name] for name in NUMBERS if name not in CORE_NUMBERS}
    for name in BOOLEANS:
        value = data.get(name, False)
        if not isinstance(value, bool):
            raise PayloadError("invalid_boolean_field")
        extras[name] = value
    extras.update({
        "chandelier_direction": str(data.get("chandelier_direction") or "SIDEWAYS").upper(),
        "supertrend_direction": str(data.get("supertrend_direction") or "SIDEWAYS").upper(),
        "trend_state": str(data.get("trend_state") or "SIDEWAYS").upper(),
        "momentum_state": str(data.get("momentum_state") or "NEUTRAL").upper(),
        "structure_state": str(data.get("structure_state") or "MIXED").upper(),
    })
    if extras["preliminary_buy"] and extras["preliminary_sell"]:
        raise PayloadError("conflicting_directions")
    supplied = not standard and all(name in data for name in ("ema20", "ema50", "ema200", "atr", "rsi", "adx"))
    return TradingViewPayload(
        event_id=event_id, event_type=event_type, script_version=str(data.get("script_version") or "standard-alert-v1"),
        source=source, symbol=symbol, ticker_id=ticker_id, exchange=exchange, instrument=instrument,
        category=category, contract_text=contract, timeframe=timeframe, open=numbers["open"], high=numbers["high"],
        low=numbers["low"], close=numbers["close"], volume=numbers["volume"], bar_timestamp=bar_timestamp,
        generated_at=generated_at, bar_confirmed=True, indicators_supplied=supplied, extra=extras,
    )
