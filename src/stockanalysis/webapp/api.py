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
from urllib.parse import quote_plus

from . import jobstore

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR   = PROJECT_ROOT / "data" / "output"

BUILTIN_UNIVERSES = ("daytrade", "watchlist", "longterm", "dividend", "sp500")

# Option value meaning "every list in watchlists.json" — lets a single-select
# universe picker still express the sweep-everything default.
ALL_UNIVERSES_SENTINEL = "__all__"


# How many names a watchlist-driven CSP scan will cover. Deliberately
# above the typed box's 60: a list is a curated set the user chose, and a
# prefix of it answers a question nobody asked. One option chain per name.
MAX_LIST_SCAN = 200


def expand_all(names: list[str]) -> list[str]:
    """ALL_UNIVERSES_SENTINEL -> every non-empty list in watchlists.json, so
    one picker entry can mean "everything I track" without the user
    Ctrl-clicking 30 options. Order-preserving and deduped; anything already
    picked alongside ALL is kept, not duplicated. A no-op when the sentinel
    isn't present."""
    if ALL_UNIVERSES_SENTINEL not in names:
        return list(names)
    from stockanalysis.reporting.research import load_watchlists
    rest = [n for n in names if n != ALL_UNIVERSES_SENTINEL]
    every = [n for n, t in load_watchlists().items() if t]
    return list(dict.fromkeys(every + rest))


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


def job_stockdaytrade(limit: int, at_time, min_grade: str, risk_pct: float | None,
                      profile: str, progress: jobstore.Progress,
                      tickers: list[str] | None = None,
                      universe: str = "") -> str:
    """Run the stock day-trade scan and replace the page's snapshot.

    Writes the snapshot via core.daytrade.store — the same file and the
    same serialiser the CLI's --save uses, so a scan run from a terminal
    and one run from the page leave the page in identical states.

    `min_grade` filters what is stored, not what is scored — a name is
    ranked against the full candidate set and then hidden, so the counts in
    the header stay honest about how selective the run was.
    """
    from stockanalysis.core.daytrade import plan as dt_plan
    from stockanalysis.core.daytrade import scan as dt_scan
    from stockanalysis.core.daytrade import store as dt_store

    settings = dt_plan.load_settings({"risk_pct": risk_pct})
    result = dt_scan.run(limit=limit, at_time=at_time, settings=settings,
                         profile=profile, tickers=tickers,
                         progress_cb=lambda m: progress.stage(m))

    scored = len(result["rows"])
    if min_grade != "ALL":
        order = ["A+", "A", "B+", "B", "C"]
        keep = set(order[:order.index(min_grade) + 1]) if min_grade in order else None
        if keep is not None:
            hidden = [r for r in result["rows"] if r.get("grade") not in keep]
            if hidden:
                result["notes"].append(
                    f"{len(hidden)} of {scored} scored rows below grade "
                    f"{min_grade} were not stored")
            result["rows"] = [r for r in result["rows"] if r.get("grade") in keep]

    # The scan knows the ticker list but not which watchlist it came from,
    # so the name is attached here for the page's Universe control.
    result["universe"] = universe or ""
    progress.stage("saving snapshot")
    dt_store.save(result)
    actionable = sum(1 for r in result["rows"] if r.get("grade") in ("A+", "A"))
    progress.stage("done")
    return (f"{len(result['rows'])} candidates stored from {scored} scored · "
            f"{actionable} actionable · session {result.get('asof')}")


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


def job_institutional(tickers: list[str] | None,
                     progress: jobstore.Progress) -> str:
    """Rebuild the 13F institutional-options snapshot.

    A job rather than an inline render because the first run of a quarter
    downloads a ~95 MB dataset from the SEC and then streams a ~400 MB
    information table — minutes, not milliseconds.

    Worth knowing before scheduling this: unlike every other job here, it is
    idempotent for months at a stretch. The filings it reads describe a
    quarter that has already closed and will not change, so re-running it
    daily returns identical numbers and burns the download for nothing. It
    only produces something new when the SEC publishes the next quarterly
    zip, which is roughly six weeks after each filing deadline.
    """
    from stockanalysis.core.institutional import engine as IE
    from stockanalysis.reporting.research import load_watchlists

    if not tickers:
        tickers = sorted({t for v in (load_watchlists() or {}).values()
                          for t in v})
    if not tickers:
        return "no tickers to look up — every watchlist is empty"

    payload = IE.build_snapshot(tickers, progress=progress)
    rows = payload.get("tickers") or []
    resolved = [r for r in rows if not r.get("error")]
    unresolved = len(rows) - len(resolved)
    with_opts = sum(1 for r in resolved
                    if (r.get("calls") or {}).get("holders")
                    or (r.get("puts") or {}).get("holders"))
    return (f'period {payload.get("period")} · {len(resolved):,} of '
            f'{len(rows):,} tickers resolved ({unresolved} not), '
            f'{with_opts:,} with reported option holders')


def job_sector_leaders(progress: jobstore.Progress) -> str:
    """Full market → sector → industry → stock scan, written to the snapshot.

    A job rather than an inline render because it downloads two years of daily
    bars for ~570 constituents plus twenty benchmarks and proxy ETFs, then
    five-minute bars for the finalists — three to five minutes, far past what
    a page render can block on. The /leaders page shows the last snapshot and
    this replaces it.
    """
    from stockanalysis.scanners.scan_sector_leaders import scan_and_store

    res = scan_and_store(
        progress_cb=lambda stage, done=None, total=None: progress.stage(
            stage, done, total))
    market = res.get("market") or {}
    sectors = res.get("sectors") or []
    bull = sum(1 for x in sectors if x.get("direction") == "bullish")
    bear = sum(1 for x in sectors if x.get("direction") == "bearish")
    return (f'{market.get("label")} ({market.get("score"):+.1f}) · '
            f'{len(sectors)} groups — {bull} bullish, {bear} bearish · '
            f'{len(res.get("candidates") or [])} candidate scores')


def job_trend_regime(progress: jobstore.Progress) -> str:
    """Classify the research library plus every watchlist into trend regimes.

    A job rather than an inline render because it downloads two years of
    daily bars for ~670 names. Batched at 120 a call that is a minute or
    two — far past a request timeout — so the /trend page shows the last
    snapshot and this replaces it, the same shape /leaders uses.
    """
    from stockanalysis.core.trend import engine as trend_engine

    res = trend_engine.scan_and_store(
        progress_cb=lambda stage, done=None, total=None: progress.stage(
            stage, done, total))
    counts = res.get("counts") or {}
    return (f'{res["classified"]}/{res["universe"]} classified · '
            f'{counts.get("A", 0)} leaders · {counts.get("C", 0)} dip '
            f'candidates · {counts.get("E", 0)} breakdowns')


def job_sector_leaders_confirm(tickers: list[str] | None,
                               progress: jobstore.Progress) -> str:
    """News, fundamentals, cash flow and the next earnings date for the
    finalists.

    Separate from the scan because it goes stale on a different clock —
    headlines age in hours, 20-day relative strength does not — and because
    it is per-ticker .info and .news calls, which are slow and rate-limited.
    With no tickers given it confirms the top names on BOTH sides: the short
    list is exactly where a contradicting headline changes the answer most.
    """
    from stockanalysis.core import leaders_store
    from stockanalysis.scanners.confirm_leaders import confirm_and_store

    snap = leaders_store.load()
    if not snap:
        return "no scan snapshot yet — run the sector-leader scan first"

    if not tickers:
        picks = []
        for direction in ("long", "short"):
            best = {}
            for c in snap.get("candidates") or []:
                if c.get("direction") != direction:
                    continue
                t = c.get("ticker")
                score = (c.get("confluence") or {}).get("score", 0)
                if t not in best or score > best[t]:
                    best[t] = score
            picks += [t for t, _ in sorted(best.items(), key=lambda kv: -kv[1])[:12]]
        tickers = list(dict.fromkeys(picks))

    out = confirm_and_store(
        tickers,
        progress_cb=lambda stage, done=None, total=None: progress.stage(
            stage, done, total))
    dated = sum(1 for v in out.values()
                if (v.get("earnings") or {}).get("next_earnings"))
    heads = sum(len(v.get("headlines") or []) for v in out.values())
    return (f"confirmed {len(out)} names — {heads} headlines, "
            f"{dated} with a scheduled earnings date")


