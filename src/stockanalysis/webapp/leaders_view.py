"""
leaders_view.py
===============
The Sector Leaders page. Presentation only — every score, level and verdict
comes from core.sector_leaders, which the CLI scanner and this page both read
through the same snapshot.

    MARKET  →  SECTOR  →  INDUSTRY  →  STOCK  →  SETUP

Why this page renders from a snapshot instead of live
------------------------------------------------------
Unlike /shortside, this scan cannot fit in a request: two years of daily bars
for ~570 constituents plus five-minute bars for the finalists is three to five
minutes of network. So the page shows the last scan with its age stated up
front, and a button starts the background job — the same shape /scanner and
/stockdaytrade use.

What the layout is arguing
--------------------------
Two rankings, side by side, never merged. A combined list on a rotation day
buries whichever side is smaller, and the entire reason to open this page
before the bell is to see both. The bearish table renders at full size even
when — as on a tape where all four indices sit above all three moving
averages — nothing in it is tradeable, because "the best short available is a
47 and here is why" is the useful answer, not an empty panel.

Confidence, not confluence, is the sort key. Confluence is price agreeing
with price; it cannot see that the cleanest breakout on the board reported
earnings ninety minutes ago. The three-layer score can, and the layer
breakdown is always visible next to it so a name that scores well on two
layers and badly on the third reads as exactly that rather than as a number.

The news column is a control, not a readout. Headlines are shown verbatim and
someone decides what they mean; see core.leaders_store.save_verdict for why
this is not a keyword count.
"""

from __future__ import annotations

from stockanalysis.core import leaders_store as store
from stockanalysis.core import sector_leaders as SL

from .views import badge, card, empty, esc, tv_url

DASH = "—"

_DIR_STATUS = {"bullish": "good", "bearish": "bad", "neutral": "muted"}
_TREND_STATUS = {
    "Strong uptrend": "good", "Emerging uptrend": "good",
    "Uptrend pullback": "muted", "Range": "muted",
    "Emerging downtrend": "bad", "Strong downtrend": "bad",
}
_VERDICT_STATUS = {"confirms": "good", "neutral": "muted", "mixed": "watch",
                   "contradicts": "bad", "unavailable": "muted"}
_GRADE_COLOUR = {"A": "#0F6E56", "B": "#0F6E56", "C": "#8a6d1a", "D": "#A32D2D"}


def _n(v, nd=2, dash=DASH):
    return dash if v is None else f"{v:,.{nd}f}"


def _pct(v, nd=2):
    return DASH if v is None else f"{v:+.{nd}f}%"


def _sub(text, colour="#898781"):
    return (f'<div style="font-size:10px;color:{colour};margin-top:2px">'
            f'{esc(text)}</div>')


def _score(v, kind="long"):
    """A 0-100 cell. The short side is never green: a high short score means
    'interesting', not 'good'."""
    if v is None:
        return f'<span style="color:#b5b3ad">{DASH}</span>'
    if kind == "short":
        colour = "#A32D2D" if v >= 65 else "#8a6d1a" if v >= 50 else "#898781"
    else:
        colour = "#0F6E56" if v >= 65 else "#8a6d1a" if v >= 50 else "#898781"
    return (f'<span style="font-weight:700;color:{colour}">{v:.0f}</span>'
            f'<span style="font-size:10px;color:#b5b3ad">/100</span>')


def _bar(pct, colour="#185FA5", width=60):
    pct = max(0.0, min(100.0, pct or 0))
    return (f'<div style="height:4px;background:#eceae4;border-radius:2px;'
            f'width:{width}px;display:inline-block;vertical-align:middle">'
            f'<div style="height:4px;border-radius:2px;width:{pct}%;'
            f'background:{colour}"></div></div>')


# ─────────────────────────────────────────────────────────────────────────
# LEVEL 1 — MARKET
# ─────────────────────────────────────────────────────────────────────────

def _market_block(market: dict) -> str:
    label = market.get("label") or "?"
    status = ("good" if "BULLISH" in label else
              "bad" if "BEARISH" in label else "muted")
    score = market.get("score")

    # How close the call sits to its own band edge. A +11.4 that clears
    # STRONG BULLISH by 0.4 is a different statement from a +14, and the
    # page should not let the label hide that.
    edges = [("STRONG BULLISH", SL_STRONG_BULL), ("BULLISH", SL_BULL),
             ("BEARISH", SL_BEAR), ("STRONG BEARISH", SL_STRONG_BEAR)]
    margin = ""
    if score is not None:
        near = min((abs(score - e) for _, e in edges), default=None)
        if near is not None and near <= 1.5:
            margin = badge(f"clears its band by {near:.1f}", "watch")

    ub = market.get("universe_breadth") or {}
    rates = market.get("rates") or {}

    breadth_txt = f'universe breadth {_n(ub.get("pct_above_50sma"), 0)}% >50SMA'
    ad_txt = f'A/D {_n(ub.get("pct_advancing_today"), 0)}% advancing'
    rates_txt = (f'10Y {rates.get("yield_pct")}% '
                 f'({rates.get("change_bps"):+.1f}bp)' if rates else "10Y N/A")
    chips = (badge(label, status)
             + badge("score —" if score is None else f"score {score:+.1f}", "info")
             + margin
             + badge(breadth_txt, "muted")
             + badge(ad_txt, "muted")
             + badge(rates_txt, "muted"))

    drivers = "".join(
        f'<div style="font-size:11px;padding:2px 0;color:#444441">{esc(d)}</div>'
        for d in (market.get("drivers") or []))

    return (chips
            + f'<div style="margin-top:8px">{drivers}</div>'
            + _sub("Every line is measured. The label is the sum; the sum is "
                   "not the same thing as today's character — check the "
                   "cyclical−defensive line before treating a bullish label "
                   "as permission to buy cyclicals."))


