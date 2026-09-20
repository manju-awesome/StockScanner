"""
What would have to change — as numbers, not adjectives.

"Wait for IV expansion" tells you the direction and nothing else. You
cannot put it on a watchlist, you cannot set an alert on it, and you
cannot tell tomorrow whether it has happened. The same verdict carrying
"premium >= $2.11, or IV/RV >= 1.10, or annualised >= 12%" is a
condition you can check in five seconds.

So every state that is not SELL emits a `requirements` list: the field,
where it is now, what it needs to be, and — where it can be computed —
the price or premium that would satisfy it. A rejection that names its
own trigger stops being a dead end and becomes a watchlist entry.

Each requirement is one of:

    ALTERNATIVE   any one of these is enough (premium OR vol OR yield)
    BLOCKING      must change before anything else matters

and carries `met` so the same structure renders as a checklist once the
name is re-scanned.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f


def _req(field, current, needs, kind="ALTERNATIVE", met=False,
         detail=None):
    return {"field": field, "current": current, "needs": needs,
            "kind": kind, "met": met, "detail": detail}


def premium_requirements(ret, req, adq, iv_op, ratio, chosen) -> list:
    """What would make an underpaid contract worth selling.

    Three independent routes, because they are genuinely alternatives:
    the premium can rise, implied vol can firm up (which raises the
    premium), or the same premium can become adequate on a shorter
    holding period. Listing them as alternatives rather than as one
    demand is the difference between a condition and a complaint.
    """
    out = []
    need_pct = f(req.get("period_pct"))
    have_pct = f(ret.get("yield_pct"))
    strike = f((chosen or {}).get("strike"))

    if need_pct and strike:
        # The premium that would exactly clear the hurdle, in dollars.
        need_premium = round(strike * need_pct / 100.0, 2)
        have_premium = f((chosen or {}).get("limit_price"))
        out.append(_req(
            "premium",
            None if have_premium is None else f"${have_premium:,.2f}",
            f"${need_premium:,.2f}",
            met=bool(have_premium and have_premium >= need_premium),
            detail=(f"{have_pct:.2f}% against a {need_pct:.2f}% requirement"
                    if have_pct is not None else None)))

    r = f(ratio.get("ratio"))
    if r is not None:
        out.append(_req("IV / realised", f"{r:.2f}×", "1.10×",
                        met=r >= 1.10,
                        detail="selling at or below realised vol earns no "
                               "variance premium"))

    ann = f(ret.get("annualised"))
    if ann is not None:
        out.append(_req("annualised", f"{ann:.1f}%", "12.0%",
                        met=ann >= 12.0))

    rank = (iv_op or {}).get("rank")
    if rank is not None:
        out.append(_req("IV Rank", f"{rank:.0f}", "30", met=rank >= 30))
    return out


def level_requirements(result, sel, spot) -> list:
    """The price that would bring the strike into a sellable zone.

    A name whose support sits far below spot is not a bad company — it
    is a good company at the wrong price, and the number that changes
    that is a price. Naming it turns the row into an alert.
    """
    out = []
    anchor = (sel or {}).get("anchor") or {}
    zone = f((sel or {}).get("buy_zone"))
    s = f(spot)

    if anchor.get("price") and s:
        p = f(anchor["price"])
        out.append(_req(
            "price", f"${s:,.2f}",
            f"≤ ${p * 1.03:,.2f}",
            kind="BLOCKING",
            met=s <= p * 1.03,
            detail=(f"within 3% of {anchor.get('name')} (${p:,.2f}) — "
                    f"currently {(s - p) / p * 100:+.1f}% above")))
    if zone and s:
        out.append(_req("price vs buy zone", f"${s:,.2f}",
                        f"≤ ${zone:,.2f}",
                        met=s <= zone,
                        detail="the long engine's own entry"))
    return out


def earnings_requirements(dist, clean_expiries=None,
                          wait_days=None) -> list:
    """What clears the event risk.

    Only two things do, and neither is "a later expiry". An expiry past
    the print necessarily contains it, so the alternatives are a SHORTER
    contract that closes before earnings, or waiting for the date to
    pass. Naming the wrong one would send you to sell exactly the
    contract you were trying to avoid.
    """
    out = []
    if not dist.get("inside"):
        return out

    days = f(dist.get("days"))
    if clean_expiries:
        out.append(_req(
            "expiry", f"spans earnings ({dist.get('band')})",
            ", ".join(clean_expiries[:3]),
            kind="BLOCKING", met=False,
            detail="these close before the print — shorter than the "
                   "preferred window, so check the premium still clears"))
    else:
        out.append(_req(
            "wait", f"earnings in {days:.0f}d" if days is not None
                    else "earnings inside the contract",
            (f"~{wait_days:.0f} days, until the print has passed"
             if wait_days is not None else "until earnings have passed"),
            kind="BLOCKING", met=False,
            detail="no listed expiry closes before the print, so no "
                   "contract avoids it — or set the earnings policy to "
                   "CONTROLLED to see it with its risk penalty applied"))
    return out


def liquidity_requirements(chosen, liq) -> list:
    out = []
    sp = f((chosen or {}).get("spread_pct"))
    if sp is not None:
        out.append(_req("bid/ask spread", f"{sp:.1f}%", "≤ 8.0%",
                        kind="BLOCKING", met=sp <= 8.0))
    oi = f((chosen or {}).get("open_interest"))
    if oi is not None:
        out.append(_req("open interest", f"{oi:,.0f}", "≥ 500",
                        met=oi >= 500))
    return out


def build(state, ctx) -> dict:
    """The requirement set for one verdict.

    `ctx` carries whatever the caller has: ret, req, adq, iv_op, ratio,
    chosen, sel, dist, liq, result, spot. Missing pieces simply produce
    fewer requirements rather than raising — a name blocked before the
    chain was fetched has no premium requirement to state.
    """
    reqs = []
    if state in ("WAIT_IV", "SELL_DIP", "VERIFY"):
        reqs += premium_requirements(
            ctx.get("ret") or {}, ctx.get("req") or {}, ctx.get("adq") or {},
            ctx.get("iv_op") or {}, ctx.get("ratio") or {},
            ctx.get("chosen"))
    if state in ("WAIT_LEVEL", "WATCH"):
        reqs += level_requirements(ctx.get("result") or {}, ctx.get("sel"),
                                   ctx.get("spot"))
    if state == "EVENT_RISK":
        reqs += earnings_requirements(ctx.get("dist") or {},
                                      ctx.get("clean_expiries"),
                                      ctx.get("wait_days"))
    if state in ("REJECT", "WATCH"):
        dist = ctx.get("dist") or {}
        if dist.get("inside"):
            reqs += earnings_requirements(dist, ctx.get("clean_expiries"),
                                          ctx.get("wait_days"))
        reqs += liquidity_requirements(ctx.get("chosen"), ctx.get("liq"))

    blocking = [r for r in reqs if r["kind"] == "BLOCKING" and not r["met"]]
    alts = [r for r in reqs if r["kind"] == "ALTERNATIVE"]
    satisfied = any(r["met"] for r in alts) if alts else None

    return {
        "requirements": reqs,
        "blocking": blocking,
        "any_alternative_met": satisfied,
        "summary": _summarise(reqs, blocking, alts),
    }


def _summarise(reqs, blocking, alts) -> str:
    if not reqs:
        return ""
    if blocking:
        b = blocking[0]
        return f"needs {b['field']} {b['needs']} (now {b['current']})"
    unmet = [r for r in alts if not r["met"]]
    if not unmet:
        return "conditions met — re-scan to confirm"
    return "needs " + " OR ".join(
        f"{r['field']} {r['needs']}" for r in unmet[:3])
