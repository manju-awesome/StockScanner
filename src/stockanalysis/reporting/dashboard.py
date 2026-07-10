"""
dashboard.py
============
Generates a self-contained HTML dashboard from enriched scan rows:

  - Market-pulse header: VIX, SPY/QQQ strength (own trend + allocation-weighted
    mega-cap breadth), top-10 S&P mega caps with weights, news catalysts, and
    upcoming high-impact economic events
  - Top 5 cards per section (Day Trade, Swing Trade, Calls, Puts) with the
    R:R-gated entry/stop/target plan from grade_signals, gate warnings (⛔/⚠),
    and a fixed-risk R:R + position-size line
    (ACCOUNT_SIZE / RISK_PER_TRADE_PCT / MAX_POSITION_PCT env vars)

Usage
-----
    from stockanalysis.reporting.dashboard import generate_dashboard

    rows = [...]   # dicts from get_metrics + categorize + enrich_row
    generate_dashboard(rows, output_dir=out_dir)
    # → writes dashboard_YYYYMMDD_HHMM.html and opens it in your browser

Run standalone (built-in sample data):
    python dashboard.py
    python -m stockanalysis.reporting.dashboard
"""

from __future__ import annotations
import html as _html
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):   # direct run: make `stockanalysis.*` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── Scoring functions ─────────────────────────────────────────────────────────

def _v(row: dict, key: str, default=None):
    val = row.get(key)
    return val if val is not None else default

# ── Quality grading ────────────────────────────────────────────────────────────
# Score scales differ wildly across sections (Day/Swing ~0-110, Call/Put ~0-20),
# so thresholds are calibrated per-section.
_GRADE_THRESHOLDS = {
    "Day Trade":   [(80, "A+"), (65, "A"), (50, "B+"), (35, "B"), (20, "C")],
    "Swing Trade": [(85, "A+"), (70, "A"), (55, "B+"), (40, "B"), (20, "C")],
    "Calls":       [(18, "A+"), (14, "A"), (10, "B+"), (6,  "B"), (1,  "C")],
    "Puts":        [(8,  "A+"), (6,  "A"), (4,  "B+"), (2,  "B"), (1,  "C")],
}

# ── Position sizing (fixed-risk model) ─────────────────────────────────────────
# shares = (account × risk% × size-flag multiplier) ÷ (entry − stop),
# capped so no single position exceeds MAX_POSITION_PCT of the account.
ACCOUNT_SIZE       = float(os.environ.get("ACCOUNT_SIZE", "100000"))
RISK_PER_TRADE_PCT = float(os.environ.get("RISK_PER_TRADE_PCT", "1.0"))
MAX_POSITION_PCT   = float(os.environ.get("MAX_POSITION_PCT", "25"))
_SIZE_MULT = {"FULL": 1.0, "HALF": 0.5, "QUARTER": 0.25, "NONE": 0.0}


