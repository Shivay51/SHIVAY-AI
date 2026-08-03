import pandas as pd


# ==========================================
# RELATIVE STRENGTH
# SHIVAY AI PRO v2.4
# ==========================================

def relative_strength(stock_close, market_close):

    stock = pd.Series(stock_close).dropna()
    market = pd.Series(market_close).dropna()

    if len(stock) < 50 or len(market) < 50:
        return False

    # -------------------------
    # 20 Candle Return
    # -------------------------

    stock20 = (
        (stock.iloc[-1] - stock.iloc[-20])
        / stock.iloc[-20]
    ) * 100

    market20 = (
        (market.iloc[-1] - market.iloc[-20])
        / market.iloc[-20]
    ) * 100

    # -------------------------
    # 50 Candle Return
    # -------------------------

    stock50 = (
        (stock.iloc[-1] - stock.iloc[-50])
        / stock.iloc[-50]
    ) * 100

    market50 = (
        (market.iloc[-1] - market.iloc[-50])
        / market.iloc[-50]
    ) * 100

    score = 0

    # Strong in Short Term

    if stock20 > market20:
        score += 40

    if stock20 > (market20 + 1):
        score += 20

    # Strong in Medium Term

    if stock50 > market50:
        score += 20

    if stock50 > (market50 + 2):
        score += 20

    return score >= 60