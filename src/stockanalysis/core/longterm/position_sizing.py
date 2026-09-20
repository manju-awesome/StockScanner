"""
position_sizing.py
==================
Risk-based position sizing over the Long-Term Buy Engine's own verdict.

The one claim this module makes: **share count is a function of risk, not of
conviction**. An Elite company with a wide structural stop gets a small
position; a merely good one with a tested shelf 2% under the entry gets a
large one. Those are different questions and the table shows both, because
collapsing them is how a portfolio ends up concentrated in whatever happened
to have the tightest stop that week.

Everything here reads the engine's existing output — the pullback ladder, the
priced targets, the trend gate — and never re-derives a level. If the engine
could not find a defensible stop, this module reports that rather than
inventing one, because a position size computed from a stop nobody would
honour is worse than no number at all: it looks like a plan.

The order of operations, which is also the order of the guards:

    planned entry  →  stop below THAT entry  →  risk/share  →  shares
                                                                ↓
                                            capped by maximum allocation

Two things that look like details and are not:

1. Risk is measured from the **planned entry**, never from the current price.
   A name whose entry is a 5% pullback away has a different risk/share than
   the one you would compute today, and sizing off today's price silently
   over-sizes every resting order.
2. The allocation cap is a second, independent limit — not a sanity check on
   the first. Risk sizing alone answers "how much can I lose"; it says
   nothing about concentration, and a stop 0.8% under the entry will happily
   ask for 90% of the account while risking exactly 2%.
"""

from __future__ import annotations

import math
import os

# The nearest a level may sit under the entry and still be called a stop.
# Shared with technicals.compute_targets() rather than restated: a stop
# inside ordinary daily noise makes the ratio a statement about proximity
# instead of about the trade.
from .technicals import MIN_STOP_PCT
# Imported for its zone->action map, so the entry logic reads the engine's
# own vocabulary rather than restating it.
from .engine import ZONE_ACTION as _ZONE_ACTION


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
# Defaults come from the same environment variables the HTML dashboard's
# sizing already reads, so the two paths agree before anyone touches a form.
# RISK_PER_TRADE_PCT defaults to 1.0 there; this engine's brief specifies 2%,
# so the env var wins when set and 2.0 is the fallback.

def env_defaults() -> dict:
    def _f(name, fallback):
        try:
            return float(os.environ.get(name) or fallback)
        except (TypeError, ValueError):
            return fallback
    return {
        "capital": _f("ACCOUNT_SIZE", 100_000.0),
        "risk_pct": _f("RISK_PER_TRADE_PCT", 2.0),
        "max_allocation_pct": _f("MAX_POSITION_PCT", 20.0),
        "atr_multiplier": _f("ATR_STOP_MULTIPLIER", 2.0),
    }


# Bounds, not preferences. They exist to stop a typo in a form field turning
# into a position size: 200% risk per trade is not a configuration, and a
# 0.25 ATR stop is inside the spread on most names.
LIMITS = {
    "capital": (100.0, 1_000_000_000.0),
    "risk_pct": (0.05, 20.0),
    "max_allocation_pct": (1.0, 100.0),
    "atr_multiplier": (0.5, 6.0),
}


def normalize_settings(raw: dict | None = None) -> dict:
    """Env defaults, overlaid with whatever the caller supplies, clamped.

    Unparseable values fall back to the default rather than raising — this
    sits behind a text input, and a page that 500s because someone typed
    "100k" is worse than one that shows the default and says so.
    """
    out = env_defaults()
    for key, value in (raw or {}).items():
        if key not in out or value is None or value == "":
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    for key, (lo, hi) in LIMITS.items():
        out[key] = max(lo, min(hi, out[key]))
    out["max_dollar_risk"] = round(out["capital"] * out["risk_pct"] / 100.0, 2)
    out["max_position_value"] = round(
        out["capital"] * out["max_allocation_pct"] / 100.0, 2)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# WHEN NOT TO SIZE AT ALL
# ─────────────────────────────────────────────────────────────────────────────
# A share count printed next to a broken thesis is read as permission. These
# are the states where the honest output is "N/A" and a reason.

# A hard no. Nothing is shown for these — not a size, not a prospective
# entry, not a ratio. The engine has rejected the name, and printing a
# priced plan underneath that would be offering a trade it just refused.
NEVER_SIZE_ACTIONS = ("AVOID", "THESIS BROKEN")

# "Not yet" rather than "no". These DO get their entry, stop and R:R
# computed and displayed — the levels are real and knowing what you are
# waiting for is the point — but the share count is withheld until the
# blocking condition clears. The distinction matters: PLTR sits 5% above a
# prior breakout it could be bought at, and showing a blank row implied the
# engine had no read on it at all.
NO_SIZE_ACTIONS = ("RESEARCH", "OWN / WAIT FOR TREND")

