"""
app.py — localhost control panel for the trading workstation
=============================================================
A small stdlib-only web app (no Flask/deps) tying the pipeline together:

  - Browse: latest dashboard, all research pages, scan CSVs — everything in
    data/output is served as static files
  - Run: refresh research pages for specific tickers, launch a universe scan
    (writes CSVs + a fresh dashboard), clean up old output files
  - Watch: background job status with auto-refresh while anything runs

Run it:
    python src/stockanalysis/webapp/app.py            # http://localhost:8899
    python -m stockanalysis.webapp.app --port 9000

Jobs run in daemon threads inside this process; one job per kind at a time
(scans are long — a second click shouldn't stack a duplicate). This is a
LOCAL tool: it binds 127.0.0.1 and has no auth — don't expose it.
"""

from __future__ import annotations

import argparse
import html as _html
import sys
import threading
import traceback
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

if __package__ in (None, ""):   # direct run: make `stockanalysis.*` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR   = PROJECT_ROOT / "data" / "output"
DEFAULT_PORT = 8899

UNIVERSES = ("daytrade", "watchlist", "longterm", "dividend", "sp500")


# ─────────────────────────────────────────────────────────────────────────────
# JOBS — one background thread per action kind
# ─────────────────────────────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}          # kind -> {status, detail, started, ...}
_jobs_lock = threading.Lock()


def _job_running(kind: str) -> bool:
    with _jobs_lock:
        j = _jobs.get(kind)
        return bool(j and j["status"] == "running")


def _start_job(kind: str, label: str, fn) -> str:
    """Run fn() on a daemon thread; refuse if that kind is already running."""
    if _job_running(kind):
        return f"a {kind} job is already running — wait for it to finish"

    def _runner():
        try:
            detail = fn()
            with _jobs_lock:
                _jobs[kind].update(status="done", detail=str(detail or "ok"),
                                   finished=datetime.now())
        except Exception as e:
            traceback.print_exc()
            with _jobs_lock:
                _jobs[kind].update(status="failed", detail=str(e)[:300],
                                   finished=datetime.now())

    with _jobs_lock:
        _jobs[kind] = {"label": label, "status": "running", "detail": "",
                       "started": datetime.now(), "finished": None}
    threading.Thread(target=_runner, daemon=True, name=f"job-{kind}").start()
    return ""


def _job_research(tickers: list[str]):
    from stockanalysis.reporting.research import refresh_research
    written = refresh_research(tickers, OUTPUT_DIR, charts=True)
    return f"{len(written)} page(s) refreshed: {', '.join(sorted(written))}"


def _job_scan(universe: str, include_portfolio: bool = True):
    from stockanalysis.scanners import scan_universe as su
    from stockanalysis.reporting.dashboard import generate_dashboard
    tickers = {
        "daytrade": su.DAY_TRADE_TICKERS, "watchlist": su.WATCHLIST_TICKERS,
        "longterm": su.LONGTERM_TICKERS, "dividend": su.DIVIDEND_STOCKS,
        "sp500": su.SP500_TICKERS,
    }[universe]
    rows = su.main(list(tickers))
    generate_dashboard(rows, output_dir=OUTPUT_DIR, open_browser=False,
                       include_portfolio=include_portfolio)
    return (f"scanned {len(rows)} tickers ({universe}); dashboard regenerated"
            + ("" if include_portfolio else " (portfolio panel excluded)"))


def _job_cleanup(days: int):
    from stockanalysis.scheduling.scheduler import cleanup_outputs
    n = cleanup_outputs(days, OUTPUT_DIR)
    return f"removed {n} file(s) older than {days}d"


def _job_news(tickers: list[str] | None):
    from stockanalysis.reporting.research import update_news
    updated = update_news(tickers, OUTPUT_DIR)
    return f"latest news spliced into {len(updated)} research page(s)"


# ─────────────────────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_COLORS = {"running": ("#FAEEDA", "#633806"),
                  "done":    ("#E1F5EE", "#085041"),
                  "failed":  ("#FCEBEB", "#791F1F")}


