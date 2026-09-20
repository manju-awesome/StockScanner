"""
profiles.py — one engine, three market-cap profiles
===================================================
The pipeline is identical for a $200M biotech and a $2T megacap:

    universe → opportunity → setup → entry quality → risk → size → manage

What differs is what the numbers *mean*. RVOL of 1.3 is noise on a
small-cap runner and a genuine institutional footprint on a megacap. A 6M
float is the whole thesis on the first and an irrelevant fact about the
second. So the thresholds and the weights live here, in a profile the
engine reads, rather than as constants scattered through eight modules —
which is what makes this one engine with three calibrations instead of
three scanners that will drift apart.

What the profiles actually encode
----------------------------------
Three shifts, in the same direction as market cap:

1. **Scarcity gives way to participation.** Float, float rotation and
   dilution dominate small caps because supply is the mechanism — the
   move happens *because* there are few shares. On a megacap no
   plausible day's volume moves the float, so the supply block collapses
   to a liquidity check and the weight goes to relative strength and the
   tape.

2. **The stock gives way to its context.** A $200M biotech on phase-3
   data trades its own news through a red market. AAPL does not: if
   QQQ, XLK and SPY are all offered, an AAPL long is fighting all three.
   So `market` (relative strength, including sector) and `regime` carry
   45% at large cap against 15% at small.

3. **Volatility stops being the point.** Small-cap selection *wants*
   ATR% > 4; requiring that of a megacap would return an empty scan on
   almost every day, and the ones it did return would be crisis names.

Weights sum to 100 in every profile and cover the same eight blocks, so
the confluence score stays comparable in construction — but never across
profiles. An 85 small-cap and an 85 large-cap are both "strong for their
class" and are not the same trade; the profile travels with the row so a
mixed table can say which yardstick was used.

These are starting weights, not settled ones. They are written to be
fitted against a trade history later, which is why they are data here
rather than logic.
"""

from __future__ import annotations

from stockanalysis.core.daytrade._common import f

# Block weights. The eight keys match engine.evaluate's scored blocks.
#
#   volatility  ATR%, RVOL, gap, dollar volume            (§3)
#   supply      float, short interest, liquidity depth    (§4)
#   catalyst    why it is moving                          (§2)
#   volume      the contraction → expansion sequence      (§8)
#   setup       intraday structure quality                (§6)
#   market      relative strength vs indices AND sector   (§7)
#   regime      whether the tape supports the direction   (§15)
#   room        distance to the next real level           (§9)

SMALL_CAP = {
    "key": "small",
    "label": "Small cap",
    "description": "under $2B — momentum, scarcity of shares, and a catalyst",
    "market_cap_min": 0,
    "market_cap_max": 2_000_000_000,
    "price_min": 2.0,
    "price_max": 30.0,
    "weights": {"catalyst": 20, "volatility": 15, "supply": 15, "volume": 15,
                "setup": 15, "market": 10, "regime": 5, "room": 5},
    # What counts as unusual for this class.
    "rvol_significant": 3.0,
    "atr_pct_min": 4.0,
    "gap_min": 3.0,
    "screen_min_abs_change": 3.0,
    # Execution floors.
    "min_dollar_volume": 5_000_000,
    "hard_min_dollar_volume": 1_000_000,
    "max_spread_pct": 1.0,
    "hard_max_spread_pct": 2.5,
    "min_avg_volume": 500_000,
    # Supply is a real driver here, not a formality.
    "float_matters": True,
}

MID_CAP = {
    "key": "mid",
    "label": "Mid cap",
    "description": "$2B-$10B — sustained direction, sector strength, structure",
    "market_cap_min": 2_000_000_000,
    "market_cap_max": 10_000_000_000,
    "price_min": 5.0,
    "price_max": 200.0,
    # Setup carries the most weight of any profile: a mid cap rarely gaps
    # 40% on a press release, so the edge is in structure and persistence
    # rather than in the size of the reaction.
    "weights": {"setup": 20, "market": 25, "catalyst": 15, "volume": 15,
                "regime": 10, "volatility": 5, "supply": 5, "room": 5},
    "rvol_significant": 2.0,
    "atr_pct_min": 2.5,
    "gap_min": 2.0,
    "screen_min_abs_change": 2.0,
    "min_dollar_volume": 20_000_000,
    "hard_min_dollar_volume": 5_000_000,
    "max_spread_pct": 0.5,
    "hard_max_spread_pct": 1.0,
    "min_avg_volume": 1_000_000,
    "float_matters": False,
}

