"""
market_movers.py
================
Market-wide context scans:

  - Top-10 pre-market / live / after-hours movers across the S&P 500 +
    extended watchlist, each enriched with a news catalyst (earnings
    beat/miss, upgrade/downgrade, M&A, product launch, macro, general)
  - VIX level/change with interpretation bands (fetch_vix)
  - This week's high/medium-impact US economic calendar (fetch_economic_events)
  - market_pulse(): dashboard-header snapshot — top-10 S&P mega caps by live
    market cap with basket allocation %, SPY/QQQ strength from their own trend
    + allocation-weighted mega-cap breadth + VIX penalty, plus per-ticker
    catalysts and upcoming econ events

Usage
-----
Standalone:
    python market_movers.py                   # auto-detects pre-market vs live
    python market_movers.py --mode premarket  # force pre-market scan
    python market_movers.py --mode live       # force live scan
    python market_movers.py --mode afterhours # force after-hours scan
    python market_movers.py --top 20          # show top 20 instead of 10
    python market_movers.py --email           # send results via Resend
    python market_movers.py --tickers NVDA AMD MU --top 5
    python -m stockanalysis.scanners.market_movers

Scheduler integration:
    from stockanalysis.scanners.market_movers import run_movers_job
    schedule.every().day.at("08:00").do(run_movers_job, mode="premarket")
    schedule.every().day.at("09:35").do(run_movers_job, mode="live")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import textwrap
import time
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)

# ── Constants ─────────────────────────────────────────────────────────────────
ET = ZoneInfo("America/New_York")

RESEND_API_KEY    = os.environ.get("RESEND_API_KEY", "")
EMAIL_TO          = os.environ.get("ALERT_EMAIL_TO",   "you@example.com")
RESEND_FROM_EMAIL = os.environ.get("ALERT_EMAIL_FROM", "alerts@yourdomain.com")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

try:
    import resend as resend_sdk
    HAVE_RESEND_SDK = True
except ImportError:
    HAVE_RESEND_SDK = False
    log.debug("resend SDK not installed — will use urllib HTTP fallback")

# Session boundaries (hour, minute) in ET
PREMARKET_START = (4,  0)
MARKET_OPEN     = (9, 30)
MARKET_CLOSE    = (16, 0)
AFTER_HOURS_END = (20, 0)

# ── Market context (VIX + economic calendar) ─────────────────────────────────
VIX_TICKER = "^VIX"

# ForexFactory public "this week" feed — no auth required
ECON_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# The ForexFactory feed rate-limits hard (HTTP 429) and the calendar only
# changes a few times a day, so responses are cached — in memory and on disk
# (survives restarts) — and refetched at most once per TTL. On a failed
# refresh the last good copy is served instead of an error.
ECON_CACHE_PATH = Path(__file__).resolve().parents[3] / "data" / "econ_calendar_cache.json"
ECON_CACHE_TTL_SECONDS = 3600
ECON_COUNTRIES = {"USD"}             # currency codes to keep
ECON_IMPACTS   = {"High", "Medium"}  # impact levels to keep

# VIX interpretation bands: (upper_bound, label) checked in order
VIX_BANDS = [
    (15.0, "LOW fear"),
    (20.0, "NORMAL"),
    (30.0, "ELEVATED"),
    (float("inf"), "HIGH fear"),
]

# ── Watchlist ─────────────────────────────────────────────────────────────────
WATCHLIST: list[str] = [
    # Mega-cap tech
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AMD","AVGO","ORCL","PLTR",
    # Semis / AI infra
    "QCOM","INTC","ARM","MRVL","SMCI","ANET","CRDO","ASML","TSM","USAR","UUUU","UMAY",
    #Memory
    "WDC","STX","CIEN","VRT","GLW","LITE","COHR","MU","AMAT","AAOI","NBIS",
    "CRWV","IREN",
    # Finance
    "JPM","GS","MS","BAC","V","MA","PYPL","HOOD","COIN",
    # Health / Biotech
    "LLY","HIMS","NVO",
    # Energy
    "XOM","CVX","OXY","SLB",
    # Retail / Consumer
    "WMT","COST","TGT","NKE","MCD",
    # Defense / Aerospace
    "LMT","RTX","NOC","BA",
    # Cybersecurity
    "PANW","CRWD","OKTA",
    # Quantum / Speculative
    "IONQ","QBTS","RGTI",
    # Macro ETFs (useful for context)
    "SPY","QQQ","IWM","TLT","GLD","UNG",
]

WATCHLIST1: list[str] = [

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


# ── Catalyst keyword patterns ─────────────────────────────────────────────────
CATALYST_PATTERNS: list[tuple[str, str]] = [
    # Earnings
    (r"\bearnings? beat\b|\bEPS beat\b|\brevenue beat\b|\bbeat estimates?\b|\bbeat expectations?\b",
     "Earnings Beat"),
    (r"\bearnings? miss\b|\bEPS miss\b|\bmissed estimates?\b|\bbelow expectations?\b|\bdisappoint\w*\b",
     "Earnings Miss"),
    (r"\bguidance rais\w+\b|\braised? (outlook|guidance|forecast)\b|\bupped? guidance\b",
     "Guidance Raised"),
    (r"\bguidance (cut|lower\w*|reduc\w*)\b|\blowered? (outlook|guidance|forecast)\b",
     "Guidance Cut"),
    # Credit rating actions — must be checked before the generic Analyst
    # Downgrade pattern below, since "Moody's downgrades..." would otherwise
    # match \bdowngrad\w+\b first (first-match-wins in _classify_headline).
    # Unordered lookaheads (agency + "rating" + a downgrade/cut verb all
    # present, any order) so both "Moody's downgrades ... rating" and
    # "S&P cuts credit rating" match — and "S&P 500 cuts losses" doesn't
    # (no "rating" anywhere), avoiding the S&P-the-agency vs S&P-the-index
    # ambiguity.
    (r"(?=.*\b(?:Moody'?s|S&P|Fitch)\b)(?=.*\brating\b)(?=.*\b(?:downgrad\w*|cuts?|lower\w*)\b)",
     "Credit Downgrade"),
    # Analyst actions
    (r"\bupgrad\w+\b|\bprice target (rais\w+|increas\w+|hik\w+)\b|\bbuy rating\b|\boutperform\b",
     "Analyst Upgrade"),
    (r"\bdowngrad\w+\b|\bprice target (cut|lower\w*|reduc\w*)\b|\bsell rating\b|\bunderperform\b",
     "Analyst Downgrade"),
    (r"\binitiati\w+ (coverage|at buy|outperform|overweight)\b|\bnew coverage\b",
     "New Coverage"),
    # M&A / Corporate
    (r"\bacquisition\b|\bacquir\w+\b|\bmerger\b|\btakeover\b|\bbuyout\b|\bprivate equity\b",
     "M&A / Acquisition"),
    (r"\bspin[\s-]?off\b|\bdivest\w*\b|\bsell\w* (unit|division|business|subsidiary)\b",
     "Divestiture"),
    (r"\bshare buyback\b|\brepurchase program\b|\bdividend (increas\w+|rais\w+|hik\w+)\b",
     "Capital Return"),
    # Product / Business — FDA Approval checked first: "FDA approves new
    # drug" would otherwise match Product Launch's "new drug" first
    (r"\bFDA (approv\w+|clear\w+|grant\w+)\b|\b510[Kk]\b|\bBLA\b|\bNDA\b|\bsNDA\b",
     "FDA Approval"),
    (r"\bproduct launch\b|\bnew (product|model|chip|drug|platform|service)\b|\blaunch\w* (new|of)\b",
     "Product Launch"),
    (r"\bcontract win\b|\bnew (deal|contract|order)\b|\bpartnership\b|\bjoint venture\b|\bMOU\b",
     "New Deal / Contract"),
    # Macro / Economic
    (r"\bFed\b|\bFOMC\b|\binterest rate\b|\brate (cut|hike|decision|pause)\b|\bpowell\b",
     "Fed / Rate Decision"),
    (r"\bCPI\b|\bPCE\b|\binflation\b|\bjobs report\b|\bnonfarm payroll\b|\bGDP\b|\bPMI\b",
     "Economic Data"),
    (r"\btariff\b|\btrade war\b|\bsanction\b|\bexport (ban|restrict\w+|control)\b",
     "Trade / Tariff"),
    # Sentiment / Risk
    (r"\bshort squeeze\b|\bhigh short interest\b|\bmost shorted\b",
     "Short Squeeze"),
    (r"\binsider buy\b|\bexecutive (purchas\w+|buy\w+)\b|\b13[Dd] filing\b",
     "Insider Buying"),
    (r"\blayoff\b|\brestructur\w+\b|\bjob cut\b|\bworkforce reduc\w+\b|\bRIF\b",
     "Restructuring"),
    (r"\bfraud\b|\bSEC invest\w+\b|\bclass action\b|\bsubpoena\b|\blawsuit\b|\blitigation\b",
     "Legal / Regulatory"),
    (r"\bCEO\b.{0,40}\b(resign\w*|depart\w*|steps? down|ousted|fired|to leave)\b|"
     r"\b(resign\w*|steps? down|ousted)\b.{0,40}\bCEO\b|"
     r"\bchief executive\b.{0,40}\b(resign\w*|depart\w*|to step down)\b|\bC-suite exit\b",
     "Executive Departure"),
]


# ── Session detection ─────────────────────────────────────────────────────────

def _now_et() -> datetime:
    return datetime.now(ET)


def _detect_mode() -> str:
    """Return 'premarket', 'live', 'afterhours', or 'closed'."""
    now = _now_et()
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return "closed"
    mins = now.hour * 60 + now.minute
    pm_start  = PREMARKET_START[0] * 60 + PREMARKET_START[1]   # 240
    mkt_open  = MARKET_OPEN[0]     * 60 + MARKET_OPEN[1]       # 570
    mkt_close = MARKET_CLOSE[0]    * 60 + MARKET_CLOSE[1]      # 960
    ah_end    = AFTER_HOURS_END[0] * 60 + AFTER_HOURS_END[1]   # 1200
    if pm_start  <= mins < mkt_open:   return "premarket"
    if mkt_open  <= mins < mkt_close:  return "live"
    if mkt_close <= mins < ah_end:     return "afterhours"
    return "closed"


# ── Market context: VIX + economic calendar ──────────────────────────────────

def _vix_label(level: float) -> str:
    for upper, label in VIX_BANDS:
        if level < upper:
            return label
    return VIX_BANDS[-1][1]


def fetch_vix() -> dict:
    """
    Fetch latest VIX close and change vs prior close.
    Returns {"level", "change", "change_pct", "label", "error"} — never raises.
    """
    try:
        df = yf.Ticker(VIX_TICKER).history(period="5d")
        closes = df["Close"].dropna() if df is not None and not df.empty else None
        if closes is None or closes.empty:
            return {"level": None, "change": None, "change_pct": None,
                    "label": None, "error": "no VIX data returned"}

        level = float(closes.iloc[-1])
        change = change_pct = None
        if len(closes) >= 2:
            prev = float(closes.iloc[-2])
            change = level - prev
            change_pct = (change / prev * 100) if prev else None

        return {
            "level":      round(level, 2),
            "change":     round(change, 2) if change is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "label":      _vix_label(level),
            "error":      None,
        }
    except Exception as e:
        log.warning("VIX fetch failed: %s", e)
        return {"level": None, "change": None, "change_pct": None,
                "label": None, "error": str(e)}


_econ_cache: dict | None = None      # {"fetched_at": epoch float, "events": [json-safe]}


def _econ_cache_load() -> dict | None:
    """In-memory cache first, then the on-disk copy; None if neither exists."""
    global _econ_cache
    if _econ_cache is None:
        try:
            _econ_cache = json.loads(ECON_CACHE_PATH.read_text())
        except Exception:
            return None
    return _econ_cache


def _econ_cache_store(events: list[dict]) -> None:
    global _econ_cache
    _econ_cache = {
        "fetched_at": time.time(),
        "events": [{**e, "when": e["when"].isoformat()} for e in events],
    }
    try:
        ECON_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ECON_CACHE_PATH.write_text(json.dumps(_econ_cache))
    except Exception as e:
        log.warning("Could not persist economic calendar cache: %s", e)


def _econ_cache_events(cache: dict) -> list[dict]:
    return [{**e, "when": datetime.fromisoformat(e["when"])} for e in cache["events"]]


def fetch_economic_events() -> dict:
    """
    This week's economic calendar (ForexFactory feed), filtered to
    ECON_COUNTRIES / ECON_IMPACTS, sorted by time (ET). Served from cache
    while fresh (ECON_CACHE_TTL_SECONDS); on a failed refresh the last good
    copy is returned regardless of age.
    Returns {"events": [...], "error": None|str} — never raises.
    """
    cache = _econ_cache_load()
    if cache and time.time() - cache.get("fetched_at", 0) < ECON_CACHE_TTL_SECONDS:
        return {"events": _econ_cache_events(cache), "error": None}

    try:
        req = urllib.request.Request(
            ECON_CALENDAR_URL,
            headers={"User-Agent": "Mozilla/5.0 (stock-scanner)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        events = []
        for item in raw:
            if item.get("country") not in ECON_COUNTRIES:
                continue
            if item.get("impact") not in ECON_IMPACTS:
                continue
            try:
                when = datetime.fromisoformat(item["date"]).astimezone(ET)
            except (KeyError, ValueError):
                continue
            events.append({
                "title":    item.get("title", ""),
                "impact":   item.get("impact", ""),
                "when":     when,
                "forecast": item.get("forecast", ""),
                "previous": item.get("previous", ""),
            })

        events.sort(key=lambda e: e["when"])
        _econ_cache_store(events)
        return {"events": events, "error": None}
    except Exception as e:
        log.warning("Economic calendar fetch failed: %s", e)
        if cache:
            age_min = (time.time() - cache.get("fetched_at", 0)) / 60
            log.warning("Serving cached economic calendar (%.0f min old) instead", age_min)
            return {"events": _econ_cache_events(cache), "error": None}
        return {"events": [], "error": str(e)}


# ── Fed rate outlook (CME FedWatch-style) ─────────────────────────────────────
# The FedWatch tool itself sits behind Akamai (HTTP 403 for scripts), so we
# compute the same signal from its underlying market: 30-Day Fed Funds futures
# (ZQ, CBOT). Implied rate = 100 − price; the spread of each month vs the
# front month is the cumulative rate change the market prices by that month
# (±25 bp ≈ one Fed move).
_ZQ_MONTH_CODES = "FGHJKMNQUVXZ"          # futures month codes Jan..Dec


def fetch_fed_rate_outlook(months_ahead: int = 6) -> dict:
    """
    Rate-change outlook for upcoming months from fed funds futures.
    Returns {"current_implied", "months": [{label, symbol, implied_rate,
    change_bps, moves}], "asof", "error"} — never raises.
    """
    try:
        now, months, anchor = _now_et(), [], None
        for i in range(months_ahead + 1):
            yr_off, m0 = divmod(now.month - 1 + i, 12)
            year, month = now.year + yr_off, m0 + 1
            sym = f"ZQ{_ZQ_MONTH_CODES[month - 1]}{str(year)[-2:]}.CBT"
            try:
                h = yf.Ticker(sym).history(period="5d")
                closes = h["Close"].dropna() if h is not None and not h.empty else None
                if closes is None or closes.empty:
                    continue
                implied = round(100 - float(closes.iloc[-1]), 3)
            except Exception as e:
                log.debug("fed funds contract %s failed: %s", sym, e)
                continue
            if anchor is None:                 # front month = "current" anchor
                anchor = implied
                continue
            chg_bps = round((implied - anchor) * 100)
            months.append({
                "label":        datetime(year, month, 1).strftime("%b %Y"),
                "symbol":       sym,
                "implied_rate": implied,
                "change_bps":   chg_bps,
                "moves":        round(chg_bps / 25, 1),   # +1 ≈ one 25bp hike
            })
        return {"current_implied": anchor, "months": months,
                "asof": now.strftime("%Y-%m-%d"),
                "error": None if anchor is not None else "no fed funds data"}
    except Exception as e:
        log.warning("Fed rate outlook failed: %s", e)
        return {"current_implied": None, "months": [], "asof": None, "error": str(e)}


def summarize_fed_outlook(fed: dict) -> str:
    """One-line human summary, e.g. 'now ~3.63% · Sep +10bp · Dec +28bp (~1 hike priced)'."""
    cur = fed.get("current_implied")
    if cur is None:
        return "Fed outlook unavailable"
    parts = [f"now ~{cur:.2f}%"]
    for m in fed.get("months") or []:
        note = ""
        if abs(m["moves"]) >= 0.9:
            n = abs(m["moves"])
            kind = "hike" if m["change_bps"] > 0 else "cut"
            note = f" (~{n:g} {kind}{'s' if n >= 1.5 else ''} priced)"
        parts.append(f"{m['label'][:3]} {m['change_bps']:+d}bp{note}")
    return " · ".join(parts)


def fetch_market_context() -> dict:
    """VIX + economic events + Fed rate outlook; each section fails independently."""
    return {"vix": fetch_vix(), "econ": fetch_economic_events(),
            "fed": fetch_fed_rate_outlook()}


# ── Market pulse (dashboard header) ───────────────────────────────────────────
# Candidate mega caps, ranked live by market cap and the top 10 kept.
# Allocation % is each stock's share of the top-10 basket — the S&P 500 is
# cap-weighted, so this tracks index concentration without holdings data.
SP500_MEGACAP_CANDIDATES = [
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "TSLA",
    "BRK-B", "JPM", "LLY", "V", "WMT", "XOM", "UNH",
]
# Subset that also dominates QQQ — drives the QQQ breadth reading
QQQ_MEGACAPS = {"NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "TSLA"}

# Index futures — trade nearly 24h, so they give live direction pre-market
# and overnight when SPY/QQQ quotes are stale
FUTURES = [("ES=F", "S&P fut"), ("NQ=F", "Nasdaq fut")]

# Cross-asset context for the Pre-Market Brief — gold/oil/bitcoin trade
# nearly 24h same as futures, so they're meaningful before the open too
MACRO_EXTRAS = [("GC=F", "Gold"), ("CL=F", "Oil (WTI)"), ("BTC-USD", "Bitcoin")]

# Sector ETFs for the rotation chips row
SECTOR_ETFS = [
    ("SMH", "Semis"),   ("XLK", "Tech"),      ("XLC", "Comms"),
    ("XLY", "Discret"), ("XLF", "Financials"), ("XLV", "Health"),
    ("XLI", "Industr"), ("XLE", "Energy"),     ("XLP", "Staples"),
    ("XLU", "Utilities"), ("IWM", "Small caps"),("IGV", "Software"),
    ("DRAM", "Memory")
]


def _quote_chg(ticker: str) -> dict | None:
    """Last price + % change vs prior close. fast_info first, history fallback."""
    try:
        t = yf.Ticker(ticker)
        try:
            last = float(t.fast_info["last_price"])
            prev = float(t.fast_info["regular_market_previous_close"])
        except Exception:
            closes = t.history(period="5d")["Close"].dropna()
            if len(closes) < 2:
                return None
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
        if not prev:
            return None
        return {"ticker": ticker, "price": round(last, 2),
                "chg_pct": round((last / prev - 1) * 100, 2)}
    except Exception as e:
        log.debug("quote failed for %s: %s", ticker, e)
        return None


def _trend_snapshot(ticker: str) -> dict | None:
    """Price/day-change/20MA/50MA/5d-return snapshot from daily history."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="3mo", interval="1d", auto_adjust=False)
        closes = hist["Close"].ffill().dropna() if hist is not None and not hist.empty else None
        if closes is None or len(closes) < 21:
            return None
        price = float(closes.iloc[-1])
        prev  = float(closes.iloc[-2])
        snap = {
            "ticker":     ticker,
            "price":      round(price, 2),
            "day_chg_pct": round((price / prev - 1) * 100, 2) if prev else None,
            "ret_5d_pct": (round((price / float(closes.iloc[-6]) - 1) * 100, 2)
                           if len(closes) >= 6 else None),
            "above_20ma": price > float(closes.rolling(20).mean().iloc[-1]),
            "above_50ma": (price > float(closes.rolling(50).mean().iloc[-1])
                           if len(closes) >= 50 else None),
        }
        try:
            # NB: FastInfo.get("market_cap") returns None even when the key
            # exists — must use item access
            snap["market_cap"] = t.fast_info["market_cap"]
        except Exception:
            snap["market_cap"] = None
        return snap
    except Exception as e:
        log.debug("pulse snapshot failed for %s: %s", ticker, e)
        return None


