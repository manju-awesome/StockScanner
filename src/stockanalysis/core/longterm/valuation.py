"""
valuation.py — what the price requires you to believe
=====================================================
The second gate. A quality company bought at any price is not an investment,
so LQuality alone must never reach a buy; this module is what lets the engine
refuse an elite business at an absurd multiple.

    REVERSE_DCF   what FCF growth rate the CURRENT price already assumes,
                  against what the company actually delivers
    PEER          the stock's own forward earnings priced at a
                  growth-adjusted sector median multiple, from the library

Both produce the same shape: a band, a distance, and the reasoning.

    🟢 UNDERVALUED   the price demands less than the company delivers
    🟡 FAIR          roughly what it delivers
    🔴 OVERVALUED    materially more than it delivers

Why REVERSE, and not a fair value
---------------------------------
A forward DCF was built here first and produced numbers that were arithmetic
rather than valuation. Fed correct free cash flow, across 29 quality
mega-caps it returned a median of -58% and called 21 of 24 OVERVALUED:
Microsoft worth $176 against $500, Nvidia $48 against $224, Lilly -85%. That
is not a bug — it is what a textbook two-stage model with a GDP-rate terminal
value says about companies trading at 40-60x free cash flow. But as a GATE it
is useless. A filter that rejects ninety percent of the universe is not
filtering; every quality name becomes WAIT and the engine returns an empty
list.

The failure is structural, and tuning the assumptions until the distribution
centres on zero would only hide it: a discounted cash flow is a lever with a
very long handle, where one point of discount rate moves the answer 10-20%
and the terminal value is usually 60-80% of the total. Any fair value it
prints is really a statement about the analyst's assumptions wearing the
costume of a statement about the company.

So the model is run backwards. Instead of assuming a growth rate and solving
for value, it takes the price the market is quoting, and solves for the
five-year free-cash-flow growth rate that price already requires. That
number is then compared with what the company has actually been doing.

    "At $500, Microsoft's price requires 28% FCF growth for five years.
     It has compounded FCF at 4% and grew revenue 18% last year."

This is a falsifiable statement about the company rather than an
unfalsifiable one about the model. The discount rate still matters, but it
now moves the *hurdle* rather than the verdict, and the hurdle is reported
alongside so it can be argued with. Crucially it also produces a real spread
of verdicts, because companies genuinely differ in how much growth their
price demands.

Why the peer fallback is not a lesser answer
--------------------------------------------
A free-cash-flow model on a bank is arithmetic, not valuation: for a
financial, cash flow from operations is a financing artifact and free cash
flow has no claim-on-owners meaning. The same holds for REITs, which are
valued on FFO. For those sectors, and for any company not generating
positive free cash flow, the peer multiple is the CORRECT method, not a
degraded one. What would be wrong is running the cash-flow model anyway and
presenting its output with the same confidence.

Assumptions are outputs, not constants
--------------------------------------
The discount rate, the terminal rate and every growth figure the comparison
rests on are returned on every result. A valuation whose assumptions are not
visible cannot be argued with, and one that cannot be argued with should not
gate a buy decision.
"""

from __future__ import annotations

import statistics

from stockanalysis.core.longterm._common import f, s

# Distance from fair value that separates the three bands, in percent.
# Used by the peer method, which does produce a fair value.
MARGIN = 10.0

BANDS = ("UNDERVALUED", "FAIR", "OVERVALUED")
BAND_ICONS = {"UNDERVALUED": "🟢", "FAIR": "🟡", "OVERVALUED": "🔴"}

# What the band is allowed to CLAIM, per method. The peer multiple compares a
# company to its sector; it cannot say what the business is worth, only what
# it costs relative to its neighbours. Labelling that "Undervalued" asserts
# an intrinsic judgment the method never made — Western Digital reading
# "🟢 Undervalued, fair value $597.95" is really "cheaper than its peers on a
# 18.8x multiple", which is useful and is a different sentence.
BAND_WORDING = {
    "REVERSE_DCF": {"UNDERVALUED": "Undervalued", "FAIR": "Fair value",
                    "OVERVALUED": "Overvalued"},
    "PEER": {"UNDERVALUED": "Relatively undervalued",
             "FAIR": "In line with peers",
             "OVERVALUED": "Relatively expensive"},
}

# Confidence is a property of the METHOD and its inputs, not of the answer.
CONFIDENCE_ICONS = {"HIGH": "🟢", "MODERATE": "🟡", "LOW": "🔴"}


