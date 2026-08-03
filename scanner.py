# ==========================================
# SHIVAY AI PRO v2.3
# Professional Scanner
# ==========================================

from ai_watchlist import get_ai_watchlist

from data import get_market_data
from engine import run_engine
from strategy import analyze_trade
from tradeplan import create_trade_plan

from config import MAX_TRADES


def scan_market():

    results = []

    scanned = set()

    watchlist = get_ai_watchlist()

    print(f"📊 Scanning {len(watchlist)} Stocks")

    for symbol in watchlist:

        try:

            # ==========================================
            # Duplicate Check
            # ==========================================

            if symbol in scanned:
                continue

            # ==========================================
            # Market Data
            # ==========================================

            market = get_market_data(symbol)

            if market is None:
                continue

            # ==========================================
            # AI Engine
            # ==========================================

            score_data = run_engine(market)

            if score_data is None:
                continue

            score = score_data["score"]

            # ==========================================
            # Trade Decision
            # ==========================================

            trade = analyze_trade(symbol, score)

            if trade["decision"] not in (
                "🔥 STRONG BUY",
                "✅ BUY",
            ):
                continue

            # ==========================================
            # Trade Plan
            # ==========================================

            plan = create_trade_plan(

                market["price"],

                score_data["atr"],

                trade["decision"]

            )

            # ==========================================
            # Final Result
            # ==========================================

            results.append({

                "symbol": symbol,

                "price": round(market["price"], 2),

                "entry": plan["entry"],

                "sl": plan["sl"],

                "target1": plan["target1"],

                "target2": plan["target2"],

                "target3": plan["target3"],

                "score": score,

                "setup": score_data.get("setup"),

                "market": score_data.get("market"),

                "market_strength": score_data.get("market_strength"),

                "ema20": score_data["ema20"],

                "ema50": score_data["ema50"],

                "ema200": score_data["ema200"],

                "rsi": score_data["rsi"],

                "atr": score_data["atr"],

                "adx": score_data["adx"],

                "supertrend": score_data["supertrend"],

                "macd": score_data["macd"],

                "vwap": score_data["vwap"],

                "volume_spike": score_data["volume_spike"],

                "support": score_data["support"],

                "resistance": score_data["resistance"],

                "decision": trade["decision"],

                "risk": trade["risk"],

                "confidence": trade["confidence"],

                "rr_ratio": plan["rr_ratio"],

                "risk_percent": plan["risk_percent"],

                "reward_percent": plan["reward_percent"],

            })

            scanned.add(symbol)

        except Exception as e:

            print(f"❌ Scanner Error [{symbol}] : {e}")

    # ==========================================
    # Best Trades First
    # ==========================================

    results.sort(

        key=lambda x: (

            x["score"],

            x["market_strength"],

            x["confidence"]

        ),

        reverse=True

    )

    print(f"✅ High Probability Trades : {len(results)}")

    return results[:MAX_TRADES]