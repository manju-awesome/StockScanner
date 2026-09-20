"""
risk_view.py
============
Renders the portfolio risk report (core/portfolio_risk_scores.analyze_portfolio)
into the Portfolio page's cards. Presentation only — it reads the cached
report dict and returns HTML, and every number it shows was computed upstream.

Split out of pages.py because that file is already the biggest in the app and
this report is a dozen tables on its own. The one rule worth keeping when
editing: if you find yourself computing a ratio here, it belongs in
portfolio_risk_scores.py instead, where it can be tested.
"""

from __future__ import annotations

from .views import badge, card, empty, esc, fmt_money, fmt_pct, tv_url

# Traffic lights and severities share one palette so a 🔴 in the dashboard and
# a "critical" row in the violations table read as the same thing.
_SEV_COLOR = {"critical": "#791F1F", "high": "#A32D2D", "moderate": "#633806"}
_ACTION_COLOR = {"EXIT": "#791F1F", "TRIM": "#A32D2D", "CLOSE": "#898781",
                 "MAINTAIN": "#0b0b0b", "INCREASE": "#0F6E56"}


def _num(v, nd=1, suffix=""):
    if v is None:
        return "—"
    return f"{v:,.{nd}f}{suffix}"


def _signed_pct(v, nd=2):
    if v is None:
        return '<span style="color:#898781">—</span>'
    color = "#0F6E56" if v >= 0 else "#A32D2D"
    return f'<span style="color:{color}">{v:+,.{nd}f}%</span>'


def _signed_money(v):
    if v is None:
        return '<span style="color:#898781">—</span>'
    color = "#0F6E56" if v >= 0 else "#A32D2D"
    return f'<span style="color:{color}">{"+" if v >= 0 else "−"}${abs(v):,.0f}</span>'


def _score_bar(label, value, weight=None, invert=False):
    """One 0-100 component bar. `invert` flips the colour ramp for the risk
    score, where a high number is bad."""
    v = max(0.0, min(100.0, float(value or 0)))
    good = (100 - v) if invert else v
    color = "#0F6E56" if good >= 70 else "#B8730E" if good >= 45 else "#A32D2D"
    w = f' <span style="color:#898781;font-size:10px">×{weight:.0%}</span>' if weight else ""
    return f"""
    <div style="display:flex;align-items:center;gap:8px;margin:3px 0">
      <span style="min-width:132px;font-size:11px">{esc(label)}{w}</span>
      <div style="flex:1;background:#f1efea;border-radius:3px;height:9px">
        <div style="width:{max(2, v)}%;background:{color};height:9px;border-radius:3px"></div></div>
      <span style="min-width:34px;text-align:right;font-size:11px;font-weight:600">{v:.0f}</span>
    </div>"""


def _alloc_bars(pcts, cap=None, limit_label=""):
    """Allocation bars with the limit drawn in. Seeing the cap next to the bar
    is the difference between "Technology 50%" and "Technology 50%, twice the
    cap" — the second one is a decision."""
    if not pcts:
        return empty("—")
    peak = max((p for _, p in pcts), default=1) or 1
    rows = []
    for k, p in pcts:
        over = cap is not None and p > cap
        color = "#A32D2D" if over else "#185FA5"
        marker = ""
        if cap and peak:
            left = min(100, cap / peak * 100)
            marker = (f'<div style="position:absolute;left:{left}%;top:-2px;bottom:-2px;'
                      f'width:2px;background:#0b0b0b;opacity:.45"></div>')
        rows.append(
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
            f'<span style="min-width:150px;font-size:11px">{esc(str(k))}</span>'
            f'<div style="flex:1;background:#f1efea;border-radius:3px;height:9px;position:relative">'
            f'<div style="width:{max(2, round(p / peak * 100))}%;background:{color};height:9px;border-radius:3px"></div>'
            f'{marker}</div>'
            f'<span style="min-width:46px;text-align:right;font-size:11px;font-weight:600;'
            f'color:{"#A32D2D" if over else "#0b0b0b"}">{p:.1f}%</span></div>')
    note = (f'<div style="font-size:10px;color:#898781;margin-top:6px">'
            f'▏marker = {esc(limit_label)} limit {cap:g}%</div>' if cap else "")
    return "".join(rows) + note


# ─────────────────────────────────────────────────────────────────────────────
# CARDS
# ─────────────────────────────────────────────────────────────────────────────

