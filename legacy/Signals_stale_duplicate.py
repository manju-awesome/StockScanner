"""
grade_signals.py
================
Classifies A/B/C grade and generates Entry / Stop / Target / Notes
directly from the dict returned by get_metrics().

── Quick start ──────────────────────────────────────────────────────────────

    from libs.grade_signals import process_ticker

    # Single ticker — calls get_metrics + categorize + grade internally
    result = process_ticker("NVDA", qqq_3m)
    print(result["Grade"], result["Entry"], result["Stop"], result["Target"])

── Batch (already have metrics rows) ────────────────────────────────────────

    from libs.grade_signals import enrich_rows

    rows = []
    for ticker in tickers:
        m = get_metrics(ticker, qqq_3m)
        cat, reason, score = categorize(m)
        m["Category"], m["Cat_Reason"], m["Rank_Score"] = cat, reason, score
        rows.append(m)

    enrich_rows(rows)   # adds Grade/Entry/Stop/Target/Notes/SizeFlag in-place

── Input — every key get_metrics() returns ──────────────────────────────────

    Required:  Category (set by categorize()), Current Price
    Used:      RS, ADX_14, RSI_14, BB_PctB, ATR_Pct, ATR20, ATR Shrinking,
               8EMA, 21EMA, 50MA, 200MA, 52W High, 52W Low,
               Prev-Day Low, Prev-Day High, VWAP, Above_VWAP,
               Pullback_Vol_Ratio, VolumeDryingUp, Dist_52W_High%,
               Days_Since_52W_High, Above_200MA, RVOL,
               EarningsDate, EarningsBeat, Short_Interest%,
               EPS_Growth%, FCF_Positive, Inst_Own%, Inst_Own_Chg,
               Price_vs_50MA%, Pct_From_52W_Low%, Pct_vs_8EMA
    All other keys are passed through untouched.

── Output columns added by enrich_row() ─────────────────────────────────────

    Grade     : "A" | "B" | "C" | "X"
    SizeFlag  : "FULL" | "HALF" | "QUARTER" | "NONE"
    Entry     : string — specific price trigger + condition
    Stop      : string — stop-loss price + method
    Target    : string — T1 / T2 / T3 with % gain
    Notes     : string — risk flags, earnings, volatility (pipe-separated)

── Grade meanings ────────────────────────────────────────────────────────────

    A  – all quality signals aligned → enter at FULL size
    B  – solid setup, 1-2 minor gaps → enter at FULL or HALF size
    C  – early/marginal → wait for the one missing condition to resolve
    X  – Avoid / gate-failed → no trade
"""

from __future__ import annotations
from typing import Any
from libs.get_metrics3 import get_metrics
from SA_ver4 import categorize


# ── helpers ──────────────────────────────────────────────────────────────────

def _v(row: dict, key: str, default: Any = None) -> Any:
    """Return row[key] if present and not None, else default."""
    val = row.get(key)
    return val if val is not None else default


def _fmt(price: float | None, decimals: int = 2) -> str:
    if price is None:
        return "N/A"
    return f"${price:,.{decimals}f}"


def _pct(val: float | None, decimals: int = 1) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.{decimals}f}%"


def _atr_stop(price: float, atr: float, multiplier: float = 1.5) -> float:
    return round(price - atr * multiplier, 2)


def _pct_stop(price: float, pct: float = 7.0) -> float:
    return round(price * (1 - pct / 100), 2)


def _target(price: float, pct: float) -> float:
    return round(price * (1 + pct / 100), 2)


# ── size flag ─────────────────────────────────────────────────────────────────

def _size_flag(atr_pct: float | None, grade: str) -> str:
    """
    FULL   – ATR% < 5  and grade A or B
    HALF   – ATR% 5-10 OR grade C
    QUARTER– ATR% > 10
    """
    atr = atr_pct or 0
    if atr > 10:
        return "QUARTER"
    if atr > 5 or grade == "C":
        return "HALF"
    return "FULL"


# ── shared risk notes ─────────────────────────────────────────────────────────

def _risk_notes(row: dict) -> list[str]:
    notes = []
    rs        = row.get("RS")
    bb        = _v(row, "BB_PctB", 0)
    rsi       = _v(row, "RSI_14", 50)
    atr_pct   = _v(row, "ATR_Pct", 0)
    earn_date = _v(row, "EarningsDate", "N/A")
    earn_beat = _v(row, "EarningsBeat")
    si        = _v(row, "Short_Interest%", 0)
    vol_dry   = _v(row, "VolumeDryingUp", False)

    if rs is not None and rs < -20:
        notes.append(f"RS={rs:.1f} — heavily lagging QQQ, may be distribution")
    if bb > 1.0:
        notes.append(f"BB_PctB={bb:.3f} — price above upper band, extended")
    if rsi > 72:
        notes.append(f"RSI={rsi:.1f} — overbought, avoid chasing")
    if rsi < 30:
        notes.append(f"RSI={rsi:.1f} — oversold, wait for stabilisation")
    if atr_pct > 10:
        notes.append(f"ATR%={atr_pct:.1f} — HIGH VOLATILITY, use QUARTER size")
    elif atr_pct > 5:
        notes.append(f"ATR%={atr_pct:.1f} — elevated, use HALF size")
    if earn_date and earn_date != "N/A":
        eb_str = "beat" if earn_beat else ("miss" if earn_beat is False else "?")
        notes.append(f"Earnings {earn_date} (last: {eb_str}) — size accordingly")
    if si > 15:
        notes.append(f"Short interest {si:.1f}% — squeeze potential if breaks key level")
    if vol_dry:
        notes.append("Volume drying up — accumulation signal for VCP/MP setups")
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# MOMENTUM
# ─────────────────────────────────────────────────────────────────────────────

