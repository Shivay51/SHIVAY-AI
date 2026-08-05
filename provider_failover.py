"""Capped provider failover without retry storms."""
from __future__ import annotations
import time
from typing import Any,Callable,Iterable
from provider_health import circuit_open,record_failure,record_success,rank_provider_names
class ProviderUnavailable(RuntimeError): pass
def execute_with_failover(providers:Iterable[Any],operation:Callable[[Any],Any],attempts_per_provider:int=1)->tuple[Any,str]:
    errors=[];items=list(providers);by_name={str(getattr(p,"name",type(p).__name__)):p for p in items};base={name:max(0,1000-index*200) for index,name in enumerate(by_name)}
    for name in rank_provider_names(list(by_name),base):
        provider=by_name[name]
        if circuit_open(name) or not bool(getattr(provider,"available",True)):continue
        for attempt in range(max(1,min(int(attempts_per_provider),2))):
            started=time.monotonic()
            try:
                result=operation(provider)
                if result is None:raise ProviderUnavailable("empty_response")
                record_success(name,(time.monotonic()-started)*1000);return result,name
            except Exception as error:
                category="permanent" if isinstance(error,(ValueError,NotImplementedError)) else "temporary";record_failure(name,category);errors.append(f"{name}:{type(error).__name__}")
                if category=="permanent":break
                if attempt+1<attempts_per_provider:time.sleep(min(1.0,0.2*(2**attempt)))
    raise ProviderUnavailable("No provider returned valid data: "+", ".join(errors[-4:]))
