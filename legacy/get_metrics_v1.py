import sys
from datetime import datetime
import pandas as pd
import pytz

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed. Run: pip install yfinance", file=sys.stderr)
    sys.exit(1)
import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MARKET_TZ       = pytz.timezone("America/New_York")
PREMARKET_START = datetime.strptime("04:00", "%H:%M").time()
MARKET_OPEN     = datetime.strptime("09:30", "%H:%M").time()

# Entry gate (all categories must pass)
CFG_MKTCAP_MIN           = 1_000_000_000   # $1 B
CFG_PRICE_MIN            = 5.0             # $5
CFG_RVOL_MIN_GATE        = 0.6
CFG_ADX_MIN_GATE         = 15              # below = no trend at all
CFG_MAX_PCT_BELOW_200MA  = -30             # % — more negative = structural downtrend

# Momentum
CFG_MOM_DIST_MAX         = -10    # within 10% of 52W high  (value is negative %)
CFG_MOM_RVOL             = 0.8
CFG_MOM_ADX              = 20
CFG_MOM_RS_MIN           = 0
CFG_MOM_DAYS_SINCE_52WH  = 10    # 52W high set within last N calendar days

# Momentum-Pullback
CFG_MP_DIST_MIN          = -30
CFG_MP_DIST_MAX          = -10
CFG_MP_8EMA_PCT_LO       = -1.0
CFG_MP_8EMA_PCT_HI       = 1.0
CFG_MP_RSI_LO            = 40
CFG_MP_RSI_HI            = 65
CFG_MP_PULLBACK_VOL      = 0.8   # < this = healthy pullback vol
CFG_MP_BB_PCTB           = 0.4   # < this = coiled near lower band
CFG_MP_RVOL              = 1.0

# VCP Setup
CFG_VCP_DIST_MIN         = -45
CFG_VCP_DIST_MAX         = -20
CFG_VCP_BASE_RVOL        = 0.8   # RVOL < this = quiet base
CFG_VCP_ADX_MIN          = 18

# Turnaround
CFG_TA_DIST_MIN          = -65
CFG_TA_DIST_MAX          = -40
CFG_TA_RVOL              = 1.5   # reversal spike
CFG_TA_SHORT_INT         = 10    # short interest > % (squeeze fuel)

# Longterm Hold
CFG_LT_PCT_FROM_LOW_MAX  = 10    # within 10% above 52W low
CFG_LT_RVOL              = 1.2
CFG_LT_EPS_GROWTH        = 15
CFG_LT_INST_OWN_MIN      = 40

