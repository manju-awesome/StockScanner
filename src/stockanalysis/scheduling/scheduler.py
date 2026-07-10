"""
scheduler.py
============
Orchestrates all stock scan runs with:
  - Time-based scheduling (pre-market, intraday, after-close)
  - VIX-based adaptive frequency (elevated VIX = more frequent put scans)
  - Market health gate (suppresses day-trade alerts in bear tape)
  - Grade filter (only A / A+ signals trigger email via Resend API)
  - Scan history CSV logging for back-review
  - HTML dashboard generated after every scan

Setup
-----
1. Install dependencies:
       pip install schedule yfinance resend python-dotenv

2. Set environment variables (or create a .env file):
       RESEND_API_KEY=re_xxxxxxxxxxxx
       ALERT_EMAIL_TO=you@example.com
       ALERT_EMAIL_FROM=alerts@yourdomain.com

3. Run:
       python scheduler.py

   Or for a one-shot run (no scheduler loop):
       python scheduler.py --run-now full
       python scheduler.py --run-now daytrade
       python scheduler.py --run-now swing
       python scheduler.py --run-now puts

Windows Task Scheduler alternative
-----------------------------------
If you prefer Windows Task Scheduler over a long-running Python process,
comment out the `_start_scheduler()` call at the bottom and create four
tasks in taskschd.msc, each running:
    python scheduler.py --run-now <mode>
at the times listed in _start_scheduler().
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import traceback
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo  # Python 3.9+

import schedule
import yfinance as yf

# ── Path setup ────────────────────────────────────────────────────────────────
# scheduler.py lives at src/stockanalysis/scheduling/scheduler.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed — fall back to real env vars only

from stockanalysis.scanners.market_movers import scan_movers, print_movers_report, email_movers, _now_et

# ── Shared state: hot tickers from last movers scan ───────────────────────────
_hot_tickers: list[str] = []
_hot_tickers_updated_at: datetime | None = None

from stockanalysis.scanners.scan_universe import main as run_scan, SP500_TICKERS, DAY_TRADE_TICKERS, WATCHLIST_TICKERS      # your main scan
from stockanalysis.reporting.dashboard import generate_dashboard, _score_to_grade  # dashboard + grading

# ── Config ────────────────────────────────────────────────────────────────────
#DAY_TRADE_TICKERS=[ 'META', 'MU', 'PLTR', 'MRVL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'AVGO', 'ARM']
DAY_TRADE_TICKERS=['AMAT', 'WDC', 'GLW', 'MU', 'STX', 'COHR', 'NBIS', 'ARM', 'LITE', 'MRVL', 'PLTR', 'AVGO', 'HOOD', 'META', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'CRWD']

INTRADAY_TICKERS  = DAY_TRADE_TICKERS  #Daytrade only
#WATCHLIST_TICKERS = WATCHLIST_TICKERS 
WATCHLIST_TICKERS = WATCHLIST_TICKERS
# #used for both swing and Longterm positions
FULL_TICKERS      = SP500_TICKERS 
#To find the next trending stocks


ET = ZoneInfo("America/New_York")

#EMAIL_FROM  = os.environ.get("ALERT_EMAIL_FROM", "onboarding@resend.dev")
EMAIL_FROM  = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

# ---- Email config (Resend) --------------------------------------------------
EMAIL_TO         = "mthimmareddy99@gmail.com"
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
RESEND_API_URL = "https://api.resend.com/emails"

REPORTS_DIR     = PROJECT_ROOT / "data" / "output"
HISTORY_CSV     = REPORTS_DIR / "scan_history.csv"


# VIX threshold above which put scans run every 30 min instead of once at noon
VIX_ELEVATED_THRESHOLD = 25.0

# Minimum grade to send an email alert
EMAIL_GRADE_THRESHOLD = {"A+", "A"}

# ── Grade helpers ─────────────────────────────────────────────────────────────
_GRADE_THRESHOLDS = {
    "Day Trade":   [(80, "A+"), (65, "A"), (50, "B+"), (35, "B"), (20, "C")],
    "Swing Trade": [(95, "A+"), (80, "A"), (60, "B+"), (40, "B"), (20, "C")],
    "Calls":       [(18, "A+"), (14, "A"), (10, "B+"), (6,  "B"), (1,  "C")],
    "Puts":        [(10, "A+"), (8,  "A"), (6,  "B+"), (4,  "B"), (1,  "C")],
}

# ── Module-level mutable universe ─────────────────────────────────────────────
_hot_tickers:            list[str]       = []
_hot_tickers_updated_at: datetime | None = None
_dynamic_day_trade = []




def _initialize_day_session() -> None:
    """
    Runs ONCE at 9:30 AM ET.
    1. Refreshes hot movers with live data
    2. Merges with DAY_TRADE_TICKERS
    3. Stores result in _dynamic_day_trade for all 30-min scans to reuse
    """
    global  _hot_tickers, _hot_tickers_updated_at, DAY_TRADE_TICKERS


    _log("🔔 Market open — initializing day session universe…")

    # Step 1: refresh hot tickers
    hot = _refresh_hot_tickers(mode="live")

    # Step 2: merge hot + base, order preserved, no duplicates
    seen     = set()
    merged   = []
    for t in (hot + DAY_TRADE_TICKERS):
        if t not in seen:
            seen.add(t)
            merged.append(t)

    # Step 3: store once — all 30-min jobs read this
    _dynamic_day_trade = merged
    DAY_TRADE_TICKERS = merged


    print("Dynamic Day trade stocks",_dynamic_day_trade)

    _log(f"✅ Day session universe locked:")
    _log(f"   {len(hot)} movers + {len(DAY_TRADE_TICKERS)} base "
         f"= {len(_dynamic_day_trade)} unique tickers")
    _log(f"   {', '.join(_dynamic_day_trade)}")


def _run_hot_scan(mode: str = "live") -> None:
    """
    Every 30-min job — uses _dynamic_day_trade set at open.
    Falls back to DAY_TRADE_TICKERS if initialize hasn't run yet.
    """
    universe = _dynamic_day_trade if _dynamic_day_trade else DAY_TRADE_TICKERS

    if not universe:
        _log("⚠  _run_hot_scan: no tickers available, skipping")
        return

    _log(f"⚡ Hot scan ({mode}) — {len(universe)} tickers")
    run(mode, tickers=universe)


def _refresh_hot_tickers(mode: str = "auto") -> list[str]:
    """
    Run market movers scan, extract tickers, store in module-level state.
    Called once at market open / pre-market, then results reused every 30 min.
    """
    global _hot_tickers, _hot_tickers_updated_at

    _log("🔍 Refreshing hot ticker list from market movers scan…")
    try:
        rows = scan_movers(mode=mode, top_n=10)
        print_movers_report(rows)
        email_movers(rows)

        _hot_tickers = [r["ticker"] for r in rows if r.get("ticker")]
        _hot_tickers_updated_at = _now_et()

        _log(f"✅ Hot tickers updated ({len(_hot_tickers)}): {', '.join(_hot_tickers)}")
    except Exception as e:
        _log(f"✗  Movers scan failed: {e}")
        traceback.print_exc()

    return _hot_tickers


def _get_hot_tickers(fallback: list[str] | None = None) -> list[str]:
    """
    Return current hot tickers.
    Falls back to provided list if movers scan hasn't run yet or returned nothing.
    """
    if _hot_tickers:
        age_mins = (
            (_now_et() - _hot_tickers_updated_at).seconds // 60
            if _hot_tickers_updated_at else 999
        )
        _log(f"  Using {len(_hot_tickers)} hot tickers "
             f"(updated {age_mins} min ago): {', '.join(_hot_tickers)}")
        return _hot_tickers

    fb = fallback or DAY_TRADE_TICKERS
    _log(f"  No hot tickers yet — falling back to {len(fb)} default tickers")
    return fb









def score_to_grade(score: float, section: str) -> str:
    thresholds = _GRADE_THRESHOLDS.get(
        section, [(80, "A+"), (60, "A"), (40, "B+"), (20, "B"), (1, "C")]
    )
    for cutoff, grade in thresholds:
        if score >= cutoff:
            return grade
    return "D"


# ── Market condition helpers ──────────────────────────────────────────────────

def _start_scheduler() -> None:

    def job_swing_premarket():  run("daytrade",tickers=WATCHLIST_TICKERS)
    def job_calls_premarket():  run("daytrade",tickers=DAY_TRADE_TICKERS)

    def job_daytrade_open():    run("daytrade",tickers=DAY_TRADE_TICKERS)
    def job_daytrade_1000():    run("daytrade",tickers=DAY_TRADE_TICKERS)
    def job_daytrade_1030():    run("daytrade",tickers=DAY_TRADE_TICKERS)
    def job_puts_midday():      run("daytrade",tickers=DAY_TRADE_TICKERS)
    def job_full_close():       run("full", tickers=FULL_TICKERS)

    # ── Pre-market movers (8:00 AM) ───────────────────────────────
    # ── 9:30 AM — initialize once, locks universe for the day ────
    schedule.every().day.at("09:30").do(_initialize_day_session)

    # ── Every 30 min — reuses _dynamic_day_trade set at 9:30 ─────
    for hhmm in ["10:00", "10:30", "11:00", "11:30",
                 "12:00", "12:30", "13:00", "13:30",
                 "14:00", "14:30", "15:00", "15:30"]:
        schedule.every().day.at(hhmm).do(_run_hot_scan)
    # ── VIX adaptive put scans ────────────────────────────────────
    def _maybe_add_vix_scans():
        if vix_is_elevated():
            _log("⚡ VIX elevated — adding extra put scans on hot tickers")
            for hhmm in ["11:15", "11:45", "12:15", "12:45", "13:15", "13:45"]:
                schedule.every().day.at(hhmm).do(
                    lambda: run("puts", tickers=_get_hot_tickers())
                )
    schedule.every().day.at("10:00").do(_maybe_add_vix_scans)

    # ── After close full scan ─────────────────────────────────────
    schedule.every().day.at("16:30").do(job_full_close)

    # ── Friday extended scan ──────────────────────────────────────
    def _friday_scan():
        if _now_et().weekday() == 4:
            _log("📅 Friday post-close — generating weekend watchlist")
            try:
                rows = run_scan(FULL_TICKERS)
                if rows:
                    generate_dashboard(rows, output_dir=REPORTS_DIR, open_browser=True)
                    _append_history(rows, "full", str(REPORTS_DIR))
            except Exception as e:
                _log(f"Friday scan failed: {e}")
    schedule.every().day.at("16:45").do(_friday_scan)

    _log("✅ Scheduler started. Jobs registered:")
    for job in schedule.jobs:
        _log(f"   {job.next_run.strftime('%H:%M ET')}  →  {job.job_func.__name__}")

    _log("⏳ Waiting for next job… (Ctrl+C to quit)\n")

    while True:
        if _now_et().weekday() >= 5:
            time.sleep(60)
            continue
        schedule.run_pending()
        time.sleep(30)



def get_vix() -> float:
    """Fetch current VIX level. Returns 20.0 as fallback on error."""
    try:
        vix = yf.Ticker("^VIX").fast_info.get("last_price")
        return float(vix) if vix else 20.0
    except Exception:
        return 20.0


def get_market_rs() -> tuple[float, float]:
    """Return (SPY_RS, QQQ_RS) as 20-day price change % vs itself (proxy RS)."""
    try:
        for _ in range(2):
            spy_hist = yf.Ticker("SPY").history(period="1mo")
            qqq_hist = yf.Ticker("QQQ").history(period="1mo")
            if not spy_hist.empty and not qqq_hist.empty:
                spy_rs = (spy_hist["Close"].iloc[-1] / spy_hist["Close"].iloc[0] - 1) * 100
                qqq_rs = (qqq_hist["Close"].iloc[-1] / qqq_hist["Close"].iloc[0] - 1) * 100
                return round(spy_rs, 2), round(qqq_rs, 2)
        return 0.0, 0.0
    except Exception:
        return 0.0, 0.0


def market_is_bullish() -> bool:
    """True if both SPY and QQQ are up >2% over the last month."""
    spy_rs, qqq_rs = get_market_rs()
    result = spy_rs > 2.0 and qqq_rs > 2.0
    _log(f"Market health → SPY {spy_rs:+.1f}%  QQQ {qqq_rs:+.1f}%  → {'BULLISH' if result else 'BEARISH'}")
    return result


def vix_is_elevated() -> bool:
    vix = get_vix()
    _log(f"VIX = {vix:.1f}  ({'ELEVATED ⚠' if vix > VIX_ELEVATED_THRESHOLD else 'normal'})")
    return vix > VIX_ELEVATED_THRESHOLD


# ── Scan history logging ──────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    print(f"[{ts}]  {msg}")


def _append_history(rows: list[dict], mode: str, dashboard_path: str) -> None:
    """Append top signals from this scan run to the running CSV log."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not HISTORY_CSV.exists()

    section_map = {
        "daytrade": "Day Trade",
        "swing":    "Swing Trade",
        "puts":     "Puts",
        "calls":    "Calls",
        "full":     "Swing Trade",   # full uses swing scoring for history
    }
    section = section_map.get(mode, "Swing Trade")

    fieldnames = [
        "scan_ts", "mode", "ticker", "category",
        "put_candidate", "put_score", "put_grade",
        "call_candidate", "call_score", "call_grade",
        "day_score", "swing_score", "rs", "rsi", "adx",
        "above_vwap", "rvol", "dashboard_path",
    ]

    with HISTORY_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()

        ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
        for r in rows:
            ps = r.get("Put_Score", 0) or 0
            cs = r.get("Call_Score", 0) or 0
            writer.writerow({
                "scan_ts":        ts,
                "mode":           mode,
                "ticker":         r.get("Ticker", ""),
                "category":       r.get("Category", ""),
                "put_candidate":  r.get("Put_Candidate", False),
                "put_score":      ps,
                "put_grade":      score_to_grade(ps, "Puts"),
                "call_candidate": r.get("Call_Candidate", False),
                "call_score":     cs,
                "call_grade":     score_to_grade(cs, "Calls"),
                "day_score":      r.get("_day_score", ""),
                "swing_score":    r.get("_swing_score", ""),
                "rs":             r.get("RS", ""),
                "rsi":            r.get("RSI_14", ""),
                "adx":            r.get("ADX_14", ""),
                "above_vwap":     r.get("Above_VWAP", False),
                "rvol":           r.get("RVOL", ""),
                "dashboard_path": dashboard_path,
            })

    _log(f"History → {len(rows)} rows appended to {HISTORY_CSV.name}")


