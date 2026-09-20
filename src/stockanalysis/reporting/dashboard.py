"""
dashboard.py
============
Generates a self-contained HTML dashboard from enriched scan rows:

  - Market-pulse header: VIX, SPY/QQQ strength (own trend + allocation-weighted
    mega-cap breadth), top-10 S&P mega caps with weights, news catalysts, and
    upcoming high-impact economic events
  - Top 5 cards per section (Day Trade, Swing Trade, Long-Term, Calls, Puts)
    with the R:R-gated entry/stop/target plan from grade_signals, gate
    warnings (⛔/⚠), and a fixed-risk R:R + position-size line
    (ACCOUNT_SIZE / RISK_PER_TRADE_PCT / MAX_POSITION_PCT env vars).
    Long-Term cards are Investment_Pass names (all six primary filters green)
    showing the pass reason, LT_Entry_Timing, and tranche add levels instead
    of a tight-stop trade plan.

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
    # Long-Term shows Investment_Pass rows only, so every card already cleared
    # all six primary filters — grades split by how much bonus scoring remains
    "Long-Term":   [(95, "A+"), (85, "A"), (75, "B+"), (65, "B"), (40, "C")],
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

# Active market regime for this dashboard build — set once by
# generate_dashboard() before any card renders, read by the sizing line so
# every position size scales with the day's regime. Default = full size.
_ACTIVE_REGIME: dict = {"regime": "Neutral", "source": "none",
                        "multipliers": {"day": 1.0, "swing": 1.0, "longterm": 1.0}}

# Card section → regime multiplier horizon. Options ride the swing horizon —
# they're multi-day directional bets with premium as the risk budget.
_SECTION_HORIZON = {"Day Trade": "day", "Swing Trade": "swing",
                    "Long-Term": "longterm", "Calls": "swing", "Puts": "swing"}


def _regime_mult(section: str) -> float:
    return _ACTIVE_REGIME["multipliers"].get(
        _SECTION_HORIZON.get(section, "swing"), 1.0)


def _regime_note(section: str) -> str:
    m = _regime_mult(section)
    if m >= 1.0 or _ACTIVE_REGIME.get("source") == "none":
        return ""
    return f' · <b>×{m:g} {_ACTIVE_REGIME["regime"].upper()} regime</b>'


def _risk_size_html(entry_px, stop_px, t1_px, t2_px, size_flag,
                    section: str = "Swing Trade") -> str:
    """R:R + fixed-risk position-size line for a card, scaled by the active
    market regime's multiplier for the section's horizon. '' when levels
    invalid."""
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
    mult   = _SIZE_MULT.get(flag, 1.0) * _regime_mult(section)
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
        f'{size_txt} ({flag} = {RISK_PER_TRADE_PCT * mult:g}% of ${ACCOUNT_SIZE:,.0f}{capped})'
        f'{_regime_note(section)}</span></div>'
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


# Card ranking formulas moved to core/strategy_scores.py (day_card_rank /
# swing_card_rank) so the Research Library's rank columns and the A+ setup
# alerts use the EXACT same numbers these Top-5 cards rank by.
from stockanalysis.core.strategy_scores import (            # noqa: E402
    day_card_rank as _day_trade_score,
    swing_card_rank as _swing_trade_score,
)


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


def _longterm_score(row: dict) -> float:
    """
    Long-term section score = Investment_Score, shown only for names passing
    ALL six primary filters (RS_Rank>80, EPS>25%, revenue>20%, above 200MA,
    FCF positive, earnings beat). A high score without the pass flag means a
    filter failed — those belong on the CSV for review, not on the buy cards.
    """
    if not _v(row, "Investment_Pass", False):
        return -999
    return float(_v(row, "Investment_Score", 0) or 0)


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
        rr_size_html = _risk_size_html(lv_e, lv_s, lv_t1, lv_t2, size_flag,
                                       section=section)
    else:  # options — max loss is the premium, so size = the risk budget itself
        # The swing SizeFlag reflects the LONG-side verdict; an "Avoid" stock is
        # a perfectly valid put candidate, so NONE must not zero the budget.
        # Derive from volatility instead: quarter budget on high-ATR names.
        flag = (size_flag or "").upper()
        if flag not in ("FULL", "HALF", "QUARTER"):
            flag = "QUARTER" if (atr_pct or 0) > 10 else "HALF"
        opt_mult = _SIZE_MULT.get(flag, 0.5) * _regime_mult(section)
        budget = ACCOUNT_SIZE * RISK_PER_TRADE_PCT / 100 * opt_mult
        rr_size_html = (
            f'<div style="display:flex;gap:6px;align-items:flex-start;margin-top:4px;font-size:11px">'
            f'<span style="min-width:42px;text-align:center;background:#E1F5EE;color:#085041;'
            f'padding:1px 5px;border-radius:3px;font-size:10px;font-weight:500;flex-shrink:0">R:R·SIZE</span>'
            f'<span style="color:#52514e;line-height:1.4">Max premium ${budget:,.0f} total '
            f'({flag} = {RISK_PER_TRADE_PCT * opt_mult:g}% of ${ACCOUNT_SIZE:,.0f}) — '
            f'max loss = premium paid{_regime_note(section)}</span></div>'
        )

    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:14px 16px;break-inside:avoid;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
        <div>
          <span style="font-size:17px;font-weight:500;color:#0b0b0b">{_tv_link(ticker)}</span>{_research_link(ticker)}
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

      {_conviction_strip_html(row)}

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


def _longterm_card(row: dict) -> str:
    """
    Card for the Long-Term Investment section — designed so an investor knows
    exactly what to do: a star rating (score/20), a vertical ✓/✗ reasons
    checklist of the investment filters, and a concrete tranche entry plan
    (Buy now 25% / 8EMA 25% / 21EMA 50%) with the actual price levels.
    Review triggers replace a tight stop — a 6-24 mo position exits on
    thesis/stage breaks, not day-range noise.
    """
    ticker   = _v(row, "Ticker", "?")
    name     = _v(row, "LongName", "")
    sector   = _v(row, "Sector", "")
    price    = _v(row, "Current Price", 0) or 0
    score    = _v(row, "_dashboard_score", 0)
    rs_rank  = _v(row, "RS_Rank")
    eps_g    = _v(row, "EPS_Growth%")
    rev_g    = _v(row, "Revenue")
    inst_chg = _v(row, "Inst_Own_Chg")
    fcf      = _v(row, "FCF_Positive", False)
    earn_b   = _v(row, "EarningsBeat", False)
    canslim  = _v(row, "CANSLIM_Pass", False)
    above200 = _v(row, "Above_200MA", False)
    ema8     = _v(row, "8EMA")
    ema21    = _v(row, "21EMA")
    ma200    = _v(row, "200MA")
    high52   = _v(row, "52W High")
    dist     = _v(row, "Dist_52W_High%")
    p200     = _v(row, "Price_vs_200MA%")
    timing   = _v(row, "LT_Entry_Timing", "")
    earn     = _v(row, "EarningsDate", "N/A")
    cat      = _v(row, "Category", "—")
    bz_score = _v(row, "Buy_Zone_Score")
    bz_label = _v(row, "Buy_Zone_Label")

    # ── Star rating: score/20, floor 1 star (cards are pass-gated) ─────────
    n_stars = max(1, min(5, round(score / 20)))
    stars = ('<span style="color:#c9a227;font-size:16px;letter-spacing:2px">'
             + "★" * n_stars + '<span style="color:#d9d7ce">'
             + "☆" * (5 - n_stars) + "</span></span>")

    # ── Reasons checklist ───────────────────────────────────────────────────
    def _detail(val, fmt="{:+.1f}%"):
        return fmt.format(val) if isinstance(val, (int, float)) else ""

    reasons = [
        ("RS Leadership (rank > 80)",
         rs_rank is not None and rs_rank > 80,
         f"rank {rs_rank:.0f}" if rs_rank is not None else ""),
        ("EPS Growth > 25%",  eps_g is not None and eps_g > 25, _detail(eps_g)),
        ("Revenue Growth > 20%", rev_g is not None and rev_g > 20, _detail(rev_g)),
        ("Above 200 MA", above200 is True, _detail(p200)),
        ("FCF Positive", fcf is True, ""),
        ("Earnings Beat", earn_b is True, ""),
        ("Institutional Buying",
         inst_chg is not None and inst_chg > 0, _detail(inst_chg)),
        ("CANSLIM", canslim is True, ""),
    ]
    reason_rows = "".join(
        f'<div style="display:flex;gap:6px;font-size:12px;line-height:1.7">'
        f'<span style="color:{"#0F6E56" if ok else "#A32D2D"};min-width:14px">'
        f'{"✓" if ok else "✗"}</span>'
        f'<span style="color:#0b0b0b">{_html.escape(label)}</span>'
        f'<span style="color:#898781;margin-left:auto">{_html.escape(detail)}</span>'
        f'</div>'
        for label, ok, detail in reasons
    )

    # ── Tranche entry plan: Buy now 25% / 8EMA 25% / 21EMA 50% ─────────────
    def _lvl(px):
        if not px or not price:
            return "—"
        rel = (px / price - 1) * 100
        return f"{_fmt(px)} ({_pct(rel)})"

    bz_badge = ""
    if bz_score is not None:
        bz_color = ("#0F6E56" if bz_score >= 80 else
                    "#8a6d1a" if bz_score >= 60 else "#A32D2D")
        bz_badge = (f'<span style="color:{bz_color};font-weight:600">'
                    f'{_html.escape(str(bz_label))} {bz_score}</span>')

    tranches = [("Buy now", "25%", _fmt(price)),
                ("Buy at 8EMA", "25%", _lvl(ema8)),
                ("Buy at 21EMA", "50%", _lvl(ema21))]
    tranche_rows = "".join(
        f'<div style="display:flex;gap:6px;font-size:12px;line-height:1.7">'
        f'<span style="color:#0b0b0b;min-width:110px">{label}:</span>'
        f'<span style="font-weight:600;color:#085041;min-width:36px">{alloc}</span>'
        f'<span style="color:#52514e">{level}</span></div>'
        for label, alloc, level in tranches
    )
    timing_note = ""
    if timing:
        note = timing
        if _regime_mult("Long-Term") < 1.0:
            note += (f" · {_ACTIVE_REGIME['regime'].upper()} regime — halve "
                     f"each tranche")
        timing_note = (f'<div style="font-size:10px;color:#898781;'
                       f'margin-top:4px">{_html.escape(note)}</div>')

    # ── Review triggers + target ────────────────────────────────────────────
    stop = (f"Weekly close below 200MA {_fmt(ma200)} "
            f"(now {_pct(p200)} above) · RS rank falls below 40 "
            f"· earnings miss + guidance cut. Trim, don't average down.")
    if high52 and price and high52 > price * 1.03:
        target = (f"T1 52W high {_fmt(high52)} ({_pct((high52 / price - 1) * 100)}); "
                  f"beyond, hold while the checklist stays green — "
                  f"reassess quarterly, 6-24 mo horizon.")
    else:
        target = ("At/near 52W high — let it run while the checklist stays "
                  "green; reassess quarterly, 6-24 mo horizon.")
    stop, target = (_html.escape(str(x)) for x in (stop, target))

    def _plan_line(label, bg, fg, text):
        return (f'<div style="display:flex;gap:6px;align-items:flex-start;'
                f'margin-bottom:4px;font-size:11px">'
                f'<span style="min-width:42px;text-align:center;background:{bg};color:{fg};'
                f'padding:1px 5px;border-radius:3px;font-size:10px;font-weight:500;'
                f'flex-shrink:0">{label}</span>'
                f'<span style="color:#52514e;line-height:1.4">{text}</span></div>')

    cat_border, cat_bg, cat_txt = _card_color(cat)
    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:14px 16px;break-inside:avoid;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
        <div>
          <span style="font-size:17px;font-weight:500;color:#0b0b0b">{_tv_link(ticker)}</span>{_research_link(ticker)}
          <span style="font-size:13px;color:#898781;margin-left:6px">${price:,.2f}</span>
          <div style="font-size:10px;color:#898781">{_html.escape(str(name))} · {_html.escape(str(sector))}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
          <span title="Investment score {score:.0f}/100">{stars}</span>
          <span style="background:{cat_bg};color:{cat_txt};font-size:10px;font-weight:500;
                       padding:2px 7px;border-radius:4px">{cat}</span>
        </div>
      </div>

      <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:2px">REASONS</div>
      <div style="margin-bottom:10px">{reason_rows}</div>

      <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:2px">
        ENTRY {f'· {bz_badge}' if bz_badge else ''}
      </div>
      <div style="background:#f9f9f7;border-radius:8px;padding:8px 10px;margin-bottom:8px">
        {tranche_rows}{timing_note}
      </div>

      <div style="border-top:0.5px solid #e1e0d9;padding-top:8px">
        {_plan_line("REVIEW", "#FCEBEB", "#791F1F", stop)}
        {_plan_line("TARGET", "#E6F1FB", "#0C447C", target)}
      </div>

      <div style="margin-top:7px;font-size:10px;color:#898781">
        Earns {earn} {'✓' if earn_b else ''} ·
        Dist {_pct(dist)} from 52W high ·
        {_pct(p200)} vs 200MA
      </div>
    </div>"""


