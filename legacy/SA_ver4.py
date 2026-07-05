"""
stock_categorizer.py
====================
Scans a watchlist / S&P 500 universe, fetches all metrics via yfinance,
categorises every ticker using a structured decision tree, and writes two CSV files:

  stock_scan_YYYYMMDD_HHMM.csv        – full metrics + category + reasons
  stock_scan_YYYYMMDD_HHMM_summary.csv – one row per category, sorted by rank score

Categories (in priority order)
-------------------------------
  Momentum          – within 10% of 52W high, trending strongly (day + swing)
  Momentum-Pullback – 10–30% off high, healthy low-volume pullback (swing 3–10 days)
  VCP Setup         – 20–45% off high, volatility contracting, quiet base (swing 1–3 wk)
  Turnaround        – 40–65% off high, catalyst present, recovering (swing/speculative)
  Longterm Hold     – near 52W low, rising inst. ownership, strong fundamentals (6–18 mo)
  Avoid             – fails entry gate OR no category matched

Fix log vs original
-------------------
  FIX-1   CFG_MP_RSI_LO  40→30  (NVDA 39.6, AAPL 32.2 were wrongly excluded)
  FIX-2   CFG_MP_PULLBACK_VOL  0.8→1.2  (catches GOOGL 1.19, still blocks AMZN 2.12)
  FIX-3   CFG_TA_RVOL  1.5→1.3  (PLTR 1.4 + earnings beat = valid Turnaround)
  FIX-4   Momentum ADX: dynamic floor — relax to 15 when dist≥-5% AND
           days_since_52wh≤5 (GLW: new 52W high 2 days ago, ADX always lags)
  FIX-5   Momentum RS: skip check when RS is None (QQQ data gap — don't penalise)
  FIX-6   categorize() elif order: Momentum-Pullback BEFORE VCP.
           Overlap zone -30% to -20%: MP has tighter criteria so it runs first;
           stocks that fail MP fall through to VCP.
           (BE, AVGO, CRDO, COHR, GOOGL were landing in VCP instead of MP)
  FIX-7   Momentum-Pullback: remove pct_vs_8EMA as hard gate; demote to quality note.
           Pullback stocks are typically 3–15% below 8EMA — ±1% gate was
           blocking every valid MP setup (GOOGL -3.9%, COHR -4.3%, BE -18.4%).
  FIX-8   VCP: RVOL<0.8 restored as hard gate (quiet base IS the VCP definition).
           Was demoted to quality note in prior pass, which let MSFT (4.46),
           META (1.10), HOOD (1.38) pass VCP incorrectly.
  FIX-9   VCP: above_200MA removed as hard gate; kept as quality note.
  FIX-10  Longterm inst_chg: explicit None guard —
           `inst_chg is not None and inst_chg >= 0`
           (was: `inst_chg >= 0` treated None as 0 and passed MSFT incorrectly)
  FIX-11  Longterm RS floor: skip check when RS is None (same as Momentum FIX-5)

Usage
-----
  pip install yfinance pandas pandas_ta pytz
  python stock_categorizer.py
  python stock_categorizer.py --tickers NVDA AAPL MSFT TSLA
"""

import argparse
import logging
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from libs.write_metrics_csv import save_metrics_to_csv
from libs.get_metrics3 import get_metrics

import pandas as pd
import yfinance as yf
import pytz

warnings.filterwarnings("ignore")
out_dir = Path(r"/Reports")

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("categorizer")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
MARKET_TZ       = pytz.timezone("America/New_York")
PREMARKET_START = datetime.strptime("04:00", "%H:%M").time()
MARKET_OPEN     = datetime.strptime("09:30", "%H:%M").time()

# Entry gate
CFG_MKTCAP_MIN          = 1_000_000_000
CFG_PRICE_MIN           = 5.0
CFG_RVOL_MIN_GATE       = 0.6
CFG_ADX_MIN_GATE        = 15
CFG_MAX_PCT_BELOW_200MA = -30

# Momentum
CFG_MOM_DIST_MAX          = -10
CFG_MOM_RVOL              = 0.8
CFG_MOM_ADX               = 20
CFG_MOM_ADX_BREAKOUT      = 15   # FIX-4: relaxed ADX for fresh 52W-high breakouts
CFG_MOM_ADX_BREAKOUT_DAYS = 5    # FIX-4: "fresh" = set within last N days
CFG_MOM_RS_MIN            = 0
CFG_MOM_DAYS_SINCE_52WH   = 10

