"""Replaceable read-only MCX chart-data adapter.

This uses the requested vendored tvdatafeed implementation.  TradingView does
not grant anonymous MCX permission consistently, so failures are explicit and
the provider never substitutes COMEX for Indian MCX.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from data_quality import validate_candles
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor" / "tvdatafeed"
IST = ZoneInfo("Asia/Kolkata")
SYMBOLS = {"GOLD": "GOLD", "SILVER": "SILVER", "CRUDEOIL": "CRUDEOIL", "NATURALGAS": "NATURALGAS", "COPPER": "COPPER"}
SCANNER_URL = "https://scanner.tradingview.com/global/scan"
SCANNER_COLUMNS = ["name", "description", "close", "open", "high", "low", "volume", "open_interest", "update_mode", "currency", "exchange", "type", "subtype", "expiration", "update_time"]
MONTH_CODES = "FGHJKMNQUVXZ"


class MCXTemporaryProvider:
    name = "mcx_temporary"
    signal_capable = False

    def __init__(self):
        self.enabled = os.getenv("ENABLE_MCX_TEMPORARY", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.cache = ProviderCache(20, 128)
        self._lock = threading.RLock()
        self._feed = None
        self._interval = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0", "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"})
        self._contracts: dict[str, tuple[float, dict[str, Any]]] = {}
        self.available = bool(self.enabled and VENDOR.exists())

    def _ensure_feed(self) -> bool:
        if self._feed is not None and self._interval is not None:
            return True
        if self.available:
            try:
                if str(VENDOR) not in sys.path:
                    sys.path.insert(0, str(VENDOR))
                from tvDatafeed import Interval, TvDatafeed
                self._feed, self._interval = TvDatafeed(), Interval
                return True
            except Exception:
                self.available = False
        return False

    @staticmethod
    def _underlying(symbol: str) -> str | None:
        key = str(symbol).upper().replace("MCX", "").replace(" FUT", "").replace(" ", "")
        return key if key in SYMBOLS else None

    def get_historical_candles(self, symbol: str, interval: str | int = "15m", days: int = 5) -> list[dict[str, Any]]:
        underlying = self._underlying(symbol)
        if not self.available or not underlying or not self._ensure_feed():
            return []
        key = str(interval).lower().replace("minute", "").replace("m", "")
        interval_map = {
            "5": self._interval.in_5_minute, "15": self._interval.in_15_minute,
            "30": self._interval.in_30_minute, "60": self._interval.in_1_hour,
        }
        selected = interval_map.get(key)
        if selected is None:
            return []
        with self._lock:
            frame = self._feed.get_hist(underlying, "MCX", selected, max(80, min(500, int(days) * 50)), fut_contract=1)
        if frame is None or frame.empty:
            return []
        candles = []
        for index, row in frame.sort_index().iterrows():
            stamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
            if not isinstance(stamp, datetime):
                continue
            stamp = stamp.replace(tzinfo=IST) if stamp.tzinfo is None else stamp
            candles.append({
                "timestamp": stamp.astimezone(timezone.utc), "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"]),
                "volume": max(0.0, float(row.get("volume", 0) or 0)), "open_interest": None, "completed": True,
            })
        return candles if validate_candles(candles)["valid"] else []

    def get_market_data(self, symbol: str, period: str = "5d", interval: str = "15m") -> dict[str, Any]:
        key = f"{symbol}:{period}:{interval}"
        cached = self.cache.get(key)
        if cached:
            return cached
        underlying = self._underlying(symbol)
        candles = self.get_historical_candles(symbol, interval, 5)
        if not underlying or not candles:
            raise ProviderUnavailable("mcx_tradingview_permission_or_data_unavailable")
        latest = candles[-1]
        age = max(0.0, (datetime.now(timezone.utc) - latest["timestamp"]).total_seconds())
        # A continuous chart is not proof of the exact exchange contract.  It is
        # useful context, but strict verification prevents it authorizing trades.
        result = {
            "symbol": f"MCX {underlying}", "underlying": underlying, "exchange": "MCX", "segment": "MCX_COMM",
            "instrument_type": "FUTCOM", "trading_symbol": f"MCX:{underlying}1!", "continuous_contract": True,
            "expiry": None, "security_id": None, "instrument_key": None, "lot_size": None, "tick_size": None,
            "price": latest["close"], "last_price": latest["close"], "timestamp": latest["timestamp"],
            "open_value": latest["open"], "day_high": max(x["high"] for x in candles[-75:]),
            "day_low": min(x["low"] for x in candles[-75:]), "latest_volume": latest["volume"],
            "open_interest": None, "oi_change": None, "bid": None, "ask": None,
            "provider": self.name, "source": self.name, "verified": False, "is_live": True,
            "is_delayed": True, "data_age": age, "is_stale": age > 300, "candles": candles,
            "interval_minutes": int(str(interval).lower().replace("m", "")),
            "open": pd.Series([x["open"] for x in candles]), "high": pd.Series([x["high"] for x in candles]),
            "low": pd.Series([x["low"] for x in candles]), "close": pd.Series([x["close"] for x in candles]),
            "volume": pd.Series([x["volume"] for x in candles]),
        }
        self.cache.set(key, result)
        return result

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        underlying = self._underlying(symbol)
        if not underlying or not self.enabled:
            return None
        contract = self.resolve_active_contract(underlying)
        if not contract:
            return None
        ticker = contract["trading_symbol"]
        try:
            response = self._session.post(SCANNER_URL, json={
                "symbols": {"tickers": [ticker], "query": {"types": []}},
                "columns": SCANNER_COLUMNS,
            }, timeout=12)
            response.raise_for_status()
            rows = response.json().get("data") or []
            values = rows[0].get("d") if rows and isinstance(rows[0], dict) else None
            if not isinstance(values, list) or len(values) != len(SCANNER_COLUMNS):
                return None
            data = dict(zip(SCANNER_COLUMNS, values))
            price = float(data["close"])
            if price <= 0 or str(data.get("exchange", "")).upper() != "MCX":
                return None
            retrieved = datetime.now(timezone.utc)
            raw_update = data.get("update_time")
            try:
                market_timestamp = datetime.fromtimestamp(float(raw_update), timezone.utc) if raw_update else None
            except (TypeError, ValueError, OSError, OverflowError):
                market_timestamp = None
            age = max(0.0, (retrieved - market_timestamp).total_seconds()) if market_timestamp else None
            return {
                "symbol": f"MCX {underlying}", "underlying": underlying, "exchange": "MCX",
                "segment": "MCX_COMM", "instrument_type": "FUTCOM", "trading_symbol": ticker,
                "continuous_contract": False, "expiry": contract["expiry"],
                "contract_description": contract.get("description"),
                "security_id": ticker, "instrument_key": ticker,
                "last_price": price, "current_price": price, "price": price,
                "open": float(data["open"]) if data.get("open") is not None else None,
                "high": float(data["high"]) if data.get("high") is not None else None,
                "low": float(data["low"]) if data.get("low") is not None else None,
                "volume": float(data["volume"]) if data.get("volume") is not None else None,
                "open_interest": float(data["open_interest"]) if data.get("open_interest") is not None else None,
                "oi_change": None, "bid": None, "ask": None, "currency": data.get("currency"),
                "timestamp": market_timestamp or retrieved, "retrieved_at": retrieved, "market_timestamp": market_timestamp,
                "timestamp_kind": "exchange_update_time" if market_timestamp else "retrieval_time",
                "data_age": age, "is_stale": age is None or age > 180,
                "primary_source": self.name, "secondary_verification_price": None,
                "received_at": retrieved, "freshness_status": "FRESH" if age is not None and age <= 180 else "STALE",
                "stale_status": "FRESH" if age is not None and age <= 180 else "STALE", "update_mode": data.get("update_mode"),
                "provider": self.name, "source": self.name, "verified": False,
                "is_live": data.get("update_mode") == "streaming" and age is not None and age <= 180,
                "is_delayed": data.get("update_mode") != "streaming" or age is None or age > 180,
                "quote_only": True, "safe_for_signals": False,
            }
        except (requests.RequestException, ValueError, TypeError, KeyError):
            return None

    def resolve_active_contract(self, underlying: str) -> dict[str, Any] | None:
        cached = self._contracts.get(underlying)
        if cached and time.time() - cached[0] < 3600:
            return dict(cached[1])
        today = datetime.now(IST).date()
        tickers = []
        for offset in range(9):
            absolute = today.year * 12 + today.month - 1 + offset
            year, month = divmod(absolute, 12)
            month += 1
            tickers.append(f"MCX:{underlying}{MONTH_CODES[month - 1]}{year}")
        try:
            columns = ["name", "description", "close", "volume", "open_interest", "update_mode", "exchange", "expiration"]
            response = self._session.post(SCANNER_URL, json={
                "symbols": {"tickers": tickers, "query": {"types": []}}, "columns": columns,
            }, timeout=12)
            response.raise_for_status()
            candidates = []
            for row in response.json().get("data") or []:
                values = row.get("d") if isinstance(row, dict) else None
                if not isinstance(values, list) or len(values) != len(columns):
                    continue
                value = dict(zip(columns, values))
                try:
                    expiration = datetime.strptime(str(int(value["expiration"])), "%Y%m%d").date()
                    price = float(value["close"])
                except (TypeError, ValueError, OverflowError):
                    continue
                if expiration < today or price <= 0 or str(value.get("exchange", "")).upper() != "MCX":
                    continue
                candidates.append((expiration, {
                    "trading_symbol": str(row.get("s") or f"MCX:{value['name']}").upper(),
                    "name": value["name"], "description": value.get("description"),
                    "expiry": expiration.isoformat(), "reference_price": price,
                    "volume": value.get("volume"), "open_interest": value.get("open_interest"),
                    "update_mode": value.get("update_mode"),
                }))
            if not candidates:
                return None
            result = min(candidates, key=lambda item: item[0])[1]
            self._contracts[underlying] = (time.time(), result)
            return dict(result)
        except (requests.RequestException, ValueError, TypeError, KeyError):
            return None

    def get_ohlc(self, symbol: str) -> dict[str, Any] | None:
        return self.get_quote(symbol)

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

    def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "available": self.available, "anonymous_mcx_verified": False,
                "continuous_contract_only": True, "quote_fallback": "tradingview_global_scanner",
                "market_timestamp_available": False, "orders_enabled": False}

    def close(self) -> None:
        self._session.close()


__all__ = ["MCXTemporaryProvider"]