# Band edges, read from the scanner so the page cannot drift from the scorer.
try:
    from stockanalysis.scanners.scan_sector_leaders import (
        STRONG_BULL as SL_STRONG_BULL, BULL as SL_BULL,
        BEAR as SL_BEAR, STRONG_BEAR as SL_STRONG_BEAR)
except ImportError:                                  # pragma: no cover
    SL_STRONG_BULL, SL_BULL, SL_BEAR, SL_STRONG_BEAR = 11.0, 4.0, -4.0, -11.0


# ─────────────────────────────────────────────────────────────────────────
# LEVEL 2 — SECTORS
# ─────────────────────────────────────────────────────────────────────────

def _sortable_head(cols, skip=()) -> str:
    """Header cells wired for click-to-sort.

    Columns listed in `skip` stay inert — a control column ("#", the expander)
    has no ordering worth asking for.
    """
    out = []
    for i, c in enumerate(cols):
        if c in skip:
            out.append(f'<th style="padding:6px 8px;text-align:left;'
                       f'font-size:10px;color:#898781;text-transform:uppercase;'
                       f'letter-spacing:.04em">{esc(c)}</th>')
            continue
        out.append(
            f'<th onclick="ldSort(this,{i})" title="click to sort" '
            f'style="padding:6px 8px;text-align:left;font-size:10px;'
            f'color:#898781;text-transform:uppercase;letter-spacing:.04em;'
            f'cursor:pointer;user-select:none;white-space:nowrap">'
            f'{esc(c)}<span class="ld-arrow" style="color:#b5b3ad">&nbsp;</span>'
            f'</th>')
    return "".join(out)


_SECTOR_COLS = ("#", "Sector", "ETF", "Tier", "Score", "1D", "5D", "20D",
                "RS vs SPY 20d", "%>50SMA", "NH/NL", "RVOL", "Trend",
                "Top 10 holdings")


def _holdings_cell(s: dict, idx: int) -> str:
    """The expander control plus a one-line preview of who is leading.

    Collapsed by default: eighteen sectors times ten holdings is a hundred
    and eighty rows, and the sector table's job is to rank sectors. The
    preview names the strongest holding so the column is worth something
    before it is opened.
    """
    rows = s.get("holdings") or []
    if not rows:
        return ('<span style="color:#b5b3ad;font-size:11px">no published '
                'holdings</span>')
    lead = rows[0]
    move = lead.get("r1d")
    colour = ("#0F6E56" if (move or 0) > 0 else
              "#A32D2D" if (move or 0) < 0 else "#898781")
    return (f'<button onclick="ldHold(event,{idx})" id="ldh-b-{idx}" '
            f'class="ld-exp" style="border:1px solid #d9d7ce;background:#fff;'
            f'border-radius:4px;width:18px;height:18px;line-height:1;'
            f'font-size:12px;cursor:pointer;padding:0;margin-right:6px">+</button>'
            f'<span style="font-size:11px">{len(rows)} · top mover '
            f'<b>{esc(lead.get("ticker"))}</b> '
            f'<span style="color:{colour}">{_pct(move, 2)}</span></span>')


def _holdings_detail(s: dict, idx: int) -> str:
    """Top holdings by allocation weight, ordered by today's move.

    Two orderings are in play and conflating them would misread the fund:
    membership of this list is by ALLOCATION (these are the ten largest
    positions), while the sort within it is by PERCENT CHANGE. The weight
    column stays visible so the distinction is legible — a +6% name at 0.8%
    weight moves the ETF far less than a +1% name at 15%.
    """
    rows = s.get("holdings") or []
    if not rows:
        return ""
    # Sortable too: the list arrives ordered by today's move, but "which of
    # the big positions is actually big" is a weight sort, and ldSort scopes
    # itself with th.closest('table') so a nested table sorts independently.
    head = _sortable_head(("Holding", "Name", "Weight", "1D", "5D", "20D",
                           ">50SMA"))

    body = []
    contrib_total = 0.0
    for h in rows:
        w, r1 = h.get("weight"), h.get("r1d")
        if w is not None and r1 is not None:
            contrib_total += w * r1 / 100.0

        def signed(v):
            c = ("#0F6E56" if (v or 0) > 0 else
                 "#A32D2D" if (v or 0) < 0 else "#898781")
            return f'<span style="color:{c}">{_pct(v)}</span>'

        above = h.get("above_sma50")
        body.append(
            '<tr>'
            f'<td style="padding:3px 8px;font-size:12px"><b>'
            f'<a href="{tv_url(h.get("ticker"))}" target="_blank" '
            f'style="color:#185FA5;text-decoration:none">'
            f'{esc(h.get("ticker"))}</a></b></td>'
            f'<td style="padding:3px 8px;font-size:11px;color:#898781">'
            f'{esc(str(h.get("name") or "")[:34])}</td>'
            f'<td style="padding:3px 8px;font-size:12px">'
            f'{_bar((w or 0) * 4, "#185FA5", 40)} {_n(w, 2)}%</td>'
            f'<td style="padding:3px 8px;font-size:12px">{signed(r1)}</td>'
            f'<td style="padding:3px 8px;font-size:12px">{signed(h.get("r5d"))}</td>'
            f'<td style="padding:3px 8px;font-size:12px">{signed(h.get("r20d"))}</td>'
            f'<td style="padding:3px 8px;font-size:12px">'
            + ("—" if above is None else
               '<span style="color:#0F6E56">yes</span>' if above else
               '<span style="color:#A32D2D">no</span>')
            + '</td></tr>')

    weight_sum = sum(h.get("weight") or 0 for h in rows)
    return (
        f'<div style="background:#faf9f6;padding:10px 14px;'
        f'border-top:1px solid #eceae4">'
        f'<div style="font-size:11px;color:#898781;margin-bottom:5px">'
        f'The ten largest positions in {esc(s.get("etf"))} by allocation, '
        f'sorted by today\'s move. They are '
        f'<b>{_n(weight_sum, 1)}%</b> of the fund; their weighted move today '
        f'is <b>{_pct(contrib_total, 2)}</b> against the ETF\'s '
        f'<b>{_pct((s.get("metrics") or {}).get("r1d"))}</b> — the gap is the '
        f'other {_n(100 - weight_sum, 1)}% of the book.</div>'
        f'<table class="ld-sortable" style="width:100%;border-collapse:collapse">'
        f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody>'
        f'</table></div>')


