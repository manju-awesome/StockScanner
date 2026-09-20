"""
engine.py — §10 confluence, §12 setup quality, §17 decision
===========================================================
Where the sub-scores become one answer, and — more importantly — where the
things that are *not* allowed to trade off against each other are kept
apart.

Three numbers, not one
-----------------------
§10 asks for CONFLUENCE, SETUP and TRADEABILITY separately, and then says
a high setup must not compensate for poor tradeability. Those two
instructions only work together if tradeability is a gate rather than a
seventh weight, so that is how it is built: `universe.compute()` returns
`tradeable`, and a False there caps the outcome at WATCH — NOT TRADEABLE
no matter what the other 90 points say. Room (§9) works the same way —
`blocked` refuses A+/A rather than subtracting from them.

    CONFLUENCE   100 pts, weighted per §10
    SETUP        §6 structure quality alone
    TRADEABILITY §11 execution quality alone — a gate

Confirmations, not points
--------------------------
§12 says A+ requires multiple *independent* confirmations and §18 says a
stock becomes A+ only when several independent factors agree. A weighted
score cannot express that: 85 points from two enormous factors and 85 from
eight modest ones are the same number and very different trades. So
confirmations are counted as discrete booleans and gate the grade
alongside the score. Both must clear.

The squeeze rule
-----------------
§4: high short interest becomes strongly bullish only alongside catalyst +
unusual volume + price strength + breakout. supply.py deliberately does
not apply that bonus, because it cannot see those four things. It is
applied here, once, and only when all four hold.
"""

from __future__ import annotations

from datetime import datetime, time as dtime

import pandas as pd

from stockanalysis.core.daytrade import (
    catalyst as C, entry as EN, plan as P, profiles as PR, regime as R,
    room as RM, strength as S, structure as ST, supply as SU, universe as U,
    volatility as V, volume as VOL,
)
from stockanalysis.core.daytrade._common import MARKET_TZ, f, points
from stockanalysis.core.daytrade.datafeed import sector_etf

# §10's weighting, for the small-cap profile. Kept as a module constant
# because callers and tests reference it, but the live weights come from
# the profile — see core/daytrade/profiles.py. Every profile weights the
# same eight blocks so the arithmetic is identical and only the
# calibration differs.
WEIGHTS = dict(PROFILES_SMALL_WEIGHTS := {
    "volatility": 15,
    "supply": 15,
    "catalyst": 20,
    "volume": 15,
    "setup": 15,
    "market": 10,
    "regime": 5,
    "room": 5,
})

# §12 grade thresholds: (grade, min confluence, min confirmations, min R:R).
GRADE_RULES = (
    ("A+", 80, 7, 2.0),
    ("A",  70, 5, 1.75),
    ("B+", 60, 4, 1.5),
    ("B",  50, 3, 1.2),
    ("C",  40, 2, 0.0),
)

# Below this coverage the confluence score is built from too little data to
# label. Same principle as the long-term engine: a 78 from half its inputs
# is not a B+ setup, it is an unmeasured one.
MIN_COVERAGE = 0.6

# §2's execution thresholds. These are gates, not bands — each one is a
# reason an otherwise-good setup cannot be entered at this price.
MAX_SPREAD_PCT = 1.0
MIN_ROOM_RATIO = 0.5              # §7: under half the expected move, avoid
MIN_RR = 2.0
MIN_DOLLAR_VOLUME = 5_000_000
MAX_BEYOND_TRIGGER_ATR = 1.0      # §5: past 1 ATR beyond trigger you are late
MAX_POSITION_LIQUIDITY_PCT = 25.0  # of one minute's dollar volume


def _is_live(session_date) -> bool:
    """Is `session_date` today, with the market currently open?"""
    now = datetime.now(MARKET_TZ)
    if now.date() != session_date or now.weekday() >= 5:
        return False
    return dtime(4, 0) <= now.time() <= dtime(20, 0)


