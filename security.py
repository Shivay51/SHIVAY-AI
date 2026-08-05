"""Security, validation, and access-control helpers for SHIVAY AI."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import config


LOGGER = logging.getLogger("shivay.security")
PROJECT_ROOT = Path(__file__).resolve().parent
SECURITY_AUDIT_FILE = PROJECT_ROOT / "security_audit.jsonl"
MAX_TELEGRAM_ID = 10**15
MAX_TEXT_LENGTH = 2_000
MAX_ARGUMENTS = 16
MAX_ARGUMENT_LENGTH = 160
DEFAULT_BLOCK_SECONDS = 300
_SECRET_NAME = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET)",
    re.IGNORECASE,
)
_VALID_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_TOKEN_VALUE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DANGEROUS_ARGUMENT = re.compile(r"(?:\.\.[/\\]|[\x00\r\n]|[;&|`<>])")
_SAFE_COMMAND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_AUDIT_LOCK = threading.RLock()
_RATE_LOCK = threading.RLock()
_rate_events: dict[tuple[int, str], deque[float]] = defaultdict(deque)
_last_execution: dict[tuple[int, str], float] = {}
_violations: dict[int, deque[float]] = defaultdict(deque)
_blocked_until: dict[int, float] = {}


RATE_POLICIES: dict[str, tuple[int, float, float]] = {
    "default": (20, 60.0, 0.5),
    "informational": (20, 60.0, 0.5),
    "scan": (2, 60.0, 15.0),
    "manual_scan": (2, 60.0, 15.0),
    "buy": (2, 60.0, 15.0),
    "sell": (2, 60.0, 15.0),
    "admin": (6, 60.0, 2.0),
    "adduser": (4, 60.0, 3.0),
    "removeuser": (3, 60.0, 5.0),
    "restart": (1, 120.0, 60.0),
    "stopbot": (1, 120.0, 60.0),
    "shutdown": (1, 120.0, 60.0),
}
ADMIN_ACTIONS = frozenset(
    {
        "add_user", "remove_user", "activate_user", "deactivate_user",
        "set_user_plan", "set_user_expiry", "start", "startbot", "stop",
        "stopbot", "shutdown", "restart", "user_management",
    }
)
CONFIRMATION_ACTIONS = frozenset({"stop", "stopbot", "shutdown", "restart"})
COMMAND_PERMISSIONS = {
    "scan": "manual_scan", "buy": "manual_scan", "sell": "manual_scan",
    "report": "reports", "performance": "reports", "trades": "trades",
    "open": "trades", "closed": "trades", "gold": "commodities",
    "silver": "commodities", "startbot": "bot_start", "stopbot": "bot_stop",
    "restart": "restart", "adduser": "user_management",
    "removeuser": "user_management", "listusers": "user_management",
}


class SecurityValidationError(ValueError):
    """A safe, non-sensitive validation failure."""


def _extract_user_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    effective_user = getattr(value, "effective_user", None)
    if effective_user is not None:
        value = getattr(effective_user, "id", None)
    elif hasattr(value, "id") and not isinstance(value, (str, bytes, int)):
        value = getattr(value, "id", None)
    if isinstance(value, str) and not re.fullmatch(r"[1-9]\d{0,14}", value.strip()):
        return None
    try:
        user_id = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return user_id if 0 < user_id <= MAX_TELEGRAM_ID else None


def validate_user_id(value: Any) -> bool:
    """Validate a Telegram user ID without coercing unsafe input."""
    return _extract_user_id(value) is not None


def _admin_module() -> Any:
    import admin

    return admin


def is_admin(user_or_update: Any) -> bool:
    """Preserve the legacy Update interface while also accepting an ID or user."""
    if _extract_user_id(user_or_update) is None:
        return False
    try:
        return bool(_admin_module().is_admin(user_or_update))
    except Exception:
        LOGGER.error("Admin validation failed closed")
        return False


def is_authorized(user_or_update: Any, permission: str = "access") -> bool:
    """Check active, non-expired, plan-aware access through admin.py."""
    if _extract_user_id(user_or_update) is None or is_user_blocked(user_or_update):
        return False
    permission = sanitize_text(permission, max_length=48).lower().replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", permission):
        return False
    try:
        return bool(_admin_module().validate_user_access(user_or_update, permission))
    except Exception:
        LOGGER.error("Authorization validation failed closed")
        return False


def validate_admin_action(
    user_or_update: Any,
    action: str,
    confirmation: str | None = None,
) -> bool:
    """Authorize an admin action and require explicit confirmation when destructive."""
    user_id = _extract_user_id(user_or_update)
    clean_action = sanitize_text(action, max_length=48).lower().replace(" ", "_")
    if user_id is None or clean_action not in ADMIN_ACTIONS or not is_admin(user_or_update):
        record_security_event("admin_action_denied", user_id, {"action": clean_action})
        return False
    if is_user_blocked(user_id):
        return False
    rate_command = clean_action.replace("_", "")
    if not check_rate_limit(user_id, rate_command if rate_command in RATE_POLICIES else "admin"):
        return False
    if clean_action in CONFIRMATION_ACTIONS:
        expected = f"CONFIRM {clean_action.upper()}"
        if confirmation is None or not secure_compare(sanitize_text(confirmation, 64), expected):
            record_security_event("admin_confirmation_failed", user_id, {"action": clean_action})
            return False
    record_security_event("admin_action_authorized", user_id, {"action": clean_action})
    return True


def authorize_command(user_or_update: Any, command: str) -> bool:
    """Apply access, permission, blocking, and rate-limit checks to a command."""
    clean_command = sanitize_text(command, max_length=32).lower().lstrip("/")
    if not _SAFE_COMMAND.fullmatch(clean_command):
        return False
    permission = COMMAND_PERMISSIONS.get(clean_command, "access")
    if not is_authorized(user_or_update, permission):
        record_security_event("command_access_denied", user_or_update, {"command": clean_command})
        return False
    return check_rate_limit(user_or_update, clean_command)


def _policy_for(command: str) -> tuple[int, float, float]:
    if command in RATE_POLICIES:
        return RATE_POLICIES[command]
    if command in {"market", "giftnifty", "prediction", "status", "ping", "version", "help", "start", "id"}:
        return RATE_POLICIES["informational"]
    if command in {"adduser", "removeuser", "listusers", "startbot"}:
        return RATE_POLICIES["admin"]
    return RATE_POLICIES["default"]


def check_rate_limit(
    user_or_update: Any,
    command: str = "default",
    limit: int | None = None,
    window_seconds: float | None = None,
    cooldown_seconds: float | None = None,
) -> bool:
    """Return True once per allowed execution; fail closed and block persistent spam."""
    user_id = _extract_user_id(user_or_update)
    clean_command = sanitize_text(command, max_length=32).lower().lstrip("/")
    if user_id is None or not _SAFE_COMMAND.fullmatch(clean_command):
        return False
    now = time.monotonic()
    with _RATE_LOCK:
        _cleanup_security_state(now)
        if _blocked_until.get(user_id, 0.0) > now:
            return False
        policy_limit, policy_window, policy_cooldown = _policy_for(clean_command)
        maximum = policy_limit if limit is None else max(1, min(int(limit), 100))
        window = policy_window if window_seconds is None else max(1.0, min(float(window_seconds), 3600.0))
        cooldown = policy_cooldown if cooldown_seconds is None else max(0.0, min(float(cooldown_seconds), window))
        key = (user_id, clean_command)
        last = _last_execution.get(key)
        events = _rate_events[key]
        while events and events[0] <= now - window:
            events.popleft()
        if (last is not None and now - last < cooldown) or len(events) >= maximum:
            _register_violation(user_id, now)
            record_security_event("rate_limit_exceeded", user_id, {"command": clean_command})
            return False
        events.append(now)
        _last_execution[key] = now
        return True


def _register_violation(user_id: int, now: float) -> None:
    violations = _violations[user_id]
    while violations and violations[0] <= now - 120.0:
        violations.popleft()
    violations.append(now)
    if len(violations) >= 5:
        _blocked_until[user_id] = max(_blocked_until.get(user_id, 0.0), now + DEFAULT_BLOCK_SECONDS)
        violations.clear()
        record_security_event("user_temporarily_blocked", user_id, {"duration_seconds": DEFAULT_BLOCK_SECONDS})


def _cleanup_security_state(now: float) -> None:
    for user_id, until in list(_blocked_until.items()):
        if until <= now:
            _blocked_until.pop(user_id, None)
    for user_id, values in list(_violations.items()):
        while values and values[0] <= now - 120.0:
            values.popleft()
        if not values:
            _violations.pop(user_id, None)
    if len(_last_execution) > 10_000:
        threshold = now - 3600.0
        for key, timestamp in list(_last_execution.items()):
            if timestamp < threshold:
                _last_execution.pop(key, None)
                _rate_events.pop(key, None)


def is_user_blocked(user_or_update: Any) -> bool:
    user_id = _extract_user_id(user_or_update)
    if user_id is None:
        return True
    now = time.monotonic()
    with _RATE_LOCK:
        until = _blocked_until.get(user_id, 0.0)
        if until <= now:
            _blocked_until.pop(user_id, None)
            return False
        return True


def block_user_temporarily(
    user_or_update: Any,
    seconds: int = DEFAULT_BLOCK_SECONDS,
    reason: str = "security policy",
) -> bool:
    user_id = _extract_user_id(user_or_update)
    if user_id is None:
        return False
    duration = max(10, min(int(seconds), 86_400))
    with _RATE_LOCK:
        _blocked_until[user_id] = max(
            _blocked_until.get(user_id, 0.0), time.monotonic() + duration
        )
    record_security_event(
        "user_temporarily_blocked",
        user_id,
        {"duration_seconds": duration, "reason": sanitize_text(reason, 80)},
    )
    return True


def unblock_user(user_or_update: Any, admin_actor: Any) -> bool:
    user_id = _extract_user_id(user_or_update)
    if user_id is None or not is_admin(admin_actor):
        return False
    with _RATE_LOCK:
        _blocked_until.pop(user_id, None)
        _violations.pop(user_id, None)
    record_security_event("user_unblocked", admin_actor, {"target": _anonymous_id(user_id)})
    return True


def sanitize_text(value: Any, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Normalize untrusted text and remove controls without applying a parse mode."""
    maximum = max(0, min(int(max_length), 10_000))
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = _CONTROL_CHARACTERS.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TOKEN_VALUE.sub("[REDACTED]", text)
    return text.strip()[:maximum]


