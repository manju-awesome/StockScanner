# StockAnalysis

S&P 500 scanning, scoring, and dashboard/email alert pipeline.

## Structure

```
StockAnalysis/
├── src/stockanalysis/
│   ├── core/                # pure scoring/classification logic, no I/O
│   │   ├── metrics.py           (was libs/get_metrics3.py)
│   │   ├── call_candidate.py    (was libs/is_callCandidate.py)
│   │   ├── put_candidate.py     (was libs/is_putCandidate.py)
│   │   └── grade_signals.py     (was libs/grade_signals.py)
│   ├── scanners/             # orchestration: fetch + categorize + score
│   │   ├── scan_universe.py     (was tests/Scan_selected.py — main scan entrypoint)
│   │   └── market_movers.py     (was libs/market_movers.py)
│   ├── scheduling/
│   │   └── scheduler.py         (was libs/Scheduler.py — cron entrypoint)
│   └── reporting/            # output generation
│       ├── dashboard.py         (was libs/top5_dashboard.py)
│       └── csv_writer.py        (was libs/write_metrics_csv.py)
├── legacy/                   # superseded/unwired scripts, kept for reference only
├── data/output/               # generated dashboards, scan CSVs, history (gitignored)
├── tests/
├── .env.example               # copy to .env and fill in RESEND_API_KEY
├── requirements.txt
└── .gitignore
```

