"""Asynchronous production scheduler for SHIVAY AI."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from contextlib import suppress
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import config
from data import force_refresh
from gift_nifty import (
    get_gift_nifty_confidence,
    get_gift_nifty_direction,
    get_gift_nifty_regime,
    get_gift_nifty_strength,
)
from gift_nifty_prediction import predict_opening
from market_prediction import predict_market
from performance import get_report
from scanner import scan_market
from signal_memory import add_signal, clear_signals, signal_exists
from signal_ranker import rank_trade
from telegram_service import (
    send_buy_signal,
    send_daily_summary,
    send_full_exit,
    send_market_report,
    send_morning_prediction,
    send_overnight_report,
    send_partial_exit,
    send_sell_signal,
    send_stoploss_hit,
    send_target_hit,
    send_trade_update,
    send_trailing_update,
    send_wait_status,
)
from trade_monitor import add_trade, check_trades, get_all_trades, remove_trade
from trade_journal import record_event, record_signal
from audience_router import recipients


LOGGER = logging.getLogger("shivay.scheduler")
IST = ZoneInfo("Asia/Kolkata")

_RUNNING_APPLICATIONS: set[int] = set()
_INSTANCE_LOCK = asyncio.Lock()
_SCAN_LOCK = asyncio.Lock()
_MONITOR_LOCK = asyncio.Lock()
_TRIAL_WARNINGS_SENT: set[str] = set()
_TRADINGVIEW_STALE_WARNING_AT: datetime | None = None


def _cfg(name: str, default: Any) -> Any:
    return getattr(config, name, default)


def _clock(prefix: str, default_hour: int, default_minute: int) -> time:
    hour = int(_cfg(f"{prefix}_HOUR", default_hour))
    minute = int(_cfg(f"{prefix}_MINUTE", default_minute))
    return time(max(0, min(hour, 23)), max(0, min(minute, 59)))


MARKET_OPEN = _clock("MARKET_START", 9, 15)
MARKET_CLOSE = _clock("MARKET_END", 15, 30)
PREOPEN_TIME = _clock("MORNING_PREDICTION", 8, 45)
EOD_TIME = _clock("EOD_REPORT", 15, 35)
OVERNIGHT_TIME = _clock("OVERNIGHT_ANALYSIS", 16, 0)
LATE_EVENING_TIME = _clock("LATE_EVENING_UPDATE", 19, 30)
LATE_NIGHT_TIME = _clock("LATE_NIGHT_UPDATE", 23, 0)
OUTLOOK_EVENING_TIME = _clock("NEXT_SESSION_EVENING", 22, 0)
OUTLOOK_OVERNIGHT_TIME = _clock("NEXT_SESSION_OVERNIGHT", 1, 0)
OUTLOOK_PREOPEN_TIME = _clock("NEXT_SESSION_PREOPEN", 8, 0)
SCAN_SECONDS = max(60, int(_cfg("SCAN_INTERVAL", 300)))
MONITOR_SECONDS = max(15, int(_cfg("TRADE_MONITOR_INTERVAL", 30)))
CACHE_SECONDS = max(180, int(_cfg("CACHE_TIME", 300)))
HEALTH_SECONDS = max(300, int(_cfg("HEALTH_CHECK_INTERVAL", 900)))
MAX_SIGNALS = max(1, min(int(_cfg("MAX_TRADES", 5)), 5))


def _now() -> datetime:
    return datetime.now(IST)


def _market_day(day: date) -> bool:
    return day.weekday() < 5


def _market_open(now: datetime) -> bool:
    return _market_day(now.date()) and MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _mcx_open(now: datetime) -> bool:
    start = _clock("MCX_START", 9, 0)
    end = _clock("MCX_END", 23, 30)
    return _market_day(now.date()) and start <= now.time() <= end


def _next_scan_boundary(now: datetime) -> datetime:
    """Probe one minute after each 15m open, otherwise use a balanced 5m cadence."""
    primary = max(15, int(_cfg("PRIMARY_TIMEFRAME_MINUTES", 15)))
    if now.minute % primary == 0 and now.second < 55:
        return now.replace(second=5, microsecond=0) + timedelta(minutes=1)
    cadence = max(1, min(5, int(_cfg("SCAN_INTERVAL", 300)) // 60))
    elapsed = now.minute % cadence
    boundary = now.replace(second=5, microsecond=0) + timedelta(minutes=(cadence - elapsed) % cadence)
    if boundary <= now:
        boundary += timedelta(minutes=cadence)
    return boundary


async def _offload(function: Callable[..., Any], *args: Any) -> Any:
    return await asyncio.to_thread(function, *args)


async def _safe_call(label: str, function: Callable[..., Any], *args: Any) -> Any:
    try:
        if inspect.iscoroutinefunction(function):
            return await function(*args)
        return await _offload(function, *args)
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.exception("%s failed", label)
        return None


async def send_all(app: Any, text: str, audience: str = "ALL") -> bool:
    """Send a plain message to every active user without blocking other users."""
    users = await _safe_call("load active users", recipients, audience) or []

    async def deliver(user: dict[str, Any]) -> bool:
        try:
            await app.bot.send_message(chat_id=user["id"], text=text)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Telegram delivery failed for %s", user.get("id"))
            return False

    if not users:
        return False
    return any(await asyncio.gather(*(deliver(user) for user in users)))


def _side(trade: dict[str, Any]) -> str | None:
    decision = str(trade.get("decision", trade.get("side", ""))).upper()
    has_buy = "BUY" in decision
    has_sell = "SELL" in decision
    if has_buy == has_sell:
        return None
    return "BUY" if has_buy else "SELL"


def _number(value: Any) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _rank(signals: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank valid scanner output without adding another restrictive trade gate."""
    best_by_symbol: dict[str, dict[str, Any]] = {}
    for original in signals:
        if not isinstance(original, dict):
            continue
        symbol = str(original.get("symbol", "")).strip()
        side = _side(original)
        if not symbol or side is None or signal_exists(symbol):
            continue
        signal = original.copy()
        try:
            signal.update(rank_trade(signal))
        except Exception:
            LOGGER.exception("Signal ranking failed for %s", symbol)
        if not signal.get("valid"):
            continue
        signal["_side"] = side
        current = best_by_symbol.get(symbol)
        key = (
            _number(signal.get("signal_score", 0)),
            _number(signal.get("score", 0)),
            _number(signal.get("confidence", 0)),
        )
        if current is None or key > current["_rank_key"]:
            signal["_rank_key"] = key
            best_by_symbol[symbol] = signal
    ranked = sorted(best_by_symbol.values(), key=lambda item: item["_rank_key"], reverse=True)
    return ranked[:MAX_SIGNALS]