def _index_strength(snap: dict, breadth_pct: float | None, vix_level: float | None) -> tuple[int, str]:
    """Score an index (SPY/QQQ) from its own trend + weighted mega-cap breadth + VIX."""
    score = 0
    score += 2 if snap.get("above_50ma") else -2
    score += 1 if snap.get("above_20ma") else -1
    score += 1 if (snap.get("ret_5d_pct") or 0) > 0 else -1
    if breadth_pct is not None:
        if breadth_pct >= 70:   score += 2
        elif breadth_pct >= 55: score += 1
        elif breadth_pct < 30:  score -= 2
        elif breadth_pct < 45:  score -= 1
    if vix_level is not None:
        if vix_level >= 30:   score -= 3
        elif vix_level >= 25: score -= 2
        elif vix_level >= 20: score -= 1
        elif vix_level < 15:  score += 1
    label = "STRONG" if score >= 3 else ("WEAK" if score <= -2 else "NEUTRAL")
    return score, label


def market_pulse(top_n: int = 10, with_catalysts: bool = True) -> dict:
    """
    Market health snapshot for the dashboard header:
      - VIX level/change/label
      - top-N S&P mega caps by live market cap, with allocation % of the basket,
        day change, and (optionally) a news catalyst per ticker
      - SPY and QQQ strength (own trend + allocation-weighted mega-cap breadth
        above the 50MA + VIX penalty)
      - next high-impact economic events
    Never raises — failed parts come back None/empty.
    """
    vix  = fetch_vix()
    spy  = _trend_snapshot("SPY")
    qqq  = _trend_snapshot("QQQ")

    snaps = [s for s in (_trend_snapshot(t) for t in SP500_MEGACAP_CANDIDATES)
             if s and s.get("market_cap")]
    snaps.sort(key=lambda s: s["market_cap"], reverse=True)
    top = snaps[:top_n]

    total_cap = sum(s["market_cap"] for s in top) or None
    for s in top:
        s["weight_pct"] = round(s["market_cap"] / total_cap * 100, 1) if total_cap else None

    def _breadth(members: list[dict]) -> float | None:
        scored = [(s["weight_pct"] or 0, s["above_50ma"]) for s in members
                  if s.get("above_50ma") is not None and s.get("weight_pct")]
        wsum = sum(w for w, _ in scored)
        if not wsum:
            return None
        return round(sum(w for w, above in scored if above) / wsum * 100, 1)

    spy_breadth = _breadth(top)
    qqq_members = [s for s in top if s["ticker"] in QQQ_MEGACAPS]
    qqq_breadth = _breadth(qqq_members) if qqq_members else None

    vix_level = vix.get("level")
    if spy:
        spy["breadth_pct"] = spy_breadth
        spy["score"], spy["strength"] = _index_strength(spy, spy_breadth, vix_level)
    if qqq:
        qqq["breadth_pct"] = qqq_breadth
        qqq["score"], qqq["strength"] = _index_strength(qqq, qqq_breadth, vix_level)

    if with_catalysts:
        for s in top:
            try:
                label, text = _fetch_catalyst(s["ticker"])
            except Exception:
                label, text = "No News Found", "—"
            s["catalyst"] = label
            s["headline"] = text

    econ = fetch_economic_events()
    upcoming = [e for e in econ.get("events", []) if e["when"] >= _now_et()][:3]

    futures = []
    for sym, label in FUTURES:
        q = _quote_chg(sym)
        if q:
            q["label"] = label
            futures.append(q)

    sectors = []
    for sym, label in SECTOR_ETFS:
        q = _quote_chg(sym)
        if q:
            q["label"] = label
            sectors.append(q)
    sectors.sort(key=lambda s: s["chg_pct"], reverse=True)

    macro = []
    for sym, label in MACRO_EXTRAS:
        q = _quote_chg(sym)
        if q:
            q["label"] = label
            macro.append(q)

    return {"vix": vix, "spy": spy, "qqq": qqq, "top10": top,
            "econ_events": upcoming, "fed": fetch_fed_rate_outlook(),
            "futures": futures, "sectors": sectors, "macro": macro}


