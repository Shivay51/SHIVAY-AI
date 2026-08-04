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

        symbol = market.get("symbol", "UNKNOWN")

        # ==========================================
        # MARKET FILTER
        # ==========================================

        if not is_market_bullish():

            print(f"🚫 {symbol} -> Market Filter")

            return None

        market_direction = get_market_direction()
        market_strength = get_market_strength()

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

            print(
                f"🚫 {symbol} -> Breakout={breakout_ok} Pullback={pullback_ok}"
            )

            return None

        # ==========================================
        # SCORE
        # ==========================================

        score_data = calculate_score(market)

        if score_data is None:

            print(f"🚫 {symbol} -> Score Data Failed")

            return None

        score = score_data["score"]

        # ==========================================
        # DYNAMIC REQUIRED SCORE
        # ==========================================

        required_score = MIN_SCORE

        if market_direction == "🟡 SIDEWAYS":
            required_score += 5

        elif market_direction == "🔴 BEARISH":
            required_score += 10

        if market_strength < 75:
            required_score += 5

        if score < required_score:

            print(
                f"🚫 {symbol} -> Score={score} Need={required_score}"
            )

            return None

        # ==========================================
        # SETUP
        # ==========================================

        if breakout_ok and pullback_ok:

            setup = "🚀 Breakout + Pullback"

            confidence = "★★★★★"

        elif breakout_ok:

            setup = "🔥 Breakout"

            confidence = "★★★★☆"

        else:

            setup = "🔄 Pullback"

            confidence = "★★★★☆"

        # ==========================================
        # ENGINE INFO
        # ==========================================

        score_data["setup"] = setup

        score_data["market"] = market_direction

        score_data["market_strength"] = market_strength

        score_data["required_score"] = required_score

        score_data["engine_confidence"] = confidence

        score_data["engine"] = "SHIVAY AI PRO v2.5"

        print(
            f"✅ {symbol} -> PASS | Score={score} | {setup}"
        )

        return score_data

    except Exception as e:

        print(

            f"❌ Engine Error [{market.get('symbol','UNKNOWN')}] : {e}"

        )

        return None