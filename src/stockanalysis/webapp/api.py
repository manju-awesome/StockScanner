"""
api.py
======
Job functions and JSON-endpoint logic for the webapp. Kept separate from
app.py (HTTP plumbing) and views.py (HTML rendering) so each file has one
job: this one talks to the scan/research/portfolio pipeline and to
jobstore.Progress; it never touches HTTP request/response objects directly.
"""

from __future__ import annotations

from pathlib import Path

from . import jobstore

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR   = PROJECT_ROOT / "data" / "output"

BUILTIN_UNIVERSES = ("daytrade", "watchlist", "longterm", "dividend", "sp500")


def available_universes() -> list[str]:
    """Built-in universes plus every user-curated watchlist (data/watchlists.json)
    — lets the Scanner run against e.g. "AI: Networking" the same way it runs
    against "sp500", without hardcoding each watchlist name into this module."""
    from stockanalysis.reporting.research import load_watchlists
    return list(BUILTIN_UNIVERSES) + sorted(
        name for name in load_watchlists() if name not in BUILTIN_UNIVERSES)

# stage-string prefix -> pipeline step index, shared with the Scanner page's
# step visualization (views.py renders the same list)
SCAN_STEPS = ("fetch_qqq", "scan", "grade", "writing", "research", "done")


def scan_stage_index(stage: str) -> int:
    """Map a raw stage string (e.g. 'scan:NVDA') to an index into SCAN_STEPS."""
    prefix = stage.split(":", 1)[0]
    return SCAN_STEPS.index(prefix) if prefix in SCAN_STEPS else 0


# ─────────────────────────────────────────────────────────────────────────────
# JOB FUNCTIONS — each takes a jobstore.Progress and returns a result string
# ─────────────────────────────────────────────────────────────────────────────

def job_research(tickers: list[str], progress: jobstore.Progress) -> str:
    from stockanalysis.reporting.research import refresh_research
    written = refresh_research(
        tickers, OUTPUT_DIR, charts=True,
        progress_cb=lambda stage, done, total: progress.stage(stage, done, total))
    progress.stage("done", len(tickers), len(tickers))
    return f"{len(written)} page(s) refreshed: {', '.join(sorted(written))}"


def job_scan(universes: list[str], include_portfolio: bool,
            progress: jobstore.Progress,
            extra_tickers: list[str] | None = None) -> str:
    from stockanalysis.scanners import scan_universe as su
    from stockanalysis.reporting.dashboard import generate_dashboard
    from stockanalysis.reporting.research import load_watchlists
    builtin = {
        "daytrade": su.DAY_TRADE_TICKERS, "watchlist": su.WATCHLIST_TICKERS,
        "longterm": su.LONGTERM_TICKERS, "dividend": su.DIVIDEND_STOCKS,
        "sp500": su.SP500_TICKERS,
    }
    user_watchlists = load_watchlists()
    # Union across every selected universe (order-preserving, deduped) so
    # picking several overlapping categories scans each ticker once; ad-hoc
    # comma-separated tickers from the Scanner form append the same way.
    tickers, seen = [], set()
    for universe in universes:
        for t in builtin.get(universe) or user_watchlists.get(universe) or []:
            if t not in seen:
                seen.add(t)
                tickers.append(t)
    for t in extra_tickers or []:
        if t not in seen:
            seen.add(t)
            tickers.append(t)
    if not tickers:
        raise ValueError("nothing to scan — no universe selected and no tickers given")
    rows = su.main(
        tickers,
        progress_cb=lambda stage, done, total: progress.stage(stage, done, total))
    progress.stage("research", len(rows), len(rows))
    tags = universes[:3] + (["custom"] if extra_tickers else [])
    name_tag = ("-".join(tags) + (f"+{len(universes) - 3}" if len(universes) > 3 else "")
                ) or "custom"
    generate_dashboard(rows, output_dir=OUTPUT_DIR, open_browser=False,
                       include_portfolio=include_portfolio,
                       report_name=f"{name_tag}Report")
    # Keep the Research Library in lockstep with the scan the dashboard was
    # built from — otherwise its scores (Swing/Day/Call/Put) go stale until
    # the watchlist monitor happens to re-touch a ticker. charts/news are
    # skipped for the same reason as the watchlist job: already fetched
    # elsewhere, pure redundant network cost on a 500-ticker scan.
    from stockanalysis.reporting.research import generate_research_pages
    written = generate_research_pages(rows, OUTPUT_DIR, charts=False, fetch_news=False)
    progress.stage("done", len(rows), len(rows))
    scope = ", ".join(universes) if universes else "custom tickers"
    if universes and extra_tickers:
        scope += f" + {len(extra_tickers)} custom"
    return (f"scanned {len(rows)} tickers ({scope}); dashboard regenerated; "
            f"research library refreshed for {len(written)}"
            + ("" if include_portfolio else " (portfolio panel excluded)"))


