"""Read-only Angel One SmartAPI market-data provider.

This module deliberately has no order placement, modification, cancellation,
buy, sell, or automated-trading methods.
"""
from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from instrument_master import resolve_instrument


class AngelProviderError(RuntimeError):
    """Angel One SmartAPI is unavailable or not configured."""


class AngelReadOnlyProvider:
    name = "angelone_primary"
    signal_capable = False
    BASE_URL = "https://apiconnect.angelone.in"

    def __init__(self, timeout: int = 15) -> None:
        self.api_key = os.getenv("ANGEL_API_KEY", "").strip()
        self.client_id = os.getenv("ANGEL_CLIENT_ID", "").strip()
        self.pin = os.getenv("ANGEL_PIN", "").strip()
        self.totp = os.getenv("ANGEL_TOTP", "").strip()
        self.timeout = timeout
        self.jwt_token: Optional[str] = None

    @property
    def configured(self) -> bool:
        return all((self.api_key, self.client_id, self.pin, self.totp))

    @property
    def available(self) -> bool:
        return self.configured

    def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": self.configured,
            "connected": bool(self.jwt_token),
            "read_only": True,
        }

    def connect(self) -> dict[str, Any]:
        if not self.configured:
            raise AngelProviderError("Angel credentials are not configured.")
        response = self._request(
            "/rest/auth/angelbroking/user/v1/loginByPassword",
            {"clientcode": self.client_id, "password": self.pin, "totp": self.totp},
        )
        data = response.get("data") or {}
        self.jwt_token = data.get("jwtToken")
        if not self.jwt_token:
            raise AngelProviderError(response.get("message", "Angel login failed."))
        return data

    def get_live_price(self, symbol: str) -> float | None:
        return float(self.get_market_data(symbol)["price"])

    def get_market_data(self, symbol: str, **_: Any) -> dict[str, Any]:
        instrument = resolve_instrument(symbol)
        if not instrument:
            raise AngelProviderError("unverified_instrument")
        token = instrument.get("angel_token") or instrument.get("symboltoken")
        if not token:
            raise AngelProviderError("angel_symbol_token_not_configured")
        self._ensure_session()
        response = self._request(
            "/rest/secure/angelbroking/order/v1/getLtpData",
            {
                "exchange": instrument.get("exchange") or "NSE",
                "symboltoken": str(token),
                "tradingsymbol": instrument["trading_symbol"],
            },
        )
        data = response.get("data") or {}
        price = data.get("ltp")
        if price is None:
            raise AngelProviderError("angel_ltp_missing")
        return {
            **instrument,
            "price": float(price),
            "timestamp": datetime.now(timezone.utc),
            "provider": self.name,
            "is_live": True,
            "is_delayed": False,
            "is_stale": False,
            "verified": True,
            "read_only": True,
        }

    def get_many(self, symbols: list[str], **kwargs: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            try:
                result[symbol] = self.get_market_data(symbol, **kwargs)
            except Exception:
                continue
        return result

    def close(self) -> None:
        if self.jwt_token:
            try:
                self._request(
                    "/rest/secure/angelbroking/user/v1/logout",
                    {"clientcode": self.client_id},
                )
            except Exception:
                pass
        self.jwt_token = None

    def _ensure_session(self) -> None:
        if not self.jwt_token:
            self.connect()

    @staticmethod
    def _local_ip() -> str:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"

    def _public_ip(self, fallback: str) -> str:
        try:
            request = Request("https://api.ipify.org", headers={"User-Agent": "SHIVAY-AI/1.0"})
            with urlopen(request, timeout=min(self.timeout, 5)) as response:
                value = response.read(64).decode("utf-8").strip()
            return value or fallback
        except (URLError, TimeoutError, OSError):
            return fallback

    @staticmethod
    def _mac_address() -> str:
        value = uuid.getnode()
        return ":".join(f"{(value >> shift) & 0xFF:02x}" for shift in range(40, -1, -8))

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        local_ip = self._local_ip()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-PrivateKey": self.api_key,
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": local_ip,
            "X-ClientPublicIP": self._public_ip(local_ip),
            "X-MACAddress": self._mac_address(),
        }
        if self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"
        request = Request(
            f"{self.BASE_URL}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise AngelProviderError(f"Angel SmartAPI request failed: HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise AngelProviderError(f"Angel SmartAPI request failed: {type(exc).__name__}") from exc
        if not isinstance(body, dict):
            raise AngelProviderError("Angel SmartAPI returned an invalid response.")
        return body