# A stop is a claim that the structure below the entry means something. Under
# a broken or impaired long-term trend there is no such structure to lean on,
# which is why a damaged name returns N/A rather than an ATR stop dressed up
# as a plan.
NO_SIZE_TRENDS = ("BROKEN", "IMPAIRED")

# ...except for a genuinely elite business, which gets priced anyway.
#
# The argument: at this quality level the question a reader has is not "is
# this company any good" but "where would I buy it if the chart repaired",
# and refusing to price it answers a question nobody asked. META is the case
# — LQuality 94, undervalued on a reverse DCF, and blank across every trading
# column because its 200 MA is falling.
#
# What the override buys is a VOLATILITY stop where no structural level
# survives, and nothing else. The trend is still broken, the row still says
# so, and the position still passes through the same risk and allocation
# caps. It is a decision to show the trade, not a decision to like it — which
# is why every row it touches carries a warning rather than a clean status.
QUALITY_OVERRIDE_MIN = 90

# The one action-level objection the override forgives, because it IS a trend
# objection wearing a different hat. AVOID and THESIS BROKEN are not.
TREND_WAIT_ACTION = "OWN / WAIT FOR TREND"


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────────────────────────────────────
# §3's priority, expressed against the engine's vocabulary. The zone-specific
# BUY actions ARE the explicit entry instruction — "BUY ON 8/21 EMA" names
# both the trigger and the price — so they rank above any inference.

# Engine zone -> the label the table shows. 50MA/200MA collapse into "Support
# Entry" rather than getting their own label: what the reader needs is
# whether the entry is a level price has to come back to, and the specific
# level is carried alongside in `level_name`.
ZONE_ENTRY_TYPE = {
    "EMA": "EMA Pullback",
    "BREAKOUT": "Breakout Entry",
    "50MA": "Support Entry",
    "200MA": "Support Entry",
    "SUPPORT": "Support Entry",
}

ENTRY_AT_MARKET = ("BUY NOW", "BUY ON CONFIRMATION")

# action -> zone, inverted from the engine's own ZONE_ACTION so the two
# cannot drift. "BUY ON SUPPORT" has no ladder zone by design: it means the
# tested shelf, which branch 3 below resolves.
ACTION_ZONE = {action: zone for zone, action in _ZONE_ACTION.items()}


def _tested_support_below(result: dict, price: float):
    """The key-level engine's shelf, when it is genuinely tested and below
    the price. None otherwise.

    Only a level the market has DEFENDED qualifies. `buy_zone` also reports
    derived levels — arithmetic on recent closes with nobody having traded
    there — and treating one of those as an entry would be inventing a price,
    which is the one thing §3 forbids.

    Compared on price rather than on `distance_pct`: buy_zone signs that
    field the opposite way round from the candidate ladder (negative means
    below there, positive means below here), and a sign convention is a
    lousy thing to depend on when the prices are right there.
    """
    bz = (result.get("pullback") or {}).get("buy_zone") or {}
    level = bz.get("price")
    if not level or not price or level >= price:
        return None
    tested = bz.get("actual_support") or bz.get("source") == "volume_shelf"
    return round(level, 2) if tested else None


