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
# First run prints a generated username/password once — save it.
python src/stockanalysis/webapp/app.py

# Change that password (or add a user) any time
python src/stockanalysis/webapp/app.py --set-password

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

`webapp/app.py` serves nine pages (stdlib `http.server`, background jobs as
daemon threads via `jobstore.py`, page HTML in `pages.py`/`views.py`, job and
JSON-endpoint logic in `api.py`). Binds `127.0.0.1` only — don't expose it
beyond localhost.

Every page, API endpoint and generated file under `data/output/` sits behind a
login (`auth.py`): PBKDF2-SHA256 password hashes in `data/users.json`,
in-memory sessions in an HttpOnly `SameSite=Lax` cookie, 12-hour idle timeout,
and a 5-minute lockout after five failed attempts. The first start creates an
account and prints the generated password once — it is not recoverable
afterwards, so use `--set-password` to reset. `--no-auth` serves without the
gate. The cookie omits `Secure` because this is plain HTTP on loopback; turn
it on in `auth.cookie_header()` before putting the app behind TLS.

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
Add/edit positions from the page via a modal form. Below the holdings table,
an **Options** card lists open contracts from `data/options_positions.csv`
(strike/expiry, contracts, premium, P&L, days-to-expiry warnings) — see
**Broker sync** below.

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

### ⚡ Day Trade (`/daytrade`)
The SPY 0DTE options day-trader — the one section not backed by
`stockanalysis.*`. See **Day Trade** below.

### ⚙️ Automation (`/automation`)
Scheduler status and job history: what ran, when, how long it took, what
failed — the web view onto `jobstore`'s bounded job history.

---

## Day Trade — `src/spydaytrader/`

An independent second engine sharing this app: a Python translation of the
`SVMKR_UT_HMA_ORB` TradingView Pine Script (kept at `docs/pine/`) driving a
human-approved SPY 0DTE options pipeline on Robinhood. It was previously its
own project on port 8900; it now runs inside the workstation.

**"Independent" is literal.** `spydaytrader.core` imports nothing from
`stockanalysis.core` and vice versa — the swing scanner and the day-trader
share this server, the page layout (`spydaytrader/webapp/pages.py` renders
through `stockanalysis.webapp.views`) and the market-regime scorer, and
nothing else. In particular they do **not** share state: SPY's proposals and
journal live under `data/spy/`, because `data/trade_proposals.json` and
`data/journal_trades.json` already belong to the swing side with incompatible
schemas (share-based vs. options).

| Piece | What it does |
|---|---|
| `core/` | Pure logic: indicators, signal engine, signal dedup, proposal lifecycle, position sizing. No network, no broker. |
| `daemon/scheduler.py` | Polls SPY every 30s during market hours, runs the signal engine, writes proposals. Runs on a background thread in the webapp (`--no-spy-daemon` to disable), or standalone. |
| `webapp/pages.py` | The `/daytrade` and `/daytrade/proposals` page bodies. |
| `scripts/spy_prepare_order.py` | All order arithmetic and disk writes for placement. |
| `scripts/spy_check_premium_exits.py` | The premium stop/target half of the exit rule. |

### Order placement is deliberately unreachable from here

The daemon and the dashboard can move a proposal to `approved` and no further,
so a misclick on a web page can never spend money. Reaching `placed` happens
only in a Claude Code session via the Robinhood MCP tools, with an explicit
per-order confirmation — see the `spy-place-approved-trade` skill. Don't add an
auto-place path; the split *is* the safety model.

### Exit rule: two halves, two runtimes

"Whichever comes first" — underlying signal flip, or premium stop/target
(−35%/+60%). Different data, so different processes:

| Half | Checked by | Data source |
|---|---|---|
| UT Bot signal flip | the daemon | yfinance SPY bars |
| Premium stop/target | `spy-premium-exit-check` scheduled task | Robinhood connector |