async def _scan_job(app: Any) -> int:
    if _SCAN_LOCK.locked():
        LOGGER.warning("Scan skipped because the previous scan is still running")
        return 0
    delivered_count = 0
    async with _SCAN_LOCK:
        signals = await _safe_call("market scan", scan_market) or []
        for trade in _rank(signals):
            side = trade.pop("_side")
            trade.pop("_rank_key", None)
            if trade.get("requires_entry_confirmation") and not trade.get("entry_confirmed"):
                symbol = str(trade["symbol"])
                existing = get_all_trades().get(symbol)
                if existing and not existing.get("closed"):
                    continue
                add_trade(trade)
                record_event("PRELIMINARY_SETUP", trade)
                continue
            sender = send_buy_signal if side == "BUY" else send_sell_signal
            delivered = await _safe_call(f"send {side} signal", sender, app, trade)
            if delivered:
                delivered_count += 1
                trade["telegram_time"] = datetime.now(IST).isoformat()
                add_signal(str(trade["symbol"]))
                add_trade(trade)
                record_signal(trade)
    return delivered_count


async def _monitor_job(app: Any) -> None:
    if _MONITOR_LOCK.locked():
        return
    async with _MONITOR_LOCK:
        alerts = await _safe_call("trade monitoring", check_trades) or []
        senders = {
            "TARGET1": send_partial_exit,
            "TARGET2": send_trailing_update,
            "TARGET3": send_target_hit,
            "TARGET HIT": send_target_hit,
            "STOP LOSS HIT": send_stoploss_hit,
            "PARTIAL EXIT": send_partial_exit,
            "FULL EXIT": send_full_exit,
            "TRAIL STOP LOSS": send_trailing_update,
        }
        for alert in alerts:
            if str(alert.get("type", "")).upper() == "ENTRY CONFIRMED":
                sender = send_sell_signal if str(alert.get("side", "")).upper() == "SELL" else send_buy_signal
                delivered = await _safe_call("send confirmed entry", sender, app, alert)
                if delivered:
                    symbol = str(alert.get("symbol", ""))
                    active = get_all_trades().get(symbol)
                    if active is not None:
                        active["activation_notified"] = True
                        active["telegram_time"] = datetime.now(IST).isoformat()
                    add_signal(symbol)
                    record_signal(dict(alert))
                continue
            sender = senders.get(str(alert.get("type", "")).upper(), send_trade_update)
            await _safe_call("send trade update", sender, app, alert)
            if alert.get("closed"):
                remove_trade(str(alert.get("symbol", "")))


