"""
market_movers.py
================
Scans pre-market and live market for top 10 movers across the S&P 500 +
extended watchlist, then enriches each with a catalyst (earnings beat/miss,
analyst upgrade/downgrade, M&A, product launch, macro event, or general news).

Usage
-----
Standalone:
    python market_movers.py                   # auto-detects pre-market vs live
    python market_movers.py --mode premarket  # force pre-market scan
    python market_movers.py --mode live       # force live scan
    python market_movers.py --mode afterhours # force after-hours scan
    python market_movers.py --top 20          # show top 20 instead of 10
    python market_movers.py --email           # send results via Resend
    python market_movers.py --tickers NVDA AMD MU --top 5

Scheduler integration:
    from market_movers import run_movers_job
    schedule.every().day.at("08:00").do(run_movers_job, mode="premarket")
    schedule.every().day.at("09:35").do(run_movers_job, mode="live")
    schedule.every().day.at("10:30").do(run_movers_job, mode="live")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import textwrap
import time
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)

# ── Constants ─────────────────────────────────────────────────────────────────
ET = ZoneInfo("America/New_York")

RESEND_API_KEY    = os.environ.get("RESEND_API_KEY", "")
EMAIL_TO          = os.environ.get("ALERT_EMAIL_TO",   "you@example.com")
RESEND_FROM_EMAIL = os.environ.get("ALERT_EMAIL_FROM", "alerts@yourdomain.com")

try:
    import resend as resend_sdk
    HAVE_RESEND_SDK = True
except ImportError:
    HAVE_RESEND_SDK = False
    log.debug("resend SDK not installed — will use urllib HTTP fallback")

# Session boundaries (hour, minute) in ET
PREMARKET_START = (4,  0)
MARKET_OPEN     = (9, 30)
MARKET_CLOSE    = (16, 0)
AFTER_HOURS_END = (20, 0)

# ── Watchlist ─────────────────────────────────────────────────────────────────
WATCHLIST: list[str] = [
    # Mega-cap tech
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AMD","AVGO","ORCL","PLTR",
    # Semis / AI infra
    "QCOM","INTC","ARM","MRVL","SMCI","ANET","CRDO","ASML","TSM","USAR","UUUU","UMAY",
    #Memory
    "WDC","STX","CIEN","VRT","GLW","LITE","COHR","MU","AMAT","AAOI","NBIS",
    "CRWV","IREN",
    # Finance
    "JPM","GS","MS","BAC","V","MA","PYPL","HOOD","COIN",
    # Health / Biotech
    "LLY","HIMS","NVO",
    # Energy
    "XOM","CVX","OXY","SLB",
    # Retail / Consumer
    "WMT","COST","TGT","AMZN","NKE","MCD",
    # Defense / Aerospace
    "LMT","RTX","NOC","BA",
    # Cybersecurity
    "PANW","CRWD","OKTA",
    # Quantum / Speculative
    "IONQ","QBTS","RGTI","IREN",
    # Macro ETFs (useful for context)
    "SPY","QQQ","IWM","TLT","GLD","UNG",
]


# ── Catalyst keyword patterns ─────────────────────────────────────────────────
CATALYST_PATTERNS: list[tuple[str, str]] = [
    # Earnings
    (r"\bearnings? beat\b|\bEPS beat\b|\brevenue beat\b|\bbeat estimates?\b|\bbeat expectations?\b",
     "Earnings Beat"),
    (r"\bearnings? miss\b|\bEPS miss\b|\bmissed estimates?\b|\bbelow expectations?\b|\bdisappoint\w*\b",
     "Earnings Miss"),
    (r"\bguidance rais\w+\b|\braised? (outlook|guidance|forecast)\b|\bupped? guidance\b",
     "Guidance Raised"),
    (r"\bguidance (cut|lower\w*|reduc\w*)\b|\blowered? (outlook|guidance|forecast)\b",
     "Guidance Cut"),
    # Analyst actions
    (r"\bupgrad\w+\b|\bprice target (rais\w+|increas\w+|hik\w+)\b|\bbuy rating\b|\boutperform\b",
     "Analyst Upgrade"),
    (r"\bdowngrad\w+\b|\bprice target (cut|lower\w*|reduc\w*)\b|\bsell rating\b|\bunderperform\b",
     "Analyst Downgrade"),
    (r"\binitiati\w+ (coverage|at buy|outperform|overweight)\b|\bnew coverage\b",
     "New Coverage"),
    # M&A / Corporate
    (r"\bacquisition\b|\bacquir\w+\b|\bmerger\b|\btakeover\b|\bbuyout\b|\bprivate equity\b",
     "M&A / Acquisition"),
    (r"\bspin[\s-]?off\b|\bdivest\w*\b|\bsell\w* (unit|division|business|subsidiary)\b",
     "Divestiture"),
    (r"\bshare buyback\b|\brepurchase program\b|\bdividend (increas\w+|rais\w+|hik\w+)\b",
     "Capital Return"),
    # Product / Business
    (r"\bproduct launch\b|\bnew (product|model|chip|drug|platform|service)\b|\blaunch\w* (new|of)\b",
     "Product Launch"),
    (r"\bFDA (approv\w+|clear\w+|grant\w+)\b|\b510[Kk]\b|\bBLA\b|\bNDA\b|\bsNDA\b",
     "FDA Approval"),
    (r"\bcontract win\b|\bnew (deal|contract|order)\b|\bpartnership\b|\bjoint venture\b|\bMOU\b",
     "New Deal / Contract"),
    # Macro / Economic
    (r"\bFed\b|\bFOMC\b|\binterest rate\b|\brate (cut|hike|decision|pause)\b|\bpowell\b",
     "Fed / Rate Decision"),
    (r"\bCPI\b|\bPCE\b|\binflation\b|\bjobs report\b|\bnonfarm payroll\b|\bGDP\b|\bPMI\b",
     "Economic Data"),
    (r"\btariff\b|\btrade war\b|\bsanction\b|\bexport (ban|restrict\w+|control)\b",
     "Trade / Tariff"),
    # Sentiment / Risk
    (r"\bshort squeeze\b|\bhigh short interest\b|\bmost shorted\b",
     "Short Squeeze"),
    (r"\binsider buy\b|\bexecutive (purchas\w+|buy\w+)\b|\b13[Dd] filing\b",
     "Insider Buying"),
    (r"\blayoff\b|\brestructur\w+\b|\bjob cut\b|\bworkforce reduc\w+\b|\bRIF\b",
     "Restructuring"),
    (r"\bfraud\b|\bSEC invest\w+\b|\bclass action\b|\bsubpoena\b|\blawsuit\b|\blitigation\b",
     "Legal / Regulatory"),
]


# ── Session detection ─────────────────────────────────────────────────────────

def _now_et() -> datetime:
    return datetime.now(ET)


def _detect_mode() -> str:
    """Return 'premarket', 'live', 'afterhours', or 'closed'."""
    now = _now_et()
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return "closed"
    mins = now.hour * 60 + now.minute
    pm_start  = PREMARKET_START[0] * 60 + PREMARKET_START[1]   # 240
    mkt_open  = MARKET_OPEN[0]     * 60 + MARKET_OPEN[1]       # 570
    mkt_close = MARKET_CLOSE[0]    * 60 + MARKET_CLOSE[1]      # 960
    ah_end    = AFTER_HOURS_END[0] * 60 + AFTER_HOURS_END[1]   # 1200
    if pm_start  <= mins < mkt_open:   return "premarket"
    if mkt_open  <= mins < mkt_close:  return "live"
    if mkt_close <= mins < ah_end:     return "afterhours"
    return "closed"


# ── Price fetch ───────────────────────────────────────────────────────────────

def _fetch_quotes(tickers: list[str], mode: str) -> list[dict]:
    """
    Fetch per-ticker using yf.Ticker.history() to avoid MultiIndex
    column issues that arise from yf.download() on multiple tickers.
    Returns a list of raw quote dicts sorted by abs % change descending.
    """
    log.info("Fetching quotes for %d tickers (mode=%s)…", len(tickers), mode)

    now_et = _now_et()
    today  = now_et.date()

    # Session start cutoff — bars before this are excluded
    cutoff_map = {
        "premarket":  now_et.replace(hour=4,  minute=0,  second=0, microsecond=0),
        "live":       now_et.replace(hour=9,  minute=30, second=0, microsecond=0),
        "afterhours": now_et.replace(hour=16, minute=0,  second=0, microsecond=0),
        "closed":     now_et.replace(hour=0,  minute=0,  second=0, microsecond=0),
    }
    cutoff = cutoff_map.get(mode, cutoff_map["closed"])

    quotes = []
    total  = len(tickers)

    for idx, ticker in enumerate(tickers, 1):
        try:
            df = yf.Ticker(ticker).history(
                period="2d",
                interval="1m",
                prepost=True,      # include pre/after-hours bars
                auto_adjust=True,
            )

            if df is None or df.empty:
                log.debug("[%d/%d] %s — no data", idx, total, ticker)
                continue

            # Normalize timezone to ET
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(ET)
            else:
                df.index = df.index.tz_convert(ET)

            # Split into previous day and today
            df_prev  = df[df.index.date < today]
            df_today = df[df.index.date == today]

            # Previous close = last clean close bar from prior day
            prev_close = None
            if not df_prev.empty:
                prev_series = df_prev["Close"].dropna()
                if not prev_series.empty:
                    prev_close = float(prev_series.iloc[-1])

            # Session slice — only bars after session start
            if mode == "closed":
                df_session = df_today   # closed: use full day for context
            else:
                df_session = df_today[df_today.index >= cutoff]

            # Fallback: if session slice is empty use all of today
            if df_session.empty:
                df_session = df_today

            if df_session.empty:
                log.debug("[%d/%d] %s — no session bars", idx, total, ticker)
                continue

            # Drop rows where Close is NaN
            df_session = df_session.dropna(subset=["Close"])
            if df_session.empty:
                continue

            last_price   = float(df_session["Close"].iloc[-1])
            session_open = float(df_session["Open"].dropna().iloc[0]) if not df_session["Open"].dropna().empty else last_price
            session_high = float(df_session["High"].max())
            session_low  = float(df_session["Low"].min())
            session_vol  = int(df_session["Volume"].sum())

            # Fallback prev_close to session open if unavailable
            if prev_close is None:
                prev_close = session_open

            chg_pct = (last_price - prev_close) / prev_close * 100 if prev_close else 0.0

            quotes.append({
                "ticker":       ticker,
                "prev_close":   round(prev_close,   2),
                "session_open": round(session_open, 2),
                "session_high": round(session_high, 2),
                "session_low":  round(session_low,  2),
                "last_price":   round(last_price,   2),
                "chg_pct":      round(chg_pct,      2),
                "session_vol":  session_vol,
                "abs_chg_pct":  abs(chg_pct),
            })

            log.debug(
                "[%d/%d] %s  last=%.2f  chg=%.2f%%  vol=%d",
                idx, total, ticker, last_price, chg_pct, session_vol,
            )

        except Exception as e:
            log.debug("[%d/%d] %s — skipped: %s", idx, total, ticker, e)
            continue

    return quotes


# ── Catalyst detection ────────────────────────────────────────────────────────

def _classify_headline(text: str) -> str:
    """Return the first matching catalyst label, or 'General News'."""
    for pattern, label in CATALYST_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "General News"


def _fetch_catalyst(ticker: str) -> tuple[str, str]:
    """
    Fetch recent news for ticker via yfinance.
    Prefers the longer summary/description over the short title.
    Returns (catalyst_label, full_text) — no truncation.
    """
    try:
        news = yf.Ticker(ticker).news or []

        candidates: list[str] = []
        for item in news[:10]:
            # yfinance ≥0.2.x nests content under 'content' dict
            content = item.get("content", {}) or {}
            title   = (item.get("title")       or content.get("title")       or "").strip()
            summary = (item.get("summary")     or content.get("summary")
                       or item.get("description") or content.get("description") or "").strip()
            # Use summary if it adds meaningful detail beyond the title
            text = summary if len(summary) > len(title) + 20 else title
            if text:
                candidates.append(text)

        if not candidates:
            return "No News Found", "—"

        # Walk candidates: return first one with a specific catalyst
        best_label    = "General News"
        best_text     = candidates[0]

        for text in candidates:
            label = _classify_headline(text)
            if label != "General News":
                best_label = label
                best_text  = text
                break

        return best_label, best_text   # full text, no truncation

    except Exception as e:
        log.debug("News fetch failed for %s: %s", ticker, e)
        return "Fetch Error", "—"


# ── Main scan ─────────────────────────────────────────────────────────────────

def scan_movers(
    tickers: list[str] | None = None,
    mode:    str = "auto",
    top_n:   int = 10,
) -> list[dict]:
    """
    Scan tickers for top N movers by absolute % change.
    Enriches each result with catalyst label, headline/summary,
    session high/low, volume, and direction.

    Parameters
    ----------
    tickers : list of ticker strings (defaults to WATCHLIST)
    mode    : 'premarket' | 'live' | 'afterhours' | 'closed' | 'auto'
    top_n   : how many movers to return

    Returns
    -------
    List of enriched dicts, sorted by abs % change descending.
    """
    if tickers is None:
        tickers = WATCHLIST

    if mode == "auto":
        mode = _detect_mode()

    log.info("Session mode: %s | Scanning %d tickers | Top %d", mode.upper(), len(tickers), top_n)

    # ── Fetch raw quotes ──────────────────────────────────────────
    quotes = _fetch_quotes(tickers, mode)

    if not quotes:
        log.error("No quotes returned — check yfinance connectivity or try --mode closed")
        return []

    # Sort by absolute % change, take top_n
    quotes.sort(key=lambda x: x["abs_chg_pct"], reverse=True)
    top = quotes[:top_n]

    log.info("Top %d movers identified — fetching catalysts…", len(top))

    # ── Enrich with catalyst + news ───────────────────────────────
    results = []
    for i, q in enumerate(top, 1):
        ticker = q["ticker"]
        log.info("  [%d/%d] %s — fetching catalyst…", i, len(top), ticker)

        catalyst, headline = _fetch_catalyst(ticker)
        time.sleep(0.3)   # gentle on yfinance news rate limits

        results.append({
            "rank":          i,
            "ticker":        ticker,
            "direction":     "▲" if q["chg_pct"] >= 0 else "▼",
            "last_price":    q["last_price"],
            "prev_close":    q["prev_close"],
            "chg_pct":       q["chg_pct"],
            "session_open":  q["session_open"],
            "session_high":  q["session_high"],
            "session_low":   q["session_low"],
            "session_vol":   q["session_vol"],
            "catalyst":      catalyst,
            "headline":      headline,
            "mode":          mode,
            "scan_time":     _now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
        })

    return results


# ── Console report ────────────────────────────────────────────────────────────

def print_movers_report(rows: list[dict]) -> None:
    if not rows:
        print("No movers found.")
        return

    mode    = rows[0].get("mode", "").upper()
    scan_ts = rows[0].get("scan_time", "")
    SEP     = "═" * 95
    THIN    = "─" * 95
    # Left-padding to align wrapped headline lines under the text column
    WRAP_INDENT = " " * 6

    print(f"\n{SEP}")
    print(f"  TOP {len(rows)} MARKET MOVERS — {mode}   [{scan_ts}]".center(95))
    print(SEP)
    print(
        f"  {'#':>2}  {'Ticker':<7} {'Last':>9}  {'Chg%':>8}  "
        f"{'Sess Low':>9}  {'Sess High':>9}  {'Volume':>8}  {'Catalyst'}"
    )
    print(f"  {THIN}")

    for r in rows:
        chg_str = f"{r['direction']} {abs(r['chg_pct']):.2f}%"
        vol_str = (
            f"{r['session_vol'] / 1_000_000:.1f}M" if r["session_vol"] >= 1_000_000
            else f"{r['session_vol'] / 1_000:.0f}K"
        )
        color = "\033[92m" if r["chg_pct"] >= 0 else "\033[91m"
        reset = "\033[0m"

        # ── Metric line ───────────────────────────────────────────
        print(
            f"  {r['rank']:>2}  {r['ticker']:<7} "
            f"${r['last_price']:>8.2f}  "
            f"{color}{chg_str:>8}{reset}  "
            f"${r['session_low']:>8.2f}  "
            f"${r['session_high']:>8.2f}  "
            f"{vol_str:>8}  "
            f"{r['catalyst']}"
        )

        # ── Full headline / summary wrapped at 88 chars ───────────
        if r["headline"] and r["headline"] != "—":
            wrapped = textwrap.wrap(r["headline"], width=88)
            for i, line in enumerate(wrapped):
                prefix = f"{WRAP_INDENT}  📰 " if i == 0 else f"{WRAP_INDENT}     "
                print(f"{prefix}{line}")

        # Prev close reference
        print(
            f"{WRAP_INDENT}     Prev close ${r['prev_close']:.2f}  "
            f"│  Session open ${r['session_open']:.2f}"
        )
        print()  # blank line between tickers

    # ── Catalyst breakdown ────────────────────────────────────────
    print(SEP)
    print("\n  CATALYST BREAKDOWN")
    print(f"  {'─' * 45}")
    counts = Counter(r["catalyst"] for r in rows)
    for cat, cnt in counts.most_common():
        bar = "█" * cnt
        print(f"  ● {cat:<28}  {bar}  {cnt}")

    top_g = rows[0]
    top_l = min(rows, key=lambda r: r["chg_pct"])
    print(
        f"\n  Top gainer : {top_g['ticker']:<6} "
        f"{top_g['direction']}{abs(top_g['chg_pct']):.2f}%  — {top_g['catalyst']}"
    )
    print(
        f"  Top loser  : {top_l['ticker']:<6} "
        f"{top_l['direction']}{abs(top_l['chg_pct']):.2f}%  — {top_l['catalyst']}"
    )
    print(f"\n{SEP}\n")


# ── Email ─────────────────────────────────────────────────────────────────────

def _build_movers_html(rows: list[dict]) -> str:
    mode    = rows[0].get("mode", "").upper() if rows else ""
    scan_ts = rows[0].get("scan_time", "") if rows else ""

    rows_html = ""
    for r in rows:
        chg_color = "#0F6E56" if r["chg_pct"] >= 0 else "#A32D2D"
        chg_str   = f"{r['direction']} {abs(r['chg_pct']):.2f}%"
        vol_str   = (
            f"{r['session_vol'] / 1_000_000:.1f}M" if r["session_vol"] >= 1_000_000
            else f"{r['session_vol'] / 1_000:.0f}K"
        )
        headline_escaped = r["headline"].replace("<", "&lt;").replace(">", "&gt;")

        rows_html += f"""
        <tr style="border-bottom:1px solid #f0efea;vertical-align:top">
          <td style="padding:10px 6px;font-weight:700;font-size:13px">{r['rank']}</td>
          <td style="padding:10px 6px;font-weight:700;font-size:15px;color:#0b0b0b">{r['ticker']}</td>
          <td style="padding:10px 6px">${r['last_price']:.2f}</td>
          <td style="padding:10px 6px;color:{chg_color};font-weight:600">{chg_str}</td>
          <td style="padding:10px 6px;color:#555">${r['session_low']:.2f}</td>
          <td style="padding:10px 6px;color:#555">${r['session_high']:.2f}</td>
          <td style="padding:10px 6px;color:#555">{vol_str}</td>
          <td style="padding:10px 6px">
            <span style="background:#E6F1FB;color:#0C447C;padding:2px 7px;
                         border-radius:3px;font-size:11px;font-weight:500;white-space:nowrap">
              {r['catalyst']}
            </span>
          </td>
          <td style="padding:10px 6px;font-size:12px;color:#444;max-width:320px;line-height:1.5">
            {headline_escaped}
            <div style="font-size:10px;color:#898781;margin-top:3px">
              Prev close ${r['prev_close']:.2f} · Open ${r['session_open']:.2f}
            </div>
          </td>
        </tr>"""

    catalyst_counts = Counter(r["catalyst"] for r in rows)
    catalyst_html = "".join(
        f'<div style="margin:3px 0;font-size:12px">● {cat} <strong>({cnt})</strong></div>'
        for cat, cnt in catalyst_counts.most_common()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Movers — {scan_ts}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             background:#f5f4f0;padding:24px;margin:0">
  <div style="max-width:1100px;margin:auto;background:white;border-radius:12px;
              padding:28px;border:0.5px solid #e1e0d9">

    <h2 style="margin:0 0 4px;font-size:20px;color:#0b0b0b">
      📊 Top {len(rows)} Market Movers — {mode}
    </h2>
    <p style="color:#898781;font-size:13px;margin:0 0 24px">{scan_ts}</p>

    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead>
        <tr style="background:#f5f4f0;text-align:left">
          <th style="padding:8px 6px">#</th>
          <th style="padding:8px 6px">Ticker</th>
          <th style="padding:8px 6px">Price</th>
          <th style="padding:8px 6px">Chg%</th>
          <th style="padding:8px 6px">Low</th>
          <th style="padding:8px 6px">High</th>
          <th style="padding:8px 6px">Volume</th>
          <th style="padding:8px 6px">Catalyst</th>
          <th style="padding:8px 6px">Headline / Summary</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>

    <div style="margin-top:24px;padding:16px;background:#f9f9f7;border-radius:8px">
      <div style="font-size:12px;font-weight:600;color:#0b0b0b;margin-bottom:8px">
        CATALYST BREAKDOWN
      </div>
      {catalyst_html}
    </div>

    <p style="margin-top:20px;font-size:11px;color:#898781;line-height:1.6">
      Not financial advice. Price data via yfinance · News via Yahoo Finance.<br>
      Verify all catalysts independently before trading.
    </p>
  </div>
</body>
</html>"""


