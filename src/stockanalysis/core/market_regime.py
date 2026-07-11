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
