"""
Steps 2 and 5 — support levels, and choosing the strike against them.

The organising idea: **the strike is a limit order, so it belongs at a
price you independently wanted to buy at.** Picking a strike by delta
first inverts that — it optimises for not being assigned, which is a
different trade wearing the same name. Delta is used here as a
CONSTRAINT (is this strike far enough out to be a sale, close enough to
be worth selling?) rather than as the selector.

Ordering, therefore:

    1. Collect support levels the long-term engine already computed.
    2. Take the strongest support below spot as the anchor.
    3. Take every listed strike at or below that anchor.
    4. Keep those whose |delta| lands in the acceptable band.
    5. Rank what survives on how well it satisfies the three conditions
       the spec names — below support, below the fundamental buy zone,
       and carrying a real margin of safety.

If nothing satisfies them, the answer is NO TRADE. That is a normal,
frequent outcome for a quality name at a full price, and returning it is
the point: the alternative is drifting the strike up until something
"qualifies", which is how a discipline becomes a rationalisation.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f, s

# The considered range. Every strike from 0.10 to 0.30 is evaluated and
# shown; the bands below name what each one MEANS rather than hiding the
# ones outside a narrow default. A 0.10-delta put on an elite name at a
# deep discount is a legitimate conservative sale, and a 0.30 is a
# legitimate aggressive one — what matters is that the page says which
# is which instead of silently picking for you.
DELTA_MIN = 0.10
DELTA_MAX = 0.30
DELTA_HARD_MAX = 0.30
DELTA_HARD_MIN = 0.10

# Where the engine prefers to land absent a reason to move.
CORE_DELTA_LOW = 0.20
CORE_DELTA_HIGH = 0.25

DELTA_CLASSES = (
    (0.15, "Conservative"),
    (0.20, "Conservative/Moderate"),
    (0.25, "Core CSP"),
    (0.30, "Aggressive"),
    (1.00, "High assignment risk"),
)


def classify_delta(delta) -> str:
    """What a delta MEANS, in the spec's own vocabulary."""
    if delta is None:
        return "—"
    d = abs(delta)
    for ceiling, name in DELTA_CLASSES:
        if d <= ceiling:
            return name
    return "High assignment risk"


# Below this a contract is not a candidate at all — see `select`.
MIN_LIQUIDITY = 40

# How much confidence each level type carries as SUPPORT. These are the
# same levels the /longterm page draws; the weights differ because what
# makes a good long-term entry (the 8 EMA, on a shallow pullback) is not
# what makes a good put strike (a level price has repeatedly refused to
# go through).
LEVEL_CONFIDENCE = {
    "200MA":    90,
    "50MA":     80,
    "BREAKOUT": 75,
    "21EMA":    55,
    "8EMA":     35,
}
LEVEL_LABEL = {
    "200MA": "200 MA", "50MA": "50 MA", "BREAKOUT": "prior breakout",
    "21EMA": "21 EMA", "8EMA": "8 EMA",
}


