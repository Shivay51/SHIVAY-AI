"""Ranked market-data providers with strict Indian-contract separation."""
from __future__ import annotations

import logging
import time
from datetime import timezone
from typing import Any, Mapping

import pandas as pd
import yfinance as yf
import config

from data_quality import assess_market_data, validate_candles, validate_instrument_contract
from angel_provider import AngelReadOnlyProvider
from instrument_master import resolve_instrument
from tradingview_bridge import TradingViewBridgeProvider
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable, execute_with_failover
from provider_health import circuit_open, get_provider_health, rank_provider_names, record_failure, record_success
from yahoo_runtime import configure_yfinance

LOGGER = logging.getLogger("shivay.provider.manager")
configure_yfinance(yf)


class YahooEmergencyProvider:
    """Delayed contextual data; never Indian futures or MCX."""
    name, available = "yahoo_emergency", True

    def __init__(self): self.cache = ProviderCache(60, 256)

    @staticmethod
    def _allowed(symbol: str) -> bool:
        value = str(symbol).strip().upper()
        return not value.endswith(" FUT") and value not in {"MCX GOLD", "MCX SILVER"}

    def _normalize(self, symbol: str, instrument: dict[str, Any], frame: Any, interval: str) -> dict[str, Any]:
        if frame is None or frame.empty: raise ProviderUnavailable("empty_yahoo_data")
        if getattr(frame.columns, "nlevels", 1) > 1: frame.columns = frame.columns.get_level_values(0)
        needed = ["Open", "High", "Low", "Close", "Volume"]
        if not set(needed).issubset(frame.columns): raise ProviderUnavailable("incomplete_yahoo_schema")
        frame = frame[needed].dropna().sort_index(); candles=[]
        for stamp, row in frame.iterrows():
            ts = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
            if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
            candles.append({"timestamp": ts, "open": float(row["Open"]), "high": float(row["High"]), "low": float(row["Low"]), "close": float(row["Close"]), "volume": float(row["Volume"])})
        quality = validate_candles(candles)
        if not quality["valid"]: raise ProviderUnavailable("invalid_yahoo_candles")
        latest=candles[-1]
        try: interval_minutes=int(str(interval).lower().replace("m", ""))
        except ValueError: interval_minutes=15
        result={**instrument,"price":latest["close"],"open":pd.Series([x["open"] for x in candles]),"high":pd.Series([x["high"] for x in candles]),"low":pd.Series([x["low"] for x in candles]),"close":pd.Series([x["close"] for x in candles]),"volume":pd.Series([x["volume"] for x in candles]),"open_value":latest["open"],"latest_volume":latest["volume"],"day_high":max(x["high"] for x in candles[-75:]),"day_low":min(x["low"] for x in candles[-75:]),"timestamp":latest["timestamp"],"provider":self.name,"is_live":False,"is_delayed":True,"data_quality":quality,"candles":candles,"interval_minutes":interval_minutes,"emergency_context_only":True}
        freshness=assess_market_data(result);result.update(delay_seconds=freshness["delay_seconds"],is_stale=freshness["is_stale"],freshness_status=freshness["status"]);return result

    def get_market_data(self, symbol: str, period: str = "1mo", interval: str = "15m") -> dict[str, Any]:
        if not self._allowed(symbol): raise ProviderUnavailable("yahoo_cannot_supply_indian_contracts")
        instrument=resolve_instrument(symbol)
        if not instrument: raise ValueError("unverified_instrument")
        key=f"{symbol}:{period}:{interval}";cached=self.cache.get(key)
        if cached:return cached
        frame=yf.download(instrument["trading_symbol"],period=period,interval=interval,auto_adjust=True,progress=False,threads=False,timeout=8)
        result=self._normalize(symbol,instrument,frame,interval);self.cache.set(key,result);return result

    def get_many(self, symbols: list[str], period: str = "1mo", interval: str = "15m") -> dict[str, dict[str, Any]]:
        instruments={s:resolve_instrument(s) for s in symbols if self._allowed(s)};instruments={s:i for s,i in instruments.items() if i};tickers=list(dict.fromkeys(i["trading_symbol"] for i in instruments.values()))
        if not tickers:return {}
        frame=yf.download(tickers=tickers,period=period,interval=interval,auto_adjust=True,progress=False,threads=True,group_by="ticker",timeout=8);result={}
        for symbol,instrument in instruments.items():
            try:
                part=frame[instrument["trading_symbol"]] if len(tickers)>1 else frame
                value=self._normalize(symbol,instrument,part,interval);self.cache.set(f"{symbol}:{period}:{interval}",value);result[symbol]=value
            except Exception:continue
        return result

    def get_live_price(self, symbol: str) -> float | None:
        data=self.get_market_data(symbol);return round(float(data["price"]),2) if data else None


