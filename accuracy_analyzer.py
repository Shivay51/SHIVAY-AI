"""Sample-aware signal accuracy and failure metrics."""
from __future__ import annotations
from collections import defaultdict
from typing import Any,Iterable,Mapping
from trade_journal import load_events
def _num(v:Any)->float:
    try:return float(str(v).replace("%",""))
    except (TypeError,ValueError):return 0.0
def analyze_accuracy(events:Iterable[Mapping[str,Any]]|None=None)->dict[str,Any]:
    rows=list(events) if events is not None else load_events();outcomes=[r for r in rows if r.get("event_type")=="OUTCOME"];rejections=[r for r in rows if r.get("event_type")=="REJECTION"]
    groups:dict[str,dict[str,float]]=defaultdict(lambda:{"trades":0,"wins":0,"pnl":0.0})
    for row in outcomes:
        result=str(row.get("result","")).upper();keys=(str(row.get("side","UNKNOWN")).upper(),str(row.get("symbol","UNKNOWN")).upper(),str(row.get("setup","UNKNOWN")).upper(),str(row.get("provider","UNKNOWN")).upper())
        for key in keys:g=groups[key];g["trades"]+=1;g["wins"]+=result=="WIN";g["pnl"]+=_num(row.get("pnl"))
    formatted={k:{**v,"win_rate":round(v["wins"]/v["trades"]*100,2) if v["trades"] else 0.0} for k,v in groups.items()}
    wins=sum(str(r.get("result","")).upper()=="WIN" for r in outcomes);profit=sum(max(0,_num(r.get("pnl"))) for r in outcomes);loss=sum(abs(min(0,_num(r.get("pnl")))) for r in outcomes)
    return {"sample_size":len(outcomes),"sufficient_sample":len(outcomes)>=50,"wins":wins,"losses":len(outcomes)-wins,"win_rate":round(wins/len(outcomes)*100,2) if outcomes else 0.0,"expectancy":round(sum(_num(r.get("pnl")) for r in outcomes)/len(outcomes),2) if outcomes else 0.0,"profit_factor":round(profit/loss,2) if loss else 0.0,"average_mfe":round(sum(_num(r.get("mfe")) for r in outcomes)/len(outcomes),2) if outcomes else 0.0,"average_mae":round(sum(_num(r.get("mae")) for r in outcomes)/len(outcomes),2) if outcomes else 0.0,"late_entry_rate":round(sum("late" in " ".join(r.get("reasons",[])) for r in rejections)/len(rejections)*100,2) if rejections else 0.0,"data_delay_failures":sum("delayed_data" in r.get("reasons",[]) for r in rejections),"groups":formatted}
def get_accuracy_report()->dict[str,Any]:return analyze_accuracy()