The daemon has no broker session and cannot see option premiums, hence the
split. Both halves only ever write a `pending_review` exit proposal; neither
closes a position.

> ⚠️ **This is not a hard stop-loss.** The scheduled task only runs while the
> Claude app is open, so a five-minute polling loop is a monitoring aid, not a
> guaranteed stop. On a fast 0DTE move the premium can travel far past −35%
> between checks. For a stop you can rely on, place a broker-side stop order
> with Robinhood at entry time and treat the task as a notifier on top of it.

---

## Day-trade scanner — `core/daytrade/` · page `/stockdaytrade`

An intraday momentum engine: unusual volume, a fresh catalyst, a defined
structure to risk against, and — separately — whether the price in front of
you is worth paying. It reads **no fundamentals at all** — no EPS, ROE,
valuation or fair value — because none of them bear on the next ninety
minutes. A stock can be `AVOID` in `core/longterm` and enterable here
without either being wrong; they answer different questions.

```bash
python -m stockanalysis.scanners.scan_daytrade                   # today's movers
python -m stockanalysis.scanners.scan_daytrade --profile large   # megacap calibration
python -m stockanalysis.scanners.scan_daytrade --limit 40 --scorecards
python -m stockanalysis.scanners.scan_daytrade --tickers RCEL VATE --profile auto
python -m stockanalysis.scanners.scan_daytrade --at-time 10:15   # as-of replay
python -m stockanalysis.scanners.scan_daytrade --save            # §16 table + JSON
```

### One engine, three market-cap profiles — `profiles.py`

The pipeline is identical for a $200M biotech and a $2T megacap; what the
numbers *mean* is not. RVOL 1.3 is noise on a small-cap runner and a real
institutional footprint on a megacap; a 6M float is the whole thesis on the
first and an irrelevant fact about the second. So thresholds and weights
live in a profile the engine reads (`small` / `mid` / `large`, or `auto` to
judge each name against its own cap band) rather than as constants across
eight modules.

Three shifts, all in the same direction as market cap: **scarcity gives way
to participation** (supply 15 → 5, float stops being a confirmation and
becomes a liquidity check), **the stock gives way to its context**
(relative strength + regime 15 → 45, because a megacap fighting QQQ, its
sector and SPY is fighting all three), and **volatility stops being the
point** (ATR% bar 4.0 → 1.5). Weights sum to 100 in every profile and cover
the same eight blocks, so the arithmetic is identical and only the
calibration differs — but scores are never comparable *across* profiles,
which is why the profile travels with each row.

### Opportunity, setup, entry — three questions, not one

The engine's central distinction: *is this stock moving* and *is this a
good trade at this price* are independent, and the second decays through
the session while the first holds still.

| Score | Answers | Stability |
|---|---|---|
| **CScore** (confluence) | is this stock worth trading today | stable all session |
| **Setup** | is there a valid structure | changes with structure |
| **Entry** (§20) | should I buy *at this price, now* | decays every bar |
| **Tradeability** | can I get in and out | a gate, not a weight |

`entry.py` measures extension in 5-minute ATRs — the same unit the stop is
denominated in, so they compare directly — and adds a **Chase Score**: six
independent ways of being late, counted rather than averaged, because being
2 ATR above VWAP is disqualifying on its own and blending it against five
healthy readings is exactly how a chase gets rationalised.

**The execution gate (§2).** A+ requires *every* execution condition to
hold: spread, room ≥ 0.5× the expected move, R:R ≥ 2, stop within 1.5× the
5-min ATR, dollar volume, entry not extended, and liquidity sufficient for
the position. Fail one and the label becomes `SETUP OK — WAIT FOR BETTER
ENTRY`; unmeasurable ones are reported apart as `EXECUTION UNVERIFIED`,
since on a real order "unknown" and "bad" have the same consequence.