# ── Price fetch ───────────────────────────────────────────────────────────────

def _fetch_quotes(tickers: list[str], mode: str) -> list[dict]:
    """
    Fetch per-ticker using yf.Ticker.history() to avoid MultiIndex
    column issues that arise from yf.download() on multiple tickers.
    Returns a list of raw quote dicts sorted by abs % change descending.
    """
    log.info("Fetching quotes for %d tickers (mode=%s)…", len(tickers), mode)

    now_et = _now_et()
    today  = now_et.date()

    # Session start cutoff — bars before this are excluded
    cutoff_map = {
        "premarket":  now_et.replace(hour=4,  minute=0,  second=0, microsecond=0),
        "live":       now_et.replace(hour=9,  minute=30, second=0, microsecond=0),
        "afterhours": now_et.replace(hour=16, minute=0,  second=0, microsecond=0),
        "closed":     now_et.replace(hour=0,  minute=0,  second=0, microsecond=0),
    }
    cutoff = cutoff_map.get(mode, cutoff_map["closed"])

    quotes = []
    total  = len(tickers)

    for idx, ticker in enumerate(tickers, 1):
        try:
            df = yf.Ticker(ticker).history(
                period="2d",
                interval="1m",
                prepost=True,      # include pre/after-hours bars
                auto_adjust=True,
            )

            if df is None or df.empty:
                log.debug("[%d/%d] %s — no data", idx, total, ticker)
                continue

            # Normalize timezone to ET
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(ET)
            else:
                df.index = df.index.tz_convert(ET)

            # Split intoZ previous day and today
            df_prev  = df[df.index.date < today]
            df_today = df[df.index.date == today]

            # Previous close = last clean close bar from prior day
            prev_close = None
            if not df_prev.empty:
                prev_series = df_prev["Close"].dropna()
                if not prev_series.empty:
                    prev_close = float(prev_series.iloc[-1])

            # Session slice — only bars after session start
            if mode == "closed":
                df_session = df_today   # closed: use full day for context
            else:
                df_session = df_today[df_today.index >= cutoff]

            # Fallback: if session slice is empty use all of today
            if df_session.empty:
                df_session = df_today

            if df_session.empty:
                log.debug("[%d/%d] %s — no session bars", idx, total, ticker)
                continue

            # Drop rows where Close is NaN
            df_session = df_session.dropna(subset=["Close"])
            if df_session.empty:
                continue

            last_price   = float(df_session["Close"].iloc[-1])
            session_open = float(df_session["Open"].dropna().iloc[0]) if not df_session["Open"].dropna().empty else last_price
            session_high = float(df_session["High"].max())
            session_low  = float(df_session["Low"].min())
            session_vol  = int(df_session["Volume"].sum())

            # Fallback prev_close to session open if unavailable
            if prev_close is None:
                prev_close = session_open

            chg_pct = (last_price - prev_close) / prev_close * 100 if prev_close else 0.0

            quotes.append({
                "ticker":       ticker,
                "prev_close":   round(prev_close,   2),
                "session_open": round(session_open, 2),
                "session_high": round(session_high, 2),
                "session_low":  round(session_low,  2),
                "last_price":   round(last_price,   2),
                "chg_pct":      round(chg_pct,      2),
                "session_vol":  session_vol,
                "abs_chg_pct":  abs(chg_pct),
            })

            log.debug(
                "[%d/%d] %s  last=%.2f  chg=%.2f%%  vol=%d",
                idx, total, ticker, last_price, chg_pct, session_vol,
            )

        except Exception as e:
            log.debug("[%d/%d] %s — skipped: %s", idx, total, ticker, e)
            continue

    return quotes


