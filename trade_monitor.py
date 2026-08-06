import logging
import time
from datetime import datetime, timezone

import config
from chandelier_exit import (
    calculate_timeframe_chandelier,
    get_completed_signal_candle,
    update_chandelier_trailing_stop,
)
from data import get_live_price
from exit_optimizer import optimize_exit
from data_quality import parse_timestamp


_active_trades = {}
LOGGER = logging.getLogger("shivay.trade_monitor")


def add_trade(trade):
    symbol = trade["symbol"]
    side = "SELL" if "SELL" in str(trade.get("decision", trade.get("side", "BUY"))) else "BUY"

    tracked = {
        "symbol": symbol,
        "side": side,
        "entry": float(trade["entry"]),
        "sl": float(trade["sl"]),
        "target1": float(trade["target1"]),
        "target2": float(trade["target2"]),
        "target3": float(trade["target3"]),
        "t1_hit": False,
        "t2_hit": False,
        "t3_hit": False,
        "partial_exit": False,
        "closed": False,
        "market_category": trade.get("market_category", "NSE F&O"),
        "segment": trade.get("segment", "UNKNOWN"),
        "provider": trade.get("provider", "UNKNOWN"),
        "setup": trade.get("setup", "UNKNOWN"),
        "score": trade.get("score", 0),
        "confidence": trade.get("confidence", 0),
        "opened_at": trade.get("telegram_time") or trade.get("timestamp"),
        "opened_monotonic": time.monotonic(),
        "highest_price": float(trade["entry"]),
        "lowest_price": float(trade["entry"]),
        "outcome_recorded": False,
        "entry_confirmed": bool(trade.get("entry_confirmed", not trade.get("requires_entry_confirmation", False))),
        "activation_notified": not bool(trade.get("requires_entry_confirmation", False)),
        "cancelled": False,
        "cancel_reason": None,
        "track_outcome": bool(trade.get("track_outcome", True)),
        "time_to_sl": None, "time_to_t1": None, "time_to_t2": None, "time_to_t3": None,
    }
    for key in ("market", "regime", "nifty_direction", "banknifty_direction", "sector", "sector_strength", "relative_strength", "signal_context_score", "timeframe_5m", "timeframe_15m", "timeframe_30m", "timeframe_60m", "adx", "rsi", "atr", "vwap", "relative_volume", "support", "resistance", "risk_reward", "valid_until", "data_timestamp", "telegram_time", "instrument_type", "entry_zone", "requires_entry_confirmation", "signal_candle", "signal_candle_open", "signal_candle_high", "signal_candle_low", "signal_candle_close", "signal_candle_timestamp", "confirmation_candle", "confirmation_candle_timestamp", "chandelier_signal_level", "hard_invalidation_level", "entry_trigger_status", "entry_confirmation_reason", "chandelier_15m", "chandelier_30m", "chandelier_60m"):
        if key in trade: tracked[key] = trade[key]
    _active_trades[symbol] = tracked


def remove_trade(symbol):
    if symbol in _active_trades:
        del _active_trades[symbol]


def cancel_trade(symbol, reason="CANCELLED SIGNAL"):
    """Close an unentered paper signal without counting it as a trade result."""
    trade = _active_trades.get(symbol)
    if trade is None or trade.get("closed"):
        return None
    trade.update(closed=True, cancelled=True, cancel_reason=str(reason)[:120])
    try:
        from trade_journal import record_event
        record_event("CANCELLED", {**trade, "reason": trade["cancel_reason"]})
    except Exception as error:
        LOGGER.warning("Signal cancellation journal recovered from %s", type(error).__name__)
    return {
        "type": "EXPIRED SIGNAL" if "EXPIRED" in str(reason).upper() else "CANCELLED SIGNAL",
        "symbol": trade["symbol"],
        "side": trade["side"],
        "closed": True,
        "cancelled": True,
        "reason": trade["cancel_reason"],
    }


