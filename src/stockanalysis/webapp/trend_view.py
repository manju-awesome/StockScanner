"""
trend_view.py — the Trend Regime page
======================================
Presentation only. Every label, score and status comes from core.trend;
nothing is decided here.

What the layout is arguing
--------------------------
The five filters are tabs rather than sections stacked down one page,
because they are five different questions and a name can legitimately
appear under more than one. Reading them as one list is exactly the
mistake the filters exist to prevent — "strongest trend" and "best entry
today" are not the same ranking and are frequently opposites.

REGIME and ENTRY are separate columns, never merged into a verdict. A
leader that has run is STRONG_LONG_TERM_BULL / DO_NOT_CHASE, and both
halves matter: the first says keep it on the list, the second says not at
this price. A single column would have to throw one of those away.

The Buy-the-Dip tab is ordered by proximity to the 21 EMA rather than by
score, and says so on the page, because a user who sees a list sorted
"best first" everywhere else will otherwise read the top row as the
highest-quality name rather than the closest one.
"""

from __future__ import annotations

from stockanalysis.core.trend import engine as TE
from stockanalysis.core.trend import filters as TF
from stockanalysis.core.trend import regime as TR

from .views import badge, card, empty, esc, tv_url

DASH = "—"

# Rows rendered per tab. Every row carries an expandable detail block with a
# six-average table, which is ~5 KB of HTML; the full 669-name library came
# to 3.5 MB and no browser should be asked to lay that out. The cap is
# announced on the page rather than applied quietly — a silently truncated
# list is worse than a short one, because the name you were looking for is
# missing and nothing says so.
PAGE_LIMIT = 150

# Regime -> the page's status colour. Pullbacks and cooling are deliberately
# "watch" rather than "bad": they are states inside an intact trend, and
# colouring them red would reproduce the confusion the engine was built to
# remove.
_REGIME_STATUS = {
    "STRONG_LONG_TERM_BULL": "good",
    "STRONG_MOMENTUM": "good",
    "MOMENTUM_ACCELERATION": "good",
    "HEALTHY_MOMENTUM_PULLBACK": "info",
    "MOMENTUM_COOLING": "watch",
    "DEEP_PULLBACK_IN_BULL_TREND": "watch",
    "LONG_TERM_BULL_WEAKENING": "watch",
    "EARLY_BULLISH_REVERSAL": "info",
    "CONFIRMED_BULLISH_REVERSAL": "good",
    "MIXED_TRANSITION": "muted",
    "EARLY_BEARISH_REVERSAL": "watch",
    "CONFIRMED_BEARISH_REVERSAL": "bad",
    "LONG_TERM_BEAR_WEAKENING": "info",
    "STRONG_LONG_TERM_BEAR": "bad",
    "LONG_TERM_BREAKDOWN": "bad",
}

_ENTRY_STATUS = {"GOOD_ENTRY": "good", "DO_NOT_CHASE": "watch",
                 "WAIT_FOR_CONFIRMATION": "info", "NO_ENTRY": "muted",
                 "AVOID": "bad", "SHORT_SETUP": "bad"}

_EXT_COLOUR = {"PARABOLIC": "#A32D2D", "VERY_EXTENDED": "#8a6d1a",
               "EXTENDED": "#8a6d1a", "NORMAL": "#0F6E56",
               "BELOW_TREND": "#0C447C", "UNKNOWN": "#898781"}

_SLOPE_MARK = {"rising": ("↑", "#0F6E56"), "falling": ("↓", "#A32D2D"),
               "flat": ("→", "#898781"), "unknown": ("·", "#b5b3ad")}


def _n(v, nd=2):
    return DASH if v is None else f"{v:,.{nd}f}"


def _pct(v, nd=1):
    if v is None:
        return f'<span style="color:#b5b3ad">{DASH}</span>'
    colour = "#0F6E56" if v > 0 else "#A32D2D" if v < 0 else "#898781"
    return f'<span style="color:{colour}">{v:+.{nd}f}%</span>'


