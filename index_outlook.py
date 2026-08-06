"""Fresh, probability-based next-session outlooks with strict validation."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from live_data_status import classify_market_state
from moneycontrol_outlook import CORE_NAMES, MoneycontrolOutlookClient
from nse_temporary_provider import NSETemporaryProvider
from tvkit_provider import TVKitProvider

IST = ZoneInfo("Asia/Kolkata")
_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0


def _number(value: Any) -> float | None:
    try:
        result = float(str(value).replace("%", "").replace(",", "").strip())
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def rank_confluence_levels(close: float, high: float, low: float,
                            call_strikes: list[float] | None = None,
                            put_strikes: list[float] | None = None) -> dict[str, float | None]:
    """Cluster previous structure, CPR, pivots, round numbers and option strikes."""
    if min(close, high, low) <= 0 or high < low:
        return {key: None for key in ("s1", "s2", "r1", "r2")}
    pivot = (high + low + close) / 3.0
    bottom = (high + low) / 2.0
    top = 2 * pivot - bottom
    step = 100.0 if close >= 30_000 else 50.0
    candidates: list[tuple[float, float]] = [
        (low, 3.0), (high, 3.0), (pivot, 2.4), (bottom, 2.0), (top, 2.0),
        (2 * pivot - high, 2.5), (pivot - (high - low), 2.0),
        (2 * pivot - low, 2.5), (pivot + (high - low), 2.0),
        (round(close / step) * step, 1.5),
    ]
    candidates.extend((float(value), 4.0) for value in (call_strikes or []) if _number(value))
    candidates.extend((float(value), 4.0) for value in (put_strikes or []) if _number(value))
    radius = max(step * 0.3, close * 0.0007)
    clusters: list[dict[str, float]] = []
    for value, weight in sorted(candidates):
        match = next((item for item in clusters if abs(item["value"] - value) <= radius), None)
        if match:
            total = match["weight"] + weight
            match["value"] = (match["value"] * match["weight"] + value * weight) / total
            match["weight"] = total
        else:
            clusters.append({"value": value, "weight": weight})
    supports = sorted((x for x in clusters if x["value"] < close), key=lambda x: (-x["weight"], close - x["value"]))[:2]
    resistances = sorted((x for x in clusters if x["value"] > close), key=lambda x: (-x["weight"], x["value"] - close))[:2]
    supports.sort(key=lambda x: x["value"], reverse=True)
    resistances.sort(key=lambda x: x["value"])
    return {"s1": round(supports[0]["value"], 2) if supports else None,
            "s2": round(supports[1]["value"], 2) if len(supports) > 1 else None,
            "r1": round(resistances[0]["value"], 2) if resistances else None,
            "r2": round(resistances[1]["value"], 2) if len(resistances) > 1 else None}


def recalculate_post_open_levels(open_price: float, first_high: float, first_low: float,
                                 vwap: float | None = None) -> dict[str, float | None]:
    close_proxy = vwap if vwap is not None and first_low <= vwap <= first_high else open_price
    return rank_confluence_levels(float(close_proxy), float(first_high), float(first_low))


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
    return rank_confluence_levels(float(rows[-1]["close"]), max(float(x["high"]) for x in rows),
                                   min(float(x["low"]) for x in rows))


def decide_outlook(inputs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Derive direction from validated percentage changes, never unrelated prices."""
    gift = inputs.get("GIFT NIFTY", {})
    global_names = ("DOW", "S&P 500", "NASDAQ", "NIKKEI", "HANG SENG", "KOSPI")
    globals_ = [inputs.get(name, {}) for name in global_names]
    usable = [item for item in globals_ if item.get("available") and item.get("direction_usable", True)]
    negative = sum(float(item.get("change_percent") or 0) < -0.05 for item in usable)
    positive = sum(float(item.get("change_percent") or 0) > 0.05 for item in usable)
    gift_change = float(gift.get("change_percent") or 0) if gift.get("available") and gift.get("direction_usable", True) else None
    majority_negative = bool(usable) and negative > len(usable) / 2
    majority_positive = bool(usable) and positive > len(usable) / 2
    if gift_change is not None and gift_change <= -0.20 and majority_negative:
        bias, opening, decision, bull, bear = "MILD BEARISH", "NEGATIVE / GAP-DOWN RISK", "MILD BEARISH", 32, 68
    elif gift_change is not None and gift_change >= 0.20 and majority_positive:
        bias, opening, decision, bull, bear = "MILD BULLISH", "POSITIVE / GAP-UP POTENTIAL", "MILD BULLISH", 68, 32
    elif gift_change is None or len(usable) < 3:
        bias, opening, decision, bull, bear = "UNCERTAIN", "UNCERTAIN", "WAIT", 50, 50
    elif gift_change < -0.05 and majority_negative:
        bias, opening, decision, bull, bear = "MILD BEARISH", "NEGATIVE", "MILD BEARISH", 40, 60
    elif gift_change > 0.05 and majority_positive:
        bias, opening, decision, bull, bear = "MILD BULLISH", "POSITIVE", "MILD BULLISH", 60, 40
    else:
        bias, opening, decision, bull, bear = "NEUTRAL / UNCERTAIN", "FLAT / UNCERTAIN", "WAIT", 50, 50
    bank = inputs.get("BANKNIFTY FUTURES", {})
    bank_change = _number(bank.get("change_percent")) if bank.get("validated") else None
    bank_bias = "MILD BULLISH" if bank_change is not None and bank_change > .20 else "MILD BEARISH" if bank_change is not None and bank_change < -.20 else "NEUTRAL"
    return {"nifty_bias": bias, "expected_open": opening, "decision": decision,
            "bull_probability": bull, "bear_probability": bear, "banknifty_bias": bank_bias}


