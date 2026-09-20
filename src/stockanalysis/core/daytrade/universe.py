"""
universe.py — §1 filters and §11 tradeability, 0-100
====================================================
§1 and §11 are the same subject seen twice: can this position be entered
and, more importantly, exited at a price close to the one on the screen.
§1 rejects; §11 scores what survives.

Why tradeability is a gate and not a weight
--------------------------------------------
§10 says a high setup score must not compensate for poor tradeability, and
§11 says an excellent setup with poor execution characteristics is
"WATCH — NOT TRADEABLE". Those are gate instructions, so `tradeable` comes
back as a boolean alongside the score and engine.py refuses to issue a
trade plan when it is False, regardless of confluence. Expressed as a
weight it would be defeated by five good factors, which is the exact
outcome §10 forbids — and it is the correct design on the merits too:
slippage is not a discount on the edge, it is a subtraction from it that
does not shrink when the setup improves.

The stale-quote problem
------------------------
`.info` bid/ask is only meaningful while the market is open. Outside
hours Yahoo returns the last resting quotes, which on a thin small cap can
be absurd — AVITA Medical showed 5.71 × 8.96 during development, a 44%
"spread" that reflects nothing but an empty book at 6pm on a Friday.
Scoring that would reject every candidate on a closed market and would be
a fabricated rejection, so the spread is only used when the session is
live. Otherwise it is reported unavailable and the score renormalises,
which is the same rule this package applies to float and RVOL.
"""

from __future__ import annotations

from stockanalysis.core.daytrade import profiles as PR
from stockanalysis.core.daytrade._common import band, blend, f

# §1's hard universe bounds.
MAX_MARKET_CAP = 2_000_000_000
PRICE_MIN, PRICE_MAX = 2.0, 30.0
MIN_AVG_VOLUME = 500_000
MIN_DOLLAR_VOLUME = 5_000_000

# §11 discrimination ranges.
DOLLAR_VOL_BANDS = ((1e6, 0), (5e6, 35), (2e7, 65), (5e7, 85), (1e12, 100))
AVG_VOL_BANDS = ((2e5, 0), (5e5, 30), (1e6, 55), (5e6, 80), (1e12, 100))
SPREAD_BANDS = ((0.1, 100), (0.3, 85), (0.6, 65), (1.0, 45), (2.0, 20), (99, 0))
PRICE_BANDS = ((1.0, 0), (2.0, 40), (3.0, 70), (5.0, 100), (30.0, 90), (60.0, 55), (1e6, 25))

# Below these the position cannot be exited in size on a bad print.
MIN_TRADEABLE_SCORE = 40
HARD_MIN_DOLLAR_VOLUME = 1_000_000


def spread_pct(info: dict, is_live: bool) -> tuple[float | None, str]:
    """Bid/ask spread as a percentage, or None with the reason why not."""
    bid, ask = f(info.get("bid")), f(info.get("ask"))
    if not is_live:
        return None, "market closed — resting quotes not meaningful"
    if not bid or not ask or ask <= 0 or bid <= 0 or ask < bid:
        return None, "no valid two-sided quote"
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid * 100.0, "live quote"


def passes_universe(row: dict, info: dict, sess: dict,
                    profile: dict | None = None,
                    market_cap: float | None = None) -> tuple[bool, list[str]]:
    """§1's hard filter. Returns (passed, reasons for rejection).

    Only three things reject outright: market cap over the ceiling, price
    outside the band, and a session so thin it cannot be traded at all.

    Average volume is deliberately NOT a rejection, even though §1 lists
    >500K among its preferences. §1 opens with "Prioritize", and treating
    those preferences as hard filters removes the exact candidates the
    scanner exists to find: AVITA Medical averages 412K shares and traded
    7.2M on its earnings gap. Rejecting it for its quiet three-month
    average would be using the stock's normal behaviour to disqualify it
    from a scan about abnormal behaviour. It is scored in §11 instead,
    where thin average volume correctly reduces tradeability without
    erasing the name.

    Likewise a missing value never rejects. Absent data is unknown, not
    bad, and rejecting on it biases the scan toward whatever Yahoo happens
    to cover well.
    """
    p = profile or PR.DEFAULT
    reasons = []
    # Prefer the caller's derived cap: on a watchlist name `.info` often has
    # no marketCap at all, and reading it here directly would skip the band
    # check entirely for exactly the rows most likely to be misfiled.
    mcap = market_cap if market_cap is not None else (
        f(row.get("market_cap")) or f(info.get("marketCap")))
    price = f(sess.get("price")) or f(row.get("price"))
    dollar_vol = f(sess.get("dollar_volume"))

    # The cap band comes from the profile, so a large-cap scan is not
    # rejected wholesale by the small-cap ceiling.
    if mcap is not None and not (p["market_cap_min"] <= mcap < p["market_cap_max"]):
        reasons.append(f"market cap ${mcap/1e9:.2f}B outside the "
                       f"{p['label']} band")
    if price is None:
        reasons.append("no price")
    elif not (p["price_min"] <= price <= p["price_max"]):
        reasons.append(f"price ${price:.2f} outside "
                       f"${p['price_min']:.0f}-${p['price_max']:.0f}")
    if dollar_vol is not None and dollar_vol < p["hard_min_dollar_volume"]:
        reasons.append(f"session dollar volume ${dollar_vol/1e6:.2f}M — untradeable "
                       f"for a {p['label'].lower()}")
    return (not reasons), reasons


