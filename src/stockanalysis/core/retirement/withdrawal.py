"""
How much portfolio a given level of spending requires — under four
different theories of how you will withdraw it.

Why four models instead of "multiply by 25"
--------------------------------------------
The 4% rule is one historical study: a 30-year US retirement, a 50/50
portfolio, annual inflation adjustments, no fees, no taxes, no flexibility.
Every one of those conditions is doing work, and a retirement that starts
at 55 breaks the first one immediately — a 37-year horizon is not a
30-year horizon with three extra years, it is a different problem, because
the failure mode is compounding a bad first decade over a longer tail.

So the engine reports the required portfolio under four models and lets
the spread between them BE the answer. When they cluster, the number is
robust. When Model A says $2.1M and Model D says $2.9M, the difference is
telling you the plan depends on your willingness to cut spending, and
that is a fact worth knowing before you retire, not after.

Every figure here is REAL (today's dollars) and pre-tax-treatment. What
you actually need to withdraw to SPEND a dollar depends on which account
it comes out of, which is section 9's problem and is never guessed at
here.
"""

from __future__ import annotations

# Model A rates. 4% is included because it is the reference point
# everyone knows, not because this engine endorses it for an early
# retirement.
TRADITIONAL_RATES = [4.0, 3.5, 3.0, 2.5]


def _required(spend: float, rate_pct: float) -> float:
    return spend * 100.0 / rate_pct if rate_pct else float("inf")


def model_a_traditional(annual_spend: float,
                        horizon_years: float | None = None) -> dict:
    """Fixed real withdrawal: pick a rate, spend that share of day-one
    value, inflate it forever, never look at the portfolio again.

    Its virtue is that it needs no decisions. Its flaw is the same thing:
    it keeps writing the same cheque into a 45% drawdown, which is
    precisely the behaviour that turns a bad decade into a failed plan.
    """
    rows = []
    for r in TRADITIONAL_RATES:
        rows.append({
            "rate": r,
            "required": _required(annual_spend, r),
            "note": _rate_note(r, horizon_years),
        })
    return {"model": "A — Traditional fixed withdrawal",
            "spend": annual_spend, "rows": rows,
            "headline": _required(annual_spend, 3.5)}


def _rate_note(rate: float, horizon: float | None) -> str:
    if rate == 4.0:
        base = ("The classic rule. Assumes a 30-year retirement; the "
                "study it comes from never claimed more.")
        if horizon and horizon > 32:
            base += (f" Your horizon is ~{horizon:.0f} years, so this rate "
                     f"is being used outside the evidence it rests on.")
        return base
    if rate == 3.5:
        return ("Reasonable for a 35-40 year horizon with a globally "
                "diversified portfolio and some spending flexibility.")
    if rate == 3.0:
        return ("Appropriate for a retirement of 40+ years, or for someone "
                "who cannot cut spending in a bad decade.")
    return ("Close to a perpetual-endowment rate. Survives almost any "
            "history, and costs the most years of work to reach.")


def model_b_dynamic(essential: float, lifestyle: float,
                    horizon_years: float) -> dict:
    """Withdrawals that respond to the portfolio rather than ignoring it.

    The floor is essential spending, funded at a rate conservative enough
    to survive a bad sequence. Lifestyle spending rides on top at a higher
    rate, because it is the part you can cut — and a plan is allowed to be
    aggressive precisely to the extent that it can retreat.

    This is why the spending split in the profile is load-bearing: the
    required portfolio under this model falls as the flexible share of
    spending rises, and that relationship is real, not an accounting
    trick.
    """
    floor_rate = 3.0 if horizon_years > 35 else 3.25
    flex_rate = 5.0
    floor_capital = _required(essential, floor_rate)
    flex_capital = _required(lifestyle, flex_rate)
    total = floor_capital + flex_capital
    return {
        "model": "B — Dynamic (floor + flexible)",
        "essential": essential, "lifestyle": lifestyle,
        "floor_rate": floor_rate, "flex_rate": flex_rate,
        "floor_capital": floor_capital, "flex_capital": flex_capital,
        "required": total, "headline": total,
        "note": (f"Essentials funded at {floor_rate:.2f}% so they survive a "
                 f"bad sequence; lifestyle at {flex_rate:.1f}% because it "
                 f"can be cut. Requires you to actually cut it."),
    }


def pv_annuity(payment: float, years: float, real_rate: float) -> float:
    """Capital needed today to pay `payment` a year for `years` years,
    ending at zero, earning `real_rate` in real terms.

    Ending at zero is a real assumption and a different one from the
    withdrawal-rate models above, which are sized to survive
    indefinitely. It is the right assumption for a bucket with a defined
    horizon and the wrong one if you outlive the horizon — which is why
    the planning horizon is set at the 85th survival percentile rather
    than at life expectancy.
    """
    if years <= 0:
        return 0.0
    if abs(real_rate) < 1e-9:
        return payment * years
    return payment * (1 - (1 + real_rate) ** -years) / real_rate