def _section(title: str, color: str, icon: str, rows: list[dict], section_key: str) -> str:
    if not rows:
        empty_msg = (
            "No name passed all six filters (RS_Rank&gt;80 · EPS&gt;25% · "
            "revenue&gt;20% · 200MA · FCF+ · earnings beat) — see the "
            "longterm CSV for near-misses."
            if section_key == "Long-Term"
            else "No candidates found — try loosening thresholds."
        )
        return f"""
        <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;padding:20px">
          <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0 0 6px">
            {icon} {title}
          </h3>
          <p style="font-size:13px;color:#898781;margin:0">{empty_msg}</p>
        </div>"""

    cards_html = "".join(
        _longterm_card(r) if section_key == "Long-Term" else _stock_card(r, section_key)
        for r in rows
    )
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



_REGIME_COLORS = {
    "Bullish":   ("#E1F5EE", "#085041", "#0F6E56"),
    "Neutral":   ("#FAEEDA", "#633806", "#8a6d1a"),
    "Defensive": ("#FCEBEB", "#791F1F", "#A32D2D"),
}


def _regime_banner_html(regime: dict) -> str:
    """Full-width regime strip: verdict badge, drivers, per-horizon sizing."""
    bg, fg, accent = _REGIME_COLORS.get(regime["regime"],
                                        _REGIME_COLORS["Neutral"])
    m = regime["multipliers"]
    drivers = " · ".join(_html.escape(d) for d in regime["drivers"])
    mult_chips = "".join(
        f'<span style="background:white;border:0.5px solid {accent};color:{fg};'
        f'font-size:11px;font-weight:500;padding:3px 10px;border-radius:5px">'
        f'{label} ×{m[key]:g}</span>'
        for key, label in (("day", "Day"), ("swing", "Swing"),
                           ("longterm", "Long-Term"))
    )
    src = {"market_pulse": "VIX + index trend + mega-cap breadth",
           "scan_breadth": "scan-universe breadth (pulse unavailable)",
           "none": "no market data"}.get(regime["source"], regime["source"])
    return f"""
    <div style="background:{bg};border:0.5px solid {accent};border-radius:12px;
                padding:14px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="background:{accent};color:white;font-size:13px;font-weight:600;
                     padding:4px 14px;border-radius:6px">MARKET REGIME: {regime["regime"].upper()}</span>
        <span style="font-size:12px;color:{fg}">score {regime["score"]:+d} · {src}</span>
        <span style="margin-left:auto;display:flex;gap:6px;flex-wrap:wrap">{mult_chips}</span>
      </div>
      <div style="font-size:12px;color:{fg};margin-top:8px">{drivers}</div>
      <div style="font-size:12px;color:{fg};margin-top:4px;font-weight:500">
        {_html.escape(regime["guidance"])}</div>
    </div>"""


