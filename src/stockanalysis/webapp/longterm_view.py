"""
longterm_view.py
================
The Long-Term Buy Engine page. Presentation only: every score, band, verdict
and blocker comes from core.longterm through api.longterm(), and this file
renders what it is handed.

That separation is what lets each row's expanded panel claim to show "why"
and be right — the ✓/✗ lines, the gate a name stopped at, and the prices to
wait for are the engine's own output, not a second implementation of the
rules in HTML.

Server-rendered rather than a JS controller like screener_view: this page has
no rule builder and no per-keystroke re-query, so the whole thing is one
render at a few tens of milliseconds over 545 rows. Filtering is a query
string and a page load, which keeps the URL shareable.

The layout follows §15 of the framework — one row per company, the eight
judgments side by side, the action last — because the point of the table is
to make the disagreements visible. A name that is Elite and Overvalued and a
name that is Watchlist and Undervalued both score in the seventies, and a
single blended number would hide which is which.
"""

from __future__ import annotations

import string
from urllib.parse import urlencode

from stockanalysis.core.longterm import buy_zones as BZ
from stockanalysis.core.longterm import position_sizing as PS
from stockanalysis.core.longterm.engine import ACTION_ICONS

from .views import badge, card, empty, esc, tv_url

_ACTION_STYLE = {
    "BUY NOW": ("#E1F5EE", "#085041"),
    "BUY ON CONFIRMATION": ("#E1F5EE", "#0F6E56"),
    "BUY ON 8/21 EMA": ("#E6F1FB", "#0C447C"),
    "BUY ON 50 MA": ("#E6F1FB", "#0C447C"),
    "BUY ON BREAKOUT RETEST": ("#E6F1FB", "#0C447C"),
    "BUY ON 200 MA": ("#E6F1FB", "#0C447C"),
    "BUY ON SUPPORT": ("#E6F1FB", "#0C447C"),
    "DEEP PULLBACK — WAIT FOR SUPPORT": ("#FAEEDA", "#633806"),
    "WATCH": ("#F1EFE8", "#444441"),
    "WAIT": ("#FAEEDA", "#633806"),
    "RESEARCH": ("#F1EFE8", "#444441"),
    "AVOID": ("#FCEBEB", "#791F1F"),
    "THESIS BROKEN": ("#FCEBEB", "#791F1F"),
    "OWN / WAIT FOR PRICE": ("#FAEEDA", "#633806"),
    "OWN / WAIT FOR TREND": ("#FAEEDA", "#633806"),
    "OWN / WAIT FOR ENTRY": ("#FAEEDA", "#633806"),
}

_INVEST_STYLE = {"CORE": "good", "OWN": "good", "WATCHLIST": "watch",
                 "REJECT": "bad"}
_TIER_STYLE = {"Elite": "good", "High Quality": "good",
               "Watchlist": "watch", "Reject": "bad"}
_BAND_STYLE = {"UNDERVALUED": "good", "FAIR": "watch", "OVERVALUED": "bad"}

# Zone and action names are written out rather than derived with .title() or
# .replace(): both mangle the acronyms this domain is full of, turning "EMA"
# into "E MA" and "BUY ON 8/21 EMA" into "Buy On 8/21 Ema".
_ZONE_SHORT = {"EMA": "8/21 EMA", "50MA": "50 MA", "200MA": "200 MA",
               "BREAKOUT": "Breakout", "NONE": "—"}
_ACTION_SHORT = {"BUY NOW": "Buy now",
                 "BUY ON CONFIRMATION": "Buy on confirmation",
                 "BUY ON 8/21 EMA": "Buy on 8/21 EMA",
                 "BUY ON 50 MA": "Buy on 50 MA",
                 "BUY ON BREAKOUT RETEST": "Buy on breakout retest",
                 "BUY ON 200 MA": "Buy on 200 MA",
                 "BUY ON SUPPORT": "Buy on support",
                 "DEEP PULLBACK — WAIT FOR SUPPORT": "Deep pullback",
                 "WATCH": "Watch", "WAIT": "Wait",
                 "RESEARCH": "Research", "AVOID": "Avoid",
                 "THESIS BROKEN": "Thesis broken",
                 "OWN / WAIT FOR PRICE": "Own / wait for price",
                 "OWN / WAIT FOR TREND": "Own / wait for trend",
                 "OWN / WAIT FOR ENTRY": "Own / wait for entry"}

# Stage is what the Pullback column shows now. The zone answers "is price on
# a tracked level" and collapses a 22%-below-the-50-MA correction to "—";
# the stage names it.
_STAGE_SHORT = {"AT_HIGHS": "At highs", "STAGE1_EMA": "S1 · 8/21 EMA",
                "STAGE2_50MA": "S2 · 50 MA", "STAGE3_DEEP": "S3 · deep",
                "STAGE4_UNCONFIRMED": "Below 200 MA · unconfirmed",
                "STAGE4_BREAKDOWN": "S4 · breakdown",
                "EXTENDED": "Extended"}
# Ordered so sorting descending puts the shallow, tradeable pullbacks first.
_STAGE_RANK = {"STAGE2_50MA": 6, "STAGE1_EMA": 5, "STAGE3_DEEP": 4,
               "AT_HIGHS": 3, "EXTENDED": 2, "STAGE4_UNCONFIRMED": 1,
               "STAGE4_BREAKDOWN": 0}

# Which gate a name stopped at, in plain language for the filter chips.
_GATE_LABEL = {
    "quality": "Failed on business quality",
    "valuation": "Failed on price",
    # Deliberately not "Failed on trend": the same gate now catches a broken
    # trend AND one whose structure holds but was never fully measured, and
    # calling the second a failure states something the data does not say.
    "trend": "Long-term trend not confirmed",
    "regime": "Held back by the market regime",
    "earnings": "Held back by earnings",
    "entry": "No entry at this price",
    "support": "Not enough support confluence",
    "trigger": "Waiting on confirmation",
    "readiness": "Support not tested yet",
    "confirmed": "All four gates passed",
}


def _pct(v, nd=1, dash="—"):
    if v is None:
        return dash
    return f"{v:+.{nd}f}%"


def _score_cell(value, label=None, style="muted"):
    if value is None:
        return '<span style="color:#898781">—</span>'
    inner = f'<strong>{value}</strong>'
    if label:
        inner += f' {badge(label, style, "small")}'
    return inner


def _action_pill(action, icon):
    bg, fg = _ACTION_STYLE.get(action, ("#F1EFE8", "#444441"))
    return (f'<span style="background:{bg};color:{fg};font-size:10px;'
            f'font-weight:700;padding:3px 9px;border-radius:5px;'
            f'white-space:nowrap">{icon} {esc(action)}</span>')


# ─────────────────────────────────────────────────────────────────────────────
# The expanded panel — the audit behind one verdict
# ─────────────────────────────────────────────────────────────────────────────

def _list_block(title, items, mark, color):
    if not items:
        return ""
    lis = "".join(
        f'<li style="margin:3px 0;color:#444441">'
        f'<span style="color:{color};font-weight:700">{mark}</span> '
        f'{esc(item)}</li>' for item in items)
    return (f'<div style="margin-bottom:10px">'
            f'<div style="font-size:10px;text-transform:uppercase;'
            f'letter-spacing:.06em;color:#898781;margin-bottom:4px">'
            f'{esc(title)}</div>'
            f'<ul style="margin:0;padding-left:16px;font-size:12px;'
            f'list-style:none">{lis}</ul></div>')


def _quality_panel(q):
    if not q.get("components"):
        return empty("no fundamental data for this ticker")
    rows = []
    for c in sorted(q["components"], key=lambda x: -x["weight"]):
        weak = c["name"] in (q.get("weak_proxies") or [])
        pct = max(0, min(100, c["score"]))
        rows.append(
            f'<tr>'
            f'<td style="padding:3px 8px 3px 0;white-space:nowrap">'
            f'{esc(c["name"])}'
            + (' <span title="thin evidence for its weight — read as a '
               'tiebreaker" style="color:#8a6d1a">⚠</span>' if weak else '')
            + f'</td>'
            f'<td style="padding:3px 8px 3px 0;color:#898781">{c["weight"]}%</td>'
            f'<td style="padding:3px 8px 3px 0;width:90px">'
            f'<div style="background:#f1efea;border-radius:3px;height:6px">'
            f'<div style="width:{pct}%;height:100%;border-radius:3px;'
            f'background:{"#0F6E56" if pct >= 60 else "#8a6d1a" if pct >= 35 else "#A32D2D"}">'
            f'</div></div></td>'
            f'<td style="padding:3px 8px 3px 0"><strong>{c["score"]:.0f}</strong></td>'
            f'<td style="padding:3px 0;color:#898781">{esc(c["detail"])}</td>'
            f'</tr>')
    missing = ""
    if q.get("missing"):
        missing = (f'<div style="font-size:11px;color:#898781;margin-top:6px">'
                   f'Not measured: {esc(", ".join(q["missing"]))} — the score '
                   f'is renormalised over the {int(q["coverage"] * 100)}% of '
                   f'weight that had data, not padded with zeros.</div>')
    return (f'<table style="width:100%;border-collapse:collapse;font-size:11px">'
            f'{"".join(rows)}</table>{missing}')


def _valuation_panel(v):
    if not v.get("band"):
        return empty("could not be valued — "
                     + "; ".join(v.get("notes") or ["no method applied"]))
    head = f'<div style="font-size:12px;margin-bottom:8px">{esc(v["headline"] or "")}</div>'
    method = {"REVERSE_DCF": "Reverse DCF — what the price already assumes",
              "PEER": "Peer multiple — priced against its sector"}.get(
                  v["method"], v["method"] or "")
    bits = "".join(f'<li style="margin:3px 0">{esc(a)}</li>'
                   for a in (v.get("assumptions") or []))
    notes = "".join(
        f'<div style="font-size:11px;color:#8a6d1a;margin-top:5px">⚠ {esc(n)}</div>'
        for n in (v.get("notes") or []))
    return (head +
            f'<div style="font-size:10px;text-transform:uppercase;'
            f'letter-spacing:.06em;color:#898781;margin-bottom:4px">'
            f'{esc(method)}</div>'
            f'<ul style="margin:0;padding-left:16px;font-size:11px;'
            f'color:#444441">{bits}</ul>{notes}')


def _trend_panel(t):
    cells = []
    for c in t.get("checks", []):
        mark, color = ("✓", "#0F6E56") if c["ok"] else \
                      ("✗", "#A32D2D") if c["ok"] is False else ("?", "#898781")
        req = ' <span style="color:#898781">(required)</span>' if c["required"] else ""
        cells.append(f'<li style="margin:3px 0;font-size:11px">'
                     f'<span style="color:{color};font-weight:700">{mark}</span> '
                     f'{esc(c["name"])}{req} — '
                     f'<span style="color:#898781">{esc(c["detail"])}</span></li>')
    return f'<ul style="margin:0;padding-left:16px;list-style:none">{"".join(cells)}</ul>'


def _targets_panel(r):
    """Stop, T1, T2 and the risk/reward each implies.

    Both targets are shown because which one matters depends on the setup.
    Western Digital risks 2.7% to its shelf; to R1 that is 1.7:1, but to the
    8 EMA the level a deep pullback is trying to reclaim it is 4.8:1.
    Quoting only the nearer one would understate the trade threefold.
    """
    t = r.get("targets") or {}
    if not t.get("stop") or not t.get("ladder"):
        return empty("no stop or target — no tested level either side")
    rows = [f'<tr><td style="padding:3px 14px 3px 0;color:#A32D2D;'
            f'font-weight:600">Stop</td>'
            f'<td style="padding:3px 14px 3px 0;text-align:right">'
            f'<strong>${t["stop"]:,.2f}</strong></td>'
            f'<td style="padding:3px 14px 3px 0;text-align:right;'
            f'color:#A32D2D">{t["risk_pct"]:+.1f}%</td>'
            f'<td style="padding:3px 0;color:#898781">risk</td></tr>']
    for i, lv in enumerate(t["ladder"], 1):
        rows.append(
            f'<tr><td style="padding:3px 14px 3px 0;color:#0F6E56;'
            f'font-weight:600">T{i}</td>'
            f'<td style="padding:3px 14px 3px 0;text-align:right">'
            f'<strong>${lv["price"]:,.2f}</strong></td>'
            f'<td style="padding:3px 14px 3px 0;text-align:right;'
            f'color:#0F6E56">{lv["move_pct"]:+.1f}%</td>'
            f'<td style="padding:3px 0;color:#898781">'
            f'{esc(lv["name"])} · <strong>{lv["rr"]:.1f}:1</strong></td></tr>')
    tech = r.get("technical") or {}
    head = ""
    if tech.get("score") is not None:
        head = (f'<div style="font-size:11px;margin-bottom:6px">'
                f'Technical {tech["score"]}/100 — {esc(tech.get("label", ""))}'
                f'</div>')
    return head + (f'<table style="border-collapse:collapse;font-size:11px">'
                   f'{"".join(rows)}</table>')


def _levels_panel(r):
    """The moving-average reference prices, moved out of the table.

    Every level with its price and signed distance, plus which of them are
    clustered around the current price. Green when price is above the level
    (support underfoot), red when below it (resistance overhead).
    """
    p = r["pullback"]
    by, cluster = p.get("by_level") or {}, p.get("ma_cluster") or {}
    near = {l["name"] for l in cluster.get("levels", [])}
    rows = []
    for key, label in (("8EMA", "8 EMA"), ("21EMA", "21 EMA"),
                       ("50MA", "50 MA"), ("200MA", "200 MA"),
                       ("Prior_Breakout_Level", "prior breakout")):
        lv = by.get(key) or {}
        dist, price = lv.get("distance_pct"), lv.get("price")
        if price is None and dist is None:
            continue
        colour = "#898781" if dist is None else (
            "#0F6E56" if lv.get("held") else "#A32D2D")
        rows.append(
            f'<tr><td style="padding:3px 12px 3px 0;white-space:nowrap">'
            f'{esc(label)}'
            + (' <span style="color:#8a6d1a" title="inside the cluster">◆</span>'
               if label in near else '')
            + f'</td>'
            f'<td style="padding:3px 12px 3px 0;text-align:right">'
            f'{f"${price:,.2f}" if price is not None else "—"}</td>'
            f'<td style="padding:3px 0;text-align:right;color:{colour};'
            f'font-weight:600">{f"{dist:+.1f}%" if dist is not None else "—"}'
            f'</td></tr>')
    head = ""
    if cluster.get("count", 0) >= 2:
        head = (f'<div style="font-size:11px;margin-bottom:6px">'
                f'{esc(cluster["label"])} — {cluster["count"]} levels within '
                f'{cluster.get("within_pct", 2):.0f}% of the price'
                + (f', spanning {cluster["span_pct"]:.1f}%'
                   if cluster.get("span_pct") is not None else "")
                + '. A coil says a move resolves here, not which way.</div>')
    return head + (f'<table style="border-collapse:collapse;font-size:11px">'
                   f'{"".join(rows)}</table>')


def _scenarios_panel(r):
    """The buy triggers as a decision tree rather than one price.

    A quality name can become buyable three different ways — the trend
    repairs, support confirms, or the price comes to the valuation — and
    they are alternatives, not steps. Presenting only the last of them
    ("wait for $434.93") describes the least likely of the three as the
    only one.
    """
    groups = {"A": ("Technical recovery", []), "B": ("Support confirmation", []),
              "C": ("Valuation reset", []), "D": ("Deep value", []),
              "X": ("Invalidation", []), "": ("Other", [])}
    for t in r.get("triggers") or []:
        key = t[0] if t[:2] in ("A ", "B ", "C ", "D ") else (
            "X" if t.lower().startswith("invalidation") else "")
        body = t[4:] if key in ("A", "B", "C", "D") else t
        groups.setdefault(key, (key, []))[1].append(body)
    out = []
    for key in ("A", "B", "C", "D", "X", ""):
        label, items = groups.get(key, ("", []))
        if not items:
            continue
        colour = "#A32D2D" if key == "X" else "#0F6E56"
        mark = "✕" if key == "X" else (key or "→")
        out.append(
            f'<div style="margin-bottom:8px">'
            f'<div style="font-size:10px;text-transform:uppercase;'
            f'letter-spacing:.06em;color:{colour};font-weight:700;'
            f'margin-bottom:2px">{esc(mark)} {esc(label)}</div>'
            + "".join(f'<div style="font-size:11px;color:#444441;'
                      f'margin-left:12px">{esc(i)}</div>' for i in items)
            + '</div>')
    return "".join(out) or empty("no triggers computed")


def _range_panel(rng):
    """The 52-week range as a position, not just two numbers."""
    high, low, price = rng.get("high"), rng.get("low"), rng.get("price")
    if high is None and low is None:
        return empty("no 52-week range for this ticker")
    pos = rng.get("position_pct")
    bar = ""
    if pos is not None:
        bar = (
            f'<div style="position:relative;height:8px;border-radius:4px;'
            f'margin:8px 0 5px;background:linear-gradient(90deg,#E1F5EE,#FAEEDA,#FCEBEB)">'
            f'<div style="position:absolute;left:{pos:.1f}%;top:-3px;'
            f'width:2px;height:14px;background:#0b0b0b;'
            f'transform:translateX(-1px)"></div></div>')

    def cell(label, value, note):
        return (f'<div><div style="font-size:10px;text-transform:uppercase;'
                f'letter-spacing:.06em;color:#898781">{esc(label)}</div>'
                f'<div style="font-size:13px;font-weight:600">{value}</div>'
                f'<div style="font-size:10px;color:#898781">{esc(note)}</div></div>')

    from_high = rng.get("from_high_pct")
    from_low = rng.get("from_low_pct")
    return (
        f'<div style="display:flex;gap:22px;flex-wrap:wrap">'
        + cell("52W Low", f'${low:,.2f}' if low is not None else "—",
               f"{from_low:+.1f}% above it" if from_low is not None else "")
        + cell("Now", f'${price:,.2f}' if price is not None else "—",
               f"{pos:.0f}% up the range" if pos is not None else "")
        + cell("52W High", f'${high:,.2f}' if high is not None else "—",
               f"{from_high:+.1f}% from it" if from_high is not None else "")
        + '</div>' + bar
        + '<div style="font-size:10px;color:#898781">Distance from the high is '
          'the drawdown you are buying; distance from the low is the recovery '
          'you are paying for.</div>')


def _support_panel(conf, vol, pullback):
    def items(entries, mark, color):
        return "".join(
            f'<li style="margin:2px 0;font-size:11px">'
            f'<span style="color:{color};font-weight:700">{mark}</span> '
            f'{esc(e["name"])} <span style="color:#898781">+{e["points"]} · '
            f'{esc(e["detail"])}</span></li>' for e in entries)

    conf_html = (f'<div style="font-size:11px;margin-bottom:4px">'
                 f'<strong>{conf["score"]}/100</strong> {esc(conf["label"])} — '
                 f'{len(conf["hits"])} of 6 levels agree</div>'
                 f'<ul style="margin:0 0 8px;padding-left:14px;list-style:none">'
                 + items(conf["hits"], "✓", "#0F6E56")
                 + items(conf["misses"], "✗", "#A32D2D") + '</ul>')

    if vol.get("score") is None:
        vol_html = empty("pullback volume not measured — run a scan")
    else:
        vol_html = (f'<div style="font-size:11px;margin-bottom:4px">'
                    f'<strong>{vol["score"]}/100</strong> {esc(vol["label"])} '
                    f'<span style="color:#898781">({vol["measured"]} of 5 '
                    f'checks measured)</span></div>'
                    f'<ul style="margin:0;padding-left:14px;list-style:none">'
                    + items(vol["hits"], "✓", "#0F6E56")
                    + items(vol["misses"], "✗", "#A32D2D")
                    + items(vol.get("unknown") or [], "?", "#898781") + '</ul>')

    return (f'<div style="font-size:11px;color:#444441;margin-bottom:8px">'
            f'{esc(pullback["note"])}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">'
            f'<div>{conf_html}</div><div>{vol_html}</div></div>')


def _entries_panel(r):
    entries = r.get("entries") or []
    if not entries:
        return ""
    rows = "".join(
        f'<tr><td style="padding:3px 10px 3px 0">{esc(e["name"])}</td>'
        f'<td style="padding:3px 10px 3px 0"><strong>${e["price"]:,.2f}</strong></td>'
        f'<td style="padding:3px 10px 3px 0;color:#898781">'
        f'{_pct(e["move_pct"])} from here</td>'
        f'<td style="padding:3px 0;color:#898781">'
        + (f'{e["tranche_pct"]}% of target' if e["tranche_pct"] else "—")
        + '</td></tr>' for e in entries[:4])
    return (f'<div style="margin-top:10px">'
            f'<div style="font-size:10px;text-transform:uppercase;'
            f'letter-spacing:.06em;color:#898781;margin-bottom:4px">'
            f'Levels below the current price</div>'
            f'<table style="border-collapse:collapse;font-size:11px">{rows}</table>'
            f'</div>')


def _kv(label, value, strong=False, colour="#0b0b0b"):
    weight = "600" if strong else "400"
    return (f'<tr><td style="padding:3px 14px 3px 0;color:#898781;'
            f'white-space:nowrap">{esc(label)}</td>'
            f'<td style="padding:3px 0;font-weight:{weight};color:{colour};'
            f'white-space:nowrap">{value}</td></tr>')


def _pending_plan(plan) -> str:
    """The trade priced, for a name the engine has not cleared.

    Everything §4 says to always display — entry, stop, stop distance,
    risk/share — plus the ratio, and deliberately nothing that implies a
    position: no share count, no position value, no dollar risk. Those are
    the figures that read as permission.
    """
    entry, stop = plan.get("entry") or {}, plan.get("stop") or {}
    z, target = plan.get("sizing") or {}, plan.get("target") or {}
    if not entry.get("price") or not stop.get("price"):
        return ""
    rows = (_kv("Entry if it triggers", f'${entry["price"]:,.2f}', strong=True)
            + _kv("Entry type", esc(entry.get("type") or "")
                  + (f' <span style="color:#898781">· {esc(entry["level_name"])}</span>'
                     if entry.get("level_name") else ""))
            + _kv("Stop would be", f'${stop["price"]:,.2f}', strong=True)
            + _kv("Stop basis", f'{esc(stop.get("source") or "")} '
                                f'<span style="color:#898781">'
                                f'({esc(stop.get("method") or "")})</span>')
            + _kv("Risk / share", f'${z.get("risk_per_share", 0):,.2f}')
            + _kv("Stop distance", f'{z.get("stop_distance_pct", 0):.1f}%'))
    if target.get("rr") is not None:
        rows += _kv("R:R to first target",
                    f'{target["rr"]:.2f}R '
                    f'<span style="color:#898781">'
                    f'(${target["price"]:,.2f} {esc(target.get("name") or "")})</span>')
    return (f'<div style="margin-top:14px;padding-top:12px;'
            f'border-top:0.5px solid #f1efea">'
            f'<div style="{_PANEL_H}">The plan, if it clears</div>'
            f'<table style="border-collapse:collapse;font-size:11px">{rows}</table>'
            f'<div style="font-size:10px;color:#898781;margin-top:6px">'
            f'Prices shown so you know what to watch for. No share count '
            f'until the blocking condition above clears.</div></div>')