def _sector_row(i: int, s: dict) -> str:
    m = s.get("metrics") or {}
    b = s.get("breadth") or {}
    sc = s.get("scores") or {}
    label = sc.get("quality_label") or "?"
    tier = "industry" if s.get("tier") == "industry" else "sector"

    def td(inner, extra=""):
        return (f'<td style="padding:6px 8px;font-size:12px;'
                f'border-top:1px solid #eceae4;{extra}">{inner}</td>')

    def signed(v):
        colour = "#0F6E56" if (v or 0) > 0 else "#A32D2D" if (v or 0) < 0 else "#898781"
        return f'<span style="color:{colour}">{_pct(v)}</span>'

    nh, nl = b.get("new_highs_20d"), b.get("new_lows_20d")
    return (
        f'<tr>'
        + td(f'<span style="color:#b5b3ad">{i}</span>')
        + td(f'<b>{esc(s.get("name"))}</b>'
             + (_sub(f'within {esc(s.get("parent"))}') if s.get("parent") else ""))
        + td(f'<a href="{tv_url(s.get("etf"))}" target="_blank" '
             f'style="color:#185FA5;text-decoration:none">{esc(s.get("etf"))}</a>')
        + td(f'<span style="font-size:10px;color:#898781">{tier}</span>')
        + td(_score(sc.get("score"),
                    "short" if s.get("direction") == "bearish" else "long"))
        + td(signed(m.get("r1d")))
        + td(signed(m.get("r5d")))
        + td(signed(m.get("r20d")))
        + td(signed((s.get("rs_spy") or {}).get("r20d")))
        + td(f'{_bar(b.get("pct_above_sma50"), "#185FA5", 44)} '
             f'{_n(b.get("pct_above_sma50"), 0)}%')
        + td(f'<span style="color:#0F6E56">{nh if nh is not None else DASH}</span>'
             f'<span style="color:#b5b3ad"> / </span>'
             f'<span style="color:#A32D2D">{nl if nl is not None else DASH}</span>')
        + td(_n(m.get("rvol"), 2))
        + td(badge(label, _TREND_STATUS.get(label, "muted"), "small"))
        + td(_holdings_cell(s, i))
        + '</tr>'
        f'<tr id="ldh-d-{i}" style="display:none" data-nosort="1">'
        f'<td colspan="{len(_SECTOR_COLS)}" style="padding:0">'
        f'{_holdings_detail(s, i)}</td></tr>')


def _sector_table(sectors: list) -> str:
    if not sectors:
        return empty("No sector data in the snapshot.")
    head = _sortable_head(_SECTOR_COLS, skip=("#",))
    body = "".join(_sector_row(i, s) for i, s in enumerate(sectors, 1))
    return (f'<div style="overflow-x:auto"><table class="ld-sortable" '
            f'style="width:100%;border-collapse:collapse">'
            f'<thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>'
            + _sub("Breadth is universe breadth over the constituent lists "
                   "this project holds — not exchange breadth. NH/NL are "
                   "20-day CLOSING highs and lows among members."))


# ─────────────────────────────────────────────────────────────────────────
# LEVEL 4/5 — CANDIDATES
# ─────────────────────────────────────────────────────────────────────────

def _levels_block(row: dict) -> str:
    setup = row.get("setup") or {}
    lv = setup.get("levels")
    if not lv:
        note = setup.get("setup") or "no setup"
        return (f'<div style="font-size:11px;color:#8a6d1a">{esc(note)}</div>'
                + _sub("No entry is priced. A level the engine cannot derive "
                       "from the bars is left empty rather than invented."))

    def line(label, value, basis, weight="400"):
        return (f'<div style="display:flex;justify-content:space-between;'
                f'gap:10px;font-size:11px;padding:2px 0">'
                f'<span style="color:#898781">{esc(label)}</span>'
                f'<span style="text-align:right"><b style="font-weight:{weight}">'
                f'{esc(value)}</b>{_sub(basis)}</span></div>')

    rr = setup.get("rr")
    rr_colour = "#0F6E56" if (rr or 0) >= 1.5 else "#8a6d1a" if (rr or 0) >= 1.0 else "#A32D2D"
    grade = str(setup.get("grade") or "")[:1]

    return (
        f'<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">'
        f'{badge(setup.get("setup") or "?", "info", "small")}'
        f'<span style="font-size:11px">grade <b style="color:'
        f'{_GRADE_COLOUR.get(grade, "#898781")}">{esc(setup.get("grade"))}</b></span>'
        f'<span style="font-size:11px">R:R <b style="color:{rr_colour}">'
        f'{_n(rr, 2)}</b> <span style="color:#b5b3ad">/ {_n(setup.get("rr2"), 2)} to T2</span></span>'
        f'</div>'
        + line("Entry zone", f'{_n(lv.get("entry_low"))} – {_n(lv.get("entry_high"))}',
               lv.get("entry_basis") or "", "700")
        + line("Stop", _n(lv.get("stop")),
               f'{lv.get("stop_basis") or ""} · risk {_n(lv.get("risk_atr"), 2)} ATR')
        + line("Target 1", _n(lv.get("target1")), lv.get("target1_basis") or "")
        + line("Target 2", _n(lv.get("target2")), lv.get("target2_basis") or "")
        + line("Invalidation", "", lv.get("invalidation") or "")
        + _sub(f'ATR(14) ${_n(lv.get("atr14"))} · risk ${_n(lv.get("risk_per_share"))}/share'))