def model_c_bucket(essential: float, lifestyle: float, horizon_years: float,
                   equity_real_return: float = 5.0) -> dict:
    """Three buckets by time horizon, sized from spending rather than
    from a percentage allocation.

    Bucket 1 (0-3 years) exists so that a crash in year one is met by
    selling T-bills instead of equities. That is the whole mechanism:
    sequence risk is the risk of being a forced seller, and cash removes
    the forcing. It costs return in the median case and buys survival in
    the bad one.

    Bucket 3 is sized as the present value of everything after year 10,
    discounted at a rate deliberately below the equity assumption. The
    haircut is the margin for being wrong about returns; without it this
    model would quietly be the most optimistic of the four while looking
    like the most cautious.
    """
    annual = essential + lifestyle
    b1 = annual * 3
    b2 = annual * 7
    remaining_years = max(horizon_years - 10, 0)
    # Discount rate for the growth bucket: expected equity real return
    # less a 1.5pt margin of safety.
    disc = max(equity_real_return - 1.5, 0.5) / 100.0
    b3_at_year_10 = pv_annuity(annual, remaining_years, disc)
    b3 = b3_at_year_10 / ((1 + equity_real_return / 100.0) ** 10)
    total = b1 + b2 + b3
    return {
        "model": "C — Bucket strategy",
        "buckets": [
            {"name": "Bucket 1 — years 0-3", "amount": b1,
             "holds": "Cash, T-bills, short-term bonds",
             "why": "Three years of spending that never has to be sold "
                    "into a down market."},
            {"name": "Bucket 2 — years 3-10", "amount": b2,
             "holds": "Intermediate bonds, conservative income",
             "why": "Refills Bucket 1. Long enough to ride out a typical "
                    "recession without touching equities."},
            {"name": "Bucket 3 — years 10+", "amount": b3,
             "holds": "Equities and growth assets",
             "why": f"Present value of years 10-{horizon_years:.0f} of "
                    f"spending, discounted at {disc*100:.1f}% real."},
        ],
        "required": total, "headline": total,
        "depletes": True,
        "note": ("Sized from spending, not from a target allocation — the "
                 "equity share falls out of the arithmetic. Reads lower "
                 "than Models A and B because it is allowed to hit zero at "
                 f"age {horizon_years:.0f} years from retirement; those two "
                 "are sized to last indefinitely."),
    }


def model_d_guardrails(essential: float, lifestyle: float,
                       initial_rate: float = 4.25) -> dict:
    """Guyton-Klinger-style guardrails: start higher, and adjust when the
    withdrawal rate drifts out of a band.

    Starting at a higher rate is not optimism — it is the payment for
    accepting a rule that cuts spending after a bad year. The engine
    reports both the starting requirement and the cut you would have to
    absorb at the lower guardrail, because the second number is the one
    that decides whether you can actually live with this model.
    """
    annual = essential + lifestyle
    required = _required(annual, initial_rate)
    upper_trigger = initial_rate * 1.2     # portfolio fell: rate drifted up
    lower_trigger = initial_rate * 0.8     # portfolio grew: raise allowed
    cut_pct = 10.0
    cut_amount = annual * cut_pct / 100.0
    floor_after_cuts = annual - cut_amount * 2   # two consecutive cuts
    return {
        "model": "D — Guardrails",
        "initial_rate": initial_rate, "required": required,
        "headline": required,
        "upper_guardrail": upper_trigger, "lower_guardrail": lower_trigger,
        "cut_pct": cut_pct,
        "spend_after_two_cuts": floor_after_cuts,
        "essential": essential,
        "viable": floor_after_cuts >= essential,
        "note": (f"Start at {initial_rate:.2f}%. If the withdrawal rate "
                 f"rises past {upper_trigger:.2f}% (portfolio down), cut "
                 f"spending {cut_pct:.0f}%; if it falls below "
                 f"{lower_trigger:.2f}%, a raise is affordable. After two "
                 f"consecutive cuts you would be living on "
                 f"${floor_after_cuts:,.0f}."),
    }


def all_models(essential: float, lifestyle: float,
               horizon_years: float,
               equity_real_return: float = 5.0) -> dict:
    """Every model, plus the spread between them.

    The spread is reported as a first-class output rather than buried,
    because a wide spread means the plan's viability is a behavioural
    question — will you cut? — and a narrow one means it is an arithmetic
    question. Those need different kinds of preparation.
    """
    annual = essential + lifestyle
    a = model_a_traditional(annual, horizon_years)
    b = model_b_dynamic(essential, lifestyle, horizon_years)
    c = model_c_bucket(essential, lifestyle, horizon_years,
                       equity_real_return)
    d = model_d_guardrails(essential, lifestyle)

    headlines = {"A": a["headline"], "B": b["headline"],
                 "C": c["headline"], "D": d["headline"]}
    lo, hi = min(headlines.values()), max(headlines.values())
    spread_pct = 100.0 * (hi - lo) / lo if lo else None

    if not d["viable"]:
        guard_warning = ("Guardrails are not usable at your spending mix: "
                         "two consecutive cuts would take you below "
                         "essential spending. That model assumes a cushion "
                         "you do not have.")
    else:
        guard_warning = None

    return {
        "A": a, "B": b, "C": c, "D": d,
        "headlines": headlines,
        "low": lo, "high": hi, "spread_pct": spread_pct,
        "planning_number": max(b["headline"], a["rows"][1]["required"]),
        "guardrail_warning": guard_warning,
        "interpretation": _spread_note(spread_pct),
    }


def _spread_note(spread_pct: float | None) -> str:
    if spread_pct is None:
        return ""
    if spread_pct < 20:
        return ("The models agree within 20%. The target is an arithmetic "
                "question and the number is robust to how you withdraw.")
    if spread_pct < 45:
        return ("A moderate spread. Which model you follow matters, but no "
                "choice among them is catastrophic.")
    return ("A wide spread. Your required portfolio depends heavily on "
            "whether you will actually cut spending in a bad decade. Decide "
            "that now, in writing, not in the middle of a drawdown.")