def _sizing_panel(r):
    """§9 — the whole sizing calculation, shown as arithmetic.

    Every intermediate is on screen (risk-based shares, allocation-limited
    shares, which one won) because the useful question is almost never "how
    many shares" but "why that many" — and a lone final number cannot answer
    the follow-up, which is whether to loosen the cap or skip the trade.
    """
    plan = r.get("sizing_plan") or {}
    s = plan.get("settings") or {}
    if not s:
        return ""

    account = (
        '<table style="border-collapse:collapse;font-size:11px">'
        + _kv("Trading capital", f'${s["capital"]:,.0f}')
        + _kv("Risk / trade", f'{s["risk_pct"]:g}%')
        + _kv("Maximum risk", f'${s["max_dollar_risk"]:,.0f}', strong=True)
        + _kv("Max allocation", f'{s["max_allocation_pct"]:g}% '
                                f'(${s["max_position_value"]:,.0f})')
        + '</table>')

    status = plan.get("status") or "NOT_ACTIONABLE"
    icon, label, tone = PS.STATUS.get(status, PS.STATUS["NOT_ACTIONABLE"])

    if not plan.get("ok"):
        # The refusal IS the output. Saying "N/A" without the reason is what
        # makes an engine look broken rather than disciplined.
        na = (f'<div style="font-size:13px;font-weight:600;margin-bottom:4px">'
              f'POSITION SIZE: N/A</div>'
              f'<div style="font-size:11px;color:#633806">'
              f'{icon} {esc(label)} — {esc(plan.get("reason") or "")}</div>'
              f'<div style="font-size:11px;color:#898781;margin-top:8px">'
              f'A size is withheld rather than computed from the risk budget '
              f'alone: ${s["max_dollar_risk"]:,.0f} of allowable loss is not '
              f'a reason to take a trade the engine has not cleared.</div>')
        # A blocked name still has a priced plan, and showing it is the
        # difference between "no read on this" and "here is the trade, and
        # here is what has to happen first".
        pending = _pending_plan(plan) if plan.get("pending") else ""
        return card("💰 Position sizing",
                    na + pending + '<div style="margin-top:12px">'
                    + account + '</div>', "")

    entry, stop = plan["entry"], plan["stop"]
    z, target = plan["sizing"], plan.get("target") or {}

    # §"quality but trend not present": the banner sits ABOVE the numbers,
    # because it changes how every figure under it should be read.
    override = ""
    if plan.get("quality_override"):
        override = (
            f'<div style="background:#FAEEDA;border:0.5px solid #e8d5aa;'
            f'border-radius:9px;padding:10px 13px;margin-bottom:12px;'
            f'font-size:11px;color:#633806">'
            f'⚠ <b>Quality override — trend not present.</b> '
            f'{esc(plan.get("warning") or "")}'
            f'<div style="margin-top:6px">The engine would normally withhold '
            f'a size here. It is shown because the business clears the '
            f'LQuality {PS.QUALITY_OVERRIDE_MIN}+ bar, so treat it as "where '
            f'I would buy this if the chart repaired" rather than a trade to '
            f'put on today.</div></div>')

    plan_tbl = (
        '<table style="border-collapse:collapse;font-size:11px">'
        + _kv("Entry", f'${entry["price"]:,.2f}', strong=True)
        + _kv("Entry type", esc(entry["type"])
              + (f' <span style="color:#898781">· {esc(entry["level_name"])}</span>'
                 if entry.get("level_name") else ""))
        + _kv("Stop", f'${stop["price"]:,.2f}', strong=True)
        + _kv("Stop basis", f'{esc(stop["source"] or "")} '
                            f'<span style="color:#898781">({esc(stop["method"] or "")})</span>')
        + _kv("Risk / share", f'${z["risk_per_share"]:,.2f}')
        + _kv("Stop distance", f'${z["stop_distance"]:,.2f} · '
                               f'{z["stop_distance_pct"]:.1f}%')
        + '</table>')

    # The two candidate sizes side by side, with the binding one in bold —
    # this is the panel's whole argument.
    bound = z["bound_by"]
    size_tbl = (
        '<table style="border-collapse:collapse;font-size:11px">'
        + _kv("Risk-based shares", f'{z["risk_shares"]:,}',
              strong=bound == "risk",
              colour="#0b0b0b" if bound == "risk" else "#898781")
        + _kv("Allocation-limited", f'{z["allocation_shares"]:,}',
              strong=bound == "allocation",
              colour="#0b0b0b" if bound == "allocation" else "#898781")
        + _kv("Final position", f'{z["shares"]:,} shares', strong=True)
        + _kv("Position value", f'${z["position_value"]:,.0f}')
        + _kv("Allocation", f'{z["allocation_pct"]:.1f}% of capital')
        + _kv("Actual risk", f'${z["actual_risk"]:,.0f} '
                             f'({z["actual_risk_pct"]:.1f}%)', strong=True)
        + '</table>'
        + f'<div style="font-size:10px;color:#898781;margin-top:6px">'
          f'Bound by <b>{esc(bound)}</b> — '
          + esc("the allocation cap cut the risk-based size"
                if bound == "allocation"
                else "risk sizing came in under the allocation cap")
        + '</div>')

    # ── R-multiple (§10) ────────────────────────────────────────────────────
    if target.get("rr") is not None:
        # The chosen rung is bold; the ones the target steps over are greyed.
        # Seeing WHICH levels the trade has to get through is the point of
        # showing the ladder at all.
        rungs = ""
        for lv in (target.get("ladder") or []):
            chosen = lv["price"] == target["price"]
            style = ("font-weight:600" if chosen else "color:#898781")
            rungs += (
                f'<tr style="{style}">'
                f'<td style="padding:2px 12px 2px 0">${lv["price"]:,.2f}</td>'
                f'<td style="padding:2px 12px 2px 0">{esc(lv["name"] or "")}</td>'
                f'<td style="padding:2px 12px 2px 0">{lv["rr"]:.2f}R</td>'
                f'<td style="padding:2px 0">'
                f'${z["shares"] * lv["profit_per_share"]:,.0f}</td></tr>')
        rr_colour = ("#0F6E56" if target["rr"] >= 2.0
                     else "#8a6d1a" if target["rr"] >= 1.5 else "#A32D2D")
        if target.get("reached_min_rr") is False:
            why = (f'Nothing on this chart reaches {PS.TARGET_MIN_RR:g}R — '
                   f'this is the best available, not a chosen target.')
        elif target.get("skipped"):
            why = (f'First level clearing {PS.TARGET_MIN_RR:g}R. '
                   f'{target["skipped"]} nearer level(s) are resistance the '
                   f'trade has to get through, not targets worth taking.')
        else:
            why = f'First level above the entry, and it clears {PS.TARGET_MIN_RR:g}R.'
        reward = (
            f'<div style="font-size:12px">'
            f'Risk <b>${z["actual_risk"]:,.0f}</b> · '
            f'Potential profit <b>${plan.get("potential_profit") or 0:,.0f}</b> · '
            f'<b style="color:{rr_colour}">{target["rr"]:.2f}R</b> '
            f'<span style="color:#898781">{esc(target.get("label") or "")}</span>'
            f'</div>'
            f'<div style="font-size:10px;color:#898781;margin-top:4px">'
            f'{esc(why)}</div>'
            f'<table style="border-collapse:collapse;font-size:11px;margin-top:8px">'
            f'<tr style="color:#898781"><th style="text-align:left;padding-right:12px">Target</th>'
            f'<th style="text-align:left;padding-right:12px">Level</th>'
            f'<th style="text-align:left;padding-right:12px">R</th>'
            f'<th style="text-align:left">Profit</th></tr>{rungs}</table>')
    else:
        reward = empty("No tracked level above the entry — R:R is not "
                       "calculated rather than assumed.")

    assessment = (
        f'<div style="font-size:12px">{icon} <b>{esc(label)}</b> — '
        f'{esc(plan.get("reason") or "")}</div>'
        f'<div style="font-size:11px;color:#898781;margin-top:6px">'
        f'Position grade <b>{esc(plan.get("grade") or "—")}</b> — this rates '
        f'the SETUP, not the company. LQuality is the business; a great one '
        f'with a wide stop still earns a small position.</div>')

    grid = (
        override
        + '<div style="display:grid;'
        'grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px">'
        f'<div><div style="{_PANEL_H}">Account</div>{account}</div>'
        f'<div><div style="{_PANEL_H}">The trade</div>{plan_tbl}</div>'
        f'<div><div style="{_PANEL_H}">Sizing</div>{size_tbl}</div>'
        f'<div><div style="{_PANEL_H}">Reward</div>{reward}</div>'
        '</div>'
        f'<div style="margin-top:12px">{assessment}</div>')
    return card("💰 Position sizing", grid, "")


_PANEL_H = ('font-size:10px;text-transform:uppercase;letter-spacing:.06em;'
            'color:#898781;margin-bottom:5px')


# Same palette the Research Library's Position column uses, so "Dominant"
# reads identically on both pages.
_POSITION_COLOR = {"dominant": "#0F6E56", "duopoly": "#0C447C",
                   "top2": "#8a6d1a", "rest": "#898781"}


def _position_line(p) -> str:
    """Competitive standing, with the basis of the claim attached to it.

    Two very different statements share this line, and they are styled apart
    on purpose. A curated data/market_structure.json entry ("EUV monopoly")
    is a structural fact someone verified, and gets the ● that marks it as
    such. Everything else is market-cap rank among the names in THIS library
    — a size proxy, not market share — and says so in full rather than in a
    tooltip, because "Dominant" next to a description of the business reads
    as a claim about the market unless something stops it.
    """
    structure = p.get("structure")
    if structure:
        note = p.get("structure_note") or "curated in data/market_structure.json"
        return (f'<span style="color:#0F6E56;font-weight:700" '
                f'title="{esc(note)}">● {esc(structure)}</span>'
                f'<div style="font-size:10px;color:#898781;margin-top:2px">'
                f'{esc(note)}</div>')

    group, rank, count = p.get("peer_group"), p.get("peer_rank"), p.get("peer_count")
    if not rank or not count:
        where = f" in {esc(group)}" if group else ""
        return (f'<span style="font-size:11px;color:#898781">No peer group'
                f'{where} — nothing in this library to rank it against.</span>')

    colour = _POSITION_COLOR.get(p.get("position_tier"), "#898781")
    weight = "400" if p.get("position_tier") == "rest" else "700"
    share = p.get("peer_share_pct")
    scope = "sector" if p.get("peer_group_is_sector") else "industry"
    bits = [f'#{rank} of {count} tracked {scope} peers']
    if share is not None:
        bits.append(f'{share:.0f}% of their combined market cap')
    return (f'<span style="color:{colour};font-weight:{weight}">'
            f'{esc(p.get("position_label") or f"#{rank}")}</span>'
            f'<span style="font-size:11px;color:#52514e"> · {esc(" · ".join(bits))}'
            f'</span>'
            f'<div style="font-size:10px;color:#898781;margin-top:2px">'
            f'Ranked on market cap across {esc(group or scope)} names in this '
            f'library — a size proxy, not market share. A verified structure '
            f'("duopoly with …") comes from data/market_structure.json, which '
            f'ships empty on purpose.</div>')

_ZONE_TONE = {"Exceptional": "#0F6E56", "Attractive": "#0F6E56",
              "Fair": "#8a6d1a", "Expensive": "#A32D2D",
              "Extreme": "#A32D2D", "Not priced": "#898781"}

_TIER_TONE = {"excellent": ("#0C447C", "#E6F1FB"),
              "preferred": ("#0F6E56", "#E1F5EE"),
              "aggressive": ("#8a6d1a", "#FAEEDA")}


def _check_row(c) -> str:
    mark = ('<span style="color:#0F6E56">✓</span>' if c["ok"] else
            '<span style="color:#A32D2D">✗</span>' if c["ok"] is False else
            '<span style="color:#898781">?</span>')
    colour = "#0b0b0b" if c["ok"] else "#791F1F" if c["ok"] is False else "#898781"
    return (f'<tr><td style="padding:2px 8px 2px 0">{mark}</td>'
            f'<td style="padding:2px 10px 2px 0;font-weight:600;color:{colour};'
            f'white-space:nowrap">{esc(c["name"])}</td>'
            f'<td style="padding:2px 0;color:#52514e">{esc(c["detail"])}</td></tr>')


_STRENGTH_TONE = {"🟢 Major": "#085041", "🟡 Strong": "#6B5900",
                  "🟠 Moderate": "#8A4B00", "🔴 Weak": "#791F1F"}

_THESIS_TONE = {"INTACT": ("#E1F5EE", "#085041"),
                "STRAINED": ("#FDF1E3", "#8A4B00"),
                "BROKEN": ("#FCEBEB", "#791F1F"),
                "UNMEASURED": ("#F0EEE6", "#52514e")}


def _thesis_block(th) -> str:
    """Whether the company is still the one worth adding to.

    Sits directly under the action banner and above the buy checks, because
    it answers the question that comes FIRST for anything already owned. The
    action banner says whether to open a position at today's price; this says
    whether a lower price would be an opportunity or a warning, and the two
    routinely disagree — WAIT on the banner with the thesis intact is the
    ordinary state of a good business that is merely not cheap.

    The three legs are listed separately rather than rolled into the
    headline. A business break and a chart break both read BROKEN and mean
    entirely different things about what to do next.
    """
    if not th.get("state"):
        return ""
    bg, fg = _THESIS_TONE.get(th["state"], _THESIS_TONE["UNMEASURED"])
    # `failures`, never `broken`. Condition names are positive assertions
    # ("200 MA not falling"), so a leg summary built from them prints the
    # reverse of what it means — the same trap thesis._condition documents.
    legs = " · ".join(
        f'{l["icon"]} {esc(l["label"])}'
        + (f' ({esc(", ".join(l["failures"]))})' if l.get("failures") else "")
        for l in th.get("legs") or [])
    inval = th.get("invalidation_price")
    return (f'<div style="background:{bg};color:{fg};padding:9px 12px;'
            f'border-radius:8px;margin-bottom:12px;font-size:12px">'
            f'<strong>{th.get("icon","")} Thesis {esc(th["state"].title())}'
            f'</strong>'
            + (f' <span style="font-size:11px">· DCA invalidation '
               f'${inval:,.2f} — not the trading stop</span>'
               if inval is not None else "")
            + f'<div style="margin-top:4px">{esc(th.get("headline") or "")}</div>'
            + (f'<div style="font-size:10px;margin-top:5px;opacity:.8">{legs}'
               f'</div>' if legs else "")
            + '</div>')


def _technical_row(z) -> str:
    """One support zone, carrying the valuation reading AND the support
    strength AT ITS OWN PRICE.

    The facts share a line on purpose. Printing "50 MA $386–395" in one panel
    and "the price is Extreme" in another is what let a support level read as
    a buy zone.

    Strength is the column that separates the rungs. The valuation reading
    measured out nearly flat down the ladder — same verdict at the 8/21 EMA
    and the 200 MA for 55 of 56 quality names — so a table carrying only that
    gave all three bands equal weight and left the reader with no reason to
    prefer one. Strength is a property of the LEVEL rather than of today's
    price, so it still discriminates when every valuation cell reads Extreme.
    """
    tone = _ZONE_TONE.get(z.get("value_zone"), "#898781")
    st = z.get("strength") or {}
    st_tone = _STRENGTH_TONE.get(st.get("label"), "#898781")
    # The basis, so a 79 is legible as a claim rather than a number: the
    # level it is, plus whatever independently agrees with it there.
    st_basis = ", ".join(filter(None, [st.get("identity")]
                                + list(st.get("agreeing") or [])))
    where = ("price is here now" if z.get("reached") == "inside"
             else f'{z["distance_pct"]:+.0f}%' if z.get("distance_pct") is not None
             else "")
    cagr = (f'{z["expected_cagr_pct"]:+.0f}%/yr'
            if z.get("expected_cagr_pct") is not None else "—")
    fund = (f'{z["fundamental_downside_pct"]:+.0f}% to model value'
            if z.get("fundamental_downside_pct") is not None else "—")
    down = (f'{z["downside_pct"]:+.0f}% to {esc(z["downside_note"])}'
            if z.get("downside_pct") is not None else esc(z["downside_note"]))
    return (f'<tr><td style="padding:5px 10px 5px 0;font-weight:600;'
            f'white-space:nowrap">{esc(z["label"])}</td>'
            f'<td style="padding:5px 10px 5px 0;font-weight:700;'
            f'white-space:nowrap">${z["low"]:,.0f} – ${z["high"]:,.0f}</td>'
            f'<td style="padding:5px 10px 5px 0;color:#898781;'
            f'white-space:nowrap">{esc(where)}</td>'
            + (f'<td style="padding:5px 10px 5px 0;color:{st_tone};'
               f'font-weight:600;white-space:nowrap" title="{esc(st_basis)}">'
               f'{st["score"]} {esc(st.get("label") or "")}</td>'
               if st.get("score") is not None else
               '<td style="padding:5px 10px 5px 0;color:#898781">—</td>')
            + f'<td style="padding:5px 10px 5px 0;color:{tone};font-weight:600;'
            f'white-space:nowrap">{esc(z.get("value_icon") or "")} '
            f'{esc(z.get("value_zone") or "")}</td>'
            f'<td style="padding:5px 10px 5px 0;color:{tone};'
            f'white-space:nowrap">{esc(cagr)}</td>'
            f'<td style="padding:5px 10px 5px 0;color:#898781;'
            f'white-space:nowrap">{esc(down)}</td>'
            f'<td style="padding:5px 0;color:#898781;'
            f'white-space:nowrap">{esc(fund)}</td></tr>')


def _fundamental_block(fu) -> str:
    if fu.get("blocked"):
        return (f'<div style="font-size:11px;color:#898781">'
                f'{esc(fu["blocked"])}</div>')

    gap = fu.get("fair_value_gap_pct")
    tone = _ZONE_TONE.get(fu.get("zone"), "#898781")
    head = (
        f'<div style="display:flex;gap:20px;flex-wrap:wrap;font-size:12px;'
        f'margin-bottom:8px">'
        f'<div><div style="font-size:10px;color:#898781;text-transform:uppercase">'
        f'Model value</div><strong>${fu["intrinsic"]:,.2f}</strong></div>'
        + (f'<div><div style="font-size:10px;color:#898781;'
           f'text-transform:uppercase">Price vs it</div>'
           f'<strong style="color:{tone}">{gap:+.0f}%</strong></div>'
           if gap is not None else "")
        + (f'<div><div style="font-size:10px;color:#898781;'
           f'text-transform:uppercase">Expected return</div>'
           f'<strong style="color:{tone}">'
           f'{fu["expected_cagr_now"]:+.1f}%/yr</strong></div>'
           if fu.get("expected_cagr_now") is not None else "")
        + (f'<div><div style="font-size:10px;color:#898781;'
           f'text-transform:uppercase">Hurdle · {esc(fu.get("quality_tier") or "")}'
           f'</div><strong>{fu["hurdle_pct"]:.0f}%/yr</strong></div>'
           if fu.get("hurdle_pct") else "")
        + (f'<div><div style="font-size:10px;color:#898781;'
           f'text-transform:uppercase">Today</div>'
           f'<strong style="color:{tone}">{esc(fu.get("zone_icon") or "")} '
           f'{esc(fu.get("zone") or "")}</strong></div>')
        + '</div>')

    rungs = "".join(
        f'<tr><td style="padding:3px 12px 3px 0;font-weight:600">'
        f'{esc(r["label"])}</td>'
        f'<td style="padding:3px 12px 3px 0;font-weight:700;white-space:nowrap">'
        f'at or below ${r["price"]:,.2f}</td>'
        f'<td style="padding:3px 0;color:#898781;white-space:nowrap">'
        f'earns {r["cagr_pct"]:.0f}%/yr</td></tr>' for r in fu.get("ladder") or [])

    caveat = (f'<div style="background:#FAEEDA;color:#633806;font-size:11px;'
              f'padding:8px 10px;border-radius:8px;margin:8px 0">⚠ '
              f'{esc(fu["caveat"])}</div>' if fu.get("caveat") else "")

    note = (f'<div style="font-size:10px;color:#898781;margin-top:6px">'
            f'Projected at {esc(fu.get("projection_note") or "")}. Assumes the '
            f'market eventually pays the model\'s value — a company priced '
            f'above it for a decade may stay there. A hurdle test, not a '
            f'forecast.</div>' if fu.get("projection_note") else "")

    return head + caveat + (f'<table>{rungs}</table>' if rungs else "") + note


def _investment_block(zones, verdict) -> str:
    if not zones:
        return (f'<div style="background:#FCEBEB;color:#791F1F;font-size:12px;'
                f'padding:10px 12px;border-radius:8px">'
                f'<strong>No investment buy zone.</strong> '
                f'{esc(verdict.get("what_would_change") or "")}</div>')
    out = []
    for z in zones:
        colour, bg = _TIER_TONE.get(z["key"], ("#444441", "#F1EFE8"))
        state = ("price is in this zone now" if z.get("reached")
                 else "not reached — a level to wait for")
        out.append(
            f'<div style="border:0.5px solid {colour};background:{bg};'
            f'border-radius:10px;padding:9px 12px;margin-bottom:6px">'
            f'<div style="display:flex;gap:10px;align-items:baseline;'
            f'flex-wrap:wrap">'
            f'<span style="color:{colour};font-weight:700">{esc(z["icon"])} '
            f'{esc(z["label"])}</span>'
            f'<span style="font-weight:700;font-size:14px">${z["low"]:,.0f} – '
            f'${z["high"]:,.0f}</span>'
            f'<span style="font-size:11px;color:#52514e">{esc(state)}</span>'
            f'</div>'
            f'<div style="font-size:11px;color:#52514e;margin-top:3px">'
            f'{esc(z["support"])} support · earns at least '
            f'{z["min_cagr_pct"]:.0f}%/yr here · needs LQuality '
            f'{z["min_quality"]}+</div></div>')
    return "".join(out)


def _buy_zone_panel(r) -> str:
    """Valuation decides whether; support decides when; the zone is where
    they overlap.

    Laid out in that order deliberately. An earlier version led with the
    support bands and appended a valuation warning, which produced "price is
    inside this band now" directly above "no band qualifies" — two true
    statements that should never have shared a panel.
    """
    bz = r.get("buy_zones") or {}
    tech, fu = bz.get("technical") or [], bz.get("fundamental") or {}
    verdict = bz.get("verdict") or {}
    if not tech and not fu.get("intrinsic"):
        return empty("Nothing to build zones from — run a scan.")

    action = verdict.get("action")
    conf = verdict.get("confidence") or {}
    ok = action == "BUY"
    banner = (
        f'<div style="background:{"#E1F5EE" if ok else "#FCEBEB"};'
        f'color:{"#085041" if ok else "#791F1F"};padding:10px 12px;'
        f'border-radius:8px;margin-bottom:12px">'
        f'<strong style="font-size:14px">{esc(action or "—")}</strong>'
        + (f' <span style="font-size:11px">· {esc(conf["level"])} confidence '
           f'({esc(conf.get("note") or "")})</span>' if conf.get("level") else "")
        + f'<div style="font-size:12px;margin-top:4px">'
          f'{esc(verdict.get("what_would_change") or "")}</div></div>')

    checks = ('<table style="font-size:11px;margin-bottom:14px">'
              + "".join(_check_row(c) for c in verdict.get("checks") or [])
              + '</table>')

    def section(title, body, sub=""):
        return (f'<div style="margin-bottom:14px">'
                f'<div style="font-size:10px;color:#898781;'
                f'text-transform:uppercase;letter-spacing:.3px">{esc(title)}</div>'
                + (f'<div style="font-size:10px;color:#898781;margin-bottom:5px">'
                   f'{esc(sub)}</div>' if sub else '<div style="height:5px"></div>')
                + body + '</div>')

    # Headers earn their place now that the row carries two scores. "79 🟢
    # Major" and "🔴 Extreme" in adjacent cells are answering different
    # questions, and unlabelled they read as one confused verdict.
    tech_head = (
        '<tr>' + "".join(
            f'<th style="padding:0 10px 4px 0;text-align:left;font-weight:600;'
            f'color:#898781;font-size:10px;text-transform:uppercase;'
            f'letter-spacing:.3px;white-space:nowrap">{h}</th>'
            for h in ("Level", "Zone", "Distance", "Support strength",
                      "Value here", "Return here", "To next level",
                      "To model value")) + '</tr>')
    tech_body = ('<table style="font-size:11px">' + tech_head
                 + "".join(_technical_row(z) for z in tech) + '</table>'
                 + (f'<div style="font-size:10px;color:#898781;margin-top:5px">'
                    f'⚠ {esc(bz["inverted"])}</div>' if bz.get("inverted") else ""))

    return (banner + _thesis_block(r.get("thesis") or {}) + checks
            + section("Investment buy zone",
                      _investment_block(bz.get("investment") or [], verdict),
                      "Where valuation and support overlap. Empty is a result.")
            + section("Fundamental — what a price would earn",
                      _fundamental_block(fu),
                      "Independent of the chart.")
            + section("Technical pullback zones", tech_body,
                      "Where support sits. A level existing is not a reason "
                      "to buy — each row carries what that price would earn."))

_SWING_STATE = {"READY": ("#0F6E56", "#E1F5EE", "🟢"),
                "WATCH": ("#0C447C", "#E6F1FB", "🔵"),
                "NO TRADE": ("#898781", "#F1EFE8", "⚪"),
                "BREAKDOWN": ("#791F1F", "#FCEBEB", "🔴"),
                "NEAR READY": ("#0F6E56", "#E1F5EE", "🟢"),
                "TRIGGERED": ("#0C447C", "#E6F1FB", "🔵"),
                "APPROACHING": ("#8a6d1a", "#FAEEDA", "🟡"),
                "DEVELOPING": ("#8a6d1a", "#FAEEDA", "🟠"),
                "EXTENDED": ("#A32D2D", "#FAEEDA", "🟠"),
                "MISSED": ("#A32D2D", "#FAEEDA", "🟠"),
                "AVOID": ("#791F1F", "#FCEBEB", "🔴"),
                "FAILED": ("#791F1F", "#FCEBEB", "🔴"),
                "NO SETUP": ("#898781", "#F1EFE8", "⚪")}

# One dot per component, so the six readings can be scanned without reading
# the table underneath them.
_DOT = ((75, "🟢"), (55, "🟡"), (0, "🔴"))


def _swing_dot(score) -> str:
    if score is None:
        return "⚪"
    return next(d for floor, d in _DOT if score >= floor)