def job_portfolio_risk(progress: jobstore.Progress) -> str:
    """Institutional-style risk report over portfolio.csv + options_positions.csv.

    A job rather than an inline request because it makes one batched history
    download plus a .info call per holding — roughly a minute for a 25-name
    book, which is well past what a page render should block on. The result
    is cached to data/output/portfolio_risk.json by the analyzer itself, so
    the Portfolio page renders the last run immediately and this job only has
    to refresh it.
    """
    from stockanalysis.core.portfolio_risk_scores import analyze_portfolio
    report = analyze_portfolio(
        progress_cb=lambda stage, done=None, total=None: progress.stage(stage, done, total))
    health, risk = report["health"], report["risk"]
    violations = report["violations"]
    critical = sum(1 for v in violations if v["severity"] == "critical")
    return (f'health {health["light"]} {health["score"]:.0f}/100 ({health["band"]}) · '
            f'risk {risk["score"]:.0f}/100 {risk["band"]} · '
            f"{len(violations)} limit breaches ({critical} critical)")


def _csp_etf_pass(wanted, risk_free, target_dte, progress=None):
    """(rows, shared holdings) for the funds in the research library.

    The universe is the library's own ETF classification, never a hardcoded
    list — a fund added to the research pages appears here on the next scan
    with nothing to edit, which is the same rule /longterm follows for
    equities.

    A NAMED scan re-prices only the funds it names and keeps the rest of
    the stored section, matching how `csp_store.save(merge=...)` treats
    equity rows: asking about SPY must not delete the other twenty-one.
    """
    from stockanalysis.core import etf_profile as EP
    from stockanalysis.core.csp import etf as ETF
    from stockanalysis.core.csp import store as csp_store

    entries = _longterm_entries()
    profiles = EP.load_profiles(OUTPUT_DIR)
    raw_by = {r.get("Ticker"): r for r in _longterm_universe()}

    # A fund the scan has classified, OR one carrying a stored profile —
    # the second half matters because three funds in the current library
    # (MDY, QQQM, VOO) have a profile and no scan row, and dropping them
    # would silently shorten the section by an eighth.
    tickers = {str(e.get("ticker") or "").upper()
               for e in entries if EP.is_etf_row(e)}
    tickers |= {str(t).upper() for t in profiles
                if not str(t).startswith("^")}
    tickers = sorted(t for t in tickers if t)

    keep, stored_overlap = [], []
    if wanted:
        asked = {str(t).upper() for t in wanted}
        snap = csp_store.load() or {}
        keep = [r for r in (snap.get("etfs") or [])
                if str(r.get("ticker") or "").upper() not in asked]
        stored_overlap = snap.get("etf_overlap") or []
        tickers = [t for t in tickers if t in asked]
    # A named scan of equities only — nothing to re-price, so the stored
    # section is handed back untouched rather than blanked.
    if not tickers:
        return keep, stored_overlap

    def tick(i, ticker):
        if progress:
            progress.stage(f"pricing fund chains — {ticker}", i, len(tickers))

    # Same live-quote rule as the equity pass. A fund's scan row and its
    # stored profile are both as old as the last refresh that touched them,
    # and every strike here is chosen relative to spot — SPY's two stored
    # prices already disagreed by a point.
    from stockanalysis.core.csp import engine as CE
    quotes = CE.live_spots(tickers)

    funds = [(t, raw_by.get(t) or {}, profiles.get(t)) for t in tickers]
    fresh = ETF.evaluate_universe(funds, risk_free=risk_free,
                                  target_dte=target_dte, progress=tick,
                                  spots=quotes)
    rows = keep + fresh
    return rows, ETF.overlap(rows)


def job_csp_scan(min_dte: int, max_dte: int, allow_earnings: bool,
                 limit: int, progress: jobstore.Progress,
                 tickers: list[str] | None = None,
                 earnings_policy: str = "AVOID", target_dte: int = 35,
                 min_stock: int = 0, min_csp: int = 0,
                 reference_rejected: bool = False,
                 reference_budget: int = 0) -> str:
    """Cash-Secured Put scan over the long-term universe.

    A job, not an inline render: the long-term evaluation is fast (it
    reads the research library), but every company that survives the
    eligibility gate then costs one option-chain round trip per expiry.
    Twenty survivors across three expiries is a minute or more.

    `limit` caps how many survivors get chain work. Rejected companies
    are still evaluated and stored — the page shows what was rejected
    and why, which is most of the value on a day when nothing qualifies.
    """
    from stockanalysis.core import csp
    from stockanalysis.core.csp import store as csp_store

    progress.stage("evaluating companies")
    data = longterm()
    rows_in = data["rows"]
    regime = data["regime"]

    # A named scan answers "what about THIS ticker", so it narrows the
    # universe, keeps the full audit even for rejections, and merges into
    # the stored snapshot instead of replacing it — scanning three names
    # must not delete the other four hundred and ninety.
    wanted = [str(t).upper() for t in (tickers or [])]
    if wanted:
        keep = set(wanted)
        rows_in = [r for r in rows_in if str(r.get("ticker") or "").upper()
                   in keep]

    from stockanalysis.core.csp import eligibility as EL
    survivors = sum(1 for r in rows_in
                    if EL.classify(r)["status"] != "CSP REJECTED")
    if wanted:
        missing = [t for t in wanted
                   if t not in {str(r.get("ticker") or "").upper()
                                for r in rows_in}]
        if missing:
            progress.stage("not in the research library: "
                           + ", ".join(missing))
    progress.stage(f"{survivors} companies eligible — pulling chains", 0,
                   min(survivors, limit))

    done = {"n": 0}

    def tick(_):
        done["n"] += 1
        progress.stage("pulling option chains", done["n"],
                       min(survivors, limit))

    risk_free, _ = longterm_risk_free()
    rows = csp.evaluate_universe(rows_in, regime=regime, risk_free=risk_free,
                                 min_dte=min_dte, max_dte=max_dte,
                                 allow_earnings=allow_earnings, limit=limit,
                                 earnings_policy=earnings_policy,
                                 dte_prefs={"target": target_dte},
                                 raw_rows={r.get("Ticker"): r
                                           for r in _longterm_universe()},
                                 # A named scan is a question about those
                                 # tickers, so a name with no qualifying
                                 # contract still gets its chain attached.
                                 reference_chain=bool(wanted),
                                 # And on a bulk scan, the highest-quality
                                 # rejections can be priced too — see
                                 # engine.MAX_REFERENCE_CHAINS. The verdict
                                 # stays REJECT; only the numbers appear.
                                 reference_rejected=reference_rejected,
                                 reference_budget=reference_budget)

    # Score floors are applied AFTER ranking, not as a scan filter. The
    # point is to refuse to fill N slots with mediocre trades rather than
    # to hide names — everything stays in the snapshot with its verdict,
    # and only the Opportunities ranking honours the floor.
    if min_stock or min_csp:
        for r in rows:
            sc, cs = r.get("stock_score"), r.get("csp_score")
            below = ((min_stock and (sc is None or sc < min_stock))
                     or (min_csp and (cs is None or cs < min_csp)))
            if below and (r.get("final") or {}).get("key") in (
                    "SELL", "SELL_DIP", "VERIFY"):
                r["final"] = {
                    "action": "🟠 WATCH", "key": "WATCH",
                    "why": (f"below the score floor — stock {sc}, CSP {cs} "
                            f"against {min_stock}/{min_csp} required")}
                r["below_floor"] = True

    # ── the fund section ────────────────────────────────────────────────
    # Runs after the equity pass, not instead of it. ETFs never reach the
    # eligibility gate above — a fund has no income statement, so it is
    # REJECTED for "no quality score", which is a category error reported
    # honestly rather than a gap. core.csp.etf answers the narrower
    # question that IS answerable for a fund; see its docstring.
    etf_rows, etf_overlap = [], []
    try:
        etf_rows, etf_overlap = _csp_etf_pass(wanted, risk_free, target_dte,
                                              progress)
    except Exception as e:                    # never lose the equity scan
        print(f"[CSP] ETF pass failed ({e})")

    settings = load_risk_settings()
    portfolio = csp.portfolio_view(rows, settings.get("capital"))

    csp_store.save(rows, {
        "etfs": etf_rows,
        "etf_overlap": etf_overlap,
        "regime": regime,
        "regime_note": data.get("regime_note"),
        "risk_free_note": data.get("risk_free_note"),
        "min_dte": min_dte, "max_dte": max_dte,
        "target_dte": target_dte,
        "allow_earnings": allow_earnings,
        "earnings_policy": earnings_policy,
        "min_stock": min_stock, "min_csp": min_csp,
        "reference_rejected": reference_rejected,
        "reference_budget": reference_budget,
        "universe": len(rows_in), "eligible": survivors,
        "portfolio": portfolio,
        "settings": settings,
    }, merge=bool(wanted), keep_full=wanted)

    counts = {}
    for r in rows:
        k = (r.get("final") or {}).get("key") or "?"
        counts[k] = counts.get(k, 0) + 1
    return (f"{counts.get('SELL', 0)} sell · "
            f"{counts.get('SELL_DIP', 0)} sell-on-dip · "
            f"{counts.get('EVENT_RISK', 0)} event-risk · "
            f"{counts.get('WAIT_IV', 0)} wait-IV · "
            f"{counts.get('WAIT_LEVEL', 0)} wait-level · "
            f"{counts.get('WATCH', 0)} watch · "
            f"{counts.get('REJECT', 0)} reject "
            f"(regime {regime}, {survivors}/{len(rows_in)} eligible, "
            f"earnings {earnings_policy})")


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


