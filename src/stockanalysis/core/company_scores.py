"""
company_scores.py
==================
Three separate quantitative proxies for the research page's Company
Overview, each answering a different question from fields the scan already
carries (single-snapshot yfinance `.info` data — pure functions, no
network calls here):

    compute_business_quality()  0-100 — how good are the CURRENT financials
        (margins, returns, growth, leverage)? This replaces what used to be
        called "Moat" — margins and ROE measure present-day profitability,
        not durability, so it's named for what it actually measures.

    compute_economic_moat()     checklist — does the financial fingerprint
        look like a durable competitive advantage (ELITE, not just good,
        margins/returns/growth)? Explicitly NOT a Wide/Narrow/None verdict:
        brand, network effects, switching costs, and patents are
        qualitative judgments no ratio confirms, so those aren't scored —
        this is a proxy, shown with its caveat every time. A company can
        have a real moat while failing every check here (biotech patents,
        early-stage network effects) or pass every check without one (a
        commodity cyclical at peak margins).

    compute_financial_health()  0-100 — how strong is the balance sheet
        (liquidity, leverage, self-funding)?

Market cap is excluded from all three: size measures scale, not advantage,
quality, or balance-sheet strength — a small niche leader can have a
strong moat, and a mega-cap can have a weak one. Institutional ownership
keeps only a small weight in business quality (conviction is a mild
quality signal) and none in the moat checklist (institutions also hold
speculative, moat-less businesses).
"""

from __future__ import annotations

MIN_INPUTS_QUALITY = 4   # of 8 possible
MIN_INPUTS_HEALTH = 3    # of 5 possible
MIN_INPUTS_MOAT = 2      # of 4 possible

LABELS = ((70, "Strong"), (45, "Moderate"), (0, "Weak"))


def _label(score: int) -> str:
    for floor, name in LABELS:
        if score >= floor:
            return name
    return "Weak"


def compute_business_quality(row: dict) -> dict:
    """
    row: a scan/research row (needs GrossMargin%, OperatingMargin%,
    ReturnOnEquity%, FCF_Margin% or FCF_Positive, Revenue (growth %),
    EPS_Growth%, DebtToEquity, Inst_Own% — any subset).
    Returns {"score": int|None, "label": str|None, "drivers": [str, ...],
             "inputs_used": int}.
    """
    score, drivers, used = 0, [], 0

    gm = row.get("GrossMargin%")
    if gm is not None:
        used += 1
        pts = 18 if gm >= 60 else 11 if gm >= 40 else 6 if gm >= 25 else 0
        score += pts
        drivers.append(f"Gross margin {gm:.0f}% ({pts:+d})")

    om = row.get("OperatingMargin%")
    if om is not None:
        used += 1
        pts = 18 if om >= 25 else 11 if om >= 15 else 6 if om >= 8 else 0
        score += pts
        drivers.append(f"Operating margin {om:.0f}% ({pts:+d})")

    roe = row.get("ReturnOnEquity%")
    if roe is not None:
        used += 1
        pts = 18 if roe >= 25 else 11 if roe >= 15 else 6 if roe >= 8 else 0
        score += pts
        drivers.append(f"ROE {roe:.0f}% ({pts:+d})")

    fcf_margin = row.get("FCF_Margin%")
    fcf_pos = row.get("FCF_Positive")
    if fcf_margin is not None:
        used += 1
        pts = 12 if fcf_margin >= 15 else 7 if fcf_margin >= 5 else 3 if fcf_margin > 0 else 0
        score += pts
        drivers.append(f"FCF margin {fcf_margin:.0f}% ({pts:+d})")
    elif fcf_pos is not None:
        used += 1
        pts = 6 if fcf_pos else 0
        score += pts
        drivers.append(f"FCF {'positive' if fcf_pos else 'negative'} ({pts:+d})")

    rev_growth = row.get("Revenue")
    if rev_growth is not None:
        used += 1
        pts = 10 if rev_growth >= 20 else 6 if rev_growth >= 10 else 3 if rev_growth >= 0 else 0
        score += pts
        drivers.append(f"Revenue growth {rev_growth:.0f}% ({pts:+d})")

    eps_growth = row.get("EPS_Growth%")
    if eps_growth is not None:
        used += 1
        pts = 7 if eps_growth >= 20 else 4 if eps_growth >= 10 else 2 if eps_growth >= 0 else 0
        score += pts
        drivers.append(f"EPS growth {eps_growth:.0f}% ({pts:+d})")

    de = row.get("DebtToEquity")
    if de is not None:
        used += 1
        pts = 12 if de <= 50 else 7 if de <= 100 else 3 if de <= 200 else 0
        score += pts
        drivers.append(f"Debt/Equity {de:.0f} ({pts:+d})")

    inst = row.get("Inst_Own%")
    if inst is not None:
        used += 1
        pts = 5 if inst >= 40 else 2 if inst >= 20 else 0
        score += pts
        drivers.append(f"Institutional ownership {inst:.0f}% ({pts:+d})")

    if used < MIN_INPUTS_QUALITY:
        return {"score": None, "label": None,
                "drivers": [f"only {used} of 8 inputs available"],
                "inputs_used": used}

    return {"score": score, "label": _label(score),
            "drivers": drivers, "inputs_used": used}


