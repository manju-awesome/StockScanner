"""
growth.py — Step 3. Inflection, not history.
============================================
The brief's sharpest instruction: "Reward accelerating growth, not simply
high historical growth." Those rank very differently and the difference is
the whole idea. A company compounding 45% for four years and decelerating
to 30% is a great past and a worsening present; one that went 12% → 19% →
34% has a worse history and is the one that becomes a leader. Screens built
on 3-year CAGR systematically buy the first and miss the second, because
CAGR is an average and an average cannot see a bend.

So this module measures the SECOND derivative and weights it above the
first. `accel_pp` is the whole point of the file: the latest growth rate
minus the rate the company has been compounding at, in percentage points.
Positive means the curve is bending upward.

Like must be compared with like
-------------------------------
Acceleration is always a year-on-year growth rate measured against ANOTHER
year-on-year growth rate. The obvious alternative — comparing the latest
rate to the multi-year CAGR — is broken in a way that is easy to miss and
fatal on this particular universe. A CAGR computed from a small base is
enormous (a company going $20M → $850M in three years compounds at 155%),
so a spot rate is subtracted from a number no company could sustain, and
EVERY early hyper-growth name is stamped "decelerating". That is precisely
the population this engine exists to find, so the bias would invert the
ranking exactly where it matters most.

Three reads, in preference order:

    QUARTERLY   latest quarter's YoY growth against the last fiscal year's
                YoY growth. Both are year-on-year rates, so the comparison
                is sound, and it is the fastest signal available.
    ANNUAL      latest fiscal year's YoY growth against the prior fiscal
                year's YoY growth — a true second difference. The fallback
                whenever Yahoo returns fewer than five quarters.
    FORWARD     next year's consensus revenue growth against this year's.
                Reported ALONGSIDE, never blended into the measured score —
                consensus is an opinion, and letting it move the score
                would mean ranking companies by how excited analysts are.

Fade from a very high base is expected, not a warning
-----------------------------------------------------
A company growing 240% will not keep growing 240%; arithmetic alone brings
it down as the base compounds. So when the PRIOR rate was extreme, the
deceleration is annotated as expected fade rather than presented as
deterioration. It still scores what it scores — the brief asks for
acceleration to be rewarded and a fading company is not accelerating — but
the reader is told which kind of deceleration they are looking at, because
"240% → 105%" and "14% → 4%" are the same sign and completely different
findings.

Where the inputs for none of the three exist, `accel_pp` is None and the
composite renormalises. It is never assumed flat: "we could not tell
whether this is accelerating" and "this is not accelerating" are different
statements about a company, and only one of them is a reason to pass.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import blend, scale

# Sequential quarters needed before a year-on-year quarterly compare is
# possible at all — Q0 against Q-4 needs five points on the series.
MIN_Q_FOR_YOY = 5

# What counts as an inflection, in percentage points of acceleration. Below
# +2pp is noise: revenue recognition timing alone moves a quarter by more.
ACCEL_NOISE_PP = 2.0

# The growth rate the level leg saturates at. Past ~60% the marginal
# information is small — a 90% grower and a 60% grower are both "growing as
# fast as a company can operationally sustain", and the difference between
# them is usually a comparison base rather than a difference in business.
GROWTH_LEVEL_CAP = 60.0

# Acceleration scale, in percentage points of year-on-year growth. +20pp is
# an extraordinary bend (12% -> 32%); -25pp floors the leg. The negative end
# is deliberately wider than the positive one because on this population
# deceleration is the common case and its magnitudes are larger — a symmetric
# scale would put half the universe at zero and stop discriminating.
ACCEL_LO, ACCEL_HI = -25.0, 20.0

# A prior-year growth rate above this is treated as unsustainable by
# arithmetic, so the fade from it is annotated as expected rather than as
# deterioration. It does not change the score — a fading company is still
# not an accelerating one — it changes what the reader is told.
FADE_BASE_PCT = 60.0

# Step 3 asks for backlog, customer and organic-growth reads. None of the
# three has a source in this data — backlog is a filing narrative, customer
# counts are an investor-deck disclosure, and separating organic from
# acquired growth needs a segment note. Named so the page can print what
# was never checked rather than implying the growth read is complete.
UNMEASURABLE = ("backlog growth", "customer growth",
                "organic vs acquired split")


def _cagr(values, years) -> float | None:
    """Compound annual growth, oldest to newest, in percent.

    None rather than a number wherever the arithmetic would lie: too few
    points, or a series crossing zero. A company going from -$4M to $60M of
    revenue has not grown at a rate, and reporting one would put an
    accounting artifact at the top of a growth ranking.
    """
    clean = [v for v in values if v is not None]
    if len(clean) < years + 1:
        return None
    newest, oldest = clean[0], clean[years]
    if oldest is None or oldest <= 0 or newest <= 0:
        return None
    return round(((newest / oldest) ** (1.0 / years) - 1.0) * 100.0, 1)


def _yoy(newer, older) -> float | None:
    if newer is None or older is None or older <= 0:
        return None
    return round((newer / older - 1.0) * 100.0, 1)


def compute(data: dict) -> dict:
    """Every growth reading for one company, plus the acceleration verdict."""
    rev = data.get("revenue_annual") or []
    qrev = data.get("revenue_quarterly") or []

    cagr_3y = _cagr(rev, 3)
    cagr_2y = _cagr(rev, 2)
    annual_yoy = _yoy(rev[0] if rev else None, rev[1] if len(rev) > 1 else None)

    # TTM from the quarterly series where four clean quarters exist. Reported
    # for scale, not growth — the year-ago TTM would need eight quarters and
    # Yahoo returns five, so a TTM growth rate is not honestly computable
    # here and is not invented.
    ttm = None
    if len([v for v in qrev[:4] if v is not None]) == 4:
        ttm = sum(qrev[:4])

    q_yoy = None
    if len(qrev) >= MIN_Q_FOR_YOY:
        q_yoy = _yoy(qrev[0], qrev[MIN_Q_FOR_YOY - 1])

    # Sequential growth, newest first. Seasonal and therefore never scored —
    # it is shown because a reader looking at a single year-on-year number
    # deserves to see whether the path to it was steady or a single spike.
    sequential = []
    for i in range(min(4, len(qrev) - 1)):
        sequential.append(_yoy(qrev[i], qrev[i + 1]))

    fwd_cy = data.get("fwd_rev_growth_cy")
    fwd_ny = data.get("fwd_rev_growth_ny")
    fwd_accel = (None if fwd_cy is None or fwd_ny is None
                 else round(fwd_ny - fwd_cy, 1))

    # ── The acceleration verdict ─────────────────────────────────────────
    # Always a YoY rate against another YoY rate. See the module docstring
    # for why the multi-year CAGR is never the comparison.
    prior_annual_yoy = _yoy(rev[1] if len(rev) > 1 else None,
                            rev[2] if len(rev) > 2 else None)

    accel_pp, basis, latest, against = None, None, None, None
    if q_yoy is not None and annual_yoy is not None:
        accel_pp, basis, latest, against = (round(q_yoy - annual_yoy, 1),
                                            "quarterly", q_yoy, annual_yoy)
    elif annual_yoy is not None and prior_annual_yoy is not None:
        accel_pp, basis, latest, against = (
            round(annual_yoy - prior_annual_yoy, 1), "annual", annual_yoy,
            prior_annual_yoy)
    elif q_yoy is not None:
        latest = q_yoy
    elif annual_yoy is not None:
        latest = annual_yoy

    # Deceleration off an arithmetically unsustainable base is its own
    # finding — see FADE_BASE_PCT.
    expected_fade = bool(accel_pp is not None and accel_pp < 0
                         and against is not None and against > FADE_BASE_PCT
                         and latest is not None and latest > 25.0)

    if accel_pp is None:
        state = "UNMEASURED"
    elif accel_pp >= ACCEL_NOISE_PP:
        state = "ACCELERATING"
    elif accel_pp <= -ACCEL_NOISE_PP:
        state = "FADING FROM A HIGH BASE" if expected_fade else "DECELERATING"
    else:
        state = "STEADY"

    # ── The score ────────────────────────────────────────────────────────
    # Acceleration outweighs level, which is the instruction. A 25%-growing
    # company bending upward outranks a 45%-growing one bending down, and
    # that inversion is the behaviour the brief asked for.
    level = None
    for candidate in (latest, cagr_2y, cagr_3y):
        if candidate is not None:
            level = scale(candidate, 0.0, GROWTH_LEVEL_CAP)
            break

    accel_score = (None if accel_pp is None
                   else scale(accel_pp, ACCEL_LO, ACCEL_HI))

    # Consistency: how many of the available annual periods grew at all. A
    # company with one explosive year inside three flat ones is a different
    # animal from one that has grown every year, and the CAGR hides it.
    yoys = [_yoy(rev[i], rev[i + 1]) for i in range(len(rev) - 1)]
    yoys = [y for y in yoys if y is not None]
    consistency = (None if not yoys
                   else sum(1 for y in yoys if y > 0) / len(yoys) * 100.0)

    scored = blend([
        ("Acceleration", 45, accel_score,
         f"{accel_pp:+.1f}pp on a {basis} basis" if accel_pp is not None
         else "not measurable"),
        ("Growth level", 35, level,
         f"{latest:.0f}% latest" if latest is not None else "no growth rate"),
        ("Consistency", 20, consistency,
         f"{sum(1 for y in yoys if y > 0)}/{len(yoys)} years grew"
         if yoys else "no annual history"),
    ])

    return {
        "score": scored["score"],
        "coverage": scored["coverage"],
        "components": scored["components"],
        "cagr_3y": cagr_3y,
        "cagr_2y": cagr_2y,
        "annual_yoy": annual_yoy,
        "quarter_yoy": q_yoy,
        "latest": latest,
        "ttm_revenue": ttm,
        "sequential": sequential,
        "accel_pp": accel_pp,
        "accel_basis": basis,
        "accel_against": against,
        "prior_annual_yoy": prior_annual_yoy,
        "expected_fade": expected_fade,
        "state": state,
        "consistency_pct": None if consistency is None else round(consistency),
        # Forward consensus, carried but never scored. See the docstring.
        "fwd_growth_cy": fwd_cy,
        "fwd_growth_ny": fwd_ny,
        "fwd_accel_pp": fwd_accel,
        "fwd_analysts": data.get("fwd_estimate_analysts"),
        "unmeasured": list(UNMEASURABLE),
        "detail": _detail(state, accel_pp, basis, latest, against,
                          expected_fade),
    }


def _detail(state, accel_pp, basis, latest, against, expected_fade) -> str:
    if state == "UNMEASURED":
        if latest is not None:
            return (f"{latest:.0f}% latest, but there is no second growth "
                    f"rate to compare it against — acceleration needs three "
                    f"fiscal years or five quarters")
        return ("No growth rate could be measured from the available "
                "statements")

    period = ("the last fiscal year" if basis == "quarterly"
              else "the year before")
    lead = f"{latest:.0f}% latest" if latest is not None else "growth"

    if state == "ACCELERATING":
        return (f"{lead}, up {accel_pp:+.1f}pp from the {against:.0f}% it "
                f"grew in {period} — the curve is bending upward")
    if state == "FADING FROM A HIGH BASE":
        return (f"{lead}, down {abs(accel_pp):.1f}pp from {against:.0f}% in "
                f"{period}. Fade from a base that high is arithmetic, not "
                f"deterioration — but it is still not acceleration")
    if state == "DECELERATING":
        return (f"{lead}, down {abs(accel_pp):.1f}pp from the {against:.0f}% "
                f"of {period} — the curve is bending downward")
    return (f"{lead}, holding within {abs(accel_pp):.1f}pp of the "
            f"{against:.0f}% of {period}")
