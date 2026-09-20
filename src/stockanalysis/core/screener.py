"""
screener.py
============
The query engine behind the Screener page — turns a tree of user conditions
into a filtered, scored, explained list of tickers from the research library.

Pure functions over row dicts, no I/O and no network: the caller loads
research_index.json (see webapp/api.screen()) and hands the rows in, exactly
like company_scores/conviction/buy_zone. That keeps the whole engine unit
testable against synthetic rows (tests/test_screener.py).

Three pieces:

    FIELDS          the registry — every screenable field, its type, unit,
                    where it lives on a row, and how to score partial
                    matches. The UI's filter menu, the natural-language
                    parser and the evaluator all read from this one table,
                    so adding a field means adding one entry here and it
                    shows up in all three.

    evaluate()      applies a condition tree (nestable AND/OR/NOT groups)
                    and returns matches with per-condition detail — which
                    conditions passed, the actual value each saw, and what
                    was missing. The per-condition detail is the point: it
                    is what the result cards use to explain *why* a ticker
                    matched, so filtering and explaining can't drift apart.

    parse_query()   deterministic natural language -> conditions.

On missing data: a row with no value for a field FAILS the condition (it
cannot be shown to satisfy it) but is counted separately in
`ScreenResult.missing_counts`. The UI reports that count per condition —
"Breakout % — 258 of 558 rows have no data for this field" — because a
filter that silently drops half the universe for lack of coverage looks
identical to one that legitimately excluded it. Coverage in the library is
uneven (Breakout_Probability ~54%, RR_T2 ~42%, technicals ~97%), so this is
the common case, not an edge case.

Why no LLM for the natural-language box: every scoring engine in this
project is deterministic (core/ai_sentiment.py, core/earnings_sentiment.py
are both "AI" features with zero model calls), the app has no API key wired
up, and a screener that returns different filters for the same words on two
runs is worse than one that returns none. The parser below matches phrases
to the same FIELDS registry the buttons use, so anything it produces is a
rule the user can see and edit as a pill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Iterable

# ─────────────────────────────────────────────────────────────────────────────
# FIELD REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
# Rows reaching this module are research-index entries enriched by
# build_universe(): curated top-level keys (ticker, sector, conv_overall...)
# plus the full scan row under "raw", plus the derived scores that the
# Research page also computes at render time (quality/moat/health/buy zone).
# `src` below is the key on the enriched row — build_universe() flattens
# everything into one namespace so the registry stays a flat table.

NUM, BOOL, ENUM = "num", "bool", "enum"
# LIST: the row holds several values at once (a ticker sits on many
# watchlists), so the test is membership rather than equality.
LIST = "list"

# Higher-is-better (1) or lower-is-better (-1). Used by match scoring to
# decide which direction "exceeds the threshold" means, and by the weighted
# composite. `None` = no natural direction (enums, bools).
UP, DOWN = 1, -1


@dataclass(frozen=True)
class Field:
    key: str                    # stable id used in saved searches + the API
    label: str                  # what the UI shows
    group: str                  # menu section
    kind: str                   # NUM / BOOL / ENUM
    src: str                    # key on the enriched row
    unit: str = ""              # "%", "x", "$"
    direction: int | None = None
    values: tuple[str, ...] = ()  # ENUM only
    hint: str = ""              # one-liner shown in the picker
    decimals: int = 1

    def format(self, v: Any) -> str:
        if v is None:
            return "—"
        if self.kind == BOOL:
            return "Yes" if v else "No"
        if self.kind == ENUM:
            return str(v)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        if self.unit == "$":
            return _fmt_big_dollar(f)
        return f"{f:,.{self.decimals}f}{self.unit}"


def _fmt_big_dollar(v: float) -> str:
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= cut:
            return f"${v / cut:,.1f}{suffix}"
    return f"${v:,.0f}"


# The `Category` column carries only these five values (Avoid, Momentum,
# Momentum-Pullback, VCP Setup, Turnaround) — the other "categories" people
# ask for (Buy Zone, Long Term, Breakout, Oversold, Recovery) are NOT values
# of that column, they live in other fields. They're exposed below as their
# own boolean fields rather than as fake Category values, so a rule always
# means what it says.
CATEGORY_VALUES = ("Momentum", "Momentum-Pullback", "VCP Setup",
                   "Turnaround", "Avoid")
GRADE_VALUES = ("A", "B", "C", "X")
BUY_ZONE_VALUES = ("Buy Zone", "Watch List", "Hold / Monitor", "Avoid")
MOAT_VALUES = ("Strong signals", "Moderate signals", "Weak signals")
ACTION_VALUES = ("BUY", "WATCH", "AVOID", "HOLD")
TREND_VALUES = ("Strong", "Trending", "Ranging")

FIELDS: tuple[Field, ...] = (
    # ── Technical ───────────────────────────────────────────────────────────
    Field("pct_vs_8ema", "Distance from 8 EMA", "Technical", NUM,
          "pct_vs_8ema", "%", None, hint="Signed % above/below the 8 EMA",
          decimals=2),
    Field("abs_vs_8ema", "Price near 8 EMA", "Technical", NUM,
          "abs_vs_8ema", "%", DOWN, hint="Absolute distance — use with 'within'",
          decimals=2),
    Field("pct_vs_21ema", "Distance from 21 EMA", "Technical", NUM,
          "pct_vs_21ema", "%", None, decimals=2),
    Field("abs_vs_21ema", "Price near 21 EMA", "Technical", NUM,
          "abs_vs_21ema", "%", DOWN, decimals=2),
    Field("pct_vs_50ma", "Distance from 50 MA", "Technical", NUM,
          "pct_vs_50ma", "%", None, decimals=2),
    Field("abs_vs_50ma", "Price near 50 MA", "Technical", NUM,
          "abs_vs_50ma", "%", DOWN, decimals=2),
    Field("pct_vs_200ma", "Distance from 200 MA", "Technical", NUM,
          "pct_vs_200ma", "%", None, decimals=2),
    Field("abs_vs_200ma", "Price near 200 MA", "Technical", NUM,
          "abs_vs_200ma", "%", DOWN, decimals=2),
    Field("above_50ma", "Above 50 MA", "Technical", BOOL, "above_50ma"),
    Field("above_200ma", "Above 200 MA", "Technical", BOOL, "above_200ma"),
    Field("above_vwap", "Above VWAP", "Technical", BOOL, "above_vwap"),
    Field("golden_cross", "Golden Cross", "Technical", BOOL, "golden_cross",
          hint="50 MA above 200 MA"),
    Field("death_cross", "Death Cross", "Technical", BOOL, "death_cross",
          hint="50 MA below 200 MA"),
    Field("rs_rank", "Relative Strength (RS Rank)", "Technical", NUM,
          "rs_rank", "", UP, hint="0-100 percentile vs the scan universe",
          decimals=0),
    Field("rs", "RS vs benchmark", "Technical", NUM, "rs", "", UP, decimals=1),
    Field("swing_score", "Swing Score", "Technical", NUM, "swing_score", "",
          UP, decimals=0),
    Field("daytrade_score", "Day Score", "Technical", NUM, "daytrade_score",
          "", UP, decimals=0),
    Field("breakout_probability", "Breakout %", "Technical", NUM,
          "breakout_probability", "%", UP, decimals=0),
    Field("bounce_probability", "Bounce %", "Technical", NUM,
          "bounce_probability", "%", UP, decimals=0),
    Field("rvol", "Volume Surge (RVOL)", "Technical", NUM, "rvol", "x", UP,
          decimals=2),
    Field("atr_pct", "ATR %", "Technical", NUM, "atr_pct", "%", None,
          decimals=2),
    Field("rsi", "RSI (14)", "Technical", NUM, "rsi", "", None, decimals=1),
    Field("adx", "ADX (14)", "Technical", NUM, "adx", "", UP, decimals=1),
    Field("dist_to_support", "Near Support", "Technical", NUM,
          "dist_to_support", "%", DOWN, hint="Distance to nearest support",
          decimals=2),
    Field("dist_to_resistance", "Near Resistance", "Technical", NUM,
          "dist_to_resistance", "%", DOWN, decimals=2),
    Field("trend_strength", "Trend Strength", "Technical", ENUM,
          "trend_strength", values=TREND_VALUES),
    Field("price", "Price", "Technical", NUM, "price", "", None, decimals=2),

    # ── Fundamentals ────────────────────────────────────────────────────────
    Field("quality", "Quality Score", "Fundamentals", NUM, "quality", "", UP,
          hint="Business quality 0-100 (margins, returns, growth, leverage)",
          decimals=0),
    Field("health", "Health Score", "Fundamentals", NUM, "health", "", UP,
          hint="Balance-sheet strength 0-100", decimals=0),
    Field("moat", "Moat (checks passed)", "Fundamentals", NUM, "moat", "", UP,
          hint="0-4 elite-economics checks passed", decimals=0),
    Field("moat_label", "Moat rating", "Fundamentals", ENUM, "moat_label",
          values=MOAT_VALUES),
    Field("eps_growth", "EPS Growth", "Fundamentals", NUM, "eps_growth", "%",
          UP, decimals=1),
    Field("revenue_growth", "Revenue Growth", "Fundamentals", NUM,
          "revenue_growth", "%", UP, decimals=1),
    Field("forward_pe", "Forward P/E", "Fundamentals", NUM, "forward_pe", "",
          DOWN, decimals=1),
    Field("peg_ratio", "PEG Ratio", "Fundamentals", NUM, "peg_ratio", "",
          DOWN, decimals=2),
    Field("inst_own", "Institutional Ownership", "Fundamentals", NUM,
          "inst_own", "%", UP, decimals=1),
    Field("inst_own_chg", "Institutional Own. Change", "Fundamentals", NUM,
          "inst_own_chg", "%", UP, decimals=2),
    Field("market_cap", "Market Cap", "Fundamentals", NUM, "market_cap", "$",
          UP, decimals=0),
    Field("canslim", "CANSLIM Pass", "Fundamentals", BOOL, "canslim"),
    Field("gross_margin", "Gross Margin", "Fundamentals", NUM, "gross_margin",
          "%", UP, decimals=1),
    Field("operating_margin", "Operating Margin", "Fundamentals", NUM,
          "operating_margin", "%", UP, decimals=1),
    Field("roe", "Return on Equity", "Fundamentals", NUM, "roe", "%", UP,
          decimals=1),
    Field("fcf_margin", "FCF Margin", "Fundamentals", NUM, "fcf_margin", "%",
          UP, decimals=1),
    Field("debt_to_equity", "Debt / Equity", "Fundamentals", NUM,
          "debt_to_equity", "", DOWN, decimals=1),
    Field("current_ratio", "Current Ratio", "Fundamentals", NUM,
          "current_ratio", "", UP, decimals=2),
    Field("short_interest", "Short Interest", "Fundamentals", NUM,
          "short_interest", "%", None, decimals=1),
    Field("investment_score", "Investment Score", "Fundamentals", NUM,
          "investment_score", "", UP, decimals=0),
    # Decision-engine scores (core/decision_engine.py), computed here so they
    # can be screened and preset on like any other field. Distinct keys from
    # the scan's own investment_score/swing_score, which measure different
    # things — conflating them in the picker would be a trap.
    Field("decision_investment", "Long-term Score", "Fundamentals", NUM,
          "decision_investment", "", UP,
          hint="Decision engine: company quality 0-100", decimals=0),
    Field("decision_swing", "Swing Setup Score", "Technical", NUM,
          "decision_swing", "", UP,
          hint="Decision engine: setup quality 0-100", decimals=0),
    Field("decision_confluence", "Confluence", "Technical", NUM,
          "decision_confluence", "", UP,
          hint="Independent factors agreeing, 0-10", decimals=0),
    Field("conviction", "Conviction Score", "Fundamentals", NUM, "conviction",
          "", UP, hint="0-100 composite: quality + setup + timing",
          decimals=0),
    Field("conv_stars", "Conviction Stars", "Fundamentals", NUM, "conv_stars",
          "★", UP, decimals=0),
    Field("conv_action", "Conviction Action", "Fundamentals", ENUM,
          "conv_action", values=ACTION_VALUES),
    Field("sector", "Sector", "Fundamentals", ENUM, "sector"),
    Field("industry", "Industry", "Fundamentals", ENUM, "industry"),

    # ── Categories ──────────────────────────────────────────────────────────
    Field("category", "Category", "Categories", ENUM, "category",
          values=CATEGORY_VALUES,
          hint="The scan's setup classification"),
    Field("grade", "Grade", "Categories", ENUM, "grade", values=GRADE_VALUES),
    Field("buy_zone_label", "Buy Zone status", "Categories", ENUM,
          "buy_zone_label", values=BUY_ZONE_VALUES),
    Field("buy_zone_score", "Buy Zone Score", "Categories", NUM,
          "buy_zone_score", "", UP, decimals=0),
    Field("in_buy_zone", "In Buy Zone", "Categories", BOOL, "in_buy_zone",
          hint="Buy Zone status is 'Buy Zone'"),
    Field("is_turnaround", "Turnaround", "Categories", BOOL, "is_turnaround"),
    Field("is_momentum", "Momentum", "Categories", BOOL, "is_momentum"),
    Field("is_momentum_pullback", "Momentum Pullback", "Categories", BOOL,
          "is_momentum_pullback"),
    Field("is_vcp", "VCP", "Categories", BOOL, "is_vcp"),
    Field("is_long_term", "Long Term candidate", "Categories", BOOL,
          "is_long_term", hint="Passes the long-term investment screen"),
    Field("is_breakout", "Breakout", "Categories", BOOL, "is_breakout",
          hint="Within 3% of the 52-week high on above-average volume"),
    Field("is_oversold", "Oversold", "Categories", BOOL, "is_oversold",
          hint="RSI below 30"),
    Field("is_watchlist", "On a watchlist", "Categories", BOOL,
          "is_watchlist"),
    # Themes like "AI" have no field in the scan — the user's own watchlist
    # is what defines them in this app, so screen on that rather than
    # approximating with a sector and calling it AI.
    Field("watchlist", "On watchlist", "Categories", LIST, "watchlists",
          hint="Membership of a named watchlist, e.g. AI or Dividend"),

    # ── Options ─────────────────────────────────────────────────────────────
    Field("call_candidate", "Call Candidate", "Options", BOOL,
          "call_candidate"),
    Field("call_score", "Call Score", "Options", NUM, "call_score", "", UP,
          decimals=0),
    Field("put_candidate", "Put Candidate", "Options", BOOL, "put_candidate"),
    Field("put_score", "Put Score", "Options", NUM, "put_score", "", UP,
          decimals=0),
    Field("earnings_soon", "Earnings Soon", "Options", BOOL, "earnings_soon",
          hint="Reports within 7 days"),
    Field("days_to_earnings", "Days to Earnings", "Options", NUM,
          "days_to_earnings", "d", None, decimals=0),

    # ── Risk ────────────────────────────────────────────────────────────────
    Field("rr", "Risk : Reward", "Risk", NUM, "rr", "", UP,
          hint="R:R to target 2", decimals=2),
    Field("dist_52w_high", "Distance from 52W High", "Risk", NUM,
          "dist_52w_high", "%", UP, decimals=1),
    Field("dist_52w_low", "Distance from 52W Low", "Risk", NUM,
          "dist_52w_low", "%", None, decimals=1),
    Field("rank_score", "Position Rank", "Risk", NUM, "rank_score", "", UP,
          decimals=0),
    Field("key_level_score", "Key Level Score", "Risk", NUM,
          "key_level_score", "", UP, decimals=1),
    Field("entry_gate_pass", "Entry Gate Pass", "Risk", BOOL,
          "entry_gate_pass"),
)

FIELD_BY_KEY = {f.key: f for f in FIELDS}
FIELD_GROUPS = ("Technical", "Fundamentals", "Categories", "Options", "Risk")


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    """Coerce to float, treating the CSV's empty/N/A markers as missing."""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "N/A", "nan", "None", "-", "—"):
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # NaN survives float() and then fails every comparison silently — a NaN
    # row would look "present but never matching" instead of "no data".
    return None if f != f else f


