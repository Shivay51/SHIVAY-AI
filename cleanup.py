"""Safe daily housekeeping for SHIVAY AI."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import config


LOGGER = logging.getLogger("shivay.cleanup")
PROJECT_ROOT = Path(__file__).resolve().parent
BACKUP_ROOT = PROJECT_ROOT / "maintenance_backups"
STATE_FILE = PROJECT_ROOT / "cleanup_state.json"
IST = ZoneInfo("Asia/Kolkata")
_LOCK = threading.Lock()
_SCHEDULER_TASK: asyncio.Task[Any] | None = None
_LAST_RESULT: dict[str, Any] = {
    "status": "NOT_RUN",
    "last_run": None,
    "running": False,
    "summary": {},
    "errors": [],
}


def _now() -> datetime:
    return datetime.now(IST)


def _inside_project(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise ValueError("Path is outside the project directory")
    return resolved


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(value: dict[str, Any]) -> None:
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".cleanup-", suffix=".tmp", dir=str(PROJECT_ROOT)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, STATE_FILE)
        temporary = ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _safe_age_days(path: Path, now: datetime) -> float:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
    return max(0.0, (now - modified).total_seconds() / 86400.0)


def cleanup_cache(max_age_seconds: int | None = None, force: bool = False) -> dict[str, Any]:
    """Invalidate stale in-memory caches without deleting market history."""
    maximum = max(60, int(max_age_seconds or getattr(config, "CACHE_TIME", 300) * 3))
    cleared: list[str] = []
    now_epoch = datetime.now().timestamp()
    candidates = {
        "market_cache": (("_market_cache", None), ("_last_update", 0)),
        "market_brain": (("_market_cache", None), ("_last_update", 0)),
        "market_prediction": (("_prediction_cache", None), ("_last_update", 0)),
        "market_sentiment": (("_sentiment_cache", None), ("_last_update", 0)),
        "market_strength": (("_strength_cache", None), ("_last_update", 0)),
    }
    for module_name, fields in candidates.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        timestamp = getattr(module, "_last_update", 0) or 0
        try:
            stale = force or not timestamp or now_epoch - float(timestamp) >= maximum
        except (TypeError, ValueError):
            stale = True
        if not stale:
            continue
        changed = False
        for field, reset_value in fields:
            if hasattr(module, field):
                setattr(module, field, reset_value)
                changed = True
        if changed:
            cleared.append(module_name)
    return {"cleared": len(cleared), "modules": cleared}


def cleanup_logs(retention_days: int | None = None) -> dict[str, Any]:
    """Compress old rotated logs while leaving every active log untouched."""
    logs = PROJECT_ROOT / "logs"
    if not logs.is_dir():
        return {"archived": 0, "bytes": 0}
    retention = max(1, int(retention_days or getattr(config, "LOG_RETENTION_DAYS", 30)))
    now = _now()
    archive = _inside_project(logs / "archive")
    archived = 0
    total_bytes = 0
    for path in logs.iterdir():
        if not path.is_file() or path.suffix == ".gz" or path.name.endswith(".log"):
            continue
        if ".log." not in path.name or _safe_age_days(path, now) < retention:
            continue
        archive.mkdir(parents=True, exist_ok=True)
        destination = _inside_project(archive / f"{path.name}.gz")
        if destination.exists():
            continue
        size = path.stat().st_size
        with path.open("rb") as source, gzip.open(destination, "xb", compresslevel=6) as target:
            shutil.copyfileobj(source, target)
        path.unlink()
        archived += 1
        total_bytes += size
    return {"archived": archived, "bytes": total_bytes}


def cleanup_signal_memory(force: bool = False) -> dict[str, Any]:
    """Reset duplicate-alert memory once per IST trading date."""
    now = _now()
    state = _load_state()
    last_date = state.get("signal_reset_date")
    should_reset = force or (last_date != now.date().isoformat() and now.hour < 2)
    if not should_reset:
        if last_date is None:
            state["signal_reset_date"] = now.date().isoformat()
            _save_state(state)
        return {"reset": False, "removed": 0}
    import signal_memory

    count = int(signal_memory.total_signals())
    signal_memory.clear_signals()
    state["signal_reset_date"] = now.date().isoformat()
    _save_state(state)
    return {"reset": True, "removed": count}


def cleanup_trade_state() -> dict[str, Any]:
    """Back up and remove only trades already marked closed."""
    import trade_monitor

    trades = trade_monitor.get_all_trades()
    completed = {
        str(symbol): dict(trade)
        for symbol, trade in list(trades.items())
        if isinstance(trade, dict) and bool(trade.get("closed"))
    }
    if not completed:
        return {"removed": 0, "active_preserved": len(trades)}
    backup_dir = _inside_project(BACKUP_ROOT / "trade_state")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%d_%H%M%S_%f")
    backup = _inside_project(backup_dir / f"completed_{stamp}.json")
    backup.write_text(json.dumps(completed, indent=2, default=str) + "\n", encoding="utf-8")
    for symbol in completed:
        trade_monitor.remove_trade(symbol)
    return {"removed": len(completed), "active_preserved": len(trades), "backup": backup.name}


def cleanup_temporary_files(max_age_hours: int | None = None) -> dict[str, Any]:
    """Move stale project temporary files into a recoverable quarantine."""
    hours = max(1, int(max_age_hours or getattr(config, "TEMP_RETENTION_HOURS", 24)))
    cutoff = _now() - timedelta(hours=hours)
    quarantine = _inside_project(BACKUP_ROOT / "temporary" / _now().strftime("%Y%m%d"))
    moved = 0
    for directory in (PROJECT_ROOT, PROJECT_ROOT / "logs", PROJECT_ROOT / "exports"):
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if not path.is_file() or path == STATE_FILE:
                continue
            if not (path.name.startswith(".") and path.suffix == ".tmp") and path.suffix.lower() != ".tmp":
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
            if modified > cutoff:
                continue
            _inside_project(path)
            quarantine.mkdir(parents=True, exist_ok=True)
            destination = _inside_project(quarantine / path.name)
            if destination.exists():
                destination = _inside_project(quarantine / f"{path.stem}_{path.stat().st_mtime_ns}{path.suffix}")
            shutil.move(str(path), str(destination))
            moved += 1
    return {"quarantined": moved}


def cleanup_reports(retention_days: int | None = None) -> dict[str, Any]:
    """Archive old generated reports; never touch canonical performance history."""
    reports = PROJECT_ROOT / "reports"
    if not reports.is_dir():
        return {"archived": 0}
    retention = max(7, int(retention_days or getattr(config, "REPORT_RETENTION_DAYS", 90)))
    archive = _inside_project(BACKUP_ROOT / "reports")
    archived = 0
    for path in reports.iterdir():
        if not path.is_file() or _safe_age_days(path, _now()) < retention:
            continue
        if "performance" in path.name.lower() or path.name.lower() in {"trades.json", "history.json"}:
            continue
        _inside_project(path)
        archive.mkdir(parents=True, exist_ok=True)
        destination = _inside_project(archive / path.name)
        if destination.exists():
            destination = _inside_project(archive / f"{path.stem}_{path.stat().st_mtime_ns}{path.suffix}")
        shutil.move(str(path), str(destination))
        archived += 1
    return {"archived": archived}


def _cleanup_expired_users() -> dict[str, Any]:
    """Remove expired users only when configuration explicitly authorizes it."""
    if not bool(getattr(config, "CLEANUP_EXPIRED_USERS", False)):
        return {"removed": 0, "enabled": False}
    actor = getattr(config, "CLEANUP_ADMIN_ID", None)
    if actor is None:
        return {"removed": 0, "enabled": True, "reason": "no authorized cleanup actor"}
    import admin

    if not admin.is_admin(actor):
        return {"removed": 0, "enabled": True, "reason": "cleanup actor is not an admin"}
    expired = admin.get_expired_users()
    removed = sum(bool(admin.remove_user(actor, user.get("id"))) for user in expired)
    return {"removed": removed, "enabled": True}


def run_cleanup(force: bool = False) -> dict[str, Any]:
    """Run one non-overlapping cleanup cycle and return a structured summary."""
    if not _LOCK.acquire(blocking=False):
        return {"status": "ALREADY_RUNNING", "last_run": _LAST_RESULT.get("last_run")}
    started = _now()
    _LAST_RESULT.update(status="RUNNING", running=True, errors=[])
    summary: dict[str, Any] = {}
    errors: list[str] = []
    operations = (
        ("cache", lambda: cleanup_cache(force=force)),
        ("logs", cleanup_logs),
        ("signals", lambda: cleanup_signal_memory(force=force)),
        ("trades", cleanup_trade_state),
        ("temporary", cleanup_temporary_files),
        ("reports", cleanup_reports),
        ("expired_users", _cleanup_expired_users),
    )
    try:
        for name, operation in operations:
            try:
                summary[name] = operation()
            except Exception as error:
                LOGGER.warning("Cleanup operation failed: %s (%s)", name, type(error).__name__)
                errors.append(f"{name}:{type(error).__name__}")
        finished = _now()
        result = {
            "status": "DEGRADED" if errors else "SUCCESS",
            "last_run": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "summary": summary,
            "errors": errors,
            "running": False,
        }
        _LAST_RESULT.clear()
        _LAST_RESULT.update(result)
        state = _load_state()
        state.update(last_cleanup=finished.isoformat(), last_status=result["status"])
        _save_state(state)
        return dict(result)
    finally:
        _LAST_RESULT["running"] = False
        _LOCK.release()


def get_cleanup_status() -> dict[str, Any]:
    return json.loads(json.dumps(_LAST_RESULT, default=str))


async def _cleanup_loop() -> None:
    hour = max(0, min(23, int(getattr(config, "CLEANUP_HOUR", 0))))
    minute = max(0, min(59, int(getattr(config, "CLEANUP_MINUTE", 10))))
    while True:
        try:
            now = _now()
            state = _load_state()
            ran_today = str(state.get("last_cleanup", ""))[:10] == now.date().isoformat()
            if not ran_today and (now.hour, now.minute) >= (hour, minute):
                await asyncio.to_thread(run_cleanup)
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning("Cleanup scheduler recovered from %s", type(error).__name__)
            await asyncio.sleep(60)


async def start_cleanup_scheduler() -> asyncio.Task[Any]:
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK is None or _SCHEDULER_TASK.done():
        _SCHEDULER_TASK = asyncio.create_task(_cleanup_loop(), name="shivay-cleanup")
    return _SCHEDULER_TASK


async def stop_cleanup_scheduler() -> None:
    global _SCHEDULER_TASK
    task = _SCHEDULER_TASK
    _SCHEDULER_TASK = None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


__all__ = [
    "run_cleanup",
    "cleanup_cache",
    "cleanup_logs",
    "cleanup_signal_memory",
    "cleanup_trade_state",
    "cleanup_temporary_files",
    "cleanup_reports",
    "get_cleanup_status",
    "start_cleanup_scheduler",
    "stop_cleanup_scheduler",
]
