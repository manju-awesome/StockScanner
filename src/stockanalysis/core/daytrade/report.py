"""
report.py — §16 ranked table and §17 decision blocks
====================================================
Rendering only. Nothing here computes a score, so a number that appears in
the report can always be traced to the engine that produced it.

Two views of the same rows
---------------------------
§16 specifies a 24-column table, which is right for a saved artefact and
unreadable in an 80-column terminal. So the full table is rendered as
markdown for the file, and a compact view — the columns you actually rank
on — is printed to the console. Both come from the same rows in the same
order; the compact one drops columns, never reorders or filters.

Missing values print as "—", never as 0
----------------------------------------
A blank RVOL and an RVOL of 0.0 mean opposite things, and the whole
package is built to keep them distinct. Undoing that at the last step, in
a formatter, would waste it.
"""

from __future__ import annotations

from stockanalysis.core.daytrade.engine import WEIGHTS

DASH = "—"


def _n(v, spec=".2f", suffix="", scale=1.0):
    if v is None:
        return DASH
    try:
        return f"{float(v) * scale:{spec}}{suffix}"
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
    """Share counts abbreviate to B above a billion.

    Always-M was fine while every candidate was sub-$2B, but a megacap's
    22,900.2M float overflowed the column and ran into the price beside it
    ("223.3222900.2M"). The profiles made billions routine.
    """
    if v is None:
        return DASH
    v = float(v)
    return f"{v/1e9:.1f}B" if v >= 1e9 else f"{v/1e6:.1f}M"


def _side(price, level):
    """Where price sits relative to a level — the §16 VWAP/PM High/PDH cells."""
    if price is None or level is None:
        return DASH
    return f"{'▲' if price > level else '▼'}{level:.2f}"


# ── §16 ──────────────────────────────────────────────────────────────────────

TABLE_HEADER = (
    "| Rank | Ticker | Price | Float | Gap % | RVOL | ATR % | $Vol | Catalyst | "
    "VWAP | PM High | PDH | Setup | RS | Room % | Setup Score | Tradeability | "
    "Confluence | Entry | Stop | T1 | T2 | R:R | Action |\n"
    "| ---- | ------ | ----: | ----: | ----: | ---: | ----: | ---: | -------- | "
    "---- | ------: | --: | ----- | -- | -----: | ----------: | -----------: | "
    "---------: | ----: | ---: | -: | -: | --: | ------ |"
)


def render_table(rows: list[dict]) -> str:
    """The §16 table, markdown."""
    lines = [TABLE_HEADER]
    for i, r in enumerate(rows, 1):
        sess, pl = r["session"], r.get("plan", {})
        vol, sup = r["volatility"], r["supply"]
        price = r.get("price")
        lines.append("| " + " | ".join([
            str(i),
            r["ticker"],
            _n(price, ".2f", scale=1.0),
            _shares(sup.get("float_shares")),
            _n(vol.get("gap_pct"), "+.1f", "%"),
            _n(vol.get("rvol"), ".2f"),
            _n(vol.get("atr_pct"), ".1f", "%"),
            _money(vol.get("dollar_volume")),
            (r["catalyst"].get("type") or DASH),
            _side(price, sess.get("vwap")),
            _side(price, sess.get("pm_high")),
            _side(price, sess.get("prev_high")),
            (r["patterns"].get("primary") or DASH),
            _n(r["strength"].get("vs_spy"), "+.1f", "pp"),
            _n(r["room"].get("nearest_pct"), ".1f", "%"),
            str(r.get("setup_score") if r.get("setup_score") is not None else DASH),
            str(r.get("tradeability") if r.get("tradeability") is not None else DASH),
            str(r.get("confluence") if r.get("confluence") is not None else DASH),
            _n(pl.get("entry")), _n(pl.get("stop")),
            _n(pl.get("target1")), _n(pl.get("target2")),
            _n(pl.get("rr"), ".2f"),
            r.get("decision", DASH),
        ]) + " |")
    return "\n".join(lines)


COMPACT_HEADER = (
    f"{'#':>2}  {'TICKER':<7}{'PRICE':>8}{'FLOAT':>8}{'GAP%':>8}{'RVOL':>7}"
    f"{'ATR%':>7}{'$VOL':>9}  {'PATTERN':<22}{'CSCORE':>7}{'SETUP':>6}"
    f"{'ENTRY':>6}{'CHASE':>6}{'R:R':>6}  {'GRADE':<4} ACTION"
)


