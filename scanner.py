from watchlist import WATCHLIST
from engine import run_engine
from strategy import analyze_trade
from tradeplan import create_trade_plan
from data import get_market_data
from config import MAX_TRADES, MIN_SCORE


def scan_market():

    results = []
    scanned = set()

    for stock in WATCHLIST:

        try:

            # ==========================
            # Duplicate Skip
            # ==========================

            if stock in scanned:
                continue

            # ==========================
            # Market Data
            # ==========================

            market = get_market_data(stock)

            if market is None:
                continue

            # Symbol Engine ને મોકલો
            market["symbol"] = stock

            # ==========================
            # AI Engine
            # ==========================

            score_data = run_engine(market)

            if score_data is None:
                continue

            score = score_data.get("score", 0)

            if score < MIN_SCORE:
                continue

            # ==========================
            # Trade Decision
            # ==========================

            trade = analyze_trade(stock, score)

            if trade["decision"] not in (
                "🔥 STRONG BUY",
                "✅ BUY",
            ):
                continue

            # ==========================
            # Trade Plan
            # ==========================

            plan = create_trade_plan(
                market["price"],
                score_data["atr"],
                trade["decision"],
            )

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

                "adx": score_data["adx"],

                "supertrend": score_data["supertrend"],

                "macd": score_data["macd"],

                "vwap": score_data["vwap"],

                "volume_spike": score_data["volume_spike"],

                "support": score_data["support"],

                "resistance": score_data["resistance"],

                "setup": score_data.get("setup", "N/A"),

                "market": score_data.get("market", "UNKNOWN"),

                "decision": trade["decision"],

                "risk": trade["risk"],

                "confidence": trade["confidence"],

            })

            scanned.add(stock)

        except Exception as e:

            print(f"❌ Scanner Error [{stock}] : {e}")

    # ==========================
    # Best Trades
    # ==========================

    results.sort(
        key=lambda x: (
            x["score"],
            float(str(x["confidence"]).replace("%", "")),
            x["adx"],
            x["rsi"],
        ),
        reverse=True,
    )

    return results[:MAX_TRADES]