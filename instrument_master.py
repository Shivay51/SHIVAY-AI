"""Verified instrument mappings; never synthesizes exchange contract codes."""
from __future__ import annotations
import json, logging, threading
from datetime import date
from pathlib import Path
from typing import Any
LOGGER=logging.getLogger("shivay.instrument_master"); ROOT=Path(__file__).resolve().parent; CONFIG_FILE=ROOT/"instruments.json"; _LOCK=threading.RLock()
_SHOONYA_MASTER_PROVIDER: Any = None
_TRIAL_MASTER_PROVIDERS: list[Any] | None = None

def _trial_master_providers() -> list[Any]:
    global _TRIAL_MASTER_PROVIDERS
    with _LOCK:
        if _TRIAL_MASTER_PROVIDERS is None:
            from groww_provider import GrowwProvider
            from upstox_provider import UpstoxProvider
            from truedata_provider import TrueDataProvider
            from gdfl_provider import GDFLProvider
            _TRIAL_MASTER_PROVIDERS = [GrowwProvider(), UpstoxProvider(), TrueDataProvider(), GDFLProvider()]
        return _TRIAL_MASTER_PROVIDERS


def _shoonya_master_provider() -> Any:
    """Return a metadata-only Shoonya provider without importing the manager.

    Keeping instrument resolution below the provider manager removes the former
    manager <-> instrument-master dependency cycle.  These operations use only
    Shoonya's public instrument files and never open a market-data session.
    """
    global _SHOONYA_MASTER_PROVIDER
    with _LOCK:
        if _SHOONYA_MASTER_PROVIDER is None:
            from shoonya_provider import ShoonyaProvider
            _SHOONYA_MASTER_PROVIDER = ShoonyaProvider()
        return _SHOONYA_MASTER_PROVIDER
INDEXES={"NIFTY":{"trading_symbol":"^NSEI","exchange":"NSE","segment":"INDEX","instrument_type":"INDEX"},"NIFTY FUT":{"trading_symbol":"^NSEI","exchange":"NSE","segment":"INDEX","instrument_type":"INDEX_PROXY"},"BANKNIFTY":{"trading_symbol":"^NSEBANK","exchange":"NSE","segment":"INDEX","instrument_type":"INDEX"},"BANKNIFTY FUT":{"trading_symbol":"^NSEBANK","exchange":"NSE","segment":"INDEX","instrument_type":"INDEX_PROXY"}}
GLOBAL={"GIFT NIFTY":{"trading_symbol":"^NSEI","exchange":"YAHOO","segment":"INDEX_PROXY","instrument_type":"INDEX_PROXY"},"GOLD":{"trading_symbol":"GC=F","exchange":"COMEX","segment":"COMMODITY","instrument_type":"GLOBAL_FUTURE"},"SILVER":{"trading_symbol":"SI=F","exchange":"COMEX","segment":"COMMODITY","instrument_type":"GLOBAL_FUTURE"}}
def _configured()->dict[str,dict[str,Any]]:
    if not CONFIG_FILE.exists(): return {}
    try:
        value=json.loads(CONFIG_FILE.read_text(encoding="utf-8")); return {str(k).upper():v for k,v in value.items() if isinstance(v,dict) and v.get("trading_symbol") and v.get("exchange")}
    except Exception: LOGGER.warning("Configured instrument master rejected"); return {}
def resolve_instrument(symbol: str, instrument_type: str|None=None)->dict[str,Any]|None:
    key=str(symbol or "").strip().upper(); configured=_configured(); item=configured.get(key) or INDEXES.get(key) or GLOBAL.get(key)
    if item is None and key.endswith(" FUT"): return None
    if item is None:
        ticker=key.removesuffix(".NS")+".NS"; item={"trading_symbol":ticker,"exchange":"NSE","segment":"CASH","instrument_type":"EQUITY"}
    result={"symbol":key,"expiry":None,"lot_size":None,"verified":key in configured,**item}
    if instrument_type and result["instrument_type"]!=instrument_type: return None
    return result
def current_expiry(symbol: str, on_date: date|None=None)->str|None:
    item=resolve_instrument(symbol); return item.get("expiry") if item else None
def list_instruments()->list[dict[str,Any]]: return [resolve_instrument(k) for k in sorted(set(INDEXES)|set(GLOBAL)|set(_configured()))]
def resolve_verified_instrument(symbol:str)->dict[str,Any]|None:
    for provider in _trial_master_providers():
        if not provider.available:continue
        try:
            value=provider.resolve_instrument(symbol)
            if value and value.get("verified"):return value
        except Exception:continue
    try:
        value=_shoonya_master_provider().resolve_instrument(symbol)
        return value if value and value.get("verified") else None
    except Exception:return None
def refresh_instrument_master()->dict[str,Any]:
    enabled=[provider for provider in _trial_master_providers() if provider.available]
    if enabled:
        resolved={}
        for symbol in ("NIFTY FUT","BANKNIFTY FUT","MCX GOLD","MCX SILVER"):
            for provider in enabled:
                try:
                    value=provider.resolve_instrument(symbol)
                    if value:resolved[symbol]=value;break
                except Exception:continue
        if resolved:return {"supported":True,"count":len(resolved),"source":"official_trial_provider","instruments":resolved}
    try:
        return _shoonya_master_provider().refresh_instruments()
    except Exception:pass
    return {"supported":CONFIG_FILE.exists(),"count":len(list_instruments()),"source":"configured" if CONFIG_FILE.exists() else "emergency_context_only"}
