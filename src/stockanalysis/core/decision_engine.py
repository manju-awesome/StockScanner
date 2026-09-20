"""
decision_engine.py
===================
A decision layer over the screener's results. The screener answers "which
stocks meet my criteria"; this answers "which of those deserve attention,
what kind of opportunity is it, and what would have to change".

This module is step one of that: the three scores everything else is built
on. It computes nothing about actions or tiers yet — those read these
scores, so they have to be right first.

    investment_score()   long-term company quality, 0-100
    swing_score()        short/intermediate setup quality, 0-100
    confluence()         0-10, how many INDEPENDENT factors agree

Nothing here replaces core/screener.py. Screening decides membership; this
ranks and characterises what came back.

The rule that shapes the whole file
-----------------------------------
A missing input must never quietly become a score. The obvious
implementations both lie: treating an absent field as 0 punishes a company
for a data gap, and skipping it while keeping the divisor does the same
thing more subtly. Assigning a neutral 50 invents a measurement.

So each score renormalises over the components it actually has, and returns
`coverage` (share of the intended weight that was measurable) plus the names
of what was missing. A score built from 40% of its inputs is not the same
claim as one built from 100%, and callers are expected to refuse
high-conviction labels below `MIN_COVERAGE`. This matters concretely here:
R:R is present for 207 of 545 library rows and breakout probability for 313,
so partial coverage is the common case, not an edge case.

Confluence counts independent confirmations, which is why it is not just a
sum of the other two. Quality and Moat both describe the business; RS rank
and being above the 200-day both describe trend. Counting each pair twice
would manufacture agreement out of one signal — the exact error the score
exists to detect.
"""

from __future__ import annotations

# A score computed from less than this share of its intended weight is
# reported but must not carry a high-conviction action.
MIN_COVERAGE = 0.60

INVESTMENT_BANDS = ((90, "Exceptional"), (80, "Strong"), (70, "Good"),
                    (60, "Average"), (0, "Weak"))
SWING_BANDS = ((90, "A+ Setup"), (80, "A Setup"), (70, "B Setup"),
               (60, "C Setup"), (0, "Avoid"))


def _f(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _band(score: float | None, bands) -> str | None:
    if score is None:
        return None
    for floor, label in bands:
        if score >= floor:
            return label
    return bands[-1][1]


def _scale(value: float, lo: float, hi: float) -> float:
    """Map a raw value onto 0-100, clamped. `lo`/`hi` are the range over
    which the metric is actually discriminating — beyond them, more is not
    meaningfully better."""
    if hi == lo:
        return 0.0
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0))


def _inverse(value: float, best: float, worst: float) -> float:
    """Lower-is-better metrics (valuation). Values at or below `best` score
    100; at or above `worst`, 0."""
    if worst == best:
        return 0.0
    return max(0.0, min(100.0, (worst - value) / (worst - best) * 100.0))


def _blend(parts: list[tuple[str, float, float | None]]) -> dict:
    """parts: [(name, weight, value_0_100_or_None)].

    Renormalises over the measurable components and reports how much of the
    intended weight that was. Returns score None when nothing is measurable
    — not 0, which would read as "bad" rather than "unknown".
    """
    total_weight = sum(w for _, w, _ in parts) or 1.0
    got_weight = 0.0
    acc = 0.0
    missing = []
    used = {}
    for name, weight, value in parts:
        if value is None:
            missing.append(name)
            continue
        acc += weight * value
        got_weight += weight
        used[name] = round(value, 1)
    if not got_weight:
        return {"score": None, "coverage": 0.0, "missing": missing,
                "components": {}}
    return {"score": int(round(acc / got_weight)),
            "coverage": round(got_weight / total_weight, 3),
            "missing": missing, "components": used}


# ─────────────────────────────────────────────────────────────────────────────
# INVESTMENT SCORE — is this a good company to own
# ─────────────────────────────────────────────────────────────────────────────
# Ranges are set where each metric stops discriminating. EPS growth tops out
# at 60% because the library's distribution runs to five figures on one-off
# comparisons — rewarding 21,000% over 60% would rank an accounting artifact
# above a real compounder. Forward P/E is scored between 10 and 45 and only
# for positive multiples: a negative P/E means no earnings, which is not
# cheap, so it scores 0 rather than off the top of the scale.

