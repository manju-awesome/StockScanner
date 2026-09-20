"""
market_regime.py
================
Classifies the day's market regime — Bullish / Neutral / Defensive — and maps
it to position-size multipliers per trading horizon, so the dashboard's
fixed-risk sizing automatically de-risks when conditions deteriorate.

Inputs (in preference order):
  1. market_pulse() dict from scanners.market_movers — VIX level, SPY/QQQ
     strength ratings, and allocation-weighted mega-cap breadth
  2. Fallback: the scan rows themselves — % of the universe above its 200MA
     (crude breadth; universe-biased, so it only nudges, never screams)
  3. Nothing available → Neutral with 1.0 multipliers and an "unknown" note

Scoring (pulse path)
--------------------
  VIX      < 17 → +2   |  < 20 → +1   |  >= 25 → -2  |  >= 20 → -1
  SPY      STRONG +1, WEAK -1
  QQQ      STRONG +1, WEAK -1
  Breadth  >= 60% +1   |  <= 40% -1     (SPY weighted mega-cap breadth)

  total >= +3 → Bullish   |   total <= -2 → Defensive   |   else Neutral

Multipliers scale the per-trade risk budget (RISK_PER_TRADE_PCT), not the
stop distance — a Defensive day trade risks a quarter of the normal budget.
Long-term is the least regime-sensitive: tranche accumulation continues at
half size in Defensive tape rather than stopping (regime timing matters less
over a 6-24 month horizon, and weak tape is where LT entries get filled).

Usage
-----
    from stockanalysis.core.market_regime import compute_regime

    regime = compute_regime(pulse=market_pulse(), rows=rows)
    regime["regime"]        # "Bullish" | "Neutral" | "Defensive"
    regime["multipliers"]   # {"day": 1.0, "swing": 1.0, "longterm": 1.0}
    regime["drivers"]       # ["VIX 14.2 (calm) +2", "SPY STRONG +1", ...]
"""

from __future__ import annotations

REGIME_SIZE_MULT = {
    "Bullish":   {"day": 1.0,  "swing": 1.0,  "longterm": 1.0},
    "Neutral":   {"day": 0.5,  "swing": 0.75, "longterm": 1.0},
    "Defensive": {"day": 0.25, "swing": 0.5,  "longterm": 0.5},
}

REGIME_GUIDANCE = {
    "Bullish":   "Full risk budget across horizons — trade the plan.",
    "Neutral":   "Mixed tape: half-size day trades, 3/4 swings, "
                 "long-term tranches unchanged. Let A-grades only.",
    "Defensive": "Risk-off: quarter-size day trades, half-size swings and "
                 "long-term starter tranches only. Capital preservation first.",
}

BULLISH_MIN  = 3    # score >= → Bullish
DEFENSIVE_MAX = -2  # score <= → Defensive


def _classify(score: int) -> str:
    if score >= BULLISH_MIN:
        return "Bullish"
    if score <= DEFENSIVE_MAX:
        return "Defensive"
    return "Neutral"


def _from_pulse(pulse: dict) -> tuple[int, list[str]] | None:
    """Score the regime from a market_pulse() dict. None if pulse is unusable."""
    vix = (pulse.get("vix") or {}).get("level")
    spy = pulse.get("spy") or {}
    qqq = pulse.get("qqq") or {}
    if vix is None and not spy and not qqq:
        return None

    score, drivers = 0, []
    if vix is not None:
        if vix < 17:
            score += 2; drivers.append(f"VIX {vix:.1f} (calm) +2")
        elif vix < 20:
            score += 1; drivers.append(f"VIX {vix:.1f} (normal) +1")
        elif vix >= 25:
            score -= 2; drivers.append(f"VIX {vix:.1f} (fear) -2")
        else:
            score -= 1; drivers.append(f"VIX {vix:.1f} (elevated) -1")

    for name, idx in (("SPY", spy), ("QQQ", qqq)):
        strength = idx.get("strength")
        if strength == "STRONG":
            score += 1; drivers.append(f"{name} STRONG +1")
        elif strength == "WEAK":
            score -= 1; drivers.append(f"{name} WEAK -1")
        elif strength:
            drivers.append(f"{name} {strength} +0")

    breadth = spy.get("breadth_pct")
    if breadth is not None:
        if breadth >= 60:
            score += 1; drivers.append(f"mega-cap breadth {breadth:.0f}% +1")
        elif breadth <= 40:
            score -= 1; drivers.append(f"mega-cap breadth {breadth:.0f}% -1")
        else:
            drivers.append(f"mega-cap breadth {breadth:.0f}% +0")

    return score, drivers


def _from_rows(rows: list[dict]) -> tuple[int, list[str]] | None:
    """Fallback breadth from the scanned universe itself (weak signal —
    capped at ±1 so a hot watchlist can't fake a Bullish regime)."""
    flags = [r.get("Above_200MA") for r in rows or []
             if r.get("Above_200MA") is not None]
    if len(flags) < 10:
        return None
    pct = sum(1 for f in flags if f) / len(flags) * 100
    if pct >= 70:
        return 1, [f"scan breadth {pct:.0f}% above 200MA +1 (pulse unavailable)"]
    if pct <= 40:
        return -1, [f"scan breadth {pct:.0f}% above 200MA -1 (pulse unavailable)"]
    return 0, [f"scan breadth {pct:.0f}% above 200MA +0 (pulse unavailable)"]