def _header_card(r: dict) -> str:
    """Health score, executive narrative, and the basis every number rests on."""
    h, risk, totals = r["health"], r["risk"], r["totals"]
    s = h["score"]
    ring = ("#0F6E56" if s >= 80 else "#B8730E" if s >= 70
            else "#C2410C" if s >= 60 else "#A32D2D")

    tiles = []
    for label, value, sub in (
        ("Portfolio Value", fmt_money(totals["portfolio_value"], 0), totals.get("value_basis", "")[:38]),
        ("Equity at Market", fmt_money(totals["equity_value"], 0), f'{totals["positions"]} positions'),
        ("Option Delta Exposure", fmt_money(totals["option_delta_value"], 0),
         f'{totals["contracts"]} contracts · est.'),
        ("Gross Exposure", f'{totals.get("gross_exposure_pct")}%', "of invested capital"),
        ("Cash", fmt_money(totals["cash"], 0), f'{totals.get("cash_pct")}%'),
        ("Risk Score", f'{risk["score"]:.0f}/100', f'{risk["band"]} · higher = riskier'),
    ):
        tiles.append(
            f'<div><div style="font-size:11px;color:#898781">{label}</div>'
            f'<div style="font-size:19px;font-weight:650">{value}</div>'
            f'<div style="font-size:10px;color:#898781">{esc(sub)}</div></div>')

    leverage_note = ""
    if (totals.get("gross_exposure_pct") or 0) > 105:
        leverage_note = (
            f'<div style="background:#FEF6E7;border-left:3px solid #B8730E;padding:8px 12px;'
            f'border-radius:6px;margin-top:12px;font-size:11px">'
            f'<b>Leveraged book.</b> Gross exposure is {totals["gross_exposure_pct"]}% of '
            f'invested capital because option delta ({fmt_money(totals["option_delta_value"], 0)}) '
            f'is far larger than the premium paid ({fmt_money(totals["option_market_value"], 0)}). '
            f'Weights below can exceed 100% — that is the leverage, not an error. '
            f'Delta exposure is what moves with the market; premium is what you can lose.</div>')

    body = f"""
    <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">
      <div style="text-align:center;min-width:130px">
        <div style="font-size:44px;font-weight:700;color:{ring};line-height:1">{s:.0f}</div>
        <div style="font-size:12px;font-weight:600">{h["light"]} {esc(h["band"])}</div>
        <div style="font-size:10px;color:#898781">Portfolio Health / 100</div>
      </div>
      <div style="flex:1;min-width:280px;font-size:12px;line-height:1.65">{esc(r["summary"]["narrative"])}</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px;margin-top:18px">
      {''.join(tiles)}
    </div>{leverage_note}
    <div style="font-size:10px;color:#898781;margin-top:12px">
      Generated {esc(r.get("generated_at", "")[:19])} · prices as of {esc(str(r.get("as_of")))} ·
      risk-free {r.get("risk_free_rate")}% · limits configurable via PR_LIMIT_* in .env
    </div>"""
    return card("Executive Summary", body, "🏛")


def _dashboard_card(r: dict) -> str:
    rows = "".join(
        f'<tr><td style="font-size:16px">{t["light"]}</td>'
        f'<td><b>{esc(t["area"])}</b></td>'
        f'<td>{esc(t["value"])}</td>'
        f'<td style="font-size:11px;color:#898781">{esc(t["note"])}</td></tr>'
        for t in r["traffic_lights"])
    legend = ('<div style="font-size:10px;color:#898781;margin-top:8px">'
              '🟢 healthy · 🟡 monitor · 🔴 immediate action · ⚪ no data</div>')
    return card("Traffic Light Dashboard",
                f'<table><tbody>{rows}</tbody></table>{legend}', "🚦")


def _scores_card(r: dict) -> str:
    div, risk, health = r["diversification"], r["risk"], r["health"]
    left = ("".join(_score_bar(k, v, health["weights"].get(k))
                    for k, v in health["components"].items()))
    mid = "".join(_score_bar(k, v) for k, v in div["components"].items())
    right = "".join(_score_bar(k, v, invert=True) for k, v in risk["components"].items())
    body = f"""
    <div style="display:flex;gap:26px;flex-wrap:wrap">
      <div style="flex:1;min-width:260px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:6px">
          HEALTH {health["score"]:.0f}/100 — higher is better</div>{left}</div>
      <div style="flex:1;min-width:260px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:6px">
          DIVERSIFICATION {div["score"]:.0f}/100 — higher is better</div>{mid}</div>
      <div style="flex:1;min-width:260px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:6px">
          RISK {risk["score"]:.0f}/100 — <b>higher is riskier</b></div>{right}</div>
    </div>"""
    return card("Scores", body, "📐")


def _risks_card(r: dict) -> str:
    s = r["summary"]

    def block(title, items, key, color):
        if not items:
            return (f'<div style="flex:1;min-width:280px"><div style="font-size:11px;'
                    f'font-weight:600;color:#898781;margin-bottom:6px">{title}</div>'
                    f'{empty("none identified")}</div>')
        lis = "".join(
            f'<div style="border-left:3px solid {_SEV_COLOR.get(i.get("severity"), color)};'
            f'padding:6px 10px;margin-bottom:8px;background:#faf9f7;border-radius:0 6px 6px 0">'
            f'<div style="font-size:12px;font-weight:600">{esc(i["headline"])}</div>'
            f'<div style="font-size:11px;color:#5c5a55;margin-top:2px">{esc(i[key])}</div></div>'
            for i in items)
        return (f'<div style="flex:1;min-width:280px"><div style="font-size:11px;'
                f'font-weight:600;color:#898781;margin-bottom:6px">{title}</div>{lis}</div>')

    body = (f'<div style="display:flex;gap:24px;flex-wrap:wrap">'
            f'{block("TOP RISKS", s["top_risks"], "detail", "#A32D2D")}'
            f'{block("TOP OPPORTUNITIES", s["top_opportunities"], "detail", "#0F6E56")}'
            f'</div>')
    return card("Top Risks & Opportunities", body, "⚠")


def _violations_card(r: dict) -> str:
    v = r["violations"]
    if not v:
        return card("Limit Compliance",
                    '<div style="color:#0F6E56;font-size:12px">✓ Every configured limit '
                    'is within tolerance.</div>', "✅")
    rows = "".join(f"""
      <tr>
        <td><span style="color:{_SEV_COLOR.get(x["severity"], "#0b0b0b")};font-weight:650;
            font-size:11px;text-transform:uppercase">{esc(x["severity"])}</span></td>
        <td><b>{esc(x["limit"].title())}</b></td>
        <td style="font-size:11px">{esc(x["scope"])}</td>
        <td style="text-align:right;font-weight:600;color:{_SEV_COLOR.get(x["severity"])}">{x["actual"]:.1f}%</td>
        <td style="text-align:right;color:#898781">{x["cap"]:g}%</td>
        <td style="text-align:right;color:#A32D2D">+{x["over_by"]:.1f}</td>
        <td style="font-size:11px;color:#5c5a55">{esc(x["risk"])}</td>
      </tr>""" for x in v)
    return card(f"Limit Violations ({len(v)})", f"""
      <div style="overflow-x:auto"><table><thead><tr>
        <th>Severity</th><th>Limit</th><th>Scope</th><th style="text-align:right">Actual</th>
        <th style="text-align:right">Cap</th><th style="text-align:right">Over</th>
        <th>Why it matters</th></tr></thead><tbody>{rows}</tbody></table></div>""", "🚨")