def _grade_momentum(row: dict) -> str:
    rs        = _v(row, "RS", -999)
    adx       = _v(row, "ADX_14", 0)
    rsi       = _v(row, "RSI_14", 50)
    rvol      = _v(row, "RVOL", 0)
    above_vwap = _v(row, "Above_VWAP", False)
    days_52h  = _v(row, "Days_Since_52W_High", 999)
    bb        = _v(row, "BB_PctB", 0.5)

    score = 0
    if rs is not None:
        if rs > 50:   score += 3
        elif rs > 20: score += 2
        elif rs > 0:  score += 1
    if adx > 35:      score += 2
    elif adx > 25:    score += 1
    if rsi < 68:      score += 1          # not overbought
    if rvol > 1.5:    score += 1
    if above_vwap:    score += 1
    if days_52h <= 5: score += 1
    if bb <= 0.85:    score += 1          # not extended above upper band

    # Disqualifiers
    if rsi > 74:      score -= 2          # overbought
    if bb > 1.0:      score -= 2          # above upper band

    if score >= 8: return "A"
    if score >= 5: return "B"
    return "C"


def _signals_momentum(row: dict) -> dict:
    price      = _v(row, "Current Price", 0)
    ema8       = _v(row, "8EMA")
    ema21      = _v(row, "21EMA")
    ma50       = _v(row, "50MA")
    high52     = _v(row, "52W High")
    pd_low     = _v(row, "Prev-Day Low")
    pd_high    = _v(row, "Prev-Day High")
    atr        = _v(row, "ATR20", 0)
    vwap       = _v(row, "VWAP")
    days_52h   = _v(row, "Days_Since_52W_High", 999)
    atr_pct    = _v(row, "ATR_Pct", 0)

    # Entry
    if days_52h <= 3:
        entry = (f"Breakout entry — buy above Prev-Day High {_fmt(pd_high)} "
                 f"with RVOL >1.5. Alternatively scale into 8EMA dip {_fmt(ema8)}.")
    elif vwap and price > vwap:
        entry = (f"VWAP hold confirmed. Add above Prev-Day High {_fmt(pd_high)} "
                 f"or on 8EMA dip {_fmt(ema8)}.")
    else:
        entry = (f"Buy on VWAP reclaim {_fmt(vwap)} or 8EMA dip {_fmt(ema8)} "
                 f"with volume expansion (RVOL >1.3).")

    # Stop — tightest of ATR or Prev-Day Low
    atr_s   = _atr_stop(price, atr, 1.0)
    pd_s    = pd_low if pd_low else _pct_stop(price, 5)
    stop_px = max(atr_s, pd_s) if pd_low else atr_s
    stop    = (f"{_fmt(stop_px)} — "
               f"Prev-Day Low {_fmt(pd_low)} / ATR×1.0 {_fmt(atr_s)}. "
               f"Hard stop -5% = {_fmt(_pct_stop(price, 5))}.")

    # Targets
    t1 = _target(price, 5)
    t2 = high52 if high52 else _target(price, 10)
    t3 = _target(price, 20)
    target = (f"T1 {_fmt(t1)} (+5%) — 8EMA trail. "
              f"T2 {_fmt(t2)} (52W high / +{((t2/price-1)*100):.1f}%). "
              f"T3 {_fmt(t3)} (+20%) — trail 21EMA {_fmt(ema21)}.")

    notes = _risk_notes(row)
    if atr_pct < 3:
        notes.append("Low ATR% — suitable for larger position size")

    return dict(entry=entry, stop=stop, target=target,
                notes=notes, size_flag=_size_flag(atr_pct, _grade_momentum(row)))


# ─────────────────────────────────────────────────────────────────────────────
# MOMENTUM-PULLBACK
# ─────────────────────────────────────────────────────────────────────────────

def _grade_mp(row: dict) -> str:
    rs        = _v(row, "RS")
    pvol      = _v(row, "Pullback_Vol_Ratio", 999)
    bb        = _v(row, "BB_PctB", 1.0)
    above200  = _v(row, "Above_200MA", False)
    atr_shrink = _v(row, "ATR Shrinking", False)
    rsi       = _v(row, "RSI_14", 50)
    pct_ema8  = _v(row, "Pct_vs_8EMA", 0)
    rvol      = _v(row, "RVOL", 0)

    score = 0
    if rs is not None:
        if rs > 50:    score += 3
        elif rs > 0:   score += 2
        elif rs > -10: score += 1
    if pvol < 0.8:     score += 2
    elif pvol < 1.0:   score += 1
    if bb < 0.2:       score += 2
    elif bb < 0.4:     score += 1
    if above200:       score += 1
    if atr_shrink:     score += 1
    if abs(pct_ema8) < 5:  score += 1    # close to 8EMA
    elif abs(pct_ema8) < 10: score += 0
    else: score -= 1                      # too extended below 8EMA
    if rvol >= 1.0:    score += 1
    if rsi < 65:       score += 1

    if score >= 11: return "A"
    if score >= 7:  return "B"
    return "C"


