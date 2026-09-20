"""
The eight stress tests — the same plan, run through eight different
histories.

Method
------
Every scenario is a real-return series handed to the same deterministic
projection, plus in two cases a spending multiplier. Nothing else
changes: same spending, same contributions, same one-offs, same horizon.
Keeping the engine fixed and varying only the world is what makes the
results comparable to each other and to the base case.

Where the shocks are placed matters more than their size
----------------------------------------------------------
A 45% crash at 40 is a buying opportunity; the same crash at 55 with no
salary coming in can end a plan. So the crash scenarios are deliberately
anchored to the retirement date rather than to a calendar year. Two of
them (4 and 8) are the same magnitude of loss at different times, and
the difference between their outcomes IS sequence risk, made visible.

Each scenario reports what it would take to fix it: the spending cut, or
the extra capital. A stress test that only says "this fails" is half a
test.
"""

from __future__ import annotations

from . import mix, projection

SCENARIOS = [
    ("bull", "Bull market", "Strong equity returns throughout."),
    ("normal", "Normal market", "The plan's own expected returns."),
    ("lost_decade", "Lost decade",
     "Ten years of roughly zero real return, starting at retirement."),
    ("crash_2008", "2008-style crash",
     "A 45% drawdown five years before retirement, then recovery."),
    ("crash_2020", "2020-style crash",
     "A 34% drawdown and a fast, complete recovery."),
    ("high_inflation", "High inflation",
     "Inflation runs hot for a decade: real returns compress and real "
     "spending rises."),
    ("stagflation", "Low returns + high inflation",
     "The worst combination — depressed real returns for the whole "
     "horizon."),
    ("early_crash", "Crash immediately after retiring",
     "A 35% drawdown in the first year of retirement, then recovery."),
]


def _series(n: int, base: float, edits: dict[int, float]) -> list[float]:
    out = [base] * n
    for i, r in edits.items():
        if 0 <= i < n:
            out[i] = r
    return out


def build_series(name: str, n_years: int, base: float,
                 years_to_retirement: int) -> tuple[list[float], float]:
    """The return series and spending multiplier for one scenario."""
    ytr = max(int(years_to_retirement), 0)

    if name == "bull":
        return [base + 0.03] * n_years, 1.0

    if name == "normal":
        return [base] * n_years, 1.0

    if name == "lost_decade":
        edits = {i: 0.0 for i in range(ytr, min(ytr + 10, n_years))}
        return _series(n_years, base, edits), 1.0

    if name == "crash_2008":
        # Five years before retirement, when the balance is near its peak
        # and there is still salary coming in to buy the recovery.
        hit = max(ytr - 5, 0)
        edits = {hit: -0.45, hit + 1: 0.22, hit + 2: 0.18, hit + 3: 0.14}
        return _series(n_years, base, edits), 1.0

    if name == "crash_2020":
        hit = max(ytr - 1, 0)
        edits = {hit: -0.34, hit + 1: 0.40}
        return _series(n_years, base, edits), 1.0

    if name == "high_inflation":
        # Real returns compress for a decade; real spending rises because
        # the goods a retiree buys most — healthcare, food, energy —
        # inflate faster than the index used to deflate returns.
        edits = {i: base - 0.03 for i in range(0, min(10, n_years))}
        return _series(n_years, base, edits), 1.08

    if name == "stagflation":
        return [base - 0.035] * n_years, 1.10

    if name == "early_crash":
        edits = {ytr: -0.35, ytr + 1: 0.15, ytr + 2: 0.15, ytr + 3: 0.12}
        return _series(n_years, base, edits), 1.0

    return [base] * n_years, 1.0


def _fix_by_cutting(profile, a, *, base_kwargs, returns, multiplier) -> dict:
    """The smallest spending cut that makes a failing scenario survive.

    Searched in 2.5% steps down to a 40% cut, and reported against
    essential spending — a "fix" that requires cutting below essentials
    is not a fix, and the engine says so instead of returning a number
    that looks like a solution.
    """
    ret = profile.retirement_spending
    essential_share = (ret["essential"] / ret["total"]) if ret["total"] else 1.0
    step = 0.025
    cut = 0.0
    while cut < 0.40:
        cut += step
        m = multiplier * (1 - cut)
        path = projection.run(profile, a, returns=returns,
                              spend_multiplier=m, **base_kwargs)
        if path["survives"]:
            below_essential = m < essential_share
            return {"cut_pct": round(cut * 100, 1),
                    "below_essential": below_essential,
                    "viable": not below_essential}
    return {"cut_pct": None, "below_essential": True, "viable": False}


