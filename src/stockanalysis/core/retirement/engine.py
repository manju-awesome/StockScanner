"""
The orchestrator: profile in, snapshot out.

Order of operations, and why it is this order
-----------------------------------------------
1. Assumptions resolve first, because everything downstream is a
   function of them and they must be identical across every module in a
   run. A balance sheet built on 5% and a Monte Carlo run on 6% would
   produce a coherent-looking page that contradicts itself.
2. The balance sheet, because the withdrawal models need to know what is
   actually available to fund retirement — after other goals take their
   capital, not before.
3. Withdrawal models and the FI ladder, which set the target.
4. Projection, which says whether the target is reachable.
5. Monte Carlo and scenarios, which say what happens when it is not
   reached smoothly.
6. The score last, because it summarises all of the above and must not
   be computed from anything the earlier steps did not actually produce.

Refuses to guess
----------------
If the profile is missing something a conclusion depends on, that
conclusion is omitted and the missing input is named. The engine returns
a `missing` list rather than filling gaps with defaults, because a
retirement number computed from invented spending is not a conservative
estimate — it is a wrong answer with a confident format.
"""

from __future__ import annotations

import datetime as _dt

from . import (assumptions as A, balance_sheet, fi, goals, mix, montecarlo,
               profile as profile_mod, projection, scenarios, score as
               score_mod, withdrawal)


def build(raw_profile: dict, *, run_monte_carlo: bool = True,
          run_scenarios: bool = True, link_brokerage: bool = True) -> dict:
    p = profile_mod.load(raw_profile)
    a = A.resolve(p.assumption_overrides)

    missing = p.missing()
    out = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "missing": missing,
        "completeness_pct": p.completeness(),
        "assumptions": A.manifest(a),
        "profile_updated_at": raw_profile.get("updated_at"),
    }

    allocation = raw_profile.get("allocation") or None
    mix_desc = mix.describe(allocation, a)
    out["mix"] = mix_desc
    real_return = mix_desc["expected_real_return"]

    balance = balance_sheet.build(p, real_return, A.value(a, "inflation"),
                                  link_brokerage=link_brokerage)
    out["balance"] = balance
    out["emergency_fund"] = goals.emergency_fund(p, balance)
    out["goal_buckets"] = goals.buckets(p, balance)

    # ── the target ──────────────────────────────────────────────────────
    ret_spend = p.retirement_spending
    out["annual_spend"] = ret_spend["total"]
    out["spend_split"] = ret_spend
    out["flexible_fraction_pct"] = round(p.flexible_fraction * 100, 1)

    age, ret_age = p.age, p.retirement_age
    horizon = A.value(a, "life_expectancy")
    if p.spouse_age is not None and age is not None:
        horizon = max(horizon, age + (A.value(a, "spouse_life_expectancy")
                                      - p.spouse_age))
    retirement_years = (horizon - ret_age) if ret_age else None
    out["retirement_age"] = ret_age
    out["horizon_age"] = round(horizon, 1)
    out["retirement_years"] = retirement_years

    if ret_spend["total"] > 0 and retirement_years:
        models = withdrawal.all_models(
            ret_spend["essential"], ret_spend["lifestyle"],
            retirement_years, real_return)
        out["withdrawal_models"] = models
        target = models["planning_number"]
    else:
        out["withdrawal_models"] = None
        target = None
    out["target_portfolio"] = target

    retirement_assets = balance["retirement_assets"]
    out["funding_pct"] = (100.0 * retirement_assets / target
                          if target else None)

    if ret_spend["total"] > 0:
        out["fi_ladder"] = fi.ladder(
            ret_spend["essential"], ret_spend["lifestyle"],
            retirement_assets, A.value(a, "safe_withdrawal_rate"),
            retirement_years, real_return)
        for rung in out["fi_ladder"]["rungs"]:
            rung["years_away"] = fi.years_to_rung(
                rung["required"], retirement_assets, p.annual_savings,
                real_return)
    else:
        out["fi_ladder"] = None

    # ── the path ────────────────────────────────────────────────────────
    can_project = age is not None and ret_age is not None \
        and ret_spend["total"] > 0
    if can_project:
        path = projection.run(p, a, retirement_age=ret_age,
                              current_portfolio=retirement_assets,
                              annual_savings=p.annual_savings,
                              allocation=allocation)
        out["projection"] = path
        out["projected_at_retirement"] = path["portfolio_at_retirement"]
        out["gap"] = ((path["portfolio_at_retirement"] - target)
                      if target else None)

        earliest = projection.earliest_safe_age(
            p, a, current_portfolio=retirement_assets,
            annual_savings=p.annual_savings, allocation=allocation)
        out["earliest_safe_age"] = earliest.get("earliest_age")
        out["earliest_caveat"] = earliest.get("caveat")

        if run_monte_carlo:
            out["timeline"] = _timeline(p, a, retirement_assets,
                                        allocation, ret_age,
                                        earliest.get("earliest_age"))

        if target:
            out["required_savings"] = projection.required_savings(
                p, a, target_portfolio=target,
                current_portfolio=retirement_assets,
                retirement_age=ret_age, allocation=allocation)
            out["savings_shortfall"] = (
                (out["required_savings"] - p.annual_savings)
                if out["required_savings"] is not None else None)
    else:
        out["projection"] = None
        out["earliest_safe_age"] = None

    # ── what happens when it is not smooth ──────────────────────────────
    mc, mc_gain = None, None
    if can_project and run_monte_carlo:
        comparison = montecarlo.compare_flexibility(
            p, a, retirement_age=ret_age,
            current_portfolio=retirement_assets,
            annual_savings=p.annual_savings, allocation=allocation)
        if not comparison.get("error"):
            mc = comparison["rigid"]
            mc_gain = comparison["gain_pts"]
            out["monte_carlo"] = mc
            out["monte_carlo_flexible"] = comparison["flexible"]
            out["flexibility_gain_pts"] = mc_gain
        else:
            out["monte_carlo"] = comparison

    if can_project and run_scenarios:
        out["scenarios"] = scenarios.run_all(
            p, a, retirement_age=ret_age,
            current_portfolio=retirement_assets,
            annual_savings=p.annual_savings, allocation=allocation)

    # ── the summary ─────────────────────────────────────────────────────
    out["score"] = score_mod.build(p, balance, mix_desc, out["funding_pct"],
                                   mc, mc_gain)
    out["savings_rate_pct"] = p.savings_rate
    out["annual_savings"] = p.annual_savings
    out["implied_savings"] = p.implied_savings
    out["actions"] = _actions(out, p, balance)
    out["risks"] = _risks(out, p, balance)
    return out


