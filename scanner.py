"""Signal scanner with explicit rejection diagnostics for SHIVAY AI."""
from __future__ import annotations

import logging
from collections import Counter
from copy import deepcopy
from typing import Any

from ai_watchlist import get_ai_watchlist
from config import MAX_TRADES
from data import get_market_data
from data_quality import assess_market_data
from engine import run_engine
from entry_validity import evaluate_entry_validity
from sector_strength import get_sector, sector_priority
from strategy import analyze_trade
from trade_journal import record_rejection
from tradeplan import create_trade_plan

LOGGER = logging.getLogger("shivay.scanner")
_LAST_DIAGNOSTICS: dict[str, Any] = {}
_ALLOWED_DECISIONS = {"🔥 STRONG BUY", "✅ BUY", "🔥 STRONG SELL", "🔻 SELL"}


def _reject(counter: Counter[str], symbol: str, reason: str, context: dict[str, Any] | None = None) -> None:
    counter[reason] += 1
    try:
        record_rejection(symbol, [reason], context or {})
    except Exception:
        LOGGER.debug("Rejection journal unavailable for %s", symbol)


def get_last_scan_diagnostics() -> dict[str, Any]:
    return deepcopy(_LAST_DIAGNOSTICS)


def scan_market() -> list[dict[str, Any]]:
    global _LAST_DIAGNOSTICS
    results: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    scanned: set[str] = set()
    watchlist = list(dict.fromkeys(str(item).strip() for item in get_ai_watchlist() if str(item).strip()))

    for symbol in watchlist:
        if symbol in scanned:
            continue
        scanned.add(symbol)
        try:
            market = get_market_data(symbol)
            if not isinstance(market, dict):
                _reject(rejected, symbol, "no_market_data")
                continue
            price = float(market.get("price") or 0)
            if price < 100:
                _reject(rejected, symbol, "price_below_100", {"price": price})
                continue
            quality = assess_market_data(market)
            if not quality.get("valid"):
                _reject(rejected, symbol, "market_data_rejected", {"quality": quality})
                continue
            score_data = run_engine(market)
            if not isinstance(score_data, dict):
                _reject(rejected, symbol, "engine_filters_not_met")
                continue
            trade = analyze_trade(symbol, score_data)
            decision = str(trade.get("decision") or "")
            if decision not in _ALLOWED_DECISIONS:
                _reject(rejected, symbol, "strategy_not_tradeable", {"decision": decision})
                continue
            side = "BUY" if "BUY" in decision else "SELL"
            entry_state = score_data.get("chandelier_entry_state")
            if not isinstance(entry_state, dict) or not entry_state.get("confirmed") or entry_state.get("side") != side:
                _reject(rejected, symbol, "chandelier_confirmation_missing", {"side": side})
                continue
            plan = create_trade_plan(price, score_data["atr"], decision, score_data)
            if not isinstance(plan, dict):
                _reject(rejected, symbol, "trade_plan_invalid")
                continue
            signal_candle = entry_state.get("signal_candle")
            if not isinstance(signal_candle, dict):
                _reject(rejected, symbol, "signal_candle_missing")
                continue
            hard_stop = float(signal_candle.get("low") if side == "BUY" else signal_candle.get("high"))
            entry = float(plan.get("entry") or 0)
            if side == "BUY":
                plan["sl"] = round(max(float(plan.get("sl") or 0), hard_stop), 2)
                if plan["sl"] >= entry:
                    _reject(rejected, symbol, "buy_invalidation_invalid")
                    continue
            else:
                plan["sl"] = round(min(float(plan.get("sl") or 0), hard_stop), 2)
                if plan["sl"] <= entry:
                    _reject(rejected, symbol, "sell_invalidation_invalid")
                    continue
            validity = evaluate_entry_validity(market, side, plan, score_data)
            if not validity.get("valid"):
                reasons = validity.get("reasons") or ["entry_invalid"]
                for reason in reasons:
                    rejected[str(reason)] += 1
                _reject(rejected, symbol, "entry_validity_failed", {"reasons": reasons})
                continue
            results.append({
                "symbol": symbol, "sector": get_sector(symbol), "sector_priority": sector_priority(symbol),
                "price": round(price, 2), "entry": plan["entry"], "entry_zone": plan.get("entry_zone", (plan["entry"], plan["entry"])),
                "sl": plan["sl"], "target1": plan["target1"], "target2": plan["target2"], "target3": plan["target3"],
                "score": score_data["score"], "regime": score_data.get("regime"), "setup": score_data.get("setup"),
                "decision": decision, "side": side, "risk": trade.get("risk"), "confidence": trade.get("confidence"),
                "provider": market.get("provider", "UNKNOWN"), "data_timestamp": market.get("timestamp"),
                "is_live": bool(market.get("is_live", False)), "is_delayed": bool(market.get("is_delayed", True)),
                "data_quality": market.get("data_quality", quality), "atr": score_data.get("atr"), "adx": score_data.get("adx"),
                "rsi": score_data.get("rsi"), "vwap": score_data.get("vwap"), "relative_volume": score_data.get("relative_volume"),
                "timeframe_5m": score_data.get("timeframe_5m", "UNKNOWN"), "timeframe_15m": score_data.get("timeframe_15m", "UNKNOWN"),
                "timeframe_30m": score_data.get("timeframe_30m", "UNKNOWN"), "timeframe_60m": score_data.get("timeframe_60m", "UNKNOWN"),
                "market": score_data.get("market", "UNKNOWN"), "market_strength": score_data.get("market_strength", 0),
                "entry_validity": validity, "valid_until": validity.get("valid_until"),
                "invalidation_condition": validity.get("invalidation_condition"), "market_data": market,
                "entry_confirmed": True, "requires_entry_confirmation": False,
                "signal_candle": signal_candle, "chandelier_entry_state": entry_state,
            })
        except Exception as error:
            _reject(rejected, symbol, "scanner_exception", {"error": type(error).__name__})
            LOGGER.warning("Scan recovered for %s: %s", symbol, type(error).__name__)

    results.sort(key=lambda item: (float(item.get("score") or 0), float(item.get("sector_priority") or 0)), reverse=True)
    _LAST_DIAGNOSTICS = {
        "scanned": len(scanned), "eligible_alerts": len(results), "rejected": sum(rejected.values()),
        "rejection_reasons": dict(rejected.most_common()),
    }
    LOGGER.info("Scan complete: scanned=%s alerts=%s rejected=%s", len(scanned), len(results), sum(rejected.values()))
    return results[:max(1, int(MAX_TRADES))]
