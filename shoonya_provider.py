"""Verified Shoonya NSE-futures and MCX market-data provider."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from data_quality import assess_market_data, parse_timestamp, validate_candles
from provider_cache import ProviderCache
from provider_failover import ProviderUnavailable
from shoonya_client import ShoonyaClient

LOGGER=logging.getLogger("shivay.provider.shoonya")
ROOT=Path(__file__).resolve().parent;MASTER_FILE=ROOT/"cache"/"shoonya_instruments.json";IST=ZoneInfo("Asia/Kolkata")
MASTER_URLS={"NFO":"https://api.shoonya.com/NFO_symbols.txt.zip","MCX":"https://api.shoonya.com/MCX_symbols.txt.zip"};MASTER_TTL=86_400;_MASTER_LOCK=threading.RLock();_MASTER_MEMORY:list[dict[str,Any]]|None=None

def _number(value:Any,default:float=0.0)->float:
    try:return float(value)
    except (TypeError,ValueError):return default
def _expiry(value:Any)->date|None:
    for fmt in ("%d-%b-%Y","%d-%m-%Y","%Y-%m-%d"):
        try:return datetime.strptime(str(value).strip().upper(),fmt).date()
        except ValueError:continue
    return None
def _atomic_json(path:Path,value:Any)->None:
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=".shoonya-master-",suffix=".tmp",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle:json.dump(value,handle,separators=(",",":"));handle.flush();os.fsync(handle.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

class ShoonyaProvider:
    name="shoonya_primary"
    documented_capabilities={"nse_futures":True,"nifty":True,"banknifty":True,"stock_futures":True,"mcx_gold":True,"mcx_silver":True,"instrument_master":True,"quotes":True,"historical_candles":True,"market_websocket":True}
    def __init__(self,client:ShoonyaClient|None=None)->None:
        self.client=client or ShoonyaClient();self.available=self.client.configured;self.cache=ProviderCache(120,512);self._resolved:dict[str,dict[str,Any]|None]={};self._rest_quote_state:dict[str,tuple[float,float]]={};self._quote_state_lock=threading.RLock()

    def _download_master(self)->list[dict[str,Any]]:
        rows=[]
        for exchange,url in MASTER_URLS.items():
            response=requests.get(url,timeout=(4,15));response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                names=archive.namelist()
                if len(names)!=1:raise ProviderUnavailable("invalid_shoonya_master_archive")
                text=archive.read(names[0]).decode("utf-8-sig",errors="replace")
            for raw in csv.DictReader(io.StringIO(text)):
                instrument=str(raw.get("Instrument") or "").upper();symbol=str(raw.get("Symbol") or "").upper().replace(" ","");token=str(raw.get("Token") or "").strip();trading=str(raw.get("TradingSymbol") or "").strip()
                if instrument not in {"FUTIDX","FUTSTK","FUTCOM"} or not all((symbol,token,trading)):continue
                expiry=_expiry(raw.get("Expiry"));
                if expiry is None:continue
                rows.append({"provider_exchange":exchange,"token":token,"security_id":token,"instrument_key":f"{exchange}|{token}","underlying":symbol,"trading_symbol":trading,"expiry":expiry.isoformat(),"instrument_type":instrument,"lot_size":int(_number(raw.get("LotSize"))) or None,"tick_size":_number(raw.get("TickSize")) or None})
        if not rows:raise ProviderUnavailable("empty_shoonya_instrument_master")
        _atomic_json(MASTER_FILE,rows);return rows

    def master(self,force:bool=False)->list[dict[str,Any]]:
        global _MASTER_MEMORY
        with _MASTER_LOCK:
            if _MASTER_MEMORY is not None and not force:return list(_MASTER_MEMORY)
            fresh=MASTER_FILE.is_file() and time.time()-MASTER_FILE.stat().st_mtime<MASTER_TTL
            if not force and fresh:
                try:value=json.loads(MASTER_FILE.read_text(encoding="utf-8"));_MASTER_MEMORY=[row for row in value if isinstance(row,dict)];return list(_MASTER_MEMORY)
                except (OSError,json.JSONDecodeError):pass
            _MASTER_MEMORY=self._download_master();return list(_MASTER_MEMORY)

    def refresh_instruments(self,force:bool=False)->dict[str,Any]:
        rows=self.master(force);return {"supported":True,"source":"shoonya_official","rows":len(rows),"nfo":sum(row["provider_exchange"]=="NFO" for row in rows),"mcx":sum(row["provider_exchange"]=="MCX" for row in rows)}

    def resolve_instrument(self,symbol:str)->dict[str,Any]|None:
        requested=str(symbol or "").strip().upper();now=datetime.now(IST)
        cached=self._resolved.get(requested)
        if cached:
            expiry=date.fromisoformat(cached["expiry"]);close=(23,30) if cached["provider_exchange"]=="MCX" else (15,30)
            if expiry>now.date() or (expiry==now.date() and now.time()<=datetime.strptime(f"{close[0]:02d}:{close[1]:02d}","%H:%M").time()):return dict(cached)
            self._resolved.pop(requested,None)
        root=requested.removesuffix(" FUT").replace("MCX","").replace(" ","");wanted="FUTCOM" if root in {"GOLD","SILVER"} else "FUTIDX" if root in {"NIFTY","BANKNIFTY"} else "FUTSTK";exchange="MCX" if wanted=="FUTCOM" else "NFO"
        candidates=[]
        for row in self.master():
            if row["provider_exchange"]!=exchange or row["instrument_type"]!=wanted or str(row["underlying"]).replace(" ","")!=root:continue
            expiry=date.fromisoformat(row["expiry"])
            if expiry<now.date():continue
            if expiry==now.date() and now.time()>datetime.strptime("23:30" if exchange=="MCX" else "15:30","%H:%M").time():continue
            candidates.append(row)
        if not candidates:self._resolved[requested]=None;return None
        row=min(candidates,key=lambda item:(item["expiry"],item["trading_symbol"]));normalized={**row,"symbol":requested,"exchange":"MCX" if exchange=="MCX" else "NSE","segment":"MCX_COMM" if exchange=="MCX" else "NSE_FNO","verified":True,"provider":self.name};self._resolved[requested]=normalized;return dict(normalized)

    @staticmethod
    def _stamp(value:Mapping[str,Any])->datetime|None:
        direct=value.get("exchange_timestamp")
        if isinstance(direct,datetime):return direct if direct.tzinfo else direct.replace(tzinfo=timezone.utc)
        raw=value.get("ft") or value.get("lut")
        stamp=parse_timestamp(raw)
        if stamp:return stamp
        request=str(value.get("request_time") or "")
        for fmt in ("%H:%M:%S %d-%m-%Y","%H:%M:%S %d/%m/%Y"):
            try:return datetime.strptime(request,fmt).replace(tzinfo=IST).astimezone(timezone.utc)
            except ValueError:continue
        return None

    def _quote(self,instrument:Mapping[str,Any])->dict[str,Any]:
        key=str(instrument["instrument_key"]);tick=self.client.cached_quote(key)
        if tick and self._stamp(tick) is not None:return tick
        value=self.client.quote(str(instrument["provider_exchange"]),str(instrument["token"]));price=_number(value.get("lp"));now=time.monotonic()
        with self._quote_state_lock:
            previous=self._rest_quote_state.get(key)
            if previous and price==previous[0] and now-previous[1]>60:raise ProviderUnavailable("frozen_shoonya_rest_quote")
            self._rest_quote_state[key]=(price,previous[1] if previous and price==previous[0] else now)
        return value

    def historical_candles(self,symbol:str,interval:int=15)->list[dict[str,Any]]:
        if interval not in {1,3,5,10,15,30,60,120,240}:raise ValueError("unsupported_shoonya_interval")
        instrument=self.resolve_instrument(symbol)
        if not instrument:raise ValueError("unverified_shoonya_instrument")
        end=datetime.now(IST);days=15 if interval<=15 else 45 if interval==30 else 90
        rows=self.client.candles(instrument["provider_exchange"],instrument["token"],(end-timedelta(days=days)).timestamp(),end.timestamp(),interval)
        if not isinstance(rows,list):raise ProviderUnavailable("invalid_shoonya_candle_response")
        candles=[]
        for row in rows:
            if not isinstance(row,dict):continue
            stamp=parse_timestamp(row.get("ssboe"))
            if stamp is None:
                try:stamp=datetime.strptime(str(row.get("time")),"%d-%m-%Y %H:%M:%S").replace(tzinfo=IST).astimezone(timezone.utc)
                except ValueError:continue
            if stamp+timedelta(minutes=interval)>datetime.now(timezone.utc):continue
            candles.append({"timestamp":stamp,"open":_number(row.get("into")),"high":_number(row.get("inth")),"low":_number(row.get("intl")),"close":_number(row.get("intc")),"volume":_number(row.get("intv"))})
        candles.sort(key=lambda item:item["timestamp"]);quality=validate_candles(candles)
        # A 60-minute trend derived from the scanner's 5-minute stream needs
        # twenty completed 60-minute observations (240 base candles).
        required = 240 if interval <= 5 else 200 if interval <= 15 else 80 if interval <= 30 else 50
        if len(candles)<required or not quality["valid"]:raise ProviderUnavailable("unsafe_shoonya_candles")
        return candles

    def get_market_data(self,symbol:str,**kwargs)->dict[str,Any]:
        interval=int(str(kwargs.get("interval","5m")).lower().replace("m",""));key=f"{symbol}:{interval}m";cached=self.cache.get_validated(key,lambda value:assess_market_data(value)["valid"])
        if cached:return cached
        instrument=self.resolve_instrument(symbol)
        if not instrument:raise ValueError("unverified_shoonya_instrument")
        self.client.login();self.client.subscribe([instrument["instrument_key"]])
        try:self.client.start_websocket(wait_seconds=3)
        except Exception:LOGGER.warning("Shoonya WebSocket unavailable; controlled REST quote fallback")
        quote=self._quote(instrument);price=_number(quote.get("lp"));stamp=self._stamp(quote)
        if price<=0 or stamp is None:raise ProviderUnavailable("invalid_shoonya_quote")
        candles=self.historical_candles(symbol,interval);latest=candles[-1];open_value=_number(quote.get("o"),latest["open"]);day_high=_number(quote.get("h"),max(item["high"] for item in candles[-75:]));day_low=_number(quote.get("l"),min(item["low"] for item in candles[-75:]));volume=_number(quote.get("v"),latest["volume"])
        result={**instrument,"price":price,"open":pd.Series([item["open"] for item in candles]),"high":pd.Series([item["high"] for item in candles]),"low":pd.Series([item["low"] for item in candles]),"close":pd.Series([item["close"] for item in candles]),"volume":pd.Series([item["volume"] for item in candles]),"open_value":open_value,"latest_volume":volume,"day_high":day_high,"day_low":day_low,"timestamp":stamp,"receive_timestamp":quote.get("receive_timestamp") or datetime.now(timezone.utc),"is_live":True,"is_delayed":False,"is_stale":False,"candles":candles,"interval_minutes":interval}
        quality=assess_market_data(result);result.update(data_quality=quality,delay_seconds=quality["delay_seconds"],is_stale=quality["is_stale"],freshness_status=quality["status"])
        if not quality["valid"]:raise ProviderUnavailable("unsafe_shoonya_market_data:"+",".join(quality["errors"][:4]))
        self.cache.set(key,result);return result

    def get_live_price(self,symbol:str)->float|None:return round(float(self.get_market_data(symbol)["price"]),2)
    def get_many(self,symbols:Iterable[str],**kwargs)->dict[str,dict[str,Any]]:
        result={}
        for symbol in dict.fromkeys(str(item) for item in symbols):
            try:result[symbol]=self.get_market_data(symbol,**kwargs)
            except Exception as error:LOGGER.warning("Shoonya data unavailable for %s: %s",symbol[:32],type(error).__name__)
        return result
    def websocket_capability(self)->dict[str,Any]:return {"supported":True,"single_connection":True,**self.client.health()}
    def close(self)->bool:return self.client.logout()

def get_shoonya_status()->dict[str,Any]:
    provider=ShoonyaProvider();return {"configured":provider.available,"capabilities":dict(provider.documented_capabilities),**provider.client.health()}
