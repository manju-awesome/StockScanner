"""
shortside_view.py
=================
The Long/Short page. Presentation only — every score, level and verdict
comes from core.shortside, which in turn reads core.longterm's own
company verdict rather than forming a second opinion about it.

Why this page renders live instead of from a snapshot
------------------------------------------------------
Unlike /csp, the short engine needs no option chains. It reads the
research library plus daily bars, and it fetches bars only for the
handful of names whose thesis already clears the threshold — so a full
552-name scan is a couple of dozen network calls and renders inside a
request. /longterm works the same way for the same reason.

What the layout is arguing
--------------------------
The two scores sit side by side and neither is styled as the primary. A
page that led with the long score would reproduce the exact defect this
engine was built to fix: a name failing the quality gate reading as
"nothing here" when it is in fact the most interesting short on the
board.

The SHORT WATCH bucket gets the most visual room, because it is where
the useful work is. SHORT NOW is rare by construction, and the value of
this scanner is the list of names whose thesis is complete and whose
price has not yet agreed — a watchlist, not a trade list.
"""

from __future__ import annotations

from stockanalysis.core.shortside import engine as SE

from .views import badge, card, empty, esc, tv_url

DASH = "—"

_BUCKET_STATUS = {"SHORT NOW": "bad", "SHORT WATCH": "watch",
                  "LONG OPPORTUNITY": "good", "NO EDGE": "muted"}

_BAND_COLOUR = {"🔴": "#A32D2D", "🟠": "#8a6d1a", "🟡": "#8a6d1a",
                "⚪": "#898781", "🟢": "#0F6E56"}


def _n(v, nd=2, dash=DASH):
    return dash if v is None else f"{v:,.{nd}f}"


def _sub(text, colour="#898781"):
    return (f'<div style="font-size:10px;color:{colour};margin-top:2px">'
            f'{esc(text)}</div>')


def _score(v, kind="short"):
    """A score cell. Short and long use opposite colour senses — a high
    short score is 'interesting', not 'good', so it is never green."""
    if v is None:
        return f'<span style="color:#b5b3ad">{DASH}</span>'
    if kind == "short":
        colour = "#A32D2D" if v >= 65 else "#8a6d1a" if v >= 50 else "#898781"
    elif kind == "long":
        colour = "#0F6E56" if v >= 65 else "#8a6d1a" if v >= 50 else "#898781"
    else:
        colour = "#898781"
    return (f'<span style="font-weight:700;color:{colour}">{v:.0f}</span>'
            f'<span style="font-size:10px;color:#b5b3ad">/100</span>')


# ─────────────────────────────────────────────────────────────────────────
# DETAIL
# ─────────────────────────────────────────────────────────────────────────

def _extension_block(row):
    ext = row.get("extension") or {}
    levels = ext.get("levels") or {}
    if not levels:
        return _sub("no extension reading")

    out = [f'<div style="font-size:11px;margin-bottom:5px">'
           f'ATR ${_n(ext.get("atr"))} '
           f'<span style="color:#898781">({_n(ext.get("atr_pct"), 1)}% of '
           f'price)</span> — every figure below is a multiple of it</div>']
    for key in ("8EMA", "21EMA", "50MA", "200MA"):
        lv = levels.get(key) or {}
        m = lv.get("atr_multiple")
        colour = _BAND_COLOUR.get((lv.get("band") or " ")[0], "#898781")
        bar = ""
        if lv.get("score") is not None:
            bar = (f'<div style="height:4px;background:#eceae4;'
                   f'border-radius:2px;width:56px;display:inline-block;'
                   f'vertical-align:middle"><div style="height:4px;'
                   f'border-radius:2px;width:{lv["score"]}%;'
                   f'background:{colour}"></div></div>')
        out.append(
            f'<div style="display:flex;justify-content:space-between;gap:8px;'
            f'font-size:11px;padding:2px 0">'
            f'<span>{esc(key)} <span style="color:#898781">'
            f'${_n(lv.get("price"))}</span></span>'
            f'<span>{bar} <b style="color:{colour}">'
            f'{"—" if m is None else f"{m:+.1f} ATR"}</b> '
            f'<span style="color:#898781">({_n(lv.get("pct"), 1)}%)</span>'
            f'</span></div>')
    return "".join(out)


