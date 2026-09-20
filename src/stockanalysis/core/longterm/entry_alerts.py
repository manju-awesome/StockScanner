"""
entry_alerts.py
===============
"Price has arrived at the level you planned to buy."

The Long-Term engine prices an entry for every actionable name — a moving
average, a tested shelf, a prior breakout — and those levels sit below the
market for weeks. The whole point of a resting order is that you are not
watching, so the one thing worth interrupting a day for is price reaching
one. This module watches that gap and nothing else.

Three decisions shape what it will and will not fire on:

1. **Live prices, stored levels.** The entry and stop come from the last
   scan; the price is fetched fresh. That asymmetry is deliberate — a
   200 MA moves a fraction of a percent a day and is stale-tolerant, while
   a price from this morning's scan is exactly the input that must not be.

2. **Resting orders only.** A BUY NOW verdict's entry IS the current price,
   so "within 1% of the entry" is true by construction and would fire on
   every such name, every cycle, forever. An alert that cannot fail to
   trigger carries no information.

3. **BUY verdicts only.** Every sized plan carries an entry, including the
   WAIT and OWN/WAIT ones, and for most of those the entry is the nearest
   support shelf — which sits within a percent of the price roughly half the
   time by construction, not by event. Measured over the live library, 44 of
   104 resting orders were inside the band and not one of them was a name
   the engine was offering to buy. Requiring the engine's own BUY verdict is
   what turns "price is near a level" into "the trade you were waiting for
   is available".

The email goes out under its own subject rather than inside the general
alert digest — see SUBJECT — so these can be filtered and read as a single
standing list of "orders to work".
"""

from __future__ import annotations

from stockanalysis.core import alerts as A

# How close counts as arriving. Symmetric: price can approach a resting buy
# from above (drifting down to it) or slip just under it, and both are the
# same event to someone with an order to place.
NEAR_ENTRY_PCT = 1.0

CATEGORY = "longterm_entry"
SUBJECT = "Longterm swing trades"


def _gap_pct(price, entry) -> float | None:
    """Signed distance from the entry, in percent of the entry. Positive =
    price is above the level and would have to fall to fill."""
    try:
        price, entry = float(price), float(entry)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    return (price / entry - 1) * 100.0


def near_entry(result: dict, price=None, within_pct: float = NEAR_ENTRY_PCT):
    """The proximity reading for one evaluated row, or None when the row is
    not a candidate at all.

    `price` overrides the row's own (stale) price — the caller passes a live
    quote. Returns a dict even when the gap is too wide, so the caller can
    tell "not close" from "not eligible"; `within` is the flag that matters.
    """
    plan = result.get("sizing_plan") or {}
    entry = (plan.get("entry") or {})
    stop = (plan.get("stop") or {})
    # An unsized plan has no trade to place, and an at-market entry is not a
    # level price can approach.
    if not plan.get("ok") or entry.get("at_market"):
        return None
    # Only a verdict the engine is actually offering. See (3) above: without
    # this the alert fires on WAIT names sitting on their nearest support,
    # which is a standing property rather than an event.
    if not str(result.get("action") or "").startswith("BUY"):
        return None
    if not entry.get("price") or not stop.get("price"):
        return None

    live = price if price is not None else result.get("price")
    gap = _gap_pct(live, entry["price"])
    if gap is None:
        return None
    return {
        "ticker": result.get("ticker"),
        "price": round(float(live), 2),
        "entry": entry["price"],
        "entry_type": entry.get("type"),
        "level_name": entry.get("level_name"),
        "stop": stop["price"],
        "gap_pct": round(gap, 2),
        "within": abs(gap) <= within_pct,
        "sizing": plan.get("sizing") or {},
        "target": plan.get("target") or {},
        "action": result.get("action"),
        "grade": plan.get("grade"),
    }


def candidates(results) -> list[str]:
    """Tickers worth fetching a live price for — those carrying a resting
    order. Keeps the quote fetch proportional to the plans that exist rather
    than to the size of the library."""
    return [n["ticker"] for n in
            (near_entry(r, price=r.get("price")) for r in results)
            if n and n.get("ticker")]


def live_prices(tickers: list[str]) -> dict:
    """{ticker: last price}. Missing symbols are simply absent — a quote
    that could not be fetched must not become an alert computed against a
    stale price."""
    out: dict = {}
    if not tickers:
        return out
    try:
        import yfinance as yf
    except ImportError:
        return out
    for ticker in tickers:
        try:
            last = yf.Ticker(ticker).fast_info["last_price"]
            if last:
                out[ticker] = float(last)
        except Exception:
            continue
    return out


def _alert(n: dict) -> dict:
    z, t = n["sizing"], n["target"]
    side = "above" if n["gap_pct"] >= 0 else "below"
    size_bit = (f"{z['shares']:,} shares (${z['position_value']:,.0f}, "
                f"${z['actual_risk']:,.0f} risk)" if z.get("shares") else
                "size withheld")
    rr_bit = (f" · {t['rr']:.1f}R to ${t['price']:,.2f}"
              if t.get("rr") is not None else "")
    level = n.get("level_name") or n.get("entry_type") or "the level"
    return A.make_alert(
        dedup_key=f"{CATEGORY}:{n['ticker']}",
        category=CATEGORY,
        ticker=n["ticker"],
        priority="HIGH",
        headline=(f"{n['ticker']} ${n['price']:,.2f} — "
                  f"{abs(n['gap_pct']):.1f}% {side} the "
                  f"${n['entry']:,.2f} entry"),
        why_it_matters=(f"The planned entry is {level} at ${n['entry']:,.2f}, "
                        f"with the stop at ${n['stop']:,.2f}. Price has "
                        f"reached the level the long-term engine wants to "
                        f"buy at{rr_bit}."),
        expected_impact=(f"A fill here risks ${n['stop']:,.2f} to "
                         f"${n['entry']:,.2f} a share"),
        suggested_action=(f"Work the order at ${n['entry']:,.2f} — "
                          f"{size_bit}. Stop ${n['stop']:,.2f}."),
        confidence=80,
        time_sensitivity="Today — price is at the level now",
        supporting_data={
            "entry": n["entry"], "stop": n["stop"], "price": n["price"],
            "gap_pct": n["gap_pct"], "entry_type": n.get("entry_type"),
            "shares": z.get("shares"), "rr": t.get("rr"),
            "action": n.get("action"), "grade": n.get("grade"),
        })