def plan_entry(result: dict) -> dict:
    """The price to actually work an order at, and what kind of entry it is.

    Never invents a level: every branch either names a price the engine
    already tracks or falls back to the current price and says so.
    """
    price = result.get("price")
    action = str(result.get("action") or "")
    candidates = (result.get("pullback") or {}).get("candidates") or []

    def _out(entry_price, entry_type, level_name, note=""):
        return {"price": entry_price, "type": entry_type,
                "level_name": level_name, "note": note,
                "at_market": entry_type == "Current Price"}

    if not price or price <= 0:
        return _out(None, "No Valid Entry", None, "no quote for this ticker")

    # 1 — an explicit zone instruction from the engine.
    #
    # The zone comes from the ACTION, by inverting the engine's own
    # ZONE_ACTION map. It used to come from `pullback.zone`, which answers a
    # different question — where price sits RIGHT NOW — and reads "NONE" for
    # any extended name. AVGO is the case: "BUY ON BREAKOUT RETEST" with
    # pullback.zone "NONE", so the filter matched nothing and the explicit
    # instruction was silently discarded.
    zone = ACTION_ZONE.get(action)
    if zone:
        below = [c for c in candidates
                 if c.get("price") and c["price"] < price
                 and c.get("zone") == zone]
        if below:
            # Nearest first: the action names the zone, and within a zone the
            # shallower level is the one price reaches first.
            level = max(below, key=lambda c: c["price"])
            return _out(round(level["price"], 2),
                        ZONE_ENTRY_TYPE.get(level.get("zone"), "Support Entry"),
                        level.get("name"))

    # 2 — buy-at-market verdicts. The entry IS the current price, and saying
    # so is not the same as failing to find a level.
    if action in ENTRY_AT_MARKET:
        return _out(round(price, 2), "Current Price", None)

    # 3 — a level the market has actually defended, if one sits below.
    #
    # This has to be read from `pullback.buy_zone` rather than from the
    # candidate ladder, because the ladder only carries the five moving-average
    # levels — the key-level engine's tested shelf (S1) is not among them.
    # Skipping it read WDC as "wait for the 200 MA at $341.09", a 21% further
    # decline, while the market had defended $422.50 one hundred and thirteen
    # times 2.7% below. §3 ranks a support level above a moving-average
    # pullback for exactly this reason, and the stop logic already consulted
    # the shelf — only the entry logic could not see it.
    zone_price = _tested_support_below(result, price)
    if zone_price:
        bz = (result.get("pullback") or {}).get("buy_zone") or {}
        return _out(zone_price, "Support Entry", bz.get("label"),
                    str(bz.get("note") or ""))

    # 4 — the engine's own next entry off the moving-average ladder.
    entries = result.get("entries") or []
    if entries and entries[0].get("price"):
        first = entries[0]
        return _out(round(first["price"], 2),
                    ZONE_ENTRY_TYPE.get(first.get("zone"), "Support Entry"),
                    first.get("name"),
                    "engine's next entry level — not yet triggered")

    # 4 — no level below the price at all. Current price is the only honest
    # answer, and it is the weakest one, so it is last.
    return _out(round(price, 2), "Current Price", None,
                "no tracked level below the price")


# ─────────────────────────────────────────────────────────────────────────────
# STOP
# ─────────────────────────────────────────────────────────────────────────────
# §4's priority. Every candidate is measured against the PLANNED ENTRY, which
# is why targets["stop"] cannot simply be reused: the engine computed it below
# the current price, and for a pullback entry that level may sit above the
# entry — i.e. be no stop at all.

# The engine's own name for a level the market has actually defended, as
# opposed to a moving average that happens to be next in line.
TESTED_STOP_NAMES = ("volume shelf",)

# Zones that count as STRUCTURAL invalidation for a multi-year position.
#
# The 8 and 21 EMA are deliberately excluded. They are trading-timeframe
# levels: on NVDA the 8 EMA sat 1.7% under the breakout entry, which is a
# defensible day-trade stop and a meaningless long-term one — losing it says
# nothing about the thesis, and sizing against it demanded 115% of the
# account. §4 asks for "the 50/200 MA or major support" for exactly this
# reason. Short EMAs remain available as a last resort below, because a stop
# that is too tight is still better than no stop and a silent N/A.
STRUCTURAL_ZONES = ("50MA", "200MA", "BREAKOUT", "SUPPORT")


