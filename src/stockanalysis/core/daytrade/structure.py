"""
structure.py — §5 premarket structure and §6 intraday structure
===============================================================
`build_session()` reduces three frames of bars into the one snapshot every
other engine reads: previous-day levels, premarket levels, the opening
range, session VWAP, EMAs and the running high/low. `detect_patterns()`
then names what price has actually done against those levels.

Why a snapshot rather than each engine slicing bars itself
----------------------------------------------------------
Every §6 level is a function of where the session boundary is drawn, and
there are four defensible places to draw it (04:00, 09:30, first print,
midnight). If volatility.py, room.py and strength.py each sliced their own
bars, they would eventually disagree about what "today" means, and the
resulting report would show a VWAP from one definition next to a gap from
another. One slice, computed once, passed everywhere.

Live sessions and finished ones
-------------------------------
The engine runs against whatever the most recent session in the data is.
On a Sunday that is Friday, and every level below is Friday's — which is
correct and useful for preparation, but it is not a live scan, so
`is_live` and `asof` are carried on the snapshot and the report states
which it is. A finished session silently presented as a live one is the
worst failure this module could have: every level would look actionable
and every one of them would be twelve hours stale.

Detection honesty
-----------------
`detect_patterns()` returns only patterns it can actually verify from
bars. §6 lists high-volume nodes, low-volume nodes and POC; those come
from room.py's volume profile, not from pattern matching, and are not
faked here. Where a pattern needs more history than Yahoo's 7-day 1m
retention allows, it is omitted rather than approximated.
"""

from __future__ import annotations

import pandas as pd

from stockanalysis.core.daytrade._common import (
    AFTERHOURS_END, MARKET_CLOSE, MARKET_OPEN, ORB_END, PREMARKET_START,
    f, pct, session_slice, sessions_in, vwap,
)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _last(series) -> float | None:
    if series is None or len(series) == 0:
        return None
    return f(series.iloc[-1])