# ── Catalyst detection ────────────────────────────────────────────────────────

def _classify_headline(text: str) -> str:
    """Return the first matching catalyst label, or 'General News'."""
    for pattern, label in CATALYST_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "General News"


def _fetch_catalyst(ticker: str) -> tuple[str, str]:
    """
    Fetch recent news for ticker via yfinance.
    Prefers the longer summary/description over the short title.
    Returns (catalyst_label, full_text) — no truncation.
    """
    try:
        news = yf.Ticker(ticker).news or []

        candidates: list[str] = []
        for item in news[:10]:
            # yfinance ≥0.2.x nests content under 'content' dict
            content = item.get("content", {}) or {}
            title   = (item.get("title")       or content.get("title")       or "").strip()
            summary = (item.get("summary")     or content.get("summary")
                       or item.get("description") or content.get("description") or "").strip()
            # Use summary if it adds meaningful detail beyond the title
            text = summary if len(summary) > len(title) + 20 else title
            if text:
                candidates.append(text)

        if not candidates:
            return "No News Found", "—"

        # Walk candidates: return first one with a specific catalyst
        best_label    = "General News"
        best_text     = candidates[0]

        for text in candidates:
            label = _classify_headline(text)
            if label != "General News":
                best_label = label
                best_text  = text
                break

        return best_label, best_text   # full text, no truncation

    except Exception as e:
        log.debug("News fetch failed for %s: %s", ticker, e)
        return "Fetch Error", "—"


