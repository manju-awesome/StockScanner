"""
strategy_scores.py
==================
Three strategy-specific scores per scanned row, replacing reliance on the
single category Rank_Score for opportunity selection. Each strategy answers a
different question, so each gets its own 0-100 score, a hard-filter pass flag,
and a reason string listing which primary filters passed/failed:

  Investment_Score  – long-term growth (6-24 mo). Primary filters:
                      RS_Rank > 80, EPS growth > 25%, revenue growth > 20%,
                      above 200MA, FCF positive, last earnings beat.
  Swing_Score       – 2 day-8 week setups. Primary filters:
                      Momentum-Pullback / VCP category, R:R to T2 >= 3,
                      ATR shrinking, RSI 40-60, BB %B < 0.4.
  DayTrade_Score    – intraday. Primary filters:
                      gap > 2%, RVOL > 1.5, above VWAP, ORB breakout,
                      ATR% > 3 (enough intraday range to trade).

RS_Rank
-------
The scan's RS column is the raw 3-month excess return vs QQQ (percentage
points), not an IBD-style 0-99 rating — so "RS > 80" can't be applied to it
directly. attach_strategy_scores() first converts RS to RS_Rank: the
percentile (0-99) of each ticker's RS within the scanned universe. On small
ad-hoc runs (< MIN_RANK_UNIVERSE tickers) a percentile is meaningless, so
RS_Rank falls back to a fixed mapping of the raw excess return.

Scores are graded, not binary: a name failing one primary filter still ranks
by everything else, so the lists stay useful on days when nothing is perfect.
Pass flags mark the strict A-list. Swing/day scores are zeroed when the entry
gate failed (Category == Avoid on gate) — no tradeable plan exists for them —
while the investment score survives a flat-ADX gate failure since a 6-24 month
accumulation doesn't need a trend already in place.

Usage
-----
    from stockanalysis.core.strategy_scores import attach_strategy_scores

    rows = [...]              # dicts out of get_metrics() + categorize()
    attach_strategy_scores(rows)   # in-place; adds the columns below

Columns added: RS_Rank, Investment_Score, Investment_Pass, Investment_Reason,
Swing_Score, Swing_Pass, Swing_Reason, DayTrade_Score, DayTrade_Pass,
DayTrade_Reason, LT_Entry_Timing, Buy_Zone_Score, Buy_Zone_Label.

Buy_Zone_Score/Label (core.buy_zone) answer a different question than
Investment_Score: not "is this worth owning long-term" but "is the current
price a good place to add" — valuation, pullback depth, and accumulation
volume on top of quality and trend. See core/buy_zone.py.
"""

from __future__ import annotations

from typing import Any

from stockanalysis.core.buy_zone import compute_buy_zone

# ── Primary filter thresholds (the user-facing strategy definitions) ─────────
# Long-Term Growth
CFG_INV_RS_RANK_MIN     = 80
CFG_INV_EPS_GROWTH_MIN  = 25.0
CFG_INV_REV_GROWTH_MIN  = 20.0
# Swing
CFG_SW_RR_MIN           = 3.0
CFG_SW_RSI_LO           = 40.0
CFG_SW_RSI_HI           = 60.0
CFG_SW_BB_PCTB_MAX      = 0.4
SWING_SETUP_CATEGORIES  = ("Momentum-Pullback", "VCP Setup")
# Day Trade
CFG_DT_GAP_MIN          = 2.0
CFG_DT_RVOL_MIN         = 1.5
CFG_DT_ATR_PCT_MIN      = 3.0

# Below this many tickers a within-scan percentile is noise; use the absolute
# excess-return fallback mapping instead
MIN_RANK_UNIVERSE = 20


def _v(row: dict, key: str, default: Any = None) -> Any:
    val = row.get(key)
    return val if val is not None else default


def _rs_rank_fallback(rs: float) -> int:
    """Map raw 3-mo excess return vs QQQ (pct-pts) to an approximate 0-99 rank."""
    steps = ((30, 95), (20, 90), (10, 80), (5, 70), (0, 60),
             (-5, 45), (-10, 35), (-20, 20))
    for threshold, rank in steps:
        if rs >= threshold:
            return rank
    return 10