def _signals_mp(row: dict) -> dict:
    price     = _v(row, "Current Price", 0)
    ema8      = _v(row, "8EMA")
    ema21     = _v(row, "21EMA")
    ma50      = _v(row, "50MA")
    ma200     = _v(row, "200MA")
    high52    = _v(row, "52W High")
    pd_low    = _v(row, "Prev-Day Low")
    atr       = _v(row, "ATR20", 0)
    atr_pct   = _v(row, "ATR_Pct", 0)
    bb        = _v(row, "BB_PctB", 0.5)
    pct_ema8  = _v(row, "Pct_vs_8EMA", 0)
    atr_shrink = _v(row, "ATR Shrinking", False)

    # Entry — tiered by how close to 8EMA
    if pct_ema8 is not None and abs(pct_ema8) <= 3:
        entry = (f"Near 8EMA — buy hold above {_fmt(ema8)} (8EMA) "
                 f"with RVOL expansion >1.2. Confirm VWAP hold.")
    elif ma50 and price < ma50:
        entry = (f"Primary: 50MA reclaim above {_fmt(ma50)} with volume surge (RVOL >1.5). "
                 f"Secondary: 8EMA reclaim {_fmt(ema8)}. "
                 f"Do NOT buy before 50MA reclaim.")
    else:
        entry = (f"8EMA reclaim above {_fmt(ema8)} on volume (RVOL >1.3). "
                 f"Scale add on pullback to 21EMA {_fmt(ema21)}.")

    # Stop
    atr_s   = _atr_stop(price, atr, 1.5)
    pd_s    = pd_low if pd_low else _pct_stop(price, 7)
    stop_px = min(atr_s, pd_s)  # use the lower (further) of the two for MP
    stop    = (f"{_fmt(stop_px)} — "
               f"Prev-Day Low {_fmt(pd_low)} / ATR×1.5 {_fmt(atr_s)}. "
               f"Hard stop -7% = {_fmt(_pct_stop(price, 7))}.")

    # Targets — step through MAs toward 52W high
    t_steps = []
    if ema8   and ema8   > price: t_steps.append((ema8,  "8EMA"))
    if ma50   and ma50   > price: t_steps.append((ma50,  "50MA"))
    if ma200  and ma200  > price: t_steps.append((ma200, "200MA"))
    if high52 and high52 > price: t_steps.append((high52, "52W high"))
    if not t_steps:
        t_steps = [(_target(price, 10), "+10%"), (_target(price, 20), "+20%")]

    t_parts = []
    for i, (px, label) in enumerate(t_steps[:3], 1):
        t_parts.append(f"T{i} {_fmt(px)} ({label} / {_pct((px/price-1)*100)})")
    target = " → ".join(t_parts) + f". Trail 21EMA {_fmt(ema21)} once in profit."

    notes = _risk_notes(row)
    if atr_shrink:
        notes.append("ATR Shrinking=TRUE — volatility contraction confirms pullback quality")
    if bb < 0.2:
        notes.append(f"BB_PctB={bb:.3f} — extremely coiled, breakout imminent")

    grade = _grade_mp(row)
    return dict(entry=entry, stop=stop, target=target,
                notes=notes, size_flag=_size_flag(atr_pct, grade))


# ─────────────────────────────────────────────────────────────────────────────
# VCP SETUP
# ─────────────────────────────────────────────────────────────────────────────

def _grade_vcp(row: dict) -> str:
    atr_shrink = _v(row, "ATR Shrinking", False)
    adx        = _v(row, "ADX_14", 0)
    bb         = _v(row, "BB_PctB", 1.0)
    rvol       = _v(row, "RVOL", 0)
    rs         = _v(row, "RS")
    above200   = _v(row, "Above_200MA", False)
    inst_chg   = row.get("Inst_Own_Chg")
    vol_dry    = _v(row, "VolumeDryingUp", False)

    score = 0
    if atr_shrink:              score += 3   # core VCP signal
    if adx >= 25:               score += 2
    elif adx >= 18:             score += 1
    if bb < 0.2:                score += 2
    elif bb < 0.4:              score += 1
    if rvol < 0.6:              score += 2   # very quiet base
    elif rvol < 0.8:            score += 1
    if vol_dry:                 score += 1
    if above200:                score += 1
    if rs is not None and rs>0: score += 1
    if inst_chg is not None and inst_chg > 0: score += 1

    if score >= 10: return "A"
    if score >= 6:  return "B"
    return "C"