def _components_block(row):
    comps = (row.get("short") or {}).get("components") or []
    if not comps:
        return _sub("not scored")
    out = []
    for c in comps:
        sc = c.get("score")
        bar = "" if sc is None else (
            f'<div style="height:4px;background:#eceae4;border-radius:2px;'
            f'width:60px;display:inline-block;vertical-align:middle">'
            f'<div style="height:4px;border-radius:2px;width:{sc}%;'
            f'background:{"#A32D2D" if sc >= 65 else "#8a6d1a" if sc >= 40 else "#b5b3ad"}">'
            f'</div></div>')
        out.append(
            f'<div style="padding:3px 0">'
            f'<div style="display:flex;justify-content:space-between;gap:8px;'
            f'font-size:11px">'
            f'<span>{esc(c["name"].replace("_", " ").title())} '
            f'<span style="color:#b5b3ad">{c["weight"]}%</span></span>'
            f'<span>{bar} '
            f'{"<span style=\'color:#b5b3ad\'>n/a</span>" if sc is None else sc}'
            f'</span></div>'
            + _sub(str(c.get("detail") or "")[:110]) + '</div>')

    cov = (row.get("short") or {}).get("coverage")
    if cov is not None and cov < 100:
        out.append(_sub(f"scored on {cov}% of the weights — the rest were "
                        f"unavailable and are excluded, not scored zero",
                        "#8a6d1a"))
    return "".join(out)


def _confirmation_block(row):
    c = row.get("confirmation") or {}
    state = c.get("state")
    status = {"STRONG": "bad", "WEAK": "watch"}.get(state, "muted")
    out = [f'<div style="margin-bottom:6px">'
           f'{badge(esc(c.get("label") or "—"), status)}</div>',
           f'<div style="font-size:11px">{esc(c.get("detail") or "")}</div>']

    frd = c.get("first_red_day") or {}
    if frd.get("detail"):
        out.append(_sub(f"first red day: {frd['detail']}"))
    if c.get("candle") and c["candle"] != "none":
        out.append(_sub(f"candle: {c['candle']}"))
    out.append(_sub("confirmation decides NOW vs WATCH — it is deliberately "
                    "not part of the thesis score, because the setup is as "
                    "good the day before it triggers as the day it does"))
    return "".join(out)


def _plan_block(row):
    p = row.get("plan") or {}
    if not p:
        return _sub("no plan — thesis below the watch threshold")

    out = [f'<div style="font-size:12px;line-height:1.7">'
           f'Entry <b>${_n(p.get("entry"))}</b><br>'
           f'Stop <b>${_n(p.get("stop"))}</b> '
           f'<span style="color:#898781">({_n(p.get("stop_pct"), 1)}%, '
           f'{_n(p.get("stop_atr"), 1)} ATR above)</span><br>'
           f'Risk ${_n(p.get("risk_per_share"))}/share</div>']

    if p.get("targets"):
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;gap:8px;'
            f'font-size:11px;padding:2px 0">'
            f'<span>{esc(t["name"])} <span style="color:#898781">'
            f'${_n(t["price"])}</span></span>'
            f'<span>{_n(t["move_pct"], 1)}% '
            f'<b style="color:#0F6E56">{_n(t.get("rr"), 2)}R</b></span>'
            f'</div>' for t in p["targets"])
        out.append(f'<div style="margin-top:6px">{rows}</div>')
    out.append(_sub("the stop sits above the recent high, never a fixed "
                    "percentage — on a parabolic name a 5% stop is inside "
                    "one session's range"))
    return "".join(out)