def _concentration_card(r: dict) -> str:
    c, limits = r["concentration"], r["limits"]
    mp = c.get("max_position") or {"ticker": "—", "pct": 0}

    def tile(label, value, cap=None):
        over = cap is not None and isinstance(value, (int, float)) and value > cap
        val = f"{value:.1f}%" if isinstance(value, (int, float)) else value
        return (f'<div><div style="font-size:11px;color:#898781">{label}</div>'
                f'<div style="font-size:19px;font-weight:650;'
                f'color:{"#A32D2D" if over else "#0b0b0b"}">{val}</div>'
                + (f'<div style="font-size:10px;color:#898781">cap {cap:g}%</div>' if cap else "")
                + "</div>")

    tiles = (tile("Max Position", mp["pct"], limits["single_position"])
             + tile("Top 5", c["top_5"], limits["top_5"])
             + tile("Top 10", c["top_10"], limits["top_10"])
             + f'<div><div style="font-size:11px;color:#898781">Effective N</div>'
               f'<div style="font-size:19px;font-weight:650">{c["effective_n"]}</div>'
               f'<div style="font-size:10px;color:#898781">of {c["positions"]} positions</div></div>'
             + f'<div><div style="font-size:11px;color:#898781">Beta-Weighted</div>'
               f'<div style="font-size:19px;font-weight:650">{c["beta_weighted_exposure"]:.2f}</div>'
               f'<div style="font-size:10px;color:#898781">vs SPY</div></div>'
             + f'<div><div style="font-size:11px;color:#898781">Vol-Weighted</div>'
               f'<div style="font-size:19px;font-weight:650">{c["vol_weighted_exposure"]:.0f}%</div>'
               f'<div style="font-size:10px;color:#898781">90d realized</div></div>')

    max_note = (f'<div style="font-size:11px;color:#898781;margin-top:8px">'
                f'Largest position: <b>{esc(mp["ticker"])}</b> at {mp["pct"]:.1f}%. '
                f'Top 5: {esc(", ".join(c["top_5_names"]))}</div>')

    grids = f"""
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:18px">
      <div style="flex:1;min-width:280px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">SECTOR</div>
        {_alloc_bars(c["sectors"], limits["sector"], "sector")}</div>
      <div style="flex:1;min-width:280px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">THEME</div>
        {_alloc_bars([t for t in c["themes"] if t[0] != "Untagged"], limits["theme"], "theme")}</div>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:14px">
      <div style="flex:1;min-width:280px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">INDUSTRY</div>
        {_alloc_bars(c["industries"][:10], limits["industry"], "industry")}</div>
      <div style="flex:1;min-width:280px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">MARKET CAP</div>
        {_alloc_bars(c["caps"])}</div>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:14px">
      <div style="flex:1;min-width:280px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">COUNTRY</div>
        {_alloc_bars(c["countries"])}</div>
      <div style="flex:1;min-width:280px">
        <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">STRATEGY</div>
        {_alloc_bars(c["strategies"])}</div>
    </div>
    <div style="font-size:10px;color:#898781;margin-top:10px">
      Theme percentages sum past 100% by design — a holding belongs to every theme it
      carries, which is the point of the overlap analysis below.</div>"""

    body = (f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));'
            f'gap:16px">{tiles}</div>{max_note}{grids}')
    return card("Portfolio Concentration", body, "🎚")


def _risk_contribution_card(r: dict) -> str:
    rc = r["risk_contributions"]
    if not rc:
        return ""
    rows = "".join(f"""
      <tr>
        <td><a href="/research/{esc(x["Ticker"])}.html"><b>{esc(x["Ticker"])}</b></a></td>
        <td style="text-align:right">{x["Weight_Pct"]:.2f}%</td>
        <td style="text-align:right;font-weight:600">{x["Risk_Contribution_Pct"]:.1f}%</td>
        <td style="text-align:right;color:{"#A32D2D" if (x["Risk_Per_Dollar"] or 0) > 1.3 else "#0b0b0b"}">
          {_num(x["Risk_Per_Dollar"], 2, "×")}</td>
        <td><div style="background:#f1efea;border-radius:3px;height:8px;min-width:80px">
          <div style="width:{max(2, min(100, x["Risk_Contribution_Pct"]))}%;background:#A32D2D;
               height:8px;border-radius:3px"></div></div></td>
      </tr>""" for x in rc[:18])
    return card("Contribution to Portfolio Risk", f"""
      <div style="font-size:11px;color:#5c5a55;margin-bottom:10px">
        Share of total portfolio volatility, not of capital. The last column is risk per
        dollar allocated — above 1.0× means the position contributes more risk than its
        size suggests, which is the number that should drive sizing.</div>
      <table><thead><tr><th>Ticker</th><th style="text-align:right">Weight</th>
      <th style="text-align:right">Risk Contribution</th><th style="text-align:right">Risk/$</th>
      <th></th></tr></thead><tbody>{rows}</tbody></table>""", "🔥")


