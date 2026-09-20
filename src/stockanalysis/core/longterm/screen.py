"""
screen.py — rules over the Long-Term Buy Engine's own columns
=============================================================
Lets the /longterm page filter on what the engine computed — LQuality, the
valuation band, trend state, pullback stage, entry readiness, the S1/R1
levels — the same way /screener filters the research library.

It reuses core.screener's rule engine rather than growing a second one.
That engine is already a well-tested Condition/Group evaluator with
operators, per-condition explanations and match scoring; the only thing it
lacked was a description of these fields and rows shaped to match. So this
module supplies exactly two things:

    LONGTERM_FIELDS   Field specs for the engine's outputs
    flatten()         one evaluate() result -> one flat screenable row

Why the fields register into FIELD_BY_KEY but not into FIELDS
--------------------------------------------------------------
core.screener resolves a rule's field through FIELD_BY_KEY, and separately
iterates FIELDS to build the /screener picker and its natural-language
autocomplete. Those are different jobs, and here they need different answers.

Registering into the lookup makes the rule engine understand `lquality >=
85`. NOT registering into FIELDS keeps these fields out of the /screener
page — where they would appear as options over a universe that has no values
for them, so every rule would come back "no data" for all 545 rows. A field
offered in a picker that cannot match anything is worse than an absent one.

The nesting is the whole point of the flattening
------------------------------------------------
evaluate() returns a deliberately nested result — quality, valuation, trend
and pullback each keep their own sub-scores, coverage and reasoning, because
the reasoning panel needs all of it. A rule engine wants scalars. flatten()
is the seam, and it is the only place that knows both shapes.
"""

from __future__ import annotations

from stockanalysis.core import screener as S
from stockanalysis.core.longterm import buy_zones as BZ
from stockanalysis.core.longterm import engine as E
from stockanalysis.core.longterm import quality as Q
from stockanalysis.core.longterm import technicals as T
from stockanalysis.core.longterm import valuation as V
from stockanalysis.core.swing import engine as SW

NUM, BOOL, ENUM = S.NUM, S.BOOL, S.ENUM
UP, DOWN = S.UP, S.DOWN

TIER_VALUES = tuple(name for _floor, name in Q.TIERS)
BAND_VALUES = V.BANDS
CONFIDENCE_VALUES = ("HIGH", "MODERATE", "LOW")
METHOD_VALUES = ("REVERSE_DCF", "PEER")
TREND_VALUES = T.TREND_STATES
STAGE_VALUES = T.STAGES
ACTION_VALUES = E.ACTIONS
INSIDER_VALUES = tuple(name for _floor, name in Q.INSIDER_BANDS)


def _band_name(label: str) -> str:
    """"🟢 Strong cluster" -> "Strong".

    Two things come off the engine's band label. The leading status emoji is
    right in a table cell and wrong in a filter value — it would have to
    survive a URL round trip and be typed to match. The trailing noun is
    already carried by the field name, so keeping it would make the rule pill
    read "Level cluster is Strong cluster".

    Derived from CLUSTER_BANDS rather than written out, so adding a band adds
    a filter value with it.
    """
    head, _, rest = str(label).partition(" ")
    name = (rest or head).strip()
    return name[:-len(" cluster")] if name.endswith(" cluster") else name


CLUSTER_VALUES = tuple(_band_name(name) for _floor, name in T.CLUSTER_BANDS)

