"""Compatibility market-data facade backed by the provider manager."""
from __future__ import annotations
import logging,threading,time
from typing import Any
from provider_manager import get_provider_manager,get_provider_status,reset_provider_manager
LOGGER=logging.getLogger("shivay.data")
SYMBOLS={
"NIFTY FUT":"^NSEI","BANKNIFTY FUT":"^NSEBANK","GOLD FUT":"GOLD","SILVER FUT":"SILVER","HDFCBANK FUT":"HDFCBANK.NS","ICICIBANK FUT":"ICICIBANK.NS","SBIN FUT":"SBIN.NS","AXISBANK FUT":"AXISBANK.NS","KOTAKBANK FUT":"KOTAKBANK.NS","INDUSINDBK FUT":"INDUSINDBK.NS","PNB FUT":"PNB.NS","TCS FUT":"TCS.NS","INFY FUT":"INFY.NS","HCLTECH FUT":"HCLTECH.NS","TECHM FUT":"TECHM.NS","WIPRO FUT":"WIPRO.NS","PERSISTENT FUT":"PERSISTENT.NS","MARUTI FUT":"MARUTI.NS","TVSMOTOR FUT":"TVSMOTOR.NS","EICHERMOT FUT":"EICHERMOT.NS","RELIANCE FUT":"RELIANCE.NS","ONGC FUT":"ONGC.NS","BPCL FUT":"BPCL.NS","IOC FUT":"IOC.NS","GAIL FUT":"GAIL.NS","NTPC FUT":"NTPC.NS","POWERGRID FUT":"POWERGRID.NS","TATAPOWER FUT":"TATAPOWER.NS","TATASTEEL FUT":"TATASTEEL.NS","JSWSTEEL FUT":"JSWSTEEL.NS","HINDALCO FUT":"HINDALCO.NS","VEDL FUT":"VEDL.NS","LT FUT":"LT.NS","SIEMENS FUT":"SIEMENS.NS","ABB FUT":"ABB.NS","BHEL FUT":"BHEL.NS","CUMMINSIND FUT":"CUMMINSIND.NS","HAL FUT":"HAL.NS","BEL FUT":"BEL.NS","ITC FUT":"ITC.NS","HINDUNILVR FUT":"HINDUNILVR.NS","NESTLEIND FUT":"NESTLEIND.NS","TRENT FUT":"TRENT.NS","BHARTIARTL FUT":"BHARTIARTL.NS"}
CACHE_SECONDS=120;_cache:dict[str,dict[str,Any]]={};_last_update=0.0;_lock=threading.RLock()
def refresh_market()->None:
    global _cache,_last_update
    with _lock:
        if _cache and time.time()-_last_update<CACHE_SECONDS:return
        try:
            values=get_provider_manager().get_verified_many(list(SYMBOLS),period="5d",interval="5m");_cache=values;_last_update=time.time()
        except Exception as error:LOGGER.warning("Provider refresh failed: %s",type(error).__name__)
def get_market_data(symbol:str)->dict[str,Any]|None:
    refresh_market();value=_cache.get(symbol);return value.copy() if value else None
def get_batch_market_data(symbols:list[str])->dict[str,dict[str,Any]]:
    refresh_market();return {symbol:_cache[symbol].copy() for symbol in symbols if symbol in _cache}
def get_live_price(symbol:str)->float|None:
    data=get_market_data(symbol)
    if data:return round(float(data["price"]),2)
    return None
def cache_size()->int:return len(_cache)
def cache_age()->float:return 99999.0 if not _last_update else round(time.time()-_last_update,2)
def get_cached_snapshot()->dict[str,dict[str,Any]]:
    refresh_market()
    with _lock:return {symbol:value.copy() for symbol,value in _cache.items()}
def force_refresh()->None:
    global _last_update
    with _lock:_last_update=0;_cache.clear();reset_provider_manager()
    refresh_market()
def provider_status()->dict[str,Any]:return get_provider_status()
