"""
The Cash-Secured Put engine — orchestration.

Order of operations is the design, not an implementation detail:

    1. Eligibility on the COMPANY, with no option data in scope at all.
    2. Only for names that survive, fetch chains (the slow, networked step).
    3. Price, greek and rank the strikes.
    4. Assignment test.
    5. Score and rank.

Fetching chains only for survivors is partly a speed decision — a chain
is one network round trip per expiry per ticker — but mainly a
correctness one: a rejected company has no strike worth computing, and
computing one invites it to be looked at.

Earnings handling deserves its own note. An expiry that spans earnings is
rejected by default rather than penalised. Selling a put through an
earnings print on a position you intend to be assigned into is the one
combination where the tail actually bites: the premium is richest exactly
because the gap risk is real, and a gap through your strike hands you
stock at a price the market no longer believes in.
"""

from __future__ import annotations

import datetime as _dt

from stockanalysis.core.longterm._common import f, s

from . import chain as C
from . import eligibility as EL
from . import greeks as G
from . import premium as PR
from . import requirements as RQ
from . import risk as RK
from . import score as SC
from . import strike as ST
from . import volatility as V

DEFAULT_MIN_DTE = 20
DEFAULT_MAX_DTE = 45

# Default number of rejected names priced as REFERENCE on one bulk scan.
# Each costs an option-chain round trip and rejections are ~90% of a run,
# so this is a time budget, not a judgment — and the caller can raise it
# (`reference_budget`) when it wants coverage over speed.
#
# Spread ACROSS rejection buckets rather than down the quality ranking; see
# screen.spread_reference_budget() for why the first version, which went
# purely by quality, left the largest category on the page blank.
MAX_REFERENCE_CHAINS = 60
# Fallback when the long-term engine cannot resolve a rate; the greeks
# are barely sensitive to r over 20-45 days, but a wrong sign would show.
FALLBACK_RISK_FREE = 0.04


# ─────────────────────────────────────────────────────────────────────────
# LIVE SPOT — the one input that must not come from the last scan
# ─────────────────────────────────────────────────────────────────────────
# The company verdict this engine consumes is stale-tolerant by design: a
# quality score from this morning is still true this afternoon, which is
# why /csp reads /longterm's result rather than recomputing it. The PRICE
# is the exception, and it is not a cosmetic one.
#
# Everything the options layer computes is a function of spot: the strike
# filter (`strike >= spot` is what makes a put out-of-the-money), delta and
# every other greek, expected move, move cushion, probability of profit,
# the OTM distance shown in the table, and the effective-basis discount.
# Measured against live quotes on 2026-08-18 the research library was
# 5.4% out on PODD, 2.6% on CRDO and 1.5% on CRM — a chain fetched seconds
# ago being priced against a spot from a scan days ago. A 5% error in spot
# does not shade a delta, it picks a different strike.
#
# This is the same asymmetry entry_alerts.py already relies on and states
# in its own docstring: stored levels, live price. The scan is already
# minutes of option chains, so one batched quote call is free by
# comparison.

# Quotes are fetched in one call for the whole scan set. Chunked because a
# single yfinance request with several hundred symbols is where it starts
# returning partial frames without saying so.
QUOTE_CHUNK = 120


def live_spots(tickers) -> dict:
    """{ticker: last price} for as many as could be quoted.

    Never raises and never guesses: a symbol that could not be quoted is
    simply absent, and the caller keeps the stored price for it rather
    than being handed a stale number dressed as a fresh one.
    """
    names = sorted({str(t).upper() for t in (tickers or ()) if t})
    if not names:
        return {}
    out: dict[str, float] = {}
    try:
        import yfinance as yf
    except ImportError:
        return out

    for i in range(0, len(names), QUOTE_CHUNK):
        chunk = names[i:i + QUOTE_CHUNK]
        try:
            raw = yf.download(chunk, period="2d", interval="1d",
                              auto_adjust=False, progress=False,
                              group_by="ticker", threads=True)
        except Exception as e:
            print(f"[CSP] live quotes unavailable for {len(chunk)} "
                  f"symbols ({e})")
            continue
        for t in chunk:
            # Both column shapes are TRIED rather than predicted from the
            # request. With group_by="ticker" yfinance returns a MultiIndex
            # even for a single symbol, so keying off len(chunk) meant every
            # one-ticker call — which is exactly what a per-row rescan and a
            # small named scan are — silently found nothing and fell back to
            # the stored price. ABBV was 3.2% out and reported as "stored".
            px = None
            for frame in (raw.get(t) if hasattr(raw, "get") else None, raw):
                if frame is None:
                    continue
                try:
                    px = float(frame["Close"].dropna().iloc[-1])
                    break
                except Exception:
                    continue
            if px and px > 0:
                out[t] = round(px, 2)
    return out


