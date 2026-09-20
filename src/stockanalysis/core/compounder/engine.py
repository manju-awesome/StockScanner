"""
engine.py — Step 12. The composite, and what it is allowed to do.
=================================================================
The FUTURE COMPOUNDER SCORE, at the weights the framework specifies:

    Secular TAM              20%
    Growth acceleration      15%
    Moat formation           15%
    Market-share opportunity 10%
    Operating leverage       10%
    Reinvestment / R&D       10%
    Competitive position      5%
    Management                5%
    Financial survivability   5%
    Market discovery          5%

Why this engine weights where the rest of this project gates
------------------------------------------------------------
Every other engine in this project is built on gates: business quality
above a bar or nothing below it matters. That design is correct there and
would be wrong here, and the reason is worth stating because it looks like
an inconsistency.

A gate encodes a condition you are unwilling to trade away. For a
long-term BUY decision that is right — no chart pattern compensates for a
deteriorating business. But this engine is not making a buy decision. It is
answering "could this become a major company in ten years", and the honest
answer is that every candidate fails something today. Negative free cash
flow, no profits, 3% institutional ownership, a market nobody has sized —
gate on any of them and the list that survives is large-cap quality names,
which is the /longterm engine's job and not this one's.

The brief says so directly: do not reject for high P/E, negative FCF, no
profits, no index membership, small size or low institutional ownership.
"Instead classify the risk." So this engine classifies. `risk_flags` names
every condition a gate would have rejected on, the risk classification
carries them, and the score keeps ranking.

The one thing that is NOT weighted
----------------------------------
Valuation. It is absent from the framework's weights and it is absent here,
which is deliberate rather than an oversight: paying 14x sales for a company
that becomes a $50B business works out, and paying 4x sales for one that
does not, does not. What valuation belongs to is the position size and the
entry, and this project already has two engines for that. A reader who
wants the price question answered should take a name from this list to
/longterm, and the page says so.

Coverage is reported everywhere
-------------------------------
`blend()` renormalises over what was measurable, so a company missing three
inputs is scored on the seven that were present rather than penalised into
the middle. `coverage` travels with every score, and a 91 built on 45% of
the weight is a different claim from a 91 built on 95%. The engine
therefore reports BOTH, and `confidence` grades the pair.
"""

from __future__ import annotations

import logging

from stockanalysis.core.longterm._common import blend, s

from . import discovery as DISC
from . import fetch as FETCH
from . import growth as GROW
from . import leverage as LEV
from . import management as MGMT
from . import moat as MOAT
from . import narrative as NARR
from . import position as POS
from . import reinvestment as REINV
from . import stage as STAGE
from . import survivability as SURV
from . import themes as TH

log = logging.getLogger(__name__)

# The universe band from the brief. Enforced as a CLASSIFICATION, never as a
# silent drop — a name that compounded out of the top of the band is the
# engine's own success case and deserves to be visible as one.
CAP_MIN = 300e6
CAP_MAX = 20e9

BANDS = ("IN BAND", "GRADUATED", "BELOW BAND", "UNKNOWN")

WEIGHTS = (
    ("Secular TAM", 20), ("Growth acceleration", 15), ("Moat formation", 15),
    ("Market-share opportunity", 10), ("Operating leverage", 10),
    ("Reinvestment / R&D", 10), ("Competitive position", 5),
    ("Management", 5), ("Financial survivability", 5),
    ("Market discovery", 5),
)

# Composite bands. Named rather than numbered because "78" means nothing to
# a reader and "the case is complete" does.
TIERS = ((78, "CONVICTION", "💎"), (68, "STRONG CANDIDATE", "🟢"),
         (58, "WATCH", "🟡"), (45, "EARLY / UNPROVEN", "🔵"),
         (0, "NO CASE YET", "⚪"))

# Coverage below this makes the score a sketch rather than a measurement.
COVERAGE_GOOD = 0.80
COVERAGE_THIN = 0.60