def _gift_report() -> dict[str, Any]:
    prediction = predict_market()
    prediction.update(
        gift_nifty_direction=get_gift_nifty_direction(),
        gift_nifty_regime=get_gift_nifty_regime(),
        gift_nifty_strength=get_gift_nifty_strength(),
        gift_nifty_confidence=get_gift_nifty_confidence(),
        risk_level=prediction.get("market_risk", "N/A"),
    )
    return prediction


def _daily_summary() -> dict[str, Any]:
    report = get_report() or {}
    return {
        "trades": report.get("total", 0),
        "wins": report.get("wins", 0),
        "losses": report.get("loss", 0),
        "pnl": report.get("total_pnl", 0),
    }


async def _morning_job(app: Any) -> None:
    try:
        module = importlib.import_module("morning_prediction")
        prediction = await _safe_call("morning prediction", module.generate_morning_prediction)
        if prediction:
            await _safe_call("send morning prediction", module.send_morning_prediction, app, prediction)
    except Exception:
        LOGGER.exception("Morning-prediction job recovered from a module failure")


async def _eod_job(app: Any) -> None:
    summary = await _safe_call("performance report", _daily_summary)
    if summary:
        await _safe_call("send daily summary", send_daily_summary, app, summary)
    report = await _safe_call("end-of-day market report", predict_market)
    if report:
        report = report.copy()
        report["risk_level"] = report.get("market_risk", "N/A")
        await _safe_call("send market report", send_market_report, app, report)
    try:
        review_module = importlib.import_module("daily_review")
        review = await _safe_call("daily strategy review", review_module.generate_daily_review)
        if review:
            accuracy = review.get("accuracy", {})
            await send_all(app, "SHIVAY AI | DAILY REVIEW\n\nSample: %s\nWin rate: %s%%\nExpectancy: %s\nProfit factor: %s\nStrategy changed: NO\n\nTuning requires sufficient samples and walk-forward validation." % (accuracy.get("sample_size",0), accuracy.get("win_rate",0), accuracy.get("expectancy",0), accuracy.get("profit_factor",0)), "ADMIN")
    except Exception:
        LOGGER.warning("Daily review unavailable")


async def _overnight_job(app: Any) -> None:
    try:
        module = importlib.import_module("overnight_analysis")
        report = await _safe_call("overnight analysis", module.analyze_overnight)
        if report:
            await _safe_call("send overnight report", module.send_overnight_report, app, report)
    except Exception:
        LOGGER.exception("Overnight-analysis job recovered from a module failure")


def _optional_modules() -> list[Any]:
    modules = []
    for name in ("gold", "silver", "mcx", "mcx_scanner", "commodity_scanner", "gold_silver", "metal_scanner"):
        try:
            modules.append(importlib.import_module(name))
        except ModuleNotFoundError as error:
            if error.name != name:
                LOGGER.warning("Optional module %s could not load: %s", name, error)
        except Exception:
            LOGGER.exception("Optional module %s could not load", name)
    return modules


async def _optional_market_job(app: Any, modules: list[Any]) -> None:
    for module in modules:
        function = next(
            (getattr(module, name, None) for name in ("scheduled_task", "run_scheduled_tasks", "scan_gold", "scan_silver", "scan_market", "scan_mcx") if callable(getattr(module, name, None))),
            None,
        )
        if function is None:
            continue
        result = await _safe_call(f"optional task {module.__name__}", function)
        report_sender = getattr(module, f"send_{module.__name__}_report", None)
        if isinstance(result, dict) and callable(report_sender):
            await _safe_call(f"send {module.__name__} report", report_sender, app, result)
        elif isinstance(result, str) and result.strip():
            await send_all(app, result)
        elif isinstance(result, list):
            for trade in _rank(item for item in result if isinstance(item, dict)):
                side = trade.pop("_side")
                trade.pop("_rank_key", None)
                sender = send_buy_signal if side == "BUY" else send_sell_signal
                if await _safe_call("send optional signal", sender, app, trade):
                    add_signal(str(trade["symbol"]))
                    add_trade(trade)


