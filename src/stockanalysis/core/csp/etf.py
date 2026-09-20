"""
etf.py — cash-secured puts on funds, where there is no company to judge
========================================================================
The rest of this package answers "is this a business worth being assigned
into, and is the price right". Neither question has an answer for a fund.
An ETF has no income statement, no moat, no capital allocation and no
reverse DCF, so `eligibility.classify()` returns CSP REJECTED for every
one of them with "no quality score — the company cannot be assessed" —
which is correct, and is a category error being reported honestly rather
than a gap to fill.

`core/etf_profile.py` reached the same conclusion for the research pages
and drew the same line: funds get their own fields, their own view and
their own refresh, and the equity screens are left alone. This module is
that line extended to options.

What is left once quality and valuation are gone
------------------------------------------------
More than it sounds, and all of it price-derived:

    structure    trend state, pullback stage and support confluence, from
                 core.longterm.technicals — every one of those reads only
                 moving averages and price, so they mean exactly the same
                 thing for SPY as for AAPL
    support      the same strike anchoring the equity path uses, because
                 "where has price refused to go through" is a chart
                 question and charts do not care what they describe
    the contract delta, credit, yield, probability, liquidity — the option
                 side is identical; a put on QQQ prices the same way as a
                 put on any underlying

What is NOT here, deliberately
------------------------------
No score out of 100, and no verdict claiming the fund is worth owning.
The equity path can say "Elite business at a 22% discount" and mean it;
nothing here could support a sentence like that, so nothing here says one.
The verdict this module produces is about the CONTRACT — is it liquid, is
the premium adequate for the collateral, is the structure under it intact
— and the fund facts it shows (AUM, expense ratio, category, top holdings)
are reported as context and never scored.

That is the whole design: the page gains an ETF section that answers a
narrower question completely, instead of an ETF row that answers the
page's usual question badly.

Concentration is context, not a gate
-------------------------------------
Two "AI" funds are routinely 70% the same six companies, and selling puts
on both is one position, not two. That matters and is shown — but it is a
PORTFOLIO fact, not a property of either contract, so it belongs in the
section's summary rather than in a per-row verdict.
"""

from __future__ import annotations

import datetime as _dt

from stockanalysis.core.csp import chain as CH
from stockanalysis.core.csp import greeks as GK
from stockanalysis.core.csp import strike as ST
from stockanalysis.core.longterm import technicals as T
from stockanalysis.core.longterm._common import f, s

# One month out, which is what the section is for. The window either side
# exists because listings are not uniform: some funds carry weeklies and
# some only the third-Friday monthly, and insisting on exactly 30 days
# would silently drop the ones that only offer 31.
TARGET_DTE = 30
MIN_DTE, MAX_DTE = 21, 45

# The band a cash-secured put is normally written in. Wider than the
# equity path's, and fixed rather than earned: `strike.delta_band()`
# narrows the window by QUALITY SCORE, and there is no quality score here.
# A fund does not earn the right to sit closer to the money by being a
# better business, so the band is one honest constant for all of them.
DELTA_LO, DELTA_HI = 0.15, 0.35
# Where inside that band the strike is taken. The same 0.30 the equity
# reference chain anchors to, so a fund and a stock in the two tables are
# showing the same trade rather than two different ones that happen to
# share a column heading.
TARGET_DELTA = 0.30

# Below this the credit does not pay for a month of collateral. A house
# rule, like premium.py's, and the single most opinionated number here.
MIN_ANNUALISED_PCT = 8.0

# A contract nobody is trading is a quote, not a price. Open interest is
# the cheaper half of the liquidity read; `chain.liquidity_score` gates on
# the spread, which is the half that actually costs you at the fill.
MIN_OPEN_INTEREST = 10

VERDICTS = ("SELL", "THIN", "ILLIQUID", "STRUCTURE", "NO_CONTRACT")


def is_etf(entry: dict) -> bool:
    """The scan's own classification, not a second opinion. Same rule
    core/etf_profile.py uses, imported rather than restated so the two
    views can never disagree about what counts as a fund."""
    from stockanalysis.core import etf_profile as EP
    return EP.is_etf_row(entry)


# The scan columns a structure read needs at minimum. Without them the
# technicals still RETURN something — compute_pullback with no moving
# averages reports AT_HIGHS, because nothing is below price when nothing
# is known — and "at highs" printed against a fund the scanner has never
# covered is a claim invented out of an empty dict. Three funds in the
# current library (MDY, QQQM, VOO) have a profile and no scan row, and
# every one of them read AT_HIGHS before this check existed.
_STRUCTURE_INPUTS = ("200MA", "50MA", "Current Price")