def cap_band(market_cap) -> tuple[str, str]:
    if market_cap is None:
        return "UNKNOWN", "market cap unavailable"
    if market_cap < CAP_MIN:
        return "BELOW BAND", (f"${market_cap / 1e6:.0f}M — below the $300M "
                              f"floor; liquidity and disclosure quality both "
                              f"degrade sharply here")
    if market_cap > CAP_MAX:
        return "GRADUATED", (f"${market_cap / 1e9:.1f}B — above the $20B "
                             f"ceiling. Kept and labelled rather than "
                             f"dropped: a name that grew out of the band is "
                             f"this engine's success case, not an error")
    return "IN BAND", f"${market_cap / 1e9:.2f}B"


def risk_flags(lev, surv, disc, growth, comp, secular) -> list[dict]:
    """Every condition a conventional screen would have rejected on.

    Reported as classified risks with the reason they are NOT rejections.
    This list is the brief's "instead classify the risk" instruction made
    literal, and it is the most-read block on the card.
    """
    out = []
    fcf = lev.get("fcf_now")
    if fcf is not None and fcf < 0:
        out.append({
            "flag": "Negative free cash flow", "level": "CLASSIFIED",
            "detail": (f"${fcf / 1e6:.0f}M — {lev.get('fcf_note')}. "
                       f"Funding state is the relevant question and is "
                       f"classified as {surv.get('classification')}")})
    if surv.get("classification") in ("HIGH DILUTION RISK", "DISTRESSED"):
        out.append({"flag": f"Funding: {surv['classification']}",
                    "level": "MATERIAL", "detail": surv.get("why") or ""})
    dil = (surv.get("dilution_risk") or {}).get("level")
    if dil in ("HIGH", "SEVERE"):
        out.append({"flag": f"Dilution {dil.lower()}", "level": "MATERIAL",
                    "detail": (surv.get("dilution_risk") or {}).get("detail")})
    if (disc.get("inst_own_pct") or 0) < 20:
        out.append({
            "flag": "Low institutional ownership", "level": "CLASSIFIED",
            "detail": (f"{disc.get('inst_own_pct')}% — thin professional "
                       f"verification of the numbers, and a wide spread on "
                       f"exit. Not a rejection: it is also what makes the "
                       f"opportunity available")})
    if (disc.get("analysts") or 0) < 4:
        out.append({
            "flag": "Minimal analyst coverage", "level": "CLASSIFIED",
            "detail": (f"{disc.get('analysts') or 0} analysts — no consensus "
                       f"to check the story against, and no forward estimates "
                       f"worth much")})
    if growth.get("state") == "DECELERATING":
        out.append({"flag": "Growth decelerating", "level": "MATERIAL",
                    "detail": growth.get("detail") or ""})
    elif growth.get("state") == "FADING FROM A HIGH BASE":
        # Classified rather than material: the fade is arithmetic. Flagged
        # anyway, because a reader who sees only "104% growth" should also
        # see that the rate halved to get there.
        out.append({"flag": "Growth fading from an unsustainable base",
                    "level": "CLASSIFIED", "detail": growth.get("detail") or ""})
    if growth.get("state") == "UNMEASURED":
        out.append({"flag": "Growth trajectory unmeasured", "level": "DATA",
                    "detail": growth.get("detail") or ""})
    if secular.get("confidence") == "LOW":
        out.append({
            "flag": "TAM is a scenario, not a measurement", "level": "MATERIAL",
            "detail": (f"{secular.get('detail') or ''} — the market this "
                       f"company addresses is largely pre-revenue, so the "
                       f"opportunity leg of the score rests on an assumption "
                       f"about a market existing at all")})
    if (comp.get("top3_path") or {}).get("verdict") == "PLAUSIBLE, UNDEFENDED":
        out.append({"flag": "Share gains with no moat evidence",
                    "level": "MATERIAL",
                    "detail": (comp.get("top3_path") or {}).get("why") or ""})
    return out


