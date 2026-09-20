"""
screen.py — rules over the CSP engine's own columns
====================================================
Lets the /csp page filter on what the engine computed — the company's
quality and margin of safety, the contract's delta and yield, how far the
premium clears its hurdle, liquidity, earnings distance and the three
scores — the same way /longterm filters the research library.

Built on the same seam as `core/longterm/screen.py`, and for the same
reasons: core.screener already has a tested Condition/Group evaluator with
operators, per-condition explanations and match scoring, and the only
thing it lacked was a description of these fields and rows shaped to
match. So this module supplies exactly two things:

    CSP_FIELDS   field specs for the engine's outputs
    flatten()    one evaluate() result -> one flat screenable row

Why these fields register into FIELD_BY_KEY but not into FIELDS
---------------------------------------------------------------
Same split longterm/screen.py documents. Registering into the lookup makes
the rule engine understand `adequacy >= 1.5`; NOT registering into FIELDS
keeps these out of the /screener page, where they would be offered over a
universe that has no values for them and every rule would come back "no
data" for every row.

The thing this page filters is not the thing /longterm filters
---------------------------------------------------------------
A long-term row is a company. A CSP row is a company AND a contract, and
most of the interesting screens cross the two: "rich premium on a merely
decent business" is the shape of the trade this engine is most often
asked about, and neither half expresses it. That is why `lquality` and
`adequacy` both live here rather than this page deferring to /longterm for
the company side.

Rows with no contract still flatten
------------------------------------
A rejection, or a name where no strike qualified, has None for every
contract field. That is deliberate: core.screener treats a missing value
as "no data" rather than as a failed comparison, so `adequacy >= 1.5`
excludes them without ever claiming their premium was inadequate — it
claims nothing, which is the truth.
"""

from __future__ import annotations

from stockanalysis.core import screener as S
from stockanalysis.core.csp.strike import DELTA_CLASSES as _DELTA_BANDS
from stockanalysis.core.longterm._common import f, s

NUM, BOOL, ENUM = S.NUM, S.BOOL, S.ENUM
UP, DOWN = S.UP, S.DOWN

ACTION_VALUES = ("SELL", "VERIFY", "SELL_DIP", "EVENT_RISK", "WAIT_IV",
                 "WAIT_LEVEL", "WATCH", "REJECT")
TIER_VALUES = ("Elite", "High Quality", "Watchlist", "Reject")
BAND_VALUES = ("UNDERVALUED", "FAIR", "OVERVALUED")
# Derived, not restated: strike.py owns the vocabulary, and a hand-copied
# list here would go stale the first time a band was renamed — silently,
# because an ENUM value that matches nothing simply returns no rows.
DELTA_CLASSES = tuple(name for _ceiling, name in _DELTA_BANDS)
SPREAD_VALUES = ("EXCELLENT", "ACCEPTABLE", "CAUTION", "REJECT", "UNKNOWN")
SPOT_VALUES = ("live", "stored")

