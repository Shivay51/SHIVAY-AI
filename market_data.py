"""Legacy-compatible market-data facade backed by verified providers.

This module intentionally contains no endpoint construction, credential store,
or broker-order functionality.  New code should normally import :mod:`data` or
:mod:`market_data_provider`; the ``MarketData`` class remains for compatibility
with older SHIVAY AI integrations.
"""
from __future__ import annotations

from typing import Any, Iterable

from provider_failover import ProviderUnavailable
from provider_manager import get_provider_manager, get_provider_status


class MarketData:
    """Provider-independent, read-only compatibility client."""

    def __init__(self) -> None:
        self._manager = get_provider_manager()

    @property
    def token(self) -> None:
        """Tokens are deliberately never exposed through the legacy facade."""
        return None

    def login(
        self,
        user: str = "",
        password: str = "",
        api_key: str = "",
        vendor: str = "",
        totp: str = "",
    ) -> dict[str, Any]:
        """Authenticate the configured read-only provider using ``.env`` only.

        Legacy arguments are accepted for call compatibility but are never read,
        retained, forwarded, or logged.  This prevents secrets from entering
        application call stacks and keeps one authoritative configuration path.
        """
        del user, password, api_key, vendor, totp
        client = getattr(getattr(self._manager, "shoonya", None), "client", None)
        if client is None:
            raise ProviderUnavailable("read_only_provider_unavailable")
        client.login()
        return client.health()

    def logout(self) -> bool:
        return bool(self._manager.close())

    def get_market_data(self, symbol: str, **kwargs: Any) -> dict[str, Any] | None:
        try:
            return self._manager.get_verified_market_data(symbol, **kwargs)
        except Exception:
            return None

    def get_live_price(self, symbol: str) -> float | None:
        value = self.get_market_data(symbol)
        return round(float(value["price"]), 2) if value else None

    def get_batch_market_data(
        self,
        symbols: Iterable[str],
        **kwargs: Any,
    ) -> dict[str, dict[str, Any]]:
        return self._manager.get_verified_many(list(symbols), **kwargs)

    def status(self) -> dict[str, Any]:
        return get_provider_status()

    def _request(self, *_args: Any, **_kwargs: Any) -> None:
        """Reject undocumented raw requests retained from the legacy interface."""
        raise ProviderUnavailable("raw_provider_requests_are_disabled")


market = MarketData()


def get_market_data(symbol: str, **kwargs: Any) -> dict[str, Any] | None:
    return market.get_market_data(symbol, **kwargs)


def get_live_price(symbol: str) -> float | None:
    return market.get_live_price(symbol)


def get_batch_market_data(
    symbols: Iterable[str],
    **kwargs: Any,
) -> dict[str, dict[str, Any]]:
    return market.get_batch_market_data(symbols, **kwargs)


__all__ = [
    "MarketData",
    "market",
    "get_market_data",
    "get_live_price",
    "get_batch_market_data",
]
