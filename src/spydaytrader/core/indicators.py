"""
indicators.py
==============
Pure-math building blocks for signal_engine.py. No network, no state, no I/O —
every function takes a pandas Series/DataFrame and returns one, so it can be
unit-tested against hand-computed values.

ATR here uses Wilder's smoothing (RMA), matching Pine's ta.atr() and the same
formula StockAnalysis_Version1/core/metrics.py uses for calculate_atr — copied
rather than imported since the two projects are independent git repos and
this is ~15 lines of math, not worth a cross-repo dependency.
"""

from __future__ import annotations

import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr_wilder(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's RMA-smoothed ATR. With period=1 this reduces to the true range
    itself (matches the Pine script's `ta.atr(1)` call for nLoss)."""
    tr = true_range(df)
    if period <= 1:
        return tr
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    if span <= 1:
        return series.copy()
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).mean()


def wma(series: pd.Series, length: int) -> pd.Series:
    weights = pd.Series(range(1, length + 1), dtype="float64")

    def _weighted(window: pd.Series) -> float:
        return (window * weights.values).sum() / weights.sum()

    return series.rolling(window=length, min_periods=length).apply(_weighted, raw=True)


def hma(series: pd.Series, length: int) -> pd.Series:
    """Standard Hull MA: WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    half_length = max(1, round(length / 2))
    sqrt_length = max(1, round(length ** 0.5))
    diff = 2 * wma(series, half_length) - wma(series, length)
    return wma(diff, sqrt_length)


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """True on the bar where `a` crosses above `b` (Pine's ta.crossover)."""
    return (a.shift(1) < b.shift(1)) & (a > b)


def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    """True on the bar where `a` crosses below `b` (Pine's ta.crossunder)."""
    return (a.shift(1) > b.shift(1)) & (a < b)
