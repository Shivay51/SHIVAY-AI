"""Safe, project-local runtime configuration for the Yahoo emergency feed."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("shivay.provider.yahoo")
_CONFIGURED = False


def configure_yfinance(module: Any) -> bool:
    global _CONFIGURED
    if _CONFIGURED:
        return True
    try:
        location = Path(__file__).resolve().parent / "cache" / "yfinance"
        location.mkdir(parents=True, exist_ok=True)
        setter = getattr(module, "set_tz_cache_location", None)
        if callable(setter):
            setter(str(location))
        _CONFIGURED = True
        return True
    except Exception as error:
        LOGGER.warning("Yahoo cache setup unavailable: %s", type(error).__name__)
        return False