def _signals_vcp(row: dict) -> dict:
    price     = _v(row, "Current Price", 0)
    ema8      = _v(row, "8EMA")
    ema21     = _v(row, "21EMA")
    ma50      = _v(row, "50MA")
    ma200     = _v(row, "200MA")
    high52    = _v(row, "52W High")
    pd_high   = _v(row, "Prev-Day High")
    pd_low    = _v(row, "Prev-Day Low")
    atr       = _v(row, "ATR20", 0)
    atr_pct   = _v(row, "ATR_Pct", 0)
    bb        = _v(row, "BB_PctB", 0.5)

    # Entry — pivot breakout above base high
    pivot = pd_high or _target(price, 3)
    entry = (f"Pivot breakout above {_fmt(pivot)} (Prev-Day High) on volume surge RVOL >2.0. "
             f"Do NOT enter during base — wait for the breakout candle to close above pivot. "
             f"Ideal BB_PctB < 0.3 before entry (current: {bb:.3f}).")

    # Stop — below base low (wider than MP)
    atr_s   = _atr_stop(price, atr, 2.0)
    pd_s    = pd_low if pd_low else _pct_stop(price, 8)
    stop_px = min(atr_s, pd_s)
    stop    = (f"{_fmt(stop_px)} — "
               f"below base (Prev-Day Low {_fmt(pd_low)} / ATR×2.0 {_fmt(atr_s)}). "
               f"Hard stop -8% = {_fmt(_pct_stop(price, 8))}. "
               f"VCP stops are WIDER — size accordingly.")

    # Targets — bigger reward as VCP is a multi-week setup
    t1 = _target(price, 10)
    t2 = ma50 if (ma50 and ma50 > price) else _target(price, 20)
    t3 = high52 if (high52 and high52 > price) else _target(price, 40)
    target = (f"T1 {_fmt(t1)} (+10%). "
              f"T2 {_fmt(t2)} (50MA / {_pct((t2/price-1)*100)}). "
              f"T3 {_fmt(t3)} (52W high / {_pct((t3/price-1)*100)}). "
              f"Hold weeks — trail 21EMA {_fmt(ema21)}.")

    notes = _risk_notes(row)
    notes.append("VCP: Do not chase. Only enter on confirmed pivot breakout with volume.")
    if bb > 0.4:
        notes.append(f"BB_PctB={bb:.3f} — base not yet tight enough. Wait for further compression.")

    grade = _grade_vcp(row)
    return dict(entry=entry, stop=stop, target=target,
                notes=notes, size_flag=_size_flag(atr_pct, grade))


# ─────────────────────────────────────────────────────────────────────────────
# TURNAROUND
# ─────────────────────────────────────────────────────────────────────────────

def _grade_turnaround(row: dict) -> str:
    rs         = _v(row, "RS")
    atr_shrink = _v(row, "ATR Shrinking", False)
    adx        = _v(row, "ADX_14", 0)
    rsi        = _v(row, "RSI_14", 50)
    p50pct     = _v(row, "Price_vs_50MA%", -999)
    eb         = _v(row, "EarningsBeat", False)
    si         = _v(row, "Short_Interest%", 0)
    above200   = _v(row, "Above_200MA", False)
    rvol       = _v(row, "RVOL", 0)

    score = 0
    if atr_shrink:        score += 2   # base forming
    if rs is not None:
        if rs > 0:        score += 2
        elif rs > -20:    score += 1
    if adx >= 20:         score += 1
    if rsi > 35:          score += 1   # not in freefall
    if p50pct > 0:        score += 2   # reclaimed 50MA
    if eb:                score += 2   # catalyst present
    if si > 15:           score += 1   # squeeze fuel
    if rvol > 1.5:        score += 1   # volume on reversal
    if above200:          score += 1

    # Disqualifiers
    if rs is not None and rs < -50: score -= 2   # structural downtrend
    if rsi < 25:                    score -= 1   # still in freefall

    if score >= 9: return "B"    # Turnaround max is B (speculative by nature)
    return "C"


def _signals_turnaround(row: dict) -> dict:
    price     = _v(row, "Current Price", 0)
    ema8      = _v(row, "8EMA")
    ema21     = _v(row, "21EMA")
    ma50      = _v(row, "50MA")
    ma200     = _v(row, "200MA")
    high52    = _v(row, "52W High")
    pd_low    = _v(row, "Prev-Day Low")
    low52     = _v(row, "52W Low")
    atr       = _v(row, "ATR20", 0)
    atr_pct   = _v(row, "ATR_Pct", 0)
    p50pct    = _v(row, "Price_vs_50MA%", -999)
    eb        = _v(row, "EarningsBeat", False)

    # Entry — base after spike, or 50MA reclaim
    if p50pct and p50pct > 0:
        entry = (f"Above 50MA — buy hold above 8EMA {_fmt(ema8)} on volume (RVOL >1.5). "
                 f"Confirmed reversal in progress.")
    elif ma50 and price < ma50:
        entry = (f"Primary trigger: 50MA reclaim above {_fmt(ma50)}. "
                 f"Do NOT enter below 50MA — wait for close above it. "
                 f"Pre-position: small starter at Prev-Day High {_fmt(_v(row,'Prev-Day High'))} "
                 f"if earnings beat catalyst present.")
    else:
        entry = (f"Base after reversal spike. Entry on first pullback hold above 8EMA {_fmt(ema8)}. "
                 f"Confirm with RVOL >1.5 on bounce day.")

    # Stop — WIDER (ATR×2.0 or below 52W low)
    atr_s    = _atr_stop(price, atr, 2.0)
    base_stop = low52 if (low52 and low52 < price * 0.95) else _pct_stop(price, 10)
    stop_px  = max(atr_s, base_stop)   # use higher of the two for turnaround
    stop     = (f"{_fmt(stop_px)} — "
                f"ATR×2.0 {_fmt(atr_s)} / 52W Low area {_fmt(low52)}. "
                f"Hard stop -10% = {_fmt(_pct_stop(price, 10))}. "
                f"TURNAROUND — VERY SMALL position, wider stop mandatory.")

    # Targets — step through MAs
    t_steps = []
    if ma50  and ma50  > price: t_steps.append((ma50,  "50MA"))
    if ma200 and ma200 > price: t_steps.append((ma200, "200MA"))
    if ema8  and ema8  > price: t_steps.append((ema8,  "8EMA"))
    if not t_steps:
        t_steps = [(_target(price, 15), "+15%"), (_target(price, 30), "+30%")]

    t_parts = []
    for i, (px, label) in enumerate(sorted(t_steps, key=lambda x: x[0])[:3], 1):
        t_parts.append(f"T{i} {_fmt(px)} ({label} / {_pct((px/price-1)*100)})")
    target = " → ".join(t_parts) + ". Take 50% off at T1, trail rest with 21EMA."

    notes = _risk_notes(row)
    notes.append("TURNAROUND — speculative category. Use QUARTER size minimum.")
    if not eb:
        notes.append("No recent earnings beat — higher risk without catalyst confirmation")

    grade = _grade_turnaround(row)
    return dict(entry=entry, stop=stop, target=target,
                notes=notes, size_flag="QUARTER")   # always quarter for TA