def _timeline(p, a, retirement_assets, allocation, target_age,
              deterministic_age) -> dict:
    """Four retirement ages, and the honest difference between them.

    AGGRESSIVE is the deterministic answer: the age at which the plan
    works if returns arrive on schedule every year. It is shown because
    it is the number most calculators report, and labelled because it is
    close to a coin flip in practice.

    SAFE and CONSERVATIVE are solved on survival probability instead —
    the age at which 80% and 90% of simulated sequences last the whole
    horizon. The gap between aggressive and conservative is the price of
    certainty, expressed in years of work, and it is usually the most
    useful single fact on this page.
    """
    kw = dict(current_portfolio=retirement_assets,
              annual_savings=p.annual_savings, allocation=allocation)
    safe = montecarlo.age_at_probability(p, a, target_prob=80.0, **kw)
    conservative = montecarlo.age_at_probability(p, a, target_prob=90.0, **kw)
    flexible_safe = montecarlo.age_at_probability(
        p, a, target_prob=80.0, flexible=True, **kw)

    rows = [
        {"label": "Aggressive (deterministic)", "age": deterministic_age,
         "basis": "Expected return every year, no bad sequence.",
         "confidence": "LOW"},
        {"label": "Target (yours)", "age": target_age,
         "basis": "The age you entered.", "confidence": "HIGH"},
        {"label": "Financially safe (80% survival)", "age": safe.get("age"),
         "basis": f"{safe.get('success_pct') or 0:.0f}% of simulated "
                  f"sequences last the full horizon.",
         "confidence": "MEDIUM"},
        {"label": "Conservative (90% survival)",
         "age": conservative.get("age"),
         "basis": f"{conservative.get('success_pct') or 0:.0f}% of "
                  f"simulated sequences survive.",
         "confidence": "MEDIUM"},
        {"label": "Safe with spending flexibility",
         "age": flexible_safe.get("age"),
         "basis": "80% survival, given a willingness to cut discretionary "
                  "spending when a guardrail is breached.",
         "confidence": "MEDIUM"},
    ]

    cost_of_certainty = None
    if deterministic_age and conservative.get("age"):
        cost_of_certainty = conservative["age"] - deterministic_age
    flex_saving = None
    if safe.get("age") and flexible_safe.get("age"):
        flex_saving = safe["age"] - flexible_safe["age"]

    return {
        "rows": rows,
        "cost_of_certainty_years": cost_of_certainty,
        "flexibility_saves_years": flex_saving,
        "note": ("The spread between these ages is not error — it is the "
                 "plan's sensitivity to luck. A narrow spread means the "
                 "date is robust; a wide one means it depends on the "
                 "sequence you happen to get."),
    }


