"""Read-only Upstox market-data adapter using documented public/API endpoints."""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

import requests
import pandas as pd

from data_quality import assess_market_data, validate_candles, validate_instrument_contract
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable

ROOT = Path(__file__).resolve().parent
MASTER_PATH = ROOT / "cache" / "upstox_instruments.json"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
API_ROOT = "https://api.upstox.com"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value: Any) -> datetime | None:
    try:
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            number = float(value)
            return datetime.fromtimestamp(number / 1000 if number > 10_000_000_000 else number, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class UpstoxProvider:
    name = "upstox_primary"

    def __init__(self, session: requests.Session | None = None):
        self.enabled = os.getenv("ENABLE_UPSTOX", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
        self.client_id = os.getenv("UPSTOX_API_KEY", "").strip()
        self.client_secret = os.getenv("UPSTOX_API_SECRET", "").strip()
        self.redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "").strip()
        self.session = session or requests.Session()
        self.cache = ProviderCache(15, 512)
        self._lock = threading.RLock()
        self._last_request = 0.0
        self._master = self._load_master()
        self.available = bool(self.enabled and self.access_token and self._master)

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.access_token)

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {self.access_token}"}

    def _request(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise ProviderUnavailable("upstox_not_configured")
        with self._lock:
            remaining = 0.12 - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request = time.monotonic()
        response = self.session.get(f"{API_ROOT}{path}", params=params, headers=self._headers(), timeout=10)
        if response.status_code in {401, 403}:
            raise PermissionError("upstox_authentication_rejected")
        if response.status_code == 429:
            raise ProviderUnavailable("upstox_rate_limited")
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict) or str(value.get("status", "success")).lower() != "success":
            raise ProviderUnavailable("upstox_invalid_response")
        return value

    def authenticate(self) -> bool:
        if not self.configured:
            return False
        try:
            return isinstance(self._request("/v2/user/profile").get("data"), dict)
        except Exception:
            return False

    @staticmethod
    def _load_master() -> list[dict[str, Any]]:
        try:
            value = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
            return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []
        except (OSError, ValueError):
            return []

    def refresh_instrument_master(self) -> dict[str, Any]:
        response = self.session.get(MASTER_URL, timeout=20, headers={"Accept": "application/gzip,application/json"})
        response.raise_for_status()
        raw = gzip.decompress(response.content)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, list) or not value:
            raise ProviderUnavailable("upstox_empty_instrument_master")
        MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = MASTER_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, MASTER_PATH)
        self._master = [dict(row) for row in value if isinstance(row, dict)]
        self.available = bool(self.configured and self._master)
        return {"supported": True, "count": len(self._master), "source": self.name}

    def get_instrument_master(self, refresh: bool = False) -> dict[str, Any]:
        if refresh:
            return self.refresh_instrument_master()
        return {"supported": bool(self._master), "count": len(self._master), "source": self.name if self._master else None}

    @staticmethod
    def _wanted(symbol: str) -> tuple[str, str]:
        key = str(symbol).strip().upper().replace("_", " ")
        if key in {"MCX GOLD", "GOLD"}: return "GOLD", "MCX_FO"
        if key in {"MCX SILVER", "SILVER"}: return "SILVER", "MCX_FO"
        return key.removesuffix(" FUT"), "NSE_FO"

    def resolve_current_contract(self, symbol: str) -> dict[str, Any] | None:
        underlying, segment = self._wanted(symbol)
        today = date.today()
        matches = []
        for row in self._master:
            if str(row.get("segment", "")).upper() != segment or str(row.get("instrument_type", "")).upper() != "FUT":
                continue
            candidate = str(row.get("underlying_symbol") or row.get("name") or "").upper().replace(" ", "")
            if candidate != underlying.replace(" ", ""):
                continue
            stamp = _timestamp(row.get("expiry"))
            if not stamp or stamp.date() < today:
                continue
            instrument_key = str(row.get("instrument_key") or "")
            lot, tick = _number(row.get("lot_size")), _number(row.get("tick_size"))
            if not instrument_key or not lot or not tick:
                continue
            kind = "FUTCOM" if segment == "MCX_FO" else "FUTIDX" if underlying in {"NIFTY", "BANKNIFTY"} else "FUTSTK"
            normalized = {
                "symbol": str(symbol).strip().upper(), "underlying": underlying,
                "exchange": "MCX" if segment == "MCX_FO" else "NSE",
                "segment": "MCX_COMM" if segment == "MCX_FO" else "NSE_FNO",
                "instrument_type": kind, "instrument_id": instrument_key,
                "instrument_key": instrument_key, "security_id": str(row.get("exchange_token") or ""),
                "trading_symbol": str(row.get("trading_symbol") or ""),
                "expiry": stamp.date().isoformat(), "lot_size": lot, "tick_size": tick,
                "provider": self.name, "verified": True,
            }
            if not validate_instrument_contract(normalized, normalized["symbol"]):
                matches.append((stamp, normalized))
        return min(matches, key=lambda item: item[0])[1] if matches else None

    def resolve_instrument(self, symbol: str) -> dict[str, Any] | None: return self.resolve_current_contract(symbol)
    def search_instrument(self, symbol: str) -> list[dict[str, Any]]:
        result = self.resolve_current_contract(symbol)
        return [result] if result else []

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        instrument = self.resolve_current_contract(symbol)
        if not instrument:
            return None
        payload = self._request("/v2/market-quote/quotes", params={"instrument_key": instrument["instrument_key"]})
        rows = payload.get("data") or {}
        raw = next(iter(rows.values()), None) if isinstance(rows, dict) else None
        if not isinstance(raw, dict): return None
        ohlc = raw.get("ohlc") if isinstance(raw.get("ohlc"), dict) else {}
        depth = raw.get("depth") if isinstance(raw.get("depth"), dict) else {}
        buys, sells = depth.get("buy") or [], depth.get("sell") or []
        timestamp = _timestamp(raw.get("timestamp") or raw.get("last_trade_time"))
        result = {
            **instrument, "timestamp": timestamp, "last_price": _number(raw.get("last_price")),
            "price": _number(raw.get("last_price")), "open": _number(ohlc.get("open")),
            "high": _number(ohlc.get("high")), "low": _number(ohlc.get("low")),
            "previous_close": _number(ohlc.get("close")), "volume": _number(raw.get("volume")),
            "open_interest": _number(raw.get("oi")),
            "bid": _number(buys[0].get("price")) if buys and isinstance(buys[0], dict) else None,
            "ask": _number(sells[0].get("price")) if sells and isinstance(sells[0], dict) else None,
            "source": self.name, "is_live": True, "is_delayed": False,
        }
        quality = assess_market_data({**result, "open_value": result["open"], "day_high": result["high"], "day_low": result["low"], "latest_volume": result["volume"] or 0})
        result["freshness"] = quality
        return result if quality["valid"] else None

    def get_ohlc(self, symbol: str) -> dict[str, Any] | None: return self.get_quote(symbol)

    def get_historical_candles(self, symbol: str, interval: str | int = "5m", days: int = 10) -> list[dict[str, Any]]:
        instrument = self.resolve_current_contract(symbol)
        if not instrument: return []
        try: minutes = int(str(interval).lower().replace("m", ""))
        except ValueError: return []
        if minutes not in {5, 15, 30, 60}: return []
        to_date, from_date = date.today(), date.today() - timedelta(days=max(2, min(days, 30)))
        key = quote(instrument["instrument_key"], safe="")
        payload = self._request(f"/v3/historical-candle/{key}/minutes/{minutes}/{to_date.isoformat()}/{from_date.isoformat()}")
        values = ((payload.get("data") or {}).get("candles") or [])
        candles = []
        for row in values:
            if not isinstance(row, (list, tuple)) or len(row) < 6: continue
            stamp = _timestamp(row[0])
            if stamp:
                candles.append({"timestamp": stamp, "open": _number(row[1]), "high": _number(row[2]), "low": _number(row[3]), "close": _number(row[4]), "volume": max(0.0, _number(row[5]) or 0.0), "open_interest": _number(row[6]) if len(row) > 6 else None, "completed": True})
        candles.sort(key=lambda item: item["timestamp"])
        return candles if validate_candles(candles)["valid"] else []

    def get_market_data(self, symbol: str, period: str = "5d", interval: str = "5m") -> dict[str, Any]:
        quote_value = self.get_quote(symbol); candles = self.get_historical_candles(symbol, interval)
        if not quote_value or not candles: raise ProviderUnavailable("upstox_verified_data_unavailable")
        latest = candles[-1]
        return {
            **quote_value, "open_value": quote_value["open"], "day_high": quote_value["high"],
            "day_low": quote_value["low"], "latest_volume": quote_value["volume"] or 0,
            "candles": candles, "interval_minutes": int(str(interval).replace("m", "")),
            "open": pd.Series([item["open"] for item in candles]),
            "high": pd.Series([item["high"] for item in candles]),
            "low": pd.Series([item["low"] for item in candles]),
            "close": pd.Series([item["close"] for item in candles]),
            "volume": pd.Series([item["volume"] for item in candles]),
        }

    def get_many(self, symbols: Iterable[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        result = {}
        for symbol in symbols:
            try: result[symbol] = self.get_market_data(symbol, **kwargs)
            except Exception: continue
        return result

    def get_live_price(self, symbol: str) -> float | None:
        value = self.get_quote(symbol); return float(value["last_price"]) if value else None
    def freshness_status(self, symbol: str) -> dict[str, Any]:
        value = self.get_quote(symbol)
        return {"symbol": symbol, "status": "READY", **value["freshness"]} if value else {"symbol": symbol, "status": "NO_DATA", "valid": False}
    def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": self.configured, "master_available": bool(self._master), "available": self.available, "authenticated": self.authenticate() if self.configured else False, "websocket_supported": True}
    def websocket_capability(self) -> dict[str, Any]: return {"supported": True, "version": 3, "transport": "protobuf"}
    def close(self) -> None: self.session.close()


__all__ = ["UpstoxProvider", "MASTER_URL", "MASTER_PATH"]