def job_longterm_entry_alerts(progress: jobstore.Progress) -> str:
    """On-demand "Check Entry Levels Now" — the same scan the market-hours
    background job runs, without its clock guard so it can be exercised at
    any time."""
    from stockanalysis.core.longterm import entry_alerts as EA

    progress.stage("evaluating the long-term engine")
    rows = longterm()["rows"]
    watching = EA.candidates(rows)
    progress.stage(f"fetching live prices for {len(watching)}", 0, len(watching))
    prices = EA.live_prices(watching)
    progress.stage("checking entry proximity", len(prices), len(watching))
    new_alerts = EA.scan_for_alerts(rows, prices=prices)
    progress.stage("done")
    if not watching:
        return "no resting entries to watch — nothing has both a level and a size"
    return (f"{len(new_alerts)} new entry alert(s) from {len(prices)} live "
            f"quote(s) across {len(watching)} resting order(s)"
            + (" — emailed 'Longterm swing trades'" if new_alerts else ""))


def job_52_week(universes: list[str], near_high: float, near_low: float,
                progress: jobstore.Progress) -> str:
    """Refresh the 52_week_high / 52_week_low watchlists from a source
    universe. Deliberately does NOT run the scan pipeline itself: it only
    rebuilds the two lists, which then appear in the Scanner's universe
    panel like any other watchlist. Ticking 52_week_high and running a scan
    puts those names through get_metrics(), which already calls
    compute_put_candidate() — so Put_Score/Put_Candidate/Put_Reason land in
    the scan CSV for exactly the fresh-high names this screen found."""
    from stockanalysis.core.fifty_two_week import scan_52_week

    res = scan_52_week(
        universes=universes, near_high_pct=near_high, near_low_pct=near_low,
        progress_cb=lambda stage, done, total: progress.stage(stage, done, total))
    progress.stage("done")
    new_hi = sum(1 for r in res["high_rows"] if r["New_High"])
    new_lo = sum(1 for r in res["low_rows"] if r["New_Low"])
    skipped = len(res["skipped"])
    return (f"{len(res['high'])} at/near 52W high ({new_hi} new today), "
            f"{len(res['low'])} at/near 52W low ({new_lo} new today) "
            f"from {res['scanned']} scanned"
            + (f", {skipped} skipped (no data / <200 bars)" if skipped else "")
            + f" — as-of {res['asof']}. Lists 52_week_high / 52_week_low "
              f"updated; tick one in Run a Scan to grade it.")


def job_earnings_today(universes: list[str] | None, days_ahead: int,
                       progress: jobstore.Progress) -> str:
    """Rebuild the earnings_today watchlist from every list in
    watchlists.json. Like job_52_week this only builds the list — ticking it
    in Run a Scan is what puts those names through the normal pipeline."""
    from stockanalysis.core.earnings_today import scan_earnings_today

    res = scan_earnings_today(
        universes=universes, days_ahead=days_ahead,
        progress_cb=lambda stage, done, total: progress.stage(stage, done, total))
    progress.stage("done")
    n_today = sum(1 for r in res["rows"] if r["Is_Today"])
    window = ("today" if not res["days_ahead"]
              else f"today +{res['days_ahead']}d ({n_today} today)")
    no_date = len(res["no_date"])
    scope = ("all watchlists" if universes is None else " + ".join(universes))
    return (f"{len(res['tickers'])} ticker(s) reporting {window} "
            f"from {res['scanned']} scanned in {scope}"
            + (f", {no_date} with no earnings date on file (ETFs, etc.)"
               if no_date else "")
            + f" — as-of {res['today']}. List earnings_today updated; "
              f"tick it in Run a Scan to grade them.")


def job_etf_profiles(progress: jobstore.Progress) -> str:
    """Refresh theme / holdings / expense ratio / AUM for every ETF in the
    library. Separate from the scan because none of it comes from the equity
    pipeline — and because it's cheap enough (~0.5s a fund) to run on its own
    whenever a new fund is added."""
    from stockanalysis.core import etf_profile
    from stockanalysis.reporting.research import load_research_index
    index = load_research_index(OUTPUT_DIR)
    tickers = etf_profile.etf_tickers(index.values())
    if not tickers:
        return "no ETFs in the library — add funds and run a scan first"
    result = etf_profile.refresh_profiles(
        tickers, OUTPUT_DIR,
        progress_cb=lambda stage, done, total: progress.stage(stage, done, total))
    msg = f"{result['ok']} of {result['requested']} fund(s) updated"
    if result["failed"]:
        msg += f", {result['failed']} failed: " + "; ".join(result["errors"][:3])
    return msg


def etf_set_theme(form: dict) -> dict:
    """Rename a fund's theme from the ETF views. form: parse_qs-style dict."""
    from stockanalysis.core import etf_profile
    ticker = (form.get("ticker") or [""])[0]
    theme = (form.get("theme") or [""])[0]
    try:
        return etf_profile.set_theme(OUTPUT_DIR, ticker, theme)
    except Exception as e:
        return {"ok": False, "message": f"could not save theme: {e}"}


ETF_ALLOCATIONS_PATH = PROJECT_ROOT / "data" / "etf_allocations.json"


def load_etf_allocations() -> dict:
    import json
    if not ETF_ALLOCATIONS_PATH.exists():
        return {}
    try:
        data = json.loads(ETF_ALLOCATIONS_PATH.read_text())
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def etf_portfolio(payload: dict) -> dict:
    """Look-through analysis for a set of ETF weights, and persist them.

    Saved so the panel comes back with your book still entered — the same
    atomic write the other user-state files use, since two servers on
    different ports have clobbered a plain write here before.
    """
    import json
    from stockanalysis.core import etf_portfolio as PF
    from stockanalysis.core import etf_profile

    allocations = payload.get("allocations") or {}
    if payload.get("save", True):
        try:
            ETF_ALLOCATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = ETF_ALLOCATIONS_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(allocations, indent=2))
            tmp.replace(ETF_ALLOCATIONS_PATH)
        except OSError as e:
            print(f"[ETF] could not save allocations ({e})")

    result = PF.analyze(allocations, etf_profile.load_profiles(OUTPUT_DIR))
    return result


def library_tickers() -> list[str]:
    """Every ticker with a research page — the index is the library, so this
    is what "all tickers in the research library" means."""
    from stockanalysis.reporting.research import load_research_index
    return sorted(load_research_index(OUTPUT_DIR))


