from telegram.ext import Application, CommandHandler
from dotenv import load_dotenv
import os

from commands import (
    start,
    help_command,
    status,
    scan,
)

# Load Token
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Create Bot
app = Application.builder().token(TOKEN).build()

# Commands
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("status", status))
app.add_handler(CommandHandler("scan", scan))

print("🔱 SHIVAY AI Bot Started...")

app.run_polling()