def _overlap_card(r: dict) -> str:
    clusters = r["overlap"]
    if not clusters:
        return card("Overlap Analysis", empty("No theme carries two or more holdings."), "🔗")
    rows = []
    for c in clusters:
        flags = []
        if c["single_point_of_failure"]:
            flags.append(f'<div style="font-size:10px;color:#791F1F">⛔ {esc(c["single_point_of_failure"])}</div>')
        if c["duplicate_exposure"]:
            flags.append(f'<div style="font-size:10px;color:#633806">⚠ {esc(c["duplicate_exposure"])}</div>')
        if c["over_limit"]:
            flags.append(f'<div style="font-size:10px;color:#791F1F">⛔ over the '
                         f'{r["limits"]["theme"]:g}% theme limit</div>')
        score = c["risk_score"]
        color = "#791F1F" if score >= 75 else "#B8730E" if score >= 50 else "#0F6E56"
        rows.append(f"""
        <tr>
          <td><b>{esc(c["theme"])}</b></td>
          <td style="font-size:11px">{esc(", ".join(c["tickers"]))}</td>
          <td style="text-align:right">{c["count"]}</td>
          <td style="text-align:right;font-weight:600;
              color:{"#A32D2D" if c["over_limit"] else "#0b0b0b"}">{c["weight_pct"]:.1f}%</td>
          <td style="text-align:right">{_num(c["avg_correlation"], 2)}</td>
          <td style="text-align:right;font-weight:650;color:{color}">{score:.0f}</td>
          <td>{"".join(flags) or '<span style="font-size:10px;color:#0F6E56">✓ clear</span>'}</td>
        </tr>""")
    return card("Overlap Analysis — what is secretly one bet", f"""
      <div style="overflow-x:auto"><table><thead><tr>
        <th>Theme</th><th>Holdings</th><th style="text-align:right">#</th>
        <th style="text-align:right">Weight</th><th style="text-align:right">Avg Corr</th>
        <th style="text-align:right">Risk</th><th>Flags</th></tr></thead>
        <tbody>{"".join(rows)}</tbody></table></div>""", "🔗")


def _correlation_card(r: dict) -> str:
    corr = r["correlation"]
    tickers, matrix = corr["tickers"], corr["matrix"]
    if not tickers:
        return ""
    # Cap the rendered grid — 27 tickers is a 729-cell table that no one reads
    # on a laptop. Largest positions are the ones whose correlation matters.
    order = [h["Ticker"] for h in r["holdings"] if h["Ticker"] in tickers][:16]
    idx = {t: tickers.index(t) for t in order}

    def cell(v):
        if v is None:
            return '<td style="background:#f6f5f2;color:#c9c7c1;text-align:center;font-size:9px">—</td>'
        # Diverging ramp: red = moves together (the risk), blue = offsets.
        if v >= 0:
            bg = f"rgba(163,45,45,{0.08 + 0.72 * min(v, 1):.2f})"
        else:
            bg = f"rgba(24,95,165,{0.08 + 0.72 * min(-v, 1):.2f})"
        color = "#fff" if abs(v) > 0.62 else "#0b0b0b"
        return (f'<td style="background:{bg};color:{color};text-align:center;'
                f'font-size:9px;padding:3px 4px">{v:.2f}</td>')

    head = "".join(f'<th style="font-size:9px;padding:3px">{esc(t)}</th>' for t in order)
    body_rows = "".join(
        f'<tr><th style="font-size:9px;text-align:left;padding:3px 6px">{esc(a)}</th>'
        + "".join(cell(matrix[idx[a]][idx[b]]) for b in order) + "</tr>"
        for a in order)

    pairs = "".join(
        f'<tr><td><b>{esc(p["a"])} ↔ {esc(p["b"])}</b></td>'
        f'<td style="text-align:right;color:#A32D2D;font-weight:600">{p["corr"]:.2f}</td>'
        f'<td style="text-align:right">{p["combined_weight_pct"]:.1f}%</td></tr>'
        for p in corr["high_pairs"][:10])
    pairs_table = (f'<div style="font-size:11px;font-weight:600;color:#898781;margin:14px 0 4px">'
                   f'SAME-TRADE PAIRS (≥0.75, combined weight &gt;1%)</div>'
                   f'<table><thead><tr><th>Pair</th><th style="text-align:right">Corr</th>'
                   f'<th style="text-align:right">Combined</th></tr></thead>'
                   f'<tbody>{pairs}</tbody></table>' if pairs else "")

    return card("Correlation Matrix", f"""
      <div style="font-size:11px;color:#5c5a55;margin-bottom:10px">
        Daily returns, 120-day window. Red = moves together (concentration you can't see
        in the allocation table); blue = offsets. Average pairwise
        <b>{corr.get("avg_pairwise")}</b> across {corr.get("pairs_measured")} pairs.
        Showing the {len(order)} largest positions.</div>
      <div style="overflow-x:auto"><table style="border-collapse:collapse">
        <thead><tr><th></th>{head}</tr></thead><tbody>{body_rows}</tbody></table></div>
      {pairs_table}""", "🌡")


def _scenarios_card(r: dict) -> str:
    rows = []
    pv = r["totals"]["portfolio_value"]
    worst = min((s["pnl"] or 0) for s in r["scenarios"]) if r["scenarios"] else 0
    for s in r["scenarios"]:
        pnl = s["pnl"] or 0
        share = abs(pnl) / abs(worst) * 100 if worst else 0
        color = "#A32D2D" if pnl < 0 else "#0F6E56"
        uncovered = s.get("uncovered_tickers") or []
        note = (f'<div style="font-size:10px;color:#898781">no factor beta for '
                f'{esc(", ".join(uncovered[:4]))}{"…" if len(uncovered) > 4 else ""}</div>'
                if uncovered else "")
        rows.append(f"""
        <tr>
          <td><b>{esc(s["scenario"])}</b>
            <div style="font-size:10px;color:#898781">{esc(s["method"])}</div>{note}</td>
          <td style="text-align:right">{esc(s["shock"])}</td>
          <td style="text-align:right;font-weight:650;color:{color}">{_signed_money(pnl)}</td>
          <td style="text-align:right;color:{color}">{_signed_pct(s["pct_of_portfolio"])}</td>
          <td><div style="background:#f1efea;border-radius:3px;height:8px;min-width:90px">
            <div style="width:{max(2, share):.0f}%;background:{color};height:8px;
                 border-radius:3px"></div></div></td>
        </tr>""")
    return card("Scenario Analysis", f"""
      <div style="font-size:11px;color:#5c5a55;margin-bottom:10px">
        Each holding's response is its own regression beta against the factor over 2 years
        of daily data — so the same shock prints different numbers per position rather than
        assuming everything moves with the market. Applied to
        {fmt_money(pv, 0)} of portfolio value.</div>
      <div style="overflow-x:auto"><table><thead><tr>
        <th>Scenario</th><th style="text-align:right">Shock</th>
        <th style="text-align:right">P&L</th><th style="text-align:right">% of Portfolio</th>
        <th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>""", "🌪")