def job_news(tickers: list[str] | None, progress: jobstore.Progress) -> str:
    from stockanalysis.reporting.research import update_news
    progress.stage("fetching news", 0, len(tickers) if tickers else 0)
    updated = update_news(tickers, OUTPUT_DIR)
    progress.stage("done", len(updated), len(updated))
    return f"latest news spliced into {len(updated)} research page(s)"


def job_ai_sentiment(progress: jobstore.Progress) -> str:
    import json
    from stockanalysis.scanners import ai_pulse
    from stockanalysis.scanners.market_movers import market_pulse
    from stockanalysis.core import ai_sentiment as aisent

    progress.stage("fetching AI tiers + macro inputs")
    pulse_extra = ai_pulse.fetch_ai_pulse()
    progress.stage("fetching market pulse (VIX, sectors, econ)")
    pulse = market_pulse()
    progress.stage("scoring")
    snapshot = aisent.build_snapshot(pulse_extra, pulse)
    (OUTPUT_DIR / "ai_sentiment.json").write_text(json.dumps(snapshot))
    progress.stage("done")
    s = snapshot["sentiment"]
    return f"AI sentiment {s['score']}/100 — {s['label']}"


def job_earnings_analysis(ticker: str, progress: jobstore.Progress) -> str:
    """Run the deterministic earnings-sentiment engine for one ticker and
    write the result to data/output/earnings/<TICKER>.json — served as a
    static file by app.py's fallback handler, so the Research Library's
    "Earnings Analysis" button just fetches it once this job's status flips
    to done instead of needing a dedicated GET route."""
    import json
    from stockanalysis.core.earnings_sentiment import analyze_earnings_sentiment
    ticker = ticker.upper().strip()
    progress.stage(f"analyzing {ticker}: earnings history, options, market context")
    result = analyze_earnings_sentiment(ticker)
    earnings_dir = OUTPUT_DIR / "earnings"
    earnings_dir.mkdir(parents=True, exist_ok=True)
    (earnings_dir / f"{ticker}.json").write_text(json.dumps(result))
    progress.stage("done")
    move = result.get("expected_move_pct")
    return (f"{ticker}: {result['trading_bias']} · bullish {result['bullish_probability']}%"
            + (f" · expected move ±{move:.1f}%" if move is not None else ""))


def job_ta_analysis(ticker: str, progress: jobstore.Progress) -> str:
    """AI multi-timeframe technical analysis (core.technical_analysis) for
    one ticker; writes data/output/ta/<TICKER>.json — fetched by the
    Research Library's "AI Technicals" modal once the job flips to done
    (same static-file pattern as the earnings analysis)."""
    import json
    from stockanalysis.core.technical_analysis import analyze_ticker
    ticker = ticker.upper().strip()
    progress.stage(f"{ticker}: fetching M/W/D/4H frames + computing indicators")
    result = analyze_ticker(ticker)
    ta_dir = OUTPUT_DIR / "ta"
    ta_dir.mkdir(parents=True, exist_ok=True)
    (ta_dir / f"{ticker}.json").write_text(json.dumps(result))
    progress.stage("done")
    if result.get("error"):
        raise ValueError(result["error"])
    return (f"{ticker}: {result['verdict']} · trend {result['current_trend']['overall']}"
            f" · probability {result['probability_score']}%")


def job_journal_review(trade_id: str, progress: jobstore.Progress) -> str:
    """Send one journal trade to the AI coach (core.trading_journal) and
    write its feedback back into data/journal_trades.json. Runs as a job
    (rather than inline in the save request) because it's a network call to
    Anthropic — same reasoning as job_earnings_analysis."""
    from stockanalysis.core import trading_journal as journal
    trade = journal.get_trade(trade_id)
    if not trade:
        raise ValueError(f"trade {trade_id} not found")
    progress.stage(f"reviewing {trade.get('ticker')} trade with AI coach")
    feedback = journal.run_ai_coach_review(trade)
    journal.update_trade(trade_id, {"ai_feedback": feedback})
    progress.stage("done")
    return f"{trade.get('ticker')}: grade {feedback.get('overall_grade')} · edge {feedback.get('edge_score')}/100"


