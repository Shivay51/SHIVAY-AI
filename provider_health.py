"""Thread-safe provider telemetry, scoring, ranking, and circuit recovery."""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

_LOCK = threading.RLock()
_STATE: dict[str, dict[str, Any]] = {}


def _entry(name: str) -> dict[str, Any]:
    return _STATE.setdefault(name, {"provider": name, "status": "UNKNOWN", "successes": 0, "failures": 0, "consecutive_failures": 0, "last_success": None, "last_failure": None, "last_error": None, "latency_ms": None, "latency_ewma_ms": None, "freshness_score": 0.0, "completeness_score": 0.0, "consistency_score": 0.0, "reliability_score": 50.0, "quality_score": 0.0, "circuit_open_until": 0.0})


def _score(entry: dict[str, Any]) -> None:
    total = entry["successes"] + entry["failures"]
    reliability = entry["successes"] / total * 100 if total else 50.0
    latency = entry.get("latency_ewma_ms")
    latency_score = 50.0 if latency is None else max(0.0, min(100.0, 100.0 - float(latency) / 40.0))
    entry["reliability_score"] = round(reliability, 1)
    entry["quality_score"] = round(reliability * .35 + float(entry["freshness_score"]) * .25 + float(entry["completeness_score"]) * .15 + float(entry["consistency_score"]) * .15 + latency_score * .10, 1)


def record_success(name: str, latency_ms: float | None = None, quality: Mapping[str, Any] | None = None) -> None:
    with _LOCK:
        entry = _entry(name)
        latency = round(float(latency_ms), 1) if latency_ms is not None else None
        previous = entry.get("latency_ewma_ms")
        ewma = latency if previous is None else round(float(previous) * .7 + float(latency or previous) * .3, 1)
        metrics = dict(quality or {})
        entry.update(status="HEALTHY", successes=entry["successes"] + 1, consecutive_failures=0, last_success=datetime.now(timezone.utc).isoformat(), last_error=None, latency_ms=latency, latency_ewma_ms=ewma, freshness_score=float(metrics.get("freshness_score", metrics.get("score", 100))), completeness_score=float(metrics.get("completeness_score", 100)), consistency_score=float(metrics.get("consistency_score", 100)), circuit_open_until=0.0)
        _score(entry)


def record_failure(name: str, category: str = "temporary", cooldown_seconds: int = 60) -> None:
    with _LOCK:
        entry = _entry(name)
        count = entry["consecutive_failures"] + 1
        entry.update(status="UNAVAILABLE" if category == "permanent" else "DEGRADED", failures=entry["failures"] + 1, consecutive_failures=count, last_failure=datetime.now(timezone.utc).isoformat(), last_error=str(category)[:64])
        if count >= 3:
            entry["circuit_open_until"] = time.monotonic() + min(900, max(30, cooldown_seconds * count))
            entry["status"] = "CIRCUIT_OPEN"
        _score(entry)


def circuit_open(name: str) -> bool:
    with _LOCK:
        entry = _entry(name)
        opened = entry["circuit_open_until"] > time.monotonic()
        if not opened and entry["status"] == "CIRCUIT_OPEN":
            entry["status"] = "RECOVERING"
        return opened


def get_provider_health(name: str | None = None) -> dict[str, Any]:
    with _LOCK:
        return deepcopy(_entry(name)) if name else {key: deepcopy(value) for key, value in _STATE.items()}


def rank_provider_names(names: list[str], base_priority: Mapping[str, int] | None = None) -> list[str]:
    priority = dict(base_priority or {})
    with _LOCK:
        return sorted(names, key=lambda name: (circuit_open(name), -(float(_entry(name).get("quality_score", 0)) + float(priority.get(name, 0)))),)


def reset_provider_health(name: str | None = None) -> None:
    with _LOCK:
        _STATE.pop(name, None) if name else _STATE.clear()


def provider_health_summary() -> dict[str, Any]:
    state = get_provider_health()
    ranked = rank_provider_names(list(state)) if state else []
    return {"healthy": sum(value["status"] == "HEALTHY" for value in state.values()), "degraded": sum(value["status"] != "HEALTHY" for value in state.values()), "ranked": ranked, "providers": state}
