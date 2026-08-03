import asyncio
import os
from datetime import datetime

from dotenv import load_dotenv

from autoscan import auto_scan
from trade_monitor import (
    add_trade,
    check_trades,
)

from config import (
    SCAN_INTERVAL,
    MARKET_START_HOUR,
    MARKET_START_MINUTE,
    MARKET_END_HOUR,
    MARKET_END_MINUTE,
)

# ==========================================
# LOAD ENVIRONMENT
# ==========================================

load_dotenv()

CHAT_ID = os.getenv("CHAT_ID")


# ==========================================
# SCHEDULER
# ==========================================

async def scheduler(app):

    print("✅ Auto Scanner Started")

    if not CHAT_ID:
        print("⚠️ CHAT_ID not found in .env")
        return

    while True:

        try:

            now = datetime.now().time()

            market_start = now.replace(
                hour=MARKET_START_HOUR,
                minute=MARKET_START_MINUTE,
                second=0,
                microsecond=0,
            )

            market_end = now.replace(
                hour=MARKET_END_HOUR,
                minute=MARKET_END_MINUTE,
                second=0,
                microsecond=0,
            )

            # ==========================================
            # MARKET CLOSED
            # ==========================================

            if now < market_start or now > market_end:

                print("⏸ Market Closed")
                await asyncio.sleep(60)
                continue

            # ==========================================
            # SCAN NEW SIGNALS
            # ==========================================

            print("🔍 Scanning Market...")

            signals = auto_scan()

            if signals:

                print(f"✅ {len(signals)} Signal(s) Found")

                for trade in signals:

                    add_trade(trade)

                    message = f"""
🔱 SHIVAY AI PRO

📈 {trade['symbol']}

🔥 Setup : {trade.get('setup', 'N/A')}
📊 Market : {trade.get('market', 'UNKNOWN')}

💰 Entry : ₹{trade['entry']}
🛑 Stop Loss : ₹{trade['sl']}

🥇 Target 1 : ₹{trade['target1']}
🥈 Target 2 : ₹{trade['target2']}
🥉 Target 3 : ₹{trade['target3']}

━━━━━━━━━━━━━━

📊 Score : {trade['score']}/100
📈 Signal : {trade['decision']}
📉 RSI : {trade['rsi']}
📏 ATR : {trade['atr']}
🎯 Confidence : {trade['confidence']}

🤖 SHIVAY AI PRO
"""

                    await app.bot.send_message(
                        chat_id=int(CHAT_ID),
                        text=message,
                    )

                    print(f"📨 Signal Sent : {trade['symbol']}")

            else:

                print("❌ No New Signal")

            # ==========================================
            # TRADE MONITOR
            # ==========================================

            alerts = check_trades()

            for alert in alerts:

                if alert["type"] == "TARGET1":

                    text = f"""
🎯 TARGET 1 HIT

📈 {alert['symbol']}

💰 Current Price : ₹{alert['price']}

🛡 Stop Loss moved to Break-even

🛑 New SL : ₹{alert['new_sl']}
"""

                elif alert["type"] == "TARGET2":

                    text = f"""
🥈 TARGET 2 HIT

📈 {alert['symbol']}

💰 Current Price : ₹{alert['price']}

📈 Trailing Stop Activated

🛑 New SL : ₹{alert['new_sl']}
"""

                elif alert["type"] == "TARGET3":

                    text = f"""
🏆 TARGET 3 HIT

📈 {alert['symbol']}

💰 Exit Price : ₹{alert['price']}

🎉 Trade Closed Successfully
"""

                else:

                    text = f"""
🛑 STOP LOSS HIT

📉 {alert['symbol']}

💰 Exit Price : ₹{alert['price']}

❌ Trade Closed
"""

                await app.bot.send_message(
                    chat_id=int(CHAT_ID),
                    text=text,
                )

                print(f"📢 {alert['type']} : {alert['symbol']}")

        except Exception as e:

            print(f"❌ Scheduler Error : {e}")

        await asyncio.sleep(SCAN_INTERVAL)