def build_session(bars_1m: pd.DataFrame, bars_5m: pd.DataFrame,
                  daily: pd.DataFrame, asof=None) -> dict | None:
    """Collapse bars into the §5/§6 level snapshot for one session.

    `asof` picks the session; default is the most recent one present in the
    1-minute data. Returns None when there are no intraday bars at all,
    which happens for symbols too illiquid to print — a case the caller
    must drop rather than score.
    """
    if bars_1m is None or bars_1m.empty:
        return None
    days = sessions_in(bars_1m)
    if not days:
        return None
    day = asof or days[-1]
    if day not in days:
        return None

    regular = session_slice(bars_1m, day, MARKET_OPEN, MARKET_CLOSE)
    premkt = session_slice(bars_1m, day, PREMARKET_START, MARKET_OPEN)
    opening = session_slice(bars_1m, day, MARKET_OPEN, ORB_END)
    extended = session_slice(bars_1m, day, PREMARKET_START, AFTERHOURS_END)

    # Previous session: the last trading day before `day` that has daily
    # data. Taken from the daily frame rather than the intraday one so a
    # holiday-shortened week or a 7-day retention edge cannot silently make
    # "previous close" mean four sessions ago.
    prev_close = prev_high = prev_low = None
    if daily is not None and not daily.empty:
        hist = daily[daily.index.date < day]
        if not hist.empty:
            row = hist.iloc[-1]
            prev_close, prev_high, prev_low = (f(row["Close"]), f(row["High"]),
                                               f(row["Low"]))
    prev_vwap = None
    prior_days = [d for d in days if d < day]
    if prior_days:
        prev_vwap = vwap(session_slice(bars_1m, prior_days[-1],
                                       MARKET_OPEN, MARKET_CLOSE))

    # Price of record. A finished session uses the regular-hours close, not
    # the after-hours print, because every level here is a regular-session
    # level and mixing an 18:40 tape into them creates gaps that never
    # traded. Premarket-only data (a scan run before 09:30) falls back to
    # the last premarket print, which is genuinely the current price.
    has_regular = not regular.empty
    price = _last(regular["Close"]) if has_regular else _last(
        premkt["Close"] if not premkt.empty else extended["Close"])

    open_price = f(regular["Open"].iloc[0]) if has_regular else None
    session_vwap = vwap(regular) if has_regular else None

    # 9/20 EMA on 5-minute bars, warmed up on prior sessions and then read
    # at the session's last bar. Seeding the EMA at 09:30 would leave it
    # tracking the first few prints instead of describing trend.
    ema9 = ema20 = None
    if bars_5m is not None and not bars_5m.empty:
        upto = bars_5m[bars_5m.index.date <= day]
        if len(upto) >= 20:
            ema9 = _last(_ema(upto["Close"], 9))
            ema20 = _last(_ema(upto["Close"], 20))

    pm_volume = float(premkt["Volume"].sum()) if not premkt.empty else None
    pm_high = f(premkt["High"].max()) if not premkt.empty else None
    pm_low = f(premkt["Low"].min()) if not premkt.empty else None

    # Gap is measured open-vs-prev-close once the session has opened, and
    # last-premarket-print-vs-prev-close before it has. Both are "the gap",
    # and which one is in force changes what the number means, so the
    # basis travels with it.
    if has_regular:
        gap_pct, gap_basis = pct(open_price, prev_close), "open vs prev close"
    else:
        gap_pct, gap_basis = pct(price, prev_close), "premarket vs prev close"

    return {
        "asof": day,
        "is_live": False,          # set by the caller, which knows the clock
        "price": price,
        "prev_close": prev_close,
        "prev_high": prev_high,
        "prev_low": prev_low,
        "prev_vwap": prev_vwap,
        "pm_high": pm_high,
        "pm_low": pm_low,
        "pm_vwap": vwap(premkt),
        "pm_volume": pm_volume,
        "pm_range_pct": pct(pm_high, pm_low) if pm_high and pm_low else None,
        "open": open_price,
        "gap_pct": gap_pct,
        "gap_basis": gap_basis,
        "or_high": f(opening["High"].max()) if not opening.empty else None,
        "or_low": f(opening["Low"].min()) if not opening.empty else None,
        "day_high": f(regular["High"].max()) if has_regular else None,
        "day_low": f(regular["Low"].min()) if has_regular else None,
        "day_range_pct": (pct(f(regular["High"].max()), f(regular["Low"].min()))
                          if has_regular else None),
        "vwap": session_vwap,
        "ema9": ema9,
        "ema20": ema20,
        "cum_volume": float(regular["Volume"].sum()) if has_regular else 0.0,
        "dollar_volume": (float((regular["Close"] * regular["Volume"]).sum())
                          if has_regular else None),
        # Median dollar volume in a single minute over the last half hour.
        # Session dollar volume says whether the stock is liquid; this says
        # whether *your* position can get out, which is the question §14's
        # liquidity constraint actually needs. Median over the recent
        # window rather than the session mean, because the 09:30 print
        # would otherwise set an expectation that no other minute meets.
        "minute_dollar_volume": (
            float((regular["Close"] * regular["Volume"]).tail(30).median())
            if has_regular and len(regular) >= 5 else None),
        "bars": regular,
        "premarket_bars": premkt,
        "opening_bars": opening,
    }


# ── §6 pattern detection ─────────────────────────────────────────────────────

def _swings(closes: pd.Series, window: int = 3):
    """Local highs/lows on a rolling window, as (position, price) pairs."""
    highs, lows = [], []
    vals = closes.values
    for i in range(window, len(vals) - window):
        seg = vals[i - window:i + window + 1]
        if vals[i] == seg.max():
            highs.append((i, float(vals[i])))
        if vals[i] == seg.min():
            lows.append((i, float(vals[i])))
    return highs, lows


