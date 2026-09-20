"""
Step 1 — is this company one to sell puts on at all?

A cash-secured put is a limit order you get paid to place. The premium is
irrelevant to whether the underlying belongs in the account: if you would
not buy the stock at the strike, the trade is a bad trade at any yield.
So this gate reads ONLY the company and its trend, and never sees an
option chain. That separation is deliberate — it makes it structurally
impossible for a fat premium to rescue a name that failed on quality.

Everything here is derived from one `longterm.engine.evaluate()` result.
The long-term engine already scores revenue and EPS growth, free cash
flow, ROE, margins, balance sheet, earnings quality, moat and
institutional sponsorship into LQuality, and already runs a reverse-DCF
valuation. Re-deriving those from raw fundamentals would produce a second
opinion that could silently disagree with the /longterm page about the
same company on the same day.

Output is one of:
    CSP APPROVED    — quality, valuation and trend all clear
    CSP WATCHLIST   — sound company, something is not yet right
    CSP REJECTED    — fails a hard rule; no strike will fix it
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f, s

# LQuality floor. Below this the business is not one to be assigned into.
MIN_QUALITY = 70
# Elite/High Quality earns a wider strike range later; this is the floor
# at which the "approved" label is available at all.
APPROVED_QUALITY = 78
# Coverage floor — a tier computed from half the inputs is not a finding.
MIN_COVERAGE = 0.60

# Stage 4 is the markdown phase. Support levels do not hold in one, so
# strike selection against support is meaningless there.
BROKEN_STAGES = ("STAGE4",)
BROKEN_TRENDS = ("BROKEN", "FAILED", "DOWNTREND")

BLOCKING_ACTIONS = ("AVOID", "THESIS BROKEN")


def _stage_broken(pullback) -> bool:
    st = (s(pullback.get("stage")) or "").upper()
    return any(st.startswith(b) for b in BROKEN_STAGES)


def classify(result: dict) -> dict:
    """{status, reasons, blockers, quality, valuation, discount} for one name.

    `reasons` are what argues FOR the trade, `blockers` what argues
    against — kept apart rather than merged into one list, because a
    name with five good reasons and one hard blocker is a rejection, and
    a single blended list hides that.
    """
    q = result.get("quality") or {}
    v = result.get("valuation") or {}
    inv = result.get("investment") or {}
    trend = result.get("trend") or {}
    pullback = result.get("pullback") or {}

    reasons, blockers, softs = [], [], []

    lq = f(q.get("score"))
    tier = s(q.get("tier"))
    coverage = f(q.get("coverage"))
    band = s(v.get("band"))
    action = (s(result.get("action")) or "").upper()

    # ── Hard blockers — no strike, expiry or premium overrides these ────

    if lq is None:
        blockers.append("no quality score — the company cannot be assessed")
    elif lq < MIN_QUALITY:
        blockers.append(f"LQuality {lq:.0f} is below the {MIN_QUALITY} floor "
                        f"— not a business to be assigned into")
    if coverage is not None and coverage < MIN_COVERAGE:
        blockers.append(f"quality built on {coverage*100:.0f}% of inputs "
                        f"— too little to stand on")
    if q.get("ownable") is False:
        blockers.append("the long-term engine does not rate this ownable")

    if action in BLOCKING_ACTIONS:
        blockers.append(f"long-term engine says {action}")

    if _stage_broken(pullback):
        blockers.append("Stage 4 markdown — support levels do not hold here")
    tstate = (s(trend.get("state")) or "").upper()
    if tstate in BROKEN_TRENDS:
        blockers.append(f"long-term trend {tstate.lower()} — the discount is "
                        f"the thesis repricing, not a sale")

    if band == "OVERVALUED" and not v.get("acceptable"):
        blockers.append(f"overvalued — {s(v.get('headline')) or 'price is demanding'}")

    price = f(result.get("price"))
    if price is not None and price < 15:
        blockers.append(f"${price:.2f} — collateral efficiency too poor "
                        f"to be worth a contract")

    # ── What argues for it ──────────────────────────────────────────────

    if lq is not None and tier:
        reasons.append(f"{s(q.get('tier_icon')) or ''} {tier} business "
                       f"— LQuality {lq:.0f}/100".strip())
    if band == "UNDERVALUED":
        reasons.append(f"Undervalued — {s(v.get('headline')) or band}")
    elif band == "FAIR":
        reasons.append(f"Fairly valued — {s(v.get('headline')) or band}")
    if s(inv.get("status")) in ("CORE", "OWN"):
        reasons.append(f"Investment view: {inv['status']} — "
                       f"{s(inv.get('why')) or 'worth owning'}")
    if tstate in ("CONFIRMED", "PARTIAL"):
        reasons.append("Long-term uptrend intact")

    # ── Soft marks — enough to hold it back, not to reject it ───────────

    if s(inv.get("status")) not in ("CORE", "OWN"):
        softs.append(f"investment view is {s(inv.get('status')) or 'unset'}, "
                     f"not CORE/OWN")
    if band == "FAIR":
        upside = f(v.get("upside_pct"))
        if upside is None or upside < 10:
            softs.append("fairly valued with little upside — the discount "
                         "is doing no work")
    if s(v.get("confidence")) == "LOW":
        softs.append("low-confidence valuation")
    if lq is not None and lq < APPROVED_QUALITY:
        softs.append(f"LQuality {lq:.0f} is sound but not high-quality")
    if pullback.get("extended"):
        softs.append("extended from support — strikes near support sit far "
                     "below spot")

    # ── Verdict ─────────────────────────────────────────────────────────

    if blockers:
        status = "CSP REJECTED"
    elif softs:
        status = "CSP WATCHLIST"
    else:
        status = "CSP APPROVED"

    return {
        "status": status,
        "icon": {"CSP APPROVED": "🟢", "CSP WATCHLIST": "🟡",
                 "CSP REJECTED": "🔴"}[status],
        "reasons": reasons,
        "blockers": blockers,
        "softs": softs,
        "quality_score": lq,
        "quality_tier": tier,
        "valuation_band": band,
        "valuation_headline": s(v.get("headline")),
        "investment_status": s(inv.get("status")),
    }


def discount_view(result: dict) -> dict:
    """How cheap is it, and measured how — the margin-of-safety reading.

    The long-term engine runs a reverse DCF as its primary method, which
    does NOT produce a fair-value price: it produces the growth rate the
    current price demands versus the growth the company has delivered.
    That is a stronger statement than a point estimate, but it means
    `fair_value` is legitimately null for most names.

    Rather than invent a fair value to fill the column, this reports
    whichever the method actually supports and names it. A run that says
    "fair value —, growth gap -8pp (reverse DCF)" is telling the truth;
    a run that back-solves a fair value from a DCF that never computed
    one is not.
    """
    v = result.get("valuation") or {}
    method = s(v.get("method"))
    price = f(result.get("price"))

    fair = f(v.get("fair_value"))
    low, high = f(v.get("fair_low")), f(v.get("fair_high"))
    upside = f(v.get("upside_pct"))
    gap = f(v.get("growth_gap_pp"))

    if fair is not None and price:
        basis = "price"
        margin = round((fair - price) / fair * 100, 1)
        detail = (f"fair value ${fair:,.2f}"
                  + (f" (${low:,.2f}–${high:,.2f})" if low and high else ""))
    elif gap is not None:
        # Negative gap = the price demands LESS growth than delivered.
        basis = "growth"
        margin = round(-gap, 1)
        detail = (f"price demands {f(v.get('implied_growth_pct')) or 0:.0f}% "
                  f"growth; delivered {f(v.get('delivered_growth_pct')) or 0:.0f}%")
    else:
        basis, margin, detail = None, None, "no valuation reading"

    return {
        "method": method,
        "basis": basis,
        "fair_value": fair, "fair_low": low, "fair_high": high,
        "upside_pct": upside,
        "growth_gap_pp": gap,
        # Percentage points of cushion, in whichever unit `basis` names.
        # NOT comparable across bases — the page labels which one it is.
        "margin_pct": margin,
        "detail": detail,
        "confidence": s(v.get("confidence")),
    }
