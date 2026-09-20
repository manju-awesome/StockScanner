"""
institutional_view.py
=====================
The Institutional Options page. Presentation only — every number comes
from core.institutional, which reads the SEC's bulk Form 13F datasets.

Why this page renders from a snapshot
-------------------------------------
A run streams a ~400 MB information table and may download a ~95 MB zip
first. That is minutes of work, far past any request timeout, so the page
reads a stored run the way /csp does.

The freshness argument is the opposite of /csp's, though, and it shapes
the whole layout. A CSP snapshot decays in hours because it holds live
option prices. This one cannot decay at all: it holds a quarter-end
photograph that will never change again. Re-running it tomorrow returns
identical numbers. So the header reports the age of the DATA — 148 days
and counting — and the generation timestamp is deliberately demoted to a
footnote. Leading with "generated 2 hours ago" would imply a freshness
these filings have never had.

What the layout is arguing
--------------------------
The long-only caveat is a BANNER, not fine print, and it sits above every
number on the page. This is the single most misreadable dataset in the
project: a row marked Put means a filer HELD puts, and 13F has no row
anywhere for a written option or a short position. Read casually, a big
put line says "institutions are betting against this". Read correctly, it
says "someone bought downside, and the counterparty is invisible". Every
previous version of this warning that lived at the bottom of the page was
a version people scrolled past.

For the same reason the put/call ratio is never shown bare. It travels
with `ratio_basis` — the sentence naming exactly which population it
describes — because the number is a ratio of REPORTED LONG POSITIONS
AMONG 13F FILERS, which is a far narrower claim than "the put/call
ratio", and the two are trivially confused.

Names that failed to resolve to a CUSIP are LISTED, not dropped. A ticker
missing from the table would read as "no institution holds options on
it", which is a claim about the market rather than about our plumbing.
"""

from __future__ import annotations

from urllib.parse import urlencode

from stockanalysis.core.institutional import store as IS

from .views import badge, card, empty, esc, tv_url

DASH = "—"

# Ratios above this read as put-heavy, below as call-heavy. Deliberately
# generous either side of 1.0: filer composition alone moves this number,
# and a 1.05 dressed up as "bearish positioning" is noise given a colour.
PUT_HEAVY, CALL_HEAVY = 1.30, 0.77


def _n(v, nd=0, dash=DASH):
    return dash if v is None else f"{v:,.{nd}f}"


def _sub(text, colour="#898781"):
    return (f'<div style="font-size:10px;color:{colour};margin-top:2px">'
            f'{esc(text)}</div>')


def _long_only_banner() -> str:
    """The caveat that has to survive being skimmed."""
    return (
        '<div style="background:#FAEEDA;border:0.5px solid #E4C97E;'
        'border-radius:10px;padding:11px 14px;margin-bottom:16px;'
        'font-size:12px;color:#633806;line-height:1.55">'
        '<strong>These are LONG positions only.</strong> A Put row means the '
        'filer <em>held</em> puts on the period end date — it does not mean '
        'they sold them. Form 13F has no row for a written option or a short '
        'stock position anywhere, so the other side of every trade here is '
        'invisible by construction. Read this as who bought exposure, never '
        'as a market-wide balance.'
        '<div style="margin-top:6px">Also absent: market makers acting as '
        'such, every manager under the $100M discretionary-AUM filing '
        'threshold, and all intra-quarter activity — a position opened and '
        'closed inside the quarter leaves no trace.</div>'
        '</div>')