def email_movers(rows: list[dict]) -> None:
    """Send top movers report via Resend (SDK first, urllib HTTP fallback)."""
    if not RESEND_API_KEY:
        log.error("Missing RESEND_API_KEY — skipping movers email")
        return
    if not rows:
        log.warning("No mover rows to email")
        return

    mode     = rows[0].get("mode", "").upper()
    date_str = _now_et().strftime("%Y-%m-%d %H:%M ET")
    subject  = f"[StockScan] Top {len(rows)} {mode} Movers — {date_str}"

    # Plain-text body
    lines = [f"Top {len(rows)} {mode} Movers — {date_str}\n"]
    for r in rows:
        lines.append(
            f"  {r['rank']:>2}. {r['ticker']:<6} "
            f"{r['direction']}{abs(r['chg_pct']):.2f}%  "
            f"Low ${r['session_low']:.2f}  High ${r['session_high']:.2f}  "
            f"[{r['catalyst']}]"
        )
        if r["headline"] and r["headline"] != "—":
            # Wrap at 80 chars for plain text
            for line in textwrap.wrap(r["headline"], width=80):
                lines.append(f"        {line}")
        lines.append("")
    lines.append("Not financial advice.")
    text_body = "\n".join(lines)

    payload = {
        "from":    RESEND_FROM_EMAIL,
        "to":      [EMAIL_TO],
        "subject": subject,
        "text":    text_body,
        "html":    _build_movers_html(rows),
    }

    # ── Resend SDK ────────────────────────────────────────────────
    if HAVE_RESEND_SDK:
        try:
            resend_sdk.api_key = RESEND_API_KEY
            result = resend_sdk.Emails.send(payload)
            _id = result.get("id") if isinstance(result, dict) else result
            log.info("Movers email sent via SDK (id=%s) → %s", _id, EMAIL_TO)
            return
        except Exception as e:
            log.warning("Resend SDK failed (%s) — trying HTTP fallback", e)

    # ── urllib HTTP fallback ──────────────────────────────────────
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "stock-scanner/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log.info("Movers email sent via HTTP (status %s) → %s", resp.status, EMAIL_TO)
    except urllib.error.HTTPError as e:
        log.error("Resend error %s: %s", e.code, e.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log.error("Email send failed: %s", e)


# ── Scheduler integration hook ────────────────────────────────────────────────

def run_movers_job(mode: str = "auto", top_n: int = 10, send_email: bool = True) -> list[dict]:
    """
    Drop-in job for Scheduler.py _start_scheduler().

    Example:
        from market_movers import run_movers_job
        schedule.every().day.at("08:00").do(run_movers_job, mode="premarket")
        schedule.every().day.at("09:35").do(run_movers_job, mode="live")
        schedule.every().day.at("10:30").do(run_movers_job, mode="live")
        schedule.every().day.at("13:00").do(run_movers_job, mode="live")
    """
    rows = scan_movers(mode=mode, top_n=top_n)
    print_movers_report(rows)
    if send_email:
        email_movers(rows)
    return rows


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Market mover scanner with catalyst detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python market_movers.py                              # auto-detect session
  python market_movers.py --mode premarket             # force pre-market
  python market_movers.py --mode live --top 20         # top 20 live movers
  python market_movers.py --mode afterhours --email    # after-hours + email
  python market_movers.py --tickers NVDA AMD MU LLY   # custom ticker list
        """,
    )
    parser.add_argument(
        "--mode", "--mode",
        choices=["premarket", "live", "afterhours", "closed", "auto"],
        default="auto",
        help="Session mode (default: auto-detect from current ET time)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top movers to show (default: 10)",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Send results via Resend API after printing",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        help="Override default watchlist  e.g. --tickers NVDA AMD MU",
    )
    args = parser.parse_args()

    rows = scan_movers(
        tickers=args.tickers or None,
        mode=args.mode,
        top_n=args.top,
    )
    print_movers_report(rows)

    if args.email:
        email_movers(rows)