"""
etf_portfolio.py
=================
Look-through exposure for a portfolio of ETFs: what you actually own once
each fund is expanded into its sectors, and which concentration limits that
breaches.

The point of the thing
----------------------
A list of a dozen thematic ETFs looks diversified because the *names* are
different. It usually isn't: SMH is 100% technology, IGV is 91%, CIBR 95%,
and MTUM — a factor fund, not a tech fund — is 47%. Hold those together and
the portfolio is one large technology bet wearing five labels. This module
computes the number instead of leaving it to intuition.

What is exact here, and what isn't
----------------------------------
Sector exposure is EXACT. `sector_weightings` from the fund provider covers
the whole portfolio and sums to 100% — unlike the top-ten holdings list,
which is a truncated sample (IWM publishes 3.2% of its holdings). So a
blended sector weight is arithmetic on complete data, not an estimate.

Single-stock look-through is a LOWER BOUND, and is labelled that way. It can
only see disclosed holdings, so an undisclosed position in the same company
would push the true figure higher, never lower. "NVDA ≥ 6.2%" is a claim the
data supports; "NVDA = 6.2%" is not.

Fund-to-fund overlap is deliberately NOT computed. It would have to come
from top-ten lists whose coverage ranges from 3% to 72% of their funds, and
an overlap derived from incomparable samples is a number that looks
authoritative while meaning nothing. Full holdings from the issuers would
fix it; see the note in etf_profile.py.

Non-equity funds (GLD, SLV) report no sector weights at all — they hold
bullion. They are bucketed as "Commodity / non-equity" rather than dropped,
because dropping them would renormalize every equity weight upward and
overstate the portfolio's stock concentration.
"""

from __future__ import annotations

from .etf_profile import display_theme

# Provider sector keys -> display names.
SECTOR_LABELS = {
    "technology": "Technology",
    "financial_services": "Financial Services",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "consumer_cyclical": "Consumer Cyclical",
    "consumer_defensive": "Consumer Defensive",
    "energy": "Energy",
    "utilities": "Utilities",
    "realestate": "Real Estate",
    "basic_materials": "Basic Materials",
    "communication_services": "Communication Services",
}
NON_EQUITY = "Commodity / non-equity"


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def normalize_allocations(allocations: dict) -> dict:
    """Drop non-positive weights and scale to 100%.

    Scaling rather than rejecting: a portfolio entered as 25/20/15/15/10/5/5
    that sums to 95 should still be analysable, and the exposures are only
    meaningful as shares of what's invested.
    """
    clean = {}
    for ticker, weight in (allocations or {}).items():
        w = _f(weight)
        if not ticker or w is None or w <= 0:
            continue
        clean[str(ticker).strip().upper()] = w
    total = sum(clean.values())
    if not total:
        return {}
    return {t: w / total * 100.0 for t, w in clean.items()}


def sector_exposure(allocations: dict, profiles: dict) -> dict:
    """Blended sector weights. Returns {"sectors": [...], "unknown": pct}."""
    alloc = normalize_allocations(allocations)
    blended: dict[str, float] = {}
    unknown = 0.0

    for ticker, weight in alloc.items():
        p = profiles.get(ticker) or {}
        sectors = p.get("sectors") or {}
        if sectors:
            total = sum(sectors.values()) or 100.0
            for key, pct in sectors.items():
                label = SECTOR_LABELS.get(key, key.replace("_", " ").title())
                blended[label] = blended.get(label, 0.0) + weight * pct / total
            continue
        # No sector weights: either a non-equity fund or a profile we never
        # fetched. asset_mix distinguishes them — guessing would be worse.
        mix = p.get("asset_mix") or {}
        if mix and (mix.get("stock") or 0) < 10:
            blended[NON_EQUITY] = blended.get(NON_EQUITY, 0.0) + weight
        else:
            unknown += weight

    rows = [{"sector": k, "pct": round(v, 2)} for k, v in blended.items()]
    rows.sort(key=lambda r: -r["pct"])
    return {"sectors": rows, "unknown": round(unknown, 2)}


def theme_exposure(allocations: dict, profiles: dict) -> list[dict]:
    """Allocation grouped by the fund's theme label.

    Separate from sector exposure on purpose: the provider only reports broad
    sectors, so SMH and DRAM both read "Technology" there. The theme is what
    distinguishes a semiconductor bet from a memory bet, and it is your own
    label — see etf_profile.set_theme.
    """
    alloc = normalize_allocations(allocations)
    by_theme: dict[str, dict] = {}
    for ticker, weight in alloc.items():
        theme = display_theme(profiles.get(ticker) or {}) or "Unclassified"
        row = by_theme.setdefault(theme, {"theme": theme, "pct": 0.0,
                                          "tickers": []})
        row["pct"] += weight
        row["tickers"].append(ticker)
    rows = list(by_theme.values())
    for r in rows:
        r["pct"] = round(r["pct"], 2)
        r["tickers"].sort()
    rows.sort(key=lambda r: -r["pct"])
    return rows


