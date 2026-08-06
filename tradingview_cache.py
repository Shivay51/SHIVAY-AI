"""Persistent, thread-safe multi-timeframe cache for accepted TradingView bars."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

from chandelier_exit import calculate_chandelier_exit
from indicators import calculate_indicator_snapshot
from tradingview_payload import TradingViewPayload

LOGGER = logging.getLogger("shivay.tradingview.cache")
ROOT = Path(__file__).resolve().parent


class TradingViewCache:
    def __init__(self, max_bars: int = 600, persistence_path: str | os.PathLike[str] | None = None):
        self.max_bars = max(200, min(int(max_bars), 5000))
        self._bars: dict[tuple[str, str, str, int], deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=self.max_bars))
        self._latest: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.accepted_count = 0
        self.persistence_path = Path(persistence_path).resolve() if persistence_path else None
        if self.persistence_path:
            self._load()

    @staticmethod
    def _key(item: dict[str, Any]) -> tuple[str, str, str, int]:
        return str(item["symbol"]), str(item.get("contract_text", "")), str(item["category"]), int(item["timeframe"])

    @staticmethod
    def _serializable(item: dict[str, Any]) -> dict[str, Any]:
        return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in item.items() if key != "secret"}

    @staticmethod
    def _restore(item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        for name in ("bar_timestamp", "generated_at", "received_at"):
            value = result.get(name)
            if isinstance(value, str):
                result[name] = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        return result

    def _load(self) -> None:
        path = self.persistence_path
        if not path or not path.is_file():
            return
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            rows = document.get("candles", []) if isinstance(document, dict) else []
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                item = self._restore(raw)
                key = self._key(item)
                self._bars[key].append(item)
                self._latest[key] = item
            self.accepted_count = int(document.get("accepted_count", len(rows))) if isinstance(document, dict) else len(rows)
        except Exception as error:
            LOGGER.warning("TradingView candle persistence was ignored safely: %s", type(error).__name__)

    def _persist(self) -> None:
        path = self.persistence_path
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        production_store = path.name.lower() == "tradingview_candles.json"
        rows = [self._serializable(item) for bars in self._bars.values() for item in bars
                if not (production_store and str(item.get("symbol", "")).upper().startswith("TEST:"))]
        document = {"version": 1, "accepted_count": len(rows), "candles": rows}
        handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(document, stream, separators=(",", ":"), ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(4):
                try:
                    os.replace(temp_name, path)
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _enrich(self, key: tuple[str, str, str, int]) -> None:
        bars = self._bars[key]
        if not bars:
            return
        latest = bars[-1]
        # Legacy Pine values remain available for migration tests. Standard-alert
        # values are always computed locally from the accepted candle history.
        snapshot = calculate_indicator_snapshot(list(bars))
        if not latest.get("indicators_supplied"):
            latest.update(snapshot)
        else:
            latest["usable_candles"] = len(bars)
            latest["state"] = "READY" if len(bars) >= 200 else "PARTIAL" if len(bars) >= 14 else "WARMING UP"
        chandelier = calculate_chandelier_exit(
            [row["high"] for row in bars], [row["low"] for row in bars], [row["close"] for row in bars]
        )
        previous_chandelier = calculate_chandelier_exit(
            [row["high"] for row in list(bars)[:-1]], [row["low"] for row in list(bars)[:-1]], [row["close"] for row in list(bars)[:-1]]
        ) if len(bars) > 1 else {"valid": False}
        latest.update(
            chandelier_long_stop=chandelier.get("long_stop") or latest.get("chandelier_long_stop", 0.0),
            chandelier_short_stop=chandelier.get("short_stop") or latest.get("chandelier_short_stop", 0.0),
            chandelier_direction=chandelier.get("trend") if chandelier.get("valid") else latest.get("chandelier_direction", "SIDEWAYS"),
            chandelier_direction_changed=bool(chandelier.get("valid") and previous_chandelier.get("valid")
                                               and chandelier.get("direction") != previous_chandelier.get("direction")),
        )
        if latest.get("source") in {"TRADINGVIEW_STANDARD_ALERT", "TRADINGVIEW_STANDARD_ALERT_BRIDGE"}:
            setup_buy = bool(snapshot.get("bullish_breakout") or snapshot.get("bullish_retest") or snapshot.get("bullish_pullback"))
            setup_sell = bool(snapshot.get("bearish_breakdown") or snapshot.get("bearish_retest") or snapshot.get("bearish_pullback"))
            latest["preliminary_buy"] = setup_buy and snapshot.get("trend_state") == "BULLISH"
            latest["preliminary_sell"] = setup_sell and snapshot.get("trend_state") == "BEARISH"
            latest["setup_valid"] = bool(latest["preliminary_buy"] != latest["preliminary_sell"])
        self._latest[key] = latest

    def put(self, payload: TradingViewPayload) -> str:
        item = payload.to_dict()
        item["received_at"] = datetime.now(timezone.utc)
        item["source"] = payload.source
        item["validation_status"] = "ACCEPTED"
        key = self._key(item)
        outcome = "accepted"
        with self._lock:
            bars = self._bars[key]
            if bars and bars[-1]["bar_timestamp"] == payload.bar_timestamp:
                # Corrections are safe only for the same completed candle and
                # contract. The newest accepted event replaces that candle.
                bars[-1] = item
                outcome = "corrected"
            elif bars and payload.bar_timestamp < bars[-1]["bar_timestamp"]:
                return "out_of_order"
            else:
                bars.append(item)
            self.accepted_count += 1
            self._enrich(key)
            self._persist()
        return outcome

    def _keys(self, symbol: str, timeframe: int, contract_text: str | None = None, category: str | None = None) -> list[tuple[str, str, str, int]]:
        return [key for key in self._latest if key[0] == symbol and key[3] == timeframe and (contract_text is None or key[1] == contract_text) and (category is None or key[2] == category)]

    def latest(self, symbol: str, timeframe: int, contract_text: str | None = None, category: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            keys = self._keys(symbol, timeframe, contract_text, category)
            if not keys or (contract_text is None and len({(key[1], key[2]) for key in keys}) > 1):
                return None
            key = max(keys, key=lambda item: self._latest[item]["generated_at"])
            return dict(self._latest[key])

    def bars(self, symbol: str, timeframe: int, contract_text: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            keys = self._keys(symbol, timeframe, contract_text, category)
            if not keys or (contract_text is None and len({(key[1], key[2]) for key in keys}) > 1):
                return []
            key = max(keys, key=lambda item: self._latest[item]["generated_at"])
            return [dict(item) for item in self._bars[key]]

    def aligned(self, symbol: str, timeframes=(5, 15, 30, 60), max_skew_seconds: int = 3900, contract_text: str | None = None, category: str | None = None) -> dict[int, dict[str, Any]] | None:
        anchor = self.latest(symbol, max(timeframes), contract_text, category)
        if not anchor:
            return None
        contract_text, category = anchor["contract_text"], anchor["category"]
        values = {tf: self.latest(symbol, tf, contract_text, category) for tf in timeframes}
        if any(value is None for value in values.values()):
            return None
        stamps = [value["generated_at"] for value in values.values() if value]
        return values if (max(stamps) - min(stamps)).total_seconds() <= max_skew_seconds else None

    def warmup_state(self, symbol: str, timeframe: int, contract_text: str | None = None, category: str | None = None) -> dict[str, Any]:
        rows = self.bars(symbol, timeframe, contract_text, category)
        if not rows:
            return {"state": "NO DATA", "candles": 0, "required": 200}
        latest = rows[-1]
        age = (datetime.now(timezone.utc) - latest["generated_at"]).total_seconds()
        freshness_limit = max(180, timeframe * 120)
        state = (
            "STALE" if age > freshness_limit
            else "READY" if len(rows) >= 200
            else "PARTIAL" if len(rows) >= 14
            else "WARMING UP"
        )
        return {"state": state, "candles": len(rows), "required": 200, "age_seconds": max(0.0, age)}

    def status(self) -> dict[str, Any]:
        with self._lock:
            states = {"NO DATA": 0, "WARMING UP": 0, "PARTIAL": 0, "READY": 0, "STALE": 0, "DEGRADED": 0}
            for key in self._latest:
                state = self.warmup_state(key[0], key[3], key[1], key[2])["state"]
                states[state] = states.get(state, 0) + 1
            return {"accepted": self.accepted_count, "streams": len(self._latest), "contracts": len({(key[0], key[1], key[2]) for key in self._latest}),
                    "last_received": max((item["received_at"] for item in self._latest.values()), default=None), "states": states,
                    "persistent": bool(self.persistence_path)}


_CACHE = TradingViewCache(persistence_path=os.getenv("TRADINGVIEW_CANDLE_STORE", str(ROOT / "storage" / "tradingview_candles.json")))


def get_tradingview_cache() -> TradingViewCache:
    return _CACHE