def sanitize_command_args(
    args: Iterable[Any] | None,
    max_args: int = MAX_ARGUMENTS,
    max_length: int = MAX_ARGUMENT_LENGTH,
) -> list[str] | None:
    """Return sanitized command arguments, or None when input is malformed."""
    if args is None:
        return []
    if isinstance(args, (str, bytes, Mapping)):
        return None
    try:
        values = list(args)
    except TypeError:
        return None
    maximum_args = max(0, min(int(max_args), 64))
    maximum_length = max(1, min(int(max_length), 1_000))
    if len(values) > maximum_args:
        return None
    result = []
    for value in values:
        text = sanitize_text(value, maximum_length)
        if not text or len(str(value)) > maximum_length or _DANGEROUS_ARGUMENT.search(text):
            return None
        result.append(text)
    return result


def validate_numeric_input(
    value: Any,
    minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
) -> int | float | None:
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    pattern = r"[+-]?\d+" if integer else r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    if not re.fullmatch(pattern, text):
        return None
    try:
        number: int | float = int(text) if integer else float(text)
    except (ValueError, OverflowError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def mask_secret(value: Any, visible: int = 2) -> str:
    """Return a non-reversible display form suitable for status output."""
    if value in (None, ""):
        return "[NOT SET]"
    text = str(value)
    shown = max(0, min(int(visible), 4))
    if shown == 0 or len(text) <= shown * 2 + 4:
        return "*" * min(max(len(text), 8), 16)
    return text[:shown] + ("*" * min(max(len(text) - shown * 2, 8), 16)) + text[-shown:]


def load_secret(name: str, default: str | None = None, required: bool = False) -> str | None:
    """Load a secret from the environment, with a config fallback when present."""
    clean_name = str(name or "").strip().upper()
    if not _VALID_ENV_NAME.fullmatch(clean_name):
        raise SecurityValidationError("Invalid secret variable name")
    value = os.getenv(clean_name)
    if value is None and hasattr(config, clean_name):
        configured = getattr(config, clean_name)
        if isinstance(configured, (str, int, float)):
            value = str(configured)
    if value is None:
        value = default
    if value is not None:
        value = str(value).strip()
    if required and not value:
        raise SecurityValidationError(f"Required secret {clean_name} is not configured")
    return value or None


def secure_compare(left: Any, right: Any) -> bool:
    """Compare strings or bytes in constant time after type-safe conversion."""
    if left is None or right is None:
        return False
    left_bytes = left if isinstance(left, bytes) else str(left).encode("utf-8")
    right_bytes = right if isinstance(right, bytes) else str(right).encode("utf-8")
    return hmac.compare_digest(left_bytes, right_bytes)


def _anonymous_id(value: Any) -> str:
    user_id = _extract_user_id(value)
    if user_id is None:
        return "unknown"
    pepper = os.getenv("SECURITY_AUDIT_PEPPER", PROJECT_ROOT.name)
    digest = hashlib.sha256(f"{pepper}:{user_id}".encode("utf-8")).hexdigest()
    return "user-" + digest[:12]


def _safe_event_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (details or {}).items():
        clean_key = sanitize_text(key, 48).lower().replace(" ", "_")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", clean_key):
            continue
        if _SECRET_NAME.search(clean_key):
            result[clean_key] = "[REDACTED]"
        elif clean_key.endswith("_id") or clean_key in {"target", "user"}:
            result[clean_key] = _anonymous_id(value)
        elif isinstance(value, (bool, int, float)):
            result[clean_key] = value
        else:
            result[clean_key] = sanitize_text(value, 160)
    return result


def record_security_event(
    event: str,
    user_or_update: Any = None,
    details: Mapping[str, Any] | None = None,
) -> bool:
    """Append a privacy-conscious, secret-free security audit event."""
    clean_event = sanitize_text(event, 64).lower().replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", clean_event):
        clean_event = "invalid_event"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": clean_event,
        "subject": _anonymous_id(user_or_update),
        "details": _safe_event_details(details),
    }
    try:
        with _AUDIT_LOCK:
            with SECURITY_AUDIT_FILE.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        LOGGER.warning("Security event recorded: %s", clean_event)
        return True
    except OSError as error:
        LOGGER.error("Security audit write failed: %s", type(error).__name__)
        return False


