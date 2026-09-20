"""
scan.py — orchestration: discover, fetch, evaluate, rank
=======================================================
The only module that knows both about the network and about the scoring.
Everything above it (§1-§17) is pure; datafeed below it is pure I/O.

Fetch order is deliberate
--------------------------
Sector ETFs cannot be fetched until sectors are known, and sectors come
from `.info`, so the context download has to wait for the info pass. Doing
it in the other order would mean either fetching all eleven sector ETFs
every run or discovering mid-loop that XBI is missing and fetching it
per-ticker — one wasteful, one throttled.

Candidates are pruned before the expensive calls
-------------------------------------------------
The screen returns up to 200 names; bars, info and news for all of them
would be several hundred requests and Yahoo would start refusing partway
through, leaving a scan that is not just slow but silently incomplete.
So the list is cut to `limit` on the two facts the screen already gives —
absolute move and dollar volume — before anything per-ticker is fetched.
This is a real limitation and it is stated in the output: a name that
gapped 4% on huge volume can be pushed out of the window by twenty names
that gapped 40% on small volume.
"""

from __future__ import annotations

import logging

from stockanalysis.core.daytrade import datafeed as D
from stockanalysis.core.daytrade import engine as E
from stockanalysis.core.daytrade import plan as P
from stockanalysis.core.daytrade import profiles as PR
from stockanalysis.core.daytrade import regime as R
from stockanalysis.core.daytrade._common import f, sessions_in, truncate_at

log = logging.getLogger(__name__)


def _bar_rank(bars_1m, day) -> float:
    """Cheap ranking for a watchlist, computed from bars already fetched.

    The screen path can rank before fetching anything because the screen
    itself returns move and volume. A watchlist returns nothing — so a
    476-name list would otherwise take the full `.info` + news pass on
    every symbol, which Yahoo throttles into uselessness long before it
    finishes. Bars are batch-downloaded and effectively free, so the
    ranking is derived from them instead and the expensive per-ticker pass
    only runs on the survivors.

    Same shape as `_prescreen_rank`: move × liquidity, multiplicatively, so
    neither a big move on no volume nor heavy turnover with no move
    survives on its own.
    """
    from stockanalysis.core.daytrade._common import (
        MARKET_CLOSE, MARKET_OPEN, session_slice)
    if bars_1m is None or bars_1m.empty:
        return 0.0
    sess = session_slice(bars_1m, day, MARKET_OPEN, MARKET_CLOSE)
    if sess.empty or len(sess) < 2:
        return 0.0
    first, last = float(sess["Open"].iloc[0]), float(sess["Close"].iloc[-1])
    if first <= 0:
        return 0.0
    move = abs(last - first) / first * 100.0
    dollar_vol = float((sess["Close"] * sess["Volume"]).sum())
    if dollar_vol < 1_000_000:
        return 0.0
    return move * min(dollar_vol, 5e8) ** 0.5


def _prescreen_rank(row: dict) -> float:
    """Cheap ordering from screen data alone: move × dollar liquidity.

    Multiplicative so that neither a big move on no volume nor a big
    turnover with no move survives on its own — §18's argument, applied at
    the cheapest possible stage.
    """
    move = abs(f(row.get("change_pct")) or 0.0)
    price = f(row.get("price")) or 0.0
    volume = f(row.get("day_volume")) or 0.0
    dollar_vol = price * volume
    if dollar_vol < 1_000_000:
        return 0.0
    return move * min(dollar_vol, 5e8) ** 0.5


