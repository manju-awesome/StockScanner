"""
key_levels.py
=============
Computes a single "Key Level Score" per ticker: candidate support/resistance
zones from 5-min swing pivots + volume profile (intraday) merged with
structural daily levels (prev-day high/low, 52W high/low, unfilled gaps),
scored on a 7-factor weighted formula, filtered to score >= 75, and reduced
to the nearest support (S1) and nearest resistance (R1) around the current
price.

Weights (sum to 100):
    Touches                 25%
    Volume at touches       25%
    Recency                 15%
    ATR reaction size       10%
    Gap/earnings level      10%
    Moving-average confluence 5%
    Volume profile HVN/POC  10%

Pure function of already-fetched data — get_metrics() passes in the 1y daily
frame and ~20-day 5-min intraday frame it already fetches for other metrics,
so this adds no new network calls. Never raises: on thin/missing data it
returns KEY_LEVEL_DEFAULTS (all None/False).

Note on "earnings" gaps: the scanner only carries the *upcoming* EarningsDate
(yfinance's calendar is forward-looking), so past gaps in the 1y history
can't be reliably matched to a specific earnings date. Gap conviction is
instead sized by how large the gap is relative to ATR — a gap >= 1.5x ATR is
much more likely to be earnings/news-driven than routine noise.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KEY_LEVEL_KEYS = (
    "S1", "R1", "Key_Level_Score", "Touches", "Volume_Confirmation",
    "Dist_to_Support%", "Dist_to_Resistance%", "RR_to_Resistance",
    "Breakout_Probability", "Bounce_Probability",
)
KEY_LEVEL_DEFAULTS = {k: None for k in KEY_LEVEL_KEYS}

# Institutional weighting for the Key Level Score (must sum to 1.0)
SCORE_WEIGHTS = {
    "touch": 0.25, "touch_volume": 0.25, "recency": 0.15,
    "atr_reaction": 0.10, "gap_earnings": 0.10,
    "ma_confluence": 0.05, "hvn_poc": 0.10,
}

_SWING_WINDOW      = 6      # bars each side for a fractal pivot on 5m bars
_SCORE_THRESHOLD   = 75.0
_MIN_TOUCHES       = 3      # below this, probabilities are an unreliable sample
_HORIZON_BARS      = 24     # ~2h look-forward window for reaction/break/hold
_MIN_INTRADAY_BARS = 100    # need a real lookback before trusting swing/volume signals


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Same formula as core.metrics.calculate_atr, duplicated to avoid a
    circular import (metrics.py is the caller of this module)."""
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _clean_intraday(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    return df[cols].dropna(subset=["Close"])


def _intraday_atr_unit(intraday: pd.DataFrame, period: int = 14) -> float:
    """Typical 5-min bar true range (median of the ATR(14) series) — the
    reaction/breakout unit. A full daily ATR move within one session is too
    strict a bar for a level derived from 5-min bars."""
    series = _atr(intraday, period=period).dropna()
    return float(series.median()) if not series.empty else 0.0


def _volume_profile(intraday: pd.DataFrame, n_bins: int = 60):
    typical = (intraday["High"] + intraday["Low"] + intraday["Close"]) / 3
    lo, hi = intraday["Low"].min(), intraday["High"].max()
    if hi <= lo:
        return np.array([]), np.array([])
    edges = np.linspace(lo, hi, n_bins + 1)
    bin_idx = np.clip(np.digitize(typical, edges) - 1, 0, n_bins - 1)
    bin_vol = np.zeros(n_bins)
    for idx, vol in zip(bin_idx, intraday["Volume"].values):
        bin_vol[idx] += vol
    bin_mid = (edges[:-1] + edges[1:]) / 2
    return bin_mid, bin_vol


def _hvn_nodes(bin_mid, bin_vol, min_frac: float = 0.55):
    """Cluster adjacent bins above min_frac*max into HVN nodes."""
    if len(bin_vol) == 0:
        return []
    threshold = bin_vol.max() * min_frac
    nodes, i, n = [], 0, len(bin_vol)
    while i < n:
        if bin_vol[i] >= threshold:
            j = i
            while j < n and bin_vol[j] >= threshold:
                j += 1
            seg_vol, seg_price = bin_vol[i:j], bin_mid[i:j]
            nodes.append({
                "price": float(np.average(seg_price, weights=seg_vol)),
                "volume": float(seg_vol.sum()),
            })
            i = j
        else:
            i += 1
    return nodes


def _find_swing_points(intraday: pd.DataFrame, window: int = _SWING_WINDOW):
    highs, lows = intraday["High"].values, intraday["Low"].values
    n = len(intraday)
    pivots = []
    for i in range(window, n - window):
        seg_h = highs[i - window:i + window + 1]
        seg_l = lows[i - window:i + window + 1]
        if highs[i] == seg_h.max():
            pivots.append({"price": highs[i], "bar": i})
        if lows[i] == seg_l.min():
            pivots.append({"price": lows[i], "bar": i})
    return pivots


def _cluster_swings(pivots, tolerance: float):
    """Cluster pivot prices within `tolerance`, width capped from the
    cluster's START price (not the last-added point) — comparing only to the
    last element causes single-linkage chaining, where a dense run of pivots
    absorbs the entire price range into one meaningless cluster."""
    if not pivots or tolerance <= 0:
        return []
    pivots_sorted = sorted(pivots, key=lambda p: p["price"])
    clusters, cur = [], [pivots_sorted[0]]
    for p in pivots_sorted[1:]:
        if p["price"] - cur[0]["price"] <= tolerance:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)
    out = []
    for c in clusters:
        prices = [x["price"] for x in c]
        bars = [x["bar"] for x in c]
        out.append({"price": float(np.mean(prices)), "bars": bars})
    return out


