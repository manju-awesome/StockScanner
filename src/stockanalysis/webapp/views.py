"""
views.py
========
HTML rendering for the webapp: one shared layout (sidebar + topbar + toast/
job-polling JS) wrapping five pages — Dashboard, Scanner, Research, Portfolio,
Automation. Pure string building, no HTTP here; app.py calls render_page()
and writes the result to the response.

Color system (reused across every page so status always reads the same way):
  green  = bullish / ready / good      #0F6E56 on #E1F5EE
  yellow = watch / neutral / caution   #633806 on #FAEEDA
  red    = avoid / danger / alert      #791F1F on #FCEBEB
  blue   = informational                #0C447C on #E6F1FB
"""

from __future__ import annotations

import html as _html
import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR   = PROJECT_ROOT / "data" / "output"
DATA_DIR     = PROJECT_ROOT / "data"


def tv_url(ticker: str) -> str:
    """TradingView chart URL for a ticker. No exchange prefix — TradingView
    resolves it (hardcoding NASDAQ: would break NYSE names); '-' share
    classes become '.' (yfinance's BRK-B is TradingView's BRK.B)."""
    return f"https://www.tradingview.com/chart/?symbol={str(ticker).replace('-', '.')}"

NAV = (("dashboard", "/", "🏠", "Dashboard"),
       ("ai-sentiment", "/ai-sentiment", "🤖", "AI Sentiment"),
       ("scanner",   "/scanner", "📡", "Scanner"),
       ("research",  "/research", "🔎", "Research"),
       ("portfolio", "/portfolio", "💼", "Portfolio"),
       ("journal",   "/journal", "📓", "Journal"),
       ("alerts",    "/alerts", "🔔", "Alerts"),
       ("automation","/automation", "⚙️", "Automation"))

_STATUS = {
    "good":  ("#E1F5EE", "#085041", "#0F6E56"),
    "watch": ("#FAEEDA", "#633806", "#8a6d1a"),
    "bad":   ("#FCEBEB", "#791F1F", "#A32D2D"),
    "info":  ("#E6F1FB", "#0C447C", "#185FA5"),
    "muted": ("#F1EFE8", "#444441", "#898781"),
}
_ACTION_STATUS = {"READY": "good", "WATCH": "watch", "AVOID": "bad"}


def _v(d, k, default=None):
    v = (d or {}).get(k)
    return default if v is None else v


def esc(s) -> str:
    return _html.escape(str(s)) if s is not None else ""


def badge(text: str, status: str = "muted", size: str = "normal") -> str:
    bg, fg, _ = _STATUS.get(status, _STATUS["muted"])
    pad = "1px 7px" if size == "small" else "3px 10px"
    fs = "9px" if size == "small" else "11px"
    return (f'<span style="background:{bg};color:{fg};font-size:{fs};'
            f'font-weight:600;padding:{pad};border-radius:5px;'
            f'white-space:nowrap">{esc(text)}</span>')


def stars(n, size: int = 13) -> str:
    n = max(0, min(5, int(n or 0)))
    return (f'<span style="color:#c9a227;font-size:{size}px;letter-spacing:1px">'
            + "★" * n + '<span style="color:#d9d7ce">' + "☆" * (5 - n)
            + "</span></span>")