def _b(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "yes", "1", "y"):
        return True
    if s in ("false", "no", "0", "n"):
        return False
    return None


def _s(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _abs(v: float | None) -> float | None:
    return None if v is None else abs(v)


def build_universe(index_rows: Iterable[dict],
                   watchlist_tickers: set[str] | None = None,
                   watchlist_map: dict[str, Iterable[str]] | None = None
                   ) -> list[dict]:
    """research_index.json entries -> flat rows this engine can screen.

    The derived scores (quality / moat / health / buy zone) are computed here
    rather than read from the index for the same reason research_page() does
    it at render time: they're pure functions of the raw scan fields, so
    computing them means every ticker has them regardless of when its
    research page was last written.
    """
    from stockanalysis.core import decision_engine as _de
    from stockanalysis.core.buy_zone import compute_buy_zone
    from stockanalysis.core.company_scores import (
        compute_business_quality, compute_economic_moat,
        compute_financial_health)

    # Invert name -> tickers once, so per-row membership is a dict lookup
    # rather than a scan of every list.
    lists_by_ticker: dict[str, list[str]] = {}
    for name, tickers in (watchlist_map or {}).items():
        for t in (tickers or []):
            lists_by_ticker.setdefault(str(t), []).append(name)
    watch = set(watchlist_tickers or ()) or set(lists_by_ticker)
    out: list[dict] = []
    for entry in index_rows:
        raw = entry.get("raw") or {}
        ticker = _s(entry.get("ticker")) or _s(raw.get("Ticker"))
        if not ticker:
            continue

        bq = compute_business_quality(raw)
        moat = compute_economic_moat(raw)
        fh = compute_financial_health(raw)
        bz = compute_buy_zone(raw)

        ma50, ma200 = _f(raw.get("50MA")), _f(raw.get("200MA"))
        rsi = _f(raw.get("RSI_14"))
        dist_52wh = _f(raw.get("Dist_52W_High%"))
        rvol = _f(raw.get("RVOL"))
        category = _s(entry.get("category")) or _s(raw.get("Category"))
        # The scan writes these as columns; fall back to computing them from
        # price and the level so a row that carries one but not the other
        # still screens (older rows predate some of these columns).
        price_raw = raw.get("Current Price")
        pct_8 = _first(_f(raw.get("Pct_vs_8EMA")),
                       _pct_from(price_raw, raw.get("8EMA")))
        pct_21 = _pct_from(price_raw, raw.get("21EMA"))
        pct_50 = _first(_f(raw.get("Price_vs_50MA%")),
                        _pct_from(price_raw, raw.get("50MA")))
        pct_200 = _first(_f(raw.get("Price_vs_200MA%")),
                         _pct_from(price_raw, raw.get("200MA")))
        bz_label = _s(bz.get("label")) or _s(raw.get("Buy_Zone_Label"))
        # Curated top-level key first, raw scan column as the fallback.
        # The two layers are filled by different writers — the research index
        # carries the curated keys, core.research_snapshot restores the raw
        # columns — so reading only one of them leaves whole filters blank
        # whenever the other layer is the one holding the value.
        def num(entry_key: str, raw_key: str) -> float | None:
            return _first(_f(entry.get(entry_key)), _f(raw.get(raw_key)))

        d2e = num("days_to_earnings", "Days_To_Earnings")

        row = {
            "ticker": ticker,
            "name": _s(raw.get("LongName")) or ticker,
            "sector": _s(entry.get("sector")) or _s(raw.get("Sector")),
            "industry": _s(raw.get("Industry")),
            "updated_at": _s(entry.get("updated_at")),
            # Set when core.research_snapshot supplied the row's fields
            # because the live index had none — the values are real but as
            # of `data_as_of`, not of the last refresh, and the UI says so
            # rather than presenting recovered data as current.
            "recovered": bool(entry.get("recovered_from_snapshot")),
            "data_as_of": (_s(entry.get("snapshot_seen_at"))
                           or _s(entry.get("updated_at"))),

            # technical
            "price": _first(_f(entry.get("price")), _f(price_raw)),
            "pct_vs_8ema": pct_8,
            "abs_vs_8ema": _abs(pct_8),
            "pct_vs_21ema": pct_21,
            "abs_vs_21ema": _abs(pct_21),
            "pct_vs_50ma": pct_50,
            "abs_vs_50ma": _abs(pct_50),
            "pct_vs_200ma": pct_200,
            "abs_vs_200ma": _abs(pct_200),
            # The levels themselves, not just the distance to them. A
            # decision that says "wait for a pullback" has to name the price
            # it is waiting for, and "-4.1% from here" is not an order you
            # can place. See decision_engine.pullback_plan.
            "ema8": _f(raw.get("8EMA")),
            "ema21": _f(raw.get("21EMA")),
            "ma50": ma50,
            "ma200": ma200,
            "high_52w": _f(raw.get("52W High")),
            "above_50ma": None if pct_50 is None else pct_50 > 0,
            "above_200ma": _b(raw.get("Above_200MA")),
            "above_vwap": _b(raw.get("Above_VWAP")),
            "golden_cross": None if (ma50 is None or ma200 is None) else ma50 > ma200,
            "death_cross": None if (ma50 is None or ma200 is None) else ma50 < ma200,
            "rs_rank": num("rs_rank", "RS_Rank"),
            "rs": _f(raw.get("RS")),
            "swing_score": num("swing_score", "Swing_Score"),
            "daytrade_score": num("daytrade_score", "DayTrade_Score"),
            "breakout_probability": num("breakout_probability",
                                        "Breakout_Probability"),
            "bounce_probability": num("bounce_probability",
                                      "Bounce_Probability"),
            "rvol": rvol,
            "atr_pct": _f(raw.get("ATR_Pct")),
            "rsi": rsi,
            "adx": _f(raw.get("ADX_14")),
            "dist_to_support": num("dist_to_support_pct", "Dist_to_Support%"),
            "dist_to_resistance": num("dist_to_resistance_pct",
                                      "Dist_to_Resistance%"),
            "trend_strength": _s(raw.get("Trend_Strength")),

            # fundamentals
            "quality": bq.get("score"),
            "quality_label": bq.get("label"),
            "quality_drivers": bq.get("drivers") or [],
            "health": fh.get("score"),
            "health_label": fh.get("label"),
            "moat": moat.get("passed") if moat.get("label") else None,
            "moat_total": moat.get("total"),
            "moat_label": moat.get("label"),
            "eps_growth": num("eps_growth", "EPS_Growth%"),
            "revenue_growth": _f(raw.get("Revenue")),
            "forward_pe": num("forward_pe", "Forward_PE"),
            "peg_ratio": num("peg_ratio", "PEG_Ratio"),
            "inst_own": num("inst_own_pct", "Inst_Own%"),
            "inst_own_chg": num("inst_own_chg", "Inst_Own_Chg"),
            "market_cap": num("market_cap", "MarketCap"),
            "canslim": _first_b(entry.get("canslim_pass"),
                                raw.get("CANSLIM_Pass")),
            "gross_margin": _f(raw.get("GrossMargin%")),
            "operating_margin": _f(raw.get("OperatingMargin%")),
            "roe": _f(raw.get("ReturnOnEquity%")),
            "fcf_margin": _f(raw.get("FCF_Margin%")),
            "debt_to_equity": _f(raw.get("DebtToEquity")),
            "current_ratio": _f(raw.get("CurrentRatio")),
            "short_interest": _f(raw.get("Short_Interest%")),
            "investment_score": num("investment_score", "Investment_Score"),
            "conviction": num("conv_overall", "Conv_Overall"),
            "conv_stars": num("conv_stars", "Conv_Stars"),
            "conv_action": _s(entry.get("conv_action")) or _s(raw.get("Conv_Action")),

            # categories
            "category": category,
            "grade": _s(entry.get("grade")) or _s(raw.get("Grade")),
            "buy_zone_label": bz_label,
            "buy_zone_score": _f(bz.get("score")),
            # Both buy-zone tiers, not just the middle one. Testing == "Buy
            # Zone" excluded "Strong Buy Zone" — the better category — so
            # the Near Buy Zone preset and the engine's BUY ZONE action
            # silently skipped the best-scoring entries.
            "in_buy_zone": (None if bz_label is None
                            else bz_label in ("Buy Zone", "Strong Buy Zone")),
            "is_turnaround": None if category is None else category == "Turnaround",
            "is_momentum": None if category is None else category == "Momentum",
            "is_momentum_pullback": (None if category is None
                                     else category == "Momentum-Pullback"),
            "is_vcp": None if category is None else category == "VCP Setup",
            "is_long_term": _b(raw.get("Investment_Pass")),
            "is_oversold": None if rsi is None else rsi < 30,
            "is_watchlist": ticker in watch,
            "watchlists": sorted(lists_by_ticker.get(ticker, ())),

            # options
            "call_candidate": _b(raw.get("Call_Candidate")),
            "call_score": _f(entry.get("call_score")),
            "put_candidate": _b(raw.get("Put_Candidate")),
            "put_score": _f(entry.get("put_score")),
            "days_to_earnings": d2e,
            "earnings_soon": None if d2e is None else 0 <= d2e <= 7,

            # risk
            "rr": _f(raw.get("RR_T2")),
            "dist_52w_high": dist_52wh,
            "dist_52w_low": _f(entry.get("dist_from_52w_low_pct")),
            "rank_score": _f(raw.get("Rank_Score")),
            "key_level_score": _f(entry.get("key_level_score")),
            "entry_gate_pass": _b(raw.get("Entry_Gate_Pass")),
        }
        # Decision scores need the row assembled first — they read the same
        # normalised fields everything else does.
        # Published only when enough of the inputs were measurable. An ETF
        # has no margins, moat or EPS, so its investment score comes off RS
        # and the 200MA alone — CIBR scored 95 from 10% coverage, which is
        # the exact failure MIN_COVERAGE exists to stop. Below the bar the
        # field reads as missing data, which the screener already reports,
        # rather than as a number that outranks a fully-measured company.
        try:
            inv = _de.investment_score(row)
            swing = _de.swing_score(row)
            row["decision_investment"] = (
                inv["score"] if inv["coverage"] >= _de.MIN_COVERAGE else None)
            row["decision_swing"] = (
                swing["score"] if swing["coverage"] >= _de.MIN_COVERAGE else None)
            row["decision_confluence"] = _de.confluence(row)["score"]
        except Exception:            # never let scoring break the universe
            row["decision_investment"] = None
            row["decision_swing"] = None
            row["decision_confluence"] = None
        # Breakout needs both inputs; with either missing the answer is
        # "unknown", not False — otherwise a data gap reads as a rejection.
        row["is_breakout"] = (None if (dist_52wh is None or rvol is None)
                              else dist_52wh >= -3.0 and rvol >= 1.2)
        out.append(row)
    return out


def _first(*vals: float | None) -> float | None:
    """First value that is actually present — not `or`, which would discard
    a legitimate 0.0 (dead-on the moving average)."""
    for v in vals:
        if v is not None:
            return v
    return None


def _first_b(*vals: Any) -> bool | None:
    """Boolean counterpart to _first — the first value that parses as a
    bool, so False from the live layer still beats True from the fallback."""
    for v in vals:
        b = _b(v)
        if b is not None:
            return b
    return None


def _pct_from(price: Any, level: Any) -> float | None:
    p, lv = _f(price), _f(level)
    if p is None or lv is None or lv == 0:
        return None
    return (p - lv) / lv * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────

OPERATORS: dict[str, str] = {
    "gt": ">", "gte": "≥", "lt": "<", "lte": "≤",
    "eq": "is", "ne": "is not",
    "within": "is within", "between": "is between",
    "in": "is any of", "has": "includes",
}

# Which operators the picker offers per field type.
OPS_FOR_KIND = {
    NUM: ("gt", "gte", "lt", "lte", "eq", "ne", "between", "within"),
    BOOL: ("eq",),
    ENUM: ("eq", "ne", "in"),
    LIST: ("has",),
}


@dataclass
class Condition:
    """One rule: <field> <op> <value>. `negate` is the NOT."""
    field: str
    op: str = "gte"
    value: Any = None
    value2: Any = None          # `between` upper bound / `within` reference
    negate: bool = False
    weight: float = 1.0         # relative importance for the composite score

    def spec(self) -> Field | None:
        return FIELD_BY_KEY.get(self.field)


@dataclass
class Group:
    """A nestable AND/OR of conditions and sub-groups."""
    op: str = "AND"                                   # AND | OR
    items: list = dc_field(default_factory=list)      # Condition | Group
    negate: bool = False


# `within` is the one operator that isn't a plain comparison: "Price is
# within 1% of the 8 EMA" reads as a rule about the *price*, but evaluates
# against the precomputed absolute-distance field. WITHIN_FIELDS maps the
# reference the user picks to that field so the pill can say "Price within
# 1% of 8 EMA" while the engine compares one number.
WITHIN_FIELDS = {
    "8ema": ("abs_vs_8ema", "8 EMA"),
    "21ema": ("abs_vs_21ema", "21 EMA"),
    "50ma": ("abs_vs_50ma", "50 MA"),
    "200ma": ("abs_vs_200ma", "200 MA"),
    "support": ("dist_to_support", "support"),
    "resistance": ("dist_to_resistance", "resistance"),
}


@dataclass
class CondResult:
    """Outcome of one condition against one row — the raw material for both
    filtering and the "why it matched" card."""
    field: str
    label: str
    passed: bool
    missing: bool
    actual: Any
    text: str                   # "Quality 100 (> 95)"
    margin: float               # 0-1, how comfortably it passed


def _compare(op: str, actual: float, value: float,
             value2: float | None) -> bool:
    if op == "gt":
        return actual > value
    if op == "gte":
        return actual >= value
    if op == "lt":
        return actual < value
    if op == "lte":
        return actual <= value
    if op == "eq":
        return actual == value
    if op == "ne":
        return actual != value
    if op == "within":
        return abs(actual) <= abs(value)
    if op == "between":
        lo, hi = sorted((value, value2 if value2 is not None else value))
        return lo <= actual <= hi
    return False


def _margin(op: str, actual: float, value: float, spec: Field) -> float:
    """How well the row clears the bar, 0-1. Used to rank matches so a
    Quality-100 row outranks a Quality-96 row on `quality > 95` instead of
    both counting as a flat pass."""
    if op in ("eq", "ne"):
        return 1.0
    if value in (0, None):
        return 1.0
    if op == "within":
        # Closer to the reference is better: dead on = 1.0, at the edge = 0.
        return max(0.0, 1.0 - abs(actual) / abs(value)) if value else 1.0
    span = abs(value) if value else 1.0
    delta = (actual - value) if op in ("gt", "gte") else (value - actual)
    return max(0.0, min(1.0, delta / span))


def eval_condition(row: dict, cond: Condition) -> CondResult:
    spec = cond.spec()
    if spec is None:
        return CondResult(cond.field, cond.field, False, True, None,
                          f"unknown field '{cond.field}'", 0.0)

    actual = row.get(spec.src)
    label = spec.label

    if actual is None:
        return CondResult(cond.field, label, False, True, None,
                          f"{label} — no data", 0.0)

    if spec.kind == LIST:
        # An empty list is a real answer ("on no watchlists"), not missing
        # data — the row was checked and simply isn't a member.
        members = actual if isinstance(actual, (list, tuple, set)) else []
        wanted = str(cond.value)
        passed = wanted in {str(m) for m in members}
        shown = ", ".join(str(m) for m in members) or "none"
        result = CondResult(cond.field, label, passed, False, list(members),
                            f"{label}: {shown}", 1.0)
    elif spec.kind == BOOL:
        want = _b(cond.value)
        want = True if want is None else want
        passed = bool(actual) is want
        text = f"{label} = {'Yes' if actual else 'No'}"
        result = CondResult(cond.field, label, passed, False, bool(actual),
                            text, 1.0)
    elif spec.kind == ENUM:
        av = str(actual)
        if cond.op == "in":
            wanted = cond.value if isinstance(cond.value, (list, tuple)) else [cond.value]
            wanted = [str(w) for w in wanted]
            passed = av in wanted
            shown = ", ".join(wanted)
        else:
            shown = str(cond.value)
            passed = (av == shown) if cond.op == "eq" else (av != shown)
        text = f"{label} = {av}"
        result = CondResult(cond.field, label, passed, False, av, text, 1.0)
    else:
        a = _f(actual)
        v = _f(cond.value)
        v2 = _f(cond.value2)
        if a is None:
            return CondResult(cond.field, label, False, True, None,
                              f"{label} — no data", 0.0)
        if v is None:
            return CondResult(cond.field, label, False, True, a,
                              f"{label} — no threshold set", 0.0)
        passed = _compare(cond.op, a, v, v2)
        sym = OPERATORS.get(cond.op, cond.op)
        if cond.op == "between":
            bound = f"{sym} {spec.format(v)}–{spec.format(v2)}"
        elif cond.op == "within":
            bound = f"{sym} {spec.format(v)}"
        else:
            bound = f"{sym} {spec.format(v)}"
        text = f"{label} {spec.format(a)} ({bound})"
        result = CondResult(cond.field, label, passed, False, a, text,
                            _margin(cond.op, a, v, spec))

    if cond.negate:
        # A NOT over a missing value stays a fail: "not X" can't be shown
        # true for a row where X is unknown.
        if not result.missing:
            result = CondResult(result.field, result.label, not result.passed,
                                False, result.actual,
                                f"NOT {result.text}", result.margin)
    return result


def eval_group(row: dict, group: Group,
               collect: list[CondResult] | None = None) -> bool:
    results = []
    for item in group.items:
        if isinstance(item, Group):
            results.append(eval_group(row, item, collect))
        else:
            r = eval_condition(row, item)
            if collect is not None:
                collect.append(r)
            results.append(r.passed)
    if not results:
        return True                       # an empty screen matches everything
    ok = all(results) if group.op == "AND" else any(results)
    return not ok if group.negate else ok


# ─────────────────────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────────────────────

def match_score(results: list[CondResult],
                weights: dict[str, float] | None = None) -> int:
    """0-100: how completely a row satisfies the screen.

    A pass contributes its full weight scaled by how far past the threshold
    it sits (70% for clearing the bar at all, 30% for the margin), so ties
    on "passed everything" break toward the row that passed by the most.
    """
    if not results:
        return 0
    total = hit = 0.0
    for r in results:
        w = (weights or {}).get(r.field, 1.0)
        total += w
        if r.passed:
            hit += w * (0.7 + 0.3 * max(0.0, min(1.0, r.margin)))
    return int(round(100 * hit / total)) if total else 0


# The composite ranks by *fields*, not by conditions: it answers "of the
# stocks that matched, which is best on the things I care about" — so it
# works even when the screen has no condition on a weighted field.
COMPOSITE_DEFAULTS = {"quality": 40, "rs_rank": 30, "eps_growth": 20,
                      "forward_pe": 10}


def composite_score(row: dict, weights: dict[str, float]) -> int | None:
    """Weighted 0-100 blend of normalized field values."""
    total = acc = 0.0
    for key, w in weights.items():
        spec = FIELD_BY_KEY.get(key)
        if spec is None or not w:
            continue
        v = _f(row.get(spec.src))
        if v is None:
            continue
        norm = _normalize(key, v)
        if norm is None:
            continue
        if spec.direction == DOWN:
            norm = 100.0 - norm
        acc += w * norm
        total += w
    return int(round(acc / total)) if total else None


# Ranges used to map a raw value onto 0-100 for the composite. Anything not
# listed is assumed already 0-100 and just clamped.
_NORM_RANGE = {
    "eps_growth": (0.0, 100.0), "revenue_growth": (0.0, 60.0),
    "forward_pe": (5.0, 60.0), "peg_ratio": (0.0, 4.0),
    "rr": (0.0, 5.0), "rvol": (0.0, 4.0), "moat": (0.0, 4.0),
    "conv_stars": (0.0, 5.0), "inst_own": (0.0, 100.0),
    "gross_margin": (0.0, 90.0), "operating_margin": (0.0, 50.0),
    "roe": (0.0, 50.0), "fcf_margin": (0.0, 40.0),
    "debt_to_equity": (0.0, 200.0), "current_ratio": (0.0, 3.0),
    "market_cap": (1e9, 3e12),
}


def _normalize(key: str, v: float) -> float | None:
    lo, hi = _NORM_RANGE.get(key, (0.0, 100.0))
    if hi == lo:
        return None
    return max(0.0, min(100.0, (v - lo) / (hi - lo) * 100.0))


# ─────────────────────────────────────────────────────────────────────────────
# SCREENING
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScreenResult:
    matches: list[dict]
    total: int                          # universe size
    missing_counts: dict[str, int]      # field key -> rows with no data
    summary: dict
    stats: list[dict] = dc_field(default_factory=list)   # per-condition


def condition_stats(rows: list[dict], group: Group) -> list[dict]:
    """How each condition performs on its own, and what the screen would
    return without it.

    This is what makes an empty result readable. "0 matches" tells you
    nothing; "Quality > 80 alone matches 2 — drop it and you get 6" points
    straight at the binding constraint. `alone` and `without` differ in a
    useful way: a rule can look permissive on its own and still be the one
    killing the screen, because of how it overlaps the others.
    """
    conds = _walk(group)
    if not conds:
        return []
    out = []
    for i, cond in enumerate(conds):
        spec = cond.spec()
        alone = missing = 0
        for row in rows:
            r = eval_condition(row, cond)
            alone += r.passed
            missing += r.missing
        # Same tree with this one condition dropped.
        trimmed = _drop(group, cond)
        without = sum(1 for row in rows if eval_group(row, trimmed))
        out.append({
            "index": i,
            "field": cond.field,
            "label": spec.label if spec else cond.field,
            "text": describe(cond),
            "alone": alone,
            "missing": missing,
            "without": without,
        })
    return out


def _drop(group: Group, target: Condition) -> Group:
    """Copy of `group` with `target` removed (identity, not equality — two
    rules can be value-identical and only one should go)."""
    items = []
    for item in group.items:
        if isinstance(item, Group):
            items.append(_drop(item, target))
        elif item is not target:
            items.append(item)
    return Group(group.op, items, group.negate)


def screen(rows: list[dict], group: Group,
           weights: dict[str, float] | None = None,
           composite_weights: dict[str, float] | None = None,
           sort: str = "match", limit: int | None = None,
           with_stats: bool = True) -> ScreenResult:
    """Apply `group` to `rows` and return scored, explained matches."""
    matches: list[dict] = []
    missing: dict[str, int] = {}

    for row in rows:
        collected: list[CondResult] = []
        ok = eval_group(row, group, collected)
        for r in collected:
            if r.missing:
                missing[r.field] = missing.get(r.field, 0) + 1
        if not ok:
            continue
        hit = dict(row)
        hit["match_score"] = match_score(collected, weights)
        hit["composite"] = composite_score(
            row, composite_weights or COMPOSITE_DEFAULTS)
        hit["why"] = [{"label": r.label, "text": r.text, "passed": r.passed,
                       "field": r.field, "actual": r.actual}
                      for r in collected]
        # Fields the screen actually constrains — the result table shows
        # these as columns so you always see the numbers you filtered on.
        hit["matched_fields"] = [r.field for r in collected if r.passed]
        matches.append(hit)

    matches.sort(key=_sort_key(sort), reverse=_sort_desc(sort))
    total_matched = len(matches)
    if limit:
        matches = matches[:limit]
    summary = summarize(matches)
    summary["count"] = total_matched          # count the screen, not the page
    stats = condition_stats(rows, group) if with_stats else []
    return ScreenResult(matches, len(rows), missing, summary, stats)


_SORT_FIELDS = {"match": "match_score", "composite": "composite",
                "quality": "quality", "rs": "rs_rank",
                "conviction": "conviction", "eps": "eps_growth",
                "breakout": "breakout_probability", "ticker": "ticker",
                "market_cap": "market_cap", "price": "price"}


def _sort_key(sort: str) -> Callable[[dict], Any]:
    key = _SORT_FIELDS.get(sort, "match_score")
    if key == "ticker":
        return lambda r: r.get("ticker") or ""
    # None sorts last under a descending sort, which is what you want for
    # "best first" — a row with no value isn't the best, it's unknown.
    return lambda r: (r.get(key) is not None, r.get(key) or 0)


def _sort_desc(sort: str) -> bool:
    return sort != "ticker"


def summarize(matches: list[dict]) -> dict:
    """The stats strip above the results."""
    def avg(key: str) -> float | None:
        vals = [_f(m.get(key)) for m in matches]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def count(key: str) -> int:
        return sum(1 for m in matches if m.get(key) is True)

    return {
        "count": len(matches),
        "avg_quality": avg("quality"),
        "avg_health": avg("health"),
        "avg_eps_growth": avg("eps_growth"),
        "avg_rs": avg("rs_rank"),
        "avg_conviction": avg("conviction"),
        "avg_breakout": avg("breakout_probability"),
        "avg_match": avg("match_score"),
        "above_200ma": count("above_200ma"),
        "in_buy_zone": count("in_buy_zone"),
        # Broken out by label: "in buy zone" alone hides how many are one
        # notch away, which is the more useful number when the strict count
        # is small (the label is deliberately selective — ~10% of the
        # library — so a single tile often reads 0).
        "strong_buy_zone": sum(1 for m in matches
                               if m.get("buy_zone_label") == "Strong Buy Zone"),
        "watch_list": sum(1 for m in matches
                          if m.get("buy_zone_label") == "Watch List"),
        "canslim": count("canslim"),
        "earnings_soon": count("earnings_soon"),
        "recovered": count("recovered"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PRESETS
# ─────────────────────────────────────────────────────────────────────────────

def _c(field: str, op: str, value: Any, value2: Any = None) -> Condition:
    return Condition(field=field, op=op, value=value, value2=value2)


# Thresholds are calibrated against the live library's distributions, not
# picked for how they read. Several round numbers are unusable here: the
# conviction composite tops out in the high 70s, so "conviction > 80" is an
# empty screen by construction, and Conv_Stars never reaches 5. Institutional
# ownership has a median near 90%, so ">70%" is not a filter at all.
#
# Valuation floors matter as much as ceilings: Forward P/E goes negative for
# loss-making companies, so a bare "P/E < 25" quietly admits every company
# with no earnings — the opposite of a value screen. Those use `between`.
#
# PRESET_GROUPS orders the sections in the UI.
PRESET_GROUPS = ("Momentum", "Quality", "Value", "Setups", "Ownership",
                 "Contrarian", "Themes", "Events", "Risk")

PRESETS: tuple[dict, ...] = (
    # ── Momentum ────────────────────────────────────────────────────────────
    {"key": "todays_breakouts", "icon": "🚀", "name": "Today's Breakouts",
     "group": "Momentum",
     "desc": "High breakout probability firing on above-average volume",
     "conditions": [_c("breakout_probability", "gte", 80),
                    _c("rvol", "gte", 1.2), _c("above_200ma", "eq", True)]},
    {"key": "high_rs", "icon": "🔥", "name": "High RS",
     "group": "Momentum", "desc": "Top-decile relative strength, trend intact",
     "conditions": [_c("rs_rank", "gt", 85), _c("above_200ma", "eq", True)]},
    {"key": "near_ath", "icon": "🏔️", "name": "Near ATH",
     "group": "Momentum", "desc": "Within 3% of the 52-week high",
     "conditions": [_c("dist_52w_high", "gte", -3),
                    _c("above_200ma", "eq", True)]},
    {"key": "momentum_leaders", "icon": "📈", "name": "Momentum Leaders",
     "group": "Momentum", "desc": "Momentum setups with the tape agreeing",
     "conditions": [_c("is_momentum", "eq", True), _c("rs_rank", "gt", 70)]},
    {"key": "momentum_pullback", "icon": "🎢", "name": "Momentum Pullback",
     "group": "Momentum",
     "desc": "Uptrends resting on the 8 EMA rather than breaking down",
     "conditions": [_c("is_momentum_pullback", "eq", True),
                    _c("abs_vs_8ema", "within", 3)]},
    {"key": "volume_surge", "icon": "📢", "name": "Volume Surge",
     "group": "Momentum", "desc": "Unusual volume behind an uptrend",
     "conditions": [_c("rvol", "gte", 1.5), _c("above_200ma", "eq", True)]},
    {"key": "golden_cross", "icon": "✨", "name": "Golden Cross",
     "group": "Momentum", "desc": "50 MA above the 200 MA with RS confirming",
     "conditions": [_c("golden_cross", "eq", True), _c("rs_rank", "gt", 60)]},
    {"key": "week52_high_club", "icon": "🎖️", "name": "52-Week High Club",
     "group": "Momentum", "desc": "Pressing the highs with leadership RS",
     "conditions": [_c("dist_52w_high", "gte", -5), _c("rs_rank", "gt", 70)]},

    # ── Quality ─────────────────────────────────────────────────────────────
    {"key": "longterm_ready", "icon": "🎓", "name": "Long-term Ready",
     "group": "Quality",
     "desc": "Decision engine rates the company 80+ — a business to own",
     "strategy": "LONGTERM",
     "conditions": [_c("decision_investment", "gt", 80)]},
    {"key": "swing_ready", "icon": "🏄", "name": "Swing Ready",
     "group": "Setups",
     "desc": "Decision engine rates the setup 80+ — a trade to take",
     "strategy": "SWING",
     "conditions": [_c("decision_swing", "gt", 80)]},
    {"key": "high_conviction", "icon": "⭐", "name": "High Conviction",
     "group": "Quality",
     "desc": "Best composite of company, setup and timing in the library",
     "conditions": [_c("conviction", "gte", 60), _c("conv_stars", "gte", 3)]},
    {"key": "high_moat", "icon": "🏰", "name": "High Moat",
     "group": "Quality", "desc": "Elite economics on 3+ of 4 moat checks",
     "conditions": [_c("moat", "gte", 3), _c("quality", "gt", 70)]},
    {"key": "high_eps", "icon": "💹", "name": "High EPS Growth",
     "group": "Quality", "desc": "Earnings compounding fast, margins to match",
     "conditions": [_c("eps_growth", "gt", 50), _c("quality", "gt", 60)]},
    {"key": "strong_earnings", "icon": "📊", "name": "Strong Earnings",
     "group": "Quality", "desc": "Earnings and revenue both growing, CANSLIM pass",
     "conditions": [_c("eps_growth", "gt", 25), _c("revenue_growth", "gt", 10),
                    _c("canslim", "eq", True)]},
    {"key": "elite_margins", "icon": "💎", "name": "Elite Margins",
     "group": "Quality", "desc": "Gross and operating margins in the top decile",
     "conditions": [_c("gross_margin", "gt", 60),
                    _c("operating_margin", "gt", 25)]},
    {"key": "fortress_balance", "icon": "🏦", "name": "Fortress Balance Sheet",
     "group": "Quality", "desc": "Strong liquidity, little leverage",
     "conditions": [_c("health", "gt", 80), _c("debt_to_equity", "lt", 50)]},
    {"key": "high_roe", "icon": "⚙️", "name": "High ROE Compounders",
     "group": "Quality", "desc": "Returns on equity well above cost of capital",
     "conditions": [_c("roe", "gt", 25), _c("quality", "gt", 70)]},
    {"key": "cash_machines", "icon": "💰", "name": "Cash Machines",
     "group": "Quality", "desc": "Converts revenue into free cash flow",
     "conditions": [_c("fcf_margin", "gt", 20), _c("quality", "gt", 65)]},
    {"key": "compounders", "icon": "🌱", "name": "Long-Term Compounders",
     "group": "Quality", "desc": "Quality, moat and balance sheet all strong",
     "conditions": [_c("quality", "gt", 85), _c("moat", "gte", 3),
                    _c("health", "gt", 75)]},

    # ── Value ───────────────────────────────────────────────────────────────
    {"key": "garp", "icon": "⚖️", "name": "Growth at Reasonable Price",
     "group": "Value", "desc": "PEG under 2 with growth actually behind it",
     "conditions": [Condition("peg_ratio", "between", 0, 2),
                    _c("eps_growth", "gt", 20), _c("quality", "gt", 60)]},
    {"key": "cheap_quality", "icon": "🏷️", "name": "Cheap Quality",
     "group": "Value", "desc": "Good businesses on a below-median multiple",
     "conditions": [Condition("forward_pe", "between", 0, 18),
                    _c("quality", "gt", 65)]},
    {"key": "value_momentum", "icon": "🧲", "name": "Value + Momentum",
     "group": "Value", "desc": "Cheap and already working",
     "conditions": [Condition("forward_pe", "between", 0, 20),
                    _c("rs_rank", "gt", 70)]},
    {"key": "deep_value", "icon": "🪙", "name": "Deep Value",
     "group": "Value", "desc": "Bottom-decile multiples that still pay the bills",
     "conditions": [Condition("forward_pe", "between", 0, 12),
                    _c("health", "gt", 50)]},
    {"key": "undervalued_ai", "icon": "🤖", "name": "Undervalued AI",
     "group": "Value",
     "desc": "Names on your AI watchlist not yet priced for perfection",
     "conditions": [_c("watchlist", "has", "AI"),
                    Condition("forward_pe", "between", 0, 30)]},

    # ── Setups ──────────────────────────────────────────────────────────────
    {"key": "near_buy_zone", "icon": "🎯", "name": "Near Buy Zone",
     "group": "Setups", "desc": "The buy-zone scorer says entry-ready",
     "conditions": [_c("in_buy_zone", "eq", True)]},
    {"key": "buy_zone_quality", "icon": "🥇", "name": "Buy Zone + Quality",
     "group": "Setups", "desc": "Entry-ready and worth owning",
     "conditions": [_c("in_buy_zone", "eq", True), _c("quality", "gt", 65)]},
    {"key": "near_support", "icon": "🛟", "name": "Near Support",
     "group": "Setups", "desc": "Sitting on a tested level in an uptrend",
     "conditions": [_c("dist_to_support", "within", 2),
                    _c("above_200ma", "eq", True)]},
    {"key": "near_8ema", "icon": "📏", "name": "Near 8 EMA",
     "group": "Setups", "desc": "Within 1% of the 8 EMA",
     "conditions": [_c("abs_vs_8ema", "within", 1),
                    _c("above_200ma", "eq", True)]},
    {"key": "near_50ma", "icon": "📐", "name": "Near 50 MA",
     "group": "Setups", "desc": "Testing the 50 MA from above",
     "conditions": [_c("abs_vs_50ma", "within", 2),
                    _c("above_200ma", "eq", True)]},
    {"key": "vcp", "icon": "🌀", "name": "VCP Setups",
     "group": "Setups", "desc": "Volatility contraction patterns",
     "conditions": [_c("is_vcp", "eq", True)]},
    {"key": "swing", "icon": "⚡", "name": "Swing Score 70+",
     "group": "Setups", "desc": "Scan's swing score high, tight to the 8 EMA",
     "conditions": [_c("swing_score", "gt", 70),
                    _c("abs_vs_8ema", "within", 3)]},
    {"key": "day_trade", "icon": "☀️", "name": "Day Trade Ready",
     "group": "Setups", "desc": "Intraday score with the volume to move",
     "conditions": [_c("daytrade_score", "gt", 60), _c("rvol", "gte", 1.5)]},
    {"key": "best_rr", "icon": "📶", "name": "Best Risk : Reward",
     "group": "Setups", "desc": "4:1 or better to the next resistance",
     "conditions": [_c("rr", "gte", 4), _c("above_200ma", "eq", True)]},

    # ── Ownership ───────────────────────────────────────────────────────────
    {"key": "institutional_buying", "icon": "🏛️", "name": "Institutional Buying",
     "group": "Ownership", "desc": "Institutions adding, not just holding",
     "conditions": [_c("inst_own_chg", "gt", 3), _c("inst_own", "gt", 85)]},
    {"key": "hedge_fund", "icon": "🛡️", "name": "Top Hedge Fund Picks",
     "group": "Ownership",
     "desc": "Heavily institutionally owned and high quality with it",
     "conditions": [_c("inst_own", "gt", 95), _c("quality", "gt", 70),
                    _c("conv_stars", "gte", 3)]},
    {"key": "accumulation", "icon": "📥", "name": "Under Accumulation",
     "group": "Ownership", "desc": "Ownership rising while the trend holds",
     "conditions": [_c("inst_own_chg", "gt", 5),
                    _c("above_200ma", "eq", True)]},

    # ── Contrarian ──────────────────────────────────────────────────────────
    {"key": "oversold_quality", "icon": "🧊", "name": "Oversold Quality",
     "group": "Contrarian", "desc": "Good businesses with RSI in the cellar",
     "conditions": [_c("quality", "gt", 70), _c("rsi", "lt", 40)]},
    {"key": "recovery", "icon": "🔄", "name": "Recovery Candidates",
     "group": "Contrarian", "desc": "Turnarounds with a survivable balance sheet",
     "conditions": [_c("is_turnaround", "eq", True), _c("health", "gt", 50)]},
    {"key": "oversold_bounce", "icon": "🪃", "name": "Oversold Bounce",
     "group": "Contrarian", "desc": "Washed out with a bounce setup at support",
     "conditions": [_c("rsi", "lt", 40), _c("bounce_probability", "gt", 50)]},
    {"key": "off_the_lows", "icon": "🕳️", "name": "Near 52-Week Low",
     "group": "Contrarian", "desc": "Close to the lows but still a real business",
     "conditions": [_c("dist_52w_low", "lt", 15), _c("quality", "gt", 55)]},

    # ── Themes ──────────────────────────────────────────────────────────────
    {"key": "ai_leaders", "icon": "🤖", "name": "AI Leaders",
     "group": "Themes", "desc": "Your AI watchlist, filtered to what's working",
     "conditions": [_c("watchlist", "has", "AI"), _c("quality", "gt", 65),
                    _c("rs_rank", "gt", 60)]},
    {"key": "ai_momentum", "icon": "⚡", "name": "AI Momentum",
     "group": "Themes", "desc": "AI names in a momentum setup",
     "conditions": [_c("watchlist", "has", "AI"),
                    _c("above_200ma", "eq", True), _c("rs_rank", "gt", 70)]},
    {"key": "semis", "icon": "🔌", "name": "Semiconductors",
     "group": "Themes", "desc": "The semi complex, ranked by strength",
     "conditions": [_c("industry", "eq", "Semiconductors"),
                    _c("rs_rank", "gt", 50)]},
    {"key": "dividend_quality", "icon": "🧾", "name": "Dividend Quality",
     "group": "Themes", "desc": "Your dividend list, screened for balance sheet",
     "conditions": [_c("watchlist", "has", "Dividend"), _c("health", "gt", 55)]},
    {"key": "small_caps", "icon": "🌾", "name": "Best Small Caps",
     "group": "Themes", "desc": "Sub-$10B with quality and relative strength",
     "conditions": [Condition("market_cap", "between", 1e9, 1e10),
                    _c("quality", "gt", 60), _c("rs_rank", "gt", 60)]},
    {"key": "mega_caps", "icon": "🐘", "name": "Mega Cap Leaders",
     "group": "Themes", "desc": "$200B+ still outperforming",
     "conditions": [_c("market_cap", "gte", 2e11), _c("rs_rank", "gt", 60)]},

    # ── Events ──────────────────────────────────────────────────────────────
    {"key": "earnings_soon", "icon": "📅", "name": "Earnings This Week",
     "group": "Events", "desc": "Reporting within 7 days — size accordingly",
     "conditions": [_c("earnings_soon", "eq", True)]},
    {"key": "post_earnings_strength", "icon": "🎆", "name": "Post-Earnings Strength",
     "group": "Events",
     "desc": "Growing and leading, with the next report more than a week out",
     "conditions": [_c("eps_growth", "gt", 25), _c("rs_rank", "gt", 70),
                    _c("days_to_earnings", "gt", 7)]},
    {"key": "call_candidates", "icon": "📞", "name": "Call Candidates",
     "group": "Events", "desc": "The scan's bullish option setups",
     "conditions": [_c("call_candidate", "eq", True)]},
    {"key": "put_candidates", "icon": "🔻", "name": "Put Candidates",
     "group": "Events", "desc": "The scan's bearish option setups",
     "conditions": [_c("put_candidate", "eq", True)]},

    # ── Risk ────────────────────────────────────────────────────────────────
    {"key": "extended", "icon": "🌡️", "name": "Extended / Overbought",
     "group": "Risk", "desc": "Stretched from the 8 EMA — a don't-chase list",
     "conditions": [_c("abs_vs_8ema", "gt", 8), _c("rsi", "gt", 70)]},
    {"key": "death_cross", "icon": "💀", "name": "Death Cross",
     "group": "Risk", "desc": "50 MA below the 200 MA — avoid or hedge",
     "conditions": [_c("death_cross", "eq", True)]},
    {"key": "high_short_interest", "icon": "🎈", "name": "High Short Interest",
     "group": "Risk", "desc": "Crowded shorts — squeeze fuel or a warning",
     "conditions": [_c("short_interest", "gt", 10)]},
)

# Which decision strategy a preset implies. A preset already expresses an
# intent — "Swing Ready" selects on setup quality — so the action shown for
# its results has to be gated on the same score. Without this the page can
# select ALL on a swing score of 82 and then label it AVOID off a long-term
# score of 53, contradicting the screen that produced it.
PRESET_GROUP_STRATEGY = {
    "Momentum": "SWING", "Setups": "SWING", "Events": "SWING",
    "Quality": "LONGTERM", "Value": "LONGTERM", "Ownership": "LONGTERM",
    "Contrarian": "LONGTERM", "Themes": "LONGTERM", "Risk": "LONGTERM",
}


def preset_strategy(key: str) -> str:
    """Explicit per-preset strategy wins; otherwise the group's."""
    p = PRESET_BY_KEY.get(key) or {}
    return p.get("strategy") or PRESET_GROUP_STRATEGY.get(
        p.get("group", ""), "LONGTERM")


PRESET_BY_KEY = {p["key"]: p for p in PRESETS}


def preset_group(key: str) -> Group | None:
    p = PRESET_BY_KEY.get(key)
    if not p:
        return None
    return Group("AND", list(p["conditions"]))


# ─────────────────────────────────────────────────────────────────────────────
# NATURAL LANGUAGE
# ─────────────────────────────────────────────────────────────────────────────
# Phrase -> field, checked longest-first so "price near 8 ema" doesn't match
# on the bare "price". Everything here resolves to a real FIELDS entry, so a
# parsed query is always an editable set of pills, never a black box.

_PHRASES: tuple[tuple[str, str], ...] = (
    ("institutional ownership", "inst_own"), ("institution ownership", "inst_own"),
    ("inst ownership", "inst_own"), ("institutional own", "inst_own"),
    ("inst own", "inst_own"), ("ownership", "inst_own"),
    ("relative strength", "rs_rank"), ("rs rank", "rs_rank"), ("rs", "rs_rank"),
    ("quality score", "quality"), ("quality", "quality"),
    ("health score", "health"), ("health", "health"),
    ("moat", "moat"),
    ("eps growth", "eps_growth"), ("earnings growth", "eps_growth"),
    ("revenue growth", "revenue_growth"), ("sales growth", "revenue_growth"),
    ("forward pe", "forward_pe"), ("forward p/e", "forward_pe"),
    ("pe ratio", "forward_pe"), ("p/e", "forward_pe"), ("pe", "forward_pe"),
    ("peg ratio", "peg_ratio"), ("peg", "peg_ratio"),
    ("market cap", "market_cap"), ("mcap", "market_cap"),
    ("conviction score", "conviction"), ("conviction", "conviction"),
    ("investment score", "investment_score"),
    ("swing score", "swing_score"), ("swing", "swing_score"),
    ("day score", "daytrade_score"), ("daytrade score", "daytrade_score"),
    ("breakout probability", "breakout_probability"),
    ("breakout %", "breakout_probability"), ("breakout", "breakout_probability"),
    ("bounce probability", "bounce_probability"),
    ("volume surge", "rvol"), ("rvol", "rvol"), ("relative volume", "rvol"),
    ("atr", "atr_pct"), ("rsi", "rsi"), ("adx", "adx"),
    ("gross margin", "gross_margin"), ("operating margin", "operating_margin"),
    ("fcf margin", "fcf_margin"), ("free cash flow margin", "fcf_margin"),
    ("return on equity", "roe"), ("roe", "roe"),
    ("debt to equity", "debt_to_equity"), ("debt/equity", "debt_to_equity"),
    ("current ratio", "current_ratio"),
    ("short interest", "short_interest"),
    ("risk reward", "rr"), ("risk:reward", "rr"), ("r:r", "rr"), ("rr", "rr"),
    ("distance from 52w high", "dist_52w_high"),
    ("distance from 52 week high", "dist_52w_high"),
    ("off 52w high", "dist_52w_high"), ("52w high", "dist_52w_high"),
    ("days to earnings", "days_to_earnings"),
    ("key level score", "key_level_score"),
    ("buy zone score", "buy_zone_score"),
    ("position rank", "rank_score"), ("rank score", "rank_score"),
    ("call score", "call_score"), ("put score", "put_score"),
    ("price", "price"),
)

# Bare flags — no operator or number needed.
_FLAGS: tuple[tuple[str, str, Any], ...] = (
    ("above 200ma", "above_200ma", True), ("above 200 ma", "above_200ma", True),
    ("above the 200ma", "above_200ma", True),
    ("below 200ma", "above_200ma", False), ("below 200 ma", "above_200ma", False),
    ("above 50ma", "above_50ma", True), ("above 50 ma", "above_50ma", True),
    ("below 50ma", "above_50ma", False),
    ("above vwap", "above_vwap", True), ("below vwap", "above_vwap", False),
    ("golden cross", "golden_cross", True), ("death cross", "death_cross", True),
    ("turnaround", "is_turnaround", True),
    ("momentum pullback", "is_momentum_pullback", True),
    ("momentum", "is_momentum", True),
    ("vcp", "is_vcp", True),
    ("long term", "is_long_term", True), ("longterm", "is_long_term", True),
    ("buy zone", "in_buy_zone", True), ("near buy zone", "in_buy_zone", True),
    ("oversold", "is_oversold", True),
    ("watchlist", "is_watchlist", True),
    ("canslim", "canslim", True),
    ("call candidate", "call_candidate", True),
    ("put candidate", "put_candidate", True),
    ("earnings soon", "earnings_soon", True),
    ("entry gate", "entry_gate_pass", True),
)

_ENUM_WORDS: tuple[tuple[str, str, str], ...] = (
    ("grade a", "grade", "A"), ("grade b", "grade", "B"),
    ("grade c", "grade", "C"),
    ("strong moat", "moat_label", "Strong signals"),
    ("trending", "trend_strength", "Trending"),
    ("ranging", "trend_strength", "Ranging"),
)

# Word-anchored: a bare "at" substring would fire on "category"/"valuation"
# and send those clauses down the within-a-moving-average branch.
_NEAR_RE = re.compile(r"\b(?:near|close to|within|around|at|off)\b")

_REF_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b8\s*ema\b", "8ema"), (r"\b21\s*ema\b", "21ema"),
    (r"\b50\s*(?:d)?\s*ma\b", "50ma"), (r"\b50\s*day\b", "50ma"),
    (r"\b200\s*(?:d)?\s*ma\b", "200ma"), (r"\b200\s*day\b", "200ma"),
    (r"\bsupport\b", "support"), (r"\bresistance\b", "resistance"),
)

# Order matters: ">=" and "at least" must be tested before ">" and "above",
# or "or above" would resolve to a strict greater-than.
_OP_WORDS: tuple[tuple[str, str], ...] = (
    (r">=|≥|\b(?:at least|minimum|min)\b|\bor (?:higher|more|above|greater)\b", "gte"),
    (r"<=|≤|\b(?:at most|maximum|max)\b|\bor (?:lower|less|below)\b", "lte"),
    (r">|\b(?:above|over|greater than|more than|higher than|exceeds)\b", "gt"),
    (r"<|\b(?:below|under|less than|lower than|cheaper than)\b", "lt"),
    (r"=|\b(?:equals|is exactly)\b", "eq"),
)

# Fields where a plain number means "at most" — nobody means "P/E above 25"
# when they type "pe 25". Everything else defaults to "at least".
_DEFAULT_LTE = {"forward_pe", "peg_ratio", "debt_to_equity", "atr_pct",
                "days_to_earnings", "short_interest"}


def _num_in(text: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(%|x|b|bn|billion|t|trillion|m|million)?",
                  text, re.I)
    if not m:
        return None
    v = float(m.group(1))
    mult = (m.group(2) or "").lower()
    if mult in ("b", "bn", "billion"):
        v *= 1e9
    elif mult in ("t", "trillion"):
        v *= 1e12
    elif mult in ("m", "million"):
        v *= 1e6
    return v


def _split_clauses(text: str) -> list[tuple[str, bool]]:
    """Split on and/,/+/with, returning (clause, negated)."""
    parts = re.split(r"\s+and\s+|\s*[,+]\s*|\s+with\s+|\s*&\s*", text, flags=re.I)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        neg = False
        m = re.match(r"^(?:not|no|exclude|excluding|without)\s+(.*)$", p, re.I)
        if m:
            neg, p = True, m.group(1).strip()
        out.append((p, neg))
    return out


def parse_query(text: str) -> list[Condition]:
    """Natural language -> conditions. Unparseable clauses are skipped
    silently; the caller shows what was understood as pills so anything
    dropped is visible by its absence."""
    if not text or not text.strip():
        return []
    conds: list[Condition] = []
    for clause, neg in _split_clauses(text.lower().strip()):
        c = _parse_clause(clause)
        if c:
            for cond in c:
                cond.negate = neg
            conds.extend(c)
    # Dedupe on (field, op) keeping the last — "quality > 90 quality > 95"
    # is a correction, not two rules.
    seen: dict[tuple[str, str], Condition] = {}
    for c in conds:
        seen[(c.field, c.op)] = c
    return list(seen.values())


_MA_REF_RE = re.compile(r"\b(?:8\s*ema|21\s*ema|50\s*(?:d)?\s*ma|"
                        r"200\s*(?:d)?\s*ma|50\s*day|200\s*day)\b")

_FLAGS_SORTED = sorted(_FLAGS, key=lambda t: -len(t[0]))
_ENUMS_SORTED = sorted(_ENUM_WORDS, key=lambda t: -len(t[0]))
_PHRASES_SORTED = sorted(_PHRASES, key=lambda t: -len(t[0]))


def _parse_clause(clause: str) -> list[Condition]:
    """One clause can carry several rules — "buy zone stocks above 200ma" is
    two. Each pattern that matches consumes its own span so the next pattern
    sees only the text left over, which is also what stops "above 200ma"
    from being re-read as a bare "200" threshold by the phrase pass."""
    clause = clause.strip()
    if not clause:
        return []
    conds: list[Condition] = []
    rest = clause

    def consume(pattern: str, count: int = 1) -> None:
        nonlocal rest
        rest = re.sub(pattern, " ", rest, count=count)

    # "price within 1% of the 8 ema" / "price near 8 ema"
    if _NEAR_RE.search(rest):
        for pattern, key in _REF_PATTERNS:
            m = re.search(pattern, rest)
            if not m:
                continue
            field, _ = WITHIN_FIELDS[key]
            # The reference itself holds digits ("8 ema"), so strip it before
            # reading the tolerance or "near 8 ema" would parse as within 8%.
            tol = _num_in(_MA_REF_RE.sub(" ", rest))
            conds.append(Condition(field, "within",
                                   tol if tol is not None else 1.0))
            consume(pattern)
            consume(_NEAR_RE.pattern)
            if tol is not None:
                consume(r"-?\d+(?:\.\d+)?\s*%?")
            break

    # Stars before the phrase pass: "conviction ≥ 4 stars" is a rule about
    # the star rating, but "conviction" is also a phrase for the 0-100
    # score, and it would otherwise claim the 4.
    if "star" in rest or "★" in rest:
        n = _num_in(rest) or float(rest.count("★"))
        if n:
            conds.append(Condition("conv_stars", "gte", n))
            consume(r"-?\d+(?:\.\d+)?")
            consume(r"★+|\bstars?\b")
            consume(r"\bconviction\b")

    # bare flags ("turnaround", "above 200ma") — longest phrase first so
    # "momentum pullback" wins over "momentum"
    for phrase, key, val in _FLAGS_SORTED:
        pattern = rf"\b{re.escape(phrase)}\b"
        if re.search(pattern, rest):
            conds.append(Condition(key, "eq", val))
            consume(pattern)

    for phrase, key, val in _ENUMS_SORTED:
        pattern = rf"\b{re.escape(phrase)}\b"
        if re.search(pattern, rest):
            conds.append(Condition(key, "eq", val))
            consume(pattern)

    # "<field> <op> <number>"
    for phrase, key in _PHRASES_SORTED:
        pattern = rf"(?<![a-z]){re.escape(phrase)}(?![a-z])"
        if not re.search(pattern, rest):
            continue
        after = re.sub(pattern, " ", rest, count=1)
        n = _num_in(after)
        if n is None:
            spec = FIELD_BY_KEY.get(key)
            # A bare metric name with no threshold isn't a filter.
            if spec and spec.kind == BOOL:
                conds.append(Condition(key, "eq", True))
                rest = after
            continue
        op = None
        for op_pattern, opkey in _OP_WORDS:
            if re.search(op_pattern, after, re.I):
                op = opkey
                break
        if op is None:
            op = "lte" if key in _DEFAULT_LTE else "gte"
        conds.append(Condition(key, op, n))
        rest = re.sub(r"-?\d+(?:\.\d+)?\s*(?:%|x|b|bn|billion|t|trillion|m|million)?",
                      " ", after, count=1)

    return conds


# ─────────────────────────────────────────────────────────────────────────────
# SUGGESTIONS
# ─────────────────────────────────────────────────────────────────────────────
# Typing "turn" should offer Turnaround and the combinations people actually
# build from it. Combos are hand-picked per anchor rather than mined from
# usage — there's no usage log in a single-user local tool, and inventing
# "most users also add..." from nothing would be a fabricated stat.

_COMBOS: dict[str, tuple[tuple[str, list[Condition]], ...]] = {
    "is_turnaround": (
        ("Turnaround", [_c("is_turnaround", "eq", True)]),
        ("Turnaround + Quality", [_c("is_turnaround", "eq", True),
                                  _c("quality", "gt", 80)]),
        ("Turnaround + RS", [_c("is_turnaround", "eq", True),
                             _c("rs_rank", "gt", 80)]),
        ("Turnaround + Buy Zone", [_c("is_turnaround", "eq", True),
                                   _c("in_buy_zone", "eq", True)]),
    ),
    "in_buy_zone": (
        ("Buy Zone", [_c("in_buy_zone", "eq", True)]),
        ("Buy Zone + Quality", [_c("in_buy_zone", "eq", True),
                                _c("quality", "gt", 85)]),
        ("Buy Zone + Momentum", [_c("in_buy_zone", "eq", True),
                                 _c("is_momentum", "eq", True)]),
        ("Buy Zone + CANSLIM", [_c("in_buy_zone", "eq", True),
                                _c("canslim", "eq", True)]),
    ),
    "quality": (
        ("Quality > 90", [_c("quality", "gt", 90)]),
        ("Quality + Moat", [_c("quality", "gt", 90), _c("moat", "gte", 3)]),
        ("Quality + Health", [_c("quality", "gt", 90), _c("health", "gt", 80)]),
    ),
    "rs_rank": (
        ("RS > 80", [_c("rs_rank", "gt", 80)]),
        ("RS > 90", [_c("rs_rank", "gt", 90)]),
        ("RS + Above 200MA", [_c("rs_rank", "gt", 85),
                              _c("above_200ma", "eq", True)]),
    ),
    "is_momentum": (
        ("Momentum", [_c("is_momentum", "eq", True)]),
        ("Momentum + RS", [_c("is_momentum", "eq", True),
                           _c("rs_rank", "gt", 85)]),
        ("Momentum + Breakout", [_c("is_momentum", "eq", True),
                                 _c("breakout_probability", "gt", 70)]),
    ),
    "abs_vs_8ema": (
        ("Price within 1% of 8 EMA", [_c("abs_vs_8ema", "within", 1)]),
        ("Price within 2% of 8 EMA", [_c("abs_vs_8ema", "within", 2)]),
    ),
    "eps_growth": (
        ("EPS Growth > 25%", [_c("eps_growth", "gt", 25)]),
        ("EPS Growth + Quality", [_c("eps_growth", "gt", 25),
                                  _c("quality", "gt", 85)]),
    ),
    "inst_own": (
        ("Institutional Ownership > 70%", [_c("inst_own", "gt", 70)]),
        ("Inst. Ownership + Quality", [_c("inst_own", "gt", 70),
                                       _c("quality", "gt", 85)]),
    ),
}


def _starts(prefix: str, text: str) -> bool:
    """Does any word in `text` start with `prefix`?

    Plain substring matching puts "AI Leaders" and "Institutional
    Ownership" under a search for "rs" (leade-rs, owne-rs-hip), which buries
    the RS Rank entries the user meant. A multi-word prefix ("8 ema") can't
    match a single word, so it falls back to a substring test.
    """
    low = text.lower()
    if " " in prefix:
        return prefix in low
    return any(w.startswith(prefix) for w in re.split(r"[^a-z0-9%]+", low) if w)


def suggest(prefix: str, limit: int = 8) -> list[dict]:
    """Autocomplete for the search box: matching combos first, then any
    other field whose label matches."""
    p = (prefix or "").strip().lower()
    if not p:
        return []
    out: list[dict] = []
    seen_labels = set()

    def add(label: str, conds: list[Condition], kind: str):
        if label in seen_labels:
            return
        seen_labels.add(label)
        out.append({"label": label, "kind": kind,
                    "conditions": [conditions_to_json(c) for c in conds]})

    # Anchor matches first — typing "turn" wants every Turnaround combo.
    # Matching a combo's *label* must not drag in its whole group, or "qual"
    # leads with Turnaround because one of its combos is "Turnaround +
    # Quality".
    for key, combos in _COMBOS.items():
        spec = FIELD_BY_KEY.get(key)
        hay = f'{spec.label if spec else ""} {key.replace("_", " ")}'
        if _starts(p, hay):
            for label, conds in combos:
                add(label, conds, "combo")

    # Then matching presets — capped, because there are ~50 of them and
    # several share a word like "Quality"; letting them all in would bury
    # the field the user is actually typing.
    matched_presets = [x for x in PRESETS if _starts(p, x["name"])]
    for preset in matched_presets[:3]:
        add(f'{preset["icon"]} {preset["name"]}', preset["conditions"], "preset")

    # …then individual combos that mention the term, whatever their anchor.
    for combos in _COMBOS.values():
        for label, conds in combos:
            if _starts(p, label):
                add(label, conds, "combo")

    for spec in FIELDS:
        if len(out) >= limit:
            break
        if _starts(p, f'{spec.label} {spec.key.replace("_", " ")}'):
            if spec.kind == BOOL:
                add(spec.label, [Condition(spec.key, "eq", True)], "field")
            else:
                add(f"{spec.label}…", [Condition(spec.key,
                                                 "lte" if spec.key in _DEFAULT_LTE
                                                 else "gte", None)], "field")
    return out[:limit]


def refine_suggestions(group: Group, limit: int = 2) -> list[dict]:
    """Given the current screen, propose a filter or two that pairs well.

    Deliberately rule-based and phrased as a suggestion, not as "most users
    who filtered X also added Y" — there is no user base to measure in a
    local single-user tool, and inventing that statistic would be a lie
    dressed up as social proof.
    """
    active = {c.field for c in _walk(group)}
    if not active:
        return []
    out: list[dict] = []
    for trigger, key, why in _REFINEMENTS:
        if len(out) >= limit:
            break
        if trigger in active and key not in active:
            spec = FIELD_BY_KEY.get(key)
            cond = _REFINE_COND[key]
            out.append({"label": f"Add {spec.label if spec else key}",
                        "why": why,
                        "conditions": [conditions_to_json(cond)]})
    return out


_REFINE_COND = {
    "rs_rank": _c("rs_rank", "gt", 80),
    "above_200ma": _c("above_200ma", "eq", True),
    "health": _c("health", "gt", 70),
    "rr": _c("rr", "gte", 2),
    "forward_pe": _c("forward_pe", "lt", 40),
    "days_to_earnings": _c("days_to_earnings", "gt", 7),
    "quality": _c("quality", "gt", 85),
}

_REFINEMENTS: tuple[tuple[str, str, str], ...] = (
    ("is_turnaround", "rs_rank", "A repairing business the market hasn't "
                                 "noticed yet still lags — RS confirms the turn "
                                 "is showing up in the tape."),
    ("is_turnaround", "health", "Turnarounds fail on the balance sheet first."),
    ("breakout_probability", "above_200ma", "Breakouts against a downtrend fail "
                                            "far more often."),
    ("quality", "forward_pe", "Quality with no valuation bound tends to return "
                              "the same expensive mega-caps."),
    ("in_buy_zone", "rr", "An entry is only as good as what it pays."),
    ("is_momentum", "days_to_earnings", "Momentum entries inside the earnings "
                                        "window carry gap risk."),
    ("eps_growth", "quality", "Growth without margins is often one-off."),
    ("inst_own", "rs_rank", "Institutions already own plenty of dead money."),
)


def _walk(group: Group) -> list[Condition]:
    out = []
    for item in group.items:
        if isinstance(item, Group):
            out.extend(_walk(item))
        else:
            out.append(item)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# (DE)SERIALIZATION — saved searches and the API speak these dicts
# ─────────────────────────────────────────────────────────────────────────────

def conditions_to_json(c: Condition) -> dict:
    return {"field": c.field, "op": c.op, "value": c.value,
            "value2": c.value2, "negate": c.negate, "weight": c.weight}


def group_to_json(g: Group) -> dict:
    return {"op": g.op, "negate": g.negate,
            "items": [group_to_json(i) if isinstance(i, Group)
                      else conditions_to_json(i) for i in g.items]}


def condition_from_json(d: dict) -> Condition:
    return Condition(field=str(d.get("field") or ""),
                     op=str(d.get("op") or "gte"),
                     value=d.get("value"), value2=d.get("value2"),
                     negate=bool(d.get("negate")),
                     weight=float(d.get("weight") or 1.0))


def group_from_json(d: dict | None) -> Group:
    if not d:
        return Group("AND", [])
    if "items" not in d:                       # a bare condition
        return Group("AND", [condition_from_json(d)])
    items = []
    for raw in d.get("items") or []:
        if isinstance(raw, dict) and "items" in raw:
            items.append(group_from_json(raw))
        elif isinstance(raw, dict):
            items.append(condition_from_json(raw))
    return Group(op=str(d.get("op") or "AND").upper(), items=items,
                 negate=bool(d.get("negate")))


def describe(cond: Condition) -> str:
    """Pill text: "Quality > 95", "Price within 1% of 8 EMA"."""
    spec = cond.spec()
    if spec is None:
        return cond.field
    prefix = "NOT " if cond.negate else ""
    if spec.kind == LIST:
        return f'{prefix}On "{cond.value}" watchlist'
    if spec.kind == BOOL:
        want = _b(cond.value)
        want = True if want is None else want
        return f"{prefix}{spec.label}" if want else f"{prefix}Not {spec.label}"
    if spec.kind == ENUM:
        if cond.op == "in" and isinstance(cond.value, (list, tuple)):
            return f"{prefix}{spec.label} = {', '.join(str(v) for v in cond.value)}"
        sym = "=" if cond.op == "eq" else "≠"
        return f"{prefix}{spec.label} {sym} {cond.value}"
    if cond.op == "within":
        ref = _WITHIN_LABEL.get(spec.key)
        tail = f" of {ref}" if ref else ""
        return f"{prefix}Price within {spec.format(cond.value)}{tail}"
    if cond.op == "between":
        return (f"{prefix}{spec.label} {spec.format(cond.value)}–"
                f"{spec.format(cond.value2)}")
    return f"{prefix}{spec.label} {OPERATORS.get(cond.op, cond.op)} {spec.format(cond.value)}"


_WITHIN_LABEL = {f: label for f, label in WITHIN_FIELDS.values()}