def _confirmations(vol, sup, cat, rs, vc, pat, room_res, trad, plan_res,
                   profile=None) -> list[dict]:
    """§12/§18's independent confirmations, each named and each a boolean.

    Returned as a list of {name, ok, detail} rather than a count so the
    report can show which ones are missing — "6 of 12" is not actionable,
    "no sector confirmation and R:R only 1.4" is.
    """
    direction = plan_res.get("direction") or "long"
    p = profile or PR.DEFAULT
    # Thresholds come from the profile: RVOL 1.3 is noise on a small-cap
    # runner and a real institutional footprint on a megacap, and one
    # constant cannot be right for both.
    rvol_bar, atr_bar, gap_bar = (p["rvol_significant"], p["atr_pct_min"],
                                  p["gap_min"])
    checks = [
        ("Fresh catalyst", bool(cat.get("fresh")), cat.get("detail")),
        # Float only earns a confirmation slot where supply is actually the
        # mechanism. On a megacap no plausible day's volume moves the
        # float, so demanding a small one would reject every valid setup
        # for a reason that has nothing to do with the trade.
        (("Float under 50M" if p["float_matters"] else "Liquidity depth"),
         (bool(sup.get("float_shares") and sup["float_shares"] < 50_000_000)
          if p["float_matters"]
          else bool((trad.get("dollar_volume") or 0) >= p["min_dollar_volume"])),
         (f"{sup['float_shares']/1e6:.1f}M" if sup.get("float_shares") else "unknown")
         if p["float_matters"]
         else (f"${(trad.get('dollar_volume') or 0)/1e6:.0f}M traded")),
        (f"RVOL above {rvol_bar:g}", bool((vol.get("rvol") or 0) >= rvol_bar),
         f"{vol.get('rvol')}x" if vol.get("rvol") else "unavailable"),
        (f"Gap above {gap_bar:g}%", bool(abs(vol.get("gap_pct") or 0) >= gap_bar),
         f"{vol.get('gap_pct'):+.1f}%" if vol.get("gap_pct") is not None else "unavailable"),
        (f"ATR% above {atr_bar:g}", bool((vol.get("atr_pct") or 0) >= atr_bar),
         f"{vol.get('atr_pct'):.1f}%" if vol.get("atr_pct") else "unavailable"),
        ("Correct side of VWAP",
         pat.get("above_vwap") is (direction == "long"),
         "above VWAP" if pat.get("above_vwap") else "below VWAP"),
        ("Structural trigger", bool(pat.get("primary")), pat.get("primary") or "none"),
        ("Volume expansion confirmed",
         bool(vc.get("score") is not None and vc["score"] >= 65 and not vc.get("expansion_failed")),
         vc.get("sequence") or "not detected"),
        ("Relative strength vs SPY", bool((rs.get("vs_spy") or 0) > 0),
         f"{rs.get('vs_spy'):+.1f}pp" if rs.get("vs_spy") is not None else "unavailable"),
        ("Sector confirmation", bool(rs.get("sector_confirms")),
         f"{rs.get('vs_sector'):+.1f}pp vs {rs.get('sector_etf')}"
         if rs.get("vs_sector") is not None else "unavailable"),
        # Open air — nothing overhead at all — is the best possible case
        # and has no ratio to report, so it must pass explicitly. Reading
        # its absent `room_ratio` as 0 marked the strongest candidates as
        # failing the room check.
        ("Room to next level",
         bool(not room_res.get("blocked")
              and (room_res.get("nearest") is None
                   or (room_res.get("room_ratio") or 0) >= 0.75)),
         room_res.get("detail")),
        ("R:R at least 2",
         bool((plan_res.get("rr_blended") or 0) >= 2.0
              and plan_res.get("targets_structural")),
         (f"{plan_res.get('rr_blended')}:1 blended "
          f"(T1 {plan_res.get('rr')}:1, T2 {plan_res.get('rr_target2')}:1)"
          + ("" if plan_res.get("targets_structural")
             else " — but both targets are bare R-multiples, not levels")
          ) if plan_res.get("rr_blended") else "unavailable"),
        (f"Spread under {p['max_spread_pct']:g}%",
         bool(trad.get("spread_pct") is not None
              and trad["spread_pct"] <= p["max_spread_pct"]),
         f"{trad['spread_pct']:.2f}%" if trad.get("spread_pct") is not None
         else trad.get("spread_note")),
        # §13/§3: a stop wider than ~1.5x the 5-min ATR is too wide for a
        # day trade — reduce size or skip. The plan already computes and
        # prints that warning, but without this check it did not reach the
        # grade, so OMDA came back A+ carrying "stop is 2.1x the 5-min ATR
        # — too wide for a day trade" in its own panel. `is False` rather
        # than `not ...` because an unmeasurable stop width is unknown, and
        # unknown must not confirm.
        ("Stop within 1.5x 5-min ATR", plan_res.get("stop_too_wide") is False,
         plan_res.get("stop_note")
         or (f"{plan_res['risk_per_share'] / plan_res['atr_5min']:.2f}x"
             if plan_res.get("atr_5min") and plan_res.get("risk_per_share")
             else "5-min ATR unavailable")),
    ]
    return [{"name": n, "ok": bool(ok), "detail": d} for n, ok, d in checks]