def job_research_session(session: str, progress: jobstore.Progress) -> str:
    """Refresh every research page in the library, labelled by session.

    Pre- and post-market runs are the same pipeline on purpose: the scan
    reads whatever quotes are live when it runs, and that is precisely what
    makes one a pre-market view and the other a post-close view. Nothing is
    faked about the session — the label records when it ran, and each page's
    updated_at carries the timestamp.
    """
    from datetime import datetime
    from stockanalysis.reporting.research import refresh_research

    tickers = library_tickers()
    if not tickers:
        raise ValueError("no research pages yet — run a scan first")
    progress.stage(f"{session}: refreshing {len(tickers)} research page(s)",
                   0, len(tickers))
    written = refresh_research(
        tickers, output_dir=OUTPUT_DIR, charts=False, fetch_news=False,
        progress_cb=lambda stage, done, total: progress.stage(stage, done, total))
    progress.stage("done")
    return (f"{session} scan: refreshed {len(written)} of {len(tickers)} "
            f"research page(s) at {datetime.now():%H:%M}")


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
        for name in expand_all(names):
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
        # The ALL sentinel is a wire value, not a name anyone recognises —
        # spelling it into the job tray as "__all__" tells the user nothing
        # about what is being refreshed.
        scope = "+".join("all watchlists" if n == ALL_UNIVERSES_SENTINEL else n
                         for n in watchlists)
        label = (f"research refresh: {scope} ({len(tickers)} tickers)"
                 if watchlists else
                 "research refresh: " + ", ".join(tickers[:12])
                 + ("…" if len(tickers) > 12 else ""))
        return jobstore.start("research", label,
                              lambda p: job_research(tickers, p))

    if action == "institutional":
        raw = first("tickers")
        tickers = [t.strip().upper() for t in raw.replace(",", " ").split()
                   if t.strip()]
        label = ("13F institutional options: " + ", ".join(tickers[:8])
                 + ("…" if len(tickers) > 8 else "") if tickers
                 else "13F institutional options: all watchlists")
        return jobstore.start("institutional", label,
                              lambda p: job_institutional(tickers, p))

    if action == "sector_leaders":
        return jobstore.start("scan", "sector-leader scan (market → sector → stock)",
                              job_sector_leaders)

    if action == "trend_regime":
        return jobstore.start("scan", "trend regime scan (library + watchlists)",
                              job_trend_regime)

    if action == "sector_leaders_confirm":
        raw = first("tickers")
        tickers = [t.strip().upper() for t in raw.replace(",", " ").split()
                   if t.strip()]
        label = ("confirming " + ", ".join(tickers[:8]) if tickers
                 else "confirming the top sector-leader names")
        return jobstore.start("news", label,
                              lambda p: job_sector_leaders_confirm(tickers, p))

    if action == "scan":
        universes = expand_all(form.get("universe") or [])
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

    if action == "scan_52_week":
        universes = form.get("universe_52w") or []
        if not universes:
            from stockanalysis.core.fifty_two_week import DEFAULT_SOURCE
            universes = list(DEFAULT_SOURCE)
        unknown = [u for u in universes if u not in available_universes()]
        if unknown:
            return f"unknown universe(s): {', '.join(unknown)}"

        def _pct(key: str, default: float) -> float | None:
            """Blank -> default; anything unparseable or negative -> None so
            the caller can turn it into a user-facing error."""
            raw = first(key, "").strip()
            if not raw:
                return default
            try:
                val = float(raw)
            except ValueError:
                return None
            return val if val >= 0 else None

        near_high = _pct("near_high_pct", 2.0)
        near_low = _pct("near_low_pct", 2.0)
        if near_high is None or near_low is None:
            return "thresholds must be non-negative numbers (e.g. 2 for 2%)"
        return jobstore.start(
            "scan_52_week", f"52-week high/low screen: {' + '.join(universes)}",
            lambda p: job_52_week(universes, near_high, near_low, p))

    if action == "research_session":
        session = first("session", "premarket").strip().lower()
        if session not in ("premarket", "postmarket"):
            return "session must be 'premarket' or 'postmarket'"
        # one jobstore kind for both, so the two can't run concurrently over
        # the same pages and race each other's index writes
        return jobstore.start("research_session",
                              f"{session} scan: all research pages",
                              lambda p: job_research_session(session, p))

    if action == "scan_earnings_today":
        raw_days = first("days_ahead", "0").strip() or "0"
        try:
            days_ahead = int(raw_days)
        except ValueError:
            return "days ahead must be a whole number (0 = today only)"
        if not 0 <= days_ahead <= 14:
            return "days ahead must be between 0 and 14"

        # ALL_UNIVERSES_SENTINEL keeps "everything I track" reachable from a
        # single-select; scan_earnings_today() reads None as "every list".
        picked = [u for u in (form.get("universe_earn") or [])
                  if u and u != ALL_UNIVERSES_SENTINEL]
        universes = picked or None
        unknown = [u for u in picked if u not in available_universes()]
        if unknown:
            return f"unknown universe(s): {', '.join(unknown)}"

        scope = " + ".join(picked) if picked else "all watchlists"
        label = ("earnings today" if not days_ahead
                 else f"earnings today +{days_ahead}d")
        return jobstore.start(
            "scan_earnings_today", f"{label} screen: {scope}",
            lambda p: job_earnings_today(universes, days_ahead, p))

    if action == "etf_profiles":
        return jobstore.start("etf_profiles", "ETF profiles refresh",
                              job_etf_profiles)

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

    # "smallcap" is the former action name, still accepted so a browser tab
    # left open across the rename does not post into a void.
    if action in ("stockdaytrade", "smallcap"):
        from datetime import datetime as _dt
        try:
            limit = max(5, min(60, int(first("limit", "25") or 25)))
        except ValueError:
            return "candidates must be a whole number"
        raw_at = first("at_time").strip()
        at_time = None
        if raw_at:
            try:
                at_time = _dt.strptime(raw_at, "%H:%M").time()
            except ValueError:
                return f"as-of time must be HH:MM, got {raw_at!r}"
        raw_risk = first("risk_pct").strip()
        try:
            risk_pct = float(raw_risk) if raw_risk else None
        except ValueError:
            return "risk % must be a number"
        if risk_pct is not None and not (0 < risk_pct <= 5):
            return "risk % must be between 0 and 5"
        min_grade = first("min_grade", "C") or "C"
        profile = first("profile", "small") or "small"
        if profile not in ("small", "mid", "large", "auto"):
            return f"unknown profile {profile!r}"

        # An empty watchlist field means "screen the market" — the page's
        # default and the only way to find names that were on no list
        # yesterday. A named list skips the §1 screen entirely.
        wl = first("watchlist").strip()
        tickers = None
        if wl:
            tickers = watchlist_tickers([wl])
            if not tickers:
                return f"watchlist {wl!r} is empty or unknown"

        scope = f"{wl} ({len(tickers)})" if wl else f"{profile}-cap screen"
        label = (f"day-trade scan: {scope}, top {limit}"
                 + (f" as of {raw_at} ET" if raw_at else "")
                 + (f", grade {min_grade}+" if min_grade != "ALL" else ""))
        return jobstore.start(
            "stockdaytrade", label,
            lambda p: job_stockdaytrade(limit, at_time, min_grade, risk_pct,
                                        profile, p, tickers=tickers,
                                        universe=wl))

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

    if action == "portfolio_risk":
        return jobstore.start("portfolio_risk", "portfolio risk analysis",
                              lambda p: job_portfolio_risk(p))

    if action == "csp_scan":
        def _int(key, default):
            try:
                return int(first(key) or default)
            except ValueError:
                return default
        min_dte, max_dte = _int("min_dte", 20), _int("max_dte", 45)
        if min_dte >= max_dte:
            return f"min DTE ({min_dte}) must be below max DTE ({max_dte})"
        policy = (first("earnings_policy") or "AVOID").upper()
        if policy not in ("AVOID", "CONTROLLED", "ACCEPT"):
            return f"earnings policy must be AVOID/CONTROLLED/ACCEPT, got {policy!r}"
        allow_earnings = policy != "AVOID"
        target_dte = _int("target_dte", 35)
        min_stock, min_csp = _int("min_stock", 0), _int("min_csp", 0)
        show_rejected = first("reference_rejected") in ("1", "on", "true")
        # 0 means "the engine's default". Capped at 600 because past that a
        # scan is an hour of chains and the job tray is the wrong place to
        # discover it.
        ref_budget = max(0, min(600, _int("reference_budget", 0)))
        limit = max(1, min(60, _int("limit", 25)))
        from stockanalysis.webapp.longterm_view import parse_tickers
        tickers = parse_tickers(first("tickers"))
        # A typed ticker wins over the picker: naming a symbol is the more
        # specific instruction, and silently unioning the two would scan a
        # list you had just narrowed away from.
        scope = ""
        if not tickers:
            picked = [n for n in (form.get("list") or []) if n.strip()]
            from_list = watchlist_tickers(picked) or []
            if from_list:
                # Capped, and the cap is applied HERE so the label can tell
                # the truth — a list silently becoming its first 60 is the
                # kind of thing you notice three minutes later, in a job
                # tray, by counting.
                #
                # More generous than the typed box's 60 because a watchlist
                # is a deliberate, curated set: you picked it, so the
                # reasonable default is to scan it rather than a prefix of
                # it. One chain per name, so 200 is a few minutes.
                tickers = [str(t).upper() for t in from_list][:MAX_LIST_SCAN]
                scope = f"{', '.join(picked)} ({len(from_list)})"
                if len(from_list) > len(tickers):
                    scope += f" — first {len(tickers)}"
        if scope:
            label = f"CSP scan: {scope}"
        elif tickers:
            label = (f"CSP scan: {', '.join(tickers[:8])}"
                     + ("…" if len(tickers) > 8 else ""))
        else:
            label = f"CSP scan: {min_dte}-{max_dte} DTE, top {limit} eligible"
        # A named set is a question about those names, so every one of them
        # should get chain work rather than the first `limit`.
        if tickers:
            limit = max(limit, len(tickers))
        if policy != "AVOID":
            label += f" (earnings {policy})"
        if show_rejected:
            label += (f" + rejected premiums"
                      + (f" (up to {ref_budget})" if ref_budget else ""))
        return jobstore.start("csp_scan", label,
                              lambda p: job_csp_scan(min_dte, max_dte,
                                                     allow_earnings, limit, p,
                                                     tickers, policy,
                                                     target_dte, min_stock,
                                                     min_csp, show_rejected,
                                                     ref_budget))

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

    if action == "longterm_entry_scan":
        return jobstore.start("longterm_entry_scan",
                              "Long-term entry levels (manual)",
                              lambda p: job_longterm_entry_alerts(p))

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


