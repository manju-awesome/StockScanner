"""
Every number this engine assumes, in one place, each one labelled.

Why a module instead of default arguments
-----------------------------------------
A retirement projection is mostly assumptions wearing the costume of a
calculation. Change "returns 7%" to "returns 5%" and the answer moves by
a decade. If those numbers live as default arguments scattered across
six modules, the output looks like a measurement and nobody can audit
what it actually rests on.

So every assumption is declared here as an `Assumption` — value, unit,
where it came from, and how much confidence it deserves — and every
report can print the full list next to its own conclusions. When the
answer is surprising, the reader can check the inputs rather than trust
the output.

Real vs nominal
---------------
The engine works in REAL (today's-dollar) terms throughout. Spending is
quoted in today's money, returns are net of inflation, and the portfolio
values it reports are what they would buy today. Mixing the two is the
most common way a retirement model quietly lies: a 7% nominal return and
a spending figure that never inflates will fund a retirement that does
not exist. Anything nominal in this codebase says so in its name.

Confidence tags follow the project convention: HIGH means observed data,
MEDIUM means a defensible standard assumption, LOW means a placeholder
that needs the user's real number before any conclusion leans on it.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


@dataclass(frozen=True)
class Assumption:
    key: str
    value: float
    unit: str
    label: str
    basis: str
    confidence: str = MEDIUM

    def as_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────
# The defaults.
#
# These are deliberately conservative rather than typical. A plan built on
# optimistic inputs fails in exactly the situation it was built for, and
# the cost of being wrong is asymmetric: too-pessimistic means working a
# year longer, too-optimistic means running out of money at 82.
# ─────────────────────────────────────────────────────────────────────────

DEFAULTS: dict[str, Assumption] = {
    a.key: a for a in [
        Assumption(
            "equity_real_return", 5.0, "%/yr",
            "Equity real return",
            "Long-run US equity real return has run ~6.5-7%; this trims it "
            "for today's starting valuations and for fees/slippage. Not a "
            "forecast — a planning number.",
            MEDIUM),
        Assumption(
            "equity_volatility", 17.0, "%/yr",
            "Equity volatility",
            "Annual standard deviation of a broad equity index, close to "
            "the long-run realised figure.",
            MEDIUM),
        Assumption(
            "bond_real_return", 1.5, "%/yr",
            "Bond real return",
            "Real yield available on intermediate high-grade bonds, held to "
            "maturity. Positive but thin.",
            MEDIUM),
        Assumption(
            "bond_volatility", 6.0, "%/yr",
            "Bond volatility",
            "Annual standard deviation of an intermediate bond index. "
            "Higher than the pre-2022 assumption for a reason.",
            MEDIUM),
        Assumption(
            "cash_real_return", 0.0, "%/yr",
            "Cash real return",
            "Cash is assumed to match inflation and no more. Over long "
            "windows it has often done slightly worse.",
            MEDIUM),
        Assumption(
            "equity_bond_correlation", 0.15, "ratio",
            "Equity/bond correlation",
            "Modestly positive. The negative correlation of 1990-2020 was "
            "a regime, not a law, and 2022 showed both sides falling "
            "together.",
            LOW),
        Assumption(
            "inflation", 3.0, "%/yr",
            "General inflation",
            "Slightly above the 2% target because the plan should survive "
            "the overshoot, not the target.",
            MEDIUM),
        Assumption(
            "healthcare_inflation", 5.5, "%/yr",
            "Healthcare inflation",
            "Medical costs have persistently outrun general inflation. "
            "Applied only to the healthcare line, never to total spending.",
            MEDIUM),
        Assumption(
            "education_inflation", 5.0, "%/yr",
            "Education inflation",
            "Tuition has outrun general inflation for decades in both the "
            "US and urban India.",
            LOW),
        Assumption(
            "life_expectancy", 92, "age",
            "Planning horizon (self)",
            "A planning age, not a life expectancy. Roughly the 85th "
            "percentile survival age for a healthy person now in their "
            "40s — you plan for the tail, not the median, because the "
            "median outcome is the one you can afford to be wrong about.",
            MEDIUM),
        Assumption(
            "spouse_life_expectancy", 95, "age",
            "Planning horizon (spouse)",
            "Set higher than the primary horizon: for a couple, the money "
            "has to last until the SECOND death, and female life "
            "expectancy runs longer.",
            MEDIUM),
        Assumption(
            "safe_withdrawal_rate", 3.5, "%",
            "Baseline withdrawal rate",
            "Below the classic 4% because that study assumed a 30-year "
            "US retirement, a 50/50 portfolio and no fees. A retirement "
            "starting before 60 is longer than 30 years.",
            MEDIUM),
        Assumption(
            "usdinr_drift", 2.0, "%/yr",
            "USD/INR drift",
            "Long-run rupee depreciation against the dollar, roughly the "
            "inflation differential. Directionally reliable, annually "
            "useless.",
            LOW),
        Assumption(
            "salary_growth_real", 1.0, "%/yr",
            "Real salary growth",
            "Above-inflation raises, net of the fact that they stop or "
            "reverse late in a career.",
            LOW),
        Assumption(
            "monte_carlo_paths", 10000, "paths",
            "Monte Carlo paths",
            "Enough that the success probability is stable to well under a "
            "percentage point between runs.",
            HIGH),
    ]
}


def resolve(overrides: dict | None = None) -> dict[str, Assumption]:
    """The assumption set for a run: defaults, with the user's on top.

    A user-supplied value is tagged HIGH — not because the user is more
    likely to be right about equity returns, but because it is THEIR plan
    and their stated intent outranks a house default. The basis line
    records that it came from them, so nothing is silently attributed to
    research it did not come from.
    """
    out = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key not in out or value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        base = out[key]
        out[key] = replace(
            base, value=value,
            basis=f"Set by you (house default was {base.value:g} {base.unit}).",
            confidence=HIGH)
    return out


def value(assumptions: dict[str, Assumption], key: str) -> float:
    a = assumptions.get(key) or DEFAULTS[key]
    return a.value


def rate(assumptions: dict[str, Assumption], key: str) -> float:
    """A percentage assumption as a decimal rate."""
    return value(assumptions, key) / 100.0


def manifest(assumptions: dict[str, Assumption]) -> list[dict]:
    """The assumption list, for printing beside any conclusion."""
    return [a.as_dict() for a in assumptions.values()]