def execution_gate(plan_res: dict, room_res: dict, trad: dict, entry_res: dict,
                   sizing: dict) -> dict:
    """The §2 gate: every execution condition that must hold before a
    candidate may be called A+.

    This is the separation the confluence score cannot express. Confluence
    asks whether the stock is worth trading today; this asks whether *this
    price* is worth paying, and the two are independent — a setup keeps its
    catalyst, float, RVOL and structure while its entry decays from
    excellent to unenterable. Scoring them together let a 92-confluence
    name print 🔥 A+ LONG while its own panel reported 2.9% of room, a stop
    2.4x the 5-min ATR and an unconfirmable spread.

    Every condition is returned with its verdict, so a failure names itself
    rather than silently subtracting a few points. `passed` is False if any
    condition is False; conditions that could not be measured are collected
    in `unverified` and also block A+ — on an execution check, "unknown"
    and "bad" have the same consequence for a real order.
    """
    checks: list[dict] = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})

    spread = trad.get("spread_pct")
    add("Spread acceptable",
        None if spread is None else spread <= MAX_SPREAD_PCT,
        f"{spread:.2f}%" if spread is not None else trad.get("spread_note"))

    room_ratio = room_res.get("room_ratio")
    if room_res.get("nearest") is None:
        add("Room to next level", True, "open air — nothing overhead")
    else:
        add("Room to next level",
            None if room_ratio is None else room_ratio >= MIN_ROOM_RATIO,
            room_res.get("detail"))

    rr = plan_res.get("rr_blended")
    add("Risk/reward >= 2", None if rr is None else rr >= MIN_RR,
        f"{rr:.2f}:1 blended" if rr is not None else "unavailable")

    wide = plan_res.get("stop_too_wide")
    add("Stop within 1.5x 5-min ATR", None if wide is None else not wide,
        plan_res.get("stop_note") or "within range")

    dv = trad.get("dollar_volume")
    add("Dollar volume sufficient", None if dv is None else dv >= MIN_DOLLAR_VOLUME,
        f"${dv/1e6:.1f}M" if dv else "unavailable")

    beyond = entry_res.get("beyond_trigger_atr")
    add("Entry not extended",
        None if beyond is None else beyond <= MAX_BEYOND_TRIGGER_ATR,
        (f"{beyond:.2f} ATR past trigger" if beyond is not None else "unavailable"))

    add("Not chasing", not entry_res.get("chase_blocked"),
        f"chase score {entry_res.get('chase_score')} — "
        + "; ".join(entry_res.get("chase_reasons") or ["clear"]))

    liq = sizing.get("position_liquidity_pct")
    add("Liquidity for this size",
        None if liq is None else liq <= MAX_POSITION_LIQUIDITY_PCT,
        (f"position is {liq:.0f}% of a minute's dollar volume"
         if liq is not None else "unavailable"))

    failed = [c["name"] for c in checks if c["ok"] is False]
    unverified = [c["name"] for c in checks if c["ok"] is None]
    return {"checks": checks, "failed": failed, "unverified": unverified,
            "passed": not failed and not unverified}


