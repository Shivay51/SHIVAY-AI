"""Scenario-based NIFTY/BANKNIFTY outlook formatting; never predicts exact prices."""
from __future__ import annotations
from typing import Any,Mapping
def build_index_outlook(name:str,session:Mapping[str,Any],probabilities:Mapping[str,Any],regime:str="UNKNOWN",strength:float=0.0,risk:str="HIGH",preferred:str="NO TRADE")->dict[str,Any]:
    def n(key,default=0.0):
        try:return float(session.get(key,default) or default)
        except (TypeError,ValueError):return default
    close=n("close",n("price"));high=n("high");low=n("low");span=max(high-low,close*.006 if close else 0);support=[round(low,2),round(low-span*.35,2)] if low else [];resistance=[round(high,2),round(high+span*.35,2)] if high else []
    available=bool(session.get("available")) and close>0
    direction=1 if preferred=="BUY" else -1 if preferred=="SELL" else 0;open_center=close+direction*span*.12
    opening_range=[round(open_center-span*.10,2),round(open_center+span*.10,2)] if available else []
    high_range=[round(max(high,opening_range[1])+span*.12,2),round(max(high,opening_range[1])+span*.32,2)] if available else []
    low_range=[round(min(low,opening_range[0])-span*.32,2),round(min(low,opening_range[0])-span*.12,2)] if available else []
    return {"symbol":name,"available":available,"latest_price":round(close,2) if available else None,"provider":session.get("provider","UNAVAILABLE"),"data_timestamp":session.get("timestamp",session.get("date")),"previous_close":round(close,2) if available else None,"expected_opening_bias":preferred if preferred in {"BUY","SELL"} else "NO TRADE","tomorrow_outlook":"POSITIVE FOR TOMORROW" if preferred=="BUY" else "NEGATIVE FOR TOMORROW" if preferred=="SELL" else "SIDEWAYS FOR TOMORROW","opening_range":opening_range,"high_range":high_range,"low_range":low_range,"bullish_probability":probabilities.get("bullish"),"bearish_probability":probabilities.get("bearish"),"sideways_probability":probabilities.get("sideways"),"gap_up_probability":probabilities.get("gap_up"),"gap_down_probability":probabilities.get("gap_down"),"gap_trap_probability":probabilities.get("gap_trap"),"trend_continuation_probability":probabilities.get("continuation"),"trend_reversal_probability":probabilities.get("reversal"),"market_regime":regime,"market_strength":strength,"support_levels":support,"resistance_levels":resistance,"bullish_above":round(high+span*.08,2) if high else None,"bearish_below":round(low-span*.08,2) if low else None,"expected_range":[round(low-span*.25,2),round(high+span*.25,2)] if available else [],"risk_level":risk,"preferred_direction":preferred,"no_trade_condition":"NO TRADE if data is stale, opening gap traps, or price remains between confirmation levels"}