Every column on the page sorts — click to sort descending, click again to
reverse, and the `#` column restores the engine's own ranking. Cells carry
their raw value rather than their formatted text (or `$9.4M` would sort
above `$215.2M`), blanks stay last in *both* directions because unknown is
not zero, and Action sorts by urgency rather than alphabetically. The rank
and ticker columns freeze to the left, so scrolling right to read R:R or
Chase never leaves you looking at a row of numbers without knowing which
stock they belong to.

**The action, not the grade, is the headline.** `🔥 ENTER NOW`,
`🟢 WAIT FOR BREAKOUT`, `🟢 WAIT FOR PULLBACK`, `🟡 SETUP OK — WAIT`,
`🟠 MISSED ENTRY — DO NOT CHASE`, `🟠 EXTENDED`, `🔴 AVOID`. Rows rank by
actionability first, so a 92-confluence name you must not chase sits below
an 80 you can enter. A setup whose trigger has already fired and run can
never read as a fresh entry.

The universe is not a fixed list — today's candidates were on no list
yesterday — so `datafeed.screen_movers()` uses `yf.screen()` to filter the
whole US market server-side by market cap, price, volume and % change, in
both directions. Everything downstream is a pure function of bars already
in memory.

The page can also run a **watchlist** instead of the screen (the `daytrade`
list leads the picker), which pairs with the `auto` profile since a
watchlist is usually mixed-cap. A list is pruned to `limit` *after* bars
are fetched and before the per-ticker `.info`/news pass — bars are batched
and effectively free, so a 476-name list costs one round of downloads
rather than 476 throttled requests.

**Three numbers, and two of them are gates.** Confluence is the §10
weighted 100 (volatility 20 · float/supply 15 · catalyst 15 · volume 15 ·
setup 25 · market 10). Setup and tradeability are reported separately, and
tradeability is a *gate*: an unexitable position is not improved by a
better chart, so a failure there caps the result at `WATCH — NOT TRADEABLE`
whatever the other 90 points say. Room-to-run gates the same way —
significant resistance immediately ahead refuses A+/A rather than
subtracting from it. Grades additionally require a count of independent
confirmations, because 85 points from two huge factors and 85 from eight
modest ones are the same number and very different trades.

**Sizing is risk-first, and size never scales with score.** Maximum
acceptable loss sets the share count; every other limit can only reduce it
(allocation cap, 1% of average volume, 25% of a minute's dollar volume, a
micro-float floor). Real risk is `|entry − stop| + slippage`, where
slippage is half the spread each way, an intraday-volatility floor, and
market impact once the position exceeds ~10% of a minute's dollar volume —
so the account's true exposure is not understated. Execution risk shrinks
the position rather than only printing a warning beside a full-size one.
Capital and the allocation cap come from `data/risk_settings.json`;
day-trade risk % is its own knob, since 2% on an intraday breakout is not
the same bet as 2% on a swing.

**What it refuses to estimate.** Borrow fee and shares-available-to-borrow
have no source. Offerings, ATM programmes, warrants and reverse-split risk
are not verifiable from yfinance, so every candidate carries an explicit
`DILUTION: UNVERIFIED` note rather than an implied all-clear — check the
latest S-1/S-3/424B5 before sizing. Market breadth (advance/decline) is
reported as unavailable rather than proxied by "SPY is up", which is not
breadth. Bid/ask spread is only scored while the market is open, because
outside hours the resting quotes are meaningless (AVITA showed 5.71 × 8.96,
a 44% "spread") and scoring them would reject every candidate on a
fabricated basis.

**Sessions.** Outside market hours the scan analyses the last *completed*
session and says so on its first line — every level is that session's, and
it is preparation, not a live scan. `--at-time HH:MM` replays a session as
of a wall-clock time with nothing later visible, which is the only way to
see the engine's real behaviour when the market is shut: read at the close,
every candidate has already made its move and correctly scores a poor R:R.

---

## Future Compounders — `core/compounder/` · page `/compounder`