# Historical catalogue of provider keys the codebase has ever supported. Kept so
# legacy PROVIDER_PRIORITY values and integration tests still resolve.
_KNOWN_PROVIDER_KEYS=(
    "angelone_primary","tradingview_alert_bridge","nse_temporary","tvkit_ohlcv",
    "mcx_temporary","groww_primary","upstox_primary","dhan_primary",
    "shoonya_primary","truedata_primary","gdfl_primary","fyers_primary",
    "market_hub","yahoo_emergency",
)
# The only providers permitted in the runtime data plane.
_RUNTIME_PROVIDER_KEYS=("angelone_primary","tradingview_alert_bridge")
_DISABLED_PROVIDER_KEYS=tuple(
    name for name in _KNOWN_PROVIDER_KEYS if name not in _RUNTIME_PROVIDER_KEYS
)


class ProviderManager:
    """Runtime data plane: Angel One primary, TradingView backup, nothing else.

    Architecture (enforced):
        Angel One SmartAPI -> authenticated TradingView backup -> NO SIGNAL

    ``DEFAULT_PRIORITY`` remains the historical catalogue of *known* provider
    keys so legacy configuration and integration tests keep resolving, but only
    the keys in ``RUNTIME_PROVIDERS`` are ever instantiated or registered. Every
    other former provider (Groww, Upstox, NSE/MCX temporary, tvkit, Dhan,
    Shoonya, TrueData, GDFL, Fyers, market hub, Yahoo) is unregistered at
    runtime and can no longer serve market data or influence signals.
    """

    DEFAULT_PRIORITY=_KNOWN_PROVIDER_KEYS
    # Only these two providers may ever be registered in the runtime data plane.
    RUNTIME_PROVIDERS=_RUNTIME_PROVIDER_KEYS
    RUNTIME_PRIMARY="angelone_primary"
    RUNTIME_BACKUP="tradingview_alert_bridge"
    DISABLED_PROVIDERS=_DISABLED_PROVIDER_KEYS

    @classmethod
    def _base_priority(cls)->dict[str,int]:
        """Angel always outranks TradingView; unregistered keys are ignored."""
        configured=getattr(config,"PROVIDER_PRIORITY",cls.RUNTIME_PROVIDERS)
        ordered=[]
        for name in configured if isinstance(configured,(list,tuple)) else cls.RUNTIME_PROVIDERS:
            value=str(name).strip().lower()
            if value in cls.RUNTIME_PROVIDERS and value not in ordered:ordered.append(value)
        ordered.extend(name for name in cls.RUNTIME_PROVIDERS if name not in ordered)
        # Hard invariant: the primary can never be demoted below the backup.
        if ordered[0]!=cls.RUNTIME_PRIMARY:
            ordered=[cls.RUNTIME_PRIMARY]+[name for name in ordered if name!=cls.RUNTIME_PRIMARY]
        return {name:(len(ordered)-index)*300 for index,name in enumerate(ordered)}

    def __init__(self):
        self.angel=AngelReadOnlyProvider()
        self.tradingview=TradingViewBridgeProvider()
        self.providers=[]
        if self.angel.available:self.providers.append(self.angel)
        if self.tradingview.available:self.providers.append(self.tradingview)
        # No emergency/delayed provider is registered: when neither Angel nor the
        # authenticated TradingView bridge can supply fresh verified data the
        # engine must produce NO SIGNAL rather than trade on degraded data.
        self.disabled_providers=list(self.DISABLED_PROVIDERS)

    def _ranked(self, verified_only: bool = False) -> list[Any]:
        values=[p for p in self.providers if not verified_only or getattr(p,"signal_capable",True)]
        by_name={p.name:p for p in values};return [by_name[name] for name in rank_provider_names(list(by_name),self._base_priority())]

    @staticmethod
    def _verified(value: Mapping[str, Any], symbol: str) -> tuple[bool, dict[str, Any]]:
        quality=assess_market_data(value);errors=list(quality.get("errors",[]));errors.extend(validate_instrument_contract(value,symbol));quality["errors"]=sorted(set(errors));quality["valid"]=not quality["errors"];quality["status"]="GOOD" if quality["valid"] else "REJECTED"
        valid=bool(quality["valid"] and value.get("verified") and value.get("is_live") and not value.get("is_delayed") and not value.get("is_stale"))
        return valid,quality

    def get_market_data(self, symbol: str, **kwargs) -> dict[str, Any]:
        return execute_with_failover(self._ranked(),lambda provider:provider.get_market_data(symbol,**kwargs))[0]

    def get_verified_market_data(self, symbol: str, **kwargs) -> dict[str, Any]:
        def operation(provider):
            value=provider.get_market_data(symbol,**kwargs);valid,quality=self._verified(value,symbol)
            if not valid:raise ProviderUnavailable("provider_data_failed_verification:"+",".join(quality["errors"][:4]))
            value=dict(value);value["data_quality"]=quality;return value
        return execute_with_failover(self._ranked(True),operation)[0]

    def get_live_price(self, symbol: str) -> float | None:
        return execute_with_failover(self._ranked(),lambda provider:provider.get_live_price(symbol))[0]

    def get_many(self, symbols: list[str], **kwargs) -> dict[str, dict[str, Any]]:
        pending=list(dict.fromkeys(symbols));result={}
        for provider in self._ranked():
            if not pending:break
            try:
                batch=provider.get_many(pending,**kwargs) if callable(getattr(provider,"get_many",None)) else {s:provider.get_market_data(s,**kwargs) for s in pending}
                result.update({symbol:value for symbol,value in batch.items() if value is not None});pending=[symbol for symbol in pending if symbol not in result]
            except Exception as error:LOGGER.warning("Bulk provider failed over: %s (%s)",provider.name,type(error).__name__)
        return result

    def get_verified_many(self, symbols: list[str], **kwargs) -> dict[str, dict[str, Any]]:
        pending=list(dict.fromkeys(symbols));result={}
        for provider in self._ranked(True):
            if not pending:break
            if circuit_open(provider.name):continue
            started=time.monotonic()
            try:
                batch=provider.get_many(pending,**kwargs) if callable(getattr(provider,"get_many",None)) else {s:provider.get_market_data(s,**kwargs) for s in pending}
                scores=[]
                for symbol,value in batch.items():
                    valid,quality=self._verified(value,symbol)
                    if valid:
                        normalized=dict(value);normalized["data_quality"]=quality;result[symbol]=normalized;scores.append(quality)
                if scores:
                    record_success(provider.name,(time.monotonic()-started)*1000,{key:sum(float(item.get(key,0)) for item in scores)/len(scores) for key in ("freshness_score","completeness_score","consistency_score")})
                else:record_failure(provider.name,"validation")
                pending=[symbol for symbol in pending if symbol not in result]
            except Exception as error:
                record_failure(provider.name,"permanent" if isinstance(error,(ValueError,PermissionError)) else "temporary");LOGGER.warning("Verified provider unavailable: %s (%s)",provider.name,type(error).__name__)
        return result

    def status(self) -> dict[str, Any]:
        health=get_provider_health()
        registered=[p.name for p in self._ranked()]
        verified=[p.name for p in self._ranked(True) if p.available]
        healthy=[name for name in verified if health.get(name,{}).get("status")=="HEALTHY"]
        angel_health=self.angel.health_check()
        primary=self.RUNTIME_PRIMARY if self.angel.available else None
        backup=self.RUNTIME_BACKUP if self.tradingview.available else None
        active=next((name for name in registered if not circuit_open(name)),None)
        if active==self.RUNTIME_PRIMARY:mode="ANGEL_PRIMARY"
        elif active==self.RUNTIME_BACKUP:mode="TRADINGVIEW_BACKUP"
        else:mode="NO_FRESH_DATA_NO_SIGNAL"
        return {
            "architecture":"angelone_primary -> tradingview_alert_bridge -> NO_SIGNAL",
            "selected_primary":primary,"configured_primary":primary,
            "selected_secondary":backup,"active_provider":active,
            "mode":mode,"signal_allowed":bool(active),
            "verified_providers":verified,"healthy_providers":healthy,
            "providers":registered,"registered_provider_count":len(registered),
            "angel_configured":self.angel.configured,"angel":angel_health,
            "angel_totp_automation":angel_health["totp_automation"],
            "angel_instrument_master":angel_health["instrument_master"],
            "tradingview_configured":self.tradingview.available,
            "tradingview_role":"authenticated_emergency_backup_only",
            "fallback":"none_no_signal_when_no_fresh_data",
            "disabled_providers":list(self.DISABLED_PROVIDERS),
            "supported_provider_priority":list(self.RUNTIME_PROVIDERS),
            "emergency_delayed_provider_registered":False,
            "health":health,
        }

    def close(self)->bool:
        for provider in (self.angel,self.tradingview):
            try:provider.close()
            except Exception:pass
        return True


_MANAGER:ProviderManager|None=None
def get_provider_manager()->ProviderManager:
    global _MANAGER
    if _MANAGER is None:_MANAGER=ProviderManager()
    return _MANAGER
def reset_provider_manager()->None:
    global _MANAGER
    if _MANAGER is not None:
        try:_MANAGER.close()
        except Exception:pass
    _MANAGER=None
def get_provider_status()->dict[str,Any]:return get_provider_manager().status()
