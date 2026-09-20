"""
score.py — Trend Quality, 0-100
================================
Eight components, weighted as section 10 specifies. Every component scores
0-1 and is multiplied by its weight, so the weights are readable as "how
much of the verdict does this fact own" and can be re-argued one line at a
time.

    long-term regime  25    which market this name is in
    MA alignment      20    is the stack ordered
    MA slope          15    are the averages going the right way
    price structure   15    is price itself making higher highs
    momentum          10    the short-term state
    ADX                5    is there a trend at all, or just drift
    volume / RVOL      5    is anyone behind it
    extension          5    room left, or already run

The spec writes extension as "-5%". It is implemented here as a 5-point
component you hold when price is near its trend and lose as it stretches —
arithmetically identical to a penalty, but it keeps the range an honest
0-100 instead of a 95 ceiling with a floating deduction.

Missing data is never scored as zero
------------------------------------
A component with no inputs is dropped and the remaining weights are
renormalised, so a name with 8 months of history is scored on what is
actually known about it rather than being pushed toward zero for the
crime of being young. `coverage` reports how much of the weight was live.
That is the same missing-never-zero rule the retirement and portfolio-risk
engines use, and it exists because a silently-zeroed component and a
genuinely bad one are indistinguishable in the output.

The hard rule
-------------
Section 10: a high score is not allowed while the 200 SMA is strongly
declining. That is enforced as a CAP after the weighted sum, not as
another negative component, because a component can always be outvoted by
seven others and a cap cannot. A name in a genuine long-term downtrend
cannot print 80 here no matter how clean its short-term stack looks.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f, scale
from stockanalysis.core.trend import indicators as I
from stockanalysis.core.trend import regime as R

WEIGHTS = {
    "long_term": 25,
    "alignment": 20,
    "slope": 15,
    "structure": 15,
    "momentum": 10,
    "adx": 5,
    "volume": 5,
    "extension": 5,
}

# Ceiling applied when the 200 SMA is strongly declining, whatever else is
# true. 45 keeps such a name inside the "weak/mixed" band and out of every
# filter that gates on 70+.
DECLINING_200_CAP = 45

BANDS = ((90, "Exceptional trend"), (80, "Strong trend"), (70, "Good trend"),
         (60, "Developing/mixed"), (40, "Weak/mixed"), (0, "Bearish/poor trend"))

_LT_POINTS = {
    R.BULLISH_LT: 1.0,
    R.WEAK_BULL_LT: 0.5,
    R.ACCUMULATION_LT: 0.35,
    R.BEARISH_LT: 0.0,
}

_MOMENTUM_POINTS = {
    "MOMENTUM_ACCELERATION": 1.0,
    "STRONG_MOMENTUM": 0.9,
    "MOMENTUM_COOLING": 0.5,
    "NEUTRAL_MOMENTUM": 0.4,
    "NEGATIVE_MOMENTUM": 0.1,
}

_STRUCTURE_POINTS = {
    I.HH_HL: 1.0,
    I.HH_LL: 0.5,
    I.LH_HL: 0.45,
    I.LH_LL: 0.0,
}


def _slope_component(ind: dict):
    """Fraction of the four decisive averages that are rising, with a flat
    average scoring half. Falling scores nothing.

    Half-credit for flat is deliberate: an average that has stopped falling
    is materially different from one still falling, and a scanner that
    cannot see the difference finds every bottom late.
    """
    states = ind.get("slope_state") or {}
    vals = []
    for key in I.CORE_MAS:
        st = states.get(key, "unknown")
        if st == "unknown":
            continue
        vals.append(1.0 if st == "rising" else 0.5 if st == "flat" else 0.0)
    return (sum(vals) / len(vals)) if vals else None


def _alignment_component(ind: dict):
    """Full bullish order scores 1. Everything else scores by how much of
    the ordering actually holds, so a stack one crossover away from clean
    does not read the same as a fully scrambled one."""
    if ind.get("alignment") == "FULL_BULLISH_ALIGNMENT":
        return 1.0
    if ind.get("alignment") == "FULL_BEARISH_ALIGNMENT":
        return 0.0
    pct = f(ind.get("alignment_pct"))
    return None if pct is None else round(pct / 100, 3)


def _volume_component(ind: dict):
    """RVOL around 1 is normal and scores the middle. This is not a
    higher-is-better axis past a point — RVOL 4 is an event, not a quality."""
    rvol = f(ind.get("rvol"))
    if rvol is None:
        return None
    return round(scale(min(rvol, 2.5), 0.5, 1.6) / 100, 3)


def _extension_component(ind: dict):
    """Full credit while price is at or below its 21 EMA, decaying to zero
    as it stretches. Measured in ATR — the whole reason this component can
    be compared across names at all."""
    mult = (ind.get("dist_atr") or {}).get("EMA21")
    if mult is None:
        return None
    if mult <= 0:
        return 1.0
    span = I.PARABOLIC_ATR["EMA21"]
    return round(max(0.0, 1 - mult / span), 3)


def compute(ind: dict, verdict: dict) -> dict:
    """Weighted 0-100 with the declining-200 cap applied. Never raises."""
    if not ind:
        return {"score": None, "band": "Unknown", "coverage": 0,
                "components": {}, "capped": False}

    adx = f(ind.get("adx"))
    components = {
        "long_term": _LT_POINTS.get(verdict["long_term"]["regime"]),
        "alignment": _alignment_component(ind),
        "slope": _slope_component(ind),
        "structure": _STRUCTURE_POINTS.get(ind.get("structure")),
        "momentum": _MOMENTUM_POINTS.get(verdict["momentum"]["state"]),
        "adx": None if adx is None else round(scale(adx, 12, 35) / 100, 3),
        "volume": _volume_component(ind),
        "extension": _extension_component(ind),
    }

    live = {k: v for k, v in components.items() if v is not None}
    weight_live = sum(WEIGHTS[k] for k in live)
    if not weight_live:
        return {"score": None, "band": "Unknown", "coverage": 0,
                "components": components, "capped": False}

    raw = sum(WEIGHTS[k] * v for k, v in live.items()) / weight_live * 100
    score = round(raw)

    capped = False
    s200 = (ind.get("slope") or {}).get("SMA200_5d_atr5")
    if s200 is not None and s200 <= I.STRONG_DECLINE_ATR5 and score > DECLINING_200_CAP:
        score, capped = DECLINING_200_CAP, True

    band = next(name for floor, name in BANDS if score >= floor)
    return {"score": score, "band": band,
            "coverage": round(weight_live / sum(WEIGHTS.values()) * 100),
            "components": components, "capped": capped}