def _scores_block(row: dict) -> str:
    """The layer breakdown. Shown always, because a 70 built from a 95
    technical and a 20 fundamental is a different animal from a flat 70."""
    conf = row.get("confidence") or {}
    stock = row.get("stock_score") or {}
    confl = row.get("confluence") or {}
    clarity = row.get("clarity") or {}
    lead = row.get("leadership") or {}

    def part(label, value, total, colour="#185FA5"):
        pctv = 100.0 * (value or 0) / total if total else 0
        return (f'<div style="display:flex;justify-content:space-between;'
                f'gap:8px;font-size:11px;padding:2px 0">'
                f'<span>{esc(label)} <span style="color:#b5b3ad">/{total}</span></span>'
                f'<span>{_bar(pctv, colour, 52)} <b>{_n(value, 1)}</b></span></div>')

    tech = ('<div style="font-size:10px;color:#898781;text-transform:uppercase;'
            'letter-spacing:.04em;margin-bottom:3px">Stock trend score</div>'
            + part("Trend", stock.get("trend"), 25)
            + part("Relative strength", stock.get("rel_strength"), 20)
            + part("Momentum", stock.get("momentum"), 15)
            + part("Volume", stock.get("volume"), 15)
            + part("Structure", stock.get("structure"), 15)
            + part("Sector confirmation", stock.get("sector_confirm"), 10))

    confl_block = ('<div style="font-size:10px;color:#898781;text-transform:uppercase;'
                   'letter-spacing:.04em;margin:8px 0 3px">Confluence</div>'
                   + part("Market", confl.get("market"), 25, "#8a6d1a")
                   + part("Sector", confl.get("sector"), 25, "#8a6d1a")
                   + part("Trend clarity", confl.get("clarity"), 20, "#8a6d1a")
                   + part("Relative strength", confl.get("rel_strength"), 15, "#8a6d1a")
                   + part("Volume", confl.get("volume"), 15, "#8a6d1a"))

    clar = ""
    if clarity:
        clar = ('<div style="font-size:10px;color:#898781;text-transform:uppercase;'
                'letter-spacing:.04em;margin:8px 0 3px">Trend clarity '
                f'{_n(clarity.get("score"), 0)}/100</div>'
                + _sub(f'R² {_n(clarity.get("r2"), 3)} · '
                       f'{_n(clarity.get("pct_right_side_ema20"), 0)}% of days on the '
                       f'right side of the 20 EMA · deepest counter-move '
                       f'{_n(clarity.get("max_adverse_atr"), 2)} ATR · MA stack held '
                       f'{_n(clarity.get("ma_stack_pct"), 0)}% of the window'))

    conf_block = ""
    if conf:
        conf_block = (
            '<div style="font-size:10px;color:#898781;text-transform:uppercase;'
            'letter-spacing:.04em;margin:8px 0 3px">Confidence stack</div>'
            + part("Technical (50%)", conf.get("technical"), 100, "#0F6E56")
            + part("Fundamental (25%)", conf.get("fundamental"), 100, "#0F6E56")
            + part("News (25%)", conf.get("news"), 100, "#0F6E56")
            + (_sub("capped: " + "; ".join(conf.get("caps_applied") or []), "#A32D2D")
               if conf.get("caps_applied") else "")
            + (_sub(conf.get("fundamental_note") or "")))

    return (f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">'
            f'<div>{tech}{clar}</div><div>{confl_block}{conf_block}</div></div>'
            + _sub(f'Leadership {_n(lead.get("score"), 0)}/100 — '
                   f'{esc(lead.get("band") or "")}'))


def _news_block(row: dict, snap: dict) -> str:
    """Headlines verbatim, plus the verdict control.

    The verdict is a stored judgement rather than a computed one. Nothing
    here counts positive and negative words — see leaders_store.save_verdict.
    """
    t = row.get("ticker")
    direction = row.get("direction")
    conf = (snap.get("confirmations") or {}).get(t) or {}
    heads = conf.get("headlines") or []
    v = store.verdict_for(snap, t, direction)

    if not conf:
        return (_sub("No confirmation pass has been run for this name yet — "
                     "news, fundamentals and the earnings date are unknown, "
                     "and the confidence score is holding news at "
                     "'unavailable' (40) rather than assuming neutral.",
                     "#8a6d1a")
                + f'<button class="btn secondary" style="margin-top:6px" '
                  f'onclick="ldConfirm(\'{esc(t)}\')">Fetch news + fundamentals</button>')

    items = []
    for h in heads[:8]:
        if not h.get("title"):
            continue
        url = h.get("url") or ""
        title = esc(h["title"])
        link = (f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer" '
                f'style="color:#185FA5;text-decoration:none">{title}</a>'
                if url else title)
        meta = f'{h.get("when") or ""} · {h.get("publisher") or ""}'
        items.append(
            '<div style="font-size:11px;padding:3px 0;'
            'border-top:1px solid #f2f0ea">'
            + link + _sub(meta) + '</div>')

    buttons = "".join(
        f'<button class="btn {"" if v.get("verdict") == k else "secondary"}" '
        f'style="padding:3px 9px;font-size:11px" '
        f'onclick="ldVerdict(\'{esc(t)}\',\'{esc(direction)}\',\'{k}\')">'
        f'{k}</button>'
        for k in ("confirms", "neutral", "mixed", "contradicts"))

    earn = conf.get("earnings") or {}
    days = earn.get("days_away")
    earn_chip = ""
    if days is not None:
        st = "bad" if days == 0 else "watch" if 0 < days <= 5 else "muted"
        earn_chip = badge(
            "reported today" if days == 0 else
            f"earnings in {days}d" if days > 0 else
            f"reported {abs(days)}d ago", st, "small")

    return (
        f'<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;'
        f'margin-bottom:6px">'
        f'{badge("news: " + (v.get("verdict") or "unavailable"), _VERDICT_STATUS.get(v.get("verdict"), "muted"), "small")}'
        f'{earn_chip}{buttons}</div>'
        + ("".join(items) or _sub("no headlines returned by the feed"))
        + (_sub(f'verdict set {v.get("set_at")}') if v.get("set_at") else
           _sub("no verdict recorded — news is scored 'unavailable' (40), "
                "deliberately below neutral")))


