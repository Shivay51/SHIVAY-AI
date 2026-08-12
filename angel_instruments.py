"""Angel One official instrument master: download, cache, daily refresh and
verified futures-contract resolution.

This module is strictly read-only.  It downloads Angel One's public
OpenAPI scrip master, caches it on disk, and resolves internal SHIVAY symbols
into exact exchange contracts (token, trading symbol, expiry, lot size and
tick size).  It never synthesizes a contract code.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger("shivay.angel.instruments")
ROOT = Path(__file__).resolve().parent
CACHE_FILE = Path(os.getenv("ANGEL_INSTRUMENT_CACHE", str(ROOT / "angel_instrument_master.json")))
MASTER_URL = os.getenv(
    "ANGEL_INSTRUMENT_MASTER_URL",
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
)
_LOCK = threading.RLock()
_STATE: dict[str, Any] = {"rows": None, "loaded_on": None, "source": None}

FUTURE_TYPES = {"FUTIDX", "FUTSTK", "FUTCOM"}
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
# Internal SHIVAY symbol -> Angel underlying name and contract family.
FUTURES_MAP: dict[str, dict[str, str]] = {
    "NIFTY FUT": {"name": "NIFTY", "instrument_type": "FUTIDX", "exch_seg": "NFO"},
    "BANKNIFTY FUT": {"name": "BANKNIFTY", "instrument_type": "FUTIDX", "exch_seg": "NFO"},
    "FINNIFTY FUT": {"name": "FINNIFTY", "instrument_type": "FUTIDX", "exch_seg": "NFO"},
    "MIDCPNIFTY FUT": {"name": "MIDCPNIFTY", "instrument_type": "FUTIDX", "exch_seg": "NFO"},
    "MCX GOLD": {"name": "GOLD", "instrument_type": "FUTCOM", "exch_seg": "MCX"},
    "MCX SILVER": {"name": "SILVER", "instrument_type": "FUTCOM", "exch_seg": "MCX"},
    "MCX CRUDEOIL": {"name": "CRUDEOIL", "instrument_type": "FUTCOM", "exch_seg": "MCX"},
    "MCX NATURALGAS": {"name": "NATURALGAS", "instrument_type": "FUTCOM", "exch_seg": "MCX"},
    "MCX COPPER": {"name": "COPPER", "instrument_type": "FUTCOM", "exch_seg": "MCX"},
}


class AngelInstrumentError(RuntimeError):
    """The Angel instrument master is unavailable or incomplete."""


def parse_expiry(value: Any) -> date | None:
    """Parse Angel expiry strings such as ``28AUG2025`` or ISO dates."""
    text = str(value or "").strip().upper()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    if len(text) >= 9 and text[:2].isdigit() and text[2:5] in _MONTHS:
        try:
            return date(int(text[5:9]), _MONTHS[text[2:5]], int(text[:2]))
        except ValueError:
            return None
    return None


def _download(timeout: int = 30) -> list[dict[str, Any]]:
    request = Request(MASTER_URL, headers={"Accept": "application/json", "User-Agent": "shivay-ai/read-only"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise AngelInstrumentError(f"angel_instrument_master_download_failed: {type(exc).__name__}") from exc
    if not isinstance(payload, list) or not payload:
        raise AngelInstrumentError("angel_instrument_master_empty")
    return [row for row in payload if isinstance(row, dict) and row.get("token") and row.get("symbol")]


def _read_cache() -> tuple[list[dict[str, Any]] | None, str | None]:
    if not CACHE_FILE.exists():
        return None, None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        rows = payload.get("rows")
        if isinstance(rows, list) and rows:
            return rows, str(payload.get("downloaded_on") or "")
    except Exception:
        LOGGER.warning("Angel instrument cache rejected")
    return None, None


def _write_cache(rows: list[dict[str, Any]]) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps({"downloaded_on": date.today().isoformat(), "count": len(rows), "rows": rows}),
            encoding="utf-8",
        )
    except Exception:
        LOGGER.warning("Angel instrument cache could not be written")


def load_instruments(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return the Angel scrip master, refreshing at most once per calendar day."""
    today = date.today().isoformat()
    with _LOCK:
        if not force_refresh and _STATE["rows"] and _STATE["loaded_on"] == today:
            return _STATE["rows"]
        if not force_refresh:
            rows, downloaded_on = _read_cache()
            if rows and downloaded_on == today:
                _STATE.update(rows=rows, loaded_on=today, source="disk_cache")
                return rows
        try:
            rows = _download()
            _write_cache(rows)
            _STATE.update(rows=rows, loaded_on=today, source="angel_openapi")
            return rows
        except AngelInstrumentError:
            rows, downloaded_on = _read_cache()
            if rows:
                _STATE.update(rows=rows, loaded_on=downloaded_on or today, source="stale_disk_cache")
                LOGGER.warning("Angel instrument master served from stale cache")
                return rows
            raise


def refresh() -> dict[str, Any]:
    """Force a daily instrument-master refresh and report the outcome."""
    rows = load_instruments(force_refresh=True)
    return {
        "supported": True,
        "count": len(rows),
        "source": _STATE.get("source"),
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }


def status() -> dict[str, Any]:
    with _LOCK:
        rows = _STATE.get("rows")
        return {
            "loaded": bool(rows),
            "count": len(rows) if rows else 0,
            "loaded_on": _STATE.get("loaded_on"),
            "source": _STATE.get("source"),
            "cache_file_present": CACHE_FILE.exists(),
        }


def _normalized(row: dict[str, Any], internal_symbol: str, family: dict[str, str]) -> dict[str, Any]:
    expiry = parse_expiry(row.get("expiry"))
    exch_seg = str(row.get("exch_seg") or family["exch_seg"]).upper()
    mcx = exch_seg == "MCX"
    lot = row.get("lotsize")
    tick = row.get("tick_size")
    try:
        lot_size = int(float(lot)) if lot not in (None, "") else None
    except (TypeError, ValueError):
        lot_size = None
    try:
        # Angel publishes tick size in paise (e.g. 5.0 == 0.05).
        tick_size = round(float(tick) / 100, 4) if tick not in (None, "") else None
    except (TypeError, ValueError):
        tick_size = None
    token = str(row.get("token"))
    trading_symbol = str(row.get("symbol")).upper()
    return {
        "symbol": internal_symbol,
        "trading_symbol": trading_symbol,
        "contract": trading_symbol,
        "underlying": str(row.get("name") or family.get("name") or "").upper(),
        "exchange": "MCX" if mcx else "NSE",
        "angel_exchange": exch_seg,
        "segment": "MCX_COMM" if mcx else "NSE_FNO",
        "instrument_type": str(row.get("instrumenttype") or family["instrument_type"]).upper(),
        "expiry": expiry.isoformat() if expiry else None,
        "lot_size": lot_size,
        "tick_size": tick_size,
        "security_id": token,
        "angel_token": token,
        "symboltoken": token,
        "instrument_key": f"{exch_seg}:{trading_symbol}",
        "verified": True,
        "source": "angel_instrument_master",
    }


def _candidates(rows: Iterable[dict[str, Any]], family: dict[str, str]) -> list[dict[str, Any]]:
    name = family["name"]
    kind = family["instrument_type"]
    exch = family["exch_seg"].lower()
    result = []
    for row in rows:
        if str(row.get("instrumenttype") or "").upper() != kind:
            continue
        if str(row.get("name") or "").upper() != name:
            continue
        if str(row.get("exch_seg") or "").lower() != exch:
            continue
        if not str(row.get("symbol") or "").upper().endswith("FUT"):
            continue
        expiry = parse_expiry(row.get("expiry"))
        if expiry is None:
            continue
        result.append((expiry, row))
    result.sort(key=lambda item: item[0])
    return [row for _, row in result]


def resolve_futures_contract(symbol: str, on_date: date | None = None, force_refresh: bool = False) -> dict[str, Any] | None:
    """Resolve the nearest non-expired futures contract for an internal symbol."""
    key = str(symbol or "").strip().upper()
    family = FUTURES_MAP.get(key)
    if family is None and key.endswith(" FUT"):
        family = {"name": key.removesuffix(" FUT").strip(), "instrument_type": "FUTSTK", "exch_seg": "NFO"}
    if family is None:
        return None
    today = on_date or datetime.now(timezone.utc).date()
    rows = load_instruments(force_refresh=force_refresh)
    for row in _candidates(rows, family):
        expiry = parse_expiry(row.get("expiry"))
        if expiry and expiry >= today:
            return _normalized(row, key, family)
    return None


def resolve_equity(symbol: str, force_refresh: bool = False) -> dict[str, Any] | None:
    """Resolve an NSE cash-segment instrument for contextual reads."""
    key = str(symbol or "").strip().upper().removesuffix(".NS")
    if not key:
        return None
    wanted = f"{key}-EQ"
    for row in load_instruments(force_refresh=force_refresh):
        if str(row.get("exch_seg") or "").lower() != "nse_cm":
            continue
        if str(row.get("symbol") or "").upper() != wanted:
            continue
        token = str(row.get("token"))
        try:
            tick_size = round(float(row.get("tick_size") or 5) / 100, 4)
        except (TypeError, ValueError):
            tick_size = 0.05
        return {
            "symbol": key,
            "trading_symbol": wanted,
            "contract": wanted,
            "underlying": key,
            "exchange": "NSE",
            "angel_exchange": "NSE",
            "segment": "CASH",
            "instrument_type": "EQUITY",
            "expiry": None,
            "lot_size": 1,
            "tick_size": tick_size,
            "security_id": token,
            "angel_token": token,
            "symboltoken": token,
            "instrument_key": f"NSE:{wanted}",
            "verified": True,
            "source": "angel_instrument_master",
        }
    return None


def resolve(symbol: str, force_refresh: bool = False) -> dict[str, Any] | None:
    """Resolve any supported internal symbol to an exact Angel contract."""
    key = str(symbol or "").strip().upper()
    if key.endswith(" FUT") or key in FUTURES_MAP:
        return resolve_futures_contract(key, force_refresh=force_refresh)
    return resolve_equity(key, force_refresh=force_refresh)


def supported_symbols() -> tuple[str, ...]:
    return tuple(FUTURES_MAP)


def daily_refresh_due(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        return _STATE.get("loaded_on") != now.date().isoformat()


def seconds_since_load() -> float | None:
    with _LOCK:
        return None if _STATE.get("rows") is None else time.monotonic()
