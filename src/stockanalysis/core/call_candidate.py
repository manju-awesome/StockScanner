"""
call_candidate.py
=================
Identifies call option buying opportunities specifically for Turnaround category stocks.
Also works as a secondary screen for Momentum-Pullback stocks near key support.

Call options on turnarounds have a specific edge:
  - Stock has already dropped 40-65% (puts are expensive, calls are cheap)
  - Implied Volatility is elevated from the decline → but starting to compress
  - A catalyst (earnings beat, short squeeze, sector rotation) provides a defined trigger
  - Asymmetric payoff: limited downside (premium paid), large upside if reversal holds

Add to get_metrics() just before return:

    from libs.call_candidate import compute_call_candidate
    compute_call_candidate(row)

Or call after categorize() so Category is available:

    row = get_metrics(ticker, qqq_3m)
    cat, reason, score = categorize(row)
    row["Category"] = cat
    compute_call_candidate(row)

Output columns added (in-place):
    Call_Candidate   : bool
    Call_Score       : int
    Call_Strength    : str  — "STRONG" | "MODERATE" | "WEAK" | "DISQUALIFIED"
    Call_Reason      : str  — pipe-separated signal explanations
    Call_Strike_Hint : str  — suggested strike zone and expiry guidance
"""

from __future__ import annotations


# ── Turnaround call option thresholds ─────────────────────────────────────────
CFG_CALL_DIST_MIN          = -65    # stock must be 40-65% off 52W high (TA range)
CFG_CALL_DIST_MAX          = -35    # don't buy calls on stocks > 35% off high
                                    # (too far = too speculative, IV too high)
CFG_CALL_RSI_MIN           = 30     # RSI must be stabilising — not in freefall
CFG_CALL_RSI_MAX           = 60     # not already recovered (calls cheap when RSI low)
CFG_CALL_ADX_MIN           = 15     # some directional structure needed
CFG_CALL_BB_COILED         = 0.4    # BB_PctB < 0.4 = base compressing = coiled
CFG_CALL_ATR_MAX           = 14     # ATR% > 14 = IV too expensive for calls
CFG_CALL_SHORT_INT         = 10     # short interest > 10% = squeeze catalyst fuel
CFG_CALL_RS_DISQ           = -55    # RS below this = structural downtrend, skip
CFG_CALL_DAYS_DISQ         = 370    # 52W high set more than this long ago = dead base
CFG_CALL_50MA_NEAR         = -12    # within 12% of 50MA = near reclaim trigger
CFG_CALL_STRONG_THRESHOLD  = 7      # score >= 7 = STRONG
CFG_CALL_MODERATE_THRESHOLD= 4      # score >= 4 = MODERATE