def sanity_check_outlook(report: Mapping[str, Any]) -> tuple[bool, list[str]]:
    inputs = report.get("inputs") if isinstance(report.get("inputs"), Mapping) else {}
    errors: list[str] = []
    for name in CORE_NAMES:
        item = inputs.get(name, {})
        if not item.get("validated"):
            errors.append(f"{name} is not validated")
        change, percent = _number(item.get("change")), _number(item.get("change_percent"))
        if change is not None and percent is not None and abs(change) > .05 and abs(percent) > .001 and change * percent < 0:
            errors.append(f"{name} sign mismatch")
    for group in ("nifty_levels", "banknifty_levels"):
        values = report.get(group) or {}
        s1, s2, r1, r2 = (_number(values.get(key)) for key in ("s1", "s2", "r1", "r2"))
        if None in (s1, s2, r1, r2) or not (s2 < s1 < r1 < r2):
            errors.append(f"{group} are not logical")
    bull, bear = _number(report.get("bull_probability")), _number(report.get("bear_probability"))
    if bull is None or bear is None or abs(bull + bear - 100) > .01:
        errors.append("probabilities are invalid")
    gift_percent = _number(inputs.get("GIFT NIFTY", {}).get("change_percent"))
    decision = str(report.get("decision") or "WAIT")
    if gift_percent is not None and gift_percent < 0 and "BULL" in decision:
        errors.append("bullish decision conflicts with negative GIFT")
    if gift_percent is not None and gift_percent > 0 and "BEAR" in decision:
        errors.append("bearish decision conflicts with positive GIFT")
    return not errors, errors


def generate_market_outlook(force_refresh: bool = True) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    with _LOCK:
        if not force_refresh and _CACHE is not None and time.monotonic() - _CACHE_AT < 30:
            return dict(_CACHE)
    now = datetime.now(timezone.utc)
    client = MoneycontrolOutlookClient()
    try:
        context = client.get_snapshot(force_refresh=True)
        inputs: dict[str, dict[str, Any]] = dict(context.get("inputs") or {})
    except Exception:
        context = {"valid": False, "validation_errors": ["context retrieval failure"]}
        inputs = {name: {"available": False, "validated": False, "direction_usable": False} for name in CORE_NAMES}
    finally:
        client.close()
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
                                    "validated": price is not None and price > 0 and previous is not None and previous > 0,
                                    "ltp": price, "change": change, "change_percent": percent,
                                    "timestamp": stamp, "retrieved_at": now, "freshness": state.get("status"),
                                    "direction_usable": state.get("status") == "CLOSED" or (state.get("data_age_seconds") or 10**9) <= 1200}
        chain = nse.get_option_chain_context("NIFTY FUT") or {}
        fno = {"volume": quote.get("volume"), "open_interest": quote.get("open_interest"),
               "oi_change": quote.get("oi_change"), "oi_change_percent": quote.get("oi_change_percent"),
               "put_call_ratio": chain.get("put_call_ratio"), "timestamp": stamp}
    except Exception:
        inputs["NIFTY FUTURES"] = {"available": False, "validated": False, "direction_usable": False}
    finally:
        nse.close()
    provider = TVKitProvider()
    try:
        nifty_levels = _levels("NIFTY", provider)
        bank_levels = _levels("BANKNIFTY", provider)
    finally:
        provider.close()
    result = {"generated_at": now, "inputs": inputs, "fno": fno,
              "context_valid": bool(context.get("valid")),
              "validation_errors": list(context.get("validation_errors") or []),
              "nifty_levels": nifty_levels, "banknifty_levels": bank_levels, **decide_outlook(inputs)}
    valid, errors = sanity_check_outlook(result)
    result["sanity_valid"] = valid
    result["validation_errors"] = list(dict.fromkeys(result["validation_errors"] + errors))
    with _LOCK:
        _CACHE, _CACHE_AT = result, time.monotonic()
    return dict(result)