def _swing_panel(r) -> str:
    """The swing verdict: what it is, what would trigger it, and what would
    invalidate it.

    Laid out as verdict → readings → triggers → risk plan, because the score
    is the least actionable thing here. An earlier version led with the
    number and buried the trigger, which invited reading "69/100" as a
    decision rather than as a ranking.
    """
    sw = r.get("swing") or {}
    if not sw or sw.get("score") is None:
        return empty("No swing reading — run a scan.")

    colour, bg, icon = _SWING_STATE.get(sw.get("state"),
                                        ("#898781", "#F1EFE8", "⚪"))
    grade = sw.get("grade") or "—"
    tone = ("good" if grade in ("A+", "A") else "watch" if grade == "B"
            else "bad")
    head = (
        f'<div style="background:{bg};border-radius:8px;padding:10px 12px;'
        f'margin-bottom:10px">'
        f'<div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">'
        f'<span style="font-size:18px;font-weight:700;color:{colour}">'
        f'{sw["score"]}<span style="font-size:11px;font-weight:400">/100</span>'
        f'</span>{badge(grade, tone)}'
        f'<span style="font-weight:700;color:{colour}">{icon} '
        f'{esc(sw.get("state") or "")}</span>'
        f'<span style="font-size:11px;color:#52514e">'
        f'{esc(sw.get("action") or "")}</span>'
        f'<span style="margin-left:auto;font-size:12px;color:#52514e">'
        f'{esc(sw.get("setup_label") or "")}</span></div>'
        # Chart verdict and trade verdict, side by side and never merged.
        f'<div style="font-size:11px;color:#52514e;margin-top:4px">'
        f'Chart {esc((sw.get("stock_view") or {}).get("icon") or "")} '
        f'{esc((sw.get("stock_view") or {}).get("label") or "")}'
        f' · Trade {esc(sw.get("state") or "")} — '
        f'{esc(sw.get("state_why") or "")}</div>'
        f'<div style="font-size:12px;color:#52514e;margin-top:5px">'
        f'{esc(sw.get("thesis") or "")}</div></div>')

    # The six readings as a dot row — the "Trend 🟢 / Momentum 🔴" summary.
    dots = "".join(
        f'<div style="min-width:96px"><div style="font-size:10px;'
        f'color:#898781;text-transform:uppercase">{esc(c["name"])}</div>'
        f'<div style="font-weight:600">{_swing_dot(c["score"])} '
        f'{c["score"]:.0f}</div></div>'
        for c in sw.get("components") or [])
    dot_row = (f'<div style="display:flex;gap:12px;flex-wrap:wrap;'
               f'font-size:11px;margin-bottom:10px">{dots}</div>')

    comps = "".join(
        f'<tr><td style="padding:2px 10px 2px 0;white-space:nowrap">'
        f'{esc(c["name"])}</td>'
        f'<td style="padding:2px 8px 2px 0;color:#898781">w{c["weight"]}</td>'
        f'<td style="padding:2px 10px 2px 0;font-weight:600;text-align:right">'
        f'{c["score"]:.0f}</td>'
        f'<td style="padding:2px 0;color:#52514e">{esc(c["detail"])}</td></tr>'
        for c in sw.get("components") or [])

    # ── triggers ────────────────────────────────────────────────────────────
    trig = sw.get("triggers") or {}

    def trigger_line(stage, label, colour_):
        if not stage:
            return ""
        mark = "✓ met" if stage.get("met") else f'{stage["distance_pct"]:+.1f}% away'
        return (f'<div style="margin-bottom:3px"><span style="color:{colour_};'
                f'font-weight:700">{esc(label)}</span> '
                f'<strong>${stage["price"]:,.2f}</strong> '
                f'<span style="color:#898781">— {esc(stage["condition"])} '
                f'({esc(mark)})</span></div>')

    chase = trig.get("max_chase") or {}
    chase_html = ""
    if chase.get("price"):
        exceeded = chase.get("exceeded")
        chase_html = (
            f'<div style="margin-top:5px;color:{"#791F1F" if exceeded else "#898781"}">'
            f'{"🔴 MISSED — " if exceeded else ""}No chase above '
            f'<strong>${chase["price"]:,.2f}</strong> — {esc(chase["note"])}</div>')

    triggers_html = ""
    if trig.get("early") or trig.get("full"):
        triggers_html = (
            f'<div style="background:#E6F1FB;font-size:11px;padding:9px 11px;'
            f'border-radius:8px;margin-bottom:10px">'
            f'<div style="font-size:10px;color:#898781;text-transform:uppercase;'
            f'margin-bottom:4px">Trigger</div>'
            + trigger_line(trig.get("early"), "Early", "#8a6d1a")
            + trigger_line(trig.get("full"), "Full", "#0F6E56")
            + chase_html + '</div>')

    # ── levels, split by which side of the trigger they sit on ─────────────
    path = sw.get("path") or {}

    def level_list(title, items, fmt):
        if not items:
            return ""
        return (f'<div style="margin-bottom:6px">'
                f'<div style="font-size:10px;color:#898781;'
                f'text-transform:uppercase">{esc(title)}</div>'
                + "".join(f'<div>{esc(i["name"])} '
                          f'<strong>${i["price"]:,.2f}</strong> '
                          f'<span style="color:#898781">{fmt(i)}</span></div>'
                          for i in items) + '</div>')

    levels_html = (
        f'<div style="font-size:11px;margin-bottom:10px">'
        + level_list("Pre-trigger resistance — must clear to reach the entry",
                     path.get("to_clear"),
                     lambda i: f'{i["move_pct"]:+.1f}% from here')
        + level_list("Post-entry support", path.get("support_after_entry"),
                     lambda i: f'{i["below_entry_pct"]:+.1f}% vs entry')
        + level_list("Target", path.get("levels"),
                     lambda i: f'{i["r"]:.2f}R · {i["move_pct"]:+.1f}%')
        + '</div>')

    path_rows = "".join(
        f'<tr><td style="padding:2px 10px 2px 0">{esc(c["name"])}</td>'
        f'<td style="padding:2px 8px 2px 0;color:#898781">w{c["weight"]}</td>'
        f'<td style="padding:2px 10px 2px 0;text-align:right;font-weight:600">'
        f'{c["score"]:.0f}</td>'
        f'<td style="padding:2px 0;color:#52514e">{esc(c["detail"])}</td></tr>'
        for c in sw.get("path_components") or [])
    path_html = (f'<div style="font-size:10px;color:#898781;'
                 f'text-transform:uppercase;margin:8px 0 3px">Path quality — '
                 f'{esc(path.get("quality") or "—")}</div>'
                 f'<table style="font-size:11px">{path_rows}</table>'
                 if path_rows else "")

    # ── the risk plan ──────────────────────────────────────────────────────
    inval, ts = sw.get("invalidation") or {}, sw.get("time_stop") or {}
    risk = sw.get("risk_pct") or {}
    plan_rows = [("Entry", f'${sw["entry"]:,.2f}' if sw.get("entry") else "—",
                  sw.get("entry_note") or "")]
    if inval.get("price"):
        plan_rows.append(("Invalidation", f'${inval["price"]:,.2f}',
                          inval["condition"]))
    if sw.get("stop"):
        plan_rows.append(("Hard stop", f'${sw["stop"]:,.2f}',
                          sw.get("stop_note") or ""))
    if ts.get("min_days"):
        plan_rows.append(("Expected hold",
                          f'{ts["min_days"]}–{ts["max_days"]} days',
                          f'cut it if it has not moved within '
                          f'{ts["review_days"]}'))
    plan_rows.append(("Risk",
                      (f'{risk.get("max")}% max' if risk.get("max")
                       else "no size"),
                      risk.get("note") or ""))
    # Sized on the same account and the same arithmetic the Position sizing
    # panel below uses — the difference is the risk budget, which is stated.
    sz = sw.get("sizing") or {}
    if sz.get("shares"):
        plan_rows.append(
            ("Position",
             f'{sz["shares"]:,} sh · ${sz["position_value"]:,.0f}',
             f'{sz["allocation_pct"]:.1f}% of capital · risk '
             f'${sz["actual_risk"]:,.0f} · bound by {sz["bound_by"]} · '
             f'{sz.get("note") or ""}'))
    plan = ('<table style="font-size:11px">' + "".join(
        f'<tr><td style="padding:3px 12px 3px 0;color:#898781;'
        f'white-space:nowrap">{esc(label)}</td>'
        f'<td style="padding:3px 12px 3px 0;font-weight:700;'
        f'white-space:nowrap">{esc(value)}</td>'
        f'<td style="padding:3px 0;color:#52514e">{esc(note)}</td></tr>'
        for label, value, note in plan_rows) + '</table>')

    gate_html = ""
    if sw.get("gates"):
        chips = []
        for g in sw["gates"]:
            mark = "✓" if g["ok"] else "✗" if g["ok"] is False else "?"
            colour = ("#0F6E56" if g["ok"] else
                      "#A32D2D" if g["ok"] is False else "#898781")
            weight = "700" if g.get("blocking") else "400"
            chips.append(f'<span title="{esc(g["detail"])}" '
                         f'style="color:{colour};font-weight:{weight};'
                         f'margin-right:10px;white-space:nowrap">{mark} '
                         f'{esc(g["name"])}</span>')
        chips.append('<span style="color:#898781">— bold gates block the '
                     'trade; the rest are advisory</span>')
        gate_html = (f'<div style="font-size:11px;margin-bottom:10px">'
                     f'<div style="font-size:10px;color:#898781;'
                     f'text-transform:uppercase;margin-bottom:3px">Gates</div>'
                     + "".join(chips) + '</div>')

    return (head + dot_row + gate_html + triggers_html + levels_html
            + f'<table style="font-size:11px">{comps}</table>'
            + path_html
            + f'<div style="font-size:10px;color:#898781;'
              f'text-transform:uppercase;margin:10px 0 3px">Risk plan</div>'
            + plan
            + '<div style="font-size:10px;color:#898781;margin-top:8px">'
              'Computed from price, volume and structure only — this engine '
              'reads no quality or valuation input, so it disagrees with the '
              'company verdict by design.</div>')


def _profile_panel(r) -> str:
    """What the company does and where it stands — the context the scores
    cannot carry.

    Deliberately the first thing in the reasoning: every panel below it
    judges the business, and judging one you cannot name is the gap this
    fills. Nothing here feeds a gate.
    """
    p = r.get("profile") or {}
    sector, industry = p.get("sector") or r.get("sector"), p.get("industry")
    chips = " ".join(badge(x, "muted", "small") for x in (sector, industry) if x)

    desc = p.get("description")
    body = (f'<div style="font-size:12px;color:#0b0b0b;line-height:1.5;'
            f'margin-top:8px">{esc(desc)}</div>' if desc else
            f'<div style="font-size:11px;color:#898781;margin-top:8px">'
            f'No business description in the library for this name — it comes '
            f'from the scan\'s BusinessSummary column.</div>')

    return (f'<div>{chips}</div>{body}'
            f'<div style="margin-top:10px;padding-top:9px;'
            f'border-top:0.5px solid #f1efea">'
            f'<div style="font-size:10px;color:#898781;text-transform:uppercase;'
            f'letter-spacing:.3px;margin-bottom:4px">Position in its '
            f'{esc("sector" if p.get("peer_group_is_sector") else "industry")}'
            f'</div>{_position_line(p)}</div>')


def _detail(r):
    q, v, t = r["quality"], r["valuation"], r["trend"]
    gate = _GATE_LABEL.get(r["gate"], r["gate"])
    parts = [
        # Before the verdict, not after it: the rest of this panel is a
        # judgment about a business, and it should be possible to know which
        # business without leaving the page.
        card(f'{r.get("name") or r["ticker"]}', _profile_panel(r), "🏢"),
        f'<div style="font-size:11px;color:#898781;margin-bottom:10px">'
        f'Stopped at: <strong style="color:#444441">{esc(gate)}</strong>'
        f' · LT score {r["lt_score"] if r["lt_score"] is not None else "—"}'
        f' from {int((r["lt_coverage"] or 0) * 100)}% of its inputs</div>',
        _list_block("Why", r.get("why"), "✓", "#0F6E56"),
        _list_block("What is blocking it", r.get("blockers"), "✗", "#A32D2D"),

        _entries_panel(r),
    ]
    grid = (
        '<div style="display:grid;grid-template-columns:1fr;gap:14px;'
        'margin-top:12px">'
        # Buy Zone before sizing, and the two are not redundant. Sizing
        # answers "if I take this trade today, how much and where is the
        # stop" — a swing question, priced to the cent. The bands answer the
        # one a multi-year holder actually asks: which prices are worth
        # owning this at, and what is still wrong at each. On this page the
        # second question is the primary one, so it reads first; the precise
        # plan is underneath for the day you act on it.
        f'<div>{card("Buy Zone", _buy_zone_panel(r), "🎚️")}</div>'
        # Second, and deliberately not folded into the one above: this is the
        # 3-to-20-day question, answered by an engine that reads none of the
        # company's inputs. AVGO is a long-term WAIT and a swing setup at the
        # same time, and both are true.
        f'<div>{card("Swing trade — 3 to 20 days", _swing_panel(r), "📈")}</div>'
        f'<div>{_sizing_panel(r)}</div>'
        f'<div>{card("Business quality — LQuality", _quality_panel(q), "💎")}</div>'
        f'<div>{card("Valuation", _valuation_panel(v), "⚖️")}</div>'
        f'<div>{card("Long-term trend", _trend_panel(t), "📈")}</div>'
        f'<div>{card("Targets and risk", _targets_panel(r), "🎯")}</div>'
        f'<div>{card("Moving-average levels", _levels_panel(r), "📐")}</div>'
        f'<div>{card("What would change it", _scenarios_panel(r), "🔀")}</div>'
        f'<div>{card("52-week range", _range_panel(r["pullback"].get("range_52w") or {}), "📏")}</div>'
        f'<div>{card("Support and pullback volume", _support_panel(r["confluence"], r["volume"], r["pullback"]), "🧱")}</div>'
        '</div>')
    return "".join(parts) + grid


# ─────────────────────────────────────────────────────────────────────────────
# The table — §15
# ─────────────────────────────────────────────────────────────────────────────

# The resize handle is absolutely positioned, so a header needs to be a
# positioned ancestor. That `position` is set in PIN_CSS, NOT here: an inline
# style beats the stylesheet, so `position:relative` in this string overrode
# `position:sticky` on the two pinned headers — and their measured `left`
# offset, which does nothing to a static-position sticky element, became a
# relative shift that slid TICKER and PRICE right by the width of the frozen
# group while their data cells stayed put.
_TH = ('padding:7px 8px;font-size:10px;text-transform:uppercase;'
       'letter-spacing:.06em;color:#898781;border-bottom:1px solid #e1e0d9;'
       'white-space:nowrap;cursor:pointer;user-select:none')

# Sorting and column dragging, in the page rather than the server, because
# both are view state — re-rendering 545 rows over the network to reverse a
# column would be a page load for something the browser already has.
#
# Two things make this less trivial than a normal sortable table:
#
#   1. Every result is TWO <tr>s — the summary and the collapsible reasoning
#      beneath it. Sorting has to move them as a unit, or the audit ends up
#      under someone else's ticker. That is why rows carry data-main /
#      data-detail and the sort operates on pairs.
#   2. Missing values must not sort as zero. A ticker with no 200 MA is not
#      the worst 200 MA support in the library; blanks are pinned to the
#      bottom on both directions.
#
# Order and sort survive reloads in localStorage, since a layout you have to
# rebuild after every filter click is not a layout you would use.
_RULE_JS = r"""
(function () {
  // The operator list and the value widget both depend on the field's kind,
  // so the form has to rebuild them when the field changes. Everything else
  // on this page is a link; this is the one control that cannot be.
  var meta = window.LT_FIELDS || {};
  var field = document.getElementById('lt-rule-field');
  var opSel = document.getElementById('lt-rule-op');
  var wrap = document.getElementById('lt-rule-valuewrap');
  var hint = document.getElementById('lt-rule-hint');
  if (!field || !opSel || !wrap) return;

  var OP_LABEL = { gte: 'at least', gt: 'more than', lte: 'at most',
                   lt: 'less than', eq: 'is', ne: 'is not',
                   between: 'between', within: 'within', in: 'is one of' };
  var INPUT = 'padding:7px 9px;font-size:12px;border:0.5px solid #e1e0d9;' +
              'border-radius:7px;width:110px';

  function rebuild() {
    var spec = meta[field.value];
    if (!spec) return;
    opSel.innerHTML = (spec.ops || []).map(function (o) {
      return '<option value="' + o + '">' + (OP_LABEL[o] || o) + '</option>';
    }).join('');
    hint.textContent = spec.hint || '';
    drawValue();
  }

  function drawValue() {
    var spec = meta[field.value];
    if (!spec) return;
    var op = opSel.value;
    if (spec.kind === 'bool') {
      wrap.innerHTML = '<select name="_value" style="' + INPUT + '">' +
        '<option value="true">Yes</option><option value="false">No</option></select>';
    } else if (spec.kind === 'enum') {
      wrap.innerHTML = '<select name="_value" style="' + INPUT + ';width:auto">' +
        (spec.values || []).map(function (v) {
          return '<option value="' + v + '">' + v + '</option>';
        }).join('') + '</select>';
    } else if (op === 'between') {
      wrap.innerHTML =
        '<input name="_value" type="number" step="any" placeholder="from" style="' + INPUT + ';width:78px">' +
        ' <input name="_value2" type="number" step="any" placeholder="to" style="' + INPUT + ';width:78px">';
    } else {
      wrap.innerHTML = '<input name="_value" type="number" step="any" ' +
        'placeholder="' + (spec.unit || 'value') + '" style="' + INPUT + '">';
    }
  }

  field.addEventListener('change', rebuild);
  opSel.addEventListener('change', drawValue);
  rebuild();

  // Assemble the three inputs into one `rule` param on submit, so the URL
  // carries "lquality:gte:85" rather than three loose fields the server
  // would have to recombine.
  field.form.addEventListener('submit', function (e) {
    var spec = meta[field.value];
    if (!spec) return;
    var v = wrap.querySelector('[name=_value]');
    var v2 = wrap.querySelector('[name=_value2]');
    var value = v ? v.value : '';
    if (value === '' || value === null) { e.preventDefault(); return; }
    if (opSel.value === 'between') {
      if (!v2 || v2.value === '') { e.preventDefault(); return; }
      value = value + ',' + v2.value;
    }
    var hiddenRule = document.createElement('input');
    hiddenRule.type = 'hidden';
    hiddenRule.name = 'rule';
    hiddenRule.value = field.value + ':' + opSel.value + ':' + value;
    field.form.appendChild(hiddenRule);
    // The three builder inputs are for the human, not the URL.
    field.removeAttribute('name');
    opSel.removeAttribute('name');
    if (v) v.removeAttribute('name');
    if (v2) v2.removeAttribute('name');
  });
})();
"""