def _fundamentals_block(row: dict, snap: dict) -> str:
    t = row.get("ticker")
    conf = (snap.get("confirmations") or {}).get(t) or {}
    if not conf:
        return ""
    f = conf.get("fundamentals") or {}
    sc = conf.get("scores") or {}
    bq = sc.get("business_quality") or {}
    fh = sc.get("financial_health") or {}
    cf = conf.get("cashflow") or {}

    def kv(label, value):
        return (f'<div style="display:flex;justify-content:space-between;'
                f'gap:8px;font-size:11px;padding:1px 0">'
                f'<span style="color:#898781">{esc(label)}</span>'
                f'<span>{esc(value)}</span></div>')

    mcap = f.get("marketCap")
    rows = (kv("Market cap", f"${mcap / 1e9:,.0f}B" if mcap else DASH)
            + kv("Forward P/E", _n(f.get("forwardPE"), 1))
            + kv("Revenue growth", _pct((f.get("revenueGrowth") or 0) * 100, 1)
                 if f.get("revenueGrowth") is not None else DASH)
            + kv("Free cash flow",
                 f"${cf.get('free_cash_flow') / 1e9:,.1f}B"
                 if cf.get("free_cash_flow") else DASH)
            + kv("Consensus target", _n(f.get("targetMeanPrice")))
            + kv("Recommendation", f.get("recommendationKey") or DASH))

    drivers = "".join(_sub(d) for d in (bq.get("drivers") or [])[:4])
    bq_score = bq.get("score")
    fh_score = fh.get("score")
    bq_chip = badge(
        f'quality {DASH if bq_score is None else bq_score} '
        f'({bq.get("label") or "n/a"})', "muted", "small")
    fh_chip = badge(
        f'health {DASH if fh_score is None else fh_score} '
        f'({fh.get("label") or "n/a"})', "muted", "small")
    return ('<div style="font-size:10px;color:#898781;text-transform:uppercase;'
            'letter-spacing:.04em;margin-bottom:3px">Fundamentals</div>'
            + bq_chip + fh_chip
            + f'<div style="margin-top:5px">{rows}</div>{drivers}'
            + _sub("Free cash flow is read off the cash-flow statement, not "
                   "the .info field, which is unreliable for this project."))


def _intraday_block(row: dict, snap: dict) -> str:
    idt = (snap.get("intraday") or {}).get(row.get("ticker"))
    m = row.get("metrics") or {}
    if not idt:
        return _sub("no intraday pass for this name — VWAP, opening range and "
                    "intraday RVOL are fetched only for the finalists")
    return (f'<div style="font-size:10px;color:#898781;text-transform:uppercase;'
            f'letter-spacing:.04em;margin-bottom:3px">'
            f'Intraday · session {esc(idt.get("session_date"))}</div>'
            f'<div style="font-size:11px">VWAP <b>{_n(idt.get("vwap"))}</b> — '
            f'closed <b>{esc(idt.get("close_vs_vwap"))}</b> · '
            f'opening range {_n(idt.get("opening_range_15m_low"))}–'
            f'{_n(idt.get("opening_range_15m_high"))}, closed '
            f'<b>{esc(idt.get("closed_vs_or"))}</b></div>'
            f'<div style="font-size:11px">intraday RVOL '
            f'<b>{_n(idt.get("intraday_rvol"), 2)}</b> · ATR '
            f'{_n(m.get("atr_pct"), 2)}% (${_n(m.get("atr14"))})</div>'
            + _sub("Describes the last completed session, not a live quote."))


def _detail(row: dict, snap: dict, idx: int) -> str:
    return (f'<div style="background:#faf9f6;padding:12px 14px;'
            f'border-top:1px solid #eceae4">'
            f'<div style="display:grid;grid-template-columns:1.1fr 1fr;gap:18px">'
            f'<div>{_levels_block(row)}'
            f'<div style="margin-top:10px">{_intraday_block(row, snap)}</div></div>'
            f'<div>{_news_block(row, snap)}'
            f'<div style="margin-top:10px">{_fundamentals_block(row, snap)}</div>'
            f'</div></div>'
            f'<div style="margin-top:12px;border-top:1px solid #eceae4;'
            f'padding-top:10px">{_scores_block(row)}</div>'
            f'</div>')


_CAND_COLS = ("", "Ticker", "Group", "Confidence", "Confluence", "Stock",
              "Leadership", "Clarity", "RS 20d", "RVOL", "Setup", "R:R", "News")


