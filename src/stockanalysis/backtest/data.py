"""
data.py
=======
Bulk daily-bar download for the backtest, with a per-ticker cache in
data/cache/. Bars are dividend/split-adjusted (auto_adjust=True) — the same
convention the live scanner's metrics use, so backtested indicators match.

Cache format is pickle ({"start", "end", "df"}) rather than parquet because
pyarrow isn't in this environment; swap to parquet if it gets installed.

Usage:
    from stockanalysis.backtest.data import load_daily
    daily = load_daily(["NVDA", "AMD"], start=date(2024,1,1), end=date(2026,7,1))
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"


def _cache_path(ticker: str, cache_dir: Path) -> Path:
    return cache_dir / f"{ticker.upper()}.pkl"


def _load_cached(ticker: str, start: date, end: date, cache_dir: Path) -> pd.DataFrame | None:
    """Return the cached frame if it covers [start, end], else None."""
    path = _cache_path(ticker, cache_dir)
    if not path.exists():
        return None
    try:
        blob = pd.read_pickle(path)
        # Compare against the RANGE THAT WAS REQUESTED when caching, not the
        # data's own min/max — a ticker that IPO'd mid-range would otherwise
        # look like a cache miss forever.
        if blob["start"] <= start and blob["end"] >= end:
            df = blob["df"]
            return df.loc[(df.index.date >= start) & (df.index.date <= end)]
    except Exception as e:
        log.debug("%s: cache read failed (%s)", ticker, e)
    return None


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Drop non-trading rows and reduce to the OHLCV the pipeline uses."""
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[cols].dropna(subset=["Close"])
    return df


def load_daily(tickers: list[str], start: date, end: date,
               cache_dir: Path = CACHE_DIR, refresh: bool = False) -> dict[str, pd.DataFrame]:
    """
    Daily adjusted OHLCV per ticker for [start, end]. Cached tickers are read
    locally; the rest are fetched in one bulk yf.download call. Tickers with
    no data (delisted, bad symbol) are omitted from the result with a warning.
    """
    tickers = [t.upper() for t in tickers]
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []

    for t in tickers:
        cached = None if refresh else _load_cached(t, start, end, cache_dir)
        if cached is not None and not cached.empty:
            out[t] = cached
        else:
            missing.append(t)

    if missing:
        log.info("Downloading %d ticker(s): %s%s", len(missing),
                 ", ".join(missing[:8]), "…" if len(missing) > 8 else "")
        raw = yf.download(
            missing, start=start, end=end + timedelta(days=1),   # yf end is exclusive
            interval="1d", auto_adjust=True, group_by="ticker",
            threads=True, progress=False,
        )
        for t in missing:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = _clean(df)
            except Exception:
                df = pd.DataFrame()
            if df.empty:
                log.warning("%s: no data for %s → %s — skipping", t, start, end)
                continue
            pd.to_pickle({"start": start, "end": end, "df": df},
                         _cache_path(t, cache_dir))
            out[t] = df

    return out


def load_benchmark(start: date, end: date, symbol: str = "QQQ",
                   cache_dir: Path = CACHE_DIR, refresh: bool = False) -> pd.DataFrame:
    """The benchmark series for RS — same cache and adjustment convention."""
    data = load_daily([symbol], start, end, cache_dir=cache_dir, refresh=refresh)
    if symbol not in data:
        raise RuntimeError(f"benchmark {symbol} download failed")
    return data[symbol]