def _card(title: str, body: str) -> str:
    return (f'<div style="background:white;border:0.5px solid #e1e0d9;'
            f'border-radius:12px;padding:16px 18px;margin-bottom:14px">'
            f'<h3 style="font-size:14px;font-weight:600;margin:0 0 10px">'
            f'{title}</h3>{body}</div>')


def _jobs_html() -> str:
    with _jobs_lock:
        items = sorted(_jobs.items(),
                       key=lambda kv: kv[1]["started"], reverse=True)
    if not items:
        return '<span style="font-size:12px;color:#898781">No jobs run yet this session.</span>'
    rows = []
    for kind, j in items:
        bg, fg = _STATUS_COLORS.get(j["status"], ("#F1EFE8", "#444441"))
        when = j["started"].strftime("%H:%M:%S")
        rows.append(
            f'<div style="display:flex;gap:10px;align-items:baseline;'
            f'font-size:12px;margin:4px 0;flex-wrap:wrap">'
            f'<span style="background:{bg};color:{fg};font-weight:600;'
            f'font-size:11px;padding:2px 9px;border-radius:5px">{j["status"].upper()}</span>'
            f'<b>{_html.escape(j["label"])}</b>'
            f'<span style="color:#898781">started {when}</span>'
            f'<span style="color:#52514e">{_html.escape(j["detail"])}</span></div>')
    return "".join(rows)


