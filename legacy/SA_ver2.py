#!/usr/bin/env python3
"""
stock_metrics_table.py

Pulls live/recent market data and classifies each ticker into one of four
trade-setup categories, each with its own column layout:


Requirements:
    pip install yfinance pandas pytz tabulate resend

Usage:
    python3 stock_metrics_table.py
    python3 stock_metrics_table.py --csv output.csv
    python3 stock_metrics_table.py --csv output.csv --email
    python3 stock_metrics_table.py --categories-only


Investing (long-term holds): GLW, LLY, INTC, and BE are the cleanest setups — above key MAs, strong EPS growth, and institutional confirmation (GLW has CANSLIM_Pass: True). AMD, CRDO, MU, SNDK, and MRVL show exceptional 3-month RS (114–161) and are in secular AI/memory tailwinds; these are core positions on any meaningful pullback.
Swing trades (5–20 day holds): COHR, GLW, CRDO, and TSM are above 8/21/50/200 MAs with compressed ATRs —
ideal for entries near 8EMA with defined risk. HIMS shows volume drying up with strong RS (+35), a classic VCP precursor. MU and SNDK are extended above 50MA (35%+) so wait for the first red week before adding.

Day trades: AMD (tight pre-market range 520–546, high RS),
 DRAM ETF (RVOL 2.07 — highest in the list, momentum ETF),
 GLW (breaking 52W high, RVOL 1.4), and
 AMZN (RVOL 1.51, liquid, near support) are the cleanest intraday setups.
 SOFI (RVOL 1.62) also worth watching for a bounce off 50MA.

Stocks to avoid: PLTR (-47% RS, below all MAs, 45% from 52W high), OKLO (-72% from 52W high, below 200MA), FLY (-66% from 52W high, deeply below all MAs), NVTS (below all MAs, negative revenue), and MSFT/META/TSLA (all deeply below 200MA with negative RS).
RGTI and IONQ are speculative quantum plays — too far from structure to trade technically.
"""


import os
import sys
import json
import base64
import argparse
import logging
from datetime import datetime, time as dtime
from libs.get_metrics import get_metrics
#import pandas_ta as ta

import pytz
import pandas as pd
import math
import json
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed. Run: pip install yfinance", file=sys.stderr)
    sys.exit(1)

try:
    import resend as resend_sdk
    HAVE_RESEND_SDK = True
except ImportError:
    HAVE_RESEND_SDK = False

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MARKET_TZ     = pytz.timezone("US/Eastern")
MARKET_OPEN   = dtime(9, 30)
PREMARKET_START = dtime(4, 0)

# All tickers scanned for Momentum / Momentum-Pullback / Pullback categories
# Longterm category is only evaluated for this curated list of high-quality names
LONGTERM_TICKERS = [
    "AVGO", "META"
]

ALL_TICKERS = [
    "PLTR", "TSLA", "AMZN",  "GOOGL", "MSFT"
]
'''
TICKERS = [
    "MU", "LITE", "CIEN", "COHR", "SPY",
    "BE", "MRVL", "CRDO",  "GLW", "AAOI",  "INTC",
    "CRWV", "HOOD", "INOD", "DRAM", "OKLO", "IREN",
    "SMCI", "HIMS", "USAR","NVTS", "FLY", "RGTI",
    "SOFI","IONQ","SNDK","HOOD"
]


# Longterm category is only evaluated for this curated list of high-quality names
LONGTERM_TICKERS = [
    "PLTR", "TSLA", "AMZN", "META", "GOOGL", "MSFT",
    "NVDA", "AVGO", "TSM", "ASML", "LLY", "GLD","AMD","SPCX"
]


# All tickers scanned for Momentum / Momentum-Pullback / Pullback categories
TICKERS = [

 "BE"
]


# Longterm category is only evaluated for this curated list of high-quality names
LONGTERM_TICKERS = [
 "META", "AMZN"
]
'''


#ALL_TICKERS = sorted(set(TICKERS) | set(LONGTERM_TICKERS))

# ---- Email config (Resend) --------------------------------------------------
EMAIL_TO         = "mthimmareddy99@gmail.com"
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
RESEND_API_URL = "https://api.resend.com/emails"


# =============================================================================
# DATA FETCHING
# =============================================================================
def calculate_atr(df, period=20):

    high_low = df["High"] - df["Low"]

    high_close = (
        df["High"] - df["Close"].shift()
    ).abs()

    low_close = (
        df["Low"] - df["Close"].shift()
    ).abs()

    tr = pd.concat(
        [high_low, high_close, low_close],
        axis=1
    ).max(axis=1)

    atr = tr.rolling(period).mean()

    return atr

