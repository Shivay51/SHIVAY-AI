"""Read-only client for the official Global Datafeeds REST/WebSocket APIs."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

import requests


class GDFLError(RuntimeError):
    pass


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


class GDFLClient:
    """Official read-only market-data surface. No trading methods are exposed."""

    def __init__(self, session: requests.Session | None = None):
        self.api_key = os.getenv("GDFL_API_KEY", "").strip()
        endpoint = os.getenv("GDFL_ENDPOINT", "").strip()
        port = os.getenv("GDFL_PORT", "").strip()
        if endpoint and "://" not in endpoint:
            endpoint = "https://" + endpoint
        parsed = urlparse(endpoint) if endpoint else None
        if parsed and parsed.scheme not in {"http", "https"}:
            endpoint = ""
        if endpoint and port and parsed and parsed.port is None:
            endpoint = endpoint.rstrip("/") + ":" + port
        self.base_url = endpoint.rstrip("/")
        self.enabled = _enabled("ENABLE_GDFL")
        self.trial_expires_at = self._expiry(os.getenv("GDFL_TRIAL_EXPIRES_AT", ""))
        self.trial_expired = bool(self.trial_expires_at and self.trial_expires_at <= datetime.now(timezone.utc))
        self.configured = bool(self.enabled and self.api_key and self.base_url and not self.trial_expired)
        self.session = session or requests.Session()
        self._lock = threading.RLock()
        self._last_request = 0.0

    @staticmethod
    def _expiry(value: str) -> datetime | None:
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    def _get(self, function: str, **params: Any) -> Any:
        if not self.configured:
            raise GDFLError("GDFL is not configured")
        with self._lock:
            delay = 0.20 - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()
            try:
                response = self.session.get(
                    f"{self.base_url}/{function}/",
                    params={"accesskey": self.api_key, **params}, timeout=(4, 10),
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                value = response.json()
            except (requests.RequestException, ValueError) as error:
                raise GDFLError(f"GDFL {function} request failed ({type(error).__name__})") from None
        if isinstance(value, Mapping) and value.get("Complete") is False:
            raise GDFLError(f"GDFL {function} rejected the request")
        return value

    def search_instruments(self, search: str, exchange: str, instrument_type: str) -> Any:
        return self._get("GetInstrumentsOnSearch", Search=search, Exchange=exchange,
                         InstrumentType=instrument_type, OnlyActive="true", detailedInfo="true")

    def get_quote(self, exchange: str, identifier: str) -> Any:
        return self._get("GetLastQuote", exchange=exchange, instrumentIdentifier=identifier)

    def get_history(self, exchange: str, identifier: str, period: int = 5, bars: int = 160) -> Any:
        return self._get("GetHistory", exchange=exchange, instrumentIdentifier=identifier,
                         periodicity="MINUTE", period=max(1, int(period)), max=max(20, min(int(bars), 1000)))

    def verify_websocket(self) -> bool:
        if not self.configured:
            return False
        try:
            import websocket
            parsed = urlparse(self.base_url)
            scheme = "wss" if parsed.scheme == "https" else "ws"
            ws = websocket.create_connection(f"{scheme}://{parsed.netloc}", timeout=6)
            try:
                ws.send(json.dumps({"MessageType": "Authenticate", "Password": self.api_key}))
                reply = json.loads(ws.recv())
                return bool(reply.get("Complete") and reply.get("MessageType") == "AuthenticateResult")
            finally:
                ws.close()
        except Exception:
            return False

    def health(self, probe: bool = False) -> dict[str, Any]:
        result = {"configured": self.configured, "rest": False, "websocket": False,
                  "trial_expired": self.trial_expired,
                  "trial_expires_at": self.trial_expires_at.isoformat() if self.trial_expires_at else None,
                  "checked_at": datetime.now(timezone.utc).isoformat()}
        if probe and self.configured:
            try:
                self._get("GetExchanges"); result["rest"] = True
            except GDFLError:
                pass
            result["websocket"] = self.verify_websocket()
        return result

    def close(self) -> None:
        self.session.close()
