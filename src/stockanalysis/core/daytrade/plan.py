"""
plan.py — §13 entry/stop/target and §14 position sizing
=======================================================
Turns a structure into an order: a trigger price, a stop that sits beyond
something real, two targets and the R:R that falls out of them.

Stops come from structure, never from a percentage
---------------------------------------------------
§13 is unambiguous about this, and the reason is mechanical rather than
stylistic. A 5% stop is the same distance whether the nearest real support
is 1% away or 9% away, so half the time it sits inside the noise and gets
tagged by a wick, and the other half it gives away far more than the trade
needed. `plan_stop()` therefore searches the §13 list — VWAP, OR low,
higher low, prior support, the breakout level itself, volume shelf,
premarket level — for the nearest one on the correct side of entry, and
places the stop *beyond* it by a fraction of the 5-minute ATR so a wick
does not tag it. The chosen basis is reported, because a stop whose reason
cannot be named is a stop that will be moved under pressure.

The trigger is not the entry
-----------------------------
§17: "the scanner should identify the trade opportunity, while the actual
entry requires the specified trigger/confirmation." So `entry` is the
price at which the setup becomes valid, expressed as a condition on a
level, and it is set slightly beyond that level rather than at it —
entering at the exact breakout price fills you on every failed poke
through.

Sizing never scales with conviction
------------------------------------
§14: "Never increase position size simply because the stock has a high
score." Size is a function of risk per share and account risk, then
reduced by whichever constraint binds first — allocation cap, ADV
participation, or the micro-float floor. Score does not appear in the
arithmetic at any point. The binding constraint is named in the output so
a small size reads as a liquidity fact rather than a mystery.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from stockanalysis.core.daytrade._common import f, pct

PROJECT_ROOT = Path(__file__).resolve().parents[4]
RISK_SETTINGS = PROJECT_ROOT / "data" / "risk_settings.json"

# Day-trade risk is its own knob rather than a reuse of the swing engine's
# `risk_pct`. Two percent of the account on a position held for weeks and
# two percent on an intraday small-cap breakout are not the same bet: the
# intraday one can gap through its stop on a halt-and-resume, and it may be
# taken several times in one session. Capital and the allocation cap ARE
# read from the shared settings, because those are facts about the account
# rather than about the strategy.
DEFAULT_RISK_PCT = 1.0
DEFAULT_CAPITAL = 100_000.0
DEFAULT_MAX_ALLOCATION_PCT = 20.0

# §14's liquidity constraint: never take more than this share of the
# stock's average daily volume, or the exit becomes the reason the price
# moves against you.
MAX_ADV_PARTICIPATION_PCT = 1.0

# Slippage model (§10). The volatility term dominates outside market hours,
# when there is no meaningful spread to read.
VOLATILITY_SLIPPAGE_ATR = 0.15      # of the 5-minute ATR
MIN_SLIPPAGE_PER_SHARE = 0.01       # a penny is the floor on any small cap
# Market impact starts once the position exceeds this share of what the
# stock trades in a minute, and costs this many bps per unit above it.
IMPACT_FREE_SHARE = 0.10
IMPACT_BPS_PER_UNIT = 0.004
# Hard ceiling on position value as a share of one minute's dollar volume:
# beyond this you are the tape, and the exit moves the price against you.
MAX_MINUTE_PARTICIPATION = 0.25
# Stop buffer and the §3 sanity ceiling, both in 5-minute ATRs.
STOP_BUFFER_ATR = 0.25
MAX_STOP_ATR = 1.5

# Minimum resampled bars before an intraday ATR means anything.
MIN_ATR_BARS = 4
# A stop closer than this share of the 5-minute ATR is inside the noise —
# it will be tagged by a single wick regardless of whether the thesis was
# right, so the R:R it implies is fiction.
MIN_STOP_ATR = 0.5

# A level this close to the entry is part of the breakout, not a
# destination. Expressed in R so it scales with the trade's own risk.
BREAKOUT_ZONE_R = 0.5
# Runner target when structure offers no second level.
FALLBACK_T2_R = 3.0
# Furthest a target may sit from entry, in multiples of the expected
# remaining move. Beyond this the session cannot reach it.
MAX_TARGET_EM_MULT = 3.0

# Which range gets projected for each breakout structure. The measured
# move — the height of the base, added to the level it broke — is the
# standard target for a range breakout, and it is what makes these plans
# usable: on RCEL's 2026-08-07 opening-range breakout it projected 7.72
# from a 6.98 entry, and the stock closed at 7.77. Taking the session
# high as the target instead put T1 seven cents away and rated a 3:1
# trade at 0.33:1.
MEASURED_MOVE_RANGES = {
    "ORB_BREAKOUT":            ("or_high", "or_low", "opening range"),
    "ORB_BREAKDOWN":           ("or_low", "or_high", "opening range"),
    "PM_HIGH_BREAKOUT":        ("pm_high", "pm_low", "premarket range"),
    "PM_LOW_BREAKDOWN":        ("pm_low", "pm_high", "premarket range"),
    "PDH_BREAKOUT":            ("prev_high", "prev_low", "prior-day range"),
    "PDL_BREAKDOWN":           ("prev_low", "prev_high", "prior-day range"),
    "CONSOLIDATION_BREAKOUT":  ("day_high", "day_low", "session range"),
    "CONSOLIDATION_BREAKDOWN": ("day_low", "day_high", "session range"),
    # Reversal and continuation structures do not break a named base, so
    # the session range is what gets projected from the extreme being
    # reclaimed. Without these, a reclaim into open air had no structural
    # target at all and fell through to bare R-multiples — see
    # `targets_structural`.
    "FAILED_BREAKDOWN":        ("day_high", "day_low", "session range"),
    "FAILED_BREAKOUT":         ("day_low", "day_high", "session range"),
    "HIGHER_LOW_CONTINUATION": ("day_high", "day_low", "session range"),
    "LOWER_HIGH_CONTINUATION": ("day_low", "day_high", "session range"),
    "VWAP_RECLAIM":            ("day_high", "day_low", "session range"),
    "VWAP_BOUNCE":             ("day_high", "day_low", "session range"),
    "VWAP_REJECTION":          ("day_low", "day_high", "session range"),
    "BULL_FLAG":               ("day_high", "day_low", "session range"),
    "BEAR_FLAG":               ("day_low", "day_high", "session range"),
}


def measured_move(sess: dict, primary: str, direction: str) -> tuple[float, str] | None:
    """Range height projected from the level that was broken."""
    spec = MEASURED_MOVE_RANGES.get(primary)
    if not spec:
        return None
    base_key, other_key, label = spec
    base, other = f(sess.get(base_key)), f(sess.get(other_key))
    if base is None or other is None:
        return None
    height = abs(base - other)
    if height <= 0:
        return None
    target = base + height if direction == "long" else base - height
    return target, f"measured move ({label} {height:.2f} projected from {base:.2f})"


def load_settings(overrides: dict | None = None) -> dict:
    """Account facts from data/risk_settings.json, strategy knobs from here."""
    raw = {}
    try:
        raw = json.loads(RISK_SETTINGS.read_text())
    except (OSError, ValueError):
        pass
    settings = {
        "capital": f(raw.get("capital")) or DEFAULT_CAPITAL,
        "risk_pct": DEFAULT_RISK_PCT,
        "max_allocation_pct": f(raw.get("max_allocation_pct")) or DEFAULT_MAX_ALLOCATION_PCT,
        "max_adv_pct": MAX_ADV_PARTICIPATION_PCT,
    }
    settings.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return settings


def intraday_atr(bars: pd.DataFrame, minutes: int = 5, period: int = 14) -> float | None:
    """ATR on the N-minute chart, resampled from the 1-minute session bars.

    §13's stop buffer and §3's "is this stop too wide for a day trade"
    check are both expressed in 5-minute ATRs, so it has to be computed on
    that timeframe rather than scaled down from the daily one.

    The period shortens when the session is young. A fixed 14 needs 75
    minutes of trading before it returns anything, so for the entire
    opening hour — which is when this scanner is most used — it returned
    None, the stop buffer fell through to a flat 0.2% of price, and stops
    landed inside the noise on stocks with 20% daily ranges. R:R then came
    back as 85:1. A shorter ATR on nine bars is a rougher estimate; a
    silent constant is not an estimate at all.
    """
    if bars is None or bars.empty:
        return None
    agg = bars.resample(f"{minutes}min").agg(
        {"High": "max", "Low": "min", "Close": "last"}).dropna()
    if len(agg) < MIN_ATR_BARS:
        return None
    period = min(period, len(agg) - 1)
    prev = agg["Close"].shift(1)
    tr = pd.concat([agg["High"] - agg["Low"], (agg["High"] - prev).abs(),
                    (agg["Low"] - prev).abs()], axis=1).max(axis=1)
    return f(tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().iloc[-1])


# Primary pattern → (level key on the session, human trigger wording).
TRIGGER_LEVELS = {
    "ORB_BREAKOUT":            ("or_high", "break of the opening-range high"),
    "ORB_BREAKDOWN":           ("or_low", "break of the opening-range low"),
    "PM_HIGH_BREAKOUT":        ("pm_high", "break of the premarket high"),
    "PM_LOW_BREAKDOWN":        ("pm_low", "break of the premarket low"),
    "PDH_BREAKOUT":            ("prev_high", "break of the prior-day high"),
    "PDL_BREAKDOWN":           ("prev_low", "break of the prior-day low"),
    "VWAP_RECLAIM":            ("vwap", "reclaim of session VWAP"),
    "VWAP_BOUNCE":             ("vwap", "hold of session VWAP"),
    "VWAP_REJECTION":          ("vwap", "rejection at session VWAP"),
    "CONSOLIDATION_BREAKOUT":  ("day_high", "break of the session high"),
    "CONSOLIDATION_BREAKDOWN": ("day_low", "break of the session low"),
    "BULL_FLAG":               ("day_high", "break of the flag high"),
    "BEAR_FLAG":               ("day_low", "break of the flag low"),
    "HIGHER_LOW_CONTINUATION": ("day_high", "break of the session high"),
    "LOWER_HIGH_CONTINUATION": ("day_low", "break of the session low"),
    "FAILED_BREAKOUT":         ("day_low", "loss of the session low"),
    "FAILED_BREAKDOWN":        ("day_high", "reclaim of the session high"),
}


def plan_stop(sess: dict, entry: float, direction: str,
              atr5: float | None) -> dict:
    """Nearest §13 structural level beyond entry, plus a wick buffer."""
    candidates = [
        (sess.get("vwap"), "session VWAP"),
        (sess.get("or_low") if direction == "long" else sess.get("or_high"),
         "opening-range " + ("low" if direction == "long" else "high")),
        (sess.get("pm_high") if direction == "long" else sess.get("pm_low"),
         "premarket " + ("high (now support)" if direction == "long" else "low (now resistance)")),
        (sess.get("prev_high") if direction == "long" else sess.get("prev_low"),
         "prior-day " + ("high (now support)" if direction == "long" else "low (now resistance)")),
        (sess.get("day_low") if direction == "long" else sess.get("day_high"),
         "session " + ("low" if direction == "long" else "high")),
        (sess.get("ema20"), "20 EMA"),
    ]
    side = [(f(lvl), name) for lvl, name in candidates
            if f(lvl) is not None
            and (f(lvl) < entry * 0.999 if direction == "long" else f(lvl) > entry * 1.001)]
    if not side:
        return {"stop": None, "basis": None, "risk_per_share": None,
                "too_wide": None, "note": "no structural level beyond entry"}

    # Nearest structure, so the trade is risking the least it can while
    # still being wrong for a reason.
    level, basis = min(side, key=lambda x: abs(entry - x[0]))
    buffer_ = (atr5 * STOP_BUFFER_ATR) if atr5 else level * 0.002
    stop = level - buffer_ if direction == "long" else level + buffer_
    risk = abs(entry - stop)

    too_wide = too_tight = None
    note = None
    if atr5 and atr5 > 0:
        too_wide = risk > atr5 * MAX_STOP_ATR
        too_tight = risk < atr5 * MIN_STOP_ATR
        if too_wide:
            note = f"stop is {risk/atr5:.1f}x the 5-min ATR — too wide for a day trade"
        elif too_tight:
            note = (f"stop is only {risk/atr5:.2f}x the 5-min ATR — inside the noise; "
                    "the R:R this implies will not survive a single wick")
    return {"stop": round(stop, 2), "basis": f"{basis} at {level:.2f}",
            "stop_level": level, "risk_per_share": round(risk, 4),
            "too_wide": too_wide, "too_tight": too_tight, "note": note}


def build(sess: dict, pat: dict, direction: str, vol: dict,
          room_result: dict, daily: pd.DataFrame) -> dict:
    """The full §13 block for one candidate."""
    price = f(sess.get("price"))
    primary = pat.get("primary")
    if price is None or primary is None:
        return {"actionable": False, "reason": "no price or no structure"}

    level_key, trigger_words = TRIGGER_LEVELS.get(
        primary, ("day_high" if direction == "long" else "day_low",
                  "break of the session extreme"))
    trigger_level = f(sess.get(level_key)) or price
    atr5 = intraday_atr(sess.get("bars"))

    # Raise the trigger above any significant level stacked just beyond it.
    # Price rarely sits at one clean level: RCEL's 2026-08-07 opening-range
    # high was 6.97 with the session high at 7.05 and the prior 52-week
    # high at 7.12 directly above. Triggering at 6.97 buys into that
    # congestion, and §9 then correctly reports resistance immediately
    # overhead — which capped every breakout at B+ and made §12's own A+
    # ORB example unreachable. The resolution is not to relax §9 but to
    # trigger where a trader actually would: above the whole cluster. The
    # trade becomes "buy the break of the 52-week high", which is both the
    # better entry and the one §17 can explain.
    cleared = []
    em = f(vol.get("expected_move_pct")) or 0.0
    zone = trigger_level * max(em * 0.5, 1.0) / 100.0
    for lvl in (room_result.get("targets") or []):
        beyond = (trigger_level < lvl["price"] <= trigger_level + zone
                  if direction == "long"
                  else trigger_level - zone <= lvl["price"] < trigger_level)
        if beyond:
            cleared.append(lvl)
    if cleared:
        top = (max(cleared, key=lambda l: l["price"]) if direction == "long"
               else min(cleared, key=lambda l: l["price"]))
        trigger_level = top["price"]
        trigger_words = f"break of {', '.join(top['sources'][:2])}"

    # Entry sits beyond the trigger, not at it.
    buf = (atr5 * 0.1) if atr5 else trigger_level * 0.001
    entry = trigger_level + buf if direction == "long" else trigger_level - buf
    # Already through the level: the trigger has fired, so the reference
    # becomes the current price and the report says so, rather than
    # quoting an entry the stock left twenty minutes ago.
    triggered = (price > trigger_level if direction == "long" else price < trigger_level)
    if triggered:
        entry = price

    stop = plan_stop(sess, entry, direction, atr5)
    risk = stop["risk_per_share"]
    if not risk or risk <= 0:
        return {"actionable": False, "reason": "no valid stop", "entry": round(entry, 2),
                "trigger": f"{trigger_words} at {trigger_level:.2f}", "stop": stop}

    # Targets are levels first and multiples last, because price stops at
    # levels — but only levels far enough away to be destinations. Anything
    # inside BREAKOUT_ZONE_R of the entry is the structure being broken
    # through, and §9's *significant* levels are the only ones eligible at
    # all (see room.py).
    sign = 1 if direction == "long" else -1
    zone = risk * BREAKOUT_ZONE_R
    floor_ = entry + sign * zone

    # A target must also be reachable before the close. SPCF traded at
    # 12.25 with a 52-week high of 46.71 — a real level, and 281% away,
    # which is not a day trade. Unreachable levels are dropped rather than
    # scaled, because a target the session cannot get to produces an R:R
    # (36:1 here) that is arithmetically fine and completely fictional.
    em = f(vol.get("expected_move_pct")) or 0.0
    reach = entry * (em * MAX_TARGET_EM_MULT) / 100.0 if em > 0 else None

    def _eligible(p):
        beyond = p > floor_ if direction == "long" else p < floor_
        return beyond and (reach is None or abs(p - entry) <= reach)

    pool = [{"price": l["price"], "basis": ", ".join(l["sources"][:2])}
            for l in (room_result.get("targets") or []) if _eligible(l["price"])]

    mm = measured_move(sess, primary, direction)
    if mm and _eligible(mm[0]):
        pool.append({"price": mm[0], "basis": mm[1]})

    pool.sort(key=lambda t: abs(t["price"] - entry))

    if pool:
        t1, t1_basis = pool[0]["price"], pool[0]["basis"]
    else:
        t1, t1_basis = entry + sign * risk, "1R — no reachable level or projection"
    if len(pool) > 1:
        t2, t2_basis = pool[1]["price"], pool[1]["basis"]
    else:
        t2 = entry + sign * risk * FALLBACK_T2_R
        t2_basis = f"{FALLBACK_T2_R:g}R (no second level ahead)"

    # T2 must be beyond T1. When a lone distant level takes the T1 slot the
    # R-multiple fallback can land inside it, and the plan then reads
    # "first target 46.71, runner 15.11" — an ordering no one can trade.
    if (t2 - t1) * sign < 0:
        t1, t2 = t2, t1
        t1_basis, t2_basis = t2_basis, t1_basis

    rr = abs(t1 - entry) / risk if risk else None
    rr2 = abs(t2 - entry) / risk if risk else None

    # Blended R:R — half the position off at T1, half at T2 — is what the
    # grade is judged on, because it is how the position is actually
    # managed (docs/day_trading_prompts.md §3: "take 1/2 or 1/3 off" at
    # T1). Judging a breakout on T1 alone systematically understates it:
    # the first significant level is often the one being cleared, so RCEL's
    # 2026-08-07 ORB scored 0.61:1 to a 52-week high 2% overhead while the
    # runner reached the 7.95 measured move — a 4.3:1 leg. Neither number
    # alone describes the trade; the blend does, and all three are reported.
    rr_blended = ((abs(t1 - entry) * 0.5 + abs(t2 - entry) * 0.5) / risk
                  if risk else None)

    # The entry that WOULD give 2:1, per the project's own risk-reward
    # prompt. Far more useful than "R:R too low" on its own — it converts a
    # rejection into a limit order.
    entry_for_2r = None
    if rr is not None and rr < 2.0 and stop.get("stop"):
        s = stop["stop"]
        implied = (t1 + 2.0 * s) / 3.0 if direction == "long" else (t1 + 2.0 * s) / 3.0
        if (direction == "long" and s < implied < t1) or \
           (direction == "short" and t1 < implied < s):
            entry_for_2r = round(implied, 2)

    invalidation = (f"{'close below' if direction == 'long' else 'close above'} "
                    f"{stop['stop']:.2f} ({stop['basis']})")

    return {
        "actionable": True,
        "direction": direction,
        "trigger": f"{trigger_words} at {trigger_level:.2f}",
        "trigger_level": round(trigger_level, 2),
        "triggered": triggered,
        "entry": round(entry, 2),
        "stop": stop["stop"],
        "stop_basis": stop["basis"],
        "stop_too_wide": stop.get("too_wide"),
        "stop_too_tight": stop.get("too_tight"),
        "stop_note": stop.get("note"),
        "risk_per_share": round(risk, 4),
        # Magnitude, not a signed change. On a short the stop sits ABOVE
        # the entry, so the signed form returned "-1.1% of entry" — risk
        # reported as a negative number, which reads as a gain.
        "risk_pct_of_price": abs(risk / entry * 100.0) if entry else None,
        "target1": round(t1, 2), "target1_basis": t1_basis,
        "target2": round(t2, 2), "target2_basis": t2_basis,
        "expected_move_pct": vol.get("expected_move_pct"),
        "rr": round(rr, 2) if rr else None,
        "rr_target2": round(rr2, 2) if rr2 else None,
        "rr_blended": round(rr_blended, 2) if rr_blended else None,
        # Whether T1 came from structure at all. When both targets are bare
        # R-multiples the blend of 1R and 3R is exactly 2.00 — precisely
        # the A-grade threshold — so TNDM and OMDA cleared the R:R gate on
        # arithmetic rather than evidence. §12 asks for independent
        # confirmations, and a number that is true by definition is not one.
        "targets_structural": bool(pool),
        "entry_for_2r": entry_for_2r,
        "invalidation": invalidation,
        "atr_5min": round(atr5, 4) if atr5 else None,
    }


def estimate_slippage(entry: float, spread_pct: float | None,
                      atr5: float | None, position_value: float | None = None,
                      minute_dollar_volume: float | None = None) -> dict:
    """Expected slippage per share, entry and exit combined.

    Real risk on a small cap is not `entry − stop`. It is the fill you
    actually get, plus the fill you get on the way out — and the exit is
    the worse of the two, because a stop triggers precisely when the book
    is thinnest. Three components:

      * half the spread on the way in, half on the way out;
      * an intraday-volatility floor, since a stop in fast tape fills
        past the trigger even with a tight quote;
      * market impact when the position is large relative to what the
        stock trades in a minute.

    Spread is unavailable outside market hours, so the volatility floor
    carries the estimate then and `basis` says which one is in force.
    """
    entry = f(entry)
    if not entry or entry <= 0:
        return {"per_share": None, "basis": "no entry price"}

    parts, basis = [], []
    if spread_pct is not None and spread_pct > 0:
        # Cross half the spread in, half out.
        parts.append(entry * spread_pct / 100.0)
        basis.append(f"{spread_pct:.2f}% spread, both sides")
    if atr5 and atr5 > 0:
        parts.append(atr5 * VOLATILITY_SLIPPAGE_ATR)
        basis.append(f"{VOLATILITY_SLIPPAGE_ATR:.2f}x 5-min ATR")

    impact = 0.0
    if position_value and minute_dollar_volume and minute_dollar_volume > 0:
        share_of_minute = position_value / minute_dollar_volume
        if share_of_minute > IMPACT_FREE_SHARE:
            impact = entry * IMPACT_BPS_PER_UNIT * (share_of_minute - IMPACT_FREE_SHARE)
            basis.append(f"{share_of_minute*100:.0f}% of a minute's dollar volume")

    if not parts:
        return {"per_share": None, "basis": "no spread or ATR to estimate from"}
    per_share = max(parts) + impact
    per_share = max(per_share, MIN_SLIPPAGE_PER_SHARE)
    return {"per_share": round(per_share, 4), "basis": " + ".join(basis),
            "impact_per_share": round(impact, 4)}


def size(plan: dict, settings: dict, avg_volume: float | None,
         float_shares: float | None = None, spread_pct: float | None = None,
         minute_dollar_volume: float | None = None,
         risk_multiplier: float = 1.0) -> dict:
    """§14 position sizing, risk-first. Score is deliberately not an input.

    Risk-first means the maximum acceptable loss sets the share count and
    every other limit can only reduce it. The previous version computed
    the same MIN() but reported whichever constraint bound last, and in
    practice the 20% allocation cap bound on every candidate — so the
    output read "binding constraint: 20% allocation cap" with $450 of a
    $1,000 risk budget used, which is an allocation-first tool wearing a
    risk-first label. The cap still applies; it is just no longer allowed
    to be the reason a day trade is sized.

    `risk_multiplier` is how execution risk feeds back into size (§11/§12):
    a high halt risk or an unverified offering shrinks the position rather
    than merely printing a warning next to a full-size one.
    """
    entry, structural_risk = f(plan.get("entry")), f(plan.get("risk_per_share"))
    if not entry or not structural_risk or structural_risk <= 0:
        return {"shares": 0, "reason": "no valid risk per share"}

    capital = settings["capital"]
    max_risk = capital * settings["risk_pct"] / 100.0 * max(0.0, min(1.0, risk_multiplier))

    # First pass with no impact term, to get a position value to estimate
    # impact against; then re-price. One iteration is enough — impact is a
    # small correction, and iterating to a fixed point would imply a
    # precision this estimate does not have.
    atr5 = f(plan.get("atr_5min"))
    slip = estimate_slippage(entry, spread_pct, atr5)
    true_risk = structural_risk + (slip.get("per_share") or 0.0)
    provisional = int(max_risk / true_risk) if true_risk > 0 else 0
    slip = estimate_slippage(entry, spread_pct, atr5,
                             position_value=provisional * entry,
                             minute_dollar_volume=minute_dollar_volume)
    true_risk = structural_risk + (slip.get("per_share") or 0.0)

    caps = [(int(max_risk / true_risk) if true_risk > 0 else 0,
             f"account risk ({settings['risk_pct']:.2f}% = ${max_risk:,.0f})")]
    caps.append((int(capital * settings["max_allocation_pct"] / 100.0 / entry),
                 f"{settings['max_allocation_pct']:.0f}% allocation cap"))
    if avg_volume and avg_volume > 0:
        caps.append((int(avg_volume * settings["max_adv_pct"] / 100.0),
                     f"{settings['max_adv_pct']:.1f}% of average volume"))
    if minute_dollar_volume and minute_dollar_volume > 0:
        caps.append((int(minute_dollar_volume * MAX_MINUTE_PARTICIPATION / entry),
                     f"{MAX_MINUTE_PARTICIPATION*100:.0f}% of a minute's dollar volume"))
    if float_shares and float_shares < 2_000_000:
        caps.append((int(float_shares * 0.001),
                     "micro-float participation limit (0.1% of float)"))

    shares, binding = min(caps, key=lambda c: c[0])
    shares = max(0, shares)
    risk_shares = caps[0][0]
    position_value = shares * entry

    liquidity_pct = (position_value / minute_dollar_volume * 100.0
                     if minute_dollar_volume else None)
    return {
        "shares": shares,
        "position_value": round(position_value, 2),
        "structural_risk_per_share": round(structural_risk, 4),
        "slippage_per_share": slip.get("per_share"),
        "slippage_basis": slip.get("basis"),
        "true_risk_per_share": round(true_risk, 4),
        "dollar_risk": round(shares * true_risk, 2),
        "max_dollar_risk": round(max_risk, 2),
        "risk_budget_used_pct": (round(shares * true_risk / max_risk * 100.0, 1)
                                 if max_risk > 0 else None),
        "pct_of_capital": round(position_value / capital * 100.0, 2) if capital else None,
        "binding_constraint": binding,
        "risk_is_binding": shares == risk_shares,
        "risk_based_shares": risk_shares,
        "position_liquidity_pct": (round(liquidity_pct, 1)
                                   if liquidity_pct is not None else None),
        "risk_multiplier": risk_multiplier,
        "capital": capital,
        "risk_pct": settings["risk_pct"],
    }