def stock_lookthrough(allocations: dict, profiles: dict,
                      limit: int = 12) -> list[dict]:
    """Single-company weight implied by the disclosed holdings.

    A FLOOR, not a measurement: only the published top holdings are visible,
    so a company held inside the undisclosed remainder of any fund adds to
    the real figure. Each row carries the funds it came from so the source of
    a concentration is obvious.
    """
    alloc = normalize_allocations(allocations)
    by_stock: dict[str, dict] = {}
    for ticker, weight in alloc.items():
        p = profiles.get(ticker) or {}
        for h in (p.get("holdings") or []):
            w = _f(h.get("weight"))
            sym = h.get("ticker")
            if not sym or w is None:
                continue
            row = by_stock.setdefault(str(sym), {
                "ticker": str(sym), "name": h.get("name") or sym,
                "pct": 0.0, "via": []})
            row["pct"] += weight * w / 100.0
            row["via"].append(ticker)
    rows = list(by_stock.values())
    for r in rows:
        r["pct"] = round(r["pct"], 3)
        r["via"].sort()
    rows.sort(key=lambda r: -r["pct"])
    return rows[:limit]


def disclosure_coverage(allocations: dict, profiles: dict) -> float:
    """Share of the portfolio whose holdings are actually published — the
    honesty bound on stock_lookthrough()."""
    alloc = normalize_allocations(allocations)
    covered = 0.0
    for ticker, weight in alloc.items():
        disclosed = _f((profiles.get(ticker) or {}).get("top10_weight")) or 0.0
        covered += weight * disclosed / 100.0
    return round(covered, 2)


# (key, label, comparison, threshold, severity). Thresholds are the ones a
# concentrated thematic book actually trips; they are guidance, not limits
# derived from any risk model.
_RULES = (
    ("sector:Technology", "Technology", "gt", 30.0, "red"),
    ("theme_group:semis", "Semiconductor / memory exposure", "gt", 20.0, "amber"),
    ("sector:Technology", "Technology", "gt", 45.0, "red_hard"),
    ("lookthrough_top", "Largest single company (floor)", "gt", 10.0, "red"),
    ("international", "International", "lt", 15.0, "amber_low"),
    ("nonequity", "Non-equity diversification", "lt", 5.0, "amber_low"),
    ("smallcap", "Small-cap", "lt", 10.0, "amber_low"),
)

# ─────────────────────────────────────────────────────────────────────────────
# ROLES — what a fund is FOR, which is not what its sector weights say
# ─────────────────────────────────────────────────────────────────────────────
# Classification drives the diversification alerts, and it can't come from
# theme text: VXUS's provider label is "Foreign Large Blend", which contains
# none of the words a naive "international" match would look for, and QUAL
# and VTI both label as "Large Blend" while playing completely different
# roles. So roles are assigned explicitly per fund and overridable, rather
# than inferred from wording that the provider chose and the user can edit.
ROLES = ("Core", "Factor", "Theme", "International", "Commodity")

ROLE_KEY = "role"          # user override, stored beside THEME_KEY

# Defaults for funds we know. Anything unlisted falls back to _infer_role.
DEFAULT_ROLES = {
    "VTI": "Core", "VOO": "Core", "SPY": "Core", "ITOT": "Core",
    "QUAL": "Factor", "MTUM": "Factor", "VTV": "Factor", "IWM": "Factor",
    "USMV": "Factor", "VUG": "Factor",
    "VXUS": "International", "VEA": "International", "VWO": "International",
    "IEFA": "International", "IEMG": "International",
    "GLD": "Commodity", "SLV": "Commodity", "IAU": "Commodity",
    "SMH": "Theme", "DRAM": "Theme", "IGV": "Theme", "CIBR": "Theme",
    "DTCR": "Theme", "GRID": "Theme", "NLR": "Theme", "ITA": "Theme",
}


def _infer_role(profile: dict) -> str:
    """Best guess for a fund with no explicit role."""
    cat = str(profile.get("category") or "").lower()
    mix = profile.get("asset_mix") or {}
    if mix and (mix.get("stock") or 0) < 10:
        return "Commodity"
    if "foreign" in cat or "emerging" in cat or "world" in cat or "global" in cat:
        return "International"
    if "large blend" in cat or "total" in cat:
        return "Core"
    if "value" in cat or "growth" in cat or "small" in cat or "mid" in cat:
        return "Factor"
    return "Theme"


def role_of(profile: dict, ticker: str = "") -> str:
    """User override → known default → inference."""
    if profile and profile.get(ROLE_KEY) in ROLES:
        return profile[ROLE_KEY]
    t = (ticker or (profile or {}).get("ticker") or "").upper()
    if t in DEFAULT_ROLES:
        return DEFAULT_ROLES[t]
    return _infer_role(profile or {})


