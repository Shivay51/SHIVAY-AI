from watchlist import WATCHLIST
from score import calculate_score

def scan_market():
    results = []

    for stock in WATCHLIST:

        score = calculate_score(stock)

        if score >= 90:
            decision = "🔥 STRONG BUY"

        elif score >= 80:
            decision = "✅ BUY"

        elif score >= 60:
            decision = "👀 WATCH"

        else:
            decision = "❌ IGNORE"

        if score < 60:
            continue

        results.append({
            "symbol": stock,
            "score": score,
            "decision": decision
        })

    return results