# ── Main scan ─────────────────────────────────────────────────────────────────

def scan_movers(
    tickers: list[str] | None = None,
    mode:    str = "auto",
    top_n:   int = 10,
) -> list[dict]:
    """
    Scan tickers for top N movers by absolute % change.
    Enriches each result with catalyst label, headline/summary,
    session high/low, volume, and direction.

    Parameters
    ----------
    tickers : list of ticker strings (defaults to WATCHLIST)
    mode    : 'premarket' | 'live' | 'afterhours' | 'closed' | 'auto'
    top_n   : how many movers to return

    Returns
    -------
    List of enriched dicts, sorted by abs % change descending.
    """
    if tickers is None:
        tickers = WATCHLIST

    if mode == "auto":
        mode = _detect_mode()

    log.info("Session mode: %s | Scanning %d tickers | Top %d", mode.upper(), len(tickers), top_n)

    # ── Fetch raw quotes ──────────────────────────────────────────
    quotes = _fetch_quotes(tickers, mode)

    if not quotes:
        log.error("No quotes returned — check yfinance connectivity or try --mode closed")
        return []

    # Sort by absolute % change, take top_n
    quotes.sort(key=lambda x: x["abs_chg_pct"], reverse=True)
    top = quotes[:top_n]

    log.info("Top %d movers identified — fetching catalysts…", len(top))

    # ── Enrich with catalyst + news ───────────────────────────────
    results = []
    for i, q in enumerate(top, 1):
        ticker = q["ticker"]
        log.info("  [%d/%d] %s — fetching catalyst…", i, len(top), ticker)

        catalyst, headline = _fetch_catalyst(ticker)
        time.sleep(0.3)   # gentle on yfinance news rate limits

        results.append({
            "rank":          i,
            "ticker":        ticker,
            "direction":     "▲" if q["chg_pct"] >= 0 else "▼",
            "last_price":    q["last_price"],
            "prev_close":    q["prev_close"],
            "chg_pct":       q["chg_pct"],
            "session_open":  q["session_open"],
            "session_high":  q["session_high"],
            "session_low":   q["session_low"],
            "session_vol":   q["session_vol"],
            "catalyst":      catalyst,
            "headline":      headline,
            "mode":          mode,
            "scan_time":     _now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
        })

    return results


