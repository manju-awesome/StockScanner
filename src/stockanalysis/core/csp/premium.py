"""
Premium adequacy, capital efficiency, and the ideal-contract zone.

This module answers the question the rest of the engine kept dodging:
**is this particular option worth selling today?** — as distinct from
whether the company is worth owning. An outstanding business does not
make an outstanding put; the two are separate findings and are scored
separately.

The organising idea is a REQUIRED premium. Selling a cash-secured put
ties up collateral that would otherwise earn the risk-free rate, and
takes on the obligation to buy stock that may be falling. So the yield
has to clear a hurdle built from:

    required = risk-free + assignment risk premium

where the assignment risk premium scales with the probability of being
assigned (delta) and the market regime, and is DISCOUNTED by quality,
valuation and support — because being assigned into a cheap, high-quality
name at a level that holds is not really a cost. That discount is the
formal version of "I would be happy to own it anyway".

Actual / required is the Premium Adequacy Score, and it is what separates
"good company, bad contract" from "good company, good contract".

Annualisation is deliberately kept out of the headline. A 1.59% yield
over 38 days annualises to 15.3%, which flatters it — you cannot
actually repeat the trade continuously at those terms, and comparing a
distorted number against a real Treasury yield is how a mediocre
contract starts looking attractive.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f

# The compensation demanded for taking equity downside at a 0.20 delta in
# a neutral regime on an average-quality name, over and above cash. Not a
# market-derived constant — a house rule, and the single most important
# knob on the page.
BASE_RISK_SPREAD = 8.0          # annualised percentage points
CORE_DELTA = 0.20               # the delta BASE_RISK_SPREAD is quoted at

REGIME_FACTOR = {"FAVORABLE": 0.85, "BULLISH": 0.85,
                 "SELECTIVE": 1.0, "NEUTRAL": 1.0,
                 "DEFENSIVE": 1.35, "BEARISH": 1.35}

# Absolute period-yield floors. The dynamic hurdle is the real test, but
# below these the contract is not worth the ticket and the assignment
# paperwork whatever the model says.
STATIC_FLOOR = ((30, 45, 1.0), (45, 60, 1.5), (60, 999, 2.0))
STATIC_FLOOR_SHORT = 0.7        # under 30 DTE


def _static_floor(dte) -> float:
    if dte is None:
        return 0.0
    if dte < 30:
        return STATIC_FLOOR_SHORT
    for lo, hi, floor in STATIC_FLOOR:
        if lo <= dte < hi:
            return floor
    return 2.0


def required(delta, dte, risk_free, quality, valuation_margin,
             support_confidence, regime, iv_rv=None) -> dict:
    """The yield this contract has to pay to be worth selling.

    Returns both an annualised hurdle and the period yield it implies
    over `dte` days, plus the reasoning. `risk_free` is a decimal
    (0.0468), everything else is in natural units.
    """
    rf_pct = (f(risk_free) or 0.04) * 100.0
    d = abs(f(delta) or CORE_DELTA)
    days = f(dte) or 30.0

    # Assignment risk scales with the chance of it happening.
    delta_factor = max(0.4, min(2.0, d / CORE_DELTA))
    regime_factor = REGIME_FACTOR.get((regime or "").upper(), 1.0)

    # The discount for wanting the stock anyway. Kept DELIBERATELY SMALL,
    # and this is the subtlest calibration on the page.
    #
    # Quality, valuation and support are already the whole of the stock
    # score. Letting them also cut the option's hurdle counts them twice
    # — which is precisely what splitting the two scores was meant to
    # prevent. An earlier version shaved up to 55% off the hurdle and
    # produced the exact failure the split exists to catch: a 1.59% yield
    # at 0.98x realised vol scored "well paid" because the hurdle had
    # been discounted to meet it.
    #
    # So the discount now bottoms out at 85%. Being happy to own the
    # stock is a reason to accept assignment; it is not a reason to
    # accept less money for taking it.
    q, disc = f(quality), f(valuation_margin)
    sup = f(support_confidence)
    want = 1.0
    notes = []
    if q is not None and q >= 85:
        want -= 0.06; notes.append("elite quality")
    elif q is not None and q >= 78:
        want -= 0.03; notes.append("high quality")
    if disc is not None and disc >= 25:
        want -= 0.06; notes.append("deep discount")
    elif disc is not None and disc >= 10:
        want -= 0.03; notes.append("undervalued")
    if sup is not None and sup >= 80:
        want -= 0.03; notes.append("strong support")
    want = max(0.85, want)

    spread = BASE_RISK_SPREAD * delta_factor * regime_factor * want
    annual = rf_pct + spread
    period = annual * days / 365.0

    floor = _static_floor(days)
    binding = "dynamic" if period >= floor else "static floor"

    return {
        "annualised": round(annual, 2),
        "period_pct": round(max(period, floor), 3),
        "dynamic_period_pct": round(period, 3),
        "static_floor_pct": floor,
        "binding": binding,
        "risk_free_pct": round(rf_pct, 2),
        "risk_spread": round(spread, 2),
        "want_factor": round(want, 2),
        "detail": (f"cash pays {rf_pct:.2f}%; this contract must clear "
                   f"{annual:.1f}% annualised "
                   f"({max(period, floor):.2f}% over {days:.0f}d)"
                   + (f" — hurdle reduced for " + ", ".join(notes)
                      if notes else "")),
    }


def adequacy(actual_period_pct, req) -> dict:
    """Actual / required. Above 1.0 the contract pays for its risk."""
    a, r = f(actual_period_pct), (req or {}).get("period_pct")
    if a is None or not r:
        return {"ratio": None, "score": None, "label": None,
                "verdict": "unknown"}

    ratio = a / r
    if ratio >= 1.5:
        label, verdict = "🟢 Well paid", "STRONG"
    elif ratio >= 1.15:
        label, verdict = "🟢 Adequate", "GOOD"
    elif ratio >= 1.0:
        label, verdict = "🟡 Marginal", "MARGINAL"
    elif ratio >= 0.75:
        label, verdict = "🟠 Underpaid", "WEAK"
    else:
        label, verdict = "🔴 Not paid for the risk", "POOR"

    # 1.0 is the pass mark and scores 60; 1.8x is needed for full marks,
    # so simply clearing the hurdle cannot carry the option score on its
    # own. Below the hurdle the score falls away steeply — being underpaid
    # for assignment risk is not a small demerit.
    score = (60.0 + min(40.0, (ratio - 1.0) / 0.8 * 40.0) if ratio >= 1.0
             else max(0.0, (ratio / 1.0) ** 1.5 * 60.0))

    return {"ratio": round(ratio, 2), "score": round(score),
            "label": label, "verdict": verdict,
            "shortfall_pct": (None if ratio >= 1.0
                              else round(r - a, 3))}


def capital_efficiency(annualised, risk_free, collateral, premium_total,
                       dte) -> dict:
    """Is tying up this collateral for these days worth this credit?

    Measured against cash, because cash is the actual alternative — the
    collateral is already sitting there. The excess over the risk-free
    rate is the whole return for taking equity risk.
    """
    a, rf = f(annualised), (f(risk_free) or 0.04) * 100.0
    if a is None:
        return {"excess": None, "score": None, "label": None}

    excess = a - rf
    # Treasury interest the collateral would have earned over the same
    # window — the like-for-like comparison, in dollars.
    cash_alt = ((f(collateral) or 0) * rf / 100.0 * (f(dte) or 0) / 365.0)

    if excess >= 12:   label = "🟢 Well above cash"
    elif excess >= 6:  label = "🟢 Above cash"
    elif excess >= 2:  label = "🟡 Barely above cash"
    elif excess >= 0:  label = "🟠 Level with cash"
    else:              label = "🔴 Below cash"

    return {
        "excess": round(excess, 1),
        "risk_free_pct": round(rf, 2),
        "cash_alternative": round(cash_alt, 2),
        "premium_total": f(premium_total),
        "edge_dollars": (None if premium_total is None
                         else round(f(premium_total) - cash_alt, 2)),
        "score": round(max(0.0, min(100.0, excess / 15.0 * 100.0))),
        "label": label,
        "detail": (f"${f(premium_total):,.0f} credit vs ${cash_alt:,.0f} "
                   f"of Treasury interest on the same collateral over "
                   f"{f(dte):.0f} days" if premium_total is not None else ""),
    }


def per_unit_downside(premium, atr, expected_move, basis, levels) -> dict:
    """Premium measured against how far the stock actually moves.

    Annualised yield says nothing about whether the premium is large
    relative to the risk being taken. A 1.6% yield on a name that swings
    3.3% a day is thin; the same yield on a placid utility is not. These
    ratios are unit-free and do not distort with DTE.
    """
    p = f(premium)
    out = {"per_atr": None, "per_expected_move": None,
           "technical_cushion_pct": None, "cushion_level": None}
    if p is None:
        return out

    a, em = f(atr), f(expected_move)
    if a and a > 0:
        out["per_atr"] = round(p / a, 2)
    if em and em > 0:
        # What share of a one-standard-deviation move the premium covers.
        out["per_expected_move"] = round(p / em, 3)

    # Technical cushion — how far the effective basis sits above the
    # nearest support BELOW it. That is the room the position has before
    # it is underwater with no level to lean on.
    b = f(basis)
    if b:
        below = [lv for lv in (levels or [])
                 if (f(lv.get("price")) or 0) < b]
        if below:
            nearest = max(below, key=lambda lv: lv["price"])
            out["technical_cushion_pct"] = round(
                (b - nearest["price"]) / b * 100, 1)
            out["cushion_level"] = nearest.get("name")
    return out


def margin_at_assignment(basis, discount) -> dict:
    """Margin of safety measured at the EFFECTIVE BASIS, not at spot.

    This is the number that says whether the trade is an acquisition at a
    favourable price or merely premium collection. It is reported in
    whichever unit the valuation method supports — a reverse DCF yields
    no fair-value price, so there is nothing to take a percentage of, and
    inventing one would be the same error as filling in a fair value.
    """
    b = f(basis)
    fair = (discount or {}).get("fair_value")
    if b is None:
        return {"pct": None, "basis_kind": None, "detail": "no basis"}

    if fair:
        pct = (fair - b) / fair * 100.0
        return {"pct": round(pct, 1), "basis_kind": "price",
                "detail": (f"basis ${b:,.2f} against fair value "
                           f"${fair:,.2f}")}

    gap = (discount or {}).get("growth_gap_pp")
    if gap is not None:
        return {"pct": round(-gap, 1), "basis_kind": "growth",
                "detail": (f"reverse DCF — {discount.get('detail') or ''}; "
                           f"assignment at ${b:,.2f} improves on spot")}
    return {"pct": None, "basis_kind": None,
            "detail": "no valuation basis to measure against"}


def ideal_zone(spot, levels, anchor, buy_zone, req, min_dte, max_dte,
               max_spread_pct=8.0, target_iv_rv=1.05) -> dict:
    """The contract this name WOULD be worth selling.

    Emitted whether or not a qualifying contract exists today, so a name
    that fails can still say what it is waiting for. "Wait for IV
    expansion" is only actionable next to the premium that would make it
    a trade.
    """
    lo_k = hi_k = None
    if anchor and anchor.get("price"):
        # Below the anchor, and not so far below that the premium
        # vanishes — roughly the 0.10-0.30 delta span in price terms.
        hi_k = round(anchor["price"] * 0.995, 2)
        lo_k = round(anchor["price"] * 0.90, 2)
    if buy_zone:
        hi_k = min(hi_k, buy_zone) if hi_k else buy_zone

    min_prem = None
    if hi_k and req.get("period_pct"):
        # Premium implied by the hurdle at the top of the strike zone.
        min_prem = round(hi_k * req["period_pct"] / 100.0, 2)

    return {
        "strike_low": lo_k, "strike_high": hi_k,
        "dte_low": min_dte, "dte_high": max_dte,
        "min_premium": min_prem,
        "ideal_premium": (None if min_prem is None
                          else round(min_prem * 1.35, 2)),
        "max_spread_pct": max_spread_pct,
        "target_delta": (0.15, 0.25),
        "target_iv_rv": target_iv_rv,
        "min_required_yield_pct": req.get("period_pct"),
    }