def _line(name: str, item: Mapping[str, Any]) -> list[str]:
    if not item.get("available"):
        return [name, "UNAVAILABLE"]
    change, percent = float(item["change"]), float(item["change_percent"])
    icon = "🟢" if change > 0 else "🔴" if change < 0 else "🟡"
    return [name, f"{float(item['ltp']):,.2f} {icon} {change:+,.2f} ({percent:+.2f}%)"]


def _fmt(value: Any) -> str:
    number = _number(value)
    return f"{number:,.2f}" if number is not None else "UNAVAILABLE"


def format_market_outlook(report: Mapping[str, Any]) -> str:
    if not report.get("sanity_valid", report.get("context_valid", True)):
        return "⚠️ DATA VERIFY"
    inputs = report.get("inputs", {}) if isinstance(report.get("inputs"), Mapping) else {}
    nlevels = report.get("nifty_levels", {}) if isinstance(report.get("nifty_levels"), Mapping) else {}
    blevels = report.get("banknifty_levels", {}) if isinstance(report.get("banknifty_levels"), Mapping) else {}
    now = report.get("generated_at") if isinstance(report.get("generated_at"), datetime) else datetime.now(timezone.utc)
    stage = str(report.get("stage") or "PRE-MARKET").replace(" IST", "").replace(" UPDATE", "")
    lines = ["🔱 SHIVAY AI PRO", "━━━━━━━━━━━━━━━━", f"📊 {stage}",
             f"📅 {now.astimezone(IST).strftime('%d %b').upper()} | ⏱ {now.astimezone(IST).strftime('%H:%M')}", "", "🌐 GLOBAL", ""]
    for name in CORE_NAMES:
        lines.extend(_line(name, inputs.get(name, {})))
        lines.append("")
    nifty_icon = "🔴" if "BEAR" in str(report.get("nifty_bias")) else "🟢" if "BULL" in str(report.get("nifty_bias")) else "🟡"
    bank_icon = "🔴" if "BEAR" in str(report.get("banknifty_bias")) else "🟢" if "BULL" in str(report.get("banknifty_bias")) else "🟡"
    lines.extend(["━━━━━━━━━━━━━━━━", "🇮🇳 NIFTY", "", f"{nifty_icon} BIAS: {report.get('nifty_bias', 'UNCERTAIN')}", "",
                  f"S1 │ {_fmt(nlevels.get('s1'))}", f"S2 │ {_fmt(nlevels.get('s2'))}", f"R1 │ {_fmt(nlevels.get('r1'))}", f"R2 │ {_fmt(nlevels.get('r2'))}", "",
                  "━━━━━━━━━━━━━━━━", "🏦 BANKNIFTY", "", f"{bank_icon} BIAS: {report.get('banknifty_bias', 'NEUTRAL')}", "",
                  f"S1 │ {_fmt(blevels.get('s1'))}", f"S2 │ {_fmt(blevels.get('s2'))}", f"R1 │ {_fmt(blevels.get('r1'))}", f"R2 │ {_fmt(blevels.get('r2'))}", "",
                  "━━━━━━━━━━━━━━━━", f"📊 BULL {report.get('bull_probability', 50)}% │ BEAR {report.get('bear_probability', 50)}%", "",
                  f"🎯 OPEN: {report.get('expected_open', 'UNCERTAIN')}", f"🧭 DECISION: {report.get('decision', 'WAIT')}"])
    return "\n".join(lines)


def build_index_outlook(name: str, session: Mapping[str, Any], probabilities: Mapping[str, Any],
                        regime: str = "UNKNOWN", strength: float = 0.0, risk: str = "HIGH",
                        preferred: str = "NO TRADE") -> dict[str, Any]:
    """Compatibility helper retained for morning and overnight modules."""
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


__all__ = ["build_index_outlook", "decide_outlook", "format_market_outlook", "generate_market_outlook",
           "rank_confluence_levels", "recalculate_post_open_levels", "sanity_check_outlook"]
