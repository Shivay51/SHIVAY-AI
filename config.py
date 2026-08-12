import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except Exception:
    pass


def _environment_float(*names, default=0.0):
    for name in names:
        value = os.getenv(name)
        if value:
            try:
                return float(value.strip())
            except (TypeError, ValueError):
                continue
    return float(default)

# ==========================================
# SHIVAY AI PRO v2
# CONFIGURATION
# ==========================================


# ==========================================
# AUTO SCANNER
# ==========================================

# Primary strategy runs on completed 15-minute candles.
PRIMARY_TIMEFRAME_MINUTES = 15
CONFIRMATION_TIMEFRAMES_MINUTES = (30, 60)
ENTRY_TIMING_TIMEFRAME_MINUTES = 5
SCAN_INTERVAL = 300
MAX_SETUP_AGE_CANDLES = 2
ENTRY_VALIDITY_MINUTES = 18
CHANDELIER_SIGNAL_TIMEFRAME = "15m"
CHANDELIER_CONFIRM_TIMEFRAME = "30m"
CHANDELIER_TREND_TIMEFRAME = "60m"


# ==========================================
# MARKET TIMING
# ==========================================

MARKET_START_HOUR = 9
MARKET_START_MINUTE = 15

MARKET_END_HOUR = 15
MARKET_END_MINUTE = 30
MCX_START_HOUR = 9
MCX_START_MINUTE = 0
MCX_END_HOUR = 23
MCX_END_MINUTE = 30


# ==========================================
# AI SCANNER
# ==========================================

# Minimum Score Required
MIN_SCORE = 75

# Maximum Signals
MAX_TRADES = 5


# ==========================================
# RISK MANAGEMENT
# ==========================================

MAX_RISK = 2.0

MIN_RISK_REWARD = max(1.0, _environment_float("MIN_RISK_REWARD", default=2.0))


# ==========================================
# AI FILTERS
# ==========================================

ENABLE_MARKET_FILTER = True

ENABLE_BREAKOUT = True

ENABLE_PULLBACK = True

ENABLE_VOLUME_FILTER = True

ENABLE_SUPPORT_RESISTANCE = True

ENABLE_MULTI_TIMEFRAME = True


# ==========================================
# TELEGRAM
# ==========================================

SEND_ONLY_NEW_SIGNALS = True

SEND_TARGET_ALERT = True

SEND_STOPLOSS_ALERT = True

SEND_MARKET_STATUS = True


# ==========================================
# MARKET CACHE
# ==========================================

CACHE_TIME = 300


# ==========================================
# DEBUG
# ==========================================

DEBUG = False


# ==========================================
# VERSION
# ==========================================

BOT_NAME = "SHIVAY AI PRO"

BOT_VERSION = "2.0"
SIGNAL_CONFIG_VERSION = "2026.08-observation-1"
# ==========================================
# ADMIN
# ==========================================

def _environment_int(*names, default=0):
    for name in names:
        value = os.getenv(name)
        if value:
            try:
                return int(value.strip())
            except (TypeError, ValueError):
                continue
    return int(default)


CHANDELIER_ATR_PERIOD = max(2, _environment_int("CHANDELIER_ATR_PERIOD", default=7))
CHANDELIER_ATR_MULTIPLIER = max(0.1, _environment_float("CHANDELIER_ATR_MULTIPLIER", default=2.0))
CHANDELIER_CONFIRMATION_SECONDS = max(45, _environment_int("CHANDELIER_CONFIRMATION_SECONDS", default=60))
ENTRY_CONFIRMATION_CANDLES = max(1, _environment_int("ENTRY_CONFIRMATION_CANDLES", default=1))
ENTRY_BREAK_BUFFER_ATR = max(0.0, _environment_float("ENTRY_BREAK_BUFFER_ATR", default=0.05))
SIGNAL_MAX_AGE_CANDLES = max(1, _environment_int("SIGNAL_MAX_AGE_CANDLES", default=2))


# ADMIN_ID is intentionally sourced at runtime; CHAT_ID remains a backward-compatible
# fallback for deployments where the administrator is also the notification recipient.
ADMIN_ID = _environment_int("ADMIN_ID", "CHAT_ID")
ADMIN_IDS = tuple(
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().lstrip("-").isdigit()
)

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").strip().lower() not in {"0", "false", "no"}
LIVE_ORDER_PLACEMENT_ENABLED = False
ENABLE_LIVE_ORDER_PLACEMENT = False
SIGNALS_ONLY = True
PAPER_MONITORING = True
ENABLE_NIGHT_REPORT_REFRESH = False
RECOVERY_ALERT_AFTER_SECONDS = 600
ENABLE_RECOVERY_ALERTS = os.getenv("ENABLE_RECOVERY_ALERTS", "false").strip().lower() in {"1", "true", "yes"}
ENABLE_DHAN = os.getenv("ENABLE_DHAN", "true").strip().lower() not in {"0", "false", "no"}

LATE_EVENING_TIME = os.getenv("LATE_EVENING_TIME", "19:30")
LATE_NIGHT_TIME = os.getenv("LATE_NIGHT_TIME", "23:00")
PROVIDER_HEALTH_INTERVAL = max(60, _environment_int("PROVIDER_HEALTH_INTERVAL", default=300))
MAX_SIGNAL_DATA_DELAY_SECONDS = max(30, _environment_int("MAX_SIGNAL_DATA_DELAY_SECONDS", default=180))
PROVIDER_PRIORITY = tuple(
    item.strip().lower()
    for item in os.getenv("PROVIDER_PRIORITY", "angelone_primary,tradingview_alert_bridge").split(",")
    if item.strip()
)
# Production runtime allows exactly two providers: Angel One primary and the
# authenticated TradingView bridge as the only emergency backup.
PRODUCTION_PROVIDERS = ("angelone_primary", "tradingview_alert_bridge")
NO_FRESH_DATA_DECISION = "NO FRESH DATA / NO SIGNAL"
ANGEL_INSTRUMENT_REFRESH_HOUR = max(0, min(23, _environment_int("ANGEL_INSTRUMENT_REFRESH_HOUR", default=8)))
