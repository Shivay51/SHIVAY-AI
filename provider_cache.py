"""Bounded thread-safe provider cache."""
from __future__ import annotations
import threading,time
from copy import deepcopy
from typing import Any
class ProviderCache:
    def __init__(self, ttl_seconds:int=60,max_entries:int=256): self.ttl=max(1,int(ttl_seconds)); self.max_entries=max(8,int(max_entries)); self._items={}; self._lock=threading.RLock();self._hits=0;self._misses=0;self._stale_rejections=0
    def get(self,key:str)->Any:
        with self._lock:
            item=self._items.get(key)
            if not item or time.monotonic()-item[0]>item[1]: self._items.pop(key,None);self._misses+=1; return None
            self._hits+=1;return deepcopy(item[2])
    def get_validated(self,key:str,validator)->Any:
        value=self.get(key)
        if value is None:return None
        try:valid=bool(validator(value))
        except Exception:valid=False
        if valid:return value
        with self._lock:self._items.pop(key,None);self._stale_rejections+=1
        return None
    def set(self,key:str,value:Any,ttl_seconds:int|None=None)->None:
        with self._lock:
            if len(self._items)>=self.max_entries: self._items.pop(min(self._items,key=lambda k:self._items[k][0]),None)
            self._items[key]=(time.monotonic(),max(1,int(ttl_seconds or self.ttl)),deepcopy(value))
    def invalidate(self,prefix:str|None=None)->int:
        with self._lock:
            keys=[k for k in self._items if prefix is None or k.startswith(prefix)]
            for k in keys:self._items.pop(k,None)
            return len(keys)
    def cleanup(self)->int:
        with self._lock:
            expired=[k for k,v in self._items.items() if time.monotonic()-v[0]>v[1]]
            for k in expired:self._items.pop(k,None)
            return len(expired)
    def status(self)->dict[str,Any]:
        self.cleanup(); return {"entries":len(self._items),"ttl_seconds":self.ttl,"max_entries":self.max_entries,"hits":self._hits,"misses":self._misses,"stale_rejections":self._stale_rejections}
_default_cache=ProviderCache()
def get_cached(key:str)->Any:return _default_cache.get(key)
def set_cached(key:str,value:Any,ttl_seconds:int|None=None)->None:_default_cache.set(key,value,ttl_seconds)
def clear_cache(prefix:str|None=None)->int:return _default_cache.invalidate(prefix)
def get_cache_status()->dict[str,Any]:return _default_cache.status()
