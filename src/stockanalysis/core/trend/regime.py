"""
regime.py — one primary regime per name, and the sub-verdicts behind it
=======================================================================
Four independent readings (long-term, alignment, momentum, pullback,
reversal) and then a single ladder that picks the one primary label.

Why a ladder and not a score
----------------------------
Every name must land in exactly one of fifteen states, and those states
overlap heavily by construction — a stock in a healthy pullback is also,
simultaneously, in a long-term bull. A weighted score cannot express
"which of these true things is the one worth saying"; an ordered ladder
can, and it can be read and argued with line by line.

The order is: SEVERITY first, then SPECIFICITY.

    A breakdown outranks everything, because being wrong about a
    breakdown costs more than being wrong about anything else here.

    A deviation from the base regime outranks the base regime. If a name
    is in a long-term bull AND pulling back, "pulling back" is the useful
    sentence — the bull part is already reported in LONG_TERM_TREND and
    saying it twice buys nothing.

The one judgement call worth naming
-----------------------------------
STRONG_LONG_TERM_BULL and STRONG_MOMENTUM are both true of most leaders,
and the spec ranks them 1 and 2 without saying which wins. They are split
here on the 200 SMA's own direction, which is the only condition that
appears in one and not the other:

    STRONG_LONG_TERM_BULL   full stack AND the 200 SMA is rising
    STRONG_MOMENTUM         full stack, fast averages rising, but the
                            200 SMA has not turned up yet

That makes them mutually exclusive rather than a tie to be broken, and it
puts a young recovery — right stack, regime not yet confirmed — in a
different bucket from an established leader, which is a distinction worth
having.

MOMENTUM_ACCELERATION still outranks both. It is a time-sensitive state,
not a description of structure, and burying it under a label that will
still be true next month would waste it.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f
from stockanalysis.core.trend import indicators as I

# ── Long-term regime: the mandatory 2x2 of section 8 ────────────────────
BULLISH_LT = "BULLISH_LONG_TERM_REGIME"
WEAK_BULL_LT = "RECOVERY_WEAK_BULL_REGIME"
ACCUMULATION_LT = "PULLBACK_POSSIBLE_ACCUMULATION"
BEARISH_LT = "BEARISH_LONG_TERM_REGIME"
UNKNOWN_LT = "LONG_TERM_UNKNOWN"

REGIMES = (
    "STRONG_LONG_TERM_BULL", "STRONG_MOMENTUM", "MOMENTUM_ACCELERATION",
    "HEALTHY_MOMENTUM_PULLBACK", "MOMENTUM_COOLING",
    "DEEP_PULLBACK_IN_BULL_TREND", "LONG_TERM_BULL_WEAKENING",
    "EARLY_BULLISH_REVERSAL", "CONFIRMED_BULLISH_REVERSAL",
    "MIXED_TRANSITION", "EARLY_BEARISH_REVERSAL",
    "CONFIRMED_BEARISH_REVERSAL", "LONG_TERM_BEAR_WEAKENING",
    "STRONG_LONG_TERM_BEAR", "LONG_TERM_BREAKDOWN",
)

BULLISH_REGIMES = frozenset(REGIMES[:6]) | {"CONFIRMED_BULLISH_REVERSAL",
                                            "EARLY_BULLISH_REVERSAL"}
BEARISH_REGIMES = frozenset(REGIMES[10:])

# How close to the 200 SMA counts as "approaching" it, in ATR. A percentage
# would mean something different on every name; this does not.
NEAR_200_ATR = 1.5
# Closes below the 200 SMA, out of the last ten, that make a break a
# breakdown rather than a wick through it.
BREAKDOWN_CLOSES = 5
BREAKDOWN_WINDOW = 10
# Closes on the other side of the 200 SMA, inside the last 60 sessions, that
# make a prior regime real rather than a single wick through the average.
PRIOR_STATE_CLOSES = 3


def _s(ind, key):
    return (ind.get("slope_state") or {}).get(key, "unknown")


def _lvl(ind, key):
    return (ind.get("ma") or {}).get(key)


def _atr_dist(ind, key):
    return (ind.get("dist_atr") or {}).get(key)


def _rising(ind, key):
    return _s(ind, key) == "rising"


def _falling(ind, key):
    return _s(ind, key) == "falling"


def _above(ind, key):
    """price > MA, or None when either side is missing."""
    p, lvl = f(ind.get("price")), _lvl(ind, key)
    return None if (p is None or lvl is None) else p > lvl


def long_term(ind: dict) -> dict:
    """The 2x2 that section 8 makes mandatory: price position AND 200 slope.

    'Price above the 200-day' on its own is the single most over-read fact
    in technical analysis. Above a rising 200 is an uptrend; above a falling
    200 is a bounce inside a downtrend, and they resolve differently often
    enough that collapsing them loses the whole point of the average.

    A flat 200 SMA is folded into the weaker of the two readings on each
    side of price — flat is not yet rising, and treating it as rising would
    promote every basing name into a bull regime on no evidence.
    """
    above = _above(ind, "SMA200")
    state = _s(ind, "SMA200")
    if above is None or state == "unknown":
        return {"regime": UNKNOWN_LT, "label": None,
                "detail": "no 200 SMA yet — fewer than 200 bars of history"}
    if above and state == "rising":
        regime, detail = BULLISH_LT, "price above a rising 200 SMA"
    elif above:
        regime = WEAK_BULL_LT
        detail = f"price above a {state} 200 SMA — a bounce, not yet a trend"
    elif state == "rising":
        regime = ACCUMULATION_LT
        detail = "price below a rising 200 SMA — pullback or accumulation"
    else:
        regime = BEARISH_LT
        detail = f"price below a {state} 200 SMA"

    # The named long-term labels of section 2, reported alongside the 2x2.
    fifty_over = (ind.get("cross") or {}).get("sma50_over_sma200")
    if regime == BULLISH_LT and fifty_over and _rising(ind, "SMA50"):
        label = "STRONG_LONG_TERM_BULL"
    elif regime == BULLISH_LT:
        label = "LONG_TERM_BULL_WEAKENING"
    elif regime == BEARISH_LT and fifty_over is False and _falling(ind, "SMA50"):
        label = "STRONG_LONG_TERM_BEAR"
    elif regime in (ACCUMULATION_LT, BEARISH_LT) and _rising(ind, "SMA50"):
        # Section 2 calls this LONG_TERM_BEAR_RECOVERY; the canonical list in
        # section 12 calls the same state LONG_TERM_BEAR_WEAKENING. One state,
        # so one label — the section 12 spelling, since that is the list every
        # name must land in.
        label = "LONG_TERM_BEAR_WEAKENING"
    else:
        label = None
    return {"regime": regime, "label": label, "detail": detail}


def momentum(ind: dict) -> dict:
    """Short-term momentum, read independently of the long-term regime."""
    price = f(ind.get("price"))
    above8 = _above(ind, "EMA8")
    e8, e21 = _lvl(ind, "EMA8"), _lvl(ind, "EMA21")
    m50, m200 = _lvl(ind, "SMA50"), _lvl(ind, "SMA200")
    stack_full = ind.get("alignment") == "FULL_BULLISH_ALIGNMENT"
    fast_rising = _rising(ind, "EMA8") and _rising(ind, "EMA21")

    # "Rising rapidly" is twice the bar an average must clear to count as
    # rising at all — a threshold in ATR, so it means the same thing on a
    # sleepy dividend name and a high-beta one.
    e8_slope = (ind.get("slope") or {}).get("EMA8_5d_atr5")
    rapid = (e8_slope is not None
             and e8_slope >= 2 * I.RISING_ATR5["EMA8"])

    accelerating = bool(
        above8 and rapid and e8 and e21 and e8 > e21
        and _rising(ind, "EMA21")
        and ind.get("at_20d_high")
        and ind.get("vol_expanding"))

    strong = bool(stack_full and fast_rising and _rising(ind, "SMA50"))

    cooling = bool(
        above8 is False
        and _above(ind, "SMA50")
        and e21 and m50 and e21 > m50
        and not _rising(ind, "EMA8")
        and _s(ind, "EMA21") in ("rising", "flat"))

    state = ("MOMENTUM_ACCELERATION" if accelerating else
             "STRONG_MOMENTUM" if strong else
             "MOMENTUM_COOLING" if cooling else
             "NEGATIVE_MOMENTUM" if above8 is False and _falling(ind, "EMA21")
             else "NEUTRAL_MOMENTUM")

    # Section 4's confirmations. They raise confidence and feed the score;
    # they are never gates, because a real trend that has not yet attracted
    # volume is still a real trend.
    rsi, adx, rvol = f(ind.get("rsi")), f(ind.get("adx")), f(ind.get("rvol"))
    confirms = {
        "rsi_in_band": (55 <= rsi <= 75) if rsi is not None else None,
        "adx_trending": (adx > 20) if adx is not None else None,
        "rvol_above_1": (rvol > 1) if rvol is not None else None,
        "structure_bullish": ind.get("structure") == I.HH_HL,
    }
    return {"state": state, "confirms": confirms,
            "accelerating": accelerating, "rapid_8ema": rapid,
            "price": price, "m200": m200}


def pullback(ind: dict) -> dict:
    """Healthy pullback, deep pullback, or neither.

    The two are separated by which average price has given up, not by how
    much it has fallen. Below the 8 EMA while the 21 still leads the 50 is
    a trend breathing; below the 50 with the 21 underneath it is a trend
    that has changed shape, and calling both "a dip" is how dip-buying
    stops working.
    """
    above50, above200 = _above(ind, "SMA50"), _above(ind, "SMA200")
    e21, m50 = _lvl(ind, "EMA21"), _lvl(ind, "SMA50")
    below8 = _above(ind, "EMA8") is False
    d8, d21 = _atr_dist(ind, "EMA8"), _atr_dist(ind, "EMA21")
    # "Near" the fast averages: within 1.5 ATR below the 21 EMA. Beyond that
    # price is not resting on the trend, it has left it.
    near_fast = (d21 is not None and -1.5 <= d21 <= 0.5) or \
                (d8 is not None and -1.0 <= d8 <= 0.5)
    # Distribution check: heavy volume on the down days turns a pullback
    # into supply. None (no red days in the window) is not a failure.
    dv = f(ind.get("down_vol_ratio"))
    quiet_decline = dv is None or dv <= 1.25

    healthy = bool(
        above50 and above200 and _rising(ind, "SMA50")
        and e21 and m50 and e21 > m50
        and below8 and near_fast
        and _s(ind, "EMA21") in ("rising", "flat")
        and quiet_decline)

    deep = bool(
        above200 and above50 is False
        and e21 and m50 and e21 < m50
        and _rising(ind, "SMA200"))

    status = ("HEALTHY_MOMENTUM_PULLBACK" if healthy else
              "DEEP_PULLBACK_IN_BULL_TREND" if deep else
              "SHALLOW_PULLBACK" if below8 and above50 else "NONE")
    return {"status": status, "healthy": healthy, "deep": deep,
            "near_fast": near_fast, "quiet_decline": quiet_decline}


def reversal(ind: dict) -> dict:
    """Trend change detected before the 200 SMA has any opinion about it.

    Volume is treated as confirming rather than required. A reversal on
    light volume is a weaker reversal, not a non-event, and requiring the
    volume would mean never seeing one until it was already priced.
    """
    cross = ind.get("cross") or {}
    e8_over_21 = cross.get("ema8_over_ema21")
    above8, above50 = _above(ind, "EMA8"), _above(ind, "SMA50")
    e21, m50 = _lvl(ind, "EMA21"), _lvl(ind, "SMA50")
    d200 = _atr_dist(ind, "SMA200")
    near_200 = d200 is not None and abs(d200) <= NEAR_200_ATR
    vol_up = bool(ind.get("vol_expanding"))

    # A reversal needs something to reverse from. Without these two clauses
    # every leader in a year-long uptrend reports CONFIRMED_BULLISH_REVERSAL
    # on every pullback that ends, and every name in a year-long downtrend
    # reports the bearish twin — which makes both columns unreadable.
    # None means "not enough history to know", and does not block: a young
    # name is not asserted to have been strong or weak either way.
    below60 = ind.get("closes_below_200_60d")
    above60 = ind.get("closes_above_200_60d")
    had_weakness = below60 is None or below60 >= PRIOR_STATE_CLOSES
    had_strength = above60 is None or above60 >= PRIOR_STATE_CLOSES

    early_bull = bool(
        above8 and e8_over_21 and above50
        and _s(ind, "EMA21") in ("rising", "flat")
        and cross.get("recent_bull_cross")
        and had_weakness)

    confirmed_bull = bool(
        above50 and e8_over_21 and e21 and m50 and e21 > m50
        and _rising(ind, "SMA50")
        and (_above(ind, "SMA200") or near_200)
        and had_weakness)

    early_bear = bool(
        above8 is False and e8_over_21 is False and above50 is False
        and _s(ind, "EMA21") in ("falling", "flat")
        and cross.get("recent_bear_cross")
        and had_strength)

    confirmed_bear = bool(
        above50 is False and e8_over_21 is False
        and e21 and m50 and e21 < m50
        and _falling(ind, "SMA50")
        and (_above(ind, "SMA200") is False or near_200)
        and had_strength)

    status = ("CONFIRMED_BEARISH_REVERSAL" if confirmed_bear else
              "EARLY_BEARISH_REVERSAL" if early_bear else
              "CONFIRMED_BULLISH_REVERSAL" if confirmed_bull else
              "EARLY_BULLISH_REVERSAL" if early_bull else "NONE")
    return {"status": status, "early_bull": early_bull,
            "confirmed_bull": confirmed_bull, "early_bear": early_bear,
            "confirmed_bear": confirmed_bear, "volume_confirms": vol_up,
            "near_200": near_200}


def _breakdown(ind: dict, series_close=None) -> bool:
    """Price below a falling 200 SMA with the 50 below it and falling, and
    enough closes below the average to rule out a single wick through it."""
    if not (_above(ind, "SMA200") is False and _falling(ind, "SMA200")
            and (ind.get("cross") or {}).get("sma50_over_sma200") is False
            and _falling(ind, "SMA50")):
        return False
    closes_below = ind.get("closes_below_200_10d")
    if closes_below is None:
        return True          # structure says breakdown; count unavailable
    return closes_below >= BREAKDOWN_CLOSES


def classify(ind: dict) -> dict:
    """The one primary regime, plus every sub-verdict that produced it."""
    if not ind:
        # Same shape as a real verdict, not a subset. A caller reading
        # verdict["long_term_trend"] must not have to know whether the frame
        # was empty — an empty frame is a normal outcome of a scan, and a
        # KeyError here would take down the whole batch with it.
        return {"regime": "MIXED_TRANSITION", "reason": "no data",
                "long_term": {"regime": UNKNOWN_LT, "label": None,
                              "detail": "no data"},
                "momentum": {"state": "NEUTRAL_MOMENTUM", "confirms": {},
                             "accelerating": False, "rapid_8ema": False},
                "pullback": {"status": "NONE", "healthy": False, "deep": False},
                "reversal": {"status": "NONE", "early_bull": False,
                             "confirmed_bull": False, "early_bear": False,
                             "confirmed_bear": False},
                "long_term_trend": "Unknown", "intermediate_trend": "Unknown",
                "short_term_trend": "Unknown"}

    lt = long_term(ind)
    mo = momentum(ind)
    pb = pullback(ind)
    rv = reversal(ind)

    fifty_over_200 = (ind.get("cross") or {}).get("sma50_over_sma200")
    stack_full = ind.get("alignment") == "FULL_BULLISH_ALIGNMENT"
    d200 = _atr_dist(ind, "SMA200")
    well_above_200 = d200 is not None and d200 > NEAR_200_ATR

    # ── The ladder. First match wins; order is severity, then specificity ──
    rules = [
        ("LONG_TERM_BREAKDOWN",
         _breakdown(ind),
         "price below a falling 200 SMA with the 50 below it and falling"),

        ("STRONG_LONG_TERM_BEAR",
         lt["regime"] == BEARISH_LT and fifty_over_200 is False
         and _falling(ind, "SMA50"),
         "bearish stack under a falling 200 SMA"),

        # A confirmed bearish reversal must be near or through the 200 SMA.
        # Without that clause it would swallow every deep pullback in an
        # intact bull trend, which is the exact error section 5 forbids.
        ("CONFIRMED_BEARISH_REVERSAL",
         rv["confirmed_bear"] and not well_above_200,
         "price under a falling 50 SMA with 8 EMA < 21 EMA < 50 SMA, at the 200"),

        ("EARLY_BEARISH_REVERSAL",
         rv["early_bear"],
         "lost the 8 EMA and the 50 SMA on a fresh bearish EMA cross"),

        ("MOMENTUM_ACCELERATION",
         mo["state"] == "MOMENTUM_ACCELERATION",
         "8 EMA rising rapidly into a 20-day high on expanding volume"),

        ("HEALTHY_MOMENTUM_PULLBACK",
         pb["healthy"],
         "below the 8 EMA but resting on a rising trend, decline on quiet volume"),

        ("MOMENTUM_COOLING",
         mo["state"] == "MOMENTUM_COOLING",
         "lost the 8 EMA and it has stopped rising, trend still intact"),

        ("DEEP_PULLBACK_IN_BULL_TREND",
         pb["deep"],
         "below the 50 SMA but above a rising 200 SMA"),

        ("CONFIRMED_BULLISH_REVERSAL",
         rv["confirmed_bull"] and lt["regime"] != BULLISH_LT,
         "reclaimed the 50 SMA with a rising stack, at the 200"),

        ("EARLY_BULLISH_REVERSAL",
         rv["early_bull"] and lt["regime"] != BULLISH_LT,
         "reclaimed the 8 EMA and 50 SMA on a fresh bullish EMA cross"),

        ("LONG_TERM_BEAR_WEAKENING",
         lt["label"] == "LONG_TERM_BEAR_WEAKENING",
         "still under the 200 SMA but the 50 SMA has turned up"),

        # Split from STRONG_MOMENTUM on the 200 SMA's own direction — see
        # the module docstring. Mutually exclusive by construction.
        ("STRONG_LONG_TERM_BULL",
         lt["regime"] == BULLISH_LT and fifty_over_200
         and _rising(ind, "SMA50"),
         "price above a rising 200 SMA with the 50 above it and rising"),

        ("STRONG_MOMENTUM",
         mo["state"] == "STRONG_MOMENTUM" or (stack_full and fifty_over_200),
         "full bullish stack with the fast averages rising, 200 SMA not yet turned"),

        ("LONG_TERM_BULL_WEAKENING",
         lt["regime"] == BULLISH_LT,
         "above a rising 200 SMA but the intermediate trend has given way"),
    ]

    for label, hit, reason in rules:
        if hit:
            break
    else:
        label = "MIXED_TRANSITION"
        reason = "averages crossing, no ordered structure to read"

    return {
        "regime": label,
        "reason": reason,
        "long_term": lt,
        "momentum": mo,
        "pullback": pb,
        "reversal": rv,
        "long_term_trend": _direction_lt(lt),
        "intermediate_trend": _direction_mid(ind),
        "short_term_trend": _direction_short(ind),
    }


def _direction_lt(lt) -> str:
    return {BULLISH_LT: "Bullish", WEAK_BULL_LT: "Weak bull",
            ACCUMULATION_LT: "Basing", BEARISH_LT: "Bearish",
            UNKNOWN_LT: "Unknown"}[lt["regime"]]


def _direction_mid(ind) -> str:
    """The 50 SMA's verdict: where price sits on it and where it is going."""
    above, state = _above(ind, "SMA50"), _s(ind, "SMA50")
    if above is None or state == "unknown":
        return "Unknown"
    if above and state == "rising":
        return "Bullish"
    if above:
        return "Weakening"
    return "Bearish" if state == "falling" else "Recovering"


