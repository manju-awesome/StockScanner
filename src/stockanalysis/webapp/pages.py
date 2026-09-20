"""
pages.py
========
The five page bodies (Dashboard, Scanner, Research, Portfolio, Automation).
Each function returns an HTML string that views.render_page() wraps in the
shared layout. Reads whatever structured data is available (snapshot.json,
research_index.json, the latest scan CSV, portfolio.csv) and always has a
friendly empty state when nothing has been generated yet — a fresh checkout
should never show a stack trace.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from . import jobstore
from .views import (
    OUTPUT_DIR, DATA_DIR, esc, badge, stars, action_badge, progress_bar,
    card, empty, fmt_money, fmt_pct, tv_url,
)


def _latest(pattern: str | tuple[str, ...], n: int = 1,
           base: Path = OUTPUT_DIR) -> list[Path]:
    patterns = (pattern,) if isinstance(pattern, str) else pattern
    seen, files = set(), []
    for p in patterns:
        for f in base.glob(p):
            if f not in seen:
                seen.add(f)
                files.append(f)
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files[:n]


# Dashboard filenames: "<universe>Report_<ts>.html" (current) — the legacy
# "dashboard_<ts>.html" is still matched so pre-existing files keep showing
# up in listings and remain eligible for cleanup after the rename.
DASHBOARD_PATTERNS = ("*Report_*.html", "dashboard_*.html")


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# OVERALL TREND (market regime)
# ─────────────────────────────────────────────────────────────────────────────
# Distinct from the `regime` tile in the market-status strip below, which is
# snapshot.json's Bullish/Neutral/Defensive from core.market_regime and only
# updates when a scan runs. This is the standalone 12-category −100…+100 read
# from the shared scorer, run on demand and shared with the SPY_DayTrader
# dashboard so both show identical numbers.

def _regime_tone(score: float) -> str:
    return "good" if score > 0.5 else ("bad" if score < -0.5 else "muted")


def render_regime(report: dict | None) -> str:
    if not report:
        return empty("No overall-trend read yet — click “Overall trend” to run one.")
    if report.get("error"):
        return (f'<div style="background:#FCEBEB;color:#791F1F;padding:12px;'
                f'border-radius:10px">Overall trend failed: {esc(report["error"])}</div>')

    overall = report.get("overall_trend_score", 0.0)
    prob = report.get("probabilities", {})
    day = report.get("day_type", {})

    def tile(label, value, tone="muted"):
        colour = {"good": "#0F6E56", "bad": "#A32D2D"}.get(tone, "#0b0b0b")
        return (f'<div style="flex:1;min-width:120px">'
                f'<div style="font-size:11px;color:#898781">{esc(label)}</div>'
                f'<div style="font-size:18px;font-weight:600;color:{colour}">{esc(str(value))}</div></div>')

    tiles = (
        tile("Overall trend score", f"{overall:+.1f}", _regime_tone(overall))
        + tile("Primary bias", report.get("primary_bias", "—"), _regime_tone(overall))
        + tile("Expected volatility", report.get("expected_volatility", "—"))
        + tile("Bull / Bear / Neutral",
               f"{prob.get('bullish_pct',0):.0f} / {prob.get('bearish_pct',0):.0f} / {prob.get('neutral_pct',0):.0f}%")
        + tile("Trend / Range / Reversal",
               f"{day.get('trend_day_pct',0):.0f} / {day.get('range_day_pct',0):.0f} / {day.get('reversal_day_pct',0):.0f}%")
    )

    def row(cells, tone="muted"):
        return ('<tr style="border-top:1px solid #E7E4DA">'
                + "".join(f'<td style="padding:6px 8px;font-size:12px;{s}">{c}</td>'
                          for c, s in cells) + "</tr>")

    rows = "".join(
        row([(f'<b>{e["score"]:+.1f}</b>',
              f'color:{"#0F6E56" if e["score"] > 0.5 else "#A32D2D" if e["score"] < -0.5 else "#898781"};'
              f'white-space:nowrap'),
             (esc(e["label"]), "font-weight:500"),
             (esc(e["confidence"]), "color:#898781"),
             (esc(e["evidence"]), "color:#444441")])
        for e in report.get("scored", [])
    )
    rows += "".join(
        row([("—", "color:#898781"), (esc(e["label"]), "color:#898781"),
             (f'excluded: {esc(e["reason"])}', "color:#898781"), ("", "")])
        for e in report.get("excluded", [])
    )

    warns = ""
    for key, prefix in (("staleness_warning", ""),
                        ("conflicting_categories", "Arguing against the aggregate: "),
                        ("low_confidence_categories", "Low-confidence inputs: ")):
        val = report.get(key)
        if not val:
            continue
        text = val if isinstance(val, str) else prefix + ", ".join(val)
        warns += (f'<div style="background:#FDF6E3;color:#7A5C00;padding:8px 10px;'
                  f'border-radius:8px;font-size:12px;margin-top:8px">{esc(text)}</div>')

    return f"""
<div style="display:flex;gap:16px;flex-wrap:wrap">{tiles}</div>
<div style="font-size:12px;color:#898781;margin-top:8px">
  {esc(report.get("bias_rationale",""))} · {esc(report.get("score_note",""))}</div>
{warns}
<table style="width:100%;border-collapse:collapse;margin-top:10px">
  <thead><tr style="text-align:left;color:#898781;font-size:11px">
    <th style="padding:4px 8px">Score</th><th style="padding:4px 8px">Category</th>
    <th style="padding:4px 8px">Conf.</th><th style="padding:4px 8px">Evidence</th>
  </tr></thead><tbody>{rows}</tbody></table>
<div style="font-size:11px;color:#898781;margin-top:6px">
  Collected {esc(str(report.get("fetched_at","?")))} · session: {esc(str(report.get("session","?")))}</div>
