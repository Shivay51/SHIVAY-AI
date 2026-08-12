"""Runtime pre-flight and health check used by the Windows one-click scripts.

Validates that every required setting is present WITHOUT ever printing a value.
Exit code 0 = ready, 1 = not ready. ``--health`` also reports process state.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
LOCK_FILE = PROJECT_ROOT / ".shivay_ai.lock"

REQUIRED_VARIABLES = (
    "TELEGRAM_BOT_TOKEN",
    "ADMIN_ID",
    "ANGEL_API_KEY",
    "ANGEL_CLIENT_CODE",
    "ANGEL_MPIN",
    "ANGEL_TOTP_SECRET",
)
OPTIONAL_VARIABLES = (
    "TRADINGVIEW_WEBHOOK_SECRET",
    "SIGNAL_ADMIN_ONLY",
    "SCAN_INTERVAL",
)
MASK = "SET (value hidden)"


def _present(name: str) -> bool:
    return bool(str(os.environ.get(name, "")).strip())


def _load_dotenv() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def variable_report() -> dict[str, Any]:
    _load_dotenv()
    required = {name: (MASK if _present(name) else "MISSING") for name in REQUIRED_VARIABLES}
    optional = {name: (MASK if _present(name) else "not set") for name in OPTIONAL_VARIABLES}
    missing = sorted(name for name, state in required.items() if state == "MISSING")
    return {"required": required, "optional": optional, "missing": missing, "ready": not missing}


def safety_report() -> dict[str, Any]:
    try:
        import config
    except Exception as error:  # noqa: BLE001
        return {"error": f"config import failed: {type(error).__name__}", "safe": False}
    safe = bool(getattr(config, "SIGNALS_ONLY", False)) and not bool(
        getattr(config, "LIVE_ORDER_PLACEMENT_ENABLED", True))
    return {
        "signals_only": bool(getattr(config, "SIGNALS_ONLY", False)),
        "live_orders_enabled": bool(getattr(config, "LIVE_ORDER_PLACEMENT_ENABLED", True)),
        "admin_only_delivery": bool(getattr(config, "SIGNAL_ADMIN_ONLY", False)),
        "scan_interval_seconds": int(getattr(config, "SCAN_INTERVAL", 0) or 0),
        "safe": safe,
    }


def process_report() -> dict[str, Any]:
    if not LOCK_FILE.is_file():
        return {"lock_file": False, "pid": None, "running": False}
    raw = LOCK_FILE.read_text(encoding="utf-8", errors="replace").strip()
    pid = int(raw) if raw.isdigit() else None
    running = False
    if pid:
        try:
            os.kill(pid, 0)
            running = True
        except (OSError, ProcessLookupError, PermissionError):
            running = bool(os.name == "nt")
    return {"lock_file": True, "pid": pid, "running": running}


def report(include_process: bool = False) -> dict[str, Any]:
    variables = variable_report()
    safety = safety_report()
    result: dict[str, Any] = {"variables": variables, "safety": safety}
    if include_process:
        result["process"] = process_report()
    result["ready"] = bool(variables["ready"] and safety.get("safe"))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SHIVAY AI PRO runtime check")
    parser.add_argument("--health", action="store_true", help="include process health")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    arguments = parser.parse_args(argv)

    result = report(include_process=arguments.health)
    if arguments.json:
        print(json.dumps(result, indent=2))
        return 0 if result["ready"] else 1

    print("SHIVAY AI PRO — RUNTIME CHECK")
    print("required settings:")
    for name, state in result["variables"]["required"].items():
        print(f"  {name}: {state}")
    print("optional settings:")
    for name, state in result["variables"]["optional"].items():
        print(f"  {name}: {state}")
    safety = result["safety"]
    print(f"safety: signals_only={safety.get('signals_only')} "
          f"live_orders_enabled={safety.get('live_orders_enabled')} "
          f"admin_only_delivery={safety.get('admin_only_delivery')} "
          f"scan_interval={safety.get('scan_interval_seconds')}s")
    if arguments.health:
        state = result["process"]
        print(f"process: lock_file={state['lock_file']} pid={state['pid']} running={state['running']}")
    if result["variables"]["missing"]:
        print("MISSING: " + ", ".join(result["variables"]["missing"]))
    print("RESULT:", "READY" if result["ready"] else "NOT READY")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