LARGE_CAP = {
    "key": "large",
    "label": "Large cap",
    "description": "over $10B — market and sector confirmation, institutional flow",
    "market_cap_min": 10_000_000_000,
    "market_cap_max": float("inf"),
    "price_min": 20.0,
    "price_max": 10_000.0,
    # Market + regime = 45. On a megacap the index and the sector are not
    # context, they are most of the trade.
    "weights": {"market": 30, "regime": 15, "catalyst": 15, "setup": 15,
                "volume": 10, "volatility": 5, "supply": 5, "room": 5},
    # 1.3x RVOL on a megacap with a catalyst is a real institutional
    # footprint; demanding 3x would only ever surface crisis days.
    "rvol_significant": 1.3,
    "atr_pct_min": 1.5,
    "gap_min": 1.0,
    "screen_min_abs_change": 1.5,
    "min_dollar_volume": 50_000_000,
    "hard_min_dollar_volume": 10_000_000,
    "max_spread_pct": 0.15,
    "hard_max_spread_pct": 0.5,
    "min_avg_volume": 2_000_000,
    "float_matters": False,
}

PROFILES = {p["key"]: p for p in (SMALL_CAP, MID_CAP, LARGE_CAP)}
DEFAULT = SMALL_CAP


def by_key(key: str | None) -> dict:
    """Profile by name; unknown or missing falls back to small cap.

    Small cap is the default because it is the profile this engine was
    calibrated and validated on, and because its thresholds are the
    strictest — defaulting to the loosest calibration would let a megacap
    slip through the small-cap gates.
    """
    return PROFILES.get((key or "").lower(), DEFAULT)


def infer_market_cap(market_cap, shares_outstanding, float_shares,
                     price) -> tuple[float | None, str]:
    """Market cap, and where the number came from.

    `.info` omits `marketCap` outright for some symbols — Western Digital
    came back with 14 fields and no cap at all — and the missing-value
    fallback then classified a $442 stock as a small cap, which rejected it
    for being "outside $2-$30". A nonsense reason for a real name.

    So the cap is derived rather than abandoned, from reported numbers only:

      shares outstanding x price   exact
      float x price                a LOWER BOUND, since float <= shares out

    The lower bound is safe in the one direction that matters. It can only
    ever move a stock UP a band, never down, so a genuine small cap can
    never be promoted into the looser large-cap thresholds by it — while
    WDC's 340.6M float at $442 puts its floor at $150B, which settles the
    classification beyond doubt.
    """
    mc, so, fs, px = f(market_cap), f(shares_outstanding), f(float_shares), f(price)
    if mc and mc > 0:
        return mc, "reported"
    if so and px and so > 0 and px > 0:
        return so * px, "shares outstanding × price"
    if fs and px and fs > 0 and px > 0:
        return fs * px, "float × price (lower bound)"
    return None, "unavailable"


def for_market_cap(market_cap: float | None) -> dict:
    """Pick a profile from the stock's own market cap.

    An unknown market cap gets the small-cap profile rather than a guess.
    Everything the screen returns here is sub-$2B by construction, and if
    the cap is missing on a name that is genuinely large, the small-cap
    thresholds are the conservative error: it will fail the stricter
    volatility and float expectations and rank low, rather than passing a
    megacap through checks that were never meant for it.
    """
    if market_cap is None:
        return DEFAULT
    for profile in (SMALL_CAP, MID_CAP, LARGE_CAP):
        if profile["market_cap_min"] <= market_cap < profile["market_cap_max"]:
            return profile
    return LARGE_CAP


def weights(profile: dict) -> dict:
    return dict(profile["weights"])


def validate() -> None:
    """Every profile must weight the same blocks and sum to 100.

    Called by the tests. A profile that sums to 97 would still produce a
    confluence score — renormalised, plausible, and quietly on a different
    scale from the other two, which is exactly the kind of drift this
    module exists to prevent.
    """
    keys = set(SMALL_CAP["weights"])
    for p in PROFILES.values():
        assert set(p["weights"]) == keys, f"{p['key']} weights different blocks"
        total = sum(p["weights"].values())
        assert total == 100, f"{p['key']} weights sum to {total}, not 100"
