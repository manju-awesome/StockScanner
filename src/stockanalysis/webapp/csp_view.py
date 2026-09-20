"""
csp_view.py
===========
The Cash-Secured Put page. Presentation only — every score, level, greek
and verdict comes from core.csp and is read back out of the snapshot
core.csp.store wrote. Nothing here recomputes a number, so each expanded
row can claim to show "why" and be right.

Why a page and not a section of /longterm
------------------------------------------
The company verdict is /longterm's; this page does not second-guess it,
it consumes it. What it adds is an options layer — chains, greeks,
volatility context, strike selection and assignment analysis — which has
its own scan (minutes of network), its own snapshot, its own staleness
rules and about twenty columns of its own. Folding that into /longterm
would have buried a slow options scan inside a fast equity page and left
both harder to read.

What the layout is arguing
--------------------------
The table leads with the COMPANY (quality, valuation, margin of safety)
and only then the CONTRACT (strike, delta, premium, yield). That order is
the thesis: the objective is not premium, it is buying a good company at
a good effective cost basis while being paid to wait. A layout that led
with yield would be quietly recommending the opposite.

Annualised return is shown in muted type next to the period yield rather
than as the headline number, for the same reason. It is a comparison aid
across different DTEs, not a return anyone actually earns.
"""

from __future__ import annotations

from urllib.parse import urlencode

from stockanalysis.core.csp import store as csp_store

from .views import badge, card, empty, esc, fmt_money, fmt_pct, tv_url

DASH = "—"

_FINAL_STATUS = {"SELL": "good", "SELL_DIP": "good", "VERIFY": "watch",
                 "EVENT_RISK": "watch", "WAIT_IV": "watch",
                 "WAIT_LEVEL": "info", "WATCH": "info", "REJECT": "bad"}
_ELIG_STATUS = {"CSP APPROVED": "good", "CSP WATCHLIST": "watch",
                "CSP REJECTED": "bad"}
_BAND_STATUS = {"UNDERVALUED": "good", "FAIR": "watch", "OVERVALUED": "bad"}


def _n(v, nd=2, dash=DASH):
    return dash if v is None else f"{v:,.{nd}f}"


def _sub(text, colour="#898781"):
    return (f'<div style="font-size:10px;color:{colour};margin-top:2px">'
            f'{esc(text)}</div>')


def _ticker_cell(row, rescan: bool = True):
    """Ticker, its sector, and a one-click rescan.

    The rescan is a NAMED scan of this ticker alone, which is the strongest
    form the engine offers: it re-quotes spot live, re-fetches the chain,
    keeps the full audit instead of slimming the row, and attaches the
    reference chain even when the company gate says no. One name is one
    chain, so it returns in seconds rather than the minutes a full scan
    costs — which is what makes a per-row button reasonable at all.

    It merges into the stored snapshot rather than replacing it, so
    rescanning one name never disturbs the other six hundred.
    """
    tk = row.get("ticker") or "?"
    btn = ""
    if rescan:
        btn = (f'<button type="button" class="csp-rescan" '
               f'data-ticker="{esc(tk)}" '
               f'onclick="cspRescan(this);event.stopPropagation()" '
               f'title="Re-scan {esc(tk)} now — live price, fresh chain, '
               f'full audit kept" '
               f'style="margin-left:5px;border:1px solid #d9d7ce;'
               f'background:white;color:#185FA5;border-radius:5px;'
               f'font-size:10px;line-height:1;padding:2px 5px;'
               f'cursor:pointer;vertical-align:1px">⟳</button>')
    return (f'<a href="{tv_url(tk)}" target="_blank" '
            f'style="font-weight:700;color:#0b0b0b;text-decoration:none">'
            f'{esc(tk)}</a>{btn}'
            + _sub(row.get("sector") or ""))


_ACTION_STATUS = {"BUY_NOW": "good", "SELL_CSP": "good",
                  "WAIT_FOR_BETTER_STRIKE": "watch",
                  "WAIT_FOR_BUY_ZONE": "watch",
                  "THESIS_CHECK": "watch", "AVOID": "bad"}


def _action_rank(row):
    """Sort key for the decision column — best action first."""
    from stockanalysis.core.csp import disposition as D
    return D.ACTION_RANK.get(D.compute(row)["final_action"])


def _entry_gap(row):
    from stockanalysis.core.csp import disposition as D
    return D.compute(row)["entry_vs_zone_pct"]


def _decision_cell(row):
    """The capital-allocation verdict, with the sentence that earned it.

    This is the column the page was missing. "REJECT" answered whether the
    contract was sellable and stopped; this answers what to do about the
    company — wait for the price, wait for a better strike, check the
    thesis, buy the shares outright, or genuinely avoid.
    """
    from stockanalysis.core.csp import disposition as D
    d = D.compute(row)
    score = d["ownership_score"]
    return (badge(d["action_label"], _ACTION_STATUS.get(d["final_action"],
                                                        "muted"))
            + _sub(d["why"][:96])
            + (_sub(f'ownership {score}/100') if score is not None else ""))


def _entry_cell(row):
    """Effective entry, and where it lands against the buy zone.

    Strike minus credit — the only price on the row you would actually end
    up owning at. Green when it lands at or below the zone, which is the
    single best test on the page for a cash-secured put.
    """
    from stockanalysis.core.csp import disposition as D
    d = D.compute(row)
    entry, gap = d["effective_entry"], d["entry_vs_zone_pct"]
    if entry is None:
        return f'<span style="color:#b5b3ad">{DASH}</span>'
    if gap is None:
        return f'<b>${_n(entry)}</b>' + _sub("no zone to measure against")
    colour = "#0F6E56" if gap <= 0 else "#8a6d1a" if gap <= 5 else "#A32D2D"
    return (f'<b>${_n(entry)}</b>'
            + _sub(f'{gap:+.1f}% vs zone top', colour))


def _buy_zone_cell(row, strike=None):
    """The long-term engine's buy zone, and whether this trade lands in it.

    Green when the STRIKE sits inside the zone: assignment would put you in
    at a price the company engine would buy at, which is the strongest
    thing a cash-secured put can claim. Amber when the price is in the zone
    but the strike is below it — still the right neighbourhood. Grey
    otherwise, with the gap, because a zone 29% below spot is worth seeing
    and is not a reason to do anything today.
    """
    z = row.get("buy_zone") or {}
    low, high = z.get("low"), z.get("high")
    if not low or not high:
        return f'<span style="color:#b5b3ad">{DASH}</span>'

    spot = row.get("price")
    price_in = spot is not None and low <= spot <= high
    # A band the price has already fallen THROUGH is overhead supply, not a
    # level to be assigned at — every strike is under it, so saying "strike
    # 21% under the zone" would be trivially true and would read as the
    # best row on the page. INTU is the case: down 51.6%, through its 200
    # MA, and the band sat 28% above spot.
    if z.get("above_spot"):
        return (f'<span style="color:#898781;white-space:nowrap">'
                f'${_n(low, 0)}–${_n(high, 0)}</span>'
                + _sub(f'{esc(z.get("label") or "")} · price is below the '
                       f'whole band', "#8a6d1a"))
    # Below the zone beats inside it: assignment puts you in cheaper than
    # the price the long-term engine would pay. Both are green; the note
    # says which, because they are different-sized wins.
    if strike is not None and strike < low:
        colour = "#0F6E56"
        note = f"strike {(1 - strike / low) * 100:.1f}% under the zone"
    elif strike is not None and strike <= high:
        colour, note = "#0F6E56", "strike is inside"
    elif price_in:
        colour, note = "#8a6d1a", "price is inside"
    else:
        gap = z.get("distance_pct")
        colour = "#898781"
        note = (f'{gap:+.1f}%' if gap is not None else "")
    strike_in = strike is not None and strike <= high
    kind = z.get("kind")
    label = z.get("label") or ""
    # An investment zone and a technical band are different claims and the
    # column must not let them look alike — the same rule /longterm's own
    # Buy Zone column follows.
    if kind != "investment" and label:
        label += " · technical"
    return (f'<span style="color:{colour};font-weight:'
            f'{"700" if strike_in else "500"};white-space:nowrap">'
            f'${_n(low, 0)}–${_n(high, 0)}</span>'
            + _sub(" · ".join(x for x in (label, note) if x)))


def _price_cell(row):
    """Spot, and whether it is a live quote or the last scan's.

    The whole options layer is a function of this number — the OTM filter,
    every greek, the expected move, the basis discount — so "is this
    current" is not a footnote. When the scan could not get a quote the
    cell says so rather than showing a stale figure that looks identical
    to a fresh one.

    The drift against the stored price is shown when it is large enough to
    matter, because it also tells you the research library needs a scan:
    the quality and valuation columns on this same row were computed from
    the OLD number and this engine does not recompute them.
    """
    px = row.get("price")
    if px is None:
        return DASH
    if row.get("spot_source") != "live":
        return (f"${_n(px)}"
                + _sub("last scan — not a live quote", "#8a6d1a"))
    drift = row.get("spot_drift_pct")
    if drift is None or abs(drift) < 1.0:
        return f"${_n(px)}" + _sub("live")
    # Past a percent the company columns beside it are describing a
    # materially different price from the one the options were quoted at.
    return (f"${_n(px)}"
            + _sub(f"live · {drift:+.1f}% vs library ${_n(row.get('spot_stored'))}"
                   f" — rescan research", "#8a6d1a"))


def _score_cell(v, out_of=100):
    if v is None:
        return f'<span style="color:#b5b3ad">{DASH}</span>'
    colour = ("#0F6E56" if v >= 70 else "#8a6d1a" if v >= 55 else "#A32D2D")
    return (f'<span style="font-weight:700;color:{colour}">{v:.0f}</span>'
            f'<span style="font-size:10px;color:#b5b3ad">/{out_of}</span>')


# ─────────────────────────────────────────────────────────────────────────
# DETAIL — the audit behind one row
# ─────────────────────────────────────────────────────────────────────────

def _levels_block(row):
    levels = row.get("levels") or []
    anchor = ((row.get("selection") or {}).get("anchor") or {})
    if not levels:
        return _sub("no support level below spot")

    out = []
    for i, lv in enumerate(levels, 1):
        is_anchor = lv.get("price") == anchor.get("price")
        mark = " ← strike anchored here" if is_anchor else ""
        notes = ", ".join(lv.get("notes") or []) or "—"
        out.append(
            f'<div style="display:flex;justify-content:space-between;gap:8px;'
            f'padding:3px 0;font-size:11px'
            f'{";font-weight:600" if is_anchor else ""}">'
            f'<span>S{i} · {esc(lv.get("name") or "")}'
            f'<span style="color:#898781"> {esc(notes)}</span>'
            f'<span style="color:#0F6E56">{esc(mark)}</span></span>'
            f'<span>${_n(lv.get("price"))} '
            f'<span style="color:#898781">({_n(lv.get("distance_pct"), 1)}%)</span> '
            f'<span style="color:#b5b3ad">conf {lv.get("confidence")}</span>'
            f'</span></div>')
    return "".join(out)


def _conditions_block(chosen):
    fit = (chosen or {}).get("fit") or {}
    out = []
    for c in fit.get("conditions") or []:
        ok = c.get("ok")
        mark, colour = (("✓", "#0F6E56") if ok else
                        ("✗", "#A32D2D") if ok is False else ("·", "#898781"))
        out.append(f'<div style="font-size:11px;padding:2px 0;color:{colour}">'
                   f'{mark} <span style="color:#0b0b0b">{esc(c.get("text") or "")}'
                   f'</span></div>')
    return "".join(out) or _sub("no strike conditions evaluated")


def _assignment_block(row):
    a = row.get("assignment") or {}
    if not a.get("tests"):
        return _sub(a.get("reason") or "not assessed")

    verdict = a.get("happy_to_own")
    text, status = (("YES — this is a price worth owning", "good")
                    if verdict is True else
                    ("NO — the basis is not a price worth owning", "bad")
                    if verdict is False else
                    ("CANNOT TELL — missing valuation or level data", "watch"))

    tests = "".join(
        f'<div style="font-size:11px;padding:2px 0">'
        f'<span style="color:{"#0F6E56" if t.get("ok") else "#A32D2D"}">'
        f'{"✓" if t.get("ok") else "✗"}</span> {esc(t.get("text") or "")}</div>'
        for t in a["tests"])

    return (f'<div style="margin-bottom:6px">{badge(text, status)}</div>'
            f'<div style="font-size:11px;color:#898781;margin-bottom:4px">'
            f'"If assigned tomorrow, would I be happy owning 100 shares at '
            f'${_n(a.get("basis"))}?"</div>{tests}')


