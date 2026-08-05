"""Pure, explainable setup ranking with no additional market-data requests."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from config import MIN_RISK_REWARD, MIN_SCORE

_best_signal: dict[str, Any] | None = None
_last_grade, _last_priority = "C", 0
_sent_signals: set[tuple[str, str]] = set()
_sent_date: date | None = None


def _num(value: Any) -> float:
    try: return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError): return 0.0


def _side(signal: Mapping[str, Any]) -> str | None:
    text = str(signal.get("decision", signal.get("side", ""))).upper()
    buy, sell = "BUY" in text, "SELL" in text
    return "BUY" if buy and not sell else "SELL" if sell and not buy else None


def rank_trade(signal):
    global _last_grade, _last_priority
    if not isinstance(signal, Mapping):
        return {"valid": False, "signal_score": 0, "trade_grade": "C", "trade_priority": 0, "telegram_priority": "NONE"}
    side = _side(signal)
    score, confidence = _num(signal.get("score")), _num(signal.get("confidence"))
    adx_value, context = _num(signal.get("adx")), _num(signal.get("signal_context_score"))
    entry, stop, target = _num(signal.get("entry")), _num(signal.get("sl")), _num(signal.get("target2"))
    risk, reward = abs(entry - stop), abs(target - entry)
    rr = reward / risk if risk > 0 else 0.0
    timeframes = [str(signal.get(f"timeframe_{item}", "UNKNOWN")) for item in ("60m", "30m", "15m", "5m")]
    expected = "BULLISH" if side == "BUY" else "BEARISH"
    mtf = timeframes[0] == expected and timeframes[1] == expected and timeframes[2] == expected and timeframes[3] in {expected, "SIDEWAYS"}
    context_checks = signal.get("market_brain", {}).get("checks", {}) if isinstance(signal.get("market_brain"), Mapping) else {}
    critical_ok = bool(context_checks) and all(bool(value) for value in context_checks.values())
    valid = bool(side and score >= max(72, int(MIN_SCORE)) and confidence >= 72 and adx_value >= 22 and rr >= float(MIN_RISK_REWARD) and bool(signal.get("volume_spike")) and bool(signal.get("entry_validity", {}).get("valid", True)) and context >= 75 and critical_ok and mtf)
    quality = int(min(100, round(score * .30 + confidence * .22 + context * .20 + min(100, adx_value * 2.5) * .10 + min(100, rr * 25) * .10 + _num(signal.get("sector_priority", 50)) * .04 + min(100, _num(signal.get("relative_volume")) * 50) * .04))) if valid else 0
    grade = "A+" if quality >= 90 else "A" if quality >= 84 else "B+" if quality >= 78 else "C"
    valid = valid and grade != "C"
    priority = 100 if grade == "A+" else 90 if grade == "A" else 75 if grade == "B+" else 0
    _last_grade, _last_priority = grade, priority
    return {"valid": valid, "signal_score": quality if valid else 0, "final_confidence": int(confidence) if valid else 0, "trade_grade": grade, "trade_priority": priority, "telegram_priority": "URGENT" if grade == "A+" else "HIGH" if grade == "A" else "NORMAL" if grade == "B+" else "NONE", "risk_reward": round(rr, 2), "signal_quality": "STRONG" if quality >= 90 else "QUALIFIED" if valid else "WEAK"}


def rank_signals(signals, max_signals=3):
    global _best_signal
    best: dict[str, dict[str, Any]] = {}
    for source in signals or []:
        if not isinstance(source, Mapping): continue
        value, symbol = dict(source), str(source.get("symbol", "")).strip()
        ranking = rank_trade(value)
        if not symbol or not ranking["valid"]: continue
        value.update(ranking)
        if symbol not in best or value["signal_score"] > best[symbol]["signal_score"]: best[symbol] = value
    ranked = sorted(best.values(), key=lambda item: (item["signal_score"], item["trade_priority"]), reverse=True)[:max(1, min(int(max_signals), 5))]
    _best_signal = dict(ranked[0]) if ranked else None
    return ranked


def get_best_signal(): return dict(_best_signal) if _best_signal else None
def get_trade_grade(): return _last_grade
def get_trade_priority(): return _last_priority


def should_send_signal(signal):
    global _sent_date, _sent_signals
    today = date.today()
    if _sent_date != today: _sent_date, _sent_signals = today, set()
    side, symbol = _side(signal), str(signal.get("symbol", "")).strip()
    identity = (symbol, str(side))
    if not symbol or side is None or identity in _sent_signals or not rank_trade(signal)["valid"]: return False
    _sent_signals.add(identity)
    return True
