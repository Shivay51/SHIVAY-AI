"""TradingView alert bridge and verified-cache provider (signals only)."""
from __future__ import annotations
import json,logging,os
from datetime import datetime,timezone,timedelta
from pathlib import Path
from typing import Any,Mapping
import pandas as pd
from data_quality import assess_market_data,validate_candles,validate_instrument_contract
from provider_failover import ProviderUnavailable
from tradingview_cache import get_tradingview_cache
from tradingview_payload import TradingViewPayload
from chandelier_exit import evaluate_chandelier_entry_state

LOGGER=logging.getLogger("shivay.tradingview.bridge");ROOT=Path(__file__).resolve().parent
ALLOWED_TIMEFRAMES={5,15,30,60}


def assert_single_provider_dataset(value:Mapping[str,Any])->str:
    """Reject any snapshot whose candles come from more than one provider."""
    sources={str(row.get("provider") or row.get("source") or value.get("provider") or "").strip().lower() for row in (value.get("candles") or [])}
    sources.discard("")
    if len(sources)>1:raise ProviderUnavailable("mixed_provider_candles_rejected:"+",".join(sorted(sources)))
    owner=str(value.get("provider") or "").strip().lower()
    if sources and owner and owner not in sources:raise ProviderUnavailable("candle_provider_mismatch")
    return owner or (next(iter(sources)) if sources else "")
def _enabled()->bool:return os.getenv("TRADINGVIEW_WEBHOOK_ENABLED","false").strip().lower() in {"1","true","yes","on"}
def load_symbol_map()->dict[str,dict[str,Any]]:
    path=Path(os.getenv("TRADINGVIEW_SYMBOL_MAP_FILE",str(ROOT/"tradingview_symbols.json")))
    try:
        value=json.loads(path.read_text(encoding="utf-8"));result={}
        for key,raw in value.items():
            if not isinstance(raw,Mapping):continue
            item=dict(raw);tv=str(item.get("tradingview_symbol") or key).strip()
            if "enabled" in item and (not bool(item.get("enabled")) or not item.get("tradingview_symbol")):continue
            if not tv:continue
            item["contract_text"]=str(item.get("contract_text") or item.get("contract") or "").strip()
            item["category"]="GIFT" if str(item.get("category","")).upper()=="CONTEXT" and "GIFT" in str(item.get("symbol","")).upper() else str(item.get("category") or "").upper()
            result[tv]=item
        return result
    except Exception:return {}
def _valid_contract(item:Mapping[str,Any],tv_symbol:str)->dict[str,Any]|None:
    if bool(item.get("context_only")):return None
    result=dict(item);result.update(trading_symbol=tv_symbol,instrument_key=result.get("instrument_key") or tv_symbol,verified=True)
    return result if not validate_instrument_contract(result,str(result.get("symbol",""))) else None

def context_snapshot(internal_symbol:str,timeframe:int=60)->dict[str,Any]|None:
    """Return configured global/GIFT context without promoting it to an Indian contract."""
    for tv,item in load_symbol_map().items():
        if bool(item.get("context_only")) and str(item.get("symbol","")).upper()==str(internal_symbol).upper():
            value=get_tradingview_cache().latest(tv,timeframe)
            if not value:return None
            return {"symbol":internal_symbol,"price":value["close"],"timestamp":value["generated_at"],"trend":value.get("trend_state"),"momentum":value.get("momentum_state"),"context_only":True,"source_type":"TRADINGVIEW_ALERT_BRIDGE"}
    return None

