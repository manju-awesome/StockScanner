"""
leaders_confluence.py
=====================
Cross-references the pre-market brief against the sector-leader scan and
reports only where the two agree — or contradict each other.

Why this exists as its own layer
---------------------------------
The two inputs answer different questions on different clocks. The brief is
this morning, live: what gapped, what reports today, what is sitting on a
level. The sector-leader scan is last night's close: which sectors carry the
trend, which names lead them, and where the setups actually are. Neither is
worth much alone before the bell — a pre-market gap with no structural trend
behind it is noise, and a beautiful daily setup that is gapping the wrong way
is not the trade the scan priced.

The overlap is the whole point, and it cuts both ways:

  ALIGNED    a pre-market gainer that is also a ranked bullish leader, or a
             loser that is a ranked bearish one. The gap is moving in the
             direction the structure already pointed.

  CONFLICT   a gainer that is a ranked BEARISH leader, or a loser that is a
             ranked BULLISH one. These are more useful than the aligned
             names, because they are the ones a single-source read gets
             wrong: shorting a name that is gapping up on news, or buying a
             pullback that is actually the thesis breaking.

Sector agreement is matched on ETF symbol, never on label. The brief calls
XLV "Health" and the scan calls it "Healthcare"; string-matching those would
work until the day it silently stopped.

Nothing here fetches. It reads the brief dict and the stored scan snapshot,
and it is the only place that knows how to line the two up.
"""

from __future__ import annotations

# A gap smaller than this is not a pre-market move worth cross-referencing —
# it is the spread. Pre-market prints on thin books are noisy, and matching
# on every 0.4% wiggle would fill the confluence table with names whose gap
# will be gone by 09:31.
MIN_GAP_PCT = 1.0

# How far down each ranking a name can sit and still count as "a leader".
# Beyond this it is a name the scan scored, not one it is pointing at.
LEADER_DEPTH = 25


def _ranked(snap: dict, direction: str) -> list:
    """Confidence-ranked leaders for one side — the same rows the /leaders
    page shows, built by core.sector_leaders.scored_rows so the email and the
    page can never disagree about who the leaders are."""
    from stockanalysis.core.sector_leaders import scored_rows
    return scored_rows(snap, direction, LEADER_DEPTH)


def _movers(brief: dict) -> list:
    """Every pre-market name the brief carries, deduped, gap-filtered.

    gainers/losers/unusual_movers overlap heavily — the same name routinely
    appears in two of the three — so they are merged rather than concatenated.
    """
    seen, out = {}, []
    for key in ("gainers", "losers", "unusual_movers"):
        for m in brief.get(key) or []:
            t = m.get("ticker")
            if not t or t in seen:
                continue
            if abs(m.get("chg_pct") or 0) < MIN_GAP_PCT:
                continue
            seen[t] = True
            out.append(m)
    out.sort(key=lambda m: -abs(m.get("chg_pct") or 0))
    return out


def _summarise(c: dict, mover: dict, kind: str, direction: str) -> dict:
    setup = c.get("setup") or {}
    lv = setup.get("levels") or {}
    conf = c.get("confidence") or {}
    return {
        "ticker": c.get("ticker"),
        "kind": kind,
        "direction": direction,
        "gap_pct": mover.get("chg_pct"),
        "catalyst": mover.get("catalyst"),
        "headline": (mover.get("headline") or "")[:160],
        "group": c.get("group"),
        "sector_direction": c.get("sector_direction"),
        "sector_score": c.get("sector_score"),
        "confluence": (c.get("confluence") or {}).get("score"),
        "confidence": conf.get("score"),
        "confidence_label": conf.get("label"),
        "leadership": (c.get("leadership") or {}).get("score"),
        "leadership_band": (c.get("leadership") or {}).get("band"),
        "clarity": (c.get("clarity") or {}).get("score"),
        "setup": setup.get("setup"),
        "grade": setup.get("grade"),
        "rr": setup.get("rr"),
        "entry_low": lv.get("entry_low"), "entry_high": lv.get("entry_high"),
        "stop": lv.get("stop"), "target1": lv.get("target1"),
        "has_entry": bool(lv),
        "note": _note(kind, direction, c, mover),
    }


