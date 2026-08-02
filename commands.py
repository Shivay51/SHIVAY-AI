from telegram import Update
from telegram.ext import ContextTypes
from scanner import scan_market
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔱 Welcome to SHIVAY AI\n\nBot Status : ✅ ONLINE"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
📚 SHIVAY AI Commands

/start
/help
/status
/scan
        """
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
🟢 SHIVAY AI STATUS

Bot : Running ✅

Scanner : Under Development

AI Engine : Under Development
        """
    )

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = scan_market()

    message = "🔍 SHIVAY AI Scanner\n\n"

    for item in results:
        message += f"{item['symbol']}\n"
        message += f"Score: {item['score']}\n"
        message += f"Decision: {item['decision']}\n\n"

    await update.message.reply_text(message)