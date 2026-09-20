"""
volume.py — §8 volume confirmation, 0-100
=========================================
§3 asks whether there is volume. This asks whether the volume is in the
right places — which is a different and more useful question, because the
same day's total can describe a healthy breakout or a distribution.

The pattern §8 names as preferred:

    CONSOLIDATION → VOLUME CONTRACTION → BREAKOUT → VOLUME EXPANSION

and the one it says to penalise:

    BREAKOUT → LOW VOLUME → IMMEDIATE FAILURE

Both are sequences, so both are detected as sequences: the bars are split
at the session's highest-volume expansion bar, and the run-up before it is
compared against the surge at it and the follow-through after it. A
scanner that only looked at aggregate volume could not tell these apart at
all — they frequently have identical daily totals.

Buying versus selling volume
-----------------------------
Volume on up-bars against volume on down-bars, which is the closest honest
proxy available from OHLCV. It is not the bid/ask-classified order flow a
professional desk would use — there is no tick data here — and it is
described in the output as up-bar/down-bar volume rather than as "buying
pressure", because calling it the latter would claim more than the data
supports.
"""

from __future__ import annotations

import pandas as pd

from stockanalysis.core.daytrade._common import blend, f, scale

# Multiples of the session's median bar volume.
EXPANSION_MULT = 2.0      # what counts as a genuine volume surge
CONTRACTION_MULT = 0.8    # what counts as a dried-up base


def _split_at_surge(bars: pd.DataFrame):
    """Locate the session's defining volume surge and split around it.

    The surge bar is the highest-volume bar that is also an expansion over
    the running median. Everything before it is the base, everything after
    is the follow-through.
    """
    if len(bars) < 12:
        return None
    vol = bars["Volume"].fillna(0.0)
    median = float(vol.median() or 0)
    if median <= 0:
        return None
    # Exclude the first three bars: the open is always the highest-volume
    # print of the day and would win this every time, telling us nothing.
    candidates = vol.iloc[3:]
    if candidates.empty or float(candidates.max()) < median * EXPANSION_MULT:
        return None
    pos = int(vol.index.get_loc(candidates.idxmax()))
    if pos < 4 or pos > len(bars) - 3:
        return None
    return pos, median


def compute(sess: dict, rvol_accel: float | None = None) -> dict:
    """§8 volume confirmation, 0-100."""
    bars = sess.get("bars")
    if bars is None or len(bars) < 12:
        return {"score": None, "coverage": 0.0, "components": [],
                "missing": ["intraday bars"], "detail": "too few bars",
                "sequence": None, "warnings": []}

    vol = bars["Volume"].fillna(0.0)
    close, open_ = bars["Close"], bars["Open"]
    median = float(vol.median() or 0)

    up_vol = float(vol[close > open_].sum())
    down_vol = float(vol[close < open_].sum())
    total_dir = up_vol + down_vol
    up_share = (up_vol / total_dir * 100.0) if total_dir > 0 else None

    sequence, warnings = None, []
    base_score = follow_score = None

    split = _split_at_surge(bars)
    if split:
        pos, med = split
        base = vol.iloc[max(0, pos - 8):pos]
        surge = float(vol.iloc[pos])
        after = vol.iloc[pos + 1:pos + 6]
        base_mean = float(base.mean() or 0)
        after_mean = float(after.mean() or 0)

        contracted = base_mean > 0 and base_mean < med * CONTRACTION_MULT
        expanded = surge >= med * EXPANSION_MULT
        held = after_mean > 0 and after_mean >= base_mean

        # Did price hold the surge? A breakout whose volume expanded and
        # whose price then gave the whole bar back is the §8 failure case.
        surge_close = f(close.iloc[pos])
        latest = f(close.iloc[-1])
        price_held = (surge_close is not None and latest is not None
                      and latest >= surge_close * 0.995)

        base_score = (100.0 if contracted and expanded
                      else 65.0 if expanded else 30.0)
        if expanded and held and price_held:
            follow_score, sequence = 100.0, "contraction → expansion → follow-through"
        elif expanded and price_held:
            follow_score, sequence = 70.0, "expansion held, follow-through light"
        elif expanded and not price_held:
            follow_score, sequence = 10.0, "expansion FAILED — price gave the surge back"
            warnings.append("breakout volume expanded and price failed to hold it (§8)")
        else:
            follow_score, sequence = 40.0, "no clear expansion"
    else:
        sequence = "no identifiable volume surge this session"

    parts = [
        ("Surge structure", 35, base_score,
         sequence if base_score is not None else "not detected"),
        ("Follow-through", 35, follow_score,
         sequence if follow_score is not None else "not detected"),
        ("Up-bar share", 20,
         scale(up_share, 35.0, 70.0) if up_share is not None else None,
         f"{up_share:.0f}% of directional volume on up-bars"
         if up_share is not None else "unavailable"),
        ("RVOL acceleration", 10,
         scale(rvol_accel, 0.6, 3.0) if rvol_accel is not None else None,
         f"{rvol_accel:.2f}x recent vs baseline" if rvol_accel is not None
         else "unavailable"),
    ]
    result = blend(parts)
    result.update({
        "up_bar_volume": up_vol, "down_bar_volume": down_vol,
        "up_share_pct": up_share, "median_bar_volume": median,
        "sequence": sequence, "warnings": warnings,
        "expansion_failed": bool(follow_score is not None and follow_score <= 10.0),
    })
    return result