def _ladder_block(row):
    """The delta ladder — the choice shown against its alternatives."""
    sel = row.get("selection") or {}
    considered = sel.get("considered") or []
    if not considered:
        return ""
    chosen_k = (row.get("chosen") or {}).get("strike")
    lo, hi = sel.get("band") or (None, None)

    head = ("<tr style='font-size:10px;color:#898781;text-align:right'>"
            "<th style='text-align:left'>Strike</th><th>Δ</th><th>Bid</th>"
            "<th>Limit</th><th>Yield</th><th>Ann.</th><th>OI</th>"
            "<th>Liq</th></tr>")
    body = []
    for c in considered:
        is_chosen = c.get("strike") == chosen_k
        d = abs(c.get("delta") or 0)
        in_band = lo is not None and lo <= d <= hi
        bg = "#F1F7F4" if is_chosen else "transparent"
        colour = "#0b0b0b" if in_band else "#b5b3ad"
        body.append(
            f"<tr style='background:{bg};color:{colour};text-align:right;"
            f"font-size:11px'>"
            f"<td style='text-align:left;font-weight:{700 if is_chosen else 400}'>"
            f"${_n(c.get('strike'))}{' ←' if is_chosen else ''}</td>"
            f"<td>{_n(c.get('delta'), 3)}</td>"
            f"<td>{_n(c.get('bid'))}</td>"
            f"<td>{_n(c.get('limit_price'))}</td>"
            f"<td>{_n(c.get('yield_pct'), 2)}%</td>"
            f"<td>{_n(c.get('annualised'), 1)}%</td>"
            f"<td>{_n(c.get('open_interest'), 0)}</td>"
            f"<td>{c.get('liquidity') if c.get('liquidity') is not None else DASH}</td>"
            f"</tr>"
            f"<tr style='background:{bg}'><td colspan='8' "
            f"style='font-size:10px;color:#898781;padding:0 0 4px'>"
            f"{esc(c.get('note') or '')}</td></tr>")
    return (f"<table style='width:100%;border-collapse:collapse'>{head}"
            f"{''.join(body)}</table>"
            + _sub(f"greyed rows sit outside the {lo:.2f}-{hi:.2f} delta band "
                   f"this name qualifies for" if lo is not None else ""))


def _score_block(row):
    d = row.get("score_detail") or {}
    comps = d.get("components") or []
    if not comps:
        return _sub("not scored")
    rows_html = []
    last_group = None
    for c in comps:
        sc = c.get("score")
        if c.get("group") != last_group:
            last_group = c.get("group")
            totals = (d.get(last_group) or {}).get("score")
            rows_html.append(
                f'<div style="font-size:10px;font-weight:700;color:#0b0b0b;'
                f'margin:6px 0 2px;text-transform:uppercase">'
                f'{esc(last_group or "")} — {totals if totals is not None else "—"}'
                f'/100</div>')
        bar = "" if sc is None else (
            f'<div style="height:4px;background:#eceae4;border-radius:2px;'
            f'width:70px;display:inline-block;vertical-align:middle">'
            f'<div style="height:4px;border-radius:2px;width:{sc}%;'
            f'background:{"#0F6E56" if sc >= 70 else "#8a6d1a" if sc >= 45 else "#A32D2D"}">'
            f'</div></div>')
        rows_html.append(
            f'<div style="display:flex;justify-content:space-between;gap:8px;'
            f'font-size:11px;padding:2px 0">'
            f'<span>{esc(c["name"].title())} '
            f'<span style="color:#b5b3ad">{c["weight"]}%</span></span>'
            f'<span>{bar} '
            f'{"<span style=\'color:#b5b3ad\'>not available</span>" if sc is None else sc}'
            f'</span></div>')
    cov = d.get("coverage")
    note = ("" if cov == 100 else
            _sub(f"score built on {cov}% of the weights — the rest were "
                 f"unavailable and are excluded, not scored zero", "#8a6d1a"))
    return "".join(rows_html) + note



def _paywall_block(row):
    """Is the option worth selling — the hurdle, and what cleared it."""
    req, adq = row.get("required") or {}, row.get("adequacy") or {}
    eff = row.get("efficiency") or {}
    ret = row.get("returns") or {}
    if not req:
        return _sub("not assessed")

    out = [f'<div style="font-size:11px;line-height:1.7">'
           f'{esc(req.get("detail") or "")}</div>']
    if adq.get("ratio") is not None:
        r = adq["ratio"]
        status = "good" if r >= 1.15 else "watch" if r >= 1.0 else "bad"
        out.append(
            f'<div style="margin:6px 0">'
            f'{badge(esc(adq.get("label") or ""), status)}</div>'
            f'<div style="font-size:11px">Pays {_n(ret.get("yield_pct"), 2)}% '
            f'against a {_n(req.get("period_pct"), 2)}% requirement — '
            f'<b>{r:.2f}×</b></div>')
        if adq.get("shortfall_pct"):
            out.append(_sub(f"short by {adq['shortfall_pct']:.2f} percentage "
                            f"points of yield", "#A32D2D"))
    if req.get("binding") == "static floor":
        out.append(_sub("the flat minimum is binding here, not the "
                        "risk-based hurdle"))
    if eff.get("label"):
        out.append(f'<div style="font-size:11px;margin-top:6px">'
                   f'{esc(eff["label"])} — {esc(eff.get("detail") or "")}</div>')
        if eff.get("edge_dollars") is not None:
            out.append(_sub(f"edge over cash: ${eff['edge_dollars']:,.0f} "
                            f"for the period"))
    return "".join(out)


def _downside_block(row):
    """Premium measured against how far the stock actually moves."""
    d = row.get("downside") or {}
    mos = row.get("margin_at_assignment") or {}
    out = []
    if d.get("per_atr") is not None:
        out.append(f'<div style="font-size:11px">Premium is '
                   f'<b>{d["per_atr"]:.2f}×</b> one ATR '
                   f'(${_n(row.get("atr"))})</div>')
    if d.get("per_expected_move") is not None:
        out.append(f'<div style="font-size:11px">Premium covers '
                   f'<b>{d["per_expected_move"]*100:.0f}%</b> of a '
                   f'one-sigma move</div>')
    if d.get("technical_cushion_pct") is not None:
        out.append(f'<div style="font-size:11px">Basis sits '
                   f'<b>{d["technical_cushion_pct"]:.1f}%</b> above '
                   f'{esc(d.get("cushion_level") or "the next level")}</div>')
    if mos.get("pct") is not None:
        unit = "pp" if mos.get("basis_kind") == "growth" else "%"
        out.append(f'<div style="font-size:11px;margin-top:4px">Margin of '
                   f'safety at assignment: <b>{mos["pct"]:+.1f}{unit}</b></div>'
                   + _sub(mos.get("detail") or ""))
    return "".join(out) or _sub("no downside measures available")


def _ideal_block(row):
    """What this name WOULD be worth selling — shown even with no trade."""
    z = row.get("ideal_zone") or {}
    if not z:
        return _sub("not computed")
    lo, hi = z.get("strike_low"), z.get("strike_high")
    tl, th = z.get("target_delta") or (None, None)
    rows = [
        ("Ideal strike", f"${_n(lo)} – ${_n(hi)}" if hi else DASH),
        ("Ideal expiry", f"{z.get('dte_low')}–{z.get('dte_high')} DTE"),
        ("Minimum premium", f"${_n(z.get('min_premium'))}"),
        ("Ideal premium", f"${_n(z.get('ideal_premium'))}"),
        ("Max spread", f"{_n(z.get('max_spread_pct'), 0)}% of mid"),
        ("Target delta", f"{tl:.2f}–{th:.2f}" if tl else DASH),
        ("Target IV/realised", f"≥ {_n(z.get('target_iv_rv'), 2)}×"),
        ("Required yield", f"{_n(z.get('min_required_yield_pct'), 2)}%"),
    ]
    return "".join(
        f'<div style="display:flex;justify-content:space-between;gap:8px;'
        f'font-size:11px;padding:2px 0"><span style="color:#898781">'
        f'{esc(k)}</span><span>{v}</span></div>' for k, v in rows)


def _reference_block(row):
    """The chain for a name with no qualifying contract.

    Carries no verdict on purpose. The engine already said REJECT or
    "no strike qualifies"; this answers the different question of what
    the market is actually paying, which the verdict alone does not.
    Everything here is labelled reference so it cannot be mistaken for
    the recommendation it deliberately is not.
    """
    ref = row.get("reference") or {}
    if not ref:
        return ""
    if not ref.get("available"):
        return _sub(ref.get("why") or "chain not available")

    best = ref.get("best") or {}
    ratio = (ref.get("iv_vs_hv") or {}).get("ratio")

    head = (f'<div style="margin-bottom:6px">'
            f'{badge("REFERENCE ONLY — not a recommendation", "bad")}</div>'
            f'<div style="font-size:11px;color:#898781;margin-bottom:6px">'
            f'{esc(ref.get("why") or "")}</div>'
            f'<div style="font-size:11px">Expiry {esc(ref.get("expiry"))} '
            f'({ref.get("dte")}d) · {len(ref.get("strikes") or [])} OTM '
            f'strikes, {ref.get("fillable", 0)} fillable'
            + (f' · IV/realised {ratio:.2f}×' if ratio else '')
            + '</div>')

    if best:
        head += (
            f'<div style="margin-top:8px;padding:8px;background:#fff;'
            f'border:1px solid #eceae4;border-radius:8px;font-size:12px;'
            f'line-height:1.7">'
            f'<b>Best fillable premium</b><br>'
            f'${_n(best.get("strike"))} put · limit <b>${_n(best.get("limit_price"))}</b> '
            f'· {_n(best.get("yield_pct"), 2)}% '
            f'<span style="color:#898781">({_n(best.get("annualised"), 1)}% ann.)</span><br>'
            f'Δ {_n(best.get("delta"), 3)} '
            f'<span style="color:#898781">{esc(best.get("delta_class") or "")}</span> '
            f'· breakeven ${_n(best.get("breakeven"))} '
            f'({_n(best.get("basis_vs_spot_pct"), 1)}% vs spot)<br>'
            f'liquidity {best.get("liquidity")}/100</div>'
            + _sub("the richest premium is always the strike closest to the "
                   "money — read the delta class beside it, not the yield "
                   "alone"))
    else:
        head += _sub("no strike on this expiry is fillable — every quote is "
                     "either bidless or too wide to sell into", "#8a6d1a")

    rows_html = []
    for c in ref.get("strikes") or []:
        tradable = c.get("liquidity_tradable")
        colour = ("#b5b3ad" if tradable is False else "#0b0b0b")
        is_best = best and c.get("strike") == best.get("strike")
        bg = "#F1F7F4" if is_best else "transparent"
        rows_html.append(
            f"<tr style='background:{bg};color:{colour};text-align:right;"
            f"font-size:11px'>"
            f"<td style='text-align:left;font-weight:{700 if is_best else 400}'>"
            f"${_n(c.get('strike'))}{' ←' if is_best else ''}</td>"
            f"<td>{_n(c.get('delta'), 3)}</td>"
            f"<td style='text-align:left;color:#898781'>"
            f"{esc(c.get('delta_class') or '')}</td>"
            f"<td>{_n(c.get('bid'))}</td>"
            f"<td>{_n(c.get('ask'))}</td>"
            f"<td>{_n(c.get('limit_price'))}</td>"
            f"<td>{_n(c.get('yield_pct'), 2)}%</td>"
            f"<td>{_n(c.get('annualised'), 1)}%</td>"
            f"<td>${_n(c.get('breakeven'))}</td>"
            f"<td>{_n(c.get('spread_pct'), 1)}%</td>"
            f"<td>{_n(c.get('open_interest'), 0)}</td>"
            f"<td>{c.get('liquidity') if c.get('liquidity') is not None else DASH}</td>"
            f"</tr>")

    head += (
        f'<div style="overflow-x:auto;margin-top:8px">'
        f'<table style="width:100%;border-collapse:collapse">'
        f"<tr style='font-size:10px;color:#898781;text-align:right'>"
        f"<th style='text-align:left'>Strike</th><th>Δ</th>"
        f"<th style='text-align:left'>Class</th><th>Bid</th><th>Ask</th>"
        f"<th>Limit</th><th>Yield</th><th>Ann.</th><th>Breakeven</th>"
        f"<th>Spread</th><th>OI</th><th>Liq</th></tr>"
        f'{"".join(rows_html)}</table></div>'
        + _sub("greyed rows are not fillable — bidless, or quoted too wide "
               "to sell into"))
    return head


def _score_or_dash(v):
    return DASH if v is None else f"{v:.0f}"


def _requirements_block(row):
    """What would have to change, as numbers.

    The most important panel on the page for anything that is not a
    SELL. "Wait for IV expansion" is not actionable; "IV/realised 0.86x
    -> 1.10x, or premium $0.95 -> $1.72" is a condition you can set an
    alert on.
    """
    rq = row.get("requirements") or {}
    reqs = rq.get("requirements") or []
    if not reqs:
        return _sub("no outstanding conditions")

    out = []
    if rq.get("summary"):
        out.append(f'<div style="font-size:12px;font-weight:600;'
                   f'margin-bottom:6px">{esc(rq["summary"])}</div>')
    for r in reqs:
        met = r.get("met")
        mark, colour = (("✓", "#0F6E56") if met else
                        ("○", "#8a6d1a" if r.get("kind") == "ALTERNATIVE"
                         else "#A32D2D"))
        tag = ("" if r.get("kind") != "BLOCKING" else
               f' {badge("blocking", "bad", "small")}')
        out.append(
            f'<div style="padding:3px 0;font-size:11px">'
            f'<span style="color:{colour};font-weight:700">{mark}</span> '
            f'<b>{esc(r.get("field") or "")}</b>{tag}<br>'
            f'<span style="color:#898781">now {esc(r.get("current") or "—")} '
            f'→ needs {esc(r.get("needs") or "—")}</span>'
            + (_sub(str(r.get("detail"))) if r.get("detail") else "")
            + '</div>')
    alts = [r for r in reqs if r.get("kind") == "ALTERNATIVE"]
    if len(alts) > 1:
        out.append(_sub("alternatives — any one of these is enough"))
    return "".join(out)