def _actions(out: dict, p, balance) -> list[dict]:
    """The ranked action list.

    Every item is derived from a specific number in this snapshot and
    names it, so an action can always be traced back to the fact that
    produced it. Items are opportunities to consider and questions to
    take to a professional — they are not instructions, and anything
    touching tax treatment says who has to confirm it.
    """
    items: list[dict] = []

    ef = out.get("emergency_fund") or {}
    if ef.get("status") == "bad":
        items.append({
            "priority": "HIGH", "title": "Rebuild the emergency reserve",
            "why": (f"Liquid cash of ${ef['held_cash']:,.0f} is below the "
                    f"three-month floor of ${ef['minimum']:,.0f}. Every "
                    f"other plan on this page assumes you are never a "
                    f"forced seller."),
        })

    mc = out.get("monte_carlo") or {}
    prob = mc.get("success_pct")
    if prob is not None and prob < 70:
        items.append({
            "priority": "HIGH",
            "title": "The plan does not survive often enough as written",
            "why": (f"Monte Carlo survival is {prob:.0f}% against a "
                    f"{montecarlo.GOOD:.0f}% target. The scenario table "
                    f"prices the three fixes: retire later, save more, or "
                    f"spend less."),
        })
    elif prob is not None and prob < 80:
        items.append({
            "priority": "MEDIUM",
            "title": "Close the gap to an 80% survival rate",
            "why": (f"Survival is {prob:.0f}%. Not a crisis, but the "
                    f"cheapest points are usually in spending flexibility "
                    f"rather than in the portfolio."),
        })

    gain = out.get("flexibility_gain_pts")
    if gain and gain >= 8:
        items.append({
            "priority": "MEDIUM",
            "title": "Write down the spending cut you would actually make",
            "why": (f"Being willing to cut discretionary spending in a bad "
                    f"decade is worth {gain:+.0f} points of survival "
                    f"probability — more than any plausible allocation "
                    f"change, and free. It only works if the rule exists "
                    f"before the drawdown."),
        })

    conc = balance.get("concentration") or []
    top = conc[0] if conc else None
    if top and (top.get("weight") or 0) > 15:
        items.append({
            "priority": "HIGH" if top["weight"] > 25 else "MEDIUM",
            "title": f"Single-position concentration: {top['ticker']}",
            "why": (f"{top['ticker']} is {top['weight']:.0f}% of the "
                    f"brokerage account. This engine classifies rather than "
                    f"recommends — but a position this size means the "
                    f"retirement date is partly a bet on one company."),
        })

    for l in balance.get("liabilities") or []:
        if l["disposition"]["action"].startswith("PAY DOWN"):
            items.append({
                "priority": "HIGH" if (l.get("rate") or 0) >= 8 else "MEDIUM",
                "title": f"Debt: {l['label']} at {l['rate']:.1f}%",
                "why": l["disposition"]["reason"],
            })

    shortfall = out.get("savings_shortfall")
    if shortfall and shortfall > 0:
        items.append({
            "priority": "HIGH" if shortfall > p.annual_savings * 0.5
            else "MEDIUM",
            "title": f"Savings gap of ${shortfall:,.0f}/yr",
            "why": (f"Hitting ${out['target_portfolio']:,.0f} by age "
                    f"{out['retirement_age']:.0f} needs "
                    f"${out['required_savings']:,.0f}/yr against the "
                    f"${p.annual_savings:,.0f} going in now."),
        })

    tax = balance.get("by_tax") or {}
    financial = sum(v for k, v in tax.items() if k != "non_financial")
    if financial and tax.get("tax_free", 0) / financial < 0.05:
        items.append({
            "priority": "MEDIUM",
            "title": "No tax-free bucket to draw on later",
            "why": ("Under 5% of financial assets are in Roth or HSA "
                    "accounts. Having all three tax treatments gives you a "
                    "dial over taxable income in retirement. Whether a Roth "
                    "contribution or conversion makes sense for you is a "
                    "CPA question — this engine does not model tax rules."),
        })

    if p.is_cross_border:
        items.append({
            "priority": "HIGH",
            "title": (f"Cross-border plan: {p.current_country} → "
                      f"{p.retirement_country}"),
            "why": ("Retirement-account treatment, tax residency and "
                    "currency exposure across a border are not modelled on "
                    "this page and must not be assumed. This needs a CPA "
                    "and a CA who have both worked the treaty."),
        })

    missing = out.get("missing") or []
    if missing:
        items.append({
            "priority": "HIGH" if len(missing) > 4 else "MEDIUM",
            "title": f"{len(missing)} inputs still missing",
            "why": ("The engine leaves gaps as gaps: " +
                    "; ".join(missing[:4]) +
                    ("…" if len(missing) > 4 else "") + "."),
        })

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    items.sort(key=lambda i: order.get(i["priority"], 3))
    return items


