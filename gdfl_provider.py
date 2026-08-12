"""Verified Indian futures/MCX provider backed by Global Datafeeds."""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Any, Mapping
import pandas as pd
from data_quality import assess_market_data, validate_candles, validate_instrument_contract
from gdfl_client import GDFLClient
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable

def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list): return [dict(x) for x in value if isinstance(x, Mapping)]
    if isinstance(value, Mapping):
        for key in ("Result", "result", "Data", "data", "Instruments", "instruments"):
            if key in value: return _items(value[key])
        return [dict(value)]
    return []
def _get(row: Mapping[str,Any], *names: str) -> Any:
    folded={re.sub(r"[^a-z0-9]", "", str(k).lower()):v for k,v in row.items()}
    for name in names:
        value=folded.get(re.sub(r"[^a-z0-9]", "", name.lower()))
        if value not in (None, ""): return value
    return None
def _float(value:Any)->float:
    try:return float(value)
    except (TypeError,ValueError):return 0.0
def _time(value:Any)->datetime|None:
    if isinstance(value,datetime): result=value
    elif isinstance(value,(int,float)):
        result=datetime.fromtimestamp(float(value)/(1000 if float(value)>1e11 else 1),timezone.utc)
    else:
        try: result=pd.to_datetime(value,utc=True).to_pydatetime()
        except Exception:return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)

class GDFLProvider:
    name="gdfl_primary"
    def __init__(self,client:GDFLClient|None=None):self.client=client or GDFLClient();self.available=self.client.configured;self.cache=ProviderCache(20,256);self.instruments=ProviderCache(21600,256)
    @staticmethod
    def _spec(symbol:str)->tuple[str,str,str,str]:
        key=str(symbol).upper().strip();root=key.removesuffix(" FUT").removeprefix("MCX ")
        if root in {"GOLD","SILVER"}:return root,"MCX","FUTCOM","MCX_COMM"
        return root,"NFO","FUTIDX" if root in {"NIFTY","BANKNIFTY"} else "FUTSTK","NSE_FNO"
    def resolve_instrument(self,symbol:str)->dict[str,Any]|None:
        cached=self.instruments.get(str(symbol).upper())
        if cached:return cached
        root,exchange,kind,segment=self._spec(symbol)
        rows=_items(self.client.search_instruments(root,exchange,kind)); candidates=[]
        for row in rows:
            identifier=str(_get(row,"InstrumentIdentifier","Identifier","TradingSymbol") or "").strip()
            expiry=_time(_get(row,"Expiry","ExpiryDate","ContractExpiration"));lot=_float(_get(row,"LotSize","QuotationLot","MarketLot"));tick=_float(_get(row,"TickSize","PriceTick","MinimumPriceMovement"))
            security=str(_get(row,"SecurityId","Token","InstrumentToken","ScripCode") or identifier).strip()
            if identifier and expiry and expiry.date()>=datetime.now(timezone.utc).date() and lot>0 and tick>0:
                candidates.append((expiry,{"symbol":str(symbol).upper(),"underlying":root,"trading_symbol":identifier,"instrument_key":identifier,"security_id":security,"exchange":"MCX" if exchange=="MCX" else "NSE","segment":segment,"instrument_type":kind,"expiry":expiry.date().isoformat(),"lot_size":lot,"tick_size":tick,"verified":True}))
        if not candidates:return None
        result=min(candidates,key=lambda x:x[0])[1];self.instruments.set(str(symbol).upper(),result);return result
    def _candles(self,value:Any)->list[dict[str,Any]]:
        result=[]
        for row in _items(value):
            stamp=_time(_get(row,"Timestamp","DateTime","Time","LastTradeTime"));o=_float(_get(row,"Open"));h=_float(_get(row,"High"));l=_float(_get(row,"Low"));c=_float(_get(row,"Close","LastTradePrice","LTP"));v=_float(_get(row,"Volume","TotalQtyTraded"))
            if stamp and min(o,h,l,c)>0:result.append({"timestamp":stamp,"open":o,"high":h,"low":l,"close":c,"volume":max(0,v)})
        return sorted(result,key=lambda x:x["timestamp"])
    def get_market_data(self,symbol:str,period:str="1mo",interval:str="5m")->dict[str,Any]:
        instrument=self.resolve_instrument(symbol)
        if not instrument:raise ProviderUnavailable("gdfl_exact_contract_unavailable")
        try: minutes=int(str(interval).lower().replace("m",""));minutes=minutes if minutes in {1,2,3,5,10,15,30,60} else 5
        except ValueError:minutes=5
        candles=self._candles(self.client.get_history("MCX" if instrument["exchange"]=="MCX" else "NFO",instrument["instrument_key"],minutes,180))
        quality=validate_candles(candles)
        if not quality["valid"]:raise ProviderUnavailable("gdfl_invalid_candles")
        quote_rows=_items(self.client.get_quote("MCX" if instrument["exchange"]=="MCX" else "NFO",instrument["instrument_key"]));quote=quote_rows[0] if quote_rows else {}
        price=_float(_get(quote,"LastTradePrice","LTP","Last")) or candles[-1]["close"];stamp=_time(_get(quote,"LastTradeTime","ServerTime","Timestamp")) or candles[-1]["timestamp"]
        result={**instrument,"price":price,"timestamp":stamp,"provider":self.name,"is_live":True,"is_delayed":False,"candles":candles,"interval_minutes":minutes,"open":pd.Series([x["open"] for x in candles]),"high":pd.Series([x["high"] for x in candles]),"low":pd.Series([x["low"] for x in candles]),"close":pd.Series([x["close"] for x in candles]),"volume":pd.Series([x["volume"] for x in candles]),"open_value":candles[-1]["open"],"latest_volume":candles[-1]["volume"],"day_high":max(x["high"] for x in candles[-75:]),"day_low":min(x["low"] for x in candles[-75:])}
        freshness=assess_market_data(result);result.update(delay_seconds=freshness["delay_seconds"],is_stale=freshness["is_stale"],freshness_status=freshness["status"])
        errors=validate_instrument_contract(result,symbol)
        if errors or result["is_stale"]:raise ProviderUnavailable("gdfl_data_rejected")
        return result
    def get_many(self,symbols:list[str],**kwargs)->dict[str,dict[str,Any]]:
        result={}
        for symbol in symbols:
            try:result[symbol]=self.get_market_data(symbol,**kwargs)
            except Exception:pass
        return result
    def get_live_price(self,symbol:str)->float|None:return float(self.get_market_data(symbol)["price"])
    def close(self)->None:self.client.close()
