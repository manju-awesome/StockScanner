"""
library.py — a research library the tests own end to end
========================================================
The Long-Term engine scores scan rows. Both halves of this suite need rows
whose verdict is known before the test runs, so the assertions can be about
the page rather than about whatever the last real scan happened to produce:

  * the backend HTTP tests write these rows to a throwaway
    data/output/research_index.json that the test server reads;
  * the in-process tests hand the same rows straight to api.longterm().

The baseline row is the one from tests/test_longterm_engine.py — an elite
company at a clean 8/21 EMA pullback with confirmation, the single shape that
reaches BUY NOW. Keeping it identical is deliberate: if the engine's
requirements move, both suites fail for the same reason instead of this one
quietly scoring something else.

Nothing here touches the network, and nothing here writes outside the tmp
directory it is handed.
"""

from __future__ import annotations

import json
from pathlib import Path

ELITE = "ELIT"          # -> BUY NOW, gate "confirmed"
OVERVALUED = "OVER"     # elite business, price demands more -> stopped at valuation
WEAK = "WEAK"           # gutted business -> AVOID, gate "quality"

INDEX_FILENAME = "research_index.json"


def scan_row(ticker: str = ELITE, **over) -> dict:
    """An elite company at a clean 8/21 EMA pullback with confirmation.

    Every fixture below mutates one thing about this row, so a test that
    fails names the input it changed.
    """
    row = {
        "Ticker": ticker, "LongName": f"{ticker} Corp",
        "Sector": "Technology", "Industry": "Software - Infrastructure",
        "Current Price": 100.0,
        # fundamentals -> LQuality well above the 85 the EMA zone needs
        "Revenue": 25.0, "EPS_Growth%": 30.0, "ReturnOnEquity%": 30.0,
        "OperatingMargin%": 30.0, "GrossMargin%": 70.0, "FCF_Margin%": 25.0,
        "FCF_Positive": True, "DebtToEquity": 20.0, "CurrentRatio": 3.0,
        "TotalCash": 5e10, "TotalDebt": 1e10, "Inst_Own%": 65.0,
        "Inst_Own_Chg": 1.5, "FreeCashFlow": 2.0e10, "NetIncome": 2.0e10,
        "SharesOutstanding": 1.0e9, "Beta": 1.0, "MarketCap": 1.0e11,
        "FCF_CAGR%": 20.0, "FCF_Years": 4, "FCF_Positive_Years": 4,
        "Revenue_CAGR%": 20.0, "OperatingMargin_Trend_pp": 2.0,
        # trend: intact and rising
        "8EMA": 99.0, "21EMA": 98.5, "50MA": 92.0, "200MA": 80.0,
        "Above_200MA": True, "Price_vs_50MA%": 8.7, "Price_vs_200MA%": 25.0,
        "Pct_vs_8EMA": 1.0, "MA50_Slope%": 2.0, "MA200_Slope%": 1.5,
        "ATR_Pct": 2.0, "RSI_14": 55.0, "Dist_52W_High%": -8.0,
        "52W High": 108.0, "Prior_Breakout_Level": 99.2,
        # volume + confirmation
        "Vol_vs_20D": 0.7, "Pullback_Vol_Ratio": 0.7, "VolumeDryingUp": True,
        "Distribution_Days_25d": 1, "Reversal_Candle": "bullish engulfing",
        "RVOL": 1.3, "RS_Rank": 85.0, "RS": 8.0,
        "Days_To_Earnings": 40,
    }
    row.update(over)
    return row


def overvalued_row() -> dict:
    """The same elite business, priced for a hundred times the cash it makes.
    Reaches the valuation gate and stops there."""
    return scan_row(OVERVALUED, FreeCashFlow=2.0e8, NetIncome=2.0e8)


def weak_row() -> dict:
    """A perfect chart on a company with no business behind it. The quality
    gate is first, so the chart is never read."""
    return scan_row(
        WEAK, FCF_Positive=False, DebtToEquity=400.0, CurrentRatio=0.6,
        TotalCash=1e8, TotalDebt=5e10, OperatingMargin_Trend_pp=-8.0,
        FreeCashFlow=-1.0e9,
        **{"Revenue": -5.0, "EPS_Growth%": -20.0, "ReturnOnEquity%": 2.0,
           "OperatingMargin%": 1.0, "GrossMargin%": 8.0, "FCF_Margin%": -5.0,
           "FCF_CAGR%": -30.0})


def default_rows() -> list[dict]:
    """One name per verdict the page has to be able to show."""
    return [scan_row(), overvalued_row(), weak_row()]


def entry(row: dict) -> dict:
    """A research-index entry wrapping a scan row.

    `price` is set because core.research_snapshot.has_quote() is what
    separates "scanned and rejected" from "never fetched", and a library
    whose entries have no quote renders as the second — every row dropped,
    every assertion below vacuously true.
    """
    return {
        "ticker": row["Ticker"],
        "name": row.get("LongName"),
        "sector": row.get("Sector"),
        "price": row.get("Current Price"),
        "market_cap": row.get("MarketCap"),
        "raw": dict(row),
    }


def entries(rows: list[dict] | None = None) -> list[dict]:
    """The shape api._longterm_entries() returns, for in-process tests."""
    return [entry(r) for r in (rows if rows is not None else default_rows())]


def write_index(output_dir: Path, rows: list[dict] | None = None) -> Path:
    """Write research_index.json into a test output dir. Returns the path.

    Guards the destination: this file only ever belongs under a tmp dir, and
    the one mistake that would matter here is writing a three-ticker library
    over the real one.
    """
    output_dir = Path(output_dir)
    if "data/output" in str(output_dir) and "tmp" not in str(output_dir):
        raise AssertionError(
            f"refusing to write a fixture library into {output_dir} — "
            f"tests write to a tmp dir, never to the live library")
    output_dir.mkdir(parents=True, exist_ok=True)
    index = {e["ticker"]: e for e in entries(rows)}
    path = output_dir / INDEX_FILENAME
    path.write_text(json.dumps(index, indent=1))
    return path
