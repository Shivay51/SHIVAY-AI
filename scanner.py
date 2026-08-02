from watchlist import WATCHLIST
from score import calculate_score
from strategy import analyze_trade
from tradeplan import create_trade_plan
from data import get_market_data


def scan_market():

    results = []

    for stock in WATCHLIST:

        market = get_market_data(stock)

        if market is None:
            continue

        score_data = calculate_score(market)

        score = score_data["score"]

        trade = analyze_trade(stock, score)

        plan = create_trade_plan(
            market["price"],
            score_data["atr"],
            trade["decision"]
        )

        if score < 60:
            continue

        results.append({

            "symbol": stock,

            "price": round(market["price"], 2),

            "entry": plan["entry"],

            "sl": plan["sl"],

            "target1": plan["target1"],

            "target2": plan["target2"],

            "target3": plan["target3"],

            "score": score,

            "ema20": score_data["ema20"],

            "ema50": score_data["ema50"],

            "ema200": score_data["ema200"],

            "rsi": score_data["rsi"],

            "atr": score_data["atr"],

            "decision": trade["decision"],

            "risk": trade["risk"],

            "confidence": trade["confidence"]

        })

    results = sorted(results, key=lambda x: x["score"], reverse=True)

    return results[:10]