def _risk_block(row):
    """The third factor, and which leg is weakest."""
    rk = row.get("risk") or {}
    if not rk.get("components"):
        return _sub("risk not scored")
    ed = row.get("earnings_distance") or {}
    mc = row.get("move_cushion") or {}
    dt = row.get("dte_fit") or {}
    aq = row.get("assignment_quality") or {}

    lines = [f'<div style="font-size:11px;line-height:1.7">'
             f'Earnings {esc(ed.get("band") or "—")}'
             + (f' <span style="color:#898781">({ed["ratio"]:.2f}× of the '
                f'contract)</span>' if ed.get("ratio") else '') + '<br>'
             f'Cushion {esc(mc.get("band") or "—")}'
             + (f' <span style="color:#898781">{mc["ratio"]:.2f}× the '
                f'expected move</span>' if mc.get("ratio") else '') + '<br>'
             f'DTE {esc(dt.get("band") or "—")} '
             f'<span style="color:#898781">{esc(dt.get("detail") or "")}</span>'
             '<br>'
             f'Assignment quality <b>{_score_or_dash(aq.get("score"))}</b>'
             f'/100</div>']
    for c in rk["components"]:
        sc = c.get("score")
        bar = "" if sc is None else (
            f'<div style="height:4px;background:#eceae4;border-radius:2px;'
            f'width:60px;display:inline-block;vertical-align:middle">'
            f'<div style="height:4px;border-radius:2px;width:{sc}%;'
            f'background:{"#0F6E56" if sc >= 70 else "#8a6d1a" if sc >= 45 else "#A32D2D"}">'
            f'</div></div>')
        lines.append(
            f'<div style="display:flex;justify-content:space-between;gap:8px;'
            f'font-size:11px;padding:2px 0">'
            f'<span>{esc(c["name"].title())} '
            f'<span style="color:#b5b3ad">{c["weight"]}%</span></span>'
            f'<span>{bar} {"—" if sc is None else sc}</span></div>')
    lines.append(_sub(rk.get("detail") or ""))
    return "".join(lines)


def _detail(row):
    chosen = row.get("chosen") or {}
    elig = row.get("eligibility") or {}
    disc = row.get("discount") or {}
    ret = row.get("returns") or {}

    def panel(title, body):
        return (f'<div style="flex:1;min-width:260px">'
                f'<div style="font-size:10px;font-weight:700;color:#898781;'
                f'text-transform:uppercase;letter-spacing:.04em;'
                f'margin-bottom:4px">{esc(title)}</div>{body}</div>')

    # Why the company qualifies.
    why = "".join(f'<div style="font-size:11px;padding:2px 0">✓ {esc(r)}</div>'
                  for r in elig.get("reasons") or [])
    for b in elig.get("blockers") or []:
        why += (f'<div style="font-size:11px;padding:2px 0;color:#A32D2D">'
                f'✗ {esc(b)}</div>')
    for sft in elig.get("softs") or []:
        why += (f'<div style="font-size:11px;padding:2px 0;color:#8a6d1a">'
                f'! {esc(sft)}</div>')
    why += _sub(f"{esc(disc.get('method') or '')} · {esc(disc.get('detail') or '')}")

    # Volatility.
    ivr, ratio = row.get("iv_rank") or {}, row.get("iv_vs_hv") or {}
    op = row.get("iv_opportunity") or {}
    if ivr.get("available"):
        vol_html = (f'<div style="font-size:11px">IV Rank '
                    f'<b>{_n(ivr.get("rank"), 0)}</b> over '
                    f'{ivr.get("observations")} days '
                    f'({_n(ivr.get("low"), 0)}%–{_n(ivr.get("high"), 0)}%)</div>')
    else:
        vol_html = (f'<div style="font-size:11px;color:#8a6d1a">IV Rank '
                    f'unavailable — {esc(ivr.get("reason") or "")}</div>')
    if ratio.get("ratio") is not None:
        vol_html += (f'<div style="font-size:11px;margin-top:3px">'
                     f'{esc(ratio.get("label") or "")} — IV {_n(ratio.get("iv"), 1)}% '
                     f'vs realised {_n(ratio.get("hv"), 1)}% '
                     f'({ratio["ratio"]:.2f}×)</div>')
    vol_html += _sub(f"scored on: {esc(op.get('source') or 'nothing available')}")

    # The contract.
    if chosen:
        contract = (
            f'<div style="font-size:12px;line-height:1.7">'
            f'<b>Sell 1 {esc(row.get("ticker"))} {esc(row.get("expiry") or "")} '
            f'${_n(chosen.get("strike"), 2)} PUT</b><br>'
            f'Limit <b>${_n(chosen.get("limit_price"))}</b> '
            f'<span style="color:#898781">(bid {_n(chosen.get("bid"))} / '
            f'ask {_n(chosen.get("ask"))}, mid {_n(chosen.get("mid"))})</span><br>'
            f'Collateral ${_n(ret.get("collateral"), 0)} · '
            f'credit ${_n(ret.get("premium_total"), 0)}<br>'
            f'Breakeven <b>${_n(chosen.get("breakeven"))}</b> '
            f'({_n(chosen.get("breakeven_pct"), 1)}% vs spot)<br>'
            f'Δ {_n(chosen.get("delta"), 3)} · θ {_n(chosen.get("theta"), 3)}/day · '
            f'vega {_n(chosen.get("vega"), 3)} · γ {_n(chosen.get("gamma"), 4)}<br>'
            f'Model P(profit) {_n((chosen.get("prob_profit") or 0) * 100, 1)}% · '
            f'P(assigned) {_n((chosen.get("prob_itm") or 0) * 100, 1)}%<br>'
            f'Expected move ±${_n(chosen.get("expected_move"))} over '
            f'{chosen.get("dte")}d</div>')
        liq_notes = chosen.get("liquidity_notes") or []
        if liq_notes:
            contract += _sub("liquidity: " + "; ".join(liq_notes), "#8a6d1a")
    else:
        contract = _sub(row.get("no_trade_reason") or "no contract selected")

    skipped = row.get("skipped_expiries") or []
    if skipped:
        contract += "".join(
            _sub(f"skipped {s.get('expiry')}: {s.get('why')}", "#8a6d1a")
            for s in skipped)

    panels = (panel("Why this company", why)
              + panel("Support levels", _levels_block(row))
              + panel("Why this strike", _conditions_block(chosen))
              + panel("Is the option worth selling?", _paywall_block(row))
              + panel("Volatility", vol_html)
              + panel("Premium vs downside", _downside_block(row))
              + panel("The contract", contract)
              + panel("If assigned", _assignment_block(row))
              + panel("What needs to change", _requirements_block(row))
              + panel("Risk", _risk_block(row))
              + panel("Ideal CSP zone", _ideal_block(row))
              + panel("Score breakdown", _score_block(row))
              + panel("Strikes considered", _ladder_block(row)))

    ref = _reference_block(row)
    ref_html = (f'<div style="flex:1 1 100%;border-top:1px solid #eceae4;'
                f'padding-top:10px;margin-top:2px">'
                f'<div style="font-size:10px;font-weight:700;color:#898781;'
                f'text-transform:uppercase;letter-spacing:.04em;'
                f'margin-bottom:4px">What the chain is paying anyway</div>'
                f'{ref}</div>') if ref else ""

    return (f'<div style="display:flex;flex-wrap:wrap;gap:18px;padding:12px 14px;'
            f'background:#faf9f7;border-top:1px solid #eceae4">'
            f'{panels}{ref_html}</div>')


# ─────────────────────────────────────────────────────────────────────────
# TABLE
# ─────────────────────────────────────────────────────────────────────────

# Company columns first, contract columns second, the two scores side by
# side. The split is the argument: an excellent stock and a mediocre
# option should be legible as exactly that at a glance.
_COLUMNS = ("Ticker", "Decision", "Eff. entry", "Quality", "Valuation",
            "Margin", "Price", "Buy zone", "Strike", "Δ", "Class",
            "Expiry", "DTE", "ER",
            "Limit", "Yield", "vs req.", "IV/RV", "Spread", "Basis",
            "Earnings", "Cushion", "STOCK", "OPTION", "RISK", "CSP",
            "Action")

# What each column sorts ON, positionally against _COLUMNS above. The keys
# are core.csp.screen's flattened scalars, NOT re-derived here: the same
# number the rule engine filters on is the one the header sorts by, so
# "adequacy >= 1.5" and a click on "vs req." can never disagree.
#
# None means the column is not sortable — Class and Expiry are labels whose
# useful order is already carried by Δ and DTE beside them.
_SORT_KEYS = ("ticker", "final_action", "entry_vs_zone_pct", "lquality",
              "valuation_band", "margin_pct", "csp_price", "buy_zone_gap",
              "strike", "delta", None, None, "dte",
              "earnings_days", "credit", "yield_pct", "adequacy",
              "iv_vs_hv", "spread_pct", "basis", "earnings_days",
              "cushion", "stock_score", "option_score", "risk_score",
              "csp_score", "action")

# Ranked so a first click on a text column puts the best at the top —
# otherwise sorting by Action leads with the rejections.
_ACTION_RANK = {"SELL": 7, "VERIFY": 6, "SELL_DIP": 5, "EVENT_RISK": 4,
                "WAIT_IV": 3, "WAIT_LEVEL": 2, "WATCH": 1, "REJECT": 0}
_BAND_RANK = {"UNDERVALUED": 2, "FAIR": 1, "OVERVALUED": 0}


def _sort_values(row) -> list:
    """One sort value per column, or None where the column is not sorted."""
    from stockanalysis.core.csp import screen as CS
    flat = CS.flatten(row)
    out = []
    for key in _SORT_KEYS:
        if key is None:
            out.append(None)
            continue
        v = flat.get(key)
        if key == "action":
            v = _ACTION_RANK.get(v)
        elif key == "valuation_band":
            v = _BAND_RANK.get(v)
        out.append(v)
    return out


def _row(row, idx):
    chosen = row.get("chosen") or {}
    elig = row.get("eligibility") or {}
    disc = row.get("discount") or {}
    ret = row.get("returns") or {}
    final = row.get("final") or {}
    adq = row.get("adequacy") or {}
    assign = row.get("assignment") or {}

    tk = row.get("ticker") or "?"
    band = elig.get("valuation_band")
    dte_v = chosen.get("dte")
    er = row.get("days_to_earnings")

    margin = disc.get("margin_pct")
    margin_txt = (DASH if margin is None else
                  f"{margin:+.1f}{'pp' if disc.get('basis') == 'growth' else '%'}")

    ratio = (row.get("iv_vs_hv") or {}).get("ratio")
    iv_txt = DASH if ratio is None else (
        f'<span style="color:'
        f'{"#0F6E56" if ratio >= 1.0 else "#8a6d1a" if ratio >= 0.85 else "#A32D2D"}">'
        f'{ratio:.2f}×</span>')

    # Adequacy is the option's headline: yield against the hurdle it had
    # to clear, not the yield on its own.
    adq_txt = DASH if adq.get("ratio") is None else (
        f'<span style="font-weight:600;color:'
        f'{"#0F6E56" if adq["ratio"] >= 1.15 else "#8a6d1a" if adq["ratio"] >= 1.0 else "#A32D2D"}">'
        f'{adq["ratio"]:.2f}×</span>'
        + _sub(f"need {_n((row.get('required') or {}).get('period_pct'), 2)}%"))

    sp = chosen.get("spread_pct")
    sv = chosen.get("spread_verdict")
    sp_colour = {"EXCELLENT": "#0F6E56", "ACCEPTABLE": "#0F6E56",
                 "CAUTION": "#8a6d1a", "REJECT": "#A32D2D",
                 "UNKNOWN": "#898781"}.get(sv, "#898781")
    sp_txt = (DASH if sp is None else
              f'<span style="color:{sp_colour}">{sp:.1f}%</span>'
              + (_sub("closed — unverified") if sv == "UNKNOWN" else ""))

    er_txt = (DASH if er is None else
              f"<span style='color:{'#A32D2D' if er <= (dte_v or 0) else '#0b0b0b'}'>"
              f"{er:.0f}d</span>")

    cells = [
        _ticker_cell(row),
        _decision_cell(row),
        _entry_cell(row),
        _score_cell(elig.get("quality_score")) + _sub(elig.get("quality_tier") or ""),
        badge(band or DASH, _BAND_STATUS.get(band, "muted")),
        margin_txt,
        _price_cell(row),
        _buy_zone_cell(row, chosen.get("strike")),
        (f"<b>${_n(chosen.get('strike'))}</b>" if chosen else DASH),
        _n(chosen.get("delta"), 3),
        _sub(chosen.get("delta_class") or "") if chosen else DASH,
        esc(row.get("expiry") or DASH),
        str(dte_v) if dte_v is not None else DASH,
        er_txt,
        (f"<b>${_n(chosen.get('limit_price'))}</b>" if chosen else DASH),
        ((f"{_n(ret.get('yield_pct'), 2)}%"
          + _sub(f"{_n(ret.get('annualised'), 1)}% ann.")) if ret.get("yield_pct")
         is not None else DASH),
        adq_txt,
        iv_txt,
        sp_txt,
        (f"${_n(assign.get('basis'))}" if assign.get("basis") else DASH),
        (esc((row.get("earnings_distance") or {}).get("band") or DASH)
         + _sub(f"{(row.get('earnings_distance') or {}).get('days') or '—'}d")),
        ((f"{(row.get('move_cushion') or {}).get('ratio'):.2f}×"
          + _sub((row.get("move_cushion") or {}).get("band") or ""))
         if (row.get("move_cushion") or {}).get("ratio") is not None else DASH),
        _score_cell(row.get("stock_score")),
        _score_cell(row.get("option_score")),
        _score_cell(row.get("risk_score")),
        _score_cell(row.get("csp_score")),
        badge(final.get("action") or DASH,
              _FINAL_STATUS.get(final.get("key"), "muted")),
    ]

    sorts = _sort_values(row)
    tds = "".join(
        f'<td data-sort="{"" if v is None else esc(str(v))}" '
        f'style="padding:7px 8px;vertical-align:top;font-size:12px">{c}</td>'
        for c, v in zip(cells, sorts))

    head = row.get("headline")
    why = esc(final.get("why") or "")
    need = (row.get("requirements") or {}).get("summary")
    summary = ((f'<b style="color:#0b0b0b">{esc(head)}</b> — ' if head else "")
               + why
               + (f' <b style="color:#8a6d1a">· {esc(need)}</b>' if need
                  else ""))
    return (
        f'<tr data-main="1" onclick="cspToggle({idx})" style="cursor:pointer;'
        f'border-top:1px solid #eceae4">{tds}</tr>'
        f'<tr><td colspan="{len(_COLUMNS)}" style="padding:0 8px 6px;'
        f'font-size:11px;color:#898781">{summary}</td></tr>'
        f'<tr id="csp-d-{idx}" style="display:none"><td colspan="{len(_COLUMNS)}" '
        f'style="padding:0">{_detail(row)}</td></tr>')


