"""
stage.py — Step 11. Where on the curve, and what moves it forward.
==================================================================
Stage is the most useful single field this engine produces, because it is
the one that makes two very different companies with the same score
comparable. A 74 at Stage 1 and a 74 at Stage 4 are not the same
investment: the first is a small position with a wide range of outcomes,
the second is a business you can size properly. One number cannot say that;
the pair can.

    STAGE 1  DISCOVERY   the product works for someone. Revenue is small or
                         lumpy, the model is unproven, survival is a live
                         question.
    STAGE 2  VALIDATION  repeatable revenue, growth accelerating, gross
                         margin establishing. The market has said yes; the
                         economics have not finished answering.
    STAGE 3  SCALING     fast growth on real scale WITH operating leverage
                         appearing. This is where most of the compounding
                         is realised.
    STAGE 4  LEADER      large, still growing, profitable, top-3 in its
                         market.
    STAGE 5  MATURE      growth has converged toward the market's. The
                         compounding is behind it.

Classified from thresholds, not from a score
--------------------------------------------
Stage is deliberately NOT a band on the composite. If it were, it would say
nothing the score did not already say. It is a set of tests on revenue
scale, growth rate, margin state and cash generation, so a company can be
Stage 2 with an excellent score (an unusually strong early business) or
Stage 4 with a poor one (a leader losing its market). Those two cells are
where the interesting names live and a band would collapse both.

The transition, named as conditions
-----------------------------------
`next_stage` returns what specifically has to happen to advance — in the
company's own numbers, with the current value beside the threshold. "Reach
$100M of revenue with gross margin above 45%" is checkable next quarter.
"Improve execution" is not.
"""

from __future__ import annotations

STAGES = {
    1: "DISCOVERY", 2: "VALIDATION", 3: "SCALING", 4: "LEADER", 5: "MATURE",
}
STAGE_ICONS = {1: "🌱", 2: "🌿", 3: "🌳", 4: "🏛", 5: "🏚"}

# Revenue scale boundaries, in dollars. Chosen as the points where the
# question a company faces changes: below $50M the question is whether the
# product sells; past $1B it is whether the market is big enough.
R_VALIDATION = 50e6
R_SCALING = 250e6
R_LEADER = 1.5e9
# Growth rates, in percent, marking the same transitions.
G_STRONG = 25.0
G_MATURE = 12.0


def classify(data: dict, growth: dict, lev: dict, surv: dict,
             comp: dict) -> dict:
    """Stage, why, and the named conditions to advance."""
    rev = growth.get("ttm_revenue")
    if rev is None:
        clean = [v for v in (data.get("revenue_annual") or [])
                 if v is not None]
        rev = clean[0] if clean else None

    g = growth.get("latest")
    gm = lev.get("gross_margin_now")
    om = lev.get("operating_margin_now")
    fcf_state = lev.get("fcf_state")
    ratio = lev.get("leverage_ratio")
    top3 = (comp.get("top3_path") or {}).get("verdict")
    funded = surv.get("classification")

    stage, why = _stage(rev, g, gm, om, fcf_state, ratio, top3, funded)

    return {
        "stage": stage,
        "label": STAGES[stage],
        "icon": STAGE_ICONS[stage],
        "why": why,
        "revenue": rev,
        "next_stage": _next(stage, rev, g, gm, om, ratio, fcf_state, top3),
        "invalidates": _invalidates(stage, g, gm, fcf_state, funded),
    }


def _stage(rev, g, gm, om, fcf_state, ratio, top3, funded) -> tuple[int, str]:
    if rev is None:
        return 1, ("revenue could not be read — classified at the earliest "
                   "stage rather than assumed")

    # STAGE 5 first: maturity is defined by growth having converged, and a
    # large slow company must not fall through into LEADER on size alone.
    if rev >= R_LEADER and g is not None and g < G_MATURE:
        return 5, (f"${rev / 1e9:.1f}B of revenue growing {g:.0f}% — the "
                   f"growth has converged toward the market's and the "
                   f"compounding is largely behind it")

    if (rev >= R_LEADER and g is not None and g >= G_MATURE
            and (om or 0) > 0 and top3 in ("ALREADY TOP-3",)):
        return 4, (f"${rev / 1e9:.1f}B of revenue, growing {g:.0f}%, "
                   f"profitable and top-3 among tracked peers")

    if rev >= R_SCALING and g is not None and g >= G_STRONG:
        leverage = (ratio is not None and ratio >= 1.0) or \
                   fcf_state in ("INFLECTED", "COMPOUNDING", "IMPROVING")
        if leverage:
            return 3, (f"${rev / 1e6:.0f}M of revenue growing {g:.0f}% with "
                       f"operating leverage appearing — the scaling phase, "
                       f"where most of the compounding gets realised")
        return 2, (f"${rev / 1e6:.0f}M growing {g:.0f}%, but costs are still "
                   f"growing at least as fast — scale without leverage yet")

    if rev >= R_VALIDATION:
        if g is not None and g >= G_STRONG:
            return 2, (f"${rev / 1e6:.0f}M of repeatable revenue growing "
                       f"{g:.0f}% — the market has said yes; the economics "
                       f"have not finished answering")
        if g is not None and g < G_MATURE and rev >= R_SCALING:
            return 5, (f"${rev / 1e6:.0f}M growing only {g:.0f}% — this is a "
                       f"business at its natural size, not an emerging one")
        return 2, (f"${rev / 1e6:.0f}M of revenue"
                   + (f" growing {g:.0f}%" if g is not None else "")
                   + " — validated commercially, not yet scaling")

    return 1, (f"${(rev or 0) / 1e6:.1f}M of revenue"
               + (f" growing {g:.0f}%" if g is not None else "")
               + " — the product works for someone; whether it becomes a "
                 "business is still the open question")


