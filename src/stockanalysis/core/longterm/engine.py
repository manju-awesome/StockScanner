"""
engine.py — the four gates, in order
====================================
This is where the hierarchy is enforced:

    business quality > valuation > long-term trend > support > entry trigger

Structurally, not by weighting. `evaluate()` walks the gates in that order
and a failure at any level stops the ones below it from mattering. A 100
technical score on a 62-quality company does not produce a buy, because the
technical gate is never reached.

That is the difference between this engine and a weighted composite. With
weights alone, a strong enough chart drags a mediocre business over any
threshold you set — you can push the threshold up, but you cannot stop the
substitution, and the whole framework exists to refuse that substitution.
`LT_Score` is still computed and still 40/20/25/15, but it ranks names that
have already passed the gates. It never decides whether they pass.

Actions
-------
    BUY NOW           at a qualifying level, with confirmation, now
    BUY ON 8/21 EMA   own it; wait for the shallow pullback
    BUY ON 50 MA      own it; wait for the core entry
    BUY ON 200 MA     own it; wait for the deep entry (elite only)
    BUY ON BREAKOUT RETEST   own it; wait for the prior breakout to be retested
    WATCH             something is missing that price alone will not fix
    WAIT              good business, wrong price or imminent earnings
    AVOID             fails the quality gate, or the trend is broken

Every BUY ON action names a dollar price. "Wait for a pullback to the 50 MA"
is not an instruction anyone can act on; "$206.05, 8.0% below here" is.

Regime, from §11
----------------
The market regime does not score. It changes how selective the gates are:

    FAVORABLE   normal operation
    SELECTIVE   quality bars rise by SELECTIVE_QUALITY_BUMP — corrections
                are when only the best businesses are worth adding to
    DEFENSIVE   no BUY NOW at any quality. Everything that would have been
                a buy becomes a watchlist entry, which is §11's "build
                watchlist + preserve cash + wait for confirmation" and the
                single most valuable thing this engine can do in a bear
                market: nothing.
"""

from __future__ import annotations

from stockanalysis.core.longterm import buy_zones as BZ
from stockanalysis.core.longterm import quality as Q
from stockanalysis.core.longterm import technicals as T
from stockanalysis.core.longterm import thesis as TH
from stockanalysis.core.longterm import valuation as V
from stockanalysis.core.longterm._common import b, blend, f, s

# Minimum LQuality to enter at each zone. Straight from §4/§5/§6/§7: the
# deeper and more dangerous the entry, the better the business has to be,
# because a 200 MA pullback is the one that most often turns out to be
# fundamental deterioration rather than a correction.
ZONE_MIN_QUALITY = {"EMA": 85, "50MA": 85, "BREAKOUT": 85, "200MA": 90}

# Share of the intended full position each tranche takes (§7). Expressed
# throughout as "% of target position", never a bare "% tranche" — the
# ambiguity between "28% of this holding" and "28% of the portfolio" is the
# difference between a normal starter and a wildly concentrated bet.
ZONE_TRANCHE = {"EMA": 22, "50MA": 28, "BREAKOUT": 28, "200MA": 25}

# Size follows the ENTRY score, not the zone alone. A 50 MA touch inside a
# confirmed trend on contracting volume deserves more capital than the same
# touch with nothing else agreeing, and the zone cannot tell them apart.
ENTRY_TRANCHE = ((80, 40), (70, 28), (60, 18), (50, 8), (0, 0))


def tranche_for(entry_score, zone=None) -> int:
    """Percent OF THE TARGET POSITION to take on this entry."""
    if entry_score is None:
        return ZONE_TRANCHE.get(zone, 0)
    for floor, pct in ENTRY_TRANCHE:
        if entry_score >= floor:
            return pct
    return 0


# Proximity to a report, as its own risk reading. A setup does not become
# wrong before earnings; it becomes unreliable, and that is worth saying
# separately from the verdict rather than folding into it.
EARNINGS_RISK_BANDS = ((30, "🟢 Clear"), (15, "🟡 Approaching"),
                       (7, "🟠 Near"), (-1, "🔴 Imminent"))


def earnings_risk(days) -> dict:
    d = f(days)
    if d is None:
        return {"label": "⚪ Unknown", "days": None}
    if d < 0:
        return {"label": "🟢 Clear", "days": d}
    for floor, label in EARNINGS_RISK_BANDS:
        if d >= floor:
            return {"label": label, "days": d}
    return {"label": "🔴 Imminent", "days": d}

ZONE_ACTION = {"EMA": "BUY ON 8/21 EMA", "50MA": "BUY ON 50 MA",
               "200MA": "BUY ON 200 MA",
               "BREAKOUT": "BUY ON BREAKOUT RETEST"}

# A buy at any zone needs the support underneath it to be real. §6 STEP 6
# asks for "at least 2-3 confluences", and that is a COUNT of independent
# confirmations rather than a level on the weighted score.
#
# The distinction decides whether Zone A can ever fire. Gating on the
# weighted score at 50 requires price to sit on two major moving averages
# simultaneously, since the 50 MA and 200 MA carry 25 points each and the
# 8/21 EMA only 20. That is a deep pullback by definition — the exact
# opposite of the shallow, high-momentum entry Zone A exists to catch — so a
# score gate silently made §4 unreachable. Counting hits asks what the
# framework actually asks: is more than one thing agreeing here.
MIN_CONFLUENCE_HITS = 2
# ...and the pullback to look like rest rather than exit.
MIN_PULLBACK_VOLUME = 40

SELECTIVE_QUALITY_BUMP = 5

