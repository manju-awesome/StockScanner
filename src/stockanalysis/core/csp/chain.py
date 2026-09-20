"""
Option-chain retrieval and normalisation for the CSP engine.

yfinance is the source: `Ticker.options` lists expiries, `option_chain(d)`
returns the puts frame with bid/ask/lastPrice/IV/openInterest/volume. It
does NOT return greeks, so `greeks.py` computes them from the chain's own
IV and the risk-free rate the long-term engine already resolves.

Two data hazards are handled here rather than downstream:

1.  **bid = 0.** Common on far-OTM strikes. A zero bid means nobody is
    bidding, so the contract is unsellable at any price the mid implies.
    Those rows are marked `sellable=False` and dropped by the engine
    rather than being ranked on a mid that cannot be hit.

2.  **IV = 0 or absurd.** yfinance emits 0.0 and 1e-5 on stale strikes.
    Anything outside a sane band is discarded and backsolved from the
    mid instead, and the row records which source the IV came from.

Everything returned is plain Python floats — numpy/pandas scalars break
`json.dumps()` when the page serialises a run.
"""

from __future__ import annotations

import datetime as _dt
import math

MIN_SANE_IV = 0.02       # 2% — below this the quote is stale, not calm
MAX_SANE_IV = 4.00       # 400% — above this it is a data error

# yfinance expiries are plain calendar dates. Options stop trading at the
# close, so a contract expiring today has hours, not a day, of life left.
# Adding this keeps `t` positive through the final session instead of
# collapsing to zero and voiding every greek.
_EXPIRY_HOUR_FRACTION = 16.0 / 24.0


def _f(v):
    """Plain float or None, absorbing numpy scalars, NaN and blanks."""
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out or math.isinf(out) else out


def year_fraction(expiry: str, today: _dt.date | None = None):
    """Calendar years to expiry. Calendar, not trading, days: the IV the
    chain quotes is itself a calendar-time annualisation, so mixing in a
    252-day count would misprice the greeks against their own input."""
    if not expiry:
        return None
    try:
        d = _dt.date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return None
    ref = today or _dt.date.today()
    days = (d - ref).days + _EXPIRY_HOUR_FRACTION
    return None if days <= 0 else days / 365.0


def dte(expiry: str, today: _dt.date | None = None):
    """Whole calendar days to expiry, as a broker screen shows it."""
    if not expiry:
        return None
    try:
        d = _dt.date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return None
    return (d - (today or _dt.date.today())).days


def expiries(ticker: str) -> list[str]:
    """Every listed expiry, oldest first. Empty on any failure — a name
    with no options is a normal outcome, not an error to raise."""
    try:
        import yfinance as yf
        return [str(e) for e in (yf.Ticker(ticker).options or ())]
    except Exception as e:                     # network, delisting, parse
        print(f"[CSP] {ticker}: expiry list unavailable ({e})")
        return []


def is_monthly(expiry: str) -> bool:
    """Whether `expiry` is a standard monthly — the third Friday.

    This matters more than it looks. Standard monthlies carry the
    overwhelming majority of open interest; the weeklies around them are
    often quoted 0.01 bid / 0.30 ask on the same strike, which is not a
    market you can sell into. Preferring the monthly is the single
    biggest liquidity win available at expiry-selection time.
    """
    try:
        d = _dt.date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return False
    return d.weekday() == 4 and 15 <= d.day <= 21


def pick_expiries(available, min_dte=20, max_dte=45, limit=3,
                  today=None) -> list[str]:
    """The expiries inside the target DTE window, monthlies first.

    Widens once to 14-60 if the window is empty, because a name whose
    only listings are 12 and 51 days out still has a tradable CSP and
    silently returning nothing would read as "no options".
    """
    def inside(lo, hi):
        out = []
        for e in available or ():
            d = dte(e, today)
            if d is not None and lo <= d <= hi:
                # Monthlies sort first; within each group, nearest first.
                out.append((0 if is_monthly(e) else 1, d, e))
        out.sort()
        return [e for _, _, e in out[:limit]]

    got = inside(min_dte, max_dte)
    return got or inside(14, 60)