def _note(kind, direction, c, mover) -> str:
    setup = c.get("setup") or {}
    gap = mover.get("chg_pct") or 0
    if kind == "aligned" and direction == "long":
        if not (setup.get("levels")):
            return ("gapping further away from an already-extended chart — "
                    "the scan priced no entry here yesterday and the gap "
                    "makes that worse, not better")
        return ("gap agrees with the daily structure; the scan's entry zone "
                "is the reference, not the pre-market print")
    if kind == "aligned" and direction == "short":
        return ("gapping down out of a bearish structure — check the entry "
                "zone still sits above price before treating it as live")
    if kind == "conflict" and direction == "short":
        return (f"gapping +{gap:.1f}% against a bearish daily structure — "
                f"squeeze risk. Do not short into this without a reason the "
                f"gap is wrong")
    if kind == "conflict" and direction == "long":
        return (f"gapping {gap:.1f}% against a bullish daily structure — "
                f"either the pullback the scan wanted, or the thesis "
                f"breaking. The catalyst decides which")
    return ""


def compute(brief: dict, snap: dict | None) -> dict:
    """Everything the combined email needs. Never raises: a missing snapshot
    yields an empty result with a reason, not an exception."""
    if not snap:
        return {"available": False,
                "reason": "no sector-leader scan snapshot to cross-reference",
                "aligned": [], "conflicts": [], "sector_agreement": [],
                "earnings_collisions": [], "level_confluence": []}

    longs = {c["ticker"]: c for c in _ranked(snap, "long")}
    shorts = {c["ticker"]: c for c in _ranked(snap, "short")}

    aligned, conflicts = [], []
    for m in _movers(brief):
        t, gap = m.get("ticker"), m.get("chg_pct") or 0
        up = gap > 0
        if up and t in longs:
            aligned.append(_summarise(longs[t], m, "aligned", "long"))
        elif not up and t in shorts:
            aligned.append(_summarise(shorts[t], m, "aligned", "short"))
        elif up and t in shorts:
            conflicts.append(_summarise(shorts[t], m, "conflict", "short"))
        elif not up and t in longs:
            conflicts.append(_summarise(longs[t], m, "conflict", "long"))

    aligned.sort(key=lambda r: -(r.get("confidence") or 0))
    conflicts.sort(key=lambda r: -abs(r.get("gap_pct") or 0))

    # ── sector agreement, matched on ETF symbol ─────────────────────────
    scan_sectors = {s.get("etf"): s for s in (snap.get("sectors") or [])}
    sector_agreement = []
    for bucket, want in (("sectors_trending", "bullish"),
                         ("sectors_lagging", "bearish")):
        for s in brief.get(bucket) or []:
            hit = scan_sectors.get(s.get("ticker"))
            if not hit:
                continue
            agrees = hit.get("direction") == want
            sector_agreement.append({
                "etf": s.get("ticker"),
                "premarket_label": s.get("label"),
                "premarket_chg_pct": s.get("chg_pct"),
                "scan_name": hit.get("name"),
                "scan_score": (hit.get("scores") or {}).get("score"),
                "scan_direction": hit.get("direction"),
                "scan_trend": (hit.get("scores") or {}).get("quality_label"),
                "premarket_side": want,
                "agrees": agrees,
            })
    sector_agreement.sort(key=lambda r: (not r["agrees"],
                                         -(r.get("scan_score") or 0)))

    # ── a confluence name that reports today is an event, not a setup ───
    earnings_today = {e.get("ticker") for e in (brief.get("earnings_today") or [])}
    collisions = [r for r in aligned + conflicts if r["ticker"] in earnings_today]

    # ── alert levels that a ranked leader is already sitting on ─────────
    level_confluence = []
    for a in brief.get("near_breakout") or []:
        t = a.get("ticker")
        c = longs.get(t) or shorts.get(t)
        if not c:
            continue
        level_confluence.append({
            "ticker": t,
            "headline": a.get("headline"),
            "direction": c.get("direction"),
            "confluence": (c.get("confluence") or {}).get("score"),
            "setup": (c.get("setup") or {}).get("setup"),
        })

    return {
        "available": True,
        "scan_generated": snap.get("generated"),
        "market": (snap.get("market") or {}).get("label"),
        "market_score": (snap.get("market") or {}).get("score"),
        "aligned": aligned,
        "conflicts": conflicts,
        "sector_agreement": sector_agreement,
        "earnings_collisions": collisions,
        "level_confluence": level_confluence,
        "counts": {"aligned": len(aligned), "conflicts": len(conflicts),
                   "sector_agree": sum(1 for s in sector_agreement if s["agrees"]),
                   "sector_disagree": sum(1 for s in sector_agreement if not s["agrees"])},
    }


