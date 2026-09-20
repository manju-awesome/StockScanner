"""
csv_writer.py
=============
Writes scan results to a timestamped, Excel-friendly CSV (UTF-8 BOM) —
call save_metrics_to_csv(rows) after the scan loop.

Usage (see scanners/scan_universe.py):
    from stockanalysis.reporting.csv_writer import save_metrics_to_csv

    rows = []
    for ticker in TICKERS:
        row = get_metrics(ticker, qqq_return_3m)
        row["Category"], row["Cat_Reason"], row["Rank_Score"] = categorize(row)
        rows.append(row)

    save_metrics_to_csv(rows)              # → metrics_YYYYMMDD_HHMMSS.csv

Run standalone (writes a CSV from built-in sample rows):
    python csv_writer.py
    python -m stockanalysis.reporting.csv_writer
"""

import csv
import os
from datetime import datetime

# ── Column order (matches get_metrics output + Category appended) ─────────────
COLUMN_ORDER = [
    # Identity
    "Ticker", "Category", "Current Price",
    "Put_Candidate", "Put_Score", "Put_Reason",
    "Call_Candidate", "Call_Score", "Call_Strength", "Call_Reason", "Call_Strike_Hint",
    "Trend_Strength",

    # Price & Market
     "MarketCap",

    # Earnings
    "EarningsDate", "EarningsBeat",

    # 52-Week levels
    "52W High", "52W Low", "52W_High_Date", "Days_Since_52W_High",
    "Dist_52W_High%", "Pct_From_52W_Low%",

    # Moving averages
    "200MA", "50MA", "8EMA", "21EMA",
    "Price_vs_200MA%", "Price_vs_50MA%", "Pct_vs_8EMA", "Above_200MA",

    # Intraday levels
    "VWAP", "Above_VWAP",
    "Pre-Market Low", "Pre-Market High",
    "Prev-Day Low", "Prev-Day High",

    # Key levels (support/resistance)
    "S1", "R1", "Key_Level_Score", "Touches", "Volume_Confirmation",
    "Dist_to_Support%", "Dist_to_Resistance%", "RR_to_Resistance",
    "Breakout_Probability", "Bounce_Probability",

    # Volatility
    "ATR20", "ATR_Pct", "ATR Shrinking",

    # Volume
    "RVOL", "Vol_vs_20D", "VolumeDryingUp", "Pullback_Vol_Ratio",

    # Momentum / technicals
    "RS", "ADX_14", "RSI_14", "BB_PctB",

    # Fundamentals
    "EPS_Growth%", "Revenue", "Inst_Own%", "Inst_Own_Chg",
    "FCF_Positive", "Short_Interest%",


    # Composite
    "CANSLIM_Pass",
    

    "LongName", "Sector",

    # Gate
    "Entry_Gate_Pass", "Entry_Gate_Reason",

    # Long-term engine (core.longterm). Appended rather than interleaved so
    # the column layout of existing saved scans is unchanged. Being listed
    # here at all matters: any key NOT in COLUMN_ORDER is written as an
    # "extra", and extras are prepended — which would put twenty new columns
    # ahead of Ticker in every spreadsheet.
    "MA50_Slope%", "MA200_Slope%", "Reversal_Candle",
    "Distribution_Days_25d", "Prior_Breakout_Level",
    "FreeCashFlow", "NetIncome", "SharesOutstanding", "Beta",
    "TotalRevenue", "EnterpriseValue", "EBITDA", "EV_EBITDA", "PriceToBook",
    "FCF_CAGR%", "FCF_Years", "FCF_Positive_Years", "Revenue_CAGR%",
    "OperatingMargin_Trend_pp", "GrossMargin_Trend_pp", "Fundamentals_As_Of",
]

# Columns to exclude from the CSV (internal calc helpers)
_EXCLUDE = {"_prior_52w_high", "_prior_52w_low",
           "BusinessSummary",  # full paragraph text — belongs on the research
                               # page (research.py), not a scan CSV column
           # Multi-year statement series from core.longterm.fundamentals: a
           # list in a spreadsheet cell is a Python repr nobody can sort or
           # filter on. The scalars derived from them (FCF_CAGR%,
           # OperatingMargin_Trend_pp) are columns above, and the full
           # series stay in research_index.json.
           "FCF_History", "OperatingMargin_History", "GrossMargin_History"}


