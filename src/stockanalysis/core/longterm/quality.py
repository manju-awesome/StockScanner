"""
quality.py — LQuality, 0-100
============================
"What deserves to be owned?" — the first and highest gate in the long-term
engine, and the only one that is purely about the business. No price, no
chart, no market context enters this score. That separation is the point: a
quality number that moves when the stock moves cannot be used to decide
whether a fall in the stock is an opportunity or a warning.

Ten factors, weighted to 100:

    Revenue growth              10   consistent, preferably >10%
    EPS growth                  15   preferably >15%
    ROE                         10   high and sustainable
    Operating margin            10   stable / increasing
    Free cash flow              10   positive and growing
    Balance sheet               10   low / manageable debt
    Competitive moat            15   durable advantage
    Earnings quality             5   no accounting red flags
    Capital allocation           5   disciplined
    Institutional sponsorship    5   strong / healthy

Tiers: 90+ Elite · 80-89 High quality · 70-79 Watchlist · <70 Reject.

What these factors actually measure, and where they fall short
--------------------------------------------------------------
Everything here is derived from a single yfinance `.info` snapshot, which is
a point-in-time reading of a trailing period. That has three consequences
worth stating plainly rather than burying:

1. "Stable/increasing" margins and "growing" FCF need a time series, which
   core.longterm.fundamentals supplies from the annual statements — up to
   four fiscal years of free cash flow and operating margin. Where that
   history is present, Operating margin and Free cash flow score the level
   AND the direction, with direction adjusting by at most ±15/±20 points:
   a 45% margin eroding slowly is still a better business than an 8% margin
   improving. Where it is absent the factors fall back to the level alone
   and the detail line says "level only", because a company whose 25% margin
   is down from 40% must not read the same as one on its way up.

2. Moat is a financial fingerprint, not a moat. Brand, network effects,
   switching costs, patents and regulatory capture are qualitative
   judgments no ratio confirms. What is scored is whether the company sits
   in the top quartile of its own sector on gross margin, operating margin,
   ROE and revenue growth simultaneously — the trace a durable advantage
   tends to leave. A real moat can fail every check (early-stage network
   effects, biotech patents) and a commodity cyclical at peak margins can
   pass all four. It carries the heaviest weight in the score and the
   weakest epistemic claim, which is uncomfortable but is what the
   framework asks for.

   A check passes on EITHER an absolute elite bar OR the top quartile of
   the company's own peer group, and needing both halves is what makes it
   work. A single fixed 60% gross margin / 25% operating margin bar is a
   software bar — no distributor or grocer alive clears it, because they
   earn on inventory turns rather than markup, so against absolute cutoffs
   alone this factor scored Walmart 0 and UnitedHealth 0, which is not a
   finding about their competitive position. But peer-relative alone fails
   the other way: this library is an AI/momentum-weighted watchlist rather
   than a neutral sample, so Technology's 75th percentile sits at 39%
   revenue growth and Microsoft "failed" for growing at 18%. That is an
   artifact of the cohort, not a fact about Microsoft. Taking the easier of
   the two bars means a genuinely high-margin business is never punished
   for having high-margin competitors, and a structurally low-margin one
   can still earn credit for leading its own industry.

   Peers are `Industry` where the library holds at least MIN_PEER_MEMBERS
   of them and `Sector` otherwise — "Semiconductors" is a comparison,
   "Technology" is a filing category.

   Revenue growth is deliberately NOT one of the moat checks, though the
   framework lists it. Growth is not a moat: it says a company is selling
   more, not that it can defend its prices. It is already worth 10 points
   of its own here and feeds another 15 through EPS growth, so counting it
   a third time inside the moat would make this factor a growth score
   wearing a different name. What remains — gross margin, operating margin,
   ROE — is pricing power and capital efficiency, which is what a moat
   actually leaves behind in the numbers.

3. Capital allocation is not scored here at all, and insider activity is
   reported separately by `insider_signal()`. There is no buyback history,
   no incremental-ROIC series and no acquisition record in this data; what
   exists is six months of insider transactions, which says something about
   how a few people view the price, their tax year or their
   diversification, and close to nothing about whether the business is good.
   Folding it in docked Western Digital — a 92 — for its executives'
   personal finances, and buried the signal where it could not be weighed on
   its own. It is now a flag beside the verdict, never something that moves
   the quality gate.

   The remaining nine factors sum to 95 rather than 100. That is not a bug:
   `blend()` renormalises over the weight actually measured, so a fully
   covered row still scores 0-100 and the tier boundaries mean what they
   did. Redistributing the orphaned 5 points across the other factors would
   have silently re-weighted the framework to hide an arithmetic tidiness
   problem.

ROIC vs ROE
-----------
The framework asks for ROIC. `.info` exposes `returnOnEquity` and no
invested-capital figure, and ROE flatters leveraged balance sheets in
exactly the way ROIC is meant to prevent. Two mitigations: the factor is
capped so a >40% ROE earns no more than a 30% one (past that the number is
usually telling you about leverage or buybacks, not returns), and the
balance-sheet factor scores debt independently, so a company manufacturing
ROE with debt loses there what it gains here.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import b, band, blend, f, scale

# Below this share of measured weight the score is still reported, but the
# tier is withheld — "Elite" asserted from half the inputs is not a finding.
MIN_COVERAGE = 0.60

TIERS = ((90, "Elite"), (80, "High Quality"), (70, "Watchlist"), (0, "Reject"))
TIER_ICONS = {"Elite": "💎", "High Quality": "🟢",
              "Watchlist": "🟡", "Reject": "🔴"}

# The universe gate from §16 step 1: below this, no amount of valuation or
# chart quality reaches a buy.
MIN_OWNABLE = 70


def tier(score) -> str | None:
    if score is None:
        return None
    for floor, name in TIERS:
        if score >= floor:
            return name
    return TIERS[-1][1]


# ── individual factors ───────────────────────────────────────────────────────
# Each returns (sub_score_0_100_or_None, detail). The detail string is not
# decoration: every score in this codebase has to be able to show the
# arithmetic that produced it.

def _revenue_growth(row):
    v = f(row.get("Revenue"))                       # already a % growth rate
    if v is None:
        return None, ""
    # 20%+ is the top of the useful range; beyond it the reading is usually
    # an acquisition or a recovery off a collapsed base, not a growth rate
    # that repeats.
    sub = band(v, [(-5, 0), (0, 20), (5, 45), (10, 65), (20, 90), (999, 100)])
    return sub, f"revenue {v:+.1f}%"


def _eps_growth(row):
    v = f(row.get("EPS_Growth%"))
    if v is None:
        return None, ""
    # Capped hard at the top for the reason decision_engine documents: the
    # library's EPS growth distribution runs into five figures on
    # comparisons against a near-zero prior quarter. Rewarding 21,000% over
    # 60% ranks an accounting artifact above a real compounder.
    sub = band(v, [(-10, 0), (0, 15), (10, 45), (15, 65), (25, 85), (999, 100)])
    return sub, f"EPS {v:+.1f}%"


def _roe(row):
    v = f(row.get("ReturnOnEquity%"))
    if v is None:
        return None, ""
    # Flat above 30: past that, ROE is usually reporting leverage or a
    # buyback-shrunk equity base rather than a better business. See the
    # module docstring on ROIC.
    sub = band(v, [(0, 0), (8, 35), (15, 60), (25, 85), (30, 100), (999, 100)])
    return sub, f"ROE {v:.1f}%"


def _operating_margin(row):
    """Level, then direction. The framework asks for margins that are
    "stable or increasing", and where core.longterm.fundamentals supplied a
    multi-year history that is a measurement rather than an assumption.

    Direction adjusts rather than dominates: it is worth at most ±15 points
    on the sub-score, because a 45% margin eroding a point a year is still a
    better business than an 8% margin improving one.
    """
    v = f(row.get("OperatingMargin%"))
    if v is None:
        return None, ""
    sub = band(v, [(0, 0), (8, 35), (15, 60), (25, 85), (35, 100), (999, 100)])
    detail = f"operating margin {v:.1f}%"

    trend = f(row.get("OperatingMargin_Trend_pp"))
    if trend is None:
        return sub, detail + " (level only — no multi-year history)"

    adj = max(-15.0, min(15.0, trend * 3.0))
    direction = ("expanding" if trend >= 1 else
                 "compressing" if trend <= -1 else "stable")
    history = row.get("OperatingMargin_History")
    span = ""
    if isinstance(history, (list, tuple)):
        clean = [h for h in history if h is not None]
        if len(clean) >= 2:
            span = f" ({clean[-1]:.1f}% → {clean[0]:.1f}%)"
    return (max(0.0, min(100.0, sub + adj)),
            f"{detail}, {direction}{span} {trend:+.1f}pp")


def _free_cash_flow(row):
    """The framework asks for free cash flow "positive AND growing", which
    needs both a level and a history. Margin carries the level; the
    multi-year CAGR and the count of positive years carry the rest.

    A company with a high FCF margin whose cash flow is shrinking every year
    and one whose cash flow is compounding are not the same holding, and
    before the statement history existed this factor could not tell them
    apart.
    """
    margin = f(row.get("FCF_Margin%"))
    positive = b(row.get("FCF_Positive"))

    if margin is not None:
        sub = band(margin, [(0, 0), (5, 45), (10, 65), (15, 85), (25, 100),
                            (999, 100)])
        detail = f"FCF margin {margin:.1f}%"
    elif positive is not None:
        # A bare positive/negative flag is a much weaker reading than a
        # margin, so it does not reach the top of the scale.
        sub = 60.0 if positive else 0.0
        detail = f"FCF {'positive' if positive else 'negative'} (margin unknown)"
    else:
        return None, ""

    cagr = f(row.get("FCF_CAGR%"))
    years = f(row.get("FCF_Years"))
    pos_years = f(row.get("FCF_Positive_Years"))

    if cagr is None:
        if pos_years is not None and years:
            return sub, detail + f", positive in {pos_years:.0f} of {years:.0f} years"
        return sub, detail + " (level only — no multi-year history)"

    # Same shape as the margin trend: direction adjusts, level decides.
    adj = max(-20.0, min(20.0, band(cagr, [(-15, -20), (-5, -10), (0, -5),
                                           (5, 0), (10, 8), (20, 15),
                                           (999, 20)])))
    consistency = ""
    if pos_years is not None and years:
        consistency = f", positive in {pos_years:.0f} of {years:.0f} years"
        if pos_years < years:
            adj -= 8      # an interrupted record is not a record
    return (max(0.0, min(100.0, sub + adj)),
            f"{detail}, compounding {cagr:+.0f}%/yr{consistency}")


def _balance_sheet(row):
    """Debt burden, liquidity, and whether cash covers the debt. Averaged
    over whichever of the three are present rather than summed, so a company
    missing the current ratio is not scored as though it had a bad one."""
    de = f(row.get("DebtToEquity"))
    cr = f(row.get("CurrentRatio"))
    cash, debt = f(row.get("TotalCash")), f(row.get("TotalDebt"))
    subs, parts = [], []
    if de is not None:
        subs.append(band(de, [(25, 100), (50, 85), (100, 60), (200, 30),
                              (9999, 5)]))
        parts.append(f"D/E {de:.0f}")
    if cr is not None:
        subs.append(band(cr, [(1.0, 25), (1.5, 60), (2.0, 85), (99, 100)]))
        parts.append(f"current ratio {cr:.1f}")
    if cash is not None and debt is not None:
        if debt <= 0:
            sub, note = 100.0, "no debt"
        elif cash >= debt:
            sub, note = 100.0, "net cash"
        elif cash >= 0.5 * debt:
            sub, note = 65.0, "cash covers half the debt"
        else:
            sub, note = max(10.0, scale(cash / debt, 0.0, 0.5) * 0.5), \
                f"cash ${cash / 1e9:.1f}B vs debt ${debt / 1e9:.1f}B"
        subs.append(sub)
        parts.append(note)
    if not subs:
        return None, ""
    return sum(subs) / len(subs), "; ".join(parts)


# The moat checklist: pricing power and capital efficiency only. Revenue
# growth is excluded on purpose — see the module docstring.
_MOAT_CHECKS = (
    ("GrossMargin%", "gross margin", 60.0),
    ("OperatingMargin%", "operating margin", 25.0),
    ("ReturnOnEquity%", "ROE", 25.0),
)
MIN_MOAT_INPUTS = 2

# Where "elite for its peer group" is drawn. Top quartile: high enough that
# most of a group fails it, low enough that it is not measuring one outlier.
MOAT_PERCENTILE = 75
# A percentile drawn from a handful of names is not a peer-group judgment.
MIN_PEER_MEMBERS = 8


def _percentile(sorted_vals, pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _peer_key(row) -> tuple[str, str]:
    """(industry, sector) as clean strings."""
    return (str(row.get("Industry") or "").strip(),
            str(row.get("Sector") or "").strip())


def build_sector_stats(rows) -> dict:
    """
    Peer-group moat thresholds: the MOAT_PERCENTILE-th percentile of each
    checked metric within each industry AND each sector.

    Both granularities are built because neither alone covers the library:
    only 21 industries hold MIN_PEER_MEMBERS names (257 of 545 rows), so
    industry is used where it exists and sector catches the rest.
    "Semiconductors" is a comparison; "Technology" is a filing category, and
    the first is worth preferring wherever there are enough of them.

    rows: raw scan rows (needs Industry/Sector plus the _MOAT_CHECKS fields).
    Returns {group_name: {field: threshold, "_n": members, "_kind": ...}}.
    Groups below MIN_PEER_MEMBERS are omitted, so those companies fall back
    to the absolute bars rather than being judged against three peers.
    """
    pools: dict[str, dict[str, list]] = {}
    kinds: dict[str, str] = {}
    for row in rows:
        industry, sector = _peer_key(row)
        for name, kind in ((industry, "industry"), (sector, "sector")):
            if not name:
                continue
            kinds[name] = kinds.get(name, kind)
            bucket = pools.setdefault(name, {})
            for key, _label, _threshold in _MOAT_CHECKS:
                v = f(row.get(key))
                # Same placeholder guard as _moat(): a 0.0 gross margin is a
                # not-applicable, and pooling it drags a financial sector's
                # percentile toward a number no member of it can report.
                if key == "GrossMargin%" and v == 0.0:
                    continue
                if v is not None:
                    bucket.setdefault(key, []).append(v)

    out = {}
    for name, bucket in pools.items():
        stats = {"_n": max((len(v) for v in bucket.values()), default=0),
                 "_kind": kinds.get(name, "sector")}
        for key, vals in bucket.items():
            if len(vals) >= MIN_PEER_MEMBERS:
                stats[key] = round(_percentile(sorted(vals), MOAT_PERCENTILE), 2)
        if len(stats) > 2:
            out[name] = stats
    return out


def _peer_stats(row, sector_stats):
    """The finest peer group with enough members: industry, then sector."""
    industry, sector = _peer_key(row)
    stats = sector_stats or {}
    for name in (industry, sector):
        if name and name in stats:
            return name, stats[name]
    return None, {}


def _moat(row, sector_stats=None):
    """Absolute elite OR top quartile of the peer group, per check — the
    easier of the two bars. See the module docstring for why needing both
    halves is the point."""
    group, stats = _peer_stats(row, sector_stats)
    passed, used, notes = 0, 0, []

    for key, label, absolute in _MOAT_CHECKS:
        v = f(row.get(key))
        # A gross margin of exactly 0 is yfinance reporting that the concept
        # does not apply, not a company that sells at cost. Banks and
        # insurers have no COGS line, and reading the placeholder as a real
        # 0% scored JPMorgan's moat down for a field it cannot have.
        if key == "GrossMargin%" and v == 0.0:
            v = None
        if v is None:
            continue
        used += 1
        peer = stats.get(key)
        by_abs = v >= absolute
        by_peer = peer is not None and v >= peer
        ok = by_abs or by_peer
        if ok:
            passed += 1
        basis = ("elite outright" if by_abs
                 else "top quartile of peers" if by_peer
                 else f"needs {min(absolute, peer):.1f}%" if peer is not None
                 else f"needs {absolute:.1f}%")
        notes.append(f"{'✓' if ok else '✗'} {label} {v:.1f}% ({basis})")

    if used < MIN_MOAT_INPUTS:
        return None, ""
    # Scored on the pass RATIO, not the raw count: 2 of 2 checks available
    # is the same evidence as 3 of 3, and dividing by a fixed 3 would punish
    # a company for a missing field rather than for a missing advantage.
    ratio = passed / used
    where = (f"vs {stats['_n']} {group} peers" if group
             else "absolute bars only — no peer group")
    return ratio * 100.0, f"{passed}/{used} ({where}): " + ", ".join(notes)


def _earnings_quality(row):
    """FCF conversion — free cash flow against reported net income.

    The standard accrual check, and the one accounting red flag this data
    can actually support. Earnings a company cannot convert to cash are
    earnings that came from working-capital timing, aggressive revenue
    recognition, or capitalised costs. Below ~0.6 conversion sustained is
    the classic warning; above 1.0 the company collects more cash than it
    books as profit.
    """
    fcf = f(row.get("FreeCashFlow"))
    ni = f(row.get("NetIncome"))
    if fcf is None or ni is None:
        return None, ""
    if ni <= 0:
        # No profit to convert. Positive FCF without accounting profit is a
        # genuinely different situation from negative both — heavy D&A or
        # early-stage — and is not scored as a red flag, but it is not
        # evidence of clean earnings either.
        return (50.0 if fcf > 0 else 0.0), \
            ("FCF positive on a net loss — not an accrual flag, but no "
             "earnings to verify" if fcf > 0 else "negative FCF and net loss")
    conv = fcf / ni
    sub = band(conv, [(0.3, 0), (0.6, 30), (0.8, 60), (1.0, 85), (99, 100)])
    return sub, f"FCF/net income {conv:.2f}×"


def _capital_allocation(row):
    """Six months of insider transactions. A weak proxy — see the module
    docstring. Scored on net share flow rather than transaction count, so
    a single large purchase is not outvoted by a dozen routine option
    exercises being sold."""
    d = row.get("Insider_Buy_6m")
    if not isinstance(d, dict):
        return None, ""
    buy = f(d.get("buy_shares")) or 0.0
    sell = f(d.get("sell_shares")) or 0.0
    if buy <= 0 and sell <= 0:
        return None, ""
    net_ratio = (buy - sell) / (buy + sell)          # -1 .. +1
    sub = scale(net_ratio, -1.0, 1.0)
    direction = "net buying" if net_ratio > 0.05 else \
                "net selling" if net_ratio < -0.05 else "balanced"
    return sub, f"insiders {direction} ({net_ratio:+.2f} net share flow)"


def _institutional(row):
    own = f(row.get("Inst_Own%"))
    chg = f(row.get("Inst_Own_Chg"))
    subs, parts = [], []
    if own is not None:
        # Both tails are informative. Under 20% is neglect; over ~90% leaves
        # no marginal buyer and turns any disappointment into a crowded exit.
        subs.append(band(own, [(20, 25), (40, 60), (60, 90), (85, 100),
                               (999, 70)]))
        parts.append(f"{own:.0f}% held")
    if chg is not None:
        subs.append(band(chg, [(-2, 10), (0, 40), (1, 70), (3, 90), (999, 100)]))
        parts.append(f"{chg:+.2f}% change")
    if not subs:
        return None, ""
    return sum(subs) / len(subs), "; ".join(parts)


# Insider activity is NOT one of these. It is reported alongside as its own
# signal — see insider_signal() and the module docstring.
FACTORS = (
    ("EPS growth", 15, _eps_growth),
    ("Competitive moat", 15, _moat),
    ("Revenue growth", 10, _revenue_growth),
    ("ROE", 10, _roe),
    ("Operating margin", 10, _operating_margin),
    ("Free cash flow", 10, _free_cash_flow),
    ("Balance sheet", 10, _balance_sheet),
    ("Earnings quality", 5, _earnings_quality),
    ("Institutional sponsorship", 5, _institutional),
)

# Factors whose evidence is materially thinner than their weight suggests.
# Surfaced on the result so the UI can mark them rather than presenting them
# all as equally well-founded.
WEAK_PROXIES = ("Competitive moat",)

INSIDER_BANDS = ((0.25, "Net buying"), (-0.05, "Balanced"),
                 (-0.5, "Net selling"), (-1.01, "Heavy net selling"))


def insider_signal(row: dict) -> dict:
    """Six months of insider transactions, reported on its own.

    Deliberately NOT folded into LQuality. Insider selling says something
    about how a handful of people view the price, their tax year, or their
    diversification — and close to nothing about whether the business is
    good. Western Digital carries a 92 LQuality with insiders selling
    89,994 net shares; scoring that inside the quality number docked an
    elite business several points for a fact about its executives' personal
    finances, and worse, buried the signal where nobody could weigh it
    separately.

    Kept as a flag the decision layer can show next to the verdict, never as
    something that moves the quality gate.
    """
    d = row.get("Insider_Buy_6m")
    if not isinstance(d, dict):
        return {"label": None, "net_ratio": None, "detail": "no insider data"}
    buy = f(d.get("buy_shares")) or 0.0
    sell = f(d.get("sell_shares")) or 0.0
    if buy <= 0 and sell <= 0:
        return {"label": None, "net_ratio": None,
                "detail": "no insider transactions in the last 6 months"}
    ratio = (buy - sell) / (buy + sell)
    label = next(name for floor, name in INSIDER_BANDS if ratio >= floor)
    net = f(d.get("net_shares"))
    detail = (f"{label.lower()}"
              + (f" ({abs(net):,.0f} net shares)" if net else "")
              + f" — {int(buy):,} bought vs {int(sell):,} sold")
    return {"label": label, "net_ratio": round(ratio, 2), "detail": detail,
            "negative": ratio < -0.05}


def compute_lquality(row: dict, sector_stats: dict | None = None) -> dict:
    """
    row: a scan / research `raw` row (the yfinance-derived column names).
    sector_stats: build_sector_stats() output. Optional — without it the
    moat factor falls back to fixed thresholds, which systematically
    penalises low-margin business models (see the module docstring).

    Returns {"score", "tier", "tier_icon", "coverage", "reliable",
             "components", "missing", "weak_proxies", "ownable"} where
    `ownable` is the §16 universe gate — score >= MIN_OWNABLE on reliable
    coverage — and is what the engine's quality gate reads.
    """
    parts = []
    for name, weight, fn in FACTORS:
        sub, detail = (fn(row, sector_stats) if fn is _moat else fn(row))
        parts.append((name, weight, sub, detail))

    out = blend(parts)
    reliable = out["coverage"] >= MIN_COVERAGE
    score = out["score"]
    # The tier is a claim about the company; it is withheld rather than
    # guessed when too little of the company was measured.
    label = tier(score) if reliable else None
    out.update({
        "tier": label,
        "tier_icon": TIER_ICONS.get(label, "⚪"),
        "reliable": reliable,
        "weak_proxies": [c["name"] for c in out["components"]
                         if c["name"] in WEAK_PROXIES],
        "ownable": bool(reliable and score is not None and score >= MIN_OWNABLE),
    })
    return out
