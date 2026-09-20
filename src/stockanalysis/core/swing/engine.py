"""
engine.py — the swing verdict, computed independently of the company verdict
=============================================================================
A 3-to-20-day trade is decided by trend, setup, momentum, volume, structure
and the path to the first resistance. It is NOT decided by a reverse DCF, and
this engine reads none of the long-term model's outputs — not LQuality, not
the valuation band, not the buy zones.

That independence is the point. The same stock gets two verdicts, and they
routinely disagree:

    AVGO   💎 company 94/100, valuation Extreme  → long-term WAIT
           🟡 50 MA pullback, no confirmation    → swing APPROACHING

    a 55-quality name with a clean breakout retest can be an excellent swing
    and a business you would never hold for five years. Requiring LQuality 85
    for a swing trade would delete exactly those, which is why nothing here
    consults it.

Seven components:

    Market regime    15   a good chart fails in a selling tape
    Trend            20   structural, from the MA stack
    Setup            20   WHICH pattern, not merely "near support"
    Momentum         15   RSI banded, not rewarded for being high
    Volume           10   classified by what the volume happened ON
    Trigger quality  10   how good the entry signal itself is
    Trade quality    10   a blend, of which reward potential is one quarter

`blend` renormalises over whatever was measurable, so a missing ADX lowers
coverage rather than silently scoring zero.

Trigger quality earned its own weight because a setup and its trigger are
different things: price closing 0.1% over the 8 EMA on average volume is not
the event that reclaiming it on 1.6x volume is, and nothing in the first six
could tell them apart.

Trade quality stopped being a bare R:R reading for the same reason. AVGO
scored 100 there on 2.39R to its 52-week high while momentum sat at 36 — a
number describing the reward AVAILABLE rather than the quality of the trade,
which pulled the composite up on the one leg asking no hard questions.

The score is not the deliverable
--------------------------------
    score        0-100, and the state band it falls in, so a number and a
                 word can never disagree
    state        READY / NEAR READY / APPROACHING / DEVELOPING / AVOID by
                 band, overridden by the events TRIGGERED / EXTENDED /
                 MISSED / FAILED / NO SETUP
    triggers     TWO stages — early and full — plus the price past which
                 entering is chasing
    invalidation where the thesis has failed, which sits ABOVE the hard stop

The execution fields matter more than the number. "65/100" invites reading a
score as a decision; "65, 50 MA pullback, APPROACHING, early trigger $402.62,
full trigger $409.73, do not chase above $422.27, thesis fails on a close
under $390.33" is the same information in a form you can act on.
"""

from __future__ import annotations

# Generic 0-100 scoring helpers. Imported from the long-term package rather
# than copied: they are arithmetic (`scale`, `band`, `blend`) with nothing
# long-term about them, and _common's own docstring exists because four
# copies of `f` drifted. This is the one dependency this engine has on that
# package, and it carries no market opinion.
from stockanalysis.core.longterm._common import b, band, blend, f, s, scale

# ─────────────────────────────────────────────────────────────────────────────
# BANDS AND THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

# How close price has to sit to a level to be "at" it. ATR-scaled with a
# floor, because 2% is a different event on a utility than on a semiconductor.
NEAR_ATR = 0.6
NEAR_FLOOR_PCT = 1.5

# Above this far over the 8 EMA the trade is chasing: the entry is extended
# and the stop has to be too wide to be worth taking.
EXTENDED_PCT = 4.0

REGIME_SCORE = {"FAVORABLE": 100.0, "SELECTIVE": 55.0, "DEFENSIVE": 15.0}

# Setup base quality. A retest of a level that has already proved itself is
# the highest-expectancy pattern here; a mean-reversion bounce into a
# downtrend is the lowest thing still worth naming.
SETUPS = {
    "breakout_retest":    ("Breakout retest", 90),
    "pullback_21ema":     ("Pullback to 21 EMA", 85),
    "breakout":           ("Breakout", 80),
    "tight_consolidation": ("Tight consolidation", 75),
    "pullback_50ma":      ("Pullback to 50 MA", 70),
    "mean_reversion":     ("Mean-reversion bounce", 40),
    "failed_breakout":    ("Failed breakout", 15),
    "none":               ("No setup", 0),
}

# The bottom grade is "No trade", not "Avoid". Blackstone scored 50 with
# momentum at 95, a good path and no pattern to trade — "Avoid" said the
# stock was undesirable when the model's own reading was the opposite. The
# grade describes the TRADE available today; `stock_view` describes the chart.
GRADES = ((85, "A+"), (75, "A"), (65, "B"), (55, "C"), (0, "No trade"))

# ─────────────────────────────────────────────────────────────────────────────
# GATES — eligibility, which the score cannot override
# ─────────────────────────────────────────────────────────────────────────────
# The score ranks; the gates decide. Without them a stock could reach 80 on
# momentum and path quality alone while having no setup to enter on, which is
# exactly BX: momentum 95, trade quality 73, setup 0. A weighted average
# cannot express "this leg is not optional".
MIN_SETUP_SCORE = 40.0        # below this there is no pattern to trade
MIN_TREND_SCORE = 50.0        # below this, no long except a reversal setup
MIN_MARKET_SCORE = 40.0       # below this, no new long swing at all
MIN_FIRST_R = 1.0             # first resistance closer than this is a poor path
MAX_STOP_PCT = 20.0           # beyond this it is not a swing trade
WIDE_STOP_PCT = 12.0          # beyond this it is a high-volatility special

REVERSAL_SETUPS = ("mean_reversion",)


def stop_efficiency(stop_pct):
    """Capital efficiency of the stop, banded.

    Position sizing keeps ACCOUNT risk constant, which is what hid this: WDC
    priced a swing with a 29.4% stop and the sizing engine dutifully cut the
    position to 6.4% of capital. Account risk was fine and the trade was
    still a bad swing — the capital is tied up for weeks against a stop that
    only a multi-month move justifies.
    """
    if stop_pct is None:
        return None
    return band(stop_pct, ((4, 100.0), (6, 90.0), (8, 75.0), (10, 60.0),
                           (15, 40.0), (20, 20.0), (999, 0.0)))


def first_resistance_grade(first_r):
    """(icon, label) for the room to the FIRST obstacle — the level the trade
    actually has to get through, as opposed to a 52-week high behind it."""
    if first_r is None:
        return "⚪", "unmeasured"
    if first_r >= 2.0:
        return "🟢", "clear"
    if first_r >= 1.0:
        return "🟡", "tight"
    return "🔴", "blocked"


