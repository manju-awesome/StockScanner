"""
entry.py — Entry Score and Chase Score
======================================
The question the rest of the package does not answer: *should I enter at
this price, right now?*

Every other engine here scores the stock or the setup, and both are
properties of the day. Entry quality is a property of the price you are
looking at, and it decays continuously while the setup stays exactly as
good. A stock can hold an A+ opportunity all morning and pass through
excellent, acceptable, late and unenterable on the way — the confluence
score cannot express that, because none of its inputs move when price
extends away from the trigger.

That is the whole reason for this module. Without it the engine's advice
degrades the longer a move works, while its score stays flat.

Everything is normalised in ATR, not percent
---------------------------------------------
"3% above VWAP" is a chase on a stock with a 4% daily range and a normal
morning wobble on one with a 25% range. So extension is measured in
5-minute ATRs throughout, which is the unit the stop is already denominated
in, and the two therefore compare directly: an entry 2 ATR above the
trigger with a stop 1 ATR below it is not a trade, and the numbers say so
without needing a rule about the stock's price or sector.

Chase Score is a count, not a weight
-------------------------------------
Six independent ways of being late, each worth one point. A count rather
than a blended score because the failure mode is disjunctive: being 2 ATR
above VWAP is disqualifying on its own, and averaging it against five
healthy readings is exactly how a chase gets rationalised. The Entry Score
answers "how good is this fill"; the Chase Score answers "am I the last
buyer", and the second question survives a good answer to the first.
"""

from __future__ import annotations

import pandas as pd

from stockanalysis.core.daytrade._common import band, blend, f

# §20's weights.
WEIGHTS = {
    "trigger_distance": 20,
    "vwap_distance": 15,
    "room": 15,
    "risk_reward": 15,
    "volume": 10,
    "candle": 10,
    "spread": 5,
    "market": 5,
    "pullback": 5,
}

# Extension bands in 5-minute ATRs, straight from §4/§5.
TRIGGER_DIST_BANDS = ((0.5, 100), (1.0, 80), (1.5, 50), (2.5, 20), (99, 0))
VWAP_DIST_BANDS = ((0.5, 100), (1.0, 85), (1.5, 60), (2.0, 30), (99, 5))
# §7's room bands, as multiples of the expected remaining move.
ROOM_BANDS = ((0.5, 10), (1.0, 45), (2.0, 80), (99, 100))
RR_BANDS = ((1.0, 0), (1.5, 30), (2.0, 60), (3.0, 85), (99, 100))
SPREAD_BANDS = ((0.3, 100), (0.6, 80), (1.0, 55), (2.0, 20), (99, 0))

# Entry Score → what to do about it (§20).
ENTRY_BANDS = ((60, "AVOID"), (70, "LOW QUALITY"), (80, "WAIT"),
               (90, "HIGH QUALITY"), (101, "ENTER"))

# Chase Score → label (§6).
CHASE_LABELS = ((1, "🟢 fine"), (3, "🟡 caution"), (99, "🔴 do not chase"))
CHASE_BLOCK = 4          # at or above this, no entry is allowed at this price
EXTENDED_TRIGGER_ATR = 1.5   # past this, the trigger has left without you


def _five_min(bars: pd.DataFrame) -> pd.DataFrame:
    if bars is None or bars.empty:
        return pd.DataFrame()
    return bars.resample("5min").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last",
         "Volume": "sum"}).dropna()


def candle_quality(bars: pd.DataFrame, direction: str) -> dict:
    """§16: how convincing is the most recent 5-minute candle.

    Body share, close location within the range, and volume against the
    session's own median. A breakout that closes on its high on double
    volume and one that closes mid-range with a long upper wick are the
    same "breakout" to a level-based detector and opposite trades.
    """
    agg = _five_min(bars)
    if len(agg) < 3:
        return {"score": None, "detail": "too few 5-min candles"}
    c = agg.iloc[-1]
    rng = float(c["High"] - c["Low"])
    if rng <= 0:
        return {"score": None, "detail": "zero-range candle"}

    body = abs(float(c["Close"] - c["Open"])) / rng * 100.0
    # Where the candle closed in its own range: 100 = on the high (good for
    # a long), 0 = on the low.
    loc = (float(c["Close"] - c["Low"]) / rng) * 100.0
    if direction == "short":
        loc = 100.0 - loc

    med_vol = float(agg["Volume"].median() or 0)
    vol_mult = (float(c["Volume"]) / med_vol) if med_vol > 0 else None

    score = 0.35 * min(100.0, body * 1.4) + 0.45 * loc
    if vol_mult is not None:
        score += 0.20 * band(vol_mult, ((0.7, 10), (1.0, 40), (1.5, 70),
                                        (2.5, 95), (99, 100)))
    else:
        score = score / 0.80          # renormalise over what was measured

    # A candle running the wrong way is not "weak", it is contrary.
    going_right = (c["Close"] >= c["Open"]) if direction == "long" else (c["Close"] <= c["Open"])
    if not going_right:
        score *= 0.45

    detail = (f"body {body:.0f}%, close {loc:.0f}% of range"
              + (f", {vol_mult:.1f}x median volume" if vol_mult else ""))
    return {"score": max(0.0, min(100.0, score)), "detail": detail,
            "body_pct": body, "close_location_pct": loc, "volume_mult": vol_mult}