def trend_quality(row):

    return (
        row["8EMA"] > row["21EMA"] >
        row["50MA"] > row["200MA"]
    )

def get_series(data):
    if isinstance(data, pd.DataFrame):
        return data.iloc[:, 0]
    return data


print("Downloading QQQ data...")

qqq = yf.download(
    "QQQ",
    period="6mo",
    progress=False,
    auto_adjust=True
)

qqq_close = get_series(qqq["Close"])

if len(qqq_close) >= 63:
    qqq_return_3m = (
            (float(qqq_close.iloc[-1]) /
             float(qqq_close.iloc[-63]) - 1)
            * 100
    )

    # print("3m qqq return:", round(qqq_return_3m, 2))
else:
    qqq_return_3m = 0






def pct_diff(price, reference):
    """% difference: positive = price above reference, negative = below."""
    if price is None or reference is None or reference == 0:
        return None
    return round(((price - reference) / reference) * 100, 2)


def build_table(tickers: list) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        print(f"  Fetching {ticker}...", file=sys.stderr, end="\r")
        rows.append(get_metrics(ticker))
    print(" " * 40, file=sys.stderr, end="\r")  # clear progress line

    df = pd.DataFrame(rows)

    price = df["Current Price"]

    df["% From 52W High"] = df.apply(lambda r: pct_diff(r["Current Price"], r["52W High"]), axis=1)
    df["% From 52W Low"]  = df.apply(lambda r: pct_diff(r["Current Price"], r["52W Low"]),  axis=1)
    df["% From 8EMA"]     = df.apply(lambda r: pct_diff(r["Current Price"], r["8EMA"]),     axis=1)
    df["% From VWAP"]     = df.apply(lambda r: pct_diff(r["Current Price"], r["VWAP"]),     axis=1)
    df["% From 200MA"]    = df.apply(lambda r: pct_diff(r["Current Price"], r["200MA"]),    axis=1)
    df["% From 21EMA"]    = df.apply(lambda r: pct_diff(r["Current Price"], r["21EMA"]),    axis=1)
    df["% From 50MA"]     = df.apply(lambda r: pct_diff(r["Current Price"], r["50MA"]),     axis=1)

    # Above VWAP / 8EMA / 200MA simultaneously
    def above_all(r):
        p = r["Current Price"]
        if p is None or r["VWAP"] is None or r["8EMA"] is None or r["200MA"] is None:
            return None
        return p > r["VWAP"] and p > r["8EMA"] and p > r["200MA"]
    df["Above VWAP/8EMA/200MA"] = df.apply(above_all, axis=1)

    def bullish_sign(r):
        p = r["Current Price"]
        if p is None or r["21EMA"] is None or r["8EMA"] is None or r["200MA"] is None or r["50MA"] is None:
            return None
        return p > r["8EMA"] and p > r["21EMA"] and p > r["50MA"] and p > r["200MA"]

    df["Above 8EMA/21EMA/50MA/200MA"] = df.apply(bullish_sign, axis=1)

    # New 52W High Today
    def new_52w_high(r):
        prior = r.get("_prior_52w_high")
        check = r.get("_today_high") or r.get("Current Price")
        if prior is None or check is None:
            return None
        return check > prior
    df["New 52W High Today"] = df.apply(new_52w_high, axis=1)

    # New 52W Low Today
    def new_52w_low(r):
        prior = r.get("_prior_52w_low")
        check = r.get("_today_low") or r.get("Current Price")
        if prior is None or check is None:
            return None
        return check < prior
    df["New 52W Low Today"] = df.apply(new_52w_low, axis=1)

    # ---- Categorisation -----------------------------------------------------
    def categorize(r):
        ticker  = r["Ticker"]
        price   = r["Current Price"]
        pct_h   = r["% From 52W High"]   # negative = below high
        pct_l   = r["% From 52W Low"]    # positive = above low
        pct_e   = r["% From 8EMA"]
        ma200   = r["200MA"]
        ema8    = r["8EMA"]
        above   = r["Above 8EMA/21EMA/50MA/200MA"]
        rs= r["RS"]
        rvol=r["RVOL"]
        revenue=r["Revenue"]

        if price is None or pct_h is None:
            return None

        dist_below_high = -pct_h  # positive number meaning "X% below high"



        # Momentum: within 10% of 52W high AND above VWAP/8EMA/200MA
        if dist_below_high <= 10 and above is True and rs > 0 and rvol > 0.6 and revenue > 30:
            return "Momentum"

        # Momentum-Pullback: max 30% below 52W high, above 200MA,
        #                    price within -1% to +1% of 8EMA
        if (dist_below_high <= 30
                and ma200 is not None and price > ma200 and revenue > 30 and rvol > 1 and pct_e is not None
                and -1 <= pct_e <= 1):
            return "Momentum-Pullback"

        # Pullback: 20%-40% below 52W high AND above 200MA
        if 20 <= dist_below_high <= 40 and ma200 is not None and price > ma200:
            return "VCP setup"

        # Longterm: -5% to +8% from 52W low (curated tickers only)
        if ticker in LONGTERM_TICKERS and pct_l is not None and -10 <= pct_l <= -1 and rvol > 1.2:
            return "Longterm"
        #all stocks
        if ticker is not None:
            return "all_stocks"


        return None

    df["Category"] = df.apply(categorize, axis=1)

    return df