def _fix_by_capital(profile, a, *, base_kwargs, returns, multiplier,
                    current_portfolio) -> float | None:
    """Extra capital needed today to survive the scenario.

    Bisection on starting portfolio. Reported in today's dollars because
    that is the form in which the decision gets made — save more, work
    longer, or spend less.
    """
    lo, hi = current_portfolio, current_portfolio * 8 + 1_000_000
    kw = dict(base_kwargs)
    kw.pop("current_portfolio", None)
    for _ in range(40):
        mid = (lo + hi) / 2
        path = projection.run(profile, a, returns=returns,
                              spend_multiplier=multiplier,
                              current_portfolio=mid, **kw)
        if path["survives"]:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1000:
            break
    extra = hi - current_portfolio
    return extra if extra > 0 else 0.0


def run_all(profile, a, *, retirement_age: float, current_portfolio: float,
            annual_savings: float, allocation: dict | None = None) -> dict:
    """Every scenario, with its outcome and its remedy."""
    base_path = projection.run(
        profile, a, retirement_age=retirement_age,
        current_portfolio=current_portfolio, annual_savings=annual_savings,
        allocation=allocation)
    if base_path.get("error"):
        return base_path

    n_years = len(base_path["rows"])
    base_return = mix.expected_real_return(allocation, a) / 100.0
    ytr = int(round(base_path["years_to_retirement"]))
    spend = base_path["annual_spend"]

    base_kwargs = dict(retirement_age=retirement_age,
                       current_portfolio=current_portfolio,
                       annual_savings=annual_savings, allocation=allocation)

    rows = []
    for key, title, blurb in SCENARIOS:
        returns, mult = build_series(key, n_years, base_return, ytr)
        path = projection.run(profile, a, returns=returns,
                              spend_multiplier=mult, **base_kwargs)
        at_ret = path["portfolio_at_retirement"]
        wr = (100.0 * spend * mult / at_ret) if at_ret else None

        row = {
            "key": key, "title": title, "blurb": blurb,
            "portfolio_at_retirement": at_ret,
            "withdrawal_rate": wr,
            "final_value": path["final_value"],
            "survives": path["survives"],
            "depleted_age": path["depleted_age"],
            "years_lasted": (path["depleted_age"] - retirement_age
                             if path["depleted_age"] else
                             path["horizon_age"] - retirement_age),
            "spend_multiplier": mult,
        }
        if not path["survives"]:
            row["fix_cut"] = _fix_by_cutting(
                profile, a, base_kwargs=base_kwargs, returns=returns,
                multiplier=mult)
            row["fix_capital"] = _fix_by_capital(
                profile, a, base_kwargs=base_kwargs, returns=returns,
                multiplier=mult, current_portfolio=current_portfolio)
        rows.append(row)

    failures = [r for r in rows if not r["survives"]]
    return {
        "scenarios": rows,
        "base": base_path,
        "failures": len(failures),
        "survived": len(rows) - len(failures),
        "worst": min(rows, key=lambda r: r["final_value"])["title"],
        "verdict": _verdict(rows),
    }


def _verdict(rows: list[dict]) -> str:
    failed = [r["title"] for r in rows if not r["survives"]]
    if not failed:
        return ("The plan survives all eight histories. That is a strong "
                "result — and it is a deterministic one, so read it "
                "alongside the Monte Carlo probability, not instead of it.")
    if len(failed) <= 2:
        return (f"Fails under: {', '.join(failed)}. A plan that survives "
                f"most histories but not the worst ones is usually fixed by "
                f"flexibility rather than by more capital — check the "
                f"spending cut each failure requires.")
    return (f"Fails under {len(failed)} of eight histories "
            f"({', '.join(failed)}). This is a structural gap, not bad "
            f"luck: the plan needs more capital, later retirement or "
            f"lower spending, and the scenario table prices each.")
