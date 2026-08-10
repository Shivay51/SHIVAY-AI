"""NSE F&O plus MCX Gold/Silver Telegram inline symbol search."""
from __future__ import annotations

import hashlib

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.ext import ContextTypes

from ai_watchlist import get_ai_watchlist

MAX_RESULTS = 20
COMMODITY_SYMBOLS = ("MCX GOLD", "MCX SILVER")


def _symbols() -> list[str]:
    values = {str(value).strip().upper() for value in get_ai_watchlist() if str(value).strip()}
    values.update(COMMODITY_SYMBOLS)
    return sorted(symbol for symbol in values if "MCX" not in symbol or symbol in COMMODITY_SYMBOLS)


def search_symbols(query: str, limit: int = MAX_RESULTS) -> list[str]:
    needle = " ".join(str(query or "").upper().split())
    values = _symbols()
    if not needle:
        return values[:limit]
    starts = [symbol for symbol in values if symbol.startswith(needle)]
    contains = [symbol for symbol in values if needle in symbol and symbol not in starts]
    return (starts + contains)[:max(1, min(limit, MAX_RESULTS))]


async def answer_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if query is None:
        return
    results = []
    for symbol in search_symbols(query.query):
        category = "MCX commodity" if symbol in COMMODITY_SYMBOLS else "NSE F&O script"
        results.append(InlineQueryResultArticle(
            id=hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:32],
            title=symbol.replace(" FUT", ""),
            description=category,
            input_message_content=InputTextMessageContent(
                f"🔎 SHIVAY AI SEARCH\nScript: {symbol}\nUse /scan for current confirmed opportunities."
            ),
        ))
    await query.answer(results, cache_time=30, is_personal=True)
