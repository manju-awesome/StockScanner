"""
entry.py — ENTRY_STATUS, kept deliberately apart from the trend
================================================================
Section 13's rule, and the reason this is a separate module rather than
three more lines at the bottom of regime.py: a trend and an entry are
different questions and they have different answers.

    STRONG_LONG_TERM_BULL + healthy pullback   ->  GOOD_ENTRY
    STRONG_LONG_TERM_BULL + 4 ATR above the 21 ->  DO_NOT_CHASE
    LONG_TERM_BULL + deep pullback             ->  WAIT_FOR_CONFIRMATION

Same trend in all three. Different instruction in all three. Collapsing
them into one field is how a scanner ends up telling you to buy a leader
on the day it is most extended, and how it ends up rejecting that same
leader three weeks later at the exact price you wanted it.

So: the regime never moves because of extension, and the entry status
never overrides the regime. Both are reported, and the sentence you act on
is the pair.

DO_NOT_CHASE is not a sell
--------------------------
It says the trend is intact and the price is wrong today. That is why it
carries the pullback level to wait for rather than just a refusal — the
number a watchlist actually needs.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f
from stockanalysis.core.trend import indicators as I
from stockanalysis.core.trend import regime as R

GOOD_ENTRY = "GOOD_ENTRY"
DO_NOT_CHASE = "DO_NOT_CHASE"
WAIT = "WAIT_FOR_CONFIRMATION"
NO_ENTRY = "NO_ENTRY"
AVOID = "AVOID"
SHORT_SETUP = "SHORT_SETUP"

# Trend Quality below which even a textbook pullback is not an entry: the
# structure underneath it is not good enough to be buying dips into.
MIN_SCORE_FOR_ENTRY = 65


def evaluate(ind: dict, verdict: dict, score: dict) -> dict:
    """ENTRY_STATUS, the level to wait for, and the risk sentence."""
    if not ind:
        return {"status": NO_ENTRY, "detail": "no data", "wait_for": None,
                "risk": "no data"}

    regime = verdict["regime"]
    ext = ind.get("extension_status")
    sc = f(score.get("score"))
    d21 = (ind.get("dist_atr") or {}).get("EMA21")
    ma = ind.get("ma") or {}

    stretched = ext in ("VERY_EXTENDED", "PARABOLIC")
    bullish = regime in R.BULLISH_REGIMES
    bearish = regime in R.BEARISH_REGIMES

    # Extension gates the entry in a bull regime and nothing else. It is
    # checked before the good-entry cases because a perfect structure at a
    # terrible price is exactly the trap section 9 exists to prevent.
    if bullish and stretched:
        status = DO_NOT_CHASE
        detail = (f"trend intact but price is {d21:.1f} ATR above the 21 EMA"
                  if d21 is not None else "trend intact but price is extended")
    elif regime in ("LONG_TERM_BREAKDOWN", "STRONG_LONG_TERM_BEAR",
                    "CONFIRMED_BEARISH_REVERSAL"):
        status, detail = SHORT_SETUP, "bearish structure, no long entry here"
    elif regime == "EARLY_BEARISH_REVERSAL":
        status, detail = AVOID, "short-term structure has broken down"
    elif regime in ("HEALTHY_MOMENTUM_PULLBACK", "MOMENTUM_COOLING"):
        if sc is not None and sc >= MIN_SCORE_FOR_ENTRY:
            status = GOOD_ENTRY
            detail = "pullback into a trend that is still intact"
        else:
            status = WAIT
            detail = f"pullback, but trend quality is only {sc:.0f}" if sc else "pullback, trend quality unknown"
    elif regime in ("STRONG_LONG_TERM_BULL", "STRONG_MOMENTUM",
                    "MOMENTUM_ACCELERATION"):
        if sc is not None and sc >= MIN_SCORE_FOR_ENTRY and ext == "NORMAL":
            status, detail = GOOD_ENTRY, "in trend and not extended"
        elif ext == "EXTENDED":
            status, detail = DO_NOT_CHASE, "in trend but ahead of the 21 EMA"
        else:
            status, detail = WAIT, "structure fine, waiting for a better price"
    elif regime in ("DEEP_PULLBACK_IN_BULL_TREND", "EARLY_BULLISH_REVERSAL",
                    "CONFIRMED_BULLISH_REVERSAL", "LONG_TERM_BEAR_WEAKENING"):
        status = WAIT
        detail = "thesis forming — needs price to confirm before it is an entry"
    elif regime == "LONG_TERM_BULL_WEAKENING":
        status, detail = WAIT, "primary trend intact, intermediate trend is not"
    else:
        status, detail = NO_ENTRY, "no readable structure"

    # Where a chaser should be waiting. The 21 EMA is the reference because
    # it is what a pullback in a live trend actually reverts to; the 8 EMA
    # is too fast to be a plan and the 50 is a different trade.
    wait_for = None
    if status in (DO_NOT_CHASE, WAIT) and bullish:
        wait_for = ma.get("EMA21") or ma.get("SMA50")

    return {"status": status, "detail": detail, "wait_for": wait_for,
            "risk": risk_note(ind, verdict)}


def risk_note(ind: dict, verdict: dict) -> str:
    """The single sentence most likely to be the thing that goes wrong.

    One risk, not a list. A list of five risks is read as no risk at all.
    """
    regime = verdict["regime"]
    d21 = (ind.get("dist_atr") or {}).get("EMA21")
    ma = ind.get("ma") or {}
    s200 = (ind.get("slope") or {}).get("SMA200_5d_atr5")
    structure = ind.get("structure")
    adx = f(ind.get("adx"))

    if s200 is not None and s200 <= I.STRONG_DECLINE_ATR5:
        return "200 SMA is declining hard — every long here is against the regime"
    if regime in ("LONG_TERM_BREAKDOWN", "STRONG_LONG_TERM_BEAR"):
        m50 = ma.get("SMA50")
        return (f"rallies into the falling 50 SMA near {m50:.2f} are supply"
                if m50 else "downtrend intact until the 50 SMA turns")
    if d21 is not None and d21 >= I.EXTENDED_ATR["EMA21"]:
        e21 = ma.get("EMA21")
        return (f"{d21:.1f} ATR above the 21 EMA — a revert to {e21:.2f} is "
                f"an ordinary move, not a thesis break" if e21 else
                "extended from the 21 EMA; mean reversion costs more than the edge")
    if regime == "HEALTHY_MOMENTUM_PULLBACK":
        m50 = ma.get("SMA50")
        return (f"a close below the 50 SMA at {m50:.2f} turns this into a "
                f"deeper pullback" if m50 else "invalidated below the 50 SMA")
    if regime in ("EARLY_BULLISH_REVERSAL", "CONFIRMED_BULLISH_REVERSAL",
                  "LONG_TERM_BEAR_WEAKENING"):
        return "reversal unconfirmed while the 200 SMA is still overhead"
    if structure == I.LH_LL:
        return "price is making lower highs and lower lows regardless of the stack"
    if adx is not None and adx < 20:
        return f"ADX {adx:.0f} — this is drift, not a trend; expect chop"
    if regime == "MIXED_TRANSITION":
        return "averages are crossing; any read here is provisional"
    return "extended if price moves more than 2 ATR above the 21 EMA"
