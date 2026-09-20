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

# How old the sector-leader scan may be before the brief re-runs it.
# The brief fires at 07:00 and again at 08:00; the scan takes three to five
# minutes, and the daily bars it reads do not change between those two runs.
# So the first run scans and the second reuses it, and a snapshot left over
# from yesterday evening is refreshed rather than quietly presented as this
# morning's structure.
SECTOR_LEADERS_MAX_AGE_H = 3.0


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

    leaders_snap, leaders_note = _ensure_sector_leaders()

    active = alerts.load_active()
    near_breakout = [v["alert"] for v in active.values()
                    if v["alert"]["dedup_key"].endswith((":breakout", ":resistance_touch"))]

    sectors = sorted(pulse.get("sectors") or [], key=lambda s: s["chg_pct"], reverse=True)

    brief = {
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
        "sector_leaders": _leaders_digest(leaders_snap),
        "sector_leaders_note": leaders_note,
    }
    # Computed after the rest of the dict exists because it cross-references
    # the movers and earnings blocks against the scan.
    from stockanalysis.core import leaders_confluence
    brief["confluence"] = leaders_confluence.compute(brief, leaders_snap)
    return brief


def _ensure_sector_leaders() -> tuple[dict | None, str]:
    """The sector-leader snapshot, re-scanned if it is too old to describe
    this morning.

    Returns (snapshot, note). Never raises — a scan failure leaves the brief
    with whatever snapshot already existed and a note saying so, because a
    failed cross-reference must not cost the user the rest of the email.
    """
    from stockanalysis.core import leaders_store

    snap = leaders_store.load()
    age = leaders_store.age_hours(snap)
    if snap and age is not None and age <= SECTOR_LEADERS_MAX_AGE_H:
        return snap, f"reused the scan from {age:.1f}h ago"

    try:
        from stockanalysis.scanners.scan_sector_leaders import scan_and_store
        scan_and_store()
        snap = leaders_store.load()
        return snap, "scanned fresh for this brief"
    except Exception as e:
        note = f"sector-leader scan failed ({str(e)[:120]})"
        if snap:
            note += f" — cross-referencing the previous snapshot ({age:.1f}h old)"
        return snap, note


def _leaders_digest(snap: dict | None) -> dict:
    """The scan reduced to what belongs in an email.

    The stored snapshot is three megabytes — every candidate carries its full
    metrics block. The brief persists to disk and is re-read by the Alerts
    page, so it keeps the ranked shortlist and the sector table, not the
    scoring inputs behind them.
    """
    if not snap:
        return {}
    from stockanalysis.core.sector_leaders import scored_rows

    def slim(r):
        setup = r.get("setup") or {}
        lv = setup.get("levels") or {}
        return {
            "ticker": r.get("ticker"), "group": r.get("group"),
            "confidence": (r.get("confidence") or {}).get("score"),
            "confidence_label": (r.get("confidence") or {}).get("label"),
            "confluence": (r.get("confluence") or {}).get("score"),
            "leadership": (r.get("leadership") or {}).get("score"),
            "leadership_band": (r.get("leadership") or {}).get("band"),
            "clarity": (r.get("clarity") or {}).get("score"),
            "setup": setup.get("setup"), "grade": setup.get("grade"),
            "rr": setup.get("rr"), "news_verdict": r.get("news_verdict"),
            "entry_low": lv.get("entry_low"), "entry_high": lv.get("entry_high"),
            "stop": lv.get("stop"), "target1": lv.get("target1"),
            "target2": lv.get("target2"),
        }

    return {
        "generated": snap.get("generated"),
        "market": (snap.get("market") or {}).get("label"),
        "market_score": (snap.get("market") or {}).get("score"),
        "sectors": [{"name": x.get("name"), "etf": x.get("etf"),
                     "score": (x.get("scores") or {}).get("score"),
                     "trend": (x.get("scores") or {}).get("quality_label"),
                     "direction": x.get("direction"),
                     "r1d": (x.get("metrics") or {}).get("r1d"),
                     "r20d": (x.get("metrics") or {}).get("r20d")}
                    for x in (snap.get("sectors") or [])],
        "longs": [slim(r) for r in scored_rows(snap, "long", 6)],
        "shorts": [slim(r) for r in scored_rows(snap, "short", 6)],
        "data_gaps": snap.get("data_gaps") or [],
    }


def _leaders_text(brief: dict) -> str:
    d = brief.get("sector_leaders") or {}
    if not d:
        return f"\nSECTOR LEADERS\n  {brief.get('sector_leaders_note') or 'no scan available'}\n"

    lines = ["", "SECTOR LEADERS",
             f"  market: {d.get('market')} ({d.get('market_score'):+.1f})"
             f"  [{brief.get('sector_leaders_note')}]"]
    top = [s for s in d.get("sectors") or []][:4]
    bottom = [s for s in d.get("sectors") or []][-3:]
    if top:
        lines.append("  strongest: " + ", ".join(
            f"{s['name']} {s['score']:.0f} ({s['trend']})" for s in top))
    if bottom:
        lines.append("  weakest:   " + ", ".join(
            f"{s['name']} {s['score']:.0f} ({s['trend']})" for s in bottom))

    for label, rows in (("BULLISH LEADERS", d.get("longs") or []),
                        ("BEARISH LEADERS", d.get("shorts") or [])):
        lines.append(f"\n  {label}")
        if not rows:
            lines.append("    none")
        for r in rows:
            lines.append(
                f"    {r['ticker']:6} {r['group'][:20]:20} conf "
                f"{r['confidence']:.0f} · {r['setup']} [{r['grade']}] · "
                f"news {r['news_verdict']}")
            if r.get("entry_low") is not None:
                lines.append(f"      entry {r['entry_low']}–{r['entry_high']} · "
                             f"stop {r['stop']} · T1 {r['target1']} · R:R {r['rr']}")
    return "\n".join(lines) + "\n"


