"""Read-only session and market-feed wrapper for Shoonya's official SDK."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

LOGGER = logging.getLogger("shivay.provider.shoonya.client")
logging.getLogger("NorenRestApiPy.NorenApi").setLevel(logging.WARNING)
ROOT = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")
REST_HOST = "https://api.shoonya.com/NorenWClientTP/"
WEBSOCKET_URL = "wss://api.shoonya.com/NorenWSTP/"
REQUIRED_FIELDS = ("SHOONYA_USER_ID", "SHOONYA_PASSWORD", "SHOONYA_VENDOR_CODE", "SHOONYA_API_KEY", "SHOONYA_IMEI")


class ShoonyaUnavailable(RuntimeError): pass
class ShoonyaAuthenticationError(ShoonyaUnavailable): pass


class ShoonyaClient:
    """Provides only authentication and documented read-only market operations."""
    name = "shoonya_primary"

    def __init__(self, env_file: str | os.PathLike[str] = ".env") -> None:
        path = Path(env_file)
        if not path.is_absolute(): path = ROOT / path
        load_dotenv(path, override=False)
        self.credentials = {name: (os.getenv(name) or "").strip() for name in REQUIRED_FIELDS}
        self.totp_secret = (os.getenv("SHOONYA_TOTP_SECRET") or "").strip()
        self.current_otp = (os.getenv("SHOONYA_TWO_FA") or "").strip()
        self.session_token = (os.getenv("SHOONYA_SESSION_TOKEN") or "").strip()
        self.enabled = (os.getenv("ENABLE_SHOONYA", "true").strip().lower() not in {"0", "false", "no"})
        self._api: Any = None
        self._authenticated = False
        self._login_lock = threading.RLock()
        self._request_lock = threading.RLock()
        self._last_request = 0.0
        self._ws_lock = threading.RLock()
        self._ws_started = False
        self._ws_open = threading.Event()
        self._ws_stop = threading.Event()
        self._subscriptions: set[str] = set()
        self._quotes: dict[str, dict[str, Any]] = {}
        self._quote_lock = threading.RLock()
        self._reconnect_thread: threading.Thread | None = None

    @staticmethod
    def sdk_available() -> bool:
        try:
            from NorenRestApiPy.NorenApi import NorenApi  # noqa:F401
            return True
        except ImportError:
            return False

    def missing_environment_variables(self) -> list[str]:
        missing = [name for name, value in self.credentials.items() if not value]
        if not self.totp_secret and not self.current_otp and not self.session_token:
            missing.append("SHOONYA_TOTP_SECRET")
        return missing

    @property
    def configured(self) -> bool: return self.enabled and self.sdk_available() and not self.missing_environment_variables()
    @property
    def authenticated(self) -> bool: return self._authenticated

    def _build_api(self):
        if self._api is None:
            from NorenRestApiPy.NorenApi import NorenApi
            self._api = NorenApi(host=REST_HOST, websocket=WEBSOCKET_URL)
        return self._api

    def _factor2(self) -> str:
        if self.current_otp: return self.current_otp
        if self.totp_secret:
            import pyotp
            return pyotp.TOTP(self.totp_secret).now()
        raise ShoonyaAuthenticationError("shoonya_second_factor_unavailable")

    def login(self) -> dict[str, Any]:
        with self._login_lock:
            if self._authenticated: return {"authenticated": True, "reused": True}
            missing = self.missing_environment_variables()
            if missing: raise ShoonyaAuthenticationError("missing_shoonya_credentials")
            api = self._build_api()
            user = self.credentials["SHOONYA_USER_ID"]
            if self.session_token:
                api.set_session(user, self.credentials["SHOONYA_PASSWORD"], self.session_token)
                self._authenticated = True
                return {"authenticated": True, "reused": True}
            response = api.login(userid=user, password=self.credentials["SHOONYA_PASSWORD"], twoFA=self._factor2(), vendor_code=self.credentials["SHOONYA_VENDOR_CODE"], api_secret=self.credentials["SHOONYA_API_KEY"], imei=self.credentials["SHOONYA_IMEI"])
            if not isinstance(response, dict) or str(response.get("stat", "")).lower() != "ok" or not response.get("susertoken"):
                raise ShoonyaAuthenticationError("shoonya_login_rejected")
            self._authenticated = True
            return {"authenticated": True, "reused": False, "exchanges": tuple(response.get("exarr") or ())}

    def _limit(self, seconds: float = .20) -> None:
        with self._request_lock:
            remaining = seconds - (time.monotonic() - self._last_request)
            if remaining > 0: time.sleep(remaining)
            self._last_request = time.monotonic()

    def _call(self, method: str, **kwargs) -> Any:
        self.login();self._limit();function=getattr(self._build_api(), method, None)
        if method not in {"searchscrip", "get_security_info", "get_quotes", "get_time_price_series"} or not callable(function):
            raise ShoonyaUnavailable("unsupported_read_only_operation")
        response=function(**kwargs)
        if response is None or (isinstance(response,dict) and str(response.get("stat","")).lower()=="not_ok"):
            raise ShoonyaUnavailable("shoonya_market_data_request_failed")
        return response

    def search_symbols(self, exchange: str, text: str) -> Any: return self._call("searchscrip", exchange=exchange, searchtext=text)
    def security_info(self, exchange: str, token: str) -> Any: return self._call("get_security_info", exchange=exchange, token=str(token))
    def quote(self, exchange: str, token: str) -> dict[str, Any]: return self._call("get_quotes", exchange=exchange, token=str(token))
    def candles(self, exchange: str, token: str, start: float, end: float, interval: int) -> Any: return self._call("get_time_price_series", exchange=exchange, token=str(token), starttime=start, endtime=end, interval=interval)

    def _tick(self, tick: Any) -> None:
        if not isinstance(tick, dict): return
        exchange, token = str(tick.get("e") or ""), str(tick.get("tk") or "")
        if not exchange or not token: return
        key=f"{exchange}|{token}";now=datetime.now(timezone.utc)
        with self._quote_lock:
            previous=self._quotes.get(key,{})
            incoming_stamp=None
            raw_stamp=tick.get("ft") or tick.get("lut")
            if raw_stamp is not None:
                try:
                    number=float(raw_stamp);number=number/1000 if number>10_000_000_000 else number;incoming_stamp=datetime.fromtimestamp(number,timezone.utc)
                except (TypeError,ValueError,OSError,OverflowError):incoming_stamp=None
            previous_stamp=previous.get("exchange_timestamp")
            if incoming_stamp is not None and isinstance(previous_stamp,datetime) and incoming_stamp<previous_stamp:return
            merged={**previous,**{field:value for field,value in tick.items() if value not in (None,"")}}
            merged["receive_timestamp"]=now
            if incoming_stamp is not None:merged["exchange_timestamp"]=incoming_stamp
            if "lp" in tick and str(tick.get("lp")) != str(previous.get("lp")):
                merged["last_price_change_monotonic"]=time.monotonic()
            else: merged.setdefault("last_price_change_monotonic",time.monotonic())
            self._quotes[key]=merged

    def _opened(self) -> None:
        self._ws_open.set()
        subscriptions=sorted(self._subscriptions)
        if subscriptions:
            try:self._build_api().subscribe(subscriptions)
            except Exception:LOGGER.warning("Shoonya resubscription failed safely")

    def _closed(self, *args) -> None:
        self._ws_open.clear()
        if not self._ws_stop.is_set(): self._schedule_reconnect()

    def _error(self, *args) -> None:
        self._ws_open.clear()
        if not self._ws_stop.is_set(): self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        with self._ws_lock:
            if self._reconnect_thread and self._reconnect_thread.is_alive(): return
            self._reconnect_thread=threading.Thread(target=self._reconnect_loop,name="shoonya-market-reconnect",daemon=True);self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        for attempt in range(1,6):
            if self._ws_stop.wait(min(60,2**attempt)): return
            try:
                with self._ws_lock:self._ws_started=False
                if self.start_websocket(wait_seconds=5): return
            except Exception:continue
        LOGGER.warning("Shoonya market WebSocket recovery exhausted")

    def start_websocket(self, wait_seconds: float = 5.0) -> bool:
        self.login()
        with self._ws_lock:
            if self._ws_started and self._ws_open.is_set(): return True
            if self._ws_started: return self._ws_open.wait(max(0,wait_seconds))
            self._ws_stop.clear();self._ws_started=True
            try:self._build_api().start_websocket(subscribe_callback=self._tick,order_update_callback=None,socket_open_callback=self._opened,socket_close_callback=self._closed,socket_error_callback=self._error)
            except Exception:
                self._ws_started=False;raise
        return self._ws_open.wait(max(0,wait_seconds))

    def subscribe(self, instruments: Iterable[str]) -> int:
        values={str(item) for item in instruments if "|" in str(item)};new=values-self._subscriptions;self._subscriptions.update(values)
        if new and self._ws_open.is_set(): self._build_api().subscribe(sorted(new))
        return len(new)

    def cached_quote(self, instrument_key: str, max_age_seconds: int = 20) -> dict[str, Any] | None:
        with self._quote_lock:value=dict(self._quotes.get(instrument_key, {}))
        if not value or "receive_timestamp" not in value:return None
        age=(datetime.now(timezone.utc)-value["receive_timestamp"]).total_seconds();exchange_stamp=value.get("exchange_timestamp")
        exchange_age=(datetime.now(timezone.utc)-exchange_stamp).total_seconds() if isinstance(exchange_stamp,datetime) else float("inf")
        frozen=time.monotonic()-float(value.get("last_price_change_monotonic",time.monotonic()))>max(60,max_age_seconds*3)
        return None if age>max_age_seconds or exchange_age>max_age_seconds or frozen else value

    def health(self) -> dict[str, Any]:
        return {"configured":self.configured,"authenticated":self._authenticated,"websocket_started":self._ws_started,"websocket_open":self._ws_open.is_set(),"subscriptions":len(self._subscriptions),"cached_quotes":len(self._quotes),"missing_environment_variables":self.missing_environment_variables()}

    def close_websocket(self) -> None:
        self._ws_stop.set();self._ws_open.clear()
        with self._ws_lock:
            if self._api is not None and self._ws_started:
                try:self._api.close_websocket()
                except Exception:pass
            self._ws_started=False

    def logout(self) -> bool:
        self.close_websocket()
        with self._login_lock:
            if self._api is not None and self._authenticated:
                try:self._api.logout()
                except Exception:pass
            self._authenticated=False
        return True


def get_shoonya_client_status() -> dict[str, Any]: return ShoonyaClient().health()
