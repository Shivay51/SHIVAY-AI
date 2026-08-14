"""Backward-compatible RSI entry point.

The canonical Wilder RSI now lives in :mod:`indicators`, so the score engine,
the indicator snapshot, and every other caller share one identical
implementation (TradingView-standard Wilder smoothing) instead of the previous
simple rolling-mean RSI. This module preserves the historical
``calculate_rsi`` name, its neutral 50.0 fallback, and 2-decimal rounding as a
thin wrapper so existing imports keep working unchanged.
"""

from indicators import rsi as _wilder_rsi


def calculate_rsi(close, period=14):
    return round(_wilder_rsi(close, period, fallback=50.0), 2)