def investment_score(row: dict) -> dict:
    quality = _f(row.get("quality"))
    health = _f(row.get("health"))
    moat = _f(row.get("moat"))
    eps = _f(row.get("eps_growth"))
    rev = _f(row.get("revenue_growth"))
    pe = _f(row.get("forward_pe"))
    rs = _f(row.get("rs_rank"))
    above200 = row.get("above_200ma")

    parts = [
        ("Quality", 25, quality),                       # already 0-100
        ("Health", 15, health),
        ("Moat", 15, None if moat is None else _scale(moat, 0, 4)),
        ("EPS growth", 15, None if eps is None else _scale(eps, 0, 60)),
        ("Revenue growth", 10, None if rev is None else _scale(rev, 0, 30)),
        ("Valuation", 10, None if pe is None or pe <= 0
                          else _inverse(pe, 10, 45)),
        ("RS", 5, rs),
        ("Technical", 5, None if above200 is None else (100.0 if above200 else 0.0)),
    ]
    out = _blend(parts)
    out["label"] = _band(out["score"], INVESTMENT_BANDS)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# SWING SCORE — is this a good trade right now
# ─────────────────────────────────────────────────────────────────────────────
# Deliberately shares no fundamental input with investment_score. A great
# business with no setup must not score well here; that conflation is what
# makes a single blended score useless for timing.

def swing_score(row: dict) -> dict:
    existing = _f(row.get("swing_score"))
    rs = _f(row.get("rs_rank"))
    breakout = _f(row.get("breakout_probability"))
    rr = _f(row.get("rr"))
    rvol = _f(row.get("rvol"))
    above200 = row.get("above_200ma")
    above50 = row.get("above_50ma")
    dist8 = _f(row.get("abs_vs_8ema"))

    # Trend: both moving averages, since one alone is a weaker statement.
    trend = None
    if above200 is not None or above50 is not None:
        legs = [x for x in (above200, above50) if x is not None]
        trend = sum(100.0 if x else 0.0 for x in legs) / len(legs)

    # Setup quality: the scan's own swing score when present — it already
    # encodes pattern work this module has no access to. Falls back to
    # proximity to the 8 EMA, which is the entry most of these setups use.
    setup = existing if existing is not None else (
        None if dist8 is None else _inverse(abs(dist8), 0.5, 8.0))

    parts = [
        ("Technical trend", 25, trend),
        ("RS", 20, rs),
        ("Setup quality", 20, setup),
        ("Breakout probability", 15, breakout),
        ("Momentum", 10, None if rvol is None else _scale(rvol, 0.8, 2.5)),
        ("Risk/reward", 10, None if rr is None else _scale(rr, 1.0, 4.0)),
    ]
    out = _blend(parts)
    out["label"] = _band(out["score"], SWING_BANDS)
    out["used_scan_swing"] = existing is not None
    return out


# ─────────────────────────────────────────────────────────────────────────────
# CONFLUENCE — how many INDEPENDENT things agree
# ─────────────────────────────────────────────────────────────────────────────
# Each check below is a different kind of evidence. Correlated signals are
# deliberately collapsed into one check rather than counted separately:
# quality and moat are one business-strength check, RS and above-200MA are
# one trend check. Counting them twice would turn a single signal into
# apparent agreement, which is precisely what this score exists to catch.
_CONFLUENCE_CHECKS = (
    "Fundamentals", "Business strength", "Relative strength", "Long-term trend",
    "Intermediate trend", "Near entry", "Setup present", "Volume confirmation",
    "Risk/reward", "Market regime",
)


