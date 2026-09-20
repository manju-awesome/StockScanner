"""
Risk as a measured quantity, not a filter.

The engine used to answer DTE and earnings with booleans: inside the
window or not, spans earnings or not. Both threw away the information
that mattered. A 36-day expiry and a 59-day expiry both "passed" a
21-60 window while being very different trades, and NTAP was rejected
outright for earnings 21 days out — which is a real risk, and also a
reason to look at a later expiry rather than to stop looking.

So four things are measured here instead of gated:

    dte_fit            distance from the target expiry, scored
    earnings_distance  six bands from Clean to Inside the contract
    move_cushion       strike distance measured in expected moves
    assignment_score   would I actually want this stock at this basis

and combined into a RISK score, which becomes the third factor beside
stock and option. A superb company on a well-paid contract can still be
a bad trade if the strike sits inside the expected move with earnings
three days before expiry, and until now nothing in the engine could say
so.

`requirements()` is the other half. Every state that is not SELL now
carries the number that would change it — "premium >= $2.11, or IV/RV
>= 1.10" rather than "wait for IV expansion". A threshold you cannot
see is not a decision you can act on.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f

# ── DTE preference bands ────────────────────────────────────────────────
# The window is the universe; the target is where the trade wants to be.
DEFAULT_MIN_DTE = 21
DEFAULT_TARGET_DTE = 35
DEFAULT_PREF_LOW = 30
DEFAULT_PREF_HIGH = 45
DEFAULT_MAX_DTE = 60
DEFAULT_TOLERANCE = 7

# ── Earnings ────────────────────────────────────────────────────────────
EARNINGS_POLICIES = ("AVOID", "CONTROLLED", "ACCEPT")
DEFAULT_EARNINGS_BUFFER = 7        # days clear of expiry to read as Clean

# What ACCEPT demands before it will let earnings inside the contract.
ACCEPT_MIN_QUALITY = 90
ACCEPT_MAX_DELTA = 0.20
ACCEPT_MIN_LIQUIDITY = 78
ACCEPT_MIN_ADEQUACY = 1.3

# CONTROLLED penalties, applied to the option and composite scores.
CONTROLLED_OPTION_PENALTY = 25
CONTROLLED_CSP_PENALTY = 15


def dte_fit(dte, target=DEFAULT_TARGET_DTE, pref=(DEFAULT_PREF_LOW,
                                                  DEFAULT_PREF_HIGH),
            hard_max=DEFAULT_MAX_DTE, tolerance=DEFAULT_TOLERANCE) -> dict:
    """How well this expiry matches the preferred holding period, 0-100.

    Inside the preferred band scores full marks; outside it the score
    falls with distance from the target rather than dropping off a cliff,
    because 46 days is not meaningfully worse than 45 and a hard edge
    there would make the ranking jump for no reason.
    """
    d = f(dte)
    if d is None:
        return {"score": None, "band": None, "detail": "no DTE"}

    lo, hi = pref
    if lo <= d <= hi:
        band, score = "🟢 Preferred", 100.0
    elif abs(d - target) <= tolerance + max(0, lo - target, target - hi):
        band = "🟡 Acceptable"
        # Linear decay over the tolerance window outside the band.
        edge = lo if d < lo else hi
        score = max(60.0, 100.0 - abs(d - edge) / max(tolerance, 1) * 40.0)
    else:
        band = "🟠 Outside preference"
        edge = lo if d < lo else hi
        score = max(0.0, 60.0 - abs(d - edge) / max(tolerance, 1) * 30.0)

    if hard_max and d > hard_max:
        band, score = "🔴 Beyond max", 0.0

    return {
        "score": round(score),
        "band": band,
        "dte": d,
        "target": target,
        "preferred": [lo, hi],
        "detail": (f"{d:.0f}d against a {lo}-{hi} preference "
                   f"(target {target}d)"),
    }


# Six bands, from clear of the contract to inside it.
def earnings_distance(days_to_earnings, dte,
                      buffer_days=DEFAULT_EARNINGS_BUFFER) -> dict:
    """Where earnings sit relative to this expiry, classified.

    `ratio` is days-to-earnings over DTE — below 1.0 the print lands
    inside the contract. Reported alongside the band because it is the
    scale-free version: earnings at 60% of the way to expiry is the same
    exposure whether the contract is 20 days or 50.
    """
    e, d = f(days_to_earnings), f(dte)
    if e is None:
        return {"band": "⚪ Unknown", "key": "UNKNOWN", "score": None,
                "inside": None, "ratio": None,
                "detail": "no earnings date on file — treat as unknown, "
                          "not as clear"}
    if d is None:
        return {"band": "⚪ Unknown", "key": "UNKNOWN", "score": None,
                "inside": None, "ratio": None, "detail": "no DTE"}

    ratio = e / d if d else None
    inside = e <= d

    if e > d + buffer_days:
        key, band, score = "CLEAN", "🟢 Clean", 100.0
        detail = f"earnings {e:.0f}d out, {e - d:.0f}d clear of expiry"
    elif e > d:
        key, band, score = "LOW", "🟢 Low risk", 85.0
        detail = (f"earnings {e:.0f}d out, only {e - d:.0f}d after expiry "
                  f"— little room if the expiry rolls")
    elif e > 14:
        key, band, score = "EXPOSED", "🟡 Earnings exposure", 55.0
        detail = f"earnings {e:.0f}d out, inside the {d:.0f}d contract"
    elif e > 7:
        key, band, score = "HIGH", "🟠 High event risk", 30.0
        detail = f"earnings {e:.0f}d out — inside, and close"
    else:
        key, band, score = "IMMINENT", "🔴 Event risk", 5.0
        detail = f"earnings {e:.0f}d out — imminent, inside the contract"

    return {"band": band, "key": key, "score": score, "inside": inside,
            "ratio": None if ratio is None else round(ratio, 2),
            "days": e, "detail": detail}


def earnings_gate(dist, policy, quality=None, delta=None, liquidity=None,
                  adequacy=None, settle: bool = False) -> dict:
    """Apply the three-state earnings policy.

    AVOID       skip the expiry entirely (the old boolean behaviour)
    CONTROLLED  allow it, penalise it, never silently
    ACCEPT      allow it only when the rest of the trade is strong
                enough to be paid for the gap risk

    Returns {allow, penalty_option, penalty_csp, why}. ACCEPT is
    deliberately the strictest-looking of the three: accepting event risk
    is a choice to be paid for it, so it demands elite quality, a
    conservative delta, excellent liquidity and a premium well above the
    hurdle. Anything less falls back to CONTROLLED's penalty rather than
    to a free pass.

    CALLED TWICE, and the distinction is load-bearing
    -------------------------------------------------
    ACCEPT's four conditions are all properties of a CONTRACT — delta,
    liquidity, and how far the premium clears its hurdle — and none of
    them exists until a chain has been fetched and a strike chosen. The
    expiry filter runs before any of that, so it must not evaluate them:
    passing four Nones made every condition fail, which meant ACCEPT could
    never once be met and silently behaved as CONTROLLED while printing
    "delta 1.00 > 0.2" about a contract that had not been selected.

    So the first call asks only "may this expiry be considered at all" and
    the second — once the strike is priced — decides the penalty.

    Which one you get is inferred from whether you supplied any contract
    data, rather than from a flag you have to remember. That is deliberate:
    the original bug was a caller with nothing to judge being handed the
    judging path, and a rule of "no inputs, no verdict" makes that
    unrepresentable. `settle=True` forces the second pass for a caller that
    genuinely has all four and wants them evaluated even if some are None.
    """
    settle = settle or any(v is not None for v in
                           (quality, delta, liquidity, adequacy))
    pol = (policy or "AVOID").upper()
    if not dist.get("inside"):
        return {"allow": True, "penalty_option": 0, "penalty_csp": 0,
                "why": dist.get("detail") or "earnings clear of expiry"}

    if pol == "AVOID":
        return {"allow": False, "penalty_option": 0, "penalty_csp": 0,
                "why": (f"{dist['detail']} — earnings policy is AVOID")}

    if pol == "ACCEPT" and not settle:
        # Admission only. The verdict on whether the trade earns its free
        # pass is deferred to the settle pass, which has the contract.
        return {"allow": True, "pending": True,
                "penalty_option": 0, "penalty_csp": 0,
                "why": (f"{dist['detail']} — earnings policy is ACCEPT; "
                        f"whether it is taken without penalty depends on "
                        f"the contract")}

    if pol == "ACCEPT":
        q, dl = f(quality), abs(f(delta) or 1.0)
        lq, adq = f(liquidity), f(adequacy)
        fails = []
        if q is None or q < ACCEPT_MIN_QUALITY:
            fails.append(f"quality {q if q is not None else '—'} < "
                         f"{ACCEPT_MIN_QUALITY}")
        if dl > ACCEPT_MAX_DELTA:
            fails.append(f"delta {dl:.2f} > {ACCEPT_MAX_DELTA}")
        if lq is None or lq < ACCEPT_MIN_LIQUIDITY:
            fails.append(f"liquidity {lq if lq is not None else '—'} < "
                         f"{ACCEPT_MIN_LIQUIDITY}")
        if adq is None or adq < ACCEPT_MIN_ADEQUACY:
            fails.append(f"premium {adq if adq is not None else '—'}× < "
                         f"{ACCEPT_MIN_ADEQUACY}×")
        if not fails:
            return {"allow": True, "penalty_option": 0, "penalty_csp": 0,
                    "why": (f"{dist['detail']} — accepted: elite quality, "
                            f"conservative delta and a premium that pays "
                            f"for the gap risk")}
        # Not strong enough to take the risk for free — fall back rather
        # than reject, so the trade is still visible with its penalty.
        return {"allow": True,
                "penalty_option": CONTROLLED_OPTION_PENALTY,
                "penalty_csp": CONTROLLED_CSP_PENALTY,
                "why": (f"{dist['detail']} — ACCEPT not met ("
                        + "; ".join(fails) + "), penalised as CONTROLLED")}

    return {"allow": True,
            "penalty_option": CONTROLLED_OPTION_PENALTY,
            "penalty_csp": CONTROLLED_CSP_PENALTY,
            "why": f"{dist['detail']} — allowed with penalty (CONTROLLED)"}


def move_cushion(spot, strike, expected_move) -> dict:
    """Strike distance measured in expected moves.

    Delta already encodes this, but opaquely. "11.2% out, and the market
    expects 4.2%" is a sentence anyone can check; "0.19 delta" is a model
    output. The ratio is the number that matters — below 1.0 the strike
    sits INSIDE what the market expects the stock to do by expiry, which
    is a very different trade from one comfortably outside it.
    """
    s, k, em = f(spot), f(strike), f(expected_move)
    if not s or k is None:
        return {"ratio": None, "score": None, "detail": "no strike"}

    distance = s - k
    distance_pct = distance / s * 100
    if not em or em <= 0:
        return {"ratio": None, "score": None,
                "distance_pct": round(distance_pct, 1),
                "detail": f"{distance_pct:.1f}% out; no expected move"}

    ratio = distance / em
    cushion = distance - em          # dollars beyond the expected move

    if ratio >= 2.0:
        band = "🟢 Well outside"
    elif ratio >= 1.5:
        band = "🟢 Outside"
    elif ratio >= 1.0:
        band = "🟡 At the edge"
    elif ratio >= 0.7:
        band = "🟠 Inside the expected move"
    else:
        band = "🔴 Deep inside the expected move"

    # 1.0 is the break-even and scores 50; 2.5x is full marks.
    score = max(0.0, min(100.0, (ratio - 0.5) / 2.0 * 100.0))
    return {
        "ratio": round(ratio, 2),
        "score": round(score),
        "band": band,
        "distance_pct": round(distance_pct, 1),
        "expected_move": round(em, 2),
        "expected_move_pct": round(em / s * 100, 1),
        "cushion": round(cushion, 2),
        "detail": (f"{distance_pct:.1f}% out against a "
                   f"{em / s * 100:.1f}% expected move — {ratio:.2f}×"),
    }


def assignment_score(elig, discount, assign, levels, basis, spot,
                     dist, cushion) -> dict:
    """"If assigned, would I actually want this?" as 0-100.

    The engine already answered this as a yes/no gate. The score is the
    same question with the strength of the answer preserved: two trades
    that both pass the gate can be a long way apart, and a ranking that
    cannot tell them apart will pick the weaker one whenever it pays
    slightly better.
    """
    parts, notes = {}, []

    q = f(elig.get("quality_score"))
    if q is not None:
        parts["quality"] = (q, 30)

    m = f(discount.get("margin_pct"))
    if m is not None:
        # Growth-gap points and price-percent are different units; both
        # are mapped so that "meaningfully cheap" lands near 80.
        span = 20.0 if discount.get("basis") == "growth" else 35.0
        parts["valuation"] = (max(0.0, min(100.0, (m + 5.0) / span * 100.0)),
                              25)

    # Basis against the nearest level that has actually held.
    b = f(basis)
    if b and levels:
        below = [lv for lv in levels if (f(lv.get("price")) or 0) < b]
        if below:
            nearest = max(below, key=lambda lv: lv["price"])
            room = (b - nearest["price"]) / b * 100
            parts["support"] = (max(0.0, min(100.0, room / 12.0 * 100.0)), 20)
            notes.append(f"{room:.1f}% above {nearest['name']}")
        else:
            parts["support"] = (25.0, 20)
            notes.append("no level below the basis")

    if cushion.get("score") is not None:
        parts["cushion"] = (cushion["score"], 15)
    if dist.get("score") is not None:
        parts["earnings"] = (dist["score"], 10)

    if not parts:
        return {"score": None, "detail": "not enough data to score"}

    wsum = sum(w for _, w in parts.values())
    score = sum(v * w for v, w in parts.values()) / wsum

    # The gate still governs. A basis the engine would not own cannot
    # score as if it would, whatever the components say.
    if assign.get("happy_to_own") is False:
        score = min(score, 40.0)
        notes.append("assignment tests fail")
    elif assign.get("happy_to_own") is None:
        score = min(score, 55.0)
        notes.append("assignment cannot be judged")

    return {
        "score": round(score),
        "coverage": round(wsum / 100 * 100),
        "components": {k: round(v) for k, (v, _) in parts.items()},
        "detail": "; ".join(notes) or "assignment quality scored",
    }


def risk_score(dte_info, dist, cushion, assign_score, liquidity) -> dict:
    """The third factor beside stock and option.

    Everything here is about what happens if the trade goes wrong: how
    long you are exposed, what lands inside that window, how much room
    the strike has, and whether you would want the shares. None of it is
    about how good the company is or how well the option pays — those
    are the other two scores, and mixing them is what let a superb
    company hide a dangerous option structure.
    """
    parts = {
        "earnings":   (dist.get("score"), 30),
        "cushion":    (cushion.get("score"), 25),
        "assignment": (assign_score.get("score"), 25),
        "dte":        (dte_info.get("score"), 10),
        "liquidity":  (liquidity, 10),
    }
    got = {k: (v, w) for k, (v, w) in parts.items() if v is not None}
    if not got:
        return {"score": None, "detail": "no risk inputs available"}

    wsum = sum(w for _, w in got.values())
    score = sum(v * w for v, w in got.values()) / wsum

    weakest = min(got.items(), key=lambda kv: kv[1][0])
    return {
        "score": round(score),
        "coverage": round(wsum / sum(w for _, w in parts.values()) * 100),
        "components": [{"name": k, "weight": parts[k][1],
                        "score": None if parts[k][0] is None
                                 else round(parts[k][0]),
                        "available": parts[k][0] is not None}
                       for k in parts],
        "weakest": weakest[0],
        "detail": (f"weakest leg is {weakest[0]} at "
                   f"{weakest[1][0]:.0f}/100"),
    }


def combine(stock, option, risk):
    """CSP score = stock x option x risk, all on 0-100.

    Multiplicative for the same reason stock x option already was: a
    product cannot be rescued by one strong factor, and risk belongs on
    that footing rather than as a tiebreak. Risk enters damped — a 70
    risk score scales the result to 0.85 rather than 0.70 — because
    unlike the other two it is a modifier on a trade that has already
    passed both, and squaring the penalty would reject everything.
    """
    if stock is None or option is None:
        return None
    base = stock / 100.0 * option
    if risk is None:
        return round(base)
    return round(base * (0.7 + 0.3 * risk / 100.0))
