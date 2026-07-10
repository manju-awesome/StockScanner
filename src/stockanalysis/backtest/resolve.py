"""
resolve.py
==========
Resolves a long swing signal (entry / stop / target) against daily bars.

This is the single implementation of the outcome rules, used by BOTH the
live signal tracker (signal_tracker.update_outcomes) and the backtest engine
(backtest.engine) — if the two resolved trades differently, the backtest
would stop predicting live results.

The two callers differ only in configuration:

  live tracker : require_trigger=False (fill assumed at `entry` on signal
                 day — the scanner acted intraday), tie="target" (legacy
                 behavior preserved)
  backtest     : require_trigger=True (signal is computed at day D's close,
                 so a fill needs a later bar to actually touch the entry
                 level), tie="stop" (intrabar order is unknowable on daily
                 bars — count the ambiguous ones against the strategy)

Outcomes: open · no_trigger · target_hit · stop_hit · expired_no_move
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def resolve_long(prices: pd.DataFrame, entry: float, stop: float, target: float,
                 signal_date: date, asof_date: date,
                 expiry_days: int = 20, tie: str = "target",
                 require_trigger: bool = False,
                 trigger_window_bars: int = 5) -> dict:
    """
    Walk daily bars from signal_date forward and decide how the trade ended.

    prices       : daily OHLC (yfinance layout); may include bars before
                   signal_date — they are ignored
    signal_date  : day the signal was generated (metrics as of this close)
    asof_date    : "today" for the live tracker, end of data for a backtest;
                   decides whether an unresolved signal is expired or open
    tie          : outcome when one bar touches both stop and target —
                   "target" (optimistic, legacy live behavior) or "stop"
    require_trigger      : entry is a buy-stop that must be touched by a bar
                           AFTER signal_date; a gap open above entry fills at
                           the open price
    trigger_window_bars  : bars allowed for the trigger to fire, else
                           no_trigger

    Returns dict(outcome, outcome_date, outcome_price, fill_date, fill_price,
    realized_r_multiple). Price/date fields are None where not applicable.
    """
    if tie not in ("target", "stop"):
        raise ValueError(f"tie must be 'target' or 'stop', got {tie!r}")

    bar_dates = (prices.index.tz_convert(None).date
                 if prices.index.tz is not None else prices.index.date)
    expiry_end = signal_date + timedelta(days=expiry_days)
    window_complete = (asof_date - signal_date).days >= expiry_days

    in_trade   = not require_trigger
    fill       = entry if in_trade else None
    fill_date  = signal_date if in_trade else None
    bars_seen  = 0          # bars scanned for a trigger
    last_close = None
    last_date  = None

    def done(outcome, price, when):
        risk = (fill - stop) if fill is not None else None
        r = (round((price - fill) / risk, 2)
             if (price is not None and risk) else None)
        return {
            "outcome": outcome,
            "outcome_date": when,
            "outcome_price": round(float(price), 2) if price is not None else None,
            "fill_date": fill_date,
            "fill_price": round(float(fill), 2) if fill is not None else None,
            "realized_r_multiple": r,
        }

    for d, (_, bar) in zip(bar_dates, prices.iterrows()):
        if d < signal_date or d > expiry_end:
            continue
        last_close, last_date = bar["Close"], d

        if not in_trade:
            if d == signal_date:        # signal computed at this close
                continue
            if bars_seen >= trigger_window_bars:
                break
            bars_seen += 1
            if bar["High"] >= entry:
                in_trade  = True
                fill      = max(entry, float(bar["Open"]))   # gap-open → open
                fill_date = d
                hit_stop = bar["Low"] <= stop
                hit_targ = bar["High"] >= target and target > fill
                if hit_stop and hit_targ:
                    return done("stop_hit", stop, d) if tie == "stop" \
                        else done("target_hit", target, d)
                if hit_stop:
                    return done("stop_hit", stop, d)
                if hit_targ:
                    return done("target_hit", target, d)
            continue

        hit_stop = bar["Low"] <= stop
        hit_targ = bar["High"] >= target
        if hit_stop and hit_targ:
            return done("stop_hit", stop, d) if tie == "stop" \
                else done("target_hit", target, d)
        if hit_stop:
            return done("stop_hit", stop, d)
        if hit_targ:
            return done("target_hit", target, d)

    # Loop ended without stop/target
    if not in_trade:
        if bars_seen >= trigger_window_bars or window_complete:
            return done("no_trigger", None, last_date)
        return done("open", None, None)
    if window_complete and last_close is not None:
        return done("expired_no_move", last_close, last_date)
    return done("open", None, None)
