"""Authentication, replay defense and bounded rate limiting for TV alerts."""
from __future__ import annotations
import hashlib,hmac,json,os,re,tempfile,threading,time
from collections import defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from tradingview_payload import TradingViewPayload

class SecurityError(PermissionError):pass
class TradingViewSecurity:
    def __init__(self,replay_path:str|os.PathLike[str]|None=None):
        self.secret=os.getenv("TRADINGVIEW_WEBHOOK_SECRET","").strip();self.max_delay=max(15,int(os.getenv("TRADINGVIEW_MAX_DELAY_SECONDS","120") or 120));self._seen={};self._rates=defaultdict(deque);self._lock=threading.RLock();self.replay_path=Path(replay_path).resolve() if replay_path else None;self._load_seen()
    def _load_seen(self)->None:
        if not self.replay_path or not self.replay_path.is_file():return
        try:
            data=json.loads(self.replay_path.read_text(encoding="utf-8"));now=time.time();self._seen={str(key):time.monotonic()-(now-float(value)) for key,value in data.items() if now-float(value)<86400}
        except Exception:self._seen={}
    def _persist_seen(self)->None:
        if not self.replay_path:return
        path=self.replay_path;path.parent.mkdir(parents=True,exist_ok=True);moment=time.monotonic();now=time.time();data={key:now-(moment-value) for key,value in self._seen.items()}
        handle,temp_name=tempfile.mkstemp(prefix=f".{path.name}.",suffix=".tmp",dir=str(path.parent))
        try:
            with os.fdopen(handle,"w",encoding="utf-8") as stream:json.dump(data,stream,separators=(",",":"));stream.flush();os.fsync(stream.fileno())
            os.replace(temp_name,path)
        finally:
            if os.path.exists(temp_name):os.unlink(temp_name)
    @property
    def configured(self)->bool:return bool(re.fullmatch(r"[A-Za-z0-9_-]{24,128}",self.secret))
    def authenticate(self,supplied:Any)->None:
        if not self.configured or not isinstance(supplied,str) or not hmac.compare_digest(self.secret.encode(),supplied.encode()):raise SecurityError("authentication_failed")
    def validate(self,payload:TradingViewPayload,allowed_symbols:set[str],allowed_timeframes:set[int],client_key:str="tradingview")->None:
        now=datetime.now(timezone.utc);delay=(now-payload.generated_at.astimezone(timezone.utc)).total_seconds();bar_delay=(now-payload.bar_timestamp.astimezone(timezone.utc)).total_seconds()
        if payload.symbol not in allowed_symbols:raise SecurityError("symbol_not_allowed")
        if payload.timeframe not in allowed_timeframes:raise SecurityError("timeframe_not_allowed")
        if delay< -30 or delay>self.max_delay:raise SecurityError("stale_or_future_payload")
        if bar_delay< -30 or bar_delay>max(self.max_delay,payload.timeframe*120):raise SecurityError("stale_or_future_bar")
        digest=hashlib.sha256(f"event|{payload.event_id}".encode()).hexdigest()
        candle_digest=hashlib.sha256(f"candle|{payload.symbol}|{payload.contract_text}|{payload.category}|{payload.timeframe}|{payload.bar_timestamp.isoformat()}".encode()).hexdigest();moment=time.monotonic()
        with self._lock:
            self._seen={key:value for key,value in self._seen.items() if moment-value<86400}
            if digest in self._seen or candle_digest in self._seen:raise SecurityError("duplicate_or_replayed_event")
            queue=self._rates[client_key]
            while queue and moment-queue[0]>60:queue.popleft()
            if len(queue)>=120:raise SecurityError("rate_limited")
            queue.append(moment);self._seen[digest]=moment;self._seen[candle_digest]=moment;self._persist_seen()
