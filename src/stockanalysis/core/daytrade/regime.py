"""
regime.py — §15 intraday market regime
======================================
🟢 RISK ON / 🟡 MIXED / 🔴 RISK OFF, from the index tape rather than from
anything slower.

Why this is separate from core/market_regime.py
------------------------------------------------
The repo already has a regime module, and it answers a different question
on a different clock: it reads a daily market-pulse snapshot to decide
whether conditions favour *adding to positions*. This one has to know
whether SPY is above its VWAP right now, because that is what decides
whether a 10:15 breakout is likely to trend or to fade. Reusing the daily
regime here would have imported a number that cannot change during the
session into a decision that is entirely about the session, and — as the
long-term package argues about its own naming — merging two measures under
one name mostly makes their disagreement invisible.

Breadth is not estimated
-------------------------
§15 asks for market breadth and sector breadth. Advance/decline data has
no yfinance source. Sector breadth is computed honestly — the share of the
eleven sector ETFs that are green and above VWAP — but market breadth is
reported as unavailable rather than proxied by "SPY is up", which is not
breadth and would hide exactly the narrow-tape condition breadth exists to
detect. The regime states which inputs it actually had.
"""

from __future__ import annotations

from stockanalysis.core.daytrade._common import MARKET_CLOSE, MARKET_OPEN, f, pct, session_slice, sessions_in, vwap
from stockanalysis.core.daytrade.datafeed import BENCHMARKS, SECTOR_ETF, VIX_TICKER

# VIX bands. Small caps care about the level more than most: a 28 VIX is
# where intraday reversals stop respecting structure.
VIX_CALM, VIX_ELEVATED = 18.0, 26.0


def _snapshot(bars, day) -> dict | None:
    """One instrument's session: % change, and whether it holds its VWAP."""
    if bars is None or bars.empty:
        return None
    days = [d for d in sessions_in(bars) if d <= day]
    if day not in days or len(days) < 2:
        return None
    today = session_slice(bars, day, MARKET_OPEN, MARKET_CLOSE)
    prior = session_slice(bars, days[-2], MARKET_OPEN, MARKET_CLOSE)
    if today.empty or prior.empty:
        return None
    last = f(today["Close"].iloc[-1])
    vw = vwap(today)
    return {
        "change_pct": pct(last, f(prior["Close"].iloc[-1])),
        "above_vwap": None if (vw is None or last is None) else last > vw,
        "vwap": vw, "last": last,
    }


def compute(context: dict, day) -> dict:
    """§15 regime from the context bars fetched once per scan."""
    snaps, missing = {}, []
    for t in BENCHMARKS:
        s = _snapshot(context.get(t), day)
        if s:
            snaps[t] = s
        else:
            missing.append(t)

    vix_snap = _snapshot(context.get(VIX_TICKER), day)
    vix_level = vix_snap["last"] if vix_snap else None
    if vix_level is None:
        missing.append("VIX")

    # Sector breadth from whichever sector ETFs the caller happened to
    # fetch. Reported with its denominator so partial coverage is visible
    # rather than being silently normalised into a clean-looking percentage.
    sector_snaps = {t: _snapshot(context.get(t), day)
                    for t in set(SECTOR_ETF.values()) if context.get(t) is not None}
    sector_snaps = {k: v for k, v in sector_snaps.items() if v}
    sector_green = sum(1 for s in sector_snaps.values() if (s["change_pct"] or 0) > 0)
    sector_breadth = (sector_green / len(sector_snaps) * 100.0) if sector_snaps else None

    score, reasons = 0, []
    for t, weight in (("SPY", 2), ("QQQ", 2), ("IWM", 3)):
        s = snaps.get(t)
        if not s:
            continue
        if s["above_vwap"] is True:
            score += weight
            reasons.append(f"{t} above VWAP ({s['change_pct']:+.2f}%)")
        elif s["above_vwap"] is False:
            score -= weight
            reasons.append(f"{t} below VWAP ({s['change_pct']:+.2f}%)")

    if vix_level is not None:
        if vix_level < VIX_CALM:
            score += 2
            reasons.append(f"VIX {vix_level:.1f} — calm")
        elif vix_level > VIX_ELEVATED:
            score -= 3
            reasons.append(f"VIX {vix_level:.1f} — elevated")
        else:
            reasons.append(f"VIX {vix_level:.1f} — neutral")

    if sector_breadth is not None:
        if sector_breadth >= 65:
            score += 1
            reasons.append(f"{sector_breadth:.0f}% of sectors green")
        elif sector_breadth <= 35:
            score -= 1
            reasons.append(f"only {sector_breadth:.0f}% of sectors green")

    if score >= 4:
        label, emoji = "RISK ON", "🟢"
    elif score <= -3:
        label, emoji = "RISK OFF", "🔴"
    else:
        label, emoji = "MIXED", "🟡"

    return {
        "label": label, "emoji": emoji, "score": score,
        "reasons": reasons,
        "spy": snaps.get("SPY"), "qqq": snaps.get("QQQ"), "iwm": snaps.get("IWM"),
        "vix": vix_level,
        "sector_breadth_pct": sector_breadth,
        "sector_breadth_n": len(sector_snaps),
        "market_breadth": None,
        "unavailable": (["market breadth (advance/decline — no yfinance source)"]
                        + [f"{m} session data" for m in missing]),
    }


def score_for(regime_result: dict, direction: str) -> float | None:
    """The regime as a 0-100 block score, from the trade's point of view.

    Separate from `confidence_adjustment` because the two do different
    jobs. The adjustment nudges a finished confluence score by a few
    points and is the right treatment for a small cap, where a biotech on
    phase-3 data trades its own news through a red tape. As a *weighted
    block* — which is what the mid- and large-cap profiles want — the same
    fact has to be a score, because on a megacap the index and the sector
    are not context, they are most of the trade.

    Symmetric: a RISK OFF tape scores a short exactly as well as a RISK ON
    tape scores a long.
    """
    if not regime_result:
        return None
    label, raw = regime_result.get("label"), regime_result.get("score")
    if label is None or raw is None:
        return None
    # `score` runs roughly -8..+9 across the inputs; flip it for shorts so
    # the block reads "how much does the tape support THIS trade".
    signed = raw if direction == "long" else -raw
    return max(0.0, min(100.0, 50.0 + signed * 6.0))


def confidence_adjustment(regime_label: str, direction: str) -> tuple[int, str]:
    """§15's "adjust setup confidence according to the market regime".

    A points delta on the confluence score, not a veto. Longs are helped by
    a risk-on tape and hurt by a risk-off one; shorts are the mirror. The
    magnitude is deliberately modest — the tape tilts the odds on an
    intraday small-cap catalyst move, it does not determine them, and a
    biotech on phase-3 data trades its own news through a red day.
    """
    if regime_label == "MIXED":
        return 0, "mixed tape — no adjustment"
    favourable = (regime_label == "RISK ON") == (direction == "long")
    if favourable:
        return 4, f"{regime_label} tape supports the {direction}"
    return -6, f"{regime_label} tape works against the {direction}"