# ─────────────────────────────────────────────────────────────────────────────
# ATR helper
# ─────────────────────────────────────────────────────────────────────────────
def calculate_atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Average True Range — rolling mean over `period` bars."""
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def get_metrics1(ticker: str, qqq_return_3m: float) -> dict:
    """
    Fetch every metric needed for categorisation.
    Never raises — failures produce None values logged at DEBUG level.
    """
    row  = {"Ticker": ticker}
    t    = yf.Ticker(ticker)
    info = {}

    # ── 1. INFO ───────────────────────────────────────────────────────────────
    try:
        info = t.info or {}
        row["Sector"]    = info.get("sector") or info.get("quoteType") or "N/A"
        row["LongName"]  = info.get("longName") or ticker
        row["MarketCap"] = info.get("marketCap")
    except Exception:
        row["Sector"]    = "N/A"
        row["LongName"]  = ticker
        row["MarketCap"] = None

    try:
        row["Revenue"] = (
            round(info["revenueGrowth"] * 100, 2)
            if info.get("revenueGrowth") is not None else None
        )
    except Exception:
        row["Revenue"] = None

    try:
        eps_t = info.get("trailingEps")
        eps_f = info.get("forwardEps")
        row["EPS_Growth%"] = (
            round(((eps_f - eps_t) / abs(eps_t)) * 100, 1)
            if (eps_t and eps_f and eps_t != 0) else None
        )
    except Exception:
        row["EPS_Growth%"] = None



    try:
        inst = info.get("institutionPercentHeld")
        row["Inst_Own%"] = round(inst * 100, 1) if inst is not None else None
    except Exception:
        row["Inst_Own%"] = None
    '''
    try:
        inst_prior = info.get("heldPercentInstitutions")
        row["Inst_Own_Chg"] = (
            round(row["Inst_Own%"] - inst_prior * 100, 2)
            if (inst_prior is not None and row.get("Inst_Own%") is not None) else None
        )
    except Exception:
        row["Inst_Own_Chg"] = None
    '''
    try:
        ih = t.institutional_holders
        if ih is not None and not ih.empty and "% Out" in ih.columns:
            total_inst = ih["% Out"].sum()  # sum of top holders
            row["Inst_Own_Chg"] = round(total_inst * 100 - (row.get("Inst_Own%") or 0), 2)
        else:
            row["Inst_Own_Chg"] = None
    except Exception:
        row["Inst_Own_Chg"] = None



    try:
        fcf = info.get("freeCashflow")
        row["FCF_Positive"] = bool(fcf > 0) if fcf is not None else None
    except Exception:
        row["FCF_Positive"] = None

    try:
        si = info.get("shortPercentOfFloat")
        row["Short_Interest%"] = round(si * 100, 1) if si is not None else None
    except Exception:
        row["Short_Interest%"] = None

    # ── 2. EARNINGS ───────────────────────────────────────────────────────────
    try:
        cal = t.calendar
        ed  = "N/A"
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
            if dates:
                ed = str(dates[0])[:10]
        elif cal is not None and not getattr(cal, "empty", True) and "Earnings Date" in cal.index:
            vals = cal.loc["Earnings Date"].dropna().tolist()
            if vals:
                ed = str(vals[0])[:10]
        row["EarningsDate"] = ed
    except Exception:
        row["EarningsDate"] = "N/A"

    try:
        eh = t.earnings_history
        if eh is not None and not eh.empty and "epsActual" in eh.columns:
            last = eh.dropna(subset=["epsActual", "epsEstimate"]).iloc[-1]
            row["EarningsBeat"] = bool(last["epsActual"] > last["epsEstimate"])
        else:
            row["EarningsBeat"] = None
    except Exception:
        row["EarningsBeat"] = None

    # ── 3. CURRENT PRICE ─────────────────────────────────────────────────────
    try:
        row["Current Price"] = round(float(t.fast_info["last_price"]), 2)
    except Exception as e:
        log.debug("%s: current price failed (%s)", ticker, e)
        row["Current Price"] = None

    # ── 4. DAILY HISTORY (1 year) ────────────────────────────────────────────
    _daily_keys = (
        "52W Low", "52W High", "52W_High_Date", "Days_Since_52W_High",
        "Pct_From_52W_Low%",
        "200MA", "50MA", "8EMA", "21EMA",
        "Price_vs_200MA%", "Price_vs_50MA%", "Pct_vs_8EMA", "Above_200MA",
        "ATR20", "ATR_Pct", "ATR Shrinking",
        "RVOL", "Vol_vs_20D", "VolumeDryingUp", "Pullback_Vol_Ratio",
        "RS", "Dist_52W_High%",
        "ADX_14", "Trend_Strength", "RSI_14", "BB_PctB",
        "CANSLIM_Pass",
        "Prev-Day Low", "Prev-Day High",
        "_prior_52w_high", "_prior_52w_low",
    )

    try:
        daily = t.history(period="1y", interval="1d", auto_adjust=False)
        if not daily.empty:
            today_et    = datetime.now(MARKET_TZ).date()
            daily_dates = (
                daily.index.tz_convert(MARKET_TZ).date
                if daily.index.tz is not None else daily.index.date
            )

            # 52W extremes
            row["52W Low"]  = round(float(daily["Low"].min()),  2)
            row["52W High"] = round(float(daily["High"].max()), 2)

            # 52W high date + days since
            high_idx = daily["High"].idxmax()
            h_date   = (high_idx.tz_convert(MARKET_TZ).date()
                        if getattr(high_idx, "tzinfo", None) else high_idx.date())
            row["52W_High_Date"]       = str(h_date)
            row["Days_Since_52W_High"] = (today_et - h_date).days

            # % above 52W low
            p = row["Current Price"] or float(daily["Close"].iloc[-1])
            row["Pct_From_52W_Low%"] = round((p / row["52W Low"] - 1) * 100, 1)

            # Prior session extremes
            prior = daily.iloc[:-1] if daily_dates[-1] == today_et else daily
            row["_prior_52w_high"] = round(float(prior["High"].max()), 2) if len(prior) else None
            row["_prior_52w_low"]  = round(float(prior["Low"].min()),  2) if len(prior) else None

            # Prev completed day H/L
            prev = (daily.iloc[-2]
                    if daily_dates[-1] == today_et and len(daily) >= 2
                    else daily.iloc[-1])
            row["Prev-Day Low"]  = round(float(prev["Low"]),  2)
            row["Prev-Day High"] = round(float(prev["High"]), 2)

            # Moving averages
            close = daily["Close"]
            row["200MA"] = (
                round(float(close.rolling(200).mean().iloc[-1]), 2)
                if len(daily) >= 200 else None
            )
            row["50MA"] = (
                round(float(close.rolling(50).mean().iloc[-1]),  2)
                if len(daily) >= 50  else None
            )
            row["8EMA"]  = round(float(close.ewm(span=8,  adjust=False).mean().iloc[-1]), 2)
            row["21EMA"] = round(float(close.ewm(span=21, adjust=False).mean().iloc[-1]), 2)

            row["Price_vs_200MA%"] = (
                round((p / row["200MA"] - 1) * 100, 1) if row["200MA"] else None
            )
            row["Price_vs_50MA%"] = (
                round((p / row["50MA"]  - 1) * 100, 1) if row["50MA"]  else None
            )
            row["Dist_52W_High%"] = round((p / row["52W High"] - 1) * 100, 1)
            row["Pct_vs_8EMA"]    = round((p / row["8EMA"]   - 1) * 100, 2)
            row["Above_200MA"]    = (p > row["200MA"]) if row["200MA"] is not None else None

            # ATR
            daily["ATR20"] = calculate_atr(daily)
            atr20     = round(float(daily["ATR20"].iloc[-1]),  2)
            atr20_10d = round(float(daily["ATR20"].iloc[-10]), 2) if len(daily) >= 10 else atr20
            row["ATR20"]          = atr20
            row["ATR_Pct"]        = round(atr20 / p * 100, 2) if p else None
            row["ATR Shrinking"]  = atr20 < atr20_10d

            # Volume
            avg50_vol = daily["Volume"].rolling(50).mean().iloc[-1]
            avg20_vol = daily["Volume"].rolling(20).mean().iloc[-1]
            row["RVOL"]           = round(daily["Volume"].iloc[-1] / avg50_vol, 2)
            row["Vol_vs_20D"]     = round(daily["Volume"].iloc[-1] / avg20_vol, 2)
            row["VolumeDryingUp"] = daily["Volume"].tail(10).mean() < avg50_vol * 0.75

            # Pullback vol ratio — avg red-day vol last 5 sessions vs 20d avg
            try:
                last5    = daily.tail(5).copy()
                red_days = last5[last5["Close"] < last5["Open"]]
                row["Pullback_Vol_Ratio"] = (
                    round(red_days["Volume"].mean() / avg20_vol, 2)
                    if not red_days.empty else None
                )
            except Exception:
                row["Pullback_Vol_Ratio"] = None

            # RS vs QQQ (3-month relative return)
            if len(close) >= 63:
                row["RS"] = round(
                    ((close.iloc[-1] / close.iloc[-63]) - 1) * 100 - qqq_return_3m, 2
                )
            else:
                row["RS"] = None

            # ADX / RSI / Bollinger via pandas_ta
            try:
                import pandas_ta as ta

                # In get_metrics(), replace the ADX block:
                adx_df = ta.adx(daily["High"], daily["Low"], close, length=14)
                if adx_df is not None and "ADX_14" in adx_df.columns:
                    # Use last non-NaN value instead of blindly taking iloc[-1]
                    adx_series = adx_df["ADX_14"].dropna()
                    if not adx_series.empty:
                        adx_val = float(adx_series.iloc[-1])
                        row["ADX_14"] = round(adx_val, 1)
                        row["Trend_Strength"] = (
                            "Strong" if adx_val >= 40 else
                            "Trending" if adx_val >= 25 else
                            "Ranging"
                        )
                    else:
                        row["ADX_14"] = None
                        row["Trend_Strength"] = "Ranging"  # default, not N/A



                rsi_s = ta.rsi(close, length=14)
                row["RSI_14"] = (
                    round(float(rsi_s.iloc[-1]), 1) if rsi_s is not None else None
                )

                bb = ta.bbands(close, length=20, std=2)
                if bb is not None:
                    upper = bb["BBU_20_2.0"].iloc[-1]
                    lower = bb["BBL_20_2.0"].iloc[-1]
                    bw    = upper - lower
                    row["BB_PctB"] = round((p - lower) / bw, 3) if bw > 0 else None
                else:
                    row["BB_PctB"] = None

            except ImportError:
                log.warning("pandas_ta not installed — ADX/RSI/BB unavailable")
                row.update({"ADX_14": None, "Trend_Strength": "N/A",
                            "RSI_14": None, "BB_PctB": None})
            except Exception as e:
                log.debug("%s: pandas_ta block (%s)", ticker, e)
                row.update({"ADX_14": None, "Trend_Strength": "N/A",
                            "RSI_14": None, "BB_PctB": None})

            # CANSLIM composite
            try:
                row["CANSLIM_Pass"] = all([
                    row.get("EPS_Growth%")    is not None and row["EPS_Growth%"]    > 20,
                    row.get("RS")             is not None and row["RS"]             > 0,
                    row.get("Vol_vs_20D")     is not None and row["Vol_vs_20D"]     > 1.2,
                    row.get("Dist_52W_High%") is not None and row["Dist_52W_High%"] >= -15,
                ])
            except Exception:
                row["CANSLIM_Pass"] = None

        else:
            for k in _daily_keys:
                row[k] = None
            row["Pct_vs_8EMA"] = None
            row["Above_200MA"] = None

    except Exception as e:
        log.warning("%s: daily history failed (%s)", ticker, e)
        for k in _daily_keys:
            row[k] = None
        row["Pct_vs_8EMA"] = None
        row["Above_200MA"] = None

    # ── 5. INTRADAY (pre-market + VWAP) ─────────────────────────────────────
    try:
        intra = t.history(period="1d", interval="1m", prepost=True, auto_adjust=False)
        if not intra.empty:
            idx_et = (
                intra.index.tz_convert(MARKET_TZ)
                if intra.index.tz is not None
                else intra.index.tz_localize(MARKET_TZ)
            )
            times   = idx_et.time
            pm_mask = [(PREMARKET_START <= tm < MARKET_OPEN) for tm in times]
            pm_df   = intra[pm_mask]
            row["Pre-Market Low"]  = round(float(pm_df["Low"].min()),  2) if not pm_df.empty else None
            row["Pre-Market High"] = round(float(pm_df["High"].max()), 2) if not pm_df.empty else None

            reg_df = intra[[tm >= MARKET_OPEN for tm in times]]
            if not reg_df.empty and reg_df["Volume"].sum() > 0:
                typ = (reg_df["High"] + reg_df["Low"] + reg_df["Close"]) / 3
                row["VWAP"] = round(
                    float((typ * reg_df["Volume"]).sum() / reg_df["Volume"].sum()), 2
                )
            else:
                row["VWAP"] = None

            p = row.get("Current Price")
            row["Above_VWAP"] = (p > row["VWAP"]) if (p and row["VWAP"]) else None
        else:
            row.update({"Pre-Market Low": None, "Pre-Market High": None,
                        "VWAP": None, "Above_VWAP": None})
    except Exception as e:
        log.debug("%s: intraday failed (%s)", ticker, e)
        row.update({"Pre-Market Low": None, "Pre-Market High": None,
                    "VWAP": None, "Above_VWAP": None})

    # ── 6. ENTRY GATE ────────────────────────────────────────────────────────
    failed = []
    if (row.get("MarketCap") or 0) < CFG_MKTCAP_MIN:
        failed.append("MarketCap<1B")
    if (row.get("Current Price") or 0) < CFG_PRICE_MIN:
        failed.append(f"Price<${CFG_PRICE_MIN:.0f}")
    if (row.get("RVOL") or 0) < CFG_RVOL_MIN_GATE:
        failed.append(f"RVOL<{CFG_RVOL_MIN_GATE}")
    if row.get("ADX_14") is not None and row["ADX_14"] < CFG_ADX_MIN_GATE:
        failed.append(f"ADX<{CFG_ADX_MIN_GATE}(flat)")
    if (row.get("Price_vs_200MA%") or 0) < CFG_MAX_PCT_BELOW_200MA:
        failed.append(f">{abs(CFG_MAX_PCT_BELOW_200MA)}%below200MA")

    row["Entry_Gate_Pass"]   = (len(failed) == 0)
    row["Entry_Gate_Reason"] = ", ".join(failed)
    for key, value in row.items():
        print(f"Key: {key} | Value: {value}")

    return row