def attach_rs_rank(rows: list[dict]) -> None:
    """
    Add RS_Rank (0-99 percentile of RS within `rows`) to every row in place.
    Rows with RS=None get RS_Rank=None. Small universes use the absolute
    fallback mapping (see module docstring).
    """
    rs_vals = sorted(r["RS"] for r in rows if r.get("RS") is not None)
    n = len(rs_vals)
    for row in rows:
        rs = row.get("RS")
        if rs is None:
            row["RS_Rank"] = None
        elif n < MIN_RANK_UNIVERSE:
            row["RS_Rank"] = _rs_rank_fallback(rs)
        else:
            below = sum(1 for v in rs_vals if v < rs)
            row["RS_Rank"] = int(round(below / n * 99))


# ─────────────────────────────────────────────────────────────────────────────
# INVESTMENT (long-term growth, 6-24 months)
# ─────────────────────────────────────────────────────────────────────────────

def score_investment(row: dict) -> tuple[int, bool, str]:
    """Return (score 0-100, primary-filters pass, reason). Missing data fails
    the filter it belongs to but only costs that filter's points."""
    rs_rank  = row.get("RS_Rank")
    eps_g    = row.get("EPS_Growth%")
    rev_g    = row.get("Revenue")
    above200 = row.get("Above_200MA")
    fcf      = row.get("FCF_Positive")
    beat     = row.get("EarningsBeat")
    inst_own = _v(row, "Inst_Own%", 0.0)
    inst_chg = row.get("Inst_Own_Chg")

    score = 0
    # Leadership — RS_Rank (max 25)
    if rs_rank is not None:
        if rs_rank > CFG_INV_RS_RANK_MIN: score += 25
        elif rs_rank >= 60:               score += 15
        elif rs_rank >= 40:               score += 8
    # Earnings growth (max 20)
    if eps_g is not None:
        if eps_g > CFG_INV_EPS_GROWTH_MIN: score += 20
        elif eps_g > 15:                   score += 12
        elif eps_g > 0:                    score += 5
    # Revenue growth (max 15)
    if rev_g is not None:
        if rev_g > CFG_INV_REV_GROWTH_MIN: score += 15
        elif rev_g > 10:                   score += 9
        elif rev_g > 0:                    score += 4
    # Stage — long-term entries only above the 200MA (10)
    if above200: score += 10
    # Quality (10 + 10)
    if fcf:  score += 10
    if beat: score += 10
    # Sponsorship (5 + 5)
    if inst_own >= 40:                       score += 5
    if inst_chg is not None and inst_chg > 0: score += 5

    checks = [
        (f"RS_Rank>{CFG_INV_RS_RANK_MIN}",
         rs_rank is not None and rs_rank > CFG_INV_RS_RANK_MIN),
        (f"EPS_growth>{CFG_INV_EPS_GROWTH_MIN:.0f}%",
         eps_g is not None and eps_g > CFG_INV_EPS_GROWTH_MIN),
        (f"revenue_growth>{CFG_INV_REV_GROWTH_MIN:.0f}%",
         rev_g is not None and rev_g > CFG_INV_REV_GROWTH_MIN),
        ("above_200MA", above200 is True),
        ("FCF_positive", fcf is True),
        ("earnings_beat", beat is True),
    ]
    return score, all(ok for _, ok in checks), _reason(checks)


def lt_entry_timing(row: dict) -> str:
    """
    Entry-timing hint for the long-term list. The Investment filters answer
    "is this a name worth owning 6-24 months?"; this answers "is NOW a good
    time to start the position?" — so an Investment-Pass leader labeled Avoid
    by the swing tree (quiet consolidation) still reads as buyable here, while
    an extended one reads as tranche-only. Purely advisory, never a filter.
    """
    above200 = row.get("Above_200MA")
    p200     = row.get("Price_vs_200MA%")
    dist     = row.get("Dist_52W_High%")
    cat      = _v(row, "Category", "")

    if above200 is False:
        return "below 200MA — no new LT entry until stage repairs"
    if above200 is None or p200 is None:
        return "insufficient history — verify trend manually"
    ext = f" (extended +{p200:.0f}% vs 200MA — small tranches)" if p200 > 50 else ""
    if cat == "Momentum-Pullback":
        return "pullback in progress — favorable add point" + ext
    if cat == "VCP Setup":
        return "base forming — favorable accumulation zone" + ext
    if p200 > 50:
        return f"extended +{p200:.0f}% vs 200MA — stage in on pullbacks only"
    if dist is not None and dist >= -10:
        return "near 52W high, trend intact — start partial, add on pullbacks"
    return "trend intact — standard tranche entry"