def _stress_card(r: dict) -> str:
    s = r["stress"]
    if s.get("note"):
        return card("Stress Test", empty(s["note"]), "🧪")

    def tile(label, value, sub=""):
        return (f'<div><div style="font-size:11px;color:#898781">{label}</div>'
                f'<div style="font-size:18px;font-weight:650">{value}</div>'
                f'<div style="font-size:10px;color:#898781">{esc(sub)}</div></div>')

    rec = s.get("observed_recovery_days")
    rec_txt = (f"{rec} days" if rec is not None
               else "not yet recovered" if s.get("still_underwater") else "—")
    tiles = (
        tile("Annualized Vol", f'{s["annual_vol_pct"]}%', f'daily {s["daily_vol_pct"]}%')
        + tile("95% VaR (1 day)", f'{s["var_95_pct"]}%',
               f'{fmt_money(s["var_95_dollars"], 0)} · parametric {s["parametric_var_95_pct"]}%')
        + tile("99% VaR (1 day)", f'{s["var_99_pct"]}%',
               f'{fmt_money(s["var_99_dollars"], 0)} · parametric {s["parametric_var_99_pct"]}%')
        + tile("Expected Shortfall 95%", f'{s["expected_shortfall_95_pct"]}%',
               f'avg loss beyond VaR · {fmt_money(s.get("expected_shortfall_95_dollars"), 0)}')
        + tile("Expected Shortfall 99%", f'{s["expected_shortfall_99_pct"]}%', "tail of the tail")
        + tile("Worst Drawdown (2y)", f'{s["worst_historical_drawdown_pct"]}%',
               f"recovery: {rec_txt}")
        + tile("Expected Max Drawdown", f'{s["expected_max_drawdown_pct"]}%',
               f'next 12m estimate · needs +{s.get("required_gain_to_recover_pct")}% to recover')
        + tile("Expected Recovery",
               f'{s["expected_recovery_months"]} months'
               if s.get("expected_recovery_months") is not None else "no estimate",
               s.get("recovery_basis", ""))
        + tile("Worst / Best Day", f'{s["worst_day_pct"]}% / +{s["best_day_pct"]}%',
               f'{s["observations"]} sessions simulated')
        + tile("Sharpe / Sortino", f'{_num(s.get("sharpe"), 2)} / {_num(s.get("sortino"), 2)}',
               "current weights, 2y")
    )
    gap = ""
    if s.get("var_99_pct") and s.get("parametric_var_99_pct"):
        excess = abs(s["var_99_pct"]) - abs(s["parametric_var_99_pct"])
        if excess > 0.5:
            gap = (f'<div style="background:#FCEBEB;border-left:3px solid #A32D2D;padding:8px 12px;'
                   f'border-radius:6px;margin-top:14px;font-size:11px"><b>Fat tails.</b> '
                   f'The historical 99% VaR ({s["var_99_pct"]}%) is {excess:.1f} points worse than '
                   f'the normal-distribution estimate ({s["parametric_var_99_pct"]}%). This book\'s '
                   f'bad days are worse than a volatility number alone implies — size for the '
                   f'historical figure.</div>')
    return card("Stress Test", f"""
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px">
        {tiles}</div>{gap}
      <div style="font-size:10px;color:#898781;margin-top:12px">
        Historical simulation: today's weights replayed through {s["observations"]} sessions of
        actual returns. Not what the account earned — what this book would have done in that
        tape, which is the only version that can stress today's positions.</div>""", "🧪")


def _sizing_card(r: dict) -> str:
    live = [s for s in r["sizing"] if not s.get("Is_Dust")]
    dust = [s for s in r["sizing"] if s.get("Is_Dust")]

    def row(s):
        color = _ACTION_COLOR.get(s["Action"], "#0b0b0b")
        reasons = "".join(f'<div style="font-size:10px;color:#5c5a55">· {esc(x)}</div>'
                          for x in s["Reasons"])
        return f"""
        <tr>
          <td><a href="/research/{esc(s["Ticker"])}.html"><b>{esc(s["Ticker"])}</b></a></td>
          <td><span style="color:{color};font-weight:700;font-size:11px">{esc(s["Action"])}</span>
            <div style="font-size:10px;color:#898781">{esc(s["Rationale"])}</div></td>
          <td style="text-align:right">{s["Weight_Pct"]:.2f}%</td>
          <td style="text-align:right">{_num(s.get("Vol_90D"), 0, "%")}</td>
          <td style="text-align:right">{_num(s.get("Risk_Contribution_Pct"), 1, "%")}</td>
          <td style="font-size:11px">{esc(s.get("Trend") or "—")}</td>
          <td>{reasons}</td>
        </tr>"""

    head = """<thead><tr><th>Ticker</th><th>Action</th><th style="text-align:right">Weight</th>
      <th style="text-align:right">Vol 90d</th><th style="text-align:right">Risk Contrib</th>
      <th>Trend</th><th>Drivers</th></tr></thead>"""
    dust_block = ""
    if dust:
        names = ", ".join(f'{d["Ticker"]} ({d["Weight_Pct"]:.2f}%)' for d in dust)
        dust_block = (f'<div style="margin-top:14px;background:#faf9f7;border-radius:8px;'
                      f'padding:10px 12px;font-size:11px;color:#5c5a55">'
                      f'<b>{len(dust)} dust positions</b> — below the size where they can move '
                      f'anything: {esc(names)}. Close them or size them up; either way they are '
                      f'housekeeping, not risk decisions, so they are kept out of the calls above.'
                      f'</div>')
    counts = {}
    for s in live:
        counts[s["Action"]] = counts.get(s["Action"], 0) + 1
    chips = " ".join(
        f'<span style="color:{_ACTION_COLOR.get(a)};font-weight:600">{n} {a.lower()}</span>'
        for a, n in sorted(counts.items()))
    return card("Position Sizing", f"""
      <div style="font-size:11px;color:#5c5a55;margin-bottom:10px">{chips}</div>
      <div style="overflow-x:auto"><table>{head}<tbody>
        {"".join(row(s) for s in live)}</tbody></table></div>{dust_block}""", "⚖")