def _cand_row(row: dict, snap: dict, idx: int) -> str:
    m = row.get("metrics") or {}
    setup = row.get("setup") or {}
    conf = row.get("confidence") or {}
    direction = row.get("direction")
    kind = "short" if direction == "short" else "long"
    v = store.verdict_for(snap, row.get("ticker"), direction)

    def td(inner, extra=""):
        return (f'<td style="padding:6px 8px;font-size:12px;'
                f'border-top:1px solid #eceae4;{extra}">{inner}</td>')

    rr = setup.get("rr")
    grade = str(setup.get("grade") or "")[:1]
    flag = row.get("divergence")

    return (
        f'<tr style="cursor:pointer" onclick="ldToggle({idx})">'
        + td('<span style="color:#b5b3ad">▸</span>')
        + td(f'<a href="{tv_url(row.get("ticker"))}" target="_blank" '
             f'onclick="event.stopPropagation()" '
             f'style="color:#185FA5;text-decoration:none;font-weight:700">'
             f'{esc(row.get("ticker"))}</a>')
        + td(f'{esc(row.get("group"))}'
             + _sub(esc(row.get("etf") or "")))
        + td(_score(conf.get("score"), kind)
             + _sub(esc((conf.get("label") or "").split("—")[0].strip())))
        + td(_score(row.get("confluence", {}).get("score"), kind))
        + td(_score(row.get("stock_score", {}).get("score"), kind))
        + td(_score(row.get("leadership", {}).get("score"), kind)
             + _sub(esc(row.get("leadership", {}).get("band") or "")))
        + td(_n((row.get("clarity") or {}).get("score"), 0))
        + td(_pct((row.get("rs_spy") or {}).get("r20d")))
        + td(_n(m.get("rvol"), 2))
        + td(f'<span style="font-size:11px">{esc(setup.get("setup"))}</span>'
             + (_sub(esc(flag), "#8a6d1a") if flag else ""))
        + td(f'<b style="color:{_GRADE_COLOUR.get(grade, "#898781")}">'
             f'{_n(rr, 2)}</b>' + _sub(esc(setup.get("grade") or "")))
        + td(badge(v.get("verdict") or "unavailable",
                   _VERDICT_STATUS.get(v.get("verdict"), "muted"), "small"))
        + '</tr>'
        f'<tr id="ld-d-{idx}" style="display:none" data-nosort="1">'
        f'<td colspan="{len(_CAND_COLS)}" style="padding:0">'
        f'{_detail(row, snap, idx)}</td></tr>')


