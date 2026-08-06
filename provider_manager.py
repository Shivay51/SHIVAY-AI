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
from dhan_provider import DhanProvider
from gdfl_provider import GDFLProvider
from groww_provider import GrowwProvider
from instrument_master import resolve_instrument
from market_hub_client import MarketHubClient
from mcx_temporary_provider import MCXTemporaryProvider
from nse_temporary_provider import NSETemporaryProvider
from shoonya_provider import ShoonyaProvider
from truedata_provider import TrueDataProvider
from upstox_provider import UpstoxProvider
from tradingview_bridge import TradingViewBridgeProvider
from tvkit_provider import TVKitProvider
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


class ProviderManager:
    # Compatibility order when no configured priority exists.  config.py places
    # authenticated Groww first; the bridge remains the safe installation default.
    DEFAULT_PRIORITY=("tradingview_alert_bridge","nse_temporary","tvkit_ohlcv","mcx_temporary","groww_primary","upstox_primary","dhan_primary","shoonya_primary","truedata_primary","gdfl_primary","fyers_primary","angelone_primary","market_hub","yahoo_emergency")

    @classmethod
    def _base_priority(cls)->dict[str,int]:
        configured=getattr(config,"PROVIDER_PRIORITY",cls.DEFAULT_PRIORITY)
        ordered=[]
        for name in configured if isinstance(configured,(list,tuple)) else cls.DEFAULT_PRIORITY:
            value=str(name).strip().lower()
            if value in cls.DEFAULT_PRIORITY and value not in ordered:ordered.append(value)
        ordered.extend(name for name in cls.DEFAULT_PRIORITY if name not in ordered)
        return {name:(len(ordered)-index)*300 for index,name in enumerate(ordered)}

    def __init__(self):
        self.groww=GrowwProvider();self.upstox=UpstoxProvider();self.tradingview=TradingViewBridgeProvider();self.nse_temporary=NSETemporaryProvider();self.tvkit=TVKitProvider();self.mcx_temporary=MCXTemporaryProvider();self.truedata=TrueDataProvider();self.gdfl=GDFLProvider();self.shoonya=ShoonyaProvider();self.dhan=DhanProvider();self.market_hub=MarketHubClient();self.yahoo=YahooEmergencyProvider();self.providers=[]
        if self.groww.available:self.providers.append(self.groww)
        if self.upstox.available:self.providers.append(self.upstox)
        if self.tradingview.available:self.providers.append(self.tradingview)
        if self.nse_temporary.available:self.providers.append(self.nse_temporary)
        if self.tvkit.available:self.providers.append(self.tvkit)
        if self.mcx_temporary.available:self.providers.append(self.mcx_temporary)
        if self.truedata.available:self.providers.append(self.truedata)
        if self.gdfl.available:self.providers.append(self.gdfl)
        if self.shoonya.available:self.providers.append(self.shoonya)
        if self.dhan.available:self.providers.append(self.dhan)
        if self.market_hub.available and self.market_hub.capabilities().get("candles"):self.providers.append(self.market_hub)
        self.providers.append(self.yahoo)

    def _ranked(self, verified_only: bool = False) -> list[Any]:
        values=[p for p in self.providers if not verified_only or (p.name!="yahoo_emergency" and getattr(p,"signal_capable",True))]
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
        verified=[p.name for p in self._ranked(True) if p.available];health=get_provider_health();healthy=[name for name in verified if health.get(name,{}).get("status")=="HEALTHY"]
        primary=healthy[0] if healthy else None;configured=verified[0] if verified else None
        return {"selected_primary":primary,"configured_primary":configured,"selected_secondary":healthy[1] if len(healthy)>1 else None,"active_provider":primary or "yahoo_emergency","mode":"PRIMARY" if primary else "EMERGENCY_FALLBACK","verified_providers":healthy,"groww_configured":self.groww.configured,"groww":self.groww.health_check(),"upstox_configured":self.upstox.configured,"upstox":self.upstox.health_check(),"tradingview_configured":self.tradingview.available,"tvkit":self.tvkit.health_check(),"nse_temporary":self.nse_temporary.health_check(),"mcx_temporary":self.mcx_temporary.health_check(),"truedata_configured":self.truedata.client.configured,"truedata":self.truedata.client.health(),"gdfl_configured":self.gdfl.client.configured,"gdfl":self.gdfl.client.health(False),"shoonya_configured":self.shoonya.available,"shoonya":self.shoonya.client.health(),"dhan_configured":self.dhan.available,"market_hub_authenticated_provider":self.market_hub.available,"market_hub_market_data_supported":False,"fallback":"yahoo_emergency_context_only","providers":[p.name for p in self._ranked()],"supported_provider_priority":list(self.DEFAULT_PRIORITY),"health":health}

    def close(self)->bool:
        for provider in (self.groww,self.upstox,self.nse_temporary,self.tvkit,self.mcx_temporary,self.truedata,self.gdfl,self.shoonya):
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