CSP_FIELDS: tuple[S.Field, ...] = (
    # ── The company ─────────────────────────────────────────────────────
    S.Field("lquality", "LQuality", "Company", NUM, "lquality", "", UP,
            hint="The long-term engine's 100-point business score",
            decimals=0),
    S.Field("lq_tier", "Quality tier", "Company", ENUM, "lq_tier",
            values=TIER_VALUES),
    S.Field("valuation_band", "Valuation", "Company", ENUM, "valuation_band",
            values=BAND_VALUES),
    S.Field("margin_pp", "Growth gap", "Company", NUM, "margin_pp",
            "pp", DOWN,
            hint="Implied minus delivered growth. Negative is cheap — the "
                 "price demands less than the company has shown",
            decimals=0),
    S.Field("margin_pct", "Margin of safety", "Company", NUM, "margin_pct",
            "%", UP,
            hint="Discount to fair value, where the method produces one",
            decimals=0),
    S.Field("csp_price", "Price", "Company", NUM, "csp_price", "$", None,
            decimals=2),
    S.Field("dist_52w_high", "Off the 52-week high", "Company", NUM,
            "dist_52w_high", "%", None,
            hint="Negative is below the high. A put is a paid limit order, "
                 "so how far the stock has already fallen is half of "
                 "whether the strike is a price you want",
            decimals=1),
    S.Field("spot_source", "Price source", "Company", ENUM, "spot_source",
            values=SPOT_VALUES,
            hint="Whether spot was a live quote or the last research scan"),

    # ── The contract ────────────────────────────────────────────────────
    S.Field("strike", "Strike", "Contract", NUM, "strike", "$", None,
            decimals=2),
    S.Field("delta", "Delta", "Contract", NUM, "delta", "", DOWN,
            hint="Absolute value — lower is further from assignment",
            decimals=2),
    S.Field("delta_class", "Delta class", "Contract", ENUM, "delta_class",
            values=DELTA_CLASSES),
    S.Field("otm_pct", "Distance OTM", "Contract", NUM, "otm_pct", "%", UP,
            hint="How far below spot the strike sits", decimals=1),
    S.Field("dte", "Days to expiry", "Contract", NUM, "dte", "d", None,
            decimals=0),
    S.Field("credit", "Credit", "Contract", NUM, "credit", "$", UP,
            hint="Limit price per share — ×100 for the contract",
            decimals=2),
    S.Field("basis", "Cost basis if assigned", "Contract", NUM, "basis", "$",
            None, decimals=2),

    # ── The long-term buy zone ──────────────────────────────────────────
    # Carried from /longterm, never recomputed here. The pairing these
    # fields exist for: a strike inside the zone the company engine would
    # buy in is the strongest thing that can be said for a cash-secured
    # put, and until these were on the row the two halves of that sentence
    # lived on different pages.
    S.Field("in_buy_zone", "Price in the buy zone", "Buy zone", BOOL,
            "in_buy_zone",
            hint="Today's price is inside the zone /longterm would buy in"),
    S.Field("strike_in_zone", "Strike inside the buy zone", "Buy zone", BOOL,
            "strike_in_zone",
            hint="Assignment lands inside the band. Narrower than "
                 "'at or below' — see that field first"),
    # The field that actually answers "would assignment be a price I want".
    # `strike_in_zone` requires the strike to land INSIDE the band, which
    # misses the better case entirely: a strike BELOW the zone puts you in
    # cheaper than the engine's own buy price. APP is the shape — strike
    # $300 against a $320-332 zone, 6.7% under the cheap end — and it read
    # False. Inside the zone is good; under it is better, and one test has
    # to cover both.
    S.Field("strike_at_or_below_zone", "Strike at or below the buy zone",
            "Buy zone", BOOL, "strike_at_or_below_zone",
            hint="Assignment puts you in at, or cheaper than, the zone the "
                 "long-term engine would buy in. The strongest thing a "
                 "cash-secured put can claim"),
    S.Field("strike_vs_zone_pct", "Strike vs the zone top", "Buy zone", NUM,
            "strike_vs_zone_pct", "%", DOWN,
            hint="How far the strike sits above the top of the zone. "
                 "Negative means at or below it — the same test as the flag "
                 "above, as a number you can sort and threshold",
            decimals=1),
    S.Field("near_buy_zone", "At or near the buy zone", "Buy zone", BOOL,
            "near_buy_zone",
            hint="Inside it, or within 5% above it"),
    S.Field("buy_zone_low", "Buy zone — low", "Buy zone", NUM,
            "buy_zone_low", "$", None, decimals=2),
    S.Field("buy_zone_high", "Buy zone — high", "Buy zone", NUM,
            "buy_zone_high", "$", None, decimals=2),
    S.Field("buy_zone_gap", "Gap to the buy zone", "Buy zone", NUM,
            "buy_zone_gap", "%", UP,
            hint="How far price sits above the zone. Negative means the "
                 "zone is below and price has that far to fall",
            decimals=1),
    S.Field("zone_above_spot", "Price below the whole zone", "Buy zone",
            BOOL, "zone_above_spot",
            hint="The band sits entirely above the price — overhead supply "
                 "the stock has already fallen through, not a level to buy "
                 "at. The strike flags read 'not applicable' here"),
    S.Field("buy_zone_kind", "Buy zone kind", "Buy zone", ENUM,
            "buy_zone_kind", values=("investment", "technical"),
            hint="An investment zone is one valuation and quality endorse; "
                 "a technical band is only where support sits"),

    # ── The allocation view ─────────────────────────────────────────────
    # The eight fields the disposition layer adds. These rank on "would I
    # own this at this effective price", which is a different — and for a
    # cash-secured put, a better — question than "what does this pay".
    S.Field("effective_entry", "Effective entry", "Decision", NUM,
            "effective_entry", "$", None,
            hint="Strike minus the credit. What assignment actually costs "
                 "you, and the only price on the row you would end up "
                 "owning at", decimals=2),
    S.Field("entry_vs_zone_pct", "Entry vs buy zone", "Decision", NUM,
            "entry_vs_zone_pct", "%", DOWN,
            hint="Effective entry against the TOP of the buy zone. "
                 "Negative is what you want — assignment lands at or below "
                 "the price you said you wanted the stock at",
            decimals=1),
    S.Field("entry_in_zone", "Effective entry in the zone", "Decision", BOOL,
            "entry_in_zone",
            hint="The single best test on the page for a cash-secured put"),
    S.Field("dist_to_buy_zone_pct", "Distance to buy zone", "Decision", NUM,
            "dist_to_buy_zone_pct", "%", DOWN,
            hint="How far TODAY'S price sits above the zone. More "
                 "actionable than distance from the 52-week high",
            decimals=1),
    S.Field("thesis_status", "Thesis", "Decision", ENUM, "thesis_status",
            values=("INTACT", "MARKDOWN", "REPRICING", "BROKEN",
                    "UNMEASURED"),
            hint="WHY the stock is down, not how far. A Stage 4 markdown "
                 "with the price in the zone is a different situation from "
                 "a trend the market is repricing"),
    S.Field("ownership_score", "Ownership score", "Decision", NUM,
            "ownership_score", "", UP,
            hint="35% quality / 25% effective entry vs zone / 15% "
                 "valuation / 10% technical / 10% premium / 5% liquidity. "
                 "Deliberately NOT csp_score, which answers whether the "
                 "contract is worth selling", decimals=0),
    S.Field("final_action", "Decision", "Decision", ENUM, "final_action",
            values=("BUY_NOW", "SELL_CSP", "WAIT_FOR_BETTER_STRIKE",
                    "WAIT_FOR_BUY_ZONE", "THESIS_CHECK", "AVOID"),
            hint="The capital-allocation verdict. A rejection now says "
                 "wait, check the thesis, or genuinely avoid"),

    # ── What it pays ────────────────────────────────────────────────────
    # `adequacy` is the field this page exists to screen on. Yield alone
    # ranks a wide, event-priced contract on a mediocre business above a
    # tight one on a good business; adequacy is that yield measured
    # against the hurdle THIS name and THIS delta had to clear.
    S.Field("adequacy", "Premium vs required", "Premium", NUM, "adequacy",
            "×", UP,
            hint="Yield ÷ the hurdle it had to clear. 1.0 is exactly paid; "
                 "this is the honest 'rich premium' test",
            decimals=2),
    S.Field("yield_pct", "Period yield", "Premium", NUM, "yield_pct", "%", UP,
            hint="Credit ÷ collateral over the life of the contract",
            decimals=2),
    S.Field("annualised", "Annualised", "Premium", NUM, "annualised", "%", UP,
            hint="A comparison aid across DTEs, not a return anyone earns",
            decimals=0),
    S.Field("iv_vs_hv", "IV vs realised", "Premium", NUM, "iv_vs_hv", "×", UP,
            hint="Above 1 the option is priced richer than the stock has "
                 "actually moved", decimals=2),
    S.Field("iv_rank", "IV rank", "Premium", NUM, "iv_rank", "", UP,
            hint="Withheld until 30 stored observations exist", decimals=0),

    # ── Whether it can be traded ────────────────────────────────────────
    S.Field("liquidity", "Liquidity", "Liquidity", NUM, "liquidity", "", UP,
            decimals=0),
    S.Field("spread_pct", "Spread", "Liquidity", NUM, "spread_pct", "%", DOWN,
            hint="Percent of mid — above 10% the contract is not tradable",
            decimals=1),
    S.Field("spread_verdict", "Spread verdict", "Liquidity", ENUM,
            "spread_verdict", values=SPREAD_VALUES),
    S.Field("open_interest", "Open interest", "Liquidity", NUM,
            "open_interest", "", UP, decimals=0),

    # ── Risk ────────────────────────────────────────────────────────────
    S.Field("earnings_days", "Days to earnings", "Risk", NUM, "earnings_days",
            "d", UP, decimals=0),
    S.Field("earnings_inside", "Earnings inside expiry", "Risk", BOOL,
            "earnings_inside"),
    S.Field("cushion", "Move cushion", "Risk", NUM, "cushion", "×", UP,
            hint="Strike distance ÷ the market's expected move. Below 1 the "
                 "strike sits inside what the market expects", decimals=2),
    S.Field("downside_pct", "Cushion to support", "Risk", NUM,
            "downside_pct", "%", UP,
            hint="How far the strike sits above the nearest support below "
                 "it", decimals=1),
    S.Field("premium_per_atr", "Credit per ATR", "Risk", NUM,
            "premium_per_atr", "×", UP,
            hint="Premium measured in how far the stock actually moves in "
                 "a day", decimals=2),

    # ── Verdict ─────────────────────────────────────────────────────────
    S.Field("csp_score", "CSP score", "Verdict", NUM, "csp_score", "", UP,
            hint="stock × option × risk — a product, so neither half "
                 "rescues the other", decimals=0),
    S.Field("stock_score", "Stock score", "Verdict", NUM, "stock_score", "",
            UP, decimals=0),
    S.Field("option_score", "Option score", "Verdict", NUM, "option_score",
            "", UP, decimals=0),
    S.Field("risk_score", "Risk score", "Verdict", NUM, "risk_score", "", UP,
            decimals=0),
    S.Field("action", "Action", "Verdict", ENUM, "action",
            values=ACTION_VALUES),
    S.Field("has_contract", "Has a contract", "Verdict", BOOL,
            "has_contract",
            hint="False for rejections and names where no strike qualified"),
)

