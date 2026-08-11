"""Read-only Angel One SmartAPI market-data provider.

This module intentionally contains no order-placement, buy, sell, modify,
cancel, or automated-trading functionality.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AngelProviderError(RuntimeError):
    """Raised when Angel One SmartAPI cannot return market data."""


class AngelReadOnlyProvider:
    BASE_URL = "https://apiconnect.angelone.in"

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        pin: Optional[str] = None,
        totp: Optional[str] = None,
        timeout: int = 15,
    ) -> None:
        self.api_key = api_key or os.getenv("ANGEL_API_KEY")
        self.client_id = client_id or os.getenv("ANGEL_CLIENT_ID")
        self.pin = pin or os.getenv("ANGEL_PIN")
        self.totp = totp or os.getenv("ANGEL_TOTP")
        self.timeout = timeout
        self.jwt_token: Optional[str] = None

    @property
    def configured(self) -> bool:
        return all((self.api_key, self.client_id, self.pin, self.totp))

    def connect(self) -> dict[str, Any]:
        if not self.configured:
            raise AngelProviderError(
                "Set ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN, and ANGEL_TOTP."
            )
        response = self._request(
            "/rest/auth/angelbroking/user/v1/loginByPassword",
            {
                "clientcode": self.client_id,
                "password": self.pin,
                "totp": self.totp,
            },
        )
        data = response.get("data") or {}
        self.jwt_token = data.get("jwtToken")
        if not self.jwt_token:
            raise AngelProviderError(response.get("message", "Angel login failed."))
        return data

    def ltp(self, exchange: str, symbol_token: str, trading_symbol: str) -> dict[str, Any]:
        """Return last-traded-price data only; this does not place trades."""
        self._require_session()
        return self._request(
            "/rest/secure/angelbroking/order/v1/getLtpData",
            {
                "exchange": exchange,
                "symboltoken": str(symbol_token),
                "tradingsymbol": trading_symbol,
            },
        )

    def logout(self) -> None:
        if self.jwt_token:
            self._request(
                "/rest/secure/angelbroking/user/v1/logout",
                {"clientcode": self.client_id},
            )
        self.jwt_token = None

    def _require_session(self) -> None:
        if not self.jwt_token:
            self.connect()

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-PrivateKey": self.api_key or "",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
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
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AngelProviderError(f"Angel SmartAPI request failed: {exc}") from exc
        if body.get("status") is False:
            raise AngelProviderError(body.get("message", "Angel SmartAPI request failed."))
        return body