def _listing(pattern: str, limit: int, fmt) -> str:
    files = sorted(OUTPUT_DIR.glob(pattern),
                   key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    if not files:
        return '<span style="font-size:12px;color:#898781">none yet — run a scan</span>'
    return "".join(fmt(f) for f in files)


def _home_html() -> str:
    refresh = ('<meta http-equiv="refresh" content="5">'
               if any(j["status"] == "running" for j in _jobs.values()) else "")

    dashboards = _listing("dashboard_*.html", 5, lambda f: (
        f'<div style="font-size:13px;line-height:1.9">📊 '
        f'<a href="/{f.name}">{f.name}</a>'
        f'<span style="color:#898781;font-size:11px;margin-left:8px">'
        f'{datetime.fromtimestamp(f.stat().st_mtime):%b %d %H:%M}</span></div>'))

    research = sorted(OUTPUT_DIR.glob("research/*.html"))
    research_html = ("".join(
        f'<a href="/research/{f.name}" style="display:inline-block;margin:2px 6px 2px 0;'
        f'font-size:12px;background:#f1efea;padding:3px 10px;border-radius:5px;'
        f'text-decoration:none;color:#0b0b0b">📄 {f.stem}</a>'
        for f in research)
        or '<span style="font-size:12px;color:#898781">none yet — refresh some tickers below</span>')

    csvs = _listing("stock_scan_*.csv", 6, lambda f: (
        f'<div style="font-size:12px;line-height:1.8">🗂 '
        f'<a href="/{f.name}">{f.name}</a></div>'))

    universe_opts = "".join(f'<option value="{u}">{u}</option>'
                            for u in UNIVERSES)
    form_style = 'style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"'
    input_style = ('style="font-size:13px;padding:6px 10px;border:1px solid #d9d7ce;'
                   'border-radius:6px"')
    btn = ('style="font-size:13px;font-weight:600;padding:6px 16px;border:none;'
           'border-radius:6px;background:#185FA5;color:white;cursor:pointer"')

    forms = f"""
      <form method="post" action="/run" {form_style}>
        <input type="hidden" name="action" value="research">
        <input name="tickers" placeholder="tickers e.g. NVDA AMD MRVL (blank = day-trade list)"
               size="42" {input_style}>
        <button {btn}>Refresh research</button>
        <span style="font-size:11px;color:#898781">fetches fresh data only for these tickers</span>
      </form>
      <form method="post" action="/run" {form_style.replace('"display', '"margin-top:10px;display')}>
        <input type="hidden" name="action" value="scan">
        <select name="universe" {input_style}>{universe_opts}</select>
        <label style="font-size:12px;color:#0b0b0b;display:flex;gap:5px;align-items:center">
          <input type="checkbox" name="portfolio" checked>
          include Portfolio management</label>
        <button {btn}>Run scan</button>
        <span style="font-size:11px;color:#898781">full pipeline: CSVs + dashboard + research pages (sp500 takes a while)</span>
      </form>
      <form method="post" action="/run" {form_style.replace('"display', '"margin-top:10px;display')}>
        <input type="hidden" name="action" value="news">
        <input name="tickers" placeholder="tickers (blank = all research pages)"
               size="30" {input_style}>
        <button {btn.replace('#185FA5', '#26215C')}>Scan latest news</button>
        <span style="font-size:11px;color:#898781">updates only the news section on existing research pages — fast, no chart/metrics refetch</span>
      </form>
      <form method="post" action="/run" {form_style.replace('"display', '"margin-top:10px;display')}>
        <input type="hidden" name="action" value="cleanup">
        <input name="days" type="number" value="7" min="0" step="1" {input_style} size="4">
        <button {btn.replace('#185FA5', '#8a6d1a')}>Clean up outputs older than N days</button>
      </form>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">{refresh}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Workstation — control panel</title>
<style>body{{font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
background:#faf9f5;color:#0b0b0b;margin:0 auto;padding:20px;max-width:980px}}
a{{color:#185FA5}}</style></head><body>
<h1 style="font-size:24px;margin:0 0 2px">🖥 Trading Workstation</h1>
<p style="font-size:12px;color:#898781;margin:0 0 16px">
  control panel · localhost only · {datetime.now():%B %d, %Y %H:%M}</p>
{_card("Actions", forms)}
{_card("Jobs (this session)", _jobs_html())}
{_card("Dashboards (latest 5)", dashboards)}
{_card(f"Research pages ({len(research)})", research_html)}
{_card("Scan CSVs (latest 6)", csvs)}
<p style="font-size:10px;color:#898781;text-align:center">
  Local tool — no auth, binds 127.0.0.1. Not financial advice.</p>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

class WorkstationHandler(SimpleHTTPRequestHandler):
    """'/' = control panel, POST /run = launch job, everything else = static
    files out of data/output (dashboards, research pages, CSVs)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT_DIR), **kwargs)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = _home_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/run":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode())
        action = (form.get("action") or [""])[0]
        err = "unknown action"

        if action == "research":
            raw = (form.get("tickers") or [""])[0]
            tickers = [t.strip().upper() for t in raw.replace(",", " ").split()
                       if t.strip()]
            if not tickers:
                from stockanalysis.scheduling.scheduler import DAY_TRADE_TICKERS
                tickers = list(DAY_TRADE_TICKERS)
            err = _start_job("research",
                             f"research refresh: {', '.join(tickers[:12])}"
                             + ("…" if len(tickers) > 12 else ""),
                             lambda: _job_research(tickers))
        elif action == "scan":
            universe = (form.get("universe") or ["daytrade"])[0]
            # unchecked checkboxes are absent from the form body
            include_pf = "portfolio" in form
            if universe in UNIVERSES:
                label = (f"scan: {universe}"
                         + ("" if include_pf else " (no portfolio panel)"))
                err = _start_job("scan", label,
                                 lambda: _job_scan(universe, include_pf))
            else:
                err = "unknown universe"
        elif action == "news":
            raw = (form.get("tickers") or [""])[0]
            tickers = [t.strip().upper() for t in raw.replace(",", " ").split()
                       if t.strip()] or None    # None = every research page
            label = ("news scan: all research pages" if tickers is None
                     else f"news scan: {', '.join(tickers[:12])}"
                     + ("…" if len(tickers) > 12 else ""))
            err = _start_job("news", label, lambda: _job_news(tickers))
        elif action == "cleanup":
            try:
                days = max(0, int((form.get("days") or ["7"])[0]))
            except ValueError:
                days = 7
            err = _start_job("cleanup", f"cleanup >{days}d",
                             lambda: _job_cleanup(days))

        # Redirect home either way; errors show up via the jobs panel note
        self.send_response(303)
        self.send_header("Location", "/" + (f"?err={err}" if err else ""))
        self.end_headers()

    def log_message(self, fmt, *args):   # quieter default logging
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Workstation web app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), WorkstationHandler)
    print(f"Workstation control panel → http://localhost:{args.port}  "
          f"(serving {OUTPUT_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
