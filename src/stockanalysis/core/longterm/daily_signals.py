"""
daily_signals.py — the chart facts the long-term engine needs that the scan
did not already compute
==========================================================================
Five metrics, all derived from the 1-year daily frame `metrics.py` has
already fetched for other purposes. Pure function, no network, no clock:
same contract as core/key_levels.py, which this module is modelled on, so a
backtest can compute point-in-time values by slicing the frame.

    MA50_Slope%            direction of the 50-day average
    MA200_Slope%           direction of the 200-day average
    Reversal_Candle        the confirming bar, by name, or "none"
    Distribution_Days_25d  institutional selling pressure
    Prior_Breakout_Level   the resistance price broke and now sits above

Why each of these and not something else
-----------------------------------------
The scan already answers "where is price relative to its averages". None of
it answers "which way are the averages going", and that is the difference
between a pullback in an uptrend and the early part of a decline. Price 3%
above a 200-day that is falling is not the same setup as price 3% above one
that is rising, and until these columns existed the engine could not tell
them apart.

The slope window is SLOPE_LOOKBACK sessions — about a month. Shorter and a
200-day average, which by construction moves slowly, reads as noise;
longer and it stops noticing a trend that has just rolled over.

Never raises. On a frame too short for a metric, that metric is None, and
every consumer reads None as "not measured" rather than as a failed check.
"""

from __future__ import annotations

import pandas as pd

LT_DAILY_KEYS = ("MA50_Slope%", "MA200_Slope%", "Reversal_Candle",
                 "Distribution_Days_25d", "Prior_Breakout_Level")
LT_DAILY_DEFAULTS = {k: None for k in LT_DAILY_KEYS}

SLOPE_LOOKBACK = 20          # sessions, ≈1 month
DISTRIBUTION_WINDOW = 25     # sessions the count covers
DISTRIBUTION_DROP = 0.2      # % decline that makes a day a down day
BREAKOUT_LOOKBACK = 120      # ≈6 months of structure
SWING_WINDOW = 5             # bars each side for a fractal pivot high


def _safe(v):
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _slope_pct(series: pd.Series, lookback: int = SLOPE_LOOKBACK):
    """Percent change in the average itself over `lookback` sessions.

    Expressed as a percentage of its own level rather than as a raw price
    slope, so the number is comparable between a $12 stock and a $900 one.
    """
    clean = series.dropna()
    if len(clean) < lookback + 1:
        return None
    now, then = _safe(clean.iloc[-1]), _safe(clean.iloc[-1 - lookback])
    if now is None or then is None or then == 0:
        return None
    return round((now / then - 1) * 100, 2)


def _reversal_candle(daily: pd.DataFrame) -> str | None:
    """Name the bullish reversal pattern on the most recent bar, or "none".

    Returns None only when there are too few bars to look — "none" is a
    measurement (no pattern today), None is the absence of one, and the
    engine treats them differently: a missing candle blocks confirmation as
    unknown, an absent pattern blocks it as a fact.

    Patterns are checked strongest first and only one is reported. All three
    are single- or two-bar formations at the level; none of them is a buy
    signal on its own, which is why the engine also requires the level, the
    volume behaviour and the trend before any of this counts.
    """
    if len(daily) < 3:
        return None
    c = daily.iloc[-1]
    p = daily.iloc[-2]
    o, h, l, cl = (_safe(c["Open"]), _safe(c["High"]),
                   _safe(c["Low"]), _safe(c["Close"]))
    po, pcl = _safe(p["Open"]), _safe(p["Close"])
    if None in (o, h, l, cl, po, pcl):
        return None

    rng = h - l
    body = abs(cl - o)

    # Bullish engulfing: yesterday red, today green and covering it whole.
    if pcl < po and cl > o and cl >= po and o <= pcl:
        return "bullish engulfing"

    # Hammer: long lower wick, small body near the top of the range. The
    # session sold off and was bought back — which is the whole point.
    if rng > 0:
        lower_wick = min(o, cl) - l
        upper_wick = h - max(o, cl)
        if (lower_wick >= 2 * body and body > 0
                and upper_wick <= body and (cl - l) / rng >= 0.6):
            return "hammer"

    # Piercing line: opens below yesterday's low-ish and closes back through
    # the midpoint of yesterday's red body.
    if pcl < po and o < pcl and cl > (po + pcl) / 2 and cl < po:
        return "piercing line"

    return "none"


def _distribution_days(daily: pd.DataFrame) -> int | None:
    """Down sessions on rising volume in the last DISTRIBUTION_WINDOW bars.

    The classic institutional-selling tell: price falls while volume
    expands, meaning size is leaving rather than the tape drifting. One is
    noise; a cluster is the reason a "healthy pullback" is not one.
    """
    if len(daily) < DISTRIBUTION_WINDOW + 1:
        return None
    window = daily.tail(DISTRIBUTION_WINDOW + 1)
    close = window["Close"].astype(float)
    volume = window["Volume"].astype(float)
    change = close.pct_change() * 100
    vol_up = volume.diff() > 0
    hits = ((change <= -DISTRIBUTION_DROP) & vol_up).sum()
    return int(hits)


def _prior_breakout(daily: pd.DataFrame, price: float | None):
    """The highest prior swing high that price has since risen above.

    That level is the breakout: it was resistance, price cleared it, and on
    a pullback it is the first structural floor — §7's "Entry 3, previous
    breakout support". Only pivots BELOW the current price qualify, because
    a swing high overhead is resistance, not support, and the whole engine
    turns on not confusing the two.

    Returns the nearest such level (the highest one under price), which is
    the one a pullback reaches first.
    """
    if price is None or len(daily) < SWING_WINDOW * 2 + 1:
        return None
    window = daily.tail(BREAKOUT_LOOKBACK)
    highs = window["High"].astype(float).to_numpy()
    n = len(highs)
    pivots = []
    for i in range(SWING_WINDOW, n - SWING_WINDOW):
        level = highs[i]
        if level != level:
            continue
        left = highs[i - SWING_WINDOW:i]
        right = highs[i + 1:i + 1 + SWING_WINDOW]
        if len(left) and len(right) and level >= left.max() and level >= right.max():
            pivots.append(float(level))
    below = [lv for lv in pivots if lv < price]
    if not below:
        return None
    return round(max(below), 2)


def compute_lt_daily(daily: pd.DataFrame, price: float | None,
                     ma50: pd.Series | None = None,
                     ma200: pd.Series | None = None) -> dict:
    """
    daily : the ffilled OHLCV frame metrics.compute_daily_metrics() built.
    price : the as-of price (metrics passes the same `p` it uses elsewhere).
    ma50 / ma200 : the rolling means, passed in when the caller already has
            them so they are not recomputed over the same frame twice.

    Never raises. Returns LT_DAILY_DEFAULTS-shaped dict.
    """
    out = dict(LT_DAILY_DEFAULTS)
    if daily is None or daily.empty:
        return out
    try:
        close = daily["Close"].astype(float)
        s50 = ma50 if ma50 is not None else close.rolling(50).mean()
        s200 = ma200 if ma200 is not None else close.rolling(200).mean()
        out["MA50_Slope%"] = _slope_pct(s50)
        out["MA200_Slope%"] = _slope_pct(s200)
        out["Reversal_Candle"] = _reversal_candle(daily)
        out["Distribution_Days_25d"] = _distribution_days(daily)
        out["Prior_Breakout_Level"] = _prior_breakout(daily, price)
    except Exception:
        # Same contract as key_levels: a thin or malformed frame degrades to
        # None columns rather than husking the whole scan row.
        pass
    return out
