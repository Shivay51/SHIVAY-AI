from telegram import Update
from telegram.ext import ContextTypes
from scanner import scan_market


# ==========================================
# START
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🔱 Welcome to SHIVAY AI\n\n"
        "✅ Bot Status : ONLINE\n"
        "📈 AI Scanner Ready"
    )


# ==========================================
# HELP
# ==========================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        """
📚 SHIVAY AI COMMANDS

/start
/help
/status
/scan
/id
"""
    )


# ==========================================
# STATUS
# ==========================================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.effective_chat.id

    await update.message.reply_text(
        f"""
🟢 SHIVAY AI STATUS

🤖 Bot : Running ✅
📈 Scanner : Active ✅
🧠 AI Engine : Active ✅
⚡ Auto Scan : Ready ✅

🆔 Chat ID
{chat_id}
"""
    )


# ==========================================
# CHAT ID
# ==========================================

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.effective_chat.id

    await update.message.reply_text(

        f"🆔 Your Chat ID\n\n{chat_id}"

    )


# ==========================================
# SCAN
# ==========================================

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        results = scan_market()

        if not results:

            await update.message.reply_text(
                "❌ No High Probability Trade Found."
            )
            return

        message = "🔱 SHIVAY AI - TOP TRADES\n\n"

        total = 0

        for item in results:

            if item["decision"] == "❌ AVOID":
                continue

            total += 1

            message += (
                f"📈 {item['symbol']}\n"
                "━━━━━━━━━━━━━━\n"
            )

            if item["decision"] in ["🔥 STRONG BUY", "✅ BUY"]:

                message += (
                    f"💰 Price : ₹{item['price']}\n"
                    f"🎯 Entry : ₹{item['entry']}\n"
                    f"🛑 Stop Loss : ₹{item['sl']}\n"
                    f"🥇 Target 1 : ₹{item['target1']}\n"
                    f"🥈 Target 2 : ₹{item['target2']}\n"
                    f"🥉 Target 3 : ₹{item['target3']}\n\n"
                )

            else:

                message += "👀 Watch Only\n\n"

            message += (
                f"📊 Score : {item['score']}/100\n"
                f"📈 Signal : {item['decision']}\n"
                f"📉 RSI : {item['rsi']}\n"
                f"📏 ATR : {round(item['atr'],2)}\n"
                f"⚠️ Risk : {item['risk']}\n"
                f"🎯 Confidence : {item['confidence']}\n"
                "\n────────────────────\n\n"
            )

        if total == 0:

            await update.message.reply_text(
                "❌ No High Probability Trade Found."
            )
            return

        await update.message.reply_text(message)

    except Exception as e:

        await update.message.reply_text(
            f"❌ Scanner Error\n\n{e}"
        )