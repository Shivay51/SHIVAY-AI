"""Secure administrator and user-access management for SHIVAY AI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from copy import deepcopy
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

import config


LOGGER = logging.getLogger("shivay.admin")
PROJECT_ROOT = Path(__file__).resolve().parent
USERS_FILE = PROJECT_ROOT / "users.json"
BACKUP_FILE = PROJECT_ROOT / "users.json.bak"
AUDIT_FILE = PROJECT_ROOT / "admin_audit.jsonl"
VALID_PLANS = {"BASIC", "PREMIUM", "VIP", "NSE_FO", "MCX", "ALL"}
MAX_TELEGRAM_ID = 10**15
_NAME_PATTERN = re.compile(r"^[\w .-]{1,40}$", re.UNICODE)
_STORAGE_LOCK = threading.RLock()
_pending_notifications: list[str] = []


DEFAULT_PLAN_PERMISSIONS = {
    "BASIC": frozenset({"access", "status", "market", "gift_nifty", "prediction"}),
    "PREMIUM": frozenset(
        {"access", "status", "market", "gift_nifty", "prediction", "manual_scan", "reports", "trades"}
    ),
    "VIP": frozenset(
        {"access", "status", "market", "gift_nifty", "prediction", "manual_scan", "reports", "trades", "commodities"}
    ),
    "NSE_FO": frozenset({"access", "status", "market", "gift_nifty", "prediction", "manual_scan", "reports", "trades"}),
    "MCX": frozenset({"access", "status", "market", "manual_scan", "reports", "trades", "commodities"}),
    "ALL": frozenset({"access", "status", "market", "gift_nifty", "prediction", "manual_scan", "reports", "trades", "commodities"}),
}
ADMIN_ONLY_PERMISSIONS = frozenset(
    {"admin", "user_management", "bot_start", "bot_stop", "restart", "audit"}
)


class AdminStorageError(RuntimeError):
    """Raised internally when user storage cannot be read safely."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _actor_id(value: Any) -> int | None:
    if value is None:
        return None
    effective_user = getattr(value, "effective_user", None)
    if effective_user is not None:
        value = getattr(effective_user, "id", None)
    elif hasattr(value, "id") and not isinstance(value, (str, bytes, int)):
        value = getattr(value, "id", None)
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if 0 < result <= MAX_TELEGRAM_ID else None


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
        admin_id = _actor_id(value)
        if admin_id is not None:
            result.add(admin_id)
    return result


def _valid_name(name: Any) -> str | None:
    value = str(name or "User").strip()
    return value if _NAME_PATTERN.fullmatch(value) else None


def _normalize_plan(plan: Any) -> str | None:
    value = str(plan or "BASIC").strip().upper()
    return value if value in VALID_PLANS else None


def _normalize_expiry(value: Any) -> str | None:
    if value in (None, "", "NONE", "NEVER"):
        return None
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time(23, 59, 59))
    elif isinstance(value, str):
        text = value.strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                parsed = datetime.combine(date.fromisoformat(text), time(23, 59, 59))
            else:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Expiry must be an ISO date or datetime") from error
    else:
        raise ValueError("Expiry must be a date, datetime, ISO string, or None")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _expiry_datetime(user: dict[str, Any]) -> datetime | None:
    value = user.get("expiry")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _is_expired(user: dict[str, Any], now: datetime | None = None) -> bool:
    expiry = _expiry_datetime(user)
    return expiry is not None and expiry < (now or _utc_now())


def _empty_database() -> dict[str, list[dict[str, Any]]]:
    return {"users": []}