def detect_patterns(sess: dict) -> dict:
    """Name the §6 structures price has actually made this session.

    Returns {"patterns": [...], "primary": str|None, "above_vwap": bool|None,
    "notes": [...]}. `primary` is the one the trade plan is built from:
    the highest-conviction structure present, ordered by how specific a
    trigger it implies rather than by how bullish it sounds.
    """
    bars = sess.get("bars")
    if bars is None or bars.empty:
        return {"patterns": [], "primary": None, "above_vwap": None,
                "notes": ["no regular-session bars"]}

    close = bars["Close"]
    price = sess.get("price")
    vw = sess.get("vwap")
    found, notes = [], []

    above_vwap = None if (price is None or vw is None) else price > vw
    if above_vwap is True:
        found.append("ABOVE_VWAP")
    elif above_vwap is False:
        found.append("BELOW_VWAP")

    # VWAP interaction over the session, not just the current bar: whether
    # price crossed and held is a different fact from where it sits now.
    if vw is not None and len(close) > 5:
        rel = close > vw
        crossed_up = bool((~rel.iloc[:-3] .astype(bool)).any() and rel.iloc[-3:].all())
        crossed_dn = bool((rel.iloc[:-3].astype(bool)).any() and (~rel.iloc[-3:]).all())
        if crossed_up:
            found.append("VWAP_RECLAIM")
        if crossed_dn:
            found.append("VWAP_LOST")
        # A bounce is a touch that held; a rejection is a touch that did not.
        lows_at_vwap = (bars["Low"] <= vw) & (bars["Close"] > vw)
        highs_at_vwap = (bars["High"] >= vw) & (bars["Close"] < vw)
        if above_vwap and lows_at_vwap.tail(30).sum() >= 2:
            found.append("VWAP_BOUNCE")
        if above_vwap is False and highs_at_vwap.tail(30).sum() >= 2:
            found.append("VWAP_REJECTION")

    def _broke(level, direction):
        if level is None or price is None:
            return False
        return price > level if direction == "up" else price < level

    if _broke(sess.get("or_high"), "up"):
        found.append("ORB_BREAKOUT")
    if _broke(sess.get("or_low"), "down"):
        found.append("ORB_BREAKDOWN")
    if _broke(sess.get("pm_high"), "up"):
        found.append("PM_HIGH_BREAKOUT")
    if _broke(sess.get("pm_low"), "down"):
        found.append("PM_LOW_BREAKDOWN")
    if _broke(sess.get("prev_high"), "up"):
        found.append("PDH_BREAKOUT")
    if _broke(sess.get("prev_low"), "down"):
        found.append("PDL_BREAKDOWN")

    # Failed breakouts: the session traded through the level and gave it
    # back. This is the single most expensive pattern to misread as a
    # breakout, so it is detected explicitly and it downgrades (§17).
    for level, name in ((sess.get("or_high"), "OR high"),
                        (sess.get("pm_high"), "PM high"),
                        (sess.get("prev_high"), "prev-day high")):
        if level and price and bars["High"].max() > level * 1.002 and price < level:
            found.append("FAILED_BREAKOUT")
            notes.append(f"traded above {name} {level:.2f} and lost it")
            break
    for level, name in ((sess.get("or_low"), "OR low"),
                        (sess.get("pm_low"), "PM low"),
                        (sess.get("prev_low"), "prev-day low")):
        if level and price and bars["Low"].min() < level * 0.998 and price > level:
            found.append("FAILED_BREAKDOWN")
            notes.append(f"traded below {name} {level:.2f} and reclaimed it")
            break

    # Trend continuation from swing structure.
    highs, lows = _swings(close)
    if len(lows) >= 2 and lows[-1][1] > lows[-2][1]:
        found.append("HIGHER_LOW_CONTINUATION")
    if len(highs) >= 2 and highs[-1][1] < highs[-2][1]:
        found.append("LOWER_HIGH_CONTINUATION")

    # Flags: an impulse leg, then a shallow drift against it on lighter
    # volume. The volume condition is what separates a flag from a
    # reversal, so it is required rather than decorative.
    if len(bars) >= 30:
        impulse = bars.iloc[-30:-10]
        pullback = bars.iloc[-10:]
        imp_move = pct(f(impulse["Close"].iloc[-1]), f(impulse["Close"].iloc[0])) or 0.0
        pb_move = pct(f(pullback["Close"].iloc[-1]), f(pullback["Close"].iloc[0])) or 0.0
        imp_vol = float(impulse["Volume"].mean() or 0)
        pb_vol = float(pullback["Volume"].mean() or 0)
        lighter = imp_vol > 0 and pb_vol < imp_vol * 0.8
        if imp_move > 2 and -imp_move * 0.5 < pb_move <= 0.5 and lighter:
            found.append("BULL_FLAG")
        if imp_move < -2 and 0 > pb_move * -1 >= imp_move * 0.5 and lighter:
            found.append("BEAR_FLAG")

        # Consolidation breakout: a contracted range that price has just
        # left. Measured against the session's own range so it means the
        # same thing on a 4% mover and a 40% one.
        day_rng = (sess.get("day_high") or 0) - (sess.get("day_low") or 0)
        base = bars.iloc[-20:-3]
        if day_rng > 0 and not base.empty:
            base_rng = float(base["High"].max() - base["Low"].min())
            if base_rng < day_rng * 0.35:
                if price and price > float(base["High"].max()):
                    found.append("CONSOLIDATION_BREAKOUT")
                elif price and price < float(base["Low"].min()):
                    found.append("CONSOLIDATION_BREAKDOWN")

    # Primary setup. Ordered by how specific a trigger the structure gives,
    # which is not the same as how bullish it is: an ORB breakout names an
    # exact price to trade against, "above VWAP" names none.
    priority = ("FAILED_BREAKOUT", "FAILED_BREAKDOWN", "ORB_BREAKOUT",
                "PDH_BREAKOUT", "PM_HIGH_BREAKOUT", "CONSOLIDATION_BREAKOUT",
                "BULL_FLAG", "VWAP_RECLAIM", "VWAP_BOUNCE",
                "HIGHER_LOW_CONTINUATION", "ORB_BREAKDOWN", "PDL_BREAKDOWN",
                "PM_LOW_BREAKDOWN", "CONSOLIDATION_BREAKDOWN", "BEAR_FLAG",
                "VWAP_REJECTION", "LOWER_HIGH_CONTINUATION")
    primary = next((p for p in priority if p in found), None)

    return {"patterns": found, "primary": primary, "above_vwap": above_vwap,
            "notes": notes}