TABLE_JS = r"""
// A named function rather than a bare IIFE because the Dashboard's Scan &
// Analyze panel injects this same table after the page has loaded, and has
// to bind sorting/reordering to markup that did not exist at DOMContentLoaded.
// The call below covers the Long-Term page, where the table is server-rendered.
function initLtTable() {
  var table = document.getElementById('lt-table');
  if (!table) return;
  // KEY is versioned so that changing the DEFAULT column order actually
  // reaches anyone who has ever dragged a column. A saved order is a full
  // list of keys and wins over the server's order outright, so moving Buy
  // Zone next to Price would have been invisible to exactly the users who
  // use this table most. Bumping resets a hand-made layout once, which is
  // the cheaper of the two mistakes. Widths are keyed by column and
  // survive the reset.
  var KEY = 'lt.cols.v3', SORTKEY = 'lt.sort.v1', WKEY = 'lt.colw.v1';
  try {
    localStorage.removeItem('lt.cols.v1');
    localStorage.removeItem('lt.cols.v2');
  } catch (e) {}
  var head = table.tHead.rows[0], body = table.tBodies[0];

  // ── Column widths ─────────────────────────────────────────────────────────
  // Width is applied to the inner [data-colw] span, not to the <th>/<td> box.
  // A `width` on a table cell is a suggestion the layout algorithm is free to
  // ignore — and does, whenever the content is wider — so dragging a column
  // narrower would appear to do nothing on exactly the dense columns worth
  // narrowing. A capped inline-block always holds.
  //
  // Only columns the user has actually dragged are sized. Handing every
  // column a default width would push the table wider than the nineteen
  // content-driven columns need, which is the opposite of the request.
  var colWidths = {};
  try { colWidths = JSON.parse(localStorage.getItem(WKEY) || '{}') || {}; }
  catch (e) { colWidths = {}; }

  function applyWidth(key) {
    var w = colWidths[key];
    var els = table.querySelectorAll('[data-colw="' + key + '"]');
    for (var i = 0; i < els.length; i++) {
      var s = els[i].style;
      if (w) {
        s.display = 'inline-block';
        s.width = w + 'px';
        s.maxWidth = w + 'px';
        s.overflow = 'hidden';
        s.verticalAlign = 'top';
      } else {
        // Back to the natural, content-driven column.
        s.display = s.width = s.maxWidth = s.overflow = s.verticalAlign = '';
      }
    }
  }

  function applyAllWidths() {
    for (var key in colWidths) {
      if (Object.prototype.hasOwnProperty.call(colWidths, key)) applyWidth(key);
    }
  }

  function saveWidths() {
    try { localStorage.setItem(WKEY, JSON.stringify(colWidths)); } catch (e) {}
  }

  var resizeKey = null, resizeStartX = 0, resizeStartW = 0;

  function onResizeMove(e) {
    if (!resizeKey) return;
    // 44px floor: narrower than this and the header label has no room to
    // show even one character, so the column can't be found to widen again.
    colWidths[resizeKey] = Math.max(44, resizeStartW + (e.clientX - resizeStartX));
    applyWidth(resizeKey);
    layoutPins();   // a pinned column changing width moves the ones after it
  }

  function onResizeEnd() {
    if (resizeKey) { saveWidths(); layoutPins(); }
    resizeKey = null;
    document.body.style.userSelect = '';
    var active = table.querySelectorAll('.col-resizer.active');
    for (var i = 0; i < active.length; i++) active[i].classList.remove('active');
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', onResizeEnd);
  }

  function startResize(e, th, handle) {
    var key = th.dataset.col;
    e.preventDefault();
    e.stopPropagation();
    resizeKey = key;
    resizeStartX = e.clientX;
    // Measured, not read back from colWidths: the first drag of an untouched
    // column has to start from the width it currently occupies on screen, or
    // the column jumps to some default before it starts following the mouse.
    resizeStartW = colWidths[key] ||
      Math.round(th.getBoundingClientRect().width);
    handle.classList.add('active');
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onResizeMove);
    document.addEventListener('mouseup', onResizeEnd);
  }

  function colKeys() {
    return Array.prototype.map.call(head.cells, function (th) {
      return th.dataset.col;
    });
  }

  // Summary row + its detail row, kept together through every reorder.
  function pairs() {
    var out = [], cur = null;
    Array.prototype.forEach.call(body.rows, function (tr) {
      if (tr.dataset.main) { cur = { main: tr, rest: [] }; out.push(cur); }
      else if (cur) { cur.rest.push(tr); }
    });
    return out;
  }

  function cellOf(tr, key) {
    return tr.querySelector('td[data-col="' + key + '"]');
  }

  // Sort keys, highest priority first: [{key, dir}, ...]. Multi-column
  // because the useful questions here are compound — "best businesses, and
  // among those the ones I can act on" is LQuality then Action, and neither
  // column answers it alone.
  var sortKeys = [];

  function compare(a, b, key, dir) {
    var th = head.querySelector('th[data-col="' + key + '"]');
    var numeric = th && th.dataset.type === 'num';
    var ta = cellOf(a.main, key), tb2 = cellOf(b.main, key);
    var ra = ta ? ta.dataset.sort : '', rb = tb2 ? tb2.dataset.sort : '';
    var blankA = (ra === undefined || ra === '');
    var blankB = (rb === undefined || rb === '');
    var va = numeric ? parseFloat(ra) : String(ra || '').toUpperCase();
    var vb = numeric ? parseFloat(rb) : String(rb || '').toUpperCase();
    if (numeric) {
      if (isNaN(va)) blankA = true;
      if (isNaN(vb)) blankB = true;
    }
    // Blanks last in BOTH directions: "no value" is not an extreme value.
    if (blankA !== blankB) return blankA ? 1 : -1;
    if (blankA) return 0;
    if (va < vb) return dir === 'asc' ? -1 : 1;
    if (va > vb) return dir === 'asc' ? 1 : -1;
    return 0;
  }

  function applySort() {
    if (!sortKeys.length) return;
    var groups = pairs();
    groups.forEach(function (g, i) { g._i = i; });   // stable tiebreak
    groups.sort(function (a, b) {
      for (var i = 0; i < sortKeys.length; i++) {
        var r = compare(a, b, sortKeys[i].key, sortKeys[i].dir);
        if (r !== 0) return r;
      }
      return a._i - b._i;
    });
    var frag = document.createDocumentFragment();
    groups.forEach(function (g) {
      frag.appendChild(g.main);
      g.rest.forEach(function (r) { frag.appendChild(r); });
    });
    body.appendChild(frag);
    markSort();
  }

  function markSort() {
    var multi = sortKeys.length > 1;
    Array.prototype.forEach.call(head.cells, function (th) {
      var arrow = th.querySelector('.lt-arrow');
      if (!arrow) return;
      var at = -1;
      for (var i = 0; i < sortKeys.length; i++) {
        if (sortKeys[i].key === th.dataset.col) { at = i; break; }
      }
      if (at === -1) {
        arrow.textContent = '';
        th.style.color = '#898781';
        th.dataset.dir = '';
      } else {
        // The priority number only appears once it means something.
        arrow.textContent = (sortKeys[at].dir === 'asc' ? ' ▲' : ' ▼')
          + (multi ? String(at + 1) : '');
        th.style.color = '#0b0b0b';
        th.dataset.dir = sortKeys[at].dir;
      }
    });
    var note = document.getElementById('lt-sort-note');
    if (note) {
      note.style.display = multi ? '' : 'none';
      if (multi) {
        note.textContent = 'Sorted by ' + sortKeys.map(function (k, i) {
          var th = head.querySelector('th[data-col="' + k.key + '"]');
          var label = th ? th.textContent.replace(/[▲▼0-9]/g, '').trim() : k.key;
          return (i + 1) + '. ' + label + ' ' + (k.dir === 'asc' ? 'asc' : 'desc');
        }).join(', ') + ' — shift-click a header to add another, click to reset.';
      }
    }
  }

  // The pinned columns never move and nothing may be placed left of them:
  // they are `position:sticky`, so away from the left edge they would float
  // on top of whichever column they now overlap. Counts the LEADING run, so
  // a stray .lt-pin further right (which should not happen) is ignored
  // rather than treated as part of the frozen group.
  function pinned() {
    var n = 0;
    while (head.cells[n] && head.cells[n].classList.contains('lt-pin')) n++;
    return n;
  }

  // Each pinned column starts where the previous one ends. Measured rather
  // than hardcoded: the ticker column's width follows the longest company
  // name on screen, which changes with every filter.
  function layoutPins() {
    var n = pinned(), offset = 0;
    for (var i = 0; i < n; i++) {
      var key = head.cells[i].dataset.col;
      var cells = table.querySelectorAll('[data-col="' + key + '"]');
      for (var j = 0; j < cells.length; j++) {
        cells[j].style.left = offset + 'px';
        cells[j].classList.toggle('lt-pin-last', i === n - 1);
      }
      offset += head.cells[i].getBoundingClientRect().width;
    }
  }

  function moveColumn(from, to) {
    var floor = pinned();
    if (from < floor || to < floor) return;
    if (from === to) return;
    var rows = [head].concat(Array.prototype.filter.call(body.rows, function (r) {
      return r.dataset.main;                 // detail rows are one colspan cell
    }));
    rows.forEach(function (row) {
      var cell = row.cells[from];
      var ref = row.cells[to];
      if (!cell || !ref) return;
      row.insertBefore(cell, from < to ? ref.nextSibling : ref);
    });
    try { localStorage.setItem(KEY, JSON.stringify(colKeys())); } catch (e) {}
  }

  // Restore a saved order by moving each column into place, left to right.
  // Orders saved before the column was pinned can list it anywhere, so the
  // pinned key is dropped from the saved order rather than honoured — the
  // alternative is restoring a layout the current rules forbid.
  try {
    var saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (Array.isArray(saved)) {
      var floor = pinned();
      saved.filter(function (key) {
        return !(floor && key === head.cells[0].dataset.col);
      }).forEach(function (key, i) {
        var now = colKeys().indexOf(key);
        if (now > -1 && now !== i + floor) moveColumn(now, i + floor);
      });
    }
  } catch (e) {}

  // Before layoutPins(): a restored width changes how wide a pinned column
  // is, and the pin offsets are measured from it.
  applyAllWidths();

  // After the restore, so the offsets are measured against the layout the
  // user will actually see. Column widths are content-driven, so anything
  // that reflows the table can move the seam — a window resize, or webfonts
  // arriving after first paint.
  layoutPins();
  window.addEventListener('resize', layoutPins);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(layoutPins).catch(function () {});
  }

  var dragFrom = null;
  Array.prototype.forEach.call(head.cells, function (th) {
    // Sorting still works on the pinned column; only reordering is off.
    th.draggable = !th.classList.contains('lt-pin');

    // Resizing is allowed on every column INCLUDING the pinned ones — the
    // reason they can't move (they're position:sticky and must stay leftmost)
    // says nothing about how wide they should be, and the ticker column is
    // the one most worth narrowing once you know your own tickers.
    var handle = th.querySelector('.col-resizer');
    if (handle) {
      handle.addEventListener('mousedown', function (e) {
        startResize(e, th, handle);
      });
      // The handle sits inside the header, which sorts on click. Without
      // this, every resize would also re-sort the table underneath it.
      handle.addEventListener('click', function (e) { e.stopPropagation(); });
      handle.addEventListener('dblclick', function (e) {
        e.preventDefault();
        e.stopPropagation();
        delete colWidths[th.dataset.col];
        applyWidth(th.dataset.col);
        saveWidths();
        layoutPins();
      });
    }
    th.addEventListener('dragstart', function (e) {
      dragFrom = Array.prototype.indexOf.call(head.cells, th);
      th.style.opacity = '.4';
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', th.dataset.col); } catch (err) {}
    });
    th.addEventListener('dragend', function () {
      th.style.opacity = '';
      Array.prototype.forEach.call(head.cells, function (o) {
        o.style.borderLeft = ''; o.style.borderRight = '';
      });
    });
    th.addEventListener('dragover', function (e) {
      // The pinned column shows no drop indicator: moveColumn would refuse
      // the drop anyway, and offering a target that silently does nothing is
      // worse than offering none.
      if (th.classList.contains('lt-pin')) { e.dataTransfer.dropEffect = 'none'; return; }
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      var over = Array.prototype.indexOf.call(head.cells, th);
      th.style.borderLeft = over < dragFrom ? '2px solid #185FA5' : '';
      th.style.borderRight = over > dragFrom ? '2px solid #185FA5' : '';
    });
    th.addEventListener('dragleave', function () {
      th.style.borderLeft = ''; th.style.borderRight = '';
    });
    th.addEventListener('drop', function (e) {
      e.preventDefault();
      var to = Array.prototype.indexOf.call(head.cells, th);
      if (dragFrom !== null) moveColumn(dragFrom, to);
      dragFrom = null;
    });
    th.addEventListener('click', function (e) {
      var key = th.dataset.col;
      // Numbers open on descending (best first); text opens ascending.
      var opening = th.dataset.type === 'num' ? 'desc' : 'asc';
      var at = -1;
      for (var i = 0; i < sortKeys.length; i++) {
        if (sortKeys[i].key === key) { at = i; break; }
      }
      if (e.shiftKey) {
        // Add as a further tiebreak, or flip it if already in the list.
        if (at === -1) sortKeys.push({ key: key, dir: opening });
        else sortKeys[at].dir = sortKeys[at].dir === 'asc' ? 'desc' : 'asc';
      } else if (at === 0 && sortKeys.length === 1) {
        sortKeys = [{ key: key, dir: sortKeys[0].dir === 'asc' ? 'desc' : 'asc' }];
      } else {
        sortKeys = [{ key: key, dir: opening }];
      }
      applySort();
    });
  });

  // Column ORDER persists; the active sort deliberately does not.
  //
  // Restoring a saved sort on load looks helpful and is not: sorting
  // dissolves the action grouping, so a sort remembered from a previous
  // visit silently destroys the default view on every subsequent page load,
  // and the grouping never comes back. Order is a layout preference worth
  // keeping. A sort is a question you asked once.
  try { localStorage.removeItem(SORTKEY); } catch (e) {}

  // One reset for the whole layout: order and widths are the same preference
  // from the user's side ("put the table back"), and a control that restored
  // half of it would need a second one next to it explaining the difference.
  var reset = document.getElementById('lt-reset-cols');
  if (reset) {
    reset.addEventListener('click', function (e) {
      e.preventDefault();
      try { localStorage.removeItem(KEY); localStorage.removeItem(WKEY); }
      catch (err) {}
      location.reload();
    });
  }
}
initLtTable();
"""
_TD = 'padding:8px;border-bottom:0.5px solid #f1efea;font-size:12px;vertical-align:top'


def _buyzone_cell(pullback):
    """The price to work an order at — and whether anyone has ever defended it.

    A tested volume shelf and a moving average that merely happens to be the
    next line down are rendered differently on purpose. Roughly a third of
    the library has no tested level, and showing a computed average in the
    same voice would turn "support is probably around here" into "support is
    here". Derived levels are muted, italic, and say which average they came
    from.
    """
    bz = (pullback or {}).get("buy_zone") or {}
    price, dist = bz.get("price"), bz.get("distance_pct")
    if price is None:
        return '<span style="color:#898781">—</span>', None
    touches = bz.get("touches")
    if bz.get("actual_support"):
        # Green and bold: the market has defended this price before.
        style = "color:#0F6E56;font-weight:700"
        sub = f"{touches:.0f} touches" if touches else "tested"
    elif bz.get("source") == "volume_shelf":
        style = "color:#8a6d1a;font-weight:600"
        sub = (f"{touches:.0f} touches · unconfirmed" if touches
               else "unconfirmed")
    else:
        # Grey and italic: arithmetic on recent closes, not a level anyone
        # has defended. The label names which average it came from.
        style = "color:#898781;font-style:italic"
        sub = f'{bz.get("label") or "MA"} · derived'
    dist_txt = f' <span style="color:#898781">{dist:+.1f}%</span>' \
        if dist is not None else ""
    return (f'<span style="{style};white-space:nowrap" '
            f'title="{esc(bz.get("note") or "")}">${price:,.2f}</span>{dist_txt}'
            f'<div style="font-size:10px;color:#898781;white-space:nowrap">'
            f'{esc(sub)}</div>', dist)


def _support_confluence_cell(conf, cluster=None):
    """Support as two readings that are allowed to disagree.

    The cluster says how tightly the levels are wound around price; the
    count says how many are actually holding it up. Meta has five levels
    inside a 1.9% band and only two of them beneath the price — a strong
    coil at a weak support reading, and both are true. Showing only the
    second describes a decision point as an absence.
    """
    n = conf.get("agreeing", len(conf.get("hits", [])))
    possible = conf.get("possible", 6)
    score = conf.get("score", 0)
    cluster = cluster or {}
    count = cluster.get("count") or 0
    if count >= 2:
        colour = "#0F6E56" if count >= 3 else "#8a6d1a"
        names = ", ".join(l["name"] for l in cluster.get("levels", []))
        head = (f'<span style="color:{colour};font-weight:700" '
                f'title="{esc(names)}">{esc(cluster.get("label", ""))}</span>')
        sub = (f'{count} within {cluster.get("within_pct", 2):.0f}%'
               + (f', span {cluster["span_pct"]:.1f}%'
                  if cluster.get("span_pct") is not None else "")
               + f' · {n} of {possible} holding')
    else:
        colour = "#0b0b0b" if n >= 3 else "#8a6d1a" if n >= 2 else "#A32D2D"
        head = (f'<span style="color:{colour};font-weight:700">'
                f'{n} of {possible}</span>')
        sub = f'{esc(conf.get("label", ""))} · {score}/100'
    return (f'{head}<div style="font-size:10px;color:#898781;'
            f'white-space:nowrap">{sub}</div>',
            # Sorted on levels actually holding, cluster size as tiebreak.
            n * 1000 + count * 10 + min(score, 9))


# The two proximity states worth a marker in the cell. Wording matters here:
# "AT ZONE" is a fact about price, not an instruction to buy — whether the
# zone is worth buying is the Action column's job, and names reach their zone
# while the engine still says AVOID.
_ZONE_MARK = {
    "IN_ZONE": ("#0F6E56", "#E1F5EE", "AT ZONE"),
    "APPROACHING": ("#8a6d1a", "#FAEEDA", "NEAR"),
}


def _zone_flag(r):
    """The proximity reading for one row, or None. Cached on the row.

    Computed once per row per render and stashed, because three separate
    things ask for it — the cell marker, the alert bar's list, and the
    ?near= filter — and recomputing it in each would let them disagree the
    moment the threshold moved.
    """
    if "_zone_prox" not in r:
        r["_zone_prox"] = BZ.zone_proximity(r)
    return r["_zone_prox"]


# LQuality at or above this, in a real drawdown, sitting on a tracked
# level — the shape the DCA note fires on. Not a recommendation and not a
# gate: the Action column's verdict is untouched, and this says only that
# the price has reached a rung on a business the engine rates highly.
DCA_QUALITY = 90
DCA_CORRECTION_PCT = -20.0


def _dca_read(r):
    """(zone, note) when a quality name is sitting on an accumulation rung.

    Deliberately additive. The engine's own Action may still be WAIT or
    OWN/WAIT FOR TREND and that verdict is not overridden — this reports a
    separate fact (price has reached rung N of the ladder on a business
    scoring DCA_QUALITY or better in a DCA_CORRECTION_PCT drawdown) and
    leaves the decision where it was.
    """
    bz = r.get("buy_zones") or {}
    hit = bz.get("zone_hit")
    if not hit:
        return None, ""
    q = (r.get("quality") or {}).get("score")
    off = r.get("dist_52w_high")
    if q is None or q < DCA_QUALITY:
        return hit, ""
    if off is None or off > DCA_CORRECTION_PCT:
        return hit, ""
    return hit, (f"Zone {hit['zone']} · {off:.0f}% off the high on a "
                 f"{q:.0f}-quality business — a DCA tranche fits here")


def _dca_cell(r):
    """The DCA column: which rung, on what kind of level, and whether the
    drawdown makes it a tranche rather than a routine pullback."""
    hit, note = _dca_read(r)
    if not hit:
        return ('<span style="color:#b5b3ad">—</span>', None)
    src = {"volume_shelf": "tested shelf", "moving_average": "moving average",
           "structure": "structural level", "fundamental": "value rung",
           "level": "prior support"}.get(hit.get("source"), "level")
    q = (r.get("quality") or {}).get("score")
    off = r.get("dist_52w_high")
    if note:
        colour, weight = "#0F6E56", "700"
        tail = f'{off:.0f}% off high · Q{q:.0f} · DCA'
    else:
        colour, weight = "#898781", "500"
        bits = [src]
        if off is not None:
            bits.append(f'{off:.0f}% off high')
        tail = " · ".join(bits)
    return (f'<span style="color:{colour};font-weight:{weight};'
            f'white-space:nowrap" title="{esc(note or hit.get("label") or "")}">'
            f'Zone {hit["zone"]}{" · in" if hit.get("inside") else ""}</span>'
            + _sub(tail),
            # Sorted so the DCA-eligible rungs lead, then shallower ones.
            (100 if note else 0) + (4 - hit["zone"]))


def _buy_zone_cell(r):
    """The buy zone, for every row.

    A qualifying investment zone is green and bold — valuation, return,
    quality and support all agree at that price. Everything else shows the
    nearest technical band the price would reach on a pullback, in grey and
    labelled with the level it comes from, because a support band is not a
    buy zone and the two must not look alike.

    A name whose price has arrived at, or come within BZ.NEAR_ZONE_PCT of,
    its zone carries a marker. That marker is the whole reason this column
    now sits beside Price: the alert bar above the table names those tickers,
    and this is where the claim can be checked against the two numbers it is
    made of.
    """
    zone = ((r.get("buy_zones") or {}).get("display_zone")) or {}
    if not zone.get("low"):
        return '<span style="color:#898781">—</span>', None
    qualifies = zone.get("kind") == "investment"
    # Overhead supply reads as neither a buy zone nor a qualifying one: the
    # price has already fallen through it, so it describes where the stock
    # came from rather than where it is going.
    overhead = bool(zone.get("above_spot"))
    colour = "#898781" if overhead else ("#0F6E56" if qualifies else "#898781")
    weight = "700" if (qualifies and not overhead) else "500"
    dist = zone.get("distance_pct")
    sub = esc(zone.get("label") or "")
    if overhead:
        sub += " · price is below this band"
    elif zone.get("kind") == "ladder":
        src = {"volume_shelf": "tested shelf", "fundamental": "value rung",
               "level": "prior support"}.get(zone.get("source"), "level")
        sub += f" · {src} — every moving average is overhead"
    elif not qualifies:
        sub += " · technical"
    if dist is not None:
        sub += f' · {dist:+.1f}%'

    # The rungs below today's price, as a second line. Zone 1 is where
    # price arrives next; 2 and 3 are where you would still be adding.
    # Shown here rather than only in the reasoning panel because "the next
    # three places I would buy" is the question the column is asked, and a
    # single band answers it only when a band happens to be underfoot.
    ladder = ((r.get("buy_zones") or {}).get("ladder")) or []
    note = (r.get("buy_zones") or {}).get("ladder_note")
    rungs = ""
    if len(ladder) > 1:
        bits = []
        for g in ladder[:3]:
            price_txt = (f'${g["low"]:,.0f}–{g["high"]:,.0f}'
                         if g["high"] != g["low"] else f'${g["price"]:,.2f}')
            bits.append(f'Z{g["zone"]} {price_txt} ({g["distance_pct"]:+.1f}%)')
        rungs = (f'<div style="font-size:10px;color:#898781;'
                 f'white-space:nowrap" title="{esc(note or "Accumulation "
                 "rungs below today’s price, nearest first")}">'
                 f'{esc(" · ".join(bits))}'
                 # A short ladder and a full one look alike, and the reader
                 # cannot tell "this is all the structure there is" from
                 # "something failed" without being told which.
                 + (f' <span style="color:#b5b3ad">·  only {len(ladder)}</span>'
                    if note else "")
                 + f'</div>')

    prox = _zone_flag(r)
    mark = ""
    if prox and prox["near"]:
        fg, bg, label = _ZONE_MARK[prox["state"]]
        gap = ("price is inside the zone now" if prox["state"] == "IN_ZONE"
               else f'{abs(prox["gap_pct"]):.1f}% above the top of the zone')
        mark = (f'<span title="{esc(gap)}" style="background:{bg};color:{fg};'
                f'font-size:9px;font-weight:700;letter-spacing:.04em;'
                f'padding:1px 5px;border-radius:4px;margin-right:5px;'
                f'vertical-align:1px">{label}</span>')

    return (f'{mark}<span style="color:{colour};font-weight:{weight};'
            f'white-space:nowrap" title="{esc(zone.get("basis") or "")}">'
            f'${zone["low"]:,.0f} – ${zone["high"]:,.0f}</span>'
            f'<div style="font-size:10px;color:#898781;white-space:nowrap">'
            f'{sub}</div>{rungs}',
            # Names at or near their zone sort above everything, because that
            # is the event this column now exists to surface. Within that,
            # qualifying zones above technical bands, and within each the
            # nearest to today's price first.
            (2000 if (prox and prox["near"]) else 0)
            + (1000 if qualifies else 0)
            - abs(dist if dist is not None else 999))


def _swing_trade_cell(r):
    """The swing engine's verdict in one cell: score, state, setup.

    Three facts rather than one, because the score alone would rank a name
    with no pattern above one that is a trigger away from being taken.
    Sorted on score, but only for names with a live setup — a 50 with no
    setup should not outrank a 48 that has one.
    """
    sw = r.get("swing") or {}
    score = sw.get("score")
    if score is None:
        return '<span style="color:#898781">—</span>', None
    colour, _bg, icon = _SWING_STATE.get(sw.get("state"),
                                         ("#898781", "", "⚪"))
    setup = sw.get("setup_label") or ""
    sub = f'{icon} {sw.get("state", "")}'
    if setup and setup != "No setup":
        sub += f' · {setup}'
    return (f'<span style="color:{colour};font-weight:700" '
            f'title="{esc(sw.get("state_why") or "")}">{score}</span>'
            f'<div style="font-size:10px;color:#898781;white-space:nowrap">'
            f'{esc(sub)}</div>',
            # Names with no tradeable setup sort below every name that has
            # one, whatever their score.
            score + (1000 if sw.get("eligible") else 0))


def _swing_cell(r):
    """The PURE technical score — price and volume only.

    Shares no input with the quality or valuation gates, which is what makes
    it a genuine second opinion rather than a restatement. The scan's own
    Swing_Score rides underneath as context; it feeds 20% of Entry and can
    stand in for a reversal candle, but it mixes chart category and grade
    with the rest, so it is not the headline here.
    """
    tech = r.get("technical") or {}
    score = tech.get("score")
    entry = r.get("entry") or {}
    swing = entry.get("swing")
    sub = ("scan swing —" if swing is None else f"scan swing {swing:.0f}")
    rr = (r.get("targets") or {}).get("rr_t2") or (r.get("targets") or {}).get("rr_t1")
    if rr is not None:
        sub += f" · {rr:.1f}:1"
    if score is None:
        return ('<span style="color:#898781">—</span>', None)
    colour = ("#0F6E56" if score >= 65 else "#8a6d1a" if score >= 50
              else "#A32D2D")
    return (f'<span style="color:{colour};font-weight:700" '
            f'title="{esc(tech.get("label", ""))}">{score}</span>'
            f'<div style="font-size:10px;color:#898781;white-space:nowrap">'
            f'{esc(sub)}</div>', score)


# ─────────────────────────────────────────────────────────────────────────────
# POSITION SIZING CELLS
# ─────────────────────────────────────────────────────────────────────────────
# Four dense columns answering §14's questions without opening the panel:
# where do I enter, where is the stop, how many shares, how much can I lose,
# what is the reward. Each stacks two related figures rather than spending a
# column on each — the alternative was twelve columns and a horizontal
# scrollbar past the Action column the table exists to show.

_SIZE_STATUS_COLOR = {"NORMAL": "#0F6E56", "HIGH_ALLOCATION": "#8a6d1a",
                      "OVERSIZED": "#A32D2D", "NO_STOP": "#A32D2D",
                      "INVALID_SETUP": "#A32D2D", "NOT_ACTIONABLE": "#898781"}

_SIZE_SORT = {"NORMAL": 4, "HIGH_ALLOCATION": 3, "OVERSIZED": 2,
              "INVALID_SETUP": 1, "NO_STOP": 1, "NOT_ACTIONABLE": 0}


def _dash(title=""):
    return (f'<span style="color:#898781" title="{esc(title)}">—</span>', None)


def _sub(text, colour="#898781"):
    return (f'<div style="font-size:10px;color:{colour};white-space:nowrap">'
            f'{esc(text)}</div>')


def _entry_stop_cell(r):
    """Entry over stop — the two prices the whole plan hangs on."""
    plan = r.get("sizing_plan") or {}
    entry, stop = plan.get("entry") or {}, plan.get("stop") or {}
    if not entry.get("price"):
        return _dash(plan.get("reason") or "no entry")
    # A plan the engine has not cleared is greyed and italic — the same
    # treatment derived support levels get, and for the same reason. The
    # prices are real and worth knowing; they are what you are WAITING for,
    # not an instruction to act.
    pending = plan.get("pending")
    head_style = ("color:#898781;font-style:italic" if pending
                  else "color:#0b0b0b")
    at_market = entry.get("at_market")
    # A market entry and a resting order at a level are different
    # instructions; the label is what tells them apart at a glance.
    head = (f'<strong style="{head_style}">${entry["price"]:,.2f}</strong>'
            f'<span style="font-size:9px;color:#898781"> '
            f'{esc("mkt" if at_market else entry.get("type", "").replace(" Entry", "").replace(" Pullback", ""))}'
            f'</span>')
    if not stop.get("price"):
        return (head + _sub("no stop", "#A32D2D"),
                entry["price"])
    sizing = plan.get("sizing") or {}
    pct = sizing.get("stop_distance_pct")
    return (head + _sub(f'stop ${stop["price"]:,.2f}'
                        + (f' · {pct:.1f}%' if pct is not None else ""),
                        "#898781" if pending else "#52514e"),
            entry["price"])


def _position_cell(r):
    """Shares, then what they cost — with the risk status as the colour.

    Sorted by status rather than by share count: "is this position sane" is
    the question the column is here to answer, and 500 shares of a $4 stock
    ranking above 50 of a $400 one would answer a different one.
    """
    plan = r.get("sizing_plan") or {}
    status = plan.get("status") or "NOT_ACTIONABLE"
    sizing = plan.get("sizing") or {}
    if not plan.get("ok") or not sizing.get("shares"):
        icon = {"NO_STOP": "🔴", "INVALID_SETUP": "🔴"}.get(status, "⚪")
        label = {"NO_STOP": "no stop", "INVALID_SETUP": "invalid",
                 "NOT_ACTIONABLE": "N/A"}.get(status, "N/A")
        return (f'<span style="color:#898781;font-size:11px" '
                f'title="{esc(plan.get("reason") or "")}">{icon} {label}</span>',
                _SIZE_SORT.get(status, 0))
    # A position priced through a broken trend must not look like a clean
    # one in the table. The panel carries the full argument, but the reader
    # scanning the column has to see that this row is different without
    # opening it — otherwise the warning only reaches people who already
    # suspected they needed it.
    if plan.get("quality_override"):
        return (f'<strong style="color:#8a6d1a">⚠ {sizing["shares"]:,}</strong>'
                f'<span style="font-size:9px;color:#898781"> sh</span>'
                + _sub(f'${sizing["position_value"]:,.0f} · quality, no trend',
                       "#8a6d1a"),
                # Ranked below every ordinary position: sorting by Position
                # should not put a broken-trend name above a clean one.
                sizing["position_value"])
    colour = _SIZE_STATUS_COLOR.get(status, "#898781")
    return (f'<strong style="color:{colour}">{sizing["shares"]:,}</strong>'
            f'<span style="font-size:9px;color:#898781"> sh</span>'
            + _sub(f'${sizing["position_value"]:,.0f} · '
                   f'{sizing["allocation_pct"]:.1f}%'),
            _SIZE_SORT.get(status, 0) * 1_000_000 + sizing["position_value"])


