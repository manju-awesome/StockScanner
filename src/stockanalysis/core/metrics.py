import sys
from datetime import datetime
import pandas as pd
import pytz
from stockanalysis.core.put_candidate import compute_put_candidate
from stockanalysis.core.call_candidate import compute_call_candidate

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

# ── Entry gate ────────────────────────────────────────────────────────────────
CFG_MKTCAP_MIN           = 1_000_000_000
CFG_PRICE_MIN            = 5.0
CFG_RVOL_MIN_GATE        = 0.6
CFG_ADX_MIN_GATE         = 15
CFG_MAX_PCT_BELOW_200MA  = -30

# ── Momentum ──────────────────────────────────────────────────────────────────
CFG_MOM_DIST_MAX         = -10
CFG_MOM_RVOL             = 0.8
CFG_MOM_ADX              = 20
CFG_MOM_RS_MIN           = 0
CFG_MOM_DAYS_SINCE_52WH  = 10

# ── Momentum-Pullback ────────────────────────────────────────────────────────
CFG_MP_DIST_MIN          = -30
CFG_MP_DIST_MAX          = -10
CFG_MP_8EMA_PCT_LO       = -1.0
CFG_MP_8EMA_PCT_HI       = 1.0
CFG_MP_RSI_LO            = 30    # FIX: was 40, too strict for deep pullbacks
CFG_MP_RSI_HI            = 65
CFG_MP_PULLBACK_VOL      = 1.2   # FIX: was 0.8->1.0->1.2; catches GOOGL(1.19), still blocks AMZN(2.12)
CFG_MP_BB_PCTB           = 0.4
CFG_MP_RVOL              = 1.0

# ── VCP Setup ────────────────────────────────────────────────────────────────
CFG_VCP_DIST_MIN         = -45
CFG_VCP_DIST_MAX         = -20
CFG_VCP_BASE_RVOL        = 0.8
CFG_VCP_ADX_MIN          = 18

# ── Turnaround ───────────────────────────────────────────────────────────────
CFG_TA_DIST_MIN          = -65
CFG_TA_DIST_MAX          = -40
CFG_TA_RVOL              = 1.3   # FIX: was 1.5, PLTR(1.4)+earnings_beat is valid Turnaround
CFG_TA_SHORT_INT         = 10

# ── Longterm Hold ────────────────────────────────────────────────────────────
CFG_LT_PCT_FROM_LOW_MAX  = 10
CFG_LT_RVOL              = 1.2
CFG_LT_EPS_GROWTH        = 15
CFG_LT_INST_OWN_MIN      = 40


def calculate_atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _safe_float(val) -> float | None:
    """Return float or None — converts nan/inf to None."""
    try:
        v = float(val)
        return None if (v != v or v == float("inf") or v == float("-inf")) else v
    except Exception:
        return None


