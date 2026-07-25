# StockAnalysis — Trading Workstation

A local, single-user trading research workstation: S&P 500 / watchlist scanning,
signal grading, conviction scoring, walk-forward backtesting, a priority alert
engine with email digests, and a stdlib-only web UI — all built on **yfinance**
as the only market-data dependency (plus Resend for email and, optionally, the
Claude API for AI-generated summaries and trade coaching).

Everything runs on `localhost` from one Python process. No database, no
external services to stand up — state lives in flat JSON/CSV files under
`data/` (a deliberate choice for a single-user tool; see the docstring in
`src/stockanalysis/webapp/app.py` for the reasoning).

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in RESEND_API_KEY etc. (see Configuration)

# Web app (http://localhost:8899)
python src/stockanalysis/webapp/app.py

# One-off scan from the command line
python -m stockanalysis.scanners.scan_universe

# Scheduler (all timed jobs: scans, briefs, alert monitors)
python -m stockanalysis.scheduling.scheduler

# Tests (no network — all yfinance calls are mocked)
python -m unittest discover tests

# Verify email setup end-to-end
python3 scripts/send_test_alert.py
```

Run from the repo root with `src/` on `PYTHONPATH` (the entry points handle
this themselves when run directly).

---

## Web app pages

`webapp/app.py` serves eight pages (stdlib `http.server`, background jobs as
daemon threads via `jobstore.py`, page HTML in `pages.py`/`views.py`, job and
JSON-endpoint logic in `api.py`). Binds `127.0.0.1` only — no auth, don't
expose it beyond localhost.

### 🏠 Dashboard (`/`)
Home page: latest generated HTML report per universe, market-pulse snapshot
(VIX, SPY/QQQ strength, mega-cap concentration), quick links to recent scan
CSVs, and an AI Sentiment teaser card.

### 🤖 AI Sentiment (`/ai-sentiment`)
The AI-infrastructure market dashboard. `scanners/ai_pulse.py` fetches tiered
AI leaders (Leadership / Networking / Power), 10Y yield, DXY, SOXX, and NVDA
VWAP; `core/ai_sentiment.py` turns those into five deterministic readings:
**Risk Score**, **Rotation Detection**, **AI Health Index**, **Macro Event
Filter**, and a composite **AI Market Sentiment Score** — pure functions, unit
tested with synthetic fixtures.

### 📡 Scanner (`/scanner`)
Run a scan against any universe — built-ins (`daytrade`, `watchlist`,
`longterm`, `dividend`, `sp500`) or any user-curated watchlist from
`data/watchlists.json` — with a live pipeline-step progress view
(fetch → scan → grade → write → research) instead of a spinner.

### 🔎 Research (`/research`)
Per-ticker research: search any symbol, browse the generated mini research
pages (`data/output/research/<TICKER>.html` — self-contained HTML with an
inline-SVG chart, EMA/MA overlays, trade-plan level lines, full metric tables,
catalysts, strategy scores, conviction verdict, plain-language summary), and
run the **earnings sentiment engine** (`core/earnings_sentiment.py`): a
deterministic, yfinance-only scoring model that predicts the likely
post-earnings reaction (bullish/bearish split, expected move, confidence,
risk level, trading bias) — same inputs, same output, reproducible.

### 💼 Portfolio (`/portfolio`)
Positions and watchlist from `data/portfolio.csv` (copy
`data/portfolio_template.csv` to start), joined with the latest scan rows:
live P&L per position plus rule-based alerts — stop breach, strategy filters
degrading, earnings inside the blackout window, category flipped to Avoid.
Add/edit positions from the page via a modal form.

### 📓 Journal (`/journal`)
AI-coached trade journal (`core/trading_journal.py`). Log each trade's plan,
execution, psychology, and rule adherence; the page computes R-multiple,
return, and expectancy, and breaks results down by setup, emotion, and rule
violation, with monthly stats. With `ANTHROPIC_API_KEY` set, an **AI Review**
button sends the trade to Claude for an objective post-mortem (grades, top
mistakes/strengths, one sentence of coaching). Storage:
`data/journal_trades.json`.

### 🔔 Alerts (`/alerts`)
The alert feed: every active alert from the priority engine (see below), plus
the latest **Pre-Market Brief** rendered inline. MEDIUM/LOW alerts live here
only; CRITICAL/HIGH also went out by email when they fired.

### ⚙️ Automation (`/automation`)
Scheduler status and job history: what ran, when, how long it took, what
failed — the web view onto `jobstore`'s bounded job history.

---

## Alert & notification system

### Priority alert engine — `core/alerts.py`
One Alert shape for the whole app: `CRITICAL / HIGH / MEDIUM / LOW` priority,
a dedup/lifecycle store (`data/alerts_state.json`) so a standing condition
("NVDA is oversold") notifies **exactly once** and re-arms only after it
resolves, and an append-only log (`data/alerts_log.json`). CRITICAL and HIGH
batch into a **single digest email** per cycle (never one email per alert);
MEDIUM/LOW surface in the webapp feed only.

### Earnings alerts — `core/earnings_alerts.py`
Watchlist tickers with earnings coming up, gated by size so routine reports
don't fire: market cap > $2B **and** (expected move ≥ 5% **or** historical
avg earnings move ≥ 6%). Three day-granularity tiers:

| Tier | When | Priority | Delivery |
|------|------|----------|----------|
| `T-5` | 2–5 days out (fires once anywhere in the window) | MEDIUM | Alerts feed |
| `T-1` | day before | HIGH | Email + feed |
| `T-0` | day of | HIGH | Email + feed |

### Watchlist alerts — `core/watchlist_alerts.py`
Boolean conditions over already-computed scan fields — support/resistance
touch, breakout/breakdown, gap, volume surge, RSI extremes, MACD cross — each
condition's "currently true" state doubling as its dedup key: fire once when
it turns true, go quiet until it turns false and true again.

### Breaking news monitor — `core/news_monitor.py`
Scans watchlist headlines and alerts only on categories that plausibly move a
stock >3% (earnings surprise, M&A, guidance, regulatory…), reusing the same
catalyst classifier the dashboard's movers use. Runs on the 10-minute monitor
cadence.

### Pre-Market Brief — `core/premarket_brief.py`
The 7:00 AM "under 5 minutes to read" email: futures, VIX, econ calendar,
overnight/pre-market movers with catalysts, today's watchlist earnings, and
stocks near breakout (read from already-active alerts — no fresh scan).
Rendered as text + HTML, emailed, and shown on the Alerts page.

### Email delivery
All email goes through one sender: `market_movers.send_resend_email()`
(Resend SDK with a raw-HTTP fallback). Sandbox note: with the default
`onboarding@resend.dev` from-address, Resend only delivers **to the address
your Resend account is registered under**. Test the pipeline any time with
`python3 scripts/send_test_alert.py`.

---

## Scanning & scoring pipeline

The scan entry point is `scanners/scan_universe.py`: for every ticker it
fetches metrics, classifies a category, grades the setup, scores conviction
and strategy fit, and writes one CSV per run
(`data/output/stock_scan_YYYYMMDD_HHMM.csv`, `Scan_Time` = completion stamp)
plus the HTML dashboard and per-ticker research pages.

### The market funnel (pre-filter)

Big universes don't go straight to the per-ticker scan loop:

```
Universe → Market Regime → Sector Strength → Scanner
```

`scanners/sector_filter.py` + `core/sector_strength.py`: sectors are ranked
by their proxy ETF's return **relative to SPY** (0.6 × 1-month + 0.4 ×
3-month), and the day's regime sets selectivity — Bullish keeps the top 5
sectors, Neutral 4, Defensive 3. Only tickers in those sectors continue to
the scan, typically cutting an S&P 500 run by 60–80%.

Mechanics: ticker→sector lookups are cached
(`data/cache/ticker_sectors.json`) and seeded from prior scan CSVs, so the
funnel itself costs ~1 batch ETF download. It **fails open** — missing ETF
data, regime, or sector info means the name (or whole universe) scans
anyway. Auto-applies at ≥ 100 tickers (i.e. `sp500`), never to curated
watchlists; override with `--focus` / `--no-focus`.

| Module | What it does |
|--------|--------------|
| `core/metrics.py` | ~45 technical + fundamental metrics per ticker (native RSI/ADX/%B with Wilder's smoothing — no pandas_ta — plus ATR, RVOL, gaps, RS vs QQQ, VWAP, CANSLIM composite, entry gate). Daily-bar math lives in a pure `compute_daily_metrics()` so the backtest can reuse it point-in-time. |
| `categorize()` (in `scan_universe.py`) | Structured decision tree classifying each row: Momentum Breakout, Momentum-Pullback, VCP Setup, Turnaround, Longterm Hold, Avoid. |
| `core/grade_signals.py` | A/B/C grade + Entry / Stop / Target / Notes per categorized row. |
| `core/conviction.py` | Splits "should I take this?" into Quality (company), Setup (trade), Timing (now) — each 0–100 — plus overall score, 1–5 stars, and a ✅/⚠/❌ "why this stock" checklist. |
| `core/strategy_scores.py` | Three strategy-specific 0–100 scores with hard-filter pass flags: `Investment_Score` (6–24 mo), `Swing_Score` (2 d–8 wk), `DayTrade_Score` (intraday). |
| `core/key_levels.py` | Nearest support (S1) / resistance (R1) from 5-min swing pivots + volume profile merged with daily structural levels, scored on a 7-factor weighted formula. |
| `core/call_candidate.py` / `core/put_candidate.py` | Options-buying screens with hard disqualifiers: cheap-call turnaround setups and put-candidate scoring, attached to every scanned row. |
| `core/market_regime.py` | Classifies the day Bullish / Neutral / Defensive from VIX + index strength + breadth, and scales position-size multipliers per horizon accordingly. |

### Market context — `scanners/market_movers.py`
Top-10 pre-market / live / after-hours movers with news-catalyst
classification (regex over Yahoo Finance headlines); VIX with interpretation
bands; the week's high/medium-impact US economic calendar (ForexFactory feed,
cached 1 h in memory + on disk at `data/econ_calendar_cache.json`, serving
the last good copy when the feed rate-limits); a Fed rate outlook computed
from fed funds futures (ZQ); and `market_pulse()` — the dashboard-header
snapshot of mega-cap concentration, SPY/QQQ strength, and upcoming econ
events.

---

## Reporting

| Module | Output |
|--------|--------|
| `reporting/dashboard.py` | Self-contained HTML dashboard: market-pulse header + top-5 cards per section (Day Trade, Swing, Long-Term, Calls, Puts) with R:R-gated trade plans and fixed-risk position sizing from `ACCOUNT_SIZE` / `RISK_PER_TRADE_PCT` / `MAX_POSITION_PCT`. |
| `reporting/research.py` | Per-ticker research pages (see Research page above). |
| `reporting/portfolio.py` | Portfolio & Watchlist panel: P&L + rule-based position alerts. |
| `reporting/csv_writer.py` | Timestamped, Excel-friendly (UTF-8 BOM) scan CSVs. |
| `reporting/signal_tracker.py` | Logs every A/B signal and later resolves it against realized bars → hit rate and expectancy by category and grade. Closes the feedback loop: a persistently negative-expectancy category is a fix-or-retire signal. |

---

## Backtesting — `backtest/`

Walk-forward replay of the live pipeline: for each trading day D, metrics are
recomputed point-in-time (`compute_daily_metrics` on bars sliced to D), the
**same production** `categorize()` + `enrich_row()` run, and each A/B/C signal
resolves through the same `resolve_long()` rules the live tracker uses.

Honesty conventions baked in: signals computed at D's close act at D+1 via
buy-stop triggers a later bar must actually touch (no instant fills);
same-bar stop+target ambiguity resolves to **stop**; one open signal per
(ticker, category) — no pyramiding.

- `data.py` — bulk daily-bar download with a per-ticker local cache (`data/cache/`)
- `resolve.py` — the single outcome-resolution implementation shared with the live tracker
- `engine.py` — the walk-forward loop
- `report.py` — hit rate/expectancy by category, grade, and regime; R:R-gate check; cumulative-R equity curve (inline SVG) with SPY buy-and-hold context

---

## Scheduler — `scheduling/scheduler.py`

All timed jobs in one process, with a VIX-adaptive put-scan frequency, a
market-health gate that suppresses day-trade alerts in bear tape, a grade
filter (only A/A+ signals email), and CLI flags (`--run-now`, `--force`,
`--universe`). Daily schedule (ET):

| Time | Job |
|------|-----|
| 06:30 | Earnings alerts scan (T-5 / T-1 / T-0 tiers) |
| 07:00 | Pre-Market Brief email |
| every 10 min | Watchlist alert + breaking-news monitor |
| 08:00 / 08:05 | Swing + calls pre-market scans |
| 09:30 / 10:00 / 11:35 | Day-trade scans |
| 11:30 | VIX check — adds extra put scans when elevated |
| 11:40 | Puts midday scan |
| 16:30 | Full close scan (whole universe) |
| 16:45 (Fri) | Weekly scan |
| 23:30 | Nightly output cleanup |

---

## Data files

| File | Contents |
|------|----------|
| `data/watchlists.json` | Named, user-curated ticker lists (usable as scan universes) |
| `data/portfolio.csv` | Positions + watch rows (`portfolio_template.csv` = starter) |
| `data/alerts_state.json` / `alerts_log.json` | Active alert set / append-only history |
| `data/journal_trades.json` | Trade journal records |
| `data/premarket_brief.json` | Latest generated brief |
| `data/econ_calendar_cache.json` | Cached economic calendar (survives restarts) |
| `data/cache/` | Backtest daily-bar cache |
| `data/output/` | Scan CSVs, dashboards, research pages, signal logs (gitignored) |

---

## Configuration (`.env`)

| Variable | Purpose |
|----------|---------|
| `RESEND_API_KEY` | Resend API key (`re_…`) — required for any email |
| `RESEND_FROM_EMAIL` / `ALERT_EMAIL_FROM` | From-address. Use `onboarding@resend.dev` unless you've verified your own domain in Resend. Set **both** (different modules read each). |
| `ALERT_EMAIL_TO` | Recipient. In Resend sandbox mode this must be your Resend account email. |
| `ACCOUNT_SIZE`, `RISK_PER_TRADE_PCT`, `MAX_POSITION_PCT` | Fixed-risk position sizing on dashboard cards |
| `ANTHROPIC_API_KEY` | Optional — enables the dashboard's AI morning-summary polish and the Journal's AI trade review; both degrade gracefully without it |

`.env` is gitignored — **never commit real keys**. (A Resend key was once
hardcoded in `scheduler.py` and had to be rotated; everything reads from the
environment now.)

---

## Project structure

```
StockAnalysis_Version1/
├── src/stockanalysis/
│   ├── core/          # pure scoring/classification logic (metrics, grades,
│   │                  #   conviction, key levels, regime, alerts, earnings,
│   │                  #   watchlist/news monitors, brief, journal)
│   ├── scanners/      # data fetching + orchestration (scan_universe,
│   │                  #   market_movers, ai_pulse)
│   ├── backtest/      # walk-forward backtesting (data, engine, resolve, report)
│   ├── reporting/     # dashboards, research pages, portfolio, CSVs, tracker
│   ├── scheduling/    # scheduler.py — all timed jobs
│   └── webapp/        # localhost UI (app, api, views, pages, jobstore)
├── scripts/           # send_test_alert.py — end-to-end email test
├── tests/             # unittest suite, fully offline (network mocked)
├── data/              # flat-file state + generated output
├── docs/              # day_trading_prompts.md
├── legacy/            # superseded scripts, reference only
└── .env.example       # copy to .env
```

## Tests

Every module with logic has an offline test file under `tests/` (19 files) —
alerts lifecycle, earnings tiers, watchlist conditions, news classification,
premarket brief, journal math, backtest point-in-time correctness, regime,
key levels, conviction, strategy scores, and more:

```bash
python -m unittest discover tests
```

## Gotchas & regression notes

Bug classes that have bitten before — each should keep a permanent regression
test:

1. **yfinance NaN propagation** — trailing NaN rows must be forward-filled
   before computing indicators, or indicators come back NaN.
2. **`categorize()` elif-order / disqualifier edge cases** — condition order
   matters (Momentum-Pullback vs VCP overlap); RVOL must not be an
   unconditional veto (holiday sessions); institutional-quality large caps
   were once misfiled as "Avoid".
3. **`^TNX` returns the yield directly in percent** — do not divide by 10.
4. **numpy/pandas scalar types break `json.dumps()`** — cast to native
   `float`/`int` before serializing anything derived from yfinance frames.
5. **Grading math** — ATR-based stop calc, EMA labeling, and inflated A+
   grades have each regressed once; `tests/` pins the fixes.
6. **Secrets** — API keys live in `.env` only, never in source.