def _structure(raw: dict) -> dict | None:
    """Trend, stage and support for a fund, from price alone — or None.

    Every function called here reads moving averages, ATR and price and
    nothing else — no revenue, no margins, no statements. That is what
    makes them meaningful for a fund, and it is worth stating because the
    same package also computes things that are NOT (compute_lquality lives
    two files away and must never be reached from here).
    """
    if not raw or not all(raw.get(k) is not None for k in _STRUCTURE_INPUTS):
        return None
    return {"trend": T.compute_trend(raw),
            "pullback": T.compute_pullback(raw),
            "confluence": T.compute_support_confluence(raw)}


def _as_result(ticker, price, struct) -> dict:
    """The minimal result-shaped dict `strike.support_levels()` reads.

    Built rather than faked: those three keys are genuinely all that
    function touches, so handing it a fund's real technicals is not a
    workaround, it is the same call with a smaller input.
    """
    return {"ticker": ticker, "price": price,
            "pullback": struct["pullback"],
            "confluence": struct["confluence"]}


def _contract(row, spot, yrs, dte, risk_free, iv_atm) -> dict | None:
    """One priced, greeked strike — or None when it cannot be assessed."""
    k = f(row.get("strike"))
    if k is None or k >= spot:
        return None                       # a CSP is written below spot
    credit = CH.limit_price(row)
    if not credit or credit <= 0:
        return None
    iv = f(row.get("iv")) or iv_atm
    if not iv or not yrs:
        return None
    g = GK.put_greeks(spot, k, yrs, risk_free, iv)
    if g.get("delta") is None:
        return None

    collateral = k * 100.0
    premium = credit * 100.0
    period = premium / collateral * 100.0
    liq = CH.liquidity_score(row) or {}
    # Assignment leaves you long at the strike less the credit — the only
    # number here that describes what you would actually own, and the
    # reason a CSP is a limit order you are paid to place.
    basis = k - credit
    return {
        "strike": k,
        "delta": abs(g["delta"]),
        "credit": credit,
        "bid": f(row.get("bid")), "ask": f(row.get("ask")),
        "premium": round(premium, 2),
        "collateral": round(collateral, 2),
        "period_pct": round(period, 3),
        "annualised_pct": round(period * 365.0 / dte, 2) if dte else None,
        "otm_pct": round((1 - k / spot) * 100, 2),
        "iv_pct": round(iv * 100, 1),
        "prob_otm": (round(g["prob_otm"] * 100, 1)
                     if g.get("prob_otm") is not None else None),
        "theta": g.get("theta"),
        "effective_basis": round(basis, 2),
        "basis_discount_pct": round((1 - basis / spot) * 100, 2),
        "open_interest": f(row.get("open_interest")) or 0.0,
        "volume": f(row.get("volume")) or 0.0,
        "spread_pct": f(row.get("spread_pct")),
        "liquidity": liq.get("score"),
        "tradable": bool(liq.get("tradable")),
        "liquidity_note": "; ".join(liq.get("notes") or []),
        "delta_class": ST.classify_delta(abs(g["delta"])),
    }


def _verdict(best, struct, anchor) -> dict:
    """Gates, not weights — the package's rule, applied to what a fund has.

    Ordered so the first failure is the one worth reading. A structurally
    broken fund is not rescued by a fat premium, and an illiquid contract
    is not rescued by a good chart; saying so in that order is the whole
    point of a gate.
    """
    if not best:
        return {"key": "NO_CONTRACT", "action": "— NO CONTRACT",
                "why": "no strike in the delta band with a usable quote"}

    # Unknown is not failure — the package rule, and it has teeth here. A
    # fund with no scan row gets no structure gate rather than a silent
    # pass dressed up as one; the row says the read is missing.
    trend = (struct or {}).get("trend") or {}
    if s(trend.get("state")) == "BROKEN":
        return {"key": "STRUCTURE", "action": "🔴 STRUCTURE",
                "why": (f"trend broken — "
                        f"{s(trend.get('summary')) or 'structure has failed'}. "
                        f"Being assigned into a fund in a downtrend is the one "
                        f"objection that survives having no business to judge.")}

    if not best["tradable"]:
        return {"key": "ILLIQUID", "action": "🟠 ILLIQUID",
                "why": (best["liquidity_note"]
                        or "spread too wide to fill at a defensible price")}
    if best["open_interest"] < MIN_OPEN_INTEREST:
        return {"key": "ILLIQUID", "action": "🟠 ILLIQUID",
                "why": (f"{best['open_interest']:.0f} contracts of open "
                        f"interest — a quote rather than a market")}

    ann = best["annualised_pct"]
    if ann is None or ann < MIN_ANNUALISED_PCT:
        return {"key": "THIN", "action": "🟡 THIN",
                "why": (f"{ann:.1f}%/yr on collateral is below the "
                        f"{MIN_ANNUALISED_PCT:.0f}% floor — the credit does "
                        f"not pay for the cash it ties up"
                        if ann is not None else "no yield to assess")}

    under = ""
    if anchor and best["strike"] <= anchor["price"]:
        under = (f" Strike sits under {anchor['name']} "
                 f"(${anchor['price']:,.2f}), so price has to break support "
                 f"before assignment.")
    return {"key": "SELL", "action": "🟢 SELL",
            "why": (f"{best['annualised_pct']:.1f}%/yr at Δ{best['delta']:.2f}, "
                    f"{best['otm_pct']:.1f}% out of the money.{under}")}


