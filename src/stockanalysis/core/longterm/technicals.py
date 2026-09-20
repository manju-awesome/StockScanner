"""
technicals.py — when to buy a company you have already decided to own
=====================================================================
Gates three and four. Nothing here asks whether the business is good; that
was settled in quality.py and valuation.py. This module answers only:

    compute_trend()               is the long-term uptrend intact?
    compute_pullback()            has price come back to a level worth
                                  buying, and which one?
    compute_support_confluence()  how many independent levels agree here?
    compute_pullback_volume()     is this pullback accumulation or
                                  distribution?

The distinction that shapes the file
------------------------------------
"Price is near the 50 MA" is not a signal. It is a coordinate. Every stock
in a downtrend passes through its 50 MA on the way down, and a screener
built on proximity alone cannot tell that pass-through from a bounce.

So no level is scored for being nearby. A level counts as SUPPORT only when
price is at or above it — a moving average the price has already fallen
through is resistance, and the sign of that difference is the entire
difference between a pullback and a breakdown. `_supports()` is where that
rule lives, and every level in the confluence score goes through it.

Tolerance is measured in the stock's own volatility
---------------------------------------------------
"Within 2% of the 50 MA" means something different for a utility and for a
semiconductor. Every proximity test here uses `_tolerance()`, which is a
multiple of the stock's ATR% with a floor, so "at the level" means the same
thing across the library. A fixed percentage would classify every
high-volatility name as permanently at every level at once.

Fields this module needs that older scan rows will not have
-----------------------------------------------------------
MA200_Slope%, MA50_Slope%, Reversal_Candle, Distribution_Days_25d and
Prior_Breakout_Level were added to metrics.py alongside this engine. Rows
scanned before that carry None for them, and every check reads None as
UNKNOWN rather than as a failure — an unscanned field is not a broken
trend. The cost is that the trend gate returns None instead of True for
those rows, which downgrades them to WATCH rather than promoting them, and
that is the correct direction to be wrong in.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import b, band, f, scale

# Proximity is ATR-scaled, but not without bounds: on a 0.5%-ATR name a pure
# multiple would demand price sit within a few cents of the average, and on
# a 15%-ATR name it would call a 20% gap "at the level".
TOLERANCE_ATR_MULT = 1.5
MIN_TOLERANCE_PCT = 1.5
MAX_TOLERANCE_PCT = 8.0

# Above this far over the 8 EMA there is no pullback to buy — this is a
# chase, whatever else scores well.
EXTENDED_ABOVE_8EMA = 4.0

ZONES = ("EMA", "50MA", "200MA", "BREAKOUT", "NONE")
ZONE_LABELS = {
    "EMA": "Zone A — 8/21 EMA",
    "50MA": "Zone B — 50 MA",
    "200MA": "Zone C — 200 MA",
    "BREAKOUT": "Prior breakout support",
    "NONE": "No pullback — not at a level",
}

# ── Pullback stages ──────────────────────────────────────────────────────────
# The zone above answers "is price sitting ON a tracked level". The stage
# answers "how deep is this pullback", and they are not the same question.
#
# The gap between them is what made Western Digital unreadable. At 21.9%
# below its 50 MA and 28.5% above its 200 MA, with an 8% tolerance, WDC is
# near nothing — zone NONE, "not at any tracked level" — which reads as "no
# setup" when the truth is "a very deep correction that has not broken the
# long-term trend". That is a specific, nameable state, and one of the more
# interesting ones a quality-on-pullback strategy can find.
STAGES = ("AT_HIGHS", "STAGE1_EMA", "STAGE2_50MA", "STAGE3_DEEP",
          "STAGE4_UNCONFIRMED", "STAGE4_BREAKDOWN", "EXTENDED")
STAGE_LABELS = {
    "AT_HIGHS": "At highs — no pullback",
    "STAGE1_EMA": "Stage 1 — 8/21 EMA pullback",
    "STAGE2_50MA": "Stage 2 — 50 MA pullback",
    "STAGE3_DEEP": "Stage 3 — deep pullback, 200 MA approach",
    "STAGE4_UNCONFIRMED": "Below the 200 MA — long-term trend unconfirmed",
    "STAGE4_BREAKDOWN": "Stage 4 — confirmed trend breakdown",
    "EXTENDED": "Extended above the 8 EMA",
}
STAGE_ICONS = {"AT_HIGHS": "⚪", "STAGE1_EMA": "🟢", "STAGE2_50MA": "🟢",
               "STAGE3_DEEP": "🟡", "STAGE4_UNCONFIRMED": "🟠",
               "STAGE4_BREAKDOWN": "🔴", "EXTENDED": "🟠"}


def classify_stage(row: dict, zone: str, extended: bool) -> str:
    """Which of the four pullback stages price occupies.

    Checked in order of severity: below the 200 MA is a breakdown whatever
    else is true, and only then does it matter which level price is resting
    on. A stock can be below its 50 MA and still be at a tradeable level
    (the prior breakout, a volume shelf), which is why the zone is consulted
    before the raw distance.
    """
    price = _price(row)
    ma50, ma200 = f(row.get("50MA")), f(row.get("200MA"))

    if price is not None and ma200 is not None and price < ma200:
        # Below the 200 MA splits three ways, and collapsing them into one
        # "breakdown" asserts something that was never measured. A breakdown
        # is price below a FALLING long-term average; price below a RISING
        # one is a deep pullback, and the two call for opposite actions.
        #
        # Meta is the case that exposed it: below its 200 MA with both
        # slopes unscanned, called a confirmed trend breakdown on the
        # strength of a single inequality.
        slope200, slope50 = f(row.get("MA200_Slope%")), f(row.get("MA50_Slope%"))
        if slope200 is None:
            return "STAGE4_UNCONFIRMED"
        if slope200 < 0 and (slope50 is None or slope50 < 0):
            return "STAGE4_BREAKDOWN"
        # Long-term average still rising underneath a price that has dipped
        # below it — a deep correction, not a broken trend.
        return "STAGE3_DEEP"
    if zone == "EMA":
        return "STAGE1_EMA"
    if zone == "50MA":
        return "STAGE2_50MA"
    if zone == "200MA":
        return "STAGE3_DEEP"
    if price is not None and ma50 is not None and price < ma50:
        # Below the 50 MA but still above the 200 MA — the deep-correction
        # state. Applies whether or not price is resting on a breakout shelf.
        return "STAGE3_DEEP"
    if extended:
        return "EXTENDED"
    if zone == "BREAKOUT":
        return "STAGE1_EMA" if price and ma50 and price >= ma50 else "STAGE3_DEEP"
    return "AT_HIGHS"

# Bands are set on the COUNT of levels agreeing, not on the weighted score.
#
# Two reasons. First, the count is what actually gates a buy
# (engine.MIN_CONFLUENCE_HITS), so a label drawn from the score described one
# thing while the decision used another — and the boundary between "adequate"
# and "weak" now falls exactly where the engine stops issuing buys.
#
# Second, the score cannot carry a label at all: measured across the 545-row
# library, the score ranges for adjacent counts OVERLAP (3 levels spans 35-70,
# 4 levels spans 60-75), because which levels agree matters as much as how
# many. A 50 MA plus 200 MA agreement scores 50 on two hits; the 8/21 EMA plus
# a key level scores 25 on the same two. The old score bands (80/65/50) put
# 448 of 545 rows in "Weak" and 14 above "Good" — a label four-fifths of the
# page shares is not telling you anything.
#
# Distribution on the live library: 4+ levels 11.7%, 3 levels 27.3%,
# 2 levels 23.5%, 0-1 levels 37.4%.
CONFLUENCE_BANDS = ((4, "🔥 Strong"), (3, "🟢 Good"),
                    (2, "🟡 Adequate"), (0, "🔴 Weak"))

# The support ladder, shallowest first: (row field, zone, display name).
# Ordered by lookback rather than by price, so S1..S4 name the same average
# on every ticker and a column of them is comparable down the page. Which
# level is actually nearest is a property of the tape and changes daily —
# see compute_pullback(), which picks the zone by distance, not by this order.
SUPPORT_LEVELS = (
    ("8EMA", "EMA", "8 EMA"),
    ("21EMA", "EMA", "21 EMA"),
    ("50MA", "50MA", "50 MA"),
    ("200MA", "200MA", "200 MA"),
    ("Prior_Breakout_Level", "BREAKOUT", "prior breakout"),
)
# S1..S4 in the UI map to the first four, by design: a fixed ladder is
# sortable and comparable across rows, which a "nearest support" numbering
# would not be.
SUPPORT_SLOTS = tuple(
    (f"S{i + 1}", key, name)
    for i, (key, _zone, name) in enumerate(SUPPORT_LEVELS[:4]))

VOLUME_BANDS = ((80, "Healthy — accumulation"), (60, "Acceptable"),
                (40, "Mixed"), (0, "Distribution — avoid"))


def _price(row):
    return f(row.get("Current Price")) or f(row.get("price"))


def _tolerance(row) -> float:
    """How close counts as "at the level", in percent, for this stock."""
    atr = f(row.get("ATR_Pct"))
    if atr is None:
        return MIN_TOLERANCE_PCT * 2      # unknown volatility: be permissive
                                          # about proximity, strict elsewhere
    return max(MIN_TOLERANCE_PCT,
               min(MAX_TOLERANCE_PCT, atr * TOLERANCE_ATR_MULT))


def _supports(price, level, tol_pct) -> tuple[bool, float | None]:
    """Is `level` acting as support for `price` right now?

    True only when price is AT or ABOVE the level and within tolerance of
    it. Price below the level means the level was lost — that is
    resistance overhead, and counting it as support is how a screener
    recommends a breakdown. Returns (is_support, signed % price sits above).
    """
    if price is None or level is None or level <= 0:
        return False, None
    dist = (price / level - 1) * 100.0
    return (0 <= dist <= tol_pct), dist


# ─────────────────────────────────────────────────────────────────────────────
# TREND — §3. The precondition for everything else.
# ─────────────────────────────────────────────────────────────────────────────

# Four states, because "damaged" and "broken" are different claims and only
# one of them can be made without the moving-average slopes.
#
#   CONFIRMED  every structural check known and holding
#   PARTIAL    structure holds; something was not measured
#   IMPAIRED   a structural check has FAILED, but the 200 MA slope is
#              unknown — the trend is damaged, its direction unconfirmed
#   BROKEN     the 200 MA is measurably falling
#
# Meta forced the split. Below its 200 MA with a death cross but both slopes
# unscanned, it read BROKEN — a verdict on the long-term trend drawn entirely
# from where price sits today. Price below a RISING 200 MA is a deep
# correction; below a FALLING one it is a breakdown, and the difference is
# the slope nobody had measured.
TREND_STATES = ("CONFIRMED", "PARTIAL", "RECOVERING", "IMPAIRED", "BROKEN")
TREND_ICONS = {"CONFIRMED": "🟢", "PARTIAL": "🟡", "RECOVERING": "🔵",
               "IMPAIRED": "🟠", "BROKEN": "🔴"}
TREND_SUMMARY = {
    "CONFIRMED": "long-term uptrend confirmed",
    "PARTIAL": "structure intact, some checks unmeasured",
    "RECOVERING": "structure repairing — the intermediate trend is ahead of "
                  "the long-term one",
    # Overridden per row by _impaired_summary() — the fixed wording claimed
    # the slopes were unmeasured even when they were.
    "IMPAIRED": "damaged — long-term structure has failed a check",
    "BROKEN": "200 MA falling — long-term trend broken",
}

# (name, weight, structural). STRUCTURAL checks answer "was this a healthy
# long-term uptrend"; the rest answer "where is price inside it right now".
# Only structural checks can break the trend — see the docstring.
# STRUCTURAL means "can break the long-term trend". The 50 MA's direction
# deliberately cannot.
#
# Nvidia is why: 200 MA rising +1.22%, 50 MA above it, price above all four
# averages and 8.7% clear of the 50 — with the 50 MA cooling -1.45%. Scored
# as structural that single reading marked the whole long-term trend
# damaged. A 50-day average rolling over inside a rising 200-day is an
# intermediate pause, which is the ordinary shape of the pullback this
# engine exists to buy. It still costs 15 points; it no longer overrides
# the 200 MA.
_TREND_CHECKS = (
    ("Price above 200 MA", 20, True),
    ("50 MA above 200 MA", 20, True),
    ("200 MA rising", 20, True),
    ("50 MA rising", 15, False),
    ("Price above 50 MA", 15, False),
    ("Price above 21 EMA", 5, False),
    ("Price above 8 EMA", 5, False),
)


def _impaired_summary(failed, slope_unknown: bool) -> str:
    """Why the trend is impaired, in terms of what was actually checked.

    A fixed string here told every impaired row that its moving-average
    slopes were unmeasured. Nvidia's were measured — the summary simply
    was not reading them, so a freshly scanned ticker was told to re-scan.
    """
    named = ", ".join(failed) if failed else "a structural check"
    if slope_unknown:
        return (f"{named} failed, and the 200 MA slope is unmeasured — "
                f"direction cannot be established")
    return f"{named} failed with the 200 MA slope measured"


def compute_trend(row: dict) -> dict:
    """
    Trend as a THREE-state reading, not a pass/fail.

        🟢 CONFIRMED  every structural check known and holding
        🟡 PARTIAL    structure holds, but something is unmeasured
        🔴 BROKEN     a structural check actually failed

    Two distinctions do the work here, and collapsing either one produces
    the wrong answer on exactly the setups this engine hunts.

    UNKNOWN IS NOT FAILURE. Western Digital sits above its 200 MA with the
    50 MA above the 200 MA, and its moving-average slopes were never
    scanned. Reporting that as "failed on trend" states something the data
    does not support, and buries a live candidate among genuine breakdowns.
    It is PARTIAL: 40 points confirmed, 35 unknown, nothing failed.

    STRUCTURE IS NOT POSITION. "Price above the 50 MA" describes where price
    sits today, not whether the uptrend is intact, and requiring it inside a
    trend gate contradicts the whole strategy: a stock cannot pull back to
    its 50 MA while remaining comfortably above it. So price-vs-MA checks
    are scored — they are real information — but they can never break the
    trend. How far price has fallen is the PULLBACK STAGE's job; see
    compute_pullback().

    Returns {"state", "icon", "score", "confirmed_points", "unknown_points",
             "checks", "failed", "unknown", "structural_failed", "pass"}.
    `pass` is kept as a convenience tri-state (True/None/False) mapping to
    CONFIRMED/PARTIAL/BROKEN for callers that only need the coarse reading.
    """
    price = _price(row)
    ma50, ma200 = f(row.get("50MA")), f(row.get("200MA"))
    ema8, ema21 = f(row.get("8EMA")), f(row.get("21EMA"))
    slope200 = f(row.get("MA200_Slope%"))
    slope50 = f(row.get("MA50_Slope%"))
    vs50 = f(row.get("Price_vs_50MA%"))
    if vs50 is None and price and ma50:
        vs50 = (price / ma50 - 1) * 100.0
    above200 = b(row.get("Above_200MA"))
    if above200 is None and price is not None and ma200 is not None:
        above200 = price > ma200

    def _above(level):
        if price is None or level is None:
            return None, "unknown"
        return price > level, f"${price:,.2f} vs ${level:,.2f}"

    results = {
        "Price above 200 MA": (
            above200,
            "unknown" if above200 is None else
            (f"${price:,.2f} vs ${ma200:,.2f}" if price and ma200
             else ("above" if above200 else "below"))),
        "50 MA above 200 MA": (
            None if (ma50 is None or ma200 is None) else ma50 > ma200,
            "unknown" if (ma50 is None or ma200 is None)
            else f"${ma50:,.2f} vs ${ma200:,.2f}"),
        "200 MA rising": (
            None if slope200 is None else slope200 > 0,
            "not measured — re-scan to populate" if slope200 is None
            else f"{slope200:+.2f}% over 20 sessions"),
        "50 MA rising": (
            None if slope50 is None else slope50 > 0,
            "not measured — re-scan to populate" if slope50 is None
            else f"{slope50:+.2f}% over 20 sessions"),
        "Price above 50 MA": (
            None if vs50 is None else vs50 > 0,
            "unknown" if vs50 is None else f"{vs50:+.1f}%"),
        "Price above 21 EMA": _above(ema21),
        "Price above 8 EMA": _above(ema8),
    }

    checks, confirmed_pts, unknown_pts = [], 0, 0
    for name, weight, structural in _TREND_CHECKS:
        ok, detail = results[name]
        checks.append({"name": name, "ok": ok, "detail": detail,
                       "weight": weight, "structural": structural,
                       # kept for the existing UI, which renders "(required)"
                       "required": structural})
        if ok is None:
            unknown_pts += weight
        elif ok:
            confirmed_pts += weight

    failed = [c["name"] for c in checks if c["ok"] is False]
    unknown = [c["name"] for c in checks if c["ok"] is None]
    structural_failed = [c["name"] for c in checks
                         if c["structural"] and c["ok"] is False]
    structural_unknown = [c["name"] for c in checks
                          if c["structural"] and c["ok"] is None]

    slope200_known = slope200 is not None
    # A 50/200 inversion means opposite things depending on which side of the
    # 200 MA price sits, and reading it as damage either way is wrong.
    #
    # Palantir: price $172.01, above its 200 MA at $152.28 and 30% above its
    # 50 MA at $132.60 — with the 50 MA still below the 200 MA. Nothing is
    # damaged; the 50 MA is a lagging average that has not yet climbed back
    # through the 200 after an earlier decline. That is a golden cross
    # pending, the exact opposite of a death cross just struck, and calling
    # it "damaged" inverted the reading.
    #
    # Price ABOVE the 200 MA with the averages still crossed is recovery.
    # Price BELOW it is impairment.
    # Two different shapes of repair, both meaning "the structure is mending
    # rather than failing":
    #
    #   price back above the 200 MA with the averages still crossed —
    #   a golden cross pending (Palantir)
    #
    #   the 50 MA turning UP while the 200 MA still falls — the intermediate
    #   trend leading the long-term one, which is how every recovery starts
    #   (Microsoft at -2.01% / +0.91%, Robinhood at -3.84% / +9.81%)
    #
    # `repairing` requires the 50/200 inversion to still be PRESENT, but no
    # longer requires it to be the ONLY structural failure.
    #
    # The exact-match it used to use (`== ["50 MA above 200 MA"]`) quietly
    # stopped matching the very case it was written for: once Palantir's
    # slopes were scanned, "200 MA rising" started failing too, the list
    # comparison broke, and the name fell through to BROKEN. A falling 200 MA
    # is not separate evidence against a recovery — it is the footprint of
    # the decline being recovered FROM, and demanding it be absent means
    # demanding the recovery already be over.
    #
    # The inversion itself must stay in the test, though, because dropping it
    # collapses two opposite shapes into one. Price above the 200 MA with the
    # 50 MA ALSO above it and only the long-term slope failing is a healthy
    # structure just beginning to roll over — deterioration, not repair — and
    # that shape must keep falling through to BROKEN.
    repairing = (above200 is True
                 and "50 MA above 200 MA" in structural_failed)
    turning_up = (slope200 is not None and slope200 < 0
                  and slope50 is not None and slope50 > 0)

    if structural_failed:
        # Checked BEFORE the breakdown test, so "price has reclaimed the 200
        # MA" wins over "the 200 MA is still falling" — the second is true of
        # every recovery and cannot be what disqualifies one.
        if repairing or turning_up:
            state = "RECOVERING"
        # A breakdown is price beneath a measurably falling 200 MA. With the
        # 50 MA already turning up it is a recovery in progress, and calling
        # it broken reads the slower average as the whole story.
        elif slope200_known and slope200 < 0:
            state = "BROKEN"
        else:
            state = "IMPAIRED"
    elif structural_unknown:
        state = "PARTIAL"
    else:
        state = "CONFIRMED"

    summary = TREND_SUMMARY[state]
    if state == "IMPAIRED":
        summary = _impaired_summary(structural_failed, slope200 is None)
    elif state == "RECOVERING":
        summary = ("the 50 MA is rising while the 200 MA still falls"
                   if turning_up else
                   "price has reclaimed the 200 MA; the 50 MA has not caught "
                   "up yet")

    return {
        "state": state,
        "icon": TREND_ICONS[state],
        # The score is out of a fixed 100, NOT renormalised over what was
        # measured. That is the point: "40 confirmed, 35 unknown" is the
        # honest reading, and renormalising would turn it into 53/100 and
        # lose the distinction between measured and merely absent.
        "score": confirmed_pts,
        "confirmed_points": confirmed_pts,
        "unknown_points": unknown_pts,
        "checks": checks,
        "failed": failed,
        "unknown": unknown,
        "structural_failed": structural_failed,
        "required_failed": structural_failed,     # legacy alias
        "summary": summary,
        # Coarse tri-state for callers that only need pass/unknown/fail.
        # IMPAIRED maps to False: it is not a trend to buy into, whatever
        # its ultimate direction turns out to be.
        # RECOVERING maps to None, not False: price is above the long-term
        # average, so this is not a trend to be ejected from the ladder — it
        # is one whose confirmation has not arrived. Same treatment as
        # PARTIAL, which is the other "not yet established" state.
        "pass": {"CONFIRMED": True, "PARTIAL": None, "RECOVERING": None,
                 "IMPAIRED": False, "BROKEN": False}[state],
    }


# ─────────────────────────────────────────────────────────────────────────────
# PULLBACK ZONE — §4/5/6. Which level is price actually at?
# ─────────────────────────────────────────────────────────────────────────────

def compute_pullback(row: dict) -> dict:
    """
    Which of the framework's entry zones price currently occupies.

    Zones are assigned by which level price is nearest AND above, not by
    which moving average is conventionally "deeper". After a fast move the
    averages do not stay in textbook order — a stock can sit below its 8 EMA
    and above its 50 MA at the same time — so the zone follows the tape.

    Returns {"zone", "label", "level", "level_price", "distance_pct",
             "extended", "depth_from_high", "candidates", "by_level",
             "range_52w", "note"}. `by_level` is keyed by row field so a
    caller can render a fixed S1..S4 ladder without re-deriving distances.
    """
    price = _price(row)
    tol = _tolerance(row)
    pct8 = f(row.get("Pct_vs_8EMA"))

    candidates = []
    by_level = {}
    for key, zone, name in SUPPORT_LEVELS:
        value = f(row.get(key))
        ok, dist = _supports(price, value, tol)
        entry = {"key": key, "zone": zone, "name": name,
                 "price": None if value is None else round(value, 2),
                 "distance_pct": None if dist is None else round(dist, 2),
                 # Above the level is support underfoot; below it the level
                 # is overhead resistance. The sign carries that, and it is
                 # the single most important thing on this row.
                 "held": None if dist is None else dist >= 0,
                 "supporting": bool(ok)}
        by_level[key] = entry
        if dist is not None:
            candidates.append(entry)

    extended = pct8 is not None and pct8 > EXTENDED_ABOVE_8EMA
    supporting = [c for c in candidates if c["supporting"]]
    # Nearest supporting level wins. Ties go to the tighter distance, which
    # is what "price is sitting on it" means.
    supporting.sort(key=lambda c: c["distance_pct"])

    depth = f(row.get("Dist_52W_High%"))

    if extended:
        zone, chosen = "NONE", None
        note = (f"Extended {pct8:.1f}% above the 8 EMA — buying here pays the "
                f"top of the move, whatever the level score says")
    elif supporting:
        chosen = supporting[0]
        zone = chosen["zone"]
        note = (f"At the {chosen['name']} — price ${price:,.2f} is "
                f"{chosen['distance_pct']:+.1f}% above ${chosen['price']:,.2f} "
                f"(within {tol:.1f}% tolerance for this stock's volatility)")
    else:
        zone, chosen = "NONE", None
        below = [c for c in candidates if c["distance_pct"] < 0]
        if below and price is not None:
            nearest = max(below, key=lambda c: c["distance_pct"])
            note = (f"Below the {nearest['name']} "
                    f"(${nearest['price']:,.2f}, {nearest['distance_pct']:+.1f}%) "
                    f"— that is resistance overhead, not support underfoot")
        else:
            note = "Not at any tracked level — no entry to price yet"

    stage = classify_stage(row, zone, extended)
    supports = compute_supports(row)

    # The stage's own sentence wins where it knows more than the zone did.
    # "Not at any tracked level" is true of a deep correction and tells you
    # nothing; naming the correction and the level under it is the finding.
    if stage == "STAGE3_DEEP" and zone == "NONE":
        vs50 = f(row.get("Price_vs_50MA%"))
        vs200 = f(row.get("Price_vs_200MA%"))
        near = supports.get("near")
        note = (f"Deep pullback — "
                + (f"{abs(vs50):.1f}% below the 50 MA" if vs50 is not None
                   else "below the 50 MA")
                + (f", still {vs200:+.1f}% above the 200 MA"
                   if vs200 is not None else "")
                + (f". Nearest support ${near['price']:,.2f} "
                   f"({near['distance_pct']:+.1f}% away)" if near else ""))
    elif stage == "STAGE4_UNCONFIRMED":
        ma200 = f(row.get("200MA"))
        vs200 = ((price / ma200 - 1) * 100.0
                 if price and ma200 else f(row.get("Price_vs_200MA%")))
        note = ("Below the 200 MA"
                + (f" ({vs200:+.1f}%)" if vs200 is not None else "")
                + " — whether the long-term trend is broken cannot be said "
                  "until the moving-average slopes are measured")
    elif stage == "STAGE4_BREAKDOWN":
        # Derived from the same price and level the stage decision used, not
        # read from Price_vs_200MA%. Where the scan column disagrees with the
        # levels on the row — a partially refreshed row, a CSV round trip —
        # trusting it prints "Below the 200 MA (+25.0%)", a sentence that
        # contradicts itself inside eight words.
        ma200 = f(row.get("200MA"))
        vs200 = ((price / ma200 - 1) * 100.0
                 if price and ma200 else f(row.get("Price_vs_200MA%")))
        note = ("Below the 200 MA"
                + (f" ({vs200:+.1f}%)" if vs200 is not None else "")
                + " — this is a trend breakdown, not a pullback to buy")

    return {
        "zone": zone,
        "label": ZONE_LABELS[zone],
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "stage_icon": STAGE_ICONS[stage],
        "level": None if chosen is None else chosen["name"],
        "level_price": None if chosen is None else chosen["price"],
        "distance_pct": None if chosen is None else chosen["distance_pct"],
        "extended": extended,
        "pct_vs_8ema": pct8,
        "depth_from_high": depth,
        "tolerance_pct": round(tol, 2),
        "candidates": candidates,
        "by_level": by_level,
        "supports": supports,
        "buy_zone": compute_buy_zone_level(row),
        "resistance": compute_resistance_level(row),
        "ma_cluster": compute_ma_cluster(row),
        "targets": compute_targets(row),
        "range_52w": compute_52w_range(row),
        "note": note,
    }


def _touches_belong_to(row: dict) -> str | None:
    """Which of S1 / R1 the row's Touches and Volume_Confirmation describe.

    core.key_levels writes those two columns for whichever level is NEAREST
    the price, not for S1 — see its `nearest = s1 if (price - s1) <= (r1 -
    price) else r1`. Attributing them to S1 unconditionally credits a support
    shelf with a resistance level's touch count whenever resistance is
    closer, which is a quiet way to make a level look better-tested than it
    is.
    """
    price = _price(row)
    s1, r1 = f(row.get("S1")), f(row.get("R1"))
    if price is None:
        return None
    if s1 is not None and r1 is not None:
        return "S1" if (price - s1) <= (r1 - price) else "R1"
    if s1 is not None:
        return "S1"
    return "R1" if r1 is not None else None


# How close a level has to sit to count as part of a cluster. Fixed rather
# than ATR-scaled, unlike _tolerance(): a cluster is a statement about the
# averages compressing around each other, and letting a volatile name call a
# 12% spread "tight" would make the metric meaningless exactly where
# compression matters most.
CLUSTER_PCT = 2.0
CLUSTER_BANDS = ((3, "🟢 Strong cluster"), (2, "🟡 Moderate cluster"),
                 (1, "🔴 Single level"), (0, "⚪ No level nearby"))


def compute_ma_cluster(row: dict) -> dict:
    """How many levels are compressed around the current price.

    Deliberately NOT the same question as support confluence, and
    deliberately not direction-aware. Confluence asks "how much is holding
    price UP", so it only counts levels price sits above. A cluster asks
    "how tightly are the averages wound around here", which is true whether
    price is a hair over them or a hair under.

    Meta is why the distinction earns its place: at $592.10 its 8 EMA, 21
    EMA, 50 MA, S1 and R1 all sit within 1.3%, which is a genuine
    decision point — and its support confluence is 25/100, because price is
    below most of them. Both readings are correct. Reporting only the second
    describes a tight coil as an absence.

    A cluster is not bullish on its own. It says a move is likely to be
    resolved here, not which way.
    """
    price = _price(row)
    out = {"count": 0, "label": CLUSTER_BANDS[-1][1], "levels": [],
           "span_pct": None, "within_pct": CLUSTER_PCT}
    if price is None or price <= 0:
        return out

    checked = [(label, f(row.get(key)))
               for key, _zone, label in SUPPORT_LEVELS]
    checked += [("S1 support", f(row.get("S1"))),
                ("R1 resistance", f(row.get("R1")))]

    near = []
    for label, level in checked:
        if level is None or level <= 0:
            continue
        dist = (level / price - 1) * 100.0
        if abs(dist) <= CLUSTER_PCT:
            near.append({"name": label, "price": round(level, 2),
                         "distance_pct": round(dist, 2)})

    near.sort(key=lambda x: x["distance_pct"])
    count = len(near)
    label = next(name for floor, name in CLUSTER_BANDS if count >= floor)
    span = None
    if count >= 2:
        span = round(near[-1]["distance_pct"] - near[0]["distance_pct"], 2)
    out.update({"count": count, "label": label, "levels": near,
                "span_pct": span})
    return out


# A stop closer than this is inside ordinary daily noise for most names, so
# a ratio measured against it says more about the level's proximity than
# about the trade.
MIN_STOP_PCT = 1.5


def compute_targets(row: dict) -> dict:
    """Stop, T1 and T2 — the trade priced, so risk/reward is a number.

    T1 is the nearest resistance overhead and T2 the next one above it. Both
    come from the same ladder the rest of the module uses (the key-level
    engine's R1, the moving averages, the 52-week high), so a target is
    always a price something has actually happened at rather than a
    percentage picked to look good.

    Which target matters depends on the setup, which is why both are
    reported. Western Digital at $434.30 with support at $422.50 risks 2.7%;
    to R1 at $454.49 that is 1.7:1, but to the 8 EMA at $491.46 — the level
    a deep pullback is actually trying to reclaim — it is 4.8:1. Quoting
    only the nearer target would understate the setup by a factor of three.
    """
    price = _price(row)
    out = {"stop": None, "stop_name": None, "risk_pct": None, "t1": None,
           "t2": None, "rr_t1": None, "rr_t2": None, "ladder": []}
    if price is None or price <= 0:
        return out

    # The stop is the nearest level that is actually FAR ENOUGH to be one.
    # compute_supports() returns the closest level of any kind, and a moving
    # average sitting 1% under the price is not a stop — it is inside a
    # normal session. Taking it anyway manufactures a 30:1 setup out of a
    # level that will be lost on a quiet Tuesday.
    below = []
    for key, _zone, label in SUPPORT_LEVELS:
        lv = f(row.get(key))
        if lv and lv < price:
            below.append((lv, label))
    s1 = f(row.get("S1"))
    if s1 and s1 < price:
        below.append((s1, "volume shelf"))
    # Nearest first, but skipping anything inside MIN_STOP_PCT.
    candidates = sorted(((lv, label) for lv, label in below
                         if (price - lv) / price * 100 >= MIN_STOP_PCT),
                        reverse=True)
    if not candidates:
        return out
    stop, stop_name = candidates[0]
    out["stop_name"] = stop_name
    risk = price - stop
    out["stop"] = round(stop, 2)
    out["risk_pct"] = round((stop / price - 1) * 100, 1)

    # Every level above the price, nearest first, deduped on price.
    above = []
    r1 = f(row.get("R1"))
    if r1 and r1 > price:
        above.append((r1, "R1" + (f" ({f(row.get('Touches')):.0f} touches)"
                                  if _touches_belong_to(row) == "R1"
                                  and f(row.get("Touches")) else "")))
    for key, _zone, label in SUPPORT_LEVELS:
        lv = f(row.get(key))
        if lv and lv > price:
            above.append((lv, label))
    high = f(row.get("52W High"))
    if high and high > price:
        above.append((high, "52W high"))

    seen, ladder = set(), []
    for level, label in sorted(above):
        key = round(level, 2)
        if key in seen:
            continue
        seen.add(key)
        ladder.append({"price": key, "name": label,
                       "move_pct": round((level / price - 1) * 100, 1),
                       "rr": round((level - price) / risk, 2)})

    if ladder:
        out["t1"] = ladder[0]
        out["rr_t1"] = ladder[0]["rr"]
    if len(ladder) > 1:
        out["t2"] = ladder[1]
        out["rr_t2"] = ladder[1]["rr"]
    out["ladder"] = ladder[:4]
    return out


# Pure price-and-volume. Nothing here reads a margin, a growth rate or a
# multiple — the whole point is a second opinion on timing that shares no
# input with the quality or valuation gates, so agreement between them means
# something.
TECHNICAL_WEIGHTS = (("Trend structure", 25), ("Pullback quality", 20),
                     ("Support quality", 20), ("Risk / reward", 20),
                     ("Momentum & volume", 15))

# Depth bands. Being FURTHER below the 50 MA is not a better entry — past a
# point it stops being a pullback and becomes a decline, and a score that
# rises with depth hunts falling knives.
DEPTH_BANDS = ((3, 60), (7, 85), (12, 100), (20, 70), (30, 35), (999, 10))


def compute_technical_score(row: dict, trend: dict, pullback: dict,
                            confluence: dict, volume: dict,
                            targets: dict) -> dict:
    """0-100 from price and volume alone."""
    parts = []

    parts.append(("Trend structure", 25, _as_num(trend.get("score")),
                  trend.get("state") or ""))

    vs50 = f(row.get("Price_vs_50MA%"))
    if vs50 is None:
        parts.append(("Pullback quality", 20, None, "no 50 MA distance"))
    elif vs50 > 0:
        # Above the 50 MA: reward being close to it, not far above.
        parts.append(("Pullback quality", 20,
                      band(vs50, [(3, 90), (8, 70), (15, 45), (99, 20)]),
                      f"{vs50:+.1f}% above the 50 MA"))
    else:
        parts.append(("Pullback quality", 20, band(abs(vs50), DEPTH_BANDS),
                      f"{abs(vs50):.1f}% below the 50 MA"))

    bz = pullback.get("buy_zone") or {}
    sup = None if confluence.get("score") is None else float(confluence["score"])
    if sup is not None and bz.get("actual_support"):
        sup = min(100.0, sup + 25)          # a tested shelf is worth more
    parts.append(("Support quality", 20, sup,
                  "tested shelf" if bz.get("actual_support") else "derived level"))

    rr = targets.get("rr_t2") or targets.get("rr_t1")
    parts.append(("Risk / reward", 20,
                  None if rr is None else scale(rr, 0.5, 4.0),
                  "no target" if rr is None else f"{rr:.1f}:1 to target"))

    rsi = f(row.get("RSI_14"))
    vol = volume.get("score")
    momentum = []
    if rsi is not None:
        momentum.append(band(rsi, [(30, 55), (45, 90), (60, 100), (70, 55),
                                   (99, 15)]))
    if vol is not None:
        momentum.append(float(vol))
    parts.append(("Momentum & volume", 15,
                  sum(momentum) / len(momentum) if momentum else None,
                  f"RSI {rsi:.0f}" if rsi is not None else "no RSI"))

    from stockanalysis.core.longterm._common import blend as _blend
    out = _blend(parts)
    out["label"] = next(
        name for floor, name in ((80, "🔥 Excellent"), (65, "🟢 Good"),
                                 (50, "🟡 Fair"), (0, "🔴 Poor"))
        if (out["score"] or 0) >= floor)
    return out


def _as_num(v):
    return None if v is None else float(v)


def compute_resistance_level(row: dict) -> dict:
    """The first thing overhead — where a rally would meet sellers.

    Mirrors compute_buy_zone_level() on the other side of the tape, with the
    same tested-versus-derived distinction. R1 from the key-level engine is a
    price the market has actually turned down from; a moving average or the
    52-week high is where one happens to sit.

    The fallback chain matters more here than on the support side. A stock in
    a clean uptrend has every moving average BELOW it — Nvidia, Microsoft and
    Gilead all do — so the only thing overhead is the 52-week high, and
    without that rung the column would be empty for exactly the names most
    worth owning.
    """
    price = _price(row)
    out = {"price": None, "distance_pct": None, "source": None,
           "touches": None, "actual_resistance": False, "label": None,
           "note": "nothing overhead — price is at new highs"}
    if price is None or price <= 0:
        return out

    def fill(level, source, label, actual, note, touches=None):
        out.update({"price": round(level, 2),
                    "distance_pct": round((level / price - 1) * 100.0, 1),
                    "source": source, "label": label,
                    "actual_resistance": actual, "note": note,
                    "touches": touches})
        return out

    r1 = f(row.get("R1"))
    if r1 is not None and r1 > price:
        touches = (f(row.get("Touches"))
                   if _touches_belong_to(row) == "R1" else None)
        return fill(r1, "key_level",
                    f"{touches:.0f} touches" if touches else "prior high",
                    True,
                    "tested resistance — price has turned down from here",
                    touches)

    above = []
    for key, _zone, label in SUPPORT_LEVELS:
        level = f(row.get(key))
        if level is not None and level > price:
            above.append((level, label))
    if above:
        level, label = min(above)
        return fill(level, "moving_average", label, False,
                    f"no tested level overhead — this is the {label}, which "
                    f"price has fallen below rather than turned down from")

    high = f(row.get("52W High"))
    if high is not None and high > price:
        return fill(high, "52w_high", "52W high", False,
                    "no nearer level — the 52-week high is the next "
                    "meaningful overhead price")
    return out


def compute_buy_zone_level(row: dict) -> dict:
    """The price to actually plan an entry around, and how much it is worth.

    Two very different things can fill this slot, and conflating them is the
    trap:

      A TESTED level — the key-level engine's S1, a shelf the market has
      traded heavily at and defended before. Western Digital's $420.26 has
      329 touches and volume confirmation. That is a real place to work an
      order.

      A DERIVED level — a moving average that happens to be the next line
      under the price. Nobody has defended it; it is arithmetic on the last
      50 closes. It is still the most useful number available when no tested
      level exists, but presenting it in the same voice would turn "here is
      where support probably is" into "here is where support is".

    So `actual_support` is returned on every result and the caller is
    expected to render the two differently. 425 of 545 library rows have a
    key level and 348 are volume-confirmed, so the derived case is roughly a
    third of the page — common enough that mislabelling it would matter.
    """
    price = _price(row)
    out = {"price": None, "distance_pct": None, "source": None,
           "touches": None, "volume_confirmed": None, "actual_support": False,
           "label": None, "note": "no level beneath the current price"}
    if price is None or price <= 0:
        return out

    def fill(level, source, label, actual, note, touches=None, confirmed=None):
        out.update({"price": round(level, 2),
                    "distance_pct": round((level / price - 1) * 100.0, 1),
                    "source": source, "label": label, "actual_support": actual,
                    "note": note, "touches": touches,
                    "volume_confirmed": confirmed})
        return out

    s1 = f(row.get("S1"))
    if s1 is not None and 0 < s1 <= price:
        # Only claim the touch count when it actually describes S1 — the scan
        # writes it for whichever level is nearest. See _touches_belong_to().
        owns = _touches_belong_to(row) == "S1"
        touches = f(row.get("Touches")) if owns else None
        confirmed = b(row.get("Volume_Confirmation")) if owns else None
        bits = []
        if touches:
            bits.append(f"{touches:.0f} touches")
        if confirmed:
            bits.append("volume-confirmed")
        return fill(s1, "volume_shelf",
                    " · ".join(bits) or "prior support",
                    bool(confirmed),
                    ("tested support — the market has defended this price"
                     if confirmed else
                     "prior support, but not volume-confirmed"),
                    touches, confirmed)

    # Fall back to the nearest moving average below price. Named after the
    # average it came from so the card can say where it came from rather
    # than presenting a computed line as a level someone defended.
    below = []
    for key, _zone, label in SUPPORT_LEVELS:
        level = f(row.get(key))
        if level is not None and 0 < level <= price:
            below.append((level, label))
    if below:
        level, label = max(below)
        return fill(level, "moving_average", label, False,
                    f"no tested level — this is the {label}, not support "
                    f"anyone has defended")
    return out


def compute_supports(row: dict) -> dict:
    """Near-term support and major support, as two separate answers.

    They are not interchangeable. For Western Digital the 200 MA sits at
    $341 — 22% below the tape — while a volume-confirmed shelf at $420 is
    4.1% away. Only one of those is a level you can plan an entry around
    this week, and a system that reports the moving average because it is
    the one with a name is answering the wrong question.

    "Near" is the closest level beneath price from any method (the key-level
    engine's volume-confirmed S1, a moving average, a prior breakout).
    "Major" is the 200 MA, the structural floor the whole thesis rests on.
    """
    price = _price(row)
    out = {"near": None, "major": None}
    if price is None or price <= 0:
        return out

    def entry(name, level, kind, confirmed=None):
        if level is None or level <= 0 or level > price:
            return None
        return {"name": name, "price": round(level, 2),
                "distance_pct": round((level / price - 1) * 100.0, 1),
                "kind": kind,
                "volume_confirmed": confirmed}

    candidates = []
    s1 = entry("volume shelf", f(row.get("S1")), "key_level",
               b(row.get("Volume_Confirmation")))
    if s1:
        touches = f(row.get("Touches"))
        if touches:
            s1["name"] = f"volume shelf ({touches:.0f} touches)"
        candidates.append(s1)
    for key, _zone, label in SUPPORT_LEVELS:
        got = entry(label, f(row.get(key)), "moving_average")
        if got:
            candidates.append(got)

    if candidates:
        # Nearest below price — the one a pullback reaches first.
        out["near"] = max(candidates, key=lambda c: c["distance_pct"])

    ma200 = f(row.get("200MA"))
    if ma200 is not None and ma200 > 0:
        out["major"] = {"name": "200 MA", "price": round(ma200, 2),
                        "distance_pct": round((ma200 / price - 1) * 100.0, 1),
                        "kind": "moving_average", "volume_confirmed": None}
    return out


def compute_52w_range(row: dict) -> dict:
    """Where price sits in its own 52-week range.

    The two endpoints are not interchangeable readings. Distance from the
    high says how much of a drawdown you are buying; distance from the low
    says how much of a recovery you are paying for. A stock 8% off its high
    and one 8% off its low can carry the same LQuality and are not the same
    purchase, and `position_pct` — where in the range price actually sits —
    is the one number that separates them at a glance.
    """
    price = _price(row)
    high, low = f(row.get("52W High")), f(row.get("52W Low"))
    from_high = f(row.get("Dist_52W_High%"))
    if from_high is None and price and high:
        from_high = (price / high - 1) * 100.0
    from_low = f(row.get("Pct_From_52W_Low%"))
    if from_low is None and price and low:
        from_low = (price / low - 1) * 100.0

    position = None
    if price is not None and high is not None and low is not None and high > low:
        position = max(0.0, min(100.0, (price - low) / (high - low) * 100.0))

    return {
        "high": high, "low": low, "price": price,
        "from_high_pct": None if from_high is None else round(from_high, 1),
        "from_low_pct": None if from_low is None else round(from_low, 1),
        "position_pct": None if position is None else round(position, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SUPPORT CONFLUENCE — §8. How many independent levels agree.
# ─────────────────────────────────────────────────────────────────────────────
# The weights are the framework's. What matters more than the weights is
# what each one is allowed to count: only levels price is currently holding
# above (see _supports), so the score cannot be run up by a stock in free
# fall passing through four averages at once.

CONFLUENCE_POINTS = (
    ("8/21 EMA", 20),
    ("50 MA", 25),
    ("200 MA", 25),
    ("Prior breakout", 15),
    ("High-volume level", 10),
    ("Key-level confluence", 5),
)


def compute_support_confluence(row: dict) -> dict:
    """
    Returns {"score": 0-100, "label", "hits": [...], "misses": [...]}.

    Unlike the other scores in this package this one does NOT renormalise
    over measured weight, and that is deliberate. Confluence is a count of
    confirmations present; a level that could not be measured is a
    confirmation you do not have, which is the same practical position as
    one that is absent. Renormalising would let a row with two of six
    inputs measured score 100 for a single agreement.
    """
    price = _price(row)
    tol = _tolerance(row)
    hits, misses = [], []
    score = 0

    def award(name, points, ok, detail):
        nonlocal score
        if ok:
            score += points
            hits.append({"name": name, "points": points, "detail": detail})
        else:
            misses.append({"name": name, "points": points, "detail": detail})

    # 8/21 EMA — either one holding counts once, not twice. They are the
    # same short-term signal at two lookbacks.
    ok8, d8 = _supports(price, f(row.get("8EMA")), tol)
    ok21, d21 = _supports(price, f(row.get("21EMA")), tol)
    best = min((d for d, ok in ((d8, ok8), (d21, ok21)) if ok),
               default=None)
    award("8/21 EMA", 20, ok8 or ok21,
          f"holding, {best:+.1f}% above" if best is not None
          else "price is not on the short-term averages")

    ok50, d50 = _supports(price, f(row.get("50MA")), tol)
    award("50 MA", 25, ok50,
          f"holding, {d50:+.1f}% above" if ok50
          else ("below it" if d50 is not None and d50 < 0
                else f"{d50:+.1f}% away" if d50 is not None else "no 50 MA"))

    ok200, d200 = _supports(price, f(row.get("200MA")), tol)
    award("200 MA", 25, ok200,
          f"holding, {d200:+.1f}% above" if ok200
          else ("below it" if d200 is not None and d200 < 0
                else f"{d200:+.1f}% away" if d200 is not None else "no 200 MA"))

    okbo, dbo = _supports(price, f(row.get("Prior_Breakout_Level")), tol)
    award("Prior breakout", 15, okbo,
          f"retesting the breakout, {dbo:+.1f}% above" if okbo
          else ("breakout level lost" if dbo is not None and dbo < 0
                else "not at the prior breakout"))

    # A level the market traded heavily at is a level the market remembers.
    # Volume_Confirmation and Touches come from core/key_levels.
    s1 = f(row.get("S1"))
    oks1, ds1 = _supports(price, s1, tol)
    vol_conf = b(row.get("Volume_Confirmation"))
    award("High-volume level", 10, bool(oks1 and vol_conf),
          f"volume-confirmed support at ${s1:,.2f}" if oks1 and vol_conf
          else ("support found but not volume-confirmed" if oks1
                else "no volume-confirmed level here"))

    # The fifth point is for genuine agreement between an independently
    # derived level (key_levels' swing/volume-profile S1) and a moving
    # average — two different methods pointing at the same price.
    touches = f(row.get("Touches"))
    ma_near_s1 = False
    if s1 is not None:
        for key in ("8EMA", "21EMA", "50MA", "200MA"):
            lv = f(row.get(key))
            if lv and abs(lv / s1 - 1) * 100 <= tol:
                ma_near_s1 = True
                break
    award("Key-level confluence", 5, bool(oks1 and ma_near_s1),
          f"S1 ${s1:,.2f} coincides with a moving average"
          f"{f' ({touches:.0f} touches)' if touches else ''}"
          if oks1 and ma_near_s1 else "no independent level agrees here")

    n_hits = len(hits)
    label = next(name for floor, name in CONFLUENCE_BANDS if n_hits >= floor)
    return {"score": score, "label": label, "hits": hits, "misses": misses,
            # The headline number. `score` is kept because WHICH levels agree
            # still matters — a 50 MA plus 200 MA agreement is worth more than
            # the 8/21 EMA plus a key level, and both are two hits — but it
            # ranks within a count rather than replacing it.
            "agreeing": n_hits, "possible": len(CONFLUENCE_POINTS)}


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL STRENGTH — how good is a support, asked about the LEVEL not the price
# ─────────────────────────────────────────────────────────────────────────────
# compute_support_confluence above scores TODAY'S PRICE: it is direction-aware
# and counts only levels price currently sits above, which is exactly right
# for "should I buy now" and useless for "is $193 a good place to add". A
# level 13% below the tape supports nothing yet — by that function's rule it
# scores zero, and an accumulation ladder needs to compare three such levels
# against each other before price reaches any of them.
#
# So this asks the other question. Given a price, how strong is the support
# THERE: what kind of level is it, how many independent levels agree with it,
# and did the market trade heavily at it.
#
# What is deliberately NOT scored
# --------------------------------
# Touch count. `Touches` comes from core.key_levels, which builds S1 from
# ~20 days of 5-MINUTE bars, so it counts bars spent near a level rather than
# times a level was defended. Measured on the live library it runs from 4 to
# 1,015 with a median of 181 — a distribution with no defensible saturation
# point, and one whose scale is an artifact of the bar size rather than a
# property of the level. `Volume_Confirmation` is derived from the same data
# but as a RATIO (touch volume >= 1.2x), which is scale-free and does carry a
# claim, so that is what earns points here. The raw count is passed through as
# context and given no weight.
#
# Historical defence — how often price fell to this level and bounced — is the
# input that would most improve this score and is not derivable from a scan
# row. It needs the daily frame, which is why it belongs in daily_signals.py
# and is not faked here.

# What the level IS, and which FAMILY it belongs to. A 200-day average is a
# different kind of object from an 8-day one: more capital has been priced
# against it, more systematic strategies watch it, and it moves slowly enough
# that it is in the same place next week. That is the whole reason a
# three-rung ladder gets deeper rather than merely lower.
#
# The family is what stops the 8 and 21 EMA agreeing with each other. They are
# one short-term signal read at two lookbacks — compute_support_confluence
# above counts them once for the same reason. Scored without families the
# EMA rung measured out STRONGER than the 200 MA rung across the live library
# (median 70 vs 40), purely because its two members are always within
# tolerance of one another. Two EMAs at one price is not a cluster.
LEVEL_IDENTITY = (
    ("200MA", "200ma", "200 MA", 45),
    ("50MA", "50ma", "50 MA", 32),
    ("Prior_Breakout_Level", "breakout", "prior breakout", 26),
    ("21EMA", "ema", "21 EMA", 15),
    ("8EMA", "ema", "8 EMA", 12),
)
# Each other FAMILY sitting within tolerance of the level being scored. This
# is the user-facing "support cluster": three independent things at one price
# is a level the market has to work through, not a line on a chart.
LEVEL_AGREEMENT_POINTS = 14
LEVEL_AGREEMENT_MAX = 35
LEVEL_VOLUME_POINTS = 20

# Bands calibrated against the live 552-name library rather than adopted from
# the framework's suggested 90/75/60, which measured out badly: on the real
# distribution those cutoffs left the top band nearly empty and filed most of
# the ladder under "weak", so the label only repeated what the rung name
# already said. These sit where the score actually separates a lone moving
# average (45) from a genuine confluence.
LEVEL_BANDS = ((75, "🟢 Major"), (55, "🟡 Strong"),
               (40, "🟠 Moderate"), (0, "🔴 Weak"))


def score_support_level(row: dict, level_price: float | None) -> dict:
    """How strong is support at `level_price`, from the row alone.

    Scores a PRICE, not the current tape, so it is meaningful for levels
    price has not reached — which is the only reason an accumulation ladder
    can rank its own rungs before any of them fill.

    Returns {"score" 0-100, "label", "identity", "agreeing", "hits",
             "misses", "touches"}. `score` is None when `level_price` is
    missing; a level with no recognisable identity scores 0 rather than
    None, because "nothing tracked is here" is a measurement.
    """
    out = {"score": None, "label": None, "identity": None, "agreeing": [],
           "hits": [], "misses": [], "touches": f(row.get("Touches"))}
    price = f(level_price)
    if price is None or price <= 0:
        return out

    tol = _tolerance(row)

    def near(value) -> bool:
        return value is not None and value > 0 and \
            abs(value / price - 1) * 100.0 <= tol

    score = 0
    hits, misses = [], []

    # ── Identity: the strongest tracked level standing at this price ────────
    # Strongest, not nearest. When the 50 MA and the 8 EMA are both within
    # tolerance the level is a 50 MA that an 8 EMA happens to be visiting,
    # and scoring it as the 8 EMA would rank the rung by its weakest member.
    matched = [(key, family, name, pts)
               for key, family, name, pts in LEVEL_IDENTITY
               if near(f(row.get(key)))]
    own_family = None
    if matched:
        key, own_family, name, pts = max(matched, key=lambda m: m[3])
        score += pts
        out["identity"] = name
        hits.append({"name": f"Level is the {name}", "points": pts,
                     "detail": f"${f(row.get(key)):,.2f}"})
    else:
        misses.append({"name": "Level identity", "points": LEVEL_IDENTITY[0][3],
                       "detail": "no tracked average or breakout at this price"})

    # ── Agreement: the other FAMILIES standing at the same price ────────────
    # By family, so the 8 EMA cannot corroborate the 21 EMA. Best member of
    # each family names it, so "50 MA" is reported rather than whichever
    # member happened to be listed first.
    agreeing, seen = [], {own_family}
    for _key, family, name, _pts in LEVEL_IDENTITY:
        if family in seen or not near(f(row.get(_key))):
            continue
        seen.add(family)
        agreeing.append(name)
    s1 = f(row.get("S1"))
    if near(s1):
        agreeing.append("volume shelf")
    out["agreeing"] = agreeing
    if agreeing:
        pts = min(LEVEL_AGREEMENT_MAX,
                  LEVEL_AGREEMENT_POINTS * len(agreeing))
        score += pts
        hits.append({"name": f"{len(agreeing)} independent level"
                             f"{'s' if len(agreeing) > 1 else ''} agree",
                     "points": pts, "detail": ", ".join(agreeing)})
    else:
        misses.append({"name": "Level agreement",
                       "points": LEVEL_AGREEMENT_POINTS,
                       "detail": "this level stands alone"})

    # ── Volume: did the market trade size here ──────────────────────────────
    vol_conf = b(row.get("Volume_Confirmation"))
    if near(s1) and vol_conf:
        score += LEVEL_VOLUME_POINTS
        hits.append({"name": "Volume-confirmed", "points": LEVEL_VOLUME_POINTS,
                     "detail": f"heavy trade at ${s1:,.2f}"})
    else:
        misses.append({"name": "Volume-confirmed", "points": LEVEL_VOLUME_POINTS,
                       "detail": ("volume shelf here but not confirmed"
                                  if near(s1) else
                                  "no volume-confirmed level at this price")})

    score = int(min(100, score))
    out.update({"score": score,
                "label": next(name for floor, name in LEVEL_BANDS
                              if score >= floor),
                "hits": hits, "misses": misses})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# PULLBACK VOLUME — §10. Is this a rest or a run for the exit?
# ─────────────────────────────────────────────────────────────────────────────

VOLUME_POINTS = (
    ("Volume below 20-day average", 20),
    ("Volume declining into the pullback", 20),
    ("No recent distribution", 20),
    ("Bullish volume reversal", 20),
    ("Accumulation at support", 20),
)


def compute_pullback_volume(row: dict) -> dict:
    """
    Returns {"score": 0-100|None, "label", "hits", "misses", "unknown",
             "measured"}.

    Renormalised over the checks that had data — unlike confluence, because
    here a missing input really is unknown rather than absent: a row without
    a distribution-day count has not been shown to be distributing.
    `measured` reports how many of the five actually answered, so a 100 from
    two checks is legible as the weaker claim it is.
    """
    vs20 = f(row.get("Vol_vs_20D"))
    pull_ratio = f(row.get("Pullback_Vol_Ratio"))
    drying = b(row.get("VolumeDryingUp"))
    dist_days = f(row.get("Distribution_Days_25d"))
    reversal = row.get("Reversal_Candle")
    rvol = f(row.get("RVOL"))

    hits, misses, unknown = [], [], []
    earned, possible = 0, 0

    def check(name, points, ok, detail):
        nonlocal earned, possible
        if ok is None:
            unknown.append({"name": name, "points": points, "detail": detail})
            return
        possible += points
        if ok:
            earned += points
            hits.append({"name": name, "points": points, "detail": detail})
        else:
            misses.append({"name": name, "points": points, "detail": detail})

    check("Volume below 20-day average", 20,
          None if vs20 is None else vs20 < 1.0,
          "no volume ratio" if vs20 is None else f"{vs20:.2f}× the 20-day average")

    check("Volume declining into the pullback", 20,
          None if pull_ratio is None else pull_ratio <= 0.9,
          "no pullback volume ratio" if pull_ratio is None
          else f"pullback volume {pull_ratio:.2f}× normal")

    # A distribution day is a down session on above-average volume — one is
    # noise, a cluster is institutions leaving.
    check("No recent distribution", 20,
          None if dist_days is None else dist_days <= 2,
          "not measured" if dist_days is None
          else f"{dist_days:.0f} distribution days in the last 25 sessions")

    # The reversal is the trigger, and volume is what makes it credible: a
    # hammer on 0.4× volume is a quiet day, not a decision by anyone.
    if reversal is None:
        check("Bullish volume reversal", 20, None, "candle pattern not measured")
    else:
        pattern = str(reversal).strip()
        bullish = bool(pattern) and pattern.lower() not in ("none", "false", "")
        if not bullish:
            check("Bullish volume reversal", 20, False, "no reversal candle")
        elif rvol is None:
            check("Bullish volume reversal", 20, None,
                  f"{pattern} but volume unknown")
        else:
            check("Bullish volume reversal", 20, rvol >= 1.0,
                  f"{pattern} on {rvol:.2f}× volume")

    check("Accumulation at support", 20,
          None if drying is None else bool(drying),
          "not measured" if drying is None
          else ("volume drying up into the level" if drying
                else "volume not contracting"))

    if not possible:
        return {"score": None, "label": None, "hits": hits, "misses": misses,
                "unknown": unknown, "measured": 0}

    score = int(round(earned / possible * 100))
    label = next(name for floor, name in VOLUME_BANDS if score >= floor)
    return {"score": score, "label": label, "hits": hits, "misses": misses,
            "unknown": unknown, "measured": len(hits) + len(misses)}


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY READINESS — "is now the moment", scored separately from "is this
# support"
# ─────────────────────────────────────────────────────────────────────────────
# Support confluence asks how many levels agree at this price. Entry
# readiness asks whether the conditions for acting are actually present:
# the level, the volume behaviour, the reversal, the market. A name can have
# excellent confluence and zero readiness — sitting on a great level while
# selling is still accelerating — and those must not collapse into one
# number, because the first tells you WHERE and the second tells you WHEN.

READINESS_BANDS = ((80, "🔥 Buy"), (65, "🟢 Buy on confirmation"),
                   (50, "🟡 Watch support"), (0, "🔴 Wait"))

# Weights. "Moving-average support" is ONE slot covering the 8/21 EMA, the
# 50 MA and the 200 MA, awarded for whichever price is actually resting on.
#
# Scored as separate 15-point rows for "Near 50 MA" and "Near 200 MA" — the
# obvious transcription — the top band is unreachable by construction,
# because price cannot be at both at once. That capped a Stage 1 EMA
# pullback at 70 and a deep pullback at 70, so "🔥 Buy" could only ever be
# reached by a stock sitting exactly on its 50 MA. There was also no row at
# all for the 8/21 EMA, which is the framework's own Entry 1. Collapsing
# them into one mutually-exclusive slot and adding the missing rung fixes
# both without touching any other weight.
READINESS_CHECKS = (
    ("At major support", 25),
    ("Moving-average support", 15),
    ("Prior breakout support", 15),
    ("Volume contraction", 10),
    ("Bullish reversal", 10),
    ("Relative strength stabilising", 5),
    ("Market supportive", 5),
)
# What a perfect setup can earn. The reported score is rescaled to 0-100
# against this so the framework's 80/65/50 band boundaries mean what they
# say; `earned` and `possible` are returned raw for anyone checking.
READINESS_MAX = sum(w for _n, w in READINESS_CHECKS)

# Nearest first — the order proximity is tested in, so a stock at the 8 EMA
# is credited for the EMA rather than for a 200 MA far below it.
_READINESS_LEVELS = (("8EMA", "8 EMA"), ("21EMA", "21 EMA"),
                     ("50MA", "50 MA"), ("200MA", "200 MA"))


def compute_entry_readiness(row: dict, pullback: dict, rs: dict,
                            regime: str | None = None) -> dict:
    """
    0-100 with the framework's weights. Returns {"score", "label", "hits",
    "misses", "unknown"}.

    Not renormalised: a confirmation that could not be measured is a
    confirmation you do not have, and this score exists to decide whether to
    act now. Unknowns are listed so the gap is visible rather than inferred
    from a low number.
    """
    hits, misses, unknown = [], [], []
    score = 0

    def award(name, points, ok, detail):
        nonlocal score
        entry = {"name": name, "points": points, "detail": detail}
        if ok is None:
            unknown.append(entry)
        elif ok:
            score += points
            hits.append(entry)
        else:
            misses.append(entry)

    by_level = pullback.get("by_level") or {}
    supports = pullback.get("supports") or {}
    tol = pullback.get("tolerance_pct") or MIN_TOLERANCE_PCT

    # "At major support" is the volume-confirmed shelf the key-level engine
    # found, not simply the nearest moving average — the whole point of the
    # 25-point weight is that the market has defended this price before.
    near = supports.get("near")
    at_major = bool(near and near.get("kind") == "key_level"
                    and near.get("volume_confirmed")
                    and abs(near["distance_pct"]) <= tol)
    award("At major support", 25, at_major,
          (f"{near['name']} at ${near['price']:,.2f}, "
           f"{near['distance_pct']:+.1f}% away" if near
           else "no volume-confirmed level beneath price"))

    def near_level(key):
        lv = by_level.get(key) or {}
        d = lv.get("distance_pct")
        if d is None:
            return None, None, None
        return abs(d) <= tol, d, lv.get("price")

    # One slot, awarded for whichever moving average price is actually on.
    resting, measured = None, False
    for key, label in _READINESS_LEVELS:
        ok, dist, level_price = near_level(key)
        if ok is None:
            continue
        measured = True
        if ok:
            resting = (label, dist, level_price)
            break
    if not measured:
        award("Moving-average support", 15, None, "no moving averages")
    elif resting:
        label, dist, level_price = resting
        award("Moving-average support", 15, True,
              f"at the {label}" + (f" (${level_price:,.2f}, {dist:+.1f}%)"
                                   if level_price else ""))
    else:
        award("Moving-average support", 15, False,
              "not resting on any moving average")

    ok, dist, level_price = near_level("Prior_Breakout_Level")
    award("Prior breakout support", 15, ok,
          "no prior breakout level" if ok is None
          else (f"retesting ${level_price:,.2f} ({dist:+.1f}%)" if ok
                else f"{dist:+.1f}% away"))

    vs20 = f(row.get("Vol_vs_20D"))
    drying = b(row.get("VolumeDryingUp"))
    pull_ratio = f(row.get("Pullback_Vol_Ratio"))
    if vs20 is None and drying is None and pull_ratio is None:
        award("Volume contraction", 10, None, "no volume data")
    else:
        contracting = bool((drying is True)
                           or (vs20 is not None and vs20 < 1.0)
                           or (pull_ratio is not None and pull_ratio <= 0.9))
        bits = []
        if vs20 is not None:
            bits.append(f"{vs20:.2f}x the 20-day average")
        if pull_ratio is not None:
            bits.append(f"pullback volume {pull_ratio:.2f}x")
        award("Volume contraction", 10, contracting,
              "; ".join(bits) or "volume not contracting")

    reversal = row.get("Reversal_Candle")
    if reversal is None:
        award("Bullish reversal", 10, None, "candle not measured")
    else:
        pattern = str(reversal).strip()
        found = bool(pattern) and pattern.lower() not in ("none", "false")
        award("Bullish reversal", 10, found,
              pattern if found else "no reversal candle yet")

    rs_score = rs.get("score")
    award("Relative strength stabilising", 5,
          None if rs_score is None else rs_score >= 40,
          "no relative strength data" if rs_score is None
          else rs.get("detail") or f"rank {rs_score:.0f}")

    reg = str(regime or "").upper()
    award("Market supportive", 5,
          None if reg not in ("FAVORABLE", "SELECTIVE", "DEFENSIVE")
          else reg != "DEFENSIVE",
          f"regime {reg}" if reg else "regime unknown")

    scaled = int(round(score / READINESS_MAX * 100)) if READINESS_MAX else 0
    label = next(name for floor, name in READINESS_BANDS if scaled >= floor)
    return {"score": scaled, "label": label, "earned": score,
            "possible": READINESS_MAX, "hits": hits, "misses": misses,
            "unknown": unknown}


# ─────────────────────────────────────────────────────────────────────────────
# RELATIVE STRENGTH — §9
# ─────────────────────────────────────────────────────────────────────────────

def relative_strength(row: dict) -> dict:
    """Market-wide RS rank plus, when the universe supplied it, the rank
    within the stock's own sector.

    Both matter and they say different things: a semiconductor at the 55th
    percentile of the whole market during a semiconductor rout may be the
    strongest name in its group. `Sector_RS_Rank` is attached by
    engine.evaluate_universe(); it cannot be computed from one row.
    """
    market = f(row.get("RS_Rank"))
    sector = f(row.get("Sector_RS_Rank"))
    parts = []
    if market is not None:
        parts.append(f"market rank {market:.0f}")
    if sector is not None:
        parts.append(f"sector rank {sector:.0f}")
    # Both are percentiles, so averaging them is meaningful where both exist.
    have = [v for v in (market, sector) if v is not None]
    score = sum(have) / len(have) if have else None
    return {"score": None if score is None else round(score),
            "market_rank": market, "sector_rank": sector,
            "detail": " · ".join(parts) or "no relative strength data",
            "strong": None if score is None else score >= 70}


def technical_sub_score(trend: dict, pullback: dict, confluence: dict,
                        volume: dict, rs: dict) -> float | None:
    """The 25% technical leg of the composite LT score.

    Trend and confluence carry the most weight because they are the two the
    framework treats as preconditions rather than preferences. The pullback
    term scores being AT a level at all — a stock with perfect trend and
    volume that is nowhere near an entry should not score as a ready setup.
    """
    parts = []
    if trend.get("score") is not None:
        parts.append((35, float(trend["score"])))
    if confluence.get("score") is not None:
        parts.append((25, float(confluence["score"])))
    if volume.get("score") is not None:
        parts.append((15, float(volume["score"])))
    if rs.get("score") is not None:
        parts.append((15, float(rs["score"])))
    zone = pullback.get("zone")
    if zone is not None:
        parts.append((10, 0.0 if zone == "NONE" else 100.0))
    if not parts:
        return None
    total = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / total
