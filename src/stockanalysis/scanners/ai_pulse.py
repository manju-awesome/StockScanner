"""
ai_pulse.py
===========
Fetching layer for the AI Sentiment dashboard — tiered AI infrastructure
leaders (Leadership / Networking / Power), the cross-macro inputs the Risk
Score needs (10Y yield, DXY, SOXX, NVDA VWAP), and relative-strength ratios
against SPY/QQQ/SMH. Same "never raises, None on failure" convention as
scanners/market_movers.py — this module only fetches and lightly shapes data;
all scoring/interpretation lives in core/ai_sentiment.py.

Usage
-----
    from stockanalysis.scanners.ai_pulse import fetch_ai_pulse
    pulse_extra = fetch_ai_pulse()
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import yfinance as yf

from stockanalysis.core.metrics import calculate_rsi

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ── Ticker groups ─────────────────────────────────────────────────────────────
TIER1 = ["NVDA", "AVGO", "MSFT", "TSM"]          # Leadership
TIER2 = ["GLW", "AAOI", "COHR", "LITE"]          # Networking / Optics
TIER3 = ["BE", "VRT", "GEV"]                     # Power

AI_HEALTH_BASKET = ["NVDA", "AVGO", "TSM", "AMD", "GLW", "AAOI", "COHR", "VRT", "BE"]

RS_BENCHMARKS = ["SPY", "QQQ", "SMH"]

SOXX_TICKER = "SOXX"
TNX_TICKER  = "^TNX"      # 10Y yield × 10 (CBOE quoting convention)
DXY_TICKER  = "DX-Y.NYB"  # ICE US Dollar Index

ALL_AI_WATCHLIST = "AI"   # data/watchlists.json key — the full curated AI universe


def _load_all_ai_tickers() -> list[str]:
    """Every ticker in the "AI" watchlist (data/watchlists.json) — the
    all-tickers table on the AI Sentiment page, as opposed to the three
    hand-picked tiers above. Never raises."""
    try:
        from stockanalysis.reporting.research import load_watchlists
        tickers = load_watchlists().get(ALL_AI_WATCHLIST) or []
        return sorted({t.strip().upper() for t in tickers if t.strip()})
    except Exception as e:
        log.debug("AI watchlist load failed: %s", e)
        return []


# ── Ticker snapshot (price/trend/RSI) ─────────────────────────────────────────

def _ticker_snapshot(ticker: str) -> dict | None:
    """Price, day change, MA/EMA position, and RSI-14 from one daily-history
    fetch. Modeled on market_movers._trend_snapshot, extended with 20 EMA and
    RSI for the AI Health Index / tier tables."""
    try:
        hist = yf.Ticker(ticker).history(period="4mo", interval="1d", auto_adjust=False)
        closes = hist["Close"].ffill().dropna() if hist is not None and not hist.empty else None
        if closes is None or len(closes) < 21:
            return None
        price = float(closes.iloc[-1])
        prev  = float(closes.iloc[-2])
        rsi_series = calculate_rsi(closes).dropna()
        rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else None
        ma20  = float(closes.rolling(20).mean().iloc[-1])
        ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
        return {
            "ticker":       ticker,
            "price":        round(price, 2),
            "day_chg_pct":  round((price / prev - 1) * 100, 2) if prev else None,
            "above_20ma":   price > ma20,
            "above_20ema":  price > ema20,
            "above_50ma":   (price > float(closes.rolling(50).mean().iloc[-1])
                              if len(closes) >= 50 else None),
            "rsi_14":       round(rsi, 1) if rsi is not None else None,
        }
    except Exception as e:
        log.debug("ai_pulse snapshot failed for %s: %s", ticker, e)
        return None


def _snapshot_group(tickers: list[str]) -> list[dict]:
    return [s for s in (_ticker_snapshot(t) for t in tickers) if s]


# ── Supply-chain basket: one batch download ──────────────────────────────────

def _bar_snapshot(closes, volumes, ticker: str) -> dict | None:
    """Rich per-ticker snapshot from daily bars: returns over 1/5/20d, EMA
    posture (20/50/200), RSI, 20-day-high flag, volume vs 20d average —
    everything compute_breadth/compute_momentum need, no extra fetches."""
    closes = closes.ffill().dropna()
    if len(closes) < 21:
        return None
    price = float(closes.iloc[-1])

    def _chg(n: int) -> float | None:
        if len(closes) <= n:
            return None
        base = float(closes.iloc[-1 - n])
        return round((price / base - 1) * 100, 2) if base else None

    def _above_ema(span: int) -> bool | None:
        if len(closes) < span:
            return None
        return price > float(closes.ewm(span=span, adjust=False).mean().iloc[-1])

    rsi_series = calculate_rsi(closes).dropna()
    vol_ratio = None
    if volumes is not None:
        v = volumes.dropna()
        if len(v) >= 21 and float(v.iloc[-21:-1].mean()) > 0:
            vol_ratio = round(float(v.iloc[-1]) / float(v.iloc[-21:-1].mean()), 2)

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "day_chg_pct": _chg(1),
        "chg_5d_pct": _chg(5),
        "chg_20d_pct": _chg(20),
        "above_20ema": _above_ema(20),
        "above_50ema": _above_ema(50),
        "above_200ema": _above_ema(200),
        "rsi_14": round(float(rsi_series.iloc[-1]), 1) if not rsi_series.empty else None,
        "high_20d": price >= float(closes.iloc[-20:].max()),
        "vol_ratio": vol_ratio,
    }


def fetch_basket_history() -> dict:
    """Basket + leading indicators + benchmarks in ONE yf.download call —
    ~30 symbols of daily bars costs one request instead of thirty, which is
    what keeps the Yahoo throttling that plagued per-ticker fetches away.
    Returns {"basket_v2": [...], "leading": [...], "benchmarks": {sym: snap}}
    — empty shapes on total failure, never raises."""
    from stockanalysis.core.ai_sentiment import AI_BASKET_TICKERS, AI_LEADING_TICKERS
    benchmarks = ["QQQ", "SPY"]
    symbols = sorted(set(AI_BASKET_TICKERS + AI_LEADING_TICKERS + benchmarks))
    try:
        df = yf.download(symbols, period="1y", interval="1d",
                         progress=False, auto_adjust=True, group_by="column")
        closes, vols = df["Close"], df.get("Volume")
    except Exception as e:
        log.warning("basket batch download failed: %s", e)
        return {"basket_v2": [], "leading": [], "benchmarks": {}}

    def _snap(sym: str) -> dict | None:
        try:
            return _bar_snapshot(closes[sym], vols[sym] if vols is not None else None, sym)
        except Exception as e:
            log.debug("basket snapshot failed for %s: %s", sym, e)
            return None

    return {
        "basket_v2": [s for s in (_snap(t) for t in AI_BASKET_TICKERS) if s],
        "leading":   [s for s in (_snap(t) for t in AI_LEADING_TICKERS) if s],
        "benchmarks": {b: s for b in benchmarks if (s := _snap(b))},
    }


def fetch_basket_earnings_days() -> dict:
    """days-to-earnings per basket name via the light t.calendar endpoint —
    feeds the news/earnings uncertainty component. Individually best-effort:
    a failed name is simply absent."""
    from stockanalysis.core.ai_sentiment import AI_BASKET_TICKERS
    out = {}
    today = datetime.now(ET).date()
    for t in AI_BASKET_TICKERS:
        try:
            cal = yf.Ticker(t).calendar or {}
            dates = cal.get("Earnings Date") or []
            future = sorted(d for d in dates if d >= today)
            if future:
                out[t] = (future[0] - today).days
        except Exception as e:
            log.debug("earnings calendar failed for %s: %s", t, e)
    return out


# ── NVDA vs VWAP ──────────────────────────────────────────────────────────────

def _nvda_vwap_flag(ticker: str = "NVDA") -> bool | None:
    """True if last price is above today's session VWAP, None if intraday
    bars aren't available (market closed / no data) — never raises."""
    try:
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:
            return None
        df = yf.Ticker(ticker).history(period="1d", interval="1m", prepost=False)
        if df is None or df.empty:
            return None
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(ET)
        else:
            df.index = df.index.tz_convert(ET)
        session = df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))]
        session = session.dropna(subset=["Close", "Volume"])
        if session.empty:
            return None
        typical = (session["High"] + session["Low"] + session["Close"]) / 3
        vwap = float((typical * session["Volume"]).sum() / session["Volume"].sum())
        last_price = float(session["Close"].iloc[-1])
        return last_price > vwap
    except Exception as e:
        log.debug("NVDA VWAP check failed: %s", e)
        return None