# =============================================================================
# CATEGORY-SPECIFIC OUTPUT TABLES
# =============================================================================

def _fmt(v, pct=False, sign=False):
    """Format a numeric value for display. Adds sign and % if requested."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if pct:
        s = f"{v:+.2f}%" if sign else f"{v:.2f}%"
        return s
    return str(v)


def build_momentum_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Momentum: Within 10% of 52W high, above VWAP/8EMA/200MA
    Columns: Category | Ticker | Current Price | % Below 52W High |
             8EMA | % From 8EMA | New 52W High Today | Sector
    """
    sub = df[df["Category"] == "Momentum"].copy()
    sub["% Below 52W High"] = sub["% From 52W High"].apply(lambda v: round(-v, 2) if v is not None else None)
    out = sub[["Category","Ticker", "Current Price",
            "52W Low", "52W High","% From 52W Low","8EMA","EarningsDate",
            "% From 52W High","% From 8EMA","Revenue","RS","RVOL","Above 8EMA/21EMA/50MA/200MA","ATR Shrinking","21EMA","50MA"]].copy()
    out = out.sort_values("% From 52W High").reset_index(drop=True)
    return out


def build_momentum_pullback_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Momentum-Pullback: Max 30% below 52W high, above 200MA, -1% to +1% from 8EMA
    Columns: Category | Ticker | Current Price | % Below 52W High |
             8EMA | % From 8EMA | New 52W High Today | Sector
    """
    sub = df[df["Category"] == "Momentum-Pullback"].copy()
    sub["% Below 52W High"] = sub["% From 52W High"].apply(lambda v: round(-v, 2) if v is not None else None)
    out = sub[["Category","Ticker", "Current Price",
            "52W Low", "52W High","% From 52W Low","8EMA","EarningsDate",
            "% From 52W High","% From 8EMA","Revenue","RS","RVOL","Above 8EMA/21EMA/50MA/200MA","ATR Shrinking","21EMA","50MA"]].copy()
    out = out.sort_values("% From 52W High").reset_index(drop=True)
    return out


def build_pullback_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pullback: 20%-40% below 52W high, above 200MA
    Columns: Category | Ticker | Current Price | % Below 52W High |
             8EMA | % From 8EMA | New 52W High Today | Sector
    """
    sub = df[df["Category"] == "VCP setup"].copy()
    sub["% Below 52W High"] = sub["% From 52W High"].apply(lambda v: round(-v, 2) if v is not None else None)
    out = sub[["Category","Ticker", "Current Price",
            "52W Low", "52W High","% From 52W Low","8EMA",
            "% From 52W High","% From 8EMA","Revenue","RS","RVOL","Above 8EMA/21EMA/50MA/200MA","ATR Shrinking","21EMA","50MA"]].copy()
    out = out.sort_values("% From 52W High").reset_index(drop=True)
    return out


