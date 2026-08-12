"""Production provider manager: Angel One primary, TradingView emergency backup.

Runtime order is strictly:

    angelone_primary -> tradingview_alert_bridge -> NO FRESH DATA / NO SIGNAL

Every other adapter in this repository is archived: the module files remain for
reuse, but they are never imported here, never registered, never ranked, never
health-reported and never used for scanner decisions or automatic startup.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from angel_provider import AngelReadOnlyProvider
from data_quality import assess_market_data, validate_instrument_contract
from provider_failover import ProviderUnavailable, execute_with_failover
from provider_health import circuit_open, get_provider_health, rank_provider_names, record_failure, record_success
from tradingview_bridge import TradingViewBridgeProvider

LOGGER = logging.getLogger("shivay.provider.manager")

NO_FRESH_DATA = "NO FRESH DATA / NO SIGNAL"


class ProviderManager:
    """Exactly two production providers, Angel One always first."""

    # Production runtime order. Angel One is the authoritative primary and the
    # authenticated TradingView bridge is the only emergency backup.
    DEFAULT_PRIORITY = ("angelone_primary", "tradingview_alert_bridge")

    # Adapters kept in the repository for reuse but excluded from production.
    ARCHIVED_PROVIDERS = (
        "groww_primary", "upstox_primary", "dhan_primary", "shoonya_primary",
        "truedata_primary", "gdfl_primary", "fyers_primary", "market_hub",
        "nse_temporary", "mcx_temporary", "tvkit_ohlcv", "yahoo_emergency",
    )

    @classmethod
    def _base_priority(cls) -> dict[str, int]:
        """Angel always outranks the backup; configuration cannot reorder them."""
        return {name: (len(cls.DEFAULT_PRIORITY) - index) * 1000 for index, name in enumerate(cls.DEFAULT_PRIORITY)}

    def __init__(self) -> None:
        self.angel = AngelReadOnlyProvider()
        self.tradingview = TradingViewBridgeProvider()
        self.providers: list[Any] = [self.angel, self.tradingview]
        self._assert_production_architecture()

    def _assert_production_architecture(self) -> None:
        names = [provider.name for provider in self.providers]
        if names != list(self.DEFAULT_PRIORITY):
            raise RuntimeError(f"Unsafe provider registration rejected: {names}")
        if len(names) != 2:
            raise RuntimeError("Exactly two production providers are permitted")
        forbidden = ("place_order", "modify_order", "cancel_order", "square_off")
        for provider in self.providers:
            if any(hasattr(provider, name) for name in forbidden):
                raise RuntimeError(f"Order surface rejected on provider {provider.name}")

    # ------------------------------------------------------------------
    # ranking
    # ------------------------------------------------------------------
    def _ranked(self, verified_only: bool = False) -> list[Any]:
        values = [p for p in self.providers if not verified_only or bool(getattr(p, "signal_capable", True))]
        by_name = {p.name: p for p in values}
        ordered = rank_provider_names(list(by_name), self._base_priority())
        # Angel must never be outranked by the backup while it is usable.
        if self.angel.name in by_name and not circuit_open(self.angel.name) and self.angel.available:
            ordered = [self.angel.name] + [name for name in ordered if name != self.angel.name]
        return [by_name[name] for name in ordered]

    @staticmethod
    def _verified(value: Mapping[str, Any], symbol: str) -> tuple[bool, dict[str, Any]]:
        quality = assess_market_data(value)
        errors = list(quality.get("errors", []))
        errors.extend(validate_instrument_contract(value, symbol))
        quality["errors"] = sorted(set(errors))
        quality["valid"] = not quality["errors"]
        quality["status"] = "GOOD" if quality["valid"] else "REJECTED"
        valid = bool(
            quality["valid"]
            and value.get("verified")
            and value.get("is_live")
            and not value.get("is_delayed")
            and not value.get("is_stale")
        )
        return valid, quality

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def get_market_data(self, symbol: str, **kwargs) -> dict[str, Any]:
        return execute_with_failover(self._ranked(), lambda provider: provider.get_market_data(symbol, **kwargs))[0]

    def get_verified_market_data(self, symbol: str, **kwargs) -> dict[str, Any]:
        def operation(provider):
            value = provider.get_market_data(symbol, **kwargs)
            valid, quality = self._verified(value, symbol)
            if not valid:
                raise ProviderUnavailable("provider_data_failed_verification:" + ",".join(quality["errors"][:4]))
            value = dict(value)
            value["data_quality"] = quality
            return value

        return execute_with_failover(self._ranked(True), operation)[0]

    def get_live_price(self, symbol: str) -> float | None:
        return execute_with_failover(self._ranked(), lambda provider: provider.get_live_price(symbol))[0]

    def get_many(self, symbols: list[str], **kwargs) -> dict[str, dict[str, Any]]:
        pending = list(dict.fromkeys(symbols))
        result: dict[str, dict[str, Any]] = {}
        for provider in self._ranked():
            if not pending:
                break
            try:
                batch = provider.get_many(pending, **kwargs) if callable(getattr(provider, "get_many", None)) else {s: provider.get_market_data(s, **kwargs) for s in pending}
                result.update({symbol: value for symbol, value in batch.items() if value is not None})
                pending = [symbol for symbol in pending if symbol not in result]
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("Bulk provider failed over: %s (%s)", provider.name, type(error).__name__)
        return result

    def get_verified_many(self, symbols: list[str], **kwargs) -> dict[str, dict[str, Any]]:
        pending = list(dict.fromkeys(symbols))
        result: dict[str, dict[str, Any]] = {}
        for provider in self._ranked(True):
            if not pending:
                break
            if circuit_open(provider.name) or not bool(getattr(provider, "available", True)):
                continue
            started = time.monotonic()
            try:
                batch = provider.get_many(pending, **kwargs) if callable(getattr(provider, "get_many", None)) else {s: provider.get_market_data(s, **kwargs) for s in pending}
                scores = []
                for symbol, value in batch.items():
                    valid, quality = self._verified(value, symbol)
                    if valid:
                        normalized = dict(value)
                        normalized["data_quality"] = quality
                        result[symbol] = normalized
                        scores.append(quality)
                if scores:
                    record_success(provider.name, (time.monotonic() - started) * 1000, {
                        key: sum(float(item.get(key, 0)) for item in scores) / len(scores)
                        for key in ("freshness_score", "completeness_score", "consistency_score")
                    })
                else:
                    record_failure(provider.name, "validation")
                pending = [symbol for symbol in pending if symbol not in result]
            except Exception as error:  # noqa: BLE001
                record_failure(provider.name, "permanent" if isinstance(error, (ValueError, PermissionError)) else "temporary")
                LOGGER.warning("Verified provider unavailable: %s (%s)", provider.name, type(error).__name__)
        return result

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def active_provider_names(self) -> list[str]:
        return [provider.name for provider in self.providers]

    def status(self) -> dict[str, Any]:
        health = get_provider_health()
        available = [p.name for p in self._ranked(True) if bool(getattr(p, "available", False))]
        healthy = [name for name in available if health.get(name, {}).get("status") == "HEALTHY"]
        primary = healthy[0] if healthy else None
        configured = available[0] if available else None
        return {
            "selected_primary": primary,
            "configured_primary": configured,
            "selected_secondary": healthy[1] if len(healthy) > 1 else None,
            "active_provider": primary or (configured or NO_FRESH_DATA),
            "mode": "PRIMARY" if primary == self.angel.name else "EMERGENCY_BACKUP" if primary else "NO_FRESH_DATA",
            "verified_providers": healthy,
            "providers": self.active_provider_names(),
            "active_provider_count": len(self.providers),
            "angel_configured": self.angel.configured,
            "angel": self.angel.health_check(),
            "backup": "tradingview_alert_bridge",
            "backup_configured": bool(self.tradingview.available),
            "tradingview_configured": bool(self.tradingview.available),
            "fallback": "tradingview_alert_bridge_only",
            "no_data_decision": NO_FRESH_DATA,
            "archived_providers": list(self.ARCHIVED_PROVIDERS),
            "supported_provider_priority": list(self.DEFAULT_PRIORITY),
            "health": health,
        }

    def close(self) -> bool:
        for provider in self.providers:
            try:
                provider.close()
            except Exception:  # noqa: BLE001
                pass
        return True


_MANAGER: ProviderManager | None = None


def get_provider_manager() -> ProviderManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ProviderManager()
    return _MANAGER


def reset_provider_manager() -> None:
    global _MANAGER
    if _MANAGER is not None:
        try:
            _MANAGER.close()
        except Exception:  # noqa: BLE001
            pass
    _MANAGER = None


def get_provider_status() -> dict[str, Any]:
    return get_provider_manager().status()


def assert_production_providers() -> list[str]:
    """Startup assertion: exactly two providers with Angel One first."""
    names = get_provider_manager().active_provider_names()
    if names != list(ProviderManager.DEFAULT_PRIORITY):
        raise RuntimeError(f"Unsafe provider architecture: {names}")
    return names