def _score_cell(v, capped=False):
    if v is None:
        return f'<span style="color:#b5b3ad">{DASH}</span>'
    colour = ("#0F6E56" if v >= 80 else "#1a7f5a" if v >= 70 else
              "#8a6d1a" if v >= 60 else "#898781" if v >= 40 else "#A32D2D")
    cap = ('<span title="capped: the 200 SMA is strongly declining" '
           'style="color:#A32D2D;font-size:9px"> ▼</span>' if capped else "")
    return (f'<span style="font-weight:700;color:{colour}">{v:.0f}</span>'
            f'<span style="font-size:10px;color:#b5b3ad">/100</span>{cap}')


def _slopes_cell(row):
    """The four decisive averages as arrows. Denser than words and it is the
    column most often read across many rows at once."""
    states = row.get("slope_state") or {}
    out = []
    for key, label in (("EMA8", "8"), ("EMA21", "21"), ("SMA50", "50"),
                       ("SMA200", "200")):
        mark, colour = _SLOPE_MARK.get(states.get(key, "unknown"))
        out.append(f'<span style="color:{colour}" title="{label} {states.get(key)}">'
                   f'{label}{mark}</span>')
    return '<span style="font-size:10px;letter-spacing:0.5px">' + " ".join(out) + "</span>"


def _sub(text, colour="#898781"):
    return (f'<div style="font-size:10px;color:{colour};margin-top:2px">'
            f'{esc(text)}</div>')


# ── DETAIL ───────────────────────────────────────────────────────────────

def _detail(row, idx) -> str:
    ma = row.get("ma") or {}
    slope = row.get("slope") or {}
    states = row.get("slope_state") or {}
    dist_pct = row.get("dist_pct") or {}
    dist_atr = row.get("dist_atr") or {}

    head = ('<tr style="font-size:10px;color:#898781;text-align:right">'
            '<th style="text-align:left">MA</th><th>Level</th><th>5d slope</th>'
            '<th>10d slope</th><th>State</th><th>Price vs</th><th>ATR away</th></tr>')
    body = []
    for key, label in (("SMA5", "SMA 5"), ("EMA8", "EMA 8"), ("SMA20", "SMA 20"),
                       ("EMA21", "EMA 21"), ("SMA50", "SMA 50"),
                       ("SMA200", "SMA 200")):
        mark, colour = _SLOPE_MARK.get(states.get(key, "unknown"))
        body.append(
            '<tr style="font-size:11px;text-align:right">'
            f'<td style="text-align:left;color:#0b0b0b">{label}</td>'
            f'<td>{_n(ma.get(key))}</td>'
            f'<td>{_n(slope.get(f"{key}_5d_pct"), 2)}%</td>'
            f'<td>{_n(slope.get(f"{key}_10d_pct"), 2)}%</td>'
            f'<td style="color:{colour}">{mark} {esc(states.get(key, "—"))}</td>'
            f'<td>{_pct(dist_pct.get(key))}</td>'
            f'<td>{_n(dist_atr.get(key), 2)}</td></tr>')

    comps = row.get("score_components") or {}
    comp_bits = []
    for key in ("long_term", "alignment", "slope", "structure", "momentum",
                "adx", "volume", "extension"):
        v = comps.get(key)
        label = key.replace("_", " ")
        comp_bits.append(
            f'<span style="font-size:10px;color:#898781">{esc(label)} '
            + (f'<b style="color:#0b0b0b">{v * 100:.0f}</b>' if v is not None
               else '<b style="color:#b5b3ad">not measured</b>')
            + "</span>")

    wait = row.get("wait_for")
    facts = [
        f'<b>Why:</b> {esc(row.get("key_reason"))}',
        f'<b>Risk:</b> {esc(row.get("risk"))}',
        f'<b>Entry:</b> {esc(row.get("entry_status"))} — {esc(row.get("entry_detail"))}'
        + (f' · wait for {wait:,.2f}' if wait else ""),
        f'<b>Long-term:</b> {esc(row.get("long_term_detail"))}',
        f'<b>Structure:</b> {esc(row.get("structure"))}'
        + (f' · last swing high {row["swing_high"]:,.2f}' if row.get("swing_high") else "")
        + (f' · last swing low {row["swing_low"]:,.2f}' if row.get("swing_low") else ""),
    ]
    if row.get("extension_flags"):
        facts.append("<b>Flags:</b> " + ", ".join(esc(f) for f in row["extension_flags"]))
    if row.get("insufficient_history"):
        facts.append('<b>Note:</b> fewer than 200 bars — there is no long-term '
                     'regime for this name, and its score is computed on the '
                     'components that exist')

    return (f'<tr id="tr-d-{idx}" style="display:none"><td colspan="13" '
            f'style="background:#faf9f5;padding:12px 14px">'
            '<div style="display:flex;gap:20px;flex-wrap:wrap">'
            '<div style="flex:1;min-width:340px">'
            '<table style="width:100%;border-collapse:collapse">'
            + head + "".join(body) + "</table></div>"
            '<div style="flex:1;min-width:280px;font-size:11px;line-height:1.8;color:#3f3f3f">'
            + "<br>".join(facts)
            + '<div style="margin-top:8px;padding-top:8px;border-top:0.5px solid #e1e0d9">'
            + '<div style="font-size:10px;color:#898781;margin-bottom:4px">'
              'Score components (0-100 each, weighted)</div>'
            + " · ".join(comp_bits)
            + f'<div style="font-size:10px;color:#898781;margin-top:4px">'
              f'coverage {row.get("score_coverage")}% of the weight</div>'
            + "</div></div></div></td></tr>")


