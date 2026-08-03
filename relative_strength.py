import pandas as pd


def relative_strength(stock_close, market_close):

    stock = pd.Series(stock_close)
    market = pd.Series(market_close)

    # ઓછામાં ઓછા 20 Candles જોઈએ
    if len(stock) < 20 or len(market) < 20:
        return False

    # છેલ્લા 20 Candles નું Return
    stock_return = (
        (stock.iloc[-1] - stock.iloc[-20])
        / stock.iloc[-20]
    ) * 100

    market_return = (
        (market.iloc[-1] - market.iloc[-20])
        / market.iloc[-20]
    ) * 100

    # Market કરતાં ઓછામાં ઓછું 0.50% Strong હોવું જોઈએ
    if stock_return > (market_return + 0.50):
        return True

    return False