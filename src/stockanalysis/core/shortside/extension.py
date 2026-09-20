"""
Extension measured in ATR, not percent.

The percentage is the wrong unit and it is wrong in a way that matters.
"10% above the 8 EMA" describes a stock that has come unglued from its
trend if the daily range is 2%, and a perfectly ordinary Tuesday if the
daily range is 6%. Two names can print the same percentage and be in
completely different states.

    MDB   +10.7% above the 8 EMA, ATR 4.28%  ->  2.5 ATR   extended
    a 2%-ATR name at the same +10.7%          ->  5.4 ATR   parabolic

So every extension reading here is a multiple of ATR20, and the
percentages are carried alongside only for display. The thresholds are
then comparable across the whole universe, which is the entire point —
a single "extended" rule that means the same thing for a utility and a
high-beta software name.

Reference points differ by moving average because their normal distance
differs: sitting 2 ATR above the 8 EMA is stretched, while 2 ATR above
the 200 MA is unremarkable in any uptrend.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f

# ATR multiples at which each level reads as EXTREME. Scaled from the
# distance each MA normally sits from price in a healthy trend, so the
# resulting scores are comparable across the four.
EXTREME_ATR = {"8EMA": 3.0, "21EMA": 4.5, "50MA": 6.0, "200MA": 9.0}
# Below this the level is not saying anything about exhaustion.
QUIET_ATR = {"8EMA": 0.8, "21EMA": 1.2, "50MA": 2.0, "200MA": 3.0}

# What each level contributes to the blended extension score. The short
# and medium averages carry more because they are what a mean-reversion
# trade actually reverts to; the 200 MA is context, not a target.
LEVEL_WEIGHT = {"8EMA": 30, "21EMA": 25, "50MA": 30, "200MA": 15}

BANDS = ((3.0, "🔴 Parabolic"), (2.0, "🟠 Very extended"),
         (1.2, "🟡 Extended"), (0.5, "⚪ Normal"), (-99, "🟢 At/below trend"))


def atr_multiple(price, level, atr):
    """How many ATRs `price` sits above `level`. Negative means below.

    None when any input is missing or ATR is zero — a zero ATR would
    divide to infinity and print as the most extended name in the
    universe, which is the opposite of what a flat stock is.
    """
    p, lv, a = f(price), f(level), f(atr)
    if p is None or lv is None or a is None or a <= 0:
        return None
    return round((p - lv) / a, 2)


def band(multiple) -> str:
    if multiple is None:
        return "⚪ Unknown"
    for floor, label in BANDS:
        if multiple >= floor:
            return label
    return BANDS[-1][1]


def _level_score(key, multiple):
    """0-100 for one level. Below QUIET it scores nothing; at EXTREME it
    scores 100; linear between, clamped."""
    if multiple is None:
        return None
    lo, hi = QUIET_ATR[key], EXTREME_ATR[key]
    if multiple <= lo:
        return 0.0
    return max(0.0, min(100.0, (multiple - lo) / (hi - lo) * 100.0))


def evaluate(row, price=None, atr=None) -> dict:
    """Extension across all four averages, in ATR.

    `row` is a scan row carrying `8EMA`, `21EMA`, `50MA`, `200MA`,
    `Current Price` and `ATR20`. Returns the per-level detail, the
    blended 0-100 score, and the headline multiple (versus the 8 EMA,
    which is what "overextended" colloquially means).

    A level whose price is missing is EXCLUDED from the blend rather
    than scored zero, and `coverage` reports how much of the weight was
    actually available — a stock with only the 200 MA on file should not
    read as unextended just because three inputs are absent.
    """
    p = f(price if price is not None else row.get("Current Price"))
    a = f(atr if atr is not None else row.get("ATR20"))
    atr_pct = f(row.get("ATR_Pct"))

    levels, parts = {}, {}
    for key in ("8EMA", "21EMA", "50MA", "200MA"):
        lv = f(row.get(key))
        m = atr_multiple(p, lv, a)
        sc = _level_score(key, m)
        levels[key] = {
            "price": lv,
            "atr_multiple": m,
            "pct": (round((p - lv) / lv * 100, 1)
                    if p is not None and lv else None),
            "score": None if sc is None else round(sc),
            "band": band(m),
            "extreme_at": EXTREME_ATR[key],
        }
        if sc is not None:
            parts[key] = sc

    got = sum(LEVEL_WEIGHT[k] for k in parts)
    score = (sum(v * LEVEL_WEIGHT[k] for k, v in parts.items()) / got
             if got else None)

    head = levels["8EMA"]["atr_multiple"]
    return {
        "score": None if score is None else round(score),
        "coverage": round(got / sum(LEVEL_WEIGHT.values()) * 100),
        "levels": levels,
        "atr": a,
        "atr_pct": atr_pct,
        "headline_atr": head,
        "headline_band": band(head),
        "detail": _detail(levels, a, atr_pct),
    }


def _detail(levels, atr, atr_pct) -> str:
    bits = []
    for key in ("8EMA", "50MA", "200MA"):
        lv = levels[key]
        if lv["atr_multiple"] is not None:
            bits.append(f"{lv['atr_multiple']:+.1f} ATR vs {key} "
                        f"({lv['pct']:+.1f}%)")
    if not bits:
        return "no extension reading"
    tail = (f" — ATR ${atr:,.2f} ({atr_pct:.1f}% of price)"
            if atr and atr_pct else "")
    return "; ".join(bits) + tail


def mean_reversion_targets(row, price=None, atr=None) -> list[dict]:
    """Where a reversion would go, nearest first.

    A short's target is not a guess: it is the level the stock is
    extended FROM. Each carries its distance in ATR, which is also the
    honest unit for a reward figure — "8% of downside" means something
    different on a 2%-ATR name than a 4%-ATR one.
    """
    p = f(price if price is not None else row.get("Current Price"))
    a = f(atr if atr is not None else row.get("ATR20"))
    if p is None:
        return []

    out = []
    for key, name in (("8EMA", "8 EMA"), ("21EMA", "21 EMA"),
                      ("50MA", "50 MA"), ("200MA", "200 MA")):
        lv = f(row.get(key))
        if lv is None or lv >= p:
            continue                       # only levels BELOW price
        out.append({
            "key": key, "name": name, "price": lv,
            "move_pct": round((lv - p) / p * 100, 1),
            "atr_multiple": atr_multiple(p, lv, a),
        })
    out.sort(key=lambda x: -x["price"])     # nearest first
    return out
