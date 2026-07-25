"""
app.py — localhost trading workstation web app
================================================
A small stdlib-only web app (no Flask/FastAPI/DB — see the note below on why)
serving five pages: Dashboard (home), Scanner, Research, Portfolio,
Automation. HTTP plumbing lives here; page HTML is built in views.py/pages.py,
background jobs run through jobstore.py, and job/search/watchlist logic lives
in api.py.

Run it:
    python src/stockanalysis/webapp/app.py            # http://localhost:8899
    python -m stockanalysis.webapp.app --port 9000

Why stdlib http.server instead of FastAPI+Redis+Postgres+WebSocket: this is a
single-user local tool run from the command line, not a hosted multi-user
service — there's no concurrent-write contention to arbitrate (a queue/DB
solves), and no client outside this machine to push updates to (a WebSocket
solves). Background jobs run as daemon threads in this one process; the UI
gets "live" progress via short-interval polling (see views.py's pollJobs()),
which is indistinguishable from push at this latency for a single browser
tab. If this ever needs multiple concurrent users or long-running jobs that
must survive a server restart, that's the point to revisit the stack — not
before, since a database and queue are new failure modes and operational
surface for a tool that until then only needs to talk to itself.

LOCAL tool: binds 127.0.0.1, no auth. Don't expose it beyond localhost.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):   # direct run: make `stockanalysis.*` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stockanalysis.webapp import api, jobstore, pages
from stockanalysis.webapp.views import render_page, OUTPUT_DIR

DEFAULT_PORT = 8899

# path -> page-body function returning (html_body, extra_js)
ROUTES = {
    "/":            ("dashboard", "Dashboard", pages.dashboard_page),
    "/ai-sentiment":("ai-sentiment", "AI Sentiment", pages.ai_sentiment_page),
    "/scanner":     ("scanner",   "Scanner",   pages.scanner_page),
    "/research":    ("research",  "Research",  pages.research_page),
    "/portfolio":   ("portfolio", "Portfolio", pages.portfolio_page),
    "/journal":     ("journal",   "Journal",   pages.journal_page),
    "/alerts":      ("alerts",    "Alerts",    pages.alerts_page),
    "/automation":  ("automation","Automation",pages.automation_page),
}


class WorkstationHandler(SimpleHTTPRequestHandler):
    """Known page paths render through views/pages; everything else falls
    through to static file serving out of data/output (dashboards, research
    pages, scan CSVs, snapshot.json)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT_DIR), **kwargs)

    # ── GET ──────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if path in ROUTES:
            _, title, page_fn = ROUTES[path]
            try:
                body, extra_js = page_fn()
            except Exception as e:
                import traceback
                traceback.print_exc()
                body, extra_js = (f'<div style="background:#FCEBEB;color:#791F1F;'
                                  f'padding:16px;border-radius:12px">Page failed to '
                                  f'render: {e}</div>', "")
            self._send_html(render_page(path.strip("/") or "dashboard",
                                        title, body, extra_js))
            return

        if path == "/api/jobs":
            self._send_json(jobstore.current())
            return

        if path == "/api/search":
            q = (query.get("q") or [""])[0]
            self._send_json(api.search_tickers(q))
            return

        super().do_GET()

    # ── POST ─────────────────────────────────────────────────────────────────
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode())

        if self.path == "/run":
            action = (form.get("action") or [""])[0]
            err = api.dispatch_run(action, form)
            self._send_json({"ok": not err, "message": err or "Started"})
            return

        if self.path == "/api/watchlist/toggle":
            name = (form.get("name") or ["Starred"])[0]
            ticker = (form.get("ticker") or [""])[0]
            if not ticker:
                self._send_json({"ok": False, "message": "no ticker given"})
                return
            watchlists = api.watchlist_toggle(name, ticker)
            self._send_json({"ok": True, "watchlists": watchlists})
            return

        if self.path == "/api/schedule/save":
            self._send_json(api.schedule_save(form))
            return

        if self.path == "/api/portfolio/save":
            self._send_json(api.portfolio_save(form))
            return

        if self.path == "/api/portfolio/delete":
            ticker = (form.get("ticker") or [""])[0]
            self._send_json(api.portfolio_delete(ticker))
            return

        if self.path == "/api/journal/save":
            self._send_json(api.journal_save(form))
            return

        if self.path == "/api/journal/delete":
            trade_id = (form.get("trade_id") or [""])[0]
            self._send_json(api.journal_delete(trade_id))
            return

        self.send_error(404)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _send_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data) -> None:
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):   # quieter default access logging
        pass


def _start_scheduler_thread() -> None:
    """Run the scheduling loop (pre-market/intraday/close scans, emails,
    nightly cleanup) on a daemon thread inside this process, so starting the
    web UI is enough to have jobs firing on schedule — no second terminal
    running scheduler.py needed."""
    from stockanalysis.scheduling.scheduler import _start_scheduler

    def _runner():
        try:
            _start_scheduler()
        except Exception:
            import traceback
            traceback.print_exc()

    threading.Thread(target=_runner, daemon=True, name="scheduler-loop").start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading workstation web app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-scheduler", action="store_true",
                        help="Don't run the automatic scan scheduler alongside the UI")
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.no_scheduler:
        _start_scheduler_thread()
        print("Scheduler loop → running in background (pass --no-scheduler to disable)")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), WorkstationHandler)
    print(f"Trading Workstation → http://localhost:{args.port}  "
          f"(serving {OUTPUT_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
