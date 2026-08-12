"""Read-only TradingView OHLCV adapter backed by the maintained tvkit package."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import site
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

from data_quality import validate_candles
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable


SYMBOLS: dict[str, tuple[str, str, str, str]] = {
    "NIFTY FUT": ("NSE:NIFTY1!", "NSE", "NSE_FNO", "FUTIDX"),
    "BANKNIFTY FUT": ("NSE:BANKNIFTY1!", "NSE", "NSE_FNO", "FUTIDX"),
    "GIFT NIFTY": ("NSEIX:NIFTY1!", "NSEIX", "GIFT_FUT", "FUTIDX"),
    "COMEX GOLD": ("COMEX:GC1!", "COMEX", "GLOBAL_COMMODITY", "FUTCOM"),
    "COMEX SILVER": ("COMEX:SI1!", "COMEX", "GLOBAL_COMMODITY", "FUTCOM"),
    "XAUUSD": ("OANDA:XAUUSD", "OANDA", "GLOBAL_SPOT", "SPOT"),
    "XAGUSD": ("OANDA:XAGUSD", "OANDA", "GLOBAL_SPOT", "SPOT"),
    "USDINR": ("FX_IDC:USDINR", "FX_IDC", "FOREX", "SPOT"),
    "DXY": ("TVC:DXY", "TVC", "INDEX", "INDEX"),
    "NIFTY": ("NSE:NIFTY", "NSE", "INDEX", "INDEX"),
    "BANKNIFTY": ("NSE:BANKNIFTY", "NSE", "INDEX", "INDEX"),
    "MCX GOLD": ("MCX:GOLD1!", "MCX", "MCX_COMM", "FUTCOM"),
    "MCX SILVER": ("MCX:SILVER1!", "MCX", "MCX_COMM", "FUTCOM"),
    "MCX CRUDEOIL": ("MCX:CRUDEOIL1!", "MCX", "MCX_COMM", "FUTCOM"),
    "MCX NATURALGAS": ("MCX:NATURALGAS1!", "MCX", "MCX_COMM", "FUTCOM"),
    "MCX COPPER": ("MCX:COPPER1!", "MCX", "MCX_COMM", "FUTCOM"),
}
SCANNER_URL = "https://scanner.tradingview.com/global/scan"
MONTH_CODES = "FGHJKMNQUVXZ"

if importlib.util.find_spec("tvkit") is None:
    bundled_site = Path(__file__).resolve().parent / ".venv" / "Lib" / "site-packages"
    if bundled_site.is_dir():
        site.addsitedir(str(bundled_site))


def _key(symbol: str) -> str:
    value = str(symbol).strip().upper().replace("GOLD FUT", "MCX GOLD").replace("SILVER FUT", "MCX SILVER")
    return value


class TVKitProvider:
    name = "tvkit_ohlcv"
    signal_capable = False

    def __init__(self) -> None:
        self.cache = ProviderCache(20, 256)
        self._lock = threading.RLock()
        self._session = requests.Session()
        self._contracts: dict[str, tuple[float, dict[str, Any]]] = {}
        self.auth_token = os.getenv("TVKIT_AUTH_TOKEN", "").strip() or None
        try:
            from tvkit.api.chart.ohlcv import OHLCV  # noqa: F401
            self.available = True
        except Exception:
            self.available = False

    @staticmethod
    def _minutes(interval: str | int) -> int:
        text = str(interval).lower().replace("minute", "").replace("m", "").strip()
        value = int(text)
        if value not in {1, 5, 15, 30, 60}:
            raise ValueError("unsupported_tvkit_interval")
        return value

    async def _fetch(self, tv_symbol: str, minutes: int, count: int) -> list[Any]:
        from tvkit.api.chart.ohlcv import OHLCV
        async with OHLCV(auth_token=self.auth_token, max_attempts=5, base_backoff=1.0, max_backoff=20.0) as client:
            return await asyncio.wait_for(
                client.get_historical_ohlcv(exchange_symbol=tv_symbol, interval=str(minutes), bars_count=count),
                timeout=35,
            )

    def resolve_active_contract(self, symbol: str) -> dict[str, Any] | None:
        normalized = _key(symbol)
        mapping = {"COMEX GOLD": ("GC", "COMEX"), "COMEX SILVER": ("SI", "COMEX"),
                   "GIFT NIFTY": ("NIFTY", "NSEIX")}
        selected = mapping.get(normalized)
        if selected is None:
            return None
        root, exchange = selected
        cache_key = f"{exchange}:{root}"
        cached = self._contracts.get(cache_key)
        if cached and time.time() - cached[0] < 3600:
            return dict(cached[1])
        today = date.today()
        tickers: list[str] = []
        for offset in range(13):
            absolute = today.year * 12 + today.month - 1 + offset
            year, month = divmod(absolute, 12)
            tickers.append(f"{exchange}:{root}{MONTH_CODES[month]}{year}")
        columns = ["name", "description", "close", "volume", "open_interest", "update_mode", "exchange", "expiration", "update_time"]
        try:
            response = self._session.post(SCANNER_URL, json={
                "symbols": {"tickers": tickers, "query": {"types": []}}, "columns": columns,
            }, timeout=15)
            response.raise_for_status()
            candidates: list[tuple[float, float, date, dict[str, Any]]] = []
            for row in response.json().get("data") or []:
                values = row.get("d") if isinstance(row, dict) else None
                if not isinstance(values, list) or len(values) != len(columns):
                    continue
                value = dict(zip(columns, values))
                try:
                    expiry = datetime.strptime(str(int(value["expiration"])), "%Y%m%d").date()
                    price = float(value["close"])
                    volume = max(0.0, float(value.get("volume") or 0))
                    oi = max(0.0, float(value.get("open_interest") or 0))
                except (TypeError, ValueError, OverflowError):
                    continue
                if expiry < today or price <= 0 or str(value.get("exchange", "")).upper() != exchange:
                    continue
                raw_update = value.get("update_time")
                stamp = datetime.fromtimestamp(float(raw_update), timezone.utc) if raw_update else None
                item = {"trading_symbol": str(row.get("s") or "").upper(), "expiry": expiry.isoformat(),
                        "exchange": exchange, "price": price, "volume": volume, "open_interest": oi,
                        "market_timestamp": stamp, "update_mode": value.get("update_mode")}
                candidates.append((volume, oi, expiry, item))
            if not candidates:
                return None
            result = max(candidates, key=lambda item: (item[0], item[1], -item[2].toordinal()))[3]
            self._contracts[cache_key] = (time.time(), result)
            return dict(result)
        except (requests.RequestException, TypeError, ValueError, OSError, OverflowError):
            return None

    def get_historical_candles(self, symbol: str, interval: str | int = "15m", days: int = 5) -> list[dict[str, Any]]:
        normalized = _key(symbol)
        item = SYMBOLS.get(normalized)
        if not self.available or not item:
            return []
        contract = self.resolve_active_contract(normalized) if normalized.startswith("COMEX ") or normalized == "GIFT NIFTY" else None
        chart_symbol = contract["trading_symbol"] if contract else item[0]
        minutes = self._minutes(interval)
        count = max(80, min(5000, int(days) * max(30, 390 // minutes)))
        try:
            with self._lock:
                bars = asyncio.run(self._fetch(chart_symbol, minutes, count))
        except Exception:
            return []
        now = datetime.now(timezone.utc)
        candles: list[dict[str, Any]] = []
        for bar in bars or []:
            try:
                raw_stamp = getattr(bar, "timestamp")
                stamp = datetime.fromtimestamp(float(raw_stamp), timezone.utc) if not isinstance(raw_stamp, datetime) else raw_stamp
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                candle = {
                    "timestamp": stamp.astimezone(timezone.utc), "open": float(getattr(bar, "open")),
                    "high": float(getattr(bar, "high")), "low": float(getattr(bar, "low")),
                    "close": float(getattr(bar, "close")), "volume": max(0.0, float(getattr(bar, "volume", 0) or 0)),
                    "open_interest": None, "completed": stamp + timedelta(minutes=minutes) <= now,
                }
            except (AttributeError, TypeError, ValueError, OSError, OverflowError):
                continue
            candles.append(candle)
        candles.sort(key=lambda row: row["timestamp"])
        return candles if candles and validate_candles(candles)["valid"] else []

    def get_market_data(self, symbol: str, period: str = "5d", interval: str = "15m") -> dict[str, Any]:
        normalized = _key(symbol)
        item = SYMBOLS.get(normalized)
        if not item:
            raise ProviderUnavailable("tvkit_symbol_unsupported")
        cache_key = f"{normalized}:{period}:{interval}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        minutes = self._minutes(interval)
        contract = self.resolve_active_contract(normalized) if normalized.startswith("COMEX ") or normalized == "GIFT NIFTY" else None
        candles = self.get_historical_candles(normalized, minutes, 5)
        if not candles:
            raise ProviderUnavailable("tvkit_data_or_permission_unavailable")
        latest = candles[-1]
        age = max(0.0, (datetime.now(timezone.utc) - latest["timestamp"]).total_seconds())
        result = {
            "symbol": normalized, "underlying": normalized.removesuffix(" FUT").replace("MCX ", ""),
            "trading_symbol": contract["trading_symbol"] if contract else item[0], "exchange": item[1], "segment": item[2], "instrument_type": item[3],
            "continuous_contract": contract is None, "expiry": contract.get("expiry") if contract else None,
            "price": latest["close"], "last_price": latest["close"],
            "timestamp": latest["timestamp"], "market_timestamp": latest["timestamp"], "open_value": latest["open"],
            "day_high": max(row["high"] for row in candles[-75:]), "day_low": min(row["low"] for row in candles[-75:]),
            "latest_volume": latest["volume"], "open_interest": None, "oi_change": None, "bid": None, "ask": None,
            "provider": self.name, "source": self.name, "primary_source": self.name,
            "secondary_verification_price": None, "received_at": datetime.now(timezone.utc),
            "freshness_status": "FRESH" if age <= max(180, minutes * 120) else "STALE_OR_CLOSED",
            "verified": True, "is_live": age <= max(180, minutes * 120),
            "is_delayed": age > max(180, minutes * 120), "is_stale": age > max(180, minutes * 120), "data_age": age,
            "candles": candles, "interval_minutes": minutes,
            "open": pd.Series([row["open"] for row in candles]), "high": pd.Series([row["high"] for row in candles]),
            "low": pd.Series([row["low"] for row in candles]), "close": pd.Series([row["close"] for row in candles]),
            "volume": pd.Series([row["volume"] for row in candles]),
        }
        self.cache.set(cache_key, result)
        return result

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        try:
            return self.get_market_data(symbol, interval="1m")
        except Exception:
            return None

    def get_live_price(self, symbol: str) -> float | None:
        value = self.get_quote(symbol)
        return float(value["price"]) if value else None

    def get_many(self, symbols: Iterable[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            try:
                values[str(symbol)] = self.get_market_data(str(symbol), **kwargs)
            except Exception:
                continue
        return values

    def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "available": self.available, "authenticated": bool(self.auth_token),
                "websocket_supported": True, "historical_supported": True, "orders_enabled": False}

    def close(self) -> None:
        self._session.close()


__all__ = ["TVKitProvider", "SYMBOLS"]
