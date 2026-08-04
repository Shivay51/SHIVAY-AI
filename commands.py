from telegram import Update
from telegram.ext import ContextTypes

from scanner import scan_market

from user_manager import (
    add_user,
    remove_user,
    get_all_users,
)

from security import is_admin


# ==========================================
# START
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(

        "🔱 SHIVAY AI PRO v3\n\n"

        "✅ Bot : ONLINE\n"

        "📈 Scanner : READY\n"

        "🤖 BUY / SELL Engine : ACTIVE\n"

        "⚡ Auto Scan : ENABLED"

    )


# ==========================================
# HELP
# ==========================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(

        "/start\n"

        "/status\n"

        "/scan\n"

        "/id\n"

        "/adduser\n"

        "/removeuser\n"

        "/listusers\n"

        "/help"

    )


# ==========================================
# STATUS
# ==========================================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(

        "🟢 SHIVAY AI PRO v3\n\n"

        "🤖 Status : ONLINE\n"

        "📈 Scanner : ACTIVE\n"

        "🧠 AI Engine : ACTIVE\n"

        "📡 Telegram : CONNECTED"

    )


# ==========================================
# CHAT ID
# ==========================================

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(

        f"🆔 Chat ID\n\n{update.effective_chat.id}"

    )


# ==========================================
# SCAN
# ==========================================

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update):

        await update.message.reply_text("⛔ Access Denied")

        return

    try:

        await update.message.reply_text(
            "🔍 Scanning Market..."
        )

        trades = scan_market()

        if not trades:

            await update.message.reply_text(
                "❌ No High Probability Trade Found."
            )

            return

        for trade in trades:

            msg = (

                f"🔱 SHIVAY AI PRO\n\n"

                f"📈 {trade['symbol']}\n\n"

                f"📊 Regime : {trade['regime']}\n"

                f"🔥 Signal : {trade['decision']}\n"

                f"📊 Score : {trade['score']}/100\n\n"

                f"💰 Entry : ₹{trade['entry']}\n"

                f"🛑 Stop Loss : ₹{trade['sl']}\n"

                f"🎯 Target 1 : ₹{trade['target1']}\n"

                f"🎯 Target 2 : ₹{trade['target2']}\n"

                f"🎯 Target 3 : ₹{trade['target3']}\n\n"

                f"📉 RSI : {trade['rsi']}\n"

                f"📏 ATR : {trade['atr']}\n"

                f"📈 ADX : {trade['adx']}\n"

                f"⚠️ Risk : {trade['risk']}\n"

                f"🎯 Confidence : {trade['confidence']}"

            )

            await update.message.reply_text(msg)

    except Exception as e:

        await update.message.reply_text(

            f"❌ Scanner Error\n\n{e}"

        )
# ==========================================
# ADD USER
# ==========================================

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Access Denied"
        )

        return

    try:

        if len(context.args) != 3:

            await update.message.reply_text(
                "Usage:\n/adduser CHAT_ID NAME PLAN"
            )

            return

        user_id = int(context.args[0])
        name = context.args[1]
        plan = context.args[2].upper()

        if plan not in ["VIP", "PREMIUM", "BASIC"]:

            await update.message.reply_text(
                "Plan must be VIP / PREMIUM / BASIC"
            )

            return

        if add_user(user_id, name, plan):

            await update.message.reply_text(
                f"""✅ USER ADDED

👤 Name : {name}
🆔 ID : {user_id}
⭐ Plan : {plan}
🟢 Status : Active
"""
            )

        else:

            await update.message.reply_text(
                "⚠️ User Already Exists"
            )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error\n\n{e}"
        )


# ==========================================
# REMOVE USER
# ==========================================

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Access Denied"
        )

        return

    try:

        if len(context.args) != 1:

            await update.message.reply_text(
                "Usage:\n/removeuser CHAT_ID"
            )

            return

        user_id = int(context.args[0])

        remove_user(user_id)

        await update.message.reply_text(
            f"✅ User Removed\n\n🆔 {user_id}"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error\n\n{e}"
        )


# ==========================================
# LIST USERS
# ==========================================

async def listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Access Denied"
        )

        return

    users = get_all_users()

    if not users:

        await update.message.reply_text(
            "❌ No Users Found"
        )

        return

    msg = "👥 SHIVAY AI USERS\n\n"

    for i, user in enumerate(users, start=1):

        status = "🟢 Active" if user["active"] else "🔴 Disabled"

        msg += (
            f"{i}. {user['name']}\n"
            f"🆔 {user['id']}\n"
            f"⭐ {user['plan']}\n"
            f"{status}\n\n"
        )

    await update.message.reply_text(msg)