def support_levels(result: dict, limit: int = 3) -> list[dict]:
    """Up to `limit` support levels below spot, best first, each scored.

    Confidence starts from the level type and is adjusted by what the
    engine observed about it: a level that has actually been tested and
    held is worth more than the same moving average untested, and a
    volume-confirmed shelf more than either.
    """
    price = f(result.get("price"))
    pullback = result.get("pullback") or {}
    conf = result.get("confluence") or {}
    if not price:
        return []

    # Which levels the confluence engine says are currently agreeing.
    hits = {s(h.get("name")): h for h in (conf.get("hits") or [])}

    out = []
    for lv in (pullback.get("levels") or []):
        lp = f(lv.get("price"))
        key = s(lv.get("key")) or s(lv.get("zone"))
        if lp is None or lp >= price:
            continue                       # only support BELOW spot

        base = LEVEL_CONFIDENCE.get((s(lv.get("zone")) or "").upper(), 40)
        notes = []

        if lv.get("held"):
            base += 5; notes.append("held on test")
        if lv.get("supporting"):
            base += 5; notes.append("currently supporting")
        for name, h in hits.items():
            if name and key and (key in name or name in (LEVEL_LABEL.get(key) or "")):
                base += 5; notes.append("confluence agrees")
                break

        out.append({
            "key": key,
            "name": s(lv.get("name")) or LEVEL_LABEL.get(key) or key,
            "price": lp,
            "distance_pct": round((lp - price) / price * 100, 1),
            "confidence": min(100, base),
            "notes": notes,
        })

    # A volume-confirmed shelf is the strongest single piece of evidence
    # that a price level is real, so it is added from the buy zone even
    # when it is not one of the moving averages.
    bz = pullback.get("buy_zone") or {}
    bzp = f(bz.get("price"))
    if bzp is not None and bzp < price:
        touches = f(bz.get("touches")) or 0
        cval = 70 + (10 if bz.get("actual_support") else 0) \
                  + (10 if touches >= 3 else 0)
        out.append({
            "key": "VOLUME_SHELF",
            "name": s(bz.get("name")) or "volume shelf",
            "price": bzp,
            "distance_pct": round((bzp - price) / price * 100, 1),
            "confidence": min(100, cval),
            "notes": ([f"{touches:.0f} touches"] if touches else [])
                     + (["tested support"] if bz.get("actual_support") else []),
        })

    # Deduplicate levels sitting on top of each other (within 0.5%), so
    # three "supports" that are really one shelf do not read as three.
    # Dedupe by confidence so the strongest name for a shared price wins.
    out.sort(key=lambda r: (-r["confidence"], -r["price"]))
    kept = []
    for lv in out:
        if any(abs(lv["price"] - k["price"]) / k["price"] < 0.005 for k in kept):
            continue
        kept.append(lv)

    # Returned NEAREST FIRST — the S1/S2/S3 convention. Proximity, not
    # strength, is the reading order: S1 is the level price meets first.
    kept = kept[:limit]
    kept.sort(key=lambda r: -r["price"])
    return kept


# A level has to be more than a line on a chart to anchor a strike.
MIN_ANCHOR_CONFIDENCE = 60


def anchor_level(levels):
    """The support a strike is measured against.

    The NEAREST level strong enough to count, not the strongest overall.
    Those differ constantly — with price at $100, a 50 MA at $92 and a
    200 MA at $85, the 200 MA scores higher as support but anchoring to
    it demands a 15% out-of-the-money strike that collects almost no
    premium. The 50 MA is the level price actually has to break to
    threaten the position, so it is the one the strike sits under.

    The deeper levels still matter — they feed the discount component and
    the assignment tests — they just do not set the anchor.
    """
    if not levels:
        return None
    strong = [lv for lv in levels
              if (lv.get("confidence") or 0) >= MIN_ANCHOR_CONFIDENCE]
    # Nearest strong level; if none clears the bar, fall back to the
    # single most confident one rather than anchoring to noise.
    return strong[0] if strong else max(
        levels, key=lambda lv: lv.get("confidence") or 0)


def delta_band(quality_score, regime: str, extended: bool) -> tuple[float, float]:
    """The |delta| window this name qualifies for, within 0.10-0.30.

    The full range is always CONSIDERED and displayed; this narrows only
    where the engine is willing to pick from. A stronger business and a
    friendlier tape justify sitting closer to the money, because the
    cost of being wrong (assignment) is lower when the company is better
    and the market is rising. A defensive regime pulls the ceiling down,
    demanding more cushion for the same premium.
    """
    lo, hi = DELTA_HARD_MIN, CORE_DELTA_HIGH

    q = f(quality_score)
    if q is not None and q >= 88:
        hi = DELTA_HARD_MAX                      # elite: aggressive allowed
    elif q is not None and q < 78:
        hi = CORE_DELTA_LOW

    reg = (regime or "").upper()
    if reg in ("FAVORABLE", "BULLISH"):
        hi = min(DELTA_HARD_MAX, hi + 0.03)
    elif reg in ("DEFENSIVE", "BEARISH"):
        hi = max(0.15, hi - 0.08)

    if extended:
        # Far above support, a delta-compliant strike is nowhere near a
        # level worth owning. Tighten so the engine says NO TRADE rather
        # than selling an arbitrary distance out.
        hi = max(0.15, hi - 0.03)

    return round(lo, 3), round(hi, 3)


