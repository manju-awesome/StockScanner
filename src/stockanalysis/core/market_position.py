"""
market_position.py
==================
Competitive standing of each research-library name inside its own industry:
is it the dominant player, half of a duopoly, or one of many?

Two layers, deliberately separated:

1. **Computed** — market-cap rank and share of peer cap within the ticker's
   yfinance ``industry`` (falling back to ``sector``), across every entry in
   research_index.json. Objective, always fresh, and labelled for exactly
   what it measures: rank among *tracked* names, by market cap.

2. **Overlay** — data/market_structure.json, hand-maintained, where a real
   structural fact can override the computed guess ("EUV monopoly").

The split matters because market cap is a proxy for size, not market share,
and the two diverge precisely on the interesting names: ASML is the sole
supplier of EUV lithography yet ranks below Applied Materials by market cap.
The computed layer would call that "#2"; only the overlay can call it a
monopoly. Nothing is seeded into the overlay by default — an unverified
market-share claim in a trading tool is worse than no claim.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

OVERLAY_FILENAME = "market_structure.json"

# Share of tracked peer market cap above which #1 is called "Dominant".
DOMINANT_SHARE = 50.0
# Combined top-2 share above which the top two *may* be called a "Duopoly".
DUOPOLY_TOP2_SHARE = 65.0
# ...but only if the runner-up is itself substantial. Without this floor a
# lone giant drags the pair over the line and its distant #2 gets mislabelled
# a duopolist — MSFT 71.4% + ORCL 7.8% is a dominant player and an also-ran,
# not a duopoly.
DUOPOLY_MIN_SECOND_SHARE = 20.0
# Concentration labels need a real peer group behind them; below this only a
# bare "#N of M" rank is reported.
#
# Deliberately high. At 4-5 peers a single ticker dropping out swings the
# leader's share by ~10 points: APH read "Duopoly /6" and then "Dominant /5"
# on the same day, purely because a peer lost its market cap and left the
# group — nothing changed at Amphenol. A word like "Dominant" over a handful
# of tracked names describes the sample, not the market, so small groups get
# a rank and a visible denominator instead.
MIN_PEERS_FOR_TIER = 8
# A rank needs someone to rank against. "#1 of 1" is not a finding — it means
# nothing else in the library shares this industry — and rendering it next to
# genuine "#1 of 19" invites reading it as dominance.
MIN_PEERS_FOR_RANK = 2


def _overlay_path() -> Path:
    from stockanalysis.reporting.research import PROJECT_DATA_DIR
    return PROJECT_DATA_DIR / OVERLAY_FILENAME


def load_market_structure() -> dict:
    """Hand-maintained ticker -> {"structure", "note"} overrides.
    Keys starting with "_" are documentation, not tickers."""
    path = _overlay_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log.warning("market_structure.json unreadable (%s) — ignoring", e)
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str):
            v = {"structure": v}
        if isinstance(v, dict) and v.get("structure"):
            out[k.upper()] = {"structure": str(v["structure"]),
                              "note": str(v.get("note") or "") or None}
    return out


def _group_of(entry: dict) -> str | None:
    """Industry when yfinance has one, else the broader sector."""
    industry = (entry.get("raw") or {}).get("Industry")
    if isinstance(industry, str) and industry.strip():
        return industry.strip()
    sector = entry.get("sector")
    if isinstance(sector, str) and sector.strip() and sector.strip() not in (
            "Unknown", "N/A"):
        return sector.strip()
    return None


def compute_peer_positions(entries: list[dict] | dict) -> dict[str, dict]:
    """
    entries : research_index.json values (or the whole dict)

    Returns {ticker: {...}} with, per ticker:
        peer_group           industry (or sector) the rank is computed in
        peer_group_is_sector True when it fell back to the broad sector
        peer_rank            1-based rank by market cap among tracked peers
        peer_count           peers with a usable market cap
        peer_share_pct       this name's % of tracked peer market cap
        peer_top2_share_pct  combined % held by the group's top two
        position_label       "Dominant" / "Duopoly" / "#2" / "#7"
        position_tier        dominant | duopoly | top2 | rest
        structure            overlay override, if any
        structure_note       overlay free-text note
    Tickers with no group or no market cap get an entry carrying only the
    overlay fields, so a curated fact still shows for an unrankable name.
    """
    if isinstance(entries, dict):
        entries = list(entries.values())
    overlay = load_market_structure()

    groups: dict[str, list[tuple[str, float]]] = {}
    meta: dict[str, dict] = {}
    for e in entries:
        t = e.get("ticker")
        if not t:
            continue
        group = _group_of(e)
        cap = e.get("market_cap")
        meta[t] = {"group": group,
                   "is_sector": bool(group) and group == e.get("sector")
                   and not (e.get("raw") or {}).get("Industry")}
        try:
            cap = float(cap) if cap is not None else None
        except (TypeError, ValueError):
            cap = None
        if group and cap and cap > 0:
            groups.setdefault(group, []).append((t, cap))

    ranked: dict[str, dict] = {}
    for group, members in groups.items():
        if len(members) < MIN_PEERS_FOR_RANK:
            continue                      # nothing to rank against
        members.sort(key=lambda m: -m[1])
        total = sum(c for _, c in members)
        count = len(members)
        top2 = sum(c for _, c in members[:2])
        top2_share = (top2 / total * 100) if total else None
        first_share = (members[0][1] / total * 100) if total else None
        second_share = ((members[1][1] / total * 100)
                        if total and count >= 2 else None)
        # Both halves must be real players, and a Dominant #1 rules it out.
        is_duopoly = (
            top2_share is not None and top2_share >= DUOPOLY_TOP2_SHARE
            and second_share is not None
            and second_share >= DUOPOLY_MIN_SECOND_SHARE
            and (first_share is None or first_share < DOMINANT_SHARE))
        for i, (t, cap) in enumerate(members, start=1):
            share = (cap / total * 100) if total else None
            tier, label = "rest", f"#{i}"
            if count >= MIN_PEERS_FOR_TIER:
                if i == 1 and share is not None and share >= DOMINANT_SHARE:
                    tier, label = "dominant", "Dominant"
                elif i <= 2 and is_duopoly:
                    tier, label = "duopoly", "Duopoly"
                elif i <= 2:
                    tier, label = "top2", f"#{i}"
            ranked[t] = {
                "peer_group": group,
                "peer_group_is_sector": meta.get(t, {}).get("is_sector", False),
                "peer_rank": i,
                "peer_count": count,
                "peer_share_pct": round(share, 1) if share is not None else None,
                "peer_top2_share_pct": (round(top2_share, 1)
                                        if top2_share is not None else None),
                "position_label": label,
                "position_tier": tier,
                # Who the rank is actually against — "#1 of 23" is only
                # interpretable once you can see the 23. Rank order, self
                # excluded, as a string so it reads straight out of the
                # Detailed Metrics table.
                "peer_names": ", ".join(o for o, _ in members if o != t),
            }

    out: dict[str, dict] = {}
    for t, m in meta.items():
        rec = ranked.get(t) or {
            "peer_group": m["group"], "peer_group_is_sector": m["is_sector"],
            "peer_rank": None, "peer_count": 0, "peer_share_pct": None,
            "peer_top2_share_pct": None, "position_label": None,
            "position_tier": None, "peer_names": "",
        }
        ov = overlay.get(t.upper())
        rec["structure"] = ov["structure"] if ov else None
        rec["structure_note"] = ov.get("note") if ov else None
        out[t] = rec
    return out


def attach_peer_positions(rows: list[dict], entries=None) -> list[dict]:
    """Merge compute_peer_positions() output into `rows` in place. `entries`
    defaults to `rows` — pass the full index when ranking a filtered subset
    so the peer set stays stable."""
    positions = compute_peer_positions(entries if entries is not None else rows)
    for r in rows:
        r.update(positions.get(r.get("ticker")) or {})
    return rows
