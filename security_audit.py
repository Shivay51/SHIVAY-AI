"""Repository secret scan and signals-only safety audit.

Run with ``python security_audit.py``. Exit code 0 means clean.
Findings never print the offending value, only file and line.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SKIP_DIRECTORIES = {".git", ".venv", "__pycache__", "cache", "storage", "node_modules", "vendor"}
TEXT_SUFFIXES = {".py", ".bat", ".ps1", ".md", ".json", ".txt", ".example", ".pine", ".yml", ".yaml", ".cfg", ".ini"}

SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("telegram_bot_token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("jwt_token", re.compile(r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b")),
    ("base32_totp_secret", re.compile(r"(?<![A-Z2-7])[A-Z2-7]{32}(?![A-Z2-7])")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("hardcoded_assignment", re.compile(
        r"(?i)\b(api_key|apikey|secret|password|passwd|mpin|totp_secret|bot_token|access_token|refresh_token)\s*=\s*[\"'][^\"'\s]{8,}[\"']")),
)
# The published RFC-6238 test vector secret is used by the unit tests; it is a
# public constant, not a credential, so base32 detection skips the test suite.
TEST_VECTOR_EXEMPT_RULES = {"base32_totp_secret"}
# Lines that only *forbid* an order capability are safety guards, not capability.
GUARD_MARKERS = ("forbidden", "FORBIDDEN", "hasattr", "assert", "must not", "no order", "raise ")

ALLOWED_PLACEHOLDERS = re.compile(
    r"(?i)(your_|example|placeholder|changeme|xxxx|redacted|dummy|<|\$\{|getenv|environ|os\.getenv|test|sample)")

ORDER_PATTERNS = (
    re.compile(r"(?i)\bplaceOrder\b"),
    re.compile(r"(?i)\bmodifyOrder\b"),
    re.compile(r"(?i)\bcancelOrder\b"),
    re.compile(r"(?i)\bplace_order\b"),
)
ORDER_ALLOWED_FILES = {"security_audit.py", "angel_provider.py", "SECURITY_CHECKLIST.md"}


def _files() -> list[Path]:
    result = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {".env.example", ".gitignore"}:
            result.append(path)
    return result


def scan_secrets() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in _files():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if len(line) > 2000:
                line = line[:2000]
            for label, pattern in SECRET_RULES:
                match = pattern.search(line)
                if not match:
                    continue
                if ALLOWED_PLACEHOLDERS.search(match.group(0)) or ALLOWED_PLACEHOLDERS.search(line):
                    continue
                if label in TEST_VECTOR_EXEMPT_RULES and "tests" in path.parts:
                    continue
                findings.append({"file": str(path.relative_to(ROOT)), "line": number, "rule": label})
    return findings


def scan_env_file() -> list[dict[str, Any]]:
    findings = []
    if (ROOT / ".env").exists():
        findings.append({"file": ".env", "line": 0, "rule": "env_file_present_in_repository"})
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").is_file() else ""
    if ".env" not in ignored:
        findings.append({"file": ".gitignore", "line": 0, "rule": "env_not_ignored"})
    return findings


def scan_order_capability() -> list[dict[str, Any]]:
    findings = []
    for path in _files():
        if path.suffix != ".py" or path.name in ORDER_ALLOWED_FILES or path.parts[-2:-1] == ("tests",):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if not any(pattern.search(line) for pattern in ORDER_PATTERNS):
                continue
            context = "\n".join(lines[max(0, number - 4):number])
            if any(marker in context for marker in GUARD_MARKERS):
                continue
            findings.append({"file": str(path.relative_to(ROOT)), "line": number, "rule": "order_api_reference"})
    return findings


def signals_only_state() -> dict[str, Any]:
    import config
    return {
        "SIGNALS_ONLY": bool(getattr(config, "SIGNALS_ONLY", False)),
        "LIVE_ORDER_PLACEMENT_ENABLED": bool(getattr(config, "LIVE_ORDER_PLACEMENT_ENABLED", True)),
        "PAPER_MONITORING": bool(getattr(config, "PAPER_MONITORING", False)),
        "SIGNAL_ADMIN_ONLY": bool(getattr(config, "SIGNAL_ADMIN_ONLY", False)),
    }


def audit() -> dict[str, Any]:
    secrets = scan_secrets()
    environment = scan_env_file()
    orders = scan_order_capability()
    state = signals_only_state()
    unsafe_state = (not state["SIGNALS_ONLY"]) or state["LIVE_ORDER_PLACEMENT_ENABLED"]
    return {
        "secret_findings": secrets,
        "environment_findings": environment,
        "order_findings": orders,
        "signals_only_state": state,
        "clean": not (secrets or environment or orders or unsafe_state),
    }


def main() -> int:
    report = audit()
    print("SHIVAY AI PRO — SECURITY AUDIT")
    print(f"secret findings: {len(report['secret_findings'])}")
    for item in report["secret_findings"]:
        print(f"  {item['rule']} -> {item['file']}:{item['line']}")
    print(f"environment findings: {len(report['environment_findings'])}")
    for item in report["environment_findings"]:
        print(f"  {item['rule']} -> {item['file']}")
    print(f"order-capability findings: {len(report['order_findings'])}")
    for item in report["order_findings"]:
        print(f"  {item['rule']} -> {item['file']}:{item['line']}")
    print(f"signals-only state: {report['signals_only_state']}")
    print("RESULT:", "CLEAN" if report["clean"] else "FINDINGS PRESENT")
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
