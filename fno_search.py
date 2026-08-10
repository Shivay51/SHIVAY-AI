"""NSE F&O-only Telegram inline symbol search."""
from __future__ import annotations

import hashlib
from typing import Any

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.ext import ContextTypes

from ai_watchlist import get_ai_watchlist

MAX_RESULTS = 20


def _symbols() -> list[str]:
    values = {str(value).strip().upper() for value in get_ai_watchlist() if str(value).strip()}
    return sorted(symbol for symbol in values if "MCX" not in symbol and "GOLD" not in symbol and "SILVER" not in symbol)


def search_fno(query: str, limit: int = MAX_RESULTS) -> list[str]:
    needle = " ".join(str(query or "").upper().split())
    if not needle:
        return _symbols()[:limit]
    starts, contains = [], []
    for symbol in _symbols():
        (starts if symbol.startswith(needle) else contains if needle in symbol else []).append(symbol)
    return (starts + contains)[:max(1, min(limit, MAX_RESULTS))]


async def answer_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if query is None:
        return
    values = search_fno(query.query)
    results = [
        InlineQueryResultArticle(
            id=hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:32],
            title=symbol.replace(" FUT", ""),
            description="NSE F&O script",
            input_message_content=InputTextMessageContent(
                f"🔎 SHIVAY AI F&O SEARCH\nScript: {symbol}\nUse /scan for current confirmed opportunities."
            ),
        )
        for symbol in values
    ]
    await query.answer(results, cache_time=30, is_personal=True)