# `src` is the key flatten() writes. Kept identical to `key` throughout so
# there is one name per concept — the screener allows them to differ and that
# freedom has no use here.
LONGTERM_FIELDS: tuple[S.Field, ...] = (
    # ── Business quality ────────────────────────────────────────────────────
    S.Field("lquality", "LQuality", "Quality", NUM, "lquality", "", UP,
            hint="The engine's own 100-point business score", decimals=0),
    S.Field("lq_tier", "Quality tier", "Quality", ENUM, "lq_tier",
            values=TIER_VALUES, hint="Elite / High Quality / Watchlist / Reject"),
    S.Field("lq_coverage", "Quality coverage", "Quality", NUM, "lq_coverage",
            "%", UP, hint="Share of factor weight that had data", decimals=0),
    S.Field("insider", "Insider signal", "Quality", ENUM, "insider",
            values=INSIDER_VALUES,
            hint="Reported beside the verdict, never inside LQuality"),

    # ── Valuation ───────────────────────────────────────────────────────────
    S.Field("valuation_band", "Valuation", "Valuation", ENUM, "valuation_band",
            values=BAND_VALUES, hint="Undervalued / Fair / Overvalued"),
    S.Field("valuation_method", "Valuation method", "Valuation", ENUM,
            "valuation_method", values=METHOD_VALUES,
            hint="Reverse DCF where cash flows allow, else a peer multiple"),
    S.Field("valuation_confidence", "Valuation confidence", "Valuation", ENUM,
            "valuation_confidence", values=CONFIDENCE_VALUES),
    S.Field("implied_growth", "Implied growth", "Valuation", NUM,
            "implied_growth", "%", DOWN,
            hint="FCF growth the current price already requires", decimals=0),
    S.Field("delivered_growth", "Delivered growth", "Valuation", NUM,
            "delivered_growth", "%", UP, decimals=0),
    S.Field("growth_gap", "Growth gap", "Valuation", NUM, "growth_gap", "pp",
            DOWN, hint="Implied minus delivered — negative is cheap",
            decimals=0),
    S.Field("upside", "Upside to fair value", "Valuation", NUM, "upside", "%",
            UP, hint="Peer method only", decimals=0),

    # ── Trend & stage ───────────────────────────────────────────────────────
    S.Field("trend_state", "Trend state", "Trend", ENUM, "trend_state",
            values=TREND_VALUES, hint="Confirmed / Partial / Broken"),
    S.Field("trend_score", "Trend score", "Trend", NUM, "trend_score", "",
            UP, hint="Points confirmed out of 100", decimals=0),
    S.Field("trend_unknown", "Trend unmeasured", "Trend", NUM,
            "trend_unknown", "", DOWN,
            hint="Points that could not be checked", decimals=0),
    S.Field("stage", "Pullback stage", "Trend", ENUM, "stage",
            values=STAGE_VALUES,
            hint="Stage 1 EMA / 2 50MA / 3 deep / 4 breakdown"),
    S.Field("extended", "Extended", "Trend", BOOL, "extended",
            hint="More than 4% above the 8 EMA"),

    # ── Levels ──────────────────────────────────────────────────────────────
    S.Field("s1_price", "S1 support price", "Levels", NUM, "s1_price", "$",
            None, decimals=2),
    S.Field("s1_distance", "S1 support distance", "Levels", NUM,
            "s1_distance", "%", None,
            hint="Negative — how far below the price support sits",
            decimals=1),
    S.Field("s1_tested", "S1 is tested support", "Levels", BOOL, "s1_tested",
            hint="A volume-confirmed shelf, not a derived moving average"),
    S.Field("s1_touches", "S1 touches", "Levels", NUM, "s1_touches", "", UP,
            decimals=0),
    S.Field("r1_price", "R1 resistance price", "Levels", NUM, "r1_price", "$",
            None, decimals=2),
    S.Field("r1_distance", "R1 resistance distance", "Levels", NUM,
            "r1_distance", "%", UP,
            hint="Positive — headroom before price meets sellers",
            decimals=1),
    S.Field("r1_tested", "R1 is tested resistance", "Levels", BOOL,
            "r1_tested"),
    S.Field("headroom_ratio", "Headroom vs downside", "Levels", NUM,
            "headroom_ratio", "x", UP,
            hint="Distance to R1 divided by distance to S1", decimals=2),

    # The cluster reading, which the Support column has shown since it was
    # built but nothing could filter on. It answers a different question from
    # `confluence_hits` and is deliberately kept separate rather than folded
    # in: confluence counts levels HOLDING price up, so it only sees levels
    # beneath. A cluster counts levels wound around price in either
    # direction. Meta is the case that forces the distinction — five levels
    # inside 1.9% with only two below it, a tight coil at a weak support
    # score, and both readings are true.
    S.Field("cluster", "Level cluster", "Levels", ENUM, "cluster",
            values=CLUSTER_VALUES,
            hint="Levels compressed within 2% of price, above OR below. Not "
                 "directional — a cluster says a move resolves here, not "
                 "which way it resolves"),
    S.Field("cluster_count", "Levels in cluster", "Levels", NUM,
            "cluster_count", "", UP,
            hint="How many of the 8 EMA / 21 EMA / 50 MA / 200 MA / S1 / R1 "
                 "sit within 2% of price", decimals=0),
    S.Field("cluster_span", "Cluster span", "Levels", NUM, "cluster_span", "%",
            DOWN, hint="Lowest to highest level in the cluster — the tighter "
                       "the span, the more coiled. None when nothing clusters",
            decimals=1),
    S.Field("lt_pct_vs_8ema", "Distance from 8 EMA", "Levels", NUM,
            "lt_pct_vs_8ema", "%", None, decimals=1),
    S.Field("lt_pct_vs_21ema", "Distance from 21 EMA", "Levels", NUM,
            "lt_pct_vs_21ema", "%", None, decimals=1),
    S.Field("lt_pct_vs_50ma", "Distance from 50 MA", "Levels", NUM,
            "lt_pct_vs_50ma", "%", None, decimals=1),
    S.Field("lt_pct_vs_200ma", "Distance from 200 MA", "Levels", NUM,
            "lt_pct_vs_200ma", "%", None, decimals=1),

    # ── Entry ───────────────────────────────────────────────────────────────
    S.Field("readiness", "Entry readiness", "Entry", NUM, "readiness", "", UP,
            hint="Is now the moment — level, volume, reversal, market",
            decimals=0),
    S.Field("confluence_hits", "Levels agreeing", "Entry", NUM,
            "confluence_hits", "", UP, hint="Of 6 — the buy gate needs 2",
            decimals=0),
    S.Field("confluence_score", "Support confluence", "Entry", NUM,
            "confluence_score", "", UP, decimals=0),
    S.Field("volume_score", "Pullback volume", "Entry", NUM, "volume_score",
            "", UP, hint="Accumulation vs distribution", decimals=0),
    S.Field("reversal", "Bullish reversal", "Entry", BOOL, "reversal"),
    S.Field("technical", "Technical score", "Entry", NUM, "technical", "", UP,
            hint="Pure price and volume — shares no input with LQuality or "
                 "valuation, which is what makes agreement mean something",
            decimals=0),

    # ── Buy zone ────────────────────────────────────────────────────────────
    # The whole point of these is the first one. "Which names have a buy zone"
    # was previously unanswerable without opening 552 reasoning panels, and
    # the answer is small enough (18 of 552 today) that it is the query worth
    # having. `buy_zone` is the existence of the zone; `buy_zone_reached` is
    # whether the price is in it — two different questions, and conflating
    # them would hide every name that is one pullback away.
    S.Field("buy_zone", "Has a buy zone", "Buy zone", BOOL, "buy_zone",
            hint="Valuation, expected return, quality and support all agree "
                 "at some price. Says nothing about whether price is there"),
    S.Field("buy_zone_reached", "Price is in the buy zone", "Buy zone", BOOL,
            "buy_zone_reached",
            hint="The zone exists AND the stock is trading inside it"),
    S.Field("buy_verdict", "Buy-zone verdict", "Buy zone", ENUM, "buy_verdict",
            values=("BUY", "WAIT"),
            hint="BUY only when the price is in a qualifying zone and no "
                 "check fails. Distinct from Action, which is the entry "
                 "engine's own timing verdict"),
    S.Field("buy_zone_tier", "Buy-zone tier", "Buy zone", ENUM,
            "buy_zone_tier", values=BZ.TIER_LABELS,
            hint="Excellent / Preferred / Aggressive — the best tier this "
                 "name qualifies for"),
    S.Field("buy_zone_confidence", "Buy-zone confidence", "Buy zone", ENUM,
            "buy_zone_confidence", values=BZ.CONFIDENCE_LEVELS,
            hint="Agreement between the cash-flow model, the trend, tested "
                 "support and volume"),
    S.Field("buy_zone_low", "Buy zone — low", "Buy zone", NUM, "buy_zone_low",
            "$", None,
            hint="Bottom of the zone shown in the table. A qualifying "
                 "investment zone where one exists, otherwise the nearest "
                 "technical support band", decimals=2),
    S.Field("buy_zone_distance", "Buy zone distance", "Buy zone", NUM,
            "buy_zone_distance", "%", None,
            hint="How far the zone sits from today's price — negative is "
                 "below", decimals=1),
    # ── The accumulation ladder ─────────────────────────────────────────
    # Which rung price is sitting on, and how deep the correction is. The
    # two are asked together on purpose: Zone 1 is the 8/21 EMA for most
    # names and 477 of 657 are sitting on it, so "in a zone" alone is not a
    # finding. Paired with the drawdown it is.
    S.Field("dca_zone", "In accumulation zone", "Buy zone", NUM, "dca_zone",
            "", None,
            hint="Which ladder rung price is at — 1 is the nearest level "
                 "below, 3 the deepest. Blank when price is not on one",
            decimals=0),
    S.Field("dca_zone_source", "Zone type", "Buy zone", ENUM,
            "dca_zone_source",
            values=("volume_shelf", "moving_average", "structure",
                    "fundamental", "level"),
            hint="What kind of level the rung is. A tested volume shelf is "
                 "far stronger evidence than an arithmetic mean"),
    S.Field("ladder_rungs", "Rungs below price", "Buy zone", NUM,
            "ladder_rungs", "", UP,
            hint="How many accumulation levels sit below today's price",
            decimals=0),
    S.Field("dist_52w_high", "Off the 52-week high", "Buy zone", NUM,
            "dist_52w_high", "%", None,
            hint="Negative is below the high. The correction measure — "
                 "pair it with a zone hit to find quality in a real "
                 "drawdown rather than on a routine pullback",
            decimals=1),

    S.Field("buy_below", "Buy below", "Buy zone", NUM, "buy_below", "$", None,
            hint="The price at which expected return meets this business's "
                 "hurdle — the fundamental ceiling, ignoring the chart",
            decimals=2),
    S.Field("value_zone", "Valuation zone today", "Buy zone", ENUM,
            "value_zone", values=BZ.VALUE_ZONES,
            hint="What today's price would earn, banded against this "
                 "business's own hurdle"),
    S.Field("expected_cagr", "Expected return", "Buy zone", NUM,
            "expected_cagr", "%", UP,
            hint="Annualised over 5 years from today's price, if the company "
                 "compounds at the slower of its FCF and revenue growth and "
                 "the market eventually pays the model's value. A hurdle "
                 "test, not a forecast", decimals=1),
    S.Field("return_hurdle", "Return hurdle", "Buy zone", NUM, "return_hurdle",
            "%", None, hint="The bar this business has to clear — higher for "
                            "better quality, not lower", decimals=0),
    S.Field("fair_value_gap", "Price vs model value", "Buy zone", NUM,
            "fair_value_gap", "%", DOWN,
            hint="Negative is below what the cash-flow model says it is "
                 "worth", decimals=0),
    S.Field("model_value", "Model value", "Buy zone", NUM, "model_value", "$",
            None, hint="Reverse-DCF value at the projected growth rate",
            decimals=2),

    # ── Swing ───────────────────────────────────────────────────────────────
    # A separate engine's answer to a separate question, filterable on its
    # own. Deliberately NOT gated on LQuality anywhere: a 55-quality name can
    # have an excellent 3-to-20-day setup, and requiring 85 here would delete
    # exactly the trades this engine exists to find.
    S.Field("swing_score", "Swing score", "Swing", NUM, "swing_score", "", UP,
            hint="Market regime 15 · trend 20 · setup 25 · momentum 15 · "
                 "volume 15 · trade quality 10. Independent of the company "
                 "verdict", decimals=0),
    S.Field("swing_grade", "Swing grade", "Swing", ENUM, "swing_grade",
            values=tuple(g for _floor, g in SW.GRADES)),
    S.Field("swing_setup", "Swing setup", "Swing", ENUM, "swing_setup",
            values=tuple(label for label, _base in SW.SETUPS.values()),
            hint="Which pattern is occurring — not merely 'near support'"),
    S.Field("swing_state", "Swing state", "Swing", ENUM, "swing_state",
            values=("READY", "NEAR READY", "TRIGGERED", "APPROACHING",
                    "DEVELOPING", "WATCH", "NO SETUP", "NO TRADE", "EXTENDED",
                    "MISSED", "BREAKDOWN", "FAILED", "AVOID"),
            hint="More actionable than the score. WATCH is a good chart with "
                 "no pattern — not the same as NO SETUP or BREAKDOWN"),
    S.Field("swing_eligible", "Swing gates pass", "Swing", BOOL,
            "swing_eligible",
            hint="Every blocking gate passed — market, trend, setup, path, "
                 "stop, no-chase. Independent of the score"),
    S.Field("swing_action", "Swing action", "Swing", ENUM, "swing_action",
            values=("enter", "wait for the trigger", "wait", "watchlist",
                    "wait for a setup", "no trade", "do not chase",
                    "avoid long", "wait for the trend", "remove from the list",
                    "ignore")),
    S.Field("swing_stop_pct", "Swing stop distance", "Swing", NUM,
            "swing_stop_pct", "%", DOWN,
            hint="A 29% stop is a different trade from a 4% one even when "
                 "position sizing holds account risk constant", decimals=1),
    S.Field("swing_first_r", "R to first resistance", "Swing", NUM,
            "swing_first_r", "R", UP,
            hint="The level the trade actually has to clear — not R to a "
                 "distant 52-week high", decimals=2),
    S.Field("swing_path", "Swing path quality", "Swing", ENUM, "swing_path",
            values=("Good", "Fair", "Poor"),
            hint="Room to the FIRST resistance from the entry, not to a "
                 "distant 52-week high"),
    S.Field("swing_volume", "Swing volume", "Swing", ENUM, "swing_volume",
            values=("reversal", "breakout", "quiet_pullback", "neutral",
                    "distribution", "selling", "unclear", "unknown"),
            hint="Classified by what the volume happened ON — 1.6x on a down "
                 "day is supply, not conviction"),

    # ── Verdict ─────────────────────────────────────────────────────────────
    S.Field("action", "Action", "Verdict", ENUM, "action",
            values=ACTION_VALUES),
    S.Field("gate", "Stopped at gate", "Verdict", ENUM, "gate",
            values=("quality", "valuation", "trend", "regime", "earnings",
                    "entry", "support", "readiness", "trigger", "confirmed")),
    S.Field("lt_score", "LT score", "Verdict", NUM, "lt_score", "", UP,
            hint="Ranks names that already passed the gates", decimals=0),
    S.Field("tranche", "Tranche size", "Verdict", NUM, "tranche", "%", UP,
            decimals=0),
    S.Field("investment_status", "Investment", "Verdict", ENUM,
            "investment_status", values=E.INVESTMENT_STATUSES,
            hint="The COMPANY verdict — Core / Own / Watchlist / Reject. "
                 "Routinely disagrees with Action, which is the ENTRY verdict"),
    S.Field("investment_score", "Investment score", "Verdict", NUM,
            "investment_score", "", UP,
            hint="Quality and valuation only — no chart in it", decimals=0),

    # ── Position (§ risk-based sizing) ───────────────────────────────────────
    # Everything here is a property of the PLANNED TRADE rather than of the
    # company, and every one of them is None until a scan has produced a
    # sizeable plan — a rule on `rr` therefore also silently filters to names
    # the sizing engine would act on, which is usually what is wanted and is
    # worth knowing when it is not.
    S.Field("rr", "Risk / reward", "Position", NUM, "rr", "R", UP,
            hint="To the nearest target clearing 2R, measured from the "
                 "planned entry — not from today's price", decimals=2),
    S.Field("position_grade", "Position grade", "Position", ENUM,
            "position_grade", values=("A", "B", "C", "D"),
            hint="Rates the SETUP, not the company"),
    S.Field("risk_status", "Risk status", "Position", ENUM, "risk_status",
            values=("NORMAL", "HIGH_ALLOCATION", "OVERSIZED", "NO_STOP",
                    "INVALID_SETUP", "NOT_ACTIONABLE"),
            hint="Whether the position is a sane size"),
    S.Field("allocation_pct", "Allocation", "Position", NUM, "allocation_pct",
            "%", None, hint="Share of trading capital this position uses",
            decimals=1),
    S.Field("stop_pct", "Stop distance", "Position", NUM, "stop_pct", "%",
            DOWN, hint="How far the stop sits below the planned entry",
            decimals=1),

    # ── Context ─────────────────────────────────────────────────────────────
    S.Field("lt_price", "Price", "Context", NUM, "lt_price", "$", None,
            decimals=2),
    S.Field("lt_rs_rank", "RS rank", "Context", NUM, "lt_rs_rank", "", UP,
            decimals=0),
    S.Field("lt_sector_rs", "Sector RS rank", "Context", NUM, "lt_sector_rs",
            "", UP, hint="Rank within its own sector", decimals=0),
    S.Field("lt_days_to_earnings", "Days to earnings", "Context", NUM,
            "lt_days_to_earnings", "", None, decimals=0),
    S.Field("lt_sector", "Sector", "Context", ENUM, "lt_sector",
            values=("Technology", "Healthcare", "Financial Services",
                    "Consumer Cyclical", "Consumer Defensive", "Industrials",
                    "Energy", "Utilities", "Real Estate", "Basic Materials",
                    "Communication Services")),
)

