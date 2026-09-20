"""
The retirement score: 0-100, and an honest account of what it covers.

Why the weights are what section 17 says
------------------------------------------
Retirement Funding carries 20 because it is the only component that
measures the actual question. Savings Rate and Portfolio Quality carry 15
each because they are the two levers with the most influence on a plan
that is still in accumulation. Everything else is smaller because it
matters at the margin or matters later.

Unscored is not zero
--------------------
A component with no data is EXCLUDED and its weight removed from the
denominator, and the report says what share of the total weight was
actually scored. The alternative — scoring healthcare 0 because you
haven't told the engine your insurance situation — produces a score that
punishes you for silence and looks identical to a score that punishes
you for being unprepared. Those are different facts and the number
should not conflate them.

So a score of 74 with 65% coverage is not "74". It is "74 on the
two-thirds of the plan we can see", and the page says so.
"""

from __future__ import annotations

WEIGHTS = {
    "savings_rate": 15,
    "portfolio_quality": 15,
    "retirement_funding": 20,
    "tax_efficiency": 10,
    "risk_management": 10,
    "healthcare": 10,
    "liquidity": 5,
    "family_goals": 5,
    "debt": 5,
    "flexibility": 5,
}

LABELS = {
    "savings_rate": "Savings rate",
    "portfolio_quality": "Portfolio quality",
    "retirement_funding": "Retirement funding",
    "tax_efficiency": "Tax efficiency",
    "risk_management": "Risk management",
    "healthcare": "Healthcare preparation",
    "liquidity": "Liquidity",
    "family_goals": "Family goals",
    "debt": "Debt",
    "flexibility": "Flexibility",
}


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def _scale(value: float | None, floor: float, ceiling: float) -> float | None:
    """Linear 0-100 between a floor and a ceiling, clamped."""
    if value is None:
        return None
    if ceiling == floor:
        return None
    return _clamp(100.0 * (value - floor) / (ceiling - floor))


def _savings_rate(profile) -> tuple[float | None, str]:
    rate = profile.savings_rate
    if rate is None:
        return None, "Income not recorded."
    s = _scale(rate, 5.0, 35.0)
    return s, (f"Saving {rate:.1f}% of gross income. 35%+ scores full "
               f"marks; below 5% scores zero.")


def _portfolio_quality(balance, mix_desc, concentration) -> tuple[float | None, str]:
    """Diversification and concentration, from what the balance sheet knows.

    Starts at 100 and takes deductions, because the failure modes here are
    specific and nameable — a top holding at 30%, everything in one
    country, no bonds within a decade of retirement — while "good
    diversification" has no positive signature worth scoring.
    """
    if not balance.get("total_assets"):
        return None, "No asset balances recorded."
    reasons, penalty = [], 0.0

    top = max((c.get("weight") or 0 for c in concentration), default=0)
    if top > 25:
        penalty += 35
        reasons.append(f"top holding is {top:.0f}% of the brokerage")
    elif top > 15:
        penalty += 22
        reasons.append(f"top holding is {top:.0f}%")
    elif top > 10:
        penalty += 12
        reasons.append(f"top holding is {top:.0f}%")

    high = [c for c in concentration if (c.get("weight") or 0) > 10]
    if len(high) >= 3:
        penalty += 15
        reasons.append(f"{len(high)} positions each above 10%")

    equity_w = (mix_desc.get("weights") or {}).get("equity", 0)
    if equity_w >= 95:
        penalty += 10
        reasons.append("no bond or cash ballast at all")

    cash = balance["by_tax"].get("cash", 0) or 0
    inv = balance.get("investable") or 0
    if inv and cash / inv > 0.35:
        penalty += 12
        reasons.append(f"{100*cash/inv:.0f}% of investable assets in cash")

    score = _clamp(100 - penalty)
    why = ("No structural problems found in the recorded holdings."
           if not reasons else "Deductions for: " + "; ".join(reasons) + ".")
    return score, why


def _retirement_funding(funding_pct: float | None) -> tuple[float | None, str]:
    if funding_pct is None:
        return None, "Target portfolio could not be computed."
    s = _scale(funding_pct, 0.0, 100.0)
    return s, (f"Retirement assets are {funding_pct:.0f}% of the planning "
               f"target. Scored linearly to 100%.")


def _tax_efficiency(balance) -> tuple[float | None, str]:
    """The MIX of tax treatments, not the absolute amount in any one.

    Scored on balance rather than on maximising tax-deferred savings,
    because a retiree with everything in a traditional 401(k) has no
    control over their taxable income and a retiree with three types of
    account has a dial. Optionality is the thing being measured.
    """
    total = sum(v for k, v in balance["by_tax"].items()
                if k != "non_financial")
    if not total:
        return None, "No financial assets recorded."
    free = balance["by_tax"].get("tax_free", 0) / total
    deferred = balance["by_tax"].get("tax_deferred", 0) / total
    taxable = balance["by_tax"].get("taxable", 0) / total

    present = sum(1 for x in (free, deferred, taxable) if x > 0.05)
    score = {0: 20.0, 1: 40.0, 2: 70.0, 3: 90.0}[present]
    if free > 0.15:
        score += 10
    score = _clamp(score)
    return score, (f"Tax-free {free*100:.0f}%, tax-deferred "
                   f"{deferred*100:.0f}%, taxable {taxable*100:.0f}%. "
                   f"Scored on having all three dials, not on the size of "
                   f"any one.")


def _risk_management(mc, profile) -> tuple[float | None, str]:
    if not mc or mc.get("error"):
        return None, "Simulation did not run."
    prob = mc.get("success_pct")
    s = _scale(prob, 50.0, 95.0)
    return s, (f"Monte Carlo survival {prob:.0f}%. 95% scores full marks, "
               f"50% scores zero.")


