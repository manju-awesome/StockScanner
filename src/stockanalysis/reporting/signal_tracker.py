"""
signal_tracker.py
=================
Logs every A/B-graded signal the scanner produces, then later checks realized
price action against the entry/stop/target levels to compute hit rate and
expectancy — broken down by category and grade.

This closes the feedback loop: without it, grade_signals.py's A/B/C grades
are untested opinions. If a category's expectancy is consistently negative,
that's the signal to fix or retire it — not gut feel.

Usage
-----
Automatic: scan_universe.py logs new A/B signals at the end of every scan.

Manual / scheduled:
    python -m stockanalysis.reporting.signal_tracker --update   # resolve open signals
    python -m stockanalysis.reporting.signal_tracker --report   # hit rate & expectancy
    python -m stockanalysis.reporting.signal_tracker --update --report

Storage is a flat CSV (data/output/signal_log.csv). Swap for SQLite once
volume grows past a few thousand signals.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

LOG_PATH = Path(__file__).resolve().parents[3] / "data" / "output" / "signal_log.csv"

# Only these categories produce tradeable swing signals worth tracking
TRACKED_CATEGORIES = {"Momentum", "Momentum-Pullback", "VCP Setup", "Turnaround"}
TRACKED_GRADES = {"A", "B"}

# Open signals unresolved after this many calendar days are marked expired
EXPIRY_DAYS = 20

COLUMNS = [
    "ticker", "category", "grade", "signal_date",
    "entry_price", "stop_price", "t1_price", "t2_price",
    "rr_t2", "outcome", "outcome_date", "outcome_price", "realized_r_multiple",
]


# ── Logging new signals ───────────────────────────────────────────────────────

def log_signals_from_rows(rows: list[dict], log_path: Path = LOG_PATH) -> int:
    """
    Append A/B-graded swing signals from enriched scan rows to the log.
    Skips rows without valid numeric levels and (ticker, date) duplicates.
    Returns the number of signals logged. Never raises.
    """
    try:
        today = date.today().isoformat()
        existing = set()
        if log_path.exists():
            prev = pd.read_csv(log_path, usecols=["ticker", "signal_date"])
            existing = set(zip(prev["ticker"], prev["signal_date"]))

        records = []
        for row in rows:
            if row.get("Grade") not in TRACKED_GRADES:
                continue
            if row.get("Category") not in TRACKED_CATEGORIES:
                continue
            levels = row.get("_levels") or {}
            entry, stop = levels.get("entry"), levels.get("stop")
            t1, t2 = levels.get("t1"), levels.get("t2")
            if not entry or not stop or not t2 or entry <= stop:
                continue
            if (row["Ticker"], today) in existing:
                continue
            records.append({
                "ticker":       row["Ticker"],
                "category":     row["Category"],
                "grade":        row["Grade"],
                "signal_date":  today,
                "entry_price":  round(float(entry), 2),
                "stop_price":   round(float(stop), 2),
                "t1_price":     round(float(t1), 2) if t1 else None,
                "t2_price":     round(float(t2), 2),
                "rr_t2":        row.get("RR_T2"),
                "outcome":      "open",
                "outcome_date": None,
                "outcome_price": None,
                "realized_r_multiple": None,
            })

        if not records:
            return 0

        log_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(records, columns=COLUMNS)
        df.to_csv(log_path, mode="a" if log_path.exists() else "w",
                  header=not log_path.exists(), index=False)
        log.info("Signal tracker: logged %d new signal(s) → %s", len(records), log_path)
        return len(records)
    except Exception as e:
        log.warning("Signal tracker logging failed: %s", e)
        return 0


# ── Resolving outcomes ────────────────────────────────────────────────────────

def _fetch_prices(ticker: str, start_date: str) -> pd.DataFrame | None:
    """Daily OHLC since start_date (exclusive of signal day itself is fine)."""
    try:
        df = yf.Ticker(ticker).history(start=start_date, auto_adjust=True)
        return df if df is not None and not df.empty else None
    except Exception as e:
        log.debug("Price fetch failed for %s: %s", ticker, e)
        return None


def _resolve(df: pd.DataFrame, idx, outcome: str, exit_price: float, exit_date) -> None:
    row = df.loc[idx]
    risk = row["entry_price"] - row["stop_price"]
    r_multiple = (exit_price - row["entry_price"]) / risk if risk else None
    df.loc[idx, "outcome"] = outcome
    df.loc[idx, "outcome_price"] = round(float(exit_price), 2)
    df.loc[idx, "outcome_date"] = str(exit_date)[:10]
    df.loc[idx, "realized_r_multiple"] = round(r_multiple, 2) if r_multiple is not None else None


def update_outcomes(log_path: Path = LOG_PATH, expiry_days: int = EXPIRY_DAYS) -> pd.DataFrame | None:
    """
    Walk all 'open' signals; mark target_hit / stop_hit (whichever came first)
    or expired_no_move after expiry_days. Uses T1 as the tracked target —
    the first scale-out is what decides whether the signal "worked".
    """
    if not log_path.exists():
        print(f"No signal log yet at {log_path}")
        return None

    df = pd.read_csv(log_path)
    open_idx = df.index[df["outcome"] == "open"]
    print(f"Resolving {len(open_idx)} open signal(s)…")

    for idx in open_idx:
        row = df.loc[idx]
        target = row["t1_price"] if pd.notna(row.get("t1_price")) else row["t2_price"]
        prices = _fetch_prices(row["ticker"], row["signal_date"])
        if prices is None:
            continue

        hit_t = prices.index[prices["High"] >= target]
        hit_s = prices.index[prices["Low"] <= row["stop_price"]]
        days_elapsed = (datetime.now().date()
                        - datetime.fromisoformat(row["signal_date"]).date()).days

        if len(hit_t) and len(hit_s):
            # both hit — whichever came first chronologically wins
            if hit_t[0] <= hit_s[0]:
                _resolve(df, idx, "target_hit", target, hit_t[0])
            else:
                _resolve(df, idx, "stop_hit", row["stop_price"], hit_s[0])
        elif len(hit_t):
            _resolve(df, idx, "target_hit", target, hit_t[0])
        elif len(hit_s):
            _resolve(df, idx, "stop_hit", row["stop_price"], hit_s[0])
        elif days_elapsed >= expiry_days:
            _resolve(df, idx, "expired_no_move", prices["Close"].iloc[-1], prices.index[-1])

    df.to_csv(log_path, index=False)
    resolved = (df["outcome"] != "open").sum()
    print(f"Done — {resolved}/{len(df)} signals resolved, {len(df) - resolved} still open.")
    return df


# ── Performance report ────────────────────────────────────────────────────────

def performance_report(log_path: Path = LOG_PATH) -> pd.DataFrame | None:
    """Hit rate and expectancy per category and per grade. Run weekly."""
    if not log_path.exists():
        print(f"No signal log yet at {log_path}")
        return None

    df = pd.read_csv(log_path)
    closed = df[df["outcome"].isin(["target_hit", "stop_hit", "expired_no_move"])]
    if closed.empty:
        print(f"No closed signals yet ({len(df)} open). Run --update after a few sessions.")
        return None

    def summarize(group: pd.DataFrame) -> pd.Series:
        n = len(group)
        wins = (group["outcome"] == "target_hit").sum()
        return pd.Series({
            "n": n,
            "hit_rate": round(wins / n, 2) if n else 0.0,
            "avg_R": round(group["realized_r_multiple"].mean(), 2),
        })

    print("\n" + "═" * 60)
    print("  SIGNAL PERFORMANCE — hit rate & expectancy (R multiples)")
    print("═" * 60)
    by_cat = closed.groupby("category").apply(summarize, include_groups=False)
    by_cat = by_cat.sort_values("avg_R", ascending=False)
    print("\nBy category:")
    print(by_cat.to_string())
    print("\nBy grade:")
    print(closed.groupby("grade").apply(summarize, include_groups=False).to_string())
    neg = by_cat[by_cat["avg_R"] < 0]
    if not neg.empty:
        print(f"\n⚠ Negative expectancy: {', '.join(neg.index)} — fix or retire these setups.")
    print()
    return by_cat


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    parser = argparse.ArgumentParser(description="Signal outcome tracker")
    parser.add_argument("--update", action="store_true", help="Resolve open signals against price data")
    parser.add_argument("--report", action="store_true", help="Print hit rate / expectancy report")
    args = parser.parse_args()

    if args.update:
        update_outcomes()
    if args.report or not args.update:
        performance_report()
