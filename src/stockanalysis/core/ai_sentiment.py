"""
ai_sentiment.py
================
Scoring for the AI Sentiment dashboard — turns the raw fetches from
scanners.ai_pulse (tier snapshots, yield/DXY/SOXX, NVDA VWAP) plus
scanners.market_movers.market_pulse() (VIX, SPY/QQQ, sector ETFs, econ
events) into the user's five scanner ideas: Risk Score, Rotation Detection,
AI Health Index, Macro Event Filter, and a composite AI Market Sentiment
Score. Pure functions, no network — mirrors core/market_regime.py's
compute_*(pulse=...) -> {score, drivers, label} pattern so it's unit
testable with synthetic fixtures (see tests/test_ai_sentiment.py).

Risk Score
----------
Implements the indicator table as given: yield/DXY/VIX/SOXX/Nasdaq-vs-SPY/
NVDA-vs-VWAP/breadth, each worth the listed points. NOTE: that table sums to
a max of +4 (breadth +2, yield-falls +2) and a min of -10 (all six negative
rows firing at once) — not the +8/-8 used in the illustrative example, which
appears aspirational rather than derived from the table. Bands below are
scaled to the table's real achievable range rather than the example's.

AI Market Sentiment Score v2 (0-100)
-------------------------------------
Built on the 15-name supply-chain basket (AI_BASKET below) — every layer of
the AI value chain, weighted, not just the largest names — because weakness
at any critical layer (optics, power, memory) often signals broader risk
before it shows up in the index heavyweights:

    composite = 0.40 * momentum   (weighted 1d/5d/20d basket returns)
              + 0.20 * breadth    (6 conditions: 20/50/200 EMA, RSI>60,
                                   20-day highs, volume >1.5x avg)
              + 0.15 * rs_vs_qqq  (weighted 20d basket return - QQQ 20d)
              + 0.10 * volume     (up-on-volume vs down-on-volume split)
              + 0.10 * macro      (VIX 10 / 10Y 8 / DXY 5 / SOXX 10 /
                                   QQQ 10 / SPY 5, normalized)
              + 0.05 * news_earnings (earnings-window uncertainty drag)

A missing component contributes its weight at neutral 50 and says so in its
drivers — no silent zeros. The all-tiers-red-while-SPY-flat penalty (the
"AI-specific rotation" tell) still applies, -15. Bands unchanged: >70
Aggressive, 40-70 Selective, <40 avoid new AI longs.

NOTE on weights: the basket spec's stock weights sum to 108 and the macro
weights to 48; both are normalized by their own total, preserving the
intended ratios. The published sector split (Semis 53 / Networking-Optics
15 / Power 19 / Software-Cloud 21) is the pre-normalization sum per group.

Breadth's "% above VWAP" condition from the spec is intentionally omitted:
it would cost one intraday fetch per basket name per refresh, and the six
daily-bar conditions carry the signal (VWAP posture is on the page via the
NVDA flag).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

MACRO_KEYWORDS = (
    "cpi", "ppi", "nonfarm", "payroll", "fomc", "fed ", "fed:", "treasury",
    "rate decision", "jobs report", "pce", "gdp",
)

DEFENSIVE_SECTORS = ["Utilities", "Staples", "Health"]
GROWTH_SECTORS    = ["Semis", "Software", "Industr", "Financials"]

# ── The supply-chain basket ──────────────────────────────────────────────────
# 15 names covering every layer of the AI value chain; weights are the spec's
# (sum 108, normalized wherever they're used).
AI_BASKET: list[dict] = [
    {"ticker": "NVDA", "category": "AI Leader",          "sector": "Semiconductors",    "weight": 15, "why": "AI demand proxy"},
    {"ticker": "AMD",  "category": "AI Chips",           "sector": "Semiconductors",    "weight": 8,  "why": "GPU competition"},
    {"ticker": "AVGO", "category": "AI ASIC",            "sector": "Semiconductors",    "weight": 8,  "why": "AI networking & custom chips"},
    {"ticker": "ASML", "category": "Chip Equipment",     "sector": "Semiconductors",    "weight": 7,  "why": "Long-term semiconductor capex"},
    {"ticker": "TSM",  "category": "AI Manufacturing",   "sector": "Semiconductors",    "weight": 8,  "why": "Foundry demand"},
    {"ticker": "MU",   "category": "AI Memory",          "sector": "Semiconductors",    "weight": 7,  "why": "HBM memory demand"},
    {"ticker": "MRVL", "category": "AI Networking",      "sector": "Networking/Optics", "weight": 6,  "why": "AI interconnects"},
    {"ticker": "CRDO", "category": "Optical Networking", "sector": "Networking/Optics", "weight": 5,  "why": "AI cluster networking"},
    {"ticker": "LITE", "category": "Optical Components", "sector": "Networking/Optics", "weight": 4,  "why": "AI data center optics"},
    {"ticker": "VRT",  "category": "AI Infrastructure",  "sector": "Power",             "weight": 8,  "why": "Data center power & cooling"},
    {"ticker": "ETN",  "category": "Power Grid",         "sector": "Power",             "weight": 6,  "why": "AI power infrastructure"},
    {"ticker": "CEG",  "category": "Utilities",          "sector": "Power",             "weight": 5,  "why": "AI power consumption"},
    {"ticker": "MSFT", "category": "Cloud AI",           "sector": "Software/Cloud",    "weight": 8,  "why": "Enterprise AI adoption"},
    {"ticker": "PLTR", "category": "AI Software",        "sector": "Software/Cloud",    "weight": 7,  "why": "Commercial AI demand"},
    {"ticker": "META", "category": "Cloud/Internet AI",  "sector": "Software/Cloud",    "weight": 6,  "why": "AI monetization"},
]
BASKET_WEIGHT = {r["ticker"]: r["weight"] for r in AI_BASKET}
BASKET_META   = {r["ticker"]: r for r in AI_BASKET}
AI_BASKET_TICKERS = [r["ticker"] for r in AI_BASKET]

# Leading indicators — often move before the core names; tracked for the
# early-warning panel, not part of the composite score.
AI_LEADING_INDICATORS: dict[str, list[str]] = {
    "AI Hardware":       ["SMCI", "DELL"],
    "Networking":        ["CIEN", "AAOI", "COHR"],
    "Memory":            ["WDC", "STX"],
    "Power":             ["GEV", "VST"],
    "AI Infrastructure": ["ORCL", "AMZN"],
}
AI_LEADING_TICKERS = [t for group in AI_LEADING_INDICATORS.values() for t in group]

# Macro indicator weights from the spec (sum 48; normalized in use)
MACRO_WEIGHTS = {"vix": 10, "tnx": 8, "dxy": 5, "soxx": 10, "qqq": 10, "spy": 5}


def _clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────────────
# 1. RISK SCORE
# ─────────────────────────────────────────────────────────────────────────────

def _risk_label(score: int) -> str:
    if score >= 3:  return "Aggressive buying"
    if score >= 1:  return "Selective buying"
    if score == 0:  return "Neutral"
    if score >= -4: return "Reduce position size"
    return "Defensive"


def compute_risk_score(pulse_extra: dict, pulse: dict | None = None) -> dict:
    """Never raises; missing inputs simply don't contribute to the score."""
    pulse = pulse or {}
    score, drivers = 0, []

    yield_info = pulse_extra.get("yield") or {}
    bps = yield_info.get("change_bps")
    if bps is not None:
        if bps > 5:
            score -= 2; drivers.append(f"10Y yield +{bps}bp -2")
        elif bps < -5:
            score += 2; drivers.append(f"10Y yield {bps}bp (falling) +2")
        else:
            drivers.append(f"10Y yield {bps:+d}bp +0")

    dxy = pulse_extra.get("dxy") or {}
    dxy_chg = dxy.get("change_pct")
    if dxy_chg is not None:
        if dxy_chg > 0.5:
            score -= 1; drivers.append(f"DXY {dxy_chg:+.2f}% -1")
        else:
            drivers.append(f"DXY {dxy_chg:+.2f}% +0")

    vix = pulse.get("vix") or {}
    vix_chg_pct = vix.get("change_pct")
    if vix_chg_pct is not None:
        if vix_chg_pct > 5:
            score -= 2; drivers.append(f"VIX {vix_chg_pct:+.1f}% -2")
        else:
            drivers.append(f"VIX {vix_chg_pct:+.1f}% +0")

    soxx = pulse_extra.get("soxx") or {}
    above_soxx_20ma = soxx.get("above_20ma")
    if above_soxx_20ma is not None:
        if not above_soxx_20ma:
            score -= 2; drivers.append("SOXX below 20DMA -2")
        else:
            drivers.append("SOXX above 20DMA +0")

    spy_chg = ((pulse.get("spy") or {}).get("day_chg_pct"))
    qqq_chg = ((pulse.get("qqq") or {}).get("day_chg_pct"))
    if spy_chg is not None and qqq_chg is not None:
        if qqq_chg < spy_chg:
            score -= 1; drivers.append(f"Nasdaq underperforms SPY ({qqq_chg:+.2f}% vs {spy_chg:+.2f}%) -1")
        else:
            drivers.append(f"Nasdaq vs SPY {qqq_chg:+.2f}% vs {spy_chg:+.2f}% +0")

    nvda_vwap = pulse_extra.get("nvda_above_vwap")
    if nvda_vwap is not None:
        if not nvda_vwap:
            score -= 2; drivers.append("NVDA below VWAP -2")
        else:
            drivers.append("NVDA above VWAP +0")

    basket = pulse_extra.get("basket") or []
    scored = [b for b in basket if b.get("day_chg_pct") is not None]
    if scored:
        pct_green = sum(1 for b in scored if b["day_chg_pct"] > 0) / len(scored) * 100
        if pct_green > 60:
            score += 2; drivers.append(f"AI basket breadth {pct_green:.0f}% green +2")
        else:
            drivers.append(f"AI basket breadth {pct_green:.0f}% green +0")

    return {"score": score, "label": _risk_label(score), "drivers": drivers}