# (row field, check name, elite threshold, unit) — an ELITE bar, stricter
# than "good" in compute_business_quality, on the signals a durable
# advantage would leave if one exists.
_MOAT_CHECKS = (
    ("GrossMargin%", "Elite gross margin", 60, "%"),
    ("OperatingMargin%", "Elite operating margin", 25, "%"),
    ("ReturnOnEquity%", "Elite ROE", 25, "%"),
    ("Revenue", "Elite revenue growth", 20, "%"),
)


def compute_economic_moat(row: dict) -> dict:
    """
    A checklist, not a blended score: does the row clear an ELITE bar on
    each of a few financial-fingerprint signals? Pass count -> a
    Strong/Moderate/Weak-signals label. Quantitative proxy only —
    durability (brand, network effects, switching costs, patents,
    regulatory capture) requires manual judgment this data can't supply,
    so none of that is scored here.
    Returns {"passed": int|None, "total": int, "label": str|None,
             "checks": [{"name", "passed", "detail"}, ...], "inputs_used": int}.
    """
    checks, used, passed = [], 0, 0
    for field, name, threshold, unit in _MOAT_CHECKS:
        val = row.get(field)
        if val is None:
            continue
        used += 1
        ok = val >= threshold
        if ok:
            passed += 1
        checks.append({"name": name, "passed": ok,
                        "detail": f"{val:.0f}{unit} (need ≥{threshold}{unit})"})

    if used < MIN_INPUTS_MOAT:
        return {"passed": None, "total": 0, "label": None,
                "checks": [], "inputs_used": used}

    ratio = passed / used
    label = ("Strong signals" if ratio >= 0.75 else
             "Moderate signals" if ratio >= 0.5 else "Weak signals")
    return {"passed": passed, "total": used, "label": label,
            "checks": checks, "inputs_used": used}


def compute_financial_health(row: dict) -> dict:
    """
    row: needs CurrentRatio, QuickRatio, DebtToEquity, TotalCash +
    TotalDebt (as a pair), FCF_Margin% or FCF_Positive — any subset.
    Returns {"score": int|None, "label": str|None, "drivers": [str, ...],
             "inputs_used": int}.
    """
    score, drivers, used = 0, [], 0

    cr = row.get("CurrentRatio")
    if cr is not None:
        used += 1
        pts = 20 if cr >= 2.0 else 13 if cr >= 1.5 else 7 if cr >= 1.0 else 0
        score += pts
        drivers.append(f"Current ratio {cr:.1f} ({pts:+d})")

    qr = row.get("QuickRatio")
    if qr is not None:
        used += 1
        pts = 15 if qr >= 1.5 else 10 if qr >= 1.0 else 5 if qr >= 0.5 else 0
        score += pts
        drivers.append(f"Quick ratio {qr:.1f} ({pts:+d})")

    de = row.get("DebtToEquity")
    if de is not None:
        used += 1
        pts = 20 if de <= 50 else 12 if de <= 100 else 5 if de <= 200 else 0
        score += pts
        drivers.append(f"Debt/Equity {de:.0f} ({pts:+d})")

    cash, debt = row.get("TotalCash"), row.get("TotalDebt")
    if cash is not None and debt is not None:
        used += 1
        pts = (20 if cash >= debt else
               12 if debt and cash >= 0.5 * debt else
               5 if cash > 0 else 0)
        score += pts
        drivers.append(f"Cash ${cash / 1e9:.1f}B vs debt ${debt / 1e9:.1f}B ({pts:+d})")

    fcf_margin = row.get("FCF_Margin%")
    fcf_pos = row.get("FCF_Positive")
    if fcf_margin is not None:
        used += 1
        pts = 25 if fcf_margin >= 15 else 15 if fcf_margin >= 5 else 6 if fcf_margin > 0 else 0
        score += pts
        drivers.append(f"FCF margin {fcf_margin:.0f}% ({pts:+d})")
    elif fcf_pos is not None:
        used += 1
        pts = 12 if fcf_pos else 0
        score += pts
        drivers.append(f"FCF {'positive' if fcf_pos else 'negative'} ({pts:+d})")

    if used < MIN_INPUTS_HEALTH:
        return {"score": None, "label": None,
                "drivers": [f"only {used} of 5 inputs available"],
                "inputs_used": used}

    return {"score": score, "label": _label(score),
            "drivers": drivers, "inputs_used": used}
