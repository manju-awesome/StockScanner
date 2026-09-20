"""
sector_leaders.py
=================
Pure scoring for the daily bullish/bearish sector-leader scan:

    MARKET  →  SECTOR  →  INDUSTRY  →  STOCK  →  SETUP

The point of this engine is that it never ranks by daily percent change. A
name that gapped +8% and closed on its low is a worse candidate than one that
added 2% on expanding volume out of a three-week base, and the only way to say
that with a straight face is to score the layers separately and then ask how
many of them agree. That agreement is the CONFLUENCE score; the per-layer
scores are what it is built from.

Three scores, deliberately not the same thing
---------------------------------------------
  Sector Trend Score   0-100  how strong the *group* is (momentum, RS,
                              breadth of its members, volume, trend quality)
  Stock Trend Score    0-100  how strong the *name* is, on the budget the
                              spec fixes: trend 25 / RS 20 / momentum 15 /
                              volume 15 / structure 15 / sector confirm 10
  Confluence           0-100  how well market, sector and stock line up —
                              a strong stock inside a weak sector scores its
                              own 80 and a confluence of 40, and that gap is
                              the divergence flag, not a bug

  Leadership           0-100  sector RS + stock RS + trend + volume + breadth
                              + market alignment, banded institutional-leader
                              → laggard. Answers "who is actually driving
                              this move", which is not the same question as
                              "what is the best entry today".
  Trend Clarity        0-100  how *clean* the trend is, independent of how
                              fast: R² of the log-price fit, time spent on
                              the right side of the 20 EMA, ATR-normalised
                              chop, and how long the MA stack has held. This
                              is the score that finds the big liquid name
                              whose trend you can actually hold.

Direction is a parameter, not an assumption
-------------------------------------------
Every stock-level function takes direction="long"|"short" and mirrors its
tests rather than negating a long score. A name below all three moving
averages making lower highs scores 85 short and 15 long; the two rankings
are produced from the same pass, which is what makes a bearish list possible
on a green day.

Measured vs interpreted
-----------------------
Everything here is arithmetic on OHLCV. Where a level is structural (a real
swing high, the 20 EMA, the 50 SMA, the 20-day range edge) the emitted level
carries basis="structure"; where it is derived from volatility (an ATR
multiple) it carries basis="atr". Nothing is invented: if the data needed for
a level is missing the level is None and the setup is dropped rather than
padded out to look complete.

Pure functions, no network — fetching lives in scanners.scan_sector_leaders.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# ── Group definitions ────────────────────────────────────────────────────────
# tier "sector"   → the 11 GICS sectors, members resolved from watchlists.json
#                   ("Sector: X" lists, which are the S&P 500 constituents the
#                   rest of the workstation already classifies)
# tier "industry" → thematic groups that cut across GICS (semis and software
#                   are both "Technology" to yfinance, which is exactly why
#                   the hierarchy needs its own level for them). Members are
#                   curated liquid names, not an index reconstruction.

GICS_SECTORS = {
    "Technology":             {"etf": "XLK",  "wl": "Sector: Technology"},
    "Financials":             {"etf": "XLF",  "wl": "Sector: Financial Services"},
    "Energy":                 {"etf": "XLE",  "wl": "Sector: Energy"},
    "Industrials":            {"etf": "XLI",  "wl": "Sector: Industrials"},
    "Healthcare":             {"etf": "XLV",  "wl": "Sector: Healthcare"},
    "Consumer Discretionary": {"etf": "XLY",  "wl": "Sector: Consumer Cyclical"},
    "Consumer Staples":       {"etf": "XLP",  "wl": "Sector: Consumer Defensive"},
    "Communication Services": {"etf": "XLC",  "wl": "Sector: Communication Services"},
    "Utilities":              {"etf": "XLU",  "wl": "Sector: Utilities"},
    "Materials":              {"etf": "XLB",  "wl": "Sector: Basic Materials"},
    "Real Estate":            {"etf": "XLRE", "wl": "Sector: Real Estate"},
}

INDUSTRY_GROUPS = {
    "Semiconductors": {
        "etf": "SMH", "parent": "Technology",
        "members": ["NVDA", "AVGO", "TSM", "AMD", "QCOM", "TXN", "INTC", "MU",
                    "ADI", "AMAT", "LRCX", "KLAC", "ASML", "NXPI", "MRVL",
                    "MCHP", "ON", "SWKS", "TER", "ARM", "MPWR", "GFS", "ENTG",
                    "QRVO", "ALAB", "CRDO"],
    },
    "Software": {
        "etf": "IGV", "parent": "Technology",
        "members": ["MSFT", "ORCL", "CRM", "ADBE", "NOW", "INTU", "PANW",
                    "SNOW", "WDAY", "TEAM", "DDOG", "CRWD", "ZS", "HUBS",
                    "MDB", "NET", "PLTR", "ADSK", "SNPS", "CDNS", "VEEV",
                    "TYL", "APP", "SHOP"],
    },
    "Cybersecurity": {
        "etf": "CIBR", "parent": "Technology",
        # CYBR dropped — acquired, no Yahoo data
        "members": ["PANW", "CRWD", "FTNT", "ZS", "S", "OKTA", "QLYS",
                    "TENB", "RPD", "VRNS", "NET", "GEN", "AKAM"],
    },
    "AI": {
        "etf": "AIQ", "parent": "Technology", "wl": "AI",
        "members": [],   # resolved from watchlists.json AI/_tickers
    },
    "Biotech": {
        "etf": "IBB", "parent": "Healthcare",
        "members": ["AMGN", "GILD", "VRTX", "REGN", "BIIB", "MRNA", "ALNY",
                    "INCY", "BMRN", "NBIX", "UTHR", "EXEL", "SRPT", "IONS",
                    "JAZZ", "TECH", "ILMN", "RARE", "CRSP"],   # APLS dropped: acquired
    },
    "Transportation": {
        "etf": "IYT", "parent": "Industrials",
        "members": ["UPS", "FDX", "UNP", "CSX", "NSC", "ODFL", "JBHT",
                    "CHRW", "XPO", "SAIA", "LSTR", "DAL", "UAL", "LUV",
                    "AAL", "R", "MATX", "KNX", "EXPD"],
    },
    "Aerospace & Defense": {
        "etf": "ITA", "parent": "Industrials",
        "members": ["BA", "RTX", "LMT", "NOC", "GD", "LHX", "TDG", "HWM",
                    "HEI", "AXON", "TXT", "CW", "LDOS", "BWXT",
                    "KTOS", "AVAV"],   # SPR, ERJ dropped: no Yahoo data
    },
}

BENCHMARKS = ["SPY", "QQQ", "IWM", "DIA"]

# Leadership bands, straight from the spec
LEADERSHIP_BANDS = [
    (90, "🔥 Institutional leader"),
    (80, "🟢 Strong leader"),
    (70, "🟡 Developing leader"),
    (60, "⚪ Neutral"),
    (50, "🟠 Weak"),
    (0,  "🔴 Laggard"),
]

# A "large, liquid" name for the primary leader list. Dollar volume rather
# than market cap: it is measured from the same bars as everything else, and
# a $200bn name that trades 300k shares a day is not a leader candidate no
# matter what its cap says. Market cap is attached later, for reporting.
MIN_DOLLAR_VOLUME = 150_000_000


def band(score: float) -> str:
    for floor, label in LEADERSHIP_BANDS:
        if score >= floor:
            return label
    return LEADERSHIP_BANDS[-1][1]


# ── small helpers ────────────────────────────────────────────────────────────

def _scale(x, lo, hi, pts):
    """Linear clamp: x<=lo → 0, x>=hi → pts. None → half credit (neutral),
    so one missing input dents a score instead of zeroing it."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return pts / 2.0
    if hi == lo:
        return pts / 2.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo))) * pts