def _decision_center_html(top_day: list[dict], top_swing: list[dict],
                          top_lt: list[dict], regime: dict) -> str:
    """
    Highest-conviction pick per horizon, with why-now one-liner and the
    regime-adjusted risk budget — the 30-second answer to "what do I actually
    do today?" before scrolling the full sections.
    """
    m = regime["multipliers"]

    def _col(title, icon, picks, horizon, one_liner_fn):
        risk = RISK_PER_TRADE_PCT * m[horizon]
        head = (f'<div style="font-size:11px;color:#898781;margin-bottom:6px">'
                f'{icon} {title} · risk budget {risk:g}% of acct</div>')
        if not picks:
            return (f'<div style="flex:1;min-width:220px">{head}'
                    f'<div style="font-size:13px;color:#898781">No qualified '
                    f'candidate today.</div></div>')
        best, rest = picks[0], picks[1:3]
        line = _html.escape(one_liner_fn(best))
        rest_txt = (" · next: " + ", ".join(
            f"{_v(r, 'Ticker', '?')} ({_v(r, '_dashboard_score', 0):.0f})"
            for r in rest)) if rest else ""
        return f"""
        <div style="flex:1;min-width:220px">{head}
          <div style="font-size:19px;font-weight:600;color:#0b0b0b">
            {_v(best, "Ticker", "?")}
            <span style="font-size:12px;font-weight:500;color:#898781;margin-left:4px">
              ${(_v(best, "Current Price", 0) or 0):,.2f} · score {_v(best, "_dashboard_score", 0):.0f}</span>
          </div>
          <div style="font-size:12px;color:#52514e;margin-top:3px;line-height:1.5">{line}</div>
          <div style="font-size:10px;color:#898781;margin-top:3px">{rest_txt}</div>
        </div>"""

    def _day_line(r):
        vwap = "above VWAP" if _v(r, "Above_VWAP") else "below VWAP"
        return (f"gap {_pct(_v(r, 'Gap%') or _v(r, 'Gap_Now%'))}, "
                f"RVOL {(_v(r, 'RVOL_Intraday') or _v(r, 'RVOL') or 0):.1f}, {vwap}, "
                f"ORB {_v(r, 'ORB_Status') or '—'}")

    def _swing_line(r):
        rr = _v(r, "RR_T2")
        return (f"{_v(r, 'Category', '—')}, grade {_v(r, 'Grade', '—')}"
                + (f", R:R {rr:.1f}" if rr else "")
                + f", RSI {(_v(r, 'RSI_14') or 0):.0f}")

    def _lt_line(r):
        return _v(r, "LT_Entry_Timing", "all six filters pass")

    cols = "".join([
        _col("DAY TRADE", "⚡", top_day, "day", _day_line),
        _col("SWING", "📈", top_swing, "swing", _swing_line),
        _col("LONG-TERM", "🏦", top_lt, "longterm", _lt_line),
    ])
    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
        <span style="font-size:18px">🎯</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">Decision Center — highest conviction per horizon</h3>
      </div>
      <div style="display:flex;gap:20px;flex-wrap:wrap">{cols}</div>
    </div>"""


def _market_summary_text(regime: dict, rows: list[dict], top_day: list[dict],
                         top_swing: list[dict], top_lt: list[dict],
                         pulse: dict | None) -> str:
    """
    Plain-language day-in-review, assembled from the scan facts. If an
    ANTHROPIC_API_KEY is set (and the anthropic package installed) the facts
    are rewritten by Claude for flow; otherwise this template text ships
    as-is, so the summary never depends on the network.
    """
    total   = len(rows)
    n_mom   = sum(1 for r in rows if _v(r, "Category") == "Momentum")
    n_mp    = sum(1 for r in rows if _v(r, "Category") == "Momentum-Pullback")
    n_avoid = sum(1 for r in rows if _v(r, "Category") in ("Avoid", "Error"))
    n_pass  = sum(1 for r in rows if _v(r, "Investment_Pass"))

    p1 = (f"The tape reads {regime['regime']} "
          f"(score {regime['score']:+d}, {', '.join(regime['drivers'][:3])}). "
          f"{regime['guidance']}")

    parts = [f"Of {total} names scanned, {n_mom} are in confirmed momentum, "
             f"{n_mp} are pulling back constructively, and {n_avoid} have no "
             f"tradeable setup."]
    if top_day:
        b = top_day[0]
        parts.append(f"The strongest intraday tape is {_v(b, 'Ticker')} "
                     f"(day score {_v(b, '_dashboard_score', 0):.0f}).")
    if top_swing:
        b = top_swing[0]
        parts.append(f"Best swing setup: {_v(b, 'Ticker')} — "
                     f"{_v(b, 'Category', '')} at swing score "
                     f"{_v(b, '_dashboard_score', 0):.0f}.")
    p2 = " ".join(parts)

    if n_pass:
        lt_names = ", ".join(_v(r, "Ticker", "?") for r in top_lt)
        p3 = (f"For the long book, {n_pass} name(s) pass all six investment "
              f"filters ({lt_names} lead) — but several are extended above "
              f"their 200MA, so tranche entries beat chasing.")
    else:
        p3 = ("No name passes all six long-term filters today — the long "
              "book waits; review near-misses in the longterm CSV.")

    paras = [p1, p2, p3]

    secs = _sector_strength(rows)
    if len(secs) >= 2:
        lead, lag = secs[0], secs[-1]
        paras.append(f"Sector rotation: {lead['sector']} leads (median RS "
                     f"rank {lead['median']:.0f} — {', '.join(lead['top'])}) "
                     f"while {lag['sector']} lags "
                     f"(rank {lag['median']:.0f}).")

    n_ext = sum(1 for r in rows
                if any(t == "EXTENDED" for t, _ in (r.get("Conv_Tags") or [])))
    if n_ext >= 3:
        paras.append(f"{n_ext} leaders are extended above their moving "
                     f"averages — favor pullback entries over chasing "
                     f"breakouts.")

    m = regime["multipliers"]
    paras.append(f"Position sizing for the day: day trades ×{m['day']:g}, "
                 f"swings ×{m['swing']:g}, long-term tranches "
                 f"×{m['longterm']:g} of the normal "
                 f"{RISK_PER_TRADE_PCT:g}% risk budget.")

    text = "\n\n".join(paras)
    return _ai_polish(text) or text


def _ai_polish(facts: str) -> str | None:
    """Optional: rewrite the template summary with Claude for readability.
    Returns None (caller keeps the template) unless a key is configured."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=400,
            messages=[{"role": "user", "content":
                       "Rewrite this trading-desk morning summary as 3-4 "
                       "crisp plain-English sentences a portfolio manager "
                       "would read aloud. Keep every number. No preamble.\n\n"
                       + facts}],
        )
        out = "".join(b.text for b in msg.content if b.type == "text").strip()
        return out or None
    except Exception as e:
        print(f"[Dashboard] AI summary polish unavailable ({e}) — using template")
        return None


