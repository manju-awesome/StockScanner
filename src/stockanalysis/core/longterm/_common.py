"""
Shared coercion and blending helpers for the long-term engine.

These live in one place rather than being copied per module (as buy_zone.py
and decision_engine.py each do) because within a package there is no
circular-import reason to duplicate them, and four copies of `_f` drift.
"""

from __future__ import annotations


def f(v):
    """Float or None. Booleans are rejected rather than silently becoming
    1.0/0.0 — `True` reaching a numeric threshold as 1 is a real bug class
    when scan columns mix flags and measurements."""
    if v is None or isinstance(v, bool):
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out       # NaN


def b(v):
    """Tri-state bool: True / False / None. Strings from CSV round-trips
    ("true", "0") are understood; anything else is None, not False."""
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "yes", "1"):
        return True
    if s in ("false", "no", "0"):
        return False
    return None


def s(v):
    """Non-empty string or None."""
    if v is None:
        return None
    out = str(v).strip()
    return out or None


def scale(value: float, lo: float, hi: float) -> float:
    """Map onto 0-100, clamped. `lo`/`hi` bound the range over which the
    metric actually discriminates — past `hi`, more is not better."""
    if hi == lo:
        return 0.0
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0))


def inverse(value: float, best: float, worst: float) -> float:
    """Lower-is-better metrics. At or below `best` scores 100; at or above
    `worst`, 0."""
    if worst == best:
        return 0.0
    return max(0.0, min(100.0, (worst - value) / (worst - best) * 100.0))


def band(value: float, points) -> float:
    """points: ascending [(upper_bound, score), ...]; the last entry is the
    tail and catches everything above the final bound."""
    for bound, score in points:
        if value <= bound:
            return float(score)
    return float(points[-1][1])


def blend(parts):
    """parts: [(name, weight, value_0_100_or_None, detail_str)].

    Renormalises over the components that were measurable and reports what
    share of the intended weight that was. Returns score None when nothing
    was measurable — not 0, which reads as "bad" rather than "unknown".

    Returns {"score", "coverage", "components", "missing"} where components
    carries each factor's sub-score, weight and one-line explanation, so a
    caller can always show the arithmetic behind the number.
    """
    total = sum(w for _, w, _, _ in parts) or 1.0
    got = 0.0
    acc = 0.0
    missing, used = [], []
    for name, weight, value, detail in parts:
        if value is None:
            missing.append(name)
            continue
        acc += weight * value
        got += weight
        used.append({"name": name, "weight": weight,
                     "score": round(float(value), 1), "detail": detail})
    if not got:
        return {"score": None, "coverage": 0.0, "components": [],
                "missing": missing}
    return {"score": int(round(acc / got)),
            "coverage": round(got / total, 3),
            "components": used, "missing": missing}