def _pullback_quality(bars: pd.DataFrame, direction: str) -> dict:
    """Is price resting rather than running — the condition that makes an
    entry good rather than merely valid.

    Measured as the last six bars' range and volume against the twenty
    before them. Contraction on lighter volume is the setup §8 wants;
    expansion means the move is happening now and any entry is a chase.
    """
    if bars is None or len(bars) < 26:
        return {"score": None, "detail": "too few bars"}
    recent, base = bars.iloc[-6:], bars.iloc[-26:-6]
    r_rng = float(recent["High"].max() - recent["Low"].min())
    b_rng = float(base["High"].max() - base["Low"].min())
    r_vol = float(recent["Volume"].mean() or 0)
    b_vol = float(base["Volume"].mean() or 0)
    if b_rng <= 0 or b_vol <= 0:
        return {"score": None, "detail": "no baseline"}

    rng_ratio, vol_ratio = r_rng / b_rng, r_vol / b_vol
    score = (band(rng_ratio, ((0.4, 100), (0.7, 80), (1.0, 50), (99, 20)))
             * 0.6
             + band(vol_ratio, ((0.6, 100), (0.9, 75), (1.3, 45), (99, 20)))
             * 0.4)
    return {"score": score,
            "detail": f"range {rng_ratio:.2f}x, volume {vol_ratio:.2f}x prior 20 bars"}


