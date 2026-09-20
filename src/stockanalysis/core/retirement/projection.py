"""
The deterministic path: portfolio value year by year, from today until
the planning horizon.

What this is for
----------------
The Monte Carlo engine answers "how often does this work". This module
answers "what does the plan look like when nothing surprising happens",
which is the version you can actually read and check. It is also the
skeleton every scenario in section 15 perturbs.

Everything is in real terms, so a portfolio value in year 20 is what it
would buy today. One-off expenses land in the year they are dated rather
than being smeared across the horizon, because sequence risk is entirely
about when money leaves.

Two solves live here
--------------------
TARGET-AGE SOLVE: given a retirement age, what does the portfolio reach
and does it survive the horizon.

EARLIEST-AGE SOLVE: the youngest retirement age at which the portfolio
still survives. This is the number people actually want, and it is found
by testing each age rather than by inverting a formula, because one-off
expenses and a changing horizon make the closed form wrong.
"""

from __future__ import annotations

from . import mix


def _one_offs_by_year(profile) -> dict[int, float]:
    """One-off costs the PORTFOLIO has to fund, keyed by calendar year.

    Items marked as funded from income or from a dedicated pot are
    excluded here and accounted for in their own goal bucket — that is
    section 12's rule, that one goal must not silently consume another's
    capital.
    """
    out: dict[int, float] = {}
    for item in profile.one_offs:
        if item.amount is None or item.year is None:
            continue
        if item.funded_from != "portfolio":
            continue
        out[item.year] = out.get(item.year, 0.0) + item.amount
    return out


def run(profile, a, *, retirement_age: float, current_portfolio: float,
        annual_savings: float, allocation: dict | None = None,
        start_year: int | None = None,
        returns: list[float] | None = None,
        spend_multiplier: float = 1.0) -> dict:
    """One deterministic path. Returns the year-by-year table and the
    verdict on whether it lasts.

    `returns` overrides the expected return with an explicit real-return
    series (decimals, one per year from today). That is how every stress
    scenario in section 15 is built: same engine, same spending, a
    different history. Scenarios that alter a return series rather than
    the model itself stay comparable to the base case, which is the whole
    point of running them.

    `spend_multiplier` scales retirement spending — used both by the
    high-inflation scenarios and by the "what cut would fix this" solve.
    """
    from . import assumptions as A
    import datetime as _dt

    age = profile.age
    if age is None or retirement_age is None:
        return {"error": "Age and retirement age are required."}

    start_year = start_year or _dt.date.today().year
    horizon_age = A.value(a, "life_expectancy")
    if profile.spouse_age is not None:
        # The money must last to the SECOND death. Translate the spouse's
        # horizon into the primary person's age scale so the table has one
        # time axis.
        spouse_horizon = A.value(a, "spouse_life_expectancy")
        years_for_spouse = spouse_horizon - profile.spouse_age
        horizon_age = max(horizon_age, age + years_for_spouse)

    ret = profile.retirement_spending
    essential, lifestyle = ret["essential"], ret["lifestyle"]
    spend = (essential + lifestyle) * spend_multiplier

    real_return = mix.expected_real_return(allocation, a) / 100.0
    salary_growth = A.rate(a, "salary_growth_real")
    lumps = _one_offs_by_year(profile)

    rows = []
    value = float(current_portfolio)
    savings = float(annual_savings)
    depleted_age = None

    n_years = int(round(horizon_age - age))
    for i in range(max(n_years, 0) + 1):
        this_age = age + i
        year = start_year + i
        working = this_age < retirement_age

        opening = value
        contribution = savings if working else 0.0
        withdrawal = 0.0 if working else spend
        lump = lumps.get(year, 0.0)

        # Growth applies to the balance net of this year's flows, on the
        # assumption they happen through the year rather than all on
        # 1 January. Half-year convention: simple, and it avoids the
        # overstatement you get from compounding a full year on money
        # that was withdrawn in March.
        year_return = (returns[i] if returns is not None and i < len(returns)
                       else real_return)
        mid = opening + (contribution - withdrawal - lump) / 2.0
        growth = mid * year_return
        value = opening + contribution - withdrawal - lump + growth

        if value < 0 and depleted_age is None:
            depleted_age = this_age
            value = 0.0

        rows.append({
            "age": round(this_age, 1), "year": year, "working": working,
            "opening": opening, "contribution": contribution,
            "withdrawal": withdrawal, "one_off": lump,
            "growth": growth, "closing": max(value, 0.0),
        })

        if working:
            savings *= (1 + salary_growth)

    at_retirement = next((r["opening"] for r in rows
                          if not r["working"]), value)

    return {
        "rows": rows,
        "age_now": age,
        "retirement_age": retirement_age,
        "horizon_age": round(horizon_age, 1),
        "years_to_retirement": max(retirement_age - age, 0),
        "retirement_years": max(horizon_age - retirement_age, 0),
        "portfolio_at_retirement": at_retirement,
        "final_value": max(value, 0.0),
        "depleted_age": depleted_age,
        "survives": depleted_age is None,
        "annual_spend": spend,
        "real_return_pct": real_return * 100,
        "one_off_total": sum(lumps.values()),
    }


def earliest_safe_age(profile, a, *, current_portfolio: float,
                      annual_savings: float, allocation: dict | None = None,
                      buffer_years: float = 2.0) -> dict:
    """The youngest age at which the deterministic path still survives,
    with a margin.

    The buffer exists because "the money lasts to the last year with $1
    left" is not a plan, it is a coincidence. The path must finish with
    `buffer_years` of spending still in hand, which keeps the answer off
    the cliff edge and leaves something for the years past the planning
    horizon that some people get. Even so, this is a DETERMINISTIC answer — it
    assumes average returns every single year, which never happens. The
    Monte Carlo probability is the number to trust; this one is the
    number to understand.
    """
    age = profile.age
    if age is None:
        return {"error": "Age is required."}
    spend = profile.retirement_spending["total"]
    cushion = spend * buffer_years

    best = None
    for candidate in range(int(age) + 1, 81):
        path = run(profile, a, retirement_age=candidate,
                   current_portfolio=current_portfolio,
                   annual_savings=annual_savings, allocation=allocation)
        if path.get("error"):
            return path
        if path["survives"] and path["final_value"] >= cushion:
            best = {"age": candidate, "path": path}
            break

    return {
        "earliest_age": best["age"] if best else None,
        "path": best["path"] if best else None,
        "buffer_years": buffer_years,
        "caveat": ("Deterministic: assumes the expected return every year, "
                   "with no bad sequence. Treat it as the optimistic edge of "
                   "the range, not as a date."),
    }


def required_savings(profile, a, *, target_portfolio: float,
                     current_portfolio: float, retirement_age: float,
                     allocation: dict | None = None) -> float | None:
    """Annual contribution needed to hit a target by a given age.

    Solved directly. Returns None when the target is already met or when
    no finite contribution reaches it.
    """
    age = profile.age
    if age is None or retirement_age is None:
        return None
    n = retirement_age - age
    if n <= 0:
        return None
    r = mix.expected_real_return(allocation, a) / 100.0
    grown = current_portfolio * ((1 + r) ** n)
    shortfall = target_portfolio - grown
    if shortfall <= 0:
        return 0.0
    if abs(r) < 1e-9:
        return shortfall / n
    annuity_factor = (((1 + r) ** n) - 1) / r
    return shortfall / annuity_factor
