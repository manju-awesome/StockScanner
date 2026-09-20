"""
portfolio_risk.py
=================
The *facts* half of the Portfolio page's "Analyze Portfolio" report: it takes
data/portfolio.csv + data/options_positions.csv and enriches every holding
with the fields a risk desk reads before it forms an opinion — sector,
industry, market cap, country, exchange, beta, realized vol, liquidity,
ownership, correlations, factor sensitivities, catalysts and theme tags.

The *judgments* built on top of these numbers (concentration limits, overlap
clusters, scores, scenarios, stress tests, rebalancing) live in the sibling
module portfolio_risk_scores.py. The split is deliberate: everything here is
measured or fetched and can be checked against a data source, everything
there is a policy choice about what a measurement means. Mixing the two
makes it impossible to tell which numbers a disagreement is actually about.

Options are folded into exposure by delta, not by premium
---------------------------------------------------------
5 MSFT 540 calls are not "$1,480 of MSFT" (the premium) and not "$270,000 of
MSFT" (the notional) — they are delta × 100 × contracts × spot of directional
MSFT exposure, which is the number that moves when MSFT moves. This app has
no live greeks (no broker session — see options_positions.py), so delta is
computed Black-Scholes from the underlying's own 90-day realized vol and the
current 10-year yield. That is an estimate, it is labelled one everywhere it
surfaces, and `delta_source` on each contract says so.

Data this tool cannot get
-------------------------
Top-5 ETF ownership per holding, FDA decision dates, product-launch dates and
lock-up expiries have no free, reliable source. They are listed in
UNAVAILABLE_FIELDS and rendered as "no source" rather than dropped, so a gap
in the data can never be misread as a zero.
"""

from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import yfinance as yf
except ImportError:                                  # pragma: no cover
    yf = None

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_PATH = DATA_DIR / "output" / "portfolio_risk.json"

# History window. 2 years gives the 1-year correlation/beta window room to
# survive a few delisted-looking gaps, and gives the drawdown stress test a
# second regime to look at instead of extrapolating from one.
HISTORY_PERIOD = "2y"

TRADING_DAYS = 252
VOL_WINDOWS = (30, 90)              # realized-vol lookbacks, calendar-ish days
CORR_WINDOW = 120                   # ~6 months of daily returns
ADV_WINDOW = 30

# Factor proxies. Each holding gets a univariate beta against every one of
# these, which is what makes the scenario table data-driven rather than a set
# of guesses about how a name "should" react to oil or rates.
FACTORS = {
    "SPY":    "SPY",             # broad market
    "QQQ":    "QQQ",             # large-cap growth / tech
    "SEMI":   "SMH",             # semiconductors
    "VIX":    "^VIX",            # volatility
    "TNX":    "^TNX",            # 10-year yield — in PERCENT already
    "OIL":    "CL=F",
    "GOLD":   "GC=F",
    "USD":    "DX-Y.NYB",
    "CRYPTO": "BTC-USD",
}

# ^TNX is a level in percent, not a price: a move from 4.10 to 4.35 is +25bp,
# and running pct_change() on it would call that "+6%" and wreck the rates
# scenario. Factors listed here regress on the first difference (percentage
# points) instead of the percent change.
LEVEL_FACTORS = {"TNX"}

UNAVAILABLE_FIELDS = [
    ("Top 5 ETF ownership",
     "no free source lists a stock's largest ETF holders — needs FactSet / "
     "ETF Global / issuer holdings files"),
    ("FDA decision dates",
     "PDUFA calendars are licensed data; yfinance carries no regulatory calendar"),
    ("Product launch dates",
     "no structured source — company IR calendars are unstructured text"),
    ("Lock-up expiry",
     "only in S-1/424B filings; needs an EDGAR full-text parse, not a quote API"),
]