def _risk_cell(r):
    """Dollars at risk, and that as a share of the account."""
    plan = r.get("sizing_plan") or {}
    sizing = plan.get("sizing") or {}
    if not plan.get("ok") or not sizing:
        return _dash(plan.get("reason") or "")
    status = plan.get("status")
    warn = status in ("OVERSIZED", "HIGH_ALLOCATION")
    return (f'<strong>${sizing["actual_risk"]:,.0f}</strong>'
            + _sub(f'{sizing["actual_risk_pct"]:.1f}% of capital'
                   + (" · capped" if status == "OVERSIZED" else ""),
                   "#8a6d1a" if warn else "#898781"),
            sizing["actual_risk"])


def _rr_cell(r):
    """R:R to the first target, with the sizing grade beside it.

    The grade is the QUALITY of the position — ratio, whether the allocation
    cap had to rescue it, whether anyone has defended the stop — and is
    deliberately not the company's LQuality. Seeing an Elite business carry a
    D position is the point.
    """
    plan = r.get("sizing_plan") or {}
    target = plan.get("target") or {}
    rr = target.get("rr")
    if rr is None:
        return _dash("no target above the entry")
    # "≤" marks a ratio that is the best the chart offers rather than the
    # first level worth trading to — without it, 1.7R on a name where nothing
    # clears 2R reads as a chosen target instead of a ceiling.
    capped = target.get("reached_min_rr") is False
    mark = "≤" if capped else ""
    tip = (f'best available — no tracked level reaches '
           f'{PS.TARGET_MIN_RR:g}R' if capped else
           f'first level clearing {PS.TARGET_MIN_RR:g}R'
           + (f", past {target['skipped']} nearer level(s)"
              if target.get("skipped") else ""))
    # A ratio on a plan the engine has not cleared is still a fact about the
    # chart, so it is shown — muted, because the trade is not on offer.
    if plan.get("pending"):
        return (f'<span style="color:#898781;font-style:italic" '
                f'title="{esc(tip)}">{mark}{rr:.1f}R</span>'
                + _sub(f'T ${target["price"]:,.2f}'), rr)
    colour = ("#0F6E56" if rr >= 2.0 else "#8a6d1a" if rr >= 1.5 else "#A32D2D")
    grade = plan.get("grade") or "—"
    return (f'<strong style="color:{colour}" title="{esc(tip)}">'
            f'{mark}{rr:.1f}R</strong>'
            f'<span style="font-size:9px;color:#898781"> {esc(grade)}</span>'
            + _sub(f'T ${target["price"]:,.2f}'),
            rr)


def _status_cell(view, style_map):
    """A status badge with its score underneath — the company verdict and
    the timing verdict shown as two separate answers."""
    status = view.get("status")
    if not status:
        return '<span style="color:#898781">—</span>', None
    rank = {"CORE": 4, "OWN": 3, "WATCHLIST": 2, "REJECT": 1}.get(status, 0)
    score = view.get("score")
    return (f'{badge(status.title(), style_map.get(status, "muted"), "small")}'
            f'<div style="font-size:10px;color:#898781" '
            f'title="{esc(view.get("why") or "")}">'
            f'{score if score is not None else "—"}/100</div>',
            rank * 1000 + (score or 0))


def _fair_value_line(v):
    """The modelled value, labelled as a reference rather than a target.

    The peer method prices a company against its sector; it does not say
    what the business is worth, and a fair value shown bare invites being
    read as a buy price. Meta's $434.93 is "where the sector multiple puts
    it", which is a reason to wait and not a limit order.
    """
    fair = v.get("fair_value")
    conf = f'{v.get("confidence_icon", "")} {(v.get("confidence") or "").title()}'
    if fair is None:
        implied = v.get("implied_growth_pct")
        extra = (f'needs {implied:.0f}% growth' if implied is not None else "")
        return (f'<div style="font-size:10px;color:#898781" '
                f'title="{esc(v.get("confidence_note") or "")}">'
                f'{esc(conf.strip())}{" · " + extra if extra else ""}</div>')
    upside = v.get("upside_pct")
    return (f'<div style="font-size:10px;color:#898781" '
            f'title="Reference value, not a price target — '
            f'{esc(v.get("confidence_note") or "")}">'
            f'ref ${fair:,.2f}'
            + (f' ({upside:+.0f}%)' if upside is not None else "")
            + f' · {esc(conf.strip())}</div>')


def _resistance_cell(pullback):
    """The first price overhead, and whether the market has actually turned
    down from it.

    Same tested/derived split as the support side, and the 52-week high rung
    matters: a stock in a clean uptrend has every moving average below it, so
    without that fallback this column would be blank for precisely the names
    worth owning. 311 of 545 rows have a tested R1, 100 fall back to a moving
    average and 134 to the 52-week high.
    """
    rz = (pullback or {}).get("resistance") or {}
    price, dist = rz.get("price"), rz.get("distance_pct")
    if price is None:
        return '<span style="color:#898781">—</span>', None
    touches = rz.get("touches")
    if rz.get("actual_resistance"):
        style = "color:#A32D2D;font-weight:700"
        sub = f"{touches:.0f} touches" if touches else "tested"
    else:
        style = "color:#898781;font-style:italic"
        sub = f'{rz.get("label") or "level"} · derived'
    dist_txt = (f' <span style="color:#898781">{dist:+.1f}%</span>'
                if dist is not None else "")
    return (f'<span style="{style};white-space:nowrap" '
            f'title="{esc(rz.get("note") or "")}">${price:,.2f}</span>{dist_txt}'
            f'<div style="font-size:10px;color:#898781;white-space:nowrap">'
            f'{esc(sub)}</div>', dist)


# (key, header, alignment, sort type). Order here is the DEFAULT order; the
# user can drag columns and the choice persists in localStorage.
#
# The four moving-average distance columns moved into the reasoning panel.
# They are reference prices rather than judgments — useful once a name is
# worth looking at, and four columns of noise before that. What replaced
# them is the pair the page exists to separate: the company verdict and the
# timing verdict, which routinely disagree.
_COLUMNS = (
    ("ticker", "Ticker", "left", "text"),
    ("price", "Price", "right", "num"),
    # Immediately after price. The zone is a statement ABOUT the price —
    # "$328–334" means nothing until you know the stock is at $345.90 — and
    # with the zone eleven columns to the right the two halves of that
    # sentence could not be on screen at once. It is also what the alert bar
    # above the table points at, so the thing the page just told you to look
    # at is the next thing you can see. Left unpinned deliberately: a third
    # frozen column would cost roughly a sixth of the viewport permanently,
    # and unlike ticker and price this one is draggable away by anyone who
    # would rather have it elsewhere.
    #
    # Present for EVERY name, not only the ones with a qualifying zone: a
    # blank cell made "no price qualifies today" and "no support below this"
    # look like the same finding.
    ("buy_zone", "Buy Zone", "right", "num"),
    # Which accumulation rung price has reached, and whether the drawdown
    # behind it makes this a tranche. Additive: the Action column keeps its
    # own verdict, and this reports a fact beside it.
    ("dca", "DCA", "left", "num"),
    ("lquality", "LQuality", "left", "num"),
    ("valuation", "Valuation", "left", "num"),
    # Beside Valuation because they answer adjacent questions and disagree
    # often enough that seeing one without the other is misleading:
    # Valuation is price against a modelled value, this is price against a
    # quality-scaled RETURN hurdle. Hoisted out of the detail drawer because
    # the rung price — "wait for $368.93" — was the number people were
    # opening the drawer to find.
    ("earns", "Earns", "left", "num"),
    ("trend", "Trend", "center", "num"),
    ("pullback", "Pullback", "left", "num"),
    ("support", "Support", "left", "num"),
    ("buyzone", "S1 \u00b7 Support", "right", "num"),
    ("resistance", "R1 \u00b7 Resistance", "right", "num"),
    ("rs", "RS", "center", "num"),
    ("market", "Market", "center", "text"),
    ("swing", "Technical", "left", "num"),
    # The swing ENGINE's verdict. Distinct from "Technical" above, which is
    # the pure price/volume score the long-term engine uses as a second
    # opinion — same word, different question, so both are labelled for what
    # they answer rather than for what they are computed from.
    ("swing_trade", "Swing", "left", "num"),
    ("investment", "Investment", "left", "num"),
    ("entry_score", "Entry", "left", "num"),
    # Sizing sits immediately before Action: the verdict and what it would
    # cost you are one thought, and putting the account impact after the
    # decision is how a 40%-of-capital position gets taken without noticing.
    # "LT" because a stock now carries three different entries — the
    # long-term buy zone, the swing trigger and the day entry — and an
    # unqualified "Entry" column made the first read as all three.
    ("entry_stop", "LT Entry · Stop", "right", "num"),
    ("position", "Position", "right", "num"),
    ("risk", "Risk", "right", "num"),
    ("rr", "R:R", "right", "num"),
    ("action", "Action", "left", "num"),
)
_HEADERS = tuple(c[1] for c in _COLUMNS)

# Sort keys for columns whose display is a symbol or a label. Ranked so
# "descending" means "better" everywhere — otherwise the first click on
# Trend puts the broken ones on top.
_TREND_RANK = {"CONFIRMED": 4, "PARTIAL": 3, "RECOVERING": 2,
               "IMPAIRED": 1, "BROKEN": 0}
_BAND_RANK = {"UNDERVALUED": 2, "FAIR": 1, "OVERVALUED": 0}
_ACTION_ORDER = ("BUY NOW", "BUY ON CONFIRMATION", "BUY ON 8/21 EMA",
                 "BUY ON 50 MA", "BUY ON BREAKOUT RETEST", "BUY ON 200 MA",
                 "BUY ON SUPPORT", "DEEP PULLBACK \u2014 WAIT FOR SUPPORT",
                 "OWN / WAIT FOR PRICE", "OWN / WAIT FOR TREND",
                 "OWN / WAIT FOR ENTRY",
                 "WATCH", "WAIT", "RESEARCH", "AVOID", "THESIS BROKEN")
_ACTION_RANK = {a: len(_ACTION_ORDER) - i for i, a in enumerate(_ACTION_ORDER)}


def _select_box(r) -> str:
    """The per-row tick, for sending a set of names to the CSP scan.

    It lives INSIDE the ticker cell rather than in a column of its own, and
    that is the whole reason it works. Ticker is pinned, so the box stays on
    screen at any horizontal scroll; a twenty-third column would be
    draggable, hideable and — being unpinned — off-screen exactly when you
    have scrolled out to read the numbers you are selecting on.

    `data-elig` carries whether the CSP engine's company gate would accept
    the name, so the bar can warn before a scan is spent rather than after.
    """
    ok = _csp_eligible(r)
    return (f'<input type="checkbox" class="lt-pick" '
            f'value="{esc(r["ticker"])}" data-elig="{"1" if ok else "0"}" '
            f'onclick="event.stopPropagation()" onchange="ltPickChanged()" '
            f'title="Select for a cash-secured put scan" '
            f'style="margin-right:6px;vertical-align:middle;cursor:pointer">')


def _csp_eligible(r) -> bool:
    """Whether core.csp's COMPANY gate would let this name reach a chain.

    Asked here, on the page where the selection is made, so that "18 of your
    24 will be rejected before a single chain is fetched" is something you
    learn while choosing rather than three minutes into a scan. It is the
    engine's own classifier, not a restatement of its rules — a second copy
    would drift the first time a threshold moved.
    """
    if "_csp_elig" not in r:
        try:
            from stockanalysis.core.csp import eligibility as EL
            r["_csp_elig"] = EL.classify(r)["status"] != "CSP REJECTED"
        except Exception:
            # A page that will not render because the CSP package moved is
            # worse than a selection bar that cannot pre-warn.
            r["_csp_elig"] = True
    return r["_csp_elig"]


# Rung labels are abbreviated because three prices have to fit on one line
# inside a table cell. The full words stay in the hover title and in the
# detail panel, which is where someone reading closely already is.
_RUNG_SHORT = {"fair": "Fair", "attractive": "Attr", "exceptional": "Exc"}


def _earns_cell(r):
    """What the CURRENT price would earn, and which rung of the ladder it
    has reached — the fundamental panel's verdict, hoisted out of the
    detail drawer and into the table.

    This is not a duplicate of the Valuation column beside it. That one
    reports the valuation engine's band (UNDERVALUED / FAIR / OVERVALUED)
    — a statement about price against a modelled value. This one reports
    the price against a RETURN HURDLE that scales with quality, which is a
    different question and routinely gives a different answer: a name can
    read OVERVALUED against its sector multiple and still clear a 12%/yr
    bar, or read fairly valued and earn nothing.

    The ladder rides along because the band alone is not actionable. "Fair"
    tells you where price is; "Attr at or below $368.93" tells you what to
    wait for, and that was the one number worth opening the drawer for.
    """
    fu = (r.get("buy_zones") or {}).get("fundamental") or {}
    dash = '<span style="color:#898781">—</span>'
    if not fu:
        return dash, None

    if fu.get("blocked"):
        # Priced off peer multiples rather than cash flows. Shown as its own
        # state, not as a blank: an empty cell here would read as "no verdict
        # yet" when the truth is that this method cannot produce one.
        return (f'<span style="color:#898781;font-size:11px" '
                f'title="{esc(fu["blocked"])}">not modelled</span>', None)

    zone = fu.get("zone") or ""
    tone = _ZONE_TONE.get(zone, "#898781")
    cagr, hurdle = fu.get("expected_cagr_now"), fu.get("hurdle_pct")
    gap, intrinsic = fu.get("fair_value_gap_pct"), fu.get("intrinsic")

    head = (f'<span style="color:{tone};font-weight:700;white-space:nowrap">'
            f'{esc(fu.get("zone_icon") or "")} {esc(zone)}</span>')

    bits = []
    if cagr is not None:
        bits.append(f'<strong style="color:{tone}">{cagr:+.1f}%/yr</strong>')
    if hurdle:
        bits.append(f'hurdle {hurdle:.0f}%')
    if intrinsic:
        bits.append(f'model ${intrinsic:,.0f}'
                    + (f' ({gap:+.0f}%)' if gap is not None else ""))
    detail = (f'<div style="font-size:10px;color:#898781;white-space:nowrap">'
              f'{" · ".join(bits)}</div>' if bits else "")

    # A ladder built on shrinking cash flow discounts toward zero — the
    # engine flags that as not_a_target, and printing "Exc ≤ $3.40" beside a
    # $279 stock would be arithmetic presented as a level to wait for.
    rungs = ""
    if fu.get("ladder") and not fu.get("not_a_target"):
        rungs = " · ".join(
            f'{_RUNG_SHORT.get(x["key"], x["label"])} ≤${x["price"]:,.0f}'
            for x in fu["ladder"])
        rungs = (f'<div style="font-size:10px;color:#52514e;'
                 f'white-space:nowrap">{rungs}</div>')
    elif fu.get("not_a_target"):
        rungs = ('<div style="font-size:10px;color:#8a6d1a;white-space:nowrap">'
                 '⚠ ladder not a target</div>')

    tip = " · ".join(
        f'{x["label"]}: at or below ${x["price"]:,.2f} earns {x["cagr_pct"]:.0f}%/yr'
        for x in fu.get("ladder") or [])
    if fu.get("caveat"):
        tip = fu["caveat"] + ((" — " + tip) if tip else "")

    return (f'<span title="{esc(tip)}">{head}{detail}{rungs}</span>', cagr)


def _cells(r, selectable: bool = False):
    """(column key -> (html, sort_value)) for one result row."""
    q, v, p, conf, rs = (r["quality"], r["valuation"], r["pullback"],
                         r["confluence"], r["rs"])
    # The engine's own tri-state icon: 🟢 confirmed / 🟡 partial / 🔴 broken.
    # A grey "unknown" dot read as "no data" when the real meaning is
    # "structure holds, slopes unmeasured".
    trend_mark = r["trend"].get("icon") or "⚪"
    trend_state = r["trend"].get("state")
    rs_mark = ("🟢" if rs["strong"] else "🔴" if rs["strong"] is False else "⚪")
    market = {"FAVORABLE": "🟢", "SELECTIVE": "🟡", "DEFENSIVE": "🔴"}.get(
        r["regime"], "⚪")

    out = {
        "ticker": (
            (_select_box(r) if selectable else "")
            + f'<a href="{tv_url(r["ticker"])}" target="_blank" '
            f'style="font-weight:700;color:#0b0b0b;text-decoration:none">'
            f'{esc(r["ticker"])}</a>'
            f'<div style="font-size:10px;color:#898781;max-width:150px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{esc(r["name"])}</div>', r["ticker"]),
        "lquality": (
            _score_cell(q["score"], q["tier"],
                        _TIER_STYLE.get(q["tier"], "muted")), q["score"]),
        "earns": _earns_cell(r),
        "valuation": (
            (f'{v["band_icon"]} '
             f'{badge(v.get("band_label") or v["band"].title(), _BAND_STYLE.get(v["band"], "muted"), "small")}'
             f'{_fair_value_line(v)}'
             if v["band"] else '<span style="color:#898781">—</span>'),
            _BAND_RANK.get(v["band"])),
        "trend": (f'<span title="{esc(r["trend"].get("summary") or "")}">'
                  f'{trend_mark}</span>',
                  _TREND_RANK.get(trend_state)),
        "pullback": (
            f'{p.get("stage_icon", "")} '
            f'{esc(_STAGE_SHORT.get(p.get("stage"), _ZONE_SHORT.get(p["zone"], p["zone"])))}',
            _STAGE_RANK.get(p.get("stage"))),
        "price": (f'<strong>${r["price"]:,.2f}</strong>' if r.get("price")
                  else '<span style="color:#898781">—</span>', r.get("price")),
        "buyzone": _buyzone_cell(p),
        "resistance": _resistance_cell(p),
        "support": _support_confluence_cell(conf, r.get("ma_cluster") or {}),
        "buy_zone": _buy_zone_cell(r),
        "dca": _dca_cell(r),
        "investment": _status_cell(r.get("investment") or {}, _INVEST_STYLE),
        "entry_score": (
            f'<strong>{(r.get("entry") or {}).get("score", "—")}</strong>'
            f'<div style="font-size:10px;color:#898781">of 100</div>',
            (r.get("entry") or {}).get("score")),
        "swing": _swing_cell(r),
        "swing_trade": _swing_trade_cell(r),
        "rs": (rs_mark, rs["score"]),
        "market": (market, r["regime"]),
        "lt": (f'<strong>{r["lt_score"] if r["lt_score"] is not None else "—"}</strong>',
               r["lt_score"]),
        "entry_stop": _entry_stop_cell(r),
        "position": _position_cell(r),
        "risk": _risk_cell(r),
        "rr": _rr_cell(r),
        "action": (
            _action_pill(r["action"], r["icon"])
            + (f'<div style="font-size:10px;color:#898781">'
               f'{r["tranche_pct"]}% of target</div>'
               if r.get("tranche_pct") else "")
            + _action_sizing_line(r)
            + _dca_line(r),
            _ACTION_RANK.get(r["action"])),
    }
    return out


def _dca_line(r) -> str:
    """The DCA note under the verdict — additive, never instead of it.

    The Action column still says what the engine's gates concluded; this
    adds the separate observation that price has reached an accumulation
    rung on a business good enough for a tranche. META reads "OWN / WAIT
    FOR TREND" and "Zone 1 · 31% off the high" at the same time, and both
    are true.
    """
    _hit, note = _dca_read(r)
    if not note:
        return ""
    return (f'<div style="font-size:10px;color:#0F6E56;font-weight:600;'
            f'white-space:nowrap">🧱 {esc(note)}</div>')


def _action_sizing_line(r) -> str:
    """§11: the trade in one line under the verdict.

    Only for actions that are actually offering an entry — printing
    "150 shares · $18,500" under AVOID would read as an instruction.
    """
    plan = r.get("sizing_plan") or {}
    action = str(r.get("action") or "")
    if not plan.get("ok") or not action.startswith("BUY"):
        return ""
    sizing, target = plan["sizing"], plan.get("target") or {}
    bits = [f'{sizing["shares"]:,} sh', f'${sizing["position_value"]:,.0f}',
            f'${sizing["actual_risk"]:,.0f} risk']
    if target.get("rr") is not None:
        bits.append(f'{target["rr"]:.1f}R')
    return (f'<div style="font-size:10px;color:#52514e;white-space:nowrap">'
            f'{esc(" · ".join(bits))}</div>')


def _row(r, open_detail: bool = False, order=None,
         selectable: bool = False):
    order = order or [c[0] for c in _COLUMNS]
    align = {c[0]: c[2] for c in _COLUMNS}
    cells = _cells(r, selectable)
    tds = []
    for key in order:
        html, sort_value = cells.get(key, ("", None))
        sort_attr = "" if sort_value is None else esc(str(sort_value))
        pin = ' class="lt-pin"' if key in PINNED_COLUMNS else ""
        # The data-colw span is deliberately unstyled here. Width lives in
        # localStorage, so only the browser knows it, and only a column the
        # user has actually resized gets sized at all — an untouched table
        # renders exactly as it did before this wrapper existed.
        tds.append(f'<td data-col="{key}"{pin} data-sort="{sort_attr}" '
                   f'style="{_TD};text-align:{align.get(key, "left")}">'
                   f'<span data-colw="{key}">{html}</span></td>')

    # Anchored by ticker so the buy-zone alert bar can hand you the row
    # rather than the ticker's name. On a 60-row table the difference between
    # "GOOGL is at its zone" and being able to READ GOOGL's row is the whole
    # value of the alert.
    return (f'<tr data-main="1" id="ltr-{esc(r["ticker"])}">{"".join(tds)}</tr>'
            f'<tr data-detail="1"><td colspan="{len(_COLUMNS)}" '
            f'style="padding:0 8px 10px;border-bottom:0.5px solid #f1efea">'
            f'<details{" open" if open_detail else ""}>'
            f'<summary style="cursor:pointer;font-size:11px;'
            f'color:#185FA5;padding:2px 0">Show the reasoning</summary>'
            f'<div style="padding:8px 0 4px">{_detail(r)}</div>'
            f'</details></td></tr>')


# Identity and price are anchors, not data columns: at nineteen columns the
# table scrolls well past its own left edge, and a row of numbers with no
# ticker attached is unreadable. Price earns the second slot because nearly
# every other cell — entry, stop, allocation, the MA distances — is a
# statement ABOUT the price, and reading them against a number that has
# scrolled away is guesswork.
#
# Pinned rather than merely sticky: they are also removed from drag-reorder
# below, because a sticky cell that is not leftmost renders on top of
# whatever it now overlaps. They stay leftmost, and in this order.
PINNED_COLUMNS = ("ticker", "price")

# `left` is set by initLtTable(), not here: the second pinned column has to
# begin exactly where the first ends, and the ticker column's width depends
# on the longest company name in the current result set. A hardcoded offset
# would be right for one page load and wrong for the next.
#
# background is load-bearing — without it the scrolling cells show THROUGH
# the pinned ones. The seam goes on the LAST pinned column only; on every
# pinned cell it would draw a line between ticker and price, which are one
# frozen group rather than two.
PIN_CSS = """
<style>
/* Containing block for the absolutely-positioned resize handle. Declared
   here rather than in the header's inline style so the sticky rule below —
   more specific, and inline styles would outrank it — still wins on the
   pinned columns. `sticky` is itself a positioned value, so the handle
   anchors correctly on those two as well. */
#lt-table th { position: relative; }
#lt-table td.lt-pin, #lt-table th.lt-pin {
  position: sticky; z-index: 2; background: #fff;
}
#lt-table th.lt-pin { z-index: 3; cursor: pointer; }
#lt-table td.lt-pin-last, #lt-table th.lt-pin-last {
  box-shadow: 1px 0 0 #e1e0d9;
}
</style>"""


