"""Telegram command handlers for SHIVAY AI."""

from __future__ import annotations

import asyncio
import csv
import importlib
import inspect
import logging
import re
import time as monotonic_time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

import config
from confidence_engine import (
    get_buy_confidence,
    get_confidence,
    get_risk_score,
    get_sell_confidence,
    get_trade_quality,
)
from gift_nifty import (
    get_gift_nifty_confidence,
    get_gift_nifty_direction,
    get_gift_nifty_regime,
    get_gift_nifty_strength,
    is_gift_nifty_tradeable,
)
from gift_nifty_prediction import predict_opening
from market_brain import (
    get_market_confidence,
    get_market_direction,
    get_market_regime,
    get_market_strength as get_brain_market_strength,
    is_market_tradeable as is_brain_market_tradeable,
)
from market_prediction import predict_market
from market_sentiment import (
    can_trade_by_sentiment,
    get_market_sentiment,
    get_sentiment_confidence,
)
from market_strength import (
    get_market_strength,
    get_strength_confidence,
    get_trend_strength,
    is_market_tradeable,
)
from performance import get_report
from scanner import scan_market
from signal_ranker import rank_trade
from trade_monitor import get_all_trades
from admin import add_user, is_authorized_user, list_users, remove_user


LOGGER = logging.getLogger("shivay.commands")
IST = ZoneInfo("Asia/Kolkata")
TELEGRAM_LIMIT = 4096
MESSAGE_LIMIT = 3900
MANUAL_SCAN_COOLDOWN = 20.0
_MANUAL_SCAN_LOCK = asyncio.Lock()
_USER_SCAN_TIMES: dict[int, float] = {}
_NAME_PATTERN = re.compile(r"^[\w .-]{1,40}$", re.UNICODE)
_VALID_PLANS = {"BASIC", "PREMIUM", "VIP", "NSE_FO", "MCX", "ALL"}


def _effective_id(update: Update) -> int | None:
    user = update.effective_user
    try:
        value = int(user.id) if user is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value is not None and value > 0 else None


def _admin_ids() -> set[int]:
    values: list[Any] = []
    if hasattr(config, "ADMIN_ID"):
        values.append(getattr(config, "ADMIN_ID"))
    configured = getattr(config, "ADMIN_IDS", ())
    if isinstance(configured, (str, int)):
        configured = [configured]
    values.extend(configured)
    result = set()
    for value in values:
        try:
            admin_id = int(value)
            if admin_id > 0:
                result.add(admin_id)
        except (TypeError, ValueError, OverflowError):
            continue
    return result


def _is_admin(update: Update) -> bool:
    user_id = _effective_id(update)
    return user_id is not None and user_id in _admin_ids()


def _authorized_ids() -> set[int]:
    # Retained for compatibility; the secure database deliberately does not
    # expose its complete ID set to non-admin callers.
    return set(_admin_ids())


def _is_authorized(update: Update) -> bool:
    user_id = _effective_id(update)
    return user_id is not None and is_authorized_user(user_id)


async def _reply(update: Update, text: str) -> None:
    message = update.effective_message
    if message is None:
        return
    text = str(text).strip() or "No data available."
    # Repair legacy source strings saved as UTF-8 bytes decoded through
    # Windows-1252. Correct Unicode text is left untouched.
    if any(marker in text for marker in ("Ã", "â", "ð", "ï")):
        try:
            text = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    chunks = []
    while len(text) > MESSAGE_LIMIT:
        split_at = text.rfind("\n", 0, MESSAGE_LIMIT)
        if split_at < MESSAGE_LIMIT // 2:
            split_at = MESSAGE_LIMIT
        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()
    chunks.append(text)
    for chunk in chunks:
        try:
            await message.reply_text(chunk[:TELEGRAM_LIMIT])
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Telegram reply delivery failed")
            return


async def _denied(update: Update) -> None:
    await _reply(update, "⛔ Access denied. This command requires authorization.")


async def _require_authorized(update: Update) -> bool:
    if _is_authorized(update):
        return True
    await _denied(update)
    return False


async def _require_admin(update: Update) -> bool:
    if _is_admin(update):
        return True
    await _reply(update, "⛔ Access denied. Administrator permission is required.")
    return False


async def _run(function: Callable[..., Any], *args: Any) -> Any:
    if inspect.iscoroutinefunction(function):
        return await function(*args)
    return await asyncio.to_thread(function, *args)


async def _safe_command(update: Update, label: str, function: Callable[[], Any]) -> None:
    try:
        result = function()
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.exception("Command failed: %s", label)
        await _reply(update, "⚠️ The request could not be completed right now. Please try again shortly.")