def schedule_settings_save(form: dict) -> dict:
    """form: dict of lists (parse_qs), from the Automation page's schedule-wide
    switches. Today that is just run_when_logged_out — whether automation keeps
    firing while nobody is signed in.

    Nothing to re-register: the scheduler loop re-reads this setting on every
    tick (every 30s), so the change takes effect on the next tick without
    touching the job registry."""
    from stockanalysis.scheduling import schedule_config

    raw = {k: v[0] for k, v in form.items() if k in schedule_config.SETTINGS_DEFAULTS}
    try:
        settings = schedule_config.save_settings(raw)
    except (OSError, ValueError) as e:
        return {"ok": False, "message": str(e)}

    if settings["run_when_logged_out"]:
        msg = "Automation now runs whether or not anyone is signed in"
    else:
        msg = "Automation now pauses when the last session signs out"
    return {"ok": True, "message": msg, "settings": settings}


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
# SCREENER — query the research library through core.screener
# ─────────────────────────────────────────────────────────────────────────────
# The universe is the research index (every ticker with a research page),
# built once per request. That's ~560 rows through a handful of pure
# functions — a few tens of ms, measured — so there's no cache to invalidate
# and a screen always reflects the library as it stands right now. If a scan
# is running while you screen, you get whatever it has already written,
# which is the same guarantee every other page in this app gives.

SAVED_SCREENS_PATH = PROJECT_ROOT / "data" / "saved_screens.json"


def _screen_universe():
    """The library as the screener sees it: research_index.json with
    core.research_snapshot filling any field the index is missing.

    The snapshot is what makes the screener robust to the index being
    rewritten by a process running older code — see research_snapshot.py.
    Live index values still win field by field, so this only ever adds
    coverage, never staleness on top of fresh data.
    """
    from stockanalysis.core import research_snapshot
    from stockanalysis.core.screener import build_universe
    from stockanalysis.reporting.research import (
        load_research_index, load_watchlists)
    index = load_research_index(OUTPUT_DIR)
    try:
        entries = research_snapshot.merged(
            index, research_snapshot.load(OUTPUT_DIR))
    except Exception as e:
        print(f"[Screener] snapshot unavailable, using index alone ({e})")
        entries = list(index.values())
    # Tickers no scan ever got a quote for are dropped rather than screened.
    # They can't satisfy any condition, so keeping them would only inflate
    # every "no value for N of M tickers" note with symbols that have no data
    # to begin with — and the entry gate stamps them Category=Avoid, so they
    # would wrongly match a "Category = Avoid" screen as if that were a
    # finding. The Research page names them; see research_snapshot.has_quote.
    entries = [e for e in entries if research_snapshot.has_quote(e)]
    try:
        watchlists = load_watchlists()
    except Exception:
        watchlists = {}
    watch = {t for names in watchlists.values() for t in (names or [])}
    return build_universe(entries, watchlist_tickers=watch,
                          watchlist_map=watchlists)


