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
from datetime import datetime, timezone
from typing import Any, Mapping

from angel_provider import AngelReadOnlyProvider
from data_quality import assess_market_data, validate_instrument_contract
from provider_failover import ProviderUnavailable, execute_with_failover
from market_session import market_state, signals_allowed
from provider_cache import ProviderCache
from provider_health import circuit_open, get_provider_health, rank_provider_names, record_failure, record_success
from tradingview_bridge import TradingViewBridgeProvider

LOGGER = logging.getLogger("shivay.provider.manager")

NO_FRESH_DATA = "NO FRESH DATA / NO SIGNAL"

# Safe verified-read cache: short enough that a 15m scanner never trades on an
# earlier candle, long enough to protect provider rate limits.
CACHE_TTL_SECONDS = 45
MAX_CLOCK_SKEW_SECONDS = 90


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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
        self.cache = ProviderCache(ttl_seconds=CACHE_TTL_SECONDS, max_entries=512)
        # request key -> canonical contract cache key, so a cached read is only
        # reused for the exact provider, contract, exchange and timeframe.
        self._cache_index: dict[str, str] = {}
        self._assert_production_architecture()

    # ------------------------------------------------------------------
    # cache + freshness
    # ------------------------------------------------------------------
    @staticmethod
    def _cache_key(provider: str, symbol: str, value: Mapping[str, Any] | None = None, timeframe: Any = None) -> str:
        item = value or {}
        parts = (
            provider,
            str(symbol).upper(),
            str(item.get("instrument_key") or item.get("security_id") or "-"),
            str(item.get("exchange") or "-"),
            str(item.get("segment") or "-"),
            str(item.get("expiry") or "-"),
            str(item.get("interval_minutes") or timeframe or "-"),
        )
        return "|".join(parts)

    @staticmethod
    def _request_key(provider: str, symbol: str, timeframe: Any) -> str:
        return f"{provider}|{str(symbol).upper()}|{timeframe or '-'}"

    @staticmethod
    def _expected_market(symbol: str) -> tuple[str, str]:
        text = str(symbol).upper()
        if text.startswith("MCX") or text.startswith("COMEX"):
            return "MCX", "MCX_COMM"
        return "NSE", "NSE_FNO"

    @classmethod
    def _cache_entry_usable(cls, value: Mapping[str, Any]) -> bool:
        """Reject cached snapshots that became stale, skewed or incomplete."""
        if not value or value.get("is_stale") or not value.get("candles"):
            return False
        exchange_time = _timestamp(value.get("exchange_timestamp") or value.get("timestamp"))
        received = _timestamp(value.get("received_at"))
        now = datetime.now(timezone.utc)
        if exchange_time is None:
            return False
        age = (now - exchange_time).total_seconds()
        if age < -MAX_CLOCK_SKEW_SECONDS or age > max(CACHE_TTL_SECONDS * 4, 180):
            return False
        if received is not None and abs((now - received).total_seconds()) > max(CACHE_TTL_SECONDS * 4, 180):
            return False
        if received is not None and (received - exchange_time).total_seconds() < -MAX_CLOCK_SKEW_SECONDS:
            return False
        return True

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

    @classmethod
    def _verified(cls, value: Mapping[str, Any], symbol: str) -> tuple[bool, dict[str, Any]]:
        quality = assess_market_data(value)
        errors = list(quality.get("errors", []))
        errors.extend(validate_instrument_contract(value, symbol))
        exchange, segment = cls._expected_market(symbol)
        if str(value.get("exchange", "")).upper() != exchange:
            errors.append("exchange_mismatch_for_requested_symbol")
        if str(value.get("segment", "")).upper() != segment:
            errors.append("segment_mismatch_for_requested_symbol")
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
        timeframe = kwargs.get("interval")
        for provider in self._ranked(True):
            contract_key = self._cache_index.get(self._request_key(provider.name, symbol, timeframe))
            cached = self.cache.get_validated(contract_key, self._cache_entry_usable) if contract_key else None
            if cached:
                return cached

        def operation(provider):
            value = provider.get_market_data(symbol, **kwargs)
            valid, quality = self._verified(value, symbol)
            if not valid:
                raise ProviderUnavailable("provider_data_failed_verification:" + ",".join(quality["errors"][:4]))
            value = dict(value)
            value["data_quality"] = quality
            value.setdefault("received_at", datetime.now(timezone.utc).isoformat())
            value["market_state"] = market_state(value.get("segment"))
            value["signals_allowed"] = signals_allowed(value.get("segment"))
            if not self._cache_entry_usable(value):
                raise ProviderUnavailable("provider_data_failed_freshness_gate")
            contract_key = self._cache_key(provider.name, symbol, value, timeframe)
            self.cache.set(contract_key, value)
            self._cache_index[self._request_key(provider.name, symbol, timeframe)] = contract_key
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
            "angel_configured": bool(getattr(self.angel, "configured", False)),
            "angel": self.angel.health_check() if callable(getattr(self.angel, "health_check", None)) else {"provider": self.angel.name},
            "backup": "tradingview_alert_bridge",
            "backup_configured": bool(getattr(self.tradingview, "available", False)),
            "tradingview_configured": bool(getattr(self.tradingview, "available", False)),
            "backup_health": self.tradingview.health_check() if callable(getattr(self.tradingview, "health_check", None)) else {"provider": self.tradingview.name},
            "fallback": "tradingview_alert_bridge_only",
            "no_data_decision": NO_FRESH_DATA,
            "cache": self.cache.status(),
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
            "max_clock_skew_seconds": MAX_CLOCK_SKEW_SECONDS,
            "market": {segment: market_state(segment) for segment in ("NSE_FNO", "MCX_COMM")},
            "signals_allowed": signals_allowed("NSE_FNO"),
            "archived_providers": list(self.ARCHIVED_PROVIDERS),
            "supported_provider_priority": list(self.DEFAULT_PRIORITY),
            "health": health,
        }

    def invalidate_cache(self, prefix: str | None = None) -> int:
        if prefix is None:
            self._cache_index.clear()
        else:
            self._cache_index = {key: value for key, value in self._cache_index.items() if not value.startswith(prefix)}
        return self.cache.invalidate(prefix)

    def close(self) -> bool:
        self.cache.invalidate()
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