# ─────────────────────────────────────────────────────────────────────────────
# LONGTERM HOLD
# ─────────────────────────────────────────────────────────────────────────────

def _grade_longterm(row: dict) -> str:
    eps_g    = _v(row, "EPS_Growth%", -999)
    fcf      = _v(row, "FCF_Positive", False)
    inst_chg = row.get("Inst_Own_Chg")
    inst_own = _v(row, "Inst_Own%", 0)
    rs       = _v(row, "RS")
    rvol     = _v(row, "RVOL", 0)
    pct_low  = _v(row, "Pct_From_52W_Low%", 999)

    score = 0
    if eps_g > 20:                              score += 3
    elif eps_g > 15:                            score += 2
    elif eps_g > 0:                             score += 1
    if fcf:                                     score += 2
    if inst_chg is not None and inst_chg > 0:  score += 2
    if inst_own > 50:                           score += 1
    if rs is not None and rs > -10:             score += 1
    if rvol > 1.5:                              score += 1
    if pct_low <= 5:                            score += 1   # very near 52W low = best LT entry

    if score >= 9: return "A"
    if score >= 6: return "B"
    return "C"


def _signals_longterm(row: dict) -> dict:
    price     = _v(row, "Current Price", 0)
    ema21     = _v(row, "21EMA")
    ma50      = _v(row, "50MA")
    ma200     = _v(row, "200MA")
    high52    = _v(row, "52W High")
    low52     = _v(row, "52W Low")
    pd_low    = _v(row, "Prev-Day Low")
    atr       = _v(row, "ATR20", 0)
    atr_pct   = _v(row, "ATR_Pct", 0)
    pct_low   = _v(row, "Pct_From_52W_Low%", 0)
    eps_g     = _v(row, "EPS_Growth%", 0)

    entry = (f"Near 52W Low ({_pct(pct_low)} above low {_fmt(low52)}). "
             f"Accumulate in tranches: 1/3 now at {_fmt(price)}, "
             f"1/3 on any dip to 52W Low area {_fmt(low52)}, "
             f"1/3 on 50MA reclaim {_fmt(ma50)}. "
             f"Hold 6-18 months.")

    # Stop — below 52W low
    stop_px = round(low52 * 0.97, 2) if low52 else _pct_stop(price, 12)
    stop    = (f"{_fmt(stop_px)} — 3% below 52W Low {_fmt(low52)}. "
               f"Hard stop -12% = {_fmt(_pct_stop(price, 12))}. "
               f"Exit if: earnings miss + guidance cut, RS falls below -30, "
               f"or 52W Low breaks on heavy volume.")

    # Targets — 12-18 month horizon
    t1 = ma200 if (ma200 and ma200 > price) else _target(price, 20)
    t2 = ma50  if (ma50  and ma50  > price) else _target(price, 35)
    t3 = high52 if (high52 and high52 > price) else _target(price, 60)
    target = (f"T1 {_fmt(t1)} (200MA / 6-9 months). "
              f"T2 {_fmt(t2)} (50MA reclaim). "
              f"T3 {_fmt(t3)} (52W High / 12-18 months). "
              f"Trail 21EMA {_fmt(ema21)} after price recovers above 200MA.")

    notes = _risk_notes(row)
    notes.append(f"EPS growth {_pct(eps_g)} — {'strong fundamental✓' if eps_g > 15 else 'below 15% threshold⚠'}")
    notes.append("EXIT triggers: 52W Low breaks, earnings miss + guidance cut, RS < -30")

    grade = _grade_longterm(row)
    return dict(entry=entry, stop=stop, target=target,
                notes=notes, size_flag=_size_flag(atr_pct, grade))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────────────────────────