# Momentum-Pullback
CFG_MP_DIST_MIN         = -30
CFG_MP_DIST_MAX         = -10
CFG_MP_RSI_LO           = 30    # FIX-1: was 40
CFG_MP_RSI_HI           = 65
CFG_MP_PULLBACK_VOL     = 1.25   # FIX-2: was 0.8
CFG_MP_BB_PCTB          = 0.4   # quality check only
CFG_MP_RVOL             = 1.0   # quality check only
CFG_MP_8EMA_NOTE_LO     = -15.0 # FIX-7: for quality note only (was hard gate ±1%)
CFG_MP_8EMA_NOTE_HI     =  2.0

# VCP Setup
CFG_VCP_DIST_MIN        = -45
CFG_VCP_DIST_MAX        = -20
CFG_VCP_BASE_RVOL       = 0.8   # FIX-8: hard gate (quiet base definition)
CFG_VCP_ADX_MIN         = 18    # hard gate

# Turnaround
CFG_TA_DIST_MIN         = -65
CFG_TA_DIST_MAX         = -40
CFG_TA_RVOL             = 1.3   # FIX-3: was 1.5
CFG_TA_SHORT_INT        = 10

# Longterm Hold
CFG_LT_PCT_FROM_LOW_MAX = 10
CFG_LT_RVOL             = 1.2
CFG_LT_EPS_GROWTH       = 15
CFG_LT_INST_OWN_MIN     = 40
CFG_LT_RS_MIN           = -25

# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSES
# ─────────────────────────────────────────────────────────────────────────────


# Longterm category is only evaluated for this curated list of high-quality names
LONGTERM_TICKERS = [
    "PLTR", "TSLA", "AMZN", "META", "GOOGL", "MSFT",
    "NVDA", "AVGO", "TSM", "ASML", "LLY", "GLD", "AMD", "SPCX"
]

SP500_TICKERS_SEL = [

    "MU", "LITE", "CIEN", "COHR", "SPY",
    "BE", "MRVL", "CRDO", "GLW", "AAOI", "INTC",
    "CRWV", "HOOD", "INOD", "DRAM", "OKLO", "IREN",
    "SMCI", "HIMS", "USAR", "NVTS", "FLY", "RGTI","APH"
    "SOFI", "IONQ", "SNDK", "HOOD", "NVDA", "AVGO", "TSM", "ASML", "LLY", "GLD",
    "AMD", "SPCX", "AVGO", "DRAM"]

