"""Market-data validation and freshness policy for SHIVAY AI."""
from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

MAX_SIGNAL_DELAY_SECONDS = 180
MAX_FUTURE_SKEW_SECONDS = 30
FUTURE_TYPES = {"FUTIDX", "FUTSTK", "FUTCOM"}

def _num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None

def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            number=float(value);number=number/1000 if number>10_000_000_000 else number
            return datetime.fromtimestamp(number, timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        numeric=value.strip()
        try:
            number=float(numeric);number=number/1000 if number>10_000_000_000 else number
            return datetime.fromtimestamp(number,timezone.utc)
        except (ValueError,OSError,OverflowError):pass
        for parser in (lambda item:datetime.fromisoformat(item.replace("Z","+00:00")),lambda item:datetime.strptime(item,"%Y-%m-%d %H:%M:%S"),lambda item:datetime.strptime(item,"%d/%m/%Y %H:%M:%S")):
            try:
                parsed=parser(value.strip());return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:continue
        return None
    return None

def validate_ohlcv(open_value: Any, high: Any, low: Any, close: Any, volume: Any) -> list[str]:
    o, h, l, c, v = map(_num, (open_value, high, low, close, volume))
    errors=[]
    if None in (o,h,l,c,v): errors.append("non_numeric_ohlcv")
    elif min(o,h,l,c) <= 0: errors.append("non_positive_price")
    elif v < 0: errors.append("negative_volume")
    elif h < max(o,c,l) or l > min(o,c,h): errors.append("impossible_ohlc")
    return errors

def validate_candles(candles: Sequence[Mapping[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc); errors=[]; warnings=[]; timestamps=[]
    if not candles: errors.append("missing_candles")
    for item in candles:
        errors.extend(validate_ohlcv(item.get("open"),item.get("high"),item.get("low"),item.get("close"),item.get("volume",0)))
        stamp=parse_timestamp(item.get("timestamp"))
        if stamp is None: errors.append("missing_timestamp")
        else: timestamps.append(stamp)
    if timestamps:
        if len(set(timestamps)) != len(timestamps): errors.append("duplicate_candles")
        if timestamps != sorted(timestamps): errors.append("out_of_order_candles")
        if timestamps[-1] > now.astimezone(timezone.utc).replace(microsecond=now.microsecond) and (timestamps[-1]-now).total_seconds()>MAX_FUTURE_SKEW_SECONDS: errors.append("future_timestamp")
        closes=[_num(x.get("close")) for x in candles[-5:]]
        if len(closes)>=5 and len(set(closes))==1: warnings.append("possibly_frozen")
    unique_errors=sorted(set(errors)); score=max(0,100-len(unique_errors)*25-len(warnings)*10)
    return {"valid":not unique_errors,"errors":unique_errors,"warnings":warnings,"score":score,"status":"GOOD" if score>=80 else "DEGRADED" if score>=50 else "REJECTED"}

def validate_instrument_contract(data: Mapping[str, Any], requested_symbol: str | None = None, today: date | None = None) -> list[str]:
    errors=[];symbol=str(requested_symbol or data.get("symbol") or "").strip().upper();exchange=str(data.get("exchange") or "").upper();segment=str(data.get("segment") or "").upper();kind=str(data.get("instrument_type") or "").upper();today=today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    commodity_names={"GOLD","SILVER","CRUDEOIL","CRUDE OIL","NATURALGAS","NATURAL GAS","COPPER"}
    normalized_symbol=symbol.removesuffix(" FUT").replace("MCX","").strip()
    wants_future=symbol.endswith(" FUT") or normalized_symbol in commodity_names
    wants_mcx=normalized_symbol in commodity_names or exchange=="MCX" or segment=="MCX_COMM"
    if wants_future and kind not in FUTURE_TYPES:errors.append("cash_futures_mismatch")
    if wants_mcx and (exchange!="MCX" or segment!="MCX_COMM" or kind!="FUTCOM"):errors.append("mcx_contract_mismatch")
    if wants_future and not wants_mcx and (exchange!="NSE" or segment!="NSE_FNO" or kind not in {"FUTIDX","FUTSTK"}):errors.append("nse_futures_contract_mismatch")
    if wants_future:
        expiry=str(data.get("expiry") or "")
        try:expiry_date=date.fromisoformat(expiry)
        except ValueError:expiry_date=None
        if expiry_date is None:errors.append("unidentified_futures_contract")
        elif expiry_date<today:errors.append("expired_contract")
        if not data.get("security_id"):errors.append("missing_security_id")
        if not data.get("instrument_key"):errors.append("missing_instrument_key")
        if not data.get("lot_size"):errors.append("missing_lot_size")
        if not data.get("tick_size"):errors.append("missing_tick_size")
        underlying=str(data.get("underlying") or "").upper().replace(" ","");expected=normalized_symbol.replace(" ","")
        aliases={"BANKNIFTY":{"BANKNIFTY","NIFTYBANK"},"NIFTY":{"NIFTY"},"GOLD":{"GOLD"},"SILVER":{"SILVER"},"CRUDEOIL":{"CRUDEOIL"},"NATURALGAS":{"NATURALGAS"},"COPPER":{"COPPER"}}
        if underlying and underlying not in aliases.get(expected,{expected}):errors.append("wrong_underlying_contract")
    provider=str(data.get("provider") or "").lower()
    trading=str(data.get("trading_symbol") or "").upper()
    if wants_mcx and (provider=="yahoo_emergency" or trading in {"GC=F","SI=F"} or exchange=="COMEX"):errors.append("comex_is_not_mcx")
    return sorted(set(errors))

def assess_market_data(data: Mapping[str, Any], now: datetime | None = None, max_delay_seconds: int = MAX_SIGNAL_DELAY_SECONDS) -> dict[str, Any]:
    now=now or datetime.now(timezone.utc); stamp=parse_timestamp(data.get("timestamp")); errors=[]
    if stamp is None: errors.append("missing_timestamp"); delay=float("inf")
    else: delay=max(0,(now-stamp.astimezone(timezone.utc)).total_seconds()); errors += ["future_timestamp"] if (stamp-now).total_seconds()>MAX_FUTURE_SKEW_SECONDS else []
    errors += validate_ohlcv(data.get("open_value",data.get("price")),data.get("day_high",data.get("price")),data.get("day_low",data.get("price")),data.get("price"),data.get("latest_volume",0))
    if delay>max_delay_seconds: errors.append("stale_data")
    errors.extend(validate_instrument_contract(data))
    if data.get("requested_instrument_type") and data.get("requested_instrument_type") != data.get("instrument_type"): errors.append("cash_futures_mismatch")
    if data.get("verified") is False: errors.append("unverified_instrument")
    candles=data.get("candles")
    if isinstance(candles,Sequence) and not isinstance(candles,(str,bytes)):
        candle_quality=validate_candles(candles,now)
        errors.extend(candle_quality.get("errors",[]))
        if "possibly_frozen" in candle_quality.get("warnings",[]):errors.append("frozen_feed")
    price=_num(data.get("price"));day_high=_num(data.get("day_high"));day_low=_num(data.get("day_low"))
    if price is not None and day_high is not None and price>day_high*1.002:errors.append("price_above_day_high")
    if price is not None and day_low is not None and price<day_low*.998:errors.append("price_below_day_low")
    errors=sorted(set(errors)); completeness_fields=("price","timestamp","exchange","segment","instrument_type","provider")
    completeness=sum(data.get(field) not in (None,"") for field in completeness_fields)/len(completeness_fields)*100
    consistency=max(0,100-sum(error in errors for error in ("impossible_ohlc","price_above_day_high","price_below_day_low","cash_futures_mismatch","mcx_contract_mismatch","nse_futures_contract_mismatch"))*25)
    freshness=0 if delay==float("inf") else max(0,min(100,100-delay/max(1,max_delay_seconds)*100))
    score=max(0,round(freshness*.4+completeness*.25+consistency*.25+(100 if not errors else 0)*.1,1))
    return {"valid":not errors,"is_stale":"stale_data" in errors,"delay_seconds":None if delay==float("inf") else round(delay,1),"errors":errors,"score":score,"freshness_score":round(freshness,1),"completeness_score":round(completeness,1),"consistency_score":round(consistency,1),"status":"GOOD" if not errors else "REJECTED"}

def is_signal_safe(data: Mapping[str, Any]) -> bool:
    return bool(assess_market_data(data).get("valid"))
