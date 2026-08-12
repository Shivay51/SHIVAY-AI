"""Angel One instrument master (scrip master) loader and contract resolver.

Angel publishes the complete tradable universe as a single public JSON file. This
module downloads it, caches it on disk, and resolves the *current* NSE F&O and
MCX contracts SHIVAY AI trades, so contract codes are never synthesized.

Resolution rules
----------------
* ``NIFTY FUT`` / ``BANKNIFTY FUT`` -> nearest non-expired NFO FUTIDX contract.
* ``MCX GOLD`` / ``MCX SILVER``     -> nearest non-expired MCX FUTCOM contract
  for the full-size GOLD / SILVER series (not MINI/PETAL/GUINEA/M variants).
* ``<STOCK> FUT``                   -> nearest non-expired NFO FUTSTK contract.
* Cash equity / index                -> NSE spot row.

Every resolved contract carries expiry, lot size, tick size, security id and
instrument key so ``data_quality.validate_instrument_contract`` can verify it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger("shivay.angel.instruments")

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "angel_instruments_cache.json"
SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)
CACHE_TTL_SECONDS = 6 * 60 * 60

INDEX_UNDERLYINGS = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY"}
COMMODITY_UNDERLYINGS = {"GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER"}
# Reject reduced-lot commodity series so MCX GOLD means the full GOLD contract.
COMMODITY_VARIANT_TOKENS = ("MINI", "PETAL", "GUINEA", "M ", "TEN", "MIC")

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {"rows": None, "loaded_at": 0.0, "source": None}

_EXPIRY_FORMATS = ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d%b%y")


class AngelInstrumentError(RuntimeError):
    """The Angel instrument master could not be loaded or the contract is unknown."""


def _to_float(value: Any) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def parse_expiry(value: Any) -> date | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def today_ist() -> date:
    return datetime.now(IST).date()


def _download() -> list[dict[str, Any]]:
    request = Request(SCRIP_MASTER_URL, headers={"User-Agent": "SHIVAY-AI/1.0"})
    timeout = int(os.getenv("ANGEL_INSTRUMENT_TIMEOUT", "30") or 30)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS host
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        raise AngelInstrumentError("angel_scrip_master_empty")
    return [row for row in payload if isinstance(row, dict)]


def _read_cache() -> tuple[list[dict[str, Any]] | None, float]:
    if not CACHE_FILE.exists():
        return None, 0.0
    try:
        blob = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        rows = blob.get("rows")
        if isinstance(rows, list) and rows:
            return rows, float(blob.get("saved_at") or 0.0)
    except Exception:
        LOGGER.warning("Angel instrument cache rejected; will refetch")
    return None, 0.0


def _write_cache(rows: list[dict[str, Any]]) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps({"saved_at": time.time(), "count": len(rows), "rows": rows}),
            encoding="utf-8",
        )
    except OSError:
        LOGGER.warning("Angel instrument cache could not be written")


def load_instruments(force: bool = False) -> list[dict[str, Any]]:
    """Return scrip-master rows, refreshing from Angel when the cache is stale."""
    with _LOCK:
        rows = _STATE.get("rows")
        age = time.time() - float(_STATE.get("loaded_at") or 0.0)
        if rows and not force and age < CACHE_TTL_SECONDS:
            return rows

        if not force:
            cached, saved_at = _read_cache()
            if cached and time.time() - saved_at < CACHE_TTL_SECONDS:
                _STATE.update(rows=cached, loaded_at=saved_at, source="disk_cache")
                return cached

        try:
            fresh = _download()
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            cached, saved_at = _read_cache()
            if cached:
                LOGGER.warning("Angel scrip master download failed; using stale cache")
                _STATE.update(rows=cached, loaded_at=saved_at, source="stale_cache")
                return cached
            raise AngelInstrumentError(f"angel_scrip_master_unavailable: {exc}") from exc

        _write_cache(fresh)
        _STATE.update(rows=fresh, loaded_at=time.time(), source="angel_api")
        return fresh


def reset_cache() -> None:
    with _LOCK:
        _STATE.update(rows=None, loaded_at=0.0, source=None)


def master_status() -> dict[str, Any]:
    with _LOCK:
        rows = _STATE.get("rows") or []
        return {
            "loaded": bool(rows),
            "count": len(rows),
            "source": _STATE.get("source"),
            "age_seconds": round(time.time() - float(_STATE.get("loaded_at") or 0.0), 1)
            if _STATE.get("loaded_at")
            else None,
            "cache_file_present": CACHE_FILE.exists(),
        }


def _normalize(row: dict[str, Any], symbol: str, segment: str, instrument_type: str) -> dict[str, Any]:
    expiry = parse_expiry(row.get("expiry"))
    lot_size = _to_float(row.get("lotsize"))
    tick_size = _to_float(row.get("tick_size"))
    if tick_size is not None and tick_size > 1:
        # Angel publishes tick size in paise for many segments.
        tick_size = tick_size / 100
    return {
        "symbol": symbol,
        "trading_symbol": str(row.get("symbol") or "").strip(),
        "exchange": str(row.get("exch_seg") or "").strip().upper(),
        "segment": segment,
        "instrument_type": instrument_type,
        "expiry": expiry.isoformat() if expiry else None,
        "lot_size": int(lot_size) if lot_size else None,
        "tick_size": tick_size,
        "security_id": str(row.get("token") or "").strip() or None,
        "instrument_key": f"{str(row.get('exch_seg') or '').upper()}|{row.get('token')}",
        "angel_token": str(row.get("token") or "").strip() or None,
        "underlying": str(row.get("name") or "").strip().upper(),
        "verified": True,
        "source": "angel_instrument_master",
    }


def _nearest(rows: Iterable[dict[str, Any]], on_date: date) -> dict[str, Any] | None:
    dated = []
    for row in rows:
        expiry = parse_expiry(row.get("expiry"))
        if expiry and expiry >= on_date:
            dated.append((expiry, row))
    if not dated:
        return None
    dated.sort(key=lambda item: item[0])
    return dated[0][1]


def _is_full_size_commodity(trading_symbol: str, underlying: str) -> bool:
    text = trading_symbol.upper()
    prefix = text.split(underlying, 1)[0] if underlying in text else text
    remainder = text.replace(underlying, "", 1)
    for token in COMMODITY_VARIANT_TOKENS:
        if token.strip() and token.strip() in prefix + remainder[:6]:
            return False
    return True


def resolve_contract(symbol: str, on_date: date | None = None, force: bool = False) -> dict[str, Any] | None:
    """Resolve ``symbol`` to a current, non-expired Angel contract."""
    key = str(symbol or "").strip().upper()
    if not key:
        return None
    on_date = on_date or today_ist()
    rows = load_instruments(force=force)

    wants_mcx = key.startswith("MCX ") or key.removesuffix(" FUT").replace("MCX", "").strip() in COMMODITY_UNDERLYINGS
    wants_future = key.endswith(" FUT") or wants_mcx
    base = key.removesuffix(" FUT").replace("MCX", "").strip()

    if wants_mcx:
        underlying = base
        candidates = [
            row
            for row in rows
            if str(row.get("exch_seg") or "").upper() == "MCX"
            and str(row.get("instrumenttype") or "").upper() == "FUTCOM"
            and str(row.get("name") or "").upper() == underlying
            and _is_full_size_commodity(str(row.get("symbol") or ""), underlying)
        ]
        chosen = _nearest(candidates, on_date)
        if not chosen:
            return None
        return _normalize(chosen, key, "MCX_COMM", "FUTCOM")

    if wants_future:
        underlying = INDEX_UNDERLYINGS.get(base, base)
        is_index = base in INDEX_UNDERLYINGS
        wanted_type = "FUTIDX" if is_index else "FUTSTK"
        candidates = [
            row
            for row in rows
            if str(row.get("exch_seg") or "").upper() == "NFO"
            and str(row.get("instrumenttype") or "").upper() == wanted_type
            and str(row.get("name") or "").upper() == underlying
        ]
        chosen = _nearest(candidates, on_date)
        if not chosen:
            return None
        contract = _normalize(chosen, key, "NSE_FNO", wanted_type)
        # data_quality requires exchange NSE for NSE futures; NFO is the NSE F&O segment.
        contract["exchange"] = "NSE"
        contract["angel_exchange"] = "NFO"
        return contract

    candidates = [
        row
        for row in rows
        if str(row.get("exch_seg") or "").upper() == "NSE"
        and str(row.get("name") or "").upper() == base
        and str(row.get("symbol") or "").upper().endswith("-EQ")
    ]
    if candidates:
        contract = _normalize(candidates[0], key, "CASH", "EQUITY")
        contract["angel_exchange"] = "NSE"
        return contract

    index_rows = [
        row
        for row in rows
        if str(row.get("exch_seg") or "").upper() == "NSE"
        and str(row.get("symbol") or "").upper().replace(" ", "") == base.replace(" ", "")
    ]
    if index_rows:
        contract = _normalize(index_rows[0], key, "INDEX", "INDEX")
        contract["angel_exchange"] = "NSE"
        return contract
    return None


def refresh() -> dict[str, Any]:
    """Force a scrip-master refresh and resolve the core SHIVAY AI universe."""
    load_instruments(force=True)
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for symbol in ("NIFTY FUT", "BANKNIFTY FUT", "MCX GOLD", "MCX SILVER"):
        try:
            contract = resolve_contract(symbol)
        except AngelInstrumentError:
            contract = None
        if contract:
            resolved[symbol] = contract
        else:
            missing.append(symbol)
    status = master_status()
    return {
        "supported": True,
        "source": "angel_instrument_master",
        "count": status["count"],
        "resolved": len(resolved),
        "missing": missing,
        "instruments": resolved,
    }
