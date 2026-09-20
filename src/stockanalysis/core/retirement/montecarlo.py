"""
Monte Carlo: how often the plan works, not whether it works on average.

Why the deterministic path is not enough
-----------------------------------------
A projection that earns 5% every year is a plan that never happens. Real
sequences have a bad decade somewhere, and WHERE that decade falls
changes everything: identical average returns produce a comfortable
retirement if the bad years come at 75 and a failed one if they come at
56. That asymmetry — sequence-of-returns risk — is invisible in a
straight-line projection and is the single largest risk most plans carry.

What this model does
--------------------
Draws an independent real return for each year from a normal
distribution whose mean and standard deviation come from the ALLOCATION,
runs the same contribution/withdrawal schedule as the deterministic
projection, and counts how many paths still have money at the horizon.

What this model does NOT do, and where it will be wrong
---------------------------------------------------------
* Returns are drawn independently. Real markets show mild mean reversion
  over long horizons, so this slightly OVERSTATES the risk of ruin.
* Returns are normal. Real returns have fatter tails, so this
  UNDERSTATES the frequency of severe crashes. The two errors point in
  opposite directions and do not cancel in any principled way.
* Inflation is embedded in the real-return assumption rather than
  modelled separately, so an inflation shock that hits spending harder
  than returns is not represented here. Scenario 6 exists for that.
* Taxes are not modelled. Withdrawals are gross portfolio withdrawals.

A success probability is a property of a model, not a property of your
life. Read 87% as "this plan is robust under these assumptions", never
as "there is a 13% chance I go broke".
"""

from __future__ import annotations

import datetime as _dt

from . import assumptions as A, mix

STRONG, GOOD, IMPROVE, RISK = 90.0, 80.0, 70.0, 0.0


def band(prob: float | None) -> tuple[str, str]:
    if prob is None:
        return "UNKNOWN", "muted"
    if prob >= STRONG:
        return "STRONG", "good"
    if prob >= GOOD:
        return "GOOD", "good"
    if prob >= IMPROVE:
        return "NEEDS IMPROVEMENT", "watch"
    return "HIGH RISK", "bad"


def _one_off_vector(profile, start_year: int, n_years: int):
    v = [0.0] * (n_years + 1)
    for item in profile.one_offs:
        if item.amount is None or item.year is None:
            continue
        if item.funded_from != "portfolio":
            continue
        i = item.year - start_year
        if 0 <= i <= n_years:
            v[i] += item.amount
    return v