def _confidence(coverage, flags) -> dict:
    data_flags = sum(1 for f_ in flags if f_["level"] == "DATA")
    if coverage is None:
        return {"level": "NONE", "icon": "⚪",
                "detail": "nothing could be measured"}
    if coverage >= COVERAGE_GOOD and not data_flags:
        return {"level": "HIGH", "icon": "🟢",
                "detail": f"{coverage * 100:.0f}% of the factor weight had data"}
    if coverage >= COVERAGE_THIN:
        return {"level": "MEDIUM", "icon": "🟡",
                "detail": (f"{coverage * 100:.0f}% of the factor weight had "
                           f"data — read the score as a sketch")}
    return {"level": "LOW", "icon": "🟠",
            "detail": (f"only {coverage * 100:.0f}% of the factor weight had "
                       f"data. Too little to rank on")}


def tier(score) -> tuple[str, str]:
    if score is None:
        return "UNSCORED", "⚪"
    for floor, name, icon in TIERS:
        if score >= floor:
            return name, icon
    return TIERS[-1][1], TIERS[-1][2]


def evaluate(data: dict, peers: dict | None = None) -> dict:
    """One company, all twelve steps. `data` is a fetch.fetch() dict.

    Pure: no network. `peers` is the per-theme distribution built by
    `build_peer_context()` — without it the peer-relative legs (moat
    percentiles, competitive rank) report unmeasured and the composite
    renormalises, which is why a single-ticker lookup still works and says
    plainly what it could not compare.
    """
    ticker = s(data.get("ticker"))
    theme_rec = TH.theme_for(ticker or "")
    data = {**data, "theme_key": (theme_rec or {}).get("key")}

    growth = GROW.compute(data)
    lev = LEV.compute(data)
    reinv = REINV.compute(data)

    # moat needs the peer-comparable fields, which live on the enriched row
    enriched = {**data,
                "gross_margin_now": lev.get("gross_margin_now"),
                "revenue_per_employee": reinv.get("revenue_per_employee"),
                "ttm_revenue": growth.get("ttm_revenue"),
                "latest_growth": growth.get("latest")}

    moat = MOAT.compute(enriched, lev, reinv, growth, peers)
    secular = POS.secular(theme_rec)
    opportunity = POS.opportunity(theme_rec, growth.get("ttm_revenue"), growth)
    comp = POS.competitive(enriched, theme_rec, growth, moat, peers)
    surv = SURV.compute(data, lev)
    mgmt = MGMT.compute(data, growth, surv, reinv)
    disc = DISC.compute(data)
    st = STAGE.classify(data, growth, lev, surv, comp)

    scored = blend([
        ("Secular TAM", 20, _v(secular), secular.get("detail") or ""),
        ("Growth acceleration", 15, _v(growth), growth.get("detail") or ""),
        ("Moat formation", 15, _v(moat), moat.get("state") or ""),
        ("Market-share opportunity", 10, _v(opportunity),
         opportunity.get("detail") or ""),
        ("Operating leverage", 10, _v(lev), lev.get("detail") or ""),
        ("Reinvestment / R&D", 10, _v(reinv), reinv.get("detail") or ""),
        ("Competitive position", 5, _v(comp),
         (comp.get("top3_path") or {}).get("verdict") or ""),
        ("Management", 5, _v(mgmt), mgmt.get("detail") or ""),
        ("Financial survivability", 5, _v(surv),
         f"{surv.get('icon')} {surv.get('classification')}"),
        ("Market discovery", 5, _v(disc), disc.get("detail") or ""),
    ])

    band, band_note = cap_band(data.get("market_cap"))
    flags = risk_flags(lev, surv, disc, growth, comp, secular)
    score = scored["score"]
    tier_name, tier_icon = tier(score)

    result = {
        "ticker": ticker,
        "name": s(data.get("name")) or ticker,
        "sector": s(data.get("sector")),
        "industry": s(data.get("industry")),
        "market_cap": data.get("market_cap"),
        "price": data.get("price"),
        "cap_band": band, "cap_note": band_note,
        "theme": theme_rec,
        "theme_key": (theme_rec or {}).get("key"),
        "theme_label": (theme_rec or {}).get("label"),

        "score": score,
        "tier": tier_name, "tier_icon": tier_icon,
        "coverage": scored["coverage"],
        "components": scored["components"],
        "missing": scored["missing"],
        "confidence": _confidence(scored["coverage"], flags),

        "secular": secular,
        "opportunity": opportunity,
        "growth": growth,
        "leverage": lev,
        "moat": moat,
        "reinvestment": reinv,
        "competitive": comp,
        "management": mgmt,
        "survivability": surv,
        "discovery": disc,
        "stage": st,
        "risk_flags": flags,
        "errors": data.get("errors") or [],
    }
    result["narrative"] = NARR.build(result)
    return result


