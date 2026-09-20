"""
stockdaytrade_view.py
=====================
The StockDayTrade page. Presentation only: every score, gate, level
and trade plan comes from core.daytrade and is read back out of the
snapshot core.daytrade.store wrote. Nothing here recomputes a number, so
each row's expanded panel can claim to show "why" and be right — the ✓/✗
confirmations, the gate a name stopped at, and the entry/stop/target prices
are the engine's own output rather than a second implementation of §12 in
HTML.

Why this page renders a snapshot instead of scanning
-----------------------------------------------------
The scan screens the whole market, then pulls bars, `.info` and news per
candidate — one to two minutes, well past any request timeout. So the page
shows the last snapshot and the Run button starts a background job that
replaces it, exactly as Scanner and AI Sentiment already work. The header
always states which session the snapshot is of and how old it is, because
a stale intraday page is far more dangerous than a stale long-term one: a
VWAP from two hours ago looks identical to a live one and is worthless.

Layout follows §16 — one row per candidate, the ranking columns in the
spec's order, the action last — and the three scores are kept in separate
columns rather than blended. A name with a 90 setup and a 45 tradeability
and a name with 70/70 both average around 68, and the first is a trap the
second is not. §10 says a good setup must not compensate for bad
execution, so the table must not let it visually either.
"""

from __future__ import annotations

from datetime import datetime

from stockanalysis.core.daytrade import profiles as PR
from stockanalysis.core.daytrade.engine import ACTION_RANK

from .views import badge, card, empty, esc, tv_url

DASH = "—"

# Grade → the shared badge palette. A+/A are the only ones that mean "take
# it"; B+ is explicitly "watch for confirmation" per §17, so it must not
# read as green.
_GRADE_STATUS = {"A+": "good", "A": "good", "B+": "watch",
                 "B": "muted", "C": "muted", "NO TRADE": "bad"}

# §17's six decision labels already carry their own emoji from the engine.
_DECISION_STATUS = {
    "🔥 A+ LONG": "good", "🟢 A LONG": "good", "🔴 SHORT": "bad",
    "🟡 WATCH FOR CONFIRMATION": "watch", "⚪ WATCHLIST": "muted",
    "⛔ NO TRADE": "bad", "⚪ WATCH — NOT TRADEABLE": "bad",
}

# The action taxonomy. Only ENTER NOW is green — "wait" is a decision, not
# a success, and colouring it like one is how a scanner talks somebody into
# a trade it just told them to wait on.
_ACTION_STATUS = {
    "🔥 ENTER NOW": "good",
    "🟢 WAIT FOR BREAKOUT": "info",
    "🟢 WAIT FOR PULLBACK": "info",
    "🟡 SETUP OK — WAIT FOR BETTER ENTRY": "watch",
    "🟡 SETUP OK — EXECUTION UNVERIFIED": "watch",
    "🟡 WATCH": "muted",
    "🟠 MISSED ENTRY — DO NOT CHASE": "bad",
    "🟠 EXTENDED — DO NOT CHASE": "bad",
    "🔴 AVOID": "bad",
}


def _n(v, spec=".2f", suffix=""):
    if v is None:
        return DASH
    try:
        return f"{float(v):{spec}}{suffix}"
    except (TypeError, ValueError):
        return DASH


def _money(v):
    """Abbreviate, but never past the point of saying nothing.

    The K branch used to catch everything under a million, so a $450 dollar
    risk printed as "$0K" and the position line read "risk $0K of $1K
    allowed" — the two numbers a sizing decision rests on, both rounded
    away. Anything under $10K now prints in full.
    """
    if v is None:
        return DASH
    try:
        v = float(v)
    except (TypeError, ValueError):
        return DASH
    if abs(v) >= 1e9:
        return f"${v/1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.1f}M"
    if abs(v) >= 1e4:
        return f"${v/1e3:.0f}K"
    return f"${v:,.0f}"


def _shares(v):
    """Share counts abbreviate to B above a billion — the profiles made
    megacap floats routine, and always-M overflowed the column."""
    if v is None:
        return DASH
    v = float(v)
    return f"{v/1e9:.1f}B" if v >= 1e9 else f"{v/1e6:.1f}M"


def _level(price, lvl):
    """§16's VWAP / PM High / PDH cells: the level, and which side of it
    price sits on. The arrow is the whole point — the number alone does not
    say whether it is support or resistance right now."""
    if price is None or lvl is None:
        return f'<span style="color:#c9c7c0">{DASH}</span>'
    above = price > lvl
    colour = "#0F6E56" if above else "#A32D2D"
    return (f'<span style="color:{colour};white-space:nowrap">'
            f'{"▲" if above else "▼"}{lvl:.2f}</span>')


def _score_cell(v, good=75, watch=55):
    if v is None:
        return f'<span style="color:#c9c7c0">{DASH}</span>'
    colour = "#0F6E56" if v >= good else ("#8a6d1a" if v >= watch else "#898781")
    return f'<span style="color:{colour};font-weight:600">{int(v)}</span>'


def _direction_badge(direction: str | None) -> str:
    """LONG / SHORT, stated outright.

    The engine has always known the direction — a short is picked up from
    BELOW_VWAP, PDL_BREAKDOWN and friends — but the page only ever showed
    it buried in the structure line, so a Bloom Energy short read as a bug:
    "why is STOP greater than entry". It is not a bug, and this is the
    label that says so.
    """
    if direction == "short":
        return badge("▼ SHORT", "bad", "small")
    if direction == "long":
        return badge("▲ LONG", "good", "small")
    return badge(DASH, "muted", "small")


def _extension_cell(dist_atr, price, vwap_):
    """How far price sits from VWAP, in ATR — §4's entry-quality ladder.

    The bare level said which side of VWAP price was on; this says whether
    the fill is any good, which is the question that actually decays over
    the session while every other column holds still.
    """
    if dist_atr is None:
        return _level(price, vwap_)
    if dist_atr < 0.5:
        colour, tag = "#0F6E56", "ideal"
    elif dist_atr < 1.0:
        colour, tag = "#0F6E56", "good"
    elif dist_atr < 1.5:
        colour, tag = "#8a6d1a", "caution"
    elif dist_atr < 2.0:
        colour, tag = "#A32D2D", "extended"
    else:
        colour, tag = "#A32D2D", "chase"
    return (f'<span style="color:{colour}" title="{tag}">'
            f'{dist_atr:.1f}<span style="font-size:9px">ATR</span></span>')