class TradingViewBridgeProvider:
    # Stable architecture key retained for backward compatibility. Every data
    # record uses the precise V10 source label below.
    name="tradingview_alert_bridge"
    # Emergency backup only: it can serve signals when Angel One is unusable,
    # but the provider manager never ranks it above Angel One.
    signal_capable=True
    role="EMERGENCY_BACKUP"
    def __init__(self):self.symbol_map=load_symbol_map();self.cache=get_tradingview_cache();self.available=bool(_enabled() and os.getenv("TRADINGVIEW_WEBHOOK_SECRET","").strip() and any(_valid_contract(v,k) for k,v in self.symbol_map.items()))
    def _mapping(self,symbol:str)->tuple[str,dict[str,Any]]:
        for tv,item in self.symbol_map.items():
            if str(item.get("symbol","")).upper()==str(symbol).upper():
                contract=_valid_contract(item,tv)
                if contract:return tv,contract
        raise ProviderUnavailable("tradingview_exact_contract_unavailable")
    def get_market_data(self,symbol:str,period:str="5d",interval:str="5m")->dict[str,Any]:
        tv,contract=self._mapping(symbol)
        try:tf=int(str(interval).lower().replace("m",""))
        except ValueError:tf=5
        rows=self.cache.bars(tv,tf)
        if tf not in ALLOWED_TIMEFRAMES:raise ProviderUnavailable("tradingview_timeframe_not_supported")
        candles=[{"timestamp":x["bar_timestamp"],"open":x["open"],"high":x["high"],"low":x["low"],"close":x["close"],"volume":x["volume"],"completed":True,"provider":self.name,"source":self.name} for x in rows if x.get("bar_confirmed")]
        if not candles:raise ProviderUnavailable("tradingview_cache_empty")
        quality=validate_candles(candles)
        if not quality["valid"]:raise ProviderUnavailable("tradingview_candles_rejected")
        latest=rows[-1];result={**contract,"price":latest["close"],"timestamp":latest["generated_at"],"provider":self.name,"source_type":"TRADINGVIEW_STANDARD_ALERT_BRIDGE","is_live":True,"is_delayed":False,"interval_minutes":tf,"candles":candles,"open":pd.Series([x["open"] for x in candles]),"high":pd.Series([x["high"] for x in candles]),"low":pd.Series([x["low"] for x in candles]),"close":pd.Series([x["close"] for x in candles]),"volume":pd.Series([x["volume"] for x in candles]),"open_value":latest["open"],"latest_volume":latest["volume"],"day_high":latest.get("day_high") or max(x["high"] for x in candles[-75:]),"day_low":latest.get("day_low") or min(x["low"] for x in candles[-75:]),"atr":latest.get("atr",0),"vwap":latest.get("vwap",0),"warmup_state":self.cache.warmup_state(tv,tf,contract.get("contract_text"),contract.get("category")),"received_at":datetime.now(timezone.utc).isoformat(),"exchange_timestamp":latest.get("bar_timestamp"),"data_source":self.name,"candle_source":self.name,"role":self.role}
        freshness=assess_market_data(result);result.update(delay_seconds=freshness["delay_seconds"],is_stale=freshness["is_stale"],freshness_status=freshness["status"])
        if not freshness["valid"]:raise ProviderUnavailable("tradingview_data_rejected")
        assert_single_provider_dataset(result)
        return result
    def get_many(self,symbols:list[str],**kwargs)->dict[str,dict[str,Any]]:
        result={}
        for symbol in symbols:
            try:result[symbol]=self.get_market_data(symbol,**kwargs)
            except Exception:pass
        return result
    def get_live_price(self,symbol:str)->float|None:return float(self.get_market_data(symbol)["price"])
    def health_check(self)->dict[str,Any]:
        cache=self.cache.status()
        return {"provider":self.name,"role":self.role,"configured":bool(_enabled() and os.getenv("TRADINGVIEW_WEBHOOK_SECRET","").strip()),
                "available":bool(self.available),"mapped_contracts":sum(1 for k,v in self.symbol_map.items() if _valid_contract(v,k)),
                "cached_series":cache.get("series"),"accepted_alerts":cache.get("accepted"),
                "supported_timeframes":sorted(ALLOWED_TIMEFRAMES),"outranks_primary":False,"signal_capable":True}
    def status(self)->dict[str,Any]:return self.health_check()
    def close(self)->None:pass

