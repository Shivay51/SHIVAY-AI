"""Read-only Angel One SmartAPI market-data provider."""
from __future__ import annotations
import json, os, socket, uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from instrument_master import resolve_instrument
class AngelProviderError(RuntimeError): pass
class AngelReadOnlyProvider:
 name="angelone_primary"; signal_capable=False; BASE_URL="https://apiconnect.angelone.in"
 def __init__(self,timeout=15):
  self.api_key=os.getenv("ANGEL_API_KEY","").strip(); self.client_id=os.getenv("ANGEL_CLIENT_ID","").strip() or os.getenv("ANGEL_CLIENT_CODE","").strip(); self.pin=os.getenv("ANGEL_PIN","").strip(); self.totp=os.getenv("ANGEL_TOTP","").strip() or os.getenv("ANGEL_TOTP_SECRET","").strip(); self.timeout=timeout; self.jwt_token:Optional[str]=None
 @property
 def configured(self): return all((self.api_key,self.client_id,self.pin,self.totp))
 @property
 def available(self): return self.configured
 def health_check(self): return {"provider":self.name,"configured":self.configured,"connected":bool(self.jwt_token),"read_only":True}
 def connect(self):
  if not self.configured: raise AngelProviderError("Angel credentials are not configured.")
  response=self._request("/rest/auth/angelbroking/user/v1/loginByPassword",{"clientcode":self.client_id,"password":self.pin,"totp":self.totp}); data=response.get("data") or {}; self.jwt_token=data.get("jwtToken")
  if not self.jwt_token: raise AngelProviderError(response.get("message","Angel login failed."))
  return data
 def get_live_price(self,symbol): return float(self.get_market_data(symbol)["price"])
 def get_market_data(self,symbol,**_):
  i=resolve_instrument(symbol); token=i and (i.get("angel_token") or i.get("symboltoken"))
  if not i: raise AngelProviderError("unverified_instrument")
  if not token: raise AngelProviderError("angel_symbol_token_not_configured")
  if not self.jwt_token: self.connect()
  data=(self._request("/rest/secure/angelbroking/order/v1/getLtpData",{"exchange":i.get("exchange") or "NSE","symboltoken":str(token),"tradingsymbol":i["trading_symbol"]}).get("data") or {})
  if data.get("ltp") is None: raise AngelProviderError("angel_ltp_missing")
  return {**i,"price":float(data["ltp"]),"timestamp":datetime.now(timezone.utc),"provider":self.name,"is_live":True,"is_delayed":False,"is_stale":False,"verified":True,"read_only":True}
 def get_many(self,symbols,**kwargs):
  result={}
  for s in symbols:
   try: result[s]=self.get_market_data(s,**kwargs)
   except Exception: continue
  return result
 def close(self):
  if self.jwt_token:
   try: self._request("/rest/secure/angelbroking/user/v1/logout",{"clientcode":self.client_id})
   except Exception: pass
  self.jwt_token=None
 def _request(self,path,payload):
  try: local_ip=socket.gethostbyname(socket.gethostname())
  except OSError: local_ip="127.0.0.1"
  try:
   with urlopen(Request("https://api.ipify.org",headers={"User-Agent":"SHIVAY-AI/1.0"}),timeout=min(self.timeout,5)) as r: public_ip=r.read(64).decode().strip() or local_ip
  except (URLError,TimeoutError,OSError): public_ip=local_ip
  mac=":".join(f"{(uuid.getnode()>>shift)&255:02x}" for shift in range(40,-1,-8)); headers={"Content-Type":"application/json","Accept":"application/json","X-PrivateKey":self.api_key,"X-UserType":"USER","X-SourceID":"WEB","X-ClientLocalIP":local_ip,"X-ClientPublicIP":public_ip,"X-MACAddress":mac}
  if self.jwt_token: headers["Authorization"]=f"Bearer {self.jwt_token}"
  try:
   with urlopen(Request(f"{self.BASE_URL}{path}",data=json.dumps(payload).encode(),headers=headers,method="POST"),timeout=self.timeout) as r: body=json.loads(r.read().decode())
  except HTTPError as e: raise AngelProviderError(f"Angel SmartAPI request failed: HTTP {e.code}") from e
  except (URLError,TimeoutError,OSError,json.JSONDecodeError) as e: raise AngelProviderError(f"Angel SmartAPI request failed: {type(e).__name__}") from e
  if not isinstance(body,dict): raise AngelProviderError("Angel SmartAPI returned an invalid response.")
  return body