# ─────────────────────────────────────────────────────────────────────────────
# SWING (2 days - 8 weeks)
# ─────────────────────────────────────────────────────────────────────────────

def score_swing(row: dict) -> tuple[int, bool, str]:
    """Return (score 0-100, primary-filters pass, reason). Zero score when the
    entry gate failed — no swing plan (levels/R:R) exists for those rows."""
    cat        = _v(row, "Category", "")
    rr         = row.get("RR_T2")
    atr_shrink = row.get("ATR Shrinking")
    rsi        = row.get("RSI_14")
    bb         = row.get("BB_PctB")
    pvol       = row.get("Pullback_Vol_Ratio")
    above200   = row.get("Above_200MA")
    rs_rank    = row.get("RS_Rank")

    if not row.get("Entry_Gate_Pass", False):
        return 0, False, ("entry_gate_failed: "
                          + (row.get("Entry_Gate_Reason") or "unknown"))

    score = 0
    # Setup category — pullback/VCP are the swing archetypes (max 20)
    if cat == "Momentum-Pullback": score += 20
    elif cat == "VCP Setup":       score += 18
    elif cat == "Momentum":        score += 12
    elif cat == "Turnaround":      score += 6
    # Reward:risk to T2 from the graded trade plan (max 20)
    if rr is not None:
        if rr >= CFG_SW_RR_MIN: score += 20
        elif rr >= 2.0:         score += 12
        elif rr >= 1.5:         score += 6
    # Volatility contraction (10)
    if atr_shrink: score += 10
    # RSI reset zone (max 15)
    if rsi is not None:
        if CFG_SW_RSI_LO <= rsi <= CFG_SW_RSI_HI: score += 15
        elif 35 <= rsi <= 65:                     score += 8
    # Bollinger coil (max 15)
    if bb is not None:
        if bb < 0.2:                  score += 15
        elif bb < CFG_SW_BB_PCTB_MAX: score += 10
    # Pullback on light volume (max 10)
    if pvol is not None:
        if pvol <= 0.8:   score += 10
        elif pvol <= 1.2: score += 5
    # Trend context (5 + 5)
    if above200: score += 5
    if rs_rank is not None and rs_rank >= 60: score += 5

    checks = [
        ("setup(MP/VCP)", cat in SWING_SETUP_CATEGORIES),
        (f"RR_T2>={CFG_SW_RR_MIN:.0f}", rr is not None and rr >= CFG_SW_RR_MIN),
        ("ATR_shrinking", atr_shrink is True),
        (f"RSI[{CFG_SW_RSI_LO:.0f},{CFG_SW_RSI_HI:.0f}]",
         rsi is not None and CFG_SW_RSI_LO <= rsi <= CFG_SW_RSI_HI),
        (f"BB_%B<{CFG_SW_BB_PCTB_MAX}",
         bb is not None and bb < CFG_SW_BB_PCTB_MAX),
    ]
    return score, all(ok for _, ok in checks), _reason(checks)


# ─────────────────────────────────────────────────────────────────────────────
# DAY TRADE (intraday)
# ─────────────────────────────────────────────────────────────────────────────

