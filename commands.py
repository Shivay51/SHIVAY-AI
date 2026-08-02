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
Scanner : Active ✅
AI Engine : Active ✅
        """
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    results = scan_market()

    if not results:
        await update.message.reply_text("❌ No Trade Found")
        return

    message = "🔱 SHIVAY AI - TOP TRADES\n\n"

    for item in results:

        message += (
            f"📈 {item['symbol']}\n"
            f"━━━━━━━━━━━━━━\n"
            f"💰 Price : ₹{item['price']}\n"
            f"🎯 Entry : ₹{item['entry']}\n"
            f"🛑 Stop Loss : ₹{item['sl']}\n"
            f"🥇 Target 1 : ₹{item['target1']}\n"
            f"🥈 Target 2 : ₹{item['target2']}\n"
            f"🥉 Target 3 : ₹{item['target3']}\n\n"
            f"📊 Score : {item['score']}/100\n"
            f"📈 Signal : {item['decision']}\n"
            f"📉 RSI : {item['rsi']}\n"
            f"📏 ATR : {round(item['atr'],2)}\n"
            f"⚠️ Risk : {item['risk']}\n"
            f"🎯 Confidence : {item['confidence']}\n"
            f"\n────────────────────\n\n"
        )

    await update.message.reply_text(message)