def _header(snap: dict) -> str:
    note, colour = IS.age_note(snap)
    cov = snap.get("coverage") or {}
    gen = str(snap.get("generated_at") or "")[:16].replace("T", " ")

    pills = "".join([
        badge(note, "warn" if colour == "#b45309" else "info"),
        badge(f'{cov.get("managers", 0):,} managers', "muted"),
        badge(f'{cov.get("option_rows", 0):,} option rows', "muted"),
        badge(f'{cov.get("superseded", 0):,} superseded filings dropped', "muted"),
        badge(f'{cov.get("notices", 0):,} notices (no holdings)', "muted"),
    ])

    # The units gate's own numbers, surfaced rather than buried. A reader
    # who knows 13,155 rows were reinterpreted reads the totals differently
    # from one who thinks every number came straight off a filing.
    gate = (
        f'<div style="font-size:11px;color:#52514e;margin-top:8px;'
        f'line-height:1.5">'
        f'<strong>Units gate:</strong> {cov.get("rescaled_rows", 0):,} rows '
        f'rescaled from contracts to underlying shares, '
        f'{cov.get("unreliable_rows", 0):,} excluded as unreliable. A '
        f'meaningful minority of filers report contracts where the form asks '
        f'for shares — understating by exactly 100× — so every option row is '
        f'checked against the median price implied by that CUSIP\'s share '
        f'rows. Rows matching neither convention are dropped rather than '
        f'guessed at.</div>')

    return (f'<div style="display:flex;gap:6px;flex-wrap:wrap;'
            f'align-items:center">{pills}</div>{gate}'
            f'<div style="font-size:10px;color:#b5b3ad;margin-top:6px">'
            f'source {esc(snap.get("dataset") or "")} · snapshot built '
            f'{esc(gen)} — the filings themselves do not change, so rebuilding '
            f'returns identical numbers until a new quarter publishes</div>')


def _ratio_cell(ratio) -> str:
    if ratio is None:
        return f'<span style="color:#898781">{DASH}</span>'
    if ratio >= PUT_HEAVY:
        colour, word = "#A32D2D", "put-heavy"
    elif ratio <= CALL_HEAVY:
        colour, word = "#0F6E56", "call-heavy"
    else:
        colour, word = "#898781", "balanced"
    return (f'<strong style="color:{colour}">{ratio:,.2f}</strong>'
            f'{_sub(word, colour)}')


def _side_cell(side: dict) -> str:
    if not side or not side.get("holders"):
        return f'<span style="color:#898781">{DASH}</span>'
    return (f'<strong>{side["contracts"]:,.0f}</strong>'
            f'{_sub(f"{side['holders']:,} holders")}')


def _flags_cell(row: dict) -> str:
    bits = []
    if row.get("cusip_status") == "override":
        bits.append('<span style="color:#0C447C">pinned CUSIP</span>')
    conf = row.get("cusip_confidence")
    if row.get("cusip_status") == "resolved" and conf is not None and conf < 0.9:
        bits.append(f'<span style="color:#8a6d1a">CUSIP {conf:.0%}</span>')
    if row.get("rescaled_rows"):
        bits.append(f'{row["rescaled_rows"]} rescaled')
    if row.get("unreliable_rows"):
        bits.append(f'<span style="color:#8a6d1a">'
                    f'{row["unreliable_rows"]} dropped</span>')
    if not bits:
        return f'<span style="color:#d9d7ce">{DASH}</span>'
    return (f'<div style="font-size:10px;color:#898781;line-height:1.5">'
            + "<br>".join(bits) + '</div>')


def _holder_list(side: dict, limit: int = 6) -> str:
    rows = (side or {}).get("top") or []
    if not rows:
        return f'<div style="font-size:11px;color:#898781">{DASH}</div>'
    out = []
    for h in rows[:limit]:
        mark = (' <span style="color:#8a6d1a" title="reported in contracts; '
                'rescaled to underlying shares">↑100×</span>'
                if h.get("units") == "contracts" else "")
        out.append(
            f'<div style="display:flex;justify-content:space-between;gap:10px;'
            f'font-size:11px;padding:2px 0">'
            f'<span style="color:#0b0b0b;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap">'
            f'{esc(h.get("manager") or "?")}{mark}</span>'
            f'<span style="color:#52514e;white-space:nowrap">'
            f'{h.get("contracts", 0):,.0f}</span></div>')
    return "".join(out)


