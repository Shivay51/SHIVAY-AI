"""Safe, read-only capability probe for the audited official TradeX SDK."""

from __future__ import annotations

import contextlib
import io
from datetime import datetime, timezone
from typing import Any

from market_hub_client import MarketHubClient


FORBIDDEN = {
    "NewOrder", "ModifyOrder", "CancelOrder", "CancelAllOrders",
    "NewGTTOrder", "ModifyGTTOrder", "CancelGTTOrder", "ExecuteBasket",
}
UNAVAILABLE_MARKET_CAPABILITIES = (
    "instrument_master", "symbol_search", "quote", "ltp", "ohlc",
    "historical_candles", "live_market_data", "market_websocket",
    "nse_futures_market_data", "mcx_gold_market_data", "mcx_silver_market_data",
)


def _status(value: Any) -> str:
    return str(getattr(value, "status", "UNKNOWN"))[:32]


def _success(value: Any) -> bool:
    return _status(value).upper() in {"OK", "200", "SUCCESS", "TRUE"}


def _schema(value: Any) -> list[str]:
    if hasattr(value, "get_dict"):
        try:
            value = value.get_dict()
        except Exception:
            return [type(value).__name__]
    return sorted(str(key) for key in value) if isinstance(value, dict) else [type(value).__name__]


def run_probe(client: MarketHubClient | None = None, perform_login: bool = False) -> dict[str, Any]:
    client = client or MarketHubClient()
    timestamp = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    authenticated = False

    def execute(capability: str, endpoint: str, function: Any) -> bool:
        row = {
            "capability": capability, "endpoint": endpoint, "method": "POST",
            "supported": True, "verified": False, "timestamp": timestamp,
            "authentication": "not_tested", "status": None,
            "response_schema": [], "error_category": None,
        }
        if perform_login and client.available:
            try:
                # The SDK writes informational dotenv messages; suppress them because
                # provider probes must never emit credential-related output.
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    value = function()
                row.update(
                    verified=_success(value), authentication="accepted" if _success(value) else "rejected",
                    status=_status(value), response_schema=_schema(value),
                )
            except Exception as error:
                row.update(authentication="rejected_or_unavailable", error_category=type(error).__name__)
        rows.append(row)
        return bool(row["verified"])

    if perform_login and client.available:
        authenticated = execute("login", "Login", lambda: client.login(True))
        token_available = bool(getattr(client._sdk(), "token", None)) if authenticated else False
    else:
        execute("login", "Login", lambda: client.login(True))
        token_available = False

    if authenticated and token_available:
        execute("user_profile", "UserProfile", client.get_user_profile)
        execute("exchange_status", "ExchangeStatus", client.get_exchange_status)
    else:
        for capability, endpoint in (("user_profile", "UserProfile"), ("exchange_status", "ExchangeStatus")):
            rows.append({
                "capability": capability, "endpoint": endpoint, "method": "POST",
                "supported": True, "verified": False, "timestamp": timestamp,
                "authentication": "not_tested", "status": None,
                "response_schema": [], "error_category": None,
            })

    for capability in UNAVAILABLE_MARKET_CAPABILITIES:
        rows.append({
            "capability": capability, "endpoint": "UNAVAILABLE_IN_AUDITED_SDK",
            "method": "N/A", "supported": False, "verified": False,
            "timestamp": timestamp, "authentication": "not_applicable",
            "status": None, "response_schema": [], "error_category": "unsupported_by_official_sdk",
        })
    return {
        "provider": "market_hub", "configured": client.configured,
        "sdk_available": client.available, "authenticated": authenticated,
        "token_available": token_available, "read_only": True,
        "forbidden_endpoints": sorted(FORBIDDEN), "results": rows,
    }


def probe_market_hub(perform_login: bool = False) -> dict[str, Any]:
    return run_probe(perform_login=perform_login)