CSP_FIELD_BY_KEY = {f.key: f for f in CSP_FIELDS}

# Registered into the lookup so the rule engine resolves these keys, and
# NOT into S.FIELDS — see the module docstring.
S.FIELD_BY_KEY.update(CSP_FIELD_BY_KEY)

FIELD_GROUPS = ("Decision", "Company", "Contract", "Buy zone", "Premium",
                "Liquidity", "Risk", "Verdict")


def flatten(row: dict) -> dict:
    """One stored CSP row -> a flat row core.screener can test.

    Reads the SNAPSHOT's shape, not evaluate()'s locals, because that is
    what the page actually filters — rejected rows have been slimmed by
    store.save() and must still screen on what survived.
    """
    elig = row.get("eligibility") or {}
    disc = row.get("discount") or {}
    ret = row.get("returns") or {}
    adq = row.get("adequacy") or {}

    # A rejected row has no `chosen` — its premium lives in the reference
    # chain's best FILLABLE strike. Falling back to it is what makes the
    # premium filters reach the rejections at all, which is the whole point
    # of pricing them: "rich premium" over a table where the rich premiums
    # are the rejections would otherwise return nothing.
    #
    # `is_reference` rides along so the caller can tell the two apart —
    # they are not the same claim. A chosen strike is one the engine would
    # trade; a reference strike is only what the board is quoting.
    chosen = row.get("chosen") or {}
    ref_best = ((row.get("reference") or {}).get("best")) or {}
    is_reference = not chosen and bool(ref_best)
    if is_reference:
        chosen = ref_best
        # The reference chain computes its own returns per strike rather
        # than a `returns` block; read them off the strike itself. Its
        # adequacy is computed against the same hurdle a chosen contract
        # faces (see engine._reference_chain), so "rich premium" means one
        # thing across the whole table.
        ret = ret or ref_best
        adq = adq or {"ratio": ref_best.get("adequacy")}
    liq = row.get("liquidity") or {}
    earn = row.get("earnings_distance") or {}
    cush = row.get("move_cushion") or {}
    down = row.get("downside") or {}
    assign = row.get("assignment") or {}
    ivr = row.get("iv_rank") or {}
    final = row.get("final") or {}

    # Quality arrives by two routes: the full row carries it inside
    # `eligibility`, and a slimmed rejection carries the scalar the store
    # was taught to keep. Reading both is what lets one rule screen the
    # whole table rather than only the un-slimmed half.
    lq = f(elig.get("quality_score"))
    if lq is None:
        lq = f(row.get("lquality"))
    tier = s(elig.get("quality_tier")) or s(row.get("lq_tier"))

    spot = f(row.get("price"))
    strike = f(chosen.get("strike"))
    zone = row.get("buy_zone") or {}
    zl, zh = f(zone.get("low")), f(zone.get("high"))

    # The allocation view. Computed here rather than stored so it always
    # reflects the CURRENT thresholds — it is an interpretation of the row,
    # not a fact about it, and a stored interpretation goes stale silently
    # the first time a weight moves.
    from stockanalysis.core.csp import disposition as DISP
    d = DISP.compute(row)

    return {
        "ticker": row.get("ticker"),
        "name": row.get("name"),
        "_result": row,

        "effective_entry": d["effective_entry"],
        "entry_vs_zone_pct": d["entry_vs_zone_pct"],
        "entry_in_zone": d["entry_in_zone"],
        "dist_to_buy_zone_pct": d["dist_to_buy_zone_pct"],
        "thesis_status": d["thesis_status"],
        "ownership_score": d["ownership_score"],
        "final_action": d["final_action"],

        "lquality": lq,
        "lq_tier": tier,
        "valuation_band": s(elig.get("valuation_band")) or s(disc.get("band")),
        "margin_pp": f(disc.get("growth_gap_pp")),
        "margin_pct": f(disc.get("margin_pct")),
        "csp_price": spot,
        "dist_52w_high": f(row.get("dist_52w_high")),
        "spot_source": s(row.get("spot_source")) or "stored",

        "buy_zone_low": zl,
        "buy_zone_high": zh,
        "buy_zone_gap": f(zone.get("distance_pct")),
        "buy_zone_kind": s(zone.get("kind")),
        "in_buy_zone": (None if not (zl and zh and spot)
                        else zl <= spot <= zh),
        # The pairing this page exists to surface: assignment would put you
        # in AT THE STRIKE, so "is the strike in the zone" is the question,
        # and it is a different one from "is the price in the zone".
        "strike_in_zone": (None if not (zl and zh and strike)
                           or zone.get("above_spot")
                           else zl <= strike <= zh),
        # None, not False, when the whole band sits ABOVE spot: the
        # question "would assignment land at or below the zone" does not
        # apply to a level price has already fallen through. Answering
        # `True` there would be trivially true of every strike and would
        # put INTU — down 51.6%, straight through its 200 MA — at the top
        # of a screen for cheap assignments.
        "strike_at_or_below_zone": (None if not (zh and strike)
                                    or zone.get("above_spot")
                                    else strike <= zh),
        # Signed against the TOP of the band, so "<= 0" is exactly
        # "at or below the zone" and the flag and the number cannot
        # disagree about the same row.
        "strike_vs_zone_pct": (None if not (zh and strike)
                               else round((strike / zh - 1) * 100, 2)),
        "near_buy_zone": zone.get("near"),
        "zone_above_spot": zone.get("above_spot"),

        "strike": strike,
        "is_reference": is_reference,
        # Absolute: a put's delta is negative and "delta <= 0.20" is what
        # anyone means, not "delta <= -0.20".
        "delta": (abs(f(chosen.get("delta")))
                  if chosen.get("delta") is not None else None),
        "delta_class": s(chosen.get("delta_class")),
        "otm_pct": (round((1 - strike / spot) * 100, 2)
                    if strike and spot else None),
        "dte": f(chosen.get("dte")),
        "credit": f(chosen.get("limit_price")),
        "basis": f(assign.get("basis")),

        # Adequacy is only ever computed for a CHOSEN contract — the
        # reference chain deliberately carries no verdict, so there is no
        # hurdle to measure against. None, not zero: "not assessed" and
        # "did not clear" are different answers.
        "adequacy": f(adq.get("ratio")),
        "yield_pct": f(ret.get("yield_pct")),
        "annualised": f(ret.get("annualised")),
        # volatility.iv_vs_hv() always returns {ratio, label, verdict, …},
        # never a bare float — the same shape csp_view's row renderer reads.
        "iv_vs_hv": f((row.get("iv_vs_hv") or {}).get("ratio")),
        "iv_rank": f(ivr.get("rank")) if ivr.get("available") else None,

        "liquidity": f(chosen.get("liquidity")) or f(liq.get("score")),
        "spread_pct": f(chosen.get("spread_pct")) or f(liq.get("spread_pct")),
        "spread_verdict": s(chosen.get("spread_verdict")
                            or liq.get("spread_verdict")),
        "open_interest": f(chosen.get("open_interest")),

        "earnings_days": f(earn.get("days")),
        "earnings_inside": earn.get("inside") if earn else None,
        "cushion": f(cush.get("ratio")),
        "downside_pct": f(down.get("technical_cushion_pct")),
        "premium_per_atr": f(down.get("per_atr")),

        "csp_score": f(row.get("csp_score")),
        "stock_score": f(row.get("stock_score")),
        "option_score": f(row.get("option_score")),
        "risk_score": f(row.get("risk_score")),
        "action": s(final.get("key")),
        "has_contract": bool(chosen),
    }


