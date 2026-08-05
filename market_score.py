"""Compatibility facade for the internal 0-100 Market Brain score."""
from market_brain import get_market_analysis, get_market_score


def calculate_market_score() -> int:
    return get_market_score()


def get_market_label() -> str:
    return str(get_market_analysis().get("market_regime", "SIDEWAYS"))


def get_market_score_details() -> dict:
    value = get_market_analysis()
    return {"market_score": int(value.get("market_score", 50)), "market_label": str(value.get("market_regime", "SIDEWAYS")), "tradeable": bool(value.get("tradeable"))}
