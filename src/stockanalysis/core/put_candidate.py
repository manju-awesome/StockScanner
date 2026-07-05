"""
put_candidate.py
================
Standalone function to compute is_put_candidate and put_score
from any get_metrics() output dict.

Add to get_metrics() just before the return statement:

    from libs.put_candidate import compute_put_candidate
    compute_put_candidate(row)   # adds Put_Candidate, Put_Score, Put_Reason in-place

Or call after get_metrics() returns:

    row = get_metrics(ticker, qqq_3m)
    compute_put_candidate(row)
    print(row["Put_Candidate"], row["Put_Score"], row["Put_Reason"])
"""

from __future__ import annotations


def compute_put_candidate(row: dict) -> dict:
    """
    Evaluates whether a stock is a put-buying candidate based on exhaustion signals.

    Adds three keys to the row dict (in-place + returned):
        Put_Candidate  : bool   — True if score >= 5 and not disqualified
        Put_Score      : int    — raw score (higher = stronger put setup)
        Put_Reason     : str    — pipe-separated signal explanations

    Scoring
    -------
    +2  near 52W high set within 10 days (dist >= -8%)   — parabolic move
    +2  price > 5% above 8EMA                            — extended
    +1  price 3-5% above 8EMA                            — slightly extended
    +3  RSI > 72                                         — overbought
    +2  RSI 68-72                                        — stretched
    +3  RVOL < 0.7                                       — buyers exhausted
    +2  RVOL 0.7-0.9                                     — volume fading
    +1  Vol_vs_20D < 0.9                                 — below 20-day avg
    +2  BB_PctB > 1.0                                    — above upper band
    +1  BB_PctB 0.85-1.0                                 — approaching upper band
    -1  ADX > 35 (strong trend — harder to reverse)

    Disqualifiers (hard-block regardless of score)
    -----------------------------------------------
    ADX > 38 AND RS > 50  — trend too strong, overbought can persist for weeks
    RS > 80               — institutional accumulation, don't fade strength
    """

    def _v(key, default=None):
        val = row.get(key)
        return val if val is not None else default

    dist      = _v("Dist_52W_High%",     -999)
    days_52h  = _v("Days_Since_52W_High",  999)
    pct_ema8  = _v("Pct_vs_8EMA",           0)
    rsi       = _v("RSI_14",               50)
    rvol      = _v("RVOL",                  1)
    vol20d    = _v("Vol_vs_20D",            1)
    bb        = _v("BB_PctB",             0.5)
    adx       = _v("ADX_14",               0)
    rs        = _v("RS",                    0) or 0

    score   = 0
    reasons = []
    disq    = False
    disq_reasons = []

    # ── Hard disqualifiers ────────────────────────────────────────────────────
    if adx > 38 and rs > 50:
        disq = True
        disq_reasons.append(
            f"trend_too_strong(ADX={adx:.1f},RS={rs:.1f}) — overbought can persist"
        )
    if rs > 80:
        disq = True
        disq_reasons.append(
            f"RS={rs:.1f} — institutional accumulation, do not fade"
        )

    if disq:
        row["Put_Candidate"] = False
        row["Put_Score"]     = 0
        row["Put_Reason"]    = "DISQUALIFIED: " + " | ".join(disq_reasons)
        return row

    # ── Signal 1: Near 52W high recently (parabolic speed) ───────────────────
    if dist >= -8 and days_52h <= 10:
        score += 2
        reasons.append(
            f"near_52wh({dist:.1f}% off high, set {days_52h}d ago)"
        )

    # ── Signal 2: Extended above 8EMA ────────────────────────────────────────
    if pct_ema8 > 5:
        score += 2
        reasons.append(f"extended_above_8ema(+{pct_ema8:.1f}%)")
    elif pct_ema8 > 3:
        score += 1
        reasons.append(f"above_8ema(+{pct_ema8:.1f}%⚠)")

    # ── Signal 3: RSI overbought ──────────────────────────────────────────────
    if rsi > 72:
        score += 3
        reasons.append(f"RSI={rsi:.1f}(overbought)")
    elif rsi > 68:
        score += 2
        reasons.append(f"RSI={rsi:.1f}(stretched)")

    # ── Signal 4: Volume exhaustion (strongest signal) ────────────────────────
    if rvol < 0.7:
        score += 3
        reasons.append(f"RVOL={rvol:.2f}(buyers_exhausted)")
    elif rvol < 0.9:
        score += 2
        reasons.append(f"RVOL={rvol:.2f}(volume_fading)")
    elif vol20d < 0.9:
        score += 1
        reasons.append(f"Vol_vs_20D={vol20d:.2f}(below_avg)")

    # ── Signal 5: Bollinger Band extension ────────────────────────────────────
    if bb > 1.0:
        score += 2
        reasons.append(f"BB_PctB={bb:.3f}(above_upper_band)")
    elif bb > 0.85:
        score += 1
        reasons.append(f"BB_PctB={bb:.3f}(approaching_upper)")

    # ── Soft penalty: strong trend (ADX>35) ───────────────────────────────────
    if adx > 35:
        score -= 1
        reasons.append(f"ADX={adx:.1f}(strong_trend⚠ -1pt)")

    # ── Result ────────────────────────────────────────────────────────────────
    is_put = score >= 5

    row["Put_Candidate"] = is_put
    row["Put_Score"]     = score
    row["Put_Reason"]    = (
        ("PUT_CANDIDATE: " if is_put else "not_put(score<5): ")
        + " | ".join(reasons) if reasons else "no_signals"
    )
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Add to COLUMN_ORDER in write_metrics_csv.py — paste after "CANSLIM_Pass":
#   "Put_Candidate", "Put_Score", "Put_Reason",
# ─────────────────────────────────────────────────────────────────────────────