# ── TABLE ────────────────────────────────────────────────────────────────

_COLS = ("Ticker", "Price", "Regime", "Score", "LT", "Int", "Short",
         "Alignment", "Slopes", "Structure", "vs 21EMA", "vs 200MA", "Entry")


def _table(rows, offset=0) -> str:
    if not rows:
        return empty("nothing matches this filter in the current snapshot")
    head = ('<tr style="font-size:10px;color:#898781;text-transform:uppercase;'
            'letter-spacing:0.4px">'
            + "".join(f'<th style="text-align:{"left" if i < 3 else "right"};'
                      f'padding:6px 8px;white-space:nowrap">{esc(c)}</th>'
                      for i, c in enumerate(_COLS))
            + "</tr>")
    body = []
    for i, row in enumerate(rows):
        idx = offset + i
        regime = row.get("regime") or DASH
        ext = row.get("extension_status")
        body.append(
            f'<tr style="border-top:0.5px solid #eeede7;cursor:pointer" '
            f'onclick="trToggle({idx})">'
            f'<td style="padding:7px 8px;font-weight:700">'
            f'<a href="{tv_url(row.get("ticker"))}" target="_blank" '
            f'onclick="event.stopPropagation()" '
            f'style="color:#0C447C;text-decoration:none">{esc(row.get("ticker"))}</a></td>'
            f'<td style="padding:7px 8px;text-align:left">{_n(row.get("price"))}</td>'
            f'<td style="padding:7px 8px">'
            f'{badge(regime.replace("_", " ").title(), _REGIME_STATUS.get(regime, "muted"), "small")}</td>'
            f'<td style="padding:7px 8px;text-align:right">'
            f'{_score_cell(row.get("score"), row.get("score_capped"))}</td>'
            f'<td style="padding:7px 8px;text-align:right;font-size:11px">{esc(row.get("long_term_trend"))}</td>'
            f'<td style="padding:7px 8px;text-align:right;font-size:11px">{esc(row.get("intermediate_trend"))}</td>'
            f'<td style="padding:7px 8px;text-align:right;font-size:11px">{esc(row.get("short_term_trend"))}</td>'
            f'<td style="padding:7px 8px;text-align:right;font-size:10px;color:#898781">'
            f'{esc((row.get("alignment") or "").replace("_ALIGNMENT", "").replace("_", " ").title())}</td>'
            f'<td style="padding:7px 8px;text-align:right">{_slopes_cell(row)}</td>'
            f'<td style="padding:7px 8px;text-align:right;font-size:11px">{esc(row.get("structure"))}</td>'
            f'<td style="padding:7px 8px;text-align:right">{_pct(row.get("dist_21ema"))}'
            f'<span style="font-size:9px;color:{_EXT_COLOUR.get(ext, "#898781")}"> '
            f'{esc((ext or "").replace("_", " ").lower())}</span></td>'
            f'<td style="padding:7px 8px;text-align:right">{_pct(row.get("dist_200sma"))}</td>'
            f'<td style="padding:7px 8px;text-align:right">'
            f'{badge((row.get("entry_status") or "").replace("_", " ").title(), _ENTRY_STATUS.get(row.get("entry_status"), "muted"), "small")}</td>'
            "</tr>")
        body.append(_detail(row, idx))
    return ('<div style="overflow-x:auto"><table style="width:100%;'
            'border-collapse:collapse">' + head + "".join(body) + "</table></div>")


