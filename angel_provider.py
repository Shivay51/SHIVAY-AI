"""Read-only Angel One SmartAPI market-data provider (runtime primary).

This module deliberately has no order placement, modification, cancellation,
buy, sell, or automated-trading methods. It supplies market data only:

* automatic TOTP login and session refresh (``angel_totp``)
* instrument-master / token lookup for current NSE F&O and MCX contracts
* live LTP / OHLC / full quotes with volume and open interest when published
* 5m / 15m / 30m / 60m historical candles
* freshness + timestamp validation, retry, reconnect and safe failure
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import angel_instruments
from angel_totp import TOTPError, generate_totp, is_valid_secret
from data_quality import assess_market_data, validate_candles

LOGGER = logging.getLogger("shivay.angel")
IST = ZoneInfo("Asia/Kolkata")

# Angel historical-candle interval codes keyed by SHIVAY AI interval strings.
INTERVAL_CODES = {
    "1m": "ONE_MINUTE",
    "3m": "THREE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "60m": "ONE_HOUR",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}
SUPPORTED_SIGNAL_INTERVALS = ("5m", "15m", "30m", "60m")
PERIOD_DAYS = {"1d": 1, "2d": 2, "5d": 5, "1w": 7, "1mo": 30, "3mo": 90}


class AngelProviderError(RuntimeError):
    """Angel One SmartAPI is unavailable, unauthenticated or not configured."""


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _series(values: list[float]) -> Any:
    """Return a pandas Series when pandas is present, else the plain list."""
    try:
        import pandas as pd

        return pd.Series(values)
    except Exception:  # pragma: no cover - pandas is a hard requirement in prod
        return values


class AngelReadOnlyProvider:
    """Angel One SmartAPI market-data provider. Read-only by construction."""

    name = "angelone_primary"
    signal_capable = True
    BASE_URL = "https://apiconnect.angelone.in"
    SESSION_TTL_SECONDS = 6 * 60 * 60
    MAX_ATTEMPTS = 3

    def __init__(self, timeout: int = 15) -> None:
        self.api_key = os.getenv("ANGEL_API_KEY", "").strip()
        self.client_id = os.getenv("ANGEL_CLIENT_ID", "").strip()
        self.pin = os.getenv("ANGEL_PIN", "").strip()
        self.totp_secret = os.getenv("ANGEL_TOTP_SECRET", "").strip()
        # Legacy single-use code retained for backwards compatibility only.
        self.static_totp = os.getenv("ANGEL_TOTP", "").strip()
        self.timeout = timeout
        self.jwt_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.feed_token: Optional[str] = None
        self.session_started_at: float = 0.0
        self.last_error: Optional[str] = None
        self.login_count = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ status

    @property
    def credentials_present(self) -> bool:
        return all((self.api_key, self.client_id, self.pin))

    @property
    def totp_ready(self) -> bool:
        return is_valid_secret(self.totp_secret) or bool(self.static_totp)

    @property
    def configured(self) -> bool:
        return self.credentials_present and self.totp_ready

    @property
    def available(self) -> bool:
        return self.configured

    @property
    def session_valid(self) -> bool:
        return bool(self.jwt_token) and (time.time() - self.session_started_at) < self.SESSION_TTL_SECONDS

    def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": self.configured,
            "credentials_present": self.credentials_present,
            "totp_automation": is_valid_secret(self.totp_secret),
            "connected": bool(self.jwt_token),
            "session_valid": self.session_valid,
            "session_age_seconds": round(time.time() - self.session_started_at, 1)
            if self.session_started_at
            else None,
            "login_count": self.login_count,
            "read_only": True,
            "signal_capable": self.signal_capable,
            "supported_intervals": list(SUPPORTED_SIGNAL_INTERVALS),
            "instrument_master": angel_instruments.master_status(),
            "last_error": self.last_error,
            "status": "HEALTHY" if self.session_valid else "CONFIGURED" if self.configured else "DISABLED",
        }

    # ---------------------------------------------------------------- session

    def _current_totp(self) -> str:
        if is_valid_secret(self.totp_secret):
            try:
                return generate_totp(self.totp_secret)
            except TOTPError as exc:
                raise AngelProviderError(f"angel_totp_failed: {exc}") from exc
        if self.static_totp:
            return self.static_totp
        raise AngelProviderError("angel_totp_not_configured")

    def connect(self) -> dict[str, Any]:
        """Log in with an automatically generated TOTP and store session tokens."""
        with self._lock:
            if not self.credentials_present:
                raise AngelProviderError("angel_credentials_not_configured")
            payload = {
                "clientcode": self.client_id,
                "password": self.pin,
                "totp": self._current_totp(),
            }
            response = self._request(
                "/rest/auth/angelbroking/user/v1/loginByPassword", payload, authenticated=False
            )
            data = response.get("data") or {}
            token = data.get("jwtToken")
            if not token:
                self.last_error = str(response.get("message") or "angel_login_failed")
                raise AngelProviderError(self.last_error)
            self.jwt_token = str(token).removeprefix("Bearer ").strip()
            self.refresh_token = data.get("refreshToken")
            self.feed_token = data.get("feedToken")
            self.session_started_at = time.time()
            self.login_count += 1
            self.last_error = None
            LOGGER.info("Angel One session established (read-only, login #%s)", self.login_count)
            return data

    def refresh_session(self) -> bool:
        """Renew the JWT with the refresh token, falling back to a fresh login."""
        with self._lock:
            if self.refresh_token:
                try:
                    response = self._request(
                        "/rest/auth/angelbroking/jwt/v1/generateTokens",
                        {"refreshToken": self.refresh_token},
                    )
                    data = response.get("data") or {}
                    token = data.get("jwtToken")
                    if token:
                        self.jwt_token = str(token).removeprefix("Bearer ").strip()
                        self.refresh_token = data.get("refreshToken") or self.refresh_token
                        self.feed_token = data.get("feedToken") or self.feed_token
                        self.session_started_at = time.time()
                        self.last_error = None
                        return True
                except AngelProviderError as exc:
                    LOGGER.warning("Angel token refresh failed (%s); re-logging in", exc)
            self.jwt_token = None
            self.connect()
            return True

    def _ensure_session(self) -> None:
        if not self.session_valid:
            if self.jwt_token or self.refresh_token:
                self.refresh_session()
            else:
                self.connect()

    def close(self) -> None:
        with self._lock:
            if self.jwt_token:
                try:
                    self._request(
                        "/rest/secure/angelbroking/user/v1/logout", {"clientcode": self.client_id}
                    )
                except Exception:
                    pass
            self.jwt_token = None
            self.refresh_token = None
            self.feed_token = None
            self.session_started_at = 0.0

    # ------------------------------------------------------------ instruments

    def resolve_instrument(self, symbol: str) -> dict[str, Any] | None:
        try:
            return angel_instruments.resolve_contract(symbol)
        except angel_instruments.AngelInstrumentError as exc:
            self.last_error = str(exc)
            return None

    def refresh_instruments(self) -> dict[str, Any]:
        return angel_instruments.refresh()

    def _contract(self, symbol: str) -> dict[str, Any]:
        contract = self.resolve_instrument(symbol)
        if not contract:
            raise AngelProviderError("unverified_instrument")
        if not contract.get("angel_token"):
            raise AngelProviderError("angel_symbol_token_not_configured")
        return contract

    # ------------------------------------------------------------------ quotes

    def get_quote(self, symbol: str, mode: str = "FULL") -> dict[str, Any]:
        """Return the raw Angel quote payload for one symbol."""
        contract = self._contract(symbol)
        exchange = contract.get("angel_exchange") or contract.get("exchange") or "NSE"
        response = self._authenticated_request(
            "/rest/secure/angelbroking/market/v1/quote/",
            {"mode": mode.upper(), "exchangeTokens": {exchange: [str(contract["angel_token"])]}},
        )
        data = response.get("data") or {}
        fetched = data.get("fetched") or []
        if not fetched:
            unfetched = data.get("unfetched") or []
            reason = (unfetched[0].get("message") if unfetched else None) or "angel_quote_missing"
            raise AngelProviderError(str(reason))
        return {"contract": contract, "quote": fetched[0]}

    def get_candles(
        self, symbol: str, interval: str = "15m", period: str = "5d"
    ) -> list[dict[str, Any]]:
        """Return validated OHLCV candles for a supported interval."""
        code = INTERVAL_CODES.get(str(interval).strip().lower())
        if not code:
            raise AngelProviderError(f"angel_unsupported_interval:{interval}")
        contract = self._contract(symbol)
        exchange = contract.get("angel_exchange") or contract.get("exchange") or "NSE"
        days = PERIOD_DAYS.get(str(period).strip().lower(), 5)
        end = datetime.now(IST)
        start = end - timedelta(days=max(1, days))
        response = self._authenticated_request(
            "/rest/secure/angelbroking/historical/v1/getCandleData",
            {
                "exchange": exchange,
                "symboltoken": str(contract["angel_token"]),
                "interval": code,
                "fromdate": start.strftime("%Y-%m-%d %H:%M"),
                "todate": end.strftime("%Y-%m-%d %H:%M"),
            },
        )
        rows = response.get("data") or []
        candles: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            stamp = self._parse_candle_stamp(row[0])
            values = [_to_float(item) for item in row[1:6]]
            if stamp is None or any(item is None for item in values):
                continue
            candles.append(
                {
                    "timestamp": stamp,
                    "open": values[0],
                    "high": values[1],
                    "low": values[2],
                    "close": values[3],
                    "volume": values[4],
                }
            )
        if not candles:
            raise AngelProviderError("angel_candles_empty")
        candles.sort(key=lambda item: item["timestamp"])
        quality = validate_candles(candles)
        if not quality["valid"]:
            raise AngelProviderError("angel_invalid_candles:" + ",".join(quality["errors"][:3]))
        return candles

    @staticmethod
    def _parse_candle_stamp(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(timezone.utc)

    def get_market_data(
        self, symbol: str, period: str = "5d", interval: str = "15m", **_: Any
    ) -> dict[str, Any]:
        """Return a fully normalized, freshness-validated market-data payload."""
        bundle = self.get_quote(symbol, "FULL")
        contract, quote = bundle["contract"], bundle["quote"]

        price = _to_float(quote.get("ltp"))
        if price is None or price <= 0:
            raise AngelProviderError("angel_ltp_missing")

        try:
            candles = self.get_candles(symbol, interval=interval, period=period)
        except AngelProviderError as exc:
            LOGGER.warning("Angel candles unavailable for %s (%s)", symbol, exc)
            candles = []

        timestamp = self._quote_timestamp(quote)
        day_high = _to_float(quote.get("high")) or (max(c["high"] for c in candles) if candles else price)
        day_low = _to_float(quote.get("low")) or (min(c["low"] for c in candles) if candles else price)
        open_value = _to_float(quote.get("open")) or (candles[0]["open"] if candles else price)
        volume = _to_float(quote.get("tradeVolume")) or (candles[-1]["volume"] if candles else 0.0)

        try:
            interval_minutes = int(str(interval).lower().replace("m", "").replace("1h", "60") or 15)
        except ValueError:
            interval_minutes = 15

        result: dict[str, Any] = {
            **contract,
            "price": round(price, 2),
            "open_value": open_value,
            "day_high": day_high,
            "day_low": day_low,
            "previous_close": _to_float(quote.get("close")),
            "latest_volume": volume,
            "total_traded_volume": _to_float(quote.get("tradeVolume")),
            "open_interest": _to_float(quote.get("opnInterest")),
            "open_interest_change": _to_float(quote.get("netChangeopnInterest")),
            "upper_circuit": _to_float(quote.get("upperCircuit")),
            "lower_circuit": _to_float(quote.get("lowerCircuit")),
            "timestamp": timestamp,
            "provider": self.name,
            "is_live": True,
            "is_delayed": False,
            "verified": True,
            "read_only": True,
            "interval": str(interval).lower(),
            "interval_minutes": interval_minutes,
            "candles": candles,
        }
        if candles:
            result.update(
                open=_series([c["open"] for c in candles]),
                high=_series([c["high"] for c in candles]),
                low=_series([c["low"] for c in candles]),
                close=_series([c["close"] for c in candles]),
                volume=_series([c["volume"] for c in candles]),
            )

        freshness = assess_market_data(result)
        result.update(
            delay_seconds=freshness["delay_seconds"],
            is_stale=bool(freshness["is_stale"]),
            freshness_status=freshness["status"],
            data_quality=freshness,
        )
        if result["is_stale"]:
            raise AngelProviderError("angel_stale_data")
        return result

    def get_multi_timeframe(
        self, symbol: str, intervals: tuple[str, ...] = SUPPORTED_SIGNAL_INTERVALS
    ) -> dict[str, list[dict[str, Any]]]:
        """Return candles for every requested timeframe, skipping failures."""
        output: dict[str, list[dict[str, Any]]] = {}
        for interval in intervals:
            try:
                output[interval] = self.get_candles(symbol, interval=interval)
            except AngelProviderError as exc:
                LOGGER.warning("Angel %s candles unavailable for %s (%s)", interval, symbol, exc)
        return output

    def get_live_price(self, symbol: str) -> float | None:
        contract = self._contract(symbol)
        exchange = contract.get("angel_exchange") or contract.get("exchange") or "NSE"
        response = self._authenticated_request(
            "/rest/secure/angelbroking/order/v1/getLtpData",
            {
                "exchange": exchange,
                "symboltoken": str(contract["angel_token"]),
                "tradingsymbol": contract["trading_symbol"],
            },
        )
        price = _to_float((response.get("data") or {}).get("ltp"))
        if price is None:
            raise AngelProviderError("angel_ltp_missing")
        return round(price, 2)

    def get_many(self, symbols: list[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            try:
                result[symbol] = self.get_market_data(symbol, **kwargs)
            except Exception as error:
                LOGGER.debug("Angel skipped %s (%s)", symbol, type(error).__name__)
                continue
        return result

    @staticmethod
    def _quote_timestamp(quote: dict[str, Any]) -> datetime:
        from data_quality import parse_timestamp

        for field in ("exchFeedTime", "exchTradeTime", "lastTradeTime"):
            stamp = parse_timestamp(quote.get(field))
            if stamp is not None:
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=IST)
                return stamp.astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    # ----------------------------------------------------------------- request

    def _authenticated_request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Request with session guarantee, one re-auth retry and capped backoff."""
        last: Exception | None = None
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                self._ensure_session()
                return self._request(path, payload)
            except AngelProviderError as exc:
                last = exc
                message = str(exc).lower()
                unauthorized = any(
                    token in message
                    for token in ("401", "403", "invalid token", "token expired", "unauthor", "session")
                )
                if unauthorized:
                    self.jwt_token = None
                    self.session_started_at = 0.0
                if attempt + 1 >= self.MAX_ATTEMPTS:
                    break
                time.sleep(min(2.0, 0.4 * (2 ** attempt)))
        self.last_error = str(last)
        raise AngelProviderError(str(last or "angel_request_failed"))

    def _request(
        self, path: str, payload: dict[str, Any], authenticated: bool = True
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-PrivateKey": self.api_key,
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": os.getenv("ANGEL_LOCAL_IP", "127.0.0.1"),
            "X-ClientPublicIP": os.getenv("ANGEL_PUBLIC_IP", "127.0.0.1"),
            "X-MACAddress": os.getenv("ANGEL_MAC_ADDRESS", "00:00:00:00:00:00"),
        }
        if authenticated and self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"
        request = Request(
            f"{self.BASE_URL}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed host
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise AngelProviderError(f"angel_http_{exc.code}: {exc.reason}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise AngelProviderError(f"angel_request_failed: {exc}") from exc
        if body.get("status") is False or body.get("success") is False:
            raise AngelProviderError(str(body.get("message") or "angel_request_rejected"))
        return body


_PROVIDER: AngelReadOnlyProvider | None = None
_PROVIDER_LOCK = threading.RLock()


def get_angel_provider() -> AngelReadOnlyProvider:
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is None:
            _PROVIDER = AngelReadOnlyProvider()
        return _PROVIDER


def reset_angel_provider() -> None:
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is not None:
            try:
                _PROVIDER.close()
            except Exception:
                pass
        _PROVIDER = None


def angel_health() -> dict[str, Any]:
    return get_angel_provider().health_check()