# ─────────────────────────────────────────────────────────────────────────
# RULE PARSING — rules live in the URL, like /longterm's
# ─────────────────────────────────────────────────────────────────────────

def parse_rule(text: str) -> S.Condition | None:
    """"adequacy:gte:1.5" -> Condition. None when unparseable, so one bad
    rule in a pasted URL drops itself rather than erroring the page."""
    parts = (text or "").split(":")
    if len(parts) < 2:
        return None
    key, op = parts[0].strip(), parts[1].strip().lower()
    spec = CSP_FIELD_BY_KEY.get(key)
    if spec is None or op not in S.OPS_FOR_KIND.get(spec.kind, ()):
        return None
    raw = ":".join(parts[2:]).strip() if len(parts) > 2 else ""
    value: object = raw
    value2 = None
    if spec.kind == NUM:
        try:
            if op in ("between", "within"):
                a, _, b = raw.partition(",")
                value, value2 = float(a), float(b)
            else:
                value = float(raw)
        except ValueError:
            return None
    elif spec.kind == BOOL:
        value = raw.lower() in ("1", "true", "yes", "on")
    return S.Condition(field=key, op=op, value=value, value2=value2)


def describe(cond: S.Condition) -> str:
    """A rule as a sentence, for the removable pills."""
    spec = CSP_FIELD_BY_KEY.get(cond.field)
    label = spec.label if spec else cond.field
    unit = spec.unit if spec else ""
    words = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "is",
             "ne": "is not", "between": "between", "within": "within"}
    op = words.get(cond.op, cond.op)
    if cond.op == "between":
        return f"{label} {op} {cond.value}{unit} and {cond.value2}{unit}"
    if isinstance(cond.value, bool):
        return f"{label} is {'yes' if cond.value else 'no'}"
    return f"{label} {op} {cond.value}{unit}"