def _count_touches(intraday: pd.DataFrame, level: float, tolerance: float):
    """Generic touch scan for structural levels (prev-day H/L, 52W H/L, gaps)
    that don't have an inherent pivot definition: any bar whose High/Low
    range comes within `tolerance` of `level` counts as one touch."""
    highs, lows, vols = intraday["High"].values, intraday["Low"].values, intraday["Volume"].values
    mask = (highs >= level - tolerance) & (lows <= level + tolerance)
    bars = np.nonzero(mask)[0].tolist()
    return bars


def _find_unfilled_gaps(daily: pd.DataFrame, atr20: float, lookback_days: int = 90):
    d = daily.tail(lookback_days).copy()
    d["prev_close"] = d["Close"].shift(1)
    gaps = []
    for i in range(1, len(d)):
        row = d.iloc[i]
        prev_close = row["prev_close"]
        if pd.isna(prev_close):
            continue
        gap_lo, gap_hi = sorted((float(row["Open"]), float(prev_close)))
        if gap_hi - gap_lo <= 0:
            continue
        later = d.iloc[i + 1:]
        filled = ((later["Low"] <= gap_lo) & (later["High"] >= gap_hi)).any() if not later.empty else False
        if not filled:
            gaps.append({
                "price": (gap_lo + gap_hi) / 2,
                "size": gap_hi - gap_lo,
                "is_big": bool(atr20 and (gap_hi - gap_lo) >= 1.5 * atr20),
            })
    return gaps


def _reaction_score(intraday: pd.DataFrame, bars: list, atr_unit: float, horizon: int = _HORIZON_BARS):
    """Avg favorable excursion after each touch, in intraday-ATR units."""
    if not bars or atr_unit <= 0:
        return 0.0
    closes, highs, lows = intraday["Close"].values, intraday["High"].values, intraday["Low"].values
    n = len(closes)
    reactions = []
    for b in bars:
        end = min(b + horizon, n - 1)
        if end <= b:
            continue
        move = max(highs[b:end + 1].max() - closes[b], closes[b] - lows[b:end + 1].min())
        reactions.append(move / atr_unit)
    return float(np.mean(reactions)) if reactions else 0.0


def _touch_volume_multiple(intraday: pd.DataFrame, bars: list, avg_bar_vol: float):
    if not bars or not avg_bar_vol:
        return 0.0
    vols = intraday["Volume"].values
    return float(np.mean([vols[b] for b in bars])) / avg_bar_vol


def _hvn_proximity(level: float, hvn_nodes: list, poc_price: float | None, tolerance: float):
    """0-100: full credit at/inside the POC's node, decaying with distance to
    the nearest HVN node otherwise — independent of touch-based volume, since
    a high-volume shelf need not be a swing pivot."""
    if not hvn_nodes:
        return 0.0
    nearest = min(hvn_nodes, key=lambda nd: abs(nd["price"] - level))
    dist = abs(nearest["price"] - level)
    proximity = max(0.0, 1.0 - dist / max(tolerance, 1e-9))
    is_poc = poc_price is not None and abs(nearest["price"] - poc_price) < 1e-9
    return 100.0 * proximity * (1.0 if is_poc else 0.85)


