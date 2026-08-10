"""Private-chat plain-text symbol search for SHIVAY AI."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from fno_search import search_symbols

MAX_MATCHES = 5


async def direct_symbol_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to a plain symbol query in a private SHIVAY AI chat only."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or chat.type != ChatType.PRIVATE:
        return
    query = (message.text or "").strip()
    if not query or query.startswith("/") or len(query) > 40:
        return
    matches = search_symbols(query, limit=MAX_MATCHES)
    if not matches:
        await message.reply_text(
            "No NSE F&O / MCX Gold-Silver match found. Try: reliance, gold, or silver."
        )
        return
    exact = next((symbol for symbol in matches if symbol == query.upper()), None)
    if exact:
        await message.reply_text(
            f"🔎 SHIVAY AI SEARCH\nScript: {exact}\nUse /scan for current confirmed opportunities."
        )
        return
    await message.reply_text(
        "🔎 Matching scripts\n\n" + "\n".join(f"• {symbol}" for symbol in matches)
        + "\n\nSend the exact script name to select it, or use /scan for current confirmed opportunities."
    )