def evaluate_gates(setup_key, setup_s, trend_s, market_s, path, stop_pct,
                   chase, full_trigger) -> list[dict]:
    """Seven pass/fail checks. Each reports None when it could not be
    measured, so "unmeasured" never silently reads as "passed"."""
    first_r = (path or {}).get("first_r")
    reversal = setup_key in REVERSAL_SETUPS
    return [
        {"name": "Market", "blocking": True, "ok": None if market_s is None
         else market_s >= MIN_MARKET_SCORE,
         "detail": f"regime {market_s:.0f}" if market_s is not None
                   else "regime unknown"},
        {"name": "Trend", "blocking": True, "ok": None if trend_s is None
         else (trend_s >= MIN_TREND_SCORE or reversal),
         "detail": (f"{trend_s:.0f} — below {MIN_TREND_SCORE:.0f} is a "
                    f"breakdown, long entries need a reversal setup"
                    if trend_s is not None and trend_s < MIN_TREND_SCORE
                    else f"{trend_s:.0f}" if trend_s is not None
                    else "not measured")},
        {"name": "Setup", "blocking": True, "ok": None if setup_s is None
         else setup_s >= MIN_SETUP_SCORE,
         "detail": (f"no pattern to trade" if setup_key == "none"
                    else f"{SETUPS.get(setup_key, SETUPS['none'])[0]} "
                         f"({setup_s:.0f})")},
        {"name": "Path", "blocking": True, "ok": None if first_r is None else first_r >= MIN_FIRST_R,
         "detail": (f"{first_r:.2f}R to the first resistance"
                    if first_r is not None else "no path measured")},
        {"name": "Stop", "blocking": True, "ok": None if stop_pct is None
         else stop_pct <= MAX_STOP_PCT,
         "detail": (f"{stop_pct:.1f}% stop"
                    + (" — high-volatility special case"
                       if stop_pct is not None and WIDE_STOP_PCT < stop_pct
                       <= MAX_STOP_PCT else "")
                    if stop_pct is not None else "no stop")},
        # Not a hard gate: absent confirmation means the trigger has not
        # fired yet, which is APPROACHING rather than rejected.
        {"name": "Volume confirmation", "blocking": False, "ok": None if not full_trigger
         else bool(full_trigger.get("met")),
         "detail": ("confirmed" if full_trigger and full_trigger.get("met")
                    else "not yet — the full trigger needs it")
         if full_trigger else "no trigger to confirm"},
        {"name": "No chase", "blocking": True, "ok": None if not chase
         else not chase.get("exceeded"),
         "detail": (f"cap ${chase['price']:,.2f}" if chase else "no cap")},
    ]

# The score bands ARE the state, so a number and a word can never disagree.
# Conditional states (TRIGGERED / EXTENDED / FAILED / NO SETUP) override
# them, because those describe events rather than degrees.
STATE_BANDS = ((85, "READY", "enter if the trigger fires"),
               (75, "NEAR READY", "wait for the trigger"),
               (65, "APPROACHING", "watchlist"),
               (55, "DEVELOPING", "no trade"),
               (0, "AVOID", "ignore"))

# Risk per trade by grade. The account's 2% is a CEILING, not the default —
# taking full size on a B setup is how a good system produces a bad month.
GRADE_RISK = {"A+": (1.5, 2.0), "A": (1.0, 1.5), "B": (0.5, 1.0),
              "C": (0.0, 0.0), "Avoid": (0.0, 0.0)}

# ...and the tape caps it again. A good setup in a selective market is still
# a good setup, but it is not one to take full size in, and leaving that
# adjustment to the trader is how a scanner's "1.5-2.0%" quietly becomes the
# number actually risked on every trade regardless of conditions.
REGIME_RISK_CAP = ((85, 2.0), (75, 1.5), (65, 1.0), (55, 0.75), (0, 0.5))


def risk_for(grade, regime_score) -> dict:
    lo, hi = GRADE_RISK.get(grade, (0.0, 0.0))
    cap, note = hi, None
    if regime_score is not None:
        cap = next(c for floor, c in REGIME_RISK_CAP if regime_score >= floor)
        if cap < hi:
            note = (f"capped at {cap:.2f}% by the market regime — the setup "
                    f"would otherwise carry up to {hi:.1f}%")
    return {"min": round(min(lo, cap), 2), "max": round(min(hi, cap), 2),
            "regime_cap_pct": cap, "capped_by_regime": bool(note),
            "note": note or ("the account's per-trade risk is a ceiling, not "
                             "a default — full size belongs to A+ setups only")}

# Expected holding window per setup, and when to give up on a position that
# has not moved. A swing that stagnates must not quietly become an
# investment — that is the specific failure a time stop prevents.
TIME_STOPS = {
    "breakout":            (3, 10, 5),
    "breakout_retest":     (3, 12, 5),
    "pullback_21ema":      (3, 12, 6),
    "pullback_50ma":       (5, 15, 7),
    "tight_consolidation": (5, 20, 10),
    "mean_reversion":      (2, 8, 4),
}


def _near(price, level, atr_pct) -> bool:
    if price is None or level is None or not price:
        return False
    tol = max(NEAR_FLOOR_PCT, (atr_pct or 0) * NEAR_ATR)
    return abs(level / price - 1) * 100 <= tol


# ─────────────────────────────────────────────────────────────────────────────
# 1. MARKET REGIME — 15%
# ─────────────────────────────────────────────────────────────────────────────

def market_component(regime: str | None) -> tuple[float | None, str]:
    """A technically perfect chart fails in a tape that is selling everything,
    which is why this is weighted before the stock is even looked at."""
    key = str(regime or "").upper()
    if key not in REGIME_SCORE:
        return None, "market regime unknown"
    return REGIME_SCORE[key], f"regime {key.title()}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. TREND — 20%
# ─────────────────────────────────────────────────────────────────────────────
# Structural points, scored out of what could be measured. Deliberately
# separate from momentum: a stock can have an intact trend and no momentum
# (AVGO), or momentum inside a broken trend (a dead-cat bounce), and one
# number covering both would hide exactly those two cases.

TREND_POINTS = (
    ("price above the 200 MA", 5),
    ("200 MA rising", 5),
    ("50 MA above the 200 MA", 4),
    ("price above the 50 MA", 3),
    ("21 EMA above the 50 MA", 2),
    ("making higher highs", 3),
)


def trend_component(row) -> tuple[float | None, str, dict]:
    price = f(row.get("Current Price"))
    ema21, ma50, ma200 = (f(row.get("21EMA")), f(row.get("50MA")),
                          f(row.get("200MA")))
    slope200 = f(row.get("MA200_Slope%"))
    days_since_high = f(row.get("Days_Since_52W_High"))

    checks = [
        ("price above the 200 MA", 5,
         None if price is None or ma200 is None else price > ma200),
        ("200 MA rising", 5, None if slope200 is None else slope200 > 0),
        ("50 MA above the 200 MA", 4,
         None if ma50 is None or ma200 is None else ma50 > ma200),
        ("price above the 50 MA", 3,
         None if price is None or ma50 is None else price > ma50),
        ("21 EMA above the 50 MA", 2,
         None if ema21 is None or ma50 is None else ema21 > ma50),
        # Proxy for higher highs: the 52-week high is recent. Named as the
        # proxy it is rather than claimed as swing-structure analysis.
        ("making higher highs", 3,
         None if days_since_high is None else days_since_high <= 30),
    ]
    got = sum(w for _n, w, ok in checks if ok is not None)
    won = sum(w for _n, w, ok in checks if ok)
    if not got:
        return None, "no trend data", {"hits": [], "misses": []}
    hits = [n for n, _w, ok in checks if ok]
    misses = [n for n, _w, ok in checks if ok is False]
    return (won / got * 100.0,
            f"{won} of {got} structural points",
            {"hits": hits, "misses": misses})


# ─────────────────────────────────────────────────────────────────────────────
# 3. SETUP — 25%
# ─────────────────────────────────────────────────────────────────────────────