# ── Alert conditions ──────────────────────────────────────────────────────────

def _num(r: dict, key: str, default: float) -> float:
    """r.get() that also falls back when the stored value is None (metric failed)."""
    v = r.get(key, default)
    return default if v is None else v


def _alert_conditions(mode: str) -> callable:
    """Return a filter function for rows that deserve an email alert."""
    conditions = {
        "daytrade": lambda r: (
            # time-adjusted RVOL when available; daily RVOL underreads all morning
            (_num(r, "RVOL_Intraday", 0) or _num(r, "RVOL", 0)) >= 1.5
            and r.get("Above_VWAP")
            and r.get("Category") in ("Momentum", "Momentum-Pullback")
            and _num(r, "ADX_14", 0) >= 25
            and r.get("Entry_Gate_Pass")
        ),
        "swing": lambda r: (
            _num(r, "BB_PctB", 1) <= 0.20
            and r.get("ATR Shrinking")
            and _num(r, "Pullback_Vol_Ratio", 2) <= 0.75
            and _num(r, "RS", -999) >= 15
            and r.get("Entry_Gate_Pass")
        ),
        "puts": lambda r: (
            r.get("Put_Candidate")
            and _num(r, "Put_Score", 0) >= 8        # A grade floor
            and _num(r, "BB_PctB", 0) >= 1.1        # extended above upper BB
        ),
        "calls": lambda r: (
            r.get("Call_Candidate")
            and _num(r, "Call_Score", 0) >= 14      # A grade floor
            and _num(r, "RS", -999) >= 20
            and r.get("VolumeDryingUp")
        ),
    }
    # "full" mode checks all categories
    return conditions.get(mode, lambda r: (
        r.get("Put_Candidate") or r.get("Call_Candidate")
    ))


