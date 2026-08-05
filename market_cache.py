"""Short-lived cache of verified Indian index-futures context.

Yahoo remains available elsewhere as an explicitly delayed emergency source, but
it is never accepted here as an Indian futures feed or used to authorize trades.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import pandas as pd

LOGGER = logging.getLogger("shivay.market_cache")
CACHE_TIME = 300
_market_cache: dict[str, Any] | None = None
_last_update = 0.0


def _verified_future(value: Mapping[str, Any] | None, instrument_type: str) -> bool:
    if not value or not value.get("verified"):
        return False
    quality = value.get("data_quality")
    quality_ok = isinstance(quality, Mapping) and bool(quality.get("valid"))
    return (
        quality_ok
        and str(value.get("exchange", "")).upper() == "NSE"
        and str(value.get("segment", "")).upper() == "NSE_FNO"
        and str(value.get("instrument_type", "")).upper() == instrument_type
        and bool(value.get("is_live"))
        and not bool(value.get("is_delayed"))
        and not bool(value.get("is_stale"))
    )


def _metrics(value: Mapping[str, Any]) -> tuple[float, float, float]:
    close = pd.Series(value.get("close", []), dtype="float64").dropna()
    if len(close) < 50:
        raise ValueError("insufficient_verified_futures_history")
    price = float(value.get("price", close.iloc[-1]))
    ema20 = float(close.ewm(span=20, adjust=False, min_periods=20).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False, min_periods=50).mean().iloc[-1])
    if min(price, ema20, ema50) <= 0:
        raise ValueError("invalid_verified_futures_values")
    return price, ema20, ema50


def load_market_cache() -> dict[str, Any] | None:
    global _market_cache, _last_update
    now = time.time()
    if _market_cache is not None and now - _last_update < CACHE_TIME:
        return _market_cache
    try:
        from provider_manager import get_provider_manager

        values = get_provider_manager().get_verified_many(["NIFTY FUT", "BANKNIFTY FUT"], period="5d", interval="5m")
        nifty, bank = values.get("NIFTY FUT"), values.get("BANKNIFTY FUT")
        if not _verified_future(nifty, "FUTIDX") or not _verified_future(bank, "FUTIDX"):
            LOGGER.info("Verified live NSE index futures are unavailable; market context disabled")
            _market_cache = None
            _last_update = now
            return None
        nifty_price, nifty_ema20, nifty_ema50 = _metrics(nifty)
        bank_price, bank_ema20, bank_ema50 = _metrics(bank)
        nifty_bullish, bank_bullish = nifty_price > nifty_ema20, bank_price > bank_ema20
        if nifty_bullish and bank_bullish:
            direction = "BULLISH"
        elif not nifty_bullish and not bank_bullish:
            direction = "BEARISH"
        else:
            direction = "SIDEWAYS"
        strength = sum((
            25 if nifty_price > nifty_ema20 else 0,
            25 if nifty_price > nifty_ema50 else 0,
            25 if bank_price > bank_ema20 else 0,
            25 if bank_price > bank_ema50 else 0,
        ))
        _market_cache = {
            "market_direction": direction, "market_strength": strength,
            "nifty_price": nifty_price, "bank_price": bank_price,
            "nifty_ema20": nifty_ema20, "nifty_ema50": nifty_ema50,
            "bank_ema20": bank_ema20, "bank_ema50": bank_ema50,
            "nifty_bullish": nifty_bullish, "bank_bullish": bank_bullish,
            "verified": True, "is_live": True,
        }
        _last_update = now
        LOGGER.info("Verified futures context refreshed: direction=%s strength=%s", direction, strength)
        return _market_cache
    except Exception as error:
        _market_cache = None
        _last_update = now
        LOGGER.warning("Market context refresh failed safely: %s", type(error).__name__)
        return None


def is_market_bullish() -> bool:
    data = load_market_cache()
    return bool(data and data["market_direction"] != "BEARISH")


def get_market_direction() -> str:
    data = load_market_cache()
    return str(data["market_direction"]) if data else "UNKNOWN"


def get_market_strength() -> int:
    data = load_market_cache()
    return int(data["market_strength"]) if data else 0


def clear_market_cache() -> None:
    global _market_cache, _last_update
    _market_cache = None
    _last_update = 0.0