def apply_rules(rows, rule_texts, op: str = "AND"):
    """Filter stored CSP rows by the rules. Returns (kept, conditions, stats).

    `stats` is core.screener's per-condition breakdown — how many rows each
    rule passed and how many had no data for it — which is what turns
    "0 matches" from a dead end into a diagnosis. It matters more here than
    on /longterm: most rows have no contract at all, so a premium rule
    legitimately finds "no data" for the majority and the page has to be
    able to say so.
    """
    conds = [c for c in (parse_rule(t) for t in (rule_texts or [])) if c]
    if not conds:
        return list(rows), [], []
    group = S.Group(op="OR" if str(op).upper() == "OR" else "AND",
                    items=list(conds))
    flat = [flatten(r) for r in rows]
    stats = S.condition_stats(flat, group)
    kept = [row["_result"] for row in flat if S.eval_group(row, group, [])]
    return kept, conds, stats


# ─────────────────────────────────────────────────────────────────────────
# PRESETS — the screens someone selling puts actually runs
# ─────────────────────────────────────────────────────────────────────────
# Grouped by the question asked. "What pays well" first because that is
# what the page is opened for, then the two questions that decide whether
# the payment is worth taking.
#
# Every threshold here is expressed against ADEQUACY rather than raw yield
# wherever it can be. A 40% annualised on a name whose hurdle is 35% is a
# thinner trade than a 14% on a name whose hurdle is 9%, and a preset
# built on the headline number would rank them the wrong way round.