def _direction(item:Mapping[str,Any])->str:
    if item.get("source") in {"TRADINGVIEW_STANDARD_ALERT","TRADINGVIEW_STANDARD_ALERT_BRIDGE"} and not item.get("ready"):
        return "WAIT"
    bull=item["ema20"]>item["ema50"]>item["ema200"] and item["close"]>item["vwap"] and item["chandelier_direction"]=="BULLISH" and item.get("supertrend_direction")=="BULLISH" and item.get("momentum_state")=="BULLISH"
    bear=item["ema20"]<item["ema50"]<item["ema200"] and item["close"]<item["vwap"] and item["chandelier_direction"]=="BEARISH" and item.get("supertrend_direction")=="BEARISH" and item.get("momentum_state")=="BEARISH"
    return "BUY" if bull else "SELL" if bear else "WAIT"

def _nse_market_support(side:str)->tuple[bool,str]:
    mappings=load_symbol_map();cache=get_tradingview_cache();directions=[]
    for internal in ("NIFTY FUT","BANKNIFTY FUT"):
        tv=next((name for name,item in mappings.items() if str(item.get("symbol","")).upper()==internal),None)
        if not tv:return False,"index_contract_not_configured"
        aligned=cache.aligned(tv,(30,60),3900)
        if not aligned:return False,"index_timeframes_unavailable"
        directions.extend((_direction(aligned[30]),_direction(aligned[60])))
    if any(value not in {side,"WAIT"} for value in directions):return False,"index_direction_conflict"
    if directions.count(side)<3:return False,"index_support_weak"
    return True,"supported"