def buy_zone_price(result: dict):
    """The price the long-term engine itself would buy at, if it names one.

    `entries` carries the engine's own priced entry ladder. The first
    entry is the shallowest. A strike at or below this is a strike at a
    price the engine independently endorsed, which is the second of the
    spec's three conditions.
    """
    for e in (result.get("entries") or []):
        p = f(e.get("price")) if isinstance(e, dict) else None
        if p:
            return p
    bz = (result.get("pullback") or {}).get("buy_zone") or {}
    return f(bz.get("price"))


def evaluate_strike(row, anchor, zone, spot, discount) -> dict:
    """Score one candidate strike on the three conditions, 0-100.

    Returns the score plus the individual condition results, so the page
    can show WHY a strike ranked where it did rather than asserting a
    number.
    """
    k = row["strike"]
    conds, pts = [], 0

    # 1. At or below strong technical support.
    if anchor and k <= anchor["price"]:
        pts += 45
        conds.append({"ok": True, "text":
                      f"below {anchor['name']} (${anchor['price']:,.2f})"})
    elif anchor:
        over = (k - anchor["price"]) / anchor["price"] * 100
        # Within 1% is effectively at the level.
        if over <= 1.0:
            pts += 30
            conds.append({"ok": True, "text":
                          f"at {anchor['name']} (${anchor['price']:,.2f})"})
        else:
            conds.append({"ok": False, "text":
                          f"{over:.1f}% ABOVE {anchor['name']} — poor "
                          f"risk/reward against the level"})
    else:
        conds.append({"ok": None, "text": "no support level below spot"})

    # 2. At or below the engine's own buy zone.
    if zone and k <= zone:
        pts += 35
        conds.append({"ok": True, "text": f"below the buy zone (${zone:,.2f})"})
    elif zone:
        conds.append({"ok": False, "text":
                      f"above the buy zone (${zone:,.2f})"})
    else:
        conds.append({"ok": None, "text": "engine names no buy zone"})

    # 3. A real discount to spot — the cushion the sale is buying.
    disc = (spot - k) / spot * 100 if spot else 0
    if disc >= 12:   pts += 20
    elif disc >= 8:  pts += 15
    elif disc >= 5:  pts += 9
    elif disc >= 3:  pts += 4
    conds.append({"ok": disc >= 5,
                  "text": f"{disc:.1f}% below spot"})

    return {"score": min(100, pts), "conditions": conds,
            "discount_pct": round(disc, 1)}


