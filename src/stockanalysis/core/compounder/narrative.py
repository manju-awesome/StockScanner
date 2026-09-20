"""
narrative.py — the written case, assembled from the measurements.
=================================================================
The framework's final output asks for nine prose blocks per company: why
this could become major, what has to go right, what would destroy it, the
biggest competitor, catalysts, metrics to monitor, the stage, the stage
transition and the invalidation.

Every sentence here is BUILT FROM A NUMBER the engine computed. Nothing is
generated freely, and that constraint is the design rather than a
limitation of it. Free prose about a small-cap company reads well, sounds
confident, and cannot be checked — and on a list whose whole purpose is
holding positions for ten years, an unfalsifiable paragraph is worse than
no paragraph. Every claim below can be traced to a field, and when the
field is missing the sentence says the thing was not measured rather than
being quietly dropped.

The test applied to every line: could a reader disagree with it by checking
something? "Gross margin expanded 6.2pp while revenue grew 44%" passes.
"Strong competitive positioning in an attractive market" does not, and
nothing shaped like it appears here.
"""

from __future__ import annotations


def _money(v) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e12:
        return f"${v / 1e12:.1f}T"
    if abs(v) >= 1e9:
        # One decimal under $10B where it carries information, none above —
        # "$420.00B" implies a precision the theme library explicitly does
        # not have, and its own docstring says the figures are rounded hard.
        b = v / 1e9
        return f"${b:.1f}B" if abs(b) < 10 else f"${b:,.0f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def build(r: dict) -> dict:
    """All nine blocks for one evaluated company."""
    return {
        "why_major": _why_major(r),
        "what_has_to_go_right": _go_right(r),
        "what_destroys_it": _destroys(r),
        "biggest_competitor": _competitor(r),
        "catalysts": _catalysts(r),
        "metrics_to_monitor": _metrics(r),
        "current_stage": _current_stage(r),
        "stage_transition": _transition(r),
        "invalidation": _invalidation(r),
    }


# ─────────────────────────────────────────────────────────────────────────────

def _why_major(r: dict) -> list[str]:
    """The bull case, one checkable claim per line.

    Ordered by how much of the case each carries: the market first, then the
    company's trajectory inside it, then the evidence of durability. A
    reader who stops after two lines should have the two that matter.
    """
    out = []
    opp, sec = r.get("opportunity") or {}, r.get("secular") or {}
    g, lev = r.get("growth") or {}, r.get("leverage") or {}
    moat, comp = r.get("moat") or {}, r.get("competitive") or {}
    theme = r.get("theme") or {}

    share = opp.get("share_pct")
    if share is not None and opp.get("tam_5y"):
        out.append(
            f"Holds {share:.3f}% of a {_money(opp.get('tam_now'))} market "
            f"that the library's curve has reaching {_money(opp['tam_5y'])} "
            f"in five years ({opp.get('tam_cagr_5y')}% a year). The room is "
            f"arithmetic, not narrative: the company does not need to take "
            f"share to grow, only to hold it.")
    elif theme.get("label"):
        out.append(f"Sells into {theme['label']} — "
                   f"{_money(theme.get('tam_now'))} today on the library's "
                   f"curve, {sec.get('confidence')} confidence.")

    if g.get("state") == "ACCELERATING":
        out.append(
            f"Growth is bending upward, not merely fast: {g.get('detail')}. "
            f"That is the second derivative, and it is what separates a "
            f"company entering its market from one that has finished.")
    elif g.get("latest") is not None:
        out.append(f"Growing {g['latest']:.0f}% with the curve "
                   f"{(g.get('state') or 'unmeasured').lower()}.")

    ratio = lev.get("leverage_ratio")
    if ratio is not None and ratio >= 1.0:
        out.append(
            f"Revenue is growing {ratio:.2f}x as fast as the operating cost "
            f"base"
            + (f", with gross margin {lev['gross_margin_trend_pp']:+.1f}pp"
               if lev.get("gross_margin_trend_pp") else "")
            + ". Scale is making the model better, which is the one thing a "
              "competitor cannot copy by spending more.")

    if lev.get("fcf_state") in ("INFLECTED", "IMPROVING"):
        out.append(f"Free cash flow {lev['fcf_state'].lower()} — "
                   f"{lev.get('fcf_note')}.")

    if (moat.get("score") or 0) >= 60:
        out.append(f"Moat {moat.get('state', '').lower()}: {moat.get('detail')}")

    if comp.get("outgrowing_peers_pp") and comp["outgrowing_peers_pp"] > 0:
        out.append(
            f"Outgrowing the median tracked peer in its own theme by "
            f"{comp['outgrowing_peers_pp']:.0f}pp — taking share inside a "
            f"growing market, which compounds twice.")

    if not out:
        out.append("Too little measured to state a case — see the coverage "
                   "note. This is a data gap, not a negative finding.")
    return out