def _room_cell(room):
    """Room as a multiple of the expected move — §7's filter, not a raw %."""
    ratio = room.get("room_ratio")
    if room.get("nearest") is None:
        return '<span style="color:#0F6E56" title="open air">open</span>'
    if ratio is None:
        return f'<span style="color:#c9c7c0">{DASH}</span>'
    colour = ("#A32D2D" if ratio < 0.5 else
              "#8a6d1a" if ratio < 1.0 else "#0F6E56")
    return (f'<span style="color:{colour}" title="{esc(room.get("detail") or "")}">'
            f'{ratio:.1f}x</span>')


def _chase_cell(row):
    chase = row.get("chase_score")
    if chase is None:
        return f'<span style="color:#c9c7c0">{DASH}</span>'
    colour = "#0F6E56" if chase <= 1 else ("#8a6d1a" if chase <= 3 else "#A32D2D")
    reasons = "; ".join((row.get("entry") or {}).get("chase_reasons") or ["clear"])
    return (f'<span style="color:{colour};font-weight:600" title="{esc(reasons)}">'
            f'{chase}/6</span>')


def _risk_cell(sizing):
    """Shares and whether risk — not the allocation cap — set the size."""
    if not sizing.get("shares"):
        return f'<span style="color:#c9c7c0">{DASH}</span>'
    ok = sizing.get("risk_is_binding")
    colour = "#0F6E56" if ok else "#8a6d1a"
    return (f'<span style="color:{colour}" '
            f'title="{esc(sizing.get("binding_constraint") or "")}">'
            f'{sizing["shares"]:,}</span>')


def _age_note(generated: str | None) -> tuple[str, str]:
    """How old the snapshot is, and how loudly to say so.

    Handles both aware and naive stamps: the writer emits aware ones now,
    but a snapshot on disk may predate that. A naive stamp is assumed to be
    this process's local time, which is the best available guess and the
    same behaviour as before.
    """
    if not generated:
        return "age unknown", "bad"
    try:
        written = datetime.fromisoformat(generated)
    except ValueError:
        return "age unknown", "bad"
    now = datetime.now(written.tzinfo) if written.tzinfo else datetime.now()
    age = (now - written).total_seconds() / 60
    if age < 2:
        return "just now", "good"
    if age < 15:
        return f"{age:.0f} min ago", "good"
    if age < 60:
        return f"{age:.0f} min ago", "watch"
    if age < 24 * 60:
        return f"{age/60:.1f} hours ago", "bad"
    return f"{age/1440:.0f} days ago", "bad"


# ── header ───────────────────────────────────────────────────────────────────

SCREEN_OPTION = ""       # empty value = screen the market rather than a list


def _universe_options(selected: str = SCREEN_OPTION) -> str:
    """Market screen plus every watchlist on disk.

    `daytrade` leads the list because it is this page's natural companion —
    the names you already decided are worth watching intraday — and burying
    it under an alphabetical run of sector lists would make the common case
    the slowest to reach.
    """
    try:
        from stockanalysis.reporting.research import (
            load_watchlists, tree_ordered_names)
        lists = load_watchlists()
    except Exception:                       # a broken watchlists.json must
        lists, tree_ordered_names = {}, None    # not take the page down
    named = [n for n, t in (lists or {}).items() if t]
    ordered = []
    if "daytrade" in named:
        ordered.append("daytrade")
    rest = [n for n in named if n != "daytrade"]
    try:
        rest = tree_ordered_names(rest) if tree_ordered_names else sorted(rest)
    except Exception:
        rest = sorted(rest)
    ordered += rest

    sel = ' selected' if not selected else ''
    out = [f'<option value="{SCREEN_OPTION}"{sel}>Market screen</option>']
    for name in ordered:
        n = len(lists.get(name) or [])
        out.append(f'<option value="{esc(name)}"'
                   f'{" selected" if name == selected else ""}>'
                   f'{esc(name)} ({n})</option>')
    # A watchlist that has since been renamed or deleted would otherwise
    # silently fall back to "Market screen" — say so instead.
    if selected and selected not in ordered:
        out.append(f'<option value="{esc(selected)}" selected>'
                   f'{esc(selected)} (no longer in watchlists.json)</option>')
    return "".join(out)


def _controls(settings: dict, selected_profile: str = "small",
              selected_universe: str = SCREEN_OPTION) -> str:
    """§1 scan controls, preselected to the scan that produced the snapshot
    on screen. `at` is the as-of replay, which is the only way to read this
    page usefully outside market hours."""
    risk = (settings or {}).get("risk_pct") or 1.0
    return (
        '<form onsubmit="return submitJob(event, this)" '
        'style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">'
        '<input type="hidden" name="action" value="stockdaytrade">'
        + _field("Universe",
                 f'<select name="watchlist" style="max-width:190px">'
                 f'{_universe_options(selected_universe)}</select>',
                 "screen the whole market for movers, or run a watchlist you "
                 "already curate")
        + _field("Profile",
                 '<select name="profile">'
                 + "".join(
                     f'<option value="{k}"{" selected" if k == selected_profile else ""}>'
                     f'{PR.PROFILES[k]["label"]}</option>'
                     for k in ("small", "mid", "large"))
                 # Auto only makes sense for a list: a screen is bounded to
                 # one cap band by construction, so there is nothing to
                 # decide per name. A watchlist is usually mixed-cap and
                 # this is the option that makes it coherent.
                 + f'<option value="auto"'
                   f'{" selected" if selected_profile == "auto" else ""}>'
                   f'Auto (per stock)</option>'
                 + "</select>",
                 "market-cap calibration — weights and thresholds differ by "
                 "class; Auto judges each name against its own cap band")
        + _field("Candidates", '<input name="limit" type="number" min="5" max="60" '
                 'value="25" style="width:70px">',
                 "how many names get the full fundamentals pass, ranked by "
                 "move × liquidity")
        + _field("As of (ET)", '<input name="at_time" type="text" placeholder="live" '
                 'pattern="[0-9]{2}:[0-9]{2}" style="width:76px">',
                 "replay the session at HH:MM; blank = latest")
        + _field("Min grade",
                 '<select name="min_grade">'
                 + "".join(f'<option{" selected" if g == "C" else ""}>{g}</option>'
                           for g in ("A+", "A", "B+", "B", "C", "ALL"))
                 + "</select>", "hide rows below this grade")
        + _field("Risk %", f'<input name="risk_pct" type="number" step="0.25" '
                 f'min="0.25" max="5" value="{risk}" style="width:70px">',
                 "account % risked per trade")
        + '<button class="btn" type="submit">Run scan</button>'
        '</form>')


