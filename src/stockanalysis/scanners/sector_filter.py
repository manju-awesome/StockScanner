"""
sector_filter.py
=================
Fetch + orchestration for the market-scanner funnel (scoring lives in
core.sector_strength):

    Universe → Market Regime → Sector Strength → Scanner

apply_market_funnel(tickers) is the one entry point — scan_universe.main()
calls it before the per-ticker scan loop. It never raises and fails open:
any missing input (ETF data, regime, sector lookups) means the universe
passes through unfiltered rather than silently losing names.

Ticker→sector mapping cost: sectors come from yf.Ticker(t).info, which is
slow and rate-limit-prone across 500 names — exactly the cost the funnel is
supposed to avoid. So the mapping is cached at data/cache/ticker_sectors.json
and seeded from recent scan CSVs (every scan row already carries a Sector
column from core.metrics), meaning after any prior full scan the funnel
resolves sectors with zero extra network calls; only genuinely new tickers
hit .info, and a lookup cap keeps a cold cache from stalling the scan.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from stockanalysis.core.sector_strength import (
    SECTOR_ETF, rank_sectors, select_sectors, filter_universe)

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
SECTOR_CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "ticker_sectors.json"

# Max cold .info lookups per run — a cache miss costs ~1s each, so a fully
# cold 500-name universe would otherwise stall the scan it's meant to speed up
MAX_INFO_LOOKUPS = 40


# ── Sector-ETF returns ───────────────────────────────────────────────────────

def fetch_sector_returns() -> dict | None:
    """{etf: {"r1m": pct, "r3m": pct}} for the 11 sector ETFs + SPY, from one
    batch download. None on any failure — caller fails open."""
    import yfinance as yf
    symbols = sorted(set(SECTOR_ETF.values())) + ["SPY"]
    try:
        closes = yf.download(symbols, period="4mo", interval="1d",
                             progress=False, auto_adjust=True)["Close"]
        closes = closes.dropna(how="all")
        if closes.empty:
            return None
        out = {}
        for sym in symbols:
            s = closes[sym].dropna()
            if len(s) < 22:
                continue
            last = float(s.iloc[-1])
            r1m = (last / float(s.iloc[-22]) - 1) * 100
            i3m = -64 if len(s) >= 64 else 0
            r3m = (last / float(s.iloc[i3m]) - 1) * 100
            out[sym] = {"r1m": round(r1m, 2), "r3m": round(r3m, 2)}
        return out if "SPY" in out else None
    except Exception as e:
        log.warning("Sector ETF fetch failed: %s", e)
        return None


# ── Ticker→sector cache ──────────────────────────────────────────────────────

def _load_sector_cache() -> dict:
    try:
        return json.loads(SECTOR_CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_sector_cache(cache: dict) -> None:
    try:
        SECTOR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECTOR_CACHE_PATH.write_text(json.dumps(cache, sort_keys=True))
    except Exception as e:
        log.warning("Could not persist sector cache: %s", e)


def _harvest_sectors_from_scans(missing: set[str], max_files: int = 5) -> dict:
    """Ticker→sector for `missing` names, read from recent scan CSVs — free
    compared to per-ticker .info calls."""
    import csv
    found: dict[str, str] = {}
    files = sorted(OUTPUT_DIR.glob("stock_scan_*.csv"),
                   key=lambda f: f.stat().st_mtime, reverse=True)[:max_files]
    for path in files:
        if not missing - found.keys():
            break
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    t, sector = row.get("Ticker"), row.get("Sector")
                    if t in missing and t not in found and sector:
                        found[t] = sector
        except Exception as e:
            log.debug("Sector harvest skipped %s (%s)", path.name, e)
    return found


def get_ticker_sectors(tickers: list[str]) -> dict[str, str | None]:
    """Resolve each ticker's sector: cache → recent scan CSVs → capped
    yf .info lookups. Unresolved tickers map to None (kept by the filter)."""
    cache = _load_sector_cache()
    result: dict[str, str | None] = {t: cache.get(t) for t in tickers}
    missing = {t for t, s in result.items() if s is None}

    if missing:
        harvested = _harvest_sectors_from_scans(missing)
        result.update(harvested)
        missing -= harvested.keys()

    looked_up = {}
    if missing:
        import yfinance as yf
        for t in sorted(missing)[:MAX_INFO_LOOKUPS]:
            try:
                sector = yf.Ticker(t).info.get("sector")
            except Exception:
                sector = None
            if sector:
                looked_up[t] = sector
        result.update(looked_up)
        skipped = len(missing) - min(len(missing), MAX_INFO_LOOKUPS)
        if skipped:
            log.info("Sector lookup capped: %d ticker(s) unresolved this run "
                     "(kept in scan; cache fills over time)", skipped)

    newly_resolved = {t: result[t] for t in tickers
                      if result[t] is not None and cache.get(t) != result[t]}
    if newly_resolved:
        cache.update(newly_resolved)
        _save_sector_cache(cache)
    return result


# ── The funnel ───────────────────────────────────────────────────────────────

def apply_market_funnel(tickers: list[str]) -> dict:
    """
    Regime + sector-strength pre-filter. Returns:
      {"applied": bool, "tickers": [...], "regime": str, "sectors": [...],
       "ranked": [...], "dropped": int, "unknown": int, "note": str}
    On any missing input, applied=False and tickers pass through unchanged.
    """
    passthrough = {"applied": False, "tickers": tickers, "regime": None,
                   "sectors": [], "ranked": [], "dropped": 0, "unknown": 0}

    etf_returns = fetch_sector_returns()
    if not etf_returns:
        return {**passthrough, "note": "sector ETF data unavailable — scanning full universe"}

    ranked = rank_sectors(etf_returns)
    if not ranked:
        return {**passthrough, "note": "could not rank sectors — scanning full universe"}

    try:
        from stockanalysis.scanners.market_movers import market_pulse
        from stockanalysis.core.market_regime import compute_regime
        regime = compute_regime(pulse=market_pulse(with_catalysts=False))
    except Exception as e:
        log.warning("Regime fetch failed (%s) — using Neutral selectivity", e)
        regime = {"regime": "Neutral"}

    sectors = select_sectors(ranked, regime["regime"])
    sector_of = get_ticker_sectors(tickers)
    split = filter_universe(tickers, sector_of, sectors)

    note = (f"{regime['regime']} regime → top {len(sectors)} sectors "
            f"({', '.join(sectors)}): {len(split['kept'])}/{len(tickers)} tickers kept, "
            f"{len(split['dropped'])} filtered out"
            + (f", {len(split['unknown'])} unknown-sector kept" if split["unknown"] else ""))
    log.info("Market funnel: %s", note)

    return {"applied": True, "tickers": split["kept"], "regime": regime["regime"],
            "sectors": sectors, "ranked": ranked, "dropped": len(split["dropped"]),
            "unknown": len(split["unknown"]), "note": note}