# ─────────────────────────────────────────────────────────────────────────────
# 2. ROTATION DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def compute_rotation(sectors: list[dict]) -> dict:
    """Classifies sector-ETF % changes (from market_pulse()["sectors"]) into
    Risk-On / Risk-Off / Mixed using the user's two patterns: defensive
    sectors up + semis down = risk-off; semis/software/industrials/
    financials all up = risk-on."""
    by_label = {s["label"]: s for s in (sectors or []) if s.get("chg_pct") is not None}

    def_up = sum(1 for l in DEFENSIVE_SECTORS if l in by_label and by_label[l]["chg_pct"] > 0)
    growth_up = sum(1 for l in GROWTH_SECTORS if l in by_label and by_label[l]["chg_pct"] > 0)
    semis = by_label.get("Semis")
    semis_down = semis is not None and semis["chg_pct"] < 0

    drivers = [f"{l} {by_label[l]['chg_pct']:+.1f}%" for l in DEFENSIVE_SECTORS + GROWTH_SECTORS
               if l in by_label]

    if not by_label:
        state = "Unknown"
    elif def_up >= 2 and semis_down:
        state = "Risk-Off"
    elif growth_up >= 3:
        state = "Risk-On"
    else:
        state = "Mixed"

    return {"state": state, "defensive_up": def_up, "growth_up": growth_up,
            "drivers": drivers}


