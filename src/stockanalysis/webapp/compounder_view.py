"""
compounder_view.py — the Future Compounder page.
================================================
Presentation only. Every score, band, stage and sentence comes from
core.compounder; this file decides what a reader sees first and nothing
else.

What the layout is arguing
--------------------------
The 10-Year Watchlist sits at the top and the full ranked table below it,
because the brief's deliverable is the twenty names — the rest of the
library is the audit trail that shows the twenty were not cherry-picked.

Every row leads with STAGE next to the score, and that pairing is the most
deliberate thing on the page. A 74 at Stage 1 and a 74 at Stage 4 are
different investments with different position sizes, and a table sorted by
score alone invites reading them as interchangeable. Putting the stage in
the same visual unit as the number stops that.

Risk flags are shown on the row, not hidden in the detail. This engine is
forbidden from rejecting a company for burning cash or having no coverage,
which means its list WILL contain companies a conventional screen would
have thrown out — and a page that scores them 80 without saying why they
would have been rejected is not classifying the risk, it is concealing it.

The valuation note appears on every card for the same reason. This engine
has no opinion about price, and a reader who takes a 10-year watchlist as a
buy list has been misled by the omission rather than by anything stated.
"""

from __future__ import annotations

from stockanalysis.core.compounder import engine as CE
from stockanalysis.core.compounder import store as CS
from stockanalysis.core.compounder import themes as TH

from .views import badge, card, empty, esc, tv_url

DASH = "—"

_TIER_STATUS = {"CONVICTION": "good", "STRONG CANDIDATE": "good",
                "WATCH": "watch", "EARLY / UNPROVEN": "info",
                "NO CASE YET": "muted", "UNSCORED": "muted"}

_RISK_COLOUR = {"MATERIAL": "#A32D2D", "CLASSIFIED": "#8a6d1a",
                "DATA": "#898781"}

_STAGE_COLOUR = {1: "#185FA5", 2: "#0F6E56", 3: "#0F6E56", 4: "#8a6d1a",
                 5: "#898781"}


def _n(v, nd=1, dash=DASH, suffix=""):
    return dash if v is None else f"{v:,.{nd}f}{suffix}"


def _money(v) -> str:
    if v is None:
        return DASH
    if abs(v) >= 1e12:
        return f"${v / 1e12:.1f}T"
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _sub(text, colour="#898781", size="10px"):
    return (f'<div style="font-size:{size};color:{colour};margin-top:2px">'
            f'{esc(text)}</div>')


def _score_cell(v, big=False):
    if v is None:
        return f'<span style="color:#b5b3ad">{DASH}</span>'
    colour = ("#0F6E56" if v >= 68 else "#8a6d1a" if v >= 55
              else "#898781" if v >= 45 else "#A32D2D")
    size = "17px" if big else "13px"
    return (f'<span style="font-weight:700;color:{colour};font-size:{size}">'
            f'{v:.0f}</span>'
            f'<span style="font-size:9px;color:#b5b3ad">/100</span>')


def _stage_pill(st):
    n = (st or {}).get("stage")
    if n is None:
        return badge("stage ?", "muted", "small")
    colour = _STAGE_COLOUR.get(n, "#898781")
    return (f'<span style="font-size:10px;font-weight:600;color:{colour};'
            f'border:1px solid {colour}33;background:{colour}11;'
            f'padding:2px 7px;border-radius:5px;white-space:nowrap">'
            f'{esc(st.get("icon", ""))} S{n} {esc(st.get("label", ""))}</span>')


def _pp(v, nd=1, expected_fade=False):
    """A percentage-point change, signed and coloured.

    `expected_fade` paints a negative reading amber rather than red. The
    engine draws a real distinction between a company fading from an
    arithmetically unsustainable base (157% down from 206%) and one bending
    down from an ordinary rate (14% to 4%), and painting both red in the
    column most readers scan would throw that distinction away at exactly
    the moment it matters.
    """
    if v is None:
        return f'<span style="color:#b5b3ad">{DASH}</span>'
    if v > 0:
        colour = "#0F6E56"
    elif v < 0:
        colour = "#8a6d1a" if expected_fade else "#A32D2D"
    else:
        colour = "#898781"
    title = " title=\"fade from a high base — arithmetic, not deterioration\"" \
        if expected_fade else ""
    return f'<span style="color:{colour}"{title}>{v:+.{nd}f}pp</span>'


# ─────────────────────────────────────────────────────────────────────────
# DETAIL — the nine narrative blocks plus the full field list
# ─────────────────────────────────────────────────────────────────────────