def _v(part):
    """A sub-engine's score as a float, or None. Never 0 for missing."""
    return None if (part or {}).get("score") is None else float(part["score"])


def build_peer_context(rows) -> dict:
    """Per-theme distributions. Needs the whole universe, hence its own pass.

    `rows` are lightly-enriched fetch dicts: enough of each company computed
    to build the distributions the second pass compares against. Computing
    growth and margins twice is the price of not holding two copies of the
    universe in memory, and it is cheap — both are pure arithmetic on data
    already fetched.
    """
    enriched = []
    for data in rows or []:
        theme_key = TH.THEME_MEMBERS.get((data.get("ticker") or "").upper())
        g = GROW.compute(data)
        lv = LEV.compute(data)
        rv = REINV.compute(data)
        enriched.append({
            "theme_key": theme_key,
            "gross_margin_now": lv.get("gross_margin_now"),
            "revenue_per_employee": rv.get("revenue_per_employee"),
            "ttm_revenue": g.get("ttm_revenue"),
            "latest_growth": g.get("latest"),
        })
    return MOAT.build_peer_stats(enriched)


def evaluate_universe(tickers=None, progress=None,
                      benchmark: str = "QQQ") -> list[dict]:
    """Fetch and score the whole theme library.

    Two passes by necessity: the peer-relative legs cannot be computed
    until every company has been measured, and a company's competitive
    standing is meaningless without them. Fetching is the slow part and it
    happens once.

    `progress` is an optional callable(done, total, ticker) for the webapp's
    job runner.
    """
    tickers = list(tickers or TH.universe())
    b3, b12 = FETCH.benchmark_returns(benchmark)

    raw, total = [], len(tickers)
    for i, t in enumerate(tickers, 1):
        try:
            raw.append(FETCH.fetch(t, b3, b12))
        except Exception as e:                          # pragma: no cover
            log.warning("[Compounder] %s: fetch failed (%s)", t, e)
        if progress:
            try:
                progress(i, total, t)
            except Exception:
                pass

    peers = build_peer_context(raw)

    out = []
    for data in raw:
        try:
            out.append(evaluate(data, peers))
        except Exception as e:                          # pragma: no cover
            log.warning("[Compounder] %s: scoring failed (%s)",
                        data.get("ticker"), e)

    out.sort(key=lambda r: -(r.get("score") or 0))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# THE 10-YEAR WATCHLIST
# ─────────────────────────────────────────────────────────────────────────────

WATCHLIST_SIZE = 20

# Below this the composite is not a ranking, it is noise — a name scored on
# under 60% of the factor weight has too many unmeasured legs to sit on a
# list somebody acts on. Excluded names are REPORTED, not hidden.
WATCHLIST_MIN_COVERAGE = COVERAGE_THIN