def score_day_trade(row: dict) -> tuple[int, bool, str]:
    """Return (score 0-100, primary-filters pass, reason). Zero score when the
    entry gate failed. Uses time-adjusted intraday RVOL when available and the
    implied gap (Gap_Now%) when the session hasn't opened yet."""
    gap  = row.get("Gap%")
    if gap is None:
        gap = row.get("Gap_Now%")
    rvol       = row.get("RVOL_Intraday")
    if rvol is None:
        rvol = row.get("RVOL")
    above_vwap = row.get("Above_VWAP")
    orb        = row.get("ORB_Status")
    atr_pct    = row.get("ATR_Pct")
    adx        = row.get("ADX_14")
    rs_rank    = row.get("RS_Rank")

    if not row.get("Entry_Gate_Pass", False):
        return 0, False, ("entry_gate_failed: "
                          + (row.get("Entry_Gate_Reason") or "unknown"))

    score = 0
    # Gap — the day's catalyst (max 20). Gap-downs can be day-trade shorts,
    # so magnitude scores; direction is visible in the Gap% column.
    if gap is not None:
        g = abs(gap)
        if g >= CFG_DT_GAP_MIN: score += 20
        elif g >= 1.0:          score += 10
    # Relative volume — participation (max 20)
    if rvol is not None:
        if rvol >= 2.0:               score += 20
        elif rvol >= CFG_DT_RVOL_MIN: score += 15
        elif rvol >= 1.2:             score += 8
    # VWAP — buyers in control (15)
    if above_vwap: score += 15
    # Opening range (max 15)
    if orb == "above":               score += 15
    elif orb in ("forming", "inside"): score += 5
    # Intraday range — needs to move, but not untradeably wild (max 10)
    if atr_pct is not None:
        if CFG_DT_ATR_PCT_MIN <= atr_pct <= 8: score += 10
        elif atr_pct > 8:                      score += 5
    # Trend strength (max 10)
    if adx is not None:
        if adx >= 30:   score += 10
        elif adx >= 20: score += 5
    # Leadership (max 10)
    if rs_rank is not None:
        if rs_rank >= 80:   score += 10
        elif rs_rank >= 60: score += 5

    checks = [
        (f"gap>{CFG_DT_GAP_MIN:.0f}%", gap is not None and abs(gap) > CFG_DT_GAP_MIN),
        (f"RVOL>{CFG_DT_RVOL_MIN}", rvol is not None and rvol > CFG_DT_RVOL_MIN),
        ("above_VWAP", above_vwap is True),
        ("ORB_breakout", orb == "above"),
        (f"ATR%>{CFG_DT_ATR_PCT_MIN:.0f}",
         atr_pct is not None and atr_pct > CFG_DT_ATR_PCT_MIN),
    ]
    return score, all(ok for _, ok in checks), _reason(checks)


def _reason(checks: list[tuple[str, bool]]) -> str:
    passed = [name for name, ok in checks if ok]
    failed = [name for name, ok in checks if not ok]
    parts = []
    if passed:
        parts.append("PASSED: " + ", ".join(passed))
    if failed:
        parts.append("FAILED: " + ", ".join(failed))
    return " | ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def attach_strategy_scores(rows: list[dict]) -> list[dict]:
    """Compute RS_Rank plus all three strategy scores for every row, in place.
    Returns the same list. Error rows (no metrics) get zero scores."""
    attach_rs_rank(rows)
    for row in rows:
        for prefix, scorer in (("Investment", score_investment),
                               ("Swing", score_swing),
                               ("DayTrade", score_day_trade)):
            try:
                score, ok, reason = scorer(row)
            except Exception as e:                  # metrics-free Error rows
                score, ok, reason = 0, False, f"score_error: {e}"
            row[f"{prefix}_Score"]  = score
            row[f"{prefix}_Pass"]   = ok
            row[f"{prefix}_Reason"] = reason
        try:
            row["LT_Entry_Timing"] = lt_entry_timing(row)
        except Exception as e:
            row["LT_Entry_Timing"] = f"timing_error: {e}"
        try:
            zone = compute_buy_zone(row)
        except Exception as e:
            zone = {"score": None, "label": None, "drivers": [f"buy_zone_error: {e}"]}
        row["Buy_Zone_Score"] = zone["score"]
        row["Buy_Zone_Label"] = zone["label"]
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# CARD RANKS (dashboard Top-5 ordering)
# ─────────────────────────────────────────────────────────────────────────────
# Moved here from reporting/dashboard.py so the Research Library and the A+
# setup alerts share the EXACT formulas the dashboard cards rank by (the
# "Score 113" badge). Unbounded additive scores, distinct from the 0-100
# Swing_Score/DayTrade_Score strategy filters above; -999 = gate failed /
# Avoid, i.e. not rankable. The scheduler's A+ bands sit on top of these
# (day >= 80, swing >= 95 — see scheduler.GRADE_BANDS).


