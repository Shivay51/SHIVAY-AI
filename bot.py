import asyncio
import os

from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CommandHandler,
)

from commands import (
    start,
    help_command,
    status,
    scan,
    id_command,
)

from scheduler import scheduler


# ==========================================
# LOAD ENVIRONMENT
# ==========================================

load_dotenv(".env", override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if BOT_TOKEN is None:
    raise ValueError("❌ BOT_TOKEN not found in .env")

print(f"✅ CHAT_ID : {CHAT_ID}")


# ==========================================
# CREATE BOT
# ==========================================

app = Application.builder().token(BOT_TOKEN).build()


# ==========================================
# COMMANDS
# ==========================================

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("status", status))
app.add_handler(CommandHandler("scan", scan))
app.add_handler(CommandHandler("id", id_command))


# ==========================================
# STARTUP
# ==========================================

async def on_startup(application):

    print("=" * 60)
    print("🔱 SHIVAY AI Started Successfully")
    print("🤖 Telegram Connected")
    print("📈 Scanner Ready")
    print("⚡ Auto Scanner Started")
    print("=" * 60)

    asyncio.create_task(
        scheduler(application)
    )


app.post_init = on_startup


# ==========================================
# RUN BOT
# ==========================================

print("🚀 Starting SHIVAY AI...\n")

app.run_polling(drop_pending_updates=True)