"""
Bearish reversal detection — the trigger that separates NOW from WATCH.

The long-term engine's `daily_signals._reversal_candle()` names BULLISH
patterns only: hammer, bullish engulfing, piercing line. Nothing in this
project has ever looked for the bearish mirror, so a short engine that
reused it would have had no way to tell "extended and rolling over" from
"extended and still going up". Those are the two states the whole
NOW-versus-WATCH distinction rests on.

So the bearish patterns are detected here, deliberately as the mirror of
the existing bullish ones plus the two that have no bullish counterpart
worth naming (shooting star's cousin, the evening star).

Nothing here is a short signal on its own. A bearish engulfing in an
uptrend is noise; the engine requires the extension, the valuation and
the distribution first, and consults this only to decide whether price
has begun to confirm what those already said. That ordering is the
discipline — the pattern is the trigger, never the thesis.

`None` means too few bars to look. `"none"` means it was looked for and
is not there. The engine treats them differently: unknown blocks
confirmation as a data gap, absent blocks it as a fact.
"""

from __future__ import annotations

# How far back a pattern still counts as "today's" trigger. A bearish
# engulfing four sessions ago that has been bought back is not a trigger.
FRESH_BARS = 2


def _safe(v):
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _bars(daily, i):
    """(open, high, low, close, volume) for row `i`, or None."""
    try:
        r = daily.iloc[i]
    except (IndexError, KeyError):
        return None
    vals = (_safe(r.get("Open")), _safe(r.get("High")), _safe(r.get("Low")),
            _safe(r.get("Close")), _safe(r.get("Volume")))
    return None if any(v is None for v in vals[:4]) else vals


def detect(daily) -> str | None:
    """Name the bearish reversal on the most recent bar, or "none".

    Checked strongest first; only one is reported. Mirrors the bullish
    detector's conventions exactly so the two read as a matched pair.
    """
    if daily is None or len(daily) < 3:
        return None

    cur, prev = _bars(daily, -1), _bars(daily, -2)
    if cur is None or prev is None:
        return None
    o, h, l, cl, _ = cur
    po, ph, pl, pcl, _ = prev

    rng = h - l
    body = abs(cl - o)

    # Bearish engulfing: yesterday green, today red and covering it whole.
    if pcl > po and cl < o and cl <= po and o >= pcl:
        return "bearish engulfing"

    # Evening star: a strong up bar, a small indecisive bar, then a red
    # bar closing back into the first one's body. Three-bar topping
    # pattern with no two-bar equivalent.
    third = _bars(daily, -3)
    if third is not None:
        to, _, _, tcl, _ = third
        mid_body = abs(pcl - po)
        first_body = abs(tcl - to)
        if (tcl > to and first_body > 0
                and mid_body <= first_body * 0.4
                and cl < o and cl < (to + tcl) / 2):
            return "evening star"

    # Shooting star: long upper wick, small body near the low of the
    # range. The session was bought and sold back — the mirror of the
    # hammer, and the classic exhaustion bar at a high.
    if rng > 0:
        upper_wick = h - max(o, cl)
        lower_wick = min(o, cl) - l
        if (upper_wick >= 2 * body and body > 0
                and lower_wick <= body and (h - cl) / rng >= 0.6):
            return "shooting star"

    # Dark cloud cover: opens above yesterday's high, closes back through
    # the midpoint of yesterday's green body. Mirror of piercing line.
    if pcl > po and o > pcl and cl < (po + pcl) / 2 and cl > po:
        return "dark cloud cover"

    return "none"


def first_red_day(daily) -> dict:
    """The First Red Day setup: a run of green closes ending in a red one.

    This is the small-cap momentum-exhaustion trigger, and it is a
    different claim from a candle pattern — it says the streak that
    produced the extension has broken, not that one bar looked bad.

    Returns {"triggered", "green_streak", "detail"}. A streak of at least
    three is required: one or two green days ending red is ordinary noise.
    """
    if daily is None or len(daily) < 5:
        return {"triggered": None, "green_streak": None,
                "detail": "too few bars"}

    cur = _bars(daily, -1)
    if cur is None:
        return {"triggered": None, "green_streak": None,
                "detail": "current bar unreadable"}
    o, _, _, cl, vol = cur
    if cl >= o:
        return {"triggered": False, "green_streak": 0,
                "detail": "today closed green"}

    streak = 0
    for i in range(-2, -12, -1):
        b = _bars(daily, i)
        if b is None or b[3] <= b[0]:
            break
        streak += 1

    triggered = streak >= 3
    return {
        "triggered": triggered,
        "green_streak": streak,
        "detail": (f"first red close after {streak} green sessions"
                   if triggered else
                   f"red close, but only {streak} green before it"),
    }


def confirmation(daily) -> dict:
    """The full bearish-confirmation reading the engine consults.

    Combines the candle, the first-red-day break and whether today's
    selling carried volume. Volume matters here in a way it does not for
    the thesis: distribution over 25 days says institutions have been
    leaving, while volume on the reversal bar says they are leaving
    TODAY, which is what makes it a trigger rather than a forecast.
    """
    candle = detect(daily)
    frd = first_red_day(daily)

    heavy = None
    if daily is not None and len(daily) >= 21:
        cur = _bars(daily, -1)
        if cur and cur[4] is not None:
            vols = [_bars(daily, i)[4] for i in range(-21, -1)
                    if _bars(daily, i) and _bars(daily, i)[4] is not None]
            if vols:
                avg = sum(vols) / len(vols)
                heavy = bool(avg > 0 and cur[4] >= avg * 1.3)

    signals = []
    if candle and candle != "none":
        signals.append(candle)
    if frd.get("triggered"):
        signals.append("first red day")
    if heavy and signals:
        signals.append("on heavy volume")

    if candle is None:
        state, label = "UNKNOWN", "⚪ No bar data"
    elif not signals:
        state, label = "NONE", "⚪ Not confirmed"
    elif len(signals) >= 2:
        state, label = "STRONG", "🔴 Confirmed"
    else:
        state, label = "WEAK", "🟠 Early"

    return {
        "state": state,
        "label": label,
        "candle": candle,
        "first_red_day": frd,
        "heavy_volume": heavy,
        "signals": signals,
        "confirmed": state == "STRONG",
        "detail": (", ".join(signals) if signals else
                   "no bearish reversal on the tape yet"),
    }


def fetch_daily(ticker: str, days: int = 90):
    """Daily bars for reversal detection.

    Never tz-converts: daily bars carry a date, and converting shifts
    them back a day, which would silently compare today's open against
    yesterday's close throughout this module.
    """
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period=f"{days}d", interval="1d",
                                      auto_adjust=False)
        return None if h is None or h.empty else h
    except Exception as e:
        print(f"[Short] {ticker}: bars unavailable ({e})")
        return None
