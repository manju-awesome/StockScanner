"""
reinvestment.py — Step 6. Is the spending buying anything?
==========================================================
"Reward productive reinvestment. Penalize spending growth without
corresponding revenue/customer growth." Those two sentences are the entire
module, and they rule out the obvious implementation. Ranking companies by
R&D-to-revenue rewards whoever spends most, which on a small-cap growth list
is usually whoever is furthest from working — a pre-revenue name spending
300% of revenue on R&D would top a naive screen of exactly this population.

So intensity is measured, and then PRODUCTIVITY decides whether the
intensity was a good thing:

    R&D PRODUCTIVITY = revenue growth ÷ R&D growth

Above 1.0, each additional research dollar came with more than a dollar's
worth of additional revenue growth. Below 1.0, the lab is outrunning the
business. The same test is applied to capex, where it means capacity is
filling rather than sitting idle.

The order matters: intensity is a prerequisite, productivity is the score.
A company spending nothing on R&D cannot become a technology leader, so
intensity still carries real weight — but it is capped, because past a
point more R&D is a symptom of difficulty rather than ambition.

Employee growth as the capacity signal
--------------------------------------
Headcount is the one expansion measure available for companies that build
nothing physical. Revenue per employee, and its direction, says whether
hiring is being absorbed. It is a snapshot in this data — Yahoo carries one
employee count, not a history — so the LEVEL is used against the theme's own
peers rather than a trend against itself. That comparison happens in
engine.py, which is the only place that can see the peer set.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import blend, scale

# Intensity is a PLATEAU, not a ladder — and getting this wrong quietly
# defeats the whole module.
#
# On a monotonic scale, a pre-revenue company spending 300% of revenue on
# R&D scores 100 on intensity, and with intensity carrying 45% of this leg
# it can out-rank a disciplined compounder despite research productivity of
# 0.33x. That is precisely the "spending growth without corresponding
# revenue growth" the brief says to PENALISE, arriving at the top of the
# ranking through the front door.
#
# So each band rises to a floor, sits flat across the range where the
# spending is serious and sane, and falls away past the point where
# intensity stops meaning ambition and starts meaning a revenue base too
# small to support the burn.
#
#          (floor, plateau_lo, plateau_hi, zero_above)
RND_BAND = (0.0, 8.0, 25.0, 80.0)
CAPEX_BAND = (0.0, 5.0, 20.0, 60.0)

# Productivity ratios. 1.0 is break-even: growth kept pace with the
# spending that bought it.
PROD_LO, PROD_HI = 0.4, 1.8

UNMEASURABLE = ("new product launches", "new facility announcements",
                "customer acquisition cost", "headcount history")


def rnd_intensity_score(pct) -> float | None:
    """R&D intensity on the plateau curve. Public because moat.py's
    technology leg asks the same question and must not keep a second copy
    of the shape — a monotonic duplicate there would reintroduce exactly
    the defect RND_BAND exists to remove, in a module nobody would think
    to check."""
    return _plateau(pct, RND_BAND)


def _plateau(value, band) -> float | None:
    """Rises to a plateau, sits flat across it, falls away after.

    See RND_BAND for why intensity must not be monotonic.
    """
    if value is None:
        return None
    floor, lo, hi, zero_above = band
    if value < lo:
        return max(0.0, min(100.0, (value - floor) / max(lo - floor, 1e-9)
                            * 100.0))
    if value <= hi:
        return 100.0
    return max(0.0, min(100.0, (zero_above - value)
                        / max(zero_above - hi, 1e-9) * 100.0))


def _growth(series) -> float | None:
    clean = [v for v in series if v is not None]
    if len(clean) < 2:
        return None
    newest, oldest = clean[0], clean[-1]
    if oldest is None or oldest <= 0:
        return None
    return round((newest / oldest - 1.0) * 100.0, 1)


def _ratio(numer_growth, denom_growth) -> float | None:
    """Growth-per-unit-of-spending-growth, guarded at both ends.

    A shrinking denominator makes the naive ratio meaningless — spending
    that FELL while revenue rose is maximum productivity, not a negative
    number — so that case is pinned to the top of the scale instead.
    """
    if numer_growth is None or denom_growth is None:
        return None
    if denom_growth <= 0:
        return PROD_HI if numer_growth > 0 else PROD_LO
    return round((1 + numer_growth / 100) / (1 + denom_growth / 100), 2)


def compute(data: dict) -> dict:
    rev = data.get("revenue_annual") or []
    rnd = data.get("rnd_annual") or []
    capex = data.get("capex_annual") or []
    employees = data.get("employees")

    rev_now = next((v for v in rev if v is not None), None)
    rnd_now = next((v for v in rnd if v is not None), None)
    # Capex is filed as a negative number; intensity is about magnitude.
    capex_now = next((abs(v) for v in capex if v is not None), None)

    rnd_pct = (round(rnd_now / rev_now * 100, 1)
               if rnd_now is not None and rev_now else None)
    capex_pct = (round(capex_now / rev_now * 100, 1)
                 if capex_now is not None and rev_now else None)

    rev_growth = _growth(rev)
    rnd_growth = _growth(rnd)
    capex_growth = _growth([abs(v) if v is not None else None for v in capex])

    rnd_productivity = _ratio(rev_growth, rnd_growth)
    capex_productivity = _ratio(rev_growth, capex_growth)

    rev_per_employee = (round(rev_now / employees)
                        if rev_now and employees else None)

    # Reinvestment RATE — total capital going back into the business. A
    # company reinvesting 4% of revenue is not building anything that
    # becomes a market leader, whatever its growth rate today.
    total_pct = None
    if rnd_pct is not None or capex_pct is not None:
        total_pct = round((rnd_pct or 0) + (capex_pct or 0), 1)

    scored = blend([
        ("R&D productivity", 30, _prod_score(rnd_productivity),
         f"{rnd_productivity:.2f}x revenue growth per unit of R&D growth"
         if rnd_productivity is not None else "no R&D history"),
        ("R&D intensity", 25, _plateau(rnd_pct, RND_BAND),
         _intensity_note(rnd_pct, RND_BAND, "R&D")),
        ("Capex productivity", 25, _prod_score(capex_productivity),
         f"{capex_productivity:.2f}x" if capex_productivity is not None
         else "no capex history"),
        ("Capex intensity", 20, _plateau(capex_pct, CAPEX_BAND),
         _intensity_note(capex_pct, CAPEX_BAND, "capex")),
    ])

    return {
        "score": scored["score"],
        "coverage": scored["coverage"],
        "components": scored["components"],
        "rnd_pct": rnd_pct,
        "rnd_growth_pct": rnd_growth,
        "rnd_productivity": rnd_productivity,
        "capex_pct": capex_pct,
        "capex_growth_pct": capex_growth,
        "capex_productivity": capex_productivity,
        "reinvestment_rate_pct": total_pct,
        "revenue_per_employee": rev_per_employee,
        "employees": employees,
        "unmeasured": list(UNMEASURABLE),
        "productive": _productive(rnd_productivity, capex_productivity),
        "detail": _detail(rnd_pct, rnd_productivity, capex_pct,
                          capex_productivity),
    }


def _prod_score(ratio):
    return None if ratio is None else scale(ratio, PROD_LO, PROD_HI)


def _intensity_note(pct, band, label) -> str:
    """Say WHICH side of the plateau a reading falls on.

    "R&D 300% of revenue" scoring near zero looks like a bug unless the
    note explains that the intensity is past the point of meaning anything
    except a revenue base too small to carry it.
    """
    if pct is None:
        return f"{label} not reported"
    _floor, lo, hi, _zero = band
    if pct < lo:
        return (f"{pct:.1f}% of revenue — below the {lo:.0f}% that a "
                f"technology leader typically reinvests")
    if pct <= hi:
        return f"{pct:.1f}% of revenue"
    return (f"{pct:.1f}% of revenue — past the point where intensity means "
            f"ambition; at this level it reflects a revenue base too small "
            f"to carry the spending")


def _productive(rnd_p, capex_p) -> bool | None:
    """Tri-state: is the reinvestment paying for itself?

    None where neither ratio could be computed — which is not the same as
    "no", and the page says so.
    """
    vals = [v for v in (rnd_p, capex_p) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals) >= 1.0


def _detail(rnd_pct, rnd_p, capex_pct, capex_p) -> str:
    bits = []
    if rnd_pct is not None:
        bits.append(f"R&D {rnd_pct:.1f}% of revenue")
    if rnd_p is not None:
        bits.append("research is outrunning revenue" if rnd_p < 1.0
                    else f"each unit of R&D growth came with {rnd_p:.2f}x "
                         f"revenue growth")
    if capex_pct is not None:
        bits.append(f"capex {capex_pct:.1f}%")
    if capex_p is not None and capex_p < 1.0:
        bits.append("capacity is being added faster than it fills")
    return "; ".join(bits) or "no reinvestment history available"