def _pct(a, b):
    """Percent change from b to a; None if either side is unusable."""
    if a is None or b is None or b == 0 or (isinstance(b, float) and math.isnan(b)):
        return None
    return (a / b - 1.0) * 100.0


def _last(s):
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else None


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR."""
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def swing_points(df: pd.DataFrame, k: int = 3):
    """Fractal swing highs/lows: a bar whose high is the max of the ±k window.
    Returns (highs, lows) as Series of prices at the swing bars only."""
    w = 2 * k + 1
    hi = df["High"]
    lo = df["Low"]
    is_hi = hi == hi.rolling(w, center=True).max()
    is_lo = lo == lo.rolling(w, center=True).min()
    return hi[is_hi], lo[is_lo]


# ── per-symbol metrics (pure: DataFrame in, dict out) ────────────────────────

def symbol_metrics(df: pd.DataFrame) -> dict | None:
    """Everything the scorers need from one symbol's daily OHLCV.

    Returns None when there is not enough history to say anything honest —
    the caller drops the symbol rather than scoring it on 30 bars.
    """
    if df is None or len(df) < 60:
        return None
    df = df.dropna(subset=["Close"])
    if len(df) < 60:
        return None

    c = df["Close"]
    v = df["Volume"]
    close = float(c.iloc[-1])

    ema20 = c.ewm(span=20, adjust=False).mean()
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean() if len(c) >= 200 else pd.Series(dtype=float)

    e20, s50 = _last(ema20), _last(sma50)
    s200 = _last(sma200) if len(sma200) else None

    a14 = _last(atr(df))
    vol20 = float(v.tail(20).mean()) if len(v) >= 20 else None
    vol5 = float(v.tail(5).mean()) if len(v) >= 5 else None
    today_vol = float(v.iloc[-1]) if len(v) else None

    # up/down volume: share of the last 10 sessions' volume that printed on
    # an up-close day. 0.5 is balanced; the tails are accumulation/distribution.
    tail = df.tail(10)
    up_mask = tail["Close"] > tail["Close"].shift(1)
    up_vol = float(tail.loc[up_mask, "Volume"].sum())
    tot_vol = float(tail["Volume"].sum())
    ud_share = (up_vol / tot_vol) if tot_vol else None

    # Two senses of "the 20-day high", and conflating them was a real bug.
    # The INTRADAY high is the breakout trigger price. The CLOSING high is
    # what "a stock made a new 20-day high" means for breadth and for setup
    # detection — testing the close against the intraday high demanded a
    # finish within 0.1% of the highest wick in a month, which essentially
    # never happens: every sector reported 0 new highs and 0 new lows, and
    # no breakout setup could fire anywhere in the universe.
    hi20c = float(c.tail(20).max())
    lo20c = float(c.tail(20).min())
    hi60c = float(c.tail(60).max())
    lo60c = float(c.tail(60).min())
    hi20 = float(df["High"].tail(20).max())
    lo20 = float(df["Low"].tail(20).min())
    hi60 = float(df["High"].tail(60).max())
    lo60 = float(df["Low"].tail(60).min())
    hi252 = float(df["High"].tail(252).max())
    lo252 = float(df["Low"].tail(252).min())

    sh, sl = swing_points(df.tail(90))
    higher_highs = bool(len(sh) >= 2 and sh.iloc[-1] > sh.iloc[-2])
    higher_lows = bool(len(sl) >= 2 and sl.iloc[-1] > sl.iloc[-2])
    lower_highs = bool(len(sh) >= 2 and sh.iloc[-1] < sh.iloc[-2])
    lower_lows = bool(len(sl) >= 2 and sl.iloc[-1] < sl.iloc[-2])

    # 20 EMA slope over 10 sessions, in percent — direction of the trend line
    ema_slope = _pct(float(ema20.iloc[-1]), float(ema20.iloc[-11])) if len(ema20) > 11 else None

    return {
        "close": close,
        "r1d":  _pct(close, float(c.iloc[-2])),
        "r5d":  _pct(close, float(c.iloc[-6])) if len(c) > 6 else None,
        "r20d": _pct(close, float(c.iloc[-21])) if len(c) > 21 else None,
        "r60d": _pct(close, float(c.iloc[-61])) if len(c) > 61 else None,
        "ema20": e20, "sma50": s50, "sma200": s200,
        "above_ema20":  close > e20 if e20 else None,
        "above_sma50":  close > s50 if s50 else None,
        "above_sma200": close > s200 if s200 else None,
        "dist_ema20_pct":  _pct(close, e20),
        "dist_sma50_pct":  _pct(close, s50),
        "dist_sma200_pct": _pct(close, s200),
        "atr14": a14,
        "atr_pct": (a14 / close * 100.0) if a14 else None,
        "rvol": (today_vol / vol20) if (today_vol and vol20) else None,
        "vol_expansion": (vol5 / vol20) if (vol5 and vol20) else None,
        "updown_share": ud_share,
        "avg_vol20": vol20,
        "dollar_vol": (vol20 * close) if vol20 else None,
        "hi20": hi20, "lo20": lo20, "hi60": hi60, "lo60": lo60,
        "hi252": hi252, "lo252": lo252,
        "hi20_close": hi20c, "lo20_close": lo20c,
        "hi60_close": hi60c, "lo60_close": lo60c,
        "at_20d_high": close >= hi20c,      # 20-day CLOSING high
        "at_20d_low":  close <= lo20c,
        "at_60d_high": close >= hi60c,
        "at_60d_low":  close <= lo60c,
        "pos_in_20d_range": ((close - lo20) / (hi20 - lo20) * 100.0) if hi20 > lo20 else None,
        "pos_in_52w_range": ((close - lo252) / (hi252 - lo252) * 100.0) if hi252 > lo252 else None,
        "higher_highs": higher_highs, "higher_lows": higher_lows,
        "lower_highs": lower_highs, "lower_lows": lower_lows,
        "ema20_slope_pct": ema_slope,
        "swing_highs": sh, "swing_lows": sl,
        "bars": len(df),
    }


def relative_strength(m: dict, bench: dict) -> dict:
    """Stock-minus-benchmark return spreads, in percentage points."""
    out = {}
    for w in ("r5d", "r20d", "r60d"):
        a, b = m.get(w), bench.get(w)
        out[w] = round(a - b, 2) if (a is not None and b is not None) else None
    return out


# ── breadth (pure: member metrics in, dict out) ──────────────────────────────

def breadth(member_metrics: list[dict]) -> dict:
    """Real breadth over a group's actual constituents.

    This is universe breadth — the members we hold lists for — not exchange
    internals. TICK/TRIN/VOLD and the full NYSE advance-decline line have no
    free feed, so they are absent here rather than approximated.
    """
    n = len(member_metrics)
    if not n:
        return {"members": 0, "pct_above_ema20": None, "pct_above_sma50": None,
                "pct_above_sma200": None, "new_highs_20d": None,
                "new_lows_20d": None, "net_new_highs_pct": None,
                "advancing": None, "pct_advancing": None}

    def share(key):
        vals = [m[key] for m in member_metrics if m.get(key) is not None]
        return round(100.0 * sum(vals) / len(vals), 1) if vals else None

    nh = sum(1 for m in member_metrics if m.get("at_20d_high"))
    nl = sum(1 for m in member_metrics if m.get("at_20d_low"))
    adv = sum(1 for m in member_metrics if (m.get("r1d") or 0) > 0)

    return {
        "members": n,
        "pct_above_ema20":  share("above_ema20"),
        "pct_above_sma50":  share("above_sma50"),
        "pct_above_sma200": share("above_sma200"),
        "new_highs_20d": nh,
        "new_lows_20d": nl,
        "net_new_highs_pct": round(100.0 * (nh - nl) / n, 1),
        "advancing": adv,
        "pct_advancing": round(100.0 * adv / n, 1),
    }


# ── sector scoring ───────────────────────────────────────────────────────────

def score_sector(m: dict, rs_spy: dict, rs_qqq: dict, rs_iwm: dict,
                 br: dict) -> dict:
    """Sector Trend Score 0-100.

    Budget: momentum 25 / relative strength 25 / breadth 25 / volume 15 /
    trend quality 10. Breadth carries a quarter of the score on purpose — a
    sector ETF can be dragged up by two mega-caps, and the members are the
    only place that shows.
    """
    # momentum 25
    mo = (_scale(m.get("r1d"), -1.5, 1.5, 5)
          + _scale(m.get("r5d"), -4, 4, 6)
          + _scale(m.get("r20d"), -8, 8, 7)
          + (2.5 if m.get("above_ema20") else 0)
          + (2.5 if m.get("above_sma50") else 0)
          + (2.0 if m.get("above_sma200") else 0))

    # relative strength 25
    rs = (_scale(rs_spy.get("r5d"), -3, 3, 6)
          + _scale(rs_spy.get("r20d"), -6, 6, 7)
          + _scale(rs_qqq.get("r20d"), -6, 6, 6)
          + _scale(rs_iwm.get("r20d"), -6, 6, 6))

    # breadth 25
    bd = (_scale(br.get("pct_above_ema20"), 0, 100, 8)
          + _scale(br.get("pct_above_sma50"), 0, 100, 8)
          + _scale(br.get("pct_above_sma200"), 0, 100, 5)
          + _scale(br.get("net_new_highs_pct"), -10, 10, 4))

    # volume 15
    vo = (_scale(m.get("rvol"), 0.6, 1.6, 6)
          + _scale(m.get("vol_expansion"), 0.8, 1.3, 4)
          + _scale(m.get("updown_share"), 0.35, 0.65, 5))

    # trend quality 10
    stack = 0.0
    if m.get("above_ema20"): stack += 1.5
    if m.get("ema20") and m.get("sma50") and m["ema20"] > m["sma50"]: stack += 1.5
    if m.get("above_sma200"): stack += 1.0
    struct = 3.0 if (m.get("higher_highs") and m.get("higher_lows")) else (
             1.5 if (m.get("higher_highs") or m.get("higher_lows")) else 0.0)
    slope = _scale(m.get("ema20_slope_pct"), -3, 3, 3)
    tq = stack + struct + slope

    total = mo + rs + bd + vo + tq
    return {
        "score": round(total, 1),
        "momentum": round(mo, 1), "rel_strength": round(rs, 1),
        "breadth": round(bd, 1), "volume": round(vo, 1), "trend_quality": round(tq, 1),
        "quality_label": trend_quality_label(m, total),
    }


def trend_quality_label(m: dict, score: float) -> str:
    """Label the tape, not the ranking.

    Moving-average context comes first and swing structure only refines it.
    An earlier cut labelled anything with lower highs and lower lows an
    "emerging downtrend" — which called XLRE, XLK and XLI downtrends while all
    three sat above their 50 and 200 SMAs. Above the 50 and 200 but under the
    20 EMA is a pullback inside an uptrend, and it maps to *neutral*, not
    bearish: it is the wrong thing to short and the wrong thing to chase.
    """
    e20, s50, s200 = m.get("above_ema20"), m.get("above_sma50"), m.get("above_sma200")
    if e20 is None or s50 is None:
        return "Range"
    hh_hl = bool(m.get("higher_highs") and m.get("higher_lows"))
    lh_ll = bool(m.get("lower_highs") and m.get("lower_lows"))
    n_above = sum(1 for f in (e20, s50, s200) if f)

    if n_above == 3:
        if hh_hl and score >= 60:
            return "Strong uptrend"
        if score >= 50:
            return "Emerging uptrend"
        return "Range"
    if e20 and s50 and not s200:
        return "Emerging uptrend" if score >= 50 else "Range"
    if not e20 and s50 and s200:
        return "Uptrend pullback"
    if n_above == 0:
        return "Strong downtrend" if lh_ll else "Emerging downtrend"
    # below the 20 EMA and the 50 SMA, still above the 200
    return "Strong downtrend" if (lh_ll and score <= 25) else "Emerging downtrend"


def sector_direction(sec: dict) -> str:
    q = sec["quality_label"]
    if q in ("Strong uptrend", "Emerging uptrend"):
        return "bullish"
    if q in ("Strong downtrend", "Emerging downtrend"):
        return "bearish"
    return "neutral"   # "Range" and "Uptrend pullback" — neither side confirmed


# ── trend clarity ────────────────────────────────────────────────────────────

def trend_clarity(df: pd.DataFrame, direction: str = "long",
                  window: int = 60) -> dict | None:
    """How *clean* the trend is, 0-100 — the "can you actually hold this"
    score, deliberately blind to how big the move was.

      R² of the log-price fit        40   straight line vs staircase
      days on the right side of 20EMA 20  persistence, not a single poke
      ATR-normalised chop            20   how deep the counter-moves ran
      MA-stack persistence           20   how long the regime has held

    A stock up 40% in six weeks of ±8% swings scores worse than one up 12%
    that never closed below its 20 EMA, which is the whole point.
    """
    if df is None or len(df) < window + 5:
        return None
    d = df.tail(window)
    c = d["Close"]
    y = np.log(c.values)
    x = np.arange(len(y), dtype=float)
    if np.std(y) == 0:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    fit = slope * x + intercept
    ss_res = float(np.sum((y - fit) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # the fit must slope the way we are trading, or clarity is worthless
    if direction == "long" and slope <= 0:
        r2 = 0.0
    if direction == "short" and slope >= 0:
        r2 = 0.0

    ema20 = df["Close"].ewm(span=20, adjust=False).mean().tail(window)
    right_side = ((c > ema20) if direction == "long" else (c < ema20)).mean() * 100.0

    a = atr(df).tail(window)
    if direction == "long":
        peak = c.cummax()
        adverse = ((peak - c) / a).max()
    else:
        trough = c.cummin()
        adverse = ((c - trough) / a).max()
    adverse = float(adverse) if pd.notna(adverse) else None

    sma50 = df["Close"].rolling(50).mean().tail(window)
    stack = ((ema20 > sma50) if direction == "long" else (ema20 < sma50))
    stack_pct = float(stack.mean() * 100.0)

    score = (_scale(r2, 0.25, 0.95, 40)
             + _scale(right_side, 40, 95, 20)
             + _scale(adverse, 7.0, 1.5, 20)      # inverted: less chop = more
             + _scale(stack_pct, 40, 100, 20))
    return {
        "score": round(score, 1),
        "r2": round(r2, 3),
        "pct_right_side_ema20": round(right_side, 1),
        "max_adverse_atr": round(adverse, 2) if adverse is not None else None,
        "ma_stack_pct": round(stack_pct, 1),
        "slope_per_day_pct": round((math.exp(slope) - 1) * 100, 3),
    }


# ── stock scoring ────────────────────────────────────────────────────────────

def score_stock(m: dict, rs_spy: dict, rs_qqq: dict, rs_sector: dict,
                setup: dict | None, sector_dir: str, market_dir: str,
                direction: str = "long") -> dict:
    """Stock Trend Score 0-100 on the spec's fixed budget.

    Trend 25 / RS 20 / momentum 15 / volume 15 / structure 15 / sector 10.
    Short scoring mirrors every test rather than negating the long score, so
    a range-bound name scores mediocre in *both* directions instead of
    scoring 100 in one of them.
    """
    lng = direction == "long"

    # trend 25
    def side(flag):
        if flag is None:
            return False
        return flag if lng else (not flag)

    trend = ((6 if side(m.get("above_ema20")) else 0)
             + (6 if side(m.get("above_sma50")) else 0)
             + (5 if side(m.get("above_sma200")) else 0))
    if lng:
        trend += (8 if (m.get("higher_highs") and m.get("higher_lows")) else
                  4 if (m.get("higher_highs") or m.get("higher_lows")) else 0)
    else:
        trend += (8 if (m.get("lower_highs") and m.get("lower_lows")) else
                  4 if (m.get("lower_highs") or m.get("lower_lows")) else 0)

    # relative strength 20
    def rsp(v, pts):
        if v is None:
            return pts / 2
        return _scale(v if lng else -v, -6, 6, pts)

    rs = rsp(rs_spy.get("r20d"), 7) + rsp(rs_qqq.get("r20d"), 6) + rsp(rs_sector.get("r20d"), 7)

    # momentum 15
    def mom(v, lo, hi, pts):
        if v is None:
            return pts / 2
        return _scale(v if lng else -v, lo, hi, pts)

    momentum = mom(m.get("r1d"), -2, 2, 4) + mom(m.get("r5d"), -5, 5, 5) + mom(m.get("r20d"), -10, 10, 6)

    # volume 15 — RVOL and expansion are direction-blind (participation is
    # participation); up/down share is not
    ud = m.get("updown_share")
    ud_pts = _scale(ud if lng else (1 - ud) if ud is not None else None, 0.35, 0.65, 5)
    dv = m.get("dollar_vol")
    volume = (_scale(m.get("rvol"), 0.7, 1.8, 6)
              + _scale(math.log10(dv) if dv and dv > 0 else None, 8.0, 9.7, 4)
              + ud_pts)

    # structure 15 — the setup's own quality, plus where in the 20-day range
    # the close sits (top of range for longs, bottom for shorts)
    struct = setup["quality_points"] if setup else 4.0
    pos = m.get("pos_in_20d_range")
    struct += _scale(pos if lng else (100 - pos) if pos is not None else None, 20, 90, 5)

    # sector confirmation 10
    want = "bullish" if lng else "bearish"
    conf = (6 if sector_dir == want else 2 if sector_dir == "neutral" else 0)
    conf += (4 if market_dir == want else 2 if market_dir == "neutral" else 0)

    total = trend + rs + momentum + volume + struct + conf
    return {
        "score": round(total, 1),
        "trend": round(trend, 1), "rel_strength": round(rs, 1),
        "momentum": round(momentum, 1), "volume": round(volume, 1),
        "structure": round(struct, 1), "sector_confirm": round(conf, 1),
    }


def leadership_score(sector_score: float, stock_rs: dict, m: dict, br: dict,
                     market_dir: str, direction: str = "long") -> dict:
    """LEADERSHIP = sector RS + stock RS + trend + volume + breadth + market
    alignment. Answers "is this name driving the group", which is a different
    question from "is this a good entry" — a leader can be extended."""
    lng = direction == "long"
    want = "bullish" if lng else "bearish"

    sec_rs = _scale(sector_score if lng else (100 - sector_score), 35, 75, 20)
    v = stock_rs.get("r20d")
    stk_rs = _scale((v if lng else -v) if v is not None else None, -6, 8, 20)

    tr = 0.0
    for key, pts in (("above_ema20", 7), ("above_sma50", 7), ("above_sma200", 6)):
        f = m.get(key)
        if f is None:
            tr += pts / 2
        elif f == lng:
            tr += pts

    vol = (_scale(m.get("rvol"), 0.7, 1.8, 8)
           + _scale(m.get("updown_share") if lng else
                    (1 - m["updown_share"]) if m.get("updown_share") is not None else None,
                    0.35, 0.65, 7))

    b = br.get("pct_above_sma50")
    bd = _scale(b if lng else (100 - b) if b is not None else None, 25, 85, 15)
    mkt = 10.0 if market_dir == want else 5.0 if market_dir == "neutral" else 0.0

    total = sec_rs + stk_rs + tr + vol + bd + mkt
    return {"score": round(total, 1), "band": band(total)}


def confluence_score(market_dir: str, market_strength: float, sector: dict,
                     stock: dict, clarity: dict | None, rs: dict,
                     m: dict, direction: str = "long") -> dict:
    """MARKET → SECTOR → STOCK, 0-100.

    Deliberately gate-like: a stock cannot buy back a hostile market with a
    prettier chart. The market and sector blocks are 50 points between them,
    so the strongest chart in a fighting-the-tape position tops out near 60 —
    which is what flags it as a divergence instead of a trade.
    """
    want = "bullish" if direction == "long" else "bearish"

    # market 25 — direction match scaled by how convinced the regime is
    if market_dir == want:
        mkt = 15 + _scale(market_strength, 0, 6, 10)
    elif market_dir == "neutral":
        mkt = 12.0
    else:
        mkt = _scale(6 - market_strength, 0, 6, 8)

    # sector 25
    sdir = sector_direction(sector)
    ss = sector["score"] if direction == "long" else 100 - sector["score"]
    sec = (_scale(ss, 40, 75, 15) + (10 if sdir == want else 4 if sdir == "neutral" else 0))

    clar = _scale(clarity["score"] if clarity else None, 30, 85, 20)
    v = rs.get("r20d")
    rss = _scale((v if direction == "long" else -v) if v is not None else None, -4, 8, 15)
    vol = (_scale(m.get("rvol"), 0.7, 1.6, 8)
           + _scale(m.get("vol_expansion"), 0.85, 1.35, 7))

    total = mkt + sec + clar + rss + vol
    return {
        "score": round(total, 1),
        "market": round(mkt, 1), "sector": round(sec, 1),
        "clarity": round(clar, 1), "rel_strength": round(rss, 1),
        "volume": round(vol, 1),
    }


# ── setup detection + levels ─────────────────────────────────────────────────
# Every emitted level carries a basis: "structure" (a real swing point, an MA,
# a range edge — a level the market drew) or "atr" (a volatility multiple —
# a level we derived). Nothing is invented; a level we cannot compute is None
# and the setup is dropped.

# Level-building constants. These exist because the first cut of this engine
# had neither, and produced stops 0.13 ATR from entry (BAX — noise would have
# taken it out inside an hour, and the 25:1 reward ratio that implied was
# fiction) alongside stops 4.8 ATR away (MRK, whose only structural swing low
# was 13.6% below the close). A stop has to sit outside the noise and inside
# the plan; if no structural level does both, the level is volatility-derived
# and says so.
MIN_STOP_ATR = 1.0    # tighter than this is inside the daily noise
MAX_STOP_ATR = 2.0    # wider than this stops being a swing stop
MIN_T1_R = 0.75       # a "target" nearer than this is not a target
MAX_T1_ATR = 4.0      # a first target further than this is not a swing target
MAX_T2_ATR = 8.0      # nor is the 52-week low a second target

# How far above the 20 EMA a name can be and still have an entry. Past this
# it is extended, and the honest output is "wait", not a chase price — this
# is the spec's "do not chase large one-day moves" as an arithmetic test
# rather than a hope.
EXTENSION_NO_ENTRY_ATR = 4.0


def _pick_stop(entry: float, atr_val: float, structural: list,
               direction: str) -> tuple[float, str]:
    """Tightest structural level that clears the noise floor, else an ATR stop.

    Returns (price, basis). Structural candidates are (price, label) pairs;
    ones inside MIN_STOP_ATR are rejected as noise and ones beyond
    MAX_STOP_ATR are rejected as not-a-stop, in which case the ATR cap is
    used and the basis says so.
    """
    lo = MIN_STOP_ATR * atr_val
    hi = MAX_STOP_ATR * atr_val
    ok = []
    for price, label in structural:
        if price is None:
            continue
        dist = (entry - price) if direction == "long" else (price - entry)
        if lo <= dist <= hi:
            ok.append((dist, price, label))
    if ok:
        dist, price, label = min(ok)          # tightest that still clears noise
        return price, f"{label} ({dist / atr_val:.1f} ATR)"
    capped = entry - hi if direction == "long" else entry + hi
    near = [f"{label} {price:.2f}" for price, label in structural if price is not None]
    note = f"no structural level between {MIN_STOP_ATR:.0f}–{MAX_STOP_ATR:.0f} ATR"
    if near:
        note += f" (nearest: {', '.join(near[:2])})"
    return capped, f"{MAX_STOP_ATR:.0f} ATR from entry — {note}"


def _pick_targets(entry: float, stop: float, atr_val: float,
                  ladder: list, direction: str) -> tuple:
    """Walk the structural ladder outward; skip levels not worth the risk, and
    stop before the ones that are not targets at all.

    Three separate failures had to be designed out:

      * a level the price already sits on is not a target — that produced
        MRK's 0.05 reward ratio, where "target 1" was the 20-day high 0.7%
        overhead. Anything nearer than MIN_T1_R is treated as reclaimed and
        stepped over.
      * the *next* level after that can be absurdly far — skipping ON's
        nearby lows landed on its 52-week low, a 42% move, as target 1.
        Beyond MAX_T1_ATR the ladder is abandoned for an ATR objective.
      * target 2 has to be further than target 1. It was not: DHR priced
        T1 242.80 and T2 234.75, and AMAT's T2 was 154.47 against a 496
        entry. The final clamp makes the ordering structural, not incidental.
    """
    risk = abs(entry - stop)
    if risk <= 0 or atr_val <= 0:
        return None, None, None, None

    sign = 1 if direction == "long" else -1

    def beyond(price):
        return (price - entry) * sign

    rungs = sorted({p for p in ladder if p is not None},
                   key=beyond)
    live = [p for p in rungs
            if MIN_T1_R * risk <= beyond(p) <= MAX_T1_ATR * atr_val]

    if live:
        t1, t1b = live[0], "structural level"
    else:
        t1 = entry + sign * 2.0 * atr_val
        far = [p for p in rungs if beyond(p) > MAX_T1_ATR * atr_val]
        t1b = ("entry ± 2 ATR — no structure within "
               f"{MAX_T1_ATR:.0f} ATR" +
               (f" (next is {far[0]:.2f})" if far else ""))

    t2_cand = [p for p in rungs
               if beyond(p) >= 1.5 * beyond(t1) and beyond(p) <= MAX_T2_ATR * atr_val]
    if t2_cand:
        t2, t2b = t2_cand[0], "next structural level"
    else:
        t2 = entry + sign * 3.5 * atr_val
        t2b = "entry ± 3.5 ATR (no further structure in range)"

    # target 2 must sit beyond target 1 whatever the ladder did
    if beyond(t2) <= beyond(t1):
        t2 = t1 + sign * 1.5 * atr_val
        t2b = "target 1 + 1.5 ATR (ladder had nothing further)"
    return t1, t1b, t2, t2b


def detect_setup(df: pd.DataFrame, m: dict, direction: str = "long") -> dict | None:
    """Pick the one setup the tape actually shows and price it."""
    if not m or m.get("atr14") in (None, 0):
        return None
    a = m["atr14"]
    c = m["close"]
    e20, s50, s200 = m.get("ema20"), m.get("sma50"), m.get("sma200")
    sh, sl_ = m.get("swing_highs"), m.get("swing_lows")
    last_sw_hi = float(sh.iloc[-1]) if sh is not None and len(sh) else None
    last_sw_lo = float(sl_.iloc[-1]) if sl_ is not None and len(sl_) else None
    prev_sw_hi = float(sh.iloc[-2]) if sh is not None and len(sh) >= 2 else None
    prev_sw_lo = float(sl_.iloc[-2]) if sl_ is not None and len(sl_) >= 2 else None

    rng20 = (m["hi20"] - m["lo20"]) / c * 100.0 if c else None
    d_e20 = m.get("dist_ema20_pct")
    d_s50 = m.get("dist_sma50_pct")

    # extension test first — a name this far from its 20 EMA has no honest
    # entry price today regardless of how good the rest of the score looks
    ext = ((c - e20) / a if direction == "long" else (e20 - c) / a) if e20 else None
    if ext is not None and ext > EXTENSION_NO_ENTRY_ATR:
        return {"setup": f"Extended {ext:.1f} ATR from 20 EMA — no entry",
                "quality_points": 3.0, "levels": None, "rr": None, "rr2": None,
                "grade": "— (wait for pullback)", "extension_atr": round(ext, 1)}

    if direction == "long":
        up_stack = bool(m.get("above_ema20") and m.get("above_sma50"))
        if m.get("at_20d_high") and m.get("at_60d_high"):
            name, quality = "Breakout (60d high)", 10.0
        elif m.get("at_20d_high"):
            name, quality = "Breakout (20d high)", 9.0
        elif rng20 is not None and rng20 < 9 and (m.get("pos_in_20d_range") or 0) >= 70 and up_stack:
            name, quality = "Consolidation breakout pending", 8.5
        elif up_stack and d_e20 is not None and -1.0 <= d_e20 <= 2.0:
            name, quality = "Pullback to 20 EMA", 9.5
        elif m.get("above_sma200") and d_s50 is not None and -1.5 <= d_s50 <= 2.5:
            name, quality = "Pullback to 50 SMA", 8.5
        elif up_stack and m.get("higher_lows"):
            name, quality = "Higher-low continuation", 7.5
        elif m.get("above_ema20") and m.get("above_sma50") and m.get("above_sma200"):
            name, quality = "Trend continuation", 5.5
        else:
            name, quality = "No clean long setup", 2.0
    else:
        dn_stack = bool(m.get("above_ema20") is False and m.get("above_sma50") is False)
        if m.get("at_20d_low") and m.get("at_60d_low"):
            name, quality = "Breakdown (60d low)", 10.0
        elif m.get("at_20d_low"):
            name, quality = "Breakdown (20d low)", 9.0
        elif dn_stack and d_e20 is not None and -2.0 <= d_e20 <= 1.0:
            name, quality = "20 EMA rejection", 9.5
        elif m.get("above_sma200") is False and d_s50 is not None and -2.5 <= d_s50 <= 1.5:
            name, quality = "50 SMA rejection", 8.5
        elif dn_stack and m.get("lower_highs"):
            name, quality = "Lower-high continuation", 7.5
        elif rng20 is not None and rng20 < 9 and (m.get("pos_in_20d_range") or 100) <= 30 and dn_stack:
            name, quality = "Bear-flag breakdown pending", 8.0
        elif dn_stack:
            name, quality = "Downtrend continuation", 5.5
        else:
            name, quality = "No clean short setup", 2.0

    if name.startswith("No clean"):
        return {"setup": name, "quality_points": quality, "levels": None,
                "rr": None, "rr2": None, "grade": "—"}

    # ── entry zone ──────────────────────────────────────────────────────────
    if direction == "long":
        if name.startswith("Breakout"):
            entry_lo, entry_hi = c, c + 0.35 * a
            entry_basis = "close at the 20d/60d high (structure) + 0.35 ATR"
        elif "Pullback to 20 EMA" in name:
            entry_lo, entry_hi = e20 - 0.15 * a, e20 + 0.35 * a
            entry_basis = "20 EMA (structure) ± ATR fraction"
        elif "Pullback to 50 SMA" in name:
            entry_lo, entry_hi = s50 - 0.2 * a, s50 + 0.4 * a
            entry_basis = "50 SMA (structure) ± ATR fraction"
        else:
            entry_lo, entry_hi = c - 0.25 * a, c + 0.35 * a
            entry_basis = "current close ± ATR fraction"
        structural = [(last_sw_lo, "last swing low"), (e20, "20 EMA"),
                      (s50, "50 SMA"), (s200, "200 SMA"), (m["lo20"], "20d low")]
        ladder = [m["hi20"], prev_sw_hi, last_sw_hi, m["hi60"], m["hi252"]]
    else:
        if name.startswith("Breakdown"):
            entry_lo, entry_hi = c - 0.35 * a, c
            entry_basis = "close at the 20d/60d low (structure) − 0.35 ATR"
        elif "20 EMA rejection" in name:
            entry_lo, entry_hi = e20 - 0.35 * a, e20 + 0.15 * a
            entry_basis = "20 EMA (structure) ± ATR fraction"
        elif "50 SMA rejection" in name:
            entry_lo, entry_hi = s50 - 0.4 * a, s50 + 0.2 * a
            entry_basis = "50 SMA (structure) ± ATR fraction"
        else:
            entry_lo, entry_hi = c - 0.35 * a, c + 0.25 * a
            entry_basis = "current close ± ATR fraction"
        structural = [(last_sw_hi, "last swing high"), (e20, "20 EMA"),
                      (s50, "50 SMA"), (s200, "200 SMA"), (m["hi20"], "20d high")]
        ladder = [m["lo20"], prev_sw_lo, last_sw_lo, m["lo60"], m["lo252"]]

    entry = (entry_lo + entry_hi) / 2
    stop, stop_basis = _pick_stop(entry, a, structural, direction)
    t1, t1b, t2, t2b = _pick_targets(entry, stop, a, ladder, direction)
    if t1 is None:
        return {"setup": name, "quality_points": quality, "levels": None,
                "rr": None, "rr2": None, "grade": "—",
                "note": "no valid risk structure"}

    risk = abs(entry - stop)
    rr1 = abs(t1 - entry) / risk
    rr2 = abs(t2 - entry) / risk

    if rr1 < 1.0:
        grade = "D — reward below risk to first target"
    elif quality >= 9 and rr1 >= 1.5:
        grade = "A"
    elif quality >= 8 and rr1 >= 1.3:
        grade = "B"
    elif quality >= 6 and rr1 >= 1.2:
        grade = "C"
    else:
        grade = "D"

    return {
        "setup": name,
        "quality_points": quality,
        "grade": grade,
        "rr": round(rr1, 2),
        "rr2": round(rr2, 2),
        "extension_atr": round(ext, 1) if ext is not None else None,
        "levels": {
            "entry_low": round(entry_lo, 2), "entry_high": round(entry_hi, 2),
            "entry_mid": round(entry, 2), "entry_basis": entry_basis,
            "stop": round(stop, 2), "stop_basis": stop_basis,
            "target1": round(t1, 2), "target1_basis": t1b,
            "target2": round(t2, 2), "target2_basis": t2b,
            "risk_per_share": round(risk, 2),
            "risk_atr": round(risk / a, 2),
            "atr14": round(a, 2),
            "invalidation": (f"close {'below' if direction == 'long' else 'above'} "
                             f"{round(stop, 2)} — {stop_basis}"),
        },
    }


def divergence_flag(sector_dir: str, stock_dir: str, rank_in_sector: int,
                    members: int) -> str | None:
    """Section 11: the four cases worth naming out loud."""
    if sector_dir == "bullish" and stock_dir == "bullish" and rank_in_sector == 1:
        return "LEADERSHIP — strongest name in a bullish sector"
    if sector_dir == "bearish" and stock_dir == "bearish" and rank_in_sector == 1:
        return "WEAK LEADERSHIP — weakest name in a bearish sector"
    if sector_dir == "bullish" and stock_dir == "bearish":
        return "BULLISH DIVERGENCE — sector strong, stock lagging; catch-up candidate, not a short"
    if sector_dir == "bearish" and stock_dir == "bullish":
        return "BEARISH DIVERGENCE — stock holding up in a weak sector; do NOT short reflexively"
    return None


# ── confirmation stack: technicals + fundamentals + news ─────────────────────
# The scan's confluence score answers "do market, sector and stock agree?" —
# entirely from price and volume. It cannot see that a name gapped because it
# reported this morning, or that the cleanest-looking short just guided up.
# Those are the two ways a technically perfect candidate turns out to be a
# trap, and both are visible in data the scan does not read.
#
# So confidence layers two more inputs on top of confluence, and then applies
# gates. Weights move a score; gates cap it. That split is deliberate: a name
# whose news contradicts its chart should not be able to average its way back
# to high confidence on a strong enough chart.

NEWS_WEIGHTS = {
    "confirms":    100,   # company-specific news pushing the same way as the chart
    "neutral":      50,   # nothing company-specific either way
    "mixed":        35,   # genuine two-sided flow
    "contradicts":   0,   # company-specific news pushing against the chart
    "unavailable":  40,   # no feed — penalised slightly vs neutral, not zeroed
}

EARNINGS_WINDOW_DAYS = 5      # inside this, the setup is an earnings bet
CAP_EARNINGS_TODAY = 45       # reported today: the move is a gap, not a trend
CAP_EARNINGS_SOON = 55
CAP_NEWS_CONTRADICTS = 50
CAP_NO_ENTRY = 50             # extended names have no honest entry price

CONFIDENCE_BANDS = [
    (80, "HIGH — technicals, fundamentals and news all agree"),
    (65, "MODERATE — two of three agree"),
    (50, "LOW — technicals only"),
    (0,  "AVOID — signals conflict"),
]


def fundamental_alignment(bq: float | None, fh: float | None, direction: str,
                          price: float | None = None,
                          target_mean: float | None = None) -> tuple[float, str]:
    """0-100: does the fundamental picture argue for this direction?

    Two halves. Balance-sheet quality, and where price sits against the
    analyst consensus target.

    The quality half is asymmetric but only mildly so. Shorting a 95-quality
    balance sheet IS harder than shorting a weak one, so quality is a headwind
    for shorts — but a straight 100−quality inversion buried every short in
    this scan, because the weak sector of the day is semiconductors and every
    name in it has a fortress balance sheet. A sector de-rating in
    high-quality names is a real trade. The inversion is therefore compressed
    toward neutral rather than applied at full strength.

    The consensus half carries a known bias: sell-side targets sit above
    price far more often than below, so this term systematically favours
    longs. It is useful for ranking names against each other, not as an
    absolute verdict, and it is reported alongside the number.
    """
    parts, notes = [], []

    vals = [v for v in (bq, fh) if v is not None]
    if vals:
        base = sum(vals) / len(vals)
        if direction == "long":
            q = base
            notes.append(f"quality/health avg {base:.0f} supports a long")
        else:
            q = 50 + (50 - base) * 0.6
            notes.append(f"quality/health avg {base:.0f} — a headwind for a "
                         f"short, compressed not disqualifying")
        parts.append((q, 2.0))
    else:
        notes.append("no quality data")

    # Bounds are wide (−15% to +45%) because they have to be. Every one of
    # the eleven semiconductor shorts in this scan carried +15% to +46%
    # consensus upside; on a narrower scale all eleven pinned to zero and the
    # term stopped discriminating between them. Weighted a third, not a half,
    # for the same reason: it is the weakest of the two inputs.
    if price and target_mean:
        upside = (target_mean / price - 1) * 100
        v = (_scale(upside, -15, 45, 100) if direction == "long"
             else _scale(upside, 45, -15, 100))
        parts.append((v, 1.0))
        notes.append(f"consensus target {target_mean:.0f} vs {price:.0f} "
                     f"({upside:+.0f}%) — sell-side targets skew high")
    else:
        notes.append("no consensus target")

    if not parts:
        return 40.0, "no fundamental data — scored below neutral, not zeroed"
    total_w = sum(w for _, w in parts)
    return sum(v * w for v, w in parts) / total_w, "; ".join(notes)


def confidence_score(confluence: float, bq: float | None, fh: float | None,
                     news_verdict: str, days_to_earnings: int | None,
                     has_entry: bool, direction: str,
                     price: float | None = None,
                     target_mean: float | None = None) -> dict:
    """Blend the three layers, then apply the gates.

    news_verdict is a human/model reading of the fetched headlines, not a
    keyword count — see scanners.confirm_leaders for why the lexicon scorer
    in this codebase is not used for it.
    """
    fund, fund_note = fundamental_alignment(bq, fh, direction, price, target_mean)
    news = NEWS_WEIGHTS.get(news_verdict, NEWS_WEIGHTS["unavailable"])

    raw = 0.50 * confluence + 0.25 * fund + 0.25 * news
    score, caps = raw, []

    if news_verdict == "contradicts" and score > CAP_NEWS_CONTRADICTS:
        score = CAP_NEWS_CONTRADICTS
        caps.append("news contradicts the chart")
    if days_to_earnings is not None:
        if days_to_earnings == 0 and score > CAP_EARNINGS_TODAY:
            score = CAP_EARNINGS_TODAY
            caps.append("reported earnings today — this is a gap, not a trend entry")
        elif 0 < days_to_earnings <= EARNINGS_WINDOW_DAYS and score > CAP_EARNINGS_SOON:
            score = CAP_EARNINGS_SOON
            caps.append(f"earnings in {days_to_earnings}d — inside the hold window")
    if not has_entry and score > CAP_NO_ENTRY:
        score = CAP_NO_ENTRY
        caps.append("extended — no honest entry price today")

    label = next(l for floor, l in CONFIDENCE_BANDS if score >= floor)
    return {
        "score": round(score, 1),
        "raw": round(raw, 1),
        "label": label,
        "technical": round(confluence, 1),
        "fundamental": round(fund, 1),
        "news": news,
        "news_verdict": news_verdict,
        "fundamental_note": fund_note,
        "caps_applied": caps,
    }


def scored_rows(snap: dict, direction: str, limit: int = 25) -> list[dict]:
    """Confidence-scored candidates for one side of the book, best first.

    Shared by the /leaders page and the pre-market confluence email so the two
    cannot disagree about what "the top bullish leader" means. Pure: the
    snapshot dict carries the confirmations and the stored news verdicts, so
    this reads no files.

    A ticker can appear in several groups — AVGO is in both Semiconductors and
    AI — and the highest-confluence instance wins, so the result lists names
    rather than memberships.
    """
    confirmations = snap.get("confirmations") or {}
    verdicts = snap.get("verdicts") or {}
    sectors = {s.get("name"): s for s in (snap.get("sectors") or [])}

    best = {}
    for c in snap.get("candidates") or []:
        if c.get("direction") != direction:
            continue
        t = c.get("ticker")
        prior = best.get(t)
        if not prior or ((c.get("confluence") or {}).get("score", 0) >
                         (prior.get("confluence") or {}).get("score", 0)):
            best[t] = c

    rows = []
    for t, c in best.items():
        conf = confirmations.get(t) or {}
        sc = conf.get("scores") or {}
        f = conf.get("fundamentals") or {}
        v = (verdicts.get(f"{t}:{direction}") or {}).get("verdict") or "unavailable"
        setup = c.get("setup") or {}

        c = dict(c)
        c["confidence"] = confidence_score(
            (c.get("confluence") or {}).get("score", 0),
            (sc.get("business_quality") or {}).get("score"),
            (sc.get("financial_health") or {}).get("score"),
            v, (conf.get("earnings") or {}).get("days_away"),
            bool(setup.get("levels")), direction,
            (c.get("metrics") or {}).get("close"), f.get("targetMeanPrice"))
        c["news_verdict"] = v
        sec = sectors.get(c.get("group")) or {}
        c["divergence"] = divergence_flag(
            sec.get("direction") or "neutral",
            "bullish" if direction == "long" else "bearish", 99, 1)
        rows.append(c)

    rows.sort(key=lambda r: -(r["confidence"]["score"]))
    return rows[:limit]
