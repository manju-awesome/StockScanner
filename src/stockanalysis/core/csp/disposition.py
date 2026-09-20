"""
disposition.py — what to DO about a name, not whether to sell its put
======================================================================
The rest of this package answers "is this put worth selling today". That
question has a rejection for an answer roughly nine times in ten, and a
rejection is not a decision — it is the absence of one. NVDA comes back
REJECTED on valuation while being an Elite business 10% above the price
you would happily own it at, and "REJECT" says none of that.

So this module asks the capital-allocation question instead:

    Which companies would I happily own, at what price, and can the
    options market pay me to wait for that price?

and answers it in five states rather than two.

The one number this is built around
------------------------------------
    effective_entry = strike - credit
    entry_vs_zone   = effective_entry - buy_zone_high

Assignment does not put you in at the strike; it puts you in at the strike
less the premium you were paid. Measured against the price you actually
wanted to own the stock at, that single subtraction reorders the whole
table — and it reorders it AGAINST the biggest yields, which is the point.

CRDO is the case that makes it concrete. Annualised 121%, which is the
most seductive number on the page. Strike $240, credit $24.76, so the
effective entry is $215.24 — against a buy zone of $168-177. The option
market is offering to pay you handsomely to buy a stock 22% above the
price you said you wanted it at. The yield is real and the trade is still
wrong, and no amount of premium fixes it.

What this module will NOT do
-----------------------------
It never overrides the gate. `eligibility.classify()` decides what may be
offered as a trade; nothing here can turn a quality failure into a
SELL_CSP, because letting a premium rescue a bad business is the exact
failure the two-score split and the gate ordering exist to prevent. What
changes is that a rejection now carries a DISPOSITION — wait for the
price, wait for a better strike, check the thesis, or genuinely avoid —
instead of stopping at "no".

Why the score is not called csp_score
--------------------------------------
`engine` already produces one (stock x option x risk), and it answers "is
this contract worth selling". This one answers "would I want to own this
company at this effective price". Two different questions have to have two
different names, or the disagreement between them becomes invisible at the
call site — the same rule `core.longterm` follows with LQuality.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f, s

# ── The five states ──────────────────────────────────────────────────────
# Ordered best-first for ranking. These are capital-allocation verdicts,
# not option verdicts: BUY_NOW is advice to skip the option entirely.
ACTIONS = ("BUY_NOW", "SELL_CSP", "WAIT_FOR_BETTER_STRIKE",
           "WAIT_FOR_BUY_ZONE", "THESIS_CHECK", "AVOID")

ACTION_LABEL = {
    "BUY_NOW": "🟢 BUY SHARES",
    "SELL_CSP": "🟢 SELL CSP",
    "WAIT_FOR_BETTER_STRIKE": "🟡 WAIT · better strike",
    "WAIT_FOR_BUY_ZONE": "🟡 WAIT · for the price",
    "THESIS_CHECK": "🟠 THESIS CHECK",
    "AVOID": "🔴 AVOID",
}
ACTION_RANK = {a: len(ACTIONS) - i for i, a in enumerate(ACTIONS)}

# Quality floors. MIN mirrors eligibility.MIN_QUALITY — a business below it
# is never a CSP underlying whatever the chart or the premium says.
MIN_QUALITY = 70
OWNABLE_QUALITY = 80
ACCUMULATE_QUALITY = 85

# How close to the buy zone still counts as "worth watching" rather than
# "come back in a year". Beyond this the wait is not actionable and the
# name is better read as a watchlist entry than a pending trade.
WATCHABLE_GAP_PCT = 15.0

# The weights the ranking uses. Ownership dominates on purpose: this is a
# capital-allocation score, and the premium is the smallest term in it.
WEIGHTS = {
    "ownership": 35,      # would I want to own this business at all
    "entry": 25,          # does the effective entry land in the buy zone
    "valuation": 15,      # is the price defensible
    "technical": 10,      # is the structure intact
    "premium": 10,        # is the option paid adequately
    "liquidity": 5,       # can it be filled
}


def effective_entry(strike, credit):
    """Strike less the premium — what assignment actually costs you.

    The single most under-used number in a CSP table: every yield on the
    page is computed from the credit, and this is the only figure that
    says what you would end up owning and at what price.
    """
    k, c = f(strike), f(credit)
    if k is None:
        return None
    return round(k - (c or 0.0), 2)


def _band(value, points, lo, hi):
    """Linear score between two bounds, clamped. `lo` scores 0, `hi` full."""
    v = f(value)
    if v is None or hi == lo:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) * points


def _entry_score(entry_gap_pct, points):
    """How well the effective entry lands against the buy zone.

    Full marks at or below the zone; zero once the effective entry is 15%
    above it. The curve is deliberately steep — being 3% over the price
    you wanted is a materially worse trade than being at it, and a scale
    that treated those as nearly equal would rank on premium again by the
    back door.
    """
    g = f(entry_gap_pct)
    if g is None:
        return None
    if g <= 0:
        return float(points)
    return max(0.0, (1.0 - g / 15.0)) * points


def compute(row: dict, lt: dict | None = None) -> dict:
    """The disposition for one evaluated CSP row.

    `row` is what `engine.evaluate()` returned (or the slimmed snapshot
    version); `lt` is the long-term result when the caller has it, for the
    thesis reading the CSP row does not carry.
    """
    elig = row.get("eligibility") or {}
    disc = row.get("discount") or {}
    zone = row.get("buy_zone") or {}
    chosen = (row.get("chosen")
              or ((row.get("reference") or {}).get("best")) or {})
    liq = row.get("liquidity") or {}
    earn = row.get("earnings_distance") or {}
    final = row.get("final") or {}

    spot = f(row.get("price"))
    quality = f(elig.get("quality_score")) or f(row.get("lquality"))
    band_lo, band_hi = f(zone.get("low")), f(zone.get("high"))
    overhead = bool(zone.get("above_spot"))

    strike = f(chosen.get("strike"))
    credit = f(chosen.get("limit_price"))
    entry = effective_entry(strike, credit)

    # ── The two distances the whole framework turns on ──────────────────
    # Both are None when there is no zone: "we do not know where you want
    # to own this" is a different answer from "the price is fine".
    dist_to_zone_pct = (None if not (spot and band_hi) else
                        round((spot - band_hi) / spot * 100, 2))
    entry_gap = (None if not (entry and band_hi) else round(entry - band_hi, 2))
    entry_gap_pct = (None if not (entry and band_hi) else
                     round((entry - band_hi) / band_hi * 100, 2))
    entry_in_zone = (None if not (entry and band_lo and band_hi) else
                     entry <= band_hi)
    price_in_zone = (None if not (spot and band_lo and band_hi) else
                     band_lo <= spot <= band_hi)

    # ── Thesis: WHY is it down, not how far ─────────────────────────────
    thesis = _thesis_status(row, lt)

    # ── The five-state machine ──────────────────────────────────────────
    action, why = _decide(
        quality=quality, thesis=thesis, entry_in_zone=entry_in_zone,
        price_in_zone=price_in_zone, dist_to_zone_pct=dist_to_zone_pct,
        entry_gap_pct=entry_gap_pct, has_contract=bool(chosen),
        overhead=overhead, offerable=(final.get("key") not in
                                      (None, "REJECT")),
        adequacy=f((row.get("adequacy") or {}).get("ratio")
                   or chosen.get("adequacy")),
        earnings_inside=bool(earn.get("inside")))

    score, parts = _score(
        quality=quality, entry_gap_pct=entry_gap_pct, disc=disc,
        thesis=thesis, adequacy=f((row.get("adequacy") or {}).get("ratio")
                                  or chosen.get("adequacy")),
        liquidity=f(chosen.get("liquidity")) or f(liq.get("score")))

    return {
        "effective_entry": entry,
        "entry_vs_zone": entry_gap,
        "entry_vs_zone_pct": entry_gap_pct,
        "entry_in_zone": entry_in_zone,
        "dist_to_buy_zone_pct": dist_to_zone_pct,
        "price_in_zone": price_in_zone,
        "valuation_gap_pp": f(disc.get("growth_gap_pp")),
        "thesis_status": thesis,
        "ownership_score": score,
        "score_parts": parts,
        "final_action": action,
        "action_label": ACTION_LABEL.get(action, action),
        "why": why,
    }


def _thesis_status(row, lt) -> str:
    """Why the stock is down — which is a different question from how far.

    ORCL and INOD are the pair that forces this. ORCL is Stage 4 with the
    price inside its stated buy zone; INOD is quality 93 with a broken
    trend the engine calls thesis repricing. "Down a lot" describes both
    and distinguishes nothing.
    """
    lt = lt or row.get("_lt") or {}
    th = (lt.get("thesis") or {}) if isinstance(lt, dict) else {}
    if th.get("broken") or s(th.get("status")) == "BROKEN":
        return "BROKEN"
    trend = s(((lt.get("trend") or {}) if isinstance(lt, dict) else {})
              .get("state")) or ""
    why = (s((row.get("final") or {}).get("why")) or "").lower()
    if "thesis" in why or trend == "BROKEN" or "trend broken" in why:
        return "REPRICING"
    if "stage 4" in why or "markdown" in why:
        return "MARKDOWN"
    if s(th.get("status")) == "INTACT" or trend in ("CONFIRMED", "PARTIAL"):
        return "INTACT"
    return "UNMEASURED"


def _decide(*, quality, thesis, entry_in_zone, price_in_zone,
            dist_to_zone_pct, entry_gap_pct, has_contract, overhead,
            offerable, adequacy, earnings_inside):
    """The five-state machine. Order is the design.

    Quality first and unconditionally: a premium may never buy its way past
    it, which is the whole reason the gate runs before any chain is
    fetched. Everything after that is about WHERE the price is relative to
    where you wanted it.
    """
    if quality is None:
        return "AVOID", "no quality score — the business cannot be assessed"
    if quality < MIN_QUALITY:
        return "AVOID", (f"LQuality {quality:.0f} is below the {MIN_QUALITY} "
                         f"floor — not a business to be assigned into, at "
                         f"any premium")
    if thesis == "BROKEN":
        return "AVOID", "thesis broken — the discount is not a sale"
    if thesis == "REPRICING":
        return "THESIS_CHECK", ("price is attractive but the trend says the "
                                "market is repricing the thesis — decide "
                                "which before selling anything")

    # A zone the price has fallen through tells you nothing about where to
    # buy; treat the location as unknown rather than as satisfied.
    if overhead:
        return "THESIS_CHECK", ("price is below every tracked support — the "
                                "buy zone is overhead, so there is no level "
                                "left to buy against")

    if price_in_zone and quality >= ACCUMULATE_QUALITY and not has_contract:
        return "BUY_NOW", ("quality business inside its buy zone and no "
                           "contract worth selling — buy the shares rather "
                           "than forcing an option trade")

    if entry_in_zone and quality >= OWNABLE_QUALITY:
        if earnings_inside:
            return "WAIT_FOR_BETTER_STRIKE", (
                "effective entry is inside the buy zone, but an earnings "
                "print sits inside the expiry — the premium is paying for "
                "that gap")
        if not offerable:
            return "WAIT_FOR_BETTER_STRIKE", (
                "effective entry lands in the buy zone, but the engine's "
                "own gate is not offering this contract — see the reason "
                "beside it")
        if adequacy is not None and adequacy < 1.0:
            return "WAIT_FOR_BETTER_STRIKE", (
                f"effective entry is inside the buy zone but the premium "
                f"pays only {adequacy:.2f}x its hurdle")
        return "SELL_CSP", ("assignment would put you in at or below your "
                            "buy zone, and the premium clears its hurdle")

    if price_in_zone and quality >= OWNABLE_QUALITY:
        return "WAIT_FOR_BETTER_STRIKE", (
            "the PRICE is in the buy zone but the effective entry is not — "
            "a lower strike or a bigger credit would get you there")

    if dist_to_zone_pct is None:
        return "WAIT_FOR_BUY_ZONE", "no buy zone to measure against yet"

    if entry_gap_pct is not None and entry_gap_pct > 0:
        return "WAIT_FOR_BUY_ZONE", (
            f"good business, wrong price: the effective entry is "
            f"{entry_gap_pct:.1f}% above the top of the buy zone")

    if dist_to_zone_pct > WATCHABLE_GAP_PCT:
        return "WAIT_FOR_BUY_ZONE", (
            f"{dist_to_zone_pct:.0f}% above the buy zone — a watchlist name "
            f"rather than a pending trade")
    return "WAIT_FOR_BUY_ZONE", (
        f"{dist_to_zone_pct:.1f}% above the buy zone — worth an alert")


def _score(*, quality, entry_gap_pct, disc, thesis, adequacy, liquidity):
    """Ownership score, 0-100, renormalised over what was measurable.

    A missing input never becomes a score — the rule the whole package
    follows. A name with no zone is not given a middling entry mark; the
    entry term drops out and the remaining weights carry the score, and
    `parts` says which were counted.
    """
    parts, got, total = {}, 0.0, 0.0

    def add(name, value, weight):
        nonlocal got, total
        parts[name] = None if value is None else round(value, 1)
        if value is None:
            return
        got += value
        total += weight

    add("ownership", _band(quality, WEIGHTS["ownership"], MIN_QUALITY, 95),
        WEIGHTS["ownership"])
    add("entry", _entry_score(entry_gap_pct, WEIGHTS["entry"]),
        WEIGHTS["entry"])
    gap = f(disc.get("growth_gap_pp"))
    # Negative growth gap is cheap; +30pp is a price demanding far more
    # than the company has shown.
    add("valuation", None if gap is None else
        _band(-gap, WEIGHTS["valuation"], -30, 10), WEIGHTS["valuation"])
    add("technical", {"INTACT": float(WEIGHTS["technical"]),
                      "MARKDOWN": WEIGHTS["technical"] * 0.3,
                      "REPRICING": 0.0, "BROKEN": 0.0,
                      "UNMEASURED": None}.get(thesis), WEIGHTS["technical"])
    add("premium", None if adequacy is None else
        _band(adequacy, WEIGHTS["premium"], 0.8, 2.0), WEIGHTS["premium"])
    add("liquidity", _band(liquidity, WEIGHTS["liquidity"], 40, 90),
        WEIGHTS["liquidity"])

    if total <= 0:
        return None, parts
    return round(got / total * 100), parts