# (label, alignment). "Category" is a column rather than a section heading
# so that grouping is one click on a sortable table instead of five
# separate tables you cannot compare across.
_PRICED_REJECT_COLUMNS = ("Ticker", "Decision", "Eff. entry", "Category",
                          "Quality", "Rejected on",
                          "Price", "Off 52w high", "Buy zone",
                          "Best fillable put", "Δ", "Credit",
                          "Period", "Annualised", "vs req.", "If assigned",
                          "Liquidity")


def _priced_rejects(rows):
    """Rejected names whose chain was fetched anyway, with the premiums.

    The point of the whole block. A rejection with every option column
    blank tells you the engine said no; it does not tell you what the
    market is paying, and for a name like CRDO — 98/100 Elite, rejected
    purely on price — those are different questions and only the reader
    can answer the second.

    What has NOT changed: the verdict. These rows are still REJECT, they
    are still outside the ranked Opportunities list, and nothing in this
    table can move them into it. Showing a premium and acting on it are
    separate decisions, and this page only does the first.
    """
    priced = [r for r in rows
              if (r.get("reference") or {}).get("available")
              and ((r.get("reference") or {}).get("best"))]
    if not priced:
        return ""

    # ONE sortable table rather than a table per category. The categories
    # are still first-class — they are a column, and sorting on it groups
    # the table in one click — but "which rejected names pay the most" is a
    # question across all of them, and five separate tables made it a
    # manual comparison between five sorted lists.
    #
    # Classified with the SAME classifier the grouped list below and the
    # engine's budget allocator use, so the counts there and the rows here
    # can never describe different sets.
    from stockanalysis.core.csp import screen as CS

    # Server-side order is best business first; from there the reader
    # sorts. It is the useful default because the whole reason to look at a
    # rejection's premium is that the company is good and something else
    # failed.
    priced.sort(key=lambda r: -(r.get("lquality") or 0))
    counts: dict[str, int] = {}
    for r in priced:
        b = CS.reject_bucket((r.get("final") or {}).get("why") or "")
        r["_bucket"] = b
        counts[b] = counts.get(b, 0) + 1

    def cell(html, align="left", sort=None):
        return (f'<td data-sort="{"" if sort is None else esc(str(sort))}" '
                f'style="padding:6px 8px;font-size:12px;'
                f'vertical-align:top;text-align:{align}">{html}</td>')

    def row_html(r):
        ref = r["reference"]
        b = ref["best"]
        why = (r.get("final") or {}).get("why") or ""
        lq, tier = r.get("lquality"), r.get("lq_tier")
        spot = r.get("price")
        be = b.get("breakeven")
        return (
            # data-main marks a sortable unit. Every row here is its own
            # unit (no detail panel travels with it), but the attribute is
            # what cspSortGroups() collects on — without it the group list
            # came back empty and sorting was a silent no-op on every
            # column. The table only LOOKED sorted by Quality because the
            # server already orders it best-business-first.
            f'<tr data-main="1" style="border-top:1px solid #eceae4">'
            + cell(_ticker_cell(r), sort=r["ticker"])
            + cell(_decision_cell(r), sort=_action_rank(r))
            + cell(_entry_cell(r), "right", sort=_entry_gap(r))
            + cell(f'<span style="font-size:11px">{esc(r["_bucket"])}</span>',
                   sort=r["_bucket"])
            + cell((_score_cell(lq) if lq is not None
                    else f'<span style="color:#b5b3ad">{DASH}</span>')
                   + _sub(tier or ""), "left", sort=lq)
            # The rejection travels with the premium, always. A table of
            # rich yields on rejected names without the reason beside each
            # one is exactly the reading this engine is built to refuse.
            + cell(f'<span style="font-size:11px;color:#791F1F">'
                   f'{esc(why[:120])}</span>', sort=why[:40])
            + cell(_price_cell(r), "right", sort=spot)
            # How far the stock has already fallen. Half of whether the
            # strike below it is a price worth being assigned at.
            + cell((f'{_n(r.get("dist_52w_high"), 1)}%'
                    if r.get("dist_52w_high") is not None
                    else f'<span style="color:#b5b3ad">{DASH}</span>'),
                   "right", sort=r.get("dist_52w_high"))
            + cell(_buy_zone_cell(r, b.get("strike")), "right",
                   sort=(r.get("buy_zone") or {}).get("distance_pct"))
            + cell(f'<b>${_n(b.get("strike"))}</b>'
                   + _sub(f'{_n(b.get("distance_pct"), 1)}% OTM · '
                          f'{esc(b.get("expiry") or "")} '
                          f'({b.get("dte")}d)'), "right",
                   sort=b.get("strike"))
            + cell(f'{_n(abs(b.get("delta") or 0), 2)}'
                   + _sub(str(b.get("delta_class") or "")), "right",
                   sort=abs(b.get("delta") or 0) or None)
            + cell(f'<b>${_n(b.get("limit_price"))}</b>'
                   + _sub(f'${_n((b.get("limit_price") or 0) * 100, 0)} '
                          f'/ contract'), "right",
                   sort=b.get("limit_price"))
            + cell(f'{_n(b.get("yield_pct"), 2)}%'
                   + _sub(f'on ${_n((b.get("strike") or 0) * 100, 0)}'),
                   "right", sort=b.get("yield_pct"))
            + cell(f'<b style="color:#8a6d1a">{_n(b.get("annualised"), 1)}%'
                   f'</b>' + _sub("comparison only"), "right",
                   sort=b.get("annualised"))
            # Computed against the same hurdle a chosen contract faces, so
            # "rich" means one thing across the whole page.
            + cell((f'<b>{_n(b.get("adequacy"), 2)}×</b>'
                    + _sub(f'need {_n(b.get("required_pct"), 2)}%'))
                   if b.get("adequacy") is not None
                   else f'<span style="color:#b5b3ad">{DASH}</span>',
                   "right", sort=b.get("adequacy"))
            + cell(f'${_n(be)}'
                   + _sub(f'{_n(b.get("basis_vs_spot_pct"), 1)}% vs spot'),
                   "right", sort=b.get("basis_vs_spot_pct"))
            + cell(_score_cell(b.get("liquidity"))
                   + _sub(esc(b.get("spread_verdict") or "")), "right",
                   sort=b.get("liquidity"))
            + '</tr>')

    head = ""
    for i, c in enumerate(_PRICED_REJECT_COLUMNS):
        head += (
            f'<th data-idx="{i}" data-dir="" class="csp-sort" '
            f'title="Click to sort · shift-click to add a second key" '
            f'style="padding:6px 8px;text-align:left;font-size:10px;'
            f'color:#898781;text-transform:uppercase;letter-spacing:.04em;'
            f'cursor:pointer">{esc(c)}<span class="csp-arrow"></span></th>')

    tables = (
        f'<div id="csp-rejects-sort-note" style="display:none;font-size:11px;'
        f'color:#898781;margin:4px 0 2px"></div>'
        f'<div style="overflow-x:auto"><table id="csp-rejects" '
        f'data-sortable="1" style="width:100%;border-collapse:collapse">'
        f'<thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(row_html(r) for r in priced)}</tbody>'
        f'</table></div>')

    # The category counts, kept as a line rather than as section headings:
    # the table sorts on Category, so the grouping is a click, and a count
    # here still says at a glance which buckets the budget reached.
    tally = " · ".join(
        f'{esc(b)} <b>{counts[b]}</b>'
        for b in CS.REJECT_BUCKETS if counts.get(b))

    stale = ""
    if any(not (r["reference"].get("quotes_live")) for r in priced):
        stale = _sub("some of these were quoted with the market closed — "
                     "last-trade artifacts, not fillable prices", "#A32D2D")

    return (
        f'<div style="margin-bottom:14px">'
        f'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;'
        f'margin-bottom:6px">'
        f'<span style="font-size:12px;font-weight:700">'
        f'Rejected, priced anyway ({len(priced)})</span>'
        f'{badge("REFERENCE ONLY — not recommendations", "bad")}</div>'
        f'<div style="font-size:11px;color:#898781;line-height:1.6;'
        f'margin-bottom:8px">'
        f'These were rejected — some on the company, some because no strike '
        f'qualified once the chain came back — and nothing about that has '
        f'moved: none is ranked, scored or offered as a trade. What they '
        f'now carry is the best <b>fillable</b> put on the nearest '
        f'qualifying expiry, so a name turned down on price rather than on '
        f'the business can be judged on what the market actually pays. The '
        f'reason each was rejected sits beside its premium on purpose: that '
        f'pairing is the decision, and either half alone is misleading.<br>'
        f'Sort any column — <b>Annualised</b> or <b>Period</b> for the '
        f'richest premiums, <b>Category</b> to group by the rule that '
        f'fired. The scan spreads its budget across those categories '
        f'rather than down the quality ranking, so a '
        f'category showing fewer names than its count below is a budget '
        f'that did not reach it, not a category with no premiums. Raise '
        f'<b>Rejects priced</b> above to cover more.</div>'
        f'<div style="font-size:11px;color:#898781;margin-bottom:4px">'
        f'{tally}</div>'
        f'{tables}{stale}</div>')


def _rejected_block(rows):
    """Rejections as a compact list, grouped by the rule that fired.

    These are rendered separately from the main table rather than as
    more rows in it. On a typical run they outnumber the candidates
    fifty to one, and giving each a full audit panel produced a
    multi-megabyte page that buried the handful of names worth reading.

    They are still shown, because "what did the engine throw out, and on
    what rule" is most of the value on a day when nothing qualifies —
    just shown at the density that volume deserves.
    """
    if not rows:
        return ""

    # Priced rejections lead: they are the ones there is anything to read.
    # The grouped name-only list below is still the right density for the
    # several hundred that failed on the business itself.
    priced = _priced_rejects(rows)

    # The shared classifier, not a local copy. The engine spends its
    # reference-chain budget across these same buckets, and two
    # definitions would mean the budget was allocated to groups the page
    # then drew differently — with no way to tell from the screen.
    from stockanalysis.core.csp import screen as CS

    groups = {}
    for r in rows:
        groups.setdefault(
            CS.reject_bucket((r.get("final") or {}).get("why") or ""),
            []).append(r)

    blocks = []
    for name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        tickers = ", ".join(
            f'<span title="{esc((i.get("final") or {}).get("why") or "")}">'
            f'{esc(i.get("ticker") or "?")}</span>'
            for i in sorted(items, key=lambda x: x.get("ticker") or ""))
        blocks.append(
            f'<details style="margin-bottom:6px">'
            f'<summary style="cursor:pointer;font-size:12px;font-weight:600">'
            f'{esc(name)} <span style="color:#898781;font-weight:400">'
            f'({len(items)})</span></summary>'
            f'<div style="font-size:11px;color:#898781;line-height:1.9;'
            f'padding:6px 0 2px">{tickers}</div></details>')

    return (priced + "".join(blocks)
            + _sub("hover a ticker for the exact rule that rejected it"
                   + ("" if priced else
                      " · tick “Price rejects” above, or type a ticker and "
                      "press Run, to see what the market pays on one of "
                      "these anyway")))


