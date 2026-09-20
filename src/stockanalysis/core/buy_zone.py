"""
buy_zone.py
===========
Buy Zone Score — "is NOW a good price to enter this name?"

Purely technical. This used to blend 70% fundamentals (business quality 30%,
valuation 15%, institutional 5%, earnings 5%) with 30% price action, which
meant the score mostly restated how good the *company* was. NVDA scored 81 —
"Buy Zone" — at $222 while sitting +8.1% above its 50 MA, +4.8% above its 8
EMA, RSI 64 and RS rank 37: a superb business at a poor entry. The label said
buy; the price action said chase.

Whether a company is worth owning is already answered elsewhere —
strategy_scores.Investment_Score, company_scores, and the decision engine's
investment score. Folding it in here a second time made this score unable to
disagree with them, which is the one thing an entry score has to be able to
do.

Six factors, all price and volume, weighted:

    Factor              Weight   Reads
    ------------------ -------  --------------------------------------
    Entry proximity       25%   Pct_vs_8EMA — how close to a real entry
    Pullback zone         20%   Dist_52W_High% — 5-15% back is ideal
    Trend intact          20%   Above_200MA, Price_vs_50MA%
    Volume accumulation   15%   Pullback_Vol_Ratio, VolumeDryingUp
    Relative strength     10%   RS_Rank
    Not overbought        10%   RSI_14

Institutional buyers scale into pullbacks rather than chasing strength, so
the shape of the scoring is unchanged: a moderate pullback on drying volume
in an intact uptrend scores best, and the strongest tape action does not.

The extension cap is the part that matters most. Price meaningfully above
its short-term averages can no longer be labelled a Buy Zone no matter how
the other factors score, because "extended" and "good entry" cannot both be
true. Without it, a deep enough pullback score plus strong volume could
still carry an extended name over the line.

Missing factors drop out of the blend and the rest are renormalized against
the weight actually covered, so a name missing RSI isn't punished as though
it were overbought. Below MIN_WEIGHT_COVERED the score is None rather than a
low-confidence guess.

Usage
-----
    from stockanalysis.core.buy_zone import compute_buy_zone

    result = compute_buy_zone(row)   # row: scan/research dict
    # {"score": int|None, "label": str|None, "drivers": [str, ...],
    #  "weight_covered": int}
"""

from __future__ import annotations

MIN_WEIGHT_COVERED = 50   # of 100 — need at least half the factors present

# Calibrated to this score's own distribution, not inherited from the
# fundamental-weighted version it replaces — a different instrument needs
# different bands. On the live library (n=545, median 70) these put roughly
# 3% in Strong Buy Zone and 8% in Buy Zone. The old 80 cutoff, carried over
# unchanged, labelled 30% of the library a Buy Zone, and a label a third of
# the market qualifies for is not telling you anything.
ZONE_LABELS = ((92, "Strong Buy Zone"), (87, "Buy Zone"),
               (78, "Watch List"), (65, "Hold / Monitor"), (0, "Avoid"))

# Above either of these, the price is extended and cannot be a Buy Zone.
MAX_PCT_ABOVE_8EMA = 4.0
MAX_PCT_ABOVE_50MA = 8.0
EXTENDED_CAP = 77         # below the Watch List floor — an extended
                          # price can never be labelled a Buy Zone


def _zone_label(score: int) -> str:
    for floor, name in ZONE_LABELS:
        if score >= floor:
            return name
    return "Avoid"


