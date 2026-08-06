"""Fresh, probability-based next-session outlooks with no provider disclosure."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import requests

from live_data_status import classify_market_state
from nse_temporary_provider import NSETemporaryProvider
from tvkit_provider import TVKitProvider

IST = ZoneInfo("Asia/Kolkata")
SCAN_URL = "https://scanner.tradingview.com/global/scan"
STATIC_SYMBOLS = {
    "NIFTY": "NSE:NIFTY", "BANKNIFTY": "NSE:BANKNIFTY",
    "DOW": "DJ:DJI", "S&P 500": "SP:SPX", "NASDAQ": "NASDAQ:IXIC",
    "NIKKEI": "TVC:NI225", "HANG SENG": "TVC:HSI",
    "USDINR": "FX_IDC:USDINR", "DXY": "TVC:DXY",
}
COLUMNS = ["name", "description", "close", "change_abs", "change", "update_time", "update_mode", "exchange"]
_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _snapshot_state(name: str, row: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    price, change, percent = (_number(row.get(key)) for key in ("close", "change_abs", "change"))
    try:
        stamp = datetime.fromtimestamp(float(row["update_time"]), timezone.utc)
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        stamp = None
    status = classify_market_state(name, {"price": price, "market_timestamp": stamp}, now=now)
    mode = str(row.get("update_mode") or "").lower()
    if status["status"] == "LIVE" and "delayed" in mode:
        status["status"] = "DELAYED"
        status["feed_fresh"] = False
    available = price is not None and price > 0 and change is not None and percent is not None and stamp is not None
    age = status["data_age_seconds"]
    direction_usable = bool(available and (status["status"] == "CLOSED" or (age is not None and age <= 1200)))
    return {
        "available": available, "ltp": price, "change": change, "change_percent": percent,
        "timestamp": stamp, "retrieved_at": now, "age_seconds": status["data_age_seconds"],
        "freshness": status["status"] if available else "UNAVAILABLE", "direction_usable": direction_usable,
    }


def _levels(symbol: str, provider: TVKitProvider) -> dict[str, float | None]:
    bars = provider.get_historical_candles(symbol, "15m", 5)
    sessions: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for bar in bars:
        stamp = bar.get("timestamp")
        if isinstance(stamp, datetime):
            sessions[stamp.astimezone(IST).date()].append(bar)
    if not sessions:
        return {key: None for key in ("s1", "s2", "r1", "r2")}
    rows = sessions[max(sessions)]
    high = max(float(row["high"]) for row in rows)
    low = min(float(row["low"]) for row in rows)
    close = float(rows[-1]["close"])
    pivot = (high + low + close) / 3.0
    return {"s1": round(2 * pivot - high, 2), "s2": round(pivot - (high - low), 2),
            "r1": round(2 * pivot - low, 2), "r2": round(pivot + (high - low), 2)}


def decide_outlook(inputs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Derive bias from fresh change percentages, never from unrelated prices."""
    gift = inputs.get("GIFT NIFTY", {})
    globals_ = [inputs.get(name, {}) for name in ("DOW", "S&P 500", "NASDAQ", "NIKKEI", "HANG SENG")]
    usable_global = [item for item in globals_ if item.get("available") and item.get("direction_usable", True)]
    negative = sum(1 for item in usable_global if float(item.get("change_percent") or 0) < -0.05)
    positive = sum(1 for item in usable_global if float(item.get("change_percent") or 0) > 0.05)
    gift_change = float(gift.get("change_percent") or 0) if gift.get("available") and gift.get("direction_usable", True) else None
    majority_negative = bool(usable_global) and negative > len(usable_global) / 2
    majority_positive = bool(usable_global) and positive > len(usable_global) / 2
    if gift_change is not None and gift_change <= -0.20 and majority_negative:
        bias, opening, decision, bull, bear = "MILD BEARISH", "NEGATIVE / GAP-DOWN RISK", "MILD BEARISH", 32, 68
    elif gift_change is not None and gift_change >= 0.20 and majority_positive:
        bias, opening, decision, bull, bear = "MILD BULLISH", "POSITIVE / GAP-UP POTENTIAL", "MILD BULLISH", 68, 32
    elif gift_change is None or not usable_global:
        bias, opening, decision, bull, bear = "UNCERTAIN", "UNCERTAIN", "WAIT", 50, 50
    elif (gift_change < -0.05 and majority_negative):
        bias, opening, decision, bull, bear = "MILD BEARISH", "NEGATIVE BIAS", "MILD BEARISH", 40, 60
    elif (gift_change > 0.05 and majority_positive):
        bias, opening, decision, bull, bear = "MILD BULLISH", "POSITIVE BIAS", "MILD BULLISH", 60, 40
    else:
        bias, opening, decision, bull, bear = "NEUTRAL / UNCERTAIN", "FLAT / UNCERTAIN", "WAIT", 50, 50
    bank_change = _number(inputs.get("BANKNIFTY", {}).get("change_percent"))
    bank_bias = "MILD BULLISH" if bank_change is not None and bank_change > 0.20 else "MILD BEARISH" if bank_change is not None and bank_change < -0.20 else "SIDEWAYS"
    return {"nifty_bias": bias, "expected_open": opening, "decision": decision,
            "bull_probability": bull, "bear_probability": bear, "banknifty_bias": bank_bias}