def _confidence(method: str, row: dict) -> tuple[str, str]:
    """How much weight the valuation can bear, and why.

    A cash-flow model built on four years of filed statements is a stronger
    claim than one extrapolating a single year, and both are stronger than a
    sector multiple — which inherits whatever the sector is collectively
    wrong about and says nothing about intrinsic worth. Surfacing this stops
    a 🟢 from reading identically whether it came from audited cash flows or
    from a peer group of eleven.
    """
    if method == "REVERSE_DCF":
        years = f(row.get("FCF_Years")) or 0
        if years >= 3:
            return "HIGH", (f"cash-flow model on {years:.0f} years of filed "
                            f"statements")
        return "MODERATE", ("cash-flow model, but less than three years of "
                            "statement history to compound")
    if method == "PEER":
        return "MODERATE", ("relative value only — no cash-flow model was "
                            "possible, so this prices the company against "
                            "its sector rather than against its earnings")
    return "LOW", "no valuation method applied"

# ── Reverse-DCF bands ────────────────────────────────────────────────────────
# The comparison is a GAP in percentage points (implied minus delivered), not
# a ratio of the two. A ratio reads naturally — "the price wants twice the
# growth" — and is wrong wherever either side goes negative: a company
# shrinking 10% a year, priced to shrink only 5%, produces a ratio of -1.67,
# which lands in the "asks less than delivered" bucket and reports a business
# in decline as UNDERVALUED. The price is in fact demanding a better outcome
# than the company is producing. A signed gap gets that right at every sign,
# and needs no special-casing around zero.
#
# Tolerance scales with the delivered rate, because 5 points of disagreement
# is a rounding error against 30% growth and a verdict against 3%.
TOLERANCE_FRACTION = 0.30
TOLERANCE_FLOOR_PP = 3.0
# No company sustains a triple-digit rate for five years, so a delivered
# figure above this is a base effect or a recovery off a collapsed prior
# year, not a demonstrated capability to underwrite against.
MAX_CREDIBLE_DELIVERED = 40.0
# A price requiring more than this is not a forecast anyone should underwrite,
# whatever the company currently delivers.
IMPLAUSIBLE_IMPLIED_GROWTH = 35.0

# ── DCF assumptions ──────────────────────────────────────────────────────────
STAGE1_YEARS = 5
# Long-run NOMINAL GDP: ~2% real plus ~2% inflation. It has to be nominal
# because everything it is used with is — free cash flow is projected in
# nominal dollars and discounted at a nominal rate (a nominal risk-free plus
# an equity risk premium). An earlier 2.5% here was a real growth rate
# quietly mixed into a nominal model, which shrank the terminal multiple from
# 18x to 14x and made every company in the library look expensive by
# construction.
TERMINAL_GROWTH = 0.04
EQUITY_RISK_PREMIUM = 0.05
DEFAULT_RISK_FREE = 0.042    # used only when the live 10Y is unavailable
MIN_DISCOUNT = 0.07          # floor: a sub-7% cost of equity on a listed
                             # equity produces fantasy valuations
MAX_DISCOUNT = 0.15

# Sectors where free cash flow is not a meaningful claim on owners.
DCF_EXCLUDED_SECTORS = ("financial services", "financial", "financials",
                        "real estate")

# Peer-relative guards. A sector median drawn from three names is not a
# market judgment, and a fair multiple untethered from its sector's is just
# the stock's own multiple restated.
MIN_PEERS = 6
PEER_MULTIPLE_FLOOR = 0.5    # of the sector median
PEER_MULTIPLE_CAP = 2.0
GROWTH_DAMPING = 0.5         # exponent: multiples scale with the SQUARE ROOT
                             # of relative growth, not linearly with it
EXTREME_PEER_GAP = 75.0      # beyond this the reading is flagged as a
                             # question rather than presented as a target


def _band(upside_pct: float | None) -> str | None:
    """upside_pct: how far fair value sits above the current price."""
    if upside_pct is None:
        return None
    if upside_pct >= MARGIN:
        return "UNDERVALUED"
    if upside_pct <= -MARGIN:
        return "OVERVALUED"
    return "FAIR"


def _discount_rate(beta: float | None, risk_free: float) -> tuple[float, str]:
    """CAPM cost of equity, clamped. Beta is the only company-specific input
    available; without it the market's own rate is used and the assumption
    is named in the returned note rather than hidden."""
    if beta is None:
        r = risk_free + EQUITY_RISK_PREMIUM
        note = f"beta unavailable — market cost of equity {r * 100:.1f}%"
    else:
        r = risk_free + beta * EQUITY_RISK_PREMIUM
        note = (f"CAPM: {risk_free * 100:.1f}% risk-free + "
                f"{beta:.2f}β × {EQUITY_RISK_PREMIUM * 100:.0f}% ERP")
    clamped = max(MIN_DISCOUNT, min(MAX_DISCOUNT, r))
    if abs(clamped - r) > 1e-9:
        note += f" → clamped to {clamped * 100:.1f}%"
    return clamped, note