def _f(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _b(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    return True if s in ("true", "yes", "1") else False if s in ("false", "no", "0") else None


def _band(value, points) -> int:
    """points: ascending [(upper_bound, score), ...]; last entry is the tail."""
    for bound, score in points:
        if value <= bound:
            return score
    return points[-1][1]


# ── factors ──────────────────────────────────────────────────────────────────

def _entry_proximity(row: dict) -> tuple[int | None, str]:
    """Distance from the 8 EMA — the level most of these setups enter on.
    Signed distance is used: below the EMA is a pullback, above it is a
    chase, and they are not equivalent."""
    pct = _f(row.get("Pct_vs_8EMA"))
    if pct is None:
        return None, ""
    if pct <= 0:                       # at or under the EMA — the good side
        score = _band(abs(pct), [(2, 100), (4, 90), (7, 70), (12, 40), (99, 20)])
        where = f"{abs(pct):.1f}% below 8EMA"
    else:
        score = _band(pct, [(1, 90), (2, 75), (4, 50), (6, 30), (99, 10)])
        where = f"{pct:.1f}% above 8EMA"
    return score, where


def _pullback_zone(row: dict) -> tuple[int | None, str]:
    """A 5-15% pullback from the 52-week high is the sweet spot. Right at
    the high is chasing; far below it, the trend is broken rather than
    resting."""
    dist = _f(row.get("Dist_52W_High%"))
    if dist is None:
        return None, ""
    depth = abs(dist)
    score = _band(depth, [(2, 45), (4, 70), (15, 100), (22, 75), (32, 45), (99, 20)])
    return score, f"{depth:.1f}% off 52W high"


def _trend_intact(row: dict) -> tuple[int | None, str]:
    """Above the 200-day is the floor. Distance above the 50-day is scored
    as a band, not a bonus: comfortably above is healthy, far above is
    stretched."""
    above200 = _b(row.get("Above_200MA"))
    vs50 = _f(row.get("Price_vs_50MA%"))
    if above200 is None and vs50 is None:
        return None, ""
    parts, score, n = [], 0, 0
    if above200 is not None:
        score += 100 if above200 else 15
        n += 1
        parts.append("above 200MA" if above200 else "below 200MA")
    if vs50 is not None:
        if vs50 < -10:
            sub = 35
        elif vs50 < 0:
            sub = 80
        elif vs50 <= 5:
            sub = 100
        elif vs50 <= 10:
            sub = 55
        else:
            sub = 20
        score += sub
        n += 1
        parts.append(f"{vs50:+.1f}% vs 50MA")
    return round(score / n), "; ".join(parts)


def _volume_accumulation(row: dict) -> tuple[int | None, str]:
    """Light volume on the pullback is accumulation; heavy volume is
    distribution."""
    ratio = _f(row.get("Pullback_Vol_Ratio"))
    drying = _b(row.get("VolumeDryingUp"))
    if ratio is None and drying is None:
        return None, ""
    parts, score, n = [], 0, 0
    if ratio is not None:
        score += _band(ratio, [(0.7, 100), (0.9, 85), (1.1, 60), (1.4, 35), (99, 15)])
        n += 1
        parts.append(f"pullback volume {ratio:.2f}x average")
    if drying is not None:
        score += 100 if drying else 45
        n += 1
        parts.append("volume drying up" if drying else "volume not drying up")
    return round(score / n), "; ".join(parts)


def _relative_strength(row: dict) -> tuple[int | None, str]:
    rs = _f(row.get("RS_Rank"))
    if rs is None:
        return None, ""
    return round(max(0.0, min(100.0, rs))), f"RS rank {rs:.0f}"


def _not_overbought(row: dict) -> tuple[int | None, str]:
    """RSI in the 40-60 band is a rest inside a trend. Above 70 is the
    condition this score exists to catch."""
    rsi = _f(row.get("RSI_14"))
    if rsi is None:
        return None, ""
    if rsi < 30:
        score = 60          # oversold can be a bounce or a knife
    elif rsi <= 60:
        score = 100
    elif rsi <= 68:
        score = 60
    elif rsi <= 75:
        score = 30
    else:
        score = 10
    return score, f"RSI {rsi:.1f}"


FACTORS = (
    ("Entry proximity", 25, _entry_proximity),
    ("Pullback zone", 20, _pullback_zone),
    ("Trend intact", 20, _trend_intact),
    ("Volume accumulation", 15, _volume_accumulation),
    ("Relative strength", 10, _relative_strength),
    ("Not overbought", 10, _not_overbought),
)


def is_extended(row: dict) -> tuple[bool, str]:
    """Price too far above its short-term averages to call an entry."""
    vs8 = _f(row.get("Pct_vs_8EMA"))
    vs50 = _f(row.get("Price_vs_50MA%"))
    if vs8 is not None and vs8 > MAX_PCT_ABOVE_8EMA:
        return True, f"{vs8:.1f}% above 8EMA"
    if vs50 is not None and vs50 > MAX_PCT_ABOVE_50MA:
        return True, f"{vs50:.1f}% above 50MA"
    return False, ""


def compute_buy_zone(row: dict) -> dict:
    """
    row: a scan/research row. Returns {"score": int|None, "label": str|None,
    "drivers": [str, ...], "weight_covered": int} where weight_covered is
    out of 100 (the sum of factor weights that had data).
    """
    weighted_sum = 0.0
    weight_covered = 0
    drivers = []
    for name, weight, fn in FACTORS:
        sub, detail = fn(row)
        if sub is None:
            continue
        weighted_sum += sub * weight
        weight_covered += weight
        drivers.append(f"{name} {sub}/100 ({weight}% wt): {detail}")

    if weight_covered < MIN_WEIGHT_COVERED:
        return {"score": None, "label": None,
                "drivers": drivers + [f"only {weight_covered}% factor weight available"],
                "weight_covered": weight_covered}

    score = round(weighted_sum / weight_covered)

    # Extension cap, applied last so it overrides every other factor.
    # "Extended" and "good entry" cannot both be true, and without this a
    # strong pullback and volume reading could still carry a chase over the
    # line — which is exactly how NVDA read "Buy Zone" at +8% over its 50MA.
    extended, why = is_extended(row)
    if extended and score > EXTENDED_CAP:
        drivers.append(f"capped at {EXTENDED_CAP}: extended — {why}")
        score = EXTENDED_CAP

    return {"score": score, "label": _zone_label(score),
            "drivers": drivers, "weight_covered": weight_covered}
