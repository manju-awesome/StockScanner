"""
discovery.py — Step 10. How far along is the market in noticing?
================================================================
"Do not require strong momentum. A developing company can score well with
early institutional discovery." That sentence inverts the usual reading of
every metric in this module, and the inversion is the point.

On a momentum screen, 6 analysts and 34% institutional ownership is a
weakness. Here it is close to ideal: it means the story is real enough to
have attracted professional money and early enough that the re-rating from
wide coverage has not happened. What this engine is trying to avoid is not
obscurity — it is the OPPOSITE, a name that 34 analysts already cover and
every institution already owns, where being right about the business and
being paid for it have already come apart.

So coverage and ownership are scored as CURVES with a peak in the middle,
not as ladders. The peak sits where a company is discovered enough to be
investable and not so discovered that the opportunity is priced.

    UNDISCOVERED    almost no coverage, low institutional ownership. The
                    highest potential and the widest bid/ask on being wrong.
    EARLY DISCOVERY the sweet spot — money is arriving, the crowd has not.
    DISCOVERED      broad coverage, high ownership, still working.
    CROWDED         everyone owns it and everyone knows why.

Price strength is included but deliberately light. A stock does not have to
be going up for the thesis to be right — the whole framework is about
finding companies BEFORE recognition — so relative strength contributes,
and never gates.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import blend, scale

# Analyst coverage. The peak is the range where enough professionals have
# built a model to make the numbers checkable, and few enough that the
# story is not consensus.
COVERAGE_PEAK = (4.0, 14.0)
COVERAGE_MAX = 40.0

# Institutional ownership, percent. Below ~15% the float is retail and
# volatile; above ~85% there is no marginal buyer left.
INST_PEAK = (25.0, 70.0)

STATES = ("UNDISCOVERED", "EARLY DISCOVERY", "DISCOVERED", "CROWDED")

# Step 10 asks for two things this data cannot answer, and they are named
# rather than proxied by something adjacent.
#
# Institutional ownership arrives as a single current percentage — there is
# no history, so "is institutional ownership RISING" is unanswerable here.
# The coverage-count change is a genuinely different signal (how many
# analysts, not how much stock) and is NOT presented as a substitute.
#
# Estimate REVISIONS likewise: the forward revenue estimate is a level, and
# without a stored history of it there is no way to say whether it was
# revised up. Volume expansion is the nearest real accumulation proxy and is
# scored on its own terms.
UNMEASURABLE = ("institutional ownership trend",
                "estimate revision direction",
                "13F holder-by-holder change")


def _plateau(value, peak_lo, peak_hi, floor, ceiling) -> float | None:
    """A curve that rises to a plateau and falls off after it.

    Straight-line up to `peak_lo`, flat 100 across the plateau, straight
    line down to `ceiling`. Used instead of a monotonic scale wherever more
    is not better past a point — which in this module is everywhere.
    """
    if value is None:
        return None
    if value < peak_lo:
        return max(0.0, min(100.0, (value - floor) / max(peak_lo - floor, 1e-9)
                            * 100.0))
    if value <= peak_hi:
        return 100.0
    return max(0.0, min(100.0, (ceiling - value)
                        / max(ceiling - peak_hi, 1e-9) * 100.0))


def compute(data: dict) -> dict:
    analysts = data.get("analysts")
    analyst_trend = data.get("analyst_trend")
    inst = data.get("inst_own")
    rs3 = data.get("rs_3m")
    rs12 = data.get("rs_12m")
    dist_high = data.get("dist_52w_high")
    vol_exp = data.get("vol_expansion")
    above_200 = data.get("above_200ma")

    coverage_score = _plateau(analysts, *COVERAGE_PEAK, 0.0, COVERAGE_MAX)
    inst_score = _plateau(inst, *INST_PEAK, 0.0, 100.0)

    # Coverage DIRECTION is the discovery signal proper. A name going from
    # 5 to 8 analysts in three months is being discovered right now, which
    # is the moment this engine exists to catch.
    trend_score = (None if analyst_trend is None
                   else scale(analyst_trend, -2.0, 4.0))

    rs_score = None
    rs_basis = "no relative strength reading"
    if rs12 is not None:
        rs_score = scale(rs12, -40.0, 60.0)
        rs_basis = f"{rs12:+.0f}% vs the benchmark over 12 months"
    elif rs3 is not None:
        rs_score = scale(rs3, -25.0, 40.0)
        rs_basis = f"{rs3:+.0f}% vs the benchmark over 3 months"

    # Volume expansion: institutions accumulating leave a footprint in
    # average volume before they show up in a 13F.
    vol_score = (None if vol_exp is None else scale(vol_exp, 0.8, 1.8))

    scored = blend([
        ("Institutional ownership", 28, inst_score,
         f"{inst:.0f}% held by institutions" if inst is not None
         else "not reported"),
        ("Analyst coverage", 24, coverage_score,
         f"{analysts:.0f} analysts covering" if analysts is not None
         else "no coverage data"),
        ("Coverage direction", 18, trend_score,
         f"{analyst_trend:+.0f} analysts in 3 months"
         if analyst_trend is not None else "no coverage history"),
        ("Relative strength", 18, rs_score, rs_basis),
        ("Volume expansion", 12, vol_score,
         f"20-day volume {vol_exp:.2f}x its 90-day average"
         if vol_exp is not None else "no volume history"),
    ])

    state, state_why = _state(analysts, inst, analyst_trend)

    return {
        "score": scored["score"],
        "coverage": scored["coverage"],
        "components": scored["components"],
        "state": state,
        "state_why": state_why,
        "analysts": analysts,
        "analyst_trend": analyst_trend,
        "inst_own_pct": inst,
        "rs_3m": rs3, "rs_12m": rs12,
        "dist_52w_high_pct": dist_high,
        "volume_expansion": vol_exp,
        "above_200ma": above_200,
        "unmeasured": list(UNMEASURABLE),
        "detail": _detail(state, analysts, inst, analyst_trend, rs12),
    }


def _state(analysts, inst, trend) -> tuple[str, str]:
    if analysts is None and inst is None:
        return "UNDISCOVERED", ("neither coverage nor ownership could be "
                                "read — treat as unmeasured, not as proven "
                                "obscurity")
    a = analysts if analysts is not None else 0
    i = inst if inst is not None else 0

    if a >= 20 and i >= 80:
        return "CROWDED", (f"{a:.0f} analysts and {i:.0f}% institutional — "
                           f"the story is consensus and priced as one")
    if a >= 15 or i >= 75:
        return "DISCOVERED", (f"{a:.0f} analysts, {i:.0f}% institutional — "
                              f"well covered; the re-rating from discovery "
                              f"has largely happened")
    if a >= 4 or i >= 25:
        rising = " and coverage is rising" if (trend or 0) > 0 else ""
        return "EARLY DISCOVERY", (f"{a:.0f} analysts, {i:.0f}% institutional"
                                   f"{rising} — professional money has "
                                   f"arrived, the crowd has not")
    return "UNDISCOVERED", (f"{a:.0f} analysts and {i:.0f}% institutional — "
                            f"almost nobody is looking. Highest potential "
                            f"and the least external verification of the "
                            f"numbers")


def _detail(state, analysts, inst, trend, rs12) -> str:
    bits = [state.lower().replace("_", " ")]
    if trend is not None and trend != 0:
        bits.append(f"coverage {trend:+.0f} analysts in 3 months")
    if rs12 is not None:
        bits.append(f"relative strength {rs12:+.0f}%")
    return "; ".join(bits)
