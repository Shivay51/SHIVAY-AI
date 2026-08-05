"""Backward-compatible NSE F&O, MCX, ALL, and ADMIN Telegram routing."""
from __future__ import annotations
from typing import Any
import admin,config

MARKET_PLANS={"NSE_FO","MCX","ALL","ADMIN"};LEGACY_PLANS={"BASIC","PREMIUM","VIP"}
def normalize_audience(value:Any)->str:
    text=str(value or "ALL").strip().upper().replace(" ","_")
    return "NSE_FO" if text in {"NSE","NSE_F&O","NSE_FO"} else "MCX" if text in {"MCX","COMMODITY"} else "ADMIN" if text=="ADMIN" else "ALL"
def user_allows(user:dict[str,Any],audience:str)->bool:
    audience=normalize_audience(audience);plan=str(user.get("plan","BASIC")).upper()
    if plan in LEGACY_PLANS:return audience!="ADMIN"
    if plan=="ADMIN":return True
    if audience=="ADMIN":return False
    return plan=="ALL" or audience=="ALL" or plan==audience
def recipients(audience:str="ALL")->list[dict[str,Any]]:
    audience=normalize_audience(audience);users=admin.get_active_users();seen={int(u["id"]) for u in users};result=[u for u in users if user_allows(u,audience)]
    for value in (getattr(config,"ADMIN_ID",0),*getattr(config,"ADMIN_IDS",())):
        try:user_id=int(value)
        except (TypeError,ValueError):continue
        if user_id>0 and user_id not in seen:result.append({"id":user_id,"name":"Administrator","plan":"ADMIN","active":True});seen.add(user_id)
    return result
def audience_for_signal(signal:dict[str,Any])->str:
    segment=str(signal.get("segment","")).upper();category=str(signal.get("market_category","")).upper()
    return "MCX" if segment=="MCX_COMM" or "MCX" in category else "NSE_FO"