def _rebalancing_card(r: dict) -> str:
    trades = r["rebalancing"]
    if not trades:
        return card("Recommended Rebalancing",
                    '<div style="color:#0F6E56;font-size:12px">✓ No rebalancing trades '
                    'required — the book is inside its limits.</div>', "🔄")
    rows = "".join(f"""
      <tr>
        <td><span style="font-weight:700;font-size:11px;color:{
            "#A32D2D" if t["action"] in ("SELL", "REDUCE Δ")
            else "#0F6E56" if t["action"] == "BUY" else "#185FA5"
        }">{esc(t["action"])}</span></td>
        <td><b>{esc(t["ticker"])}</b></td>
        <td style="text-align:right;font-weight:600">{fmt_money(t["dollars"], 0)}</td>
        <td style="text-align:right">{_num(t.get("shares"), 1) if t.get("shares") else "—"}</td>
        <td style="text-align:right;font-size:11px">{t["from_pct"]:.1f}% → {t["to_pct"]:.1f}%</td>
        <td style="font-size:11px">{esc(t["why"])}
          {"".join(f'<div style="font-size:10px;color:#898781">· {esc(d)}</div>' for d in t.get("detail") or [])}</td>
        <td style="font-size:10px;color:#0F6E56">{esc(", ".join(t.get("improves") or []))}</td>
      </tr>""" for t in trades)
    sells = sum(t["dollars"] for t in trades if t["action"] == "SELL")
    return card("Recommended Rebalancing", f"""
      <div style="font-size:11px;color:#5c5a55;margin-bottom:10px">
        Trims are sized to bring a position back to its cap, not to zero — the goal is a book
        inside its limits, not a flat one. Proceeds are routed to the cash minimum first, then
        to sectors the book has no claim on. <b>{fmt_money(sells, 0)}</b> raised in total.
        Nothing here is placed automatically.</div>
      <div style="overflow-x:auto"><table><thead><tr>
        <th>Action</th><th>Ticker</th><th style="text-align:right">Amount</th>
        <th style="text-align:right">Shares</th><th style="text-align:right">Weight</th>
        <th>Why</th><th>Improves</th></tr></thead><tbody>{rows}</tbody></table></div>""", "🔄")


