"""
The financial-independence ladder — four numbers, not one.

Why a ladder
------------
"My FI number is $3M" is a single point on a curve, and the curve is what
actually governs decisions. The useful facts are: at what portfolio value
do you stop being financially fragile (Lean), at what value can you keep
living exactly as you do now (Standard), and how much does each further
increment of lifestyle cost in years of work.

The ladder also reframes the question. Lean FI is usually reached years
before Standard FI, and reaching it changes your relationship with your
job long before you can quit — it converts "I must" into "I choose to",
which is most of the psychological benefit of retiring, available early.

The rungs
---------
LEAN        Essential spending only. The plan survives; the lifestyle
            does not. This is the fragility line, not a goal.
STANDARD    Essential plus today's lifestyle spending. Life continues
            unchanged.
COMFORTABLE Standard plus 25%. Room for the travel and the help that
            people actually want retirement for.
FAT         Standard plus 60%. Genuine flexibility — family support,
            a bad year absorbed without a spreadsheet.

The multipliers on the top two rungs are conventions, not findings, and
are labelled as such wherever they are shown.
"""

from __future__ import annotations

COMFORTABLE_MULTIPLE = 1.25
FAT_MULTIPLE = 1.60


def ladder(essential: float, lifestyle: float, current_portfolio: float,
           withdrawal_rate: float, horizon_years: float | None = None,
           equity_real_return: float = 5.0) -> dict:
    """The four rungs, each with required capital and progress to it.

    Progress is measured against RETIREMENT-funding assets, not net
    worth — the caller is responsible for passing the right base. Using
    net worth here would count the house and make every rung look closer
    than it is.
    """
    standard = essential + lifestyle
    rungs = [
        ("Lean", essential,
         "Essentials only — housing, food, healthcare, insurance, taxes. "
         "The line below which the plan is fragile."),
        ("Standard", standard,
         "Life continues exactly as it does today."),
        ("Comfortable", standard * COMFORTABLE_MULTIPLE,
         f"Today's spending +{(COMFORTABLE_MULTIPLE-1)*100:.0f}%. A "
         f"convention, not a finding."),
        ("Fat", standard * FAT_MULTIPLE,
         f"Today's spending +{(FAT_MULTIPLE-1)*100:.0f}%. Flexibility for "
         f"family support and bad years."),
    ]

    out = []
    for name, spend, note in rungs:
        required = spend * 100.0 / withdrawal_rate if withdrawal_rate else None
        progress = (100.0 * current_portfolio / required) if required else None
        out.append({
            "level": name,
            "annual_spending": spend,
            "required": required,
            "progress_pct": progress,
            "gap": (required - current_portfolio) if required else None,
            "reached": bool(required and current_portfolio >= required),
            "note": note,
        })

    reached = [r["level"] for r in out if r["reached"]]
    return {
        "rungs": out,
        "withdrawal_rate": withdrawal_rate,
        "highest_reached": reached[-1] if reached else None,
        "next_rung": next((r for r in out if not r["reached"]), None),
        "basis": (f"Required capital = annual spending ÷ "
                  f"{withdrawal_rate:.2f}%. Real terms, before any tax on "
                  f"withdrawals."),
    }


def years_to_rung(required: float, current: float, annual_savings: float,
                  real_return: float) -> float | None:
    """Years until contributions and compounding reach a target.

    Solved rather than iterated, and returns None when the target is not
    reachable on the current path — an honest None beats a number like
    "94 years" that implies a plan.
    """
    if required is None or current >= required:
        return 0.0
    r = real_return / 100.0
    if annual_savings <= 0 and r <= 0:
        return None
    if abs(r) < 1e-9:
        return (required - current) / annual_savings if annual_savings else None

    import math
    # FV = current*(1+r)^n + savings*((1+r)^n - 1)/r  →  solve for n
    numerator = required * r + annual_savings
    denominator = current * r + annual_savings
    if denominator <= 0 or numerator <= 0:
        return None
    n = math.log(numerator / denominator) / math.log(1 + r)
    return n if 0 < n < 100 else None