# ── Macro inputs: 10Y yield, DXY, SOXX ────────────────────────────────────────

def _yield_change_bps() -> dict:
    """10Y Treasury yield change vs prior close, in basis points.
    yfinance's ^TNX feed returns the yield directly in percent (e.g. 4.569
    for a 4.569% yield, verified against live data) rather than the classic
    CBOE ticker-tape convention of yield×10 — so a 0.01 point move in the
    series = 1 bp."""
    try:
        closes = yf.Ticker(TNX_TICKER).history(period="5d")["Close"].dropna()
        if len(closes) < 2:
            return {"level_pct": None, "change_bps": None, "error": "no ^TNX data"}
        level = float(closes.iloc[-1])
        prev  = float(closes.iloc[-2])
        return {
            "level_pct":  round(level, 3),
            "change_bps": round((level - prev) * 100),
            "error":      None,
        }
    except Exception as e:
        log.debug("10Y yield fetch failed: %s", e)
        return {"level_pct": None, "change_bps": None, "error": str(e)}


def _dxy_change_pct() -> dict:
    """Dollar Index (DXY) % change vs prior close."""
    try:
        closes = yf.Ticker(DXY_TICKER).history(period="5d")["Close"].dropna()
        if len(closes) < 2:
            return {"level": None, "change_pct": None, "error": "no DXY data"}
        level = float(closes.iloc[-1])
        prev  = float(closes.iloc[-2])
        return {
            "level":      round(level, 2),
            "change_pct": round((level / prev - 1) * 100, 2) if prev else None,
            "error":      None,
        }
    except Exception as e:
        log.debug("DXY fetch failed: %s", e)
        return {"level": None, "change_pct": None, "error": str(e)}