def classify_setup(row) -> tuple[str, str]:
    """Which pattern is occurring. Checked most specific first, because a
    breakout retest also looks like "near a level" to anything less careful.

    Returns (key, why). "none" is a real answer — most of a library on most
    days has no actionable pattern, and inventing one for every row is how a
    scanner becomes noise.
    """
    price = f(row.get("Current Price"))
    ema8, ema21 = f(row.get("8EMA")), f(row.get("21EMA"))
    ma50, ma200 = f(row.get("50MA")), f(row.get("200MA"))
    pbl = f(row.get("Prior_Breakout_Level"))
    atr = f(row.get("ATR_Pct")) or 2.0
    vol = f(row.get("Vol_vs_20D"))
    rsi = f(row.get("RSI_14"))
    dist_high = f(row.get("Dist_52W_High%"))
    days_high = f(row.get("Days_Since_52W_High"))
    shrinking = b(row.get("ATR Shrinking"))

    if price is None:
        return "none", "no price"

    above200 = ma200 is not None and price > ma200

    # Broke out and lost it — the short-side pattern, and the one most often
    # mistaken for a pullback because price is back near a familiar level.
    if (pbl is not None and price < pbl and days_high is not None
            and days_high <= 30):
        return "failed_breakout", (f"lost the ${pbl:,.2f} breakout level "
                                   f"within a month of the 52-week high")

    # Back to the level it broke out from, from above. All four conditions
    # are load-bearing: Prior_Breakout_Level sits within 3% of price for 49%
    # of the library, so proximity alone matched half of everything and made
    # the highest-scoring pattern the commonest one. A retest also requires a
    # RECENT high to have retested from, and a pullback shallow enough to be
    # a test rather than a failure.
    if (pbl is not None and price >= pbl and price <= pbl * 1.02
            and above200
            and days_high is not None and days_high <= 30
            and dist_high is not None and -15 <= dist_high <= -0.5):
        return "breakout_retest", (f"pulled {abs(dist_high):.0f}% off a high "
                                   f"made {days_high:.0f} days ago and holding "
                                   f"the ${pbl:,.2f} breakout level")

    if (pbl is not None and price > pbl and dist_high is not None
            and dist_high > -5 and vol is not None and vol >= 1.5):
        return "breakout", (f"through ${pbl:,.2f} near the 52-week high on "
                            f"{vol:.1f}x volume")

    if shrinking and _near(price, ema21, atr) and _near(price, ma50, atr):
        return "tight_consolidation", ("range contracting with the 21 EMA and "
                                       "50 MA converging on price")

    # Pullbacks: shallow first. Both require the long-term structure intact —
    # a "pullback" under the 200 MA is a downtrend.
    if above200 and ema21 is not None and _near(price, ema21, atr):
        return "pullback_21ema", f"resting on the 21 EMA at ${ema21:,.2f}"

    if above200 and ma50 is not None and _near(price, ma50, atr):
        return "pullback_50ma", f"resting on the 50 MA at ${ma50:,.2f}"

    if not above200 and rsi is not None and rsi < 35:
        return "mean_reversion", (f"oversold at RSI {rsi:.0f} below the "
                                  f"200 MA — counter-trend")

    return "none", "no recognised pattern at this price"


def setup_component(row, setup_key) -> tuple[float | None, str]:
    """The pattern's base quality, adjusted by whether it is confirmed.

    Confirmation moves the score rather than gating it, because an
    unconfirmed pullback in a strong trend is a real setup that has not
    triggered yet — which the state field says explicitly.
    """
    label, base = SETUPS.get(setup_key, SETUPS["none"])
    if setup_key == "none":
        return 0.0, "no actionable pattern"
    score = float(base)
    notes = [label]

    reversal = s(row.get("Reversal_Candle"))
    if reversal and reversal.lower() not in ("none", "no", "-"):
        score += 8
        notes.append(f"{reversal} candle")
    if b(row.get("Volume_Confirmation")):
        score += 7
        notes.append("volume confirmed")
    price, ema8 = f(row.get("Current Price")), f(row.get("8EMA"))
    if price is not None and ema8 is not None and price > ema8:
        score += 5
        notes.append("back above the 8 EMA")
    touches = f(row.get("Touches"))
    if touches is not None and touches >= 50:
        score += 5
        notes.append(f"{touches:.0f} touches at this level")
    return max(0.0, min(100.0, score)), " · ".join(notes)


# ─────────────────────────────────────────────────────────────────────────────
# 4. MOMENTUM — 15%
# ─────────────────────────────────────────────────────────────────────────────

def momentum_component(row) -> tuple[float | None, str]:
    """RSI is BANDED, not rewarded for being high.

    The common error is scoring RSI 78 above RSI 62. In a swing window an
    overbought reading is as often the last few days of a move as the middle
    of one, so the band peaks in the 55-70 range and steps DOWN above 75.
    """
    parts, notes = [], []

    rsi = f(row.get("RSI_14"))
    if rsi is not None:
        parts.append(band(rsi, ((30, 10.0), (45, 35.0), (55, 70.0),
                                (70, 100.0), (75, 75.0), (100, 45.0))))
        notes.append(f"RSI {rsi:.0f}")

    adx = f(row.get("ADX_14"))
    if adx is not None:
        # Below 20 there is no trend to ride; above 40 it is already extended.
        parts.append(band(adx, ((15, 20.0), (20, 45.0), (25, 75.0),
                                (40, 100.0), (100, 70.0))))
        notes.append(f"ADX {adx:.0f}")

    rs_rank = f(row.get("RS_Rank"))
    if rs_rank is not None:
        parts.append(scale(rs_rank, 20.0, 90.0))
        notes.append(f"RS rank {rs_rank:.0f}")

    pct8 = f(row.get("Pct_vs_8EMA"))
    if pct8 is not None:
        # Just above the 8 EMA is ideal; far above is extended, far below is
        # a stock still falling.
        parts.append(band(pct8, ((-6, 15.0), (-2, 55.0), (0, 80.0),
                                 (4, 100.0), (8, 55.0), (100, 25.0))))
        notes.append(f"{pct8:+.1f}% vs the 8 EMA")

    if not parts:
        return None, "no momentum data"
    return sum(parts) / len(parts), " · ".join(notes)


# ─────────────────────────────────────────────────────────────────────────────
# 5. VOLUME — 15%
# ─────────────────────────────────────────────────────────────────────────────

def volume_component(row) -> tuple[float | None, str, str]:
    """Classified by WHAT THE VOLUME HAPPENED ON, which is the thing a bare
    ratio cannot tell you.

    AVGO is the case that forces it: 1.63x average volume alongside five
    distribution days. Read as a ratio that looks like conviction; read with
    direction it is selling. Returns (score, detail, class).
    """
    price = f(row.get("Current Price"))
    prev = f(row.get("Prev-Day Close"))
    vol = f(row.get("Vol_vs_20D"))
    pullback_vol = f(row.get("Pullback_Vol_Ratio"))
    drying = b(row.get("VolumeDryingUp"))
    dist_days = f(row.get("Distribution_Days_25d"))
    reversal = s(row.get("Reversal_Candle"))
    confirmed = b(row.get("Volume_Confirmation"))

    up_day = None if price is None or prev is None else price >= prev

    kind, score, notes = "unclear", None, []
    if vol is not None:
        notes.append(f"{vol:.2f}x 20-day volume")

    if (reversal and reversal.lower() not in ("none", "no", "-")
            and vol is not None and vol >= 1.5 and up_day):
        kind, score = "reversal", 100.0
        notes.append("expansion on a reversal off support")
    elif vol is not None and vol >= 1.5 and up_day is True:
        kind, score = "breakout", 90.0
        notes.append("expansion on an up day")
    elif vol is not None and vol >= 1.5 and up_day is False:
        kind, score = "selling", 15.0
        notes.append("expansion on a DOWN day — this is supply")
    elif drying or (pullback_vol is not None and pullback_vol < 0.8):
        kind, score = "quiet_pullback", 85.0
        notes.append("contracting into the pullback")
    elif pullback_vol is not None:
        kind, score = "neutral", 55.0
        notes.append(f"pullback volume {pullback_vol:.2f}x")

    if score is not None and dist_days is not None and dist_days >= 4:
        # A confirmation, never a veto: heavy distribution reduces the score
        # and shows up in the thesis, but it does not delete a setup that
        # trend, structure and momentum all agree on.
        score = max(0.0, score - 25.0)
        kind = "distribution" if kind in ("neutral", "quiet_pullback") else kind
        notes.append(f"{dist_days:.0f} distribution days in 25")
    if confirmed is False and score is not None:
        notes.append("no volume confirmation yet")

    if score is None:
        return None, "no volume data", "unknown"
    return score, " · ".join(notes), kind


