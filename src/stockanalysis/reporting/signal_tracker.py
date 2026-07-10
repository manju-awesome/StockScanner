"""
signal_tracker.py
=================
Logs every A/B-graded signal the scanner produces, then later checks realized
price action against the entry/stop/target levels to compute hit rate and
expectancy — broken down by category and grade.

This closes the feedback loop: without it, grade_signals.py's A/B/C grades
are untested opinions. If a category's expectancy is consistently negative,
that's the signal to fix or retire it — not gut feel.

Tracks two logs:
  signal_log.csv     – A/B swing signals, resolved against daily bars
  day_trade_log.csv  – day-trade breakout candidates (ORB/PDH plans from the
                       dashboard's Day Trade section), resolved bar-by-bar
                       against that session's 5-min data: no_trigger /
                       stop_hit / target_hit / eod_flat

Usage
-----
Automatic: scan_universe.py logs new swing signals AND day-trade candidates
at the end of every scan.

Manual / scheduled (run --update after the close):
    python -m stockanalysis.reporting.signal_tracker --update   # resolve both logs
    python -m stockanalysis.reporting.signal_tracker --report   # both reports
    python -m stockanalysis.reporting.signal_tracker --update --report

Storage is flat CSVs in data/output/. Swap for SQLite once volume grows
past a few thousand signals.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

if __package__ in (None, ""):   # direct run: make `stockanalysis.*` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stockanalysis.backtest.resolve import resolve_long

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


# ── Day-trade candidate tracking ──────────────────────────────────────────────
# Day-trade candidates are CONDITIONAL breakout plans (buy ORB/PDH high with a
# structure stop). Resolution therefore answers three questions in order:
#   1. did the entry trigger at all?           → no_trigger if not
#   2. after triggering, stop or target first? → stop_hit / target_hit
#   3. neither by the close?                   → eod_flat at the closing price

DAY_LOG_PATH = Path(__file__).resolve().parents[3] / "data" / "output" / "day_trade_log.csv"
DAY_SCORE_MIN = 20          # same gate as the dashboard's Day Trade section

DAY_COLUMNS = [
    "ticker", "signal_date", "signal_time", "day_score", "setup",
    "orb_status", "rvol_intraday", "above_vwap", "gap_pct",
    "entry_price", "stop_price", "t1_price", "t2_price",
    "trigger_time",     # when the breakout entry actually filled
    "outcome", "outcome_time", "outcome_price", "realized_r_multiple",
]


def log_day_trades_from_rows(rows: list[dict], log_path: Path = DAY_LOG_PATH) -> int:
    """
    Append today's day-trade candidates (dashboard score gate + valid levels)
    as open conditional plans. One per (ticker, date) — the first scan that
    flags a name is the actionable one. Returns count logged. Never raises.
    """
    try:
        from stockanalysis.reporting.dashboard import _day_trade_score, day_trade_levels

        now = datetime.now()
        today = now.date().isoformat()
        existing = set()
        if log_path.exists():
            prev = pd.read_csv(log_path, usecols=["ticker", "signal_date"])
            existing = set(zip(prev["ticker"], prev["signal_date"]))

        records = []
        for row in rows:
            score = _day_trade_score(row)
            if score <= DAY_SCORE_MIN:
                continue
            lv = day_trade_levels(row)
            if not lv or lv["stop"] >= lv["entry"]:
                continue
            if (row.get("Ticker"), today) in existing:
                continue
            records.append({
                "ticker":        row.get("Ticker"),
                "signal_date":   today,
                "signal_time":   now.strftime("%H:%M"),
                "trigger_time":  None,
                "day_score":     round(score),
                "setup":         lv["setup"],
                "orb_status":    row.get("ORB_Status"),
                "rvol_intraday": row.get("RVOL_Intraday"),
                "above_vwap":    row.get("Above_VWAP"),
                "gap_pct":       row.get("Gap%") if row.get("Gap%") is not None
                                 else row.get("Gap_Now%"),
                "entry_price":   lv["entry"],
                "stop_price":    lv["stop"],
                "t1_price":      lv["t1"],
                "t2_price":      lv["t2"],
                "outcome":       "open",
                "outcome_time":  None,
                "outcome_price": None,
                "realized_r_multiple": None,
            })

        if not records:
            return 0
        log_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(records, columns=DAY_COLUMNS).to_csv(
            log_path, mode="a" if log_path.exists() else "w",
            header=not log_path.exists(), index=False)
        log.info("Day-trade tracker: logged %d candidate(s) → %s", len(records), log_path)
        return len(records)
    except Exception as e:
        log.warning("Day-trade tracker logging failed: %s", e)
        return 0


def _fetch_intraday(ticker: str, day: str) -> pd.DataFrame | None:
    """5-min regular-session bars for one date (yfinance keeps ~60 days)."""
    try:
        start = datetime.fromisoformat(day).date()
        df = yf.Ticker(ticker).history(
            start=start, end=start + pd.Timedelta(days=1),
            interval="5m", prepost=False, auto_adjust=False)
        return df if df is not None and not df.empty else None
    except Exception as e:
        log.debug("Intraday fetch failed for %s %s: %s", ticker, day, e)
        return None


def _resolve_day(df: pd.DataFrame, idx, outcome: str, price: float, when) -> None:
    row = df.loc[idx]
    risk = row["entry_price"] - row["stop_price"]
    r = ((price - row["entry_price"]) / risk
         if risk and outcome not in ("no_trigger", "expired_no_data") else None)
    df.loc[idx, "outcome"] = outcome
    df.loc[idx, "outcome_price"] = round(float(price), 2) if price is not None else None
    df.loc[idx, "outcome_time"] = str(when)[:16] if when is not None else None
    df.loc[idx, "realized_r_multiple"] = round(r, 2) if r is not None else None


def update_day_trade_outcomes(log_path: Path = DAY_LOG_PATH) -> pd.DataFrame | None:
    """
    Resolve open day-trade candidates against that session's 5-min bars,
    starting from the bar after signal_time. Same-bar stop+target resolves
    to stop (conservative). Sessions older than yfinance's intraday window
    are marked expired_no_data.
    """
    if not log_path.exists():
        print(f"No day-trade log yet at {log_path}")
        return None

    df = pd.read_csv(log_path)
    if "trigger_time" not in df.columns:      # logs written before this column
        df["trigger_time"] = None
    # all-NaN columns load as float64; cast so string outcomes assign cleanly
    for col in ("outcome_time", "outcome", "trigger_time"):
        df[col] = df[col].astype("object")
    today = datetime.now().date().isoformat()
    # only resolve completed sessions (or today after ~16:05 ET would also work;
    # keep it simple: any date before today)
    open_idx = [i for i in df.index[df["outcome"] == "open"]
                if df.loc[i, "signal_date"] < today]
    print(f"Resolving {len(open_idx)} open day-trade candidate(s)…")

    for idx in open_idx:
        row = df.loc[idx]
        bars = _fetch_intraday(row["ticker"], row["signal_date"])
        if bars is None:
            age = (datetime.now().date()
                   - datetime.fromisoformat(row["signal_date"]).date()).days
            if age > 55:
                _resolve_day(df, idx, "expired_no_data", None, None)
            continue

        sig_t = str(row.get("signal_time") or "09:30")
        after = bars[[b.strftime("%H:%M") >= sig_t for b in bars.index]]
        if after.empty:
            after = bars

        entry, stop, t1 = row["entry_price"], row["stop_price"], row["t1_price"]
        in_trade = False
        for ts, bar in after.iterrows():
            if not in_trade:
                if bar["High"] >= entry:
                    in_trade = True
                    df.loc[idx, "trigger_time"] = str(ts)[:16]
                    # triggered — check the same bar for an immediate stop
                    if bar["Low"] <= stop:
                        _resolve_day(df, idx, "stop_hit", stop, ts)   # conservative
                        break
                continue
            hit_stop, hit_t1 = bar["Low"] <= stop, bar["High"] >= t1
            if hit_stop and hit_t1:
                _resolve_day(df, idx, "stop_hit", stop, ts)           # conservative
                break
            if hit_stop:
                _resolve_day(df, idx, "stop_hit", stop, ts)
                break
            if hit_t1:
                _resolve_day(df, idx, "target_hit", t1, ts)
                break
        else:
            last_close, last_ts = float(after["Close"].iloc[-1]), after.index[-1]
            if in_trade:
                _resolve_day(df, idx, "eod_flat", last_close, last_ts)
            else:
                _resolve_day(df, idx, "no_trigger", last_close, last_ts)

    df.to_csv(log_path, index=False)
    resolved = (df["outcome"] != "open").sum()
    print(f"Done — {resolved}/{len(df)} day-trade candidates resolved.")
    return df


def day_trade_report(log_path: Path = DAY_LOG_PATH) -> pd.DataFrame | None:
    """Trigger rate, win rate and expectancy for day-trade candidates."""
    if not log_path.exists():
        print(f"No day-trade log yet at {log_path}")
        return None

    df = pd.read_csv(log_path)
    closed = df[df["outcome"].isin(["target_hit", "stop_hit", "eod_flat", "no_trigger"])]
    if closed.empty:
        print(f"No resolved day-trade candidates yet ({len(df)} open). "
              f"Run --update after the close.")
        return None

    traded = closed[closed["outcome"] != "no_trigger"]

    def summarize(g: pd.DataFrame) -> pd.Series:
        entered = g[g["outcome"] != "no_trigger"]
        n_ent = len(entered)
        return pd.Series({
            "candidates":   len(g),
            "trigger_rate": round(n_ent / len(g), 2) if len(g) else 0.0,
            "win_rate":     (round((entered["outcome"] == "target_hit").sum() / n_ent, 2)
                             if n_ent else None),
            "avg_R":        (round(entered["realized_r_multiple"].mean(), 2)
                             if n_ent else None),
        })

    print("\n" + "═" * 60)
    print("  DAY-TRADE PERFORMANCE — trigger / win rate / expectancy")
    print("═" * 60)
    print(f"\nOverall: {summarize(closed).to_dict()}")
    print("\nBy setup:")
    print(closed.groupby("setup").apply(summarize, include_groups=False).to_string())
    if len(traded):
        buckets = pd.cut(closed["day_score"], bins=[0, 35, 65, 999],
                         labels=["C (≤35)", "B (35-65)", "A (>65)"])
        print("\nBy day-score bucket:")
        print(closed.groupby(buckets, observed=True)
              .apply(summarize, include_groups=False).to_string())
        neg = traded["realized_r_multiple"].mean()
        if neg is not None and neg < 0:
            print(f"\n⚠ Negative expectancy overall ({neg:.2f}R) — review the "
                  f"setup gates before sizing up.")

    _option_suitability_block(closed)
    print()
    return closed


def _option_suitability_block(closed: pd.DataFrame) -> None:
    """
    0DTE / short-dated call suitability: options need signals that resolve
    FAST after triggering — theta turns slow winners and eod_flat grinds into
    losses. Prints median trigger→target time, the share of winners resolving
    within 30/60 min, and the eod_flat share of triggered trades.
    """
    if "trigger_time" not in closed.columns:
        return
    traded = closed[closed["outcome"].isin(["target_hit", "stop_hit", "eod_flat"])]
    wins = traded[(traded["outcome"] == "target_hit")
                  & traded["trigger_time"].notna()
                  & traded["outcome_time"].notna()].copy()

    print("\n" + "─" * 60)
    print("  OPTION SUITABILITY (0DTE / short-dated calls)")
    print("─" * 60)
    if len(traded) < 5 or wins.empty:
        print(f"  Insufficient data — {len(traded)} triggered trade(s), "
              f"{len(wins)} timed winner(s). Need ≥5 triggered and ≥1 winner.")
        return

    mins = ((pd.to_datetime(wins["outcome_time"])
             - pd.to_datetime(wins["trigger_time"]))
            .dt.total_seconds() / 60).clip(lower=0)
    med      = float(mins.median())
    fast30   = float((mins <= 30).mean())
    fast60   = float((mins <= 60).mean())
    flat_pct = float((traded["outcome"] == "eod_flat").mean())

    print(f"  Winners: median trigger→target {med:.0f} min · "
          f"{fast30:.0%} within 30 min · {fast60:.0%} within 60 min")
    print(f"  eod_flat share of triggered trades: {flat_pct:.0%} "
          f"(scratches on stock, theta LOSSES on 0DTE)")

    if med <= 60 and flat_pct <= 0.30:
        verdict = ("FAST RESOLVERS — short-dated calls viable on these signals "
                   "(0DTE only on SPY/QQQ, ~0.6-0.7 delta, premium = full risk)")
    elif med <= 120 and flat_pct <= 0.45:
        verdict = ("MIXED — prefer 1-4 DTE over 0DTE; too many winners need "
                   "hours to resolve")
    else:
        verdict = ("GRINDERS — avoid 0DTE with these signals; trade shares or "
                   "ITM weeklies instead")
    print(f"  VERDICT: {verdict}")


# ── Resolving outcomes ────────────────────────────────────────────────────────

def _fetch_prices(ticker: str, start_date: str) -> pd.DataFrame | None:
    """Daily OHLC since start_date (exclusive of signal day itself is fine)."""
    try:
        # auto_adjust=False: logged entry/stop/target levels are nominal prices,
        # so resolution bars must be nominal too — an ex-dividend date inside
        # the window would shift adjusted bars down and fake stop hits.
        # (A split inside the ~20-day window would still skew; rare enough.)
        df = yf.Ticker(ticker).history(start=start_date, auto_adjust=False)
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

    Resolution rules live in backtest/resolve.py, SHARED with the backtest
    engine so live tracking and backtests can never drift apart. This path
    keeps the legacy live conventions: fill assumed at entry on signal day,
    same-bar stop+target ties go to target.
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

        res = resolve_long(
            prices, entry=row["entry_price"], stop=row["stop_price"], target=target,
            signal_date=datetime.fromisoformat(row["signal_date"]).date(),
            asof_date=datetime.now().date(),
            expiry_days=expiry_days, tie="target", require_trigger=False,
        )
        if res["outcome"] != "open":
            _resolve(df, idx, res["outcome"], res["outcome_price"], res["outcome_date"])

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

def main() -> None:
    """Standalone entry point — run this module directly."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    parser = argparse.ArgumentParser(description="Signal outcome tracker")
    parser.add_argument("--update", action="store_true", help="Resolve open signals against price data")
    parser.add_argument("--report", action="store_true", help="Print hit rate / expectancy report")
    args = parser.parse_args()

    if args.update:
        update_outcomes()
        update_day_trade_outcomes()
    if args.report or not args.update:
        performance_report()
        day_trade_report()


if __name__ == "__main__":
    main()
