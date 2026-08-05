"""Verified TrueData adapter; requires a provider-issued exact contract catalog."""
from __future__ import annotations
import json
from datetime import date,datetime,timezone
from pathlib import Path
from typing import Any
import pandas as pd
from data_quality import assess_market_data,validate_candles,validate_instrument_contract
from provider_failover import ProviderUnavailable
from truedata_client import TrueDataClient

CATALOG=Path(__file__).resolve().parent/"cache"/"truedata_instruments.json"
class TrueDataProvider:
    name="truedata_primary"
    def __init__(self,client:TrueDataClient|None=None):self.client=client or TrueDataClient();self._catalog=self._load();self.available=bool(self.client.configured and self._catalog)
    @staticmethod
    def _load()->list[dict[str,Any]]:
        try:
            value=json.loads(CATALOG.read_text(encoding="utf-8"));rows=value if isinstance(value,list) else value.get("instruments",[])
            return [dict(x) for x in rows if isinstance(x,dict)]
        except Exception:return []
    def resolve_instrument(self,symbol:str)->dict[str,Any]|None:
        key=str(symbol).upper();today=date.today();valid=[]
        for row in self._catalog:
            try:
                item={k.lower():v for k,v in row.items()};expiry=date.fromisoformat(str(item["expiry"])[:10]);lot=float(item["lot_size"]);tick=float(item["tick_size"]);identifier=str(item.get("instrument_key") or item.get("trading_symbol") or "")
                if str(item.get("symbol","")).upper()==key and expiry>=today and identifier and lot>0 and tick>0:
                    valid.append((expiry,{**item,"symbol":key,"instrument_key":identifier,"trading_symbol":identifier,"expiry":expiry.isoformat(),"lot_size":lot,"tick_size":tick,"verified":True}))
            except Exception:continue
        return min(valid,key=lambda x:x[0])[1] if valid else None
    def get_market_data(self,symbol:str,period:str="1mo",interval:str="5m")->dict[str,Any]:
        instrument=self.resolve_instrument(symbol)
        if not instrument:raise ProviderUnavailable("truedata_exact_contract_unavailable")
        try:minutes=int(str(interval).lower().replace("m",""))
        except ValueError:minutes=5
        frame=self.client.history(instrument["instrument_key"],minutes,180)
        if not isinstance(frame,pd.DataFrame):frame=pd.DataFrame(frame)
        frame.columns=[str(x).lower() for x in frame.columns]; candles=[]
        for index,row in frame.iterrows():
            try:
                stamp=pd.to_datetime(row.get("timestamp",row.get("datetime",index)),utc=True).to_pydatetime();o=float(row["open"]);h=float(row["high"]);l=float(row["low"]);c=float(row["close"]);v=float(row.get("volume",row.get("vol",0)) or 0)
                candles.append({"timestamp":stamp,"open":o,"high":h,"low":l,"close":c,"volume":max(0,v)})
            except Exception:continue
        candles.sort(key=lambda x:x["timestamp"]);quality=validate_candles(candles)
        if not quality["valid"]:raise ProviderUnavailable("truedata_invalid_candles")
        quote=self.client.quote(instrument["instrument_key"]);fold={str(k).lower():v for k,v in quote.items()}
        price=float(fold.get("ltp",fold.get("price",candles[-1]["close"])));stamp=pd.to_datetime(fold.get("timestamp",candles[-1]["timestamp"]),utc=True).to_pydatetime()
        result={**instrument,"price":price,"timestamp":stamp,"provider":self.name,"is_live":True,"is_delayed":False,"candles":candles,"interval_minutes":minutes,"open":pd.Series([x["open"] for x in candles]),"high":pd.Series([x["high"] for x in candles]),"low":pd.Series([x["low"] for x in candles]),"close":pd.Series([x["close"] for x in candles]),"volume":pd.Series([x["volume"] for x in candles]),"open_value":candles[-1]["open"],"latest_volume":candles[-1]["volume"],"day_high":max(x["high"] for x in candles[-75:]),"day_low":min(x["low"] for x in candles[-75:])}
        freshness=assess_market_data(result);result.update(delay_seconds=freshness["delay_seconds"],is_stale=freshness["is_stale"],freshness_status=freshness["status"])
        if freshness["is_stale"] or validate_instrument_contract(result,symbol):raise ProviderUnavailable("truedata_data_rejected")
        return result
    def get_many(self,symbols:list[str],**kwargs)->dict[str,dict[str,Any]]:
        result={}
        for symbol in symbols:
            try:result[symbol]=self.get_market_data(symbol,**kwargs)
            except Exception:pass
        return result
    def get_live_price(self,symbol:str)->float|None:return float(self.get_market_data(symbol)["price"])
    def close(self)->None:self.client.close()
