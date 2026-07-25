"""
engine.py
=========
Walk-forward backtest of the scanner's swing signals. For every trading day D
it recomputes metrics point-in-time (compute_daily_metrics on bars sliced to
D), then runs the SAME production categorize() + enrich_row() the live scan
uses, and resolves each A/B/C signal with the shared resolve_long() rules.

Honesty conventions (interviewers probe exactly these):
  * Signals are computed at day D's close and act at D+1 — entries are
    buy-stop triggers that a later bar must actually touch (no instant fills).
    Untriggered plans within 5 bars are no_trigger.
  * Same-bar stop+target ambiguity resolves to STOP (tie="stop").
  * One open signal per (ticker, category) — no pyramiding the same setup
    every day it persists.

Known limitations (documented, not hidden):
  * Survivorship bias — the universe is today's list; delisted losers absent.
  * No point-in-time fundamentals/earnings calendar: the earnings blackout
    gate never fires, Turnaround can only qualify via RVOL (not earnings
    beat), Longterm Hold and CANSLIM never pass. Backtested grades are
    slightly conservative vs live.
  * No intraday data: VWAP/ORB quality bonuses are absent; RVOL is the daily
    (EOD) definition.

Usage:
    python -m stockanalysis.backtest.engine --universe watchlist \\
        --start 2025-01-02 --end 2026-07-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

if __package__ in (None, ""):   # direct run: make `stockanalysis.*` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stockanalysis.backtest.data import load_benchmark, load_daily
from stockanalysis.backtest.resolve import resolve_long
from stockanalysis.core.grade_signals import enrich_row
from stockanalysis.core.metrics import (
    CFG_ADX_MIN_GATE, CFG_MAX_PCT_BELOW_200MA, CFG_PRICE_MIN,
    compute_daily_metrics,
)
from stockanalysis.reporting.signal_tracker import TRACKED_CATEGORIES

log = logging.getLogger(__name__)

WARMUP_DAYS  = 420   # calendar days of bars needed before the first scan day
MIN_BARS     = 200   # skip ticker-days with less history (matches live 1y fetch)
LOOKBACK_BARS = 252  # "52W" window, in bars — mirrors the live period="1y"


def backtest_entry_gate(row: dict) -> tuple[bool, str]:
    """
    The live entry gate minus the market-cap check — there is no free
    point-in-time market cap, and the curated universes are large-cap anyway.
    """
    failed = []
    if (row.get("Current Price") or 0) < CFG_PRICE_MIN:
        failed.append(f"Price<${CFG_PRICE_MIN:.0f}")
    if row.get("ADX_14") is not None and row["ADX_14"] < CFG_ADX_MIN_GATE:
        failed.append(f"ADX<{CFG_ADX_MIN_GATE}(flat)")
    if (row.get("Price_vs_200MA%") or 0) < CFG_MAX_PCT_BELOW_200MA:
        failed.append(f">{abs(CFG_MAX_PCT_BELOW_200MA)}%below200MA")
    return len(failed) == 0, ", ".join(failed)


def run_backtest(daily: dict[str, pd.DataFrame], qqq: pd.DataFrame,
                 start: date, end: date,
                 expiry_days: int = 20, tie: str = "stop",
                 trigger_window_bars: int = 5) -> pd.DataFrame:
    """
    Generate and resolve signals for every trading day in [start, end].
    `daily` frames must extend ~WARMUP_DAYS before `start` for indicator
    warmup, and to `end` for resolution.
    """
    # categorize() lives in the scanner module; imported here (not at module
    # top) so importing the engine never drags in the dashboard/reporting deps
    from stockanalysis.scanners.scan_universe import categorize

    qqq_close  = qqq["Close"].ffill()
    qqq_ret_3m = (qqq_close / qqq_close.shift(63) - 1) * 100
    scan_days  = [ts for ts in qqq.index if start <= ts.date() <= end]

    signals: list[dict] = []
    cooldown: dict[tuple[str, str], date] = {}   # (ticker, category) -> free-after

    for n, ts in enumerate(scan_days, 1):
        d = ts.date()
        q3 = float(qqq_ret_3m.get(ts)) if ts in qqq_ret_3m.index else float("nan")

        for ticker, bars in daily.items():
            win = bars.loc[:ts].tail(LOOKBACK_BARS)
            if len(win) < MIN_BARS or win.index[-1].date() != d:
                continue   # not enough history, or didn't trade that day

            row = {"Ticker": ticker}
            row.update(compute_daily_metrics(win, q3, asof_date=d))
            row["Current Price"] = round(float(win["Close"].iloc[-1]), 2)
            row["Entry_Gate_Pass"], row["Entry_Gate_Reason"] = backtest_entry_gate(row)

            cat, reason, score = categorize(row)
            if cat not in TRACKED_CATEGORIES:
                continue
            if cooldown.get((ticker, cat), date.min) > d:
                continue   # previous signal for this setup still live
            row["Category"], row["Cat_Reason"], row["Rank_Score"] = cat, reason, score

            enrich_row(row)
            levels = row.get("_levels") or {}
            entry, stop = levels.get("entry"), levels.get("stop")
            t1, t2 = levels.get("t1"), levels.get("t2")
            if not entry or not stop or not t2 or entry <= stop:
                continue   # same validity rules as the live tracker

            signals.append({
                "ticker": ticker, "signal_date": d,
                "category": cat, "grade": row["Grade"],
                "rank_score": score, "rr_t2": row.get("RR_T2"),
                "size_flag": row.get("SizeFlag"),
                "entry": round(float(entry), 2), "stop": round(float(stop), 2),
                "t1": round(float(t1), 2) if t1 else None,
                "t2": round(float(t2), 2),
            })
            cooldown[(ticker, cat)] = d + timedelta(days=expiry_days)

        if n % 20 == 0 or n == len(scan_days):
            log.info("scanned %d/%d days — %d signal(s) so far",
                     n, len(scan_days), len(signals))

    # ── Resolution pass — shared rules with the live tracker ─────────────────
    for s in signals:
        target = s["t1"] if s["t1"] else s["t2"]   # T1 decides "worked", like live
        res = resolve_long(
            daily[s["ticker"]], entry=s["entry"], stop=s["stop"], target=target,
            signal_date=s["signal_date"], asof_date=end,
            expiry_days=expiry_days, tie=tie,
            require_trigger=True, trigger_window_bars=trigger_window_bars,
        )
        s["target_used"] = target
        s.update(res)

    return pd.DataFrame(signals)


def print_summary(df: pd.DataFrame) -> None:
    """Compact results table — the full analysis report is a separate step."""
    if df.empty:
        print("No signals generated.")
        return

    resolved = df[df["outcome"].isin(["target_hit", "stop_hit", "expired_no_move"])]
    triggered = df[df["fill_price"].notna()]

    def summarize(g: pd.DataFrame) -> pd.Series:
        res = g[g["outcome"].isin(["target_hit", "stop_hit", "expired_no_move"])]
        n_res = len(res)
        return pd.Series({
            "signals":      len(g),
            "trigger_rate": round(g["fill_price"].notna().mean(), 2),
            "resolved":     n_res,
            "win_rate":     round((res["outcome"] == "target_hit").mean(), 2) if n_res else None,
            "avg_R":        round(res["realized_r_multiple"].mean(), 2) if n_res else None,
            "total_R":      round(res["realized_r_multiple"].sum(), 1) if n_res else None,
        })

    print("\n" + "═" * 64)
    print("  BACKTEST SUMMARY")
    print("═" * 64)
    print(f"\nSignals: {len(df)}  triggered: {len(triggered)}  resolved: {len(resolved)}")
    print(f"Outcomes: {df['outcome'].value_counts().to_dict()}")
    print("\nBy category:")
    print(df.groupby("category").apply(summarize, include_groups=False).to_string())
    print("\nBy grade:")
    print(df.groupby("grade").apply(summarize, include_groups=False).to_string())
    print()


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    from stockanalysis.scanners.scan_universe import (
        DAY_TRADE_TICKERS, DIVIDEND_STOCKS, LONGTERM_TICKERS,
        SP500_TICKERS, WATCHLIST_TICKERS,
    )
    universes = {
        "daytrade": DAY_TRADE_TICKERS, "watchlist": WATCHLIST_TICKERS,
        "sp500": SP500_TICKERS, "longterm": LONGTERM_TICKERS,
        "dividend": DIVIDEND_STOCKS,
    }

    parser = argparse.ArgumentParser(description="Walk-forward scanner backtest")
    parser.add_argument("--universe", choices=sorted(universes), default="watchlist")
    parser.add_argument("--tickers", nargs="+", metavar="TICKER",
                        help="override the universe with an ad-hoc list")
    parser.add_argument("--start", type=date.fromisoformat,
                        default=date.today() - timedelta(days=365))
    parser.add_argument("--end", type=date.fromisoformat,
                        default=date.today() - timedelta(days=1))
    parser.add_argument("--expiry", type=int, default=20)
    parser.add_argument("--tie", choices=["stop", "target"], default="stop")
    parser.add_argument("--refresh", action="store_true", help="ignore the data cache")
    parser.add_argument("--grade", choices=["A+", "A", "B+", "B", "C"], default=None,
                        help="restrict the summary to one grade band — use A+ to "
                             "validate the live A+ alert gate against history before "
                             "trusting it for real trades")
    args = parser.parse_args()

    tickers = ([t.upper() for t in args.tickers] if args.tickers
               else universes[args.universe])
    data_start = args.start - timedelta(days=WARMUP_DAYS)

    log.info("Loading %d tickers %s → %s (+%dd warmup)…",
             len(tickers), args.start, args.end, WARMUP_DAYS)
    daily = load_daily(tickers, data_start, args.end, refresh=args.refresh)
    qqq   = load_benchmark(data_start, args.end, refresh=args.refresh)
    log.info("Data ready for %d/%d tickers", len(daily), len(tickers))

    df = run_backtest(daily, qqq, args.start, args.end,
                      expiry_days=args.expiry, tie=args.tie)

    out_dir = Path(__file__).resolve().parents[3] / "data" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"backtest_{args.universe}_{args.start}_{args.end}.csv"
    df.to_csv(out, index=False)
    log.info("Saved %d signal(s) → %s", len(df), out)

    if args.grade:
        graded = df[df["grade"] == args.grade]
        print(f"\n{'═' * 64}\n  {args.grade} GATE VALIDATION "
              f"({len(graded)}/{len(df)} signals)\n{'═' * 64}")
        print_summary(graded)
    else:
        print_summary(df)


if __name__ == "__main__":
    cli()
