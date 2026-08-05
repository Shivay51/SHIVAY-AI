"""Institutional-style, fail-closed market context for signal validation.

The brain consumes only the shared provider cache. It never fetches independently,
never fabricates missing breadth/global data, and exposes numeric scores internally.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Mapping

import pandas as pd

from indicators import adx, atr, ema20, ema50, ema200, vwap
from sector_strength import get_sector

LOGGER = logging.getLogger("shivay.market_brain")
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
_TTL = 300.0
_LOCK = threading.RLock()


def _series(value: Mapping[str, Any], key: str) -> list[float]:
    return pd.Series(value.get(key, []), dtype="float64").dropna().tolist()


def _verified(value: Mapping[str, Any] | None) -> bool:
    quality = value.get("data_quality") if value else None
    return bool(value and value.get("verified") and value.get("is_live") and not value.get("is_delayed") and not value.get("is_stale") and isinstance(quality, Mapping) and quality.get("valid"))


def _instrument(value: Mapping[str, Any]) -> dict[str, Any]:
    close, high, low, volume = (_series(value, key) for key in ("close", "high", "low", "volume"))
    if min(map(len, (close, high, low, volume))) < 200:
        raise ValueError("insufficient_market_brain_history")
    price = float(value.get("price") or close[-1])
    e20, e50, e200 = ema20(close), ema50(close), ema200(close)
    atr_value, adx_value = atr(high, low, close), adx(high, low, close)
    vwap_value = vwap(high, low, close, volume)
    average_volume = sum(volume[-21:-1]) / 20 if len(volume) >= 21 else 0.0
    relative_volume = volume[-1] / average_volume if average_volume > 0 else 0.0
    prior_high, prior_low = max(high[-21:-1]), min(low[-21:-1])
    bullish, bearish = e20 > e50 > e200 and price > e20, e20 < e50 < e200 and price < e20
    trend = "BULLISH" if bullish else "BEARISH" if bearish else "SIDEWAYS"
    structure = "HIGHER" if close[-1] > close[-5] and low[-1] >= min(low[-5:-1]) else "LOWER" if close[-1] < close[-5] and high[-1] <= max(high[-5:-1]) else "MIXED"
    buffer = max(atr_value * 0.10, price * 0.0005)
    breakout = price > prior_high + buffer if bullish else price < prior_low - buffer if bearish else False
    fake_breakout = bool(high[-1] > prior_high and price <= prior_high)
    fake_breakdown = bool(low[-1] < prior_low and price >= prior_low)
    extension = abs(price - e20) / atr_value if atr_value > 0 else 99.0
    opening = float(value.get("open_value") or close[-2])
    gap_percent = (opening - close[-2]) / close[-2] * 100 if close[-2] else 0.0
    gap_trap = bool(abs(gap_percent) >= 0.45 and ((gap_percent > 0 and price < opening) or (gap_percent < 0 and price > opening)))
    volatility_percent = atr_value / price * 100 if price else 0.0
    momentum = (price - close[-6]) / close[-6] * 100 if close[-6] else 0.0
    pullback = bool((bullish or bearish) and extension <= 0.85 and ((bullish and price > close[-2]) or (bearish and price < close[-2])))
    candle_range = max(high[-1] - low[-1], 0.0)
    close_location = (price - low[-1]) / candle_range if candle_range > 0 else 0.5
    smart_money = (
        "ACCUMULATION" if relative_volume >= 1.20 and close_location >= 0.65 and price > close[-2]
        else "DISTRIBUTION" if relative_volume >= 1.20 and close_location <= 0.35 and price < close[-2]
        else "NEUTRAL"
    )
    exhaustion = bool(extension > 2.0 or adx_value < 18 or fake_breakout or fake_breakdown or gap_trap)
    continuation = bool((bullish or bearish) and adx_value >= 22 and not exhaustion and (breakout or pullback or abs(momentum) >= 0.25))
    liquidity = "HIGH" if relative_volume >= 1.25 else "NORMAL" if relative_volume >= 0.70 else "LOW"
    return {"price": price, "trend": trend, "structure": structure, "adx": round(adx_value, 2), "atr_percent": round(volatility_percent, 3), "relative_volume": round(relative_volume, 2), "volume_supporting": relative_volume >= 1.0, "liquidity": liquidity, "vwap": round(vwap_value, 2), "support": round(prior_low, 2), "resistance": round(prior_high, 2), "momentum": round(momentum, 3), "pullback": pullback, "breakout": breakout, "breakdown": bool(bearish and price < prior_low - buffer), "fake_breakout": fake_breakout, "fake_breakdown": fake_breakdown, "gap_percent": round(gap_percent, 3), "gap_trap": gap_trap, "smart_money_activity": smart_money, "trend_continuation": continuation, "trend_exhaustion": exhaustion, "extension_atr": round(extension, 2)}


def _breadth(snapshot: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    advances = declines = unchanged = 0
    sectors: dict[str, list[float]] = defaultdict(list)
    for symbol, value in snapshot.items():
        if symbol in {"NIFTY FUT", "BANKNIFTY FUT"} or not _verified(value):
            continue
        close = _series(value, "close")
        if len(close) < 2 or close[-2] <= 0:
            continue
        change = (close[-1] - close[-2]) / close[-2] * 100
        advances += change > 0.02
        declines += change < -0.02
        unchanged += abs(change) <= 0.02
        sectors[get_sector(symbol)].append(change)
    total = advances + declines + unchanged
    ratio = advances / total * 100 if total else None
    sector_scores = {name: round(sum(values) / len(values), 3) for name, values in sectors.items() if values}
    return {"available": total >= 5, "advances": advances, "declines": declines, "unchanged": unchanged, "breadth_percent": round(ratio, 2) if ratio is not None else None, "sector_momentum": sector_scores}


def _regime(score: int) -> str:
    return "STRONG BULL" if score >= 80 else "BULL" if score >= 60 else "STRONG BEAR" if score <= 20 else "BEAR" if score <= 40 else "SIDEWAYS"


def _build() -> dict[str, Any]:
    from data import get_cached_snapshot
    snapshot = get_cached_snapshot()
    nifty_value, bank_value = snapshot.get("NIFTY FUT"), snapshot.get("BANKNIFTY FUT")
    vix_value = snapshot.get("INDIA VIX") or snapshot.get("VIX")
    vix = {"available": False, "value": None, "risk": "UNKNOWN"}
    if _verified(vix_value):
        value = float(vix_value.get("price") or 0)
        if value > 0:
            vix = {"available": True, "value": round(value, 2), "risk": "HIGH" if value >= 25 else "ELEVATED" if value >= 18 else "NORMAL"}
    if not _verified(nifty_value) or not _verified(bank_value):
        return {"market_score": 50, "market_strength": 0, "market_confidence": 0, "market_direction": "SIDEWAYS", "market_regime": "SIDEWAYS", "trend_quality": 0, "risk_mode": "HIGH", "volatility_mode": "UNKNOWN", "tradeable": False, "nifty": {}, "bank_nifty": {}, "breadth": {"available": False}, "vix": vix, "reason": "verified_index_futures_unavailable"}
    nifty, bank = _instrument(nifty_value), _instrument(bank_value)
    breadth = _breadth(snapshot)
    score = 50.0
    for item, weight in ((nifty, 18), (bank, 18)):
        score += weight if item["trend"] == "BULLISH" else -weight if item["trend"] == "BEARISH" else 0
        score += 5 if item["structure"] == "HIGHER" else -5 if item["structure"] == "LOWER" else 0
        if item["fake_breakout"] or item["fake_breakdown"] or item["gap_trap"] or item["trend_exhaustion"]:
            score += -5 if item["trend"] == "BULLISH" else 5 if item["trend"] == "BEARISH" else 0
    if breadth.get("available"):
        breadth_percent = float(breadth["breadth_percent"])
        score += max(-10.0, min(10.0, (breadth_percent - 50.0) / 3.0))
    score = max(0, min(100, int(round(score))))
    regime = _regime(score)
    confidence = min(100, int(abs(score - 50) * 2))
    trend_quality = int(round((min(100, nifty["adx"] * 2.5) + min(100, bank["adx"] * 2.5)) / 2))
    volatility = max(nifty["atr_percent"], bank["atr_percent"])
    volatility_mode = "HIGH" if volatility > 1.8 else "LOW" if volatility < 0.10 else "NORMAL"
    aligned = nifty["trend"] == bank["trend"] and nifty["trend"] != "SIDEWAYS"
    traps = any((nifty["fake_breakout"], bank["fake_breakout"], nifty["fake_breakdown"], bank["fake_breakdown"], nifty["gap_trap"], bank["gap_trap"]))
    vix_safe = not vix["available"] or vix["risk"] != "HIGH"
    tradeable = bool(aligned and regime != "SIDEWAYS" and confidence >= 55 and trend_quality >= 50 and volatility_mode == "NORMAL" and vix_safe and not traps)
    return {"market_score": score, "market_strength": confidence, "market_confidence": confidence, "market_direction": regime, "market_regime": regime, "trend_quality": trend_quality, "risk_mode": "MODERATE" if tradeable else "HIGH", "volatility_mode": volatility_mode, "tradeable": tradeable, "nifty": nifty, "bank_nifty": bank, "breadth": breadth, "vix": vix, "trend_continuation": nifty["trend_continuation"] and bank["trend_continuation"], "trend_exhaustion": nifty["trend_exhaustion"] or bank["trend_exhaustion"], "gap_trap": nifty["gap_trap"] or bank["gap_trap"], "fake_breakout": nifty["fake_breakout"] or bank["fake_breakout"], "fake_breakdown": nifty["fake_breakdown"] or bank["fake_breakdown"]}


def get_market_analysis(force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    with _LOCK:
        if not force and _CACHE is not None and time.time() - _CACHE_AT < _TTL:
            return dict(_CACHE)
        try:
            _CACHE = _build()
        except Exception as error:
            LOGGER.warning("Market brain recovered from %s", type(error).__name__)
            _CACHE = {"market_score": 50, "market_strength": 0, "market_confidence": 0, "market_direction": "SIDEWAYS", "market_regime": "SIDEWAYS", "trend_quality": 0, "risk_mode": "HIGH", "volatility_mode": "UNKNOWN", "tradeable": False, "nifty": {}, "bank_nifty": {}, "breadth": {"available": False}, "reason": "analysis_unavailable"}
        _CACHE_AT = time.time()
        return dict(_CACHE)


def assess_signal_context(market: Mapping[str, Any], symbol: str, side: str) -> dict[str, Any]:
    """Return explainable pre-signal checks without exposing internal scores."""
    side = str(side).upper()
    expected = "BULLISH" if side == "BUY" else "BEARISH"
    analysis = get_market_analysis()
    checks: dict[str, bool] = {
        "market_supporting": analysis.get("market_regime") in ({"BULL", "STRONG BULL"} if side == "BUY" else {"BEAR", "STRONG BEAR"}),
        "trend_not_exhausted": not bool(analysis.get("trend_exhaustion")),
        "no_market_trap": not bool(analysis.get("gap_trap") or analysis.get("fake_breakout")),
    }
    try:
        instrument = _instrument(market)
        checks.update({
            "trend_strong": instrument["trend"] == expected and instrument["adx"] >= 22,
            "structure_supporting": instrument["structure"] == ("HIGHER" if side == "BUY" else "LOWER"),
            "liquidity_supporting": instrument["liquidity"] != "LOW",
            "volume_supporting": instrument["volume_supporting"],
            "vwap_supporting": instrument["price"] > instrument["vwap"] if side == "BUY" else instrument["price"] < instrument["vwap"],
            "momentum_supporting": instrument["momentum"] > 0 if side == "BUY" else instrument["momentum"] < 0,
            "smart_money_supporting": instrument["smart_money_activity"] != ("DISTRIBUTION" if side == "BUY" else "ACCUMULATION"),
            "entry_not_late": instrument["extension_atr"] <= 2.0,
            "no_fake_breakout": not instrument["fake_breakout"],
            "no_fake_breakdown": not instrument["fake_breakdown"],
        })
    except Exception:
        checks.update({"trend_strong": False, "structure_supporting": False, "liquidity_supporting": False, "volume_supporting": False, "vwap_supporting": False, "momentum_supporting": False, "smart_money_supporting": False, "entry_not_late": False, "no_fake_breakout": False, "no_fake_breakdown": False})
        instrument = {}
    breadth = analysis.get("breadth") if isinstance(analysis.get("breadth"), Mapping) else {}
    sector_momentum = breadth.get("sector_momentum") if isinstance(breadth.get("sector_momentum"), Mapping) else {}
    sector_value = sector_momentum.get(get_sector(symbol))
    sector_optional = any(name in str(symbol).upper() for name in ("NIFTY", "GOLD", "SILVER"))
    checks["sector_supporting"] = sector_optional if sector_value is None else (float(sector_value) >= 0 if side == "BUY" else float(sector_value) <= 0)
    nifty = analysis.get("nifty") if isinstance(analysis.get("nifty"), Mapping) else {}
    symbol_close, nifty_price = _series(market, "close"), float(nifty.get("price", 0) or 0)
    relative_strength = 0.0
    if len(symbol_close) >= 6 and symbol_close[-6] > 0 and nifty_price > 0:
        symbol_momentum = (symbol_close[-1] - symbol_close[-6]) / symbol_close[-6] * 100
        relative_strength = symbol_momentum - float(nifty.get("momentum", 0) or 0)
    checks["relative_strength_supporting"] = relative_strength >= -0.05 if side == "BUY" else relative_strength <= 0.05
    failed = [name for name, passed in checks.items() if not passed]
    quality = max(0, min(100, int(round(sum(checks.values()) / len(checks) * 100))))
    return {"valid": not failed, "checks": checks, "failed_checks": failed, "signal_context_score": quality, "relative_strength": round(relative_strength, 3), "relative_weakness": round(max(0.0, -relative_strength), 3), "sector_momentum": sector_value, "instrument": instrument}


def _get_market_analysis(): return get_market_analysis()
def get_market_strength(): return int(get_market_analysis()["market_strength"])
def get_market_confidence(): return int(get_market_analysis()["market_confidence"])
def get_market_direction(): return str(get_market_analysis()["market_direction"])
def get_market_regime(): return str(get_market_analysis()["market_regime"])
def get_market_score(): return int(get_market_analysis()["market_score"])
def is_market_bullish(): return get_market_regime() in {"STRONG BULL", "BULL"}
def is_market_bearish(): return get_market_regime() in {"STRONG BEAR", "BEAR"}
def is_sideways_market(): return get_market_regime() == "SIDEWAYS"
def is_market_tradeable(): return bool(get_market_analysis()["tradeable"])
def can_trade_market(): return is_market_tradeable()