def day_card_rank(row: dict) -> float:
    """
    Day trade card rank — prioritises intraday momentum signals.
    Best candidates: Momentum stocks, above VWAP, high RVOL, tight ATR.
    """
    score = 0.0
    cat        = _v(row, "Category", "")
    # Prefer time-adjusted intraday RVOL — daily RVOL reads ~0.3 all morning
    rvol       = _v(row, "RVOL_Intraday") or _v(row, "RVOL", 0) or 0
    above_vwap = _v(row, "Above_VWAP", False)
    rs         = _v(row, "RS", 0) or 0
    adx        = _v(row, "ADX_14", 0) or 0
    rsi        = _v(row, "RSI_14", 50) or 50
    atr_pct    = _v(row, "ATR_Pct", 99) or 99
    dist       = _v(row, "Dist_52W_High%", -999) or -999
    days_52h   = _v(row, "Days_Since_52W_High", 999) or 999
    above200   = _v(row, "Above_200MA", False)
    entry_pass = _v(row, "Entry_Gate_Pass", False)

    if not entry_pass or cat == "Avoid":
        return -999

    # Category bonus — Momentum best for day trades
    if cat == "Momentum":              score += 30
    elif cat == "Momentum-Pullback":   score += 15
    elif cat == "Turnaround":          score += 5

    # RVOL — most important intraday signal
    if rvol >= 2.0:    score += 25
    elif rvol >= 1.5:  score += 18
    elif rvol >= 1.2:  score += 10
    elif rvol >= 0.8:  score += 5

    # Above VWAP — buyers in control
    if above_vwap:     score += 15

    # ADX — trend strength (day trades need trend)
    if adx >= 35:      score += 12
    elif adx >= 25:    score += 8
    elif adx >= 20:    score += 4

    # RS — relative strength vs QQQ
    if rs >= 50:       score += 10
    elif rs >= 20:     score += 7
    elif rs >= 0:      score += 3
    else:              score -= 5

    # RSI — not overbought or oversold
    if 45 <= rsi <= 65: score += 8
    elif 35 <= rsi < 45 or 65 < rsi <= 72: score += 4

    # ATR% — lower = tighter moves = better risk control intraday
    if atr_pct <= 3:   score += 8
    elif atr_pct <= 5: score += 4
    elif atr_pct > 10: score -= 10   # too wild for day trade

    # Fresh near 52W high
    if dist >= -5 and days_52h <= 5:   score += 10
    elif dist >= -10 and days_52h <= 10: score += 5

    # Above 200MA — structural health
    if above200:       score += 5

    return round(score, 1)


def swing_card_rank(row: dict) -> float:
    """
    Swing trade card rank — multi-day to multi-week setups.
    Best candidates: MP or VCP with coiling + low pullback vol + RS improving.
    """
    score = 0.0
    cat        = _v(row, "Category", "")
    rs         = _v(row, "RS", 0) or 0
    rsi        = _v(row, "RSI_14", 50) or 50
    bb         = _v(row, "BB_PctB", 1) or 1
    pvol       = _v(row, "Pullback_Vol_Ratio", 2) or 2
    atr_shrink = _v(row, "ATR Shrinking", False)
    above200   = _v(row, "Above_200MA", False)
    vol_dry    = _v(row, "VolumeDryingUp", False)
    atr_pct    = _v(row, "ATR_Pct", 99) or 99
    p50pct     = _v(row, "Price_vs_50MA%", -999) or -999
    earn_beat  = _v(row, "EarningsBeat", False)
    entry_pass = _v(row, "Entry_Gate_Pass", False)

    if not entry_pass or cat == "Avoid":
        return -999

    # Category bonus
    if cat == "Momentum-Pullback": score += 30
    elif cat == "VCP Setup":       score += 28
    elif cat == "Momentum":        score += 20
    elif cat == "Turnaround":      score += 10

    # RS — must be improving or positive
    if rs >= 50:    score += 20
    elif rs >= 20:  score += 14
    elif rs >= 0:   score += 8
    elif rs >= -10: score += 2
    else:           score -= 8

    # BB coil — compressed = energy building
    if bb <= 0.1:   score += 18
    elif bb <= 0.2: score += 14
    elif bb <= 0.3: score += 10
    elif bb <= 0.4: score += 6

    # ATR shrinking — volatility contracting
    if atr_shrink:  score += 12

    # Pullback volume — light selling = healthy
    if pvol <= 0.6: score += 10
    elif pvol <= 0.8: score += 7
    elif pvol <= 1.0: score += 4

    # Above 200MA
    if above200:    score += 8

    # Volume drying up — accumulation signal
    if vol_dry:     score += 6

    # RSI sweet spot for swing
    if 30 <= rsi <= 50: score += 8   # oversold bouncing
    elif 50 < rsi <= 65: score += 5  # healthy

    # Near 50MA (reclaim candidate)
    if -5 <= p50pct <= 5:  score += 8
    elif -15 <= p50pct < -5: score += 4

    # Earnings beat = fundamental catalyst
    if earn_beat:   score += 5

    # ATR penalty for swing (too volatile = hard to hold)
    if atr_pct > 12: score -= 8
    elif atr_pct > 8: score -= 3

    return round(score, 1)