async def process_accepted_payload(application:Any,payload:TradingViewPayload)->dict[str,Any]:
    """Combine timeframes and send only a newly activated, revalidated setup."""
    if payload.event_type=="test":return {"decision":"WAIT","reason":"safe_test_event"}
    mapping=load_symbol_map().get(payload.symbol);aligned=get_tradingview_cache().aligned(payload.symbol)
    if not mapping or not aligned:return {"decision":"WAIT","reason":"timeframes_not_aligned"}
    if any(item.get("source") in {"TRADINGVIEW_STANDARD_ALERT","TRADINGVIEW_STANDARD_ALERT_BRIDGE"} and not item.get("ready") for item in aligned.values()):
        return {"decision":"WAIT","reason":"warming_up"}
    cache=get_tradingview_cache();bars15=cache.bars(payload.symbol,15,aligned[15].get("contract_text"),aligned[15].get("category"))
    candle_rows=[{"timestamp":row.get("bar_timestamp"),"open":row.get("open"),"high":row.get("high"),"low":row.get("low"),"close":row.get("close"),"volume":row.get("volume",0),"completed":row.get("bar_confirmed",False)} for row in bars15]
    first_minute=cache.latest(payload.symbol,1,aligned[15].get("contract_text"),aligned[15].get("category"))
    if first_minute:candle_rows.append({"timestamp":first_minute.get("bar_timestamp"),"open":first_minute.get("open"),"high":first_minute.get("high"),"low":first_minute.get("low"),"close":first_minute.get("close"),"volume":first_minute.get("volume",0),"completed":False})
    entry_state=evaluate_chandelier_entry_state({"interval_minutes":15,"candles":candle_rows,"price":first_minute.get("close") if first_minute else None,"confirmation_elapsed_seconds":60 if first_minute else 0},15)
    if not entry_state.get("confirmed"):return {"decision":"WAIT","reason":entry_state.get("reason","chandelier_confirmation_missing")}
    d60,d30,d15=_direction(aligned[60]),_direction(aligned[30]),_direction(aligned[15]);latest=aligned[5]
    side=str(entry_state.get("side")) if d60==d30==d15==entry_state.get("side") else "WAIT"
    if side=="WAIT" or not aligned[15]["bar_confirmed"]:return {"decision":"WAIT","reason":"direction_not_confirmed"}
    signal=aligned[15];signal_candle=entry_state["signal_candle"];confirmation_candle=entry_state["confirmation_candle"];atr=float(signal["atr"]);price=float(latest["close"])
    side_setup=signal.get("preliminary_buy") if side=="BUY" else signal.get("preliminary_sell")
    momentum_ok=signal.get("macd_histogram",0)>0 and 48<=signal.get("rsi",0)<=76 if side=="BUY" else signal.get("macd_histogram",0)<0 and 24<=signal.get("rsi",100)<=52
    if atr<=0 or signal["adx"]<18 or signal["relative_volume"]<0.75 or not signal.get("setup_valid") or not side_setup or not momentum_ok:return {"decision":"WAIT","reason":"setup_quality_weak"}
    buffer=atr*max(0,float(getattr(__import__('config'),"ENTRY_BREAK_BUFFER_ATR",.05)))
    activated=price>=float(signal_candle["close"])-buffer if side=="BUY" else price<=float(signal_candle["close"])+buffer
    if not activated:return {"decision":"WAIT","reason":"entry_not_activated"}
    if (side=="BUY" and price-float(confirmation_candle["close"])>atr*.75) or (side=="SELL" and float(confirmation_candle["close"])-price>atr*.75):return {"decision":"WAIT","reason":"entry_missed"}
    from tradeplan import create_trade_plan
    plan=create_trade_plan(price,atr,side,{"support":signal["swing_low"],"resistance":signal["swing_high"],"ema20":signal["ema20"],"vwap":signal["vwap"],"chandelier_15m":{"long_stop":signal["chandelier_long_stop"],"short_stop":signal["chandelier_short_stop"]}})
    hard_invalidation=float(signal_candle["low"] if side=="BUY" else signal_candle["high"])
    plan["sl"]=max(float(plan["sl"]),hard_invalidation) if side=="BUY" else min(float(plan["sl"]),hard_invalidation)
    if (side=="BUY" and plan["sl"]>=plan["entry"]) or (side=="SELL" and plan["sl"]<=plan["entry"]):return {"decision":"WAIT","reason":"signal_candle_invalidation_makes_risk_invalid"}
    if not all(float(plan.get(k) or 0)>0 for k in ("entry","sl","target1","target2","target3")):return {"decision":"WAIT","reason":"levels_unavailable"}
    strong=signal["adx"]>=28 and signal["relative_volume"]>=1.2;decision=f"STRONG {side}" if strong else side;internal=str(mapping["symbol"])
    if str(mapping.get("segment","")).upper()=="NSE_FNO":
        supported,reason=_nse_market_support(side)
        if not supported:return {"decision":"WAIT","reason":reason}
    trade={**plan,"symbol":internal,"decision":decision,"side":side,"price":price,"atr":atr,"support":signal["swing_low"],"resistance":signal["swing_high"],"trend":"STRONG BULLISH" if side=="BUY" and strong else "STRONG BEARISH" if strong else "BULLISH" if side=="BUY" else "BEARISH","signal_candle":signal_candle,"signal_candle_open":signal_candle["open"],"signal_candle_high":signal_candle["high"],"signal_candle_low":signal_candle["low"],"signal_candle_close":signal_candle["close"],"signal_candle_timestamp":signal_candle.get("timestamp"),"confirmation_candle":confirmation_candle,"confirmation_candle_timestamp":confirmation_candle.get("timestamp"),"chandelier_signal_level":entry_state.get("chandelier_level"),"hard_invalidation_level":hard_invalidation,"chandelier_entry_state":entry_state,"entry_confirmed":True,"requires_entry_confirmation":False,"entry_trigger_status":"CONFIRMED","entry_confirmation_reason":entry_state.get("reason"),"expected_hold":"25-30 MIN / 30-60 MIN / 1-2 HOURS","provider":TradingViewBridgeProvider.name,"market_category":"MCX" if mapping.get("exchange")=="MCX" else "NSE F&O","segment":mapping.get("segment"),"instrument_type":mapping.get("instrument_type"),"valid_until":(datetime.now(timezone.utc)+timedelta(minutes=18)).isoformat()}
    from signal_memory import signal_exists,add_signal
    if signal_exists(internal):return {"decision":"WAIT","reason":"duplicate_signal"}
    sender=__import__('telegram_service').send_buy_signal if side=="BUY" else __import__('telegram_service').send_sell_signal
    delivered=await sender(application,trade)
    if delivered:
        add_signal(internal);__import__('trade_monitor').add_trade(trade)
        try:
            from trade_journal import record_signal
            record_signal(trade)
        except Exception:LOGGER.warning("TradingView signal journal update failed safely")
    return {"decision":decision if delivered else "WAIT","delivered":bool(delivered),"trade":trade}