def plan_stop(result: dict, entry_price, settings: dict,
              atr_pct=None, allow_volatility_stop: bool = False) -> dict:
    """The technical invalidation below `entry_price`, or a reason there is none.

    Structural levels are preferred over an ATR band because this is the
    long-term engine: the question is "what would prove the thesis wrong",
    and a volatility multiple answers "what would be an unusual week". The
    ATR stop exists only for a confirmed trend that has run far enough from
    its levels to leave nothing underneath.
    """
    def _out(price, source, method, note=""):
        return {"price": price, "source": source, "method": method,
                "note": note}

    if not entry_price or entry_price <= 0:
        return _out(None, None, None, "no planned entry")

    candidates = (result.get("pullback") or {}).get("candidates") or []
    targets = result.get("targets") or {}

    # Everything the engine tracks that sits far enough below the entry to be
    # a stop rather than a rounding error.
    below = []
    for c in candidates:
        lv = c.get("price")
        if lv and lv < entry_price:
            gap_pct = (entry_price - lv) / entry_price * 100.0
            if gap_pct >= MIN_STOP_PCT:
                below.append((lv, c.get("name"), c.get("zone")))
    # The engine's own stop is worth adding because it can be a volume shelf
    # the pullback ladder does not carry. Its ZONE must not be assumed: an
    # earlier version stamped every engine stop "SUPPORT", which promoted
    # ADBE's 8 EMA into the structural tier and reported a 3.7% trading stop
    # as long-term invalidation. Match it back to a candidate for the real
    # zone; a shelf that matches nothing is genuine support, anything else
    # falls to the last-resort tier.
    engine_stop = targets.get("stop")
    if engine_stop and engine_stop < entry_price:
        gap_pct = (entry_price - engine_stop) / entry_price * 100.0
        if gap_pct >= MIN_STOP_PCT:
            name = targets.get("stop_name")
            match = next((c for c in candidates
                          if c.get("price") is not None
                          and round(c["price"], 2) == round(engine_stop, 2)),
                         None)
            zone = (match.get("zone") if match else
                    "SUPPORT" if (name or "") in TESTED_STOP_NAMES else None)
            below.append((engine_stop, name, zone))

    # Within any tier the NEAREST qualifying level wins. A wider stop is not
    # a better one — it buys fewer shares for the same conviction — so the
    # tiers rank by what kind of level it is, never by how much room it gives.
    def _nearest(items):
        return max(items, key=lambda b: b[0])

    # 1 — a level the market has actually defended.
    tested = [b for b in below if (b[1] or "") in TESTED_STOP_NAMES]
    if tested:
        lv, name, _zone = _nearest(tested)
        return _out(round(lv, 2), name, "tested level")

    # 2 — structural invalidation: the 50/200 MA, a prior breakout, major
    # support. What losing it would actually say about the thesis.
    structural = [b for b in below if b[2] in STRUCTURAL_ZONES]
    if structural:
        lv, name, _zone = _nearest(structural)
        return _out(round(lv, 2), name, "structural level")

    # 3 — volatility fallback, for a trend worth leaning on, or for an elite
    # business the quality override has unlocked. An ATR stop under a broken
    # trend is otherwise a number, not an invalidation level.
    trend_state = (result.get("trend") or {}).get("state")
    if (atr_pct and atr_pct > 0
            and (allow_volatility_stop or trend_state not in NO_SIZE_TRENDS)):
        mult = settings["atr_multiplier"]
        stop = entry_price * (1 - (atr_pct / 100.0) * mult)
        if stop > 0:
            return _out(round(stop, 2), f"ATR × {mult:g}", "volatility band",
                        "no structural level below the entry — "
                        "volatility stop, wider than a tested shelf")

    # 4 — a short EMA, which is a trading stop rather than a thesis stop.
    # Reported as such so the row does not claim more than it has.
    if below:
        lv, name, _zone = _nearest(below)
        return _out(round(lv, 2), name, "short-term level",
                    "only a short EMA sits below the entry — a trading stop, "
                    "not long-term invalidation")

    return _out(None, None, None,
                "no level sits far enough below the entry to be a stop")


# ─────────────────────────────────────────────────────────────────────────────
# SIZE
# ─────────────────────────────────────────────────────────────────────────────