def watchlist(rows, size: int = WATCHLIST_SIZE) -> dict:
    """The top names, ranked by score, with every exclusion stated.

    Ranked by FUTURE COMPOUNDER SCORE, per the brief — explicitly not by
    valuation, size or momentum. Three things shape the list, and each is
    reported rather than applied silently:

    COVERAGE      a score built on under 60% of the factor weight is not a
                  ranking. Held off and named.

    STAGE 5       a MATURE company is excluded, because the framework's own
                  definition of that stage is that the compounding is behind
                  it. A "10-Year Watchlist" containing a company whose
                  growth has already converged on its market's contradicts
                  the deliverable — Nutanix at 10% growth is a fine business
                  and not a future compounder. It stays in the full ranking,
                  where its score is still the honest answer to "how good is
                  this company"; what it does not do is occupy one of twenty
                  slots reserved for names that could still become leaders.

    STAGE ORDER   §11 says to prioritise Stages 1 and 2, and §12 says to
                  rank by score. Both are honoured by making score the
                  primary key and stage the TIE-BREAKER: among companies the
                  engine rates equally, the earlier one wins, because it has
                  more of its compounding ahead of it. Stage never overrides
                  a score difference.

    Theme concentration is REPORTED rather than corrected. Capping names per
    theme would silently override the ranking, and if the top of the list is
    seven AI-infrastructure names then that is what the framework found —
    the reader should see the concentration and decide, which is a different
    thing from the engine hiding it.
    """
    scored = [r for r in rows or [] if r.get("score") is not None]
    thin = [r for r in scored
            if (r.get("coverage") or 0) < WATCHLIST_MIN_COVERAGE]
    deep = [r for r in scored
            if (r.get("coverage") or 0) >= WATCHLIST_MIN_COVERAGE]

    mature = [r for r in deep if (r.get("stage") or {}).get("stage") == 5]
    eligible = [r for r in deep if (r.get("stage") or {}).get("stage") != 5]

    # Score first, stage second. See the docstring.
    eligible.sort(key=lambda r: (-(r["score"] or 0),
                                 (r.get("stage") or {}).get("stage") or 9))
    top = eligible[:size]

    themes: dict[str, int] = {}
    stages: dict[str, int] = {}
    for r in top:
        key = r.get("theme_label") or "unthemed"
        themes[key] = themes.get(key, 0) + 1
        lbl = (r.get("stage") or {}).get("label") or "?"
        stages[lbl] = stages.get(lbl, 0) + 1

    early = [r for r in top if (r.get("stage") or {}).get("stage") in (1, 2)]

    return {
        "rows": top,
        "size": len(top),
        "themes": dict(sorted(themes.items(), key=lambda kv: -kv[1])),
        "stages": stages,
        "early_count": len(early),
        "excluded_thin": [{"ticker": r["ticker"], "score": r["score"],
                           "coverage": r.get("coverage")} for r in thin],
        # Named, not hidden: a reader who knows the engine scored Nutanix 68
        # deserves to know why it is not on the twenty, and "it is already
        # mature" is a more useful answer than its absence.
        "excluded_mature": [{"ticker": r["ticker"], "score": r["score"],
                             "why": (r.get("stage") or {}).get("why")}
                            for r in sorted(
                                mature, key=lambda x: -(x["score"] or 0))],
        "unscored": [r["ticker"] for r in (rows or [])
                     if r.get("score") is None],
        "concentration_note": _concentration(themes, len(top)),
    }


def _concentration(themes, n) -> str | None:
    if not themes or not n:
        return None
    label, count = next(iter(themes.items()))
    if count / n < 0.35:
        return None
    return (f"{count} of {n} names are in one theme ({label}). The list is "
            f"ranked purely on score and is not diversified for you — a "
            f"single theme thesis failing would take most of it down "
            f"together.")


def counts(rows) -> dict:
    """Tier counts for the page header."""
    out = {name: 0 for _f, name, _i in TIERS}
    out["UNSCORED"] = 0
    for r in rows or []:
        out[r.get("tier", "UNSCORED")] = out.get(r.get("tier", "UNSCORED"), 0) + 1
    return out


def stage_counts(rows) -> dict:
    out: dict[str, int] = {}
    for r in rows or []:
        lbl = (r.get("stage") or {}).get("label")
        if lbl:
            out[lbl] = out.get(lbl, 0) + 1
    return out
