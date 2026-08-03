"""
fifty_two_week.py
=================
Screen a source universe for stocks at (or near) their 52-week high and
52-week low, then persist the two sides as the ``52_week_high`` /
``52_week_low`` watchlists in data/watchlists.json.

Once written, both lists show up in the Scanner's universe panel like any
other watchlist, so ticking ``52_week_high`` and running a scan puts the
names through the normal pipeline — which already calls
:func:`stockanalysis.core.put_candidate.compute_put_candidate` inside
``get_metrics()``. That is what grades the fresh-high names for exhaustion
(Put_Candidate / Put_Score / Put_Reason land in the scan CSV).

Conventions match core.metrics.compute_daily_metrics so the two agree:
  * 52W High = intraday ``High.max()``  over the window
  * 52W Low  = intraday ``Low.min()``   over the window
  * current price = last close
  * Dist_52W_High% = (price / 52W High - 1) * 100   (<= 0)
  * Pct_From_52W_Low% = (price / 52W Low - 1) * 100 (>= 0)

Bars are fetched with ``auto_adjust=True`` (FIX-L in metrics.py) so
ex-dividend drops don't distort the 52-week extremes.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

log = logging.getLogger(__name__)

HIGH_LIST_NAME = "52_week_high"
LOW_LIST_NAME  = "52_week_low"

# A name needs at least this many bars for a "52-week" extreme to mean
# anything — recent IPOs otherwise print a 52-week high on day three.
MIN_BARS = 200

DEFAULT_SOURCE = ("sp500",)


def _source_tickers(universes: Iterable[str]) -> list[str]:
    """Order-preserving, deduped union of the named watchlists/universes."""
    from stockanalysis.reporting.research import load_watchlists

    lists = load_watchlists()
    out: list[str] = []
    seen: set[str] = set()
    for name in universes:
        for t in lists.get(name) or []:
            t = (t or "").strip().upper()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def scan_52_week(universes: Iterable[str] | None = None,
                 near_high_pct: float = 2.0,
                 near_low_pct: float = 2.0,
                 progress_cb: Callable[[str, int | None, int | None], None] | None = None,
                 save: bool = True) -> dict:
    """
    Screen `universes` (default: sp500) for 52-week highs and lows.

    near_high_pct : include names trading within this % of the 52W high
                    (0 = only names printing a new high today)
    near_low_pct  : include names trading within this % of the 52W low
    save          : write the two lists into data/watchlists.json

    Returns a summary dict:
        {"high": [...], "low": [...], "high_rows": [...], "low_rows": [...],
         "scanned": int, "skipped": [...], "asof": "YYYY-MM-DD"}
    """
    import pandas as pd
    import yfinance as yf

    def emit(label: str, done: int | None = None, total: int | None = None) -> None:
        if progress_cb:
            progress_cb(label, done, total)

    names = list(universes or DEFAULT_SOURCE)
    tickers = _source_tickers(names)
    if not tickers:
        raise ValueError(f"no tickers in universe(s) {', '.join(names)!r}")

    emit(f"fetching 1y bars for {len(tickers)} ticker(s)", 0, len(tickers))
    # One batched request instead of len(tickers) round-trips — this screen
    # only needs OHLC, not the per-ticker .info that get_metrics fetches.
    px = yf.download(tickers, period="1y", interval="1d", auto_adjust=True,
                     progress=False, threads=True, group_by="column")
    if px.empty:
        raise RuntimeError("yfinance returned no data for the source universe")

    # A single ticker collapses the column MultiIndex — normalise to frames
    # keyed by field so both shapes read the same below.
    if isinstance(px.columns, pd.MultiIndex):
        close, high, low = px["Close"], px["High"], px["Low"]
    else:
        close = px[["Close"]].rename(columns={"Close": tickers[0]})
        high  = px[["High"]].rename(columns={"High": tickers[0]})
        low   = px[["Low"]].rename(columns={"Low": tickers[0]})

    asof = str(close.index[-1].date())
    high_rows: list[dict] = []
    low_rows: list[dict] = []
    skipped: list[str] = []
    scanned = 0

    for i, t in enumerate(tickers):
        if progress_cb and i % 25 == 0:
            emit(f"screening {t}", i, len(tickers))
        if t not in close.columns:
            skipped.append(t)
            continue
        # Per-ticker dropna: a merged calendar leaves all-NaN rows for names
        # that didn't trade every session, which would poison rolling stats.
        c = close[t].dropna()
        h = high[t].dropna()
        l = low[t].dropna()
        if len(c) < MIN_BARS or h.empty or l.empty:
            skipped.append(t)
            continue

        scanned += 1
        price = float(c.iloc[-1])
        hi52 = float(h.max())
        lo52 = float(l.min())
        if hi52 <= 0 or lo52 <= 0:
            skipped.append(t)
            continue

        dist_high = (price / hi52 - 1) * 100.0     # <= 0
        pct_off_low = (price / lo52 - 1) * 100.0   # >= 0
        hi_date = str(h.idxmax().date())
        lo_date = str(l.idxmin().date())

        if dist_high >= -abs(near_high_pct):
            high_rows.append({
                "Ticker": t, "Price": round(price, 2),
                "52W High": round(hi52, 2),
                "Dist_52W_High%": round(dist_high, 2),
                "52W_High_Date": hi_date,
                # "made a new high" = the window's peak was set in the latest
                # session. Comparing price >= hi52 would be wrong: hi52 is the
                # intraday High and price is the close, so it'd be False even
                # on a name that printed a fresh high minutes ago.
                "New_High": hi_date == asof,
            })
        if pct_off_low <= abs(near_low_pct):
            low_rows.append({
                "Ticker": t, "Price": round(price, 2),
                "52W Low": round(lo52, 2),
                "Pct_From_52W_Low%": round(pct_off_low, 2),
                "52W_Low_Date": lo_date,
                "New_Low": lo_date == asof,
            })

    # Closest to the extreme first — the top of each list is the freshest.
    high_rows.sort(key=lambda r: -r["Dist_52W_High%"])
    low_rows.sort(key=lambda r: r["Pct_From_52W_Low%"])
    highs = [r["Ticker"] for r in high_rows]
    lows = [r["Ticker"] for r in low_rows]

    if save:
        from stockanalysis.reporting.research import load_watchlists, save_watchlists
        wl = load_watchlists()
        wl[HIGH_LIST_NAME] = highs
        wl[LOW_LIST_NAME] = lows
        save_watchlists(wl)
        log.info("52-week screen: wrote %d high / %d low to watchlists.json",
                 len(highs), len(lows))

    emit(f"done — {len(highs)} high, {len(lows)} low", len(tickers), len(tickers))
    return {"high": highs, "low": lows,
            "high_rows": high_rows, "low_rows": low_rows,
            "scanned": scanned, "skipped": skipped,
            "universes": names, "asof": asof,
            "near_high_pct": abs(near_high_pct), "near_low_pct": abs(near_low_pct)}


def main() -> None:
    """Standalone: python -m stockanalysis.core.fifty_two_week"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = scan_52_week(progress_cb=lambda s, d, n: log.info("  %s", s))
    print(f"\nas-of {res['asof']} — scanned {res['scanned']}, "
          f"skipped {len(res['skipped'])}")
    print(f"\n52-WEEK HIGHS (within {res['near_high_pct']}%): {len(res['high'])}")
    for r in res["high_rows"][:25]:
        flag = "NEW" if r["New_High"] else "   "
        print(f"  {flag} {r['Ticker']:<6} {r['Price']:>9,.2f}  "
              f"{r['Dist_52W_High%']:>6.2f}%  high {r['52W_High_Date']}")
    print(f"\n52-WEEK LOWS (within {res['near_low_pct']}%): {len(res['low'])}")
    for r in res["low_rows"][:25]:
        flag = "NEW" if r["New_Low"] else "   "
        print(f"  {flag} {r['Ticker']:<6} {r['Price']:>9,.2f}  "
              f"+{r['Pct_From_52W_Low%']:>5.2f}%  low {r['52W_Low_Date']}")


if __name__ == "__main__":
    main()