def fetch_puts(ticker: str, expiry: str) -> list[dict]:
    """Normalised put rows for one expiry.

    Each row: strike, bid, ask, mid, last, spread, spread_pct, volume,
    open_interest, iv, iv_source, sellable. No greeks yet — those need
    spot and the risk-free rate, which the engine holds.
    """
    try:
        import yfinance as yf
        frame = yf.Ticker(ticker).option_chain(expiry).puts
    except Exception as e:
        print(f"[CSP] {ticker} {expiry}: chain unavailable ({e})")
        return []

    rows = []
    for rec in frame.to_dict("records"):
        strike = _f(rec.get("strike"))
        if strike is None or strike <= 0:
            continue

        bid, ask = _f(rec.get("bid")), _f(rec.get("ask"))
        last = _f(rec.get("lastPrice"))

        # Mid is only meaningful with a two-sided market. With one side
        # missing the last trade is the honest fallback, and the row is
        # flagged unsellable so nothing ranks on it.
        if bid is not None and ask is not None and ask > 0 and bid > 0:
            mid = (bid + ask) / 2.0
            spread = ask - bid
        else:
            mid, spread = last, None

        iv = _f(rec.get("impliedVolatility"))
        iv_source = "chain"
        if iv is None or not (MIN_SANE_IV <= iv <= MAX_SANE_IV):
            iv, iv_source = None, "unusable"

        rows.append({
            "strike": strike,
            "bid": bid, "ask": ask, "mid": mid, "last": last,
            "spread": spread,
            "spread_pct": (round(spread / mid * 100, 1)
                           if spread is not None and mid else None),
            "volume": _f(rec.get("volume")) or 0.0,
            "open_interest": _f(rec.get("openInterest")) or 0.0,
            "iv": iv,
            "iv_source": iv_source,
            "in_the_money": bool(rec.get("inTheMoney")),
            # A zero or absent bid is the whole test: you cannot SELL a
            # put into a market with no bid, however rich the mid looks.
            "sellable": bool(bid and bid > 0),
        })

    rows.sort(key=lambda r: r["strike"])
    return rows


def atm_iv(rows, spot):
    """IV of the strike nearest spot — the name's headline vol.

    Taken from the put chain the engine already fetched rather than a
    separate ATM lookup, so the IV that feeds IV Rank is the same
    surface the strikes are priced off.
    """
    if not rows or not spot:
        return None
    usable = [r for r in rows if r["iv"] is not None]
    if not usable:
        return None
    return min(usable, key=lambda r: abs(r["strike"] - spot))["iv"]


# Spread bands, as a share of mid. The spread is not one input among
# several — it is the price of being wrong about the fill, and it is paid
# on the way in AND on the way out if the position is ever closed early.
SPREAD_EXCELLENT = 5.0
SPREAD_ACCEPTABLE = 8.0
SPREAD_CAUTION = 10.0            # above this the contract is not tradable

SPREAD_VERDICTS = ("EXCELLENT", "ACCEPTABLE", "CAUTION", "REJECT")


def spread_verdict(spread_pct) -> tuple[str, str]:
    """(verdict, human sentence) for a bid/ask spread."""
    if spread_pct is None:
        return "REJECT", "no two-sided market"
    if spread_pct <= SPREAD_EXCELLENT:
        return "EXCELLENT", f"tight market ({spread_pct:.1f}% of mid)"
    if spread_pct <= SPREAD_ACCEPTABLE:
        return "ACCEPTABLE", f"workable spread ({spread_pct:.1f}% of mid)"
    if spread_pct <= SPREAD_CAUTION:
        return "CAUTION", f"wide spread ({spread_pct:.1f}% of mid)"
    return "REJECT", (f"{spread_pct:.1f}% bid/ask spread — the round trip "
                      f"costs more than the edge")