# ── PAGE ─────────────────────────────────────────────────────────────────

_TABS = [("ALL", "All", "every classified name, strongest trend first")] + [
    (k, spec["name"], spec["desc"]) for k, spec in TF.FILTERS.items()]


def _tabs(active: str, counts: dict, total: int, typed: str) -> str:
    out = []
    for key, name, _ in _TABS:
        n = total if key == "ALL" else counts.get(key, 0)
        on = key == active
        qs = f"?filter={key}" + (f"&tickers={typed}" if typed else "")
        out.append(
            f'<a href="{qs}" style="text-decoration:none;padding:6px 11px;'
            f'border-radius:7px;font-size:11px;font-weight:600;white-space:nowrap;'
            + ("background:#0C447C;color:white" if on
               else "background:#f5f4f0;color:#3f3f3f") + '">'
            + esc(name)
            + f'<span style="opacity:.7;margin-left:5px">{n}</span></a>')
    return ('<div style="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0">'
            + "".join(out) + "</div>")


_THESIS = (
    '<div style="font-size:11px;color:#898781;line-height:1.6">'
    '<b>Price above the 200 SMA is not, by itself, bullish.</b> Above a '
    '<i>rising</i> 200 is an uptrend; above a <i>falling</i> one is a bounce '
    'inside a downtrend. Every name here is read as a hierarchy — 200 SMA is '
    'the regime, 50 SMA the trend, 21 EMA the momentum, 8 EMA the trigger — '
    'and a short-term MA violation is never reported as a long-term trend '
    'break.<br><br>'
    '<b>Trend and entry are separate columns and they disagree often.</b> '
    'A leader 4 ATR above its 21 EMA is still a leader; it is just not a buy '
    'today. Merging the two would mean either chasing extended names or '
    'rejecting good ones for being temporarily weak — and the whole point '
    'of the split is that neither is necessary.<br><br>'
    '<b>Extension is measured in ATR, never percent</b>, and the '
    'Buy-the-Dip tab is ranked by <i>proximity</i> to the 21 EMA rather than '
    'by trend score: the best dip is the closest one, not the strongest '
    'one.</div>')


def _controls(typed: str, generated) -> str:
    return (
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
        'margin-top:10px">'
        f'<input id="tr-tickers" value="{esc(typed)}" placeholder="classify '
        f'specific tickers, e.g. NVDA AMD MSFT" style="flex:1;min-width:240px;'
        'padding:7px 10px;border:0.5px solid #d9d7ce;border-radius:8px;'
        'font-size:12px">'
        '<button onclick="trGo()" style="padding:7px 14px;border:0;'
        'border-radius:8px;background:#0C447C;color:white;font-size:12px;'
        'font-weight:600;cursor:pointer">Classify</button>'
        '<button onclick="trScan()" style="padding:7px 14px;border:0.5px solid '
        '#d9d7ce;border-radius:8px;background:white;color:#3f3f3f;font-size:12px;'
        'font-weight:600;cursor:pointer">Rescan universe</button>'
        "</div>")