def render_compact(rows: list[dict]) -> str:
    """Console view, ordered by what is tradeable now rather than by which
    stock is most interesting — see engine.rank_key."""
    out = [COMPACT_HEADER, "─" * len(COMPACT_HEADER)]
    for i, r in enumerate(rows, 1):
        vol, pl = r["volatility"], r.get("plan", {})
        chase = r.get("chase_score")
        out.append(
            f"{i:>2}  {r['ticker']:<7}{_n(r.get('price')):>8}"
            f"{_shares(r['supply'].get('float_shares')):>8}"
            f"{_n(vol.get('gap_pct'), '+.1f', '%'):>8}"
            f"{_n(vol.get('rvol'), '.2f'):>7}"
            f"{_n(vol.get('atr_pct'), '.1f', '%'):>7}"
            f"{_money(vol.get('dollar_volume')):>9}  "
            f"{(r['patterns'].get('primary') or DASH):<22}"
            f"{(r.get('confluence') if r.get('confluence') is not None else DASH)!s:>7}"
            f"{(r.get('setup_score') if r.get('setup_score') is not None else DASH)!s:>6}"
            f"{(r.get('entry_score') if r.get('entry_score') is not None else DASH)!s:>6}"
            f"{(f'{chase}/6' if chase is not None else DASH):>6}"
            f"{_n(pl.get('rr_blended'), '.2f'):>6}  {r.get('grade', DASH):<4} "
            f"{r.get('action', '')}"
        )
    return "\n".join(out)


# ── §15 ──────────────────────────────────────────────────────────────────────

def render_regime(regime: dict | None) -> str:
    if not regime:
        return "MARKET REGIME: unavailable"
    lines = [f"{regime['emoji']} MARKET REGIME: {regime['label']}  (score {regime['score']:+d})"]
    for reason in regime.get("reasons", []):
        lines.append(f"    · {reason}")
    if regime.get("unavailable"):
        lines.append(f"    ! not measured: {'; '.join(regime['unavailable'])}")
    return "\n".join(lines)


# ── §17 ──────────────────────────────────────────────────────────────────────

