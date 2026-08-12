"""Repository security audit.

Fails the build when tracked files contain real secrets, or when any runtime
module exposes live order-placement capability. SHIVAY AI is read-only market
data + signalling only: it must never place, modify or cancel a live order.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {".git", "__pycache__", ".github", "node_modules", ".venv", "venv"}
TEXT_SUFFIXES = {
    ".py", ".txt", ".md", ".json", ".yml", ".yaml", ".bat", ".ps1",
    ".cfg", ".ini", ".pine", ".example", "",
}
ALLOWED_PLACEHOLDER = re.compile(
    r"(your[_-]?|example|placeholder|dummy|sample|xxxx|<|changeme|redacted|test|fake|\*{3})",
    re.IGNORECASE,
)

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("telegram_bot_token", re.compile(r"\b\d{9,12}:AA[0-9A-Za-z_\-]{32,}\b")),
    ("generic_assigned_secret", re.compile(
        r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|"
        r"client[_-]?secret|password|passwd|totp[_-]?secret)\b\s*[:=]\s*"
        r"['\"]([A-Za-z0-9_\-\.\/\+]{16,})['\"]")),
]

# Order-side API paths / SDK calls that must never appear in runtime code.
LIVE_ORDER_PATTERNS = [
    re.compile(r"/order/v1/placeOrder", re.IGNORECASE),
    re.compile(r"/order/v1/modifyOrder", re.IGNORECASE),
    re.compile(r"/order/v1/cancelOrder", re.IGNORECASE),
    re.compile(r"\bplace_order\s*\(", re.IGNORECASE),
    re.compile(r"\bplaceOrder\s*\(", re.IGNORECASE),
    re.compile(r"\bcancel_order\s*\(", re.IGNORECASE),
    re.compile(r"\bmodify_order\s*\(", re.IGNORECASE),
]

ENV_FILE_NAMES = {".env", ".env.local", ".env.production"}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
        files = [ROOT / line for line in out.splitlines() if line.strip()]
        if files:
            return files
    except Exception:
        pass
    return [p for p in ROOT.rglob("*") if p.is_file()]


def readable(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.name == Path(__file__).name:
        return False
    return path.suffix.lower() in TEXT_SUFFIXES


def main() -> int:
    failures: list[str] = []

    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix() if path.is_absolute() else str(path)

        if path.name in ENV_FILE_NAMES:
            failures.append(f"{rel}: real environment file must never be committed")
            continue

        if not path.exists() or not readable(path):
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        is_example = "example" in path.name.lower() or path.suffix == ".md"

        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > 4000:
                continue
            for label, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                captured = match.group(match.lastindex or 0)
                if ALLOWED_PLACEHOLDER.search(captured) or ALLOWED_PLACEHOLDER.search(line):
                    continue
                if is_example and label == "generic_assigned_secret":
                    continue
                if "os.getenv" in line or "os.environ" in line or "getenv(" in line:
                    continue
                failures.append(f"{rel}:{lineno}: possible {label} committed")

            if path.suffix == ".py" and "tests/" not in rel:
                for pattern in LIVE_ORDER_PATTERNS:
                    if pattern.search(line):
                        failures.append(
                            f"{rel}:{lineno}: live order capability is forbidden "
                            f"(read-only build): {line.strip()[:100]}"
                        )

    gitignore = ROOT / ".gitignore"
    if not gitignore.exists() or ".env" not in gitignore.read_text(encoding="utf-8"):
        failures.append(".gitignore must ignore .env")

    if failures:
        print("SECURITY AUDIT FAILED")
        for item in sorted(set(failures)):
            print(f"  - {item}")
        return 1

    print("SECURITY AUDIT PASSED: no secrets, no live-order code paths.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
