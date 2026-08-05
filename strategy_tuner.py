"""Conservative, versioned tuning recommendations with no automatic live-rule mutation."""
from __future__ import annotations
import json,os,shutil,tempfile
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Mapping
from accuracy_analyzer import analyze_accuracy
ROOT=Path(__file__).resolve().parent;STATE=ROOT/"storage"/"strategy_versions.json";BACKUP=ROOT/"storage"/"strategy_versions.json.bak";MIN_SAMPLE=50
def recommend_tuning(metrics:Mapping[str,Any]|None=None)->dict[str,Any]:
    value=dict(metrics or analyze_accuracy());sample=int(value.get("sample_size",0));recommendations=[]
    if sample<MIN_SAMPLE:return {"eligible":False,"sample_size":sample,"minimum_sample":MIN_SAMPLE,"recommendations":[],"reason":"insufficient_sample"}
    if float(value.get("late_entry_rate",0))>15:recommendations.append("review_entry_extension_threshold")
    if int(value.get("data_delay_failures",0))>5:recommendations.append("review_provider_freshness")
    return {"eligible":bool(recommendations),"sample_size":sample,"minimum_sample":MIN_SAMPLE,"recommendations":recommendations,"requires_walk_forward":True,"automatic_rule_changes":False}
def version_strategy(snapshot:Mapping[str,Any],approved:bool=False)->str|None:
    if not approved:return None
    STATE.parent.mkdir(parents=True,exist_ok=True);data=[]
    if STATE.exists():
        try:data=json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:data=[]
        shutil.copy2(STATE,BACKUP)
    version=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ");data.append({"version":version,"snapshot":dict(snapshot)})
    fd,tmp=tempfile.mkstemp(prefix=".strategy-",suffix=".tmp",dir=str(STATE.parent))
    with os.fdopen(fd,"w",encoding="utf-8") as h:json.dump(data,h,indent=2);h.flush();os.fsync(h.fileno())
    os.replace(tmp,STATE);return version