def build_longterm_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Longterm: -5% to +8% from 52W low (curated tickers only)
    Columns: Category | Ticker | Current Price | 52W Low | % Below 52W High |
             % Above 52W Low | 200MA | % From 8EMA |
             Above VWAP/8EMA/200MA | New 52W Low Today | Sector
    """
    sub = df[df["Category"] == "Longterm"].copy()
    sub["% Below 52W High"] = sub["% From 52W High"].apply(lambda v: round(-v, 2) if v is not None else None)
    sub["% Above 52W Low"]  = sub["% From 52W Low"]
    out = sub[["Category","Ticker", "Current Price",
            "52W Low", "52W High","% From 52W Low","8EMA","EarningsDate",
            "% From 52W High","% From 8EMA","Revenue","RS","RVOL","Above 8EMA/21EMA/50MA/200MA","ATR Shrinking","21EMA","50MA"]].copy()
    out = out.sort_values("% From 52W Low").reset_index(drop=True)
    return out

def build_allstocks_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Longterm: -5% to +8% from 52W low (curated tickers only)
    Columns: Category | Ticker | Current Price | 52W Low | % Below 52W High |
             % Above 52W Low | 200MA | % From 8EMA |
             Above VWAP/8EMA/200MA | New 52W Low Today | Sector
    """
    sub = df[df["Category"] == "all_stocks"].copy()

    out = sub[["Ticker", "Current Price",
            "52W Low", "52W High","% From 52W Low","8EMA","EarningsDate",
            "% From 52W High","% From 8EMA","Revenue","RS","RVOL","Above 8EMA/21EMA/50MA/200MA","ATR Shrinking","21EMA","50MA"]].copy()

    return out