def _cand_table(rows: list, snap: dict, start: int) -> str:
    if not rows:
        return empty("Nothing on this side of the book.")
    head = _sortable_head(_CAND_COLS, skip=("",))
    body = "".join(_cand_row(r, snap, start + i) for i, r in enumerate(rows))
    return (f'<div style="overflow-x:auto"><table class="ld-sortable" '
            f'style="width:100%;border-collapse:collapse">'
            f'<thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


# ─────────────────────────────────────────────────────────────────────────
# DAY TRADE
# ─────────────────────────────────────────────────────────────────────────

_DAY_COLS = ("Ticker", "Group", "Dir", "iRVOL", "ATR%", "ATR$", "VWAP",
             "vs VWAP", "Opening range 15m", "Closed", "Confidence")


def _day_table(rows: list, snap: dict) -> str:
    """Ranked by intraday relative volume over EVERY name the intraday pass
    covered — not over the swing tables above it.

    Those tables are sorted by confidence, and the first cut of this page fed
    them straight in. That dropped MRNA (38× relative volume) and MRK (10×)
    off the day-trading list entirely, because both are extended and score
    badly as swings. They are the two names a day trader most wants on the
    screen. Section 8 of the spec asks for these lists to be separated for
    exactly this reason, and sharing a sort key silently re-merged them.
    """
    intraday = snap.get("intraday") or {}
    by_ticker = {r.get("ticker"): r for r in rows}
    live = []
    for t in intraday:
        r = by_ticker.get(t) or _row_for(snap, t)
        if r:
            live.append(r)
    live.sort(key=lambda r: -(intraday[r["ticker"]].get("intraday_rvol") or 0))
    if not live:
        return empty("No intraday pass in this snapshot.")

    head = _sortable_head(_DAY_COLS)
    body = []
    for r in live[:14]:
        i = intraday[r["ticker"]]
        m = r.get("metrics") or {}

        def td(inner):
            return (f'<td style="padding:6px 8px;font-size:12px;'
                    f'border-top:1px solid #eceae4">{inner}</td>')

        vs = i.get("close_vs_vwap")
        vs_colour = "#0F6E56" if vs == "above" else "#A32D2D" if vs == "below" else "#898781"
        body.append(
            '<tr>'
            + td(f'<a href="{tv_url(r.get("ticker"), "5")}" target="_blank" '
                 f'style="color:#185FA5;text-decoration:none;font-weight:700">'
                 f'{esc(r.get("ticker"))}</a>')
            + td(esc(r.get("group")))
            + td(badge(r.get("direction") or "", _DIR_STATUS.get(
                "bullish" if r.get("direction") == "long" else "bearish"), "small"))
            + td(f'<b>{_n(i.get("intraday_rvol"), 2)}</b>')
            + td(_n(m.get("atr_pct"), 2))
            + td(_n(m.get("atr14")))
            + td(_n(i.get("vwap")))
            + td(f'<span style="color:{vs_colour}">{esc(vs)}</span>')
            + td(f'{_n(i.get("opening_range_15m_low"))} – '
                 f'{_n(i.get("opening_range_15m_high"))}')
            + td(esc(i.get("closed_vs_or")))
            + td(_score((r.get("confidence") or {}).get("score"),
                        "short" if r.get("direction") == "short" else "long"))
            + '</tr>')
    return (f'<div style="overflow-x:auto"><table class="ld-sortable" '
            f'style="width:100%;border-collapse:collapse">'
            f'<thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
            + _sub("Ranked by intraday relative volume. Every figure "
                   "describes the last completed session — when this page is "
                   "opened before the bell these are yesterday's numbers, "
                   "which is what makes them a watchlist rather than a "
                   "trigger."))


# ─────────────────────────────────────────────────────────────────────────
# PAGE
# ─────────────────────────────────────────────────────────────────────────

def _build_rows(snap: dict, direction: str, limit: int) -> list:
    """Confidence-scored rows for one side.

    The scoring itself lives in core.sector_leaders.scored_rows, shared with
    the pre-market confluence email — two copies would have drifted the first
    time either gained a gate.
    """
    return SL.scored_rows(snap, direction, limit)


def _row_for(snap: dict, ticker: str) -> dict | None:
    """Scored row for one ticker, whichever direction reads stronger.

    Used by the day-trading table for names the swing tables cut. A name with
    38× relative volume belongs on the intraday list whether or not it is a
    swing candidate, and it still needs its confidence score attached.
    """
    best, best_dir = None, None
    for c in snap.get("candidates") or []:
        if c.get("ticker") != ticker:
            continue
        score = (c.get("confluence") or {}).get("score", 0)
        if best is None or score > (best.get("confluence") or {}).get("score", 0):
            best, best_dir = c, c.get("direction")
    if not best:
        return None
    for r in _build_rows(snap, best_dir, 10_000):
        if r.get("ticker") == ticker:
            return r
    return None


def _staleness(snap: dict) -> str:
    age = store.age_hours(snap)
    if age is None:
        return badge("age unknown", "watch")
    if age < 1:
        return badge(f"scanned {age * 60:.0f} min ago", "good")
    if age < 18:
        return badge(f"scanned {age:.1f}h ago", "muted")
    return badge(f"scanned {age / 24:.1f} days ago — re-run before trusting "
                 f"any level", "bad")


def leaders_page(query: dict | None = None) -> tuple[str, str]:
    snap = store.load()

    controls = (
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
        'margin-top:10px">'
        '<button class="btn" onclick="ldRun()">Run sector-leader scan</button>'
        '<button class="btn secondary" onclick="ldConfirmTop()">'
        'Confirm top names (news + fundamentals)</button>'
        '</div>')

    thesis = (
        '<div style="font-size:11px;color:#898781;line-height:1.6">'
        'Ranks the market, then the sector, then the industry, then the '
        'stock, then the setup — and scores how many of those layers agree. '
        '<b>Nothing here is ranked by daily percent change.</b><br><br>'
        '<b>Two rankings, never merged.</b> A combined list on a rotation '
        'day buries whichever side is smaller.<br><br>'
        '<b>Confluence is price agreeing with price.</b> It cannot see that '
        'the cleanest breakout on the board reported earnings ninety minutes '
        'ago, or that the tidiest short just guided up. Confidence adds '
        'fundamentals and a read of the headlines on top, and gates: news '
        'that contradicts the chart caps the score rather than averaging '
        'into it.<br><br>'
        '<b>Extension is measured in ATR.</b> A name more than '
        f'{SL.EXTENSION_NO_ENTRY_ATR:.0f} ATR from its 20 EMA gets no entry '
        'price at all — it is reported as extended, because a chase price is '
        'not a plan.</div>')

    if not snap:
        return (card("Sector Leaders",
                     badge("no scan yet", "watch") + thesis + controls,
                     icon="🧭")
                + card("Nothing scanned",
                       empty("Run the scan to populate this page. It takes "
                             "three to five minutes — two years of daily bars "
                             "for ~570 constituents, then five-minute bars "
                             "for the finalists.")),
                _JS)

    market = snap.get("market") or {}
    longs = _build_rows(snap, "long", 18)
    shorts = _build_rows(snap, "short", 18)
    sectors = snap.get("sectors") or []

    bullish = [s for s in sectors if s.get("direction") == "bullish"]
    bearish = [s for s in sectors if s.get("direction") == "bearish"]

    n_cand = len(snap.get("candidates") or [])
    header = (
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
        'margin-bottom:8px">'
        + _staleness(snap)
        + badge(f"{len(sectors)} groups", "muted")
        + badge(f"{len(bullish)} bullish", "good")
        + badge(f"{len(bearish)} bearish", "bad")
        + badge(f"{n_cand} scored both ways", "muted")
        + '</div>')

    gaps = "".join(
        f'<div style="font-size:11px;color:#8a6d1a;padding:2px 0">• {esc(g)}</div>'
        for g in (snap.get("data_gaps") or []))

    body = (
        card("Sector Leaders", header + thesis + controls, icon="🧭")
        + card("Level 1 · Market", _market_block(market), icon="🌐")
        + card(f"Level 2 · Sector ranking ({len(sectors)})",
               '<div style="display:flex;gap:8px;margin-bottom:8px">'
               '<button class="btn secondary" style="padding:3px 10px;'
               'font-size:11px" onclick="ldHoldAll(true)">Expand all '
               'holdings</button>'
               '<button class="btn secondary" style="padding:3px 10px;'
               'font-size:11px" onclick="ldHoldAll(false)">Collapse all'
               '</button></div>'
               + _sector_table(sectors)
               + _sub("Every column header sorts — click once for ascending, "
                      "again for descending. The Top 10 holdings column lists "
                      "each fund's largest positions BY ALLOCATION, ordered "
                      "within the list by today's move."),
               icon="🧱")
        + card(f"🟢 Bullish leaders ({len(longs)})",
               _cand_table(longs, snap, 0)
               + _sub("Sorted by confidence, not by today's move. Click a row "
                      "for levels, headlines and the score breakdown."),
               icon="📈")
        + card(f"🔴 Bearish leaders ({len(shorts)})",
               _cand_table(shorts, snap, 1000)
               + _sub("Rendered at full size even when nothing in it is "
                      "tradeable — 'the best short available is a 47, and "
                      "here is why' is the useful answer."),
               icon="📉")
        + card("Day-trading leaders", _day_table(longs + shorts, snap), icon="⚡")
        + card("Data gaps — stated, not filled", gaps or empty("none recorded"),
               icon="⚠️"))
    return body, _JS


_JS = r"""
/* Row expanders. Every table on this page pairs a data row with a hidden
   detail row that carries data-nosort, so the two travel together through a
   re-sort instead of the detail landing under someone else's row. */
function ldToggle(i){
  var el=document.getElementById('ld-d-'+i);
  if(el) el.style.display = el.style.display==='none' ? '' : 'none';
}

/* Sector holdings: + expands, - collapses. The click is stopped from
   bubbling because the sector row itself is not a toggle and the candidate
   rows are — one shared handler would make the button do two things. */
function ldHold(ev, i){
  ev.stopPropagation();
  var el=document.getElementById('ldh-d-'+i), btn=document.getElementById('ldh-b-'+i);
  if(!el) return;
  var open = el.style.display==='none';
  el.style.display = open ? '' : 'none';
  if(btn) btn.textContent = open ? '\u2212' : '+';   /* minus : plus */
}
function ldHoldAll(open){
  document.querySelectorAll('tr[id^="ldh-d-"]').forEach(function(r){
    r.style.display = open ? '' : 'none';
  });
  document.querySelectorAll('button[id^="ldh-b-"]').forEach(function(b){
    b.textContent = open ? '\u2212' : '+';
  });
}

/* Click-to-sort on every column of every table.

   Two rules make this safe on these tables:
   - a row carrying data-nosort is a detail panel and is never sorted on its
     own; it is carried along behind the row it belongs to.
   - cells are compared numerically when a leading number can be read out of
     them ("83/100" -> 83, "+22.93%" -> 22.93, "10 / 0" -> 10) and as text
     otherwise, so score, percent and name columns all sort sensibly without
     the server having to emit sort keys. */
function ldNum(text){
  var m = String(text).replace(/,/g,'').match(/-?\+?\d+(\.\d+)?/);
  if(!m) return null;
  var v = parseFloat(m[0].replace('+',''));
  return isNaN(v) ? null : v;
}
function ldSort(th, col){
  var table = th.closest('table'), tbody = table.tBodies[0];
  var dir = th.getAttribute('data-dir') === 'asc' ? 'desc' : 'asc';
  table.querySelectorAll('th').forEach(function(h){
    h.removeAttribute('data-dir');
    var a = h.querySelector('.ld-arrow'); if(a) a.innerHTML = '&nbsp;';
  });
  th.setAttribute('data-dir', dir);
  var arrow = th.querySelector('.ld-arrow');
  if(arrow) arrow.textContent = dir === 'asc' ? ' \u25B2' : ' \u25BC';

  /* pair each visible row with the detail row that follows it */
  var rows = [...tbody.rows], groups = [], i = 0;
  while(i < rows.length){
    var group = [rows[i]];
    while(i + 1 < rows.length && rows[i+1].hasAttribute('data-nosort')){
      group.push(rows[i+1]); i++;
    }
    groups.push(group); i++;
  }

  var numeric = groups.every(function(g){
    var c = g[0].cells[col];
    return !c || !c.innerText.trim() || ldNum(c.innerText) !== null;
  });

  groups.sort(function(a, b){
    var ca = a[0].cells[col], cb = b[0].cells[col];
    var ta = ca ? ca.innerText.trim() : '', tb = cb ? cb.innerText.trim() : '';
    var r;
    if(numeric){
      var na = ldNum(ta), nb = ldNum(tb);
      if(na === null && nb === null) r = 0;
      else if(na === null) r = 1;        /* blanks last, both directions */
      else if(nb === null) r = -1;
      else r = na - nb;
      if((na === null) !== (nb === null)) return r;
    } else {
      r = ta.localeCompare(tb, undefined, {numeric:true, sensitivity:'base'});
    }
    return dir === 'asc' ? r : -r;
  });

  groups.forEach(function(g){ g.forEach(function(row){ tbody.appendChild(row); }); });
}

function ldPost(action, extra){
  var body='action='+encodeURIComponent(action);
  for(var k in (extra||{})) body+='&'+k+'='+encodeURIComponent(extra[k]);
  return fetch('/run',{method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json()});
}
function ldRun(){
  ldPost('sector_leaders').then(function(j){
    alert(j.message||'started');
    if(j.ok && window.pollJobs) window.pollJobs();
  });
}
function ldConfirmTop(){
  ldPost('sector_leaders_confirm').then(function(j){
    alert(j.message||'started');
    if(j.ok && window.pollJobs) window.pollJobs();
  });
}
function ldConfirm(t){
  ldPost('sector_leaders_confirm',{tickers:t}).then(function(j){
    alert(j.message||'started');
    if(j.ok && window.pollJobs) window.pollJobs();
  });
}
function ldVerdict(t,dir,v){
  var body='ticker='+encodeURIComponent(t)+'&direction='+encodeURIComponent(dir)
          +'&verdict='+encodeURIComponent(v);
  fetch('/api/leaders/verdict',{method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json()})
    .then(function(j){ if(j.ok){ location.reload(); }
                       else { alert(j.error||'could not save'); } });
}
"""
