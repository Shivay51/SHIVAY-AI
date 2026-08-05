"""Compact, private Telegram delivery for SHIVAY AI."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from audience_router import audience_for_signal, recipients

IST = ZoneInfo("Asia/Kolkata")
TELEGRAM_LIMIT = 4000
_sent_messages: set[Any] = set()
_symbol_direction: dict[str, str] = {}
_message_date = None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _money(value: Any) -> str | None:
    number = _number(value)
    return f"\u20b9{number:,.2f}" if number is not None else None


def _confidence(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        text = str(value or "LOW").upper()
        return text if text in {"HIGH", "MEDIUM", "LOW"} else "LOW"
    return "HIGH" if score >= 80 else "MEDIUM" if score >= 60 else "LOW"


def _clock(value: Any) -> str:
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return "Current setup"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    return stamp.astimezone(IST).strftime("%I:%M %p")


def _trend(value: Any) -> str:
    text = str(value or "SIDEWAYS").upper()
    if "BULL" in text or text == "BUY":
        return "BULLISH"
    if "BEAR" in text or text == "SELL":
        return "BEARISH"
    return "SIDEWAYS"


def _line(label: str, value: Any) -> str | None:
    return f"{label}: {value}" if value not in (None, "", [], ()) else None


async def _send(app: Any, message_key: Any, text: str, audience: str = "ALL") -> bool:
    global _message_date, _sent_messages, _symbol_direction
    today = datetime.now(IST).date()
    if _message_date != today:
        _message_date, _sent_messages, _symbol_direction = today, set(), {}
    if message_key in _sent_messages or app is None or not hasattr(app, "bot"):
        return False
    users = await asyncio.to_thread(recipients, audience)
    delivered = False
    for user in users:
        try:
            await app.bot.send_message(chat_id=int(user["id"]), text=str(text)[:TELEGRAM_LIMIT])
            delivered = True
        except asyncio.CancelledError:
            raise
        except Exception:
            continue
    if delivered:
        _sent_messages.add(message_key)
    return delivered


async def _delivery_valid(trade: dict[str, Any]) -> bool:
    """Revalidate a signal immediately before delivery using verified data."""
    try:
        from data import get_market_data
        from engine import run_engine
        from entry_validity import evaluate_entry_validity
        symbol = str(trade.get("symbol", ""))
        market = await asyncio.to_thread(get_market_data, symbol)
        if not isinstance(market, Mapping):
            return False
        refreshed = await asyncio.to_thread(run_engine, market)
        if not isinstance(refreshed, Mapping):
            return False
        side = "SELL" if "SELL" in str(trade.get("decision", trade.get("side", ""))).upper() else "BUY"
        if str(refreshed.get("signal_side", "")).upper() != side:
            return False
        price = _number(market.get("price"))
        if price is None:
            return False
        zone = trade.get("entry_zone")
        if not trade.get("entry_confirmed") and isinstance(zone, Sequence) and not isinstance(zone, (str, bytes)) and len(zone) >= 2:
            low, high = _number(zone[0]), _number(zone[1])
            atr = _number(trade.get("atr")) or 0.0
            tolerance = min(atr * 0.10, abs(float(high or 0) - float(low or 0)) * 0.50)
            if low is not None and high is not None and not (min(low, high) - tolerance <= price <= max(low, high) + tolerance):
                return False
        updated = dict(trade)
        updated.update({key: refreshed[key] for key in (
            "atr", "ema20", "ema50", "ema200", "vwap", "support", "resistance",
            "relative_volume", "volume_spike", "timeframe_5m", "timeframe_15m",
            "timeframe_30m", "timeframe_60m", "market_brain", "signal_context_score",
            "timeframe_aligned", "entry_timing_confirmed", "relative_strength",
            "chandelier_15m", "chandelier_30m", "chandelier_60m",
        ) if refreshed.get(key) is not None})
        validity = evaluate_entry_validity(market, side, updated, updated, current_price=price)
        if not validity.get("valid"):
            return False
        trade.update(updated, market_data=market, delivery_price=price, entry_validity=validity)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


def _signal_text(trade: dict[str, Any], side: str) -> str:
    validity = trade.get("entry_validity") if isinstance(trade.get("entry_validity"), Mapping) else {}
    strong = "STRONG" in str(trade.get("decision", side)).upper()
    heading = f"STRONG {side}" if strong else side
    icon = "\U0001f525" if strong else "\U0001f7e2" if side == "BUY" else "\U0001f534"
    lines = [f"{icon} SHIVAY AI — {str(trade.get('symbol', 'UNKNOWN')).upper()}", "", heading, ""]
    for label, raw in (("Current Price", trade.get("delivery_price", trade.get("price"))), ("Entry Price", trade.get("entry")),
                       ("Stop Loss", trade.get("sl")), ("Target 1", trade.get("target1")),
                       ("Target 2", trade.get("target2")), ("Target 3", trade.get("target3"))):
        value = _money(raw)
        if value:
            lines.append(f"{label}: {value}")
    raw_trend = trade.get("regime", trade.get("trend"))
    trend = _trend(raw_trend)
    if "STRONG" in str(raw_trend or "").upper():
        trend = f"STRONG {trend}"
    lines.extend(["", f"Trend: {trend}"])
    for label in ("Support", "Resistance"):
        value = _money(trade.get(label.lower()))
        if value:
            lines.append(f"{label}: {value}")
    lines.append(f"Valid Until: {_clock(validity.get('valid_until', trade.get('valid_until')))}")
    return "\n".join(lines)


def format_wait_status(analysis: Mapping[str, Any] | None = None) -> str:
    value = dict(analysis or {})
    if not value:
        try:
            from market_brain import get_market_analysis
            value = dict(get_market_analysis() or {})
        except Exception:
            value = {}
    instrument = value.get("nifty") if isinstance(value.get("nifty"), Mapping) else {}
    lines = ["\U0001f7e1 SHIVAY AI — MARKET", "", "WAIT", "", f"Trend: {_trend(value.get('market_regime'))}"]
    for label, raw in (("Buy Above", instrument.get("resistance")), ("Sell Below", instrument.get("support")),
                       ("Support", instrument.get("support")), ("Resistance", instrument.get("resistance"))):
        money = _money(raw)
        if money:
            lines.append(f"{label}: {money}")
    lines.append("Status: Waiting for a confirmed entry")
    return "\n".join(lines)


async def send_wait_status(app: Any) -> bool:
    return await _send(app, ("WAIT", datetime.now(IST).date(), "STARTUP"), format_wait_status(), "ALL")


def format_commodity_signal(data: Mapping[str, Any], instrument: str) -> str:
    value = dict(data or {})
    name = str(instrument).strip().upper()
    trend = _trend(value.get("trend"))
    if not value.get("indian_mcx_verified"):
        return f"\U0001f7e8 MCX {name} — WAIT\n\nIndian MCX price unavailable\nTrend: {trend}\nAction: Wait for verified MCX data"
    indicators = value.get("indicators") if isinstance(value.get("indicators"), Mapping) else {}
    signal = str(value.get("signal", "WAIT")).upper()
    side = signal if signal in {"BUY", "SELL"} else "WAIT"
    strong = side != "WAIT" and float(value.get("confidence", 0) or 0) >= 90
    heading = f"STRONG {side}" if strong else side
    icon = "\U0001f525" if strong else "\U0001f7e2" if side == "BUY" else "\U0001f534" if side == "SELL" else "\U0001f7e1"
    state = str(value.get("status", value.get("data_status", ""))).upper()
    if side == "WAIT" and state in {"NO DATA", "WARMING UP", "PARTIAL", "STALE", "DEGRADED"}:
        lines = [f"{icon} SHIVAY AI — {name}", "", "WARMING UP"]
        price = _money(value.get("price"))
        if price:
            lines.extend(["", f"Current Price: {price}"])
        lines.append(f"Trend: {trend}")
        if value.get("data_ready"):
            lines.append(f"Data Ready: {value['data_ready']}")
        if value.get("pending"):
            lines.append(f"Pending: {value['pending']}")
        return "\n".join(lines)
    if side == "WAIT":
        lines = [f"{icon} SHIVAY AI — {name}", "", "WAIT", "", f"Trend: {trend}"]
        for label, raw in (("Buy Above", indicators.get("resistance")), ("Sell Below", indicators.get("support")),
                           ("Support", indicators.get("support")), ("Resistance", indicators.get("resistance"))):
            money = _money(raw)
            if money:
                lines.append(f"{label}: {money}")
        lines.append("Status: Waiting for a confirmed entry")
        return "\n".join(lines)
    lines = [f"{icon} SHIVAY AI — {name}", "", heading, ""]
    for label, raw in (("Current Price", value.get("price")), ("Entry", value.get("entry")), ("Stop Loss", value.get("sl")),
                       ("Target 1", value.get("target1")), ("Target 2", value.get("target2")), ("Target 3", value.get("target3"))):
        money = _money(raw)
        if money:
            lines.append(f"{label}: {money}")
    lines.extend(["", f"Trend: {trend}"])
    for label in ("Support", "Resistance"):
        money = _money(indicators.get(label.lower()))
        if money:
            lines.append(f"{label}: {money}")
    lines.append(f"Valid Until: {_clock(value.get('valid_until') or datetime.now(IST) + timedelta(minutes=18))}")
    return "\n".join(lines)


async def _send_signal(app: Any, trade: dict[str, Any], side: str) -> bool:
    symbol = str(trade.get("symbol", "")).strip()
    if not symbol or _symbol_direction.get(symbol) not in (None, side) or not await _delivery_valid(trade):
        return False
    key = ("SIGNAL", side, symbol, trade.get("entry"), trade.get("valid_until"))
    delivered = await _send(app, key, _signal_text(trade, side), audience_for_signal(trade))
    if delivered:
        _symbol_direction[symbol] = side
    return delivered


async def send_buy_signal(app: Any, trade: dict[str, Any]) -> bool:
    return await _send_signal(app, trade, "BUY")


async def send_sell_signal(app: Any, trade: dict[str, Any]) -> bool:
    return await _send_signal(app, trade, "SELL")


async def send_trade_update(app: Any, update: Mapping[str, Any]) -> bool:
    status = str(update.get("type", "HOLD")).upper()
    symbol = str(update.get("symbol", "UNKNOWN"))
    headings = {"TARGET1": "\u2705 TARGET 1 HIT", "TARGET2": "\u2705 TARGET 2 HIT", "TARGET3": "\U0001f3c1 TARGET 3 HIT",
                "TARGET HIT": "\U0001f3c1 TARGET HIT", "STOP LOSS HIT": "\u274c STOP LOSS HIT", "HOLD UPDATE": "\U0001f525 HOLD"}
    lines = [headings.get(status, f"\u26a0\ufe0f TRADE UPDATE — {symbol}"), symbol]
    if status == "TARGET1":
        lines.append("Protect capital; move SL toward cost when structurally safe")
    elif status == "TARGET2":
        lines.append("Trail stop more aggressively")
    elif status in {"TARGET3", "TARGET HIT", "STOP LOSS HIT"}:
        lines.append("Trade closed")
    else:
        lines.append(f"Status: {status}")
    for label, raw in (("Price", update.get("price")), ("Active SL", update.get("new_sl"))):
        money = _money(raw)
        if money:
            lines.append(f"{label}: {money}")
    if update.get("cancelled"):
        lines.append(f"Reason: {str(update.get('reason', status)).replace('_', ' ')[:100]}")
    elif update.get("closed"):
        points = float(update.get("pnl_points", 0) or 0)
        percent = float(update.get("pnl_percent", 0) or 0)
        result = "PROFIT" if points > 0 else "LOSS" if points < 0 else "BREAKEVEN"
        lines.extend(["", f"Result: {result}", f"P&L: {points:+.2f} pts ({percent:+.2f}%)"])
    key = ("UPDATE", symbol, status, update.get("new_sl"), update.get("price"))
    return await _send(app, key, "\n".join(lines), audience_for_signal(dict(update)))


async def send_target_hit(app: Any, update: Mapping[str, Any]) -> bool:
    return await send_trade_update(app, dict(update, type=update.get("type", "TARGET3")))


async def send_stoploss_hit(app: Any, update: Mapping[str, Any]) -> bool:
    return await send_trade_update(app, dict(update, type="STOP LOSS HIT"))


async def send_partial_exit(app: Any, update: Mapping[str, Any]) -> bool:
    return await send_trade_update(app, dict(update, type=update.get("type", "TARGET1")))


async def send_full_exit(app: Any, update: Mapping[str, Any]) -> bool:
    return await send_trade_update(app, dict(update, type="FULL EXIT"))


async def send_trailing_update(app: Any, update: Mapping[str, Any]) -> bool:
    return await send_trade_update(app, dict(update, type=update.get("type", "TARGET2")))


def _index_block(name: str, value: Mapping[str, Any]) -> str | None:
    if not value.get("available") or not _number(value.get("latest_price")):
        return None
    def band(key: str) -> str | None:
        values = [_money(item) for item in value.get(key, []) if _money(item)]
        return f"{values[0]} – {values[1]}" if len(values) >= 2 else None
    lines = [name, "", str(value.get("tomorrow_outlook", "SIDEWAYS FOR TOMORROW"))]
    for label, key in (("Open", "opening_range"), ("High", "high_range"), ("Low", "low_range")):
        text = band(key)
        if text:
            lines.append(f"{label}: {text}")
    for label, key in (("Bullish Above", "bullish_above"), ("Bearish Below", "bearish_below")):
        money = _money(value.get(key))
        if money:
            lines.append(f"{label}: {money}")
    supports = [_money(item) for item in value.get("support_levels", []) if _money(item)]
    resistances = [_money(item) for item in value.get("resistance_levels", []) if _money(item)]
    if supports:
        lines.append(f"Support: {' / '.join(supports[:2])}")
    if resistances:
        lines.append(f"Resistance: {' / '.join(resistances[:2])}")
    return "\n".join(lines)


def format_index_outlook_report(data: Mapping[str, Any], night: bool = False) -> str:
    outlooks = data.get("index_outlooks") if isinstance(data.get("index_outlooks"), Mapping) else {}
    nifty = _index_block("NIFTY", outlooks.get("NIFTY", {})) if isinstance(outlooks.get("NIFTY"), Mapping) else None
    bank = _index_block("BANKNIFTY", outlooks.get("BANKNIFTY", {})) if isinstance(outlooks.get("BANKNIFTY"), Mapping) else None
    if not nifty or not bank:
        return "\u26a0\ufe0f SHIVAY AI — MARKET OUTLOOK\n\nWARMING UP\n\nFresh NIFTY and BANKNIFTY history is not yet sufficient."
    preferred = str(data.get("preferred_next_session_direction" if night else "preferred_trade_direction", "NO TRADE")).upper()
    plan = "BUY ON DIP" if preferred == "BUY" else "SELL ON RISE" if preferred == "SELL" else "WAIT"
    risk = str(data.get("overnight_risk_level" if night else "risk_level", "HIGH")).upper()
    title = "\U0001f319 SHIVAY AI — NIGHT MARKET OUTLOOK" if night else "\U0001f305 SHIVAY AI — MARKET OUTLOOK"
    return f"{title}\n\n{nifty}\n\n{bank}\n\nBest Plan: {plan}\nRisk: {risk}"


async def send_market_report(app: Any, report: Mapping[str, Any]) -> bool:
    outlooks = report.get("index_outlooks") if isinstance(report.get("index_outlooks"), Mapping) else {}
    if outlooks:
        text = format_index_outlook_report(report)
    else:
        direction = _trend(report.get("market_direction", report.get("market_bias")))
        preferred = "BUY" if direction == "BULLISH" else "SELL" if direction == "BEARISH" else "WAIT"
        text = f"\U0001f310 SHIVAY AI — MARKET\n\nDirection: {direction}\nPreferred Trade: {preferred}\nRisk: {report.get('risk_level', report.get('market_risk', 'HIGH'))}"
    return await _send(app, ("MARKET", datetime.now(IST).strftime("%Y%m%d%H%M"), hash(text)), text, "ALL")


async def send_morning_prediction(app: Any, prediction: Mapping[str, Any]) -> bool:
    text = format_index_outlook_report(prediction)
    return await _send(app, ("MORNING", datetime.now(IST).date(), hash(text)), text, "ALL")


async def send_overnight_report(app: Any, report: Mapping[str, Any]) -> bool:
    text = format_index_outlook_report(report, night=True)
    return await _send(app, ("NIGHT", datetime.now(IST).date(), hash(text)), text, "ALL")


async def send_daily_summary(app: Any, summary: Mapping[str, Any]) -> bool:
    text = f"\U0001f4c8 SHIVAY AI — DAILY SUMMARY\n\nTrades: {summary.get('trades', 0)}\nWins: {summary.get('wins', 0)}\nLosses: {summary.get('losses', 0)}\nP&L: {summary.get('pnl', 0)}"
    return await _send(app, ("SUMMARY", datetime.now(IST).date()), text, "ADMIN")