# ── Console report ────────────────────────────────────────────────────────────

def _print_context_block(context: dict) -> None:
    vix  = context.get("vix")  or {}
    econ = context.get("econ") or {}
    today = _now_et().date()

    print("\n  MARKET CONTEXT")
    print(f"  {'─' * 45}")

    if vix.get("level") is not None:
        change, change_pct = vix.get("change"), vix.get("change_pct")
        if change is not None:
            arrow = "▲" if change >= 0 else "▼"
            # Rising VIX = rising fear → red; falling VIX → green
            color = "\033[91m" if change >= 0 else "\033[92m"
            chg_str = f"  {color}{arrow} {abs(change):.2f} ({change_pct:+.1f}%)\033[0m"
        else:
            chg_str = ""
        print(f"  VIX {vix['level']:.2f}{chg_str}  — {vix['label']}")
    else:
        print(f"  VIX unavailable: {vix.get('error', 'unknown error')}")

    fed = context.get("fed") or {}
    if fed.get("current_implied") is not None:
        print(f"\n  FED RATE OUTLOOK (fed funds futures, FedWatch-style)")
        print(f"  {summarize_fed_outlook(fed)}")
        for m in fed.get("months") or []:
            direction = ("HIKE" if m["change_bps"] >= 13 else
                         "CUT" if m["change_bps"] <= -13 else "hold")
            print(f"    {m['label']:<9} implied {m['implied_rate']:.2f}%  "
                  f"{m['change_bps']:+4d}bp vs front  [{direction}]")
    elif fed.get("error"):
        print(f"\n  Fed outlook unavailable: {fed['error']}")

    impacts = "/".join(sorted(ECON_IMPACTS))
    countries = "/".join(sorted(ECON_COUNTRIES))
    print(f"\n  This week's {countries} events ({impacts} impact):")
    if econ.get("error"):
        print(f"    Economic calendar unavailable: {econ['error']}")
    elif not econ.get("events"):
        print("    No matching events this week.")
    else:
        for ev in econ["events"]:
            marker = "→ " if ev["when"].date() == today else "  "
            fcst = f"  fcst {ev['forecast']}" if ev["forecast"] else ""
            prev = f"  prev {ev['previous']}" if ev["previous"] else ""
            print(
                f"  {marker}{ev['when'].strftime('%a %m/%d %H:%M')}  "
                f"{ev['title']:<32} [{ev['impact']}]{fcst}{prev}"
            )
    print()


def print_movers_report(rows: list[dict], context: dict | None = None) -> None:
    if context:
        _print_context_block(context)

    if not rows:
        print("No movers found.")
        return

    mode    = rows[0].get("mode", "").upper()
    scan_ts = rows[0].get("scan_time", "")
    SEP     = "═" * 95
    THIN    = "─" * 95
    # Left-padding to align wrapped headline lines under the text column
    WRAP_INDENT = " " * 6

    print(f"\n{SEP}")
    print(f"  TOP {len(rows)} MARKET MOVERS — {mode}   [{scan_ts}]".center(95))
    print(SEP)
    print(
        f"  {'#':>2}  {'Ticker':<7} {'Last':>9}  {'Chg%':>8}  "
        f"{'Sess Low':>9}  {'Sess High':>9}  {'Volume':>8}  {'Catalyst'}"
    )
    print(f"  {THIN}")

    for r in rows:
        chg_str = f"{r['direction']} {abs(r['chg_pct']):.2f}%"
        vol_str = (
            f"{r['session_vol'] / 1_000_000:.1f}M" if r["session_vol"] >= 1_000_000
            else f"{r['session_vol'] / 1_000:.0f}K"
        )
        color = "\033[92m" if r["chg_pct"] >= 0 else "\033[91m"
        reset = "\033[0m"

        # ── Metric line ───────────────────────────────────────────
        print(
            f"  {r['rank']:>2}  {r['ticker']:<7} "
            f"${r['last_price']:>8.2f}  "
            f"{color}{chg_str:>8}{reset}  "
            f"${r['session_low']:>8.2f}  "
            f"${r['session_high']:>8.2f}  "
            f"{vol_str:>8}  "
            f"{r['catalyst']}"
        )

        # ── Full headline / summary wrapped at 88 chars ───────────
        if r["headline"] and r["headline"] != "—":
            wrapped = textwrap.wrap(r["headline"], width=88)
            for i, line in enumerate(wrapped):
                prefix = f"{WRAP_INDENT}  📰 " if i == 0 else f"{WRAP_INDENT}     "
                print(f"{prefix}{line}")

        # Prev close reference
        print(
            f"{WRAP_INDENT}     Prev close ${r['prev_close']:.2f}  "
            f"│  Session open ${r['session_open']:.2f}"
        )
        print()  # blank line between tickers

    # ── Catalyst breakdown ────────────────────────────────────────
    print(SEP)
    print("\n  CATALYST BREAKDOWN")
    print(f"  {'─' * 45}")
    counts = Counter(r["catalyst"] for r in rows)
    for cat, cnt in counts.most_common():
        bar = "█" * cnt
        print(f"  ● {cat:<28}  {bar}  {cnt}")

    top_g = rows[0]
    top_l = min(rows, key=lambda r: r["chg_pct"])
    print(
        f"\n  Top gainer : {top_g['ticker']:<6} "
        f"{top_g['direction']}{abs(top_g['chg_pct']):.2f}%  — {top_g['catalyst']}"
    )
    print(
        f"  Top loser  : {top_l['ticker']:<6} "
        f"{top_l['direction']}{abs(top_l['chg_pct']):.2f}%  — {top_l['catalyst']}"
    )
    print(f"\n{SEP}\n")


# ── Email ─────────────────────────────────────────────────────────────────────

