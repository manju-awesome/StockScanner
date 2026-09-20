"""
Goal buckets and the emergency fund — section 12 and section 14.

The rule this module enforces
-------------------------------
One pot funding two goals looks like it funds both, right up until the
year they collide. So every declared goal gets its own line, its own
target and its own funding source, and any goal drawing on the retirement
portfolio is subtracted from retirement-funding assets rather than
quietly shared with them.

The emergency fund is sized on ESSENTIAL spending
---------------------------------------------------
Not total spending. In the situation an emergency fund exists for — lost
job, medical event, a move — the travel budget is the first thing to go.
Sizing the reserve on total spending overstates it by whatever your
discretionary share is, and cash held above what the emergency actually
needs is a real cost: it earns nothing in real terms, every year, for
decades.
"""

from __future__ import annotations

STANDARD_MONTHS = 6


def emergency_fund(profile, balance) -> dict:
    """Minimum, recommended and maximum reserve, with the reasons.

    The months are adjusted for the specific things that make an
    emergency longer or more expensive: a single income, dependents,
    self-employment, and a planned international move — which is the big
    one, because relocating a household across a border while between
    jobs is exactly when a reserve gets tested.
    """
    monthly_essential = profile.essential_spending / 12.0
    if not monthly_essential:
        return {"error": "Essential spending not recorded."}

    months = float(STANDARD_MONTHS)
    reasons = [f"{STANDARD_MONTHS} months is the baseline."]

    income = profile.income_detail
    earners = sum(1 for k, v in income.items()
                  if v and k in ("salary", "spouse_salary", "business"))
    if earners <= 1:
        months += 2
        reasons.append("single household income: +2 months")

    dependents = len(profile.children) + len(
        [p for p in profile.people if p.role == "dependent"])
    if dependents:
        months += 1
        reasons.append(f"{dependents} dependent(s): +1 month")

    if income.get("business"):
        months += 2
        reasons.append("business income is variable: +2 months")

    if profile.is_cross_border:
        months += 3
        reasons.append(
            f"a planned move to {profile.retirement_country} adds "
            f"relocation costs and a gap in coverage: +3 months")

    minimum = monthly_essential * 3
    recommended = monthly_essential * months
    maximum = monthly_essential * (months + 6)

    held = (balance.get("by_liquidity") or {}).get("liquid", 0)
    cash = (balance.get("by_tax") or {}).get("cash", 0)

    if cash < minimum:
        status, verdict = "bad", "Below the three-month floor."
    elif cash < recommended:
        status, verdict = "watch", "Above the floor, below the recommendation."
    elif cash <= maximum:
        status, verdict = "good", "Appropriately sized."
    else:
        excess = cash - maximum
        status = "watch"
        verdict = (f"${excess:,.0f} above what the emergency case needs. "
                   f"Cash earns roughly zero in real terms — that excess "
                   f"costs you compounding every year it sits there.")

    return {
        "monthly_essential": monthly_essential,
        "months_recommended": months,
        "minimum": minimum, "recommended": recommended, "maximum": maximum,
        "held_cash": cash, "held_liquid": held,
        "status": status, "verdict": verdict, "reasons": reasons,
    }


def buckets(profile, balance) -> list[dict]:
    """Every goal, its target, its dedicated capital and its shortfall.

    A goal with no dedicated capital and a portfolio funding source is
    flagged — not as an error, but because it means the retirement number
    on this page is overstated by that amount unless the one-off has
    already been netted out, which it has.
    """
    declared = profile.goals or {}
    rows = []

    for name, spec in declared.items():
        if not isinstance(spec, dict):
            continue
        target = spec.get("target")
        funded = spec.get("funded")
        rows.append({
            "goal": name.replace("_", " ").title(),
            "target": float(target) if target else None,
            "funded": float(funded) if funded else 0.0,
            "gap": ((float(target) - float(funded or 0))
                    if target else None),
            "source": spec.get("source", "dedicated"),
            "when": spec.get("when"),
        })

    # One-off expenses are goals too, whether or not they were declared
    # as such.
    for o in profile.one_offs:
        rows.append({
            "goal": o.label or "One-off",
            "target": o.amount,
            "funded": 0.0 if o.funded_from == "portfolio" else o.amount,
            "gap": o.amount if o.funded_from == "portfolio" else 0.0,
            "source": o.funded_from,
            "when": o.year,
            "from_one_off": True,
        })

    for r in rows:
        r["competes_with_retirement"] = (r["source"] == "portfolio")
    return rows