Finds companies that could become 5–10 year market leaders **before** their
growth is fully recognised. Twelve steps ending in one `FUTURE COMPOUNDER
SCORE` and a 20-name 10-Year Watchlist.

```bash
python -m stockanalysis.core.compounder.scan                 # full library
python -m stockanalysis.core.compounder.scan --tickers ALAB RKLB   # merges
python -m stockanalysis.core.compounder.scan --theme nuclear
python -m stockanalysis.core.compounder.scan --list-themes
```

**This is the one engine that weights instead of gating, and the exception
is deliberate.** Everywhere else in this project a gate encodes a condition
that cannot be traded away — no chart pattern compensates for a
deteriorating business. That is right for a buy decision and wrong here,
because every candidate in this population fails something today. Gate on
negative FCF, no profit, thin coverage or small size and the survivors are
large-cap quality names, which is `/longterm`'s job. So the brief's
instruction — "instead classify the risk" — is taken literally: every
condition a conventional screen would reject on appears as a labelled risk
flag on the row, and the ranking continues.

**Weights** (§12): secular TAM 20, growth acceleration 15, moat formation
15, market-share opportunity 10, operating leverage 10, reinvestment 10,
competitive position 5, management 5, survivability 5, discovery 5.

**There is no valuation term**, deliberately. Paying 14× sales for a company
that becomes a $50B business works out and 4× for one that doesn't, does
not. Price and entry belong to `/longterm`, and the page says so.

### TAM is a curated claim, never a generated number

Steps 1–2 need a TAM curve, and no filing contains one. The tempting
shortcut — have the model estimate a TAM per company at scan time — is the
one thing `themes.py` exists to prevent: TAM carries 20% of the composite
and market-share opportunity another 10%, so a fabricated figure would
drive 30% of the ranking, unfalsifiably and differently on every run.

Instead TAM lives per **theme**, versioned in git with a source basis, an
`as_of` date and a confidence level. Confidence applies twice — a
proportional discount (`×0.65` for LOW) and a hard ceiling — so a
speculative market's arithmetic cannot outrank a measured one. Quantum
computing scores 60 raw on pure TAM CAGR against semiconductor equipment's
49; after the discount and the 45 ceiling it lands below it, which is the
honest ordering.

`THEME_MEMBERS` is also the **universe**: a ticker is in the scan *because*
a structural trend was identified for it, which is what Step 1 asks and the
reverse of ranking whatever sits in an index. Market cap ($300M–$20B) is
classified live, not filtered — a name that compounded past $20B is
labelled `GRADUATED`, because that is the engine's success case, not an
error.

### Acceleration, not history

The framework's sharpest instruction is to reward *accelerating* growth
rather than high historical growth, and the two rank very differently. A
company compounding 45% for four years and fading to 30% has a great past;
one that went 12% → 19% → 34% has a worse history and is the one that
becomes a leader.

Acceleration is always a year-on-year rate against **another year-on-year
rate**. Comparing a spot rate to a multi-year CAGR — the obvious
implementation — is broken in a way that is easy to miss: a CAGR off a
small base is enormous ($20M → $850M in three years compounds at 155%), so
every early hyper-growth name gets stamped "decelerating", inverting the
ranking exactly where it matters most. Fade from an unsustainable base is
reported as `FADING FROM A HIGH BASE` rather than filed with a company
going 14% → 4%.

### What is measured, and what is not

Measured from filings and prices: growth and its second derivative, margin
trends, the revenue-vs-opex leverage ratio, R&D and capex *productivity*,
share-count dilution, cash runway, insider open-market buying, analyst
coverage drift, institutional ownership, relative strength.

**Not available, and never proxied:** backlog growth, customer counts,
guidance-versus-actual history, acquisition returns, patent portfolios and
third-party market-share data. Each is named in the output's `unmeasured`
list and printed on the card, because a reader who does not know backlog
was never checked will assume it was.

