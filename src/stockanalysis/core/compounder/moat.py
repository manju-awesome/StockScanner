"""
moat.py — Step 5. Evidence a moat is FORMING, not proof one exists.
===================================================================
"Do NOT require an already-established moat. Reward evidence that a moat is
forming." That instruction rules out every standard moat metric. High ROIC,
stable share, decades of pricing power — those are moats that already
finished forming, and screening on them finds the companies whose returns
have mostly been earned. This engine is looking for the ten years before
that, when the evidence is directional and nobody has written the
Morningstar rating yet.

What a forming moat looks like in numbers
-----------------------------------------
The honest answer is that switching costs, network effects and ecosystems
are not in a filing. What IS in a filing is their consequence, and the
consequence arrives before the label does:

    PRICING POWER      gross margin ABOVE the theme's own peers, and
                       RISING while revenue grows fast. Holding price
                       while scaling is the hardest thing for a company
                       without an advantage to do — competitors take share
                       with price, so a margin that expands into growth is
                       the single strongest available evidence.
    SCALE ADVANTAGE    revenue per employee above peers, and operating
                       margin expanding faster than gross margin — the
                       signature of fixed costs being spread.
    TECHNOLOGY         sustained R&D intensity that is producing margin
                       rather than just cost.
    CAPITAL EFFICIENCY returns on the capital actually employed.

Every one is measured AGAINST THE THEME'S OWN PEERS, not against the
market. A 42% gross margin is unremarkable for security software and
exceptional for a contract manufacturer; comparing to the S&P median would
rank whole themes rather than companies inside them, which is the mistake
this whole file exists to avoid.

The qualitative half is a claim, not a guess
--------------------------------------------
Switching costs, network effects, data advantage and customer dependency
cannot be derived from any of this. Where they are known they belong in
`MOAT_NOTES` — written down, attributable, and visible in a diff — and they
adjust the score within a bounded band. Where they are not known the score
says so. What this module never does is let the model invent a network
effect at scan time and then score it.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import blend, scale

from . import reinvestment as REINV

# How far a curated qualitative note may move the measured score, in points.
# Bounded deliberately: the note is a human judgment with no arithmetic
# behind it, and it should be able to break a tie, not decide the ranking.
NOTE_BAND = 12.0

# Peer percentile is the primary lens. A company needs this many peers with
# a measurable value before a percentile means anything — below it the
# comparison is dropped rather than computed off two companies.
MIN_PEERS = 4

MOAT_MECHANISMS = ("switching costs", "network effects", "ecosystem",
                   "proprietary technology", "IP/patents", "data advantage",
                   "cost advantage", "manufacturing advantage",
                   "distribution advantage", "customer dependency")

# ─────────────────────────────────────────────────────────────────────────────
# CURATED QUALITATIVE EVIDENCE
# ─────────────────────────────────────────────────────────────────────────────
# ticker -> {mechanism: (+/- points, one-line evidence)}. Only mechanisms
# with a specific, checkable reason belong here. "Strong brand" is not
# evidence; "designed into the reference platform of the dominant accelerator
# vendor, which fixes the socket for a product generation" is.
#
# Empty is the correct default. A ticker absent from this table is scored on
# its numbers alone and reported as "no qualitative evidence recorded",
# which is an honest statement about the library rather than a judgment
# about the company.
MOAT_NOTES: dict[str, dict] = {
    "ALAB": {"proprietary technology":
             (8, "connectivity silicon designed into accelerator reference "
                 "platforms — the socket is fixed for a product generation"),
             "switching costs":
             (5, "re-qualifying an interconnect part costs a design cycle")},
    "CRDO": {"proprietary technology":
             (7, "SerDes IP reused across cable, retimer and chiplet lines")},
    "RGEN": {"switching costs":
             (9, "bioprocessing consumables written into filed drug "
                 "manufacturing processes — changing supplier can require "
                 "a regulatory amendment")},
    "BWXT": {"manufacturing advantage":
             (9, "naval nuclear component qualification is a decades-long "
                 "regulatory barrier with effectively one qualified source")},
    "LEU": {"manufacturing advantage":
            (8, "the only licensed domestic HALEU enrichment capability")},
    "AVAV": {"distribution advantage":
             (6, "programs of record embed the platform in doctrine and "
                 "training, not just procurement")},
    "SYM": {"switching costs":
            (7, "warehouse automation is installed into the customer's "
                "physical operation and re-platforming halts fulfilment")},
    "VRT": {"distribution advantage":
            (6, "installed base plus service network at hyperscale sites")},
    "CGNX": {"proprietary technology":
             (6, "machine-vision algorithms embedded in customer line "
                 "controllers")},
    "RKLB": {"manufacturing advantage":
             (7, "vertically integrated launch plus components — sells into "
                 "its own competitors' supply chains")},
    "TXG": {"switching costs":
            (6, "instrument installed base pulls proprietary consumables")},
    "PACB": {"switching costs":
             (5, "sequencer placements pull proprietary consumables")},
    "TWST": {"manufacturing advantage":
             (6, "silicon-based DNA synthesis gives a structural cost-per-"
                 "base advantage over column chemistry")},
}


def _pct_rank(value, peers) -> float | None:
    """Percentile of `value` within `peers`, 0-100."""
    vals = sorted(v for v in peers if v is not None)
    if value is None or len(vals) < MIN_PEERS:
        return None
    below = sum(1 for v in vals if v < value)
    return round(below / max(1, len(vals) - 1) * 100, 1)


def build_peer_stats(rows) -> dict:
    """Per-theme distributions of the peer-relative measures.

    `rows` are the per-company measurement dicts, each already carrying its
    theme key. Built once per scan by engine.evaluate_universe(), because a
    percentile is not computable from a single company.
    """
    out: dict[str, dict] = {}
    for r in rows or []:
        key = r.get("theme_key")
        if not key:
            continue
        slot = out.setdefault(key, {"gross_margin": [], "rev_per_employee": [],
                                    "revenue": [], "growth": []})
        slot["gross_margin"].append(r.get("gross_margin_now"))
        slot["rev_per_employee"].append(r.get("revenue_per_employee"))
        slot["revenue"].append(r.get("ttm_revenue"))
        # position.competitive() reads this to rank a company's growth
        # against its own theme rather than against the market.
        slot["growth"].append(r.get("latest_growth"))
    return out


def compute(data: dict, lev: dict, reinv: dict, growth: dict,
            peers: dict | None = None) -> dict:
    """Moat formation for one company.

    `lev`, `reinv` and `growth` are this package's own Step 4 / 6 / 3
    outputs — reused rather than recomputed so the margin trend behind the
    moat reading is provably the same number the leverage card shows.
    """
    ticker = (data.get("ticker") or "").upper()
    peer_slot = (peers or {}).get(data.get("theme_key")) or {}

    gm_now = lev.get("gross_margin_now")
    gm_trend = lev.get("gross_margin_trend_pp")
    om_trend = lev.get("operating_margin_trend_pp")
    rpe = reinv.get("revenue_per_employee")

    gm_rank = _pct_rank(gm_now, peer_slot.get("gross_margin") or [])
    rpe_rank = _pct_rank(rpe, peer_slot.get("rev_per_employee") or [])

    # ── Pricing power ────────────────────────────────────────────────────
    # Margin expanding WHILE growing fast is the strong form. Expanding
    # while shrinking is cost-cutting and reads very differently, so the
    # growth state gates which interpretation applies.
    pricing = None
    pricing_note = "gross margin trend not measured"
    if gm_trend is not None:
        pricing = scale(gm_trend, -6.0, 8.0)
        fast = (growth.get("latest") or 0) > 20
        if gm_trend > 1 and fast:
            pricing = min(100.0, pricing + 12.0)
            pricing_note = (f"gross margin {gm_trend:+.1f}pp WHILE growing "
                            f"{growth.get('latest'):.0f}% — holding price "
                            f"through scale")
        elif gm_trend > 1:
            pricing_note = (f"gross margin {gm_trend:+.1f}pp, but growth is "
                            f"modest — could be mix or cost, not power")
        else:
            pricing_note = f"gross margin {gm_trend:+.1f}pp"
    if gm_rank is not None:
        # Blend the level-vs-peers in: being ABOVE the theme's peers is what
        # makes the margin evidence of an advantage rather than of an
        # industry's economics.
        pricing = (gm_rank if pricing is None else (pricing * 0.6
                                                    + gm_rank * 0.4))
        pricing_note += (f"; above {gm_rank:.0f}% of the tracked names in "
                         f"its theme")

    # ── Operating leverage as scale advantage ────────────────────────────
    # Operating margin expanding FASTER than gross margin means the fixed
    # cost base is being spread — a scale advantage forming, distinct from
    # simply charging more.
    scale_adv, scale_note = None, "margin history incomplete"
    if om_trend is not None and gm_trend is not None:
        spread = round(om_trend - gm_trend, 1)
        scale_adv = scale(spread, -6.0, 10.0)
        scale_note = (f"operating margin outpaced gross by {spread:+.1f}pp — "
                      f"fixed costs spreading" if spread > 0 else
                      f"operating margin trailed gross by {abs(spread):.1f}pp "
                      f"— opex is growing into the gross profit")
    elif om_trend is not None:
        scale_adv = scale(om_trend, -10.0, 15.0)
        scale_note = f"operating margin {om_trend:+.1f}pp"

    # ── Technology position ──────────────────────────────────────────────
    rnd_pct = reinv.get("rnd_pct")
    rnd_prod = reinv.get("rnd_productivity")
    tech, tech_note = None, "R&D not reported"
    if rnd_pct is not None:
        # The plateau curve, shared with reinvestment.py rather than
        # reimplemented: spending 300% of revenue on research is not
        # evidence of a technology position, it is evidence of a revenue
        # base too small to carry one.
        tech = REINV.rnd_intensity_score(rnd_pct)
        tech_note = f"R&D {rnd_pct:.1f}% of revenue"
        if rnd_prod is not None:
            # Spending that produces revenue is evidence of a technology
            # position; spending that does not is evidence of difficulty.
            tech = tech * 0.6 + scale(rnd_prod, 0.4, 1.8) * 0.4
            tech_note += f", productivity {rnd_prod:.2f}x"

    # ── Capital efficiency ───────────────────────────────────────────────
    op_now = next((v for v in (data.get("operating_annual") or [])
                   if v is not None), None)
    equity, debt = data.get("equity"), data.get("debt")
    invested = None
    if equity is not None:
        invested = equity + (debt or 0)
    roic = (round(op_now / invested * 100, 1)
            if op_now is not None and invested and invested > 0 else None)
    # Scored from -20% because a company investing ahead of returns is the
    # expected state in this universe, not a disqualification. What is
    # rewarded is the approach to positive returns on capital.
    cap_eff = None if roic is None else scale(roic, -20.0, 25.0)

    # ── Efficiency vs peers ──────────────────────────────────────────────
    efficiency_note = ("revenue per employee not comparable — too few peers"
                       if rpe_rank is None else
                       f"${rpe:,.0f} revenue per employee, above "
                       f"{rpe_rank:.0f}% of the tracked names in its theme")

    scored = blend([
        ("Pricing power", 32, pricing, pricing_note),
        ("Scale advantage", 24, scale_adv, scale_note),
        ("Technology position", 22, tech, tech_note),
        ("Capital efficiency", 12, cap_eff,
         f"{roic:.1f}% return on invested capital" if roic is not None
         else "balance sheet incomplete"),
        ("Operating efficiency", 10, rpe_rank, efficiency_note),
    ])

    base = scored["score"]
    notes = MOAT_NOTES.get(ticker) or {}
    adjust = 0.0
    evidence = []
    for mechanism, (points, why) in notes.items():
        adjust += points
        evidence.append({"mechanism": mechanism, "points": points, "why": why})
    adjust = max(-NOTE_BAND, min(NOTE_BAND, adjust))

    final = (None if base is None
             else int(round(max(0.0, min(100.0, base + adjust)))))

    return {
        "score": final,
        "measured_score": base,
        "note_adjustment": round(adjust, 1),
        "coverage": scored["coverage"],
        "components": scored["components"],
        "evidence": evidence,
        "mechanisms_recorded": sorted(notes),
        "mechanisms_unrecorded": [m for m in MOAT_MECHANISMS
                                  if m not in notes],
        "gross_margin_percentile": gm_rank,
        "rev_per_employee_percentile": rpe_rank,
        "roic_pct": roic,
        "state": _state(final, evidence),
        "detail": _detail(final, evidence, pricing_note),
    }


def _state(score, evidence) -> str:
    """What stage the moat itself is at — separate from the company's stage.

    A company can be scaling fast with no moat forming at all, and that
    pairing is the most dangerous one in this universe: growth that
    competitors can take back.
    """
    if score is None:
        return "UNMEASURED"
    if score >= 75:
        return "FORMING — STRONG"
    if score >= 60:
        return "FORMING"
    if score >= 45:
        return "EARLY / UNPROVEN"
    return "NO EVIDENCE YET"


def _detail(score, evidence, pricing_note) -> str:
    if score is None:
        return "not enough margin or R&D history to read a moat"
    bits = [pricing_note]
    if evidence:
        bits.append("; ".join(f"{e['mechanism']}: {e['why']}"
                              for e in evidence[:2]))
    else:
        bits.append("no qualitative moat evidence recorded in the library — "
                    "score is from the numbers alone")
    return ". ".join(bits)
