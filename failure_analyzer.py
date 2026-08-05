"""Aggregate rejection and losing-trade causes without changing strategy."""
from __future__ import annotations
from collections import Counter
from typing import Any,Iterable,Mapping
from trade_journal import load_events
def analyze_failures(events:Iterable[Mapping[str,Any]]|None=None)->dict[str,Any]:
    rows=list(events) if events is not None else load_events();reasons=Counter();losses=Counter()
    for row in rows:
        if row.get("event_type")=="REJECTION":reasons.update(str(x) for x in row.get("reasons",[]))
        if row.get("event_type")=="OUTCOME" and str(row.get("result","")).upper()=="LOSS":losses[str(row.get("setup","UNKNOWN"))]+=1
    return {"rejection_reasons":dict(reasons.most_common()),"losing_setups":dict(losses.most_common()),"fake_breakout_failures":reasons.get("fake_breakout",0),"late_entry_failures":sum(v for k,v in reasons.items() if "late" in k or "travelled" in k),"data_delay_failures":reasons.get("delayed_data",0)}