# ── rendering ───────────────────────────────────────────────────────────────

def _levels_str(r: dict) -> str:
    if not r.get("has_entry"):
        return "no entry priced"
    return (f'entry {r["entry_low"]}–{r["entry_high"]} · stop {r["stop"]} · '
            f'T1 {r["target1"]} · R:R {r["rr"]}')


def render_text(conf: dict) -> str:
    if not conf.get("available"):
        return f"\nSECTOR-LEADER CONFLUENCE\n  {conf.get('reason')}\n"

    lines = ["", "SECTOR-LEADER CONFLUENCE",
             f"  scan: {conf.get('market')} ({conf.get('market_score'):+.1f}) "
             f"— {str(conf.get('scan_generated'))[:16].replace('T', ' ')}"]

    lines.append(f"\n  ALIGNED ({len(conf['aligned'])}) — gap agrees with the daily structure")
    for r in conf["aligned"][:8] or []:
        lines.append(f"    {r['ticker']} {r['direction'].upper()} {r['gap_pct']:+.1f}% pre — "
                     f"{r['group']} · confidence {r['confidence']:.0f} "
                     f"({str(r['confidence_label']).split('—')[0].strip()}) · "
                     f"{r['setup']} [{r['grade']}]")
        lines.append(f"      {_levels_str(r)}")
        lines.append(f"      {r['note']}")
    if not conf["aligned"]:
        lines.append("    none")

    lines.append(f"\n  CONFLICTS ({len(conf['conflicts'])}) — gap fights the daily structure")
    for r in conf["conflicts"][:8] or []:
        lines.append(f"    {r['ticker']} {r['gap_pct']:+.1f}% pre vs a "
                     f"{r['direction']} ranking — {r['group']}")
        lines.append(f"      {r['note']}")
    if not conf["conflicts"]:
        lines.append("    none")

    if conf["sector_agreement"]:
        lines.append("\n  SECTOR AGREEMENT (pre-market move vs scan ranking)")
        for s in conf["sector_agreement"][:8]:
            mark = "agrees" if s["agrees"] else "DISAGREES"
            lines.append(f"    {s['etf']} {s['premarket_chg_pct']:+.2f}% pre — scan "
                         f"{s['scan_name']} {s['scan_score']:.0f}/100 "
                         f"{s['scan_trend']} → {mark}")

    if conf["earnings_collisions"]:
        lines.append("\n  ⚠️  REPORTS TODAY — these are events, not setups")
        for r in conf["earnings_collisions"]:
            lines.append(f"    {r['ticker']} ({r['group']})")

    if conf["level_confluence"]:
        lines.append("\n  SITTING ON A LEVEL")
        for r in conf["level_confluence"][:6]:
            lines.append(f"    {r['ticker']} — {r['headline']} · {r['setup']}")
    return "\n".join(lines) + "\n"


