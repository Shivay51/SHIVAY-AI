"""Stable verified-data facade; emergency context is explicit and separate."""
from __future__ import annotations
from typing import Any
from provider_manager import get_provider_manager,get_provider_status
from data import SYMBOLS

def get_market_data(symbol:str)->dict[str,Any]|None:
    try:return get_provider_manager().get_verified_market_data(symbol,period="5d",interval="5m")
    except Exception:return None
def get_live_price(symbol:str)->float|None:
    value=get_market_data(symbol);return round(float(value["price"]),2) if value else None
def get_emergency_market_context(symbol:str)->dict[str,Any]|None:
    try:return get_provider_manager().yahoo.get_market_data(symbol)
    except Exception:return None
def refresh_market(symbols:list[str]|None=None)->dict[str,dict[str,Any]]:
    return get_provider_manager().get_verified_many(symbols or list(SYMBOLS),period="5d",interval="5m")
def get_batch_market_data(symbols:list[str])->dict[str,dict[str,Any]]:return refresh_market(symbols)
def force_refresh()->dict[str,dict[str,Any]]:
    from provider_manager import reset_provider_manager
    reset_provider_manager();return {}
def cache_size()->int:
    return sum(getattr(provider,"cache").status()["entries"] for provider in get_provider_manager().providers if hasattr(provider,"cache"))
def cache_age()->float:return 0.0 if cache_size() else 99999.0
def provider_status()->dict[str,Any]:return get_provider_status()