def analysis_table(rows, open_detail: bool = False,
                   empty_msg: str = "Nothing matches this filter.",
                   selectable: bool = False) -> str:
    """The engine's table for an arbitrary set of evaluated rows.

    Both the Long-Term page and the Dashboard's Scan & Analyze panel render
    through here, so a name reads identically wherever it is shown. A
    simplified second table for the Dashboard would be a second thing to keep
    in step with the engine's columns, and the first one to go stale.

    min-width matters: without it `width:100%` makes the browser compress the
    columns to fit the card instead of overflowing, and Action — the one the
    table exists to show — is what gets crushed.

    `selectable` adds the per-row tick that feeds the CSP scan. Off by
    default, so the Dashboard's Scan & Analyze panel — which renders through
    here and has no selection bar to act on them — does not grow a column of
    checkboxes that do nothing.
    """
    headers = ""
    for key, label, align, stype in _COLUMNS:
        pinned = key in PINNED_COLUMNS
        hint = ("Click to sort · pinned left · drag right edge to resize"
                if pinned else
                "Click to sort · drag to reorder · drag right edge to resize")
        # The label rides in its own data-colw span so a width can be applied
        # to it; the arrow stays outside so a narrowed column still shows
        # which way it is sorted. draggable="false" on the handle keeps a
        # mousedown there out of the header's own drag-to-reorder.
        headers += (
            f'<th data-col="{key}" data-type="{stype}" data-dir=""'
            + (' class="lt-pin"' if pinned else "")
            + f' title="{hint}"'
            + f' style="{_TH};text-align:{align}">'
              f'<span data-colw="{key}">{esc(label)}</span>'
              f'<span class="lt-arrow"></span>'
              f'<span class="col-resizer" draggable="false"></span></th>')
    body_rows = "".join(_row(r, open_detail=open_detail,
                             selectable=selectable) for r in rows) or (
        f'<tr><td colspan="{len(_COLUMNS)}" style="padding:24px;'
        f'text-align:center">{empty(empty_msg)}</td></tr>')
    # border-collapse:separate — `collapse` drops the borders of a sticky
    # cell as it scrolls, which left the pinned column's seam flickering in
    # and out. border-spacing:0 keeps the layout identical to collapse.
    return (PIN_CSS
            + '<div style="overflow-x:auto">'
            '<table id="lt-table" style="width:100%;min-width:1180px;'
            'border-collapse:separate;border-spacing:0">'
            f'<thead><tr>{headers}</tr></thead>'
            f'<tbody>{body_rows}</tbody></table></div>')

# Ticker characters that survive the round trip to yfinance and TradingView:
# letters and digits, plus the separators share classes and indices use
# (BRK-B, BF.B, ^GSPC). Everything else is treated as a delimiter.
_TICKER_CHARS = set(string.ascii_uppercase + string.digits + ".-^")


def parse_tickers(raw: str, limit: int = 60) -> list[str]:
    """"nvda, msft  aapl;brk-b" -> ["NVDA", "MSFT", "AAPL", "BRK-B"].

    Split on anything that is not a ticker character rather than on a
    specific separator, because people paste lists in every shape a
    spreadsheet, a broker export or a chat message produces — commas,
    newlines, tabs, semicolons, or plain spaces. Order is the order typed
    and duplicates collapse, so a searched list reads back the way it was
    written.

    `limit` exists only to stop a pasted thousand-line column turning one
    page render into a thousand table rows.
    """
    out, seen, token = [], set(), []

    def flush():
        if not token:
            return
        word = "".join(token)
        if word and word not in seen:
            seen.add(word)
            out.append(word)
        token.clear()

    for ch in (raw or "").upper():
        if ch in _TICKER_CHARS:
            token.append(ch)
        else:
            flush()
    flush()
    return out[:limit]

# Actions the framework treats as actionable, in the order the page shows them.
_BUY_ACTIONS = ("BUY NOW", "BUY ON CONFIRMATION", "BUY ON 8/21 EMA",
                "BUY ON 50 MA", "BUY ON BREAKOUT RETEST", "BUY ON 200 MA",
                "BUY ON SUPPORT")

DEFAULT_LIMIT = 60


# ─────────────────────────────────────────────────────────────────────────────
# RULE BUILDER — screener rules over the engine's own columns
# ─────────────────────────────────────────────────────────────────────────────

def _list_options(active: str) -> str:
    """The saved watchlists, most-used first.

    Ordered by hand rather than alphabetically because "daytrade" and
    "watchlist" are the two anyone reaches for, and burying them between
    "AI: Optics" and "Dividend" makes the picker a scrolling exercise.
    """
    from . import api
    lists = api.longterm_lists()
    preferred = [n for n in ("watchlist", "daytrade", "Longterm", "AI",
                             "Dividend") if n in lists]
    rest = sorted(n for n in lists if n not in preferred)
    out = ['<option value="">All tickers</option>']
    for name in preferred + rest:
        sel = " selected" if name == active else ""
        out.append(f'<option value="{esc(name)}"{sel}>{esc(name)} '
                   f'({len(lists[name])})</option>')
    return "".join(out)


def _default_screen():
    """The long-term screen module — the default for every builder below.

    These builders render a preset pill, a removable rule and a
    field/operator/value form, and none of that is specific to WHICH set of
    fields is being screened. /csp supplies its own module exposing the same
    names (PRESETS, PRESET_GROUPS, FIELD_GROUPS, describe, and a field
    tuple), so the two pages share one implementation rather than growing a
    second that drifts.
    """
    from stockanalysis.core.longterm import screen as LS
    return LS


def _preset_bar(link, active_rules, needs_rescan, counts, mod=None):
    """The screens a manager actually runs, grouped by the question asked.

    Ordered "what to own" -> "when to buy" -> "what would stop me" because
    that is the order the work happens in, and it is the framework's own
    hierarchy. A preset list organised by field type would scatter it.
    """
    # `mod` is the screen module supplying PRESET_GROUPS/PRESETS. Defaults
    # to the long-term one; /csp passes core.csp.screen, which exposes the
    # same names. Parameterised rather than copied so there is one builder
    # for a preset pill and not two that drift.
    LS = mod or _default_screen()
    current = set(active_rules)
    sections = []
    for group in LS.PRESET_GROUPS:
        items = [p for p in LS.PRESETS if p["group"] == group]
        if not items:
            continue
        pills = []
        for preset in items:
            on = current == set(preset["rules"])
            # A preset whose inputs the library does not carry yet returns
            # nothing, and would be indistinguishable from a broken one
            # without saying so.
            stale = preset.get("needs_statements") and needs_rescan
            bg, fg = ("#0b0b0b", "white") if on else (
                ("#F7F4EC", "#a09b8c") if stale else ("#F1EFE8", "#444441"))
            note = ("needs a scan for statement data — "
                    "the reverse DCF has no inputs yet" if stale
                    else preset["desc"])
            # Counted live against the universe now on screen, never stored
            # on the preset. A recorded count is a claim about a moving
            # target: splitting one pullback stage in two silently took
            # Fallen Quality from 10 matches to 0 while it still advertised
            # 10, and clicking it returned an empty table.
            n = counts.get(preset["key"])
            count = "" if n is None else f"{n}"
            pills.append(
                f'<a href="{esc(link(rule=[] if on else list(preset["rules"]), action=""))}" '
                f'title="{esc(note)}" style="background:{bg};color:{fg};'
                f'font-size:11px;font-weight:600;padding:5px 10px;'
                f'border-radius:6px;text-decoration:none;white-space:nowrap;'
                f'display:inline-flex;gap:5px;align-items:center">'
                f'{preset["icon"]} {esc(preset["name"])}'
                + (f'<span style="font-weight:400;opacity:.65">'
                   f'{esc("needs scan" if stale else count)}</span>'
                   if (count or stale) else "")
                + '</a>')
        sections.append(
            f'<div style="display:flex;gap:6px;flex-wrap:wrap;'
            f'align-items:center;margin-bottom:6px">'
            f'<span style="font-size:10px;text-transform:uppercase;'
            f'letter-spacing:.06em;color:#898781;min-width:112px">'
            f'{esc(group)}</span>{"".join(pills)}</div>')
    return (f'<div style="background:white;border:0.5px solid #e1e0d9;'
            f'border-radius:12px;padding:12px 14px;margin-bottom:14px">'
            f'{"".join(sections)}</div>')


def _rule_pills(conds, link, mod=None):
    """Active rules, each removable. Rules live in the URL, so a filtered
    view is a link and the back button undoes one rule at a time."""
    LS = mod or _default_screen()
    if not conds:
        return ""
    out = []
    for i, c in enumerate(conds):
        rest = [_rule_text(x) for j, x in enumerate(conds) if j != i]
        out.append(
            f'<span style="display:inline-flex;align-items:center;gap:6px;'
            f'background:#E6F1FB;color:#0C447C;font-size:11px;font-weight:600;'
            f'padding:4px 8px;border-radius:6px">{esc(LS.describe(c))}'
            f'<a href="{esc(link(rule=rest))}" style="color:#0C447C;'
            f'text-decoration:none;font-weight:700" title="Remove">×</a></span>')
    return "".join(out)


def _num_text(v):
    """85.0 -> "85". Values arrive as floats from the parser and go back into
    the URL on every re-render, so without this a rule picks up a trailing
    ".0" the moment it round-trips through a pill."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _rule_text(cond) -> str:
    if isinstance(cond.value, bool):
        value = "true" if cond.value else "false"
    elif cond.op == "between":
        value = f"{_num_text(cond.value)},{_num_text(cond.value2)}"
    else:
        value = _num_text(cond.value)
    return f"{cond.field}:{cond.op}:{value}"


def _rule_builder(conds, rule_op, link, stats, n_matched, n_total,
                  search_q="", mod=None, action="/longterm",
                  search_name="q"):
    """Field -> operator -> value, as a plain GET form.

    The operator list and the value widget both depend on the field's kind,
    which is the only reason there is any JavaScript here: everything else on
    this page is a link.
    """
    import json as _json

    from stockanalysis.core.screener import OPS_FOR_KIND

    LS = mod or _default_screen()
    fields = getattr(LS, "LONGTERM_FIELDS", None) or LS.CSP_FIELDS
    meta = {f.key: {"kind": f.kind, "ops": list(OPS_FOR_KIND.get(f.kind, ())),
                    "values": list(f.values), "unit": f.unit,
                    "hint": f.hint, "label": f.label}
            for f in fields}

    options = []
    for group in LS.FIELD_GROUPS:
        items = [f for f in fields if f.group == group]
        if not items:
            continue
        options.append(f'<optgroup label="{esc(group)}">' + "".join(
            f'<option value="{esc(f.key)}">{esc(f.label)}</option>'
            for f in items) + '</optgroup>')

    hidden = "".join(
        f'<input type="hidden" name="rule" value="{esc(_rule_text(c))}">'
        for c in conds)

    # Per-rule diagnosis. "0 matches" is a dead end; "0 matches, and 431 rows
    # had no data for this rule" is an answer.
    # Per-rule diagnosis. "0 matches" is a dead end; naming which rule is
    # binding, and what dropping it would return, is an answer. `alone` and
    # `without` differ usefully: a rule can look permissive on its own and
    # still be the one killing the screen, through how it overlaps the rest.
    diag = ""
    if conds and stats:
        # Exactly one rule gets the marker. Flagging every rule whose
        # removal helps marks all of them on a tight screen, which is the
        # same as marking none.
        loosest = None
        if len(conds) > 1:
            ranked = [st for st in stats if st.get("without") is not None]
            if ranked:
                top = max(ranked, key=lambda st: st["without"])
                if top["without"] > max(n_matched * 2, n_matched + 5):
                    loosest = top.get("index")

        lines = []
        for st in stats:
            label = st.get("label") or st.get("field")
            without = st.get("without")
            binding = loosest is not None and st.get("index") == loosest
            lines.append(
                f'<li style="margin:2px 0">'
                f'{"<strong>" if binding else ""}{esc(str(label))}'
                f'{"</strong>" if binding else ""} — '
                f'{st.get("alone", 0)} on its own'
                + (f', {st["missing"]} with no data' if st.get("missing")
                   else "")
                + (f' · drop it and {without} match' if len(conds) > 1
                   and without is not None else "")
                + (' ← the binding one' if binding else "") + '</li>')
        diag = (f'<div style="font-size:11px;color:#898781;margin-top:8px">'
                f'<strong>{n_matched}</strong> of {n_total} match'
                f'<ul style="margin:4px 0 0;padding-left:16px;list-style:none">'
                f'{"".join(lines)}</ul></div>')

    toggle = ""
    if len(conds) > 1:
        other = "OR" if rule_op == "AND" else "AND"
        toggle = (f'<a href="{esc(link(rule_op=other))}" '
                  f'style="font-size:11px;color:#185FA5;margin-left:6px">'
                  f'match {esc(other.lower())} instead</a>')

    return f"""
<div style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;
            padding:12px 14px;margin-bottom:14px">
  <form method="get" action="{esc(action)}"
        style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    {hidden}
    <input type="hidden" name="{esc(search_name)}" value="{esc(search_q or '')}">
    <input type="hidden" name="rule_op" value="{esc(rule_op)}">
    <span style="font-size:11px;color:#898781;text-transform:uppercase;
                 letter-spacing:.06em">Add rule</span>
    <select name="_field" id="lt-rule-field"
            style="padding:7px 9px;font-size:12px;border:0.5px solid #e1e0d9;
                   border-radius:7px">{"".join(options)}</select>
    <select name="_op" id="lt-rule-op"
            style="padding:7px 9px;font-size:12px;border:0.5px solid #e1e0d9;
                   border-radius:7px"></select>
    <span id="lt-rule-valuewrap"></span>
    <button type="submit" class="btn" style="padding:7px 14px">Add</button>
    <span id="lt-rule-hint" style="font-size:11px;color:#898781"></span>
  </form>
  <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;
              margin-top:{'10px' if conds else '0'}">
    {_rule_pills(conds, link, mod=LS)}{toggle}
    {f'<a href="{esc(link(rule=[]))}" style="font-size:11px;color:#185FA5;margin-left:auto">Clear rules</a>' if conds else ''}
  </div>
  {diag}
</div>
<script>window.LT_FIELDS = {_json.dumps(meta)};</script>"""


RISK_JS = r"""
async function saveRiskSettings(form) {
  const fd = new FormData(form);
  try {
    const res = await fetch('/api/risk/save', { method: 'POST',
      body: new URLSearchParams(fd) });
    const data = await res.json();
    toast(data.message || (data.ok ? 'Saved' : 'Failed'), data.ok ? 'ok' : 'err');
    // Every share count on the page was computed against the old account, so
    // a partial update would leave the table disagreeing with its own header.
    if (data.ok) setTimeout(() => location.reload(), 400);
  } catch (e) { toast('Save failed: ' + e, 'err'); }
  return false;
}
"""

EXPORT_JS = r"""
// Comma-separated tickers for the current selection, as a rescan input.
function ltCopy(which) {
  var box = document.getElementById('lt-export');
  var area = document.getElementById('lt-export-box');
  var note = document.getElementById('lt-export-note');
  if (!box || !area) return;
  var text = box.getAttribute('data-' + which) || '';
  if (!text) { toast('Nothing to copy', 'err'); return; }

  // Shown BEFORE the copy is attempted, not after it succeeds. Over plain
  // http from another machine navigator.clipboard is undefined and the
  // catch below is the normal path, not the exceptional one — the reader
  // needs the text on screen to select by hand either way.
  area.value = text;
  area.style.display = '';
  var n = text.split(',').length;
  if (note) note.textContent = which === 'library'
    ? n + ' tickers — the whole library'
    : n + ' tickers — the rows these filters left';

  var done = function () {
    toast('Copied ' + n + ' tickers', 'ok');
  };
  var manual = function () {
    area.focus();
    area.select();
    try {
      if (document.execCommand('copy')) { done(); return; }
    } catch (e) { /* fall through to the message below */ }
    toast('Select the box and copy — this browser blocked the clipboard',
          'err');
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, manual);
  } else {
    manual();
  }
}

// ── CSV export ──────────────────────────────────────────────────────────────
// The data is server-rendered into #lt-csv-data — every row the filters left,
// one scalar per field. This side only chooses columns, orders rows and
// assembles the file, so the numbers in the CSV are the same objects the
// table was rendered from rather than a second reading of the DOM.
var LT_CSV_KEY = 'lt.csvcols.v1';

function ltCsvData() {
  var tag = document.getElementById('lt-csv-data');
  if (!tag) return null;
  try { return JSON.parse(tag.textContent || 'null'); } catch (e) { return null; }
}

function ltCsvBoxes() {
  return Array.prototype.slice.call(
    document.querySelectorAll('.lt-csv-col'));
}

// Selected columns, in the order the picker lists them — which is the order
// _CSV_FIELDS declares, so a reader who ticks Price and Ticker still gets
// Ticker first rather than an order that depends on which they clicked.
function ltCsvChosen() {
  return ltCsvBoxes().filter(function (b) { return b.checked; })
                     .map(function (b) { return b.value; });
}

// Stored as {key: true|false} rather than a list of the ticked ones, so a
// field ADDED to _CSV_FIELDS later is absent from the record rather than
// recorded as unwanted — see initLtCsv().
function ltCsvSaveCols() {
  var state = {};
  ltCsvBoxes().forEach(function (b) { state[b.value] = b.checked; });
  try { localStorage.setItem(LT_CSV_KEY, JSON.stringify(state)); } catch (e) {}
  ltCsvNote();
}

function ltCsvNote() {
  var note = document.getElementById('lt-csv-note');
  if (!note) return;
  var n = ltCsvChosen().length;
  note.textContent = n ? n + ' column' + (n === 1 ? '' : 's') + ' selected'
                       : 'no columns selected';
}

// 1 = all, 0 = none, -1 = back to the server's defaults.
function ltCsvSelect(mode) {
  ltCsvBoxes().forEach(function (b) {
    b.checked = mode === 1 ? true
              : mode === 0 ? false
              : b.dataset.default === '1';
  });
  ltCsvSaveCols();
}

function ltCsvQuote(v) {
  if (v === null || v === undefined) return '';
  var s = String(v);
  if (s === 'true') s = 'yes';
  else if (s === 'false') s = 'no';
  // RFC 4180: quote anything carrying a delimiter, a quote or a newline, and
  // double the quotes inside. Company names arrive with commas in them.
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

// Rows in the order they are on screen. The table sorts client-side, so the
// server's order is only the starting point; exporting in it would hand back
// a different ranking from the one being looked at. Rows the page truncated
// away are still exported — appended in the server's order, after the ones
// that are visible.
//
// A row is an ARRAY positioned against data.fields, so `at` is the index of
// the ticker field rather than a property name.
function ltCsvOrder(rows, at) {
  var seen = {}, out = [], byTicker = {};
  rows.forEach(function (r) { byTicker[r[at]] = r; });
  var cells = document.querySelectorAll(
    '#lt-table tbody tr[data-main] td[data-col="ticker"]');
  Array.prototype.forEach.call(cells, function (td) {
    var t = td.getAttribute('data-sort');
    if (t && byTicker[t] && !seen[t]) { seen[t] = 1; out.push(byTicker[t]); }
  });
  rows.forEach(function (r) { if (!seen[r[at]]) out.push(r); });
  return out;
}

function ltCsvDownload() {
  var data = ltCsvData();
  if (!data || !data.rows || !data.rows.length) {
    toast('Nothing to export', 'err'); return;
  }
  var chosen = ltCsvChosen();
  if (!chosen.length) { toast('Pick at least one column', 'err'); return; }

  var labels = {}, index = {}, tickerAt = 0;
  data.fields.forEach(function (f, i) {
    labels[f.key] = f.label;
    index[f.key] = i;
    if (f.key === 'ticker') tickerAt = i;
  });

  var lines = [chosen.map(function (k) {
    return ltCsvQuote(labels[k] || k);
  }).join(',')];
  ltCsvOrder(data.rows, tickerAt).forEach(function (r) {
    lines.push(chosen.map(function (k) {
      return ltCsvQuote(r[index[k]]);
    }).join(','));
  });

  // CRLF and a BOM: without the BOM Excel reads the file as Latin-1 and the
  // sector names and · separators come through as mojibake, which looks like
  // corrupted data rather than an encoding default.
  var blob = new Blob(['﻿' + lines.join('\r\n') + '\r\n'],
                      { type: 'text/csv;charset=utf-8;' });
  var stamp = new Date().toISOString().slice(0, 10);
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'longterm-' + stamp + '-' + data.rows.length + 'rows.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  toast('Downloaded ' + data.rows.length + ' rows × ' + chosen.length +
        ' columns', 'ok');
}

// ── Send a selection to the CSP scan ────────────────────────────────────────
// The scan is a job of minutes, so this starts one and hands back a link to
// /csp filtered to exactly the selection. It never blocks and never claims
// to know the answer yet.
var LT_MAX_PICK = 60;

function ltPicks() {
  return Array.prototype.slice.call(document.querySelectorAll('.lt-pick'));
}

function ltPicked() {
  return ltPicks().filter(function (b) { return b.checked; });
}

function ltPickChanged() {
  var bar = document.getElementById('lt-csp-bar');
  if (!bar) return;
  var on = ltPicked();
  bar.style.display = on.length ? '' : 'none';
  var count = document.getElementById('lt-csp-count');
  var warn = document.getElementById('lt-csp-warn');
  if (count) {
    count.textContent = on.length + ' selected';
  }
  if (warn) {
    // Counted before the scan rather than reported after it. Naming a
    // ticker makes the scan fetch its chain even when the company gate
    // says no, so these are NOT names that come back blank — they come
    // back rejected with their premiums attached, for you to judge. Saying
    // "will be rejected before any chain is fetched" was true of a bulk
    // scan and false of this one.
    var bad = on.filter(function (b) { return b.dataset.elig === '0'; }).length;
    var bits = [];
    if (bad) {
      bits.push(bad + ' fail the company gate — priced as reference, '
                + 'not ranked');
    }
    if (on.length > LT_MAX_PICK) {
      bits.push('only the first ' + LT_MAX_PICK + ' will be scanned');
    }
    warn.textContent = bits.join(' · ');
  }
}

// mode 1 = every row on screen, 0 = none.
function ltPickAll(mode) {
  ltPicks().forEach(function (b) { b.checked = !!mode; });
  ltPickChanged();
}

// The subset the CSP engine would actually take a chain for. The single
// most useful button here: it turns "these twenty look cheap" into "these
// eleven are worth the scan".
function ltPickEligible() {
  ltPicks().forEach(function (b) { b.checked = b.dataset.elig === '1'; });
  ltPickChanged();
}

function ltPickScan() {
  var on = ltPicked().map(function (b) { return b.value; });
  if (!on.length) { toast('Nothing selected', 'err'); return; }
  var msg = document.getElementById('lt-csp-msg');
  var go = document.getElementById('lt-csp-go');
  var list = on.slice(0, LT_MAX_PICK);
  var dte = parseInt((document.getElementById('lt-csp-dte') || {}).value, 10);
  if (!dte || dte < 7 || dte > 120) dte = 35;

  var body = new URLSearchParams();
  body.append('action', 'csp_scan');
  body.append('tickers', list.join(','));
  body.append('target_dte', dte);
  // A window around the target rather than the page default, so a 35-day
  // target does not land in a 20-45 window that also admits 20-day weeklies.
  body.append('min_dte', Math.max(7, dte - 14));
  body.append('max_dte', dte + 14);
  body.append('earnings_policy',
              (document.getElementById('lt-csp-earn') || {}).value || 'AVOID');
  // The scan's `limit` caps how many SURVIVORS get chain work. Left at its
  // default of 25 a selection of forty would quietly lose its tail after
  // the eligibility pass, which is the one failure a hand-picked list must
  // not have.
  body.append('limit', list.length);

  if (go) { go.disabled = true; go.textContent = 'starting…'; }
  if (msg) msg.textContent = '';
  var href = '/csp?tickers=' + encodeURIComponent(list.join(','));
  fetch('/run', { method: 'POST', body: body })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (go) { go.disabled = false; go.textContent = '🪙 Scan for put premiums →'; }
      if (!d.ok) { if (msg) msg.textContent = d.message || 'could not start'; return; }
      if (msg) {
        msg.innerHTML = 'Scanning ' + list.length + ' name(s) — a few minutes. '
          + 'Results appear on <a href="' + href + '" style="color:#8FBEF0">'
          + 'the CSP page →</a> when the job tray clears.';
      }
      toast('CSP scan started for ' + list.length + ' names', 'ok');
    })
    .catch(function () {
      if (go) { go.disabled = false; go.textContent = '🪙 Scan for put premiums →'; }
      if (msg) msg.textContent = 'could not reach the server';
    });
}