def _detail(row):
    def panel(title, body):
        return (f'<div style="flex:1;min-width:250px">'
                f'<div style="font-size:10px;font-weight:700;color:#898781;'
                f'text-transform:uppercase;letter-spacing:.04em;'
                f'margin-bottom:4px">{esc(title)}</div>{body}</div>')

    long_info = row.get("long") or {}
    avoid = row.get("avoid") or {}
    caps = (row.get("short") or {}).get("caps") or []

    long_html = (f'<div style="font-size:11px;line-height:1.7">'
                 f'{esc(long_info.get("detail") or "—")}<br>'
                 f'<span style="color:#898781">Investment view: '
                 f'{esc(long_info.get("investment_status") or "—")}</span>'
                 f'</div>'
                 + _sub("taken from the Long-Term engine, not recomputed "
                        "— one verdict per company across the workstation"))

    avoid_html = (f'<div style="font-size:11px">'
                  f'{esc(avoid.get("detail") or "—")}</div>'
                  + _sub("a conflict penalty applies when both sides argue "
                         "about equally — that is noise, and it should read "
                         "as more reason to stand aside, not less"))

    caps_html = ("".join(f'<div style="font-size:11px;color:#8a6d1a;'
                         f'padding:2px 0">! {esc(c)}</div>' for c in caps)
                 or _sub("no caps applied"))

    return (f'<div style="display:flex;flex-wrap:wrap;gap:18px;'
            f'padding:12px 14px;background:#faf9f7;'
            f'border-top:1px solid #eceae4">'
            + panel("Extension (in ATR)", _extension_block(row))
            + panel("Short thesis", _components_block(row))
            + panel("Confirmation", _confirmation_block(row))
            + panel("Short plan", _plan_block(row))
            + panel("Long reading", long_html)
            + panel("Why stand aside", avoid_html)
            + panel("Caps", caps_html)
            + '</div>')


# ─────────────────────────────────────────────────────────────────────────
# TABLE
# ─────────────────────────────────────────────────────────────────────────

_COLUMNS = ("Ticker", "Price", "8EMA", "50MA", "RSI", "%B", "Dist",
            "52W", "Reward", "Confirm", "LONG", "SHORT", "AVOID", "Call")


def _row(row, idx):
    ext = row.get("extension") or {}
    levels = ext.get("levels") or {}
    parts = (row.get("short") or {}).get("parts") or {}
    plan = row.get("plan") or {}
    conf = row.get("confirmation") or {}
    tk = row.get("ticker") or "?"

    def atr_cell(key):
        lv = levels.get(key) or {}
        m = lv.get("atr_multiple")
        if m is None:
            return DASH
        colour = _BAND_COLOUR.get((lv.get("band") or " ")[0], "#898781")
        return (f'<span style="color:{colour};font-weight:600">{m:+.1f}</span>'
                f'<span style="font-size:10px;color:#b5b3ad"> ATR</span>'
                + _sub(f"{_n(lv.get('pct'), 1)}%"))

    ob = parts.get("overbought") or {}
    dist = parts.get("distribution") or {}
    loc = parts.get("location") or {}

    rsi, bb = ob.get("rsi"), ob.get("bb_pct_b")
    rsi_txt = (DASH if rsi is None else
               f'<span style="color:{"#A32D2D" if rsi >= 70 else "#0b0b0b"}">'
               f'{rsi:.0f}</span>')
    bb_txt = (DASH if bb is None else
              f'<span style="color:{"#A32D2D" if bb >= 1.0 else "#0b0b0b"}">'
              f'{bb:.2f}</span>')
    dd = dist.get("days")
    dist_txt = (DASH if dd is None else
                f'<span style="color:{"#A32D2D" if dd >= 7 else "#8a6d1a" if dd >= 5 else "#0b0b0b"}">'
                f'{dd:.0f}d</span>')
    dh = loc.get("dist_52w_high")
    loc_txt = (DASH if dh is None else
               f'<span style="color:{"#A32D2D" if dh >= -3 else "#0b0b0b"}">'
               f'{dh:+.1f}%</span>')

    rr = plan.get("best_rr")
    conf_txt = badge(esc(conf.get("label") or "—"),
                     {"STRONG": "bad", "WEAK": "watch"}.get(
                         conf.get("state"), "muted"))

    cells = [
        (f'<a href="{tv_url(tk)}" target="_blank" style="font-weight:700;'
         f'color:#0b0b0b;text-decoration:none">{esc(tk)}</a>'
         + _sub(row.get("sector") or "")),
        f"${_n(row.get('price'))}" + _sub(f"ATR {_n(row.get('atr_pct'), 1)}%"),
        atr_cell("8EMA"),
        atr_cell("50MA"),
        rsi_txt, bb_txt, dist_txt, loc_txt,
        (DASH if rr is None else f'<b>{rr:.1f}R</b>'),
        conf_txt,
        _score(row.get("long_score"), "long"),
        _score(row.get("short_score"), "short"),
        _score(row.get("avoid_score"), "avoid"),
        badge(f'{row.get("icon", "")} {row.get("bucket", "")}',
              _BUCKET_STATUS.get(row.get("bucket"), "muted")),
    ]
    tds = "".join(f'<td style="padding:7px 8px;vertical-align:top;'
                  f'font-size:12px">{c}</td>' for c in cells)

    return (f'<tr onclick="ssToggle({idx})" style="cursor:pointer;'
            f'border-top:1px solid #eceae4">{tds}</tr>'
            f'<tr><td colspan="{len(_COLUMNS)}" style="padding:0 8px 6px;'
            f'font-size:11px;color:#898781">{esc(row.get("why") or "")}</td>'
            f'</tr>'
            f'<tr id="ss-d-{idx}" style="display:none">'
            f'<td colspan="{len(_COLUMNS)}" style="padding:0">'
            f'{_detail(row)}</td></tr>')


