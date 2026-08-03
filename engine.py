from config import MIN_SCORE
from score import calculate_score
from breakout import breakout_filter
from pullback import pullback_filter

from market_cache import (
    is_market_bullish,
    get_market_direction,
    get_market_strength,
)


def run_engine(market):

    try:

        # ==========================================
        # MARKET FILTER
        # ==========================================

        if not is_market_bullish():
            return None

        market_strength = get_market_strength()
        market_direction = get_market_direction()

        # ==========================================
        # BREAKOUT
        # ==========================================

        breakout_ok = breakout_filter(
            market["high"],
            market["low"],
            market["close"],
            market["volume"],
        )

        # ==========================================
        # PULLBACK
        # ==========================================

        pullback_ok = pullback_filter(
            market["high"],
            market["low"],
            market["close"],
        )

        if not breakout_ok and not pullback_ok:
            return None

        # ==========================================
        # AI SCORE
        # ==========================================

        score_data = calculate_score(market)

        if score_data is None:
            return None

        score = score_data.get("score", 0)

        # ==========================================
        # MARKET BASED FILTER
        # ==========================================

        required_score = MIN_SCORE

        if market_strength < 75:
            required_score += 5

        if market_direction == "🟡 SIDEWAYS":
            required_score += 5

        if score < required_score:
            return None

        # ==========================================
        # SETUP
        # ==========================================

        if breakout_ok and pullback_ok:

            setup = "🚀 Breakout + Pullback"

        elif breakout_ok:

            setup = "🔥 Breakout"

        else:

            setup = "🔄 Pullback"

        # ==========================================
        # EXTRA DATA
        # ==========================================

        score_data["setup"] = setup
        score_data["market"] = market_direction
        score_data["market_strength"] = market_strength
        score_data["required_score"] = required_score
        score_data["engine"] = "SHIVAY AI PRO v2.3"

        return score_data

    except Exception as e:

        symbol = market.get("symbol", "UNKNOWN")

        print(f"❌ Engine Error [{symbol}] : {e}")

        return None