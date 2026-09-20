"""
earnings_today.py
=================
Find every ticker across data/watchlists.json reporting earnings today (or
within a small forward window) and persist them as the ``earnings_today``
watchlist, so the list can be ticked in the Scanner like any other universe.

Companion to core.fifty_two_week — same shape (screen -> watchlist -> scan),
same reason: once the list exists, ticking it in "Run a Scan" runs the whole
normal pipeline over exactly those names.

yfinance has no bulk earnings-calendar endpoint, so this is one
``Ticker.calendar`` call per ticker, threaded. The date parsing mirrors
core.metrics.get_metrics' EARNINGS block so both agree on what
"EarningsDate" means for a given name.

Dates only — yfinance's calendar does not carry a reliable before/after-open
release TIME (see core.earnings_alerts), so none is reported here.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Callable, Iterable

import pytz

log = logging.getLogger(__name__)

LIST_NAME = "earnings_today"
MARKET_TZ = pytz.timezone("America/New_York")

# Yahoo throttles hard above this; get_metrics' retry helper covers the
# per-call case but keeping concurrency modest avoids tripping it at all.
MAX_WORKERS = 8


def _earnings_date(ticker: str) -> date | None:
    """Next earnings date for `ticker`, or None. Never raises."""
    import yfinance as yf

    try:
        cal = yf.Ticker(ticker).calendar
    except Exception as e:
        log.debug("%s: calendar fetch failed (%s)", ticker, e)
        return None

    raw = None
    try:
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or []
            raw = dates[0] if dates else None
        elif cal is not None and not getattr(cal, "empty", True) \
                and "Earnings Date" in cal.index:
            vals = cal.loc["Earnings Date"].dropna().tolist()
            raw = vals[0] if vals else None
    except Exception as e:
        log.debug("%s: calendar parse failed (%s)", ticker, e)
        return None

    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _all_watchlist_tickers(universes: Iterable[str] | None) -> list[str]:
    """Every unique ticker across the named lists, or across all of
    data/watchlists.json when `universes` is None."""
    from stockanalysis.reporting.research import load_watchlists

    lists = load_watchlists()
    names = list(universes) if universes else list(lists)
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        for t in lists.get(name) or []:
            t = (t or "").strip().upper()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def scan_earnings_today(universes: Iterable[str] | None = None,
                        days_ahead: int = 0,
                        progress_cb: Callable[[str, int | None, int | None], None] | None = None,
                        save: bool = True) -> dict:
    """
    Screen watchlist tickers for earnings today.

    universes   : list names to search (default: every list in watchlists.json)
    days_ahead  : 0 = today only; N includes the next N calendar days
    save        : write the result to the ``earnings_today`` watchlist

    Returns {"tickers": [...], "rows": [...], "scanned": int,
             "no_date": [...], "today": "YYYY-MM-DD", ...}
    """
    def emit(label: str, done: int | None = None, total: int | None = None) -> None:
        if progress_cb:
            progress_cb(label, done, total)

    tickers = _all_watchlist_tickers(universes)
    if not tickers:
        raise ValueError("no tickers found in the selected watchlist(s)")

    today = datetime.now(MARKET_TZ).date()
    horizon = today + timedelta(days=max(0, days_ahead))
    emit(f"checking earnings dates for {len(tickers)} ticker(s)", 0, len(tickers))

    results: dict[str, date | None] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_earnings_date, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                results[t] = fut.result()
            except Exception:
                results[t] = None
            done += 1
            if done % 25 == 0 or done == len(tickers):
                emit(f"checked {done}/{len(tickers)}", done, len(tickers))

    rows = []
    no_date = []
    for t in tickers:                      # preserve input order
        ed = results.get(t)
        if ed is None:
            no_date.append(t)
            continue
        if today <= ed <= horizon:
            rows.append({"Ticker": t, "EarningsDate": ed.isoformat(),
                         "Days_Away": (ed - today).days,
                         "Is_Today": ed == today})

    rows.sort(key=lambda r: (r["Days_Away"], r["Ticker"]))
    found = [r["Ticker"] for r in rows]

    if save:
        from stockanalysis.reporting.research import load_watchlists, save_watchlists
        wl = load_watchlists()
        wl[LIST_NAME] = found
        save_watchlists(wl)
        log.info("earnings screen: wrote %d ticker(s) to %s", len(found), LIST_NAME)

    emit(f"done — {len(found)} reporting", len(tickers), len(tickers))
    return {"tickers": found, "rows": rows, "scanned": len(tickers),
            "no_date": no_date, "today": today.isoformat(),
            "days_ahead": max(0, days_ahead),
            "universes": list(universes) if universes else ["<all>"]}


def main() -> None:
    """Standalone: python -m stockanalysis.core.earnings_today"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = scan_earnings_today(progress_cb=lambda s, d, n: log.info("  %s", s))
    print(f"\nas-of {res['today']} — scanned {res['scanned']}, "
          f"{len(res['no_date'])} with no date on file")
    print(f"\nREPORTING (window +{res['days_ahead']}d): {len(res['tickers'])}")
    for r in res["rows"]:
        flag = "TODAY" if r["Is_Today"] else f"+{r['Days_Away']}d "
        print(f"  {flag} {r['Ticker']:<6} {r['EarningsDate']}")


if __name__ == "__main__":
    main()