def _market_summary_html(summary: str) -> str:
    paras = "".join(
        f'<p style="font-size:13px;color:#52514e;line-height:1.6;margin:0 0 8px">'
        f'{_html.escape(p)}</p>'
        for p in summary.split("\n\n") if p.strip())
    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span style="font-size:18px">🗞️</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">AI Market Brief</h3>
      </div>
      {paras}
    </div>"""


def _alloc_bars(pcts: list) -> str:
    """Horizontal allocation bars (label, pct-of-portfolio)."""
    if not pcts:
        return '<span style="font-size:11px;color:#898781">—</span>'
    peak = max(p for _, p in pcts) or 1
    return "".join(
        f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
        f'<span style="min-width:110px;font-size:11px;color:#0b0b0b">{_html.escape(str(k))}</span>'
        f'<div style="flex:1;background:#f1efea;border-radius:3px;height:10px">'
        f'<div style="width:{max(2, round(p / peak * 100))}%;background:#185FA5;'
        f'height:10px;border-radius:3px"></div></div>'
        f'<span style="min-width:44px;text-align:right;font-size:11px;'
        f'font-weight:600">{p:.1f}%</span></div>'
        for k, p in pcts)


def _portfolio_html(view: list[dict], totals: dict,
                    alloc: dict | None = None) -> str:
    """Portfolio & Watchlist panel. Empty portfolio → setup instructions."""
    if not view:
        return f"""
        <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                    padding:20px;margin-bottom:16px">
          <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0 0 6px">
            💼 Portfolio &amp; Watchlist</h3>
          <p style="font-size:13px;color:#898781;margin:0">
            No portfolio file found. Copy <code>data/portfolio_template.csv</code>
            to <code>data/portfolio.csv</code> and list your positions
            (Ticker, Shares, Avg_Cost, Entry_Date, Strategy
            day/swing/longterm/watch, optional Stop/Target, Notes).
            Rows with 0 shares are watchlist-only.</p>
        </div>"""

    gain = totals.get("total_gain")
    gain_c = "#0F6E56" if (gain or 0) >= 0 else "#A32D2D"
    gain_txt = (f'<span style="color:{gain_c};font-weight:600">'
                f'{"+" if gain >= 0 else ""}{gain:,.2f} '
                f'({totals["total_gain_pct"]:+.1f}%)</span>'
                if gain is not None else "—")

    rows_html = []
    for p in view:
        watch = p["Is_Watch"]
        alerts = "".join(
            f'<div style="font-size:10px;color:{"#791F1F" if "⛔" in a else "#633806"}">'
            f'{_html.escape(a)}</div>' for a in p["Alerts"])
        g_pct, g_usd = p["Gain_Pct"], p["Gain_Dollars"]
        g_color = "#0F6E56" if (g_pct or 0) >= 0 else "#A32D2D"
        g_pct_html = (f'<span style="color:{g_color}">{g_pct:+.1f}%</span>'
                      if g_pct is not None else "—")
        g_usd_html = (f'<span style="color:{g_color}">{g_usd:+,.0f}</span>'
                      if g_usd is not None else "—")
        action = p["Next_Action"]
        act_urgent = action.split(" ")[0] in ("EXIT", "TRIM", "REDUCE", "REVIEW")
        act_html = (f'<span style="font-weight:600;'
                    f'color:{"#791F1F" if act_urgent else "#0b0b0b"}">'
                    f'{_html.escape(action)}</span>')
        rows_html.append(f"""
        <tr style="border-top:0.5px solid #e1e0d9;{'opacity:0.65' if watch else ''}">
          <td style="padding:6px 8px;font-weight:600">{_tv_link(p['Ticker'])}{_research_link(p['Ticker'])}</td>
          <td style="padding:6px 8px">{p['Strategy']}</td>
          <td style="padding:6px 8px">{p.get('Cap') or '—'}</td>
          <td style="padding:6px 8px;text-align:right">{p['Shares']:g}</td>
          <td style="padding:6px 8px;text-align:right">{_fmt(p['Avg_Cost'])}</td>
          <td style="padding:6px 8px;text-align:right">{_fmt(p['Price'])}</td>
          <td style="padding:6px 8px;text-align:right">{g_pct_html}</td>
          <td style="padding:6px 8px;text-align:right">{g_usd_html}</td>
          <td style="padding:6px 8px;text-align:right">{f"{p['Alloc_Pct']:.1f}%" if p.get('Alloc_Pct') is not None else '—'}</td>
          <td style="padding:6px 8px;text-align:right">{p['Days_Held'] if p['Days_Held'] is not None else '—'}</td>
          <td style="padding:6px 8px;text-align:right">{_fmt(p['Risk'], decimals=0)}</td>
          <td style="padding:6px 8px;text-align:right">{_fmt(p['Stop'])}</td>
          <td style="padding:6px 8px;text-align:right">{_fmt(p['Target'])}</td>
          <td style="padding:6px 8px">{act_html}</td>
          <td style="padding:6px 8px">{alerts or '<span style="font-size:10px;color:#0F6E56">✓ clear</span>'}
              <div style="font-size:10px;color:#898781">{_html.escape(p['Notes'] or '')}</div></td>
        </tr>""")

    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">
        <span style="font-size:18px">💼</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">Portfolio &amp; Watchlist</h3>
        <span style="margin-left:auto;font-size:12px;color:#52514e">
          portfolio ${totals.get('portfolio_value') or 0:,.0f} ·
          invested ${totals['total_value']:,.0f}
          ({totals.get('invested_pct') or 0:g}%) ·
          cash ${totals.get('cash') or 0:,.0f} ·
          gain {gain_txt} ·
          at-risk ${totals.get('total_risk') or 0:,.0f} ·
          {totals['alerts']} alert(s)</span>
      </div>
      <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr style="color:#898781;font-size:10px;text-align:left">
          <th style="padding:4px 8px">TICKER</th><th style="padding:4px 8px">STRATEGY</th>
          <th style="padding:4px 8px">CAP</th>
          <th style="padding:4px 8px;text-align:right">SHARES</th>
          <th style="padding:4px 8px;text-align:right">AVG COST</th>
          <th style="padding:4px 8px;text-align:right">PRICE</th>
          <th style="padding:4px 8px;text-align:right">GAIN %</th>
          <th style="padding:4px 8px;text-align:right">GAIN $</th>
          <th style="padding:4px 8px;text-align:right">ALLOC %</th>
          <th style="padding:4px 8px;text-align:right">DAYS HELD</th>
          <th style="padding:4px 8px;text-align:right">RISK $</th>
          <th style="padding:4px 8px;text-align:right">STOP</th>
          <th style="padding:4px 8px;text-align:right">TARGET</th>
          <th style="padding:4px 8px">NEXT ACTION</th>
          <th style="padding:4px 8px">ALERTS / NOTES</th>
        </tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table></div>
      {_alloc_footer_html(alloc, totals)}
    </div>"""


def _alloc_footer_html(alloc: dict | None, totals: dict) -> str:
    """Cap-bucket + sector allocation bars, warnings, and the risk budget."""
    if not alloc:
        return ""
    warn_html = "".join(
        f'<div style="background:#FCEBEB;color:#791F1F;font-size:11px;'
        f'padding:5px 10px;border-radius:6px;margin-top:6px">⚠ {_html.escape(w)}</div>'
        for w in alloc.get("warnings") or [])
    pv = alloc.get("portfolio_value") or 0
    risk_budget = pv * RISK_PER_TRADE_PCT / 100
    return f"""
      <div style="display:flex;gap:28px;flex-wrap:wrap;border-top:0.5px solid #e1e0d9;
                  margin-top:10px;padding-top:10px">
        <div style="flex:1;min-width:220px">
          <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">
            MARKET-CAP ALLOCATION (% of portfolio)</div>
          {_alloc_bars(alloc.get("caps") or [])}
        </div>
        <div style="flex:1;min-width:220px">
          <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">
            SECTOR ALLOCATION (% of portfolio)</div>
          {_alloc_bars(alloc.get("sectors") or [])}
        </div>
        <div style="min-width:180px">
          <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">
            RISK BUDGET</div>
          <div style="font-size:12px;line-height:1.8;color:#0b0b0b">
            Portfolio <b>${pv:,.0f}</b><br>
            Max risk per trade <b>{RISK_PER_TRADE_PCT:g}%</b> =
            <b>${risk_budget:,.0f}</b><br>
            <span style="font-size:10px;color:#898781">
              regime-scaled on each card's R:R·SIZE line</span></div>
        </div>
      </div>
      {warn_html}"""


# ── Prioritization layer (conviction engine UI) ──────────────────────────────

_ACTION_STYLE = {"READY": ("🟢", "#E1F5EE", "#085041"),
                 "WATCH": ("🟡", "#FAEEDA", "#633806"),
                 "AVOID": ("🔴", "#FCEBEB", "#791F1F")}
_TAG_STYLE = {"good": ("#E1F5EE", "#085041"),
              "warn": ("#FAEEDA", "#633806"),
              "bad":  ("#FCEBEB", "#791F1F")}
_WHY_MARK = {"+": ("✅", "#0F6E56"), "!": ("⚠", "#8a6d1a"),
             "-": ("❌", "#A32D2D")}


def _stars_html(n: int, size: int = 15) -> str:
    n = max(0, min(5, int(n)))
    return (f'<span style="color:#c9a227;font-size:{size}px;letter-spacing:2px">'
            + "★" * n + '<span style="color:#d9d7ce">' + "☆" * (5 - n)
            + "</span></span>")


# Macro keyword → tag for the Economic News panel. A headline qualifies by
# matching at least one entry; MARKET-only matches rank behind true macro ones.
_MACRO_TAGS = (
    ("fed ", "FED"), ("fomc", "FED"), ("powell", "FED"),
    ("rate cut", "FED"), ("rate hike", "FED"), ("interest rate", "FED"),
    ("central bank", "FED"),
    ("inflation", "INFLATION"), ("cpi", "INFLATION"), ("ppi", "INFLATION"),
    ("pce", "INFLATION"),
    ("jobs report", "JOBS"), ("payroll", "JOBS"), ("unemployment", "JOBS"),
    ("jobless", "JOBS"), ("labor market", "JOBS"),
    ("tariff", "TRADE"), ("trade deal", "TRADE"), ("trade war", "TRADE"),
    ("china", "TRADE"), ("export", "TRADE"),
    ("treasury", "RATES"), ("yield", "RATES"), ("10-year", "RATES"),
    ("bond market", "RATES"),
    ("gdp", "GROWTH"), ("recession", "GROWTH"), ("soft landing", "GROWTH"),
    ("consumer spending", "GROWTH"), ("retail sales", "GROWTH"),
    ("oil price", "ENERGY"), ("opec", "ENERGY"), ("crude", "ENERGY"),
    ("stock market", "MARKET"), ("s&p 500", "MARKET"), ("nasdaq", "MARKET"),
    ("dow", "MARKET"), ("wall street", "MARKET"), ("sell-off", "MARKET"),
    ("selloff", "MARKET"), ("rally", "MARKET"), ("vix", "MARKET"),
)

_MACRO_TAG_COLORS = {
    "FED": ("#EEEDFE", "#26215C"), "INFLATION": ("#FCEBEB", "#791F1F"),
    "JOBS": ("#E6F1FB", "#0C447C"), "TRADE": ("#FAEEDA", "#633806"),
    "RATES": ("#E6F1FB", "#0C447C"), "GROWTH": ("#E1F5EE", "#085041"),
    "ENERGY": ("#FAEEDA", "#633806"), "MARKET": ("#F1EFE8", "#444441"),
}


def _macro_tags(title: str) -> list[str]:
    t = f" {title.lower()} "
    return sorted({label for kw, label in _MACRO_TAGS if kw in t})