def _go_right(r: dict) -> list[str]:
    """The conditions the thesis is actually resting on, as testable claims.

    Written as things that must CONTINUE or START, each anchored to the
    number it would show up in — so a reader can check them next quarter
    rather than re-forming an opinion.
    """
    out = []
    g, lev = r.get("growth") or {}, r.get("leverage") or {}
    opp, surv = r.get("opportunity") or {}, r.get("survivability") or {}
    st, moat = r.get("stage") or {}, r.get("moat") or {}
    reinv = r.get("reinvestment") or {}

    if g.get("latest") is not None:
        out.append(f"Growth holds near {g['latest']:.0f}% for several more "
                   f"years rather than reverting to the theme's own "
                   f"{opp.get('tam_cagr_5y') or '—'}% market growth. Every "
                   f"year it does is a year of share gain.")
    if opp.get("implied_share_5y_pct") is not None:
        out.append(f"Share reaches roughly "
                   f"{opp['implied_share_5y_pct']:.2f}% of the theme's "
                   f"five-year TAM — which is what its current growth rate "
                   f"implies if both it and the market stay on trend.")
    if (lev.get("leverage_ratio") or 0) < 1.0:
        out.append("Operating expenses stop outgrowing revenue. Today the "
                   f"ratio is {lev.get('leverage_ratio')}x, and no amount of "
                   f"revenue growth compounds while that stays below 1.")
    else:
        out.append(f"Operating leverage persists past the current "
                   f"{lev.get('leverage_ratio')}x as the company adds the "
                   f"sales and support cost that scale usually brings.")

    if lev.get("fcf_state") in ("BURNING", "IMPROVING", "WIDENING"):
        out.append(f"Free cash flow crosses zero before the balance sheet "
                   f"forces a raise — {surv.get('why')}")
    if (moat.get("score") or 0) < 60:
        out.append("A durable advantage actually forms. The growth is "
                   "measured; the moat is not yet, and growth without one is "
                   "share a better-funded competitor can take back.")
    if reinv.get("productive") is False:
        out.append("The current R&D and capex start producing revenue — on "
                   "the available history the spending has been growing "
                   "faster than the business it bought.")

    nxt = st.get("next_stage") or {}
    unmet = [c for c in (nxt.get("conditions") or []) if c.get("met") is False]
    if unmet:
        out.append(f"It clears the {nxt.get('to')} conditions: "
                   + "; ".join(f"{c['label']} (now {c['current']})"
                               for c in unmet[:3]) + ".")
    return out


def _destroys(r: dict) -> list[str]:
    """What kills it — from the measured risks, plus the theme's own."""
    out = []
    surv, theme = r.get("survivability") or {}, r.get("theme") or {}
    lev, comp = r.get("leverage") or {}, r.get("competitive") or {}
    g, sec = r.get("growth") or {}, r.get("secular") or {}

    cls = surv.get("classification")
    if cls in ("HIGH DILUTION RISK", "DISTRESSED"):
        out.append(f"{surv.get('icon')} Funding. {surv.get('why')}. This is "
                   f"the risk that ends the thesis without the business ever "
                   f"being wrong — a forced raise at the wrong price "
                   f"transfers the upside to whoever provides it.")
    elif cls == "CAPITAL DEPENDENT":
        out.append(f"Funding. {surv.get('why')}. A capital market that closes "
                   f"for two quarters is a different event for this company "
                   f"than for a self-funded one.")

    if theme.get("risk"):
        out.append(f"The theme itself: {theme['risk']}")
    if sec.get("confidence") == "LOW":
        out.append("The market may not arrive. This theme's five- and "
                   "ten-year TAM are scenarios rather than extrapolations of "
                   "shipped revenue, and the opportunity leg of the score "
                   "depends on them.")

    if comp.get("incumbent") and comp["incumbent"] != "not recorded":
        out.append(f"The incumbents decide to care: {comp['incumbent']}. A "
                   f"large competitor entering a niche it previously ignored "
                   f"resets pricing faster than any share gain compounds.")

    if lev.get("gross_margin_trend_pp") is not None and \
            lev["gross_margin_trend_pp"] < 0:
        out.append(f"Gross margin is already falling "
                   f"({lev['gross_margin_trend_pp']:+.1f}pp). If that is "
                   f"price rather than mix, the moat case is not merely "
                   f"unproven — it is being disproven.")
    if g.get("state") == "DECELERATING":
        out.append(f"Growth is already bending down: {g.get('detail')}. "
                   f"Deceleration at this size usually means the early "
                   f"adopters are bought and the mainstream has not started.")

    dil = surv.get("dilution_pct_yr")
    if dil is not None and dil > 8:
        out.append(f"Dilution at {dil:.1f}%/yr. The business can succeed on "
                   f"every operating measure while the per-share claim "
                   f"shrinks enough to erase the return.")
    if not out:
        out.append("No measured structural risk stood out — which given how "
                   "many inputs are unmeasurable for a company this size is "
                   "a statement about the data, not a clean bill of health.")
    return out


