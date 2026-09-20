"""
The short-side engine — both directions scored, neither one a gate.

The change from the long-only design, stated plainly: **failing the
long-term quality test is not a verdict, it is one input.** The old path
ended at "AVOID — failed on business quality", which is a correct
statement about owning the company and an incomplete one about the
stock. Every name rejected for a long is now evaluated for a short, and
a low LQuality raises the short score instead of ending the analysis.

Three readings, then a direction:

    LONG SCORE    from core.longterm — not recomputed here
    SHORT SCORE   from thesis.py — weakness, valuation, extension,
                  overbought, distribution, location, reward
    AVOID SCORE   how strongly the name argues for standing aside

and four buckets:

    🔥 SHORT NOW        strong thesis AND price confirming
    👀 SHORT WATCH      strong thesis, no confirmation yet
    🟢 LONG OPPORTUNITY the long engine's own verdict stands
    ⚪ NO EDGE          neither side clears

The distinction that makes this useful: a short score of 82 does NOT
mean short it. It means the asymmetric thesis is there and price has not
yet agreed. Conflating those two is how a short book gets built at the
exact moment a momentum name is least willing to break, so confirmation
is a separate axis from strength and is never blended into it.

NO EDGE is reserved for names where neither side clears. A stock does
not land there merely for failing the long test — that was the whole
problem with the old output.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import f, s

from . import reversal as RV
from . import thesis as TH

# ONE threshold for the thesis, and confirmation as a separate axis.
#
# An earlier version had a second, higher bar for SHORT NOW, which
# created a dead band: a 69-scoring name with a textbook bearish
# engulfing, a first red day AND heavy volume still read WATCH, because
# confirmation simply did not count below 70. That is backwards — the
# confirmation is precisely the evidence that closes the gap. So the
# thesis clears once, and whether price has begun to agree decides which
# of the two short buckets it lands in.
SHORT_STRONG = 65        # the thesis is real
LONG_STRONG = 65         # the long engine's own score

# Long actions that mean the long engine positively wants the name.
LONG_ACTIONS = ("BUY NOW", "BUY ON CONFIRMATION", "BUY ON 8/21 EMA",
                "BUY ON 50 MA", "BUY ON BREAKOUT RETEST", "BUY ON 200 MA",
                "BUY ON SUPPORT")

BUCKETS = ("SHORT NOW", "SHORT WATCH", "LONG OPPORTUNITY", "NO EDGE")
BUCKET_ICONS = {"SHORT NOW": "🔥", "SHORT WATCH": "👀",
                "LONG OPPORTUNITY": "🟢", "NO EDGE": "⚪"}


def long_score(result) -> dict:
    """The long reading, taken from core.longterm rather than rebuilt.

    Rebuilding it here would create a second opinion that could disagree
    with /longterm about the same company on the same day. The engine's
    `lt_score` already blends quality, valuation, trend and entry.
    """
    lt = f(result.get("lt_score"))
    action = s(result.get("action"))
    inv = result.get("investment") or {}

    return {
        "score": None if lt is None else round(lt),
        "action": action,
        "investment_status": s(inv.get("status")),
        "wants_it": action in LONG_ACTIONS,
        "detail": (f"{action} — LT score {lt:.0f}/100" if lt is not None
                   else action or "no long reading"),
    }


def avoid_score(long_info, short_info) -> dict:
    """How strongly the name argues for standing aside.

    Deliberately NOT `100 - max(long, short)`. Two very different states
    collapse into the same number that way: a stock nobody has an opinion
    about, and a stock where both sides half-argue and the honest answer
    is that it is unreadable. The second is worse, and this separates
    them by adding a penalty when the two readings are close together.
    """
    l, sh = long_info.get("score"), short_info.get("score")
    if l is None and sh is None:
        return {"score": None, "detail": "neither side could be scored"}

    best = max(v for v in (l, sh) if v is not None)
    base = max(0.0, 100.0 - best)

    conflict = 0.0
    if l is not None and sh is not None and abs(l - sh) < 15:
        # Both sides making a similar-strength case is not a signal, it
        # is noise, and it should read as MORE reason to stand aside.
        conflict = 15.0 - abs(l - sh)

    score = min(100.0, base + conflict)
    return {
        "score": round(score),
        "conflict": round(conflict),
        "detail": ("no clear edge on either side" if best < 50 else
                   "both sides argue about equally — unreadable"
                   if conflict > 8 else
                   f"the {'long' if best == l else 'short'} case is the "
                   f"stronger one"),
    }


def _bucket(long_info, short_info, confirm) -> dict:
    """The four-way call, and why."""
    l, sh = long_info.get("score"), short_info.get("score")
    confirmed = (confirm or {}).get("confirmed")
    caps = short_info.get("caps") or []

    short_strong = sh is not None and sh >= SHORT_STRONG

    if short_strong and confirmed:
        return {"bucket": "SHORT NOW", "icon": "🔥",
                "why": (f"short thesis {sh}/100 and price is confirming — "
                        f"{(confirm or {}).get('detail')}")}

    if short_strong:
        # The single most important sentence this engine produces.
        blockers = []
        if not confirmed:
            blockers.append((confirm or {}).get("detail")
                            or "no bearish reversal yet")
        blockers.extend(caps)
        return {"bucket": "SHORT WATCH", "icon": "👀",
                "why": (f"short thesis {sh}/100, awaiting confirmation — "
                        + "; ".join(blockers))}

    if l is not None and l >= LONG_STRONG and long_info.get("wants_it"):
        return {"bucket": "LONG OPPORTUNITY", "icon": "🟢",
                "why": long_info.get("detail") or f"long score {l}/100"}

    # Everything left over. Note what this does NOT say: it never cites
    # the quality gate as the reason, because failing to be ownable is
    # not the same as having no edge.
    bits = []
    if sh is not None:
        bits.append(f"short {sh}/100 below the {SHORT_STRONG} threshold")
    if l is not None:
        bits.append(f"long {l}/100")
    return {"bucket": "NO EDGE", "icon": "⚪",
            "why": "; ".join(bits) or "not enough data to read either side"}


def plan(row, price, atr, short_info) -> dict:
    """Entry, stop and targets for the short, in ATR terms.

    The stop is above the recent high rather than a fixed percentage: on
    a parabolic name a 5% stop is inside a single session's range and
    would be taken out by noise. Targets are the levels the stock is
    extended FROM, which is where a reversion actually goes.
    """
    p = f(price)
    a = f(atr)
    if p is None:
        return {}

    high_52 = f(row.get("52W High"))
    prev_high = f(row.get("Prev-Day High"))
    # The nearer of the two, but never inside one ATR of price.
    anchors = [x for x in (high_52, prev_high) if x and x > p]
    stop = min(anchors) if anchors else None
    if stop is not None and a and stop - p < a:
        stop = p + a
    elif stop is None and a:
        stop = p + 1.5 * a

    targets = (short_info.get("parts", {}).get("reward", {}) or {}).get(
        "targets") or []
    risk = (stop - p) if stop else None

    laddered = []
    for t in targets[:3]:
        rr = (None if not risk or risk <= 0
              else round((p - t["price"]) / risk, 2))
        laddered.append({**t, "rr": rr})

    return {
        "entry": p,
        "stop": None if stop is None else round(stop, 2),
        "stop_pct": (None if stop is None else
                     round((stop - p) / p * 100, 1)),
        "stop_atr": (None if stop is None or not a else
                     round((stop - p) / a, 2)),
        "risk_per_share": None if risk is None else round(risk, 2),
        "targets": laddered,
        "best_rr": max((t["rr"] for t in laddered if t["rr"] is not None),
                       default=None),
    }


def evaluate(result: dict, row: dict, fetch_bars: bool = True,
             daily=None) -> dict:
    """One long-term result plus its scan row -> a two-sided verdict."""
    ticker = s(result.get("ticker")) or s(row.get("Ticker")) or "?"
    price = f(result.get("price")) or f(row.get("Current Price"))
    atr = f(row.get("ATR20"))

    long_info = long_score(result)
    short_info = TH.compute(result, row, price, atr)

    # Bars are fetched only for names whose thesis already clears —
    # confirmation is a trigger for a setup that exists, so there is
    # nothing to confirm below the threshold, and this keeps a 552-name
    # scan to a few dozen network calls.
    sh = short_info.get("score")
    confirm = {"state": "NOT_CHECKED", "label": "⚪ Not checked",
               "confirmed": False, "signals": [],
               "detail": "thesis below threshold — confirmation not checked"}
    if sh is not None and sh >= SHORT_STRONG:
        if daily is None and fetch_bars:
            daily = RV.fetch_daily(ticker)
        if daily is not None:
            confirm = RV.confirmation(daily)

    avoid = avoid_score(long_info, short_info)
    call = _bucket(long_info, short_info, confirm)

    return {
        "ticker": ticker,
        "name": s(result.get("name")) or ticker,
        "sector": s(result.get("sector")),
        "price": price,
        "atr": atr,
        "atr_pct": f(row.get("ATR_Pct")),
        "long": long_info,
        "short": short_info,
        "avoid": avoid,
        "confirmation": confirm,
        "extension": short_info.get("extension"),
        "plan": plan(row, price, atr, short_info) if sh and sh >= SHORT_STRONG
                else {},
        "bucket": call["bucket"],
        "icon": call["icon"],
        "why": call["why"],
        "long_score": long_info.get("score"),
        "short_score": sh,
        "avoid_score": avoid.get("score"),
    }


def evaluate_universe(results, rows, fetch_bars: bool = True,
                      limit=None) -> list:
    """Every name scored both ways, ranked most actionable first.

    `rows` is {ticker: scan_row}. Names are pre-sorted by short thesis
    before bar fetching so that `limit` caps the network work on the
    strongest candidates rather than an arbitrary alphabetical slice.
    """
    by_ticker = rows if isinstance(rows, dict) else {
        r.get("Ticker"): r for r in (rows or [])}

    # First pass: no network, just the thesis.
    staged = []
    for res in results or []:
        row = by_ticker.get(res.get("ticker"))
        if not row:
            continue
        staged.append((res, row, TH.compute(
            res, row, f(res.get("price")) or f(row.get("Current Price")),
            f(row.get("ATR20"))).get("score") or 0))
    staged.sort(key=lambda x: -x[2])

    out, fetched = [], 0
    for res, row, sc in staged:
        allow = fetch_bars and (limit is None or fetched < limit)
        if allow and sc >= SHORT_STRONG:
            fetched += 1
        try:
            out.append(evaluate(res, row, fetch_bars=allow))
        except Exception as e:
            print(f"[Short] {res.get('ticker')}: evaluation failed ({e})")

    order = {"SHORT NOW": 0, "SHORT WATCH": 1, "LONG OPPORTUNITY": 2,
             "NO EDGE": 3}
    out.sort(key=lambda r: (order.get(r["bucket"], 9),
                            -(r.get("short_score") or 0)))
    return out


def counts(rows) -> dict:
    out = {b: 0 for b in BUCKETS}
    for r in rows or []:
        out[r["bucket"]] = out.get(r["bucket"], 0) + 1
    return out
