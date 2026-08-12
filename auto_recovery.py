"""Bounded, non-destructive automatic recovery for SHIVAY AI services."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import threading
import time
from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import config


LOGGER = logging.getLogger("shivay.auto_recovery")
IST = ZoneInfo("Asia/Kolkata")
CHECK_INTERVAL = max(20, int(getattr(config, "RECOVERY_CHECK_INTERVAL", 60)))
MAX_ATTEMPTS = max(1, min(int(getattr(config, "RECOVERY_MAX_ATTEMPTS", 3)), 6))
BASE_BACKOFF = max(1.0, min(float(getattr(config, "RECOVERY_BASE_BACKOFF", 2.0)), 30.0))
MAX_BACKOFF = max(BASE_BACKOFF, min(float(getattr(config, "RECOVERY_MAX_BACKOFF", 60.0)), 300.0))
RESTART_WINDOW = max(300.0, float(getattr(config, "RECOVERY_RESTART_WINDOW", 900.0)))
CACHE_STALE_SECONDS = max(120.0, float(getattr(config, "RECOVERY_CACHE_STALE_SECONDS", 600.0)))
NOTIFICATION_COOLDOWN = max(300.0, float(getattr(config, "RECOVERY_NOTIFICATION_COOLDOWN", 900.0)))
ALERT_AFTER_SECONDS = max(60.0, float(getattr(config, "RECOVERY_ALERT_AFTER_SECONDS", 600.0)))

_STATE_LOCK = threading.RLock()
_RECOVERY_LOCKS: dict[str, asyncio.Lock] = {}
_TASKS: dict[int, asyncio.Task[Any]] = {}
_application: Any = None
_stopping = False
_component_state: dict[str, dict[str, Any]] = {}
_last_health: dict[str, Any] = {}
_last_notification: dict[str, float] = {}


def _now() -> datetime:
    return datetime.now(IST)


def _state(component: str) -> dict[str, Any]:
    with _STATE_LOCK:
        return _component_state.setdefault(
            component,
            {
                "attempts": 0,
                "failures": 0,
                "recoveries": 0,
                "status": "UNKNOWN",
                "last_failure": None,
                "first_failure_monotonic": None,
                "last_success": None,
                "last_error": None,
                "restart_times": [],
            },
        )


def _safe_error(error: BaseException) -> str:
    return type(error).__name__


def _record_success(component: str) -> None:
    state = _state(component)
    with _STATE_LOCK:
        state["attempts"] = 0
        state["status"] = "HEALTHY"
        state["last_success"] = _now().isoformat()
        state["last_error"] = None
        state["first_failure_monotonic"] = None


def report_component_failure(component: str, error: BaseException | str | None = None) -> None:
    """Allow scheduler/data callers to report a failure without exposing details."""
    name = str(component or "unknown").strip().lower()[:48]
    state = _state(name)
    with _STATE_LOCK:
        state["failures"] += 1
        if state.get("first_failure_monotonic") is None:
            state["first_failure_monotonic"] = time.monotonic()
        state["status"] = "FAILED"
        state["last_failure"] = _now().isoformat()
        state["last_error"] = _safe_error(error) if isinstance(error, BaseException) else "reported_failure"


def _trim_restart_window(component: str) -> list[float]:
    now = time.monotonic()
    state = _state(component)
    with _STATE_LOCK:
        values = [stamp for stamp in state["restart_times"] if stamp > now - RESTART_WINDOW]
        state["restart_times"] = values
        return values


def _restart_allowed(component: str) -> bool:
    return len(_trim_restart_window(component)) < MAX_ATTEMPTS


async def _notify_admins(application: Any, component: str, message: str) -> None:
    if not bool(getattr(config, "ENABLE_RECOVERY_ALERTS", False)):
        return
    now = time.monotonic()
    with _STATE_LOCK:
        state = _state(component)
        started = state.get("first_failure_monotonic")
        if not isinstance(started, (int, float)) or now - started < ALERT_AFTER_SECONDS:
            return
        if now - _last_notification.get(component, 0.0) < NOTIFICATION_COOLDOWN:
            return
        _last_notification[component] = now
    try:
        admin_module = importlib.import_module("admin")
        function = getattr(admin_module, "notify_admins", None)
        if inspect.iscoroutinefunction(function):
            duration = max(1, round((time.monotonic() - started) / 60)) if isinstance(started, (int, float)) else 1
            problem = component.replace("_", " ").title()
            await function(
                application,
                "⚠️ SHIVAY AI SYSTEM ALERT\n\n"
                f"Problem: {problem} unavailable\n"
                f"Duration: {duration} minute{'s' if duration != 1 else ''}\n"
                "Status: Signals paused where affected\n"
                "Action: Automatic recovery continuing",
            )
    except Exception:
        LOGGER.warning("Admin recovery notification unavailable")


async def _check_telegram(application: Any) -> bool:
    if application is None or not hasattr(application, "bot"):
        return False
    try:
        await asyncio.wait_for(application.bot.get_me(), timeout=8.0)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


def _cache_health() -> tuple[bool, float | None, int]:
    try:
        module = importlib.import_module("data")
        age = float(module.cache_age())
        size = int(module.cache_size())
        return size > 0 and age <= CACHE_STALE_SECONDS, age, size
    except Exception:
        return False, None, 0


def _interfaces_health() -> dict[str, bool]:
    result = {}
    checks = {
        "scanner": ("scanner", "scan_market"),
        "trade_monitor": ("trade_monitor", "check_trades"),
        "scheduler_interface": ("scheduler", "scheduler"),
        "telegram_service": ("telegram_service", "send_buy_signal"),
    }
    for label, (module_name, function_name) in checks.items():
        try:
            module = importlib.import_module(module_name)
            result[label] = callable(getattr(module, function_name, None))
        except Exception:
            result[label] = False
    return result


async def check_system_health(application: Any = None) -> dict[str, Any]:
    """Return an honest, non-secret snapshot without running a market scan."""
    target = application if application is not None else _application
    telegram_ready, cache_result, interfaces = await asyncio.gather(
        _check_telegram(target),
        asyncio.to_thread(_cache_health),
        asyncio.to_thread(_interfaces_health),
    )
    cache_ready, cache_age, cache_size = cache_result
    startup_health: dict[str, Any] = {}
    try:
        startup_module = importlib.import_module("startup")
        function = getattr(startup_module, "health_check", None)
        if inspect.iscoroutinefunction(function):
            startup_health = await function(target, probe_network=False)
    except Exception:
        startup_health = {}
    scheduler_ready = bool(startup_health.get("scheduler_ready", False))
    try:
        provider_summary = importlib.import_module("provider_health").provider_health_summary()
    except Exception:
        provider_summary = {"healthy": 0, "degraded": 1, "providers": {}}
    provider_ready = provider_summary.get("degraded", 0) == 0 or provider_summary.get("healthy", 0) > 0
    background = startup_health.get("background_task_status", {})
    crashed_tasks = [name for name, status in background.items() if status != "running"] if isinstance(background, dict) else []
    healthy = (
        telegram_ready
        and cache_ready
        and interfaces.get("scanner", False)
        and interfaces.get("trade_monitor", False)
        and interfaces.get("scheduler_interface", False)
        and provider_ready
        and (scheduler_ready or target is None)
        and not crashed_tasks
    )
    snapshot = {
        "checked_at": _now().isoformat(),
        "healthy": healthy,
        "degraded": not healthy,
        "telegram_ready": telegram_ready,
        "scheduler_ready": scheduler_ready,
        "market_cache_ready": cache_ready,
        "market_cache_age_seconds": cache_age,
        "market_cache_size": cache_size,
        "scanner_ready": interfaces.get("scanner", False),
        "trade_monitor_ready": interfaces.get("trade_monitor", False),
        "telegram_service_ready": interfaces.get("telegram_service", False),
        "provider_ready": provider_ready,
        "provider_health": provider_summary,
        "crashed_background_tasks": crashed_tasks,
        "active_trade_count": startup_health.get("active_trade_count", 0),
    }
    with _STATE_LOCK:
        _last_health.clear()
        _last_health.update(snapshot)
    return snapshot


async def _recover_cache() -> bool:
    try:
        module = importlib.import_module("data")
        await asyncio.to_thread(module.force_refresh)
        ready, _, _ = await asyncio.to_thread(_cache_health)
        return ready
    except Exception:
        return False


async def _recover_provider() -> bool:
    """Reset only provider connections/caches; never touch active trade state."""
    try:
        manager = importlib.import_module("provider_manager")
        manager.reset_provider_manager()
        data_module = importlib.import_module("data")
        await asyncio.to_thread(data_module.force_refresh)
        ready, _, _ = await asyncio.to_thread(_cache_health)
        return ready
    except Exception:
        return False


async def _recover_scheduler(application: Any) -> bool:
    if application is None:
        return False
    try:
        startup_module = importlib.import_module("startup")
        initializer = getattr(startup_module, "initialize", None)
        if inspect.iscoroutinefunction(initializer):
            await initializer(application)
        health = await startup_module.health_check(application, probe_network=False)
        if health.get("scheduler_ready"):
            return True
        # Scheduler ownership remains exclusively in startup.py.  Creating a
        # second untracked task here could race with lifecycle recovery even
        # though scheduler.py has its own duplicate guard.
        return False
    except Exception:
        return False


async def _recover_telegram(application: Any) -> bool:
    return await _check_telegram(application)


async def _recover_interface(component: str) -> bool:
    mapping = {
        "scanner": ("scanner", "scan_market"),
        "trade_monitor": ("trade_monitor", "check_trades"),
        "telegram_service": ("telegram_service", "send_buy_signal"),
    }
    target = mapping.get(component)
    if target is None:
        return False
    try:
        module = importlib.import_module(target[0])
        return callable(getattr(module, target[1], None))
    except Exception:
        return False


async def recover_component(component: str, application: Any = None) -> bool:
    """Attempt bounded recovery of one non-critical component."""
    name = str(component or "").strip().lower()
    aliases = {"cache": "market_cache", "data": "market_cache", "scheduler_job": "scheduler"}
    name = aliases.get(name, name)
    target = application if application is not None else _application
    if name not in {"market_cache", "market_provider", "scheduler", "telegram", "scanner", "trade_monitor", "telegram_service"}:
        return False
    lock = _RECOVERY_LOCKS.setdefault(name, asyncio.Lock())
    if lock.locked() or not _restart_allowed(name):
        return False
    async with lock:
        state = _state(name)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if _stopping:
                return False
            with _STATE_LOCK:
                state["attempts"] = attempt
                state["status"] = "RECOVERING"
                state["restart_times"].append(time.monotonic())
            try:
                if name == "market_cache":
                    recovered = await _recover_cache()
                elif name == "market_provider":
                    recovered = await _recover_provider()
                elif name == "scheduler":
                    recovered = await _recover_scheduler(target)
                elif name == "telegram":
                    recovered = await _recover_telegram(target)
                else:
                    recovered = await _recover_interface(name)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                recovered = False
                state["last_error"] = _safe_error(error)
            if recovered:
                with _STATE_LOCK:
                    state["recoveries"] += 1
                _record_success(name)
                LOGGER.info("Component recovered automatically: %s", name)
                return True
            report_component_failure(name)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(min(BASE_BACKOFF * (2 ** (attempt - 1)), MAX_BACKOFF))
        with _STATE_LOCK:
            state["status"] = "DEGRADED"
        await _notify_admins(target, name, "recovery attempts exhausted; degraded mode active")
        return False


async def _recovery_loop(application: Any) -> None:
    while not _stopping:
        try:
            health = await check_system_health(application)
            failures = []
            if not health["telegram_ready"]:
                failures.append("telegram")
            if not health["market_cache_ready"]:
                failures.append("market_cache")
            if not health.get("provider_ready", False):
                failures.append("market_provider")
            if not health["scanner_ready"]:
                failures.append("scanner")
            if not health["trade_monitor_ready"]:
                failures.append("trade_monitor")
            if not health["scheduler_ready"]:
                failures.append("scheduler")
            for component in failures:
                await recover_component(component, application)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Auto-recovery health cycle failed safely")
        await asyncio.sleep(CHECK_INTERVAL)


async def start_auto_recovery(application: Any = None) -> asyncio.Task[Any]:
    """Idempotently start one recovery supervisor per Application."""
    global _application
    global _stopping
    _application = application if application is not None else _application
    _stopping = False
    key = id(_application) if _application is not None else 0
    existing = _TASKS.get(key)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(_recovery_loop(_application), name=f"shivay-auto-recovery-{key}")
    _TASKS[key] = task

    def completed(done: asyncio.Task[Any]) -> None:
        if _TASKS.get(key) is done:
            _TASKS.pop(key, None)
        if not done.cancelled():
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                LOGGER.error("Auto-recovery task stopped: %s", type(error).__name__)

    task.add_done_callback(completed)
    return task


async def stop_auto_recovery(application: Any = None) -> bool:
    """Stop recovery supervisors without touching active trade state."""
    global _stopping
    _stopping = True
    if application is None:
        tasks = list(_TASKS.values())
    else:
        task = _TASKS.get(id(application))
        tasks = [task] if task is not None else []
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for key, task in list(_TASKS.items()):
        if task in tasks:
            _TASKS.pop(key, None)
    return True


def get_recovery_status() -> dict[str, Any]:
    with _STATE_LOCK:
        return {
            "running_tasks": sum(not task.done() for task in _TASKS.values()),
            "stopping": _stopping,
            "degraded": any(state.get("status") == "DEGRADED" for state in _component_state.values()),
            "components": deepcopy(_component_state),
            "last_health": deepcopy(_last_health),
            "checked_at": _now().isoformat(),
        }


def reset_recovery_state() -> bool:
    """Reset counters only when no recovery operation is active."""
    if any(lock.locked() for lock in _RECOVERY_LOCKS.values()):
        return False
    with _STATE_LOCK:
        _component_state.clear()
        _last_health.clear()
        _last_notification.clear()
    return True