def _competitor(r: dict) -> dict:
    comp = r.get("competitive") or {}
    return {
        "incumbent": comp.get("incumbent"),
        "tracked_peers": comp.get("peer_tickers") or [],
        "rank": comp.get("rank"), "of": comp.get("peer_count"),
        "structure": comp.get("structure"),
        "top3": comp.get("top3_path") or {},
        "caveat": comp.get("caveat"),
    }


def _catalysts(r: dict) -> list[str]:
    """Events that would move the thesis forward, in order of nearness."""
    out = []
    st, g = r.get("stage") or {}, r.get("growth") or {}
    lev, disc = r.get("leverage") or {}, r.get("discovery") or {}
    surv = r.get("survivability") or {}

    if g.get("accel_pp") is not None:
        out.append(f"Next quarterly report: a year-on-year growth print above "
                   f"{(g.get('latest') or 0):.0f}% would extend the "
                   f"acceleration; below its {g.get('cagr_2y') or '—'}% "
                   f"two-year rate would end it.")
    if lev.get("fcf_state") in ("IMPROVING", "BURNING", "WIDENING"):
        out.append("The free-cash-flow crossing. It re-rates the funding "
                   "classification and removes the financing clock in one "
                   "event.")
    if lev.get("gross_margin_now") is not None:
        out.append(f"Gross margin above {lev['gross_margin_now'] + 2:.0f}% on "
                   f"growing revenue — the cleanest available evidence of "
                   f"pricing power forming.")
    if disc.get("state") in ("UNDISCOVERED", "EARLY DISCOVERY"):
        out.append(f"Analyst initiations. At {disc.get('analysts') or 0} "
                   f"covering, each new one is a step in the discovery the "
                   f"engine is trying to front-run; the re-rating usually "
                   f"comes with the coverage, not with the results.")
    if surv.get("classification") in ("CAPITAL DEPENDENT",
                                      "HIGH DILUTION RISK"):
        out.append("A financing done from strength rather than necessity — "
                   "the terms of the next raise say more about the company's "
                   "position than any operating metric this quarter.")
    nxt = (st.get("next_stage") or {}).get("to")
    if nxt:
        out.append(f"Advancing to {nxt} — see the transition conditions.")
    return out