def _shares(row: dict) -> float | None:
    """Share count, backed out of market cap where yfinance omits it.

    `sharesOutstanding` is missing often enough to matter (Exxon, among
    others), and market cap divided by price is the definition rather than
    an estimate — arithmetic on two fields the row already carries.
    """
    shares = f(row.get("SharesOutstanding"))
    if shares and shares > 0:
        return shares
    cap = f(row.get("MarketCap"))
    price = f(row.get("Current Price")) or f(row.get("price"))
    if cap and price and price > 0:
        return cap / price
    return None


def _dcf_blocked(row: dict) -> str | None:
    """Why a DCF must not be run here. Returns the reason, or None to
    proceed."""
    sector = (s(row.get("Sector")) or "").lower()
    if any(x in sector for x in DCF_EXCLUDED_SECTORS):
        return (f"{s(row.get('Sector'))} — free cash flow is not a claim on "
                f"owners for this sector; priced on peers instead")
    fcf = f(row.get("FreeCashFlow"))
    if fcf is None:
        return "free cash flow not available"
    if fcf <= 0:
        return f"free cash flow negative (${fcf / 1e9:.2f}B) — nothing to discount"
    if _shares(row) is None:
        return "share count not available, and no market cap to back it out of"
    return None


def _equity_value(fcf0, growth, discount, net_cash):
    """Two-stage model: present value of the explicit cash flows, plus a
    perpetuity terminal value, plus net cash.

    Growth is held CONSTANT through the explicit window and drops to the
    terminal rate after it. An earlier version faded it linearly to terminal
    by year 5, which is a defensible forward model but makes the inverse
    uninterpretable: solving it returned "requires 76% growth" for a path
    that only grew at 76% in year one and at 4% by year five. Since the
    entire purpose of running this backwards is to state a rate a human can
    compare against what a company actually does, the rate has to mean what
    it says.
    """
    if discount <= TERMINAL_GROWTH:
        return None
    pv, fcf = 0.0, fcf0
    for year in range(1, STAGE1_YEARS + 1):
        fcf *= (1 + growth)
        pv += fcf / (1 + discount) ** year
    terminal = fcf * (1 + TERMINAL_GROWTH) / (discount - TERMINAL_GROWTH)
    pv += terminal / (1 + discount) ** STAGE1_YEARS
    return pv + net_cash


# Bounds for the solver. The lower bound is deeply negative because a
# declining business genuinely can be priced for shrinkage, and clamping at
# zero would report "requires 0% growth" for every melting ice cube.
SOLVE_LOW, SOLVE_HIGH = -0.50, 2.00
SOLVE_ITERATIONS = 60