def job_premarket_brief(progress: jobstore.Progress) -> str:
    """On-demand "Generate Now" for the Pre-Market Brief — the same
    function the 7:00 AM scheduler job calls, so a manual run and the
    scheduled one always agree (see core/premarket_brief.py)."""
    from stockanalysis.core.premarket_brief import send_premarket_brief
    progress.stage("gathering macro, movers, earnings, breakout context")
    brief = send_premarket_brief()
    progress.stage("done")
    return f"brief generated and emailed ({len(brief.get('earnings_today') or [])} earnings today)"


def job_watchlist_scan(progress: jobstore.Progress) -> str:
    """On-demand "Scan Now" for the Watchlist Alert monitor — the same
    condition scan the 10-minute background job runs during market hours.
    Also updates the Research Library (research_index.json + each
    ticker's research/<T>.html page) from the same freshly-fetched rows —
    scan_universe.main() already runs the full categorize/grade/score
    pipeline, identical to what a regular Scanner "+ New Scan" produces, so
    skipping this step (the original version of this job did) just meant
    the Research Library silently went stale after a watchlist scan.
    charts=False/fetch_news=False: chart bars and news are already fetched
    elsewhere (Research page charts, core.news_monitor's own headline
    scan) — regenerating them here on every 10-minute cycle would be
    redundant network cost for no new information."""
    from stockanalysis.core.watchlist_alerts import scan_rows_for_alerts, default_alert_tickers
    from stockanalysis.reporting.research import generate_research_pages
    from stockanalysis.scanners import scan_universe as su

    tickers = default_alert_tickers()
    if not tickers:
        raise ValueError('no tickers in the "watchlist" list (data/watchlists.json)')

    progress.stage(f"scanning {len(tickers)} watchlist ticker(s)", 0, len(tickers))
    rows = su.main(tickers, progress_cb=lambda stage, done, total: progress.stage(stage, done, total))
    new_alerts = scan_rows_for_alerts(rows)
    progress.stage("updating research library")
    written = generate_research_pages(rows, OUTPUT_DIR, charts=False, fetch_news=False)
    progress.stage("done")
    return (f"{len(new_alerts)} new alert(s) from {len(rows)} ticker(s); "
           f"research library refreshed for {len(written)}")


def job_news_scan(progress: jobstore.Progress) -> str:
    """On-demand "Scan News Now" for the Breaking News monitor."""
    from stockanalysis.core.news_monitor import scan_news_for_alerts
    from stockanalysis.core.watchlist_alerts import default_alert_tickers

    tickers = default_alert_tickers()
    if not tickers:
        raise ValueError('no tickers in the "watchlist" list (data/watchlists.json)')

    progress.stage(f"checking recent headlines for {len(tickers)} ticker(s)")
    new_alerts = scan_news_for_alerts(tickers)
    progress.stage("done")
    return f"{len(new_alerts)} new alert(s) from {len(tickers)} ticker(s)"


def job_earnings_scan(progress: jobstore.Progress) -> str:
    """On-demand "Check Earnings Now" for the Earnings Alert monitor."""
    from stockanalysis.core.earnings_alerts import scan_earnings_for_alerts
    from stockanalysis.core.watchlist_alerts import default_alert_tickers

    tickers = default_alert_tickers()
    if not tickers:
        raise ValueError('no tickers in the "watchlist" list (data/watchlists.json)')

    progress.stage(f"checking earnings dates for {len(tickers)} ticker(s)")
    new_alerts = scan_earnings_for_alerts(tickers)
    progress.stage("done")
    return f"{len(new_alerts)} new alert(s) from {len(tickers)} ticker(s)"


def job_cleanup(days: int, progress: jobstore.Progress) -> str:
    from stockanalysis.scheduling.scheduler import cleanup_outputs
    progress.stage("scanning output directory")
    n = cleanup_outputs(days, OUTPUT_DIR)
    progress.stage("done")
    return f"removed {n} file(s) older than {days}d"


