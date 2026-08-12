"""Structured rejection logging with rotation and secret redaction.

Every scanner/provider rejection is appended as one JSON line. Files rotate at
``REJECTION_LOG_MAX_BYTES`` and only ``REJECTION_LOG_BACKUPS`` archives are kept.
Any value that looks like a credential is redacted before it is written.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

LOGGER = logging.getLogger("shivay.rejections")
ROOT = Path(__file__).resolve().parent
LOG_FILE = Path(os.getenv("REJECTION_LOG_FILE", str(ROOT / "storage" / "rejections.jsonl")))
MAX_BYTES = max(65536, int(os.getenv("REJECTION_LOG_MAX_BYTES", "2097152")))
BACKUPS = max(1, int(os.getenv("REJECTION_LOG_BACKUPS", "5")))
_LOCK = threading.RLock()

REDACTED = "***REDACTED***"
_SECRET_KEYS = (
    "token", "secret", "password", "passwd", "pin", "mpin", "otp", "totp", "api_key",
    "apikey", "auth", "authorization", "jwt", "refresh", "feed_token", "client_code",
    "credential", "bot_token", "chat_id", "session",
)
_SECRET_PATTERNS = (
    re.compile(r"\b\d{6}\b"),                              # OTP / PIN
    re.compile(r"\b[A-Z2-7]{16,}\b"),                      # base32 TOTP secret
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),        # telegram bot token
    re.compile(r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),  # JWT
)


def _is_secret_key(key: Any) -> bool:
    text = str(key).lower()
    return any(marker in text for marker in _SECRET_KEYS)


def redact(value: Any, key: Any = None) -> Any:
    """Return a log-safe copy of ``value`` with credentials removed."""
    if key is not None and _is_secret_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(inner): redact(item, inner) for inner, item in list(value.items())[:40]}
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in list(value)[:40]]
    if isinstance(value, str):
        text = value[:400]
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value))


def _rotate() -> None:
    try:
        if not LOG_FILE.is_file() or LOG_FILE.stat().st_size < MAX_BYTES:
            return
        for index in range(BACKUPS - 1, 0, -1):
            source = LOG_FILE.with_suffix(LOG_FILE.suffix + f".{index}")
            if source.is_file():
                source.replace(LOG_FILE.with_suffix(LOG_FILE.suffix + f".{index + 1}"))
        LOG_FILE.replace(LOG_FILE.with_suffix(LOG_FILE.suffix + ".1"))
        oldest = LOG_FILE.with_suffix(LOG_FILE.suffix + f".{BACKUPS + 1}")
        if oldest.is_file():
            oldest.unlink()
    except OSError as error:
        LOGGER.warning("Rejection log rotation failed: %s", type(error).__name__)


def record(symbol: Any, reason: Any, details: Mapping[str, Any] | None = None,
           *, stage: str = "SCANNER", now: datetime | None = None) -> dict[str, Any] | None:
    """Append one redacted rejection record. Returns the written entry."""
    reasons = [str(item) for item in reason] if isinstance(reason, (list, tuple, set)) else [str(reason)]
    entry = {
        "timestamp": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "stage": str(stage)[:32],
        "symbol": str(symbol or "")[:40],
        "reasons": reasons[:10],
        "details": redact(dict(details or {})),
    }
    try:
        with _LOCK:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _rotate()
            with LOG_FILE.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
    except OSError as error:
        LOGGER.warning("Rejection log write failed: %s", type(error).__name__)
        return None
    return entry


def load(limit: int | None = None, *, include_archives: bool = True) -> list[dict[str, Any]]:
    files: list[Path] = []
    if include_archives:
        files.extend(sorted(
            (path for path in LOG_FILE.parent.glob(LOG_FILE.name + ".*") if path.is_file()),
            key=lambda path: path.name, reverse=True,
        ))
    if LOG_FILE.is_file():
        files.append(LOG_FILE)
    rows: list[dict[str, Any]] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows[-limit:] if limit else rows


def status() -> dict[str, Any]:
    return {
        "file": str(LOG_FILE),
        "exists": LOG_FILE.is_file(),
        "size_bytes": LOG_FILE.stat().st_size if LOG_FILE.is_file() else 0,
        "max_bytes": MAX_BYTES,
        "backups": BACKUPS,
        "records": len(load()),
    }


def reset_for_tests(path: Path | None = None, *, max_bytes: int | None = None) -> None:
    global LOG_FILE, MAX_BYTES
    with _LOCK:
        if path is not None:
            LOG_FILE = Path(path)
        if max_bytes is not None:
            MAX_BYTES = int(max_bytes)