def liquidity_score(row, quotes_live: bool = True) -> dict:
    """{score, notes, spread_verdict, tradable} for one contract.

    The spread GATES the score rather than contributing to it. A
    contract quoted 1.60/1.83 has a 13% spread: whatever its open
    interest, you are giving up a large share of the credit to the
    market maker at entry, and the previous additive scoring let strong
    open interest and volume carry it to a respectable 64/100. Under a
    gate it cannot exceed 35, and `tradable` is False, which is what the
    number is actually saying.
    """
    notes = []
    if not row.get("sellable"):
        return {"score": 0, "notes": ["no bid — cannot be sold at any price"],
                "spread_verdict": "REJECT", "tradable": False}

    oi, vol = row.get("open_interest") or 0, row.get("volume") or 0
    sp = row.get("spread_pct")
    verdict, sentence = spread_verdict(sp)

    # Outside regular hours market makers pull their quotes and the book
    # widens to something that has nothing to do with the fill you would
    # get at 10am. A 21%-wide after-hours quote on a name that trades 5%
    # wide intraday is a measurement artifact, and rejecting on it would
    # throw away good trades every evening. So when quotes are not live
    # the spread is reported as UNKNOWN rather than scored or gated — the
    # honest answer is that it cannot be assessed until the open.
    if not quotes_live:
        notes.append(f"spread not assessable — market closed "
                     f"(quoted {sp:.0f}% wide, re-scan at the open)"
                     if sp is not None else
                     "spread not assessable — market closed")
        verdict = "UNKNOWN"
    elif verdict != "EXCELLENT":
        notes.append(sentence)

    # Depth, 0-100 on its own terms.
    if oi >= 2000:   depth = 65
    elif oi >= 1000: depth = 58
    elif oi >= 500:  depth = 48
    elif oi >= 200:  depth = 34; notes.append(f"thin open interest ({oi:.0f})")
    elif oi >= 50:   depth = 18; notes.append(f"very thin open interest ({oi:.0f})")
    else:            depth = 4;  notes.append(f"almost no open interest ({oi:.0f})")

    if vol >= 500:   depth += 35
    elif vol >= 100: depth += 28
    elif vol >= 20:  depth += 18
    elif vol >= 1:   depth += 8
    else:            notes.append("no volume today")

    # The gate. A CAUTION spread caps the contract well below the
    # threshold anything downstream will accept as a primary trade.
    cap = {"EXCELLENT": 100, "ACCEPTABLE": 78, "CAUTION": 35,
           "REJECT": 15, "UNKNOWN": 70}[verdict]
    score = min(depth, cap)

    # None, not False: "cannot be assessed" is a third state, and callers
    # must not read it as a failed gate.
    tradable = (None if verdict == "UNKNOWN"
                else verdict in ("EXCELLENT", "ACCEPTABLE"))
    return {"score": int(score), "notes": notes,
            "spread_verdict": verdict, "quotes_live": quotes_live,
            "tradable": tradable}


def limit_price(row):
    """What to actually work the order at, rather than the midpoint.

    Selling at the mid assumes the market meets you halfway; on a wide
    spread it usually does not, and quoting the mid as "the premium"
    overstates every yield downstream. This sits a third of the way from
    mid toward the bid — inside the spread so there is room to be filled,
    below the mid so the number is defensible — then rounds DOWN to the
    nearest cent so the printed price is one you can actually enter.

    On a spread of 2% or less the mid is realistic and is used as-is.
    """
    bid, ask, mid = row.get("bid"), row.get("ask"), row.get("mid")
    if bid is None or ask is None or mid is None or ask <= bid:
        return bid or mid
    sp_pct = row.get("spread_pct")
    if sp_pct is not None and sp_pct <= 2:
        return math.floor(mid * 100) / 100.0
    return math.floor((mid - (mid - bid) / 3.0) * 100) / 100.0