def _leaders_html(brief: dict) -> str:
    d = brief.get("sector_leaders") or {}
    if not d:
        return (f'<div style="margin-bottom:14px"><h3 style="font-size:15px;'
                f'margin:0 0 4px">Sector leaders</h3>'
                f'<div style="font-size:12px;color:#8a6d1a">'
                f'{brief.get("sector_leaders_note") or "no scan available"}'
                f'</div></div>')

    def rows_html(rows, accent):
        if not rows:
            return '<div style="font-size:12px;color:#898781">none</div>'
        out = []
        for r in rows:
            levels = ("no entry priced" if r.get("entry_low") is None else
                      f'entry {r["entry_low"]}–{r["entry_high"]} · stop '
                      f'{r["stop"]} · T1 {r["target1"]} · R:R {r["rr"]}')
            out.append(
                f'<div style="border-left:3px solid {accent};padding:4px 0 4px 9px;'
                f'margin-bottom:6px">'
                f'<div style="font-size:13px"><b>{r["ticker"]}</b> '
                f'<span style="color:#898781">{r["group"]}</span> · '
                f'confidence <b>{r["confidence"]:.0f}</b> · '
                f'leadership {r["leadership"]:.0f}</div>'
                f'<div style="font-size:12px">{r["setup"]} [{r["grade"]}] · '
                f'news {r["news_verdict"]}</div>'
                f'<div style="font-size:12px;color:#444441">{levels}</div>'
                f'</div>')
        return "".join(out)

    sectors = d.get("sectors") or []
    table = "".join(
        f'<div style="font-size:12px;margin-bottom:1px">'
        f'<b>{s["name"]}</b> <span style="color:#898781">{s["etf"]}</span> '
        f'{s["score"]:.0f}/100 · {s["r1d"]:+.2f}% 1D · {s["r20d"]:+.2f}% 20D '
        f'— {s["trend"]}</div>'
        for s in sectors[:5] + sectors[-3:])

    return f"""
      <div style="margin-bottom:16px;border-top:2px solid #eceae4;padding-top:12px">
        <h3 style="font-size:15px;margin:0 0 4px">Sector leaders</h3>
        <div style="font-size:11px;color:#898781;margin-bottom:8px">
          {d.get('market')} ({d.get('market_score'):+.1f}) ·
          {brief.get('sector_leaders_note')}
        </div>
        <div style="margin-bottom:10px">{table}</div>
        <div style="font-size:13px;font-weight:600;margin:8px 0 4px">Bullish leaders</div>
        {rows_html(d.get("longs") or [], "#0F6E56")}
        <div style="font-size:13px;font-weight:600;margin:12px 0 4px">Bearish leaders</div>
        {rows_html(d.get("shorts") or [], "#A32D2D")}
      </div>"""


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

    # Sector leaders and the cross-reference go last: the macro/movers block
    # above is what the brief has always led with, and the confluence section
    # only makes sense once both of its inputs have been read.
    from stockanalysis.core import leaders_confluence

    lines.append(_leaders_text(brief))
    lines.append(leaders_confluence.render_text(brief.get("confluence") or {}))

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
      {_leaders_html(brief)}
      {_confluence_html(brief)}
      <p style="font-size:11px;color:#898781">Not financial advice.</p>
    </div>"""
    return sections


def _confluence_html(brief: dict) -> str:
    from stockanalysis.core import leaders_confluence
    return leaders_confluence.render_html(brief.get("confluence") or {})


def send_premarket_brief(watchlist_tickers: list[str] | None = None) -> dict:
    """Generates the brief, emails it (Low-priority routine summary — one
    email regardless of what it contains, per the master prompt's delivery
    rules), persists it so the Alerts page can show the latest one without
    re-running every fetch, and returns the brief dict."""
    from stockanalysis.scanners.market_movers import send_resend_email

    brief = generate_premarket_brief(watchlist_tickers)
    date_str = brief["generated_at"][:10]
    # One email, not two. The sector-leader scan and the movers brief are
    # separate engines but a single morning read, and the confluence section
    # only exists because they arrive together.
    conf = brief.get("confluence") or {}
    counts = conf.get("counts") or {}
    tag = ""
    if conf.get("available"):
        tag = (f" · {counts.get('aligned', 0)} aligned, "
               f"{counts.get('conflicts', 0)} conflicting")
    send_resend_email(
        f"[Trading Assistant] Pre-Market Brief + Sector Leaders — {date_str}{tag}",
        render_text(brief), render_html(brief))

    LATEST_BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
    # default=str: econ_events carry real datetime objects (from
    # market_movers.fetch_economic_events), not preformatted strings
    LATEST_BRIEF_PATH.write_text(json.dumps(brief, indent=2, default=str))
    return brief