SP500_TICKERS = [

    "MMM", "AOS", "ABT", "ABBV", "ACN", "ADBE", "AMD", "AES", "AFL", "A", "APD", "ABNB", "AKAM",
    "ALB","ARE", "ALGN", "ALLE", "LNT", "ALL", "GOOGL", "GOOG", "MO", "AMZN", "AMCR", "AEE", "AAL", "AEP",
    "AXP", "AIG", "AMT", "AWK", "AMP", "AME", "AMGN", "APH", "ADI", "ANSS", "AON", "APA", "AAPL",
    "AMAT", "APTV", "ACGL", "ADM", "ANET", "AJG", "AIZ", "T", "ATO", "ADSK", "ADP", "AZO", "AVB",
    "AVY", "AXON", "BKR", "BALL", "BAC", "BK", "BBWI", "BAX", "BDX", "WRB", "BBY", "BIO", "TECH",
    "BIIB", "BLK", "BX", "BA", "BSX", "BMY", "AVGO", "BR", "BRO", "BF-B", "BLDR", "BG", "CDNS",
    "CZR", "CPT", "CPB", "COF", "CAH", "KMX", "CCL", "CARR", "CTLT", "CAT", "CBOE", "CBRE", "CDW",
    "CE", "COR", "CNC", "CNP", "CF", "CHRW", "CRL", "SCHW", "CHTR", "CVX", "CMG", "CB", "CHD", "CI",
    "CINF", "CTAS", "CSCO", "C", "CFG", "CLX", "CME", "CMS", "KO", "CTSH", "CL", "CMCSA", "CMA",
    "CAG", "COP", "ED", "STZ", "CEG", "COO", "CPRT", "GLW", "CPAY", "CTVA", "CSGP", "COST", "CTRA",
    "CCI", "CSX", "CMI", "CVS", "DHR", "DRI", "DVA", "DAY", "DECK", "DE", "DAL", "DVN", "DXCM",
    "FANG", "DLR", "DFS", "DG", "DLTR", "D", "DPZ", "DOV", "DHI", "DTE", "DUK", "DD", "EMN", "ETN",
    "EBAY", "ECL", "EIX", "EW", "EA", "ELV", "LLY", "EMR", "ENPH", "ETR", "EOG", "EPAM", "EQT",
    "EFX", "EQIX", "EQR", "ESS", "EL", "ETSY", "EG", "ES", "EXC", "EXPE", "EXPD", "EXR", "XOM",
    "FFIV", "FDS", "FICO", "FAST", "FRT", "FDX", "FIS", "FITB", "FSLR", "FE", "FI", "FMC", "F",
    "FTNT", "FTV", "FOXA", "FOX", "BEN", "FCX", "GRMN", "IT", "GE", "GEHC", "GEV", "GEN", "GNRC",
    "GD", "GIS", "GM", "GPC", "GILD", "GS", "HAL", "HIG", "HAS", "HCA", "DOC", "HSIC", "HSY", "HES",
    "HPE", "HLT", "HOLX", "HD", "HON", "HRL", "HST", "HWM", "HPQ", "HUBB", "HUM", "HBAN", "HII",
    "IBM", "IEX", "IDXX", "ITW", "INCY", "IR", "PODD", "INTC", "ICE", "IFF", "IP", "IPG", "INTU",
    "ISRG", "IVZ", "INVH", "IQV", "IRM", "JBHT", "JBL", "JKHY", "J", "JNJ", "JCI", "JPM", "JNPR",
    "K", "KVUE", "KDP", "KEY", "KEYS", "KMB", "KIM", "KMI", "KLAC", "KHC", "KR", "LHX", "LH",
    "LRCX", "LW", "LVS", "LDOS", "LEN", "LIN", "LYV", "LKQ", "LMT", "L", "LOW", "LULU", "LYB",
    "MTB", "MRO", "MPC", "MKTX", "MAR", "MMC", "MLM", "MAS", "MA", "MTCH", "MKC", "MCD", "MCK",
    "MDT", "MRK", "META", "MET", "MTD", "MGM", "MCHP", "MU", "MSFT", "MAA", "MRNA", "MHK", "MOH",
    "TAP", "MDLZ", "MPWR", "MNST", "MCO", "MS", "MOS", "MSI", "MSCI", "NDAQ", "NTAP", "NFLX",
    "NEM", "NWSA", "NWS", "NEE", "NKE", "NI", "NDSN", "NSC", "NTRS", "NOC", "NCLH", "NRG", "NUE",
    "NVDA", "NVR", "NXPI", "ORLY", "OXY", "ODFL", "OMC", "ON", "OKE", "ORCL", "OTIS", "PCAR",
    "PKG", "PANW", "PARA", "PH", "PAYX", "PAYC", "PYPL", "PNR", "PEP", "PFE", "PCG", "PM", "PSX",
    "PNW", "PNC", "POOL", "PPG", "PPL", "PFG", "PG", "PGR", "PLD", "PRU", "PEG", "PTC", "PSA",
    "PHM", "PWR", "QCOM", "DGX", "RL", "RJF", "RTX", "O", "REG", "REGN", "RF", "RSG", "RMD", "RVTY",
    "ROK", "ROL", "ROP", "ROST", "RCL", "SPGI", "CRM", "SBAC", "SLB", "STX", "SEE", "SRE", "NOW",
    "SHW", "SPG", "SWKS", "SJM", "SW", "SNA", "SOLV", "SO", "LUV", "SWK", "SBUX", "STT", "STLD",
    "STE", "SYK", "SMCI", "SYF", "SNPS", "SYY", "TMUS", "TROW", "TTWO", "TPR", "TRGP", "TGT",
    "TEL", "TDY", "TFX", "TER", "TSLA", "TXN", "TXT", "TMO", "TJX", "TSCO", "TT", "TDG", "TRV",
    "TRMB", "TFC", "TYL", "TSN", "USB", "UBER", "UDR", "ULTA", "UNP", "UAL", "UPS", "URI", "UNH",
    "UHS", "VLO", "VTR", "VRSN", "VRSK", "VZ", "VRTX", "VLTO", "VFC", "VTRS", "VICI", "V", "VST",
    "WAB", "WBA", "WMT", "DIS", "WBD", "WM", "WAT", "WEC", "WFC", "WELL", "WST", "WDC", "WY", "WHR",
    "WMB", "WTW", "GWW", "WYNN", "XEL", "XYL", "YUM", "ZBRA", "ZBH", "ZTS",
]


