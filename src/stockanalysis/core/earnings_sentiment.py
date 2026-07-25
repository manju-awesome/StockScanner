"""
earnings_sentiment.py
======================
Deterministic earnings-reaction sentiment engine for a single ticker —
predicts the likely post-earnings market reaction (Bullish/Bearish
probability split, expected move, confidence, risk level, trading bias)
from a weighted scoring model over several factor categories. No LLM call:
every input is a number computed from yfinance data, and the weighting is a
fixed formula, so a re-run on the same day's data reproduces the same score.

Scope — what this covers vs. a "full" institutional framework
---------------------------------------------------------------
This project's only external data dependency is yfinance (see
requirements.txt). Several inputs a full earnings-sentiment framework would
want — options gamma exposure, dark-pool/institutional flow, IV rank
computed against a real historical IV series, Reddit/X social sentiment,
earnings-call transcript tone — need paid data feeds yfinance doesn't
provide. Rather than fake those, analyze() reports them in "data_gaps" and
scores only what it can actually compute:

  Category            Weight   Source
  -------------------- ------  --------------------------------------------
  Previous earnings     25%    yfinance earnings_dates (beat/miss streak,
                                avg post-earnings 1-day move, gap frequency)
  Options activity       20%   yfinance option_chain (ATM straddle implied
                                move, IV, put/call open-interest ratio)
  Technical trend        15%   core.metrics.get_metrics() (RSI, EMAs, ADX,
                                trend strength)
  Market trend            10%   scanners.market_movers.market_pulse() (SPY/
                                QQQ strength)
  VIX                     10%   market_pulse() vix level
  Fed / macro              10%   market_pulse() fed-funds-futures outlook
  Treasury yield curve      5%   ^TNX (10Y) vs ^FVX (5Y) — yfinance has no
                                direct 2Y index ticker, so this is a 5s10s
                                slope proxy for the textbook 2s10s
  News sentiment            5%   yfinance ticker.news headlines, scored by a
                                small bullish/bearish keyword lexicon (not
                                real NLP — see data_gaps)

Weights sum to 100, matching the -100..+100 total_score range the trading
bias / probability mapping below assumes.

Score → probability is a simple linear heuristic (bullish_probability =
50 + total_score/2, clamped 5-95), not a calibrated statistical model — this
codebase has no historical backtest of this scoring formula's accuracy yet.
Treat outputs as a structured second opinion, not a guarantee.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

log = logging.getLogger("earnings_sentiment")

TNX_TICKER = "^TNX"   # 10Y yield, quoted directly in percent by yfinance
FVX_TICKER = "^FVX"   # 5Y yield — closest free proxy to 2Y (no ^UST2Y on Yahoo)

_POS_WORDS = (
    "beat", "beats", "surge", "soar", "rally", "upgrade", "raises", "raise",
    "record", "strong", "outperform", "buyback", "expands", "expansion",
    "wins", "win", "partnership", "approval", "approved", "breakthrough",
    "accelerat", "tops estimates", "above estimates", "bullish",
)
_NEG_WORDS = (
    "miss", "misses", "plunge", "slump", "downgrade", "cuts", "cut",
    "lawsuit", "probe", "investigat", "recall", "layoff", "layoffs",
    "warns", "warning", "weak", "underperform", "delay", "delayed",
    "below estimates", "short seller", "bearish", "resigns", "resignation",
    "fraud", "bankrupt",
)


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHERS — each never raises; failures come back as a dict with
# populated "error" and everything else None, so a bad ticker or a flaky
# network call degrades one factor's score to 0 instead of aborting the run.
# ─────────────────────────────────────────────────────────────────────────────

def fetch_earnings_history(ticker: str, lookback: int = 8) -> dict:
    """Beat/miss streak + post-earnings price reaction from yfinance's
    earnings_dates (EPS estimate/actual/surprise%, several years deep).
    Requires the optional 'lxml' dependency yfinance uses to fetch this."""
    import yfinance as yf
    import pandas as pd

    out = {
        "next_earnings_date": None, "days_to_earnings": None,
        "consensus_eps": None,
        "history": [], "beat_count": 0, "miss_count": 0, "beat_rate": None,
        "streak": 0, "avg_move_pct": None, "avg_abs_move_pct": None,
        "gap_up_freq": None, "gap_down_freq": None, "error": None,
    }
    try:
        ed = yf.Ticker(ticker).earnings_dates
        if ed is None or ed.empty:
            out["error"] = "no earnings_dates data"
            return out
        ed = ed.sort_index(ascending=False)

        upcoming = ed[ed["Reported EPS"].isna()]
        if not upcoming.empty:
            next_dt = upcoming.index[0]
            out["next_earnings_date"] = str(next_dt.date())
            days = (next_dt.tz_localize(None) - datetime.now()).days
            out["days_to_earnings"] = max(days, 0)
            out["consensus_eps"] = _num(upcoming.iloc[0].get("EPS Estimate"))

        past = ed[ed["Reported EPS"].notna()].head(lookback)
        if past.empty:
            out["error"] = "no reported quarters yet"
            return out

        start = (past.index.min() - timedelta(days=7)).tz_localize(None)
        end = (past.index.max() + timedelta(days=7)).tz_localize(None)
        prices = yf.Ticker(ticker).history(start=start, end=end)["Close"]
        prices.index = prices.index.tz_localize(None)

        rows = []
        for dt, r in past.iterrows():
            day = dt.tz_localize(None).normalize()
            before = prices[prices.index < day]
            after = prices[prices.index > day]
            move_pct = None
            if len(before) and len(after):
                pre, post = float(before.iloc[-1]), float(after.iloc[0])
                move_pct = round((post / pre - 1) * 100, 2) if pre else None
            surprise_pct = _num(r.get("Surprise(%)"))
            rows.append({
                "date": str(day.date()),
                "eps_estimate": _num(r.get("EPS Estimate")),
                "eps_reported": _num(r.get("Reported EPS")),
                "surprise_pct": surprise_pct,
                "beat": bool(surprise_pct is not None and surprise_pct > 0),
                "move_pct": move_pct,
            })
        out["history"] = rows

        beats = [r for r in rows if r["beat"]]
        misses = [r for r in rows if r["surprise_pct"] is not None and not r["beat"]]
        out["beat_count"], out["miss_count"] = len(beats), len(misses)
        scored = len(beats) + len(misses)
        out["beat_rate"] = round(len(beats) / scored * 100, 1) if scored else None

        streak = 0
        for r in rows:  # rows are newest-first
            if r["surprise_pct"] is None:
                break
            if r["beat"]:
                if streak >= 0:
                    streak += 1
                else:
                    break
            else:
                if streak <= 0:
                    streak -= 1
                else:
                    break
        out["streak"] = streak

        moves = [r["move_pct"] for r in rows if r["move_pct"] is not None]
        if moves:
            out["avg_move_pct"] = round(sum(moves) / len(moves), 2)
            out["avg_abs_move_pct"] = round(sum(abs(m) for m in moves) / len(moves), 2)
            out["gap_up_freq"] = round(sum(1 for m in moves if m > 0) / len(moves) * 100, 1)
            out["gap_down_freq"] = round(sum(1 for m in moves if m < 0) / len(moves) * 100, 1)
        return out
    except ImportError as e:
        out["error"] = f"missing dependency: {e}"
        return out
    except Exception as e:
        log.debug("%s: earnings history fetch failed: %s", ticker, e)
        out["error"] = str(e)
        return out


def fetch_options_snapshot(ticker: str, price: float | None,
                           next_earnings_date: str | None = None) -> dict:
    """ATM straddle-implied move, IV, and put/call open-interest ratio from
    the nearest listed expiration on/after the next earnings date (falls
    back to the nearest expiration overall if no earnings date is known)."""
    import yfinance as yf

    out = {
        "expiration": None, "atm_strike": None, "call_iv": None,
        "put_iv": None, "avg_iv": None, "straddle_price": None,
        "implied_move_pct": None, "call_oi": None, "put_oi": None,
        "put_call_oi_ratio": None, "total_volume": None, "error": None,
    }
    if not price:
        out["error"] = "no current price"
        return out
    try:
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            out["error"] = "no listed options"
            return out
        expiration = exps[0]
        if next_earnings_date:
            after = [e for e in exps if e >= next_earnings_date]
            if after:
                expiration = after[0]
        out["expiration"] = expiration

        chain = t.option_chain(expiration)
        calls, puts = chain.calls, chain.puts
        if calls.empty or puts.empty:
            out["error"] = "empty option chain"
            return out

        calls = calls.assign(dist=(calls["strike"] - price).abs())
        puts = puts.assign(dist=(puts["strike"] - price).abs())
        atm_call = calls.sort_values("dist").iloc[0]
        atm_put = puts.sort_values("dist").iloc[0]
        out["atm_strike"] = float(atm_call["strike"])
        out["call_iv"] = round(float(atm_call["impliedVolatility"]) * 100, 1)
        out["put_iv"] = round(float(atm_put["impliedVolatility"]) * 100, 1)
        out["avg_iv"] = round((out["call_iv"] + out["put_iv"]) / 2, 1)

        call_mid = (float(atm_call["bid"]) + float(atm_call["ask"])) / 2
        put_mid = (float(atm_put["bid"]) + float(atm_put["ask"])) / 2
        if call_mid > 0 or put_mid > 0:
            straddle = call_mid + put_mid
            out["straddle_price"] = round(straddle, 2)
            out["implied_move_pct"] = round(straddle / price * 100, 2)

        call_oi = int(calls["openInterest"].fillna(0).sum())
        put_oi = int(puts["openInterest"].fillna(0).sum())
        out["call_oi"], out["put_oi"] = call_oi, put_oi
        out["put_call_oi_ratio"] = round(put_oi / call_oi, 2) if call_oi else None
        out["total_volume"] = int(calls["volume"].fillna(0).sum()
                                  + puts["volume"].fillna(0).sum())
        return out
    except Exception as e:
        log.debug("%s: options snapshot failed: %s", ticker, e)
        out["error"] = str(e)
        return out


def fetch_analyst_snapshot(ticker: str, lookback_days: int = 90) -> dict:
    """Rating-change count over the trailing window from yfinance's
    upgrades_downgrades feed (no structured price-target-change series is
    reliably populated across tickers, so this stays action-count only)."""
    import yfinance as yf

    out = {"upgrades": 0, "downgrades": 0, "net_actions": 0,
          "most_recent": None, "error": None}
    try:
        ud = yf.Ticker(ticker).upgrades_downgrades
        if ud is None or ud.empty:
            out["error"] = "no analyst action data"
            return out
        cutoff = datetime.now() - timedelta(days=lookback_days)
        idx = ud.index
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        recent = ud[idx >= cutoff]
        if recent.empty:
            out["error"] = f"no analyst actions in last {lookback_days}d"
            return out
        actions = recent["Action"].astype(str).str.lower()
        out["upgrades"] = int((actions == "up").sum())
        out["downgrades"] = int((actions == "down").sum())
        out["net_actions"] = out["upgrades"] - out["downgrades"]
        top = recent.iloc[0]
        out["most_recent"] = (f'{top.get("Firm", "?")}: {top.get("Action", "?")} '
                              f'({top.get("ToGrade", "?")})')
        return out
    except Exception as e:
        log.debug("%s: analyst snapshot failed: %s", ticker, e)
        out["error"] = str(e)
        return out


def fetch_valuation_extra(ticker: str) -> dict:
    """Fields core.metrics.get_metrics() doesn't already compute (EV/EBITDA,
    gross margin) — everything else valuation-related (Forward PE, PEG,
    revenue growth) comes from the scan row instead of a second fetch."""
    import yfinance as yf

    out = {"ev_ebitda": None, "gross_margin_pct": None, "error": None}
    try:
        info = yf.Ticker(ticker).info or {}
        ev = info.get("enterpriseToEbitda")
        gm = info.get("grossMargins")
        out["ev_ebitda"] = round(float(ev), 1) if ev is not None else None
        out["gross_margin_pct"] = round(float(gm) * 100, 1) if gm is not None else None
        return out
    except Exception as e:
        log.debug("%s: valuation extra fetch failed: %s", ticker, e)
        out["error"] = str(e)
        return out


def fetch_news_sentiment(ticker: str, limit: int = 10) -> dict:
    """Lightweight keyword-lexicon polarity over recent headlines — not real
    NLP (see module docstring's data_gaps note), just enough signal to avoid
    treating every earnings setup as sentiment-neutral."""
    from stockanalysis.reporting.research import _fetch_ticker_news

    out = {"score": 0.0, "positive_hits": 0, "negative_hits": 0,
          "headlines_scanned": 0, "error": None}
    try:
        items = _fetch_ticker_news(ticker, limit=limit)
        out["headlines_scanned"] = len(items)
        if not items:
            out["error"] = "no headlines available"
            return out
        pos = neg = 0
        for it in items:
            title = it.get("title", "").lower()
            pos += sum(1 for w in _POS_WORDS if w in title)
            neg += sum(1 for w in _NEG_WORDS if w in title)
        out["positive_hits"], out["negative_hits"] = pos, neg
        total = pos + neg
        out["score"] = round((pos - neg) / total, 2) if total else 0.0
        return out
    except Exception as e:
        log.debug("%s: news sentiment failed: %s", ticker, e)
        out["error"] = str(e)
        return out


def fetch_yield_curve() -> dict:
    """10Y level/change + a 5s10s slope proxy for the textbook 2s10s curve
    (yfinance has no direct 2Y Treasury index ticker)."""
    import yfinance as yf

    out = {"ten_year_pct": None, "ten_year_chg_bps": None,
          "five_year_pct": None, "slope_5s10s_bps": None, "error": None}
    try:
        tnx = yf.Ticker(TNX_TICKER).history(period="5d")["Close"].dropna()
        fvx = yf.Ticker(FVX_TICKER).history(period="5d")["Close"].dropna()
        if len(tnx) >= 2:
            level, prev = float(tnx.iloc[-1]), float(tnx.iloc[-2])
            out["ten_year_pct"] = round(level, 3)
            out["ten_year_chg_bps"] = round((level - prev) * 100)
        if len(fvx) and out["ten_year_pct"] is not None:
            out["five_year_pct"] = round(float(fvx.iloc[-1]), 3)
            out["slope_5s10s_bps"] = round(
                (out["ten_year_pct"] - out["five_year_pct"]) * 100)
        if out["ten_year_pct"] is None:
            out["error"] = "no ^TNX data"
        return out
    except Exception as e:
        log.debug("yield curve fetch failed: %s", e)
        out["error"] = str(e)
        return out


def _num(v):
    try:
        f = float(v)
        return f if f == f else None   # NaN check
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SCORING — each _score_* returns (total_points, [(sub_points, reason), ...]).
# Reasons carry their OWN point contribution rather than inheriting the
# category's overall sign: a category can net positive while one of its
# reasons is individually bearish (e.g. a 100% beat rate but a negative
# average post-earnings move), and that reason must land in the bearish
# list, not get swept into bullish because the category total was positive.
# total_points are capped to the category's weight so the eight categories
# sum to at most ±100, matching the band thresholds in _classify_total().
# ─────────────────────────────────────────────────────────────────────────────

def _score_previous_earnings(hist: dict) -> tuple[int, list[tuple[int, str]]]:
    if hist.get("error") and not hist.get("history"):
        return 0, []
    pts, reasons = 0, []
    rate = hist.get("beat_rate")
    if rate is not None:
        if rate >= 75:
            r = 10; reasons.append((r, f"beat estimates in {rate:.0f}% of the last "
                                    f"{hist['beat_count'] + hist['miss_count']} quarters"))
        elif rate >= 50:
            r = 4
        elif rate >= 25:
            r = -4
        else:
            r = -10; reasons.append((r, f"missed estimates in {100-rate:.0f}% of recent quarters"))
        pts += r
    streak = hist.get("streak") or 0
    if streak >= 2:
        r = min(streak, 5); pts += r; reasons.append((r, f"{streak}-quarter beat streak"))
    elif streak <= -2:
        r = -min(-streak, 5); pts += r; reasons.append((r, f"{-streak}-quarter miss streak"))
    avg_move = hist.get("avg_move_pct")
    if avg_move is not None:
        if avg_move > 1:
            r = 5; pts += r; reasons.append((r, f"avg post-earnings move +{avg_move:.1f}%"))
        elif avg_move < -1:
            r = -5; pts += r; reasons.append((r, f"avg post-earnings move {avg_move:.1f}%"))
    gap_up, gap_down = hist.get("gap_up_freq"), hist.get("gap_down_freq")
    if gap_up is not None and gap_down is not None:
        if gap_up - gap_down >= 25:
            r = 5; pts += r; reasons.append((r, f"gaps up more often than down ({gap_up:.0f}% vs {gap_down:.0f}%)"))
        elif gap_down - gap_up >= 25:
            r = -5; pts += r; reasons.append((r, f"gaps down more often than up ({gap_down:.0f}% vs {gap_up:.0f}%)"))
    return max(-25, min(25, pts)), reasons


def _score_options(opt: dict) -> tuple[int, list[tuple[int, str]]]:
    if opt.get("error"):
        return 0, []
    pts, reasons = 0, []
    ratio = opt.get("put_call_oi_ratio")
    if ratio is not None:
        if ratio < 0.7:
            r = 10; pts += r; reasons.append((r, f"call-heavy open interest (put/call {ratio:.2f})"))
        elif ratio > 1.3:
            r = -10; pts += r; reasons.append((r, f"put-heavy open interest (put/call {ratio:.2f})"))
    move = opt.get("implied_move_pct")
    if move is not None and move <= 3:
        r = 4; pts += r; reasons.append((r, f"options pricing a modest ±{move:.1f}% move"))
    return max(-20, min(20, pts)), reasons


def _score_technical(row: dict) -> tuple[int, list[tuple[int, str]]]:
    if not row:
        return 0, []
    pts, reasons = 0, []
    if row.get("Above_200MA") is True:
        r = 4; pts += r; reasons.append((r, "price above 200-day MA"))
    elif row.get("Above_200MA") is False:
        r = -4; pts += r; reasons.append((r, "price below 200-day MA"))
    p50 = row.get("Price_vs_50MA%")
    if p50 is not None:
        pts += 3 if p50 > 0 else -3
    rsi = row.get("RSI_14")
    if rsi is not None:
        if rsi >= 70:
            r = -2; pts += r; reasons.append((r, f"RSI {rsi:.0f} (overbought)"))
        elif rsi <= 30:
            r = 2; pts += r; reasons.append((r, f"RSI {rsi:.0f} (oversold)"))
    trend = row.get("Trend_Strength")
    if trend == "Strong":
        r = 3 if (row.get("RS") or 0) >= 0 else -3
        pts += r
    return max(-15, min(15, pts)), reasons


def _score_market(pulse: dict) -> tuple[int, list[tuple[int, str]]]:
    pts, reasons = 0, []
    for name in ("spy", "qqq"):
        idx = (pulse or {}).get(name) or {}
        strength = idx.get("strength")
        if strength == "STRONG":
            r = 5; pts += r; reasons.append((r, f"{name.upper()} trend STRONG"))
        elif strength == "WEAK":
            r = -5; pts += r; reasons.append((r, f"{name.upper()} trend WEAK"))
    return max(-10, min(10, pts)), reasons


def _score_vix(pulse: dict) -> tuple[int, list[tuple[int, str]]]:
    vix = ((pulse or {}).get("vix") or {}).get("level")
    if vix is None:
        return 0, []
    if vix < 17:
        return 6, [(6, f"VIX {vix:.1f} (calm)")]
    if vix < 20:
        return 3, []
    if vix >= 28:
        return -10, [(-10, f"VIX {vix:.1f} (elevated fear)")]
    if vix >= 20:
        return -5, [(-5, f"VIX {vix:.1f} (elevated)")]
    return 0, []


def _score_fed(pulse: dict) -> tuple[int, list[tuple[int, str]]]:
    fed = (pulse or {}).get("fed") or {}
    months = fed.get("months") or []
    if not months:
        return 0, []
    bps = months[0].get("change_bps")
    if bps is None:
        return 0, []
    if bps >= 15:
        return -8, [(-8, f"Fed pricing ~{bps}bp of hikes next meeting (hawkish)")]
    if bps >= 5:
        return -3, []
    if bps <= -15:
        return 8, [(8, f"Fed pricing ~{-bps}bp of cuts next meeting (dovish)")]
    if bps <= -5:
        return 3, []
    return 0, []


def _score_treasury(yc: dict) -> tuple[int, list[tuple[int, str]]]:
    bps = (yc or {}).get("ten_year_chg_bps")
    if bps is None:
        return 0, []
    if bps >= 8:
        return -3, [(-3, "10Y yield rising fast (growth-stock headwind)")]
    if bps <= -8:
        return 3, [(3, "10Y yield falling fast (growth-stock tailwind)")]
    return 0, []


def _score_sentiment(news: dict) -> tuple[int, list[tuple[int, str]]]:
    score = (news or {}).get("score") or 0.0
    pts = round(score * 5)
    reasons = []
    if pts >= 3:
        reasons.append((pts, "recent headlines skew positive"))
    elif pts <= -3:
        reasons.append((pts, "recent headlines skew negative"))
    return max(-5, min(5, pts)), reasons


def _classify_total(score: int) -> tuple[str, str]:
    """(market_regime_style_label, trading_bias) per the spec's bands."""
    if score >= 70:
        return "Strong Bullish", "Strong Buy"
    if score >= 40:
        return "Bullish", "Buy"
    if score >= 20:
        return "Slight Bullish", "Buy"
    if score > -20:
        return "Neutral", "Neutral"
    if score > -40:
        return "Bearish", "Sell"
    return "Strong Bearish", "Strong Sell"


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

DATA_GAPS = [
    "IV rank/percentile — needs a tracked historical IV series (only a live "
    "snapshot is available)",
    "gamma exposure — needs options-dealer positioning data, not in yfinance",
    "dark pool / off-exchange institutional flow — needs a paid flow feed",
    "Reddit/X social sentiment — needs Reddit/X API access",
    "earnings call tone — needs a transcript provider",
    "2Y Treasury yield — no direct ^UST2Y ticker on Yahoo; using a 5Y (^FVX) "
    "proxy for the yield-curve slope instead of the textbook 2s10s",
]


def analyze_earnings_sentiment(ticker: str) -> dict:
    """Run the full deterministic pipeline for one ticker. Never raises —
    a missing sub-fetch just zeroes that factor's score rather than
    aborting the whole analysis."""
    from stockanalysis.core.metrics import get_metrics
    from stockanalysis.scanners.scan_universe import fetch_qqq_return
    from stockanalysis.scanners.market_movers import market_pulse

    ticker = ticker.upper().strip()

    hist = fetch_earnings_history(ticker)
    row = {}
    try:
        row = get_metrics(ticker, fetch_qqq_return())
    except Exception as e:
        log.debug("%s: get_metrics failed: %s", ticker, e)
    price = row.get("Current Price")

    opt = fetch_options_snapshot(ticker, price, hist.get("next_earnings_date"))
    analyst = fetch_analyst_snapshot(ticker)
    valuation = fetch_valuation_extra(ticker)
    news = fetch_news_sentiment(ticker)
    yc = fetch_yield_curve()
    try:
        pulse = market_pulse(top_n=5, with_catalysts=False)
    except Exception as e:
        log.debug("market_pulse failed: %s", e)
        pulse = {}

    earn_pts, earn_r = _score_previous_earnings(hist)
    opt_pts, opt_r = _score_options(opt)
    tech_pts, tech_r = _score_technical(row)
    mkt_pts, mkt_r = _score_market(pulse)
    vix_pts, vix_r = _score_vix(pulse)
    fed_pts, fed_r = _score_fed(pulse)
    trea_pts, trea_r = _score_treasury(yc)
    sent_pts, sent_r = _score_sentiment(news)

    factor_scores = {
        "previous_earnings": earn_pts, "options": opt_pts,
        "technical": tech_pts, "market": mkt_pts, "vix": vix_pts,
        "fed_macro": fed_pts, "treasury": trea_pts, "sentiment": sent_pts,
    }
    total_score = sum(factor_scores.values())
    regime_label, trading_bias = _classify_total(total_score)

    bullish_probability = max(5, min(95, round(50 + total_score / 2)))
    bearish_probability = 100 - bullish_probability

    expected_move_pct, expected_move_source = opt.get("implied_move_pct"), "options_straddle"
    if expected_move_pct is None:
        expected_move_pct = hist.get("avg_abs_move_pct")
        expected_move_source = "historical_avg" if expected_move_pct is not None else None

    if expected_move_pct is None:
        earnings_risk = "Unknown"
    elif expected_move_pct < 4:
        earnings_risk = "Low"
    elif expected_move_pct < 8:
        earnings_risk = "Medium"
    else:
        earnings_risk = "High"

    factors_with_data = sum(1 for f in (hist, opt, row, pulse, yc, news) if f and not f.get("error"))
    completeness = factors_with_data / 6
    nonzero = [p for p in factor_scores.values() if p != 0]
    if nonzero and total_score != 0:
        agreement = sum(1 for p in nonzero if (p > 0) == (total_score > 0)) / len(nonzero)
    else:
        agreement = 0.5
    confidence = round(10 * completeness * (0.5 + 0.5 * agreement), 1)

    # Each reason carries its own point contribution (set inside the _score_*
    # functions above) — bucket by that, not by the category's overall sign,
    # so e.g. a negative avg-move reason lands in bearish even when the
    # previous-earnings category nets positive overall (see module note).
    all_reasons = [r for rs in (earn_r, opt_r, tech_r, mkt_r, vix_r, fed_r,
                                trea_r, sent_r) for r in rs]
    bullish_reasons = [text for pts, text in
                       sorted((r for r in all_reasons if r[0] > 0), key=lambda r: -r[0])][:6]
    bearish_reasons = [text for pts, text in
                       sorted((r for r in all_reasons if r[0] < 0), key=lambda r: r[0])][:6]

    risk_factors = []
    if expected_move_pct is not None and expected_move_pct >= 8:
        risk_factors.append(f"large expected move (±{expected_move_pct:.1f}%)")
    if (opt.get("avg_iv") or 0) >= 70:
        risk_factors.append(f"very high implied volatility ({opt['avg_iv']:.0f}%)")
    if ((pulse.get("vix") or {}).get("level") or 0) >= 25:
        risk_factors.append("elevated VIX — macro tape adds noise to the reaction")
    if hist.get("error") and not hist.get("history"):
        risk_factors.append("no earnings history available — limited pattern data")
    if opt.get("error"):
        risk_factors.append("no options data available — expected move is a historical estimate")

    return {
        "ticker": ticker,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "next_earnings_date": hist.get("next_earnings_date"),
        "days_to_earnings": hist.get("days_to_earnings"),
        "bullish_probability": bullish_probability,
        "bearish_probability": bearish_probability,
        "confidence": confidence,
        "expected_move_pct": expected_move_pct,
        "expected_move_source": expected_move_source,
        "market_regime": regime_label,
        "earnings_risk": earnings_risk,
        "trading_bias": trading_bias,
        "total_score": total_score,
        "factor_scores": factor_scores,
        "key_bullish_reasons": bullish_reasons,
        "key_bearish_reasons": bearish_reasons,
        "risk_factors": risk_factors,
        "sections": {
            "earnings_history": hist, "options": opt, "analyst": analyst,
            "valuation": valuation, "sentiment": news, "yield_curve": yc,
            "technical": {k: row.get(k) for k in (
                "RSI_14", "ADX_14", "Trend_Strength", "Above_200MA",
                "Price_vs_50MA%", "Price_vs_200MA%", "ATR_Pct", "RS")},
            "market_environment": {
                "vix": (pulse.get("vix") or {}).get("level"),
                "spy_strength": (pulse.get("spy") or {}).get("strength"),
                "qqq_strength": (pulse.get("qqq") or {}).get("strength"),
                "fed_next_month_bps": ((pulse.get("fed") or {}).get("months") or [{}])[0].get("change_bps")
                                      if (pulse.get("fed") or {}).get("months") else None,
            },
        },
        "data_gaps": DATA_GAPS,
    }
