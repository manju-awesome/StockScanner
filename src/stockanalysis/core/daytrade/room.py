"""
room.py — §9 room-to-run engine, 0-100
======================================
The question §9 exists to force: if this works, where does it actually go
before it runs into something? A perfect setup into a wall three cents
overhead is not a trade — it is a scratch with commission.

Room is measured in ATR, not percent
-------------------------------------
"2% to the next level" means something completely different on a stock
with a 3% daily range than on one with a 15% range. The former cannot
reach it; the latter passes through it before lunch. So the headline score
is driven by room expressed in units of the stock's own remaining expected
move, and the percentage is reported alongside because that is what §16's
table asks for.

The obstruction gate
--------------------
§9's instruction — "do not recommend a long trade when significant
resistance is immediately above the entry" — is returned as `blocked`, a
hard boolean, not as a low score. A score can be outweighed by five good
ones; a gate cannot. engine.py refuses A+/A on any blocked candidate.

Levels are collected from all six §9 sources, and each carries its origin
so the report can say *what* is overhead rather than just how far.
"""

from __future__ import annotations

import pandas as pd

from stockanalysis.core.daytrade._common import (
    band, f, pct, whole_number_levels,
)

# Room, expressed as a multiple of the expected remaining move.
ROOM_BANDS = ((0.25, 0), (0.5, 20), (1.0, 45), (2.0, 75), (4.0, 95), (99, 100))

# Inside this fraction of the expected move, a level is "immediately"
# overhead in §9's sense and the candidate is gated.
BLOCK_THRESHOLD = 0.35

# Clustered weight at which a level is worth trading to. Below it the
# level is reported as context but never used as a target.
SIGNIFICANT_WEIGHT = 4


def volume_profile(bars: pd.DataFrame, n_bins: int = 40) -> dict:
    """Session volume profile: POC and the high-volume nodes §6 asks for."""
    if bars is None or bars.empty:
        return {"poc": None, "hvn": [], "lvn": []}
    lo, hi = float(bars["Low"].min()), float(bars["High"].max())
    if not (hi > lo):
        return {"poc": None, "hvn": [], "lvn": []}
    typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3.0
    bins = pd.cut(typical, bins=n_bins)
    grouped = bars["Volume"].groupby(bins, observed=False).sum()
    if grouped.empty or float(grouped.max()) <= 0:
        return {"poc": None, "hvn": [], "lvn": []}
    mids = [float(iv.mid) for iv in grouped.index]
    vols = [float(v) for v in grouped.values]
    peak = max(vols)
    poc = mids[vols.index(peak)]
    hvn = [m for m, v in zip(mids, vols) if v >= peak * 0.55]
    lvn = [m for m, v in zip(mids, vols) if 0 < v <= peak * 0.15]
    return {"poc": poc, "hvn": hvn, "lvn": lvn}


def _swing_levels(bars: pd.DataFrame, window: int = 5) -> tuple[list, list]:
    """Intraday swing highs and lows, as §6's 'recent swing highs/lows'."""
    if bars is None or len(bars) < window * 2 + 1:
        return [], []
    highs, lows = [], []
    h, l = bars["High"].values, bars["Low"].values
    for i in range(window, len(bars) - window):
        if h[i] == h[i - window:i + window + 1].max():
            highs.append(float(h[i]))
        if l[i] == l[i - window:i + window + 1].min():
            lows.append(float(l[i]))
    return highs, lows


def collect_levels(sess: dict, daily: pd.DataFrame) -> list[dict]:
    """Every §9 level with its origin, unsorted and unfiltered."""
    bars = sess.get("bars")
    levels: list[dict] = []

    def add(price, source, weight):
        p = f(price)
        if p and p > 0:
            levels.append({"price": p, "source": source, "weight": weight})

    add(sess.get("pm_high"), "premarket high", 3)
    add(sess.get("pm_low"), "premarket low", 3)
    add(sess.get("prev_high"), "prev-day high", 4)
    add(sess.get("prev_low"), "prev-day low", 4)
    add(sess.get("or_high"), "opening-range high", 3)
    add(sess.get("or_low"), "opening-range low", 3)
    add(sess.get("vwap"), "session VWAP", 3)
    add(sess.get("day_high"), "session high", 4)
    add(sess.get("day_low"), "session low", 4)

    prof = volume_profile(bars)
    add(prof["poc"], "volume POC", 4)
    for node in prof["hvn"][:6]:
        add(node, "high-volume node", 2)

    sh, sl = _swing_levels(bars)
    for p in sh[-4:]:
        add(p, "intraday swing high", 2)
    for p in sl[-4:]:
        add(p, "intraday swing low", 2)

    for w in whole_number_levels(sess.get("price") or 0):
        add(w, "whole number", 1)

    # 52-week extremes from completed sessions only. Including the current
    # bar makes the level meaningless — a stock printing a new high all day
    # would keep finding "52-week high" resistance at its own last tick —
    # and in an as-of replay it is look-ahead: RCEL's 8.07 high was offered
    # as a 10:15 resistance level six hours before it traded there.
    if daily is not None and not daily.empty:
        window = daily[daily.index.date < sess["asof"]].tail(252)
        if not window.empty:
            add(window["High"].max(), "52-week high", 5)
            add(window["Low"].min(), "52-week low", 5)

    return levels