# ─────────────────────────────────────────────────────────────────────────────
# 6. TRADE QUALITY — 10%, and the path that decides it
# ─────────────────────────────────────────────────────────────────────────────

# How far below price a defended shelf can sit and still be the entry you
# would actually work an order at. Beyond this it is a different trade with a
# different holding period, not this one at a better price.
ENTRY_REACH_ATR = 1.25
# Touches at which a level counts as defended rather than merely tracked.
DEFENDED_TOUCHES = 20


def tested_support(row, price):
    """The key-level shelf below price, when the market has actually defended
    it. (price, label) or (None, reason).

    Same preference the position-sizing engine applies, and for the same
    reason: `S1` can be a volume-confirmed shelf the market has turned at
    dozens of times, or arithmetic on recent closes. Only the first is a
    price to work an order at, and treating the second as one invents a
    level.
    """
    s1 = f(row.get("S1"))
    touches = f(row.get("Touches"))
    confirmed = b(row.get("Volume_Confirmation"))
    if not s1 or not price or s1 >= price:
        return None, "no shelf below the price"
    defended = (touches is not None and touches >= DEFENDED_TOUCHES) or bool(confirmed)
    if not defended:
        return None, f"the ${s1:,.2f} shelf is not volume-confirmed"
    bits = []
    if touches is not None:
        bits.append(f"{touches:.0f} touches")
    if confirmed:
        bits.append("volume-confirmed")
    return round(s1, 2), " · ".join(bits) or "tested"


def planned_entry(row, setup_key):
    """The price the trade would actually be taken at, which is not always
    today's price.

    A pullback setup triggers on reclaiming the 8 EMA, so the 8 EMA is part
    of the entry rather than an obstacle above it. Measuring the path from
    today's price instead graded BMY's clean 21 EMA pullback as "Poor" on the
    strength of an 8 EMA sitting 0.5% overhead — the exact level the trigger
    requires it to clear.
    """
    price = f(row.get("Current Price"))
    ema8 = f(row.get("8EMA"))
    atr_pct = f(row.get("ATR_Pct")) or 2.0
    if price is None:
        return None, "no price"

    # A defended shelf within reach wins, because that is where the order
    # actually goes. The two panels disagreed on exactly this: the sizing
    # engine worked a $266.26 tested support while this one quoted $279.44,
    # the current price, for the same stock on the same day.
    shelf, shelf_note = tested_support(row, price)
    if shelf and (price - shelf) / price * 100 <= atr_pct * ENTRY_REACH_ATR:
        return shelf, f"at the tested shelf ${shelf:,.2f} — {shelf_note}"

    if setup_key in ("pullback_21ema", "pullback_50ma", "tight_consolidation"):
        if ema8 is not None and ema8 > price:
            return round(ema8, 2), f"on a reclaim of the 8 EMA at ${ema8:,.2f}"
    if shelf:
        return round(price, 2), (f"at the current price — the ${shelf:,.2f} "
                                 f"shelf is further than one ATR below")
    return round(price, 2), f"at the current price — {shelf_note}"


def resistance_path(row, entry, stop, price=None) -> dict:
    """Every tracked level between the entry and the 52-week high, priced in R.

    The reason this exists: quoting 4.46R to a distant 52-week high while
    three resistances sit within 4% of the entry describes a trade that does
    not have that path. The first resistance is what the trade actually has
    to get through, so it is reported first and it is what grades the path.
    """
    if entry is None or stop is None or entry <= stop:
        return {"levels": [], "first_r": None, "quality": None,
                "note": "no priced stop to measure against"}
    risk = entry - stop
    raw = [("R1 resistance", f(row.get("R1"))),
           ("21 EMA", f(row.get("21EMA"))),
           ("8 EMA", f(row.get("8EMA"))),
           ("prior breakout", f(row.get("Prior_Breakout_Level"))),
           ("52-week high", f(row.get("52W High")))]
    levels = []
    for name, level in raw:
        if level is None or level <= entry:
            continue
        levels.append({"name": name, "price": level,
                       "r": round((level - entry) / risk, 2),
                       "move_pct": round((level / entry - 1) * 100, 1)})
    levels.sort(key=lambda x: x["price"])
    # Dedupe levels sitting on top of each other — two names for one price is
    # not two obstacles.
    deduped = []
    for lvl in levels:
        if deduped and abs(lvl["price"] / deduped[-1]["price"] - 1) < 0.005:
            deduped[-1]["name"] += f" / {lvl['name']}"
            continue
        deduped.append(lvl)

    if not deduped:
        return {"levels": [], "first_r": None, "quality": None,
                "note": "nothing tracked overhead"}
    # Levels the price has to climb through just to REACH the trigger. They
    # are not obstacles to the trade — the trade has not started — but they
    # are the reason it may never start, and moving the entry up to the
    # trigger price silently hid them: AVGO has to clear R1 at $397 and the
    # 21 EMA at $403 before an 8 EMA reclaim at $410 is even on the table.
    first = deduped[0]["r"]
    # The same levels play two roles depending on which side of the trigger
    # you are on, and calling them one thing was wrong. Below the entry they
    # are resistance the price must clear to REACH the trigger; once entered
    # above them, they are the support the position sits on. Reporting them
    # as "resistance the trade has to clear" after entry described obstacles
    # that are behind you.
    to_clear, support_after = [], []
    if price is not None and entry > price:
        for name, level in raw:
            if level is None or not (price < level < entry):
                continue
            to_clear.append({"name": name, "price": level,
                             "move_pct": round((level / price - 1) * 100, 1)})
        to_clear.sort(key=lambda x: x["price"])
    for name, level in raw:
        if level is None or level >= entry or (stop and level <= stop):
            continue
        support_after.append({"name": name, "price": level,
                              "below_entry_pct": round((level / entry - 1) * 100, 1)})
    support_after.sort(key=lambda x: -x["price"])

    top = deduped[0]
    where = f'{top["name"]} at ${top["price"]:,.2f}'
    if first >= 1.5:
        quality, note = "Good", (f"clear room before the first resistance — "
                                 f"{where}, {first:.2f}R away")
    elif first >= 0.8:
        quality, note = "Fair", f"{where} is {first:.2f}R away — about one R"
    else:
        quality, note = "Poor", (f"{where} is only {first:.2f}R away — the "
                                 f"headline target is not a clean path")
    return {"levels": deduped, "first_r": first, "quality": quality,
            "note": note, "to_clear": to_clear,
            "support_after_entry": support_after}


# Path quality is a blend, not a single R reading. AVGO scored 100 here on
# 2.39R to its 52-week high while momentum sat at 36 and volume at 65 — a
# number that described the REWARD available rather than the quality of the
# trade, and pulled the composite up on the one leg that was not asking any
# hard questions.
PATH_WEIGHTS = (
    ("Resistance clearance", 30),   # how far to the first thing overhead
    ("Target room", 25),            # R to the furthest tracked level
    ("Resistance density", 20),     # how many levels stand in the way
    ("Stop quality", 15),           # is the stop tight enough to be usable
    ("Level strength", 10),         # have the levels overhead been defended
)