def _holdings_card(r: dict) -> str:
    """The full enrichment table — one row per underlying, every field the
    report could source. Wide on purpose: this is the reference table the rest
    of the report summarizes."""
    rows = []
    for h in r["holdings"]:
        tech = (r.get("technicals") or {}).get(h["Ticker"]) or {}
        ins = h.get("Insider_Activity") or {}
        ins_txt = (f'{ins.get("direction")} ({ins.get("net_shares", 0):,.0f} sh)'
                   if ins else "—")
        themes = ", ".join(h.get("Themes") or []) or "—"
        earn = h.get("Earnings_Date") or "—"
        dte = h.get("Days_To_Earnings")
        earn_color = "#A32D2D" if (dte is not None and 0 <= dte <= 14) else "#0b0b0b"
        dust = ' <span style="font-size:9px;color:#898781">dust</span>' if h.get("Is_Dust") else ""
        rows.append(f"""
        <tr>
          <td><a href="{tv_url(h["Ticker"])}" target="_blank" rel="noopener"><b>{esc(h["Ticker"])}</b></a>{dust}
            <div style="font-size:10px;color:#898781">{esc((h.get("Name") or "")[:26])}</div></td>
          <td style="font-size:11px">{esc(h.get("Sector") or "—")}
            <div style="font-size:10px;color:#898781">{esc((h.get("Industry") or "")[:24])}</div></td>
          <td style="font-size:11px">{esc(h.get("Cap_Bucket") or "—")}
            <div style="font-size:10px;color:#898781">{fmt_money(h.get("Market_Cap"), 0) if h.get("Market_Cap") else "—"}</div></td>
          <td style="font-size:11px">{esc(h.get("Country") or "—")}
            <div style="font-size:10px;color:#898781">{esc((h.get("Exchange") or "")[:16])}</div></td>
          <td style="text-align:right;font-weight:600">{_num(h.get("Weight_Pct"), 2, "%")}</td>
          <td style="text-align:right">{fmt_money(h.get("Equity_Value"), 0)}</td>
          <td style="text-align:right;color:#185FA5">{fmt_money(h.get("Option_Delta_Value"), 0)}</td>
          <td style="text-align:right">{_num(h.get("Beta_SPY"), 2)}</td>
          <td style="text-align:right">{_num(h.get("Vol_30D"), 0, "%")}</td>
          <td style="text-align:right">{_num(h.get("Vol_90D"), 0, "%")}</td>
          <td style="text-align:right">{_num(h.get("Corr_SPY"), 2)}</td>
          <td style="text-align:right">{_num(h.get("Corr_QQQ"), 2)}</td>
          <td style="text-align:right">{fmt_money(h.get("ADV_Dollars"), 0) if h.get("ADV_Dollars") else "—"}</td>
          <td style="text-align:right">{_num(h.get("Inst_Own_Pct"), 0, "%")}</td>
          <td style="text-align:right">{_num(h.get("Short_Pct_Float"), 1, "%")}</td>
          <td style="font-size:11px">{esc(h.get("Analyst_Rec") or "—")}
            <div style="font-size:10px;color:#898781">{_num(h.get("Analyst_Count"), 0)} covering</div></td>
          <td style="font-size:11px">{esc(ins_txt)}</td>
          <td style="font-size:11px;color:{earn_color}">{esc(earn)}
            {f'<div style="font-size:10px">in {dte}d</div>' if dte is not None and dte >= 0 else ""}</td>
          <td style="text-align:right">{_num(h.get("PE"), 1)}</td>
          <td style="text-align:right">{_num(h.get("EV_EBITDA"), 1)}</td>
          <td style="text-align:right">{_num(h.get("FCF_Yield"), 1, "%")}</td>
          <td style="text-align:right">{_num(h.get("Revenue_Growth"), 0, "%")}</td>
          <td style="text-align:right">{_num(h.get("ROE"), 0, "%")}</td>
          <td style="text-align:right">{_num(h.get("Debt_Equity"), 0)}</td>
          <td style="text-align:right">{_num(tech.get("RSI"), 0)}</td>
          <td style="font-size:11px;color:{"#A32D2D" if tech.get("Above_200MA") is False else "#0b0b0b"}">
            {esc(tech.get("Trend") or "—")}
            <div style="font-size:10px;color:#898781">200MA {_num(tech.get("Dist_200MA_Pct"), 0, "%")}</div></td>
          <td style="text-align:right">{_num(h.get("Max_Drawdown_2Y"), 0, "%")}</td>
          <td style="font-size:10px">{esc(themes)}</td>
        </tr>""")
    return card(f'Holdings — full enrichment ({len(r["holdings"])})', f"""
      <div style="overflow-x:auto;max-height:560px"><table><thead><tr>
        <th>Ticker</th><th>Sector / Industry</th><th>Cap</th><th>Country / Exch</th>
        <th style="text-align:right">Weight</th><th style="text-align:right">Equity $</th>
        <th style="text-align:right">Δ Notional</th><th style="text-align:right">Beta</th>
        <th style="text-align:right">Vol 30d</th><th style="text-align:right">Vol 90d</th>
        <th style="text-align:right">Corr SPY</th><th style="text-align:right">Corr QQQ</th>
        <th style="text-align:right">ADV $</th><th style="text-align:right">Inst %</th>
        <th style="text-align:right">Short %</th><th>Analysts</th><th>Insiders</th>
        <th>Earnings</th><th style="text-align:right">P/E</th>
        <th style="text-align:right">EV/EBITDA</th><th style="text-align:right">FCF Yld</th>
        <th style="text-align:right">Rev Gr</th><th style="text-align:right">ROE</th>
        <th style="text-align:right">D/E</th><th style="text-align:right">RSI</th>
        <th>Trend</th><th style="text-align:right">Max DD 2y</th><th>Themes</th>
      </tr></thead><tbody>{"".join(rows)}</tbody></table></div>
      <div style="font-size:10px;color:#898781;margin-top:8px">
        Δ Notional is delta-adjusted option exposure on the same underlying, folded into the
        weight. Scroll horizontally for the full field set.</div>""", "🔬")


def _options_risk_card(r: dict) -> str:
    opts = r["options"]
    if not opts:
        return ""
    rows = "".join(f"""
      <tr>
        <td><b>{esc(o["Label"])}</b></td>
        <td>{esc(o["Side"])}</td>
        <td style="text-align:right">{o["Contracts"]:g}</td>
        <td style="text-align:right;color:{"#A32D2D" if (o["DTE"] or 99) <= 7 else "#0b0b0b"};
            font-weight:600">{o["DTE"] if o["DTE"] is not None else "—"}</td>
        <td style="text-align:right">{fmt_money(o.get("Spot"))}</td>
        <td style="text-align:right">{_num(o.get("Moneyness_Pct"), 1, "%")}</td>
        <td style="text-align:right">{_num(o.get("IV_Proxy_Pct"), 0, "%")}</td>
        <td style="text-align:right;font-weight:600">{_num(o.get("Delta"), 3)}</td>
        <td style="text-align:right">{_num(o.get("Shares_Equiv"), 0)}</td>
        <td style="text-align:right;color:#185FA5;font-weight:600">{fmt_money(o.get("Delta_Notional"), 0)}</td>
        <td style="text-align:right">{fmt_money(o.get("Market_Value"), 0)}</td>
        <td style="text-align:right;color:#A32D2D">{fmt_money(o.get("Premium_At_Risk"), 0)}</td>
      </tr>""" for o in opts)
    total_delta = sum(o.get("Delta_Notional") or 0 for o in opts)
    total_risk = sum(o.get("Premium_At_Risk") or 0 for o in opts)
    lever = (total_delta / total_risk) if total_risk else None
    return card("Options — delta exposure", f"""
      <div style="font-size:11px;color:#5c5a55;margin-bottom:10px">
        Delta is a <b>Black-Scholes estimate</b> from each underlying's 90-day realized vol —
        this app has no broker session and therefore no live greeks. Two different numbers
        matter here: <b>Δ notional</b> ({fmt_money(total_delta, 0)}) is what moves when the
        underlying moves and is what the concentration and scenario math uses;
        <b>premium at risk</b> ({fmt_money(total_risk, 0)}) is the most these contracts can
        lose{f", which is {lever:.0f}× less" if lever and lever > 1 else ""}.</div>
      <div style="overflow-x:auto"><table><thead><tr>
        <th>Contract</th><th>Side</th><th style="text-align:right">Qty</th>
        <th style="text-align:right">DTE</th><th style="text-align:right">Spot</th>
        <th style="text-align:right">Moneyness</th><th style="text-align:right">Vol proxy</th>
        <th style="text-align:right">Delta</th><th style="text-align:right">Shares eq</th>
        <th style="text-align:right">Δ Notional</th><th style="text-align:right">Value</th>
        <th style="text-align:right">Premium at risk</th></tr></thead>
        <tbody>{rows}</tbody></table></div>""", "🎯")