def compute(sess: dict, plan_res: dict, room_res: dict, vol: dict,
            vc: dict, trad: dict, rs: dict, regime_label: str,
            direction: str) -> dict:
    """Entry Score 0-100 and Chase Score 0-6."""
    price = f(sess.get("price"))
    vwap_ = f(sess.get("vwap"))
    atr5 = f(plan_res.get("atr_5min"))
    trigger = f(plan_res.get("trigger_level"))
    bars = sess.get("bars")

    # ── extension measurements ──────────────────────────────────────────
    vwap_dist_pct = vwap_dist_atr = None
    if price is not None and vwap_ and vwap_ > 0:
        vwap_dist_pct = (price - vwap_) / vwap_ * 100.0
        if direction == "short":
            vwap_dist_pct = -vwap_dist_pct
        if atr5 and atr5 > 0:
            vwap_dist_atr = abs(price - vwap_) / atr5

    trig_dist_pct = trig_dist_atr = None
    if price is not None and trigger and trigger > 0:
        # Signed so that "past the trigger" is positive for both directions.
        raw = (price - trigger) if direction == "long" else (trigger - price)
        trig_dist_pct = raw / trigger * 100.0
        if atr5 and atr5 > 0:
            trig_dist_atr = raw / atr5

    # Only extension BEYOND the trigger counts against the entry. Sitting
    # below a breakout level is not lateness, it is the ideal position —
    # you have not bought yet and the trigger has not fired.
    beyond = max(0.0, trig_dist_atr) if trig_dist_atr is not None else None

    consumed_pct = None
    em = f(vol.get("expected_move_pct"))
    day_rng = f(vol.get("day_range_pct"))
    if em and day_rng and (em + day_rng) > 0:
        consumed_pct = day_rng / (day_rng + em) * 100.0

    candle = candle_quality(bars, direction)
    pullback = _pullback_quality(bars, direction)
    spread = f(trad.get("spread_pct"))
    room_ratio = f(room_res.get("room_ratio"))
    rr = f(plan_res.get("rr_blended"))

    market_ok = None
    if regime_label:
        favourable = (regime_label == "RISK ON") == (direction == "long")
        market_ok = (100.0 if favourable else
                     (55.0 if regime_label == "MIXED" else 15.0))
        if rs.get("sector_confirms") is False:
            market_ok = min(market_ok, 45.0)

    parts = [
        ("Distance from trigger", WEIGHTS["trigger_distance"],
         band(beyond, TRIGGER_DIST_BANDS) if beyond is not None else None,
         (f"{beyond:.2f} ATR past trigger" if beyond and beyond > 0.01
          else "at or below trigger — not chasing")
         if beyond is not None else "unavailable"),
        ("Distance from VWAP", WEIGHTS["vwap_distance"],
         band(vwap_dist_atr, VWAP_DIST_BANDS) if vwap_dist_atr is not None else None,
         (f"{vwap_dist_atr:.2f} ATR ({vwap_dist_pct:+.1f}%) from VWAP"
          if vwap_dist_atr is not None else "unavailable")),
        ("Room", WEIGHTS["room"],
         band(room_ratio, ROOM_BANDS) if room_ratio is not None else
         (100.0 if room_res.get("nearest") is None else None),
         room_res.get("detail") or "unavailable"),
        ("Risk / reward", WEIGHTS["risk_reward"],
         band(rr, RR_BANDS) if rr is not None else None,
         f"{rr:.2f}:1 blended" if rr is not None else "unavailable"),
        ("Volume confirmation", WEIGHTS["volume"], f(vc.get("score")),
         vc.get("sequence") or "unavailable"),
        ("Candle quality", WEIGHTS["candle"], candle.get("score"),
         candle.get("detail")),
        ("Spread", WEIGHTS["spread"],
         band(spread, SPREAD_BANDS) if spread is not None else None,
         f"{spread:.2f}%" if spread is not None else trad.get("spread_note")),
        ("Market confirmation", WEIGHTS["market"], market_ok,
         f"{regime_label} tape" if regime_label else "unavailable"),
        ("Pullback quality", WEIGHTS["pullback"], pullback.get("score"),
         pullback.get("detail")),
    ]
    result = blend(parts)

    # ── §6 chase score ──────────────────────────────────────────────────
    chase, chase_why = 0, []
    if vwap_dist_atr is not None and vwap_dist_atr > 1.0:
        chase += 1
        chase_why.append(f"{vwap_dist_atr:.1f} ATR from VWAP")
    if beyond is not None and beyond > 1.0:
        chase += 1
        chase_why.append(f"{beyond:.1f} ATR past the trigger")
    if consumed_pct is not None and consumed_pct > 80:
        chase += 1
        chase_why.append(f"{consumed_pct:.0f}% of the expected move already spent")
    if vwap_dist_pct is not None and vwap_dist_pct > 3.0:
        chase += 1
        chase_why.append(f"{vwap_dist_pct:+.1f}% above VWAP")
    big, declining = _extension_candles(bars, direction)
    if big:
        chase += 1
        chase_why.append("3+ consecutive expansion candles")
    if declining:
        chase += 1
        chase_why.append("price extending on declining volume")

    label = next(lbl for bound, lbl in CHASE_LABELS if chase <= bound)
    grade = next(g for bound, g in ENTRY_BANDS
                 if (result.get("score") or 0) < bound)

    result.update({
        "chase_score": chase, "chase_label": label, "chase_reasons": chase_why,
        "chase_blocked": chase >= CHASE_BLOCK,
        "entry_grade": grade,
        "vwap_distance_pct": vwap_dist_pct,
        "vwap_distance_atr": vwap_dist_atr,
        "trigger_distance_pct": trig_dist_pct,
        "trigger_distance_atr": trig_dist_atr,
        "beyond_trigger_atr": beyond,
        "expected_move_consumed_pct": consumed_pct,
        "candle": candle, "pullback": pullback,
        # "The trigger fired and left" — the condition that turns an A+
        # setup into a missed one (§19).
        "extended_past_trigger": bool(beyond is not None and beyond > EXTENDED_TRIGGER_ATR),
    })
    return result


def _extension_candles(bars: pd.DataFrame, direction: str) -> tuple[bool, bool]:
    """(three consecutive expansion candles, extending on falling volume)."""
    agg = _five_min(bars)
    if len(agg) < 8:
        return False, False
    med_rng = float((agg["High"] - agg["Low"]).median() or 0)
    last3 = agg.iloc[-3:]
    big = bool(med_rng > 0 and all(
        (r["High"] - r["Low"]) > med_rng * 1.3
        and ((r["Close"] >= r["Open"]) if direction == "long" else (r["Close"] <= r["Open"]))
        for _, r in last3.iterrows()))

    # Price making progress while volume fades — buyers thinning out.
    moved = ((float(last3["Close"].iloc[-1]) > float(agg["Close"].iloc[-4]))
             if direction == "long" else
             (float(last3["Close"].iloc[-1]) < float(agg["Close"].iloc[-4])))
    v_now = float(last3["Volume"].mean() or 0)
    v_prev = float(agg.iloc[-8:-3]["Volume"].mean() or 0)
    declining = bool(moved and v_prev > 0 and v_now < v_prev * 0.7)
    return big, declining
