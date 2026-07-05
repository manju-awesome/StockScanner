"""
stock_categorizer.py  — Day Trade Edition
──────────────────────────────────────────
Fetches live metrics for every ticker in WATCHLIST via get_metrics(),
then scores each for intraday tradability using professional confluences:

  • RVOL (relative volume vs 50-day avg)
  • Gap Up / Gap Down detection with magnitude
  • Intraday trend direction (price vs VWAP, VWAP vs prior close)
  • Price vs key intraday MAs (9EMA, 20EMA on 1-min chart)
  • Pre-market range & momentum
  • ATR-based expected move
  • Prev-day high/low as key levels
  • Opening Range Breakout (ORB) potential
  • Market context (SPY / QQQ trend)

Output per run
  stock_daytrade.html    — sortable dashboard with confluences
  stock_daytrade.csv     — full metrics, Excel-ready (UTF-8 BOM)
  Console summary

Usage
  python stock_categorizer.py              # full watchlist
  python stock_categorizer.py NVDA TSLA    # override tickers
"""

# ─────────────────────────────────────────────────────────────────────────────
import csv
import math
import sys
import logging
from datetime import datetime, time as dtime
from collections import defaultdict
from zoneinfo import ZoneInfo

import yfinance as yf

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
MARKET_TZ       = ZoneInfo("America/New_York")
PREMARKET_START = dtime(4,  0)
MARKET_OPEN     = dtime(9, 30)
ORB_END         = dtime(9, 45)   # 15-min Opening Range

OUTPUT_HTML = "stock_daytrade.html"
OUTPUT_CSV  = "stock_daytrade.csv"

# ── Watchlist ─────────────────────────────────────────────────────────────────
# Market Indices / Benchmarks
INDICES = ["SPY", "QQQ", "IWM", "SPX","DRAM","NVDA","AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA","AVGO","HOOD"]          # SPX = ^GSPC via yfinance

# ETFs in focus
ETFS = ["DRAM", "GLD"]

'''
# Magnificent 7
MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

# Existing watchlist (minus duplicates)
OTHERS = [
    "AAOI", "AMD",  "ASML", "AVGO", "BE",   "CIEN", "COHR",
    "CRDO", "CRWV", "FLY",  "GLW",  "HIMS",
    "HOOD", "INOD", "INTC", "IONQ", "IREN", "LITE", "LLY",
    "MRVL", "MU",   "NVTS", "OKLO", "PLTR", "RGTI",
    "SMCI", "SNDK", "SOFI", "SPCX", "TSM",  "USAR",
]
'''
# Ticker normalisation: SPX needs ^GSPC for yfinance
TICKER_MAP = {"SPX": "^GSPC"}

WATCHLIST_DISPLAY = list(INDICES)    # indices only: SPY, QQQ, IWM, SPX

def yf_ticker(display: str) -> str:
    return TICKER_MAP.get(display, display)

# Group labels for dashboard
def ticker_group(t: str) -> str:
    if t in INDICES:        return "Index"
    if t in ETFS:           return "ETF"
    if t in MAG7:           return "Mag7"
    return "Watchlist"

log = logging.getLogger("dt_cat")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")

