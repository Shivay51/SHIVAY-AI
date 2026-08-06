"""Read-only Groww market-data adapter for NSE F&O and MCX contracts.

Only endpoints documented by Groww are used.  The adapter intentionally has
no order surface and remains unavailable until an API subscription token is
present.  Instrument metadata comes from Groww's public official CSV.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from data_quality import assess_market_data, validate_candles, validate_instrument_contract
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable

ROOT = Path(__file__).resolve().parent
API_ROOT = "https://api.groww.in/v1"
MASTER_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
MASTER_PATH = ROOT / "cache" / "groww_instruments.json"
SUPPORTED_INTERVALS = {1: "1minute", 5: "5minute", 15: "15minute", 30: "30minute", 60: "1hour"}
IST = ZoneInfo("Asia/Kolkata")


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
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class GrowwProvider:
    name = "groww_primary"

    def __init__(self, session: requests.Session | None = None):
        self.enabled = os.getenv("ENABLE_GROWW", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.access_token = os.getenv("GROWW_ACCESS_TOKEN", "").strip()
        self.session = session or requests.Session()
        self.cache = ProviderCache(15, 512)
        self._lock = threading.RLock()
        self._last_request = 0.0
        self._master = self._load_master()
        self.available = bool(self.configured and self._master)

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.access_token)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "X-API-VERSION": "1.0",
        }

    def _request(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise ProviderUnavailable("groww_not_configured")
        with self._lock:
            remaining = 0.11 - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request = time.monotonic()
        response = self.session.get(f"{API_ROOT}{path}", params=params, headers=self._headers(), timeout=10)
        if response.status_code in {401, 403}:
            raise PermissionError("groww_authentication_rejected")
        if response.status_code == 429:
            raise ProviderUnavailable("groww_rate_limited")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or str(payload.get("status", "")).upper() != "SUCCESS":
            raise ProviderUnavailable("groww_invalid_response")
        return payload

    def authenticate(self) -> bool:
        if not self.configured:
            return False
        try:
            value = self._request("/user/detail")
            return isinstance(value.get("payload"), dict)
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
        response = self.session.get(MASTER_URL, headers={"Accept": "text/csv"}, timeout=20)
        response.raise_for_status()
        rows = [dict(row) for row in csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))) if isinstance(row, dict)]
        if not rows or not {"exchange", "trading_symbol", "segment", "expiry_date"}.issubset(rows[0]):
            raise ProviderUnavailable("groww_invalid_instrument_master")
        MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = MASTER_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, MASTER_PATH)
        self._master = rows
        self.available = bool(self.configured and self._master)
        return {"supported": True, "count": len(rows), "source": self.name, "official": True}

    def get_instrument_master(self, refresh: bool = False) -> dict[str, Any]:
        if refresh:
            return self.refresh_instrument_master()
        return {"supported": bool(self._master), "count": len(self._master), "source": self.name if self._master else None}

    @staticmethod
    def _wanted(symbol: str) -> tuple[str, str, str]:
        key = str(symbol).strip().upper().replace("_", " ")
        commodity_aliases = {
            "GOLD": "GOLD", "GOLD FUT": "GOLD", "MCX GOLD": "GOLD",
            "SILVER": "SILVER", "SILVER FUT": "SILVER", "MCX SILVER": "SILVER",
            "CRUDE OIL": "CRUDEOIL", "CRUDEOIL": "CRUDEOIL", "CRUDEOIL FUT": "CRUDEOIL",
            "NATURAL GAS": "NATURALGAS", "NATURALGAS": "NATURALGAS", "NATURALGAS FUT": "NATURALGAS",
            "COPPER": "COPPER", "COPPER FUT": "COPPER",
        }
        if key in commodity_aliases:
            return commodity_aliases[key], "MCX", "COMMODITY"
        return key.removesuffix(" FUT").replace(" ", ""), "NSE", "FNO"

    def resolve_current_contract(self, symbol: str) -> dict[str, Any] | None:
        underlying, exchange, segment = self._wanted(symbol)
        matches: list[tuple[date, dict[str, Any]]] = []
        for row in self._master:
            if str(row.get("exchange", "")).upper() != exchange or str(row.get("segment", "")).upper() != segment:
                continue
            if str(row.get("instrument_type", "")).upper() != "FUT":
                continue
            candidate = str(row.get("underlying_symbol") or row.get("name") or "").upper().replace(" ", "")
            if candidate != underlying:
                continue
            try:
                expiry = date.fromisoformat(str(row.get("expiry_date") or "")[:10])
            except ValueError:
                continue
            if expiry < date.today():
                continue
            token = str(row.get("exchange_token") or "").strip()
            lot, tick = _number(row.get("lot_size")), _number(row.get("tick_size"))
            if not token or not lot or not tick:
                continue
            is_mcx = exchange == "MCX"
            normalized = {
                "symbol": str(symbol).strip().upper(), "underlying": underlying,
                "exchange": exchange, "segment": "MCX_COMM" if is_mcx else "NSE_FNO",
                "provider_segment": segment,
                "instrument_type": "FUTCOM" if is_mcx else "FUTIDX" if underlying in {"NIFTY", "BANKNIFTY"} else "FUTSTK",
                "instrument_id": token, "instrument_key": token, "security_id": token,
                "trading_symbol": str(row.get("trading_symbol") or ""),
                "groww_symbol": str(row.get("groww_symbol") or ""),
                "expiry": expiry.isoformat(), "lot_size": lot, "tick_size": tick,
                "provider": self.name, "verified": True,
            }
            if not validate_instrument_contract(normalized, normalized["symbol"]):
                matches.append((expiry, normalized))
        return min(matches, key=lambda item: item[0])[1] if matches else None

    def resolve_instrument(self, symbol: str) -> dict[str, Any] | None:
        return self.resolve_current_contract(symbol)

    def search_instrument(self, symbol: str) -> list[dict[str, Any]]:
        value = self.resolve_current_contract(symbol)
        return [value] if value else []

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        instrument = self.resolve_current_contract(symbol)
        if not instrument:
            return None
        payload = self._request("/live-data/quote", params={
            "exchange": instrument["exchange"],
            "segment": instrument["provider_segment"],
            "trading_symbol": instrument["trading_symbol"],
        }).get("payload")
        if not isinstance(payload, dict):
            return None
        ohlc = payload.get("ohlc")
        if isinstance(ohlc, str):
            try:
                ohlc = ast.literal_eval(ohlc)
            except (SyntaxError, ValueError):
                values = {
                    key.lower(): _number(value)
                    for key, value in re.findall(
                        r"\b(open|high|low|close)\s*:\s*(-?\d+(?:\.\d+)?)", ohlc, re.IGNORECASE
                    )
                }
                ohlc = values or None
        if not isinstance(ohlc, Mapping):
            ohlc = payload
        stamp = _timestamp(payload.get("last_trade_time") or payload.get("timestamp"))
        result = {
            **instrument, "timestamp": stamp, "last_price": _number(payload.get("last_price")),
            "price": _number(payload.get("last_price")), "open": _number(ohlc.get("open")),
            "high": _number(ohlc.get("high")), "low": _number(ohlc.get("low")),
            "previous_close": _number(ohlc.get("close")), "volume": _number(payload.get("volume")),
            "open_interest": _number(payload.get("open_interest")),
            "previous_open_interest": _number(payload.get("previous_open_interest")),
            "oi_change": _number(payload.get("oi_day_change")),
            "bid": _number(payload.get("bid_price")), "ask": _number(payload.get("offer_price")),
            "source": self.name, "is_live": True, "is_delayed": False,
        }
        quality = assess_market_data({
            **result, "open_value": result["open"], "day_high": result["high"],
            "day_low": result["low"], "latest_volume": result["volume"] or 0,
        })
        result["freshness"] = quality
        return result if quality["valid"] else None

    def get_ohlc(self, symbol: str) -> dict[str, Any] | None:
        return self.get_quote(symbol)

    def get_option_chain_context(self, symbol: str) -> dict[str, Any] | None:
        """Return documented NSE option-chain aggregates without inventing freshness.

        Groww's option-chain response has no exchange timestamp in its documented
        schema, so this context is explicitly marked freshness-unverified and must
        not independently authorize a trade.
        """
        underlying, exchange, segment = self._wanted(symbol)
        if exchange != "NSE" or segment != "FNO":
            return None
        expiries: list[date] = []
        for row in self._master:
            if (str(row.get("exchange", "")).upper() != "NSE"
                    or str(row.get("segment", "")).upper() != "FNO"
                    or str(row.get("instrument_type", "")).upper() not in {"CE", "PE"}
                    or str(row.get("underlying_symbol", "")).upper().replace(" ", "") != underlying):
                continue
            try:
                expiry = date.fromisoformat(str(row.get("expiry_date") or "")[:10])
            except ValueError:
                continue
            if expiry >= date.today():
                expiries.append(expiry)
        if not expiries:
            return None
        expiry = min(expiries)
        payload = self._request(
            f"/option-chain/exchange/NSE/underlying/{underlying}",
            params={"expiry_date": expiry.isoformat()},
        ).get("payload")
        if not isinstance(payload, Mapping):
            return None
        strikes = payload.get("strikes")
        if not isinstance(strikes, Mapping):
            return None
        call_oi = put_oi = call_volume = put_volume = 0.0
        contracts = 0
        for value in strikes.values():
            if not isinstance(value, Mapping):
                continue
            for side, is_put in ((value.get("CE") or value.get("ce"), False),
                                 (value.get("PE") or value.get("pe"), True)):
                if not isinstance(side, Mapping):
                    continue
                contracts += 1
                oi = max(0.0, _number(side.get("open_interest")) or 0.0)
                volume = max(0.0, _number(side.get("volume")) or 0.0)
                if is_put:
                    put_oi += oi
                    put_volume += volume
                else:
                    call_oi += oi
                    call_volume += volume
        return {
            "underlying": underlying, "expiry": expiry.isoformat(),
            "underlying_ltp": _number(payload.get("underlying_ltp")),
            "call_open_interest": call_oi, "put_open_interest": put_oi,
            "put_call_ratio": round(put_oi / call_oi, 4) if call_oi else None,
            "call_volume": call_volume, "put_volume": put_volume,
            "contracts": contracts, "retrieved_at": datetime.now(timezone.utc),
            "market_timestamp": None, "freshness_unverified": True,
            "provider": self.name,
        }

    def get_historical_candles(self, symbol: str, interval: str | int = "15m", days: int = 5) -> list[dict[str, Any]]:
        instrument = self.resolve_current_contract(symbol)
        # Groww's current Backtesting API documents CASH and FNO only.  Never
        # infer MCX candles from COMEX or silently promote an unsupported feed.
        if not instrument or instrument["provider_segment"] == "COMMODITY":
            return []
        try:
            minutes = int(str(interval).lower().replace("minute", "").replace("m", ""))
        except ValueError:
            return []
        candle_interval = SUPPORTED_INTERVALS.get(minutes)
        if not candle_interval:
            return []
        now = datetime.now(timezone.utc)
        payload = self._request("/historical/candles", params={
            "exchange": instrument["exchange"], "segment": instrument["provider_segment"],
            "groww_symbol": instrument["groww_symbol"],
            "start_time": (now - timedelta(days=max(2, min(int(days), 30)))).strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": now.strftime("%Y-%m-%d %H:%M:%S"), "candle_interval": candle_interval,
        }).get("payload") or {}
        rows = payload.get("candles") if isinstance(payload, dict) else []
        candles = []
        for row in rows or []:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            stamp = _timestamp(row[0])
            if stamp:
                candles.append({
                    "timestamp": stamp, "open": _number(row[1]), "high": _number(row[2]),
                    "low": _number(row[3]), "close": _number(row[4]),
                    "volume": max(0.0, _number(row[5]) or 0.0),
                    "open_interest": _number(row[6]) if len(row) > 6 else None,
                    "completed": stamp + timedelta(minutes=minutes) <= now,
                })
        candles.sort(key=lambda item: item["timestamp"])
        return candles if validate_candles(candles)["valid"] else []

    def get_market_data(self, symbol: str, period: str = "5d", interval: str = "15m") -> dict[str, Any]:
        cache_key = f"{symbol}:{period}:{interval}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        quote = self.get_quote(symbol)
        candles = self.get_historical_candles(symbol, interval, 5)
        if not quote or not candles:
            raise ProviderUnavailable("groww_verified_data_unavailable")
        result = {
            **quote, "open_value": quote["open"], "day_high": quote["high"], "day_low": quote["low"],
            "latest_volume": quote["volume"] or 0, "candles": candles,
            "interval_minutes": int(str(interval).lower().replace("m", "")),
            "open": pd.Series([item["open"] for item in candles]),
            "high": pd.Series([item["high"] for item in candles]),
            "low": pd.Series([item["low"] for item in candles]),
            "close": pd.Series([item["close"] for item in candles]),
            "volume": pd.Series([item["volume"] for item in candles]),
        }
        self.cache.set(cache_key, result)
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
        value = self.get_quote(symbol)
        return float(value["last_price"]) if value else None

    def freshness_status(self, symbol: str) -> dict[str, Any]:
        value = self.get_quote(symbol)
        return {"symbol": symbol, "status": "READY", **value["freshness"]} if value else {"symbol": symbol, "status": "NO_DATA", "valid": False}

    def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.name, "configured": self.configured, "master_available": bool(self._master),
            "available": self.available, "authenticated": self.authenticate() if self.configured else False,
            "streaming_supported": True, "orders_enabled": False,
        }

    def close(self) -> None:
        self.session.close()


__all__ = ["GrowwProvider", "MASTER_URL", "MASTER_PATH", "SUPPORTED_INTERVALS"]