def expire_trade(symbol):
    return cancel_trade(symbol, "EXPIRED SIGNAL")


def get_all_trades():
    return _active_trades


def check_stoploss(trade, price):
    if trade["side"] == "BUY":
        return float(price) <= float(trade["sl"])

    return float(price) >= float(trade["sl"])


def check_target(trade, price):
    price = float(price)

    if trade["side"] == "BUY":
        if not trade["t1_hit"] and price >= trade["target1"]:
            return "TARGET1"
        if trade["t1_hit"] and not trade["t2_hit"] and price >= trade["target2"]:
            return "TARGET2"
        if trade["t2_hit"] and not trade["t3_hit"] and price >= trade["target3"]:
            return "TARGET3"
    else:
        if not trade["t1_hit"] and price <= trade["target1"]:
            return "TARGET1"
        if trade["t1_hit"] and not trade["t2_hit"] and price <= trade["target2"]:
            return "TARGET2"
        if trade["t2_hit"] and not trade["t3_hit"] and price <= trade["target3"]:
            return "TARGET3"

    return None


def check_trailing_stop(trade, price, trailing_stop=0.0):
    trailing_stop = float(trailing_stop)

    if trailing_stop <= 0:
        return False

    if trade["side"] == "BUY":
        return float(price) <= trailing_stop

    return float(price) >= trailing_stop


def check_exit(trade, price, market=None, score_data=None):
    if check_stoploss(trade, price):
        return "STOP LOSS HIT", 0.0

    target = check_target(trade, price)

    if target == "TARGET3":
        return "TARGET3", 0.0

    if market is not None and score_data is not None:
        exit_data = optimize_exit(
            market,
            score_data,
            trade,
            trade["side"],
        )

        if exit_data["full_exit"]:
            return "FULL EXIT", float(exit_data["best_exit"])

        if exit_data["trail_stop"]:
            return "TRAIL STOP LOSS", float(exit_data["trailing_stop_price"])

        if exit_data["partial_exit"]:
            return "PARTIAL EXIT", float(exit_data["best_exit"])

    if target == "TARGET1":
        return "TARGET1", 0.0

    if target == "TARGET2":
        return "TARGET2", float(trade["target1"])

    return "HOLD", 0.0


def should_hold_trade(trade, price, market=None, score_data=None):
    action, _ = check_exit(trade, price, market, score_data)
    return action == "HOLD"