def screen(payload: dict) -> dict:
    """Run a screen. payload: {rules, weights, composite, sort, limit,
    preset, query}. Returns JSON-safe results for the Screener page."""
    from stockanalysis.core import screener as S

    rows = _screen_universe()

    if payload.get("preset"):
        group = S.preset_group(str(payload["preset"])) or S.Group("AND", [])
    elif payload.get("query"):
        group = S.Group("AND", S.parse_query(str(payload["query"])))
    else:
        group = S.group_from_json(payload.get("rules"))

    weights = {str(k): float(v) for k, v in (payload.get("weights") or {}).items()}
    composite = {str(k): float(v)
                 for k, v in (payload.get("composite") or {}).items()}
    sort = str(payload.get("sort") or "match")
    try:
        limit = int(payload.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100

    res = S.screen(rows, group, weights=weights or None,
                   composite_weights=composite or None, sort=sort, limit=limit)

    # Decision layer over the matches. Screening decides membership; this
    # says what to do about each one. Applied after the sort/limit so it
    # only runs over the page being returned, not the whole universe.
    from stockanalysis.core import decision_engine as DE
    strategy = str(payload.get("strategy") or "LONGTERM").upper()
    if strategy not in DE.STRATEGIES:
        strategy = "LONGTERM"
    regime = str(payload.get("regime") or "FAVORABLE").upper()
    for m in res.matches:
        try:
            d = DE.decide(m, regime=regime, strategy=strategy)
            # Both verdicts, always. A single action is ambiguous without
            # knowing which question it answered — ALL reads AVOID as an
            # investment and BUY NOW as a trade, and showing only one of
            # those makes the other look like a contradiction.
            lt = (d if strategy == "LONGTERM"
                  else DE.decide(m, regime=regime, strategy="LONGTERM"))
            sw = (d if strategy == "SWING"
                  else DE.decide(m, regime=regime, strategy="SWING"))
        except Exception as e:                              # pragma: no cover
            print(f"[Screener] decision failed for {m.get('ticker')}: {e}")
            continue
        m["action_longterm"] = lt["action"]
        m["action_longterm_icon"] = lt["icon"]
        m["action_swing"] = sw["action"]
        m["action_swing_icon"] = sw["icon"]
        m["action"] = d["action"]
        m["action_icon"] = d["icon"]
        m["inv_score"] = d["investment"]
        m["swing_dec"] = d["swing"]
        m["confluence"] = d["confluence"]
        m["decision_triggers"] = d["triggers"]
        m["decision_risks"] = d["risks"]
        # The pullback plan, when either verdict is waiting for one. The
        # levels and distances are strategy-independent — they are read off
        # the tape — so the active strategy's plan is preferred only for the
        # "what gate did it miss" part, which is the one strategy-specific
        # piece. `strategies` names who is waiting: both agreeing is a
        # stronger statement than one of them, and the card says which.
        pb = d.get("pullback") or lt.get("pullback") or sw.get("pullback")
        if pb:
            waiting = [name for name, v in (("Long-term", lt), ("Swing", sw))
                       if v["action"] == "BUY ON PULLBACK"]
            m["pullback"] = {**pb, "strategies": waiting}
        m["earnings_risk"] = d["earnings_risk"]
        m["decision_reliable"] = d["reliable"]

    if sort == "decision":
        # Actionable first, then by how strongly it scored. The engine's
        # action order is the ranking — sorting by score alone would put a
        # high-scoring AVOID above a BUY.
        rank = {a: i for i, a in enumerate(DE.ACTIONS)}
        res.matches.sort(key=lambda m: (
            rank.get(m.get("action"), len(DE.ACTIONS)),
            -(m.get("inv_score") or 0) if strategy == "LONGTERM"
            else -(m.get("swing_dec") or 0),
            -(m.get("confluence") or 0)))

    return {
        "ok": True,
        "universe": res.total,
        "count": res.summary["count"],
        "shown": len(res.matches),
        "summary": res.summary,
        "results": [_screen_row(m) for m in res.matches],
        # Pills echo back the parsed rules so a natural-language search
        # becomes editable pills rather than an opaque query string.
        "rules": S.group_to_json(group),
        "pills": [{"text": S.describe(c), "field": c.field}
                  for c in S._walk(group)],
        "stats": res.stats,
        "missing": [{"field": k, "label": _field_label(k), "rows": v}
                    for k, v in sorted(res.missing_counts.items(),
                                       key=lambda kv: -kv[1]) if v],
        "refine": S.refine_suggestions(group),
        "strategy": strategy,
        "actions": _action_counts(res.matches),
    }


def _action_counts(matches: list[dict]) -> dict:
    """Tally by action so the page can lead with what is actionable."""
    out: dict = {}
    for m in matches:
        a = m.get("action")
        if a:
            out[a] = out.get(a, 0) + 1
    return out


def _field_label(key: str) -> str:
    from stockanalysis.core.screener import FIELD_BY_KEY
    spec = FIELD_BY_KEY.get(key)
    return spec.label if spec else key


# Columns every result card shows, plus whatever the screen filtered on.
_SCREEN_KEYS = (
    "ticker", "name", "sector", "industry", "price", "quality", "quality_label",
    "health", "health_label", "moat", "moat_total", "moat_label", "rs_rank",
    "eps_growth", "forward_pe", "inst_own", "market_cap", "category", "grade",
    "conviction", "conv_stars", "conv_action", "above_200ma", "in_buy_zone",
    "buy_zone_label", "buy_zone_score", "abs_vs_8ema", "pct_vs_8ema",
    "pct_vs_50ma", "pct_vs_200ma", "breakout_probability", "swing_score",
    "daytrade_score", "rr", "dist_52w_high", "rvol", "atr_pct", "rsi",
    "days_to_earnings", "earnings_soon", "canslim", "updated_at",
    "recovered", "data_as_of",
    "match_score", "composite", "why", "matched_fields",
    # decision layer (core/decision_engine.py)
    "action", "action_icon", "inv_score", "swing_dec", "confluence",
    "action_longterm", "action_longterm_icon",
    "action_swing", "action_swing_icon",
    "decision_triggers", "decision_risks", "earnings_risk", "decision_reliable",
    "pullback",
)


def _screen_row(m: dict) -> dict:
    return {k: m.get(k) for k in _SCREEN_KEYS}


def screener_meta() -> dict:
    """Field registry + presets + saved searches — everything the page needs
    to build its pickers without hardcoding a second copy of the registry.

    The universe is built once and reused: this used to call
    _screen_universe() three times (counts, enum values, preset counts),
    which is the same ~560-row build repeated for no reason.
    """
    from stockanalysis.core import screener as S
    rows = _screen_universe()
    return {
        "fields": [{"key": f.key, "label": f.label, "group": f.group,
                    "kind": f.kind, "unit": f.unit, "hint": f.hint,
                    "values": list(f.values), "decimals": f.decimals,
                    "direction": f.direction,
                    "ops": list(S.OPS_FOR_KIND.get(f.kind, ("gte",)))}
                   for f in S.FIELDS],
        "groups": list(S.FIELD_GROUPS),
        "operators": S.OPERATORS,
        # Counts are computed live (~60ms for the whole set) rather than
        # cached, so a preset that currently matches nothing says so on its
        # card instead of after a click. Whether a screen is empty depends on
        # the last scan, not on the preset.
        "presets": [{"key": p["key"], "icon": p["icon"], "name": p["name"],
                     "desc": p["desc"], "group": p.get("group", "Other"),
                     "strategy": S.preset_strategy(p["key"]),
                     "count": S.screen(rows, S.preset_group(p["key"]),
                                       with_stats=False).summary["count"],
                     "conditions": [S.conditions_to_json(c)
                                    for c in p["conditions"]],
                     "pills": [S.describe(c) for c in p["conditions"]]}
                    for p in S.PRESETS],
        "preset_groups": list(S.PRESET_GROUPS),
        "enums": _enum_values(rows),
        "composite_defaults": S.COMPOSITE_DEFAULTS,
        "saved": _saved_with_counts(rows),
        "universe": len(rows),
    }


def _saved_with_counts(rows: list[dict]) -> list[dict]:
    """Saved searches with a live match count, so they can render in the
    preset grid alongside the built-ins instead of only in a dropdown — a
    screen you built yourself is the one you most want a count on."""
    from stockanalysis.core import screener as S
    out = []
    for s in load_saved_screens():
        entry = dict(s)
        try:
            group = S.group_from_json(s.get("rules"))
            entry["count"] = S.screen(rows, group, with_stats=False
                                      ).summary["count"]
            entry["pills"] = [S.describe(c) for c in S._walk(group)]
        except Exception:
            entry["count"] = None
            entry["pills"] = []
        out.append(entry)
    return out


def _enum_values(rows: list[dict] | None = None) -> dict:
    """Real values present in the library for the free-form enums (sector,
    industry) and lists (watchlists), so the picker offers what actually
    exists instead of a hardcoded list that drifts."""
    from stockanalysis.core.screener import FIELDS, ENUM, LIST
    if rows is None:
        rows = _screen_universe()
    out = {}
    for f in FIELDS:
        if f.kind == ENUM:
            out[f.key] = list(f.values) if f.values else sorted(
                {str(r.get(f.src)) for r in rows if r.get(f.src)})
        elif f.kind == LIST:
            out[f.key] = sorted({str(v) for r in rows
                                 for v in (r.get(f.src) or [])})
    return out


def screener_ticker(query: str) -> dict:
    """Which presets a single ticker qualifies for.

    The screener answers "what matches this screen"; this answers the
    inverse — "which screens does this name already appear in". A ticker in
    six presets is a different proposition from one scraping into a single
    loose screen, and that is not visible from any one result list.

    Evaluating all 62 presets against one row costs a few milliseconds, so
    it runs live rather than off a cache that could disagree with the grid.
    """
    from stockanalysis.core import screener as S
    from stockanalysis.core import decision_engine as DE

    ticker = (query or "").strip().upper()
    if not ticker:
        return {"ok": False, "message": "Enter a ticker"}

    rows = _screen_universe()
    match = next((r for r in rows if (r.get("ticker") or "").upper() == ticker),
                 None)
    if match is None:
        near = sorted(r["ticker"] for r in rows
                      if r.get("ticker", "").startswith(ticker[:2]))[:6]
        return {"ok": False, "ticker": ticker,
                "message": f"{ticker} is not in the research library",
                "suggestions": near}

    hits = []
    for preset in S.PRESETS:
        group = S.preset_group(preset["key"])
        if group and S.eval_group(match, group):
            hits.append({"key": preset["key"], "icon": preset["icon"],
                         "name": preset["name"], "group": preset["group"],
                         "kind": "preset"})
    for saved in load_saved_screens():
        try:
            if S.eval_group(match, S.group_from_json(saved.get("rules"))):
                hits.append({"key": "saved:" + saved["name"],
                             "icon": saved.get("icon") or "⭐",
                             "name": saved["name"], "group": "My screens",
                             "kind": "saved"})
        except Exception:
            continue

    scores = DE.score_row(match)
    return {
        "ok": True, "ticker": ticker, "name": match.get("name"),
        "sector": match.get("sector"), "price": match.get("price"),
        "presets": hits, "total_presets": len(S.PRESETS),
        "investment": scores["investment"]["score"],
        "swing": scores["swing"]["score"],
        "confluence": scores["confluence"]["score"],
        "actions": {s: DE.decide(match, strategy=s)["action"]
                    for s in ("LONGTERM", "SWING")},
        "buy_zone_label": match.get("buy_zone_label"),
        "category": match.get("category"),
        "recovered": match.get("recovered"),
        "data_as_of": match.get("data_as_of"),
    }


def screener_suggest(prefix: str) -> list[dict]:
    from stockanalysis.core.screener import suggest
    return suggest(prefix)


# ── saved searches ───────────────────────────────────────────────────────────

def _read_saved_screens() -> list[dict] | None:
    """Saved searches from disk. None means "the file is there but couldn't
    be read" — deliberately distinct from "no file yet".

    The distinction is the whole point. This used to answer both cases with
    the shipped starter list, so a read that lost a race with a rewrite
    returned the defaults, and the next save persisted them — silently
    replacing the user's own searches with stock ones. Substituting defaults
    for data you failed to read is indistinguishable from the user having
    deleted their work.
    """
    import json
    if not SAVED_SCREENS_PATH.exists():
        return []
    try:
        data = json.loads(SAVED_SCREENS_PATH.read_text())
    except (ValueError, OSError):
        return None
    return data if isinstance(data, list) else None


def load_saved_screens() -> list[dict]:
    """For display. Seeds the starter list only when there is genuinely no
    file — never as a stand-in for an unreadable one."""
    screens = _read_saved_screens()
    if screens is None:
        print("[Screener] saved_screens.json unreadable — showing it as empty "
              "rather than overwriting it with defaults")
        return []
    # An existing file holding [] means the user deleted everything; only a
    # missing file means "first run, offer the starters". Seeding on empty
    # would resurrect deleted searches on the next save.
    if not screens and not SAVED_SCREENS_PATH.exists():
        return list(_STARTER_SCREENS)
    return screens


def _write_saved_screens(screens: list[dict]) -> None:
    """Atomic: write a sibling temp file and rename over the target.

    A plain write_text() truncates first, so a reader in another process (the
    workstation is routinely run on more than one port) can observe an empty
    or half-written file. rename() is atomic on the same filesystem, so a
    reader sees either the old file or the new one, never a torn one.
    """
    import json
    SAVED_SCREENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SAVED_SCREENS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(screens, indent=2))
    tmp.replace(SAVED_SCREENS_PATH)


