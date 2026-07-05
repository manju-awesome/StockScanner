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
  VCP Setup         – 20–45% off high, volatility contracting, base forming (swing 1–3 wk)
  Turnaround        – 40–65% off high, catalyst present, recovering (swing/speculative)
  Longterm Hold     – near 52W low, rising inst. ownership, strong fundamentals (6–18 mo)
  Avoid             – fails entry gate OR no category matched

Usage
-----
  pip install yfinance pandas pandas_ta pytz
  python stock_categorizer.py

  # Optionally override the ticker list:
  python stock_categorizer.py --tickers NVDA AAPL MSFT TSLA

Edit the CONFIG section below to adjust thresholds.
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
out_dir = r"/Reports"
out_dir = Path(out_dir)

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
# CONFIG  –  edit thresholds here
# ─────────────────────────────────────────────────────────────────────────────
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
CFG_LT_INST_OWN_MIN      = 40 # institutional ownership > 40%
CFG_LT_RS_MIN = -25

# Curated longterm universe (expand as needed)
LONGTERM_TICKERS: set = {
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "BRK-B",
    "JNJ",  "JPM",  "V",    "MA",    "UNH",  "LLY",  "AVGO",  "TSM",
}

# S&P 500 universe (override via --tickers CLI)




# Longterm category is only evaluated for this curated list of high-quality names
LONGTERM_TICKERS = [
    "PLTR", "TSLA", "AMZN", "META", "GOOGL", "MSFT",
    "NVDA", "AVGO", "TSM", "ASML", "LLY", "GLD","AMD","SPCX"
]


SP500_TICKERS =[

     "MU", "LITE", "CIEN", "COHR", "SPY",
    "BE", "MRVL", "CRDO",  "GLW", "AAOI",  "INTC",
    "CRWV", "HOOD", "INOD", "DRAM", "OKLO", "IREN",
    "SMCI", "HIMS", "USAR","NVTS", "FLY", "RGTI",
    "SOFI","IONQ","SNDK","HOOD","NVDA", "AVGO", "TSM", "ASML", "LLY", "GLD",
    "AMD","SPCX","AVGO","DRAM"
    
    "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB","AKAM","ALB",
    "ARE","ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN","AMCR","AEE","AAL","AEP",
    "AXP","AIG","AMT","AWK","AMP","AME","AMGN","APH","ADI","ANSS","AON","APA","AAPL",
    "AMAT","APTV","ACGL","ADM","ANET","AJG","AIZ","T","ATO","ADSK","ADP","AZO","AVB",
    "AVY","AXON","BKR","BALL","BAC","BK","BBWI","BAX","BDX","WRB","BBY","BIO","TECH",
    "BIIB","BLK","BX","BA","BSX","BMY","AVGO","BR","BRO","BF-B","BLDR","BG","CDNS",
    "CZR","CPT","CPB","COF","CAH","KMX","CCL","CARR","CTLT","CAT","CBOE","CBRE","CDW",
    "CE","COR","CNC","CNP","CF","CHRW","CRL","SCHW","CHTR","CVX","CMG","CB","CHD","CI",
    "CINF","CTAS","CSCO","C","CFG","CLX","CME","CMS","KO","CTSH","CL","CMCSA","CMA",
    "CAG","COP","ED","STZ","CEG","COO","CPRT","GLW","CPAY","CTVA","CSGP","COST","CTRA",
    "CCI","CSX","CMI","CVS","DHR","DRI","DVA","DAY","DECK","DE","DAL","DVN","DXCM",
    "FANG","DLR","DFS","DG","DLTR","D","DPZ","DOV","DHI","DTE","DUK","DD","EMN","ETN",
    "EBAY","ECL","EIX","EW","EA","ELV","LLY","EMR","ENPH","ETR","EOG","EPAM","EQT",
    "EFX","EQIX","EQR","ESS","EL","ETSY","EG","ES","EXC","EXPE","EXPD","EXR","XOM",
    "FFIV","FDS","FICO","FAST","FRT","FDX","FIS","FITB","FSLR","FE","FI","FMC","F",
    "FTNT","FTV","FOXA","FOX","BEN","FCX","GRMN","IT","GE","GEHC","GEV","GEN","GNRC",
    "GD","GIS","GM","GPC","GILD","GS","HAL","HIG","HAS","HCA","DOC","HSIC","HSY","HES",
    "HPE","HLT","HOLX","HD","HON","HRL","HST","HWM","HPQ","HUBB","HUM","HBAN","HII",
    "IBM","IEX","IDXX","ITW","INCY","IR","PODD","INTC","ICE","IFF","IP","IPG","INTU",
    "ISRG","IVZ","INVH","IQV","IRM","JBHT","JBL","JKHY","J","JNJ","JCI","JPM","JNPR",
    "K","KVUE","KDP","KEY","KEYS","KMB","KIM","KMI","KLAC","KHC","KR","LHX","LH",
    "LRCX","LW","LVS","LDOS","LEN","LIN","LYV","LKQ","LMT","L","LOW","LULU","LYB",
    "MTB","MRO","MPC","MKTX","MAR","MMC","MLM","MAS","MA","MTCH","MKC","MCD","MCK",
    "MDT","MRK","META","MET","MTD","MGM","MCHP","MU","MSFT","MAA","MRNA","MHK","MOH",
    "TAP","MDLZ","MPWR","MNST","MCO","MS","MOS","MSI","MSCI","NDAQ","NTAP","NFLX",
    "NEM","NWSA","NWS","NEE","NKE","NI","NDSN","NSC","NTRS","NOC","NCLH","NRG","NUE",
    "NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON","OKE","ORCL","OTIS","PCAR",
    "PKG","PANW","PARA","PH","PAYX","PAYC","PYPL","PNR","PEP","PFE","PCG","PM","PSX",
    "PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR","PLD","PRU","PEG","PTC","PSA",
    "PHM","PWR","QCOM","DGX","RL","RJF","RTX","O","REG","REGN","RF","RSG","RMD","RVTY",
    "ROK","ROL","ROP","ROST","RCL","SPGI","CRM","SBAC","SLB","STX","SEE","SRE","NOW",
    "SHW","SPG","SWKS","SJM","SW","SNA","SOLV","SO","LUV","SWK","SBUX","STT","STLD",
    "STE","SYK","SMCI","SYF","SNPS","SYY","TMUS","TROW","TTWO","TPR","TRGP","TGT",
    "TEL","TDY","TFX","TER","TSLA","TXN","TXT","TMO","TJX","TSCO","TT","TDG","TRV",
    "TRMB","TFC","TYL","TSN","USB","UBER","UDR","ULTA","UNP","UAL","UPS","URI","UNH",
    "UHS","VLO","VTR","VRSN","VRSK","VZ","VRTX","VLTO","VFC","VTRS","VICI","V","VST",
    "WAB","WBA","WMT","DIS","WBD","WM","WAT","WEC","WFC","WELL","WST","WDC","WY","WHR",
    "WMB","WTW","GWW","WYNN","XEL","XYL","YUM","ZBRA","ZBH","ZTS",
]



