"""
metrics.py
==========
Fetches and computes ~45 technical + fundamental metrics for one ticker via
yfinance: price/MA/EMA levels, native RSI/ADX/Bollinger %B (Wilder's smoothing,
no pandas_ta), ATR, RVOL, gap columns (Gap% / Gap_Now% vs prev close), RS vs
QQQ, 52W stats, prev-day & pre-market levels, VWAP, institutional ownership,
CANSLIM composite, and the entry gate. Also attaches put/call candidate scores.

Main entry: get_metrics(ticker, qqq_return_3m) -> dict (never raises; failed
metrics come back None).

All daily-bar-derived metrics live in compute_daily_metrics(daily, ...) — a
pure function with no network or clock dependence, so a backtest can compute
point-in-time metrics by passing bars sliced up to any historical date.
get_metrics() is the live wrapper: fetch → compute_daily_metrics(asof=today)
→ add live-only extras (VWAP, ORB, pre-market, fundamentals).

Run standalone:
    python metrics.py NVDA            # one or more tickers
    python -m stockanalysis.core.metrics NVDA AMD
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz

if __package__ in (None, ""):   # direct run: make `stockanalysis.*` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
ORB_END         = datetime.strptime("09:45", "%H:%M").time()   # 15-min opening range

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


# ── Native indicators (FIX-K: replaced pandas_ta — it can't install on
#    Python 3.14 (numba pin), and its column renames were bug class #1) ───────

def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI with Wilder's smoothing (matches TradingView / pandas_ta)."""
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(100.0)  # all-gain window → RSI 100


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                  period: int = 14) -> pd.Series:
    """ADX with Wilder's smoothing (matches TradingView / pandas_ta)."""
    up, dn = high.diff(), -low.diff()
    plus_dm  = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low  - close.shift(1)).abs()], axis=1).max(axis=1)
    w = dict(alpha=1 / period, adjust=False)
    atr      = tr.ewm(**w).mean()
    plus_di  = 100 * plus_dm.ewm(**w).mean()  / atr
    minus_di = 100 * minus_dm.ewm(**w).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    return dx.ewm(**w).mean()


def calculate_bb_pctb(close: pd.Series, price: float,
                      period: int = 20, num_std: float = 2.0) -> float | None:
    """Bollinger %B of `price` vs the last completed band (population std)."""
    if len(close) < period:
        return None
    mid = float(close.rolling(period).mean().iloc[-1])
    sd  = float(close.rolling(period).std(ddof=0).iloc[-1])
    bw  = 2 * num_std * sd
    if not bw or bw != bw:
        return None
    lower = mid - num_std * sd
    return round((price - lower) / bw, 3)


def _safe_float(val) -> float | None:
    """Return float or None — converts nan/inf to None."""
    try:
        v = float(val)
        return None if (v != v or v == float("inf") or v == float("-inf")) else v
    except Exception:
        return None


# Keys produced by compute_daily_metrics(). get_metrics() None-fills these
# when the daily fetch fails so downstream dict access never KeyErrors.
DAILY_METRIC_KEYS = (
    "52W Low", "52W High", "52W_High_Date", "Days_Since_52W_High",
    "Pct_From_52W_Low%",
    "200MA", "50MA", "8EMA", "21EMA",
    "Price_vs_200MA%", "Price_vs_50MA%", "Pct_vs_8EMA", "Above_200MA",
    "ATR20", "ATR_Pct", "ATR Shrinking",
    "RVOL", "Vol_vs_20D", "VolumeDryingUp", "Pullback_Vol_Ratio",
    "RS", "Dist_52W_High%",
    "ADX_14", "Trend_Strength", "RSI_14", "BB_PctB",
    "CANSLIM_Pass",
    "Prev-Day Low", "Prev-Day High", "Prev-Day Close", "Gap%", "Gap_Now%",
    "_prior_52w_high", "_prior_52w_low",
    "Call_Candidate", "Call_Score", "Call_Strength", "Call_Reason", "Call_Strike_Hint"
)