def compute_call_candidate(row: dict) -> dict:
    """
    Evaluates whether a Turnaround (or MP) stock is a call option buying candidate.

    Scoring (max possible ~18):
    ──────────────────────────
    CATALYST signals (most important):
      +4  Earnings beat in most recent quarter
      +3  Short interest > 10% (squeeze potential)
      +2  Short interest > 15% (high squeeze fuel)
      +2  RS > 0 (positive vs QQQ — money rotating in)
      +1  RS > 20 (strong rotation)

    BASE / COMPRESSION signals:
      +3  ATR Shrinking = TRUE (volatility contracting = base forming)
      +2  BB_PctB < 0.2 (ultra-coiled — breakout imminent)
      +1  BB_PctB 0.2-0.4 (coiling)
      +2  VolumeDryingUp = TRUE (quiet base = accumulation)

    PRICE ACTION signals:
      +2  Above 200MA (structure intact)
      +2  Above VWAP (intraday buyers in control)
      +2  Price within 12% of 50MA (about to reclaim key level)
      +1  Price within 20% of 50MA (approaching)
      +1  RSI 35-60 (stabilising, not oversold freefall)
      +2  RSI 30-35 (bouncing from oversold — high reward zone)
      +1  ADX > 20 (some trend structure)
      +1  ADX > 30 (clear trend structure)

    DISQUALIFIERS (hard-block):
    ───────────────────────────
      RS < -55                  — structural downtrend, call will expire worthless
      RSI < 25                  — still in freefall, no floor yet
      ATR% > 14                 — IV too expensive, calls overpriced
      Days_Since_52W_High > 370 — dead base, no catalyst momentum
      No EarningsBeat AND RS<0  — no catalyst + underperforming = avoid
      Category == "Avoid"       — failed entry gate entirely

    Call_Strike_Hint guidance:
    ──────────────────────────
      STRONG   → Buy slightly OTM calls (5% above current price), 45-60 DTE
      MODERATE → Buy ATM calls, 30-45 DTE, half size
      WEAK     → Paper trade only, do not risk real capital
    """

    def _v(key, default=None):
        val = row.get(key)
        return val if val is not None else default

    # ── Pull all needed metrics ───────────────────────────────────────────────
    category   = _v("Category", "Avoid")
    dist       = _v("Dist_52W_High%",     -999)
    days_52h   = _v("Days_Since_52W_High",  999)
    rsi        = _v("RSI_14",               50)
    adx        = _v("ADX_14",               0)
    rvol       = _v("RVOL",                 1)
    rs         = _v("RS",                   0) or 0
    bb         = _v("BB_PctB",             0.5)
    atr_pct    = _v("ATR_Pct",             0)
    atr_shrink = _v("ATR Shrinking",       False)
    short_int  = _v("Short_Interest%",     0)
    eb         = _v("EarningsBeat",        False)
    above200   = _v("Above_200MA",         False)
    above_vwap = _v("Above_VWAP",         False)
    p50pct     = _v("Price_vs_50MA%",     -999)
    vol_dry    = _v("VolumeDryingUp",      False)
    price      = _v("Current Price",       0)
    ma50       = _v("50MA")
    ma200      = _v("200MA")
    high52     = _v("52W High")
    low52      = _v("52W Low")
    earn_date  = _v("EarningsDate",        "N/A")
    pvol       = _v("Pullback_Vol_Ratio",  1)

    score   = 0
    reasons = []
    disq    = False
    disq_reasons = []

    # ── Hard disqualifiers ────────────────────────────────────────────────────

    # Only evaluate for Turnaround (primary) or Momentum-Pullback (secondary)
    if category not in ("Turnaround", "Momentum-Pullback", "VCP Setup"):
        row.update({
            "Call_Candidate":   False,
            "Call_Score":       0,
            "Call_Strength":    "N/A",
            "Call_Reason":      f"Not applicable for {category} — calls only for Turnaround/MP/VCP",
            "Call_Strike_Hint": "N/A",
        })
        return row

    if rs < CFG_CALL_RS_DISQ:
        disq = True
        disq_reasons.append(f"RS={rs:.1f} < {CFG_CALL_RS_DISQ} — structural downtrend, call will expire worthless")

    if rsi < 25:
        disq = True
        disq_reasons.append(f"RSI={rsi:.1f} < 25 — still in freefall, no floor established")

    if atr_pct > CFG_CALL_ATR_MAX:
        disq = True
        disq_reasons.append(f"ATR%={atr_pct:.1f} > {CFG_CALL_ATR_MAX} — IV too expensive, calls overpriced")

    if days_52h > CFG_CALL_DAYS_DISQ:
        disq = True
        disq_reasons.append(f"{days_52h}d since 52W high — dead base, no catalyst momentum")

    if not eb and rs < 0:
        disq = True
        disq_reasons.append("no_earnings_beat + RS<0 — no catalyst and underperforming QQQ")

    if category == "Avoid":
        disq = True
        disq_reasons.append("failed entry gate — skip")

    if disq:
        row.update({
            "Call_Candidate":   False,
            "Call_Score":       0,
            "Call_Strength":    "DISQUALIFIED",
            "Call_Reason":      "DISQUALIFIED: " + " | ".join(disq_reasons),
            "Call_Strike_Hint": "No trade",
        })
        return row

    # ── CATALYST signals ──────────────────────────────────────────────────────
    if eb:
        score += 4
        reasons.append("earnings_beat✓(+4)")

    if short_int > 15:
        score += 3
        reasons.append(f"short_int={short_int:.1f}%(high_squeeze_fuel+3)")
    elif short_int > CFG_CALL_SHORT_INT:
        score += 2
        reasons.append(f"short_int={short_int:.1f}%(squeeze_fuel+2)")

    if rs > 20:
        score += 3
        reasons.append(f"RS={rs:.1f}(strong_rotation+3)")
    elif rs > 0:
        score += 2
        reasons.append(f"RS={rs:.1f}(positive_vs_QQQ+2)")
    elif rs < -30:
        score -= 2
        reasons.append(f"RS={rs:.1f}(heavy_QQQ_lag-2⚠)")

    # ── BASE / COMPRESSION signals ────────────────────────────────────────────
    if atr_shrink:
        score += 3
        reasons.append("ATR_shrinking(base_forming+3)")

    if bb < 0.2:
        score += 2
        reasons.append(f"BB_PctB={bb:.3f}(ultra_coiled+2)")
    elif bb < CFG_CALL_BB_COILED:
        score += 1
        reasons.append(f"BB_PctB={bb:.3f}(coiling+1)")

    if vol_dry:
        score += 2
        reasons.append("volume_drying_up(accumulation+2)")

    if pvol is not None and pvol < 0.7:
        score += 1
        reasons.append(f"pullback_vol={pvol:.2f}(light_selling+1)")

    # ── PRICE ACTION signals ──────────────────────────────────────────────────
    if above200:
        score += 2
        reasons.append("above_200MA(structure_intact+2)")

    if above_vwap:
        score += 2
        reasons.append("above_VWAP(intraday_buyers+2)")

    if p50pct is not None and p50pct > -12:
        score += 2
        reasons.append(f"near_50MA({p50pct:+.1f}%,reclaim_imminent+2)")
    elif p50pct is not None and p50pct > -20:
        score += 1
        reasons.append(f"approaching_50MA({p50pct:+.1f}%+1)")

    if CFG_CALL_RSI_MIN <= rsi <= 35:
        score += 2
        reasons.append(f"RSI={rsi:.1f}(oversold_bounce_zone+2)")
    elif 35 < rsi <= CFG_CALL_RSI_MAX:
        score += 1
        reasons.append(f"RSI={rsi:.1f}(stabilising+1)")

    if adx > 30:
        score += 2
        reasons.append(f"ADX={adx:.1f}(clear_trend+2)")
    elif adx > CFG_CALL_ADX_MIN:
        score += 1
        reasons.append(f"ADX={adx:.1f}(trend_forming+1)")

    # ── Determine strength ────────────────────────────────────────────────────
    if score >= CFG_CALL_STRONG_THRESHOLD:
        strength = "STRONG"
    elif score >= CFG_CALL_MODERATE_THRESHOLD:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    is_call = score >= CFG_CALL_MODERATE_THRESHOLD

    # ── Strike and expiry hint ────────────────────────────────────────────────
    if price and price > 0:
        otm_5pct  = round(price * 1.05, 2)
        otm_10pct = round(price * 1.10, 2)
        atm       = round(price, 2)

        if strength == "STRONG":
            strike_hint = (
                f"Buy OTM calls — strike ${otm_5pct:,.2f} (+5%) or "
                f"${otm_10pct:,.2f} (+10% for more leverage). "
                f"Expiry: 45-60 DTE"
                + (f" (after earnings {earn_date})" if earn_date != "N/A" else "") + ". "
                f"T1 target: 50MA ${ma50:,.2f}" if ma50 else ""
                + f", T2: 200MA ${ma200:,.2f}" if ma200 else ""
                + f". Full call size."
            )
        elif strength == "MODERATE":
            strike_hint = (
                f"Buy ATM calls — strike ~${atm:,.2f}. "
                f"Expiry: 30-45 DTE. "
                f"T1 target: 50MA ${ma50:,.2f}" if ma50 else ""
                + f". HALF call size — wait for 50MA reclaim to confirm."
            )
        else:
            strike_hint = (
                f"Paper trade only — score too low for real capital. "
                f"Watch for 50MA reclaim ${ma50:,.2f} before entry."
                if ma50 else "Paper trade only."
            )
    else:
        strike_hint = "Price unavailable"

    call_label = "CALL_CANDIDATE" if is_call else f"not_call(score={score}<{CFG_CALL_MODERATE_THRESHOLD})"

    row.update({
        "Call_Candidate":   is_call,
        "Call_Score":       score,
        "Call_Strength":    strength,
        "Call_Reason":      f"{call_label}: " + " | ".join(reasons),
        "Call_Strike_Hint": strike_hint,
    })
    return row