def _fetch_econ_headlines(max_items: int = 6) -> list[dict]:
    """
    Major economic/market-moving headlines — pulled from the index feeds
    (SPY/QQQ/^GSPC/^VIX), deduped, kept only when macro-tagged, with
    macro-specific stories ranked above generic market wrap-ups.
    Network-dependent; returns [] on failure or when nothing qualifies.
    Pure data — _econ_news_html() renders it, generate_dashboard() also
    reuses it for snapshot.json so the fetch happens only once per scan.
    """
    from stockanalysis.reporting.research import _fetch_ticker_news
    items, seen = [], set()
    for symbol in ("SPY", "QQQ", "^GSPC", "^VIX"):
        try:
            for n in _fetch_ticker_news(symbol, limit=10):
                tags = _macro_tags(n["title"])
                if not tags:
                    continue
                # Wire services re-publish the same story with reworded
                # titles ("Update: …") — dedupe on source + tags + the
                # title's opening, not the exact string
                key = (n["publisher"], tuple(tags),
                       n["title"].strip().lower()[:35])
                if key in seen:
                    continue
                seen.add(key)
                n["tags"] = tags
                items.append(n)
        except Exception:
            continue
    # newest first, then macro-specific above bare market wrap-ups
    # (stable sorts compose: second sort preserves date order within bands)
    items.sort(key=lambda n: n["when"], reverse=True)
    items.sort(key=lambda n: n["tags"] == ["MARKET"])
    return items[:max_items]


def _econ_news_html(items: list[dict]) -> str:
    """Render pre-fetched econ headlines (see _fetch_econ_headlines). Sits
    above the market-pulse strip (whose calendar covers the Fed outlook)."""
    if not items:
        return ""
    lis = []
    for n in items:
        chips = "".join(
            f'<span style="background:{_MACRO_TAG_COLORS.get(t, ("#F1EFE8", "#444441"))[0]};'
            f'color:{_MACRO_TAG_COLORS.get(t, ("#F1EFE8", "#444441"))[1]};'
            f'font-size:9px;font-weight:600;padding:1px 6px;border-radius:3px;'
            f'margin-right:4px">{t}</span>' for t in n["tags"])
        title = _html.escape(n["title"][:160])
        link = (f'<a href="{_html.escape(n["url"])}" target="_blank" '
                f'style="color:#0b0b0b;text-decoration:none">{title}</a>'
                if n.get("url") else title)
        lis.append(
            f'<div style="font-size:12px;line-height:1.5;margin-bottom:7px">'
            f'<span style="color:#898781;font-size:10px">{_html.escape(n["when"])}</span> '
            f'{chips}<br>{link}'
            f'<span style="color:#898781;font-size:10px"> — {_html.escape(n["publisher"])}</span></div>')
    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span style="font-size:18px">🌐</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">
          Economic News — macro headlines moving stocks</h3>
        <span style="margin-left:auto;font-size:11px;color:#898781">
          as of {datetime.now():%H:%M} · Fed/econ calendar below</span>
      </div>
      {''.join(lis)}
    </div>"""


def _day_session_html(output_dir: Path) -> str:
    """
    Day Session Universe panel — renders day_session.json written by the
    scheduler's _initialize_day_session() at market open (hot movers merged
    with the day-trade base list). '' when the file is missing; marked stale
    when it isn't from today.
    """
    import json
    f = Path(output_dir) / "day_session.json"
    if not f.exists():
        return ""
    try:
        data = json.loads(f.read_text())
    except Exception as e:
        print(f"[Dashboard] day_session.json unreadable ({e}) — skipped")
        return ""
    hot   = data.get("hot") or []
    base  = data.get("base") or []
    is_today = data.get("date") == datetime.now().strftime("%Y-%m-%d")
    stale = ("" if is_today else
             f'<span style="background:#FCEBEB;color:#791F1F;font-size:10px;'
             f'font-weight:600;padding:2px 8px;border-radius:4px">'
             f'STALE — from {_html.escape(str(data.get("date")))}</span>')

    def _chips(tickers, bg, fg, bold=False):
        return "".join(
            f'<span style="background:{bg};color:{fg};font-size:11px;'
            f'{"font-weight:600;" if bold else ""}padding:3px 9px;'
            f'border-radius:5px;margin:0 4px 4px 0;display:inline-block">'
            f'{_tv_link(t)}{_research_link(t)}</span>'
            for t in tickers)

    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap">
        <span style="font-size:18px">🔔</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">
          Day Session Universe</h3>
        {stale}
        <span style="margin-left:auto;font-size:11px;color:#898781">
          initialized {_html.escape(str(data.get("updated_at", "?")))} ·
          {len(hot)} movers + {len(base)} base = {len(data.get("merged") or [])} tickers</span>
      </div>
      <div style="font-size:11px;font-weight:600;color:#8a6d1a;margin-bottom:3px">
        🔥 HOT MOVERS (from market-movers scan at open)</div>
      <div style="margin-bottom:8px">{_chips(hot, "#FAEEDA", "#633806", bold=True)
          or '<span style="font-size:11px;color:#898781">none captured</span>'}</div>
      <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:3px">
        BASE DAY-TRADE LIST</div>
      <div>{_chips(base, "#f1efea", "#0b0b0b")}</div>
    </div>"""


def _hero_html(opp: dict, regime: dict) -> str:
    """Today's Opportunity — the single number you see first."""
    risk_bg, risk_fg = {"LOW": ("#E1F5EE", "#085041"),
                        "MEDIUM": ("#FAEEDA", "#633806"),
                        "HIGH": ("#FCEBEB", "#791F1F")}.get(
                            opp["risk"], ("#F1EFE8", "#444441"))
    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:20px 24px;margin-bottom:16px;display:flex;gap:28px;
                align-items:center;flex-wrap:wrap">
      <div>
        <div style="font-size:11px;font-weight:600;color:#898781;letter-spacing:0.5px">
          TODAY'S OPPORTUNITY</div>
        <div style="font-size:44px;font-weight:650;color:#0b0b0b;line-height:1.1">
          {opp["score"]}<span style="font-size:20px;color:#898781;font-weight:500">/100</span></div>
        <div>{_stars_html(opp["stars"], 18)}</div>
      </div>
      <div style="flex:1;min-width:200px">
        <div style="font-size:16px;font-weight:500;color:#0b0b0b">{_html.escape(opp["label"])}</div>
        <div style="font-size:12px;color:#52514e;margin-top:4px">
          {opp["n_ready"]} name(s) READY 🟢 · regime {regime["regime"]}</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:11px;font-weight:600;color:#898781;letter-spacing:0.5px">RISK</div>
        <div style="background:{risk_bg};color:{risk_fg};font-size:18px;font-weight:650;
                    padding:6px 22px;border-radius:8px;margin-top:4px">{opp["risk"]}</div>
      </div>
    </div>"""


def _priority_queue_html(rows: list[dict], top_n: int = 10) -> str:
    """Trade Priority Queue — triage: where to focus, in order."""
    # Triage order: action first (all 🟢 above all 🟡 above all 🔴), then
    # conviction within each band — a READY 70 beats a WATCH 85 for "what do
    # I do right now"
    ranked = sorted((r for r in rows if r.get("Conv_Overall") is not None
                     and _v(r, "Category") != "Error"),
                    key=lambda r: ({"READY": 0, "WATCH": 1, "AVOID": 2}
                                   .get(r.get("Conv_Action"), 3),
                                   -r["Conv_Overall"]))
    # READY/WATCH first, then the top avoided names as explicit "don't touch"
    # entries — the red rows are information too
    active = [r for r in ranked if r.get("Conv_Action") != "AVOID"][:top_n]
    avoided = [r for r in ranked if r.get("Conv_Action") == "AVOID"
               and r.get("Conv_Overall", 0) >= 50][:max(2, top_n - len(active))]
    queue = (active + avoided)[:top_n]
    if not queue:
        return ""

    trs = []
    for i, r in enumerate(queue, 1):
        dot, a_bg, a_fg = _ACTION_STYLE.get(r.get("Conv_Action"),
                                            _ACTION_STYLE["AVOID"])
        why = r.get("Conv_Why") or []
        first_why = (f'{_WHY_MARK[why[0][0]][0]} {_html.escape(why[0][1])}'
                     if why else "")
        q, s, t = (r.get("Conv_Quality", 0), r.get("Conv_Setup", 0),
                   r.get("Conv_Timing", 0))
        trs.append(f"""
        <tr style="border-top:0.5px solid #e1e0d9">
          <td style="padding:7px 8px;font-weight:600;color:#898781">{dot} {i}</td>
          <td style="padding:7px 8px;font-weight:650;font-size:14px">{_tv_link(_v(r, "Ticker"))}{_research_link(_v(r, "Ticker"))}</td>
          <td style="padding:7px 8px">{_stars_html(r.get("Conv_Stars", 1), 13)}</td>
          <td style="padding:7px 8px"><span style="background:{a_bg};color:{a_fg};
              font-size:11px;font-weight:600;padding:3px 9px;border-radius:5px">
              {_html.escape(r.get("Conv_Action_Reason", ""))}</span></td>
          <td style="padding:7px 8px;text-align:right">{_fmt(_v(r, "Current Price"))}</td>
          <td style="padding:7px 8px;text-align:right;font-size:11px;color:#52514e">
              Q {q} · S {s} · T {t}</td>
          <td style="padding:7px 8px;font-size:11px;color:#52514e">{first_why}</td>
        </tr>""")

    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span style="font-size:18px">🚦</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">Trade Priority Queue</h3>
        <span style="margin-left:auto;font-size:11px;color:#898781">
          🟢 ready · 🟡 watch · 🔴 avoid — ranked by conviction</span>
      </div>
      <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr style="color:#898781;font-size:10px;text-align:left">
          <th style="padding:4px 8px">PRIORITY</th><th style="padding:4px 8px">TICKER</th>
          <th style="padding:4px 8px">CONFIDENCE</th><th style="padding:4px 8px">ACTION</th>
          <th style="padding:4px 8px;text-align:right">PRICE</th>
          <th style="padding:4px 8px;text-align:right">QUALITY·SETUP·TIMING</th>
          <th style="padding:4px 8px">TOP REASON</th></tr></thead>
        <tbody>{''.join(trs)}</tbody>
      </table></div>
    </div>"""