def trend_page(query: dict | None = None) -> tuple[str, str]:
    query = query or {}
    typed = (query.get("tickers") or [""])[0].strip()
    active = (query.get("filter") or ["ALL"])[0].upper()
    if active not in {k for k, _, _ in _TABS}:
        active = "ALL"

    live = bool(typed)
    if live:
        # A typed list is classified now rather than read from the snapshot:
        # it is a handful of names, one batched download, and the whole
        # reason to type a ticker is that you want today's read of it.
        names = [t for t in typed.replace(",", " ").split() if t][:40]
        rows = TE.classify_live(names)
        rows.sort(key=lambda r: -(r.get("score") or 0))
        generated, universe = "just now", len(names)
    else:
        snap = TE.load_snapshot()
        rows = snap.get("rows") or []
        generated, universe = snap.get("generated"), snap.get("universe") or 0

    counts = TF.counts(rows)
    matched = rows if active == "ALL" else TF.apply(rows, active)
    shown, truncated = matched[:PAGE_LIMIT], max(0, len(matched) - PAGE_LIMIT)

    stale = badge(f"scanned {esc(generated)}", "muted") if generated else \
        badge("never scanned — press Rescan universe", "watch")
    regime_counts = TE.regime_counts(rows)
    bulls = sum(v for k, v in regime_counts.items() if k in TR.BULLISH_REGIMES)
    bears = sum(v for k, v in regime_counts.items() if k in TR.BEARISH_REGIMES)

    header = ('<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
              'margin-bottom:8px">'
              + stale
              + badge(f"{len(rows)} of {universe} classified", "info")
              + badge(f"{bulls} bullish", "good")
              + badge(f"{bears} bearish", "bad")
              + badge(f"{regime_counts.get('MIXED_TRANSITION', 0)} mixed", "muted")
              + (badge("live — typed list", "info") if live else "")
              + "</div>")

    note = next(d for k, _, d in _TABS if k == active)
    if active == "E":
        # The stronger variant of filter E is a subset, not a separate tab:
        # both are short candidates and splitting them would imply the
        # weaker ones are something else. Marked inline instead.
        strong = sum(1 for r in shown if TF.breakdown_high_confidence(r))
        note += (f" — {strong} of {len(shown)} also below a falling 200 SMA, "
                 f"where the long-term regime agrees with the break")
    if truncated:
        note += (f" · showing the first {len(shown)} of {len(matched)} — "
                 f"narrow it with a filter or the ticker box")

    body = (card("Trend Regime", header + _THESIS + _controls(typed, generated),
                 icon="📐")
            + card("", _tabs(active, counts, len(rows), typed)
                   + _sub(note) + _table(shown), pad="12px 14px"))

    js = """
function trToggle(i){
  var el=document.getElementById('tr-d-'+i);
  if(el) el.style.display = el.style.display==='none' ? '' : 'none';
}
function trGo(){
  var v=document.getElementById('tr-tickers').value.trim();
  location.href='/trend'+(v?('?tickers='+encodeURIComponent(v)):'');
}
function trScan(){
  fetch('/run',{method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:'action=trend_regime'})
    .then(function(r){return r.json()})
    .then(function(j){ alert(j.message||'started');
                       if(j.ok && window.pollJobs) window.pollJobs(); });
}
document.addEventListener('keydown', function(e){
  if(e.key==='Enter' && document.activeElement &&
     document.activeElement.id==='tr-tickers'){ trGo(); }
});
"""
    return body, js