# "WATCH" said nothing about WHAT was being watched. A name can be held back
# by its price, its trend, or simply having no setup yet, and those are three
# different waits with three different triggers. Where the business is worth
# owning the action says so first, then names the wait.
ACTIONS = ("BUY NOW", "BUY ON CONFIRMATION", "BUY ON 8/21 EMA",
           "BUY ON 50 MA", "BUY ON BREAKOUT RETEST", "BUY ON 200 MA",
           "BUY ON SUPPORT", "DEEP PULLBACK — WAIT FOR SUPPORT",
           "OWN / WAIT FOR PRICE", "OWN / WAIT FOR TREND",
           "OWN / WAIT FOR ENTRY",
           "WATCH", "WAIT", "RESEARCH", "AVOID", "THESIS BROKEN")

# Which "wait" label to use, by what is holding the name back. Only offered
# to businesses good enough to own — below that the wait is about the
# company, and WATCH/RESEARCH say so.
OWN_AND_WAIT = {"price": "OWN / WAIT FOR PRICE",
                "trend": "OWN / WAIT FOR TREND",
                "entry": "OWN / WAIT FOR ENTRY"}
OWNABLE_TIERS = ("CORE", "OWN")

ACTION_ICONS = {"BUY NOW": "🟢", "BUY ON CONFIRMATION": "🟢",
                "BUY ON 8/21 EMA": "🟢", "BUY ON 50 MA": "🟢",
                "BUY ON BREAKOUT RETEST": "🟢", "BUY ON 200 MA": "🟢",
                "BUY ON SUPPORT": "🟢",
                "DEEP PULLBACK — WAIT FOR SUPPORT": "🟡",
                "OWN / WAIT FOR PRICE": "🟡", "OWN / WAIT FOR TREND": "🟡",
                "OWN / WAIT FOR ENTRY": "🟡",
                "WATCH": "🔵", "WAIT": "⏳", "RESEARCH": "🔎", "AVOID": "🔴",
                "THESIS BROKEN": "💀"}

# A buy that is not yet actionable needs the trend CONFIRMED, not merely
# un-broken. PARTIAL means something structural was never measured, and the
# most a name can earn on unmeasured structure is "buy when this confirms".
CONFIRMED_ONLY_ACTIONS = ("BUY NOW",)

# Readiness bands (core.longterm.technicals.READINESS_BANDS) mapped to what
# the engine is willing to do about them.
READY_BUY = 80
READY_CONFIRM = 65
READY_WATCH = 50

REGIME_SCORE = {"FAVORABLE": 100.0, "SELECTIVE": 55.0, "DEFENSIVE": 15.0}

# An imminent report is gap risk no setup quality offsets — the same guard
# decision_engine applies, for the same reason.
EARNINGS_BLOCK_DAYS = 7


def _earnings_days(row):
    return f(row.get("Days_To_Earnings"))


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITE SCORE — §14. Ranks names that already passed the gates.
# ─────────────────────────────────────────────────────────────────────────────

