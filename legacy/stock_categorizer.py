"""
stock_categorizer.py
────────────────────
Fetches live metrics via get_metrics() for every ticker in WATCHLIST,
then classifies each into: Day Trade | Swing Trade | Long Term | Avoid.

Usage:
    python stock_categorizer.py                  # uses default WATCHLIST
    python stock_categorizer.py NVDA TSLA AMD    # override tickers via CLI

Output:
    stock_categories.html   — interactive dashboard
    stock_metrics.csv       — full metrics table (Excel-ready)
    Console summary printed to stdout
"""

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────
import csv
import math
import sys
import logging
from datetime import datetime
from collections import defaultdict
from zoneinfo import ZoneInfo

import yfinance as yf

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
MARKET_TZ       = ZoneInfo("America/New_York")
PREMARKET_START = datetime.strptime("04:00", "%H:%M").time()
MARKET_OPEN     = datetime.strptime("09:30", "%H:%M").time()

OUTPUT_HTML = "stock_categories.html"
OUTPUT_CSV  = "stock_metrics.csv"

WATCHLIST = [
    "AAOI", "AMD",  "AMZN", "ASML", "AVGO", "BE",   "CIEN", "COHR",
    "CRDO", "CRWV", "DRAM", "FLY",  "GLD",  "GLW",  "GOOGL","HIMS",
    "HOOD", "INOD", "INTC", "IONQ", "IREN", "LITE", "LLY",  "META",
    "MRVL", "MSFT", "MU",   "NVDA", "NVTS", "OKLO", "PLTR", "RGTI",
    "SMCI", "SNDK", "SOFI", "SPCX", "TSLA", "TSM",  "USAR",
]

log = logging.getLogger("stock_cat")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