LONGTERM_FIELD_BY_KEY = {f.key: f for f in LONGTERM_FIELDS}

# Make the shared rule engine able to resolve these keys. Deliberately does
# NOT touch S.FIELDS — see the module docstring.
S.FIELD_BY_KEY.update(LONGTERM_FIELD_BY_KEY)

# "Buy zone" sits straight after Valuation rather than at the end: it is
# downstream of quality and valuation and upstream of everything technical,
# which is the order the engine now reasons in.
FIELD_GROUPS = ("Quality", "Valuation", "Buy zone", "Swing", "Trend",
                "Levels", "Entry", "Position", "Verdict", "Context")


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _cluster_value(cluster: dict) -> str | None:
    """The cluster band as a filterable enum value.

    Reads the label the engine already computed rather than re-deriving the
    band from the count, so the filter and the Support column can never
    disagree about where a name sits. Returns None — "no data", which no
    equality rule matches — when the engine produced no cluster at all,
    rather than reporting that as "No level nearby", which is a measurement.
    """
    if not cluster:
        return None
    label = cluster.get("label")
    if label:
        return _band_name(label)
    count = cluster.get("count") or 0
    return next((_band_name(name) for floor, name in T.CLUSTER_BANDS
                 if count >= floor), None)


def flatten(result: dict) -> dict:
    """One evaluate() result -> a flat row core.screener can test.

    Keeps `_result` so the caller can render the full nested verdict for
    whatever survives the filter, rather than re-running the engine.
    """
    q = result.get("quality") or {}
    v = result.get("valuation") or {}
    t = result.get("trend") or {}
    p = result.get("pullback") or {}
    conf = result.get("confluence") or {}
    vol = result.get("volume") or {}
    rs = result.get("rs") or {}
    ready = result.get("readiness") or {}
    ins = result.get("insider") or {}
    bz = p.get("buy_zone") or {}
    rz = p.get("resistance") or {}
    by = p.get("by_level") or {}
    # The engine writes the cluster at the top level; technicals also leaves a
    # copy on `pullback`. Read both so a result from either path filters.
    cl = result.get("ma_cluster") or p.get("ma_cluster") or {}
    tech = result.get("technical") or {}
    inv = result.get("investment") or {}
    # Computed inside evaluate(), so always present — but every field below
    # still guards, because a result assembled by an older cached path or a
    # test fixture may not carry it.
    # `bzones`, not `bz` — `bz` a few lines up is pullback["buy_zone"], the
    # SINGULAR S1 level, and reusing the name here silently blanked
    # s1_distance and headroom_ratio for every row.
    swing = result.get("swing") or {}
    bzones = result.get("buy_zones") or {}
    fund = bzones.get("fundamental") or {}
    zones = bzones.get("investment") or []
    bz_verdict = bzones.get("verdict") or {}
    best = zones[0] if zones else {}

    # Attached by api.longterm() after evaluation, so it is absent whenever
    # the engine is run directly. Every position field then flattens to None
    # rather than raising, which is the same treatment an unscanned column
    # gets everywhere else in this module.
    plan = result.get("sizing_plan") or {}
    plan_size = plan.get("sizing") or {}
    plan_target = plan.get("target") or {}

    def level_pct(key):
        return _num((by.get(key) or {}).get("distance_pct"))

    s1_dist, r1_dist = _num(bz.get("distance_pct")), _num(rz.get("distance_pct"))
    # Headroom against downside — how much room to the first overhead level
    # per unit of give-back to the first support. Only meaningful when the
    # levels straddle the price, which is the normal case; when support sits
    # above or resistance below, the ratio would invert its own meaning.
    headroom = None
    if s1_dist is not None and r1_dist is not None and s1_dist < 0 < r1_dist:
        headroom = round(r1_dist / abs(s1_dist), 2)

    return {
        "ticker": result.get("ticker"),
        "name": result.get("name"),
        "_result": result,

        "lquality": _num(q.get("score")),
        "lq_tier": q.get("tier"),
        "lq_coverage": None if q.get("coverage") is None
                       else round(q["coverage"] * 100),
        "insider": ins.get("label"),

        "valuation_band": v.get("band"),
        "valuation_method": v.get("method"),
        "valuation_confidence": v.get("confidence"),
        "implied_growth": _num(v.get("implied_growth_pct")),
        "delivered_growth": _num(v.get("delivered_growth_pct")),
        "growth_gap": _num(v.get("growth_gap_pp")),
        "upside": _num(v.get("upside_pct")),

        "trend_state": t.get("state"),
        "trend_score": _num(t.get("score")),
        "trend_unknown": _num(t.get("unknown_points")),
        "stage": p.get("stage"),
        "extended": p.get("extended"),

        "s1_price": _num(bz.get("price")),
        "s1_distance": s1_dist,
        "s1_tested": bz.get("actual_support"),
        "s1_touches": _num(bz.get("touches")),
        "r1_price": _num(rz.get("price")),
        "r1_distance": r1_dist,
        "r1_tested": rz.get("actual_resistance"),
        "headroom_ratio": headroom,
        "cluster": _cluster_value(cl),
        "cluster_count": _num(cl.get("count")) if cl else None,
        "cluster_span": _num(cl.get("span_pct")),
        "lt_pct_vs_8ema": level_pct("8EMA"),
        "lt_pct_vs_21ema": level_pct("21EMA"),
        "lt_pct_vs_50ma": level_pct("50MA"),
        "lt_pct_vs_200ma": level_pct("200MA"),

        "readiness": _num(ready.get("score")),
        "confluence_hits": _num(conf.get("agreeing")),
        "confluence_score": _num(conf.get("score")),
        "volume_score": _num(vol.get("score")),
        "reversal": _reversal_flag(result),

        "technical": _num(tech.get("score")),

        # `bz or None` rather than a bare bool: with no buy-zone data at all,
        # "has no buy zone" would be a claim the row cannot support, and a
        # False here would quietly include those names in a `buy_zone:eq:false`
        # search alongside names actually measured and rejected.
        "buy_zone": bool(zones) if bzones else None,
        "buy_zone_reached": (any(z.get("reached") for z in zones)
                             if bzones else None),
        "buy_verdict": bz_verdict.get("action"),
        "buy_zone_tier": best.get("label"),
        "buy_zone_confidence": (bz_verdict.get("confidence") or {}).get("level"),
        "buy_below": _num(fund.get("buy_below")),
        "dca_zone": _num((bzones.get("zone_hit") or {}).get("zone")),
        "dca_zone_source": (bzones.get("zone_hit") or {}).get("source"),
        "ladder_rungs": len(bzones.get("ladder") or []) if bzones else None,
        # The engine keeps the scan row it was built from; that is where
        # the drawdown lives. Read defensively — a result assembled by a
        # test fixture or an older cached path may not carry it.
        "dist_52w_high": _num(result.get("dist_52w_high")),
        "buy_zone_low": _num((bzones.get("display_zone") or {}).get("low")),
        "buy_zone_distance": _num(
            (bzones.get("display_zone") or {}).get("distance_pct")),
        "value_zone": fund.get("zone"),
        "expected_cagr": _num(fund.get("expected_cagr_now")),
        "return_hurdle": _num(fund.get("hurdle_pct")),
        "fair_value_gap": _num(fund.get("fair_value_gap_pct")),
        "model_value": _num(fund.get("intrinsic")),

        "swing_score": _num(swing.get("score")),
        "swing_grade": swing.get("grade"),
        "swing_setup": swing.get("setup_label"),
        "swing_state": swing.get("state"),
        "swing_path": (swing.get("path") or {}).get("quality"),
        "swing_volume": swing.get("volume_kind"),
        "swing_eligible": swing.get("eligible") if swing else None,
        "swing_action": swing.get("action"),
        "swing_stop_pct": _num(swing.get("stop_pct")),
        "swing_first_r": _num(swing.get("first_r")),

        "action": result.get("action"),
        "gate": result.get("gate"),
        "lt_score": _num(result.get("lt_score")),
        "tranche": _num(result.get("tranche_pct")),
        "investment_status": inv.get("status"),
        "investment_score": _num(inv.get("score")),

        "rr": _num(plan_target.get("rr")),
        "position_grade": plan.get("grade") if plan.get("grade") in
                          ("A", "B", "C", "D") else None,
        "risk_status": plan.get("status"),
        "allocation_pct": _num(plan_size.get("allocation_pct")),
        "stop_pct": _num(plan_size.get("stop_distance_pct")),

        "lt_price": _num(result.get("price")),
        "lt_rs_rank": _num(rs.get("market_rank")),
        "lt_sector_rs": _num(rs.get("sector_rank")),
        "lt_days_to_earnings": _num(result.get("days_to_earnings")),
        "lt_sector": result.get("sector"),
    }


