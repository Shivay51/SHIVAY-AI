"""Normalized read-only TrueData adapter for an official provider-issued trial.

No endpoint is guessed: contract discovery uses only a provider-issued catalog
stored in ``cache/truedata_instruments.json`` and market calls delegate to the
official SDK wrapper in :mod:`truedata_client`.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from data_quality import assess_market_data, validate_candles, validate_instrument_contract
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable
from truedata_client import TrueDataClient, TrueDataError


CATALOG = Path(__file__).resolve().parent / "cache" / "truedata_instruments.json"
IST = ZoneInfo("Asia/Kolkata")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _field(value: Mapping[str, Any], *names: str) -> Any:
    folded = {str(key).strip().lower().replace(" ", "_"): item for key, item in value.items()}
    for name in names:
        key = name.lower().replace(" ", "_")
        if key in folded and folded[key] not in (None, ""):
            return folded[key]
    return None


class TrueDataProvider:
    name = "truedata_primary"

    def __init__(self, client: TrueDataClient | None = None):
        self.client = client or TrueDataClient()
        self.cache = ProviderCache(15, 256)
        self._catalog = self._load_catalog()
        self.available = bool(self.client.configured and self._catalog)

    @staticmethod
    def _load_catalog() -> list[dict[str, Any]]:
        try:
            document = json.loads(CATALOG.read_text(encoding="utf-8"))
            rows = document if isinstance(document, list) else document.get("instruments", [])
            return [dict(row) for row in rows if isinstance(row, dict)]
        except (OSError, ValueError, TypeError):
            return []

    def authenticate(self) -> bool:
        try:
            return bool(self.client.connect())
        except TrueDataError:
            return False

    def health_check(self) -> dict[str, Any]:
        health = self.client.health()
        state = (
            "UNSUPPORTED" if not health.get("sdk_installed")
            else "NO_DATA" if not health.get("configured")
            else "AUTHENTICATING" if not health.get("authenticated")
            else "MAPPING" if not self._catalog
            else "READY"
        )
        return {
            **health,
            "provider": self.name,
            "catalog_available": bool(self._catalog),
            "catalog_count": len(self._catalog),
            "available": self.available,
            "state": state,
        }

    def get_instrument_master(self, refresh: bool = False) -> dict[str, Any]:
        if refresh:
            self._catalog = self._load_catalog()
            self.available = bool(self.client.configured and self._catalog)
        return {
            "supported": bool(self._catalog),
            "count": len(self._catalog),
            "source": self.name if self._catalog else None,
            "instruments": [dict(row) for row in self._catalog],
        }

    def resolve_current_contract(self, symbol: str) -> dict[str, Any] | None:
        key = str(symbol or "").strip().upper()
        today = date.today()
        matches: list[tuple[date, dict[str, Any]]] = []
        for raw in self._catalog:
            row = {str(name).lower(): value for name, value in raw.items()}
            try:
                expiry = date.fromisoformat(str(row["expiry"])[:10])
                identifier = str(row.get("instrument_id") or row.get("instrument_key") or row.get("trading_symbol") or "").strip()
                trading_symbol = str(row.get("trading_symbol") or identifier).strip()
                lot_size = _number(row.get("lot_size"))
                tick_size = _number(row.get("tick_size"))
                if str(row.get("symbol", "")).upper() != key or expiry < today:
                    continue
                if not identifier or not trading_symbol or not lot_size or lot_size <= 0 or not tick_size or tick_size <= 0:
                    continue
                normalized = {
                    **row,
                    "symbol": key,
                    "instrument_id": identifier,
                    "instrument_key": identifier,
                    "security_id": str(row.get("security_id") or identifier),
                    "trading_symbol": trading_symbol,
                    "expiry": expiry.isoformat(),
                    "lot_size": lot_size,
                    "tick_size": tick_size,
                    "provider": self.name,
                    "verified": True,
                }
                if not validate_instrument_contract(normalized, key):
                    matches.append((expiry, normalized))
            except (KeyError, TypeError, ValueError):
                continue
        return min(matches, key=lambda item: item[0])[1] if matches else None

    def resolve_instrument(self, symbol: str) -> dict[str, Any] | None:
        return self.resolve_current_contract(symbol)

    def search_instrument(self, query: str) -> list[dict[str, Any]]:
        term = str(query or "").strip().upper()
        if not term:
            return []
        results = []
        for row in self._catalog:
            text = " ".join(str(row.get(key, "")) for key in ("symbol", "trading_symbol", "underlying")).upper()
            if term in text:
                value = self.resolve_current_contract(str(row.get("symbol", "")))
                if value and value not in results:
                    results.append(value)
        return results

    def _candles(self, identifier: str, interval_minutes: int, bars: int = 240) -> list[dict[str, Any]]:
        frame = self.client.history(identifier, interval_minutes, bars)
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame(frame)
        frame.columns = [str(column).strip().lower() for column in frame.columns]
        candles: list[dict[str, Any]] = []
        for index, row in frame.iterrows():
            try:
                stamp = pd.to_datetime(row.get("timestamp", row.get("datetime", index)), utc=True).to_pydatetime()
                candle = {
                    "timestamp": stamp,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": max(0.0, float(row.get("volume", row.get("vol", 0)) or 0)),
                    "open_interest": _number(row.get("open_interest", row.get("oi"))),
                    "completed": True,
                }
                candles.append(candle)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        candles.sort(key=lambda item: item["timestamp"])
        quality = validate_candles(candles)
        if not quality["valid"]:
            raise ProviderUnavailable("truedata_invalid_candles")
        return candles

    def get_historical_candles(self, symbol: str, interval: str | int = "5m", bars: int = 240) -> list[dict[str, Any]]:
        instrument = self.resolve_current_contract(symbol)
        if not instrument:
            return []
        try:
            minutes = int(str(interval).lower().replace("min", "").replace("m", ""))
        except ValueError:
            return []
        if minutes not in {5, 15, 30, 60}:
            return []
        return self._candles(instrument["instrument_id"], minutes, bars)

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        instrument = self.resolve_current_contract(symbol)
        if not instrument:
            return None
        raw = self.client.quote(instrument["instrument_id"])
        price = _number(_field(raw, "ltp", "last_price", "price"))
        timestamp = pd.to_datetime(_field(raw, "timestamp", "last_trade_time", "ltt"), utc=True, errors="coerce")
        if not price or price <= 0 or pd.isna(timestamp):
            return None
        result = {
            "symbol": instrument["symbol"],
            "exchange": instrument.get("exchange"),
            "segment": instrument.get("segment"),
            "instrument_type": instrument.get("instrument_type"),
            "instrument_id": instrument["instrument_id"],
            "instrument_key": instrument["instrument_key"],
            "security_id": instrument["security_id"],
            "trading_symbol": instrument["trading_symbol"],
            "underlying": instrument.get("underlying"),
            "expiry": instrument["expiry"],
            "lot_size": instrument["lot_size"],
            "tick_size": instrument["tick_size"],
            "timestamp": timestamp.to_pydatetime(),
            "last_price": price,
            "price": price,
            "open": _number(_field(raw, "open", "day_open")),
            "high": _number(_field(raw, "high", "day_high")),
            "low": _number(_field(raw, "low", "day_low")),
            "previous_close": _number(_field(raw, "previous_close", "prev_close", "close")),
            "volume": _number(_field(raw, "volume", "total_volume")),
            "open_interest": _number(_field(raw, "open_interest", "oi")),
            "bid": _number(_field(raw, "bid", "best_bid", "bid_price")),
            "ask": _number(_field(raw, "ask", "best_ask", "ask_price")),
            "source": self.name,
            "provider": self.name,
            "verified": True,
            "is_live": True,
            "is_delayed": False,
        }
        quality_input = {
            **result,
            "open_value": result["open"], "day_high": result["high"], "day_low": result["low"],
            "latest_volume": result["volume"] if result["volume"] is not None else 0,
        }
        quality = assess_market_data(quality_input)
        result["freshness"] = quality
        return result if quality["valid"] else None

    def get_ohlc(self, symbol: str) -> dict[str, Any] | None:
        quote = self.get_quote(symbol)
        if not quote:
            return None
        return {key: quote.get(key) for key in ("symbol", "exchange", "segment", "instrument_id", "trading_symbol", "expiry", "timestamp", "open", "high", "low", "last_price", "previous_close", "volume", "open_interest", "source", "freshness")}

    def freshness_status(self, symbol: str) -> dict[str, Any]:
        quote = self.get_quote(symbol)
        if not quote:
            return {"symbol": symbol, "status": "NO_DATA", "valid": False}
        quality = dict(quote.get("freshness") or {})
        return {"symbol": symbol, "status": "READY" if quality.get("valid") else "STALE" if quality.get("is_stale") else "DEGRADED", **quality}

    def get_market_data(self, symbol: str, period: str = "1mo", interval: str = "5m") -> dict[str, Any]:
        quote = self.get_quote(symbol)
        candles = self.get_historical_candles(symbol, interval, 240)
        if not quote or not candles:
            raise ProviderUnavailable("truedata_verified_data_unavailable")
        latest = candles[-1]
        result = {
            **quote,
            "price": quote["last_price"],
            "open_value": quote.get("open") or latest["open"],
            "day_high": quote.get("high") or max(item["high"] for item in candles[-75:]),
            "day_low": quote.get("low") or min(item["low"] for item in candles[-75:]),
            "latest_volume": quote.get("volume") if quote.get("volume") is not None else latest["volume"],
            "candles": candles,
            "interval_minutes": int(str(interval).lower().replace("m", "")),
            "open": pd.Series([item["open"] for item in candles]),
            "high": pd.Series([item["high"] for item in candles]),
            "low": pd.Series([item["low"] for item in candles]),
            "close": pd.Series([item["close"] for item in candles]),
            "volume": pd.Series([item["volume"] for item in candles]),
        }
        quality = assess_market_data(result)
        if not quality["valid"]:
            raise ProviderUnavailable("truedata_data_rejected")
        result.update(delay_seconds=quality["delay_seconds"], is_stale=quality["is_stale"], freshness_status=quality["status"])
        return result

    def get_many(self, symbols: Iterable[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        result = {}
        for symbol in symbols:
            try:
                result[symbol] = self.get_market_data(symbol, **kwargs)
            except Exception:
                continue
        return result

    def get_live_price(self, symbol: str) -> float | None:
        quote = self.get_quote(symbol)
        return float(quote["last_price"]) if quote else None

    def close(self) -> None:
        self.cache.invalidate()
        self.client.close()


__all__ = ["TrueDataProvider", "CATALOG"]