# ─────────────────────────────────────────────────────────────────────────────
# ATR helper
# ─────────────────────────────────────────────────────────────────────────────
def calculate_atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ─────────────────────────────────────────────────────────────────────────────
# QQQ benchmark
# ─────────────────────────────────────────────────────────────────────────────
def fetch_qqq_return() -> float:
    """Fetch QQQ 3-month return as benchmark for RS calculation."""
    try:
        qqq = yf.Ticker("QQQ").history(period="1y", interval="1d", auto_adjust=False)
        if len(qqq) >= 63:
            close = qqq["Close"].ffill()   # guard against trailing NaN row
            c_now = float(close.iloc[-1])
            c_63  = float(close.iloc[-63])
            if c_now and c_63:
                result = round(((c_now / c_63) - 1) * 100, 2)
                log.info("QQQ 3M return: %.2f%%", result)
                return result
    except Exception as e:
        log.warning("QQQ fetch failed: %s", e)
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIZE  –  full decision tree
# ─────────────────────────────────────────────────────────────────────────────
def categorize(row: dict) -> tuple:
    """
    Returns (category: str, reason: str, rank_score: int).
    rank_score: higher = stronger setup within the same category.

    elif order (matters for overlap zone -30% to -20%):
      Momentum → Momentum-Pullback → VCP Setup → Turnaround → Longterm → Avoid
    """

    def v(key, default=None):
        val = row.get(key)
        return val if val is not None else default

    # ── Step 0: Entry gate ────────────────────────────────────────────────────
    if not row.get("Entry_Gate_Pass", False):
        return (
            "Avoid",
            "Entry gate failed: " + (row.get("Entry_Gate_Reason") or "unknown"),
            0,
        )

    dist       = v("Dist_52W_High%",     -999)
    days_52h   = v("Days_Since_52W_High", 999)
    above200   = v("Above_200MA",         False)
    rvol       = v("RVOL",                0.0)
    rs         = row.get("RS")                    # None = QQQ data unavailable (FIX-5)
    adx        = v("ADX_14",              0.0)
    rsi        = v("RSI_14",             50.0)
    pct_ema8   = v("Pct_vs_8EMA",        999)
    pull_vol   = v("Pullback_Vol_Ratio",  999)
    bb_pctb    = v("BB_PctB",             1.0)
    atr_shr    = v("ATR Shrinking",       False)
    eps_g      = v("EPS_Growth%",        -999)
    inst_own   = v("Inst_Own%",           0.0)
    inst_chg   = row.get("Inst_Own_Chg")          # None = unavailable (FIX-10)
    fcf        = v("FCF_Positive",        False)
    si         = v("Short_Interest%",     0.0)
    eb         = v("EarningsBeat",        False)
    pct_low    = v("Pct_From_52W_Low%",  999)
    p50pct     = v("Price_vs_50MA%",     -999)
    above_vwap = v("Above_VWAP",         False)

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 1 — MOMENTUM
    # Hard gates: within 10% of 52W high · above 200MA · RVOL ≥ 0.8
    #             · RS ≥ 0 (skipped if None) · ADX ≥ 20 (15 for fresh breakout)
    # ═════════════════════════════════════════════════════════════════════════
    # FIX-4: relax ADX when stock just set a new 52W high (ADX lags breakouts)
    is_fresh_breakout = (dist >= -5) and (days_52h <= CFG_MOM_ADX_BREAKOUT_DAYS)
    adx_floor = CFG_MOM_ADX_BREAKOUT if is_fresh_breakout else CFG_MOM_ADX

    c_m_dist  = dist  >= CFG_MOM_DIST_MAX
    c_m_200ma = above200
    c_m_rvol  = rvol  >= CFG_MOM_RVOL
    c_m_rs    = rs is None or rs >= CFG_MOM_RS_MIN   # FIX-5: skip if unavailable
    c_m_adx   = adx   >= adx_floor

    if all([c_m_dist, c_m_200ma, c_m_rvol, c_m_rs, c_m_adx]):
        passed = [
            f"dist_52wh≥{CFG_MOM_DIST_MAX}%", "above_200MA",
            f"RVOL≥{CFG_MOM_RVOL}",
            f"RS≥{CFG_MOM_RS_MIN}" + ("(skipped-no_data)" if rs is None else ""),
            f"ADX≥{adx_floor}" + ("(breakout_relaxed)" if is_fresh_breakout else ""),
        ]
        bonuses = []
        if above_vwap:
            bonuses.append("above_VWAP")
        if days_52h <= CFG_MOM_DAYS_SINCE_52WH:
            bonuses.append(f"52WH_set_{days_52h}d_ago")
        else:
            bonuses.append(f"note:52WH_set_{days_52h}d_ago(extended)")
        if rs is None:
            bonuses.append("RS_data_unavailable")

        rs_val = rs if rs is not None else 0.0
        score  = int(rs_val * 2 + rvol * 10 + adx)
        reason = "PASSED: " + ", ".join(passed)
        if bonuses:
            reason += " | " + ", ".join(bonuses)
        return "Momentum", reason, score

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 2 — MOMENTUM-PULLBACK  (FIX-6: checked BEFORE VCP)
    # Hard gates: 10–30% off high · RSI 30–65 · pullback_vol ≤ 1.2
    # Quality:    above_200MA · pct_vs_8EMA · BB%B · RVOL
    # FIX-7: pct_vs_8EMA removed as hard gate (pullback stocks are 3–15% below 8EMA)
    # ═════════════════════════════════════════════════════════════════════════
    c_mp_dist = CFG_MP_DIST_MIN <= dist < CFG_MP_DIST_MAX
    c_mp_rsi  = CFG_MP_RSI_LO  <= rsi  <= CFG_MP_RSI_HI   # FIX-1: lo=30
    c_mp_pvol = pull_vol <= CFG_MP_PULLBACK_VOL     # FIX-2: thresh=1.2

    pvol_note = (
        f"pullback_vol={pull_vol:.2f}{'✓' if pull_vol <= CFG_MP_PULLBACK_VOL else '⚠'}"
        if pull_vol < 999 else "pullback_vol=none(all_green_days)"
    )
    if all([c_mp_dist, c_mp_rsi, c_mp_pvol]):
        # Quality notes
        q_200ma = above200
        q_ema8  = CFG_MP_8EMA_NOTE_LO <= pct_ema8 <= CFG_MP_8EMA_NOTE_HI
        q_bb    = bb_pctb <= CFG_MP_BB_PCTB
        q_rvol  = rvol >= CFG_MP_RVOL
        rs_val  = rs if rs is not None else 0.0

        quality = [
            f"above_200MA={'yes✓' if q_200ma else 'no⚠'}",
            f"pct_vs_8EMA={pct_ema8:.1f}%{'✓' if q_ema8 else '(extended_below)'}",
            f"pullback_vol={pull_vol:.2f}{'✓' if pull_vol <= CFG_MP_PULLBACK_VOL else '⚠'}"
            if pull_vol < 999 else "pullback_vol=none(all_green_days)",
            f"BB_%B={bb_pctb:.3f}{'✓' if q_bb else ''}",
            f"RVOL={rvol:.2f}{'✓' if q_rvol else '⚠'}",
            f"RS={rs:.1f}{'✓' if rs>=0 else '(lagging_QQQ)⚠'}" if rs is not None else "RS=unavailable",
        ]



        score = max(0, int(
            abs(dist) * -2 + rs_val * 2
            + (10 if c_mp_pvol else 0)
            + (5 if q_bb else 0)
            + (5 if q_rvol else 0)
            + (5 if q_200ma else 0)
        ))
        passed = f"dist_52wh[{CFG_MP_DIST_MIN}%,{CFG_MP_DIST_MAX}%), RSI[{CFG_MP_RSI_LO},{CFG_MP_RSI_HI}], pullback_vol≤{CFG_MP_PULLBACK_VOL}"
        return "Momentum-Pullback", f"PASSED: {passed} | {'; '.join(quality)}", score

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 3 — VCP SETUP  (FIX-6: checked AFTER MP)
    # Hard gates: 20–45% off high · ADX ≥ 18 · RVOL < 0.8 (quiet base)
    # Quality:    above_200MA · ATR shrinking · inst_own rising
    # FIX-8: RVOL<0.8 is a hard gate (quiet base IS the VCP definition)
    # FIX-9: above_200MA demoted to quality note
    # ═════════════════════════════════════════════════════════════════════════
    c_vcp_dist = CFG_VCP_DIST_MIN <= dist < CFG_VCP_DIST_MAX
    c_vcp_adx  = adx  >= CFG_VCP_ADX_MIN
    c_vcp_rvol = rvol <  CFG_VCP_BASE_RVOL   # FIX-8: hard gate

    if all([c_vcp_dist, c_vcp_adx, c_vcp_rvol]):
        q_200ma = above200
        q_atr   = atr_shr
        q_inst  = inst_chg is not None and inst_chg > 0   # FIX-10 guard
        rs_note = f"RS={rs:.1f}{'✓' if rs>=0 else '⚠'}" if rs is not None else "RS=unavailable"

        quality = [
            f"above_200MA={'yes✓' if q_200ma else 'no⚠'}",
            f"ATR_contracting={'yes✓' if q_atr else 'no⚠'}",
            f"base_RVOL={rvol:.2f}✓",
            f"ADX={adx:.1f}✓",
            f"inst_own_rising={'yes✓' if q_inst else 'unclear'}",
            rs_note,
        ]
        score = max(0, int(
            abs(dist) * -1
            + (15 if q_atr   else 0)
            + (10 if q_200ma else 0)
            + (5  if c_vcp_adx else 0)
            + (5  if q_inst  else 0)
        ))

        return (
            "VCP Setup",
            f"PASSED: dist_52wh[{CFG_VCP_DIST_MIN}%,{CFG_VCP_DIST_MAX}%), ADX≥{CFG_VCP_ADX_MIN}, RVOL<{CFG_VCP_BASE_RVOL} | {'; '.join(quality)}",
            score,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 4 — TURNAROUND
    # Hard gates: 40–65% off high · (RVOL ≥ 1.3 OR earnings beat)
    # Quality:    short interest ≥ 10% · above 50MA
    # ═════════════════════════════════════════════════════════════════════════
    c_ta_dist  = CFG_TA_DIST_MIN <= dist < CFG_TA_DIST_MAX
    c_ta_rvol  = rvol >= CFG_TA_RVOL
    c_ta_earn  = eb is True
    c_ta_short = si >= CFG_TA_SHORT_INT
    c_ta_50ma  = p50pct > 0

    if c_ta_dist and (c_ta_rvol or c_ta_earn):
        checks = [f"dist_52wh={dist:.1f}%✓"]
        if c_ta_rvol:
            checks.append(f"reversal_RVOL={rvol:.2f}✓")
        if c_ta_earn:
            checks.append("earnings_beat✓")
        quality = []
        if c_ta_short:
            quality.append(f"short_int={si:.1f}%(squeeze_fuel✓)")
        quality.append(f"{'above' if c_ta_50ma else 'below'}_50MA({p50pct:+.1f}%{'✓' if c_ta_50ma else '⚠'})")
        rs_note = f"RS={rs:.1f}{'✓' if rs>=0 else '⚠'}" if rs is not None else "RS=unavailable"
        quality.append(rs_note)

        score = int(
            (100 + dist) * 2
            + rvol * 5
            + (20 if c_ta_earn  else 0)
            + (10 if c_ta_short else 0)
            + (5  if c_ta_50ma  else 0)
        )
        return (
            "Turnaround",
            f"PASSED: {', '.join(checks)} | {'; '.join(quality)}",
            score,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 5 — LONGTERM HOLD
    # Hard gates: curated ticker · within 10% of 52W low · RVOL ≥ 1.2
    #             · RS > -25 or None (FIX-11)
    # Quality:    EPS ≥ 15% · FCF+ · inst_own ≥ 40% & rising
    # ═════════════════════════════════════════════════════════════════════════
    c_lt_tick  = row["Ticker"] in LONGTERM_TICKERS
    c_lt_low   = 0 <= pct_low <= CFG_LT_PCT_FROM_LOW_MAX
    c_lt_rvol  = rvol >= CFG_LT_RVOL
    c_lt_rs    = rs is None or rs > CFG_LT_RS_MIN   # FIX-11: skip if unavailable
    c_lt_eps   = eps_g >= CFG_LT_EPS_GROWTH
    c_lt_fcf   = fcf is True
    c_lt_inst  = inst_own >= CFG_LT_INST_OWN_MIN
    # FIX-10: explicit None guard (None ≠ 0; missing data ≠ rising ownership)
    c_lt_instc = inst_chg is not None and inst_chg >= 0

    if all([c_lt_tick, c_lt_low, c_lt_rvol, c_lt_rs]):
        fund = [
            f"EPS_growth={eps_g:.1f}%{'✓' if c_lt_eps else '⚠'}",
            f"FCF={'positive✓' if c_lt_fcf else 'negative⚠'}",
            f"inst_own={inst_own:.1f}%{'✓' if c_lt_inst else ''}"
            + ("_rising✓" if c_lt_instc else "_flat/falling⚠"),
        ]
        if rs is not None:
            fund.append(f"RS={rs:.1f}{'✓' if rs > CFG_LT_RS_MIN else '⚠'}")

        score = int(
            (eps_g if c_lt_eps else 0)
            + inst_own
            + (20 if c_lt_fcf   else 0)
            + (10 if c_lt_instc else 0)
        )
        return (
            "Longterm Hold",
            f"PASSED: curated_ticker, pct_from_52wl={pct_low:.1f}%, RVOL={rvol:.2f} | {'; '.join(fund)}",
            score,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # AVOID
    # ═════════════════════════════════════════════════════════════════════════
    avoid_why = []
    if dist < CFG_TA_DIST_MIN:
        avoid_why.append(f"dist_52wh={dist:.1f}%(too_extended_down)")
    elif dist < CFG_MOM_DIST_MAX:
        avoid_why.append(f"dist_52wh={dist:.1f}%(no_category_matched)")
    if not above200:
        avoid_why.append("below_200MA")
    if rvol < CFG_RVOL_MIN_GATE:
        avoid_why.append(f"RVOL={rvol:.2f}(too_low)")
    if rs is not None and rs < 0:
        avoid_why.append(f"RS={rs:.1f}(underperforming_QQQ)")
    if adx < CFG_ADX_MIN_GATE:
        avoid_why.append(f"ADX={adx:.1f}(no_trend)")
    if not avoid_why:
        avoid_why.append("no_category_criteria_met")

    return "Avoid", "No match: " + "; ".join(avoid_why), 0


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main(tickers: list) -> None:
    run_ts = datetime.now().strftime("%Y%m%d_%H%M")

    log.info("Fetching QQQ 3-month return …")
    qqq_3m = fetch_qqq_return()
    log.info("QQQ 3m return: %.2f%%", qqq_3m)

    log.info("Scanning %d tickers …", len(tickers))
    rows = []

    for i, ticker in enumerate(tickers, 1):
        log.info("[%d/%d] %s", i, len(tickers), ticker)
        try:
            metrics = get_metrics(ticker, qqq_3m)
            cat, reason, score = categorize(metrics)
            metrics["Category"]   = cat
            metrics["Cat_Reason"] = reason
            metrics["Rank_Score"] = score
            rows.append(metrics)
        except Exception as e:
            log.error("%s: unexpected error — %s", ticker, e)
            rows.append({
                "Ticker":            ticker,
                "Category":          "Error",
                "Cat_Reason":        str(e),
                "Rank_Score":        0,
                "Entry_Gate_Pass":   False,
                "Entry_Gate_Reason": str(e),
            })
        time.sleep(0.25)

    save_metrics_to_csv(rows, output_dir=out_dir)

    # ── DataFrame + column ordering ──────────────────────────────────────────
    df = pd.DataFrame(rows)

    priority = [
        "Ticker", "LongName", "Sector",
        "Category", "Cat_Reason", "Rank_Score",
        "Entry_Gate_Pass", "Entry_Gate_Reason",
        "Current Price", "Dist_52W_High%", "Pct_From_52W_Low%",
        "52W_High_Date", "Days_Since_52W_High",
        "RVOL", "Vol_vs_20D", "Pullback_Vol_Ratio",
        "RSI_14", "ADX_14", "Trend_Strength", "BB_PctB",
        "200MA", "50MA", "8EMA", "21EMA",
        "Price_vs_200MA%", "Price_vs_50MA%", "Pct_vs_8EMA",
        "Above_200MA", "Above_VWAP", "VWAP",
        "ATR20", "ATR_Pct", "ATR Shrinking",
        "RS", "CANSLIM_Pass",
        "EPS_Growth%", "Revenue", "Inst_Own%", "Inst_Own_Chg",
        "FCF_Positive", "Short_Interest%", "EarningsBeat", "EarningsDate",
        "MarketCap",
        "52W High", "52W Low",
        "Prev-Day High", "Prev-Day Low",
        "Pre-Market High", "Pre-Market Low",
    ]
    existing = [c for c in priority if c in df.columns]
    rest     = [c for c in df.columns if c not in priority and not c.startswith("_")]
    df = df[existing + rest]

    cat_order = {
        "Momentum": 1, "Momentum-Pullback": 2, "VCP Setup": 3,
        "Turnaround": 4, "Longterm Hold": 5, "Avoid": 6, "Error": 7,
    }
    df["_sort"] = df["Category"].map(cat_order).fillna(8)
    df = df.sort_values(["_sort", "Rank_Score"], ascending=[True, False]).drop(columns=["_sort"])

    # ── Full CSV ──────────────────────────────────────────────────────────────
    full_path = out_dir / f"stock_scan_{run_ts}.csv"
    df.to_csv(full_path, index=False)
    log.info("Full results → %s  (%d rows)", full_path, len(df))

    # ── Summary CSV ───────────────────────────────────────────────────────────
    summary_rows = []
    for cat in ["Momentum", "Momentum-Pullback", "VCP Setup",
                "Turnaround", "Longterm Hold", "Avoid", "Error"]:
        sub = df[df["Category"] == cat]
        if sub.empty:
            continue
        top5 = sub.nlargest(5, "Rank_Score")[["Ticker", "Rank_Score", "Cat_Reason"]]
        summary_rows.append({
            "Category": cat,
            "Count": len(sub),
            "Top_Tickers": ", ".join(str(t) for t in top5["Ticker"].tolist()),
            "Top_Scores": ", ".join(str(s) for s in top5["Rank_Score"].tolist()),
        })
    summary_df   = pd.DataFrame(summary_rows)
    summary_path = out_dir / f"stock_scan_{run_ts}_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    log.info("Summary → %s", summary_path)

    # ── Console output ────────────────────────────────────────────────────────
    sep = "═" * 74
    print(f"\n{sep}")
    print(f"  SCAN COMPLETE  |  {run_ts}  |  {len(df)} tickers  |  QQQ 3m={qqq_3m:.2f}%")
    print(sep)
    for _, r in summary_df.iterrows():
        print(f"  {r['Category']:<22}  {r['Count']:>4} stocks   top: {r['Top_Tickers']}")
    print(sep)
    print(f"\n  Full CSV    : {full_path.resolve()}")
    print(f"  Summary CSV : {summary_path.resolve()}\n")

    for cat in ["Momentum", "Momentum-Pullback", "VCP Setup", "Turnaround", "Longterm Hold"]:
        sub = df[df["Category"] == cat]
        if sub.empty:
            continue
        print(f"\n── {cat} ({len(sub)}) {'─' * (60 - len(cat))}")
        show_cols = ["Ticker", "Current Price", "Dist_52W_High%",
                     "RVOL", "RSI_14", "ADX_14", "RS", "Rank_Score", "Cat_Reason"]
        show_cols = [c for c in show_cols if c in sub.columns]
        print(sub[show_cols].to_string(index=False, max_colwidth=60))


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="S&P 500 stock categorizer")
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Override universe (e.g. --tickers NVDA AAPL MSFT TSLA)",
    )
    args     = parser.parse_args()
    universe = args.tickers if args.tickers else SP500_TICKERS
    main(universe)