def compute_lt_score(lq, val, tech_sub, market_sub) -> dict:
    return blend([
        ("Fundamental quality", 40, None if lq.get("score") is None
         else float(lq["score"]), lq.get("tier") or "unrated"),
        ("Valuation", 20, V.valuation_sub_score(val),
         val.get("band") or "not priced"),
        ("Technical setup", 25, tech_sub, "trend, support, volume, RS"),
        ("Market & sector", 15, market_sub, "regime and sector strength"),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# TWO VERDICTS, NOT ONE NUMBER
# ─────────────────────────────────────────────────────────────────────────────
# "Excellent company" and "poor entry" are different findings, and a single
# blended score cannot hold both. Meta scored 51 with a 92 LQuality — a number
# that reads as a mediocre opportunity when the data says an elite business at
# a bad price. Averaging them destroys the only thing the reader needed.
#
# So the engine reports two statuses side by side:
#
#   INVESTMENT  do I want to own this company at all?   quality + valuation
#   ENTRY       is now the moment to buy it?            trend + support + volume
#
# LT_Score survives as a ranking key across names that already share a
# verdict. It is not the verdict.

# Tiers, not a pass/fail. The question "do I want to own this business" has
# more than two answers, and the top one has to survive a bad price.
INVESTMENT_TIERS = ((85, "CORE"), (75, "OWN"), (65, "WATCHLIST"), (0, "REJECT"))
INVESTMENT_ICONS = {"CORE": "💎", "OWN": "🟢", "WATCHLIST": "🟡",
                    "REJECT": "🔴"}
INVESTMENT_STATUSES = tuple(name for _f, name in INVESTMENT_TIERS)

# Valuation is 10% of the company question, not 30%.
#
# At 30% an expensive price dragged elite businesses out of the top tier —
# Nvidia at 98 quality landed in the same bucket as a mediocre company,
# which is the opposite of useful. A demanding price is a reason to wait for
# a better entry, not a reason to stop wanting the business; that judgment
# belongs to the ENTRY side, where waiting is the available action.
#
# The remaining 90% is LQuality, which already contains the framework's
# earnings growth, revenue growth, FCF, balance sheet and moat weights —
# restating them here would define the same thing twice and let the two
# copies drift.
INVESTMENT_WEIGHTS = (90, 10)


def investment_tier(score) -> str | None:
    if score is None:
        return None
    for floor, name in INVESTMENT_TIERS:
        if score >= floor:
            return name
    return INVESTMENT_TIERS[-1][1]


def _wait_label(investment: dict, holding_back: str) -> str:
    """Name the wait, and say first whether the business is worth owning.

    "WATCH" on Meta told the reader nothing: the company is a 93, and what
    is missing is a trend. "OWN / WAIT FOR TREND" is the same verdict with
    the useful half restored.
    """
    if (investment or {}).get("status") in OWNABLE_TIERS:
        return OWN_AND_WAIT[holding_back]
    return "WATCH" if holding_back == "trend" else "WAIT"


def investment_view(lq: dict, val: dict) -> dict:
    """The company verdict, computed without reference to timing.

    A name can be OWN here and WAIT on entry — that pairing is the single
    most useful thing this page says, and it is unrepresentable in one score.
    """
    quality = lq.get("score")
    val_sub = V.valuation_sub_score(val)
    blended = blend([
        ("Business quality", INVESTMENT_WEIGHTS[0],
         None if quality is None else float(quality), lq.get("tier") or ""),
        ("Valuation", INVESTMENT_WEIGHTS[1], val_sub, val.get("band") or ""),
    ])

    score = blended["score"]
    if quality is None or not lq.get("reliable"):
        status, why = "REJECT", "Quality could not be assessed"
    elif quality < Q.MIN_OWNABLE:
        status, why = "REJECT", (f"LQuality {quality} is below the "
                                 f"{Q.MIN_OWNABLE} ownable bar")
    else:
        status = investment_tier(score)
        why = {
            "CORE": "A business to own through cycles",
            "OWN": "Worth owning",
            "WATCHLIST": "Good, not yet a core holding",
            "REJECT": "Below the ownable bar",
        }[status]
        if val.get("acceptable") is False:
            why += " — but the price is demanding"
        elif val.get("acceptable") is None:
            why += " — the price could not be assessed"

    return {"score": score, "status": status,
            "icon": INVESTMENT_ICONS[status], "why": why,
            "priced_well": val.get("acceptable"),
            "coverage": blended["coverage"], "components": blended["components"]}


# The entry question. Trend carries the most weight because it is the one
# leg that can veto the others: strong support inside a broken trend is a
# reason to be careful, not a reason to buy.
#
# "Swing setup" is the scan's own Swing_Score, included because it is an
# INDEPENDENT read on the same question — is now the moment — built from
# pattern work this engine has no access to: the setup category, risk/reward
# to target, ATR contraction, Bollinger position. Brown & Brown is the case
# that argued for it: sitting on its 8 EMA with a Momentum-Pullback
# classification, 6.8 R:R and RS 88, the swing engine calls it a buy while
# the long-term readiness sees only a moving average nearby.
ENTRY_WEIGHTS = (("Long-term trend", 30), ("Entry readiness", 25),
                 ("Swing setup", 20), ("Support confluence", 15),
                 ("Pullback volume", 10))


def swing_signal(row: dict) -> tuple[float | None, str]:
    """The scan's Swing_Score, or None where the number is administrative.

    core.strategy_scores ZEROES the swing score whenever the entry gate
    failed — 126 of 552 library rows — and that zero means "no tradeable
    swing plan", not "a bad setup". Folding it in as a zero would punish
    long-term entries for a short-term gate, which the scan's own docstring
    warns against: a 6-24 month accumulation does not need a trend already
    in place, and the investment score is deliberately allowed to survive a
    flat-ADX failure.

    So an administrative zero is reported as UNMEASURED and blend()
    renormalises over the rest — the same rule every other missing input in
    this package follows.
    """
    score = f(row.get("Swing_Score"))
    if score is None:
        return None, "no swing score"
    gate_ok = b(row.get("Entry_Gate_Pass"))
    if score == 0 and gate_ok is False:
        return None, "swing score zeroed by the scan's entry gate — not a "\
                     "judgment on the setup"
    bits = [x for x in (s(row.get("Category")), s(row.get("Grade")) and
                        f"grade {row.get('Grade')}") if x]
    rr = f(row.get("RR_T2"))
    if rr:
        bits.append(f"R:R {rr:.1f}")
    return score, "; ".join(bits) or "swing setup"


def entry_view(trend: dict, confluence: dict, volume: dict,
               readiness: dict, row: dict | None = None) -> dict:
    swing, swing_note = swing_signal(row or {})
    blended = blend([
        ("Long-term trend", 30, _as_float(trend.get("score")),
         trend.get("state") or ""),
        ("Entry readiness", 25, _as_float(readiness.get("score")),
         readiness.get("label") or ""),
        ("Swing setup", 20, swing, swing_note),
        ("Support confluence", 15, _as_float(confluence.get("score")),
         confluence.get("label") or ""),
        ("Pullback volume", 10, _as_float(volume.get("score")),
         volume.get("label") or ""),
    ])
    return {"score": blended["score"], "coverage": blended["coverage"],
            "components": blended["components"],
            "swing": swing, "swing_note": swing_note}


def _as_float(v):
    return None if v is None else float(v)


def component_scores(lq, val, trend, confluence, volume, readiness,
                     cluster) -> list[dict]:
    """The five readings the card shows separately.

    Kept as a list of (label, score, note) rather than folded into one
    number, because the whole point is that they can disagree — Meta is
    92 quality against a 25 trend, and both are true.
    """
    return [
        {"key": "quality", "label": "Business quality", "icon": "💎",
         "score": lq.get("score"), "note": lq.get("tier") or "unrated"},
        {"key": "valuation", "label": "Valuation", "icon": "💰",
         "score": None if V.valuation_sub_score(val) is None
                  else round(V.valuation_sub_score(val)),
         "note": val.get("band_label") or val.get("band") or "not priced"},
        {"key": "trend", "label": "Long-term trend", "icon": "📈",
         "score": trend.get("score"),
         "note": f"{trend.get('icon', '')} {trend.get('state', '')}".strip()},
        {"key": "pullback", "label": "Pullback quality", "icon": "📉",
         "score": volume.get("score"), "note": volume.get("label") or "not measured"},
        {"key": "support", "label": "Support", "icon": "🧱",
         "score": confluence.get("score"),
         "note": (cluster.get("label") or "") if cluster else
                 (confluence.get("label") or "")},
    ]


def _market_sub_score(row, regime) -> tuple[float | None, str]:
    """The 15% market/sector leg: the regime, plus how the stock's sector is
    ranking against the rest of the library."""
    parts, notes = [], []
    reg = REGIME_SCORE.get(str(regime or "").upper())
    if reg is not None:
        parts.append(reg)
        notes.append(f"regime {regime}")
    sector_rank = f(row.get("Sector_Strength_Rank"))
    if sector_rank is not None:
        parts.append(max(0.0, min(100.0, sector_rank)))
        notes.append(f"sector strength {sector_rank:.0f}")
    if not parts:
        return None, "no market context"
    return sum(parts) / len(parts), " · ".join(notes)


# ─────────────────────────────────────────────────────────────────────────────
# THE GATES
# ─────────────────────────────────────────────────────────────────────────────

def _entry_prices(pullback, price) -> list[dict]:
    """Every tracked level below the current price, nearest first, as prices
    you could leave a resting order at."""
    below = [c for c in pullback.get("candidates", [])
             if c["distance_pct"] is not None and c["distance_pct"] > 0]
    below.sort(key=lambda c: c["distance_pct"])
    out = []
    for c in below:
        move = (c["price"] / price - 1) * 100.0 if price else None
        out.append({"name": c["name"], "zone": c["zone"], "price": c["price"],
                    "move_pct": None if move is None else round(move, 1),
                    "tranche_pct": ZONE_TRANCHE.get(c["zone"])})
    return out


# The swing engine's own bar for "this is a setup" — the same threshold the
# Screener card uses when it prints "Swing Score 75 (> 70)".
SWING_CONFIRMS = 70

# Risk/reward strong enough to stand in for a confirmation trigger, and the
# quality it takes to earn that.
#
# The reasoning is the one the framework already uses for position sizing: a
# 4:1 setup can be wrong three times out of four and still make money, so
# waiting for a reversal candle costs more than it protects. It substitutes
# for the TRIGGER only — the quality, valuation and trend gates are upstream
# and untouched, and a great chart on an expensive company still stops at
# valuation.
RR_CONFIRMS = 4.0
RR_MIN_QUALITY = 85
# ...and the stop has to be far enough away to be a stop. Risk/reward is a
# ratio, so a support level sitting 0.8% under the price manufactures a 10:1
# setup out of a level that ordinary noise removes before lunch. Requiring a
# minimum risk distance is what stops the rule rewarding tight stops rather
# than good ones.
RR_MIN_RISK_PCT = 2.0

# ── Valuation override ───────────────────────────────────────────────────────
# An exceptional risk/reward downgrades OVERVALUED from a block to a warning.
#
# The case for it: valuation says what you are paying for the business over
# years; risk/reward says what this entry costs if the thesis is wrong now.
# A 7:1 setup on an elite company with a confirmed uptrend risks a few
# percent to a level the market has defended, and refusing it on a
# trailing-cash-flow multiple treats a cyclical trough as a permanent state.
# Western Digital is the case: quality 95, 200 MA rising 13%, a tested shelf
# 2.7% below — and $1.28B of free cash flow after two negative years, which
# makes any multiple built on it close to meaningless.
#
# The case against, which is why the bars are high: this is the one place the
# hierarchy bends, and every guard below exists so it bends rather than
# breaks.
VALUATION_OVERRIDE_RR = 5.0        # clearly above the 4:1 that carries a
                                   # trigger — this buys more than an entry
VALUATION_OVERRIDE_QUALITY = 85    # elite businesses only
# Measured across the first three targets rather than the second alone. What
# the override asks is whether the trade's realistic range justifies the
# price, and for a deep pullback that range is the level it fell from. The
# 52-week high is deliberately out of reach of this window, so a collapsed
# name cannot borrow a huge ratio from a distant old high.
VALUATION_OVERRIDE_TARGETS = 3


def valuation_override(lq, val, trend, targets) -> dict | None:
    """Whether an exceptional setup earns a warning instead of a block.

    Returns the reason when it applies, None otherwise. Requires ALL of:
    an elite business, a CONFIRMED trend (not merely un-broken), a real stop,
    and a ratio well beyond the one that already carries a trigger.
    """
    if val.get("acceptable") is not False:
        return None
    quality = lq.get("score")
    if quality is None or quality < VALUATION_OVERRIDE_QUALITY:
        return None
    if trend.get("state") != "CONFIRMED":
        return None
    risk = abs(targets.get("risk_pct") or 0)
    if risk < RR_MIN_RISK_PCT:
        return None
    ladder = (targets.get("ladder") or [])[:VALUATION_OVERRIDE_TARGETS]
    if not ladder:
        return None
    best = max(ladder, key=lambda lv: lv["rr"])
    if best["rr"] < VALUATION_OVERRIDE_RR:
        return None
    return {"rr": best["rr"], "target": best,
            "stop": targets.get("stop"), "risk_pct": targets.get("risk_pct")}


def _confirmation(row, pullback, volume) -> tuple[bool | None, list[str]]:
    """§5: touch → stabilization → confirmation → entry. A touch is not an
    entry, and this is the check that keeps the engine from buying one.

    Returns (confirmed, missing_reasons). None means the confirming inputs
    were never measured — which is not a confirmation, and is reported as
    such rather than assumed either way.

    A strong swing score is accepted as an ALTERNATIVE confirmation. The
    scan's Swing_Score above SWING_CONFIRMS already encodes a recognised
    setup, a risk/reward to target and volatility contraction — independent
    pattern work reaching the same conclusion a reversal candle would, and
    on Brown & Brown it does so while the candle column is blank. It cannot
    override a measured failure: overbought RSI or distributing volume still
    block, because those are reasons the setup is wrong rather than gaps in
    what was measured.
    """
    missing = []
    reversal = row.get("Reversal_Candle")
    rsi = f(row.get("RSI_14"))
    vol_score = volume.get("score")

    have_any = False
    confirmed = True

    if reversal is None:
        missing.append("No candle data — reversal cannot be confirmed")
        confirmed = None
    else:
        have_any = True
        pattern = str(reversal).strip()
        if not pattern or pattern.lower() in ("none", "false"):
            missing.append("No bullish reversal candle yet")
            confirmed = False

    if vol_score is None:
        missing.append("Pullback volume not measured")
        if confirmed is not False:
            confirmed = None
    else:
        have_any = True
        if vol_score < MIN_PULLBACK_VOLUME:
            missing.append(f"Pullback volume {vol_score}/100 — "
                           f"{volume.get('label')}")
            confirmed = False

    if rsi is not None and rsi > 70:
        have_any = True
        missing.append(f"RSI {rsi:.0f} — overbought into the level")
        confirmed = False

    # Risk/reward as an alternative trigger. Same rule as the swing setup:
    # it rescues the UNMEASURED case, never a measured failure.
    targets = (pullback or {}).get("targets") or {}
    rr = targets.get("rr_t2") or targets.get("rr_t1")
    quality = f((row.get("_lquality")))
    risk_pct = abs(targets.get("risk_pct") or 0)
    if (confirmed is not False and rr is not None and rr >= RR_CONFIRMS
            and risk_pct >= RR_MIN_RISK_PCT
            and quality is not None and quality >= RR_MIN_QUALITY):
        t2 = targets.get("t2") or targets.get("t1") or {}
        return True, [f"Risk/reward {rr:.1f}:1 carries the entry — stop "
                      f"${targets['stop']:,.2f} ({targets['risk_pct']:+.1f}%), "
                      f"target ${t2.get('price', 0):,.2f} "
                      f"({t2.get('name', 'resistance')})"]

    swing, swing_note = swing_signal(row)
    if confirmed is not False and swing is not None and swing >= SWING_CONFIRMS:
        # Only rescues the UNMEASURED case. `confirmed is False` means
        # something was checked and failed, and a pattern score does not
        # overrule that.
        return True, [f"Confirmed by the swing setup instead — "
                      f"Swing_Score {swing:.0f} ({swing_note})"]

    if not have_any:
        return None, missing
    return confirmed, missing


def evaluate(row: dict, peers: dict | None = None,
             regime: str = "FAVORABLE",
             risk_free: float = V.DEFAULT_RISK_FREE,
             sector_stats: dict | None = None) -> dict:
    """
    One verdict for one company. `row` is a scan / research `raw` row.

    `peers` and `sector_stats` are universe-level context — the sector
    multiples the valuation fallback prices against, and the sector
    percentiles the moat check compares to. Both are optional and both
    degrade explicitly rather than silently; `evaluate_universe()` builds
    them and is the normal entry point.

    Returns a dict carrying every intermediate score plus the action, the
    reasoning that produced it, and — for every BUY ON action — the price
    to wait for and the tranche to take there.
    """
    ticker = s(row.get("Ticker")) or s(row.get("ticker"))
    price = f(row.get("Current Price")) or f(row.get("price"))

    lq = Q.compute_lquality(row, sector_stats)
    val = V.compute_valuation(row, peers, risk_free)
    trend = T.compute_trend(row)
    pullback = T.compute_pullback(row)
    confluence = T.compute_support_confluence(row)
    volume = T.compute_pullback_volume(row)
    rs = T.relative_strength(row)
    readiness = T.compute_entry_readiness(row, pullback, rs, regime)
    insider = Q.insider_signal(row)
    cluster = pullback.get("ma_cluster") or {}
    targets = pullback.get("targets") or {}
    # Pure price-and-volume, sharing no input with the quality or valuation
    # gates — which is what makes agreement between them informative.
    technical = T.compute_technical_score(row, trend, pullback, confluence,
                                          volume, targets)
    investment = investment_view(lq, val)
    entry = entry_view(trend, confluence, volume, readiness, row)
    components = component_scores(lq, val, trend, confluence, volume,
                                  readiness, cluster)

    tech_sub = T.technical_sub_score(trend, pullback, confluence, volume, rs)
    market_sub, market_note = _market_sub_score(row, regime)
    lt = compute_lt_score(lq, val, tech_sub, market_sub)

    regime_up = str(regime or "FAVORABLE").upper()
    bump = SELECTIVE_QUALITY_BUMP if regime_up == "SELECTIVE" else 0

    blockers, triggers = [], []
    entries = _entry_prices(pullback, price)
    why_extra: list[str] = []

    def verdict(action, gate):
        return _assemble(ticker, action, gate, row, lq, val, trend, pullback,
                         confluence, volume, rs, lt, market_note, entries,
                         blockers, triggers, price, regime_up, readiness,
                         insider, investment, entry, components, cluster,
                         # risk_free rides along because the buy zones re-read
                         # the reverse DCF at each band's price, and it has to
                         # be the same discount rate the verdict used.
                         why_extra, technical, targets, risk_free)

    # ── GATE 1 — business quality. Nothing below matters if this fails. ──
    if lq["score"] is None or not lq["reliable"]:
        blockers.append(
            f"Quality could not be assessed — {int(lq['coverage'] * 100)}% of "
            f"the factor weight had data" +
            (f" (missing {', '.join(lq['missing'][:3])})" if lq["missing"] else ""))
        triggers.append("Run a scan so the fundamental fields populate")
        return verdict("AVOID", "quality")

    if lq["score"] < Q.MIN_OWNABLE:
        blockers.append(f"LQuality {lq['score']} ({lq['tier']}) — below the "
                        f"{Q.MIN_OWNABLE} ownable bar. No chart pattern "
                        f"compensates for this.")
        # Deterioration in the business AND a measurably falling 200 MA is a
        # different statement from either alone: the market and the numbers
        # agree. Named separately so it is not filed with names that are
        # merely mediocre.
        if trend["state"] == "BROKEN":
            blockers.append("The 200 MA is falling too — the business and "
                            "the tape agree")
            return verdict("THESIS BROKEN", "quality")
        return verdict("AVOID", "quality")

    # ── GATE 2 — valuation. A great company at any price is not a buy. ──
    override = valuation_override(lq, val, trend, targets)
    if val["acceptable"] is False and override:
        # Downgraded to a warning, not waived. The blocker still shows, and
        # the card still says the price is demanding — what changes is that
        # the ladder is allowed to continue past it.
        blockers.append(f"⚠ Price is demanding — {val['headline']}")
        why_extra.append(
            f"Overvaluation overridden by a {override['rr']:.1f}:1 setup — "
            f"stop ${override['stop']:,.2f} ({override['risk_pct']:+.1f}%), "
            f"target ${override['target']['price']:,.2f} "
            f"({override['target']['name']}). The price is still demanding; "
            f"the trade is small enough to be wrong")
    elif val["acceptable"] is False:
        # The headline is the only field both methods produce — the reverse
        # DCF deliberately has no fair value, and printing one it never
        # computed would be a number nobody could defend.
        blockers.append(f"{val['band_icon']} Overvalued — {val['headline']}")
        if val["fair_value"] is not None:
            triggers.append(f"Price falls toward ${val['fair_value']:,.2f}, "
                            f"or earnings grow into the multiple")
        elif val["implied_growth_pct"] is not None:
            triggers.append(
                f"The gap closes — either the price falls, or growth "
                f"accelerates toward the {val['implied_growth_pct']:.0f}% "
                f"the price already assumes")
        else:
            triggers.append("Valuation improves")
        return verdict(_wait_label(investment, "price"), "valuation")

    if val["acceptable"] is None:
        blockers.append("Could not be valued — " +
                        (val["notes"][0] if val["notes"] else "no method applied"))

    # ── GATE 3 — long-term trend. Buying weakness inside an uptrend only. ──
    if trend["pass"] is False:
        blockers.append(f"{trend['icon']} Trend {trend['state'].lower()} — "
                        f"{trend['summary']}: "
                        + ", ".join(trend["required_failed"]))
        triggers.append("Trend repairs — price reclaims the 200 MA and the "
                        "50 MA crosses back above it")
        if trend["state"] == "IMPAIRED":
            triggers.append("Re-scan to measure the 200 MA slope — that is "
                            "what separates a deep correction from a "
                            "breakdown")
        # Not AVOID: the business still passed gate 1, so this is a
        # watchlist name whose chart has to heal, not a company to discard.
        return verdict(_wait_label(investment, "trend"), "trend")

    if trend["state"] == "RECOVERING":
        # Not a shortfall in measurement — a shortfall in confirmation. Price
        # is above the long-term average and the 50 MA is climbing back
        # toward it; what is missing is the cross, not the data.
        blockers.append(f"{trend['icon']} Trend recovering — "
                        f"{trend['summary']}")
        triggers.append("C — Trend confirmation: the 50 MA crosses back above "
                        "the 200 MA")
    elif trend["state"] == "PARTIAL":
        # Explicitly NOT a failure. Structure holds; something is unmeasured.
        # It costs the name a BUY NOW — that requires CONFIRMED — but it must
        # not eject it from the ladder, which is what "failed on trend" did
        # to Western Digital while it sat above a rising 200 MA structure
        # with only its slopes unscanned.
        blockers.append(
            f"Trend partially confirmed — {trend['confirmed_points']} points "
            f"confirmed, {trend['unknown_points']} unmeasured "
            f"({', '.join(trend['unknown'][:3])}). Structure is intact; "
            f"nothing has failed")

    # ── GATE 4 — stage, support and trigger. Where, and has it confirmed? ──
    stage = pullback["stage"]

    if regime_up == "DEFENSIVE":
        blockers.append("Market regime DEFENSIVE — §11 says stop adding, "
                        "build the watchlist and hold cash")
        triggers.append("Regime improves to SELECTIVE or FAVORABLE")
        return verdict("WATCH", "regime")

    days = _earnings_days(row)
    if days is not None and 0 <= days <= EARNINGS_BLOCK_DAYS:
        blockers.append(f"Earnings in {days:.0f} day"
                        f"{'s' if days != 1 else ''} — gap risk no setup "
                        f"quality offsets")
        triggers.append("Re-assess once the report is out")
        return verdict("WAIT", "earnings")

    triggers.extend(_buy_triggers(row, pullback, readiness, trend))

    # Below the ownable-but-not-elite bar, the framework says research it,
    # not buy it — §15's "<80 / cheap / confirmed" row.
    if lq["score"] < min(ZONE_MIN_QUALITY.values()) + bump:
        blockers.append(
            f"LQuality {lq['score']} ({lq['tier']}) — this engine requires "
            f"{min(ZONE_MIN_QUALITY.values()) + bump} to enter on a pullback"
            + (" in a SELECTIVE regime" if bump else ""))
        return verdict("RESEARCH", "entry")

    # STAGE 4 — below the 200 MA. Not a pullback; a thesis review.
    if stage == "STAGE4_BREAKDOWN":
        blockers.append(pullback["note"])
        return verdict(_wait_label(investment, "trend"), "trend")

    # STAGE 3 — deep correction, long-term structure not broken. The state
    # that had no name before: too far below the 50 MA to be a normal
    # pullback, still above the 200 MA, and therefore neither a buy nor a
    # discard. What decides it is whether support has actually held.
    if stage == "STAGE3_DEEP":
        if readiness["score"] >= READY_CONFIRM and trend["state"] != "BROKEN":
            return verdict("BUY ON CONFIRMATION", "trigger")
        near = (pullback.get("supports") or {}).get("near")
        if near:
            blockers.append(
                f"Nearest support is {near['name']} at ${near['price']:,.2f}, "
                f"{abs(near['distance_pct']):.1f}% below — not tested yet")
        blockers.append(f"Entry readiness {readiness['score']}/100 "
                        f"({readiness['label']})")
        return verdict("DEEP PULLBACK — WAIT FOR SUPPORT", "readiness")

    # EXTENDED or AT_HIGHS — nothing to buy here; name the price to wait for.
    if stage in ("EXTENDED", "AT_HIGHS"):
        blockers.append(pullback["note"] if pullback["extended"]
                        else "Not at a support level — no entry to take yet")
        if entries:
            first = entries[0]
            if lq["score"] >= ZONE_MIN_QUALITY.get(first["zone"], 85) + bump:
                return verdict(ZONE_ACTION.get(first["zone"], "WATCH"), "entry")
        return verdict(_wait_label(investment, "entry"), "entry")

    # STAGE 1 / STAGE 2 — price is on a tracked level.
    zone = pullback["zone"]
    min_q = ZONE_MIN_QUALITY.get(zone, 85) + bump
    if lq["score"] < min_q:
        blockers.append(f"LQuality {lq['score']} — entering at the "
                        f"{pullback['level']} requires {min_q}"
                        + (" in a SELECTIVE regime" if bump else ""))
        return verdict("WATCH", "entry")

    hits = len(confluence["hits"])
    if hits < MIN_CONFLUENCE_HITS:
        only = confluence["hits"][0]["name"] if hits else "nothing"
        blockers.append(f"Only {hits} support confirmation"
                        f"{'' if hits == 1 else 's'} here ({only}) — "
                        f"confluence {confluence['score']}/100. One level "
                        f"alone is a coordinate, not support")
        return verdict("WATCH", "support")

    confirmed, missing = _confirmation({**row, "_lquality": lq["score"]},
                                      pullback, volume)
    if confirmed is True and missing:
        why_extra.extend(missing)      # how it confirmed, not what is missing
    if confirmed is not True:
        blockers.extend(missing)
        if readiness["score"] >= READY_CONFIRM:
            return verdict("BUY ON CONFIRMATION", "trigger")
        return verdict("WATCH", "trigger")

    # Everything holds. A BUY NOW additionally needs the trend CONFIRMED —
    # acting today on structure that was never measured is the one thing the
    # PARTIAL state exists to prevent.
    if trend["state"] != "CONFIRMED":
        return verdict("BUY ON CONFIRMATION", "trigger")

    triggers.insert(0, f"Take {tranche_for(entry.get('score'), zone)}% of the "
                       f"target position here; add on the next level down or "
                       f"on trend resumption")
    return verdict("BUY NOW", "confirmed")


# ─────────────────────────────────────────────────────────────────────────────
# BUY TRIGGERS — what would actually make this a buy, in prices
# ─────────────────────────────────────────────────────────────────────────────
# "Re-scan to populate the moving-average slopes" is a note about the
# software, not about the stock. A trigger has to be something the tape can
# do: a level tested and held, an average reclaimed, volume drying up. Each
# one below names a price wherever the data supports naming one.

def _buy_triggers(row, pullback, readiness, trend) -> list[str]:
    out = []
    supports = pullback.get("supports") or {}
    by_level = pullback.get("by_level") or {}
    near, major = supports.get("near"), supports.get("major")
    price = f(row.get("Current Price")) or f(row.get("price"))

    if near and near["distance_pct"] < 0:
        out.append(
            f"A — Support buy: price tests {near['name']} at "
            f"${near['price']:,.2f} ({near['distance_pct']:+.1f}%), holds it, "
            f"volume contracts, and a bullish reversal prints")

    ema8 = (by_level.get("8EMA") or {}).get("price")
    ema21 = (by_level.get("21EMA") or {}).get("price")
    if ema8 and price and price < ema8:
        reclaim = f"${ema8:,.2f}"
        if ema21:
            reclaim += f" then the 21 EMA at ${ema21:,.2f}"
        out.append(f"B — Momentum repair: price reclaims the 8 EMA at "
                   f"{reclaim} on improving volume")

    ma50 = (by_level.get("50MA") or {}).get("price")
    if ma50 and price and price < ma50:
        out.append(f"C — Trend repair: price reclaims the 50 MA at "
                   f"${ma50:,.2f} and the 50 MA slope turns positive")

    if major and major["distance_pct"] < -1:
        out.append(f"D — Deep-value entry: only near the 200 MA at "
                   f"${major['price']:,.2f} ({major['distance_pct']:+.1f}%), "
                   f"and only if the fundamentals still hold")

    if major:
        out.append(f"Invalidation: a break below ${major['price']:,.2f} on "
                   f"heavy volume with deteriorating fundamentals — that is "
                   f"a thesis review, not a cheaper entry")

    for u in readiness.get("unknown", []):
        if u["name"] == "Bullish reversal":
            out.append("Re-scan to measure the reversal candle — it cannot be "
                       "confirmed from the current data")
    if trend["state"] == "PARTIAL" and trend["unknown"]:
        out.append("Re-scan to populate " + ", ".join(trend["unknown"][:2])
                   + " — the only structural checks still unmeasured")
    return out


def _assemble(ticker, action, gate, row, lq, val, trend, pullback, confluence,
              volume, rs, lt, market_note, entries, blockers, triggers, price,
              regime, readiness=None, insider=None, investment=None,
              entry=None, components=None, cluster=None,
              why_extra=None, technical=None, targets=None,
              risk_free=V.DEFAULT_RISK_FREE) -> dict:
    """The verdict with its whole audit attached — never a score without the
    reasoning that produced it."""
    zone = pullback["zone"]
    entry_score = (entry or {}).get("score")
    tranche = (tranche_for(entry_score, zone)
               if action.startswith("BUY") else None)

    why = list(why_extra or [])
    if lq["tier"]:
        why.append(f"{lq['tier_icon']} {lq['tier']} business — LQuality "
                   f"{lq['score']}/100")
    # Only a price that is actually acceptable belongs in the reasons TO buy.
    # Listing "🔴 Overvalued" among them read as an argument for the trade,
    # and sat directly beside the override note contradicting it.
    if val["band"] and val.get("acceptable"):
        why.append(f"{val['band_icon']} {val.get('band_label') or val['band']} "
                   f"— {val['headline']}")
    if trend["state"] == "CONFIRMED":
        why.append("Long-term uptrend confirmed")
    elif trend["state"] == "PARTIAL":
        why.append(f"Long-term structure intact — {trend['confirmed_points']} "
                   f"points confirmed, nothing failed")
    n_hits = len(confluence["hits"])
    if n_hits >= MIN_CONFLUENCE_HITS:
        # Stated as the count, without the score's band label. The gate is a
        # count of confirmations and the label is a level on the weighted
        # score, so pairing them put "🔴 Weak — confluence 35/100" in the
        # list of reasons TO buy. Both readings are correct on their own
        # terms; asserting them together is the contradiction.
        names = ", ".join(h["name"] for h in confluence["hits"][:3])
        why.append(f"{n_hits} support levels hold here ({names})")
    if rs["strong"]:
        why.append(f"Relative strength — {rs['detail']}")
    if volume.get("score") is not None and volume["score"] >= 60:
        why.append(f"Pullback volume {volume['score']}/100 — {volume['label']}")

    return {
        "ticker": ticker,
        "name": s(row.get("LongName")) or ticker,
        "sector": s(row.get("Sector")),
        "price": price,
        # The drawdown, carried onto the result so the screener can ask
        # "quality, and genuinely in a correction" without re-reading the
        # scan row it no longer has a handle on.
        "dist_52w_high": f(row.get("Dist_52W_High%")),
        "action": action,
        "icon": ACTION_ICONS.get(action, ""),
        # Which gate produced this verdict — the whole point of the ordering
        # is that you can see where a name stopped.
        "gate": gate,
        "lt_score": lt["score"],
        "lt_coverage": lt["coverage"],
        "lt_components": lt["components"],
        "quality": lq,
        "valuation": val,
        "trend": trend,
        "pullback": pullback,
        "confluence": confluence,
        "volume": volume,
        "rs": rs,
        "readiness": readiness or {},
        # Reported beside the verdict, never inside the quality score.
        "insider": insider or {},
        # The two verdicts, and the five readings behind them. `action` is
        # the ENTRY answer; `investment` is the company answer, and a name
        # is routinely OWN on one and WAIT on the other.
        "investment": investment or {},
        "entry": entry or {},
        "components": components or [],
        "ma_cluster": cluster or {},
        "technical": technical or {},
        "targets": targets or {},
        "regime": regime,
        "market_note": market_note,
        "tranche_pct": tranche,
        "earnings_risk": earnings_risk(row.get("Days_To_Earnings")),
        "entries": entries,
        # Valuation and support computed separately and intersected — a buy
        # zone exists only where both agree, which is usually nowhere. See
        # buy_zones.py for why this is not built from the support levels.
        "buy_zones": BZ.compute(row, pullback, val, lq, trend, volume, price,
                                risk_free=risk_free),
        # Whether the company is still the one you decided to own, which is a
        # different question from every gate above and is deliberately NOT one
        # of them. `action` answers "should I open a position at this price";
        # this answers "may I keep adding to one". A name is routinely WAIT on
        # the first and accumulable on the second — that is the gap the
        # ladder lives in, not an inconsistency. See thesis.py.
        "thesis": TH.compute_thesis(row),
        "why": why,
        "blockers": blockers,
        "triggers": triggers,
        "days_to_earnings": _earnings_days(row),
    }


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE — the two things that cannot be computed from one row
# ─────────────────────────────────────────────────────────────────────────────

def attach_sector_context(rows) -> None:
    """In place: Sector_RS_Rank and Sector_Strength_Rank.

    Sector_RS_Rank is the ticker's percentile of raw RS WITHIN its own
    sector — §9's point, that a name lagging the market while leading its
    group is a different animal from one lagging both.

    Sector_Strength_Rank is the percentile of the sector's own median RS
    across all sectors, which is the "sector trend" term §14 asks for.
    Sectors with fewer than MIN_SECTOR_MEMBERS names in the library are left
    None rather than ranked off two tickers.
    """
    MIN_SECTOR_MEMBERS = 4
    by_sector: dict[str, list] = {}
    for row in rows:
        sector, rs = s(row.get("Sector")), f(row.get("RS"))
        if sector and rs is not None:
            by_sector.setdefault(sector, []).append(rs)

    medians = {}
    for sector, vals in by_sector.items():
        if len(vals) >= MIN_SECTOR_MEMBERS:
            ordered = sorted(vals)
            n = len(ordered)
            medians[sector] = (ordered[n // 2] if n % 2
                               else (ordered[n // 2 - 1] + ordered[n // 2]) / 2)

    ranked_sectors = sorted(medians, key=lambda k: medians[k])
    n_sec = len(ranked_sectors)
    sector_strength = {sec: round(i / max(1, n_sec - 1) * 100)
                       for i, sec in enumerate(ranked_sectors)} if n_sec > 1 else {}

    ordered_by_sector = {sec: sorted(vals) for sec, vals in by_sector.items()
                         if len(vals) >= MIN_SECTOR_MEMBERS}

    for row in rows:
        sector, rs = s(row.get("Sector")), f(row.get("RS"))
        row["Sector_Strength_Rank"] = sector_strength.get(sector or "")
        vals = ordered_by_sector.get(sector or "")
        if vals is None or rs is None:
            row["Sector_RS_Rank"] = None
            continue
        below = sum(1 for v in vals if v < rs)
        row["Sector_RS_Rank"] = round(below / max(1, len(vals) - 1) * 100) \
            if len(vals) > 1 else None


def evaluate_universe(rows, regime: str = "FAVORABLE",
                      risk_free: float = V.DEFAULT_RISK_FREE) -> list[dict]:
    """
    Evaluate the whole library. Does the three things `evaluate()` cannot do
    from a single row — build the sector peer multiples the valuation
    fallback needs, build the sector percentiles the moat check compares to,
    and rank each name against its own sector — then runs every row through
    the gates.

    Sorted by action priority, then LT score: the ranking a human reads
    top-down.
    """
    rows = list(rows)
    attach_sector_context(rows)
    peers = V.build_peer_stats(rows)
    sector_stats = Q.build_sector_stats(rows)
    out = [evaluate(row, peers, regime, risk_free, sector_stats)
           for row in rows]
    order = {a: i for i, a in enumerate(ACTIONS)}
    out.sort(key=lambda r: (order.get(r["action"], 99), -(r["lt_score"] or 0)))
    return out
