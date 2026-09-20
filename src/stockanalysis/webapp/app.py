"""
app.py — localhost trading workstation web app
================================================
A small stdlib-only web app (no Flask/FastAPI/DB — see the note below on why)
serving the workstation's pages: Dashboard (home), AI Sentiment, Scanner,
Research, Portfolio, Journal, Alerts, Day Trade, Automation. HTTP plumbing
lives here; page HTML is built in views.py/pages.py, background jobs run
through jobstore.py, and job/search/watchlist logic lives in api.py.

Day Trade is the one section not backed by `stockanalysis.*`: it's the SPY
0DTE signal engine in the sibling `spydaytrader` package, wired in here as an
independent feature. It shares this server, this layout and the market-regime
scorer, and nothing else — its state lives under data/spy/ and its core logic
imports nothing from stockanalysis.core.

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

LOCAL tool: binds 127.0.0.1. Every route is behind a login (see auth.py) —
that's a second lock on an already-closed door, and the reason to have it is
that the door does get opened: a `--host 0.0.0.0` one day, an SSH tunnel, a
screen-shared laptop, or another process on this machine talking to port 8899.
Binding to localhost is still the primary control; don't expose it without
putting TLS in front and setting WORKSTATION_BEHIND_TLS=1, which is what marks
the session cookie Secure (see auth.BEHIND_TLS). The intended way to reach
this from another device is a tunnel to 127.0.0.1 — `tailscale serve` or a
Cloudflare tunnel — which terminates HTTPS without this process ever listening
on a public interface.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import inspect
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

if __package__ in (None, ""):   # direct run: make `stockanalysis.*` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from spydaytrader.webapp import pages as spy_pages
from stockanalysis.webapp import api, auth, jobstore, pages
from stockanalysis.webapp.login_view import render_login
from stockanalysis.webapp.views import render_page, OUTPUT_DIR

DEFAULT_PORT = 8899

# Flipped off by --no-auth. A module global rather than a handler attribute
# because ThreadingHTTPServer constructs a fresh handler per request.
AUTH_ENABLED = True

# Reachable without a session. Deliberately tiny: everything else — pages,
# API endpoints, and the static research/scan files under data/output — is
# gated, since those files *are* the data worth protecting.
PUBLIC_PATHS = {"/login", "/favicon.ico"}

# path -> page-body function returning (html_body, extra_js)
ROUTES = {
    "/":            ("dashboard", "Dashboard", pages.dashboard_page),
    "/ai-sentiment":("ai-sentiment", "AI Sentiment", pages.ai_sentiment_page),
    "/scanner":     ("scanner",   "Scanner",   pages.scanner_page),
    "/screener":    ("screener",  "Screener",  pages.screener_page),
    "/research":    ("research",  "Research",  pages.research_page),
    "/portfolio":   ("portfolio", "Portfolio", pages.portfolio_page),
    "/journal":     ("journal",   "Journal",   pages.journal_page),
    "/alerts":      ("alerts",    "Alerts",    pages.alerts_page),
    "/daytrade":    ("daytrade",  "Day Trade", spy_pages.daytrade_page),
    "/daytrade/proposals": ("daytrade", "Day Trade · Proposals",
                            spy_pages.daytrade_proposals_page),
    # Its own route rather than a /daytrade sub-page: that section is the SPY
    # 0DTE options engine and this ranks equities across all three market-cap
    # profiles. Nesting them would imply a shared model they do not have.
    "/stockdaytrade": ("stockdaytrade", "StockDayTrade", pages.stockdaytrade_page),
    # Former path, kept so existing bookmarks and any saved links still land
    # on the page. It renders identically and lights up the same nav entry.
    "/smallcap":      ("stockdaytrade", "StockDayTrade", pages.stockdaytrade_page),
    "/longterm":    ("longterm",  "Long-Term Buy Engine", pages.longterm_page),
    # Its own route rather than a /scanner or /screener sub-page: those rank
    # names against each other on blended scores. This one answers a prior
    # question — which of fifteen states each name is in — and its output is
    # a classification, not a ranking. It also deliberately reports trend and
    # entry as two columns that are allowed to disagree, which no other page
    # here does.
    "/trend":       ("trend",     "Trend Regime", pages.trend_page),
    # Its own route rather than a /longterm sub-page: it consumes that
    # engine's company verdict wholesale, but adds an options layer with
    # its own slow scan, its own snapshot and its own staleness rules.
    "/csp":         ("csp",       "Cash-Secured Puts", pages.csp_page),
    # Both directions on one page. Not folded into /longterm because that
    # engine answers "is this worth owning" and stops; this one treats its
    # rejections as input rather than as a verdict.
    "/shortside":   ("shortside", "Long / Short", pages.shortside_page),
    # Its own route rather than a /longterm sub-page: that engine gates on
    # quality and valuation and would reject this entire population on the
    # first gate. This one ranks companies that could BECOME quality, and
    # has no valuation term at all.
    "/compounder":  ("compounder", "Future Compounders",
                     pages.compounder_page),
    # Its own route rather than a /scanner sub-page: Scanner ranks a ticker
    # universe you chose, this one decides which universe should matter today
    # by scoring the sectors first and only then the names inside them.
    "/leaders":     ("leaders",   "Sector Leaders", pages.leaders_page),
    # Its own route rather than a /portfolio sub-page: that page answers
    # "what do I hold and what is it doing", this one answers "will this
    # fund the rest of our lives". Different horizon, different inputs
    # (spending, savings, ages, goals) and a verdict that a position table
    # has no way to express.
    "/retirement":  ("retirement", "Retirement", pages.retirement_page),
    # Its own route rather than a /longterm panel: every other page here
    # answers a question about a company's own numbers, and this one answers
    # who else is positioned in it. Different source (SEC filings, not market
    # data), different clock (quarterly and months stale, not daily) — folding
    # it into a page that refreshes every morning would lend it a freshness it
    # does not have.
    "/institutional": ("institutional", "Institutional Options",
                       pages.institutional_page),
    "/automation":  ("automation","Automation",pages.automation_page),
}


def _wants_query(fn) -> bool:
    """Whether a page function should be handed the parsed query string.

    Pages take no arguments by default. One whose state lives in the URL —
    filters, an overridden market regime — declares a single parameter and
    gets the parse_qs dict. Detected from the signature rather than listed in
    ROUTES so adding such a page means touching one file instead of two.
    """
    try:
        return bool(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return False


class WorkstationHandler(SimpleHTTPRequestHandler):
    """Known page paths render through views/pages; everything else falls
    through to static file serving out of data/output (dashboards, research
    pages, scan CSVs, snapshot.json)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT_DIR), **kwargs)

    # ── auth ─────────────────────────────────────────────────────────────────
    def _session(self):
        return auth.session_for(auth.parse_cookie(self.headers.get("Cookie")))

    def _authorized(self, path: str) -> bool:
        """True if this request may proceed. Otherwise it has already been
        answered — with a redirect to /login for a page, or a 401 for an API
        call, so a fetch() from a stale tab surfaces as an error instead of
        the login page's HTML arriving where JSON was expected."""
        if not AUTH_ENABLED or path in PUBLIC_PATHS or self._session():
            return True
        if path.startswith("/api/"):
            self._send_json({"ok": False, "error": "not signed in"}, status=401)
        else:
            self._redirect(f"/login?next={quote(self.path, safe='')}")
        return False

    @staticmethod
    def _safe_next(raw: str) -> str:
        """Sanitise the post-login redirect target.

        Anything not a single-slash-rooted local path becomes "/". Without
        this, /login?next=https://evil.example turns the login form into an
        open redirect, and "//evil.example" is protocol-relative — it looks
        local but isn't."""
        return raw if raw.startswith("/") and not raw.startswith("//") else "/"

    # ── GET ──────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if path == "/login":
            if not AUTH_ENABLED or self._session():
                self._redirect("/")
                return
            self._send_html(render_login(
                next_path=self._safe_next((query.get("next") or ["/"])[0])))
            return

        if not self._authorized(path):
            return

        if path in ROUTES:
            nav_key, title, page_fn = ROUTES[path]
            try:
                body, extra_js = (page_fn(query) if _wants_query(page_fn)
                                  else page_fn())
            except Exception as e:
                import traceback
                traceback.print_exc()
                body, extra_js = (f'<div style="background:#FCEBEB;color:#791F1F;'
                                  f'padding:16px;border-radius:12px">Page failed to '
                                  f'render: {e}</div>', "")
            # nav_key, not the path: sub-pages like /daytrade/proposals must
            # still light up their parent's sidebar entry.
            sess = self._session()
            self._send_html(render_page(nav_key, title, body, extra_js,
                                        user=sess["username"] if sess else None))
            return

        if path == "/api/jobs":
            self._send_json(jobstore.current())
            return

        if path == "/api/search":
            q = (query.get("q") or [""])[0]
            self._send_json(api.search_tickers(q))
            return

        if path == "/api/analysis":
            # HTML, not JSON — same reasoning as /api/regime below: the
            # Dashboard panel shows the Long-Term engine's own cells and
            # reasoning grid, and a JSON contract here would mean a second
            # copy of fifteen cell renderers in JavaScript.
            from stockanalysis.webapp import longterm_view
            scope = (query.get("scope") or ["all"])[0]
            value = (query.get("value") or [""])[0]
            try:
                panel = longterm_view.analysis_panel(api.analysis(scope, value))
            except Exception as e:
                import traceback
                traceback.print_exc()
                panel = longterm_view.analysis_panel(
                    {"error": f"analysis failed: {e}"})
            self._send_html(panel.encode())
            return

        if path == "/api/screener/meta":
            self._send_json(api.screener_meta())
            return

        if path == "/api/screener/ticker":
            q = (query.get("q") or [""])[0]
            self._send_json(api.screener_ticker(q))
            return

        if path == "/api/screener/suggest":
            q = (query.get("q") or [""])[0]
            self._send_json(api.screener_suggest(q))
            return

        super().do_GET()

    # ── POST ─────────────────────────────────────────────────────────────────
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        # rfile can only be drained once, and the Day Trade endpoints post JSON
        # while everything else posts a form — so keep the raw bytes too.
        raw = self.rfile.read(length) if length else b""
        form = parse_qs(raw.decode())

        if self.path == "/login":
            self._do_login(form)
            return

        if self.path == "/logout":
            auth.destroy_session(auth.parse_cookie(self.headers.get("Cookie")))
            self._redirect("/login", cookie=auth.clear_cookie_header())
            return

        if not self._authorized(urlparse(self.path).path):
            return

        if self.path == "/api/spy/scan":
            from spydaytrader.daemon import scheduler as spy_scheduler
            try:
                self._send_json(spy_scheduler.job_scan_spy(force=True))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_json({"error": f"scan failed: {e}"})
            return

        if self.path == "/api/spy/proposals/decide":
            from spydaytrader.core import trade_proposals as spy_proposals
            try:
                payload = json.loads(raw)
                proposal_id, status = payload["id"], payload["status"]
            except (ValueError, KeyError, TypeError):
                self._send_json({"error": "expected JSON {id, status}"})
                return
            if status not in ("approved", "rejected"):
                self._send_json({"error": "status must be 'approved' or 'rejected'"})
                return
            updated = spy_proposals.set_status(proposal_id, status)
            self._send_json(updated or {"error": f"no proposal {proposal_id}"})
            return

        if self.path == "/api/regime":
            # Runs the shared market-regime scorer (~15s) and returns rendered
            # HTML rather than JSON — the report is presentation, not an API
            # contract. Kept out of jobstore because it's a short synchronous
            # read the caller waits on, not a long background scan.
            from stockanalysis.core import regime_client
            # _send_html takes bytes here (render_page already encodes).
            self._send_html(pages.render_regime(regime_client.run_regime()).encode())
            return

        if self.path == "/api/leaders/verdict":
            self._send_json(api.leaders_verdict(
                (form.get("ticker") or [""])[0],
                (form.get("direction") or [""])[0],
                (form.get("verdict") or [""])[0],
                (form.get("note") or [""])[0]))
            return

        if self.path == "/run":
            action = (form.get("action") or [""])[0]
            err = api.dispatch_run(action, form)
            self._send_json({"ok": not err, "message": err or "Started"})
            return

        # The screener posts JSON, not a form: a condition tree is nested,
        # and urlencoded form fields can't express that without inventing a
        # flattening scheme on both sides.
        if self.path in ("/api/screen", "/api/screener/save",
                         "/api/screener/delete"):
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                self._send_json({"ok": False, "message": "expected JSON body"})
                return
            try:
                if self.path == "/api/screen":
                    self._send_json(api.screen(payload))
                elif self.path == "/api/screener/save":
                    self._send_json(api.save_screen(payload))
                else:
                    self._send_json(api.delete_screen(
                        str(payload.get("name") or "")))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_json({"ok": False, "message": f"screen failed: {e}"})
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

        if self.path == "/api/etf/portfolio":
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                self._send_json({"ok": False, "message": "expected JSON body"})
                return
            try:
                self._send_json(api.etf_portfolio(payload))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_json({"ok": False, "message": f"analysis failed: {e}"})
            return

        if self.path == "/api/etf/theme":
            self._send_json(api.etf_set_theme(form))
            return

        if self.path == "/api/risk/save":
            self._send_json(api.save_risk_settings(form))
            return

        if self.path == "/api/schedule/save":
            self._send_json(api.schedule_save(form))
            return

        if self.path == "/api/schedule/settings":
            self._send_json(api.schedule_settings_save(form))
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

    def _do_login(self, form) -> None:
        username = (form.get("username") or [""])[0]
        password = (form.get("password") or [""])[0]
        next_path = self._safe_next((form.get("next") or ["/"])[0])

        if not AUTH_ENABLED:
            self._redirect(next_path)
            return

        token, error = auth.authenticate(username, password)
        if not token:
            # 200, not 401: this is the form being re-rendered with an error,
            # and the browser is showing it rather than consuming a status.
            self._send_html(render_login(error=error, username=username,
                                         next_path=next_path))
            return
        self._redirect(next_path, cookie=auth.cookie_header(token))

    # ── helpers ──────────────────────────────────────────────────────────────
    def _send_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        # 303: the login POST must become a GET of the destination, so a
        # refresh after signing in doesn't re-submit the credentials.
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):   # quieter default access logging
        pass