def run(limit: int = 25, asof=None, settings: dict | None = None,
        tickers: list[str] | None = None, at_time=None, profile: str | None = None,
        progress_cb=None) -> dict:
    """Run the full scan. Returns {"rows", "regime", "asof", "notes"}.

    `at_time` replays the session as of a wall-clock time — 10:15 shows
    what the scanner would have said at 10:15, with no knowledge of the
    rest of the day. That is the only way to see this engine's real
    behaviour outside market hours: read at the close, every candidate has
    already made its move, sits near its high with resistance overhead,
    and correctly scores a poor R:R. A scanner judged only on end-of-day
    data would look far more conservative than it actually is.
    """
    settings = settings or P.load_settings()
    notes: list[str] = []
    # "auto" means each row is judged against the profile for its own market
    # cap, which is what makes a mixed-cap watchlist coherent. A named
    # profile forces one calibration on everything — the right choice when
    # screening, since the screen itself is then bounded to that cap band.
    prof = PR.by_key(profile) if profile and profile != "auto" else None
    notes.append(f"profile: {prof['label']} ({prof['description']})" if prof
                 else "profile: auto — each name judged against its own cap band")

    def progress(msg):
        log.info(msg)
        if progress_cb:
            progress_cb(msg)

    # ── §1 discovery ────────────────────────────────────────────────────
    if tickers:
        candidates = [{"ticker": t, "direction": "up"} for t in tickers]
        notes.append(f"explicit ticker list ({len(tickers)}) — §1 screen skipped")
    else:
        screen_profile = prof or PR.DEFAULT
        progress(f"screening {screen_profile['label'].lower()} movers…")
        candidates = D.screen_movers(profile=screen_profile)
        if not candidates:
            return {"rows": [], "regime": None, "asof": None,
                    "notes": ["screen returned nothing — market data unavailable"]}
        candidates.sort(key=_prescreen_rank, reverse=True)
        if len(candidates) > limit:
            notes.append(f"{len(candidates)} names passed the screen; "
                         f"deepest analysis run on the top {limit} by move × liquidity")
            candidates = candidates[:limit]

    symbols = [c["ticker"] for c in candidates]
    by_ticker = {c["ticker"]: c for c in candidates}

    # ── bars ────────────────────────────────────────────────────────────
    progress(f"fetching intraday bars for {len(symbols)} names…")
    bars_1m = D.fetch_bars(symbols, "1m", "7d")
    bars_5m = D.fetch_bars(symbols, "5m", "60d")
    daily = D.fetch_daily(symbols, "1y")

    # The session everything is measured against: the most recent date any
    # candidate printed. Taken from the data rather than the clock so a
    # weekend, a holiday or a late run all resolve to the same real session.
    if asof is None:
        all_days = sorted({d for df in bars_1m.values() for d in sessions_in(df)})
        if not all_days:
            return {"rows": [], "regime": None, "asof": None,
                    "notes": notes + ["no intraday bars returned for any candidate"]}
        asof = all_days[-1]

    if at_time is not None:
        bars_1m = {t: truncate_at(df, asof, at_time) for t, df in bars_1m.items()}
        bars_5m = {t: truncate_at(df, asof, at_time) for t, df in bars_5m.items()}
        bars_1m = {t: df for t, df in bars_1m.items() if df is not None and not df.empty}
        notes.append(f"as-of replay: session truncated at {at_time.strftime('%H:%M')} ET — "
                     "nothing after that time was visible to the scan")

    # ── prune a watchlist down before the expensive pass ────────────────
    # Only for explicit ticker lists; the screen path already pruned on the
    # screen's own move/volume data before any fetch happened.
    if tickers and len(symbols) > limit:
        ranked = sorted(symbols, key=lambda s: _bar_rank(bars_1m.get(s), asof),
                        reverse=True)
        dropped = len(symbols) - limit
        symbols = ranked[:limit]
        by_ticker = {t: by_ticker[t] for t in symbols}
        notes.append(f"{dropped} of the list's names dropped before the "
                     f"fundamentals pass — deepest analysis run on the top "
                     f"{limit} by move × liquidity")

    # ── info, news, then context ────────────────────────────────────────
    progress("fetching fundamentals and headlines…")
    infos, news = {}, {}
    for i, sym in enumerate(symbols, 1):
        infos[sym] = D.fetch_info(sym)
        news[sym] = D.fetch_news(sym)
        if i % 10 == 0:
            progress(f"  …{i}/{len(symbols)}")

    needed_etfs = {D.sector_etf(infos[s].get("sector"), infos[s].get("industry"))
                   for s in symbols}
    needed_etfs.discard(None)
    progress("fetching benchmarks, VIX and sector ETFs…")
    context = D.fetch_context(sorted(needed_etfs))
    if at_time is not None:
        # The benchmarks must be cut to the same instant, or §7 would
        # compare a 10:15 stock against a 16:00 SPY and §15 would read a
        # regime from a tape the scan could not have seen.
        context = {t: truncate_at(df, asof, at_time) for t, df in context.items()}

    regime = R.compute(context, asof)

    # ── evaluate ────────────────────────────────────────────────────────
    progress("scoring candidates…")
    rows = []
    for sym in symbols:
        if sym not in bars_1m:
            notes.append(f"{sym}: no intraday bars — skipped")
            continue
        try:
            result = E.evaluate(
                sym, by_ticker.get(sym, {}), infos.get(sym, {}), news.get(sym, []),
                bars_1m.get(sym), bars_5m.get(sym), daily.get(sym),
                context, regime, settings, asof=asof, profile=prof)
        except Exception as e:                      # one bad symbol must not
            log.warning("%s: evaluation failed: %s", sym, e)   # kill the scan
            notes.append(f"{sym}: evaluation failed ({e})")
            continue
        if result:
            rows.append(result)

    rows.sort(key=E.rank_key)
    # Echoed back so the caller and the page can show what was requested
    # rather than re-deriving it from the rows.
    profile_requested = profile or "small"

    live = any(r.get("is_live") for r in rows)
    if not live and rows:
        notes.insert(0, f"market closed — this is the completed {asof} session, "
                        "not a live scan; every level is that session's close")
    return {"rows": rows, "regime": regime, "asof": asof, "notes": notes,
            "settings": settings, "profile_requested": profile_requested}
