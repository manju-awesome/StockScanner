"""
signal_engine.py
=================
Python translation of the SVMKR_UT_HMA_ORB Pine Script (v6). Three active
components carried over (the LRS block at the bottom of the script is
commented out there and is skipped entirely here):

1. UT Bot — the only literal `alertcondition` in the script, and therefore
   the only actionable entry trigger. An ATR(1)-based trailing stop that
   ratchets toward price; `buy`/`sell` fire on the bar where price crosses
   the stop.
2. HMA(31) — trend-color filter (rising/falling), not an independent alert.
3. 200 SMA — price vs SMA(200) trend-bias filter, also visual-only in the
   script.
4. ORB — opening-range high/low. The script's default session (10:10-10:15
   ET) is replaced here with the standard market open (09:30, 5 minutes) per
   an explicit product decision. The script only plots these levels; this
   module adds a derived breakout flag since that's the natural use of an
   ORB level, but treats it as context/confluence, not a second trigger.

Pure function of a DataFrame in, DataFrame out — no I/O, no broker/network
calls, so it can be tested with hand-built OHLC series.
"""

from __future__ import annotations

from datetime import datetime, time

import numpy as np
import pandas as pd

from spydaytrader.core import indicators


def _ut_trailing_stop(df: pd.DataFrame, key_value: float, atr_period: int) -> pd.Series:
    close = df["Close"]
    n_loss = key_value * indicators.atr_wilder(df, atr_period)

    stop = pd.Series(index=df.index, dtype="float64")
    prev_stop = 0.0
    prev_src = float("nan")
    for i in range(len(df)):
        src = close.iloc[i]
        loss = n_loss.iloc[i]
        if pd.isna(loss) or pd.isna(src):
            stop.iloc[i] = float("nan")
            continue
        if src > prev_stop and prev_src > prev_stop:
            new_stop = max(prev_stop, src - loss)
        elif src < prev_stop and prev_src < prev_stop:
            new_stop = min(prev_stop, src + loss)
        elif src > prev_stop:
            new_stop = src - loss
        else:
            new_stop = src + loss
        stop.iloc[i] = new_stop
        prev_stop = new_stop
        prev_src = src
    return stop


def _trend_from_slope(series: pd.Series) -> pd.Series:
    rising = series > series.shift(1)
    falling = series < series.shift(1)
    return np.select([rising, falling], ["bullish", "bearish"], default="flat")


def _orb_levels(df: pd.DataFrame, session_start: str, minutes: int) -> tuple[pd.Series, pd.Series]:
    """Per-day frozen opening-range high/low. `session_start` is "HH:MM" ET;
    the window is [session_start, session_start + minutes). Assumes df.index
    is tz-aware in America/New_York (yfinance's native intraday tz for US
    tickers) — bars outside that are treated as UTC-naive local time."""
    start_h, start_m = (int(x) for x in session_start.split(":"))
    start_t = time(start_h, start_m)
    start_minutes = start_h * 60 + start_m
    end_minutes = start_minutes + minutes

    orb_high = pd.Series(index=df.index, dtype="float64")
    orb_low = pd.Series(index=df.index, dtype="float64")

    for _, day_idx in df.groupby(df.index.date).groups.items():
        day_rows = df.loc[day_idx]
        bar_minutes = day_rows.index.hour * 60 + day_rows.index.minute
        in_session = (bar_minutes >= start_minutes) & (bar_minutes < end_minutes)
        after_session = bar_minutes >= end_minutes

        if in_session.any():
            session_high = day_rows.loc[in_session, "High"].max()
            session_low = day_rows.loc[in_session, "Low"].min()
        else:
            session_high = float("nan")
            session_low = float("nan")

        orb_high.loc[day_idx[in_session | after_session]] = session_high
        orb_low.loc[day_idx[in_session | after_session]] = session_low

    return orb_high, orb_low


def compute_signal_frame(
    df: pd.DataFrame,
    *,
    key_value: float = 2,
    atr_period: int = 1,
    hma_period: int = 31,
    ma200_period: int = 200,
    orb_session_start: str = "09:30",
    orb_minutes: int = 15,
) -> pd.DataFrame:
    """Annotates `df` (columns: Open/High/Low/Close/Volume, tz-aware
    DatetimeIndex) with every signal-engine column. Returns a new frame;
    `df` is not modified."""
    out = df.copy()
    close = out["Close"]

    stop = _ut_trailing_stop(out, key_value, atr_period)
    out["ut_stop"] = stop
    out["buy"] = (close > stop) & indicators.crossover(close, stop)
    out["sell"] = (close < stop) & indicators.crossunder(close, stop)

    out["hma"] = indicators.hma(close, hma_period)
    out["hma_trend"] = _trend_from_slope(out["hma"])

    out["ma200"] = indicators.sma(close, ma200_period)
    out["ma200_trend"] = np.select(
        [close > out["ma200"], close < out["ma200"]], ["bullish", "bearish"], default="flat"
    )

    orb_high, orb_low = _orb_levels(out, orb_session_start, orb_minutes)
    out["orb_high"] = orb_high
    out["orb_low"] = orb_low

    # Built explicitly as an object Series rather than via np.select(...,
    # default=None): numpy coerces that None to nan under pandas 3.x while
    # pandas 2.x kept it as None, so `is None` checks downstream silently
    # changed meaning between versions. This yields None on both.
    breakout = pd.Series([None] * len(out), index=out.index, dtype=object)
    breakout[close > orb_high] = "long"
    breakout[close < orb_low] = "short"
    out["orb_breakout"] = breakout

    return out


def latest_signal(frame: pd.DataFrame, *, ticker: str = "SPY", vix: float | None = None) -> dict | None:
    """Returns the composite signal record if the UT Bot fired on the most
    recent (last) bar of `frame`, else None. All values cast to native
    Python types so the result is JSON-safe."""
    if frame.empty:
        return None
    last = frame.iloc[-1]

    side = None
    if bool(last["buy"]):
        side = "long"
    elif bool(last["sell"]):
        side = "short"
    if side is None:
        return None

    orb_high = last["orb_high"]
    orb_low = last["orb_low"]
    orb_breakout = last["orb_breakout"]
    orb_aligned = orb_breakout == side

    ts = frame.index[-1]
    signal_time = ts.isoformat() if isinstance(ts, (pd.Timestamp, datetime)) else str(ts)

    return {
        "ticker": ticker,
        "side": side,
        "signal_time": signal_time,
        "spy_price": float(last["Close"]),
        "hma_trend": str(last["hma_trend"]),
        "ma200_trend": str(last["ma200_trend"]),
        "orb_high": float(orb_high) if pd.notna(orb_high) else None,
        "orb_low": float(orb_low) if pd.notna(orb_low) else None,
        "orb_breakout_aligned": orb_aligned,
        "vix": float(vix) if vix is not None else None,
    }
