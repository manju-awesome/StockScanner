"""
strength.py — §7 relative strength engine, 0-100
================================================
Whether the stock is being bought, or merely floating on a market that is
lifting everything.

Measured from the previous close, not the open
-----------------------------------------------
Both the stock and the benchmark are measured close-to-now over the same
window, which keeps the gap inside the comparison. Measuring from the open
would discard it, and for a gapper the gap is most of the move — a stock
that opened +30% and drifted sideways all day would show ~0% "relative
strength" against a flat SPY, which is the opposite of true.

The asymmetry §7 asks for
--------------------------
"Reward stocks that remain strong while the market or sector is weak." A
+4% move against a -1% tape is a stronger statement of demand than the
same +4% against a +3% tape, because in the second case most of it was
beta. So divergence — outperforming while the benchmark is actually down —
earns an explicit bonus rather than just a larger difference.

IWM carries the most weight of the three indices. Small caps track the
Russell; SPY and QQQ are included because §7 names them, but a $200M
biotech's correlation to the Nasdaq-100 is close to incidental.
"""

from __future__ import annotations

import pandas as pd

from stockanalysis.core.daytrade._common import (
    MARKET_CLOSE, MARKET_OPEN, blend, f, pct, scale, session_slice, sessions_in,
)

BENCH_WEIGHTS = {"IWM": 45, "SPY": 30, "QQQ": 25}


def _bench_move(bars: pd.DataFrame, day) -> float | None:
    """Benchmark % change for the session, previous close to last print."""
    if bars is None or bars.empty:
        return None
    days = [d for d in sessions_in(bars) if d <= day]
    if day not in days or len(days) < 2:
        return None
    today = session_slice(bars, day, MARKET_OPEN, MARKET_CLOSE)
    prior = session_slice(bars, days[-2], MARKET_OPEN, MARKET_CLOSE)
    if today.empty or prior.empty:
        return None
    return pct(f(today["Close"].iloc[-1]), f(prior["Close"].iloc[-1]))


def _bench_vs_vwap(bars: pd.DataFrame, day) -> bool | None:
    """Is the benchmark above its own session VWAP? §15's regime input."""
    from stockanalysis.core.daytrade._common import vwap
    if bars is None or bars.empty:
        return None
    today = session_slice(bars, day, MARKET_OPEN, MARKET_CLOSE)
    if today.empty:
        return None
    vw, last = vwap(today), f(today["Close"].iloc[-1])
    return None if (vw is None or last is None) else last > vw


def compute(sess: dict, context: dict, sector_etf: str | None = None) -> dict:
    """§7 relative strength, 0-100.

    `context` is {ticker: bars} from datafeed.fetch_context — the indices,
    VIX and any sector ETFs, all fetched once for the whole scan.
    """
    day = sess["asof"]
    stock_move = pct(sess.get("price"), sess.get("prev_close"))
    if stock_move is None:
        return {"score": None, "coverage": 0.0, "components": [],
                "missing": ["stock move"], "detail": "no prev close to compare"}

    parts, diffs, diverging = [], {}, []
    for bench, weight in BENCH_WEIGHTS.items():
        move = _bench_move(context.get(bench), day)
        if move is None:
            parts.append((f"vs {bench}", weight, None, "unavailable"))
            continue
        diff = stock_move - move
        diffs[bench] = diff
        # ±8% spans the range over which intraday relative strength
        # actually discriminates for this class of stock.
        sub = scale(diff, -8.0, 8.0)
        if diff > 0 and move < 0:
            sub = min(100.0, sub + 15.0)
            diverging.append(bench)
        parts.append((f"vs {bench}", weight, sub,
                      f"{diff:+.1f}pp ({bench} {move:+.1f}%)"))

    sector_diff = None
    if sector_etf:
        sec_move = _bench_move(context.get(sector_etf), day)
        if sec_move is not None:
            sector_diff = stock_move - sec_move
            sub = scale(sector_diff, -8.0, 8.0)
            if sector_diff > 0 and sec_move < 0:
                sub = min(100.0, sub + 15.0)
                diverging.append(sector_etf)
            parts.append((f"vs {sector_etf}", 40, sub,
                          f"{sector_diff:+.1f}pp ({sector_etf} {sec_move:+.1f}%)"))
        else:
            parts.append((f"vs {sector_etf}", 40, None, "unavailable"))

    result = blend(parts)
    result.update({
        "stock_move_pct": stock_move,
        "vs_spy": diffs.get("SPY"),
        "vs_qqq": diffs.get("QQQ"),
        "vs_iwm": diffs.get("IWM"),
        "vs_sector": sector_diff,
        "sector_etf": sector_etf,
        "diverging_from": diverging,
        "above_vwap": sess.get("price") is not None and sess.get("vwap") is not None
                      and sess["price"] > sess["vwap"],
        # §12's "sector confirmation" line: the sector is participating, so
        # the move is not the stock fighting its own group.
        "sector_confirms": bool(sector_diff is not None and sector_diff > -2),
    })
    return result