def select(candidates, result, regime, spot) -> dict:
    """Choose the strike. `candidates` are priced+greeked chain rows.

    Returns {"chosen", "considered", "anchor", "band", "no_trade_reason"}.
    `considered` is the full delta ladder the spec asks for (0.10 / 0.15
    / 0.20 / 0.25 / 0.30), so the choice is shown against its
    alternatives rather than asserted alone.
    """
    levels = support_levels(result)
    anchor = anchor_level(levels)
    zone = buy_zone_price(result)
    q = (result.get("quality") or {}).get("score")
    extended = bool((result.get("pullback") or {}).get("extended"))
    lo, hi = delta_band(q, regime, extended)

    usable = [c for c in candidates
              if c.get("delta") is not None and c.get("sellable")
              and c.get("bid")]
    if not usable:
        return {"chosen": None, "considered": [], "anchor": anchor,
                "levels": levels, "band": (lo, hi), "buy_zone": zone,
                "no_trade_reason": "no put on this expiry has a live bid "
                                   "and a computable delta"}

    # Kept whole for the ladder and for the delta-band test, so an
    # alternative that was passed over can be shown with the reason.
    all_live = list(usable)


    # The ladder — nearest strike to each reference delta, for context.
    # Built from `all_live` rather than the liquidity-filtered pool so
    # the alternatives are shown even when they are unfillable, with the
    # reason attached. A strike the engine passed over for a 20% spread
    # is information; silently omitting it looks like it did not exist.
    considered = []
    for target in (0.10, 0.15, 0.20, 0.25, 0.30):
        near = min(all_live, key=lambda c: abs(abs(c["delta"]) - target))
        if near["strike"] in [x["strike"] for x in considered]:
            continue
        d = abs(near["delta"])
        entry = {**near, "ladder_delta": target,
                 "delta_class": classify_delta(near["delta"]),
                 "assignment_pct": round(d * 100)}
        considered.append(entry)
    considered.sort(key=lambda c: -c["strike"])

    # Say the trade-off out loud rather than leaving it in the numbers.
    # The richest premium on the ladder is always the closest to the
    # money; the page has to name what that costs.
    if len(considered) > 1:
        richest = max(considered, key=lambda c: c.get("yield_pct") or 0)
        safest = min(considered, key=lambda c: abs(c["delta"]))
        for c in considered:
            if c["strike"] == richest["strike"]:
                c["note"] = (f"most premium on the ladder, but "
                             f"{c['assignment_pct']}% model chance of "
                             f"assignment — {c['delta_class'].lower()}")
            elif c["strike"] == safest["strike"]:
                c["note"] = (f"most cushion, least premium — "
                             f"{c['delta_class'].lower()}")
            else:
                c["note"] = c["delta_class"]

    # ORDER MATTERS. The delta band is applied to the whole live chain
    # first, and only then is liquidity applied within it. Doing it the
    # other way round made the diagnosis lie: with every sane strike
    # dropped for a wide spread, the survivors were all deep in the money
    # and the engine reported "no strike in the delta band" — blaming the
    # delta for a spread problem, and sending the user off to wait for a
    # price move that would not have helped.
    in_band = [c for c in all_live if lo <= abs(c["delta"]) <= hi]
    if not in_band:
        deltas = [abs(c["delta"]) for c in all_live]
        return {"chosen": None, "considered": considered, "anchor": anchor,
                "levels": levels, "band": (lo, hi), "buy_zone": zone,
                "blocked_on": "delta",
                "no_trade_reason":
                    (f"no strike sits in the {lo:.2f}-{hi:.2f} delta band "
                     f"(chain spans {min(deltas):.2f}-{max(deltas):.2f})")}

    # Now liquidity, within the band that actually matters.
    # `liquidity_tradable is None` means the spread could not be assessed
    # (market closed). That is not a failed gate, so it does not filter —
    # it is carried through to the verdict as an unknown instead.
    fillable = [c for c in in_band
                if (c.get("liquidity") or 0) >= MIN_LIQUIDITY
                and c.get("liquidity_tradable") is not False]
    if not fillable:
        best = max(in_band, key=lambda c: c.get("liquidity") or 0)
        sp = best.get("spread_pct")
        return {"chosen": None, "considered": considered, "anchor": anchor,
                "levels": levels, "band": (lo, hi), "buy_zone": zone,
                "blocked_on": "liquidity", "best_untradable": best,
                "no_trade_reason":
                    (f"the right strike exists (${best['strike']:,.2f}, "
                     f"delta {abs(best['delta']):.2f}) but is quoted "
                     f"{sp:.0f}% wide — too wide to fill reliably"
                     if sp is not None else
                     "no strike in the delta band has a two-sided market")}

    scored = []
    for c in fillable:
        fit = evaluate_strike(c, anchor, zone, spot, None)
        scored.append({**c, "fit": fit})

    # Rank on structural fit first, then fillability, then premium. A
    # strike that clears support and the buy zone at a smaller premium
    # beats a richer one sitting above the level — that is the entire
    # thesis of the page. Liquidity outranks premium because an
    # unfillable quote is not a premium at all. Fit is bucketed to the
    # nearest 10 so a 92-vs-95 difference, which is noise, does not
    # outweigh a real liquidity gap.
    scored.sort(key=lambda c: (-round(c["fit"]["score"] / 10),
                               -(c.get("liquidity") or 0),
                               -(c.get("annualised") or 0)))
    best = scored[0]

    # The one hard structural rule: a strike ABOVE strong support with a
    # poor fit is not a trade, however good the yield.
    if best["fit"]["score"] < 40:
        return {"chosen": None, "considered": considered, "anchor": anchor,
                "levels": levels, "band": (lo, hi), "buy_zone": zone,
                "blocked_on": "level",
                "no_trade_reason":
                    ("no delta-compliant strike sits at or below support "
                     "and the buy zone — the premium would be paying for "
                     "risk at a price worth owning")}

    best = {**best, "delta_class": classify_delta(best["delta"])}
    return {"chosen": best, "considered": considered, "anchor": anchor,
            "levels": levels, "band": (lo, hi), "buy_zone": zone,
            "alternatives": scored[1:4], "blocked_on": None,
            "no_trade_reason": None}
