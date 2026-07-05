"""
top5_dashboard.py
=================
Generates a self-contained HTML dashboard showing Top 5 stocks
for Calls, Puts, Swing Trade, and Day Trade after the scan runs.

Usage
-----
    from stockanalysis.reporting.dashboard import generate_dashboard

    # After your main scan loop:
    rows = [...]   # list of enriched dicts from get_metrics + categorize + enrich_row
    generate_dashboard(rows, output_dir=out_dir)
    # → opens Reports/dashboard_YYYYMMDD_HHMM.html in your browser automatically

Or call standalone:
    python top5_dashboard.py  (uses built-in sample data)
"""

from __future__ import annotations
import os
import webbrowser
from datetime import datetime
from pathlib import Path


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
    rvol       = _v(row, "RVOL", 0) or 0
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
    rvol    = _v(row, "RVOL")
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

    # Section-specific signals
    if section == "Day Trade":
        entry   = (f"Above VWAP ${vwap:.2f} — buy Prev-Day High ${pd_high:.2f} breakout with RVOL>1.5"
                   if above_vwap else
                   #pass
                   f"Wait for VWAP reclaim ${vwap} then Prev-Day High ${pd_high}")



        stop    = f"Below Prev-Day Low ${pd_low:.2f} — hard stop: ${price*0.95:.2f} (-5%)"
        target  = (f"T1 ${price*1.03:.2f} (+3%), T2 ${price*1.05:.2f} (+5%). Trail VWAP."
                   if ema8 is None else
                   f"T1 ${price*1.03:.2f} (+3%), T2 8EMA ${ema8:.2f} (+{((ema8/price-1)*100):.1f}%)")

    elif section == "Swing Trade":
        below50 = (ma50 or 0) > price
        entry   = (f"50MA reclaim ${ma50:.2f} with RVOL>1.3" if (below50 and ma50) else
                   f"8EMA hold ${ema8:.2f} — add on 21EMA dip" if ema8 else
                   f"Buy above Prev-Day High ${pd_high:.2f}")
        stop    = (f"Below Prev-Day Low ${pd_low:.2f}. ATR stop: ${price*(1-atr_pct/100*1.5):.2f}"
                   if atr_pct else f"Below ${pd_low:.2f}")
        t1 = ema8 if (ema8 and ema8 > price) else (ma50 if ma50 and ma50 > price else price*1.10)
        t2 = ma200 if (ma200 and ma200 > price) else price * 1.20
        target  = f"T1 ${t1:.2f} ({_pct((t1/price-1)*100)}), T2 ${t2:.2f} ({_pct((t2/price-1)*100)}). Trail 21EMA."

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


def generate_dashboard(
    rows: list[dict],
    output_dir: str | Path = ".",
    open_browser: bool = True,
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

<div class="kpi-row">
  <div class="kpi"><div class="kpi-n">{total}</div><div class="kpi-l">Scanned</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#185FA5">{mom_count}</div><div class="kpi-l">Momentum</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#0F6E56">{mp_count}</div><div class="kpi-l">MP</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#633806">{ta_count}</div><div class="kpi-l">Turnaround</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#3B6D11">{call_count}</div><div class="kpi-l">Call signals</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#A32D2D">{put_count}</div><div class="kpi-l">Put signals</div></div>
  <div class="kpi"><div class="kpi-n" style="color:#898781">{avoid_count}</div><div class="kpi-l">Avoided</div></div>
</div>

<div class="grid">
  {day_html}
  {swing_html}
  {calls_html}
  {puts_html}
</div>

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
if __name__ == "__main__":
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