def job_run_tests(progress: jobstore.Progress) -> str:
    """Run the pytest suite (tests/) as a subprocess and record the result.
    Full output is written to data/output/last_test_run.txt so a failure can
    be inspected from the dashboard without a terminal."""
    import os
    import subprocess
    import sys

    progress.stage("running pytest")
    tests_dir = PROJECT_ROOT / "tests"
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(tests_dir)],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            timeout=600, env=env,
        )
        output = proc.stdout + proc.stderr
        passed = proc.returncode == 0
    except FileNotFoundError:
        output = "pytest is not installed — run: pip3 install pytest"
        passed = False
    except subprocess.TimeoutExpired:
        output = "test run timed out after 10 minutes"
        passed = False

    (OUTPUT_DIR / "last_test_run.txt").write_text(output)
    summary = next((ln for ln in reversed(output.strip().splitlines())
                    if ln.strip()), "no output")
    progress.stage("done")
    return f"{'PASSED' if passed else 'FAILED'}: {summary}"


def job_trigger_cron(job_name: str, progress: jobstore.Progress) -> str:
    """Run one scheduled job's function immediately, out of band — for
    verifying a cron job actually does what it's supposed to without waiting
    for its real trigger time. Resolves through the SCHEDULED_JOBS registry
    first (works even for jobs currently disabled in the schedule config),
    falling back to whatever is registered with `schedule` (covers
    dynamically-added jobs like the VIX put loop). Calls the function
    directly rather than schedule.Job.run(), so it does NOT advance any
    next_run — the real schedule is untouched."""
    import schedule
    from stockanalysis.scheduling.scheduler import SCHEDULED_JOBS

    fn = next((f for f in SCHEDULED_JOBS.values() if f.__name__ == job_name), None)
    if fn is None:
        match = next((j for j in schedule.jobs if jobstore.job_name(j) == job_name), None)
        if match is None:
            raise ValueError(f"no scheduled job named {job_name!r} "
                              f"(is the scheduler running? see the Scheduler card)")
        fn = match.job_func

    progress.stage(f"running {job_name}")
    fn()
    progress.stage("done")
    return f"{job_name} executed on demand (its normal schedule is unaffected)"


def job_test_scheduler(progress: jobstore.Progress) -> str:
    """Exercise the scheduler end-to-end: registers a one-off job ~1 minute
    from now (via scheduler._start_scheduler_test) and blocks until it fires,
    so you can confirm cron wiring works without waiting for a real market
    session. Runs on a jobstore background thread so it doesn't block the UI."""
    from stockanalysis.scheduling.scheduler import _start_scheduler_test
    progress.stage("scheduling test job (fires in ~1 min)")
    _start_scheduler_test()
    progress.stage("done")
    return "self-test complete — check the app's console output for the fired job's log lines"


