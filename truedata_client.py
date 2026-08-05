"""Read-only adapter around TrueData's official Python SDK."""
from __future__ import annotations

import importlib.util
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any


class TrueDataError(RuntimeError):
    pass


class TrueDataClient:
    def __init__(self, sdk: Any = None):
        self.username = os.getenv("TRUEDATA_USERNAME", "").strip()
        self.password = os.getenv("TRUEDATA_PASSWORD", "").strip()
        self.enabled = os.getenv("ENABLE_TRUEDATA", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.trial_expires_at = self._expiry(os.getenv("TRUEDATA_TRIAL_EXPIRES_AT", ""))
        self.trial_expired = bool(self.trial_expires_at and self.trial_expires_at <= datetime.now(timezone.utc))
        self.sdk_installed = sdk is not None or importlib.util.find_spec("truedata") is not None
        self.configured = bool(self.enabled and self.username and self.password and self.sdk_installed and not self.trial_expired)
        self._sdk = sdk; self.live = None; self.hist = None; self._lock = threading.RLock()

    @staticmethod
    def _expiry(value: str) -> datetime | None:
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    def connect(self) -> bool:
        if not self.configured:
            raise TrueDataError("TrueData is not configured")
        with self._lock:
            if self.live is not None and self.hist is not None:
                return True
            try:
                sdk = self._sdk
                if sdk is None:
                    import truedata as sdk
                self.live = sdk.TD_live(self.username, self.password, log_level=logging.WARNING)
                self.hist = sdk.TD_hist(self.username, self.password, log_level=logging.WARNING)
                return True
            except Exception as error:
                self.live = self.hist = None
                raise TrueDataError(f"TrueData authentication failed ({type(error).__name__})") from None

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict): return dict(value)
        if hasattr(value, "to_dict"):
            result = value.to_dict(); return dict(result) if isinstance(result, dict) else {}
        return {name: getattr(value, name) for name in dir(value) if not name.startswith("_") and not callable(getattr(value, name, None))}

    def quote(self, identifier: str) -> dict[str, Any]:
        self.connect()
        try:
            request_ids = self.live.start_live_data([identifier])
            request_id = request_ids[0] if isinstance(request_ids, (list, tuple)) else request_ids
            value = self.live.live_data.get(request_id)
            if value is None: value = self.live.live_data.get(identifier)
            if value is None: raise TrueDataError("TrueData quote is not ready")
            return self._object(value)
        except TrueDataError: raise
        except Exception as error: raise TrueDataError(f"TrueData quote failed ({type(error).__name__})") from None

    def history(self, identifier: str, interval: int = 5, bars: int = 160) -> Any:
        self.connect()
        allowed = {1, 2, 3, 5, 10, 15, 30, 60}
        interval = interval if interval in allowed else 5
        try:
            return self.hist.get_n_historical_bars(identifier, no_of_bars=max(20, min(int(bars), 1000)), bar_size=f"{interval} min")
        except Exception as error: raise TrueDataError(f"TrueData history failed ({type(error).__name__})") from None

    def health(self) -> dict[str, Any]:
        return {"configured": self.configured, "sdk_installed": self.sdk_installed,
                "trial_expired": self.trial_expired,
                "trial_expires_at": self.trial_expires_at.isoformat() if self.trial_expires_at else None,
                "authenticated": bool(self.live and self.hist), "checked_at": datetime.now(timezone.utc).isoformat()}

    def close(self) -> None:
        with self._lock:
            for client in (self.live, self.hist):
                try:
                    if callable(getattr(client, "disconnect", None)): client.disconnect()
                except Exception: pass
            self.live = self.hist = None