def _risks(out: dict, p, balance) -> list[dict]:
    """The risk panel: each risk named, rated, and tied to its number."""
    rows = []

    mix_w = (out.get("mix") or {}).get("weights") or {}
    vol = (out.get("mix") or {}).get("volatility")
    rows.append({
        "risk": "Portfolio risk",
        "level": ("HIGH" if (vol or 0) > 15 else
                  "MODERATE" if (vol or 0) > 9 else "LOW"),
        "detail": (f"{mix_w.get('equity', 0):.0f}% equity, modelled "
                   f"volatility {vol:.1f}%/yr." if vol else "Not computed."),
    })

    conc = balance.get("concentration") or []
    top = max((c.get("weight") or 0 for c in conc), default=0)
    rows.append({
        "risk": "Concentration risk",
        "level": "HIGH" if top > 20 else "MODERATE" if top > 10 else "LOW",
        "detail": (f"Largest single position is {top:.0f}% of the brokerage."
                   if conc else "No brokerage positions read."),
    })

    prob = (out.get("monte_carlo") or {}).get("success_pct")
    rows.append({
        "risk": "Sequence risk",
        "level": ("HIGH" if (prob or 100) < 75 else
                  "MODERATE" if (prob or 100) < 88 else "LOW"),
        "detail": (f"Deterministic path "
                   f"{'survives' if (out.get('projection') or {}).get('survives') else 'fails'}, "
                   f"but only {prob:.0f}% of simulated sequences do."
                   if prob is not None else "Simulation not run."),
    })

    hc = p.raw.get("healthcare") or {}
    rows.append({
        "risk": "Healthcare risk",
        "level": "HIGH" if not hc else "MODERATE",
        "detail": ("No healthcare plan recorded. Medical inflation runs "
                   "above general inflation and this plan has no line for "
                   "it." if not hc else "Healthcare section populated."),
    })

    currencies = balance.get("by_currency") or {}
    non_usd = sum(v for k, v in currencies.items() if k != "USD")
    total = sum(currencies.values()) or 1
    rows.append({
        "risk": "Currency risk",
        "level": ("HIGH" if p.is_cross_border and non_usd / total < 0.2
                  else "MODERATE" if p.is_cross_border else "LOW"),
        "detail": (f"Retiring in {p.retirement_country} with "
                   f"{100*non_usd/total:.0f}% of assets outside USD."
                   if p.is_cross_border else
                   "Assets and spending are in the same currency."),
    })

    rows.append({
        "risk": "Tax risk",
        "level": "UNSCORED",
        "detail": ("This engine models no tax rules. Withdrawal figures are "
                   "gross. Treatment of retirement accounts, especially "
                   "across a border, needs a CPA/CA."),
    })
    return rows
