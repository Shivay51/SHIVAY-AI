# ==========================================
# SHIVAY AI PRO v3
# AI Scanner
# ==========================================

from ai_watchlist import get_ai_watchlist
from sector_strength import (
    get_sector,
    sector_priority,
)

from data import get_market_data
from engine import run_engine
from strategy import analyze_trade
from tradeplan import create_trade_plan

from config import MAX_TRADES


def scan_market():

    results = []

    scanned = set()

    watchlist = get_ai_watchlist()

    print(f"\n🔍 AI Scanner Started")
    print(f"📊 Total Stocks : {len(watchlist)}")

    for symbol in watchlist:

        try:

            if symbol in scanned:
                continue

            market = get_market_data(symbol)

            if market is None:
                continue

            score_data = run_engine(market)

            if score_data is None:
                continue

            trade = analyze_trade(
                symbol,
                score_data,
            )

            if trade["decision"] not in (
                "🔥 STRONG BUY",
                "✅ BUY",
                "🔥 STRONG SELL",
                "🔻 SELL",
            ):
                continue

            plan = create_trade_plan(
                market["price"],
                score_data["atr"],
                trade["decision"],
            )

            results.append({

                "symbol": symbol,

                "sector": get_sector(symbol),

                "sector_priority": sector_priority(symbol),

                "price": round(market["price"], 2),

                "entry": plan["entry"],

                "sl": plan["sl"],

                "target1": plan["target1"],

                "target2": plan["target2"],

                "target3": plan["target3"],

                "score": score_data["score"],

                "regime": score_data["regime"],

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

            })

            scanned.add(symbol)

        except Exception as e:

            print(f"❌ {symbol} : {e}")

    results.sort(

        key=lambda x: (

            x["score"],

            x["sector_priority"],

            x["market_strength"],

            float(
                str(x["confidence"]).replace("%", "")
            ),

        ),

        reverse=True,

    )

    print(f"✅ Signals Found : {len(results)}")

    return results[:MAX_TRADES]