def _reversal_flag(result) -> bool | None:
    """True/False when the candle was measured, None when it was not.

    The readiness check already draws this distinction; reading it back off
    that result keeps one source of truth rather than re-parsing the raw
    column here.
    """
    ready = result.get("readiness") or {}
    for bucket, value in (("hits", True), ("misses", False),
                          ("unknown", None)):
        for item in ready.get(bucket) or []:
            if item.get("name") == "Bullish reversal":
                return value
    return None


# ─────────────────────────────────────────────────────────────────────────────
# RULE PARSING — rules live in the URL, like the rest of this page
# ─────────────────────────────────────────────────────────────────────────────
# "lquality:gte:85" — field, operator, value. The /screener page posts a JSON
# rule tree because it has a nested AND/OR builder; this page has a flat list,
# and a flat list in the query string keeps every filtered view a shareable
# link and the back button honest.

def parse_rule(text: str) -> S.Condition | None:
    """"lquality:gte:85" -> Condition. None when unparseable, so one bad
    rule in a pasted URL drops itself rather than erroring the page."""
    parts = (text or "").split(":")
    if len(parts) < 2:
        return None
    key, op = parts[0].strip(), parts[1].strip().lower()
    spec = LONGTERM_FIELD_BY_KEY.get(key)
    if spec is None or op not in S.OPS_FOR_KIND.get(spec.kind, ()):
        return None
    raw = ":".join(parts[2:]).strip() if len(parts) > 2 else ""
    value: object = raw
    value2 = None
    if spec.kind == NUM:
        if op == "between":
            lo, _, hi = raw.partition(",")
            value, value2 = _num(lo), _num(hi)
            if value is None or value2 is None:
                return None
        else:
            value = _num(raw)
            if value is None:
                return None
    elif spec.kind == BOOL:
        value = raw.lower() in ("1", "true", "yes", "y")
    elif spec.kind == ENUM and op == "in":
        # "is any of" is offered for every ENUM field (S.OPS_FOR_KIND), but
        # the matcher only treats a LIST as a set of alternatives — a bare
        # string is wrapped as [value] and compared whole. So an unsplit
        # "Fair,Attractive,Exceptional" silently matched nothing, and the
        # operator looked available while being unusable from a rule URL.
        value = [v.strip() for v in raw.split(",") if v.strip()]
        if not value:
            return None
    return S.Condition(field=key, op=op, value=value, value2=value2)


