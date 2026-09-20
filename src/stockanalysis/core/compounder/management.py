"""
management.py — Step 8. Execution, measured from actions not words.
===================================================================
Step 8 asks for revenue execution, guidance accuracy, capital allocation,
dilution, insider ownership and insider buying. Two of those are honestly
unavailable here — guidance accuracy needs a history of what was promised
against what was delivered, and acquisition discipline needs deal-by-deal
returns — so they are named as unmeasured rather than proxied by something
that sounds similar.

What remains is better than it sounds, because it is all revealed
preference:

    INSIDER OWNERSHIP   how much of their own money is in the outcome
    INSIDER BUYING      open-market purchases only. A grant is
                        compensation; a purchase is a decision, and only
                        one of them tells you anything
    DILUTION            what management chose to charge shareholders for
                        the growth they produced
    CAPITAL EFFICIENCY  revenue produced per dollar of cumulative
                        reinvestment — the closest available read on
                        whether capital allocation has worked

The dilution-adjusted growth test
---------------------------------
The measure this module leans on hardest is growth NET of dilution. A
company that grew revenue 60% while issuing 35% more shares grew about 19%
per share, and per share is the only unit the owner of the stock is paid
in. Screens that ignore this systematically favour management teams that
fund growth by printing stock, which is the exact opposite of what a
10-year holder wants to select for.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import blend, inverse, scale

UNMEASURABLE = ("guidance vs actual history", "acquisition returns",
                "management tenure", "compensation structure")

# Insider ownership. Above ~15% founder-level alignment stops adding
# information and starts adding control risk, so the scale saturates there
# rather than rewarding 60% ownership as six times better than 10%.
INSIDER_LO, INSIDER_HI = 1.0, 15.0

# Per-share revenue growth, in percent per year, over the available history.
PER_SHARE_LO, PER_SHARE_HI = 0.0, 35.0


def _cagr(values, years=None) -> float | None:
    clean = [v for v in values if v is not None and v > 0]
    if len(clean) < 2:
        return None
    n = years or (len(clean) - 1)
    if n <= 0:
        return None
    return round(((clean[0] / clean[-1]) ** (1.0 / n) - 1.0) * 100.0, 1)


def compute(data: dict, growth: dict, surv: dict, reinv: dict) -> dict:
    rev = data.get("revenue_annual") or []
    shares = data.get("shares_annual") or []

    # ── Growth per share ─────────────────────────────────────────────────
    per_share = []
    for i, r in enumerate(rev):
        sh = shares[i] if i < len(shares) else None
        per_share.append(None if (r is None or not sh or sh <= 0) else r / sh)
    per_share_cagr = _cagr(per_share)
    revenue_cagr = _cagr(rev)
    dilution_cost = (None if per_share_cagr is None or revenue_cagr is None
                     else round(revenue_cagr - per_share_cagr, 1))

    insider_own = data.get("insider_own")
    buys = data.get("insider_buys")
    sells = data.get("insider_sells")
    net_value = data.get("insider_net_value")

    insider_signal = _insider_signal(buys, sells, net_value)

    # ── Capital allocation ───────────────────────────────────────────────
    # Revenue added per dollar of cumulative reinvestment over the window.
    # Crude on purpose: it asks only whether the money spent showed up as
    # business, which is the question, and it needs nothing that is not in
    # the statements.
    invested = 0.0
    have_invest = False
    for series in (data.get("capex_annual") or [], data.get("rnd_annual") or []):
        for v in series:
            if v is not None:
                invested += abs(v)
                have_invest = True
    rev_added = None
    clean_rev = [v for v in rev if v is not None]
    if len(clean_rev) >= 2:
        rev_added = clean_rev[0] - clean_rev[-1]
    capital_return = (round(rev_added / invested, 2)
                      if have_invest and invested > 0 and rev_added is not None
                      else None)

    dilution = surv.get("dilution_pct_yr")

    scored = blend([
        ("Growth per share", 30,
         None if per_share_cagr is None
         else scale(per_share_cagr, PER_SHARE_LO, PER_SHARE_HI),
         f"{per_share_cagr:+.1f}%/yr per share"
         + (f" against {revenue_cagr:+.1f}% headline"
            if revenue_cagr is not None else "")
         if per_share_cagr is not None else "no per-share history"),
        ("Dilution discipline", 25,
         None if dilution is None else inverse(dilution, 2.0, 18.0),
         f"{dilution:+.1f}%/yr share count" if dilution is not None
         else "no share history"),
        ("Insider alignment", 20,
         None if insider_own is None
         else scale(insider_own, INSIDER_LO, INSIDER_HI),
         f"insiders hold {insider_own:.1f}%" if insider_own is not None
         else "ownership not reported"),
        ("Insider transactions", 15, insider_signal["score"],
         insider_signal["detail"]),
        ("Capital allocation", 10,
         None if capital_return is None else scale(capital_return, 0.0, 2.5),
         f"${capital_return:.2f} of revenue added per $1 reinvested"
         if capital_return is not None else "no reinvestment history"),
    ])

    return {
        "score": scored["score"],
        "coverage": scored["coverage"],
        "components": scored["components"],
        "revenue_cagr": revenue_cagr,
        "per_share_cagr": per_share_cagr,
        "dilution_cost_pp": dilution_cost,
        "insider_own_pct": insider_own,
        "insider_buys": buys, "insider_sells": sells,
        "insider_net_value": net_value,
        "insider_signal": insider_signal,
        "capital_return": capital_return,
        "unmeasured": list(UNMEASURABLE),
        "detail": _detail(per_share_cagr, revenue_cagr, dilution_cost,
                          insider_own, insider_signal),
    }


def _insider_signal(buys, sells, net_value) -> dict:
    """Open-market insider activity, scored.

    Selling is treated gently on purpose. Insiders sell for houses, taxes,
    divorces and diversification, and in this universe most of an
    executive's net worth is in the stock — routine selling is close to
    meaningless. BUYING is the signal, because there is only one reason to
    do it.
    """
    if buys is None and sells is None:
        return {"score": None, "state": "UNMEASURED",
                "detail": "no insider transaction data"}
    buys, sells = buys or 0, sells or 0
    if buys == 0 and sells == 0:
        return {"score": None, "state": "QUIET",
                "detail": "no open-market insider transactions on file"}
    if buys > 0 and net_value and net_value > 0:
        return {"score": 90.0, "state": "NET BUYING",
                "detail": f"{buys} open-market purchase"
                          f"{'s' if buys != 1 else ''} against {sells} sale"
                          f"{'s' if sells != 1 else ''} — net buyers"}
    if buys > 0:
        return {"score": 65.0, "state": "MIXED",
                "detail": f"{buys} purchase{'s' if buys != 1 else ''} and "
                          f"{sells} sale{'s' if sells != 1 else ''} — buying "
                          f"is happening, selling is larger"}
    if sells >= 8:
        return {"score": 30.0, "state": "HEAVY SELLING",
                "detail": f"{sells} sales and no open-market purchases"}
    return {"score": 45.0, "state": "SELLING ONLY",
            "detail": f"{sells} sale{'s' if sells != 1 else ''}, no purchases "
                      f"— ordinary for this stage, but nobody is adding"}


def _detail(per_share, headline, cost, insider_own, signal) -> str:
    bits = []
    if per_share is not None and headline is not None:
        if cost and cost > 5:
            bits.append(f"revenue compounded {headline:.0f}%/yr but only "
                        f"{per_share:.0f}%/yr per share — {cost:.0f}pp went "
                        f"to shareholders funding it")
        else:
            bits.append(f"{per_share:.0f}%/yr per share")
    if insider_own is not None and insider_own >= 5:
        bits.append(f"insiders hold {insider_own:.1f}%")
    if signal.get("state") in ("NET BUYING", "HEAVY SELLING"):
        bits.append(signal["detail"])
    return "; ".join(bits) or "little management evidence available"