def _validate_database(data: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(data, dict) or not isinstance(data.get("users"), list):
        raise AdminStorageError("User database has an invalid structure")
    validated: list[dict[str, Any]] = []
    seen: set[int] = set()
    for source in data["users"]:
        if not isinstance(source, dict):
            LOGGER.warning("Ignored a malformed user record")
            continue
        user_id = _actor_id(source.get("id"))
        if user_id is None or user_id in seen:
            LOGGER.warning("Ignored an invalid or duplicate user record")
            continue
        user = deepcopy(source)
        user["id"] = user_id
        user["name"] = _valid_name(user.get("name")) or "User"
        user["plan"] = _normalize_plan(user.get("plan")) or "BASIC"
        user["active"] = bool(user.get("active", False))
        expiry = user.get("expiry")
        if expiry:
            try:
                user["expiry"] = _normalize_expiry(expiry)
            except ValueError:
                user["expiry"] = "1970-01-01T00:00:00+00:00"
        seen.add(user_id)
        validated.append(user)
    result = deepcopy(data)
    result["users"] = validated
    return result


def _load_database(create: bool = True) -> dict[str, list[dict[str, Any]]]:
    with _STORAGE_LOCK:
        if not USERS_FILE.exists():
            if not create:
                return _empty_database()
            _atomic_write(_empty_database(), backup=False)
        try:
            with USERS_FILE.open("r", encoding="utf-8") as handle:
                return _validate_database(json.load(handle))
        except (OSError, json.JSONDecodeError, AdminStorageError) as error:
            LOGGER.error("User database could not be read safely: %s", type(error).__name__)
            raise AdminStorageError("User database is unavailable") from error


def _atomic_write(data: dict[str, Any], backup: bool = True) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".users-", suffix=".tmp", dir=str(USERS_FILE.parent)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            json.dump(data, handle, ensure_ascii=False, indent=4)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if backup and USERS_FILE.exists():
            shutil.copy2(USERS_FILE, BACKUP_FILE)
        os.replace(temporary_name, USERS_FILE)
        temporary_name = ""
    except OSError as error:
        LOGGER.error("Atomic user-database write failed: %s", type(error).__name__)
        raise AdminStorageError("Could not save the user database") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def _audit(action: str, actor: int, target: int | None, success: bool, reason: str = "") -> None:
    entry = {
        "timestamp": _utc_now().isoformat(),
        "action": str(action)[:48],
        "actor_id": actor,
        "target_id": target,
        "success": bool(success),
        "reason": re.sub(r"[\r\n]+", " ", str(reason))[:160],
    }
    try:
        with _STORAGE_LOCK:
            with AUDIT_FILE.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    except OSError as error:
        LOGGER.error("Admin audit write failed: %s", type(error).__name__)


def _notify(action: str, target: int | None = None) -> None:
    message = f"Admin action completed: {action}"
    if target is not None:
        message += f" (user {target})"
    with _STORAGE_LOCK:
        _pending_notifications.append(message[:300])
        del _pending_notifications[:-100]


def _admin_action_allowed(actor: Any, action: str, target: int | None = None) -> int | None:
    actor_id = _actor_id(actor)
    allowed = actor_id is not None and actor_id in _admin_ids()
    if not allowed:
        _audit(action, actor_id or 0, target, False, "unauthorized")
        LOGGER.warning("Unauthorized admin action rejected: %s", action)
        return None
    return actor_id


def is_admin(user_or_update: Any) -> bool:
    """Return whether a Telegram ID, user, or Update belongs to an admin."""
    user_id = _actor_id(user_or_update)
    return user_id is not None and user_id in _admin_ids()


def is_authorized_user(user_or_update: Any, permission: str = "access") -> bool:
    """Return whether a user currently has the requested permission."""
    return validate_user_access(user_or_update, permission)


def validate_user_access(user_or_update: Any, permission: str = "access") -> bool:
    user_id = _actor_id(user_or_update)
    if user_id is None:
        return False
    permission = str(permission or "access").strip().lower()
    if user_id in _admin_ids():
        return True
    if permission in ADMIN_ONLY_PERMISSIONS:
        return False
    try:
        database = _load_database()
    except AdminStorageError:
        return False
    user = next((item for item in database["users"] if item["id"] == user_id), None)
    if user is None or not user.get("active", False) or _is_expired(user):
        return False
    configured = getattr(config, "PLAN_PERMISSIONS", None)
    plan_permissions = DEFAULT_PLAN_PERMISSIONS
    if isinstance(configured, dict):
        plan_permissions = {
            plan: frozenset(str(item).lower() for item in permissions)
            for plan, permissions in configured.items()
            if isinstance(permissions, (list, tuple, set, frozenset))
        }
    allowed = plan_permissions.get(str(user.get("plan", "BASIC")).upper(), frozenset({"access"}))
    return permission in allowed or (permission == "access" and "access" in allowed)


def add_user(
    actor: Any,
    user_id: Any,
    name: str = "User",
    plan: str = "BASIC",
    expiry: Any = None,
) -> bool:
    target = _actor_id(user_id)
    actor_id = _admin_action_allowed(actor, "add_user", target)
    if actor_id is None or target is None:
        return False
    clean_name = _valid_name(name)
    clean_plan = _normalize_plan(plan)
    try:
        clean_expiry = _normalize_expiry(expiry)
    except ValueError:
        _audit("add_user", actor_id, target, False, "invalid expiry")
        return False
    if clean_name is None or clean_plan is None:
        _audit("add_user", actor_id, target, False, "invalid name or plan")
        return False
    try:
        with _STORAGE_LOCK:
            database = _load_database()
            if any(user["id"] == target for user in database["users"]):
                _audit("add_user", actor_id, target, False, "duplicate")
                return False
            now = _utc_now().isoformat()
            user = {
                "id": target,
                "name": clean_name,
                "plan": clean_plan,
                "active": True,
                "expiry": clean_expiry,
                "created_at": now,
                "updated_at": now,
            }
            database["users"].append(user)
            _atomic_write(database)
        _audit("add_user", actor_id, target, True)
        _notify("user added", target)
        return True
    except AdminStorageError:
        _audit("add_user", actor_id, target, False, "storage error")
        return False


def remove_user(actor: Any, user_id: Any) -> bool:
    target = _actor_id(user_id)
    actor_id = _admin_action_allowed(actor, "remove_user", target)
    if actor_id is None or target is None:
        return False
    if target in _admin_ids():
        _audit("remove_user", actor_id, target, False, "protected admin")
        return False
    try:
        with _STORAGE_LOCK:
            database = _load_database()
            original_count = len(database["users"])
            database["users"] = [user for user in database["users"] if user["id"] != target]
            if len(database["users"]) == original_count:
                _audit("remove_user", actor_id, target, False, "not found")
                return False
            _atomic_write(database)
        _audit("remove_user", actor_id, target, True)
        _notify("user removed", target)
        return True
    except AdminStorageError:
        _audit("remove_user", actor_id, target, False, "storage error")
        return False


def list_users(actor: Any) -> list[dict[str, Any]]:
    actor_id = _admin_action_allowed(actor, "list_users")
    if actor_id is None:
        return []
    try:
        users = deepcopy(_load_database()["users"])
        _audit("list_users", actor_id, None, True)
        return users
    except AdminStorageError:
        _audit("list_users", actor_id, None, False, "storage error")
        return []


def _update_user(actor: Any, user_id: Any, action: str, changes: dict[str, Any]) -> bool:
    target = _actor_id(user_id)
    actor_id = _admin_action_allowed(actor, action, target)
    if actor_id is None or target is None:
        return False
    if action == "deactivate_user" and target in _admin_ids():
        _audit(action, actor_id, target, False, "protected admin")
        return False
    try:
        with _STORAGE_LOCK:
            database = _load_database()
            user = next((item for item in database["users"] if item["id"] == target), None)
            if user is None:
                _audit(action, actor_id, target, False, "not found")
                return False
            user.update(changes)
            user["updated_at"] = _utc_now().isoformat()
            _atomic_write(database)
        _audit(action, actor_id, target, True)
        _notify(action.replace("_", " "), target)
        return True
    except AdminStorageError:
        _audit(action, actor_id, target, False, "storage error")
        return False


def activate_user(actor: Any, user_id: Any) -> bool:
    return _update_user(actor, user_id, "activate_user", {"active": True})


def deactivate_user(actor: Any, user_id: Any) -> bool:
    return _update_user(actor, user_id, "deactivate_user", {"active": False})


def set_user_plan(actor: Any, user_id: Any, plan: Any) -> bool:
    clean_plan = _normalize_plan(plan)
    if clean_plan is None:
        actor_id = _actor_id(actor)
        _audit("set_user_plan", actor_id or 0, _actor_id(user_id), False, "invalid plan")
        return False
    return _update_user(actor, user_id, "set_user_plan", {"plan": clean_plan})


def set_user_expiry(actor: Any, user_id: Any, expiry: Any) -> bool:
    try:
        clean_expiry = _normalize_expiry(expiry)
    except ValueError:
        actor_id = _actor_id(actor)
        _audit("set_user_expiry", actor_id or 0, _actor_id(user_id), False, "invalid expiry")
        return False
    return _update_user(actor, user_id, "set_user_expiry", {"expiry": clean_expiry})


def get_user(actor: Any, user_id: Any = None) -> dict[str, Any] | None:
    actor_id = _actor_id(actor)
    target = actor_id if user_id is None else _actor_id(user_id)
    if actor_id is None or target is None:
        return None
    if actor_id != target and actor_id not in _admin_ids():
        _audit("get_user", actor_id, target, False, "unauthorized")
        return None
    try:
        user = next((item for item in _load_database()["users"] if item["id"] == target), None)
    except AdminStorageError:
        return None
    if user is None and target in _admin_ids():
        user = {"id": target, "name": "Administrator", "plan": "ADMIN", "active": True, "expiry": None}
    if user is None:
        return None
    result = deepcopy(user)
    result["expired"] = _is_expired(result)
    result["authorized"] = validate_user_access(target)
    return result


def get_active_users() -> list[dict[str, Any]]:
    try:
        now = _utc_now()
        return [
            deepcopy(user)
            for user in _load_database()["users"]
            if user.get("active", False) and not _is_expired(user, now)
        ]
    except AdminStorageError:
        return []


def get_expired_users() -> list[dict[str, Any]]:
    try:
        now = _utc_now()
        return [deepcopy(user) for user in _load_database()["users"] if _is_expired(user, now)]
    except AdminStorageError:
        return []


def get_audit_trail(actor: Any, limit: int = 100) -> list[dict[str, Any]]:
    actor_id = _admin_action_allowed(actor, "get_audit_trail")
    if actor_id is None:
        return []
    limit = max(1, min(int(limit), 500))
    try:
        with _STORAGE_LOCK:
            if not AUDIT_FILE.exists():
                return []
            lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
        entries = []
        for line in lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    entries.append(value)
            except json.JSONDecodeError:
                continue
        return entries
    except OSError:
        LOGGER.error("Audit trail could not be read")
        return []


async def notify_admins(application: Any, message: str | None = None) -> int:
    """Deliver a sanitized notification, or queued admin actions, to all admins."""
    if application is None or not hasattr(application, "bot"):
        return 0
    with _STORAGE_LOCK:
        if message is None:
            messages = list(_pending_notifications)
            _pending_notifications.clear()
        else:
            messages = [str(message)]
    delivered = 0
    for text in messages:
        safe_text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED]", text)
        safe_text = re.sub(r"[\r\n]{3,}", "\n\n", safe_text).strip()[:3500]
        for admin_id in sorted(_admin_ids()):
            try:
                await application.bot.send_message(chat_id=admin_id, text="🔐 SHIVAY AI ADMIN\n\n" + safe_text)
                delivered += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.warning("Admin notification delivery failed")
    return delivered


def can_start_bot(user_or_update: Any) -> bool:
    return validate_user_access(user_or_update, "bot_start")


def can_stop_bot(user_or_update: Any) -> bool:
    return validate_user_access(user_or_update, "bot_stop")


def can_restart_bot(user_or_update: Any) -> bool:
    return validate_user_access(user_or_update, "restart")


def can_manual_scan(user_or_update: Any) -> bool:
    return validate_user_access(user_or_update, "manual_scan")


def can_view_reports(user_or_update: Any) -> bool:
    return validate_user_access(user_or_update, "reports")