# Where a reference strike is taken. 0.30 is the conventional
# cash-secured-put delta — far enough out that assignment is the exception,
# close enough that the credit is worth the collateral — and the band is
# what "near 0.30" means in a real chain, where the listed strikes rarely
# land on it exactly. A 0.27 and a 0.32 are the same trade; a 0.45 is not.
REFERENCE_TARGET_DELTA = 0.30
REFERENCE_DELTA_BAND = (0.25, 0.35)
# Widened once when nothing is listed inside the band — a chain with $50
# strike spacing can step straight past it. Beyond this the contract is no
# longer the trade the column claims to be showing, so it reports nothing.
REFERENCE_DELTA_OUTER = (0.15, 0.45)


def _pick_by_delta(candidates, target: float = REFERENCE_TARGET_DELTA,
                   band=REFERENCE_DELTA_BAND, outer=REFERENCE_DELTA_OUTER):
    """The contract closest to `target` delta, preferring the tight band.

    Not the richest: see the note at the call site. Ties break toward the
    LOWER delta — further from assignment — because between two contracts
    equidistant from 0.30, the safer one is the honest representative.
    """
    def d(c):
        v = c.get("delta")
        return None if v is None else abs(v)

    usable = [c for c in (candidates or ()) if d(c) is not None]
    if not usable:
        return None
    for lo, hi in (band, outer):
        inside = [c for c in usable if lo <= d(c) <= hi]
        if inside:
            return min(inside, key=lambda c: (abs(d(c) - target), d(c)))
    return None


def _buy_zone_view(result: dict) -> dict:
    """The long-term engine's buy zone, flattened for this page.

    Read from `buy_zones.display_zone` — the same zone /longterm's Buy Zone
    column renders — so the two pages cannot quote different bands for one
    name. `state` and `near` come from the same proximity reading that
    page's alert bar uses, for the same reason.

    Returns a dict of Nones rather than None when there is no zone, so the
    caller never has to guard before reading a key.
    """
    blank = {"low": None, "high": None, "label": None, "kind": None,
             "distance_pct": None, "state": None, "near": None,
             "qualifies": None, "above_spot": None}
    zone = ((result.get("buy_zones") or {}).get("display_zone")) or {}
    if not zone.get("low"):
        return blank
    try:
        from stockanalysis.core.longterm import buy_zones as BZ
        prox = BZ.zone_proximity(result) or {}
    except Exception:                      # never fail a scan on context
        prox = {}
    return {
        "low": f(zone.get("low")), "high": f(zone.get("high")),
        "label": s(zone.get("label")), "kind": s(zone.get("kind")),
        "distance_pct": f(zone.get("distance_pct")),
        "state": s(prox.get("state")),
        "near": prox.get("near"),
        "qualifies": zone.get("kind") == "investment",
        # Price has already fallen through the band — it is overhead
        # supply, not a level to be assigned at.
        "above_spot": bool(zone.get("above_spot")),
    }


