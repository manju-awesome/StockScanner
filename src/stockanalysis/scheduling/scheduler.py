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
#
DAY_TRADE_TICKERS=[ 'META','MSFT', 'NVDA', 'TSLA', 'AMD', 'AVGO']
#DAY_TRADE_TICKERS=['COIN','AMAT', 'WDC', 'ARM', 'LITE', 'INTC', 'GLW', 'MU', 'HOOD', 'AAOI', 'STX', 'COHR', 'NBIS', 'MRVL', 'PLTR', 'AVGO', 'META', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'CRWD']


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

# Nightly output cleanup (scheduler loop, 23:30 ET Mon–Fri): delete generated
# files older than this many days. 0 or negative disables the nightly job.
CLEANUP_DAYS = int(os.environ.get("CLEANUP_DAYS", "7"))

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
    global _hot_tickers, _hot_tickers_updated_at, DAY_TRADE_TICKERS, _dynamic_day_trade


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

    # Surface the day's universe on the Alerts page (MEDIUM = feed only, no
    # email; dated dedup key = fires once per trading day, and yesterday's
    # entry expires from the feed via the LOW/standing-alert lifecycle when
    # the key stops being checked)
    try:
        from stockanalysis.core import alerts as alerts_mod
        today = _now_et().strftime("%Y-%m-%d")
        shown = ", ".join(merged[:15]) + ("…" if len(merged) > 15 else "")
        a = alerts_mod.make_alert(
            dedup_key=f"day_session:init_{today}", category="day_session",
            priority="MEDIUM",
            headline=(f"day-trade universe set: {len(merged)} tickers "
                      f"({len(hot)} hot mover(s) merged)"),
            why_it_matters=f"Today's intraday scans run on: {shown}",
            expected_impact="Hot movers join the static day-trade list for every "
                            "intraday scan until the next session init.",
            suggested_action="Expand the daytrade category on the Scanner page to "
                             "review or edit the merged list.",
            confidence=100, time_sensitivity="today",
            supporting_data={"hot_movers": hot, "merged_universe": merged})
        alerts_mod.raise_alerts([a], {f"day_session:init_{today}"})
    except Exception as e:
        _log(f"   day-session alert failed: {e}")

    # Persist for the dashboard's Day Session Universe panel — dashboards are
    # often generated in another process (webapp/CLI), so module state alone
    # would be invisible to them
    try:
        import json
        base = [t for t in merged if t not in hot]
        (REPORTS_DIR / "day_session.json").write_text(json.dumps({
            "date": str(_now_et().date()),
            "updated_at": _now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
            "hot": hot, "base": base, "merged": merged,
        }, indent=1))
    except Exception as e:
        _log(f"⚠  day_session.json write failed ({e})")

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
    dashboard_path = generate_dashboard(rows, output_dir=REPORTS_DIR, open_browser=True,
                                        report_name=f"{mode}Report")

    # ── Research Library ──────────────────────────────────────────
    # Keep the library in lockstep with every scheduled scan (day-trade,
    # swing, puts, calls, full) — same rationale as the webapp's scan job:
    # without this, library scores go stale until the watchlist monitor
    # happens to re-touch a ticker. charts/news skipped (redundant fetches).
    try:
        from stockanalysis.reporting.research import generate_research_pages
        written = generate_research_pages(rows, REPORTS_DIR,
                                          charts=False, fetch_news=False)
        _log(f"   Research library refreshed for {len(written)} ticker(s)")
    except Exception as e:
        _log(f"   Research library refresh failed: {e}")

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
# Module-level named functions (not closures) so the Automation page can
# identify each registered job by function name, "Run now" it, and re-register
# it after a schedule edit (schedule_config.py holds the editable cadence).

def _put_scan_job():
    """Put scan — runs more frequently when VIX is elevated."""
    run("puts")


def job_swing_premarket():
    run("swing")


def job_calls_premarket():
    run("calls")


def job_daytrade_scan():
    run("daytrade")


def job_puts_midday():
    run("puts")


def job_full_close():
    run("full", tickers=FULL_TICKERS)


def job_day_session_init():
    """Market open: lock the day's scan universe (live movers + base list)."""
    _initialize_day_session()


# Pre-Market Brief — one composed email; macro/movers/earnings/breakout
# context already fetched elsewhere, see core/premarket_brief.py for why
# this isn't a fresh data source.
def job_premarket_brief():
    if datetime.now(ET).weekday() >= 5:
        return
    from stockanalysis.core.premarket_brief import send_premarket_brief
    try:
        send_premarket_brief()
        _log("📰 Pre-market brief sent")
    except Exception as e:
        _log(f"Pre-market brief failed: {e}")


