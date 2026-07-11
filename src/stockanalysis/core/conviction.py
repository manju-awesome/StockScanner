"""
conviction.py
=============
The prioritization layer: turns each scanned row's ~15 metrics into the
handful of things a human decides with, answering three separate questions a
single score conflates:

  Conv_Quality  0-100  — is this a good COMPANY? (fundamentals)
  Conv_Setup    0-100  — is this a good TRADE?   (pattern / plan quality)
  Conv_Timing   0-100  — is NOW the moment?      (intraday + entry proximity)
  Conv_Overall  0-100  — 0.35·quality + 0.35·setup + 0.30·timing
  Conv_Stars    1-5    — confidence stars (overall/20)

  Conv_Why      list[(mark, text)] — the "why this stock?" checklist;
                mark ∈ {"+", "!", "-"} → render ✅ / ⚠ / ❌
  Conv_Tags     list[(label, level)] — color-coded risk/character badges;
                level ∈ {"good", "warn", "bad"}
  Conv_Action   "READY" | "WATCH" | "AVOID"  (🟢 / 🟡 / 🔴)
  Conv_Action_Reason  short imperative for the priority queue
                ("Buy now", "Watch breakout", "Wait for pullback",
                 "Too extended", "Avoid today")

daily_opportunity(rows, regime) rolls the whole scan + market regime into one
headline number for the dashboard hero panel ("Today's Opportunity 82/100 —
Good trading day · Risk MEDIUM").

Not computed here (needs tracked-trade history the repo doesn't have yet):
win probability and historical setup success rate — wire those to the
signal_tracker/backtest outcomes when enough live history accumulates.
"""

from __future__ import annotations

from typing import Any

OVERALL_WEIGHTS = (0.35, 0.35, 0.30)          # quality, setup, timing

READY_MIN_OVERALL = 70
WATCH_MIN_OVERALL = 45

_CATEGORY_SETUP_PTS = {"Momentum-Pullback": 25, "VCP Setup": 22,
                       "Momentum": 20, "Longterm Hold": 15, "Turnaround": 10}
_GRADE_PTS = {"A": 20, "B": 12, "C": 5}


def _v(row: dict, key: str, default: Any = None) -> Any:
    val = row.get(key)
    return val if val is not None else default


def score_quality(row: dict) -> int:
    """Company quality — pure fundamentals, no chart involved."""
    s = 0
    eps = row.get("EPS_Growth%")
    if eps is not None:
        if eps > 25:   s += 25
        elif eps > 10: s += 15
        elif eps > 0:  s += 8
    rev = row.get("Revenue")
    if rev is not None:
        if rev > 20:   s += 20
        elif rev > 10: s += 12
        elif rev > 0:  s += 6
    if row.get("FCF_Positive"):  s += 15
    if row.get("EarningsBeat"):  s += 10
    if (_v(row, "Inst_Own%", 0) or 0) >= 40: s += 10
    chg = row.get("Inst_Own_Chg")
    if chg is not None and chg > 0: s += 10
    if row.get("CANSLIM_Pass"):  s += 10
    return min(100, s)


def score_setup(row: dict) -> int:
    """Trade quality — the pattern and the plan, independent of the clock."""
    s = _CATEGORY_SETUP_PTS.get(_v(row, "Category", ""), 0)
    s += _GRADE_PTS.get(_v(row, "Grade", ""), 0)
    rr = row.get("RR_T2")
    if rr is not None:
        if rr >= 3:     s += 20
        elif rr >= 2:   s += 12
        elif rr >= 1.5: s += 6
    if row.get("ATR Shrinking"): s += 10
    bb = row.get("BB_PctB")
    if bb is not None:
        if bb < 0.2:   s += 15
        elif bb < 0.4: s += 10
    pvol = row.get("Pullback_Vol_Ratio")
    if pvol is not None:
        if pvol <= 0.8:   s += 10
        elif pvol <= 1.2: s += 5
    return min(100, s)