def market_outlook(symbol:str)->dict[str,Any]:
    provider=TradingViewBridgeProvider();tv,mapping=provider._mapping(symbol);aligned=provider.cache.aligned(tv,(30,60),3900)
    if not aligned:return {"status":"UNAVAILABLE","reason":"insufficient_verified_candles"}
    bars=provider.cache.bars(tv,60)
    if len(bars)<20:return {"status":"UNAVAILABLE","reason":"insufficient_verified_candles"}
    last=aligned[60];atr=last["atr"];bias60=_direction(last);bias30=_direction(aligned[30]);bias=bias60 if bias60==bias30 else "WAIT";close=last["close"];previous=last["previous_close"]
    gift=context_snapshot("GIFT NIFTY",60);gift_direction=str((gift or {}).get("trend","SIDEWAYS"));gap_atr=(close-previous)/atr if atr>0 else 0
    if symbol.upper()=="NIFTY FUT" and gift and ((bias=="BUY" and gift_direction=="BEARISH") or (bias=="SELL" and gift_direction=="BULLISH")):bias="WAIT"
    risk="HIGH" if atr/close>0.018 or abs(gap_atr)>1 else "LOW" if atr/close<0.008 else "MEDIUM"
    return {"status":"READY","symbol":symbol,"bias":"POSITIVE FOR TOMORROW" if bias=="BUY" else "NEGATIVE FOR TOMORROW" if bias=="SELL" else "SIDEWAYS FOR TOMORROW","open_range":(round(close-atr*.35,2),round(close+atr*.35,2)),"high_range":(round(close+atr*.4,2),round(close+atr*1.2,2)),"low_range":(round(close-atr*1.2,2),round(close-atr*.4,2)),"bullish_above":last["swing_high"],"bearish_below":last["swing_low"],"support":last["swing_low"],"support2":last["previous_day_low"],"resistance":last["swing_high"],"resistance2":last["previous_day_high"],"plan":"BUY ON DIP" if bias=="BUY" else "SELL ON RISE" if bias=="SELL" else "WAIT","risk":risk,"gift_context_available":bool(gift),"gap_context":"GAP_UP" if gap_atr>.35 else "GAP_DOWN" if gap_atr<-.35 else "FLAT"}

def format_market_outlook(value: Mapping[str, Any]) -> str:
    if value.get("status") != "READY":
        return "\U0001f305 SHIVAY AI \u2014 MARKET OUTLOOK\n\nWAIT\n\nValidated market history is not yet sufficient."
    name = str(value.get("symbol", "")).replace(" FUT", "")

    def band(pair: tuple[float, float]) -> str:
        return f"{pair[0]:,.2f} \u2013 {pair[1]:,.2f}"

    return (
        f"\U0001f305 SHIVAY AI \u2014 MARKET OUTLOOK\n\n{name}\n\n{value['bias']}\n\n"
        f"Open: {band(value['open_range'])}\nHigh: {band(value['high_range'])}\nLow: {band(value['low_range'])}\n\n"
        f"Bullish Above: {value['bullish_above']:,.2f}\nBearish Below: {value['bearish_below']:,.2f}\n\n"
        f"Support: {value['support']:,.2f} / {value['support2']:,.2f}\n"
        f"Resistance: {value['resistance']:,.2f} / {value['resistance2']:,.2f}\n\n"
        f"Best Plan: {value['plan']}\nRisk: {value['risk']}"
    )