def scan_for_alerts(results, within_pct: float = NEAR_ENTRY_PCT,
                    prices: dict | None = None) -> list[dict]:
    """Raise an alert for every resting entry price has arrived at.

    `checked_keys` covers every eligible name, not just the ones firing, so
    a name that drifts back out of the band clears its alert and can fire
    again on the next approach — without it the first touch would suppress
    every later one forever.
    """
    eligible = [(r, near_entry(r, price=r.get("price"))) for r in results]
    eligible = [(r, n) for r, n in eligible if n]
    if prices is None:
        prices = live_prices([n["ticker"] for _r, n in eligible])

    checked_keys, current, readings = set(), [], []
    for result, _stale in eligible:
        ticker = result.get("ticker")
        # No live quote means no opinion — neither an alert nor a clear.
        if ticker not in prices:
            continue
        n = near_entry(result, price=prices[ticker], within_pct=within_pct)
        if not n:
            continue
        checked_keys.add(f"{CATEGORY}:{ticker}")
        if n["within"]:
            current.append(_alert(n))
            readings.append(n)

    new_alerts = A.raise_alerts(current, checked_keys)
    if new_alerts:
        fired = {a["ticker"] for a in new_alerts}
        send_entry_email([n for n in readings if n["ticker"] in fired])
    return new_alerts


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL — its own subject, deliberately outside the general digest
# ─────────────────────────────────────────────────────────────────────────────

def send_entry_email(readings: list[dict]) -> bool:
    """One mail listing every entry reached this cycle.

    Sent from here rather than through alerts.send_alert_emails() because
    the subject has to be stable and specific — this is a standing list of
    orders to work, and burying it in a digest headed "3 alerts: Earnings 1 ·
    Swing Trades 2" makes it unfilterable. alerts.SELF_EMAILING_CATEGORIES
    holds the other half of that arrangement, so nothing goes out twice.
    """
    if not readings:
        return False
    from stockanalysis.scanners.market_movers import send_resend_email

    readings = sorted(readings, key=lambda n: abs(n["gap_pct"]))
    lines, rows = [], []
    for n in readings:
        z, t = n["sizing"], n["target"]
        shares = f"{z['shares']:,} sh" if z.get("shares") else "—"
        rr = f"{t['rr']:.1f}R" if t.get("rr") is not None else "—"
        lines += [
            f"{n['ticker']}  ${n['price']:,.2f} — {n['gap_pct']:+.2f}% vs entry "
            f"${n['entry']:,.2f} ({n.get('level_name') or n.get('entry_type')})",
            f"    stop ${n['stop']:,.2f} · {shares} · "
            f"${z.get('actual_risk', 0):,.0f} risk · {rr} · {n.get('action')}",
            "",
        ]
        rows.append(
            f'<tr>'
            f'<td style="padding:6px 10px;font-weight:700">{n["ticker"]}</td>'
            f'<td style="padding:6px 10px">${n["price"]:,.2f}</td>'
            f'<td style="padding:6px 10px">${n["entry"]:,.2f}'
            f'<div style="font-size:11px;color:#898781">'
            f'{n.get("level_name") or n.get("entry_type") or ""}</div></td>'
            f'<td style="padding:6px 10px;color:'
            f'{"#0F6E56" if abs(n["gap_pct"]) <= 0.5 else "#8a6d1a"}">'
            f'{n["gap_pct"]:+.2f}%</td>'
            f'<td style="padding:6px 10px">${n["stop"]:,.2f}</td>'
            f'<td style="padding:6px 10px">{shares}</td>'
            f'<td style="padding:6px 10px">{rr}</td>'
            f'<td style="padding:6px 10px;font-size:11px">{n.get("action") or ""}</td>'
            f'</tr>')

    n_txt = f"{len(readings)} entry level(s) reached"
    text_body = "\n".join([n_txt, ""] + lines)
    html_body = (
        f'<div style="font-family:sans-serif;max-width:760px">'
        f'<h2 style="font-size:16px">{n_txt}</h2>'
        f'<p style="font-size:12px;color:#52514e">Price is within '
        f'{NEAR_ENTRY_PCT:g}% of a level the Long-Term Buy Engine planned to '
        f'buy at. Sizes are risk-based against the configured account.</p>'
        f'<table style="border-collapse:collapse;font-size:12px;width:100%">'
        f'<tr style="text-align:left;color:#898781;font-size:11px;'
        f'text-transform:uppercase">'
        f'<th style="padding:6px 10px">Ticker</th>'
        f'<th style="padding:6px 10px">Price</th>'
        f'<th style="padding:6px 10px">Entry</th>'
        f'<th style="padding:6px 10px">Gap</th>'
        f'<th style="padding:6px 10px">Stop</th>'
        f'<th style="padding:6px 10px">Size</th>'
        f'<th style="padding:6px 10px">R:R</th>'
        f'<th style="padding:6px 10px">Action</th></tr>'
        + "".join(rows) + '</table></div>')
    return send_resend_email(SUBJECT, text_body, html_body)
