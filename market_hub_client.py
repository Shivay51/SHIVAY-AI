"""Read-only adapter for the official Saral TradeX SDK.

The audited SDK has no quote, candle, instrument-master, or market-tick API;
those methods deliberately report unsupported instead of guessing endpoints.
"""
from __future__ import annotations
import importlib,importlib.util,logging,os,sys
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
LOGGER=logging.getLogger("shivay.provider.markethub")
_VENDOR=Path(__file__).resolve().parent/"vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:sys.path.insert(0,str(_VENDOR))
READ_ONLY_CAPABILITIES={"login":True,"token_reuse":True,"user_profile":True,"exchange_status":True,"instrument_master":False,"symbol_search":False,"quote":False,"ltp":False,"ohlc":False,"candles":False,"historical":False,"live_market_data":False,"market_websocket":False,"order_trade_websocket":True,"nse_futures_market_data":False,"mcx_gold_market_data":False,"mcx_silver_market_data":False}
class MarketHubUnavailable(RuntimeError):pass
class MarketHubClient:
    name="market_hub"
    def __init__(self,env_file:str=".env"):
        env_path=Path(env_file)
        if not env_path.is_absolute():env_path=Path(__file__).resolve().parent/env_path
        load_dotenv(env_path,override=False)
        self.app_key=os.getenv("TRADEX_APP_KEY");self.secret_key=os.getenv("TRADEX_SECRET_KEY");self.base_url=os.getenv("TRADEX_BASE_URL");self.client_id=os.getenv("TRADEX_CLIENT_ID");self.user_id=os.getenv("TRADEX_USER_ID");self.websocket_url=os.getenv("TRADEX_WEBSOCKET_URL");self.env_file=env_file;self._client=None
    @property
    def configured(self)->bool:return all((self.app_key,self.secret_key,self.base_url,self.client_id,self.user_id))
    @property
    def available(self)->bool:
        try:return self.configured and importlib.util.find_spec("tradex_client") is not None
        except Exception:return False
    def capabilities(self)->dict[str,bool]:return dict(READ_ONLY_CAPABILITIES)
    def _sdk(self):
        if not self.configured:raise MarketHubUnavailable("TradeX credentials are not configured")
        if self._client is None:
            try:
                cls=importlib.import_module("tradex_client").TradeXClient
                kwargs={"app_key":self.app_key,"secret_key":self.secret_key,"base_url":self.base_url,"client_id":self.client_id,"user_id":self.user_id,"debug":False,"timeout":8,"env_file":str(Path(__file__).resolve().parent/self.env_file) if not Path(self.env_file).is_absolute() else self.env_file}
                if self.websocket_url:kwargs["websocket_url"]=self.websocket_url
                self._client=cls(**kwargs)
            except Exception as error:raise MarketHubUnavailable(f"Official TradeX SDK unavailable: {type(error).__name__}") from None
        return self._client
    def login(self,reuse_token:bool=True)->Any:return self._sdk().login(get_new_token=not reuse_token)
    def get_user_profile(self)->Any:return self._sdk().get_user_profile()
    def get_exchange_status(self)->Any:return self._sdk().get_exchange_status()
    def get_market_data(self,*args,**kwargs):raise MarketHubUnavailable("Official audited TradeX SDK does not expose market quotes or candles")
    def get_live_price(self,*args,**kwargs):raise MarketHubUnavailable("Official audited TradeX SDK does not expose LTP")
    def start_market_websocket(self,*args,**kwargs):raise MarketHubUnavailable("Official audited WebSocket carries order/trade events only")
def get_market_hub_status()->dict[str,Any]:
    client=MarketHubClient();return {"configured":client.configured,"sdk_installed":importlib.util.find_spec("tradex_client") is not None,"available":client.available,"capabilities":client.capabilities()}