PRESET_GROUPS = ("What to do", "What pays well",
                 "What is safe to be assigned", "What would stop me")

PRESETS: tuple[dict, ...] = (
    # ── What to do ──────────────────────────────────────────────────────
    # The disposition layer's five states, as one-click screens. These come
    # first because they are the question the page is for; everything below
    # is a way of interrogating one of them.
    {"key": "act_sell_csp", "icon": "🟢", "name": "Sell the Put",
     "group": "What to do",
     "desc": "Assignment would put you in at or below your buy zone, on a "
             "business worth owning, with a premium that clears its "
             "hurdle. The whole framework in one screen.",
     "rules": ["final_action:eq:SELL_CSP"]},
    {"key": "act_buy_now", "icon": "🟢", "name": "Buy the Shares",
     "group": "What to do",
     "desc": "Quality inside its buy zone with no contract worth selling. "
             "The screen that says stop waiting for a perfect option and "
             "just accumulate.",
     "rules": ["final_action:eq:BUY_NOW"]},
    {"key": "act_better_strike", "icon": "🟡", "name": "Wait for a Better Strike",
     "group": "What to do",
     "desc": "The price is right and the contract is not. A lower strike "
             "or a bigger credit would put the effective entry inside the "
             "zone — worth re-checking as the chain moves.",
     "rules": ["final_action:eq:WAIT_FOR_BETTER_STRIKE"]},
    {"key": "act_wait_zone", "icon": "🟡", "name": "Wait for the Price",
     "group": "What to do",
     "desc": "Good business, wrong price: the effective entry is still "
             "above the buy zone. NVDA is the shape — Elite, and the put "
             "pays you to buy it 5.3% above where you said you wanted it.",
     "rules": ["final_action:eq:WAIT_FOR_BUY_ZONE"]},
    {"key": "act_thesis", "icon": "🟠", "name": "Thesis Check",
     "group": "What to do",
     "desc": "The price is attractive and something in the business or the "
             "trend has deteriorated. INOD is the shape: quality 93 with "
             "the engine calling the discount a repricing rather than a "
             "sale. Decide which before selling anything.",
     "rules": ["final_action:eq:THESIS_CHECK"]},
    {"key": "act_ready", "icon": "🎯", "name": "Effective Entry In the Zone",
     "group": "What to do",
     "desc": "Strike minus credit lands at or below the top of the buy "
             "zone, on a business worth owning. The single best test on "
             "the page, independent of what the premium happens to be.",
     "rules": ["entry_in_zone:eq:true", "lquality:gte:80"]},

    # ── What pays well ──────────────────────────────────────────────────
    {"key": "rich_premium", "icon": "💰", "name": "Rich Premium",
     "group": "What pays well",
     "desc": "The premium clears its own hurdle by half again. Measured "
             "against what THIS name at THIS delta had to pay, not against "
             "a flat yield — which is the only version of 'rich' that "
             "survives comparing a volatile name to a quiet one.",
     "rules": ["adequacy:gte:1.5"]},
    {"key": "rich_medium_quality", "icon": "⚖️",
     "name": "Rich Premium, Medium Quality",
     "group": "What pays well",
     "desc": "A well-paid contract on a decent-but-not-elite business. The "
             "trade this page is most often asked about, and the one a "
             "single blended score hides: the premium is doing the work, "
             "so the business only has to be good enough to be assigned "
             "into without regret.",
     "rules": ["adequacy:gte:1.5", "lquality:between:70,85"]},
    {"key": "iv_rich", "icon": "🌊", "name": "Option Priced Above the Stock",
     "group": "What pays well",
     "desc": "Implied volatility above what the stock has actually "
             "delivered — you are being paid for movement the shares have "
             "not been making. The cleanest reason a premium is generous "
             "rather than merely large.",
     "rules": ["iv_vs_hv:gte:1.15", "adequacy:gte:1.0"]},
    {"key": "high_annualised", "icon": "📈", "name": "High Annualised",
     "group": "What pays well",
     "desc": "20%+ annualised on collateral, on a contract that can "
             "actually be filled. Annualised is a comparison aid across "
             "DTEs and not a return anyone collects twelve times, so the "
             "liquidity rule is doing as much work here as the yield one.",
     "rules": ["annualised:gte:20", "liquidity:gte:60"]},

    {"key": "elite_rich_reject", "icon": "💎",
     "name": "Elite Business, 100%+ Annualised",
     "group": "What pays well",
     "desc": "Top-decile quality paying over 100% annualised. Almost all of "
             "these are REJECTED — a business that good is rarely cheap, so "
             "the premium is the market pricing the same expensiveness the "
             "valuation gate rejected it for. Two names live: ALAB (94, "
             "106%) and CRDO (98, 121%), both turned down on price. The "
             "list is worth seeing precisely because the engine will not "
             "offer it.",
     "rules": ["lquality:gt:90", "annualised:gt:100"]},
    {"key": "rich_reject", "icon": "🔥", "name": "Richest Rejected Premiums",
     "group": "What pays well",
     "desc": "The rejections that pay best. These failed the company gate "
             "and still do — nothing here is a trade the engine offers — "
             "but their premiums are measured against the same hurdle a "
             "chosen contract faces, so 'rich' means the same thing. The "
             "reason each was thrown out sits beside its yield.",
     "rules": ["action:eq:REJECT", "adequacy:gte:2.0"]},

    # ── What is safe to be assigned ─────────────────────────────────────
    {"key": "strike_at_zone", "icon": "🎯",
     "name": "Assigned At or Below the Buy Zone",
     "group": "What is safe to be assigned",
     "desc": "Assignment puts you in at, or cheaper than, the price the "
             "Long-Term engine would buy at. The strongest thing a "
             "cash-secured put can claim — and a different test from 'the "
             "price is in the zone', because the strike is where you "
             "actually end up. APP is the shape: strike $300 against a "
             "$320-332 zone, so being assigned is 6.7% better than the "
             "engine's own buy price.",
     "rules": ["strike_at_or_below_zone:eq:true"]},
    {"key": "strike_in_buy_zone", "icon": "◎",
     "name": "Strike Inside the Band",
     "group": "What is safe to be assigned",
     "desc": "The stricter version: assignment lands inside the band rather "
             "than under it. Fewer names, and not better ones — a strike "
             "below the zone is the better trade. Kept for reading the "
             "structure rather than for screening.",
     "rules": ["strike_in_zone:eq:true"]},
    {"key": "at_buy_zone", "icon": "📍", "name": "Price At or Near the Zone",
     "group": "What is safe to be assigned",
     "desc": "Inside the buy zone, or within 5% above it. The names where "
             "the company engine is already interested and a put pays you "
             "to wait for the last few percent.",
     "rules": ["near_buy_zone:eq:true"]},
    {"key": "investment_zone", "icon": "💠", "name": "Zone Valuation Endorses",
     "group": "What is safe to be assigned",
     "desc": "A qualifying INVESTMENT zone rather than a technical band — "
             "valuation, expected return, quality and support all agree at "
             "that price. Rare by construction.",
     "rules": ["buy_zone_kind:eq:investment"]},
    {"key": "beaten_down_quality", "icon": "📉",
     "name": "Quality Well Off Its High",
     "group": "What is safe to be assigned",
     "desc": "A good business 20%+ below its 52-week high, with a premium "
             "that clears its hurdle. A put here is a paid limit order at "
             "a price the market has already come down to.",
     "rules": ["lquality:gte:80", "dist_52w_high:lte:-20",
               "adequacy:gte:1.0"]},
    {"key": "core_conservative", "icon": "🛡️",
     "name": "Elite Business, Conservative Strike",
     "group": "What is safe to be assigned",
     "desc": "The trade you would be content to have assigned: a top-tier "
             "business, a strike well out of the money, and a premium that "
             "still clears its hurdle. The shortest list here by design.",
     "rules": ["lquality:gte:85", "delta:lte:0.20", "adequacy:gte:1.0"]},
    {"key": "deep_cushion", "icon": "🪂", "name": "Outside the Expected Move",
     "group": "What is safe to be assigned",
     "desc": "The strike sits more than 1.5 expected moves below spot. "
             "Delta encodes this too, but opaquely — this states it in the "
             "market's own units.",
     "rules": ["cushion:gte:1.5", "has_contract:eq:true"]},
    {"key": "discount_basis", "icon": "🏷️", "name": "Assigned at a Discount",
     "group": "What is safe to be assigned",
     "desc": "Undervalued on the reverse DCF, and the cost basis after the "
             "credit is further below spot again. Being assigned here is "
             "the outcome you wanted, not the one you tolerated.",
     "rules": ["valuation_band:eq:UNDERVALUED", "otm_pct:gte:5"]},
    {"key": "clean_window", "icon": "🗓️", "name": "No Earnings in the Window",
     "group": "What is safe to be assigned",
     "desc": "No print before expiry. A later expiry never clears an "
             "earnings date — every expiry past it contains it — so this "
             "is a property of the calendar rather than something a longer "
             "contract fixes.",
     "rules": ["earnings_inside:eq:false", "has_contract:eq:true"]},

    # ── What would stop me ──────────────────────────────────────────────
    {"key": "thin_market", "icon": "🕳️", "name": "Too Wide to Fill",
     "group": "What would stop me",
     "desc": "The spread gates the liquidity score rather than feeding it, "
             "so these are contracts whose quoted yield you would give "
             "back at the fill. Worth seeing precisely because the headline "
             "premium looks fine.",
     "rules": ["spread_pct:gte:10"]},
    {"key": "event_priced", "icon": "⚡", "name": "Premium Is Event-Priced",
     "group": "What would stop me",
     "desc": "A generous premium with the print inside the expiry. The "
             "premium is richest exactly because the gap risk is real, and "
             "this is the list where a fat number is a warning rather than "
             "an opportunity.",
     "rules": ["earnings_inside:eq:true", "adequacy:gte:1.3"]},
    {"key": "paid_too_little", "icon": "🪫", "name": "Not Paid Enough",
     "group": "What would stop me",
     "desc": "A contract that exists and does not clear its hurdle. Kept as "
             "a screen because 'why is this not a SELL' is answered by a "
             "number here rather than by an adjective.",
     "rules": ["has_contract:eq:true", "adequacy:lt:1.0"]},
    {"key": "stale_price", "icon": "🕰️", "name": "Priced Off the Last Scan",
     "group": "What would stop me",
     "desc": "Spot could not be quoted live, so every strike, delta and "
             "distance on the row was computed from whatever the research "
             "library last recorded. Rare, and worth knowing when it "
             "happens.",
     "rules": ["spot_source:eq:stored"]},
)