def render_decision(r: dict) -> str:
    """The eight §17 explanations for one actionable setup."""
    pl, room_, cat = r.get("plan", {}), r["room"], r["catalyst"]
    sess, vol = r["session"], r["volatility"]
    direction = r.get("direction", "long")
    L = []

    L.append(f"{r.get('action') or r['decision']}   {r['ticker']} — "
             f"{r.get('name') or ''}".rstrip())
    L.append(f"  {r.get('action_why') or ''}")
    L.append(f"  grade {r['grade']} · CScore {r['confluence']} · setup {r['setup_score']}"
             f" · entry {r.get('entry_score')} ({r.get('entry_grade')})"
             f" · chase {r.get('chase_score')}/6 · tradeability {r['tradeability']}"
             f" · {r['n_confirmations']}/{r['n_checks']} confirmations")
    if r.get("grade_note"):
        L.append(f"  note: {r['grade_note']}")
    gate = r.get("gate") or {}
    blocking = (gate.get("failed") or []) + (gate.get("unverified") or [])
    if blocking:
        L.append(f"  execution gate (§2) blocks A+: {', '.join(blocking)}")
    L.append("")

    L.append("  WHY IT IS MOVING")
    if cat.get("headline"):
        age = f"{cat['age_hours']:.0f}h ago" if cat.get("age_hours") is not None else "undated"
        L.append(f"    {cat['type']} ({age}): {cat['headline']}")
        if cat.get("publisher"):
            L.append(f"    — {cat['publisher']}")
    else:
        L.append(f"    {cat.get('detail')}")
    L.append(f"    Gap {_n(vol.get('gap_pct'), '+.1f', '%')} on RVOL "
             f"{_n(vol.get('rvol'), '.2f')}, {_money(vol.get('dollar_volume'))} traded.")

    L.append("")
    L.append("  WHY IT CAN CONTINUE")
    L.append(f"    {r['setup_detail']}")
    if vol.get("expected_move_pct"):
        L.append(f"    ATR% {_n(vol.get('atr_pct'), '.1f', '%')}, "
                 f"{_n(vol.get('day_range_pct'), '.1f', '%')} range so far — "
                 f"another {vol['expected_move_pct']:.1f}% is a reasonable expectation.")
    rs = r["strength"]
    if rs.get("vs_spy") is not None:
        bits = [f"SPY {rs['vs_spy']:+.1f}pp"]
        if rs.get("vs_iwm") is not None:
            bits.append(f"IWM {rs['vs_iwm']:+.1f}pp")
        if rs.get("vs_sector") is not None:
            bits.append(f"{rs.get('sector_etf')} {rs['vs_sector']:+.1f}pp")
        L.append(f"    Relative strength: {', '.join(bits)}.")
        if rs.get("diverging_from"):
            L.append(f"    Holding up while {', '.join(rs['diverging_from'])} "
                     f"{'is' if len(rs['diverging_from']) == 1 else 'are'} red.")
    if r.get("squeeze_credit"):
        L.append(f"    Squeeze conditions met: {_n(r['supply'].get('short_pct_of_float'), '.1f', '%')} "
                 f"of float short, {_n(r['supply'].get('days_to_cover'), '.1f')} days to cover, "
                 f"with catalyst, volume and a breakout all present (§4).")

    L.append("")
    L.append("  WHAT CONFIRMS THE ENTRY")
    # Direction stated outright: on a short the stop is above the entry and
    # the targets below, which reads as a bug when the side is left implicit.
    L.append(f"    Direction: {direction.upper()}"
             + ("  (stop sits ABOVE entry, targets below)"
                if direction == "short" else ""))
    L.append(f"    Trigger: {pl.get('trigger')}")
    if pl.get("triggered"):
        L.append(f"    Already through the trigger — reference price is {_n(pl.get('entry'))}, "
                 f"not the level. Wait for a pullback or take the next structure.")
    L.append(f"    Entry {_n(pl.get('entry'))} · volume must expand through the level "
             f"({r['volume'].get('sequence')}).")

    L.append("")
    L.append("  WHERE THE STOP GOES")
    L.append(f"    {_n(pl.get('stop'))} — {pl.get('stop_basis')}")
    L.append(f"    Risk/share {_n(pl.get('risk_per_share'), '.3f')} "
             f"({_n(pl.get('risk_pct_of_price'), '.1f', '%')} of entry)")
    if pl.get("stop_too_wide"):
        L.append(f"    ⚠ {pl.get('stop_note')}")

    L.append("")
    L.append("  WHERE THE FIRST TARGET IS")
    L.append(f"    T1 {_n(pl.get('target1'))} ({pl.get('target1_basis')}) — R:R {_n(pl.get('rr'), '.2f')}")
    L.append(f"    T2 {_n(pl.get('target2'))} ({pl.get('target2_basis')}) — R:R {_n(pl.get('rr_target2'), '.2f')}")
    L.append(f"    Blended (half off at T1): {_n(pl.get('rr_blended'), '.2f')}:1 — this is what the grade uses")
    if pl.get("entry_for_2r"):
        L.append(f"    R:R is under 2 here; {_n(pl['entry_for_2r'])} would be the entry that makes it 2:1.")

    L.append("")
    L.append("  WHAT INVALIDATES THE TRADE")
    L.append(f"    {pl.get('invalidation')}")

    L.append("")
    L.append("  WHAT RESISTANCE MUST BE CLEARED")
    if room_.get("nearest"):
        L.append(f"    {room_['nearest']:.2f} — {', '.join(room_.get('nearest_sources', [])[:4])}")
        L.append(f"    {room_.get('detail')}")
    else:
        L.append(f"    {room_.get('detail')}")
    if room_.get("blocked"):
        L.append("    ⚠ §9: significant resistance immediately ahead — do not chase.")

    L.append("")
    L.append("  WHAT WOULD DOWNGRADE THIS SETUP")
    for c in r["confirmations"]:
        if not c["ok"]:
            L.append(f"    ✗ {c['name']}: {c['detail']}")
    losable = [c["name"] for c in r["confirmations"] if c["ok"]][:3]
    if losable:
        L.append(f"    Losing any of: {', '.join(losable)}.")
    for w in r.get("warnings", []):
        L.append(f"    ⚠ {w}")
    L.append(f"    ⚠ {r['supply'].get('dilution_note')}")

    sizing = r.get("sizing", {})
    if sizing.get("shares"):
        L.append("")
        L.append("  POSITION (§14, risk-first)")
        L.append(f"    {sizing['shares']:,} shares · {_money(sizing['position_value'])} "
                 f"({sizing.get('pct_of_capital')}% of capital)")
        L.append(f"    Risk/share {_n(sizing.get('structural_risk_per_share'), '.3f')} "
                 f"structural + {_n(sizing.get('slippage_per_share'), '.3f')} slippage "
                 f"= {_n(sizing.get('true_risk_per_share'), '.3f')}")
        L.append(f"    Risk {_money(sizing['dollar_risk'])} of "
                 f"{_money(sizing['max_dollar_risk'])} allowed")
        L.append(f"    Binding constraint: {sizing['binding_constraint']}"
                 + ("" if sizing.get("risk_is_binding") else
                    f" (risk alone would allow {sizing.get('risk_based_shares'):,})"))
        if sizing.get("position_liquidity_pct") is not None:
            L.append(f"    Position is {sizing['position_liquidity_pct']:.0f}% of a "
                     f"minute's dollar volume")
    return "\n".join(L)