def simulate(profile, a, *, retirement_age: float, current_portfolio: float,
             annual_savings: float, allocation: dict | None = None,
             paths: int | None = None, seed: int | None = 7,
             flexible: bool = False) -> dict:
    """Run the simulation.

    `flexible=True` applies the plan's own flexibility: in any year the
    portfolio has fallen far enough that the withdrawal rate breaches a
    guardrail, discretionary spending is cut. This is not a way to make
    the number look better — it is a different plan, and it is reported
    beside the rigid one so the value of flexibility is visible as a
    number of percentage points rather than asserted.

    The seed is fixed by default so that two runs of an unchanged profile
    produce an identical probability. A success rate that wobbles by a
    point every refresh trains you to ignore it.
    """
    try:
        import numpy as np
    except ImportError:
        return {"error": "numpy is required for the Monte Carlo engine."}

    age = profile.age
    if age is None or retirement_age is None:
        return {"error": "Age and retirement age are required."}

    horizon_age = A.value(a, "life_expectancy")
    if profile.spouse_age is not None:
        horizon_age = max(horizon_age,
                          age + (A.value(a, "spouse_life_expectancy")
                                 - profile.spouse_age))
    n_years = int(round(horizon_age - age))
    if n_years <= 0:
        return {"error": "Planning horizon has already passed."}

    n_paths = int(paths or A.value(a, "monte_carlo_paths"))
    mu = mix.expected_real_return(allocation, a) / 100.0
    sigma = mix.volatility(allocation, a) / 100.0

    ret = profile.retirement_spending
    essential = ret["essential"]
    lifestyle = ret["lifestyle"]
    spend = essential + lifestyle
    salary_growth = A.rate(a, "salary_growth_real")
    start_year = _dt.date.today().year
    lumps = _one_off_vector(profile, start_year, n_years)

    rng = np.random.default_rng(seed)
    draws = rng.normal(mu, sigma, size=(n_paths, n_years))

    value = np.full(n_paths, float(current_portfolio))
    alive = np.ones(n_paths, dtype=bool)
    years_lasted = np.full(n_paths, float(n_years))
    savings = float(annual_savings)
    track = []

    for i in range(n_years):
        this_age = age + i
        working = this_age < retirement_age

        contribution = savings if working else 0.0
        if working:
            withdrawal = np.zeros(n_paths)
        elif flexible:
            # Guardrail: if this year's withdrawal would exceed 5.5% of
            # the current balance, cut discretionary spending — down to
            # the essential floor, never below it.
            desired = np.full(n_paths, spend)
            breach = value > 0
            rate_now = np.divide(desired, np.maximum(value, 1.0))
            cut = breach & (rate_now > 0.055)
            withdrawal = np.where(cut, essential, desired)
        else:
            withdrawal = np.full(n_paths, spend)

        lump = lumps[i]
        flows = contribution - withdrawal - lump
        mid = value + flows / 2.0
        value = value + flows + mid * draws[:, i]

        newly_dead = alive & (value <= 0)
        years_lasted = np.where(newly_dead, float(i), years_lasted)
        alive = alive & (value > 0)
        value = np.maximum(value, 0.0)

        if working:
            savings *= (1 + salary_growth)

        if not working:
            track.append({
                "age": round(this_age + 1, 1),
                "p10": float(np.percentile(value, 10)),
                "p50": float(np.percentile(value, 50)),
                "p90": float(np.percentile(value, 90)),
            })

    success = float(100.0 * alive.mean())
    terminal = value
    tag, status = band(success)

    return {
        "success_pct": round(success, 1),
        "band": tag, "status": status,
        "paths": n_paths,
        "horizon_age": round(horizon_age, 1),
        "years_modelled": n_years,
        "mu_pct": round(mu * 100, 2), "sigma_pct": round(sigma * 100, 2),
        "flexible": flexible,
        "terminal": {
            "p10": float(np.percentile(terminal, 10)),
            "p50": float(np.percentile(terminal, 50)),
            "p90": float(np.percentile(terminal, 90)),
            "worst": float(terminal.min()),
        },
        "median_years_lasted": float(np.median(years_lasted)),
        "failure_age_p10": (round(age + float(np.percentile(
            years_lasted[~alive], 10)), 1) if (~alive).any() else None),
        "corridor": track[::max(len(track) // 40, 1)] if track else [],
        "caveat": ("Independent normal real returns, no taxes, no inflation "
                   "shock. A model result, not a forecast."),
    }


def compare_flexibility(profile, a, **kw) -> dict:
    """The rigid plan and the flexible one, side by side.

    The gap between them is the value of being willing to cut spending —
    usually worth more than any plausible change to the asset allocation,
    and free.
    """
    rigid = simulate(profile, a, flexible=False, **kw)
    flex = simulate(profile, a, flexible=True, **kw)
    if rigid.get("error") or flex.get("error"):
        return rigid if rigid.get("error") else flex
    return {
        "rigid": rigid, "flexible": flex,
        "gain_pts": round(flex["success_pct"] - rigid["success_pct"], 1),
    }


def age_at_probability(profile, a, *, current_portfolio: float,
                       annual_savings: float, allocation: dict | None = None,
                       target_prob: float = 80.0, flexible: bool = False,
                       search_paths: int = 2500) -> dict:
    """The youngest retirement age whose survival probability clears a bar.

    This is the number section 5 actually wants, and it is different from
    the deterministic earliest age in a way that matters: the
    deterministic solve assumes the expected return in every single year,
    which is the one sequence that never happens. Retiring at the
    deterministic earliest age typically lands near a coin flip.

    Searched with a reduced path count for speed, then re-run at full
    resolution on the winner so the probability that gets REPORTED is the
    same quality as every other probability on the page.
    """
    age = profile.age
    if age is None:
        return {"error": "Age is required."}

    found = None
    for candidate in range(int(age) + 1, 81):
        r = simulate(profile, a, retirement_age=candidate,
                     current_portfolio=current_portfolio,
                     annual_savings=annual_savings, allocation=allocation,
                     paths=search_paths, flexible=flexible)
        if r.get("error"):
            return r
        if r["success_pct"] >= target_prob:
            found = candidate
            break

    if found is None:
        return {"age": None, "target_prob": target_prob,
                "note": (f"No retirement age up to 80 reaches "
                         f"{target_prob:.0f}% survival on the current "
                         f"savings and spending. The plan needs a change "
                         f"other than waiting.")}

    confirmed = simulate(profile, a, retirement_age=found,
                         current_portfolio=current_portfolio,
                         annual_savings=annual_savings,
                         allocation=allocation, flexible=flexible)
    return {"age": found, "target_prob": target_prob,
            "success_pct": confirmed.get("success_pct"),
            "flexible": flexible}