(function initLtCsv() {
  var boxes = ltCsvBoxes();
  if (!boxes.length) return;
  // A remembered choice only overrides fields it actually has an opinion
  // about. A field removed from _CSV_FIELDS has no checkbox and disappears;
  // a field ADDED since the choice was saved keeps its server default rather
  // than arriving silently unticked, which is how a new column would
  // otherwise go unnoticed forever by the people who use the export most.
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(LT_CSV_KEY) || 'null'); }
  catch (e) {}
  if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
    boxes.forEach(function (b) {
      if (Object.prototype.hasOwnProperty.call(saved, b.value)) {
        b.checked = !!saved[b.value];
      }
    });
  }
  boxes.forEach(function (b) { b.addEventListener('change', ltCsvSaveCols); });
  ltCsvNote();
})();
"""


def _risk_settings_form(s: dict) -> str:
    """§1 — the four inputs the whole engine sizes against."""
    def field(name, label, value, step, suffix=""):
        return (f'<label style="font-size:10px;text-transform:uppercase;'
                f'letter-spacing:.06em;color:#898781">{esc(label)}'
                f'<input name="{name}" type="number" step="{step}" min="0" '
                f'value="{value:g}" style="display:block;width:110px;'
                f'margin-top:3px;padding:6px 8px;font-size:12px">'
                f'</label>{suffix}')
    return (
        '<form onsubmit="event.preventDefault();saveRiskSettings(this);return false" '
        'style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end">'
        + field("capital", "Trading capital $", s["capital"], "1000")
        + field("risk_pct", "Risk / trade %", s["risk_pct"], "0.1")
        + field("max_allocation_pct", "Max allocation %",
                s["max_allocation_pct"], "1")
        + field("atr_multiplier", "ATR stop ×", s["atr_multiplier"], "0.1")
        + '<button class="btn" style="padding:8px 16px">Apply</button>'
        + f'<div style="font-size:11px;color:#898781;padding-bottom:7px">'
          f'Max risk <b>${s["max_dollar_risk"]:,.0f}</b> · '
          f'max position <b>${s["max_position_value"]:,.0f}</b></div>'
        + '</form>')


def _risk_dashboard(summary: dict, s: dict) -> str:
    """§13 — what the setups currently on screen would commit, in aggregate.

    Every figure is PLANNED, not open: most of these entries are resting
    orders that have not triggered, and several are alternatives to each
    other. The card says so rather than presenting the sum as a portfolio,
    because "Planned risk $7,250" read as open risk is the kind of number
    that changes behaviour on a false premise.
    """
    def tile(label, value, sub="", tone=""):
        colour = {"good": "#0F6E56", "watch": "#8a6d1a",
                  "bad": "#A32D2D"}.get(tone, "#0b0b0b")
        return (f'<div style="min-width:118px">'
                f'<div style="font-size:10px;color:#898781;text-transform:uppercase;'
                f'letter-spacing:.06em">{esc(label)}</div>'
                f'<div style="font-size:16px;font-weight:600;color:{colour}">'
                f'{value}</div>'
                + (f'<div style="font-size:10px;color:#898781">{esc(sub)}</div>'
                   if sub else "")
                + '</div>')

    n = summary["n_actionable"]
    if not n:
        body = (_risk_settings_form(s)
                + '<div style="margin-top:12px">'
                + empty("No sizeable setups in this view — nothing here has "
                        "both an entry and a defensible stop.") + '</div>')
        return card("Portfolio risk", body, "🛡️")

    # Committing more than the whole account across simultaneous entries is
    # arithmetically possible and worth flagging; so is stacking more than a
    # few maximum-risk trades at once.
    cap_tone = ("bad" if summary["planned_capital_pct"] > 100 else
                "watch" if summary["planned_capital_pct"] > 75 else "good")
    risk_tone = ("bad" if summary["planned_risk_pct"] > 10 else
                 "watch" if summary["planned_risk_pct"] > 6 else "good")
    rr = summary.get("avg_rr")
    rr_tone = ("good" if rr and rr >= 2 else "watch" if rr and rr >= 1.5
               else "bad" if rr else "")

    tiles = (
        tile("Capital", f'${s["capital"]:,.0f}',
             f'{s["risk_pct"]:g}% / trade · {s["max_allocation_pct"]:g}% max')
        + tile("Actionable", f'{n}', "with entry + stop")
        + tile("Planned capital", f'${summary["planned_capital"]:,.0f}',
               f'{summary["planned_capital_pct"]:.0f}% of account', cap_tone)
        + tile("Planned risk", f'${summary["planned_risk"]:,.0f}',
               f'{summary["planned_risk_pct"]:.2f}% of account', risk_tone)
        + tile("Average R:R", f'{rr:.2f}R' if rr else "—",
               "across sized setups", rr_tone)
        + tile("Largest position",
               f'{summary["max_allocation"]["pct"]:.1f}%',
               summary["max_allocation"]["ticker"])
        + tile("Largest risk", f'${summary["max_risk"]["dollars"]:,.0f}',
               summary["max_risk"]["ticker"])
        + (tile("Top sector", f'{summary["top_sector_pct"]:.0f}%',
                summary["top_sector"] or "")
           if summary.get("top_sector") else ""))

    note = (
        '<div style="font-size:10px;color:#898781;margin-top:10px">'
        'These are <b>planned</b> figures for setups that mostly have not '
        'triggered, and several are alternatives to each other — read them as '
        'a concentration check, not as open risk. Sector concentration is the '
        'cheapest available proxy for correlated risk and a weak one: two AI '
        'semiconductor names in different GICS sectors will draw down '
        'together and this will not see it.</div>')

    return card("Portfolio risk", _risk_settings_form(s)
                + '<div style="display:flex;gap:22px;flex-wrap:wrap;'
                  'margin-top:14px">' + tiles + '</div>' + note, "🛡️")


# ─────────────────────────────────────────────────────────────────────────────
# BUY-ZONE ALERT — the one thing on this page that is an event
# ─────────────────────────────────────────────────────────────────────────────
# Everything else the engine publishes is a standing judgment: a quality
# score, a valuation band, a zone that sits below the market for weeks. None
# of it changes between visits, which is why the page can be read at leisure.
# Price arriving at a zone is the exception — it is dated, it is the thing
# the zone was computed FOR, and it is invisible in a table sorted by
# anything else. So it gets stated at the top, in words, before the table.
#
# Deliberately not framed as a buy list. The engine's own verdict lives in
# the Action column and disagrees with proximity often: a name can sit inside
# a technical band while quality, valuation or the thesis all say no. The bar
# reports where price is and hands you the row.


def _ownable(r) -> bool:
    """Whether the engine considers this a business worth owning at all.

    The gate the alert needs, and the reason it is a gate rather than a
    sort. Measured over the live library, 223 of 657 names are at or within
    5% of the zone in their Buy Zone column — and 166 of those are names the
    engine has already REJECTED on the business. Support arriving under a
    company that failed the quality gate is not an event; the price was
    always going to reach some moving average eventually, and the reason not
    to buy it was never the price.

    Firing on all 223 would reproduce exactly the failure entry_alerts.py
    documents: an alert that cannot fail to trigger carries no information.
    So the bar reports the 57 that survive and SAYS how many it withheld,
    with a link — hidden and unsaid are different things.
    """
    return (r.get("investment") or {}).get("status") != "REJECT"


def zone_alerts(rows, ownable_only: bool = True) -> list[tuple[dict, dict]]:
    """(row, proximity) for every name at or approaching its buy zone.

    Ordered the way the reader should work through them: names already in a
    zone first, then names approaching, each group nearest-first, and within
    that a qualifying investment zone ahead of a technical band — being
    inside a zone valuation also endorses is a different event from touching
    a moving average.
    """
    out = []
    for r in rows:
        prox = _zone_flag(r)
        if prox and prox["near"] and (not ownable_only or _ownable(r)):
            out.append((r, prox))
    out.sort(key=lambda rp: (0 if rp[1]["state"] == "IN_ZONE" else 1,
                             0 if rp[1]["qualifies"] else 1,
                             abs(rp[1]["gap_pct"])))
    return out


def _nearest_miss(rows) -> tuple[dict, dict] | None:
    """The closest name that did NOT make the cut, with its gap.

    The empty state is the reason this exists. "Nothing is near a zone" alone
    reads as a broken filter; "nothing within 5% — the closest is AMD at
    6.3%" reads as a measurement, and tells you whether to look again
    tomorrow or next month.
    """
    best = None
    for r in rows:
        prox = _zone_flag(r)
        if not prox or prox["state"] != "ABOVE":
            continue
        if best is None or abs(prox["gap_pct"]) < abs(best[1]["gap_pct"]):
            best = (r, prox)
    return best


# Past this many the bar stops being something you read and becomes a second
# table above the table. The rest are one click away behind the filter.
MAX_ZONE_PILLS = 24


def _zone_alert_bar(rows, link, near_mode: str) -> str:
    """The alert itself: who is at their zone, and a filter down to them."""
    alerts = zone_alerts(rows)
    withheld = len(zone_alerts(rows, ownable_only=False)) - len(alerts)
    near_on = bool(near_mode)
    if not alerts:
        miss = _nearest_miss(rows)
        tail = ""
        if miss:
            r, prox = miss
            tail = (f' The closest is <strong>{esc(r["ticker"])}</strong>, '
                    f'{abs(prox["gap_pct"]):.1f}% above the top of its '
                    f'{esc(prox.get("label") or "")} zone.')
        return (f'<div style="background:#F1EFE8;border-radius:10px;'
                f'padding:10px 13px;margin-bottom:14px;font-size:12px;'
                f'color:#444441">Nothing in this view is within '
                f'{BZ.NEAR_ZONE_PCT:.0f}% of its buy zone.{tail}</div>')

    in_zone = [a for a in alerts if a[1]["state"] == "IN_ZONE"]
    pills = []
    for r, prox in alerts[:MAX_ZONE_PILLS]:
        fg, bg, _label = _ZONE_MARK[prox["state"]]
        gap = ("in zone" if prox["state"] == "IN_ZONE"
               else f'{abs(prox["gap_pct"]):.1f}% away')
        # The verdict travels WITH the ticker rather than being looked up in
        # the table afterwards. A list of names under an alert heading reads
        # as a recommendation unless the engine's actual answer is on the
        # same line, and for a good number of these it is WATCH or AVOID.
        action = _ACTION_SHORT.get(r.get("action"), r.get("action") or "")
        title = (f'{prox.get("label") or "zone"} '
                 f'${prox["low"]:,.2f}–${prox["high"]:,.2f}'
                 f' · {r.get("action") or ""}')
        # A row that reached its zone with no price on it cannot exist —
        # proximity is measured FROM the price — but the cell renderers all
        # guard the same way and a formatting crash here would take the whole
        # page rather than one number.
        price = (f'${r["price"]:,.2f} → ' if r.get("price") else "")
        pills.append(
            f'<a href="#ltr-{esc(r["ticker"])}" title="{esc(title)}" '
            f'style="display:inline-flex;gap:6px;align-items:baseline;'
            f'background:{bg};color:{fg};border-radius:7px;padding:4px 9px;'
            f'text-decoration:none;white-space:nowrap;font-size:11px">'
            f'<strong style="font-size:12px">{esc(r["ticker"])}</strong>'
            f'<span style="opacity:.85">{price}'
            f'${prox["low"]:,.0f}–${prox["high"]:,.0f}</span>'
            f'<span style="opacity:.7">{esc(gap)}</span>'
            f'<span style="opacity:.6">· {esc(action)}</span></a>')

    if len(alerts) > MAX_ZONE_PILLS:
        pills.append(
            f'<a href="{esc(link(near="1", limit=""))}" '
            f'style="display:inline-flex;align-items:center;'
            f'background:#F1EFE8;color:#444441;border-radius:7px;'
            f'padding:4px 9px;text-decoration:none;font-size:11px;'
            f'font-weight:600">+{len(alerts) - MAX_ZONE_PILLS} more →</a>')

    head = (f'{len(in_zone)} in a zone now'
            if len(in_zone) == len(alerts) else
            f'{len(alerts)} at or within {BZ.NEAR_ZONE_PCT:.0f}% of a buy zone'
            + (f' · {len(in_zone)} inside one now' if in_zone else ''))

    toggle = (f'<a href="{esc(link(near="" if near_on else "1", limit=""))}" '
              f'style="margin-left:auto;font-size:11px;font-weight:600;'
              f'color:#0C447C;text-decoration:none;white-space:nowrap">'
              f'{"Show every name" if near_on else "Show only these →"}</a>')

    # Withheld, and said so. The alternative — quietly dropping 166 names —
    # would make the bar's count unreproducible from the table beside it,
    # which is the one thing a page built on "every claim is checkable"
    # cannot do.
    held = ""
    if withheld:
        held = (f' <a href="{esc(link(near="all", limit=""))}" '
                f'style="color:#185FA5">{withheld} more</a> reached a zone '
                f'on a business the engine has rejected, and are not counted '
                f'here — the price was never the reason to pass on those.')

    return (
        f'<div style="background:#FFFDF7;border:0.5px solid #e8d5aa;'
        f'border-radius:12px;padding:11px 13px;margin-bottom:14px">'
        f'<div style="display:flex;gap:10px;align-items:baseline;'
        f'flex-wrap:wrap;margin-bottom:8px">'
        f'<span style="font-size:12px;font-weight:700;color:#633806">'
        f'⚑ Approaching a buy zone</span>'
        f'<span style="font-size:11px;color:#8a6d1a">{esc(head)}</span>'
        f'{toggle}</div>'
        f'<div style="display:flex;gap:6px;flex-wrap:wrap">'
        f'{"".join(pills)}</div>'
        f'<div style="font-size:10px;color:#898781;margin-top:8px">'
        f'A price event, not a verdict — the zone is where support and, for '
        f'the green ones, valuation both sit, and the engine\'s own answer is '
        f'the last item on each pill. Measured against the same zone the Buy '
        f'Zone column shows, so every claim here is checkable one click away.'
        f'{held}</div></div>')


# ─────────────────────────────────────────────────────────────────────────────
# CSV EXPORT — the engine's numbers, in a spreadsheet
# ─────────────────────────────────────────────────────────────────────────────
# Deliberately NOT a dump of the table's cells. A table cell is a rendered
# judgment — "$328 – $334 · 200 MA · -4.9%" is one string carrying four
# facts, and pasted into a spreadsheet it is four facts you cannot sort on.
# So the export has its own field list, one scalar per field, and a cell that
# stacks two numbers becomes two columns.
#
# The values come from screen.flatten() wherever it already computes them.
# That module exists precisely to turn one nested result into scalars for the
# rule engine, and a second flattener here would be the one that goes stale
# the next time a score is renamed. Only what flatten has no reason to carry
# — the sized plan's own prices, the zone's upper bound, today's proximity —
# is extracted below.

# (key, header, group, on-by-default). The default set is the answer to
# "what did the engine conclude and at what price", which is what a CSV of
# this page is nearly always for; everything else is one checkbox away.
_CSV_FIELDS = (
    ("ticker", "Ticker", "Identity", True),
    ("name", "Company", "Identity", True),
    ("lt_sector", "Sector", "Identity", True),
    ("lt_price", "Price", "Identity", True),
    ("regime", "Market regime", "Identity", False),

    ("zone_low", "Buy zone low", "Buy zone", True),
    ("zone_high", "Buy zone high", "Buy zone", True),
    ("zone_label", "Buy zone level", "Buy zone", True),
    ("zone_kind", "Buy zone kind", "Buy zone", True),
    ("zone_state", "Zone proximity", "Buy zone", True),
    ("zone_gap_pct", "Gap to zone %", "Buy zone", True),
    ("buy_zone_reached", "Price in zone", "Buy zone", False),
    ("buy_verdict", "Buy-zone verdict", "Buy zone", False),
    ("buy_zone_tier", "Buy-zone tier", "Buy zone", False),
    ("buy_zone_confidence", "Buy-zone confidence", "Buy zone", False),
    ("buy_below", "Buy below", "Buy zone", False),
    ("expected_cagr", "Expected CAGR %", "Buy zone", False),
    ("return_hurdle", "Return hurdle %", "Buy zone", False),
    ("model_value", "Model value", "Buy zone", False),

    ("lquality", "LQuality", "Quality", True),
    ("lq_tier", "Quality tier", "Quality", True),
    ("lq_coverage", "Quality coverage %", "Quality", False),
    ("insider", "Insider signal", "Quality", False),

    ("valuation_band", "Valuation band", "Valuation", True),
    ("valuation_method", "Valuation method", "Valuation", False),
    ("valuation_confidence", "Valuation confidence", "Valuation", False),
    ("implied_growth", "Implied growth %", "Valuation", False),
    ("delivered_growth", "Delivered growth %", "Valuation", False),
    ("growth_gap", "Growth gap pp", "Valuation", False),
    ("upside", "Upside to fair value %", "Valuation", False),

    ("trend_state", "Trend state", "Trend & levels", True),
    ("trend_score", "Trend score", "Trend & levels", False),
    ("stage", "Pullback stage", "Trend & levels", True),
    ("s1_price", "S1 support", "Trend & levels", False),
    ("s1_distance", "S1 distance %", "Trend & levels", False),
    ("r1_price", "R1 resistance", "Trend & levels", False),
    ("r1_distance", "R1 distance %", "Trend & levels", False),
    ("cluster", "Level cluster", "Trend & levels", False),
    ("confluence_hits", "Support levels holding", "Trend & levels", False),
    ("lt_pct_vs_50ma", "vs 50 MA %", "Trend & levels", False),
    ("lt_pct_vs_200ma", "vs 200 MA %", "Trend & levels", False),

    ("technical", "Technical score", "Timing", False),
    ("entry_score", "Entry score", "Timing", True),
    ("swing_score", "Swing score", "Timing", False),
    ("swing_state", "Swing state", "Timing", False),
    ("swing_setup", "Swing setup", "Timing", False),
    ("lt_rs_rank", "RS rank", "Timing", False),
    ("lt_days_to_earnings", "Days to earnings", "Timing", False),

    ("action", "Action", "Verdict", True),
    ("gate", "Stopped at gate", "Verdict", False),
    ("investment_status", "Investment status", "Verdict", False),
    ("lt_score", "LT score", "Verdict", False),
    ("tranche", "Tranche % of target", "Verdict", False),

    ("entry_price", "LT entry", "Position", True),
    ("stop_price", "Stop", "Position", True),
    ("stop_pct", "Stop distance %", "Position", False),
    ("shares", "Shares", "Position", True),
    ("position_value", "Position $", "Position", True),
    ("risk_dollars", "Risk $", "Position", True),
    ("allocation_pct", "Allocation %", "Position", False),
    ("rr", "R:R", "Position", True),
    ("risk_status", "Sizing status", "Position", False),
)

_CSV_GROUPS = ("Identity", "Buy zone", "Quality", "Valuation",
               "Trend & levels", "Timing", "Verdict", "Position")


def _csv_row(r) -> dict:
    """One evaluated result -> one flat dict keyed by _CSV_FIELDS."""
    from stockanalysis.core.longterm import screen as LS

    flat = LS.flatten(r)
    zone = ((r.get("buy_zones") or {}).get("display_zone")) or {}
    prox = _zone_flag(r) or {}
    plan = r.get("sizing_plan") or {}
    sizing = plan.get("sizing") or {}

    out = {k: flat.get(k) for k, _h, _g, _d in _CSV_FIELDS}
    out.update({
        "name": r.get("name"),
        "regime": r.get("regime"),
        "zone_low": zone.get("low"),
        "zone_high": zone.get("high"),
        "zone_label": zone.get("label"),
        # "investment" / "technical" — the distinction the column's colour
        # carries and a CSV otherwise loses entirely.
        "zone_kind": zone.get("kind"),
        # Blank rather than a state word when there is no zone to be near:
        # "ABOVE" against an empty zone would be a claim about nothing.
        "zone_state": prox.get("state"),
        "zone_gap_pct": prox.get("gap_pct"),
        "entry_score": (r.get("entry") or {}).get("score"),
        # Only from a plan the engine actually sized. A pending plan's prices
        # are real, but shares/risk on an unsized one are absent, and half a
        # row of position figures is worse than none.
        "entry_price": (plan.get("entry") or {}).get("price"),
        "stop_price": (plan.get("stop") or {}).get("price"),
        "shares": sizing.get("shares"),
        "position_value": sizing.get("position_value"),
        "risk_dollars": sizing.get("actual_risk"),
    })
    return {k: v for k, v in out.items()
            if k in {f[0] for f in _CSV_FIELDS}}


def _csv_scalar(v):
    """One value, small. Floats arrive off numpy and pandas arithmetic
    carrying fifteen digits — 91.40000000000001 is both wrong-looking in a
    spreadsheet and, multiplied by 62 fields and 657 rows, most of the
    payload. Four decimals is more precision than any field here has."""
    if isinstance(v, float):
        return None if v != v else round(v, 4)
    if v is None or isinstance(v, (int, bool, str)):
        return v
    return str(v)


def _csv_payload(rows) -> str:
    """Field spec + one record per row, as JSON the picker's JS reads.

    Emitted for every row the filters left, not only the page's first
    `limit` — a download that silently stopped at row 60 would be the same
    bug the ticker export was written to avoid, and harder to notice in a
    spreadsheet than on screen.

    Rows are ARRAYS positioned against `fields`, not objects. As objects the
    unfiltered page carried 942KB, of which roughly two thirds was the same
    62 key names repeated 657 times — a cost paid on every page load by
    everyone, including the majority who never open the export.
    """
    import json as _json

    keys = [k for k, _h, _g, _d in _CSV_FIELDS]
    payload = {
        "fields": [{"key": k, "label": h, "group": g, "default": d}
                   for k, h, g, d in _CSV_FIELDS],
        "groups": list(_CSV_GROUPS),
        "rows": [[_csv_scalar(row.get(k)) for k in keys]
                 for row in (_csv_row(r) for r in rows)],
    }
    # </script> inside a JSON string would close this block early. The rest
    # is belt-and-braces against an HTML comment opener doing the same.
    text = _json.dumps(payload, default=str,
                       separators=(",", ":")).replace("</", "<\\/")
    return (f'<script type="application/json" id="lt-csv-data">{text}'
            f'</script>')


def _csv_bar(n_rows: int, filtered: bool) -> str:
    """The download button and its column picker.

    The picker is server-rendered rather than built in JS so that the field
    list has exactly one definition — _CSV_FIELDS above — instead of a Python
    copy for the payload and a JavaScript copy for the checkboxes.
    """
    if not n_rows:
        return ""
    boxes = []
    for group in _CSV_GROUPS:
        items = [(k, h, d) for k, h, g, d in _CSV_FIELDS if g == group]
        if not items:
            continue
        checks = "".join(
            f'<label style="display:inline-flex;gap:5px;align-items:center;'
            f'font-size:11px;color:#444441;white-space:nowrap;cursor:pointer">'
            f'<input type="checkbox" class="lt-csv-col" value="{esc(k)}"'
            f'{" checked" if default else ""} data-default='
            f'"{"1" if default else "0"}">{esc(label)}</label>'
            for k, label, default in items)
        boxes.append(
            f'<div style="display:flex;gap:10px;flex-wrap:wrap;'
            f'align-items:center;margin-bottom:5px">'
            f'<span style="font-size:10px;text-transform:uppercase;'
            f'letter-spacing:.06em;color:#898781;min-width:104px">'
            f'{esc(group)}</span>{checks}</div>')

    scope = ("the rows these filters left" if filtered
             else "every name the engine scored")
    return (
        f'<details id="lt-csv" style="margin:-6px 0 14px">'
        f'<summary style="cursor:pointer;font-size:11px;font-weight:600;'
        f'color:#185FA5;list-style:none">⬇ Download CSV — choose columns</summary>'
        f'<div style="background:white;border:0.5px solid #e1e0d9;'
        f'border-radius:12px;padding:12px 14px;margin-top:8px">'
        f'{"".join(boxes)}'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
        f'margin-top:10px;padding-top:10px;border-top:0.5px solid #f1efea">'
        f'<button type="button" class="btn" onclick="ltCsvDownload()" '
        f'style="padding:6px 14px;font-size:11px">Download '
        f'<span id="lt-csv-count">{n_rows}</span> rows</button>'
        f'<a href="#" onclick="ltCsvSelect(1);return false" '
        f'style="font-size:11px;color:#185FA5">All</a>'
        f'<a href="#" onclick="ltCsvSelect(0);return false" '
        f'style="font-size:11px;color:#185FA5">None</a>'
        f'<a href="#" onclick="ltCsvSelect(-1);return false" '
        f'style="font-size:11px;color:#185FA5">Reset</a>'
        f'<span id="lt-csv-note" style="font-size:10px;color:#898781"></span>'
        f'</div>'
        f'<div style="font-size:10px;color:#898781;margin-top:8px">'
        f'{esc(n_rows)} rows — {esc(scope)}, not just the page you can see. '
        f'Rows come out in the order the table is currently sorted, and '
        f'columns in the order they are listed here. Your choice of columns '
        f'is remembered in this browser.</div>'
        f'</div></details>')


# ─────────────────────────────────────────────────────────────────────────────
# CSP HAND-OFF — send a chosen set of names to the put scan
# ─────────────────────────────────────────────────────────────────────────────
# The two pages already share a spine: /csp consumes /longterm's company
# verdict and adds the options layer. What was missing is the step between
# them — you could see that twenty quality names had pulled back, and then
# had to retype them into the CSP box.
#
# Why the selection is made HERE and not on /csp: choosing which businesses
# you would accept being assigned into is a question about quality,
# valuation and how far price has fallen, and those three columns live on
# this page. /csp cannot show them, because it consumes the verdict rather
# than recomputing it.
#
# The scan is a job (minutes of option chains), so this starts one and hands
# back a link. It does not wait, and it does not pretend to.

# The scan's own per-survivor cap. A selection larger than this would have
# its tail silently dropped after the chains had already been paid for.
MAX_CSP_SELECTION = 60


def _csp_bar() -> str:
    """The bar that appears once something is ticked.

    Rendered always and hidden by CSS until a box is checked — building it
    in JS would put the copy explaining what the scan does inside a string
    literal, where nobody edits it.
    """
    def btn(label, onclick, primary=False, ident=""):
        bg, fg, bd = (("#0b0b0b", "white", "#0b0b0b") if primary
                      else ("white", "#444441", "#d9d7ce"))
        return (f'<button type="button"{f" id={ident}" if ident else ""} '
                f'onclick="{onclick}" style="background:{bg};color:{fg};'
                f'border:1px solid {bd};font-size:11px;font-weight:600;'
                f'padding:6px 12px;border-radius:6px;cursor:pointer;'
                f'white-space:nowrap">{label}</button>')

    return (
        f'<div id="lt-csp-bar" style="display:none;position:sticky;bottom:0;'
        f'z-index:5;background:#0b0b0b;color:white;border-radius:12px;'
        f'padding:10px 14px;margin:10px 0 14px;box-shadow:0 2px 14px '
        f'rgba(0,0,0,.18)">'
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;'
        f'align-items:center">'
        f'<span id="lt-csp-count" style="font-size:12px;font-weight:700"></span>'
        f'<span id="lt-csp-warn" style="font-size:11px;color:#F0C674"></span>'
        + btn("Select all shown", "ltPickAll(1)")
        + btn("Only CSP-eligible", "ltPickEligible()")
        + btn("Clear", "ltPickAll(0)")
        + f'<label style="font-size:11px;display:inline-flex;gap:5px;'
          f'align-items:center;margin-left:6px">Target DTE'
          f'<input id="lt-csp-dte" type="number" min="7" max="120" value="35" '
          f'style="width:58px;padding:4px 6px;font-size:11px;border-radius:5px;'
          f'border:1px solid #4a4a48;background:#1c1c1b;color:white"></label>'
        + f'<label style="font-size:11px;display:inline-flex;gap:5px;'
          f'align-items:center" title="AVOID skips any expiry containing an '
          f'earnings print; a LATER expiry never clears one">Earnings'
          f'<select id="lt-csp-earn" style="padding:4px 6px;font-size:11px;'
          f'border-radius:5px;border:1px solid #4a4a48;background:#1c1c1b;'
          f'color:white">'
          f'<option value="AVOID">avoid</option>'
          f'<option value="CONTROLLED">controlled</option>'
          f'<option value="ACCEPT">accept</option></select></label>'
        + '<span style="margin-left:auto;display:flex;gap:8px;'
          'align-items:center">'
        + btn("🪙 Scan for put premiums →", "ltPickScan()", primary=True,
              ident="lt-csp-go")
        + '</span></div>'
        f'<div id="lt-csp-msg" style="font-size:11px;color:#c9c7c1;'
        f'margin-top:7px"></div>'
        f'<div style="font-size:10px;color:#8d8b86;margin-top:5px">'
        f'Starts a cash-secured put scan on exactly these names — one option '
        f'chain each, so this takes minutes rather than seconds. Because you '
        f'named them, <b>every one gets priced, including the rejects</b>: '
        f'a name that fails on quality, valuation or a broken trend comes '
        f'back with its chain attached as reference, so you can read the '
        f'premium and weigh the risk yourself. What it does not get is a '
        f'score or a place in the ranked list — the gate still decides what '
        f'is offered, you still decide what is taken. Premiums are judged '
        f'against a required yield, so "attractive" is the engine\'s verdict '
        f'rather than the biggest number.<br>'
        f'<b>Earnings</b>: <i>avoid</i> skips any expiry containing a print, '
        f'which is why a name reporting soon can come back with no contract '
        f'at all — switch to <i>controlled</i> to see it with a scoring '
        f'penalty, or <i>accept</i> to take it unpenalised when quality, '
        f'delta, liquidity and premium are all strong enough to be paid for '
        f'the gap.</div>'
        f'</div>')


# Above this, a "Scan these" deep link stops being the right tool: the URL
# gets long, and a rescan of that many names is a universe scan, which the
# Scanner's own category checkboxes already do better. Copy still works at
# any size — only the one-click link is withheld, and the bar says why.
MAX_DEEP_LINK_TICKERS = 200


def _export_bar(matching: list[str], library: list[str], filtered: bool) -> str:
    """Hand the current selection back as a rescan input.

    Two lists, always both offered. "Filtered" is whatever the chips, rules,
    search and saved list have left; "whole library" is every name the engine
    scored. They are the same set on an unfiltered page, and the bar collapses
    to one button rather than offering the same thing twice under two names.

    The textarea is not decoration. navigator.clipboard is unavailable over
    plain http from another machine on the LAN — which is how this
    workstation is often reached — so the copy has to degrade to something
    the reader can select by hand rather than failing silently.
    """
    if not matching and not library:
        return ""
    same = matching == library
    csv_matching = ", ".join(matching)
    csv_library = ", ".join(library)

    def button(label, target, count, primary):
        bg, fg, bd = (("#0b0b0b", "white", "#0b0b0b") if primary
                      else ("white", "#444441", "#d9d7ce"))
        return (f'<button type="button" onclick="ltCopy(\'{target}\')" '
                f'style="background:{bg};color:{fg};border:1px solid {bd};'
                f'font-size:11px;font-weight:600;padding:5px 11px;'
                f'border-radius:6px;cursor:pointer;white-space:nowrap">'
                f'{esc(label)} ({count})</button>')

    buttons = [button("📋 Copy filtered" if not same else "📋 Copy tickers",
                      "matching", len(matching), True)]
    if not same:
        buttons.append(button("📋 Whole library", "library", len(library),
                              False))

    # One click straight into a scan of exactly this set. Withheld rather
    # than truncated past the cap — a link that silently scanned the first
    # 200 of 552 would be worse than no link.
    scan = ""
    if matching and len(matching) <= MAX_DEEP_LINK_TICKERS:
        href = "/scanner?" + urlencode({"tickers": csv_matching})
        scan = (f'<a href="{esc(href)}" '
                f'style="font-size:11px;font-weight:600;padding:5px 11px;'
                f'border-radius:6px;text-decoration:none;color:#185FA5;'
                f'border:1px solid #d9d7ce;white-space:nowrap">'
                f'Rescan these {len(matching)} →</a>')
    elif matching:
        scan = (f'<span style="font-size:10px;color:#898781">'
                f'{len(matching)} is past the {MAX_DEEP_LINK_TICKERS}-name '
                f'deep-link cap — copy and paste into the Scanner, or tick a '
                f'category there.</span>')

    note = ("the rows these filters left, not just the page you can see"
            if filtered else "every name the engine scored")

    return (
        f'<div id="lt-export" data-matching="{esc(csv_matching)}" '
        f'data-library="{esc(csv_library)}" '
        f'style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;'
        f'margin:-6px 0 14px">'
        + "".join(buttons) + scan
        + f'<span id="lt-export-note" style="font-size:10px;color:#898781">'
          f'{esc(note)}</span>'
        + '<textarea id="lt-export-box" readonly rows="2" '
          'style="display:none;width:100%;margin-top:6px;padding:7px 9px;'
          'border:1px solid #d9d7ce;border-radius:7px;font-size:11px;'
          'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
          'resize:vertical"></textarea>'
        + '</div>')


def longterm_page(query: dict | None = None) -> tuple[str, str]:
    from . import api

    query = query or {}
    action_filter = (query.get("action") or [""])[0].strip()
    regime_override = (query.get("regime") or [""])[0].strip() or None
    raw_query = (query.get("q") or [""])[0]
    list_name = (query.get("list") or [""])[0].strip()
    rule_texts = [r for r in (query.get("rule") or []) if r.strip()]
    rule_op = ((query.get("rule_op") or ["AND"])[0].strip().upper()
               or "AND")
    # The buy-zone alert's own filter. A separate parameter rather than a
    # rule, because it is a property of TODAY's price against a stored level
    # and the rule set is a description of the company — mixing them would
    # make a saved rule URL mean something different next week.
    #
    # Two modes, because the bar makes two different claims: "1" is the names
    # it alerted on, "all" is every name at a zone including the ones whose
    # business it rejected. Without the second the bar would cite a number
    # the page had no way to show.
    raw_near = (query.get("near") or [""])[0].strip().lower()
    near_mode = ("all" if raw_near == "all"
                 else "1" if raw_near in ("1", "true", "on") else "")
    near_on = bool(near_mode)
    wanted = parse_tickers(raw_query)
    # Default FIRST, clamp second. Clamping an absent value ran
    # max(10, min(500, 0)) -> 10, so `or DEFAULT_LIMIT` could never fire and
    # the page quietly showed ten rows instead of sixty.
    raw_limit = (query.get("limit") or [""])[0].strip()
    try:
        limit = int(raw_limit) if raw_limit else DEFAULT_LIMIT
    except ValueError:
        limit = DEFAULT_LIMIT
    limit = max(10, min(500, limit))

    data = api.longterm(regime_override)
    rows, cov = data["rows"], data["coverage"]
    # A result set that arrived without sizing (an older cache, a caller that
    # evaluated the engine directly) still renders: the sizing cells degrade
    # to dashes and the risk card shows its empty state. Sizing is a lens on
    # the verdict, and losing the lens should not lose the verdict.
    settings = data.get("settings") or PS.normalize_settings()

    def link(**params):
        """A URL carrying the page's whole state, so no control silently
        discards another's — clicking a chip while a search is active used
        to drop the search, and now must not drop the rules either."""
        state = {"q": raw_query.strip(), "list": list_name,
                 "action": action_filter,
                 "regime": regime_override or "",
                 "near": near_mode,
                 "rule": list(rule_texts),
                 "rule_op": "" if rule_op == "AND" else rule_op}
        for key, value in params.items():
            state[key] = value if isinstance(value, list) else (
                "" if value is None else str(value))
        kept = {k: v for k, v in state.items() if v}
        # doseq: `rule` repeats once per condition.
        return "/longterm" + (f"?{urlencode(kept, doseq=True)}" if kept else "")

    # ── ticker search ───────────────────────────────────────────────────────
    # Narrows the universe before anything else looks at it, so the chips
    # below count within the search rather than across the whole library.
    # Counting globally would offer "Avoid 347" next to three searched
    # tickers and return an empty table when clicked.
    by_ticker = {r["ticker"]: r for r in rows}

    # A saved list narrows the universe before anything else looks at it.
    # Tickers on the list that no scan has covered are NAMED rather than
    # quietly dropped — the same rule the ticker search follows, and the
    # reason a 25-name list showing 19 rows is legible instead of alarming.
    lists = api.longterm_lists()
    list_missing = []
    if list_name and list_name in lists:
        members = lists[list_name]
        rows = [by_ticker[t] for t in members if t in by_ticker]
        list_missing = [t for t in members if t not in by_ticker]
        by_ticker = {r["ticker"]: r for r in rows}

    missing = []
    if wanted:
        found = []
        for t in wanted:
            (found.append(by_ticker[t]) if t in by_ticker else missing.append(t))
        # Input order, not engine ranking: someone who typed a list is
        # comparing those names against each other and expects to read them
        # back in the order they wrote them.
        base = found
    else:
        base = rows

    # Rules run after the ticker search and before the action chips, so the
    # chip counts describe what the rules actually left.
    from stockanalysis.core.longterm import screen as LS
    # Counted over the search-narrowed universe, which is what clicking a
    # preset will actually screen — presets replace the rules but keep `q`.
    preset_counts = LS.preset_counts(base)
    before_rules = len(base)
    base, rule_conds, rule_stats = LS.apply_rules(base, rule_texts, rule_op)

    # Built from what the rules left, BEFORE ?near= narrows it — otherwise
    # turning the filter on would rewrite the alert that offered it, and the
    # bar would always read "N at a zone, all N shown" whatever the truth.
    zone_bar = _zone_alert_bar(base, link, near_mode)
    n_near = len(zone_alerts(base))
    if near_mode == "all":
        base = [r for r in base if (_zone_flag(r) or {}).get("near")]
    elif near_mode:
        base = [r for r, _p in zone_alerts(base)]

    counts = {}
    for r in base:
        counts[r["action"]] = counts.get(r["action"], 0) + 1

    # ── the banner that stops this page looking broken ──────────────────────
    # A library scanned before core.longterm existed has none of the columns
    # the trend gate and the confirmation check read, so every name legitimately
    # lands on WATCH. Without saying so, the page reads as an engine that
    # cannot find anything rather than one that has not been fed.
    warn = ""
    if cov["needs_rescan"]:
        warn = (
            f'<div style="background:#FAEEDA;border:0.5px solid #e8d5aa;'
            f'border-radius:10px;padding:12px 14px;margin-bottom:14px;'
            f'font-size:12px;color:#633806">'
            f'<strong>This library predates the engine.</strong> '
            f'Moving-average slopes are present on {cov["ma_slope"]} of '
            f'{cov["total"]} tickers, reversal candles on {cov["reversal"]}, '
            f'and multi-year statements on {cov["statements"]}. Until a scan '
            f'repopulates them the trend gate reads UNKNOWN and no name can '
            f'reach BUY NOW — the engine is refusing to confirm an entry it '
            f'cannot see, not failing to find one. Run a scan from the '
            f'<a href="/scanner" style="color:#633806">Scanner</a> page.</div>')

    # ── search bar ──────────────────────────────────────────────────────────
    # A plain GET form: the result stays in the URL, so a comparison of five
    # names is a link that can be bookmarked or pasted, and the back button
    # behaves.
    search = f"""