def classify_grade_and_signals(row: dict) -> tuple[str, dict]:
    """
    Given a fully-populated metrics dict (with Category already set),
    returns (grade: str, signals: dict).

    signals keys: entry, stop, target, notes (list[str]), size_flag
    """
    cat = row.get("Category", "Avoid")

    if cat == "Momentum":
        grade   = _grade_momentum(row)
        signals = _signals_momentum(row)

    elif cat == "Momentum-Pullback":
        grade   = _grade_mp(row)
        signals = _signals_mp(row)

    elif cat == "VCP Setup":
        grade   = _grade_vcp(row)
        signals = _signals_vcp(row)

    elif cat == "Turnaround":
        grade   = _grade_turnaround(row)
        signals = _signals_turnaround(row)

    elif cat == "Longterm Hold":
        grade   = _grade_longterm(row)
        signals = _signals_longterm(row)

    else:
        # Avoid / Error / gate-failed
        avoid_reason = row.get("Entry_Gate_Reason") or row.get("Cat_Reason") or "No category matched"
        return "X", dict(
            entry="No trade — Avoid",
            stop="N/A",
            target="N/A",
            notes=[f"Avoid: {avoid_reason}"],
            size_flag="NONE",
        )

    # Ensure size_flag is always set correctly
    signals["size_flag"] = _size_flag(row.get("ATR_Pct"), grade)
    if cat == "Turnaround":
        signals["size_flag"] = "QUARTER"

    return grade, signals


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def enrich_row(row: dict) -> dict:
    """
    Add Grade/Entry/Stop/Target/Notes/SizeFlag to a metrics dict in-place.

    Expects row["Category"] already set by categorize().
    All other fields come directly from get_metrics() output — no mapping needed.

    Returns the same dict (also mutated in-place).
    """
    grade, signals = classify_grade_and_signals(row)
    row["Grade"]    = grade
    row["SizeFlag"] = signals["size_flag"]
    row["Entry"]    = signals["entry"]
    row["Stop"]     = signals["stop"]
    row["Target"]   = signals["target"]
    row["Notes"]    = " | ".join(signals["notes"]) if signals["notes"] else ""
    return row


def enrich_rows(rows: list[dict]) -> list[dict]:
    """
    Batch-enrich a list of metric dicts (in-place).
    Each row must already have Category/Cat_Reason/Rank_Score set by categorize().

    Returns the same list.
    """
    for row in rows:
        enrich_row(row)
    return rows


def process_ticker(
    ticker: str,
    qqq_return_3m: float,
    get_metrics_fn,
    categorize_fn,
) -> dict:
    """
    Full single-ticker pipeline:
        get_metrics  →  categorize  →  grade + signals

    Parameters
    ----------
    ticker          : e.g. "NVDA"
    qqq_return_3m   : float from fetch_qqq_return()
    get_metrics_fn  : the get_metrics function from get_metrics3.py
    categorize_fn   : the categorize function from stock_categorizer.py

    Returns
    -------
    Fully enriched dict with all metrics + Category + Grade + Entry/Stop/Target/Notes.

    Example
    -------
        from libs.get_metrics3 import get_metrics
        from stock_categorizer import categorize, fetch_qqq_return
        from libs.grade_signals import process_ticker

        qqq_3m = fetch_qqq_return()
        result = process_ticker("NVDA", qqq_3m, get_metrics, categorize)
        print(result["Grade"], result["Entry"])
    """
    row = get_metrics_fn(ticker, qqq_return_3m)
    cat, reason, score = categorize_fn(row)
    row["Category"]   = cat
    row["Cat_Reason"] = reason
    row["Rank_Score"] = score
    enrich_row(row)
    return row


def process_tickers(
    tickers: list[str],
    qqq_return_3m: float,
    get_metrics,
    categorize,
    delay: float = 0.25,
) -> list[dict]:
    """
    Full batch pipeline for a list of tickers.

    Parameters
    ----------
    tickers         : list of ticker strings
    qqq_return_3m   : float from fetch_qqq_return()
    get_metrics_fn  : get_metrics from get_metrics3.py
    categorize_fn   : categorize from stock_categorizer.py
    delay           : seconds between requests (default 0.25 to avoid rate-limit)

    Returns
    -------
    List of fully enriched dicts sorted by Category priority then Rank_Score.

    Example
    -------
        from libs.get_metrics3 import get_metrics
        from stock_categorizer import categorize, fetch_qqq_return
        from libs.grade_signals import process_tickers

        qqq_3m = fetch_qqq_return()
        rows = process_tickers(["NVDA","AMD","PANW"], qqq_3m, get_metrics, categorize)
        for r in rows:
            print(r["Ticker"], r["Category"], r["Grade"], r["Entry"][:60])
    """
    import time
    import logging
    log = logging.getLogger("grade_signals")

    rows = []
    for i, ticker in enumerate(tickers, 1):
        log.info("[%d/%d] %s", i, len(tickers), ticker)
        try:
            row = process_ticker(ticker, qqq_return_3m, get_metrics, categorize)
            rows.append(row)
        except Exception as e:
            log.error("%s: failed — %s", ticker, e)
            rows.append({
                "Ticker": ticker, "Category": "Error", "Grade": "X",
                "Cat_Reason": str(e), "Rank_Score": 0,
                "Entry": "Error", "Stop": "N/A", "Target": "N/A",
                "Notes": str(e), "SizeFlag": "NONE",
            })
        if delay and i < len(tickers):
            time.sleep(delay)

    cat_order = {
        "Momentum": 1, "Momentum-Pullback": 2, "VCP Setup": 3,
        "Turnaround": 4, "Longterm Hold": 5, "Avoid": 6, "Error": 7,
    }
    grade_order = {"A": 0, "B": 1, "C": 2, "X": 3}
    rows.sort(key=lambda r: (
        cat_order.get(r.get("Category", "Error"), 8),
        grade_order.get(r.get("Grade", "X"), 4),
        -(r.get("Rank_Score") or 0),
    ))
    return rows