# ── Theme classification ─────────────────────────────────────────────────────
# Themes are what actually blow up together — a "Technology" sector bucket
# says NVDA and MSFT are the same risk, and the 2022 tape disagreed. Explicit
# ticker sets come first because they're unambiguous; the keyword rules catch
# everything else off the profile's industry/name so a new holding isn't
# silently untagged.
THEME_TICKERS = {
    "AI":             {"NVDA", "AMD", "MSFT", "GOOGL", "META", "AMZN", "ORCL",
                       "NBIS", "NOW", "PLTR", "SMCI", "AVGO", "MRVL", "TSM",
                       "CRWD", "IREN", "IGV", "TSLA"},
    "Semiconductor":  {"NVDA", "AMD", "AVGO", "MRVL", "TSM", "MU", "INTC",
                       "NVTS", "POET", "SMH", "ARM", "QCOM", "TXN"},
    "Software":       {"MSFT", "ORCL", "NOW", "CRM", "SNOW", "PLTR", "CRWD",
                       "PANW", "ZS", "IGV", "ADBE"},
    "Cloud":          {"MSFT", "AMZN", "GOOGL", "ORCL", "NBIS", "SNOW", "NOW",
                       "IREN"},
    "Crypto":         {"IREN", "MARA", "RIOT", "CLSK", "COIN", "MSTR", "HUT",
                       "CIFR", "WULF"},
    "Nuclear":        {"OKLO", "SMR", "CCJ", "LEU", "BWXT", "VST", "NNE", "URA"},
    "Commodity":      {"GLD", "SLV", "GDX", "USO", "UNG", "COPX", "USAR", "CCJ",
                       "LEU"},
    "Cybersecurity":  {"CRWD", "PANW", "ZS", "S", "FTNT", "OKTA"},
    "Quantum":        {"IONQ", "RGTI", "QBTS", "QUBT"},
    "Optical/Photonic": {"AAOI", "LITE", "POET", "COHR", "GLW", "CIEN", "FN"},
    "Defense/Space":  {"FLY", "RKLB", "LMT", "NOC", "RTX", "LHX", "AVAV"},
}

THEME_KEYWORDS = {
    "AI":             ("artificial intelligence", "machine learning", "data center",
                       "accelerated computing"),
    "Semiconductor":  ("semiconductor", "chip", "wafer", "foundry",
                       "electronic components"),
    "Software":       ("software", "saas", "application"),
    "Cloud":          ("cloud", "hosting", "infrastructure services"),
    "Crypto":         ("bitcoin", "crypto", "digital asset", "blockchain", "mining"),
    "Nuclear":        ("nuclear", "uranium", "smr", "fission"),
    "Commodity":      ("gold", "silver", "mining", "oil", "gas", "copper",
                       "rare earth", "commodity", "materials"),
    "Cybersecurity":  ("security", "cyber"),
    "Quantum":        ("quantum",),
    "Optical/Photonic": ("optical", "photonic", "laser", "fiber"),
    "Defense/Space":  ("aerospace", "defense", "space", "satellite", "launch"),
    "Healthcare":     ("biotech", "pharma", "medical", "health", "drug"),
    "Fintech":        ("fintech", "payment", "brokerage", "financial technology"),
    "Consumer":       ("retail", "apparel", "restaurant", "beverage", "consumer"),
    "Robotics":       ("robot", "automation", "autonomous"),
}

# ETF look-through. Holding an ETF and calling its sector "Unknown" hides real
# concentration: 3 IGV calls are a software bet that belongs in the software
# theme alongside MSFT/ORCL/NOW. Weights are issuer factsheet approximations
# as of the AS_OF date below — good enough to place exposure in the right
# bucket, not a substitute for a holdings file.
ETF_LOOKTHROUGH_AS_OF = "2026-07"
ETF_LOOKTHROUGH = {
    "SPY":  {"sectors": {"Technology": 34, "Financial Services": 13,
                         "Consumer Cyclical": 11, "Healthcare": 10,
                         "Communication Services": 10, "Industrials": 8,
                         "Consumer Defensive": 6, "Energy": 3, "Utilities": 2,
                         "Real Estate": 2, "Basic Materials": 1},
             "themes": ["AI", "Software"], "cap": "Mega", "country": "United States"},
    "QQQ":  {"sectors": {"Technology": 52, "Communication Services": 16,
                         "Consumer Cyclical": 14, "Healthcare": 6,
                         "Consumer Defensive": 6, "Industrials": 5,
                         "Utilities": 1},
             "themes": ["AI", "Software", "Semiconductor"], "cap": "Mega",
             "country": "United States"},
    "IGV":  {"sectors": {"Technology": 100},
             "themes": ["Software", "Cloud", "AI", "Cybersecurity"],
             "cap": "Large", "country": "United States"},
    "SMH":  {"sectors": {"Technology": 100},
             "themes": ["Semiconductor", "AI"], "cap": "Large",
             "country": "United States"},
    "GLD":  {"sectors": {"Commodity — Gold": 100}, "themes": ["Commodity"],
             "cap": "Commodity", "country": "Global"},
    "SLV":  {"sectors": {"Commodity — Silver": 100}, "themes": ["Commodity"],
             "cap": "Commodity", "country": "Global"},
    "URA":  {"sectors": {"Energy": 100}, "themes": ["Nuclear", "Commodity"],
             "cap": "Mid", "country": "Global"},
}

CONTRACT_MULTIPLIER = 100
DEFAULT_RISK_FREE = 0.04