def _direction_short(ind) -> str:
    """The 8/21 EMA pair — the trigger and the momentum line."""
    above8 = _above(ind, "EMA8")
    e8_over_21 = (ind.get("cross") or {}).get("ema8_over_ema21")
    if above8 is None or e8_over_21 is None:
        return "Unknown"
    if above8 and e8_over_21:
        return "Bullish"
    if not above8 and not e8_over_21:
        return "Bearish"
    return "Mixed"


def confidence(ind: dict, verdict: dict) -> str:
    """HIGH / MEDIUM / LOW — how much the averages and price agree.

    Section 11's rule: moving averages describe the average of price, and
    price structure describes price. Confidence is what happens when those
    two are asked the same question separately.
    """
    structure = ind.get("structure")
    align = ind.get("alignment")
    regime = verdict["regime"]
    if structure == I.UNKNOWN or align is None:
        return "LOW"
    bullish = regime in BULLISH_REGIMES
    bearish = regime in BEARISH_REGIMES
    agree = ((bullish and structure == I.HH_HL and align == "FULL_BULLISH_ALIGNMENT")
             or (bearish and structure == I.LH_LL
                 and align == "FULL_BEARISH_ALIGNMENT"))
    partial = ((bullish and structure == I.HH_HL)
               or (bearish and structure == I.LH_LL))
    if agree:
        return "HIGH"
    if partial:
        return "MEDIUM"
    return "LOW"