def _start_scheduler_thread() -> None:
    """Run the scheduling loop (pre-market/intraday/close scans, emails,
    nightly cleanup) on a daemon thread inside this process, so starting the
    web UI is enough to have jobs firing on schedule — no second terminal
    running scheduler.py needed.

    Turns on the scheduler's login gate when this server requires a login:
    with it on, jobs only fire while somebody is signed in, and pause on the
    last sign-out. Under --no-auth there are no sessions to count, so the
    gate stays off and jobs run as they always did. The gate itself can be
    turned off from Automation → Scheduler for an always-on deployment."""
    from stockanalysis.scheduling.scheduler import _start_scheduler, set_login_gate

    set_login_gate(AUTH_ENABLED)

    def _runner():
        try:
            _start_scheduler()
        except Exception:
            import traceback
            traceback.print_exc()

    threading.Thread(target=_runner, daemon=True, name="scheduler-loop").start()


def _start_spy_daemon_thread() -> None:
    """Run the SPY 0DTE signal daemon on its own daemon thread, same deal as
    the scan scheduler above: starting the web UI is enough to have Day Trade
    signals firing. It polls yfinance every 30s and only acts during market
    hours; it can never place an order, only write a pending_review proposal."""
    from spydaytrader.daemon.scheduler import run_forever

    def _runner():
        try:
            run_forever()
        except Exception:
            import traceback
            traceback.print_exc()

    threading.Thread(target=_runner, daemon=True, name="spy-signal-daemon").start()