def compute_regime(pulse: dict | None = None,
                   rows: list[dict] | None = None) -> dict:
    """
    Returns {"regime", "score", "drivers", "multipliers", "guidance",
    "source"}. Never raises; with no usable input it returns Neutral at full
    multipliers ("source": "none") — unknown tape must not silently shrink
    positions.
    """
    scored = _from_pulse(pulse) if pulse else None
    source = "market_pulse"
    if scored is None:
        scored = _from_rows(rows)
        source = "scan_breadth"
    if scored is None:
        return {
            "regime": "Neutral", "score": 0,
            "drivers": ["no market data — regime unknown, sizing unchanged"],
            "multipliers": dict(REGIME_SIZE_MULT["Bullish"]),  # 1.0s
            "guidance": "Regime unknown — verify market conditions manually.",
            "source": "none",
        }

    score, drivers = scored
    regime = _classify(score)
    return {
        "regime": regime, "score": score, "drivers": drivers,
        "multipliers": dict(REGIME_SIZE_MULT[regime]),
        "guidance": REGIME_GUIDANCE[regime],
        "source": source,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BENCHMARK LEADERSHIP — who is leading, and does the rest of the market agree
# ─────────────────────────────────────────────────────────────────────────────
# compute_regime() above already scores VIX, SPY, QQQ and mega-cap breadth
# into a single number. This answers a different question that the score
# cannot: WHICH part of the market is carrying it. A +3 regime driven by QQQ
# alone while the Dow lags is a narrow, tech-led tape; the same +3 with DIA
# and IWM confirming is a broad one, and they warrant different position
# sizing even though the number matches.
#
#   SPY   market direction
#   QQQ   growth / momentum
#   DIA   blue-chip confirmation (old economy)
#   IWM   risk appetite (small caps)
#
# Trend is measured against the 200-day average, not today's move. A verdict
# that flips because the Dow closed -0.2% would be noise dressed as a signal;
# the 200MA relationship is what "leading" or "lagging" actually means over a
# swing horizon. Today's change is reported alongside but never drives the
# classification.
LEADERSHIP_TICKERS = ("SPY", "QQQ", "DIA", "IWM")


def benchmark_leadership(profiles: dict) -> dict:
    """Read the tape from benchmark ETF profiles (core.etf_profile shape).

    Returns {"ok", "verdict", "tone", "detail", "legs", "missing"}. Missing
    benchmarks are named rather than silently treated as neutral — a verdict
    built on two of four legs is not the same claim as one built on four.
    """
    legs, missing = {}, []
    for ticker in LEADERSHIP_TICKERS:
        p = profiles.get(ticker) or {}
        dist = p.get("dist_ma200")
        if dist is None:
            missing.append(ticker)
            continue
        legs[ticker] = {
            "ticker": ticker,
            "above_200ma": dist > 0,
            "dist_ma200": round(float(dist), 2),
            "change_pct": p.get("change_pct"),
            "ytd": p.get("ytd_return"),
        }

    if len(legs) < 2:
        return {"ok": False, "verdict": "Not enough benchmark data",
                "tone": "muted", "legs": list(legs.values()),
                "missing": missing,
                "detail": "Need at least SPY plus one other benchmark — "
                          "run Refresh ETFs."}

    up = {t for t, v in legs.items() if v["above_200ma"]}
    spy, qqq, dia, iwm = (t in up for t in LEADERSHIP_TICKERS)
    has = lambda t: t in legs                                    # noqa: E731

    if has("SPY") and has("QQQ") and has("DIA") and spy and qqq and dia:
        verdict, tone = "Broad risk-on", "good"
        detail = ("SPY, QQQ and DIA all above their 200-day — the rally has "
                  "old-economy confirmation, not just tech.")
        if has("IWM"):
            detail += (" Small caps confirm too." if iwm else
                       " Small caps still lag, so risk appetite is not yet full.")
    elif has("SPY") and has("DIA") and not spy and not dia:
        verdict, tone = "Broad risk-off", "bad"
        detail = ("Both SPY and DIA below their 200-day — this is the whole "
                  "market, not a rotation. Size down or stand aside.")
    elif has("QQQ") and has("DIA") and qqq and not dia:
        verdict, tone = "Tech-led, narrow", "watch"
        detail = ("QQQ leads while DIA lags its 200-day — breadth is thin and "
                  "the tape is carried by growth. Thematic tech exposure is "
                  "doing the work; a rotation would hit it hardest.")
    elif has("DIA") and has("QQQ") and dia and not qqq:
        verdict, tone = "Defensive rotation", "watch"
        detail = ("DIA holds while QQQ is below its 200-day — money is in "
                  "blue chips, not growth. Momentum and semis setups face a "
                  "headwind here.")
    elif spy:
        verdict, tone = "Mixed, market up", "watch"
        detail = "SPY is above its 200-day but the other legs disagree."
    else:
        verdict, tone = "Mixed, market down", "watch"
        detail = "SPY is below its 200-day; leadership is unsettled."

    if has("IWM") and not iwm and has("SPY") and spy:
        detail += " IWM below its 200-day says risk appetite is selective."

    return {"ok": True, "verdict": verdict, "tone": tone, "detail": detail,
            "legs": [legs[t] for t in LEADERSHIP_TICKERS if t in legs],
            "missing": missing}
