"""Official DhanHQ v2 read-only market-data provider for SHIVAY AI.

Only documented data endpoints are exposed. This module contains no order methods.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from data_quality import assess_market_data, parse_timestamp, validate_candles
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable


LOGGER = logging.getLogger("shivay.provider.dhan")
ROOT = Path(__file__).resolve().parent
MASTER_FILE = ROOT / "cache" / "dhan_instruments.csv"
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
BASE_URL = "https://api.dhan.co/v2"
MASTER_TTL_SECONDS = 86_400
REQUEST_TIMEOUT = (4, 10)

SEGMENTS = {"IDX_I", "NSE_EQ", "NSE_FNO", "MCX_COMM"}
FUTURES_TYPES = {"FUTIDX", "FUTSTK", "FUTCOM"}
_MASTER_LOCK = threading.RLock()
_MASTER_MEMORY: list[dict[str, str]] | None = None
_MASTER_TIME = 0.0
_CATALOG: list[dict[str, Any]] | None = None
IST = ZoneInfo("Asia/Kolkata")
_QUOTE_LOCK = threading.RLock()
_DATA_LOCK = threading.RLock()
_LAST_QUOTE_REQUEST = 0.0
_LAST_DATA_REQUEST = 0.0


def _rate_limit(lock: threading.RLock, kind: str, minimum_interval: float) -> None:
    global _LAST_QUOTE_REQUEST, _LAST_DATA_REQUEST
    with lock:
        last = _LAST_QUOTE_REQUEST if kind == "quote" else _LAST_DATA_REQUEST
        remaining = minimum_interval - (time.monotonic() - last)
        if remaining > 0: time.sleep(remaining)
        if kind == "quote": _LAST_QUOTE_REQUEST = time.monotonic()
        else: _LAST_DATA_REQUEST = time.monotonic()


def _field(row: dict[str, str], *names: str) -> str:
    normalized = {str(key).strip().upper(): str(value or "").strip() for key, value in row.items()}
    for name in names:
        value = normalized.get(name.upper())
        if value:
            return value
    return ""


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".dhan-master-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _master_rows(session: requests.Session, force: bool = False) -> list[dict[str, str]]:
    global _MASTER_MEMORY, _MASTER_TIME
    with _MASTER_LOCK:
        if _MASTER_MEMORY is not None and not force and time.monotonic() - _MASTER_TIME < MASTER_TTL_SECONDS:
            return list(_MASTER_MEMORY)
        usable_file = MASTER_FILE.is_file() and time.time() - MASTER_FILE.stat().st_mtime < MASTER_TTL_SECONDS
        if force or not usable_file:
            response = session.get(MASTER_URL, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            if len(response.content) < 100 or b"SEM_" not in response.content[:1000]:
                raise ProviderUnavailable("invalid_dhan_instrument_master")
            _atomic_bytes(MASTER_FILE, response.content)
        try:
            text = MASTER_FILE.read_text(encoding="utf-8-sig", errors="replace")
            rows = [dict(item) for item in csv.DictReader(io.StringIO(text))]
        except (OSError, csv.Error) as error:
            raise ProviderUnavailable("unreadable_dhan_instrument_master") from error
        if not rows:
            raise ProviderUnavailable("empty_dhan_instrument_master")
        _MASTER_MEMORY, _MASTER_TIME = rows, time.monotonic()
        return list(rows)


def _expiry(value: str) -> date | None:
    value = value.strip().split(" ", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            result = datetime.strptime(value, fmt).date()
            return result if result.year >= 2000 else None
        except ValueError:
            continue
    return None


def _catalog(session: requests.Session) -> list[dict[str, Any]]:
    global _CATALOG
    with _MASTER_LOCK:
        if _CATALOG is not None:
            return _CATALOG
        result: list[dict[str, Any]] = []
        for row in _master_rows(session):
            exchange = _field(row, "SEM_EXM_EXCH_ID", "EXCH_ID")
            segment_code = _field(row, "SEM_SEGMENT", "SEGMENT")
            instrument = _field(row, "SEM_INSTRUMENT_NAME", "INSTRUMENT").upper()
            trading = _field(row, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL")
            display = _field(row, "SEM_CUSTOM_SYMBOL", "DISPLAY_NAME", "SM_SYMBOL_NAME", "SYMBOL_NAME", "UNDERLYING_SYMBOL")
            underlying = _field(row, "SM_SYMBOL_NAME", "UNDERLYING_SYMBOL", "SYMBOL_NAME")
            security_id = _field(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
            if not security_id or exchange not in {"NSE", "MCX"}:
                continue
            segment = "MCX_COMM" if exchange == "MCX" else "NSE_FNO" if instrument in FUTURES_TYPES or segment_code in {"D", "FNO"} else "IDX_I" if instrument == "INDEX" else "NSE_EQ"
            result.append({
                "search": (display or trading).upper().replace(" ", ""), "trading_symbol": trading,
                "underlying": (underlying or trading.split("-", 1)[0]).upper().replace(" ", ""),
                "security_id": security_id, "exchange": exchange, "segment": segment,
                "instrument_type": instrument or ("INDEX" if segment == "IDX_I" else "EQUITY"),
                "expiry_date": _expiry(_field(row, "SEM_EXPIRY_DATE", "EXPIRY_DATE")),
                "lot_size": int(float(_field(row, "SEM_LOT_UNITS", "LOT_SIZE") or 0)) or None,
                # Compact master expresses minimum price increments in paise.
                "tick_size_raw": float(_field(row, "SEM_TICK_SIZE", "TICK_SIZE") or 0) or None,
            })
        _CATALOG = result
        return _CATALOG


class DhanProvider:
    """Credential-gated DhanHQ REST provider with verified contract resolution."""

    name = "dhan_primary"
    documented_capabilities = {
        "nse_equity": True, "nse_futures": True, "nifty": True,
        "banknifty": True, "mcx_gold": True, "mcx_silver": True,
        "instrument_master": True, "ltp": True, "ohlc": True,
        "quote": True, "historical_candles": True, "market_websocket": True,
    }

    def __init__(self) -> None:
        load_dotenv(ROOT / ".env", override=False)
        self.client_id = (os.getenv("DHAN_CLIENT_ID") or "").strip()
        self.access_token = (os.getenv("DHAN_ACCESS_TOKEN") or "").strip()
        self.enabled = (os.getenv("ENABLE_DHAN", "true").strip().lower() not in {"0", "false", "no"})
        self.available = bool(self.enabled and self.client_id and self.access_token)
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json", "Content-Type": "application/json",
            "access-token": self.access_token, "client-id": self.client_id,
        })
        self.cache = ProviderCache(ttl_seconds=240, max_entries=512)
        self._resolved: dict[str, dict[str, Any] | None] = {}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.available:
            raise ProviderUnavailable("dhan_credentials_or_data_plan_unavailable")
        response = self.session.request(method, BASE_URL + path, timeout=REQUEST_TIMEOUT, **kwargs)
        if response.status_code in {401, 403}:
            raise PermissionError("dhan_authentication_or_data_plan_rejected")
        if response.status_code == 429:
            raise ProviderUnavailable("dhan_rate_limit")
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict) or value.get("status") == "failure" or value.get("errorCode"):
            raise ProviderUnavailable("dhan_data_api_error")
        return value

    def validate_credentials(self) -> dict[str, Any]:
        if not self.available:
            return {"configured": False, "authenticated": False, "data_plan_active": False}
        value = self._request("GET", "/profile")
        return {
            "configured": True, "authenticated": bool(value.get("dhanClientId")),
            "data_plan_active": str(value.get("dataPlan", "")).lower() == "active",
            "active_segments_present": bool(value.get("activeSegment")),
        }

    def refresh_instruments(self, force: bool = False) -> dict[str, Any]:
        rows = _master_rows(self.session, force=force)
        counts = {segment: 0 for segment in SEGMENTS}
        for row in rows:
            exchange = _field(row, "SEM_EXM_EXCH_ID", "EXCH_ID")
            segment = _field(row, "SEM_SEGMENT", "SEGMENT")
            key = "IDX_I" if exchange == "NSE" and segment in {"I", "INDEX"} else "NSE_EQ" if exchange == "NSE" and segment in {"E", "EQUITY"} else "NSE_FNO" if exchange == "NSE" and segment in {"D", "FNO"} else "MCX_COMM" if exchange == "MCX" else ""
            if key in counts:
                counts[key] += 1
        return {"supported": True, "rows": len(rows), "segments": counts, "source": "dhan_official"}

    def resolve_instrument(self, symbol: str) -> dict[str, Any] | None:
        requested = str(symbol or "").strip().upper()
        if requested in self._resolved:
            value = self._resolved[requested]
            if value and value.get("expiry"):
                expiry=date.fromisoformat(str(value["expiry"]));now=datetime.now(IST);close=(23,30) if value.get("segment")=="MCX_COMM" else (15,30)
                if expiry>now.date() or (expiry==now.date() and now.time()<=datetime.strptime(f"{close[0]:02d}:{close[1]:02d}","%H:%M").time()):return dict(value)
            self._resolved.pop(requested, None)
        root = requested.removesuffix(" FUT").replace(" ", "")
        search_roots = {root}
        if root == "BANKNIFTY":
            search_roots.add("NIFTYBANK")
        want_future = requested.endswith(" FUT") or root in {"GOLD", "SILVER"}
        want_index = requested in {"NIFTY", "BANKNIFTY"}
        candidates: list[dict[str, Any]] = []
        now_ist = datetime.now(IST)
        today = now_ist.date()
        for item in _catalog(self.session):
            instrument = item["instrument_type"]
            normalized = item["search"]
            underlying = item.get("underlying") or normalized.split("-", 1)[0]
            if underlying not in search_roots:
                continue
            segment = item["segment"]
            if want_future and instrument not in FUTURES_TYPES:
                continue
            if want_index and requested == root and segment != "IDX_I":
                continue
            if root == "NIFTY" and "BANKNIFTY" in normalized:
                continue
            expiry_date = item["expiry_date"]
            if want_future and (expiry_date is None or expiry_date < today):
                continue
            if want_future and expiry_date == today:
                close_hour, close_minute = (23, 30) if segment == "MCX_COMM" else (15, 30)
                if now_ist.time() > datetime.strptime(f"{close_hour:02d}:{close_minute:02d}", "%H:%M").time():
                    continue
            candidates.append({
                "symbol": requested, "trading_symbol": item["trading_symbol"], "security_id": item["security_id"],
                "underlying": item.get("underlying"),
                "exchange": item["exchange"], "segment": segment, "instrument_type": instrument,
                "expiry": expiry_date.isoformat() if expiry_date else None,
                "lot_size": item["lot_size"],
                "tick_size": round(item["tick_size_raw"] / 100.0 if item.get("tick_size_raw",0)>=1 else item["tick_size_raw"], 4) if item.get("tick_size_raw") else None,
                "verified": True, "provider": self.name,
            })
        if not candidates:
            self._resolved[requested] = None
            return None
        candidates.sort(key=lambda item: (item.get("expiry") or "9999-12-31", len(item.get("trading_symbol") or "")))
        candidates[0]["instrument_key"] = f"{candidates[0]['segment']}:{candidates[0]['security_id']}"
        self._resolved[requested] = candidates[0]
        return dict(candidates[0])

    def _quote(self, instrument: dict[str, Any]) -> dict[str, Any]:
        _rate_limit(_QUOTE_LOCK,"quote",1.02)
        payload = {instrument["segment"]: [int(instrument["security_id"])]}
        value = self._request("POST", "/marketfeed/quote", json=payload)
        item = value.get("data", {}).get(instrument["segment"], {}).get(str(instrument["security_id"]), {})
        if not isinstance(item, dict) or float(item.get("last_price") or 0) <= 0:
            raise ProviderUnavailable("empty_dhan_quote")
        return item

    def _candles(self, instrument: dict[str, Any], interval_minutes: int = 5) -> list[dict[str, Any]]:
        interval_minutes = interval_minutes if interval_minutes in {1, 5, 15, 25, 60} else 5
        end = datetime.now()
        start = end - timedelta(days=15)
        payload = {
            "securityId": str(instrument["security_id"]), "exchangeSegment": instrument["segment"],
            "instrument": instrument["instrument_type"], "interval": str(interval_minutes), "oi": instrument["instrument_type"] in FUTURES_TYPES,
            "fromDate": start.strftime("%Y-%m-%d %H:%M:%S"), "toDate": end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _rate_limit(_DATA_LOCK,"data",.21)
        value = self._request("POST", "/charts/intraday", json=payload)
        arrays = {key: value.get(key, []) for key in ("open", "high", "low", "close", "volume", "timestamp")}
        size = min((len(values) for values in arrays.values()), default=0)
        candles = []
        for index in range(size):
            stamp = datetime.fromtimestamp(float(arrays["timestamp"][index]), timezone.utc)
            if stamp + timedelta(minutes=interval_minutes) > datetime.now(timezone.utc):
                continue
            candles.append({"timestamp": stamp, "open": float(arrays["open"][index]), "high": float(arrays["high"][index]), "low": float(arrays["low"][index]), "close": float(arrays["close"][index]), "volume": float(arrays["volume"][index])})
        quality = validate_candles(candles)
        if len(candles) < 200 or not quality["valid"]:
            raise ProviderUnavailable("insufficient_or_invalid_dhan_candles")
        return candles

    def get_market_data(self, symbol: str, **kwargs: Any) -> dict[str, Any]:
        key = str(symbol).strip().upper()
        raw_interval = str(kwargs.get("interval", "5m")).lower().replace("m", "")
        try:
            interval_minutes = int(raw_interval)
        except ValueError:
            interval_minutes = 5
        cache_key = f"{key}:{interval_minutes}m"
        cached = self.cache.get(cache_key)
        if cached:
            cached_quality=assess_market_data(cached)
            if cached_quality["valid"]:return cached
            self.cache.invalidate(cache_key)
        instrument = self.resolve_instrument(key)
        if not instrument:
            raise ValueError("unverified_dhan_instrument")
        candles = self._candles(instrument, interval_minutes)
        quote = self._quote(instrument)
        price = float(quote["last_price"])
        ohlc = quote.get("ohlc") if isinstance(quote.get("ohlc"), dict) else {}
        raw_stamp = quote.get("last_trade_time") or quote.get("last_trade_timestamp")
        if raw_stamp is None:
            raise ProviderUnavailable("dhan_quote_has_no_exchange_timestamp")
        if isinstance(raw_stamp,str) and not raw_stamp.strip().replace(".","",1).isdigit():
            stamp=None
            for fmt in ("%Y-%m-%d %H:%M:%S","%d/%m/%Y %H:%M:%S"):
                try:stamp=datetime.strptime(raw_stamp.strip(),fmt).replace(tzinfo=IST).astimezone(timezone.utc);break
                except ValueError:continue
        else:
            stamp=parse_timestamp(raw_stamp)
        if stamp is None:raise ProviderUnavailable("invalid_dhan_exchange_timestamp")
        quality = validate_candles(candles)
        result = {
            **instrument, "price": price,
            "open": pd.Series([item["open"] for item in candles]), "high": pd.Series([item["high"] for item in candles]),
            "low": pd.Series([item["low"] for item in candles]), "close": pd.Series([item["close"] for item in candles]),
            "volume": pd.Series([item["volume"] for item in candles]), "open_value": float(ohlc.get("open") or candles[-1]["open"]),
            "latest_volume": float(quote.get("volume") or candles[-1]["volume"]), "day_high": float(ohlc.get("high") or max(item["high"] for item in candles[-75:])),
            "day_low": float(ohlc.get("low") or min(item["low"] for item in candles[-75:])), "timestamp": stamp,
            "is_live": True, "is_delayed": False, "data_quality": quality, "candles": candles,
            "interval_minutes": interval_minutes,
        }
        freshness = assess_market_data(result)
        result.update(delay_seconds=freshness["delay_seconds"], is_stale=freshness["is_stale"], freshness_status=freshness["status"])
        if not freshness["valid"]:
            raise ProviderUnavailable("unsafe_dhan_market_data")
        self.cache.set(cache_key, result)
        return result

    def get_live_price(self, symbol: str) -> float | None:
        instrument = self.resolve_instrument(symbol)
        if not instrument:
            return None
        return round(float(self._quote(instrument)["last_price"]), 2)

    def get_many(self, symbols: Iterable[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for index, symbol in enumerate(dict.fromkeys(str(item) for item in symbols)):
            try:
                result[symbol] = self.get_market_data(symbol, **kwargs)
            except Exception as error:
                LOGGER.warning("Dhan data unavailable for %s: %s", str(symbol)[:32], type(error).__name__)
            if index and index % 4 == 0:
                time.sleep(1.05)
        return result

    def websocket_capability(self) -> dict[str, Any]:
        return {"supported": True, "endpoint": "wss://api-feed.dhan.co", "protocol": "binary", "sdk": "dhanhq", "configured": self.available}


def get_dhan_status() -> dict[str, Any]:
    provider = DhanProvider()
    return {"configured": provider.available, "enabled": provider.enabled, "capabilities": dict(provider.documented_capabilities)}