# ── Email via Resend ──────────────────────────────────────────────────────────

def _build_email_html(rows: list[dict], mode: str, dashboard_path: str) -> str:
    section_map = {
        "daytrade": "Day Trade",
        "swing":    "Swing Trade",
        "puts":     "Puts",
        "calls":    "Calls",
        "full":     "All",
    }
    section = section_map.get(mode, mode)
    ts = datetime.now(ET).strftime("%b %d, %Y %H:%M ET")

    rows_html = ""
    for r in rows:
        ticker   = r.get("Ticker", "?")
        cat      = r.get("Category", "")
        price    = r.get("Current Price", 0) or 0
        ps       = r.get("Put_Score", 0) or 0
        cs       = r.get("Call_Score", 0) or 0
        pg       = score_to_grade(ps, "Puts")  if r.get("Put_Candidate")  else "—"
        cg       = score_to_grade(cs, "Calls") if r.get("Call_Candidate") else "—"
        rs       = r.get("RS", 0) or 0
        rvol     = r.get("RVOL", 0) or 0
        put_flag = "✅ PUT"  if r.get("Put_Candidate")  else ""
        call_flag= "🟢 CALL" if r.get("Call_Candidate") else ""
        reason   = r.get("Put_Reason") or r.get("Call_Reason") or r.get("Call_Strike_Hint") or ""

        rows_html += f"""
        <tr>
          <td style="padding:8px;font-weight:600">{ticker}</td>
          <td style="padding:8px;color:#555">{cat}</td>
          <td style="padding:8px">${price:,.2f}</td>
          <td style="padding:8px;color:#185FA5">{f'RS {rs:+.1f}'}</td>
          <td style="padding:8px">{f'RVOL {rvol:.2f}'}</td>
          <td style="padding:8px;color:#A32D2D">{put_flag} {f'({ps}) {pg}' if put_flag else ''}</td>
          <td style="padding:8px;color:#0F6E56">{call_flag} {f'({cs}) {cg}' if call_flag else ''}</td>
          <td style="padding:8px;font-size:11px;color:#666">{reason[:80]}</td>
        </tr>"""

    return f"""
    <html><body style="font-family:sans-serif;max-width:900px;margin:auto;padding:20px">
      <h2 style="color:#0b0b0b">📊 Stock Scan Alert — {section}</h2>
      <p style="color:#666;font-size:13px">Scan time: {ts} · {len(rows)} alert-grade signals</p>
      <table border="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="background:#f5f4f0;text-align:left">
            <th style="padding:8px">Ticker</th>
            <th style="padding:8px">Category</th>
            <th style="padding:8px">Price</th>
            <th style="padding:8px">RS</th>
            <th style="padding:8px">RVOL</th>
            <th style="padding:8px">Put</th>
            <th style="padding:8px">Call</th>
            <th style="padding:8px">Reason</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="margin-top:20px;font-size:11px;color:#898781">
        Dashboard saved → {dashboard_path}<br>
        Not financial advice. Verify all signals before trading.
      </p>
    </body></html>"""