PUT_COLUMNS = ["Put_Candidate", "Put_Score", "Put_Reason"]


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# python put_candidate.py
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_data = [
        # ticker, dist, days, ema8_pct, rsi, rvol, vol20d, bb,    adx,  rs
        ("LLY",  -0.6,  1,  7.91, 60.3, 2.28, 2.24, 1.425, 29.9,   1.1),
        ("GE",   -2.8,  2,  2.53, 75.1, 1.28, 1.43, 0.881, 36.1,   4.0),
        ("UNH",   0.0,  1,  4.54, 66.0, 0.90, 0.93, 1.032, 36.0,  33.1),
        ("ABBV",  0.0,  1,  8.32, 71.5, 7.27, 6.08, 1.329, 22.1, -11.2),
        ("PANW", -0.7,  1,  5.79, 66.1, 0.88, 0.78, 1.018, 40.2,  72.0),
        ("FTNT", -0.9,  1,  2.78, 66.4, 1.42, 1.26, 0.896, 44.7,  64.4),
        ("AMD",  -7.4,  5, -0.75, 58.5, 1.33, 1.61, 0.581, 24.2, 136.3),
        ("MU",   -9.8,  2,  0.84, 64.5, 1.61, 1.45, 0.734, 24.3, 212.4),
        ("AMAT", -6.3,  2,  1.54, 72.5, 3.10, 2.35, 0.780, 31.9,  70.8),
        ("GLW",  -4.1,  2,  6.68, 64.3, 3.08, 2.77, 0.935, 17.3,  39.3),
        ("NVDA",-18.6, 44, -4.34, 39.6, 1.10, 1.05, 0.021, 16.1, -10.5),  # not a put
        ("PLTR",-45.6,236, -3.64, 27.4, 1.40, 1.33, 0.176, 22.5, -52.4),  # not a put
    ]

    print("=" * 80)
    print("PUT CANDIDATE DETECTION TEST")
    print("=" * 80)
    print(f"\n{'Ticker':<7} {'Cand':<6} {'Score':>5}  Reason")
    print("-" * 80)

    for ticker, dist, days, ema8_pct, rsi, rvol, vol20d, bb, adx, rs in test_data:
        row = {
            "Ticker":              ticker,
            "Dist_52W_High%":      dist,
            "Days_Since_52W_High": days,
            "Pct_vs_8EMA":         ema8_pct,
            "RSI_14":              rsi,
            "RVOL":                rvol,
            "Vol_vs_20D":          vol20d,
            "BB_PctB":             bb,
            "ADX_14":              adx,
            "RS":                  rs,
        }
        compute_put_candidate(row)
        flag = "✓ YES" if row["Put_Candidate"] else "✗ NO "
        print(f"{ticker:<7} {flag}  {row['Put_Score']:>4}   {row['Put_Reason'][:72]}")