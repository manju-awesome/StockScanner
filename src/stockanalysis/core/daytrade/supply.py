"""
supply.py — §4 float / supply engine, 0-100
===========================================
Small float is the amplifier: the same dollar of buying moves a 6M-share
float far more than a 60M-share one. But §1 is explicit that the lowest
float must not automatically rank highest, and §4 is explicit that short
interest alone is not a buy signal. Both instructions are structural here
rather than advisory.

Float is scored on an inverted-U, not a slope
---------------------------------------------
Tightness helps until it stops being a market. Under ~2M shares the stock
is not a tradeable instrument for size — it is halt-prone, the spread
widens to whatever the one market maker wants, and the exit that the plan
depends on may not exist at any price. So the float curve peaks in the
3-15M band and falls away on *both* sides. A 0.8M-share float scores below
a 12M one, which is the whole content of "do not automatically rank the
lowest-float stocks highest."

Short interest is potential, not permission
-------------------------------------------
`squeeze_ready` is returned as a flag, not folded into the score as a
bonus. The engine grants squeeze credit only when §4's four conditions all
hold — catalyst, unusual volume, price strength, breakout — and it does so
in engine.py where it can see them. Scoring the bonus here, where only the
short data is visible, is precisely the substitution the spec forbids.

What is not measurable, and is therefore not scored
----------------------------------------------------
Borrow fee, shares available to borrow, recent offerings, ATM programmes,
warrants, convertibles and reverse-split risk have no yfinance source.
None of them is estimated. `dilution_checked` comes back False and every
candidate carries an explicit unverified flag, because on a small-cap
runner an active ATM is the single most common way a good-looking setup
fails, and a scanner that silently omitted it would be implying an all
clear it never checked.
"""

from __future__ import annotations

from stockanalysis.core.daytrade._common import band, blend, f

# Inverted-U on float: peaks 3-15M, penalised below 2M for untradeability
# and above 50M for lack of amplification.
FLOAT_BANDS = (
    (1_000_000, 25),      # sub-1M: a quote, not a market
    (2_000_000, 55),
    (3_000_000, 80),
    (10_000_000, 100),    # §1's highest-score band
    (25_000_000, 90),     # §1's second band
    (50_000_000, 70),
    (100_000_000, 40),
    (1e12, 15),
)
SHORT_PCT_BANDS = ((5, 10), (10, 35), (15, 55), (20, 75), (30, 95), (999, 100))
DAYS_TO_COVER_BANDS = ((0.5, 10), (1.0, 25), (2.0, 50), (4.0, 75), (8.0, 95), (999, 100))

# Below this the float is small enough that liquidity risk dominates, and
# the engine caps position size regardless of score (§14).
MICRO_FLOAT = 2_000_000


def compute(info: dict, avg_volume: float | None = None) -> dict:
    """§4 supply score, 0-100, plus the raw supply picture."""
    float_shares = f(info.get("floatShares"))
    shares_out = f(info.get("sharesOutstanding"))
    shares_short = f(info.get("sharesShort"))
    short_prior = f(info.get("sharesShortPriorMonth"))

    # shortPercentOfFloat arrives as a fraction (0.0859 = 8.59%). Deriving
    # it from the raw share counts when absent is arithmetic on two
    # reported numbers, not an estimate.
    short_pct = f(info.get("shortPercentOfFloat"))
    if short_pct is not None:
        short_pct *= 100.0
    elif shares_short and float_shares and float_shares > 0:
        short_pct = shares_short / float_shares * 100.0

    # shortRatio is Yahoo's days-to-cover against its own average volume.
    days_to_cover = f(info.get("shortRatio"))
    if days_to_cover is None and shares_short and avg_volume and avg_volume > 0:
        days_to_cover = shares_short / avg_volume

    float_pct_of_out = (float_shares / shares_out * 100.0
                        if float_shares and shares_out and shares_out > 0 else None)

    short_trend = None
    if shares_short and short_prior and short_prior > 0:
        change = (shares_short - short_prior) / short_prior * 100.0
        short_trend = "rising" if change > 5 else ("falling" if change < -5 else "flat")

    parts = [
        ("Float", 60,
         band(float_shares, FLOAT_BANDS) if float_shares is not None else None,
         f"{float_shares/1e6:.1f}M shares" if float_shares else "unavailable"),
        ("Short interest", 25,
         band(short_pct, SHORT_PCT_BANDS) if short_pct is not None else None,
         f"{short_pct:.1f}% of float" if short_pct is not None else "unavailable"),
        ("Days to cover", 15,
         band(days_to_cover, DAYS_TO_COVER_BANDS) if days_to_cover is not None else None,
         f"{days_to_cover:.1f} days" if days_to_cover is not None else "unavailable"),
    ]
    result = blend(parts)

    warnings = []
    if float_shares is not None and float_shares < MICRO_FLOAT:
        warnings.append(
            f"micro float {float_shares/1e6:.2f}M — halt and slippage risk, size down")
    if float_pct_of_out is not None and float_pct_of_out < 20:
        warnings.append(
            f"only {float_pct_of_out:.0f}% of shares outstanding are float — "
            "insider/lockup supply can arrive without warning")

    result.update({
        "float_shares": float_shares,
        "shares_outstanding": shares_out,
        "float_pct_of_outstanding": float_pct_of_out,
        "short_pct_of_float": short_pct,
        "days_to_cover": days_to_cover,
        "short_trend": short_trend,
        "micro_float": bool(float_shares is not None and float_shares < MICRO_FLOAT),
        # §4's precondition, evaluated by engine.py against catalyst,
        # volume, strength and breakout before it is allowed to mean
        # anything.
        "squeeze_ready": bool(short_pct is not None and short_pct >= 15
                              and (days_to_cover or 0) >= 2),
        # §1's dilution penalties, honestly unscored.
        "dilution_checked": False,
        "dilution_note": ("offerings/ATM/warrants/reverse-split risk not verifiable "
                          "from yfinance — check the latest S-1/S-3/424B5 before sizing"),
        "warnings": warnings,
    })
    return result
