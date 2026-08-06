# ==========================================
# SHIVAY AI PRO v3
# AI Scanner
# ==========================================

import logging

from ai_watchlist import get_ai_watchlist
from sector_strength import (
    get_sector,
    sector_priority,
)

from data import get_market_data
from data_quality import assess_market_data
from engine import run_engine
from strategy import analyze_trade
from tradeplan import create_trade_plan
from entry_validity import evaluate_entry_validity
from trade_journal import record_rejection

from config import MAX_TRADES

LOGGER = logging.getLogger("shivay.scanner")


def scan_market():

    results = []

    scanned = set()

    watchlist = get_ai_watchlist()

    LOGGER.info("AI scanner started for %d instruments", len(watchlist))

    for symbol in watchlist:

        try:

            if symbol in scanned:
                continue

            market = get_market_data(symbol)

            if market is None:
                continue

            quality = assess_market_data(market)
            if not quality["valid"]:
                continue

            score_data = run_engine(market)

            if score_data is None:
                continue

            trade = analyze_trade(
                symbol,
                score_data,
            )

            if trade["decision"] not in (
                "🔥 STRONG BUY",
                "✅ BUY",
                "🔥 STRONG SELL",
                "🔻 SELL",
            ):
                continue

            plan = create_trade_plan(
                market["price"],
                score_data["atr"],
                trade["decision"],
                score_data,
            )

            side = "BUY" if "BUY" in trade["decision"] else "SELL"
            chandelier_entry = score_data.get("chandelier_entry_state")
            if (not isinstance(chandelier_entry, dict) or not chandelier_entry.get("confirmed")
                    or chandelier_entry.get("side") != side):
                record_rejection(symbol, ["chandelier_primary_confirmation_missing"], {"side": side})
                continue
            signal_candle = chandelier_entry.get("signal_candle")
            confirmation_candle = chandelier_entry.get("confirmation_candle")
            if not isinstance(signal_candle, dict) or not isinstance(confirmation_candle, dict):
                record_rejection(symbol, ["missing_chandelier_signal_or_confirmation_candle"], {"side": side})
                continue
            hard_invalidation = float(signal_candle["low"] if side == "BUY" else signal_candle["high"])
            if side == "BUY":
                plan["sl"] = round(max(float(plan["sl"]), hard_invalidation), 2)
                if plan["sl"] >= float(plan["entry"]):
                    record_rejection(symbol, ["invalid_buy_signal_candle_invalidation"], {"side": side})
                    continue
            else:
                plan["sl"] = round(min(float(plan["sl"]), hard_invalidation), 2)
                if plan["sl"] <= float(plan["entry"]):
                    record_rejection(symbol, ["invalid_sell_signal_candle_invalidation"], {"side": side})
                    continue
            validity = evaluate_entry_validity(market, side, plan, score_data)
            if not validity["valid"]:
                record_rejection(symbol, validity["reasons"], {"side": side, "setup": score_data.get("setup"), "provider": market.get("provider"), "delay_seconds": quality.get("delay_seconds")})
                continue

            results.append({

                "symbol": symbol,

                "sector": get_sector(symbol),

                "sector_priority": sector_priority(symbol),

                "price": round(market["price"], 2),

                "entry": plan["entry"],

                "sl": plan["sl"],

                "target1": plan["target1"],

                "target2": plan["target2"],

                "target3": plan["target3"],

                "score": score_data["score"],

                "regime": score_data["regime"],

                "setup": score_data.get("setup"),

                "market": score_data.get("market"),

                "market_strength": score_data.get("market_strength"),

                "ema20": score_data["ema20"],

                "ema50": score_data["ema50"],

                "ema200": score_data["ema200"],

                "rsi": score_data["rsi"],

                "atr": score_data["atr"],

                "adx": score_data["adx"],

                "supertrend": score_data["supertrend"],

                "macd": score_data["macd"],

                "vwap": score_data["vwap"],

                "volume_spike": score_data["volume_spike"],

                "relative_volume": score_data.get("relative_volume"),

                "timeframe_5m": score_data.get("timeframe_5m", "UNKNOWN"),

                "timeframe_15m": score_data.get("timeframe_15m", "UNKNOWN"),

                "timeframe_30m": score_data.get("timeframe_30m", "UNKNOWN"),

                "timeframe_60m": score_data.get("timeframe_60m", "UNKNOWN"),

                "timeframe_aligned": bool(score_data.get("timeframe_aligned", False)),

                "entry_timing_confirmed": bool(score_data.get("entry_timing_confirmed", False)),

                "support": score_data["support"],

                "resistance": score_data["resistance"],

                "decision": trade["decision"],

                "risk": trade["risk"],

                "confidence": trade["confidence"],

                "provider": market.get("provider", "UNKNOWN"),

                "data_timestamp": market.get("timestamp"),

                "is_live": bool(market.get("is_live", False)),

                "is_delayed": bool(market.get("is_delayed", True)),

                "delay_seconds": quality.get("delay_seconds"),

                "freshness_status": quality.get("status", "REJECTED"),

                "data_quality": market.get("data_quality", quality),

                "instrument_type": market.get("instrument_type", "UNKNOWN"),
                "trading_symbol": market.get("trading_symbol", symbol),

                "market_category": "MCX" if market.get("segment") == "MCX_COMM" else "NSE F&O",

                "segment": market.get("segment", "UNKNOWN"),

                "sector_strength": sector_priority(symbol),

                "relative_strength": score_data.get("relative_strength"),

                "relative_weakness": score_data.get("market_brain", {}).get("relative_weakness"),

                "smart_money_activity": score_data.get("market_brain", {}).get("instrument", {}).get("smart_money_activity"),

                "signal_context_score": score_data.get("signal_context_score"),

                "market_brain": score_data.get("market_brain", {}),

                "nifty_direction": score_data.get("market", "UNKNOWN"),

                "banknifty_direction": score_data.get("market", "UNKNOWN"),

                "expiry": market.get("expiry"),

                "timestamp": market.get("timestamp"),

                "freshness": quality.get("status", "REJECTED"),

                "entry_zone": plan.get("entry_zone", (plan["entry"], plan["entry"])),

                "risk_reward": validity["current_risk_reward"],

                "valid_until": validity["valid_until"],

                "invalidation_condition": validity["invalidation_condition"],

                "entry_validity": validity,

                "market_data": market,
                "entry_confirmed": True,
                "requires_entry_confirmation": False,
                "signal_candle": signal_candle,
                "signal_candle_high": signal_candle["high"],
                "signal_candle_low": signal_candle["low"],
                "signal_candle_close": signal_candle["close"],
                "signal_candle_timestamp": signal_candle.get("timestamp"),
                "signal_candle_open": signal_candle["open"],
                "chandelier_signal_level": chandelier_entry.get("chandelier_level"),
                "confirmation_candle": confirmation_candle,
                "confirmation_candle_timestamp": confirmation_candle.get("timestamp"),
                "entry_trigger_status": "CONFIRMED",
                "entry_confirmation_reason": chandelier_entry.get("reason"),
                "hard_invalidation_level": hard_invalidation,
                "expected_hold": "25-30 MIN / 30-60 MIN / 1-2 HOURS",
                "chandelier_15m": score_data.get("chandelier_15m"),
                "chandelier_30m": score_data.get("chandelier_30m"),
                "chandelier_60m": score_data.get("chandelier_60m"),
                "chandelier_entry_state": chandelier_entry,

            })

            scanned.add(symbol)

        except Exception as e:

            LOGGER.warning("Scan recovered for %s: %s", symbol, type(e).__name__)

    results.sort(

        key=lambda x: (

            x["score"],

            x["sector_priority"],

            x["market_strength"],

            float(
                str(x["confidence"]).replace("%", "")
            ),

        ),

        reverse=True,

    )

    LOGGER.info("Scan completed with %d ranked signals", len(results))

    return results[:MAX_TRADES]