# Watchlist Alert Monitor — market-hours guard is inside the job (an
# interval trigger fires around the clock). News scanning rides along on
# the same cadence — it's a cheap per-ticker headline fetch, not worth a
# second market-hours-guarded loop.
def job_watchlist_alerts():
    now = datetime.now(ET)
    if now.weekday() >= 5 or not ((9, 30) <= (now.hour, now.minute) <= (16, 0)):
        return
    from stockanalysis.core.watchlist_alerts import scan_rows_for_alerts, default_alert_tickers
    from stockanalysis.reporting.research import generate_research_pages
    from stockanalysis.scanners import scan_universe as su
    tickers = default_alert_tickers()
    if not tickers:
        return
    try:
        rows = su.main(tickers)
    except Exception as e:
        _log(f"Watchlist alert scan failed: {e}")
        return
    new_alerts = scan_rows_for_alerts(rows)
    if new_alerts:
        _log(f"🔔 {len(new_alerts)} new watchlist alert(s): "
            + ", ".join(a["dedup_key"] for a in new_alerts))
    # Keeps the Research Library's columns current from the same rows
    # already fetched for the alert scan — see job_watchlist_scan in
    # webapp/api.py (the on-demand button) for the full rationale.
    try:
        generate_research_pages(rows, REPORTS_DIR, charts=False, fetch_news=False)
    except Exception as e:
        _log(f"Research library refresh failed: {e}")


def job_news_alerts():
    """Breaking-news headline scan — split out of job_watchlist_alerts so the
    Automation page can schedule it independently (it's also the Alerts
    page's "Scan News Now" button). Same weekday/market-hours guard."""
    now = datetime.now(ET)
    if now.weekday() >= 5 or not ((9, 30) <= (now.hour, now.minute) <= (16, 0)):
        return
    from stockanalysis.core.news_monitor import scan_news_for_alerts
    from stockanalysis.core.watchlist_alerts import default_alert_tickers
    tickers = default_alert_tickers()
    if not tickers:
        return
    try:
        new_news = scan_news_for_alerts(tickers)
        if new_news:
            _log(f"📰 {len(new_news)} new breaking-news alert(s): "
                + ", ".join(a["dedup_key"] for a in new_news))
    except Exception as e:
        _log(f"News monitor scan failed: {e}")


# Earnings Alert Monitor — runs before the market-hours window (and before
# the pre-market brief) since earnings releases happen before the open or
# after the close, not during 9:30-16:00 — the watchlist scan's guard would
# miss them.
def job_earnings_alerts():
    if datetime.now(ET).weekday() >= 5:
        return
    from stockanalysis.core.earnings_alerts import scan_earnings_for_alerts
    from stockanalysis.core.watchlist_alerts import default_alert_tickers
    tickers = default_alert_tickers()
    if not tickers:
        return
    try:
        new_alerts = scan_earnings_for_alerts(tickers)
        if new_alerts:
            _log(f"📅 {len(new_alerts)} new earnings alert(s): "
                + ", ".join(a["dedup_key"] for a in new_alerts))
    except Exception as e:
        _log(f"Earnings alert scan failed: {e}")


# Friday after-close extended scan — generates weekend watchlist; opens
# browser for review. Friday guard inside so the trigger time is editable
# without losing the Friday-only behavior.
def job_friday_scan():
    if datetime.now(ET).weekday() != 4:  # 4 = Friday
        return
    _log("📅 Friday post-close scan — generating weekend watchlist")
    try:
        rows = run_scan(FULL_TICKERS)
    except Exception as e:
        _log(f"Friday scan failed: {e}")
        return
    if rows:
        generate_dashboard(rows, output_dir=REPORTS_DIR, open_browser=True,
                           report_name="fullReport")
        _append_history(rows, "full", str(REPORTS_DIR))


# VIX adaptive — register 30-min put scans for the afternoon if VIX is
# elevated when this fires. The extra jobs it adds are wiped (and re-added
# next trigger if still elevated) whenever the schedule is re-registered.
def job_vix_adaptive_puts():
    if vix_is_elevated():
        _log("⚡ VIX elevated — adding 30-min put scan loop (11 AM – 2 PM ET)")
        for hhmm in ["11:00", "11:30", "12:30", "13:00", "13:30", "14:00"]:
            schedule.every().day.at(hhmm).do(_put_scan_job)


# Nightly output cleanup — same pruning as `--cleanup N`; CLEANUP_DAYS=0 in
# the environment disables it (guard inside so the job stays listed/editable).
def job_nightly_cleanup():
    if CLEANUP_DAYS > 0:
        cleanup_outputs(CLEANUP_DAYS)


# schedule_config.JOB_DEFS key -> function. Keys/labels/default cadences
# live in schedule_config.py; this maps them to the code to run.
SCHEDULED_JOBS: dict[str, callable] = {
    "earnings_alerts":   job_earnings_alerts,
    "premarket_brief":   job_premarket_brief,
    "watchlist_alerts":  job_watchlist_alerts,
    "news_alerts":       job_news_alerts,
    "swing_premarket":   job_swing_premarket,
    "calls_premarket":   job_calls_premarket,
    "day_session_init":  job_day_session_init,
    "daytrade_scans":    job_daytrade_scan,
    "puts_midday":       job_puts_midday,
    "vix_adaptive_puts": job_vix_adaptive_puts,
    "full_close":        job_full_close,
    "friday_scan":       job_friday_scan,
    "nightly_cleanup":   job_nightly_cleanup,
}