def _implied_growth(market_equity, fcf0, discount, net_cash):
    """Solve for the stage-1 growth rate that makes the model equal today's
    market capitalisation.

    Bisection rather than a closed form: the two-stage model with a linear
    growth fade has no clean inverse, and 60 iterations of bisection over a
    monotonic function costs nothing and cannot diverge the way
    Newton-Raphson can near the terminal-rate singularity.
    """
    lo, hi = SOLVE_LOW, SOLVE_HIGH
    v_lo = _equity_value(fcf0, lo, discount, net_cash)
    v_hi = _equity_value(fcf0, hi, discount, net_cash)
    if v_lo is None or v_hi is None:
        return None
    # Outside the bracket there is no answer to report, and extrapolating
    # past it would invent one.
    if market_equity < v_lo or market_equity > v_hi:
        return None
    for _ in range(SOLVE_ITERATIONS):
        mid = (lo + hi) / 2
        v = _equity_value(fcf0, mid, discount, net_cash)
        if v is None:
            return None
        if v < market_equity:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _delivered_growth(row: dict) -> tuple[float | None, str]:
    """What the company has actually been doing, in percent.

    The higher of the multi-year free-cash-flow CAGR and the revenue CAGR.

    FCF CAGR is the like-for-like comparison — it is the same quantity the
    implied figure is a rate of — but taken alone it penalises precisely the
    companies investing hardest. Microsoft compounded free cash flow at 4%
    across four years while revenue compounded at 16%, because capital
    expenditure went from $28B to $116B building AI capacity. Reading that
    as "delivers 4%" treats a deliberate investment cycle as a deteriorating
    business.

    Over a five-year horizon free cash flow growth is bounded by revenue
    growth once an investment cycle normalises, so revenue is the fair
    ceiling and taking the lower of the two would be the harsher error. Both
    are reported, so a reader can see when they disagree and why.
    """
    fcf_cagr = f(row.get("FCF_CAGR%"))
    rev_cagr = f(row.get("Revenue_CAGR%"))
    rev_yoy = f(row.get("Revenue"))

    candidates = [(v, k) for v, k in ((fcf_cagr, "free cash flow"),
                                      (rev_cagr, "revenue")) if v is not None]
    if candidates:
        best, which = max(candidates)
        years = f(row.get("FCF_Years"))
        span = f" over {years:.0f} years" if years else ""
        if len(candidates) == 2:
            note = (f"{which} compounded {best:+.0f}%/yr{span} — free cash "
                    f"flow {fcf_cagr:+.0f}%, revenue {rev_cagr:+.0f}%"
                    + (", so the cash-flow figure is being held down by "
                       "capital spending" if which == "revenue"
                       and fcf_cagr < rev_cagr - 5 else ""))
        else:
            note = f"{which} compounded {best:+.0f}%/yr{span}"
        return best, note

    if rev_yoy is not None:
        return rev_yoy, (f"revenue grew {rev_yoy:+.0f}% last year — no "
                         f"multi-year statement history to compound")
    return None, ""


def _reverse_dcf(row: dict, risk_free: float) -> dict | None:
    fcf0 = f(row.get("FreeCashFlow"))
    shares = _shares(row)
    price = f(row.get("Current Price")) or f(row.get("price"))
    if not fcf0 or not shares or not price:
        return None
    cash = f(row.get("TotalCash")) or 0.0
    debt = f(row.get("TotalDebt")) or 0.0
    net_cash = cash - debt
    discount, discount_note = _discount_rate(f(row.get("Beta")), risk_free)

    market_equity = price * shares
    implied = _implied_growth(market_equity, fcf0, discount, net_cash)
    if implied is None:
        return None
    implied_pct = implied * 100

    delivered_raw, delivered_note = _delivered_growth(row)
    if delivered_raw is None:
        return None

    delivered = min(delivered_raw, MAX_CREDIBLE_DELIVERED)
    capped = delivered != delivered_raw

    gap = implied_pct - delivered
    tolerance = max(TOLERANCE_FLOOR_PP, abs(delivered) * TOLERANCE_FRACTION)

    # Two independent routes to OVERVALUED, and which one fired changes
    # what the verdict sentence may truthfully claim.
    implausible = implied_pct >= IMPLAUSIBLE_IMPLIED_GROWTH
    if implausible:
        band = "OVERVALUED"
    elif gap > tolerance:
        band = "OVERVALUED"
    elif gap < -tolerance:
        band = "UNDERVALUED"
    else:
        band = "FAIR"

    # The sentence has to name the rule that actually decided the band.
    # When the implausible-growth ceiling is what forced OVERVALUED and
    # the gap was inside tolerance, "demands more growth than delivered"
    # is simply false — IDXX reached here with a gap of -1.8, meaning the
    # price demanded LESS than delivered, under a sentence saying more.
    if implausible and gap <= tolerance:
        verdict = (f"requires growth above the "
                   f"{IMPLAUSIBLE_IMPLIED_GROWTH:.0f}% ceiling any forecast "
                   f"should carry, whatever the company currently delivers")
    elif band == "OVERVALUED":
        verdict = "demands more growth than the company has delivered"
    elif band == "UNDERVALUED":
        verdict = "demands less growth than the company has delivered"
    else:
        verdict = "demands roughly what the company has delivered"

    assumptions = [
        f"At ${price:,.2f} the price requires {implied_pct:.0f}% free-cash-flow "
        f"growth every year for {STAGE1_YEARS} years, then "
        f"{TERMINAL_GROWTH * 100:.1f}% (long-run nominal GDP) forever",
        f"The company has delivered {delivered_raw:+.0f}% — {delivered_note}"
        + (f", treated as {MAX_CREDIBLE_DELIVERED:.0f}% because no company "
           f"sustains that rate for five years" if capped else ""),
        f"Gap {gap:+.0f} points against a ±{tolerance:.0f}-point tolerance "
        f"(scaled to the delivered rate)",
        f"Free cash flow ${fcf0 / 1e9:.2f}B"
        + (f" as of {row.get('Fundamentals_As_Of')}"
           if row.get("Fundamentals_As_Of") else ""),
        f"Discount rate {discount * 100:.1f}% ({discount_note}) — this moves "
        f"the hurdle, not the verdict",
        f"Net {'cash' if net_cash >= 0 else 'debt'} ${abs(net_cash) / 1e9:.1f}B, "
        f"{shares / 1e6:,.0f}M shares",
    ]
    if implied_pct >= IMPLAUSIBLE_IMPLIED_GROWTH:
        assumptions.append(
            f"Above {IMPLAUSIBLE_IMPLIED_GROWTH:.0f}% implied growth the price "
            f"is not underwriting a forecast anyone should take, whatever the "
            f"company currently delivers")

    return {
        "method": "REVERSE_DCF",
        "band": band,
        "implied_growth_pct": round(implied_pct, 1),
        "delivered_growth_pct": round(delivered_raw, 1),
        "growth_gap_pp": round(gap, 1),
        "tolerance_pp": round(tolerance, 1),
        # The delivered figure printed here MUST be the one the gap was
        # computed against. Printing the raw rate beside a verdict derived
        # from the capped one produced a headline that contradicted
        # itself on ten names — "requires 55%; delivers +194% — demands
        # more growth than the company has delivered" (NVDA).
        "headline": (f"Price requires {implied_pct:.0f}% growth a year; the "
                     f"company delivers {delivered_raw:+.0f}%"
                     + (f" (credited at {delivered:.0f}%, the sustainable "
                        f"ceiling)" if capped else "")
                     + f" — {verdict}"),
        "delivered_credited_pct": round(delivered, 1),
        "delivered_capped": capped,
        "implausible_growth": implausible,
        "assumptions": assumptions,
        # No fair value: this method deliberately does not produce one.
        "fair_value": None, "fair_low": None, "fair_high": None,
    }


