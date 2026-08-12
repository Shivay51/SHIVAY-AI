"""Read-only Angel One SmartAPI market-data provider (production primary).

This module deliberately has no order placement, modification, cancellation,
buy, sell, position, holding or automated-trading methods.  It exposes only
market data: quotes, OHLC, historical candles, volume, open interest,
timestamps and freshness metadata.

Authentication uses SmartAPI login-by-password with an MPIN/PIN and a TOTP
generated at runtime from ANGEL_TOTP_SECRET.  A static six-digit code is never
stored, requested or logged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import socket
import struct
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import angel_instruments
from data_quality import assess_market_data, validate_candles

LOGGER = logging.getLogger("shivay.angel.provider")
IST = ZoneInfo("Asia/Kolkata")

# SmartAPI historical-candle interval names keyed by minutes.
_INTERVALS = {1: "ONE_MINUTE", 3: "THREE_MINUTE", 5: "FIVE_MINUTE", 10: "TEN_MINUTE", 15: "FIFTEEN_MINUTE", 30: "THIRTY_MINUTE", 60: "ONE_HOUR"}
_PERIOD_DAYS = {"1d": 1, "2d": 2, "5d": 5, "1w": 7, "10d": 10, "1mo": 30, "2mo": 60, "3mo": 90}
_SESSION_TTL_SECONDS = int(os.getenv("ANGEL_SESSION_TTL_SECONDS", "18000"))  # refresh well inside Angel's daily window


class AngelProviderError(RuntimeError):
    """Angel One SmartAPI is unavailable, unconfigured or returned bad data."""


class AngelRateLimited(AngelProviderError):
    """Angel One SmartAPI applied a rate limit."""


def generate_totp(secret: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    """Generate a fresh RFC 6238 TOTP from a base32 secret.

    The current code is always derived at call time; no OTP value is persisted.
    """
    cleaned = "".join(str(secret or "").split()).upper().replace("-", "")
    if not cleaned:
        raise AngelProviderError("angel_totp_secret_missing")
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    try:
        key = base64.b32decode(cleaned + padding, casefold=True)
    except Exception as exc:  # noqa: BLE001 - invalid secret must fail closed
        raise AngelProviderError("angel_totp_secret_invalid") from exc
    counter = int((at if at is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def _local_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _mac() -> str:
    node = uuid.getnode()
    return ":".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -8, -8))


class AngelReadOnlyProvider:
    """Angel One SmartAPI read-only market-data adapter."""

    name = "angelone_primary"
    signal_capable = True
    BASE_URL = os.getenv("ANGEL_BASE_URL", "https://apiconnect.angelone.in")
    LOGIN_PATH = "/rest/auth/angelbroking/user/v1/loginByPassword"
    REFRESH_PATH = "/rest/auth/angelbroking/jwt/v1/generateTokens"
    LOGOUT_PATH = "/rest/secure/angelbroking/user/v1/logout"
    QUOTE_PATH = "/rest/secure/angelbroking/market/v1/quote/"
    CANDLE_PATH = "/rest/secure/angelbroking/historical/v1/getCandleData"
    PROFILE_PATH = "/rest/secure/angelbroking/user/v1/getProfile"

    def __init__(self, timeout: int = 12, max_attempts: int = 3) -> None:
        self.api_key = _first_env("ANGEL_API_KEY", "ANGEL_MARKET_API_KEY")
        self.client_code = _first_env("ANGEL_CLIENT_CODE", "ANGEL_CLIENT_ID")
        self.mpin = _first_env("ANGEL_MPIN", "ANGEL_PIN", "ANGEL_PASSWORD")
        self.totp_secret = _first_env("ANGEL_TOTP_SECRET", "ANGEL_TOTP")
        self.timeout = max(4, int(timeout))
        self.max_attempts = max(1, min(int(max_attempts), 4))
        self.jwt_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.feed_token: Optional[str] = None
        self.session_started: float = 0.0
        self._lock = threading.RLock()
        self._client_ip = _local_ip()
        self._mac = _mac()
        self._rate_limited_until = 0.0
        self._last_error: str | None = None
        self._instrument_refreshed_on: str | None = None

    # ------------------------------------------------------------------
    # configuration / health
    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return all((self.api_key, self.client_code, self.mpin, self.totp_secret))

    @property
    def available(self) -> bool:
        return self.configured and time.monotonic() >= self._rate_limited_until

    @property
    def session_valid(self) -> bool:
        return bool(self.jwt_token) and (time.monotonic() - self.session_started) < _SESSION_TTL_SECONDS

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self.api_key:
            missing.append("ANGEL_API_KEY")
        if not self.client_code:
            missing.append("ANGEL_CLIENT_CODE")
        if not self.mpin:
            missing.append("ANGEL_MPIN")
        if not self.totp_secret:
            missing.append("ANGEL_TOTP_SECRET")
        return missing

    def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": self.configured,
            "connected": self.session_valid,
            "read_only": True,
            "signal_capable": self.signal_capable,
            "missing_credentials": self.missing_credentials(),
            "rate_limited": time.monotonic() < self._rate_limited_until,
            "instrument_master": angel_instruments.status(),
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------
    # session handling
    # ------------------------------------------------------------------
    def connect(self) -> dict[str, Any]:
        if not self.configured:
            raise AngelProviderError("angel_credentials_not_configured:" + ",".join(self.missing_credentials()))
        payload = {"clientcode": self.client_code, "password": self.mpin, "totp": generate_totp(self.totp_secret)}
        body = self._request(self.LOGIN_PATH, payload, authenticated=False)
        data = body.get("data") or {}
        token = data.get("jwtToken") or data.get("jwttoken")
        if not token:
            raise AngelProviderError(str(body.get("message") or "angel_login_failed"))
        with self._lock:
            self.jwt_token = str(token).removeprefix("Bearer ").strip()
            self.refresh_token = data.get("refreshToken") or data.get("refreshtoken")
            self.feed_token = data.get("feedToken") or data.get("feedtoken")
            self.session_started = time.monotonic()
        LOGGER.info("Angel SmartAPI session established (read-only)")
        return {"connected": True, "feed_token_present": bool(self.feed_token)}

    def refresh_session(self) -> bool:
        """Renew the session using the refresh token; fall back to fresh login."""
        with self._lock:
            refresh_token = self.refresh_token
        if refresh_token:
            try:
                body = self._request(self.REFRESH_PATH, {"refreshToken": refresh_token}, authenticated=True)
                data = body.get("data") or {}
                token = data.get("jwtToken") or data.get("jwttoken")
                if token:
                    with self._lock:
                        self.jwt_token = str(token).removeprefix("Bearer ").strip()
                        self.refresh_token = data.get("refreshToken") or refresh_token
                        self.feed_token = data.get("feedToken") or self.feed_token
                        self.session_started = time.monotonic()
                    return True
            except AngelProviderError:
                LOGGER.warning("Angel session refresh failed; reconnecting")
        self.connect()
        return True

    def _ensure_session(self) -> None:
        if not self.session_valid:
            if self.jwt_token:
                self.refresh_session()
            else:
                self.connect()
        self._ensure_instruments()

    def _ensure_instruments(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if self._instrument_refreshed_on == today:
            return
        try:
            angel_instruments.load_instruments()
            self._instrument_refreshed_on = today
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Angel instrument master unavailable: %s", type(exc).__name__)

    def close(self) -> None:
        with self._lock:
            token = self.jwt_token
        if token:
            try:
                self._request(self.LOGOUT_PATH, {"clientcode": self.client_code}, authenticated=True)
            except Exception:
                pass
        with self._lock:
            self.jwt_token = None
            self.refresh_token = None
            self.feed_token = None
            self.session_started = 0.0

    # ------------------------------------------------------------------
    # instruments
    # ------------------------------------------------------------------
    def resolve_current_contract(self, symbol: str) -> dict[str, Any] | None:
        try:
            return angel_instruments.resolve(symbol)
        except Exception as exc:  # noqa: BLE001
            self._last_error = type(exc).__name__
            return None

    def search_instrument(self, symbol: str) -> dict[str, Any] | None:
        return self.resolve_current_contract(symbol)

    def get_instrument_master(self, refresh: bool = False) -> dict[str, Any]:
        return angel_instruments.refresh() if refresh else angel_instruments.status()

    def refresh_instrument_master(self) -> dict[str, Any]:
        result = angel_instruments.refresh()
        self._instrument_refreshed_on = datetime.now(timezone.utc).date().isoformat()
        return result

    def _instrument(self, symbol: str) -> dict[str, Any]:
        contract = self.resolve_current_contract(symbol)
        if not contract:
            raise AngelProviderError("angel_contract_unavailable")
        expiry = contract.get("expiry")
        if expiry:
            try:
                if date.fromisoformat(str(expiry)) < datetime.now(IST).date():
                    raise AngelProviderError("angel_expired_contract")
            except ValueError as exc:
                raise AngelProviderError("angel_unidentified_contract") from exc
        if not contract.get("angel_token"):
            raise AngelProviderError("angel_symbol_token_missing")
        return contract

    # ------------------------------------------------------------------
    # market data
    # ------------------------------------------------------------------
    def get_quote(self, symbol: str) -> dict[str, Any]:
        """Return a FULL-mode Angel quote for one contract."""
        instrument = self._instrument(symbol)
        self._ensure_session()
        exchange = str(instrument.get("angel_exchange") or "NSE").upper()
        body = self._request(
            self.QUOTE_PATH,
            {"mode": "FULL", "exchangeTokens": {exchange: [str(instrument["angel_token"])]}},
            authenticated=True,
        )
        data = body.get("data") or {}
        fetched = data.get("fetched") or []
        if not fetched:
            unfetched = data.get("unfetched") or []
            reason = str((unfetched[0] or {}).get("message", "")) if unfetched else ""
            raise AngelProviderError(f"angel_quote_unavailable:{reason[:60]}")
        row = fetched[0]
        return {"instrument": instrument, "quote": row}

    def get_candles(self, symbol: str, interval_minutes: int = 15, period: str = "5d") -> list[dict[str, Any]]:
        """Return completed historical candles for a supported timeframe."""
        minutes = int(interval_minutes)
        if minutes not in _INTERVALS:
            raise AngelProviderError(f"angel_unsupported_interval:{minutes}")
        instrument = self._instrument(symbol)
        self._ensure_session()
        days = _PERIOD_DAYS.get(str(period).lower(), 5)
        now = datetime.now(IST)
        start = now - timedelta(days=max(1, days))
        payload = {
            "exchange": str(instrument.get("angel_exchange") or "NSE").upper(),
            "symboltoken": str(instrument["angel_token"]),
            "interval": _INTERVALS[minutes],
            "fromdate": start.strftime("%Y-%m-%d %H:%M"),
            "todate": now.strftime("%Y-%m-%d %H:%M"),
        }
        body = self._request(self.CANDLE_PATH, payload, authenticated=True)
        rows = body.get("data") or []
        candles: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            stamp = row[0]
            parsed = stamp if isinstance(stamp, datetime) else None
            if parsed is None:
                try:
                    parsed = datetime.fromisoformat(str(stamp))
                except ValueError:
                    continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=IST)
            candles.append({
                "timestamp": parsed.astimezone(timezone.utc),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "completed": True,
                "provider": self.name,
                "source": self.name,
            })
        candles.sort(key=lambda item: item["timestamp"])
        if not candles:
            raise AngelProviderError("angel_candles_empty")
        return candles

    def get_market_data(self, symbol: str, period: str = "5d", interval: str = "15m", **_: Any) -> dict[str, Any]:
        """Return a normalized, scanner-compatible verified snapshot."""
        try:
            minutes = int(str(interval).lower().replace("m", "").replace("min", ""))
        except ValueError:
            minutes = 15
        if minutes not in _INTERVALS:
            minutes = 15
        quote_result = self.get_quote(symbol)
        instrument = quote_result["instrument"]
        quote = quote_result["quote"]
        candles = self.get_candles(symbol, minutes, period)
        candle_quality = validate_candles(candles)
        if not candle_quality["valid"]:
            raise AngelProviderError("angel_candles_rejected:" + ",".join(candle_quality["errors"][:3]))

        price = _float(quote.get("ltp"))
        if price is None:
            raise AngelProviderError("angel_ltp_missing")
        received_at = datetime.now(timezone.utc)
        exchange_stamp = _parse_angel_timestamp(quote.get("exchFeedTime") or quote.get("exchTradeTime")) or received_at
        window = candles[-75:]
        result: dict[str, Any] = {
            **instrument,
            "price": price,
            "ltp": price,
            "open_value": _float(quote.get("open")) or window[-1]["open"],
            "day_high": _float(quote.get("high")) or max(item["high"] for item in window),
            "day_low": _float(quote.get("low")) or min(item["low"] for item in window),
            "previous_close": _float(quote.get("close")),
            "latest_volume": _float(quote.get("tradeVolume")) or window[-1]["volume"],
            "volume": _float(quote.get("tradeVolume")) or window[-1]["volume"],
            "open_interest": _float(quote.get("opnInterest")),
            "previous_open_interest": _float(quote.get("prevOpnInterest")),
            "bid": _best_depth(quote, "buy"),
            "ask": _best_depth(quote, "sell"),
            "upper_circuit": _float(quote.get("upperCircuit")),
            "lower_circuit": _float(quote.get("lowerCircuit")),
            "timestamp": exchange_stamp,
            "exchange_timestamp": exchange_stamp,
            "received_at": received_at,
            "retrieval_timestamp": received_at,
            "provider": self.name,
            "source_type": "ANGEL_SMARTAPI_READ_ONLY",
            "is_live": True,
            "is_delayed": False,
            "verified": True,
            "read_only": True,
            "interval_minutes": minutes,
            "candles": candles,
            "candle_source": self.name,
            "data_source": self.name,
            "candle_quality": candle_quality,
        }
        try:
            import pandas as pd

            result.update(
                open=pd.Series([item["open"] for item in candles]),
                high=pd.Series([item["high"] for item in candles]),
                low=pd.Series([item["low"] for item in candles]),
                close=pd.Series([item["close"] for item in candles]),
                volume=pd.Series([item["volume"] for item in candles]),
            )
        except Exception:  # noqa: BLE001 - pandas is a hard dependency, stay safe
            LOGGER.warning("Angel snapshot series could not be built")
        freshness = assess_market_data(result)
        result.update(
            delay_seconds=freshness["delay_seconds"],
            is_stale=freshness["is_stale"],
            freshness_status=freshness["status"],
            data_quality=freshness,
            data_age_seconds=freshness["delay_seconds"],
        )
        if freshness["is_stale"]:
            raise AngelProviderError("angel_data_stale")
        from tradingview_bridge import assert_single_provider_dataset
        assert_single_provider_dataset(result)
        return result

    def get_ohlc(self, symbol: str) -> dict[str, Any] | None:
        value = self.get_market_data(symbol)
        return {
            "symbol": value["symbol"],
            "exchange": value["exchange"],
            "contract": value.get("trading_symbol"),
            "open": value["open_value"],
            "high": value["day_high"],
            "low": value["day_low"],
            "close": value["price"],
            "volume": value.get("latest_volume"),
            "open_interest": value.get("open_interest"),
            "timestamp": value["timestamp"],
        }

    def get_historical_candles(self, symbol: str, interval: str = "15m", period: str = "5d") -> list[dict[str, Any]]:
        try:
            minutes = int(str(interval).lower().replace("m", ""))
        except ValueError:
            minutes = 15
        return self.get_candles(symbol, minutes, period)

    def get_live_price(self, symbol: str) -> float | None:
        return round(float(self.get_market_data(symbol)["price"]), 2)

    def get_many(self, symbols: list[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for symbol in dict.fromkeys(symbols):
            try:
                result[symbol] = self.get_market_data(symbol, **kwargs)
            except Exception as exc:  # noqa: BLE001 - per-symbol isolation
                self._last_error = f"{symbol}:{type(exc).__name__}"
                continue
        return result

    def freshness_status(self, symbol: str) -> dict[str, Any]:
        try:
            value = self.get_market_data(symbol)
        except Exception as exc:  # noqa: BLE001
            return {"symbol": symbol, "status": "UNAVAILABLE", "reason": str(exc)[:80], "is_stale": True}
        return {
            "symbol": symbol,
            "status": value["freshness_status"],
            "delay_seconds": value["delay_seconds"],
            "is_stale": value["is_stale"],
            "exchange_timestamp": value["exchange_timestamp"],
            "received_at": value["received_at"],
        }

    def authenticate(self) -> bool:
        try:
            self._ensure_session()
            return self.session_valid
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)[:120]
            return False

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _headers(self, authenticated: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": self._client_ip,
            "X-ClientPublicIP": self._client_ip,
            "X-MACAddress": self._mac,
            "X-PrivateKey": self.api_key,
        }
        if authenticated and self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"
        return headers

    def _request(self, path: str, payload: dict[str, Any], authenticated: bool = True) -> dict[str, Any]:
        if time.monotonic() < self._rate_limited_until:
            raise AngelRateLimited("angel_rate_limited")
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            request = Request(
                f"{self.BASE_URL}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers=self._headers(authenticated),
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code in {429, 503}:
                    self._rate_limited_until = time.monotonic() + min(60, 5 * (attempt + 1))
                    last_error = AngelRateLimited(f"angel_rate_limited:{exc.code}")
                else:
                    last_error = AngelProviderError(f"angel_http_error:{exc.code}")
            except (URLError, TimeoutError, socket.timeout) as exc:
                last_error = AngelProviderError(f"angel_network_error:{type(exc).__name__}")
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = AngelProviderError(f"angel_invalid_response:{type(exc).__name__}")
            else:
                if body.get("status") is False or str(body.get("errorcode") or "").strip() not in {"", "SUCCESS"}:
                    message = str(body.get("message") or body.get("errorcode") or "angel_request_failed")
                    if "rate" in message.lower() or "exceed" in message.lower():
                        self._rate_limited_until = time.monotonic() + 30
                        raise AngelRateLimited(f"angel_rate_limited:{message[:60]}")
                    self._last_error = message[:120]
                    raise AngelProviderError(message[:160])
                self._last_error = None
                return body
            if attempt + 1 < self.max_attempts:
                time.sleep(min(2.0, 0.4 * (2 ** attempt)))
        self._last_error = str(last_error)[:120] if last_error else None
        raise last_error or AngelProviderError("angel_request_failed")


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _best_depth(quote: dict[str, Any], side: str) -> float | None:
    depth = quote.get("depth") or {}
    rows = depth.get(side) or []
    if rows and isinstance(rows, list) and isinstance(rows[0], dict):
        return _float(rows[0].get("price"))
    return None


def _parse_angel_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=IST)
    text = str(value).strip()
    for pattern in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=IST).astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


# Read-only guarantee: assert at import time that no trading surface exists.
_FORBIDDEN = ("place_order", "modify_order", "cancel_order", "buy", "sell", "square_off", "exit_position")
assert not any(hasattr(AngelReadOnlyProvider, name) for name in _FORBIDDEN), "Angel provider must remain read-only"