def _next(stage, rev, g, gm, om, ratio, fcf_state, top3) -> dict:
    """What specifically advances this company one stage, with live values."""
    if stage == 1:
        conditions = [
            _cond("Revenue reaches $50M", rev, R_VALIDATION, _money),
            _cond("Growth holds above 25%", g, G_STRONG, _pct),
            _cond("Gross margin establishes above 40%", gm, 40.0, _pct),
        ]
        return {"to": "STAGE 2 — VALIDATION", "conditions": conditions,
                "summary": ("what turns discovery into validation is "
                            "repeatability: the same product sold to the "
                            "next customer at the same margin")}
    if stage == 2:
        conditions = [
            _cond("Revenue reaches $250M", rev, R_SCALING, _money),
            _cond("Growth stays above 25%", g, G_STRONG, _pct),
            _cond("Revenue outgrows operating expenses", ratio, 1.0, _ratio),
            _cond("Free cash flow inflects",
                  1.0 if fcf_state in ("INFLECTED", "COMPOUNDING",
                                       "IMPROVING") else 0.0, 1.0, _bool),
        ]
        return {"to": "STAGE 3 — SCALING", "conditions": conditions,
                "summary": ("the transition is operating leverage: the same "
                            "growth arriving with costs that grow slower "
                            "than it")}
    if stage == 3:
        conditions = [
            _cond("Revenue reaches $1.5B", rev, R_LEADER, _money),
            _cond("Operating margin turns positive", om, 0.1, _pct),
            _cond("Reaches top-3 among tracked peers",
                  1.0 if top3 == "ALREADY TOP-3" else 0.0, 1.0, _bool),
        ]
        return {"to": "STAGE 4 — LEADER", "conditions": conditions,
                "summary": ("leadership is scale plus position — growing "
                            "fast at $1.5B while nobody has passed you")}
    if stage == 4:
        return {"to": "STAGE 5 — MATURE (not a goal)", "conditions": [],
                "summary": ("the next transition is the one to avoid. A "
                            "leader becomes mature when growth converges "
                            "on the market's, and there is no way back")}
    return {"to": "—", "conditions": [],
            "summary": ("mature. The compounding this engine looks for has "
                        "already happened here")}


def _invalidates(stage, g, gm, fcf_state, funded) -> list[str]:
    """Stage-specific thesis breakers — what would say this is going wrong."""
    out = []
    if stage <= 2:
        out.append("Two consecutive quarters of decelerating revenue growth "
                   "without a named one-off cause")
        out.append("Gross margin falling while revenue grows — the product "
                   "is being sold on price")
    if stage <= 3:
        out.append("Operating expenses growing faster than revenue for a "
                   "full year — scale is not producing leverage")
    if funded in ("HIGH DILUTION RISK", "DISTRESSED"):
        out.append("A financing on dilutive terms before the cash-flow "
                   "crossing — the thesis then needs the equity window as "
                   "much as it needs the business")
    if stage >= 3:
        out.append("Growth converging toward the theme's own TAM growth — "
                   "at that point the company is taking no more share")
    if fcf_state == "WIDENING":
        out.append("The cash burn widening for another year — the crossing "
                   "moves further away every quarter it does")
    return out


# ── condition helpers ────────────────────────────────────────────────────

def _cond(label, value, threshold, fmt) -> dict:
    met = None if value is None else bool(value >= threshold)
    return {"label": label, "met": met,
            "current": "not measured" if value is None else fmt(value),
            "needed": fmt(threshold)}


def _money(v) -> str:
    if v is None:
        return "—"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    return f"${v / 1e6:.0f}M"


def _pct(v):
    return "—" if v is None else f"{v:.0f}%"


def _ratio(v):
    return "—" if v is None else f"{v:.2f}x"


def _bool(v):
    return "yes" if v and v >= 1.0 else "no"