def send_alert_email(rows: list[dict], mode: str, dashboard_path: str) -> None:
    if not RESEND_API_KEY:
        _log("⚠  RESEND_API_KEY not set — skipping email")
        return
    if not rows:
        return

    try:
        import resend
        resend.api_key = RESEND_API_KEY

        section_labels = {
            "daytrade": "Day Trade 🔥",
            "swing":    "Swing Setup 📈",
            "puts":     "Put Alert 🔴",
            "calls":    "Call Alert 🟢",
            "full":     "Full Scan 📊",
        }
        subject = (
            f"[StockScan] {section_labels.get(mode, mode)} — "
            f"{len(rows)} A/A+ signal{'s' if len(rows) != 1 else ''} "
            f"· {datetime.now(ET).strftime('%b %d %H:%M ET')}"
        )

        resend.Emails.send({
            "from":    EMAIL_FROM,
            "to":      [EMAIL_TO],
            "subject": subject,
            "html":    _build_email_html(rows, mode, dashboard_path),
        })
        _log(f"✉  Email sent → {EMAIL_TO}  ({len(rows)} alerts, subject: {subject})")

    except Exception as e:
        _log(f"✗  Email failed: {e}")
        traceback.print_exc()


# ── Core scan runner ──────────────────────────────────────────────────────────