def trade_component(path, row, entry, stop) -> tuple[float | None, str, dict]:
    """Five readings, not one. Reward potential is 25% of this and never the
    whole of it."""
    if path.get("first_r") is None:
        return None, path.get("note") or "no path measured", {}

    # Every detail names the PRICE it is talking about. "0.20R away" is only
    # interpretable next to the level it measures to and the entry it
    # measures from: the reader cannot check a bare R multiple against a
    # chart, and checking against the chart is the whole point of showing
    # the workings.
    parts = []
    levels = path.get("levels") or []
    first = path["first_r"]
    first_level = levels[0] if levels else None
    entry_txt = f" from ${entry:,.2f}" if entry else ""
    parts.append(("Resistance clearance", 30,
                  band(first, ((0.5, 15.0), (0.8, 40.0), (1.5, 75.0),
                               (3.0, 100.0), (99.0, 95.0))),
                  (f'{first_level["name"]} ${first_level["price"]:,.2f} — '
                   f'{first:.2f}R{entry_txt} ({first_level["move_pct"]:+.1f}%)'
                   if first_level else f"first resistance {first:.2f}R away")))

    furthest = levels[-1] if levels else None
    if furthest is not None:
        parts.append(("Target room", 25,
                      band(furthest["r"], ((1.0, 15.0), (2.0, 50.0),
                                           (3.0, 80.0), (99.0, 100.0))),
                      f'{furthest["name"]} ${furthest["price"]:,.2f} — '
                      f'{furthest["r"]:.1f}R ({furthest["move_pct"]:+.1f}%), '
                      f'the furthest tracked level'))

    # Density: levels stacked between the entry and the target are the
    # difference between a 2.4R path and a 2.4R obstacle course.
    blocking = [lvl for lvl in levels[:-1]] if len(levels) > 1 else []
    named = ", ".join(f'{lvl["name"]} ${lvl["price"]:,.2f}' for lvl in blocking)
    parts.append(("Resistance density", 20,
                  band(len(blocking), ((0, 100.0), (1, 75.0), (2, 45.0),
                                       (3, 25.0), (99, 10.0))),
                  (f"{len(blocking)} level(s) in the way: {named}" if blocking
                   else "nothing between the entry and the target")))

    if entry and stop and entry > stop:
        stop_pct = (entry - stop) / entry * 100
        atr_pct = f(row.get("ATR_Pct")) or 2.0
        # A stop wider than ~3 ATR is not a swing stop; the position it
        # implies is too small to matter or the risk too large to take.
        parts.append(("Stop quality", 15,
                      band(stop_pct / atr_pct, ((1.0, 70.0), (2.0, 100.0),
                                                (3.0, 60.0), (99.0, 20.0))),
                      f"stop ${stop:,.2f} — {stop_pct:.1f}% "
                      f"({stop_pct / atr_pct:.1f} ATR) below the "
                      f"${entry:,.2f} entry"))

    # `Touches` counts tests of the R1/S1 key level, so it only describes the
    # level overhead when R1 IS the first thing overhead. Applying it to a
    # 52-week high 20% away — as the first version did — attaches a real
    # number to the wrong level and reads as evidence.
    touches, r1 = f(row.get("Touches")), f(row.get("R1"))
    if (touches is not None and r1 is not None and first_level
            and abs(first_level["price"] / r1 - 1) < 0.005):
        # A heavily defended level overhead is a harder ceiling, so strength
        # scores INVERSELY here — the opposite of how it reads as support.
        parts.append(("Level strength", 10,
                      band(touches, ((20, 100.0), (100, 70.0), (300, 40.0),
                                     (99999, 25.0))),
                      f"{touches:.0f} touches at R1 ${r1:,.2f}, the first "
                      f"level overhead"))

    scored = blend([(n, w, v, d) for n, w, v, d in parts])
    return (scored["score"], f'path {path["quality"]} · ' +
            " · ".join(c["detail"] for c in scored["components"][:2]),
            scored)


def trigger_component(row, early, full, chase) -> tuple[float | None, str]:
    """How good the trigger itself is, separately from the setup.

    A setup can be excellent and its trigger weak: price closing 0.1% over
    the 8 EMA on average volume is not the same event as reclaiming it on
    1.6x volume with relative strength improving, and the old score could not
    tell them apart.
    """
    if not full:
        return None, "no trigger to grade"
    parts, notes = [], []

    vol = f(row.get("Vol_vs_20D"))
    if vol is not None:
        parts.append(band(vol, ((0.8, 25.0), (1.0, 45.0), (1.5, 80.0),
                                (3.0, 100.0), (99.0, 70.0))))
        notes.append(f"{vol:.2f}x volume")

    reversal = s(row.get("Reversal_Candle"))
    has_reversal = bool(reversal and reversal.lower() not in ("none", "no", "-"))
    parts.append(100.0 if has_reversal else 35.0)
    notes.append(reversal if has_reversal else "no reversal candle")

    rs_rank = f(row.get("RS_Rank"))
    if rs_rank is not None:
        parts.append(scale(rs_rank, 20.0, 85.0))
        notes.append(f"RS rank {rs_rank:.0f}")

    # Entry efficiency: how far price sits from the trigger. A trigger 4%
    # away is a worse trigger than one 0.5% away, because everything between
    # is give-back you pay for before the trade even starts.
    dist = full.get("distance_pct")
    if dist is not None:
        parts.append(band(abs(dist), ((0.5, 100.0), (1.5, 80.0), (3.0, 50.0),
                                      (5.0, 25.0), (99.0, 10.0))))
        notes.append(f"trigger {dist:+.1f}% from here")

    if chase and chase.get("exceeded"):
        parts.append(0.0)
        notes.append("already past the no-chase cap")

    if not parts:
        return None, "no trigger data"
    return sum(parts) / len(parts), " · ".join(notes)


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION — triggers, chase cap, invalidation
# ─────────────────────────────────────────────────────────────────────────────
# One trigger was too blunt. On AVGO the single trigger was a close above the
# 8 EMA at $409.73 while price sat at $392.99 — asking for a 4.3% move before
# entry, against a stop at $374.40, which is an 8.6% stop on a 3-to-20-day
# trade. Two stages let the trader take the earlier, tighter entry with a
# smaller size, or wait for the full confirmation.

# How far past the trigger is still an entry rather than a chase. Beyond
# this the move has happened without you and the stop is too far behind.
MAX_CHASE_ATR = 0.75


def triggers_for(row, setup_key, entry=None):
    """(early, full, chase cap). Early is the first evidence the pullback has
    stopped; full is the confirmation. Both carry the level and the
    condition, and whether it has already happened."""
    price = f(row.get("Current Price"))
    ema8, ema21 = f(row.get("8EMA")), f(row.get("21EMA"))
    ma50 = f(row.get("50MA"))
    pbl = f(row.get("Prior_Breakout_Level"))
    atr_pct = f(row.get("ATR_Pct")) or 2.0
    reversal = s(row.get("Reversal_Candle"))
    has_reversal = bool(reversal and reversal.lower() not in ("none", "no", "-"))
    confirmed = b(row.get("Volume_Confirmation"))

    if setup_key in ("none", "failed_breakout"):
        return None, None, None

    # When the entry is a limit at a tested shelf BELOW the price, the early
    # trigger is confirmation AT that shelf — not a reclaim above it. Leaving
    # the reclaim as the early stage made the panel say "buy at $391.33" and
    # "trigger: reclaim $409.73" at once, which are two different trades.
    if entry is not None and price is not None and entry < price:
        early = {"price": round(entry, 2),
                 "condition": (f"hold ${entry:,.2f} with a bullish reversal "
                               f"on expanding volume"),
                 "met": bool(has_reversal and confirmed),
                 "distance_pct": round((entry / price - 1) * 100, 1)}
        full_level = ema8 if ema8 else entry
        full = {"price": round(full_level, 2),
                "condition": "close back above the 8 EMA on expanding volume",
                "met": bool(price >= full_level and (confirmed or has_reversal)),
                "distance_pct": round((full_level / price - 1) * 100, 1)}
        cap = full["price"] * (1 + atr_pct * MAX_CHASE_ATR / 100.0)
        return early, full, {
            "price": round(cap, 2), "exceeded": price > cap,
            "note": (f"above ${cap:,.2f} the move has already happened — "
                     f"wait for a retest rather than chasing")}

    if setup_key == "pullback_50ma":
        early_level, early_what = ema21, "reclaim the 21 EMA"
        full_level, full_what = ema8, "close above the 8 EMA on expanding volume"
    elif setup_key == "pullback_21ema":
        early_level, early_what = ema8, "reclaim the 8 EMA"
        full_level, full_what = (ema8, "close above the 8 EMA on expanding volume")
    elif setup_key in ("breakout", "breakout_retest"):
        early_level, early_what = pbl, "hold the breakout level"
        full_level, full_what = (pbl * 1.01 if pbl else None,
                                 "close 1% clear of the breakout level on volume")
    elif setup_key == "tight_consolidation":
        early_level, early_what = ema8, "reclaim the 8 EMA"
        full_level, full_what = ((f(row.get("R1")) or ema8),
                                 "close above the range high on expanding volume")
    else:                                   # mean reversion
        early_level, early_what = ma50, "a reversal candle off the low"
        full_level, full_what = (ema21, "reclaim the 21 EMA")

    def stage(level, what, needs_volume):
        if level is None or price is None:
            return None
        met = price >= level and (not needs_volume or bool(confirmed)
                                  or has_reversal)
        return {"price": round(level, 2), "condition": what, "met": bool(met),
                "distance_pct": round((level / price - 1) * 100, 1)}

    early = stage(early_level, early_what, needs_volume=False)
    full = stage(full_level, full_what, needs_volume=True)

    # No-chase: an entry this far past the full trigger is a different, worse
    # trade than the one that was analysed.
    chase = None
    if full and full["price"]:
        cap = full["price"] * (1 + atr_pct * MAX_CHASE_ATR / 100.0)
        chase = {"price": round(cap, 2),
                 "note": (f"above ${cap:,.2f} the move has already happened — "
                          f"wait for a retest rather than chasing"),
                 "exceeded": price is not None and price > cap}
    return early, full, chase