def _grade(confluence, n_confirm, rr, tradeable, blocked, coverage,
           missing_required=(), stop_too_wide=False):
    """§12 classification. Gates first, then the score."""
    if not tradeable:
        return "NO TRADE", "tradeability gate"
    if confluence is None or coverage < MIN_COVERAGE:
        return "NO TRADE", f"insufficient data (coverage {coverage:.0%})"
    # Coverage alone is too permissive for the blocks this engine is
    # actually about. A recent IPO with ten daily bars has no ATR and no
    # RVOL baseline, so §3 is entirely unmeasured — yet the remaining 80
    # points of budget still cleared MIN_COVERAGE and STLN graded A+ with
    # both volatility columns blank. A day-trade scanner that cannot
    # measure volatility or structure has not found a setup; it has found
    # a stock it knows nothing about.
    if missing_required:
        return "C", ("cannot measure " + ", ".join(missing_required)
                     + " — no grade above C")
    for grade, min_score, min_conf, min_rr in GRADE_RULES:
        if confluence >= min_score and n_confirm >= min_conf and (rr or 0) >= min_rr:
            # §9's gate: significant resistance immediately overhead means
            # the best available label is B+, whatever the score.
            if blocked and grade in ("A+", "A"):
                return "B+", "downgraded — resistance immediately ahead (§9)"
            # A+ is the label for a setup where the risk geometry is clean
            # too. A stop that has to sit beyond 1.5x the 5-min ATR is a
            # defect in the setup even when the thesis is strong, and
            # printing "A+ LONG" directly above this plan's own "too wide
            # for a day trade" warning is the kind of self-contradiction
            # that makes a tool untrustworthy. Sizing already responds —
            # wider stop, fewer shares — so this caps the label, not the
            # trade.
            if stop_too_wide and grade == "A+":
                return "A", "capped at A — stop wider than 1.5x the 5-min ATR (§13)"
            return grade, None
    return "NO TRADE", "below the C threshold"


def _decision(grade, tradeable, direction, blocked, plan_res):
    """§17's six labels."""
    if not tradeable:
        return "⚪ WATCH — NOT TRADEABLE"
    if grade == "NO TRADE":
        return "⛔ NO TRADE"
    if direction == "short":
        return "🔴 SHORT" if grade in ("A+", "A") else "⚪ WATCHLIST"
    if grade == "A+":
        return "🔥 A+ LONG"
    if grade == "A":
        return "🟢 A LONG"
    if grade == "B+":
        return "🟡 WATCH FOR CONFIRMATION"
    return "⚪ WATCHLIST"


def _action(grade, tradeable, direction, plan_res, entry_res, gate) -> tuple[str, str]:
    """What to actually do, right now — returns (action, why).

    This is the column that answers the question a grade cannot. A grade
    describes the opportunity and is stable across the session; the action
    describes the price in front of you and changes every bar. Their
    disagreement is the useful output: "A+ setup, but you are 2 ATR late"
    is a far more actionable statement than either half alone.

    Ordered by how disqualifying each condition is, most first, because a
    stock can be several of these at once and the worst one governs.
    """
    if not tradeable:
        return "🔴 AVOID", "execution characteristics fail — not tradeable at any size"
    if grade == "NO TRADE":
        return "🔴 AVOID", "does not clear the C threshold"

    # Being late is checked before quality: a setup that has already run is
    # not improved by having been excellent, and this is the single case
    # the old engine got most wrong — it graded a triggered, extended name
    # A+ and printed it as a fresh entry.
    if entry_res.get("chase_blocked"):
        return ("🟠 EXTENDED — DO NOT CHASE",
                "chase score "
                f"{entry_res.get('chase_score')}: "
                + "; ".join(entry_res.get("chase_reasons") or []))
    if plan_res.get("triggered") and entry_res.get("extended_past_trigger"):
        return ("🟠 MISSED ENTRY — DO NOT CHASE",
                f"trigger already fired and price is "
                f"{entry_res.get('beyond_trigger_atr'):.1f} ATR beyond it — "
                f"wait for a pullback or the next structure")

    entry_grade = entry_res.get("entry_grade")
    if gate.get("failed"):
        return ("🟡 SETUP OK — WAIT FOR BETTER ENTRY",
                "execution conditions failed: " + ", ".join(gate["failed"]))
    if gate.get("unverified"):
        return ("🟡 SETUP OK — EXECUTION UNVERIFIED",
                "cannot confirm: " + ", ".join(gate["unverified"]))

    if grade in ("A+", "A") and entry_grade in ("ENTER", "HIGH QUALITY"):
        if plan_res.get("triggered"):
            return ("🔥 ENTER NOW",
                    f"triggered, entry score {entry_res.get('score')}, "
                    f"still within {entry_res.get('beyond_trigger_atr'):.2f} ATR of the trigger")
        return ("🟢 WAIT FOR BREAKOUT",
                f"ready — enter on {plan_res.get('trigger')}")
    if entry_grade == "WAIT":
        return ("🟢 WAIT FOR PULLBACK",
                f"entry score {entry_res.get('score')} — better fill available closer to VWAP")
    return "🟡 WATCH", f"entry score {entry_res.get('score')} ({entry_grade})"