def run(mode: str, tickers: list[str] | None = None, force: bool = False) -> None:
    """
    Execute one scan cycle for the given mode.

    mode options:
        daytrade  – intraday momentum (RVOL, VWAP focus)
        swing     – multi-day setups (BB coil, ATR shrink)
        puts      – put candidate scan
        calls     – call candidate scan
        full      – all categories, full ticker universe
    """


    _log(f"{'─'*60}")
    _log(f"▶  Starting scan: mode={mode.upper()}")

    # ── Market gate ────────────────────────────────────────────
    if mode == "daytrade" and not force and not market_is_bullish():
        _log("⚠  Market not bullish — suppressing day-trade scan")
        _log("   Use --force to override the market health gate")
        return

    # ── VIX adaptive frequency (handled in scheduler, but log here too) ───
    vix = get_vix()

    # ── Ticker universe ─────────────────────────────────────────
    if tickers is None:
        if mode == "daytrade":
            #_initialize_day_session()
            tickers = _dynamic_day_trade if _dynamic_day_trade else DAY_TRADE_TICKERS
        elif mode in ("swing", "puts", "calls"):
            tickers = WATCHLIST_TICKERS
        elif mode == "full":
            tickers = FULL_TICKERS
        else:
            tickers = SP500_TICKERS

    _log(f"  Universe: {len(tickers)} tickers "
         f"({'dynamic' if tickers is _dynamic_day_trade else 'static'})")



    # ── Run scan ─────────────────────────────────────────────────
    try:
        rows = run_scan(tickers)
    except Exception as e:
        _log(f"✗  Scan failed: {e}")
        traceback.print_exc()
        return

    if not rows:
        _log("⚠  Scan returned no rows")
        return

    _log(f"✓  Scan complete — {len(rows)} tickers processed")

    # ── Dashboard ─────────────────────────────────────────────────
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    dashboard_path = generate_dashboard(rows, output_dir=REPORTS_DIR, open_browser=True)

    # ── Grade + filter for alerts ─────────────────────────────────
    condition = _alert_conditions(mode)
    alert_rows = [r for r in rows if condition(r)]
    _log(f"   Alert-worthy rows (pre-grade filter): {len(alert_rows)}")

    # Attach grade and further filter to A / A+ only
    def _row_grade(r: dict) -> str:
        if r.get("Put_Candidate"):
            return score_to_grade(r.get("Put_Score", 0) or 0, "Puts")
        if r.get("Call_Candidate"):
            return score_to_grade(r.get("Call_Score", 0) or 0, "Calls")
        # day/swing: score stored in _dashboard_score by top5 fn;
        # for alert rows it may not be set yet — use RS as proxy
        s = r.get("RS", 0) or 0
        return score_to_grade(s, mode.capitalize())

    high_grade_rows = [r for r in alert_rows if _row_grade(r) in EMAIL_GRADE_THRESHOLD]
    _log(f"   A/A+ rows after grade filter: {len(high_grade_rows)}")

    # ── Email ─────────────────────────────────────────────────────
    if high_grade_rows:
        send_alert_email(high_grade_rows, mode, dashboard_path)
    else:
        _log("   No A/A+ signals — email suppressed")

    # ── History log ───────────────────────────────────────────────
    _append_history(rows, mode, dashboard_path)

    # ── Console report ────────────────────────────────────────────
    SEP = "─" * 100
    print(f"\n{SEP}")
    print(f"  PUT REPORT  [{mode.upper()}]  VIX={vix:.1f}".center(100))
    print(SEP)
    print(f"  {'Ticker':<20} {'Category':<18} {'Flag':<7} {'Score':>5} {'Grade':>4}   {'Reason'}")
    print(f"  {'─'*18} {'─'*16} {'─'*5} {'─'*5} {'─'*4}   {'─'*50}")
    for r in rows:
        if not r.get("Put_Candidate"):
            continue
        ps    = r.get("Put_Score", 0) or 0
        grade = score_to_grade(ps, "Puts")
        flag  = "✓ YES" if r.get("Put_Candidate") else "✗ NO "
        print(f"  {r.get('Ticker',''):<20} {r.get('Category',''):<18} {flag:<7} {ps:>5} {grade:>4}   "
              f"{str(r.get('Put_Reason',''))[:60]}")

    print(f"\n{SEP}")
    print(f"  CALL REPORT  [{mode.upper()}]".center(100))
    print(SEP)
    print(f"  {'Ticker':<20} {'Category':<18} {'Flag':<7} {'Strength':<12} {'Score':>5} {'Grade':>4}   {'Reason'}")
    print(f"  {'─'*18} {'─'*16} {'─'*5} {'─'*10} {'─'*5} {'─'*4}   {'─'*40}")
    for r in rows:
        if not r.get("Call_Candidate"):
            continue
        cs    = r.get("Call_Score", 0) or 0
        grade = score_to_grade(cs, "Calls")
        flag  = "✓ YES" if r.get("Call_Candidate") else "✗ NO "
        strength = r.get("Call_Strength", "N/A") or "N/A"
        print(f"  {r.get('Ticker',''):<20} {r.get('Category',''):<18} {flag:<7} {strength:<12} {cs:>5} {grade:>4}   "
              f"{str(r.get('Call_Reason', r.get('Call_Strike_Hint','')) or '')[:40]}")
    print(SEP)


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def _put_scan_job():
    """Put scan — runs more frequently when VIX is elevated."""
    run("puts")