def invalidation_for(row, setup_key):
    """The setup thesis failing, which is NOT the hard stop.

    A 50 MA pullback that closes decisively under the 50 MA on heavy volume
    has already failed, whether or not the stop an ATR lower has been hit.
    Waiting for the hard stop there is paying for information you already
    have.
    """
    anchor = {"pullback_21ema": ("the 21 EMA", f(row.get("21EMA"))),
              "pullback_50ma": ("the 50 MA", f(row.get("50MA"))),
              "breakout": ("the breakout level", f(row.get("Prior_Breakout_Level"))),
              "breakout_retest": ("the breakout level",
                                  f(row.get("Prior_Breakout_Level"))),
              "tight_consolidation": ("the range low", f(row.get("S1"))),
              "mean_reversion": ("the low", f(row.get("52W Low")))}.get(setup_key)
    if not anchor or anchor[1] is None:
        return None
    name, level = anchor
    return {"price": round(level, 2),
            "condition": (f"a close below {name} at ${level:,.2f} on "
                          f"above-average volume"),
            "note": ("the thesis has failed at this point even though the "
                     "hard stop sits lower — exit on the close, not the stop")}


# ─────────────────────────────────────────────────────────────────────────────
# STATE, TRIGGER, THESIS
# ─────────────────────────────────────────────────────────────────────────────

# Gates that, when failed, name the state outright. Order matters: a
# breakdown is a more important thing to say than a missing setup, and a
# missing setup more important than a tight path.
def trade_state(row, setup_key, gates, score, chase, full_trigger,
                trend_s, momentum_s) -> tuple[str, str, str]:
    """(state, why, action) — the state machine.

    The score ranks; these decide. BX is the case that forced the split:
    momentum 95, trade quality 73, path Good, setup 0. On score alone it
    read "50 — Avoid", which says the stock is undesirable. What the model
    actually found was a good chart with no pattern to trade today, and
    those need different words.
    """
    failed = {g["name"] for g in gates if g["ok"] is False and g["blocking"]}

    if setup_key == "failed_breakout":
        return "FAILED", "the breakout level was lost", "remove from the list"
    if "Trend" in failed:
        # BREAKDOWN is reserved for price under its 200 MA — a fact off the
        # chart. A trend SCORE of 45 is a mixed structure, and calling that a
        # breakdown (as the first version did for BX) states something the
        # data does not: BX was above its 200 MA the whole time.
        price, ma200 = f(row.get("Current Price")), f(row.get("200MA"))
        broken = price is not None and ma200 is not None and price < ma200
        if broken:
            return ("BREAKDOWN", "price is below its 200 MA", "avoid long")
        return ("NO TRADE",
                f"trend {trend_s:.0f} is below the {MIN_TREND_SCORE:.0f} a long "
                f"entry needs, though the 200 MA still holds"
                if trend_s is not None else "trend not measured",
                "wait for the trend")
    if "Market" in failed:
        return ("NO TRADE", "the market regime is too weak for a new long",
                "wait for the tape")
    if "Setup" in failed:
        # A good chart with no pattern is a watchlist name, not an avoid.
        good_chart = ((trend_s or 0) >= 60 and (momentum_s or 0) >= 60)
        if good_chart:
            return ("WATCH",
                    "trend and momentum are there but no pattern has formed",
                    "wait for a setup")
        return "NO SETUP", "no actionable pattern at this price", "no trade"
    if chase and chase.get("exceeded"):
        return ("MISSED",
                f"price is past the no-chase cap at ${chase['price']:,.2f} — "
                f"wait for a retest", "do not chase")

    pct8 = f(row.get("Pct_vs_8EMA"))
    if pct8 is not None and pct8 > EXTENDED_PCT:
        return ("EXTENDED",
                f"{pct8:+.1f}% above the 8 EMA — the entry is chasing and the "
                f"stop has to be too wide", "do not chase")
    if "Stop" in failed:
        return ("NO TRADE",
                "the structural stop is too wide for a swing", "no trade")

    # Any remaining blocking failure (in practice the Path gate) caps the
    # state at APPROACHING. Without this, 77 names read READY while only 59
    # passed every gate — a trigger firing on a path with under 1R to the
    # first resistance is still not a trade to take.
    blocked = sorted(failed)
    if blocked:
        return ("APPROACHING",
                f"{' and '.join(blocked).lower()} gate not passed — "
                f"{[g['detail'] for g in gates if g['name'] == blocked[0]][0]}",
                "wait")

    if full_trigger and full_trigger.get("met"):
        return "READY", "the full trigger has fired", "enter"
    if score is None:
        return "APPROACHING", "not enough data to score the setup", "wait"

    state, action = next((st, a) for floor, st, a in STATE_BANDS
                         if score >= floor)
    # Every gate passed but the trigger has not fired: READY by band still
    # means "enter IF the trigger fires", never "enter now".
    if state == "READY":
        return "NEAR READY", f"{score}/100 — gates pass, trigger has not fired", \
            "wait for the trigger"
    return state, f"{score}/100 — {action}", action


def stock_view(trend_s, momentum_s) -> tuple[str, str]:
    """The CHART's verdict, separate from whether a trade exists today.

    "No setup" and "bad stock" are different findings and the old output
    collapsed them into one word.
    """
    if trend_s is None or momentum_s is None:
        return "⚪", "not scored"
    avg = (trend_s + momentum_s) / 2
    if avg >= 75:
        return "🟢", "strong chart"
    if avg >= 60:
        return "🟢", "constructive"
    if avg >= 45:
        return "🟡", "mixed"
    return "🔴", "weak chart"