def _bot_enabled(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.application.bot_data.get("shivay_enabled", True))


def _number(value: Any) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _side(trade: dict[str, Any]) -> str | None:
    decision = str(trade.get("decision", trade.get("side", ""))).upper()
    buy = "BUY" in decision
    sell = "SELL" in decision
    if buy == sell:
        return None
    return "BUY" if buy else "SELL"


def _rank_results(signals: Iterable[dict[str, Any]], wanted_side: str | None = None) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for source in signals:
        if not isinstance(source, dict):
            continue
        symbol = str(source.get("symbol", "")).strip()
        side = _side(source)
        if not symbol or side is None or (wanted_side and side != wanted_side):
            continue
        trade = source.copy()
        try:
            trade.update(rank_trade(trade))
        except Exception:
            LOGGER.exception("Ranking failed for %s", symbol)
        if not trade.get("valid"):
            continue
        trade["side"] = side
        key = (
            _number(trade.get("signal_score")),
            _number(trade.get("score")),
            _number(trade.get("confidence")),
            _number(trade.get("sector_priority")),
        )
        current = best.get(symbol)
        if current is None or key > current["_sort_key"]:
            trade["_sort_key"] = key
            best[symbol] = trade
    ranked = sorted(best.values(), key=lambda item: item["_sort_key"], reverse=True)
    maximum = max(1, min(int(getattr(config, "MAX_TRADES", 5)), 5))
    return ranked[:maximum]


def _format_trade(trade: dict[str, Any], index: int) -> str:
    side = trade.get("side") or _side(trade) or "NO TRADE"
    icon = "🟢" if side == "BUY" else "🔴"
    lines = [f"{index}. {icon} {side} — {trade.get('symbol', 'UNKNOWN')}"]
    zone = trade.get("entry_zone")
    if isinstance(zone, (list, tuple)) and len(zone) >= 2 and _number(zone[0]) > 0 and _number(zone[1]) > 0:
        lines.append(f"Entry: ₹{_number(zone[0]):,.2f} – ₹{_number(zone[1]):,.2f}")
    for label, key in (("SL", "sl"), ("T1", "target1"), ("T2", "target2"), ("T3", "target3")):
        value = _number(trade.get(key))
        if value > 0: lines.append(f"{label}: ₹{value:,.2f}")
    lines.append(f"Reason: {str(trade.get('setup', 'Fresh confirmed setup')).replace('_', ' ')[:80]}")
    return "\n".join(lines)


async def _manual_scan(update: Update, context: ContextTypes.DEFAULT_TYPE, wanted_side: str | None = None) -> None:
    if not await _require_authorized(update):
        return
    if not _bot_enabled(context):
        await _reply(update, "⏸️ SHIVAY AI trading commands are paused by the administrator.")
        return
    user_id = _effective_id(update) or 0
    now = monotonic_time.monotonic()
    remaining = MANUAL_SCAN_COOLDOWN - (now - _USER_SCAN_TIMES.get(user_id, 0.0))
    if remaining > 0:
        await _reply(update, f"⏳ Please wait {int(remaining) + 1} seconds before scanning again.")
        return
    if _MANUAL_SCAN_LOCK.locked():
        await _reply(update, "⏳ A market scan is already running. Please try again shortly.")
        return
    _USER_SCAN_TIMES[user_id] = now
    await _reply(update, f"🔎 Scanning for {wanted_side or 'BUY and SELL'} opportunities…")
    async with _MANUAL_SCAN_LOCK:
        signals = await _run(scan_market)
    ranked = _rank_results(signals or [], wanted_side)
    if not ranked:
        await _reply(update, f"NO TRADE\n\nNo valid {wanted_side or 'BUY or SELL'} setup is available right now.")
        return
    heading = f"SHIVAY AI — {wanted_side or 'RANKED'} SIGNALS"
    await _reply(update, heading + "\n\n" + "\n\n".join(_format_trade(item, i) for i, item in enumerate(ranked, 1)))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        await _reply(
            update,
            "🔱 SHIVAY AI PRO\n\n"
            "AI-assisted NSE market analysis, ranked BUY/SELL signals, and active-trade monitoring.\n\n"
            "Use /help to view available commands.",
        )
    await _safe_command(update, "start", action)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        await _reply(
            update,
            "SHIVAY AI COMMANDS\n\n"
            "/status — System and market status\n/scan — Ranked BUY/SELL scan\n"
            "/market — Market regime and strength\n/giftnifty — Gift Nifty analysis\n"
            "/prediction — Opening and market prediction\n/buy — BUY opportunities\n/sell — SELL opportunities\n"
            "/trades — Active trades\n/open — Open trades\n/closed — Closed-trade summary\n"
            "/report — Market report\n/performance — Performance report\n"
            "/gold — Gold analysis\n/silver — Silver analysis\n"
            "/ping — Service health\n/version — Version\n/id — Your Telegram ID\n\n"
            "Administrator: /adduser /removeuser /listusers /startbot /stopbot /restart\n"
            "Details: /marketdetails /predictiondetails /golddetails /silverdetails /provider /systemhealth",
        )
    await _safe_command(update, "help", action)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_authorized(update):
            return
        data = await _run(lambda: {
            "direction": get_market_direction(),
            "regime": get_market_regime(),
            "tradeable": is_brain_market_tradeable(),
        })
        await _reply(
            update,
            "🟢 SHIVAY AI STATUS\n\n"
            f"Bot: {'ACTIVE' if _bot_enabled(context) else 'PAUSED'}\n"
            f"Market: {data['direction']} ({data['regime']})\n"
            f"Action: {'LOOK FOR CONFIRMED SETUPS' if data['tradeable'] else 'WAIT'}",
        )
    await _safe_command(update, "status", action)


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_command(update, "scan", lambda: _manual_scan(update, context))


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        user_id = _effective_id(update)
        chat = update.effective_chat
        await _reply(update, f"Telegram user ID: {user_id or 'Unavailable'}\nChat ID: {chat.id if chat else 'Unavailable'}")
    await _safe_command(update, "id", action)


