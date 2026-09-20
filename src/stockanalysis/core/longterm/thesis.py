"""
thesis.py — is this still the company you decided to own?
==========================================================
The gate that lets accumulation continue, and the only thing that stops it.

Why this exists
---------------
The engine's second gate is valuation, and for buying a full position at one
price that is the right gate. For adding to a business you have already
decided to own it is the wrong one, and measuring it against the live library
shows how wrong:

    For 55 of the 56 LQuality>=85 names, walking price from the 8/21 EMA all
    the way down to the 200 MA does not change the valuation verdict. Only
    CPAY moves (Fair -> Attractive). Everything else reads Extreme at every
    zone or Exceptional at every zone.

So valuation is binary PER NAME rather than graded per price. It can say
whether a business is ownable; it cannot size a tranche, because it says the
same thing at -4% and at -34%. Gating a DCA ladder on it produces an empty
page for exactly the names a pullback ladder exists to buy — NVDA's return
hurdle is not met until $71 against a $225 tape, PLTR's until $23 against
$174. A ladder that waits for those is the "waiting for the bottom" habit
rewritten as arithmetic.

What replaces it is not a softer valuation test. It is a different question:
has the business, its pricing power, or its long-term structure actually
deteriorated? While the answer is no, a lower price is a better price and the
ladder may keep filling. When the answer is yes, no price is low enough.

Three invalidations, never collapsed
-------------------------------------
    business      revenue, cash flow, earnings and cash generation
    competitive   pricing power and who is holding the shares
    structure     the long-term floor on the chart

They are reported separately because they fail for different reasons and
imply different actions, and because a single roll-up hides which one fired.
A stock below a RISING 200 MA is a deep correction — the thing to buy. Below
a FALLING one it is a breakdown. Same price, opposite instruction, and only
the structure leg knows the difference.

STRAINED is the state that earns its keep
------------------------------------------
Within a leg, one broken condition is STRAINED and two are BROKEN. One alone
is usually a company investing rather than a company failing: Microsoft grew
free cash flow +4% while revenue grew +16%, because capital expenditure went
from $28B to $116B. Read as a single deterioration that is correct and small.
Read as a thesis break it would have stopped accumulation in a compounder.

Two independent legs of the same statement failing at once is not noise.

UNMEASURED is not INTACT
-------------------------
The package rule (see core.longterm.__init__): a missing input never becomes
a satisfied condition. A leg with too few measurable conditions reports
UNMEASURED, and the caller must treat that as "cannot confirm the thesis
holds" rather than as permission. `MIN_MEASURED` is where that line sits.

What is NOT measured here, and cannot be
-----------------------------------------
Brand, network effects, switching costs, a competitor's product launch,
guidance withdrawn, an accounting restatement, a regulator. None of them are
columns on a scan row and none of them are inferable from one. The
competitive leg measures two observable shadows of a moat eroding — gross
margin falling and institutions leaving — and says so. It is not a moat
assessment and must never be presented as one; a human reading the news is
still the only thing that catches the other kind of break.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f

STATES = ("INTACT", "STRAINED", "BROKEN", "UNMEASURED")
STATE_ICONS = {"INTACT": "🟢", "STRAINED": "🟠", "BROKEN": "🔴",
               "UNMEASURED": "⚪"}

# How many conditions a leg needs measured before it may claim a state.
# Below this the leg is UNMEASURED — the same rule quality.py applies with
# MIN_COVERAGE and _moat() applies with MIN_MOAT_INPUTS.
MIN_MEASURED = 2

# Broken conditions that turn a leg from STRAINED to BROKEN.
BREAKS_FOR_BROKEN = 2

# ── Business thresholds ──────────────────────────────────────────────────────
# Each is a level of DETERIORATION, not a level of excellence. The question is
# "has this stopped working", so a flat number is not a break — only a
# measurably negative one is. Growth SLOWING is not here on purpose: a
# compounder decelerating from 40% to 25% is still compounding, and a ladder
# that stops on deceleration stops on every maturing business.
REVENUE_BREAK_PCT = 0.0        # 4-year revenue CAGR below this is shrinking
FCF_BREAK_PCT = 0.0            # 4-year free-cash-flow CAGR below this
# Earnings swing on one-off items in a way four-year CAGRs do not, so the
# single-year EPS figure needs a materiality buffer before it counts.
EPS_BREAK_PCT = -10.0
# Operating margin absorbs deliberate investment (see the Microsoft case
# above), so it needs the widest buffer of the three margin-ish checks.
OPERATING_MARGIN_BREAK_PP = -3.0
# Share of measured fiscal years in which free cash flow was positive.
FCF_POSITIVE_SHARE = 0.75

# ── Competitive thresholds ───────────────────────────────────────────────────
# Gross margin is the closest thing on a scan row to pricing power: it sits
# above operating expense, so it moves when a company has to discount, not
# when it chooses to spend. That makes it the one margin whose decline is
# evidence about competition rather than about strategy.
GROSS_MARGIN_BREAK_PP = -2.0
# Institutional ownership falling. Not a verdict on the business — it is who
# is holding it, which is the other observable shadow of a thesis changing.
INST_EXIT_PCT = -2.0

# ── Structure thresholds ─────────────────────────────────────────────────────
# How far below the 200 MA counts as through it rather than at it. ATR-scaled
# for the same reason technicals._tolerance is: 3% below the 200 MA is a
# rounding error on a stock that moves 5% a day and a real break on a utility.
STRUCTURE_BUFFER_ATR = 1.5
MIN_STRUCTURE_BUFFER_PCT = 2.0
MAX_STRUCTURE_BUFFER_PCT = 10.0
# How much the 200 MA has to move over 20 sessions before it counts as
# FALLING rather than flat. Deck's average measured -0.08% over a month —
# arithmetic noise on an average by construction designed to be slow — and
# without this buffer that reading alone tipped a healthy business into
# BROKEN. `compute_trend` can use a bare `> 0` because it is scoring a
# gradient; this is deciding whether to stop buying, and it has to be sure.
MA200_FALLING_PCT = -0.5


def _condition(name, ok, detail, failed_as=None) -> dict:
    """One tri-state condition. `ok` True = holding, False = broken,
    None = not measured.

    `failed_as` states the FAILURE as a failure. Condition names are positive
    assertions — "Revenue growing", "200 MA rising" — so a summary built by
    joining the names of broken conditions reads as a list of things that are
    true when it means the reverse. The first draft of this module printed
    "Thesis broken — Structure: Above the long-term floor, 200 MA rising",
    which contradicts itself inside nine words.
    """
    return {"name": name, "ok": ok, "detail": detail,
            "failed_as": failed_as or f"{name.lower()} — failed"}


def _leg(label, conditions, note=None) -> dict:
    """Roll a list of conditions into one leg's state.

    Counts rather than weights, for the reason the whole package prefers
    gates: a weighted deterioration score would let a large move in one
    healthy input mask an outright failure in another.
    """
    broken = [c for c in conditions if c["ok"] is False]
    holding = [c for c in conditions if c["ok"] is True]
    unmeasured = [c for c in conditions if c["ok"] is None]

    if len(broken) + len(holding) < MIN_MEASURED:
        state = "UNMEASURED"
    elif len(broken) >= BREAKS_FOR_BROKEN:
        state = "BROKEN"
    elif broken:
        state = "STRAINED"
    else:
        state = "INTACT"

    return {"label": label, "state": state, "icon": STATE_ICONS[state],
            "conditions": conditions,
            "broken": [c["name"] for c in broken],
            # The same failures phrased as failures, for prose. See
            # _condition's docstring for why both lists exist.
            "failures": [c["failed_as"] for c in broken],
            "unmeasured": [c["name"] for c in unmeasured],
            "measured": len(broken) + len(holding),
            "note": note}


def _business(row: dict) -> dict:
    """Is the business still growing and still generating cash?"""
    rev = f(row.get("Revenue_CAGR%"))
    fcf = f(row.get("FCF_CAGR%"))
    eps = f(row.get("EPS_Growth%"))
    om = f(row.get("OperatingMargin_Trend_pp"))
    years = f(row.get("FCF_Years"))
    pos_years = f(row.get("FCF_Positive_Years"))

    fcf_positive_ok = fcf_positive_detail = None
    if years and years > 0 and pos_years is not None:
        share = pos_years / years
        fcf_positive_ok = share >= FCF_POSITIVE_SHARE
        fcf_positive_detail = (f"{pos_years:.0f} of {years:.0f} fiscal years "
                               f"free-cash-flow positive")
    else:
        fcf_positive_detail = "cash-flow history not available"

    return _leg("Business", [
        _condition(
            "Revenue growing",
            None if rev is None else rev > REVENUE_BREAK_PCT,
            "not measured" if rev is None
            else f"{rev:+.1f}%/yr over the filed statements",
            None if rev is None else f"revenue shrinking {rev:+.1f}%/yr"),
        _condition(
            "Free cash flow growing",
            None if fcf is None else fcf > FCF_BREAK_PCT,
            "not measured" if fcf is None
            else f"{fcf:+.1f}%/yr over the filed statements",
            None if fcf is None else f"free cash flow shrinking {fcf:+.1f}%/yr"),
        _condition(
            "Earnings growing",
            None if eps is None else eps > EPS_BREAK_PCT,
            "not measured" if eps is None
            else f"{eps:+.1f}% (a decline past {EPS_BREAK_PCT:.0f}% counts)",
            None if eps is None else f"earnings down {eps:.1f}%"),
        _condition(
            "Operating margin holding",
            None if om is None else om > OPERATING_MARGIN_BREAK_PP,
            "not measured" if om is None
            else f"{om:+.1f}pp over the filed statements",
            None if om is None else f"operating margin down {om:.1f}pp"),
        _condition("Cash generative", fcf_positive_ok, fcf_positive_detail,
                   None if fcf_positive_ok is None else
                   f"free cash flow positive in only {pos_years:.0f} of "
                   f"{years:.0f} years"),
    ])


def _competitive(row: dict) -> dict:
    """Pricing power, and who is holding the shares.

    Two observable shadows of a moat eroding. Not a moat assessment — see
    the module docstring, and quality._moat, which scores the financial
    fingerprint of a moat at a LEVEL where this measures its DIRECTION.
    """
    gm = f(row.get("GrossMargin_Trend_pp"))
    inst = f(row.get("Inst_Own_Chg"))

    return _leg("Competitive", [
        _condition(
            "Pricing power holding",
            None if gm is None else gm > GROSS_MARGIN_BREAK_PP,
            "not measured" if gm is None
            else f"gross margin {gm:+.1f}pp over the filed statements",
            None if gm is None else f"gross margin down {gm:.1f}pp — the "
                                    f"company is discounting"),
        _condition(
            "Institutions not leaving",
            None if inst is None else inst > INST_EXIT_PCT,
            "not measured" if inst is None
            else f"institutional ownership {inst:+.1f}pp",
            None if inst is None
            else f"institutional ownership down {inst:.1f}pp"),
    ], note="Brand, network effects, switching costs and competitor moves "
            "are not on a scan row and are not measured here.")


def structure_break_price(row: dict) -> tuple[float | None, float | None]:
    """(the price that would invalidate, the buffer used) — the DCA
    invalidation level, which is NOT the trading stop.

    position_sizing.plan_stop answers "where is this trade wrong", and its
    answer is a few ATR under an entry because a swing has to be cut before
    it costs real money. A ten-year position is wrong somewhere else
    entirely: at the level where the long-term structure the thesis rests on
    has failed. Quoting the trading stop to a long-term holder is how a
    -8% wiggle ends a position that was never wrong.
    """
    ma200 = f(row.get("200MA"))
    if ma200 is None or ma200 <= 0:
        return None, None
    atr = f(row.get("ATR_Pct"))
    buffer_pct = (MIN_STRUCTURE_BUFFER_PCT * 2 if atr is None else
                  max(MIN_STRUCTURE_BUFFER_PCT,
                      min(MAX_STRUCTURE_BUFFER_PCT, atr * STRUCTURE_BUFFER_ATR)))
    return round(ma200 * (1 - buffer_pct / 100.0), 2), round(buffer_pct, 2)


def _structure(row: dict) -> dict:
    """Has the long-term floor failed?

    Deliberately not compute_trend(). That function asks "is this a healthy
    uptrend whose pullbacks are worth buying" across seven weighted checks,
    which is the right question for opening a position and too strict for
    continuing one — a name can fail three of its checks and still be a
    business whose 200 MA has never been lost.

    This asks one thing: is price through the long-term floor, and is that
    floor falling. Both halves are required, because they are what separates
    the two cases that look identical on a price chart. Price under a rising
    200 MA is the deep correction the third rung exists to buy; under a
    falling one it is a trend that has rolled over.
    """
    price = f(row.get("Current Price")) or f(row.get("price"))
    ma200 = f(row.get("200MA"))
    slope = f(row.get("MA200_Slope%"))
    break_price, buffer_pct = structure_break_price(row)

    through = None
    if price is not None and break_price is not None:
        through = price < break_price

    falling = None if slope is None else slope < MA200_FALLING_PCT
    conditions = [
        _condition(
            "Above the long-term floor",
            None if through is None else not through,
            "not measured" if through is None
            else (f"${price:,.2f} vs the 200 MA ${ma200:,.2f} less a "
                  f"{buffer_pct:.1f}% buffer — invalidation ${break_price:,.2f}"),
            None if break_price is None
            else f"price is through the ${break_price:,.2f} floor"),
        _condition(
            "200 MA not falling",
            None if falling is None else not falling,
            "not measured — re-scan to populate" if slope is None
            else (f"{slope:+.2f}% over 20 sessions "
                  f"(falling past {MA200_FALLING_PCT:.1f}% counts)"),
            None if slope is None else f"the 200 MA is falling {slope:+.2f}%"),
    ]

    leg = _leg("Structure", conditions)

    # Both halves required, so the generic count is overridden here. Through
    # the floor with the average still rising is the correction to accumulate
    # into, and the count alone would have called that one break STRAINED —
    # right state, wrong reason. Named explicitly so the sentence the page
    # prints is the one this branch actually decided.
    if through is True and falling is not None:
        if not falling:
            leg["state"] = "STRAINED"
            leg["note"] = (
                f"below the 200 MA, but the average is not falling "
                f"({slope:+.2f}%) — a deep correction rather than a "
                f"breakdown, which is the case the deepest rung exists for")
        else:
            leg["state"] = "BROKEN"
            # Describes; does not instruct. compute_thesis's headline is what
            # tells the reader to stop, and a note that also said so produced
            # "Stop adding. Stop adding at any price."
            leg["note"] = (
                f"below the 200 MA and the average is falling "
                f"({slope:+.2f}%) — price and the trend agree")
        leg["icon"] = STATE_ICONS[leg["state"]]
    return leg


# Worst leg wins. Not an average: a business whose cash flows are fine and
# whose 200 MA has rolled over is not "half intact", it is a name to stop
# adding to, and averaging the three would report it as healthy.
_SEVERITY = {"BROKEN": 3, "STRAINED": 2, "UNMEASURED": 1, "INTACT": 0}


def compute_thesis(row: dict) -> dict:
    """
    Returns {"state", "icon", "legs", "broken", "strained", "unmeasured",
             "invalidation_price", "may_accumulate", "headline"}.

    `may_accumulate` is the one field a caller has to respect: True only
    while nothing is broken and enough was measured to say so. UNMEASURED
    does not permit accumulation, because "we could not check" and "we
    checked and it is fine" are different facts and this package never lets
    them collapse.
    """
    legs = [_business(row), _competitive(row), _structure(row)]
    state = max((l["state"] for l in legs), key=lambda s: _SEVERITY[s])

    broken = [l["label"] for l in legs if l["state"] == "BROKEN"]
    strained = [l["label"] for l in legs if l["state"] == "STRAINED"]
    unmeasured = [l["label"] for l in legs if l["state"] == "UNMEASURED"]
    break_price, _buffer = structure_break_price(row)

    def _why(want: str) -> str:
        """Why the named legs are in that state, in their own words.

        The structure leg writes a `note` whenever its override fired, and
        that sentence knows more than a list of conditions can — it is the
        one that separates a deep correction from a breakdown. Where a leg
        has no note, the failures are already phrased as failures.
        """
        parts = []
        for l in legs:
            if l["state"] != want:
                continue
            if l["note"] and l["state"] in ("STRAINED", "BROKEN"):
                parts.append(l["note"].rstrip("."))
            elif l["failures"]:
                parts.append(f'{l["label"].lower()} — '
                             f'{", ".join(l["failures"])}')
        return "; ".join(parts)

    if state == "BROKEN":
        headline = f"Thesis broken — {_why('BROKEN')}. Stop adding at any price."
    elif state == "STRAINED":
        headline = (f"Thesis strained — {_why('STRAINED')}. One deterioration "
                    f"is usually investment rather than failure; size down and "
                    f"read the filings before the next tranche.")
    elif state == "UNMEASURED":
        headline = (f"Thesis cannot be confirmed — {', '.join(unmeasured)} "
                    f"had too little measured. Not the same as intact.")
    else:
        headline = ("Thesis intact — business, pricing power and long-term "
                    "structure all still holding. A lower price is a better "
                    "price.")

    return {
        "state": state,
        "icon": STATE_ICONS[state],
        "legs": legs,
        "broken": broken,
        "strained": strained,
        "unmeasured": unmeasured,
        # The level, so a caller can show "DCA invalidation $X" beside — and
        # visibly apart from — the trading stop.
        "invalidation_price": break_price,
        "may_accumulate": state in ("INTACT", "STRAINED"),
        "headline": headline,
    }
