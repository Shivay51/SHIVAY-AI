from pathlib import Path

path = Path(__file__).with_name('morning_prediction.py')
text = path.read_text(encoding='utf-8')
start = text.index('def _previous_session(')
end = text.index('\n\ndef _call_optional', start)
replacement = '''def _previous_session(symbol: str, today: date) -> dict[str, Any]:
    """Build the last completed NSE session from verified provider candles."""
    try:
        from market_data_provider import get_historical_candles
        candles = get_historical_candles(symbol, interval="15m", period="5d")
    except Exception:
        LOGGER.warning("Previous-session history is unavailable for %s", symbol)
        return {"available": False}
    sessions: dict[date, list[tuple[datetime, float, float, float, float]]] = {}
    for candle in candles or []:
        stamp = candle.get("timestamp") or candle.get("datetime")
        if not isinstance(stamp, datetime):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        else:
            stamp = stamp.astimezone(IST)
        session_day = stamp.date()
        if session_day >= today:
            continue
        opened = _number(candle.get("open"))
        high = _number(candle.get("high"))
        low = _number(candle.get("low"))
        close = _number(candle.get("close"))
        if min(opened, high, low, close) <= 0:
            continue
        sessions.setdefault(session_day, []).append((stamp, opened, high, low, close))
    if not sessions:
        return {"available": False}
    session_day = max(sessions)
    rows = sorted(sessions[session_day], key=lambda row: row[0])
    session_open = rows[0][1]
    previous_close = rows[-1][4]
    previous_high = max(row[2] for row in rows)
    previous_low = min(row[3] for row in rows)
    change_percent = ((previous_close - session_open) / session_open) * 100.0
    trend = "BULLISH" if change_percent > 0.20 else "BEARISH" if change_percent < -0.20 else "SIDEWAYS"
    return {"available": True, "date": session_day.isoformat(), "open": round(session_open, 2), "close": round(previous_close, 2), "high": round(previous_high, 2), "low": round(previous_low, 2), "change_percent": round(change_percent, 2), "trend": trend, "range_percent": round(((previous_high - previous_low) / previous_close) * 100.0, 2)}
'''
text = text[:start] + replacement + text[end:]
text = text.replace('_previous_session("NIFTY", now.date())', '_previous_session("NIFTY FUT", now.date())')
text = text.replace('_previous_session("BANKNIFTY", now.date())', '_previous_session("BANKNIFTY FUT", now.date())')
path.write_text(text, encoding='utf-8')
print('DONE: previous-session history now uses verified provider candles.')