def _fmt(value) -> str:
    """Format a value for CSV — booleans as TRUE/FALSE, None as blank."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def save_metrics_to_csv(
    rows: list[dict],
    output_dir: str = ".",
    filename: str | None = None,
) -> str:
    """
    Write a list of get_metrics() dicts to a CSV file.

    Parameters
    ----------
    rows       : list of dicts returned by get_metrics() (+ optional 'Category' key)
    output_dir : folder to write into (default: current dir)
    filename   : override filename; auto-generates metrics_YYYYMMDD_HHMMSS.csv if None

    Returns
    -------
    str : absolute path of the written file
    """
    if not rows:
        print("[CSV] No rows to write.")
        return ""

    os.makedirs(output_dir, exist_ok=True)

    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"metrics_{ts}.csv"

    filepath = os.path.join(output_dir, filename)

    # Build final column list: COLUMN_ORDER first, then any unexpected extras
    all_keys  = set()
    for r in rows:
        all_keys.update(r.keys())
    all_keys -= _EXCLUDE

    ordered   = [c for c in COLUMN_ORDER if c in all_keys]
    extras    = sorted(all_keys - set(COLUMN_ORDER))   # any new keys not in template
    final_cols = extras + ordered

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        # utf-8-sig adds BOM → Excel opens without needing "Import" wizard
        writer = csv.DictWriter(
            f,
            fieldnames=final_cols,
            extrasaction="ignore",   # silently skip _EXCLUDE keys
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _fmt(row.get(col)) for col in final_cols})

    print(f"[CSV] Saved {len(rows)} tickers → {os.path.abspath(filepath)}")
    return os.path.abspath(filepath)


# ── Quick standalone test (python write_metrics_csv.py) ──────────────────────
def main() -> None:
    """Standalone entry point — run this module directly."""
    sample = [
        {
            "Ticker": "NVDA", "LongName": "NVIDIA Corporation", "Sector": "Technology",
            "Category": "Momentum", "Current Price": 131.50, "MarketCap": 3_200_000_000_000,
            "52W High": 153.13, "52W Low": 75.61, "52W_High_Date": "2025-11-21",
            "Days_Since_52W_High": 217, "Dist_52W_High%": -14.1, "Pct_From_52W_Low%": 73.9,
            "200MA": 128.40, "50MA": 118.90, "8EMA": 130.20, "21EMA": 126.80,
            "Price_vs_200MA%": 2.4, "Price_vs_50MA%": 10.6, "Pct_vs_8EMA": 0.99,
            "Above_200MA": True, "VWAP": 130.10, "Above_VWAP": True,
            "Pre-Market Low": 129.50, "Pre-Market High": 132.30,
            "Prev-Day Low": 128.80, "Prev-Day High": 132.60,
            "ATR20": 4.21, "ATR_Pct": 3.20, "ATR Shrinking": False,
            "RVOL": 1.43, "Vol_vs_20D": 1.31, "VolumeDryingUp": False,
            "Pullback_Vol_Ratio": 0.72, "RS": 18.4,
            "ADX_14": 32.5, "Trend_Strength": "Trending",
            "RSI_14": 57.3, "BB_PctB": 0.61,
            "EPS_Growth%": 87.2, "Revenue": 122.4, "Inst_Own%": 65.3,
            "Inst_Own_Chg": 1.2, "FCF_Positive": True, "Short_Interest%": 0.8,
            "EarningsDate": "2025-08-27", "EarningsBeat": True,
            "CANSLIM_Pass": True, "Entry_Gate_Pass": True, "Entry_Gate_Reason": "",
            "_prior_52w_high": 152.90, "_prior_52w_low": 75.61,
        },
        {
            "Ticker": "TSLA", "LongName": "Tesla Inc", "Sector": "Consumer Cyclical",
            "Category": "Momentum-Pullback", "Current Price": 285.20, "MarketCap": 912_000_000_000,
            "52W High": 488.54, "52W Low": 182.00, "52W_High_Date": "2024-12-18",
            "Days_Since_52W_High": 190, "Dist_52W_High%": -41.6, "Pct_From_52W_Low%": 56.7,
            "200MA": 310.50, "50MA": 270.30, "8EMA": 282.10, "21EMA": 275.60,
            "Price_vs_200MA%": -8.2, "Price_vs_50MA%": 5.5, "Pct_vs_8EMA": 1.10,
            "Above_200MA": False, "VWAP": 283.40, "Above_VWAP": True,
            "Pre-Market Low": 282.00, "Pre-Market High": 287.50,
            "Prev-Day Low": 279.30, "Prev-Day High": 290.10,
            "ATR20": 12.80, "ATR_Pct": 4.49, "ATR Shrinking": True,
            "RVOL": 0.91, "Vol_vs_20D": 0.88, "VolumeDryingUp": True,
            "Pullback_Vol_Ratio": 0.61, "RS": -5.2,
            "ADX_14": 22.1, "Trend_Strength": "Ranging",
            "RSI_14": 48.6, "BB_PctB": 0.38,
            "EPS_Growth%": 12.1, "Revenue": 3.2, "Inst_Own%": 44.1,
            "Inst_Own_Chg": -0.8, "FCF_Positive": True, "Short_Interest%": 2.9,
            "EarningsDate": "2025-07-23", "EarningsBeat": False,
            "CANSLIM_Pass": False, "Entry_Gate_Pass": False,
            "Entry_Gate_Reason": "RVOL<0.6, ADX<15(flat)",
            "_prior_52w_high": 488.54, "_prior_52w_low": 182.00,
        },
    ]
    save_metrics_to_csv(sample, output_dir=".", filename="test_metrics.csv")


if __name__ == "__main__":
    main()