def setup_state(row, setup_key, vol_kind, path, score=None,
                chase=None) -> tuple[str, str]:
    """Where the setup is in its life.

    The degree states come straight from the score bands, so "69/100" and
    "APPROACHING" can never disagree — previously the two were computed by
    different logic and a low score could still read READY.

    Event states override the bands, because they describe something that
    HAPPENED rather than how good the setup is: a failed breakout is not a
    weak setup, it is a different one, and a stock 9% above its 8 EMA is
    unenterable at any score.
    """
    pct8 = f(row.get("Pct_vs_8EMA"))
    reversal = s(row.get("Reversal_Candle"))
    confirmed = b(row.get("Volume_Confirmation"))
    has_reversal = bool(reversal and reversal.lower() not in ("none", "no", "-"))

    if setup_key == "none":
        return "NO SETUP", "no actionable pattern at this price"
    if setup_key == "failed_breakout":
        return "FAILED", "the breakout level was lost"
    if chase and chase.get("exceeded"):
        return "MISSED", (f"price is past the no-chase cap at "
                          f"${chase['price']:,.2f} — wait for a retest")
    if pct8 is not None and pct8 > EXTENDED_PCT:
        return "EXTENDED", (f"{pct8:+.1f}% above the 8 EMA — the entry is "
                            f"chasing and the stop has to be too wide")
    if has_reversal and confirmed:
        return "TRIGGERED", "reversal candle on confirming volume"
    if score is None:
        return "APPROACHING", "not enough data to score the setup"

    state, action = next((st, a) for floor, st, a in STATE_BANDS
                         if score >= floor)
    return state, f"{score}/100 — {action}"


def entry_trigger(row, setup_key, state) -> str | None:
    """The specific condition that would make this actionable. A state of
    APPROACHING with no trigger named is just a shrug."""
    if state in ("NO SETUP", "FAILED"):
        return None
    ema21, ema8 = f(row.get("21EMA")), f(row.get("8EMA"))
    ma50 = f(row.get("50MA"))
    pbl = f(row.get("Prior_Breakout_Level"))
    if state == "EXTENDED":
        return (f"Wait for a pullback toward the 21 EMA"
                + (f" at ${ema21:,.2f}" if ema21 else "") +
                " rather than entering here.")
    if setup_key in ("pullback_21ema", "pullback_50ma"):
        level = ema21 if setup_key == "pullback_21ema" else ma50
        bits = []
        if ema8:
            bits.append(f"a close back above the 8 EMA at ${ema8:,.2f}")
        if level:
            bits.append(f"holding ${level:,.2f}")
        return ("Enter on " + " with ".join(bits) +
                ", on volume expanding off the low." if bits else None)
    if setup_key == "breakout_retest" and pbl:
        return (f"Enter while ${pbl:,.2f} holds; a close below it invalidates "
                f"the retest.")
    if setup_key == "breakout" and pbl:
        return f"Entered on the break of ${pbl:,.2f}; stop back inside the base."
    if setup_key == "tight_consolidation" and ema21:
        return (f"Enter on the range expanding — a close above the recent high "
                f"with volume above average.")
    if setup_key == "mean_reversion":
        return ("Counter-trend: only on a reversal candle with volume, and "
                "sized down.")
    return None


def thesis(row, setup_key, trend, momentum, volume_detail, path, state) -> str:
    """One sentence a human would say, instead of a list of levels.

    "Strong cluster — 3 levels within 2%" is a measurement. "The long-term
    trend is intact but the pullback has no momentum or volume behind it, and
    resistance is stacked within 4%" is the finding.
    """
    label = SETUPS.get(setup_key, SETUPS["none"])[0]
    bits = []

    hits, misses = trend.get("hits", []), trend.get("misses", [])
    if "price above the 200 MA" in hits and "200 MA rising" in hits:
        bits.append("The long-term trend is intact")
    elif "price above the 200 MA" in hits:
        bits.append("Price holds the 200 MA but the long-term slope is flat")
    else:
        bits.append("Price is below its 200 MA")

    if setup_key == "none":
        bits.append("and there is no actionable pattern here")
    else:
        short = [m for m in misses if "50 MA" in m or "21 EMA" in m or "higher" in m]
        # "pullback to 50 ma" — the acronyms have to survive .lower().
        pretty = (label.lower().replace(" ma", " MA").replace(" ema", " EMA")
                  .replace("21 EMA", "21 EMA"))
        bits.append(f"and the setup is a {pretty}"
                    + (f" — though {short[0]} is not true" if short else ""))

    if momentum is not None and momentum < 50:
        bits.append("momentum is weak")
    elif momentum is not None and momentum >= 75:
        bits.append("momentum is behind it")

    if "DOWN day" in (volume_detail or ""):
        bits.append("and the recent volume came on selling")
    elif "distribution days" in (volume_detail or ""):
        bits.append("and distribution is showing in the volume")
    elif "contracting" in (volume_detail or ""):
        bits.append("with volume contracting into the pullback")

    if path.get("to_clear"):
        names = ", ".join(f'{c["name"]} ${c["price"]:,.2f}'
                          for c in path["to_clear"])
        bits.append(f"and it has to clear {names} before the entry trigger is "
                    f"even reached")
    if path.get("quality") == "Poor" and path.get("levels"):
        lvls = path["levels"]
        if len(lvls) == 1:
            bits.append(f"and the only level overhead sits "
                        f"{lvls[0]['move_pct']:.0f}% away, so there is little "
                        f"room before it")
        else:
            bits.append(f"and resistance is stacked from "
                        f"{lvls[0]['move_pct']:.0f}% to "
                        f"{lvls[-1]['move_pct']:.0f}% overhead, so the "
                        f"headline target is not a clean path")

    sentence = ", ".join(bits)
    # Tidy the joins rather than assembling two half-sentences: an earlier
    # version produced "momentum is weak. with volume contracting".
    sentence = sentence.replace(", and ", " and ").replace(" and and ", " and ")
    return sentence.rstrip(". ") + "."


# ─────────────────────────────────────────────────────────────────────────────

def _stop_for(row, setup_key, entry=None):
    """The stop, chosen by the same tiers the position-sizing engine uses.

    Tested level, then structural, then a volatility band — never the other
    way round. The two panels reported different stops for the same stock
    ($255.03 against $239.50) because this function reached for the setup's
    own anchor while the sizing engine reached for whatever the market had
    actually defended. Same ranking now, so they can only differ when they
    are genuinely pricing different entries.
    """
    price = f(row.get("Current Price"))
    ref = entry or price
    atr_pct = f(row.get("ATR_Pct")) or 2.0
    if ref is None:
        return None, "no price to measure a stop from"

    # 1 — a level the market has defended.
    shelf, shelf_note = tested_support(row, ref)
    if shelf:
        return (round(shelf * (1 - atr_pct / 100.0), 2),
                f"1 ATR below the tested shelf ${shelf:,.2f} ({shelf_note})")

    # 2 — the NEAREST structural level below the entry, not the setup's own
    # anchor. Reaching for the anchor put MSFT's swing stop 18% down at the
    # 50 MA while the sizing engine used a prior breakout 4.4% below — a
    # swing stop wider than the long-term one, which is backwards. A wider
    # stop is never a better one: it buys fewer shares for the same
    # conviction, which is the sizing engine's rule and now this one's.
    below = [(lvl, name) for name, lvl in
             (("the 21 EMA", f(row.get("21EMA"))),
              ("the 50 MA", f(row.get("50MA"))),
              ("the 200 MA", f(row.get("200MA"))),
              ("the breakout level", f(row.get("Prior_Breakout_Level"))))
             if lvl is not None and lvl < ref
             # Inside half an ATR of the entry a "stop" is ordinary noise.
             and (ref - lvl) / ref * 100 >= atr_pct * 0.5]
    if below:
        anchor, anchor_name = max(below, key=lambda x: x[0])
        return (round(anchor * (1 - atr_pct * 0.25 / 100.0), 2),
                f"a quarter ATR below {anchor_name} ${anchor:,.2f}")

    # 3 — volatility band, said to be one.
    return (round(ref * (1 - atr_pct * 2 / 100.0), 2),
            f"2 ATR below ${ref:,.2f} — no structural level underneath")


