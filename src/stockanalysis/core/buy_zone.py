"""
buy_zone.py
===========
Buy Zone Score — "is NOW a good price to accumulate this name?", distinct
from Investment_Score in strategy_scores.py ("is this a name worth owning
6-24 months?"). A stock can pass every Investment filter while trading at a
terrible entry (chasing at the 52W high, PEG of 4) — this score answers the
entry-price question on its own axis instead of folding it into one blended
number.

Institutional buyers scale into pullbacks on strong companies rather than
chasing strength, so the score rewards quality names sitting in a moderate
(5-15%) pullback with light selling volume, not the strongest tape action.

Eight weighted factors, each 0-100 internally, blended by weight:

    Factor                  Weight   Source
    ---------------------- -------  ---------------------------------
    Fundamental Quality       30%   core.company_scores business quality
    Technical Trend           20%   Above_200MA, Price_vs_50MA%, RS_Rank, ADX_14
    Valuation                 15%   PEG_Ratio (fallback Forward_PE)
    Pullback Zone             10%   Dist_52W_High%
    Volume Accumulation       10%   Pullback_Vol_Ratio, VolumeDryingUp
    Institutional Buying       5%   Inst_Own%, Inst_Own_Chg
    Relative Strength          5%   RS_Rank
    Earnings / Catalysts       5%   EarningsBeat

Missing factors drop out of the blend entirely rather than scoring zero —
the remaining factors are renormalized against the weight actually covered,
so a name missing PEG isn't punished as if it were expensive. Needs at least
half the total weight covered (some fundamentals + some technicals) or the
score is None rather than a low-confidence guess.

Usage
-----
    from stockanalysis.core.buy_zone import compute_buy_zone

    result = compute_buy_zone(row)   # row: scan/research dict
    # {"score": int|None, "label": str|None, "drivers": [str, ...],
    #  "weight_covered": int}
"""

from __future__ import annotations

from stockanalysis.core.company_scores import compute_business_quality

MIN_WEIGHT_COVERED = 50   # of 100 — need at least half the factors present

ZONE_LABELS = ((90, "Strong Buy Zone"), (80, "Buy Zone"),
               (70, "Watch List"), (60, "Hold / Monitor"), (0, "Avoid"))


def _zone_label(score: int) -> str:
    for floor, name in ZONE_LABELS:
        if score >= floor:
            return name
    return "Avoid"


def _fundamental_score(row: dict) -> tuple[int | None, str]:
    bq = compute_business_quality(row)
    if bq["score"] is None:
        return None, "insufficient fundamental data"
    return bq["score"], f"business quality {bq['label']} ({bq['score']})"


def _technical_score(row: dict) -> tuple[int | None, str]:
    above200 = row.get("Above_200MA")
    p50      = row.get("Price_vs_50MA%")
    rs_rank  = row.get("RS_Rank")
    adx      = row.get("ADX_14")

    pts = possible = 0
    parts = []
    if above200 is not None:
        possible += 40
        if above200: pts += 40
        parts.append("above 200MA" if above200 else "below 200MA")
    if p50 is not None:
        possible += 20
        if p50 > 0: pts += 20
        parts.append(f"{'above' if p50 > 0 else 'below'} 50MA ({p50:+.1f}%)")
    if rs_rank is not None:
        possible += 20
        if rs_rank >= 70:   pts += 20
        elif rs_rank >= 50: pts += 10
        parts.append(f"RS rank {rs_rank:.0f}")
    if adx is not None:
        possible += 20
        if adx >= 20: pts += 20
        parts.append(f"ADX {adx:.0f}")

    if possible == 0:
        return None, "insufficient technical data"
    return round(pts / possible * 100), "; ".join(parts)


def _valuation_score(row: dict) -> tuple[int | None, str]:
    peg = row.get("PEG_Ratio")
    fpe = row.get("Forward_PE")

    if peg is not None and peg > 0:
        if peg < 1.0:   score = 100
        elif peg < 1.5: score = 80
        elif peg < 2.0: score = 55
        elif peg < 3.0: score = 30
        else:           score = 10
        return score, f"PEG {peg:.2f}"

    if fpe is not None and fpe > 0:
        if fpe < 15:    score = 90
        elif fpe < 25:  score = 70
        elif fpe < 35:  score = 45
        elif fpe < 50:  score = 25
        else:           score = 10
        return score, f"Fwd P/E {fpe:.1f} (no PEG)"

    return None, "no valuation data"


def _pullback_score(row: dict) -> tuple[int | None, str]:
    dist = row.get("Dist_52W_High%")
    if dist is None:
        return None, "no 52W high data"

    if dist >= -5:
        score = 70    # near/at highs — buyable but not a discount
    elif dist >= -15:
        score = 100   # institutional accumulation sweet spot
    elif dist >= -25:
        score = 75
    elif dist >= -35:
        score = 45
    else:
        score = 20    # deep drawdown — verify thesis intact
    return score, f"{dist:+.1f}% from 52W high"


def _volume_score(row: dict) -> tuple[int | None, str]:
    pvol    = row.get("Pullback_Vol_Ratio")
    vol_dry = row.get("VolumeDryingUp")

    pts = possible = 0
    parts = []
    if pvol is not None:
        possible += 60
        if pvol <= 0.7:   pts += 60
        elif pvol <= 1.0: pts += 35
        parts.append(f"pullback volume {pvol:.2f}x average")
    if vol_dry is not None:
        possible += 40
        if vol_dry: pts += 40
        parts.append("volume drying up" if vol_dry else "volume not drying up")

    if possible == 0:
        return None, "no volume-accumulation data"
    return round(pts / possible * 100), "; ".join(parts)


def _institutional_score(row: dict) -> tuple[int | None, str]:
    inst_own = row.get("Inst_Own%")
    inst_chg = row.get("Inst_Own_Chg")

    pts = possible = 0
    parts = []
    if inst_own is not None:
        possible += 40
        if inst_own >= 40:   pts += 40
        elif inst_own >= 20: pts += 20
        parts.append(f"inst. ownership {inst_own:.0f}%")
    if inst_chg is not None:
        possible += 60
        if inst_chg > 2:   pts += 60
        elif inst_chg > 0: pts += 35
        parts.append(f"inst. ownership change {inst_chg:+.1f}%")

    if possible == 0:
        return None, "no institutional data"
    return round(pts / possible * 100), "; ".join(parts)


def _rs_score(row: dict) -> tuple[int | None, str]:
    rs_rank = row.get("RS_Rank")
    if rs_rank is None:
        return None, "no RS data"
    return min(100, round(rs_rank / 99 * 100)), f"RS rank {rs_rank:.0f}"


def _catalyst_score(row: dict) -> tuple[int | None, str]:
    beat = row.get("EarningsBeat")
    if beat is None:
        return None, "no earnings data"
    return (100 if beat else 30), ("last earnings beat" if beat else "last earnings missed")


FACTORS = (
    ("Fundamental Quality", 30, _fundamental_score),
    ("Technical Trend",     20, _technical_score),
    ("Valuation",           15, _valuation_score),
    ("Pullback Zone",       10, _pullback_score),
    ("Volume Accumulation", 10, _volume_score),
    ("Institutional Buying", 5, _institutional_score),
    ("Relative Strength",    5, _rs_score),
    ("Earnings/Catalysts",   5, _catalyst_score),
)


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
    return {"score": score, "label": _zone_label(score),
            "drivers": drivers, "weight_covered": weight_covered}