def dispatch_run(action: str, form: dict) -> str:
    """
    form: dict of lists (as parse_qs returns). Starts the matching job via
    jobstore.start(). Returns "" on success or an error string — never
    raises (bad input just becomes an error string shown to the user).
    """
    def first(key, default=""):
        return (form.get(key) or [default])[0]

    def watchlist_tickers(names: list[str]) -> list[str] | None:
        """Union of tickers across one or more data/watchlists.json
        categories (order-preserving, deduped), or None if `names` is empty
        — lets callers fall back to their own default."""
        if not names:
            return None
        from stockanalysis.reporting.research import load_watchlists
        lists = load_watchlists()
        tickers, seen = [], set()
        for name in names:
            for t in lists.get(name) or []:
                if t not in seen:
                    seen.add(t)
                    tickers.append(t)
        return tickers

    if action == "research":
        watchlists = form.get("watchlist") or []
        tickers = watchlist_tickers(watchlists)
        if tickers is None:
            raw = first("tickers")
            tickers = [t.strip().upper() for t in raw.replace(",", " ").split()
                       if t.strip()]
            if not tickers:
                from stockanalysis.scheduling.scheduler import DAY_TRADE_TICKERS
                tickers = list(DAY_TRADE_TICKERS)
        elif not tickers:
            return f"watchlist(s) {', '.join(watchlists)!r} empty or unknown"
        label = (f"research refresh: {'+'.join(watchlists)}" if watchlists else
                 "research refresh: " + ", ".join(tickers[:12])
                 + ("…" if len(tickers) > 12 else ""))
        return jobstore.start("research", label,
                              lambda p: job_research(tickers, p))

    if action == "scan":
        universes = form.get("universe") or []
        raw = first("tickers")
        extra = [t.strip().upper() for t in raw.replace(",", " ").split() if t.strip()]
        if not universes and not extra:
            universes = ["daytrade"]        # bare "Run Scan" keeps its old default
        unknown = [u for u in universes if u not in available_universes()]
        if unknown:
            return f"unknown universe(s): {', '.join(unknown)}"
        include_pf = "portfolio" in form
        parts = list(universes)
        if extra:
            parts.append(", ".join(extra[:8]) + ("…" if len(extra) > 8 else ""))
        label = f"scan: {' + '.join(parts)}" + ("" if include_pf else " (no portfolio panel)")
        return jobstore.start("scan", label,
                              lambda p: job_scan(universes, include_pf, p,
                                                 extra_tickers=extra))

    if action == "news":
        watchlists = form.get("watchlist") or []
        tickers = watchlist_tickers(watchlists)
        if tickers is None:
            raw = first("tickers")
            tickers = [t.strip().upper() for t in raw.replace(",", " ").split()
                       if t.strip()] or None
        elif not tickers:
            return f"watchlist(s) {', '.join(watchlists)!r} empty or unknown"
        label = (f"news scan: {'+'.join(watchlists)}" if watchlists else
                 "news scan: all research pages" if tickers is None
                 else f"news scan: {', '.join(tickers[:12])}"
                 + ("…" if len(tickers) > 12 else ""))
        return jobstore.start("news", label, lambda p: job_news(tickers, p))

    if action == "ai_sentiment":
        return jobstore.start("ai_sentiment", "AI sentiment refresh",
                              lambda p: job_ai_sentiment(p))

    if action == "earnings_analysis":
        ticker = first("ticker").strip().upper()
        if not ticker:
            return "no ticker given"
        return jobstore.start("earnings", f"earnings analysis: {ticker}",
                              lambda p: job_earnings_analysis(ticker, p))

    if action == "ta_analysis":
        ticker = first("ticker").upper().strip()
        if not ticker:
            return "no ticker given"
        return jobstore.start("ta", f"AI technicals: {ticker}",
                              lambda p: job_ta_analysis(ticker, p))

    if action == "journal_review":
        trade_id = first("trade_id")
        if not trade_id:
            return "no trade_id given"
        return jobstore.start("journal_review", f"AI coach review: {trade_id}",
                              lambda p: job_journal_review(trade_id, p))

    if action == "premarket_brief":
        return jobstore.start("premarket_brief", "Pre-Market Brief (manual)",
                              lambda p: job_premarket_brief(p))

    if action == "watchlist_scan":
        return jobstore.start("watchlist_scan", "Watchlist alert scan (manual)",
                              lambda p: job_watchlist_scan(p))

    if action == "news_scan":
        return jobstore.start("news_scan", "Breaking news scan (manual)",
                              lambda p: job_news_scan(p))

    if action == "earnings_scan":
        return jobstore.start("earnings_scan", "Earnings alert scan (manual)",
                              lambda p: job_earnings_scan(p))

    if action == "cleanup":
        try:
            days = max(0, int(first("days", "7")))
        except ValueError:
            days = 7
        return jobstore.start("cleanup", f"cleanup >{days}d",
                              lambda p: job_cleanup(days, p))

    if action == "test":
        return jobstore.start("test", "unit tests: tests/",
                              lambda p: job_run_tests(p))

    if action == "run_cron":
        name = first("job_name")
        if not name:
            return "no job_name given"
        return jobstore.start(f"cron:{name}", f"manual trigger: {name}",
                              lambda p: job_trigger_cron(name, p))

    if action == "test_scheduler":
        return jobstore.start("scheduler_test", "scheduler self-test (fires in ~1 min)",
                              lambda p: job_test_scheduler(p))

    return "unknown action"


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULE — edit one job's cadence/enabled flag from the Automation page
# ─────────────────────────────────────────────────────────────────────────────

