"""
scanners/rosputnia_scan.py

Swing-trade scanner inspired by publicly discussed elements of Inna Rosputnia's
trading commentary (18-day EMA trend filter, double-bottom reversal pattern,
disciplined risk-based trade management). This is NOT a reproduction of her
proprietary system -- that isn't publicly documented -- it's a rule-based
scanner built from the concepts she has discussed in interviews/MoneyShow
sessions, adapted to fit the existing StockAnalysis scanner pattern
(hard disqualifiers -> pattern detection -> scoring -> entry/stop/target).

Drop into: src/stockanalysis/scanners/rosputnia_scan.py

Adjust the two imports below to match your actual module paths:
    from stockanalysis.core.universe import SP500_TICKERS
    from stockanalysis.core.data import get_price_history
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

# --------------------------------------------------------------------------
# Universe import -- adjust to your actual path. Fallback included so this
# file runs standalone for testing.
# --------------------------------------------------------------------------
'''
try:
    from stockanalysis.core.universe import SP500_TICKERS as UNIVERSE
except ImportError:
    UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMD", "AVGO", "MU", "ARM"]
'''
UNIVERSE1 = ["META","GLW","MRVL", "HOOD","PLTR","MSFT", "NVDA", "AMD", "AVGO", "MU", "ARM"]
UNIVERSE= [
    # ── Chips / Semiconductors ────────────────────────────────────────────────
    "GOOGL", "AMD", "AVGO", "ASML",
    "TSM", "ARM", "INTC", "TXN",
    "DELL", "SMCI", "MRVL", "QCOM",
    #"KLAC", "LRCX", "AMAT",          # wafer fab equipment — AI capex beneficiaries

    # ── Memory / Storage ──────────────────────────────────────────────────────
    "MU", "WDC", "STX", "CIEN", "DRAM",

    # ── AI Infrastructure / Data Center ───────────────────────────────────────
    "VRT", "APH", "CRDO", "INOD", "IREN", "CRWV", "NBIS",
    "NFLX",                           # AI content + data center spend story

    # ── Photonics / Networking ────────────────────────────────────────────────
    "COHR", "LITE", "GLW", "AAOI",
     #"VIAV",                           # optical test & measurement, fiber buildout

    # ── Cloud / Software / Applications ───────────────────────────────────────
    "PLTR", "NOW", "CRM", "MSFT", "GOOGL", "META", "AMZN",
    "IGV", "ADBE", "WDAY", "ORCL",          # enterprise software with AI integration
    "SNOW", "MDB",                    # data platforms riding AI wave

    # ── Cybersecurity ─────────────────────────────────────────────────────────
    "PANW", "CRWD", "ZS", "FTNT",    # core cyber — all strong momentum names
    "S",                              # SentinelOne — AI-native security
    "OKTA",                           # identity security recovery play

    # ── Defense / Aerospace ───────────────────────────────────────────────────
    "LMT", "RTX", "NOC", "GD",       # prime contractors — $900B+ defense budget
    "GE",                             # GE Aerospace — jet engines, huge backlog
    "KTOS",                   # "AVAV", drone plays — high beta defense
    "RKLB",                           # Rocket Lab — space + defense
    #"HII",                            # nuclear submarines
    #"TDG",                            # TransDigm — aftermarket aerospace parts
    #"LHX",                            # L3Harris — defense electronics

    # ── Energy / Power (AI demand + nuclear renaissance) ──────────────────────
    "BE",  # Bloom Energy — fuel cells, strong momentum

    #"VST", "CEG", "GEV",             # power generation for AI data centers

    #"OKLO", "NNE", "SMR",            # small modular reactors — speculative
    #"FSLR",                           # First Solar — IRA beneficiary
    #"NEE",                            # NextEra — largest US utility + renewables
    #"ETN",                            # Eaton — power management, data center pick
    #"ENPH",                           # Enphase — solar microinverters

    # ── Quantum Computing ─────────────────────────────────────────────────────
    "IONQ", "QBTS", "IBM", "RGTI",

    # ── Rare Earth / Strategic Materials ──────────────────────────────────────
    "MP", "USAR",

    # ── Healthcare / Biotech / GLP-1 ─────────────────────────────────────────
    "LLY", "NVO",                     # GLP-1 duopoly — Ozempic/Mounjaro
    "HIMS",                           # GLP-1 compounder + telehealth
    "UNH", # managed care — defensive + dividend

    #"ISRG",                           # robotic surgery, strong moat
    #"DXCM",                           # continuous glucose monitoring
    "ABBV",                           # post-Humira diversification play
    #"TMO",                            # Thermo Fisher — life science tools

    # ── Fintech / Financial ───────────────────────────────────────────────────
    "HOOD", "SOFI",                   # retail fintech with momentum
    "V", "MA",                        # payment networks — defensive growth
    "PYPL",                           # turnaround candidate
    "COIN",                           # crypto proxy, high beta
                         # SQ Block — fintech + Bitcoin exposure

    # ── Consumer / EV ────────────────────────────────────────────────────────
    "TSLA",                           # EV + AI + robotics narrative

    # ── Sector ETFs (benchmarks + pattern recognition) ────────────────────────
    "SMH","IWM"                          # semiconductor ETF
    "ITA",                            # aerospace & defense ETF
    "GLD", "SLV",                     # gold/silver — safe haven momentum
]


# --------------------------------------------------------------------------
# Config -- tune these thresholds against your existing gate values
# --------------------------------------------------------------------------
EMA_TREND_PERIOD = 18          # Rosputnia's 18-day EMA trend filter
LOOKBACK_DAYS = 130            # enough history for pattern detection + EMA warmup
BOTTOM_TOLERANCE_PCT = 0.025   # two bottoms must be within 2.5% of each other
MIN_BOTTOM_SEPARATION = 8      # minimum bars between the two bottoms
MAX_BOTTOM_SEPARATION = 60     # maximum bars between the two bottoms
NECKLINE_BUFFER_PCT = 0.002    # breakout confirmation buffer above neckline
RISK_REWARD_MIN = 1.5          # minimum reward:risk to qualify as a candidate
RVOL_MIN = 0.8                 # relative volume floor on breakout day


@dataclass
class RosputniaCandidate:
    ticker: str
    pattern: str
    entry: float
    stop: float
    target: float
    risk_reward: float
    ema18: float
    price: float
    rvol: float
    score: float
    notes: str = ""


def get_price_history(ticker: str, days: int = LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """Per-ticker fetch (avoids yfinance batch-download MultiIndex/NaN issues)."""
    try:
        df = yf.Ticker(ticker).history(period=f"{days}d", auto_adjust=True)
        if df.empty or len(df) < 40:
            return None
        df = df.ffill()
        return df
    except Exception:
        return None


def compute_ema18(df: pd.DataFrame) -> pd.Series:
    return df["Close"].ewm(span=EMA_TREND_PERIOD, adjust=False).mean()


def compute_rvol(df: pd.DataFrame, window: int = 20) -> float:
    avg_vol = df["Volume"].iloc[-(window + 1):-1].mean()
    if not avg_vol or np.isnan(avg_vol) or avg_vol == 0:
        return 0.0
    return float(df["Volume"].iloc[-1] / avg_vol)


def find_local_minima(series: pd.Series, order: int = 3) -> list[int]:
    """Simple local-minima finder: index i is a min if it's <= all points
    within `order` bars on both sides."""
    vals = series.values
    minima = []
    for i in range(order, len(vals) - order):
        window = vals[i - order : i + order + 1]
        if vals[i] == window.min():
            minima.append(i)
    return minima


def detect_double_bottom(df: pd.DataFrame) -> Optional[dict]:
    """
    Detects a double-bottom setup:
      - two swing lows within BOTTOM_TOLERANCE_PCT of each other
      - separated by MIN/MAX_BOTTOM_SEPARATION bars
      - a neckline (peak between the two lows)
      - current price breaking above the neckline (+buffer)
    Returns a dict with pattern levels, or None if no valid setup found.
    """
    lows = df["Low"]
    minima_idx = find_local_minima(lows, order=3)
    if len(minima_idx) < 2:
        return None

    # Check pairs of minima from most recent backwards
    for j in range(len(minima_idx) - 1, 0, -1):
        i2 = minima_idx[j]
        for i1 in reversed(minima_idx[:j]):
            sep = i2 - i1
            if sep < MIN_BOTTOM_SEPARATION:
                continue
            if sep > MAX_BOTTOM_SEPARATION:
                break  # too far back, stop searching this branch

            low1, low2 = lows.iloc[i1], lows.iloc[i2]
            if abs(low1 - low2) / low1 > BOTTOM_TOLERANCE_PCT:
                continue

            # neckline = highest high between the two bottoms
            between = df["High"].iloc[i1:i2 + 1]
            neckline = between.max()
            if neckline <= max(low1, low2):
                continue

            last_close = df["Close"].iloc[-1]
            breakout_level = neckline * (1 + NECKLINE_BUFFER_PCT)

            if last_close < breakout_level:
                continue  # pattern present but not yet confirmed

            return {
                "bottom1_idx": i1,
                "bottom2_idx": i2,
                "bottom_price": min(low1, low2),
                "neckline": neckline,
                "breakout_level": breakout_level,
            }
    return None


def hard_disqualifiers(df: pd.DataFrame, ema18: pd.Series, rvol: float) -> Optional[str]:
    """Return a disqualification reason, or None if the ticker passes."""
    price = df["Close"].iloc[-1]
    if price < ema18.iloc[-1]:
        return "Price below 18 EMA trend filter"
    if rvol < RVOL_MIN:
        return f"RVOL {rvol:.2f} below minimum {RVOL_MIN}"
    if price < 5:
        return "Price below $5 minimum"
    return None


def score_candidate(risk_reward: float, rvol: float, ema_slope_pct: float) -> float:
    """Simple composite score: reward:risk weighted highest, plus volume
    confirmation and trend strength (18 EMA slope)."""
    return round((risk_reward * 40) + (min(rvol, 3.0) * 15) + (ema_slope_pct * 100), 2)


def evaluate_ticker(ticker: str) -> Optional[RosputniaCandidate]:
    df = get_price_history(ticker)
    if df is None:
        return None

    ema18 = compute_ema18(df)
    rvol = compute_rvol(df)

    dq_reason = hard_disqualifiers(df, ema18, rvol)
    if dq_reason:
        return None

    pattern = detect_double_bottom(df)
    if pattern is None:
        return None

    entry = pattern["breakout_level"]
    stop = pattern["bottom_price"] * 0.995  # small buffer below the lower bottom
    risk = entry - stop
    if risk <= 0:
        return None

    measured_move = pattern["neckline"] - pattern["bottom_price"]
    target = entry + measured_move
    reward = target - entry
    risk_reward = reward / risk

    if risk_reward < RISK_REWARD_MIN:
        return None

    ema_slope_pct = (ema18.iloc[-1] - ema18.iloc[-6]) / ema18.iloc[-6]
    score = score_candidate(risk_reward, rvol, ema_slope_pct)

    return RosputniaCandidate(
        ticker=ticker,
        pattern="Double Bottom + 18EMA Trend",
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        risk_reward=round(risk_reward, 2),
        ema18=round(ema18.iloc[-1], 2),
        price=round(df["Close"].iloc[-1], 2),
        rvol=round(rvol, 2),
        score=score,
        notes=f"Neckline {pattern['neckline']:.2f}",
    )


def scan_universe(tickers: list[str] = UNIVERSE) -> list[RosputniaCandidate]:
    results = []
    for t in tickers:
        print("Ticker is:",t)
        try:
            candidate = evaluate_ticker(t)
            if candidate:
                results.append(candidate)
        except Exception as e:
            print(f"[rosputnia_scan] {t} failed: {e}")
    results.sort(key=lambda c: c.score, reverse=True)
    return results


if __name__ == "__main__":
    candidates = scan_universe()
    if not candidates:
        print("No Rosputnia-style double-bottom candidates found today.")
    for c in candidates:
        print(
            f"{c.ticker:<6} score={c.score:>6} entry={c.entry:<8} stop={c.stop:<8} "
            f"target={c.target:<8} R:R={c.risk_reward:<5} RVOL={c.rvol}"
        )