_HEAT_ROWS = [
    ("Momentum",         lambda r: _v(r, "Category") == "Momentum",         "#185FA5"),
    ("Swing (MP + VCP)", lambda r: _v(r, "Category") in
                                   ("Momentum-Pullback", "VCP Setup"),      "#0F6E56"),
    ("Long-term pass",   lambda r: bool(_v(r, "Investment_Pass")),          "#3B6D11"),
    ("Turnaround",       lambda r: _v(r, "Category") == "Turnaround",       "#8a6d1a"),
    ("Avoid",            lambda r: _v(r, "Category") in ("Avoid", "Error"), "#898781"),
]


def _heatmap_html(rows: list[dict]) -> str:
    """Category heat map — one glance at where today's opportunity sits."""
    counts = [(label, sum(1 for r in rows if pred(r)), color)
              for label, pred, color in _HEAT_ROWS]
    peak = max((c for _, c, _ in counts), default=0) or 1
    bars = "".join(
        f'<div style="display:flex;align-items:center;gap:10px;margin:5px 0">'
        f'<span style="min-width:130px;font-size:12px;color:#0b0b0b">{label}</span>'
        f'<div style="flex:1;background:#f1efea;border-radius:4px;height:16px">'
        f'<div style="width:{max(2, round(c / peak * 100))}%;background:{color};'
        f'height:16px;border-radius:4px"></div></div>'
        f'<span style="min-width:34px;text-align:right;font-size:12px;'
        f'font-weight:600;color:{color}">{c}</span></div>'
        for label, c, color in counts)
    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span style="font-size:18px">📊</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">Scan Heat Map</h3>
        <span style="margin-left:auto;font-size:11px;color:#898781">{len(rows)} tickers scanned</span>
      </div>
      {bars}
    </div>"""


def _sector_strength(rows: list[dict]) -> list[dict]:
    """Sectors ranked by median RS_Rank of their members (≥2 members)."""
    by = {}
    for r in rows:
        sec, rk = _v(r, "Sector"), r.get("RS_Rank")
        if not sec or sec == "N/A" or rk is None:
            continue
        by.setdefault(sec, []).append((rk, _v(r, "Ticker", "?")))
    out = []
    for sec, members in by.items():
        if len(members) < 2:
            continue
        ranks = sorted(rk for rk, _ in members)
        out.append({"sector": sec, "median": ranks[len(ranks) // 2],
                    "n": len(members),
                    "top": [t for _, t in sorted(members, reverse=True)[:3]]})
    out.sort(key=lambda s: -s["median"])
    return out


def _sector_rotation_html(rows: list[dict]) -> str:
    """Leaders / laggards by sector + best names inside the strongest ones."""
    secs = _sector_strength(rows)
    if len(secs) < 2:
        return ""
    leaders, laggards = secs[:3], secs[-3:][::-1]

    def _col(title, items, color):
        lis = "".join(
            f'<div style="font-size:12px;line-height:1.8;color:#0b0b0b">'
            f'{_html.escape(s["sector"])} '
            f'<span style="color:#898781">(rank {s["median"]:.0f} · {s["n"]})</span></div>'
            for s in items)
        return (f'<div style="flex:1;min-width:180px">'
                f'<div style="font-size:11px;font-weight:600;color:{color};'
                f'margin-bottom:4px">{title}</div>{lis}</div>')

    best = []
    lead_names = {s["sector"] for s in leaders}
    for r in sorted(rows, key=lambda r: -(r.get("RS_Rank") or 0)):
        if _v(r, "Sector") in lead_names and _v(r, "Ticker") not in best:
            best.append(_v(r, "Ticker"))
        if len(best) >= 6:
            break
    best_html = " · ".join(f"<b>{t}</b>" for t in best)

    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span style="font-size:18px">🔄</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">Sector Rotation</h3>
        <span style="margin-left:auto;font-size:11px;color:#898781">
          by median RS rank within this scan</span>
      </div>
      <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:8px">
        {_col("TODAY'S LEADERS", leaders, "#0F6E56")}
        {_col("TODAY'S WEAKEST", laggards, "#A32D2D")}
      </div>
      <div style="border-top:0.5px solid #e1e0d9;padding-top:8px;font-size:12px;color:#52514e">
        Best stocks inside strongest sectors: {best_html or "—"}</div>
    </div>"""


def _conviction_strip_html(row: dict) -> str:
    """Per-card strip: action chip, confidence stars, Quality/Setup/Timing
    split, why checklist, risk tags. '' when the conviction layer didn't run."""
    if row.get("Conv_Overall") is None:
        return ""
    dot, a_bg, a_fg = _ACTION_STYLE.get(row.get("Conv_Action"),
                                        _ACTION_STYLE["AVOID"])
    tags = "".join(
        f'<span style="background:{_TAG_STYLE[lv][0]};color:{_TAG_STYLE[lv][1]};'
        f'font-size:9px;font-weight:600;padding:2px 6px;border-radius:3px;'
        f'margin-right:4px">{_html.escape(label)}</span>'
        for label, lv in (row.get("Conv_Tags") or []))
    why_items = "".join(
        f'<div style="font-size:11px;line-height:1.6">'
        f'<span style="color:{_WHY_MARK[m][1]}">{_WHY_MARK[m][0]}</span> '
        f'<span style="color:#52514e">{_html.escape(txt)}</span></div>'
        for m, txt in (row.get("Conv_Why") or [])[:5])
    return f"""
      <div style="background:#f9f9f7;border-radius:8px;padding:8px 10px;margin-bottom:10px">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:5px">
          <span style="background:{a_bg};color:{a_fg};font-size:11px;font-weight:600;
                       padding:2px 9px;border-radius:5px">{dot} {row.get("Conv_Action", "")}</span>
          {_stars_html(row.get("Conv_Stars", 1), 13)}
          <span style="font-size:10px;color:#898781;margin-left:auto">
            Quality {row.get("Conv_Quality", 0)} · Setup {row.get("Conv_Setup", 0)} ·
            Timing {row.get("Conv_Timing", 0)}</span>
        </div>
        {why_items}
        <div style="margin-top:5px">{tags}</div>
      </div>"""


# ── Research-page links + Turnaround Recovery panel ──────────────────────────

# Tickers with a generated research page (data/output/research/<T>.html).
# Populated by generate_dashboard(); card/table builders read it.
_RESEARCH_PAGES: set = set()


def _tv_link(ticker) -> str:
    """Ticker text as a TradingView chart link (new tab), inheriting the
    surrounding font styling. No exchange prefix — TradingView resolves it
    (hardcoding NASDAQ: would break NYSE names); '-' share classes become
    '.' (yfinance BRK-B == TradingView BRK.B). The 📄 research link next to
    it is unchanged."""
    if not ticker:
        return "?"
    sym = str(ticker).replace("-", ".")
    return (f'<a href="https://www.tradingview.com/chart/?symbol={sym}" '
            f'target="_blank" style="text-decoration:none;color:inherit" '
            f'title="Open TradingView chart">{ticker}</a>')