async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_admin(update):
            return
        args = list(context.args or [])
        if len(args) not in (1, 2, 3):
            await _reply(update, "Usage: /adduser TELEGRAM_ID [NAME] [BASIC|PREMIUM|VIP|NSE_FO|MCX|ALL]")
            return
        try:
            user_id = int(args[0])
        except (TypeError, ValueError, OverflowError):
            await _reply(update, "⚠️ TELEGRAM_ID must be a positive integer.")
            return
        if user_id <= 0 or user_id > 10**15:
            await _reply(update, "⚠️ TELEGRAM_ID is outside the valid range.")
            return
        name = args[1].strip() if len(args) >= 2 else "User"
        plan = args[2].upper().strip() if len(args) == 3 else "BASIC"
        if not _NAME_PATTERN.fullmatch(name):
            await _reply(update, "⚠️ Name may contain only letters, numbers, spaces, dots, hyphens, and underscores.")
            return
        if plan not in _VALID_PLANS:
            await _reply(update, "Plan must be BASIC, PREMIUM, VIP, NSE_FO, MCX, or ALL.")
            return
        added = await _run(add_user, _effective_id(update), user_id, name, plan)
        await _reply(update, f"✅ User {user_id} added with {plan} access." if added else "ℹ️ That user already exists.")
    await _safe_command(update, "adduser", action)


async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_admin(update):
            return
        args = list(context.args or [])
        if len(args) != 1:
            await _reply(update, "Usage: /removeuser TELEGRAM_ID")
            return
        try:
            user_id = int(args[0])
        except (TypeError, ValueError, OverflowError):
            await _reply(update, "⚠️ TELEGRAM_ID must be a positive integer.")
            return
        if user_id <= 0 or user_id > 10**15:
            await _reply(update, "⚠️ TELEGRAM_ID is outside the valid range.")
            return
        existed = await _run(remove_user, _effective_id(update), user_id)
        await _reply(update, f"✅ User {user_id} removed." if existed else "ℹ️ User was not found.")
    await _safe_command(update, "removeuser", action)


async def listusers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_admin(update):
            return
        users = await _run(list_users, _effective_id(update))
        if not users:
            await _reply(update, "No users are configured.")
            return
        lines = ["AUTHORIZED USERS"]
        for index, user in enumerate(users, 1):
            name = str(user.get("name", "User")).replace("\n", " ")[:40]
            state = "Active" if user.get("active", False) else "Disabled"
            lines.append(f"{index}. {name} — {user.get('id', 'N/A')} — {user.get('plan', 'BASIC')} — {state}")
        await _reply(update, "\n".join(lines))
    await _safe_command(update, "listusers", action)


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_authorized(update):
            return
        import morning_prediction
        from telegram_service import format_index_outlook_report
        data = await _run(morning_prediction.generate_morning_prediction)
        await _reply(update, format_index_outlook_report(data, night=False).replace("🌅 MORNING MARKET OUTLOOK", "🌐 MARKET STATUS"))
    await _safe_command(update, "market", action)