def _pending_entry_update(trade, price, market=None, score_data=None):
    """Confirm or cancel a preliminary setup without using future candles."""
    now = datetime.now(timezone.utc)
    expiry = parse_timestamp(trade.get("valid_until"))
    if expiry is not None and now > expiry.astimezone(timezone.utc):
        return cancel_trade(trade["symbol"], "EXPIRED SIGNAL")
    side = trade["side"]
    price = float(price)
    atr_value = float(trade.get("atr") or 0)
    signal_high = float(trade.get("signal_candle_high") or 0)
    signal_low = float(trade.get("signal_candle_low") or 0)
    signal_close = float(trade.get("signal_candle_close") or 0)
    if min(price, atr_value, signal_high, signal_low, signal_close) <= 0 or signal_high < signal_low:
        return cancel_trade(trade["symbol"], "INVALID SIGNAL CANDLE")
    buffer = atr_value * max(0.0, float(getattr(config, "ENTRY_BREAK_BUFFER_ATR", 0.05)))
    invalidated = price < signal_low - buffer if side == "BUY" else price > signal_high + buffer
    if invalidated:
        return cancel_trade(trade["symbol"], "STRUCTURAL INVALIDATION")
    target1 = float(trade["target1"])
    planned_entry = float(trade["entry"])
    travelled = ((price - planned_entry) / (target1 - planned_entry)) if side == "BUY" and target1 > planned_entry else ((planned_entry - price) / (planned_entry - target1)) if side == "SELL" and target1 < planned_entry else 1.0
    if travelled >= 0.65:
        return cancel_trade(trade["symbol"], "TARGET 1 NEARLY REACHED")
    if (side == "BUY" and price - planned_entry > atr_value * 0.75) or (side == "SELL" and planned_entry - price > atr_value * 0.75):
        return cancel_trade(trade["symbol"], "LATE ENTRY")

    chandelier_15 = calculate_timeframe_chandelier(market, 15) if market else trade.get("chandelier_15m", {})
    chandelier_30 = calculate_timeframe_chandelier(market, 30) if market else trade.get("chandelier_30m", {})
    required_trend = "BULLISH" if side == "BUY" else "BEARISH"
    if chandelier_30.get("valid") and chandelier_30.get("trend") != required_trend:
        return cancel_trade(trade["symbol"], "CHANDELIER TREND REVERSED")
    completed = get_completed_signal_candle(market, 15) if market else None
    signal_stamp = parse_timestamp(trade.get("signal_candle_timestamp"))
    if signal_stamp is not None:
        maximum_age = max(1, int(getattr(config, "SIGNAL_MAX_AGE_CANDLES", 2))) * 15 * 60
        if (now - signal_stamp.astimezone(timezone.utc)).total_seconds() > maximum_age:
            return cancel_trade(trade["symbol"], "EXPIRED SIGNAL")
    completed_stamp = parse_timestamp(completed.get("timestamp")) if completed else None
    confirmation_window = max(1, int(getattr(config, "ENTRY_CONFIRMATION_CANDLES", 1))) * 15 * 60
    next_candle = bool(signal_stamp and completed_stamp and 0 < (completed_stamp - signal_stamp).total_seconds() <= confirmation_window)
    breakout = price >= signal_high + buffer if side == "BUY" else price <= signal_low - buffer
    retest = False
    if next_candle and completed:
        candle_open, candle_close = float(completed["open"]), float(completed["close"])
        if side == "BUY":
            retest = candle_close > max(candle_open, signal_close) and float(completed["low"]) <= signal_high + buffer
        else:
            retest = candle_close < min(candle_open, signal_close) and float(completed["high"]) >= signal_low - buffer
    if not (breakout or retest):
        return {"type": "PENDING ENTRY", "symbol": trade["symbol"], "side": side, "closed": False}

    stop = float(trade["sl"])
    chandelier_stop = float(chandelier_15.get("current_stop") or 0)
    if chandelier_stop > 0:
        stop = update_chandelier_trailing_stop(side, stop, chandelier_stop)
    if side == "BUY":
        stop = min(stop, price - atr_value * 0.65)
        stop = max(stop, price - atr_value * 1.75)
    else:
        stop = max(stop, price + atr_value * 0.65)
        stop = min(stop, price + atr_value * 1.75)
    risk = abs(price - stop)
    remaining_reward = (float(trade["target2"]) - price) if side == "BUY" else (price - float(trade["target2"]))
    if risk <= 0 or remaining_reward / risk < float(getattr(config, "MIN_RISK_REWARD", 2.0)):
        return cancel_trade(trade["symbol"], "RISK REWARD DETERIORATED")
    direction = 1 if side == "BUY" else -1
    trade.update(
        entry=price, sl=stop, target1=price + direction * risk * 1.5,
        target2=price + direction * risk * 2.25, target3=price + direction * risk * 3.25,
        entry_confirmed=True, entry_trigger_status="CONFIRMED",
        entry_confirmation_reason="candle_breakout" if breakout else "completed_candle_retest",
        opened_monotonic=time.monotonic(), highest_price=price, lowest_price=price,
        active_chandelier_stop=chandelier_stop if chandelier_stop > 0 else stop,
        last_chandelier_candle=completed.get("timestamp") if completed else trade.get("signal_candle_timestamp"),
    )
    return {"type": "ENTRY CONFIRMED", "symbol": trade["symbol"], "side": side, "closed": False, **{key: trade.get(key) for key in trade}}


