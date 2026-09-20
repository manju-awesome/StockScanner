"""
Shared primitives for the stock day-trade engine.

The generic coercion/scoring helpers (`f`, `b`, `s`, `scale`, `inverse`,
`band`, `blend`) are imported from `core.longterm._common` rather than
recopied. That module's own docstring is a complaint about four copies of
`_f` drifting apart, and making it five would be an odd way to honour it.
Nothing about those seven functions is long-term-specific — they are
arithmetic — and the import is one-directional, so the packages stay
independent in the direction that matters (longterm never imports daytrade).

What IS defined here is the vocabulary this engine needs and the long-term
one does not: session clocks, whole-number magnetism, and the distinction
between a score that is low and a score that is unknown when the market is
shut.
"""

from __future__ import annotations

from datetime import time

import pandas as pd
import pytz

from stockanalysis.core.longterm._common import (  # noqa: F401  (re-exported)
    b, blend, band, f, inverse, s, scale,
)

# ── Session clock ────────────────────────────────────────────────────────────
# Matched to core/metrics.py, which already fixes 04:00 / 09:30 / 09:45 for
# the swing scanner. Two different opening-range definitions inside one repo
# is the kind of thing that makes two reports disagree for a reason nobody
# can find, so the 15-minute ORB is inherited rather than re-chosen.
MARKET_TZ       = pytz.timezone("America/New_York")
PREMARKET_START = time(4, 0)
MARKET_OPEN     = time(9, 30)
ORB_END         = time(9, 45)
MARKET_CLOSE    = time(16, 0)
AFTERHOURS_END  = time(20, 0)


def to_et(df: pd.DataFrame) -> pd.DataFrame:
    """Intraday bars indexed in US/Eastern. yfinance returns tz-aware data
    for intraday intervals, but the tz varies with the exchange, and naive
    frames show up in cached/mocked data — both are normalised here so no
    downstream module has to think about it."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    out.index = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(MARKET_TZ)
    return out.sort_index()


def session_slice(df: pd.DataFrame, day, start: time, end: time) -> pd.DataFrame:
    """Bars for one calendar day between two wall-clock times, end-exclusive.

    End-exclusive matters for the opening range: a 09:45 bar is the first
    bar of the breakout window, not the last bar of the range. Including it
    lets the range absorb the very move it is supposed to be broken by.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    same_day = df[df.index.date == day]
    if same_day.empty:
        return same_day
    t = same_day.index.time
    return same_day[(t >= start) & (t < end)]


def sessions_in(df: pd.DataFrame) -> list:
    """Distinct trading dates present, oldest first."""
    if df is None or df.empty:
        return []
    return sorted({d for d in df.index.date})


def truncate_at(df: pd.DataFrame, day, at: time) -> pd.DataFrame:
    """Drop bars after `at` on `day`, keeping every earlier session whole.

    This is what makes an as-of replay honest. Prior sessions must survive
    intact because they are the baselines — the RVOL median, the EMA
    warm-up, the previous close — and truncating those too would compare
    a partial day against other partial days and quietly halve every
    baseline. Only the day under examination is cut.
    """
    if df is None or df.empty:
        return df
    keep = (df.index.date != day) | (df.index.time <= at)
    return df[keep]


def vwap(df: pd.DataFrame) -> float | None:
    """Volume-weighted average price over the bars given, on typical price.

    Anchoring is the caller's job — pass the regular-hours bars for session
    VWAP, the 04:00-09:30 bars for premarket VWAP. Zero total volume returns
    None rather than a divide-by-zero or a silent fallback to the mean,
    because "VWAP with no volume behind it" is not a level anyone defends.
    """
    if df is None or df.empty:
        return None
    vol = df["Volume"].fillna(0.0)
    total = float(vol.sum())
    if total <= 0:
        return None
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    return float((typical * vol).sum() / total)


def pct(a: float | None, b_: float | None) -> float | None:
    """Percentage change from `b_` to `a`. None-safe, and refuses a zero or
    negative base instead of returning an infinity that later formats as a
    plausible-looking number."""
    a, b_ = f(a), f(b_)
    if a is None or b_ is None or b_ <= 0:
        return None
    return (a - b_) / b_ * 100.0


def whole_number_levels(price: float, span_pct: float = 12.0) -> list[float]:
    """Round-number levels near `price` (§6, §9).

    The increment scales with price because round-number magnetism is about
    the digit traders type, not a fixed dollar amount: $0.50 steps matter on
    a $3 stock and are noise on a $28 one.
    """
    p = f(price)
    if p is None or p <= 0:
        return []
    step = 0.50 if p < 5 else (1.0 if p < 15 else 5.0)
    lo, hi = p * (1 - span_pct / 100.0), p * (1 + span_pct / 100.0)
    first = int(lo / step)
    return [round(k * step, 2) for k in range(first, int(hi / step) + 2)
            if lo <= k * step <= hi]


def points(score_0_100: float | None, budget: float) -> float | None:
    """Rescale a canonical 0-100 sub-score onto its §10 point budget.

    Every engine scores 0-100 internally and is converted once, here. The
    spec quotes two different scales for the same engines (§2 calls catalyst
    0-20, §10 weights it 15), and computing directly in points would make
    that contradiction structural. One canonical scale, converted at the
    edges, keeps both presentations honest and neither authoritative.
    """
    v = f(score_0_100)
    return None if v is None else round(v / 100.0 * budget, 1)


def native(obj):
    """Recursively convert numpy/pandas scalars to built-ins.

    json.dumps() chokes on numpy.int64/float64 and pandas.Timestamp, which
    is a recurring failure in this repo whenever yfinance output reaches a
    JSON writer. Applied once at the serialisation boundary.
    """
    if isinstance(obj, dict):
        return {str(k): native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [native(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if hasattr(obj, "item") and callable(obj.item) and getattr(obj, "shape", None) == ():
        return obj.item()
    return obj