def compute(sess: dict, daily: pd.DataFrame, direction: str,
            expected_move_pct: float | None, entry: float | None = None) -> dict:
    """§9 room-to-run, 0-100, from `entry` (defaults to current price).

    For a long, "room" is the distance to the nearest meaningful level
    above; for a short, below. Levels within a hair of the entry are
    ignored — a whole number two cents away is not an obstruction, it is
    the same price.
    """
    price = f(entry) or f(sess.get("price"))
    if price is None or price <= 0:
        return {"score": None, "detail": "no price", "blocked": False,
                "nearest": None, "levels": []}

    levels = collect_levels(sess, daily)
    ahead = [l for l in levels
             if (l["price"] > price * 1.002 if direction == "long"
                 else l["price"] < price * 0.998)]
    ahead.sort(key=lambda l: abs(l["price"] - price))

    # Cluster: several sources naming the same price is one strong level,
    # not three weak ones. Merging them is what lets "PDH + POC + whole
    # number all at 8.00" read as the wall it actually is.
    clustered: list[dict] = []
    for lvl in ahead:
        for c in clustered:
            if abs(c["price"] - lvl["price"]) / price < 0.005:
                c["weight"] += lvl["weight"]
                c["sources"].append(lvl["source"])
                break
        else:
            clustered.append({"price": lvl["price"], "weight": lvl["weight"],
                              "sources": [lvl["source"]]})

    # "Significant" means a clustered weight of 4+ — one major level (PDH,
    # 52-week high, POC) or several minor ones agreeing. Only these are
    # offered as targets. A lone whole number two cents overhead is a real
    # level in the sense that it exists, and useless as a target: taking
    # it produced R:R of 0.10 on every candidate, because the reward was
    # measured to a price the stock was already touching.
    significant = [c for c in clustered if c["weight"] >= SIGNIFICANT_WEIGHT]
    nearest = significant[0] if significant else (clustered[0] if clustered else None)

    if nearest is None:
        # Nothing overhead at all: blue sky. Genuinely the best case, but
        # it also means no reference for a target, so it is scored high
        # and flagged rather than scored perfect.
        return {"score": 90.0, "detail": "no levels ahead — open air",
                "blocked": False, "nearest": None, "nearest_pct": None,
                "expected_move_pct": expected_move_pct, "levels": clustered[:8],
                "targets": [], "room_ratio": None}

    room_pct = abs(pct(nearest["price"], price) or 0.0)
    em = expected_move_pct if (expected_move_pct and expected_move_pct > 0) else None
    room_ratio = (room_pct / em) if em else None

    if room_ratio is not None:
        score = band(room_ratio, ROOM_BANDS)
        blocked = room_ratio < BLOCK_THRESHOLD
        detail = (f"{room_pct:.1f}% to {nearest['price']:.2f} "
                  f"({', '.join(nearest['sources'][:3])}) = "
                  f"{room_ratio:.2f}x expected move")
    else:
        # No expected-move estimate: fall back to raw percent, and never
        # gate on it. Blocking a candidate on a measurement this engine
        # could not make would be exactly the fabrication the package
        # refuses elsewhere.
        score = band(room_pct, ((0.5, 10), (1.5, 35), (3.0, 60), (6.0, 85), (99, 100)))
        blocked = False
        detail = (f"{room_pct:.1f}% to {nearest['price']:.2f} "
                  f"({', '.join(nearest['sources'][:3])}) — expected move unavailable")

    return {"score": float(score), "detail": detail, "blocked": bool(blocked),
            "nearest": nearest["price"], "nearest_pct": room_pct,
            "nearest_sources": nearest["sources"],
            "room_ratio": room_ratio, "expected_move_pct": expected_move_pct,
            "levels": clustered[:8], "targets": significant[:6]}