"""


def _regime_card() -> str:
    from stockanalysis.core import regime_client
    return card(
        "Overall trend",
        '<div style="font-size:12px;color:#444441;margin-bottom:10px">'
        'Scores today\'s market regime across 12 categories into a single −100…+100 read. '
        'Run this before scanning — the regime decides which setups are worth taking. '
        'Takes about 15 seconds.</div>'
        '<button onclick="runRegime()" style="padding:7px 14px;border-radius:8px;'
        'border:1px solid #D8D4C8;background:#fff;cursor:pointer;font-size:13px">'
        'Overall trend</button> <span id="regime-status" style="font-size:12px;color:#898781"></span>'
        f'<div id="regime-out" style="margin-top:10px">{render_regime(regime_client.load_cached())}</div>',
        icon="🧭",
    )


REGIME_JS = """
function runRegime() {
  var s = document.getElementById('regime-status');
  s.textContent = ' collecting market data…';
  fetch('/api/regime', {method: 'POST'}).then(r => r.text()).then(html => {
    document.getElementById('regime-out').innerHTML = html;
    s.textContent = '';
  }).catch(e => { s.textContent = ' failed: ' + e; });
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# SCAN & ANALYZE — refresh the research library, then read the engine's verdict
# ─────────────────────────────────────────────────────────────────────────────
# Two halves of one loop, deliberately in one card: the scan is what fills the
# research library, and the analysis is only ever as current as the last scan
# that touched those names. Splitting them across two pages is what made
# "why does NVDA say WATCH" hard to answer — the missing step was invisible.

ANALYZE_JS = r"""
// Which scope the panel is currently showing, so a research job finishing can
// refresh it in place. Null until the user asks for an analysis — the
// Dashboard's default reload-on-finish must survive untouched otherwise.
let _analysisScope = null;

function anScopeChanged() {
  const scope = document.getElementById('an-scope').value;
  document.getElementById('an-ticker').style.display =
    scope === 'ticker' ? '' : 'none';
  document.getElementById('an-list').style.display =
    scope === 'watchlist' ? '' : 'none';
}

// {scope, value} for the current picker state, or null after complaining.
function anParams() {
  const scope = document.getElementById('an-scope').value;
  if (scope === 'ticker') {
    const raw = document.getElementById('an-ticker').value.trim();
    if (!raw) { toast('Type a ticker first — e.g. NVDA', 'err'); return null; }
    return { scope: scope, value: raw };
  }
  if (scope === 'watchlist') {
    const name = document.getElementById('an-list').value;
    if (!name) { toast('Pick a watchlist first', 'err'); return null; }
    return { scope: scope, value: name };
  }
  return { scope: scope, value: '' };
}

async function loadAnalysis() {
  const params = anParams();
  if (!params) return;
  const out = document.getElementById('analysis-out');
  out.innerHTML = '<div style="font-size:12px;color:#898781;padding:14px 0">'
    + 'Running the engine over the library…</div>';
  try {
    const res = await fetch('/api/analysis?scope=' + encodeURIComponent(params.scope)
      + '&value=' + encodeURIComponent(params.value));
    out.innerHTML = await res.text();
    _analysisScope = params;
    // The table was injected after load, so its sort/reorder handlers have
    // to be bound now rather than at DOMContentLoaded.
    if (typeof initLtTable === 'function') initLtTable();
  } catch (e) {
    out.innerHTML = '<div style="font-size:12px;color:#791F1F">'
      + 'Analysis failed: ' + e + '</div>';
  }
}

// The scan half: refresh_research runs the full per-ticker pipeline, which is
// what repopulates the fields the engine reads. Posting through /run means it
// shows up in the job tray and toasts like every other job.
async function runScanAnalyze() {
  const params = anParams();
  if (!params) return;
  const body = { action: 'research' };
  if (params.scope === 'ticker') body.tickers = params.value;
  else if (params.scope === 'daytrade') body.watchlist = 'daytrade';
  else if (params.scope === 'watchlist') body.watchlist = params.value;
  // The sweep-everything sentinel comes from the server rather than being
  // spelled again here — two copies of a wire constant is one to forget.
  else body.watchlist = document.getElementById('an-scope').dataset.all;
  try {
    const res = await fetch('/run', { method: 'POST',
      body: new URLSearchParams(body) });
    const data = await res.json();
    toast(data.message || (data.ok ? 'Started' : 'Failed to start'),
          data.ok ? 'ok' : 'err');
    if (data.ok) { _analysisScope = params; _justSubmitted.add('research'); }
  } catch (e) { toast('Request failed: ' + e, 'err'); }
  pollJobs();
}

// A research job the panel is waiting on refreshes the panel; anything else
// keeps the Dashboard's old reload-when-a-job-lands behaviour. Reloading here
// instead would throw away the analysis the user just asked for.
function onJobFinished(j) {
  if (j.kind === 'research' && _analysisScope) { loadAnalysis(); return; }
  setTimeout(() => location.reload(), 1200);
}
"""


def _scan_analyze_card() -> str:
    """The scope picker, the two buttons, and the empty panel they fill."""
    from stockanalysis.webapp.api import ALL_UNIVERSES_SENTINEL
    try:
        from stockanalysis.reporting.research import (
            load_watchlists, tree_ordered_names, SUBLIST_SEP)
        watchlists = {n: t for n, t in load_watchlists().items() if t}
    except Exception as e:
        print(f"[Dashboard] watchlists unavailable ({e})")
        watchlists, SUBLIST_SEP, tree_ordered_names = {}, ": ", list

    def _opt(name: str) -> str:
        """Sublists indent under their parent by leaf name; the value stays
        the full "AI: Power" the backend resolves."""
        nested = SUBLIST_SEP in name
        label = name.split(SUBLIST_SEP, 1)[1] if nested else name
        pad = "&nbsp;&nbsp;└ " if nested else ""
        return (f'<option value="{esc(name)}">{pad}{esc(label)} '
                f'({len(watchlists[name])})</option>')

    list_opts = ('<option value="">Pick a watchlist…</option>' + "".join(
        _opt(n) for n in tree_ordered_names(sorted(watchlists))))
    n_all = len({t for v in watchlists.values() for t in v})
    n_day = len(watchlists.get("daytrade") or [])

    ctl = "padding:8px 10px;font-size:12px;border:0.5px solid #e1e0d9;border-radius:8px"
    body = f"""
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <select id="an-scope" onchange="anScopeChanged()"
              data-all="{esc(ALL_UNIVERSES_SENTINEL)}" style="{ctl}">
        <option value="ticker" selected>Single ticker</option>
        <option value="daytrade">Day trade list ({n_day})</option>
        <option value="watchlist">Watchlist…</option>
        <option value="all">All tickers ({n_all})</option>
      </select>
      <input id="an-ticker" placeholder="NVDA, or NVDA AMD MSFT"
             autocomplete="off" style="{ctl};min-width:220px">
      <select id="an-list" style="{ctl};display:none;max-width:240px">
        {list_opts}
      </select>
      <button class="btn secondary" onclick="runScanAnalyze()">
        Scan &amp; refresh research</button>
      <button class="btn" onclick="loadAnalysis()">Show analysis</button>
    </div>
    <div style="font-size:11px;color:#898781;margin-top:8px">
      <b>Scan &amp; refresh research</b> re-fetches each ticker and rewrites its
      research page — a few seconds per name, so a whole-library scan is a
      coffee break, not a click. <b>Show analysis</b> reads what the last scan
      stored and runs the Long-Term Buy Engine over it: same gates, scores and
      per-row reasoning as the
      <a href="/longterm">Long-Term</a> page. The panel refreshes itself when
      a scan started here finishes.
    </div>
    <div id="analysis-out" style="margin-top:14px"></div>"""
    return card("Scan & Analyze", body, "🔬",
                right='<a href="/longterm" style="font-size:11px">'
                      'Full engine →</a>')


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD (home)
# ─────────────────────────────────────────────────────────────────────────────

def dashboard_page() -> tuple[str, str]:
    from . import longterm_view

    snap = _read_json(OUTPUT_DIR / "snapshot.json")
    if not snap:
        # The overall-trend read and the Scan & Analyze card are independent of
        # scan output, so both stay available before the first scan has ever
        # run — this empty state is exactly when someone needs to start one.
        return (_regime_card() + _scan_analyze_card() + card("", empty(
            "No scan yet. Click “+ New Scan” above, or use Scan & Analyze, to "
            "run your first scan — this page fills in with market status, "
            "opportunities, and alerts once a scan completes."), pad="40px"),
            REGIME_JS + ANALYZE_JS + longterm_view.TABLE_JS)

    regime = snap.get("regime") or {}
    opp = snap.get("opportunity") or {}
    market = snap.get("market") or {}
    pf = (snap.get("portfolio") or {}).get("totals") or {}
    alerts = (snap.get("portfolio") or {}).get("alerts") or []

    regime_status = {"Bullish": "good", "Neutral": "watch",
                     "Defensive": "bad"}.get(regime.get("regime"), "muted")

    # ── Market status strip ─────────────────────────────────────────────────
    def _idx_tile(label, price, chg, strength):
        color = "#0F6E56" if (chg or 0) >= 0 else "#A32D2D"
        return (f'<div style="flex:1;min-width:110px">'
                f'<div style="font-size:11px;color:#898781">{label}</div>'
                f'<div style="font-size:16px;font-weight:600">{fmt_money(price) if price else "—"} '
                f'<span style="color:{color};font-size:12px">{fmt_pct(chg)}</span></div>'
                f'{badge(strength, {"STRONG":"good","WEAK":"bad"}.get(strength,"watch"), "small") if strength else ""}'
                f'</div>')

    market_html = (
        '<div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">'
        + _idx_tile("SPY", market.get("spy_price"), market.get("spy_chg_pct"), market.get("spy_strength"))
        + _idx_tile("QQQ", market.get("qqq_price"), market.get("qqq_chg_pct"), market.get("qqq_strength"))
        + f'<div style="flex:1;min-width:110px"><div style="font-size:11px;color:#898781">VIX</div>'
          f'<div style="font-size:16px;font-weight:600">{market.get("vix") if market.get("vix") is not None else "—"}</div></div>'
        + f'<div style="margin-left:auto">{badge(regime.get("regime", "Unknown").upper(), regime_status)}</div>'
        + '</div>'
        + f'<div style="font-size:11px;color:#898781;margin-top:8px">{esc(regime.get("guidance", ""))}</div>'
        + _leadership_html())

    # ── Hero: today's opportunity ───────────────────────────────────────────
    risk_status = {"LOW": "good", "MEDIUM": "watch", "HIGH": "bad"}.get(opp.get("risk"), "muted")
    hero = f"""
    <div style="display:flex;gap:28px;align-items:center;flex-wrap:wrap">
      <div>
        <div style="font-size:11px;font-weight:600;color:#898781">TODAY'S OPPORTUNITY</div>
        <div style="font-size:40px;font-weight:650;line-height:1.1">
          {opp.get('score', '—')}<span style="font-size:18px;color:#898781">/100</span></div>
        {stars(opp.get('stars', 0), 17)}
      </div>
      <div style="flex:1;min-width:180px">
        <div style="font-size:15px;font-weight:500">{esc(opp.get('label', ''))}</div>
        <div style="font-size:12px;color:#52514e;margin-top:3px">{opp.get('n_ready', 0)} name(s) ready 🟢</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:11px;font-weight:600;color:#898781">RISK</div>
        {badge(opp.get('risk', '—'), risk_status)}
      </div>
    </div>"""

    # ── Portfolio summary card ──────────────────────────────────────────────
    gain = pf.get("total_gain")
    gain_color = "good" if (gain or 0) >= 0 else "bad"
    pf_body = (
        f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px">'
        f'<div><div style="font-size:11px;color:#898781">Portfolio Value</div>'
        f'<div style="font-size:18px;font-weight:600">{fmt_money(pf.get("portfolio_value"), 0)}</div></div>'
        f'<div><div style="font-size:11px;color:#898781">Invested</div>'
        f'<div style="font-size:18px;font-weight:600">{fmt_money(pf.get("total_value"), 0)} '
        f'<span style="font-size:11px;color:#898781">({pf.get("invested_pct") or 0:g}%)</span></div></div>'
        f'<div><div style="font-size:11px;color:#898781">Total Gain</div>'
        f'<div style="font-size:18px;font-weight:600">{badge(fmt_money(gain, 0) if gain is not None else "—", gain_color)}</div></div>'
        f'<div><div style="font-size:11px;color:#898781">Cash</div>'
        f'<div style="font-size:18px;font-weight:600">{fmt_money(pf.get("cash"), 0)}</div></div>'
        f'</div>'
        if pf.get("positions") is not None else empty("No portfolio configured — see the Portfolio page."))

    # ── Top opportunity cards ───────────────────────────────────────────────
    def _opp_col(title, icon, items):
        if not items:
            return f'<div style="flex:1;min-width:200px"><div style="font-size:12px;font-weight:600;margin-bottom:8px">{icon} {title}</div>{empty("none qualified")}</div>'
        rows = "".join(
            f'<a href="{tv_url(r["ticker"])}" target="_blank" style="display:block;text-decoration:none;color:#0b0b0b;'
            f'padding:8px 10px;border-radius:8px;background:#f9f9f7;margin-bottom:6px" '
            f'title="Open TradingView chart">'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<b>{r["ticker"]}</b>{action_badge(r.get("action"), "small")}</div>'
            f'<div style="font-size:11px;color:#898781;margin-top:2px">'
            f'{fmt_money(r.get("price"))} · score {r.get("score")} · {esc(r.get("action_reason") or "")}</div></a>'
            for r in items[:3])
        return f'<div style="flex:1;min-width:200px"><div style="font-size:12px;font-weight:600;margin-bottom:8px">{icon} {title}</div>{rows}</div>'

    opp_cols = (
        '<div style="display:flex;gap:16px;flex-wrap:wrap">'
        + _opp_col("Day Trade", "🔥", snap.get("top_day") or [])
        + _opp_col("Swing", "🚀", snap.get("top_swing") or [])
        + _opp_col("Long Term", "💎", snap.get("top_longterm") or [])
        + '</div>')

    # ── Alerts ───────────────────────────────────────────────────────────────
    if alerts:
        alerts_html = "".join(
            f'<div style="font-size:12px;padding:6px 0;border-top:0.5px solid #f1efea">'
            f'{badge(a["ticker"], "bad" if a["severity"]=="high" else "watch", "small")} '
            f'{esc(a["text"])}</div>' for a in alerts[:8])
    else:
        alerts_html = empty("No alerts — all monitored positions clear.")

    # ── Recovery watch + econ headlines (compact) ───────────────────────────
    recovery = snap.get("recovery") or []
    rec_html = "".join(
        f'<a href="{tv_url(r["ticker"])}" target="_blank" class="chip" title="Open TradingView chart">'
        f'{ {"Bottoming":"🔴","Recovering":"🟡","Trend Confirmed":"🟢"}.get(r.get("stage"), "") } '
        f'{r["ticker"]}</a>' for r in recovery) or empty("none identified")

    econ = snap.get("econ_headlines") or []
    econ_html = "".join(
        f'<div style="font-size:11px;padding:5px 0;border-top:0.5px solid #f1efea">'
        f'<span style="color:#898781">{esc(h.get("when",""))}</span> {esc(h.get("title",""))[:110]}</div>'
        for h in econ[:4]) or empty("none captured this scan")

    # ── Recent research + dashboards ────────────────────────────────────────
    idx = _read_json(OUTPUT_DIR / "research_index.json") or {}
    recent_research = sorted(idx.values(), key=lambda r: r.get("updated_at") or "",
                             reverse=True)[:6]
    rr_html = "".join(
        f'<a href="/research/{r["ticker"]}.html" style="display:flex;justify-content:space-between;'
        f'text-decoration:none;color:#0b0b0b;font-size:12px;padding:5px 0;border-top:0.5px solid #f1efea">'
        f'<span><b>{r["ticker"]}</b> <span style="color:#898781">{esc(r.get("sector") or "")}</span></span>'
        f'{action_badge(r.get("conv_action"), "small")}</a>' for r in recent_research
    ) or empty("none yet")

    dashboards = _latest(DASHBOARD_PATTERNS, 5)
    dash_html = "".join(
        f'<div style="font-size:12px;padding:4px 0"><a href="/{f.name}">📊 {f.name}</a></div>'
        for f in dashboards) or empty("none yet")

    body = (
        _regime_card()
        + card("Market Status", market_html, "🌐")
        + _ai_sentiment_teaser()
        + card("", hero, pad="20px 24px")
        + card("Portfolio", pf_body, "💼", right='<a href="/portfolio" style="font-size:11px">View all →</a>')
        + card("Today's Opportunities", opp_cols, "🎯",
              right='<a href="/scanner" style="font-size:11px">Run new scan →</a>')
        + _scan_analyze_card()
        + f"""<div style="display:flex;gap:16px;flex-wrap:wrap">
             <div style="flex:1;min-width:260px">{card("Recent Alerts", alerts_html, "🔔")}
                {card("Turnaround Watch", rec_html, "🔧")}</div>
             <div style="flex:1;min-width:260px">{card("Latest News", econ_html, "📰")}
                {card("Recent Research", rr_html, "🔎", right='<a href="/research" style="font-size:11px">Library →</a>')}
                {card("Recent Dashboards", dash_html, "📊")}</div>
           </div>"""
    )
    # onJobFinished lives in ANALYZE_JS: it has to know whether the panel is
    # showing an analysis before deciding to reload the page out from under it.
    return body, REGIME_JS + ANALYZE_JS + longterm_view.TABLE_JS


# ─────────────────────────────────────────────────────────────────────────────
# AI SENTIMENT
# ─────────────────────────────────────────────────────────────────────────────


def _leadership_html() -> str:
    """Benchmark leadership under the Market Status tiles.

    The tiles above come from snapshot.json and only move when a scan runs;
    this reads the ETF profiles, which refresh independently. Both are
    labelled with where they came from rather than blended into one
    apparently-live number.

    It answers what the regime badge cannot: the badge says how strong, this
    says which part of the market is carrying it. QQQ leading while the Dow
    lags is a different tape from all three advancing together, even when
    the badge reads the same.
    """
    from stockanalysis.core import etf_profile
    from stockanalysis.core.market_regime import benchmark_leadership
    try:
        profiles = etf_profile.load_profiles(OUTPUT_DIR)
        lead = benchmark_leadership(profiles)
    except Exception as e:
        print(f"[Dashboard] leadership unavailable ({e})")
        return ""
    if not lead.get("legs"):
        return ""

    tone = {"good": ("#E1F5EE", "#085041"), "watch": ("#FAEEDA", "#633806"),
            "bad": ("#FCEBEB", "#791F1F"), "muted": ("#f1efea", "#52514e")}
    bg, fg = tone.get(lead.get("tone"), tone["muted"])

    legs = "".join(
        f'<span style="margin-right:14px">{esc(l["ticker"])} '
        f'<b style="color:{"#0F6E56" if l["above_200ma"] else "#A32D2D"}">'
        f'{"↑" if l["above_200ma"] else "↓"} '
        f'{l["dist_ma200"]:+.1f}%</b></span>'
        for l in lead["legs"])

    vix = profiles.get("^VIX") or {}
    vix_html = ""
    if vix.get("price") is not None:
        # Above its 200-day means rising fear — the opposite reading to
        # every other leg here, so it is coloured and worded separately.
        calm = (vix.get("dist_ma200") or 0) < 0
        vix_html = (f'<span style="margin-right:14px">VIX '
                    f'<b style="color:{"#0F6E56" if calm else "#A32D2D"}">'
                    f'{vix["price"]:.1f} {"calm" if calm else "elevated"}</b></span>')

    missing = ""
    if lead.get("missing"):
        missing = (f'<div style="margin-top:3px;font-size:10px">No 200-day data '
                   f'for {esc(", ".join(lead["missing"]))} — this read uses the rest.</div>')

    stamp = (profiles.get("SPY") or {}).get("quote_at") or ""
    return (f'<div style="background:{bg};color:{fg};border-radius:9px;'
            f'padding:9px 12px;margin-top:10px;font-size:11.5px">'
            f'<b>{esc(lead["verdict"])}</b> — {esc(lead["detail"])}'
            f'<div style="margin-top:5px;font-size:10.5px;opacity:.85">'
            f'{legs}{vix_html}<span style="opacity:.7">vs 200-day'
            f'{" · ETF profiles as of " + esc(stamp[:16]) if stamp else ""}</span></div>'
            f'{missing}</div>')


def _ai_sentiment_teaser() -> str:
    """Compact score+link card for the home Dashboard — hidden until the
    first AI Sentiment refresh has run, same "no stack trace on fresh
    checkout" convention as the rest of this file."""
    snap = _read_json(OUTPUT_DIR / "ai_sentiment.json")
    if not snap:
        return ""
    s = snap.get("sentiment") or {}
    score = s.get("score")
    status = "good" if (score or 0) > 70 else ("watch" if (score or 0) >= 40 else "bad")
    body = (
        '<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">'
        f'<div style="font-size:28px;font-weight:700">{score if score is not None else "—"}'
        f'<span style="font-size:13px;color:#898781">/100</span></div>'
        + badge(s.get("label", "—"), status)
        + '<a href="/ai-sentiment" style="margin-left:auto;font-size:11px">View AI Sentiment →</a>'
        + '</div>')
    return card("AI Sentiment", body, "🤖")


def _refresh_ai_sentiment_form(generated_at: str = "") -> str:
    note = (f'<span style="font-size:11px;color:#898781;margin-left:10px">'
            f'Last updated {esc(generated_at)}</span>' if generated_at else "")
    return (
        '<form onsubmit="submitJob(event, this, null); return false;">'
        '<input type="hidden" name="action" value="ai_sentiment">'
        '<button class="btn">Refresh AI Sentiment</button>'
        f'{note}</form>')


def _ticker_chip_ai(s: dict) -> str:
    chg = s.get("day_chg_pct")
    green = (chg or 0) >= 0
    color = "#0F6E56" if green else "#A32D2D"
    bg = "#E1F5EE" if green else "#FCEBEB"
    rsi = s.get("rsi_14")
    ema = s.get("above_20ema")
    ema_dot = "🟢" if ema else ("🔴" if ema is False else "")
    return (f'<a href="/research/{esc(s["ticker"])}.html" style="text-decoration:none;display:inline-block;'
            f'background:{bg};color:{color};border-radius:8px;padding:8px 12px;margin:0 6px 6px 0;min-width:88px">'
            f'<div style="font-weight:700;font-size:13px">{esc(s["ticker"])}</div>'
            f'<div style="font-size:11px">{fmt_pct(chg)}</div>'
            f'<div style="font-size:10px;color:{color};opacity:.85">RSI {rsi if rsi is not None else "—"} {ema_dot}</div>'
            f'</a>')


def _tier_card(title: str, icon: str, tier: dict) -> str:
    snaps = tier.get("tickers") or []
    status = tier.get("status") or {}
    chips = "".join(_ticker_chip_ai(s) for s in snaps) or empty("no data")
    all_red = badge("ALL RED", "bad", "small") if status.get("all_red") else ""
    header = (f'<div style="font-size:11px;color:#898781;margin-bottom:8px">'
              f'{status.get("green", 0)} green / {status.get("red", 0)} red {all_red}</div>')
    return card(title, header + f'<div>{chips}</div>', icon)


_SENTIMENT_STATUS = lambda score: ("good" if (score or 0) > 70
                                   else "watch" if (score or 0) >= 40 else "bad")
_RISK_STATUS = {"Aggressive buying": "good", "Selective buying": "good",
               "Neutral": "watch", "Reduce position size": "watch", "Defensive": "bad"}
_ROTATION_STATUS = {"Risk-On": "good", "Mixed": "watch", "Risk-Off": "bad", "Unknown": "muted"}


def ai_sentiment_page() -> tuple[str, str]:
    snap = _read_json(OUTPUT_DIR / "ai_sentiment.json")
    if not snap:
        body = card("", empty(
            "No AI sentiment data yet. Click “Refresh AI Sentiment” below to pull "
            "tier snapshots (Leadership / Networking / Power), macro inputs "
            "(10Y yield, DXY, SOXX), sector rotation, and an AI Health Index — "
            "this page fills in with a 0-100 sentiment score once the first "
            "refresh completes.") + f'<div style="margin-top:14px">{_refresh_ai_sentiment_form()}</div>',
            pad="40px")
        extra_js = "function onJobFinished(j) { if (j.kind === 'ai_sentiment') setTimeout(() => location.reload(), 1200); }"
        return body, extra_js

    sentiment = snap.get("sentiment") or {}
    risk = snap.get("risk") or {}
    rotation = snap.get("rotation") or {}
    health = snap.get("ai_health") or {}
    macro = snap.get("macro") or {}
    tiers = snap.get("tiers") or {}
    yld = snap.get("yield") or {}
    dxy = snap.get("dxy") or {}
    soxx = snap.get("soxx") or {}
    vwap = snap.get("nvda_above_vwap")

    # ── Hero: composite score ───────────────────────────────────────────────
    rotation_banner = (
        '<div style="background:#FCEBEB;color:#791F1F;padding:10px 14px;'
        'border-radius:8px;font-size:12px;margin-bottom:12px">'
        '⚠ All three AI tiers are red while the broader market (SPY) is flat — '
        'this looks like an AI-specific rotation, not a broad sell-off.</div>'
        if sentiment.get("ai_specific_rotation") else "")
    # component strip: the six v2 formula inputs with their weights
    comp_labels = {"momentum": "Momentum", "breadth": "Breadth",
                   "rs_vs_qqq": "RS vs QQQ", "volume": "Volume",
                   "macro": "Macro", "news_earnings": "News/Earnings"}
    components = sentiment.get("components") or {}
    weights = sentiment.get("weights") or {}
    missing = set(sentiment.get("missing") or [])
    comp_cells = ""
    for key, label in comp_labels.items():
        val = components.get(key)
        if val is None:
            continue
        color = "#0F6E56" if val >= 70 else "#8a6d1a" if val >= 45 else "#A32D2D"
        note = ' <span style="color:#898781">(no data)</span>' if key in missing else ""
        comp_cells += (
            f'<div style="min-width:96px"><div style="font-size:10px;color:#898781">'
            f'{label} · {weights.get(key, 0) * 100:.0f}%</div>'
            f'<div style="font-size:16px;font-weight:700;color:{color}">{val:.0f}{note}</div>'
            f'<div style="background:#f1efea;border-radius:2px;height:4px">'
            f'<div style="width:{val:.0f}%;height:4px;border-radius:2px;background:{color}"></div></div></div>')
    comp_strip = (f'<div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:14px">{comp_cells}</div>'
                  if comp_cells else "")

    hero = (
        rotation_banner
        + '<div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">'
        + '<div><div style="font-size:11px;font-weight:600;color:#898781">AI MARKET SENTIMENT</div>'
        + f'<div style="font-size:40px;font-weight:650">{sentiment.get("score", "—")}'
        + '<span style="font-size:18px;color:#898781">/100</span></div></div>'
        + f'<div style="flex:1;min-width:220px">{badge(sentiment.get("label", "—"), _SENTIMENT_STATUS(sentiment.get("score")))}'
        + '<div style="font-size:10px;color:#898781;margin-top:4px">15-name supply-chain basket · '
          '40% momentum · 20% breadth · 15% RS vs QQQ · 10% volume · 10% macro · 5% news/earnings</div></div>'
        + '</div>' + comp_strip)

    # ── Macro inputs strip ──────────────────────────────────────────────────
    yld_bps = yld.get("change_bps")
    yld_color = "#A32D2D" if (yld_bps or 0) > 0 else "#0F6E56"
    yld_level = yld.get("level_pct")
    yld_level_str = f"{yld_level:.2f}%" if yld_level is not None else "—"
    yld_bps_str = f"{yld_bps:+d}bp" if yld_bps is not None else ""
    dxy_chg = dxy.get("change_pct")
    inputs_html = (
        '<div style="display:flex;gap:24px;flex-wrap:wrap">'
        + f'<div><div style="font-size:11px;color:#898781">10Y Yield</div>'
          f'<div style="font-size:15px;font-weight:600">{yld_level_str} '
          f'<span style="font-size:11px;color:{yld_color}">{yld_bps_str}</span></div></div>'
        + f'<div><div style="font-size:11px;color:#898781">DXY</div>'
          f'<div style="font-size:15px;font-weight:600">{dxy.get("level", "—")} '
          f'<span style="font-size:11px">{fmt_pct(dxy_chg) if dxy_chg is not None else ""}</span></div></div>'
        + f'<div><div style="font-size:11px;color:#898781">SOXX</div>'
          f'<div style="font-size:15px;font-weight:600">{fmt_money(soxx.get("price")) if soxx.get("price") is not None else "—"} '
          + (badge("ABOVE 20DMA", "good", "small") if soxx.get("above_20ma")
             else badge("BELOW 20DMA", "bad", "small") if soxx.get("above_20ma") is False else "")
          + '</div></div>'
        + f'<div><div style="font-size:11px;color:#898781">NVDA vs VWAP</div>'
          f'<div style="font-size:15px;font-weight:600">'
          + (badge("ABOVE", "good", "small") if vwap else
             badge("BELOW", "bad", "small") if vwap is False else badge("N/A", "muted", "small"))
          + '</div></div>'
        + '</div>')

    # ── Tier cards ───────────────────────────────────────────────────────────
    tier_cols = (
        '<div style="display:flex;gap:16px;flex-wrap:wrap">'
        + f'<div style="flex:1;min-width:220px">{_tier_card("Tier 1 · Leadership", "🏆", tiers.get("tier1") or {})}</div>'
        + f'<div style="flex:1;min-width:220px">{_tier_card("Tier 2 · Networking", "🔌", tiers.get("tier2") or {})}</div>'
        + f'<div style="flex:1;min-width:220px">{_tier_card("Tier 3 · Power", "⚡", tiers.get("tier3") or {})}</div>'
        + '</div>')

    # ── Risk Score ───────────────────────────────────────────────────────────
    risk_drivers = "".join(
        f'<div style="font-size:11px;padding:3px 0;color:#52514e">· {esc(d)}</div>'
        for d in risk.get("drivers") or []) or empty("no data")
    risk_html = (
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
        + f'<div style="font-size:28px;font-weight:700">{risk.get("score", 0):+d}</div>'
        + badge(risk.get("label", "—"), _RISK_STATUS.get(risk.get("label"), "muted"))
        + '</div>' + risk_drivers)

    # ── Rotation Detection ───────────────────────────────────────────────────
    sector_chips = "".join(
        f'<span class="chip" style="border-left:3px solid {"#0F6E56" if (s.get("chg_pct") or 0) >= 0 else "#A32D2D"}">'
        f'{esc(s.get("label"))} {fmt_pct(s.get("chg_pct"))}</span>'
        for s in snap.get("sectors") or [] if s.get("chg_pct") is not None
    ) or empty("no sector data")
    rotation_html = (
        f'<div style="margin-bottom:10px">{badge(rotation.get("state", "—"), _ROTATION_STATUS.get(rotation.get("state"), "muted"))}</div>'
        f'<div>{sector_chips}</div>')

    # ── AI Health Index ──────────────────────────────────────────────────────
    idx = health.get("index")
    health_html = (
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
        + f'<div style="font-size:28px;font-weight:700">{idx if idx is not None else "—"}'
        + '<span style="font-size:14px;color:#898781">/100</span></div>'
        + badge(health.get("label", "—"), "good" if (idx or 0) > 60 else "watch" if (idx or 0) > 40 else "bad")
        + '</div>'
        + '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;font-size:11px">'
        + f'<div><div style="color:#898781">Above 20 EMA</div><div style="font-weight:600">{health.get("pct_above_20ema", "—")}%</div></div>'
        + f'<div><div style="color:#898781">Avg RSI</div><div style="font-weight:600">{health.get("avg_rsi", "—")}</div></div>'
        + f'<div><div style="color:#898781">Avg Daily Return</div><div style="font-weight:600">{fmt_pct(health.get("avg_daily_return_pct"))}</div></div>'
        + f'<div><div style="color:#898781">Adv/Decl</div><div style="font-weight:600">{health.get("advancers", 0)}/{health.get("decliners", 0)} ({health.get("adv_decl_ratio", "—")})</div></div>'
        + '</div>')

    # ── Macro Event Filter ───────────────────────────────────────────────────
    macro_events = "".join(
        f'<div style="font-size:11px;padding:5px 0;border-top:0.5px solid #f1efea">'
        f'<b>{esc(ev.get("when", ""))}</b> {esc(ev.get("title", ""))} '
        f'<span style="color:#898781">[{esc(ev.get("impact", ""))}]</span></div>'
        for ev in macro.get("events") or []
    ) or empty("none in the next 48h")
    macro_html = (
        f'<div style="margin-bottom:8px">'
        + (badge("⚠ CAUTION", "bad") if macro.get("caution") else badge("CLEAR", "good"))
        + f' <span style="font-size:11px;color:#898781">{esc(macro.get("note", ""))}</span></div>'
        + macro_events)

    # ── Relative Strength ────────────────────────────────────────────────────
    rs = snap.get("relative_strength") or {}
    benches = ["SPY", "QQQ", "SMH"]
    if rs:
        rs_rows = ""
        for ticker, data in rs.items():
            cells = ""
            for b in benches:
                d = (data or {}).get(b)
                if not d:
                    cells += '<td style="text-align:right">—</td>'
                else:
                    color = ("#0F6E56" if d["trend"] == "rising"
                             else "#A32D2D" if d["trend"] == "falling" else "#898781")
                    arrow = "▲" if d["trend"] == "rising" else "▼" if d["trend"] == "falling" else "·"
                    cells += f'<td style="text-align:right;color:{color}">{arrow} {fmt_pct(d.get("chg_pct"))}</td>'
            rs_rows += f'<tr><td><b>{esc(ticker)}</b></td>{cells}</tr>'
        rs_html = (f'<table><thead><tr><th>Ticker</th>'
                  + "".join(f'<th style="text-align:right">vs {b}</th>' for b in benches)
                  + f'</tr></thead><tbody>{rs_rows}</tbody></table>')
    else:
        rs_html = empty("no data")

    # ── All AI Tickers (full data/watchlists.json "AI" universe) ────────────
    all_ai = snap.get("all_ai") or []
    if all_ai:
        research_idx = _read_json(OUTPUT_DIR / "research_index.json") or {}
        all_ai_rows = []
        for r in all_ai:
            ridx = research_idx.get(r["ticker"]) or {}
            ed = ridx.get("earnings_date")
            all_ai_rows.append({
                **r,
                "earnings_date": ed if ed and ed != "N/A" else None,
                "days_to_earnings": ridx.get("days_to_earnings"),
                "week52_low": ridx.get("week52_low"),
                "week52_high": ridx.get("week52_high"),
            })
        all_ai_html = '<div id="all-ai-root" style="overflow-x:auto"></div>'
        all_ai_js = f"""
        const ALL_AI_ROWS = {json.dumps(all_ai_rows)};
        let aiSortKey = 'ticker', aiSortDir = 1;
        function aiSetSort(key) {{
          aiSortDir = (aiSortKey === key) ? -aiSortDir : 1;
          aiSortKey = key;
          renderAllAI();
        }}
        function renderAllAI() {{
          const root = document.getElementById('all-ai-root');
          if (!root) return;
          const rows = [...ALL_AI_ROWS].sort((a, b) => {{
            const av = a[aiSortKey], bv = b[aiSortKey];
            if (av == null && bv == null) return 0;
            if (av == null) return 1;
            if (bv == null) return -1;
            return (av > bv ? 1 : av < bv ? -1 : 0) * aiSortDir;
          }});
          const arrow = key => aiSortKey === key ? (aiSortDir === 1 ? ' ▲' : ' ▼') : '';
          const th = (key, label) => `<th style="cursor:pointer;white-space:nowrap" onclick="aiSetSort('${{key}}')">${{label}}${{arrow(key)}}</th>`;
          root.innerHTML = `<table><thead><tr>
            ${{th('ticker', 'Ticker')}}${{th('price', 'Price')}}
            ${{th('week52_low', '52W Low')}}${{th('week52_high', '52W High')}}
            ${{th('day_chg_pct', 'Chg%')}}
            ${{th('rsi_14', 'RSI')}}${{th('above_20ema', 'Above 20 EMA')}}${{th('above_50ma', 'Above 50 MA')}}
            ${{th('days_to_earnings', 'DOE')}}
          </tr></thead><tbody>
          ${{rows.map(r => {{
            const chg = r.day_chg_pct;
            const color = (chg ?? 0) >= 0 ? '#0F6E56' : '#A32D2D';
            const flag = v => v === true ? '✓' : v === false ? '✗' : '—';
            return `<tr>
              <td><a href="/research/${{r.ticker}}.html"><b>${{r.ticker}}</b></a></td>
              <td>${{r.price != null ? '$' + r.price.toFixed(2) : '—'}}</td>
              <td>${{r.week52_low != null ? '$' + r.week52_low.toFixed(2) : '—'}}</td>
              <td>${{r.week52_high != null ? '$' + r.week52_high.toFixed(2) : '—'}}</td>
              <td style="color:${{color}}">${{chg != null ? (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%' : '—'}}</td>
              <td>${{r.rsi_14 ?? '—'}}</td>
              <td>${{flag(r.above_20ema)}}</td>
              <td>${{flag(r.above_50ma)}}</td>
              <td style="white-space:nowrap">${{r.days_to_earnings ?? '—'}}</td>
            </tr>`;
          }}).join('')}}
          </tbody></table>`;
        }}
        document.addEventListener('DOMContentLoaded', renderAllAI);
        """
    else:
        all_ai_html = empty("no data")
        all_ai_js = ""

    # ── Supply-chain basket sections (v2) ────────────────────────────────────
    breadth = snap.get("breadth") or {}
    breadth_rows = ""
    for label, pct in (breadth.get("conditions") or {}).items():
        if pct is None:
            continue
        color = "#0F6E56" if pct >= 70 else "#8a6d1a" if pct >= 40 else "#A32D2D"
        breadth_rows += (
            f'<div style="display:flex;align-items:center;gap:10px;padding:4px 0">'
            f'<span style="min-width:130px;font-size:11px">{esc(label)}</span>'
            f'<div style="flex:1;background:#f1efea;border-radius:3px;height:8px">'
            f'<div style="width:{pct}%;height:8px;border-radius:3px;background:{color}"></div></div>'
            f'<span style="min-width:38px;text-align:right;font-size:12px;font-weight:600">{pct}%</span></div>')
    breadth_html = (breadth_rows +
                    f'<div style="font-size:10px;color:#898781;margin-top:6px">'
                    f'{breadth.get("n", 0)} basket names · breadth often weakens before the leaders do</div>'
                    ) if breadth_rows else empty("no breadth data")

    leadership_rows = "".join(
        f'<tr><td style="font-size:12px"><b>{esc(g["sector"])}</b>'
        f' <span style="font-size:10px;color:#898781">{", ".join(g["tickers"])}</span></td>'
        f'<td style="text-align:right;color:{"#0F6E56" if (g["chg_pct"] or 0) >= 0 else "#A32D2D"};font-weight:600">'
        f'{fmt_pct(g["chg_pct"])}</td>'
        f'<td style="text-align:right;color:{"#0F6E56" if (g["chg_5d_pct"] or 0) >= 0 else "#A32D2D"}">'
        f'{fmt_pct(g["chg_5d_pct"])}</td></tr>'
        for g in snap.get("leadership") or [])
    leadership_html = (f'<table><thead><tr><th>Layer</th><th style="text-align:right">1d</th>'
                      f'<th style="text-align:right">5d</th></tr></thead>'
                      f'<tbody>{leadership_rows}</tbody></table>'
                      if leadership_rows else empty("no leadership data"))

    leading_html = ""
    for g in snap.get("leading_groups") or []:
        chg5 = g.get("chg_5d_pct")
        chips = "".join(
            f'<span class="chip" style="border-left:3px solid '
            f'{"#0F6E56" if (m.get("day_chg_pct") or 0) >= 0 else "#A32D2D"}">'
            f'{esc(m["ticker"])} {fmt_pct(m.get("day_chg_pct"))}</span>'
            for m in g.get("members") or [])
        warn = badge("⚠ ROLLING OVER", "bad", "small") if g.get("warning") else ""
        leading_html += (
            f'<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700">'
            f'{esc(g["group"])}</span> <span style="font-size:10px;color:#898781">5d '
            f'{fmt_pct(chg5) if chg5 is not None else "—"}</span> {warn}'
            f'<div style="margin-top:3px">{chips}</div></div>')
    leading_html = leading_html or empty("no leading-indicator data")

    basket_v2 = snap.get("basket_v2") or []
    flag = lambda v: "✓" if v is True else "✗" if v is False else "—"
    basket_rows = "".join(
        f'<tr><td><a href="/research/{esc(s["ticker"])}.html"><b>{esc(s["ticker"])}</b></a></td>'
        f'<td style="font-size:11px">{esc(s.get("category") or "—")}</td>'
        f'<td style="text-align:right;font-size:11px">{s.get("weight", 0)}%</td>'
        f'<td style="text-align:right">{fmt_money(s.get("price")) if s.get("price") is not None else "—"}</td>'
        f'<td style="text-align:right;color:{"#0F6E56" if (s.get("day_chg_pct") or 0) >= 0 else "#A32D2D"}">{fmt_pct(s.get("day_chg_pct"))}</td>'
        f'<td style="text-align:right">{fmt_pct(s.get("chg_5d_pct"))}</td>'
        f'<td style="text-align:right">{fmt_pct(s.get("chg_20d_pct"))}</td>'
        f'<td style="text-align:right">{s.get("rsi_14") if s.get("rsi_14") is not None else "—"}</td>'
        f'<td style="text-align:center">{flag(s.get("above_20ema"))}</td>'
        f'<td style="text-align:center">{flag(s.get("above_200ema"))}</td>'
        f'<td style="text-align:center">{flag(s.get("high_20d"))}</td>'
        f'<td style="text-align:right">{s.get("vol_ratio") if s.get("vol_ratio") is not None else "—"}×</td></tr>'
        for s in basket_v2)
    basket_html = (
        '<div style="overflow-x:auto"><table style="white-space:nowrap"><thead><tr>'
        '<th>Ticker</th><th>Layer</th><th style="text-align:right">Wt</th>'
        '<th style="text-align:right">Price</th><th style="text-align:right">1d</th>'
        '<th style="text-align:right">5d</th><th style="text-align:right">20d</th>'
        '<th style="text-align:right">RSI</th><th>20E</th><th>200E</th><th>20dH</th>'
        f'<th style="text-align:right">Vol</th></tr></thead><tbody>{basket_rows}</tbody></table></div>'
        if basket_rows else empty("no basket data — refresh to fetch"))

    body = (
        card("", hero, pad="20px 24px")
        + f"""<div style="display:flex;gap:16px;flex-wrap:wrap">
             <div style="flex:1;min-width:300px">{card("AI Breadth", breadth_html, "📶")}</div>
             <div style="flex:1;min-width:300px">{card("Relative Leadership (supply chain)", leadership_html, "🥇")}</div>
           </div>"""
        + card(f"AI Supply-Chain Basket ({len(basket_v2)})", basket_html, "🧺")
        + card("Leading Indicators (early-warning names)", leading_html, "🚨")
        + card("Macro Inputs", inputs_html, "📉")
        + tier_cols
        + f"""<div style="display:flex;gap:16px;flex-wrap:wrap">
             <div style="flex:1;min-width:280px">{card("Risk Score", risk_html, "🎯")}
                {card("Rotation Detection", rotation_html, "🔁")}</div>
             <div style="flex:1;min-width:280px">{card("AI Health Index", health_html, "❤️")}
                {card("Macro Event Filter", macro_html, "📅")}</div>
           </div>"""
        + card("Relative Strength (vs SPY / QQQ / SMH)", rs_html, "📐")
        + card(f"All AI Tickers ({len(all_ai)})", all_ai_html, "📋")
        + card("Refresh", _refresh_ai_sentiment_form(snap.get("generated_at", "")), "🔄")
    )
    extra_js = (
        "function onJobFinished(j) { if (j.kind === 'ai_sentiment') setTimeout(() => location.reload(), 1200); }"
        + all_ai_js)
    return body, extra_js


# ─────────────────────────────────────────────────────────────────────────────
# SCANNER
# ─────────────────────────────────────────────────────────────────────────────

SCAN_STEP_LABELS = [
    ("fetch_qqq", "Market Data"), ("scan", "Fundamentals + Technicals"),
    ("grade", "Grading & CANSLIM"), ("writing", "Strategy Scores"),
    ("research", "Research Pages"), ("done", "Dashboard Complete"),
]


def longterm_page(query: dict | None = None) -> tuple[str, str]:
    """The Long-Term Buy Engine — see webapp/longterm_view.py. Takes the
    query string because its filters and regime override live in the URL."""
    from . import longterm_view
    return longterm_view.longterm_page(query)


def csp_page(query: dict | None = None) -> tuple[str, str]:
    """The Cash-Secured Put engine — see webapp/csp_view.py.

    Takes the query string because its ticker lookup lives in the URL.
    Renders the last stored scan; distinct from /longterm, whose company
    verdict it consumes rather than recomputes."""
    from stockanalysis.webapp import csp_view
    return csp_view.csp_page(query)


def leaders_page(query: dict | None = None) -> tuple[str, str]:
    """The market → sector → industry → stock → setup scan — see
    webapp/leaders_view.py.

    Takes the query dict for the same reason /shortside does: it renders from
    a stored snapshot and its filters belong in the URL, not in session state.
    """
    from stockanalysis.webapp import leaders_view
    return leaders_view.leaders_page(query)


def shortside_page(query: dict | None = None) -> tuple[str, str]:
    """The two-sided decision engine — see webapp/shortside_view.py.

    Takes the query string because its manual ticker list lives in the
    URL. Scores every name long AND short; distinct from /longterm, whose
    quality rejections it treats as evidence rather than as a verdict."""
    from stockanalysis.webapp import shortside_view
    return shortside_view.shortside_page(query)


def trend_page(query: dict | None = None) -> tuple[str, str]:
    """The trend regime classifier — see webapp/trend_view.py.

    Takes the query string because both its state lives there: which of the
    five filters is active, and any manually typed ticker list (which is
    classified live rather than read from the snapshot).
    """
    from stockanalysis.webapp import trend_view
    return trend_view.trend_page(query)


def retirement_page(query: dict | None = None) -> tuple[str, str]:
    """The retirement and financial-independence engine —
    see webapp/retirement_view.py.

    Takes the query dict so that ?refresh=1 can force a recompute; the
    page otherwise renders the stored snapshot and only recomputes when
    the profile has changed under it.
    """
    from stockanalysis.webapp import retirement_view
    return retirement_view.retirement_page(query)


def institutional_page(query: dict | None = None) -> tuple[str, str]:
    """Institutional long call/put positions from 13F —
    see webapp/institutional_view.py.

    Renders a stored snapshot rather than scanning: a run streams a ~400 MB
    SEC information table. Takes the query string for its ticker lookup and
    the put/call leaderboard toggle.
    """
    from stockanalysis.webapp import institutional_view
    return institutional_view.institutional_page(query)


def compounder_page(query: dict | None = None) -> tuple[str, str]:
    """The Future Compounder engine — see webapp/compounder_view.py.

    Renders a stored scan rather than scanning live: a run is six network
    calls per company across the whole theme library. Takes the query string
    because its ticker filter lives in the URL, the same way /shortside
    does."""
    from stockanalysis.webapp import compounder_view
    return compounder_view.compounder_page(query)


def stockdaytrade_page() -> tuple[str, str]:
    """The stock day-trade scanner — see webapp/stockdaytrade_view.py.

    Distinct from /daytrade, which is the SPY 0DTE engine in spydaytrader.
    Both are intraday and they share nothing: that one trades one index's
    options off a signal engine, this one ranks equities on §10 confluence
    across small/mid/large-cap profiles and reads no fundamentals at all."""
    from stockanalysis.webapp import stockdaytrade_view
    return stockdaytrade_view.stockdaytrade_page()


def screener_page() -> tuple[str, str]:
    """Screener lives in its own module — see screener_view.py. Scanner runs
    the pipeline that produces the data; Screener queries what it produced."""
    from stockanalysis.webapp import screener_view
    return screener_view.screener_page()


def scanner_page(query: dict | None = None) -> tuple[str, str]:
    # ?tickers=NVDA,AMD prefills the ad-hoc box — how /longterm's "Rescan
    # these" arrives, so a filtered set becomes a scan without a copy-paste.
    # Declaring the parameter is all the routing needs: app._wants_query
    # detects it from the signature.
    prefill = ((query or {}).get("tickers") or [""])[0].strip()
    steps_html = "".join(
        f'<div id="step-{key}" style="flex:1;text-align:center;padding:10px 6px;'
        f'border-radius:8px;background:#f1efea;font-size:11px;font-weight:600;'
        f'color:#898781;transition:.2s">{i+1}. {label}</div>'
        + ('<div style="align-self:center;color:#d9d7ce">→</div>' if i < len(SCAN_STEP_LABELS) - 1 else "")
        for i, (key, label) in enumerate(SCAN_STEP_LABELS))
    pipeline = (f'<div style="display:flex;align-items:stretch;gap:4px;margin-bottom:10px">{steps_html}</div>'
                f'<div id="scan-progress-wrap" style="display:none">'
                f'<div id="scan-progress-label" style="font-size:11px;color:#898781;margin-bottom:4px"></div>'
                f'{progress_bar(None)}</div>')

    # load_watchlists(), not a raw read: watchlists.json nests AI sublists on
    # disk and this needs the flat "AI: Power" view.
    from stockanalysis.reporting.research import (
        load_watchlists, tree_ordered_names, SUBLIST_SEP)
    from stockanalysis.webapp.api import ALL_UNIVERSES_SENTINEL
    watchlists = load_watchlists()
    builtin_universes = ("daytrade", "watchlist", "longterm", "dividend", "sp500")
    # Expandable per-category ticker browser + editor: a + button per category
    # unfolds its ticker list (chips deep-link into the Research Library);
    # each list supports add / edit / remove via /api/watchlist/toggle. Chips
    # render client-side from UNIVERSE_DATA so edits update in place.
    _user_names = sorted(n for n, t in watchlists.items()
                         if t and n not in builtin_universes)

    # tree order: parent immediately followed by its "Parent: Child" sublists,
    # instead of children scattering alphabetically among unrelated lists
    univ_names = [u for u in builtin_universes if watchlists.get(u)] \
        + tree_ordered_names(_user_names)
    universe_data = {n: watchlists.get(n) or [] for n in univ_names}
    # Rendering hints: indent children, and label them by leaf name only.
    univ_depth = {n: (1 if SUBLIST_SEP in n else 0) for n in univ_names}
    univ_label = {n: (n.split(SUBLIST_SEP, 1)[1] if SUBLIST_SEP in n else n)
                  for n in univ_names}
    # Integrated universe panel: replaces the old <select multiple> — the
    # checkbox picks categories to scan (serializes as name="universe", same
    # as the select did), the + expands the category in place for viewing
    # and add/edit/remove of its tickers.
    def _univ_row(i: int, name: str) -> str:
        depth = univ_depth.get(name, 0)
        # children sit under their parent with a tree elbow; the checkbox
        # still submits the full "AI: Power" name
        indent = (f'<span style="width:14px;flex-shrink:0;color:#d9d7ce;'
                  f'font-size:11px;text-align:center">└</span>' if depth else "")
        return (
            f'<div style="border-top:0.5px solid #f1efea;padding:3px 0'
            f'{";padding-left:12px" if depth else ""}">'
            f'<div style="display:flex;gap:6px;align-items:center">'
            f'{indent}'
            f'<input type="checkbox" name="universe" value="{esc(name)}" '
            f'title="Include {esc(name)} in the scan">'
            f'<button type="button" id="univ-btn-{i}" onclick="toggleUniv({i})" '
            f'title="Show tickers" style="background:none;border:1px solid #d9d7ce;'
            f'border-radius:4px;width:18px;height:18px;line-height:1;cursor:pointer;'
            f'font-weight:700;flex-shrink:0;font-size:11px">+</button>'
            f'<b style="font-size:11px;flex:1;min-width:0;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap'
            f'{";font-weight:500;color:#4a4945" if depth else ""}" '
            f'title="{esc(name)}">{esc(univ_label.get(name, name))}</b>'
            f'<span id="univ-count-{i}" style="font-size:10px;color:#898781">'
            f'({len(universe_data[name])})</span></div>'
            f'<div id="univ-wrap-{i}" style="display:none;margin:5px 0 4px 24px">'
            f'<div style="display:flex;gap:4px;margin-bottom:5px">'
            f'<input id="univ-add-{i}" placeholder="Add ticker…" '
            f'style="width:110px;font-size:11px" '
            f'onkeydown="if(event.key===\'Enter\'){{event.preventDefault();univAdd({i});}}">'
            f'<button type="button" class="btn secondary" style="font-size:10px;padding:2px 8px" '
            f'onclick="univAdd({i})">Add</button></div>'
            f'<div id="univ-list-{i}" style="max-height:170px;overflow-y:auto"></div>'
            f'</div></div>')

    def _univ_header(label: str) -> str:
        return (f'<div style="font-size:9px;font-weight:700;color:#898781;'
                f'text-transform:uppercase;letter-spacing:.4px;padding:5px 0 2px">{label}</div>')

    # One checkbox for "everything I track" — the backend expands the
    # sentinel (api.expand_all), so it can't drift from the category list
    # the way a client-side tick-them-all would. No +/expand button: there's
    # no single list behind it.
    _all_n = len({t for v in watchlists.values() for t in (v or [])})
    all_row = (
        f'<div style="border-top:0.5px solid #f1efea;padding:3px 0">'
        f'<div style="display:flex;gap:6px;align-items:center">'
        f'<input type="checkbox" name="universe" value="{ALL_UNIVERSES_SENTINEL}" '
        f'title="Scan every ticker across all watchlists">'
        f'<span style="width:18px;flex-shrink:0"></span>'
        f'<b style="font-size:11px;flex:1">ALL tickers</b>'
        f'<span style="font-size:10px;color:#898781">({_all_n})</span>'
        f'</div></div>')

    n_builtin = sum(1 for n in univ_names if n in builtin_universes)
    panel_rows = all_row + _univ_header("Built-in") + "".join(
        _univ_row(i, name) for i, name in enumerate(univ_names[:n_builtin]))
    if len(univ_names) > n_builtin:
        panel_rows += _univ_header("Watchlists") + "".join(
            _univ_row(i + n_builtin, name)
            for i, name in enumerate(univ_names[n_builtin:]))
    universe_panel = (
        f'<div style="width:280px;max-height:300px;overflow-y:auto;'
        f'border:1px solid #d9d7ce;border-radius:8px;background:#fff;'
        f'padding:2px 10px 6px">{panel_rows}</div>')

    form = f"""
    <form onsubmit="submitJob(event, this, null); return false;" style="display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap">
      <input type="hidden" name="action" value="scan">
      <div>
        {universe_panel}
        <div style="font-size:10px;color:#898781;margin-top:3px">
          ✓ tick categories to scan · + expands a category to view / add / ✎ edit / × remove tickers</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <div>
          <input name="tickers" placeholder="NVDA, AMD, MU…" style="min-width:220px"
                 value="{esc(prefill)}">
          <div style="font-size:10px;color:#898781;margin-top:3px">
            optional tickers (comma or space separated) — added to the selected
            categories, or scanned alone if none selected</div>
          {f'<div style="font-size:10px;color:#185FA5;margin-top:3px">'
           f'{len([t for t in prefill.replace(chr(44), chr(32)).split() if t])} '
           f'tickers loaded from the Long-Term page — nothing is selected '
           f'above, so Run Scan covers exactly these.</div>' if prefill else ''}
        </div>
        <label style="font-size:12px;display:flex;gap:6px;align-items:center">
          <input type="checkbox" name="portfolio" checked> include Portfolio management</label>
        <button class="btn">Run Scan</button>
        <span style="font-size:11px;color:#898781">writes CSVs, research pages, and a fresh dashboard
          · nothing selected = daytrade</span>
      </div>
    </form>"""

    # ── 52-week high/low screen ──────────────────────────────────────────
    # Rebuilds the 52_week_high / 52_week_low watchlists, which then appear
    # in the universe panel above — ticking one and running a scan grades it
    # through get_metrics(), where compute_put_candidate() already runs.
    from stockanalysis.core.fifty_two_week import (
        HIGH_LIST_NAME, LOW_LIST_NAME, DEFAULT_SOURCE)
    src_opts = "".join(
        f'<option value="{esc(n)}"'
        + (" selected" if n == DEFAULT_SOURCE[0] else "")
        + f'>{esc(n)} ({len(universe_data[n])})</option>'
        for n in univ_names)
    n_hi, n_lo = (len(watchlists.get(HIGH_LIST_NAME) or []),
                  len(watchlists.get(LOW_LIST_NAME) or []))
    lists_state = (
        f'<div style="font-size:11px;color:#898781;margin-top:6px">'
        f'current lists · <b>{esc(HIGH_LIST_NAME)}</b> ({n_hi}) · '
        f'<b>{esc(LOW_LIST_NAME)}</b> ({n_lo})'
        + ('' if (n_hi or n_lo) else ' — not built yet') + '</div>')
    form_52w = f"""
    <form onsubmit="submitJob(event, this, null); return false;"
          style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
      <input type="hidden" name="action" value="scan_52_week">
      <label style="font-size:11px;color:#898781">source universe<br>
        <select name="universe_52w" style="min-width:170px;font-size:12px">{src_opts}</select></label>
      <label style="font-size:11px;color:#898781">within % of high<br>
        <input name="near_high_pct" value="2" style="width:70px"></label>
      <label style="font-size:11px;color:#898781">within % of low<br>
        <input name="near_low_pct" value="2" style="width:70px"></label>
      <button class="btn">Find 52-Week Highs / Lows</button>
    </form>
    <div style="font-size:11px;color:#898781;margin-top:6px">
      rewrites the <b>{esc(HIGH_LIST_NAME)}</b> and <b>{esc(LOW_LIST_NAME)}</b> watchlists
      · then tick <b>{esc(HIGH_LIST_NAME)}</b> in <i>Run a Scan</i> above to score them —
      put_candidate grades every scanned name for exhaustion
      (Put_Score / Put_Candidate / Put_Reason in the scan CSV)</div>
    {lists_state}"""

    # ── earnings-today screen ────────────────────────────────────────────
    from stockanalysis.core.earnings_today import LIST_NAME as EARNINGS_LIST
    from stockanalysis.webapp.api import ALL_UNIVERSES_SENTINEL
    n_earn = len(watchlists.get(EARNINGS_LIST) or [])
    # Same picker as the 52-week card, plus an "all watchlists" option so the
    # sweep-everything default survives a single-select. Sublists are indented
    # by leaf name, matching the universe panel above.
    earn_opts = (
        f'<option value="{ALL_UNIVERSES_SENTINEL}" selected>'
        f'— all watchlists ({len(watchlists)}) —</option>'
        + "".join(
            f'<option value="{esc(n)}">'
            + ("&nbsp;&nbsp;└ " if univ_depth.get(n) else "")
            + f'{esc(univ_label.get(n, n))} ({len(universe_data[n])})</option>'
            for n in univ_names))
    form_earn = f"""
    <form onsubmit="submitJob(event, this, null); return false;"
          style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
      <input type="hidden" name="action" value="scan_earnings_today">
      <label style="font-size:11px;color:#898781">source universe<br>
        <select name="universe_earn" style="min-width:200px;font-size:12px">{earn_opts}</select></label>
      <label style="font-size:11px;color:#898781">days ahead (0 = today only)<br>
        <input name="days_ahead" value="0" style="width:70px"></label>
      <button class="btn">Find Earnings Today</button>
    </form>
    <div style="font-size:11px;color:#898781;margin-top:6px">
      rewrites the <b>{esc(EARNINGS_LIST)}</b> watchlist
      · tick it in <i>Run a Scan</i> above to grade the reporters
      · dates only — yfinance carries no reliable before/after-open time</div>
    <div style="font-size:11px;color:#898781;margin-top:6px">
      current list · <b>{esc(EARNINGS_LIST)}</b> ({n_earn})
      {'' if n_earn else ' — not built yet'}</div>"""

    csvs = _latest("stock_scan_*.csv", 8)
    csvs = [f for f in csvs if not any(f.stem.endswith(s) for s in
                                       ("_daytrade", "_swing", "_longterm"))]
    csv_html = "".join(
        f'<div style="font-size:12px;padding:5px 0;border-top:0.5px solid #f1efea;'
        f'display:flex;justify-content:space-between">'
        f'<a href="/{f.name}">🗂 {f.name}</a>'
        f'<span style="color:#898781">{datetime.fromtimestamp(f.stat().st_mtime):%b %d %H:%M}</span></div>'
        for f in csvs) or empty("none yet")

    extra_js = (
        "const UNIVERSE_DATA = " + json.dumps(universe_data) + ";\n"
        "const UNIV_NAMES = " + json.dumps(univ_names) + ";\n"
        + """
    function renderUnivList(i) {
      const name = UNIV_NAMES[i];
      const tickers = UNIVERSE_DATA[name] || [];
      document.getElementById('univ-count-' + i).textContent = '(' + tickers.length + ')';
      document.getElementById('univ-list-' + i).innerHTML = tickers.map(t => `
        <span class="chip" style="font-size:10px;padding:2px 7px;display:inline-flex;gap:6px;align-items:center">
          <a href="/research?ticker=${t}" style="text-decoration:none">${t}</a>
          <a onclick="univEdit(${i}, '${t}')" title="Edit ${t}" style="cursor:pointer;color:#898781">✎</a>
          <a onclick="univDelete(${i}, '${t}')" title="Remove ${t}" style="cursor:pointer;color:#A32D2D;font-weight:700">×</a>
        </span>`).join('') || '<span style="font-size:11px;color:#898781">empty</span>';
    }
    function toggleUniv(i) {
      const wrap = document.getElementById('univ-wrap-' + i);
      const btn = document.getElementById('univ-btn-' + i);
      const open = wrap.style.display === 'none';
      wrap.style.display = open ? 'block' : 'none';
      btn.textContent = open ? '−' : '+';
      if (open) renderUnivList(i);
    }
    async function univToggleApi(name, ticker) {
      const res = await fetch('/api/watchlist/toggle', {
        method: 'POST', body: new URLSearchParams({name, ticker})});
      return res.json();
    }
    async function univAdd(i) {
      const name = UNIV_NAMES[i];
      const input = document.getElementById('univ-add-' + i);
      const t = (input.value || '').trim().toUpperCase();
      if (!t) return;
      if ((UNIVERSE_DATA[name] || []).includes(t)) {
        toast(t + ' is already in ' + name, 'err'); return;
      }
      try {
        const r = await univToggleApi(name, t);
        UNIVERSE_DATA[name] = (r.watchlists || {})[name] || [];
        input.value = '';
        renderUnivList(i);
        toast(t + ' added to ' + name, 'ok');
      } catch (e) { toast('Add failed: ' + e, 'err'); }
    }
    async function univDelete(i, t) {
      const name = UNIV_NAMES[i];
      if (!confirm('Remove ' + t + ' from "' + name + '"?')) return;
      try {
        const r = await univToggleApi(name, t);
        UNIVERSE_DATA[name] = (r.watchlists || {})[name] || [];
        renderUnivList(i);
        toast(t + ' removed from ' + name, 'ok');
      } catch (e) { toast('Remove failed: ' + e, 'err'); }
    }
    async function univEdit(i, t) {
      const name = UNIV_NAMES[i];
      const nu = prompt('Replace ' + t + ' in "' + name + '" with:', t);
      if (!nu) return;
      const T = nu.trim().toUpperCase();
      if (T === t) return;
      if ((UNIVERSE_DATA[name] || []).includes(T)) {
        toast(T + ' is already in ' + name, 'err'); return;
      }
      try {
        await univToggleApi(name, t);             // remove old
        const r = await univToggleApi(name, T);   // add new
        UNIVERSE_DATA[name] = (r.watchlists || {})[name] || [];
        renderUnivList(i);
        toast(t + ' → ' + T + ' in ' + name, 'ok');
      } catch (e) { toast('Edit failed: ' + e, 'err'); }
    }
    function onJobRunning(j) {
      if (j.kind !== 'scan') return;
      document.getElementById('scan-progress-wrap').style.display = 'block';
      document.getElementById('scan-progress-label').textContent = j.stage + (j.pct != null ? ' · ' + j.pct + '%' : '');
      const bar = document.querySelector('#scan-progress-wrap div div');
      if (bar && j.pct != null) bar.style.width = Math.max(2, j.pct) + '%';
      const stepIdx = ['fetch_qqq','scan','grade','writing','research','done']
        .findIndex(s => j.stage.startsWith(s));
      document.querySelectorAll('[id^="step-"]').forEach((el, i) => {
        el.style.background = i <= stepIdx ? '#E6F1FB' : '#f1efea';
        el.style.color = i <= stepIdx ? '#0C447C' : '#898781';
      });
    }
    function onJobFinished(j) {
      if (j.kind !== 'scan') return;
      document.getElementById('scan-progress-wrap').style.display = 'none';
      document.querySelectorAll('[id^="step-"]').forEach(el => { el.style.background = '#f1efea'; el.style.color = '#898781'; });
      if (j.status === 'done') setTimeout(() => location.reload(), 1200);
    }
    """)

    return (
        card("Scan Pipeline", pipeline, "📡")
        + card("Run a Scan", form, "▶️")
        + card("52-Week High / Low Screen", form_52w, "🎯")
        + card("Earnings Today Screen", form_earn, "📅")
        + card("Recent Scan Files", csv_html, "🗂")
    ), extra_js


# ─────────────────────────────────────────────────────────────────────────────
# RESEARCH LIBRARY
# ─────────────────────────────────────────────────────────────────────────────

def research_page() -> tuple[str, str]:
    idx = _read_json(OUTPUT_DIR / "research_index.json") or {}
    # flat view — watchlists.json nests AI sublists on disk
    from stockanalysis.reporting.research import load_watchlists as _load_wl
    watchlists = _load_wl()
    # Read through core.research_snapshot so a ticker whose index entry was
    # overwritten by a process running older code still shows its last known
    # values instead of a row of dashes. Live index fields always win; the
    # snapshot only fills what the index no longer carries.
    from stockanalysis.core import research_snapshot
    try:
        rows = research_snapshot.merged(idx, research_snapshot.load(OUTPUT_DIR))
    except Exception as e:
        print(f"[Research page] snapshot unavailable ({e})")
        rows = list(idx.values())
    rows = sorted(rows, key=lambda r: r.get("ticker") or "")
    # market_cap joined the curated index fields later than most — entries
    # written before then only carry it inside "raw", so backfill from there.
    # The three company scores are computed here at render time
    # (core.company_scores, pure functions over raw scan fields) rather than
    # stored in the index, so every entry has them regardless of when its
    # page was last generated.
    from stockanalysis.core.company_scores import (
        compute_business_quality, compute_economic_moat, compute_financial_health)
    from stockanalysis.core.buy_zone import compute_buy_zone
    from stockanalysis.core.strategy_scores import day_card_rank, swing_card_rank
    for r in rows:
        raw = r.get("raw") or {}
        if r.get("market_cap") is None:
            r["market_cap"] = raw.get("MarketCap")
        if r.get("eps_growth") is None:
            r["eps_growth"] = raw.get("EPS_Growth%")
        for field, raw_key in (("swing_score", "Swing_Score"),
                               ("daytrade_score", "DayTrade_Score"),
                               ("call_score", "Call_Score"),
                               ("put_score", "Put_Score")):
            if r.get(field) is None:
                r[field] = raw.get(raw_key)
        # Dashboard card-rank scores (the Top-5 cards' "Score 113" badge
        # numbers), same formulas via core.strategy_scores; -999 (entry gate
        # failed / Avoid) renders as not-rankable
        sw, dy = swing_card_rank(raw), day_card_rank(raw)
        r["swing_rank"] = sw if sw > -999 else None
        r["day_rank"] = dy if dy > -999 else None
        bq = compute_business_quality(raw)
        moat = compute_economic_moat(raw)
        fh = compute_financial_health(raw)
        r["business_quality_score"] = bq["score"]
        r["business_quality_label"] = bq["label"]
        r["economic_moat_passed"] = moat["passed"]
        r["economic_moat_total"] = moat["total"]
        r["economic_moat_label"] = moat["label"]
        r["financial_health_score"] = fh["score"]
        r["financial_health_label"] = fh["label"]
        bz = compute_buy_zone(raw)
        r["buy_zone_score"] = bz["score"]
        r["buy_zone_label"] = bz["label"]
    # Competitive standing — must run after the market_cap backfill above, and
    # over the whole row set at once (it's a cross-ticker ranking, not a
    # per-row score like the ones in the loop).
    from stockanalysis.core.market_position import attach_peer_positions
    attach_peer_positions(rows)
    # A ticker no scan ever got a quote for is shown as "no data", not as the
    # Avoid/AVOID/1-star the entry gate stamps on a husk row — see
    # research_snapshot.has_quote(). Marked here rather than in the renderer
    # so the flag also drives the filter counts below.
    for r in rows:
        r["no_data"] = not research_snapshot.has_quote(r)
        if r["no_data"]:
            # Zeroed scores and an "Avoid" grade on a husk row are artifacts
            # of the entry gate rejecting a missing price, not findings.
            # Blanking them here (rather than only in the cell renderers)
            # keeps sorting and the Action filter honest too — otherwise
            # sorting by Conviction would rank never-fetched tickers as
            # genuinely worst-in-library.
            for k in ("category", "grade", "conv_overall", "conv_stars",
                      "conv_action", "investment_score", "swing_score",
                      "daytrade_score", "call_score", "put_score",
                      "swing_rank", "day_rank", "buy_zone_score",
                      "buy_zone_label"):
                r[k] = None
    no_data_count = sum(1 for r in rows if r["no_data"])

    # ETFs get their own columns: every fundamental the equity table shows
    # is undefined for a fund, so the same row shape would be all dashes.
    from stockanalysis.core import etf_profile
    try:
        etf_profile.attach_profiles(
            rows, etf_profile.load_profiles(OUTPUT_DIR))
    except Exception as e:
        print(f"[Research page] ETF profiles unavailable ({e})")
    # A fund with a profile but no library entry still belongs in the ETF
    # view: everything that tab shows (theme, price, expense, AUM, change,
    # holdings) comes from the profile, not the equity scan. Requiring a scan
    # first would hide a newly added ETF from the one view built for it.
    try:
        profiles = etf_profile.load_profiles(OUTPUT_DIR)
        known = {r.get("ticker") for r in rows}
        for ticker, p in sorted(profiles.items()):
            if ticker in known or p.get("error"):
                continue
            rows.append({"ticker": ticker, "sector": "ETF",
                         "no_data": False, "not_scanned": True,
                         "price": p.get("price"), "raw": {}})
        etf_profile.attach_profiles(rows, profiles)
    except Exception as e:
        print(f"[Research page] ETF profile-only rows skipped ({e})")

    etf_count = sum(1 for r in rows if etf_profile.is_etf_row(r))
    from stockanalysis.webapp.api import load_etf_allocations
    try:
        etf_alloc_json = json.dumps(load_etf_allocations())
    except Exception:
        etf_alloc_json = "{}"
    # "Held" = on the user's ETF watchlist. Benchmarks were given profiles
    # but deliberately not added to that list, which is what separates a
    # reference index from a position — and lets IWM/VTI be both.
    try:
        etf_held = set(watchlists.get("Sector: ETF") or [])
    except Exception:
        etf_held = set()
    # Leadership read over the benchmark ETFs — which part of the market is
    # carrying it, which the single regime score can't express.
    from stockanalysis.core.market_regime import benchmark_leadership
    try:
        leadership_json = json.dumps(
            benchmark_leadership(etf_profile.load_profiles(OUTPUT_DIR)))
    except Exception as e:
        print(f"[Research page] leadership unavailable ({e})")
        leadership_json = "null"
    for r in rows:
        r["is_etf"] = etf_profile.is_etf_row(r)
        r["is_benchmark"] = etf_profile.is_benchmark(r.get("ticker"))
        r["is_holding"] = r.get("ticker") in etf_held
    rows_json = json.dumps(rows)
    from stockanalysis.reporting.research import SUBLIST_SEP as _SEP
    watch_names = sorted(set(list(watchlists.keys()) or []) |
                        {"AI", "Dividend", "Swing", "Breakout", "Earnings"})
    # Sublists render inside an <optgroup> for their parent so "AI: Power"
    # sits under AI instead of alphabetically among unrelated lists. The
    # option value stays the full name — the filter matches on that.
    _parents = [w for w in watch_names if _SEP not in w]
    _children: dict[str, list[str]] = {p: [] for p in _parents}
    _loose: list[str] = []
    for w in watch_names:
        if _SEP not in w:
            continue
        parent = w.split(_SEP, 1)[0]
        (_children[parent] if parent in _children else _loose).append(w)

    def _opt(value: str, label: str) -> str:
        return f'<option value="{esc(value)}">{esc(label)}</option>'

    watch_opts = ""
    for p in _parents:
        watch_opts += _opt(p, p)
        if _children.get(p):
            watch_opts += f'<optgroup label="{esc(p)} sublists">' + "".join(
                _opt(c, "└ " + c.split(_SEP, 1)[1]) for c in _children[p]
            ) + "</optgroup>"
    watch_opts += "".join(_opt(c, c) for c in _loose)

    if not rows:
        body = card("", empty(
            "No research pages yet. Use “+ Refresh Research” above "
            "or run a scan to populate the library."), pad="40px")
        return body, ""

    # Same preset list the Screener offers, built from the one registry in
    # core.screener so the two pages can't drift apart.
    from stockanalysis.core.screener import PRESETS as _PRESETS
    preset_opts = "".join(
        f'<option value="{esc(p["key"])}">{esc(p["icon"] + " " + p["name"])}'
        f'</option>' for p in _PRESETS)

    # Named, not hidden: these tickers look like ordinary rows of dashes
    # otherwise, and the natural reading of a dash is "bad" rather than
    # "never fetched".
    no_data_note = (
        f'<div style="font-size:11px;padding:9px 13px;border-radius:9px;'
        f'margin-bottom:12px;background:#FAEEDA;color:#633806;'
        f'border:0.5px solid #f0dfc0">⚠ <b>{no_data_count}</b> of {len(rows)} '
        f'tickers have no quote from any scan — the data source returns '
        f'nothing for these symbols (usually delisted, acquired or renamed). '
        f'They are shown as “no data” rather than rated, and are excluded '
        f'from the Screener. Use the <b>No data</b> tab to review or unstar '
        f'them.</div>') if no_data_count else ""

    controls = f"""
    {no_data_note}
    <dialog id="etf-modal">
      <div class="modal-body" style="min-width:440px;max-width:560px">
        <h3 id="etf-modal-title"></h3>
        <div id="etf-modal-body"></div>
        <div class="modal-actions">
          <button class="btn secondary" onclick="closeModal('etf-modal')">Close</button>
        </div>
      </div>
    </dialog>
    <div id="action-tabs" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px"></div>
    <div style="display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap;margin-bottom:14px">
      <input id="rsearch" placeholder="Filter by ticker or sector…" style="min-width:220px"
             oninput="renderResearch()">
      <select id="rview" onchange="renderResearch()">
        <option value="table">Table view</option>
        <option value="grouped">Grouped by sector</option>
      </select>
      <span style="display:inline-flex;align-items:center;gap:6px">
        <select id="rpreset" onchange="applyResearchPreset(this.value)"
                title="Filter this table by one of the Screener's preset screens">
          <option value="">Screen: none</option>
          {preset_opts}
        </select>
        <span id="rpreset-info" style="font-size:11px;color:#898781"></span>
      </span>
      <form style="display:contents" onsubmit="submitJob(event, this, null); return false;">
        <input type="hidden" name="action" value="research_session">
        <input type="hidden" name="session" value="premarket">
        <button class="btn secondary" style="font-size:11px"
                title="Refresh every research page in the library against pre-open quotes">☀ Pre-market scan</button>
      </form>
      <form style="display:contents" onsubmit="submitJob(event, this, null); return false;">
        <input type="hidden" name="action" value="research_session">
        <input type="hidden" name="session" value="postmarket">
        <button class="btn secondary" style="font-size:11px"
                title="Refresh every research page in the library against post-close quotes">🌙 Post-market scan</button>
      </form>
      <button type="button" class="btn secondary" style="font-size:11px"
              title="Download the rows and columns currently shown, in the same column order"
              onclick="downloadResearchCsv('visible')">⬇ CSV</button>
      <button type="button" class="btn secondary" style="font-size:11px"
              title="Download every research column — visible ones first in table order, then the rest"
              onclick="downloadResearchCsv('all')">⬇ CSV · all columns</button>
      <div id="colpicker-wrap" style="position:relative">
        <button type="button" class="btn secondary" onclick="toggleColPicker()"
                style="font-size:11px">Columns (<span id="colcount">…</span>) ▾</button>
        <div id="colpicker" style="display:none;position:absolute;top:34px;left:0;z-index:50;
             background:#fff;border:1px solid #d9d7ce;border-radius:8px;
             box-shadow:0 4px 16px rgba(0,0,0,.12);padding:10px;width:280px;
             max-height:420px;overflow-y:auto">
          <input id="colsearch" placeholder="Find column…" style="width:100%;margin-bottom:8px;font-size:12px"
                 oninput="renderColPicker()">
          <button type="button" class="btn secondary" style="font-size:10px;padding:3px 8px;margin-bottom:4px"
                  onclick="resetCols()">Reset to default</button>
          <button type="button" class="btn secondary" style="font-size:10px;padding:3px 8px;margin-bottom:4px"
                  onclick="resetColWidths()">Reset widths</button>
          <div id="colpicker-list"></div>
        </div>
      </div>
      <div>
        <div style="display:flex;gap:6px;align-items:center">
          <select id="rwatch" multiple size="6" style="min-width:180px" onchange="renderResearch()">{watch_opts}</select>
          <button type="button" class="btn secondary" style="font-size:10px;padding:3px 8px"
                  onclick="document.getElementById('rwatch').selectedIndex=-1; renderResearch()">Clear</button>
        </div>
        <div style="font-size:10px;color:#898781;margin-top:3px">⌘/Ctrl-click to view several categories together (none = all tickers)</div>
      </div>
      <span style="font-size:11px;color:#898781;margin-left:auto">
        <span id="rcount">{len(rows)}</span> tickers</span>
    </div>
    <div style="font-size:10px;color:#898781;margin-bottom:4px">Drag a column header to reorder it · drag its right edge to resize</div>
    <div id="research-root" style="overflow-x:auto"></div>
    <dialog id="modal-detail" style="max-width:640px;width:90vw">
      <div class="modal-body">
        <h3 id="detail-title">Detailed Metrics</h3>
        <div id="detail-body" style="max-height:60vh;overflow-y:auto"></div>
        <div class="modal-actions">
          <button type="button" class="btn secondary" onclick="closeModal('modal-detail')">Close</button>
        </div>
      </div>
    </dialog>
    <dialog id="modal-ta" style="max-width:680px;width:90vw">
      <div class="modal-body">
        <h3 id="ta-title">AI Technical Analysis</h3>
        <div id="ta-body" style="max-height:65vh;overflow-y:auto"></div>
        <div class="modal-actions">
          <button type="button" class="btn secondary" onclick="closeModal('modal-ta')">Close</button>
        </div>
      </div>
    </dialog>
    <dialog id="modal-earnings" style="max-width:640px;width:90vw">
      <div class="modal-body">
        <h3 id="earnings-title">Earnings Analysis</h3>
        <div id="earnings-body" style="max-height:65vh;overflow-y:auto"></div>
        <div class="modal-actions">
          <button type="button" class="btn secondary" onclick="closeModal('modal-earnings')">Close</button>
        </div>
      </div>
    </dialog>
    """

    js = f"""
    const RESEARCH_ROWS = {rows_json};
    // Seeded from data/etf_allocations.json so the book you entered is still
    // there after a reload.
    const ETF_ALLOC = {etf_alloc_json};
    const ETF_LEADERSHIP = {leadership_json};
    const WATCHLISTS = {json.dumps(watchlists)};
    let sortKey = 'ticker', sortDir = 1;
    let actionFilter = '';

    // ── Configurable columns ─────────────────────────────────────────────
    // "Standard" = the curated table columns; "Detailed metrics" = every raw
    // scan field (same data the Detailed Metrics modal shows). The user's
    // selection AND column order persist in localStorage: newly-checked
    // columns append at the end, and the rendered header's drag-and-drop
    // (see onColDragStart/onColDrop below) lets the user reorder from there.
    const fmtMoney = v => v != null ? '$' + v.toFixed(2) : '—';
    const fmtScore = v => v == null ? '—'
      : `<span style="font-weight:600;color:${{v >= 70 ? '#0F6E56' : v >= 45 ? '#8a6d1a' : '#898781'}}">${{v}}</span>`;
    // Order = decision flow: quality (is it a good company?) → setup/verdict
    // (is it a good trade?) → timing/plan (is now the moment?). The first 16
    // are DEFAULT_COLS; the rest are picker-only level detail.
    const CURATED_COLS = [
      ['price', 'Price', r => fmtMoney(r.price)],
      ['market_cap', 'Mkt Cap', r => fmtCap(r.market_cap)],
      ['business_quality_score', 'Quality', r => r.business_quality_score != null
        ? `<span style="color:${{r.business_quality_label === 'Strong' ? '#0F6E56' : r.business_quality_label === 'Moderate' ? '#8a6d1a' : '#A32D2D'}};font-weight:600">${{r.business_quality_label}} ${{r.business_quality_score}}</span>`
        : '—'],
      ['economic_moat_label', 'Moat', r => r.economic_moat_label != null
        ? `<span style="color:${{r.economic_moat_label === 'Strong signals' ? '#0F6E56' : r.economic_moat_label === 'Moderate signals' ? '#8a6d1a' : '#A32D2D'}};font-weight:600">${{r.economic_moat_label.replace(' signals', '')}} ${{r.economic_moat_passed}}/${{r.economic_moat_total}}</span>`
        : '—'],
      // Competitive standing. A curated data/market_structure.json entry wins
      // over the computed rank and is marked with a dot, so a real structural
      // fact is never confused with "biggest by market cap among names you
      // track". Tooltip always spells out what the number actually measures.
      ['market_position', 'Position', r => {{
        if (r.structure) {{
          const tip = (r.structure_note || 'from data/market_structure.json')
            .replace(/"/g, '&quot;');
          return `<span title="${{tip}}" style="color:#0F6E56;font-weight:600">● ${{r.structure}}</span>`;
        }}
        if (r.peer_rank == null) return '—';
        const color = r.position_tier === 'dominant' ? '#0F6E56'
                    : r.position_tier === 'duopoly' ? '#0C447C'
                    : r.position_tier === 'top2' ? '#8a6d1a' : '#898781';
        const scope = r.peer_group_is_sector ? 'sector' : 'industry';
        const tip = `#${{r.peer_rank}} of ${{r.peer_count}} tracked ${{scope}} peers `
                  + `by market cap (${{r.peer_share_pct}}% of peer cap) — `
                  + `${{r.peer_group}}. Size proxy, not market share.`;
        const weight = r.position_tier === 'rest' ? '400' : '600';
        return `<span title="${{tip.replace(/"/g, '&quot;')}}" `
             + `style="color:${{color}};font-weight:${{weight}}">${{r.position_label}}`
             + `<span style="color:#898781;font-weight:400"> /${{r.peer_count}}</span></span>`;
      }}],
      ['financial_health_score', 'Health', r => r.financial_health_score != null
        ? `<span style="color:${{r.financial_health_label === 'Strong' ? '#0F6E56' : r.financial_health_label === 'Moderate' ? '#8a6d1a' : '#A32D2D'}};font-weight:600">${{r.financial_health_label}} ${{r.financial_health_score}}</span>`
        : '—'],
      ['buy_zone_score', 'Buy Zone', r => r.buy_zone_score != null
        ? `<span style="color:${{r.buy_zone_score >= 80 ? '#0F6E56' : r.buy_zone_score >= 60 ? '#8a6d1a' : '#A32D2D'}};font-weight:600">${{r.buy_zone_label}} ${{r.buy_zone_score}}</span>`
        : '—'],
      ['inst_own_pct', 'Inst Own%', r => (r.inst_own_pct != null ? r.inst_own_pct + '%' : '—') + instOwnChgHtml(r.inst_own_chg)],
      ['eps_growth', 'EPS Gr%', r => r.eps_growth != null
        ? `<span style="color:${{r.eps_growth > 0 ? '#0F6E56' : '#A32D2D'}}">${{r.eps_growth > 0 ? '+' : ''}}${{r.eps_growth}}%</span>` : '—'],
      ['forward_pe', 'Fwd P/E', r => r.forward_pe ?? '—'],
      ['category', 'Category', r => r.no_data
        ? '<span style="color:#898781">no data</span>' : (r.category || '—')],
      // A husk row (failed fetch) carries Avoid/AVOID/1★ from the entry
      // gate, not from analysis. Showing that verdict would assert we looked
      // at the company and rejected it, so these render as unknown instead.
      ['conv_action', 'Action', r => r.no_data
        ? '<span style="color:#898781" title="No scan ever obtained a quote for this ticker — it is not rated">no data</span>'
        : `<span style="color:${{actionColor(r.conv_action)}};font-weight:600">${{r.conv_action || '—'}}</span>`],
      ['conv_stars', 'Conv ★', r => (r.conv_stars != null && !r.no_data)
        ? `<span style="color:#c9a227;letter-spacing:1px">${{'★'.repeat(r.conv_stars)}}<span style="color:#d9d7ce">${{'☆'.repeat(Math.max(0, 5 - r.conv_stars))}}</span></span>` : '—'],
      ['swing_score', 'Swing', r => fmtScore(r.swing_score)],
      ['daytrade_score', 'Day', r => fmtScore(r.daytrade_score)],
      ['swing_rank', 'Swing Rank', r => r.swing_rank != null ? `<b>${{r.swing_rank}}</b>` : '—'],
      ['day_rank', 'Day Rank', r => r.day_rank != null ? `<b>${{r.day_rank}}</b>` : '—'],
      ['call_score', 'Call', r => fmtScore(r.call_score)],
      ['put_score', 'Put', r => fmtScore(r.put_score)],
      ['rs_rank', 'RS', r => r.rs_rank ?? '—'],
      ['canslim_pass', 'CANSLIM', r => r.canslim_pass === true ? '✓' : r.canslim_pass === false ? '✗' : '—'],
      ['entry_zone', 'Entry Zone', r => fmtMoney(r.entry_zone)],
      ['rr_to_resistance', 'R:R', r => r.rr_to_resistance ?? '—'],
      ['breakout_probability', 'Breakout%', r => r.breakout_probability != null ? r.breakout_probability + '%' : '—'],
      ['days_to_earnings', 'DOE', r => r.days_to_earnings ?? '—'],
      ['updated_at', 'Updated', r => `<span style="color:#898781">${{(r.updated_at || '').slice(5,16)}}</span>`],
      // ── picker-only from here down ──
      ['week52_low', '52W Low', r => fmtMoney(r.week52_low)],
      ['dist_from_52w_low_pct', 'Dist 52W Low', r => r.dist_from_52w_low_pct != null ? r.dist_from_52w_low_pct + '%' : '—'],
      ['week52_high', '52W High', r => fmtMoney(r.week52_high)],
      ['earnings_date', 'Earnings Date', r => r.earnings_date && r.earnings_date !== 'N/A' ? `<span style="color:#898781">${{r.earnings_date}}</span>` : '—'],
      ['peg_ratio', 'PEG', r => r.peg_ratio ?? '—'],
      ['s1', 'S1', r => fmtMoney(r.s1)],
      ['r1', 'R1', r => fmtMoney(r.r1)],
      ['key_level_score', 'Key Level', r => r.key_level_score ?? '—'],
      ['touches', 'Touches', r => r.touches ?? '—'],
      ['volume_confirmation', 'Vol Conf', r => r.volume_confirmation === true ? '✓' : r.volume_confirmation === false ? '✗' : '—'],
      ['dist_to_support_pct', 'Dist S1', r => r.dist_to_support_pct != null ? r.dist_to_support_pct + '%' : '—'],
      ['dist_to_resistance_pct', 'Dist R1', r => r.dist_to_resistance_pct != null ? r.dist_to_resistance_pct + '%' : '—'],
      ['bounce_probability', 'Bounce%', r => r.bounce_probability != null ? r.bounce_probability + '%' : '—'],
    ].map(([key, label, cell]) => ({{key, label, cell, group: 'Standard'}}));

    // Raw fields already represented by a Standard column stay out of the
    // picker's Detailed list — one name per fact, no duplicates.
    const RAW_SKIP = new Set(['Ticker', 'BusinessSummary',
      'Current Price', 'MarketCap', 'EPS_Growth%', 'Forward_PE', 'PEG_Ratio',
      'Inst_Own%', 'Inst_Own_Chg', 'Category', 'Conv_Action', 'Conv_Stars',
      'Swing_Score', 'DayTrade_Score', 'Call_Score', 'Put_Score',
      'Buy_Zone_Score', 'Buy_Zone_Label',
      'RS_Rank', 'CANSLIM_Pass', 'RR_to_Resistance', 'Breakout_Probability',
      'Bounce_Probability', 'Days_To_Earnings', 'EarningsDate',
      '52W High', '52W Low', 'S1', 'R1', 'Key_Level_Score', 'Touches',
      'Volume_Confirmation', 'Dist_to_Support%', 'Dist_to_Resistance%',
      'Pct_From_52W_Low%']);
    const RAW_KEYS = [...new Set(RESEARCH_ROWS.flatMap(r => Object.keys(r.raw || {{}})))]
      .filter(k => !RAW_SKIP.has(k)).sort();
    const RAW_COLS = RAW_KEYS.map(k => ({{
      key: 'raw:' + k, label: k, group: 'Detailed metrics',
      cell: r => `<span style="max-width:180px;display:inline-block;overflow:hidden;text-overflow:ellipsis;vertical-align:bottom">${{fmtDetailVal((r.raw || {{}})[k])}}</span>`,
    }}));
    const ALL_COLS = [...CURATED_COLS, ...RAW_COLS];
    const COL_BY_KEY = Object.fromEntries(ALL_COLS.map(c => [c.key, c]));
    const DEFAULT_COLS = ['price', 'market_cap', 'business_quality_score',
      'economic_moat_label', 'market_position', 'financial_health_score',
      'buy_zone_score', 'inst_own_pct',
      'eps_growth', 'forward_pe', 'category', 'conv_action', 'conv_stars',
      'swing_score', 'daytrade_score', 'call_score', 'put_score',
      'rs_rank', 'canslim_pass', 'entry_zone', 'rr_to_resistance',
      'breakout_probability', 'days_to_earnings', 'updated_at'];
    const COLS_LS_KEY = 'research_visible_cols_v1';
    // Columns added after this key was first written. A saved selection wins
    // over DEFAULT_COLS, so without this a newly-shipped default column would
    // stay invisible to anyone who has ever opened this page. Appending is
    // gentler than bumping the key, which would discard the user's layout.
    const COLS_ADDED_SINCE_V1 = ['market_position'];
    let visibleCols = (() => {{
      try {{
        const saved = JSON.parse(localStorage.getItem(COLS_LS_KEY) || 'null');
        if (Array.isArray(saved) && saved.length) {{
          const cols = saved.filter(k => COL_BY_KEY[k]);
          for (const k of COLS_ADDED_SINCE_V1) {{
            if (COL_BY_KEY[k] && !cols.includes(k)) cols.push(k);
          }}
          return cols;
        }}
      }} catch (e) {{}}
      return [...DEFAULT_COLS];
    }})();
    function saveCols() {{ localStorage.setItem(COLS_LS_KEY, JSON.stringify(visibleCols)); }}
    function toggleCol(key) {{
      // newly-checked columns append to the end rather than re-sorting into
      // canonical order — that would silently undo any drag-to-reorder the
      // user has already done to visibleCols.
      if (visibleCols.includes(key)) visibleCols = visibleCols.filter(k => k !== key);
      else visibleCols = [...visibleCols, key];
      saveCols(); renderColPicker(); renderResearch();
    }}
    function resetCols() {{
      visibleCols = [...DEFAULT_COLS];
      saveCols(); renderColPicker(); renderResearch();
    }}
    // ── Drag-to-reorder column headers ───────────────────────────────────
    let _dragColKey = null;
    function onColDragStart(e, key) {{
      _dragColKey = key;
      e.dataTransfer.effectAllowed = 'move';
      e.target.style.opacity = '0.4';
    }}
    function onColDragEnd(e) {{ e.target.style.opacity = '1'; }}
    function onColDragOver(e) {{ e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }}
    function onColDragEnter(e) {{ if (_dragColKey) e.currentTarget.style.borderLeft = '2px solid #0F6E56'; }}
    function onColDragLeave(e) {{ e.currentTarget.style.borderLeft = ''; }}
    function onColDrop(e, targetKey) {{
      e.preventDefault();
      e.currentTarget.style.borderLeft = '';
      const dragged = _dragColKey;
      _dragColKey = null;
      if (!dragged || dragged === targetKey) return;
      const from = visibleCols.indexOf(dragged);
      const to = visibleCols.indexOf(targetKey);
      if (from === -1 || to === -1) return;
      visibleCols.splice(from, 1);
      visibleCols.splice(to, 0, dragged);
      saveCols(); renderResearch();
    }}
    // ── Drag-to-resize column widths ─────────────────────────────────────
    // Widths persist per column key in localStorage, same pattern as
    // visibleCols/colOrder above. Columns without a saved width fall back
    // to a size guessed from the label so the table looks reasonable
    // before the user has resized anything.
    const COLWIDTHS_LS_KEY = 'research_col_widths_v1';
    let colWidths = (() => {{
      try {{
        const saved = JSON.parse(localStorage.getItem(COLWIDTHS_LS_KEY) || 'null');
        if (saved && typeof saved === 'object') return saved;
      }} catch (e) {{}}
      return {{}};
    }})();
    function saveColWidths() {{ localStorage.setItem(COLWIDTHS_LS_KEY, JSON.stringify(colWidths)); }}
    function defaultColWidth(label) {{ return Math.max(64, label.length * 7 + 28); }}
    function colW(key, label) {{ return colWidths[key] || defaultColWidth(label); }}
    function resetColWidths() {{
      colWidths = {{}};
      saveColWidths(); renderResearch();
    }}
    let _resizeKey = null, _resizeStartX = 0, _resizeStartW = 0;
    function onColResizeStart(e, key) {{
      e.preventDefault(); e.stopPropagation();
      _resizeKey = key;
      _resizeStartX = e.clientX;
      _resizeStartW = colW(key, key === 'ticker' ? 'Ticker' : (COL_BY_KEY[key] || {{}}).label || '');
      e.currentTarget.classList.add('active');
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', onColResizeMove);
      document.addEventListener('mouseup', onColResizeEnd);
    }}
    function onColResizeMove(e) {{
      if (!_resizeKey) return;
      const w = Math.max(44, _resizeStartW + (e.clientX - _resizeStartX));
      colWidths[_resizeKey] = w;
      document.querySelectorAll(`[data-colw="${{_resizeKey}}"]`).forEach(el => {{
        el.style.width = w + 'px';
        el.style.maxWidth = w + 'px';
      }});
    }}
    function onColResizeEnd() {{
      if (_resizeKey) saveColWidths();
      _resizeKey = null;
      document.body.style.userSelect = '';
      document.querySelectorAll('.col-resizer.active').forEach(el => el.classList.remove('active'));
      document.removeEventListener('mousemove', onColResizeMove);
      document.removeEventListener('mouseup', onColResizeEnd);
    }}
    function toggleColPicker() {{
      const p = document.getElementById('colpicker');
      p.style.display = p.style.display === 'none' ? 'block' : 'none';
      if (p.style.display === 'block') renderColPicker();
    }}
    function renderColPicker() {{
      const q = (document.getElementById('colsearch').value || '').toUpperCase();
      document.getElementById('colpicker-list').innerHTML = ['Standard', 'Detailed metrics'].map(g => {{
        const items = ALL_COLS.filter(c => c.group === g && (!q || c.label.toUpperCase().includes(q)));
        if (!items.length) return '';
        return `<div style="font-size:10px;font-weight:700;color:#898781;margin:8px 0 4px;text-transform:uppercase">${{g}}</div>`
          + items.map(c => `<label style="display:flex;gap:6px;align-items:center;font-size:12px;padding:2px 0;cursor:pointer">
              <input type="checkbox" ${{visibleCols.includes(c.key) ? 'checked' : ''}} onchange="toggleCol('${{c.key}}')">${{c.label}}</label>`).join('');
      }}).join('');
      document.getElementById('colcount').textContent = visibleCols.length;
    }}
    document.addEventListener('click', e => {{
      const p = document.getElementById('colpicker');
      if (p && p.style.display === 'block' && !e.target.closest('#colpicker-wrap')) p.style.display = 'none';
    }});
    function colVal(r, key) {{
      return key.startsWith('raw:') ? (r.raw || {{}})[key.slice(4)] : r[key];
    }}
    function starredIn(name, ticker) {{ return (WATCHLISTS[name] || []).includes(ticker); }}
    function actionColor(a) {{ return a === 'READY' ? '#0F6E56' : a === 'WATCH' ? '#8a6d1a' : a === 'AVOID' ? '#A32D2D' : '#898781'; }}
    function setActionFilter(a) {{ actionFilter = a; renderResearch(); }}
    function renderActionTabs() {{
      const tabs = [['', 'All'], ['READY', 'Ready'], ['WATCH', 'Watch'], ['AVOID', 'Avoid'],
                    ['ETF', 'ETFs'], ['NODATA', 'No data']];
      document.getElementById('action-tabs').innerHTML = tabs.map(([val, label]) => {{
        const count = val === 'NODATA' ? RESEARCH_ROWS.filter(r => r.no_data).length
          : val === 'ETF' ? RESEARCH_ROWS.filter(r => r.is_etf).length
          : val ? RESEARCH_ROWS.filter(r => r.conv_action === val).length
          : RESEARCH_ROWS.length;
        if ((val === 'NODATA' || val === 'ETF') && !count) return '';
        const active = actionFilter === val;
        const color = val ? actionColor(val) : '#52514e';
        return `<button onclick="setActionFilter('${{val}}')"
          style="font-size:11px;font-weight:600;padding:4px 12px;border-radius:14px;cursor:pointer;
                 border:1px solid ${{color}};background:${{active ? color : 'transparent'}};
                 color:${{active ? '#fff' : color}}">${{label}} (${{count}})</button>`;
      }}).join('');
    }}
    function fmtCap(v) {{
      if (v == null) return '—';
      if (v >= 1e12) return '$' + (v / 1e12).toFixed(2) + 'T';
      if (v >= 1e9)  return '$' + (v / 1e9).toFixed(1) + 'B';
      if (v >= 1e6)  return '$' + (v / 1e6).toFixed(0) + 'M';
      return '$' + Math.round(v).toLocaleString();
    }}
    function instOwnChgHtml(chg) {{
      if (chg == null) return '';
      const color = chg > 0 ? '#0F6E56' : chg < 0 ? '#A32D2D' : '#898781';
      const arrow = chg > 0 ? '▲' : chg < 0 ? '▼' : '·';
      return ` <span style="color:${{color}};font-size:10px">${{arrow}}${{Math.abs(chg).toFixed(1)}}</span>`;
    }}
    function sortRows(rows) {{
      return [...rows].sort((a, b) => {{
        const av = colVal(a, sortKey) ?? '', bv = colVal(b, sortKey) ?? '';
        return (av > bv ? 1 : av < bv ? -1 : 0) * sortDir;
      }});
    }}
    // Search + watchlist + action-tab filtering, shared by the table render
    // and the CSV export so a download can never drift from what's on screen.
    // ── ETF view ───────────────────────────────────────────────────────────
    function fmtEtfPct(v, nd) {{
      return v == null ? '—' : Number(v).toFixed(nd == null ? 2 : nd) + '%';
    }}
    // Signed, coloured, and carrying the timestamp of the quote it came
    // from: this is the change as of the last ETF refresh, not a live tick,
    // and an unqualified "+1.83%" would imply otherwise.
    function fmtEtfChange(pct, abs, at) {{
      if (pct == null) return '—';
      const c = pct > 0 ? '#0F6E56' : pct < 0 ? '#A32D2D' : '#898781';
      const sign = pct > 0 ? '+' : '';
      const tip = (abs != null ? `${{sign}}${{Number(abs).toFixed(2)}} ` : '') +
                  (at ? `as of ${{at}}` : '');
      return `<span style="color:${{c}};font-weight:600" title="${{tip}}">` +
             `${{sign}}${{Number(pct).toFixed(2)}}%</span>`;
    }}
    // ── theme naming ───────────────────────────────────────────────────────
    // The provider's category is too broad to be useful on a thematic fund —
    // SMH and IGV are both "Technology" — so the label is editable and the
    // user's wording is stored separately from the fetched category, which
    // is what lets it survive every ETF refresh.
    function etfThemeCell(r) {{
      const name = r.etf_theme_name || r.etf_category || '—';
      const custom = r.etf_theme_custom;
      return `<span id="theme-${{r.ticker}}" onclick="editEtfTheme('${{r.ticker}}')"
        title="${{custom ? 'Your label — click to edit (blank resets to “' +
          (r.etf_category || 'provider category') + '”)' : 'Click to name this theme'}}"
        style="cursor:pointer;border-bottom:1px dotted #b9b7ae;${{
          custom ? 'font-weight:600' : 'color:#898781'}}">${{name}}</span>`;
    }}
    function editEtfTheme(ticker) {{
      const r = RESEARCH_ROWS.find(x => x.ticker === ticker);
      if (!r) return;
      const cell = document.getElementById('theme-' + ticker);
      if (!cell || cell.dataset.editing) return;
      cell.dataset.editing = '1';
      const current = r.etf_theme_custom ? (r.etf_theme_name || '') : '';
      cell.outerHTML = `<input id="theme-input-${{ticker}}" value="${{current}}"
        placeholder="${{r.etf_category || 'theme'}}" maxlength="60"
        style="width:130px;font-size:11px;padding:2px 6px"
        onkeydown="if(event.key==='Enter'){{saveEtfTheme('${{ticker}}')}}
                   else if(event.key==='Escape'){{renderResearch()}}"
        onblur="saveEtfTheme('${{ticker}}')">`;
      const input = document.getElementById('theme-input-' + ticker);
      if (input) {{ input.focus(); input.select(); }}
    }}
    let _themeSaving = false;
    async function saveEtfTheme(ticker) {{
      // blur fires again when the input is torn down by the re-render
      if (_themeSaving) return;
      const input = document.getElementById('theme-input-' + ticker);
      if (!input) return;
      _themeSaving = true;
      try {{
        const res = await fetch('/api/etf/theme', {{
          method: 'POST',
          body: new URLSearchParams({{ ticker, theme: input.value }}),
        }});
        const data = await res.json();
        if (data.ok) {{
          const r = RESEARCH_ROWS.find(x => x.ticker === ticker);
          if (r) {{ r.etf_theme_name = data.theme; r.etf_theme_custom = data.custom; }}
          toast(data.message, 'ok');
        }} else {{ toast(data.message || 'Could not save theme', 'err'); }}
      }} catch (e) {{ toast('Could not save theme: ' + e, 'err'); }}
      _themeSaving = false;
      renderResearch();
    }}

    // How much of the fund the published holdings actually account for, and
    // over how many names. The provider publishes ten for most funds but
    // only five for some (DRAM), so a fixed "Top 10" label would assert a
    // completeness the data doesn't have — and understate concentration risk
    // by implying the rest is diversified rather than simply undisclosed.
    function etfDisclosedCell(r) {{
      const w = r.etf_top10_weight, n = r.etf_holdings_count;
      if (w == null || !n) return '—';
      const mix = r.etf_asset_mix || {{}};
      const partial = n < 10;
      let tip = `Top ${{n}} published holdings = ${{w.toFixed(1)}}% of the fund; ` +
                `${{(100 - w).toFixed(1)}}% is not disclosed here.`;
      if (partial) tip += ` The provider publishes only ${{n}} holdings for this fund.`;
      if (mix.other) tip += ` ${{mix.other}}% of assets sit in "other" ` +
        `(often swap exposure, which never appears in a holdings list).`;
      return `<span title="${{tip}}" style="${{partial ? 'color:#8a6d1a;font-weight:600' : ''}}">
        ${{w.toFixed(1)}}% <span style="font-size:9px;color:#898781">/${{n}}${{
          partial ? ' ⚠' : ''}}</span></span>`;
    }}

    // Spells out what the list does and doesn't cover, so a name being
    // absent is read as "not published" rather than "not held".
    function etfCoverageNote(r) {{
      const w = r.etf_top10_weight, n = r.etf_holdings_count || 0;
      if (!n) return '';
      const mix = r.etf_asset_mix || {{}};
      const rest = w == null ? null : (100 - w);
      let msg = `These are the <b>${{n}}</b> holdings the fund's data provider ` +
        `publishes` + (w != null ? `, together <b>${{w.toFixed(1)}}%</b> of the fund` : '') +
        `. ` + (rest != null ? `The remaining <b>${{rest.toFixed(1)}}%</b> is not
        disclosed here, so a company absent from this list may still be held. ` : '');
      if (n < 10) msg += `Note this provider publishes only ${{n}} holdings for
        this fund, fewer than the usual ten. `;
      if (mix.other) msg += `<b>${{mix.other}}%</b> of assets are classified
        "other" — typically swap or derivative exposure, which never shows up
        as a holding even when the underlying company is part of the strategy. `;
      if (mix.stock != null) msg += `Asset mix: ${{mix.stock}}% stock` +
        (mix.cash ? `, ${{mix.cash}}% cash` : '') +
        (mix.other ? `, ${{mix.other}}% other` : '') + '.';
      return `<div style="margin-top:12px;font-size:11px;line-height:1.55;
        background:#FAEEDA;color:#633806;border-radius:8px;padding:9px 12px">${{msg}}</div>`;
    }}

    // ── ETF column order ───────────────────────────────────────────────────
    // Its own order list — the equity table's visibleCols is a different set
    // of columns entirely. Widths are shared (colWidths, keyed by column
    // key) because the keys don't overlap and one resize store is simpler
    // than two that behave identically.
    const ETFCOLS_LS_KEY = 'etf_col_order_v1';
    let etfColOrder = (() => {{
      try {{
        const raw = JSON.parse(localStorage.getItem(ETFCOLS_LS_KEY) || '[]');
        return Array.isArray(raw) ? raw.filter(k => typeof k === 'string') : [];
      }} catch (e) {{ return []; }}
    }})();
    function saveEtfColOrder() {{
      try {{ localStorage.setItem(ETFCOLS_LS_KEY, JSON.stringify(etfColOrder)); }}
      catch (e) {{ /* private mode — the order just won't persist */ }}
    }}
    let _dragEtfColKey = null;
    function onEtfColDragStart(e, key) {{
      _dragEtfColKey = key;
      e.dataTransfer.effectAllowed = 'move';
      e.target.style.opacity = '0.4';
    }}
    function onEtfColDrop(e, targetKey) {{
      e.preventDefault();
      e.currentTarget.style.borderLeft = '';
      const dragged = _dragEtfColKey;
      _dragEtfColKey = null;
      if (!dragged || dragged === targetKey) return;
      const from = etfColOrder.indexOf(dragged);
      const to = etfColOrder.indexOf(targetKey);
      if (from === -1 || to === -1) return;
      etfColOrder.splice(from, 1);
      etfColOrder.splice(to, 0, dragged);
      saveEtfColOrder();
      renderResearch();
    }}
    function resetEtfCols() {{
      etfColOrder = [];
      saveEtfColOrder();
      // Clear widths for ETF columns only — the equity table shares this
      // store and its columns must keep whatever the user set there.
      Object.keys(colWidths).forEach(k => {{
        if (k === 'ticker' || k === 'price' || k === '_alloc' || k.startsWith('etf_'))
          delete colWidths[k];
      }});
      saveColWidths();
      renderResearch();
    }}

    // ── ETF sorting ────────────────────────────────────────────────────────
    let etfSortKey = 'ticker', etfSortDir = 1;
    function setEtfSort(key) {{
      // Clicking the active column flips direction; a new column starts
      // descending for numbers (biggest first is what you want from AUM or
      // today's move) and ascending for text.
      if (etfSortKey === key) {{ etfSortDir = -etfSortDir; }}
      else {{ etfSortKey = key; etfSortDir = (key === 'ticker' ||
             key === 'etf_theme_name' || key === 'etf_family') ? 1 : -1; }}
      renderResearch();
    }}
    function sortEtfRows(rows) {{
      const key = etfSortKey;
      return [...rows].sort((a, b) => {{
        const av = a[key], bv = b[key];
        // Missing values sort last under either direction — a fund with no
        // expense ratio isn't the cheapest one.
        const an = av === null || av === undefined, bn = bv === null || bv === undefined;
        if (an && bn) return 0;
        if (an) return 1;
        if (bn) return -1;
        if (typeof av === 'number' && typeof bv === 'number') {{
          return (av - bv) * etfSortDir;
        }}
        return String(av).localeCompare(String(bv)) * etfSortDir;
      }});
    }}

    // ── portfolio look-through ─────────────────────────────────────────────
    // Weights are entered in the table's last column; this bar totals them
    // and runs core.etf_portfolio over the result. The analysis is
    // server-side because the sector blending and the alert thresholds are
    // the kind of thing that must have one implementation, not two.
    function allocTotal() {{
      return Object.values(ETF_ALLOC).reduce((a, b) => a + (Number(b) || 0), 0);
    }}
    function onAllocInput(ticker, value) {{
      const v = parseFloat(value);
      if (!value || isNaN(v) || v <= 0) delete ETF_ALLOC[ticker];
      else ETF_ALLOC[ticker] = v;
      const el = document.getElementById('alloc-total');
      if (el) {{
        const t = allocTotal();
        el.textContent = t.toFixed(1) + '%';
        // Not an error: analyze() normalises, so 95% still works — but the
        // number should be visible rather than silently rescaled.
        el.style.color = Math.abs(t - 100) < 0.05 ? '#0F6E56' : '#8a6d1a';
      }}
    }}
    function etfPortfolioBar() {{
      const t = allocTotal();
      return `<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;
        background:white;border:0.5px solid #e1e0d9;border-radius:10px;
        padding:10px 13px;margin-bottom:12px">
        <b style="font-size:12px">Portfolio look-through</b>
        <span style="font-size:11px;color:#898781">Enter weights in the
          <b>Weight %</b> column, then analyse. Totals are normalised, so 95%
          works.</span>
        <span style="margin-left:auto;font-size:11px;color:#898781">Total
          <b id="alloc-total" style="color:${{Math.abs(t - 100) < 0.05 ?
            '#0F6E56' : '#8a6d1a'}}">${{t.toFixed(1)}}%</b></span>
        <button class="btn" style="font-size:11px" onclick="runEtfPortfolio()">
          Analyse portfolio</button>
        <button class="btn secondary" style="font-size:11px"
          onclick="clearEtfPortfolio()">Clear</button>
        <form style="display:inline" onsubmit="submitJob(event, this, null); return false;">
          <input type="hidden" name="action" value="etf_profiles">
          <button class="btn secondary" style="font-size:11px"
            title="Re-fetch theme, holdings, expense ratio, AUM, price levels and today's change for every ETF">🧺 Refresh ETFs</button>
        </form>
        <button class="btn secondary" style="font-size:11px" onclick="resetEtfCols()"
          title="Restore the default ETF column order and widths">↺ Columns</button>
        </div>
        <div style="font-size:10px;color:#898781;margin:-6px 0 10px 2px">
          Drag a column header to reorder it · drag its right edge to resize ·
          click it to sort</div>`;
    }}
    function clearEtfPortfolio() {{
      for (const k of Object.keys(ETF_ALLOC)) delete ETF_ALLOC[k];
      fetch('/api/etf/portfolio', {{method: 'POST',
        body: JSON.stringify({{allocations: {{}}, save: true}})}});
      renderResearch();
    }}
    async function runEtfPortfolio() {{
      const box = document.getElementById('etf-report');
      if (!Object.keys(ETF_ALLOC).length) {{
        toast('Enter at least one weight first', 'err'); return;
      }}
      box.innerHTML = '<div style="font-size:11px;color:#898781;padding:8px">Analysing…</div>';
      let d;
      try {{
        const res = await fetch('/api/etf/portfolio', {{
          method: 'POST', body: JSON.stringify({{allocations: ETF_ALLOC, save: true}})}});
        d = await res.json();
      }} catch (e) {{ box.innerHTML = ''; toast('Analysis failed: ' + e, 'err'); return; }}
      if (!d.ok) {{ box.innerHTML = ''; toast(d.message || 'Analysis failed', 'err'); return; }}
      box.innerHTML = renderEtfReport(d);
    }}
    function bar(pct, color) {{
      const w = Math.max(0, Math.min(100, pct));
      return `<span style="display:inline-block;width:150px;height:9px;
        background:#f1efea;border-radius:5px;overflow:hidden;vertical-align:middle">
        <span style="display:block;width:${{w}}%;height:100%;background:${{color}}"></span></span>`;
    }}
    function renderEtfReport(d) {{
      const alertColor = {{red: '#791F1F', amber: '#8a6d1a', green: '#0F6E56'}};
      const alertBg = {{red: '#FCEBEB', amber: '#FAEEDA', green: '#E1F5EE'}};
      const alerts = d.alerts.map(a => `
        <div style="background:${{alertBg[a.level]}};color:${{alertColor[a.level]}};
          border-radius:8px;padding:7px 11px;font-size:11.5px;margin-bottom:5px">
          ${{a.level === 'red' ? '🔴' : a.level === 'amber' ? '🟠' : '🟢'}}
          <b>${{a.text}}</b>${{a.detail ? ` — ${{a.detail}}` : ''}}</div>`).join('');
      const sectors = d.sectors.map(s => `
        <div style="display:flex;gap:9px;align-items:center;font-size:11.5px;margin-bottom:3px">
          <span style="width:150px">${{s.sector}}</span>
          ${{bar(s.pct, s.sector === 'Technology' && s.pct > 30 ? '#A32D2D' : '#185FA5')}}
          <b>${{s.pct.toFixed(1)}}%</b></div>`).join('');
      const roles = d.roles.map(r => `
        <div style="display:flex;gap:9px;align-items:center;font-size:11.5px;margin-bottom:3px">
          <span style="width:110px">${{r.role}}</span>
          ${{bar(r.pct, '#0F6E56')}}
          <b>${{r.pct.toFixed(1)}}%</b>
          <span style="color:#898781;font-size:10px">${{r.tickers.join(' ')}}</span></div>`).join('');
      const stocks = d.stocks.slice(0, 8).map(s => `
        <tr><td><b>${{s.ticker}}</b></td>
          <td style="font-size:11px">${{s.name || ''}}</td>
          <td style="text-align:right;font-weight:600">≥${{s.pct.toFixed(2)}}%</td>
          <td style="font-size:10px;color:#898781">${{s.via.join(', ')}}</td></tr>`).join('');
      // The floor caveat travels with the number, every time it is shown.
      const coverage = `<div style="font-size:11px;color:#633806;background:#FAEEDA;
        border-radius:8px;padding:8px 11px;margin-top:8px">
        Single-company figures are a <b>floor</b>, not a measurement: only
        <b>${{d.coverage.toFixed(1)}}%</b> of this portfolio's holdings are published
        by the fund providers. A company held inside the undisclosed remainder
        adds to the real number — it can never be lower.</div>`;
      const unknown = d.unknown_pct > 0 ? `<div style="font-size:11px;color:#791F1F;
        margin-top:6px">${{d.unknown_pct.toFixed(1)}}% of the portfolio has no sector
        data — use <b>Refresh ETFs</b>.</div>` : '';
      return `<div style="background:white;border:0.5px solid #e1e0d9;border-radius:10px;
        padding:14px 16px;margin-bottom:14px">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
          <div><div style="font-size:11px;font-weight:700;color:#898781;
            text-transform:uppercase;margin-bottom:7px">Sector exposure (exact)</div>
            ${{sectors}}${{unknown}}</div>
          <div><div style="font-size:11px;font-weight:700;color:#898781;
            text-transform:uppercase;margin-bottom:7px">Risk checks</div>${{alerts}}</div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:16px">
          <div><div style="font-size:11px;font-weight:700;color:#898781;
            text-transform:uppercase;margin-bottom:7px">Portfolio role</div>${{roles}}</div>
          <div><div style="font-size:11px;font-weight:700;color:#898781;
            text-transform:uppercase;margin-bottom:7px">Single-company look-through</div>
            <table><tbody>${{stocks}}</tbody></table></div>
        </div>
        ${{coverage}}</div>`;
    }}

    // Level plus the signed distance from it — "$451.59" alone makes you do
    // the arithmetic against price; "+28.4%" is the thing you were after.
    // Green above / red below reads directly for the moving averages; for
    // the 52-week high a negative distance is normal, so it stays neutral.
    function etfLevel(level, dist) {{
      if (level == null) return '—';
      const px = '$' + Number(level).toFixed(2);
      if (dist == null) return px;
      const c = dist > 0 ? '#0F6E56' : dist < 0 ? '#A32D2D' : '#898781';
      return `${{px}}<br><span style="font-size:9.5px;color:${{c}}">${{
        dist > 0 ? '+' : ''}}${{Number(dist).toFixed(1)}}%</span>`;
    }}

    // Benchmarks in the order given, so the reference strip reads
    // large-cap → tech → industrials → small → mid → total-market rather
    // than alphabetically.
    const ETF_BENCH_ORDER = ['SPY', 'VOO', 'QQQ', 'QQQM', 'DIA', 'MDY', 'IWM',
                             'VTI', '^VIX'];
    // VIX rises when the market falls, so "up = green" would paint a fear
    // spike as strength. Its colours are flipped and its distance-from-200
    // is labelled as fear rather than trend.
    const ETF_INVERTED = new Set(['^VIX']);

    // Trend is measured against the 200-day, not today's close — see
    // core/market_regime.benchmark_leadership for why.
    function etfLeadershipBanner() {{
      const L = ETF_LEADERSHIP;
      if (!L) return '';
      const tone = {{good: ['#E1F5EE', '#085041'], watch: ['#FAEEDA', '#633806'],
                     bad: ['#FCEBEB', '#791F1F'], muted: ['#f1efea', '#52514e']}};
      const [bg, fg] = tone[L.tone] || tone.muted;
      const legs = (L.legs || []).map(l =>
        `<span style="margin-right:12px">${{l.ticker}}
          <b style="color:${{l.above_200ma ? '#0F6E56' : '#A32D2D'}}">${{
            l.above_200ma ? '↑' : '↓'}} ${{l.dist_ma200 > 0 ? '+' : ''}}${{
            l.dist_ma200.toFixed(1)}}%</b></span>`).join('');
      const missing = (L.missing || []).length
        ? `<div style="margin-top:4px;font-size:10px">No 200-day data for
           ${{L.missing.join(', ')}} — this read is based on the rest.</div>` : '';
      return `<div style="background:${{bg}};color:${{fg}};border-radius:9px;
        padding:9px 12px;margin-bottom:9px;font-size:11.5px">
        <b>${{L.verdict}}</b> — ${{L.detail}}
        <div style="margin-top:5px;font-size:10.5px;opacity:.85">${{legs}}
          <span style="opacity:.7">vs 200-day</span></div>${{missing}}</div>`;
    }}

    function renderEtfBenchmarks(rows) {{
      if (!rows || !rows.length) return '';
      const cell = r => {{
        const chg = r.etf_change_pct;
        const inv = ETF_INVERTED.has(r.ticker);
        const up = '#0F6E56', down = '#A32D2D';
        const c = chg > 0 ? (inv ? down : up) : chg < 0 ? (inv ? up : down) : '#898781';
        const held = r.is_holding
          ? ' <span style="font-size:9px;color:#8a6d1a" title="Also one of your positions">held</span>' : '';
        return `<div style="flex:0 0 auto;min-width:118px;background:#faf9f5;
          border:0.5px solid #e1e0d9;border-radius:9px;padding:8px 11px">
          <div style="font-size:12px;font-weight:700">${{r.ticker.replace('^','')}}${{held}}</div>
          <div style="font-size:14px;font-weight:600;margin:1px 0">${{fmtMoney(r.price)}}</div>
          <div style="font-size:11px;font-weight:600;color:${{c}}">${{
            chg == null ? '—' : (chg > 0 ? '+' : '') + chg.toFixed(2) + '%'}}</div>
          <div style="font-size:9.5px;color:#898781;margin-top:2px">${{
            inv
              ? (r.etf_dist_ma200 == null ? 'vs200 —'
                 : (r.etf_dist_ma200 < 0 ? 'calm' : 'elevated') + ' · ' +
                   (r.etf_dist_ma200 > 0 ? '+' : '') + r.etf_dist_ma200.toFixed(0) +
                   '% vs 200d')
              : 'YTD ' + (r.etf_ytd_return == null ? '—' : r.etf_ytd_return.toFixed(1) + '%') +
                ' · vs200 ' + (r.etf_dist_ma200 == null ? '—' :
                  (r.etf_dist_ma200 > 0 ? '+' : '') + r.etf_dist_ma200.toFixed(1) + '%')
          }}</div>
        </div>`;
      }};
      return `<div style="margin-bottom:14px">
        <div style="font-size:11px;font-weight:700;color:#898781;
          text-transform:uppercase;letter-spacing:.3px;margin-bottom:7px">
          Benchmarks — reference only, not positions</div>
        ${{etfLeadershipBanner()}}
        <div style="display:flex;gap:8px;flex-wrap:wrap">${{rows.map(cell).join('')}}</div>
      </div>`;
    }}

    function renderEtfTable(rows, root) {{
      if (!rows.length) {{
        root.innerHTML = '<div style="padding:24px;text-align:center;color:#898781;' +
          'font-size:12px">No ETFs match the current filters.</div>';
        return;
      }}
      // Column definitions keyed rather than positional, so drag-to-reorder
      // can emit them in any order. `cell` returns the <td> contents; the
      // width/resize plumbing is shared with the equity table above (keys
      // are distinct, so colWidths can hold both without collision).
      const ETF_COLS = [
        ['ticker', 'Ticker', r => r.not_scanned
          ? `<b title="Profile only — no scan has picked this up yet, so it has no technicals">${{r.ticker}}</b>
             <span style="font-size:9px;color:#8a6d1a"> new</span>`
          : `<a href="/research/${{r.ticker}}.html"><b>${{r.ticker}}</b></a>`],
        ['etf_theme_name', 'Theme', r => etfThemeCell(r)],
        ['etf_family', 'Family', r =>
          `<span style="color:#898781">${{r.etf_family || '—'}}</span>`],
        ['price', 'Price', r => fmtMoney(r.price)],
        ['etf_change_pct', 'Chg today', r =>
          fmtEtfChange(r.etf_change_pct, r.etf_change_abs, r.etf_quote_at)],
        ['etf_expense_ratio', 'Expense', r => fmtEtfPct(r.etf_expense_ratio)],
        ['etf_aum', 'AUM', r => fmtCap(r.etf_aum)],
        ['etf_ytd_return', 'YTD', r =>
          `<span style="color:${{(r.etf_ytd_return ?? 0) >= 0 ? '#0F6E56' : '#A32D2D'}}">${{
            fmtEtfPct(r.etf_ytd_return, 1)}}</span>`],
        ['etf_yield_pct', 'Yield', r => fmtEtfPct(r.etf_yield_pct, 2)],
        ['etf_ema8', '8 EMA', r => etfLevel(r.etf_ema8, r.etf_dist_ema8)],
        ['etf_ma50', '50 MA', r => etfLevel(r.etf_ma50, r.etf_dist_ma50)],
        ['etf_ma200', '200 MA', r => etfLevel(r.etf_ma200, r.etf_dist_ma200)],
        ['etf_week52_high', '52W high', r =>
          etfLevel(r.etf_week52_high, r.etf_dist_52w_high)],
        ['etf_week52_low', '52W low', r =>
          etfLevel(r.etf_week52_low, r.etf_dist_52w_low)],
        ['etf_top10_weight', 'Disclosed', r => etfDisclosedCell(r)],
        ['etf_holdings_count', 'Top holdings', r => {{
          const hs = r.etf_holdings || [];
          // A commodity trust holds bullion, not equities — no holdings is a
          // correct answer for GLD/SLV, not a fetch failure.
          return hs.length
            ? `<button class="btn secondary" style="font-size:10px;padding:3px 9px"
                 onclick="showEtfHoldings('${{r.ticker}}')">${{hs.length}} holdings</button>
               <span style="font-size:10px;color:#898781;margin-left:6px">${{
                 hs.slice(0, 3).map(h => h.ticker + ' ' + fmtEtfPct(h.weight, 1)).join(' · ')}}</span>`
            : '<span style="font-size:10px;color:#898781">physical / not published</span>';
        }}],
        ['_alloc', 'Weight %', r =>
          `<input type="number" min="0" max="100" step="0.5"
             id="alloc-${{r.ticker}}" value="${{ETF_ALLOC[r.ticker] ?? ''}}"
             oninput="onAllocInput('${{r.ticker}}', this.value)"
             style="width:62px;font-size:11px;padding:2px 5px">`],
      ];
      const ETF_COL_BY_KEY = {{}};
      ETF_COLS.forEach(c => {{ ETF_COL_BY_KEY[c[0]] = c; }});
      // Drop keys that no longer exist and append ones added since the order
      // was saved, so a stored order never hides a new column.
      etfColOrder = etfColOrder.filter(k => ETF_COL_BY_KEY[k]);
      ETF_COLS.forEach(c => {{ if (!etfColOrder.includes(c[0])) etfColOrder.push(c[0]); }});

      // Benchmarks render as a reference strip above the holdings table.
      // IWM and VTI stay in BOTH: they are genuinely a holding and an index,
      // and dropping either from the holdings table would hide a real
      // position just because it doubles as a yardstick.
      const benchRows = ETF_BENCH_ORDER
        .map(t => rows.find(r => r.ticker === t)).filter(Boolean);
      rows = rows.filter(r => r.is_holding);

      rows = sortEtfRows(rows);
      const head = etfColOrder.map(key => {{
        const [, label] = ETF_COL_BY_KEY[key];
        const w = colW(key, label);
        const sizing = colWidths[key] != null
          ? `width:${{w}}px;max-width:${{w}}px` : `max-width:${{w}}px`;
        const arrow = etfSortKey === key ? (etfSortDir > 0 ? ' ▲' : ' ▼') : '';
        return `<th draggable="true" style="cursor:grab;white-space:nowrap;position:relative"
          title="Click to sort · drag to reorder · drag right edge to resize"
          onclick="setEtfSort('${{key}}')"
          ondragstart="onEtfColDragStart(event,'${{key}}')" ondragend="onColDragEnd(event)"
          ondragover="onColDragOver(event)" ondragenter="onColDragEnter(event)"
          ondragleave="onColDragLeave(event)" ondrop="onEtfColDrop(event,'${{key}}')"
          ><span style="display:inline-block;${{sizing}};overflow:hidden;
            text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom">${{label}}${{arrow}}</span>
          <span class="col-resizer" draggable="false"
            onmousedown="onColResizeStart(event,'${{key}}')"
            onclick="event.stopPropagation()"></span></th>`;
      }}).join('');
      const body = rows.map(r => '<tr>' + etfColOrder.map(key => {{
        const [, label, cell] = ETF_COL_BY_KEY[key];
        const w = colW(key, label);
        const sizing = colWidths[key] != null
          ? `width:${{w}}px;max-width:${{w}}px` : `max-width:${{w}}px`;
        return `<td><span style="display:inline-block;${{sizing}};overflow:hidden;
          text-overflow:ellipsis;vertical-align:bottom">${{cell(r)}}</span></td>`;
      }}).join('') + '</tr>').join('');
      root.innerHTML = `
        ${{etfPortfolioBar()}}
        <div id="etf-report"></div>
        ${{renderEtfBenchmarks(benchRows)}}
        <div style="font-size:11px;color:#898781;margin-bottom:8px">
          Fund-specific fields — margins, EPS growth and moat don't exist for an
          ETF, so the equity columns are hidden here rather than shown empty.
          Expense ratio and AUM come from the fund profile
          (<b>Refresh ETFs</b> to update), including the price levels — those are fetched per fund, so a newly added ETF has them before any scan runs.
          <b>Click a theme to rename it</b> — your label is kept separately from the
          provider's category, so a refresh won't overwrite it. Blank resets it.
        </div>
        <table><thead><tr>${{head}}</tr></thead>
        <tbody>${{body}}</tbody></table>`;
    }}
    function showEtfHoldings(ticker) {{
      const r = RESEARCH_ROWS.find(x => x.ticker === ticker);
      if (!r) return;
      const hs = r.etf_holdings || [];
      document.getElementById('etf-modal-title').textContent =
        `${{ticker}} — top ${{hs.length}} published holdings`;
      document.getElementById('etf-modal-body').innerHTML = `
        <div style="font-size:11px;color:#898781;margin-bottom:10px">
          ${{r.etf_theme_name || r.etf_category || ''}}${{r.etf_family ? ' · ' + r.etf_family : ''}}
          ${{r.etf_top10_weight != null ? ' · top 10 = ' + fmtEtfPct(r.etf_top10_weight, 1)
             + ' of the fund' : ''}}
        </div>
        <table><thead><tr><th>#</th><th>Ticker</th><th>Name</th>
          <th style="text-align:right">Weight</th></tr></thead><tbody>
        ${{hs.map((h, i) => `<tr><td style="color:#898781">${{i + 1}}</td>
          <td><a href="/research/${{h.ticker}}.html"><b>${{h.ticker}}</b></a></td>
          <td style="font-size:11px">${{h.name || ''}}</td>
          <td style="text-align:right;font-weight:600">${{fmtEtfPct(h.weight)}}</td></tr>`).join('')}}
        </tbody></table>
        ${{etfCoverageNote(r)}}
        ${{r.etf_theme ? `<div style="margin-top:12px;font-size:11px;line-height:1.5;
          color:#52514e"><b>Built on:</b> ${{r.etf_theme}}</div>` : ''}}`;
      openModal('etf-modal');
    }}

    function filteredRows() {{
      const q = document.getElementById('rsearch').value.trim().toUpperCase();
      const watchSelected = Array.from(document.getElementById('rwatch').selectedOptions).map(o => o.value);
      const watchTickers = watchSelected.length
        ? new Set(watchSelected.flatMap(w => WATCHLISTS[w] || [])) : null;
      return RESEARCH_ROWS.filter(r =>
        (!q || r.ticker.includes(q) || (r.sector || '').toUpperCase().includes(q)) &&
        (!watchTickers || watchTickers.has(r.ticker)) &&
        (!presetTickers || presetTickers.has(r.ticker)) &&
        (!actionFilter || (actionFilter === 'NODATA' ? r.no_data
                           : actionFilter === 'ETF' ? r.is_etf
                           : r.conv_action === actionFilter)));
    }}

    // ── Screener presets ───────────────────────────────────────────────────
    // The preset is evaluated by /api/screen — the same engine the Screener
    // page uses — and only the resulting ticker set is applied here. Doing
    // the comparisons in JS instead would mean a second implementation of
    // every operator and threshold, free to drift from the first.
    let presetTickers = null;      // null = no preset filter
    async function applyResearchPreset(key) {{
      const label = document.getElementById('rpreset-info');
      if (!key) {{
        presetTickers = null;
        label.textContent = '';
        renderResearch();
        return;
      }}
      label.textContent = 'running…';
      try {{
        const res = await fetch('/api/screen', {{
          method: 'POST',
          body: JSON.stringify({{ preset: key, limit: 5000, sort: 'match' }}),
        }});
        const data = await res.json();
        if (!data.ok) {{ toast(data.message || 'Screen failed', 'err'); label.textContent = ''; return; }}
        presetTickers = new Set(data.results.map(r => r.ticker));
        // The Screener excludes tickers with no quote, so a preset can match
        // names this table no longer lists; report what actually landed.
        // This counts the screen against the whole library. The table's own
        // count can be lower because the search, watchlist and Action
        // filters still apply on top — saying just "14" next to a table
        // showing 9 reads like one of them is wrong.
        const shown = RESEARCH_ROWS.filter(r => presetTickers.has(r.ticker)).length;
        label.textContent = `${{shown}} in library match this screen`;
        renderResearch();
      }} catch (e) {{
        toast('Preset failed: ' + e, 'err');
        label.textContent = '';
      }}
    }}
    function renderResearch() {{
      renderActionTabs();
      const view = document.getElementById('rview').value;
      let rows = filteredRows();
      document.getElementById('rcount').textContent = rows.length;
      const root = document.getElementById('research-root');
      // The ETF tab replaces the table rather than filtering it: a fund has
      // no margins, EPS or moat, so 20 of the 24 equity columns would be
      // dashes and the four that matter (theme, cost, size, holdings) have
      // nowhere to go.
      if (actionFilter === 'ETF') {{ renderEtfTable(rows, root); return; }}
      if (view === 'grouped') {{
        const bySec = {{}};
        rows.forEach(r => {{ const s = r.sector || 'Unknown'; (bySec[s] = bySec[s] || []).push(r); }});
        root.innerHTML = Object.keys(bySec).sort().map(sec => `
          <div style="margin-bottom:14px">
            <div style="font-size:12px;font-weight:700;color:#898781;margin-bottom:6px">${{sec}} (${{bySec[sec].length}})</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px">
              ${{bySec[sec].map(r => `<a href="/research/${{r.ticker}}.html" class="chip" style="border-left:3px solid ${{actionColor(r.conv_action)}}">${{r.ticker}} · ${{r.conv_overall ?? '—'}}</a>`).join('')}}
            </div></div>`).join('');
        return;
      }}
      rows = sortRows(rows);
      const arrow = key => sortKey === key ? (sortDir === 1 ? ' ▲' : ' ▼') : '';
      // Resize handle on the right edge of each header cell. draggable="false"
      // on the handle excludes it from the header's own HTML5 drag-to-reorder
      // (see onColDragStart) so a mousedown here starts a resize instead;
      // click is also stopped so it doesn't trigger setSort.
      const resizer = key => `<span class="col-resizer" draggable="false"
             onmousedown="onColResizeStart(event,'${{key}}')" onclick="event.stopPropagation()"></span>`;
      // Width is enforced on an inner inline-block span (max-width + ellipsis),
      // not on the <th>/<td> box itself — table-layout:fixed column widths
      // aren't reliably honored for short-content cells in every rendering
      // engine this runs under, but a capped inline-block always is (same
      // technique the Detailed-metrics raw columns already use below).
      // Only a column the user has actually dragged (colWidths[key] set)
      // gets a forced 'width' — that's what makes short content (e.g. "—")
      // visibly fill the wider column instead of just capping how far it
      // COULD grow. Untouched columns stay natural-width-up-to-a-cap so the
      // table isn't padded out to every column's default guess by default.
      const cellSpan = (key, label, w) => {{
        const sizing = colWidths[key] != null ? `width:${{w}}px;max-width:${{w}}px` : `max-width:${{w}}px`;
        return `<span data-colw="${{key}}" style="display:inline-block;${{sizing}};
             overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom">${{label}}</span>`;
      }};
      const th = (key, label) => {{
        const w = colW(key, label);
        return `<th draggable="true" title="Drag to reorder — drag right edge to resize"
             style="cursor:grab;white-space:nowrap;position:relative"
             onclick="setSort('${{key}}')"
             ondragstart="onColDragStart(event,'${{key}}')" ondragend="onColDragEnd(event)"
             ondragover="onColDragOver(event)" ondragenter="onColDragEnter(event)"
             ondragleave="onColDragLeave(event)" ondrop="onColDrop(event,'${{key}}')"
             >${{cellSpan(key, label + arrow(key), w)}}${{resizer(key)}}</th>`;
      }};
      const defs = visibleCols.map(k => COL_BY_KEY[k]).filter(Boolean);
      document.getElementById('colcount').textContent = visibleCols.length;
      // star + Ticker cells are sticky-left so the ticker stays visible
      // while scrolling the wide table horizontally
      const tickerW = colW('ticker', 'Ticker');
      const stickyStar = 'position:sticky;left:0;background:#fff;z-index:2;width:26px;min-width:26px;max-width:26px';
      const stickyTicker = 'position:sticky;left:26px;background:#fff;z-index:2;border-right:1px solid #f1efea';
      root.innerHTML = `<table style="width:auto;min-width:100%;white-space:nowrap"><thead><tr>
        <th style="${{stickyStar}};z-index:3"></th>
        <th style="${{stickyTicker}};z-index:3;cursor:pointer;white-space:nowrap"
            onclick="setSort('ticker')">${{cellSpan('ticker', 'Ticker' + arrow('ticker'), tickerW)}}${{resizer('ticker')}}</th>
        ${{defs.map(d => th(d.key, d.label)).join('')}}
        <th></th></tr></thead><tbody>
        ${{rows.map(r => `<tr>
          <td style="${{stickyStar}}"><button onclick="toggleWatchlist(document.getElementById('rwatch').value, '${{r.ticker}}', this)"
              style="background:none;border:none;font-size:14px;color:#c9a227;padding:0">${{starredIn(document.getElementById('rwatch').value, r.ticker) ? '★' : '☆'}}</button></td>
          <td style="${{stickyTicker}}"><a href="https://www.tradingview.com/chart/?symbol=${{r.ticker.replace('-', '.')}}"
                 target="_blank" title="Open TradingView chart">${{cellSpan('ticker', `<b>${{r.ticker}}</b>`, tickerW)}}</a></td>
          ${{defs.map(d => `<td style="font-size:11px">${{cellSpan(d.key, d.cell(r), colW(d.key, d.label))}}</td>`).join('')}}
          <td style="display:flex;gap:4px">
            <a href="/research/${{r.ticker}}.html" class="btn secondary" style="text-decoration:none;padding:3px 10px;font-size:11px">Open</a>
            <button type="button" onclick="openDetail('${{r.ticker}}')" class="btn secondary" style="padding:3px 10px;font-size:11px">Detailed Metrics</button>
            <button type="button" onclick="runEarningsAnalysis('${{r.ticker}}')" class="btn secondary" style="padding:3px 10px;font-size:11px">Earnings Analysis</button>
            <button type="button" onclick="runTaAnalysis('${{r.ticker}}')" class="btn secondary" style="padding:3px 10px;font-size:11px">AI Technicals</button>
          </td>
        </tr>`).join('')}}
      </tbody></table>`;
    }}
    function setSort(key) {{
      sortDir = (sortKey === key) ? -sortDir : 1;
      sortKey = key;
      renderResearch();
    }}

    // ── CSV export ───────────────────────────────────────────────────────
    // Columns come from visibleCols, which is exactly what drag-to-reorder
    // writes, so the file's column order matches the table's. 'all' appends
    // the unselected columns after the visible ones rather than reordering,
    // keeping the on-screen order intact at the front. Rows honour the
    // current search / watchlist / action filter and the active sort.
    const csvScratch = document.createElement('div');
    function csvText(html) {{
      csvScratch.innerHTML = html;
      const t = (csvScratch.textContent || '').replace(/\\s+/g, ' ').trim();
      return t === '—' ? '' : t;   // the table's null placeholder
    }}
    function csvValue(col, r) {{
      if (col.key.startsWith('raw:')) {{
        const v = (r.raw || {{}})[col.key.slice(4)];
        return v == null ? '' : v;
      }}
      // Prefer the stored scalar so numbers land in a spreadsheet as numbers
      // rather than "$88.19". Composite cells (Moat "Strong 3/4", Position
      // "#5 /19") have no single field behind them — fall back to the
      // rendered text, which is what the user sees anyway.
      const v = r[col.key];
      if (typeof v === 'number' || typeof v === 'boolean') return v;
      if (typeof v === 'string' && v !== '') return v;
      try {{ return csvText(col.cell(r)); }} catch (e) {{ return ''; }}
    }}
    function csvEscape(v) {{
      const s = (v === null || v === undefined) ? '' : String(v);
      return /[",\\r\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }}
    function downloadResearchCsv(mode) {{
      const keys = mode === 'all'
        ? [...visibleCols, ...ALL_COLS.map(c => c.key).filter(k => !visibleCols.includes(k))]
        : [...visibleCols];
      const cols = keys.map(k => COL_BY_KEY[k]).filter(Boolean);
      const rows = sortRows(filteredRows());
      if (!rows.length) {{ toast('Nothing to export — the current filter matches no rows', 'err'); return; }}
      // Ticker is a sticky column outside visibleCols; without it the export
      // has no row identity.
      const header = ['Ticker', ...cols.map(c => c.label)];
      const lines = [header.map(csvEscape).join(',')];
      for (const r of rows) {{
        lines.push([r.ticker, ...cols.map(c => csvValue(c, r))].map(csvEscape).join(','));
      }}
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:]/g, '').replace('T', '_');
      const name = `research_${{mode === 'all' ? 'allcols' : 'view'}}_${{stamp}}.csv`;
      // BOM + CRLF so Excel reads the ★ / ▲ / — glyphs as UTF-8 and splits rows
      const blob = new Blob(['\\ufeff' + lines.join('\\r\\n')], {{type: 'text/csv;charset=utf-8;'}});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast(`${{rows.length}} row(s) × ${{header.length}} column(s) → ${{name}}`, 'ok');
    }}
    // "Detailed Metrics": every column the scan/research pipeline computed
    // for this ticker (row.raw, same fields stock_scan_*.csv has) merged
    // with the curated fields already shown in the table — one place to see
    // everything instead of hunting across a 25-column row.
    function fmtDetailVal(v) {{
      if (v === null || v === undefined || v === '') return '—';
      if (typeof v === 'boolean') return v ? '✓' : '✗';
      return String(v);
    }}
    // Who the Position column ranked this name against. "#1 of 23" is only
    // interpretable once the 23 are visible, so the peer group is rendered
    // ahead of the raw field dump, in rank order with this ticker marked.
    function peerBlockHtml(row) {{
      if (!row.peer_group) return '';
      const peers = RESEARCH_ROWS
        .filter(r => r.peer_group === row.peer_group && r.peer_rank != null)
        .sort((a, b) => a.peer_rank - b.peer_rank);
      const scope = row.peer_group_is_sector ? 'sector' : 'industry';
      if (!peers.length) {{
        return `<div style="margin-bottom:12px;font-size:12px;color:#898781">
          <b>${{row.peer_group}}</b> (${{scope}}) — no tracked peer has a market cap yet,
          so no rank could be computed.</div>`;
      }}
      const rows = peers.map(p => {{
        const me = p.ticker === row.ticker;
        return `<tr style="${{me ? 'background:#E6F1FB' : ''}}">
          <td style="color:#898781;padding:2px 10px 2px 0;white-space:nowrap">#${{p.peer_rank}}</td>
          <td style="padding:2px 10px 2px 0;white-space:nowrap">${{me
              ? `<b>${{p.ticker}}</b>`
              : `<a href="/research/${{p.ticker}}.html">${{p.ticker}}</a>`}}</td>
          <td style="padding:2px 10px 2px 0;color:#898781;white-space:nowrap">${{fmtCap(p.market_cap)}}</td>
          <td style="padding:2px 0;color:#898781;white-space:nowrap">${{p.peer_share_pct != null ? p.peer_share_pct + '%' : '—'}}</td>
        </tr>`;
      }}).join('');
      const struct = row.structure
        ? ` · curated: <b style="color:#0F6E56">${{row.structure}}</b>` : '';
      return `<div style="margin-bottom:14px;border:1px solid #e1e0d9;border-radius:8px;padding:10px 12px">
        <div style="font-size:12px;font-weight:700;margin-bottom:2px">Peer group — ${{row.peer_group}}</div>
        <div style="font-size:11px;color:#898781;margin-bottom:8px">
          ranked by market cap among ${{peers.length}} tracked ${{scope}} peers${{struct}}
          · size proxy, not market share</div>
        <table style="font-size:11px"><tbody>${{rows}}</tbody></table></div>`;
    }}
    function openDetail(ticker) {{
      const row = RESEARCH_ROWS.find(r => r.ticker === ticker);
      if (!row) return;
      document.getElementById('detail-title').textContent = ticker + ' — Detailed Metrics';
      const {{ raw, ...curated }} = row;
      const all = {{ ...curated, ...(raw || {{}}) }};
      const keys = Object.keys(all);
      document.getElementById('detail-body').innerHTML = peerBlockHtml(row) + (keys.length
        ? `<table style="width:100%"><tbody>${{keys.map(k => `
            <tr><td style="color:#898781;white-space:nowrap;padding-right:14px;vertical-align:top">${{k}}</td>
                <td style="font-weight:600;word-break:break-word">${{fmtDetailVal(all[k])}}</td></tr>`).join('')}}</tbody></table>`
        : '<span style="font-size:12px;color:#898781">No detailed metrics captured yet — re-run a scan or research refresh for this ticker.</span>');
      openModal('modal-detail');
    }}
    // Deep links from alert cards etc.: /research?ticker=NVDA&cols=all
    // pre-fills the filter and shows EVERY column (standard + detailed) for
    // this visit only — the user's saved column selection isn't overwritten.
    document.addEventListener('DOMContentLoaded', () => {{
      const params = new URLSearchParams(location.search);
      if (params.get('cols') === 'all') {{
        visibleCols = ALL_COLS.map(c => c.key);   // transient — no saveCols()
      }}
      const t = params.get('ticker');
      if (t) {{
        document.getElementById('rsearch').value = t;
      }} else {{
        // Default view = every ticker, no category filter. This used to
        // preselect the "watchlist" category, which quietly hid most of the
        // library — including ETFs that aren't on that list, so the ETF tab
        // showed 12 of 16. Browsers also restore <select> state across a
        // reload, so the selection is cleared explicitly rather than just
        // left unset.
        const wsel = document.getElementById('rwatch');
        if (wsel) wsel.selectedIndex = -1;
      }}
      renderResearch();
    }});

    // "Earnings Analysis": deterministic weighted-score engine (see
    // core/earnings_sentiment.py) run on demand via the job-tray pattern —
    // POST starts it, the job writes /earnings/<TICKER>.json, and once
    // /api/jobs reports it done this fetches that file and renders it.
    let _pendingEarningsTicker = null;
    async function runEarningsAnalysis(ticker) {{
      toast('Analyzing ' + ticker + ' — earnings history, options chain, '
           + 'market context (~15-20s)…', 'ok');
      try {{
        const res = await fetch('/run', {{
          method: 'POST',
          body: new URLSearchParams({{ action: 'earnings_analysis', ticker }}),
        }});
        const data = await res.json();
        if (!data.ok) {{ toast(data.message || 'Failed to start', 'err'); return; }}
      }} catch (e) {{ toast('Request failed: ' + e, 'err'); return; }}
      _pendingEarningsTicker = ticker;
      pollJobs();
    }}
    function biasColor(bias) {{
      if (bias === 'Strong Buy' || bias === 'Buy') return '#0F6E56';
      if (bias === 'Strong Sell' || bias === 'Sell') return '#A32D2D';
      return '#8a6d1a';
    }}
    function riskColor(risk) {{
      return risk === 'Low' ? '#0F6E56' : risk === 'High' ? '#A32D2D'
           : risk === 'Medium' ? '#8a6d1a' : '#898781';
    }}
    function reasonList(arr) {{
      return (arr && arr.length)
        ? `<ul style="margin:4px 0 0;padding-left:16px">${{arr.map(r =>
            `<li style="font-size:11px;margin-bottom:2px">${{r}}</li>`).join('')}}</ul>`
        : '<div style="font-size:11px;color:#898781">none flagged</div>';
    }}
    function renderEarningsModal(ticker, data) {{
      document.getElementById('earnings-title').textContent = ticker + ' — Earnings Analysis';
      const bColor = biasColor(data.trading_bias);
      const rColor = riskColor(data.earnings_risk);
      const move = data.expected_move_pct;
      const moveSrc = data.expected_move_source === 'options_straddle' ? 'options-implied'
                    : data.expected_move_source === 'historical_avg' ? 'historical avg (no options data)' : '';
      const factorRows = Object.entries(data.factor_scores || {{}}).map(([k, v]) => {{
        const halfPct = Math.min(50, Math.abs(v) / 25 * 50);
        const color = v > 0 ? '#0F6E56' : v < 0 ? '#A32D2D' : '#d9d7ce';
        const barLeft = v >= 0 ? 50 : 50 - halfPct;
        return `<div style="display:flex;align-items:center;gap:8px;margin:3px 0">
          <span style="min-width:120px;font-size:11px;text-transform:capitalize">${{k.replace(/_/g,' ')}}</span>
          <div style="flex:1;background:#f1efea;border-radius:3px;height:8px;position:relative">
            <div style="position:absolute;top:0;left:${{barLeft}}%;width:${{halfPct}}%;height:8px;background:${{color}};border-radius:2px"></div>
            <div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:#d9d7ce"></div>
          </div>
          <span style="min-width:28px;text-align:right;font-size:11px;font-weight:600">${{v > 0 ? '+' : ''}}${{v}}</span>
        </div>`;
      }}).join('');
      document.getElementById('earnings-body').innerHTML = `
        <div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:14px">
          <div><div style="font-size:11px;color:#898781">Trading Bias</div>
            <div style="font-size:16px;font-weight:700;color:${{bColor}}">${{data.trading_bias}}</div></div>
          <div><div style="font-size:11px;color:#898781">Confidence</div>
            <div style="font-size:16px;font-weight:700">${{data.confidence}}/10</div></div>
          <div><div style="font-size:11px;color:#898781">Expected Move</div>
            <div style="font-size:16px;font-weight:700">${{move != null ? '±' + move.toFixed(1) + '%' : '—'}}</div>
            <div style="font-size:10px;color:#898781">${{moveSrc}}</div></div>
          <div><div style="font-size:11px;color:#898781">Earnings Risk</div>
            <div style="font-size:16px;font-weight:700;color:${{rColor}}">${{data.earnings_risk}}</div></div>
          <div><div style="font-size:11px;color:#898781">Next Earnings</div>
            <div style="font-size:14px;font-weight:600">${{data.next_earnings_date || '—'}}${{data.days_to_earnings != null ? ' (' + data.days_to_earnings + 'd)' : ''}}</div></div>
        </div>
        <div style="margin-bottom:14px">
          <div style="font-size:11px;color:#898781;margin-bottom:4px">Bullish ${{data.bullish_probability}}% / Bearish ${{data.bearish_probability}}% · total score ${{data.total_score}} · regime ${{data.market_regime}}</div>
          <div style="display:flex;height:14px;border-radius:7px;overflow:hidden">
            <div style="width:${{data.bullish_probability}}%;background:#0F6E56"></div>
            <div style="width:${{data.bearish_probability}}%;background:#A32D2D"></div>
          </div>
        </div>
        <div style="margin-bottom:14px">
          <div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">FACTOR SCORES (of ±100 total)</div>
          ${{factorRows}}
        </div>
        <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:14px">
          <div style="flex:1;min-width:200px">
            <div style="font-size:11px;font-weight:600;color:#0F6E56">BULLISH REASONS</div>
            ${{reasonList(data.key_bullish_reasons)}}
          </div>
          <div style="flex:1;min-width:200px">
            <div style="font-size:11px;font-weight:600;color:#A32D2D">BEARISH REASONS</div>
            ${{reasonList(data.key_bearish_reasons)}}
          </div>
        </div>
        ${{(data.risk_factors && data.risk_factors.length) ? `
        <div style="margin-bottom:10px">
          <div style="font-size:11px;font-weight:600;color:#633806">RISK FACTORS</div>
          ${{reasonList(data.risk_factors)}}
        </div>` : ''}}
        <details style="margin-top:6px">
          <summary style="font-size:10px;color:#898781;cursor:pointer">What this doesn't cover</summary>
          <ul style="margin:4px 0 0;padding-left:16px">${{(data.data_gaps || []).map(g =>
            `<li style="font-size:10px;color:#898781;margin-bottom:2px">${{g}}</li>`).join('')}}</ul>
        </details>
        <div style="font-size:10px;color:#898781;margin-top:8px">Generated ${{data.generated_at}} · deterministic scoring, not investment advice.</div>
      `;
      openModal('modal-earnings');
    }}

    // "AI Technicals": multi-timeframe Claude analysis (core.technical_analysis)
    // via the same job-tray pattern as Earnings Analysis.
    let _pendingTaTicker = null;
    async function runTaAnalysis(ticker) {{
      toast('Analyzing ' + ticker + ' — Monthly/Weekly/Daily/4H frames, '
           + 'indicators, then AI review (~30-60s)…', 'ok');
      try {{
        const res = await fetch('/run', {{
          method: 'POST',
          body: new URLSearchParams({{ action: 'ta_analysis', ticker }}),
        }});
        const data = await res.json();
        if (!data.ok) {{ toast(data.message || 'Failed to start', 'err'); return; }}
      }} catch (e) {{ toast('Request failed: ' + e, 'err'); return; }}
      _pendingTaTicker = ticker;
      pollJobs();
    }}
    function taPrice(v) {{ return v != null ? '$' + Number(v).toFixed(2) : '—'; }}
    function renderTaModal(ticker, d) {{
      document.getElementById('ta-title').textContent = ticker + ' — AI Technical Analysis';
      if (d.error) {{
        document.getElementById('ta-body').innerHTML =
          `<div style="font-size:12px;color:#A32D2D">${{d.error}}</div>`;
        openModal('modal-ta'); return;
      }}
      const t = d.current_trend || {{}};
      const vColor = d.verdict === 'BUY_ZONE_VALID' ? '#0F6E56' : '#8a6d1a';
      const trendChip = (label, v) => `<div><div style="font-size:10px;color:#898781">${{label}}</div>
        <div style="font-size:12px;font-weight:600">${{v || '—'}}</div></div>`;
      const list = arr => (arr && arr.length)
        ? `<ul style="margin:4px 0 0;padding-left:16px">${{arr.map(x =>
            `<li style="font-size:11px;margin-bottom:2px">${{x}}</li>`).join('')}}</ul>`
        : '<div style="font-size:11px;color:#898781">none listed</div>';
      document.getElementById('ta-body').innerHTML = `
        <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px">
          <div><div style="font-size:11px;color:#898781">Verdict</div>
            <div style="font-size:16px;font-weight:700;color:${{vColor}}">${{d.verdict === 'BUY_ZONE_VALID' ? 'Buy zone valid' : 'WAIT'}}</div></div>
          <div><div style="font-size:11px;color:#898781">Overall Trend</div>
            <div style="font-size:16px;font-weight:700">${{t.overall || '—'}}</div></div>
          <div><div style="font-size:11px;color:#898781">Probability</div>
            <div style="font-size:16px;font-weight:700">${{d.probability_score}}%</div></div>
          <div><div style="font-size:11px;color:#898781">Price now</div>
            <div style="font-size:16px;font-weight:700">${{taPrice(d.current_price)}}</div></div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px">
          ${{trendChip('Monthly', t.monthly)}}${{trendChip('Weekly', t.weekly)}}
          ${{trendChip('Daily', t.daily)}}${{trendChip('4H', t.four_hour)}}
        </div>
        ${{d.verdict !== 'BUY_ZONE_VALID' ? `
        <div style="background:#FAEEDA;color:#633806;padding:8px 12px;border-radius:8px;font-size:12px;margin-bottom:12px">
          <b>Wait:</b> ${{d.wait_reason || ''}}
          ${{d.better_entry_price != null ? ` · better entry ≈ <b>${{taPrice(d.better_entry_price)}}</b>` : ''}}
        </div>` : ''}}
        <table style="width:100%;font-size:12px;margin-bottom:12px"><tbody>
          <tr><td style="color:#898781;width:150px">Buy zone</td>
              <td><b>${{taPrice(d.buy_zone?.low)}} – ${{taPrice(d.buy_zone?.high)}}</b></td></tr>
          <tr><td style="color:#898781">Ideal entry</td><td><b>${{taPrice(d.ideal_entry)}}</b></td></tr>
          <tr><td style="color:#898781">Stop loss</td><td style="color:#A32D2D"><b>${{taPrice(d.stop_loss)}}</b></td></tr>
          <tr><td style="color:#898781">Targets</td>
              <td>T1 <b>${{taPrice(d.targets?.t1)}}</b> · T2 <b>${{taPrice(d.targets?.t2)}}</b> · T3 <b>${{taPrice(d.targets?.t3)}}</b></td></tr>
          <tr><td style="color:#898781">Risk : Reward</td><td>${{d.risk_reward || '—'}}</td></tr>
        </tbody></table>
        <div style="font-size:11px;font-weight:600;color:#0C447C">CONFIRMATIONS REQUIRED BEFORE BUYING</div>
        ${{list(d.confirmations_required)}}
        <div style="font-size:11px;font-weight:600;color:#0F6E56;margin-top:10px">WHY HIGH-PROBABILITY</div>
        ${{list(d.reasons)}}
        <div style="font-size:11px;font-weight:600;color:#A32D2D;margin-top:10px">TRADE INVALIDATED IF</div>
        ${{list(d.invalidations)}}
        <div style="font-size:12px;color:#52514e;margin-top:10px;line-height:1.5">${{d.summary || ''}}</div>
        <div style="font-size:10px;color:#898781;margin-top:10px">
          ${{d.model || ''}} · ${{d.generated_at || ''}} · ${{(d.data_notes || []).join(' · ')}} ·
          analysis, not financial advice</div>`;
      openModal('modal-ta');
    }}

    // A research/news job finishing means RESEARCH_ROWS is stale (updated
    // scores, new tickers, refreshed timestamps) — reload once to pick it up
    function onJobFinished(j) {{
      if (j.kind === 'research' || j.kind === 'news') setTimeout(() => location.reload(), 1200);
      if (j.kind === 'earnings' && _pendingEarningsTicker) {{
        const ticker = _pendingEarningsTicker;
        _pendingEarningsTicker = null;
        if (j.status === 'done') {{
          fetch('/earnings/' + ticker + '.json').then(r => r.json())
            .then(data => renderEarningsModal(ticker, data))
            .catch(e => toast('Could not load analysis: ' + e, 'err'));
        }}
      }}
      if (j.kind === 'ta' && _pendingTaTicker) {{
        const ticker = _pendingTaTicker;
        _pendingTaTicker = null;
        fetch('/ta/' + ticker + '.json').then(r => r.json())
          .then(data => renderTaModal(ticker, data))
          .catch(e => toast('Could not load analysis: ' + e, 'err'));
      }}
    }}
    """

    return card("Research Library", controls, "🔎", pad="16px 18px"), js


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO
# ─────────────────────────────────────────────────────────────────────────────

def _latest_scan_rows() -> list[dict]:
    # Take a window of recent scans and *then* drop the per-strategy splits —
    # the splits are written after the full scan, so filtering a 1-file list
    # would leave nothing and silently blank out every price on the page.
    files = [f for f in _latest("stock_scan_*.csv", 8)
             if not any(f.stem.endswith(s) for s in
                        ("_daytrade", "_swing", "_longterm"))][:1]
    if not files:
        return []
    import pandas as pd
    df = pd.read_csv(files[0])
    return [{k: (None if pd.isna(v) else v) for k, v in r.items()}
            for _, r in df.iterrows()]


def _options_card() -> str:
    """Open option contracts from data/options_positions.csv (written by the
    get-portfolio skill). Kept out of the Holdings table on purpose — the
    equity table's columns (price, alloc %, days held, stop/target) describe a
    share position, and options need strike/expiry/premium instead."""
    from stockanalysis.reporting import options_positions as op

    view = op.build_options_view(op.load_options())
    if not view:
        return card("Options", empty(
            "No option positions on file. Ask me to “get portfolio” to pull "
            "them from Robinhood into data/options_positions.csv."), "🎯")

    totals = op.options_totals(view)
    gain = totals.get("total_gain")
    gain_status = "good" if (gain or 0) >= 0 else "bad"
    summary = f"""
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:16px;margin-bottom:12px">
      <div><div style="font-size:11px;color:#898781">Open Contracts</div>
        <div style="font-size:20px;font-weight:650">{totals.get('contracts')}</div></div>
      <div><div style="font-size:11px;color:#898781">Cost Basis</div>
        <div style="font-size:20px;font-weight:650">{fmt_money(totals.get('total_cost'), 0)}</div></div>
      <div><div style="font-size:11px;color:#898781">Market Value</div>
        <div style="font-size:20px;font-weight:650">{fmt_money(totals.get('total_value'), 0)}</div></div>
      <div><div style="font-size:11px;color:#898781">Open P&L</div>
        <div style="font-size:20px;font-weight:650">{badge(fmt_money(gain, 0) if gain is not None else '—', gain_status)}
        <span style="font-size:11px;color:#898781"> {fmt_pct(totals.get('total_gain_pct'))}</span></div></div>
      <div><div style="font-size:11px;color:#898781">Expiring ≤{op.EXPIRY_WARN_DAYS}d</div>
        <div style="font-size:20px;font-weight:650">{totals.get('expiring_soon')}</div></div>
    </div>"""

    rows = []
    for o in view:
        g_pct = o.get("Gain_Pct")
        g_color = "#0F6E56" if (g_pct or 0) >= 0 else "#A32D2D"
        dte = o.get("Days_To_Expiry")
        dte_color = ("#791F1F" if (dte is not None and dte <= 0)
                     else "#633806" if (dte is not None and dte <= op.EXPIRY_WARN_DAYS)
                     else "#0b0b0b")
        alerts = "".join(
            f'<div style="font-size:10px;color:{"#791F1F" if "⛔" in a else "#633806"}">{esc(a)}</div>'
            for a in o.get("Alerts") or [])
        rows.append(f"""
        <tr>
          <td><a href="{tv_url(o['Underlying'])}" target="_blank" rel="noopener"><b>{esc(o['Label'])}</b></a></td>
          <td>{esc(o['Side'])}</td>
          <td style="text-align:right">{o['Contracts']:g}</td>
          <td style="text-align:right">{fmt_money(o.get('Avg_Premium'))}</td>
          <td style="text-align:right">{fmt_money(o.get('Current_Premium'))}</td>
          <td style="text-align:right">{fmt_money(o.get('Market_Value'), 0)}</td>
          <td style="text-align:right;color:{g_color}">{fmt_money(o.get('Gain_Dollars'), 0)}</td>
          <td style="text-align:right;color:{g_color}">{fmt_pct(g_pct) if g_pct is not None else '—'}</td>
          <td style="text-align:right;color:{dte_color};font-weight:600">{dte if dte is not None else '—'}</td>
          <td>{esc(o.get('Strategy') or '')}</td>
          <td>{alerts or '<span style="font-size:10px;color:#0F6E56">✓ clear</span>'}</td>
        </tr>""")

    # The quote is whatever the last sync captured — this app has no broker
    # session, so saying when it was taken is the difference between a number
    # you can act on and one you can't.
    quoted = [o.get("Quote_At") for o in view if o.get("Quote_At")]
    freshness = (f"Premiums quoted {esc(max(quoted))} — a snapshot from the last "
                 f"sync, not live. Ask me to “get portfolio” to refresh."
                 if quoted else
                 "No premium quotes yet — ask me to “get portfolio” to fetch them.")

    table = f"""<table><thead><tr>
      <th>Contract</th><th>Side</th><th style="text-align:right">Contracts</th>
      <th style="text-align:right">Avg Premium</th><th style="text-align:right">Last Quote</th>
      <th style="text-align:right">Value</th><th style="text-align:right">Gain $</th>
      <th style="text-align:right">Gain %</th><th style="text-align:right">DTE</th>
      <th>Strategy</th><th>Alerts</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table>
      <div style="font-size:10px;color:#898781;margin-top:8px">{freshness}</div>"""
    return card("Options", summary + table, "🎯")


def portfolio_page() -> tuple[str, str]:
    from stockanalysis.reporting.portfolio import (
        load_positions, build_portfolio_view, portfolio_totals,
        allocation_summary)

    positions = load_positions()
    if not positions:
        body = card("", empty(
            "No portfolio file found. Copy data/portfolio_template.csv to "
            "data/portfolio.csv and list your positions (Ticker, Shares, "
            "Avg_Cost, Entry_Date, Strategy, Stop, Target, Notes) — or add "
            "your first one below."), pad="40px")
        body += card("", '<button type="button" class="btn" '
                     'onclick="openPositionModal(null)">+ Add Position</button>', pad="0 18px 18px")
        # Options are a separate file — they can exist before any equity row does.
        return body + _options_card() + _position_modal(), _position_js

    rows = _latest_scan_rows()
    view = build_portfolio_view(positions, rows)
    totals = portfolio_totals(view)
    alloc = allocation_summary(view)

    gain = totals.get("total_gain")
    gain_status = "good" if (gain or 0) >= 0 else "bad"
    day = totals.get("total_day")
    day_status = "good" if (day or 0) >= 0 else "bad"

    def _tile(label, value, sub=""):
        sub = (f'<span style="font-size:11px;color:#898781"> {sub}</span>'
               if sub else "")
        return (f'<div><div style="font-size:11px;color:#898781">{label}</div>'
                f'<div style="font-size:20px;font-weight:650">{value}{sub}</div></div>')

    stale = totals.get("priced_from_broker") or 0
    stale_note = (f'<div style="font-size:11px;color:#898781;margin-top:10px">'
                  f'{stale} of {totals.get("positions")} positions aren\'t in '
                  f"today's scan — priced from the last broker sync.</div>"
                  if stale else "")
    summary = f"""
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:16px">
      {_tile('Market Value', fmt_money(totals.get('total_value'), 0))}
      {_tile('Cost Basis', fmt_money(totals.get('total_cost'), 0))}
      {_tile('Total Gain',
             badge(fmt_money(gain, 0) if gain is not None else '—', gain_status),
             fmt_pct(totals.get('total_gain_pct')))}
      {_tile('Day Change',
             badge(fmt_money(day, 0) if day is not None else '—', day_status),
             fmt_pct(totals.get('total_day_pct')))}
      {_tile('Portfolio Value', fmt_money(totals.get('portfolio_value'), 0))}
      {_tile('Cash', fmt_money(totals.get('cash'), 0))}
      {_tile('At Risk', fmt_money(totals.get('total_risk'), 0))}
      {_tile('Positions / Watching',
             f"{totals.get('positions')} / {totals.get('watching')}")}
    </div>{stale_note}"""

    def _alloc_bars(pcts):
        if not pcts:
            return empty("—")
        peak = max(p for _, p in pcts) or 1
        return "".join(
            f'<div style="display:flex;align-items:center;gap:8px;margin:4px 0">'
            f'<span style="min-width:100px;font-size:11px">{esc(k)}</span>'
            f'<div style="flex:1;background:#f1efea;border-radius:3px;height:9px">'
            f'<div style="width:{max(2, round(p / peak * 100))}%;background:#185FA5;height:9px;border-radius:3px"></div></div>'
            f'<span style="min-width:40px;text-align:right;font-size:11px;font-weight:600">{p:.1f}%</span></div>'
            for k, p in pcts)

    warn_html = "".join(f'<div style="font-size:11px;color:#791F1F;margin-top:6px">⚠ {esc(w)}</div>'
                        for w in alloc.get("warnings") or [])
    alloc_html = (f'<div style="display:flex;gap:24px;flex-wrap:wrap">'
                 f'<div style="flex:1;min-width:200px"><div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">MARKET CAP</div>{_alloc_bars(alloc.get("caps") or [])}</div>'
                 f'<div style="flex:1;min-width:200px"><div style="font-size:11px;font-weight:600;color:#898781;margin-bottom:4px">SECTOR</div>{_alloc_bars(alloc.get("sectors") or [])}</div>'
                 f'</div>{warn_html}')

    def _signed(val, kind="money"):
        """Green/red cell for a P&L number; em dash when there's nothing."""
        if val is None:
            return '<td style="text-align:right">—</td>'
        color = "#0F6E56" if val >= 0 else "#A32D2D"
        txt = fmt_money(val) if kind == "money" else fmt_pct(val)
        if kind == "money" and val >= 0:
            txt = "+" + txt
        return f'<td style="text-align:right;color:{color}">{txt}</td>'

    holdings_rows = []
    for p in view:
        g_pct = p.get("Gain_Pct")
        action = p.get("Next_Action", "")
        act_urgent = action.split(" ")[0] in ("EXIT", "TRIM", "REDUCE", "REVIEW")
        alerts = "".join(f'<div style="font-size:10px;color:{"#791F1F" if "⛔" in a else "#633806"}">{esc(a)}</div>'
                         for a in p.get("Alerts") or [])
        edit_payload = esc(json.dumps({
            "Ticker":     p["Ticker"],
            "Shares":     p.get("Shares") or "",
            "Avg_Cost":   p.get("Avg_Cost") if p.get("Avg_Cost") is not None else "",
            "Entry_Date": p["Entry_Date"].isoformat() if p.get("Entry_Date") else "",
            "Strategy":   p.get("Strategy") or "watch",
            "Stop":       p.get("Stop") if p.get("Stop") is not None else "",
            "Target":     p.get("Target") if p.get("Target") is not None else "",
            "Notes":      p.get("Notes") or "",
        }))
        row_actions = (
            f'<div style="display:flex;gap:4px">'
            f'<button type="button" class="btn secondary" style="font-size:10px;padding:2px 8px" '
            f'''data-position='{edit_payload}' onclick="openPositionModal(JSON.parse(this.dataset.position))">Edit</button>'''
            f'<button type="button" class="btn secondary" style="font-size:10px;padding:2px 8px;color:#791F1F" '
            f'onclick="deletePosition(\'{esc(p["Ticker"])}\')">Delete</button>'
            f'</div>')
        # Mark prices that came from the broker snapshot instead of the scan —
        # they're as old as the last sync, and the user should see which.
        px = fmt_money(p.get("Price"))
        if p.get("Price_Source") == "broker":
            px = (f'<span title="From the last broker sync, not today\'s scan" '
                  f'style="border-bottom:1px dotted #898781">{px}</span>')
        holdings_rows.append(f"""
        <tr style="{'opacity:.6' if p['Is_Watch'] else ''}">
          <td><a href="/research/{p['Ticker']}.html"><b>{p['Ticker']}</b></a></td>
          <td>{esc(p['Strategy'])}</td>
          <td>{esc(p.get('Cap') or '—')}</td>
          <td style="text-align:right">{p['Shares']:g}</td>
          <td style="text-align:right">{fmt_money(p.get('Avg_Cost'))}</td>
          <td style="text-align:right">{px}</td>
          {_signed(p.get('Day_Dollars'))}
          {_signed(p.get('Day_Pct'), 'pct')}
          <td style="text-align:right">{fmt_money(p.get('Cost_Basis'))}</td>
          <td style="text-align:right;font-weight:600">{fmt_money(p.get('Value'))}</td>
          {_signed(p.get('Gain_Dollars'))}
          {_signed(g_pct, 'pct')}
          <td style="text-align:right">{f"{p['Alloc_Pct']:.1f}%" if p.get('Alloc_Pct') is not None else '—'}</td>
          <td style="text-align:right">{p['Days_Held'] if p.get('Days_Held') is not None else '—'}</td>
          <td style="text-align:right">{fmt_money(p.get('Stop'))}</td>
          <td style="text-align:right">{fmt_money(p.get('Target'))}</td>
          <td style="text-align:right">{fmt_money(p.get('Risk'))}</td>
          <td style="color:{'#791F1F' if act_urgent else '#0b0b0b'};font-weight:600;font-size:11px">{esc(action)}</td>
          <td>{alerts or '<span style="font-size:10px;color:#0F6E56">✓ clear</span>'}</td>
          <td>{row_actions}</td>
        </tr>""")

    foot = (f'<tfoot><tr style="border-top:2px solid #d9d7ce;font-weight:650">'
            f'<td colspan="8">TOTAL — {totals.get("positions")} positions</td>'
            f'<td style="text-align:right">{fmt_money(totals.get("total_cost"))}</td>'
            f'<td style="text-align:right">{fmt_money(totals.get("total_value"))}</td>'
            f'{_signed(totals.get("total_gain"))}{_signed(totals.get("total_gain_pct"), "pct")}'
            f'<td colspan="4"></td>'
            f'<td style="text-align:right">{fmt_money(totals.get("total_risk"))}</td>'
            f'<td colspan="3"></td></tr></tfoot>')

    # 20 columns overflow the card on a laptop — scroll the table, not the page
    holdings = f"""<div style="overflow-x:auto"><table><thead><tr>
      <th>Ticker</th><th>Strategy</th><th>Cap</th><th style="text-align:right">Shares</th>
      <th style="text-align:right">Avg Cost</th><th style="text-align:right">Price</th>
      <th style="text-align:right">Day $</th><th style="text-align:right">Day %</th>
      <th style="text-align:right">Cost Basis</th><th style="text-align:right">Market Value</th>
      <th style="text-align:right">Gain $</th><th style="text-align:right">Gain %</th>
      <th style="text-align:right">Alloc %</th><th style="text-align:right">Days Held</th>
      <th style="text-align:right">Stop</th><th style="text-align:right">Target</th>
      <th style="text-align:right">At Risk</th>
      <th>Next Action</th><th>Alerts</th><th></th></tr></thead>
      <tbody>{''.join(holdings_rows)}</tbody>{foot}</table></div>"""

    # The risk report renders from the last saved run so the page is instant;
    # the button below refreshes it as a background job (~1 min of fetching).
    from stockanalysis.core.portfolio_risk_scores import load_cached
    from stockanalysis.webapp import risk_view
    cached = load_cached()
    run_button = ('<form onsubmit="return submitJob(event, this)" style="display:inline">'
                  '<input type="hidden" name="action" value="portfolio_risk">'
                  '<button type="submit" class="btn" style="font-size:11px;padding:4px 10px">'
                  '🏛 Analyze Portfolio</button></form>')
    stamp = (f'<span style="font-size:10px;color:#898781;margin-right:10px">'
             f'last run {esc(cached.get("generated_at", "")[:16])}</span>' if cached else "")

    body = (
        card("Portfolio Summary", summary, "💼", right=stamp + run_button)
        + card("Allocation", alloc_html, "📊")
        + card("Holdings & Watchlist", holdings, "📋",
              right='<button type="button" class="btn" style="font-size:11px;padding:4px 10px" '
                    'onclick="openPositionModal(null)">+ Add Position</button>')
        + _options_card()
        + risk_view.render_risk_report(cached)
        + _position_modal()
    )
    extra_js = ("function onJobFinished(j) { setTimeout(() => location.reload(), 1200); }"
               + _position_js)
    return body, extra_js


def _position_modal() -> str:
    return """
    <dialog id="modal-position">
      <form class="modal-body" onsubmit="submitPosition(event, this); return false;">
        <h3 id="position-modal-title">Add position</h3>
        <input type="hidden" name="Original_Ticker" value="">
        <div style="display:flex;flex-direction:column;gap:10px">
          <input name="Ticker" placeholder="Ticker (e.g. NVDA)" required style="text-transform:uppercase">
          <div style="display:flex;gap:8px">
            <input name="Shares" type="number" step="any" placeholder="Shares (blank = watchlist)" style="flex:1">
            <input name="Avg_Cost" type="number" step="any" placeholder="Avg cost" style="flex:1">
          </div>
          <div style="display:flex;gap:8px">
            <input name="Entry_Date" type="date" style="flex:1">
            <select name="Strategy" style="flex:1">
              <option value="watch">watch</option>
              <option value="day">day</option>
              <option value="swing">swing</option>
              <option value="longterm">longterm</option>
            </select>
          </div>
          <div style="display:flex;gap:8px">
            <input name="Stop" type="number" step="any" placeholder="Stop" style="flex:1">
            <input name="Target" type="number" step="any" placeholder="Target" style="flex:1">
          </div>
          <input name="Notes" placeholder="Notes">
        </div>
        <div class="modal-actions">
          <button type="button" class="btn secondary" onclick="closeModal('modal-position')">Cancel</button>
          <button class="btn">Save</button>
        </div>
      </form>
    </dialog>"""


_position_js = r"""
function openPositionModal(pos) {
  const form = document.getElementById('modal-position').querySelector('form');
  form.reset();
  form.Original_Ticker.value = pos ? pos.Ticker : '';
  document.getElementById('position-modal-title').textContent = pos ? 'Edit ' + pos.Ticker : 'Add position';
  if (pos) {
    form.Ticker.value = pos.Ticker || '';
    form.Shares.value = pos.Shares || '';
    form.Avg_Cost.value = pos.Avg_Cost ?? '';
    form.Entry_Date.value = pos.Entry_Date || '';
    form.Strategy.value = pos.Strategy || 'watch';
    form.Stop.value = pos.Stop ?? '';
    form.Target.value = pos.Target ?? '';
    form.Notes.value = pos.Notes || '';
  }
  openModal('modal-position');
}
async function submitPosition(event, form) {
  event.preventDefault();
  const fd = new FormData(form);
  try {
    const res = await fetch('/api/portfolio/save', { method: 'POST', body: new URLSearchParams(fd) });
    const data = await res.json();
    if (data.ok) {
      toast(data.message || 'Saved', 'ok');
      closeModal('modal-position');
      setTimeout(() => location.reload(), 600);
    } else { toast(data.message || 'Save failed', 'err'); }
  } catch (e) { toast('Request failed: ' + e, 'err'); }
}
async function deletePosition(ticker) {
  if (!confirm('Remove ' + ticker + ' from the portfolio?')) return;
  try {
    const res = await fetch('/api/portfolio/delete', { method: 'POST', body: new URLSearchParams({ ticker }) });
    const data = await res.json();
    if (data.ok) {
      toast(data.message || 'Removed', 'ok');
      setTimeout(() => location.reload(), 600);
    } else { toast(data.message || 'Delete failed', 'err'); }
  } catch (e) { toast('Request failed: ' + e, 'err'); }
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# JOURNAL — AI-coached trade journal (plan/execution/psychology/checklist in,
# grades + coaching advice out). Storage and analytics math live in
# core/trading_journal.py; this section is presentation only.
# ─────────────────────────────────────────────────────────────────────────────

def _grade_status(grade: str | None) -> str:
    if not grade:
        return "muted"
    g = grade[0].upper()
    return {"A": "good", "B": "good", "C": "watch"}.get(g, "bad")


def journal_page() -> tuple[str, str]:
    from stockanalysis.core import trading_journal as journal

    trades = journal.load_trades()
    trades_sorted = sorted(
        trades, key=lambda t: (t.get("date") or "", t.get("time") or ""), reverse=True)

    metrics = journal.aggregate_metrics(trades)
    summary = _journal_summary_cards(metrics)

    analytics = ""
    if metrics.get("count"):
        analytics = (
            _journal_breakdown_card("Setup Performance", "🎯", journal.setup_performance(trades))
            + _journal_breakdown_card("Time of Day", "🕐", journal.time_of_day_performance(trades))
            + _journal_breakdown_card("Day of Week", "📅", journal.day_of_week_performance(trades))
            + _journal_breakdown_card("Higher-TF Trend", "📈", journal.trend_performance(trades))
            + _journal_emotion_card(journal.emotion_correlation(trades))
            + _journal_violation_card(journal.rule_violation_stats(trades))
            + _journal_monthly_card(journal.monthly_review(trades))
        )

    rows = "".join(_journal_row(t) for t in trades_sorted) or (
        f'<tr><td colspan="7">{empty("No trades logged yet — click + Log Trade to start.")}</td></tr>')
    log_table = f"""<table><thead><tr>
      <th>Date</th><th>Ticker</th><th>Setup</th><th style="text-align:right">R</th>
      <th style="text-align:right">Return %</th><th>AI Grade</th><th></th></tr></thead>
      <tbody>{rows}</tbody></table>"""

    body = (
        card("Performance Summary", summary, "📓",
            right='<button type="button" class="btn" style="font-size:11px;padding:4px 10px" '
                  'onclick="openJournalModal(null)">+ Log Trade</button>')
        + analytics
        + card("Trade Log", log_table, "📜")
        + _journal_modal()
    )
    return body, _journal_js


def _journal_summary_cards(m: dict) -> str:
    if not m.get("count"):
        return empty("No completed trades yet (needs both an actual entry and "
                     "exit) — log one below to start building analytics.")

    def stat(label, value, status=None):
        v = badge(str(value), status) if status else esc(value)
        return (f'<div><div style="font-size:11px;color:#898781">{esc(label)}</div>'
                f'<div style="font-size:20px;font-weight:650">{v}</div></div>')

    avg_r = m["avg_r"]
    r_status = "good" if avg_r >= 0 else "bad"
    dd = m["max_drawdown_r"]
    return f"""
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:16px">
      {stat("Trades", m["count"])}
      {stat("Win Rate", f'{m["win_rate"]}%')}
      {stat("Avg R", f'{avg_r:+.2f}R', r_status)}
      {stat("Expectancy", f'{m["expectancy"]:+.2f}R', r_status)}
      {stat("Profit Factor", m["profit_factor"] if m["profit_factor"] is not None else "—")}
      {stat("Max Drawdown", f'{dd:.2f}R', "bad" if dd < 0 else "muted")}
      {stat("Best Streak", f'{m["max_consecutive_wins"]}W / {m["max_consecutive_losses"]}L')}
    </div>"""


def _journal_breakdown_card(title: str, icon: str, buckets: list[dict]) -> str:
    if not buckets:
        return ""

    def row(b):
        color = "#0F6E56" if b["avg_r"] >= 0 else "#A32D2D"
        return (f'<tr><td>{esc(b["key"])}</td><td style="text-align:right">{b["count"]}</td>'
                f'<td style="text-align:right">{b["win_rate"]}%</td>'
                f'<td style="text-align:right;color:{color}">{b["avg_r"]:+.2f}R</td></tr>')

    table = (f'<table><thead><tr><th>{esc(title)}</th><th style="text-align:right">Trades</th>'
            f'<th style="text-align:right">Win %</th><th style="text-align:right">Avg R</th></tr></thead>'
            f'<tbody>{"".join(row(b) for b in buckets)}</tbody></table>')
    return card(title, table, icon)


def _journal_emotion_card(rows: list[dict]) -> str:
    if not rows:
        return ""

    def row(r):
        hi = r["avg_r_high"] if r["avg_r_high"] is not None else "—"
        lo = r["avg_r_low"] if r["avg_r_low"] is not None else "—"
        corr = r["correlation_with_r"] if r["correlation_with_r"] is not None else "—"
        return (f'<tr><td style="text-transform:capitalize">{esc(r["dimension"])}</td>'
                f'<td style="text-align:right">{hi}</td><td style="text-align:right">{lo}</td>'
                f'<td style="text-align:right">{corr}</td></tr>')

    table = (f'<table><thead><tr><th>Dimension</th><th style="text-align:right">Avg R (score ≥7)</th>'
            f'<th style="text-align:right">Avg R (score ≤4)</th><th style="text-align:right">Corr. w/ R</th></tr></thead>'
            f'<tbody>{"".join(row(r) for r in rows)}</tbody></table>')
    return card("Emotion Correlation", table, "🧠")


def _journal_violation_card(rows: list[dict]) -> str:
    if not rows:
        return ""

    def row(r):
        absent = f'{r["avg_r_when_absent"]:+.2f}R' if r["avg_r_when_absent"] is not None else "—"
        return (f'<tr><td>{esc(r["violation"])}</td><td style="text-align:right">{r["count"]}</td>'
                f'<td style="text-align:right;color:#A32D2D">{r["avg_r_when_present"]:+.2f}R</td>'
                f'<td style="text-align:right">{absent}</td></tr>')

    table = (f'<table><thead><tr><th>Violation</th><th style="text-align:right">Times</th>'
            f'<th style="text-align:right">Avg R when present</th>'
            f'<th style="text-align:right">Avg R when absent</th></tr></thead>'
            f'<tbody>{"".join(row(r) for r in rows)}</tbody></table>')
    return card("Rule Violations & Cost", table, "⚠️")


def _journal_monthly_card(rows: list[dict]) -> str:
    if not rows:
        return ""

    def row(m):
        win = f'{m["win_rate"]}%' if m["win_rate"] is not None else "—"
        avg_r = f'{m["avg_r"]:+.2f}R' if m["avg_r"] is not None else "—"
        return (f'<tr><td>{esc(m["month"])}</td><td style="text-align:right">{m["count"]}</td>'
                f'<td style="text-align:right">{win}</td><td style="text-align:right">{avg_r}</td>'
                f'<td>{esc(m.get("best_setup") or "—")}</td>'
                f'<td>{esc(m.get("top_recurring_mistake") or "—")}</td></tr>')

    table = (f'<table><thead><tr><th>Month</th><th style="text-align:right">Trades</th>'
            f'<th style="text-align:right">Win %</th><th style="text-align:right">Avg R</th>'
            f'<th>Best Setup</th><th>Top Recurring Mistake</th></tr></thead>'
            f'<tbody>{"".join(row(m) for m in rows)}</tbody></table>')
    return card("Monthly Review", table, "🗓️")


def _journal_row(t: dict) -> str:
    from stockanalysis.core import trading_journal as journal
    result = t.get("trade_result", {})
    r = result.get("r_multiple")
    r_html = f'<span style="color:{"#0F6E56" if r >= 0 else "#A32D2D"}">{r:+.2f}R</span>' if r is not None else "—"
    ret = result.get("return_pct")
    ret_html = fmt_pct(ret) if ret is not None else "—"
    ai = t.get("ai_feedback")
    grade_html = badge(ai["overall_grade"], _grade_status(ai.get("overall_grade"))) if ai else empty("not reviewed")
    trade_id = esc(t["id"])
    review_btn = "" if ai else (
        f'<form style="display:inline" onsubmit="submitJob(event, this, null); return false;">'
        f'<input type="hidden" name="action" value="journal_review">'
        f'<input type="hidden" name="trade_id" value="{trade_id}">'
        f'<button class="btn secondary" style="font-size:10px;padding:2px 8px">AI Review</button></form>')
    edit_payload = esc(json.dumps(journal.trade_to_form_dict(t)))
    edit_btn = (f'<button type="button" class="btn secondary" style="font-size:10px;padding:2px 8px" '
               f'''data-trade='{edit_payload}' onclick="openJournalModal(JSON.parse(this.dataset.trade))">Edit</button>''')
    detail_id = f"jd-{trade_id}"
    return f"""
    <tr>
      <td>{esc(t.get('date') or '—')} {esc(t.get('time') or '')}</td>
      <td><b>{esc(t.get('ticker'))}</b> <span style="font-size:10px;color:#898781">{esc(t.get('direction'))}</span></td>
      <td>{esc(t.get('trade_plan', {}).get('setup_name') or '—')}</td>
      <td style="text-align:right">{r_html}</td>
      <td style="text-align:right">{ret_html}</td>
      <td>{grade_html}</td>
      <td style="display:flex;gap:4px">
        <button type="button" class="btn secondary" style="font-size:10px;padding:2px 8px" onclick="toggleJournalDetail('{detail_id}')">Details</button>
        {edit_btn}
        {review_btn}
        <button type="button" class="btn secondary" style="font-size:10px;padding:2px 8px;color:#791F1F" onclick="deleteJournalTrade('{trade_id}')">Delete</button>
      </td>
    </tr>
    <tr id="{detail_id}" style="display:none"><td colspan="7">{_journal_detail_html(t)}</td></tr>"""


def _journal_detail_html(t: dict) -> str:
    plan, ctx = t.get("trade_plan", {}), t.get("market_context", {})
    execu = t.get("execution", {})
    psych, chk = t.get("psychology", {}), t.get("rule_checklist", {})
    review = t.get("post_trade_review", {})
    ai = t.get("ai_feedback")

    def kv(label, value):
        v = esc(value) if value not in (None, "") else "—"
        return f'<div style="margin-bottom:3px"><span style="color:#898781">{esc(label)}:</span> {v}</div>'

    plan_html = "".join([
        kv("Why", plan.get("why")),
        kv("Setup", f"{plan.get('setup_name') or '—'} ({plan.get('setup_grade') or '—'})"),
        kv("HTF trend", plan.get("higher_tf_trend")), kv("LTF trigger", plan.get("lower_tf_trigger")),
        kv("Entry/Stop/T1/T2", f"{plan.get('entry')} / {plan.get('stop')} / {plan.get('target1')} / {plan.get('target2')}"),
        kv("Risk % / R:R", f"{plan.get('risk_pct')} / {plan.get('risk_reward')}"),
        kv("Max loss / gain", f"{plan.get('max_loss_accepted')} / {plan.get('max_gain_expected')}"),
        kv("Context notes", ctx.get("notes")),
    ])
    exec_flags = ", ".join(k.replace("_", " ") for k in
                          ("deviated_from_plan", "hesitation", "late_entry", "early_exit",
                           "fomo", "revenge_trade", "overtrading") if execu.get(k)) or "none"
    exec_html = "".join([
        kv("Actual entry/exit", f"{execu.get('actual_entry')} / {execu.get('actual_exit')}"),
        kv("Position size", execu.get("position_size")), kv("Scaling", execu.get("scaling")),
        kv("Partial exits", execu.get("partial_exits")),
        kv("Duration (min)", execu.get("duration_minutes")),
        kv("MFE / MAE", f"{execu.get('mfe')} / {execu.get('mae')}"),
        kv("Flags", exec_flags),
    ])
    psych_html = "".join([
        kv("Before", psych.get("before")), kv("During", psych.get("during")), kv("After", psych.get("after")),
        kv("Stress/Confidence/Discipline/Patience/Focus",
           f"{psych.get('stress')}/{psych.get('confidence')}/{psych.get('discipline')}/{psych.get('patience')}/{psych.get('focus')}"),
        kv("Fear/Greed/Hope/Regret", f"{psych.get('fear')}/{psych.get('greed')}/{psych.get('hope')}/{psych.get('regret')}"),
    ])
    chk_flags = ", ".join(k.replace("_", " ") for k in
                          ("impulsive", "stop_moved", "target_changed", "averaged_down", "emotional")
                          if chk.get(k)) or "none"
    chk_html = kv("Rules broken", chk.get("rules_broken")) + kv("Flags", chk_flags)
    review_html = "".join([
        kv("What worked", review.get("what_worked")), kv("What failed", review.get("what_failed")),
        kv("Repeat", review.get("repeat")), kv("Never again", review.get("never_again")),
    ])

    ai_html = empty("Not yet reviewed by the AI coach.")
    if ai:
        score_fields = [
            ("Execution", "execution_score"), ("Planning", "planning_score"),
            ("Psychology", "psychology_score"), ("Risk Mgmt", "risk_management_score"),
            ("Setup Quality", "setup_quality_score"), ("Edge", "edge_score"),
        ]
        scores = "".join(
            f'<div><div style="font-size:10px;color:#898781">{esc(label)}</div>'
            f'<div style="font-size:15px;font-weight:650">{esc(ai.get(key, "—"))}</div></div>'
            for label, key in score_fields)
        mistakes = "".join(f"<li>{esc(m)}</li>" for m in ai.get("top_mistakes") or []) or "<li>—</li>"
        strengths = "".join(f"<li>{esc(s)}</li>" for s in ai.get("top_strengths") or []) or "<li>—</li>"
        suggestions = "".join(f"<li>{esc(s)}</li>" for s in ai.get("improvement_suggestions") or []) or "<li>—</li>"
        ai_html = (
            f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:10px;margin-bottom:10px">{scores}</div>'
            f'<div style="margin-bottom:6px"><b>Top mistakes</b><ul style="margin:4px 0 0 18px">{mistakes}</ul></div>'
            f'<div style="margin-bottom:6px"><b>Top strengths</b><ul style="margin:4px 0 0 18px">{strengths}</ul></div>'
            f'<div style="margin-bottom:6px"><b>Improvement suggestions</b><ul style="margin:4px 0 0 18px">{suggestions}</ul></div>'
            f'<div style="margin-bottom:6px"><b>Confidence in similar setup:</b> {esc(ai.get("confidence_in_future_similar_setup"))}</div>'
            f'<div style="font-style:italic">“{esc(ai.get("coaching_advice"))}”</div>'
        )

    return f"""
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;font-size:12px;padding:10px 4px">
      <div><b>Trade Plan</b>{plan_html}</div>
      <div><b>Execution</b>{exec_html}</div>
      <div><b>Psychology</b>{psych_html}</div>
      <div><b>Checklist</b>{chk_html}<b style="display:block;margin-top:8px">Lessons</b>{review_html}</div>
    </div>
    <div style="border-top:0.5px solid #e1e0d9;margin-top:8px;padding-top:10px;font-size:12px">
      <b>AI Coach Review</b><div style="margin-top:6px">{ai_html}</div>
    </div>"""


def _journal_modal() -> str:
    return """
    <dialog id="modal-journal" style="max-width:640px;width:92vw">
      <form class="modal-body" onsubmit="submitJournalTrade(event, this); return false;" style="max-height:80vh;overflow-y:auto">
        <h3 id="journal-modal-title">Log a trade</h3>
        <input type="hidden" name="trade_id" value="">
        <div style="display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;gap:8px">
            <input name="ticker" placeholder="Ticker" required style="flex:1;text-transform:uppercase">
            <select name="direction" style="flex:1">
              <option>Long</option><option>Short</option>
            </select>
          </div>
          <div style="display:flex;gap:8px">
            <input name="date" type="date" style="flex:1">
            <input name="time" type="time" style="flex:1">
          </div>
          <div style="display:flex;gap:8px">
            <input name="market" placeholder="Market (stocks/options/futures)" style="flex:1">
            <input name="sector" placeholder="Sector" style="flex:1">
          </div>

          <details open>
            <summary style="cursor:pointer;font-weight:600;font-size:12px;margin:6px 0">Trade Plan</summary>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
              <textarea name="plan_why" placeholder="Why was this trade taken?" rows="2"></textarea>
              <div style="display:flex;gap:8px">
                <input name="plan_setup_name" placeholder="Setup (e.g. EMA Pullback)" style="flex:2">
                <select name="plan_setup_grade" style="flex:1">
                  <option value="">Setup grade</option>
                  <option>A+</option><option>A</option><option>B</option><option>C</option>
                </select>
              </div>
              <div style="display:flex;gap:8px">
                <input name="plan_htf_trend" placeholder="Higher timeframe trend" style="flex:1">
                <input name="plan_ltf_trigger" placeholder="Lower timeframe trigger" style="flex:1">
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <input name="plan_entry" type="number" step="any" placeholder="Entry" style="flex:1;min-width:90px">
                <input name="plan_stop" type="number" step="any" placeholder="Stop" style="flex:1;min-width:90px">
                <input name="plan_target1" type="number" step="any" placeholder="Target 1" style="flex:1;min-width:90px">
                <input name="plan_target2" type="number" step="any" placeholder="Target 2" style="flex:1;min-width:90px">
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <input name="plan_risk_pct" type="number" step="any" placeholder="Risk %" style="flex:1;min-width:90px">
                <input name="plan_expected_reward" type="number" step="any" placeholder="Expected reward" style="flex:1;min-width:110px">
                <input name="plan_max_loss" type="number" step="any" placeholder="Max loss accepted" style="flex:1;min-width:110px">
                <input name="plan_max_gain" type="number" step="any" placeholder="Max gain expected" style="flex:1;min-width:110px">
              </div>
              <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:12px">
                <label><input type="checkbox" name="ctx_earnings"> Earnings considered</label>
                <label><input type="checkbox" name="ctx_fed"> Fed considered</label>
                <label><input type="checkbox" name="ctx_vix"> VIX considered</label>
                <label><input type="checkbox" name="ctx_yields"> Yields considered</label>
                <label><input type="checkbox" name="ctx_news"> News considered</label>
              </div>
              <input name="ctx_notes" placeholder="Market context notes">
            </div>
          </details>

          <details>
            <summary style="cursor:pointer;font-weight:600;font-size:12px;margin:6px 0">Execution</summary>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <input name="exec_actual_entry" type="number" step="any" placeholder="Actual entry" style="flex:1;min-width:90px">
                <input name="exec_actual_exit" type="number" step="any" placeholder="Actual exit" style="flex:1;min-width:90px">
                <input name="exec_duration_minutes" type="number" step="any" placeholder="Duration (min)" style="flex:1;min-width:110px">
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <input name="exec_mfe" type="number" step="any" placeholder="Max favorable excursion" style="flex:1;min-width:140px">
                <input name="exec_mae" type="number" step="any" placeholder="Max adverse excursion" style="flex:1;min-width:140px">
              </div>
              <input name="exec_position_size" placeholder="Position sizing">
              <input name="exec_scaling" placeholder="Scaling (adds/trims)">
              <input name="exec_partial_exits" placeholder="Partial exits">
              <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:12px">
                <label><input type="checkbox" name="exec_deviated_from_plan"> Deviated from plan</label>
                <label><input type="checkbox" name="exec_hesitation"> Hesitation</label>
                <label><input type="checkbox" name="exec_late_entry"> Late entry</label>
                <label><input type="checkbox" name="exec_early_exit"> Early exit</label>
                <label><input type="checkbox" name="exec_fomo"> FOMO</label>
                <label><input type="checkbox" name="exec_revenge"> Revenge trade</label>
                <label><input type="checkbox" name="exec_overtrading"> Overtrading</label>
              </div>
            </div>
          </details>

          <details>
            <summary style="cursor:pointer;font-weight:600;font-size:12px;margin:6px 0">Psychology</summary>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
              <input name="psych_before" placeholder="Emotional state before">
              <input name="psych_during" placeholder="Emotional state during">
              <input name="psych_after" placeholder="Emotional state after">
              <div style="display:flex;gap:6px;flex-wrap:wrap">
                <input name="psych_stress" type="number" min="1" max="10" placeholder="Stress 1-10" style="flex:1;min-width:80px">
                <input name="psych_confidence" type="number" min="1" max="10" placeholder="Confidence 1-10" style="flex:1;min-width:80px">
                <input name="psych_discipline" type="number" min="1" max="10" placeholder="Discipline 1-10" style="flex:1;min-width:80px">
                <input name="psych_patience" type="number" min="1" max="10" placeholder="Patience 1-10" style="flex:1;min-width:80px">
                <input name="psych_focus" type="number" min="1" max="10" placeholder="Focus 1-10" style="flex:1;min-width:80px">
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <input name="psych_fear" placeholder="Fear" style="flex:1;min-width:90px">
                <input name="psych_greed" placeholder="Greed" style="flex:1;min-width:90px">
                <input name="psych_hope" placeholder="Hope" style="flex:1;min-width:90px">
                <input name="psych_regret" placeholder="Regret" style="flex:1;min-width:90px">
              </div>
              <input name="psych_fomo_note" placeholder="FOMO note">
            </div>
          </details>

          <details>
            <summary style="cursor:pointer;font-weight:600;font-size:12px;margin:6px 0">Checklist</summary>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
              <input name="chk_rules_broken" placeholder="Which rules were broken, if any">
              <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:12px">
                <label><input type="checkbox" name="chk_impulsive"> Impulsive decision</label>
                <label><input type="checkbox" name="chk_stop_moved"> Stop moved</label>
                <label><input type="checkbox" name="chk_target_changed"> Target changed</label>
                <label><input type="checkbox" name="chk_averaged_down"> Averaged down</label>
                <label><input type="checkbox" name="chk_emotional"> Emotional decision</label>
              </div>
            </div>
          </details>

          <details>
            <summary style="cursor:pointer;font-weight:600;font-size:12px;margin:6px 0">Lessons</summary>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
              <textarea name="rev_worked" placeholder="What worked?" rows="2"></textarea>
              <textarea name="rev_failed" placeholder="What failed?" rows="2"></textarea>
              <textarea name="rev_repeat" placeholder="What should be repeated?" rows="2"></textarea>
              <textarea name="rev_never_again" placeholder="What should never happen again?" rows="2"></textarea>
            </div>
          </details>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn secondary" onclick="closeModal('modal-journal')">Cancel</button>
          <button class="btn">Save trade</button>
        </div>
      </form>
    </dialog>"""


_journal_js = r"""
function openJournalModal(payload) {
  const dialog = document.getElementById('modal-journal');
  const form = dialog.querySelector('form');
  form.reset();
  // Editing an existing trade: expand every section so prefilled values in
  // collapsed <details> (e.g. Psychology, Checklist) are actually visible
  // instead of looking like the edit silently did nothing.
  form.querySelectorAll('details').forEach(d => { d.open = !!payload; });
  document.getElementById('journal-modal-title').textContent = payload ? 'Edit trade' : 'Log a trade';
  if (payload) {
    for (const [key, value] of Object.entries(payload)) {
      const el = form.elements[key];
      if (!el) continue;
      if (el.type === 'checkbox') el.checked = !!value;
      else el.value = (value === null || value === undefined) ? '' : value;
    }
  }
  openModal('modal-journal');
}
async function submitJournalTrade(event, form) {
  event.preventDefault();
  const fd = new FormData(form);
  try {
    const res = await fetch('/api/journal/save', { method: 'POST', body: new URLSearchParams(fd) });
    const data = await res.json();
    if (data.ok) {
      toast(data.message || 'Trade logged', 'ok');
      closeModal('modal-journal');
      setTimeout(() => location.reload(), 600);
    } else { toast(data.message || 'Save failed', 'err'); }
  } catch (e) { toast('Request failed: ' + e, 'err'); }
}
async function deleteJournalTrade(id) {
  if (!confirm('Delete this trade from the journal?')) return;
  try {
    const res = await fetch('/api/journal/delete', { method: 'POST', body: new URLSearchParams({ trade_id: id }) });
    const data = await res.json();
    if (data.ok) { toast(data.message || 'Removed', 'ok'); setTimeout(() => location.reload(), 500); }
    else { toast(data.message || 'Delete failed', 'err'); }
  } catch (e) { toast('Request failed: ' + e, 'err'); }
}
function toggleJournalDetail(id) {
  const row = document.getElementById(id);
  row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
}
function onJobFinished(j) {
  if (j.kind === 'journal_review') setTimeout(() => location.reload(), 1200);
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# ALERTS — the "personal trading assistant" feed: active watchlist
# conditions (core.watchlist_alerts, deduped/prioritized by core.alerts) and
# the latest Pre-Market Brief (core.premarket_brief). Both run on a
# schedule (see scheduler.py) and can be triggered on demand from here.
# ─────────────────────────────────────────────────────────────────────────────

_PRIORITY_STATUS = {"CRITICAL": "bad", "HIGH": "watch", "MEDIUM": "info", "LOW": "muted"}


def alerts_page() -> tuple[str, str]:
    from stockanalysis.core import alerts as alerts_mod
    from stockanalysis.core.premarket_brief import load_latest_brief

    # Newest first, minus LOW alerts older than LOW_TTL_HOURS (24h) — standing
    # low-grade conditions stop cluttering the feed after a day. The Sort
    # dropdown below re-orders client-side; this order is the fallback for a
    # page that loads with JS disabled.
    active_alerts = alerts_mod.active_display_alerts(sort="newest")

    def _toolbar_form(action: str, label: str, primary: bool = False) -> str:
        btn_class = "btn" if primary else "btn secondary"
        return (f'<form style="display:inline" onsubmit="submitJob(event, this, null); return false;">'
               f'<input type="hidden" name="action" value="{action}">'
               f'<button class="{btn_class}" style="font-size:11px;padding:4px 10px">{esc(label)}</button></form> ')

    toolbar = (
        _toolbar_form("watchlist_scan", "Scan Watchlist Now")
        + _toolbar_form("longterm_entry_scan", "Check Entry Levels Now")
        + _toolbar_form("news_scan", "Scan News Now")
        + _toolbar_form("earnings_scan", "Check Earnings Now")
        # runs the scheduler's day-session init (movers merge) on demand and
        # drops a feed alert with the merged universe
        + (f'<form style="display:inline" onsubmit="submitJob(event, this, null); return false;">'
           f'<input type="hidden" name="action" value="run_cron">'
           f'<input type="hidden" name="job_name" value="job_day_session_init">'
           f'<button class="btn secondary" style="font-size:11px;padding:4px 10px">'
           f'Init Day Universe Now</button></form> ')
        + _toolbar_form("premarket_brief", "Generate Brief Now", primary=True)
    )

    # A mute you can only detect by NOT receiving mail is a bad mute. Said
    # here, next to the alerts it applies to, rather than only in a comment.
    allowed = alerts_mod.notify_categories()
    if allowed is None:
        mute_note = ""
    else:
        kept = (", ".join(sorted(allowed)) if allowed else "none")
        mute_note = (
            f'<div style="font-size:11px;padding:9px 13px;border-radius:9px;'
            f'margin-bottom:12px;background:#E6F1FB;color:#0C447C;'
            f'border:0.5px solid #cfe0f2">🔕 <b>Email/Telegram muted</b> for '
            f'the alert digest — pushing categories: <b>{esc(kept)}</b>. '
            f'Alerts below are unaffected: they still fire, dedup and log. '
            f'The Pre-Market Brief, Market Movers and “Longterm swing trades” '
            f'emails have their own channels and still send. Set '
            f'<code>ALERT_NOTIFY_CATEGORIES=all</code> to unmute.</div>')

    if not active_alerts:
        active_html = empty("No active alerts — conditions are being checked every 10 minutes "
                            "during market hours (Scanner runs the same checks on demand above).")
    else:
        prio_counts = {}
        for a in active_alerts:
            prio_counts[a["priority"]] = prio_counts.get(a["priority"], 0) + 1
        prio_opts = f'<option value="">All ({len(active_alerts)})</option>' + "".join(
            f'<option value="{p}">{p} ({prio_counts[p]})</option>'
            for p in ("CRITICAL", "HIGH", "MEDIUM", "LOW") if p in prio_counts)
        active_html = (
            f'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px">'
            f'<label style="font-size:11px;color:#898781">Sort</label>'
            f'<select id="alert-sort" onchange="sortActiveAlerts()" style="font-size:12px">'
            f'<option value="newest">Newest first</option>'
            f'<option value="oldest">Oldest first</option>'
            f'<option value="priority">Priority</option></select>'
            f'<label style="font-size:11px;color:#898781">Priority</label>'
            f'<select id="alert-prio-filter" onchange="onAlertPrioChange()" '
            f'style="font-size:12px">{prio_opts}</select>'
            f'<label style="font-size:11px;color:#898781">Category</label>'
            f'<select id="alert-cat-filter" onchange="filterActiveAlerts()" '
            f'style="font-size:12px"></select>'
            f'<span id="alert-filter-count" style="font-size:11px;color:#898781"></span></div>'
            f'<div id="active-alerts-box" style="max-height:420px;overflow-y:auto;'
            f'padding-right:6px;border:0.5px solid #f1efea;border-radius:8px;padding:8px">'
            + "".join(_alert_card(a) for a in active_alerts) + '</div>')

    log_rows = alerts_mod.load_log(100)
    log_json = json.dumps([{
        "created_at": a.get("created_at"), "priority": a.get("priority"),
        "category": a.get("category"), "ticker": a.get("ticker"),
        "headline": a.get("headline"), "confidence": a.get("confidence"),
    } for a in log_rows])
    log_html = (empty("No alerts have fired yet.") if not log_rows else (
        '<div style="display:flex;gap:10px;align-items:center;margin-bottom:8px">'
        '<label style="font-size:11px;color:#898781">Show</label>'
        '<select id="log-filter" onchange="renderAlertLog()" '
        'style="font-size:12px;max-width:420px"></select>'
        '<span id="log-filter-count" style="font-size:11px;color:#898781"></span></div>'
        '<div id="alert-log-root" style="overflow-x:auto;max-height:380px;overflow-y:auto;'
        'border:0.5px solid #f1efea;border-radius:8px;padding:4px 8px"></div>'))

    brief = load_latest_brief()
    brief_html = (
        f'<div style="max-height:420px;overflow-y:auto;border:0.5px solid #f1efea;'
        f'border-radius:8px;padding:10px 14px">{_premarket_brief_html(brief)}</div>')

    body = (
        card("Active Alerts", mute_note + active_html, "🔔", right=toolbar)
        + card("Latest Pre-Market Brief", brief_html, "📰")
        + card("Recent Alert Log", log_html, "🗒️")
    )
    extra_js = (
        "function onJobFinished(j) { if (['watchlist_scan', 'news_scan', 'earnings_scan', "
        "'longterm_entry_scan', 'premarket_brief'].includes(j.kind) "
        "|| j.kind.startsWith('cron:')) "
        "setTimeout(() => location.reload(), 1200); }\n"
        "const ALERT_LOG_ROWS = " + log_json + ";\n"
        + """
    const ALERT_CAT_LABELS = {earnings: 'Earnings', news: 'News Catalyst',
      watchlist: 'Technical', put_setup: 'Put Setup', call_setup: 'Call Setup',
      a_plus_setup: 'A+ Setup', longterm_entry: 'Entry Reached',
      other: 'Other'};
    function alertCards() {
      return Array.from(document.querySelectorAll('#active-alerts-box .alert-card'));
    }
    // Category options are recomputed for the chosen priority, so e.g. with
    // HIGH selected the dropdown reads "Earnings (3) · News Catalyst (12) ·
    // Put Setup (2)" — only sub-categories that actually exist at that tier.
    function renderAlertCatOptions() {
      const catSel = document.getElementById('alert-cat-filter');
      if (!catSel) return;
      const prio = document.getElementById('alert-prio-filter').value;
      const pool = alertCards().filter(el => !prio || el.dataset.priority === prio);
      const counts = {};
      pool.forEach(el => { counts[el.dataset.category] = (counts[el.dataset.category] || 0) + 1; });
      const prev = catSel.value;
      catSel.innerHTML = `<option value="">All (${pool.length})</option>` +
        Object.keys(counts).sort().map(c =>
          `<option value="${c}">${ALERT_CAT_LABELS[c] || c} (${counts[c]})</option>`).join('');
      if (counts[prev] != null) catSel.value = prev;   // keep selection if still present
    }
    function onAlertPrioChange() {
      renderAlertCatOptions();
      filterActiveAlerts();
    }
    // Re-orders the cards in place. data-since is an ISO timestamp, which
    // sorts correctly as a string, so no Date parsing is needed; cards with
    // no timestamp sort last in either direction rather than jumping to the
    // top of "newest".
    const ALERT_PRIO_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3};
    function sortActiveAlerts() {
      const box = document.getElementById('active-alerts-box');
      const sel = document.getElementById('alert-sort');
      if (!box || !sel) return;
      const mode = sel.value;
      const cards = alertCards();
      cards.sort((a, b) => {
        const as = a.dataset.since || '', bs = b.dataset.since || '';
        if (mode === 'priority') {
          const ap = ALERT_PRIO_ORDER[a.dataset.priority] ?? 9;
          const bp = ALERT_PRIO_ORDER[b.dataset.priority] ?? 9;
          if (ap !== bp) return ap - bp;
          return bs.localeCompare(as);             // newest first inside a tier
        }
        if (!as && !bs) return 0;
        if (!as) return 1;
        if (!bs) return -1;
        return mode === 'oldest' ? as.localeCompare(bs) : bs.localeCompare(as);
      });
      cards.forEach(el => box.appendChild(el));
      box.scrollTop = 0;
    }
    function filterActiveAlerts() {
      const prioSel = document.getElementById('alert-prio-filter');
      if (!prioSel) return;
      const prio = prioSel.value;
      const cat = document.getElementById('alert-cat-filter').value;
      let shown = 0;
      alertCards().forEach(el => {
        const show = (!prio || el.dataset.priority === prio)
                  && (!cat || el.dataset.category === cat);
        el.style.display = show ? '' : 'none';
        if (show) shown++;
      });
      document.getElementById('alert-filter-count').textContent =
        (prio || cat) ? shown + ' shown' : '';
      document.getElementById('active-alerts-box').scrollTop = 0;
    }
    document.addEventListener('DOMContentLoaded', renderAlertCatOptions);
    """
        + """
    // Sortable alert log. Priority sorts by rank (CRITICAL first), not
    // alphabetically; default order = newest first (same as the stored log).
    const LOG_PRIORITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3};
    const LOG_PRIORITY_BADGE = {CRITICAL: ['#FCEBEB', '#791F1F'], HIGH: ['#FAEEDA', '#633806'],
                                MEDIUM: ['#E6F1FB', '#0C447C'], LOW: ['#F1EFE8', '#444441']};
    let logSortKey = 'created_at', logSortDir = -1;
    const escLog = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
    // Hierarchical log filter: priority groups, each holding "All <PRIORITY>"
    // plus every distinct ticker+headline at that tier as a sub-entry.
    let LOG_FILTERS = [null];
    const logRowLabel = r => (r.ticker ? r.ticker + ' ' : '') + (r.headline || '');
    function buildLogFilterOptions() {
      const sel = document.getElementById('log-filter');
      if (!sel) return;
      const byPrio = {};
      ALERT_LOG_ROWS.forEach(r => {
        const p = r.priority || '—';
        (byPrio[p] = byPrio[p] || {});
        const h = logRowLabel(r);
        byPrio[p][h] = (byPrio[p][h] || 0) + 1;
      });
      LOG_FILTERS = [null];
      let html = `<option value="0">All (${ALERT_LOG_ROWS.length})</option>`;
      ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].filter(p => byPrio[p]).forEach(p => {
        const total = Object.values(byPrio[p]).reduce((a, b) => a + b, 0);
        html += `<optgroup label="${p} (${total})">`;
        LOG_FILTERS.push({priority: p});
        html += `<option value="${LOG_FILTERS.length - 1}">All ${p} (${total})</option>`;
        Object.keys(byPrio[p]).sort().forEach(h => {
          LOG_FILTERS.push({priority: p, headline: h});
          const n = byPrio[p][h];
          html += `<option value="${LOG_FILTERS.length - 1}">· ${escLog(h).slice(0, 70)}${n > 1 ? ' ×' + n : ''}</option>`;
        });
        html += '</optgroup>';
      });
      sel.innerHTML = html;
    }
    function logSetSort(key) {
      logSortDir = (logSortKey === key) ? -logSortDir : (key === 'created_at' ? -1 : 1);
      logSortKey = key;
      renderAlertLog();
    }
    function renderAlertLog() {
      const root = document.getElementById('alert-log-root');
      if (!root) return;
      const fSel = document.getElementById('log-filter');
      const f = LOG_FILTERS[+(fSel ? fSel.value : 0)] || null;
      const filtered = ALERT_LOG_ROWS.filter(r => !f ||
        (r.priority === f.priority && (!f.headline || logRowLabel(r) === f.headline)));
      const cnt = document.getElementById('log-filter-count');
      if (cnt) cnt.textContent = f ? filtered.length + ' shown' : '';
      const rows = [...filtered].sort((a, b) => {
        let av = a[logSortKey], bv = b[logSortKey];
        if (logSortKey === 'priority') {
          av = LOG_PRIORITY_ORDER[av] ?? 9; bv = LOG_PRIORITY_ORDER[bv] ?? 9;
        }
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        return (av > bv ? 1 : av < bv ? -1 : 0) * logSortDir;
      });
      const arrow = k => logSortKey === k ? (logSortDir === 1 ? ' ▲' : ' ▼') : '';
      const th = (k, label, right) =>
        `<th style="cursor:pointer;white-space:nowrap${right ? ';text-align:right' : ''}"
             onclick="logSetSort('${k}')">${label}${arrow(k)}</th>`;
      root.innerHTML = `<table><thead><tr>
        ${th('created_at', 'When')}${th('priority', 'Priority')}${th('category', 'Category')}
        ${th('ticker', 'Ticker')}${th('headline', 'Headline')}${th('confidence', 'Confidence', true)}
        </tr></thead><tbody>${rows.map(a => {
          const [bg, fg] = LOG_PRIORITY_BADGE[a.priority] || ['#F1EFE8', '#444441'];
          return `<tr><td style="white-space:nowrap">${escLog(a.created_at) || '—'}</td>
            <td><span style="background:${bg};color:${fg};font-size:10px;font-weight:700;
                 padding:2px 8px;border-radius:9px">${escLog(a.priority)}</span></td>
            <td>${escLog(a.category) || '—'}</td>
            <td><b>${escLog(a.ticker) || '—'}</b></td>
            <td>${escLog(a.headline)}</td>
            <td style="text-align:right">${a.confidence != null ? a.confidence + '%' : '—'}</td></tr>`;
        }).join('')}</tbody></table>`;
    }
    document.addEventListener('DOMContentLoaded', () => {
      buildLogFilterOptions();
      renderAlertLog();
    });
    """)
    return body, extra_js


def _alert_age(since: str | None) -> str:
    """Relative age for the feed ("just now", "3h ago", "2d ago").

    The absolute timestamp stays on the card too — relative alone is useless
    when deciding whether an alert predates a position, and absolute alone
    makes you do date arithmetic to see what's fresh."""
    if not since:
        return ""
    try:
        delta = datetime.now() - datetime.fromisoformat(since)
    except (ValueError, TypeError):
        return ""
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    if mins < 60 * 24:
        return f"{mins // 60}h ago"
    return f"{mins // (60 * 24)}d ago"


def _alert_card(a: dict) -> str:
    color = {"CRITICAL": "#791F1F", "HIGH": "#8a6d1a", "MEDIUM": "#185FA5", "LOW": "#898781"}.get(a["priority"], "#898781")
    since = a.get("since") or a.get("created_at") or ""
    age = _alert_age(since)
    age_html = (f'<span style="margin-left:auto;font-size:10px;color:#898781;'
                f'white-space:nowrap" title="{esc(since)}">{esc(age)}</span>'
                if age else "")
    return f"""
    <div class="alert-card" data-priority="{esc(a["priority"])}" data-category="{esc(a.get("category") or "other")}"
         data-since="{esc(since)}"
         style="border-left:3px solid {color};padding:8px 14px;margin-bottom:10px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:3px">
        {badge(a["priority"], _PRIORITY_STATUS.get(a["priority"], "muted"))}
        <span style="font-size:9px;color:#898781;text-transform:uppercase;letter-spacing:.3px">{esc(a.get("category") or "")}</span>
        <b style="font-size:13px">{(f'<a href="/research?ticker={esc(a["ticker"])}&cols=all" '
                                    f'title="Open in Research Library with all columns" '
                                    f'style="color:inherit">{esc(a["ticker"])}</a> ')
                                  if a.get("ticker") else ''}{esc(a["headline"])}</b>
        {age_html}
      </div>
      <div style="font-size:12px;color:#444441">Why it matters: {esc(a["why_it_matters"])}</div>
      <div style="font-size:12px;color:#444441">Expected impact: {esc(a["expected_impact"])}</div>
      <div style="font-size:12px;color:#444441">Suggested action: {esc(a["suggested_action"])}</div>
      <div style="font-size:11px;color:#898781;margin-top:2px">Confidence {a["confidence"]}% · {esc(a["time_sensitivity"])} · since {esc(since)}</div>
    </div>"""


def _premarket_brief_html(brief: dict | None) -> str:
    if not brief:
        return empty('No brief generated yet — click "Generate Brief Now" above '
                     "(it also runs automatically at 7:00 AM ET on trading days).")

    def stat(label, value):
        return (f'<div><div style="font-size:11px;color:#898781">{esc(label)}</div>'
                f'<div style="font-size:14px;font-weight:600">{esc(value)}</div></div>')

    vix = brief.get("vix") or {}
    y = brief.get("yield_10y") or {}
    d = brief.get("dollar_index") or {}
    macro_stats = []
    if vix.get("level") is not None:
        macro_stats.append(stat("VIX", f'{vix["level"]:.2f} ({vix.get("change_pct", 0):+.1f}%)'))
    for f in brief.get("futures") or []:
        macro_stats.append(stat(f["label"], f'{f["price"]:.2f} ({f["chg_pct"]:+.2f}%)'))
    for m in brief.get("macro") or []:
        macro_stats.append(stat(m["label"], f'{m["price"]:.2f} ({m["chg_pct"]:+.2f}%)'))
    if y.get("level_pct") is not None:
        macro_stats.append(stat("10Y Yield", f'{y["level_pct"]:.3f}% ({y.get("change_bps", 0):+d}bp)'))
    if d.get("level") is not None:
        macro_stats.append(stat("Dollar Index", f'{d["level"]:.2f} ({d.get("change_pct", 0):+.2f}%)'))

    def chips(items, fmt):
        return "".join(f'<span class="chip">{esc(fmt(i))}</span>' for i in items) or empty("—")

    sections = f"""
    <div style="font-size:11px;color:#898781;margin-bottom:10px">Generated {esc(brief["generated_at"][:16].replace("T", " "))}</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:14px;margin-bottom:14px">
      {"".join(macro_stats) or empty("—")}
    </div>
    <div style="margin-bottom:10px"><b style="font-size:12px">Trending sectors</b><br>
      {chips(brief.get("sectors_trending") or [], lambda s: f'{s["label"]} {s["chg_pct"]:+.1f}%')}</div>
    <div style="margin-bottom:10px"><b style="font-size:12px">Lagging sectors</b><br>
      {chips(brief.get("sectors_lagging") or [], lambda s: f'{s["label"]} {s["chg_pct"]:+.1f}%')}</div>
    <div style="margin-bottom:10px"><b style="font-size:12px">Pre-market gainers</b><br>
      {chips(brief.get("gainers") or [], lambda g: f'{g["ticker"]} +{g["chg_pct"]:.1f}%')}</div>
    <div style="margin-bottom:10px"><b style="font-size:12px">Pre-market losers</b><br>
      {chips(brief.get("losers") or [], lambda l: f'{l["ticker"]} {l["chg_pct"]:.1f}%')}</div>
    <div style="margin-bottom:10px"><b style="font-size:12px">Earnings today</b><br>
      {chips(brief.get("earnings_today") or [], lambda e: e["ticker"])}</div>
    <div style="margin-bottom:0"><b style="font-size:12px">Near a key level</b><br>
      {chips(brief.get("near_breakout") or [], lambda a: f'{a["ticker"]} ({a["headline"]})')}</div>
    """
    return sections


# ─────────────────────────────────────────────────────────────────────────────
# AUTOMATION
# ─────────────────────────────────────────────────────────────────────────────

def automation_page() -> tuple[str, str]:
    from stockanalysis.reporting.portfolio import PORTFOLIO_VALUE, SMALLCAP_MAX_PCT
    from stockanalysis.scheduling.schedule_config import (
        JOB_DEFS, SETTINGS_DEFS, load_config, load_settings, describe_spec)
    from urllib.parse import urlencode
    from stockanalysis.scheduling.scheduler import SCHEDULED_JOBS
    # The module, not the flag: LOGIN_GATE is set at server startup, and a
    # `from ... import LOGIN_GATE` would bind whatever it was when this
    # function was first imported.
    from stockanalysis.scheduling import scheduler as scheduler_mod

    alive = jobstore.scheduler_alive()
    cfg = load_config()
    settings = load_settings()

    # function name -> earliest next-run string (scheduler_jobs is sorted by
    # next_run, so the first occurrence per name wins); also collect jobs the
    # config doesn't manage (VIX-adaptive extras, self-test one-offs)
    next_run: dict[str, str] = {}
    managed_names = {fn.__name__ for fn in SCHEDULED_JOBS.values()}
    unmanaged = []
    for j in jobstore.scheduler_jobs():
        if j["job"] in managed_names:
            next_run.setdefault(j["job"], j["next_run"] or "—")
        else:
            unmanaged.append(j)

    def _run_now_form(job_name: str) -> str:
        return (
            f'<form style="margin:0" onsubmit="submitJob(event, this, null); return false;">'
            f'<input type="hidden" name="action" value="run_cron">'
            f'<input type="hidden" name="job_name" value="{esc(job_name)}">'
            f'<button class="btn secondary" style="font-size:10px;padding:2px 8px">Run now</button></form>'
        )

    def _schedule_row(key: str) -> str:
        meta, spec = JOB_DEFS[key], cfg[key]
        fn_name = SCHEDULED_JOBS[key].__name__
        is_daily = spec["type"] == "daily"
        times_val = ", ".join(spec.get("times") or [])
        minutes_val = spec.get("minutes", 10)
        nr = next_run.get(fn_name, "—") if spec["enabled"] else "disabled"
        return f"""
        <tr>
          <td style="min-width:180px"><b style="font-size:12px">{esc(meta["label"])}</b>
            <div style="font-size:10px;color:#898781;max-width:280px">{esc(meta["description"])}</div></td>
          <td>
            <form onsubmit="return saveSchedule(event, this)"
                  style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:0">
              <input type="hidden" name="job_key" value="{esc(key)}">
              <label style="display:flex;gap:4px;align-items:center;font-size:11px">
                <input type="checkbox" name="enabled" {"checked" if spec["enabled"] else ""}> on</label>
              <select name="type" onchange="onSchedTypeChange(this)" style="font-size:11px">
                <option value="daily" {"selected" if is_daily else ""}>daily at</option>
                <option value="interval" {"selected" if not is_daily else ""}>every</option>
              </select>
              <input name="times" value="{esc(times_val)}" placeholder="06:30, 10:00"
                     style="width:130px;font-size:11px;{'' if is_daily else 'display:none'}">
              <span style="display:{'inline-flex' if not is_daily else 'none'};gap:4px;align-items:center;font-size:11px"
                    class="sched-minutes-wrap">
                <input name="minutes" type="number" value="{minutes_val}" min="1" max="1440"
                       style="width:60px;font-size:11px"> min</span>
              <button class="btn" style="font-size:10px;padding:2px 8px">Save</button>
              <button type="button" class="btn secondary" onclick="deleteSchedule(this.form, '{esc(meta["label"])}')"
                      style="font-size:10px;padding:2px 8px;color:#A32D2D;border-color:#A32D2D"
                      {"disabled" if not spec["enabled"] else ""}>Delete</button>
            </form>
          </td>
          <td style="font-size:11px;color:#52514e;white-space:nowrap">{esc(nr)}</td>
          <td>{_run_now_form(fn_name)}</td>
        </tr>"""

    # Only scheduled (enabled) jobs get a table row; everything else lives in
    # the "+ Add job" panel below, so Delete/Add behave like removing and
    # re-adding list entries rather than a wall of disabled rows.
    sched_rows = "".join(_schedule_row(key) for key in JOB_DEFS
                        if cfg[key]["enabled"])

    def _add_item(key: str) -> str:
        meta, spec = JOB_DEFS[key], cfg[key]
        params = {"job_key": key, "enabled": "on", "type": spec["type"]}
        if spec["type"] == "daily":
            params["times"] = ", ".join(spec["times"])
        else:
            params["minutes"] = spec["minutes"]
        return f'''
        <div style="display:flex;gap:10px;align-items:center;justify-content:space-between;
                    padding:6px 0;border-bottom:0.5px solid #f1efea">
          <div><b style="font-size:12px">{esc(meta["label"])}</b>
            <div style="font-size:10px;color:#898781;max-width:300px">{esc(meta["description"])}</div>
            <div style="font-size:10px;color:#52514e">{esc(describe_spec(spec))}</div></div>
          <button type="button" class="btn" style="font-size:10px;padding:2px 10px"
                  onclick="addSchedule('{esc(urlencode(params))}', '{esc(meta["label"])}')">Add</button>
        </div>'''

    available = [k for k in JOB_DEFS if not cfg[k]["enabled"]]
    add_panel = f'''
      <div id="addjob-wrap" style="position:relative;display:inline-block;margin-top:10px">
        <button type="button" class="btn" style="font-size:11px"
                onclick="toggleAddJob()">+ Add job ({len(available)})</button>
        <div id="addjob-panel" style="display:none;position:absolute;top:34px;left:0;z-index:50;
             background:#fff;border:1px solid #d9d7ce;border-radius:8px;
             box-shadow:0 4px 16px rgba(0,0,0,.12);padding:4px 12px 8px;width:400px;
             max-height:400px;overflow-y:auto">
          {"".join(_add_item(k) for k in available)
           or '<div style="font-size:12px;color:#898781;padding:8px 0">All jobs are already scheduled.</div>'}
        </div>
      </div>'''
    unmanaged_html = ""
    if unmanaged:
        items = ", ".join(f'{esc(j["job"])} ({esc(j["next_run"] or "—")})' for j in unmanaged)
        unmanaged_html = (f'<div style="font-size:10px;color:#898781;margin-top:8px">'
                          f'Dynamically added (not editable): {items}</div>')

    status_html = (
        badge("● RUNNING", "good", "small") if alive else
        badge("○ NOT RUNNING", "bad", "small")
        + '<span style="font-size:11px;color:#898781;margin-left:8px">'
          'Start the app without <code>--no-scheduler</code> to enable automatic scans — '
          'schedule edits below still save and apply on next start.</span>'
    )
    # Sign-out gate. Only meaningful when this server requires a login: with
    # --no-auth (or scheduler.py standalone) there are no sessions to count,
    # so say that instead of showing a switch that changes nothing.
    gate_meta = SETTINGS_DEFS["run_when_logged_out"]
    gate_off = bool(settings["run_when_logged_out"])   # True = never pause
    if not scheduler_mod.LOGIN_GATE:
        gate_html = (
            '<div style="font-size:11px;color:#898781;margin-top:10px;'
            'border-top:1px solid #e6e4df;padding-top:10px">'
            + badge("ALWAYS ON", "muted", "small")
            + ' This server runs without a login (<code>--no-auth</code>), so there '
              'is no sign-out to pause on — automation runs whenever the scheduler '
              'is up.</div>')
    else:
        gate_html = (
            '<div style="margin-top:10px;border-top:1px solid #e6e4df;padding-top:10px">'
            + (badge("ALWAYS ON", "watch", "small") if gate_off
               else badge("PAUSES AT SIGN-OUT", "good", "small"))
            + '<form style="display:inline-flex;align-items:center;gap:8px;margin:0 0 0 10px" '
              'onsubmit="saveSchedSettings(event, this); return false;">'
              '<label style="font-size:12px;display:inline-flex;align-items:center;gap:6px">'
              '<input type="checkbox" name="run_when_logged_out"'
            + (' checked' if gate_off else '') + '>'
            + esc(gate_meta["label"]) + '</label>'
              '<button class="btn secondary" style="font-size:10px;padding:2px 8px">Save</button>'
              '</form>'
              '<div style="font-size:10px;color:#898781;max-width:720px;margin-top:6px">'
            + esc(gate_meta["description"])
            + ' Sessions live in memory only, so a server restart also counts as '
              'nobody being signed in: with this off, automation stays paused after '
              'a restart until you open the tool and sign in.</div></div>')

    scheduler_html = (
        status_html
        + gate_html
        + '<div style="font-size:11px;color:#898781;margin-top:8px">'
          'All automated jobs, from data/schedule_config.json. Edit a row and Save to '
          'change its frequency (daily = comma-separated ET times, e.g. "09:30, 15:30"; '
          'every = minutes between runs) or untick "on" to disable it — changes apply '
          'to the running scheduler immediately, no restart. Weekday/market-hours/'
          'Friday/VIX guards live inside the jobs themselves, so they hold whatever '
          'cadence you pick. "Run now" fires a job\'s function immediately without '
          'touching its schedule.</div>'
          f'<table style="margin-top:10px"><thead><tr><th>Job</th><th>Schedule</th>'
          f'<th>Next run (ET)</th><th></th></tr></thead><tbody>'
          f'{sched_rows or f"""<tr><td colspan="4">{empty("No jobs scheduled — use + Add job below.")}</td></tr>"""}'
          f'</tbody></table>'
          + add_panel + unmanaged_html +
          '<form style="margin-top:12px" onsubmit="submitJob(event, this, null); return false;">'
          '<input type="hidden" name="action" value="test_scheduler">'
          '<button class="btn secondary">Run scheduler self-test (fires a job in ~1 min)</button></form>'
    )

    hist = jobstore.history()
    hist_rows = "".join(
        f'<tr><td>{badge(j["status"].upper(), {"done":"good","failed":"bad","running":"watch"}.get(j["status"],"muted"), "small")}</td>'
        f'<td>{esc(j["label"])}</td><td>{esc(j["started"] or "")}</td>'
        f'<td>{esc(j["finished"] or "")}</td><td style="font-size:11px;color:#52514e">{esc(j["detail"])}</td></tr>'
        for j in hist) or '<tr><td colspan="5">' + empty("No jobs run yet this session.") + '</td></tr>'
    history_table = f"""<table><thead><tr>
      <th>Status</th><th>Job</th><th>Started</th><th>Finished</th><th>Detail</th></tr></thead>
      <tbody>{hist_rows}</tbody></table>"""

    settings_html = f"""
    <table><tbody>
      <tr><td style="color:#898781">Portfolio Value</td><td>{fmt_money(PORTFOLIO_VALUE, 0)}</td></tr>
      <tr><td style="color:#898781">Small-cap warning threshold</td><td>{SMALLCAP_MAX_PCT:g}%</td></tr>
      <tr><td style="color:#898781">Account size (risk sizing)</td><td>{fmt_money(float(os.environ.get('ACCOUNT_SIZE', 100000)), 0)}</td></tr>
      <tr><td style="color:#898781">Risk per trade</td><td>{os.environ.get('RISK_PER_TRADE_PCT', '1.0')}%</td></tr>
      <tr><td style="color:#898781">Cleanup retention</td><td>{os.environ.get('CLEANUP_DAYS', '7')} days</td></tr>
    </tbody></table>
    <div style="font-size:11px;color:#898781;margin-top:8px">
      Edit these via environment variables or a .env file in the project root, then restart.</div>"""

    day_session = _read_json(OUTPUT_DIR / "day_session.json")
    if day_session:
        ds_html = (f'<div style="font-size:12px">Initialized {esc(day_session.get("updated_at",""))} — '
                   f'{len(day_session.get("hot") or [])} movers + '
                   f'{len(day_session.get("base") or [])} base = '
                   f'{len(day_session.get("merged") or [])} tickers</div>')
    else:
        ds_html = empty("Not initialized yet — runs automatically at market open under the scheduler.")

    cleanup_form = """
    <form onsubmit="submitJob(event, this, null); return false;" style="display:flex;gap:10px;align-items:center">
      <input type="hidden" name="action" value="cleanup">
      <input name="days" type="number" value="7" min="0" step="1" style="width:80px">
      <button class="btn warn">Clean up outputs older than N days</button>
    </form>"""

    last_test = next((j for j in hist if j["kind"] == "test"), None)
    if last_test:
        ok = last_test["status"] == "done" and last_test["detail"].startswith("PASSED")
        test_status = (
            badge(("✓ " if ok else "✗ ") + last_test["detail"][:80],
                  "good" if ok else "bad", "small")
            + f'<div style="font-size:11px;color:#898781;margin-top:6px">'
              f'Last run {esc(last_test["finished"] or last_test["started"] or "")} · '
              f'<a href="/last_test_run.txt" target="_blank">full output</a></div>'
        )
    else:
        test_status = empty("Not run yet this session.")
    tests_form = f"""
    <div style="margin-bottom:10px">{test_status}</div>
    <form onsubmit="submitJob(event, this, null); return false;">
      <input type="hidden" name="action" value="test">
      <button class="btn">Run unit tests (tests/)</button>
    </form>"""

    body = (
        card("Scheduler", scheduler_html, "⏱")
        + card("Unit Tests", tests_form, "🧪")
        + card("Scan History", history_table, "🗒")
        + card("Settings (read-only)", settings_html, "⚙️")
        + card("Day Session Universe", ds_html, "🔔")
        + card("Cleanup", cleanup_form, "🧹")
    )
    # Any job finishing while this page is open means the history table /
    # day-session panel are stale — reload once rather than leave them frozen
    extra_js = """
    function onJobFinished(j) { setTimeout(() => location.reload(), 1200); }
    function onSchedTypeChange(sel) {
      const form = sel.closest('form');
      const daily = sel.value === 'daily';
      form.querySelector('[name="times"]').style.display = daily ? '' : 'none';
      form.querySelector('.sched-minutes-wrap').style.display = daily ? 'none' : 'inline-flex';
    }
    async function saveSchedSettings(event, form) {
      event.preventDefault();
      // Unchecked checkboxes post nothing, so send the flag explicitly —
      // save_settings() leaves out-of-form keys alone, and an omitted key
      // would read as "no change" instead of "turn it off".
      const params = new URLSearchParams();
      params.set('run_when_logged_out',
                 form.querySelector('[name="run_when_logged_out"]').checked ? 'on' : 'off');
      try {
        const res = await fetch('/api/schedule/settings', { method: 'POST', body: params });
        const data = await res.json();
        toast(data.message || (data.ok ? 'Saved' : 'Save failed'), data.ok ? 'ok' : 'err');
        if (data.ok) setTimeout(() => location.reload(), 900);
      } catch (e) { toast('Request failed: ' + e, 'err'); }
      return false;
    }
    async function saveSchedule(event, form) {
      event.preventDefault();
      try {
        const res = await fetch('/api/schedule/save', {
          method: 'POST', body: new URLSearchParams(new FormData(form)),
        });
        const data = await res.json();
        toast(data.message || (data.ok ? 'Saved' : 'Save failed'), data.ok ? 'ok' : 'err');
        if (data.ok) setTimeout(() => location.reload(), 900);
      } catch (e) { toast('Request failed: ' + e, 'err'); }
      return false;
    }
    // Delete = remove the job from the live schedule (enabled: off). The row
    // stays listed with its saved times so it can be re-enabled any time —
    // jobs are built-ins, so "gone forever" would just be confusing.
    function toggleAddJob() {
      const p = document.getElementById('addjob-panel');
      p.style.display = p.style.display === 'none' ? 'block' : 'none';
    }
    // qs is a prebuilt job_key/enabled/type/times query string — the job's
    // saved spec, so re-adding restores whatever cadence it had before Delete.
    async function addSchedule(qs, label) {
      try {
        const res = await fetch('/api/schedule/save', {
          method: 'POST', body: new URLSearchParams(qs),
        });
        const data = await res.json();
        toast(data.ok ? label + ' added to schedule' : (data.message || 'Add failed'),
              data.ok ? 'ok' : 'err');
        if (data.ok) setTimeout(() => location.reload(), 900);
      } catch (e) { toast('Request failed: ' + e, 'err'); }
    }
    async function deleteSchedule(form, label) {
      if (!confirm('Remove "' + label + '" from the schedule?\\n\\nIt stops running ' +
                   'immediately but stays listed here — tick "on" and Save to restore it.')) return;
      const params = new URLSearchParams(new FormData(form));
      params.delete('enabled');
      try {
        const res = await fetch('/api/schedule/save', { method: 'POST', body: params });
        const data = await res.json();
        toast(data.ok ? label + ' removed from schedule' : (data.message || 'Delete failed'),
              data.ok ? 'ok' : 'err');
        if (data.ok) setTimeout(() => location.reload(), 900);
      } catch (e) { toast('Request failed: ' + e, 'err'); }
    }"""
    return body, extra_js