async def giftnifty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_authorized(update): return
        data = await _run(lambda: {"direction": get_gift_nifty_direction(), "regime": get_gift_nifty_regime(),
            "tradeable": is_gift_nifty_tradeable()})
        await _reply(update, "🌏 GIFT NIFTY\n\n" + f"Direction: {data['direction']}\nRegime: {data['regime']}\n"
            f"Action: {'WATCH FOR CONFIRMATION' if data['tradeable'] else 'WAIT'}")
    await _safe_command(update, "giftnifty", action)


async def prediction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_authorized(update): return
        import morning_prediction
        data = await _run(morning_prediction.generate_morning_prediction)
        await _reply(update, morning_prediction.format_morning_report(data))
    await _safe_command(update, "prediction", action)


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_command(update, "buy", lambda: _manual_scan(update, context, "BUY"))


async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_command(update, "sell", lambda: _manual_scan(update, context, "SELL"))


def _open_trade_text() -> str:
    trades = [trade.copy() for trade in get_all_trades().values() if not trade.get("closed", False)]
    if not trades:
        return "NO OPEN TRADES"
    lines = ["📌 OPEN TRADES"]
    for index, trade in enumerate(trades, 1):
        lines.append(f"{index}. {trade.get('symbol')} — {trade.get('side')}\nEntry ₹{trade.get('entry')} | SL ₹{trade.get('sl')}\nT1 ₹{trade.get('target1')} | T2 ₹{trade.get('target2')} | T3 ₹{trade.get('target3')}")
    return "\n\n".join(lines)


async def trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if await _require_authorized(update): await _reply(update, await _run(_open_trade_text))
    await _safe_command(update, "trades", action)


async def open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await trades(update, context)


def _history_rows() -> list[dict[str, str]]:
    try:
        from performance_report import get_completed_trades
        records = get_completed_trades(100)
        if records:
            return [{
                "Date": str(row.get("closed_at", ""))[:16],
                "Symbol": str(row.get("symbol", "")),
                "Result": str(row.get("result", "")),
                "PnL %": str(row.get("pnl_percent", row.get("pnl", 0))),
                "Exit": str(row.get("exit_price", row.get("exit", 0))),
            } for row in reversed(records)]
    except Exception:
        LOGGER.warning("Canonical completed-trade history unavailable; using legacy CSV")
    path = Path(__file__).with_name("trade_history.csv")
    if not path.exists(): return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


async def closed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_authorized(update): return
        rows = await _run(_history_rows)
        closed_rows = [row for row in rows if row.get("Result") in {"WIN", "LOSS"}]
        if not closed_rows:
            await _reply(update, "NO CLOSED TRADES")
            return
        lines = ["📚 RECENT CLOSED TRADES"]
        for row in closed_rows[-10:][::-1]:
            lines.append(f"{row.get('Date', '')} — {row.get('Symbol', '')}\n{row.get('Result', '')} | P&L {row.get('PnL %', '0')}% | Exit ₹{row.get('Exit', '0')}")
        await _reply(update, "\n\n".join(lines))
    await _safe_command(update, "closed", action)


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_authorized(update): return
        data = await _run(predict_market)
        await _reply(update, "📊 MARKET REPORT\n\n" +
            f"Bias: {data.get('market_bias', 'NO TRADE')}\nDirection: {data.get('expected_direction', 'SIDEWAYS')}\n"
            f"Bullish: {data.get('bullish_probability', 0)}%\nBearish: {data.get('bearish_probability', 0)}%\n"
            f"Sideways: {data.get('sideways_probability', 0)}%\n"
            f"Quality: {data.get('prediction_quality', 'LOW')}\nRisk: {data.get('market_risk', 'HIGH')}")
    await _safe_command(update, "report", action)


async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_authorized(update): return
        data = await _run(get_report)
        if not data:
            await _reply(update, "📈 PERFORMANCE\n\nNo completed performance data is available yet.")
            return
        await _reply(update, "📈 PERFORMANCE\n\n" + f"Total trades: {data.get('total', 0)}\nWins: {data.get('wins', 0)}\n"
            f"Losses: {data.get('loss', 0)}\nOpen: {data.get('open', 0)}\nWin rate: {data.get('win_rate', 0)}%\n"
            f"Total P&L: {data.get('total_pnl', 0)}%\nAverage P&L: {data.get('average_pnl', 0)}%\n"
            f"Best: {data.get('best_trade', 0)}% | Worst: {data.get('worst_trade', 0)}%")
    await _safe_command(update, "performance", action)