# ── Column names to add to write_metrics_csv.py COLUMN_ORDER ─────────────────
CALL_COLUMNS = [
    "Call_Candidate", "Call_Score", "Call_Strength",
    "Call_Reason", "Call_Strike_Hint",
]


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# python call_candidate.py
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Standalone entry point — run this module directly."""
    import sys

    test_data = [
        # ticker, cat, dist, days, rsi, adx, rs, bb, atr_pct, atr_shrink,
        # short_int, eb, above200, above_vwap, p50pct, vol_dry, pvol, price, ma50, ma200
        ("SMCI", "Turnaround",        -50.9, 331, 45.5, 22.9,  16.86, 0.292, 12.76, True,  19.4, True,  False, False, -8.6,  False, 0.65, 30.63,  33.52,  35.35),
        ("INOD", "Turnaround",        -40.9,  23, 37.4, 31.1,  69.80, 0.007, 15.09, False,  8.1, True,  True,  True,  -7.8,  False, 0.76, 73.90,  80.15,  63.22),
        ("RKLB", "Turnaround",        -44.0,  31, 34.2, 29.6,   5.09, 0.117, 12.35, True,   5.9, True,  True,  True,  -20.0, False, 0.92, 84.54, 105.73,  74.87),
        ("PYPL", "Turnaround",        -44.3, 334, 45.0, 20.5, -30.12, 0.872,  3.03, False,  6.9, True,  False, True,  -2.4,  False, 0.72, 44.29,  45.36,  54.21),
        ("USAR", "Turnaround",        -53.5, 257, 41.6, 15.8,   6.64, 0.233, 11.35, True,  13.6, True,  True,  True,  -15.3, False, 0.83, 20.47,  24.17,  20.07),
        ("WDAY", "Turnaround",        -50.3, 271, 36.3, 17.0, -35.72, 0.361,  6.26, True,  16.5, True,  False, True,  -2.5,  False, 1.09,124.21, 127.45, 176.72),
        ("NOW",  "Turnaround",        -53.5, 359, 37.4, 17.9, -37.29, 0.349,  7.57, True,   5.8, True,  False, True,  -1.3,  False, 0.69, 98.34,  99.60, 134.48),
        ("ORCL", "Turnaround",        -57.0, 290, 30.9, 25.5, -18.17, 0.093,  9.33, False,  2.2, True,  False, False, -21.5, False, 1.06,148.53, 189.15, 203.16),
        ("PLTR", "Turnaround",        -45.6, 236, 27.4, 22.5, -52.36, 0.176,  6.29, False,  3.6, True,  False, True,  -17.2, False, 1.20,112.93, 136.34, 158.82),
        ("NFLX", "Turnaround",        -45.0, 362, 19.1, 38.9, -51.45, 0.226,  3.13, False,  2.4, False, False, False, -14.1, False, 1.25, 73.81,  85.97,  97.02),
        # Non-turnaround should show N/A
        ("MU",   "Momentum",          -9.8,    2, 64.5, 24.3, 212.38, 0.734,  8.76, False,  3.7, True,  True,  False,  41.2, False, 1.21,1132.33, 802.14, 425.84),
        # MP secondary use case
        ("QCOM", "Momentum-Pullback", -27.1,  29, 47.7, 19.0,  33.86, 0.079,  9.46, True,   4.7, None,  True,  False,  -4.8, False, 1.13, 189.39, 199.03, 168.00),
    ]

    print("=" * 95)
    print("TURNAROUND CALL OPTION CANDIDATE TEST")
    print("=" * 95)
    print(f"\n{'Ticker':<7} {'Cat':<18} {'Cand':<6} {'Str':<12} {'Score':>5}  Reason (truncated)")
    print("-" * 95)

    for row_vals in test_data:
        (ticker, cat, dist, days, rsi, adx, rs, bb, atr_pct, atr_shrink,
         short_int, eb, above200, above_vwap, p50pct, vol_dry, pvol, price, ma50, ma200) = row_vals

        row = {
            "Ticker":              ticker,
            "Category":            cat,
            "Dist_52W_High%":      dist,
            "Days_Since_52W_High": days,
            "RSI_14":              rsi,
            "ADX_14":              adx,
            "RS":                  rs,
            "BB_PctB":             bb,
            "ATR_Pct":             atr_pct,
            "ATR Shrinking":       atr_shrink,
            "Short_Interest%":     short_int,
            "EarningsBeat":        eb,
            "Above_200MA":         above200,
            "Above_VWAP":          above_vwap,
            "Price_vs_50MA%":      p50pct,
            "VolumeDryingUp":      vol_dry,
            "Pullback_Vol_Ratio":  pvol,
            "Current Price":       price,
            "50MA":                ma50,
            "200MA":               ma200,
            "EarningsDate":        "8/6/2026",
            "RVOL":                1.2,
        }
        compute_call_candidate(row)

        flag = "✓ YES" if row["Call_Candidate"] else "✗ NO "
        print(f"{ticker:<7} {cat:<18} {flag}  {row['Call_Strength']:<12} {row['Call_Score']:>4}   "
              f"{row['Call_Reason'][:55]}")

    print()
    print("─" * 95)
    print("STRONG (≥7): Enter now with full call size")
    print("MODERATE (4-6): Enter with half size, wait for 50MA reclaim to add")
    print("WEAK (<4): Paper trade only")
    print("DISQUALIFIED: Skip — one or more hard blocks triggered")


if __name__ == "__main__":
    main()