# ─────────────────────────────────────────────────────────────────────────────
# ATR HELPER  (used inside get_metrics)
# ─────────────────────────────────────────────────────────────────────────────
def calculate_atr(df, period: int = 20):
    """Average True Range over `period` bars."""
    import pandas as pd
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"].shift(1)
    tr    = pd.concat([high - low,
                       (high - close).abs(),
                       (low  - close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ─────────────────────────────────────────────────────────────────────────────
# QQQ BENCHMARK  (fetched once, shared across all tickers)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_qqq_return_3m() -> float:
    try:
        qqq = yf.Ticker("QQQ")
        hist = qqq.history(period="1y", interval="1d", auto_adjust=False)
        if len(hist) >= 63:
            ret = ((hist["Close"].iloc[-1] / hist["Close"].iloc[-63]) - 1) * 100
            log.info("QQQ 3M return: %.2f%%", ret)
            return round(ret, 4)
    except Exception as e:
        log.warning("QQQ fetch failed (%s) — defaulting to 0", e)
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# get_metrics  — YOUR ORIGINAL FUNCTION (verbatim, injected with qqq_return_3m)
# ─────────────────────────────────────────────────────────────────────────────
def get_metrics(ticker: str, qqq_return_3m: float = 0.0) -> dict:
    """Fetch all required metrics for a single ticker."""
    row = {"Ticker": ticker}
    t   = yf.Ticker(ticker)

    # ── Sector ───────────────────────────────────────────────────────────────
    try:
        info = t.info
        row["Sector"] = info.get("sector") or info.get("quoteType") or "N/A"
    except Exception:
        row["Sector"] = "N/A"
        info = {}

    # ── Earnings calendar ────────────────────────────────────────────────────
    try:
        earnings = t.calendar.loc["Earnings Date"]
        row["EarningsDate"] = earnings
    except Exception:
        row["EarningsDate"] = "N/A"

    # ── Current price ────────────────────────────────────────────────────────
    try:
        row["Current Price"] = round(float(t.fast_info["last_price"]), 2)
    except Exception as e:
        log.warning("%s: current price failed (%s)", ticker, e)
        row["Current Price"] = None

    # ── Daily history (1 year) ───────────────────────────────────────────────
    try:
        daily = t.history(period="1y", interval="1d", auto_adjust=False)
        if not daily.empty:
            row["52W Low"]  = round(float(daily["Low"].min()),  2)
            row["52W High"] = round(float(daily["High"].max()), 2)

            today_et    = datetime.now(MARKET_TZ).date()
            daily_dates = (
                daily.index.tz_convert(MARKET_TZ).date
                if daily.index.tz is not None else daily.index.date
            )

            # Prior 52W high / low (excluding today)
            if daily_dates[-1] == today_et:
                prior_series = daily.iloc[:-1]
            else:
                prior_series = daily

            row["_prior_52w_high"] = (
                round(float(prior_series["High"].max()), 2) if len(prior_series) else None
            )
            row["_prior_52w_low"] = (
                round(float(prior_series["Low"].min()), 2) if len(prior_series) else None
            )

            # ── Moving Averages ───────────────────────────────────────────
            row["200MA"] = (
                round(float(daily["Close"].rolling(200).mean().iloc[-1]), 2)
                if len(daily) >= 200 else None
            )
            row["50MA"] = (
                round(float(daily["Close"].rolling(50).mean().iloc[-1]), 2)
                if len(daily) >= 50 else None
            )
            row["8EMA"]  = round(float(daily["Close"].ewm(span=8,  adjust=False).mean().iloc[-1]), 2)
            row["21EMA"] = round(float(daily["Close"].ewm(span=21, adjust=False).mean().iloc[-1]), 2)

            # Derived MA distances
            price = row.get("Current Price") or float(daily["Close"].iloc[-1])
            for ma_key, ma_label in [("50MA", "% From 50MA"), ("21EMA", "% From 21EMA"), ("8EMA", "% From 8EMA")]:
                ma_val = row.get(ma_key)
                if ma_val and ma_val != 0:
                    row[ma_label] = round((price / ma_val - 1) * 100, 2)
                else:
                    row[ma_label] = None

            # Above all MAs flag
            ma200 = row.get("200MA")
            ma50  = row.get("50MA")
            ema21 = row.get("21EMA")
            ema8  = row.get("8EMA")
            row["Above 8EMA/21EMA/50MA/200MA"] = (
                all(v is not None and price > v for v in [ema8, ema21, ma50, ma200])
            )

            # 52W distances
            if row["52W Low"] and row["52W Low"] != 0:
                row["% From 52W Low"] = round((price / row["52W Low"] - 1) * 100, 2)
            else:
                row["% From 52W Low"] = None

            week52_high = daily["High"].max()
            row["Dist_52W_High%"] = round(((float(daily["Close"].iloc[-1]) / week52_high) - 1) * 100, 1)

            # ── ATR & Volatility ──────────────────────────────────────────
            daily["ATR20"] = calculate_atr(daily)
            atr20         = round(float(daily["ATR20"].iloc[-1]),  2)
            atr20_10d     = round(float(daily["ATR20"].iloc[-10]), 2)
            row["ATR Shrinking"] = atr20 < atr20_10d

            # ── Volume metrics ────────────────────────────────────────────
            avg50_vol = daily["Volume"].rolling(50).mean().iloc[-1]
            row["RVOL"] = round(daily["Volume"].iloc[-1] / avg50_vol, 2) if avg50_vol else None

            avg10_vol      = daily["Volume"].tail(10).mean()
            avg50_vol_tail = daily["Volume"].tail(50).mean()
            row["VolumeDryingUp"] = avg10_vol < avg50_vol_tail * 0.75

            avg20_vol = daily["Volume"].rolling(20).mean().iloc[-1]
            row["Vol_vs_20D"] = round(daily["Volume"].iloc[-1] / avg20_vol, 2) if avg20_vol else None

            # ── Relative Strength (vs QQQ) ────────────────────────────────
            stock_return_3m = ((daily["Close"].iloc[-1] / daily["Close"].iloc[-63]) - 1) * 100
            row["stock_return_3m"] = stock_return_3m
            row["qqq_return_3m"]   = qqq_return_3m
            row["RS"] = round(stock_return_3m - qqq_return_3m, 2)

            # ── ADX(14) ───────────────────────────────────────────────────
            try:
                import pandas_ta as ta
                adx_df = ta.adx(daily["High"], daily["Low"], daily["Close"], length=14)
                row["ADX_14"] = round(float(adx_df["ADX_14"].iloc[-1]), 1) if adx_df is not None else None
                adx_val = row["ADX_14"]
                if adx_val is None:
                    row["Trend_Strength"] = "N/A"
                elif adx_val >= 40:
                    row["Trend_Strength"] = "Strong"
                elif adx_val >= 25:
                    row["Trend_Strength"] = "Trending"
                else:
                    row["Trend_Strength"] = "Ranging"
            except ImportError:
                row["ADX_14"]        = None
                row["Trend_Strength"] = "N/A"
            except Exception as e:
                log.warning("%s: ADX failed (%s)", ticker, e)
                row["ADX_14"]        = None
                row["Trend_Strength"] = "N/A"

            # ── Fundamentals ──────────────────────────────────────────────
            row["Revenue"] = (
                round(info["revenueGrowth"] * 100, 2)
                if info.get("revenueGrowth") is not None else None
            )

            try:
                eps_trailing = info.get("trailingEps")
                eps_forward  = info.get("forwardEps")
                if eps_trailing and eps_forward and eps_trailing != 0:
                    row["EPS_Growth%"] = round(
                        ((eps_forward - eps_trailing) / abs(eps_trailing)) * 100, 1
                    )
                else:
                    row["EPS_Growth%"] = None
            except Exception:
                row["EPS_Growth%"] = None

            try:
                inst_pct = info.get("institutionPercentHeld")
                row["Inst_Own%"] = round(inst_pct * 100, 1) if inst_pct is not None else None
            except Exception:
                row["Inst_Own%"] = None

            # ── CANSLIM flag ──────────────────────────────────────────────
            try:
                row["CANSLIM_Pass"] = all([
                    row.get("EPS_Growth%") is not None and row["EPS_Growth%"] > 20,
                    row.get("RS")          is not None and row["RS"] > 0,
                    row.get("Vol_vs_20D")  is not None and row["Vol_vs_20D"] > 1.2,
                    row.get("Dist_52W_High%") is not None and row["Dist_52W_High%"] >= -15,
                ])
            except Exception:
                row["CANSLIM_Pass"] = None

            # ── Previous trading day ──────────────────────────────────────
            prev_row = daily.iloc[-2] if (daily_dates[-1] == today_et and len(daily) >= 2) else daily.iloc[-1]
            row["Prev-Day Low"]  = round(float(prev_row["Low"]),  2) if prev_row is not None else None
            row["Prev-Day High"] = round(float(prev_row["High"]), 2) if prev_row is not None else None

        else:
            for k in ("52W Low","52W High","200MA","8EMA","21EMA","50MA",
                      "Prev-Day Low","Prev-Day High","_prior_52w_high","_prior_52w_low",
                      "Vol_vs_20D","Dist_52W_High%","ADX_14","Trend_Strength",
                      "EPS_Growth%","Inst_Own%","CANSLIM_Pass",
                      "% From 50MA","% From 21EMA","% From 8EMA","% From 52W Low",
                      "Above 8EMA/21EMA/50MA/200MA"):
                row[k] = None

    except Exception as e:
        log.warning("%s: daily history failed (%s)", ticker, e)
        for k in ("52W Low","52W High","200MA","8EMA","21EMA","50MA",
                  "Prev-Day Low","Prev-Day High","_prior_52w_high","_prior_52w_low",
                  "Vol_vs_20D","Dist_52W_High%","ADX_14","Trend_Strength",
                  "EPS_Growth%","Inst_Own%","CANSLIM_Pass",
                  "% From 50MA","% From 21EMA","% From 8EMA","% From 52W Low",
                  "Above 8EMA/21EMA/50MA/200MA"):
            row[k] = None

    # ── Intraday (1-min, incl. pre-market) ───────────────────────────────────
    try:
        intra = t.history(period="1d", interval="1m", prepost=True, auto_adjust=False)
        if not intra.empty:
            idx_et = (
                intra.index.tz_convert(MARKET_TZ)
                if intra.index.tz is not None
                else intra.index.tz_localize(MARKET_TZ)
            )
            times = idx_et.time

            pm_mask = [(PREMARKET_START <= tm < MARKET_OPEN) for tm in times]
            pm_df   = intra[pm_mask]
            row["Pre-Market Low"]  = round(float(pm_df["Low"].min()),  2) if not pm_df.empty else None
            row["Pre-Market High"] = round(float(pm_df["High"].max()), 2) if not pm_df.empty else None

            reg_mask = [tm >= MARKET_OPEN for tm in times]
            reg_df   = intra[reg_mask]
            if not reg_df.empty and reg_df["Volume"].sum() > 0:
                typical = (reg_df["High"] + reg_df["Low"] + reg_df["Close"]) / 3
                row["VWAP"] = round(
                    float((typical * reg_df["Volume"]).sum() / reg_df["Volume"].sum()), 2
                )
            else:
                row["VWAP"] = None

            row["_today_high"] = round(float(intra["High"].max()), 2)
            row["_today_low"]  = round(float(intra["Low"].min()),  2)
        else:
            for k in ("Pre-Market Low","Pre-Market High","VWAP","_today_high","_today_low"):
                row[k] = None
    except Exception as e:
        log.warning("%s: intraday history failed (%s)", ticker, e)
        for k in ("Pre-Market Low","Pre-Market High","VWAP","_today_high","_today_low"):
            row[k] = None

    return row


# ─────────────────────────────────────────────────────────────────────────────
# DATA PIPELINE  — fetch all tickers
# ─────────────────────────────────────────────────────────────────────────────
def fetch_all(tickers: list[str]) -> list[dict]:
    qqq_ret = fetch_qqq_return_3m()
    rows = []
    for i, ticker in enumerate(tickers, 1):
        log.info("[%d/%d] Fetching %s …", i, len(tickers), ticker)
        try:
            row = get_metrics(ticker, qqq_return_3m=qqq_ret)
            rows.append(row)
        except Exception as e:
            log.error("%s: unexpected error (%s) — skipped", ticker, e)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# HELPER UTILITIES  (safe access + derived metrics)
# ─────────────────────────────────────────────────────────────────────────────
def safe(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default

def intraday_range_pct(row):
    lo = safe(row.get("_today_low"), 0)
    hi = safe(row.get("_today_high"), 0)
    return (hi - lo) / lo * 100 if lo > 0 else 0.0

def above_all_mas(row):
    v = row.get("Above 8EMA/21EMA/50MA/200MA", False)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")

def has_ma_data(row):
    v = row.get("200MA")
    if v is None:
        return False
    try:
        return not math.isnan(float(v))
    except (TypeError, ValueError):
        return False

def is_etf(row):
    sector = str(row.get("Sector", "")).lower()
    qt     = str(row.get("quoteType", "")).lower()
    return "etf" in sector or qt == "etf"


# ─────────────────────────────────────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def score_daytrade(row):
    score, reasons = 0, []

    ir = intraday_range_pct(row)
    if ir >= 5:
        score += 30; reasons.append(f"Wide intraday range {ir:.1f}%")
    elif ir >= 3:
        score += 15; reasons.append(f"Intraday range {ir:.1f}%")

    rv = safe(row.get("RVOL"), 0)
    if rv >= 0.5:
        score += 25; reasons.append(f"High RVOL {rv:.2f}")
    elif rv >= 0.3:
        score += 10; reasons.append(f"Moderate RVOL {rv:.2f}")

    price = safe(row.get("Current Price"), 0)
    vwap  = safe(row.get("VWAP"), 0)
    if vwap > 0:
        diff = abs((price - vwap) / vwap * 100)
        if diff <= 1.0:
            score += 20; reasons.append(f"Near VWAP ({diff:.1f}%)")
        elif diff <= 2.5:
            score += 10; reasons.append(f"Close to VWAP ({diff:.1f}%)")

    ret3m = safe(row.get("stock_return_3m"), -999)
    qqq   = safe(row.get("qqq_return_3m"), 0)
    if ret3m > qqq + 50:
        score += 20; reasons.append(f"3M outperformer +{ret3m:.0f}%")
    elif ret3m > qqq + 10:
        score += 10; reasons.append(f"3M outperformer")

    e8 = safe(row.get("% From 8EMA"), -999)
    if e8 >= -2:
        score += 10; reasons.append("Hugging 8EMA")
    elif e8 >= -5:
        score += 5

    if safe(row.get("RS"), 0) < -30:
        score -= 20; reasons.append("⚠ Weak RS")

    return score, reasons


def score_swing(row):
    score, reasons = 0, []

    if not has_ma_data(row):
        return 0, []

    if above_all_mas(row):
        score += 30; reasons.append("Above all MAs (8/21/50/200)")
    else:
        p50 = safe(row.get("% From 50MA"), -999)
        if p50 >= 0:
            score += 15; reasons.append(f"Above 50MA (+{p50:.1f}%)")
        p21 = safe(row.get("% From 21EMA"), -999)
        if p21 >= 0:
            score += 10; reasons.append(f"Above 21EMA (+{p21:.1f}%)")

    vol_dry = row.get("VolumeDryingUp")
    if vol_dry is True or str(vol_dry) in ("True","1"):
        score += 20; reasons.append("Volume drying up (VCP)")

    atr_shr = row.get("ATR Shrinking")
    if atr_shr is True or str(atr_shr) in ("True","1"):
        score += 10; reasons.append("ATR contracting")

    rs = safe(row.get("RS"), -999)
    if rs >= 50:
        score += 25; reasons.append(f"RS {rs:.0f} — strong outperformer")
    elif rs >= 20:
        score += 15; reasons.append(f"RS {rs:.0f} — outperformer")
    elif rs >= 0:
        score += 5

    eps = safe(row.get("EPS_Growth%"), -999)
    if eps >= 100:
        score += 15; reasons.append(f"EPS growth {eps:.0f}%")
    elif eps >= 30:
        score += 8

    d52h = safe(row.get("Dist_52W_High%"), -999)
    if d52h >= -10:
        score += 10; reasons.append(f"Near 52W high ({d52h:.1f}%)")
    elif d52h >= -20:
        score += 5

    # Penalty: too extended above 50MA
    if safe(row.get("% From 50MA"), 0) > 40:
        score -= 15; reasons.append("⚠ Overextended above 50MA")

    if rs < -20:
        score -= 20

    return score, reasons


def score_longterm(row):
    score, reasons = 0, []

    if is_etf(row):
        return 0, []

    rs = safe(row.get("RS"), -999)
    if rs >= 100:
        score += 35; reasons.append(f"Elite RS {rs:.0f}")
    elif rs >= 50:
        score += 25; reasons.append(f"Strong RS {rs:.0f}")
    elif rs >= 20:
        score += 15; reasons.append(f"Positive RS {rs:.0f}")
    elif rs < 0:
        score -= 20

    eps = safe(row.get("EPS_Growth%"), -999)
    if eps >= 200:
        score += 25; reasons.append(f"Exceptional EPS growth {eps:.0f}%")
    elif eps >= 50:
        score += 15; reasons.append(f"Good EPS growth {eps:.0f}%")
    elif eps >= 20:
        score += 8

    rev = safe(row.get("Revenue"), -999)
    if rev >= 100:
        score += 20; reasons.append(f"Revenue +{rev:.0f}%")
    elif rev >= 30:
        score += 12; reasons.append(f"Revenue +{rev:.0f}%")
    elif rev >= 15:
        score += 6

    if has_ma_data(row):
        price  = safe(row.get("Current Price"), 0)
        ma200  = safe(row.get("200MA"), 0)
        if ma200 > 0 and price > ma200:
            score += 15; reasons.append("Above 200MA")

    ret3m = safe(row.get("stock_return_3m"), -999)
    qqq   = safe(row.get("qqq_return_3m"), 0)
    if ret3m > qqq + 100:
        score += 15; reasons.append(f"Massive 3M outperformance (+{ret3m:.0f}%)")
    elif ret3m > qqq + 20:
        score += 8

    p52l = safe(row.get("% From 52W Low"), 0)
    if p52l < 10:
        score -= 15; reasons.append("⚠ Near 52W Low")

    # Bonus: CANSLIM pass
    canslim = row.get("CANSLIM_Pass")
    if canslim is True or str(canslim) == "True":
        score += 10; reasons.append("CANSLIM criteria met")

    # Bonus: strong institutional ownership
    inst = safe(row.get("Inst_Own%"), 0)
    if inst >= 70:
        score += 5; reasons.append(f"Inst. ownership {inst:.0f}%")

    return score, reasons


def score_avoid(row):
    score, reasons = 0, []

    d52h = safe(row.get("Dist_52W_High%"), 0)
    if d52h < -50:
        score += 30; reasons.append(f"Deep drawdown from 52W high ({d52h:.0f}%)")
    elif d52h < -35:
        score += 15; reasons.append(f"Significant drawdown ({d52h:.0f}%)")

    rs = safe(row.get("RS"), 0)
    if rs < -30:
        score += 25; reasons.append(f"Very weak RS {rs:.0f}")
    elif rs < -10:
        score += 10; reasons.append(f"Weak RS {rs:.0f}")

    eps = safe(row.get("EPS_Growth%"), 0)
    if eps < -100:
        score += 20; reasons.append(f"Negative EPS {eps:.0f}%")

    rev = safe(row.get("Revenue"), 0)
    if rev < 0:
        score += 10; reasons.append(f"Negative revenue growth {rev:.0f}%")

    if has_ma_data(row):
        p50 = safe(row.get("% From 50MA"), 0)
        if p50 < -20:
            score += 20; reasons.append(f"Far below 50MA ({p50:.0f}%)")
        elif p50 < -10:
            score += 10

    ret3m = safe(row.get("stock_return_3m"), 0)
    if ret3m < -10:
        score += 15; reasons.append(f"Negative 3M return ({ret3m:.1f}%)")
    elif ret3m < 0:
        score += 5

    if not has_ma_data(row) and not is_etf(row):
        score += 10; reasons.append("Insufficient MA data")

    return score, reasons


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICATION  — pick winning category per ticker
# ─────────────────────────────────────────────────────────────────────────────
def classify(row):
    if is_etf(row):
        rv = safe(row.get("RVOL"), 0)
        ir = intraday_range_pct(row)
        if rv >= 0.5 and ir >= 3:
            return "Day Trade", {}, ["ETF — elevated RVOL + wide range"]
        return "Long Term", {}, ["ETF — hold for diversification"]

    s_day,   r_day   = score_daytrade(row)
    s_swing, r_swing = score_swing(row)
    s_lt,    r_lt    = score_longterm(row)
    s_avoid, r_avoid = score_avoid(row)

    scores = {
        "Day Trade":   s_day,
        "Swing Trade": s_swing,
        "Long Term":   s_lt,
        "Avoid":       s_avoid,
    }
    reason_map = {
        "Day Trade":   r_day,
        "Swing Trade": r_swing,
        "Long Term":   r_lt,
        "Avoid":       r_avoid,
    }

    # Force Avoid if it leads all others
    if s_avoid >= 40 and s_avoid > max(s_day, s_swing, s_lt):
        return "Avoid", scores, r_avoid

    best = max(scores, key=scores.get)
    return best, scores, reason_map[best]


# ─────────────────────────────────────────────────────────────────────────────
# HTML DASHBOARD BUILDER
# ─────────────────────────────────────────────────────────────────────────────
CAT_CONFIG = {
    "Day Trade":   {"color": "#F59E0B", "icon": "⚡",
                    "desc": "Intraday momentum — elevated range & RVOL, near VWAP"},
    "Swing Trade": {"color": "#10B981", "icon": "📈",
                    "desc": "Multi-day setups — constructive structure, above key MAs, RS positive"},
    "Long Term":   {"color": "#6366F1", "icon": "🏦",
                    "desc": "Institutional-grade — elite RS, strong EPS/Revenue, sector leaders"},
    "Avoid":       {"color": "#EF4444", "icon": "🚫",
                    "desc": "Broken charts — deep drawdown, weak RS, below all MAs"},
}

def fmt_num(v, suffix="", precision=1, plus=False):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    try:
        s = f"{v:+.{precision}f}" if plus else f"{v:.{precision}f}"
        return s + suffix
    except (TypeError, ValueError):
        return "—"

def build_card(r):
    cat = r["category"]
    cfg = CAT_CONFIG[cat]
    sc  = r["scores"].get(cat, 0) if r["scores"] else 0

    reasons_html = "".join(f"<li>{x}</li>" for x in r["reasons"][:4])
    above_badge  = '<span class="badge-yes">✓ Above All MAs</span>' if r["above_all"] else ""
    canslim_badge = '<span class="badge-cs">★ CANSLIM</span>' if r.get("canslim") else ""

    return f"""
<div class="card" data-cat="{cat}">
  <div class="card-header" style="border-left:4px solid {cfg['color']}">
    <div class="ticker-row">
      <span class="ticker">{r['ticker']}</span>
      <span class="sector">{r['sector']}</span>
    </div>
    <div class="price-row">
      <span class="price">${fmt_num(r['price'], precision=2)}</span>
      <span class="score-chip" style="background:{cfg['color']}22;color:{cfg['color']}">Score {sc}</span>
    </div>
    {above_badge}{canslim_badge}
  </div>
  <div class="card-metrics">
    <div class="metric"><span class="m-label">RS</span>
      <span class="m-val">{fmt_num(r['rs'], plus=True)}</span></div>
    <div class="metric"><span class="m-label">EPS%</span>
      <span class="m-val">{fmt_num(r['eps'], suffix='%', precision=0, plus=True)}</span></div>
    <div class="metric"><span class="m-label">3M Ret</span>
      <span class="m-val">{fmt_num(r['ret3m'], suffix='%', precision=1, plus=True)}</span></div>
    <div class="metric"><span class="m-label">vs 52H</span>
      <span class="m-val">{fmt_num(r['dist52h'], suffix='%', precision=1)}</span></div>
    <div class="metric"><span class="m-label">vs 50MA</span>
      <span class="m-val">{fmt_num(r['pct50ma'], suffix='%', precision=1, plus=True)}</span></div>
    <div class="metric"><span class="m-label">RVOL</span>
      <span class="m-val">{fmt_num(r['rvol'], precision=2)}</span></div>
  </div>
  <div class="card-reasons"><ul>{reasons_html}</ul></div>
</div>"""


def build_html(results, by_cat, run_time):
    sections_html = ""
    for cat in ["Day Trade", "Swing Trade", "Long Term", "Avoid"]:
        cfg   = CAT_CONFIG[cat]
        items = sorted(by_cat[cat],
                       key=lambda x: -(x["scores"].get(cat, 0) if x["scores"] else 0))
        cards = "".join(build_card(r) for r in items)
        sections_html += f"""
<section class="category-section" id="{cat.replace(' ','_')}">
  <div class="section-header">
    <span class="cat-icon">{cfg['icon']}</span>
    <div>
      <h2 style="color:{cfg['color']}">{cat}
        <span class="count">({len(items)})</span></h2>
      <p class="cat-desc">{cfg['desc']}</p>
    </div>
  </div>
  <div class="card-grid">{cards}</div>
</section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock Categorizer — {run_time}</title>
<style>
  :root {{
    --bg:#0B0F1A; --surface:#131929; --border:#1E2D45;
    --text:#E2E8F0; --muted:#64748B;
    --day:#F59E0B; --swing:#10B981; --lt:#6366F1; --avoid:#EF4444;
    --font:'Inter','Segoe UI',system-ui,sans-serif;
    --mono:'JetBrains Mono','Fira Code',monospace;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:var(--font);padding-bottom:60px}}

  .topbar{{background:var(--surface);border-bottom:1px solid var(--border);
    padding:14px 32px;display:flex;align-items:center;
    justify-content:space-between;position:sticky;top:0;z-index:100}}
  .topbar h1{{font-size:1.1rem;letter-spacing:.08em;font-weight:700;color:#fff}}
  .topbar .ts{{font-size:.75rem;color:var(--muted);font-family:var(--mono)}}
  .nav-pills{{display:flex;gap:8px;flex-wrap:wrap}}
  .nav-pill{{padding:5px 14px;border-radius:20px;font-size:.78rem;font-weight:600;
    border:1px solid;cursor:pointer;transition:all .15s;background:transparent}}
  .nav-pill:hover{{opacity:.85}}
  .np-day{{color:var(--day);border-color:var(--day)}}
  .np-swing{{color:var(--swing);border-color:var(--swing)}}
  .np-lt{{color:var(--lt);border-color:var(--lt)}}
  .np-avoid{{color:var(--avoid);border-color:var(--avoid)}}

  .summary{{display:flex;gap:16px;padding:20px 32px;flex-wrap:wrap;
    border-bottom:1px solid var(--border)}}
  .stat-box{{flex:1;min-width:130px;background:var(--surface);
    border:1px solid var(--border);border-radius:10px;padding:14px 18px}}
  .stat-box .num{{font-size:2rem;font-weight:800;font-family:var(--mono)}}
  .stat-box .lbl{{font-size:.72rem;color:var(--muted);text-transform:uppercase;
    letter-spacing:.06em;margin-top:2px}}

  .category-section{{padding:32px 32px 0}}
  .section-header{{display:flex;align-items:flex-start;gap:16px;margin-bottom:20px;
    padding-bottom:16px;border-bottom:1px solid var(--border)}}
  .cat-icon{{font-size:2rem;line-height:1}}
  .section-header h2{{font-size:1.3rem;font-weight:800}}
  .count{{font-size:1rem;font-weight:500;opacity:.7}}
  .cat-desc{{font-size:.8rem;color:var(--muted);margin-top:3px}}

  .card-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}}
  .card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;
    overflow:hidden;transition:transform .15s,box-shadow .15s}}
  .card:hover{{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.4)}}
  .card-header{{padding:14px 16px 10px;border-bottom:1px solid var(--border)}}
  .ticker-row{{display:flex;justify-content:space-between;align-items:baseline}}
  .ticker{{font-size:1.2rem;font-weight:800;font-family:var(--mono);letter-spacing:.04em}}
  .sector{{font-size:.7rem;color:var(--muted);text-transform:uppercase}}
  .price-row{{display:flex;justify-content:space-between;align-items:center;margin-top:6px}}
  .price{{font-size:1rem;font-weight:600;font-family:var(--mono)}}
  .score-chip{{font-size:.72rem;font-weight:700;padding:2px 10px;
    border-radius:20px;font-family:var(--mono)}}
  .badge-yes{{display:inline-block;margin-top:6px;font-size:.68rem;font-weight:700;
    color:#10B981;background:#10B98122;padding:2px 8px;border-radius:20px}}
  .badge-cs{{display:inline-block;margin-top:6px;margin-left:4px;font-size:.68rem;
    font-weight:700;color:#F59E0B;background:#F59E0B22;padding:2px 8px;border-radius:20px}}

  .card-metrics{{display:grid;grid-template-columns:repeat(3,1fr)}}
  .metric{{padding:8px 12px;border-right:1px solid var(--border);
    border-bottom:1px solid var(--border)}}
  .metric:nth-child(3n){{border-right:none}}
  .m-label{{font-size:.65rem;color:var(--muted);text-transform:uppercase;
    letter-spacing:.04em;display:block}}
  .m-val{{font-size:.88rem;font-weight:700;font-family:var(--mono);display:block;margin-top:2px}}

  .card-reasons{{padding:10px 16px 12px}}
  .card-reasons ul{{list-style:none}}
  .card-reasons li{{font-size:.75rem;color:#94A3B8;padding:2px 0 2px 12px;position:relative}}
  .card-reasons li::before{{content:'›';position:absolute;left:0;color:var(--muted)}}
</style>
</head>
<body>
<div class="topbar">
  <h1>⬡ STOCK CATEGORIZER</h1>
  <nav class="nav-pills">
    <button class="nav-pill np-day"
      onclick="document.getElementById('Day_Trade').scrollIntoView({{behavior:'smooth'}})">⚡ Day Trade</button>
    <button class="nav-pill np-swing"
      onclick="document.getElementById('Swing_Trade').scrollIntoView({{behavior:'smooth'}})">📈 Swing Trade</button>
    <button class="nav-pill np-lt"
      onclick="document.getElementById('Long_Term').scrollIntoView({{behavior:'smooth'}})">🏦 Long Term</button>
    <button class="nav-pill np-avoid"
      onclick="document.getElementById('Avoid').scrollIntoView({{behavior:'smooth'}})">🚫 Avoid</button>
  </nav>
  <span class="ts">{run_time}</span>
</div>

<div class="summary">
  <div class="stat-box">
    <div class="num" style="color:var(--day)">{len(by_cat['Day Trade'])}</div>
    <div class="lbl">Day Trade</div>
  </div>
  <div class="stat-box">
    <div class="num" style="color:var(--swing)">{len(by_cat['Swing Trade'])}</div>
    <div class="lbl">Swing Trade</div>
  </div>
  <div class="stat-box">
    <div class="num" style="color:var(--lt)">{len(by_cat['Long Term'])}</div>
    <div class="lbl">Long Term</div>
  </div>
  <div class="stat-box">
    <div class="num" style="color:var(--avoid)">{len(by_cat['Avoid'])}</div>
    <div class="lbl">Avoid</div>
  </div>
  <div class="stat-box">
    <div class="num" style="color:#fff">{len(results)}</div>
    <div class="lbl">Total</div>
  </div>
</div>

{sections_html}
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# CSV EXPORT  — full raw metrics + category columns, Excel-ready
# ─────────────────────────────────────────────────────────────────────────────

# Ordered column spec: (csv_header, raw_row_key, type_hint)
#   type_hint  f=float  s=string  b=bool  i=int  p=pct(already %)
CSV_COLUMNS = [
    # ── Identity ──────────────────────────────────────────────────────────────
    ("Ticker",              "Ticker",                       "s"),
    ("Sector",              "Sector",                       "s"),
    ("Earnings Date",       "EarningsDate",                 "s"),
    ("Category",            "_category",                    "s"),   # injected
    ("Category Score",      "_cat_score",                   "i"),   # injected
    # ── Price ─────────────────────────────────────────────────────────────────
    ("Current Price",       "Current Price",                "f"),
    ("VWAP",                "VWAP",                         "f"),
    ("Pre-Market Low",      "Pre-Market Low",               "f"),
    ("Pre-Market High",     "Pre-Market High",              "f"),
    ("Today Low",           "_today_low",                   "f"),
    ("Today High",          "_today_high",                  "f"),
    ("Prev-Day Low",        "Prev-Day Low",                 "f"),
    ("Prev-Day High",       "Prev-Day High",                "f"),
    # ── 52-Week range ─────────────────────────────────────────────────────────
    ("52W Low",             "52W Low",                      "f"),
    ("52W High",            "52W High",                     "f"),
    ("Prior 52W Low",       "_prior_52w_low",               "f"),
    ("Prior 52W High",      "_prior_52w_high",              "f"),
    ("% From 52W Low",      "% From 52W Low",               "p"),
    ("Dist From 52W High%", "Dist_52W_High%",               "p"),
    # ── Moving averages ───────────────────────────────────────────────────────
    ("200MA",               "200MA",                        "f"),
    ("50MA",                "50MA",                         "f"),
    ("21EMA",               "21EMA",                        "f"),
    ("8EMA",                "8EMA",                         "f"),
    ("% From 50MA",         "% From 50MA",                  "p"),
    ("% From 21EMA",        "% From 21EMA",                 "p"),
    ("% From 8EMA",         "% From 8EMA",                  "p"),
    ("Above All MAs",       "Above 8EMA/21EMA/50MA/200MA",  "b"),
    # ── Trend / volatility ────────────────────────────────────────────────────
    ("ADX 14",              "ADX_14",                       "f"),
    ("Trend Strength",      "Trend_Strength",               "s"),
    ("ATR Shrinking",       "ATR Shrinking",                "b"),
    # ── Volume ────────────────────────────────────────────────────────────────
    ("RVOL",                "RVOL",                         "f"),
    ("Vol vs 20D Avg",      "Vol_vs_20D",                   "f"),
    ("Volume Drying Up",    "VolumeDryingUp",               "b"),
    # ── Relative strength ─────────────────────────────────────────────────────
    ("Stock Return 3M%",    "stock_return_3m",              "p"),
    ("QQQ Return 3M%",      "qqq_return_3m",                "p"),
    ("RS vs QQQ",           "RS",                           "f"),
    # ── Fundamentals ──────────────────────────────────────────────────────────
    ("Revenue Growth%",     "Revenue",                      "p"),
    ("EPS Growth%",         "EPS_Growth%",                  "p"),
    ("Inst Ownership%",     "Inst_Own%",                    "f"),
    ("CANSLIM Pass",        "CANSLIM_Pass",                 "b"),
    # ── Category scores (all four) ────────────────────────────────────────────
    ("Score DayTrade",      "_score_Day Trade",             "i"),   # injected
    ("Score Swing",         "_score_Swing Trade",           "i"),   # injected
    ("Score LongTerm",      "_score_Long Term",             "i"),   # injected
    ("Score Avoid",         "_score_Avoid",                 "i"),   # injected
    # ── Reasons (top 3) ───────────────────────────────────────────────────────
    ("Reason 1",            "_reason_0",                    "s"),   # injected
    ("Reason 2",            "_reason_1",                    "s"),   # injected
    ("Reason 3",            "_reason_2",                    "s"),   # injected
]


def _fmt_csv(val, type_hint: str) -> str:
    """Convert a raw value to a clean string for CSV."""
    if val is None:
        return ""
    if type_hint == "b":
        if isinstance(val, bool):
            return "TRUE" if val else "FALSE"
        return "TRUE" if str(val).strip().lower() in ("true", "1", "yes") else "FALSE"
    if type_hint in ("f", "p", "i"):
        try:
            v = float(val)
            if math.isnan(v):
                return ""
            if type_hint == "i":
                return str(int(round(v)))
            if type_hint == "p":
                return f"{v:.2f}"   # percentage already stored as e.g. 22.34
            return f"{v:.4f}" if abs(v) < 1 else f"{v:.2f}"
        except (TypeError, ValueError):
            return ""
    # string
    s = str(val)
    return "" if s in ("N/A", "None", "nan") else s


def write_csv(raw_rows: list[dict], classify_results: list[dict], path: str):
    """
    Write the full metrics CSV.

    raw_rows        — list of dicts from get_metrics()
    classify_results — parallel list of dicts from the classify() pipeline
                       (must be same order as raw_rows)
    path            — output file path
    """
    # Build a lookup: ticker → classify result
    clf_by_ticker = {r["ticker"]: r for r in classify_results}

    headers = [col[0] for col in CSV_COLUMNS]

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        # utf-8-sig adds the BOM so Excel auto-detects UTF-8 without importing wizard
        writer = csv.writer(fh)
        writer.writerow(headers)

        for row in raw_rows:
            ticker = row.get("Ticker", "")
            clf    = clf_by_ticker.get(ticker, {})
            scores = clf.get("scores") or {}
            reasons = clf.get("reasons") or []
            cat    = clf.get("category", "")
            cat_score = scores.get(cat, "") if scores else ""

            # Inject derived keys into a merged dict so CSV_COLUMNS can reference them
            merged = dict(row)   # shallow copy of raw metrics
            merged["_category"]            = cat
            merged["_cat_score"]           = cat_score
            merged["_score_Day Trade"]     = scores.get("Day Trade", "")
            merged["_score_Swing Trade"]   = scores.get("Swing Trade", "")
            merged["_score_Long Term"]     = scores.get("Long Term", "")
            merged["_score_Avoid"]         = scores.get("Avoid", "")
            merged["_reason_0"]            = reasons[0] if len(reasons) > 0 else ""
            merged["_reason_1"]            = reasons[1] if len(reasons) > 1 else ""
            merged["_reason_2"]            = reasons[2] if len(reasons) > 2 else ""

            csv_row = [
                _fmt_csv(merged.get(raw_key), type_hint)
                for _, raw_key, type_hint in CSV_COLUMNS
            ]
            writer.writerow(csv_row)

    log.info("CSV written → %s  (%d rows, %d columns)", path, len(raw_rows), len(headers))


# ─────────────────────────────────────────────────────────────────────────────
# CONSOLE SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
def print_summary(by_cat):
    print(f"\n{'='*70}")
    print(f"  STOCK CATEGORIZER  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")
    for cat in ["Day Trade", "Swing Trade", "Long Term", "Avoid"]:
        items = by_cat[cat]
        print(f"\n{'─'*60}")
        print(f"  {cat.upper()}  ({len(items)} stocks)")
        print(f"{'─'*60}")
        for r in sorted(items, key=lambda x: -(x["scores"].get(cat, 0) if x["scores"] else 0)):
            sc    = r["scores"].get(cat, "ETF") if r["scores"] else "ETF"
            top_r = r["reasons"][0] if r["reasons"] else ""
            print(f"  {r['ticker']:<8}  Score:{str(sc):<5}  {top_r}")
    print(f"\n{'='*70}\n")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    tickers = sys.argv[1:] if len(sys.argv) > 1 else WATCHLIST

    log.info("Starting categorizer for %d tickers: %s", len(tickers), tickers)
    raw_rows = fetch_all(tickers)

    results = []
    for row in raw_rows:
        cat, scores, reasons = classify(row)
        canslim = row.get("CANSLIM_Pass")
        results.append({
            "ticker":   row["Ticker"],
            "sector":   row.get("Sector", ""),
            "price":    safe(row.get("Current Price"), 0),
            "category": cat,
            "scores":   scores,
            "reasons":  reasons,
            "rs":       row.get("RS"),
            "eps":      row.get("EPS_Growth%"),
            "dist52h":  row.get("Dist_52W_High%"),
            "pct50ma":  row.get("% From 50MA"),
            "rvol":     row.get("RVOL"),
            "ret3m":    row.get("stock_return_3m"),
            "above_all": above_all_mas(row),
            "canslim":  canslim is True or str(canslim) == "True",
        })

    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    print_summary(by_cat)

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── HTML dashboard ────────────────────────────────────────────────────────
    html = build_html(results, by_cat, run_time)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("Dashboard written → %s", OUTPUT_HTML)

    # ── CSV export (full metrics + category) ──────────────────────────────────
    write_csv(raw_rows, results, OUTPUT_CSV)
    log.info("Open %s in Excel — columns are BOM-encoded UTF-8, no import wizard needed", OUTPUT_CSV)


if __name__ == "__main__":
    main()