def action_badge(action: str | None, size: str = "normal") -> str:
    if not action:
        return badge("—", "muted", size)
    dot = {"READY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(action, "")
    return badge(f"{dot} {action}", _ACTION_STATUS.get(action, "muted"), size)


def progress_bar(pct, color="#185FA5") -> str:
    if pct is None:
        return (f'<div style="background:#f1efea;border-radius:4px;height:8px;'
                f'overflow:hidden"><div style="width:35%;height:100%;'
                f'background:{color};animation:indet 1.2s ease-in-out infinite"></div></div>')
    return (f'<div style="background:#f1efea;border-radius:4px;height:8px;overflow:hidden">'
            f'<div style="width:{max(2, pct)}%;height:100%;background:{color};'
            f'transition:width .4s"></div></div>')


def card(title: str, body: str, icon: str = "", right: str = "",
        pad: str = "16px 18px") -> str:
    head = ""
    if title:
        head = (f'<div style="display:flex;align-items:center;gap:8px;'
                f'margin-bottom:10px;flex-wrap:wrap">'
                + (f'<span style="font-size:17px">{icon}</span>' if icon else "")
                + f'<h3 style="font-size:14px;font-weight:600;color:#0b0b0b;margin:0">{esc(title)}</h3>'
                + (f'<span style="margin-left:auto">{right}</span>' if right else "")
                + '</div>')
    return (f'<div style="background:white;border:0.5px solid #e1e0d9;'
            f'border-radius:12px;padding:{pad};margin-bottom:16px">{head}{body}</div>')


def empty(msg: str) -> str:
    return f'<span style="font-size:13px;color:#898781">{esc(msg)}</span>'


def fmt_money(v, nd=2) -> str:
    try:
        return f"${float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(v, nd=1) -> str:
    try:
        v = float(v)
        return f"{'+' if v >= 0 else ''}{v:.{nd}f}%"
    except (TypeError, ValueError):
        return "—"


# ─────────────────────────────────────────────────────────────────────────────
# SHARED LAYOUT
# ─────────────────────────────────────────────────────────────────────────────

_STYLE = """
* { box-sizing:border-box }
body { margin:0; font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
       background:#faf9f5; color:#0b0b0b; display:flex; min-height:100vh }
a { color:#185FA5 }
table { border-collapse:collapse; width:100% }
th { text-align:left; font-size:10px; color:#898781; padding:4px 8px;
     text-transform:uppercase; letter-spacing:.3px }
td { padding:7px 8px; font-size:12px; border-top:0.5px solid #e1e0d9 }
input, select { font-size:13px; padding:6px 10px; border:1px solid #d9d7ce;
                border-radius:6px; font-family:inherit; background:white }
button { font-family:inherit; cursor:pointer }
.btn { font-size:12px; font-weight:600; padding:7px 14px; border:none;
       border-radius:7px; background:#185FA5; color:white }
.btn.secondary { background:white; color:#0b0b0b; border:1px solid #d9d7ce }
.btn.warn { background:#8a6d1a }
.btn:disabled { opacity:.5; cursor:not-allowed }
.chip { display:inline-block; background:#f1efea; color:#0b0b0b; font-size:11px;
        padding:3px 9px; border-radius:5px; margin:0 4px 4px 0; text-decoration:none }
.sidebar { width:196px; flex-shrink:0; background:white; border-right:0.5px solid #e1e0d9;
           padding:18px 12px; position:sticky; top:0; height:100vh; overflow-y:auto }
.navlink { display:flex; align-items:center; gap:9px; padding:9px 12px; border-radius:8px;
           font-size:13px; font-weight:500; color:#444441; text-decoration:none; margin-bottom:2px }
.navlink.active { background:#E6F1FB; color:#0C447C }
.navlink:hover:not(.active) { background:#f5f4f0 }
.main { flex:1; min-width:0; padding:18px 26px 60px }
.topbar { display:flex; align-items:center; gap:10px; margin-bottom:18px; flex-wrap:wrap;
          position:relative }
.searchwrap { position:relative; flex:1; min-width:220px; max-width:360px }
#search-results { position:absolute; top:38px; left:0; right:0; background:white;
                  border:0.5px solid #e1e0d9; border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,.08);
                  max-height:320px; overflow-y:auto; z-index:50; display:none }
.search-row { display:flex; gap:8px; align-items:center; padding:8px 12px; text-decoration:none;
              color:#0b0b0b; font-size:12px; border-top:0.5px solid #f1efea }
.search-row:first-child { border-top:none }
.search-row:hover { background:#f5f4f0 }
dialog { border:none; border-radius:12px; padding:0; box-shadow:0 20px 60px rgba(0,0,0,.2) }
dialog::backdrop { background:rgba(11,11,11,.35) }
.modal-body { padding:20px 22px; min-width:320px }
.modal-body h3 { margin:0 0 14px; font-size:15px }
.modal-actions { display:flex; gap:8px; margin-top:16px; justify-content:flex-end }
#toasts { position:fixed; bottom:18px; right:18px; z-index:100; display:flex;
          flex-direction:column; gap:8px }
.toast { background:#0b0b0b; color:white; font-size:12px; padding:10px 16px;
         border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,.2); max-width:340px;
         animation:slidein .2s ease-out }
.toast.err { background:#791F1F }
.toast.ok { background:#0F6E56 }
#jobtray { position:fixed; bottom:18px; left:212px; z-index:90; display:none;
           background:white; border:0.5px solid #e1e0d9; border-radius:10px;
           padding:10px 14px; box-shadow:0 8px 24px rgba(0,0,0,.1); min-width:260px }
.col-resizer { position:absolute; top:0; right:0; bottom:0; width:6px; cursor:col-resize;
               user-select:none; touch-action:none }
.col-resizer:hover, .col-resizer.active { background:#0F6E56 }
@keyframes slidein { from{transform:translateY(8px);opacity:0} to{transform:translateY(0);opacity:1} }
@keyframes indet { 0%{margin-left:0%} 50%{margin-left:65%} 100%{margin-left:0%} }
@media (max-width:820px) { .sidebar{display:none} .main{padding:14px} }
"""

_JS = r"""
function toast(msg, kind) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'err' ? ' err' : kind === 'ok' ? ' ok' : '');
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

function openModal(id) { document.getElementById(id).showModal(); }
function closeModal(id) { document.getElementById(id).close(); }

async function submitJob(event, form, modalId) {
  // event.preventDefault() must run synchronously, before the first await —
  // this is an async function, so "return submitJob(...)" in an onsubmit
  // attribute returns a pending Promise (truthy), which does NOT stop the
  // browser's native form submission. Without this, every submit fired both
  // our fetch AND a real page navigation (GET to the current URL with the
  // form fields as a query string), racing each other — the symptom was the
  // page appearing to "hang" in a reload instead of showing the toast.
  event.preventDefault();
  const fd = new FormData(form);
  try {
    const res = await fetch('/run', { method: 'POST', body: new URLSearchParams(fd) });
    const data = await res.json();
    if (data.ok) { toast(data.message || 'Started', 'ok'); }
    else { toast(data.message || 'Failed to start', 'err'); }
  } catch (e) { toast('Request failed: ' + e, 'err'); }
  if (modalId) closeModal(modalId);
  const action = fd.get('action');
  if (action) _justSubmitted.add(action);
  pollJobs();
  return false;
}

// ── search autocomplete ─────────────────────────────────────────────────────
let searchTimer = null;
function onSearchInput(e) {
  clearTimeout(searchTimer);
  const q = e.target.value;
  searchTimer = setTimeout(() => runSearch(q), 150);
}
async function runSearch(q) {
  const box = document.getElementById('search-results');
  if (!q.trim()) { box.style.display = 'none'; box.innerHTML = ''; return; }
  try {
    const res = await fetch('/api/search?q=' + encodeURIComponent(q));
    const rows = await res.json();
    if (!rows.length) { box.innerHTML = '<div class="search-row">no matches</div>'; }
    else {
      box.innerHTML = rows.map(r =>
        `<a class="search-row" href="/research/${r.ticker}.html">
           <b>${r.ticker}</b>
           <span style="color:#898781">${r.sector || ''}</span>
           <span style="margin-left:auto">${r.conv_action || ''} · score ${r.conv_overall ?? '—'}</span>
         </a>`).join('');
    }
    box.style.display = 'block';
  } catch (e) { box.style.display = 'none'; }
}
document.addEventListener('click', (e) => {
  const box = document.getElementById('search-results');
  if (box && !e.target.closest('.searchwrap')) box.style.display = 'none';
});

// ── job polling: live tray + toast on running->done/failed transitions ─────
// jobstore keeps the LAST job per kind forever (so Automation's history has
// something to show) — it never expires. That means a job that was already
// "done" before this page loaded stays "done" on every future poll too. If
// we toasted/reloaded for every not-yet-seen-as-finished job (keyed only by
// a per-load Set), a page that auto-reloads on completion would see that
// same old finished job again immediately after reloading — reload, see it
// "unseen" again, reload again — an infinite loop (the reported "blinking").
//
// Fix: only treat a job as a live finish if THIS tab actually watched it —
// either polled it while status was "running" (_knownRunning), or just
// submitted it itself (_justSubmitted, covers jobs finishing faster than
// the polling interval). A job that was already sitting "done" when the
// page loaded is never in either set, so it's correctly ignored.
const _knownRunning  = new Set();   // job keys ("kind|started") seen running
const _justSubmitted = new Set();   // kinds this tab started, not yet observed
async function pollJobs() {
  try {
    const res = await fetch('/api/jobs');
    const jobs = await res.json();
    renderJobTray(jobs);
    for (const j of jobs) {
      const key = j.kind + '|' + j.started;
      const engaged = _knownRunning.has(key) || _justSubmitted.has(j.kind);
      if (j.status === 'running') {
        _knownRunning.add(key);
        _justSubmitted.delete(j.kind);
        if (typeof onJobRunning === 'function') onJobRunning(j);
      } else if (engaged) {
        _knownRunning.delete(key);
        _justSubmitted.delete(j.kind);
        if (j.status === 'done') toast('✓ ' + j.label + ' — ' + j.detail, 'ok');
        else if (j.status === 'failed') toast('✗ ' + j.label + ' failed: ' + j.detail, 'err');
        if (typeof onJobFinished === 'function') onJobFinished(j);
      }
    }
  } catch (e) { /* server may be mid-restart; ignore */ }
}
function renderJobTray(jobs) {
  const tray = document.getElementById('jobtray');
  const running = jobs.filter(j => j.status === 'running');
  if (!running.length) { tray.style.display = 'none'; return; }
  tray.style.display = 'block';
  tray.innerHTML = running.map(j => {
    const pct = j.pct;
    const barWidth = pct == null ? 35 : Math.max(2, pct);
    const anim = pct == null ? 'animation:indet 1.2s ease-in-out infinite' : '';
    return `<div style="margin-bottom:6px">
      <div style="font-size:11px;font-weight:600;margin-bottom:3px">${j.label}</div>
      <div style="font-size:10px;color:#898781;margin-bottom:3px">${j.stage || 'running'}${pct!=null ? ' · '+pct+'%':''}</div>
      <div style="background:#f1efea;border-radius:4px;height:6px;overflow:hidden">
        <div style="width:${barWidth}%;height:100%;background:#185FA5;${anim}"></div>
      </div></div>`;
  }).join('');
}
setInterval(pollJobs, 1500);
document.addEventListener('DOMContentLoaded', pollJobs);

async function toggleWatchlist(name, ticker, btn) {
  if (!name) { toast('Pick a list first', 'err'); return; }
  try {
    const res = await fetch('/api/watchlist/toggle', {
      method: 'POST',
      body: new URLSearchParams({ name, ticker }),
    });
    const data = await res.json();
    // Keep any page-local WATCHLISTS in sync so a later re-render (sort,
    // filter, view toggle) doesn't revert the star from stale client state
    if (typeof WATCHLISTS !== 'undefined') Object.assign(WATCHLISTS, data.watchlists);
    const starred = (data.watchlists[name] || []).includes(ticker);
    if (btn) btn.textContent = starred ? '★' : '☆';
    toast(ticker + (starred ? ' added to ' : ' removed from ') + name, 'ok');
  } catch (e) { toast('Watchlist update failed', 'err'); }
}
"""


def render_layout(active: str, title: str, body: str,
                  extra_js: str = "") -> str:
    navlinks = "".join(
        f'<a class="navlink{" active" if key == active else ""}" href="{href}">'
        f'<span>{icon}</span><span>{label}</span></a>'
        for key, href, icon, label in NAV)

    toolbar = """
      <button class="btn" onclick="openModal('modal-scan')">+ New Scan</button>
      <button class="btn secondary" onclick="openModal('modal-research')">+ Refresh Research</button>
      <button class="btn secondary" onclick="openModal('modal-news')">+ Update News</button>
      <a class="btn secondary" href="/portfolio" style="text-decoration:none;display:inline-flex;align-items:center">Portfolio</a>
      <a class="btn secondary" href="/automation" style="text-decoration:none;display:inline-flex;align-items:center">Settings</a>
    """

    try:
        watchlists = json.loads((DATA_DIR / "watchlists.json").read_text())
    except Exception:
        watchlists = {}
    builtin_universes = ("daytrade", "watchlist", "longterm", "dividend", "sp500")
    universe_opts = '<optgroup label="Built-in">' + "".join(
        f'<option value="{u}">{u}</option>' for u in builtin_universes
    ) + '</optgroup>'
    if watchlists:
        universe_opts += '<optgroup label="Watchlists">' + "".join(
            f'<option value="{esc(name)}">{esc(name)} ({len(tickers)})</option>'
            for name, tickers in sorted(watchlists.items())
            if tickers and name not in builtin_universes
        ) + '</optgroup>'

    watchlist_opts = "".join(
        f'<option value="{esc(name)}">{esc(name)} ({len(tickers)})</option>'
        for name, tickers in sorted(watchlists.items()) if tickers)

    modals = f"""
    <dialog id="modal-scan">
      <form class="modal-body" onsubmit="submitJob(event, this, 'modal-scan'); return false;">
        <h3>Run a scan</h3>
        <input type="hidden" name="action" value="scan">
        <div style="display:flex;flex-direction:column;gap:10px">
          <select name="universe" multiple size="8">{universe_opts}</select>
          <div style="font-size:10px;color:#898781;margin-top:-6px">⌘/Ctrl-click to scan several categories at once (none selected = daytrade)</div>
          <label style="font-size:12px;display:flex;gap:6px;align-items:center">
            <input type="checkbox" name="portfolio" checked> include Portfolio management</label>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn secondary" onclick="closeModal('modal-scan')">Cancel</button>
          <button class="btn">Run scan</button>
        </div>
      </form>
    </dialog>
    <dialog id="modal-research">
      <form class="modal-body" onsubmit="submitJob(event, this, 'modal-research'); return false;">
        <h3>Refresh research pages</h3>
        <input type="hidden" name="action" value="research">
        <div style="display:flex;flex-direction:column;gap:10px">
          <select name="watchlist" multiple size="6">{watchlist_opts}</select>
          <div style="font-size:10px;color:#898781;margin-top:-6px">⌘/Ctrl-click to combine categories</div>
          <input name="tickers" placeholder="or type tickers e.g. NVDA AMD (blank = day-trade list; ignored if a category is picked)" style="width:100%">
        </div>
        <div class="modal-actions">
          <button type="button" class="btn secondary" onclick="closeModal('modal-research')">Cancel</button>
          <button class="btn">Refresh</button>
        </div>
      </form>
    </dialog>
    <dialog id="modal-news">
      <form class="modal-body" onsubmit="submitJob(event, this, 'modal-news'); return false;">
        <h3>Update latest news</h3>
        <input type="hidden" name="action" value="news">
        <div style="display:flex;flex-direction:column;gap:10px">
          <select name="watchlist" multiple size="6">{watchlist_opts}</select>
          <div style="font-size:10px;color:#898781;margin-top:-6px">⌘/Ctrl-click to combine categories</div>
          <input name="tickers" placeholder="or type tickers (blank = all research pages; ignored if a category is picked)" style="width:100%">
        </div>
        <div class="modal-actions">
          <button type="button" class="btn secondary" onclick="closeModal('modal-news')">Cancel</button>
          <button class="btn">Scan news</button>
        </div>
      </form>
    </dialog>
    """

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Trading Workstation</title>
<style>{_STYLE}</style></head><body>
<div class="sidebar">
  <div style="font-weight:700;font-size:15px;padding:8px 12px 16px">🖥 Workstation</div>
  {navlinks}
</div>
<div class="main">
  <div class="topbar">
    <div class="searchwrap">
      <input id="global-search" placeholder="🔍 Search ticker or sector…" style="width:100%"
             oninput="onSearchInput(event)" onfocus="onSearchInput(event)">
      <div id="search-results"></div>
    </div>
    {toolbar}
  </div>
  {body}
</div>
<div id="toasts"></div>
<div id="jobtray"></div>
{modals}
<script>{_JS}{extra_js}</script>
</body></html>"""


def render_page(active: str, title: str, body: str, extra_js: str = "") -> bytes:
    return render_layout(active, title, body, extra_js).encode()