def _table(rows, start=0, table_id="csp-table"):
    """The opportunity table. Headers sort; the detail row travels with its
    summary row so a sort never separates a name from its own reasoning."""
    if not rows:
        return empty("No candidates in this snapshot.")
    head = ""
    for i, c in enumerate(_COLUMNS):
        sortable = _SORT_KEYS[i] is not None
        head += (
            f'<th data-idx="{i}" data-dir=""'
            + (' class="csp-sort"' if sortable else "")
            + f' title="{"Click to sort · shift-click to add a second key"
                        if sortable else "Not sortable"}"'
            + f' style="padding:6px 8px;text-align:left;font-size:10px;'
              f'color:#898781;text-transform:uppercase;letter-spacing:.04em'
            + (';cursor:pointer' if sortable else '')
            + f'">{esc(c)}<span class="csp-arrow"></span></th>')
    body = "".join(_row(r, start + i) for i, r in enumerate(rows))
    return (SORT_CSS
            + f'<div style="overflow-x:auto"><table id="{esc(table_id)}" '
            f'data-sortable="1" '
            f'style="width:100%;border-collapse:collapse">'
            f'<thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


SORT_CSS = """
<style>
table[data-sortable] th.csp-sort:hover { color: #0b0b0b; }
table[data-sortable] .csp-arrow {
  font-size: 9px; margin-left: 3px; color: #185FA5;
}
</style>"""


# ─────────────────────────────────────────────────────────────────────────
# ETF SECTION — the same options question, without a company to judge
# ─────────────────────────────────────────────────────────────────────────
# A separate block rather than rows in the table above, because the table
# above leads with quality, valuation and margin of safety and a fund has
# none of the three. Slotting ETFs in with those cells blank would make an
# absence of data look like a poor score; giving them their own block with
# their own columns lets each answer the question it can actually answer.
#
# See core/csp/etf.py for why the company gate is not merely skipped here.

_ETF_STATUS = {"SELL": "good", "THIN": "watch", "ILLIQUID": "watch",
               "STRUCTURE": "bad", "NO_CONTRACT": "muted"}

_ETF_COLUMNS = ("Fund", "Structure", "Expiry", "Strike", "OTM", "Δ",
                "Credit", "Period", "Annualised", "If assigned", "Liquidity",
                "Verdict")


def _etf_structure_cell(st):
    """Trend and stage, or an honest blank.

    A fund with no scan row reads UNMEASURED rather than inheriting the
    technicals' defaults — with an empty row compute_pullback reports
    AT_HIGHS, because nothing is below a price when nothing is known, and
    printing that would be a claim invented from no data.
    """
    st = st or {}
    if not st.get("measured"):
        return (f'<span style="color:#b5b3ad">unmeasured</span>'
                + _sub(st.get("note") or "no scan row"))
    stage = str(st.get("stage") or "").replace("_", " ").title()
    conf = st.get("confluence")
    bits = [x for x in (stage, (f'{conf} of {st.get("confluence_of")} support'
                                if conf is not None else "")) if x]
    return (f'{st.get("trend_icon") or ""} '
            f'<span style="font-weight:600">{esc(st.get("trend_state") or "—")}'
            f'</span>' + _sub(" · ".join(bits)))


def _etf_row(row, idx):
    c = row.get("contract") or {}
    final = row.get("final") or {}
    tone = _ETF_STATUS.get(final.get("key"), "muted")

    def cell(html, align="left"):
        return (f'<td style="padding:6px 8px;font-size:12px;'
                f'vertical-align:top;text-align:{align}">{html}</td>')

    aum = row.get("aum")
    aum_txt = (f'${aum / 1e9:,.1f}B AUM' if aum and aum >= 1e9
               else f'${aum / 1e6:,.0f}M AUM' if aum else "")
    exp = row.get("expense_ratio")
    ctx = " · ".join(x for x in (row.get("category") or "", aum_txt,
                                 (f'{exp:.2f}% fee' if exp else "")) if x)

    if not c:
        return (
            f'<tr style="border-top:1px solid #eceae4">'
            + cell(f'<a href="{tv_url(row["ticker"])}" target="_blank" '
                   f'style="font-weight:700;color:#0b0b0b;'
                   f'text-decoration:none">{esc(row["ticker"])}</a>'
                   + _sub(ctx))
            + cell(_etf_structure_cell(row.get("structure")))
            + f'<td colspan="9" style="padding:6px 8px;font-size:12px;'
              f'color:#898781">{esc(final.get("why") or "")}</td>'
            + cell(badge(final.get("action") or "—", tone))
            + '</tr>')

    anchor = row.get("anchor") or {}
    under = (f'under {anchor["name"]}' if anchor
             and c["strike"] <= (anchor.get("price") or 0) else "")

    return (
        f'<tr style="border-top:1px solid #eceae4">'
        + cell(f'<a href="{tv_url(row["ticker"])}" target="_blank" '
               f'style="font-weight:700;color:#0b0b0b;text-decoration:none">'
               f'{esc(row["ticker"])}</a>' + _sub(ctx))
        + cell(_etf_structure_cell(row.get("structure")))
        + cell(f'{esc(row.get("expiry") or "—")}'
               + _sub(f'{row.get("dte")}d' if row.get("dte") else ""))
        + cell(f'<b>${_n(c["strike"])}</b>'
               + _sub(f'spot ${_n(row.get("price"))}'
                      + ("" if row.get("spot_source") == "live"
                         else " (last scan)")), "right")
        + cell(f'{_n(c["otm_pct"], 1)}%'
               + _sub(under or (f'{_n(c["prob_otm"], 0)}% model P(OTM)'
                                if c.get("prob_otm") is not None else "")),
               "right")
        + cell(f'{_n(c["delta"], 2)}'
               + _sub(str(c.get("delta_class") or "")), "right")
        + cell(f'<b>${_n(c["credit"])}</b>'
               + _sub(f'${_n(c["premium"], 0)} / contract'), "right")
        + cell(f'{_n(c["period_pct"], 2)}%'
               + _sub(f'on ${_n(c["collateral"], 0)}'), "right")
        # Annualised is the ranking number and the most over-read one on
        # the page: it is a comparison aid across DTEs, not a return
        # anyone collects twelve times.
        + cell(f'<b style="color:#0F6E56">{_n(c["annualised_pct"], 1)}%</b>'
               + _sub("comparison only"), "right")
        # The only number here describing what you would actually own —
        # assignment leaves you long at the strike less the credit, which
        # is the whole reason a CSP is a limit order you are paid to place.
        + cell(f'${_n(c["effective_basis"])}'
               + _sub(f'{_n(c["basis_discount_pct"], 1)}% below spot'), "right")
        + cell(_score_cell(c.get("liquidity"))
               + _sub(f'OI {c.get("open_interest") or 0:,.0f}'
                      + (f' · {_n(c.get("spread_pct"), 1)}% wide'
                         if c.get("spread_pct") is not None else "")), "right")
        + cell(badge(final.get("action") or "—", tone)
               + _sub(final.get("why") or ""))
        + '</tr>')


def _etf_overlap_block(shared):
    """What the set holds in common. A portfolio fact, not a row's."""
    if not shared:
        return ""
    def line(o):
        funds = ", ".join(f'{x["ticker"]} {x["weight"]:.1f}%'
                          for x in o["funds"])
        return (f'<tr style="border-top:1px solid #eceae4;font-size:12px">'
                f'<td style="padding:4px 8px"><b>{esc(o["holding"])}</b></td>'
                f'<td style="padding:4px 8px;color:#898781">{esc(funds)}</td>'
                f'<td style="padding:4px 8px;text-align:right">'
                f'{len(o["funds"])} funds</td></tr>')

    items = "".join(line(o) for o in shared)
    return (
        f'<div style="margin-top:14px">'
        f'<div style="font-size:11px;font-weight:700;margin-bottom:4px">'
        f'Shared holdings across the sellable funds</div>'
        f'<div style="font-size:10px;color:#898781;margin-bottom:6px">'
        f'Selling puts on two funds that are a third the same six companies '
        f'is one position, not two. This is a property of the SET, which is '
        f'why it is here rather than inside a row — no single contract '
        f'carries the risk it describes.</div>'
        f'<table style="width:100%;border-collapse:collapse">{items}</table>'
        f'</div>')


def _etf_block(etfs, meta):
    """The whole ETF section."""
    if not etfs:
        return empty("No ETF scan in this snapshot — run a scan and the "
                     "funds in the research library will be priced here.")

    from stockanalysis.core.csp import etf as ETF

    counts = {}
    for r in etfs:
        k = (r.get("final") or {}).get("key") or "?"
        counts[k] = counts.get(k, 0) + 1
    pills = "".join(badge(f"{counts.get(k, 0)} {lbl}", st)
                    for k, lbl, st in (("SELL", "sellable", "good"),
                                       ("THIN", "thin premium", "watch"),
                                       ("ILLIQUID", "illiquid", "watch"),
                                       ("STRUCTURE", "structure broken", "bad"),
                                       ("NO_CONTRACT", "no contract", "muted"))
                    if counts.get(k))

    note = (
        f'<div style="font-size:11px;color:#898781;line-height:1.6;'
        f'margin-bottom:10px">'
        f'A fund has no income statement, so there is <b>no quality score, '
        f'no reverse DCF and no margin of safety</b> here, and this section '
        f'makes no claim that any of these are worth owning. What it judges '
        f'is the <b>contract</b>: is it liquid, does the credit pay for the '
        f'collateral, and is the structure under it intact. Those three are '
        f'gates in that order — a fat premium never rescues an unfillable '
        f'quote, and neither rescues a fund below a falling 200 MA.<br><br>'
        f'Targeted about a month out ({ETF.MIN_DTE}–{ETF.MAX_DTE} DTE, '
        f'nearest to {ETF.TARGET_DTE}), strikes read from the '
        f'{ETF.DELTA_LO:.2f}–{ETF.DELTA_HI:.2f} delta band, and ranked by '
        f'annualised yield among contracts that can actually be filled. '
        f'Fund facts — category, AUM, fee — are shown as context and are '
        f'never scored.</div>')

    head = "".join(f'<th style="padding:6px 8px;text-align:left;font-size:10px;'
                   f'color:#898781;text-transform:uppercase;'
                   f'letter-spacing:.04em">{esc(c)}</th>'
                   for c in _ETF_COLUMNS)
    body = "".join(_etf_row(r, i) for i, r in enumerate(etfs))
    table = (f'<div style="overflow-x:auto"><table style="width:100%;'
             f'border-collapse:collapse"><thead><tr>{head}</tr></thead>'
             f'<tbody>{body}</tbody></table></div>')

    return (f'<div style="display:flex;gap:8px;flex-wrap:wrap;'
            f'margin-bottom:8px">{pills}</div>{note}{table}'
            + _etf_overlap_block(meta.get("etf_overlap") or []))


# ─────────────────────────────────────────────────────────────────────────
# PORTFOLIO / CONTROLS / PAGE
# ─────────────────────────────────────────────────────────────────────────

def _portfolio_block(pf):
    if not pf or not pf.get("positions"):
        return empty("No SELL-rated trades to size.")
    rows = "".join(
        f'<tr style="border-top:1px solid #eceae4;font-size:12px">'
        f'<td style="padding:5px 8px"><b>{esc(p["ticker"])}</b></td>'
        f'<td style="padding:5px 8px;color:#898781">{esc(p["sector"])}</td>'
        f'<td style="padding:5px 8px;text-align:right">{p["contracts"]}</td>'
        f'<td style="padding:5px 8px;text-align:right">'
        f'${_n(p["collateral_each"], 0)}</td>'
        f'<td style="padding:5px 8px;text-align:right">'
        f'${_n(p["committed"], 0)}</td>'
        f'<td style="padding:5px 8px;text-align:right">'
        f'{_n(p.get("pct_of_capital"), 1)}%</td>'
        f'<td style="padding:5px 8px;text-align:right;color:#0F6E56">'
        f'${_n(p.get("premium"), 0)}</td></tr>' for p in pf["positions"])

    warn = "".join(f'<div style="margin-top:6px">{badge(esc(w), "bad")}</div>'
                   for w in pf.get("warnings") or [])

    return (
        f'<table style="width:100%;border-collapse:collapse">'
        f'<tr style="font-size:10px;color:#898781;text-transform:uppercase">'
        f'<th style="text-align:left;padding:4px 8px">Ticker</th>'
        f'<th style="text-align:left;padding:4px 8px">Sector</th>'
        f'<th style="text-align:right;padding:4px 8px">Contracts</th>'
        f'<th style="text-align:right;padding:4px 8px">Collateral each</th>'
        f'<th style="text-align:right;padding:4px 8px">Committed</th>'
        f'<th style="text-align:right;padding:4px 8px">% capital</th>'
        f'<th style="text-align:right;padding:4px 8px">Credit</th></tr>'
        f'{rows}</table>'
        f'<div style="margin-top:8px;font-size:12px">'
        f'Total collateral <b>${_n(pf.get("total_collateral"), 0)}</b> of '
        f'${_n(pf.get("capital"), 0)} '
        f'({_n(pf.get("utilisation_pct"), 1)}% utilised) · '
        f'total credit <b style="color:#0F6E56">'
        f'${_n(pf.get("total_premium"), 0)}</b></div>{warn}')


def _watchlist_options(active: str = "") -> str:
    """The saved watchlists, most-used first.

    The same lists the Scanner and /longterm run against — read through
    api.longterm_lists() rather than the file, so a nested AI sublist
    resolves the same way on all three pages instead of this one keeping a
    second interpretation that drifts.
    """
    from . import api
    try:
        lists = api.longterm_lists()
    except Exception as e:                      # a picker is not worth a 500
        print(f"[CSP] watchlists unavailable ({e})")
        return '<option value="">— watchlist —</option>'
    preferred = [n for n in ("watchlist", "daytrade", "Longterm", "AI",
                             "Dividend") if n in lists]
    rest = sorted(n for n in lists if n not in preferred)
    out = ['<option value="">— watchlist —</option>']
    for name in preferred + rest:
        sel = " selected" if name == active else ""
        out.append(f'<option value="{esc(name)}"{sel}>{esc(name)} '
                   f'({len(lists[name])})</option>')
    return "".join(out)


def _controls(meta, typed="", listed=""):
    """The scan form. The ticker box does double duty.

    Typing names filters the stored snapshot instantly — no network, no
    job — because most of the time the question is "what did the last
    scan say about NEM" and re-scanning to answer it is absurd. Pressing
    Run with names in the box scans only those, which is the answer to
    the other question: "the snapshot is stale / this name was never
    scanned, go look now."
    """
    return (
        f'<form onsubmit="return cspRun(event)" '
        f'style="display:flex;gap:10px;align-items:end;flex-wrap:wrap">'
        f'<label style="font-size:11px;color:#898781;flex:1;min-width:240px">'
        f'Tickers<br>'
        f'<input name="tickers" value="{esc(typed)}" '
        f'placeholder="NEM, RMD — blank scans the whole library" '
        f'style="width:100%;padding:6px 9px;border:1px solid #d9d7ce;'
        f'border-radius:6px;font-size:13px"></label>'
        # The picker does the same double duty the ticker box does:
        # "Filter" narrows the STORED snapshot to the list with no network,
        # "Run" scans exactly that list. Typed tickers win over it, because
        # typing a name is the more specific instruction.
        f'<label style="font-size:11px;color:#898781" '
        f'title="Filter the stored scan to this list, or Run to scan just '
        f'it. Typed tickers take precedence.">Watchlist<br>'
        f'<select name="list" style="padding:6px;max-width:190px;'
        f'border:1px solid #d9d7ce;border-radius:6px;font-size:12px">'
        f'{_watchlist_options(listed)}</select></label>'
        f'<label style="font-size:11px;color:#898781">Min DTE<br>'
        f'<input name="min_dte" type="number" value="{meta.get("min_dte", 20)}" '
        f'style="width:70px;padding:5px;border:1px solid #d9d7ce;'
        f'border-radius:6px"></label>'
        f'<label style="font-size:11px;color:#898781">Max DTE<br>'
        f'<input name="max_dte" type="number" value="{meta.get("max_dte", 45)}" '
        f'style="width:70px;padding:5px;border:1px solid #d9d7ce;'
        f'border-radius:6px"></label>'
        f'<label style="font-size:11px;color:#898781">Max names<br>'
        f'<input name="limit" type="number" value="25" '
        f'style="width:70px;padding:5px;border:1px solid #d9d7ce;'
        f'border-radius:6px"></label>'
        f'<label style="font-size:11px;color:#898781">Target DTE<br>'
        f'<input name="target_dte" type="number" '
        f'value="{meta.get("target_dte", 35)}" '
        f'style="width:70px;padding:5px;border:1px solid #d9d7ce;'
        f'border-radius:6px"></label>'
        # Three states, not a checkbox: "allow earnings" collapsed two
        # different decisions into one bit — see the note under the form.
        f'<label style="font-size:11px;color:#898781">Earnings<br>'
        f'<select name="earnings_policy" style="padding:6px;'
        f'border:1px solid #d9d7ce;border-radius:6px">'
        + "".join(
            f'<option value="{pol}"'
            + (" selected" if (meta.get("earnings_policy") or "AVOID") == pol
               else "")
            + f'>{pol.title()}</option>'
            for pol in ("AVOID", "CONTROLLED", "ACCEPT"))
        + '</select></label>'
        f'<label style="font-size:11px;color:#898781">Min stock<br>'
        f'<input name="min_stock" type="number" '
        f'value="{meta.get("min_stock", 0)}" '
        f'style="width:70px;padding:5px;border:1px solid #d9d7ce;'
        f'border-radius:6px"></label>'
        f'<label style="font-size:11px;color:#898781">Min CSP<br>'
        f'<input name="min_csp" type="number" '
        f'value="{meta.get("min_csp", 0)}" '
        f'style="width:70px;padding:5px;border:1px solid #d9d7ce;'
        f'border-radius:6px"></label>'
        # Not "stop rejecting" — the gate stays, and nothing priced here can
        # turn a REJECT into a SELL. What it changes is that the rejection
        # arrives WITH the numbers instead of with blank columns, so a name
        # like CRDO (98/100 Elite, rejected purely on price) can be weighed
        # on its premium rather than only on the engine's answer.
        f'<label style="font-size:11px;color:#898781;display:flex;gap:5px;'
        f'align-items:center;padding-bottom:6px" '
        f'title="Prices the highest-quality rejections as REFERENCE — the '
        f'verdict stays REJECT and they never enter the ranked list">'
        f'<input type="checkbox" name="reference_rejected" value="1"'
        + (" checked" if meta.get("reference_rejected") else "")
        + f'>Price rejects</label>'
        f'<label style="font-size:11px;color:#898781" '
        f'title="How many rejected names to price. Spread across the '
        f'rejection categories, best business first within each. One '
        f'option chain per name, so 600 is roughly an hour.">'
        f'Rejects priced<br>'
        f'<input name="reference_budget" type="number" min="0" max="600" '
        f'value="{meta.get("reference_budget") or 60}" '
        f'style="width:70px;padding:5px;border:1px solid #d9d7ce;'
        f'border-radius:6px"></label>'
        f'<button type="button" onclick="cspFilter()" class="btn secondary" '
        f'style="padding:7px 14px">Filter</button>'
        f'<button type="submit" class="btn" style="padding:7px 16px">'
        f'Run CSP scan</button>'
        + (f'<a href="/csp" class="btn secondary" style="text-decoration:none;'
           f'padding:7px 14px">Clear</a>' if typed else "")
        + f'<span id="csp-msg" style="font-size:11px;color:#898781"></span>'
        f'</form>'
        + _sub("Earnings: AVOID skips any expiry containing a print; "
               "CONTROLLED shows it with a scoring penalty; ACCEPT waives "
               "the penalty only when quality, delta, liquidity and premium "
               "are all strong enough to be paid for the gap risk — "
               "otherwise it falls back to CONTROLLED rather than a free "
               "pass. Score floors rank first and cut after, so a thin day "
               "returns fewer names rather than worse ones. "
               "Price rejects: fetches the chain for the highest-quality "
               "rejections so you can see what the market pays and judge the "
               "risk yourself — shown as REFERENCE, never ranked, and no "
               "premium found there can turn a rejection into a trade."))


_TABS = (("live", "📋", "Opportunities"),
         ("etf", "🧺", "ETFs"),
         ("rejected", "🚫", "Rejected"))


def _tab_bar(active, n_live, n_etf, n_rejected, n_filtered, link):
    """Three lists, three tabs — and the counts are the point.

    They answer different questions: what to sell, what the funds pay, and
    what was turned down and what it pays anyway. Stacked, the last sat
    below two summary cards and a hundred rows.

    Every tab is an ANCHOR carrying the page's whole state, so a view is a
    link and the back button works; the JS below upgrades the click to an
    instant swap without a round trip. Without JS it still navigates, which
    is why they are links rather than buttons.
    """
    counts = {"live": n_live, "etf": n_etf, "rejected": n_rejected}
    active = active if active in counts else "live"
    out = []
    for key, icon, label in _TABS:
        on = key == active
        bg, fg, bd = (("#0b0b0b", "white", "#0b0b0b") if on
                      else ("white", "#444441", "#d9d7ce"))
        out.append(
            f'<a href="{esc(link(tab="" if key == "live" else key))}" '
            f'data-tab="{key}" onclick="return cspTab(event, \'{key}\')" '
            f'style="display:inline-flex;gap:6px;align-items:center;'
            f'background:{bg};color:{fg};border:1px solid {bd};'
            f'font-size:12px;font-weight:600;padding:7px 14px;'
            f'border-radius:8px;text-decoration:none;white-space:nowrap">'
            f'{icon} {esc(label)}'
            f'<span style="opacity:.65;font-weight:400">'
            f'{counts[key]}</span></a>')
    # Said once, here, rather than repeated on each tab: the rules narrow
    # every one of them, and a reader who applied a filter on the
    # Opportunities tab needs to know the Rejected tab moved too.
    note = ""
    if n_filtered:
        note = (f'<span style="font-size:11px;color:#8a6d1a;'
                f'margin-left:auto">filters hid {n_filtered} row(s) across '
                f'all three tabs</span>')
    return (f'<div style="display:flex;gap:8px;flex-wrap:wrap;'
            f'align-items:center;margin:0 0 14px">{"".join(out)}{note}</div>')


# The thresholds people actually type, as one row of number boxes.
#
# The full rule builder above can express all of these and more, but it is
# a three-control sequence (field, operator, value) sitting above the tabs,
# and "quality over 90 paying over 100% annualised" is two numbers. This is
# the same rule engine reached in one gesture — every box writes a rule
# into the same query string, so a quick filter and a built rule are the
# same object and can be removed from the same pill.
#
# (field key, operator, label, unit, input width, placeholder)
_QUICK_FILTERS = (
    ("lquality", "gte", "Quality ≥", "", 62, "90"),
    ("annualised", "gte", "Annual return ≥", "%", 68, "100"),
    ("adequacy", "gte", "vs required ≥", "×", 62, "1.5"),
    ("dist_52w_high", "lte", "Off 52w high ≤", "%", 68, "-20"),
    ("delta", "lte", "Delta ≤", "", 58, "0.25"),
    # Negative means the strike is at or under the zone top — assignment at
    # or below the price the long-term engine would buy at. Typing 0 is the
    # whole "APP" screen.
    ("strike_vs_zone_pct", "lte", "Strike vs zone ≤", "%", 62, "0"),
)


# The yes/no conditions worth one click, beside the numeric boxes. A
# number box cannot express "strike at or below the zone" without the
# reader knowing the sign convention, and a rule builder can but takes
# three controls — so the conditions people actually combine with a
# threshold get a chip each.
#
# (field, op, value, label, title)
_QUICK_TOGGLES = (
    ("strike_at_or_below_zone", "eq", "true", "🎯 Strike at/below zone",
     "Toggle: keep only names whose strike is at or under the buy zone"),
    ("near_buy_zone", "eq", "true", "📍 Price at/near zone",
     "Price is inside the buy zone, or within 5% above it"),
    ("buy_zone_kind", "eq", "investment", "💠 Investment zone",
     "A zone valuation and quality endorse, not just a support band"),
    ("earnings_inside", "eq", "false", "🗓️ No earnings in window",
     "No print before expiry"),
    ("action", "eq", "REJECT", "🚫 Rejected only",
     "Only names the engine turned down — their premiums are the richest"),
    ("has_contract", "eq", "true", "📄 Has a contract",
     "Excludes names with no priced strike at all"),
)


def _quick_toggles(conds, link):
    """One-click yes/no conditions, composable with the number boxes.

    Links rather than checkboxes: each carries the whole query string with
    that one rule added or removed, so they compose with the numeric row,
    survive without JavaScript, and every combination is a shareable URL.
    """
    active = {(c.field, str(c.value).lower()) for c in (conds or [])}
    out = []
    for field, op, value, label, title in _QUICK_TOGGLES:
        on = (field, value.lower()) in active
        rule = f"{field}:{op}:{value}"
        rest = [_rule_text(c) for c in (conds or [])
                if not (c.field == field
                        and str(c.value).lower() == value.lower())]
        target = rest if on else rest + [rule]
        bg, fg, bd = (("#0C447C", "white", "#0C447C") if on
                      else ("white", "#444441", "#d9d7ce"))
        out.append(
            f'<a href="{esc(link(rule=target))}" title="{esc(title)}" '
            f'style="background:{bg};color:{fg};border:1px solid {bd};'
            f'font-size:11px;font-weight:600;padding:4px 9px;'
            f'border-radius:6px;text-decoration:none;white-space:nowrap">'
            f'{esc(label)}</a>')
    return "".join(out)


def _rule_text(cond) -> str:
    """A condition back to its URL form. Shared with longterm_view's own
    builder — imported rather than reimplemented so a rule written by a
    toggle and one written by the builder are byte-identical, and the
    remove links match."""
    from .longterm_view import _rule_text as fmt
    return fmt(cond)


def _quick_filters(conds, link):
    """One-line numeric filters, prefilled from whatever is already active.

    Prefilling matters: without it, applying a second box would silently
    drop the first, because Apply rewrites all of them at once.

    Matched on FIELD, not on (field, operator). A preset writes
    `lquality:gt:90` and this row writes `lquality:gte:90`; keying on the
    pair would leave the box blank next to an active quality rule, and
    Apply would then add a second rule on the same field. One field, one
    box, one rule — the operator shown in the label is the one Apply uses.
    """
    active = {c.field: c.value for c in (conds or [])}
    boxes = ""
    for key, op, label, unit, width, hint in _QUICK_FILTERS:
        value = active.get(key)
        shown = "" if value is None else (
            f"{value:g}" if isinstance(value, float) else str(value))
        boxes += (
            f'<label style="font-size:11px;color:#898781;display:inline-flex;'
            f'gap:4px;align-items:center;white-space:nowrap">{esc(label)}'
            f'<input class="csp-quick" data-field="{key}" data-op="{op}" '
            f'type="number" step="any" value="{esc(shown)}" '
            f'placeholder="{esc(hint)}" '
            f'onkeydown="if(event.key===\'Enter\'){{cspQuick();return false}}" '
            f'style="width:{width}px;padding:4px 6px;font-size:11px;'
            f'border:1px solid #d9d7ce;border-radius:5px">'
            f'{esc(unit)}</label>')
    return (
        f'<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;'
        f'background:white;border:0.5px solid #e1e0d9;border-radius:10px;'
        f'padding:9px 12px;margin:-6px 0 14px">'
        f'<span style="font-size:10px;text-transform:uppercase;'
        f'letter-spacing:.06em;color:#898781">Quick filter</span>'
        f'{boxes}'
        f'<button type="button" onclick="cspQuick()" class="btn" '
        f'style="padding:5px 12px;font-size:11px">Apply</button>'
        f'<a href="#" onclick="cspQuickClear();return false" '
        f'style="font-size:11px;color:#185FA5">Clear these</a>'
        f'<span style="font-size:10px;color:#898781">'
        f'applies to all three tabs — blank a box to drop that rule</span>'
        f'<div style="flex-basis:100%;height:0"></div>'
        f'<span style="font-size:10px;text-transform:uppercase;'
        f'letter-spacing:.06em;color:#898781">Conditions</span>'
        f'{_quick_toggles(conds, link)}'
        f'</div>')


def _reject_filter_note(conds, before, after):
    """What the active rules did to THIS list.

    The rules narrow the rejected table exactly as they narrow the
    opportunities — it is one row set split by verdict — but that is not
    obvious from a card you reached through a tab, and a reader who does
    not know it will read a filtered list as the whole one.
    """
    if not conds:
        return ""
    from stockanalysis.core.csp import screen as CS
    rules = " · ".join(CS.describe(c) for c in conds)
    return (f'<div style="background:#E6F1FB;border-radius:8px;'
            f'padding:8px 12px;margin-bottom:10px;font-size:11px;'
            f'color:#0C447C">Filtered by <b>{esc(rules)}</b> — '
            f'{after} of {before} rows left, across every tab. '
            f'The premium columns here read the reference chain, so a rule '
            f'like "premium vs required ≥ 1.5×" screens these rows on the '
            f'same measure it screens a live opportunity.</div>')


def _list_note(listed, missing):
    """What the chosen watchlist can and cannot answer from this snapshot.

    Named rather than silently dropped: a 25-name list rendering 19 rows
    with no explanation reads as a bug, and the fix — run the scan with the
    list picked — is only obvious once it is said.
    """
    if not listed:
        return ""
    if not missing:
        return _sub(f"showing the {esc(listed)} list")
    shown_names = ", ".join(missing[:12]) + ("…" if len(missing) > 12 else "")
    return (f'<div style="background:#F1EFE8;border-radius:8px;'
            f'padding:9px 12px;margin-top:8px;font-size:11px;color:#444441">'
            f'<b>{esc(listed)}</b> — {len(missing)} of its tickers are not in '
            f'the last scan: {esc(shown_names)}. Press <b>Run CSP scan</b> '
            f'with the list picked to fetch chains for them; it merges into '
            f'the snapshot rather than replacing it.</div>')


def _lookup_block(typed, wanted, shown, slim_only, missing):
    """What the snapshot can and cannot answer for the typed names."""
    if not wanted:
        return ""
    bits = []
    if shown:
        bits.append(badge(f"{len(shown)} found", "good"))
    if slim_only:
        bits.append(badge(f"{len(slim_only)} rejected — audit not stored",
                          "watch"))
    if missing:
        bits.append(badge(f"{len(missing)} not in the last scan", "bad"))

    notes = []
    if slim_only:
        # Rejected rows are pruned on save, so the detail panel genuinely
        # is not there to render. Say so rather than showing an empty one.
        notes.append(
            f'<b>{esc(", ".join(slim_only))}</b> — the last scan rejected '
            f'these and stored only the rule that fired, not the full '
            f'audit. Press <b>Run CSP scan</b> with them in the box to '
            f're-scan and keep the whole reasoning.')
    if missing:
        notes.append(
            f'<b>{esc(", ".join(missing))}</b> — not in the last scan. '
            f'Either outside the research library, or past the scan\'s '
            f'name cap. Press <b>Run CSP scan</b> to fetch chains for them '
            f'now; it merges into the snapshot rather than replacing it.')

    return (f'<div style="margin-top:8px;display:flex;gap:6px;'
            f'flex-wrap:wrap">{"".join(bits)}</div>'
            + "".join(f'<div style="font-size:11px;color:#898781;'
                      f'margin-top:5px;line-height:1.6">{n}</div>'
                      for n in notes))


def csp_page(query: dict | None = None) -> tuple[str, str]:
    from stockanalysis.webapp.longterm_view import parse_tickers

    snap = csp_store.load()
    meta = snap or {}
    all_rows = (snap or {}).get("rows") or []
    # Stored beside `rows`, not inside them: an ETF row has a different
    # shape (no eligibility, no scores, no margin of safety) and mixing the
    # two would make every consumer of `rows` guess which kind it had.
    etfs = (snap or {}).get("etfs") or []

    query = query or {}
    typed = (query.get("tickers") or [""])[0]
    wanted = parse_tickers(typed)
    listed = (query.get("list") or [""])[0].strip()
    # Normalised HERE, once, rather than at each use. A pasted ?tab=nonsense
    # otherwise hid every pane: the tab bar fell back to Opportunities for
    # highlighting while the panes each compared against the raw value and
    # all three matched none of it, leaving a page with a header and
    # nothing under it.
    tab = (query.get("tab") or [""])[0].strip().lower()
    if tab not in {key for key, _icon, _label in _TABS}:
        tab = "live"
    rule_texts = [r for r in (query.get("rule") or []) if r.strip()]
    rule_op = (query.get("rule_op") or ["AND"])[0].strip().upper() or "AND"

    def link(**params):
        """A URL carrying the page's whole state, so no control silently
        discards another's — the same rule /longterm's link() follows."""
        state = {"tickers": typed.strip(),
                 "list": listed,
                 "tab": "" if tab == "live" else tab,
                 "rule": list(rule_texts),
                 "rule_op": "" if rule_op == "AND" else rule_op}
        for key, value in params.items():
            state[key] = value if isinstance(value, list) else (
                "" if value is None else str(value))
        kept = {k: v for k, v in state.items() if v}
        return "/csp" + (f"?{urlencode(kept, doseq=True)}" if kept else "")

    rows = all_rows
    # A chosen list narrows the snapshot before anything else looks at it,
    # so the verdict pills and the rule counts describe the list rather
    # than the library. Names on the list the last scan never covered are
    # NAMED rather than quietly dropped — the same rule the ticker lookup
    # follows, and the reason a 25-name list showing 19 rows reads as
    # information instead of a bug.
    list_missing = []
    if listed:
        from . import api
        members = (api.longterm_lists() or {}).get(listed) or []
        have = {str(r.get("ticker") or "").upper() for r in all_rows}
        rows = [r for r in all_rows
                if str(r.get("ticker") or "").upper()
                in {str(m).upper() for m in members}]
        list_missing = [m for m in members if str(m).upper() not in have]

    slim_only, missing, shown = [], [], []
    if wanted:
        by_ticker = {str(r.get("ticker") or "").upper(): r for r in rows}
        rows = []
        for tk in wanted:
            r = by_ticker.get(tk)
            if r is None:
                missing.append(tk)
                continue
            rows.append(r)
            # `eligibility` is the marker, NOT `score_detail`. A rejected
            # company never gets a score at all — evaluate() returns
            # before scoring — so score_detail is absent on a FULL
            # rejected row too, and using it reported every stored
            # rejection as "audit not stored" when the audit was there.
            (shown if r.get("eligibility") else slim_only).append(tk)

    # Rules run AFTER the ticker lookup, so a filtered view of a searched
    # set is possible and the verdict pills below count what the rules
    # actually left rather than what the snapshot holds.
    from stockanalysis.core.csp import screen as CS
    preset_counts = CS.preset_counts(rows)
    before_rules = len(rows)
    rows, rule_conds, rule_stats = CS.apply_rules(rows, rule_texts, rule_op)

    age_text, age_status = csp_store.age_note(snap)

    counts = {}
    for r in rows:
        k = (r.get("final") or {}).get("key") or "?"
        counts[k] = counts.get(k, 0) + 1

    pills = "".join(badge(f"{counts.get(k, 0)} {lbl}", st)
                    for k, lbl, st in (
                        ("SELL", "sell", "good"),
                        ("VERIFY", "verify at open", "watch"),
                        ("SELL_DIP", "sell on dip", "good"),
                        ("WAIT_IV", "wait for IV", "watch"),
                        ("WAIT_LEVEL", "wait for level", "watch"),
                        ("WATCH", "watch", "info"),
                        ("REJECT", "reject", "bad")) if counts.get(k))

    eligible = f"{meta.get('eligible', 0)}/{meta.get('universe', 0)} eligible"
    header = (
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
        f'margin-bottom:8px">'
        f'{badge(age_text, age_status)}'
        f'{badge("regime " + str(meta.get("regime") or "?"), "info")}'
        + (badge(f"filtered to {len(rows)} of {len(all_rows)}", "info")
           if (wanted or rule_texts or listed) else badge(eligible, "muted"))
        + f'{pills}</div>')

    thesis = (
        '<div style="font-size:11px;color:#898781;line-height:1.6;'
        'margin-bottom:10px">'
        'The objective is <b>not</b> to maximise premium: it is to buy a '
        'high-quality, undervalued company at an attractive effective cost '
        'basis while being paid to wait. Eligibility is decided on the '
        'company alone, before any option chain is fetched — so a rich '
        'premium can never rescue a name that failed on quality or '
        'valuation. Strikes are anchored to support levels the Long-Term '
        'engine computed, then checked against a delta band; delta is a '
        'constraint here, not the selector. Click any row for the full '
        'audit.<br><br>'
        '<b>STOCK and OPTION are scored separately and multiplied.</b> '
        '"Would I want to own this company?" and "is this put worth '
        'selling today?" are different questions, and a single blended '
        'score lets an excellent answer to the first carry a poor answer '
        'to the second — which is exactly how a great business becomes a '
        'mediocre trade. A product means neither half can rescue the '
        'other.</div>')

    live = [r for r in rows if (r.get("final") or {}).get("key") != "REJECT"]
    rejected = [r for r in rows if (r.get("final") or {}).get("key") == "REJECT"]

    # The filter layer, borrowed wholesale from /longterm rather than
    # reimplemented: same preset pills, same removable rule chips, same
    # field/operator/value form, pointed at core.csp.screen's fields.
    from stockanalysis.webapp import longterm_view as LV
    screens = (
        LV._preset_bar(link, rule_texts, False, preset_counts, mod=CS)
        # No separate pill row: _rule_builder already renders the active
        # rules as removable chips. A second copy here showed every rule
        # twice, and the two came from different call sites — which is how
        # one of them ended up labelled with the raw field key.
        + LV._rule_builder(rule_conds, rule_op, link, rule_stats,
                           len(rows), before_rules, search_q=typed,
                           mod=CS, action="/csp", search_name="tickers"))

    # The three lists this page produces, as tabs. They answer different
    # questions — what to sell, what the funds pay, what was turned down and
    # what it pays anyway — and stacking them meant the last one sat below
    # two summary cards and a hundred rows, which is where things go to not
    # be read. The active tab lives in the URL so a view is a link.
    n_filtered = before_rules - len(rows)
    tabs = (_tab_bar(tab, len(live), len(etfs), len(rejected),
                     n_filtered if rule_texts else 0, link)
            + _quick_filters(rule_conds, link))

    body = (
        card("Cash-Secured Put Engine",
             header + thesis + _controls(meta, typed, listed)
             + _list_note(listed, list_missing)
             + _lookup_block(typed, wanted, shown, slim_only, missing),
             icon="🪙")
        + screens
        + tabs
        + f'<div id="csp-pane-live" data-pane="live"'
          f'{"" if tab in ("", "live") else " style=display:none"}>'
        + card("Opportunities",
               '<div id="csp-table-sort-note" style="display:none;font-size:11px;'
               'color:#898781;margin:-2px 0 6px"></div>'
               + _table(live)
               + _sub("Click a header to sort; shift-click a second to sort "
                      "by both — e.g. premium-vs-required, then LQuality. "
                      "Rows with no contract sink to the bottom whichever "
                      "way a column points."),
               icon="📋")
        # Collateral rides with the opportunities: it is the arithmetic of
        # taking them, and it describes nothing on the other two tabs.
        + card("Collateral & concentration",
               _portfolio_block(meta.get("portfolio")), icon="🧮")
        + '</div>'
        # The ticker filter deliberately does NOT narrow the funds — typing
        # "NEM" is a question about a company, and silently emptying the
        # fund section would read as "no ETFs qualify today".
        + f'<div id="csp-pane-etf" data-pane="etf"'
          f'{"" if tab == "etf" else " style=display:none"}>'
        + card(f"ETFs ({len(etfs)})", _etf_block(etfs, meta), icon="🧺")
        + '</div>'
        + f'<div id="csp-pane-rejected" data-pane="rejected"'
          f'{"" if tab == "rejected" else " style=display:none"}>'
        + card(f"Rejected ({len(rejected)})",
               _reject_filter_note(rule_conds, before_rules, len(rows))
               # A named lookup gets the full table: you asked about this
               # ticker, so "rejected, and here is the reading" is the
               # answer, not a name in a grouped list.
               + (_table(rejected, len(live)) if wanted
                  else _rejected_block(rejected)),
               icon="🚫")
        + '</div>')

    js = """
function cspToggle(i){
  var el=document.getElementById('csp-d-'+i);
  if(el) el.style.display = el.style.display==='none' ? '' : 'none';
}
// Filter narrows the STORED snapshot — no network, no job. It rebuilds the
// URL from the CURRENT one rather than from scratch, so filtering does not
// silently discard the rules you already applied; every control on this
// page writes to the same query string and none of them may drop another's
// state.
function cspFilter(){
  var q = new URLSearchParams(window.location.search);
  var box = document.querySelector('input[name=tickers]');
  var sel = document.querySelector('select[name=list]');
  q.delete('tickers');
  q.delete('list');
  var v = box ? box.value.trim() : '';
  if (v) q.set('tickers', v);
  if (sel && sel.value) q.set('list', sel.value);
  var s = q.toString();
  window.location = '/csp' + (s ? '?' + s : '');
}
function cspRun(e){
  e.preventDefault();
  var f=e.target, msg=document.getElementById('csp-msg');
  var b=new URLSearchParams(new FormData(f)); b.append('action','csp_scan');
  msg.textContent='starting…';
  fetch('/run',{method:'POST',body:b})
    .then(function(r){return r.json()})
    .then(function(d){
      msg.textContent=d.message||'';
      if(d.ok) msg.textContent='Scan started — this takes a few minutes. '
        +'Reload when the job tray clears.';
    })
    .catch(function(){msg.textContent='could not start the scan'});
  return false;
}

// ── Column sorting ──────────────────────────────────────────────────────
// Sorts on the data-sort attribute the server wrote, which is
// core.csp.screen's flattened scalar — the same number the rule engine
// filters on. Reading the rendered cell text instead would sort "$1,234.50"
// as a string and rank $9.10 above it.
//
// Each name occupies THREE rows here (summary, the why-line, the hidden
// detail panel), so the sorter moves groups rather than rows. A sort that
// separated a row from its own reasoning would be worse than none.
function cspSortGroups(body) {
  var out = [], cur = null, seen = false;
  Array.prototype.forEach.call(body.rows, function (tr) {
    if (tr.dataset.main) {
      seen = true;
      cur = { main: tr, rest: [] };
      out.push(cur);
    } else if (cur) {
      cur.rest.push(tr);
    }
  });
  // A table whose rows carry no marker is one row per unit. Falling back
  // rather than returning nothing matters: an empty group list makes the
  // sort silently do nothing, which is indistinguishable from a sort that
  // ran and found the order already correct.
  if (!seen) {
    return Array.prototype.map.call(body.rows, function (tr) {
      return { main: tr, rest: [] };
    });
  }
  return out;
}

// Multi-key: shift-click adds a second (then third) key rather than
// replacing the first, so "best premium, then best business" is one
// gesture. Kept in memory only — a sort restored on load looks helpful and
// is not, because you cannot see what it is sorted by until you scroll.
var CSP_SORT = [];

// Each sortable table keeps its own key stack, keyed by table id — the
// Opportunities table and the rejected-premiums table are answering
// different questions and a shared sort state would make one of them jump
// when you clicked the other.
var CSP_SORTS = {};

function cspSort(th, additive) {
  // Found from the header rather than by a fixed id, so one function
  // serves every table on the page.
  var table = th.closest('table');
  if (!table || !th.classList.contains('csp-sort')) return;
  var id = table.id || 'csp-table';
  var CSP_SORT = CSP_SORTS[id] || (CSP_SORTS[id] = []);
  var idx = parseInt(th.dataset.idx, 10);
  var prev = CSP_SORT.filter(function (k) { return k.idx === idx; })[0];
  var dir = prev && prev.dir === 'desc' ? 'asc' : 'desc';
  if (additive) {
    CSP_SORT = CSP_SORT.filter(function (k) { return k.idx !== idx; });
    CSP_SORT.push({ idx: idx, dir: dir });
  } else {
    CSP_SORT = [{ idx: idx, dir: dir }];
  }
  CSP_SORTS[id] = CSP_SORT;

  var body = table.tBodies[0];
  var groups = cspSortGroups(body);

  function val(g, i) {
    var td = g.main.cells[i];
    var raw = td ? td.getAttribute('data-sort') : '';
    if (raw === '' || raw === null) return null;
    var n = parseFloat(raw);
    return isNaN(n) ? raw.toLowerCase() : n;
  }

  groups.sort(function (a, b) {
    for (var i = 0; i < CSP_SORT.length; i++) {
      var k = CSP_SORT[i];
      var x = val(a, k.idx), y = val(b, k.idx);
      // Blanks always sink, whichever way the column is pointing: a row
      // with no contract has no yield, and floating it to the top of a
      // yield sort would be answering a different question.
      if (x === null && y === null) continue;
      if (x === null) return 1;
      if (y === null) return -1;
      if (x < y) return k.dir === 'desc' ? 1 : -1;
      if (x > y) return k.dir === 'desc' ? -1 : 1;
    }
    return 0;
  });

  groups.forEach(function (g) {
    body.appendChild(g.main);
    g.rest.forEach(function (tr) { body.appendChild(tr); });
  });

  Array.prototype.forEach.call(table.tHead.rows[0].cells, function (cell) {
    var arrow = cell.querySelector('.csp-arrow');
    if (!arrow) return;
    var at = CSP_SORT.map(function (k) { return k.idx; })
                     .indexOf(parseInt(cell.dataset.idx, 10));
    arrow.textContent = at === -1 ? '' :
      (CSP_SORT[at].dir === 'desc' ? '▼' : '▲')
      + (CSP_SORT.length > 1 ? String(at + 1) : '');
  });

  var note = document.getElementById(id + '-sort-note');
  if (note) {
    var names = CSP_SORT.map(function (k) {
      return table.tHead.rows[0].cells[k.idx].textContent.replace(/[▼▲\\d]/g, '').trim()
        + (k.dir === 'desc' ? ' ↓' : ' ↑');
    });
    note.textContent = 'sorted by ' + names.join(', then ');
    note.style.display = '';
  }
}

// ── Per-row rescan ──────────────────────────────────────────────────────
// A named scan of one ticker: live spot, fresh chain, full audit kept, and
// the reference chain attached even if the company gate rejects it. One
// name is one chain, so it comes back in seconds — and it MERGES into the
// snapshot, so re-scanning one row never disturbs the rest.
//
// The row is not repainted in place. The scan is a background job and its
// result is a stored snapshot, so the honest thing to offer when it
// finishes is a reload — repainting one row from a job that has not
// necessarily written yet would show a number that is not in the snapshot
// the rest of the page is drawn from.
function cspRescan(btn) {
  var tk = btn.dataset.ticker;
  if (!tk || btn.disabled) return;
  var original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '…';
  btn.title = 'scanning ' + tk + '…';

  var body = new URLSearchParams();
  body.append('action', 'csp_scan');
  body.append('tickers', tk);
  body.append('limit', '1');
  // Ask for the chain whatever the verdict — the whole point of rescanning
  // a REJECTED row is to see what it is paying now.
  body.append('reference_rejected', '1');
  body.append('reference_budget', '1');
  ['min_dte', 'max_dte', 'target_dte', 'earnings_policy'].forEach(function (n) {
    var el = document.querySelector('[name=' + n + ']');
    if (el && el.value !== '') body.append(n, el.value);
  });

  fetch('/run', { method: 'POST', body: body })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) {
        btn.disabled = false;
        btn.innerHTML = original;
        toast(d.message || ('could not rescan ' + tk), 'err');
        return;
      }
      btn.innerHTML = '↻';
      btn.title = tk + ' is rescanning — reload when the job tray clears';
      btn.style.color = '#0F6E56';
      btn.disabled = false;
      btn.onclick = function (e) { e.stopPropagation(); location.reload(); };
      toast('Rescanning ' + tk + ' — click ↻ again to reload', 'ok');
    })
    .catch(function () {
      btn.disabled = false;
      btn.innerHTML = original;
      toast('could not reach the server', 'err');
    });
}

// ── Quick filters ───────────────────────────────────────────────────────
// Each box writes a rule into the same query string the builder above uses,
// so a quick filter and a built rule are the same object — removable from
// the same pill, describable by the same sentence. Apply rewrites every
// quick-filter rule at once and leaves rules on OTHER fields alone, which
// is why the boxes are prefilled: an un-prefilled box would silently drop
// the rule it was showing.
function cspQuick() {
  var q = new URLSearchParams(window.location.search);
  var boxes = document.querySelectorAll('.csp-quick');
  // Owned by FIELD, whatever operator wrote it — a preset's
  // `lquality:gt:90` and this row's `lquality:gte:90` are the same rule to
  // a reader, and leaving both would filter on quality twice.
  var mine = {};
  Array.prototype.forEach.call(boxes, function (b) {
    mine[b.dataset.field] = true;
  });
  var keep = q.getAll('rule').filter(function (r) {
    return !mine[r.split(':')[0]];
  });
  q.delete('rule');
  keep.forEach(function (r) { q.append('rule', r); });
  Array.prototype.forEach.call(boxes, function (b) {
    var v = b.value.trim();
    if (v !== '' && !isNaN(parseFloat(v))) {
      q.append('rule', b.dataset.field + ':' + b.dataset.op + ':' + v);
    }
  });
  var s = q.toString();
  window.location = '/csp' + (s ? '?' + s : '');
}

function cspQuickClear() {
  Array.prototype.forEach.call(document.querySelectorAll('.csp-quick'),
    function (b) { b.value = ''; });
  cspQuick();
}

// ── Tabs ────────────────────────────────────────────────────────────────
// The anchors already carry a working URL; this upgrades the click to an
// instant swap and rewrites the address bar to match, so the view stays
// linkable and the back button still moves between tabs.
function cspTab(e, key) {
  var panes = document.querySelectorAll('[data-pane]');
  if (!panes.length) return true;          // no panes: let the link navigate
  e.preventDefault();
  Array.prototype.forEach.call(panes, function (p) {
    p.style.display = p.dataset.pane === key ? '' : 'none';
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-tab]'),
    function (a) {
      var on = a.dataset.tab === key;
      a.style.background = on ? '#0b0b0b' : 'white';
      a.style.color = on ? 'white' : '#444441';
      a.style.borderColor = on ? '#0b0b0b' : '#d9d7ce';
    });
  try {
    var q = new URLSearchParams(window.location.search);
    if (key === 'live') { q.delete('tab'); } else { q.set('tab', key); }
    var str = q.toString();
    history.replaceState(null, '', '/csp' + (str ? '?' + str : ''));
  } catch (err) { /* a URL that will not rewrite is not worth a broken tab */ }
  // A table hidden at load has no measurable width, so anything that sized
  // itself against one needs a nudge once it is visible.
  window.dispatchEvent(new Event('resize'));
  return false;
}

(function initCspSort() {
  var tables = document.querySelectorAll('table[data-sortable]');
  Array.prototype.forEach.call(tables, function (table) {
    if (!table.tHead) return;
    Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th) {
      th.addEventListener('click', function (e) { cspSort(th, e.shiftKey); });
    });
  });
})();
"""
    return body, js
