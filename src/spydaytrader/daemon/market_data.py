"""
market_data.py
===============
The daemon's only data source — yfinance, no broker session required. Bars
are used for signal computation, VIX for the signal record's context field.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

ET_TZ = "America/New_York"


def fetch_spy_bars(interval: str = "5m", period: str = "5d") -> pd.DataFrame:
    """Regular-hours SPY OHLCV bars, tz-normalized to America/New_York so
    signal_engine's ORB session-window logic lines up with wall-clock ET."""
    df = yf.download("SPY", interval=interval, period=period, auto_adjust=True, progress=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(ET_TZ)
    else:
        df.index = df.index.tz_convert(ET_TZ)
    return df


def fetch_vix() -> float | None:
    """Last VIX close. Never raises — returns None on any failure so a data
    hiccup doesn't take down the signal loop."""
    try:
        hist = yf.Ticker("^VIX").history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None
