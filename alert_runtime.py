"""Run SHIVAY's alert-only quality gate over JSONL signals.

Input: one signal object per line. Output: accepted signals with decision metadata.
No broker or order functionality exists in this module.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from shivay_alert_guard import evaluate_signal, load_policy


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, sort_keys=True)
    temporary.replace(path)


def _cooldown_key(signal: dict[str, Any]) -> str:
    return "|".join((str(signal.get("market", "")), str(signal.get("symbol", "")), str(signal.get("side", "")).upper()))


def _in_cooldown(last_sent: str | None, now: datetime, minutes: int) -> bool:
    if not last_sent:
        return False
    try:
        sent_at = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
    except ValueError:
        return False
    return now - sent_at < timedelta(minutes=minutes)


def process_signals(signals: Iterable[dict[str, Any]], policy: dict[str, Any], state_path: str | Path) -> list[dict[str, Any]]:
    state_file = Path(state_path)
    state = _load_state(state_file)
    accepted: list[dict[str, Any]] = []
    now = _utc_now()
    cooldown = int(policy["cooldown_minutes"])

    for signal in signals:
        decision = evaluate_signal(signal, policy)
        record = dict(signal)
        record["guard_score"] = decision.score
        record["guard_reasons"] = list(decision.reasons)
        record["alert_mode"] = "ALERTS_ONLY"
        if not decision.accepted:
            continue
        key = _cooldown_key(signal)
        if _in_cooldown(state.get(key), now, cooldown):
            continue
        state[key] = now.isoformat().replace("+00:00", "Z")
        record["accepted_at"] = state[key]
        accepted.append(record)

    _save_state(state_file, state)
    return accepted


def main() -> int:
    parser = argparse.ArgumentParser(description="SHIVAY alert-only runtime")
    parser.add_argument("--input", required=True, help="JSONL signal input file")
    parser.add_argument("--output", required=True, help="JSONL accepted-alert output file")
    parser.add_argument("--policy", default="alert_quality_policy.json")
    parser.add_argument("--state", default="storage/alert_cooldown_state.json")
    args = parser.parse_args()

    with Path(args.input).open("r", encoding="utf-8") as handle:
        signals = [json.loads(line) for line in handle if line.strip()]
    accepted = process_signals(signals, load_policy(args.policy), args.state)
    with Path(args.output).open("w", encoding="utf-8") as handle:
        for record in accepted:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