def _table(rows, start=0):
    if not rows:
        return empty("Nothing in this bucket.")
    head = "".join(f'<th style="padding:6px 8px;text-align:left;font-size:10px;'
                   f'color:#898781;text-transform:uppercase;'
                   f'letter-spacing:.04em">{esc(c)}</th>' for c in _COLUMNS)
    body = "".join(_row(r, start + i) for i, r in enumerate(rows))
    return (f'<div style="overflow-x:auto"><table style="width:100%;'
            f'border-collapse:collapse"><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _no_edge_block(rows):
    """NO EDGE compactly — it is most of the universe by construction."""
    if not rows:
        return ""
    items = ", ".join(
        f'<span title="{esc(r.get("why") or "")}">{esc(r.get("ticker") or "?")}'
        f'</span>' for r in sorted(rows, key=lambda x: -(x.get("short_score") or 0)))
    return (f'<div style="font-size:11px;color:#898781;line-height:1.9">'
            f'{items}</div>'
            + _sub("sorted by short score, strongest first — hover for the "
                   "reading. None of these is here for failing the quality "
                   "gate; that alone never routes a name to NO EDGE"))


def _controls(typed: str) -> str:
    """The manual ticker box.

    A full scan is the default because the useful output is the list you
    did not already know to look at. But when you DO have a name in mind,
    scanning 552 to read one is noise, so a typed list narrows it — and
    when one is given the page shows every requested ticker regardless of
    bucket, including NO EDGE. You asked about that name specifically;
    "it did not make the cut" is the answer, not a reason to hide it.
    """
    return (
        f'<form method="get" style="display:flex;gap:8px;align-items:center;'
        f'flex-wrap:wrap;margin-top:10px">'
        f'<input name="tickers" value="{esc(typed)}" '
        f'placeholder="MDB, CRL, NVDA — blank scans the whole library" '
        f'style="flex:1;min-width:260px;padding:7px 10px;border:1px solid '
        f'#d9d7ce;border-radius:7px;font-size:13px">'
        f'<button type="submit" class="btn">Scan these</button>'
        + (f'<a href="/shortside" class="btn secondary" '
           f'style="text-decoration:none;padding:7px 14px">Full library</a>'
           if typed else "")
        + '</form>')


def _missing_block(missing):
    """Requested tickers with no row in the research library.

    Reported rather than silently dropped: a typed ticker that vanishes
    reads as "no signal", when the truth is that nothing was measured.
    """
    if not missing:
        return ""
    return (f'<div style="margin-top:8px">'
            f'{badge(str(len(missing)) + " not in the research library", "watch")}'
            f'<div style="font-size:11px;color:#898781;margin-top:4px">'
            f'{esc(", ".join(missing))} — run Scanner or "+ Refresh Research" '
            f'on these first; the engine reads the library and does not '
            f'fetch fundamentals itself.</div></div>')


def shortside_page(query: dict | None = None) -> tuple[str, str]:
    from stockanalysis.webapp import api
    from stockanalysis.webapp.longterm_view import parse_tickers

    typed = ((query or {}).get("tickers") or [""])[0]
    wanted = parse_tickers(typed)

    data = api.longterm()
    raw = {r.get("Ticker"): r for r in api._longterm_universe()}

    results = data["rows"]
    missing = []
    if wanted:
        have = {r.get("ticker") for r in results}
        missing = [t for t in wanted if t not in have]
        keep = set(wanted)
        results = [r for r in results if r.get("ticker") in keep]

    # With a short typed list every name is worth confirming; on a full
    # library run the cap keeps the scan to a couple of dozen calls.
    limit = max(40, len(wanted)) if wanted else 40
    rows = SE.evaluate_universe(results, raw, limit=limit)
    counts = SE.counts(rows)

    by = {b: [r for r in rows if r["bucket"] == b] for b in SE.BUCKETS}

    header = (
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
        f'margin-bottom:8px">'
        f'{badge("regime " + str(data.get("regime") or "?"), "info")}'
        f'{badge(str(len(rows)) + (" name" if len(rows) == 1 else " names") + " scored both ways", "muted")}'
        f'{badge(str(counts.get("SHORT NOW", 0)) + " short now", "bad")}'
        f'{badge(str(counts.get("SHORT WATCH", 0)) + " short watch", "watch")}'
        f'{badge(str(counts.get("LONG OPPORTUNITY", 0)) + " long", "good")}'
        f'{badge(str(counts.get("NO EDGE", 0)) + " no edge", "muted")}'
        + (badge("manual list", "info") if wanted else "")
        + '</div>')

    thesis = (
        '<div style="font-size:11px;color:#898781;line-height:1.6">'
        'Failing the long-term quality test is <b>not</b> a verdict here, it '
        'is one input. A weak business at a demanding valuation, far above '
        'its own averages, overbought and into distribution is a short '
        'setup — so every name the Long-Term engine rejects is scored on '
        'the short side before anything is called NO EDGE.<br><br>'
        '<b>A high short score is a setup, not an instruction.</b> '
        'Confirmation — a bearish reversal or a first red day — is a '
        'separate axis and is never blended into the score, because the '
        'thesis is exactly as good the day before it triggers as the day it '
        'does. That is the whole difference between SHORT NOW and SHORT '
        'WATCH.<br><br>'
        '<b>Extension is measured in ATR, never percent.</b> The same 10% '
        'above the 8 EMA is a parabolic move on a 2%-ATR name and an '
        'ordinary week on a 5%-ATR one.</div>')

    n = 0
    sections = []
    for bucket, icon, note in (
            ("SHORT NOW", "🔥", "thesis complete and price confirming"),
            ("SHORT WATCH", "👀", "thesis complete, waiting for price to agree"),
            ("LONG OPPORTUNITY", "🟢", "the Long-Term engine's own verdict")):
        items = by.get(bucket) or []
        if wanted and not items:
            continue                    # a typed list should not show empties
        sections.append(card(f"{icon} {bucket} ({len(items)})",
                             _table(items, n) + _sub(note)))
        n += len(items)

    no_edge = by.get("NO EDGE") or []
    if wanted and no_edge:
        # Typed names get the full table even here — see _controls().
        sections.append(card(f"⚪ NO EDGE ({len(no_edge)})",
                             _table(no_edge, n)
                             + _sub("scored in full because you asked for "
                                    "these by name")))
    elif no_edge:
        sections.append(card(f"⚪ NO EDGE ({len(no_edge)})",
                             _no_edge_block(no_edge)))

    body = (card("Long / Short Decision Engine",
                 header + thesis + _controls(typed) + _missing_block(missing),
                 icon="⚖️")
            + "".join(sections))

    js = """
function ssToggle(i){
  var el=document.getElementById('ss-d-'+i);
  if(el) el.style.display = el.style.display==='none' ? '' : 'none';
}
"""
    return body, js
