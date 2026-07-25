"""
sector_strength.py
===================
Pure scoring for the market-scanner funnel:

    Universe → Market Regime → Sector Strength → Scanner

Instead of scanning all ~500 names every run, the funnel keeps only tickers
in the strongest sectors for the day's regime — cutting the expensive
per-ticker fetch list by well over half before the scan loop starts.

Sector strength is relative return vs SPY (an absolute-return rank would keep
every sector in a rally and none in a selloff): each GICS sector's proxy ETF
is scored 0.6 × (1-month return − SPY 1-month) + 0.4 × (3-month − SPY 3-month)
— the shorter window dominates so rotation shows up within days, the longer
one keeps a single hot week from crowning a sector.

The regime (core.market_regime) sets how selective to be:
    Bullish   → top 5 sectors (broad participation, cast wider)
    Neutral   → top 4
    Defensive → top 3 (risk-off: only the clear leaders, which in weak tape
                are usually the defensive sectors anyway — no hardcoded
                "defensive sector" list needed)

Fail-open rule: a ticker whose sector is unknown is KEPT. The funnel exists
to cut fetch cost, not to silently drop names because a lookup failed.

Pure functions, no network — fetching lives in scanners.sector_filter.
"""

from __future__ import annotations

# yfinance `info["sector"]` name -> proxy ETF (the 11 GICS sectors)
SECTOR_ETF = {
    "Technology":             "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical":      "XLY",
    "Financial Services":     "XLF",
    "Healthcare":             "XLV",
    "Industrials":            "XLI",
    "Energy":                 "XLE",
    "Consumer Defensive":     "XLP",
    "Utilities":              "XLU",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
}

TOP_SECTORS_BY_REGIME = {"Bullish": 5, "Neutral": 4, "Defensive": 3}

W_1M, W_3M = 0.6, 0.4


def rank_sectors(etf_returns: dict) -> list[dict]:
    """
    etf_returns: {"SPY": {"r1m": pct, "r3m": pct}, "XLK": {...}, ...}
    Returns sectors sorted strongest-first:
    [{"sector", "etf", "score", "rel_1m", "rel_3m"}, ...]
    Sectors with missing data are omitted; [] if SPY itself is missing.
    """
    spy = etf_returns.get("SPY")
    if not spy or spy.get("r1m") is None or spy.get("r3m") is None:
        return []

    ranked = []
    for sector, etf in SECTOR_ETF.items():
        r = etf_returns.get(etf)
        if not r or r.get("r1m") is None or r.get("r3m") is None:
            continue
        rel_1m = r["r1m"] - spy["r1m"]
        rel_3m = r["r3m"] - spy["r3m"]
        ranked.append({
            "sector": sector,
            "etf":    etf,
            "score":  round(W_1M * rel_1m + W_3M * rel_3m, 2),
            "rel_1m": round(rel_1m, 2),
            "rel_3m": round(rel_3m, 2),
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def select_sectors(ranked: list[dict], regime: str) -> list[str]:
    """Strongest sector names to keep for this regime (unknown regime →
    Neutral's count)."""
    n = TOP_SECTORS_BY_REGIME.get(regime, TOP_SECTORS_BY_REGIME["Neutral"])
    return [r["sector"] for r in ranked[:n]]


def filter_universe(tickers: list[str], sector_of: dict[str, str | None],
                    allowed: list[str] | set[str]) -> dict:
    """
    Split tickers by sector membership. Unknown-sector tickers are kept
    (fail-open — see module docstring).
    Returns {"kept": [...], "dropped": [...], "unknown": [...]}.
    """
    allowed_set = set(allowed)
    kept, dropped, unknown = [], [], []
    for t in tickers:
        sector = sector_of.get(t)
        if sector is None:
            unknown.append(t)
            kept.append(t)
        elif sector in allowed_set:
            kept.append(t)
        else:
            dropped.append(t)
    return {"kept": kept, "dropped": dropped, "unknown": unknown}