def preset_counts(rows) -> dict:
    """How many rows each preset currently matches, computed live.

    Never recorded on the preset. /longterm learned this the hard way: a
    count written beside a rule is a claim about a moving target, and one
    engine change left two presets advertising matches they no longer had.
    """
    flat = [flatten(r) for r in rows]
    out = {}
    for preset in PRESETS:
        conds = [c for c in (parse_rule(t) for t in preset["rules"]) if c]
        if not conds:
            out[preset["key"]] = None
            continue
        group = S.Group(op="AND", items=conds)
        out[preset["key"]] = sum(1 for row in flat
                                 if S.eval_group(row, group))
    return out


# ─────────────────────────────────────────────────────────────────────────
# REJECTION BUCKETS — one classifier, two callers
# ─────────────────────────────────────────────────────────────────────────
# The page groups rejections by the rule that fired, and the engine spends
# its reference-chain budget across those same groups. Those must be the
# same classification or the budget would be allocated to buckets the page
# then draws differently — so it lives here rather than as a closure in the
# view, and both sides call it.
#
# Classified from the reason TEXT because that is what both callers have:
# the engine holds `eligibility["blockers"][0]` before it evaluates, and
# the page holds `final["why"]` after. They are the same string.

REJECT_BUCKETS = ("Quality below the floor", "Overvalued",
                  "Stage 4 markdown", "Trend broken", "Options too illiquid",
                  "Earnings inside the expiry", "No listed options",
                  "Cost basis not worth owning", "Premium is event-priced",
                  "Other")