def _research_link(ticker) -> str:
    """📄 link to the ticker's research page, '' if none was generated."""
    if not ticker or ticker not in _RESEARCH_PAGES:
        return ""
    return (f' <a href="research/{ticker}.html" target="_blank" '
            f'style="text-decoration:none;font-size:12px" '
            f'title="Open research page">📄</a>')


_REC_STAGE_STYLE = {
    "Bottoming":       ("🔴", "#FCEBEB", "#791F1F"),
    "Recovering":      ("🟡", "#FAEEDA", "#633806"),
    "Trend Confirmed": ("🟢", "#E1F5EE", "#085041"),
}


def _turnaround_html(recovery: list[dict]) -> str:
    """Turnaround Recovery panel — which beaten-down names are becoming
    investable, with maturity stage, balanced why/risks, and an entry rule."""
    if not recovery:
        return ""
    trs = []
    for r in recovery:
        stage = r.get("Rec_Stage", "Bottoming")
        dot, s_bg, s_fg = _REC_STAGE_STYLE.get(stage,
                                               _REC_STAGE_STYLE["Bottoming"])
        why = "".join(f'<div style="font-size:11px;line-height:1.5;color:#0F6E56">'
                      f'· {_html.escape(w)}</div>'
                      for w in (r.get("Rec_Why") or [])[:3]) or \
              '<span style="font-size:11px;color:#898781">no repair signals yet</span>'
        risks = "".join(f'<div style="font-size:11px;line-height:1.5;color:#A32D2D">'
                        f'· {_html.escape(w)}</div>'
                        for w in (r.get("Rec_Risks") or [])[:3]) or \
                '<span style="font-size:11px;color:#898781">—</span>'
        trs.append(f"""
        <tr style="border-top:0.5px solid #e1e0d9;vertical-align:top">
          <td style="padding:8px;font-weight:650;font-size:14px">
              {_tv_link(_v(r, "Ticker"))}{_research_link(_v(r, "Ticker"))}
              <div style="font-size:10px;color:#898781;font-weight:400">
                {_fmt(_v(r, "Current Price"))} · {_pct(_v(r, "Dist_52W_High%"))} off high</div></td>
          <td style="padding:8px"><span style="background:{s_bg};color:{s_fg};
              font-size:11px;font-weight:600;padding:3px 9px;border-radius:5px;
              white-space:nowrap">{dot} {stage}</span></td>
          <td style="padding:8px">{_stars_html(r.get("Rec_Stars", 1), 13)}</td>
          <td style="padding:8px">{why}</td>
          <td style="padding:8px">{risks}</td>
          <td style="padding:8px;font-size:11px;color:#52514e;max-width:220px">
              {_html.escape(r.get("Rec_Entry", ""))}</td>
        </tr>""")

    return f"""
    <div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
                padding:16px 18px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <span style="font-size:18px">🔧</span>
        <h3 style="font-size:15px;font-weight:500;color:#0b0b0b;margin:0">
          Turnaround Recovery — beaten-down names becoming investable</h3>
        <span style="margin-left:auto;font-size:11px;color:#898781">
          ≥35% off 52W high · 🔴 bottoming → 🟡 recovering → 🟢 trend confirmed</span>
      </div>
      <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr style="color:#898781;font-size:10px;text-align:left">
          <th style="padding:4px 8px">TICKER</th>
          <th style="padding:4px 8px">RECOVERY STAGE</th>
          <th style="padding:4px 8px">CONFIDENCE</th>
          <th style="padding:4px 8px">WHY IT MAY RECOVER</th>
          <th style="padding:4px 8px">RISKS</th>
          <th style="padding:4px 8px">ENTRY</th></tr></thead>
        <tbody>{''.join(trs)}</tbody>
      </table></div>
      <div style="font-size:10px;color:#898781;margin-top:6px">
        Speculative by nature — QUARTER size max; stage tells maturity, not certainty.</div>
    </div>"""


def _snap_row(r: dict) -> dict:
    """Compact per-ticker projection for snapshot.json top-N lists."""
    return {
        "ticker": _v(r, "Ticker"), "price": _v(r, "Current Price"),
        "score": round(_v(r, "_dashboard_score", 0) or 0),
        "category": _v(r, "Category"), "grade": _v(r, "Grade"),
        "action": _v(r, "Conv_Action"), "action_reason": _v(r, "Conv_Action_Reason"),
        "stars": _v(r, "Conv_Stars"),
    }


def build_snapshot(rows: list[dict], regime: dict, opp: dict,
                   pulse: dict | None, top_day: list[dict],
                   top_swing: list[dict], top_lt: list[dict],
                   recovery: list[dict], econ_headlines: list[dict],
                   pf_view: list[dict], pf_totals: dict,
                   pf_alloc: dict) -> dict:
    """
    Distilled JSON snapshot of a completed scan — the webapp's dashboard
    homepage reads this instead of re-deriving everything from CSVs/rows
    or scraping the generated HTML. Pure function: no I/O, easy to test.
    """
    total = len(rows)
    cat_counts = {}
    for r in rows:
        c = _v(r, "Category", "Unknown")
        cat_counts[c] = cat_counts.get(c, 0) + 1

    vix = (pulse or {}).get("vix") or {}
    spy = (pulse or {}).get("spy") or {}
    qqq = (pulse or {}).get("qqq") or {}
    market = {
        "vix": vix.get("level"), "vix_change": vix.get("change"),
        "spy_price": spy.get("price"), "spy_chg_pct": spy.get("day_chg_pct"),
        "spy_strength": spy.get("strength"),
        "qqq_price": qqq.get("price"), "qqq_chg_pct": qqq.get("day_chg_pct"),
        "qqq_strength": qqq.get("strength"),
    }

    alerts = []
    for p in pf_view:
        for a in p.get("Alerts") or []:
            alerts.append({"ticker": p["Ticker"], "text": a,
                           "severity": "high" if "⛔" in a else "medium"})

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_scanned": total,
        "category_counts": cat_counts,
        "regime": {"regime": regime.get("regime"), "score": regime.get("score"),
                  "drivers": regime.get("drivers"),
                  "multipliers": regime.get("multipliers"),
                  "guidance": regime.get("guidance")},
        "opportunity": opp,
        "market": market,
        "top_day":   [_snap_row(r) for r in top_day],
        "top_swing": [_snap_row(r) for r in top_swing],
        "top_longterm": [_snap_row(r) for r in top_lt],
        "recovery": [{"ticker": r.get("Ticker"), "stage": r.get("Rec_Stage"),
                      "stars": r.get("Rec_Stars"),
                      "price": r.get("Current Price")} for r in recovery],
        "econ_headlines": [{"when": h.get("when"), "title": h.get("title"),
                            "tags": h.get("tags"), "url": h.get("url")}
                           for h in econ_headlines],
        "portfolio": {"totals": pf_totals, "allocation": pf_alloc,
                     "alerts": alerts[:15]},
    }


def _safe_report_name(name: str) -> str:
    """Filename-safe report prefix: alnum only, falls back to 'dashboard'
    for an empty/all-punctuation input so a bad caller can't produce a
    filename starting with '_' or nothing at all."""
    cleaned = "".join(c for c in name if c.isalnum())
    return cleaned or "dashboard"


# A generated report is a standalone file the webapp Dashboard links to as
# /<universe>Report_<ts>.html, so following that link leaves the app with no
# way back. Kept as a module constant with inline styles and no external
# dependencies so scripts/add_report_back_link.py can inject the identical
# markup into reports generated before this existed.
#
# href="/" is what the webapp serves the Dashboard from; the onclick prefers
# history.back() when the page was actually navigated to, which keeps the
# control working for reports opened straight off disk (file://) or through
# the plain http.server on 8788, where "/" is not the Dashboard.
BACK_LINK_MARKER = 'id="back-to-dashboard"'
BACK_TO_DASHBOARD_HTML = (
    '<style>@media print{#back-to-dashboard{display:none}}</style>'
    f'<div {BACK_LINK_MARKER} style="position:sticky;top:0;z-index:50;'
    'background:#f5f4f0;padding:10px 0 12px">'
    '<a href="/" onclick="if(document.referrer&&history.length>1)'
    '{history.back();return false;}" '
    'style="display:inline-flex;align-items:center;gap:7px;font-size:12px;'
    'font-weight:600;text-decoration:none;color:#0C447C;background:#fff;'
    'border:0.5px solid #d9d7ce;border-radius:6px;padding:7px 13px">'
    '<span style="font-size:15px;line-height:1">←</span>'
    ' Back to Dashboard</a></div>'
)


