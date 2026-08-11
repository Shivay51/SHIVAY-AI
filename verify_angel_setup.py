from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    required = ("ENABLE_ANGELONE", "ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    enabled = os.getenv("ENABLE_ANGELONE", "").strip().lower() in {"1", "true", "yes", "on"}
    if missing or not enabled:
        print("ANGEL VERIFY: FAIL")
        print("Missing/disabled: " + ", ".join((["ENABLE_ANGELONE=true"] if not enabled else []) + missing))
        return 2
    try:
        from angelone_provider import AngelOneProvider
        provider = AngelOneProvider()
        health = provider.health_check()
        if not health.get("configured") or not health.get("available"):
            print("ANGEL VERIFY: FAIL")
            print("Provider is not configured or available.")
            return 3
        print("ANGEL VERIFY: CONFIGURATION PASS")
        print("Live candle login is checked only after the runtime manager registration patch.")
        return 0
    except Exception as error:
        print("ANGEL VERIFY: FAIL")
        print(type(error).__name__ + ": " + str(error))
        return 4


if __name__ == "__main__":
    sys.exit(main())