def _approved_roots(allowed_roots: Iterable[str | os.PathLike[str]] | None) -> tuple[Path, ...]:
    roots = [PROJECT_ROOT]
    if allowed_roots is not None:
        for value in allowed_roots:
            try:
                root = Path(value).resolve(strict=False)
                root.relative_to(PROJECT_ROOT)
                roots.append(root)
            except (OSError, ValueError, TypeError):
                continue
    return tuple(dict.fromkeys(roots))


def validate_file_path(
    path: str | os.PathLike[str],
    allowed_roots: Iterable[str | os.PathLike[str]] | None = None,
    must_exist: bool = False,
    for_write: bool = False,
) -> Path | None:
    """Resolve a path and ensure it remains inside approved project storage."""
    if not isinstance(path, (str, os.PathLike)) or "\x00" in str(path):
        return None
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        resolved = candidate.resolve(strict=must_exist)
        if not any(resolved == root or root in resolved.parents for root in _approved_roots(allowed_roots)):
            record_security_event("path_traversal_rejected", None, {"path": candidate.name})
            return None
        if must_exist and not resolved.exists():
            return None
        if for_write:
            parent = resolved.parent
            if not parent.exists() or not parent.is_dir():
                return None
            cursor = parent
            while cursor != PROJECT_ROOT.parent:
                if cursor.is_symlink():
                    return None
                if cursor == PROJECT_ROOT:
                    break
                cursor = cursor.parent
            if resolved.exists() and resolved.is_symlink():
                return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def secure_json_load(
    path: str | os.PathLike[str],
    default: Any = None,
    max_bytes: int = 2_000_000,
) -> Any:
    safe_path = validate_file_path(path, must_exist=True)
    if safe_path is None or not safe_path.is_file():
        return default
    try:
        if safe_path.stat().st_size > max(1, min(int(max_bytes), 20_000_000)):
            return default
        with safe_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        record_security_event("malformed_json_rejected", None, {"file": safe_path.name})
        return default


