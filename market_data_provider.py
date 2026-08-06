"""Stable, read-only market-data facade for every SHIVAY AI consumer.

Only data that passes the central Indian-instrument and freshness policy is
returned by the verified methods. Yahoo remains available through the
explicit context-only method and can never satisfy this interface for Indian
Futures or MCX contracts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, runtime_checkable

from data import SYMBOLS
from data_quality import assess_market_data
from instrument_master import list_instruments, refresh_instrument_master, resolve_verified_instrument
from provider_manager import get_provider_manager, get_provider_status


@runtime_checkable
class MarketDataProvider(Protocol):
    """Read-only capability contract implemented by provider adapters."""

    def authenticate(self) -> bool: ...
    def get_quote(self, symbol: str) -> dict[str, Any] | None: ...
    def get_ohlc(self, symbol: str) -> dict[str, Any] | None: ...
    def get_historical_candles(self, symbol: str, interval: str = "5m", period: str = "5d") -> list[dict[str, Any]]: ...
    def search_instrument(self, symbol: str) -> dict[str, Any] | None: ...
    def get_instrument_master(self, refresh: bool = False) -> dict[str, Any]: ...
    def health_check(self) -> dict[str, Any]: ...
    def freshness_status(self, symbol: str) -> dict[str, Any]: ...
    def resolve_current_contract(self, symbol: str) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


class VerifiedMarketDataProvider:
    """Fail-closed facade over the existing ranked provider manager."""

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        try:
            value = get_provider_manager().get_verified_market_data(symbol, period="5d", interval="5m")
            result = dict(value)
            result.update(normalize_market_snapshot(value, symbol))
            return result
        except Exception:
            return None

    def get_ohlc(self, symbol: str) -> dict[str, Any] | None:
        value = self.get_quote(symbol)
        if not value:
            return None
        result = {
            "symbol": value.get("symbol", symbol),
            "exchange": value.get("exchange"),
            "contract": value.get("contract") or value.get("trading_symbol"),
            "timestamp": value.get("timestamp"),
            "open": value.get("open_value"),
            "high": value.get("day_high"),
            "low": value.get("day_low"),
            "close": value.get("price"),
            "volume": value.get("latest_volume"),
            "open_interest": value.get("open_interest"),
        }
        return result if all(result.get(key) not in (None, 0, 0.0) for key in ("open", "high", "low", "close")) else None

    def get_historical_candles(self, symbol: str, interval: str = "5m", period: str = "5d") -> list[dict[str, Any]]:
        try:
            value = get_provider_manager().get_verified_market_data(symbol, period=period, interval=interval)
        except Exception:
            return []
        candles = value.get("candles")
        return [dict(item) for item in candles] if isinstance(candles, list) else []

    def search_instrument(self, symbol: str) -> dict[str, Any] | None:
        value = resolve_verified_instrument(symbol)
        return dict(value) if value and value.get("verified") else None

    def get_instrument_master(self, refresh: bool = False) -> dict[str, Any]:
        if refresh:
            return refresh_instrument_master()
        rows = [dict(item) for item in list_instruments() if item and item.get("verified")]
        return {"supported": bool(rows), "count": len(rows), "source": "verified_local", "instruments": rows}

    def health_check(self) -> dict[str, Any]:
        status = get_provider_status()
        return {
            "active_provider": status.get("selected_primary"),
            "verified_providers": list(status.get("verified_providers") or []),
            "context_only_provider": "yahoo_emergency",
            "mode": "VERIFIED" if status.get("selected_primary") else "NO VERIFIED DATA",
            "health": status.get("health", {}),
        }

    def freshness_status(self, symbol: str) -> dict[str, Any]:
        value = self.get_quote(symbol)
        if not value:
            return {"symbol": str(symbol), "status": "NO VERIFIED DATA", "decision": "WAIT", "valid": False}
        quality = assess_market_data(value)
        return {"symbol": str(symbol), "status": quality.get("status"), "decision": "AVAILABLE" if quality.get("valid") else "WAIT", **quality}


_FACADE = VerifiedMarketDataProvider()


def normalize_market_snapshot(data: Mapping[str, Any], requested_symbol: str | None = None,
                              retrieval_time: datetime | None = None) -> dict[str, Any]:
    """Return the stable internal quote contract while preserving provider payloads."""
    retrieved = retrieval_time or datetime.now(timezone.utc)
    exchange_stamp = data.get("exchange_timestamp") or data.get("market_timestamp") or data.get("timestamp")
    if isinstance(exchange_stamp, datetime) and exchange_stamp.tzinfo is None:
        exchange_stamp = exchange_stamp.replace(tzinfo=timezone.utc)
    price = data.get("ltp", data.get("last_price", data.get("price")))
    is_stale = bool(data.get("is_stale"))
    if not price or float(price) <= 0:
        status = "UNAVAILABLE"
    elif is_stale:
        status = "STALE"
    elif data.get("market_state") == "CLOSED" or data.get("freshness_status") == "CLOSED":
        status = "CLOSED"
    elif data.get("is_delayed"):
        status = "DELAYED"
    elif data.get("is_live"):
        status = "LIVE"
    else:
        status = "UNAVAILABLE"
    return {
        "symbol": str(data.get("symbol") or requested_symbol or "").upper(),
        "exchange": data.get("exchange"),
        "contract": data.get("contract") or data.get("trading_symbol"),
        "ltp": float(price) if price not in (None, "") else None,
        "open": data.get("open_value", data.get("open")),
        "high": data.get("day_high", data.get("high")),
        "low": data.get("day_low", data.get("low")),
        "close": data.get("close_value", data.get("previous_close", price)),
        "volume": data.get("latest_volume", data.get("volume")),
        "oi": data.get("open_interest"),
        "bid": data.get("bid"),
        "ask": data.get("ask"),
        "exchange_timestamp": exchange_stamp,
        "retrieval_timestamp": data.get("retrieved_at") or data.get("received_at") or retrieved,
        "status": status,
        "provider_internal": data.get("provider") or data.get("source"),
    }


def get_quote(symbol: str) -> dict[str, Any] | None: return _FACADE.get_quote(symbol)
def get_ohlc(symbol: str) -> dict[str, Any] | None: return _FACADE.get_ohlc(symbol)
def get_historical_candles(symbol: str, interval: str = "5m", period: str = "5d") -> list[dict[str, Any]]: return _FACADE.get_historical_candles(symbol, interval, period)
def search_instrument(symbol: str) -> dict[str, Any] | None: return _FACADE.search_instrument(symbol)
def get_instrument_master(refresh: bool = False) -> dict[str, Any]: return _FACADE.get_instrument_master(refresh)
def health_check() -> dict[str, Any]: return _FACADE.health_check()
def freshness_status(symbol: str) -> dict[str, Any]: return _FACADE.freshness_status(symbol)


def get_market_data(symbol: str) -> dict[str, Any] | None: return get_quote(symbol)
def get_live_price(symbol: str) -> float | None:
    value = get_quote(symbol)
    return round(float(value["price"]), 2) if value else None
def get_emergency_market_context(symbol: str) -> dict[str, Any] | None:
    try: return get_provider_manager().yahoo.get_market_data(symbol)
    except Exception: return None
def refresh_market(symbols: list[str] | None = None) -> dict[str, dict[str, Any]]:
    return get_provider_manager().get_verified_many(symbols or list(SYMBOLS), period="5d", interval="5m")
def get_batch_market_data(symbols: list[str]) -> dict[str, dict[str, Any]]: return refresh_market(symbols)
def force_refresh() -> dict[str, dict[str, Any]]:
    from provider_manager import reset_provider_manager
    reset_provider_manager()
    return {}
def cache_size() -> int:
    return sum(getattr(provider, "cache").status()["entries"] for provider in get_provider_manager().providers if hasattr(provider, "cache"))
def cache_age() -> float: return 0.0 if cache_size() else 99999.0
def provider_status() -> dict[str, Any]: return get_provider_status()


__all__ = [
    "MarketDataProvider", "VerifiedMarketDataProvider", "get_quote", "get_ohlc",
    "get_historical_candles", "search_instrument", "get_instrument_master",
    "health_check", "freshness_status", "get_market_data", "get_live_price",
    "get_emergency_market_context", "refresh_market", "get_batch_market_data",
    "force_refresh", "cache_size", "cache_age", "provider_status", "normalize_market_snapshot",
]