def _liquidity_card(r: dict) -> str:
    rows = "".join(f"""
      <tr>
        <td><b>{esc(x["Ticker"])}</b></td>
        <td style="text-align:right">{_num(x.get("Shares_Equivalent"), 0)}</td>
        <td style="text-align:right">{fmt_money(x.get("ADV_Dollars"), 0) if x.get("ADV_Dollars") else "—"}</td>
        <td style="text-align:right">{_num(x.get("Pct_Of_ADV"), 2, "%")}</td>
        <td style="text-align:right;font-weight:600;color:{
            "#A32D2D" if (x.get("Days_To_Exit") or 0) > 1 else "#0b0b0b"}">
          {_num(x.get("Days_To_Exit"), 2)}</td>
        <td style="font-size:11px;color:{"#A32D2D" if x["Flag"].startswith("illiquid") else "#898781"}">
          {esc(x["Flag"])}</td>
      </tr>""" for x in r["liquidity"][:14])
    return card("Liquidity", f"""
      <div style="font-size:11px;color:#5c5a55;margin-bottom:10px">
        Days to exit each position without taking more than 10% of average daily volume.
        Uses delta-adjusted share equivalents, so an options-heavy name shows the shares
        someone would actually have to move.</div>
      <table><thead><tr><th>Ticker</th><th style="text-align:right">Shares eq</th>
        <th style="text-align:right">ADV $</th><th style="text-align:right">% of ADV</th>
        <th style="text-align:right">Days to exit</th><th>Flag</th></tr></thead>
        <tbody>{rows}</tbody></table>""", "💧")


def _watchlist_card(r: dict) -> str:
    w = r["watchlist"]
    missing = w["missing_sectors"]
    sugg = "".join(
        f'<tr><td><b>{esc(s["ticker"])}</b></td><td>{esc(s["sector"])}</td>'
        f'<td style="font-size:11px;color:#5c5a55">{esc(s["why"])}</td></tr>'
        for s in w["suggestions"])
    divs = "".join(
        f'<tr><td><b>{esc(d["ticker"])}</b></td><td>{esc(d["name"])}</td>'
        f'<td style="font-size:11px;color:#5c5a55">{esc(d["why"])}</td></tr>'
        for d in w["diversifiers"])
    missing_txt = (esc(", ".join(missing)) if missing
                   else "none — every major sector has some representation")
    return card("Watchlist — diversification gaps", f"""
      <div style="font-size:12px;margin-bottom:10px">
        <b>Sectors with under 2% exposure:</b> {missing_txt}</div>
      <div style="display:flex;gap:24px;flex-wrap:wrap">
        <div style="flex:1;min-width:280px">
          <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">
            FILL THE GAPS</div>
          <table><tbody>{sugg or '<tr><td colspan="3" style="color:#898781">—</td></tr>'}</tbody></table></div>
        <div style="flex:1;min-width:280px">
          <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">
            CORRELATION DIVERSIFIERS</div>
          <table><tbody>{divs}</tbody></table></div>
      </div>
      <div style="font-size:10px;color:#898781;margin-top:10px">{esc(w["note"])}</div>""", "🔭")


def _data_card(r: dict) -> str:
    """Everything the report could not source, stated plainly. A gap that
    isn't shown reads as a zero, and a zero passes limits it shouldn't."""
    notes = "".join(f'<div style="font-size:11px;color:#633806">⚠ {esc(n)}</div>'
                    for n in r.get("data_notes") or [])
    fields = "".join(
        f'<tr><td><b>{esc(f["field"])}</b></td>'
        f'<td style="font-size:11px;color:#5c5a55">{esc(f["reason"])}</td></tr>'
        for f in r.get("unavailable_fields") or [])
    return card("Data Coverage", f"""
      {notes or '<div style="font-size:11px;color:#0F6E56">✓ Every holding priced and profiled.</div>'}
      <div style="font-size:11px;font-weight:600;color:#898781;margin:14px 0 4px">
        FIELDS WITH NO AVAILABLE SOURCE</div>
      <table><tbody>{fields}</tbody></table>
      <div style="font-size:10px;color:#898781;margin-top:10px">
        These are shown rather than dropped so a missing input can never be mistaken for a
        zero. ETF look-through weights are issuer approximations as of
        {esc(r.get("etf_lookthrough_as_of", ""))}.</div>""", "📚")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def render_risk_report(report: dict | None) -> str:
    """Full report HTML, or a prompt to run it when there's no cached run."""
    if not report:
        return card("Portfolio Risk Analysis", empty(
            "No risk analysis yet. Hit <b>Analyze Portfolio</b> above to run one — it "
            "fetches 2 years of history plus fundamentals for every holding and takes "
            "about a minute."), "🏛")
    try:
        return "".join([
            _header_card(report),
            _dashboard_card(report),
            _risks_card(report),
            _scores_card(report),
            _violations_card(report),
            _concentration_card(report),
            _overlap_card(report),
            _correlation_card(report),
            _risk_contribution_card(report),
            _sizing_card(report),
            _rebalancing_card(report),
            _scenarios_card(report),
            _stress_card(report),
            _options_risk_card(report),
            _liquidity_card(report),
            _holdings_card(report),
            _watchlist_card(report),
            _data_card(report),
        ])
    except Exception as e:                            # noqa: BLE001
        # A stale cache from an older schema shouldn't take the whole Portfolio
        # page down with it — the holdings table above still has to render.
        import traceback
        traceback.print_exc()
        return card("Portfolio Risk Analysis", empty(
            f"Couldn't render the saved report ({esc(str(e))}). Re-run "
            f"<b>Analyze Portfolio</b> to rebuild it."), "🏛")