async def _health_job() -> None:
    users = await _safe_call("scheduler health users", recipients, "ALL")
    trades = get_all_trades()
    for symbol, trade in list(trades.items()):
        if trade.get("closed"):
            remove_trade(symbol)
    LOGGER.info("Scheduler healthy: users=%s active_trades=%s", len(users or []), len(get_all_trades()))


async def _provider_health_job(app: Any, notify: bool = False) -> None:
    global _TRADINGVIEW_STALE_WARNING_AT
    try:
        from provider_health import provider_health_summary
        from provider_manager import get_provider_status
        summary, status = provider_health_summary(), get_provider_status()
        active = status.get("active_provider", status.get("selected_primary"))
        LOGGER.info("Provider health: active=%s mode=%s healthy=%s degraded=%s", active, status.get("mode"), summary.get("healthy"), summary.get("degraded"))
        if notify and summary.get("degraded"):
            LOGGER.warning("Verified market data is degraded; new invalid or stale signals remain blocked")
        if notify:
            for name in ("truedata", "gdfl"):
                expires = (status.get(name) or {}).get("trial_expires_at")
                if not expires or name in _TRIAL_WARNINGS_SENT:
                    continue
                try:
                    remaining = datetime.fromisoformat(expires.replace("Z", "+00:00")) - datetime.now().astimezone()
                    if remaining <= timedelta(days=2):
                        LOGGER.warning("Configured market-data access expires within two days")
                        _TRIAL_WARNINGS_SENT.add(name)
                except (TypeError, ValueError):
                    continue
            try:
                from tradingview_webhook import status as tradingview_status
                tv = tradingview_status()
                last = (tv.get("cache") or {}).get("last_received")
                if tv.get("enabled") and last:
                    stale_after = max(30, int(_cfg("MAX_SIGNAL_DATA_DELAY_SECONDS", 180)))
                    is_stale = (_now().astimezone(last.tzinfo) - last).total_seconds() > stale_after
                    cooldown_ready = _TRADINGVIEW_STALE_WARNING_AT is None or (_now() - _TRADINGVIEW_STALE_WARNING_AT).total_seconds() >= 3600
                    if is_stale and cooldown_ready:
                        LOGGER.warning("Authorized alert feed is stale; exact signals remain blocked")
                        _TRADINGVIEW_STALE_WARNING_AT = _now()
            except Exception:
                LOGGER.warning("TradingView bridge health check unavailable")
    except Exception:
        LOGGER.warning("Provider health check unavailable")


async def _late_update_job(app: Any, label: str) -> None:
    try:
        module = importlib.import_module("overnight_analysis")
        report = await _safe_call(label, lambda: module.analyze_overnight(force_refresh=True))
        if report:
            report = report.copy()
            report["update_type"] = label
            await _safe_call(f"send {label}", send_overnight_report, app, report)
    except Exception:
        LOGGER.exception("Late update recovered from an analysis failure")
    await _provider_health_job(app, notify=True)


async def _next_session_outlook_job(app: Any, label: str) -> None:
    """Publish refreshed NSE, Gold and Silver probability outlooks without fabricating inputs."""
    try:
        overnight = importlib.import_module("overnight_analysis")
        report = await _safe_call(label, lambda: overnight.analyze_overnight(force_refresh=True))
        if report:
            text = await _safe_call("format NSE next-session outlook", overnight.format_overnight_report, report)
            if text:
                await send_all(app, f"SHIVAY NEXT SESSION OUTLOOK - NSE\n{label}\n\n{text}")
    except Exception:
        LOGGER.exception("NSE next-session outlook recovered from a module failure")
    for module_name, title in (("gold", "SHIVAY MCX GOLD OUTLOOK"), ("silver", "SHIVAY MCX SILVER OUTLOOK")):
        try:
            module = importlib.import_module(module_name)
            analysis = await _safe_call(f"{module_name} outlook", getattr(module, f"analyze_{module_name}"), True)
            formatter = getattr(module, f"format_{module_name}_report")
            text = await _safe_call(f"format {module_name} outlook", formatter, analysis)
            if text:
                await send_all(app, f"{title}\n{label}\n\n{text}", "MCX")
        except Exception:
            LOGGER.exception("%s next-session outlook recovered from a module failure", module_name)


def _due(now: datetime, scheduled: time, completed: set[tuple[str, date]], name: str) -> bool:
    key = (name, now.date())
    return _market_day(now.date()) and now.time() >= scheduled and key not in completed