def _start_scheduler() -> None:
    """
    Register all scheduled jobs and start the blocking loop.

    All times are Eastern Time (ET).
    Runs Mon–Fri only (weekend guard inside the loop).
    """


    def job_swing_premarket():
        run("swing")

    def job_calls_premarket():
        run("calls")

    def job_daytrade_open():
        run("daytrade")

    def job_daytrade_1000():
        run("daytrade")

    def job_daytrade_1030():
        run("daytrade")

    def job_puts_midday():
        run("puts")

    def job_full_close():
        run("full", tickers=FULL_TICKERS)

    schedule.every().day.at("08:00").do(job_swing_premarket)
    schedule.every().day.at("08:05").do(job_calls_premarket)
    schedule.every().day.at("09:30").do(job_daytrade_open)
    schedule.every().day.at("10:00").do(job_daytrade_1000)
    schedule.every().day.at("10:30").do(job_daytrade_1030)
    schedule.every().day.at("12:00").do(job_puts_midday)
    schedule.every().day.at("16:30").do(job_full_close)





    # ── Friday after-close extended scan (4:45 PM ET) ────────────
    # Generates weekend watchlist; opens browser for review
    def _friday_scan():
        if datetime.now(ET).weekday() == 4:  # 4 = Friday
            _log("📅 Friday post-close scan — generating weekend watchlist")
            rows = None
            try:
                rows = run_scan(FULL_TICKERS)
            except Exception as e:
                _log(f"Friday scan failed: {e}")
                return
            if rows:
                generate_dashboard(rows, output_dir=REPORTS_DIR, open_browser=True)
                _append_history(rows, "full", str(REPORTS_DIR))

    schedule.every().day.at("16:45").do(_friday_scan)

    # ── VIX adaptive — register 30-min put scans if VIX elevated at 10 AM ──
    def _maybe_add_vix_scans():
        if vix_is_elevated():
            _log("⚡ VIX elevated — adding 30-min put scan loop (11 AM – 2 PM ET)")
            for hhmm in ["11:00", "11:30", "12:30", "13:00", "13:30", "14:00"]:
                schedule.every().day.at(hhmm).do(_put_scan_job)

    schedule.every().day.at("10:00").do(_maybe_add_vix_scans)

    _log("✅ Scheduler started. Jobs registered:")
    for job in schedule.jobs:
        _log(f"   {job}")

    _log("⏳ Waiting for next scheduled job... (Ctrl+C to quit)\n")

    while True:
        # Skip weekends
        if datetime.now(ET).weekday() >= 5:   # 5=Sat, 6=Sun
            time.sleep(60)
            continue
        schedule.run_pending()
        time.sleep(30)   # check every 30 seconds

