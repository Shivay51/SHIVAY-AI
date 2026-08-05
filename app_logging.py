"""Central, secret-safe logging configuration for SHIVAY AI."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import config


PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIRECTORY = PROJECT_ROOT / "logs"
DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_BACKUP_COUNT = 7
_SETUP_LOCK = threading.RLock()
_configured = False
_managed_loggers: set[str] = set()


class SecretMaskingFilter(logging.Filter):
    _telegram_token = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
    _assignment = re.compile(
        r"(?i)\b(token|secret|password|passwd|api[_-]?key|private[_-]?key|client[_-]?secret)"
        r"\s*[:=]\s*([^\s,;]+)"
    )
    _private_id = re.compile(r"(?i)\b(admin|chat|user)[_-]?id\s*[:=]\s*[-+]?\d+")

    def __init__(self) -> None:
        super().__init__()
        configured = [
            os.getenv("BOT_TOKEN"), os.getenv("TELEGRAM_BOT_TOKEN"),
            os.getenv("CHAT_ID"), os.getenv("ADMIN_ID"),
            os.getenv("SHOONYA_USER_ID"), os.getenv("SHOONYA_VENDOR_CODE"),
            os.getenv("SHOONYA_IMEI"), os.getenv("SHOONYA_SESSION_TOKEN"),
        ]
        for name, value in os.environ.items():
            normalized = name.upper()
            if value and any(marker in normalized for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "PRIVATE_KEY")):
                configured.append(value)
        if hasattr(config, "ADMIN_ID"):
            configured.append(str(getattr(config, "ADMIN_ID")))
        self._known_secrets = tuple(str(value) for value in configured if value)

    def mask(self, value: Any) -> str:
        text = str(value)
        for secret in self._known_secrets:
            text = text.replace(secret, "[REDACTED]")
        text = self._telegram_token.sub("[REDACTED_TOKEN]", text)
        text = self._assignment.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
        text = self._private_id.sub(lambda match: match.group(0).split(":")[0].split("=")[0] + "=[REDACTED]", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self.mask(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "[LOG MESSAGE REDACTED AFTER FORMATTING FAILURE]"
            record.args = ()
        return True


class SafeConsoleHandler(logging.StreamHandler[Any]):
    """Console output that survives legacy Windows encodings and emoji."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except UnicodeEncodeError:
            try:
                message = self.format(record)
                encoding = getattr(self.stream, "encoding", None) or "utf-8"
                safe = message.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
                self.stream.write(safe + self.terminator)
                self.flush()
            except Exception:
                self.handleError(record)


def _level(value: Any) -> int:
    if isinstance(value, int):
        return max(logging.DEBUG, min(value, logging.CRITICAL))
    return getattr(logging, str(value or "INFO").upper(), logging.INFO)


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _mark(handler: logging.Handler) -> logging.Handler:
    handler._shivay_managed = True  # type: ignore[attr-defined]
    handler.addFilter(SecretMaskingFilter())
    handler.setFormatter(_formatter())
    return handler