# ─────────────────────────────────────────────────────────────────────────────
# 3. AI HEALTH INDEX
# ─────────────────────────────────────────────────────────────────────────────

def _health_label(index: float) -> str:
    if index > 80: return "Strong AI trend"
    if index > 60: return "Healthy"
    if index > 40: return "Mixed"
    if index > 20: return "Weak"
    return "Broad AI correction"


def compute_ai_health(basket_snapshots: list[dict]) -> dict:
    """% above 20 EMA, avg RSI, avg daily return, advance/decline ratio for
    the equal-weight AI basket, combined into a 0-100 index (average of four
    0-100-normalized sub-scores)."""
    snaps = [s for s in (basket_snapshots or []) if s]
    if not snaps:
        return {"index": None, "label": "No data", "pct_above_20ema": None,
                "avg_rsi": None, "avg_daily_return_pct": None,
                "advancers": 0, "decliners": 0, "adv_decl_ratio": None}

    with_ema = [s for s in snaps if s.get("above_20ema") is not None]
    pct_above_20ema = (sum(1 for s in with_ema if s["above_20ema"]) / len(with_ema) * 100
                        if with_ema else None)

    rsis = [s["rsi_14"] for s in snaps if s.get("rsi_14") is not None]
    avg_rsi = sum(rsis) / len(rsis) if rsis else None

    rets = [s["day_chg_pct"] for s in snaps if s.get("day_chg_pct") is not None]
    avg_ret = sum(rets) / len(rets) if rets else None
    advancers = sum(1 for r in rets if r > 0)
    decliners = sum(1 for r in rets if r < 0)
    adv_decl_ratio = round(advancers / decliners, 2) if decliners else (float(advancers) or None)
    pct_advancers = advancers / len(rets) * 100 if rets else None

    sub_scores = []
    if pct_above_20ema is not None: sub_scores.append(pct_above_20ema)
    if avg_rsi is not None:         sub_scores.append(avg_rsi)
    if pct_advancers is not None:   sub_scores.append(pct_advancers)
    if avg_ret is not None:         sub_scores.append(_clamp(50 + avg_ret * 10))

    index = round(sum(sub_scores) / len(sub_scores), 1) if sub_scores else None

    return {
        "index": index, "label": _health_label(index) if index is not None else "No data",
        "pct_above_20ema": round(pct_above_20ema, 1) if pct_above_20ema is not None else None,
        "avg_rsi": round(avg_rsi, 1) if avg_rsi is not None else None,
        "avg_daily_return_pct": round(avg_ret, 2) if avg_ret is not None else None,
        "advancers": advancers, "decliners": decliners,
        "adv_decl_ratio": adv_decl_ratio,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. MACRO EVENT FILTER
# ─────────────────────────────────────────────────────────────────────────────

def compute_macro_caution(econ_events: list[dict], now: datetime | None = None) -> dict:
    """Flags CPI/PPI/Jobs/FOMC/Treasury-auction-style events within the next
    48h (these move yields first, which ripples into AI names)."""
    now = now or datetime.now(ET)
    upcoming = []
    for ev in econ_events or []:
        when = ev.get("when")
        if when is None:
            continue
        title = (ev.get("title") or "").lower()
        hrs = (when - now).total_seconds() / 3600
        if 0 <= hrs <= 48 and any(k in title for k in MACRO_KEYWORDS):
            upcoming.append(ev)

    note = (f"{len(upcoming)} high-impact macro release(s) within 48h — "
            f"expect yield/vol swings, be more cautious on new entries"
            if upcoming else "No major macro releases in the next 48h")
    return {"caution": bool(upcoming), "events": upcoming, "note": note}


# ─────────────────────────────────────────────────────────────────────────────
# TIER STATUS (Leadership / Networking / Power)
# ─────────────────────────────────────────────────────────────────────────────

def compute_tier_status(snapshots: list[dict]) -> dict:
    scored = [s for s in (snapshots or []) if s.get("day_chg_pct") is not None]
    green = sum(1 for s in scored if s["day_chg_pct"] > 0)
    red = sum(1 for s in scored if s["day_chg_pct"] < 0)
    total = len(scored)
    return {"green": green, "red": red, "total": total,
            "all_red": total > 0 and red == total}


# ─────────────────────────────────────────────────────────────────────────────
# 5. COMPOSITE AI MARKET SENTIMENT SCORE
# ─────────────────────────────────────────────────────────────────────────────

ROTATION_COMPONENT = {"Risk-On": 100, "Mixed": 50, "Risk-Off": 0, "Unknown": 50}


def _sentiment_label(score: int) -> str:
    if score > 70: return "Aggressive — full risk budget on AI longs"
    if score >= 40: return "Selective — A-grade setups only"
    return "Defensive — avoid new AI longs (oversold-rebound only)"


def compute_ai_sentiment(risk: dict, rotation: dict, ai_health: dict,
                         tier_statuses: list[dict], market_flat: bool) -> dict:
    risk_component = _clamp(50 + risk["score"] * 8)
    rotation_component = ROTATION_COMPONENT.get(rotation["state"], 50)
    ai_health_index = ai_health.get("index")
    ai_health_component = ai_health_index if ai_health_index is not None else 50

    composite = (0.5 * ai_health_component
                + 0.3 * risk_component
                + 0.2 * rotation_component)

    all_tiers_red = bool(tier_statuses) and all(t.get("all_red") for t in tier_statuses)
    ai_specific_rotation = all_tiers_red and market_flat
    if ai_specific_rotation:
        composite -= 15

    composite = round(_clamp(composite))
    return {
        "score": composite, "label": _sentiment_label(composite),
        "ai_specific_rotation": ai_specific_rotation,
        "components": {"ai_health": ai_health_component, "risk": round(risk_component, 1),
                       "rotation": rotation_component},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6. SUPPLY-CHAIN BASKET COMPONENTS (Sentiment Score v2)
# ─────────────────────────────────────────────────────────────────────────────
# Every function takes basket snapshots (dicts from ai_pulse's batch fetch:
# ticker, day_chg_pct, chg_5d_pct, chg_20d_pct, above_20ema/50/200, rsi_14,
# high_20d, vol_ratio) and returns {"score": 0-100|None, "drivers": [...]}.
# Weights come from BASKET_WEIGHT here — snapshots stay plain market data.


def _weighted_avg(snaps: list[dict], key: str) -> float | None:
    num = den = 0.0
    for s in snaps:
        v, w = s.get(key), BASKET_WEIGHT.get(s.get("ticker"), 0)
        if v is not None and w:
            num += v * w
            den += w
    return num / den if den else None


def compute_breadth(snaps: list[dict]) -> dict:
    """% of the basket meeting each of six conditions; score = plain average
    of the available condition percentages (equal-weight — breadth is about
    participation, so the small names count the same as NVDA here)."""
    conds = (("above_20ema", "Above 20 EMA"), ("above_50ema", "Above 50 EMA"),
             ("above_200ema", "Above 200 EMA"), ("rsi_gt_60", "RSI > 60"),
             ("high_20d", "20-day highs"), ("strong_volume", "Volume > 1.5x avg"))
    values, conditions = [], {}
    for key, label in conds:
        have = [s for s in snaps if _cond_value(s, key) is not None]
        if not have:
            conditions[label] = None
            continue
        pct = sum(1 for s in have if _cond_value(s, key)) / len(have) * 100
        conditions[label] = round(pct)
        values.append(pct)
    score = round(sum(values) / len(values), 1) if values else None
    return {"score": score, "conditions": conditions, "n": len(snaps),
            "drivers": [f"{label} {pct}%" for label, pct in conditions.items()
                        if pct is not None]}


def _cond_value(s: dict, key: str):
    if key == "rsi_gt_60":
        rsi = s.get("rsi_14")
        return None if rsi is None else rsi > 60
    if key == "strong_volume":
        vr = s.get("vol_ratio")
        return None if vr is None else vr >= 1.5
    return s.get(key)


def compute_momentum(snaps: list[dict]) -> dict:
    """Weight-averaged basket returns over 1d/5d/20d, each mapped to 0-100
    around 50 (1d moves count strongest per point: x10 / x4 / x2), blended
    50/30/20 — recent momentum leads, the 20d anchor stops one green day
    from reading as a trend."""
    parts, drivers, blend = [], [], ((("day_chg_pct", 10, 0.5, "1d")),
                                     ("chg_5d_pct", 4, 0.3, "5d"),
                                     ("chg_20d_pct", 2, 0.2, "20d"))
    out = {}
    for key, mult, wt, label in blend:
        ret = _weighted_avg(snaps, key)
        out[f"wret_{label}"] = round(ret, 2) if ret is not None else None
        if ret is not None:
            parts.append((_clamp(50 + ret * mult), wt))
            drivers.append(f"{label} weighted return {ret:+.2f}%")
    if not parts:
        return {"score": None, "drivers": ["no return data"], **out}
    total_w = sum(w for _, w in parts)
    score = round(sum(v * w for v, w in parts) / total_w, 1)
    return {"score": score, "drivers": drivers, **out}


def compute_rs_vs_qqq(snaps: list[dict], qqq_chg_20d: float | None) -> dict:
    """Weighted 20d basket return minus QQQ's 20d return, 5 pts of score per
    pct-pt of spread — distinguishes 'AI leading the market' from 'AI just
    riding beta'."""
    basket_20d = _weighted_avg(snaps, "chg_20d_pct")
    if basket_20d is None or qqq_chg_20d is None:
        return {"score": None, "spread_pct": None,
                "drivers": ["missing basket or QQQ 20d return"]}
    spread = basket_20d - qqq_chg_20d
    return {"score": round(_clamp(50 + spread * 5), 1),
            "spread_pct": round(spread, 2),
            "drivers": [f"basket {basket_20d:+.2f}% vs QQQ {qqq_chg_20d:+.2f}% over 20d "
                        f"(spread {spread:+.2f}%)"]}


def compute_volume_participation(snaps: list[dict]) -> dict:
    """Who's getting the heavy volume — gainers or losers. Score is 50 plus
    the up-on-strong-volume fraction minus the down-on-strong-volume
    fraction, x100: accumulation reads high, distribution reads low."""
    have = [s for s in snaps
            if s.get("vol_ratio") is not None and s.get("day_chg_pct") is not None]
    if not have:
        return {"score": None, "drivers": ["no volume data"]}
    up_strong = sum(1 for s in have if s["day_chg_pct"] > 0 and s["vol_ratio"] >= 1.5)
    down_strong = sum(1 for s in have if s["day_chg_pct"] < 0 and s["vol_ratio"] >= 1.5)
    n = len(have)
    score = round(_clamp(50 + (up_strong - down_strong) / n * 100), 1)
    return {"score": score, "up_on_volume": up_strong, "down_on_volume": down_strong,
            "drivers": [f"{up_strong}/{n} up on >1.5x volume, {down_strong}/{n} down on it"]}


def compute_macro_component(pulse_extra: dict, pulse: dict | None = None) -> dict:
    """Macro inputs with the spec's weights (VIX 10 / 10Y 8 / DXY 5 / SOXX 10
    / QQQ 10 / SPY 5, normalized over whichever are available) — separates
    AI-specific weakness from a broad risk-off tape."""
    pulse = pulse or {}
    subs: list[tuple[str, float, int]] = []   # (driver, score, weight)

    vix_level = (pulse.get("vix") or {}).get("level")
    if vix_level is not None:
        s = 80 if vix_level < 15 else 60 if vix_level < 20 else 35 if vix_level < 27 else 15
        subs.append((f"VIX {vix_level:.1f}", s, MACRO_WEIGHTS["vix"]))
    bps = (pulse_extra.get("yield") or {}).get("change_bps")
    if bps is not None:
        s = 75 if bps <= -5 else 55 if bps <= 5 else 35 if bps <= 10 else 20
        subs.append((f"10Y {bps:+d}bp", s, MACRO_WEIGHTS["tnx"]))
    dxy_chg = (pulse_extra.get("dxy") or {}).get("change_pct")
    if dxy_chg is not None:
        s = 70 if dxy_chg <= -0.3 else 55 if dxy_chg < 0.3 else 35 if dxy_chg < 0.7 else 25
        subs.append((f"DXY {dxy_chg:+.2f}%", s, MACRO_WEIGHTS["dxy"]))
    soxx_above = (pulse_extra.get("soxx") or {}).get("above_20ema")
    if soxx_above is not None:
        subs.append((f"SOXX {'above' if soxx_above else 'below'} 20EMA",
                     80 if soxx_above else 25, MACRO_WEIGHTS["soxx"]))
    for key in ("qqq", "spy"):
        chg = (pulse.get(key) or {}).get("day_chg_pct")
        if chg is not None:
            subs.append((f"{key.upper()} {chg:+.2f}%", _clamp(50 + chg * 15),
                         MACRO_WEIGHTS[key]))

    if not subs:
        return {"score": None, "drivers": ["no macro data"]}
    total_w = sum(w for _, _, w in subs)
    score = round(sum(s * w for _, s, w in subs) / total_w, 1)
    return {"score": score, "drivers": [f"{d} ({s:.0f})" for d, s, _ in subs]}


def compute_news_earnings(earnings_days: dict[str, int | None] | None) -> dict:
    """Earnings-window uncertainty drag: starts at 75 (no imminent reports =
    mildly supportive), -15 per basket name reporting within 5 days. This is
    honestly an uncertainty measure, not headline NLP — the pipeline has no
    free real-time news-sentiment source, and pretending otherwise would be
    a fabricated input."""
    if not earnings_days:
        return {"score": None, "imminent": [], "drivers": ["no earnings-window data"]}
    imminent = sorted(t for t, d in earnings_days.items()
                      if d is not None and 0 <= d <= 5)
    score = _clamp(75 - 15 * len(imminent))
    drivers = ([f"{t} reports within 5d" for t in imminent]
               or ["no basket earnings within 5 days"])
    return {"score": round(score, 1), "imminent": imminent, "drivers": drivers}


def compute_leadership(snaps: list[dict]) -> list[dict]:
    """Weighted day-change per supply-chain layer, strongest first — which
    part of the AI ecosystem is leading (rotation context)."""
    groups: dict[str, list[dict]] = {}
    for s in snaps:
        meta = BASKET_META.get(s.get("ticker"))
        if meta and s.get("day_chg_pct") is not None:
            groups.setdefault(meta["sector"], []).append(s)
    out = []
    for sector, members in groups.items():
        chg = _weighted_avg(members, "day_chg_pct")
        chg5 = _weighted_avg(members, "chg_5d_pct")
        out.append({"sector": sector, "chg_pct": round(chg, 2) if chg is not None else None,
                    "chg_5d_pct": round(chg5, 2) if chg5 is not None else None,
                    "tickers": [m["ticker"] for m in members]})
    out.sort(key=lambda g: g["chg_pct"] if g["chg_pct"] is not None else -999, reverse=True)
    return out


def compute_leading_groups(leading_snaps: list[dict]) -> list[dict]:
    """Equal-weight day/5d change per leading-indicator group, plus an
    early-warning flag when a group is down >2% over 5 days — these names
    often roll over before the core basket does."""
    by_ticker = {s["ticker"]: s for s in leading_snaps if s.get("ticker")}
    out = []
    for group, tickers in AI_LEADING_INDICATORS.items():
        members = [by_ticker[t] for t in tickers if t in by_ticker]
        chgs = [m["day_chg_pct"] for m in members if m.get("day_chg_pct") is not None]
        chg5s = [m["chg_5d_pct"] for m in members if m.get("chg_5d_pct") is not None]
        chg = sum(chgs) / len(chgs) if chgs else None
        chg5 = sum(chg5s) / len(chg5s) if chg5s else None
        out.append({"group": group, "chg_pct": round(chg, 2) if chg is not None else None,
                    "chg_5d_pct": round(chg5, 2) if chg5 is not None else None,
                    "warning": chg5 is not None and chg5 < -2.0,
                    "members": members})
    return out


SENTIMENT_V2_WEIGHTS = {"momentum": 0.40, "breadth": 0.20, "rs_vs_qqq": 0.15,
                        "volume": 0.10, "macro": 0.10, "news_earnings": 0.05}


def compute_ai_sentiment_v2(momentum: dict, breadth: dict, rs: dict, volume: dict,
                            macro: dict, news: dict,
                            tier_statuses: list[dict] | None = None,
                            market_flat: bool = False) -> dict:
    """The composite. A component with score=None contributes neutral 50 at
    its full weight (and is listed under missing) — the score never silently
    collapses to whatever data happened to load."""
    inputs = {"momentum": momentum, "breadth": breadth, "rs_vs_qqq": rs,
              "volume": volume, "macro": macro, "news_earnings": news}
    components, missing, composite = {}, [], 0.0
    for name, weight in SENTIMENT_V2_WEIGHTS.items():
        score = inputs[name].get("score")
        if score is None:
            score = 50.0
            missing.append(name)
        components[name] = round(score, 1)
        composite += weight * score

    all_tiers_red = bool(tier_statuses) and all(t.get("all_red") for t in tier_statuses)
    ai_specific_rotation = all_tiers_red and market_flat
    if ai_specific_rotation:
        composite -= 15

    composite = round(_clamp(composite))
    return {"score": composite, "label": _sentiment_label(composite),
            "components": components, "weights": dict(SENTIMENT_V2_WEIGHTS),
            "missing": missing, "ai_specific_rotation": ai_specific_rotation}


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_events(events: list[dict]) -> list[dict]:
    out = []
    for e in events or []:
        when = e.get("when")
        out.append({**e, "when": when.strftime("%a %m/%d %H:%M ET") if hasattr(when, "strftime") else when})
    return out


def build_snapshot(pulse_extra: dict, pulse: dict | None = None) -> dict:
    """Orchestrates all compute_* functions into one JSON-serializable dict —
    the AI Sentiment page reads this straight from data/output/ai_sentiment.json,
    same shape convention as reporting/dashboard.py::build_snapshot."""
    pulse = pulse or {}

    risk = compute_risk_score(pulse_extra, pulse)
    rotation = compute_rotation(pulse.get("sectors") or [])
    ai_health = compute_ai_health(pulse_extra.get("basket") or [])
    macro = compute_macro_caution(pulse.get("econ_events") or [])

    tier1_status = compute_tier_status(pulse_extra.get("tier1") or [])
    tier2_status = compute_tier_status(pulse_extra.get("tier2") or [])
    tier3_status = compute_tier_status(pulse_extra.get("tier3") or [])

    spy_chg = (pulse.get("spy") or {}).get("day_chg_pct")
    market_flat = spy_chg is not None and abs(spy_chg) < 0.3
    tier_statuses = [tier1_status, tier2_status, tier3_status]

    # v2 composite over the supply-chain basket (falls back to neutral-50
    # components when the batch fetch came back empty)
    basket_v2 = pulse_extra.get("basket_v2") or []
    leading = pulse_extra.get("leading") or []
    qqq_chg_20d = (pulse_extra.get("benchmarks") or {}).get("QQQ", {}).get("chg_20d_pct")

    momentum = compute_momentum(basket_v2)
    breadth = compute_breadth(basket_v2)
    rs = compute_rs_vs_qqq(basket_v2, qqq_chg_20d)
    volume = compute_volume_participation(basket_v2)
    macro_comp = compute_macro_component(pulse_extra, pulse)
    news = compute_news_earnings(pulse_extra.get("earnings_days"))

    sentiment = compute_ai_sentiment_v2(
        momentum, breadth, rs, volume, macro_comp, news,
        tier_statuses, market_flat)

    return {
        "generated_at": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET"),
        "sentiment": sentiment,
        "momentum": momentum,
        "breadth": breadth,
        "rs_vs_qqq": rs,
        "volume": volume,
        "macro_component": macro_comp,
        "news_earnings": news,
        "leadership": compute_leadership(basket_v2),
        "leading_groups": compute_leading_groups(leading),
        "basket_v2": [{**BASKET_META.get(s.get("ticker"), {}), **s} for s in basket_v2],
        "risk": risk,
        "rotation": rotation,
        "ai_health": ai_health,
        "macro": {**macro, "events": _fmt_events(macro["events"])},
        "tiers": {
            "tier1": {"tickers": pulse_extra.get("tier1") or [], "status": tier1_status},
            "tier2": {"tickers": pulse_extra.get("tier2") or [], "status": tier2_status},
            "tier3": {"tickers": pulse_extra.get("tier3") or [], "status": tier3_status},
        },
        "all_ai": pulse_extra.get("all_ai") or [],
        "soxx": pulse_extra.get("soxx"),
        "yield": pulse_extra.get("yield"),
        "dxy": pulse_extra.get("dxy"),
        "nvda_above_vwap": pulse_extra.get("nvda_above_vwap"),
        "relative_strength": pulse_extra.get("relative_strength") or {},
        "sectors": pulse.get("sectors") or [],
        "vix": pulse.get("vix") or {},
        "market_flat": market_flat,
    }