def _set_password_interactive(username: str | None = None) -> None:
    """`--set-password`: create the first account or reset an existing one.

    Two ways in, because the interactive one cannot always be used — a
    password set from a GUI-launched terminal, a script, or a container has
    no prompt to answer:

      python app.py --set-password                 # prompts, hidden input
      WORKSTATION_PASSWORD='…' python app.py --set-password --user manju

    Interactive is the default and the better one: getpass keeps the password
    off the screen and out of shell history, and it reads twice because a typo
    in a hidden field is otherwise only discovered at the login screen. The
    environment variable is the escape hatch, and it is read from the
    environment rather than taken as a CLI argument on purpose — an argument
    would sit in `ps` output and in .zsh_history for anyone on the machine.

    This is the ONLY supported way to set a password. Writing one into
    users.json by hand cannot work: the file stores a PBKDF2 hash, and there
    is deliberately no code path that treats its contents as a plaintext
    password to compare against.
    """
    existing = sorted(auth.load_users())
    if existing:
        print(f"Existing users: {', '.join(existing)}")

    env_password = os.environ.get("WORKSTATION_PASSWORD")
    if env_password:
        username = username or os.environ.get("WORKSTATION_USER") or "admin"
        print(f"Using WORKSTATION_PASSWORD from the environment for "
              f"'{username}'.")
    else:
        username = username or input("Username [admin]: ").strip() or "admin"
        env_password = getpass.getpass("New password (min 8 chars): ")
        if env_password != getpass.getpass("Confirm password: "):
            print("Passwords don't match — nothing changed.")
            return

    try:
        auth.set_user_password(username, env_password)
    except ValueError as e:
        print(f"Nothing changed: {e}")
        return
    print(f"✓ Password set for '{username}'. Any open sessions were signed out.")


