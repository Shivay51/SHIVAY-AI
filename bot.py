"""Telegram entry point and lifecycle integration for SHIVAY AI."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, InlineQueryHandler

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

import app_logging
import auto_recovery
import cleanup
import config
import startup as lifecycle
from fno_search import answer_inline_query

LOGGER = logging.getLogger("shivay.bot")
TOKEN_PATTERN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")
app: Application | None = None


def _load_token() -> str:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    token = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not configured")
    if not TOKEN_PATTERN.fullmatch(token):
        raise RuntimeError("BOT_TOKEN has an invalid format")
    return token


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    LOGGER.error("Telegram update failed safely: %s", type(error).__name__ if error else "UnknownError")
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text("The request could not be completed safely. Please try again shortly.")
        except Exception:
            LOGGER.warning("Could not deliver the generic command-error response")


async def on_startup(application: Application) -> None:
    try:
        state = await lifecycle.startup(application)
        await auto_recovery.start_auto_recovery(application)
        await cleanup.start_cleanup_scheduler()
        from tradingview_webhook import start_tradingview_webhook, startup_self_check
        await start_tradingview_webhook(application)
        check = await startup_self_check()
        application.bot_data["tradingview_webhook_health"] = check
        application.bot_data["shivay_enabled"] = True
        try:
            import admin
            await admin.notify_admins(application, "🔱 SHIVAY AI PRO\n✅ BOT ACTIVE\n🎯 MODE: SIGNALS ONLY")
        except Exception:
            LOGGER.warning("Startup admin notification was not delivered")
        LOGGER.info("SHIVAY AI startup completed: %s", state.get("state", "READY"))
    except Exception:
        LOGGER.exception("SHIVAY AI could not complete critical startup")
        await cleanup.stop_cleanup_scheduler()
        await auto_recovery.stop_auto_recovery(application)
        await lifecycle.shutdown(application)
        raise


async def on_shutdown(application: Application) -> None:
    from tradingview_webhook import stop_tradingview_webhook
    await stop_tradingview_webhook()
    await cleanup.stop_cleanup_scheduler()
    await auto_recovery.stop_auto_recovery(application)
    await lifecycle.shutdown(application)
    LOGGER.info("SHIVAY AI shutdown completed")


def build_application(token: str | None = None) -> Application:
    application = Application.builder().token(token or _load_token()).post_init(on_startup).post_shutdown(on_shutdown).build()
    application.add_handler(InlineQueryHandler(answer_inline_query))
    application.add_error_handler(error_handler)
    return application


def run_bot() -> None:
    global app
    if (bool(getattr(config, "LIVE_ORDER_PLACEMENT_ENABLED", False))
            or bool(getattr(config, "ENABLE_LIVE_ORDER_PLACEMENT", False))
            or not bool(getattr(config, "SIGNALS_ONLY", True))
            or not bool(getattr(config, "PAPER_MONITORING", True))):
        raise RuntimeError("Unsafe execution configuration rejected: SHIVAY AI must run signals-only")
    app_logging.setup_logging()
    app = build_application()
    try:
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    finally:
        app_logging.shutdown_logging()


def main() -> None:
    run_bot()


if __name__ == "__main__":
    main()