# ─────────────────────────────────────────────────────────────────────────────
# ATR helper
# ─────────────────────────────────────────────────────────────────────────────
def calculate_atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Average True Range — rolling mean over `period` bars."""
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ─────────────────────────────────────────────────────────────────────────────
# QQQ benchmark  (computed once, passed to every get_metrics call)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_qqq_return() -> float:
    try:
        qqq = yf.Ticker("QQQ").history(period="1y", interval="1d", auto_adjust=False)
        if len(qqq) >= 63:
            # FIX: ffill before indexing — yfinance can return NaN on last row
            # (partial bar or pending split adjustment), making iloc[-1] = nan
            close = qqq["Close"].ffill()
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
# categorize  –  full decision tree
# ─────────────────────────────────────────────────────────────────────────────
def categorize(row: dict) -> tuple:
    """
    Returns (category: str, reason: str, rank_score: int).
    rank_score: higher = stronger setup within the same category.
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

    # Pull every metric we'll need across all branches
    dist      = v("Dist_52W_High%",     -999)
    days_52h  = v("Days_Since_52W_High", 999)
    above200  = v("Above_200MA",         False)
    rvol      = v("RVOL",                0.0)
    rs        = v("RS",                 -999)
    adx       = v("ADX_14",              0.0)
    rsi       = v("RSI_14",             50.0)
    pct_ema8  = v("Pct_vs_8EMA",        999)
    pull_vol  = v("Pullback_Vol_Ratio",  999)
    bb_pctb   = v("BB_PctB",             1.0)
    atr_shr   = v("ATR Shrinking",       False)
    eps_g     = v("EPS_Growth%",        -999)
    inst_own  = v("Inst_Own%",           0.0)
    inst_chg  = v("Inst_Own_Chg",        0.0)
    fcf       = v("FCF_Positive",        False)
    si        = v("Short_Interest%",     0.0)
    eb        = v("EarningsBeat",        False)
    pct_low   = v("Pct_From_52W_Low%",  999)
    p50pct    = v("Price_vs_50MA%",     -999)
    above_vwap = v("Above_VWAP",        False)

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 1 — MOMENTUM
    # Criteria: within 10% of 52W high · above 200MA · RVOL ≥ 0.8
    #           · RS ≥ 0 · ADX ≥ 20 · (bonus) 52W high set in last 10 days
    # ═════════════════════════════════════════════════════════════════════════
    m_checks_passed = []
    m_checks_failed = []

    def mc(cond, label):
        (m_checks_passed if cond else m_checks_failed).append(label)
        return cond

    c_dist   = mc(dist >= CFG_MOM_DIST_MAX,             f"dist_52wh≥{CFG_MOM_DIST_MAX}%")
    c_200ma  = mc(above200,                              "above_200MA")
    c_rvol   = mc(rvol  >= CFG_MOM_RVOL,                f"RVOL≥{CFG_MOM_RVOL}")
    c_rs     = mc(rs    >= CFG_MOM_RS_MIN,              f"RS≥{CFG_MOM_RS_MIN}")
    c_adx    = mc(adx   >= CFG_MOM_ADX,                 f"ADX≥{CFG_MOM_ADX}")

    if all([c_dist, c_200ma, c_rvol, c_rs, c_adx]):
        bonuses = []
        if above_vwap:
            bonuses.append("above_VWAP")
        if days_52h <= CFG_MOM_DAYS_SINCE_52WH:
            bonuses.append(f"52WH_set_{days_52h}d_ago")
        else:
            bonuses.append(f"note:52WH_set_{days_52h}d_ago(extended)")

        score  = int(rs * 2 + rvol * 10 + adx)
        passed = ", ".join(m_checks_passed)
        if bonuses:
            passed += " | " + ", ".join(bonuses)
        return "Momentum", f"PASSED: {passed}", score

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 2 — MOMENTUM-PULLBACK
    # Criteria: 10–30% off high · above 200MA · price within ±1% of 8EMA
    #           · RSI 40–65 (not overbought/oversold)
    # Quality:  pullback vol < 0.8 · BB %B < 0.4 (bonuses)
    # ═════════════════════════════════════════════════════════════════════════
    mp_checks = []

    def mpc(cond, label):
        mp_checks.append(("✓" if cond else "✗") + label)
        return cond

    c_dist2  = mpc(CFG_MP_DIST_MIN <= dist < CFG_MP_DIST_MAX,
                   f"dist_52wh[{CFG_MP_DIST_MIN}%,{CFG_MP_DIST_MAX}%)")
    c_200ma2 = mpc(above200,                                  "above_200MA")
    c_ema8   = mpc(CFG_MP_8EMA_PCT_LO <= pct_ema8 <= CFG_MP_8EMA_PCT_HI,
                   f"pct_vs_8EMA[{CFG_MP_8EMA_PCT_LO}%,{CFG_MP_8EMA_PCT_HI}%]")
    c_rsi    = mpc(CFG_MP_RSI_LO <= rsi <= CFG_MP_RSI_HI,
                   f"RSI[{CFG_MP_RSI_LO},{CFG_MP_RSI_HI}]")

    # Quality gates (not hard requirements but surface in reason)
    q_pvol = pull_vol <= CFG_MP_PULLBACK_VOL
    q_bb   = bb_pctb <= CFG_MP_BB_PCTB
    q_rvol = rvol >= CFG_MP_RVOL

    if all([c_dist2, c_200ma2, c_ema8, c_rsi]):
        quality = []
        quality.append(f"pullback_vol={'light✓' if q_pvol else f'heavy({pull_vol:.2f})⚠'}")
        quality.append(f"BB_%B={bb_pctb:.2f}{'✓' if q_bb else '(not coiled)'}")
        quality.append(f"RVOL={rvol:.2f}{'✓' if q_rvol else '⚠'}")

        score = int(
            abs(dist) * -2 + rs * 2
            + (10 if q_pvol else 0)
            + (5  if q_bb   else 0)
            + (5  if q_rvol else 0)
        )
        return (
            "Momentum-Pullback",
            f"PASSED: {', '.join(c for c in mp_checks if c.startswith('✓'))} | {'; '.join(quality)}",
            score,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 3 — VCP SETUP
    # Criteria: 20–45% off high · above 200MA
    # Quality:  ATR contracting · quiet base (RVOL < 0.8) · ADX ≥ 18
    # ═════════════════════════════════════════════════════════════════════════
    vcp_checks = []

    def vc(cond, label):
        vcp_checks.append(("✓" if cond else "✗") + label)
        return cond

    c_vdist  = vc(CFG_VCP_DIST_MIN <= dist < CFG_VCP_DIST_MAX,
                  f"dist_52wh[{CFG_VCP_DIST_MIN}%,{CFG_VCP_DIST_MAX}%)")
    c_v200   = vc(above200,                                    "above_200MA")
    c_vatr   = vc(atr_shr,                                     "ATR_shrinking")
    c_vvol   = vc(rvol <= CFG_VCP_BASE_RVOL,                  f"base_RVOL≤{CFG_VCP_BASE_RVOL}")
    c_vadx   = vc(adx  >= CFG_VCP_ADX_MIN,                   f"ADX≥{CFG_VCP_ADX_MIN}")

    if all([c_vdist, c_v200]):
        quality = [
            f"ATR_contracting={'yes✓' if c_vatr else 'no⚠'}",
            f"base_quiet={'yes✓' if c_vvol else 'elevated⚠'}",
            f"ADX={adx:.1f}{'✓' if c_vadx else '(weak trend)'}",
            f"inst_own_rising={'yes✓' if inst_chg > 0 else 'unclear'}",
        ]
        score = int(
            abs(dist) * -1
            + (15 if c_vatr else 0)
            + (10 if c_vvol else 0)
            + (5  if c_vadx else 0)
            + (5  if inst_chg > 0 else 0)
        )
        passed_vcp = ", ".join(c for c in vcp_checks if c.startswith("✓"))
        return (
            "VCP Setup",
            f"PASSED: {passed_vcp} | {'; '.join(quality)}",
            score,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 4 — TURNAROUND
    # Criteria: 40–65% off high · (RVOL ≥ 1.5 OR earnings beat)
    # Quality:  short interest ≥ 10% · above 50MA (reclaiming)
    # ═════════════════════════════════════════════════════════════════════════
    c_tdist  = CFG_TA_DIST_MIN <= dist < CFG_TA_DIST_MAX
    c_trvol  = rvol >= CFG_TA_RVOL
    c_tearn  = eb is True
    c_tshort = si >= CFG_TA_SHORT_INT
    c_t50ma  = p50pct > 0

    if c_tdist and (c_trvol or c_tearn):
        checks = [f"dist_52wh={dist:.1f}%✓"]
        if c_trvol:
            checks.append(f"reversal_RVOL={rvol:.2f}✓")
        if c_tearn:
            checks.append("earnings_beat✓")
        quality = []
        if c_tshort:
            quality.append(f"short_int={si:.1f}%(squeeze fuel✓)")
        if c_t50ma:
            quality.append(f"above_50MA(+{p50pct:.1f}%✓)")
        else:
            quality.append(f"below_50MA({p50pct:.1f}%⚠)")
        if not quality:
            quality.append("speculative — wider stop needed")

        score = int(
            (100 + dist) * 2
            + rvol * 5
            + (20 if c_tearn  else 0)
            + (10 if c_tshort else 0)
            + (5  if c_t50ma  else 0)
        )
        return (
            "Turnaround",
            f"PASSED: {', '.join(checks)} | {'; '.join(quality)}",
            score,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # CATEGORY 5 — LONGTERM HOLD
    # Criteria: curated ticker · within 10% of 52W low · RVOL ≥ 1.2
    # Quality:  EPS growth ≥ 15% · FCF positive · inst. own ≥ 40% & rising
    # ═════════════════════════════════════════════════════════════════════════
    c_ltick = row["Ticker"] in LONGTERM_TICKERS
    c_llow  = 0 <= pct_low <= CFG_LT_PCT_FROM_LOW_MAX
    c_lrvol = rvol >= CFG_LT_RVOL
    c_leps  = eps_g >= CFG_LT_EPS_GROWTH
    c_lfcf  = fcf is True
    c_linst = inst_own >= CFG_LT_INST_OWN_MIN
    c_linstc = inst_chg >= 0
    c_rs = row.get("RS") is None or row["RS"] > CFG_LT_RS_MIN

    if all([c_ltick, c_llow, c_lrvol, c_rs]):
        fund = []
        fund.append(f"EPS_growth={eps_g:.1f}%{'✓' if c_leps  else '⚠'}")
        fund.append(f"FCF={'positive✓' if c_lfcf else 'negative⚠'}")
        fund.append(f"inst_own={inst_own:.1f}%{'✓' if c_linst  else ''}"
                    f"{'_rising✓' if c_linstc else '_flat/falling⚠'}")
        score = int(
            (eps_g if c_leps else 0)
            + inst_own
            + (20 if c_lfcf   else 0)
            + (10 if c_linstc else 0)
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
        avoid_why.append(f"dist_52wh={dist:.1f}%(too extended down)")
    elif dist < CFG_MOM_DIST_MAX:
        avoid_why.append(f"dist_52wh={dist:.1f}%(no category matched)")
    if not above200:
        avoid_why.append("below_200MA")
    if rvol < CFG_RVOL_MIN_GATE:
        avoid_why.append(f"RVOL={rvol:.2f}(too low)")
    if rs < 0:
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
    run_ts  = datetime.now().strftime("%Y%m%d_%H%M")


    log.info("Fetching QQQ 3-month return …")
    qqq_3m = fetch_qqq_return()
    print("qqq returns is",qqq_3m)
    log.info("QQQ 3m return: %.2f%%", qqq_3m)

    log.info("Scanning %d tickers …", len(tickers))
    rows = []
    results=[]

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
        time.sleep(0.25)   # avoid rate-limit
    save_metrics_to_csv(rows, output_dir=out_dir)

    # ── DataFrame + column ordering ──────────────────────────────────────────
    df = pd.DataFrame(rows)

    priority = [
        "Ticker", "LongName", "Sector",
        "Category", "Cat_Reason", "Rank_Score",
        "Entry_Gate_Pass", "Entry_Gate_Reason",
        # Price / distance
        "Current Price", "Dist_52W_High%", "Pct_From_52W_Low%",
        "52W_High_Date", "Days_Since_52W_High",
        # Volume
        "RVOL", "Vol_vs_20D", "Pullback_Vol_Ratio",
        # Technicals
        "RSI_14", "ADX_14", "Trend_Strength", "BB_PctB",
        "200MA", "50MA", "8EMA", "21EMA",
        "Price_vs_200MA%", "Price_vs_50MA%", "Pct_vs_8EMA",
        "Above_200MA", "Above_VWAP", "VWAP",
        "ATR20", "ATR_Pct", "ATR Shrinking",
        "RS", "CANSLIM_Pass",
        # Fundamentals
        "EPS_Growth%", "Revenue", "Inst_Own%", "Inst_Own_Chg",
        "FCF_Positive", "Short_Interest%", "EarningsBeat", "EarningsDate",
        "MarketCap",
        # Ranges
        "52W High", "52W Low",
        "Prev-Day High", "Prev-Day Low",
        "Pre-Market High", "Pre-Market Low",
    ]
    existing = [c for c in priority if c in df.columns]
    rest     = [c for c in df.columns
                if c not in priority and not c.startswith("_")]
    df = df[existing + rest]

    # Sort: category priority then rank score desc
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
            "Category":    cat,
            "Count":       len(sub),
            "Top_Tickers": ", ".join(top5["Ticker"].tolist()),
            "Top_Scores":  ", ".join(str(s) for s in top5["Rank_Score"].tolist()),
        })
    summary_df   = pd.DataFrame(summary_rows)
    summary_path = out_dir / f"stock_scan_{run_ts}_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    log.info("Summary       → %s", summary_path)

    # ── Console output ────────────────────────────────────────────────────────
    sep = "═" * 74
    print(f"\n{sep}")
    print(f"  SCAN COMPLETE  |  {run_ts}  |  {len(df)} tickers  |  QQQ 3m={qqq_3m:.2f}%")
    print(sep)
    for _, r in summary_df.iterrows():
        print(f"  {r['Category']:<22}  {r['Count']:>4} stocks   "
              f"top: {r['Top_Tickers']}")
    print(sep)
    print(f"\n  Full CSV    : {full_path.resolve()}")
    print(f"  Summary CSV : {summary_path.resolve()}\n")

    # Per-category detail table in console
    for cat in ["Momentum", "Momentum-Pullback", "VCP Setup", "Turnaround", "Longterm Hold"]:
        sub = df[df["Category"] == cat]
        if sub.empty:
            continue
        print(f"\n── {cat} ({len(sub)}) {'─'*(60-len(cat))}")
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



