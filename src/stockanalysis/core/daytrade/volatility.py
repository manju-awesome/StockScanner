"""
volatility.py — §3 volatility engine, 0-100
===========================================
Answers one question: can this stock actually travel far enough today to
pay for the risk of trading it? Range, not direction.

RVOL is time-of-day anchored
----------------------------
The naive relative-volume calculation — today's volume so far divided by
the average full day — is wrong in a way that always points the same
direction, and it is the most common defect in retail scanners. At 10:00
a stock has traded maybe a fifth of its normal day, so the naive figure
reads ~0.2 and every genuine morning runner looks quiet. Here, cumulative
volume up to the current bar is compared against the median cumulative
volume at *that same clock time* over the trailing sessions. At 10:00 it
is judged against other 10:00s.

That requires an intraday baseline, so RVOL is built from 5-minute bars
across Yahoo's ~60-day retention rather than from daily volume. When there
are too few comparable sessions the value is None, and the score
renormalises around it — a scanner that cannot compute RVOL should say so,
not print 1.0.

ATR is daily, ATR% is what ranks
--------------------------------
A $2.20 ATR means nothing until it is divided by price. §1 wants ATR% > 4%,
which on a $3 stock is $0.12 and on a $28 stock is $1.12 — the same trade
in risk terms and wildly different in dollars.
"""

from __future__ import annotations

import pandas as pd

from stockanalysis.core.daytrade._common import (
    MARKET_CLOSE, MARKET_OPEN, band, blend, f, pct, session_slice, sessions_in,
)

# §1's stated preferences, as the points at which each metric stops
# discriminating. Past the top band more is not better — a 40% ATR stock is
# not twice the opportunity of a 20% one, it is twice the slippage.
ATR_PCT_BANDS   = ((2.0, 0), (3.0, 25), (4.0, 50), (6.0, 75), (9.0, 100), (99, 85))
RVOL_BANDS      = ((1.0, 0), (1.5, 20), (2.0, 45), (3.0, 70), (5.0, 90), (999, 100))
GAP_BANDS       = ((1.0, 0), (3.0, 30), (5.0, 55), (10.0, 80), (20.0, 100), (999, 80))
DOLLAR_VOL_BANDS = ((1e6, 0), (5e6, 40), (2e7, 70), (1e8, 100), (1e12, 100))

# On a day whose realised range already exceeds ATR, the share of that
# range still considered available. See expected_remaining_move().
CONTINUATION_SHARE = 0.35

# Median baseline shares below which an RVOL ratio is meaningless.
MIN_RVOL_BASELINE = 25_000


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """Wilder-smoothed ATR, matching core/metrics.py so the two scanners
    cannot quote different ATRs for the same stock."""
    if df is None or len(df) < period + 1:
        return None
    tr = true_range(df)
    val = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return f(val.iloc[-1])


def relative_volume(bars_5m: pd.DataFrame, day, lookback: int = 20) -> dict:
    """Time-of-day RVOL, plus the acceleration §3 asks for.

    Returns {"rvol", "rvol_accel", "sessions_used", "cum_volume"}. `rvol_accel`
    compares the last 30 minutes against the same 30-minute window on the
    baseline days — it is what separates a stock that gapped on volume at
    09:30 and has since gone quiet from one that is being bought right now.
    """
    out = {"rvol": None, "rvol_accel": None, "sessions_used": 0,
           "cum_volume": None, "baseline_too_thin": False, "baseline_volume": None}
    if bars_5m is None or bars_5m.empty:
        return out
    days = [d for d in sessions_in(bars_5m) if d <= day]
    if day not in days:
        return out
    baseline_days = days[-(lookback + 1):-1]
    today = session_slice(bars_5m, day, MARKET_OPEN, MARKET_CLOSE)
    if today.empty:
        return out

    cutoff = today.index[-1].time()
    out["cum_volume"] = float(today["Volume"].sum())

    def _cum_to_cutoff(d):
        sess = session_slice(bars_5m, d, MARKET_OPEN, MARKET_CLOSE)
        if sess.empty:
            return None
        upto = sess[sess.index.time <= cutoff]
        return float(upto["Volume"].sum()) if not upto.empty else None

    prior = [v for v in (_cum_to_cutoff(d) for d in baseline_days) if v and v > 0]
    out["sessions_used"] = len(prior)
    # Median, not mean: one prior catalyst day in the window would drag a
    # mean up enough to hide today's expansion entirely.
    if len(prior) >= 5:
        med = float(pd.Series(prior).median())
        # A ratio against a baseline of a few thousand shares is arithmetic,
        # not a measurement. Near-dormant tickers produced RVOLs of 3,562
        # and 9,401 — numbers that say only "this used to trade nothing",
        # printed with a precision that implies otherwise. Below the floor
        # RVOL is undefined and the gap and dollar-volume factors carry §3
        # on their own.
        if med >= MIN_RVOL_BASELINE:
            out["rvol"] = round(out["cum_volume"] / med, 2)
        else:
            out["baseline_too_thin"] = True
            out["baseline_volume"] = med

    # Acceleration over the trailing half hour.
    recent = today.tail(6)
    if len(recent) >= 3 and len(prior) >= 5:
        start = recent.index[0].time()
        window_prior = []
        for d in baseline_days:
            sess = session_slice(bars_5m, d, MARKET_OPEN, MARKET_CLOSE)
            if sess.empty:
                continue
            w = sess[(sess.index.time >= start) & (sess.index.time <= cutoff)]
            if not w.empty:
                window_prior.append(float(w["Volume"].sum()))
        if window_prior:
            med_w = float(pd.Series(window_prior).median())
            if med_w > 0:
                out["rvol_accel"] = round(float(recent["Volume"].sum()) / med_w, 2)
    return out


