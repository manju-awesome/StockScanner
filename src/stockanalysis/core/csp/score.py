"""
Steps 6, 7 and 11 — premium quality, assignment analysis, and the CSP score.

The assignment test in `assignment()` is the one that decides trades. Every
other number here is a ranking aid; that one is a gate. A cash-secured put
is an obligation to buy 100 shares at the strike, so the only question that
cannot be traded away is whether the effective cost basis is a price worth
owning. `happy_to_own` is therefore computed from the company's own
valuation and support structure, never from the yield.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f, s

# TWO SCORES, NOT ONE
# ────────────────────────────────────────────────────────────────────────
# "Would I want to own this company?" and "is this particular put worth
# selling today?" are different questions with different answers, and a
# single blended number lets a superb answer to the first carry a poor
# answer to the second. That is exactly how an excellent stock becomes a
# mediocre trade: quality 98, valuation +50pp and a textbook strike
# outvote a 1.59% yield at 0.98x realised vol on a 13%-wide market, and
# the blend prints 79/100.
#
# So they are scored apart and COMBINED MULTIPLICATIVELY. A product has
# the property the sum lacks — neither factor can rescue the other, and
# a near-zero on either collapses the result.

STOCK_WEIGHTS = {
    "quality":    45,     # the business
    "valuation":  35,     # the price of the business
    "support":    20,     # the level being sold into
}

OPTION_WEIGHTS = {
    "adequacy":    35,    # premium vs the hurdle it must clear
    "iv":          25,    # variance risk premium being harvested
    "liquidity":   20,    # can this actually be filled
    "efficiency":  15,    # versus leaving the collateral in cash
    "probability":  5,    # model chance of keeping the credit
}

# Retained for the page's component breakdown of the blended view.
WEIGHTS = {**{f"stock_{k}": v for k, v in STOCK_WEIGHTS.items()},
           **{f"option_{k}": v for k, v in OPTION_WEIGHTS.items()}}

# Yields above this are almost always paying for an event, not for time.
SUSPICIOUS_ANNUALISED = 60.0
MIN_DTE_FOR_FULL_CREDIT = 18


def returns(strike, premium, dte_days) -> dict:
    """Step 6. Premium yield, annualised return, and return on risk.

    `premium` is per share (the option's quoted price), so collateral is
    strike x 100 and the premium collected is premium x 100.

    Annualising a 20-day yield to 365 days is a convention, not a
    forecast — it assumes you can repeat the trade all year at the same
    terms, which you cannot. It is reported because it is the only way
    to compare a 21-day and a 45-day contract, and flagged as a
    convention wherever the page prints it.
    """
    if strike is None or premium is None or not dte_days or dte_days <= 0:
        return {"collateral": None, "premium_total": None, "yield_pct": None,
                "annualised": None, "return_on_risk": None,
                "breakeven": None, "breakeven_pct": None}

    collateral = strike * 100.0
    total = premium * 100.0
    y = total / collateral
    # Capital actually at risk is the collateral net of the premium you
    # already hold — return on risk is therefore slightly above the raw
    # yield, and is the honest denominator once the credit is received.
    at_risk = collateral - total

    return {
        "collateral": round(collateral, 2),
        "premium_total": round(total, 2),
        "yield_pct": round(y * 100, 2),
        "annualised": round(y * (365.0 / dte_days) * 100, 1),
        "return_on_risk": round(total / at_risk * 100, 2) if at_risk > 0 else None,
        "breakeven": round(strike - premium, 2),
    }


def assignment(strike, premium, result, discount, levels) -> dict:
    """Step 7 — if assigned tomorrow, is this a price worth owning?

    Answers `happy_to_own` as True / False / None. None means the inputs
    needed to judge are missing; the engine treats None as "not clearly
    yes" and refuses the trade, because the spec's rule is that anything
    short of a clear yes is a rejection.
    """
    spot = f(result.get("price"))
    if strike is None or premium is None:
        return {"happy_to_own": None, "tests": [],
                "reason": "no priced contract"}

    basis = strike - premium
    tests, votes = [], []

    def note(ok, text):
        tests.append({"ok": ok, "text": text})
        if ok is not None:
            votes.append(ok)

    # vs spot — the discount you are locking in.
    if spot:
        d = (basis - spot) / spot * 100
        note(d <= -3, f"effective basis ${basis:,.2f} is {abs(d):.1f}% "
                      f"{'below' if d < 0 else 'ABOVE'} the ${spot:,.2f} spot")

    # vs fair value / growth gap — the valuation test.
    fair = discount.get("fair_value")
    if fair:
        d = (basis - fair) / fair * 100
        note(basis <= fair, f"basis is {abs(d):.1f}% "
                            f"{'below' if basis <= fair else 'above'} "
                            f"fair value ${fair:,.2f}")
    elif discount.get("basis") == "growth":
        gap = discount.get("growth_gap_pp")
        # A reverse DCF gives no price, so the valuation test is whether
        # the company was cheap at spot at all — buying lower only
        # improves it.
        note(gap is not None and gap <= 0,
             f"reverse DCF: {discount.get('detail')}"
             + ("" if (gap or 0) <= 0 else " — price already demands more "
                                           "growth than delivered"))

    # vs support — is the basis under a level that has held?
    if levels:
        top = levels[0]
        note(basis <= top["price"],
             f"basis is {'below' if basis <= top['price'] else 'above'} "
             f"{top['name']} (${top['price']:,.2f})")

    # vs the engine's own buy zone.
    entries = result.get("entries") or []
    zone = next((f(e.get("price")) for e in entries
                 if isinstance(e, dict) and f(e.get("price"))), None)
    if zone:
        note(basis <= zone,
             f"basis is {'inside' if basis <= zone else 'outside'} the "
             f"engine's buy zone (${zone:,.2f})")

    if not votes:
        return {"basis": round(basis, 2), "happy_to_own": None, "tests": tests,
                "reason": "not enough valuation or level data to judge"}

    passed = sum(1 for v in votes if v)
    # A clear yes means most tests agree AND the valuation test did not
    # fail outright. "Most" rather than "all": the spot test fails by
    # construction on any strike close to the money, and demanding
    # unanimity would reject every reasonable 0.25-delta sale.
    happy = passed >= max(2, (len(votes) + 1) // 2)

    return {
        "basis": round(basis, 2),
        "happy_to_own": happy,
        "tests": tests,
        "passed": passed, "total": len(votes),
        "reason": (f"{passed}/{len(votes)} assignment tests pass"),
    }


def _support_component(fit, anchor) -> float | None:
    if not fit:
        return None
    base = fit.get("score")
    if base is None:
        return None
    # A strike below a high-confidence level is worth more than the same
    # structural fit against a weak one.
    if anchor and anchor.get("confidence"):
        base = base * (0.7 + 0.3 * anchor["confidence"] / 100.0)
    return max(0.0, min(100.0, base))


def _premium_component(ret) -> float | None:
    """Yield mapped to 0-100, deliberately saturating.

    Past roughly 25% annualised the extra yield is compensation for risk
    the other components are supposed to be judging, so more stops
    scoring. This is what stops the page from ranking on premium.
    """
    a = ret.get("annualised")
    if a is None:
        return None
    return max(0.0, min(100.0, (a - 4.0) / 21.0 * 100.0))


def _weighted(parts, weights):
    """Average over the weights actually AVAILABLE.

    A missing input reduces coverage rather than scoring zero, because
    zero is a claim ("the vol is bad") and absent is not.
    """
    got = {k: v for k, v in parts.items() if v is not None}
    wsum = sum(weights[k] for k in got)
    score = (sum(v * weights[k] for k, v in got.items()) / wsum
             if wsum else None)
    return score, (round(wsum / sum(weights.values()) * 100) if wsum else 0)


def stock_score(result, discount, chosen, fit_anchor=None) -> dict:
    """"Would I want to own this company, at this level?" — 0-100.

    Deliberately blind to the option. Nothing about premium, IV or
    spread can move this number.
    """
    q = f((result.get("quality") or {}).get("score"))

    m = discount.get("margin_pct")
    if m is None:
        val_c = None
    elif discount.get("basis") == "growth":
        # Growth-gap points: 0pp is neutral, +15pp is a deep discount.
        val_c = max(0.0, min(100.0, (m + 5.0) / 20.0 * 100.0))
    else:
        val_c = max(0.0, min(100.0, (m + 5.0) / 35.0 * 100.0))

    parts = {
        "quality":   q,
        "valuation": val_c,
        "support":   _support_component((chosen or {}).get("fit"), fit_anchor),
    }
    score, coverage = _weighted(parts, STOCK_WEIGHTS)
    return {"score": None if score is None else round(score),
            "coverage": coverage, "parts": parts}


def option_score(adq, iv_op, liq, eff, chosen) -> dict:
    """"Is THIS put worth selling today?" — 0-100.

    Deliberately blind to the company. A wonderful business cannot lift
    this number, which is the entire point of separating them.
    """
    prob = (chosen or {}).get("prob_profit")
    parts = {
        "adequacy":    (adq or {}).get("score"),
        "iv":          (iv_op or {}).get("score"),
        "liquidity":   (liq or {}).get("score"),
        "efficiency":  (eff or {}).get("score"),
        "probability": None if prob is None else prob * 100.0,
    }
    score, coverage = _weighted(parts, OPTION_WEIGHTS)
    return {"score": None if score is None else round(score),
            "coverage": coverage, "parts": parts}


def combine(stock, option) -> int | None:
    """CSP Score = Stock × Option, on a 0-100 scale.

    Multiplicative, so neither factor can carry the other. A 98-quality
    company on a 35-quality contract scores 34, not the 79 a weighted
    sum produced — which is the correct reading of "excellent stock,
    mediocre option".
    """
    s, o = stock.get("score"), option.get("score")
    if s is None or o is None:
        return None
    return round(s / 100.0 * o)


def compute(result, elig, discount, chosen, ret, iv_op, liq,
            regime, assign, fit_anchor=None, adq=None, eff=None) -> dict:
    """Both scores, their product, and the hard rejections."""
    stock = stock_score(result, discount, chosen, fit_anchor)
    option = option_score(adq, iv_op, liq, eff, chosen)
    score = combine(stock, option)

    # ── Hard rejections — applied AFTER the score, so the page can show
    #    a high-scoring trade that was rejected anyway and say why.
    rejects = []
    if elig["status"] == "CSP REJECTED":
        rejects.extend(elig["blockers"])
    if assign.get("happy_to_own") is not True:
        rejects.append("would not clearly be happy owning at the effective "
                       "cost basis — " + (assign.get("reason") or ""))

    liq_score = (liq or {}).get("score")
    if (liq or {}).get("tradable") is False:
        rejects.append(
            f"{(chosen or {}).get('spread_pct') or 0:.0f}% bid/ask spread — "
            f"too wide to fill reliably")
    elif liq_score is not None and liq_score < 40:
        rejects.append(f"liquidity {liq_score}/100 — the fill is not reliable")

    if chosen and (chosen.get("fit") or {}).get("score", 0) < 40:
        rejects.append("strike sits above the acceptable buy zone")

    d = chosen.get("dte") if chosen else None
    a = ret.get("annualised")
    if a is not None and a > SUSPICIOUS_ANNUALISED:
        rejects.append(f"{a:.0f}% annualised is event pricing, not time "
                       f"premium — find the catalyst before selling this")
    if d is not None and d < MIN_DTE_FOR_FULL_CREDIT and a is not None:
        # Not a rejection; a penalty. Very short expiries annualise to
        # flattering numbers on trivial absolute premium.
        score = None if score is None else max(0, score - 8)

    def rows(parts, weights, prefix):
        return [{"name": k, "weight": weights[k], "group": prefix,
                 "score": None if parts[k] is None else round(parts[k]),
                 "available": parts[k] is not None} for k in weights]

    return {
        "score": score,
        "stock": stock,
        "option": option,
        "components": (rows(stock["parts"], STOCK_WEIGHTS, "stock")
                       + rows(option["parts"], OPTION_WEIGHTS, "option")),
        "coverage": round((stock["coverage"] + option["coverage"]) / 2),
        "rejections": rejects,
    }


# The pass marks for each half. Both must clear for a SELL — that is
# what "excellent stock AND adequate option" means operationally.
STOCK_PASS = 65
OPTION_PASS = 60


def final_action(score_info, elig, assign, no_trade_reason,
                 ret=None, iv_op=None, adq=None, liq=None,
                 blocked_on=None, earnings=None, risk=None) -> dict:
    """The six-way call — and, crucially, WHICH HALF fell short.

    🟢 SELL CSP
    🟢 SELL ON DIP                 stock excellent, entry not optimal
    🟡 WAIT FOR IV EXPANSION       stock excellent, premium too low
    🟡 WAIT FOR STOCK TO REACH LEVEL   good company, strike not attractive
    🟠 WATCH                       fundamentals fine, several things short
    🔴 REJECT                      fundamental, event, liquidity or
                                   assignment problem

    A flat "WAIT" was the weakest output of the previous design. It read
    identically whether the company was wrong, the strike was wrong, the
    spread was wrong or the vol was wrong — four different problems with
    four different resolutions, one of which resolves itself in a week
    and one of which never resolves. Routing on which score fell short,
    and on what the strike search was blocked on, makes the verdict
    actionable: "wait for IV expansion" tells you what to watch for.
    """
    if score_info["rejections"]:
        return {"action": "🔴 REJECT", "key": "REJECT",
                "why": score_info["rejections"][0]}

    stock = (score_info.get("stock") or {}).get("score")
    option = (score_info.get("option") or {}).get("score")
    stock_ok = stock is not None and stock >= STOCK_PASS

    # No contract was selectable — say what blocked the search.
    if no_trade_reason:
        if blocked_on == "liquidity":
            return {"action": "🔴 REJECT", "key": "REJECT",
                    "why": no_trade_reason}
        if blocked_on in ("level", "delta") and stock_ok:
            return {"action": "🟡 WAIT FOR STOCK TO REACH LEVEL",
                    "key": "WAIT_LEVEL", "why": no_trade_reason}
        return {"action": "🟠 WATCH", "key": "WATCH", "why": no_trade_reason}

    if stock is None or option is None:
        return {"action": "🟠 WATCH", "key": "WATCH",
                "why": "not enough data to score both halves of this trade"}

    if not stock_ok:
        return {"action": "🟠 WATCH", "key": "WATCH",
                "why": (elig["softs"][0] if elig.get("softs") else
                        f"stock score {stock}/100 — the company or its price "
                        f"is not compelling here")}

    # Event risk is its own state, not a rejection and not a premium
    # problem. A contract whose window contains an earnings print is a
    # different KIND of trade — the premium is rich precisely because
    # the gap risk is real — so it is surfaced rather than folded into
    # whichever adjacent bucket happened to fire.
    if (earnings or {}).get("inside") and (earnings or {}).get("key") in (
            "EXPOSED", "HIGH", "IMMINENT"):
        return {"action": "🟠 EVENT RISK", "key": "EVENT_RISK",
                "why": (f"{earnings['detail']} — the premium is rich "
                        f"because of it, not in spite of it")}

    # From here the STOCK is approved; only the contract is in question.
    if option >= OPTION_PASS:
        # A SELL asserts the contract is fillable at the quoted price.
        # Against a closed book that assertion cannot be made: overnight
        # quotes widen to spreads that have nothing to do with the 10am
        # market, and the premium itself is a last-trade artifact. The
        # trade may well be right — it just cannot be confirmed until the
        # open, and saying so is more useful than a green light that
        # needs re-checking anyway.
        if (liq or {}).get("quotes_live") is False:
            return {"action": "🟡 VERIFY AT OPEN", "key": "VERIFY",
                    "why": (f"stock {stock}/100 × option {option}/100 clears, "
                            f"but the market was closed — re-scan at the open "
                            f"to confirm the spread and the fill")}
        return {"action": "🟢 SELL CSP", "key": "SELL",
                "why": (f"stock {stock}/100 × option {option}/100 — quality, "
                        f"price and premium all clear")}

    # Which part of the option failed decides what you are waiting for.
    iv_weak = ((iv_op or {}).get("score") is not None
               and iv_op["score"] < 50)
    pay_weak = ((adq or {}).get("ratio") is not None
                and adq["ratio"] < 1.0)
    liq_weak = ((liq or {}).get("spread_verdict") == "CAUTION")

    if liq_weak:
        return {"action": "🟠 WATCH", "key": "WATCH",
                "why": (f"excellent stock, but a "
                        f"{(liq or {}).get('spread_pct') or 0:.0f}% bid/ask "
                        f"spread makes the fill unreliable")}
    if iv_weak or pay_weak:
        bits = []
        if iv_weak and (iv_op or {}).get("detail"):
            bits.append(iv_op["detail"])
        if pay_weak:
            bits.append(f"pays {adq['ratio']:.2f}× the required premium")
        return {"action": "🟡 WAIT FOR IV EXPANSION", "key": "WAIT_IV",
                "why": (f"excellent stock, inadequate option — "
                        + "; ".join(bits))}

    # Contract is sound but not compelling — usually the strike sitting
    # further from the level than it should.
    return {"action": "🟢 SELL ON DIP", "key": "SELL_DIP",
            "why": (f"stock {stock}/100 is approved; option {option}/100 is "
                    f"workable but not compelling — better on a pullback")}


def headline(score_info, final) -> str:
    """One line naming both verdicts, for the row summary.

    "🟡 WAIT — EXCELLENT STOCK, INADEQUATE OPTION" carries more than
    "WAIT" because it says which half to go and fix.
    """
    stock = (score_info.get("stock") or {}).get("score")
    option = (score_info.get("option") or {}).get("score")
    if stock is None or option is None:
        return final.get("why") or ""

    def band(v):
        return ("excellent" if v >= 80 else "good" if v >= 65 else
                "adequate" if v >= 50 else "weak")

    return (f"{band(stock)} stock ({stock}) · {band(option)} option "
            f"({option})").upper()
    if sc >= 70:
        return {"action": "🟢 SELL CSP NOW", "key": "SELL",
                "why": f"CSP score {sc}/100 — quality, price and premium agree"}
    if sc >= 55:
        return {"action": "🟡 WAIT", "key": "WAIT",
                "why": f"CSP score {sc}/100 — workable, but wait for a "
                       f"better strike or richer premium"}
    return {"action": "🟠 WATCHLIST", "key": "WATCHLIST",
            "why": f"CSP score {sc}/100 — not compelling here"}