def get_metrics(ticker: str, qqq_return_3m: float) -> dict:
    """
    Fetch every metric needed for categorisation.
    Never raises — failures produce None values logged at DEBUG level.

    Fix log (vs original):
      FIX-A  ffill daily Close/High/Low/Open BEFORE assigning `close` variable
             and before any rolling/ewm calculations. Prevents nan 200MA, 50MA,
             ATR, RSI, ADX, RS when yfinance returns trailing NaN rows.
      FIX-B  pandas_ta BB column name changed in 0.4.71b0+:
             BBU_20_2.0 -> BBU_20_2.0_2.0. KeyError silently wiped ADX+RSI too.
             Now uses startswith() lookup + 3 isolated try/except blocks.
      FIX-C  ADX + RSI use dropna().iloc[-1] (not iloc[-1]) for safety.
      FIX-D  RS guards against nan qqq_return_3m (treat as 0 = absolute return).
      FIX-E  Inst_Own_Chg: uses institutional_holders (pctHeld or % Out column)
             instead of heldPercentInstitutions which equals institutionPercentHeld.
      FIX-F  Entry gate ADX check only fires when ADX_14 is not None.
      FIX-G  _safe_float() guards all rolling/ewm values against nan/inf.
      FIX-H  CFG_MP_RSI_LO lowered 40->30 (NVDA 39.6, AAPL 32.2 were excluded).
      FIX-H  CFG_MP_RSI_LO lowered 40->30 (NVDA 39.6, AAPL 32.2 were excluded).
      FIX-I  CFG_MP_PULLBACK_VOL raised 0.8->1.2 (1.0 too tight for GOOGL 1.19).
      FIX-J  CFG_TA_RVOL lowered 1.5->1.3 (earnings beat reduces RVOL bar).
    """
    row = {"Ticker": ticker}
    t   = yf.Ticker(ticker)
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

    # FIX-E: institutional_holders gives real position data; handles both yfinance versions
    try:
        ih = t.institutional_holders
        if ih is not None and not ih.empty:
            if "pctHeld" in ih.columns:
                top_sum = float(ih["pctHeld"].sum()) * 100      # decimal -> %
            elif "% Out" in ih.columns:
                top_sum = float(ih["% Out"].sum())              # already %
            else:
                top_sum = None
            row["Inst_Own_Chg"] = (
                round(top_sum - row["Inst_Own%"], 2)
                if (top_sum is not None and row.get("Inst_Own%") is not None)
                else None
            )
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
        "Call_Candidate", "Call_Score", "Call_Strength", "Call_Reason", "Call_Strike_Hint"
    )

    try:
        daily = t.history(period="1y", interval="1d", auto_adjust=False)
        if not daily.empty:
            today_et    = datetime.now(MARKET_TZ).date()
            daily_dates = (
                daily.index.tz_convert(MARKET_TZ).date
                if daily.index.tz is not None else daily.index.date
            )

            # FIX-A: ffill FIRST, then assign close — so all downstream calcs
            # use the filled series. Original bug: close was assigned from daily
            # BEFORE ffill, so RS/MAs/indicators still saw trailing NaN.
            daily = daily.copy()
            daily["Close"]  = daily["Close"].ffill()
            daily["High"]   = daily["High"].ffill()
            daily["Low"]    = daily["Low"].ffill()
            daily["Open"]   = daily["Open"].ffill()
            daily["Volume"] = daily["Volume"].fillna(0)

            # Assign close AFTER ffill (critical — was the RS=nan root cause)
            close = daily["Close"]

            # 52W extremes
            row["52W Low"]  = round(float(daily["Low"].min()),  2)
            row["52W High"] = round(float(daily["High"].max()), 2)

            # 52W high date + days since
            high_idx = daily["High"].idxmax()
            h_date   = (high_idx.tz_convert(MARKET_TZ).date()
                        if getattr(high_idx, "tzinfo", None) else high_idx.date())
            row["52W_High_Date"]       = str(h_date)
            row["Days_Since_52W_High"] = (today_et - h_date).days

            # Current price — prefer fast_info, fall back to last close
            p = row["Current Price"] or float(close.iloc[-1])
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

            # ── Moving averages ───────────────────────────────────────────────
            ma200_raw = _safe_float(close.rolling(200).mean().iloc[-1]) if len(daily) >= 200 else None
            ma50_raw  = _safe_float(close.rolling(50).mean().iloc[-1])  if len(daily) >= 50  else None
            row["200MA"] = round(ma200_raw, 2) if ma200_raw is not None else None
            row["50MA"]  = round(ma50_raw,  2) if ma50_raw  is not None else None
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

            # ── ATR ───────────────────────────────────────────────────────────
            daily["ATR20"] = calculate_atr(daily)
            atr20     = _safe_float(daily["ATR20"].iloc[-1])
            atr20_10d = _safe_float(daily["ATR20"].iloc[-10]) if len(daily) >= 10 else atr20
            row["ATR20"]         = round(atr20, 2) if atr20 is not None else None
            row["ATR_Pct"]       = round(atr20 / p * 100, 2) if (atr20 and p) else None
            row["ATR Shrinking"] = (atr20 < atr20_10d) if (atr20 and atr20_10d) else None

            # ── Volume ────────────────────────────────────────────────────────
            avg50_vol = _safe_float(daily["Volume"].rolling(50).mean().iloc[-1])
            avg20_vol = _safe_float(daily["Volume"].rolling(20).mean().iloc[-1])
            cur_vol   = float(daily["Volume"].iloc[-1])
            row["RVOL"]           = round(cur_vol / avg50_vol, 2) if avg50_vol else None
            row["Vol_vs_20D"]     = round(cur_vol / avg20_vol, 2) if avg20_vol else None
            row["VolumeDryingUp"] = (
                bool(daily["Volume"].tail(10).mean() < avg50_vol * 0.75)
                if avg50_vol else None
            )

            try:
                last5    = daily.tail(5).copy()
                red_days = last5[last5["Close"] < last5["Open"]]
                row["Pullback_Vol_Ratio"] = (
                    round(red_days["Volume"].mean() / avg20_vol, 2)
                    if not red_days.empty and avg20_vol else None  # None = no red days
                )

            except Exception:
                row["Pullback_Vol_Ratio"] = None

            # ── RS vs QQQ — FIX-D: guard nan qqq_return_3m ───────────────────
            # If QQQ fetch failed upstream, qqq_return_3m may be nan/None.
            # Fall back to absolute 3-month return (no benchmark subtraction).
            if len(close) >= 63:
                c_now = _safe_float(close.iloc[-1])
                c_63  = _safe_float(close.iloc[-63])
                if c_now and c_63:
                    qqq_safe = (
                        qqq_return_3m
                        if (qqq_return_3m is not None and
                            qqq_return_3m == qqq_return_3m)   # not nan
                        else 0.0
                    )
                    row["RS"] = round(((c_now / c_63) - 1) * 100 - qqq_safe, 2)
                else:
                    row["RS"] = None
            else:
                row["RS"] = None

            # ── ADX / RSI / Bollinger — FIX-B/C: isolated blocks + dynamic BB cols
            try:
                import pandas_ta as ta
            except ImportError:
                log.warning("%s: pandas_ta not installed", ticker)
                ta = None
                row.update({"ADX_14": None, "Trend_Strength": "Ranging",
                            "RSI_14": None, "BB_PctB": None})

            if ta is not None:
                # ADX — FIX-C: dropna().iloc[-1]
                try:
                    adx_df = ta.adx(daily["High"], daily["Low"], close, length=14)
                    if adx_df is not None and "ADX_14" in adx_df.columns:
                        adx_series = adx_df["ADX_14"].dropna()
                        if not adx_series.empty:
                            adx_val = float(adx_series.iloc[-1])
                            row["ADX_14"] = round(adx_val, 1)
                            row["Trend_Strength"] = (
                                "Strong"   if adx_val >= 40 else
                                "Trending" if adx_val >= 25 else
                                "Ranging"
                            )
                        else:
                            row["ADX_14"] = None
                            row["Trend_Strength"] = "Ranging"
                    else:
                        row["ADX_14"] = None
                        row["Trend_Strength"] = "Ranging"
                except Exception as e:
                    log.debug("%s: ADX failed (%s)", ticker, e)
                    row["ADX_14"] = None
                    row["Trend_Strength"] = "Ranging"

                # RSI — FIX-C: dropna().iloc[-1]
                try:
                    rsi_s = ta.rsi(close, length=14)
                    if rsi_s is not None:
                        rsi_clean = rsi_s.dropna()
                        row["RSI_14"] = (
                            round(float(rsi_clean.iloc[-1]), 1)
                            if not rsi_clean.empty else None
                        )
                    else:
                        row["RSI_14"] = None
                except Exception as e:
                    log.debug("%s: RSI failed (%s)", ticker, e)
                    row["RSI_14"] = None

                # Bollinger Bands — FIX-B: startswith() handles old+new column names
                try:
                    bb = ta.bbands(close, length=20, std=2)
                    if bb is not None:
                        upper_cols = [c for c in bb.columns if c.startswith("BBU")]
                        lower_cols = [c for c in bb.columns if c.startswith("BBL")]
                        if upper_cols and lower_cols:
                            upper = _safe_float(bb[upper_cols[0]].iloc[-1])
                            lower = _safe_float(bb[lower_cols[0]].iloc[-1])
                            if upper is not None and lower is not None:
                                bw = upper - lower
                                row["BB_PctB"] = round((p - lower) / bw, 3) if bw > 0 else None
                            else:
                                row["BB_PctB"] = None
                        else:
                            log.debug("%s: BB columns not found: %s", ticker, bb.columns.tolist())
                            row["BB_PctB"] = None
                    else:
                        row["BB_PctB"] = None
                except Exception as e:
                    log.debug("%s: BB failed (%s)", ticker, e)
                    row["BB_PctB"] = None

            # ── CANSLIM composite ─────────────────────────────────────────────
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

            p_now = row.get("Current Price")
            row["Above_VWAP"] = (p_now > row["VWAP"]) if (p_now and row["VWAP"]) else None
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
    '''
    if (row.get("RVOL") or 0) < CFG_RVOL_MIN_GATE:
        failed.append(f"RVOL<{CFG_RVOL_MIN_GATE}")
    '''
    # FIX-F: only gate on ADX when value is not None
    if row.get("ADX_14") is not None and row["ADX_14"] < CFG_ADX_MIN_GATE:
        failed.append(f"ADX<{CFG_ADX_MIN_GATE}(flat)")
    if (row.get("Price_vs_200MA%") or 0) < CFG_MAX_PCT_BELOW_200MA:
        failed.append(f">{abs(CFG_MAX_PCT_BELOW_200MA)}%below200MA")




    # ── 7. PUT CANDIDATE ─────────────────────────────────────────────────────
    compute_put_candidate(row)  # adds Put_Candidate, Put_Score, Put_Reason
    compute_call_candidate(row)


    row["Entry_Gate_Pass"] = (len(failed) == 0)
    row["Entry_Gate_Reason"] = ", ".join(failed)
    for key, value in row.items():
        print(f"Key: {key} | Value: {value}")
    return row




