"""
filters.py — five scanners, not one "bullish" list
===================================================
Section 15's point, which is worth restating because it is the whole
design: a single bullish filter is useless. The names it returns are a
mixture of leaders you cannot buy today because they have run, dips you
can buy today, and reversals that are not confirmed yet — three different
trades with three different risks, ranked against each other as though
they were comparable.

So there are five, each answering one question, each with its own ranking
key, and a name can legitimately appear in more than one.

    A  LONG-TERM LEADERS   what is structurally strongest
    B  MOMENTUM LEADERS    what is moving now
    C  BUY-THE-DIP         what is on sale inside an intact trend
    D  EARLY REVERSAL      what is turning before the 200 SMA notices
    E  BREAKDOWN / SHORT   what is failing

Filter C is the one that earns its keep. It is the only one whose ranking
is by PROXIMITY rather than by strength — the best dip candidate is the
one closest to its 21 EMA, not the one with the highest score, and every
"top ranked bullish name" list gets that exactly backwards.

Each filter is a pure predicate over a classified row (see engine.row()),
so the same code backs the page tabs, the CLI and any future alert.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f


def _ma(row, key):
    return (row.get("ma") or {}).get(key)


def _slope_state(row, key):
    return (row.get("slope_state") or {}).get(key, "unknown")


def _dist_atr(row, key):
    return (row.get("dist_atr") or {}).get(key)


def _rising(row, key):
    return _slope_state(row, key) == "rising"


def _not_falling(row, key):
    return _slope_state(row, key) in ("rising", "flat")


def long_term_leaders(row) -> bool:
    """A: trend score >= 80, above a rising 200, golden-cross structure."""
    sc = f(row.get("score"))
    return bool(
        sc is not None and sc >= 80
        and row.get("above_200")
        and _rising(row, "SMA200")
        and (row.get("cross") or {}).get("sma50_over_sma200"))


def momentum_leaders(row) -> bool:
    """B: the full stack, with the three fast averages all rising."""
    return bool(
        row.get("alignment") == "FULL_BULLISH_ALIGNMENT"
        and _rising(row, "EMA8")
        and _rising(row, "EMA21")
        and _rising(row, "SMA50"))


def buy_the_dip(row) -> bool:
    """C: above a rising 200 and a rising 50, but back at or under the 21 EMA.

    The price window is the filter: at or below the 21 EMA (the pullback
    has happened) and at or above the 50 SMA (it has not gone too far).
    Outside that band this is either a chase or a broken trend.
    """
    sc = f(row.get("score"))
    price, e21, m50 = f(row.get("price")), _ma(row, "EMA21"), _ma(row, "SMA50")
    if None in (price, e21, m50) or sc is None:
        return False
    return bool(
        row.get("above_200") and _rising(row, "SMA200") and _rising(row, "SMA50")
        and price <= e21 and price >= m50
        and sc >= 70
        and row.get("regime") != "LONG_TERM_BREAKDOWN")


# Sessions below the 50 SMA, out of the last thirty, that make a name
# "recovering above" it rather than simply "above" it.
RECOVERY_CLOSES = 3


def early_reversal(row) -> bool:
    """D: RECOVERING above the 50, with the fast pair crossed up and the
    21 EMA no longer falling.

    Two clauses carry this filter, and both were added after the first run
    returned four of six names, all of them leaders:

      * the name must have been BELOW the 50 SMA recently. "Above" and
        "recovering above" are different claims and only the count
        separates them.
      * the long-term regime must not ALREADY be bullish. An ordinary
        pullback in an uptrend dips under the 50 SMA and climbs back over
        it, satisfying every structural condition here — but a trend
        continuing is not a trend reversing, and a filter that cannot tell
        those apart is just a second momentum scanner.

    Volume and price structure are required to be *not contradicting*
    rather than confirming. Demanding both means the name is found after
    the reversal is obvious, which is not what an early-reversal scanner
    is for.
    """
    price, m50 = f(row.get("price")), _ma(row, "SMA50")
    if price is None or m50 is None:
        return False
    below = row.get("closes_below_50_30d")
    recovering = below is None or below >= RECOVERY_CLOSES
    turning = row.get("long_term_regime") != "BULLISH_LONG_TERM_REGIME"
    return bool(
        price > m50
        and recovering and turning
        and (row.get("cross") or {}).get("ema8_over_ema21")
        and _not_falling(row, "EMA21")
        and row.get("structure") != "LH/LL")


def breakdown_short(row) -> bool:
    """E: under a falling 50 with the fast averages stacked bearishly."""
    price, e8, e21, m50 = (f(row.get("price")), _ma(row, "EMA8"),
                           _ma(row, "EMA21"), _ma(row, "SMA50"))
    if None in (price, e8, e21, m50):
        return False
    return bool(price < m50 and _slope_state(row, "SMA50") == "falling"
                and e8 < e21 and e21 < m50)


def breakdown_high_confidence(row) -> bool:
    """E, the stronger version: the long-term regime agrees with the break."""
    return bool(breakdown_short(row)
                and row.get("above_200") is False
                and _slope_state(row, "SMA200") == "falling")


# ── Ranking ──────────────────────────────────────────────────────────────
# Filter C ranks by how close price is to the averages it is pulling back
# to, ascending. Everything else ranks by trend score, descending.

def _dip_rank(row):
    """Distance to the 8/21/50, in ATR, closest first. Ties broken by score
    so that among two names sitting on their 21 EMA, the better trend wins."""
    parts = [abs(_dist_atr(row, k)) for k in ("EMA8", "EMA21", "SMA50")
             if _dist_atr(row, k) is not None]
    proximity = (sum(parts) / len(parts)) if parts else 99.0
    return (round(proximity, 3), -(f(row.get("score")) or 0))


def _score_rank(row):
    return -(f(row.get("score")) or 0)


FILTERS = {
    "A": {"key": "A", "name": "Long-Term Leaders",
          "desc": "Trend score 80+, above a rising 200 SMA, 50 above 200",
          "test": long_term_leaders, "rank": _score_rank},
    "B": {"key": "B", "name": "Momentum Leaders",
          "desc": "Full bullish stack with 8/21/50 all rising",
          "test": momentum_leaders, "rank": _score_rank},
    "C": {"key": "C", "name": "Buy-the-Dip",
          "desc": "Intact rising trend, price back at or under the 21 EMA — "
                  "ranked by proximity, not by strength",
          "test": buy_the_dip, "rank": _dip_rank},
    "D": {"key": "D", "name": "Early Reversal",
          "desc": "Reclaimed the 50 SMA, 8 EMA above 21, 21 no longer falling",
          "test": early_reversal, "rank": _score_rank},
    "E": {"key": "E", "name": "Breakdown / Short",
          "desc": "Below a falling 50 SMA with 8 EMA < 21 EMA < 50 SMA",
          "test": breakdown_short, "rank": _score_rank},
}


def apply(rows, key: str) -> list:
    """Rows matching filter `key`, in that filter's own ranking order."""
    spec = FILTERS.get(key.upper())
    if not spec:
        return []
    hits = [r for r in rows if spec["test"](r)]
    return sorted(hits, key=spec["rank"])


def counts(rows) -> dict:
    """{filter key: how many names match} — for the tab badges."""
    return {k: sum(1 for r in rows if spec["test"](r))
            for k, spec in FILTERS.items()}