def reject_bucket(why: str) -> str:
    """Which rule threw this name out, as one of REJECT_BUCKETS."""
    w = (why or "").lower()
    if "overvalued" in w:
        return "Overvalued"
    # Checked after "overvalued" on purpose: the valuation blocker mentions
    # neither, but the quality one says "LQuality 62 is below the 70 floor"
    # and a bare "quality" test would also catch "quality built on 40% of
    # inputs", which is the same bucket anyway.
    if "lquality" in w or "quality" in w:
        return "Quality below the floor"
    if "stage 4" in w:
        return "Stage 4 markdown"
    # "wide" and "fill" catch the strike-stage rejection, whose sentence is
    # "the right strike exists ($120.00, delta 0.15) but is quoted 84% wide
    # — too wide to fill reliably". Matching only "spread"/"fillable" put
    # all 28 of those in Other, which was the least accurate bucket on the
    # page: they are the most interesting rejections there are — a good
    # company, the right strike already identified, and only the quote in
    # the way — and "Other" said nothing about any of that.
    if ("spread" in w or "liquidity" in w or "fillable" in w
            or "wide" in w or "fill" in w):
        return "Options too illiquid"
    if "earnings" in w:
        return "Earnings inside the expiry"
    if "trend" in w:
        return "Trend broken"
    if "no listed options" in w:
        return "No listed options"
    if "happy owning" in w:
        return "Cost basis not worth owning"
    if "event pricing" in w:
        return "Premium is event-priced"
    return "Other"


def spread_reference_budget(candidates, budget: int) -> set:
    """Which rejected tickers get a chain, given a budget of `budget` fetches.

    `candidates` is [(ticker, why, quality)]. Returns a set of tickers.

    Round-robin ACROSS buckets, quality-ordered WITHIN each. Spending the
    budget purely on quality — which is what it did first — put all 25
    fetches on names rejected for being expensive, because those are the
    only high-quality rejections there are. The 437 names below the quality
    floor got nothing, so a whole category of the page stayed blank and the
    reason looked like a bug rather than a budget.
    """
    if budget <= 0 or not candidates:
        return set()
    buckets: dict[str, list] = {}
    for ticker, why, quality in candidates:
        if not ticker:
            continue
        buckets.setdefault(reject_bucket(why), []).append(
            (-(quality or 0), str(ticker).upper()))
    for items in buckets.values():
        items.sort()

    # Largest bucket first so a budget too small to cover everything still
    # lands where the most names are.
    order = sorted(buckets, key=lambda b: -len(buckets[b]))
    picked: set = set()
    i = 0
    while len(picked) < budget and any(buckets[b] for b in order):
        bucket = order[i % len(order)]
        if buckets[bucket]:
            picked.add(buckets[bucket].pop(0)[1])
        i += 1
    return picked