def confluence(row: dict, regime_favorable: bool | None = None) -> dict:
    """0-10. `regime_favorable` comes from the caller (market regime or
    benchmark leadership); None means unknown and simply isn't counted."""
    hits, misses, unknown = [], [], []

    def check(name, value):
        if value is None:
            unknown.append(name)
        elif value:
            hits.append(name)
        else:
            misses.append(name)

    quality = _f(row.get("quality"))
    health = _f(row.get("health"))
    moat = _f(row.get("moat"))
    eps = _f(row.get("eps_growth"))
    rs = _f(row.get("rs_rank"))
    rr = _f(row.get("rr"))
    rvol = _f(row.get("rvol"))
    breakout = _f(row.get("breakout_probability"))
    dist8 = _f(row.get("abs_vs_8ema"))

    check("Fundamentals", None if (eps is None and health is None) else
          ((eps or 0) >= 15 and (health or 0) >= 60))
    # Quality and Moat describe the same thing — one check, not two.
    check("Business strength", None if (quality is None and moat is None) else
          ((quality or 0) >= 70 or (moat or 0) >= 3))
    check("Relative strength", None if rs is None else rs >= 70)
    check("Long-term trend", row.get("above_200ma"))
    check("Intermediate trend", row.get("above_50ma"))
    check("Near entry", None if (dist8 is None and row.get("in_buy_zone") is None)
          else (row.get("in_buy_zone") is True or (dist8 is not None and dist8 <= 3)))
    check("Setup present", None if (row.get("category") is None and breakout is None)
          else (str(row.get("category") or "") in
                ("Momentum", "Momentum-Pullback", "VCP Setup", "Turnaround")
                or (breakout or 0) >= 70))
    check("Volume confirmation", None if rvol is None else rvol >= 1.2)
    check("Risk/reward", None if rr is None else rr >= 2.0)
    check("Market regime", regime_favorable)

    return {"score": len(hits), "max": len(_CONFLUENCE_CHECKS),
            "hits": hits, "misses": misses, "unknown": unknown,
            # A 7/10 with three unknowns is a weaker claim than a clean 7/10.
            "measured": len(hits) + len(misses)}


