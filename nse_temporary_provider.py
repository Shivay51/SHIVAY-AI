"""Read-only temporary NSE F&O provider using NSE's public web data.

The request flow follows the open-source nse-rs implementation vendored in
``vendor/nse-rs``.  This is a replaceable temporary adapter, not an exchange
licensed redistribution feed, and it intentionally exposes no order methods.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from data_quality import assess_market_data, validate_candles
from groww_provider import GrowwProvider
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable

IST = ZoneInfo("Asia/Kolkata")
QUOTE_URL = "https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi"
SEARCH_URL = "https://charting.nseindia.com/v1/exchanges/symbolsDynamic"
HISTORY_URL = "https://charting.nseindia.com/v1/charts/symbolHistoricalData"
INTERVALS = {1, 3, 5, 15, 30, 60}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _quote_time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except (TypeError, ValueError):
        return None


class NSETemporaryProvider:
    name = "nse_temporary"

    def __init__(self, session: requests.Session | None = None):
        self.enabled = os.getenv("ENABLE_NSE_TEMPORARY", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": "https://www.nseindia.com/get-quotes/derivatives",
            "Cache-Control": "no-cache",
        })
        self.cache = ProviderCache(10, 256)
        self._lock = threading.RLock()
        self._last_request = 0.0
        self._batch_cursor = 0
        self._master = GrowwProvider(session=requests.Session())
        self.available = bool(self.enabled and self._master.get_instrument_master().get("supported"))

    def _get(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise ProviderUnavailable("nse_temporary_unavailable")
        with self._lock:
            wait = 0.35 - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            response = self.session.get(url, params=params, timeout=15)
        if response.status_code in {401, 403, 429}:
            raise ProviderUnavailable(f"nse_public_endpoint_{response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderUnavailable("nse_invalid_response")
        return payload

    def resolve_current_contract(self, symbol: str) -> dict[str, Any] | None:
        contract = self._master.resolve_current_contract(symbol)
        if not contract or contract.get("exchange") != "NSE" or contract.get("segment") != "NSE_FNO":
            return None
        return {**contract, "provider": self.name, "verified": True}

    def resolve_instrument(self, symbol: str) -> dict[str, Any] | None:
        return self.resolve_current_contract(symbol)

    def search_instrument(self, symbol: str) -> list[dict[str, Any]]:
        result = self.resolve_current_contract(symbol)
        return [result] if result else []

    def _derivatives(self, underlying: str) -> dict[str, Any]:
        return self._get(QUOTE_URL, {
            "functionName": "getSymbolDerivativesData",
            "symbol": underlying,
            "_": int(time.time() * 1000),
        })

    def get_quote(self, symbol: str) -> dict[str, Any] | None:
        contract = self.resolve_current_contract(symbol)
        if not contract:
            return None
        payload = self._derivatives(contract["underlying"])
        expiry = datetime.strptime(contract["expiry"], "%Y-%m-%d").strftime("%d-%b-%Y").upper()
        rows = payload.get("data") or []
        match = next((row for row in rows if isinstance(row, Mapping)
                      and str(row.get("instrumentType", "")).upper() == contract["instrument_type"]
                      and str(row.get("underlying", "")).upper().replace(" ", "") == contract["underlying"]
                      and str(row.get("expiryDate", "")).upper() == expiry), None)
        if not isinstance(match, Mapping):
            return None
        stamp = _quote_time(payload.get("timestamp"))
        now = datetime.now(timezone.utc)
        age = max(0.0, (now - stamp.astimezone(timezone.utc)).total_seconds()) if stamp else None
        result = {
            **contract, "timestamp": stamp, "market_timestamp": stamp, "last_price": _number(match.get("lastPrice")),
            "price": _number(match.get("lastPrice")), "open": _number(match.get("openPrice")),
            "high": _number(match.get("highPrice")), "low": _number(match.get("lowPrice")),
            "previous_close": _number(match.get("prevClose") or match.get("closePrice")),
            "volume": _number(match.get("totalTradedVolume")),
            "open_interest": _number(match.get("openInterest")),
            "oi_change": _number(match.get("changeinOpenInterest")),
            "oi_change_percent": _number(match.get("pchangeinOpenInterest")),
            "bid": None, "ask": None, "source": self.name, "is_live": True,
            "primary_source": self.name, "secondary_verification_price": None,
            "received_at": now, "is_delayed": False, "data_age": age,
            "freshness_status": "FRESH" if age is not None and age <= 180 else "STALE_OR_CLOSED",
            "is_stale": age is None or age > 180,
        }
        quality = assess_market_data({
            **result, "open_value": result["open"], "day_high": result["high"],
            "day_low": result["low"], "latest_volume": result["volume"] or 0,
        })
        result["freshness"] = quality
        return result

    def get_ohlc(self, symbol: str) -> dict[str, Any] | None:
        return self.get_quote(symbol)

    def get_option_chain_context(self, symbol: str) -> dict[str, Any] | None:
        contract = self.resolve_current_contract(symbol)
        if not contract:
            return None
        payload = self._derivatives(contract["underlying"])
        options = [row for row in payload.get("data") or [] if isinstance(row, Mapping)
                   and str(row.get("instrumentType", "")).upper().startswith("OPT")]
        if not options:
            return None
        expiries = []
        for row in options:
            try:
                expiry = datetime.strptime(str(row.get("expiryDate")), "%d-%b-%Y").date()
            except ValueError:
                continue
            if expiry >= date.today():
                expiries.append(expiry)
        if not expiries:
            return None
        nearest = min(expiries)
        selected = [row for row in options if str(row.get("expiryDate", "")).upper() == nearest.strftime("%d-%b-%Y").upper()]
        call_oi = sum(_number(row.get("openInterest")) or 0 for row in selected if str(row.get("optionType", "")).upper() == "CE")
        put_oi = sum(_number(row.get("openInterest")) or 0 for row in selected if str(row.get("optionType", "")).upper() == "PE")
        return {
            "underlying": contract["underlying"], "expiry": nearest.isoformat(),
            "call_open_interest": call_oi, "put_open_interest": put_oi,
            "put_call_ratio": round(put_oi / call_oi, 4) if call_oi else None,
            "timestamp": _quote_time(payload.get("timestamp")), "provider": self.name,
        }

    def get_historical_candles(self, symbol: str, interval: str | int = "15m", days: int = 5) -> list[dict[str, Any]]:
        contract = self.resolve_current_contract(symbol)
        if not contract:
            return []
        try:
            minutes = int(str(interval).lower().replace("minute", "").replace("m", "").replace("h", "60"))
        except ValueError:
            return []
        if minutes not in INTERVALS:
            return []
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(2, min(int(days), 30)))
        payload = self._get(HISTORY_URL, {
            "chartType": "I", "fromDate": int(start.timestamp()) + 19800,
            "symbol": contract["trading_symbol"], "symbolType": "Futures",
            "timeInterval": minutes, "toDate": int(end.timestamp()) + 19800,
            "token": contract["security_id"],
        })
        raw = payload.get("data") or []
        candles: list[dict[str, Any]] = []
        previous_volume: float | None = None
        previous_day: date | None = None
        for row in sorted((item for item in raw if isinstance(item, Mapping)), key=lambda item: item.get("time", 0)):
            try:
                stamp = datetime.fromtimestamp(float(row["time"]) / 1000 - 19800, timezone.utc)
            except (KeyError, TypeError, ValueError, OSError, OverflowError):
                continue
            ist_day = stamp.astimezone(IST).date()
            cumulative = max(0.0, _number(row.get("volume")) or 0.0)
            volume = cumulative if previous_volume is None or previous_day != ist_day else max(0.0, cumulative - previous_volume)
            if previous_volume is not None and previous_day == ist_day and cumulative < previous_volume:
                volume = cumulative
            previous_volume, previous_day = cumulative, ist_day
            candles.append({
                "timestamp": stamp, "open": _number(row.get("open")), "high": _number(row.get("high")),
                "low": _number(row.get("low")), "close": _number(row.get("close")),
                "volume": volume, "open_interest": None, "completed": stamp + timedelta(minutes=minutes) <= end,
            })
        return candles if validate_candles(candles)["valid"] else []

    def get_market_data(self, symbol: str, period: str = "5d", interval: str = "15m") -> dict[str, Any]:
        key = f"{symbol}:{period}:{interval}"
        cached = self.cache.get(key)
        if cached:
            return cached
        quote = self.get_quote(symbol)
        candles = self.get_historical_candles(symbol, interval, 5)
        if not quote or not candles:
            raise ProviderUnavailable("nse_temporary_data_unavailable")
        minutes = int(str(interval).lower().replace("m", ""))
        result = {
            **quote, "open_value": quote["open"], "day_high": quote["high"], "day_low": quote["low"],
            "latest_volume": quote["volume"] or 0, "candles": candles, "interval_minutes": minutes,
            "open": pd.Series([x["open"] for x in candles]), "high": pd.Series([x["high"] for x in candles]),
            "low": pd.Series([x["low"] for x in candles]), "close": pd.Series([x["close"] for x in candles]),
            "volume": pd.Series([x["volume"] for x in candles]),
        }
        self.cache.set(key, result)
        return result

    def get_many(self, symbols: Iterable[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        requested = [item for item in dict.fromkeys(symbols) if self.resolve_current_contract(item)]
        priority = [item for item in ("NIFTY FUT", "BANKNIFTY FUT") if item in requested]
        others = [item for item in requested if item not in priority]
        now = datetime.now(IST)
        market_open = now.weekday() < 5 and (now.hour, now.minute) >= (9, 15) and (now.hour, now.minute) <= (15, 35)
        # NSE's public website is not a bulk licensed feed.  Bound each polling
        # cycle and rotate stock futures to avoid abusive request volume.
        selected = priority
        if market_open and others:
            width = min(10, len(others))
            selected += [others[(self._batch_cursor + offset) % len(others)] for offset in range(width)]
            self._batch_cursor = (self._batch_cursor + width) % len(others)
        result = {}
        for symbol in selected:
            try:
                result[symbol] = self.get_market_data(symbol, **kwargs)
            except Exception:
                continue
        return result

    def get_live_price(self, symbol: str) -> float | None:
        quote = self.get_quote(symbol)
        return float(quote["last_price"]) if quote and quote.get("last_price") else None

    def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "available": self.available, "official_exchange_source": True,
                "streaming": False, "polling": True, "orders_enabled": False}

    def close(self) -> None:
        self.session.close()
        self._master.close()


__all__ = ["NSETemporaryProvider"]