def parse_rules(texts, op: str = "AND") -> S.Group:
    conds = [c for c in (parse_rule(t) for t in (texts or [])) if c]
    return S.Group(op="OR" if str(op).upper() == "OR" else "AND", items=conds)


def describe(cond: S.Condition) -> str:
    """A rule as a sentence, for the pill."""
    spec = LONGTERM_FIELD_BY_KEY.get(cond.field)
    if spec is None:
        return cond.field
    words = {"gte": "≥", "gt": ">", "lte": "≤", "lt": "<", "eq": "is",
             "ne": "is not", "between": "between", "within": "within",
             "in": "in", "has": "has"}
    op = words.get(cond.op, cond.op)
    if spec.kind == BOOL:
        return f"{spec.label} {'yes' if cond.value else 'no'}"
    if cond.op == "in" and isinstance(cond.value, (list, tuple)):
        # spec.format() on a list renders its Python repr, so the pill read
        # "Valuation zone today in ['Fair', 'Attractive']" — brackets, quotes
        # and all — in the one place the rule is shown back to the reader.
        return f"{spec.label} is {' or '.join(str(v) for v in cond.value)}"
    if cond.op == "between":
        return (f"{spec.label} between {spec.format(cond.value)} and "
                f"{spec.format(cond.value2)}")
    return f"{spec.label} {op} {spec.format(cond.value)}"