def _startup_completed(now: datetime) -> set[tuple[str, date]]:
    """Mark past report slots complete so a restart never floods historical reports."""
    slots = {
        "morning": PREOPEN_TIME, "eod": EOD_TIME, "overnight": OVERNIGHT_TIME,
        "late_evening": LATE_EVENING_TIME, "late_night": LATE_NIGHT_TIME,
        "outlook_2200": OUTLOOK_EVENING_TIME, "outlook_0100": OUTLOOK_OVERNIGHT_TIME,
        "outlook_0800": OUTLOOK_PREOPEN_TIME,
    }
    return {(name, now.date()) for name, scheduled in slots.items() if now.time() >= scheduled}


async def scheduler(application: Any) -> None:
    """Run SHIVAY AI jobs for a python-telegram-bot Application."""
    application_id = id(application)
    async with _INSTANCE_LOCK:
        if application_id in _RUNNING_APPLICATIONS:
            LOGGER.warning("Duplicate scheduler start ignored")
            return
        _RUNNING_APPLICATIONS.add(application_id)

    started_at = _now()
    completed: set[tuple[str, date]] = _startup_completed(started_at)
    optional_modules = _optional_modules()
    last_day = started_at.date()
    next_scan = _next_scan_boundary(started_at)
    next_monitor = started_at
    next_cache = started_at
    next_health = started_at + timedelta(seconds=HEALTH_SECONDS)
    next_optional = started_at + timedelta(seconds=SCAN_SECONDS)
    LOGGER.info("SHIVAY AI scheduler started in Asia/Kolkata")

    try:
        while True:
            now = _now()
            if now.date() != last_day:
                clear_signals()
                completed = {item for item in completed if item[1] >= now.date() - timedelta(days=1)}
                last_day = now.date()

            if _due(now, PREOPEN_TIME, completed, "morning") and now.time() < MARKET_OPEN:
                await _morning_job(application)
                completed.add(("morning", now.date()))

            for outlook_name, outlook_time, outlook_label in (
                ("outlook_0100", OUTLOOK_OVERNIGHT_TIME, "01:00 IST OVERNIGHT UPDATE"),
                ("outlook_0800", OUTLOOK_PREOPEN_TIME, "08:00 IST FINAL PRE-MARKET UPDATE"),
                ("outlook_2200", OUTLOOK_EVENING_TIME, "22:00 IST EVENING UPDATE"),
            ):
                if _due(now, outlook_time, completed, outlook_name):
                    await _next_session_outlook_job(application, outlook_label)
                    completed.add((outlook_name, now.date()))

            if _market_open(now):
                if now >= next_cache:
                    await _safe_call("market cache refresh", force_refresh)
                    next_cache = now + timedelta(seconds=CACHE_SECONDS)
                if now >= next_scan:
                    await _scan_job(application)
                    next_scan = _next_scan_boundary(_now())
                if now >= next_monitor:
                    await _monitor_job(application)
                    next_monitor = _now() + timedelta(seconds=MONITOR_SECONDS)

            if optional_modules and _mcx_open(now) and now >= next_optional:
                await _optional_market_job(application, optional_modules)
                next_optional = _now() + timedelta(seconds=SCAN_SECONDS)

            if _due(now, EOD_TIME, completed, "eod"):
                await _eod_job(application)
                completed.add(("eod", now.date()))

            if _due(now, OVERNIGHT_TIME, completed, "overnight"):
                await _overnight_job(application)
                completed.add(("overnight", now.date()))

            if bool(_cfg("ENABLE_NIGHT_REPORT_REFRESH", False)) and _due(now, LATE_EVENING_TIME, completed, "late_evening"):
                await _late_update_job(application, "Late-evening GIFT NIFTY update")
                completed.add(("late_evening", now.date()))

            if bool(_cfg("ENABLE_NIGHT_REPORT_REFRESH", False)) and _due(now, LATE_NIGHT_TIME, completed, "late_night"):
                await _late_update_job(application, "Late-night next-session update")
                completed.add(("late_night", now.date()))

            if now >= next_health:
                await _health_job()
                await _provider_health_job(application)
                next_health = _now() + timedelta(seconds=HEALTH_SECONDS)

            await asyncio.sleep(5)
    except asyncio.CancelledError:
        LOGGER.info("SHIVAY AI scheduler stopped")
        raise
    except Exception:
        LOGGER.exception("Scheduler stopped after an unexpected loop failure; supervisor will restart it")
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(10)
    finally:
        async with _INSTANCE_LOCK:
            _RUNNING_APPLICATIONS.discard(application_id)