def _closes(ticker: str, days: int = 200) -> list[float]:
    """Daily closes for realised-vol. Never tz-converts: daily bars carry
    a date, and converting shifts them back a day, which silently
    corrupts every window that starts from 'the previous close'."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period=f"{days}d", interval="1d",
                                      auto_adjust=False)
        if h is None or h.empty:
            return []
        return [float(x) for x in h["Close"].tolist() if x == x]
    except Exception as e:
        print(f"[CSP] {ticker}: price history unavailable ({e})")
        return []


def _earnings_conflict(result, expiry, today=None) -> str | None:
    """Whether earnings land on or before `expiry`. None means clear."""
    days = f(result.get("days_to_earnings"))
    if days is None:
        return None                       # unknown, reported separately
    d = C.dte(expiry, today)
    if d is None:
        return None
    if days <= d:
        return (f"earnings in {days:.0f} day(s), before the {expiry} "
                f"expiry ({d}d)")
    return None


def _reference_chain(ticker, spot, r, min_dte, max_dte, today,
                     atr=None, result=None) -> dict:
    """Every put on the nearest qualifying expiry, priced and greeked.

    Shown for a rejected company when it was asked about BY NAME. It
    carries no verdict and no score: the point is to answer "what is
    actually quoted here", not to relitigate the rejection. The best
    premium is reported as the richest FILLABLE strike, because the
    richest quote on the board is usually a 0.01-bid contract nobody
    will trade.
    """
    from .store import market_open
    quotes_live = market_open()

    expiries = C.pick_expiries(C.expiries(ticker), min_dte, max_dte,
                               limit=1, today=today)
    if not expiries:
        return {"available": False,
                "why": f"no expiry in the {min_dte}-{max_dte} DTE window"}

    expiry = expiries[0]
    rows = C.fetch_puts(ticker, expiry)
    if not rows:
        return {"available": False, "why": "chain unavailable"}

    t = C.year_fraction(expiry, today)
    d = C.dte(expiry, today)
    if not t or not d:
        return {"available": False, "why": "expiry already passed"}

    atm = C.atm_iv(rows, spot)
    strikes = []
    for row in rows:
        if row["strike"] >= spot:          # cash-secured puts are OTM
            continue
        iv = row["iv"]
        if iv is None and row.get("mid"):
            iv = G.implied_vol(row["mid"], spot, row["strike"], t, r)
        if iv is None:
            continue
        gk = G.put_greeks(spot, row["strike"], t, r, iv)
        if gk["delta"] is None:
            continue
        limit = C.limit_price(row)
        ret = SC.returns(row["strike"], limit, d)
        liq = C.liquidity_score(row, quotes_live)
        be = ret.get("breakeven")
        strikes.append({
            **row, **gk, "iv": iv, "expiry": expiry, "dte": d,
            "limit_price": limit,
            "liquidity": liq["score"], "liquidity_notes": liq["notes"],
            "liquidity_tradable": liq["tradable"],
            "spread_verdict": liq["spread_verdict"],
            "delta_class": ST.classify_delta(gk["delta"]),
            "breakeven": be,
            "basis_vs_spot_pct": (round((be - spot) / spot * 100, 1)
                                  if be and spot else None),
            "distance_pct": round((row["strike"] - spot) / spot * 100, 1),
            **{k: v for k, v in ret.items() if k != "breakeven"},
        })

    strikes.sort(key=lambda c: -c["strike"])

    # The reference strike is chosen by DELTA, not by yield.
    #
    # Ranking on annualised return always picks the closest-to-the-money
    # contract, because that is where the premium is. Measured over the
    # live library it did exactly that: median delta 0.42, 104 of 112 rows
    # above 0.35, many barely 1% out of the money. Those are near-coin-flip
    # assignments, and worse, no two rows were comparable — each headline
    # yield came from a different moneyness, so "121% annualised" and "30%
    # annualised" were answering different questions.
    #
    # Anchoring to one delta makes the column mean something: every row is
    # the same trade, and the yield difference is then a fact about the
    # name rather than about which strike happened to pay most.
    fillable = [c for c in strikes
                if c.get("liquidity_tradable") is not False and c.get("bid")]
    best = _pick_by_delta(fillable)

    # The hurdle, computed for the reference strike too. Every input
    # `premium.required()` needs — delta, DTE, risk-free, quality and the
    # valuation margin — exists on a rejected row; none of them comes from
    # the option scoring path a rejection never reaches. Without this,
    # "rich premium" meant nothing on exactly the rows whose premiums are
    # richest, because adequacy was the only honest measure of rich and it
    # was absent.
    #
    # It is still NOT a verdict. It says "this pays 1.8x what a contract at
    # this delta on a business this good should pay" and stops there; the
    # company gate has already said no for reasons a yield cannot answer.
    if best is not None and result is not None:
        try:
            q = (result.get("quality") or {}).get("score")
            disc = EL.discount_view(result) or {}
            req = PR.required(best.get("delta"), best.get("dte"), r, q,
                              disc.get("margin_pct"), None, "SELECTIVE")
            adq = PR.adequacy(best.get("yield_pct"), req)
            best["required_pct"] = req.get("period_pct")
            best["adequacy"] = adq.get("ratio")
            best["adequacy_label"] = adq.get("label")
        except Exception as e:                # context, never a scan
            print(f"[CSP] {ticker}: reference hurdle unavailable ({e})")

    hv = V.realised_vol(_closes(ticker))
    return {
        "available": True,
        "expiry": expiry, "dte": d, "atm_iv": atm, "hv": hv,
        "iv_vs_hv": V.iv_vs_hv(atm, hv),
        "strikes": strikes,
        "best": best,
        "fillable": len(fillable),
        "quotes_live": quotes_live,
        "why": ("shown because you asked for this ticker by name — the "
                "company failed the eligibility gate, so none of this is "
                "a recommendation"),
    }


def evaluate(result: dict, regime: str = "SELECTIVE", risk_free=None,
             min_dte=DEFAULT_MIN_DTE, max_dte=DEFAULT_MAX_DTE,
             allow_earnings=False, today=None, raw=None,
             quotes_live=None, reference_chain=False,
             earnings_policy=None, dte_prefs=None) -> dict:
    """One long-term engine result -> one full CSP verdict.

    `raw` is the scan row the result was built from. It carries ATR,
    which the engine result does not, and ATR is what makes premium
    comparable across names of different volatility.
    """
    ticker = s(result.get("ticker")) or "?"
    atr = f((raw or {}).get("ATR20"))
    spot = f(result.get("price"))
    r = f(risk_free)
    if r is None:
        r = FALLBACK_RISK_FREE
    elif r > 1:                            # given as a percent
        r = r / 100.0

    elig = EL.classify(result)
    discount = EL.discount_view(result)

    base = {
        "ticker": ticker,
        "name": s(result.get("name")) or ticker,
        "sector": s(result.get("sector")),
        "price": spot,
        "eligibility": elig,
        # Which price everything below was computed from. Carried on every
        # row, including rejections, because "is this the current price"
        # must be answerable from the table rather than inferred from how
        # recently someone remembers scanning.
        "spot_source": s(result.get("spot_source")) or "stored",
        "spot_stored": f(result.get("spot_stored")),
        "spot_drift_pct": f(result.get("spot_drift_pct")),
        # Context from the long-term engine that this page filters and
        # sorts on but never recomputes. Carried as a flat block rather
        # than by keeping `_lt`, which the store prunes because it tripled
        # the snapshot: these five values are what the page actually reads.
        #
        # The buy zone matters here specifically because a put is a paid
        # limit order — "the strike I would be assigned at is inside the
        # zone the long-term engine would buy in" is the single strongest
        # thing that can be said for a cash-secured put, and until now the
        # two pages held the halves of that sentence separately.
        "buy_zone": _buy_zone_view(result),
        "dist_52w_high": f((raw or {}).get("Dist_52W_High%")),
        "discount": discount,
        "regime": regime,
        "levels": ST.support_levels(result),
        "atr": atr,
        "days_to_earnings": f(result.get("days_to_earnings")),
        "earnings_risk": result.get("earnings_risk") or {},
        "lt_action": s(result.get("action")),
        "lt_score": f(result.get("lt_score")),
        "_lt": result,
    }

    # Step 1 is a gate. A rejected company never reaches an option chain
    # on a bulk scan — that ordering is what makes it structurally
    # impossible for a premium to rescue a name that failed on quality.
    #
    # `reference_chain` is the one exception, and it is narrow on
    # purpose: when you ask about ONE ticker by name, "rejected" answers
    # the recommendation but not the question "what is the market paying
    # here anyway". So the chain is fetched and shown as REFERENCE — the
    # verdict is untouched, the row never enters the ranked opportunity
    # list, and nothing computed here can flip a REJECT.
    if elig["status"] == "CSP REJECTED":
        q = result.get("quality") or {}
        out = {**base, "chosen": None, "trade": None,
               "final": {"action": "🔴 REJECT", "key": "REJECT",
                         "why": elig["blockers"][0]},
               "csp_score": None,
               # Carried as scalars so they survive the store's pruning of
               # rejected rows: a reference premium is only readable next
               # to what the business actually scored.
               "lquality": f(q.get("score")),
               "lq_tier": s(q.get("tier")),
               "no_trade_reason": elig["blockers"][0]}
        if reference_chain and spot:
            out["reference"] = _reference_chain(
                ticker, spot, r, min_dte, max_dte, today,
                atr=atr, result=result)
        return out

    if not spot:
        return {**base, "chosen": None, "trade": None, "csp_score": None,
                "final": {"action": "🟠 WATCH", "key": "WATCH",
                          "why": "no spot price"},
                "no_trade_reason": "no spot price"}

    # ── Chains ──────────────────────────────────────────────────────────
    all_exp = C.expiries(ticker)
    if not all_exp:
        return {**base, "chosen": None, "trade": None, "csp_score": None,
                "final": {"action": "🟠 WATCH", "key": "WATCH",
                          "why": "no listed options"},
                "no_trade_reason": "no listed options for this ticker"}

    picked = C.pick_expiries(all_exp, min_dte, max_dte, limit=3, today=today)
    if not picked:
        return {**base, "chosen": None, "trade": None, "csp_score": None,
                "final": {"action": "🟠 WATCH", "key": "WATCH",
                          "why": f"no expiry between {min_dte} and {max_dte} DTE"},
                "no_trade_reason": f"no expiry in the {min_dte}-{max_dte} DTE window"}

    # Quote quality depends on whether the book is live. Resolved once
    # per evaluation rather than per contract.
    from .store import market_open
    quotes_live = market_open() if quotes_live is None else quotes_live

    hv = V.realised_vol(_closes(ticker))
    best, per_expiry, skipped = None, [], []

    earn_policy = (earnings_policy or
                   ("CONTROLLED" if allow_earnings else "AVOID")).upper()

    for expiry in picked:
        d_check = C.dte(expiry, today)
        dist = RK.earnings_distance(result.get("days_to_earnings"), d_check)
        gate = RK.earnings_gate(dist, earn_policy)
        if not gate["allow"]:
            skipped.append({"expiry": expiry, "why": gate["why"],
                            "earnings": dist})
            continue

        rows = C.fetch_puts(ticker, expiry)
        if not rows:
            skipped.append({"expiry": expiry, "why": "chain unavailable"})
            continue

        t = C.year_fraction(expiry, today)
        d = C.dte(expiry, today)
        if not t or not d:
            continue

        atm = C.atm_iv(rows, spot)
        V.record(ticker, atm)

        candidates = []
        for row in rows:
            if row["strike"] >= spot:      # cash-secured puts are OTM
                continue
            iv = row["iv"]
            if iv is None and row.get("mid"):
                iv = G.implied_vol(row["mid"], spot, row["strike"], t, r)
                row["iv_source"] = "backsolved" if iv else "unusable"
            if iv is None:
                continue

            gk = G.put_greeks(spot, row["strike"], t, r, iv)
            if gk["delta"] is None:
                continue

            limit = C.limit_price(row)
            ret = SC.returns(row["strike"], limit, d)
            liq = C.liquidity_score(row, quotes_live)
            be = ret.get("breakeven")

            candidates.append({
                **row, **gk, "iv": iv, "expiry": expiry, "dte": d, "t": t,
                "limit_price": limit,
                "liquidity": liq["score"],
                "liquidity_notes": liq["notes"],
                "liquidity_tradable": liq["tradable"],
                "spread_verdict": liq["spread_verdict"],
                "_liq": liq,
                "expected_move": G.expected_move(spot, t, iv),
                # Probability of PROFIT is measured from the breakeven,
                # not the strike: the premium is kept either way.
                "prob_profit": G.prob_above(spot, be, t, r, iv) if be else None,
                "breakeven": be,
                "breakeven_pct": (round((be - spot) / spot * 100, 1)
                                  if be and spot else None),
                "distance_pct": round((row["strike"] - spot) / spot * 100, 1),
                **{k: v for k, v in ret.items() if k != "breakeven"},
            })

        sel = ST.select(candidates, result, regime, spot)
        sel["earnings"] = dist
        sel["earnings_gate"] = gate
        per_expiry.append({"expiry": expiry, "dte": d, "atm_iv": atm,
                           "selection": sel, "candidates": len(candidates),
                           "earnings": dist})

        if sel["chosen"] and (best is None or
                              (sel["chosen"]["fit"]["score"],
                               sel["chosen"].get("annualised") or 0) >
                              (best[1]["chosen"]["fit"]["score"],
                               best[1]["chosen"].get("annualised") or 0)):
            best = (expiry, sel, atm)

    if best is None:
        why = (skipped[0]["why"] if skipped else
               next((p["selection"]["no_trade_reason"] for p in per_expiry
                     if p["selection"].get("no_trade_reason")),
                    "no strike satisfied the support and buy-zone conditions"))
        blocked = next((p["selection"].get("blocked_on") for p in per_expiry
                        if p["selection"].get("blocked_on")), None)
        # Even with no contract, the stock half is scoreable and the ideal
        # zone is computable — that is what makes "wait for X" actionable
        # rather than just "nothing today".
        stock = SC.stock_score(result, discount, None, None)
        req = PR.required(0.20, (min_dte + max_dte) // 2, r,
                          (result.get("quality") or {}).get("score"),
                          discount.get("margin_pct"),
                          (ST.anchor_level(base["levels"]) or {}).get("confidence"),
                          regime)
        sinfo = {"score": None, "stock": stock, "option": {"score": None},
                 "components": [], "coverage": stock["coverage"],
                 "rejections": []}
        # An expiry skipped for earnings is EVENT RISK, not a generic
        # watch — the name is blocked by a date, and a date is the most
        # actionable trigger there is.
        skipped_earn = next((s2.get("earnings") for s2 in skipped
                             if s2.get("earnings")), None)
        if skipped_earn and skipped_earn.get("inside"):
            final = {"action": "🟠 EVENT RISK", "key": "EVENT_RISK",
                     "why": why}
        else:
            final = SC.final_action(sinfo, elig, {"happy_to_own": None}, why,
                                    blocked_on=blocked)

        # Expiries that avoid the print — and the direction here is the
        # opposite of the obvious one. To keep earnings OUT of the
        # contract the expiry must land BEFORE the print, not after it:
        # every expiry longer than the earnings date necessarily spans
        # it. So "wait for a post-earnings expiry" is not a thing you can
        # sell today — the two real options are a shorter contract that
        # closes before the print, or waiting for the print to pass.
        clean, wait_days = [], None
        edays = f(result.get("days_to_earnings"))
        if edays is not None:
            latest = edays - RK.DEFAULT_EARNINGS_BUFFER
            for e in all_exp:
                dd = C.dte(e, today)
                if dd is not None and 0 < dd <= latest:
                    clean.append(f"{e} ({dd}d)")
                if len(clean) >= 3:
                    break
            if not clean:
                # Nothing expires before the print, so the trigger is a
                # date rather than a contract.
                wait_days = round(edays)

        no_reqs = RQ.build(final["key"], {
            "result": result, "spot": spot, "dist": skipped_earn or {},
            "clean_expiries": clean, "wait_days": wait_days,
            "sel": next((p2["selection"] for p2 in per_expiry), None)})
        # No qualifying contract is still a question about what the chain
        # is paying — "no strike in the delta band" does not mean no
        # strikes exist. Same reference treatment as a rejected company,
        # and the same rule: it carries no verdict.
        ref = (_reference_chain(ticker, spot, r, min_dte, max_dte, today,
                                atr=atr, result=result)
               if reference_chain else None)
        q_no = result.get("quality") or {}
        return {**base, "chosen": None, "trade": None, "csp_score": None,
                "reference": ref,
                # Same scalars the rejected branch carries, and for the same
                # reason: the store slims these rows too, and a reference
                # premium only reads next to what the business scored.
                "lquality": f(q_no.get("score")),
                "lq_tier": s(q_no.get("tier")),
                "requirements": no_reqs,
                "earnings_distance": skipped_earn,
                "clean_expiries": clean,
                "earnings_wait_days": wait_days,
                "per_expiry": per_expiry, "skipped_expiries": skipped,
                "stock_score": stock["score"], "option_score": None,
                "score_detail": sinfo,
                "required": req,
                "ideal_zone": PR.ideal_zone(
                    spot, base["levels"], ST.anchor_level(base["levels"]),
                    None, req, min_dte, max_dte),
                "final": final,
                "no_trade_reason": why}

    expiry, sel, atm = best
    chosen = sel["chosen"]

    ivr = V.rank(ticker, atm)
    ratio = V.iv_vs_hv(atm, hv)
    iv_op = V.iv_opportunity(ivr, ratio)

    ret = SC.returns(chosen["strike"], chosen["limit_price"], chosen["dte"])
    assign = SC.assignment(chosen["strike"], chosen["limit_price"],
                           result, discount, sel["levels"])

    # ── Is the option itself worth selling? ─────────────────────────────
    anchor = sel.get("anchor") or {}
    req = PR.required(chosen.get("delta"), chosen.get("dte"), r,
                      (result.get("quality") or {}).get("score"),
                      discount.get("margin_pct"),
                      anchor.get("confidence"), regime,
                      iv_rv=ratio.get("ratio"))
    adq = PR.adequacy(ret.get("yield_pct"), req)
    eff = PR.capital_efficiency(ret.get("annualised"), r,
                                ret.get("collateral"),
                                ret.get("premium_total"), chosen.get("dte"))
    downside = PR.per_unit_downside(chosen.get("limit_price"), atr,
                                    chosen.get("expected_move"),
                                    assign.get("basis"), sel["levels"])
    mos = PR.margin_at_assignment(assign.get("basis"), discount)
    zone = PR.ideal_zone(spot, sel["levels"], anchor, sel.get("buy_zone"),
                         req, min_dte, max_dte)

    liq = chosen.get("_liq") or {}
    liq = {**liq, "spread_pct": chosen.get("spread_pct")}

    # ── Risk: the third factor ──────────────────────────────────────────
    prefs = dte_prefs or {}
    dte_info = RK.dte_fit(chosen.get("dte"),
                          target=prefs.get("target", RK.DEFAULT_TARGET_DTE),
                          pref=(prefs.get("pref_low", RK.DEFAULT_PREF_LOW),
                                prefs.get("pref_high", RK.DEFAULT_PREF_HIGH)),
                          hard_max=prefs.get("hard_max", max_dte))
    dist = sel.get("earnings") or RK.earnings_distance(
        result.get("days_to_earnings"), chosen.get("dte"))
    cushion = RK.move_cushion(spot, chosen.get("strike"),
                              chosen.get("expected_move"))
    assign_q = RK.assignment_score(elig, discount, assign, sel["levels"],
                                   assign.get("basis"), spot, dist, cushion)
    rinfo = RK.risk_score(dte_info, dist, cushion, assign_q,
                          chosen.get("liquidity"))

    sinfo = SC.compute(result, elig, discount, chosen, ret, iv_op, liq,
                       regime, assign, fit_anchor=anchor, adq=adq, eff=eff)

    # The earnings policy's penalty lands on the option score, not on the
    # company's — the print is a property of the contract's window.
    #
    # Settled HERE rather than at the expiry filter, because ACCEPT's four
    # conditions are all properties of the contract and none of them
    # existed up there. Re-asked now that delta, liquidity and adequacy are
    # known, so an ACCEPT that is genuinely earned is taken without a
    # penalty instead of being reported as "ACCEPT not met (delta 1.00)"
    # about a strike that had not been picked yet.
    gate = sel.get("earnings_gate") or {}
    if gate.get("pending"):
        gate = RK.earnings_gate(
            dist, earn_policy,
            quality=(result.get("quality") or {}).get("score"),
            delta=chosen.get("delta"),
            liquidity=chosen.get("liquidity"),
            adequacy=(adq or {}).get("ratio"),
            settle=True)
        sel["earnings_gate"] = gate
    pen_opt = gate.get("penalty_option") or 0
    if pen_opt and sinfo.get("option", {}).get("score") is not None:
        sinfo["option"]["score"] = max(0, sinfo["option"]["score"] - pen_opt)

    stock_sc = (sinfo.get("stock") or {}).get("score")
    option_sc = (sinfo.get("option") or {}).get("score")
    sinfo["score"] = RK.combine(stock_sc, option_sc, rinfo.get("score"))
    sinfo["risk"] = rinfo

    final = SC.final_action(sinfo, elig, assign, None, ret=ret, iv_op=iv_op,
                            adq=adq, liq=liq, blocked_on=sel.get("blocked_on"),
                            earnings=dist, risk=rinfo)

    reqs = RQ.build(final["key"], {
        "ret": ret, "req": req, "adq": adq, "iv_op": iv_op, "ratio": ratio,
        "chosen": chosen, "sel": sel, "dist": dist, "liq": liq,
        "result": result, "spot": spot})

    return {
        **base,
        "expiry": expiry,
        "chosen": chosen,
        "selection": sel,
        "per_expiry": per_expiry,
        "skipped_expiries": skipped,
        "atm_iv": atm,
        "hv": hv,
        "atr": atr,
        "iv_rank": ivr,
        "iv_vs_hv": ratio,
        "iv_opportunity": iv_op,
        "returns": ret,
        "assignment": assign,
        "required": req,
        "adequacy": adq,
        "efficiency": eff,
        "downside": downside,
        "margin_at_assignment": mos,
        "ideal_zone": zone,
        "liquidity": liq,
        "csp_score": sinfo["score"],
        "stock_score": (sinfo.get("stock") or {}).get("score"),
        "option_score": (sinfo.get("option") or {}).get("score"),
        "risk_score": rinfo.get("score"),
        "risk": rinfo,
        "dte_fit": dte_info,
        "earnings_distance": dist,
        "move_cushion": cushion,
        "assignment_quality": assign_q,
        "requirements": reqs,
        "score_detail": sinfo,
        "final": final,
        "headline": SC.headline(sinfo, final),
        "no_trade_reason": None,
    }


def evaluate_universe(results, regime="SELECTIVE", risk_free=None,
                      min_dte=DEFAULT_MIN_DTE, max_dte=DEFAULT_MAX_DTE,
                      allow_earnings=False, limit=None, today=None,
                      raw_rows=None, reference_chain=False,
                      earnings_policy=None, dte_prefs=None,
                      reference_rejected=False, live_prices=True,
                      reference_budget=None) -> list:
    """Every long-term result -> ranked CSP verdicts.

    Eligibility runs on all of them first and the chain work is done only
    for survivors, so a 46-name universe that yields six approved
    companies makes six chain passes, not forty-six.
    """
    ordered = sorted(
        results or [],
        key=lambda r: -(f((r.get("quality") or {}).get("score")) or 0))

    # One batched quote call for the whole set, before any chain work. The
    # result dicts are re-priced in place: every consumer downstream reads
    # `result["price"]`, so overriding it here is what makes the strike
    # filter, the greeks and the table all agree on one number — and on
    # the number the chain beside them was quoted against.
    #
    # `spot_source` is recorded rather than assumed. A symbol that could
    # not be quoted keeps its stored price, and the page has to be able to
    # say which of the two it is looking at.
    if live_prices:
        quotes = live_spots(r.get("ticker") for r in ordered)
        for res in ordered:
            fresh = quotes.get(str(res.get("ticker") or "").upper())
            stored = f(res.get("price"))
            if fresh:
                res["price"] = fresh
                res["spot_source"] = "live"
                res["spot_stored"] = stored
                res["spot_drift_pct"] = (round((fresh / stored - 1) * 100, 2)
                                         if stored else None)
            else:
                res["spot_source"] = "stored"
                res["spot_drift_pct"] = None

    # Which REJECTED names still get their chain priced as reference. The
    # verdict is untouched — see evaluate()'s comment — this only decides
    # which rejections get to answer "what is the market paying here
    # anyway" alongside "no".
    #
    # Budgeted rather than unlimited because rejections are ~90% of a run
    # and each costs a chain round trip. Spread ACROSS rejection buckets
    # rather than down the quality ranking: spending it purely on quality
    # put every fetch on names rejected for being expensive — those are the
    # only high-quality rejections there are — so the 437 names below the
    # quality floor stayed blank and the page looked broken rather than
    # budgeted. See screen.spread_reference_budget().
    ref_wanted: set = set()
    if reference_rejected:
        from . import screen as _CS
        cands = []
        for res in ordered:
            elig = EL.classify(res)
            if elig["status"] != "CSP REJECTED":
                continue
            cands.append((res.get("ticker"),
                          (elig.get("blockers") or [""])[0],
                          f((res.get("quality") or {}).get("score"))))
        ref_wanted = _CS.spread_reference_budget(
            cands, reference_budget or MAX_REFERENCE_CHAINS)

    out, fetched = [], 0
    for res in ordered:
        elig = EL.classify(res)
        raw = (raw_rows or {}).get(res.get("ticker"))
        if elig["status"] == "CSP REJECTED":
            want_ref = (reference_chain
                        or str(res.get("ticker") or "").upper() in ref_wanted)
            out.append(evaluate(res, regime, risk_free, min_dte, max_dte,
                                allow_earnings, today, raw,
                                reference_chain=want_ref,
                                earnings_policy=earnings_policy,
                                dte_prefs=dte_prefs))
            continue
        if limit is not None and fetched >= limit:
            continue
        fetched += 1
        # The same budget covers the OTHER way a name comes back empty:
        # it cleared the company gate, its chain was fetched, and no strike
        # qualified — usually on liquidity. WDAY is the shape: the right
        # strike exists at $160 and is quoted 61% wide. That is a real
        # rejection and it stays one, but "no strike qualifies" and "there
        # is nothing here" are different statements, and only the second
        # justifies a row of blanks.
        #
        # This does NOT draw on `budget`. The extra fetch only happens for a
        # name that ends up with no qualifying contract, and the eligible
        # set is already capped by `limit` — spending the rejected budget
        # here would let twenty-five successful names exhaust it and leave
        # nothing for the rejections it was reserved for.
        try:
            out.append(evaluate(res, regime, risk_free, min_dte, max_dte,
                                allow_earnings, today, raw,
                                reference_chain=(reference_chain
                                                 or reference_rejected),
                                earnings_policy=earnings_policy,
                                dte_prefs=dte_prefs))
        except Exception as e:                      # one bad chain, not a run
            print(f"[CSP] {res.get('ticker')}: evaluation failed ({e})")

    rank_order = {"SELL": 0, "VERIFY": 1, "SELL_DIP": 2, "EVENT_RISK": 3,
                  "WAIT_IV": 4, "WAIT_LEVEL": 5, "WATCH": 6, "REJECT": 7}
    out.sort(key=lambda r: (rank_order.get((r.get("final") or {}).get("key"), 9),
                            -(r.get("csp_score") or 0)))
    return out


def portfolio_view(rows, capital, max_single_pct=20.0,
                   max_sector_pct=35.0) -> dict:
    """Step 10 — what the SELL-rated set does to the account.

    Concentration is measured against the CSP collateral commitment, not
    against the existing portfolio: this answers "if I sold all of these
    today, what would I be on the hook for", which is the exposure a
    cash-secured put actually creates.
    """
    sells = [r for r in rows
             if (r.get("final") or {}).get("key") in ("SELL", "VERIFY",
                                                      "SELL_DIP")
             and r.get("chosen")]
    cap = f(capital) or 0.0

    positions, by_sector, total = [], {}, 0.0
    for r in sells:
        coll = (r.get("returns") or {}).get("collateral")
        if not coll:
            continue
        max_dollars = cap * max_single_pct / 100.0 if cap else None
        n = int(max_dollars // coll) if max_dollars else 1
        n = max(0, n)
        committed = n * coll
        total += committed
        sec = r.get("sector") or "Unknown"
        by_sector[sec] = by_sector.get(sec, 0.0) + committed
        positions.append({
            "ticker": r["ticker"], "sector": sec,
            "contracts": n, "collateral_each": coll,
            "committed": committed,
            "pct_of_capital": round(committed / cap * 100, 1) if cap else None,
            "premium": (r.get("returns") or {}).get("premium_total"),
        })

    warnings = []
    if cap and total > cap:
        warnings.append(f"total collateral ${total:,.0f} exceeds ${cap:,.0f} "
                        f"of capital — not all of these can be held at once")
    for sec, amt in by_sector.items():
        pct = (amt / cap * 100) if cap else 0
        if pct > max_sector_pct:
            warnings.append(f"{sec} would be {pct:.0f}% of capital "
                            f"(cap {max_sector_pct:.0f}%)")

    return {
        "positions": positions,
        "total_collateral": round(total, 2),
        "capital": cap,
        "utilisation_pct": round(total / cap * 100, 1) if cap else None,
        "by_sector": {k: round(v, 2) for k, v in by_sector.items()},
        "total_premium": round(sum(p["premium"] or 0 for p in positions), 2),
        "warnings": warnings,
    }