# ─────────────────────────────────────────────────────────────────────────────
# ATR HELPER
# ─────────────────────────────────────────────────────────────────────────────
def calculate_atr(df, period: int = 14):
    import pandas as pd
    high  = df["High"]
    low   = df["Low"]
    prev  = df["Close"].shift(1)
    tr    = pd.concat([(high - low),
                       (high - prev).abs(),
                       (low  - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ─────────────────────────────────────────────────────────────────────────────
# MARKET CONTEXT  — fetch SPY / QQQ once
# ─────────────────────────────────────────────────────────────────────────────
def fetch_market_context() -> dict:
    ctx = {"spy_trend": "N/A", "qqq_trend": "N/A",
           "spy_vwap_bias": "N/A", "qqq_vwap_bias": "N/A"}
    for sym, key in [("SPY", "spy"), ("QQQ", "qqq")]:
        try:
            t    = yf.Ticker(sym)
            d    = t.history(period="5d", interval="1d", auto_adjust=False)
            intr = t.history(period="1d", interval="1m", prepost=False, auto_adjust=False)
            if len(d) >= 5:
                ma5 = d["Close"].tail(5).mean()
                last_close = float(d["Close"].iloc[-1])
                ctx[f"{key}_trend"] = "Bullish" if last_close > ma5 else "Bearish"
            if not intr.empty:
                typical = (intr["High"] + intr["Low"] + intr["Close"]) / 3
                vwap = float((typical * intr["Volume"]).sum() / intr["Volume"].sum())
                last = float(intr["Close"].iloc[-1])
                ctx[f"{key}_vwap_bias"] = "Above VWAP" if last > vwap else "Below VWAP"
        except Exception as e:
            log.warning("Market context %s failed: %s", sym, e)
    log.info("Market context: SPY=%s %s | QQQ=%s %s",
             ctx["spy_trend"], ctx["spy_vwap_bias"],
             ctx["qqq_trend"], ctx["qqq_vwap_bias"])
    return ctx

# ─────────────────────────────────────────────────────────────────────────────
# get_metrics  — day-trade focused
# ─────────────────────────────────────────────────────────────────────────────
def get_metrics(display_ticker: str) -> dict:
    ticker = yf_ticker(display_ticker)
    row    = {"Ticker": display_ticker, "_yf_ticker": ticker,
              "Group": ticker_group(display_ticker)}
    t      = yf.Ticker(ticker)

    # ── Info / Sector ─────────────────────────────────────────────────────────
    try:
        info = t.info
        row["Sector"] = (info.get("sector") or
                         info.get("quoteType") or "N/A")
    except Exception:
        info = {}
        row["Sector"] = "N/A"

    # ── Current price ─────────────────────────────────────────────────────────
    try:
        row["Current Price"] = round(float(t.fast_info["last_price"]), 2)
    except Exception:
        row["Current Price"] = None

    # ── Daily history (60 days) ───────────────────────────────────────────────
    try:
        daily = t.history(period="60d", interval="1d", auto_adjust=False)
        if not daily.empty:
            today_et    = datetime.now(MARKET_TZ).date()
            daily_dates = (daily.index.tz_convert(MARKET_TZ).date
                           if daily.index.tz else daily.index.date)

            # Exclude today's partial candle for prior-session metrics
            is_today = daily_dates[-1] == today_et
            prior    = daily.iloc[:-1] if is_today else daily

            prev_close = float(prior["Close"].iloc[-1]) if len(prior) else None
            prev_high  = float(prior["High"].iloc[-1])  if len(prior) else None
            prev_low   = float(prior["Low"].iloc[-1])   if len(prior) else None
            row["Prev Close"]    = round(prev_close, 2) if prev_close else None
            row["Prev-Day High"] = round(prev_high,  2) if prev_high  else None
            row["Prev-Day Low"]  = round(prev_low,   2) if prev_low   else None

            # ── ATR(14) — daily ───────────────────────────────────────────────
            if len(prior) >= 15:
                daily["ATR14"] = calculate_atr(daily)
                atr14 = float(daily["ATR14"].iloc[-1])
                row["ATR14_daily"]       = round(atr14, 2)
                price_for_atr            = row["Current Price"] or prev_close or 1
                row["ATR14_pct"]         = round(atr14 / price_for_atr * 100, 2)
            else:
                row["ATR14_daily"] = None
                row["ATR14_pct"]   = None

            # ── Volume baseline (50-day avg) ──────────────────────────────────
            vol_series = prior["Volume"]
            row["Avg50_Vol"] = round(float(vol_series.tail(50).mean()), 0) if len(vol_series) >= 10 else None
            row["Avg20_Vol"] = round(float(vol_series.tail(20).mean()), 0) if len(vol_series) >= 10 else None

            # ── 20-day high/low (key S/R levels) ─────────────────────────────
            row["20D High"] = round(float(prior["High"].tail(20).max()), 2)  if len(prior) >= 20 else None
            row["20D Low"]  = round(float(prior["Low"].tail(20).min()),  2)  if len(prior) >= 20 else None

            # ── Daily MAs (for trend backdrop) ───────────────────────────────
            row["50MA"]  = round(float(daily["Close"].rolling(50).mean().iloc[-1]), 2) if len(daily) >= 50 else None
            row["20MA"]  = round(float(daily["Close"].rolling(20).mean().iloc[-1]), 2) if len(daily) >= 20 else None
            row["9EMA_daily"] = round(float(daily["Close"].ewm(span=9, adjust=False).mean().iloc[-1]), 2)

            # ── Daily trend (price vs 20MA vs 50MA) ──────────────────────────
            cp = row.get("Current Price") or 0
            ma20 = row.get("20MA") or 0
            ma50 = row.get("50MA") or 0
            if cp > 0 and ma20 > 0 and ma50 > 0:
                if cp > ma20 > ma50:
                    row["Daily Trend"] = "Uptrend"
                elif cp < ma20 < ma50:
                    row["Daily Trend"] = "Downtrend"
                elif cp > ma20 and ma20 < ma50:
                    row["Daily Trend"] = "Recovery"
                elif cp < ma20 and ma20 > ma50:
                    row["Daily Trend"] = "Pullback"
                else:
                    row["Daily Trend"] = "Ranging"
            else:
                row["Daily Trend"] = "N/A"

            # ── 52W range ─────────────────────────────────────────────────────
            hist1y = t.history(period="1y", interval="1d", auto_adjust=False)
            if not hist1y.empty:
                row["52W High"] = round(float(hist1y["High"].max()),  2)
                row["52W Low"]  = round(float(hist1y["Low"].min()),   2)
                if row["52W High"] and cp > 0:
                    row["Dist_52W_High%"] = round((cp / row["52W High"] - 1) * 100, 1)
                else:
                    row["Dist_52W_High%"] = None
            else:
                row["52W High"] = row["52W Low"] = row["Dist_52W_High%"] = None

        else:
            for k in ("Prev Close","Prev-Day High","Prev-Day Low","ATR14_daily",
                      "ATR14_pct","Avg50_Vol","Avg20_Vol","20D High","20D Low",
                      "50MA","20MA","9EMA_daily","Daily Trend",
                      "52W High","52W Low","Dist_52W_High%"):
                row[k] = None

    except Exception as e:
        log.warning("%s: daily history failed (%s)", display_ticker, e)
        for k in ("Prev Close","Prev-Day High","Prev-Day Low","ATR14_daily",
                  "ATR14_pct","Avg50_Vol","Avg20_Vol","20D High","20D Low",
                  "50MA","20MA","9EMA_daily","Daily Trend",
                  "52W High","52W Low","Dist_52W_High%"):
            row[k] = None

    # ── Intraday 1-min (pre-market + regular session) ────────────────────────
    try:
        intra = t.history(period="1d", interval="1m", prepost=True, auto_adjust=False)
        if not intra.empty:
            idx_et = (intra.index.tz_convert(MARKET_TZ)
                      if intra.index.tz else intra.index.tz_localize(MARKET_TZ))
            times = idx_et.time

            # Pre-market bars
            pm_mask = [PREMARKET_START <= tm < MARKET_OPEN for tm in times]
            pm_df   = intra[pm_mask]
            row["PM Low"]       = round(float(pm_df["Low"].min()),   2) if not pm_df.empty else None
            row["PM High"]      = round(float(pm_df["High"].max()),  2) if not pm_df.empty else None
            row["PM Last"]      = round(float(pm_df["Close"].iloc[-1]), 2) if not pm_df.empty else None
            row["PM Vol"]       = int(pm_df["Volume"].sum())              if not pm_df.empty else 0

            # Gap calculation vs prev close
            prev_close = row.get("Prev Close")
            if prev_close and row["PM Last"]:
                gap_pct = (row["PM Last"] - prev_close) / prev_close * 100
                row["Gap%"]      = round(gap_pct, 2)
                row["Gap Type"]  = ("Gap Up"   if gap_pct >=  0.5 else
                                    "Gap Down"  if gap_pct <= -0.5 else
                                    "Flat Open")
            elif prev_close and row.get("Current Price"):
                gap_pct = (row["Current Price"] - prev_close) / prev_close * 100
                row["Gap%"]      = round(gap_pct, 2)
                row["Gap Type"]  = ("Gap Up"  if gap_pct >=  0.5 else
                                    "Gap Down" if gap_pct <= -0.5 else
                                    "Flat Open")
            else:
                row["Gap%"]     = None
                row["Gap Type"] = "N/A"

            # Regular session bars
            reg_mask = [tm >= MARKET_OPEN for tm in times]
            reg_df   = intra[reg_mask]

            if not reg_df.empty:
                # VWAP (cumulative, session-only)
                vol_sum = reg_df["Volume"].sum()
                if vol_sum > 0:
                    typical = (reg_df["High"] + reg_df["Low"] + reg_df["Close"]) / 3
                    row["VWAP"] = round(float((typical * reg_df["Volume"]).sum() / vol_sum), 2)
                else:
                    row["VWAP"] = None

                # Today's session volume → RVOL vs 50-day avg
                session_vol = int(reg_df["Volume"].sum())
                row["Session Vol"] = session_vol
                avg50 = row.get("Avg50_Vol")
                if avg50 and avg50 > 0:
                    # Scale to full-day equivalent: current time / 6.5 hours
                    now_et = datetime.now(MARKET_TZ)
                    mins_elapsed = max(1, (now_et.hour * 60 + now_et.minute) - 9 * 60 - 30)
                    day_frac = min(mins_elapsed / 390, 1.0)   # 390 min = full session
                    proj_vol = session_vol / day_frac if day_frac > 0 else session_vol
                    row["RVOL"]      = round(proj_vol / avg50, 2)
                    row["Vol_vs_20D"] = round(proj_vol / row["Avg20_Vol"], 2) if row.get("Avg20_Vol") else None
                else:
                    row["RVOL"]       = None
                    row["Vol_vs_20D"] = None

                # Opening Range (first 15 min)
                orb_mask = [MARKET_OPEN <= tm <= ORB_END for tm in times]
                orb_df   = intra[orb_mask]
                if not orb_df.empty:
                    row["ORB High"] = round(float(orb_df["High"].max()), 2)
                    row["ORB Low"]  = round(float(orb_df["Low"].min()),  2)
                else:
                    row["ORB High"] = row["ORB Low"] = None

                # Intraday range (today full session)
                row["_today_high"] = round(float(reg_df["High"].max()), 2)
                row["_today_low"]  = round(float(reg_df["Low"].min()),  2)
                row["Intraday Range%"] = round(
                    (row["_today_high"] - row["_today_low"]) / row["_today_low"] * 100, 2
                ) if row["_today_low"] > 0 else None

                # Last price / session close
                row["Last Price"] = round(float(reg_df["Close"].iloc[-1]), 2)

                # Intraday trend: price vs VWAP + direction of VWAP vs prev close
                last    = row["Last Price"]
                vwap    = row.get("VWAP")
                pc      = row.get("Prev Close")
                if last and vwap and pc:
                    above_vwap = last > vwap
                    vwap_vs_pc = vwap > pc        # VWAP above/below prior close = bullish/bearish context
                    if above_vwap and vwap_vs_pc:
                        row["Intraday Trend"] = "Bull"
                    elif above_vwap and not vwap_vs_pc:
                        row["Intraday Trend"] = "Recovering"
                    elif not above_vwap and vwap_vs_pc:
                        row["Intraday Trend"] = "Pulling Back"
                    else:
                        row["Intraday Trend"] = "Bear"
                else:
                    row["Intraday Trend"] = "N/A"

                # 9EMA and 20EMA on 1-min chart (last bar)
                row["9EMA_1m"]  = round(float(reg_df["Close"].ewm(span=9,  adjust=False).mean().iloc[-1]), 2)
                row["20EMA_1m"] = round(float(reg_df["Close"].ewm(span=20, adjust=False).mean().iloc[-1]), 2)

                # Price vs 1-min MAs
                if last:
                    row["Above 9EMA_1m"]  = last > row["9EMA_1m"]
                    row["Above 20EMA_1m"] = last > row["20EMA_1m"]
                else:
                    row["Above 9EMA_1m"]  = None
                    row["Above 20EMA_1m"] = None

                # Price vs VWAP distance %
                if vwap and vwap > 0 and last:
                    row["Price vs VWAP%"] = round((last - vwap) / vwap * 100, 2)
                else:
                    row["Price vs VWAP%"] = None

                # Price vs Prev-Day High / Low
                pdh = row.get("Prev-Day High")
                pdl = row.get("Prev-Day Low")
                if last and pdh:
                    row["vs PDH%"] = round((last - pdh) / pdh * 100, 2)
                else:
                    row["vs PDH%"] = None
                if last and pdl:
                    row["vs PDL%"] = round((last - pdl) / pdl * 100, 2)
                else:
                    row["vs PDL%"] = None

            else:
                for k in ("VWAP","Session Vol","RVOL","Vol_vs_20D",
                          "ORB High","ORB Low","_today_high","_today_low",
                          "Intraday Range%","Last Price","Intraday Trend",
                          "9EMA_1m","20EMA_1m","Above 9EMA_1m","Above 20EMA_1m",
                          "Price vs VWAP%","vs PDH%","vs PDL%"):
                    row[k] = None
        else:
            for k in ("PM Low","PM High","PM Last","PM Vol","Gap%","Gap Type",
                      "VWAP","Session Vol","RVOL","Vol_vs_20D",
                      "ORB High","ORB Low","_today_high","_today_low",
                      "Intraday Range%","Last Price","Intraday Trend",
                      "9EMA_1m","20EMA_1m","Above 9EMA_1m","Above 20EMA_1m",
                      "Price vs VWAP%","vs PDH%","vs PDL%"):
                row[k] = None

    except Exception as e:
        log.warning("%s: intraday failed (%s)", display_ticker, e)
        for k in ("PM Low","PM High","PM Last","PM Vol","Gap%","Gap Type",
                  "VWAP","Session Vol","RVOL","Vol_vs_20D",
                  "ORB High","ORB Low","_today_high","_today_low",
                  "Intraday Range%","Last Price","Intraday Trend",
                  "9EMA_1m","20EMA_1m","Above 9EMA_1m","Above 20EMA_1m",
                  "Price vs VWAP%","vs PDH%","vs PDL%"):
            row[k] = None

    return row


# ─────────────────────────────────────────────────────────────────────────────
# DATA PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
def fetch_all(display_tickers: list) -> list:
    rows = []
    for i, ticker in enumerate(display_tickers, 1):
        log.info("[%d/%d] %s …", i, len(display_tickers), ticker)
        try:
            rows.append(get_metrics(ticker))
        except Exception as e:
            log.error("%s: skipped (%s)", ticker, e)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# SAFE HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def safe(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default

def bool_val(v):
    if isinstance(v, bool):   return v
    if v is None:             return False
    return str(v).strip().lower() in ("true", "1", "yes")


# ─────────────────────────────────────────────────────────────────────────────
# DAY TRADE CONFLUENCE SCORER
# Professional confluences used by active traders:
#   1. RVOL  — institutional / retail interest
#   2. Gap   — overnight catalyst creates momentum
#   3. VWAP  — institutional benchmark; price above = bullish flow
#   4. ORB   — Opening Range Breakout; high-probability momentum entry
#   5. PDH/PDL — key prior-day levels; breakout/breakdown signals
#   6. 9EMA_1m — short-term momentum MA on intraday chart
#   7. Daily Trend alignment — trade with the bigger picture
#   8. ATR   — expected intraday move, filters low-volatility names
#   9. Pre-market volume — early conviction before the open
#  10. Intraday Range   — actual movement available to trade
# ─────────────────────────────────────────────────────────────────────────────
def score_daytrade(row, market_ctx: dict) -> tuple:
    score      = 0
    confluences = []       # ≥ 3 professional confluences required

    # ── 1. RVOL ──────────────────────────────────────────────────────────────
    rvol = safe(row.get("RVOL"), 0)
    if rvol >= 3.0:
        score += 30; confluences.append(f"🔥 RVOL {rvol:.1f}x — extreme volume surge")
    elif rvol >= 2.0:
        score += 22; confluences.append(f"⚡ RVOL {rvol:.1f}x — strong accumulation")
    elif rvol >= 1.5:
        score += 14; confluences.append(f"📊 RVOL {rvol:.1f}x — above-avg volume")
    elif rvol >= 1.0:
        score += 6
    elif rvol > 0:
        score -= 5   # below-avg volume = low conviction

    # ── 2. Gap Up / Down ─────────────────────────────────────────────────────
    gap_pct  = safe(row.get("Gap%"), 0)
    gap_type = row.get("Gap Type", "N/A")
    if gap_type == "Gap Up":
        if gap_pct >= 5:
            score += 28; confluences.append(f"🚀 Gap Up +{gap_pct:.1f}% — high catalyst")
        elif gap_pct >= 2:
            score += 20; confluences.append(f"↑ Gap Up +{gap_pct:.1f}%")
        else:
            score += 10; confluences.append(f"↑ Small Gap Up +{gap_pct:.1f}%")
    elif gap_type == "Gap Down":
        if gap_pct <= -5:
            score += 25; confluences.append(f"💥 Gap Down {gap_pct:.1f}% — short candidate")
        elif gap_pct <= -2:
            score += 18; confluences.append(f"↓ Gap Down {gap_pct:.1f}%")
        else:
            score += 8; confluences.append(f"↓ Small Gap Down {gap_pct:.1f}%")
    # Flat open gets no gap score

    # ── 3. VWAP position ─────────────────────────────────────────────────────
    vwap_dist = safe(row.get("Price vs VWAP%"), None)
    intra_trend = row.get("Intraday Trend", "N/A")
    if vwap_dist is not None:
        if vwap_dist >= 0.5:
            score += 18; confluences.append(f"✅ Above VWAP +{vwap_dist:.1f}% (bullish bias)")
        elif vwap_dist >= -0.3:
            score += 10; confluences.append(f"〰 Hugging VWAP ({vwap_dist:+.1f}%) — inflection zone")
        elif vwap_dist <= -1.5:
            score += 12; confluences.append(f"🔻 Below VWAP {vwap_dist:.1f}% (bearish/short bias)")
        else:
            score += 5

    # ── 4. Opening Range Breakout ─────────────────────────────────────────────
    last  = safe(row.get("Last Price") or row.get("Current Price"), 0)
    orb_h = safe(row.get("ORB High"), 0)
    orb_l = safe(row.get("ORB Low"), 0)
    if orb_h > 0 and orb_l > 0 and last > 0:
        if last > orb_h:
            score += 22; confluences.append(f"🎯 ORB Breakout above ${orb_h:.2f}")
        elif last < orb_l:
            score += 20; confluences.append(f"🎯 ORB Breakdown below ${orb_l:.2f}")
        elif orb_h > 0:
            orb_range = (orb_h - orb_l) / orb_l * 100 if orb_l > 0 else 0
            confluences.append(f"📐 Inside ORB range {orb_range:.1f}% (${orb_l:.2f}–${orb_h:.2f})")

    # ── 5. Prev-Day High / Low levels ────────────────────────────────────────
    pdh = safe(row.get("Prev-Day High"), 0)
    pdl = safe(row.get("Prev-Day Low"),  0)
    vs_pdh = safe(row.get("vs PDH%"), None)
    vs_pdl = safe(row.get("vs PDL%"), None)
    if vs_pdh is not None and last > 0 and pdh > 0:
        if 0 <= vs_pdh <= 1.5:
            score += 18; confluences.append(f"📍 Testing Prev-Day High ${pdh:.2f} (+{vs_pdh:.1f}%)")
        elif vs_pdh > 1.5:
            score += 14; confluences.append(f"✅ Above PDH ${pdh:.2f} (+{vs_pdh:.1f}%)")
    if vs_pdl is not None and last > 0 and pdl > 0:
        if -1.5 <= vs_pdl <= 0:
            score += 15; confluences.append(f"📍 Testing Prev-Day Low ${pdl:.2f} ({vs_pdl:.1f}%)")
        elif vs_pdl < -1.5:
            score += 12; confluences.append(f"🔻 Below PDL ${pdl:.2f} ({vs_pdl:.1f}%)")

    # ── 6. 9EMA (1-min) — short-term momentum ────────────────────────────────
    ema9_1m = safe(row.get("9EMA_1m"), 0)
    ema20_1m = safe(row.get("20EMA_1m"), 0)
    a9  = bool_val(row.get("Above 9EMA_1m"))
    a20 = bool_val(row.get("Above 20EMA_1m"))
    if last > 0 and ema9_1m > 0:
        if a9 and a20:
            score += 16; confluences.append("📈 Price > 9EMA & 20EMA (1-min) — momentum aligned")
        elif a9 and not a20:
            score += 8;  confluences.append("↑ Price > 9EMA (1-min) — short-term bullish")
        elif not a9 and not a20:
            score += 10; confluences.append("↓ Price < 9EMA & 20EMA (1-min) — short bias")

    # ── 7. Daily trend alignment ──────────────────────────────────────────────
    daily_trend = row.get("Daily Trend", "N/A")
    if daily_trend == "Uptrend":
        score += 14; confluences.append("🏔 Daily Uptrend (price > 20MA > 50MA) — trend trade long")
    elif daily_trend == "Downtrend":
        score += 12; confluences.append("📉 Daily Downtrend — short-bias intraday")
    elif daily_trend == "Recovery":
        score += 8;  confluences.append("↗ Daily Recovery above 20MA")
    elif daily_trend == "Pullback":
        score += 7;  confluences.append("↘ Healthy Pullback — dip-buy zone")

    # ── 8. ATR — volatility / expected move filter ───────────────────────────
    atr_pct = safe(row.get("ATR14_pct"), 0)
    if atr_pct >= 4:
        score += 16; confluences.append(f"💨 ATR {atr_pct:.1f}% — high daily volatility")
    elif atr_pct >= 2:
        score += 10; confluences.append(f"💨 ATR {atr_pct:.1f}% — tradeable volatility")
    elif atr_pct > 0 and atr_pct < 1:
        score -= 8   # too low-vol for day trading

    # ── 9. Pre-market volume / conviction ────────────────────────────────────
    pm_vol   = safe(row.get("PM Vol"), 0)
    avg50    = safe(row.get("Avg50_Vol"), 1)
    pm_ratio = pm_vol / (avg50 * 0.1) if avg50 > 0 else 0   # PM ~10% of daily avg
    if pm_ratio >= 3:
        score += 14; confluences.append(f"🌅 Heavy pre-mkt volume ({pm_vol:,.0f}) — strong overnight conviction")
    elif pm_ratio >= 1.5:
        score += 8;  confluences.append(f"🌅 Elevated pre-mkt volume ({pm_vol:,.0f})")
    elif pm_ratio >= 0.5:
        score += 4

    # ── 10. Intraday range ────────────────────────────────────────────────────
    ir = safe(row.get("Intraday Range%"), 0)
    if ir >= 6:
        score += 14; confluences.append(f"📏 Wide intraday range {ir:.1f}% — active price discovery")
    elif ir >= 3:
        score += 8;  confluences.append(f"📏 Intraday range {ir:.1f}%")
    elif ir > 0 and ir < 1:
        score -= 5   # very tight, nothing to trade

    # ── Market context alignment ─────────────────────────────────────────────
    spy_trend = market_ctx.get("spy_trend", "N/A")
    spy_vwap  = market_ctx.get("spy_vwap_bias", "N/A")
    gap_t     = row.get("Gap Type", "N/A")
    if spy_trend == "Bullish" and spy_vwap == "Above VWAP":
        if gap_t == "Gap Up" or intra_trend in ("Bull", "Recovering"):
            score += 8; confluences.append("🌐 Market tailwind (SPY bullish + above VWAP)")
    elif spy_trend == "Bearish" and spy_vwap == "Below VWAP":
        if gap_t == "Gap Down" or intra_trend in ("Bear", "Pulling Back"):
            score += 8; confluences.append("🌐 Market headwind (SPY bearish + below VWAP)")

    # ── Grade ─────────────────────────────────────────────────────────────────
    if score >= 100:
        grade = "A+"
    elif score >= 80:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"

    return score, grade, confluences


# ─────────────────────────────────────────────────────────────────────────────
# BUILD RESULT RECORDS
# ─────────────────────────────────────────────────────────────────────────────
def build_results(raw_rows: list, market_ctx: dict) -> list:
    results = []
    for row in raw_rows:
        score, grade, confluences = score_daytrade(row, market_ctx)
        results.append({
            "ticker":         row["Ticker"],
            "group":          row.get("Group", ""),
            "sector":         row.get("Sector", ""),
            "price":          row.get("Last Price") or row.get("Current Price"),
            "score":          score,
            "grade":          grade,
            "confluences":    confluences,
            "gap_type":       row.get("Gap Type", "N/A"),
            "gap_pct":        row.get("Gap%"),
            "rvol":           row.get("RVOL"),
            "intraday_trend": row.get("Intraday Trend", "N/A"),
            "daily_trend":    row.get("Daily Trend", "N/A"),
            "vwap":           row.get("VWAP"),
            "price_vs_vwap":  row.get("Price vs VWAP%"),
            "orb_high":       row.get("ORB High"),
            "orb_low":        row.get("ORB Low"),
            "pdh":            row.get("Prev-Day High"),
            "pdl":            row.get("Prev-Day Low"),
            "atr_pct":        row.get("ATR14_pct"),
            "ir_pct":         row.get("Intraday Range%"),
            "pm_vol":         row.get("PM Vol"),
            "pm_high":        row.get("PM High"),
            "pm_low":         row.get("PM Low"),
            "dist_52h":       row.get("Dist_52W_High%"),
            "above_9ema_1m":  row.get("Above 9EMA_1m"),
            "above_20ema_1m": row.get("Above 20EMA_1m"),
            "above_vwap":     (safe(row.get("Price vs VWAP%"), None) is not None and
                               safe(row.get("Price vs VWAP%"), -999) > 0),
            "vwap_val":       row.get("VWAP"),
            "current_price":  row.get("Current Price"),
            "confluence_count": len(confluences),
        })
    # Sort by score descending
    results.sort(key=lambda x: -x["score"])
    return results


# ─────────────────────────────────────────────────────────────────────────────
# CONSOLE SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
def print_summary(results, market_ctx):
    print(f"\n{'='*90}")
    print(f"  DAY TRADE SCANNER — INDICES  |  {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"  SPY: {market_ctx.get('spy_trend','?')} / {market_ctx.get('spy_vwap_bias','?')}"
          f"   QQQ: {market_ctx.get('qqq_trend','?')} / {market_ctx.get('qqq_vwap_bias','?')}")
    print(f"{'='*90}")
    print(f"  {'TICKER':<6} {'GR':<4} {'SC':<5} {'PRICE':>8} {'VWAP':>8} "
          f"{'PM HI':>8} {'PM LO':>8} {'ORB HI':>8} {'ORB LO':>8} "
          f"{'GAP':>8} {'RVOL':>6} {'TREND'}")
    print(f"  {'─'*86}")
    for r in results:
        def fv(v, pre=2): return f"${v:.{pre}f}" if v is not None else "  —"
        gap_str  = f"{r['gap_pct']:+.2f}%" if r['gap_pct'] is not None else "  —"
        rvol_str = f"{r['rvol']:.1f}x"     if r['rvol']    is not None else "—"
        print(f"  {r['ticker']:<6} {r['grade']:<4} {r['score']:<5}"
              f" {fv(r['current_price']):>8} {fv(r['vwap_val']):>8}"
              f" {fv(r['pm_high']):>8} {fv(r['pm_low']):>8}"
              f" {fv(r['orb_high']):>8} {fv(r['orb_low']):>8}"
              f" {gap_str:>8} {rvol_str:>6}  {r['intraday_trend']}")
    print(f"\n{'='*90}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CSV EXPORT
# ─────────────────────────────────────────────────────────────────────────────
CSV_COLS = [
    # Identity
    ("Ticker",             "Ticker"),
    ("Group",              "Group"),
    ("Sector",             "Sector"),
    # Score
    ("DT Score",           "_score"),
    ("Grade",              "_grade"),
    ("# Confluences",      "_n_conf"),
    # Price
    ("Last Price",         "Last Price"),
    ("Current Price",      "Current Price"),
    ("Prev Close",         "Prev Close"),
    # Gap
    ("Gap Type",           "Gap Type"),
    ("Gap%",               "Gap%"),
    # Pre-market
    ("PM Low",             "PM Low"),
    ("PM High",            "PM High"),
    ("PM Last",            "PM Last"),
    ("PM Vol",             "PM Vol"),
    # Intraday
    ("Session Vol",        "Session Vol"),
    ("RVOL",               "RVOL"),
    ("Vol vs 20D",         "Vol_vs_20D"),
    ("VWAP",               "VWAP"),
    ("Price vs VWAP%",     "Price vs VWAP%"),
    ("Intraday Trend",     "Intraday Trend"),
    ("Intraday Range%",    "Intraday Range%"),
    ("Today High",         "_today_high"),
    ("Today Low",          "_today_low"),
    # ORB
    ("ORB High",           "ORB High"),
    ("ORB Low",            "ORB Low"),
    # Prev-day levels
    ("Prev-Day High",      "Prev-Day High"),
    ("Prev-Day Low",       "Prev-Day Low"),
    ("vs PDH%",            "vs PDH%"),
    ("vs PDL%",            "vs PDL%"),
    # 1-min EMAs
    ("9EMA 1m",            "9EMA_1m"),
    ("20EMA 1m",           "20EMA_1m"),
    ("Above 9EMA 1m",      "Above 9EMA_1m"),
    ("Above 20EMA 1m",     "Above 20EMA_1m"),
    # Daily trend / MAs
    ("Daily Trend",        "Daily Trend"),
    ("20MA",               "20MA"),
    ("50MA",               "50MA"),
    ("9EMA Daily",         "9EMA_daily"),
    # Volatility
    ("ATR14 $",            "ATR14_daily"),
    ("ATR14%",             "ATR14_pct"),
    # Key levels
    ("20D High",           "20D High"),
    ("20D Low",            "20D Low"),
    ("52W High",           "52W High"),
    ("52W Low",            "52W Low"),
    ("Dist 52W High%",     "Dist_52W_High%"),
    # Confluences
    ("Confluence 1",       "_c0"),
    ("Confluence 2",       "_c1"),
    ("Confluence 3",       "_c2"),
    ("Confluence 4",       "_c3"),
    ("Confluence 5",       "_c4"),
]

def write_csv(raw_rows: list, results: list, path: str):
    clf_by_ticker = {r["ticker"]: r for r in results}

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([c[0] for c in CSV_COLS])

        for row in raw_rows:
            t   = row["Ticker"]
            clf = clf_by_ticker.get(t, {})
            cfls = clf.get("confluences", [])

            # Inject derived keys
            row["_score"]  = clf.get("score", "")
            row["_grade"]  = clf.get("grade", "")
            row["_n_conf"] = len(cfls)
            for i in range(5):
                row[f"_c{i}"] = cfls[i] if i < len(cfls) else ""

            def fv(v):
                if v is None: return ""
                if isinstance(v, bool): return "TRUE" if v else "FALSE"
                try:
                    f = float(v)
                    return "" if math.isnan(f) else (f"{f:.0f}" if abs(f) > 1000 else f"{f:.3f}")
                except (TypeError, ValueError):
                    s = str(v)
                    return "" if s in ("None","nan","N/A") else s

            writer.writerow([fv(row.get(raw_key)) for _, raw_key in CSV_COLS])

    log.info("CSV → %s (%d rows, %d cols)", path, len(raw_rows), len(CSV_COLS))


# ─────────────────────────────────────────────────────────────────────────────
# HTML DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
GRADE_COLOR = {"A+": "#00FF9C", "A": "#10B981", "B": "#F59E0B",
               "C": "#FB923C",  "D": "#EF4444"}
TREND_COLOR = {"Bull": "#10B981", "Recovering": "#6EE7B7",
               "Pulling Back": "#FB923C", "Bear": "#EF4444",
               "N/A": "#64748B"}
GAP_COLOR   = {"Gap Up": "#10B981", "Gap Down": "#EF4444", "Flat Open": "#64748B", "N/A": "#64748B"}

def fmt(v, suffix="", precision=2, plus=False):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    try:
        s = f"{float(v):+.{precision}f}" if plus else f"{float(v):.{precision}f}"
        return s + suffix
    except:
        return str(v) if v else "—"

def build_card(r):
    gc   = GRADE_COLOR.get(r["grade"], "#64748B")
    tc   = TREND_COLOR.get(r["intraday_trend"], "#64748B")
    gapc = GAP_COLOR.get(r["gap_type"], "#64748B")
    dtc  = TREND_COLOR.get(
        "Bull" if r["daily_trend"] == "Uptrend" else
        "Bear" if r["daily_trend"] == "Downtrend" else "N/A", "#64748B")

    conf_items = "".join(f"<li>{c}</li>" for c in r["confluences"][:6])

    # Badge row
    badges = ""
    # VWAP position — always shown first, most important intraday signal
    if r.get("price_vs_vwap") is not None:
        if r.get("above_vwap"):
            badges += f'<span class="badge b-vwap-above">▲ Above VWAP {fmt(r["price_vs_vwap"], suffix="%", plus=True)}</span>'
        else:
            badges += f'<span class="badge b-vwap-below">▼ Below VWAP {fmt(r["price_vs_vwap"], suffix="%", plus=True)}</span>'
    if r.get("above_9ema_1m"):   badges += '<span class="badge b-green">9EMA✓</span>'
    if r.get("above_20ema_1m"):  badges += '<span class="badge b-green">20EMA✓</span>'
    if r["gap_type"] == "Gap Up":   badges += '<span class="badge b-gap-up">▲ GAP UP</span>'
    if r["gap_type"] == "Gap Down": badges += '<span class="badge b-gap-dn">▼ GAP DN</span>'

    n_conf = r["confluence_count"]
    conf_bar = min(n_conf, 6)

    return f"""<div class="card" data-score="{r['score']}" data-group="{r['group']}">
  <div class="card-top">
    <div class="tl">
      <span class="ticker">{r['ticker']}</span>
      <span class="grp-tag">{r['group']}</span>
    </div>
    <div class="tr">
      <span class="grade" style="color:{gc};border-color:{gc}">{r['grade']}</span>
      <span class="score-num">{r['score']}</span>
    </div>
  </div>

  <div class="card-price-row">
    <span class="price">${fmt(r['price'], precision=2)}</span>
    <span class="gap-chip" style="color:{gapc}">{r['gap_type']} {fmt(r['gap_pct'], suffix='%', plus=True) if r['gap_pct'] is not None else ''}</span>
  </div>

  <div class="trend-row">
    <span class="trend-pill" style="background:{tc}22;color:{tc}">{r['intraday_trend']}</span>
    <span class="trend-pill" style="background:{dtc}22;color:{dtc}">Daily: {r['daily_trend']}</span>
  </div>

  <div class="metrics-grid">
    <div class="m"><span class="ml">Price</span><span class="mv">${fmt(r['current_price'], precision=2)}</span></div>
    <div class="m"><span class="ml">VWAP</span><span class="mv">${fmt(r['vwap_val'], precision=2)}</span></div>
    <div class="m"><span class="ml">PM High</span><span class="mv">${fmt(r['pm_high'], precision=2)}</span></div>
    <div class="m"><span class="ml">PM Low</span><span class="mv">${fmt(r['pm_low'], precision=2)}</span></div>
    <div class="m"><span class="ml">ORB Hi (15m)</span><span class="mv">${fmt(r['orb_high'], precision=2)}</span></div>
    <div class="m"><span class="ml">ORB Lo (15m)</span><span class="mv">${fmt(r['orb_low'], precision=2)}</span></div>
    <div class="m"><span class="ml">RVOL</span><span class="mv">{fmt(r['rvol'], suffix='x', precision=1)}</span></div>
    <div class="m"><span class="ml">ATR%</span><span class="mv">{fmt(r['atr_pct'], suffix='%')}</span></div>
    <div class="m"><span class="ml">IR%</span><span class="mv">{fmt(r['ir_pct'], suffix='%')}</span></div>
    <div class="m"><span class="ml">PDH</span><span class="mv">${fmt(r['pdh'], precision=2)}</span></div>
  </div>

  {('<div class="badges">' + badges + '</div>') if badges else ''}

  <div class="conf-header">
    <span>Confluences</span>
    <span class="conf-pips">{"●" * conf_bar}{"○" * (6 - conf_bar)}</span>
  </div>
  <ul class="conf-list">{conf_items}</ul>
</div>"""


def build_html(results, market_ctx, run_time):
    # Group tabs
    groups     = ["All"] + list(dict.fromkeys(r["group"] for r in results))
    group_btns = "".join(
        f'<button class="tab-btn" data-group="{g}" onclick="filterGroup(this)">{g}</button>'
        for g in groups
    )

    cards_html = "".join(build_card(r) for r in results)

    spy_t  = market_ctx.get("spy_trend","?")
    spy_v  = market_ctx.get("spy_vwap_bias","?")
    qqq_t  = market_ctx.get("qqq_trend","?")
    qqq_v  = market_ctx.get("qqq_vwap_bias","?")
    spy_c  = "#10B981" if spy_t == "Bullish" else "#EF4444"
    qqq_c  = "#10B981" if qqq_t == "Bullish" else "#EF4444"

    a_plus = sum(1 for r in results if r["grade"] == "A+")
    a_g    = sum(1 for r in results if r["grade"] == "A")
    b_g    = sum(1 for r in results if r["grade"] == "B")
    high3  = sum(1 for r in results if r["confluence_count"] >= 3)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Day Trade Scanner — {run_time}</title>
<style>
:root{{
  --bg:#070D17;--surface:#0F1923;--surface2:#162030;
  --border:#1E2D45;--text:#E2E8F0;--muted:#4A6080;
  --green:#10B981;--red:#EF4444;--amber:#F59E0B;--blue:#6366F1;
  --font:'Inter','Segoe UI',system-ui,sans-serif;
  --mono:'JetBrains Mono','Fira Code','Consolas',monospace;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;padding-bottom:80px}}

/* ── TOPBAR ── */
.topbar{{background:var(--surface);border-bottom:1px solid var(--border);
  padding:12px 28px;display:flex;align-items:center;
  justify-content:space-between;position:sticky;top:0;z-index:200;gap:12px;flex-wrap:wrap}}
.topbar-left{{display:flex;align-items:center;gap:14px}}
.logo{{font-size:.95rem;font-weight:800;letter-spacing:.1em;color:#fff;
  font-family:var(--mono);white-space:nowrap}}
.mkt-chip{{display:flex;gap:6px;align-items:center;font-size:.75rem;
  background:var(--surface2);border:1px solid var(--border);
  border-radius:6px;padding:4px 10px}}
.mkt-dot{{width:7px;height:7px;border-radius:50%;display:inline-block}}
.ts{{font-size:.72rem;color:var(--muted);font-family:var(--mono);white-space:nowrap}}

/* ── STAT BAR ── */
.statbar{{display:flex;gap:10px;padding:14px 28px;
  border-bottom:1px solid var(--border);flex-wrap:wrap}}
.stat{{background:var(--surface);border:1px solid var(--border);
  border-radius:8px;padding:10px 16px;min-width:110px}}
.stat .n{{font-size:1.7rem;font-weight:800;font-family:var(--mono)}}
.stat .l{{font-size:.68rem;color:var(--muted);text-transform:uppercase;
  letter-spacing:.07em;margin-top:1px}}

/* ── CONTROLS ── */
.controls{{display:flex;gap:10px;padding:14px 28px;flex-wrap:wrap;
  border-bottom:1px solid var(--border);align-items:center}}
.tab-btn{{padding:5px 14px;border-radius:20px;font-size:.78rem;font-weight:600;
  border:1px solid var(--border);cursor:pointer;background:transparent;
  color:var(--muted);transition:all .15s}}
.tab-btn:hover,.tab-btn.active{{background:var(--blue);color:#fff;border-color:var(--blue)}}
.sort-label{{font-size:.75rem;color:var(--muted);margin-left:auto}}
select{{background:var(--surface2);border:1px solid var(--border);
  color:var(--text);padding:5px 10px;border-radius:6px;font-size:.78rem}}

/* ── GRID ── */
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));
  gap:14px;padding:20px 28px}}

/* ── CARD ── */
.card{{background:var(--surface);border:1px solid var(--border);
  border-radius:12px;overflow:hidden;transition:transform .15s,box-shadow .15s}}
.card:hover{{transform:translateY(-3px);box-shadow:0 12px 30px rgba(0,0,0,.5)}}
.card-top{{display:flex;justify-content:space-between;align-items:flex-start;
  padding:14px 16px 8px}}
.tl{{display:flex;flex-direction:column;gap:3px}}
.ticker{{font-size:1.35rem;font-weight:900;font-family:var(--mono);
  letter-spacing:.04em;color:#fff}}
.grp-tag{{font-size:.65rem;color:var(--muted);text-transform:uppercase;
  letter-spacing:.08em}}
.tr{{display:flex;align-items:center;gap:8px}}
.grade{{font-size:1.1rem;font-weight:900;font-family:var(--mono);
  border:2px solid;border-radius:6px;padding:2px 8px}}
.score-num{{font-size:.85rem;color:var(--muted);font-family:var(--mono)}}

.card-price-row{{display:flex;justify-content:space-between;
  align-items:baseline;padding:0 16px 8px}}
.price{{font-size:1.05rem;font-weight:700;font-family:var(--mono)}}
.gap-chip{{font-size:.82rem;font-weight:700;font-family:var(--mono)}}

.trend-row{{display:flex;gap:6px;padding:0 16px 10px;flex-wrap:wrap}}
.trend-pill{{font-size:.7rem;font-weight:700;padding:3px 9px;
  border-radius:20px;white-space:nowrap}}

.metrics-grid{{display:grid;grid-template-columns:repeat(5,1fr);
  border-top:1px solid var(--border)}}
.m{{padding:7px 10px;border-right:1px solid var(--border);
  border-bottom:1px solid var(--border)}}
.m:nth-child(5n){{border-right:none}}
.ml{{font-size:.62rem;color:var(--muted);text-transform:uppercase;
  letter-spacing:.04em;display:block}}
.mv{{font-size:.82rem;font-weight:700;font-family:var(--mono);
  display:block;margin-top:1px}}

.conf-header{{display:flex;justify-content:space-between;align-items:center;
  padding:8px 16px 4px;font-size:.68rem;color:var(--muted);
  text-transform:uppercase;letter-spacing:.06em}}
.conf-pips{{font-size:.9rem;letter-spacing:2px;color:var(--green)}}
.conf-list{{list-style:none;padding:0 16px 12px}}
.conf-list li{{font-size:.74rem;color:#94A3B8;padding:3px 0 3px 14px;
  position:relative;border-bottom:1px solid #1E2D4530;line-height:1.4}}
.conf-list li:last-child{{border-bottom:none}}
.conf-list li::before{{content:'›';position:absolute;left:0;color:var(--muted);
  font-size:1rem;line-height:1.2}}

.badges{{display:flex;gap:5px;padding:0 16px 12px;flex-wrap:wrap}}
.badge{{font-size:.65rem;font-weight:800;padding:2px 8px;border-radius:20px}}
.b-green{{color:var(--green);background:#10B98122}}
.b-gap-up{{color:#00FF9C;background:#00FF9C15}}
.b-gap-dn{{color:var(--red);background:#EF444422}}
.b-vwap-above{{color:#00FF9C;background:#00FF9C15;font-size:.7rem}}
.b-vwap-below{{color:#FB923C;background:#FB923C18;font-size:.7rem}}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-left">
    <span class="logo">⬡ DAY TRADE SCANNER</span>
    <div class="mkt-chip">
      <span class="mkt-dot" style="background:{spy_c}"></span>
      <span>SPY {spy_t} · {spy_v}</span>
    </div>
    <div class="mkt-chip">
      <span class="mkt-dot" style="background:{qqq_c}"></span>
      <span>QQQ {qqq_t} · {qqq_v}</span>
    </div>
  </div>
  <span class="ts">{run_time} ET</span>
</div>

<div class="statbar">
  <div class="stat"><div class="n" style="color:#00FF9C">{a_plus}</div><div class="l">A+ Setups</div></div>
  <div class="stat"><div class="n" style="color:var(--green)">{a_g}</div><div class="l">A Grade</div></div>
  <div class="stat"><div class="n" style="color:var(--amber)">{b_g}</div><div class="l">B Grade</div></div>
  <div class="stat"><div class="n" style="color:var(--blue)">{high3}</div><div class="l">3+ Confluences</div></div>
  <div class="stat"><div class="n" style="color:#fff">{len(results)}</div><div class="l">Total Scanned</div></div>
</div>

<div class="controls">
  {group_btns}
  <span class="sort-label">Sort:</span>
  <select onchange="sortCards(this.value)">
    <option value="score">Score</option>
    <option value="rvol">RVOL</option>
    <option value="gap">Gap%</option>
    <option value="ir">Intraday Range%</option>
    <option value="atr">ATR%</option>
  </select>
</div>

<div class="grid" id="card-grid">
{cards_html}
</div>

<script>
const allCards = Array.from(document.querySelectorAll('.card'));
let activeGroup = 'All';
let activeSort  = 'score';

function filterGroup(btn) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeGroup = btn.dataset.group;
  render();
}}

function sortCards(val) {{
  activeSort = val;
  render();
}}

function render() {{
  const grid = document.getElementById('card-grid');
  let cards = allCards.filter(c => activeGroup === 'All' || c.dataset.group === activeGroup);

  const key = {{
    score: c => -parseInt(c.dataset.score || 0),
    rvol:  c => -(parseFloat(c.querySelector('.mv')?.textContent) || 0),
    gap:   c => 0,
    ir:    c => 0,
    atr:   c => 0,
  }};

  cards.sort((a,b) => (key[activeSort]?.(a) || 0) - (key[activeSort]?.(b) || 0));
  allCards.forEach(c => c.style.display = 'none');
  cards.forEach(c => {{ c.style.display = ''; grid.appendChild(c); }});
}}

// Activate All tab on load
document.querySelector('.tab-btn[data-group="All"]').classList.add('active');
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    display_tickers = sys.argv[1:] if len(sys.argv) > 1 else WATCHLIST_DISPLAY

    log.info("Day Trade Scanner — %d tickers", len(display_tickers))

    # Fetch market context first (SPY + QQQ)
    log.info("Fetching market context (SPY/QQQ)…")
    market_ctx = fetch_market_context()

    # Fetch all ticker metrics
    raw_rows = fetch_all(display_tickers)

    # Score & rank
    results = build_results(raw_rows, market_ctx)

    # Output
    print_summary(results, market_ctx)

    run_time = datetime.now(MARKET_TZ).strftime("%Y-%m-%d %H:%M")
    html     = build_html(results, market_ctx, run_time)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("Dashboard → %s", OUTPUT_HTML)

    write_csv(raw_rows, results, OUTPUT_CSV)


if __name__ == "__main__":
    main()
