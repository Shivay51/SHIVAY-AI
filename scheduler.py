import asyncio
import os
from datetime import datetime

from dotenv import load_dotenv

from autoscan import auto_scan
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

            now = datetime.now()

            current = now.time()

            market_start = current.replace(
                hour=MARKET_START_HOUR,
                minute=MARKET_START_MINUTE,
                second=0,
                microsecond=0,
            )

            market_end = current.replace(
                hour=MARKET_END_HOUR,
                minute=MARKET_END_MINUTE,
                second=0,
                microsecond=0,
            )

            # ==========================================
            # MARKET CLOSED
            # ==========================================

            if current < market_start or current > market_end:

                print("⏸ Market Closed")

                await asyncio.sleep(60)

                continue

            # ==========================================
            # SCAN MARKET
            # ==========================================

            print("🔍 Scanning Market...")

            signals = auto_scan()

            if not signals:

                print("❌ No New Signal")

            else:

                print(f"✅ {len(signals)} Signal(s) Found")

                for trade in signals:

                    message = f"""
🔱 SHIVAY AI PRO

📈 {trade['symbol']}

🔥 Setup : {trade.get('setup', 'N/A')}
📊 Market : {trade.get('market', 'UNKNOWN')}

💰 Price : ₹{trade['price']}
🎯 Entry : ₹{trade['entry']}

🛑 Stop Loss : ₹{trade['sl']}

🥇 Target 1 : ₹{trade['target1']}
🥈 Target 2 : ₹{trade['target2']}
🥉 Target 3 : ₹{trade['target3']}

━━━━━━━━━━━━━━

📊 Score : {trade['score']}/100
📈 Signal : {trade['decision']}
📉 RSI : {trade['rsi']}
📏 ATR : {trade['atr']}
⚠️ Risk : {trade['risk']}
🎯 Confidence : {trade['confidence']}

🤖 SHIVAY AI
"""

                    await app.bot.send_message(
                        chat_id=int(CHAT_ID),
                        text=message,
                    )

                    print(f"📨 Alert Sent : {trade['symbol']}")

        except Exception as e:

            print(f"❌ Scheduler Error : {e}")

        await asyncio.sleep(SCAN_INTERVAL)