def _mutate_saved_screens(change) -> dict:
    """Read-modify-write guarded against clobbering an unreadable file.

    `change(screens) -> (new_screens, result)`. If the file exists but can't
    be parsed we refuse to write at all: overwriting it would turn a
    transient read failure into permanent data loss, which is exactly how
    two of these went missing on 2026-08-05.
    """
    screens = _read_saved_screens()
    if screens is None:
        return {"ok": False, "message":
                "saved_screens.json could not be read — refusing to overwrite "
                "it. Check the file, then try again."}
    if not screens and not SAVED_SCREENS_PATH.exists():
        screens = list(_STARTER_SCREENS)
    new_screens, result = change(screens)
    if new_screens is not None:
        _write_saved_screens(new_screens)
        result["saved"] = new_screens
    return result


def save_screen(payload: dict) -> dict:
    """Create or overwrite a saved search by name."""
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "message": "Give the search a name"}
    rules = payload.get("rules") or {}

    def change(screens):
        kept = [s for s in screens if s.get("name") != name]
        kept.append({"name": name, "icon": payload.get("icon") or "⭐",
                     "rules": rules, "sort": payload.get("sort") or "match"})
        kept.sort(key=lambda s: s.get("name") or "")
        return kept, {"ok": True, "message": f"Saved “{name}”"}

    return _mutate_saved_screens(change)


def delete_screen(name: str) -> dict:
    def change(screens):
        kept = [s for s in screens if s.get("name") != name]
        if len(kept) == len(screens):
            return None, {"ok": False,
                          "message": f"No saved search named “{name}”",
                          "saved": screens}
        return kept, {"ok": True, "message": f"Deleted “{name}”"}

    return _mutate_saved_screens(change)


# Shipped so the Saved panel isn't empty on first load. They're written to
# disk on the first edit, after which they're ordinary user rows.
def _mk(*conds) -> dict:
    from stockanalysis.core.screener import Condition, Group, group_to_json
    return group_to_json(Group("AND", [Condition(*c) for c in conds]))


_STARTER_SCREENS: tuple[dict, ...] = (
    {"name": "AI Leaders", "icon": "⭐", "sort": "match",
     "rules": _mk(("quality", "gt", 90), ("moat", "gte", 3),
                  ("eps_growth", "gt", 25), ("rs_rank", "gt", 80))},
    {"name": "Buy Zone", "icon": "⭐", "sort": "match",
     "rules": _mk(("in_buy_zone", "eq", True), ("above_200ma", "eq", True))},
    {"name": "Cheap Growth", "icon": "⭐", "sort": "composite",
     "rules": _mk(("forward_pe", "lt", 25), ("eps_growth", "gt", 20),
                  ("quality", "gt", 80))},
    {"name": "High RS Stocks", "icon": "⭐", "sort": "rs",
     "rules": _mk(("rs_rank", "gt", 90), ("above_200ma", "eq", True))},
    {"name": "Strong Fundamentals", "icon": "⭐", "sort": "quality",
     "rules": _mk(("quality", "gt", 85), ("health", "gt", 80),
                  ("moat", "gte", 3))},
    {"name": "Swing Ready", "icon": "⭐", "sort": "match",
     "rules": _mk(("swing_score", "gt", 75), ("abs_vs_8ema", "within", 2))},
    {"name": "Turnaround Candidates", "icon": "⭐", "sort": "match",
     "rules": _mk(("is_turnaround", "eq", True), ("health", "gt", 60))},
)


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


# ─────────────────────────────────────────────────────────────────────────────
# LONG-TERM BUY ENGINE
# ─────────────────────────────────────────────────────────────────────────────
# The engine (core.longterm) runs over the RAW scan rows rather than the
# flattened screener universe: it reads statement-level fields the screener
# never surfaces (free cash flow history, share count, beta, moving-average
# slopes), and flattening them into the screener's row shape first would
# mean maintaining a second copy of that mapping.

# core.market_regime speaks Bullish/Neutral/Defensive; the long-term engine
# speaks the decision_engine vocabulary. Neutral maps to SELECTIVE rather
# than FAVORABLE because §11 of the framework treats a mixed tape as a
# raise-the-bar condition, not a normal one.
LONGTERM_REGIME_MAP = {"Bullish": "FAVORABLE", "Neutral": "SELECTIVE",
                       "Defensive": "DEFENSIVE"}


def _longterm_entries() -> list[dict]:
    """Research-index entries for the library, snapshot-merged.

    Split out from _longterm_universe() because two callers need two shapes:
    the engine wants the raw scan row, while core.market_position ranks over
    the index entry (it reads `sector`, `market_cap` and `raw.Industry`).
    Deriving one from the other at the call site would mean rebuilding half
    an entry from a row that had it a moment ago.
    """
    from stockanalysis.core import research_snapshot
    from stockanalysis.reporting.research import load_research_index
    index = load_research_index(OUTPUT_DIR)
    try:
        return research_snapshot.merged(
            index, research_snapshot.load(OUTPUT_DIR))
    except Exception as e:
        print(f"[LongTerm] snapshot unavailable, using index alone ({e})")
        return list(index.values())


def _longterm_universe() -> list[dict]:
    """Raw scan rows for every library ticker that has a real quote, with
    core.research_snapshot filling anything the live index is missing —
    the same durability the Screener relies on."""
    from stockanalysis.core import research_snapshot
    rows = []
    for entry in _longterm_entries():
        if not research_snapshot.has_quote(entry):
            continue
        raw = dict(entry.get("raw") or {})
        if not raw.get("Ticker"):
            raw["Ticker"] = entry.get("ticker")
        if raw.get("Ticker"):
            rows.append(raw)
    return rows


def longterm_risk_free() -> tuple[float, str]:
    """The 10-year Treasury yield as a decimal, for the reverse DCF's
    discount rate.

    ^TNX already quotes the yield in percent — 4.21 means 4.21%, not 42.1%
    — so it is divided by 100 and never by 10. Falls back to the module
    default rather than failing the page: the discount rate moves the
    hurdle, and a page that will not render because a quote was slow is
    worse than one built on a 4.2% assumption that says so.
    """
    from stockanalysis.core.longterm.valuation import DEFAULT_RISK_FREE
    try:
        import yfinance as yf
        hist = yf.Ticker("^TNX").history(period="5d")
        if not hist.empty:
            pct = float(hist["Close"].iloc[-1])
            if 0 < pct < 25:
                return pct / 100.0, f"10Y Treasury {pct:.2f}% (live)"
    except Exception as e:
        print(f"[LongTerm] ^TNX unavailable ({e})")
    return DEFAULT_RISK_FREE, (f"10Y Treasury unavailable — assumed "
                               f"{DEFAULT_RISK_FREE * 100:.1f}%")


def longterm_regime(override: str | None = None) -> tuple[str, str]:
    """(regime, explanation). An explicit override always wins so the page
    can ask "what would this look like in a defensive tape"."""
    if override and str(override).upper() in LONGTERM_REGIME_MAP.values():
        return str(override).upper(), "set manually on this page"
    try:
        from stockanalysis.core import regime_client
        cached = regime_client.load_cached() or {}
        bias = str(cached.get("primary_bias") or "").strip().title()
        mapped = LONGTERM_REGIME_MAP.get(bias)
        if mapped:
            return mapped, (f"market regime {bias} as of "
                            f"{cached.get('generated_at_et', 'unknown')[:10]}")
    except Exception as e:
        print(f"[LongTerm] regime unavailable ({e})")
    # Unknown tape must not silently loosen the gates, and must not silently
    # close them either — SELECTIVE is the honest middle.
    return "SELECTIVE", "market regime unknown — defaulting to SELECTIVE"