def _build_context_html(context: dict) -> str:
    vix  = context.get("vix")  or {}
    econ = context.get("econ") or {}

    if vix.get("level") is not None:
        change, change_pct = vix.get("change"), vix.get("change_pct")
        if change is not None:
            chg_color = "#A32D2D" if change >= 0 else "#0F6E56"
            arrow = "▲" if change >= 0 else "▼"
            chg_html = (f'<span style="color:{chg_color};font-weight:600">'
                        f'{arrow} {abs(change):.2f} ({change_pct:+.1f}%)</span>')
        else:
            chg_html = ""
        vix_html = f"""
      <div style="font-size:13px;margin-bottom:12px">
        <strong style="font-size:16px">VIX {vix['level']:.2f}</strong>
        {chg_html}
        <span style="background:#E6F1FB;color:#0C447C;padding:2px 7px;border-radius:3px;
                     font-size:11px;font-weight:500;margin-left:6px">{vix['label']}</span>
      </div>"""
    else:
        vix_html = (f'<div style="font-size:12px;color:#898781;margin-bottom:12px">'
                    f'VIX unavailable: {vix.get("error", "unknown error")}</div>')

    if econ.get("error"):
        econ_html = (f'<div style="font-size:12px;color:#898781">'
                     f'Economic calendar unavailable: {econ["error"]}</div>')
    elif not econ.get("events"):
        econ_html = '<div style="font-size:12px;color:#898781">No matching events this week.</div>'
    else:
        ev_rows = ""
        for ev in econ["events"]:
            impact_bg = "#FBE6E6" if ev["impact"] == "High" else "#FBF3E6"
            impact_fg = "#A32D2D" if ev["impact"] == "High" else "#8A5A0C"
            title_escaped = ev["title"].replace("<", "&lt;").replace(">", "&gt;")
            ev_rows += f"""
        <tr style="border-bottom:1px solid #f0efea">
          <td style="padding:6px;white-space:nowrap;color:#555">{ev['when'].strftime('%a %m/%d %H:%M')}</td>
          <td style="padding:6px">{title_escaped}</td>
          <td style="padding:6px">
            <span style="background:{impact_bg};color:{impact_fg};padding:2px 7px;
                         border-radius:3px;font-size:11px;font-weight:500">{ev['impact']}</span>
          </td>
          <td style="padding:6px;color:#555">{ev['forecast'] or '—'}</td>
          <td style="padding:6px;color:#555">{ev['previous'] or '—'}</td>
        </tr>"""
        econ_html = f"""
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="background:#f5f4f0;text-align:left">
            <th style="padding:6px">Time (ET)</th>
            <th style="padding:6px">Event</th>
            <th style="padding:6px">Impact</th>
            <th style="padding:6px">Forecast</th>
            <th style="padding:6px">Previous</th>
          </tr>
        </thead>
        <tbody>{ev_rows}</tbody>
      </table>"""

    impacts = "/".join(sorted(ECON_IMPACTS))
    countries = "/".join(sorted(ECON_COUNTRIES))
    return f"""
    <div style="margin:0 0 24px;padding:16px;background:#f9f9f7;border-radius:8px">
      <div style="font-size:12px;font-weight:600;color:#0b0b0b;margin-bottom:10px">
        MARKET CONTEXT
      </div>
      {vix_html}
      <div style="font-size:12px;font-weight:600;color:#0b0b0b;margin:0 0 6px">
        This week's {countries} events ({impacts} impact)
      </div>
      {econ_html}
    </div>"""


