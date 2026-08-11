"""Angel One SmartAPI market-data provider; deliberately contains no order APIs."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pyotp
import requests
from SmartApi import SmartConnect

from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable

_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
_INTERVALS = {"1m": "ONE_MINUTE", "3m": "THREE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE", "30m": "THIRTY_MINUTE", "60m": "ONE_HOUR", "1h": "ONE_HOUR", "1d": "ONE_DAY"}


class AngelOneProvider:
    name = "angelone_primary"
    signal_capable = True
    _master: list[dict[str, Any]] | None = None
    _master_at = 0.0
    _lock = threading.RLock()

    def __init__(self) -> None:
        self.api_key = os.getenv("ANGEL_API_KEY", "").strip()
        self.client_code = os.getenv("ANGEL_CLIENT_CODE", "").strip()
        self.pin = os.getenv("ANGEL_PIN", "").strip()
        self.totp_secret = os.getenv("ANGEL_TOTP_SECRET", "").replace(" ", "").strip()
        self.enabled = os.getenv("ENABLE_ANGELONE", "false").strip().lower() in {"1", "true", "yes"}
        self.cache = ProviderCache(20, 512)
        self.client: Any = None
        self.session_at = 0.0

    @property
    def configured(self) -> bool:
        return self.enabled and all((self.api_key, self.client_code, self.pin, self.totp_secret))

    @property
    def available(self) -> bool:
        return self.configured

    def _session(self) -> Any:
        if not self.configured:
            raise ProviderUnavailable("angelone_not_configured")
        with self._lock:
            if self.client is not None and time.monotonic() - self.session_at < 7200:
                return self.client
            try:
                client = SmartConnect(api_key=self.api_key)
                response = client.generateSession(self.client_code, self.pin, pyotp.TOTP(self.totp_secret).now())
                if not isinstance(response, dict) or not response.get("status"):
                    raise ProviderUnavailable("angelone_login_failed")
                self.client, self.session_at = client, time.monotonic()
                return client
            except ProviderUnavailable:
                raise
            except Exception as error:
                raise ProviderUnavailable("angelone_login_error:" + type(error).__name__) from error

    @classmethod
    def _instruments(cls) -> list[dict[str, Any]]:
        with cls._lock:
            if cls._master is not None and time.monotonic() - cls._master_at < 21600:
                return cls._master
            try:
                response = requests.get(_MASTER_URL, timeout=20)
                response.raise_for_status()
                values = response.json()
                if not isinstance(values, list):
                    raise ValueError("invalid_master")
                cls._master, cls._master_at = values, time.monotonic()
                return values
            except Exception as error:
                raise ProviderUnavailable("angelone_master_unavailable:" + type(error).__name__) from error

    def resolve_instrument(self, symbol: str) -> dict[str, Any] | None:
        key = str(symbol).strip().upper().replace(".NS", "")
        values = self._instruments()
        today = datetime.now(timezone.utc).date()
        futures = key.endswith(" FUT")
        root = key.removesuffix(" FUT").replace("BANK NIFTY", "BANKNIFTY")
        candidates = []
        for item in values:
            exchange = str(item.get("exch_seg", "")).upper()
            name = str(item.get("name", "")).upper().replace(" ", "")
            tradingsymbol = str(item.get("symbol", "")).upper()
            if futures:
                if exchange != "NFO" or str(item.get("instrumenttype", "")).upper() not in {"FUTIDX", "FUTSTK"}:
                    continue
                if name != root.replace(" ", ""):
                    continue
                try:
                    expiry = datetime.strptime(str(item.get("expiry", "")), "%d%b%Y").date()
                except ValueError:
                    continue
                if expiry >= today:
                    candidates.append((expiry, item))
            elif exchange == "NSE" and (tradingsymbol == key or name == root.replace(" ", "")):
                return self._normalise_instrument(key, item)
        if candidates:
            return self._normalise_instrument(key, min(candidates, key=lambda pair: pair[0])[1])
        return None

    @staticmethod
    def _normalise_instrument(symbol: str, item: dict[str, Any]) -> dict[str, Any]:
        return {"symbol": symbol, "trading_symbol": str(item.get("symbol", "")), "exchange": str(item.get("exch_seg", "")), "segment": str(item.get("exch_seg", "")), "instrument_type": str(item.get("instrumenttype", "")), "token": str(item.get("token", "")), "expiry": item.get("expiry"), "lot_size": int(float(item.get("lotsize") or 0)) or None, "verified": True}

    def get_market_data(self, symbol: str, period: str = "1mo", interval: str = "15m") -> dict[str, Any]:
        key = f"{symbol}:{interval}"
        cached = self.cache.get(key)
        if cached:
            return cached
        instrument = self.resolve_instrument(symbol)
        if not instrument or not instrument.get("token"):
            raise ProviderUnavailable("angelone_instrument_not_found")
        api_interval = _INTERVALS.get(str(interval).lower())
        if not api_interval:
            raise ProviderUnavailable("angelone_unsupported_interval")
        client = self._session()
        now = datetime.now()
        request = {"exchange": instrument["exchange"], "symboltoken": instrument["token"], "interval": api_interval, "fromdate": (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M"), "todate": now.strftime("%Y-%m-%d %H:%M")}
        try:
            response = client.getCandleData(request)
            rows = response.get("data", []) if isinstance(response, dict) else []
            candles = [{"timestamp": datetime.fromisoformat(str(row[0]).replace("Z", "+00:00")), "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5] or 0)} for row in rows if len(row) >= 6]
            if not candles:
                raise ProviderUnavailable("angelone_empty_candles")
            latest = candles[-1]
            try:
                quote = client.ltpData(instrument["exchange"], instrument["trading_symbol"], instrument["token"])
                price = float(quote.get("data", {}).get("ltp") or latest["close"])
            except Exception:
                price = latest["close"]
            minutes = {"ONE_MINUTE": 1, "THREE_MINUTE": 3, "FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15, "THIRTY_MINUTE": 30, "ONE_HOUR": 60, "ONE_DAY": 1440}[api_interval]
            result = {**instrument, "price": price, "open": pd.Series([x["open"] for x in candles]), "high": pd.Series([x["high"] for x in candles]), "low": pd.Series([x["low"] for x in candles]), "close": pd.Series([x["close"] for x in candles]), "volume": pd.Series([x["volume"] for x in candles]), "open_value": latest["open"], "latest_volume": latest["volume"], "day_high": max(x["high"] for x in candles[-75:]), "day_low": min(x["low"] for x in candles[-75:]), "timestamp": latest["timestamp"], "provider": self.name, "is_live": True, "is_delayed": False, "is_stale": False, "candles": candles, "interval_minutes": minutes}
            self.cache.set(key, result)
            return result
        except ProviderUnavailable:
            raise
        except Exception as error:
            self.client = None
            raise ProviderUnavailable("angelone_market_data_error:" + type(error).__name__) from error

    def get_many(self, symbols: list[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        result = {}
        for symbol in symbols:
            try:
                result[symbol] = self.get_market_data(symbol, **kwargs)
            except Exception:
                continue
        return result

    def get_live_price(self, symbol: str) -> float | None:
        return round(float(self.get_market_data(symbol)["price"]), 2)

    def health_check(self) -> dict[str, Any]:
        return {"configured": self.configured, "available": self.available, "mode": "MARKET_DATA_ONLY"}

    def close(self) -> bool:
        self.client = None
        return True