def generate_market_outlook(force_refresh: bool = True) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    with _LOCK:
        if not force_refresh and _CACHE is not None and time.monotonic() - _CACHE_AT < 45:
            return dict(_CACHE)
    now = datetime.now(timezone.utc)
    provider = TVKitProvider()
    gift = provider.resolve_active_contract("GIFT NIFTY")
    gold = provider.resolve_active_contract("COMEX GOLD")
    silver = provider.resolve_active_contract("COMEX SILVER")
    symbols = dict(STATIC_SYMBOLS)
    symbols.update({"GIFT NIFTY": (gift or {}).get("trading_symbol"),
                    "COMEX GOLD": (gold or {}).get("trading_symbol"),
                    "COMEX SILVER": (silver or {}).get("trading_symbol")})
    valid = {name: ticker for name, ticker in symbols.items() if ticker}
    inputs: dict[str, dict[str, Any]] = {}
    try:
        response = requests.post(SCAN_URL, json={"symbols": {"tickers": list(valid.values()), "query": {"types": []}},
                                                 "columns": COLUMNS}, timeout=20)
        response.raise_for_status()
        by_symbol = {str(row.get("s")): dict(zip(COLUMNS, row.get("d", [])))
                     for row in response.json().get("data") or [] if len(row.get("d", [])) == len(COLUMNS)}
        for name, ticker in valid.items():
            inputs[name] = _snapshot_state(name, by_symbol.get(ticker, {}), now)
    except (requests.RequestException, TypeError, ValueError):
        inputs = {name: _snapshot_state(name, {}, now) for name in valid}
    fno: dict[str, Any] = {}
    nse = NSETemporaryProvider()
    try:
        quote = nse.get_quote("NIFTY FUT") or {}
        price, previous = _number(quote.get("price")), _number(quote.get("previous_close"))
        change = price - previous if price is not None and previous is not None else None
        percent = change / previous * 100 if change is not None and previous else None
        stamp = quote.get("market_timestamp") or quote.get("timestamp")
        state = classify_market_state("NSE F&O", quote, now=now)
        inputs["NIFTY FUTURES"] = {"available": price is not None and change is not None and isinstance(stamp, datetime),
                                    "ltp": price, "change": change, "change_percent": percent,
                                    "timestamp": stamp, "retrieved_at": now,
                                    "age_seconds": state.get("data_age_seconds"), "freshness": state.get("status"),
                                    "direction_usable": state.get("status") == "CLOSED" or (state.get("data_age_seconds") or 10**9) <= 1200,
                                    "contract": quote.get("trading_symbol"), "expiry": quote.get("expiry")}
        chain = nse.get_option_chain_context("NIFTY FUT") or {}
        fno = {"volume": quote.get("volume"), "open_interest": quote.get("open_interest"),
               "oi_change": quote.get("oi_change"), "oi_change_percent": quote.get("oi_change_percent"),
               "put_call_ratio": chain.get("put_call_ratio"), "timestamp": stamp}
    except Exception:
        inputs["NIFTY FUTURES"] = {"available": False, "freshness": "UNAVAILABLE", "direction_usable": False}
    finally:
        nse.close()
    decision = decide_outlook(inputs)
    result = {"generated_at": now, "inputs": inputs, "fno": fno,
              "nifty_levels": _levels("NIFTY", provider),
              "banknifty_levels": _levels("BANKNIFTY", provider), **decision}
    with _LOCK:
        _CACHE, _CACHE_AT = result, time.monotonic()
    provider.close()
    return dict(result)