def _rotating_handler(filename: str, level: int = logging.DEBUG) -> logging.Handler:
    maximum = max(500_000, int(getattr(config, "LOG_MAX_BYTES", DEFAULT_MAX_BYTES)))
    backups = max(1, min(int(getattr(config, "LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT)), 30))
    handler = logging.handlers.RotatingFileHandler(
        LOG_DIRECTORY / filename,
        maxBytes=maximum,
        backupCount=backups,
        encoding="utf-8",
        delay=True,
    )
    handler.setLevel(level)
    return _mark(handler)


def _add_once(logger: logging.Logger, handler: logging.Handler, identity: str) -> None:
    for existing in logger.handlers:
        if getattr(existing, "_shivay_identity", None) == identity:
            handler.close()
            return
    handler._shivay_identity = identity  # type: ignore[attr-defined]
    logger.addHandler(handler)


def setup_logging(level: str | int | None = None, log_directory: str | os.PathLike[str] | None = None) -> bool:
    """Idempotently configure console, general, error, and category logs."""
    global _configured
    global LOG_DIRECTORY
    with _SETUP_LOCK:
        try:
            if log_directory is not None:
                candidate = Path(log_directory)
                if not candidate.is_absolute():
                    candidate = PROJECT_ROOT / candidate
                resolved = candidate.resolve(strict=False)
                if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
                    return False
                LOG_DIRECTORY = resolved
            LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
            configured_level = _level(level if level is not None else getattr(config, "LOG_LEVEL", "INFO"))
            root = logging.getLogger()
            root.setLevel(configured_level)
            console = SafeConsoleHandler(sys.stderr)
            console.setLevel(configured_level)
            _add_once(root, _mark(console), "console")
            _add_once(root, _rotating_handler("shivay_ai.log", logging.DEBUG), "general")
            error_handler = _rotating_handler("errors.log", logging.ERROR)
            _add_once(root, error_handler, "errors")

            categories = {
                "shivay.trade": "trades.log",
                "shivay.signal": "signals.log",
                "shivay.security": "security.log",
                "shivay.performance": "performance.log",
                "shivay.scheduler": "scheduler.log",
                "shivay.provider": "provider.log",
                "shivay.recovery": "recovery.log",
                "shivay.auto_recovery": "recovery.log",
                "shivay.startup": "startup.log",
                "shivay.shutdown": "startup.log",
            }
            for name, filename in categories.items():
                logger = logging.getLogger(name)
                logger.setLevel(logging.DEBUG)
                logger.propagate = True
                _add_once(logger, _rotating_handler(filename, logging.DEBUG), f"category:{name}")
                _managed_loggers.add(name)
            logging.captureWarnings(True)
            _configured = True
            logging.getLogger("shivay.startup").info("Logging initialized")
            return True
        except Exception:
            _configured = False
            try:
                sys.stderr.write("SHIVAY AI logging initialization failed safely\n")
            except Exception:
                pass
            return False


def get_logger(name: str = "shivay") -> logging.Logger:
    if not _configured:
        setup_logging()
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or "shivay"))[:120]
    if not clean.startswith("shivay"):
        clean = "shivay." + clean
    return logging.getLogger(clean)


def _safe_payload(payload: Mapping[str, Any] | Any) -> str:
    if isinstance(payload, Mapping):
        safe: dict[str, Any] = {}
        for key, value in payload.items():
            clean_key = str(key)[:80]
            if re.search(r"(?i)token|secret|password|api.?key|chat.?id|user.?id|admin.?id", clean_key):
                safe[clean_key] = "[REDACTED]"
            elif isinstance(value, (str, int, float, bool)) or value is None:
                safe[clean_key] = value
            else:
                safe[clean_key] = str(value)[:200]
        return json.dumps(safe, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(payload)[:2_000]


def log_trade(payload: Mapping[str, Any] | Any, message: str = "trade") -> None:
    get_logger("shivay.trade").info("%s | %s", str(message)[:100], _safe_payload(payload))


def log_signal(payload: Mapping[str, Any] | Any, message: str = "signal") -> None:
    get_logger("shivay.signal").info("%s | %s", str(message)[:100], _safe_payload(payload))


def log_security_event(event: str, details: Mapping[str, Any] | Any = "") -> None:
    get_logger("shivay.security").warning("%s | %s", str(event)[:100], _safe_payload(details))


def log_performance(payload: Mapping[str, Any] | Any, message: str = "performance") -> None:
    get_logger("shivay.performance").info("%s | %s", str(message)[:100], _safe_payload(payload))


def log_exception(
    logger_or_name: logging.Logger | str,
    message: str,
    error: BaseException | None = None,
) -> None:
    logger = logger_or_name if isinstance(logger_or_name, logging.Logger) else get_logger(str(logger_or_name))
    if error is None:
        logger.exception("%s", str(message)[:500])
    else:
        logger.error("%s | error=%s", str(message)[:500], type(error).__name__, exc_info=(type(error), error, error.__traceback__))


def shutdown_logging() -> None:
    global _configured
    with _SETUP_LOCK:
        loggers = [logging.getLogger(), *(logging.getLogger(name) for name in _managed_loggers)]
        seen: set[int] = set()
        for logger in loggers:
            for handler in list(logger.handlers):
                if not getattr(handler, "_shivay_managed", False) or id(handler) in seen:
                    continue
                seen.add(id(handler))
                try:
                    handler.flush()
                    handler.close()
                finally:
                    logger.removeHandler(handler)
        logging.shutdown()
        _managed_loggers.clear()
        _configured = False


def logging_status() -> dict[str, Any]:
    return {
        "configured": _configured,
        "log_directory": str(LOG_DIRECTORY),
        "managed_loggers": sorted(_managed_loggers),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