<form method="get" action="/longterm"
      style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;
             margin-bottom:12px">
  <input name="q" value="{esc(raw_query.strip())}" autocomplete="off"
         placeholder="Look up tickers — NVDA, or MSFT, AAPL, GOOGL"
         style="flex:1;min-width:260px;max-width:460px;padding:9px 12px;
                font-size:13px;border:0.5px solid #e1e0d9;border-radius:8px">
  {f'<input type="hidden" name="regime" value="{esc(regime_override)}">'
   if regime_override else ''}
  <select name="list" style="padding:9px 10px;font-size:12px;
          border:0.5px solid #e1e0d9;border-radius:8px;max-width:220px">
    {_list_options(list_name)}
  </select>
  <button type="submit" class="btn" style="padding:9px 16px">Search</button>
  {f'<a href="{esc(link(q="", action="", limit=""))}" '
   f'style="font-size:12px;color:#185FA5">Clear</a>' if wanted else ''}
</form>"""

    notfound = ""
    if list_missing:
        shown = ", ".join(list_missing[:12])
        notfound += (
            f'<div style="background:#F1EFE8;border-radius:8px;padding:9px 12px;'
            f'margin-bottom:12px;font-size:12px;color:#444441">'
            f'<strong>{esc(list_name)}</strong> — {len(list_missing)} of its '
            f'tickers have no research page yet: {esc(shown)}'
            + ("…" if len(list_missing) > 12 else "")
            + f'. Scan them from the '
              f'<a href="/scanner" style="color:#185FA5">Scanner</a> page.</div>')
    if missing:
        notfound = (
            f'<div style="background:#F1EFE8;border-radius:8px;padding:9px 12px;'
            f'margin-bottom:12px;font-size:12px;color:#444441">'
            f'Not in the research library: '
            f'<strong>{esc(", ".join(missing))}</strong> — the engine only '
            f'scores tickers a scan has already covered. Add them from the '
            f'<a href="/scanner" style="color:#185FA5">Scanner</a> page and '
            f'they will appear here.</div>')

    # ── filter chips ────────────────────────────────────────────────────────
    def chip(label, value, count):
        on = action_filter == value
        bg, fg = ("#0b0b0b", "white") if on else ("#F1EFE8", "#444441")
        return (f'<a href="{esc(link(action="" if on else value, limit=""))}" '
                f'style="background:{bg};color:{fg};'
                f'font-size:11px;font-weight:600;padding:5px 11px;'
                f'border-radius:6px;text-decoration:none;white-space:nowrap">'
                f'{esc(label)} {count}</a>')

    buy_total = sum(counts.get(a, 0) for a in _BUY_ACTIONS)
    chips = [chip("All", "", len(base))]
    if buy_total or not wanted:
        chips.append(chip("Buy-ready", "BUY", buy_total))
    for action in _BUY_ACTIONS + ("WATCH", "WAIT", "AVOID"):
        if counts.get(action):
            chips.append(chip(_ACTION_SHORT.get(action, action), action,
                              counts[action]))

    # Sits with the action chips because it filters the same table, but
    # coloured apart from them because it asks a different question: those
    # select on the engine's verdict, this one on where price is today. A
    # name can be AVOID and at its zone, and both chips are then true of it.
    if n_near or near_on:
        nz_bg, nz_fg = (("#633806", "white") if near_on
                        else ("#FAEEDA", "#633806"))
        nz_label = ("⚑ At buy zone · all" if near_mode == "all"
                    else f'⚑ At buy zone {n_near}')
        chips.append(
            f'<a href="{esc(link(near="" if near_on else "1", limit=""))}" '
            f'title="Price is inside, or within {BZ.NEAR_ZONE_PCT:.0f}% of, '
            f'the zone in the Buy Zone column, on a business the engine has '
            f'not rejected" '
            f'style="background:{nz_bg};color:{nz_fg};font-size:11px;'
            f'font-weight:600;padding:5px 11px;border-radius:6px;'
            f'text-decoration:none;white-space:nowrap">{nz_label}</a>')

    shown = base
    if action_filter == "BUY":
        shown = [r for r in base if r["action"] in _BUY_ACTIONS]
    elif action_filter:
        shown = [r for r in base if r["action"] == action_filter]
    total_matching = len(shown)
    # Captured HERE, before the `[:limit]` truncation below. The export is a
    # rescan input, so it has to be everything the filters left rather than
    # everything the page happens to be showing — a filter matching 51 names
    # under a 60-row page looks identical either way, and a filter matching
    # 300 would silently hand back 60 and rescan the wrong set.
    matching_tickers = [r["ticker"] for r in shown]
    library_tickers = [r["ticker"] for r in data["rows"]]
    # Same rule for the CSV, and for the same reason: a download that stopped
    # at the page's row limit would be wrong in a file, where there is no
    # "Show all 300" link underneath to reveal that it had.
    csv_source = list(shown)
    # Aggregated over everything the filters left, not just the first page —
    # a concentration figure that changed when you clicked "show all" would
    # be describing the pagination rather than the portfolio.
    risk_summary = PS.portfolio_summary(shown, settings)
    # An explicit search or a chosen list is never truncated — asking for
    # eight tickers, or for "daytrade", and being shown the first six
    # silently would be worse than a long page.
    shown = shown if (wanted or list_name) else shown[:limit]

    # One result means the reasoning IS the answer, so don't make the user
    # click again to reach it.
    empty_msg = ("Nothing matches this filter."
                 if not wanted else
                 f"None of those {len(base)} tickers are "
                 f"{_ACTION_SHORT.get(action_filter, action_filter)}.")

    more = ""
    if total_matching > len(shown):
        more = (f'<div style="text-align:center;padding:12px">'
                f'<a href="{esc(link(limit=total_matching))}" '
                f'style="font-size:12px;color:#185FA5">'
                f'Show all {total_matching}</a></div>')

    regime_pill = {"FAVORABLE": "good", "SELECTIVE": "watch",
                   "DEFENSIVE": "bad"}.get(data["regime"], "muted")

    is_filtered = bool(action_filter or rule_texts or wanted or list_name
                       or near_on)
    export = _export_bar(matching_tickers, library_tickers,
                         filtered=is_filtered)
    csv_bar = (_csv_bar(len(csv_source), is_filtered)
               + _csv_payload(csv_source))

    table = analysis_table(shown, open_detail=len(shown) == 1,
                           empty_msg=empty_msg, selectable=True) + more

    body = f"""
<div style="display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap;
            margin-bottom:12px">
  <div>
    <h2 style="margin:0 0 3px;font-size:19px">Long-Term Buy Engine</h2>
    <div style="font-size:11px;color:#898781;max-width:640px">
      Business quality → valuation → long-term trend → support → entry
      trigger, in that order. A company that fails an earlier gate cannot be
      rescued by a later one, so the chart never compensates for the
      business.
    </div>
  </div>
  <div style="margin-left:auto;text-align:right">
    {badge(data["regime"], regime_pill)}
    <div style="font-size:10px;color:#898781;margin-top:4px">
      {esc(data["regime_note"])}<br>{esc(data["risk_free_note"])}
    </div>
  </div>
</div>

{warn}
{zone_bar}
{_risk_dashboard(risk_summary, settings)}
{search}
{notfound}
{_preset_bar(link, rule_texts, cov["needs_rescan"], preset_counts)}
{_rule_builder(rule_conds, rule_op, link, rule_stats, len(base),
               before_rules, raw_query)}

<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;
            margin-bottom:14px">
  {"".join(chips)}
</div>

{export}
{csv_bar}

<div id="lt-sort-note" style="display:none;font-size:11px;color:#898781;
     margin:-4px 0 8px"></div>

{card("", table, pad="6px 10px 10px")}
{_csp_bar()}

<div style="font-size:10px;color:#898781;margin-top:-6px">
  Click a header to sort; <strong>shift-click a second header</strong> to
  sort by both — e.g. LQuality then Action. Drag a header to reorder, or drag
  its <strong>right edge</strong> to set a column's width (double-click that
  edge to give the column its natural width back). Order and widths persist
  in this browser
  (<a href="#" id="lt-reset-cols" style="color:#185FA5">reset columns</a>).
  S1 · Support is the price to work an order at and R1 · Resistance the
  first thing overhead; both are green/red and bold when the market has
  actually traded there, and grey italic when the figure is only the nearest
  moving average or the 52-week high — a derived level, not one anyone has
  defended. The four MA columns show how far price sits above or below each,
  green when the level is holding underfoot and red when it was lost.
  <br>
  LQuality is this engine's own 100-point score and is deliberately not the
  same number as the Screener's Quality — they are calibrated for different
  questions. Valuation prices most companies by reverse DCF (what growth the
  current price already requires) and falls back to a sector multiple for
  financials, REITs and anything without positive free cash flow; the method
  is shown per row. Moat and capital-allocation factors are quantitative
  proxies, marked ⚠ where the evidence is thinner than the weight.
</div>
"""
    return body, TABLE_JS + _RULE_JS + RISK_JS + EXPORT_JS


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD PANEL — the same analysis, for one scope, without leaving home
# ─────────────────────────────────────────────────────────────────────────────

def analysis_panel(data: dict) -> str:
    """The Scan & Analyze result for one scope, as an HTML fragment.

    Returned as HTML rather than JSON for the same reason /api/regime is: the
    caller is a div on the Dashboard, not a client with its own opinion about
    how a valuation band should look. Shipping rows as JSON would mean
    reimplementing fifteen cell renderers and the reasoning grid in
    JavaScript, and the two copies would disagree the first time a column
    changed.
    """
    if data.get("error"):
        return (f'<div style="background:#FCEBEB;color:#791F1F;font-size:12px;'
                f'padding:10px 13px;border-radius:9px">'
                f'{esc(data["error"])}</div>')

    rows = data.get("rows") or []
    missing = data.get("missing") or []
    matched = data.get("matched", len(rows))
    regime_pill = {"FAVORABLE": "good", "SELECTIVE": "watch",
                   "DEFENSIVE": "bad"}.get(data.get("regime"), "muted")

    # "60 of 552 scored" would be a lie — all 552 were scored, 60 are shown.
    count = (f"{matched} name(s) scored" if len(rows) == matched
             else f"showing {len(rows)} of {matched} scored")
    head = (
        f'<div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;'
        f'margin-bottom:10px">'
        f'<div style="font-size:13px;font-weight:600">{esc(data.get("label") or "")}</div>'
        f'<div style="font-size:11px;color:#898781">{esc(count)}'
        + (f' · {len(missing)} not yet scanned' if missing else '')
        + f'</div>'
        f'<div style="margin-left:auto">{badge(data.get("regime", "—"), regime_pill)}</div>'
        f'</div>')

    # Named, not silently dropped: a ticker the engine has never scored looks
    # identical to one it scored badly if it simply isn't in the table, and
    # the fix — run the scan above — is only obvious once it's said.
    notes = ""
    if missing:
        shown = ", ".join(missing[:12]) + ("…" if len(missing) > 12 else "")
        notes += (
            f'<div style="background:#FAEEDA;border:0.5px solid #f0dfc0;'
            f'color:#633806;font-size:11px;padding:9px 12px;border-radius:9px;'
            f'margin-bottom:10px">'
            f'<b>{len(missing)}</b> ticker(s) have no research page yet — the '
            f'engine only scores what a scan has covered: {esc(shown)}. '
            f'Run <b>Scan &amp; refresh research</b> above and they will '
            f'appear here.</div>')
    if matched > len(rows):
        notes += (
            f'<div style="font-size:11px;color:#898781;margin-bottom:10px">'
            f'Capped at the first {len(rows)} — each row carries its full '
            f'reasoning grid, so all {matched} at once is several megabytes of '
            f'page. Narrow the scope, or '
            f'<a href="{esc(data.get("full_link") or "/longterm")}">'
            f'open the Long-Term page →</a></div>')

    return head + notes + analysis_table(
        rows, open_detail=len(rows) == 1,
        empty_msg="Nothing to analyse in this scope yet — run a scan first.")