def evaluate(ticker: str, raw: dict, profile: dict | None = None,
             risk_free: float = 0.042, target_dte: int = TARGET_DTE,
             min_dte: int = MIN_DTE, max_dte: int = MAX_DTE,
             today=None, spot=None) -> dict:
    """One fund -> its best cash-secured put about a month out.

    `raw` is the scan row (moving averages, ATR, 52-week range); `profile`
    is core/etf_profile's fund record. Returns a row even when there is no
    contract, because "this fund has no options worth writing" is a finding
    the section should print rather than a name that quietly vanishes.
    """
    today = today or _dt.date.today()
    profile = profile or {}
    price = f(spot) or f(raw.get("Current Price")) or f(profile.get("price"))

    out = {
        "ticker": ticker,
        "name": s(profile.get("name")) or s(raw.get("Company")) or ticker,
        "category": s(profile.get("category")),
        "family": s(profile.get("family")),
        "aum": f(profile.get("aum")),
        "expense_ratio": f(profile.get("expense_ratio")),
        # etf_profile stores the top-ten sample as `holdings`; `top_holdings`
        # is the yfinance attribute it reads FROM, not the key it writes.
        "holdings": (profile.get("holdings") or [])[:10],
        "top10_weight": f(profile.get("top10_weight")),
        "price": price,
        # Same provenance the equity rows carry. A caller-supplied spot is
        # a live quote; anything else is whatever the last refresh wrote,
        # and every strike below was chosen relative to it.
        "spot_source": "live" if f(spot) else "stored",
        "contract": None, "alternatives": [], "levels": [], "anchor": None,
        "structure": None, "expiry": None, "dte": None,
        "final": {"key": "NO_CONTRACT", "action": "— NO CONTRACT",
                  "why": "no price for the fund"},
    }
    if not price:
        return out

    struct = _structure(raw)
    if struct:
        out["structure"] = {
            "measured": True,
            "trend_state": s((struct["trend"] or {}).get("state")),
            "trend_icon": s((struct["trend"] or {}).get("icon")),
            "trend_summary": s((struct["trend"] or {}).get("summary")),
            "stage": s((struct["pullback"] or {}).get("stage")),
            "stage_icon": s((struct["pullback"] or {}).get("stage_icon")),
            "confluence": (struct["confluence"] or {}).get("agreeing"),
            "confluence_of": (struct["confluence"] or {}).get("possible"),
        }
        levels = ST.support_levels(_as_result(ticker, price, struct))
        out["levels"] = levels
        out["anchor"] = ST.anchor_level(levels)
    else:
        out["structure"] = {
            "measured": False,
            "note": ("no scan row — run a scan to read trend, stage and "
                     "support for this fund"),
        }

    try:
        available = CH.expiries(ticker)
    except Exception as e:                              # one dead symbol
        out["final"] = {"key": "NO_CONTRACT", "action": "— NO CONTRACT",
                        "why": f"option chain unavailable ({e})"}
        return out
    if not available:
        out["final"] = {"key": "NO_CONTRACT", "action": "— NO CONTRACT",
                        "why": "no listed options"}
        return out

    picks = CH.pick_expiries(available, min_dte=min_dte, max_dte=max_dte,
                             limit=4, today=today)
    if not picks:
        out["final"] = {"key": "NO_CONTRACT", "action": "— NO CONTRACT",
                        "why": f"nothing listed {min_dte}-{max_dte} days out"}
        return out
    # Nearest to the target, not merely the first monthly. pick_expiries
    # sorts monthlies first, which is the right default for the equity
    # path and the wrong one for a section whose whole premise is "a month
    # out" — a 21-day monthly would beat a 30-day weekly on that ordering.
    expiry = min(picks, key=lambda e: abs((CH.dte(e, today) or 999)
                                          - target_dte))
    dte = CH.dte(expiry, today)
    out["expiry"], out["dte"] = expiry, dte

    try:
        puts = CH.fetch_puts(ticker, expiry)
    except Exception as e:
        out["final"] = {"key": "NO_CONTRACT", "action": "— NO CONTRACT",
                        "why": f"chain fetch failed ({e})"}
        return out
    if not puts:
        out["final"] = {"key": "NO_CONTRACT", "action": "— NO CONTRACT",
                        "why": f"{expiry} chain came back empty"}
        return out

    iv_atm = CH.atm_iv(puts, price)
    yrs = CH.year_fraction(expiry, today)
    priced = [c for c in (_contract(r, price, yrs, dte, risk_free, iv_atm)
                          for r in puts) if c]
    band = [c for c in priced if DELTA_LO <= c["delta"] <= DELTA_HI]
    # Widened once, and only when the band is empty — the same concession
    # pick_expiries makes for DTE, for the same reason: a fund whose only
    # listed strikes straddle the band still has a tradable put, and
    # returning nothing would read as "no options".
    if not band:
        band = [c for c in priced if 0.05 <= c["delta"] <= 0.45]
    band.sort(key=lambda c: -(c["annualised_pct"] or 0))

    tradable = [c for c in band if c["tradable"]]
    # Chosen by DELTA, not by yield, and only among contracts that can be
    # filled. Ranking on yield always lands on the closest-to-the-money
    # strike — that is where the premium is — so every fund's headline came
    # from a different moneyness and none of them were comparable. One
    # delta makes the column a like-for-like reading across funds; a
    # headline yield off a one-sided quote remains the single most
    # misleading number this section could print, hence `tradable` first.
    from stockanalysis.core.csp.engine import _pick_by_delta
    pool = tradable or band
    best = _pick_by_delta(pool, target=TARGET_DELTA,
                          band=(DELTA_LO, DELTA_HI),
                          outer=(0.05, 0.45)) or (pool[0] if pool else None)
    out["contract"] = best
    out["alternatives"] = [c for c in band if best is None
                           or c["strike"] != best["strike"]][:4]
    out["final"] = _verdict(best, struct, out["anchor"])
    return out