def _field(label: str, control: str, hint: str = "") -> str:
    return (f'<label style="display:flex;flex-direction:column;gap:3px;font-size:11px;'
            f'color:#898781"><span title="{esc(hint)}">{esc(label)}</span>'
            f'{control}</label>')


def _regime_card(regime: dict | None) -> str:
    if not regime:
        return card("Market regime", empty("no regime in this snapshot"), "🌡️")
    reasons = "".join(
        f'<li style="margin:2px 0">{esc(r)}</li>' for r in regime.get("reasons") or [])
    unavailable = regime.get("unavailable") or []
    # §15 asks for breadth this project has no source for. Saying so on the
    # page is the point — an unstated omission reads as a measurement.
    missing = (f'<div style="font-size:11px;color:#8a6d1a;margin-top:8px">'
               f'Not measured: {esc("; ".join(unavailable))}</div>'
               if unavailable else "")
    status = {"RISK ON": "good", "RISK OFF": "bad"}.get(regime.get("label"), "watch")
    return card(
        "Market regime",
        f'<ul style="margin:0;padding-left:16px;font-size:12px;color:#444441">'
        f'{reasons}</ul>{missing}', "🌡️",
        right=badge(f'{regime.get("emoji", "")} {regime.get("label", "?")}', status))


# ── §17 detail panel ─────────────────────────────────────────────────────────

def _kv(label: str, value: str) -> str:
    return (f'<div style="display:flex;gap:8px;margin:3px 0;font-size:12px">'
            f'<span style="color:#898781;min-width:132px;flex-shrink:0">{esc(label)}</span>'
            f'<span style="color:#0b0b0b">{value}</span></div>')


def _section(title: str, body: str) -> str:
    return (f'<div style="margin-bottom:12px">'
            f'<div style="font-size:10px;font-weight:700;letter-spacing:.6px;'
            f'color:#898781;text-transform:uppercase;margin-bottom:4px">{esc(title)}</div>'
            f'{body}</div>')


def _confirmations(row: dict) -> str:
    items = []
    for c in row.get("confirmations") or []:
        ok = c.get("ok")
        mark, colour = ("✓", "#0F6E56") if ok else ("✗", "#A32D2D")
        items.append(
            f'<div style="display:flex;gap:6px;font-size:11px;margin:2px 0">'
            f'<span style="color:{colour};font-weight:700">{mark}</span>'
            f'<span style="color:{"#444441" if ok else "#791F1F"};min-width:170px">'
            f'{esc(c.get("name"))}</span>'
            f'<span style="color:#898781">{esc(c.get("detail") or "")}</span></div>')
    return "".join(items) or empty("no confirmations recorded")


def _scorecard(row: dict) -> str:
    """The §10 arithmetic, so the confluence number is auditable rather than
    asserted."""
    blocks, pts = row.get("blocks") or {}, row.get("block_points") or {}
    # Weights travel with the row: a mid-cap and a small-cap row on the
    # same page were scored on different budgets, and showing one row's
    # points against the other's weights would be quietly wrong.
    weights = row.get("weights") or PR.weights(PR.by_key(row.get("profile")))
    lines = []
    for key, weight in weights.items():
        raw, earned = blocks.get(key), pts.get(key)
        bar = 0 if raw is None else max(2, min(100, raw))
        lines.append(
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:11px">'
            f'<span style="color:#898781;min-width:78px">{esc(key)}</span>'
            f'<div style="flex:1;background:#f1efea;border-radius:4px;height:6px;'
            f'overflow:hidden;min-width:70px"><div style="width:{bar}%;height:100%;'
            f'background:{"#d9d7ce" if raw is None else "#185FA5"}"></div></div>'
            f'<span style="color:#0b0b0b;min-width:64px;text-align:right">'
            f'{DASH if earned is None else f"{earned:.1f}"}/{weight}</span></div>')
    extra = []
    if row.get("squeeze_credit"):
        extra.append("squeeze credit +5 (§4 preconditions met)")
    if row.get("regime_adjustment"):
        extra.append(str(row["regime_adjustment"]))
    tail = (f'<div style="font-size:11px;color:#898781;margin-top:6px">'
            f'{esc(" · ".join(extra))}</div>' if extra else "")
    cov = row.get("coverage")
    head = (f'<div style="font-size:11px;color:#898781;margin-bottom:6px">'
            f'confluence {row.get("confluence")} · coverage '
            f'{DASH if cov is None else f"{cov:.0%}"}</div>')
    return head + "".join(lines) + tail


