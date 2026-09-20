"""
scan_sector_leaders.py
======================
Fetch + orchestration for the daily bullish/bearish sector-leader scan
(scoring lives in core.sector_leaders):

    MARKET  →  SECTOR  →  INDUSTRY  →  STOCK  →  SETUP

Produces two separate rankings — BULLISH LEADERS and BEARISH LEADERS — rather
than one combined list, because a combined ranking on a rotation day buries
whichever side is smaller, and the whole reason to run this before the open is
to see both.

Fetch plan (batched, because ~600 names is the whole point of batching):
  1. benchmarks + VIX + 10Y  — one download
  2. every group's proxy ETF — one download
  3. all group members       — chunked downloads, 1y daily
  4. 5-minute bars for the finalists only — VWAP, opening range and intraday
     RVOL are the day-trade half of the output and are far too expensive to
     fetch for the full universe

Honest gaps, stated not filled: exchange internals (TICK, TRIN, VOLD, the full
NYSE advance-decline line, market-wide new highs/lows) have no free feed, so
breadth here is universe breadth over the constituent lists we actually hold.
Options positioning is likewise absent. Neither is estimated.

    python3 -m stockanalysis.scanners.scan_sector_leaders --json out.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from stockanalysis.core import sector_leaders as sl
from stockanalysis.core.daytrade.datafeed import fetch_bars

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WATCHLISTS = PROJECT_ROOT / "data" / "watchlists.json"

CHUNK = 120


# ── universe assembly ────────────────────────────────────────────────────────

def load_groups() -> dict:
    """Resolve every group's members. GICS sectors come from watchlists.json
    (the S&P 500 constituents the workstation already classifies); thematic
    groups carry curated liquid member lists in core."""
    wl = json.loads(WATCHLISTS.read_text()) if WATCHLISTS.exists() else {}

    def resolve(key):
        node = wl.get(key)
        if isinstance(node, list):
            return list(node)
        if isinstance(node, dict):
            out = list(node.get("_tickers") or [])
            for k, v in node.items():
                if k != "_tickers" and isinstance(v, list):
                    out += v
            return list(dict.fromkeys(out))
        return []

    groups = {}
    for name, cfg in sl.GICS_SECTORS.items():
        groups[name] = {"etf": cfg["etf"], "tier": "sector", "parent": None,
                        "members": resolve(cfg["wl"])}
    for name, cfg in sl.INDUSTRY_GROUPS.items():
        members = list(cfg["members"])
        if cfg.get("wl"):
            members = list(dict.fromkeys(members + resolve(cfg["wl"])))
        groups[name] = {"etf": cfg["etf"], "tier": "industry",
                        "parent": cfg.get("parent"), "members": members}
    return groups


def fetch_daily_chunked(tickers: list[str], period: str = "1y",
                        progress_cb=None, label: str = "fetching bars") -> dict:
    frames = {}
    tickers = list(dict.fromkeys(t for t in tickers if t))
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        got = fetch_bars(chunk, interval="1d", period=period, prepost=False)
        frames.update(got)
        print(f"  fetched {len(frames)}/{len(tickers)}", file=sys.stderr)
        if progress_cb:
            progress_cb(label, min(i + CHUNK, len(tickers)), len(tickers))
    return frames


# ── proxy-ETF holdings ───────────────────────────────────────────────────────

def fetch_top_holdings(etfs: list[str], limit: int = 10) -> dict:
    """{etf: [{ticker, name, weight}]} for each group's proxy fund.

    Reuses core.etf_profile's fetcher so the /leaders page and the ETF views
    show the same holdings from the same source rather than two readings of
    the same fund. One `.funds_data` call per ETF — eighteen calls, which is
    noise next to the ~570-name bar download this runs beside.

    Best-effort per fund: a proxy with no published holdings returns an empty
    list and the column says so, rather than the scan failing over a
    presentation detail.
    """
    import yfinance as yf
    from stockanalysis.core.etf_profile import _fetch_holdings

    out = {}
    for etf in etfs:
        try:
            out[etf] = _fetch_holdings(yf.Ticker(etf), limit=limit)
        except Exception as e:
            log.warning("holdings for %s failed: %s", etf, e)
            out[etf] = []
    return out


def attach_holding_moves(holdings: dict, metrics_by_ticker: dict) -> dict:
    """Add each holding's 1-day, 5-day and 20-day move, then sort the list by
    today's move, strongest first.

    Allocation weight decides WHICH ten names appear — they are the fund's
    ten biggest positions, which is what "top holdings" means. The ORDER is
    by percent change, because once the ten are fixed the useful question is
    which of them moved, not which is biggest: the weights barely change
    week to week and the moves are the reason to look.
    """
    out = {}
    for etf, rows in holdings.items():
        enriched = []
        for h in rows:
            m = metrics_by_ticker.get(h["ticker"]) or {}
            enriched.append({**h,
                             "r1d": m.get("r1d"), "r5d": m.get("r5d"),
                             "r20d": m.get("r20d"),
                             "above_sma50": m.get("above_sma50"),
                             "quote": "measured" if m else "no bars"})
        enriched.sort(key=lambda h: (h["r1d"] is None, -(h["r1d"] or 0)))
        out[etf] = enriched
    return out


# ── market regime (measured) ─────────────────────────────────────────────────

CYCLICAL_ETFS  = ["XLK", "XLY", "XLI", "XLF"]
DEFENSIVE_ETFS = ["XLP", "XLU", "XLV"]

# Score bands. The maximum reachable score is ~17.5, so STRONG BULLISH is set
# where the standing trend AND the day's rotation AND breadth all agree — an
# earlier cut put the cutoff at +6, which every intact uptrend cleared on the
# moving averages alone and which therefore never distinguished anything.
STRONG_BULL, BULL, BEAR, STRONG_BEAR = 11.0, 4.0, -4.0, -11.0


def rates_snapshot(df) -> dict | None:
    """10-year yield level and daily change.

    ^TNX gets its own path because Yahoo returns only a couple of dozen bars
    for it on multi-year requests — not enough for symbol_metrics, and plenty
    for a level and a one-day change. The series is the yield in percent
    already; it is NOT yield x 10.
    """
    if df is None or len(df) < 2:
        return None
    c = df["Close"].dropna()
    if len(c) < 2:
        return None
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    return {"yield_pct": round(last, 3), "prev_pct": round(prev, 3),
            "change_bps": round((last - prev) * 100, 1)}


def score_market(mx: dict, sector_metrics: dict, universe_metrics: list[dict],
                 etf_mx: dict, rates: dict | None) -> dict:
    """Classify the tape on measured index / volatility / rates / breadth /
    rotation inputs.

    Returns a score, the five-way label, and every driver that built it, so
    the classification can be argued with rather than trusted. Standing trend
    (where price sits against its moving averages) and the day's character
    (rotation, advancing share, volume) are both represented, because a
    defensive-rotation session inside an intact uptrend is exactly the tape
    that a moving-average-only reading calls STRONG BULLISH and a
    today-only reading calls bearish. It is neither.
    """
    score, drivers = 0.0, []

    for sym, w in (("SPY", 1.0), ("QQQ", 0.8), ("IWM", 0.6), ("DIA", 0.4)):
        m = mx.get(sym)
        if not m:
            continue
        s = 0.0
        s += w if m.get("above_ema20") else -w
        s += w if m.get("above_sma50") else -w
        s += w * 0.5 if m.get("above_sma200") else -w * 0.5
        score += s
        drivers.append(
            f"{sym} {m['close']:.2f} {m['r1d']:+.2f}% · "
            f"{'>' if m.get('above_ema20') else '<'}20EMA "
            f"{'>' if m.get('above_sma50') else '<'}50SMA "
            f"{'>' if m.get('above_sma200') else '<'}200SMA → {s:+.1f}")

    spy = mx.get("SPY")
    if spy:
        v = _scale_sym(spy.get("r5d"), -3, 3, 1.5) + _scale_sym(spy.get("r20d"), -5, 5, 1.5)
        score += v
        drivers.append(f"SPY momentum 5d {spy.get('r5d'):+.2f}% / 20d "
                       f"{spy.get('r20d'):+.2f}% → {v:+.1f}")
        rv = spy.get("rvol")
        if rv:
            vv = 0.5 if rv >= 1.15 and (spy.get("r1d") or 0) > 0 else \
                 -0.5 if rv >= 1.15 and (spy.get("r1d") or 0) < 0 else 0.0
            score += vv
            drivers.append(f"SPY volume RVOL {rv:.2f} on a "
                           f"{spy.get('r1d'):+.2f}% session → {vv:+.1f}")

    vix = mx.get("^VIX")
    if vix:
        lvl = vix["close"]
        v = 2.0 if lvl < 15 else 1.0 if lvl < 18 else 0.0 if lvl < 20 else -1.5 if lvl < 25 else -3.0
        score += v
        drivers.append(f"VIX {lvl:.2f} ({vix['r1d']:+.1f}%) → {v:+.1f}")

    if rates:
        bp = rates["change_bps"]
        v = 0.5 if bp < -2 else -0.5 if bp > 4 else 0.0
        score += v
        drivers.append(f"10Y {rates['yield_pct']:.3f}% ({bp:+.1f}bp) → {v:+.1f}")
    else:
        drivers.append("10Y yield → N/A (no data)")

    # rotation: is today's money going to cyclicals or hiding in defensives
    cyc = [etf_mx[e]["r1d"] for e in CYCLICAL_ETFS if etf_mx.get(e)]
    dfn = [etf_mx[e]["r1d"] for e in DEFENSIVE_ETFS if etf_mx.get(e)]
    if cyc and dfn:
        spread = sum(cyc) / len(cyc) - sum(dfn) / len(dfn)
        v = _scale_sym(spread, -1.5, 1.5, 1.0)
        score += v
        drivers.append(f"cyclical−defensive 1d spread {spread:+.2f}pp "
                       f"(cyc {sum(cyc)/len(cyc):+.2f}% vs def {sum(dfn)/len(dfn):+.2f}%) → {v:+.1f}")

    above50 = [m for m in sector_metrics.values() if m and m.get("above_sma50")]
    pct = 100.0 * len(above50) / max(1, len(sector_metrics))
    v = 1.5 if pct >= 70 else 0.5 if pct >= 55 else -0.5 if pct >= 40 else -1.5
    score += v
    drivers.append(f"{len(above50)}/{len(sector_metrics)} sector ETFs > 50SMA ({pct:.0f}%) → {v:+.1f}")

    ub50 = [m for m in universe_metrics if m.get("above_sma50")]
    ub200 = [m for m in universe_metrics if m.get("above_sma200")]
    p50 = 100.0 * len(ub50) / max(1, len(universe_metrics))
    p200 = 100.0 * len(ub200) / max(1, len(universe_metrics))
    v = 2.0 if p50 >= 65 else 1.0 if p50 >= 55 else 0.0 if p50 >= 45 else -1.5 if p50 >= 35 else -2.5
    score += v
    drivers.append(f"universe breadth {p50:.0f}% >50SMA, {p200:.0f}% >200SMA "
                   f"(n={len(universe_metrics)}) → {v:+.1f}")

    # today's advance/decline across the same constituent list. This is
    # universe A/D, NOT the exchange advance-decline line — that has no free
    # feed and is reported as a gap rather than substituted for.
    adv = sum(1 for m in universe_metrics if (m.get("r1d") or 0) > 0)
    padv = 100.0 * adv / max(1, len(universe_metrics))
    v = _scale_sym(padv - 50, -20, 20, 1.0)
    score += v
    drivers.append(f"universe advance/decline {adv}/{len(universe_metrics)} "
                   f"advancing ({padv:.0f}%) → {v:+.1f}")

    qqq = mx.get("QQQ")
    rs_note = "N/A"
    if spy and qqq and spy.get("r20d") is not None and qqq.get("r20d") is not None:
        spread = qqq["r20d"] - spy["r20d"]
        v = 0.5 if spread > 1 else -0.5 if spread < -1 else 0.0
        score += v
        rs_note = f"QQQ−SPY 20d {spread:+.2f}pp"
        drivers.append(f"{rs_note} → {v:+.1f}")

    if score >= STRONG_BULL:
        label = "STRONG BULLISH"
    elif score >= BULL:
        label = "BULLISH"
    elif score > BEAR:
        label = "NEUTRAL"
    elif score > STRONG_BEAR:
        label = "BEARISH"
    else:
        label = "STRONG BEARISH"

    direction = ("bullish" if score >= BULL else
                 "bearish" if score <= BEAR else "neutral")
    return {"score": round(score, 1), "label": label, "direction": direction,
            "strength": min(6.0, abs(score) / 2.0), "drivers": drivers,
            "spy_qqq_rs": rs_note, "rates": rates,
            "universe_breadth": {"pct_above_50sma": round(p50, 1),
                                 "pct_above_200sma": round(p200, 1),
                                 "pct_advancing_today": round(padv, 1),
                                 "n": len(universe_metrics)},
            "sector_etf_breadth_pct": round(pct, 1)}


def _scale_sym(x, lo, hi, pts):
    """Symmetric scale: midpoint → 0, ends → ±pts. None → 0 (no opinion),
    unlike the sector scorers' half-credit, because the market score is a
    signed sum rather than a 0-100 budget."""
    if x is None:
        return 0.0
    mid = (lo + hi) / 2
    span = (hi - lo) / 2
    return max(-1.0, min(1.0, (x - mid) / span)) * pts


# ── intraday (day-trade half) ────────────────────────────────────────────────

def intraday_stats(tickers: list[str]) -> dict:
    """Session VWAP, opening range and intraday RVOL from 5-minute bars.

    Everything here describes the most recent completed session when the scan
    runs after the close — it is not a live quote, and the caller says so.
    """
    bars = fetch_bars(tickers, interval="5m", period="10d", prepost=False)
    out = {}
    for t, df in bars.items():
        if df is None or df.empty:
            continue
        d = df.copy()
        d["day"] = pd.DatetimeIndex(d.index).date
        days = sorted(set(d["day"]))
        if not days:
            continue
        today = d[d["day"] == days[-1]]
        if today.empty:
            continue
        tp = (today["High"] + today["Low"] + today["Close"]) / 3
        vol = today["Volume"]
        vwap = float((tp * vol).sum() / vol.sum()) if vol.sum() else None
        opening = today.head(3)          # first 15 minutes
        or_hi = float(opening["High"].max()) if len(opening) else None
        or_lo = float(opening["Low"].min()) if len(opening) else None
        day_vols = d.groupby("day")["Volume"].sum()
        prior = day_vols.iloc[:-1].tail(20)
        rvol = float(day_vols.iloc[-1] / prior.mean()) if len(prior) and prior.mean() else None
        close = float(today["Close"].iloc[-1])
        out[t] = {
            "session_date": str(days[-1]),
            "vwap": round(vwap, 2) if vwap else None,
            "close_vs_vwap": ("above" if vwap and close > vwap else
                              "below" if vwap else None),
            "opening_range_15m_high": round(or_hi, 2) if or_hi else None,
            "opening_range_15m_low": round(or_lo, 2) if or_lo else None,
            "closed_vs_or": (None if or_hi is None else
                             "above" if close > or_hi else
                             "below" if close < or_lo else "inside"),
            "intraday_rvol": round(rvol, 2) if rvol else None,
        }
    return out


# ── main ─────────────────────────────────────────────────────────────────────

def run(cache: Path | None = None, progress_cb=None) -> dict:
    """cache: optional pickle of the raw bar frames. Fetching ~570 names takes
    minutes and the bars do not change between re-scores on the same evening,
    so iterating on the scoring should not re-hit Yahoo each time."""
    groups = load_groups()

    if cache and cache.exists():
        import pickle
        blob = pickle.loads(cache.read_bytes())
        bench_frames, member_frames = blob["bench"], blob["members"]
        bench_syms = blob["bench_syms"]
        etf_syms = blob["etf_syms"]
        print(f"loaded {len(bench_frames) + len(member_frames)} cached frames",
              file=sys.stderr)
        return _score_all(groups, bench_syms, etf_syms, bench_frames,
                          member_frames, progress_cb)

    print("fetching benchmarks…", file=sys.stderr)
    if progress_cb:
        progress_cb("fetching benchmarks and sector ETFs", 0, 1)
    bench_syms = sl.BENCHMARKS + ["^VIX", "^TNX", "RSP"]
    etf_syms = sorted({g["etf"] for g in groups.values()})
    bench_frames = fetch_daily_chunked(bench_syms + etf_syms, period="2y",
                                       progress_cb=progress_cb,
                                       label="fetching benchmarks")

    all_members = sorted({t for g in groups.values() for t in g["members"]})
    print(f"fetching {len(all_members)} members…", file=sys.stderr)
    member_frames = fetch_daily_chunked(all_members, period="2y",
                                        progress_cb=progress_cb,
                                        label="fetching constituents")

    if cache:
        import pickle
        cache.write_bytes(pickle.dumps(
            {"bench": bench_frames, "members": member_frames,
             "bench_syms": bench_syms, "etf_syms": etf_syms}))

    return _score_all(groups, bench_syms, etf_syms, bench_frames,
                      member_frames, progress_cb)


def _score_all(groups, bench_syms, etf_syms, bench_frames, member_frames,
               progress_cb=None) -> dict:
    mx = {s: sl.symbol_metrics(bench_frames.get(s)) for s in bench_syms}
    mx = {k: v for k, v in mx.items() if v}
    etf_mx = {s: sl.symbol_metrics(bench_frames.get(s)) for s in etf_syms}
    member_mx = {t: sl.symbol_metrics(df) for t, df in member_frames.items()}
    member_mx = {t: m for t, m in member_mx.items() if m}

    gics_etfs = {cfg["etf"]: etf_mx.get(cfg["etf"]) for cfg in sl.GICS_SECTORS.values()}
    gics_etfs = {k: v for k, v in gics_etfs.items() if v}

    # universe breadth uses the GICS sector constituents only — the thematic
    # lists overlap them and would double-count the mega-caps
    gics_members = {t for name, g in groups.items() if g["tier"] == "sector"
                    for t in g["members"]}
    universe_mx = [m for t, m in member_mx.items() if t in gics_members]

    rates = rates_snapshot(bench_frames.get("^TNX"))
    if progress_cb:
        progress_cb("fetching proxy-ETF holdings", 0, len(etf_syms))
    holdings = fetch_top_holdings(etf_syms)

    # A fund's top ten need not all sit in our constituent lists — XLV holds
    # JNJ and PFE, XLB holds names no theme list carries — so the missing
    # ones get their own small batch rather than rendering as blanks.
    missing = sorted({h["ticker"] for rows in holdings.values() for h in rows}
                     - set(member_mx))
    if missing:
        print(f"backfilling {len(missing)} holding quotes…", file=sys.stderr)
        extra = fetch_daily_chunked(missing, period="2y", progress_cb=progress_cb,
                                    label="fetching holding quotes")
        for t, df in extra.items():
            m = sl.symbol_metrics(df)
            if m:
                member_mx[t] = m
    holdings = attach_holding_moves(holdings, member_mx)

    market = score_market(mx, gics_etfs, universe_mx, etf_mx, rates)
    print(f"market: {market['label']} ({market['score']:+.1f})", file=sys.stderr)
    if progress_cb:
        progress_cb(f"market {market['label']} — scoring sectors", 0, len(groups))

    spy, qqq, iwm = mx.get("SPY"), mx.get("QQQ"), mx.get("IWM")

    # ── sector layer ────────────────────────────────────────────────────────
    sectors = {}
    for gi, (name, g) in enumerate(groups.items(), 1):
        if progress_cb:
            progress_cb(f"scoring sector — {name}", gi, len(groups))
        em = etf_mx.get(g["etf"])
        if not em:
            log.warning("no data for %s proxy %s", name, g["etf"])
            continue
        mems = [member_mx[t] for t in g["members"] if t in member_mx]
        br = sl.breadth(mems)
        sc = sl.score_sector(em,
                             sl.relative_strength(em, spy) if spy else {},
                             sl.relative_strength(em, qqq) if qqq else {},
                             sl.relative_strength(em, iwm) if iwm else {},
                             br)
        sectors[name] = {
            "name": name, "etf": g["etf"], "tier": g["tier"],
            "parent": g["parent"], "metrics": em, "breadth": br,
            "scores": sc, "direction": sl.sector_direction(sc),
            "coverage": f"{len(mems)}/{len(g['members'])}",
            "rs_spy": sl.relative_strength(em, spy) if spy else {},
            "rs_qqq": sl.relative_strength(em, qqq) if qqq else {},
            "rs_iwm": sl.relative_strength(em, iwm) if iwm else {},
            "holdings": holdings.get(g["etf"]) or [],
        }

    ranked = sorted(sectors.values(), key=lambda s: s["scores"]["score"], reverse=True)

    # ── stock layer ─────────────────────────────────────────────────────────
    # Score every liquid member of every group in BOTH directions; the two
    # rankings fall out of one pass.
    candidates = []
    for name, g in groups.items():
        sec = sectors.get(name)
        if not sec:
            continue
        em = sec["metrics"]
        for t in g["members"]:
            m = member_mx.get(t)
            if not m or not m.get("dollar_vol") or m["dollar_vol"] < sl.MIN_DOLLAR_VOLUME:
                continue
            df = member_frames.get(t)
            rs_spy = sl.relative_strength(m, spy) if spy else {}
            rs_qqq = sl.relative_strength(m, qqq) if qqq else {}
            rs_sec = sl.relative_strength(m, em)
            for direction in ("long", "short"):
                setup = sl.detect_setup(df, m, direction)
                stock = sl.score_stock(m, rs_spy, rs_qqq, rs_sec, setup,
                                       sec["direction"], market["direction"], direction)
                clar = sl.trend_clarity(df, direction)
                conf = sl.confluence_score(market["direction"], market["strength"],
                                           sec["scores"], stock, clar, rs_sec, m, direction)
                lead = sl.leadership_score(sec["scores"]["score"], rs_sec, m,
                                           sec["breadth"], market["direction"], direction)
                candidates.append({
                    "ticker": t, "group": name, "tier": g["tier"],
                    "parent": g["parent"], "etf": g["etf"], "direction": direction,
                    "metrics": m, "rs_spy": rs_spy, "rs_qqq": rs_qqq, "rs_sector": rs_sec,
                    "stock_score": stock, "clarity": clar, "confluence": conf,
                    "leadership": lead, "setup": setup,
                    "sector_direction": sec["direction"],
                    "sector_score": sec["scores"]["score"],
                })

    return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "market": market, "market_metrics": mx,
            "sectors": ranked, "candidates": candidates,
            "data_gaps": [
                "Exchange internals (TICK, TRIN, VOLD, full NYSE advance-decline "
                "line, market-wide new highs/lows) — no free data source; NOT estimated",
                "Options positioning (put/call, gamma exposure, unusual flow, dark "
                "pool) — no free data source; NOT estimated",
                "Breadth shown is universe breadth over held constituent lists, "
                "not exchange breadth",
            ]}


def scan_and_store(progress_cb=None, intraday_top: int = 40) -> dict:
    """Full scan, intraday pass, and snapshot write — the entry point the
    webapp's background job calls. Returns the result it stored."""
    from stockanalysis.core import leaders_store

    res = run(progress_cb=progress_cb)
    top = sorted(res["candidates"], key=lambda c: c["confluence"]["score"],
                 reverse=True)
    picks = list(dict.fromkeys(c["ticker"] for c in top))[:intraday_top]
    if progress_cb:
        progress_cb(f"intraday pass — {len(picks)} finalists", 0, 1)
    res["intraday"] = intraday_stats(picks)
    leaders_store.save(res)
    return res


def _strip(o):
    """pandas objects and numpy scalars do not survive json.dumps()."""
    import numpy as np
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items()
                if not isinstance(v, pd.Series)}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--intraday-top", type=int, default=40)
    ap.add_argument("--cache", help="pickle path for raw bars (re-score offline)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    res = run(Path(args.cache) if args.cache else None)

    # intraday only for the finalists on either side
    top = sorted(res["candidates"], key=lambda c: c["confluence"]["score"], reverse=True)
    picks = list(dict.fromkeys([c["ticker"] for c in top[:args.intraday_top * 3]]))[:args.intraday_top]
    print(f"fetching intraday for {len(picks)} finalists…", file=sys.stderr)
    res["intraday"] = intraday_stats(picks)

    Path(args.json).write_text(json.dumps(_strip(res), indent=1, default=str))
    print(f"wrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