def compute_key_levels(ticker: str, price: float, daily: pd.DataFrame,
                       intraday_5m: pd.DataFrame | None,
                       atr20: float | None, ma50: float | None, ma200: float | None,
                       prev_day_high: float | None, prev_day_low: float | None,
                       prior_52w_high: float | None, prior_52w_low: float | None) -> dict:
    intraday = _clean_intraday(intraday_5m)
    if (not price or daily is None or daily.empty
            or len(intraday) < _MIN_INTRADAY_BARS or not atr20):
        return dict(KEY_LEVEL_DEFAULTS)

    tolerance = 0.4 * atr20  # zone width — daily-ATR scale, not the tiny 5m-bar scale
    atr_unit = _intraday_atr_unit(intraday)
    if atr_unit <= 0 or tolerance <= 0:
        return dict(KEY_LEVEL_DEFAULTS)

    n_bars = len(intraday)
    avg_bar_vol = float(intraday["Volume"].mean()) or 1.0

    bin_mid, bin_vol = _volume_profile(intraday)
    hvn_nodes = _hvn_nodes(bin_mid, bin_vol)
    poc_price = float(bin_mid[np.argmax(bin_vol)]) if len(bin_vol) else None

    # ── candidate zones ──────────────────────────────────────────────────
    candidates = []

    pivots = _find_swing_points(intraday)
    for cluster in _cluster_swings(pivots, tolerance):
        candidates.append({"level": cluster["price"], "bars": cluster["bars"], "is_gap": False})

    for lvl in (prev_day_high, prev_day_low, prior_52w_high, prior_52w_low):
        if lvl is None:
            continue
        bars = _count_touches(intraday, lvl, tolerance)
        candidates.append({"level": float(lvl), "bars": bars, "is_gap": False})

    for gap in _find_unfilled_gaps(daily, atr20):
        bars = _count_touches(intraday, gap["price"], tolerance)
        candidates.append({"level": gap["price"], "bars": bars, "is_gap": True, "is_big_gap": gap["is_big"]})

    # ── score each candidate ─────────────────────────────────────────────
    scored = []
    for c in candidates:
        touches = len(c["bars"])
        if touches == 0:
            continue
        touch_score = min(touches / 6.0, 1.0) * 100.0

        touch_vol_mult = _touch_volume_multiple(intraday, c["bars"], avg_bar_vol)
        touch_volume_score = min(touch_vol_mult / 2.0, 1.0) * 100.0

        last_bar = max(c["bars"])
        recency_score = 100.0 * (last_bar / n_bars)

        avg_reaction = _reaction_score(intraday, c["bars"], atr_unit)
        atr_reaction_score = min(avg_reaction / 6.0, 1.0) * 100.0

        if c.get("is_gap"):
            gap_earnings_score = 100.0 if c.get("is_big_gap") else 60.0
        else:
            gap_earnings_score = 0.0

        ma_confluence_score = 0.0
        for ma in (ma50, ma200):
            if ma:
                dist = abs(ma - c["level"]) / atr20
                ma_confluence_score = max(ma_confluence_score, 100.0 * max(0.0, 1.0 - dist))

        hvn_poc_score = _hvn_proximity(c["level"], hvn_nodes, poc_price, tolerance)

        w = SCORE_WEIGHTS
        score = (w["touch"] * touch_score + w["touch_volume"] * touch_volume_score
                 + w["recency"] * recency_score + w["atr_reaction"] * atr_reaction_score
                 + w["gap_earnings"] * gap_earnings_score
                 + w["ma_confluence"] * ma_confluence_score + w["hvn_poc"] * hvn_poc_score)

        scored.append({
            "level": c["level"], "touches": touches, "bars": c["bars"],
            "touch_vol_mult": touch_vol_mult, "score": score,
        })

    qualifying = [c for c in scored if c["score"] >= _SCORE_THRESHOLD]
    supports = sorted((c for c in qualifying if c["level"] < price), key=lambda c: -c["level"])
    resistances = sorted((c for c in qualifying if c["level"] > price), key=lambda c: c["level"])
    s1 = supports[0] if supports else None
    r1 = resistances[0] if resistances else None

    def _hold_break_prob(cand, direction: str) -> float | None:
        """direction='support' -> fraction of touches that held (bounced);
        direction='resistance' -> fraction of touches that broke out."""
        if cand is None or cand["touches"] < _MIN_TOUCHES:
            return None
        closes, n = intraday["Close"].values, len(intraday)
        level = cand["level"]
        outcomes = []
        for b in cand["bars"]:
            end = min(b + _HORIZON_BARS, n - 1)
            if end <= b:
                continue
            window_close = closes[b:end + 1]
            if direction == "resistance":
                outcomes.append(bool((window_close > level + 0.25 * atr_unit).any()))
            else:
                outcomes.append(bool(not (window_close < level - 0.25 * atr_unit).any()))
        return round(100.0 * np.mean(outcomes), 1) if outcomes else None

    breakout_probability = _hold_break_prob(r1, "resistance")
    bounce_probability = _hold_break_prob(s1, "support")

    nearest = None
    if s1 and r1:
        nearest = s1 if (price - s1["level"]) <= (r1["level"] - price) else r1
    else:
        nearest = s1 or r1

    return {
        "S1": round(s1["level"], 2) if s1 else None,
        "R1": round(r1["level"], 2) if r1 else None,
        "Key_Level_Score": round(nearest["score"], 1) if nearest else None,
        "Touches": nearest["touches"] if nearest else None,
        "Volume_Confirmation": (nearest["touch_vol_mult"] >= 1.2) if nearest else None,
        "Dist_to_Support%": round((price - s1["level"]) / price * 100, 2) if s1 else None,
        "Dist_to_Resistance%": round((r1["level"] - price) / price * 100, 2) if r1 else None,
        "RR_to_Resistance": (
            round((r1["level"] - price) / (price - s1["level"]), 2)
            if s1 and r1 and (price - s1["level"]) > 0 else None
        ),
        "Breakout_Probability": breakout_probability,
        "Bounce_Probability": bounce_probability,
    }