def size_swing(entry, stop, risk_pct, settings) -> dict | None:
    """The swing trade sized on the SAME account and the SAME arithmetic the
    long-term panel uses — `position_sizing.size_position`.

    Imported rather than reimplemented: shares-from-risk is arithmetic with
    no market opinion in it, exactly like `blend`, and two copies of it is
    how the two panels came to disagree in the first place. What differs is
    the input — this passes the SWING's risk budget (grade x regime), not
    the account's 2% ceiling — so the share counts differ for a stated
    reason rather than an accidental one.
    """
    if not (entry and stop and entry > stop and settings and risk_pct):
        return None
    from stockanalysis.core.longterm import position_sizing as PS
    # normalize_settings(), not a dict copy with a key set: `max_dollar_risk`
    # is DERIVED from capital x risk_pct, so overwriting risk_pct alone left
    # the dollar budget at the account's 2% and the swing sized itself on the
    # long-term risk after all.
    swing_settings = PS.normalize_settings(
        dict(settings, risk_pct=risk_pct))
    sized = PS.size_position(entry, stop, swing_settings)
    sized["risk_pct_used"] = risk_pct
    sized["note"] = (f"sized on {risk_pct:.2f}% risk — the swing budget for "
                     f"this grade and tape, not the account's "
                     f"{settings.get('risk_per_trade_pct', 2):g}% ceiling")
    return sized


def evaluate(row: dict, regime: str | None = None,
             settings: dict | None = None) -> dict:
    """The swing verdict for one scan row. Reads no long-term output."""
    price = f(row.get("Current Price"))
    setup_key, setup_why = classify_setup(row)
    entry, entry_note = planned_entry(row, setup_key)
    stop, stop_note = _stop_for(row, setup_key, entry)
    path = resistance_path(row, entry, stop, price)

    early, full, chase = triggers_for(row, setup_key, entry)

    market_s, market_d = market_component(regime)
    trend_s, trend_d, trend_parts = trend_component(row)
    setup_s, setup_d = setup_component(row, setup_key)
    mom_s, mom_d = momentum_component(row)
    vol_s, vol_d, vol_kind = volume_component(row)
    trig_s, trig_d = trigger_component(row, early, full, chase)
    trade_s, trade_d, trade_parts = trade_component(path, row, entry, stop)

    # Setup 25 -> 20 and Volume 15 -> 10 to make room for Trigger quality: a
    # setup can be excellent while the trigger it offers is weak, and the
    # previous weights had no way to say so.
    scored = blend([
        ("Market regime", 15, market_s, market_d),
        ("Trend", 20, trend_s, trend_d),
        ("Setup", 20, setup_s, setup_d),
        ("Momentum", 15, mom_s, mom_d),
        ("Volume", 10, vol_s, vol_d),
        ("Trigger quality", 10, trig_s, trig_d),
        ("Trade quality", 10, trade_s, trade_d),
    ])

    score = scored["score"]
    grade = next((g for floor, g in GRADES if score is not None and score >= floor),
                 "No trade") if score is not None else None

    stop_pct = (None if not (entry and stop and entry > stop)
                else round((entry - stop) / entry * 100, 1))
    # The stop is PLACED relative to the level it sits under, but it is RISKED
    # relative to the entry, and those are different numbers whenever the
    # entry is a trigger above today's price: ACN read "4.5% (1 ATR) below
    # $152.20" beside "17.9% below the $176.89 entry" with no way to see that
    # both were true. The note now carries both references.
    if stop_pct is not None and entry:
        stop_note = (f"{stop_note}; {stop_pct:.1f}% below the ${entry:,.2f} "
                     f"entry")
    gates = evaluate_gates(setup_key, setup_s, trend_s, market_s, path,
                           stop_pct, chase, full)
    state, state_why, action = trade_state(row, setup_key, gates, score, chase,
                                           full, trend_s, mom_s)
    chart_icon, chart_label = stock_view(trend_s, mom_s)
    lo_days, hi_days, review = TIME_STOPS.get(setup_key, (3, 15, 7))

    return {
        "ticker": row.get("Ticker"),
        "score": score,
        "grade": grade,
        "coverage": scored["coverage"],
        "components": scored["components"],
        "missing": scored["missing"],
        "setup": setup_key,
        "setup_label": SETUPS.get(setup_key, SETUPS["none"])[0],
        "setup_why": setup_why,
        "state": state,
        "state_why": state_why,
        "action": action,
        # The chart's own verdict, which is NOT the trade's. A strong chart
        # with no pattern is a watchlist name; the old output called it Avoid.
        "stock_view": {"icon": chart_icon, "label": chart_label},
        "gates": gates,
        # Advisory checks (volume confirmation) are reported but do not make
        # a setup ineligible — an unconfirmed trigger is APPROACHING, not
        # rejected, and counting it as a failed gate made every pullback
        # awaiting confirmation look blocked.
        "eligible": all(g["ok"] is not False for g in gates if g["blocking"]),
        "stop_pct": stop_pct,
        "stop_efficiency": stop_efficiency(stop_pct),
        "first_r": (path or {}).get("first_r"),
        "first_r_grade": first_resistance_grade((path or {}).get("first_r")),
        "targets": _targets(row, entry, stop, path),
        "trigger": entry_trigger(row, setup_key, state),
        # The execution layer: two stages, a chase cap and a thesis-failure
        # level that is deliberately not the hard stop.
        "triggers": {"early": early, "full": full, "max_chase": chase},
        "invalidation": invalidation_for(row, setup_key),
        "price": price,
        "entry": entry,
        "entry_note": entry_note,
        "stop": stop,
        "stop_note": stop_note,
        "path": path,
        "path_components": trade_parts.get("components") or [],
        "volume_kind": vol_kind,
        "trend": trend_parts,
        # A stagnating swing must not become an investment by default.
        "time_stop": {"min_days": lo_days, "max_days": hi_days,
                      "review_days": review,
                      "note": (f"expect {lo_days}-{hi_days} trading days; if it "
                               f"has not moved in your favour within {review}, "
                               f"cut it rather than letting it become a hold")},
        "risk_pct": risk_for(grade, market_s),
        "sizing": size_swing(entry, stop,
                             risk_for(grade, market_s).get("max"), settings),
        "thesis": thesis(row, setup_key, trend_parts, mom_s, vol_d, path, state),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TARGETS — a ladder, not one number
# ─────────────────────────────────────────────────────────────────────────────

def _targets(row, entry, stop, path) -> list[dict]:
    """T1 nearest resistance, T2 the next, T3 a measured move, T4 the 52-week
    high.

    The 52-week high was being used as THE target, which flatters every
    trade: it is the furthest thing on the chart, so it always shows the
    biggest R. It belongs last. T3 is the breakout level plus the depth of
    the base beneath it — a measured move — and is only offered when both
    halves exist.
    """
    if not (entry and stop and entry > stop):
        return []
    risk = entry - stop
    out, seen = [], set()

    def add(label, price, basis):
        if price is None or price <= entry:
            return
        key = round(price, 2)
        if key in seen:
            return
        seen.add(key)
        out.append({"label": label, "price": round(price, 2), "basis": basis,
                    "r": round((price - entry) / risk, 2),
                    "move_pct": round((price / entry - 1) * 100, 1)})

    levels = (path or {}).get("levels") or []
    if levels:
        add("T1", levels[0]["price"], levels[0]["name"])
    if len(levels) > 1:
        add("T2", levels[1]["price"], levels[1]["name"])

    pbl, s1 = f(row.get("Prior_Breakout_Level")), f(row.get("S1"))
    if pbl and s1 and pbl > s1:
        add("T3", pbl + (pbl - s1), "measured move from the base")
    add("T4", f(row.get("52W High")), "52-week high")
    return out