def evaluate(ticker: str, row: dict, info: dict, news: list, bars_1m: pd.DataFrame,
             bars_5m: pd.DataFrame, daily: pd.DataFrame, context: dict,
             regime_result: dict, settings: dict, asof=None, now=None,
             profile=None) -> dict | None:
    """Score one candidate end to end. None when there is nothing to score.

    `profile` selects the market-cap calibration (see profiles.py). None
    means pick it from the stock's own market cap, which is what makes a
    mixed-cap scan coherent: each row is judged against the yardstick for
    its class, and the profile travels with the row so the report can say
    which one was used.
    """
    sess = ST.build_session(bars_1m, bars_5m, daily, asof=asof)
    if sess is None:
        return None
    # The screen supplies a market cap; a watchlist supplies nothing, so it
    # is derived from whatever `.info` did return. See infer_market_cap.
    market_cap, cap_basis = PR.infer_market_cap(
        f(row.get("market_cap")) or f(info.get("marketCap")),
        info.get("sharesOutstanding"), info.get("floatShares"),
        f(sess.get("price")) or f(row.get("price")))
    prof = profile or PR.for_market_cap(market_cap)
    sess["is_live"] = _is_live(sess["asof"])

    # Catalyst age is measured from the session under examination, not from
    # the wall clock. Scanning Friday's tape on a Sunday, every headline
    # that drove Friday's move is ~50 hours old against `now` and the §2
    # decay would zero out the very catalyst being analysed — the scan
    # would report "no fresh catalyst" for a stock that gapped 37% on
    # earnings that morning. The reference point is the last bar of the
    # session, so freshness means "fresh as of the tape being read".
    if now is None and not sess["is_live"]:
        bars = sess.get("bars")
        if bars is not None and not bars.empty:
            now = bars.index[-1].to_pydatetime()

    passed, reject_reasons = U.passes_universe(row, info, sess, prof, market_cap)

    pat = ST.detect_patterns(sess)
    setup = ST.score_setup(sess, pat)
    direction = setup.get("direction") or "long"

    vol = V.compute(sess, daily, bars_5m)
    sup = SU.compute(info, avg_volume=f(info.get("averageVolume")))
    cat = C.compute(news, now=now, ticker=ticker,
                    company=info.get("longName") or row.get("name"))
    vc = VOL.compute(sess, rvol_accel=vol.get("rvol_accel"))
    etf = sector_etf(info.get("sector"), info.get("industry"))
    rs = S.compute(sess, context, etf)
    trad = U.compute(row, info, sess, sup, prof)

    # Room is computed twice on purpose. The first pass discovers the
    # levels the plan needs in order to place its trigger; the second
    # measures room from the entry the plan actually chose. Judging §9
    # from the current price would answer the wrong question — the trade
    # is not taken here, it is taken at the trigger, and on a breakout
    # those are on opposite sides of the congestion.
    room_res = RM.compute(sess, daily, direction, vol.get("expected_move_pct"))
    plan_res = P.build(sess, pat, direction, vol, room_res, daily)
    if plan_res.get("actionable"):
        room_res = RM.compute(sess, daily, direction, vol.get("expected_move_pct"),
                              entry=plan_res.get("entry"))

    entry_res = EN.compute(sess, plan_res, room_res, vol, vc, trad, rs,
                           regime_result.get("label", "MIXED"), direction)

    # Execution risk shrinks the position rather than only printing a
    # warning beside a full-size one (§11/§12). Unverified dilution is the
    # one that matters most on a small-cap runner, and it is unverifiable
    # by construction here — so it costs size on every candidate rather
    # than pretending some are clean.
    risk_mult = 1.0
    if sup.get("micro_float"):
        risk_mult *= 0.5
    if entry_res.get("chase_score", 0) >= 2:
        risk_mult *= 0.75
    sizing = (P.size(plan_res, settings, trad.get("avg_volume"),
                     sup.get("float_shares"), spread_pct=trad.get("spread_pct"),
                     minute_dollar_volume=sess.get("minute_dollar_volume"),
                     risk_multiplier=risk_mult)
              if plan_res.get("actionable") else {"shares": 0, "reason": "no actionable plan"})

    gate = execution_gate(plan_res, room_res, trad, entry_res, sizing)

    # §4 squeeze credit — applied here, where all four preconditions are
    # visible, and nowhere else.
    squeeze = bool(
        sup.get("squeeze_ready")
        and cat.get("fresh")
        and (vol.get("rvol") or 0) >= 2.0
        and pat.get("above_vwap") is True
        and bool({"ORB_BREAKOUT", "PDH_BREAKOUT", "PM_HIGH_BREAKOUT",
                  "CONSOLIDATION_BREAKOUT"} & set(pat.get("patterns") or []))
    )

    # §10 confluence: each engine's 0-100 converted once onto its budget,
    # then renormalised over the budget that was actually measurable.
    weights = PR.weights(prof)
    blocks = {
        "volatility": vol.get("score"), "supply": sup.get("score"),
        "catalyst": cat.get("score"), "volume": vc.get("score"),
        "setup": setup.get("score"), "market": rs.get("score"),
        # Regime and room are scored blocks rather than side-notes because
        # the mid- and large-cap profiles need them weighted: on a megacap
        # the index and the sector are not context, they are most of the
        # trade. At small cap they carry 5 points each and the behaviour is
        # essentially unchanged.
        "regime": R.score_for(regime_result, direction),
        "room": room_res.get("score"),
    }
    earned = {k: points(v, weights[k]) for k, v in blocks.items()}
    got_budget = sum(weights[k] for k, v in earned.items() if v is not None)
    coverage = got_budget / sum(weights.values())
    confluence = (round(sum(v for v in earned.values() if v is not None)
                        / got_budget * 100.0) if got_budget else None)

    if confluence is not None:
        if squeeze:
            confluence = min(100, confluence + 5)
        adj, adj_reason = R.confidence_adjustment(regime_result.get("label", "MIXED"), direction)
        confluence = max(0, min(100, confluence + adj))
    else:
        adj_reason = "no confluence score"

    confirmations = _confirmations(vol, sup, cat, rs, vc, pat, room_res, trad,
                                   plan_res, prof)
    n_confirm = sum(1 for c in confirmations if c["ok"])

    # Block-level coverage is too coarse here: §3 still returns a score off
    # gap and dollar volume alone, so a name with no ATR and no RVOL
    # baseline kept a volatility block and STLN graded A+ with both
    # columns blank. ATR% is named explicitly because the stop distance,
    # the room ratio and the position size are all denominated in it — no
    # ATR% means the trade cannot be sized, not merely that it scores less.
    missing_required = []
    if vol.get("atr_pct") is None:
        missing_required.append("ATR%")
    if setup.get("score") is None:
        missing_required.append("intraday structure")
    missing_required = tuple(missing_required)
    grade, grade_note = _grade(confluence, n_confirm, plan_res.get("rr_blended"),
                               trad.get("tradeable"), room_res.get("blocked"),
                               coverage, missing_required,
                               stop_too_wide=bool(plan_res.get("stop_too_wide")))
    if not passed:
        grade, grade_note = "NO TRADE", "; ".join(reject_reasons)

    # §2: A+ is reserved for setups whose execution conditions ALL hold.
    # Everything else about the name may be excellent — that is what the
    # confluence score and the A/B+ grades are for.
    if grade == "A+" and not gate["passed"]:
        blocking = gate["failed"] or gate["unverified"]
        grade = "A"
        grade_note = ("A+ withheld — execution: " + ", ".join(blocking[:3]))

    decision = _decision(grade, trad.get("tradeable"), direction,
                         room_res.get("blocked"), plan_res)
    action, action_why = _action(grade, trad.get("tradeable"), direction,
                                 plan_res, entry_res, gate)

    return {
        "ticker": ticker,
        "name": info.get("longName") or row.get("name"),
        "profile": prof["key"], "profile_label": prof["label"],
        "market_cap": market_cap, "market_cap_basis": cap_basis,
        "weights": weights,
        "asof": sess["asof"], "is_live": sess["is_live"],
        "price": sess.get("price"), "session": sess,
        "direction": direction,
        "confluence": confluence, "coverage": round(coverage, 3),
        "setup_score": setup.get("score"), "setup_detail": setup.get("detail"),
        "tradeability": trad.get("score"), "tradeable": trad.get("tradeable"),
        "gate_reason": trad.get("gate_reason"),
        "grade": grade, "grade_note": grade_note, "decision": decision,
        "action": action, "action_why": action_why,
        "entry_score": entry_res.get("score"),
        "entry_grade": entry_res.get("entry_grade"),
        "chase_score": entry_res.get("chase_score"),
        "chase_label": entry_res.get("chase_label"),
        "entry": entry_res, "gate": gate,
        "confirmations": confirmations, "n_confirmations": n_confirm,
        "n_checks": len(confirmations),
        "squeeze_credit": squeeze,
        "regime_adjustment": adj_reason,
        "blocks": blocks, "block_points": earned,
        "volatility": vol, "supply": sup, "catalyst": cat, "volume": vc,
        "strength": rs, "room": room_res, "patterns": pat, "plan": plan_res,
        "sizing": sizing, "universe_pass": passed, "reject_reasons": reject_reasons,
        "warnings": list(trad.get("warnings") or []) + list(vc.get("warnings") or [])
                    + ([cat["headline"]] if cat.get("dilution_headline") else []),
    }