def _line(name: str, item: Mapping[str, Any]) -> list[str]:
    if not item.get("available"):
        return [name, "UNAVAILABLE"]
    change = float(item["change"]); percent = float(item["change_percent"])
    icon = "🟢" if change > 0 else "🔴" if change < 0 else "🟡"
    return [name, f"{float(item['ltp']):,.2f} | {change:+,.2f} ({percent:+.2f}%) {icon}"]


def format_market_outlook(report: Mapping[str, Any]) -> str:
    inputs = report.get("inputs", {}) if isinstance(report.get("inputs"), Mapping) else {}
    nlevels = report.get("nifty_levels", {}) if isinstance(report.get("nifty_levels"), Mapping) else {}
    blevels = report.get("banknifty_levels", {}) if isinstance(report.get("banknifty_levels"), Mapping) else {}
    now = report.get("generated_at") if isinstance(report.get("generated_at"), datetime) else datetime.now(timezone.utc)
    lines = ["🔱 SHIVAY AI PRO", f"📊 MARKET OUTLOOK {now.astimezone(IST).strftime('%d %b %Y | %I:%M %p IST')}", ""]
    for name in ("GIFT NIFTY", "DOW", "S&P 500", "NASDAQ"):
        lines.extend(_line(name, inputs.get(name, {}))); lines.append("")
    icon = "🔴" if "BEAR" in str(report.get("nifty_bias")) else "🟢" if "BULL" in str(report.get("nifty_bias")) else "🟡"
    lines.extend(["NIFTY", f"BIAS: {icon} {report.get('nifty_bias', 'UNCERTAIN')}",
                  f"OPEN: {report.get('expected_open', 'UNCERTAIN')}", "",
                  f"S1: {_fmt(nlevels.get('s1'))}", f"S2: {_fmt(nlevels.get('s2'))}",
                  f"R1: {_fmt(nlevels.get('r1'))}", f"R2: {_fmt(nlevels.get('r2'))}", "",
                  "BANKNIFTY", f"BIAS: {report.get('banknifty_bias', 'SIDEWAYS')}",
                  f"S1: {_fmt(blevels.get('s1'))}", f"S2: {_fmt(blevels.get('s2'))}",
                  f"R1: {_fmt(blevels.get('r1'))}", f"R2: {_fmt(blevels.get('r2'))}", "",
                  f"BULL: {report.get('bull_probability', 50)}%", f"BEAR: {report.get('bear_probability', 50)}%", "",
                  "DECISION:", str(report.get("decision", "WAIT"))])
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    number = _number(value)
    return f"{number:,.2f}" if number is not None else "UNAVAILABLE"


def build_index_outlook(name: str, session: Mapping[str, Any], probabilities: Mapping[str, Any],
                        regime: str = "UNKNOWN", strength: float = 0.0, risk: str = "HIGH",
                        preferred: str = "NO TRADE") -> dict[str, Any]:
    """Compatibility helper retained for morning/overnight modules."""
    close = _number(session.get("close", session.get("price")))
    high, low = _number(session.get("high")), _number(session.get("low"))
    available = bool(session.get("available")) and close is not None and close > 0
    span = max((high or close or 0) - (low or close or 0), (close or 0) * .006)
    return {"symbol": name, "available": available, "latest_price": close if available else None,
            "tomorrow_outlook": "POSITIVE FOR TOMORROW" if preferred == "BUY" else "NEGATIVE FOR TOMORROW" if preferred == "SELL" else "SIDEWAYS FOR TOMORROW",
            "opening_range": [round((close or 0) - span * .1, 2), round((close or 0) + span * .1, 2)] if available else [],
            "high_range": [], "low_range": [], "bullish_above": high, "bearish_below": low,
            "support_levels": [low] if low else [], "resistance_levels": [high] if high else [],
            "risk_level": risk, "preferred_direction": preferred, "market_regime": regime,
            "market_strength": strength, **{f"{key}_probability": value for key, value in probabilities.items()}}


__all__ = ["build_index_outlook", "decide_outlook", "format_market_outlook", "generate_market_outlook"]
