"""Offline syntax and safety preflight for SHIVAY AI."""
from __future__ import annotations

import compileall
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXCLUDED = {".venv", "venv", "__pycache__", "logs", "storage", "cache"}


def main() -> int:
    files = [p for p in ROOT.rglob("*.py") if not any(part in EXCLUDED for part in p.parts)]
    failures = [p.relative_to(ROOT) for p in files if not compileall.compile_file(str(p), quiet=1, force=True)]
    policy_path = ROOT / "alert_quality_policy.json"
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        safety = policy.get("mode") == "ALERTS_ONLY" and policy.get("orders_enabled") is False
    except (OSError, json.JSONDecodeError):
        safety = False
    if failures or not safety:
        print("PREFLIGHT FAILED")
        for path in failures:
            print(f"syntax: {path}")
        if not safety:
            print("policy: alerts-only safety configuration invalid")
        return 1
    print(f"PREFLIGHT PASSED: {len(files)} Python files compiled; alerts-only policy verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