# Which patterns argue which way. Used by the engine to decide whether a
# candidate is a long or a short before it prices anything — a setup whose
# structure is bearish must never be handed a long trade plan.
LONG_PATTERNS = {
    "ORB_BREAKOUT", "PDH_BREAKOUT", "PM_HIGH_BREAKOUT", "CONSOLIDATION_BREAKOUT",
    "BULL_FLAG", "VWAP_RECLAIM", "VWAP_BOUNCE", "HIGHER_LOW_CONTINUATION",
    "FAILED_BREAKDOWN", "ABOVE_VWAP",
}
SHORT_PATTERNS = {
    "ORB_BREAKDOWN", "PDL_BREAKDOWN", "PM_LOW_BREAKDOWN", "CONSOLIDATION_BREAKDOWN",
    "BEAR_FLAG", "VWAP_REJECTION", "LOWER_HIGH_CONTINUATION", "VWAP_LOST",
    "FAILED_BREAKOUT", "BELOW_VWAP",
}


def score_setup(sess: dict, pat: dict) -> dict:
    """§6 technical setup quality, 0-100 — the 25-point block in §10.

    Scored on what the structure gives a trader, in this order: a named
    trigger level, agreement between independent structures, position
    relative to VWAP, and trend alignment via the EMAs. A stock that is
    merely up a lot with no structure scores poorly here, which is the
    intent — §18's whole argument is that volatility without structure is
    not a trade.
    """
    found = set(pat.get("patterns") or [])
    if not found:
        return {"score": None, "detail": "no structure detected"}

    direction = "long" if len(found & LONG_PATTERNS) >= len(found & SHORT_PATTERNS) else "short"
    aligned = found & (LONG_PATTERNS if direction == "long" else SHORT_PATTERNS)
    against = found & (SHORT_PATTERNS if direction == "long" else LONG_PATTERNS)

    # Structures that name an exact trigger price are worth more than
    # descriptive ones. ABOVE_VWAP is context; ORB_BREAKOUT is an order.
    triggered = {"ORB_BREAKOUT", "ORB_BREAKDOWN", "PDH_BREAKOUT", "PDL_BREAKDOWN",
                 "PM_HIGH_BREAKOUT", "PM_LOW_BREAKDOWN", "CONSOLIDATION_BREAKOUT",
                 "CONSOLIDATION_BREAKDOWN", "BULL_FLAG", "BEAR_FLAG"}
    score = 25.0
    score += 25.0 * min(1.0, len(aligned & triggered) / 3.0)
    score += 15.0 * min(1.0, max(0, len(aligned) - 1) / 3.0)

    if pat.get("above_vwap") is (direction == "long"):
        score += 15.0
    if sess.get("ema9") and sess.get("ema20"):
        rising = sess["ema9"] > sess["ema20"]
        if rising is (direction == "long"):
            score += 10.0
    # Contradictory structure is subtracted, not ignored. A failed breakout
    # sitting inside an otherwise-bullish read is the exact configuration
    # that produces confident losing trades.
    score -= 12.0 * len(against & {"FAILED_BREAKOUT", "FAILED_BREAKDOWN"})
    score -= 5.0 * len(against - {"FAILED_BREAKOUT", "FAILED_BREAKDOWN"})

    detail = f"{direction}: " + ", ".join(sorted(aligned)) if aligned else direction
    if against:
        detail += f" | against: {', '.join(sorted(against))}"
    return {"score": round(max(0.0, min(100.0, score))), "direction": direction,
            "detail": detail, "aligned": sorted(aligned), "against": sorted(against)}