def register_jobs() -> None:
    """Register every enabled job from data/schedule_config.json (defaults
    from schedule_config.JOB_DEFS when unedited). day_session_init is
    registered before daytrade_scans by dict order, so a shared 09:30 slot
    initializes the universe before the scan that uses it."""
    from stockanalysis.scheduling.schedule_config import load_config, describe_spec
    cfg = load_config()
    for key, fn in SCHEDULED_JOBS.items():
        spec = cfg[key]
        if not spec["enabled"]:
            _log(f"   (disabled) {key}")
            continue
        if spec["type"] == "interval":
            schedule.every(spec["minutes"]).minutes.do(fn)
        else:
            for hhmm in spec["times"]:
                schedule.every().day.at(hhmm).do(fn)
        _log(f"   {key} — {describe_spec(spec)}")


def reschedule() -> None:
    """Re-register everything from the (just-edited) config. Called by the
    webapp after a schedule save so edits apply live, no restart. Also drops
    any VIX-adaptive extras; job_vix_adaptive_puts re-adds them on its next
    trigger if VIX is still elevated."""
    schedule.clear()
    _log("🔄 Schedule edited — re-registering jobs:")
    register_jobs()


def _start_scheduler() -> None:
    """
    Register all scheduled jobs from config and start the blocking loop.
    All times are Eastern Time (ET); jobs carry their own weekday/market-
    hours guards.
    """
    _log("✅ Scheduler started. Jobs registered:")
    register_jobs()

    _log("⏳ Waiting for next scheduled job... (Ctrl+C to quit)\n")

    while True:
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
    _log(f"   Dashboard    → {REPORTS_DIR / '<universe>Report_*.html'}")
    _log(f"   History CSV  → {HISTORY_CSV}")




# ── Output cleanup ────────────────────────────────────────────────────────────

# Only generated artifacts are eligible for cleanup — never user state
# (portfolio.csv), never the rolling history/tracker CSVs.
# Dashboards are named "<universe>Report_<ts>.html" (e.g. sp500Report_...);
# "dashboard_*.html" is still matched for files written before that rename.
CLEANUP_PATTERNS = ("stock_scan_*.csv", "*Report_*.html", "dashboard_*.html",
                    "metrics_*.csv", "research/*.html")


def cleanup_outputs(days: int = 7, reports_dir: Path | None = None) -> int:
    """
    Delete generated output files whose modification time is older than
    `days` days. Returns the number of files removed.
    """
    base = Path(reports_dir) if reports_dir else REPORTS_DIR
    cutoff = time.time() - days * 86400
    removed = 0
    for pattern in CLEANUP_PATTERNS:
        for f in base.glob(pattern):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError as e:
                _log(f"⚠  cleanup: could not remove {f.name} ({e})")
    _log(f"🧹 Cleanup: removed {removed} file(s) older than {days}d "
         f"from {base}")
    return removed


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
    parser.add_argument(
        "--cleanup",
        nargs="?", const=7, type=int, metavar="DAYS",
        help="Delete generated output files (scan CSVs, dashboards, research "
             "pages) modified more than DAYS days ago, then exit. "
             "Default 7 when the flag is given without a value.",
    )
    parser.add_argument(
        "--research",
        nargs="*", metavar="TICKER",
        help="Refresh research pages for ONLY these tickers (fresh data "
             "fetched just for them), then exit. With no tickers listed, "
             "refreshes the DAY_TRADE_TICKERS list.",
    )
    parser.add_argument("--test-scheduler", action="store_true",
                        help="Fire all jobs within 4 min and exit")
    args = parser.parse_args()

    # One-shot maintenance/refresh actions — run whichever were asked, exit
    if args.cleanup is not None or args.research is not None:
        if args.cleanup is not None:
            cleanup_outputs(args.cleanup)
        if args.research is not None:
            from stockanalysis.reporting.research import refresh_research
            tickers = args.research or DAY_TRADE_TICKERS
            _log(f"📄 Refreshing research pages for: {', '.join(tickers)}")
            written = refresh_research(tickers)
            _log(f"📄 Research refresh done — {len(written)} page(s) → "
                 f"{REPORTS_DIR / 'research'}")
        return

    if args.run_now:
        _log(f"One-shot mode: running {args.run_now.upper()} scan now")
        run(args.run_now, force=args.force)

    elif args.test_scheduler:
        _start_scheduler_test()
    else:
        _start_scheduler()


if __name__ == "__main__":
    main()