def apply_rules(results, rule_texts, op: str = "AND", sort: str = "lt_score"):
    """Filter engine results by the rules. Returns (kept, conditions, stats).

    `stats` is core.screener's per-condition breakdown — how many rows each
    rule passed and how many had no data for it — which is what turns "0
    matches" from a dead end into a diagnosis.
    """
    conds = [c for c in (parse_rule(t) for t in (rule_texts or [])) if c]
    if not conds:
        return list(results), [], []
    group = S.Group(op="OR" if str(op).upper() == "OR" else "AND",
                    items=list(conds))
    rows = [flatten(r) for r in results]
    stats = S.condition_stats(rows, group)
    kept = []
    for row in rows:
        if S.eval_group(row, group, []):
            kept.append(row["_result"])
    return kept, conds, stats


# ─────────────────────────────────────────────────────────────────────────────
# PRESETS — the screens a long-only quality manager actually runs
# ─────────────────────────────────────────────────────────────────────────────
# Grouped by the question being asked, because that is the order the work
# happens in: decide what deserves to be owned, then decide when to buy it,
# then check what would stop you. That is the framework's own hierarchy, and
# a preset list organised by field type instead would scatter it.
#
# Every preset below was measured against the live 545-row library and its
# count recorded in `note`. That is not decoration — three separate features
# in this engine were first built with thresholds that were empty by
# construction (the forward DCF, the confluence gate, the readiness bands),
# and a preset that returns nothing on every library is indistinguishable
# from a broken one.
#
# `needs_statements` marks the four screens that depend on the annual
# cash-flow and income statements. Those columns are 0% covered until a scan
# runs core.longterm.fundamentals, so these presets return nothing today —
# not because the rules are wrong but because the reverse DCF has no inputs
# and every valuation is falling back to a peer multiple. The UI says so
# rather than showing a bare zero.

PRESET_GROUPS = ("What to own", "When to buy", "What would stop me")