def _bullets(items, colour="#3d3d3a"):
    if not items:
        return _sub("nothing recorded")
    return ('<ul style="margin:4px 0 0 16px;padding:0;font-size:11px;'
            f'color:{colour};line-height:1.65">'
            + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>")


def _fields(r):
    """The framework's full output field list, as a definition grid.

    Everything the brief asks to return, in one place, so the page can be
    read as the specified deliverable rather than as a summary of it.
    """
    g, lev = r.get("growth") or {}, r.get("leverage") or {}
    opp, moat = r.get("opportunity") or {}, r.get("moat") or {}
    comp, surv = r.get("competitive") or {}, r.get("survivability") or {}
    mgmt, disc = r.get("management") or {}, r.get("discovery") or {}
    reinv, st = r.get("reinvestment") or {}, r.get("stage") or {}

    rows = [
        ("Market cap", _money(r.get("market_cap")) + f" — {r.get('cap_band')}"),
        ("Sector", r.get("sector") or DASH),
        ("Secular theme", r.get("theme_label") or "none mapped"),
        ("Stage", f"{st.get('icon', '')} {st.get('label', DASH)}"),
        ("TAM (now → 5y → 10y)",
         f"{_money(opp.get('tam_now'))} → {_money(opp.get('tam_5y'))} → "
         f"{_money(opp.get('tam_10y'))}"),
        ("TAM CAGR", f"{_n(opp.get('tam_cagr_5y'), 1, suffix='%')} over 5y, "
                     f"{_n(opp.get('tam_cagr_10y'), 1, suffix='%')} over 10y"),
        ("Revenue CAGR", f"3y {_n(g.get('cagr_3y'), 1, suffix='%')}, "
                         f"2y {_n(g.get('cagr_2y'), 1, suffix='%')}"),
        ("Latest growth", _n(g.get("latest"), 0, suffix="% YoY")),
        ("Growth acceleration",
         f"{_n(g.get('accel_pp'), 1, suffix='pp')} — {g.get('state', DASH)}"),
        ("Gross margin", _n(lev.get("gross_margin_now"), 1, suffix="%")),
        ("Margin trend",
         f"gross {_n(lev.get('gross_margin_trend_pp'), 1, suffix='pp')}, "
         f"operating {_n(lev.get('operating_margin_trend_pp'), 1, suffix='pp')}"),
        ("R&D %", _n(reinv.get("rnd_pct"), 1, suffix="% of revenue")),
        ("Moat formation score",
         f"{_n(moat.get('score'), 0)}/100 — {moat.get('state', DASH)}"),
        ("Market share",
         f"{_n(opp.get('share_pct'), 3, suffix='% of theme TAM')}"),
        ("Competitive position",
         f"#{comp.get('rank') or DASH} of {comp.get('peer_count') or DASH} "
         f"tracked — {(comp.get('top3_path') or {}).get('verdict', DASH)}"),
        ("Balance sheet",
         f"{surv.get('icon', '')} {surv.get('classification', DASH)} — cash "
         f"{_money(surv.get('cash'))}, debt {_money(surv.get('debt'))}"),
        ("Dilution risk",
         f"{(surv.get('dilution_risk') or {}).get('icon', '')} "
         f"{(surv.get('dilution_risk') or {}).get('level', DASH)} — "
         f"{(surv.get('dilution_risk') or {}).get('detail', '')}"),
        ("Management score", f"{_n(mgmt.get('score'), 0)}/100"),
        ("Market discovery",
         f"{_n(disc.get('score'), 0)}/100 — {disc.get('state', DASH)}"),
        ("FUTURE COMPOUNDER SCORE",
         f"{_n(r.get('score'), 0)}/100 — {r.get('tier', DASH)}"),
    ]
    cells = "".join(
        f'<div style="display:flex;gap:8px;padding:3px 0;'
        f'border-bottom:0.5px solid #f1efea">'
        f'<div style="min-width:150px;font-size:10px;color:#898781;'
        f'text-transform:uppercase;letter-spacing:.03em">{esc(k)}</div>'
        f'<div style="font-size:11px;color:#3d3d3a">{esc(v)}</div></div>'
        for k, v in rows)
    return f'<div style="margin-top:4px">{cells}</div>'


def _conditions(conds):
    if not conds:
        return _sub("no conditions recorded")
    out = []
    for c in conds:
        met = c.get("met")
        icon = "✅" if met else "⬜" if met is False else "❔"
        colour = "#0F6E56" if met else "#898781"
        out.append(
            f'<div style="font-size:11px;color:{colour};padding:2px 0">'
            f'{icon} {esc(c.get("label", ""))} '
            f'<span style="color:#b5b3ad">— now {esc(str(c.get("current")))}, '
            f'needs {esc(str(c.get("needed")))}</span></div>')
    return "".join(out)


def _metrics_table(metrics):
    if not metrics:
        return _sub("no metrics recorded")
    head = ('<tr style="font-size:9px;color:#898781;text-align:left">'
            '<th style="padding:3px 6px">Metric</th>'
            '<th style="padding:3px 6px">Now</th>'
            '<th style="padding:3px 6px">What would change the view</th></tr>')
    body = "".join(
        f'<tr style="border-top:0.5px solid #f1efea">'
        f'<td style="padding:4px 6px;font-size:11px;font-weight:600">'
        f'{esc(m["metric"])}</td>'
        f'<td style="padding:4px 6px;font-size:11px;white-space:nowrap">'
        f'{esc(str(m["now"]))}</td>'
        f'<td style="padding:4px 6px;font-size:10px;color:#898781">'
        f'{esc(m["watch_for"])}</td></tr>' for m in metrics)
    return (f'<table style="width:100%;border-collapse:collapse">'
            f'{head}{body}</table>')


def _components(r):
    """The composite's own arithmetic — every leg, its weight and score."""
    comps = r.get("components") or []
    if not comps:
        return _sub("nothing was measurable")
    rows = []
    for c in comps:
        pct = max(0.0, min(100.0, c["score"]))
        rows.append(
            f'<div style="display:flex;align-items:center;gap:8px;'
            f'padding:2px 0">'
            f'<div style="min-width:150px;font-size:10px;color:#3d3d3a">'
            f'{esc(c["name"])} <span style="color:#b5b3ad">'
            f'{c["weight"]}%</span></div>'
            f'<div style="height:5px;background:#eceae4;border-radius:3px;'
            f'width:90px"><div style="height:5px;width:{pct * 0.9:.0f}px;'
            f'background:#185FA5;border-radius:3px"></div></div>'
            f'<div style="font-size:10px;font-weight:600;min-width:26px">'
            f'{c["score"]:.0f}</div>'
            f'<div style="font-size:10px;color:#898781;flex:1">'
            f'{esc(c["detail"][:110])}</div></div>')
    missing = r.get("missing") or []
    note = ""
    if missing:
        note = _sub(f"Not measurable, and renormalised out rather than "
                    f"scored zero: {', '.join(missing)}")
    return "".join(rows) + note


def _unmeasured_block(r):
    """Every input the framework asks for that has no source in this data.

    Shown rather than left implicit. A reader who does not know backlog and
    customer growth were never checked will assume they were, and a growth
    read that silently omits both is a stronger claim than the engine can
    support.
    """
    seen, out = set(), []
    for key in ("growth", "reinvestment", "management", "competitive",
                "discovery", "leverage"):
        for item in (r.get(key) or {}).get("unmeasured") or []:
            if item not in seen:
                seen.add(item)
                out.append(item)
    if not out:
        return ""
    return (f'<div style="font-size:11px;color:#898781;margin-top:4px;'
            f'line-height:1.6">Asked for by the framework, no source in this '
            f'data, and <b>not</b> approximated: {esc(", ".join(out))}.</div>')


def _risk_block(flags):
    if not flags:
        return _sub("no classified risks — which for a company this size is "
                    "a statement about the available data as much as about "
                    "the company")
    out = []
    for fl in flags:
        colour = _RISK_COLOUR.get(fl.get("level"), "#898781")
        out.append(
            f'<div style="padding:4px 0;border-top:0.5px solid #f1efea">'
            f'<span style="font-size:11px;font-weight:600;color:{colour}">'
            f'{esc(fl["flag"])}</span> '
            f'<span style="font-size:9px;color:#b5b3ad">{esc(fl["level"])}'
            f'</span>{_sub(fl.get("detail") or "")}</div>')
    return "".join(out)


def _detail(r, idx):
    nar = r.get("narrative") or {}
    comp = nar.get("biggest_competitor") or {}
    trans = nar.get("stage_transition") or {}
    theme = r.get("theme") or {}

    def block(title, body):
        return (f'<div style="margin-top:12px">'
                f'<div style="font-size:10px;font-weight:700;color:#0b0b0b;'
                f'text-transform:uppercase;letter-spacing:.04em">'
                f'{esc(title)}</div>{body}</div>')

    competitor = (
        f'<div style="font-size:11px;color:#3d3d3a;margin-top:4px">'
        f'<b>Outside the band:</b> {esc(comp.get("incumbent") or DASH)}</div>'
        f'<div style="font-size:11px;color:#3d3d3a;margin-top:3px">'
        f'<b>Inside the library:</b> #{comp.get("rank") or DASH} of '
        f'{comp.get("of") or DASH} — {esc(comp.get("structure") or "")}</div>'
        f'<div style="font-size:11px;color:#3d3d3a;margin-top:3px">'
        f'<b>Top-3 path:</b> {esc((comp.get("top3") or {}).get("verdict") or DASH)}'
        f' — {esc((comp.get("top3") or {}).get("why") or "")}</div>'
        + _sub(comp.get("caveat") or ""))

    transition = (
        f'<div style="font-size:11px;color:#3d3d3a;margin-top:4px">'
        f'{esc(trans.get("from") or "")} → <b>{esc(trans.get("to") or "")}</b>'
        f'</div>{_sub(trans.get("summary") or "")}'
        f'{_conditions(trans.get("conditions"))}')

    theme_block = ""
    if theme:
        theme_block = (
            f'<div style="font-size:11px;color:#3d3d3a;margin-top:4px">'
            f'{esc(theme.get("label", ""))} — {esc(theme.get("basis", ""))}'
            f'</div>'
            + _sub(f'TAM confidence {theme.get("confidence")} '
                   f'(as of {theme.get("as_of")}). '
                   f'{"Score capped for confidence. " if (r.get("secular") or {}).get("capped") else ""}'
                   f'Adjacent markets: '
                   f'{", ".join(theme.get("adjacent_markets") or []) or "none"}'))

    return (
        f'<div id="fc-d-{idx}" style="display:none;padding:12px 14px;'
        f'background:#fbfaf7;border-top:0.5px solid #e1e0d9">'
        + block("Score arithmetic", _components(r))
        + block("Classified risks — what a conventional screen would have "
                "rejected on", _risk_block(r.get("risk_flags")))
        + block("What was not measured", _unmeasured_block(r))
        + block("Why this could become a major company",
                _bullets(nar.get("why_major")))
        + block("What has to go right",
                _bullets(nar.get("what_has_to_go_right")))
        + block("What could destroy the thesis",
                _bullets(nar.get("what_destroys_it")))
        + block("Biggest competitor", competitor)
        + block("Key catalysts", _bullets(nar.get("catalysts")))
        + block("Key metrics to monitor",
                _metrics_table(nar.get("metrics_to_monitor")))
        + block("Current stage",
                f'<div style="font-size:11px;color:#3d3d3a;margin-top:4px">'
                f'{esc((nar.get("current_stage") or {}).get("why") or "")}</div>')
        + block(f'What would move it to {esc(trans.get("to") or "the next stage")}',
                transition)
        + block("What would invalidate the thesis",
                _bullets(nar.get("invalidation"), "#A32D2D"))
        + block("The secular theme", theme_block)
        + block("Full field list", _fields(r))
        + '</div>')


# ─────────────────────────────────────────────────────────────────────────
# TABLE
# ─────────────────────────────────────────────────────────────────────────

# (label, sort key, type). "num" sorts numerically with missing values
# always last; "text" sorts case-insensitively. The blank first column is
# the expand caret and is not sortable.
_COLS = (("", None, None),
         ("Ticker", "ticker", "text"),
         ("Company", "name", "text"),
         ("Cap", "cap", "num"),
         ("Theme", "theme", "text"),
         ("Stage", "stage", "num"),
         ("Growth", "growth", "num"),
         ("Accel", "accel", "num"),
         ("GM", "gm", "num"),
         ("Moat", "moat", "num"),
         ("Share", "share", "num"),
         ("Funding", "funding", "text"),
         ("Disc", "disc", "text"),
         ("Score", "score", "num"))

_HEAD = tuple(c[0] for c in _COLS)


def _d(v):
    """A sort value for a data attribute. Empty string means "not measured",
    which the sorter pushes to the bottom in BOTH directions rather than
    treating as zero — the same rule the scoring engine follows, and the
    reason an unmeasured margin must not sort as the worst margin."""
    return "" if v is None else str(v)


def _table(rows, offset=0, detail=True, table_id="fc-t"):
    """`detail=False` renders rows only, with the ticker linking to its own
    filtered card.

    A full detail block — the nine narrative sections plus the field grid —
    is around 35KB of HTML. Inlining one per row across the whole library
    produced a 5MB page that took seconds to paint for content the reader
    opens two or three of. So the watchlist and any filtered view carry
    their detail inline, and the full ranking links out.

    Every row carries `data-*` sort/filter values so the column headers and
    the filter bar can work client-side. That is deliberate: the page is a
    stored snapshot, so re-sorting is a rearrangement of data already in the
    browser and a round-trip to the server would buy nothing.
    """
    if not rows:
        return empty("Nothing here.")

    head_cells = []
    for label, key, kind in _COLS:
        if not key:
            head_cells.append('<th style="padding:5px 6px"></th>')
            continue
        head_cells.append(
            f'<th onclick="fcSort(\'{table_id}\',\'{key}\',\'{kind}\')" '
            f'data-key="{key}" '
            f'style="padding:5px 6px;font-size:9px;color:#898781;'
            f'text-align:left;font-weight:600;white-space:nowrap;'
            f'cursor:pointer;user-select:none" '
            f'title="Sort by {esc(label)}">{esc(label)}'
            f'<span class="fc-arrow" style="color:#d9d7ce"> ⇅</span></th>')
    head = "".join(head_cells)

    body = []
    for i, r in enumerate(rows):
        idx = offset + i
        g, lev = r.get("growth") or {}, r.get("leverage") or {}
        opp, moat = r.get("opportunity") or {}, r.get("moat") or {}
        surv, disc = r.get("survivability") or {}, r.get("discovery") or {}
        flags = [f_ for f_ in (r.get("risk_flags") or [])
                 if f_.get("level") == "MATERIAL"]

        click = (f'onclick="fcToggle({idx})"' if detail
                 else f"onclick=\"location.href='/compounder?tickers="
                      f"{r['ticker']}'\"")
        st = r.get("stage") or {}
        # Sort/filter payload. Values are the RAW numbers, never the
        # formatted cell text — "$48.47B" and "$310M" sort backwards as
        # strings, and "—" would sort between them.
        attrs = (
            f' class="fc-row" data-row="{idx}"'
            f' data-ticker="{esc(r.get("ticker") or "")}"'
            f' data-name="{esc((r.get("name") or "").lower())}"'
            f' data-cap="{_d(r.get("market_cap"))}"'
            f' data-theme="{esc(r.get("theme_label") or "")}"'
            f' data-stage="{_d(st.get("stage"))}"'
            f' data-stagelabel="{esc(st.get("label") or "")}"'
            f' data-growth="{_d(g.get("latest"))}"'
            f' data-accel="{_d(g.get("accel_pp"))}"'
            f' data-gm="{_d(lev.get("gross_margin_now"))}"'
            f' data-moat="{_d(moat.get("score"))}"'
            f' data-share="{_d(opp.get("share_pct"))}"'
            f' data-funding="{esc(surv.get("classification") or "")}"'
            f' data-disc="{esc(disc.get("state") or "")}"'
            f' data-band="{esc(r.get("cap_band") or "")}"'
            # Stage 1-2 as one flag rather than two select values: the header
            # counts them together ("61 at stage 1-2") because they are one
            # idea — a company still proving the thing works — and a facet
            # select can only match one value at a time.
            f' data-early="{1 if (st.get("stage") or 9) <= 2 else 0}"'
            f' data-tier="{esc(r.get("tier") or "")}"'
            f' data-risks="{len(flags)}"'
            f' data-score="{_d(r.get("score"))}"')
        body.append(
            f'<tr{attrs} style="border-top:0.5px solid #f1efea;cursor:pointer" '
            f'{click}>'
            f'<td style="padding:6px 6px;color:#b5b3ad;font-size:10px">'
            f'{"▸" if detail else "→"}</td>'
            f'<td style="padding:6px 6px"><a href="{tv_url(r["ticker"])}" '
            f'target="_blank" onclick="event.stopPropagation()" '
            f'style="font-weight:700;font-size:12px;color:#185FA5;'
            f'text-decoration:none">{esc(r["ticker"])}</a></td>'
            f'<td style="padding:6px 6px;font-size:11px;max-width:150px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{esc(r.get("name") or "")}</td>'
            f'<td style="padding:6px 6px;font-size:11px;white-space:nowrap">'
            f'{_money(r.get("market_cap"))}'
            + (f'{_sub(r.get("cap_band"), "#8a6d1a", "9px")}'
               if r.get("cap_band") != "IN BAND" else "")
            + f'</td>'
            f'<td style="padding:6px 6px;font-size:10px;color:#898781;'
            f'max-width:130px">{esc(r.get("theme_label") or DASH)}</td>'
            f'<td style="padding:6px 6px">{_stage_pill(r.get("stage"))}</td>'
            f'<td style="padding:6px 6px;font-size:11px;white-space:nowrap">'
            f'{_n(g.get("latest"), 0, suffix="%")}</td>'
            f'<td style="padding:6px 6px;font-size:11px;white-space:nowrap">'
            f'{_pp(g.get("accel_pp"), expected_fade=bool(g.get("expected_fade")))}</td>'
            f'<td style="padding:6px 6px;font-size:11px;white-space:nowrap">'
            f'{_n(lev.get("gross_margin_now"), 0, suffix="%")}'
            f'{_sub(_fmt_pp(lev.get("gross_margin_trend_pp")), "#b5b3ad", "9px")}'
            f'</td>'
            f'<td style="padding:6px 6px">{_score_cell(moat.get("score"))}</td>'
            f'<td style="padding:6px 6px;font-size:10px;white-space:nowrap">'
            f'{_n(opp.get("share_pct"), 2, suffix="%")}</td>'
            f'<td style="padding:6px 6px;font-size:10px;white-space:nowrap">'
            f'{esc(surv.get("icon", ""))} '
            f'{esc((surv.get("classification") or "").title())}</td>'
            f'<td style="padding:6px 6px;font-size:10px;color:#898781;'
            f'white-space:nowrap">{esc((disc.get("state") or "").title())}</td>'
            f'<td style="padding:6px 6px">{_score_cell(r.get("score"), True)}'
            + (f'{_sub(str(len(flags)) + " material risk" + ("s" if len(flags) != 1 else ""), "#A32D2D", "9px")}'
               if flags else "")
            + '</td></tr>'
            # The detail row is tagged with its parent so the sorter moves
            # the pair together — sorting the summary rows while leaving the
            # expanded panels behind would attach every open card to the
            # wrong company.
            + (f'<tr class="fc-detail" data-for="{idx}">'
               f'<td colspan="{len(_HEAD)}" style="padding:0">'
               f'{_detail(r, idx)}</td></tr>' if detail else ""))

    return (f'<div style="overflow-x:auto"><table id="{table_id}" '
            f'style="width:100%;'
            f'border-collapse:collapse"><tr>{head}</tr>'
            + "".join(body) + '</table></div>')


def _fmt_pp(v):
    return DASH if v is None else f"{v:+.1f}pp"


# ─────────────────────────────────────────────────────────────────────────
# FILTERS
# ─────────────────────────────────────────────────────────────────────────
# Options are built from the values PRESENT in the rows being shown, not
# from the full set the engine can emit. A dropdown offering "DISTRESSED"
# when nothing in the table is distressed invites a click that returns an
# empty table and reads as a broken filter; one that offers only what is
# there tells the reader what the library actually contains.

# (data attribute, label, sort key for ordering the options)
_FACETS = (("stagelabel", "Stage", "stage"),
           ("theme", "Theme", None),
           ("funding", "Funding", None),
           ("tier", "Tier", None),
           ("disc", "Discovery", None),
           ("band", "Cap band", None))


def _facet_values(rows, attr):
    """Distinct values for a facet, with the count of rows carrying each."""
    counts: dict[str, int] = {}
    order: dict[str, float] = {}
    for r in rows:
        if attr == "stagelabel":
            val = (r.get("stage") or {}).get("label")
            order[val or ""] = (r.get("stage") or {}).get("stage") or 9
        elif attr == "theme":
            val = r.get("theme_label")
        elif attr == "funding":
            val = (r.get("survivability") or {}).get("classification")
        elif attr == "tier":
            val = r.get("tier")
        elif attr == "disc":
            val = (r.get("discovery") or {}).get("state")
        else:
            val = r.get("cap_band")
        if val:
            counts[val] = counts.get(val, 0) + 1
    if attr == "stagelabel":
        keys = sorted(counts, key=lambda k: order.get(k, 9))
    else:
        keys = sorted(counts, key=lambda k: (-counts[k], k))
    return [(k, counts[k]) for k in keys]


def _filter_bar(rows, table_id):
    """Facet dropdowns plus a free-text box and a minimum-score slider."""
    if len(rows) < 2:
        return ""

    selects = []
    for attr, label, _ in _FACETS:
        vals = _facet_values(rows, attr)
        if len(vals) < 2:
            continue            # a facet with one value filters nothing
        opts = "".join(
            f'<option value="{esc(v)}">{esc(v.title() if attr == "funding" else v)}'
            f' ({n})</option>' for v, n in vals)
        selects.append(
            f'<select data-attr="{attr}" onchange="fcFilter(\'{table_id}\')" '
            f'style="padding:5px 7px;border:1px solid #d9d7ce;'
            f'border-radius:6px;font-size:11px;background:white;max-width:210px">'
            f'<option value="">{esc(label)}: all</option>{opts}</select>')

    return (
        f'<div id="{table_id}-filters" style="display:flex;gap:6px;'
        f'flex-wrap:wrap;align-items:center;margin-bottom:8px">'
        + "".join(selects)
        + f'<input data-attr="_text" oninput="fcFilter(\'{table_id}\')" '
          f'placeholder="ticker or company" style="padding:5px 8px;'
          f'border:1px solid #d9d7ce;border-radius:6px;font-size:11px;'
          f'min-width:130px">'
        + f'<label style="font-size:11px;color:#898781;display:flex;gap:5px;'
          f'align-items:center">min score '
          f'<input type="range" data-attr="_minscore" min="0" max="100" '
          f'value="0" step="5" oninput="fcFilter(\'{table_id}\')" '
          f'style="width:90px"> '
          f'<span id="{table_id}-minscore" style="font-weight:600;'
          f'min-width:14px">0</span></label>'
        + f'<label style="font-size:11px;color:#898781;display:flex;gap:4px;'
          f'align-items:center"><input type="checkbox" data-attr="_norisk" '
          f'onchange="fcFilter(\'{table_id}\')"> hide names with material '
          f'risks</label>'
        # A real control rather than a hidden state the header badge sets
        # behind the reader's back: clicking "61 at stage 1–2" has to leave
        # something on screen explaining why 61 of 141 rows are showing, and
        # something to untick.
        + f'<label style="font-size:11px;color:#898781;display:flex;gap:4px;'
          f'align-items:center"><input type="checkbox" data-attr="_early" '
          f'onchange="fcFilter(\'{table_id}\')"> stage 1–2 only</label>'
        + f'<button type="button" onclick="fcReset(\'{table_id}\')" '
          f'style="padding:5px 10px;border:1px solid #d9d7ce;border-radius:6px;'
          f'font-size:11px;background:white;cursor:pointer">Reset</button>'
        + f'<span id="{table_id}-count" style="font-size:11px;color:#898781">'
          f'</span></div>')


# ─────────────────────────────────────────────────────────────────────────
# PAGE
# ─────────────────────────────────────────────────────────────────────────

_THESIS = (
    '<div style="font-size:11px;color:#898781;line-height:1.65">'
    'This engine answers one question — <b>could this become a major company '
    'in ten years</b> — and it is the only engine here that is forbidden from '
    'rejecting a company for being unprofitable, cash-burning, uncovered, '
    'un-indexed or small. Those are the normal conditions of the population '
    'it searches, so each one is <b>classified as a risk</b> and the ranking '
    'continues. Every row shows what a conventional screen would have thrown '
    'it out for.<br><br>'
    '<b>Growth acceleration outweighs growth level.</b> A company bending '
    'from 12% to 34% outranks one fading from 60% to 45%, and that inversion '
    'is the point: CAGR is an average, and an average cannot see a bend.<br><br>'
    '<b>Low market share is an asset here.</b> 0.4% of a $50B market growing '
    '20% has room that 34% of a $2B market growing 6% does not, and no other '
    'metric on the page separates them.<br><br>'
    '<b>TAM is a curated claim, not a computed number.</b> It comes from a '
    'versioned theme library with a source, an as-of date and a confidence '
    'level — never estimated per company at scan time — and LOW-confidence '
    'themes are capped so a speculative market\'s huge CAGR cannot outrank a '
    'real one\'s measured growth.<br><br>'
    '<b>There is no valuation in this score.</b> Deliberately: paying 14x '
    'sales for a company that becomes a $50B business works out and 4x for '
    'one that does not, does not. This is a research ranking, not a buy list '
    '— take a name to <a href="/longterm" style="color:#185FA5">/longterm</a> '
    'for the price and entry question.</div>')


# Header count -> the filter that reproduces it, as (facet attr, value).
# `_all` clears everything; `_early` is the pseudo-facet defined on the row
# above. The keys are also URL values (`/compounder?focus=conviction`), so a
# filtered view is linkable and survives a reload.
_FOCUS = {
    "all":        ("_all", ""),
    "in-band":    ("band", "IN BAND"),
    "graduated":  ("band", "GRADUATED"),
    "conviction": ("tier", "CONVICTION"),
    "strong":     ("tier", "STRONG CANDIDATE"),
    "watch":      ("tier", "WATCH"),
    "early":      ("_early", "1"),
}


def _count_badge(text, status, focus_key) -> str:
    """A header count that shows you the rows it counted.

    The click is handled in JS when the full table is on the page, and falls
    through to a real navigation when it is not — which is the case whenever
    a ticker filter is active, since that view renders `fc-sel` and no filter
    bar. Same href either way, so the counts stay linkable and a middle-click
    or a bookmark behaves the way the underline promises.
    """
    inner = badge(text, status)
    return (f'<a href="/compounder?focus={focus_key}" '
            f'onclick="return fcFocus(\'{focus_key}\')" '
            f'title="Show these {esc(text)}" '
            f'style="text-decoration:none;cursor:pointer">{inner}</a>')


def _header(snap, rows):
    status, note = CS.age_note(snap)
    counts = CE.counts(rows)
    stages = CE.stage_counts(rows)
    in_band = sum(1 for r in rows if r.get("cap_band") == "IN BAND")
    grads = sum(1 for r in rows if r.get("cap_band") == "GRADUATED")

    bits = [_count_badge(f"{len(rows)} companies", "muted", "all"),
            _count_badge(f"{in_band} in the $300M–$20B band", "info",
                         "in-band")]
    if grads:
        bits.append(_count_badge(f"{grads} graduated above $20B", "watch",
                                 "graduated"))
    for tier_name, key in (("CONVICTION", "conviction"),
                           ("STRONG CANDIDATE", "strong"),
                           ("WATCH", "watch")):
        if counts.get(tier_name):
            bits.append(_count_badge(f"{counts[tier_name]} {tier_name.lower()}",
                                     _TIER_STATUS.get(tier_name, "muted"), key))
    early = sum(v for k, v in stages.items()
                if k in ("DISCOVERY", "VALIDATION"))
    if early:
        bits.append(_count_badge(f"{early} at stage 1–2", "info", "early"))

    return ('<div style="display:flex;gap:8px;flex-wrap:wrap;'
            'align-items:center;margin-bottom:8px">' + "".join(bits) +
            '</div>'
            + _sub("Click any count to show the companies behind it.")
            + _sub(note, "#8a6d1a" if status != "fresh" else "#898781",
                   "11px"))


def _watchlist_block(wl):
    if not wl or not wl.get("rows"):
        return card("🎯 10-Year Watchlist",
                    empty("No scan stored yet — run one to populate this."))

    themes = wl.get("themes") or {}
    stages = wl.get("stages") or {}
    chips = "".join(badge(f"{v}× {k}", "muted", "small") + " "
                    for k, v in list(themes.items())[:6])
    stage_chips = "".join(badge(f"{v} {k.lower()}", "info", "small") + " "
                          for k, v in stages.items())

    warn = ""
    if wl.get("concentration_note"):
        warn = (f'<div style="margin-top:8px;padding:8px 10px;'
                f'background:#fdf6e3;border-radius:7px;font-size:11px;'
                f'color:#8a6d1a">⚠ {esc(wl["concentration_note"])}</div>')

    excluded = ""
    if wl.get("excluded_thin"):
        names = ", ".join(f'{e["ticker"]} ({e["score"]}, '
                          f'{(e.get("coverage") or 0) * 100:.0f}% coverage)'
                          for e in wl["excluded_thin"][:8])
        excluded = _sub(f'Held off the list for thin data rather than for a '
                        f'low score: {names}. A score built on under 60% of '
                        f'the factor weight is not a ranking.')
    if wl.get("excluded_mature"):
        names = ", ".join(f'{e["ticker"]} ({e["score"]})'
                          for e in wl["excluded_mature"][:8])
        excluded += _sub(
            f'Scored well but already mature, so held off a ten-year list: '
            f'{names}. These are fine businesses whose growth has converged '
            f'on their market\'s — they keep their score in the full ranking '
            f'below; what they do not do is take a slot from a company that '
            f'still has its compounding ahead of it.')

    return card(
        f"🎯 10-Year Watchlist — top {wl['size']} by Future Compounder Score",
        f'<div style="margin-bottom:8px">{chips}</div>'
        f'<div style="margin-bottom:10px">{stage_chips}</div>'
        + _filter_bar(wl["rows"], "fc-wl")
        + _table(wl["rows"], 0, detail=True, table_id="fc-wl")
        + warn + excluded
        + _sub("Ranked purely on score — not on valuation, size or momentum, "
               "per the framework. Concentration is reported, never corrected: "
               "capping names per theme would silently override the ranking."))


def _theme_coverage_block():
    cov = TH.coverage()
    cells = "".join(
        f'<div style="display:flex;gap:8px;padding:2px 0;font-size:11px">'
        f'<div style="min-width:230px;color:#3d3d3a">'
        f'{esc((TH.THEMES.get(k) or {}).get("label", k))}</div>'
        f'<div style="color:#898781">{v} tracked</div>'
        f'<div style="color:#b5b3ad;font-size:10px">'
        f'TAM {_money((TH.THEMES.get(k) or {}).get("tam_now"))} → '
        f'{_money((TH.THEMES.get(k) or {}).get("tam_5y"))}, '
        f'{esc((TH.THEMES.get(k) or {}).get("confidence", ""))} confidence'
        f'</div></div>' for k, v in cov.items())
    return card(
        "Theme library — the universe is the theme mapping",
        f'<div>{cells}</div>'
        + _sub("A ticker is in this universe BECAUSE a structural trend was "
               "identified for it, which is what Step 1 asks — the reverse of "
               "ranking whatever happened to be in an index. TAM figures are "
               "curated per theme with a source and an as-of date, versioned "
               "in git, and never generated at scan time."))


def compounder_page(query: dict | None = None) -> tuple[str, str]:
    snap = CS.load()
    rows = (snap or {}).get("rows") or []
    wl = (snap or {}).get("watchlist")

    typed = ((query or {}).get("tickers") or [""])[0].strip()
    if typed:
        wanted = {t.strip().upper() for t in typed.replace(",", " ").split()
                  if t.strip()}
        rows = [r for r in rows if r.get("ticker") in wanted]

    if not snap:
        body = card(
            "🚀 Future Compounder / Emerging Leader Engine",
            _THESIS
            + '<div style="margin-top:12px">'
            + badge("no scan stored", "watch") + '</div>'
            + _sub("Run one from the command line — it is minutes of network "
                   "across the whole theme library:")
            + '<pre style="background:#f7f6f2;padding:10px;border-radius:7px;'
              'font-size:11px;margin-top:6px;overflow-x:auto">'
              'python -m stockanalysis.core.compounder.scan</pre>',
            icon="🚀") + _theme_coverage_block()
        return body, _JS

    header = _header(snap, (snap or {}).get("rows") or [])

    controls = (
        f'<form method="get" style="display:flex;gap:8px;align-items:center;'
        f'flex-wrap:wrap;margin-top:10px">'
        f'<input name="tickers" value="{esc(typed)}" '
        f'placeholder="ALAB, RKLB — blank shows the whole library" '
        f'style="flex:1;min-width:260px;padding:7px 10px;border:1px solid '
        f'#d9d7ce;border-radius:7px;font-size:13px">'
        f'<button type="submit" class="btn">Filter</button>'
        + (f'<a href="/compounder" class="btn secondary" '
           f'style="text-decoration:none;padding:7px 14px">Whole library</a>'
           if typed else "") + '</form>')

    body = card("🚀 Future Compounder / Emerging Leader Engine",
                header + _THESIS + controls, icon="🚀")

    if not typed and wl:
        body += _watchlist_block(wl)

    if typed:
        body += card(f"Filtered ({len(rows)})",
                     _table(rows, 1000, detail=True, table_id="fc-sel")
                     + _sub("The full case: score arithmetic, classified "
                            "risks, what has to go right, what would "
                            "invalidate it, and the stage transitions."))
    else:
        body += card(
            f"Full ranking ({len(rows)})",
            _filter_bar(rows, "fc-all")
            + _table(rows, 1000, detail=False, table_id="fc-all")
            + _sub("Click any column header to sort — a second click "
                   "reverses it, and names whose value was never measured "
                   "stay at the bottom either way rather than sorting as "
                   "zero. Click a row to open that company's full case."))

    if not typed:
        body += _theme_coverage_block()

    return body, _JS


_JS = """
function fcToggle(i){
  var el=document.getElementById('fc-d-'+i);
  if(el) el.style.display = el.style.display==='none' ? '' : 'none';
}

// Sort state per table, so a second click on the same header reverses.
var fcSortState={};

/* Rows and their (optional) expanded detail row move as a unit. Sorting the
   summary rows alone would leave every open panel attached to whichever
   company landed in that position. */
function fcPairs(table){
  var out=[], rows=table.querySelectorAll('tr.fc-row');
  for(var i=0;i<rows.length;i++){
    var r=rows[i], id=r.getAttribute('data-row');
    var d=table.querySelector('tr.fc-detail[data-for="'+id+'"]');
    out.push({row:r, detail:d});
  }
  return out;
}

function fcSort(tableId, key, kind){
  var table=document.getElementById(tableId);
  if(!table) return;
  var st=fcSortState[tableId]||{};
  // Numeric columns open descending (a ranking reads best-first); text
  // columns open ascending (A-Z is what alphabetical means).
  var dir = (st.key===key) ? -st.dir : (kind==='num' ? -1 : 1);
  fcSortState[tableId]={key:key, dir:dir};

  var pairs=fcPairs(table);
  pairs.sort(function(a,b){
    var x=a.row.getAttribute('data-'+key), y=b.row.getAttribute('data-'+key);
    // Unmeasured sinks to the bottom in BOTH directions — it is "unknown",
    // not "worst", and flipping the sort must not float it to the top.
    var xe=(x===null||x===''), ye=(y===null||y==='');
    if(xe&&ye) return 0;
    if(xe) return 1;
    if(ye) return -1;
    if(kind==='num'){ return (parseFloat(x)-parseFloat(y))*dir; }
    return x.toLowerCase().localeCompare(y.toLowerCase())*dir;
  });

  var frag=document.createDocumentFragment();
  pairs.forEach(function(p){ frag.appendChild(p.row);
                             if(p.detail) frag.appendChild(p.detail); });
  (table.tBodies[0]||table).appendChild(frag);

  table.querySelectorAll('th[data-key]').forEach(function(th){
    var a=th.querySelector('.fc-arrow');
    if(!a) return;
    if(th.getAttribute('data-key')===key){
      a.textContent = dir===1 ? ' ▲' : ' ▼';
      a.style.color='#185FA5';
    } else { a.textContent=' ⇅'; a.style.color='#d9d7ce'; }
  });
}

function fcFilter(tableId){
  var table=document.getElementById(tableId);
  var bar=document.getElementById(tableId+'-filters');
  if(!table||!bar) return;

  var facets=[], text='', minScore=0, noRisk=false, early=false;
  bar.querySelectorAll('[data-attr]').forEach(function(el){
    var a=el.getAttribute('data-attr');
    if(a==='_text'){ text=el.value.trim().toLowerCase(); }
    else if(a==='_minscore'){ minScore=parseFloat(el.value)||0;
      var lbl=document.getElementById(tableId+'-minscore');
      if(lbl) lbl.textContent=el.value; }
    else if(a==='_norisk'){ noRisk=el.checked; }
    else if(a==='_early'){ early=el.checked; }
    else if(el.value){ facets.push([a, el.value]); }
  });

  var shown=0, total=0;
  fcPairs(table).forEach(function(p){
    total++;
    var ok=true;
    for(var i=0;i<facets.length;i++){
      if(p.row.getAttribute('data-'+facets[i][0])!==facets[i][1]){ ok=false; break; }
    }
    if(ok&&text){
      var t=(p.row.getAttribute('data-ticker')||'').toLowerCase();
      var n=(p.row.getAttribute('data-name')||'');
      ok = t.indexOf(text)>=0 || n.indexOf(text)>=0;
    }
    if(ok&&minScore>0){
      var s=p.row.getAttribute('data-score');
      // An unscored name cannot clear a minimum — but it is hidden for
      // being unmeasured, not for being bad, which the count line says.
      ok = (s!=='' && s!==null && parseFloat(s)>=minScore);
    }
    if(ok&&noRisk){ ok = (parseInt(p.row.getAttribute('data-risks')||'0',10)===0); }
    if(ok&&early){ ok = (p.row.getAttribute('data-early')==='1'); }

    p.row.style.display = ok ? '' : 'none';
    if(p.detail && !ok) p.detail.style.display='none';
    // A filtered-out row's panel is closed, not merely hidden, so it does
    // not spring back open when the filter is cleared.
    if(p.detail && !ok){
      var inner=p.detail.querySelector('div[id^="fc-d-"]');
      if(inner) inner.style.display='none';
    }
    if(p.detail && ok) p.detail.style.display='';
    if(ok) shown++;
  });

  var c=document.getElementById(tableId+'-count');
  if(c) c.textContent = shown===total ? total+' shown'
                                      : shown+' of '+total+' shown';
}

function fcReset(tableId){
  var bar=document.getElementById(tableId+'-filters');
  if(!bar) return;
  bar.querySelectorAll('[data-attr]').forEach(function(el){
    if(el.type==='checkbox') el.checked=false;
    else if(el.type==='range') el.value=0;
    else el.value='';
  });
  fcFilter(tableId);
}

// Header counts -> the rows they counted.
var FC_FOCUS={"all":["_all",""],
              "in-band":["band","IN BAND"],
              "graduated":["band","GRADUATED"],
              "conviction":["tier","CONVICTION"],
              "strong":["tier","STRONG CANDIDATE"],
              "watch":["tier","WATCH"],
              "early":["_early","1"]};

// Returns false when it handled the click (so the <a> does not navigate) and
// true when it could not — a ticker filter is active, so the full table and
// its filter bar are not on this page and the href has to do the work.
function fcFocus(key){
  var spec=FC_FOCUS[key], bar=document.getElementById('fc-all-filters');
  if(!spec||!bar) return true;
  fcReset('fc-all');
  if(spec[0]==='_early'){
    var cb=bar.querySelector('[data-attr="_early"]');
    if(!cb) return true;
    cb.checked=true;
  } else if(spec[0]!=='_all'){
    var sel=bar.querySelector('select[data-attr="'+spec[0]+'"]');
    // A facet with fewer than two values is never rendered, so a count can
    // outlive its dropdown — a library where every name is IN BAND has no
    // cap-band select. Falling through to the href would reload to the same
    // dead end, so say what happened instead of silently doing nothing.
    if(!sel) return true;
    var found=false;
    for(var i=0;i<sel.options.length;i++){
      if(sel.options[i].value===spec[1]){ found=true; break; }
    }
    if(!found) return true;
    sel.value=spec[1];
  }
  fcFilter('fc-all');
  var t=document.getElementById('fc-all');
  if(t&&t.scrollIntoView) t.scrollIntoView({behavior:'smooth', block:'start'});
  return false;
}

// ?focus=<key> on load, so the counts are linkable and survive a reload.
(function(){
  function apply(){
    var m=/[?&]focus=([^&]+)/.exec(window.location.search);
    if(m) fcFocus(decodeURIComponent(m[1]));
  }
  if(document.readyState==='loading')
    document.addEventListener('DOMContentLoaded', apply);
  else apply();
})();
"""