def render_scorecard(r: dict) -> str:
    """The §10 arithmetic for one row, so the confluence number is auditable."""
    L = [f"  {r['ticker']} confluence {r['confluence']} (coverage {r['coverage']:.0%})"]
    for key, weight in WEIGHTS.items():
        raw, earned = r["blocks"].get(key), r["block_points"].get(key)
        L.append(f"    {key:<12} {('—' if raw is None else f'{raw:>3.0f}'):>4}/100"
                 f" → {('—' if earned is None else f'{earned:>4.1f}')}/{weight}")
    if r.get("squeeze_credit"):
        L.append("    squeeze credit +5 (§4 preconditions met)")
    L.append(f"    regime: {r.get('regime_adjustment')}")
    return "\n".join(L)


def render(result: dict, show_scorecards: bool = False) -> str:
    """Full text report."""
    rows, notes = result.get("rows", []), result.get("notes", [])
    # Named from the profiles actually present, not hard-coded: the engine
    # runs three calibrations now, and a large-cap scan headed "SMALL-CAP"
    # is the kind of stale label that makes a reader distrust the rest.
    kinds = sorted({r.get("profile_label") for r in rows if r.get("profile_label")})
    title = (" / ".join(kinds).upper() if kinds else "DAY-TRADE")
    L = ["=" * 100,
         f"{title} DAY-TRADE SCAN — session {result.get('asof')}",
         "=" * 100, ""]
    for n in notes:
        L.append(f"  ! {n}")
    if notes:
        L.append("")
    L.append(render_regime(result.get("regime")))
    L.append("")

    if not rows:
        L.append("No candidates.")
        return "\n".join(L)

    L.append(f"§16 RANKED CANDIDATES ({len(rows)})")
    L.append("")
    L.append(render_compact(rows))
    L.append("")

    # Actionable means "can be acted on now", which is the action, not the
    # grade. A 92-confluence name you must not chase is not a setup to
    # write up; a B+ you can enter is.
    actionable = [r for r in rows if r.get("action") in
                  ("🔥 ENTER NOW", "🟢 WAIT FOR BREAKOUT", "🟢 WAIT FOR PULLBACK")]
    L.append("=" * 100)
    if actionable:
        L.append(f"§17 DECISION ENGINE — {len(actionable)} actionable setup(s)")
        L.append("=" * 100)
        for r in actionable:
            L.append("")
            L.append(render_decision(r))
            L.append("")
            L.append("-" * 100)
    else:
        L.append("§17 DECISION ENGINE — nothing enterable at its current price")
        L.append("=" * 100)
        L.append("")
        L.append("  Nothing is enterable at its current price.")
        L.append("  The top-ranked names and what is stopping each:")
        for r in rows[:5]:
            gate = r.get("gate") or {}
            blocking = (gate.get("failed") or []) + (gate.get("unverified") or [])
            L.append("")
            L.append(f"    {r['ticker']:<7} {r.get('grade', '—'):<4} {r.get('action', '')}")
            L.append(f"      {r.get('action_why') or 'below threshold'}")
            if blocking:
                L.append(f"      execution: {', '.join(blocking[:6])}")

    if show_scorecards:
        L.append("")
        L.append("=" * 100)
        L.append("§10 SCORECARDS")
        L.append("=" * 100)
        for r in rows:
            L.append("")
            L.append(render_scorecard(r))
    return "\n".join(L)