def build_peer_stats(rows) -> dict:
    """Sector medians for the peer-relative method, computed once over the
    whole library rather than per row.

    rows: the raw scan rows (needs Sector, Forward_PE, and a growth rate).
    Returns {sector: {"pe": median, "growth": median, "n": int}}.

    Only positive forward multiples are pooled. A negative forward P/E means
    no expected earnings, which is not a cheap multiple — including it drags
    the sector median toward zero and makes every peer in that sector look
    expensive against a number no company could trade at.
    """
    by_sector: dict[str, dict[str, list]] = {}
    for row in rows:
        sector = s(row.get("Sector"))
        pe = f(row.get("Forward_PE"))
        if not sector or pe is None or pe <= 0 or pe > 200:
            continue
        bucket = by_sector.setdefault(sector, {"pe": [], "growth": []})
        bucket["pe"].append(pe)
        g = f(row.get("EPS_Growth%"))
        if g is not None:
            bucket["growth"].append(g)
    out = {}
    for sector, bucket in by_sector.items():
        if len(bucket["pe"]) < MIN_PEERS:
            continue
        out[sector] = {
            "pe": round(statistics.median(bucket["pe"]), 2),
            "growth": (round(statistics.median(bucket["growth"]), 1)
                       if bucket["growth"] else None),
            "n": len(bucket["pe"]),
        }
    return out