def _detail(row: dict) -> str:
    """§17's eight explanations for one candidate."""
    pl = row.get("plan") or {}
    cat = row.get("catalyst") or {}
    vol = row.get("volatility") or {}
    rs = row.get("strength") or {}
    room = row.get("room") or {}
    sizing = row.get("sizing") or {}
    out = []

    # WHY IT IS MOVING
    if cat.get("headline"):
        age = (f"{cat['age_hours']:.0f}h ago" if cat.get("age_hours") is not None
               else "undated")
        why = (f'<div style="font-size:12px;color:#0b0b0b">{esc(cat["headline"])}</div>'
               f'<div style="font-size:11px;color:#898781;margin-top:2px">'
               f'{esc(cat.get("type"))} · {esc(age)}'
               + (f' · {esc(cat.get("publisher"))}' if cat.get("publisher") else "")
               + "</div>")
    else:
        why = empty(cat.get("detail") or "no catalyst identified")
    why += _kv("Move", f'gap {_n(vol.get("gap_pct"), "+.1f", "%")} · RVOL '
                       f'{_n(vol.get("rvol"))} · {_money(vol.get("dollar_volume"))} traded')
    out.append(_section("Why it is moving", why))

    # WHY IT CAN CONTINUE
    cont = _kv("Structure", esc(row.get("setup_detail") or DASH))
    if vol.get("expected_move_pct") is not None:
        cont += _kv("Range", f'ATR {_n(vol.get("atr_pct"), ".1f", "%")} · '
                             f'{_n(vol.get("day_range_pct"), ".1f", "%")} so far · '
                             f'~{_n(vol.get("expected_move_pct"), ".1f", "%")} left')
    bits = [f'SPY {_n(rs.get("vs_spy"), "+.1f", "pp")}',
            f'IWM {_n(rs.get("vs_iwm"), "+.1f", "pp")}']
    if rs.get("sector_etf"):
        bits.append(f'{esc(rs["sector_etf"])} {_n(rs.get("vs_sector"), "+.1f", "pp")}')
    cont += _kv("Relative strength", " · ".join(bits))
    if rs.get("diverging_from"):
        cont += _kv("Divergence",
                    f'holding up while {esc(", ".join(rs["diverging_from"]))} red')
    out.append(_section("Why it can continue", cont))

    # ENTRY / STOP / TARGETS
    if pl.get("actionable"):
        # Direction first. On a short the stop sits ABOVE the entry and the
        # targets below it, which reads as a bug unless the page has said
        # which way the trade goes — and it never did.
        plan_html = _kv("Direction", _direction_badge(row.get("direction"))
                        + ('<span style="color:#898781;margin-left:8px">'
                           'stop sits above entry, targets below</span>'
                           if row.get("direction") == "short" else ""))
        plan_html += _kv("Trigger", esc(pl.get("trigger") or DASH))
        if pl.get("triggered"):
            plan_html += _kv("", '<span style="color:#8a6d1a">already through the '
                                 'trigger — reference price, not a fresh entry</span>')
        plan_html += _kv("Entry", f'<b>{_n(pl.get("entry"))}</b>')
        plan_html += _kv("Stop", f'<b>{_n(pl.get("stop"))}</b> — {esc(pl.get("stop_basis"))}')
        plan_html += _kv("Risk / share", f'{_n(pl.get("risk_per_share"), ".3f")} '
                                         f'({_n(pl.get("risk_pct_of_price"), ".1f", "%")} of entry)')
        if pl.get("stop_note"):
            plan_html += _kv("", f'<span style="color:#791F1F">⚠ {esc(pl["stop_note"])}</span>')
        plan_html += _kv("Target 1", f'{_n(pl.get("target1"))} — {esc(pl.get("target1_basis"))} '
                                     f'(R:R {_n(pl.get("rr"))})')
        plan_html += _kv("Target 2", f'{_n(pl.get("target2"))} — {esc(pl.get("target2_basis"))} '
                                     f'(R:R {_n(pl.get("rr_target2"))})')
        plan_html += _kv("Blended R:R",
                         f'<b>{_n(pl.get("rr_blended"))}</b> — half off at T1; this is '
                         f'what the grade uses')
        if pl.get("entry_for_2r"):
            plan_html += _kv("For 2:1", f'{_n(pl["entry_for_2r"])} would be the entry '
                                        f'that makes it 2:1')
        plan_html += _kv("Invalidation", esc(pl.get("invalidation") or DASH))
        out.append(_section("Entry · stop · targets (§13)", plan_html))
    else:
        out.append(_section("Entry · stop · targets (§13)",
                            empty(pl.get("reason") or "no actionable plan")))

    # ENTRY QUALITY — the section that answers "at this price, right now?"
    en = row.get("entry") or {}
    eq = _kv("Entry score", f'<b>{en.get("score")}</b> — {esc(en.get("entry_grade") or DASH)}')
    eq += _kv("From trigger",
              (f'{en["beyond_trigger_atr"]:.2f} ATR past'
               if (en.get("beyond_trigger_atr") or 0) > 0.01
               else "at or below the trigger — not chasing")
              if en.get("beyond_trigger_atr") is not None else DASH)
    eq += _kv("From VWAP",
              f'{_n(en.get("vwap_distance_atr"), ".2f")} ATR '
              f'({_n(en.get("vwap_distance_pct"), "+.1f", "%")})')
    if en.get("expected_move_consumed_pct") is not None:
        eq += _kv("Move consumed",
                  f'{en["expected_move_consumed_pct"]:.0f}% of the expected range')
    chase_colour = ("#0F6E56" if (en.get("chase_score") or 0) <= 1
                    else "#8a6d1a" if (en.get("chase_score") or 0) <= 3 else "#791F1F")
    eq += _kv("Chase score",
              f'<span style="color:{chase_colour}"><b>{en.get("chase_score")}</b>/6 '
              f'{esc(en.get("chase_label") or "")}</span>')
    for why in en.get("chase_reasons") or []:
        eq += _kv("", f'<span style="color:#8a6d1a">· {esc(why)}</span>')
    if (en.get("candle") or {}).get("detail"):
        eq += _kv("Last candle", esc(en["candle"]["detail"]))
    if (en.get("pullback") or {}).get("detail"):
        eq += _kv("Pullback", esc(en["pullback"]["detail"]))
    out.append(_section("Entry quality (§20)", eq))

    # EXECUTION GATE — §2
    gate = row.get("gate") or {}
    if gate.get("checks"):
        items = []
        for c in gate["checks"]:
            ok = c.get("ok")
            mark, colour = (("✓", "#0F6E56") if ok is True else
                            ("✗", "#A32D2D") if ok is False else ("?", "#8a6d1a"))
            items.append(
                f'<div style="display:flex;gap:6px;font-size:11px;margin:2px 0">'
                f'<span style="color:{colour};font-weight:700">{mark}</span>'
                f'<span style="color:#444441;min-width:170px">{esc(c["name"])}</span>'
                f'<span style="color:#898781">{esc(c.get("detail") or "")}</span></div>')
        head = ('<div style="font-size:11px;margin-bottom:4px;color:'
                + ("#0F6E56" if gate.get("passed") else "#791F1F") + '">'
                + ("all execution conditions met — A+ available"
                   if gate.get("passed")
                   else "A+ withheld: " + esc(", ".join(
                       (gate.get("failed") or []) + (gate.get("unverified") or []))))
                + "</div>")
        out.append(_section("Execution gate (§2)", head + "".join(items)))

    # RESISTANCE
    res = _kv("Nearest", esc(room.get("detail") or DASH))
    if room.get("blocked"):
        res += _kv("", '<span style="color:#791F1F">⚠ §9: significant resistance '
                       'immediately ahead — do not chase</span>')
    out.append(_section("What must be cleared (§9)", res))

    # CONFIRMATIONS
    out.append(_section(
        f'Confirmations — {row.get("n_confirmations")}/{row.get("n_checks")} (§12)',
        _confirmations(row)))

    # WARNINGS
    warns = list(row.get("warnings") or [])
    dilution = (row.get("supply") or {}).get("dilution_note")
    if dilution:
        warns.append(dilution)
    if warns:
        out.append(_section("Warnings", "".join(
            f'<div style="font-size:11px;color:#791F1F;margin:2px 0">⚠ {esc(w)}</div>'
            for w in warns)))

    # POSITION
    if sizing.get("shares"):
        pos = _kv("Size", f'<b>{sizing["shares"]:,}</b> shares · '
                          f'{_money(sizing.get("position_value"))} '
                          f'({_n(sizing.get("pct_of_capital"), ".1f", "%")} of capital)')
        pos += _kv("Risk", f'{_money(sizing.get("dollar_risk"))} of '
                           f'{_money(sizing.get("max_dollar_risk"))} allowed'
                           + (f' ({sizing["risk_budget_used_pct"]:.0f}% used)'
                              if sizing.get("risk_budget_used_pct") is not None else ""))
        # Structural risk and true risk shown apart: the difference is the
        # slippage the account actually pays, and hiding it inside one
        # number is how sizing quietly understates the loss (§10).
        pos += _kv("Risk / share",
                   f'{_n(sizing.get("structural_risk_per_share"), ".3f")} structural + '
                   f'{_n(sizing.get("slippage_per_share"), ".3f")} slippage = '
                   f'<b>{_n(sizing.get("true_risk_per_share"), ".3f")}</b>')
        if sizing.get("slippage_basis"):
            pos += _kv("", f'<span style="color:#898781">{esc(sizing["slippage_basis"])}</span>')
        binding_colour = "#0F6E56" if sizing.get("risk_is_binding") else "#8a6d1a"
        # `risk_based_shares` is absent from snapshots written before
        # risk-first sizing, and `:,` on None is a TypeError that takes the
        # whole page down. A stored snapshot is a schema this code does not
        # control — it can be hours old and from any prior version — so
        # every new field has to degrade rather than assume.
        risk_shares = sizing.get("risk_based_shares")
        alt = (f' — risk alone would allow {risk_shares:,}'
               if not sizing.get("risk_is_binding") and isinstance(risk_shares, int)
               else "")
        pos += _kv("Binding constraint",
                   f'<span style="color:{binding_colour}">'
                   f'{esc(sizing.get("binding_constraint"))}</span>{alt}')
        if sizing.get("position_liquidity_pct") is not None:
            liq = sizing["position_liquidity_pct"]
            lc = "#0F6E56" if liq <= 10 else ("#8a6d1a" if liq <= 25 else "#791F1F")
            pos += _kv("Position liquidity",
                       f'<span style="color:{lc}">{liq:.0f}% of a minute\'s '
                       f'dollar volume</span>')
        if (sizing.get("risk_multiplier") or 1) < 1:
            pos += _kv("Risk haircut",
                       f'<span style="color:#8a6d1a">size reduced to '
                       f'{sizing["risk_multiplier"]*100:.0f}% for execution risk</span>')
        out.append(_section("Position (§14)", pos))
    elif sizing.get("reason"):
        out.append(_section("Position (§14)", empty(sizing["reason"])))

    out.append(_section("Confluence arithmetic (§10)", _scorecard(row)))

    return (f'<div style="display:grid;grid-template-columns:repeat(auto-fit,'
            f'minmax(330px,1fr));gap:0 26px;padding:14px 16px;background:#faf9f6;'
            f'border-top:0.5px solid #e1e0d9">{"".join(out)}</div>')