def _risk_size_html(entry_px, stop_px, t1_px, t2_px, size_flag) -> str:
    """R:R + fixed-risk position-size line for a card. '' when levels invalid."""
    try:
        entry_px, stop_px = float(entry_px), float(stop_px)
    except (TypeError, ValueError):
        return ""
    risk_sh = entry_px - stop_px
    if risk_sh <= 0 or entry_px <= 0:
        return ""

    def _rr(t):
        try:
            t = float(t)
        except (TypeError, ValueError):
            return None
        return (t - entry_px) / risk_sh if t > entry_px else None

    rr1, rr2 = _rr(t1_px), _rr(t2_px)
    flag   = (size_flag or "FULL").upper()
    mult   = _SIZE_MULT.get(flag, 1.0)
    budget = ACCOUNT_SIZE * RISK_PER_TRADE_PCT / 100 * mult
    shares = int(budget // risk_sh)
    capped = ""
    max_cost = ACCOUNT_SIZE * MAX_POSITION_PCT / 100
    if shares * entry_px > max_cost:
        shares = int(max_cost // entry_px)
        capped = f", capped at {MAX_POSITION_PCT:.0f}% acct"

    rr_txt = " / ".join(f"T{i} {v:.1f}:1" for i, v in ((1, rr1), (2, rr2))
                        if v is not None) or "n/a"
    rr2_bad = rr2 is not None and rr2 < 2.0
    bg, fg  = ("#FCEBEB", "#791F1F") if rr2_bad else ("#E1F5EE", "#085041")
    size_txt = (f"{shares:,} sh ≈ ${shares * entry_px:,.0f}" if shares > 0
                else "0 sh — stop too wide for risk budget")
    return (
        f'<div style="display:flex;gap:6px;align-items:flex-start;margin-top:4px;font-size:11px">'
        f'<span style="min-width:42px;text-align:center;background:{bg};color:{fg};'
        f'padding:1px 5px;border-radius:3px;font-size:10px;font-weight:500;flex-shrink:0">R:R·SIZE</span>'
        f'<span style="color:#52514e;line-height:1.4">R:R {rr_txt} · risk ${risk_sh:,.2f}/sh · '
        f'{size_txt} ({flag} = {RISK_PER_TRADE_PCT * mult:g}% of ${ACCOUNT_SIZE:,.0f}{capped})</span></div>'
    )


def day_trade_levels(row: dict) -> dict | None:
    """
    Numeric day-trade breakout plan for a scan row. Single source of truth —
    used by the Day Trade cards AND the outcome tracker so the plan that gets
    graded is exactly the plan that was displayed.

    entry = ORB high (primary) or prev-day high; stop = nearest structure low
    below entry (ORB low / prev-day low) else -5%; t1 = +3%; t2 = 8EMA if it
    has ≥3% headroom, else +5%. setup labels which trigger was used.
    """
    orb_h, orb_l = _v(row, "ORB_High"), _v(row, "ORB_Low")
    pd_high, pd_low = _v(row, "Prev-Day High"), _v(row, "Prev-Day Low")
    price, ema8 = _v(row, "Current Price"), _v(row, "8EMA")
    entry = orb_h or pd_high or price
    if not entry:
        return None
    struct_lows = [x for x in (orb_l, pd_low) if x and x < entry]
    stop = max(struct_lows) if struct_lows else entry * 0.95
    t2 = ema8 if (ema8 and ema8 > entry * 1.03) else entry * 1.05
    return {
        "entry": round(float(entry), 2),
        "stop":  round(float(stop), 2),
        "t1":    round(float(entry) * 1.03, 2),
        "t2":    round(float(t2), 2),
        "setup": "ORB breakout" if orb_h else "PDH breakout",
    }


def _score_to_grade(score: float, section: str) -> str:
    thresholds = _GRADE_THRESHOLDS.get(
        section, [(80, "A+"), (60, "A"), (40, "B+"), (20, "B"), (1, "C")]
    )
    for cutoff, grade in thresholds:
        if score >= cutoff:
            return grade
    return "D"


_GRADE_COLORS = {
    "A+": ("#0F6E56", "#E1F5EE"),
    "A":  ("#185FA5", "#E6F1FB"),
    "B+": ("#26215C", "#EEEDFE"),
    "B":  ("#633806", "#FAEEDA"),
    "C":  ("#A32D2D", "#FCEBEB"),
    "D":  ("#444441", "#F1EFE8"),
}


def _day_trade_score(row: dict) -> float:
    """
    Day trade score — prioritises intraday momentum signals.
    Best candidates: Momentum stocks, above VWAP, high RVOL, tight ATR.
    """
    score = 0.0
    cat        = _v(row, "Category", "")
    # Prefer time-adjusted intraday RVOL — daily RVOL reads ~0.3 all morning
    rvol       = _v(row, "RVOL_Intraday") or _v(row, "RVOL", 0) or 0
    vol20      = _v(row, "Vol_vs_20D", 0) or 0
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


def _swing_trade_score(row: dict) -> float:
    """
    Swing trade score — multi-day to multi-week setups.
    Best candidates: MP or VCP with coiling + low pullback vol + RS improving.
    """
    score = 0.0
    cat        = _v(row, "Category", "")
    rs         = _v(row, "RS", 0) or 0
    adx        = _v(row, "ADX_14", 0) or 0
    rsi        = _v(row, "RSI_14", 50) or 50
    bb         = _v(row, "BB_PctB", 1) or 1
    pvol       = _v(row, "Pullback_Vol_Ratio", 2) or 2
    atr_shrink = _v(row, "ATR Shrinking", False)
    above200   = _v(row, "Above_200MA", False)
    rvol       = _v(row, "RVOL", 0) or 0
    vol_dry    = _v(row, "VolumeDryingUp", False)
    dist       = _v(row, "Dist_52W_High%", -999) or -999
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


def _call_score(row: dict) -> float:
    """Use Call_Score from compute_call_candidate(), else recompute proxy."""
    cat = _v(row, "Category", "")
    if cat not in ("Turnaround", "Momentum-Pullback", "VCP Setup"):
        return -999
    if not _v(row, "Entry_Gate_Pass", False):
        return -999
    cs  = _v(row, "Call_Score", 0) or 0
    cnd = _v(row, "Call_Candidate", False)
    return float(cs) if cnd else -1.0


def _put_score(row: dict) -> float:
    """Use Put_Score from compute_put_candidate()."""
    if not _v(row, "Entry_Gate_Pass", False):
        return -999
    ps  = _v(row, "Put_Score", 0) or 0
    cnd = _v(row, "Put_Candidate", False)
    return float(ps) if cnd else -1.0


def _top5(rows: list[dict], score_fn, min_score: float = 0) -> list[dict]:
    """Return top 5 rows by score_fn, with score attached."""
    scored = []
    for r in rows:
        s = score_fn(r)
        if s > min_score:
            scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    result = []
    for s, r in scored[:5]:
        r = dict(r)   # copy — don't mutate original
        r["_dashboard_score"] = s
        result.append(r)
    return result


# ── HTML generation ───────────────────────────────────────────────────────────

def _card_color(cat: str) -> tuple[str, str]:
    """Return (border_color, badge_bg) for category."""
    return {
        "Momentum":           ("#2a78d6", "#E6F1FB", "#0C447C"),
        "Momentum-Pullback":  ("#1baf7a", "#E1F5EE", "#085041"),
        "VCP Setup":          ("#4a3aa7", "#EEEDFE", "#26215C"),
        "Turnaround":         ("#eda100", "#FAEEDA", "#633806"),
        "Longterm Hold":      ("#639922", "#EAF3DE", "#173404"),
        "Avoid":              ("#888780", "#F1EFE8", "#444441"),
    }.get(cat, ("#888780", "#F1EFE8", "#444441"))


def _fmt(val, prefix="$", decimals=2) -> str:
    if val is None:
        return "—"
    try:
        return f"{prefix}{float(val):,.{decimals}f}"
    except Exception:
        return str(val)


def _pct(val) -> str:
    if val is None:
        return "—"
    try:
        v = float(val)
        return f"{'+' if v >= 0 else ''}{v:.1f}%"
    except Exception:
        return str(val)


def _stock_card(row: dict, section: str) -> str:
    ticker  = _v(row, "Ticker", "?")
    price   = _v(row, "Current Price", 0) or 0
    cat     = _v(row, "Category", "—")
    rs      = _v(row, "RS")
    rsi     = _v(row, "RSI_14")
    adx     = _v(row, "ADX_14")
    bb      = _v(row, "BB_PctB")
    rvol    = _v(row, "RVOL_Intraday") or _v(row, "RVOL")   # prefer time-adjusted
    atr_pct = _v(row, "ATR_Pct")
    dist    = _v(row, "Dist_52W_High%")
    above_vwap = _v(row, "Above_VWAP", False)
    atr_shrink = _v(row, "ATR Shrinking", False)
    earn    = _v(row, "EarningsDate", "N/A")
    earn_b  = _v(row, "EarningsBeat", False)
    short_i = _v(row, "Short_Interest%")
    ma50    = _v(row, "50MA")
    ma200   = _v(row, "200MA")
    ema8    = _v(row, "8EMA")
    pd_low  = _v(row, "Prev-Day Low")
    pd_high = _v(row, "Prev-Day High")
    vwap    = _v(row, "VWAP")
    pvol    = _v(row, "Pullback_Vol_Ratio")
    score   = _v(row, "_dashboard_score", 0)
    above200 = _v(row, "Above_200MA", False)
    p50pct  = _v(row, "Price_vs_50MA%")

    colors = {
        "Momentum":          ("#185FA5", "#E6F1FB"),
        "Momentum-Pullback": ("#0F6E56", "#E1F5EE"),
        "VCP Setup":         ("#26215C", "#EEEDFE"),
        "Turnaround":        ("#633806", "#FAEEDA"),
    }
    txt_c, bg_c = colors.get(cat, ("#444441", "#F1EFE8"))

    # Section-specific signals. lv_* hold the numeric levels backing the text,
    # used below for the R:R / position-size line.
    lv_e = lv_s = lv_t1 = lv_t2 = None
    if section == "Day Trade":
        orb_h, orb_l = _v(row, "ORB_High"), _v(row, "ORB_Low")
        orb_status   = _v(row, "ORB_Status")
        dl = day_trade_levels(row) or {}
        lv_e, lv_s  = dl.get("entry"), dl.get("stop")
        lv_t1, lv_t2 = dl.get("t1"), dl.get("t2")
        trigger = (f"buy ORB high {_fmt(orb_h)} breakout (range {_fmt(orb_l)}–{_fmt(orb_h)}, "
                   f"now {orb_status}), confirm over Prev-Day High {_fmt(pd_high)}"
                   if orb_h else
                   f"buy Prev-Day High {_fmt(pd_high)} breakout")
        entry   = (f"Above VWAP {_fmt(vwap)} — {trigger} with RVOL>1.5"
                   if above_vwap else
                   f"Wait for VWAP reclaim {_fmt(vwap)}, then {trigger}")
        stop    = (f"Below ORB low {_fmt(orb_l)} / Prev-Day Low {_fmt(pd_low)} — "
                   f"hard stop: {_fmt(price * 0.95 if price else None)} (-5%)"
                   if orb_l else
                   f"Below Prev-Day Low {_fmt(pd_low)} — hard stop: {_fmt(price * 0.95 if price else None)} (-5%)")
        t2_lab  = "8EMA " if (lv_t2 is not None and lv_t2 == ema8) else ""
        t2_pct  = _pct((lv_t2 / lv_e - 1) * 100) if (lv_t2 and lv_e) else "—"
        target  = f"T1 {_fmt(lv_t1)} (+3%), T2 {t2_lab}{_fmt(lv_t2)} ({t2_pct}). Trail VWAP."

    elif section == "Swing Trade":
        # Prefer the plan from grade_signals.enrich_rows — it carries the
        # R:R and earnings gates the heuristics below know nothing about.
        enr = [_v(row, k) for k in ("Entry", "Stop", "Target")]
        if (all(isinstance(x, str) and x.strip() and x.strip() != "N/A" for x in enr)
                and "No trade" not in enr[0]):
            entry, stop, target = (x if len(x) <= 220 else x[:220] + "…" for x in enr)
            lv = _v(row, "_levels") or {}
            lv_e, lv_s  = lv.get("entry"), lv.get("stop")
            lv_t1, lv_t2 = lv.get("t1"), lv.get("t2")
        else:
            below50 = (ma50 or 0) > price
            below8  = bool(ema8) and price < ema8
            entry   = (f"50MA reclaim ${ma50:.2f} with RVOL>1.3" if (below50 and ma50) else
                       f"8EMA reclaim ${ema8:.2f} — no entry while below it" if below8 else
                       f"8EMA hold ${ema8:.2f} — add on 21EMA dip" if ema8 else
                       f"Buy above Prev-Day High {_fmt(pd_high)}")
            # 1.5×ATR stop, capped at -8% — uncapped it printed absurd stops
            # (e.g. -18%) on high-ATR names, dwarfing any realistic target
            atr_stop_pct = min(atr_pct * 1.5, 8.0) if atr_pct else None
            stop    = (f"Below Prev-Day Low {_fmt(pd_low)}. ATR stop: ${price*(1-atr_stop_pct/100):.2f}"
                       if atr_stop_pct else f"Below {_fmt(pd_low)}")
            # T1 needs headroom above the entry area, not just above price —
            # else a "reclaim 8EMA" entry gets the same 8EMA as its target
            t1_cands = [x for x in (ema8, ma50) if x and x > price * 1.05]
            t1 = min(t1_cands) if t1_cands else price * 1.10
            t2_cands = [x for x in (ma50, ma200, price * 1.20) if x and x > t1 * 1.03]
            t2 = min(t2_cands)
            target  = f"T1 ${t1:.2f} ({_pct((t1/price-1)*100)}), T2 ${t2:.2f} ({_pct((t2/price-1)*100)}). Trail 21EMA."
            lv_e, lv_t1, lv_t2 = price, t1, t2
            lv_s = price * (1 - atr_stop_pct / 100) if atr_stop_pct else pd_low

    elif section == "Calls":
        hint  = _v(row, "Call_Strike_Hint", "") or ""
        entry = hint[:120] if hint else f"OTM calls +5%: ${price*1.05:.2f}, expiry 45-60 DTE"
        stop  = f"Max loss = premium paid. Exit at -50% on option."
        target= f"T1 50MA ${ma50:.2f} ({_pct((ma50/price-1)*100) if ma50 else '?'}). Take 50% off at +50% gain."

    elif section == "Puts":
        entry = f"Slightly OTM puts: ${price*0.95:.2f} strike, 30-45 DTE. Enter on RVOL fade <0.8"
        stop  = f"Max loss = premium paid. Exit if stock breaks back above ${pd_high:.2f} (Prev-Day High)."
        target= f"T1 8EMA ${ema8:.2f} ({_pct((ema8/price-1)*100) if ema8 else '?'}). Take profit at 8EMA retest."
    else:
        entry = stop = target = "—"

    # Flags row
    flags = []
    if above_vwap:  flags.append(('<span style="color:#0F6E56">▲ VWAP</span>'))
    if atr_shrink:  flags.append(('<span style="color:#26215C">ATR▼</span>'))
    if earn_b:      flags.append(('<span style="color:#185FA5">Earn✓</span>'))
    if short_i and short_i > 10: flags.append(f'<span style="color:#A32D2D">SI {short_i:.0f}%</span>')
    if above200:    flags.append(('<span style="color:#3B6D11">200MA✓</span>'))
    if rvol and rvol > 1.5: flags.append(f'<span style="color:#185FA5">RVOL {rvol:.1f}</span>')

    rs_color = "#0F6E56" if (rs or 0) >= 0 else "#A32D2D"
    grade = _score_to_grade(score, section)
    grade_txt, grade_bg = _GRADE_COLORS.get(grade, ("#444441", "#F1EFE8"))

    # Gate warnings from grade_signals (R:R fail, earnings blackout, downgrades)
    # must be visible on the card — a high section score can't be allowed to
    # hide a SKIP verdict.
    notes = _v(row, "Notes", "") or ""
    warn  = next((n.strip() for n in notes.split("|") if "⛔" in n or "⚠" in n), None)
    if warn:
        w_bg, w_txt = ("#FCEBEB", "#791F1F") if "⛔" in warn else ("#FAEEDA", "#633806")
        if len(warn) > 160:
            warn = warn[:160] + "…"
        warn_html = (f'<div style="background:{w_bg};color:{w_txt};font-size:10px;'
                     f'padding:4px 8px;border-radius:5px;margin-bottom:8px;'
                     f'line-height:1.4">{_html.escape(warn)}</div>')
    else:
        warn_html = ""

    # Plan text can contain "<" (e.g. "R:R < 1.0", "BB_PctB < 0.3") — escape it
    # or the browser eats everything after it as a malformed tag.
    entry, stop, target = (_html.escape(str(x)) for x in (entry, stop, target))

    # R:R + fixed-risk position size
    size_flag = _v(row, "SizeFlag")
    if section in ("Day Trade", "Swing Trade"):
        rr_size_html = _risk_size_html(lv_e, lv_s, lv_t1, lv_t2, size_flag)
    else:  # options — max loss is the premium, so size = the risk budget itself
        # The swing SizeFlag reflects the LONG-side verdict; an "Avoid" stock is
        # a perfectly valid put candidate, so NONE must not zero the budget.
        # Derive from volatility instead: quarter budget on high-ATR names.
        flag = (size_flag or "").upper()
        if flag not in ("FULL", "HALF", "QUARTER"):
            flag = "QUARTER" if (atr_pct or 0) > 10 else "HALF"
        budget = ACCOUNT_SIZE * RISK_PER_TRADE_PCT / 100 * _SIZE_MULT.get(flag, 0.5)
        rr_size_html = (
            f'<div style="display:flex;gap:6px;align-items:flex-start;margin-top:4px;font-size:11px">'
            f'<span style="min-width:42px;text-align:center;background:#E1F5EE;color:#085041;'
            f'padding:1px 5px;border-radius:3px;font-size:10px;font-weight:500;flex-shrink:0">R:R·SIZE</span>'
            f'<span style="color:#52514e;line-height:1.4">Max premium ${budget:,.0f} total '
            f'({flag} = {RISK_PER_TRADE_PCT * _SIZE_MULT.get(flag, 0.5):g}% of ${ACCOUNT_SIZE:,.0f}) — '
            f'max loss = premium paid</span></div>'
        )

    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:14px 16px;break-inside:avoid;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
        <div>
          <span style="font-size:17px;font-weight:500;color:#0b0b0b">{ticker}</span>
          <span style="font-size:13px;color:#898781;margin-left:6px">${price:,.2f}</span>
        </div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end">
          <span style="background:{bg_c};color:{txt_c};font-size:10px;font-weight:500;
                       padding:2px 7px;border-radius:4px">{cat}</span>
          <span style="background:#F1EFE8;color:#444441;font-size:10px;font-weight:500;
                       padding:2px 7px;border-radius:4px">Score {score:.0f}</span>
          <span style="background:{grade_bg};color:{grade_txt};font-size:10px;font-weight:600;
                       padding:2px 7px;border-radius:4px">{grade}</span>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:6px;margin-bottom:10px">
        <div style="text-align:center;background:#f9f9f7;border-radius:6px;padding:5px">
          <div style="font-size:12px;font-weight:500;color:{rs_color}">{_pct(rs)}</div>
          <div style="font-size:9px;color:#898781">RS vs QQQ</div>
        </div>
        <div style="text-align:center;background:#f9f9f7;border-radius:6px;padding:5px">
          <div style="font-size:12px;font-weight:500;color:#0b0b0b">{f'{rsi:.0f}' if rsi else '—'}</div>
          <div style="font-size:9px;color:#898781">RSI</div>
        </div>
        <div style="text-align:center;background:#f9f9f7;border-radius:6px;padding:5px">
          <div style="font-size:12px;font-weight:500;color:#0b0b0b">{f'{adx:.0f}' if adx else '—'}</div>
          <div style="font-size:9px;color:#898781">ADX</div>
        </div>
        <div style="text-align:center;background:#f9f9f7;border-radius:6px;padding:5px">
          <div style="font-size:12px;font-weight:500;
                      color:{'#0F6E56' if bb is not None and bb < 0.3 else '#0b0b0b'}">{f'{bb:.3f}' if bb is not None else '—'}</div>
          <div style="font-size:9px;color:#898781">BB %B</div>
        </div>
      </div>

      <div style="font-size:10px;display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
        {' · '.join(flags) if flags else '<span style="color:#898781">no flags</span>'}
      </div>
      {warn_html}
      <div style="border-top:0.5px solid #e1e0d9;padding-top:8px">
        <div style="display:flex;gap:6px;align-items:flex-start;margin-bottom:4px;font-size:11px">
          <span style="min-width:42px;text-align:center;background:#E1F5EE;color:#085041;
                       padding:1px 5px;border-radius:3px;font-size:10px;font-weight:500;flex-shrink:0">ENTRY</span>
          <span style="color:#52514e;line-height:1.4">{entry}</span>
        </div>
        <div style="display:flex;gap:6px;align-items:flex-start;margin-bottom:4px;font-size:11px">
          <span style="min-width:42px;text-align:center;background:#FCEBEB;color:#791F1F;
                       padding:1px 5px;border-radius:3px;font-size:10px;font-weight:500;flex-shrink:0">STOP</span>
          <span style="color:#52514e;line-height:1.4">{stop}</span>
        </div>
        <div style="display:flex;gap:6px;align-items:flex-start;font-size:11px">
          <span style="min-width:42px;text-align:center;background:#E6F1FB;color:#0C447C;
                       padding:1px 5px;border-radius:3px;font-size:10px;font-weight:500;flex-shrink:0">TARGET</span>
          <span style="color:#52514e;line-height:1.4">{target}</span>
        </div>
        {rr_size_html}
      </div>

      <div style="margin-top:7px;font-size:10px;color:#898781">
        Earns {earn} {'✓' if earn_b else ''} ·
        Dist {_pct(dist)} from 52W high ·
        RVOL {f'{rvol:.2f}' if rvol else '—'}
      </div>
    </div>"""


def _section(title: str, color: str, icon: str, rows: list[dict], section_key: str) -> str:
    if not rows:
        return f"""
        <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;padding:20px">
          <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0 0 6px">
            {icon} {title}
          </h3>
          <p style="font-size:13px;color:#898781;margin:0">No candidates found — try loosening thresholds.</p>
        </div>"""

    cards_html = "".join(_stock_card(r, section_key) for r in rows)
    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;padding:16px 18px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:14px">
        <span style="font-size:18px">{icon}</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">{title}</h3>
        <span style="margin-left:auto;background:#f1efea;color:#898781;font-size:11px;
                     padding:2px 8px;border-radius:4px">Top {len(rows)}</span>
      </div>
      {cards_html}
    </div>"""


def _market_pulse_html(pulse: dict) -> str:
    """Render the market-pulse header strip (VIX, SPY/QQQ strength, top-10 mega
    caps by allocation, catalysts, upcoming econ events)."""
    if not pulse:
        return ""
    vix, spy, qqq = pulse.get("vix") or {}, pulse.get("spy") or {}, pulse.get("qqq") or {}
    top10 = pulse.get("top10") or []

    st_colors = {"STRONG": ("#E1F5EE", "#085041"), "NEUTRAL": ("#F1EFE8", "#444441"),
                 "WEAK": ("#FCEBEB", "#791F1F")}

    def _idx_tile(name, d):
        if not d:
            return (f'<div style="flex:1;min-width:170px"><div style="font-size:11px;color:#898781">{name}</div>'
                    f'<div style="font-size:15px">n/a</div></div>')
        bg, fg = st_colors.get(d.get("strength"), st_colors["NEUTRAL"])
        chg = d.get("day_chg_pct")
        chg_html = (f'<span style="color:{"#0F6E56" if chg >= 0 else "#A32D2D"}">{_pct(chg)}</span>'
                    if chg is not None else "—")
        return (
            f'<div style="flex:1;min-width:170px">'
            f'<div style="font-size:11px;color:#898781">{name}</div>'
            f'<div style="font-size:15px;font-weight:500">${d.get("price"):,.2f} {chg_html} '
            f'<span style="background:{bg};color:{fg};font-size:10px;font-weight:600;'
            f'padding:2px 7px;border-radius:4px;margin-left:4px">{d.get("strength", "?")}</span></div>'
            f'<div style="font-size:10px;color:#898781">'
            f'{"above" if d.get("above_50ma") else "below"} 50MA · '
            f'{"above" if d.get("above_20ma") else "below"} 20MA · '
            f'5d {_pct(d.get("ret_5d_pct"))} · mega-cap breadth '
            f'{d.get("breadth_pct")}% (wt&gt;50MA)</div></div>'
        )

    v_level = vix.get("level")
    v_color = ("#A32D2D" if (v_level or 0) >= 25 else
               "#8a6d1a" if (v_level or 0) >= 20 else "#0F6E56")
    v_chg = vix.get("change")
    vix_tile = (
        f'<div style="flex:1;min-width:150px">'
        f'<div style="font-size:11px;color:#898781">VIX</div>'
        f'<div style="font-size:15px;font-weight:500;color:{v_color}">'
        f'{v_level if v_level is not None else "n/a"}'
        f'{f" ({v_chg:+.2f})" if v_chg is not None else ""}</div>'
        f'<div style="font-size:10px;color:#898781">{_html.escape(str(vix.get("label") or ""))}</div></div>'
    )

    # Futures tile — live direction pre-market / overnight when SPY is stale
    futures = pulse.get("futures") or []
    if futures:
        fut_bits = []
        for f in futures:
            c = "#0F6E56" if f["chg_pct"] >= 0 else "#A32D2D"
            fut_bits.append(f'<b>{_html.escape(f["label"])}</b> '
                            f'<span style="color:{c}">{_pct(f["chg_pct"])}</span>')
        futures_tile = (
            f'<div style="flex:1;min-width:150px">'
            f'<div style="font-size:11px;color:#898781">Futures (24h)</div>'
            f'<div style="font-size:13px;font-weight:500;line-height:1.6">'
            f'{" · ".join(fut_bits)}</div>'
            f'<div style="font-size:10px;color:#898781">use pre-market — SPY/QQQ quotes lag</div></div>'
        )
    else:
        futures_tile = ""

    chips = []
    for s in top10:
        chg = s.get("day_chg_pct")
        c = "#0F6E56" if (chg or 0) >= 0 else "#A32D2D"
        chips.append(
            f'<span style="background:#f9f9f7;border:0.5px solid #e1e0d9;border-radius:6px;'
            f'padding:3px 8px;font-size:11px;white-space:nowrap">'
            f'<b>{s["ticker"]}</b> <span style="color:#898781">{s.get("weight_pct")}%</span> '
            f'<span style="color:{c}">{_pct(chg)}</span></span>')

    # Sector rotation chips, strongest → weakest
    sector_chips = []
    for s in pulse.get("sectors") or []:
        c = "#0F6E56" if s["chg_pct"] >= 0 else "#A32D2D"
        sector_chips.append(
            f'<span style="background:#f9f9f7;border:0.5px solid #e1e0d9;border-radius:6px;'
            f'padding:3px 8px;font-size:11px;white-space:nowrap">'
            f'{_html.escape(s["label"])} <b>{s["ticker"]}</b> '
            f'<span style="color:{c}">{_pct(s["chg_pct"])}</span></span>')

    news_lines = []
    for s in top10:
        cat = s.get("catalyst")
        if cat and cat not in ("General News", "No News Found"):
            headline = _html.escape((s.get("headline") or "")[:140])
            news_lines.append(
                f'<div style="font-size:11px;color:#52514e;margin-top:3px;line-height:1.4">'
                f'⚡ <b>{s["ticker"]}</b> ({s.get("weight_pct")}%) — '
                f'<span style="color:#185FA5">{_html.escape(cat)}</span>: {headline}</div>')

    econ_lines = []
    for e in pulse.get("econ_events") or []:
        fc = f' (forecast {_html.escape(str(e["forecast"]))})' if e.get("forecast") else ""
        econ_lines.append(
            f'<div style="font-size:11px;color:#52514e;margin-top:3px">'
            f'📅 {e["when"].strftime("%a %m/%d %H:%M ET")} — <b>{_html.escape(e["title"])}</b>{fc}</div>')

    # Fed rate outlook (FedWatch-style, from fed funds futures)
    fed = pulse.get("fed") or {}
    if fed.get("current_implied") is not None:
        try:
            from stockanalysis.scanners.market_movers import summarize_fed_outlook
            summary = summarize_fed_outlook(fed)
        except Exception:
            summary = f"now ~{fed['current_implied']:.2f}%"
        # color by the furthest-month direction: hikes red, cuts green
        last_chg = (fed.get("months") or [{}])[-1].get("change_bps", 0)
        f_color = "#A32D2D" if last_chg >= 13 else "#0F6E56" if last_chg <= -13 else "#52514e"
        econ_lines.append(
            f'<div style="font-size:11px;color:{f_color};margin-top:3px">'
            f'🏦 <b>Fed outlook</b> (fed funds futures): {_html.escape(summary)}</div>')

    return f"""
<div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
            padding:14px 16px;margin-bottom:16px">
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:10px">
    {vix_tile}
    {_idx_tile("SPY", spy)}
    {_idx_tile("QQQ", qqq)}
    {futures_tile}
  </div>
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
    {''.join(chips)}
  </div>
  {(f'<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">'
    f'<span style="font-size:10px;color:#898781;margin-right:2px">SECTORS</span>'
    + ''.join(sector_chips) + '</div>') if sector_chips else ''}
  {''.join(news_lines)}
  {''.join(econ_lines)}
</div>"""


GAPPER_MIN_PCT = float(os.environ.get("GAPPER_MIN_PCT", "2.0"))


def _gappers_html(rows: list[dict], fetch_catalysts: bool = True) -> str:
    """
    Full-width "Gappers" table: scanned tickers gapping ≥ GAPPER_MIN_PCT
    (true open gap when today's bar exists, else the pre-market implied gap),
    ranked by |gap| × RVOL, with the news catalyst attached.
    Returns '' when nothing qualifies.
    """
    gappers = []
    for r in rows:
        gap = r.get("Gap%") if r.get("Gap%") is not None else r.get("Gap_Now%")
        if gap is None or abs(gap) < GAPPER_MIN_PCT:
            continue
        rvol = _v(r, "RVOL_Intraday") or _v(r, "RVOL") or 0
        gappers.append((abs(gap) * max(rvol, 0.5), gap, rvol, r))
    if not gappers:
        return ""
    gappers.sort(key=lambda g: g[0], reverse=True)
    gappers = gappers[:10]

    catalysts = {}
    if fetch_catalysts:
        try:
            from stockanalysis.scanners.market_movers import _fetch_catalyst
            for _, _, _, r in gappers[:8]:          # cap the news calls
                try:
                    catalysts[r["Ticker"]] = _fetch_catalyst(r["Ticker"])
                except Exception:
                    pass
        except Exception:
            pass

    trs = []
    for _, gap, rvol, r in gappers:
        tk    = _v(r, "Ticker", "?")
        price = _v(r, "Current Price")
        vwap_ok = _v(r, "Above_VWAP")
        atrp  = _v(r, "ATR_Pct")
        cat, headline = catalysts.get(tk, ("", ""))
        if cat in ("General News", "No News Found"):
            cat = ""
        g_c = "#0F6E56" if gap >= 0 else "#A32D2D"
        news = (f'<span style="color:#185FA5">{_html.escape(cat)}</span> — '
                f'{_html.escape((headline or "")[:110])}' if cat else
                _html.escape((headline or "—")[:110]))
        trs.append(
            f'<tr style="border-top:0.5px solid #e1e0d9">'
            f'<td style="padding:6px 10px;font-weight:600">{tk}</td>'
            f'<td style="padding:6px 10px">{_fmt(price)}</td>'
            f'<td style="padding:6px 10px;color:{g_c};font-weight:600">{_pct(gap)}</td>'
            f'<td style="padding:6px 10px">{rvol:.2f}</td>'
            f'<td style="padding:6px 10px">{"▲" if vwap_ok else "▼" if vwap_ok is False else "—"} VWAP</td>'
            f'<td style="padding:6px 10px">{f"{atrp:.1f}%" if atrp is not None else "—"}</td>'
            f'<td style="padding:6px 10px;font-size:11px;color:#52514e">{news}</td></tr>')

    return f"""
<div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
            padding:14px 16px;margin-bottom:16px">
  <div style="font-size:14px;font-weight:600;margin-bottom:8px">
    ⚡ Gappers (≥{GAPPER_MIN_PCT:g}%, ranked by gap × RVOL)</div>
  <table style="width:100%;border-collapse:collapse;font-size:12px">
    <tr style="color:#898781;font-size:10px;text-align:left">
      <th style="padding:4px 10px">TICKER</th><th style="padding:4px 10px">PRICE</th>
      <th style="padding:4px 10px">GAP</th><th style="padding:4px 10px">RVOL</th>
      <th style="padding:4px 10px">VWAP</th><th style="padding:4px 10px">ATR%</th>
      <th style="padding:4px 10px">CATALYST</th></tr>
    {''.join(trs)}
  </table>
</div>"""


CATEGORY_ORDER = ["Momentum", "Momentum-Pullback", "VCP Setup",
                  "Turnaround", "Longterm Hold", "Avoid", "Error"]

_CATEGORY_COLORS = {
    "Momentum":          "#185FA5", "Momentum-Pullback": "#0F6E56",
    "VCP Setup":         "#26215C", "Turnaround":        "#633806",
    "Longterm Hold":     "#3B6D11", "Avoid":             "#898781",
    "Error":             "#A32D2D",
}


def _categories_html(rows: list[dict]) -> str:
    """Full category breakdown — every scanned ticker, grouped by category,
    as collapsible tables below the Top-5 grid. Avoid/Error start collapsed."""
    sections = []
    for cat in CATEGORY_ORDER:
        sub = [r for r in rows if _v(r, "Category") == cat]
        if not sub:
            continue
        sub.sort(key=lambda r: _v(r, "Rank_Score", 0) or 0, reverse=True)
        color = _CATEGORY_COLORS.get(cat, "#444441")
        is_open = "" if cat in ("Avoid", "Error") else " open"

        trs = []
        for r in sub:
            rvol   = _v(r, "RVOL_Intraday") or _v(r, "RVOL")
            reason = _html.escape(str(_v(r, "Cat_Reason", ""))[:110])
            grade  = _v(r, "Grade", "—") or "—"
            g_txt, g_bg = _GRADE_COLORS.get(grade, ("#444441", "#F1EFE8"))
            def n(key, fmt="{:.1f}"):
                val = _v(r, key)
                return fmt.format(val) if isinstance(val, (int, float)) else "—"
            trs.append(
                f'<tr style="border-top:0.5px solid #e1e0d9">'
                f'<td style="padding:5px 10px;font-weight:600">{_v(r, "Ticker", "?")}</td>'
                f'<td style="padding:5px 10px">{_fmt(_v(r, "Current Price"))}</td>'
                f'<td style="padding:5px 10px"><span style="background:{g_bg};color:{g_txt};'
                f'font-size:10px;font-weight:600;padding:1px 6px;border-radius:3px">{grade}</span></td>'
                f'<td style="padding:5px 10px">{_v(r, "Rank_Score", "—")}</td>'
                f'<td style="padding:5px 10px">{n("Dist_52W_High%")}%</td>'
                f'<td style="padding:5px 10px">{f"{rvol:.2f}" if isinstance(rvol, (int, float)) else "—"}</td>'
                f'<td style="padding:5px 10px">{n("RSI_14", "{:.0f}")}</td>'
                f'<td style="padding:5px 10px">{n("ADX_14", "{:.0f}")}</td>'
                f'<td style="padding:5px 10px">{n("RS")}</td>'
                f'<td style="padding:5px 10px">{n("ATR_Pct")}%</td>'
                f'<td style="padding:5px 10px;font-size:11px;color:#52514e">{reason}</td></tr>')

        sections.append(f"""
<details{is_open} style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                 padding:10px 16px;margin-bottom:12px">
  <summary style="cursor:pointer;font-size:14px;font-weight:600;color:{color}">
    {cat} ({len(sub)})</summary>
  <div style="overflow-x:auto">
  <table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:8px">
    <tr style="color:#898781;font-size:10px;text-align:left">
      <th style="padding:4px 10px">TICKER</th><th style="padding:4px 10px">PRICE</th>
      <th style="padding:4px 10px">GRADE</th><th style="padding:4px 10px">SCORE</th>
      <th style="padding:4px 10px">52W HIGH</th><th style="padding:4px 10px">RVOL</th>
      <th style="padding:4px 10px">RSI</th><th style="padding:4px 10px">ADX</th>
      <th style="padding:4px 10px">RS</th><th style="padding:4px 10px">ATR%</th>
      <th style="padding:4px 10px">REASON</th></tr>
    {''.join(trs)}
  </table>
  </div>
</details>""")

    if not sections:
        return ""
    return ('<h2 style="font-size:16px;font-weight:600;margin:24px 0 12px">'
            'All Categories</h2>' + "".join(sections))


def generate_dashboard(
    rows: list[dict],
    output_dir: str | Path = ".",
    open_browser: bool = True,
    include_market_pulse: bool = True,
) -> str:
    """
    Build a Top-5 HTML dashboard from enriched scan rows.

    Parameters
    ----------
    rows         : list of dicts from get_metrics + categorize + enrich_row + put/call candidate
    output_dir   : folder to write the HTML file
    open_browser : auto-open in default browser (default True)

    Returns
    -------
    str : absolute path to the generated HTML file
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    run_time = datetime.now().strftime("%B %d, %Y  %H:%M")
    filepath = output_dir / f"dashboard_{ts}.html"

    # Compute top 5 for each section
    top_day   = _top5(rows, _day_trade_score,   min_score=20)
    top_swing = _top5(rows, _swing_trade_score, min_score=20)
    top_calls = _top5(rows, _call_score,        min_score=0)
    top_puts  = _top5(rows, _put_score,         min_score=0)

    # Summary stats
    total       = len(rows)
    mom_count   = sum(1 for r in rows if _v(r,"Category")=="Momentum")
    mp_count    = sum(1 for r in rows if _v(r,"Category")=="Momentum-Pullback")
    ta_count    = sum(1 for r in rows if _v(r,"Category")=="Turnaround")
    call_count  = sum(1 for r in rows if _v(r,"Call_Candidate"))
    put_count   = sum(1 for r in rows if _v(r,"Put_Candidate"))
    avoid_count = sum(1 for r in rows if _v(r,"Category")=="Avoid")
    qqq_rs      = "QQQ benchmark run"

    day_html   = _section("Day Trade Top 5",    "#2a78d6", "⚡", top_day,   "Day Trade")
    swing_html = _section("Swing Trade Top 5",  "#1baf7a", "📈", top_swing, "Swing Trade")
    calls_html = _section("Call Options Top 5", "#639922", "🟢", top_calls, "Calls")
    puts_html  = _section("Put Options Top 5",  "#e34948", "🔴", top_puts,  "Puts")

    # Market pulse header — VIX, SPY/QQQ strength, top-10 mega caps, catalysts.
    # Network-dependent, so a failure just drops the strip, never the dashboard.
    pulse_html = ""
    if include_market_pulse:
        try:
            from stockanalysis.scanners.market_movers import market_pulse
            pulse_html = _market_pulse_html(market_pulse())
        except Exception as e:
            print(f"[Dashboard] market pulse unavailable ({e}) — header skipped")

    # Gappers table — catalysts need the network, so reuse the pulse flag
    try:
        gappers_html = _gappers_html(rows, fetch_catalysts=include_market_pulse)
    except Exception as e:
        print(f"[Dashboard] gappers section failed ({e}) — skipped")
        gappers_html = ""

    # Full category breakdown (every scanned ticker)
    try:
        categories_html = _categories_html(rows)
    except Exception as e:
        print(f"[Dashboard] categories section failed ({e}) — skipped")
        categories_html = ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stock Scan Dashboard — {ts}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #f5f4f0; color: #0b0b0b; padding: 24px; }}
  h1 {{ font-size: 22px; font-weight: 500; margin-bottom: 4px; }}
  .sub {{ font-size: 13px; color: #898781; margin-bottom: 20px; }}
  .kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
              gap: 10px; margin-bottom: 20px; }}
  .kpi {{ background: white; border: 0.5px solid #e1e0d9; border-radius: 8px;
          padding: 10px 12px; }}
  .kpi-n {{ font-size: 22px; font-weight: 500; }}
  .kpi-l {{ font-size: 11px; color: #898781; margin-top: 1px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  @media print {{
    body {{ background: white; padding: 12px; }}
    .grid {{ grid-template-columns: 1fr 1fr; }}
  }}
</style>
</head>
<body>

<h1>Stock Scan Dashboard</h1>
<p class="sub">Generated {run_time} · {total} tickers scanned</p>

{pulse_html}

<div class="kpi-row">
  <div class="kpi"><div class="kpi-n">{total}</div><div class="kpi-l">Scanned</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#185FA5">{mom_count}</div><div class="kpi-l">Momentum</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#0F6E56">{mp_count}</div><div class="kpi-l">MP</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#633806">{ta_count}</div><div class="kpi-l">Turnaround</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#3B6D11">{call_count}</div><div class="kpi-l">Call signals</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#A32D2D">{put_count}</div><div class="kpi-l">Put signals</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#898781">{avoid_count}</div><div class="kpi-l">Avoided</div></div>
</div>

{gappers_html}

<div class="grid">
  {day_html}
  {swing_html}
  {calls_html}
  {puts_html}
</div>

{categories_html}

<p style="margin-top:20px;font-size:11px;color:#898781;text-align:center">
  Not financial advice. All signals are algorithmic — verify before trading.
  Stops and targets are suggestions based on ATR and key levels.
</p>

</body>
</html>"""

    filepath.write_text(html, encoding="utf-8")
    print(f"[Dashboard] Saved → {filepath.resolve()}")

    if open_browser:
        webbrowser.open(filepath.resolve().as_uri())

    return str(filepath.resolve())


# ── Integration snippet for stock_categorizer.py main() ──────────────────────
INTEGRATION_SNIPPET = '''
# Add to imports at top of stock_categorizer.py:
from stockanalysis.reporting.dashboard import generate_dashboard

# Add at the end of main(), after save_metrics_to_csv():
generate_dashboard(rows, output_dir=out_dir, open_browser=True)
'''


# ── Standalone test ───────────────────────────────────────────────────────────
def main() -> None:
    """Standalone entry point — run this module directly."""
    sample = [
        {"Ticker":"NVDA","Category":"Momentum-Pullback","Current Price":192.53,
         "RS":-10.66,"ADX_14":16.1,"RSI_14":37.5,"BB_PctB":0.042,"ATR_Pct":4.13,"ATR20":7.94,
         "Dist_52W_High%":-18.6,"Days_Since_52W_High":45,"Above_200MA":True,"Above_VWAP":False,
         "RVOL":1.10,"Vol_vs_20D":1.05,"VolumeDryingUp":False,"Pullback_Vol_Ratio":0.89,
         "ATR Shrinking":True,"8EMA":200.56,"21EMA":205.88,"50MA":210.09,"200MA":190.64,
         "Prev-Day Low":191.22,"Prev-Day High":195.74,"VWAP":193.38,
         "EarningsDate":"2026-08-26","EarningsBeat":True,"Short_Interest%":1.3,
         "Price_vs_50MA%":-8.4,"Entry_Gate_Pass":True,
         "Call_Candidate":True,"Call_Score":15,"Call_Strength":"STRONG",
         "Call_Strike_Hint":"Strike $202 (+5%) or $211 (+10%). 45-60 DTE. Earns 8/26.",
         "Put_Candidate":False,"Put_Score":0},
        {"Ticker":"AMD","Category":"Momentum","Current Price":521.58,
         "RS":132.63,"ADX_14":24.2,"RSI_14":56.1,"BB_PctB":0.587,"ATR_Pct":6.73,"ATR20":35.12,
         "Dist_52W_High%":-7.4,"Days_Since_52W_High":6,"Above_200MA":True,"Above_VWAP":True,
         "RVOL":1.36,"Vol_vs_20D":1.64,"VolumeDryingUp":False,"Pullback_Vol_Ratio":0.83,
         "ATR Shrinking":False,"8EMA":523.09,"21EMA":503.86,"50MA":439.12,"200MA":270.47,
         "Prev-Day Low":502.61,"Prev-Day High":525.11,"VWAP":518.83,
         "EarningsDate":"2026-08-04","EarningsBeat":True,"Short_Interest%":2.9,
         "Price_vs_50MA%":18.8,"Entry_Gate_Pass":True,
         "Call_Candidate":False,"Call_Score":0,"Call_Strength":"N/A",
         "Call_Strike_Hint":"N/A","Put_Candidate":False,"Put_Score":0},
        {"Ticker":"SMCI","Category":"Turnaround","Current Price":30.63,
         "RS":13.83,"ADX_14":22.9,"RSI_14":44.0,"BB_PctB":0.295,"ATR_Pct":12.76,"ATR20":3.91,
         "Dist_52W_High%":-50.9,"Days_Since_52W_High":332,"Above_200MA":False,"Above_VWAP":False,
         "RVOL":0.92,"Vol_vs_20D":0.64,"VolumeDryingUp":False,"Pullback_Vol_Ratio":0.64,
         "ATR Shrinking":True,"8EMA":32.16,"21EMA":33.96,"50MA":33.50,"200MA":35.35,
         "Prev-Day Low":30.28,"Prev-Day High":31.71,"VWAP":30.89,
         "EarningsDate":"2026-08-04","EarningsBeat":True,"Short_Interest%":19.4,
         "Price_vs_50MA%":-8.6,"Entry_Gate_Pass":True,
         "Call_Candidate":True,"Call_Score":18,"Call_Strength":"STRONG",
         "Call_Strike_Hint":"Strike $32.16 (+5%). Earns 8/4. Short int 19.4%.",
         "Put_Candidate":False,"Put_Score":1},
        {"Ticker":"MRVL","Category":"Momentum-Pullback","Current Price":266.77,
         "RS":155.58,"ADX_14":34.4,"RSI_14":52.6,"BB_PctB":0.399,"ATR_Pct":12.55,"ATR20":33.47,
         "Dist_52W_High%":-19.1,"Days_Since_52W_High":10,"Above_200MA":True,"Above_VWAP":False,
         "RVOL":0.92,"Vol_vs_20D":0.58,"VolumeDryingUp":True,"Pullback_Vol_Ratio":0.58,
         "ATR Shrinking":False,"8EMA":281.11,"21EMA":266.48,"50MA":213.21,"200MA":118.51,
         "Prev-Day Low":262.00,"Prev-Day High":274.20,"VWAP":267.13,
         "EarningsDate":"2026-08-27","EarningsBeat":True,"Short_Interest%":5.3,
         "Price_vs_50MA%":25.1,"Entry_Gate_Pass":True,
         "Call_Candidate":True,"Call_Score":16,"Call_Strength":"STRONG",
         "Call_Strike_Hint":"Strike $280 (+5%). Earns 8/27. RS=155 strong rotation.",
         "Put_Candidate":False,"Put_Score":0},
        {"Ticker":"QBTS","Category":"Turnaround","Current Price":22.76,
         "RS":38.15,"ADX_14":16.6,"RSI_14":46.5,"BB_PctB":0.261,"ATR_Pct":11.28,"ATR20":2.57,
         "Dist_52W_High%":-51.3,"Days_Since_52W_High":256,"Above_200MA":False,"Above_VWAP":True,
         "RVOL":0.86,"Vol_vs_20D":0.92,"VolumeDryingUp":False,"Pullback_Vol_Ratio":0.77,
         "ATR Shrinking":True,"8EMA":23.50,"21EMA":24.11,"50MA":23.22,"200MA":24.06,
         "Prev-Day Low":20.93,"Prev-Day High":22.86,"VWAP":22.50,
         "EarningsDate":"2026-08-06","EarningsBeat":True,"Short_Interest%":17.2,
         "Price_vs_50MA%":-2.0,"Entry_Gate_Pass":True,
         "Call_Candidate":True,"Call_Score":20,"Call_Strength":"STRONG",
         "Call_Strike_Hint":"Strike $23.90 (+5%). Short int 17.2% — squeeze fuel.",
         "Put_Candidate":False,"Put_Score":2},
        {"Ticker":"MU","Category":"Momentum","Current Price":1132.33,
         "RS":191.39,"ADX_14":24.3,"RSI_14":59.0,"BB_PctB":0.76,"ATR_Pct":8.76,"ATR20":99.21,
         "Dist_52W_High%":-9.8,"Days_Since_52W_High":3,"Above_200MA":True,"Above_VWAP":False,
         "RVOL":1.64,"Vol_vs_20D":1.48,"VolumeDryingUp":False,"Pullback_Vol_Ratio":1.28,
         "ATR Shrinking":False,"8EMA":1104.83,"21EMA":1016.24,"50MA":802.14,"200MA":425.84,
         "Prev-Day Low":1121.36,"Prev-Day High":1198.71,"VWAP":1153.03,
         "EarningsDate":"2026-09-23","EarningsBeat":True,"Short_Interest%":3.7,
         "Price_vs_50MA%":41.2,"Entry_Gate_Pass":True,
         "Call_Candidate":False,"Call_Score":0,"Put_Candidate":False,"Put_Score":0},
        {"Ticker":"LLY","Category":"Momentum","Current Price":1208.12,
         "RS":1.06,"ADX_14":29.9,"RSI_14":60.3,"BB_PctB":1.425,"ATR_Pct":3.24,"ATR20":39.2,
         "Dist_52W_High%":-0.6,"Days_Since_52W_High":1,"Above_200MA":True,"Above_VWAP":True,
         "RVOL":2.28,"Vol_vs_20D":2.24,"VolumeDryingUp":False,"Pullback_Vol_Ratio":1.23,
         "ATR Shrinking":False,"8EMA":1119.53,"21EMA":1103.85,"50MA":1029.69,"200MA":975.51,
         "Prev-Day Low":1128.80,"Prev-Day High":1215.57,"VWAP":1204.86,
         "EarningsDate":"2026-08-05","EarningsBeat":True,"Short_Interest%":1.1,
         "Price_vs_50MA%":17.3,"Entry_Gate_Pass":True,
         "Call_Candidate":False,"Call_Score":0,
         "Put_Candidate":True,"Put_Score":6,
         "Put_Reason":"near_52wh + extended_above_8ema + BB=1.425(above_upper)"},
    ]

    out = generate_dashboard(sample, output_dir="/tmp", open_browser=False)
    print(f"Test dashboard: {out}")
    print("\nIntegration snippet for stock_categorizer.py:")
    print(INTEGRATION_SNIPPET)


if __name__ == "__main__":
    main()