def size_position(entry_price, stop_price, settings: dict) -> dict:
    """Shares from risk, capped by allocation. The whole arithmetic of §2/§7.

    Returns both candidate share counts, not just the winner: which limit
    bound the position is the single most useful thing on the row, and a
    lone final number hides it.
    """
    risk_per_share = round(entry_price - stop_price, 4)
    max_risk = settings["max_dollar_risk"]
    max_value = settings["max_position_value"]

    # Whole shares, always down. Rounding up breaks the risk ceiling by
    # definition — that is what the ceiling is.
    risk_shares = int(math.floor(max_risk / risk_per_share))
    allocation_shares = int(math.floor(max_value / entry_price))
    shares = max(0, min(risk_shares, allocation_shares))

    position_value = round(shares * entry_price, 2)
    actual_risk = round(shares * risk_per_share, 2)
    return {
        "risk_per_share": risk_per_share,
        "risk_shares": risk_shares,
        "allocation_shares": allocation_shares,
        "shares": shares,
        "position_value": position_value,
        "allocation_pct": round(position_value / settings["capital"] * 100.0, 2),
        "actual_risk": actual_risk,
        "actual_risk_pct": round(actual_risk / settings["capital"] * 100.0, 2),
        # What risk sizing ALONE would have committed. This is the number the
        # OVERSIZED warning is about: the row shows the capped position, and
        # without this the warning would quote the very allocation the cap
        # just enforced and read as a contradiction.
        "risk_allocation_pct": round(
            risk_shares * entry_price / settings["capital"] * 100.0, 1),
        "stop_distance": round(risk_per_share, 2),
        "stop_distance_pct": round(risk_per_share / entry_price * 100.0, 2),
        # Which constraint actually decided the number.
        "bound_by": ("allocation" if allocation_shares < risk_shares
                     else "risk"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TARGET AND R-MULTIPLE
# ─────────────────────────────────────────────────────────────────────────────

RR_BANDS = ((3.0, "Excellent"), (2.0, "Good"), (1.5, "Acceptable"),
            (0.0, "Weak"))

# The ratio a level has to clear to be quoted as THE target.
#
# The primary target used to be the nearest level overhead, full stop. That
# is arithmetically honest and practically useless: an entry resting just
# under a cluster makes the first thing above it the target, and a 1.3% pop
# becomes the trade's stated reward. META quoted 0.2R to a 21 EMA $7 away,
# WDC 0.4R, PLTR 0.3R — three different names all reporting that their setup
# was worthless, when what was actually true is that the nearest line was
# close.
#
# 2R is the bar because it is the same one §10 already uses to call a trade
# "Good", so the target a row quotes is the first level at which the trade
# it describes would be worth taking. Below that the level is a waypoint,
# not a target.
TARGET_MIN_RR = 2.0


def rr_label(rr) -> str | None:
    if rr is None:
        return None
    return next(name for floor, name in RR_BANDS if rr >= floor)


def target_ladder(result: dict, entry_price, risk_per_share) -> list[dict]:
    """Every tracked level above the planned entry, nearest first, with R:R
    measured from that entry.

    Built from the union of the engine's target ladder AND its pullback
    candidates, because the two describe different halves of the chart
    relative to the CURRENT price: `targets.ladder` holds what is above the
    price today, `pullback.candidates` what is below it. For an entry that
    sits below the current price, the levels in between live in the second
    list — and using only the first would skip the nearest resistance
    entirely. WDC is the case: entering at the 200 MA, the first ladder
    level is R1 at 5.3R, while the 50 MA the price has to reclaim first sits
    far nearer and is the target that actually gets hit.
    """
    if not risk_per_share or risk_per_share <= 0:
        return []
    seen, out = set(), []
    pools = ((result.get("targets") or {}).get("ladder") or [],
             (result.get("pullback") or {}).get("candidates") or [])
    for pool in pools:
        for lv in pool:
            price = lv.get("price")
            if not price or price <= entry_price:
                continue
            key = round(price, 2)
            if key in seen:
                continue
            seen.add(key)
            profit = price - entry_price
            out.append({
                "price": key, "name": lv.get("name"),
                "rr": round(profit / risk_per_share, 2),
                "profit_per_share": round(profit, 2),
                "move_pct": round((price / entry_price - 1) * 100.0, 1),
            })
    out.sort(key=lambda lv: lv["price"])
    return out


def plan_target(result: dict, entry_price, risk_per_share) -> dict:
    """The primary target — the NEAREST level that clears TARGET_MIN_RR —
    plus the whole ladder.

    R:R is recomputed from the planned entry rather than reused from the
    engine: its `rr` is measured from the current price against the
    current-price stop, and for a pullback entry both ends of that ratio
    move. Quoting it would price a trade nobody is taking.

    Nearest-that-clears, not simply nearest: see TARGET_MIN_RR. When no
    tracked level reaches the bar the furthest one is quoted instead, with
    `reached_min_rr` False — "the most this chart offers is 1.7R" is a real
    answer, and silently falling back to the nearest rung would report 0.4R
    for the same setup.
    """
    out = {"price": None, "name": None, "rr": None, "label": None,
           "profit_per_share": None, "move_pct": None, "ladder": [],
           "best_rr": None, "reached_min_rr": None, "skipped": 0,
           "nearest": None}
    ladder = target_ladder(result, entry_price, risk_per_share)
    if not ladder:
        return out
    # rr rises monotonically with price, so the first rung that clears the
    # bar is also the nearest one — no separate search needed.
    chosen = next((lv for lv in ladder if lv["rr"] >= TARGET_MIN_RR), None)
    out["reached_min_rr"] = chosen is not None
    if chosen is None:
        chosen = ladder[-1]
    out.update(chosen)
    out["label"] = rr_label(chosen["rr"])
    out["nearest"] = ladder[0]
    # How many levels the target steps over. Named because they are real
    # resistance the trade has to get through, and a target quoted past three
    # untested lines is a different proposition from one quoted past none.
    out["skipped"] = sum(1 for lv in ladder if lv["price"] < chosen["price"])
    out["ladder"] = ladder[:6]
    out["best_rr"] = max(lv["rr"] for lv in ladder)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# STATUS AND GRADE
# ─────────────────────────────────────────────────────────────────────────────

STATUS = {
    "NORMAL":         ("🟢", "NORMAL", "good"),
    "HIGH_ALLOCATION":("🟡", "HIGH ALLOCATION", "watch"),
    "OVERSIZED":      ("🔴", "OVERSIZED", "bad"),
    "NO_STOP":        ("🔴", "NO STOP", "bad"),
    "INVALID_SETUP":  ("🔴", "INVALID SETUP", "bad"),
    "NOT_ACTIONABLE": ("⚪", "N/A", "muted"),
}

# How close to the allocation ceiling counts as "high" rather than normal.
# Three-quarters of the cap, so a 20% limit starts warning at 15% — far
# enough in to be worth saying, not so close that the warning and the cap
# fire together and one of them is redundant.
HIGH_ALLOCATION_FRACTION = 0.75


def position_grade(rr, status_key, stop_method) -> str:
    """A letter for the QUALITY of the sizing, which is not the quality of
    the company — that is LQuality, and the two disagreeing is informative.

    Driven by risk/reward first (it is the only thing here that says whether
    the trade pays), then docked for a position the allocation cap had to
    rescue and for a stop nobody has tested.
    """
    if status_key in ("NO_STOP", "INVALID_SETUP", "NOT_ACTIONABLE"):
        return "—"
    if rr is None:
        return "D"
    grade = "A" if rr >= 3.0 else "B" if rr >= 2.0 else "C" if rr >= 1.5 else "D"
    if status_key == "OVERSIZED" and grade in ("A", "B"):
        # The ratio is real but the stop was tight enough that risk sizing
        # alone wanted an unacceptable concentration.
        grade = chr(ord(grade) + 1)
    # A stop the engine had to improvise is worth less than one the market
    # has defended, however good the ratio measured against it looks.
    if stop_method in ("volatility band", "short-term level") and grade == "A":
        grade = "B"
    return grade


def classify(sizing: dict, settings: dict) -> str:
    """Which of §6's risk states this position is in."""
    if sizing["shares"] <= 0:
        return "INVALID_SETUP"
    # Capped means risk-based sizing asked for more than the account should
    # hold in one name. The position on the row is already the safe one; the
    # flag is about what the tight stop tried to do.
    if sizing["bound_by"] == "allocation":
        return "OVERSIZED"
    if (sizing["allocation_pct"]
            >= settings["max_allocation_pct"] * HIGH_ALLOCATION_FRACTION):
        return "HIGH_ALLOCATION"
    return "NORMAL"


def _trend_reason(result: dict, state: str) -> str:
    """Why the trend gate withholds a size, naming the checks that failed.

    An earlier version of this said "no validated stop, because there is no
    structure left below the entry" — which was true of META, where every
    level sat within 1% of the price, and false of 76 of the 153 names the
    gate catches. PLTR has a prior breakout 5% below and four more levels
    under that; telling the user there was no structure while the panel two
    inches above listed five levels was simply wrong.

    The real argument is narrower and survives contact with the data: the
    levels exist, but a stop is a bet that structure holds, and these are
    names whose structure has already failed.
    """
    checks = (result.get("trend") or {}).get("checks") or []
    failed = [c.get("name") for c in checks
              if c.get("required") and not c.get("ok") and c.get("name")]
    detail = "; ".join(failed[:3]) if failed else "structural checks failed"
    return (f"Long-term trend {state.lower()} — {detail}. The levels below "
            f"are real, but a stop is a bet on structure holding and this "
            f"structure has already broken, so the size is withheld until "
            f"the trend repairs.")


def _not_actionable(reason: str, entry=None, settings=None) -> dict:
    return {
        "ok": False, "status": "NOT_ACTIONABLE", "reason": reason,
        "entry": entry or {}, "stop": {}, "sizing": {}, "target": {},
        "grade": "—", "settings": settings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# THE WHOLE ASSESSMENT
# ─────────────────────────────────────────────────────────────────────────────

def assess(result: dict, settings: dict, atr_pct=None) -> dict:
    """Entry, stop, size, target and risk status for one engine verdict.

    `atr_pct` is the raw scan row's ATR_Pct — the one input the engine's
    result does not carry. Optional: without it the volatility fallback is
    simply unavailable, which costs a fallback stop and never a wrong one.
    """
    action = str(result.get("action") or "")
    trend_state = (result.get("trend") or {}).get("state")

    # A rejected name gets nothing priced.
    if action in NEVER_SIZE_ACTIONS:
        return _not_actionable(
            f"{action} — the engine has rejected this name, so no entry is "
            f"priced", settings=settings)

    # An elite business is priced through a trend objection — see
    # QUALITY_OVERRIDE_MIN. Only a TREND objection: a name the engine has told
    # to avoid never reaches here, and a RESEARCH verdict is not about the
    # chart, so neither is forgiven.
    quality = (result.get("quality") or {}).get("score")
    trend_objection = (trend_state in NO_SIZE_TRENDS
                       or action == TREND_WAIT_ACTION)
    override = (quality is not None and quality >= QUALITY_OVERRIDE_MIN
                and trend_objection)

    # A blocked name still gets its levels. `blocked` withholds the share
    # count at the end; it does not stop the plan being computed, because
    # "what am I waiting for, and at what price" is exactly the question a
    # WAIT verdict raises.
    blocked = None
    if override:
        pass
    elif trend_state in NO_SIZE_TRENDS:
        blocked = _trend_reason(result, trend_state)
    elif action in NO_SIZE_ACTIONS:
        blocked = f"{action} — no entry offered yet"

    entry = plan_entry(result)
    if not entry["price"]:
        return _not_actionable(entry["note"] or "no valid entry",
                               entry=entry, settings=settings)

    stop = plan_stop(result, entry["price"], settings, atr_pct=atr_pct,
                     allow_volatility_stop=override)
    if not stop["price"]:
        # When a name is BOTH blocked and unstoppable — META, below a falling
        # 200 MA with every level inside 1% — say both. The trend is the more
        # fundamental objection and the one that would have to change first,
        # so it leads.
        reason = (f"{blocked} There is also no level far enough below "
                  f"${entry['price']:,.2f} to act as a stop."
                  if blocked else stop["note"])
        return {"ok": False, "status": "NO_STOP", "reason": reason,
                "entry": entry, "stop": stop, "sizing": {}, "target": {},
                "grade": "—", "settings": settings}
    if stop["price"] >= entry["price"]:
        return {"ok": False, "status": "INVALID_SETUP",
                "reason": (f"stop ${stop['price']:,.2f} is not below the entry "
                           f"${entry['price']:,.2f}"),
                "entry": entry, "stop": stop, "sizing": {}, "target": {},
                "grade": "—", "settings": settings}

    sizing = size_position(entry["price"], stop["price"], settings)
    target = plan_target(result, entry["price"], sizing["risk_per_share"])

    if blocked:
        # The plan, priced, with the position withheld. `sizing` is dropped
        # to the per-share facts §4 says to always display — risk/share and
        # stop distance — and carries no share count, position value or
        # dollar risk, because those are the numbers that would read as
        # permission to act on a setup the engine has not cleared.
        return {
            "ok": False, "status": "NOT_ACTIONABLE", "reason": blocked,
            "pending": True, "entry": entry, "stop": stop, "target": target,
            "sizing": {k: sizing[k] for k in
                       ("risk_per_share", "stop_distance", "stop_distance_pct")},
            "grade": "—", "settings": settings,
        }

    status = classify(sizing, settings)
    grade = position_grade(target["rr"], status, stop["method"])

    potential_profit = (round(sizing["shares"] * target["profit_per_share"], 2)
                        if target["profit_per_share"] is not None else None)

    # The override is carried BESIDE the status, not as one of its values.
    # "the allocation cap bound this" and "this is priced despite a broken
    # trend" are independent facts and a row routinely has both; collapsing
    # them into one field would lose whichever was written second.
    warning = _override_warning(result, trend_state, stop) if override else None

    return {
        "ok": sizing["shares"] > 0,
        "status": status,
        "quality_override": bool(override),
        "warning": warning,
        "reason": _status_reason(status, sizing, settings),
        "entry": entry, "stop": stop, "sizing": sizing, "target": target,
        "potential_profit": potential_profit,
        "grade": grade, "settings": settings,
        # The headline §5 asks for, built once here so the table, the panel
        # and the action column cannot phrase it three different ways.
        "summary": (f"{sizing['shares']:,} shares | "
                    f"${sizing['position_value']:,.0f} position | "
                    f"{sizing['allocation_pct']:.1f}% allocation | "
                    f"${sizing['actual_risk']:,.0f} risk"),
    }


def _override_warning(result: dict, trend_state, stop: dict) -> str:
    """Why this row is priced at all, said plainly.

    The row exists because the business is elite, NOT because the setup is
    good — and a share count with no explanation attached would be read as
    the second. Names the failed trend checks so the warning is specific
    enough to argue with.
    """
    quality = (result.get("quality") or {}).get("score")
    tier = (result.get("quality") or {}).get("tier") or "Elite"
    checks = (result.get("trend") or {}).get("checks") or []
    failed = [c.get("name") for c in checks
              if c.get("structural") and not c.get("ok") and c.get("name")]
    detail = "; ".join(failed[:3]) if failed else "structural checks failed"
    extra = ("" if stop.get("method") != "volatility band" else
             " No structural level survives below the entry, so the stop is a "
             "volatility band rather than a level anyone has defended — it is "
             "a risk budget, not an invalidation price.")
    return (f"{tier} business (LQuality {quality}) with a "
            f"{str(trend_state).lower()} long-term trend — {detail}. "
            f"Priced because the company clears the quality bar, not because "
            f"the chart is ready.{extra}")


def _status_reason(status: str, sizing: dict, settings: dict) -> str:
    if status == "OVERSIZED":
        return (f"Risk is only ${sizing['actual_risk']:,.0f}, but the stop is "
                f"tight enough that risk sizing wanted {sizing['risk_shares']:,} "
                f"shares — {sizing['risk_allocation_pct']:.1f}% of capital. "
                f"Capped at {settings['max_allocation_pct']:g}%.")
    if status == "HIGH_ALLOCATION":
        return (f"Risk is within budget, but the position uses "
                f"{sizing['allocation_pct']:.1f}% of capital")
    if status == "INVALID_SETUP":
        return "stop is too far from the entry to buy a single share"
    return (f"{sizing['shares']:,} shares risking "
            f"${sizing['actual_risk']:,.0f} ({sizing['actual_risk_pct']:.1f}% "
            f"of capital)")


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO-LEVEL VIEW (§13)
# ─────────────────────────────────────────────────────────────────────────────

def portfolio_summary(results, settings: dict) -> dict:
    """Aggregate planned risk across the rows currently on screen.

    Deliberately over what is DISPLAYED rather than over the whole library:
    the question it answers is "if I took the trades in front of me, what
    would that commit", and averaging in 400 names nobody is looking at makes
    the number describe the database instead of the decision.

    These are PLANNED figures for setups that mostly have not triggered.
    Summing them is a concentration check, not a statement of open risk.
    """
    sized = [r for r in results
             if (r.get("sizing_plan") or {}).get("ok")]
    out = {
        "n_actionable": len(sized), "capital": settings["capital"],
        "max_dollar_risk": settings["max_dollar_risk"],
        "max_allocation_pct": settings["max_allocation_pct"],
        "planned_capital": 0.0, "planned_risk": 0.0,
        "planned_capital_pct": 0.0, "planned_risk_pct": 0.0,
        "avg_rr": None, "max_allocation": None, "max_risk": None,
        "top_sector": None, "top_sector_pct": None,
    }
    if not sized:
        return out

    plans = [r["sizing_plan"] for r in sized]
    out["planned_capital"] = round(
        sum(p["sizing"]["position_value"] for p in plans), 2)
    out["planned_risk"] = round(
        sum(p["sizing"]["actual_risk"] for p in plans), 2)
    out["planned_capital_pct"] = round(
        out["planned_capital"] / settings["capital"] * 100.0, 1)
    out["planned_risk_pct"] = round(
        out["planned_risk"] / settings["capital"] * 100.0, 2)

    rrs = [p["target"]["rr"] for p in plans if p["target"].get("rr") is not None]
    if rrs:
        out["avg_rr"] = round(sum(rrs) / len(rrs), 2)

    worst_alloc = max(sized, key=lambda r: r["sizing_plan"]["sizing"]["allocation_pct"])
    out["max_allocation"] = {
        "ticker": worst_alloc["ticker"],
        "pct": worst_alloc["sizing_plan"]["sizing"]["allocation_pct"]}
    worst_risk = max(sized, key=lambda r: r["sizing_plan"]["sizing"]["actual_risk"])
    out["max_risk"] = {
        "ticker": worst_risk["ticker"],
        "dollars": worst_risk["sizing_plan"]["sizing"]["actual_risk"]}

    # Sector concentration — correlated risk's cheapest available proxy. Named
    # as a proxy in the UI: two AI semiconductor names in different GICS
    # sectors are correlated in every way that matters to a drawdown, and this
    # will not see it.
    by_sector: dict[str, float] = {}
    for r in sized:
        sector = r.get("sector") or "Unknown"
        by_sector[sector] = (by_sector.get(sector, 0.0)
                             + r["sizing_plan"]["sizing"]["position_value"])
    if by_sector:
        sector, value = max(by_sector.items(), key=lambda kv: kv[1])
        out["top_sector"] = sector
        out["top_sector_pct"] = round(value / settings["capital"] * 100.0, 1)
        out["by_sector"] = {k: round(v, 2) for k, v in
                            sorted(by_sector.items(), key=lambda kv: -kv[1])}
    return out


def attach(results, raw_by_ticker: dict, settings: dict) -> None:
    """In place: `result["sizing_plan"]` for every evaluated row.

    Done over the whole result set at once, after evaluation, because the
    settings are the user's and the engine's verdict is not — keeping sizing
    out of `evaluate()` is what lets the same verdict be sized against two
    different accounts without re-running the gates.
    """
    for r in results:
        raw = raw_by_ticker.get(r.get("ticker")) or {}
        atr_pct = raw.get("ATR_Pct")
        try:
            atr_pct = float(atr_pct) if atr_pct is not None else None
        except (TypeError, ValueError):
            atr_pct = None
        try:
            r["sizing_plan"] = assess(r, settings, atr_pct=atr_pct)
        except Exception as e:            # never let sizing break the page
            # Logged, not just swallowed. A NameError in here rendered as an
            # ordinary "not actionable" row on four tickers and looked exactly
            # like a considered refusal — the failure has to be noisy on the
            # console even though the page survives it.
            import traceback
            print(f"[Sizing] {r.get('ticker')}: {type(e).__name__}: {e}")
            traceback.print_exc()
            r["sizing_plan"] = _not_actionable(f"sizing failed: {e}",
                                               settings=settings)