def expected_remaining_move(atr_pct: float | None, day_range_pct: float | None) -> float | None:
    """How much further this stock can reasonably travel today, in percent.

    Unspent daily ATR is the obvious estimate and it is wrong for exactly
    the stocks this scanner selects. A name that has already traded a 14%
    range on a 7.9% ATR has "used up" its ATR twice over, so the naive
    `ATR% − range so far` returns zero and declares a stock in the middle
    of a 60% day incapable of moving — which then propagates into §9,
    where every candidate looks blocked and no R:R can be computed.

    The resolution is that ATR is simply the wrong scale on an expansion
    day. Once realised range exceeds ATR, the day's own range is the
    better predictor of what remains, so the estimate becomes a fraction
    of what has already happened. `CONTINUATION_SHARE` is a heuristic, not
    a measurement, and it is the one number in this package that is — it
    exists to keep §9's ratios on a sane scale rather than to forecast.
    """
    if atr_pct is None:
        return None
    unspent = atr_pct - (day_range_pct or 0.0)
    if day_range_pct is None:
        return max(0.0, unspent)
    return max(unspent, day_range_pct * CONTINUATION_SHARE)


def compute(sess: dict, daily: pd.DataFrame, bars_5m: pd.DataFrame) -> dict:
    """§3 volatility score, 0-100, with the raw measurements attached."""
    price = sess.get("price")
    # ATR from completed sessions only. Including the session under
    # examination would let today's own range set the yardstick today's
    # range is being measured against, and in an as-of replay it is
    # outright look-ahead: the 10:15 scan would be using the 16:00 bar.
    hist = daily[daily.index.date < sess["asof"]] if daily is not None and not daily.empty else daily
    atr14 = atr(hist, 14)
    atr5 = atr(hist, 5)
    atr_pct = (atr14 / price * 100.0) if (atr14 and price) else None
    rv = relative_volume(bars_5m, sess["asof"])

    gap = sess.get("gap_pct")
    gap_abs = abs(gap) if gap is not None else None
    dollar_vol = sess.get("dollar_volume")

    parts = [
        ("ATR%", 30, band(atr_pct, ATR_PCT_BANDS) if atr_pct is not None else None,
         f"{atr_pct:.1f}%" if atr_pct is not None else "unavailable"),
        ("RVOL", 30, band(rv["rvol"], RVOL_BANDS) if rv["rvol"] is not None else None,
         f"{rv['rvol']:.2f}x ({rv['sessions_used']} sessions)" if rv["rvol"] is not None
         else ("baseline volume too thin to divide by" if rv.get("baseline_too_thin")
               else "insufficient intraday history")),
        ("Gap", 20, band(gap_abs, GAP_BANDS) if gap_abs is not None else None,
         f"{gap:+.1f}% ({sess.get('gap_basis')})" if gap is not None else "unavailable"),
        ("Dollar volume", 20,
         band(dollar_vol, DOLLAR_VOL_BANDS) if dollar_vol is not None else None,
         f"${dollar_vol/1e6:.1f}M" if dollar_vol else "unavailable"),
    ]
    result = blend(parts)
    result.update({
        "atr": atr14, "atr_pct": atr_pct, "atr5": atr5,
        "rvol": rv["rvol"], "rvol_accel": rv["rvol_accel"],
        "rvol_sessions": rv["sessions_used"],
        "gap_pct": gap, "dollar_volume": dollar_vol,
        "pm_range_pct": sess.get("pm_range_pct"),
        "day_range_pct": sess.get("day_range_pct"),
        # Used by §9 to judge whether the room to the next level is even
        # reachable in the session that remains.
        "expected_move_pct": expected_remaining_move(
            atr_pct, sess.get("day_range_pct")),
    })
    return result
