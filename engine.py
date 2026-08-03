from config import MIN_SCORE
from score import calculate_score
from breakout import breakout_filter
from pullback import pullback_filter
from market_cache import (
    is_market_bullish,
    get_market_direction,
)


def run_engine(market):

    try:

        # ==========================
        # Market Trend
        # ==========================

        if not is_market_bullish():
            return None

        # ==========================
        # Breakout Check
        # ==========================

        breakout_ok = breakout_filter(
            market["high"],
            market["low"],
            market["close"],
            market["volume"],
        )

        # ==========================
        # Pullback Check
        # ==========================

        pullback_ok = pullback_filter(
            market["high"],
            market["low"],
            market["close"],
        )

        # કોઈ Setup નથી
        if not (breakout_ok or pullback_ok):
            return None

        # ==========================
        # AI Score
        # ==========================

        score_data = calculate_score(market)

        if score_data is None:
            return None

        score = score_data.get("score", 0)

        if score < MIN_SCORE:
            return None

        # ==========================
        # Setup Name
        # ==========================

        if breakout_ok and pullback_ok:
            setup = "🚀 Breakout + Pullback"

        elif breakout_ok:
            setup = "🔥 Breakout"

        else:
            setup = "🔄 Pullback"

        # ==========================
        # Extra Information
        # ==========================

        score_data["setup"] = setup
        score_data["market"] = get_market_direction()
        score_data["engine"] = "SHIVAY AI PRO v2"

        return score_data

    except Exception as e:

        symbol = market.get("symbol", "UNKNOWN")
        print(f"❌ Engine Error [{symbol}] : {e}")

        return None