def _json_safe(obj):
    """numpy/pandas scalars are not JSON-serialisable and reach here inside
    scan rows (int64 distribution-day counts, float64 statement figures).
    json.dumps() raises on them, which turns a working page into a 500."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (str, bool)) or obj is None:
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return None if obj != obj else float(obj)
    item = getattr(obj, "item", None)          # numpy scalar
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass
    return str(obj)


def longterm_lists() -> dict:
    """{name: [tickers]} for the /longterm list picker.

    The same watchlists the Scanner runs against, so "daytrade" and
    "watchlist" mean the same set of names on both pages rather than the
    long-term engine keeping a second copy that drifts.
    """
    from stockanalysis.reporting.research import load_watchlists
    try:
        lists = load_watchlists()
    except Exception as e:
        print(f"[LongTerm] watchlists unavailable ({e})")
        return {}
    return {name: [str(t).upper() for t in (tickers or [])]
            for name, tickers in lists.items() if tickers}


# ─────────────────────────────────────────────────────────────────────────────
# RISK SETTINGS — the account the sizing engine sizes against
# ─────────────────────────────────────────────────────────────────────────────

RISK_SETTINGS_PATH = PROJECT_ROOT / "data" / "risk_settings.json"


def load_risk_settings() -> dict:
    """Trading capital, risk per trade, allocation cap and ATR multiplier.

    Falls back to the environment defaults (the same ACCOUNT_SIZE /
    RISK_PER_TRADE_PCT / MAX_POSITION_PCT the HTML dashboard's sizing reads)
    whenever the file is missing OR unreadable. Unlike saved searches, there
    is nothing to lose here by substituting defaults: these are four numbers
    the user can retype, not work they created, and a page that will not
    render because a settings file got truncated is the worse failure.
    """
    from stockanalysis.core.longterm import position_sizing as PS
    import json
    raw = {}
    if RISK_SETTINGS_PATH.exists():
        try:
            loaded = json.loads(RISK_SETTINGS_PATH.read_text())
            if isinstance(loaded, dict):
                raw = loaded
        except (ValueError, OSError) as e:
            print(f"[Risk] settings unreadable, using defaults ({e})")
    return PS.normalize_settings(raw)


def save_risk_settings(form: dict) -> dict:
    """Persist the four sizing inputs. Values are normalized and CLAMPED
    before they touch disk, so a typo can never be stored as a setting."""
    from stockanalysis.core.longterm import position_sizing as PS
    import json

    def first(key):
        return (form.get(key) or [""])[0]

    settings = PS.normalize_settings({
        "capital": first("capital"),
        "risk_pct": first("risk_pct"),
        "max_allocation_pct": first("max_allocation_pct"),
        "atr_multiplier": first("atr_multiplier"),
    })
    keep = {k: settings[k] for k in
            ("capital", "risk_pct", "max_allocation_pct", "atr_multiplier")}
    try:
        RISK_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = RISK_SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(keep, indent=2))
        tmp.replace(RISK_SETTINGS_PATH)
    except OSError as e:
        return {"ok": False, "message": f"could not save: {e}"}
    return {"ok": True, "message": (
        f"Sizing set to ${keep['capital']:,.0f} capital · "
        f"{keep['risk_pct']:g}% risk · {keep['max_allocation_pct']:g}% max "
        f"position"), "settings": settings}


def longterm(regime_override: str | None = None,
             risk_settings: dict | None = None) -> dict:
    """The whole Long-Term Buy Engine run, ready for the page.

    Returns {"rows", "counts", "regime", "regime_note", "risk_free_note",
             "coverage", "settings"}. `coverage` reports how much of the
    library carries the columns the engine needs, because a library scanned
    before core.longterm existed will produce WATCH for everything and the
    page has to say why rather than looking broken.

    Position sizing is attached here rather than inside `evaluate()`: the
    verdict belongs to the engine and the account belongs to the user, so
    keeping them apart is what lets one evaluation be sized against two
    different accounts without re-running the gates.
    """
    from stockanalysis.core.longterm import engine as E
    from stockanalysis.core.longterm import position_sizing as PS
    from stockanalysis.core.longterm import profile as PR
    from stockanalysis.core.swing import engine as SW

    entries = _longterm_entries()
    rows = _longterm_universe()
    regime, regime_note = longterm_regime(regime_override)
    risk_free, risk_free_note = longterm_risk_free()

    results = E.evaluate_universe(rows, regime=regime, risk_free=risk_free)

    settings = risk_settings or load_risk_settings()
    by_ticker = {r.get("Ticker"): r for r in rows}
    PS.attach(results, by_ticker, settings)
    # Same seam as sizing: context the engine's gates never read, attached
    # after the verdict rather than computed inside it.
    PR.attach(results, by_ticker, entries)

    # The swing verdict, from a separate engine that reads none of the above.
    # Attached here rather than inside evaluate() precisely because it is a
    # different question: the same stock can be a long-term WAIT and a swing
    # setup, and folding it into the company verdict would make one of the
    # two answers a qualifier on the other.
    for result in results:
        row = by_ticker.get(result.get("ticker"))
        if row:
            result["swing"] = SW.evaluate(row, regime=regime,
                                          settings=settings)

    counts = {}
    for r in results:
        counts[r["action"]] = counts.get(r["action"], 0) + 1

    total = len(rows) or 1
    coverage = {
        "total": len(rows),
        "ma_slope": sum(1 for r in rows if r.get("MA200_Slope%") is not None),
        "reversal": sum(1 for r in rows if r.get("Reversal_Candle") is not None),
        "statements": sum(1 for r in rows if r.get("FCF_CAGR%") is not None),
        "breakout": sum(1 for r in rows
                        if r.get("Prior_Breakout_Level") is not None),
    }
    coverage["needs_rescan"] = coverage["reversal"] < total * 0.5

    return _json_safe({
        "rows": results,
        "counts": counts,
        "regime": regime,
        "regime_note": regime_note,
        "risk_free_note": risk_free_note,
        "coverage": coverage,
        "settings": settings,
    })


# ─────────────────────────────────────────────────────────────────────────────
# SCAN & ANALYZE — the Long-Term engine narrowed to one scope, for the Dashboard
# ─────────────────────────────────────────────────────────────────────────────

ANALYSIS_SCOPES = ("ticker", "watchlist", "daytrade", "all")

# Enough rows to read a watchlist end to end, few enough that the reasoning
# grid each row carries doesn't turn the Dashboard into a megabyte of HTML.
ANALYSIS_LIMIT = 60


def analysis(scope: str, value: str = "") -> dict:
    """Long-Term engine rows for one scope — a ticker, a watchlist, the
    day-trade list, or everything the library covers.

    The whole universe is evaluated and THEN narrowed, never the reverse: the
    engine ranks relative strength across the library, so scoring three
    tickers on their own would quietly hand back different numbers than the
    Long-Term page shows for the same three names.
    """
    from .longterm_view import parse_tickers

    scope = (scope or "all").strip().lower()
    if scope not in ANALYSIS_SCOPES:
        return {"error": f"unknown scope {scope!r}"}

    data = longterm()
    rows = data["rows"]
    by_ticker = {r["ticker"]: r for r in rows}
    missing: list[str] = []
    full_link = "/longterm"

    if scope == "ticker":
        wanted = parse_tickers(value)
        if not wanted:
            return {"error": "Type a ticker first — e.g. NVDA, or NVDA AMD MSFT."}
        picked = []
        for t in wanted:
            (picked.append(by_ticker[t]) if t in by_ticker else missing.append(t))
        # Input order, not engine ranking: someone comparing names they typed
        # expects to read them back the way they wrote them.
        rows = picked
        label = "Ticker · " + ", ".join(wanted)
        full_link = f"/longterm?q={quote_plus(' '.join(wanted))}"
    elif scope in ("watchlist", "daytrade"):
        name = "daytrade" if scope == "daytrade" else (value or "").strip()
        lists = longterm_lists()
        members = lists.get(name)
        if members is None:
            return {"error": f"no such list: {name or '(none picked)'}"}
        rows = [by_ticker[t] for t in members if t in by_ticker]
        missing = [t for t in members if t not in by_ticker]
        label = ("Day trade list" if scope == "daytrade"
                 else f"Watchlist · {name}")
        full_link = f"/longterm?list={quote_plus(name)}"
    else:
        label = "All tickers"

    return {"rows": rows[:ANALYSIS_LIMIT], "missing": missing, "label": label,
            # `matched` counts what the scope resolved to before the display
            # cap, so the panel can say "showing 60 of 552" rather than
            # implying the other 492 went unscored.
            "matched": len(rows), "scope_total": len(rows) + len(missing),
            "full_link": full_link, "regime": data["regime"],
            "regime_note": data["regime_note"]}


def leaders_verdict(ticker: str, direction: str, verdict: str,
                    note: str = "") -> dict:
    """Store a human reading of one name's headlines for the /leaders page.

    Validation lives here rather than in the handler so the CLI and any future
    caller get the same rules: an unknown verdict or a direction that is not
    long/short is rejected rather than written through.
    """
    from stockanalysis.core import leaders_store

    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"ok": False, "error": "no ticker"}
    if direction not in ("long", "short"):
        return {"ok": False, "error": "direction must be 'long' or 'short'"}
    if verdict not in leaders_store.VERDICTS:
        return {"ok": False,
                "error": f"verdict must be one of {', '.join(leaders_store.VERDICTS)}"}
    saved = leaders_store.save_verdict(ticker, direction, verdict, note)
    if saved is None:
        return {"ok": False, "error": "no sector-leader snapshot to write to"}
    return {"ok": True, "verdict": saved}
