"""Historical win-rate feedback for signal ranking.

The bot already records the outcome of every completed trade (WIN/LOSS) in the
trade journal, and :mod:`accuracy_analyzer` already aggregates those outcomes
into per-setup / per-symbol / per-side win-rates. Until now that information was
report-only: two signals with identical live indicators ranked the same even if
one setup had historically won 70% of the time and the other only 35%.

This module closes that loop. It turns the historical win-rate into a bounded,
*self-neutralizing* adjustment to a signal's quality score:

* It returns **exactly 0.0** (no effect) until there are enough real outcomes,
  so live grading is unchanged until the bot has a genuine track record. Nothing
  about today's behaviour changes on day one.
* Once a setup/symbol has a trustworthy sample, signals from historically
  *winning* setups are nudged up and *losing* setups are nudged down, measured
  relative to the system's own average win-rate (its baseline), not an arbitrary
  50%.
* The adjustment is hard-capped (``MAX_ADJUSTMENT`` points) so a noisy or small
  sample can never dominate the live technical read.

Everything here is local (reads the journal, cached) and defensive: any failure
degrades to a neutral 0.0 adjustment, never an exception into the ranker.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from accuracy_analyzer import analyze_accuracy

# --- Tunables ---------------------------------------------------------------
# Overall completed-trade floor before any feedback is applied. Mirrors
# accuracy_analyzer's own ``sufficient_sample`` threshold.
MIN_TOTAL_OUTCOMES = 50
# Per-key (per-setup / per-symbol) sample floor before that key's win-rate is
# trusted. A setup seen only a handful of times is ignored (edge stays neutral).
MIN_GROUP_TRADES = 20
# Maximum absolute swing, in quality points, the historical edge may apply.
MAX_ADJUSTMENT = 8.0
# Win-rate gap (in percentage points, vs baseline) that maps to the full swing.
FULL_SWING_WINRATE_GAP = 25.0
# Recompute the journal aggregation at most this often (seconds). The scanner
# ranks several signals per cycle; this keeps it to one journal read per window.
CACHE_TTL_SECONDS = 300

_cache: dict[str, Any] = {"at": 0.0, "data": None}


def refresh() -> None:
    """Force the next lookup to re-read and re-aggregate the journal."""
    _cache["data"] = None
    _cache["at"] = 0.0


def _stats(now: float | None = None) -> dict[str, Any]:
    timestamp = now if now is not None else time.time()
    if _cache["data"] is None or timestamp - float(_cache["at"]) > CACHE_TTL_SECONDS:
        try:
            _cache["data"] = analyze_accuracy()
        except Exception:
            _cache["data"] = {"sample_size": 0, "win_rate": 0.0, "groups": {}}
        _cache["at"] = timestamp
    return _cache["data"]


def _group_win_rate(groups: Mapping[str, Any], key: Any) -> tuple[float, float] | None:
    """Return ``(win_rate, trades)`` for a key, or None if not trustworthy."""
    if not key:
        return None
    group = groups.get(str(key).upper())
    if not isinstance(group, Mapping):
        return None
    trades = float(group.get("trades", 0) or 0)
    if trades < MIN_GROUP_TRADES:
        return None
    return float(group.get("win_rate", 0.0) or 0.0), trades


def setup_edge(
    setup: Any = None,
    symbol: Any = None,
    side: Any = None,
    *,
    stats: Mapping[str, Any] | None = None,
) -> float:
    """Bounded quality adjustment in ``[-MAX_ADJUSTMENT, +MAX_ADJUSTMENT]``.

    Returns ``0.0`` (neutral, no-op) whenever there is not yet enough history —
    which is the case until the bot has accumulated a real track record.
    """
    data = stats if stats is not None else _stats()
    if float(data.get("sample_size", 0) or 0) < MIN_TOTAL_OUTCOMES:
        return 0.0
    groups = data.get("groups", {})
    if not isinstance(groups, Mapping):
        return 0.0
    baseline = float(data.get("win_rate", 0.0) or 0.0)

    # Collect trustworthy per-key win-rates (setup and symbol are the two keys
    # meaningful for a live signal; side is folded into the setup grouping).
    samples: list[tuple[float, float]] = []
    for key in (setup, symbol):
        result = _group_win_rate(groups, key)
        if result is not None:
            samples.append(result)
    if not samples:
        return 0.0

    total_trades = sum(trades for _, trades in samples)
    if total_trades <= 0:
        return 0.0
    win_rate = sum(rate * trades for rate, trades in samples) / total_trades

    edge = (win_rate - baseline) / FULL_SWING_WINRATE_GAP
    edge = max(-1.0, min(1.0, edge))
    return round(edge * MAX_ADJUSTMENT, 2)


def signal_edge(signal: Mapping[str, Any], *, stats: Mapping[str, Any] | None = None) -> float:
    """Convenience wrapper: extract keys from a live signal mapping."""
    if not isinstance(signal, Mapping):
        return 0.0
    return setup_edge(
        signal.get("setup") or signal.get("setup_type"),
        signal.get("symbol"),
        signal.get("side") or signal.get("decision"),
        stats=stats,
    )