def render_html(conf: dict) -> str:
    if not conf.get("available"):
        return (f'<div style="margin-bottom:14px"><h3 style="font-size:14px;'
                f'margin:0 0 6px">Sector-leader confluence</h3>'
                f'<div style="font-size:12px;color:#8a6d1a">'
                f'{conf.get("reason")}</div></div>')

    def chip(text, bg, fg):
        return (f'<span style="background:{bg};color:{fg};font-size:11px;'
                f'padding:2px 8px;border-radius:10px;margin-right:5px">'
                f'{text}</span>')

    def block(r, accent):
        return (
            f'<div style="border-left:3px solid {accent};padding:5px 0 5px 9px;'
            f'margin-bottom:8px">'
            f'<div style="font-size:13px"><b>{r["ticker"]}</b> '
            f'<span style="color:#898781">{r["group"]}</span> · '
            f'{r["gap_pct"]:+.1f}% pre-market</div>'
            f'<div style="font-size:12px">confidence <b>{r["confidence"]:.0f}</b>'
            f' · leadership {r["leadership"]:.0f} · {r["setup"]} '
            f'[{r["grade"]}]</div>'
            f'<div style="font-size:12px;color:#444441">{_levels_str(r)}</div>'
            f'<div style="font-size:11px;color:#898781">{r["note"]}</div>'
            f'</div>')

    aligned = "".join(block(r, "#0F6E56" if r["direction"] == "long" else "#A32D2D")
                      for r in conf["aligned"][:8]) or \
        '<div style="font-size:12px;color:#898781">none — no pre-market mover ' \
        'is also a ranked leader on the same side</div>'
    conflicts = "".join(block(r, "#8a6d1a") for r in conf["conflicts"][:8]) or \
        '<div style="font-size:12px;color:#898781">none</div>'

    sectors = "".join(
        f'<div style="font-size:12px;margin-bottom:2px">'
        f'<b>{s["etf"]}</b> {s["premarket_chg_pct"]:+.2f}% pre — scan '
        f'{s["scan_name"]} {s["scan_score"]:.0f}/100 {s["scan_trend"]} '
        + (chip("agrees", "#E1F5EE", "#085041") if s["agrees"]
           else chip("disagrees", "#FCEBEB", "#791F1F"))
        + '</div>'
        for s in conf["sector_agreement"][:8])

    warn = ""
    if conf["earnings_collisions"]:
        names = ", ".join(r["ticker"] for r in conf["earnings_collisions"])
        warn = (f'<div style="background:#FCEBEB;color:#791F1F;font-size:12px;'
                f'padding:7px 10px;border-radius:6px;margin-bottom:10px">'
                f'<b>Reports today:</b> {names} — these are events, not '
                f'setups. The scan cannot see an earnings date in the bars.'
                f'</div>')

    levels = "".join(
        f'<div style="font-size:12px">• <b>{r["ticker"]}</b> {r["headline"]} '
        f'· {r["setup"]}</div>' for r in conf["level_confluence"][:6])

    return f"""
      <div style="margin-bottom:16px;border-top:2px solid #eceae4;padding-top:12px">
        <h3 style="font-size:15px;margin:0 0 4px">Sector-leader confluence</h3>
        <div style="font-size:11px;color:#898781;margin-bottom:8px">
          Scan {conf.get('market')} ({conf.get('market_score'):+.1f}) ·
          {str(conf.get('scan_generated'))[:16].replace('T', ' ')}.
          Where this morning's gaps agree with last night's structure — and
          where they fight it.
        </div>
        {warn}
        <div style="font-size:13px;font-weight:600;margin:8px 0 4px">
          Aligned ({conf['counts']['aligned']})</div>
        {aligned}
        <div style="font-size:13px;font-weight:600;margin:12px 0 4px">
          Conflicts ({conf['counts']['conflicts']}) — the ones a single-source
          read gets wrong</div>
        {conflicts}
        {f'<div style="font-size:13px;font-weight:600;margin:12px 0 4px">Sector agreement</div>{sectors}' if sectors else ''}
        {f'<div style="font-size:13px;font-weight:600;margin:12px 0 4px">Sitting on a level</div>{levels}' if levels else ''}
      </div>"""
