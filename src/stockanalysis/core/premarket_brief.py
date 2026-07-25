"""
premarket_brief.py
===================
The 7:00-8:30 AM Pre-Market Brief from the master prompt: one "under 5
minutes to read" snapshot of what's actually likely to matter for today's
trading, built entirely from data this project already fetches elsewhere
(market_movers' pulse/movers, earnings_sentiment's earnings history, and
ai_pulse's yield/DXY fetches) — no new data source, just one composition
layer plus text/HTML rendering and an email send.

"Stocks near breakout" is deliberately NOT a fresh scan here — running the
full scan pipeline against the whole watchlist just for the brief would be
slow and duplicate what the Watchlist Alert monitor (core/watchlist_alerts)
already computes on its own schedule. Instead this reads whatever breakout/
resistance-touch alerts are already active in core/alerts' state, so the
brief and the alert feed always agree with each other.

Explicitly out of scope (per the same data-availability call as
core/alerts.py): general "options market highlights" beyond the expected
move already available for today's earnings names.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LATEST_BRIEF_PATH = PROJECT_ROOT / "data" / "premarket_brief.json"


def load_latest_brief() -> dict | None:
    """Reads whatever send_premarket_brief() last wrote — works regardless
    of whether that call came from the webapp's scheduler thread, a
    standalone `python scheduler.py` process, or the Alerts page's
    "Generate Now" button, since all three call the same function."""
    if not LATEST_BRIEF_PATH.exists():
        return None
    try:
        return json.loads(LATEST_BRIEF_PATH.read_text())
    except Exception:
        return None


def _default_watchlist_tickers() -> list[str]:
    """Same default as the Watchlist Alert monitor (just the "watchlist"
    category, not every saved list) — see
    core.watchlist_alerts.default_alert_tickers for why."""
    from stockanalysis.core.watchlist_alerts import default_alert_tickers
    return default_alert_tickers()


def generate_premarket_brief(watchlist_tickers: list[str] | None = None) -> dict:
    """Never raises — any sub-fetch failure just leaves that section empty
    rather than aborting the whole brief (same convention as market_pulse)."""
    from stockanalysis.scanners.market_movers import market_pulse, scan_movers
    from stockanalysis.scanners.ai_pulse import _yield_change_bps, _dxy_change_pct
    from stockanalysis.core.earnings_sentiment import fetch_earnings_history
    from stockanalysis.core import alerts

    watchlist_tickers = watchlist_tickers if watchlist_tickers is not None else _default_watchlist_tickers()

    try:
        pulse = market_pulse(top_n=10, with_catalysts=True)
    except Exception:
        pulse = {}

    try:
        movers = scan_movers(mode="premarket", top_n=10)
    except Exception:
        movers = []
    gainers = sorted((m for m in movers if m.get("chg_pct", 0) > 0),
                     key=lambda m: m["chg_pct"], reverse=True)[:5]
    losers = sorted((m for m in movers if m.get("chg_pct", 0) < 0),
                    key=lambda m: m["chg_pct"])[:5]
    unusual_movers = [m for m in movers if abs(m.get("chg_pct", 0)) >= 3]

    try:
        yield_10y = _yield_change_bps()
    except Exception:
        yield_10y = {"level_pct": None, "change_bps": None, "error": "fetch failed"}
    try:
        dollar_index = _dxy_change_pct()
    except Exception:
        dollar_index = {"level": None, "change_pct": None, "error": "fetch failed"}

    earnings_today = []
    for ticker in watchlist_tickers:
        try:
            hist = fetch_earnings_history(ticker)
        except Exception:
            continue
        if hist.get("days_to_earnings") == 0:
            earnings_today.append({
                "ticker": ticker,
                "next_earnings_date": hist.get("next_earnings_date"),
                "avg_abs_move_pct": hist.get("avg_abs_move_pct"),
            })

    active = alerts.load_active()
    near_breakout = [v["alert"] for v in active.values()
                    if v["alert"]["dedup_key"].endswith((":breakout", ":resistance_touch"))]

    sectors = sorted(pulse.get("sectors") or [], key=lambda s: s["chg_pct"], reverse=True)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "vix": pulse.get("vix") or {},
        "futures": pulse.get("futures") or [],
        "macro": pulse.get("macro") or [],          # oil/gold/bitcoin
        "yield_10y": yield_10y,
        "dollar_index": dollar_index,
        "fed": pulse.get("fed") or {},
        "econ_events": pulse.get("econ_events") or [],
        "spy": pulse.get("spy") or {},
        "qqq": pulse.get("qqq") or {},
        "sectors_trending": sectors[:3],
        "sectors_lagging": list(reversed(sectors[-3:])) if sectors else [],
        "gainers": gainers,
        "losers": losers,
        "unusual_movers": unusual_movers,
        "earnings_today": earnings_today,
        "near_breakout": near_breakout,
    }


def render_text(brief: dict) -> str:
    lines = [f"Pre-Market Brief — {brief['generated_at'][:16].replace('T', ' ')}\n"]

    vix = brief["vix"]
    if vix.get("level") is not None:
        lines.append(f"VIX {vix['level']:.2f} ({vix.get('change_pct', 0):+.1f}%) — {vix.get('label')}")
    for f in brief["futures"]:
        lines.append(f"{f['label']}: {f['price']:.2f} ({f['chg_pct']:+.2f}%)")
    for m in brief["macro"]:
        lines.append(f"{m['label']}: {m['price']:.2f} ({m['chg_pct']:+.2f}%)")
    y = brief["yield_10y"]
    if y.get("level_pct") is not None:
        lines.append(f"10Y yield: {y['level_pct']:.3f}% ({y.get('change_bps', 0):+d} bps)")
    d = brief["dollar_index"]
    if d.get("level") is not None:
        lines.append(f"Dollar Index: {d['level']:.2f} ({d.get('change_pct', 0):+.2f}%)")
    lines.append("")

    if brief["sectors_trending"]:
        lines.append("Trending sectors: " + ", ".join(
            f"{s['label']} {s['chg_pct']:+.1f}%" for s in brief["sectors_trending"]))
    if brief["sectors_lagging"]:
        lines.append("Lagging sectors: " + ", ".join(
            f"{s['label']} {s['chg_pct']:+.1f}%" for s in brief["sectors_lagging"]))
    lines.append("")

    if brief["gainers"]:
        lines.append("Pre-market gainers: " + ", ".join(
            f"{g['ticker']} +{g['chg_pct']:.1f}%" for g in brief["gainers"]))
    if brief["losers"]:
        lines.append("Pre-market losers: " + ", ".join(
            f"{l['ticker']} {l['chg_pct']:.1f}%" for l in brief["losers"]))
    if brief["unusual_movers"]:
        lines.append("Unusual pre-market activity: " + ", ".join(
            f"{u['ticker']} ({u['chg_pct']:+.1f}%)" for u in brief["unusual_movers"]))
    lines.append("")

    if brief["earnings_today"]:
        lines.append("Earnings today:")
        for e in brief["earnings_today"]:
            move = f"±{e['avg_abs_move_pct']:.1f}% historical avg move" if e.get("avg_abs_move_pct") else "no history"
            lines.append(f"  {e['ticker']} — {move}")
    if brief["near_breakout"]:
        lines.append("Watchlist tickers near a key level:")
        for a in brief["near_breakout"]:
            lines.append(f"  {a['ticker']} — {a['headline']}")
    if brief["econ_events"]:
        lines.append("Upcoming economic events:")
        for e in brief["econ_events"]:
            when = e["when"].strftime("%a %H:%M ET") if hasattr(e["when"], "strftime") else str(e["when"])
            lines.append(f"  {when} — {e['title']} ({e['impact']})")

    lines.append("\nNot financial advice.")
    return "\n".join(lines)


def render_html(brief: dict) -> str:
    def row(label, value):
        return f'<div style="font-size:12px;margin-bottom:2px"><b>{label}:</b> {value}</div>'

    macro_rows = ""
    vix = brief["vix"]
    if vix.get("level") is not None:
        macro_rows += row("VIX", f"{vix['level']:.2f} ({vix.get('change_pct', 0):+.1f}%) — {vix.get('label')}")
    for f in brief["futures"]:
        macro_rows += row(f["label"], f"{f['price']:.2f} ({f['chg_pct']:+.2f}%)")
    for m in brief["macro"]:
        macro_rows += row(m["label"], f"{m['price']:.2f} ({m['chg_pct']:+.2f}%)")
    y = brief["yield_10y"]
    if y.get("level_pct") is not None:
        macro_rows += row("10Y yield", f"{y['level_pct']:.3f}% ({y.get('change_bps', 0):+d} bps)")
    d = brief["dollar_index"]
    if d.get("level") is not None:
        macro_rows += row("Dollar Index", f"{d['level']:.2f} ({d.get('change_pct', 0):+.2f}%)")

    def ticker_list(items, fmt):
        return ", ".join(fmt(i) for i in items) if items else "—"

    sections = f"""
    <div style="font-family:sans-serif;max-width:640px">
      <h2 style="font-size:17px">Pre-Market Brief — {brief['generated_at'][:16].replace('T', ' ')}</h2>
      <div style="margin-bottom:14px">{macro_rows}</div>
      <div style="margin-bottom:14px">
        {row("Trending sectors", ticker_list(brief["sectors_trending"], lambda s: f"{s['label']} {s['chg_pct']:+.1f}%"))}
        {row("Lagging sectors", ticker_list(brief["sectors_lagging"], lambda s: f"{s['label']} {s['chg_pct']:+.1f}%"))}
      </div>
      <div style="margin-bottom:14px">
        {row("Pre-market gainers", ticker_list(brief["gainers"], lambda g: f"{g['ticker']} +{g['chg_pct']:.1f}%"))}
        {row("Pre-market losers", ticker_list(brief["losers"], lambda l: f"{l['ticker']} {l['chg_pct']:.1f}%"))}
        {row("Unusual pre-market activity", ticker_list(brief["unusual_movers"], lambda u: f"{u['ticker']} ({u['chg_pct']:+.1f}%)"))}
      </div>
      <div style="margin-bottom:14px">
        {row("Earnings today", ticker_list(brief["earnings_today"], lambda e: e["ticker"]))}
        {row("Near a key level", ticker_list(brief["near_breakout"], lambda a: f"{a['ticker']} ({a['headline']})"))}
      </div>
      <p style="font-size:11px;color:#898781">Not financial advice.</p>
    </div>"""
    return sections


def send_premarket_brief(watchlist_tickers: list[str] | None = None) -> dict:
    """Generates the brief, emails it (Low-priority routine summary — one
    email regardless of what it contains, per the master prompt's delivery
    rules), persists it so the Alerts page can show the latest one without
    re-running every fetch, and returns the brief dict."""
    from stockanalysis.scanners.market_movers import send_resend_email

    brief = generate_premarket_brief(watchlist_tickers)
    date_str = brief["generated_at"][:10]
    send_resend_email(f"[Trading Assistant] Pre-Market Brief — {date_str}",
                      render_text(brief), render_html(brief))

    LATEST_BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
    # default=str: econ_events carry real datetime objects (from
    # market_movers.fetch_economic_events), not preformatted strings
    LATEST_BRIEF_PATH.write_text(json.dumps(brief, indent=2, default=str))
    return brief