def compute(row: dict, info: dict, sess: dict, supply: dict,
            profile: dict | None = None) -> dict:
    """§11 tradeability, 0-100, plus the `tradeable` gate."""
    p = profile or PR.DEFAULT
    price = f(sess.get("price")) or f(row.get("price"))
    dollar_vol = f(sess.get("dollar_volume"))
    avg_vol = f(info.get("averageVolume10days")) or f(info.get("averageVolume")) \
        or f(row.get("avg_volume_3m"))
    spr, spread_note = spread_pct(info, bool(sess.get("is_live")))

    parts = [
        ("Dollar volume", 35,
         band(dollar_vol, DOLLAR_VOL_BANDS) if dollar_vol is not None else None,
         f"${dollar_vol/1e6:.1f}M today" if dollar_vol else "unavailable"),
        ("Average volume", 25,
         band(avg_vol, AVG_VOL_BANDS) if avg_vol is not None else None,
         f"{avg_vol/1e6:.2f}M shares" if avg_vol else "unavailable"),
        ("Spread", 25, band(spr, SPREAD_BANDS) if spr is not None else None,
         f"{spr:.2f}%" if spr is not None else spread_note),
        ("Price", 15, band(price, PRICE_BANDS) if price is not None else None,
         f"${price:.2f}" if price else "unavailable"),
    ]
    result = blend(parts)

    warnings = list(supply.get("warnings") or [])
    if supply.get("micro_float"):
        warnings.append("micro float — halt risk on any impulse move")
    if spr is not None and spr > p["max_spread_pct"]:
        warnings.append(f"spread {spr:.2f}% exceeds the {p['max_spread_pct']:g}% "
                        f"{p['label'].lower()} preference")
    if dollar_vol is not None and dollar_vol < p["min_dollar_volume"]:
        warnings.append(
            f"dollar volume ${dollar_vol/1e6:.1f}M below the "
            f"${p['min_dollar_volume']/1e6:.0f}M {p['label'].lower()} preference")
    if avg_vol is not None and avg_vol < p["min_avg_volume"]:
        warnings.append(
            f"average volume {avg_vol/1e3:.0f}K below the "
            f"{p['min_avg_volume']/1e3:.0f}K {p['label'].lower()} preference — "
            "today's liquidity is event-driven and will not persist")

    score = result.get("score")
    # The gate. Poor liquidity fails outright; a micro float only fails
    # when it is *also* thin, since a 1.5M float trading $40M is illiquid
    # in shares but perfectly exitable in dollars.
    tradeable = True
    gate_reason = None
    if score is None:
        tradeable, gate_reason = False, "tradeability unmeasurable"
    elif score < MIN_TRADEABLE_SCORE:
        tradeable, gate_reason = False, f"tradeability {score} below {MIN_TRADEABLE_SCORE}"
    elif dollar_vol is not None and dollar_vol < p["hard_min_dollar_volume"]:
        tradeable, gate_reason = False, (
            f"dollar volume under ${p['hard_min_dollar_volume']/1e6:.0f}M")
    elif supply.get("micro_float") and (dollar_vol or 0) < p["min_dollar_volume"]:
        tradeable, gate_reason = False, "micro float on thin dollar volume"
    elif spr is not None and spr > p["hard_max_spread_pct"]:
        tradeable, gate_reason = False, f"spread {spr:.2f}% — slippage exceeds the edge"

    result.update({
        "spread_pct": spr, "spread_note": spread_note,
        "dollar_volume": dollar_vol, "avg_volume": avg_vol, "price": price,
        "tradeable": tradeable, "gate_reason": gate_reason,
        "warnings": warnings,
    })
    return result