def _detail(row: dict) -> str:
    """Who actually holds it, both sides, side by side."""
    ref = row.get("reference_price")
    head = (f'<div style="font-size:11px;color:#898781;margin-bottom:8px">'
            f'CUSIP {esc(row.get("cusip") or "?")}'
            + (f' · quarter-end reference ${ref:,.2f}' if ref else "")
            + f' · {esc(row.get("ratio_basis") or "")}</div>')
    return (head +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">'
            '<div><div style="font-size:10px;color:#0F6E56;font-weight:700;'
            'text-transform:uppercase;margin-bottom:4px">Largest call '
            'holders</div>' + _holder_list(row.get("calls")) + '</div>'
            '<div><div style="font-size:10px;color:#A32D2D;font-weight:700;'
            'text-transform:uppercase;margin-bottom:4px">Largest put '
            'holders</div>' + _holder_list(row.get("puts")) + '</div>'
            '</div>')


_TD = "padding:8px 10px;border-bottom:0.5px solid #f1efea;vertical-align:top"
_TH = ("padding:7px 10px;font-size:10px;font-weight:600;color:#898781;"
       "text-transform:uppercase;text-align:left;border-bottom:0.5px solid #e1e0d9")


def _table(rows: list[dict]) -> str:
    if not rows:
        return empty("No resolved names in this view.")
    head = ("".join(f'<th style="{_TH};text-align:{a}">{esc(h)}</th>'
                    for h, a in (("Ticker", "left"), ("Issuer", "left"),
                                 ("Calls", "right"), ("Puts", "right"),
                                 ("Put / Call", "right"), ("Notes", "left"))))
    body = []
    for r in rows:
        body.append(
            f'<tr>'
            f'<td style="{_TD}">'
            f'<a href="{tv_url(r["ticker"])}" target="_blank" '
            f'style="font-weight:700;color:#0b0b0b;text-decoration:none">'
            f'{esc(r["ticker"])}</a></td>'
            f'<td style="{_TD};font-size:11px;color:#52514e;max-width:220px;'
            f'overflow:hidden;text-overflow:ellipsis">'
            f'{esc(r.get("issuer") or "")}</td>'
            f'<td style="{_TD};text-align:right">{_side_cell(r.get("calls"))}</td>'
            f'<td style="{_TD};text-align:right">{_side_cell(r.get("puts"))}</td>'
            f'<td style="{_TD};text-align:right">'
            f'{_ratio_cell(r.get("put_call_ratio_contracts"))}</td>'
            f'<td style="{_TD}">{_flags_cell(r)}</td>'
            f'</tr>'
            f'<tr><td colspan="6" style="padding:0 10px 10px;'
            f'border-bottom:0.5px solid #f1efea">'
            f'<details><summary style="cursor:pointer;font-size:11px;'
            f'color:#185FA5;padding:2px 0">Who holds it</summary>'
            f'<div style="padding:8px 0 4px">{_detail(r)}</div>'
            f'</details></td></tr>')
    return (f'<div style="overflow-x:auto"><table style="width:100%;'
            f'border-collapse:collapse"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _board(rows: list[dict], side: str) -> str:
    """Largest reported books by issuer.

    `rescaled` is a column rather than a footnote because a single
    reinterpreted row can carry a whole name into the top fifteen — the
    sample quarter has an Eversource put line that is 100% rescaled and
    ranks 11th globally on the strength of one filing whose units are
    genuinely undecidable.
    """
    if not rows:
        return empty("No leaderboard in this snapshot.")
    out = []
    for i, r in enumerate(rows, 1):
        share = r.get("rescaled_share") or 0
        flag = (f'<span style="color:#8a6d1a">{share:.0%}</span>' if share
                else '<span style="color:#d9d7ce">—</span>')
        out.append(
            f'<tr><td style="{_TD};color:#b5b3ad;width:24px">{i}</td>'
            f'<td style="{_TD};font-size:11px">{esc(r.get("issuer") or "")}'
            f'{_sub(r.get("cusip") or "")}</td>'
            f'<td style="{_TD};text-align:right;font-weight:700">'
            f'{r.get("contracts", 0):,.0f}</td>'
            f'<td style="{_TD};text-align:right;font-size:11px;color:#898781">'
            f'{r.get("holders", 0):,}</td>'
            f'<td style="{_TD};text-align:right;font-size:11px">{flag}</td></tr>')
    colour = "#A32D2D" if side == "Put" else "#0F6E56"
    return (f'<div style="overflow-x:auto"><table style="width:100%;'
            f'border-collapse:collapse"><thead><tr>'
            f'<th style="{_TH}"></th>'
            f'<th style="{_TH}">Issuer</th>'
            f'<th style="{_TH};text-align:right;color:{colour}">Contracts</th>'
            f'<th style="{_TH};text-align:right">Holders</th>'
            f'<th style="{_TH};text-align:right">Rescaled</th>'
            f'</tr></thead><tbody>{"".join(out)}</tbody></table>'
            f'<div style="font-size:10px;color:#898781;margin-top:8px;'
            f'line-height:1.5">Ranked on normalised underlying shares, not on '
            f'filed value — value is the field filers treat most '
            f'inconsistently for options. <strong>Rescaled</strong> is the '
            f'share of the total coming from rows the units gate '
            f'reinterpreted; a high figure means the ranking rests on a '
            f'reading of the filing rather than on the filing as '
            f'written.</div></div>')


def _unresolved_block(rows: list[dict]) -> str:
    if not rows:
        return ""
    items = []
    for r in rows:
        cands = r.get("candidates") or []
        tail = (" · candidates: " + ", ".join(
            f'{c["cusip"]} ({c["rows"]:,} rows)' for c in cands[:3])
            if cands else "")
        items.append(f'<div style="font-size:11px;color:#52514e;padding:2px 0">'
                     f'<strong>{esc(r["ticker"])}</strong> — '
                     f'{esc(r.get("cusip_status") or "?")}{esc(tail)}</div>')
    return card(
        f"Not resolved to a CUSIP ({len(rows)})", "".join(items) +
        '<div style="font-size:10px;color:#898781;margin-top:8px;line-height:1.5">'
        'CUSIP is licensed and the SEC publishes no ticker-to-CUSIP map, so it '
        'is derived by majority vote over normalised issuer names. Dual-class '
        'issuers are reported ambiguous rather than collapsed onto one class — '
        'guessing would attribute one class\'s book to the other. Pin any of '
        'these in <code>data/institutional/cusip_overrides.json</code>; the '
        'candidates above are what to choose between.</div>', "🔍")


def institutional_page(query: dict | None = None) -> tuple[str, str]:
    query = query or {}
    typed = (query.get("tickers") or [""])[0]
    side = (query.get("side") or ["Put"])[0].strip().title()
    if side not in ("Put", "Call"):
        side = "Put"

    snap = IS.load()
    if not snap:
        return (card(
            "No snapshot yet",
            '<div style="font-size:12px;color:#52514e;line-height:1.6">'
            'Nothing has been built for this page yet. The first run downloads '
            'a ~95 MB quarterly dataset from the SEC and streams a ~400 MB '
            'information table, so it runs as a background job rather than on '
            'request.<br><br>From a shell:<br>'
            '<code>python scripts/fetch_13f.py NVDA --json</code></div>', "🏛"), "")

    rows = [r for r in snap.get("tickers") or [] if not r.get("error")]
    unresolved = [r for r in snap.get("tickers") or [] if r.get("error")]

    from .longterm_view import parse_tickers
    wanted = parse_tickers(typed)
    missing = []
    if wanted:
        by_ticker = {r["ticker"]: r for r in rows}
        found = [by_ticker[t] for t in wanted if t in by_ticker]
        missing = [t for t in wanted if t not in by_ticker]
        rows = found
    else:
        # Put-heaviest first. The page's reason to exist is spotting where
        # reported downside positioning concentrates, and an alphabetical
        # default buries that under whatever starts with A.
        rows = sorted(
            [r for r in rows
             if (r.get("puts") or {}).get("holders")
             or (r.get("calls") or {}).get("holders")],
            key=lambda r: (r.get("put_call_ratio_contracts") or 0,
                           (r.get("puts") or {}).get("contracts") or 0),
            reverse=True)

    def link(**params):
        state = {"tickers": typed.strip(), "side": "" if side == "Put" else side}
        state.update({k: ("" if v is None else str(v)) for k, v in params.items()})
        kept = {k: v for k, v in state.items() if v}
        return "/institutional" + (f"?{urlencode(kept)}" if kept else "")

    search = (
        f'<form method="get" action="/institutional" '
        f'style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
        f'<input name="tickers" value="{esc(typed)}" placeholder="NVDA AAPL SPY" '
        f'style="flex:1;min-width:200px;padding:7px 10px;font-size:12px;'
        f'border:0.5px solid #d9d7ce;border-radius:8px">'
        f'<button style="padding:7px 14px;font-size:12px;font-weight:600;'
        f'background:#0b0b0b;color:white;border:0;border-radius:8px;'
        f'cursor:pointer">Look up</button>'
        + (f'<a href="/institutional" style="font-size:11px;color:#185FA5">'
           f'clear</a>' if typed.strip() else "")
        + '</form>')

    miss = ""
    if missing:
        miss = (f'<div style="font-size:11px;color:#8a6d1a;margin-top:8px">'
                f'Not in this snapshot: {esc(", ".join(missing))} — either the '
                f'universe did not cover it, or its CUSIP is unresolved '
                f'(listed below).</div>')

    tabs = "".join(
        f'<a href="{link(side="" if s == "Put" else s)}" '
        f'style="padding:5px 12px;font-size:11px;font-weight:600;'
        f'border-radius:7px;text-decoration:none;'
        f'background:{"#0b0b0b" if s == side else "white"};'
        f'color:{"white" if s == side else "#444441"};'
        f'border:0.5px solid {"#0b0b0b" if s == side else "#d9d7ce"}">'
        f'Largest {s.lower()} books</a>' for s in ("Put", "Call"))

    board_rows = snap.get(f"leaderboard_{side.lower()}") or []

    # Deliberately understated. Rebuilding is a ~95 MB download and a
    # 400 MB streaming pass that returns IDENTICAL numbers until the SEC
    # publishes the next quarter — this is not a refresh button in the sense
    # every other page uses the word, and styling it like one would invite
    # people to press it daily for nothing.
    rebuild = (
        '<form onsubmit="return submitJob(event, this)" '
        'style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
        '<input type="hidden" name="action" value="institutional">'
        '<input name="tickers" placeholder="tickers (blank = all watchlists)" '
        'style="flex:1;min-width:220px;padding:6px 9px;font-size:11px;'
        'border:0.5px solid #d9d7ce;border-radius:7px">'
        '<button style="padding:6px 12px;font-size:11px;font-weight:600;'
        'background:white;color:#444441;border:0.5px solid #d9d7ce;'
        'border-radius:7px;cursor:pointer">Rebuild snapshot</button>'
        '<span style="font-size:10px;color:#898781">only produces new numbers '
        'once the SEC publishes the next quarter</span></form>')

    body = (
        _long_only_banner()
        + card("Coverage", _header(snap) +
               '<div style="margin-top:12px;padding-top:12px;'
               'border-top:0.5px solid #f1efea">' + rebuild + '</div>', "🏛")
        + card(f"By ticker ({len(rows)})", search + miss +
               '<div style="margin-top:12px">' + _table(rows) + '</div>', "📊")
        + card("Largest reported books",
               f'<div style="display:flex;gap:6px;margin-bottom:12px">{tabs}</div>'
               + _board(board_rows, side), "🏆")
        + _unresolved_block(unresolved))
    # (body, extra_js) — the page title comes from the ROUTES entry, not
    # from here. Returning (title, body) renders the title AS the body.
    return body, ""