# ── Relative strength vs SPY/QQQ/SMH ──────────────────────────────────────────

def relative_strength(tickers: list[str], benchmarks: list[str] = RS_BENCHMARKS) -> dict:
    """
    For each ticker × benchmark pair: ratio(close_now) vs ratio(close 5
    sessions ago). Rising ratio = the ticker is outperforming that benchmark
    even if its own price is pulling back; falling = underperforming.
    Returns {ticker: {benchmark: {"ratio": float, "chg_pct": float,
    "trend": "rising"|"falling"|"flat"} | None}}.
    """
    closes_cache: dict[str, "pd.Series"] = {}

    def _closes(t: str):
        if t in closes_cache:
            return closes_cache[t]
        try:
            h = yf.Ticker(t).history(period="1mo", interval="1d", auto_adjust=True)
            c = h["Close"].dropna() if h is not None and not h.empty else None
        except Exception as e:
            log.debug("relative_strength fetch failed for %s: %s", t, e)
            c = None
        closes_cache[t] = c
        return c

    result: dict[str, dict] = {}
    for ticker in tickers:
        t_closes = _closes(ticker)
        result[ticker] = {}
        for bench in benchmarks:
            b_closes = _closes(bench)
            if (t_closes is None or b_closes is None
                    or len(t_closes) < 6 or len(b_closes) < 6):
                result[ticker][bench] = None
                continue
            ratio_now = float(t_closes.iloc[-1]) / float(b_closes.iloc[-1])
            ratio_5d  = float(t_closes.iloc[-6]) / float(b_closes.iloc[-6])
            chg_pct = (ratio_now / ratio_5d - 1) * 100 if ratio_5d else None
            trend = "flat"
            if chg_pct is not None:
                trend = "rising" if chg_pct > 0.5 else ("falling" if chg_pct < -0.5 else "flat")
            result[ticker][bench] = {
                "ratio": round(ratio_now, 4),
                "chg_pct": round(chg_pct, 2) if chg_pct is not None else None,
                "trend": trend,
            }
    return result


# ── Orchestrator ──────────────────────────────────────────────────────────────

def fetch_ai_pulse() -> dict:
    """
    Full fetch for the AI Sentiment page. Never raises — each section fails
    independently (missing pieces come back None/empty and the scoring layer
    in core/ai_sentiment.py degrades gracefully).
    """
    tier1 = _snapshot_group(TIER1)
    tier2 = _snapshot_group(TIER2)
    tier3 = _snapshot_group(TIER3)
    basket = _snapshot_group(AI_HEALTH_BASKET)
    soxx = _ticker_snapshot(SOXX_TICKER)
    all_ai = _snapshot_group(_load_all_ai_tickers())

    basket_history = fetch_basket_history()

    return {
        "asof": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET"),
        "tier1": tier1, "tier2": tier2, "tier3": tier3,
        "basket": basket,
        "all_ai": all_ai,
        "soxx": soxx,
        "yield": _yield_change_bps(),
        "dxy": _dxy_change_pct(),
        "nvda_above_vwap": _nvda_vwap_flag(),
        "relative_strength": relative_strength(TIER2 + TIER3),
        **basket_history,                      # basket_v2 / leading / benchmarks
        "earnings_days": fetch_basket_earnings_days(),
    }
