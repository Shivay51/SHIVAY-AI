from pathlib import Path

path = Path(__file__).with_name('data.py')
text = path.read_text(encoding='utf-8')
old = '''def refresh_market()->None:
 global _cache,_last_update
 with _lock:
  if _cache and time.time()-_last_update<CACHE_SECONDS:return
  values=get_provider_manager().get_verified_many(list(SYMBOLS),period="5d",interval="5m");_cache=values;_last_update=time.time()
def get_market_data(symbol:str)->dict[str,Any]|None:
 refresh_market();value=_cache.get(symbol);return value.copy() if value else None
def get_batch_market_data(symbols:list[str])->dict[str,dict[str,Any]]:
 refresh_market();return {symbol:_cache[symbol].copy() for symbol in symbols if symbol in _cache}
'''
new = '''def refresh_market(symbols: list[str] | None = None)->None:
 global _cache,_last_update
 requested = list(dict.fromkeys(symbols or list(SYMBOLS)))
 with _lock:
  if _cache and time.time()-_last_update<CACHE_SECONDS and all(symbol in _cache for symbol in requested): return
  values=get_provider_manager().get_verified_many(requested,period="5d",interval="5m")
  _cache.update(values);_last_update=time.time()
def get_market_data(symbol:str)->dict[str,Any]|None:
 refresh_market([symbol]);value=_cache.get(symbol);return value.copy() if value else None
def get_batch_market_data(symbols:list[str])->dict[str,dict[str,Any]]:
 refresh_market(symbols);return {symbol:_cache[symbol].copy() for symbol in symbols if symbol in _cache}
'''
if old not in text:
 raise SystemExit('Expected data refresh block was not found; no changes made.')
text = text.replace(old, new, 1)
old_snapshot = '''def get_cached_snapshot()->dict[str,dict[str,Any]]:
 refresh_market()
 with _lock:return {symbol:value.copy() for symbol,value in _cache.items()}
'''
new_snapshot = '''def get_cached_snapshot()->dict[str,dict[str,Any]]:
 refresh_market(["NIFTY FUT", "BANKNIFTY FUT"])
 with _lock:return {symbol:value.copy() for symbol,value in _cache.items()}
'''
if old_snapshot not in text:
 raise SystemExit('Expected snapshot block was not found; no changes made.')
path.write_text(text.replace(old_snapshot, new_snapshot, 1), encoding='utf-8')
print('DONE: market checks now refresh only requested symbols.')