def score_timing(row: dict) -> int:
    """Is NOW the entry moment — participation, trigger proximity, freshness."""
    s = 0
    rvol = row.get("RVOL_Intraday")
    if rvol is None:
        rvol = row.get("RVOL")
    if rvol is not None:
        if rvol >= 2.0:   s += 20
        elif rvol >= 1.5: s += 15
        elif rvol >= 1.0: s += 8
    if row.get("Above_VWAP"): s += 15
    rsi = row.get("RSI_14")
    if rsi is not None:
        if 40 <= rsi <= 60:  s += 15
        elif 35 <= rsi <= 70: s += 8
    gap = row.get("Gap%")
    if gap is None:
        gap = row.get("Gap_Now%")
    if gap is not None and abs(gap) >= 2: s += 10
    if row.get("ORB_Status") == "above": s += 10
    e8 = row.get("Pct_vs_8EMA")
    if e8 is not None:
        if abs(e8) <= 3:  s += 15   # at the trigger zone
        elif abs(e8) <= 8: s += 8
    adx = row.get("ADX_14")
    if adx is not None:
        if adx >= 25:   s += 15
        elif adx >= 20: s += 8
    return min(100, s)


# ─────────────────────────────────────────────────────────────────────────────
# WHY THIS STOCK? + RISK TAGS
# ─────────────────────────────────────────────────────────────────────────────

def build_why(row: dict, max_items: int = 6) -> list[tuple[str, str]]:
    """Ordered checklist: positives first, then warnings, then blockers.
    Warnings/blockers always survive the cap — positives yield the space."""
    why: list[tuple[str, str]] = []
    eps      = row.get("EPS_Growth%")
    rs_rank  = row.get("RS_Rank")
    p200     = row.get("Price_vs_200MA%")
    e8       = row.get("Pct_vs_8EMA")
    dte      = row.get("Days_To_Earnings")
    atr_pct  = row.get("ATR_Pct")
    rsi      = row.get("RSI_14")
    bb       = row.get("BB_PctB")
    rvol     = row.get("RVOL_Intraday") or row.get("RVOL")

    if row.get("EarningsBeat"):
        why.append(("+", "Strong earnings (last report beat)"))
    if eps is not None and eps > 25:
        why.append(("+", f"EPS growth {eps:+.0f}%"))
    if rs_rank is not None and rs_rank >= 80:
        why.append(("+", f"Strong RS (rank {rs_rank:.0f})"))
    if row.get("Above_200MA"):
        why.append(("+", "Above 200 MA"))
    inst = row.get("Inst_Own_Chg")
    if inst is not None and inst > 0:
        why.append(("+", f"Institutional buying ({inst:+.1f}%)"))
    if (bb is not None and bb < 0.3) and row.get("ATR Shrinking"):
        why.append(("+", "Tight base — volatility contracting"))
    if rvol is not None and rvol >= 1.5:
        why.append(("+", f"Volume surge (RVOL {rvol:.1f})"))

    if e8 is not None and e8 > 8:
        why.append(("!", f"Extended {e8:.0f}% above 8EMA"))
    elif p200 is not None and p200 > 50:
        why.append(("!", f"Extended {p200:.0f}% above 200MA"))
    if dte is not None and 0 <= dte <= 7:
        why.append(("!", f"Earnings in {int(dte)}d"))
    if atr_pct is not None and atr_pct > 8:
        why.append(("!", f"High volatility (ATR {atr_pct:.0f}%)"))
    if rsi is not None and rsi > 72:
        why.append(("!", f"Overbought (RSI {rsi:.0f})"))
    if rs_rank is not None and rs_rank < 30:
        why.append(("!", f"Lagging RS (rank {rs_rank:.0f})"))

    if row.get("Above_200MA") is False:
        why.append(("-", "Below 200 MA"))
    if not row.get("Entry_Gate_Pass", False):
        why.append(("-", "Entry gate failed: "
                    + (row.get("Entry_Gate_Reason") or "unknown")))

    pos  = [w for w in why if w[0] == "+"]
    rest = [w for w in why if w[0] != "+"]
    keep_pos = max(2, max_items - len(rest))
    return (pos[:keep_pos] + rest)[:max_items]