def _peer(row: dict, peers: dict) -> dict | None:
    sector = s(row.get("Sector"))
    stats = (peers or {}).get(sector or "")
    pe = f(row.get("Forward_PE"))
    price = f(row.get("Current Price")) or f(row.get("price"))
    if not stats or pe is None or pe <= 0 or price is None or price <= 0:
        return None

    # Forward EPS backed out of the multiple the row already carries, rather
    # than fetched: price / forward P/E is the definition, so this is
    # arithmetic on present data, not an estimate.
    forward_eps = price / pe
    median_pe = stats["pe"]
    fair_pe, growth_note = median_pe, f"sector median {median_pe:.1f}×"

    # The same credibility cap the reverse DCF applies to delivered growth,
    # and for the same reason. AbbVie's +361% EPS growth is a base effect off
    # a collapsed prior year; uncapped it drove the damped ratio straight
    # into PEER_MULTIPLE_CAP and priced the company at 32.6x against a
    # sector median of 16x, reporting it 117% undervalued. A one-off
    # comparison must not license a doubled multiple.
    g_stock = f(row.get("EPS_Growth%"))
    if g_stock is not None:
        g_stock = min(g_stock, MAX_CREDIBLE_DELIVERED)
    g_sector = stats.get("growth")
    if g_sector is not None:
        g_sector = min(g_sector, MAX_CREDIBLE_DELIVERED)
    if g_stock is not None and g_sector is not None and g_sector > 1.0 \
            and g_stock > 0:
        # Damped, because forward multiples empirically scale with roughly
        # the square root of relative growth, not linearly. The linear
        # version hands a company growing 4× its sector a 4× multiple, which
        # is how a DCF-free model talks itself into any price.
        ratio = (g_stock / g_sector) ** GROWTH_DAMPING
        fair_pe = median_pe * max(PEER_MULTIPLE_FLOOR,
                                  min(PEER_MULTIPLE_CAP, ratio))
        growth_note = (f"sector median {median_pe:.1f}× adjusted for growth "
                       f"{g_stock:+.0f}% vs sector {g_sector:+.0f}% "
                       f"→ {fair_pe:.1f}×")

    fair = fair_pe * forward_eps
    if fair <= 0:
        return None

    upside = (fair / price - 1) * 100.0
    band = _band(upside)
    # The floor/cap on the multiple is the honest range here — the sector
    # median itself is the uncertainty, so the band is drawn at ±20% of the
    # fair multiple rather than from a discount rate this method never used.
    lo, hi = round(fair * 0.8, 2), round(fair * 1.2, 2)
    straddles = lo <= price <= hi
    notes = []
    # A multiple this far from its peer group is almost never free money.
    # It usually reflects something the comparison cannot see — earnings in
    # decline, a cyclical peak that makes the E in the P/E temporary, or a
    # business only nominally in the same sector. The band still stands (the
    # stock IS cheap against its peers) but the magnitude is not a target,
    # and the library's tail runs past +300%, which read as a price forecast
    # would be absurd.
    if abs(upside) >= EXTREME_PEER_GAP:
        notes.append(
            f"{abs(upside):.0f}% from the peer-implied value — a gap this "
            f"wide usually means the comparison is missing something "
            f"(declining earnings, peak-cycle margins, or a business not "
            f"really comparable to its sector). Treat it as a flag to "
            f"investigate, not a price target.")
    if straddles and band != "FAIR":
        # The point estimate says one thing and its own error bar disagrees.
        # Deferring to the range is the only reading that does not pretend
        # the model is more precise than it is.
        notes.append(f"Fair-value range ${lo:,.2f}–${hi:,.2f} contains the "
                     f"current price — treated as FAIR despite a "
                     f"{upside:+.0f}% point estimate")
        band = "FAIR"

    return {
        "method": "PEER",
        "band": band,
        "fair_value": round(fair, 2),
        "fair_low": lo,
        "fair_high": hi,
        "upside_pct": round(upside, 1),
        "straddles": straddles,
        "headline": (f"Fair value ${fair:,.2f} against ${price:,.2f} "
                     f"({upside:+.0f}%) on a {fair_pe:.1f}× multiple"),
        "assumptions": [
            f"Forward EPS ${forward_eps:.2f} (price ÷ forward P/E {pe:.1f}×)",
            f"Fair multiple {fair_pe:.1f}× — {growth_note}",
            f"{stats['n']} {sector} peers in the library",
            "Relative value, not intrinsic value — this prices the company "
            "against its sector, so it inherits whatever the sector is "
            "collectively wrong about",
        ],
        "notes": notes,
    }