def compute_daily_metrics(daily: pd.DataFrame, qqq_return_3m: float,
                          current_price: float | None = None,
                          asof_date=None,
                          eps_growth: float | None = None) -> dict:
    """
    Compute every metric derivable from a daily OHLCV frame. Pure: no network,
    and no clock dependence once `asof_date` is pinned — the live scanner calls
    this with the latest 1y of bars, a backtest calls it with bars sliced up to
    a historical date so every value is point-in-time correct.

    daily         : yfinance-layout OHLCV frame (~1y) ending at the as-of date.
                    Pass a dividend/split-adjusted series (auto_adjust=True) so
                    MAs/RSI/RS/52W stats don't see ex-dividend price drops.
    qqq_return_3m : benchmark 3-month return (%) for RS; must be computed on
                    the same adjustment convention as `daily`
    current_price : live quote if available; falls back to the last close
    asof_date     : datetime.date the metrics are "as of" (default: now, ET).
                    When the last bar carries this date it is treated as the
                    in-progress session: prev-day levels come from the bar
                    before it, Gap% from its open, RVOL from its volume.
    eps_growth    : EPS growth %, from fundamentals, for the CANSLIM composite
                    (a backtest without point-in-time fundamentals passes None)

    Returns {} if `daily` is empty. May raise on malformed frames — the caller
    handles the fallback (get_metrics None-fills DAILY_METRIC_KEYS).
    """
    if daily.empty:
        return {}

    row: dict = {}
    asof = asof_date or datetime.now(MARKET_TZ).date()
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
    row["Days_Since_52W_High"] = (asof - h_date).days

    # Current price — prefer the live quote, fall back to last close
    p = current_price or float(close.iloc[-1])
    row["Pct_From_52W_Low%"] = round((p / row["52W Low"] - 1) * 100, 1)

    # Prior session extremes
    prior = daily.iloc[:-1] if daily_dates[-1] == asof else daily
    row["_prior_52w_high"] = round(float(prior["High"].max()), 2) if len(prior) else None
    row["_prior_52w_low"]  = round(float(prior["Low"].min()),  2) if len(prior) else None

    # Prev completed day H/L
    prev = (daily.iloc[-2]
            if daily_dates[-1] == asof and len(daily) >= 2
            else daily.iloc[-1])
    row["Prev-Day Low"]   = round(float(prev["Low"]),   2)
    row["Prev-Day High"]  = round(float(prev["High"]),  2)
    prev_close = _safe_float(prev["Close"])
    row["Prev-Day Close"] = round(prev_close, 2) if prev_close else None

    # Gap vs prev completed close:
    #   Gap%     — today's open vs prev close (None until today's bar exists)
    #   Gap_Now% — current price vs prev close (implied gap; works pre-market)
    if prev_close:
        today_open = (_safe_float(daily["Open"].iloc[-1])
                      if daily_dates[-1] == asof else None)
        row["Gap%"]     = (round((today_open / prev_close - 1) * 100, 2)
                           if today_open else None)
        row["Gap_Now%"] = round((p / prev_close - 1) * 100, 2) if p else None
    else:
        row["Gap%"] = row["Gap_Now%"] = None

    # ── Moving averages ───────────────────────────────────────────────────
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

    # ── ATR ───────────────────────────────────────────────────────────────
    daily["ATR20"] = calculate_atr(daily)
    atr20     = _safe_float(daily["ATR20"].iloc[-1])
    atr20_10d = _safe_float(daily["ATR20"].iloc[-10]) if len(daily) >= 10 else atr20
    row["ATR20"]         = round(atr20, 2) if atr20 is not None else None
    row["ATR_Pct"]       = round(atr20 / p * 100, 2) if (atr20 and p) else None
    row["ATR Shrinking"] = (atr20 < atr20_10d) if (atr20 and atr20_10d) else None

    # ── Volume ────────────────────────────────────────────────────────────
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

    # ── RS vs QQQ — FIX-D: guard nan qqq_return_3m ────────────────────────
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

    # ── ADX / RSI / Bollinger — FIX-K: native implementations
    #    (pandas_ta removed; see calculate_rsi/adx/bb_pctb above)
    try:
        adx_series = calculate_adx(daily["High"], daily["Low"], close).dropna()
        adx_val = _safe_float(adx_series.iloc[-1]) if not adx_series.empty else None
        row["ADX_14"] = round(adx_val, 1) if adx_val is not None else None
        row["Trend_Strength"] = (
            "Strong"   if adx_val is not None and adx_val >= 40 else
            "Trending" if adx_val is not None and adx_val >= 25 else
            "Ranging"
        )
    except Exception as e:
        log.debug("ADX failed (%s)", e)
        row["ADX_14"] = None
        row["Trend_Strength"] = "Ranging"

    try:
        rsi_clean = calculate_rsi(close).dropna()
        rsi_val = _safe_float(rsi_clean.iloc[-1]) if not rsi_clean.empty else None
        row["RSI_14"] = round(rsi_val, 1) if rsi_val is not None else None
    except Exception as e:
        log.debug("RSI failed (%s)", e)
        row["RSI_14"] = None

    try:
        row["BB_PctB"] = calculate_bb_pctb(close, p)
    except Exception as e:
        log.debug("BB failed (%s)", e)
        row["BB_PctB"] = None

    # ── CANSLIM composite ─────────────────────────────────────────────────
    try:
        row["CANSLIM_Pass"] = all([
            eps_growth              is not None and eps_growth            > 20,
            row.get("RS")             is not None and row["RS"]             > 0,
            row.get("Vol_vs_20D")     is not None and row["Vol_vs_20D"]     > 1.2,
            row.get("Dist_52W_High%") is not None and row["Dist_52W_High%"] >= -15,
        ])
    except Exception:
        row["CANSLIM_Pass"] = None

    return row


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
      FIX-E2 Inst_Own%: heldPercentInstitutions (yfinance>=1.4) with legacy-key
             fallback; Inst_Own_Chg: pctHeld-weighted pctChange of top holders
             instead of heldPercentInstitutions which equals institutionPercentHeld.
      FIX-F  Entry gate ADX check only fires when ADX_14 is not None.
      FIX-G  _safe_float() guards all rolling/ewm values against nan/inf.
      FIX-H  CFG_MP_RSI_LO lowered 40->30 (NVDA 39.6, AAPL 32.2 were excluded).
      FIX-H  CFG_MP_RSI_LO lowered 40->30 (NVDA 39.6, AAPL 32.2 were excluded).
      FIX-I  CFG_MP_PULLBACK_VOL raised 0.8->1.2 (1.0 too tight for GOOGL 1.19).
      FIX-J  CFG_TA_RVOL lowered 1.5->1.3 (earnings beat reduces RVOL bar).
      FIX-L  Daily bars fetched auto_adjust=True (ex-div drops were skewing
             MAs/RSI/RS/52W stats vs the adjusted QQQ benchmark); daily-derived
             metrics extracted into compute_daily_metrics() for backtest reuse.
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
        inst = info.get("heldPercentInstitutions")       # yfinance >= 1.4 key
        if inst is None:
            inst = info.get("institutionPercentHeld")    # older yfinance key
        row["Inst_Own%"] = round(inst * 100, 1) if inst is not None else None
    except Exception:
        row["Inst_Own%"] = None

    # FIX-E2: institutional_holders now reports per-holder pctChange since the
    # last filing. Aggregate as the pctHeld-weighted net change of the top
    # reported holders (in % of shares outstanding; positive = accumulating).
    # Old proxy (top-N pctHeld sum − total Inst_Own%) was always ≤ 0, so the
    # "institutions buying" bonuses downstream could never trigger.
    try:
        ih = t.institutional_holders
        if (ih is not None and not ih.empty
                and {"pctHeld", "pctChange"} <= set(ih.columns)):
            sub = ih[["pctHeld", "pctChange"]].dropna()
            # pctChange of exactly 1.0 (+100%) is a filing-entity-rename
            # artifact (e.g. Vanguard shows it on every ticker), not real
            # buying; clip the rest so one extreme holder can't swamp the sum.
            sub = sub[sub["pctChange"] != 1.0]
            row["Inst_Own_Chg"] = (
                round(float((sub["pctHeld"]
                             * sub["pctChange"].clip(-1.0, 1.0)).sum()) * 100, 2)
                if not sub.empty else None
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
    # auto_adjust=True: indicators need a dividend/split-consistent series —
    # raw closes drop on every ex-div date, skewing MAs/RSI/RS/52W stats.
    # (fetch_qqq_return() uses the same flag so RS subtracts like from like.)
    try:
        daily = t.history(period="1y", interval="1d", auto_adjust=True)
        computed = compute_daily_metrics(
            daily, qqq_return_3m,
            current_price=row["Current Price"],
            eps_growth=row.get("EPS_Growth%"),
        )
        if computed:
            row.update(computed)
        else:
            for k in DAILY_METRIC_KEYS:
                row[k] = None
    except Exception as e:
        log.warning("%s: daily history failed (%s)", ticker, e)
        for k in DAILY_METRIC_KEYS:
            row[k] = None

    # ── 5. INTRADAY (pre-market + VWAP + ORB + time-adjusted RVOL) ───────────
    _intraday_none = {"Pre-Market Low": None, "Pre-Market High": None,
                      "VWAP": None, "Above_VWAP": None,
                      "ORB_High": None, "ORB_Low": None, "ORB_Status": None,
                      "RVOL_Intraday": None}
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

            reg_mask = [tm >= MARKET_OPEN for tm in times]
            reg_df   = intra[reg_mask]
            if not reg_df.empty and reg_df["Volume"].sum() > 0:
                typ = (reg_df["High"] + reg_df["Low"] + reg_df["Close"]) / 3
                row["VWAP"] = round(
                    float((typ * reg_df["Volume"]).sum() / reg_df["Volume"].sum()), 2
                )
            else:
                row["VWAP"] = None

            p_now = row.get("Current Price")
            row["Above_VWAP"] = (p_now > row["VWAP"]) if (p_now and row["VWAP"]) else None

            # ── Opening Range (9:30–9:45) ─────────────────────────────────────
            orb_df = intra[[MARKET_OPEN <= tm < ORB_END for tm in times]]
            if not orb_df.empty:
                orb_h = round(float(orb_df["High"].max()), 2)
                orb_l = round(float(orb_df["Low"].min()),  2)
                row["ORB_High"], row["ORB_Low"] = orb_h, orb_l
                # Status only once the range is complete (a bar exists ≥ 9:45)
                if any(tm >= ORB_END for tm in times) and p_now:
                    row["ORB_Status"] = ("above" if p_now > orb_h else
                                         "below" if p_now < orb_l else "inside")
                else:
                    row["ORB_Status"] = "forming"
            else:
                row["ORB_High"] = row["ORB_Low"] = row["ORB_Status"] = None

            # ── Time-adjusted RVOL ────────────────────────────────────────────
            # Session volume so far vs the average volume traded BY THE SAME
            # time-of-day over prior sessions. The daily RVOL column compares
            # against full-day averages, which reads ~0.3 all morning and
            # blinds every RVOL≥1.5 gate until the afternoon.
            try:
                row["RVOL_Intraday"] = None
                if not reg_df.empty:
                    reg_times    = [tm for tm, m in zip(times, reg_mask) if m]
                    cutoff       = max(reg_times)             # last bar of session so far
                    session_date = idx_et[-1].date()
                    today_cum    = float(reg_df["Volume"].sum())

                    h5 = t.history(period="10d", interval="5m", auto_adjust=False)
                    if h5 is not None and not h5.empty and today_cum > 0:
                        h5_et    = (h5.index.tz_convert(MARKET_TZ)
                                    if h5.index.tz is not None
                                    else h5.index.tz_localize(MARKET_TZ))
                        h5_dates = h5_et.date
                        h5_times = h5_et.time
                        prior = {}
                        for d, tm, v in zip(h5_dates, h5_times, h5["Volume"]):
                            if d != session_date and MARKET_OPEN <= tm <= cutoff and v == v:
                                prior[d] = prior.get(d, 0.0) + float(v)
                        if len(prior) >= 3:
                            avg_cum = sum(prior.values()) / len(prior)
                            if avg_cum > 0:
                                row["RVOL_Intraday"] = round(today_cum / avg_cum, 2)
            except Exception as e:
                log.debug("%s: intraday RVOL failed (%s)", ticker, e)
                row["RVOL_Intraday"] = None

            # RVOL: the daily calc divides today's partial volume by a full-day
            # average, so it reads ~0.3 all morning and poisons every RVOL gate
            # (categorize said "RVOL too_low" on stocks trading 2x normal pace,
            # and put_candidate read it as "buyers fading"). Promote the
            # time-adjusted value; keep the raw daily calc as RVOL_EOD.
            row["RVOL_EOD"] = row.get("RVOL")
            if row.get("RVOL_Intraday") is not None:
                row["RVOL"] = row["RVOL_Intraday"]
        else:
            row.update(_intraday_none)
    except Exception as e:
        log.debug("%s: intraday failed (%s)", ticker, e)
        row.update(_intraday_none)
    row.setdefault("RVOL_EOD", row.get("RVOL"))   # always present, even w/o intraday

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
    return row


def main() -> None:
    """Fetch and print all metrics for the tickers given on the command line."""
    tickers = [t.upper() for t in sys.argv[1:]] or ["NVDA"]
    try:
        qqq = yf.Ticker("QQQ").history(period="3mo")["Close"]
        qqq_3m = float((qqq.iloc[-1] / qqq.iloc[0] - 1) * 100)
    except Exception:
        qqq_3m = 0.0
    for ticker in tickers:
        row = get_metrics(ticker, qqq_3m)
        print(f"\n── {ticker} {'─' * 60}")
        for key, value in row.items():
            print(f"  {key:<22}: {value}")


if __name__ == "__main__":
    main()