## Running

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in RESEND_API_KEY
python -m stockanalysis.scheduling.scheduler        # cron entrypoint
```

Run from the repo root with `src/` on `PYTHONPATH` (or `pip install -e .` if you add a
`pyproject.toml`/`setup.py` later). All internal imports use the `stockanalysis.*` package
path, so this is the only path-related thing you need to set.

## What changed from the original layout

This was reorganized by tracing the actual `import` graph rather than guessing, so it's
mapped exactly to what runs today:

**Live pipeline** (everything below is imported, directly or indirectly, by `Scheduler.py`):
`Scheduler.py → market_movers.py, Scan_selected.py → get_metrics3.py, grade_signals.py,
is_putCandidate.py, is_callCandidate.py, write_metrics_csv.py, top5_dashboard.py`

**Moved to `legacy/`** (not imported anywhere in the live pipeline — kept, not deleted):
`SA_ver2.py`, `SA_ver3.py`, `SA_ver4.py`, `get_metrics.py` (v1, superseded by v3),
`market_mover_ver1.py` (superseded by `market_movers.py`), `Signals.py` (a stale duplicate
of `grade_signals.py` — different filename, same docstring, not imported anywhere),
`send_email.py` (unused — `market_movers.py` has its own inline Resend logic),
`stock_categorizer.py` (superseded — its `categorize()` was forked directly into
`Scan_selected.py` rather than imported), `stock_categoryJune25.py`, `Scanner_sp500.py`
(earlier draft of `Scan_selected.py`), `Day_trade.py`, `premarket_gapdown.py` (standalone
scripts, run manually, not part of the scheduled pipeline), `main.py` (unused PyCharm
scaffold).

## Two things fixed during reorg — please double-check

1. **Hardcoded Resend API key removed from `scheduler.py`.** It previously sat in
   plaintext as `RESEND_API_KEY = "re_..."`. It now reads from the `RESEND_API_KEY`
   environment variable (or `.env`, loaded via `python-dotenv` if installed).
   **Since that key was shared in a chat conversation, rotate it in your Resend dashboard.**

2. **Two different, disconnected output directories became one.** The old
   `Scan_selected.py` wrote dashboards to a hardcoded absolute Windows path
   (`C:/Users/manju.thimmareddy/.../Reports`), while `Scheduler.py`'s own history CSV
   logic wrote to `libs/Reports/` — a different folder, computed relative to `libs/`.
   Both now resolve to `data/output/` relative to the project root, so scans and the
   scheduler's history log always land in the same place, and the path isn't tied to
   one machine/username anymore.

Old generated dashboards/CSVs from both original locations were preserved: the root
`Reports/` contents are in `data/output/`, and the `libs/Reports/` contents are in
`data/output/archive_libs_Reports/` for reference — safe to delete once you've confirmed
you don't need the history.

# StockAnalysis — Project Documentation

> **Note:** This document was written from project history/discussion, not by reading the live
> repository. Function names, exact parameters, and file locations should be verified against
> the current `src/stockanalysis/` code and corrected where they've drifted. Treat this as a
> starting skeleton for onboarding, not ground truth.

## What this project does

`StockAnalysis` is a Python-based stock scanning, scoring, and alerting system. It pulls price
and fundamental data (via `yfinance`), computes ~45 technical/fundamental metrics per ticker
(via `pandas_ta` and custom logic), classifies each stock into a trade category, grades
day-trade / swing-trade / options candidates, builds an HTML dashboard, and emails alerts
(via the Resend API) on a market-hours schedule.

It's built for a trader who combines CANSLIM-style fundamentals (EPS growth, RS rank,
institutional ownership) with technical setups (VCP, breakouts, pullbacks, double bottoms).

## Package layout

```
src/stockanalysis/
├── core/          # metrics computation, categorization, shared utilities
├── scanners/       # pattern/setup-specific scanners (e.g. rosputnia_scan.py)
├── scheduling/      # Scheduler.py — when/how the pipeline runs
└── reporting/       # dashboard generation, grading, market movers
```

## Core modules

### `core/metrics.py` (formerly `get_metrics3.py`)
Computes ~45 metrics per ticker: RS, RSI, BB_%B, ADX, RVOL, EMA/MA levels, ATR, pullback
ratios, institutional ownership, and more.

Known gotchas baked into this module (regression-test these):
- `pandas_ta` was removed (July 2026): it can't install on Python 3.14 (its numba pin
  supports <3.14), and its BB column renames were a recurring silent-breakage source.
  RSI/ADX/BB %B are now computed natively in this module
  (`calculate_rsi/calculate_adx/calculate_bb_pctb`, Wilder's smoothing).
- yfinance can return trailing `NaN` rows; these must be forward-filled (`ffill`) before
  computing indicators, or indicators come back NaN.
- `dict.get()` doesn't gracefully handle `None` values in a few spots — needs explicit
  `if val is None` handling rather than relying on a default.

### `core/categorize()`
Classifies a stock into one of: **Momentum Breakout, Momentum-Pullback, VCP Setup,
Turnaround, Longterm Hold, Avoid**, based on the metrics above.

Known bugs fixed here (each should have a permanent regression test):
- `elif` ordering bug caused overlap/misclassification between Momentum-Pullback and VCP
  Setup — order of conditions matters.
- RVOL blanket gate was disqualifying high-quality stocks during genuinely low-volume
  sessions (e.g. holidays) — RVOL threshold shouldn't be an unconditional veto.
- Category logic incorrectly classified some quality names (e.g. ARM) as "Avoid" — check
  the disqualifier conditions for false positives on institutional-quality large caps.

### `core/call_candidate.py` / `core/put_candidate.py`
Scoring modules for options candidates, with hard disqualifiers (conditions that force
a "no" regardless of score).

## Scanners (`scanners/`)

### `scanners/rosputnia_scan.py`
Newest scanner. Uses an 18-day EMA trend filter combined with double-bottom pattern
recognition to flag setups.

## Reporting (`reporting/`)

### `reporting/grade_signals.py`
Generates A/B/C grades with entry/stop/target price levels. Previously had bugs around
inflated A+ scores, incorrect ATR-based stop calculation, and a mislabeled EMA — verify
these are still fixed if grading logic changes.

### `reporting/top5_dashboard.py`
Generates a self-contained HTML dashboard showing Top 5 stocks across four buckets: Day
Trade, Swing Trade, Calls, Puts.

### `reporting/market_movers.py`
Pulls pre-market and live top movers, with catalyst detection via regex against Yahoo
Finance headlines.

## Scheduling (`scheduling/Scheduler.py`)

- VIX-adaptive scheduling (adjusts run frequency/behavior based on market volatility).
- Market health gating (skips or modifies runs under certain market conditions).
- CLI flags: `--run-now`, `--force`, `--universe`.
- Hot ticker pipeline: merges live movers with a static `DAY_TRADE_TICKERS` list.
- Ticker universe system: four predefined lists, selectable via `--universe`.
- **Security note:** a Resend API key was previously hardcoded in this file and needed
  rotation. Confirm the key is now read from an environment variable
  (e.g. `os.environ["RESEND_API_KEY"]`), not committed to source.

## Alerts

Email alerts are sent via the Resend API to `mthimmareddy99@gmail.com`.

## Environment / setup notes

- Runs in PyCharm on Windows, Python 3.13.
- A two-interpreter issue was previously hit on Windows (pip installing packages into the
  wrong Python environment) — if dependencies seem "missing" despite being installed,
  check `pip --version` / interpreter path first.
- Output directories were unified under `data/output/` (previously scattered/hardcoded
  paths across modules).

## Known bug classes worth permanent regression tests

1. `pandas_ta` column renames breaking downstream indicator references.
2. NaN propagation from yfinance trailing rows.
3. `categorize()` elif-order and disqualifier edge cases (see ARM example).
4. `UnboundLocalError` on globals like `DAY_TRADE_TICKERS` (missing `global` declaration).
5. Dashboard scoring math (ATR stop calc, EMA labeling, inflated grades).
6. Hardcoded secrets (API keys) — verify these are always pulled from environment/config,
   never committed.

## Suggested next steps for future contributors

- Add a `tests/` directory mirroring this package structure (see prior discussion on
  pytest setup — synthetic OHLCV fixtures, mocked `yfinance.download`, mocked
  `resend.Emails.send`).
- Pin one regression test per bug listed above so fixes can't silently regress.
- Consider a `CHANGELOG.md` alongside this file for tracking metric/scoring changes,
  since scoring logic changes affect which stocks get flagged historically.