def evaluate_universe(funds, risk_free: float = 0.042,
                      target_dte: int = TARGET_DTE, today=None,
                      progress=None, spots=None) -> list[dict]:
    """`funds` is [(ticker, raw_row, profile)] -> rows, best yield first.

    Every fund costs one chain round trip, so this is a job's work rather
    than a request's — the same reason the equity scan is a job.

    `spots` overrides the stored price per ticker. Both a fund's scan row
    and its stored profile carry whatever price the last refresh wrote, and
    every strike below is chosen relative to spot — so the caller passes
    live quotes for the same reason engine.evaluate_universe() fetches
    them. Absent, the stored price is used and the row says so.
    """
    spots = spots or {}
    out = []
    for i, (ticker, raw, profile) in enumerate(funds or (), 1):
        if progress:
            progress(i, ticker)
        try:
            out.append(evaluate(ticker, raw or {}, profile, risk_free,
                                target_dte, today=today,
                                spot=spots.get(str(ticker).upper())))
        except Exception as e:                  # one bad chain, not a run
            print(f"[CSP/ETF] {ticker}: evaluation failed ({e})")
    rank = {k: i for i, k in enumerate(VERDICTS)}
    out.sort(key=lambda r: (rank.get((r.get("final") or {}).get("key"), 9),
                            -((r.get("contract") or {}).get("annualised_pct")
                              or 0)))
    return out


def overlap(rows, top: int = 10) -> list[dict]:
    """Holdings shared across the funds with a live contract.

    Selling puts on SMH and IGV is not two positions if both are a third
    the same three names. This is the section's one portfolio-level
    reading, and it is deliberately outside the per-row verdict: it is a
    fact about the SET, and putting it in a row would make each row look
    individually worse for a risk neither one carries alone.
    """
    live = [r for r in rows
            if (r.get("final") or {}).get("key") in ("SELL", "THIN")]
    seen: dict[str, dict] = {}
    for r in live:
        for h in r.get("holdings") or []:
            name = s(h.get("ticker")) or s(h.get("name"))
            if not name:
                continue
            rec = seen.setdefault(name, {"holding": name, "funds": [],
                                         "total_weight": 0.0})
            weight = f(h.get("weight")) or 0.0
            rec["funds"].append({"ticker": r["ticker"], "weight": weight})
            rec["total_weight"] += weight
    shared = [v for v in seen.values() if len(v["funds"]) > 1]
    shared.sort(key=lambda v: (-len(v["funds"]), -v["total_weight"]))
    return shared[:top]
