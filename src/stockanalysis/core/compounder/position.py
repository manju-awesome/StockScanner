"""
position.py — Steps 1, 2 and 7. The market, the share, and the standing.
========================================================================
Three questions that all depend on the same curated TAM curve:

    STEP 1  is the trend structural and long enough to matter?
    STEP 2  how much room is left, and is the company barely into it?
    STEP 7  can this company plausibly become a top-3 player?

Low penetration is an asset, and that is not obvious
----------------------------------------------------
Ordinary screens reward market share. This one rewards its ABSENCE, when it
sits inside a large and fast-growing market. A company with 0.4% of a $50B
market growing 20% a year has a hundred-bagger's worth of arithmetic room;
one with 34% of a $2B market growing 6% has already won and has nowhere to
go. Both can look identical on growth, margin and quality — penetration is
the term that separates them, and it is the reason Step 2 exists.

The honest limits, stated because they change how to read the output
--------------------------------------------------------------------
1. Share is computed as TTM revenue ÷ theme TAM. For a company selling into
   only part of a theme this UNDERSTATES share, sometimes by a lot. It is
   the right shape of number — a penetration order of magnitude — and it is
   not a market-research share figure. The engine says so on the card.

2. The competitive peer set is THIS LIBRARY's members of the theme, not
   every company in the market. Private companies, foreign listings and
   the mega-cap incumbents that are usually the real competitor are absent
   by construction. So "rank 2 of 9" means second-largest among the
   small-and-mid-cap names tracked here, and `incumbent` names the
   large-cap competitor the ranking cannot see.

Both limits are reported in the output rather than buried, because a share
figure that a reader takes literally is worse than no share figure.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import blend, scale

from . import themes as TH

# TAM size scale, in dollars. $5B is the floor at which a market can support
# a large company at all; $250B saturates — beyond it, size stops
# discriminating and the constraint becomes share, not room.
TAM_LO, TAM_HI = 5e9, 250e9

# TAM growth scale. 4% is GDP-ish and not a secular trend; 25% compounding
# for five years is a market tripling.
CAGR_LO, CAGR_HI = 4.0, 25.0

# Penetration, inverted — lower is better. A company above 12% of its theme's
# TAM has captured the obvious share and the next leg has to come from the
# market growing rather than from taking ground.
PEN_BEST, PEN_WORST = 0.05, 12.0

# The incumbent each theme's small caps are actually competing with. Named
# because the library's own peer ranking cannot see these — they are all
# outside the market-cap band — and a competitive read that omits them
# would be flattering nonsense.
INCUMBENTS = {
    "ai_infrastructure": "NVIDIA, Broadcom, and the hyperscalers' own silicon",
    "advanced_packaging": "TSMC, ASE, Amkor at scale",
    "semicap": "Applied Materials, Lam Research, ASML, KLA",
    "photonics_optical": "Coherent, Lumentum, Broadcom, Innolight (private)",
    "power_grid": "Eaton, Schneider, ABB, Siemens Energy, Hitachi Energy",
    "datacenter_infrastructure": "Vertiv, Schneider, Eaton, nVent",
    "power_semis": "Infineon, onsemi, STMicro, Texas Instruments",
    "energy_storage": "Tesla Energy, CATL, Sungrow, BYD",
    "nuclear": "Westinghouse (private), Framatome, GE Vernova Hitachi",
    "cybersecurity": "Palo Alto, CrowdStrike, Microsoft, Zscaler",
    "robotics_automation": "Fanuc, ABB, Keyence, Rockwell, Siemens",
    "defense_tech": "Lockheed, RTX, Northrop, Anduril (private), Palantir",
    "space": "SpaceX (private), Lockheed, Northrop, Airbus",
    "digital_infrastructure": "Cisco, Arista, Nokia, Ciena at scale",
    "biotech_tools": "Thermo Fisher, Danaher, Illumina, Sartorius",
    "water_infrastructure": "Xylem, Veralto, Pentair, Ecolab",
    "quantum": "IBM, Google, Microsoft, PsiQuantum (private)",
}

UNMEASURABLE = ("third-party market-share data", "win/loss rates",
                "private competitor revenue", "patent portfolio strength")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — SECULAR TAM SCORE
# ─────────────────────────────────────────────────────────────────────────────

def secular(theme_rec: dict | None) -> dict:
    """0-100 on the market itself, before any company is considered.

    Every company in a theme gets the same secular score — that is correct
    and intentional. Step 1 asks about the trend, not the participant, and
    the participant is scored by the other nine steps.
    """
    if not theme_rec:
        return {"score": None, "coverage": 0.0, "components": [],
                "detail": ("no secular theme identified — this company is "
                           "not mapped in the theme library"),
                "confidence": None, "capped": False}

    cagr5 = theme_rec.get("tam_cagr_5y")
    cagr10 = theme_rec.get("tam_cagr_10y")
    tam = theme_rec.get("tam_now")
    adjacencies = theme_rec.get("adjacencies")
    durability = theme_rec.get("durability")

    scored = blend([
        # Durability leads. Step 1 asks for a trend "capable of expanding
        # for 5-10+ years", and that is a question about whether the demand
        # is contracted, regulated or physically committed — not about how
        # fast it is growing right now. A 30% market that is one capex
        # cycle deep is not a secular trend.
        ("Durability", 30, None if durability is None else float(durability),
         f"{durability}/100 contracted, regulated or physically committed"
         if durability is not None else "not assessed"),
        ("5-year TAM growth", 28,
         None if cagr5 is None else scale(cagr5, CAGR_LO, CAGR_HI),
         f"{cagr5:.1f}% CAGR to {_money(theme_rec.get('tam_5y'))}"
         if cagr5 is not None else "no 5-year figure"),
        ("Market size", 22,
         None if tam is None else scale(tam, TAM_LO, TAM_HI),
         f"{_money(tam)} today"),
        ("10-year persistence", 12,
         None if cagr10 is None else scale(cagr10, CAGR_LO, CAGR_HI),
         f"{cagr10:.1f}% CAGR to {_money(theme_rec.get('tam_10y'))}"
         if cagr10 is not None else "no 10-year figure"),
        ("Adjacent markets", 8,
         None if adjacencies is None else scale(float(adjacencies), 1.0, 5.0),
         f"{adjacencies} separately-addressable adjacent markets"
         if adjacencies is not None else "not assessed"),
    ])

    # Confidence applies TWICE, and both are load-bearing.
    #
    # The FACTOR discounts proportionally: a market whose five-year size is
    # a scenario is worth less than the same size measured, and that is a
    # difference of degree that should show up as a difference of degree.
    #
    # The CEILING is the backstop for the case the factor cannot reach —
    # a tiny speculative market whose CAGR is arithmetically enormous
    # because its base is near zero. Quantum computing scores 60 raw on
    # pure arithmetic against semiconductor equipment's 49, and without the
    # ceiling a pre-revenue market would outrank a $115B one with a decade
    # of shipped revenue behind it.
    raw = scored["score"]
    confidence = theme_rec.get("confidence")
    factor = TH.tam_confidence_factor(theme_rec)
    ceiling = TH.CONFIDENCE_CEILING.get(confidence, 45.0)

    discounted = None if raw is None else float(raw) * factor
    capped = discounted is not None and discounted > ceiling
    final = None if discounted is None else int(round(min(discounted, ceiling)))

    detail = theme_rec.get("basis") or ""
    if factor < 1.0:
        detail += (f" — discounted ×{factor:.2f} for {confidence} TAM "
                   f"confidence")
    if capped:
        detail += f", then capped at {ceiling:.0f}"

    return {
        "score": final, "raw_score": raw, "capped": capped,
        "confidence_factor": factor,
        "ceiling": ceiling, "confidence": confidence,
        "coverage": scored["coverage"], "components": scored["components"],
        "detail": detail, "risk": theme_rec.get("risk"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — TAM, SHARE, AND THE ROOM LEFT
# ─────────────────────────────────────────────────────────────────────────────

def opportunity(theme_rec: dict | None, ttm_revenue, growth: dict) -> dict:
    """Market-share opportunity: how much room, and how little taken."""
    if not theme_rec:
        return {"score": None, "share_pct": None, "detail":
                "no theme mapped — market-share opportunity not assessable",
                "components": [], "coverage": 0.0}

    tam = theme_rec.get("tam_now")
    tam5 = theme_rec.get("tam_5y")
    share = (round(ttm_revenue / tam * 100, 3)
             if ttm_revenue and tam else None)

    # Share the company would hold in five years IF it grew at its own
    # current rate and the market grew at the theme's rate. Explicitly a
    # projection of two assumptions, labelled as one, and reported rather
    # than scored — its only job is to make the arithmetic of the
    # opportunity visible.
    implied = None
    latest = growth.get("latest")
    if share is not None and latest is not None and tam and tam5:
        company_5y = ttm_revenue * ((1 + min(latest, 60.0) / 100) ** 5)
        implied = round(company_5y / tam5 * 100, 2)

    headroom = None if share is None else round(100.0 - share, 2)

    pen_score = None
    if share is not None:
        # Inverted: less penetration is more opportunity.
        pen_score = max(0.0, min(100.0, (PEN_WORST - share)
                                 / (PEN_WORST - PEN_BEST) * 100.0))

    tam5_score = (None if not tam5 else scale(tam5, TAM_LO * 2, TAM_HI * 2))
    adj = theme_rec.get("adjacencies")

    scored = blend([
        ("Penetration headroom", 45, pen_score,
         f"{share:.3f}% of the theme's TAM taken" if share is not None
         else "revenue or TAM missing"),
        ("Market size in 5 years", 30, tam5_score,
         f"{_money(tam5)} addressable by then" if tam5 else "no 5-year TAM"),
        ("Adjacent expansion", 25,
         None if adj is None else scale(float(adj), 1.0, 5.0),
         ", ".join(theme_rec.get("adjacent_markets") or []) or "none listed"),
    ])

    return {
        "score": scored["score"],
        "coverage": scored["coverage"],
        "components": scored["components"],
        "tam_now": tam, "tam_5y": tam5, "tam_10y": theme_rec.get("tam_10y"),
        "tam_cagr_5y": theme_rec.get("tam_cagr_5y"),
        "tam_cagr_10y": theme_rec.get("tam_cagr_10y"),
        "share_pct": share,
        "headroom_pct": headroom,
        "implied_share_5y_pct": implied,
        "adjacent_markets": theme_rec.get("adjacent_markets") or [],
        "caveat": ("share is TTM revenue over the whole theme TAM — a "
                   "company addressing only part of the theme will read "
                   "lower than its true share of the segment it sells into"),
        "detail": _share_detail(share, implied, tam, tam5),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — COMPETITIVE POSITION
# ─────────────────────────────────────────────────────────────────────────────

def competitive(data: dict, theme_rec: dict | None, growth: dict,
                moat: dict, peers: dict | None = None) -> dict:
    """Rank within the theme, and whether top-3 is reachable.

    Top-3 is assessed on TRAJECTORY rather than position: a company ranked
    seventh of eleven that is growing three times as fast as the theme's
    median is on its way up, and a screen that reads only current rank
    would rank it below the incumbent it is about to pass.
    """
    key = data.get("theme_key")
    slot = (peers or {}).get(key) or {}
    revs = sorted((v for v in (slot.get("revenue") or []) if v), reverse=True)
    mine = data.get("ttm_revenue")

    rank = n = None
    if mine and revs:
        n = len(revs)
        rank = sum(1 for v in revs if v > mine) + 1

    # Fragmentation: the largest tracked peer's share of all tracked
    # revenue. A theme where one name holds 60% is consolidating around it;
    # one where the leader holds 18% is still open.
    frag, structure = None, "not assessable"
    if revs and sum(revs) > 0:
        frag = round(revs[0] / sum(revs) * 100, 1)
        structure = ("CONSOLIDATING — one tracked name holds "
                     f"{frag:.0f}% of tracked revenue" if frag >= 45 else
                     f"FRAGMENTED — the largest tracked name holds only "
                     f"{frag:.0f}%")

    # Trajectory against the theme's own growth, not the market's.
    peer_growths = [g for g in (slot.get("growth") or []) if g is not None]
    median_growth = None
    if peer_growths:
        ordered = sorted(peer_growths)
        m = len(ordered)
        median_growth = (ordered[m // 2] if m % 2
                         else (ordered[m // 2 - 1] + ordered[m // 2]) / 2)
    mine_growth = growth.get("latest")
    outgrowing = (None if median_growth is None or mine_growth is None
                  else round(mine_growth - median_growth, 1))

    rank_score = None
    if rank is not None and n and n > 1:
        # Being large among peers is worth something, but not much — this
        # engine is looking for the company that BECOMES top-3, and current
        # rank is the thing it is trying to see past.
        rank_score = (n - rank) / (n - 1) * 100.0

    traj_score = (None if outgrowing is None
                  else scale(outgrowing, -20.0, 30.0))

    scored = blend([
        ("Growth vs theme peers", 45, traj_score,
         f"{outgrowing:+.0f}pp against the theme median of "
         f"{median_growth:.0f}%" if outgrowing is not None
         else "no peer growth distribution"),
        ("Moat formation", 35,
         None if moat.get("score") is None else float(moat["score"]),
         moat.get("state") or "unmeasured"),
        ("Current standing", 20, rank_score,
         f"#{rank} of {n} tracked names by revenue" if rank
         else "revenue not comparable"),
    ])

    top3 = _top3(rank, n, outgrowing, moat.get("score"))

    return {
        "score": scored["score"],
        "coverage": scored["coverage"],
        "components": scored["components"],
        "rank": rank, "peer_count": n,
        "peer_tickers": TH.members(key) if key else [],
        "fragmentation_pct": frag,
        "structure": structure,
        "outgrowing_peers_pp": outgrowing,
        "peer_median_growth": (None if median_growth is None
                               else round(median_growth, 1)),
        "incumbent": INCUMBENTS.get(key or "", "not recorded"),
        "top3_path": top3,
        "caveat": ("rank is against this library's tracked small/mid-cap "
                   "members of the theme only — private companies and the "
                   "large-cap incumbents named above are not in it"),
        "unmeasured": list(UNMEASURABLE),
    }


def _top3(rank, n, outgrowing, moat_score) -> dict:
    """Can it become a top-3 player, and what is the gating condition?"""
    if rank is None:
        return {"verdict": "UNKNOWN",
                "why": "revenue not comparable against the theme's peers"}
    if rank <= 3:
        holding = (outgrowing is not None and outgrowing >= 0)
        return {"verdict": "ALREADY TOP-3" if holding else "TOP-3, LOSING GROUND",
                "why": (f"#{rank} of {n} tracked names and "
                        + ("still outgrowing the median" if holding else
                           "growing slower than the theme median — the "
                           "position is not being defended"))}
    if outgrowing is not None and outgrowing > 10 and (moat_score or 0) >= 55:
        return {"verdict": "PLAUSIBLE",
                "why": (f"#{rank} of {n}, but outgrowing the theme median by "
                        f"{outgrowing:.0f}pp with a moat forming — the gap "
                        f"closes if that holds for three more years")}
    if outgrowing is not None and outgrowing > 10:
        return {"verdict": "PLAUSIBLE, UNDEFENDED",
                "why": (f"outgrowing peers by {outgrowing:.0f}pp with no moat "
                        f"evidence yet — share taken this way is share a "
                        f"competitor can take back")}
    return {"verdict": "NOT ON CURRENT TRAJECTORY",
            "why": (f"#{rank} of {n} and not outgrowing the theme median — "
                    f"nothing in the numbers points at a top-3 finish")}


# ─────────────────────────────────────────────────────────────────────────────

def _money(v) -> str:
    if not v:
        return "—"
    if v >= 1e12:
        return f"${v / 1e12:.1f}T"
    if v >= 1e9:
        return f"${v / 1e9:.0f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _share_detail(share, implied, tam, tam5) -> str:
    if share is None:
        return f"TAM {_money(tam)} → {_money(tam5)} in 5 years; company " \
               f"revenue unavailable so share could not be computed"
    lead = (f"{share:.3f}% of a {_money(tam)} market" if share < 1
            else f"{share:.1f}% of a {_money(tam)} market")
    if implied is not None:
        lead += (f"; at its current growth rate against the theme's own "
                 f"growth that becomes {implied:.2f}% by year five")
    return lead