def schedule_save(form: dict) -> dict:
    """form: dict of lists (as parse_qs returns), from one Automation-page
    schedule row. Persists to data/schedule_config.json and, if the
    scheduler loop is alive, re-registers the live schedule so the edit
    takes effect immediately. Returns {"ok": ..., "message": ...} — never
    raises (bad input becomes an error message shown as a toast)."""
    from stockanalysis.scheduling import schedule_config

    def first(key, default=""):
        return (form.get(key) or [default])[0]

    key = first("job_key")
    raw = {
        # checkbox: present ("on") when checked, absent entirely when not
        "enabled": "enabled" in form,
        "type": first("type", "daily"),
        "times": first("times"),
        "minutes": first("minutes", "10"),
    }
    try:
        spec = schedule_config.save_job(key, raw)
    except ValueError as e:
        return {"ok": False, "message": str(e)}

    applied = ""
    if jobstore.scheduler_alive():
        from stockanalysis.scheduling.scheduler import reschedule
        reschedule()
        applied = " — applied to the running scheduler"
    else:
        applied = " — takes effect when the scheduler starts"

    label = schedule_config.JOB_DEFS[key]["label"]
    state = schedule_config.describe_spec(spec) if spec["enabled"] else "disabled"
    return {"ok": True, "message": f"{label}: {state}{applied}"}


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH — ticker autocomplete over the research index + watchlists + portfolio
# ─────────────────────────────────────────────────────────────────────────────

def search_tickers(query: str, limit: int = 10) -> list[dict]:
    from stockanalysis.reporting.research import load_research_index
    q = query.strip().upper()
    index = load_research_index(OUTPUT_DIR)
    if not q:
        items = sorted(index.values(), key=lambda r: r.get("ticker") or "")
        return items[:limit]
    starts = [r for t, r in index.items() if t.startswith(q)]
    contains = [r for t, r in index.items() if q in t and not t.startswith(q)]
    sector_hits = [r for r in index.values()
                   if q in str(r.get("sector") or "").upper()
                   and r not in starts and r not in contains]
    return (starts + contains + sector_hits)[:limit]


def watchlist_toggle(name: str, ticker: str) -> dict:
    from stockanalysis.reporting.research import toggle_watchlist
    return toggle_watchlist(name, ticker)


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO — add/edit/delete rows in data/portfolio.csv from the Portfolio page
# ─────────────────────────────────────────────────────────────────────────────

def portfolio_save(form: dict) -> dict:
    """form: dict of lists (as parse_qs returns), from the Add/Edit Position
    modal. Returns {"ok": True, "message": ...} or {"ok": False, "message":
    ...} — never raises (a blank ticker just becomes an error string)."""
    from stockanalysis.reporting.portfolio import upsert_position

    def first(key, default=""):
        return (form.get(key) or [default])[0]

    fields = {k: first(k) for k in
             ("Ticker", "Shares", "Avg_Cost", "Entry_Date", "Strategy", "Stop", "Target", "Notes")}
    try:
        upsert_position(fields, original_ticker=first("Original_Ticker"))
    except ValueError as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": f"{fields['Ticker'].strip().upper()} saved"}


def portfolio_delete(ticker: str) -> dict:
    from stockanalysis.reporting.portfolio import delete_position
    ticker = (ticker or "").strip()
    if not ticker:
        return {"ok": False, "message": "no ticker given"}
    delete_position(ticker)
    return {"ok": True, "message": f"{ticker.upper()} removed"}


# ─────────────────────────────────────────────────────────────────────────────
# JOURNAL — save/delete are immediate (no network call); the AI coach review
# itself runs as a background job (see job_journal_review above)
# ─────────────────────────────────────────────────────────────────────────────

def journal_save(form: dict) -> dict:
    """Handles both "+ Log Trade" (no trade_id) and "Edit" (trade_id set) —
    a trade logged with only partial detail (e.g. just the plan, before it
    closes) can be reopened and completed later without creating a
    duplicate row."""
    from stockanalysis.core import trading_journal as journal
    ticker = (form.get("ticker") or [""])[0].strip()
    if not ticker:
        return {"ok": False, "message": "ticker is required"}
    trade_id = (form.get("trade_id") or [""])[0].strip()
    if trade_id:
        trade = journal.update_trade_from_form(trade_id, form)
        if not trade:
            return {"ok": False, "message": f"trade {trade_id} not found"}
        return {"ok": True, "message": f"{trade['ticker']} trade updated", "id": trade["id"]}
    trade = journal.add_trade(form)
    return {"ok": True, "message": f"{trade['ticker']} trade logged", "id": trade["id"]}


def journal_delete(trade_id: str) -> dict:
    from stockanalysis.core import trading_journal as journal
    trade_id = (trade_id or "").strip()
    if not trade_id:
        return {"ok": False, "message": "no trade_id given"}
    ok = journal.delete_trade(trade_id)
    return {"ok": ok, "message": "trade removed" if ok else "trade not found"}