def role_exposure(allocations: dict, profiles: dict) -> list[dict]:
    alloc = normalize_allocations(allocations)
    by_role: dict[str, dict] = {}
    for ticker, weight in alloc.items():
        role = role_of(profiles.get(ticker) or {}, ticker)
        row = by_role.setdefault(role, {"role": role, "pct": 0.0, "tickers": []})
        row["pct"] += weight
        row["tickers"].append(ticker)
    rows = list(by_role.values())
    for r in rows:
        r["pct"] = round(r["pct"], 2)
        r["tickers"].sort()
    rows.sort(key=lambda r: -r["pct"])
    return rows


def _role_pct(roles: list[dict], name: str) -> float:
    return round(sum(r["pct"] for r in roles if r["role"] == name), 2)


# The semiconductor complex still needs theme matching — no provider field
# distinguishes a semis fund from a software fund, both are "Technology".
_SEMI_WORDS = ("semiconductor", "memory", "dram", "chip", "semis")
_SMALL_WORDS = ("small",)


def _theme_match(themes: list[dict], words: tuple[str, ...]) -> float:
    return round(sum(t["pct"] for t in themes
                     if any(w in t["theme"].lower() for w in words)), 2)


def analyze(allocations: dict, profiles: dict) -> dict:
    """Full look-through report for a set of {ticker: weight%}."""
    alloc = normalize_allocations(allocations)
    if not alloc:
        return {"ok": False, "message": "No allocations given"}

    sectors = sector_exposure(alloc, profiles)
    themes = theme_exposure(alloc, profiles)
    stocks = stock_lookthrough(alloc, profiles)
    by_sector = {r["sector"]: r["pct"] for r in sectors["sectors"]}

    roles = role_exposure(alloc, profiles)
    tech = by_sector.get("Technology", 0.0)
    semis = _theme_match(themes, _SEMI_WORDS)
    intl = _role_pct(roles, "International")
    small = _theme_match(themes, _SMALL_WORDS)
    nonequity = by_sector.get(NON_EQUITY, 0.0)
    core = _role_pct(roles, "Core")
    thematic = _role_pct(roles, "Theme")
    top_stock = stocks[0] if stocks else None

    alerts = []

    def add(level, text, detail=""):
        alerts.append({"level": level, "text": text, "detail": detail})

    if tech > 45:
        add("red", f"Technology {tech:.1f}% — more than 45% of the portfolio",
            "Thematic tech funds stack: check how many of your positions are "
            "the same bet under different names.")
    elif tech > 30:
        add("red", f"Technology {tech:.1f}% — above the 30% guide")
    else:
        add("green", f"Technology {tech:.1f}%")

    if semis > 20:
        add("amber", f"Semiconductor / memory {semis:.1f}% — above the 20% guide")
    elif semis:
        add("green", f"Semiconductor / memory {semis:.1f}%")

    if top_stock and top_stock["pct"] > 10:
        add("red", f"{top_stock['ticker']} at least {top_stock['pct']:.1f}% "
                   f"of the portfolio",
            f"via {', '.join(top_stock['via'])} — floor, from disclosed "
            f"holdings only")
    elif top_stock:
        add("green", f"Largest single company {top_stock['ticker']} "
                     f"≥{top_stock['pct']:.1f}%")

    add("green" if intl >= 15 else "amber",
        f"International {intl:.1f}%" +
        ("" if intl >= 15 else " — below the 15% guide"))
    add("green" if small >= 10 else "amber",
        f"Small-cap {small:.1f}%" +
        ("" if small >= 10 else " — below the 10% guide"))
    add("green" if nonequity >= 5 else "amber",
        f"Non-equity {nonequity:.1f}%" +
        ("" if nonequity >= 5 else " — below the 5% guide"))

    if not core:
        add("amber", "No broad-market core holding",
            "Every position is a theme, factor or commodity bet.")
    if thematic > 50:
        add("amber", f"Thematic funds {thematic:.1f}% of the portfolio",
            "Satellites have become the core.")

    order = {"red": 0, "amber": 1, "green": 2}
    alerts.sort(key=lambda a: order.get(a["level"], 3))

    return {
        "ok": True,
        "allocations": [{"ticker": t, "pct": round(w, 2)}
                        for t, w in sorted(alloc.items(), key=lambda kv: -kv[1])],
        "sectors": sectors["sectors"],
        "unknown_pct": sectors["unknown"],
        "themes": themes,
        "roles": roles,
        "stocks": stocks,
        "coverage": disclosure_coverage(alloc, profiles),
        "alerts": alerts,
        "totals": {"technology": round(tech, 2), "semis": semis,
                   "international": intl, "small_cap": small,
                   "non_equity": round(nonequity, 2)},
    }