def compute_valuation(row: dict, peers: dict | None = None,
                      risk_free: float = DEFAULT_RISK_FREE) -> dict:
    """
    row: a scan / research `raw` row. `peers`: build_peer_stats() output.
    `risk_free`: the 10-year yield as a DECIMAL (0.042 for 4.2%).

    Tries the reverse DCF first and falls back to the peer multiple. The two
    methods answer with different evidence — one an implied growth rate, the
    other a fair value — so the result carries whichever fields its method
    produced and `method` says which was used. Both always set `band`.

    `acceptable` is the gate the engine reads: True for UNDERVALUED/FAIR,
    False for OVERVALUED, None when the company could not be priced at all.
    Three states, because "we could not value it" is not the same answer as
    "the price is fine".
    """
    price = f(row.get("Current Price")) or f(row.get("price"))
    notes = []

    result = None
    blocked = _dcf_blocked(row)
    if blocked:
        notes.append(f"Cash-flow model not used: {blocked}")
    else:
        result = _reverse_dcf(row, risk_free)
        if result is None:
            notes.append("Reverse DCF could not solve — the price sits "
                         "outside the range of growth rates the model spans, "
                         "or there is no growth history to compare against")

    if result is None:
        result = _peer(row, peers or {})
        if result is None:
            notes.append("No sector peer group with enough members, and no "
                         "positive forward multiple")

    if result is None or price is None or price <= 0 or not result.get("band"):
        return {"method": None, "band": None, "band_icon": "⚪",
                "band_label": None, "confidence": "LOW",
                "confidence_icon": "🔴",
                "confidence_note": "no valuation method applied",
                "price": price, "acceptable": None, "headline": None,
                "fair_value": None, "fair_low": None, "fair_high": None,
                "upside_pct": None, "implied_growth_pct": None,
                "delivered_growth_pct": None, "growth_gap_pp": None,
                "tolerance_pp": None, "straddles": None,
                "assumptions": [], "notes": notes}

    band = result["band"]
    method = result["method"]
    confidence, confidence_note = _confidence(method, row)
    return {
        "method": method,
        "band": band,
        "band_icon": BAND_ICONS.get(band, "⚪"),
        # What this method is entitled to claim — "Relatively undervalued"
        # for a peer comparison, "Undervalued" only for a cash-flow model.
        "band_label": BAND_WORDING.get(method, {}).get(
            band, band.title() if band else None),
        "confidence": confidence,
        "confidence_icon": CONFIDENCE_ICONS.get(confidence, "⚪"),
        "confidence_note": confidence_note,
        "price": price,
        "acceptable": band in ("UNDERVALUED", "FAIR"),
        "headline": result.get("headline"),
        # Reverse-DCF fields — None on a PEER result, and vice versa. The UI
        # renders whichever the method produced rather than inventing the
        # other, because a fair value this method never computed would be a
        # number nobody can defend.
        "implied_growth_pct": result.get("implied_growth_pct"),
        "delivered_growth_pct": result.get("delivered_growth_pct"),
        # The rate the GAP was actually measured against — the raw delivered
        # figure capped at the sustainable ceiling. Anything that reasons
        # about the band downstream (the buy zones price a "demands only what
        # it delivers" level off it) has to use this one, or NVDA's +194%
        # would price that level in the thousands.
        "delivered_credited_pct": result.get("delivered_credited_pct"),
        "growth_gap_pp": result.get("growth_gap_pp"),
        "tolerance_pp": result.get("tolerance_pp"),
        "fair_value": result.get("fair_value"),
        "fair_low": result.get("fair_low"),
        "fair_high": result.get("fair_high"),
        "upside_pct": result.get("upside_pct"),
        "straddles": result.get("straddles"),
        "assumptions": result.get("assumptions", []),
        "notes": notes + list(result.get("notes") or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# READING THE MODEL AT A DIFFERENT PRICE
# ─────────────────────────────────────────────────────────────────────────────
# Both of these run the SAME two-stage model as the verdict above, at a price
# other than today's. That is why they are here and not in a caller: the
# moment a second module reimplements the discount rate or the share count,
# the buy zones start disagreeing with the band printed beside them.
#
# Note what these deliberately are NOT. `_reverse_dcf` refuses to publish a
# fair value, because the method's output is a RATE and turning it into a
# price would imply the model knows what growth to expect. These two dodge
# that: one reports the rate at a hypothetical price, the other reports the
# price at a rate the CALLER supplies. Neither invents a growth forecast, and
# every figure they produce has to be shown with its condition attached.

def _dcf_inputs(row: dict, risk_free: float):
    """(fcf0, shares, discount, net_cash) or None when the model can't run."""
    fcf0, shares = f(row.get("FreeCashFlow")), _shares(row)
    if not fcf0 or not shares:
        return None
    net_cash = (f(row.get("TotalCash")) or 0.0) - (f(row.get("TotalDebt")) or 0.0)
    discount, _note = _discount_rate(f(row.get("Beta")), risk_free)
    return fcf0, shares, discount, net_cash


def implied_growth_at_price(row: dict, price: float,
                            risk_free: float = DEFAULT_RISK_FREE):
    """The growth rate the model would require if the stock traded at `price`.

    "What would this have to grow at to justify $148?" — the same question
    the verdict answers at today's price, which is what makes the two
    comparable.
    """
    inputs = _dcf_inputs(row, risk_free)
    if not inputs or not price or price <= 0:
        return None
    fcf0, shares, discount, net_cash = inputs
    implied = _implied_growth(price * shares, fcf0, discount, net_cash)
    return None if implied is None else round(implied * 100, 1)


def price_at_implied_growth(row: dict, growth_pct: float,
                            risk_free: float = DEFAULT_RISK_FREE):
    """The price at which the model would require exactly `growth_pct`.

    The forward direction of the same model, so no solver: value the cash
    flows at that rate and divide by shares. `growth_pct` is supplied by the
    caller — usually the rate the company has actually delivered — so the
    result is always "the price that asks only for X", never "the price this
    is worth".
    """
    inputs = _dcf_inputs(row, risk_free)
    if not inputs or growth_pct is None:
        return None
    fcf0, shares, discount, net_cash = inputs
    equity = _equity_value(fcf0, growth_pct / 100.0, discount, net_cash)
    if equity is None or equity <= 0:
        return None
    return round(equity / shares, 2)


# ─────────────────────────────────────────────────────────────────────────────
# EXPECTED RETURN
# ─────────────────────────────────────────────────────────────────────────────
# "Is it undervalued" is a yes/no about today. "What would I earn from here"
# is the question a long-term buyer is actually asking, and it has a number.
#
# The identity these rest on is the DCF's own: a discounted cash-flow model
# says that paying its computed value earns you the discount rate. Pay less
# and you multiply your terminal wealth by (value / price); pay more and you
# divide by it. Annualised over the horizon:
#
#     expected CAGR = (1 + discount) x (value / price)^(1/years) - 1
#     price for a target CAGR = value x ((1 + discount) / (1 + target))^years
#
# No new assumptions beyond the model that produced `value` — but that model
# assumes the business compounds at its DELIVERED rate and that the market
# eventually pays the model's number for it. The second half is the strong
# one: a company the market has priced above this model for a decade may
# simply keep being priced above it. Every figure these produce has to be
# shown as conditional on that, never as a forecast.

RETURN_HORIZON_YEARS = 5


def expected_cagr_at_price(row: dict, price: float, growth_pct: float,
                           risk_free: float = DEFAULT_RISK_FREE,
                           years: int = RETURN_HORIZON_YEARS):
    """Annualised return from buying at `price`, if the business compounds at
    `growth_pct` and the market ends up paying this model's value for it."""
    inputs = _dcf_inputs(row, risk_free)
    if not inputs or not price or price <= 0 or growth_pct is None:
        return None
    fcf0, shares, discount, net_cash = inputs
    equity = _equity_value(fcf0, growth_pct / 100.0, discount, net_cash)
    if equity is None or equity <= 0:
        return None
    value = equity / shares
    return round(((1 + discount) * (value / price) ** (1 / years) - 1) * 100, 1)


def price_for_expected_cagr(row: dict, target_cagr_pct: float,
                            growth_pct: float,
                            risk_free: float = DEFAULT_RISK_FREE,
                            years: int = RETURN_HORIZON_YEARS):
    """The price at which the expected return would equal `target_cagr_pct`.
    The inverse of the function above, and the one that turns a return hurdle
    into a level you can actually place an order at."""
    inputs = _dcf_inputs(row, risk_free)
    if not inputs or target_cagr_pct is None or growth_pct is None:
        return None
    fcf0, shares, discount, net_cash = inputs
    equity = _equity_value(fcf0, growth_pct / 100.0, discount, net_cash)
    if equity is None or equity <= 0:
        return None
    value = equity / shares
    return round(value * ((1 + discount) / (1 + target_cagr_pct / 100.0)) ** years, 2)


def valuation_sub_score(val: dict) -> float | None:
    """0-100 for the composite LT score.

    The two methods are scored on their own quantities and deliberately not
    forced onto a common one. For the reverse DCF it is the growth gap in
    percentage points, across -10 (the price asks well less than the company
    delivers) to +25. For the peer method it is the discount to fair value
    across -30% to +40%, which is where that band stops discriminating.

    The implausibility rule is applied here too, and has to be: without it
    Nvidia scored 100 on this axis while its band read OVERVALUED, because a
    price requiring 55% growth against a company that just grew 194% has a
    hugely negative gap. The band already refuses to underwrite a 55% forecast
    at any delivered rate, and a sub-score that disagreed with the band would
    push exactly that name up the composite ranking.
    """
    from stockanalysis.core.longterm._common import inverse, scale
    val = val or {}
    implied = f(val.get("implied_growth_pct"))
    if implied is not None and implied >= IMPLAUSIBLE_IMPLIED_GROWTH:
        return 0.0
    gap = f(val.get("growth_gap_pp"))
    if gap is not None:
        return inverse(gap, -10.0, 25.0)
    up = f(val.get("upside_pct"))
    if up is not None:
        return scale(up, -30.0, 40.0)
    return None