# ── §16 table ────────────────────────────────────────────────────────────────

# §22: the ranking columns come first and the execution columns sit next to
# the action, because the question the table answers is "how good is this
# trade right now", not "how interesting is this stock". Entry, Chase and
# Action are the three that changed the page's meaning.
#
# (label, alignment, tooltip). "Setup" and "Entry" each used to appear
# twice — once as a structure/price column and once as a score — which was
# merely confusing to read and becomes ambiguous the moment the headers are
# clickable, so the structure column is "Pattern" and the score is "EntryQ".
_COLS = (
    ("#", "right", "rank in the current sort"),
    ("Ticker", "left", "ticker and last price"),
    # Third, and frozen with the ticker. It was originally ninth, which put
    # it off-screen at rest — so the one fact that explains why a stop sits
    # above an entry was invisible exactly while you were reading the stop
    # and the entry.
    ("Dir", "left", "long or short — on a short the stop is ABOVE the entry "
                    "and the targets below it"),
    ("Catalyst", "left", "why it is moving (§2)"),
    ("Float", "right", "shares in the float (§4)"),
    ("Gap %", "right", "open vs previous close (§3)"),
    ("RVOL", "right", "volume vs the same clock time on prior sessions (§3)"),
    ("ATR %", "right", "daily ATR as a share of price (§3)"),
    ("$Vol", "right", "dollar volume traded this session"),
    ("Pattern", "left", "primary intraday structure (§6)"),
    ("RS", "right", "relative strength vs SPY (§7)"),
    ("VWAP", "right", "distance from VWAP in 5-min ATRs (§4)"),
    ("Room", "right", "room to the next level, as a multiple of the expected move (§9)"),
    ("Entry", "right", "trigger entry price (§13)"),
    ("Stop", "right", "structural stop (§13)"),
    ("T1", "right", "first target (§13)"),
    ("R:R", "right", "blended risk/reward — half off at T1"),
    ("CScore", "right",
     "confluence score 0-100: is this stock worth trading today (§10)"),
    ("Setup", "right", "structure quality (§6)"),
    ("EntryQ", "right", "entry quality: should I buy at THIS price (§20)"),
    ("Chase", "left", "how many ways you are late, out of 6 (§6)"),
    ("Risk", "right", "position size in shares (§14)"),
    ("Action", "left", "what to do right now (§19)"),
)