PRESETS: tuple[dict, ...] = (
    # ── What to own ─────────────────────────────────────────────────────────
    {"key": "quality_at_discount", "icon": "💎", "name": "Quality at a Discount",
     "group": "What to own", "desc": "The core screen: an elite business, not overpriced, trend not "
             "broken. Everything else here is a refinement of this one.",
     "rules": ["lquality:gte:85", "valuation_band:ne:OVERVALUED",
               "trend_state:ne:BROKEN"]},
    {"key": "wide_moat_cheap", "icon": "🏰", "name": "Wide Moat, Undervalued",
     "group": "What to own", "desc": "Top-decile quality trading below what the model says it is "
             "worth. Rare by design — if this list is long, check the "
             "valuation method before believing it.",
     "rules": ["lquality:gte:90", "valuation_band:eq:UNDERVALUED"]},
    {"key": "cashflow_verified", "icon": "🧾", "name": "Cash-Flow Verified Value",
     "group": "What to own", "needs_statements": True,
     "desc": "Undervalued on a reverse DCF built from filed statements, not "
             "on a peer multiple. The strongest valuation claim this engine "
             "can make.",
     "rules": ["valuation_method:eq:REVERSE_DCF",
               "valuation_confidence:eq:HIGH",
               "valuation_band:eq:UNDERVALUED"]},
    {"key": "garp", "icon": "📐", "name": "Growth at a Reasonable Price",
     "group": "What to own", "needs_statements": True,
     "desc": "Compounding at 15%+ while the price demands little more than "
             "that. The gap between implied and delivered growth is the "
             "whole test.",
     "rules": ["delivered_growth:gte:15", "growth_gap:lte:10",
               "lquality:gte:75"]},
    {"key": "sector_leaders", "icon": "🥇", "name": "Sector Leaders",
     "group": "What to own", "desc": "Top-quintile relative strength inside its OWN sector — a name "
             "leading its group while the group lags still leads.",
     "rules": ["lt_sector_rs:gte:80", "lquality:gte:85"]},

    # ── When to buy ─────────────────────────────────────────────────────────
    # The three buy-zone screens first, because they are the only ones that
    # test valuation, expected return, quality and support together — every
    # other preset in this group tests the chart and leaves the price to the
    # reader.
    {"key": "buy_zone_open", "icon": "🎯", "name": "Buy Zone Open Now",
     "group": "When to buy",
     "desc": "The price is inside a zone where the cash-flow model, the "
             "return hurdle, the quality floor and a support level all agree. "
             "The shortest list this engine produces, and the only one where "
             "the answer is act rather than watch.",
     "rules": ["buy_zone_reached:eq:true"]},
    # Measured 2026-08-22 on the 659-row library: 133 rows — 23 Fair,
    # 25 Attractive, 85 Exceptional. 252 rows have no zone at all (priced
    # off a peer multiple, so there is no expected return to band) and 274
    # read Expensive or Extreme. The screen is a third of the library, which
    # is the point: it is a floor, not a shortlist.
    {"key": "fair_or_better", "icon": "📊", "name": "Fair or Better on Return",
     "group": "When to buy",
     "desc": "Today's price lands on the fundamental ladder rather than off "
             "the end of it — Fair, Attractive or Exceptional against this "
             "business's own return hurdle. Note that Fair sits just BELOW "
             "the hurdle: it is 'not overpaying', not 'clears the bar'. "
             "Attractive and Exceptional are the two that clear it.",
     "rules": ["value_zone:in:Fair,Attractive,Exceptional"]},
    {"key": "buy_zone_waiting", "icon": "⏱️", "name": "Buy Zone — Waiting for the Price",
     "group": "When to buy",
     "desc": "A qualifying zone exists and the stock has not reached it. "
             "These are the names worth a price alert: nothing about the "
             "business needs to change, only the quote.",
     "rules": ["buy_zone:eq:true", "buy_zone_reached:eq:false"]},
    {"key": "buy_zone_high_conf", "icon": "🎖️", "name": "Buy Zone · High Confidence",
     "group": "When to buy",
     "desc": "A zone whose legs agree — cash-flow model on filed statements, "
             "trend confirmed, support that has actually been defended, and "
             "volume not distributing. Confidence is about how much the "
             "inputs corroborate each other, never about how good the price "
             "looks.",
     "rules": ["buy_zone:eq:true", "buy_zone_confidence:eq:High"]},
    {"key": "ready_to_deploy", "icon": "🟢", "name": "Ready to Deploy",
     "group": "When to buy", "desc": "Entry readiness 65+ on a quality business — the level, the "
             "volume and the market all lining up now rather than eventually.",
     "rules": ["readiness:gte:65", "lquality:gte:85"]},
    {"key": "compounder_pullback", "icon": "🎯", "name": "Compounder Pullback",
     "group": "When to buy", "desc": "Stage 1: quality resting on the 8/21 EMA with a tested shelf "
             "underneath. The shallow, high-momentum entry.",
     "rules": ["lquality:gte:85", "stage:eq:STAGE1_EMA", "s1_tested:eq:true"]},
    {"key": "fifty_ma_entry", "icon": "🎣", "name": "50 MA Pullback",
     "group": "When to buy", "desc": "Stage 2 — the framework's preferred core entry for a quality "
             "name, and the only stage where every readiness point is "
             "reachable.",
     "rules": ["lquality:gte:85", "stage:eq:STAGE2_50MA"]},
    {"key": "deep_pullback_quality", "icon": "🪂", "name": "Deep Pullback in Quality",
     "group": "When to buy", "desc": "Elite businesses well below the 50 MA but still above the 200 "
             "MA, structure intact. Not a buy — a list to work support on.",
     "rules": ["lq_tier:eq:Elite", "stage:eq:STAGE3_DEEP",
               "trend_state:ne:BROKEN"]},
    {"key": "asymmetric_entry", "icon": "⚖️", "name": "Asymmetric Entry",
     "group": "When to buy", "desc": "Twice as much headroom to the first resistance as give-back to "
             "a TESTED support. Position sizing writes itself.",
     "rules": ["headroom_ratio:gte:2", "s1_tested:eq:true",
               "lquality:gte:85"]},
    {"key": "coiled_quality", "icon": "🌀", "name": "Coiled at a Decision Point",
     "group": "When to buy",
     "desc": "Three or more levels wound within 2% of price on a business "
             "worth owning. A cluster is not bullish on its own — it says the "
             "next move gets resolved here rather than drifting — so this is "
             "the list to have an order and a stop ready on, not a buy. "
             "Requiring the trend intact is what keeps it from surfacing "
             "names coiling on the way down.",
     "rules": ["cluster:eq:Strong", "lquality:gte:80",
               "trend_state:ne:BROKEN"]},
    {"key": "accumulation", "icon": "🧲", "name": "Quiet Accumulation",
     "group": "When to buy", "desc": "Pullback volume drying up while relative strength holds — the "
             "shape of institutions buying rather than retail selling.",
     "rules": ["volume_score:gte:60", "lt_rs_rank:gte:60",
               "lquality:gte:80"]},
    {"key": "three_r_quality", "icon": "🏹", "name": "3R Setup in Quality",
     "group": "When to buy",
     "desc": "A good business whose chart agrees and whose priced trade pays "
             "three to one. The three readings are independent — LQuality "
             "reads the statements, Technical reads price and volume alone, "
             "and R:R comes from the planned entry against its stop — so "
             "requiring all three is a genuine triangulation rather than the "
             "same fact counted three times. TSM is the shape: 91 / 79 / 9R.",
     "rules": ["lquality:gte:80", "technical:gte:75", "rr:gte:3"]},
    {"key": "both_engines_agree", "icon": "🤝", "name": "Business & Chart Agree",
     "group": "When to buy",
     "desc": "A name the company verdict wants to OWN and the pure-technical "
             "score independently rates well. The disagreements are the "
             "normal case, which is what makes the overlap worth a list.",
     "rules": ["investment_status:eq:CORE", "technical:gte:70"]},
    {"key": "put_candidates", "icon": "🪙", "name": "Put-Selling Candidates",
     "group": "When to buy",
     "desc": "Quality that has come off its highs, at a price you would "
             "accept, with the trend intact — the shape a cash-secured put "
             "wants. Every rule here mirrors a gate in core.csp.eligibility, "
             "so the list is what will actually survive a CSP scan rather "
             "than what looks promising before one: measured over the live "
             "library all 20 matches cleared the company gate, and a "
             "selection sent from here spends no chain fetches on names the "
             "engine was always going to reject. Below the 8 EMA is the "
             "'down' test — a name at its highs has nothing to be paid to "
             "wait for, and $15 is where collateral stops being worth the "
             "contract.",
     "rules": ["lquality:gte:78", "valuation_band:ne:OVERVALUED",
               "trend_state:ne:BROKEN", "lt_pct_vs_8ema:lt:0",
               "lt_price:gte:15"]},
    {"key": "asymmetric_sized", "icon": "📏", "name": "Sized and Asymmetric",
     "group": "When to buy",
     "desc": "3R or better on a stop tight enough that the position is worth "
             "taking, and small enough that the allocation cap did not have "
             "to rescue it. The setups where risk sizing and conviction point "
             "the same way.",
     "rules": ["rr:gte:3", "risk_status:eq:NORMAL", "lquality:gte:75"]},

    # ── What would stop me ──────────────────────────────────────────────────
    {"key": "priced_for_perfection", "icon": "🎈", "name": "Priced for Perfection",
     "group": "What would stop me", "needs_statements": True,
     "desc": "Great businesses whose price already requires 35%+ FCF growth "
             "for five years. The most expensive mistake in a quality "
             "portfolio is a quality company.",
     "rules": ["lquality:gte:85", "implied_growth:gte:35"]},
    {"key": "dca_tranche", "icon": "🧱",
     "name": "DCA Tranche — Quality in a Correction",
     "group": "When to buy",
     "desc": "LQuality above 90, more than 20% off the 52-week high, and "
             "price sitting on a tracked accumulation rung. The three "
             "conditions are asked together because none is a finding "
             "alone: 477 of 657 names are on Zone 1 at any moment, since "
             "that rung is usually the 8/21 EMA, and a routine pullback is "
             "not a correction. Pair the zone with the drawdown and the "
             "list becomes short and specific — 13 names today. The Action "
             "column keeps its own verdict; several of these still read "
             "OWN / WAIT FOR TREND, and both readings are true at once.",
     "rules": ["lquality:gt:90", "dist_52w_high:lte:-20", "dca_zone:gte:1"]},
    {"key": "dca_deep", "icon": "🪜", "name": "DCA — Deep Rung",
     "group": "When to buy",
     "desc": "The same screen at the deeper end: 30%+ off the high. These "
             "are the tranches you would be adding on the second or third "
             "rung rather than the first.",
     "rules": ["lquality:gt:90", "dist_52w_high:lte:-30", "dca_zone:gte:1"]},
    {"key": "fallen_quality", "icon": "🩺", "name": "Fallen Quality — Thesis Review",
     "group": "What would stop me", "desc": "Businesses that still score well while price sits below the "
             "200 MA. Either the market knows something the fundamentals "
             "have not shown yet, or it is wrong. Worth deciding which.",
     # Expressed as "price below the 200 MA" rather than as a stage. The
     # stage split into UNCONFIRMED/BREAKDOWN when slope measurement arrived
     # and this preset silently emptied; the underlying question — a good
     # business trading under its long-term average — did not change.
     "rules": ["lquality:gte:85", "lt_pct_vs_200ma:lt:0"]},
    {"key": "dry_powder", "icon": "⏳", "name": "Dry Powder Watchlist",
     "group": "What would stop me", "desc": "Worth owning, priced acceptably, and extended above the 8 EMA. "
             "Nothing to do but wait for the pullback — which is the point.",
     "rules": ["lquality:gte:85", "valuation_band:ne:OVERVALUED",
               "extended:eq:true"]},
    {"key": "insider_aligned", "icon": "🤝", "name": "Insiders Buying",
     "group": "What would stop me", "desc": "Quality where the people who know it best are net buyers. A "
             "tiebreaker, never a thesis — it sits outside LQuality for "
             "exactly that reason.",
     "rules": ["lquality:gte:85", "insider:eq:Net buying"]},
    {"key": "priced_out_quality", "icon": "🧊", "name": "Great Business, No Price",
     "group": "What would stop me",
     "desc": "Elite businesses whose expected return from here is negative — "
             "the price already assumes more than the company has delivered, "
             "so the next five years pay you nothing for owning it. AVGO is "
             "the shape: 94 quality, confirmed trend, support underfoot, and "
             "a model value less than a third of the price.",
     "rules": ["lquality:gte:85", "expected_cagr:lt:0"]},
    {"key": "chart_disagrees", "icon": "📉", "name": "Chart Disagrees",
     "group": "What would stop me",
     "desc": "Businesses the statements rate highly and price action does "
             "not. Technical shares no input with LQuality, so this is the "
             "second opinion dissenting — either the market is early or the "
             "fundamentals are stale, and it is worth deciding which before "
             "the next tranche.",
     "rules": ["lquality:gte:85", "technical:lte:45"]},
    {"key": "conviction_no_trade", "icon": "🚧", "name": "Own It, Can't Trade It",
     "group": "What would stop me",
     "desc": "Names the company verdict rates Core or Own where the priced "
             "trade does not pay — under 1.5R to the nearest target worth "
             "taking. Conviction and entry are different questions, and this "
             "is the list where they part company.",
     "rules": ["investment_status:eq:CORE", "rr:lt:1.5"]},
    {"key": "earnings_clear", "icon": "📅", "name": "Clear of Earnings",
     "group": "What would stop me", "desc": "Quality names with no report inside two weeks — the gap risk "
             "that no amount of setup quality offsets.",
     "rules": ["lquality:gte:85", "lt_days_to_earnings:gte:14"]},
)

PRESET_BY_KEY = {p["key"]: p for p in PRESETS}


def preset_rules(key: str) -> list[str]:
    preset = PRESET_BY_KEY.get(str(key or ""))
    return list(preset["rules"]) if preset else []


def preset_counts(results) -> dict:
    """How many rows each preset currently matches, computed live.

    The counts used to be recorded in each preset as a measured note. That
    was wrong in a way that took one engine change to expose: when
    STAGE4_BREAKDOWN was split into "confirmed breakdown" and "unconfirmed",
    Fallen Quality went from 10 matches to 0 while still advertising 10, and
    Quality at a Discount went from 20 to 31. A number written down beside a
    rule is a claim about a moving target.

    Flattens the universe ONCE and reuses it across every preset — 16 groups
    over one row set rather than 16 passes of flatten().
    """
    rows = [flatten(r) for r in results]
    out = {}
    for preset in PRESETS:
        conds = [c for c in (parse_rule(t) for t in preset["rules"]) if c]
        if not conds:
            out[preset["key"]] = None
            continue
        group = S.Group(op="AND", items=conds)
        out[preset["key"]] = sum(1 for row in rows if S.eval_group(row, group))
    return out