# Actionability first. §16 lists confluence at the top of its ranking, and
# ranking by it alone put the most *interesting* stocks first rather than
# the most *tradeable* ones — a 92-confluence name you cannot enter
# outranked an 80 you can. Confluence still breaks ties within an action
# band, so the spec's ordering survives where it discriminates.
ACTION_RANK = {
    "🔥 ENTER NOW": 0,
    "🟢 WAIT FOR BREAKOUT": 1,
    "🟢 WAIT FOR PULLBACK": 2,
    "🟡 SETUP OK — WAIT FOR BETTER ENTRY": 3,
    "🟡 SETUP OK — EXECUTION UNVERIFIED": 4,
    "🟡 WATCH": 5,
    "🟠 MISSED ENTRY — DO NOT CHASE": 6,
    "🟠 EXTENDED — DO NOT CHASE": 7,
    "🔴 AVOID": 8,
}


def rank_key(r: dict):
    return (
        ACTION_RANK.get(r.get("action"), 9),
        -(r.get("entry_score") or 0),
        -(r.get("confluence") or 0),
        -(r.get("setup_score") or 0),
        -(r.get("tradeability") or 0),
        -(r.get("catalyst", {}).get("score") or 0),
        -(r.get("volatility", {}).get("rvol") or 0),
        (r.get("supply", {}).get("float_shares") or float("inf")),
    )