def _healthcare(profile, balance) -> tuple[float | None, str]:
    hc = (profile.raw.get("healthcare") or {})
    if not hc:
        return None, ("No healthcare plan recorded — not scored rather than "
                      "scored zero.")
    covered = hc.get("covered_until_medicare")
    reserve = hc.get("reserve")
    ltc = hc.get("long_term_care")
    score = 30.0
    bits = []
    if covered:
        score += 30
        bits.append("bridge coverage identified")
    if reserve:
        score += 25
        bits.append(f"reserve of ${float(reserve):,.0f}")
    if ltc:
        score += 15
        bits.append("long-term care considered")
    return _clamp(score), ("Credited for: " + ", ".join(bits) + "."
                          if bits else "Healthcare section is empty.")


def _liquidity(balance, profile) -> tuple[float | None, str]:
    inv = balance.get("investable") or 0
    if not inv:
        return None, "No investable assets recorded."
    liquid = balance["by_liquidity"].get("liquid", 0)
    months = ((liquid / (profile.total_spending / 12))
              if profile.total_spending else None)
    if months is None:
        return None, "Spending not recorded."
    s = _scale(months, 1.0, 12.0)
    return s, (f"{months:.0f} months of spending in liquid assets. "
               f"12 months scores full marks.")


def _family_goals(profile, balance) -> tuple[float | None, str]:
    """Whether other goals have their own capital or are quietly riding
    on the retirement portfolio."""
    one_offs = profile.one_offs
    if not profile.raw.get("one_offs") and not profile.goals:
        return None, "No family goals or one-off expenses recorded."
    if not one_offs:
        return 100.0, "No major one-off expenses declared."
    dedicated = sum(1 for o in one_offs if o.funded_from == "dedicated")
    portfolio_funded = [o for o in one_offs if o.funded_from == "portfolio"]
    total_portfolio_lump = sum(o.amount or 0 for o in portfolio_funded)
    retirement_assets = balance.get("retirement_assets") or 0
    drag = (total_portfolio_lump / retirement_assets
            if retirement_assets else None)

    score = 60.0 + 40.0 * (dedicated / len(one_offs))
    if drag and drag > 0.5:
        score -= 30
    return _clamp(score), (
        f"{dedicated} of {len(one_offs)} goals have dedicated capital; "
        f"${total_portfolio_lump:,.0f} is being taken from the retirement "
        f"portfolio.")


def _debt(balance, profile) -> tuple[float | None, str]:
    if profile.raw.get("liabilities") is None:
        return None, "Liabilities not recorded."
    liabs = balance.get("liabilities") or []
    if not liabs:
        return 100.0, "No debt."
    dti = balance.get("debt_to_income_pct")
    paydown = [l for l in liabs
               if l["disposition"]["action"].startswith("PAY DOWN")]
    score = 100.0
    if dti is not None:
        score = _scale(dti, 45.0, 5.0) or 50.0
    score -= 15 * len(paydown)
    return _clamp(score), (
        f"Debt service is {dti:.0f}% of gross income; {len(paydown)} of "
        f"{len(liabs)} debts read PAY DOWN."
        if dti is not None else f"{len(liabs)} debts recorded.")


def _flexibility(profile, mc_gain) -> tuple[float | None, str]:
    total = profile.total_spending
    if not total:
        return None, "Spending not recorded."
    flex = profile.flexible_fraction * 100
    s = _scale(flex, 5.0, 40.0)
    extra = (f" Being willing to use it is worth {mc_gain:+.0f} points of "
             f"survival probability." if mc_gain else "")
    return s, (f"{flex:.0f}% of spending is discretionary and could be cut "
               f"in a bad decade.{extra}")


def build(profile, balance, mix_desc, funding_pct, mc, mc_gain=None) -> dict:
    """Every component, the weighted total, and the coverage behind it."""
    raw = {
        "savings_rate": _savings_rate(profile),
        "portfolio_quality": _portfolio_quality(
            balance, mix_desc, balance.get("concentration") or []),
        "retirement_funding": _retirement_funding(funding_pct),
        "tax_efficiency": _tax_efficiency(balance),
        "risk_management": _risk_management(mc, profile),
        "healthcare": _healthcare(profile, balance),
        "liquidity": _liquidity(balance, profile),
        "family_goals": _family_goals(profile, balance),
        "debt": _debt(balance, profile),
        "flexibility": _flexibility(profile, mc_gain),
    }

    components, scored_weight = [], 0.0
    weighted_total = 0.0
    for key, weight in WEIGHTS.items():
        value, why = raw[key]
        scored = value is not None
        if scored:
            scored_weight += weight
            weighted_total += value * weight
        components.append({
            "key": key, "label": LABELS[key], "weight": weight,
            "score": round(value, 1) if scored else None,
            "scored": scored, "why": why,
            "points_lost": (round((100 - value) * weight / 100.0, 1)
                            if scored else None),
        })

    total = round(weighted_total / scored_weight, 1) if scored_weight else None
    coverage = round(100.0 * scored_weight / sum(WEIGHTS.values()), 0)

    drags = sorted([c for c in components if c["scored"]],
                   key=lambda c: -(c["points_lost"] or 0))[:3]

    return {
        "score": total,
        "coverage_pct": coverage,
        "components": components,
        "biggest_drags": drags,
        "unscored": [c["label"] for c in components if not c["scored"]],
        "note": (f"Scored on {coverage:.0f}% of the total weight. "
                 f"Unscored components are excluded from the denominator, "
                 f"not counted as zero."),
    }