def _announce_auth() -> None:
    """Create the first account if there isn't one, and say so loudly.

    The generated password is printed exactly once, here, and only when it
    was just generated — it's not recoverable afterwards because only its
    PBKDF2 hash is stored. `--set-password` is the way back in."""
    created = auth.bootstrap_if_empty()
    if created:
        username, password = created
        print("\n" + "─" * 62)
        print("  First run — created a login for this workstation:")
        print(f"     username: {username}")
        print(f"     password: {password}")
        print("  Save it now; it is not stored in recoverable form.")
        print("  Change it any time with:  python app.py --set-password")
        print("─" * 62 + "\n")
        return

    users = auth.load_users()
    # Say it at startup, not only at the login screen: a store edited by hand
    # is discovered by trying to sign in and failing, and the sign-in failure
    # is several minutes and one wrong theory away from the console.
    broken = [name for name, rec in users.items()
              if not auth.looks_hashed(rec.get("password"))]
    if broken:
        print("\n" + "─" * 62)
        print(f"  ⚠️  Cannot sign in as: {', '.join(sorted(broken))}")
        print("  Their stored password is not a PBKDF2 hash — users.json")
        print("  holds hashes, so a password typed into it directly can")
        print("  never match. Set a real one with:")
        print("     python app.py --set-password")
        print("─" * 62 + "\n")
    print(f"Auth → enabled ({len(users)} user(s); --set-password to reset)")
    # Said out loud because getting it wrong looks like a broken password
    # rather than a misconfiguration: a Secure cookie is dropped by the
    # browser over plain HTTP, so /login just redirects back to /login.
    if auth.BEHIND_TLS:
        print("Cookies → Secure (WORKSTATION_BEHIND_TLS is set) — sign in "
              "through the HTTPS front door; http://localhost cannot")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading workstation web app")
    # PORT lets a supervisor (the preview harness, a container) assign a free
    # port without editing the launch config. Explicit --port still wins, so
    # `python app.py` on its own is unchanged: 8899.
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT") or DEFAULT_PORT))
    parser.add_argument("--no-scheduler", action="store_true",
                        help="Don't run the automatic scan scheduler alongside the UI")
    parser.add_argument("--no-spy-daemon", action="store_true",
                        help="Don't run the SPY day-trade signal daemon alongside the UI")
    parser.add_argument("--set-password", action="store_true",
                        help="Set or reset a user's password, then exit")
    parser.add_argument("--user", metavar="NAME",
                        help="Username for --set-password (skips the prompt)")
    parser.add_argument("--no-auth", action="store_true",
                        help="Serve without a login (localhost-only tool; "
                             "leaves every page and data file open to anything "
                             "that can reach the port)")
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.set_password:
        _set_password_interactive(args.user)
        return

    global AUTH_ENABLED
    AUTH_ENABLED = not args.no_auth
    if AUTH_ENABLED:
        _announce_auth()
    else:
        print("⚠️  Auth DISABLED (--no-auth) — anything that can reach this "
              "port can read your portfolio and place-order proposals")

    if not args.no_scheduler:
        _start_scheduler_thread()
        print("Scheduler loop → running in background (pass --no-scheduler to disable)")
        if AUTH_ENABLED:
            from stockanalysis.scheduling.schedule_config import load_settings
            if load_settings()["run_when_logged_out"]:
                print("   Automation runs whether or not anyone is signed in "
                      "(Automation → Scheduler to change)")
            else:
                print("   Automation is PAUSED until you sign in, and pauses "
                      "again on sign-out (Automation → Scheduler to change)")

    if not args.no_spy_daemon:
        _start_spy_daemon_thread()
        print("SPY signal daemon → running in background (pass --no-spy-daemon to disable)")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), WorkstationHandler)
    print(f"Trading Workstation → http://localhost:{args.port}  "
          f"(serving {OUTPUT_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
