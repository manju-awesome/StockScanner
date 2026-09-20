"""
portfolio_risk_scores.py
========================
The *judgments* half of the Portfolio page's risk report, and its entry point.

portfolio_risk.py measures things (beta, vol, correlation, delta, ownership).
This module decides what those measurements mean: which limits are breached,
which holdings are really one bet wearing four tickers, what each position
should do about it, and what the whole book loses in a set of macro shocks.
Every threshold here is a policy choice, which is exactly why they are all
collected in LIMITS at the top and overridable from the environment rather
than scattered through the code as magic numbers.

analyze_portfolio() at the bottom is what the webapp calls. It returns one
JSON-safe dict — the complete report — and writes it to
data/output/portfolio_risk.json so the Portfolio page can render the last run
without re-fetching every time someone reloads.

Scoring direction, because two of these run opposite ways:
    Diversification / Health   0 = terrible, 100 = excellent
    Risk                       0 = placid,   100 = maximum risk
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stockanalysis.core import portfolio_risk as pr


def _limit(name: str, default: float) -> float:
    """Risk limits are configurable — a 12% single-name cap is a house view,
    not a law of finance. Env override: PR_LIMIT_SINGLE_POSITION=10."""
    raw = os.environ.get(f"PR_LIMIT_{name.upper()}")
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


# Percent-of-portfolio caps. Defaults are the profile in the feature request,
# which is close to what a multi-manager platform runs at the pod level.
LIMITS = {
    "single_position":   _limit("single_position", 8.0),
    "high_conviction":   _limit("high_conviction", 12.0),
    "top_5":             _limit("top_5", 40.0),
    "top_10":            _limit("top_10", 60.0),
    "sector":            _limit("sector", 25.0),
    "industry":          _limit("industry", 15.0),
    "theme":             _limit("theme", 30.0),
    "country_non_home":  _limit("country_non_home", 20.0),
    "small_cap":         _limit("small_cap", 20.0),
    "micro_cap":         _limit("micro_cap", 10.0),
    "crypto":            _limit("crypto", 8.0),
    "china":             _limit("china", 10.0),
    "speculative":       _limit("speculative", 15.0),
    "cash_minimum":      _limit("cash_minimum", 5.0),
}

HOME_COUNTRY = os.environ.get("PR_HOME_COUNTRY", "United States")

# A position is "speculative" if it fails the tests a quality screen would
# apply: no earnings, tiny cap, or vol that only makes sense as a lottery
# ticket. Deliberately mechanical — "speculative" as a vibe is how a book ends
# up 40% in story stocks with every individual one looking defensible.
SPECULATIVE_VOL_PCT = float(os.environ.get("PR_SPECULATIVE_VOL", "70"))
SPECULATIVE_CAP = float(os.environ.get("PR_SPECULATIVE_CAP", "10e9"))

HIGH_CORR = 0.75          # "these are the same trade"
LIQUIDITY_ADV_PCT = 10.0  # max share of ADV to take in one day when exiting
EARNINGS_WINDOW = 14      # days — event risk lookahead

# Below this weight a position cannot move the portfolio, so a risk report
# has nothing useful to say about it. These get bucketed as housekeeping
# rather than scored — otherwise a 0.002-share META stub collects the same
# "too small / high vol / below the 200MA" penalties as a real position and
# lands in the exit list next to something that actually matters.
DUST_PCT = float(os.environ.get("PR_DUST_PCT", "0.25"))


def resolve_portfolio_value(equity_value: float, option_premium: float) -> tuple[float, str]:
    """(denominator, how it was derived).

    Every weight, limit and scenario percentage divides by this, so getting it
    from the wrong place quietly invalidates the entire report.

    PORTFOLIO_VALUE is used when explicitly set — it's the only way to tell
    the report about cash it can't see. Otherwise the denominator is the book
    itself (equities at market + option premium paid), which makes weights
    percentages of invested capital.

    ACCOUNT_SIZE is deliberately NOT the fallback here, even though
    reporting.portfolio uses it: it's the risk-sizing constant that answers
    "how many shares is 1% of risk", not a statement about what the account
    holds. Defaulting to it made a $19k book read as 81% cash and cleared
    every concentration limit.
    """
    explicit = os.environ.get("PORTFOLIO_VALUE", "").strip()
    if explicit:
        try:
            v = float(explicit)
            if v > 0:
                return v, "PORTFOLIO_VALUE from .env (includes cash)"
        except ValueError:
            pass
    book = (equity_value or 0.0) + (option_premium or 0.0)
    return max(book, 1.0), ("invested capital — equities at market + option "
                            "premium; set PORTFOLIO_VALUE in .env to include cash")


# ─────────────────────────────────────────────────────────────────────────────
# CONCENTRATION
# ─────────────────────────────────────────────────────────────────────────────

def _bucket(holdings: list[dict], key_fn, weight_key="Weight_Pct") -> list[tuple[str, float]]:
    """Sum weights into named buckets, biggest first. key_fn may return a list
    (themes) — then the holding counts fully in each bucket it belongs to,
    which is correct for overlap analysis and means theme weights legitimately
    sum past 100%."""
    out: dict[str, float] = {}
    for h in holdings:
        w = h.get(weight_key) or 0.0
        keys = key_fn(h)
        if keys is None:
            keys = ["Unknown"]
        if isinstance(keys, str):
            keys = [keys]
        for k in keys:
            out[k or "Unknown"] = out.get(k or "Unknown", 0.0) + w
    return sorted(((k, round(v, 2)) for k, v in out.items()), key=lambda kv: -kv[1])


def concentration(holdings: list[dict], totals: dict) -> dict:
    """Every allocation cut the limit sheet is written against.

    Long exposure only for the top-N cuts: a short/put position reduces
    concentration, and letting a negative weight net against a long would
    report a book as diversified when it is really two large opposing bets.
    """
    longs = sorted((h for h in holdings if (h.get("Weight_Pct") or 0) > 0),
                   key=lambda h: -(h["Weight_Pct"] or 0))
    weights = [h["Weight_Pct"] or 0.0 for h in longs]

    def top_n(n):
        return round(sum(weights[:n]), 2)

    # Effective N (inverse Herfindahl) is the honest headline: "23 positions"
    # means nothing when two of them are 60% of the risk.
    gross = sum(abs(h.get("Weight_Pct") or 0) for h in holdings)
    hhi = sum((w / gross) ** 2 for w in weights) if gross else 0.0
    effective_n = round(1 / hhi, 1) if hhi else 0.0

    beta_weighted = sum((h.get("Weight_Pct") or 0) * (h.get("Beta_SPY") or 0)
                        for h in holdings) / 100.0
    vol_weighted = sum((h.get("Weight_Pct") or 0) * (h.get("Vol_90D") or 0)
                       for h in holdings) / 100.0

    return {
        "max_position":  {"ticker": longs[0]["Ticker"], "pct": longs[0]["Weight_Pct"]} if longs else None,
        "top_5":         top_n(5),
        "top_10":        top_n(10),
        "top_5_names":   [h["Ticker"] for h in longs[:5]],
        "top_10_names":  [h["Ticker"] for h in longs[:10]],
        "positions":     len(holdings),
        "effective_n":   effective_n,
        "invested_pct":  totals.get("invested_pct"),
        "cash_pct":      totals.get("cash_pct"),
        "sectors":       _bucket(holdings, lambda h: h.get("Sector")),
        "industries":    _bucket(holdings, lambda h: h.get("Industry")),
        "caps":          _bucket(holdings, lambda h: h.get("Cap_Bucket")),
        "countries":     _bucket(holdings, lambda h: h.get("Country")),
        "themes":        _bucket(holdings, lambda h: h.get("Themes") or ["Untagged"]),
        "strategies":    _bucket(holdings, lambda h: h.get("Strategy")),
        "beta_weighted_exposure": round(beta_weighted, 3),
        "vol_weighted_exposure":  round(vol_weighted, 2),
        "dollar_weights": [(h["Ticker"], h.get("Exposure")) for h in longs],
    }


def risk_contributions(holdings: list[dict], closes: pd.DataFrame,
                       portfolio_value: float) -> list[dict]:
    """Each position's share of total portfolio volatility.

    This is the number that reorders a book. Allocation says a 3% position is
    small; risk contribution says a 3% position running 90% vol and correlated
    0.9 with the largest holding is contributing 8% of the risk. Computed as
    the standard marginal contribution w_i·(Σw)_i / σ_p².
    """
    tickers = [h["Ticker"] for h in holdings if h["Ticker"] in closes.columns]
    if len(tickers) < 2 or not portfolio_value:
        return []
    weights = np.array([(next(h for h in holdings if h["Ticker"] == t)["Exposure"] or 0.0)
                        / portfolio_value for t in tickers])
    rets = pr.daily_returns(closes)[tickers].dropna(how="all").fillna(0.0)
    if len(rets) < 40:
        return []
    cov = rets.cov().to_numpy() * pr.TRADING_DAYS
    port_var = float(weights @ cov @ weights)
    if port_var <= 0:
        return []
    port_vol = math.sqrt(port_var)
    marginal = cov @ weights
    contrib = weights * marginal
    out = []
    for t, w, c, m in zip(tickers, weights, contrib, marginal):
        out.append({
            "Ticker": t,
            "Weight_Pct": round(float(w) * 100, 2),
            "Risk_Contribution_Pct": round(float(c / port_var) * 100, 2),
            "Marginal_Vol": round(float(m / port_vol) * 100, 2),
            # >1 means the position punches above its allocation — the single
            # most actionable column in the whole report.
            "Risk_Per_Dollar": (round(float(c / port_var) / float(w), 2)
                                if w else None),
        })
    out.sort(key=lambda r: -(r["Risk_Contribution_Pct"] or 0))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# OVERLAP — what is secretly one bet
# ─────────────────────────────────────────────────────────────────────────────

def overlap_analysis(holdings: list[dict], corr_tickers: list[str],
                     matrix: list[list[float | None]]) -> list[dict]:
    """Group holdings by shared theme and score each cluster as a single bet.

    A theme cluster is dangerous in proportion to three things: how much of
    the book it is, how tightly its members move together, and how few
    distinct catalysts sit behind it. The risk score below multiplies the
    first two — 30% of the portfolio at 0.9 average correlation is a 30%
    position with extra steps.
    """
    idx = {t: i for i, t in enumerate(corr_tickers)}

    def avg_pairwise(tickers):
        vals = []
        for i, a in enumerate(tickers):
            for b in tickers[i + 1:]:
                if a in idx and b in idx:
                    v = matrix[idx[a]][idx[b]]
                    if v is not None:
                        vals.append(v)
        return round(float(np.mean(vals)), 2) if vals else None

    clusters = []
    by_theme: dict[str, list[dict]] = {}
    for h in holdings:
        for theme in (h.get("Themes") or []):
            by_theme.setdefault(theme, []).append(h)

    for theme, members in by_theme.items():
        if len(members) < 2:
            continue
        tickers = [m["Ticker"] for m in members]
        weight = round(sum(m.get("Weight_Pct") or 0 for m in members), 2)
        corr = avg_pairwise(tickers)
        # 0-100. Weight relative to the theme cap does the heavy lifting;
        # correlation scales it, because 5 uncorrelated names in a theme is a
        # sector bet and 5 correlated ones is a single position.
        weight_score = min(100.0, weight / max(LIMITS["theme"], 1) * 100)
        corr_mult = 0.6 + 0.4 * ((corr + 1) / 2) if corr is not None else 0.8
        risk_score = round(min(100.0, weight_score * corr_mult), 0)
        biggest = max(members, key=lambda m: m.get("Weight_Pct") or 0)
        clusters.append({
            "theme": theme,
            "tickers": sorted(tickers, key=lambda t: -next(
                (m.get("Weight_Pct") or 0) for m in members if m["Ticker"] == t)),
            "count": len(members),
            "weight_pct": weight,
            "avg_correlation": corr,
            "risk_score": risk_score,
            "over_limit": weight > LIMITS["theme"],
            # One name carrying more than half a cluster means the theme's
            # diversification is an illusion — it IS that position.
            "single_point_of_failure": (
                f'{biggest["Ticker"]} is {round((biggest.get("Weight_Pct") or 0) / weight * 100)}% '
                f"of this theme's exposure"
                if weight and (biggest.get("Weight_Pct") or 0) / weight > 0.5 else None),
            "duplicate_exposure": (
                "members move as one position (avg correlation ≥ 0.75)"
                if corr is not None and corr >= HIGH_CORR else None),
        })
    clusters.sort(key=lambda c: -c["risk_score"])
    return clusters


def correlation_stats(corr_tickers: list[str], matrix: list[list[float | None]],
                      holdings: list[dict]) -> dict:
    """Average pairwise correlation plus the specific pairs that are the same
    trade — weighted by how much of the book each pair actually is, so a
    0.95-correlated pair of 0.1% positions doesn't headline the report."""
    weight = {h["Ticker"]: (h.get("Weight_Pct") or 0) for h in holdings}
    vals, pairs = [], []
    for i, a in enumerate(corr_tickers):
        for j in range(i + 1, len(corr_tickers)):
            v = matrix[i][j]
            if v is None:
                continue
            vals.append(v)
            b = corr_tickers[j]
            combined = weight.get(a, 0) + weight.get(b, 0)
            if v >= HIGH_CORR and combined > 1.0:
                pairs.append({"a": a, "b": b, "corr": round(v, 2),
                              "combined_weight_pct": round(combined, 2)})
    pairs.sort(key=lambda p: -(p["corr"] * p["combined_weight_pct"]))
    return {
        "avg_pairwise": round(float(np.mean(vals)), 3) if vals else None,
        "max_pairwise": round(float(np.max(vals)), 2) if vals else None,
        "high_pairs": pairs[:15],
        "pairs_measured": len(vals),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LIMIT VIOLATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _is_speculative(h: dict) -> bool:
    cap = h.get("Market_Cap") or 0
    vol = h.get("Vol_90D") or 0
    no_earnings = (h.get("PE") is None and h.get("Profit_Margin") is not None
                   and h["Profit_Margin"] < 0)
    return bool(vol >= SPECULATIVE_VOL_PCT
                or (cap and cap < SPECULATIVE_CAP and vol >= 50)
                or no_earnings)


def limit_violations(holdings: list[dict], conc: dict, totals: dict) -> list[dict]:
    """Every breach of LIMITS, each with the risk it actually creates.

    The `risk` string is the point of the exercise. "NVDA 14% > 8%" is a fact;
    "one earnings gap moves the whole book 3-4%" is why anyone should care.
    """
    out = []

    def add(name, scope, actual, cap, risk, severity=None):
        if actual is None or actual <= cap:
            return
        over = actual - cap
        sev = severity or ("critical" if over > cap * 0.5 else
                           "high" if over > cap * 0.2 else "moderate")
        out.append({"limit": name, "scope": scope, "actual": round(actual, 2),
                    "cap": cap, "over_by": round(over, 2), "severity": sev,
                    "risk": risk})

    for h in holdings:
        w = h.get("Weight_Pct") or 0
        if w <= LIMITS["single_position"]:
            continue
        cap = (LIMITS["high_conviction"] if h.get("Strategy") == "longterm"
               else LIMITS["single_position"])
        label = ("high-conviction single position" if cap == LIMITS["high_conviction"]
                 else "single position")
        vol = h.get("Vol_90D")
        move = f"a 1-day {vol:.0f}%-vol move" if vol else "a single adverse day"
        daily_shock = (w * (vol or 40) / 100 / math.sqrt(pr.TRADING_DAYS)) if vol else None
        add(label, h["Ticker"], w, cap,
            f"{h['Ticker']} is {w:.1f}% of the book; "
            + (f"{move} swings the portfolio ~{daily_shock:.2f}% on its own."
               if daily_shock else "idiosyncratic news moves the portfolio directly."))

    add("top 5 holdings", ", ".join(conc["top_5_names"]), conc["top_5"], LIMITS["top_5"],
        "Five names drive most of the P&L — the other positions are noise "
        "around them, and diversification below the top 5 is cosmetic.")
    add("top 10 holdings", ", ".join(conc["top_10_names"]), conc["top_10"], LIMITS["top_10"],
        "The tail of the book contributes little; consider consolidating it "
        "into fewer, better-sized positions or raising cash.")

    for sector, w in conc["sectors"]:
        add("sector", sector, w, LIMITS["sector"],
            f"A sector-wide de-rating in {sector} hits {w:.1f}% of the book at "
            f"once, and sector drawdowns are the ones that last quarters.")
    for industry, w in conc["industries"][:8]:
        # "ETF / fund" is a placeholder for "this holding has no industry",
        # not an industry — flagging it would tell the user their funds are
        # concentrated in being funds.
        if industry and industry not in ("Unknown", "ETF / fund"):
            add("industry", industry, w, LIMITS["industry"],
                f"{industry} names share customers, supply chains and pricing "
                f"cycles — they re-rate together.")
    for theme, w in conc["themes"]:
        if theme != "Untagged":
            add("theme", theme, w, LIMITS["theme"],
                f"{theme} is a single catalyst set. One narrative break "
                f"re-prices {w:.1f}% of the portfolio in the same session.")
    for country, w in conc["countries"]:
        # "Global" is what a commodity ETF resolves to — it carries no single
        # country's currency, policy or disclosure risk, which is the entire
        # thing this limit exists to cap.
        if country and country not in (HOME_COUNTRY, "Unknown", "Global"):
            cap = LIMITS["china"] if country == "China" else LIMITS["country_non_home"]
            add("country", country, w, cap,
                f"Non-home exposure to {country} carries currency, disclosure "
                f"and policy risk the rest of the book doesn't have.")

    caps = dict(conc["caps"])
    add("small cap", "Small", caps.get("Small"), LIMITS["small_cap"],
        "Small caps gap on liquidity, not just news — exits get expensive "
        "exactly when everyone wants one.")
    add("micro cap", "Micro", caps.get("Micro"), LIMITS["micro_cap"],
        "Micro caps can be untradeable in a stressed tape; size them as if "
        "the exit takes a week, because it might.")

    spec = round(sum(h.get("Weight_Pct") or 0 for h in holdings if _is_speculative(h)), 2)
    add("speculative", "high-vol / unprofitable / sub-scale names", spec,
        LIMITS["speculative"],
        "Speculative names are correlated in drawdowns even when their stories "
        "are unrelated — they sell off together when risk appetite turns.")

    crypto = dict(conc["themes"]).get("Crypto")
    add("crypto", "Crypto", crypto, LIMITS["crypto"],
        "Crypto-linked equities carry both equity beta and bitcoin beta — the "
        "drawdowns compound rather than offset.")

    cash_pct = totals.get("cash_pct")
    if cash_pct is not None and cash_pct < LIMITS["cash_minimum"]:
        out.append({
            "limit": "cash minimum", "scope": "portfolio",
            "actual": round(cash_pct, 2), "cap": LIMITS["cash_minimum"],
            "over_by": round(LIMITS["cash_minimum"] - cash_pct, 2),
            "severity": "high" if cash_pct < LIMITS["cash_minimum"] / 2 else "moderate",
            "risk": "No dry powder. Every rebalance becomes a forced sale, and "
                    "a drawdown offers opportunities the book can't take.",
        })

    order = {"critical": 0, "high": 1, "moderate": 2}
    out.sort(key=lambda v: (order.get(v["severity"], 3), -v["over_by"]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# LIQUIDITY
# ─────────────────────────────────────────────────────────────────────────────

def liquidity_analysis(holdings: list[dict]) -> list[dict]:
    """Days to exit each position without exceeding LIQUIDITY_ADV_PCT of ADV.

    Uses delta-adjusted share equivalents, so an options-heavy name reports
    the shares someone would actually have to move to flatten the exposure.
    """
    out = []
    for h in holdings:
        adv = h.get("ADV_Shares")
        price = h.get("Price")
        exposure = h.get("Exposure") or 0
        shares_equiv = (exposure / price) if price else None
        days = None
        if adv and shares_equiv:
            capacity = adv * LIQUIDITY_ADV_PCT / 100.0
            days = round(abs(shares_equiv) / capacity, 2) if capacity else None
        out.append({
            "Ticker": h["Ticker"],
            "Shares_Equivalent": round(shares_equiv, 1) if shares_equiv else None,
            "ADV_Shares": h.get("ADV_Shares"),
            "ADV_Dollars": h.get("ADV_Dollars"),
            "Pct_Of_ADV": (round(abs(shares_equiv) / adv * 100, 3)
                           if (adv and shares_equiv) else None),
            "Days_To_Exit": days,
            "Flag": ("illiquid — multi-day exit" if days and days > 1
                     else "thin" if days and days > 0.25 else "liquid"),
        })
    out.sort(key=lambda r: -(r["Days_To_Exit"] or 0))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────

def position_sizing(holdings: list[dict], contributions: list[dict],
                    corr_tickers: list[str], matrix: list[list[float | None]],
                    technicals: dict[str, dict]) -> list[dict]:
    """INCREASE / MAINTAIN / TRIM / EXIT per holding, with the reasons.

    Scored rather than branched: each input contributes a signed vote, and the
    net decides. A single bad input shouldn't force an exit (a name can be
    below its 200MA and still be a correctly-sized long), but three should.
    """
    risk_by_ticker = {c["Ticker"]: c for c in contributions}
    idx = {t: i for i, t in enumerate(corr_tickers)}
    out = []

    for h in holdings:
        t = h["Ticker"]
        w = h.get("Weight_Pct") or 0
        tech = technicals.get(t) or {}
        cap = (LIMITS["high_conviction"] if h.get("Strategy") == "longterm"
               else LIMITS["single_position"])

        # Dust is a housekeeping question, not a risk one — decided here and
        # skipped past the scoring so it can't crowd the real calls out of the
        # exit list.
        if h.get("Is_Dust"):
            out.append({
                "Ticker": t, "Weight_Pct": w, "Action": "CLOSE",
                "Rationale": "dust — too small to affect the portfolio",
                "Score": 0, "Is_Dust": True,
                "Reasons": [f"{w:.2f}% of the book — below the {DUST_PCT:g}% "
                            f"threshold where a position can move anything",
                            "close it or size it up to a real position; either "
                            "way it shouldn't sit here costing attention"],
                "Vol_90D": h.get("Vol_90D"), "Trend": tech.get("Trend"),
                "Risk_Contribution_Pct": (risk_by_ticker.get(t) or {}).get("Risk_Contribution_Pct"),
            })
            continue

        score, reasons = 0, []
        if w > cap * 1.5:
            score -= 3
            reasons.append(f"{w:.1f}% is {w / cap:.1f}× the {cap:g}% cap")
        elif w > cap:
            score -= 2
            reasons.append(f"{w:.1f}% over the {cap:g}% cap")
        elif w < cap * 0.08:
            # Sub-scale but not dust: it can move the needle slightly, which
            # is the worst size — real enough to lose money on, too small to
            # be worth the monitoring.
            score -= 1
            reasons.append(f"{w:.2f}% is sub-scale against a {cap:g}% cap")

        vol = h.get("Vol_90D")
        if vol and vol >= 80:
            score -= 2
            reasons.append(f"90d vol {vol:.0f}% — size for the volatility, not the story")
        elif vol and vol >= 55:
            score -= 1
            reasons.append(f"90d vol {vol:.0f}% is high")

        rc = risk_by_ticker.get(t)
        if rc and rc.get("Risk_Per_Dollar") and rc["Risk_Per_Dollar"] > 1.6 and w > 1:
            score -= 2
            reasons.append(f"contributes {rc['Risk_Contribution_Pct']:.1f}% of portfolio "
                           f"risk on {w:.1f}% of capital")

        if t in idx:
            peers = [(corr_tickers[j], matrix[idx[t]][j]) for j in range(len(corr_tickers))
                     if j != idx[t] and matrix[idx[t]][j] is not None]
            hot = [p for p in peers if p[1] >= HIGH_CORR]
            if len(hot) >= 3:
                score -= 2
                reasons.append(f"correlated ≥{HIGH_CORR} with {len(hot)} other holdings "
                               f"({', '.join(p[0] for p in hot[:3])}…)")
            elif hot:
                score -= 1
                reasons.append(f"duplicates {', '.join(p[0] for p in hot)}")

        if tech.get("Above_200MA") is False:
            score -= 2
            reasons.append(f"below the 200MA ({tech.get('Dist_200MA_Pct'):+.0f}%) — "
                           f"trend broken")
        elif tech.get("Trend") == "Strong uptrend":
            score += 1
            reasons.append("price > 20 > 50 > 200MA — trend intact")
        rsi = tech.get("RSI")
        if rsi and rsi >= 80:
            score -= 1
            reasons.append(f"RSI {rsi:.0f} — extended, poor entry for adds")
        elif rsi and rsi <= 30 and tech.get("Above_200MA"):
            score += 1
            reasons.append(f"RSI {rsi:.0f} in an uptrend — pullback, not a break")

        # Fundamentals: only vote when the data exists. A missing ROE is not
        # a bad ROE, and treating it as one would penalize every ETF.
        roe, de, growth = h.get("ROE"), h.get("Debt_Equity"), h.get("Revenue_Growth")
        if roe is not None and roe > 20 and (de is None or de < 150):
            score += 1
            reasons.append(f"ROE {roe:.0f}% with contained leverage")
        if de is not None and de > 200:
            score -= 1
            reasons.append(f"debt/equity {de:.0f} — leveraged into a rate shock")
        if growth is not None and growth < 0:
            score -= 1
            reasons.append(f"revenue growth {growth:.0f}% — shrinking")

        dte = h.get("Days_To_Earnings")
        if dte is not None and 0 <= dte <= 7 and w > LIMITS["single_position"]:
            score -= 1
            reasons.append(f"earnings in {dte}d on an oversized position")

        # Thresholds are deliberately asymmetric: it takes more evidence to
        # tell someone to sell than to leave a position alone, because the
        # inputs that drive a sell (vol, risk contribution, broken trend) are
        # themselves correlated and can all fire off one bad quarter.
        if score <= -6:
            action, why = "EXIT", "multiple risk limits breached and the trend is broken"
        elif score <= -3:
            action, why = "TRIM", "oversized for its volatility and correlation"
        elif score >= 2:
            action, why = "INCREASE", "quality and trend support a larger position"
        else:
            action, why = "MAINTAIN", "sized appropriately for its risk"

        # Never recommend adding to something already over its cap, whatever
        # the other votes say — the cap is the binding constraint.
        if action == "INCREASE" and w > cap:
            action, why = "MAINTAIN", "attractive, but already at its position cap"

        out.append({
            "Ticker": t, "Weight_Pct": w, "Action": action, "Rationale": why,
            "Score": score, "Reasons": reasons or ["no flags"],
            "Vol_90D": vol, "Trend": tech.get("Trend"),
            "Risk_Contribution_Pct": (rc or {}).get("Risk_Contribution_Pct"),
        })

    # Dust sorts last: it's the only bucket that isn't a risk decision.
    rank = {"EXIT": 0, "TRIM": 1, "INCREASE": 2, "MAINTAIN": 3, "CLOSE": 4}
    out.sort(key=lambda r: (rank[r["Action"]], -(r["Weight_Pct"] or 0)))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# SCORES
# ─────────────────────────────────────────────────────────────────────────────

def _scale(value, good, bad) -> float:
    """Map a metric to 0-100 where `good` scores 100 and `bad` scores 0,
    clamped. Works in either direction (good may be below or above bad)."""
    if value is None:
        return 50.0
    if good == bad:
        return 50.0
    pct = (value - bad) / (good - bad)
    return round(max(0.0, min(1.0, pct)) * 100, 1)


def diversification_score(conc: dict, corr: dict, holdings: list[dict]) -> dict:
    """0-100, higher is better. Each component is the same question asked of a
    different axis: how much of the book rides on one thing?"""
    def top_bucket(items):
        return items[0][1] if items else 0.0

    sectors = _scale(top_bucket(conc["sectors"]), LIMITS["sector"] * 0.6, LIMITS["sector"] * 2)
    industries = _scale(top_bucket(conc["industries"]), LIMITS["industry"] * 0.6, LIMITS["industry"] * 2.5)
    themes = _scale(top_bucket([t for t in conc["themes"] if t[0] != "Untagged"]),
                    LIMITS["theme"] * 0.6, LIMITS["theme"] * 2.2)
    non_home = sum(w for c, w in conc["countries"] if c and c not in (HOME_COUNTRY, "Unknown"))
    # Both extremes are a failure: 0% non-home is a single-economy bet, and
    # >40% is uncompensated country risk for a US-based book.
    country = 100.0 - abs(non_home - 15) * 3
    country = round(max(0.0, min(100.0, country)), 1)
    correlation_s = _scale(corr.get("avg_pairwise"), 0.15, 0.75)
    caps = dict(conc["caps"])
    mega_large = (caps.get("Mega", 0) + caps.get("Large", 0))
    market_cap = _scale(abs(mega_large - 60), 0, 60)     # ~60% large/mega is balanced
    effective = _scale(conc["effective_n"], 15, 2)

    # Factor spread: how many distinct macro drivers the book actually has.
    betas = [h.get("Beta_SPY") for h in holdings if h.get("Beta_SPY") is not None]
    factor = _scale(float(np.std(betas)) if len(betas) > 2 else None, 0.6, 0.05)

    components = {
        "Sector": sectors, "Industry": industries, "Theme": themes,
        "Country": country, "Correlation": correlation_s,
        "Market Cap": market_cap, "Effective N": effective, "Factor": factor,
    }
    weights = {"Sector": 0.2, "Industry": 0.12, "Theme": 0.2, "Country": 0.08,
               "Correlation": 0.2, "Market Cap": 0.08, "Effective N": 0.07,
               "Factor": 0.05}
    score = round(sum(components[k] * weights[k] for k in components), 1)
    return {"score": score, "components": components}


def risk_score(holdings: list[dict], conc: dict, corr: dict, totals: dict,
               liquidity: list[dict], stress: dict, violations: list[dict]) -> dict:
    """0-100 where HIGHER IS RISKIER — the opposite direction from the
    diversification and health scores, which is why it is labelled everywhere
    it is displayed."""
    port_vol = stress.get("annual_vol_pct")
    market = _scale(abs(conc.get("beta_weighted_exposure") or 0), 0.3, 1.6)
    volatility = _scale(port_vol, 12, 55)
    illiquid = sum(1 for r in liquidity if (r.get("Days_To_Exit") or 0) > 1)
    liquidity_s = _scale(illiquid, 0, max(3, len(liquidity) * 0.3))
    earnings_soon = sum(h.get("Weight_Pct") or 0 for h in holdings
                        if h.get("Days_To_Earnings") is not None
                        and 0 <= h["Days_To_Earnings"] <= EARNINGS_WINDOW)
    earnings = _scale(earnings_soon, 5, 45)
    concentration_s = _scale(conc.get("top_5"), LIMITS["top_5"] * 0.6, LIMITS["top_5"] * 1.8)
    sector_s = _scale(conc["sectors"][0][1] if conc["sectors"] else 0,
                      LIMITS["sector"] * 0.6, LIMITS["sector"] * 2)
    correlation_s = _scale(corr.get("avg_pairwise"), 0.15, 0.8)
    drawdown = _scale(abs(stress.get("worst_historical_drawdown_pct") or 0), 10, 55)
    tail = _scale(abs(stress.get("expected_shortfall_99_pct") or 0), 2, 12)
    critical = sum(1 for v in violations if v["severity"] == "critical")
    event = _scale(critical, 0, 4)

    # Each is inverted here: _scale() returns "how good", risk wants "how bad".
    components = {
        "Market Risk":        round(100 - market, 1),
        "Volatility Risk":    round(100 - volatility, 1),
        "Liquidity Risk":     round(100 - liquidity_s, 1),
        "Earnings Risk":      round(100 - earnings, 1),
        "Event Risk":         round(100 - event, 1),
        "Concentration Risk": round(100 - concentration_s, 1),
        "Sector Risk":        round(100 - sector_s, 1),
        "Correlation Risk":   round(100 - correlation_s, 1),
        "Drawdown Risk":      round(100 - drawdown, 1),
        "Tail Risk":          round(100 - tail, 1),
    }
    score = round(sum(components.values()) / len(components), 1)
    band = ("Extreme" if score >= 80 else "High" if score >= 65 else
            "Elevated" if score >= 50 else "Moderate" if score >= 35 else "Low")
    return {"score": score, "band": band, "components": components,
            "direction": "higher = riskier"}


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIOS & STRESS
# ─────────────────────────────────────────────────────────────────────────────

# (label, factor key, shock, unit). Shocks are the move in the FACTOR; each
# holding's response is its own regression beta against that factor, so the
# same -10% SPY prints a different number for OKLO than for GLD.
FACTOR_SCENARIOS = [
    ("SPY −5%",            "SPY",    -0.05, "pct"),
    ("SPY −10%",           "SPY",    -0.10, "pct"),
    ("QQQ −15%",           "QQQ",    -0.15, "pct"),
    ("VIX +30%",           "VIX",     0.30, "pct"),
    ("Interest rates +1%", "TNX",     1.00, "points"),
    ("Oil +20%",           "OIL",     0.20, "pct"),
    ("Gold +15%",          "GOLD",    0.15, "pct"),
    ("USD +10%",           "USD",     0.10, "pct"),
    ("Semiconductors −15%", "SEMI",  -0.15, "pct"),
    ("Crypto −30%",        "CRYPTO", -0.30, "pct"),
]

# Theme shocks bypass the regression: "AI stocks −20%" is a statement about
# the tagged names themselves, not about a proxy index they happen to track.
THEME_SCENARIOS = [("AI stocks −20%", "AI", -0.20)]


def scenarios(holdings: list[dict], portfolio_value: float) -> list[dict]:
    out = []
    for label, factor, shock, unit in FACTOR_SCENARIOS:
        pnl, covered, missing = 0.0, 0.0, []
        for h in holdings:
            beta = (h.get("Factor_Betas") or {}).get(factor)
            exposure = h.get("Exposure") or 0.0
            if beta is None:
                if abs(exposure) > 0:
                    missing.append(h["Ticker"])
                continue
            pnl += exposure * beta * shock
            covered += abs(exposure)
        out.append({
            "scenario": label, "factor": factor,
            "shock": f"{shock:+.0%}" if unit == "pct" else f"{shock:+.2f} pts",
            "pnl": round(pnl, 0),
            "pct_of_portfolio": round(pnl / portfolio_value * 100, 2) if portfolio_value else None,
            "method": "regression beta to the factor over 2y of daily data",
            "uncovered_tickers": missing,
        })

    for label, theme, shock in THEME_SCENARIOS:
        members = [h for h in holdings if theme in (h.get("Themes") or [])]
        pnl = sum((h.get("Exposure") or 0.0) * shock for h in members)
        out.append({
            "scenario": label, "factor": f"theme:{theme}",
            "shock": f"{shock:+.0%}",
            "pnl": round(pnl, 0),
            "pct_of_portfolio": round(pnl / portfolio_value * 100, 2) if portfolio_value else None,
            "method": f"direct shock to the {len(members)} holdings tagged {theme}",
            "uncovered_tickers": [],
        })
    out.sort(key=lambda s: s["pnl"])
    return out


def stress_test(port_returns: pd.Series, closes: pd.DataFrame,
                weights: dict[str, float], portfolio_value: float,
                equity_value: float) -> dict:
    """VaR, expected shortfall and drawdown for the current book.

    Historical simulation is primary and parametric is shown beside it. They
    disagree by design: the parametric number assumes a normal distribution,
    the historical one doesn't, and the gap between them IS the tail risk
    estimate. Reporting only the parametric figure is how a book ends up
    "99% safe" right up until the day it isn't.
    """
    out = {
        "observations": int(len(port_returns)),
        "annual_vol_pct": None, "daily_vol_pct": None,
        "var_95_pct": None, "var_99_pct": None,
        "var_95_dollars": None, "var_99_dollars": None,
        "expected_shortfall_95_pct": None, "expected_shortfall_99_pct": None,
        "expected_shortfall_95_dollars": None,
        "parametric_var_95_pct": None, "parametric_var_99_pct": None,
        "worst_historical_drawdown_pct": None,
        "worst_day_pct": None, "best_day_pct": None,
        "expected_max_drawdown_pct": None, "expected_recovery_months": None,
        "sharpe": None, "sortino": None,
        "basis": "delta-adjusted exposure vs total portfolio value",
    }
    r = port_returns.dropna()
    if len(r) < 60:
        out["note"] = ("not enough overlapping history to stress this book "
                       f"({len(r)} usable days)")
        return out

    # Scale from "return on invested capital" to "return on the whole account"
    # — cash doesn't move, so a 90%-invested book takes 90% of the hit.
    invested_share = (equity_value / portfolio_value) if portfolio_value else 1.0
    daily = r * invested_share

    daily_vol = float(daily.std(ddof=1))
    annual_vol = daily_vol * math.sqrt(pr.TRADING_DAYS)
    out["daily_vol_pct"] = round(daily_vol * 100, 3)
    out["annual_vol_pct"] = round(annual_vol * 100, 1)

    var95 = float(np.percentile(daily, 5))
    var99 = float(np.percentile(daily, 1))
    out["var_95_pct"] = round(var95 * 100, 2)
    out["var_99_pct"] = round(var99 * 100, 2)
    out["var_95_dollars"] = round(var95 * portfolio_value, 0)
    out["var_99_dollars"] = round(var99 * portfolio_value, 0)

    tail95 = daily[daily <= var95]
    tail99 = daily[daily <= var99]
    if len(tail95):
        out["expected_shortfall_95_pct"] = round(float(tail95.mean()) * 100, 2)
        out["expected_shortfall_95_dollars"] = round(float(tail95.mean()) * portfolio_value, 0)
    if len(tail99):
        out["expected_shortfall_99_pct"] = round(float(tail99.mean()) * 100, 2)

    out["parametric_var_95_pct"] = round(-1.645 * daily_vol * 100, 2)
    out["parametric_var_99_pct"] = round(-2.326 * daily_vol * 100, 2)

    curve = (1 + daily).cumprod()
    dd = curve / curve.cummax() - 1
    out["worst_historical_drawdown_pct"] = round(float(dd.min()) * 100, 1)
    out["worst_day_pct"] = round(float(daily.min()) * 100, 2)
    out["best_day_pct"] = round(float(daily.max()) * 100, 2)

    # Time actually spent below the prior peak at the worst episode — the
    # honest version of "recovery time", measured rather than modelled.
    trough = dd.idxmin()
    peak = curve.loc[:trough].idxmax()
    recovered = curve.loc[trough:][curve.loc[trough:] >= curve.loc[peak]]
    if len(recovered):
        out["observed_recovery_days"] = int((recovered.index[0] - trough).days)
    else:
        out["observed_recovery_days"] = None
        out["still_underwater"] = bool(dd.iloc[-1] < -0.01)

    mean_daily = float(daily.mean())

    # Forward-looking max drawdown: at zero drift, E[maxDD] over a year runs
    # roughly 1.5-2× annualized vol. The obvious linear form (1.75 × σ) breaks
    # on a high-vol book — at 62% vol it returns −108%, and a portfolio cannot
    # fall more than 100%. Using 1 − exp(−1.75σ) keeps the same slope for
    # ordinary vol and saturates toward −100% instead of walking through it.
    out["expected_max_drawdown_pct"] = -round((1 - math.exp(-1.75 * annual_vol)) * 100, 1)

    # Recovery is driven by drift, not volatility: getting back from −50%
    # takes a +100% gain, and how long that takes depends entirely on the
    # return the book earns. Use its own measured drift and say so — if the
    # drift is negative there is no honest recovery estimate to give.
    dd_frac = abs(out["expected_max_drawdown_pct"]) / 100
    out["required_gain_to_recover_pct"] = round(dd_frac / (1 - dd_frac) * 100, 1)
    monthly_drift = mean_daily * 21
    months = (math.log(1 / (1 - dd_frac)) / math.log(1 + monthly_drift)
              if monthly_drift > 0 else None)
    # Past ~10 years the estimate is arithmetic, not information: a drift that
    # weak can't be distinguished from zero over the sample it was measured on,
    # and printing "265.7 months" implies a precision that isn't there.
    if months is None or months > 120:
        out["expected_recovery_months"] = None
        out["recovery_basis"] = (
            "no estimate — this book's measured drift is negative, so there is "
            "no rate to recover at" if monthly_drift <= 0 else
            "no meaningful estimate — measured drift is too weak to project a "
            "recovery from a drawdown this size")
    else:
        out["expected_recovery_months"] = round(months, 1)
        out["recovery_basis"] = (f"at this book's measured drift "
                                 f"({monthly_drift * 100:+.1f}%/month)")

    if daily_vol:
        out["sharpe"] = round(mean_daily / daily_vol * math.sqrt(pr.TRADING_DAYS), 2)
    downside = daily[daily < 0]
    if len(downside) > 5:
        dstd = float(downside.std(ddof=1))
        if dstd:
            out["sortino"] = round(mean_daily / dstd * math.sqrt(pr.TRADING_DAYS), 2)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# REBALANCING & WATCHLIST
# ─────────────────────────────────────────────────────────────────────────────

SECTOR_ETF_SUGGESTIONS = {
    "Healthcare":         ("XLV", "healthcare — defensive earnings, low correlation to AI capex"),
    "Financial Services": ("XLF", "financials — the one sector that benefits from higher rates"),
    "Consumer Defensive": ("XLP", "staples — the classic drawdown ballast"),
    "Energy":             ("XLE", "energy — the natural hedge against an oil shock"),
    "Utilities":          ("XLU", "utilities — bond proxy, and the AI power-demand trade"),
    "Industrials":        ("XLI", "industrials — cyclical exposure outside tech"),
    "Real Estate":        ("XLRE", "REITs — rate-sensitive, uncorrelated to software"),
    "Basic Materials":    ("XLB", "materials — inflation pass-through"),
    "Consumer Cyclical":  ("XLY", "discretionary — consumer cycle exposure"),
    "Communication Services": ("XLC", "communications — media/telco outside mega-cap tech"),
}

DIVERSIFIER_SUGGESTIONS = [
    ("IEF", "7-10y Treasuries", "the only asset that reliably rallies in an equity panic"),
    ("BIL", "T-bills", "risk-free carry while waiting — counts toward the cash minimum"),
    ("XLV", "Healthcare", "lowest correlation to the AI complex among equity sectors"),
    ("XLP", "Consumer staples", "defensive earnings that don't need a growth narrative"),
]


def rebalancing(sizing: list[dict], holdings: list[dict], conc: dict,
                violations: list[dict], totals: dict,
                portfolio_value: float) -> list[dict]:
    """Concrete trades, in dollars, ordered by how much risk each removes.

    Trims are sized to bring a position back to its cap rather than to zero —
    the goal is a book inside its limits, not a flat one.
    """
    trades = []
    by_ticker = {h["Ticker"]: h for h in holdings}

    for s in sizing:
        if s["Action"] not in ("EXIT", "TRIM"):
            continue
        h = by_ticker.get(s["Ticker"])
        if not h:
            continue
        w = h.get("Weight_Pct") or 0
        cap = (LIMITS["high_conviction"] if h.get("Strategy") == "longterm"
               else LIMITS["single_position"])
        target_w = 0.0 if s["Action"] == "EXIT" else min(cap, w)
        dollars = (w - target_w) / 100 * portfolio_value
        if dollars < portfolio_value * 0.002:      # ignore rounding-noise trades
            continue
        price = h.get("Price")
        equity_value = abs(h.get("Equity_Value") or 0)
        delta_value = abs(h.get("Option_Delta_Value") or 0)
        # A position whose exposure is mostly option delta cannot be reduced
        # by selling shares — GLD showed as "SELL $9,297" while the account
        # held $260 of stock and 3 calls. Say which instrument to work on, and
        # don't print a share count that can't be filled.
        option_led = delta_value > equity_value
        contracts = h.get("Option_Contracts") or 0
        detail = list(s["Reasons"][:2])
        if option_led:
            detail.insert(0, f"exposure is mostly option delta "
                             f"({_money(delta_value)} across {contracts:g} contracts vs "
                             f"{_money(equity_value)} of stock) — close or roll contracts "
                             f"rather than selling shares")
        trades.append({
            "action": "REDUCE Δ" if option_led else "SELL",
            "ticker": s["Ticker"],
            "dollars": round(dollars, 0),
            "shares": (None if option_led
                       else round(dollars / price, 1) if price else None),
            "from_pct": round(w, 2),
            "to_pct": round(target_w, 2),
            "why": s["Rationale"],
            "detail": detail,
            "improves": ["concentration", "volatility"]
                        + (["correlation"] if any("correlat" in r or "duplicat" in r
                                                  for r in s["Reasons"]) else []),
        })

    # Only share sales free up cash. Closing a long call returns its premium,
    # not its delta notional — counting that as proceeds would fund buys with
    # money the trade never produced.
    raised = sum(t["dollars"] for t in trades if t["action"] == "SELL")
    cash_pct = totals.get("cash_pct") or 0
    missing = missing_sectors(conc)

    if raised > 0:
        # Where the proceeds go decides whether this is risk reduction or just
        # a different flavour of the same bet.
        if cash_pct < LIMITS["cash_minimum"]:
            need = (LIMITS["cash_minimum"] - cash_pct) / 100 * portfolio_value
            to_cash = min(raised, need)
            trades.append({
                "action": "HOLD CASH", "ticker": "—",
                "dollars": round(to_cash, 0), "shares": None,
                "from_pct": round(cash_pct, 2),
                "to_pct": round(cash_pct + to_cash / portfolio_value * 100, 2),
                "why": f"restore the {LIMITS['cash_minimum']:g}% cash minimum",
                "detail": ["dry powder converts the next drawdown from a "
                           "problem into an opportunity"],
                "improves": ["drawdown", "flexibility"],
            })
            raised -= to_cash
    if raised > portfolio_value * 0.005:
        per = raised / max(len(missing[:3]) or len(DIVERSIFIER_SUGGESTIONS[:3]), 1)
        picks = ([(SECTOR_ETF_SUGGESTIONS[s][0], SECTOR_ETF_SUGGESTIONS[s][1])
                  for s in missing[:3] if s in SECTOR_ETF_SUGGESTIONS]
                 or [(t, f"{n} — {w}") for t, n, w in DIVERSIFIER_SUGGESTIONS[:3]])
        for ticker, why in picks:
            trades.append({
                "action": "BUY", "ticker": ticker,
                "dollars": round(per, 0), "shares": None,
                "from_pct": 0.0, "to_pct": round(per / portfolio_value * 100, 2),
                "why": why,
                "detail": ["redeploys trim proceeds into an uncorrelated sleeve "
                           "instead of back into the same theme"],
                "improves": ["diversification", "correlation", "sharpe"],
            })

    order = {"SELL": 0, "REDUCE Δ": 0, "HOLD CASH": 1, "BUY": 2}
    trades.sort(key=lambda t: (order.get(t["action"], 3), -t["dollars"]))
    return trades


def missing_sectors(conc: dict) -> list[str]:
    """GICS-ish sectors with no meaningful exposure (<2% of the book)."""
    present = {s: w for s, w in conc["sectors"]}
    return [s for s in SECTOR_ETF_SUGGESTIONS if present.get(s, 0) < 2.0]


def watchlist_gaps(conc: dict, corr: dict) -> dict:
    missing = missing_sectors(conc)
    return {
        "missing_sectors": missing,
        "suggestions": [
            {"ticker": SECTOR_ETF_SUGGESTIONS[s][0], "sector": s,
             "why": SECTOR_ETF_SUGGESTIONS[s][1]}
            for s in missing if s in SECTOR_ETF_SUGGESTIONS
        ],
        "diversifiers": [
            {"ticker": t, "name": n, "why": w} for t, n, w in DIVERSIFIER_SUGGESTIONS
        ],
        "note": ("These are diversification candidates, not recommendations — "
                 "sized additions to sectors the book currently has no claim on."),
    }


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH SCORE & DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

HEALTH_WEIGHTS = {
    "Diversification": 0.20, "Concentration": 0.20, "Correlation": 0.15,
    "Volatility": 0.15, "Liquidity": 0.10, "Fundamental Quality": 0.10,
    "Technical Health": 0.05, "Cash Allocation": 0.05,
}


def health_score(div: dict, conc: dict, corr: dict, stress: dict,
                 liquidity: list[dict], holdings: list[dict],
                 technicals: dict[str, dict], totals: dict) -> dict:
    """0-100, higher is better, weighted per the profile in HEALTH_WEIGHTS."""
    weighted_days = sum((h.get("Weight_Pct") or 0) for h in holdings
                        if next((r["Days_To_Exit"] or 0 for r in liquidity
                                 if r["Ticker"] == h["Ticker"]), 0) > 1)
    liquidity_s = _scale(weighted_days, 0, 40)

    quality_votes = []
    for h in holdings:
        bits = []
        if h.get("ROE") is not None:
            bits.append(_scale(h["ROE"], 25, -5))
        if h.get("Revenue_Growth") is not None:
            bits.append(_scale(h["Revenue_Growth"], 25, -10))
        if h.get("Debt_Equity") is not None:
            bits.append(_scale(h["Debt_Equity"], 30, 250))
        if h.get("FCF_Yield") is not None:
            bits.append(_scale(h["FCF_Yield"], 5, -3))
        if bits:
            quality_votes.append((h.get("Weight_Pct") or 0, float(np.mean(bits))))
    total_w = sum(w for w, _ in quality_votes)
    quality = (round(sum(w * v for w, v in quality_votes) / total_w, 1)
               if total_w else 50.0)

    above = sum((h.get("Weight_Pct") or 0) for h in holdings
                if (technicals.get(h["Ticker"]) or {}).get("Above_200MA"))
    invested = totals.get("invested_pct") or 0
    technical = _scale(above / invested * 100 if invested else None, 85, 20)

    cash_pct = totals.get("cash_pct")
    # Both directions are penalized: below the minimum is fragile, far above
    # it is a performance drag that this report shouldn't quietly reward.
    cash = (100.0 if cash_pct is not None and LIMITS["cash_minimum"] <= cash_pct <= 25
            else _scale(cash_pct, LIMITS["cash_minimum"], 0) if (cash_pct or 0) < LIMITS["cash_minimum"]
            else _scale(cash_pct, 25, 60))

    components = {
        "Diversification": div["score"],
        "Concentration": _scale(conc.get("top_5"), LIMITS["top_5"] * 0.6, LIMITS["top_5"] * 1.8),
        "Correlation": _scale(corr.get("avg_pairwise"), 0.15, 0.8),
        "Volatility": _scale(stress.get("annual_vol_pct"), 12, 55),
        "Liquidity": liquidity_s,
        "Fundamental Quality": quality,
        "Technical Health": technical,
        "Cash Allocation": round(cash, 1),
    }
    score = round(sum(components[k] * HEALTH_WEIGHTS[k] for k in components), 1)
    band, light = (("Excellent", "🟢") if score >= 90 else
                   ("Strong", "🟢") if score >= 80 else
                   ("Acceptable", "🟡") if score >= 70 else
                   ("Elevated Risk", "🟠") if score >= 60 else
                   ("Immediate Review", "🔴"))
    return {"score": score, "band": band, "light": light,
            "components": components, "weights": HEALTH_WEIGHTS}


def traffic_lights(conc: dict, corr: dict, div: dict, risk: dict, stress: dict,
                   violations: list[dict], totals: dict,
                   liquidity: list[dict]) -> list[dict]:
    """One line per risk area: 🟢 healthy, 🟡 monitor, 🔴 immediate action."""
    def light(value, amber, red, higher_is_worse=True):
        if value is None:
            return "⚪"
        bad = value >= red if higher_is_worse else value <= red
        warn = value >= amber if higher_is_worse else value <= amber
        return "🔴" if bad else "🟡" if warn else "🟢"

    top_sector = conc["sectors"][0] if conc["sectors"] else ("—", 0)
    top_theme = next((t for t in conc["themes"] if t[0] != "Untagged"), ("—", 0))
    max_pos = conc.get("max_position") or {"ticker": "—", "pct": 0}
    cash_pct = totals.get("cash_pct") or 0
    illiquid = sum(1 for r in liquidity if (r.get("Days_To_Exit") or 0) > 1)
    critical = sum(1 for v in violations if v["severity"] == "critical")

    return [
        {"area": "Single position", "light": light(max_pos["pct"], LIMITS["single_position"], LIMITS["high_conviction"]),
         "value": f'{max_pos["ticker"]} {max_pos["pct"]:.1f}%',
         "note": f'cap {LIMITS["single_position"]:g}% / {LIMITS["high_conviction"]:g}% high-conviction'},
        {"area": "Top 5 concentration", "light": light(conc["top_5"], LIMITS["top_5"] * 0.8, LIMITS["top_5"]),
         "value": f'{conc["top_5"]:.1f}%', "note": f'cap {LIMITS["top_5"]:g}%'},
        {"area": "Sector", "light": light(top_sector[1], LIMITS["sector"] * 0.8, LIMITS["sector"]),
         "value": f"{top_sector[0]} {top_sector[1]:.1f}%", "note": f'cap {LIMITS["sector"]:g}%'},
        {"area": "Theme", "light": light(top_theme[1], LIMITS["theme"] * 0.8, LIMITS["theme"]),
         "value": f"{top_theme[0]} {top_theme[1]:.1f}%", "note": f'cap {LIMITS["theme"]:g}%'},
        {"area": "Correlation", "light": light(corr.get("avg_pairwise"), 0.5, 0.65),
         "value": f'avg {corr.get("avg_pairwise")}', "note": "average pairwise, 120d"},
        {"area": "Diversification", "light": light(div["score"], 65, 50, higher_is_worse=False),
         "value": f'{div["score"]:.0f}/100', "note": "higher is better"},
        {"area": "Portfolio volatility", "light": light(stress.get("annual_vol_pct"), 30, 45),
         "value": f'{stress.get("annual_vol_pct")}% annual', "note": "delta-adjusted, current weights"},
        {"area": "Tail risk (99% VaR)", "light": light(abs(stress.get("var_99_pct") or 0), 4, 7),
         "value": f'{stress.get("var_99_pct")}% / day', "note": "historical simulation"},
        {"area": "Liquidity", "light": light(illiquid, 1, 3),
         "value": f"{illiquid} multi-day exits", "note": f"at {LIQUIDITY_ADV_PCT:g}% of ADV"},
        {"area": "Cash", "light": light(cash_pct, LIMITS["cash_minimum"], LIMITS["cash_minimum"] / 2, higher_is_worse=False),
         "value": f"{cash_pct:.1f}%", "note": f'minimum {LIMITS["cash_minimum"]:g}%'},
        {"area": "Limit breaches", "light": light(critical, 1, 2),
         "value": f"{len(violations)} total, {critical} critical", "note": "see the violations table"},
        {"area": "Overall risk", "light": light(risk["score"], 55, 70),
         "value": f'{risk["score"]:.0f}/100 {risk["band"]}', "note": "higher = riskier"},
    ]


def executive_summary(holdings: list[dict], conc: dict, corr: dict, health: dict,
                      risk: dict, stress: dict, violations: list[dict],
                      overlap: list[dict], sizing: list[dict], totals: dict) -> dict:
    """The paragraph a CRO reads first, plus ranked top risks/opportunities."""
    top_risks = []
    for v in violations[:6]:
        top_risks.append({
            "headline": f'{v["limit"].title()}: {v["scope"]} at {v["actual"]:.1f}% '
                        f'(cap {v["cap"]:g}%)',
            "severity": v["severity"], "detail": v["risk"],
        })
    for c in overlap[:3]:
        if c["risk_score"] >= 60:
            top_risks.append({
                "headline": f'{c["theme"]} cluster: {c["count"]} holdings, '
                            f'{c["weight_pct"]:.1f}% of the book, avg correlation '
                            f'{c["avg_correlation"]}',
                "severity": "high" if c["risk_score"] >= 75 else "moderate",
                "detail": (c["single_point_of_failure"] or c["duplicate_exposure"]
                           or "one catalyst set drives all of these positions"),
            })
    opportunities = []
    for s in sizing:
        if s["Action"] == "INCREASE":
            opportunities.append({
                "headline": f'{s["Ticker"]} — room to add at {s["Weight_Pct"]:.1f}%',
                "detail": "; ".join(s["Reasons"][:2]),
            })
    if (totals.get("cash_pct") or 0) > LIMITS["cash_minimum"] * 2:
        opportunities.append({
            "headline": f'{totals["cash_pct"]:.1f}% cash available to deploy',
            "detail": "enough dry powder to add a full position without selling anything",
        })

    exits = [s["Ticker"] for s in sizing if s["Action"] == "EXIT"]
    trims = [s["Ticker"] for s in sizing if s["Action"] == "TRIM"]
    narrative = (
        f'{len(holdings)} positions, {conc["effective_n"]:.1f} effective — '
        f'{conc["top_5"]:.0f}% sits in the top 5. '
        f'Beta-weighted exposure {conc["beta_weighted_exposure"]:.2f}, '
        f'annualized volatility {stress.get("annual_vol_pct")}%, '
        f'99% one-day VaR {stress.get("var_99_pct")}% '
        f'({_money(stress.get("var_99_dollars"))}). '
        f'Health {health["score"]:.0f}/100 ({health["band"]}), '
        f'risk {risk["score"]:.0f}/100 ({risk["band"]}). '
        + (f'{len(violations)} limit breaches, '
           f'{sum(1 for v in violations if v["severity"] == "critical")} critical. '
           if violations else "No limit breaches. ")
        + (f'Sizing calls: {len(exits)} exit, {len(trims)} trim.'
           if (exits or trims) else "No sizing changes required.")
    )
    return {"narrative": narrative, "top_risks": top_risks[:8],
            "top_opportunities": opportunities[:5],
            "exits": exits, "trims": trims}


def _money(v) -> str:
    if v is None:
        return "—"
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def analyze_portfolio(progress_cb=None, write_cache: bool = True) -> dict:
    """Run the full risk report over data/portfolio.csv + options_positions.csv.

    progress_cb(stage, done, total) matches the webapp's job protocol, so the
    Portfolio page shows real stages instead of a spinner during the ~60s of
    Yahoo calls this makes.

    Returns a JSON-safe dict and, unless write_cache=False, saves it to
    data/output/portfolio_risk.json — the page renders that cached copy on
    load so a reload doesn't re-fetch the whole book.
    """
    from stockanalysis.reporting import options_positions as op
    from stockanalysis.reporting import portfolio as pf

    def step(stage, done=None, total=None):
        if progress_cb:
            progress_cb(stage, done, total)

    step("loading positions")
    positions = pf.load_positions()
    options = op.load_options()
    held = [p for p in positions if (p.get("Shares") or 0)]
    open_options = [o for o in options if (o.get("Contracts") or 0)]
    if not held and not open_options:
        raise ValueError("no open positions in portfolio.csv or options_positions.csv")

    tickers = sorted({p["Ticker"] for p in held} | {o["Underlying"] for o in open_options})

    step(f"fetching 2y history for {len(tickers)} names + {len(pr.FACTORS)} factors",
         0, len(tickers))
    closes, volumes, failed = pr.fetch_history(tickers + list(pr.FACTORS.values()))
    if closes.empty:
        raise ValueError("no price history returned — Yahoo may be rate-limiting; "
                         "wait a minute and re-run")

    profiles, profile_errors = {}, []
    for i, t in enumerate(tickers, 1):
        step(f"profile: {t}", i, len(tickers))
        profiles[t] = pr.fetch_profile(t)
        if profiles[t].get("profile_error"):
            profile_errors.append(f'{t}: {profiles[t]["profile_error"]}')

    step("pricing options by delta")
    rets = pr.daily_returns(closes)
    vols = {t: pr.realized_vol(rets[t], 90) if t in rets.columns else None
            for t in tickers}
    rate = pr.risk_free_rate(closes)
    option_rows = pr.build_option_rows(open_options, closes, vols, rate)

    step("building holdings")
    # Two passes: the denominator is derived from the book itself (unless
    # PORTFOLIO_VALUE overrides it), so holdings are built once to price the
    # book and once more to attach weights against the resolved value.
    priced = pr.build_holdings(held, option_rows, closes, volumes, profiles, 1.0)
    equity_value = sum(h.get("Equity_Value") or 0 for h in priced)
    option_market_value = sum(o.get("Market_Value") or 0 for o in option_rows)
    portfolio_value, value_basis = resolve_portfolio_value(equity_value,
                                                           option_market_value)

    holdings = pr.build_holdings(held, option_rows, closes, volumes, profiles,
                                 portfolio_value)
    technicals = {h["Ticker"]: pr.technical_state(closes, h["Ticker"]) for h in holdings}
    for h in holdings:
        h["Is_Dust"] = abs(h.get("Weight_Pct") or 0) < DUST_PCT

    delta_value = sum(h.get("Option_Delta_Value") or 0 for h in holdings)
    exposure_value = equity_value + delta_value
    # Cash is the account minus what the equities and option premium actually
    # cost — not minus the delta exposure, which is leverage, not spend.
    cash = portfolio_value - equity_value - option_market_value
    totals = {
        "portfolio_value": portfolio_value,
        "value_basis": value_basis,
        "equity_value": round(equity_value, 2),
        "option_delta_value": round(delta_value, 2),
        "option_market_value": round(option_market_value, 2),
        "exposure_value": round(exposure_value, 2),
        "gross_exposure_pct": round(sum(abs(h.get("Exposure") or 0) for h in holdings)
                                    / portfolio_value * 100, 1) if portfolio_value else None,
        "net_exposure_pct": round(exposure_value / portfolio_value * 100, 1) if portfolio_value else None,
        "invested_pct": round(exposure_value / portfolio_value * 100, 1) if portfolio_value else None,
        "cash": round(cash, 2),
        "cash_pct": round(cash / portfolio_value * 100, 1) if portfolio_value else None,
        "positions": len(holdings),
        "contracts": len(option_rows),
    }

    step("correlations")
    corr_tickers, matrix = pr.correlation_matrix(closes, [h["Ticker"] for h in holdings])
    corr = correlation_stats(corr_tickers, matrix, holdings)

    step("scoring")
    conc = concentration(holdings, totals)
    contributions = risk_contributions(holdings, closes, portfolio_value)
    liquidity = liquidity_analysis(holdings)
    overlap = overlap_analysis(holdings, corr_tickers, matrix)
    violations = limit_violations(holdings, conc, totals)
    sizing = position_sizing(holdings, contributions, corr_tickers, matrix, technicals)

    step("stress testing")
    weights = {h["Ticker"]: (h.get("Exposure") or 0.0) for h in holdings}
    port_returns = pr.portfolio_return_series(closes, weights)
    stress = stress_test(port_returns, closes, weights, portfolio_value, exposure_value)
    scen = scenarios(holdings, portfolio_value)

    div = diversification_score(conc, corr, holdings)
    risk = risk_score(holdings, conc, corr, totals, liquidity, stress, violations)
    health = health_score(div, conc, corr, stress, liquidity, holdings, technicals, totals)
    trades = rebalancing(sizing, holdings, conc, violations, totals, portfolio_value)
    lights = traffic_lights(conc, corr, div, risk, stress, violations, totals, liquidity)
    summary = executive_summary(holdings, conc, corr, health, risk, stress,
                                violations, overlap, sizing, totals)

    data_notes = []
    if failed:
        data_notes.append(f'no price history: {", ".join(sorted(failed))} — '
                          f"excluded from beta, correlation and stress math")
    if profile_errors:
        data_notes.append(f"profile fetch failed for {len(profile_errors)} name(s): "
                          + "; ".join(profile_errors[:4]))
    unpriced = [h["Ticker"] for h in holdings if h.get("Price") is None]
    if unpriced:
        data_notes.append(f'no current price: {", ".join(unpriced)} — '
                          f"their exposure is understated")

    report = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "as_of": str(closes.index[-1].date()) if len(closes.index) else None,
        "limits": LIMITS,
        "totals": totals,
        "holdings": holdings,
        "technicals": technicals,
        "options": option_rows,
        "risk_free_rate": round(rate * 100, 2),
        "concentration": conc,
        "risk_contributions": contributions,
        "correlation": {"tickers": corr_tickers, "matrix": matrix, **corr},
        "overlap": overlap,
        "violations": violations,
        "liquidity": liquidity,
        "sizing": sizing,
        "diversification": div,
        "risk": risk,
        "health": health,
        "scenarios": scen,
        "stress": stress,
        "rebalancing": trades,
        "watchlist": watchlist_gaps(conc, corr),
        "traffic_lights": lights,
        "summary": summary,
        "unavailable_fields": [{"field": f, "reason": r}
                               for f, r in pr.UNAVAILABLE_FIELDS],
        "data_notes": data_notes,
        "etf_lookthrough_as_of": pr.ETF_LOOKTHROUGH_AS_OF,
    }
    report = pr.jsonable(report)

    if write_cache:
        pr.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        pr.CACHE_PATH.write_text(json.dumps(report))
    step("done")
    return report


def load_cached() -> dict | None:
    """Last saved report, or None. A corrupt cache reads as "no report yet"
    rather than breaking the Portfolio page."""
    if not pr.CACHE_PATH.exists():
        return None
    try:
        return json.loads(pr.CACHE_PATH.read_text())
    except (ValueError, OSError):
        return None


if __name__ == "__main__":                            # pragma: no cover
    out = analyze_portfolio(progress_cb=lambda s, d=None, t=None: print(f"  {s}"))
    print(json.dumps(out["summary"], indent=2))
    print(json.dumps(out["health"], indent=2))