def build_tags(row: dict) -> list[tuple[str, str]]:
    """Short color-coded character/risk badges. level: good | warn | bad."""
    tags: list[tuple[str, str]] = []
    cat      = _v(row, "Category", "")
    rs_rank  = row.get("RS_Rank")
    p200     = row.get("Price_vs_200MA%")
    e8       = row.get("Pct_vs_8EMA")
    atr_pct  = row.get("ATR_Pct")
    dte      = row.get("Days_To_Earnings")
    bb       = row.get("BB_PctB")

    extended = ((e8 is not None and e8 > 8)
                or (p200 is not None and p200 > 50))

    if cat == "Momentum" and (rs_rank or 0) >= 80:
        tags.append(("HIGH MOMENTUM", "good"))
    if row.get("Swing_Pass"):
        tags.append(("IDEAL SWING", "good"))
    if (row.get("Above_200MA") and atr_pct is not None and atr_pct < 3
            and (rs_rank or 0) >= 60):
        tags.append(("LOW RISK TREND", "good"))
    if bb is not None and bb < 0.3 and row.get("ATR Shrinking"):
        tags.append(("TIGHT BASE", "good"))
    if extended:
        tags.append(("EXTENDED", "warn"))
        if cat in ("Momentum", "Avoid"):
            tags.append(("WAIT PULLBACK", "warn"))
    if dte is not None and 0 <= dte <= 7:
        tags.append((f"EARNINGS {int(dte)}D", "warn"))
    if atr_pct is not None and atr_pct > 8:
        tags.append(("HIGH ATR", "warn"))
    if rs_rank is not None and rs_rank < 30:
        tags.append(("WEAK RS", "bad"))
    if row.get("Above_200MA") is False:
        tags.append(("BELOW 200MA", "bad"))
    if not row.get("Entry_Gate_Pass", False):
        tags.append(("GATE FAILED", "bad"))
    return tags[:5]


def decide_action(row: dict, overall: int, timing: int,
                  tags: list[tuple[str, str]]) -> tuple[str, str]:
    """(action, reason). READY🟢 / WATCH🟡 / AVOID🔴 + queue phrase."""
    labels   = {t for t, _ in tags}
    has_bad  = any(lv == "bad" for _, lv in tags)
    extended = "EXTENDED" in labels
    earnings = any(t.startswith("EARNINGS") for t in labels)
    cat      = _v(row, "Category", "")

    if not row.get("Entry_Gate_Pass", False):
        return "AVOID", "Avoid today — gate failed"
    if has_bad:
        return "AVOID", "Avoid today — " + next(
            t for t, lv in tags if lv == "bad").lower()

    if overall >= READY_MIN_OVERALL and not extended and not earnings:
        return "READY", ("Buy now" if timing >= 70 else "Buy on trigger")
    if overall >= WATCH_MIN_OVERALL or extended or earnings:
        if extended:
            return "WATCH", "Wait for pullback"
        if earnings:
            return "WATCH", "Wait for earnings print"
        if cat == "Momentum":
            return "WATCH", "Watch breakout"
        return "WATCH", "Watch — setup building"
    if extended:
        return "AVOID", "Too extended"
    return "AVOID", "Avoid today — weak conviction"


def compute_conviction(row: dict) -> dict:
    quality = score_quality(row)
    setup   = score_setup(row)
    timing  = score_timing(row)
    wq, ws, wt = OVERALL_WEIGHTS
    overall = round(wq * quality + ws * setup + wt * timing)
    tags = build_tags(row)
    action, reason = decide_action(row, overall, timing, tags)
    return {
        "Conv_Quality": quality,
        "Conv_Setup":   setup,
        "Conv_Timing":  timing,
        "Conv_Overall": overall,
        "Conv_Stars":   max(1, min(5, round(overall / 20))),
        "Conv_Why":     build_why(row),
        "Conv_Tags":    tags,
        "Conv_Action":  action,
        "Conv_Action_Reason": reason,
    }