# Room is unbounded when nothing is overhead, so "open air" has to sort
# above every finite ratio rather than as a missing value. Not a fabricated
# measurement — there genuinely is no level in the way.
_OPEN_AIR_SORT = 1e9


def _sort_key(value) -> str:
    """The `data-sort` attribute for a cell.

    Cells render formatted text — "$19.9M", "+36.8%", "1.4ATR", "2/6" — and
    sorting those as strings puts $9.4M above $215.2M. So each cell carries
    its raw comparable value, and the JS never parses display text.

    An empty string means "no value": those sort last in BOTH directions,
    because unknown is not small. That is the same rule the engine applies
    everywhere else, and reversing it here would make a column of dashes
    look like the best rows on one click and the worst on the next.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(float(value))
    return str(value).strip().lower()


# Rank, ticker and direction freeze to the left. With 23 columns on a
# 1620px table, scrolling right to read R:R or Chase otherwise leaves you
# looking at a row of numbers with no idea which stock they belong to or
# which way the trade goes — and on this page a misattributed stop price
# is not a cosmetic problem. Widths are fixed so each offset is exact.
_COL_W = {0: 40, 1: 92, 2: 68}
_FROZEN = {i: sum(_COL_W[j] for j in range(i)) for i in _COL_W}
_FREEZE_EDGE = max(_FROZEN)      # the column that carries the divider


def _td(html: str, align: str, sort_value, extra_style: str = "",
        col: int | None = None) -> str:
    base = (f'padding:7px 8px;text-align:{align};font-size:11px;'
            f'border-bottom:0.5px solid #f1efea;white-space:nowrap{extra_style}')
    if col in _FROZEN:
        # An opaque background is required, not decorative: a sticky cell
        # is transparent by default and the scrolled columns slide visibly
        # underneath it.
        base += (f';position:sticky;left:{_FROZEN[col]}px;z-index:2;'
                 f'background:white;width:{_COL_W[col]}px;'
                 f'min-width:{_COL_W[col]}px')
        if col == _FREEZE_EDGE:
            base += ";box-shadow:1px 0 0 #e1e0d9"
    return f'<td style="{base}" data-sort="{esc(_sort_key(sort_value))}">{html}</td>'


def _th(idx: int, label: str, align: str, tip: str) -> str:
    # The header row is already sticky vertically; the first two cells are
    # sticky in both axes at once, so they need a higher z-index than
    # either the plain headers (which scroll under them horizontally) or
    # the frozen body cells (which scroll under them vertically).
    style = (f'padding:7px 8px;text-align:{align};'
             f'font-size:10px;font-weight:600;color:#898781;text-transform:uppercase;'
             f'letter-spacing:.4px;white-space:nowrap;position:sticky;top:0;'
             f'background:#faf9f6;border-bottom:0.5px solid #e1e0d9;'
             f'cursor:pointer;user-select:none;z-index:3')
    if idx in _FROZEN:
        style += (f';left:{_FROZEN[idx]}px;z-index:4;'
                  f'width:{_COL_W[idx]}px;min-width:{_COL_W[idx]}px')
        if idx == _FREEZE_EDGE:
            style += ";box-shadow:1px 0 0 #e1e0d9"
    return (f'<th data-col="{idx}" onclick="sdtSort({idx})" '
            f'title="{esc(tip)} — click to sort" style="{style}">{esc(label)}'
            f'<span class="sdt-arrow" style="color:#c9c7c0;margin-left:3px"></span></th>')


def _table(rows: list[dict]) -> str:
    head = "".join(_th(idx, label, align, tip)
                   for idx, (label, align, tip) in enumerate(_COLS))

    body = []
    for i, r in enumerate(rows, 1):
        sess, pl = r.get("session") or {}, r.get("plan") or {}
        vol, sup = r.get("volatility") or {}, r.get("supply") or {}
        en, sz = r.get("entry") or {}, r.get("sizing") or {}
        room = r.get("room") or {}
        price = r.get("price")
        action = r.get("action") or DASH

        room_sort = (_OPEN_AIR_SORT if room.get("nearest") is None
                     else room.get("room_ratio"))

        cells = [
            _td(str(i), "right", i, ";color:#898781", col=0),
            _td(f'<a href="{tv_url(r["ticker"])}" target="_blank" rel="noopener" '
                f'style="font-weight:700;color:#0b0b0b;text-decoration:none">'
                f'{esc(r["ticker"])}</a>'
                f'<div style="font-size:10px;color:#898781">{_n(price)}</div>',
                "left", r.get("ticker"), col=1),
            _td(_direction_badge(r.get("direction")), "left",
                r.get("direction") or "", col=2),
            _td(esc((r.get("catalyst") or {}).get("type") or DASH), "left",
                (r.get("catalyst") or {}).get("type"), ";color:#444441"),
            _td(_shares(sup.get("float_shares")), "right", sup.get("float_shares")),
            _td(_n(vol.get("gap_pct"), "+.1f", "%"), "right", vol.get("gap_pct")),
            _td(_n(vol.get("rvol")), "right", vol.get("rvol")),
            _td(_n(vol.get("atr_pct"), ".1f", "%"), "right", vol.get("atr_pct")),
            _td(_money(vol.get("dollar_volume")), "right", vol.get("dollar_volume")),
            _td(esc((r.get("patterns") or {}).get("primary") or DASH), "left",
                (r.get("patterns") or {}).get("primary"), ";color:#444441"),
            _td(_n((r.get("strength") or {}).get("vs_spy"), "+.1f", "pp"), "right",
                (r.get("strength") or {}).get("vs_spy")),
            # VWAP extension in ATR is the number that decides whether the
            # fill is good, so it replaces the bare level.
            _td(_extension_cell(en.get("vwap_distance_atr"), price, sess.get("vwap")),
                "right", en.get("vwap_distance_atr")),
            _td(_room_cell(room), "right", room_sort),
            _td(_n(pl.get("entry")), "right", pl.get("entry")),
            _td(_n(pl.get("stop")), "right", pl.get("stop")),
            _td(_n(pl.get("target1")), "right", pl.get("target1")),
            _td(f'<b>{_n(pl.get("rr_blended"))}</b>', "right", pl.get("rr_blended")),
            _td(_score_cell(r.get("confluence"), 80, 60), "right", r.get("confluence")),
            _td(_score_cell(r.get("setup_score")), "right", r.get("setup_score")),
            _td(_score_cell(r.get("entry_score"), 80, 70), "right", r.get("entry_score")),
            _td(_chase_cell(r), "left", r.get("chase_score")),
            _td(_risk_cell(sz), "right", sz.get("shares")),
            # Action sorts by the engine's own severity order, not
            # alphabetically — "AVOID" before "ENTER NOW" would be a
            # nonsense ordering of a column whose whole point is urgency.
            _td(f'<a href="#sc-{esc(r["ticker"])}" style="text-decoration:none" '
                f'title="{esc(r.get("action_why") or "")}">'
                f'{badge(action, _ACTION_STATUS.get(action, "muted"), "small")}</a>',
                "left", ACTION_RANK.get(action, 99)),
        ]
        body.append(f'<tr>{"".join(cells)}</tr>')

    return (f'<div style="overflow-x:auto;max-height:70vh;overflow-y:auto;'
            f'border:0.5px solid #e1e0d9;border-radius:12px;background:white">'
            f'<table id="sdt-table" style="width:100%;min-width:1620px;'
            f'border-collapse:collapse">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
            f'<div style="font-size:11px;color:#898781;margin:6px 2px 0">'
            f'Click any column to sort; click again to reverse. Blank values stay '
            f'last in both directions — unknown is not zero.</div>')


# Client-side rather than a query-string round trip: the whole table is
# already in the DOM, the row count is in the tens, and re-sorting must not
# cost a page load — the levels on this page go stale by the minute, so a
# reload to reorder twenty rows would be the slowest possible way to answer
# "which of these has the most room".
_SORT_JS = r"""
function sdtSort(col) {
  const table = document.getElementById('sdt-table');
  if (!table) return;
  const headers = table.tHead.rows[0].cells;
  const prev = parseInt(table.dataset.sortCol, 10);
  // First click on a column: descending, because every score and metric
  // here is "more is more interesting". Clicking the same column again
  // reverses. The rank column is the exception — ascending puts the
  // engine's own ordering back, which is what people expect from "#".
  let dir;
  if (prev === col) { dir = table.dataset.sortDir === 'desc' ? 'asc' : 'desc'; }
  else { dir = col === 0 ? 'asc' : 'desc'; }
  table.dataset.sortCol = col;
  table.dataset.sortDir = dir;

  for (let i = 0; i < headers.length; i++) {
    const arrow = headers[i].querySelector('.sdt-arrow');
    if (!arrow) continue;
    arrow.textContent = (i === col) ? (dir === 'asc' ? '▲' : '▼') : '';
    arrow.style.color = (i === col) ? '#185FA5' : '#c9c7c0';
    headers[i].style.color = (i === col) ? '#185FA5' : '#898781';
  }

  const body = table.tBodies[0];
  const rows = Array.from(body.rows);
  const sign = dir === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    const av = a.cells[col].dataset.sort, bv = b.cells[col].dataset.sort;
    // Blanks last in BOTH directions: unknown is not small. Flipping them
    // would make a column of dashes look like the best rows on one click
    // and the worst on the next.
    if (av === '' && bv === '') return 0;
    if (av === '') return 1;
    if (bv === '') return -1;
    const an = parseFloat(av), bn = parseFloat(bv);
    const numeric = !isNaN(an) && !isNaN(bn) && av !== '' && bv !== ''
                    && /^-?[\d.]+(e[-+]?\d+)?$/i.test(av)
                    && /^-?[\d.]+(e[-+]?\d+)?$/i.test(bv);
    if (numeric) return (an - bn) * sign;
    return av.localeCompare(bv) * sign;
  });
  // Re-append in order. The rank cell is deliberately NOT renumbered: it
  // is the engine's ranking, and rewriting it to match an ad-hoc sort
  // would destroy the one column that can restore the original order.
  rows.forEach(r => body.appendChild(r));
}
"""


def _decision_cards(rows: list[dict]) -> str:
    """§17, below the table rather than inside it.

    The first version nested each panel in a `<td colspan>`, which put it
    inside the table's 1560px horizontal scroll — the right-hand column of
    every panel was clipped, so the stop note and the targets were
    unreadable without scrolling sideways. §16 and §17 are separate sections
    in the spec for the same reason they are separate here: the table is
    wide and scans across, the explanation is narrow and reads down.

    Actionable setups start expanded; everything else is collapsed but
    present, because "what would have to change for this to become a trade"
    is the most useful thing on the page for a name that did not qualify.
    """
    cards = []
    for r in rows:
        grade = r.get("grade") or DASH
        action = r.get("action") or DASH
        # Expanded for anything you might act on now — which is the action,
        # not the grade. A 92-confluence name you must not chase does not
        # need to be open; a B+ you can enter does.
        open_now = action in ("🔥 ENTER NOW", "🟢 WAIT FOR BREAKOUT",
                              "🟢 WAIT FOR PULLBACK")
        cards.append(
            f'<details id="sc-{esc(r["ticker"])}"{" open" if open_now else ""} '
            f'style="background:white;border:0.5px solid #e1e0d9;border-radius:12px;'
            f'margin-bottom:10px;overflow:hidden">'
            f'<summary style="cursor:pointer;padding:11px 16px;list-style:none;'
            f'display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            f'<span style="font-weight:700;font-size:14px;color:#0b0b0b">'
            f'{esc(r["ticker"])}</span>'
            f'<span style="font-size:11px;color:#898781">{esc(r.get("name") or "")}</span>'
            f'{_direction_badge(r.get("direction"))}'
            f'{badge(action, _ACTION_STATUS.get(action, "muted"), "small")}'
            f'{badge(grade, _GRADE_STATUS.get(grade, "muted"), "small")}'
            f'<span style="font-size:11px;color:#898781">'
            f'CScore {r.get("confluence")} · setup {r.get("setup_score")} · '
            f'entry {r.get("entry_score")} · chase {r.get("chase_score")}/6</span>'
            f'<span style="font-size:11px;color:#8a6d1a;flex:1">'
            f'{esc(r.get("action_why") or "")}</span>'
            f'<span style="color:#185FA5;font-size:11px">details ▾</span>'
            f'</summary>{_detail(r)}</details>')
    return "".join(cards)


# ── page ─────────────────────────────────────────────────────────────────────

def stockdaytrade_page() -> tuple[str, str]:
    from stockanalysis.core.daytrade import store

    data = store.load()
    if not data:
        return (card(
            "StockDayTrade",
            '<p style="font-size:13px;color:#444441;margin:0 0 12px">'
            'No scan yet. This screens the whole US market for names on unusual '
            'volume with a fresh catalyst and a definable structure, ranks them '
            'by §10 confluence, and then asks separately whether the price in '
            'front of you is worth paying. Pick a market-cap profile — the '
            'weights and thresholds differ by class. It takes a minute or two.</p>'
            + _controls({}), "🔥"), "")

    rows = data.get("rows") or []
    regime = data.get("regime")
    age_text, age_status = _age_note(data.get("generated"))
    live = any(r.get("is_live") for r in rows)

    notes = "".join(
        f'<div style="font-size:11px;color:#8a6d1a;margin:2px 0">! {esc(n)}</div>'
        for n in data.get("notes") or [])

    # The staleness banner is deliberately loud. Every level on this page —
    # VWAP, opening range, premarket high — is an intraday level, and a
    # two-hour-old one is indistinguishable from a live one by eye while
    # being useless to trade against.
    banner = ""
    if not live:
        banner = (
            f'<div style="background:#FAEEDA;color:#633806;padding:10px 14px;'
            f'border-radius:10px;font-size:12px;margin-bottom:14px">'
            f'<b>Not a live scan.</b> These are the completed '
            f'{esc(data.get("asof"))} session\'s levels, captured {esc(age_text)}. '
            f'Useful for preparation; every price here is that session\'s close.</div>')
    elif age_status != "good":
        banner = (
            f'<div style="background:#FCEBEB;color:#791F1F;padding:10px 14px;'
            f'border-radius:10px;font-size:12px;margin-bottom:14px">'
            f'<b>Stale intraday data.</b> Captured {esc(age_text)} — re-run before '
            f'acting on any level below.</div>')

    # Summarised by action, not grade. The grade distribution answers "how
    # interesting was today"; the action distribution answers "how many of
    # these can I actually take", which is the number worth showing first.
    counts = {}
    for r in rows:
        counts[r.get("action")] = counts.get(r.get("action"), 0) + 1
    summary = " ".join(
        badge(f"{a} {counts[a]}", _ACTION_STATUS.get(a, "muted"), "small")
        for a in _ACTION_STATUS if counts.get(a))

    header = card(
        "StockDayTrade",
        banner
        + f'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;'
          f'margin-bottom:12px">{summary}'
          f'<span style="color:#898781;font-size:11px;margin-left:4px">'
          f'{len(rows)} candidates · session {esc(data.get("asof"))} · '
          f'captured {esc(age_text)}</span></div>'
        + notes + ('<div style="height:8px"></div>' if notes else "")
        # What was requested, not what the rows resolved to — see store.py.
        # Falls back to the first row's profile for snapshots written before
        # the request was recorded.
        + _controls(data.get("settings") or {},
                    data.get("profile_requested")
                    or (rows[0].get("profile") if rows else "small") or "small",
                    data.get("universe") or SCREEN_OPTION),
        "🔥", right=badge(age_text, age_status))

    body = header + _regime_card(regime)
    if rows:
        enterable = sum(1 for r in rows if r.get("action") in
                        ("🔥 ENTER NOW", "🟢 WAIT FOR BREAKOUT", "🟢 WAIT FOR PULLBACK"))
        body += _table(rows)
        body += (
            f'<div style="display:flex;align-items:baseline;gap:10px;'
            f'margin:20px 2px 10px">'
            f'<h3 style="font-size:14px;font-weight:600;color:#0b0b0b;margin:0">'
            f'§17 Decision engine</h3>'
            f'<span style="font-size:11px;color:#898781">'
            f'{enterable} of {len(rows)} enterable now · those are expanded</span></div>')
        body += _decision_cards(rows)
    else:
        body += card("", empty("No candidates in this snapshot."), "")
    body += (
        '<p style="font-size:11px;color:#898781;margin:12px 2px 0">'
        'Ranking follows §16: confluence, then setup, tradeability, catalyst, '
        'RVOL, float, room, relative strength. Tradeability and room are gates — '
        'they cap the grade rather than averaging into it. Dilution, borrow fee '
        'and market breadth have no yfinance source and are never estimated; '
        'check the latest S-1/S-3/424B5 before sizing.</p>')
    return body, _SORT_JS