Intensity metrics are **plateaus, not ladders**. A pre-revenue company
spending 300% of revenue on R&D scores near zero on intensity, not 100 —
on a monotonic scale it would top the reinvestment leg despite research
productivity of 0.33×, which is precisely the "spending growth without
corresponding revenue growth" the framework says to penalise.

### Stage is not a band on the score

`stage.py` classifies from thresholds on revenue scale, growth, margin state
and cash generation — never from the composite. If stage were a band it
would say nothing the score did not. Kept independent, a company can be
Stage 2 with an excellent score or Stage 4 with a poor one, and those two
cells are where the interesting names live. Each row carries the *named
conditions* to advance, with the live value beside the threshold ("revenue
reaches $250M — now $118M").

Every narrative line on the card is built from a computed field. Free prose
about a small-cap reads well, sounds confident and cannot be checked, which
on a ten-year holding list is worse than no prose at all.

### Sorting and filtering the page

Both tables sort on any column (click the header, click again to reverse)
and filter on six facets — stage, theme, funding class, tier, discovery
state and cap band — plus free text, a minimum score and a "hide names with
material risks" toggle. All client-side: the page is a stored snapshot, so
re-sorting rearranges data the browser already has.

Two behaviours are deliberate. **Facet options are built from the values
present in the rows shown**, with counts, so a dropdown never offers a
choice that returns an empty table. And **a value that was never measured
sorts to the bottom in both directions** rather than as zero — the same rule
the scoring engine follows, and the reason an unreported gross margin must
not rank as the worst gross margin.

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

### How the dashboard's Top-5 scores are decided

Every card in the scanner HTML carries a number badge and a letter grade. This
section is the full derivation of the three most-asked-about sections —
**Long-Term**, **Puts**, and **Swing** — because the number on a card does not
come from the same place for all three.

#### First: there are two different score families

This is the single most common source of confusion when reading the report.

| Family | Range | Computed in | Used by |
|--------|-------|-------------|---------|
| **Strategy scores** — `Investment_Score`, `Swing_Score`, `DayTrade_Score` | 0–100, bounded, each with a `*_Pass` flag and a `*_Reason` string | `core/strategy_scores.py` (`score_investment` / `score_swing` / `score_day_trade`) | The **CSV** columns, the Research Library, alerts |
| **Card ranks** — `day_card_rank`, `swing_card_rank` | unbounded additive (~0–125 in practice), `-999` = not rankable | `core/strategy_scores.py`, bottom section | The **dashboard Top-5 cards** for Day Trade and Swing |

So the badge on a Swing card — the "Score 113" — is **`swing_card_rank`, not
`Swing_Score`**. They use overlapping inputs but different weights and different
scales, and a name can rank high on one and middling on the other. The Long-Term
and Puts cards, by contrast, badge the strategy/candidate score directly:

| Card section | Badge number is | Source |
|--------------|-----------------|--------|
| Day Trade | `day_card_rank(row)` | unbounded card rank |
| Swing Trade | `swing_card_rank(row)` | unbounded card rank |
| Long-Term | `Investment_Score` | 0–100 strategy score |
| Calls | `Call_Score` | `core/call_candidate.py` |
| Puts | `Put_Score` | `core/put_candidate.py` |

#### The entry gate is the kill switch for three of the five sections

`core/metrics.py` step 6 sets `Entry_Gate_Pass` by testing four conditions.
Any failure records the reason in `Entry_Gate_Reason`:

| Check | Threshold |
|-------|-----------|
| Market cap | ≥ $1B |
| Price | ≥ $5 |
| ADX(14) | ≥ 15 — skipped entirely when ADX is `None` |
| Price vs 200MA | not more than 30% below |

An RVOL ≥ 0.6 gate exists in the source but is **currently commented out**
(`metrics.py:794-797`), so low-volume names are not gated today.

Failing the gate forces `Swing_Score`, `DayTrade_Score`, `day_card_rank`,
`swing_card_rank`, `_call_score` and `_put_score` to zero or `-999` — those
sections have no tradeable plan without it. `Investment_Score` deliberately
**survives** a gate failure: a 6–24 month accumulation does not need a trend
already in place, so a flat-ADX name can still be a long-term buy.

#### Long-Term scoring (`score_investment`)

Two independent things decide whether a name appears on a Long-Term card.

**1. `Investment_Score`, 0–100 additive.** Eight buckets, best matching tier wins
within each bucket; missing data simply forfeits that bucket's points:

| Bucket | Points |
|--------|--------|
| Leadership — `RS_Rank` | 25 if > 80 · 15 if ≥ 60 · 8 if ≥ 40 |
| Earnings growth — `EPS_Growth%` | 20 if > 25% · 12 if > 15% · 5 if > 0 |
| Revenue growth — `Revenue` | 15 if > 20% · 9 if > 10% · 4 if > 0 |
| Stage — `Above_200MA` | 10 |
| Cash generation — `FCF_Positive` | 10 |
| Execution — `EarningsBeat` | 10 |
| Sponsorship level — `Inst_Own%` ≥ 40 | 5 |
| Sponsorship trend — `Inst_Own_Chg` > 0 | 5 |

**2. `Investment_Pass` — all six primary filters, hard AND.** `RS_Rank` > 80,
EPS growth > 25%, revenue growth > 20%, above the 200MA, FCF positive, last
earnings a beat. `_longterm_score()` returns `-999` for anything without the
pass flag, so **a high-scoring name that fails one filter never reaches a
card** — it stays in the CSV for review. That is why the section is titled
"all filters pass".

*Consequence worth knowing:* the six pass filters are worth exactly 90 of the
100 points, so every Long-Term card scores **90–100** and only the two 5-point
sponsorship bonuses move it. Against the grade bands below that means Long-Term
cards can only ever render **A** (90–94) or **A+** (95+) — the B+/B/C bands are
unreachable for this section by construction.

**`RS_Rank` is derived, not raw.** The scan's `RS` column is raw 3-month excess
return vs QQQ in percentage points, so "RS > 80" cannot be applied to it
directly. `attach_rs_rank()` converts it to a 0–99 **percentile within the
scanned universe**. This makes `RS_Rank` relative to what you scanned: the same
ticker gets a different rank in an S&P 500 run than in a 30-name watchlist run.
Below 20 tickers a percentile is noise, so a fixed absolute mapping of excess
return is used instead (`_rs_rank_fallback`).

**`LT_Entry_Timing`** is attached alongside and answers a different question —
not "is this worth owning" but "is now a good time to start" (below 200MA / base
forming / extended +50% vs 200MA → tranches only). It is advisory text, never a
filter. **`Buy_Zone_Score`** (`core/buy_zone.py`) is a third, separate axis: an
8-factor weighted blend (30% fundamental quality, 20% technical trend, 15%
valuation, 10% pullback depth, 10% volume accumulation, 5% each institutional /
RS / catalysts) that renormalizes over whatever factors have data and returns
`None` below 50% weight coverage rather than guessing.

#### Put options scoring (`compute_put_candidate`)

Puts are scored as an **exhaustion/fade screen**, not a downtrend screen — it
looks for strong names running out of buyers, not names already broken.

**Hard disqualifiers run first.** Either one forces `Put_Score = 0`,
`Put_Candidate = False` and a `DISQUALIFIED:` reason, regardless of any signal:

- `ADX > 38` **and** `RS > 50` — trend too strong; overbought can persist for weeks
- `RS > 80` — institutional accumulation, do not fade strength

**Then five signals accumulate a small integer score:**

| Signal | Points |
|--------|--------|
| Near 52W high set within 10 days (`Dist_52W_High%` ≥ −8) — parabolic | +2 |
| Price > 5% above 8EMA — extended | +2 |
| Price 3–5% above 8EMA | +1 |
| `RSI_14` > 72 — overbought | +3 |
| `RSI_14` 68–72 — stretched | +2 |
| `RVOL` < 0.7 — buyers exhausted (the strongest single signal) | +3 |
| `RVOL` 0.7–0.9 — volume fading | +2 |
| `Vol_vs_20D` < 0.9 — below 20-day average (only if RVOL ≥ 0.9) | +1 |
| `BB_PctB` > 1.0 — closed above the upper band | +2 |
| `BB_PctB` 0.85–1.0 — approaching upper band | +1 |
| `ADX_14` > 35 — strong trend, soft penalty | **−1** |

`Put_Candidate` is `True` at **score ≥ 5**. `_put_score()` returns `-1.0` for
non-candidates and the Top-5 filter keeps only scores > 0, so **only confirmed
candidates ever render a Put card**. Practical range on a card is 5–12.

*Quirk worth knowing:* put candidates must also pass the same long-biased
`Entry_Gate_Pass`. A stock more than 30% below its 200MA, or with ADX < 15,
is gate-failed and can never surface as a put — even though those are exactly
the conditions a bearish screen might want. Combined with the ADX > 38
disqualifier and the ADX > 35 penalty, the workable band is roughly ADX 15–35.
This is intentional for a fade-the-exhaustion strategy but it means **this
screen will not find you puts in a bear market**.

#### Swing trade scoring

The dashboard card ranks by **`swing_card_rank`** — unbounded, `-999` when the
entry gate failed or the category is `Avoid`:

| Component | Points |
|-----------|--------|
| Category | Momentum-Pullback 30 · VCP Setup 28 · Momentum 20 · Turnaround 10 |
| `RS` (raw excess vs QQQ) | ≥50 → 20 · ≥20 → 14 · ≥0 → 8 · ≥−10 → 2 · else **−8** |
| Bollinger coil `BB_PctB` | ≤0.1 → 18 · ≤0.2 → 14 · ≤0.3 → 10 · ≤0.4 → 6 |
| `ATR Shrinking` | 12 |
| Pullback volume `Pullback_Vol_Ratio` | ≤0.6 → 10 · ≤0.8 → 7 · ≤1.0 → 4 |
| `Above_200MA` | 8 |
| `VolumeDryingUp` | 6 |
| `RSI_14` | 30–50 → 8 (oversold bouncing) · 50–65 → 5 (healthy) |
| Near 50MA `Price_vs_50MA%` | −5..+5 → 8 · −15..−5 → 4 |
| `EarningsBeat` | 5 |
| ATR penalty | > 12% → **−8** · > 8% → **−3** |

Note this rewards *contraction*, not strength: the best swing card is a quality
name coiling on drying volume, not the one making the biggest move.

The separate 0–100 **`Swing_Score`** in the CSV uses different weights — setup
category 20, R:R to T2 20 (`RR_T2` ≥ 3), ATR shrinking 10, RSI 40–60 15,
`BB_PctB` 15, pullback volume 10, plus 5 each for above-200MA and `RS_Rank` ≥ 60
— and its `Swing_Pass` flag is a hard AND of five primary filters: setup is
Momentum-Pullback or VCP, `RR_T2` ≥ 3, ATR shrinking, RSI in 40–60, `BB_%B` < 0.4.
Unlike Long-Term, `Swing_Pass` is **not** required to render a Swing card.

#### Grade bands

`_score_to_grade()` in `reporting/dashboard.py` maps the badge number to a
letter. Because the scales differ per section, so do the cutoffs:

| Section | A+ | A | B+ | B | C |
|---------|----|---|----|---|---|
| Day Trade | 80 | 65 | 50 | 35 | 20 |
| Swing Trade | 85 | 70 | 55 | 40 | 20 |
| Long-Term | 95 | 85 | 75 | 65 | 40 |
| Calls | 18 | 14 | 10 | 6 | 1 |
| Puts | 8 | 6 | 4 | 2 | 1 |

Anything below the C cutoff grades D. Top-5 selection additionally requires
score > 20 for Day and Swing, and > 0 for Long-Term, Calls and Puts — so a
section renders fewer than five cards, or none, when the tape does not offer
them. That is the intended behaviour, not a bug.

#### What the regime does — and does not — change

`core/market_regime.py` classifies the day and sets per-horizon multipliers
(Bullish 1.0/1.0/1.0, Neutral day 0.5 / swing 0.75 / longterm 1.0, Defensive
0.25/0.5/0.5). These scale the **position size** on the R:R·SIZE line of each
card, never the score or the grade. Options sections ride the swing horizon.
When no regime data is available the source is `"none"` and multipliers stay at
1.0 — an unknown tape must not silently shrink your sizing.

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
| `data/portfolio.csv` | Equity positions + watch rows (`portfolio_template.csv` = starter) |
| `data/options_positions.csv` | Open option contracts (written by the broker sync) |
| `data/backups/` | Timestamped CSV backups taken before every broker sync |
| `data/alerts_state.json` / `alerts_log.json` | Active alert set / append-only history |
| `data/journal_trades.json` | Trade journal records |
| `data/premarket_brief.json` | Latest generated brief |
| `data/econ_calendar_cache.json` | Cached economic calendar (survives restarts) |
| `data/cache/` | Backtest daily-bar cache |
| `data/output/` | Scan CSVs, dashboards, research pages, signal logs (gitignored) |

---

## Broker sync — `core/broker_sync.py`

Pulls real holdings from Robinhood into `data/portfolio.csv` and
`data/options_positions.csv`. Driven by the **`get-portfolio` skill**, which
fetches through the Robinhood MCP tools and pipes the payloads into
`scripts/sync_broker_positions.py`. Read-only against the broker — this path
cannot place, modify or cancel an order.

```bash
python3 scripts/sync_broker_positions.py show        # current local state
python3 scripts/sync_broker_positions.py sync --dry-run \
    --equities @equities.json --options @options.json \
    --premiums '{"SPY260725C00601000": 1.23}'
```

**The merge is deliberately conservative**, because `portfolio.csv` is
hand-maintained state (strategies, stops, targets, notes, and the
`Target_Weight`/`Theme` columns behind the allocation plan) and a broker knows
none of it:

- writes **only** `Shares` and `Avg_Cost`; never touches Strategy, Stop,
  Target, Notes, Entry_Date or any hand-added column;
- **never deletes a row.** A holding the broker stops reporting is zeroed and
  marked `closed at broker`, keeping its notes as a watchlist row;
- **never touches rows it doesn't own.** Ownership is tracked in a `Source`
  column — without it the sync couldn't tell "sold it" from "it's on my
  watchlist and I never held it", and would zero out watchlist rows every run.

Bookkeeping columns (`Source`, `Last_Synced`, `Account`) ride in the `_extra`
dict rather than `POSITION_FIELDS`, so the webapp's Edit Position form —
which doesn't know about them — round-trips them instead of blanking them.

Every write is preceded by a timestamped backup in `data/backups/`. New rows
default to `Strategy=longterm` and are listed under `needs_strategy` in the
report, as is any watchlist row that just became a real holding — Strategy
selects which alerts fire on real money, so the choice gets reviewed rather
than silently inherited.

Options live in their own file because `portfolio.csv`'s contract is "one row
= one equity ticker": `build_portfolio_view()` joins to the day's scan by
ticker and values rows as `price × Shares`, which for an option means no scan
match, no ×100 multiplier and no expiry — a silently wrong number in
`portfolio_totals()`. See `reporting/options_positions.py`.

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
│   │   ├── longterm/   # the four-gate long-term buy engine
│   │   ├── shortside/  # both directions scored on one page
│   │   ├── csp/        # cash-secured puts over the longterm verdict
│   │   ├── daytrade/   # gates-not-weights intraday equity scanner
│   │   └── compounder/ # 10-year emerging-leader engine + theme/TAM library
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
