"""
The Short Opportunity Score — the seven components and their weights.

The inversion this module exists for: **a low LQuality is not a
rejection here, it is evidence.** The long engine stops at "failed on
business quality" and says AVOID; that verdict is correct about owning
the company and says nothing about the trade. A weak business trading at
a demanding valuation, far above its own moving averages, overbought,
into distribution, is the setup — the weakness is the thesis, not a
disqualifier.

That does NOT make weakness sufficient. A bad company can compound
upward for years, and the graveyard of short sellers is full of people
who were right about the business and early about the price. So the
score is built so that fundamentals alone cannot carry it: weakness and
valuation together are 35 of 100, and the remaining 65 are all price and
tape. A terrible company that is not extended, not overbought and not
under distribution scores in the thirties and belongs in NO EDGE.

Confirmation is deliberately NOT in this score. Whether price has begun
to roll over decides the BUCKET (SHORT NOW versus SHORT WATCH), not the
strength of the thesis — the setup is exactly as good the day before it
triggers as the day it does.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f, s

from . import extension as EX

# The seven components. Fundamentals are a minority on purpose — see the
# module docstring.
WEIGHTS = {
    "weakness":   20,    # the business itself
    "valuation":  15,    # what the price demands of it
    "extension":  25,    # how far from its own trend (in ATR)
    "overbought": 15,    # RSI, Bollinger position
    "distribution": 10,  # institutions leaving over 25 days
    "location":   10,    # at resistance / 52-week high
    "reward":      5,    # is there room to fall
}

# Above this relative strength, the tape is still bidding the name up
# regardless of the story. Does not disqualify — it caps the bucket.
STRONG_RS = 80
# A trend this strong can stay overbought for weeks.
STRONG_ADX = 38


def weakness(result) -> dict:
    """Fundamental weakness, 0-100. Inverts LQuality.

    Anchored on the long engine's OWN tier boundaries rather than an
    invented scale: it treats 70 as the floor for "Watchlist" and calls
    anything below that Reject. So 75 is where weakness starts to
    register at all, and 45 — deep in Reject territory — is full marks.
    A LQuality of 60 is squarely in the long engine's Reject band and
    reads as half-weak, which is the honest description of a mediocre
    business that is not yet a distressed one.
    """
    q = f((result.get("quality") or {}).get("score"))
    if q is None:
        return {"score": None, "detail": "no quality score", "lquality": None}

    score = max(0.0, min(100.0, (75.0 - q) / 30.0 * 100.0))
    tier = s((result.get("quality") or {}).get("tier"))
    return {
        "score": round(score),
        "lquality": q,
        "tier": tier,
        "detail": (f"LQuality {q:.0f}/100"
                   + (f" ({tier})" if tier else "")
                   + (" — weak business" if score >= 50 else
                      " — not weak enough to be the thesis")),
    }


def valuation_excess(result) -> dict:
    """How much growth the price demands versus what is delivered.

    Uses the long engine's reverse DCF, which produces exactly the
    quantity a short thesis wants: the gap between implied and delivered
    growth. A positive gap means the price is underwritten by growth the
    company has not produced.
    """
    v = result.get("valuation") or {}
    gap = f(v.get("growth_gap_pp"))
    band = s(v.get("band"))

    if gap is None:
        # Peer/multiple methods give upside instead; a large negative
        # upside is the same statement in a different unit.
        up = f(v.get("upside_pct"))
        if up is None:
            return {"score": None, "detail": "no valuation reading",
                    "band": band}
        score = max(0.0, min(100.0, (-up - 10.0) / 45.0 * 100.0))
        return {"score": round(score), "band": band, "upside_pct": up,
                "detail": s(v.get("headline")) or f"{up:.0f}% upside"}

    # 0pp is fair; +40pp is a price demanding far more than delivered.
    score = max(0.0, min(100.0, (gap - 5.0) / 35.0 * 100.0))
    return {
        "score": round(score),
        "band": band,
        "growth_gap_pp": gap,
        "implied": f(v.get("implied_growth_pct")),
        "delivered": f(v.get("delivered_growth_pct")),
        "detail": s(v.get("headline")) or f"growth gap {gap:+.0f}pp",
    }


def overbought(row) -> dict:
    """RSI and Bollinger position, 0-100."""
    rsi = f(row.get("RSI_14"))
    bb = f(row.get("BB_PctB"))

    parts, bits = {}, []
    if rsi is not None:
        # 65 is where it starts to mean something; 85 is the ceiling.
        parts["rsi"] = max(0.0, min(100.0, (rsi - 65.0) / 20.0 * 100.0))
        bits.append(f"RSI {rsi:.0f}")
    if bb is not None:
        # Above 1.0 is outside the upper band.
        parts["bb"] = max(0.0, min(100.0, (bb - 0.80) / 0.28 * 100.0))
        bits.append(f"%B {bb:.2f}")

    if not parts:
        return {"score": None, "detail": "no momentum reading"}
    score = sum(parts.values()) / len(parts)
    return {"score": round(score), "rsi": rsi, "bb_pct_b": bb,
            "detail": ", ".join(bits)}


def distribution(row) -> dict:
    """Institutional selling over the last 25 sessions, 0-100.

    Down days on rising volume. Anchored on the classic 4-5-in-25
    warning threshold, not on this universe's median — which currently
    sits at 5 only because the whole scan is a late-stage bull market,
    and calibrating "normal" against a distribution of tops would define
    the warning out of existence.
    """
    d = f(row.get("Distribution_Days_25d"))
    if d is None:
        return {"score": None, "detail": "no distribution count"}
    score = max(0.0, min(100.0, (d - 2.0) / 6.0 * 100.0))
    return {
        "score": round(score), "days": d,
        "detail": (f"{d:.0f} distribution days in 25 sessions"
                   + (" — heavy" if d >= 7 else
                      " — into the warning band" if d >= 5 else
                      " — clean" if d <= 3 else "")),
    }


def location(row) -> dict:
    """Where price sits in its own 52-week range, 0-100.

    A short wants price at the top of the range and, ideally, having just
    printed the high — that is where the supply of willing buyers is
    thinnest and where a failure is most visible.
    """
    dist = f(row.get("Dist_52W_High%"))
    days_since = f(row.get("Days_Since_52W_High"))
    if dist is None:
        return {"score": None, "detail": "no 52-week reading"}

    # 0% from the high scores 100; 15% below scores nothing.
    score = max(0.0, min(100.0, (15.0 + dist) / 15.0 * 100.0))
    if days_since is not None and days_since <= 3:
        score = min(100.0, score + 10)

    return {
        "score": round(score),
        "dist_52w_high": dist,
        "days_since_high": days_since,
        "detail": (f"{abs(dist):.1f}% from the 52-week high"
                   + (f", set {days_since:.0f} session(s) ago"
                      if days_since is not None and days_since <= 5 else "")),
    }


def reward(row, price=None, atr=None) -> dict:
    """Room to fall, measured to the nearest mean-reversion targets.

    Reward is in ATR, not percent, for the same reason extension is: a
    12% move to the 50 MA is a different trade at 2% ATR than at 5%.
    """
    targets = EX.mean_reversion_targets(row, price, atr)
    if not targets:
        return {"score": None, "targets": [], "detail": "no level below price"}

    # The 50 MA is the honest primary target for a reversion short; fall
    # back to whatever is nearest if it is not below price.
    primary = next((t for t in targets if t["key"] == "50MA"), targets[0])
    m = primary.get("atr_multiple")
    score = (None if m is None else
             max(0.0, min(100.0, (m - 1.5) / 5.5 * 100.0)))

    return {
        "score": None if score is None else round(score),
        "targets": targets,
        "primary": primary,
        "detail": (f"{primary['name']} is {abs(primary['move_pct']):.1f}% "
                   f"below"
                   + (f" ({m:.1f} ATR)" if m is not None else "")),
    }


def compute(result, row, price=None, atr=None) -> dict:
    """The Short Opportunity Score and every component behind it.

    Components are averaged over the weights actually AVAILABLE, so a
    missing input reduces coverage rather than scoring zero — absent is
    not the same claim as "this factor argues against the short".
    """
    ext = EX.evaluate(row, price, atr)
    parts = {
        "weakness":     weakness(result),
        "valuation":    valuation_excess(result),
        "extension":    {"score": ext["score"], "detail": ext["detail"]},
        "overbought":   overbought(row),
        "distribution": distribution(row),
        "location":     location(row),
        "reward":       reward(row, price, atr),
    }

    got = {k: v["score"] for k, v in parts.items() if v.get("score") is not None}
    wsum = sum(WEIGHTS[k] for k in got)
    score = (sum(v * WEIGHTS[k] for k, v in got.items()) / wsum
             if wsum else None)

    # ── Caps, not disqualifiers ────────────────────────────────────────
    # A name the market is still aggressively bidding can stay extended
    # for months. That is a reason to wait for price to prove it, not a
    # reason to stop looking — so these cap the score and later block
    # SHORT NOW, rather than removing the name from the scan.
    rs = f(row.get("RS_Rank"))
    adx = f(row.get("ADX_14"))
    caps = []
    if score is not None and rs is not None and rs >= STRONG_RS:
        score = min(score, 85.0)
        caps.append(f"RS {rs:.0f} — the tape is still bidding this up")
    if score is not None and adx is not None and adx >= STRONG_ADX:
        score = min(score, 80.0)
        caps.append(f"ADX {adx:.0f} — trend too strong to fade on the story "
                    f"alone")

    return {
        "score": None if score is None else round(score),
        "coverage": round(wsum / sum(WEIGHTS.values()) * 100) if wsum else 0,
        "components": [
            {"name": k, "weight": WEIGHTS[k],
             "score": parts[k].get("score"),
             "detail": parts[k].get("detail"),
             "available": parts[k].get("score") is not None}
            for k in WEIGHTS
        ],
        "parts": parts,
        "extension": ext,
        "caps": caps,
        "rs_rank": rs,
        "adx": adx,
    }
