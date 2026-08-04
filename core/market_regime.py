# ==========================================
# SHIVAY AI PRO v3
# Market Regime Engine
# ==========================================

from market_cache import (
    get_market_direction,
    get_market_strength,
)


def get_market_regime():

    direction = get_market_direction()
    strength = get_market_strength()

    if direction == "🟢 BULLISH":

        if strength >= 90:
            return "🟢 STRONG BULL"

        return "🟢 BULL"

    elif direction == "🔴 BEARISH":

        if strength <= 30:
            return "🔴 STRONG BEAR"

        return "🔴 BEAR"

    return "🟡 SIDEWAYS"