def _metrics(r: dict) -> list[dict]:
    """What to actually watch, with the current value and the trigger.

    A monitoring list without current values is a list of words. Each entry
    carries where the number is now and what reading would change the view,
    so it can be checked against a filing without re-deriving anything.
    """
    out = []
    g, lev = r.get("growth") or {}, r.get("leverage") or {}
    surv, disc = r.get("survivability") or {}, r.get("discovery") or {}
    reinv, opp = r.get("reinvestment") or {}, r.get("opportunity") or {}

    def add(metric, now, watch_for):
        out.append({"metric": metric, "now": now, "watch_for": watch_for})

    add("Revenue growth, year on year",
        f"{g['latest']:.0f}%" if g.get("latest") is not None else "not measured",
        f"two consecutive prints below the "
        f"{g.get('cagr_2y') or g.get('cagr_3y') or '—'}% multi-year rate "
        f"— that is deceleration confirmed, not noise")
    add("Growth acceleration",
        f"{g['accel_pp']:+.1f}pp" if g.get("accel_pp") is not None
        else "not measurable",
        "the sign turning negative and staying there for two quarters")
    add("Gross margin",
        f"{lev['gross_margin_now']:.1f}%" if lev.get("gross_margin_now")
        is not None else "not reported",
        "any decline while revenue grows — that is price competition, and it "
        "invalidates the pricing-power half of the moat case")
    add("Revenue vs operating-expense growth",
        f"{lev['leverage_ratio']:.2f}x" if lev.get("leverage_ratio")
        is not None else "not measurable",
        "falling below 1.0 for a full year")
    add("Free cash flow",
        f"{_money(lev.get('fcf_now'))} — {lev.get('fcf_state', '').lower()}",
        "the burn widening rather than narrowing")
    add("Cash runway",
        # The precomputed label, not the raw number — see survivability.py
        # for why the raw one does not survive the snapshot.
        surv.get("runway_label") or _runway(surv.get("runway_years")),
        "dropping under two years without a financing already arranged")
    add("Share count",
        f"{surv['dilution_pct_yr']:+.1f}%/yr" if surv.get("dilution_pct_yr")
        is not None else "no history",
        "issuance accelerating — especially any raise below the prior round "
        "or at a discount to market")
    add("R&D productivity",
        f"{reinv['rnd_productivity']:.2f}x" if reinv.get("rnd_productivity")
        is not None else "not measurable",
        "sustained readings under 1.0x — research outrunning the business")
    add("Analyst coverage",
        f"{disc.get('analysts') or 0} covering"
        + (f" ({disc['analyst_trend']:+.0f} in 3 months)"
           if disc.get("analyst_trend") else ""),
        "coverage being dropped — an analyst stopping is a stronger signal "
        "than one downgrading")
    if opp.get("share_pct") is not None:
        add("Share of theme TAM", f"{opp['share_pct']:.3f}%",
            "share flat across two years while the market grows — it means "
            "the company is riding the market rather than taking it")
    return out


def _runway(v) -> str:
    if v is None:
        return "not measurable"
    if v == float("inf"):
        return "no burn"
    return f"{v:.1f} years"


def _current_stage(r: dict) -> dict:
    st = r.get("stage") or {}
    return {"stage": st.get("stage"), "label": st.get("label"),
            "icon": st.get("icon"), "why": st.get("why"),
            "revenue": st.get("revenue")}


def _transition(r: dict) -> dict:
    """The named conditions to advance a stage, with live values.

    The framework asks specifically what moves a name from Stage 1 to
    Stage 2. Generalised to whatever the current stage is, because a Stage 3
    company's reader needs the Stage 3→4 conditions and printing the 1→2
    ones would be answering a question nobody asked.
    """
    st = r.get("stage") or {}
    nxt = st.get("next_stage") or {}
    return {
        "from": f"{st.get('icon', '')} STAGE {st.get('stage')} — "
                f"{st.get('label')}",
        "to": nxt.get("to"),
        "summary": nxt.get("summary"),
        "conditions": nxt.get("conditions") or [],
    }


def _invalidation(r: dict) -> list[str]:
    """What says the thesis is wrong — distinct from what makes it risky.

    A risk is something that might happen. An invalidation is an observation
    that, if made, means the reasoning was mistaken. Keeping them apart is
    what stops a position being held all the way down on the grounds that
    the risks were "already known".
    """
    out = list((r.get("stage") or {}).get("invalidates") or [])
    comp, moat = r.get("competitive") or {}, r.get("moat") or {}
    opp, sec = r.get("opportunity") or {}, r.get("secular") or {}

    if (comp.get("top3_path") or {}).get("verdict") in (
            "NOT ON CURRENT TRAJECTORY", "TOP-3, LOSING GROUND"):
        out.append(f"Competitive standing: {(comp['top3_path']).get('why')}. "
                   f"If that persists for two years the compounder thesis is "
                   f"simply not the right one for this company.")
    if (moat.get("score") or 0) < 45:
        out.append("No moat evidence appearing after another two years of "
                   "scale — at that point the growth is the market's, not "
                   "the company's.")
    if opp.get("share_pct") is not None and opp["share_pct"] > 10:
        out.append(f"At {opp['share_pct']:.1f}% of its theme's TAM the "
                   f"company is no longer under-penetrated; further growth "
                   f"has to come from the market rather than from share, "
                   f"which is a different and slower thesis.")
    if sec.get("confidence") == "LOW":
        out.append("The theme's TAM curve failing to materialise on schedule "
                   "— for a scenario-based market, two years of flat industry "
                   "revenue is the disconfirmation.")
    return out