# Strong put candidates need ALL of these:
def is_put_candidate():
    is_put_candidate = (
        # 1. Parabolic speed — big move in few days
        dist_52wh >= -5 and days_since_52wh <= 10 and    # new 52W high, set very recently

        # 2. Extended above 8EMA
        pct_vs_8ema > 5 and                               # >5% above 8EMA

        # 3. Overbought
        rsi > 72 and                                      # overbought

        # 4. Volume exhaustion — the key signal
        rvol < 0.8 and                                    # volume drying up TODAY
        vol_vs_20d < 0.9 and                              # below 20-day avg volume

        # 5. BB extended — above upper band
        bb_pctb > 1.0 and                                 # price above upper Bollinger band

        # 6. Price action topping signals (at least one)
        (
            prev_day_high < two_days_ago_high or          # lower high forming
            price < vwap or                               # lost VWAP intraday
            atr_shrinking == False                        # range expanding = volatile top
        )
    )


# ── CSV column order including new signal columns ─────────────────────────────

SIGNAL_COLUMNS = ["Grade", "SizeFlag", "Entry", "Stop", "Target", "Notes"]

CSV_PRIORITY = [
    "Ticker", "LongName", "Sector",
    "Category", "Grade", "SizeFlag", "Cat_Reason", "Rank_Score",
    "Entry", "Stop", "Target", "Notes",
    "Entry_Gate_Pass", "Entry_Gate_Reason",
    "Current Price", "Dist_52W_High%", "Pct_From_52W_Low%",
    "52W_High_Date", "Days_Since_52W_High",
    "RVOL", "Vol_vs_20D", "Pullback_Vol_Ratio",
    "RSI_14", "ADX_14", "Trend_Strength", "BB_PctB",
    "200MA", "50MA", "8EMA", "21EMA",
    "Price_vs_200MA%", "Price_vs_50MA%", "Pct_vs_8EMA",
    "Above_200MA", "Above_VWAP", "VWAP",
    "ATR20", "ATR_Pct", "ATR Shrinking",
    "RS", "CANSLIM_Pass",
    "EPS_Growth%", "Revenue", "Inst_Own%", "Inst_Own_Chg",
    "FCF_Positive", "Short_Interest%", "EarningsBeat", "EarningsDate",
    "MarketCap",
    "52W High", "52W Low",
    "Prev-Day High", "Prev-Day Low",
    "Pre-Market High", "Pre-Market Low",
]


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE — run directly to test against live yfinance data
# python grade_signals.py                        # uses built-in test tickers
# python grade_signals.py NVDA AMD PANW CRDO     # pass tickers as CLI args
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")

    # Try to import the real pipeline; fall back to offline sample data
    try:
        # Adjust import paths if running outside the project root
        sys.path.insert(0, "../tests")
        sys.path.insert(0, "..")
        from libs.get_metrics3 import get_metrics          # type: ignore
        from stock_categorizer import categorize, fetch_qqq_return  # type: ignore
        LIVE = True
    except ImportError:
        LIVE = False

    cli_tickers = sys.argv[1:] if len(sys.argv) > 1 else []

    if LIVE and cli_tickers:
        # ── Live mode: fetch real data for CLI-supplied tickers ───────────────
        print(f"\nFetching QQQ benchmark …")
        qqq_3m = fetch_qqq_return()
        print(f"QQQ 3m return: {qqq_3m:.2f}%\n")

        rows = process_tickers(cli_tickers, qqq_3m, get_metrics, categorize)

        print("=" * 90)
        print(f"{'Ticker':<7} {'Category':<22} {'Grade':<6} {'Size':<9} {'RS':>8}  Entry (first 70 chars)")
        print("=" * 90)
        for r in rows:
            entry_preview = (r.get("Entry") or "")[:70]
            print(f"{r['Ticker']:<7} {r.get('Category','?'):<22} "
                  f"{r.get('Grade','?'):<6} {r.get('SizeFlag','?'):<9} "
                  f"{(r.get('RS') or 0):>8.1f}  {entry_preview}")
            notes = r.get("Notes", "")
            if notes:
                for n in notes.split(" | "):
                    print(f"{'':>7}   NOTE: {n}")
            print()

    else:
        # ── Offline mode: built-in sample rows (no yfinance needed) ──────────
        sample_rows = [
            dict(
                Ticker="PANW", Category="Momentum",
                **{"Current Price": 304.20, "RS": 72.0, "ADX_14": 40.2, "RSI_14": 66.1,
                   "BB_PctB": 1.018, "ATR_Pct": 4.43, "ATR20": 13.48,
                   "Dist_52W_High%": -0.7, "Days_Since_52W_High": 1,
                   "Above_200MA": True, "ATR Shrinking": True, "RVOL": 0.88,
                   "8EMA": 287.54, "21EMA": 275.70, "50MA": 237.34, "200MA": 198.51,
                   "52W High": 306.24, "52W Low": 139.57,
                   "Prev-Day Low": 290.00, "Prev-Day High": 306.24,
                   "VWAP": 301.04, "Above_VWAP": True,
                   "Pullback_Vol_Ratio": 0.65, "VolumeDryingUp": False,
                   "EarningsDate": "8/18/2026", "EarningsBeat": None,
                   "Short_Interest%": 2.8},
            ),
            dict(
                Ticker="NVDA", Category="Momentum-Pullback",
                **{"Current Price": 192.53, "RS": -10.49, "ADX_14": 16.1, "RSI_14": 39.6,
                   "BB_PctB": 0.021, "ATR_Pct": 4.13, "ATR20": 7.94,
                   "Dist_52W_High%": -18.6, "Days_Since_52W_High": 44,
                   "Above_200MA": True, "ATR Shrinking": True, "RVOL": 1.10,
                   "8EMA": 201.27, "21EMA": 206.17, "50MA": 210.15, "200MA": 190.66,
                   "52W High": 236.54, "52W Low": 151.49,
                   "Prev-Day Low": 191.22, "Prev-Day High": 195.74,
                   "VWAP": 193.38, "Above_VWAP": False,
                   "Pullback_Vol_Ratio": 0.85, "VolumeDryingUp": False,
                   "EarningsDate": "8/26/2026", "EarningsBeat": True,
                   "Short_Interest%": 1.3, "Pct_vs_8EMA": -4.34},
            ),
            dict(
                Ticker="IONQ", Category="VCP Setup",
                **{"Current Price": 49.31, "RS": 56.45, "ADX_14": 20.4, "RSI_14": 41.3,
                   "BB_PctB": 0.096, "ATR_Pct": 11.30, "ATR20": 5.57,
                   "Dist_52W_High%": -41.7, "Days_Since_52W_High": 257,
                   "Above_200MA": False, "ATR Shrinking": True, "RVOL": 0.69,
                   "8EMA": 54.44, "21EMA": 56.71, "50MA": 54.53, "200MA": 49.59,
                   "52W High": 84.64, "52W Low": 25.89,
                   "Prev-Day Low": 48.77, "Prev-Day High": 52.48,
                   "VWAP": 49.87, "Above_VWAP": False,
                   "Pullback_Vol_Ratio": 0.88, "VolumeDryingUp": True,
                   "EarningsDate": "8/5/2026", "EarningsBeat": None,
                   "Short_Interest%": 13.6, "Inst_Own_Chg": None},
            ),
            dict(
                Ticker="SMCI", Category="Turnaround",
                **{"Current Price": 30.63, "RS": 16.86, "ADX_14": 22.9, "RSI_14": 45.5,
                   "BB_PctB": 0.292, "ATR_Pct": 12.76, "ATR20": 3.91,
                   "Dist_52W_High%": -50.9, "Days_Since_52W_High": 331,
                   "Above_200MA": False, "ATR Shrinking": True, "RVOL": 0.91,
                   "8EMA": 32.39, "21EMA": 34.05, "50MA": 33.52, "200MA": 35.35,
                   "52W High": 62.36, "52W Low": 19.48,
                   "Prev-Day Low": 30.28, "Prev-Day High": 31.71,
                   "VWAP": 30.89, "Above_VWAP": False,
                   "Pullback_Vol_Ratio": 0.65, "VolumeDryingUp": False,
                   "EarningsDate": "8/4/2026", "EarningsBeat": True,
                   "Short_Interest%": 19.4, "Price_vs_50MA%": -8.6},
            ),
            dict(
                Ticker="MSFT", Category="Longterm Hold",
                **{"Current Price": 372.97, "RS": -28.44, "ADX_14": 24.4, "RSI_14": 28.7,
                   "BB_PctB": 0.29, "ATR_Pct": 3.72, "ATR20": 13.87,
                   "Dist_52W_High%": -32.9, "Days_Since_52W_High": 331,
                   "Above_200MA": False, "ATR Shrinking": False, "RVOL": 4.46,
                   "8EMA": 369.32, "21EMA": 388.86, "50MA": 410.57, "200MA": 447.89,
                   "52W High": 555.45, "52W Low": 349.20,
                   "Prev-Day Low": 355.43, "Prev-Day High": 376.61,
                   "VWAP": 370.48, "Above_VWAP": True,
                   "Pullback_Vol_Ratio": 1.69, "VolumeDryingUp": False,
                   "EarningsDate": "7/29/2026", "EarningsBeat": True,
                   "Short_Interest%": 1.3, "EPS_Growth%": 15.4,
                   "FCF_Positive": True, "Inst_Own%": 18.3,
                   "Inst_Own_Chg": None, "Pct_From_52W_Low%": 6.8},
            ),
            dict(
                Ticker="COIN", Category="Avoid",
                Entry_Gate_Pass=False, Entry_Gate_Reason=">30%below200MA",
                **{"Current Price": 149.06, "RS": -38.9},
            ),
        ]

        print("=" * 80)
        print("OFFLINE TEST (no yfinance) — pass ticker args for live mode:")
        print("  python grade_signals.py NVDA AMD PANW")
        print("=" * 80)

        for row in sample_rows:
            enrich_row(row)
            print(f"\n{'─'*70}")
            print(f"  {row['Ticker']:<8} │ {row['Category']:<22} │ "
                  f"Grade: {row['Grade']}  │ Size: {row['SizeFlag']}")
            print(f"{'─'*70}")
            print(f"  ENTRY  : {row['Entry'][:100]}")
            print(f"  STOP   : {row['Stop'][:100]}")
            print(f"  TARGET : {row['Target'][:100]}")
            for n in (row["Notes"] or "").split(" | "):
                if n:
                    print(f"  NOTE   : {n}")