def secure_json_write(
    path: str | os.PathLike[str],
    data: Any,
    backup: bool = True,
) -> bool:
    """Atomically write JSON inside the project and preserve the previous file."""
    safe_path = validate_file_path(path, for_write=True)
    if safe_path is None:
        return False
    descriptor = -1
    temporary = ""
    try:
        encoded = json.dumps(data, ensure_ascii=False, indent=4, allow_nan=False)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{safe_path.name}-", suffix=".tmp", dir=safe_path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if backup and safe_path.exists():
            shutil.copy2(safe_path, safe_path.with_suffix(safe_path.suffix + ".bak"))
        os.replace(temporary, safe_path)
        temporary = ""
        return True
    except (OSError, TypeError, ValueError) as error:
        LOGGER.error("Secure JSON write failed: %s", type(error).__name__)
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def validate_config_security() -> dict[str, Any]:
    """Return a non-secret configuration security report."""
    token = load_secret("BOT_TOKEN") or load_secret("TELEGRAM_BOT_TOKEN")
    admin_values = []
    if hasattr(config, "ADMIN_ID"):
        admin_values.append(getattr(config, "ADMIN_ID"))
    configured = getattr(config, "ADMIN_IDS", ())
    if isinstance(configured, (str, int)):
        configured = [configured]
    admin_values.extend(configured)
    valid_admins = {_extract_user_id(value) for value in admin_values}
    valid_admins.discard(None)
    token_format_valid = bool(token and re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", token))
    env_path = PROJECT_ROOT / ".env"
    environment_permissions = "not_present"
    if env_path.exists():
        try:
            mode = env_path.stat().st_mode & 0o777
            environment_permissions = "restricted" if os.name == "nt" or mode & 0o077 == 0 else "too_open"
        except OSError:
            environment_permissions = "unknown"
    issues = []
    if not token:
        issues.append("bot_token_missing")
    elif not token_format_valid:
        issues.append("bot_token_format_invalid")
    if not valid_admins:
        issues.append("admin_configuration_invalid")
    if environment_permissions == "too_open":
        issues.append("environment_file_permissions_too_open")
    return {
        "secure": not issues,
        "bot_token_configured": bool(token),
        "bot_token_masked": mask_secret(token),
        "bot_token_format_valid": token_format_valid,
        "admin_configuration_valid": bool(valid_admins),
        "admin_count": len(valid_admins),
        "environment_file_permissions": environment_permissions,
        "issues": issues,
    }


def validate_update(update: Any, max_message_length: int = MAX_TEXT_LENGTH) -> bool:
    """Validate the minimal trusted shape of a polling or webhook Update."""
    user = getattr(update, "effective_user", None)
    if user is None or not validate_user_id(getattr(user, "id", None)):
        return False
    message = getattr(update, "effective_message", None)
    if message is None:
        return True
    text = getattr(message, "text", None)
    if text is not None and (not isinstance(text, str) or len(text) > max(1, int(max_message_length))):
        return False
    return True


def clear_security_state() -> None:
    """Clear in-memory rate and block state during a controlled shutdown."""
    with _RATE_LOCK:
        _rate_events.clear()
        _last_execution.clear()
        _violations.clear()
        _blocked_until.clear()