def attach_conviction(rows: list[dict]) -> list[dict]:
    """Add Conv_* keys to every row in place; error rows get zeros/AVOID."""
    for row in rows:
        try:
            row.update(compute_conviction(row))
        except Exception as e:
            row.update({"Conv_Quality": 0, "Conv_Setup": 0, "Conv_Timing": 0,
                        "Conv_Overall": 0, "Conv_Stars": 1,
                        "Conv_Why": [("-", f"scoring error: {e}")],
                        "Conv_Tags": [("ERROR", "bad")],
                        "Conv_Action": "AVOID",
                        "Conv_Action_Reason": "Avoid — scoring error"})
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# TURNAROUND RECOVERY — which beaten-down names are becoming investable?
# ─────────────────────────────────────────────────────────────────────────────

# A name qualifies for the recovery watch when it's at least this far off its
# 52-week high — the Turnaround category alone misses names still classified
# Avoid whose repair is just starting
RECOVERY_MIN_DRAWDOWN = -35.0

RECOVERY_STAGES = {          # emoji, maturity order
    "Bottoming":       ("🔴", 0),
    "Recovering":      ("🟡", 1),
    "Trend Confirmed": ("🟢", 2),
}


def recovery_stage(row: dict) -> str:
    """Classify how mature the recovery is (stage, not tradability)."""
    p50      = row.get("Price_vs_50MA%")
    rs_rank  = row.get("RS_Rank") or 0
    pct_low  = row.get("Pct_From_52W_Low%")
    if row.get("Above_200MA") or (p50 is not None and p50 > 0 and rs_rank >= 60):
        return "Trend Confirmed"
    if ((p50 is not None and p50 > -5)
            or (pct_low is not None and pct_low >= 25)
            or (row.get("EarningsBeat") and rs_rank >= 40)):
        return "Recovering"
    return "Bottoming"


def recovery_why(row: dict) -> list[str]:
    """Why it may recover — technical AND fundamental reasons together."""
    why = []
    rs_rank = row.get("RS_Rank")
    rev     = row.get("Revenue")
    inst    = row.get("Inst_Own_Chg")
    p50     = row.get("Price_vs_50MA%")
    si      = row.get("Short_Interest%")
    pct_low = row.get("Pct_From_52W_Low%")

    if rs_rank is not None and rs_rank >= 50:
        why.append(f"RS improving (rank {rs_rank:.0f})")
    if row.get("EarningsBeat"):
        why.append("Earnings beat last quarter")
    if rev is not None and rev > 10:
        why.append(f"Revenue accelerating (+{rev:.0f}%)")
    if inst is not None and inst > 0:
        why.append(f"Institutions buying ({inst:+.1f}%)")
    if p50 is not None and p50 > 0:
        why.append(f"Reclaimed 50 MA ({p50:+.1f}%)")
    if row.get("ATR Shrinking"):
        why.append("Base forming — volatility contracting")
    if si is not None and si >= 10:
        why.append(f"Squeeze fuel (short interest {si:.0f}%)")
    if pct_low is not None and pct_low >= 30:
        why.append(f"{pct_low:.0f}% off its 52W low")
    return why


def recovery_risks(row: dict) -> list[str]:
    """Why it could fail — every turnaround states its bear case."""
    risks = []
    rev     = row.get("Revenue")
    rsi     = row.get("RSI_14")
    atr_pct = row.get("ATR_Pct")
    dte     = row.get("Days_To_Earnings")
    rvol    = row.get("RVOL_Intraday") or row.get("RVOL")
    rs_rank = row.get("RS_Rank")

    if row.get("FCF_Positive") is False:
        risks.append("Negative free cash flow")
    if row.get("Above_200MA") is False:
        risks.append("Still below 200 MA")
    if rev is not None and rev < 0:
        risks.append(f"Revenue declining ({rev:.0f}%)")
    if rsi is not None and rsi < 35:
        risks.append(f"Downside momentum (RSI {rsi:.0f})")
    if atr_pct is not None and atr_pct > 8:
        risks.append(f"High volatility (ATR {atr_pct:.0f}%)")
    if dte is not None and 0 <= dte <= 7:
        risks.append(f"Earnings in {int(dte)}d — gap risk")
    if rvol is not None and rvol < 0.8:
        risks.append(f"Weak volume (RVOL {rvol:.1f}) — no accumulation yet")
    if rs_rank is not None and rs_rank < 30:
        risks.append(f"Still lagging the market (rank {rs_rank:.0f})")
    return risks