def _start_scheduler_test() -> None:
    from datetime import timedelta

    def _in(minutes: int) -> str:
        t = (datetime.now(ET) + timedelta(minutes=minutes)).strftime("%H:%M")
        _log(f"  Scheduling test job at {t} ET")
        return t

    schedule.every().day.at(_in(1)).do(lambda: run("daytrade"))

    _log("🧪 TEST SCHEDULER — jobs fire at +1, +2, +3, +4 minutes from now")
    _log("   Waiting up to 8 minutes for all jobs to complete...\n")

    deadline = datetime.now(ET) + timedelta(minutes=3)   # ← wait long enough

    while datetime.now(ET) < deadline:
        schedule.run_pending()
        time.sleep(5)   # ← check every 5 sec, not 10

    _log("✅ Test complete — check:")
    _log(f"   Email inbox  → {EMAIL_TO}")
    _log(f"   Dashboard    → {REPORTS_DIR / 'dashboard_*.html'}")
    _log(f"   History CSV  → {HISTORY_CSV}")




# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Standalone entry point — run this module directly."""
    parser = argparse.ArgumentParser(description="Stock scan scheduler")

    parser.add_argument(
        "--run-now",
        choices=["daytrade", "swing", "puts", "calls", "full"],
        help="Run one scan immediately and exit (skips scheduler loop)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass market health gate and run scan regardless of SPY/QQQ trend",
    )
    args = parser.parse_args()

    parser.add_argument("--test-scheduler", action="store_true",
                        help="Fire all jobs within 4 min and exit")
    args = parser.parse_args()

    if args.run_now:
        _log(f"One-shot mode: running {args.run_now.upper()} scan now")
        run(args.run_now, force=args.force)

    elif args.test_scheduler:
        _start_scheduler_test()
    else:
        _start_scheduler()


if __name__ == "__main__":
    main()