def print_section(title: str, criteria: str, df_section: pd.DataFrame):
    """Print a labelled, tab-separated category block ready for Excel paste."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"  Criteria: {criteria}")
    print(f"{'='*70}")
    if df_section.empty:
        print("  (no tickers qualify currently)\n")
    else:
        print(df_section.to_csv(sep="\t", index=False))


# =============================================================================
# EMAIL
# =============================================================================

def send_categories_email(sections: dict, csv_path: str):
    """Email all four category tables via Resend as a CSV attachment with a
    readable text body summarising each category."""
    if not RESEND_API_KEY:
        log.error("Missing RESEND_API_KEY environment variable. Cannot send email.")
        return

    now_str = datetime.now(MARKET_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    subject = f"Stock Categories - {datetime.now(MARKET_TZ).strftime('%Y-%m-%d')}"

    body_lines = [f"Stock category scan results — {now_str}\n"]
    for cat, (label, df_section) in sections.items():
        tickers = df_section["Ticker"].tolist() if not df_section.empty else []
        body_lines.append(f"{cat} ({len(tickers)}): {', '.join(tickers) if tickers else 'none'}")
    body_lines.append("\nFull details attached as CSV.")
    body = "\n".join(body_lines)

    # Base64-encode the CSV attachment
    try:
        with open(csv_path, "rb") as f:
            csv_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        log.error("Could not read CSV for attachment: %s", e)
        csv_b64 = None

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [EMAIL_TO],
        "subject": subject,
        "text": body,
    }
    if csv_b64:
        payload["attachments"] = [{"filename": os.path.basename(csv_path), "content": csv_b64}]

    if HAVE_RESEND_SDK:
        try:
            resend_sdk.api_key = RESEND_API_KEY
            result = resend_sdk.Emails.send(payload)
            log.info("Email sent via Resend SDK (id=%s)",
                     result.get("id") if isinstance(result, dict) else result)
            print(f"Email sent to {EMAIL_TO}", file=sys.stderr)
            return
        except Exception as e:
            log.warning("Resend SDK failed (%s) — trying raw HTTP fallback", e)

    import urllib.request, urllib.error
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; stock-metrics-table/1.0)",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log.info("Email sent via Resend HTTP (status %s)", resp.status)
            print(f"Email sent to {EMAIL_TO}", file=sys.stderr)
    except urllib.error.HTTPError as e:
        log.error("Resend API error %s: %s", e.code, e.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log.error("Failed to send email: %s", e)


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Stock category scanner.")
    parser.add_argument("--csv",            help="Save full metrics to this CSV path")
    parser.add_argument("--email",          action="store_true",
                        help="Email categorized results via Resend")
    parser.add_argument("--categories-only", action="store_true",
                        help="Skip the full metrics table; print category tables only")
    args = parser.parse_args()

    print("Fetching data for all tickers...", file=sys.stderr)
    df = build_table(ALL_TICKERS)

    # ---- Optional: full metrics table ---------------------------------------
    if not args.categories_only:
        full_cols2 = [
            "Ticker", "Current Price",
            "52W Low", "52W High","% From 52W Low","8EMA","EarningsDate"
            "% From 52W High","% From 8EMA","Revenue","RS","RVOL","Above 8EMA/21EMA/50MA/200MA","ATR Shrinking",
            "21EMA","50MA","% From 21EMA","% From 50MA"]
        full_cols = [
            "Ticker",
            "Sector",
            "EarningsDate",

            "Current Price",
            "52W Low",
            "52W High",
            "_prior_52w_low",
            "_prior_52w_high",

            "% From 52W Low",
            "% From 52W High",
            "Dist_52W_High%",

            "200MA",
            "50MA",
            "21EMA",
            "8EMA",

            "% From 50MA",
            "% From 21EMA",
            "% From 8EMA",

            "VWAP",

            "Revenue",
            "EPS_Growth%",

            "stock_return_3m",
            "qqq_return_3m",
            "RS",

            "RVOL",
            "Vol_vs_20D",
            "VolumeDryingUp",

            "ATR Shrinking",
            "ADX_14",
            "Trend_Strength",

            "Inst_Own%",
            "CANSLIM_Pass",

            "Prev-Day Low",
            "Prev-Day High",

            "Pre-Market Low",
            "Pre-Market High",

            "_today_low",
            "_today_high",

            "Above 8EMA/21EMA/50MA/200MA"
        ]
        # Momentum: within 10% of 52W high AND above VWAP/8EMA/200MA



        full_df = df[[c for c in full_cols if c in df.columns]].copy()
        print("\n=== FULL METRICS TABLE ===")
        try:
            print(full_df.to_markdown(index=False))
        except ImportError:
            print(full_df.to_string(index=False))

    # ---- Four category tables -----------------------------------------------
    mom_df  = build_momentum_df(df)
    mpb_df  = build_momentum_pullback_df(df)
    pb_df   = build_pullback_df(df)
    lt_df   = build_longterm_df(df)
    all_df=  build_allstocks_df(df)

    print_section(
        "ALL STOCKS",
        "All stocks in CSV format",
        all_df,
    )

    print_section(
        "MOMENTUM",
        "Within 5% of high Above VWAP Above 8EMA Above 21EMA Above 50MA Above 200MA RVOL > 1.5 Revenue Growth > 30% RS > QQQ",
        mom_df,
    )
    print_section(
        "MOMENTUM-PULLBACK",
        "10-30% below high Above 200MA Within 1% of 8EMA Revenue Growth > 20%",
        mpb_df,
    )
    print_section(
        "VCP setup (watchlist)",
        "10-25% below high ATR shrinking Volume drying up",
        pb_df,
    )
    print_section(
        "LONGTERM  (curated tickers only)",
        "Current price between 40-70% below high and Revenue acceleration Above 200MA",
        lt_df,
    )
    print(f"\nScanned at: {datetime.now(MARKET_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

    # ---- CSV export ---------------------------------------------------------
    sections = {
        "All stocks":        ("all stocks",full_df),
        "Momentum":          ("Momentum",          mom_df),
        "Momentum-Pullback": ("Momentum-Pullback",  mpb_df),
        "Pullback":          ("Pullback",           pb_df),
        "Longterm":          ("Longterm",           lt_df),
    }

    cat_csv_path = None
    if args.csv:
        # Full metrics CSV (drops internal helper columns)
        save_cols = [c for c in df.columns if not c.startswith("_")]
        df[save_cols].to_csv(args.csv, index=False)
        print(f"Saved full metrics to {args.csv}", file=sys.stderr)

        # Categorized CSV: all four sections stacked with a blank row between
        cat_csv_path = args.csv.rsplit(".", 1)[0] + "_categories.csv"
        frames = []
        for cat, (label, df_section) in sections.items():
            if not df_section.empty:
                frames.append(df_section)
                frames.append(pd.DataFrame([{}]))   # blank separator row
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(cat_csv_path, index=False)
            print(f"Saved categorized results to {cat_csv_path}", file=sys.stderr)

    if args.email:
        if cat_csv_path is None:
            # Need a temp CSV to attach even if --csv wasn't passed
            cat_csv_path = "/tmp/stock_categories.csv"
            frames = []
            for cat, (label, df_section) in sections.items():
                if not df_section.empty:
                    frames.append(df_section)
                    frames.append(pd.DataFrame([{}]))
            if frames:
                pd.concat(frames, ignore_index=True).to_csv(cat_csv_path, index=False)

        send_categories_email(sections, cat_csv_path)


if __name__ == "__main__":
    main()