def generate_dashboard(
    rows: list[dict],
    output_dir: str | Path = ".",
    open_browser: bool = True,
    include_market_pulse: bool = True,
    include_portfolio: bool = True,
    report_name: str | None = None,
) -> str:
    """
    Build a Top-5 HTML dashboard from enriched scan rows.

    Parameters
    ----------
    rows         : list of dicts from get_metrics + categorize + enrich_row + put/call candidate
    output_dir   : folder to write the HTML file
    open_browser : auto-open in default browser (default True)
    include_market_pulse : fetch the network-dependent market-pulse header
    include_portfolio    : render the Portfolio & Watchlist panel (positions,
                           P&L, allocation) — off gives a market-only dashboard
    report_name  : filename prefix identifying which scan produced this
                   dashboard, e.g. "sp500Report", "daytradeReport" — the file
                   becomes <report_name>_<YYYYMMDD>_<HHMMSS>.html. Defaults
                   to "dashboard" (the original, universe-agnostic name).

    Returns
    -------
    str : absolute path to the generated HTML file
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    prefix   = _safe_report_name(report_name) if report_name else "dashboard"
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_time = datetime.now().strftime("%B %d, %Y  %H:%M")
    filepath = output_dir / f"{prefix}_{ts}.html"

    # Strategy scores may be missing when a caller passes rows that skipped
    # scan_universe.main() (e.g. saved rows) — compute them here so the
    # Long-Term section never renders empty for lack of columns
    if rows and "Investment_Pass" not in rows[0]:
        try:
            from stockanalysis.core.strategy_scores import attach_strategy_scores
            attach_strategy_scores(rows)
        except Exception as e:
            print(f"[Dashboard] strategy scores unavailable ({e})")

    # Market pulse FIRST — the regime is derived from it, and the regime
    # multiplier must be active before any card's sizing line renders.
    # Network-dependent, so a failure just drops the strip, never the dashboard.
    pulse, pulse_html = None, ""
    econ_headlines, econ_news_html = [], ""
    if include_market_pulse:
        try:
            from stockanalysis.scanners.market_movers import market_pulse
            pulse = market_pulse()
            pulse_html = _market_pulse_html(pulse)
        except Exception as e:
            print(f"[Dashboard] market pulse unavailable ({e}) — header skipped")
        # Macro headlines panel — rendered directly above the pulse strip
        # (which carries the Fed/econ calendar outlook); fetched once here
        # and reused for snapshot.json below
        try:
            econ_headlines = _fetch_econ_headlines()
            econ_news_html = _econ_news_html(econ_headlines)
        except Exception as e:
            print(f"[Dashboard] economic news unavailable ({e}) — skipped")

    # Market regime → position-size multipliers for every card below
    global _ACTIVE_REGIME
    try:
        from stockanalysis.core.market_regime import compute_regime
        _ACTIVE_REGIME = compute_regime(pulse=pulse, rows=rows)
    except Exception as e:
        print(f"[Dashboard] regime unavailable ({e}) — full sizing")
        _ACTIVE_REGIME = {"regime": "Neutral", "source": "none",
                          "multipliers": {"day": 1.0, "swing": 1.0,
                                          "longterm": 1.0}}
    regime = _ACTIVE_REGIME

    # Conviction layer: Quality/Setup/Timing splits, stars, why-checklists,
    # risk tags, READY/WATCH/AVOID actions — feeds the hero panel, priority
    # queue, and every card's conviction strip
    opp = {"score": 0, "stars": 1, "label": "unavailable", "risk": "MEDIUM",
           "n_ready": 0}
    try:
        from stockanalysis.core.conviction import (
            attach_conviction, daily_opportunity)
        attach_conviction(rows)
        opp = daily_opportunity(rows, regime)
    except Exception as e:
        print(f"[Dashboard] conviction layer unavailable ({e})")

    # Per-ticker research pages (data/output/research/<T>.html) — built
    # before any card renders so the 📄 links know which pages exist.
    # Charts need one bars-fetch per ticker; reuse the pulse flag as the
    # "network allowed" signal.
    global _RESEARCH_PAGES
    _RESEARCH_PAGES = set()
    try:
        from stockanalysis.reporting.research import generate_research_pages
        _RESEARCH_PAGES = generate_research_pages(
            rows, output_dir, charts=include_market_pulse,
            fetch_news=include_market_pulse)
        print(f"[Dashboard] {len(_RESEARCH_PAGES)} research pages → "
              f"{output_dir / 'research'}")
    except Exception as e:
        print(f"[Dashboard] research pages failed ({e}) — links skipped")

    # Turnaround recovery watch — beaten-down names becoming investable
    recovery = []
    try:
        from stockanalysis.core.conviction import recovery_candidates
        recovery = recovery_candidates(rows)
    except Exception as e:
        print(f"[Dashboard] recovery watch failed ({e}) — skipped")

    # Compute top 5 for each section
    top_day   = _top5(rows, _day_trade_score,   min_score=20)
    top_swing = _top5(rows, _swing_trade_score, min_score=20)
    top_lt    = _top5(rows, _longterm_score,    min_score=0)
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
    lt_html    = _section("Long-Term Investment Top 5 (all filters pass)",
                          "#639922", "🏦", top_lt, "Long-Term")
    calls_html = _section("Call Options Top 5", "#639922", "🟢", top_calls, "Calls")
    puts_html  = _section("Put Options Top 5",  "#e34948", "🔴", top_puts,  "Puts")

    # Workstation panels: hero score, regime banner, priority queue, heat
    # map, sector rotation, AI brief, decision center, portfolio & watchlist.
    # Each is independent — a failure drops that panel only.
    hero_html     = _hero_html(opp, regime)
    regime_html   = _regime_banner_html(regime)
    decision_html = _decision_center_html(top_day, top_swing, top_lt, regime)
    queue_html = heatmap_html = sector_html = turnaround_html = ""
    day_session_html = ""
    try:
        queue_html   = _priority_queue_html(rows)
        heatmap_html = _heatmap_html(rows)
        sector_html  = _sector_rotation_html(rows)
        turnaround_html = _turnaround_html(recovery)
        day_session_html = _day_session_html(output_dir)
    except Exception as e:
        print(f"[Dashboard] prioritization panels failed ({e}) — skipped")
    try:
        summary_html = _market_summary_html(
            _market_summary_text(regime, rows, top_day, top_swing, top_lt, pulse))
    except Exception as e:
        print(f"[Dashboard] market summary failed ({e}) — skipped")
        summary_html = ""
    portfolio_html = ""
    pf_view, pf_totals, pf_alloc = [], {}, {}
    if include_portfolio:
        try:
            from stockanalysis.reporting.portfolio import (
                load_positions, build_portfolio_view, portfolio_totals,
                allocation_summary)
            pf_view = build_portfolio_view(load_positions(), rows)
            pf_totals = portfolio_totals(pf_view)
            pf_alloc = allocation_summary(pf_view)
            portfolio_html = _portfolio_html(pf_view, pf_totals, pf_alloc)
        except Exception as e:
            print(f"[Dashboard] portfolio panel failed ({e}) — skipped")

    # Gappers table — catalysts need the network, so reuse the pulse flag
    try:
        gappers_html = _gappers_html(rows, fetch_catalysts=include_market_pulse)
    except Exception as e:
        print(f"[Dashboard] gappers section failed ({e}) — skipped")
        gappers_html = ""

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

{BACK_TO_DASHBOARD_HTML}

<h1>Trading Workstation</h1>
<p class="sub">Generated {run_time} · {total} tickers scanned</p>

{hero_html}

{regime_html}

{summary_html}

{day_session_html}

{queue_html}

{decision_html}

{portfolio_html}

{heatmap_html}

{sector_html}

{econ_news_html}

{pulse_html}

{gappers_html}

<div class="grid">
  {day_html}
  {swing_html}
  {lt_html}
  {calls_html}
  {puts_html}
</div>

{turnaround_html}

<p style="margin-top:20px;font-size:11px;color:#898781;text-align:center">
  Not financial advice. All signals are algorithmic — verify before trading.
  Stops and targets are suggestions based on ATR and key levels.
</p>

</body>
</html>"""

    filepath.write_text(html, encoding="utf-8")
    print(f"[Dashboard] Saved → {filepath.resolve()}")

    # snapshot.json — structured summary the webapp reads for its homepage,
    # so it never has to re-run analysis or scrape the HTML. Best-effort:
    # the HTML dashboard is the source of truth, this is a convenience mirror.
    try:
        import json
        snapshot = build_snapshot(
            rows, regime, opp, pulse, top_day, top_swing, top_lt,
            recovery, econ_headlines, pf_view, pf_totals, pf_alloc)
        (output_dir / "snapshot.json").write_text(json.dumps(snapshot, indent=1))
    except Exception as e:
        print(f"[Dashboard] snapshot.json write failed ({e})")

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
