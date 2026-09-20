"""
leverage.py — Step 4. Does scale make this business better?
===========================================================
Operating leverage is the single most reliable early tell that a company is
becoming a leader rather than merely getting bigger, because it cannot be
bought. Revenue growth can be acquired, gross margin can be mix, but revenue
growing faster than the cost of producing it, sustained, means the model
itself improves with scale — and that is what turns a fast grower into a
compounder.

The measurement, stated plainly:

    LEVERAGE RATIO = revenue growth ÷ operating-expense growth

Above 1.0, the company is out-growing its own cost base. Below 1.0 it is
buying its growth, and the further below, the more expensive each dollar of
revenue is becoming. This one ratio does more work than the three margin
trends combined, because margins can improve for a year on a one-off and
the ratio cannot.

Margin TREND, never margin LEVEL
--------------------------------
A 28% gross margin says nothing on its own — it is elite for a distributor
and catastrophic for software. What travels across industries is the
direction: a gross margin up 6pp over three years means pricing power or
mix improvement wherever it happens. So every margin here is scored on its
change in percentage points, and the level is reported beside it for
context without entering the score.

The FCF inflection
------------------
Step 4 asks for "FCF inflection" specifically, which is not the same as FCF
being positive. The engine is explicitly forbidden from rejecting companies
for negative free cash flow, so what matters is the DIRECTION and how close
the crossing is. A company at -$40M improving from -$120M is inflecting; one
at +$5M down from +$60M is deteriorating, and a level test scores them
backwards.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import blend, scale

# Percentage-point change over the available history that maps to a full
# score. +8pp of gross margin in three years is a structural change in the
# business; -6pp is a business losing its pricing.
GM_LO, GM_HI = -6.0, 8.0
OM_LO, OM_HI = -10.0, 15.0
FCF_LO, FCF_HI = -12.0, 15.0

# Leverage ratio scale. 1.0 is the break-even — growing revenue and costs in
# step. 2.0 means costs grew half as fast as revenue, which is the profile
# of a business whose unit economics improve as it scales.
RATIO_LO, RATIO_HI = 0.5, 2.0

MIN_MARGIN_YEARS = 2

# Step 4 asks for an EBITDA margin trend alongside the other three. It is
# deliberately absent rather than approximated: the annual statements here
# carry operating income but not a reliable depreciation-and-amortisation
# line for every filer, and "operating margin plus a D&A guess" is a
# different number wearing EBITDA's name. Operating margin is reported
# instead and is the stricter of the two — it is the one that says whether
# the business earns money after the cost of the assets it uses.
UNMEASURABLE = ("EBITDA margin trend",)


def _margins(numerator, revenue) -> list:
    """Per-year margin percentages, newest first, None where either side is
    missing or revenue is non-positive."""
    out = []
    for i, rev in enumerate(revenue):
        num = numerator[i] if i < len(numerator) else None
        out.append(None if (num is None or rev is None or rev <= 0)
                   else round(num / rev * 100, 1))
    return out


def _trend_pp(margins) -> float | None:
    """Newest minus oldest, in percentage points — the direction."""
    clean = [m for m in margins if m is not None]
    if len(clean) < MIN_MARGIN_YEARS:
        return None
    return round(clean[0] - clean[-1], 1)


def _growth(series) -> float | None:
    """Total growth from oldest to newest available point, in percent."""
    clean = [v for v in series if v is not None]
    if len(clean) < 2:
        return None
    newest, oldest = clean[0], clean[-1]
    if oldest is None or oldest <= 0:
        return None
    return round((newest / oldest - 1.0) * 100.0, 1)


def compute(data: dict) -> dict:
    rev = data.get("revenue_annual") or []
    gross = data.get("gross_annual") or []
    op = data.get("operating_annual") or []
    opex = data.get("opex_annual") or []
    fcf = data.get("fcf_annual") or []

    gm = _margins(gross, rev)
    om = _margins(op, rev)
    fm = _margins(fcf, rev)

    gm_trend = _trend_pp(gm)
    om_trend = _trend_pp(om)
    fm_trend = _trend_pp(fm)

    # ── The leverage ratio ───────────────────────────────────────────────
    rev_growth = _growth(rev)
    opex_growth = _growth(opex)
    ratio = None
    if rev_growth is not None and opex_growth is not None:
        if opex_growth <= 0 < rev_growth:
            # Revenue up while the cost base SHRANK. A ratio is undefined
            # (division by a negative denominator flips the sign and would
            # score this as the worst case), so it is capped at the top of
            # the scale, which is what it means.
            ratio = RATIO_HI
        elif opex_growth > 0:
            ratio = round((1 + rev_growth / 100) / (1 + opex_growth / 100), 2)

    ratio_score = None if ratio is None else scale(ratio, RATIO_LO, RATIO_HI)

    # ── FCF inflection ───────────────────────────────────────────────────
    clean_fcf = [v for v in fcf if v is not None]
    fcf_now = clean_fcf[0] if clean_fcf else None
    fcf_state, fcf_note = _fcf_state(clean_fcf, fm_trend)

    scored = blend([
        ("Operating leverage", 35, ratio_score,
         f"revenue/opex growth {ratio:.2f}x" if ratio is not None
         else "opex history unavailable"),
        ("Gross margin trend", 25,
         None if gm_trend is None else scale(gm_trend, GM_LO, GM_HI),
         f"{gm_trend:+.1f}pp" if gm_trend is not None else "not measured"),
        ("Operating margin trend", 25,
         None if om_trend is None else scale(om_trend, OM_LO, OM_HI),
         f"{om_trend:+.1f}pp" if om_trend is not None else "not measured"),
        ("FCF margin trend", 15,
         None if fm_trend is None else scale(fm_trend, FCF_LO, FCF_HI),
         f"{fm_trend:+.1f}pp — {fcf_state.lower()}" if fm_trend is not None
         else "not measured"),
    ])

    return {
        "score": scored["score"],
        "coverage": scored["coverage"],
        "components": scored["components"],
        "gross_margin_now": next((m for m in gm if m is not None), None),
        "gross_margin_history": gm,
        "gross_margin_trend_pp": gm_trend,
        "operating_margin_now": next((m for m in om if m is not None), None),
        "operating_margin_history": om,
        "operating_margin_trend_pp": om_trend,
        "fcf_margin_now": next((m for m in fm if m is not None), None),
        "fcf_margin_trend_pp": fm_trend,
        "fcf_now": fcf_now,
        "fcf_history": fcf,
        "fcf_state": fcf_state,
        "fcf_note": fcf_note,
        "revenue_growth_total_pct": rev_growth,
        "opex_growth_total_pct": opex_growth,
        "leverage_ratio": ratio,
        "unmeasured": list(UNMEASURABLE),
        "detail": _detail(ratio, gm_trend, om_trend, fcf_state),
    }


def _fcf_state(clean_fcf, fm_trend) -> tuple[str, str]:
    """Where the company is on the free-cash-flow curve.

    Deliberately five states rather than positive/negative. "Burning less
    every year" and "burning more every year" are the two most important
    distinctions this engine can draw about an unprofitable company, and a
    sign test collapses them into one bucket.
    """
    if not clean_fcf:
        return "UNMEASURED", "no cash-flow history"
    now = clean_fcf[0]
    if len(clean_fcf) < 2:
        return ("POSITIVE" if now > 0 else "BURNING",
                "single year of cash-flow data — no direction yet")
    oldest = clean_fcf[-1]
    if now > 0 and oldest <= 0:
        return "INFLECTED", ("crossed into positive free cash flow within "
                             "the available history")
    if now > 0:
        return ("COMPOUNDING" if now > oldest else "POSITIVE, SOFTENING",
                f"positive and {'growing' if now > oldest else 'declining'}")
    if now > oldest:
        return "IMPROVING", ("still negative, but the burn is shrinking — "
                             "the crossing is ahead, not behind")
    return "WIDENING", "negative and deteriorating — the burn is growing"


def _detail(ratio, gm_trend, om_trend, fcf_state) -> str:
    bits = []
    if ratio is not None:
        bits.append(f"revenue grew {ratio:.2f}x as fast as operating costs"
                    if ratio >= 1 else
                    f"costs outgrew revenue ({ratio:.2f}x) — growth is being "
                    f"bought")
    if gm_trend is not None:
        bits.append(f"gross margin {gm_trend:+.1f}pp")
    if om_trend is not None:
        bits.append(f"operating margin {om_trend:+.1f}pp")
    if fcf_state not in ("UNMEASURED",):
        bits.append(f"FCF {fcf_state.lower()}")
    return "; ".join(bits) or "no margin or cost history available"