# ─────────────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def jsonable(obj):
    """Recursively cast numpy/pandas scalars to plain Python.

    json.dumps() raises on np.float64/np.bool_/Timestamp, and this report is
    cached to disk as JSON — so every number that reaches the cache goes
    through here. (Hit twice before on this codebase; not a hypothetical.)
    """
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, pd.Series):
        return jsonable(obj.to_dict())
    return obj


def _f(val) -> float | None:
    """Float or None — tolerating None, NaN, '', and yfinance's odd strings."""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _round(val, nd=2):
    v = _f(val)
    return None if v is None else round(v, nd)


def cap_bucket(market_cap) -> str | None:
    """Mega ≥ $200B, Large ≥ $10B, Mid ≥ $2B, Small ≥ $300M, else Micro.

    Deliberately finer than reporting.portfolio.cap_bucket(), which stops at
    "Small": the limit sheet has separate small-cap (20%) and micro-cap (10%)
    caps, so collapsing them would make the micro-cap limit unenforceable.
    """
    mc = _f(market_cap)
    if not mc:
        return None
    for floor, label in ((200e9, "Mega"), (10e9, "Large"), (2e9, "Mid"),
                         (300e6, "Small")):
        if mc >= floor:
            return label
    return "Micro"


def bs_delta(spot: float, strike: float, t_years: float, vol: float,
             rate: float, is_call: bool) -> float:
    """Black-Scholes delta. At or past expiry, or with a degenerate input, it
    falls back to intrinsic delta (1/0 for calls, -1/0 for puts) rather than
    raising — an expired-but-still-listed contract should report the exposure
    it actually has, not blow up the whole report."""
    if not (spot and spot > 0 and strike and strike > 0):
        return 0.0
    if t_years <= 0 or not vol or vol <= 0:
        itm = spot > strike if is_call else spot < strike
        return (1.0 if is_call else -1.0) if itm else 0.0
    d1 = ((math.log(spot / strike) + (rate + 0.5 * vol * vol) * t_years)
          / (vol * math.sqrt(t_years)))
    nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    return nd1 if is_call else nd1 - 1.0


# ─────────────────────────────────────────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────────────────────────────────────────