def _build_movers_html(rows: list[dict], context: dict | None = None) -> str:
    mode    = rows[0].get("mode", "").upper() if rows else ""
    scan_ts = rows[0].get("scan_time", "") if rows else ""
    context_html = _build_context_html(context) if context else ""

    rows_html = ""
    for r in rows:
        chg_color = "#0F6E56" if r["chg_pct"] >= 0 else "#A32D2D"
        chg_str   = f"{r['direction']} {abs(r['chg_pct']):.2f}%"
        vol_str   = (
            f"{r['session_vol'] / 1_000_000:.1f}M" if r["session_vol"] >= 1_000_000
            else f"{r['session_vol'] / 1_000:.0f}K"
        )
        headline_escaped = r["headline"].replace("<", "&lt;").replace(">", "&gt;")

        rows_html += f"""
        <tr style="border-bottom:1px solid #f0efea;vertical-align:top">
          <td style="padding:10px 6px;font-weight:700;font-size:13px">{r['rank']}</td>
          <td style="padding:10px 6px;font-weight:700;font-size:15px;color:#0b0b0b">{r['ticker']}</td>
          <td style="padding:10px 6px">${r['last_price']:.2f}</td>
          <td style="padding:10px 6px;color:{chg_color};font-weight:600">{chg_str}</td>
          <td style="padding:10px 6px;color:#555">${r['session_low']:.2f}</td>
          <td style="padding:10px 6px;color:#555">${r['session_high']:.2f}</td>
          <td style="padding:10px 6px;color:#555">{vol_str}</td>
          <td style="padding:10px 6px">
            <span style="background:#E6F1FB;color:#0C447C;padding:2px 7px;
                         border-radius:3px;font-size:11px;font-weight:500;white-space:nowrap">
              {r['catalyst']}
            </span>
          </td>
          <td style="padding:10px 6px;font-size:12px;color:#444;max-width:320px;line-height:1.5">
            {headline_escaped}
            <div style="font-size:10px;color:#898781;margin-top:3px">
              Prev close ${r['prev_close']:.2f} · Open ${r['session_open']:.2f}
            </div>
          </td>
        </tr>"""

    catalyst_counts = Counter(r["catalyst"] for r in rows)
    catalyst_html = "".join(
        f'<div style="margin:3px 0;font-size:12px">● {cat} <strong>({cnt})</strong></div>'
        for cat, cnt in catalyst_counts.most_common()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Movers — {scan_ts}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             background:#f5f4f0;padding:24px;margin:0">
  <div style="max-width:1100px;margin:auto;background:white;border-radius:12px;
              padding:28px;border:0.5px solid #e1e0d9">

    <h2 style="margin:0 0 4px;font-size:20px;color:#0b0b0b">
      📊 Top {len(rows)} Market Movers — {mode}
    </h2>
    <p style="color:#898781;font-size:13px;margin:0 0 24px">{scan_ts}</p>
    {context_html}
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead>
        <tr style="background:#f5f4f0;text-align:left">
          <th style="padding:8px 6px">#</th>
          <th style="padding:8px 6px">Ticker</th>
          <th style="padding:8px 6px">Price</th>
          <th style="padding:8px 6px">Chg%</th>
          <th style="padding:8px 6px">Low</th>
          <th style="padding:8px 6px">High</th>
          <th style="padding:8px 6px">Volume</th>
          <th style="padding:8px 6px">Catalyst</th>
          <th style="padding:8px 6px">Headline / Summary</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>

    <div style="margin-top:24px;padding:16px;background:#f9f9f7;border-radius:8px">
      <div style="font-size:12px;font-weight:600;color:#0b0b0b;margin-bottom:8px">
        CATALYST BREAKDOWN
      </div>
      {catalyst_html}
    </div>

    <p style="margin-top:20px;font-size:11px;color:#898781;line-height:1.6">
      Not financial advice. Price data via yfinance · News via Yahoo Finance.<br>
      Verify all catalysts independently before trading.
    </p>
  </div>
</body>
</html>"""


def send_resend_email(subject: str, text_body: str, html_body: str,
                      to: str | None = None) -> bool:
    """Low-level Resend send (SDK first, urllib HTTP fallback) — the one
    Resend integration in the app. email_movers() below and the alert
    engine (core/alerts.py's premarket brief + watchlist alert emails) both
    call this rather than each keeping their own copy of the SDK/HTTP
    fallback dance. Returns True on a send that didn't raise/error."""
    if not RESEND_API_KEY:
        log.error("Missing RESEND_API_KEY — skipping email %r", subject)
        return False

    recipient = to or EMAIL_TO
    payload = {"from": RESEND_FROM_EMAIL, "to": [recipient],
              "subject": subject, "text": text_body, "html": html_body}

    if HAVE_RESEND_SDK:
        try:
            resend_sdk.api_key = RESEND_API_KEY
            result = resend_sdk.Emails.send(payload)
            _id = result.get("id") if isinstance(result, dict) else result
            log.info("Email sent via SDK (id=%s) → %s: %s", _id, recipient, subject)
            return True
        except Exception as e:
            log.warning("Resend SDK failed (%s) — trying HTTP fallback", e)

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "stock-scanner/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log.info("Email sent via HTTP (status %s) → %s: %s", resp.status, recipient, subject)
            return True
    except urllib.error.HTTPError as e:
        log.error("Resend error %s: %s", e.code, e.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log.error("Email send failed: %s", e)
    return False


TELEGRAM_MSG_LIMIT = 4096  # hard cap enforced by the Bot API


def _chunk_for_telegram(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Splits on line boundaries so a long digest doesn't get truncated
    mid-alert by Telegram's per-message character cap."""
    if len(text) <= limit:
        return [text]
    chunks, current, length = [], [], 0
    for line in text.split("\n"):
        if current and length + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_telegram_message(text: str) -> bool:
    """Low-level Telegram send via the Bot API — the one Telegram
    integration in the app. core/alerts.py's CRITICAL/HIGH digest calls
    this alongside send_resend_email. Returns True only if every chunk
    sends without error (a message over TELEGRAM_MSG_LIMIT is split first)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID — skipping Telegram push")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ok = True
    for chunk in _chunk_for_telegram(text):
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                  "parse_mode": "HTML", "disable_web_page_preview": True}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info("Telegram message sent (status %s)", resp.status)
        except urllib.error.HTTPError as e:
            log.error("Telegram error %s: %s", e.code, e.read().decode("utf-8", errors="replace"))
            ok = False
        except Exception as e:
            log.error("Telegram send failed: %s", e)
            ok = False
    return ok


def email_movers(rows: list[dict], context: dict | None = None) -> None:
    """Send top movers report via Resend."""
    if not rows:
        log.warning("No mover rows to email")
        return

    mode     = rows[0].get("mode", "").upper()
    date_str = _now_et().strftime("%Y-%m-%d %H:%M ET")
    subject  = f"[StockScan] Top {len(rows)} {mode} Movers — {date_str}"

    # Plain-text body
    lines = [f"Top {len(rows)} {mode} Movers — {date_str}\n"]
    vix = (context or {}).get("vix") or {}
    if vix.get("level") is not None:
        chg = f" ({vix['change_pct']:+.1f}%)" if vix.get("change_pct") is not None else ""
        lines.insert(1, f"VIX {vix['level']:.2f}{chg} — {vix['label']}\n")
    for r in rows:
        lines.append(
            f"  {r['rank']:>2}. {r['ticker']:<6} "
            f"{r['direction']}{abs(r['chg_pct']):.2f}%  "
            f"Low ${r['session_low']:.2f}  High ${r['session_high']:.2f}  "
            f"[{r['catalyst']}]"
        )
        if r["headline"] and r["headline"] != "—":
            # Wrap at 80 chars for plain text
            for line in textwrap.wrap(r["headline"], width=80):
                lines.append(f"        {line}")
        lines.append("")
    lines.append("Not financial advice.")
    text_body = "\n".join(lines)

    send_resend_email(subject, text_body, _build_movers_html(rows, context))


# ── Scheduler integration hook ────────────────────────────────────────────────

def run_movers_job(mode: str = "auto", top_n: int = 10, send_email: bool = True) -> list[dict]:
    """
    Drop-in job for Scheduler.py _start_scheduler().

    Example:
        from market_movers import run_movers_job
        schedule.every().day.at("08:00").do(run_movers_job, mode="premarket")
        schedule.every().day.at("09:35").do(run_movers_job, mode="live")
        schedule.every().day.at("10:30").do(run_movers_job, mode="live")
        schedule.every().day.at("13:00").do(run_movers_job, mode="live")
    """
    context = fetch_market_context()
    rows = scan_movers(mode=mode, top_n=top_n)
    print_movers_report(rows, context)
    if send_email:
        email_movers(rows, context)
    return rows


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """Standalone entry point — run this module directly."""
    parser = argparse.ArgumentParser(
        description="Market mover scanner with catalyst detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python market_movers.py                              # auto-detect session
  python market_movers.py --mode premarket             # force pre-market
  python market_movers.py --mode live --top 20         # top 20 live movers
  python market_movers.py --mode afterhours --email    # after-hours + email
  python market_movers.py --tickers NVDA AMD MU LLY   # custom ticker list
        """,
    )
    parser.add_argument(
        "--mode", "--mode",
        choices=["premarket", "live", "afterhours", "closed", "auto"],
        default="auto",
        help="Session mode (default: auto-detect from current ET time)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top movers to show (default: 10)",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Send results via Resend API after printing",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        help="Override default watchlist  e.g. --tickers NVDA AMD MU",
    )
    args = parser.parse_args()

    context = fetch_market_context()
    rows = scan_movers(
        tickers=args.tickers or None,
        mode=args.mode,
        top_n=args.top,
    )
    print_movers_report(rows, context)

    if args.email:
        email_movers(rows, context)


if __name__ == "__main__":
    main()