def _commodity_function(metal: str) -> Callable[..., Any] | None:
    for module_name in (metal, "mcx", "commodity_scanner", "gold_silver", "metal_scanner"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        except Exception:
            LOGGER.exception("Optional commodity module failed: %s", module_name)
            continue
        for name in (f"get_{metal}_analysis", f"analyze_{metal}", f"scan_{metal}"):
            function = getattr(module, name, None)
            if callable(function): return function
    return None


async def _commodity(update: Update, metal: str) -> None:
    if not await _require_authorized(update): return
    function = _commodity_function(metal)
    if function is None:
        await _reply(update, f"{metal.upper()} analysis is not configured on this installation.")
        return
    result = await _run(function)
    if isinstance(result, dict):
        module = importlib.import_module(metal)
        formatter = getattr(module, f"format_{metal}_report", None)
        text = formatter(result) if callable(formatter) else str(result)
    else:
        text = str(result or "No analysis available.")
    await _reply(update, f"{metal.upper()} ANALYSIS\n\n{text}")


async def gold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_command(update, "gold", lambda: _commodity(update, "gold"))


async def silver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_command(update, "silver", lambda: _commodity(update, "silver"))


async def _details(update: Update, kind: str) -> None:
    if not await _require_admin(update): return
    if kind in {"gold", "silver"}:
        module = importlib.import_module(kind)
        data = await _run(getattr(module, f"analyze_{kind}"))
        await _reply(update, getattr(module, f"format_{kind}_details")(data))
    elif kind == "provider":
        from provider_manager import get_provider_status
        data = await _run(get_provider_status)
        await _reply(update, "PROVIDER DETAILS\n\n" + "\n".join(f"{key}: {value}" for key, value in data.items()))
    elif kind == "systemhealth":
        import startup
        data = await startup.health_check(None, probe_network=False)
        await _reply(update, "SYSTEM HEALTH\n\n" + "\n".join(f"{key}: {value}" for key, value in data.items()))
    elif kind == "prediction":
        import morning_prediction
        data = await _run(morning_prediction.generate_morning_prediction)
        await _reply(update, "PREDICTION DETAILS\n\n" + "\n".join(f"{key}: {value}" for key, value in data.items() if key != "inputs"))
    else:
        data = await _run(predict_market)
        await _reply(update, "MARKET DETAILS\n\n" + "\n".join(f"{key}: {value}" for key, value in data.items()))


async def marketdetails(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await _safe_command(update, "marketdetails", lambda: _details(update, "market"))
async def predictiondetails(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await _safe_command(update, "predictiondetails", lambda: _details(update, "prediction"))
async def golddetails(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await _safe_command(update, "golddetails", lambda: _details(update, "gold"))
async def silverdetails(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await _safe_command(update, "silverdetails", lambda: _details(update, "silver"))
async def provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await _safe_command(update, "provider", lambda: _details(update, "provider"))
async def systemhealth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await _safe_command(update, "systemhealth", lambda: _details(update, "systemhealth"))


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        started = monotonic_time.monotonic()
        await context.bot.get_me()
        latency = int((monotonic_time.monotonic() - started) * 1000)
        await _reply(update, f"🏓 Pong — Telegram connected ({latency} ms)\nTime: {datetime.now(IST):%d-%b-%Y %I:%M:%S %p IST}")
    await _safe_command(update, "ping", action)


async def version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        name = str(getattr(config, "BOT_NAME", "SHIVAY AI PRO"))
        value = str(getattr(config, "BOT_VERSION", "3.0"))
        await _reply(update, f"{name}\nVersion: {value}\nScheduler: Async / Asia-Kolkata")
    await _safe_command(update, "version", action)


async def startbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_admin(update): return
        context.application.bot_data["shivay_enabled"] = True
        await _reply(update, "✅ SHIVAY AI trading commands are active.")
    await _safe_command(update, "startbot", action)


async def stopbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_admin(update): return
        context.application.bot_data["shivay_enabled"] = False
        await _reply(update, "⏸️ SHIVAY AI trading commands are paused. Telegram remains online.")
    await _safe_command(update, "stopbot", action)


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def action() -> None:
        if not await _require_admin(update): return
        context.application.bot_data["shivay_enabled"] = False
        _USER_SCAN_TIMES.clear()
        await asyncio.sleep(0)
        context.application.bot_data["shivay_enabled"] = True
        await _reply(update, "✅ SHIVAY AI command state restarted successfully.")
    await _safe_command(update, "restart", action)
