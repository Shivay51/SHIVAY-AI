"""Startup and lifecycle management for SHIVAY AI.

This module performs infrastructure validation and supervision only.  Trading
decisions remain in the scanner, strategy, and validation modules.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
import logging.handlers
import os
import platform
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


IST = ZoneInfo("Asia/Kolkata")
PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIRECTORY = PROJECT_ROOT / "logs"
LOCK_FILE = PROJECT_ROOT / ".shivay_ai.lock"
MINIMUM_PYTHON = (3, 10)
MAX_NETWORK_ATTEMPTS = 3
NETWORK_TIMEOUT = 6.0


class StartupState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


NOT_STARTED = StartupState.NOT_STARTED
STARTING = StartupState.STARTING
READY = StartupState.READY
DEGRADED = StartupState.DEGRADED
STOPPING = StartupState.STOPPING
STOPPED = StartupState.STOPPED
FAILED = StartupState.FAILED


@dataclass
class LifecycleStatus:
    state: StartupState = StartupState.NOT_STARTED
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    last_market_refresh: datetime | None = None
    last_scan: datetime | None = None
    telegram_ready: bool = False
    scheduler_ready: bool = False
    scanner_ready: bool = False
    market_cache_ready: bool = False
    trade_monitor_ready: bool = False
    internet_ready: bool = False
    initialized_modules: set[str] = field(default_factory=set)
    optional_unavailable: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


_status = LifecycleStatus()
_lifecycle_lock: asyncio.Lock | None = None
_lifecycle_loop: asyncio.AbstractEventLoop | None = None
_background_tasks: set[asyncio.Task[Any]] = set()
_scheduler_tasks: dict[int, asyncio.Task[Any]] = {}
_application: Any = None
_instance_handle: Any = None
_instance_guard = threading.Lock()
_logging_ready = False


CRITICAL_MODULES = (
    "telegram",
    "telegram.ext",
    "dotenv",
    "config",
    "commands",
    "scheduler",
    "scanner",
    "data",
    "trade_monitor",
    "signal_memory",
    "telegram_service",
)

PROJECT_MODULES = (
    "market_cache",
    "market_brain",
    "market_prediction",
    "market_sentiment",
    "market_strength",
    "confidence_engine",
    "trade_validator",
    "entry_optimizer",
    "exit_optimizer",
    "risk_manager",
    "signal_ranker",
    "gift_nifty",
    "gift_nifty_prediction",
    "performance",
    "performance_report",
    "morning_prediction",
    "overnight_analysis",
    "data_quality",
    "instrument_master",
    "provider_cache",
    "provider_health",
    "provider_failover",
      "provider_manager",
      "truedata_client",
      "truedata_provider",
      "gdfl_client",
      "gdfl_provider",
      "tradingview_payload",
      "tradingview_security",
      "tradingview_cache",
      "tradingview_bridge",
      "tradingview_provider",
      "tradingview_webhook",
      "dhan_provider",
    "shoonya_client",
    "shoonya_provider",
    "entry_validity",
    "audience_router",
    "trade_journal",
    "accuracy_analyzer",
    "failure_analyzer",
    "strategy_tuner",
    "daily_review",
    "index_outlook",
    "market_data_provider",
    "market_hub_client",
    "market_hub_probe",
)

OPTIONAL_MODULES = (
    "autoscan",
    "mcx",
    "mcx_scanner",
    "commodity_scanner",
    "gold_silver",
    "metal_scanner",
    "gold",
    "silver",
)


class StartupError(RuntimeError):
    """Raised when a critical startup requirement is not satisfied."""


class TemporaryNetworkError(RuntimeError):
    """Sanitized temporary network failure."""


class _SecretFilter(logging.Filter):
    _token_pattern = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
    _secret_assignment = re.compile(
        r"(?i)\b(token|secret|password|passwd|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret)\s*[:=]\s*([^\s,;]+)"
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        secrets = [
            os.getenv("BOT_TOKEN"),
            os.getenv("TELEGRAM_BOT_TOKEN"),
            os.getenv("CHAT_ID"),
            os.getenv("SHOONYA_USER_ID"),
            os.getenv("SHOONYA_PASSWORD"),
            os.getenv("SHOONYA_VENDOR_CODE"),
            os.getenv("SHOONYA_API_KEY"),
            os.getenv("SHOONYA_IMEI"),
            os.getenv("SHOONYA_TOTP_SECRET"),
            os.getenv("SHOONYA_SESSION_TOKEN"),
        ]
        sensitive_markers = (
            "TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY",
            "ACCESS_KEY", "PRIVATE_KEY", "CLIENT_SECRET",
        )
        secrets.extend(
            value
            for name, value in os.environ.items()
            if value and any(marker in name.upper() for marker in sensitive_markers)
        )
        for secret in secrets:
            if secret:
                message = message.replace(str(secret), "[REDACTED]")
        message = self._token_pattern.sub("[REDACTED_TOKEN]", message)
        message = self._secret_assignment.sub(
            lambda match: f"{match.group(1)}=[REDACTED]", message
        )
        record.msg = message
        record.args = ()
        return True


LOGGER = logging.getLogger("shivay.startup")


def _now() -> datetime:
    return datetime.now(IST)


def _get_lock() -> asyncio.Lock:
    global _lifecycle_lock
    global _lifecycle_loop
    loop = asyncio.get_running_loop()
    if _lifecycle_lock is None or _lifecycle_loop is not loop:
        _lifecycle_lock = asyncio.Lock()
        _lifecycle_loop = loop
    return _lifecycle_lock


def _set_state(state: StartupState) -> None:
    _status.state = state
    LOGGER.info("Lifecycle state: %s", state.value)


def _warning(message: str) -> None:
    if message not in _status.warnings:
        _status.warnings.append(message)
    LOGGER.warning("%s", message)


def _configure_logging() -> None:
    global _logging_ready
    if _logging_ready:
        return
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    secret_filter = _SecretFilter()
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(getattr(_load_config(), "LOG_LEVEL", "INFO")).upper(), logging.INFO))
    if not any(
        getattr(handler, "_shivay_handler", False)
        or getattr(handler, "_shivay_managed", False)
        for handler in root.handlers
    ):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(secret_filter)
        console._shivay_handler = True  # type: ignore[attr-defined]
        root.addHandler(console)
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                LOG_DIRECTORY / "shivay_ai.log",
                maxBytes=2_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(secret_filter)
            file_handler._shivay_handler = True  # type: ignore[attr-defined]
            root.addHandler(file_handler)
        except OSError as error:
            LOGGER.warning("File logging unavailable: %s", type(error).__name__)
    _logging_ready = True


def _load_config() -> Any:
    config_module = importlib.import_module("config")
    if bool(getattr(config_module, "ENABLE_LIVE_ORDER_PLACEMENT", False)):
        raise StartupError("Unsafe configuration: live order placement must remain disabled")
    if not bool(getattr(config_module, "SIGNALS_ONLY", True)):
        raise StartupError("Unsafe configuration: signals-only mode must remain enabled")
    return config_module


def _validate_python() -> None:
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(map(str, MINIMUM_PYTHON))
        raise StartupError(f"Python {required} or newer is required")


def _load_environment() -> str:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise StartupError("Required environment variable BOT_TOKEN is missing")
    token = token.strip()
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", token):
        raise StartupError("BOT_TOKEN has an invalid format")
    return token


def _validate_admin(config_module: Any) -> set[int]:
    raw_values: list[Any] = []
    if hasattr(config_module, "ADMIN_ID"):
        raw_values.append(getattr(config_module, "ADMIN_ID"))
    configured = getattr(config_module, "ADMIN_IDS", ())
    if isinstance(configured, (str, int)):
        configured = [configured]
    raw_values.extend(configured)
    admin_ids = set()
    for value in raw_values:
        try:
            admin_id = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 < admin_id <= 10**15:
            admin_ids.add(admin_id)
    if not admin_ids:
        raise StartupError("No valid administrator ID is configured")
    return admin_ids


def _validate_modules() -> None:
    missing = [name for name in CRITICAL_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        raise StartupError("Missing required Python modules: " + ", ".join(missing))
    for name in PROJECT_MODULES:
        if importlib.util.find_spec(name) is None:
            raise StartupError(f"Required project module is missing: {name}")
    _status.optional_unavailable = {
        name for name in OPTIONAL_MODULES if importlib.util.find_spec(name) is None
    }


def _validate_storage() -> None:
    if not PROJECT_ROOT.is_dir():
        raise StartupError("Project directory is unavailable")
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for directory in (PROJECT_ROOT, LOG_DIRECTORY):
        try:
            with tempfile.NamedTemporaryFile(prefix=".shivay-write-", dir=directory, delete=True):
                pass
        except OSError as error:
            raise StartupError(f"Storage is not writable: {directory.name}") from error
    for filename in ("users.json", "chat_ids.json"):
        path = PROJECT_ROOT / filename
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    json.load(handle)
            except (OSError, json.JSONDecodeError) as error:
                raise StartupError(f"Invalid storage file: {filename}") from error


def _acquire_instance_lock() -> None:
    global _instance_handle
    with _instance_guard:
        if _instance_handle is not None:
            return
        handle = LOCK_FILE.open("a+b")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii"))
            handle.flush()
            _instance_handle = handle
        except (OSError, BlockingIOError) as error:
            handle.close()
            raise StartupError("Another SHIVAY AI instance is already running") from error


def _release_instance_lock() -> None:
    global _instance_handle
    with _instance_guard:
        handle = _instance_handle
        _instance_handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # Windows always releases the byte-range lock when the handle is
            # closed; some runtimes still reject an explicit LK_UNLCK after a
            # truncate/write cycle.
            LOGGER.debug("Explicit instance unlock deferred to handle close")
        finally:
            handle.close()


def _internet_probe() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=NETWORK_TIMEOUT):
            return True
    except OSError as error:
        raise TemporaryNetworkError("Internet connectivity is unavailable") from error


def _telegram_probe(token: str) -> bool:
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/getMe",
        headers={"User-Agent": "SHIVAY-AI/startup"},
    )
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
            payload = json.loads(response.read(32_768).decode("utf-8"))
            return response.status == 200 and payload.get("ok") is True
    except urllib.error.HTTPError as error:
        if error.code in (401, 404):
            raise StartupError("Telegram rejected BOT_TOKEN") from None
        raise TemporaryNetworkError("Telegram service is temporarily unavailable") from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise TemporaryNetworkError("Telegram validation could not reach the service") from None


async def _retry_network(
    label: str,
    operation: Callable[[], Any],
    attempts: int = MAX_NETWORK_ATTEMPTS,
) -> Any:
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(operation)
        except StartupError:
            raise
        except TemporaryNetworkError:
            if attempt >= attempts:
                raise StartupError(f"{label} failed after {attempts} attempts") from None
            LOGGER.warning("%s attempt %s/%s failed; retrying", label, attempt, attempts)
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 4.0)
    raise StartupError(f"{label} failed")


async def _call(function: Callable[..., Any], *args: Any) -> Any:
    if inspect.iscoroutinefunction(function):
        return await function(*args)
    return await asyncio.to_thread(function, *args)


def _import_required(name: str) -> Any:
    try:
        module = importlib.import_module(name)
    except Exception as error:
        raise StartupError(f"Required module failed to initialize: {name}") from error
    _status.initialized_modules.add(name)
    return module


def _warm_indicators() -> None:
    indicators = _import_required("indicators")
    close = [100.0 + index * 0.2 for index in range(240)]
    high = [value + 1.0 for value in close]
    low = [value - 1.0 for value in close]
    volume = [100_000.0 + index * 100.0 for index in range(240)]
    values = (
        indicators.ema20(close),
        indicators.ema50(close),
        indicators.ema200(close),
        indicators.atr(high, low, close),
        indicators.adx(high, low, close),
        indicators.vwap(high, low, close, volume),
    )
    if not all(value is not None for value in values):
        raise RuntimeError("Indicator warm-up returned incomplete results")


def _refresh_market_cache() -> bool:
    data_module = _import_required("data")
    data_module.force_refresh()
    size = int(data_module.cache_size())
    if size <= 0:
        return False
    _status.last_market_refresh = _now()
    return True


async def _warm_market_data() -> bool:
    delay = 1.0
    for attempt in range(1, MAX_NETWORK_ATTEMPTS + 1):
        try:
            if await asyncio.to_thread(_refresh_market_cache):
                return True
        except Exception:
            LOGGER.warning("Market cache warm-up attempt %s failed", attempt)
        if attempt < MAX_NETWORK_ATTEMPTS:
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 4.0)
    return False


def _initialize_project_modules() -> None:
    for name in PROJECT_MODULES:
        _import_required(name)
    scanner_module = _import_required("scanner")
    monitor_module = _import_required("trade_monitor")
    signal_module = _import_required("signal_memory")
    performance_module = _import_required("performance")
    telegram_module = _import_required("telegram_service")
    if not callable(getattr(scanner_module, "scan_market", None)):
        raise StartupError("Scanner interface is unavailable")
    if not callable(getattr(monitor_module, "check_trades", None)):
        raise StartupError("Trade-monitor interface is unavailable")
    if not callable(getattr(signal_module, "signal_exists", None)):
        raise StartupError("Signal-memory interface is unavailable")
    if not callable(getattr(performance_module, "get_report", None)):
        raise StartupError("Performance interface is unavailable")
    if not callable(getattr(telegram_module, "send_buy_signal", None)):
        raise StartupError("Telegram-service interface is unavailable")
    _status.scanner_ready = True
    _status.trade_monitor_ready = True


def _initialize_optional_modules() -> None:
    for name in OPTIONAL_MODULES:
        if name in _status.optional_unavailable:
            continue
        try:
            importlib.import_module(name)
            _status.initialized_modules.add(name)
        except Exception:
            _status.optional_unavailable.add(name)
            _warning(f"Optional module unavailable: {name}")


def _existing_command_names(application: Any) -> set[str]:
    names: set[str] = set()
    for handlers in getattr(application, "handlers", {}).values():
        for handler in handlers:
            commands = getattr(handler, "commands", None)
            if commands:
                names.update(str(command).lower() for command in commands)
    return names


def _initialize_command_handlers(application: Any) -> None:
    if application is None:
        return
    from telegram.ext import CommandHandler

    commands_module = _import_required("commands")
    mapping = {
        "start": "start", "help": "help_command", "status": "status", "scan": "scan",
        "id": "id_command", "adduser": "adduser", "removeuser": "removeuser",
        "listusers": "listusers", "market": "market", "giftnifty": "giftnifty",
        "prediction": "prediction", "buy": "buy", "sell": "sell", "trades": "trades",
        "open": "open", "closed": "closed", "report": "report",
        "performance": "performance", "gold": "gold", "silver": "silver",
        "datastatus": "datastatus", "ping": "ping", "version": "version", "startbot": "startbot",
        "stopbot": "stopbot", "restart": "restart",
        "marketdetails": "marketdetails", "predictiondetails": "predictiondetails",
        "golddetails": "golddetails", "silverdetails": "silverdetails",
        "provider": "provider", "systemhealth": "systemhealth",
    }
    existing = _existing_command_names(application)
    for command, function_name in mapping.items():
        function = getattr(commands_module, function_name, None)
        if command not in existing and callable(function):
            application.add_handler(CommandHandler(command, function))


def _track_task(task: asyncio.Task[Any], label: str) -> asyncio.Task[Any]:
    _background_tasks.add(task)
    task.set_name(label)

    def completed(done: asyncio.Task[Any]) -> None:
        _background_tasks.discard(done)
        for key, value in list(_scheduler_tasks.items()):
            if value is done:
                _scheduler_tasks.pop(key, None)
        if done.cancelled():
            return
        try:
            error = done.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            LOGGER.error("Background task %s stopped unexpectedly: %s", label, type(error).__name__)
            if label.startswith("shivay-scheduler") and _status.state in (StartupState.READY, StartupState.DEGRADED):
                _status.scheduler_ready = False
                _set_state(StartupState.DEGRADED)

    task.add_done_callback(completed)
    return task


def _initialize_scheduler(application: Any) -> None:
    if application is None:
        _status.scheduler_ready = False
        return
    application_id = id(application)
    existing = _scheduler_tasks.get(application_id)
    if existing is not None and not existing.done():
        _status.scheduler_ready = True
        return
    scheduler_module = _import_required("scheduler")
    scheduler_function = getattr(scheduler_module, "scheduler", None)
    if not inspect.iscoroutinefunction(scheduler_function):
        raise StartupError("Scheduler interface is unavailable")
    task = _track_task(
        asyncio.create_task(scheduler_function(application)),
        f"shivay-scheduler-{application_id}",
    )
    _scheduler_tasks[application_id] = task
    _status.scheduler_ready = True


async def _validate_telegram(application: Any, token: str) -> None:
    if application is not None:
        try:
            await asyncio.wait_for(application.bot.get_me(), timeout=NETWORK_TIMEOUT)
            _status.telegram_ready = True
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning("Application Telegram check failed; using direct validation")
    await _retry_network("Telegram validation", lambda: _telegram_probe(token))
    _status.telegram_ready = True


def _startup_summary() -> None:
    optional = ", ".join(sorted(_status.optional_unavailable)) or "none"
    LOGGER.info("=" * 56)
    LOGGER.info("SHIVAY AI startup summary")
    LOGGER.info("State: %s", _status.state.value)
    LOGGER.info("Python: %s", platform.python_version())
    LOGGER.info("Timezone: Asia/Kolkata")
    LOGGER.info("Telegram: %s", "ready" if _status.telegram_ready else "unavailable")
    LOGGER.info("Scheduler: %s", "ready" if _status.scheduler_ready else "not attached")
    LOGGER.info("Scanner: %s", "ready" if _status.scanner_ready else "unavailable")
    LOGGER.info("Market cache: %s", "ready" if _status.market_cache_ready else "degraded")
    LOGGER.info("Trade monitor: %s", "ready" if _status.trade_monitor_ready else "unavailable")
    LOGGER.info("Optional modules unavailable: %s", optional)
    LOGGER.info("=" * 56)


async def initialize(application: Any = None) -> dict[str, Any]:
    """Validate and initialize SHIVAY AI without generating trade signals."""
    global _application
    lock = _get_lock()
    async with lock:
        if _status.state in (StartupState.READY, StartupState.DEGRADED):
            if application is not None:
                _application = application
                _initialize_command_handlers(application)
                _initialize_scheduler(application)
            return await health_check(application, probe_network=False)
        if _status.state == StartupState.STARTING:
            return await health_check(application, probe_network=False)

        _status.warnings.clear()
        _status.initialized_modules.clear()
        _status.optional_unavailable.clear()
        _set_state(StartupState.STARTING)
        try:
            _validate_python()
            token = _load_environment()
            _configure_logging()
            config_module = _load_config()
            _validate_admin(config_module)
            _validate_modules()
            _validate_storage()
            _acquire_instance_lock()

            await _retry_network("Internet check", _internet_probe)
            _status.internet_ready = True
            await _validate_telegram(application, token)

            await asyncio.to_thread(_initialize_project_modules)
            try:
                await asyncio.to_thread(_warm_indicators)
            except Exception:
                _warning("Indicator warm-up was skipped after a safe validation failure")

            _status.market_cache_ready = await _warm_market_data()
            if not _status.market_cache_ready:
                _warning("Market data cache is not warm; background recovery will continue")

            await asyncio.to_thread(_initialize_optional_modules)
            _application = application
            _initialize_command_handlers(application)
            _initialize_scheduler(application)

            _status.started_at = _now()
            _status.stopped_at = None
            final_state = StartupState.READY if _status.market_cache_ready else StartupState.DEGRADED
            _set_state(final_state)
            _startup_summary()
            return await health_check(application, probe_network=False)
        except asyncio.CancelledError:
            _set_state(StartupState.FAILED)
            _release_instance_lock()
            raise
        except Exception as error:
            _set_state(StartupState.FAILED)
            LOGGER.error("Startup failed safely: %s", str(error))
            await _cancel_background_tasks()
            _release_instance_lock()
            if isinstance(error, StartupError):
                raise
            raise StartupError("SHIVAY AI initialization failed") from error


async def startup(application: Any = None) -> dict[str, Any]:
    """Idempotently initialize and start supervised SHIVAY AI services."""
    return await initialize(application)


async def _cancel_background_tasks() -> None:
    current = asyncio.current_task()
    tasks = [task for task in list(_background_tasks) if task is not current and not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background_tasks.difference_update(tasks)
    _scheduler_tasks.clear()
    _status.scheduler_ready = False


async def shutdown(application: Any = None) -> dict[str, Any]:
    """Gracefully stop lifecycle-owned tasks and release process resources."""
    global _application
    lock = _get_lock()
    async with lock:
        if _status.state in (StartupState.NOT_STARTED, StartupState.STOPPED):
            _release_instance_lock()
            _set_state(StartupState.STOPPED)
            return _health_snapshot(False)
        _set_state(StartupState.STOPPING)
        await _cancel_background_tasks()
        try:
            provider_manager = importlib.import_module("provider_manager")
            await asyncio.to_thread(provider_manager.get_provider_manager().close)
        except Exception:
            LOGGER.warning("Market provider shutdown recovered safely")
        _release_instance_lock()
        _application = None
        _status.telegram_ready = False
        _status.internet_ready = False
        _status.stopped_at = _now()
        _set_state(StartupState.STOPPED)
        LOGGER.info("SHIVAY AI shutdown complete")
        return _health_snapshot(False)


async def restart(application: Any = None) -> dict[str, Any]:
    """Perform a bounded, clean lifecycle restart."""
    target = application if application is not None else _application
    await shutdown(target)
    return await startup(target)


def _last_refresh_from_cache() -> datetime | None:
    try:
        data_module = importlib.import_module("data")
        age = float(data_module.cache_age())
        if age >= 99_999:
            return None
        return _now() - timedelta(seconds=max(age, 0.0))
    except Exception:
        return _status.last_market_refresh


def _health_snapshot(internet_ready: bool | None = None) -> dict[str, Any]:
    if internet_ready is None:
        internet_ready = _status.internet_ready
    try:
        monitor = importlib.import_module("trade_monitor")
        active_trades = sum(
            1 for trade in monitor.get_all_trades().values() if not trade.get("closed", False)
        )
    except Exception:
        active_trades = 0
    tasks = {
        task.get_name(): ("running" if not task.done() else "done")
        for task in list(_background_tasks)
    }
    scheduler_running = any(
        not task.done() for task in _scheduler_tasks.values()
    )
    scheduler_ready = _status.scheduler_ready and scheduler_running
    if _application is None and _status.state in (StartupState.READY, StartupState.DEGRADED):
        scheduler_ready = False
    last_refresh = _last_refresh_from_cache()
    if last_refresh is not None:
        _status.last_market_refresh = last_refresh
    try:
        provider_status = importlib.import_module("provider_manager").get_provider_status()
        provider_health = importlib.import_module("provider_health").provider_health_summary()
    except Exception:
        provider_status, provider_health = {"selected_primary": None}, {"healthy": 0, "degraded": 1}
    return {
        "state": _status.state.value,
        "healthy": _status.state == StartupState.READY,
        "degraded": _status.state == StartupState.DEGRADED,
        "telegram_ready": _status.telegram_ready,
        "scheduler_ready": scheduler_ready,
        "scanner_ready": _status.scanner_ready,
        "market_cache_ready": _status.market_cache_ready,
        "provider_ready": bool(provider_status.get("selected_primary")),
        "provider_status": provider_status,
        "provider_health": provider_health,
        "trade_monitor_ready": _status.trade_monitor_ready,
        "internet_ready": bool(internet_ready),
        "last_successful_market_data_refresh": last_refresh.isoformat() if last_refresh else None,
        "last_successful_scan": _status.last_scan.isoformat() if _status.last_scan else None,
        "active_trade_count": active_trades,
        "background_task_status": tasks,
        "optional_modules_unavailable": sorted(_status.optional_unavailable),
        "warnings": list(_status.warnings),
        "started_at": _status.started_at.isoformat() if _status.started_at else None,
        "checked_at": _now().isoformat(),
    }


async def health_check(
    application: Any = None,
    probe_network: bool = True,
) -> dict[str, Any]:
    """Return a non-secret operational health report."""
    internet_ready = _status.internet_ready
    if probe_network:
        try:
            internet_ready = bool(await asyncio.to_thread(_internet_probe))
        except TemporaryNetworkError:
            internet_ready = False
    if application is not None and _status.telegram_ready:
        try:
            await asyncio.wait_for(application.bot.get_me(), timeout=NETWORK_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception:
            _status.telegram_ready = False
    return _health_snapshot(internet_ready)


def get_state() -> StartupState:
    """Return the current lifecycle state without performing I/O."""
    return _status.state