def _apply_completed_candle_trail(trade, market):
    completed = get_completed_signal_candle(market, 15) if market else None
    if not completed:
        return None
    stamp = str(completed.get("timestamp") or "")
    if not stamp or stamp == str(trade.get("last_chandelier_candle") or ""):
        return None
    trade["last_chandelier_candle"] = stamp
    result = calculate_timeframe_chandelier(market, 15)
    side = trade["side"]
    expected = "BULLISH" if side == "BUY" else "BEARISH"
    candidate = float(result.get("current_stop") or 0)
    if result.get("valid") and candidate > 0:
        tightened = update_chandelier_trailing_stop(side, float(trade.get("active_chandelier_stop") or trade["sl"]), candidate)
        if tightened > 0:
            trade["active_chandelier_stop"] = tightened
            trade["sl"] = update_chandelier_trailing_stop(side, float(trade["sl"]), tightened)
    close = float(completed["close"])
    active = float(trade.get("active_chandelier_stop") or 0)
    crossed = active > 0 and ((side == "BUY" and close < active) or (side == "SELL" and close > active))
    if crossed or (result.get("valid") and result.get("trend") != expected):
        return "CHANDELIER EXIT", close
    return None


def monitor_trade(trade, price, market=None, score_data=None):
    if not trade.get("entry_confirmed", True):
        return _pending_entry_update(trade, price, market, score_data)
    if not trade.get("activation_notified", True):
        return {"type": "ENTRY CONFIRMED", "symbol": trade["symbol"], "side": trade["side"],
                "closed": False, **{key: trade.get(key) for key in trade}}
    trade["highest_price"] = max(float(trade.get("highest_price", price)), float(price))
    trade["lowest_price"] = min(float(trade.get("lowest_price", price)), float(price))
    invalidation = float(trade.get("hard_invalidation_level") or (trade.get("signal_candle_low") if trade["side"] == "BUY" else trade.get("signal_candle_high")) or 0)
    hard_invalidated = invalidation > 0 and ((trade["side"] == "BUY" and float(price) < invalidation)
                                             or (trade["side"] == "SELL" and float(price) > invalidation))
    active_chandelier = float(trade.get("active_chandelier_stop") or 0)
    chandelier_invalidated = check_trailing_stop(trade, price, active_chandelier)
    if hard_invalidated:
        action, updated_stop = "SIGNAL CANDLE INVALIDATION", 0.0
    elif chandelier_invalidated:
        action, updated_stop = "CHANDELIER EXIT", active_chandelier
    elif check_stoploss(trade, price):
        action, updated_stop = "STOP LOSS HIT", 0.0
    else:
        candle_exit = _apply_completed_candle_trail(trade, market)
        action, updated_stop = candle_exit if candle_exit else check_exit(trade, price, market, score_data)
    elapsed = round(max(0.0, time.monotonic() - float(trade.get("opened_monotonic", time.monotonic()))), 2)

    if action == "STOP LOSS HIT":
        trade["closed"] = True
        trade["time_to_sl"] = trade.get("time_to_sl") or elapsed
        trade["failure_cause"] = "stop_loss_hit"
    elif action == "TARGET3":
        trade["t3_hit"] = True
        trade["time_to_t3"] = trade.get("time_to_t3") or elapsed
        trade["closed"] = True
    elif action == "TARGET1":
        trade["t1_hit"] = True
        trade["time_to_t1"] = trade.get("time_to_t1") or elapsed
        trade["partial_exit"] = True
        if trade["side"] == "BUY":
            trade["sl"] = max(float(trade["sl"]), float(trade["entry"]))
        else:
            trade["sl"] = min(float(trade["sl"]), float(trade["entry"]))
    elif action == "TARGET2":
        trade["t2_hit"] = True
        trade["time_to_t2"] = trade.get("time_to_t2") or elapsed
        if updated_stop > 0:
            if trade["side"] == "BUY":
                trade["sl"] = max(float(trade["sl"]), updated_stop)
            else:
                trade["sl"] = min(float(trade["sl"]), updated_stop)
    elif action in {"FULL EXIT", "CHANDELIER EXIT", "SIGNAL CANDLE INVALIDATION"}:
        trade["closed"] = True
        trade["failure_cause"] = "signal_candle_invalidation" if action == "SIGNAL CANDLE INVALIDATION" else "strategy_exit"

    aligned_hold = (score_data or {}).get("timeframe_30m") in ({"BULLISH"} if trade["side"] == "BUY" else {"BEARISH"})
    chandelier_hold = (score_data or {}).get("chandelier_15m", {}).get("trend") in (None, "BULLISH" if trade["side"] == "BUY" else "BEARISH")
    if action == "HOLD" and trade.get("t2_hit") and aligned_hold and chandelier_hold and not trade.get("hold_alert_sent"):
        trade["hold_alert_sent"] = True
        action = "HOLD UPDATE"

    if trade["closed"] and trade.get("track_outcome") and not trade.get("outcome_recorded"):
        entry = float(trade["entry"])
        signed_pnl = float(price) - entry if trade["side"] == "BUY" else entry - float(price)
        mfe = float(trade["highest_price"]) - entry if trade["side"] == "BUY" else entry - float(trade["lowest_price"])
        mae = entry - float(trade["lowest_price"]) if trade["side"] == "BUY" else float(trade["highest_price"]) - entry
        outcome = {**trade, "exit": float(price), "exit_price": float(price), "closed_at": datetime.now(timezone.utc).isoformat(), "pnl": signed_pnl, "mfe": max(0.0, mfe), "mae": max(0.0, mae), "holding_seconds": elapsed, "reason": action, "result": "WIN" if signed_pnl > 0 else "LOSS" if signed_pnl < 0 else "BREAKEVEN"}
        try:
            from trade_journal import record_outcome
            from performance_report import record_trade
            record_outcome(outcome)
            record_trade(outcome)
        except Exception as error:
            LOGGER.warning("Completed-trade persistence recovered from %s", type(error).__name__)
        trade["outcome_recorded"] = True

    entry = float(trade["entry"])
    signed_pnl = float(price) - entry if trade["side"] == "BUY" else entry - float(price)
    pnl_percent = signed_pnl / entry * 100 if entry else 0.0
    return {
        "type": action,
        "symbol": trade["symbol"],
        "side": trade["side"],
        "entry": round(entry, 2),
        "price": round(float(price), 2),
        "new_sl": round(float(trade["sl"]), 2),
        "target1": round(float(trade["target1"]), 2),
        "target2": round(float(trade["target2"]), 2),
        "target3": round(float(trade["target3"]), 2),
        "pnl_points": round(signed_pnl, 2),
        "pnl_percent": round(pnl_percent, 2),
        "holding_seconds": elapsed,
        "closed": bool(trade["closed"]),
        "market_category": trade.get("market_category", "NSE F&O"),
        "segment": trade.get("segment", "UNKNOWN"),
    }


def update_trade(symbol, price=None, market=None, score_data=None):
    trade = _active_trades.get(symbol)

    if trade is None or trade["closed"]:
        return None

    if market is None:
        try:
            from data import get_market_data
            market = get_market_data(symbol)
        except Exception:
            market = None
    if score_data is None and market is not None:
        try:
            from engine import run_engine
            score_data = run_engine(market)
        except Exception:
            score_data = None
    if price is None:
        price = market.get("price") if isinstance(market, dict) else get_live_price(symbol)

    if price is None:
        return None

    return monitor_trade(trade, price, market, score_data)


def check_trades():
    alerts = []

    for symbol, trade in list(_active_trades.items()):
        if trade["closed"]:
            continue

        alert = update_trade(symbol)

        if alert is not None and alert["type"] != "HOLD":
            alerts.append(alert)

    return alerts