def score_row(row: dict, regime_favorable: bool | None = None) -> dict:
    """All three scores for one screener result row."""
    inv = investment_score(row)
    swing = swing_score(row)
    conf = confluence(row, regime_favorable)
    return {
        "ticker": row.get("ticker"),
        "investment": inv,
        "swing": swing,
        "confluence": conf,
        # Surfaced so callers don't have to re-derive the rule that decides
        # whether a high-conviction action is even permissible.
        "reliable": (inv["coverage"] >= MIN_COVERAGE
                     and swing["coverage"] >= MIN_COVERAGE),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ACTIONS — exactly one primary action per stock
# ─────────────────────────────────────────────────────────────────────────────
# Thresholds are set from the library's real distribution, not from round
# numbers. Swing tops out at 83 and confluence at 9/10, so the natural-looking
# ">= 85 and >= 8/10" gate for BUY NOW is empty by construction — not because
# nothing qualifies but because the scale doesn't reach there. The cutoffs
# below sit at roughly the 97th/90th percentile of what the scores actually
# produce, which is what "exceptional" has to mean if the label is to name
# anything. Moving the goalposts to a rounder number would give a screen that
# reads as rigorous and returns nothing.
BUY_NOW_SCORE = 78
BUY_NOW_CONFLUENCE = 7
PULLBACK_SCORE = 72
PULLBACK_CONFLUENCE = 6
WATCH_SCORE = 65
MIN_RR = 2.0

ACTIONS = ("BUY NOW", "BUY ZONE", "BUY ON PULLBACK", "BREAKOUT ENTRY",
           "WATCH", "WAIT", "SPECULATIVE", "AVOID")

ACTION_ICONS = {"BUY NOW": "🟢", "BUY ZONE": "🟢", "BUY ON PULLBACK": "🟢",
                "BREAKOUT ENTRY": "🚀", "WATCH": "🔵", "WAIT": "⏳",
                "SPECULATIVE": "🟡", "AVOID": "🔴"}

# Earnings proximity. An imminent report is a gap risk no setup quality
# offsets, so it downgrades rather than scoring.
EARNINGS_BANDS = ((2, "CRITICAL"), (7, "HIGH"), (14, "MEDIUM"))


def earnings_risk(days) -> str:
    d = _f(days)
    if d is None:
        return "UNKNOWN"
    if d < 0:
        return "LOW"
    for limit, label in EARNINGS_BANDS:
        if d <= limit:
            return label
    return "LOW"


STRATEGIES = ("LONGTERM", "SWING", "BALANCED")


def _primary_score(inv, swing, strategy: str) -> int:
    """Which score gates the action.

    Using max() here — the obvious shortcut — silently undoes the whole
    point of scoring investment and swing separately: it lets a strong chart
    on a weak company, or a strong company with no setup, both reach BUY
    NOW. Neither is a buy for the same reason, and calling them the same
    thing hides which one you are actually taking.

    LONGTERM gates on the company, SWING on the setup, and BALANCED demands
    both — the lower of the two, which is the honest reading when no
    strategy has been declared.
    """
    inv, swing = inv or 0, swing or 0
    if strategy == "LONGTERM":
        return inv
    if strategy == "SWING":
        return swing
    return min(inv, swing)


def _context(row, s, regime, strategy, earnings_strategy) -> dict:
    """Everything the rules read, resolved once.

    Pulling this out is the point of the rewrite: when each branch fetched
    its own inputs inline, three separate branches ended up reading the
    investment score while the caller had asked for a swing decision. Here
    `best` is resolved once from the strategy and every rule uses it.
    """
    inv, swing = s["investment"]["score"], s["swing"]["score"]
    er = earnings_risk(row.get("days_to_earnings"))
    bump = {"FAVORABLE": 0, "SELECTIVE": 3, "DEFENSIVE": 6}.get(regime, 0)
    # SIGNED distance — abs cannot tell a chase from a collapse.
    pct8 = _f(row.get("pct_vs_8ema"))
    return {
        "row": row, "scores": s, "strategy": strategy, "er": er,
        "inv": inv, "swing": swing, "conf": s["confluence"]["score"],
        "best": _primary_score(inv, swing, strategy),
        "reliable": s["reliable"],
        "rr": _f(row.get("rr")), "quality": _f(row.get("quality")),
        "health": _f(row.get("health")), "rs": _f(row.get("rs_rank")),
        "rvol": _f(row.get("rvol")),
        "breakout": _f(row.get("breakout_probability")),
        "pct8": pct8, "dist8": None if pct8 is None else abs(pct8),
        "above200": row.get("above_200ma"),
        "in_zone": row.get("in_buy_zone") is True,
        "buy_score": BUY_NOW_SCORE + bump,
        "buy_conf": BUY_NOW_CONFLUENCE + (1 if regime == "DEFENSIVE" else 0),
        # An imminent report is gap risk no setup quality offsets. Held as
        # one flag the engine applies to every rule marked `buy`, rather
        # than a check each branch has to remember — forgetting it in one
        # branch is exactly how MNST reached BUY ZONE on earnings day.
        "earnings_blocked": (er in ("HIGH", "CRITICAL")
                             and not earnings_strategy),
    }


def _gt(v, threshold) -> bool:
    """None fails. A missing input is not a satisfied condition."""
    return v is not None and v >= threshold


# ── the ladder ───────────────────────────────────────────────────────────────
# Ordered; first match wins. `buy` marks an action that puts money at risk —
# the engine refuses those on unreliable data and inside the earnings
# window, so a new rule cannot forget either guard. `note` builds the
# blockers/triggers shown with the verdict.
def _rule(action, test, buy=False, note=None):
    return {"action": action, "test": test, "buy": buy, "note": note}


def _avoid_reasons(c) -> list[str]:
    out = []
    if c["above200"] is False and not _gt(c["inv"], 60):
        out.append("Below the 200-day with weak fundamentals")
    if c["health"] is not None and c["health"] < 30:
        out.append(f"Balance sheet weak (health {c['health']:.0f})")
    if c["rr"] is not None and c["rr"] < 1.0:
        out.append(f"R:R {c['rr']:.1f} — risk exceeds reward")
    return out


def _earnings_when(c) -> str:
    days = _f(c["row"].get("days_to_earnings"))
    if days is None:
        return "imminent"
    return "today" if days <= 0 else f"in {days:.0f} day{'s' if days != 1 else ''}"


def _watch_triggers(c) -> list[str]:
    out = []
    if c["breakout"] is not None and c["breakout"] >= 70 and not _gt(c["rvol"], 1.2):
        out.append("Breakout confirms on above-average volume")
    if c["pct8"] is not None and abs(c["pct8"]) > 3:
        side = "above" if c["pct8"] > 0 else "below"
        out.append(f"Price returns to the 8 EMA "
                   f"({abs(c['pct8']):.1f}% {side} it now)")
    if c["rr"] is None:
        # A missing R:R blocks every buy gate just as firmly as a poor one,
        # but said nothing — MU carries the library's best investment score
        # and could never reach BUY NOW because this field is absent, with
        # no hint on the card as to why.
        out.append("R:R is unknown — no target/stop levels for this ticker")
    elif c["rr"] < MIN_RR:
        out.append(f"R:R improves above {MIN_RR:.0f} (now {c['rr']:.1f})")
    if not c["in_zone"] and c["row"].get("buy_zone_label"):
        out.append("Price enters the buy zone")
    return out or ["A valid entry appears"]


# ─────────────────────────────────────────────────────────────────────────────
# THE PULLBACK, PRICED
# ─────────────────────────────────────────────────────────────────────────────
# BUY ON PULLBACK is the only action that names a price you are not being
# offered yet, which is what makes the bare label unusable: it says wait
# without saying what for, how far below the tape it sits, or what would
# turn it into a buy. Two different rules produce it — extended above the
# 8 EMA (a chase), and a good name that simply missed a BUY NOW gate — and
# those are different waits, so the plan says which one this is.
#
# Every number here is measured off the row. Where a moving average's price
# is absent it is back-derived from the signed distance the scan did write
# (level = price / (1 + pct/100)), which is arithmetic on data already
# present rather than an estimate; with neither, the level is dropped.
_PULLBACK_LEVELS = (
    ("8 EMA", "ema8", "pct_vs_8ema"),
    ("21 EMA", "ema21", "pct_vs_21ema"),
    ("50 MA", "ma50", "pct_vs_50ma"),
)

# How deep each level is, ranked by where it actually sits rather than by
# which average it is. Labelling the 50 MA "full reset" by convention read
# as nonsense on CRDO, whose 50 MA sits 0.3% under the price while its
# 8 EMA is 5.5% below — the moving averages do not stay in textbook order
# after a fast move, and the description has to follow the tape.
_DEPTH = ("first stop on the way down", "deeper — second tranche",
          "full reset")


def _level(price, level_px, pct):
    """(level price, signed % the price sits above it, % move to reach it)."""
    if level_px is None and price and pct is not None and pct != -100:
        level_px = price / (1 + pct / 100.0)
    if pct is None and price and level_px:
        pct = (price - level_px) / level_px * 100.0
    move = (level_px / price - 1) * 100.0 if (price and level_px) else None
    return level_px, pct, move


def _entry_levels(c) -> list[str]:
    """The entry named as a price, not as a distance. "Pull back toward the
    8/21 EMA" is not something you can leave a limit order on."""
    row, price = c["row"], _f(c["row"].get("price"))
    below = []
    for name, px_key, pct_key in _PULLBACK_LEVELS:
        lp, _pct, move = _level(price, _f(row.get(px_key)), _f(row.get(pct_key)))
        if lp is None or move is None or move >= 0:
            continue                      # price is already at or under it
        below.append((move, name, lp))
    # Nearest first, and only the two you would actually stage into — a
    # third line adds a price nobody is waiting for.
    below.sort(reverse=True)
    return [f"Pulls back to the {name} at ${lp:,.2f} ({move:+.1f}% from here)"
            for move, name, lp in below[:2]]


def _pullback_triggers(c) -> list[str]:
    out = _entry_levels(c)
    for t in _watch_triggers(c):
        # The level lines already say this, and with a price attached.
        if out and ("8 EMA" in t or t == "A valid entry appears"):
            continue
        out.append(t)
    return out or ["A valid entry appears"]


def _score_label(strategy: str) -> str:
    return {"LONGTERM": "Investment score", "SWING": "Swing score"}.get(
        strategy, "Weaker of the two scores")


def _buy_now_gaps(c) -> list[str]:
    """What separates this from a BUY NOW, as numbers.

    The second pullback rule fires on names that are not extended at all,
    so without this the card said "wait" and named nothing to wait for.
    """
    out = []
    if not _gt(c["best"], c["buy_score"]):
        out.append(f"{_score_label(c['strategy'])} {c['best']} — a buy needs "
                   f"{c['buy_score']}")
    if not _gt(c["conf"], c["buy_conf"]):
        out.append(f"Confluence {c['conf']}/10 — a buy needs {c['buy_conf']}")
    if c["rr"] is None:
        out.append("R:R unknown — no target/stop levels for this ticker")
    elif c["rr"] < MIN_RR:
        out.append(f"R:R {c['rr']:.1f} — a buy needs {MIN_RR:.0f}")
    if not c["in_zone"] and c["dist8"] is not None and c["dist8"] > 3:
        out.append(f"{c['dist8']:.1f}% from the 8 EMA — a buy needs it inside "
                   f"3%, or price in the buy zone")
    return out


def pullback_plan(c) -> dict:
    """The wait, in numbers: how extended it is, off what, where the entry
    sits, and what still has to change before it is a buy.

    Takes the resolved context from `_context`, not a raw row — which gate
    it fell short of depends on the strategy being asked about, and that
    lives there rather than on the row.
    """
    row, pct8 = c["row"], c["pct8"]
    price = _f(row.get("price"))

    levels = []
    for name, px_key, pct_key in _PULLBACK_LEVELS:
        lp, pct, move = _level(price, _f(row.get(px_key)), _f(row.get(pct_key)))
        if lp is None and pct is None:
            continue
        levels.append({"name": name,
                       "price": None if lp is None else round(lp, 2),
                       "pct": None if pct is None else round(pct, 2),
                       "move": None if move is None else round(move, 2),
                       "note": None})
    # The note is the level's role in the wait, which depends on how deep it
    # sits — not on which average it happens to be. Levels the price has
    # already fallen through are not entries you are waiting for, and say so.
    depth = sorted((l for l in levels if (l["move"] or 0) < 0),
                   key=lambda l: -l["move"])
    for i, l in enumerate(depth):
        l["note"] = _DEPTH[i] if i < len(_DEPTH) else _DEPTH[-1]
    for l in levels:
        if l["note"] is None:
            l["note"] = ("price is already below it" if l["move"] is not None
                         else "")

    # Extension in units of the stock's own daily range: 6% above the 8 EMA
    # is a chase on a quiet name and a normal Tuesday on a volatile one, and
    # the raw percentage cannot tell those apart. Only computed where there
    # is something to be stretched by — "0.2× ATR above the 8 EMA" is a way
    # of saying "not extended", and on a card it reads as a finding.
    atr = _f(row.get("atr_pct"))
    stretch = (pct8 / atr) if (atr and atr > 0 and pct8 is not None
                               and pct8 > 2) else None

    if pct8 is None:
        reason = "unknown"
        headline = ("No 8 EMA distance for this ticker — the entry cannot be "
                    "priced, so this is a watch rather than a plan")
    elif pct8 > 3:
        reason = "extended"
        headline = (f"Extended {pct8:.1f}% above the 8 EMA — buying here pays "
                    f"the top of the move")
    elif pct8 < -1:
        reason = "in_progress"
        headline = (f"Already {abs(pct8):.1f}% below the 8 EMA — the pullback "
                    f"is under way; what is missing is a buy trigger")
    else:
        reason = "at_the_line"
        headline = (f"Sitting on the 8 EMA ({pct8:+.1f}%) — the entry is here, "
                    f"but the setup falls short of a buy")

    ctx = []

    def add(label, value, note=None):
        if value is not None:
            ctx.append({"label": label, "value": value, "note": note})

    d52 = _f(row.get("dist_52w_high"))
    if d52 is not None:
        add("52W high", "at the high" if d52 >= -0.5 else f"{abs(d52):.1f}% below",
            None if _f(row.get("high_52w")) is None
            else f"${_f(row.get('high_52w')):,.2f}")
    if stretch is not None:
        # No claim about how long the gap takes to close — that would be a
        # forecast. The multiple alone is the honest statement: this much
        # extension is N days' worth of the stock's own range.
        add("Stretch", f"{stretch:.1f}× ATR above the 8 EMA",
            f"ATR {atr:.1f}%/day")
    elif atr is not None:
        add("ATR", f"{atr:.1f}%/day")
    rsi = _f(row.get("rsi"))
    if rsi is not None:
        add("RSI (14)", f"{rsi:.0f}",
            "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else None)
    add("R:R", "unknown" if c["rr"] is None else f"{c['rr']:.1f}",
        None if c["rr"] is None or c["rr"] >= MIN_RR
        else f"below the {MIN_RR:.0f} a buy needs")
    bz = row.get("buy_zone_label")
    if bz:
        score = _f(row.get("buy_zone_score"))
        add("Buy zone", str(bz), None if score is None else f"score {score:.0f}")

    return {
        "reason": reason,
        "headline": headline,
        "pct_vs_8ema": pct8,
        "stretch_atr": None if stretch is None else round(stretch, 1),
        "price": price,
        "levels": levels,
        "context": ctx,
        # Not extended and not in the zone means the wait is about the
        # setup, not the price — say which gate, or the card is a shrug.
        "gaps": _buy_now_gaps(c),
    }


LADDER = (
    _rule("AVOID", lambda c: bool(_avoid_reasons(c)),
          note=lambda c: (_avoid_reasons(c), [])),

    _rule("WAIT", lambda c: c["earnings_blocked"] and _gt(c["best"], WATCH_SCORE),
          note=lambda c: ([f"Earnings {_earnings_when(c)}"],
                          ["Re-assess once the report is out"])),

    # Attractive but fragile — never mixed in with high-conviction buys.
    _rule("SPECULATIVE",
          lambda c: _gt(c["best"], WATCH_SCORE)
          and ((c["quality"] is not None and c["quality"] < 45)
               or (c["health"] is not None and c["health"] < 45)),
          note=lambda c: (["Quality or balance sheet is weak — size this as "
                           "a speculation, not a core position"], [])),

    _rule("BREAKOUT ENTRY", buy=True,
          test=lambda c: _gt(c["swing"], c["buy_score"]) and _gt(c["rs"], 80)
          and _gt(c["breakout"], 70) and _gt(c["rvol"], 1.2)
          and _gt(c["rr"], MIN_RR)),

    _rule("BUY NOW", buy=True,
          test=lambda c: _gt(c["best"], c["buy_score"])
          and _gt(c["conf"], c["buy_conf"]) and _gt(c["rr"], MIN_RR)
          and (c["in_zone"] or _gt(3, c["dist8"] if c["dist8"] is not None else 99))),

    _rule("BUY ZONE", buy=True,
          test=lambda c: c["in_zone"] and _gt(c["best"], PULLBACK_SCORE)
          and _gt(c["quality"], 70) and _gt(c["health"], 60)),

    # Extended ABOVE the 8 EMA is a chase worth waiting out.
    _rule("BUY ON PULLBACK", buy=True,
          test=lambda c: _gt(c["best"], PULLBACK_SCORE)
          and _gt(c["conf"], PULLBACK_CONFLUENCE)
          and c["pct8"] is not None and c["pct8"] > 3,
          note=lambda c: ([f"Extended {c['pct8']:.1f}% above the 8 EMA"],
                          _pullback_triggers(c))),

    # Poor R:R on an otherwise good name is a timing problem, not a verdict.
    _rule("WAIT",
          lambda c: _gt(c["best"], PULLBACK_SCORE)
          and _gt(c["conf"], PULLBACK_CONFLUENCE)
          and c["rr"] is not None and c["rr"] < MIN_RR,
          note=lambda c: ([f"R:R only {c['rr']:.1f}"],
                          [f"R:R improves above {MIN_RR:.0f} "
                           f"(now {c['rr']:.1f})"])),

    # The catch-all pullback: scores well enough to own, but missed a BUY
    # NOW gate. It used to carry no note at all, so the card read "wait"
    # and named neither the gate nor a price to wait for.
    _rule("BUY ON PULLBACK", buy=True,
          test=lambda c: _gt(c["best"], PULLBACK_SCORE)
          and _gt(c["conf"], PULLBACK_CONFLUENCE)
          and not (c["pct8"] is not None and c["pct8"] < -6),
          note=lambda c: (_buy_now_gaps(c), _pullback_triggers(c))),

    # Well under the short-term average is weakness, not a dip to buy.
    _rule("WATCH",
          lambda c: _gt(c["best"], WATCH_SCORE),
          note=lambda c: ([], _watch_triggers(c))),

    _rule("AVOID", lambda c: True,
          note=lambda c: ([f"Scores below the watch threshold "
                           f"(best {c['best']})"], [])),
)


def decide(row: dict, scores: dict | None = None,
           regime: str = "FAVORABLE",
           strategy: str = "BALANCED",
           earnings_strategy: bool = False) -> dict:
    """One primary action for one row, with the reasoning that produced it.

    `regime` (FAVORABLE / SELECTIVE / DEFENSIVE) only changes how selective
    the gates are — it is not a market prediction. `earnings_strategy=True`
    is the explicit opt-in that stops an imminent report blocking a buy.
    """
    s = scores or score_row(row)
    c = _context(row, s, regime, strategy, earnings_strategy)

    # Data quality first: nothing below can issue a buy on a row whose
    # scores were built from too few inputs.
    if not c["reliable"]:
        missing = sorted(set(s["investment"]["missing"] + s["swing"]["missing"]))
        return _verdict("AVOID", row, s, c["er"],
                        why=["Insufficient data to judge this setup"],
                        blockers=["Key inputs missing — " + ", ".join(missing[:4])],
                        triggers=["Run a scan so the missing fields populate"])

    for rule in LADDER:
        # Guards applied by the engine, not by each rule: a `buy` can never
        # fire inside the earnings window, whatever its own test says.
        if rule["buy"] and c["earnings_blocked"]:
            continue
        if not rule["test"](c):
            continue
        blockers, triggers = rule["note"](c) if rule["note"] else ([], [])
        # "Wait for a pullback" is only actionable with the pullback
        # attached — both rules that produce it carry the full plan.
        plan = pullback_plan(c) if rule["action"] == "BUY ON PULLBACK" else None
        return _verdict(rule["action"], row, s, c["er"],
                        blockers=blockers, triggers=triggers, pullback=plan)

    return _verdict("AVOID", row, s, c["er"])



def _verdict(action, row, scores, er, why=None, blockers=None, triggers=None,
             pullback=None):
    """Assemble the audit alongside the action — §9/§22: never a score
    without the reasoning that produced it.

    `institutional` rides along as CONTEXT and touches nothing above it: no
    score, no ladder rule, no blocker. It is 13F put/call skew, which is a
    quarter-end photograph months stale, long-only (written options are not
    reported at all), and drawn from filers over $100M AUM rather than from
    the options market. Any one of those disqualifies it from moving a buy;
    together they would make a precise-looking number quietly load-bearing.

    It appears in `context` rather than in `risks` for the same reason —
    `risks` is read as things that argue against the action, and a put-heavy
    filing from last quarter does not argue anything. See
    core.institutional.signal for the full argument.
    """
    conf = scores["confluence"]
    positives = why or [f"{n}" for n in conf["hits"]][:6]
    missing = conf["misses"][:4]
    unknown = sorted(set(scores["investment"]["missing"]
                         + scores["swing"]["missing"]))
    risks = list(blockers or [])
    if er in ("HIGH", "CRITICAL") and not any("Earnings" in r for r in risks):
        risks.append(f"Earnings risk {er}")
    if row.get("recovered"):
        risks.append(f"Data recovered from snapshot, as of "
                     f"{row.get('data_as_of') or 'an earlier scan'}")
    return {
        "ticker": row.get("ticker"),
        "action": action,
        "icon": ACTION_ICONS.get(action, ""),
        "investment": scores["investment"]["score"],
        "swing": scores["swing"]["score"],
        "confluence": conf["score"],
        "reliable": scores["reliable"],
        "earnings_risk": er,
        "why": positives,
        # Attached by the caller (nothing here reads a snapshot off disk),
        # and absent whenever the ticker was not in the last 13F run.
        "context": ([S] if (S := row.get("institutional_summary")) else []),
        "institutional": row.get("institutional"),
        "missing": missing,
        "unknown_fields": unknown,
        "risks": risks,
        "triggers": triggers or [],
        # Present only on BUY ON PULLBACK; see pullback_plan().
        "pullback": pullback,
    }