def fetch_history(tickers: list[str], period: str = HISTORY_PERIOD
                  ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Batch-download daily closes and volumes for `tickers`.

    One yf.download() for the whole list rather than a loop of Ticker.history()
    calls: the scanner already learned that Yahoo throttles per-request, and a
    25-name portfolio plus 9 factor proxies is 34 round trips the batch API
    does in one. Returns (closes, volumes, failed) — a name Yahoo won't serve
    is reported in `failed` and simply absent from the frames, so downstream
    math skips it instead of correlating against a column of NaN.
    """
    if yf is None:
        return pd.DataFrame(), pd.DataFrame(), list(tickers)
    uniq = sorted(set(t for t in tickers if t))
    if not uniq:
        return pd.DataFrame(), pd.DataFrame(), []

    raw = yf.download(uniq, period=period, interval="1d", auto_adjust=True,
                      progress=False, group_by="column", threads=True)
    if raw is None or raw.empty:
        return pd.DataFrame(), pd.DataFrame(), uniq

    def _field(name: str) -> pd.DataFrame:
        if isinstance(raw.columns, pd.MultiIndex):
            if name not in raw.columns.get_level_values(0):
                return pd.DataFrame()
            return raw[name].copy()
        # Single ticker: yfinance flattens the column index.
        return raw[[name]].rename(columns={name: uniq[0]}) if name in raw else pd.DataFrame()

    closes, volumes = _field("Close"), _field("Volume")
    # A column that is entirely NaN is a failed fetch wearing a column's
    # clothes — drop it here so beta/corr never see it.
    if not closes.empty:
        good = [c for c in closes.columns if closes[c].notna().sum() >= 20]
        failed = [t for t in uniq if t not in good]
        closes = closes[good]
        if not volumes.empty:
            volumes = volumes[[c for c in volumes.columns if c in good]]
    else:
        failed = uniq
    return closes, volumes, failed


def fetch_profile(ticker: str) -> dict:
    """Company/ETF profile for one ticker via yfinance .info + .calendar.

    Every field is optional. Yahoo drops keys without warning and renames them
    between versions (the institutional-ownership key moved in 1.4), so each
    lookup tries the known aliases and falls back to None — a missing field
    shows as "no data" in the report rather than defaulting to a number that
    would quietly pass a risk limit.
    """
    out = {"Ticker": ticker, "profile_error": None}
    if yf is None:
        out["profile_error"] = "yfinance not installed"
        return out
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:                            # noqa: BLE001
        out["profile_error"] = str(e)[:160]
        return out

    def pick(*keys):
        for k in keys:
            v = info.get(k)
            if v not in (None, "", "None"):
                return v
        return None

    quote_type = (pick("quoteType") or "").upper()
    is_fund = quote_type in ("ETF", "MUTUALFUND", "INDEX")

    out.update({
        "Name":        pick("shortName", "longName"),
        "Quote_Type":  quote_type or None,
        "Is_Fund":     is_fund,
        "Sector":      pick("sector"),
        "Industry":    pick("industry"),
        "Country":     pick("country"),
        "Exchange":    pick("fullExchangeName", "exchange"),
        "Currency":    pick("currency"),
        "Market_Cap":  _f(pick("marketCap", "totalAssets")),
        "Beta_Info":   _f(pick("beta", "beta3Year")),
        "PE":          _f(pick("trailingPE")),
        "Forward_PE":  _f(pick("forwardPE")),
        "PEG":         _f(pick("trailingPegRatio", "pegRatio")),
        "EV_EBITDA":   _f(pick("enterpriseToEbitda")),
        "Price_Sales": _f(pick("priceToSalesTrailing12Months")),
        "ROE":         _f(pick("returnOnEquity")),
        "Debt_Equity": _f(pick("debtToEquity")),
        "Revenue_Growth": _f(pick("revenueGrowth")),
        "Earnings_Growth": _f(pick("earningsGrowth", "earningsQuarterlyGrowth")),
        "Profit_Margin": _f(pick("profitMargins")),
        "Free_Cashflow": _f(pick("freeCashflow")),
        "Inst_Own":    _f(pick("heldPercentInstitutions", "institutionPercentHeld")),
        "Insider_Own": _f(pick("heldPercentInsiders", "insiderPercentHeld")),
        "Short_Pct_Float": _f(pick("shortPercentOfFloat")),
        "Shares_Short":  _f(pick("sharesShort")),
        "Short_Ratio":   _f(pick("shortRatio")),
        "Analyst_Rec":   pick("recommendationKey"),
        "Analyst_Mean":  _f(pick("recommendationMean")),
        "Analyst_Count": _f(pick("numberOfAnalystOpinions")),
        "Target_Mean":   _f(pick("targetMeanPrice")),
        "Dividend_Yield": _f(pick("dividendYield")),
    })

    # FCF yield is the fundamental screen that survives a growth-multiple
    # regime change, and Yahoo gives the pieces but not the ratio.
    fcf, mcap = out.get("Free_Cashflow"), out.get("Market_Cap")
    out["FCF_Yield"] = (fcf / mcap * 100) if (fcf and mcap) else None

    out["Earnings_Date"] = _next_earnings(t)
    out["Insider_Activity"] = _insider_summary(t)
    return out


def _next_earnings(t) -> str | None:
    """Next earnings date as ISO, or None. yfinance has returned this as a
    DataFrame, a dict of lists, and a dict of dates across versions — hence
    the shape-sniffing instead of a direct index."""
    try:
        cal = t.calendar
    except Exception:                                 # noqa: BLE001
        return None
    if cal is None:
        return None
    value = None
    if isinstance(cal, dict):
        value = cal.get("Earnings Date") or cal.get("earningsDate")
    elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
        try:
            value = cal.loc["Earnings Date"].iloc[0]
        except Exception:                             # noqa: BLE001
            value = None
    if isinstance(value, (list, tuple, pd.Series, np.ndarray)):
        value = value[0] if len(value) else None
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10] or None


def _insider_summary(t) -> dict | None:
    """Net insider buying/selling over the last 6 months of Form 4 filings.

    Reported as share counts and a direction, not dollars: yfinance's
    transaction values are inconsistently populated, and direction is the
    signal a risk report actually uses.
    """
    try:
        df = t.insider_transactions
    except Exception:                                 # noqa: BLE001
        return None
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    cols = {c.lower(): c for c in df.columns}
    text_col = cols.get("text") or cols.get("transaction")
    shares_col = cols.get("shares")
    date_col = cols.get("start date") or cols.get("date")
    if not text_col or not shares_col:
        return None
    recent = df
    if date_col:
        try:
            dates = pd.to_datetime(df[date_col], errors="coerce")
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=182)
            recent = df[dates >= cutoff]
        except Exception:                             # noqa: BLE001
            recent = df
    bought = sold = 0.0
    for _, r in recent.iterrows():
        txt = str(r.get(text_col) or "").lower()
        shares = _f(r.get(shares_col)) or 0.0
        if "purchase" in txt or "buy" in txt:
            bought += shares
        elif "sale" in txt or "sell" in txt or "disposit" in txt:
            sold += shares
    if not bought and not sold:
        return None
    net = bought - sold
    return {
        "bought_shares": bought, "sold_shares": sold, "net_shares": net,
        "direction": "buying" if net > 0 else "selling" if net < 0 else "flat",
        "window": "6 months",
    }


# ─────────────────────────────────────────────────────────────────────────────
# RETURN-SERIES MATH
# ─────────────────────────────────────────────────────────────────────────────

def daily_returns(closes: pd.DataFrame) -> pd.DataFrame:
    return closes.sort_index().pct_change(fill_method=None)


def realized_vol(returns: pd.Series, window: int) -> float | None:
    """Annualized realized vol over the last `window` observations, in %."""
    r = returns.dropna().tail(window)
    if len(r) < max(10, window // 3):
        return None
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS) * 100)


def beta_against(returns: pd.Series, factor: pd.Series,
                 min_obs: int = 40) -> float | None:
    """OLS slope of `returns` on `factor` over their overlapping dates.

    Pairwise alignment, not a shared calendar: BTC-USD trades weekends and
    CL=F has its own holidays, so intersecting every factor at once would
    throw away most of the sample. dropna() on the pair keeps each beta on the
    most data it can legitimately use.
    """
    pair = pd.concat([returns, factor], axis=1).dropna()
    if len(pair) < min_obs:
        return None
    y, x = pair.iloc[:, 0].to_numpy(), pair.iloc[:, 1].to_numpy()
    var = float(np.var(x, ddof=1))
    if var <= 0:
        return None
    return float(np.cov(y, x, ddof=1)[0, 1] / var)


def correlation(a: pd.Series, b: pd.Series, window: int = CORR_WINDOW,
                min_obs: int = 40) -> float | None:
    pair = pd.concat([a, b], axis=1).dropna().tail(window)
    if len(pair) < min_obs:
        return None
    c = pair.iloc[:, 0].corr(pair.iloc[:, 1])
    return None if pd.isna(c) else float(c)


def max_drawdown(closes: pd.Series) -> tuple[float | None, int | None]:
    """(worst peak-to-trough %, days spent under water at the worst point).

    Returned together because a −40% drawdown that recovered in 3 weeks and
    one still unrecovered after 2 years are different risks, and the number
    alone can't tell them apart.
    """
    s = closes.dropna()
    if len(s) < 20:
        return None, None
    running_max = s.cummax()
    dd = s / running_max - 1.0
    trough_idx = dd.idxmin()
    worst = float(dd.min() * 100)
    peak_slice = s.loc[:trough_idx]
    peak_idx = peak_slice.idxmax()
    under_water = int((trough_idx - peak_idx).days) if hasattr(trough_idx - peak_idx, "days") else None
    return worst, under_water


def factor_series(closes: pd.DataFrame) -> dict[str, pd.Series]:
    """Per-factor driver series: percent change for prices, first difference
    for level factors (see LEVEL_FACTORS)."""
    out = {}
    for key, symbol in FACTORS.items():
        if symbol not in closes.columns:
            continue
        col = closes[symbol].dropna()
        if col.empty:
            continue
        out[key] = col.diff() if key in LEVEL_FACTORS else col.pct_change(fill_method=None)
    return out


def classify_themes(ticker: str, profile: dict) -> list[str]:
    """Theme tags for one holding: explicit ticker membership first, then
    industry/name keywords, then ETF look-through."""
    themes = {name for name, tickers in THEME_TICKERS.items() if ticker in tickers}
    haystack = " ".join(str(profile.get(k) or "").lower()
                        for k in ("Industry", "Sector", "Name"))
    for name, words in THEME_KEYWORDS.items():
        if any(w in haystack for w in words):
            themes.add(name)
    for t in (ETF_LOOKTHROUGH.get(ticker) or {}).get("themes", []):
        themes.add(t)
    return sorted(themes)


def resolve_sector(ticker: str, profile: dict) -> str:
    """Sector for allocation math, with ETFs resolved through look-through to
    their dominant sector so a fund can't hide in an "Unknown" bucket."""
    sector = profile.get("Sector")
    if sector:
        return str(sector)
    look = ETF_LOOKTHROUGH.get(ticker)
    if look:
        return max(look["sectors"].items(), key=lambda kv: kv[1])[0]
    return "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# ASSEMBLY — positions + market data -> enriched holdings
# ─────────────────────────────────────────────────────────────────────────────

def _last_price(closes: pd.DataFrame, ticker: str) -> float | None:
    if ticker not in closes.columns:
        return None
    s = closes[ticker].dropna()
    return float(s.iloc[-1]) if len(s) else None


def build_option_rows(options: list[dict], closes: pd.DataFrame,
                      vols: dict[str, float | None], risk_free: float,
                      today: date | None = None) -> list[dict]:
    """Per-contract delta exposure.

    Delta_Notional is signed for direction, not for balance-sheet value: a
    long put and a short call both come back negative, because both lose when
    the underlying rallies. That is the sign convention the concentration and
    scenario math needs — a report that added a protective put to the shares
    it hedges as if it were more long exposure would be backwards.
    """
    today = today or date.today()
    rows = []
    for opt in options:
        contracts = _f(opt.get("Contracts")) or 0.0
        if not contracts:
            continue
        underlying = opt["Underlying"]
        spot = _last_price(closes, underlying)
        strike = _f(opt.get("Strike"))
        exp = opt.get("Expiration")
        dte = (exp - today).days if isinstance(exp, date) else None
        is_call = (opt.get("Type") or "call") == "call"
        is_short = (opt.get("Side") or "long") == "short"
        # Realized vol of the underlying stands in for implied vol. It is the
        # wrong number (IV carries an event premium realized vol doesn't) but
        # it is a *measured* wrong number, and delta is far less sensitive to
        # the vol input than price is.
        vol_pct = vols.get(underlying)
        vol = (vol_pct / 100.0) if vol_pct else None
        t_years = max((dte or 0), 0) / 365.0

        delta = bs_delta(spot or 0.0, strike or 0.0, t_years, vol or 0.0,
                         risk_free, is_call)
        signed_delta = -delta if is_short else delta
        shares_equiv = signed_delta * contracts * CONTRACT_MULTIPLIER
        delta_notional = (shares_equiv * spot) if spot else None

        premium = _f(opt.get("Current_Premium"))
        avg_premium = _f(opt.get("Avg_Premium"))
        market_value = (premium * contracts * CONTRACT_MULTIPLIER
                        * (-1 if is_short else 1)) if premium is not None else None
        # For a long option the most you can lose is what you paid; for a
        # short one the risk is open-ended and premium says nothing about it.
        premium_at_risk = ((avg_premium or 0) * contracts * CONTRACT_MULTIPLIER
                           if not is_short else None)

        rows.append({
            "Underlying":     underlying,
            "Label":          f"{underlying} {strike:g}{'C' if is_call else 'P'} "
                              f"{exp.strftime('%m/%d/%y') if isinstance(exp, date) else '?'}"
                              if strike is not None else underlying,
            "Type":           "call" if is_call else "put",
            "Side":           "short" if is_short else "long",
            "Strike":         strike,
            "Expiration":     exp.isoformat() if isinstance(exp, date) else None,
            "DTE":            dte,
            "Contracts":      contracts,
            "Spot":           _round(spot),
            "Moneyness_Pct":  _round((spot / strike - 1) * 100, 1) if (spot and strike) else None,
            "IV_Proxy_Pct":   _round(vol_pct, 1),
            "Delta":          _round(signed_delta, 4),
            "Delta_Source":   "Black-Scholes estimate (90d realized vol, no live greeks)",
            "Shares_Equiv":   _round(shares_equiv, 1),
            "Delta_Notional": _round(delta_notional, 2),
            "Avg_Premium":    avg_premium,
            "Current_Premium": premium,
            "Market_Value":   _round(market_value, 2),
            "Premium_At_Risk": _round(premium_at_risk, 2),
            "Quote_At":       opt.get("Quote_At") or "",
            "Strategy":       opt.get("Strategy") or "",
        })
    rows.sort(key=lambda r: (r["DTE"] is None, r["DTE"] if r["DTE"] is not None else 0))
    return rows


def build_holdings(positions: list[dict], option_rows: list[dict],
                   closes: pd.DataFrame, volumes: pd.DataFrame,
                   profiles: dict[str, dict], portfolio_value: float) -> list[dict]:
    """One enriched row per underlying — equity shares and option delta from
    the same ticker collapse into a single exposure line.

    They have to: NVDA shares plus NVDA calls is one NVDA bet, and a limit
    sheet that scores them separately will pass a position that is over the
    single-name cap. `Equity_Value` and `Option_Delta_Value` stay on the row
    so the split is always visible.
    """
    rets = daily_returns(closes)
    factors = factor_series(closes)

    option_by_ticker: dict[str, list[dict]] = {}
    for row in option_rows:
        option_by_ticker.setdefault(row["Underlying"], []).append(row)

    equity_by_ticker = {p["Ticker"]: p for p in positions if (p.get("Shares") or 0)}
    tickers = sorted(set(equity_by_ticker) | set(option_by_ticker))

    holdings = []
    for ticker in tickers:
        pos = equity_by_ticker.get(ticker)
        profile = profiles.get(ticker) or {"Ticker": ticker}
        price = _last_price(closes, ticker)
        shares = _f(pos.get("Shares")) if pos else 0.0
        avg_cost = _f(pos.get("Avg_Cost")) if pos else None

        equity_value = (price * shares) if (price and shares) else 0.0
        opts = option_by_ticker.get(ticker, [])
        option_delta_value = sum(o["Delta_Notional"] or 0.0 for o in opts)
        exposure = equity_value + option_delta_value

        series = rets[ticker] if ticker in rets.columns else pd.Series(dtype=float)
        vol30 = realized_vol(series, 30)
        vol90 = realized_vol(series, 90)

        adv_shares = adv_dollars = None
        if ticker in volumes.columns:
            v = volumes[ticker].dropna().tail(ADV_WINDOW)
            if len(v) >= 5:
                adv_shares = float(v.mean())
                adv_dollars = adv_shares * price if price else None

        betas = {name: beta_against(series, fs) for name, fs in factors.items()}
        mdd, mdd_days = max_drawdown(closes[ticker]) if ticker in closes.columns else (None, None)

        gain_pct = ((price / avg_cost - 1) * 100
                    if (price and avg_cost) else None)
        earnings = profile.get("Earnings_Date")
        days_to_earnings = None
        if earnings:
            try:
                days_to_earnings = (date.fromisoformat(earnings[:10]) - date.today()).days
            except ValueError:
                days_to_earnings = None

        holdings.append({
            "Ticker":     ticker,
            "Name":       profile.get("Name"),
            "Sector":     resolve_sector(ticker, profile),
            "Industry":   profile.get("Industry") or (
                "ETF / fund" if profile.get("Is_Fund") or ticker in ETF_LOOKTHROUGH else None),
            "Country":    profile.get("Country") or (ETF_LOOKTHROUGH.get(ticker) or {}).get("country"),
            "Exchange":   profile.get("Exchange"),
            "Market_Cap": profile.get("Market_Cap"),
            "Cap_Bucket": cap_bucket(profile.get("Market_Cap")) or (
                (ETF_LOOKTHROUGH.get(ticker) or {}).get("cap")),
            "Is_Fund":    bool(profile.get("Is_Fund") or ticker in ETF_LOOKTHROUGH),
            "Themes":     classify_themes(ticker, profile),

            "Shares":     shares or 0.0,
            "Avg_Cost":   avg_cost,
            "Price":      _round(price),
            "Equity_Value": _round(equity_value),
            "Option_Contracts": sum(o["Contracts"] for o in opts) if opts else 0.0,
            "Option_Delta_Value": _round(option_delta_value),
            "Exposure":   _round(exposure),
            "Weight_Pct": _round(exposure / portfolio_value * 100, 2) if portfolio_value else None,
            "Gain_Pct":   _round(gain_pct, 2),
            "Strategy":   (pos or {}).get("Strategy") or "options-only",

            "Beta_SPY":   _round(betas.get("SPY"), 2),
            "Beta_QQQ":   _round(betas.get("QQQ"), 2),
            "Beta_Info":  _round(profile.get("Beta_Info"), 2),
            "Factor_Betas": {k: _round(v, 3) for k, v in betas.items()},
            "Vol_30D":    _round(vol30, 1),
            "Vol_90D":    _round(vol90, 1),
            "Corr_SPY":   _round(correlation(series, rets["SPY"]) if "SPY" in rets.columns else None, 2),
            "Corr_QQQ":   _round(correlation(series, rets["QQQ"]) if "QQQ" in rets.columns else None, 2),
            "Max_Drawdown_2Y": _round(mdd, 1),
            "Drawdown_Days":   mdd_days,

            "ADV_Shares":  _round(adv_shares, 0),
            "ADV_Dollars": _round(adv_dollars, 0),

            "Inst_Own_Pct":    _round((profile.get("Inst_Own") or 0) * 100, 1) if profile.get("Inst_Own") else None,
            "Insider_Own_Pct": _round((profile.get("Insider_Own") or 0) * 100, 1) if profile.get("Insider_Own") else None,
            "Short_Pct_Float": _round((profile.get("Short_Pct_Float") or 0) * 100, 1) if profile.get("Short_Pct_Float") else None,
            "Short_Ratio":     _round(profile.get("Short_Ratio"), 1),
            "Insider_Activity": profile.get("Insider_Activity"),
            "Analyst_Rec":     profile.get("Analyst_Rec"),
            "Analyst_Mean":    _round(profile.get("Analyst_Mean"), 2),
            "Analyst_Count":   _round(profile.get("Analyst_Count"), 0),
            "Target_Mean":     _round(profile.get("Target_Mean"), 2),

            "PE":            _round(profile.get("PE"), 1),
            "Forward_PE":    _round(profile.get("Forward_PE"), 1),
            "PEG":           _round(profile.get("PEG"), 2),
            "EV_EBITDA":     _round(profile.get("EV_EBITDA"), 1),
            "FCF_Yield":     _round(profile.get("FCF_Yield"), 2),
            "ROE":           _round((profile.get("ROE") or 0) * 100, 1) if profile.get("ROE") else None,
            "Debt_Equity":   _round(profile.get("Debt_Equity"), 1),
            "Revenue_Growth": _round((profile.get("Revenue_Growth") or 0) * 100, 1) if profile.get("Revenue_Growth") else None,
            "Earnings_Growth": _round((profile.get("Earnings_Growth") or 0) * 100, 1) if profile.get("Earnings_Growth") else None,
            "Profit_Margin": _round((profile.get("Profit_Margin") or 0) * 100, 1) if profile.get("Profit_Margin") else None,

            "Earnings_Date":    earnings,
            "Days_To_Earnings": days_to_earnings,
            "Profile_Error":    profile.get("profile_error"),
        })

    holdings.sort(key=lambda h: -(abs(h["Exposure"] or 0)))
    return holdings


def technical_state(closes: pd.DataFrame, ticker: str) -> dict:
    """20/50/200-day MAs, distance from each, RSI(14) and a trend verdict.

    Position sizing reads this: a name over its allocation cap that is also
    below its 200MA is a different trade from one over its cap and leading.
    """
    out = {"MA20": None, "MA50": None, "MA200": None, "Above_200MA": None,
           "Dist_200MA_Pct": None, "RSI": None, "Trend": None, "RS_3M_Pct": None}
    if ticker not in closes.columns:
        return out
    s = closes[ticker].dropna()
    if len(s) < 25:
        return out
    price = float(s.iloc[-1])

    def _ma(n):
        return float(s.tail(n).mean()) if len(s) >= n else None

    ma20, ma50, ma200 = _ma(20), _ma(50), _ma(200)
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float(100 - 100 / (1 + rs.iloc[-1])) if not pd.isna(rs.iloc[-1]) else None

    if len(s) >= 64:
        out["RS_3M_Pct"] = round(float(s.iloc[-1] / s.iloc[-64] - 1) * 100, 1)

    trend = None
    if ma20 and ma50 and ma200:
        if price > ma20 > ma50 > ma200:
            trend = "Strong uptrend"
        elif price > ma200:
            trend = "Uptrend"
        elif price < ma20 < ma50 < ma200:
            trend = "Strong downtrend"
        else:
            trend = "Downtrend"
    out.update({
        "MA20": _round(ma20), "MA50": _round(ma50), "MA200": _round(ma200),
        "Above_200MA": (price > ma200) if ma200 else None,
        "Dist_200MA_Pct": _round((price / ma200 - 1) * 100, 1) if ma200 else None,
        "RSI": _round(rsi, 1), "Trend": trend,
    })
    return out


def correlation_matrix(closes: pd.DataFrame, tickers: list[str]
                       ) -> tuple[list[str], list[list[float | None]]]:
    """Pairwise correlation of daily returns over CORR_WINDOW.

    Built pair-by-pair rather than with DataFrame.corr() so each cell uses its
    own overlapping history — one recently-listed holding would otherwise
    truncate the window for the entire matrix.
    """
    rets = daily_returns(closes)
    usable = [t for t in tickers if t in rets.columns]
    matrix = []
    for a in usable:
        row = []
        for b in usable:
            row.append(1.0 if a == b else correlation(rets[a], rets[b]))
        matrix.append(row)
    return usable, matrix


def portfolio_return_series(closes: pd.DataFrame, weights: dict[str, float]
                            ) -> pd.Series:
    """Daily return series of the CURRENT portfolio held constant back through
    history. Not a backtest of what the user actually earned — it's the
    standard risk-desk construction that answers "what would today's book have
    done in that tape", which is the only version that can stress today's
    positions."""
    rets = daily_returns(closes)
    cols = [t for t in weights if t in rets.columns and weights[t]]
    if not cols:
        return pd.Series(dtype=float)
    w = np.array([weights[t] for t in cols], dtype=float)
    total = w.sum()
    if total <= 0:
        return pd.Series(dtype=float)
    w = w / total
    sub = rets[cols].dropna(how="all").fillna(0.0)
    return pd.Series(sub.to_numpy() @ w, index=sub.index)


def risk_free_rate(closes: pd.DataFrame) -> float:
    """10-year yield as a decimal, from ^TNX.

    ^TNX quotes the yield directly in percent (4.12 means 4.12%). Dividing by
    10 — the shape the ticker's name suggests — would price every option off a
    0.4% rate.
    """
    sym = FACTORS["TNX"]
    if sym in closes.columns:
        s = closes[sym].dropna()
        if len(s):
            return float(s.iloc[-1]) / 100.0
    return DEFAULT_RISK_FREE