def recovery_entry(row: dict, stage: str) -> str:
    ma50  = row.get("50MA")
    ema21 = row.get("21EMA")

    def f(px):
        return f"${px:,.2f}" if px else "n/a"

    if stage == "Trend Confirmed":
        return f"Buy pullbacks to 21 EMA {f(ema21)} — trend does the work"
    if stage == "Recovering":
        p50 = row.get("Price_vs_50MA%")
        if p50 is not None and p50 > 0:
            return f"Hold above 21 EMA {f(ema21)}; add on higher low"
        return f"Buy 50 MA reclaim {f(ma50)} on RVOL > 1.5 — not before"
    return f"No entry — wait for 50 MA reclaim {f(ma50)}. Speculative only"


def recovery_candidates(rows: list[dict],
                        min_drawdown: float = RECOVERY_MIN_DRAWDOWN,
                        max_names: int = 8) -> list[dict]:
    """
    Beaten-down names becoming investable: everything ≥ |min_drawdown|% off
    its 52W high (Turnaround category included by construction), with stage /
    why / risks / entry attached (Rec_* keys, in place). Sorted by maturity
    then score. Error rows and sub-$5/zero-data rows are skipped.
    """
    out = []
    for row in rows:
        dist = row.get("Dist_52W_High%")
        if (dist is None or dist > min_drawdown
                or _v(row, "Category") == "Error"):
            continue
        stage = recovery_stage(row)
        why   = recovery_why(row)
        risks = recovery_risks(row)
        base  = {"Trend Confirmed": 40, "Recovering": 25, "Bottoming": 10}[stage]
        score = max(0, min(100, base + 8 * len(why) - 5 * len(risks)))
        row.update({
            "Rec_Stage": stage,
            "Rec_Score": score,
            "Rec_Stars": max(1, min(5, round(score / 20))),
            "Rec_Why":   why,
            "Rec_Risks": risks,
            "Rec_Entry": recovery_entry(row, stage),
        })
        out.append(row)
    out.sort(key=lambda r: (-RECOVERY_STAGES[r["Rec_Stage"]][1],
                            -r["Rec_Score"]))
    return out[:max_names]


# ─────────────────────────────────────────────────────────────────────────────
# TODAY'S OPPORTUNITY — one number for the whole day
# ─────────────────────────────────────────────────────────────────────────────

_REGIME_BONUS = {"Bullish": 15, "Neutral": 0, "Defensive": -20}
_REGIME_RISK  = {"Bullish": "LOW", "Neutral": "MEDIUM", "Defensive": "HIGH"}


def daily_opportunity(rows: list[dict], regime: dict) -> dict:
    """
    Headline day score: how much opportunity does this scan offer, tempered
    by the market regime. 0.7 × mean overall of the top-5 conviction names
    + up to 15 for READY breadth + regime bonus/penalty.
    Requires attach_conviction() to have run.
    """
    overalls = sorted((r.get("Conv_Overall", 0) or 0 for r in rows),
                      reverse=True)[:5]
    base = sum(overalls) / len(overalls) if overalls else 0
    n_ready = sum(1 for r in rows if r.get("Conv_Action") == "READY")
    score = round(0.7 * base
                  + min(n_ready * 3, 15)
                  + _REGIME_BONUS.get(regime.get("regime", "Neutral"), 0))
    score = max(0, min(100, score))
    label = ("Excellent trading day" if score >= 80 else
             "Good trading day"      if score >= 65 else
             "Selective day — A-grade setups only" if score >= 45 else
             "Defensive day — mostly stand aside")
    return {
        "score": score,
        "stars": max(1, min(5, round(score / 20))),
        "label": label,
        "risk":  _REGIME_RISK.get(regime.get("regime", "Neutral"), "MEDIUM"),
        "n_ready": n_ready,
    }
