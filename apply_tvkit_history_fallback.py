from pathlib import Path

path = Path(__file__).with_name('morning_prediction.py')
text = path.read_text(encoding='utf-8')
old = '''        from market_data_provider import get_historical_candles
        candles = get_historical_candles(symbol, interval="15m", period="5d")
    except Exception:
        LOGGER.warning("Previous-session history is unavailable for %s", symbol)
        return {"available": False}
    sessions: dict[date, list[tuple[datetime, float, float, float, float]]] = {}
'''
new = '''        from market_data_provider import get_historical_candles
        candles = get_historical_candles(symbol, interval="15m", period="5d")
    except Exception:
        candles = []
    if not candles:
        try:
            from tvkit_provider import TVKitProvider
            provider = TVKitProvider()
            try:
                index_symbol = "NIFTY" if symbol == "NIFTY FUT" else "BANKNIFTY" if symbol == "BANKNIFTY FUT" else symbol
                candles = provider.get_historical_candles(index_symbol, "15m", 5)
            finally:
                provider.close()
        except Exception:
            LOGGER.warning("Previous-session history is unavailable for %s", symbol)
            candles = []
    sessions: dict[date, list[tuple[datetime, float, float, float, float]]] = {}
'''
if old not in text:
    raise SystemExit('Expected provider-history block was not found; no changes made.')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('DONE: previous-session history now falls back to TVKit candles.')
