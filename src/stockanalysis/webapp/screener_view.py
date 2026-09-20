"""
screener_view.py
=================
The Screener page — visual rule builder, natural-language search, presets,
saved searches and result cards over the research library.

Split out of pages.py for the same reason risk_view.py was: pages.py is
already the biggest file in the app and this page is a small application on
its own. The rule that keeps it honest: nothing here decides what matches.
Every threshold, comparison, score and explanation comes from
core/screener.py through /api/screen — this file renders what it is given.
That's why the result cards can claim "here's why it matched" and be right:
the ✓/✗ lines are the engine's own per-condition output, not a second
implementation of the same rules in JavaScript.

The page is one static shell plus a JS controller that keeps the rule tree
in a variable and re-posts it on every change. Rendering results server-side
would mean a full page load per pill toggle; at ~560 rows the whole screen
costs a few tens of ms, so the interaction is a fetch and a re-render.
"""

from __future__ import annotations

from .views import card


def screener_page() -> tuple[str, str]:
    body = f"""
{_STYLE}
<div class="scr-head">
  <div>
    <h2 style="margin:0 0 3px;font-size:19px">Screener</h2>
    <div style="font-size:11px;color:#898781">
      Combine any number of conditions over the research library —
      <span id="scr-universe">…</span> tickers with a research page.
    </div>
  </div>
  <div style="display:flex;gap:8px;margin-left:auto;align-items:center">
    <div id="scr-savedwrap" style="position:relative">
      <button class="btn secondary" onclick="scrToggleSaved(event)">
        ⭐ Saved <span id="scr-savedcount">(0)</span> ▾</button>
      <div id="scr-savedmenu"></div>
    </div>
    <button class="btn secondary" onclick="scrOpenSave()">☆ Save search</button>
    <button class="btn secondary" onclick="scrReset()">Clear all</button>
  </div>
</div>

<div class="scr-searchbar">
  <div style="position:relative;flex:1;min-width:260px">
    <input id="scr-nl" placeholder="Try: price near 8 ema, quality above 90, turnaround, buy zone stocks above 200ma"
           autocomplete="off" oninput="scrOnType(this.value)"
           onkeydown="if(event.key==='Enter'){{scrRunNL();return false}}"
           style="width:100%;padding:10px 12px;font-size:13px">
    <div id="scr-suggest"></div>
  </div>
  <button class="btn" onclick="scrRunNL()">Search</button>
  <button class="btn secondary" onclick="scrOpenPicker()">+ Add condition</button>
  <input id="scr-lookup" placeholder="Look up a ticker…" autocomplete="off"
         style="width:150px" title="Which of the presets does this ticker qualify for?"
         onkeydown="if(event.key==='Enter'){{scrLookup();return false}}">
  <button class="btn secondary" onclick="scrLookup()">Where does it fit?</button>
</div>
<div id="scr-lookupresult"></div>

<div class="scr-presetbar">
  <input id="scr-presetsearch" placeholder="Filter presets…" autocomplete="off"
         oninput="scrRenderPresets()" style="width:200px">
  <div id="scr-presettabs" class="scr-presettabs"></div>
  <span id="scr-presetcount" style="font-size:11px;color:#898781;margin-left:auto"></span>
</div>
<div id="scr-presets" class="scr-presets"></div>

{card("Active rules", '<div id="scr-rules"></div>', icon="🧩",
      right='<span id="scr-livecount" class="scr-live">—</span>')}

<div id="scr-refine"></div>
<div id="scr-missing"></div>
<div id="scr-summary"></div>

<div class="scr-resulthead">
  <div id="scr-resulttitle" style="font-size:13px;font-weight:600">Results</div>
  <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
    <label style="font-size:11px;color:#898781">Strategy</label>
    <select id="scr-strategy" onchange="scrRun()"
            title="Which score gates the action: the company, the setup, or both">
      <option value="LONGTERM">Long-term</option>
      <option value="SWING">Swing</option>
      <option value="BALANCED">Balanced</option>
    </select>
    <label style="font-size:11px;color:#898781">Action</label>
    <select id="scr-groupby" onchange="scrOnActionPick(this.value)"
            title="All actions groups the results into sections; pick one to see only those">
      <option value="">All actions</option>
    </select>
    <label style="font-size:11px;color:#898781">Buy Zone</label>
    <select id="scr-bzpick" onchange="scrOnBuyZonePick(this.value)"
            title="Filter by the technical entry-quality label">
      <option value="">All</option>
    </select>
    <label style="font-size:11px;color:#898781">Sort</label>
    <select id="scr-sort" onchange="scrRun()">
      <option value="match">Match score</option>
      <option value="decision">Action, then score</option>
      <option value="composite">Weighted composite</option>
      <option value="quality">Quality</option>
      <option value="rs">RS rank</option>
      <option value="conviction">Conviction</option>
      <option value="eps">EPS growth</option>
      <option value="breakout">Breakout %</option>
      <option value="market_cap">Market cap</option>
      <option value="ticker">Ticker A-Z</option>
    </select>
    <button class="btn secondary" onclick="scrOpenWeights()">⚖ Weights</button>
    <button class="btn secondary" onclick="scrToggleView()" id="scr-viewbtn">Table view</button>
    <button class="btn secondary" onclick="scrDownloadCsv()"
            title="Download these results, including the rules each stock matched">⬇ CSV</button>
  </div>
</div>
<div id="scr-results"><div class="scr-empty">Loading…</div></div>

{_MODALS}
"""
    return body, _JS


_STYLE = """
<style>
.scr-head { display:flex; align-items:flex-end; gap:12px; margin-bottom:14px; flex-wrap:wrap }
.scr-searchbar { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; align-items:flex-start }
#scr-suggest { position:absolute; top:40px; left:0; right:0; background:white;
  border:0.5px solid #e1e0d9; border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,.10);
  max-height:300px; overflow-y:auto; z-index:60; display:none }
.scr-sug { display:flex; gap:8px; align-items:center; padding:8px 12px; font-size:12px;
  cursor:pointer; border-top:0.5px solid #f1efea }
.scr-sug:first-child { border-top:none }
.scr-sug:hover, .scr-sug.on { background:#E6F1FB }
.scr-sug .k { margin-left:auto; font-size:10px; color:#898781; text-transform:uppercase }
.scr-presetbar { display:flex; gap:8px; align-items:center; margin-bottom:9px; flex-wrap:wrap }
.scr-presettabs { display:flex; gap:4px; flex-wrap:wrap }
.scr-ptab { font-size:11px; font-weight:600; padding:4px 11px; border-radius:14px;
  cursor:pointer; border:1px solid #d9d7ce; background:transparent; color:#52514e }
.scr-ptab:hover { border-color:#185FA5; color:#185FA5 }
.scr-ptab.on { background:#185FA5; border-color:#185FA5; color:white }
/* A grid, not a horizontal scroller: 51 presets in a one-line strip means
   scrolling blind past 40 of them. */
.scr-presets { display:grid; grid-template-columns:repeat(auto-fill, minmax(186px, 1fr));
  gap:8px; margin-bottom:14px; max-height:330px; overflow-y:auto; padding-right:2px }
.scr-preset { background:white; border:0.5px solid #e1e0d9; border-radius:10px;
  padding:9px 12px; cursor:pointer; position:relative }
.scr-preset:hover { border-color:#185FA5; background:#F7FBFF }
.scr-preset.on { border-color:#185FA5; background:#E6F1FB }
.scr-preset.zero { opacity:.5 }
.scr-preset b { display:block; font-size:12px; margin-bottom:2px; padding-right:30px }
.scr-preset span.d { font-size:10px; color:#898781; line-height:1.35; display:block }
/* The live match count, so an empty screen is visible before it's clicked */
.scr-preset .n { position:absolute; top:8px; right:9px; font-size:10px; font-weight:700;
  color:#0C447C; background:#E6F1FB; border-radius:9px; padding:1px 6px }
.scr-preset.zero .n { color:#898781; background:#f1efea }
.scr-presetempty { grid-column:1/-1; padding:16px; text-align:center; color:#898781;
  font-size:12px }
.scr-live { font-size:11px; font-weight:600; color:#0C447C; background:#E6F1FB;
  padding:3px 9px; border-radius:20px }
.scr-pill { display:inline-flex; align-items:center; gap:7px; background:#E6F1FB; color:#0C447C;
  border:0.5px solid #cfe2f5; font-size:11.5px; font-weight:600; padding:5px 6px 5px 11px;
  border-radius:20px; margin:0 5px 5px 0 }
.scr-pill.neg { background:#FCEBEB; color:#791F1F; border-color:#f3d5d5 }
.scr-pill .x { cursor:pointer; border:none; background:rgba(0,0,0,.06); color:inherit;
  border-radius:50%; width:17px; height:17px; line-height:15px; font-size:12px; padding:0 }
.scr-pill .x:hover { background:rgba(0,0,0,.16) }
.scr-pill .n { font-weight:500; opacity:.72; font-size:10px }
.scr-joiner { display:inline-block; font-size:10px; font-weight:700; color:#898781;
  margin:0 6px 5px 1px; letter-spacing:.5px; cursor:pointer; text-decoration:underline dotted }
.scr-joiner:hover { color:#185FA5 }
.scr-empty { padding:26px; text-align:center; color:#898781; font-size:12px;
  background:white; border:0.5px solid #e1e0d9; border-radius:12px }
.scr-note { font-size:11px; padding:9px 13px; border-radius:9px; margin-bottom:10px;
  background:#FAEEDA; color:#633806; border:0.5px solid #f0dfc0 }
.scr-note b { font-weight:600 }
.scr-refine { font-size:11px; padding:9px 13px; border-radius:9px; margin-bottom:10px;
  background:#F7FBFF; color:#0C447C; border:0.5px solid #cfe2f5;
  display:flex; gap:10px; align-items:center; flex-wrap:wrap }
.scr-stats { display:flex; gap:9px; flex-wrap:wrap; margin-bottom:14px }
.scr-stat { background:white; border:0.5px solid #e1e0d9; border-radius:11px;
  padding:10px 15px; min-width:104px }
.scr-stat .v { font-size:19px; font-weight:600; line-height:1.15 }
.scr-stat .l { font-size:10px; color:#898781; text-transform:uppercase; letter-spacing:.3px }
.scr-resulthead { display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap:wrap }
.scr-card { background:white; border:0.5px solid #e1e0d9; border-radius:12px; padding:13px 15px;
  margin-bottom:9px }
.scr-card:hover { border-color:#cfe2f5 }
.scr-cardtop { display:flex; align-items:center; gap:10px; flex-wrap:wrap }
.scr-tk { font-size:15px; font-weight:700; text-decoration:none; color:#0b0b0b }
.scr-tk:hover { color:#185FA5 }
.scr-why { display:flex; flex-wrap:wrap; gap:5px; margin-top:9px }
.scr-why div { font-size:11px; padding:3px 9px; border-radius:6px; background:#E1F5EE;
  color:#085041 }
.scr-why div.no { background:#FCEBEB; color:#791F1F }
.scr-hl { background:#CFF0E4; color:#085041; font-weight:700; padding:0 4px; border-radius:4px }
.scr-meta { display:flex; gap:14px; flex-wrap:wrap; margin-top:8px; font-size:11px;
  color:#898781 }
.scr-meta b { color:#0b0b0b; font-weight:600 }
/* Pullback plan: collapsed by default so a screen of 100 rows stays
   readable, but one click from the verdict that depends on it. */
.scr-pb summary::-webkit-details-marker { display:none }
.scr-pb summary:hover { text-decoration:underline }
.scr-pb[open] summary { color:#0b0b0b }
.scr-ms { margin-left:auto; text-align:right; flex-shrink:0 }
.scr-ms .v { font-size:17px; font-weight:700; color:#0F6E56 }
.scr-ms .l { font-size:9px; color:#898781; text-transform:uppercase }
#scr-savedmenu { position:absolute; top:34px; right:0; min-width:246px; background:white;
  border:0.5px solid #e1e0d9; border-radius:10px; box-shadow:0 8px 24px rgba(0,0,0,.12);
  z-index:70; display:none; overflow:hidden; max-height:340px; overflow-y:auto }
#scr-savedmenu.open { display:block }
.scr-savedrow { display:flex; align-items:center; gap:8px; padding:8px 10px 8px 13px;
  font-size:12px; border-top:0.5px solid #f1efea }
.scr-savedrow:first-child { border-top:none }
.scr-savedrow:hover { background:#E6F1FB }
.scr-savedrow .nm { flex:1; cursor:pointer; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis }
.scr-savedrow .x { flex-shrink:0; border:none; background:rgba(0,0,0,.06); color:#52514e;
  border-radius:50%; width:18px; height:18px; line-height:16px; font-size:12px; padding:0;
  cursor:pointer }
.scr-savedrow .x:hover { background:#FCEBEB; color:#791F1F }
.scr-savedempty { padding:13px; font-size:11.5px; color:#898781; line-height:1.45 }
.scr-wrow { display:flex; align-items:center; gap:9px; margin-bottom:8px }
.scr-wrow label { font-size:12px; flex:1 }
.scr-wrow input[type=range] { flex:1.4 }
.scr-wrow .pct { font-size:11px; font-weight:600; width:38px; text-align:right }
.scr-tablewrap { background:white; border:0.5px solid #e1e0d9; border-radius:12px;
  overflow-x:auto }
.scr-tablewrap td.hit { background:#E1F5EE; font-weight:600 }
</style>
"""


_MODALS = """
<dialog id="scr-modal-picker">
  <div class="modal-body" style="min-width:430px;max-width:520px">
    <h3>Add a condition</h3>
    <input id="scr-fieldsearch" placeholder="Filter fields…" oninput="scrRenderPicker()"
           style="width:100%;margin-bottom:10px">
    <div id="scr-fieldlist" style="max-height:270px;overflow-y:auto;
         border:0.5px solid #e1e0d9;border-radius:8px"></div>
    <div id="scr-condform" style="margin-top:12px;display:none">
      <div style="font-size:12px;font-weight:600;margin-bottom:8px" id="scr-condlabel"></div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <select id="scr-condop" onchange="scrPickerOpChanged()"></select>
        <span id="scr-condvaluewrap"></span>
        <label style="font-size:11px;display:flex;align-items:center;gap:5px">
          <input type="checkbox" id="scr-condneg"> NOT
        </label>
      </div>
      <div id="scr-condhint" style="font-size:11px;color:#898781;margin-top:8px"></div>
    </div>
    <div class="modal-actions">
      <button class="btn secondary" onclick="closeModal('scr-modal-picker')">Cancel</button>
      <button class="btn" id="scr-condsubmit"
              onclick="scrAddCondition()">Add condition</button>
    </div>
  </div>
</dialog>

<dialog id="scr-modal-save">
  <form class="modal-body" onsubmit="scrSave(event)" style="min-width:340px">
    <h3>Save this search</h3>
    <input id="scr-savename" placeholder="e.g. AI Leaders" style="width:100%" required>
    <div style="font-size:11px;color:#898781;margin-top:8px" id="scr-savepreview"></div>
    <div class="modal-actions">
      <button type="button" class="btn secondary"
              onclick="closeModal('scr-modal-save')">Cancel</button>
      <button type="submit" class="btn">Save</button>
    </div>
  </form>
</dialog>

<dialog id="scr-modal-weights">
  <div class="modal-body" style="min-width:400px">
    <h3>Weighted composite</h3>
    <div style="font-size:11px;color:#898781;margin-bottom:14px">
      Ranks matches by a blend of these fields, independent of which
      conditions you filtered on. Pick “Weighted composite” in the Sort
      menu to order results by it.
    </div>
    <div id="scr-weightrows"></div>
    <div class="modal-actions">
      <button class="btn secondary" onclick="scrResetWeights()">Reset</button>
      <button class="btn" onclick="closeModal('scr-modal-weights');scrRun()">Apply</button>
    </div>
  </div>
</dialog>
"""


# The controller. Kept as one string (the app has no bundler and every other
# page does the same) but organised in the same order as the page reads:
# state, rules, presets/saved, run, render.
_JS = r"""
// ── state ───────────────────────────────────────────────────────────────────
// RULES is the single source of truth for the screen; every interaction
// mutates it and calls scrRun(), which re-posts the whole tree. There's no
// incremental filtering on the client — the engine owns matching, so the
// client never has a second opinion about what a rule means.
let META = null;
let RULES = { op: 'AND', negate: false, items: [] };
let WEIGHTS = {};
let LAST = null;
let VIEW = 'cards';
let PICKED = null;
let SUGI = -1;

function scrEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function scrInit() {
  try {
    const res = await fetch('/api/screener/meta');
    META = await res.json();
  } catch (e) { toast('Could not load screener fields', 'err'); return; }
  WEIGHTS = Object.assign({}, META.composite_defaults);
  document.getElementById('scr-universe').textContent = META.universe;
  scrRenderPresetTabs();
  scrRenderPresets();
  scrRenderSaved();
  scrRun();
}

// ── rules ───────────────────────────────────────────────────────────────────
function scrField(key) { return (META.fields || []).find(f => f.key === key); }

function scrAdd(conds, replace) {
  if (replace) RULES = { op: RULES.op, negate: false, items: [] };
  for (const c of conds) {
    // Same field+operator twice is a correction, not a second rule.
    const i = RULES.items.findIndex(
      x => !x.items && x.field === c.field && x.op === c.op);
    if (i >= 0) RULES.items[i] = c; else RULES.items.push(c);
  }
  scrRun();
}

function scrRemove(i) { RULES.items.splice(i, 1); scrRun(); }
function scrToggleJoin() { RULES.op = RULES.op === 'AND' ? 'OR' : 'AND'; scrRun(); }
function scrToggleNeg(i) {
  RULES.items[i].negate = !RULES.items[i].negate; scrRun();
}
function scrReset() {
  RULES = { op: 'AND', negate: false, items: [] };
  document.getElementById('scr-nl').value = '';
  scrRun();
}

function scrRenderRules() {
  const box = document.getElementById('scr-rules');
  if (!RULES.items.length) {
    box.innerHTML = '<div style="font-size:12px;color:#898781">' +
      'No conditions yet — pick a preset, type a search, or use ' +
      '<b>+ Add condition</b>. An empty screen lists the whole library.</div>';
    return;
  }
  const stats = {};
  (LAST && LAST.stats || []).forEach(s => { stats[s.index] = s; });
  const parts = RULES.items.map((c, i) => {
    const st = stats[i];
    // "alone: N" is how many tickers the rule matches by itself — the
    // cheapest way to see which rule is doing the damage.
    const n = st ? ` <span class="n">${st.alone}</span>` : '';
    const pill = `<span class="scr-pill${c.negate ? ' neg' : ''}" title="${
      st ? scrEsc(st.text) + ' — matches ' + st.alone + ' on its own; drop it and the screen returns ' + st.without : ''}">
        <span onclick="scrEditRule(${i})" style="cursor:pointer"
              title="Click to change the operator or value">${
          scrEsc(st ? st.text : (c.field || ''))}${n}</span>
        <button class="x" onclick="scrRemove(${i})">×</button></span>`;
    const join = i < RULES.items.length - 1
      ? `<span class="scr-joiner" onclick="scrToggleJoin()">${RULES.op}</span>` : '';
    return pill + join;
  });
  box.innerHTML = parts.join('') +
    '<div style="font-size:10px;color:#898781;margin-top:7px">' +
    'Click a pill to edit it · click ' + RULES.op + ' to switch to ' +
    (RULES.op === 'AND' ? 'OR' : 'AND') + ' · the small number is how many ' +
    'tickers that rule matches on its own</div>';
}

// ── presets & saved ─────────────────────────────────────────────────────────
let presetGroup = '';        // '' = all groups

// Saved searches are shown as presets in their own group. They are the
// same thing from the user's side — a named screen with a count — and
// keeping them only in a dropdown hid the ones you actually built.
const MY_SCREENS = 'My screens';

function scrAllPresets() {
  const saved = (META.saved || []).map(s => ({
    key: 'saved:' + s.name, icon: s.icon || '⭐', name: s.name,
    desc: (s.pills || []).join('  AND  ') || 'Saved screen',
    group: MY_SCREENS, count: s.count, saved: true,
    conditions: ((s.rules || {}).items || []).filter(i => !i.items),
    pills: s.pills || [], strategy: s.strategy_mode || null,
  }));
  return saved.concat(META.presets || []);
}

function scrRenderPresetTabs() {
  const all = scrAllPresets();
  const groups = [MY_SCREENS, ...(META.preset_groups || [])];
  const counts = {};
  for (const p of all) counts[p.group] = (counts[p.group] || 0) + 1;
  const tabs = [['', 'All', all.length],
                ...groups.map(g => [g, g, counts[g] || 0])].filter(t => t[2]);
  document.getElementById('scr-presettabs').innerHTML = tabs.map(([val, label, n]) =>
    `<button class="scr-ptab${presetGroup === val ? ' on' : ''}"
       onclick="scrSetPresetGroup('${val}')">${scrEsc(label)} ${n}</button>`).join('');
}

function scrSetPresetGroup(g) {
  presetGroup = g;
  scrRenderPresetTabs();
  scrRenderPresets();
}

function scrRenderPresets() {
  const q = (document.getElementById('scr-presetsearch').value || '').trim().toLowerCase();
  const all = scrAllPresets();
  const shown = all.filter(p =>
    (!presetGroup || p.group === presetGroup) &&
    (!q || p.name.toLowerCase().includes(q) || p.desc.toLowerCase().includes(q) ||
     (p.pills || []).some(t => t.toLowerCase().includes(q))));
  document.getElementById('scr-presetcount').textContent =
    `${shown.length} of ${all.length} presets`;
  document.getElementById('scr-presets').innerHTML = shown.length
    ? shown.map(p =>
        // count === 0 is dimmed rather than hidden: the screen is valid, the
        // library just has nothing matching it right now, and hiding it would
        // look like the preset doesn't exist.
        `<div class="scr-preset${p.count === 0 ? ' zero' : ''}"
              onclick="scrApplyPreset(this.dataset.k)" data-k="${scrEsc(p.key)}"
              title="${scrEsc(p.pills.join('  AND  '))}${p.count === 0 ?
                '\n\nNothing in the library matches this right now.' : ''}">
           <span class="n">${p.count}</span>
           <b>${p.icon} ${scrEsc(p.name)}</b>
           <span class="d">${scrEsc(p.desc)}</span></div>`).join('')
    : '<div class="scr-presetempty">No preset matches that search.</div>';
}

function scrApplyPreset(key) {
  const p = scrAllPresets().find(x => x.key === key);
  if (!p) return;
  RULES = { op: 'AND', negate: false, items: p.conditions.map(c => Object.assign({}, c)) };
  document.getElementById('scr-nl').value = '';
  const savedDef = p.saved
    ? (META.saved || []).find(s => s.name === p.name) : null;
  document.getElementById('scr-sort').value =
    (savedDef && savedDef.sort) || 'match';
  // The preset picks the strategy too. A screen selecting on setup quality
  // must have its verdicts gated on the setup score, or the page
  // contradicts itself — Swing Ready picking ALL on swing 82, then
  // labelling it AVOID off a long-term score of 53.
  const sel = document.getElementById('scr-strategy');
  if (sel && p.strategy) sel.value = p.strategy;
  scrRun();
}

function scrToggleSaved(ev) {
  if (ev) ev.stopPropagation();       // don't hit the close-on-outside-click
  document.getElementById('scr-savedmenu').classList.toggle('open');
}

function scrCloseSaved() {
  const m = document.getElementById('scr-savedmenu');
  if (m) m.classList.remove('open');
}

function scrRenderSaved() {
  const saved = (META && META.saved) || [];
  const count = document.getElementById('scr-savedcount');
  if (count) count.textContent = '(' + saved.length + ')';
  const box = document.getElementById('scr-savedmenu');
  if (!box) return;
  // Handlers take the index, not the name: a saved search called
  // Bill's picks would otherwise close the inlined JS string early, and
  // HTML-escaping the quote doesn't help because the attribute value is
  // unescaped back to ' before the JS parser ever sees it.
  box.innerHTML = saved.length
    ? saved.map((s, i) =>
        `<div class="scr-savedrow">
           <span class="nm" onclick="scrLoadSaved(${i})"
                 title="${scrEsc(s.name)}">${s.icon || '⭐'} ${scrEsc(s.name)}</span>
           <button class="x" title="Delete this saved search"
                   onclick="scrDeleteSaved(${i}, event)">×</button>
         </div>`).join('')
    : '<div class="scr-savedempty">Nothing saved yet — build a screen ' +
      'and hit ☆ Save search.</div>';
}

function scrLoadSaved(i) {
  const s = (META.saved || [])[i];
  scrCloseSaved();
  if (!s) return;
  RULES = JSON.parse(JSON.stringify(s.rules || { op: 'AND', items: [] }));
  if (!RULES.items) RULES = { op: 'AND', negate: false, items: [] };
  if (s.sort) document.getElementById('scr-sort').value = s.sort;
  document.getElementById('scr-nl').value = '';
  scrRun();
}

function scrOpenSave() {
  document.getElementById('scr-savepreview').textContent =
    RULES.items.length ? (LAST ? LAST.pills.map(p => p.text).join('  ' + RULES.op + '  ') : '')
                       : 'This screen has no conditions.';
  openModal('scr-modal-save');
}

async function scrSave(ev) {
  ev.preventDefault();
  const name = document.getElementById('scr-savename').value.trim();
  if (!name) return false;
  const res = await fetch('/api/screener/save', {
    method: 'POST',
    body: JSON.stringify({ name, rules: RULES,
                           sort: document.getElementById('scr-sort').value }),
  });
  const data = await res.json();
  if (data.ok) { META.saved = data.saved; scrRenderSaved(); toast(data.message, 'ok'); }
  else toast(data.message || 'Save failed', 'err');
  closeModal('scr-modal-save');
  document.getElementById('scr-savename').value = '';
  return false;
}

async function scrDeleteSaved(i, ev) {
  if (ev) ev.stopPropagation();      // deleting must not also load the row
  const s = (META.saved || [])[i];
  if (!s) return;
  const res = await fetch('/api/screener/delete', {
    method: 'POST', body: JSON.stringify({ name: s.name }),
  });
  const data = await res.json();
  if (data.ok) { META.saved = data.saved; scrRenderSaved(); toast(data.message, 'ok'); }
  else toast(data.message || 'Delete failed', 'err');
}

// ── ticker lookup ──────────────────────────────────────────────────────────
// The inverse of a screen: not "what matches these rules" but "which of the
// 62 screens does this name already qualify for". A ticker in twelve
// presets is a different proposition from one scraping into a single loose
// screen, and no individual result list shows that.
async function scrLookup() {
  const box = document.getElementById('scr-lookupresult');
  const q = document.getElementById('scr-lookup').value.trim();
  if (!q) { box.innerHTML = ''; return; }
  box.innerHTML = '<div style="font-size:11px;color:#898781;padding:6px">Looking up…</div>';
  let d;
  try {
    d = await (await fetch('/api/screener/ticker?q=' + encodeURIComponent(q))).json();
  } catch (e) { box.innerHTML = ''; toast('Lookup failed: ' + e, 'err'); return; }

  if (!d.ok) {
    const near = (d.suggestions || []).length
      ? ' Did you mean ' + d.suggestions.map(scrEsc).join(', ') + '?' : '';
    box.innerHTML = `<div class="scr-note">${scrEsc(d.message)}.${near}</div>`;
    return;
  }

  const byGroup = {};
  for (const p of d.presets) (byGroup[p.group] = byGroup[p.group] || []).push(p);
  const chips = Object.keys(byGroup).map(g => `
    <div style="margin-bottom:5px"><span style="font-size:10px;color:#898781;
      text-transform:uppercase;letter-spacing:.3px">${scrEsc(g)}</span><br>
      ${byGroup[g].map(p => `<span class="chip" style="cursor:pointer;margin:2px 4px 2px 0"
        onclick="scrApplyPreset(this.dataset.k)" data-k="${scrEsc(p.key)}"
        title="Open this screen">${p.icon} ${scrEsc(p.name)}</span>`).join('')}
    </div>`).join('');

  const none = !d.presets.length
    ? `<div style="font-size:11.5px;color:#898781">Qualifies for none of the
       ${d.total_presets} presets — it is in the library but not in any
       prebuilt screen.</div>` : '';
  const stale = d.recovered
    ? `<div style="font-size:10.5px;color:#633806;margin-top:5px">↺ values as of
       ${scrEsc((d.data_as_of || '').slice(0, 16))} — recovered from the snapshot</div>` : '';

  box.innerHTML = `<div style="background:white;border:0.5px solid #e1e0d9;
    border-radius:11px;padding:12px 15px;margin-bottom:12px">
    <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
      <a class="scr-tk" href="/research/${scrEsc(d.ticker)}.html">${scrEsc(d.ticker)}</a>
      <span style="font-size:11.5px;color:#898781">${scrEsc(d.name || '')}</span>
      <span class="chip" style="margin:0">${scrEsc(d.sector || '')}</span>
      ${d.category ? `<span class="chip" style="margin:0">${scrEsc(d.category)}</span>` : ''}
      ${d.buy_zone_label ? `<span class="chip" style="margin:0">${scrEsc(d.buy_zone_label)}</span>` : ''}
      <span style="margin-left:auto;font-size:11px;color:#898781">
        Appears in <b style="color:#0C447C">${d.presets.length}</b> of ${d.total_presets} screens</span>
    </div>
    <div class="scr-meta" style="margin-top:7px">
      <span>Inv <b>${d.investment ?? '—'}</b></span>
      <span>Swing <b>${d.swing ?? '—'}</b></span>
      <span>Confluence <b>${d.confluence}/10</b></span>
      <span>Long-term <b>${scrEsc(d.actions.LONGTERM)}</b></span>
      <span>Swing <b>${scrEsc(d.actions.SWING)}</b></span>
    </div>
    <div style="margin-top:9px">${chips}${none}</div>${stale}
    <div style="font-size:10px;color:#898781;margin-top:4px">
      Click a screen to open it.</div>
  </div>`;
}

// ── natural language ────────────────────────────────────────────────────────
let scrSugTimer = null;
function scrOnType(v) {
  clearTimeout(scrSugTimer);
  scrSugTimer = setTimeout(() => scrSuggest(v), 140);
}

async function scrSuggest(q) {
  const box = document.getElementById('scr-suggest');
  const term = (q || '').trim();
  // Only suggest on the last clause — "turnaround and qual" should offer
  // quality, not re-offer turnaround.
  const tail = term.split(/,|\band\b|\+/).pop().trim();
  if (tail.length < 2) { box.style.display = 'none'; return; }
  try {
    const res = await fetch('/api/screener/suggest?q=' + encodeURIComponent(tail));
    const rows = await res.json();
    if (!rows.length) { box.style.display = 'none'; return; }
    SUGI = -1;
    box.innerHTML = rows.map((r, i) =>
      `<div class="scr-sug" data-i="${i}" onclick="scrPickSuggestion(${i})">
         <span>${scrEsc(r.label)}</span><span class="k">${r.kind}</span></div>`).join('');
    box._rows = rows;
    box.style.display = 'block';
  } catch (e) { box.style.display = 'none'; }
}

function scrPickSuggestion(i) {
  const box = document.getElementById('scr-suggest');
  const r = (box._rows || [])[i];
  box.style.display = 'none';
  if (!r) return;
  // A suggestion with no threshold set (e.g. "Quality Score…") is an
  // invitation to pick one, not a rule — open the picker on that field.
  const bare = r.conditions.filter(c => c.value === null || c.value === undefined);
  if (bare.length === 1 && r.conditions.length === 1) {
    document.getElementById('scr-nl').value = '';
    scrOpenPicker(bare[0].field);
    return;
  }
  document.getElementById('scr-nl').value = '';
  scrAdd(r.conditions.map(c => Object.assign({}, c)), false);
}

function scrRunNL() {
  const q = document.getElementById('scr-nl').value.trim();
  document.getElementById('scr-suggest').style.display = 'none';
  if (!q) { scrRun(); return; }
  scrRun({ query: q });
}

document.addEventListener('keydown', (e) => {
  const box = document.getElementById('scr-suggest');
  if (!box || box.style.display === 'none') return;
  const rows = box.querySelectorAll('.scr-sug');
  if (!rows.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    SUGI = (SUGI + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length;
    rows.forEach((r, i) => r.classList.toggle('on', i === SUGI));
  } else if (e.key === 'Enter' && SUGI >= 0) {
    e.preventDefault(); scrPickSuggestion(SUGI);
  } else if (e.key === 'Escape') { box.style.display = 'none'; }
});
document.addEventListener('click', (e) => {
  const box = document.getElementById('scr-suggest');
  if (box && !e.target.closest('#scr-nl') && !e.target.closest('#scr-suggest'))
    box.style.display = 'none';
  if (!e.target.closest('#scr-savedwrap')) scrCloseSaved();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') scrCloseSaved();
});

// ── field picker ────────────────────────────────────────────────────────────
let EDIT_INDEX = null;      // rule being edited, or null when adding

function scrEditRule(i) {
  const c = RULES.items[i];
  if (!c || c.items) return;
  EDIT_INDEX = i;
  scrOpenPicker(c.field, c);
}

function scrOpenPicker(preselect, existing) {
  if (!existing) EDIT_INDEX = null;
  document.getElementById('scr-fieldsearch').value = '';
  PICKED = null;
  document.getElementById('scr-condform').style.display = 'none';
  scrRenderPicker();
  openModal('scr-modal-picker');
  if (preselect) scrPickField(preselect);
  const submit = document.getElementById('scr-condsubmit');
  if (submit) submit.textContent = existing ? 'Update rule' : 'Add condition';
  if (existing) {
    // Prefill so editing changes one thing rather than retyping the rule.
    const op = document.getElementById('scr-condop');
    if (op) { op.value = existing.op; scrPickerOpChanged(); }
    const v = document.getElementById('scr-condvalue');
    if (v && existing.value !== null && existing.value !== undefined) {
      v.value = existing.value;
    }
    const v2 = document.getElementById('scr-condvalue2');
    if (v2 && existing.value2 != null) v2.value = existing.value2;
    document.getElementById('scr-condneg').checked = !!existing.negate;
  }
}

function scrRenderPicker() {
  const q = document.getElementById('scr-fieldsearch').value.trim().toLowerCase();
  const list = document.getElementById('scr-fieldlist');
  let html = '';
  for (const g of META.groups) {
    const fields = META.fields.filter(f => f.group === g &&
      (!q || f.label.toLowerCase().includes(q) || f.key.includes(q)));
    if (!fields.length) continue;
    html += `<div style="font-size:10px;text-transform:uppercase;letter-spacing:.3px;
              color:#898781;padding:7px 12px 4px;background:#faf9f5">${g}</div>`;
    html += fields.map(f =>
      `<div onclick="scrPickField('${f.key}')" style="padding:7px 12px;font-size:12px;
            cursor:pointer;border-top:0.5px solid #f1efea"
            onmouseover="this.style.background='#E6F1FB'"
            onmouseout="this.style.background=''">
         ${scrEsc(f.label)}${f.hint ? '<div style="font-size:10px;color:#898781">' +
           scrEsc(f.hint) + '</div>' : ''}</div>`).join('');
  }
  list.innerHTML = html || '<div style="padding:14px;font-size:12px;color:#898781">no fields match</div>';
}

function scrPickField(key) {
  const f = scrField(key);
  if (!f) return;
  PICKED = f;
  document.getElementById('scr-condform').style.display = 'block';
  document.getElementById('scr-condlabel').textContent = f.label;
  document.getElementById('scr-condhint').textContent = f.hint || '';
  document.getElementById('scr-condop').innerHTML = f.ops.map(o =>
    `<option value="${o}">${scrEsc(META.operators[o] || o)}</option>`).join('');
  scrPickerOpChanged();
}

function scrPickerOpChanged() {
  const f = PICKED;
  if (!f) return;
  const op = document.getElementById('scr-condop').value;
  const wrap = document.getElementById('scr-condvaluewrap');
  if (f.kind === 'bool') {
    wrap.innerHTML = `<select id="scr-condvalue">
        <option value="true">Yes</option><option value="false">No</option></select>`;
  } else if (f.kind === 'enum' || f.kind === 'list') {
    // list = the row holds many values (watchlists); the picker still
    // chooses one, the engine tests membership rather than equality.
    const vals = (META.enums && META.enums[f.key]) || f.values || [];
    wrap.innerHTML = `<select id="scr-condvalue">${vals.map(v =>
      `<option value="${scrEsc(v)}">${scrEsc(v)}</option>`).join('')}</select>`;
  } else if (op === 'between') {
    wrap.innerHTML = `<input id="scr-condvalue" type="number" step="any" placeholder="from"
        style="width:88px"> <input id="scr-condvalue2" type="number" step="any"
        placeholder="to" style="width:88px">`;
  } else {
    wrap.innerHTML = `<input id="scr-condvalue" type="number" step="any"
        placeholder="value" style="width:104px">${f.unit ?
        ' <span style="font-size:11px;color:#898781">' + scrEsc(f.unit) + '</span>' : ''}`;
  }
}

function scrAddCondition() {
  if (!PICKED) { toast('Pick a field first', 'err'); return; }
  const op = document.getElementById('scr-condop').value;
  const el = document.getElementById('scr-condvalue');
  let value = el ? el.value : null;
  if (PICKED.kind === 'bool') value = value === 'true';
  else if (PICKED.kind !== 'enum' && PICKED.kind !== 'list') {
    if (value === '' || value === null) { toast('Enter a value', 'err'); return; }
    value = parseFloat(value);
    if (isNaN(value)) { toast('Enter a number', 'err'); return; }
  }
  const cond = { field: PICKED.key, op, value,
                 negate: document.getElementById('scr-condneg').checked };
  const v2 = document.getElementById('scr-condvalue2');
  if (v2 && v2.value !== '') cond.value2 = parseFloat(v2.value);
  document.getElementById('scr-condneg').checked = false;
  closeModal('scr-modal-picker');
  if (EDIT_INDEX !== null) {
    RULES.items[EDIT_INDEX] = cond;
    EDIT_INDEX = null;
    scrRun();
    return;
  }
  scrAdd([cond], false);
}

// ── weights ─────────────────────────────────────────────────────────────────
function scrOpenWeights() {
  const box = document.getElementById('scr-weightrows');
  const keys = Object.keys(WEIGHTS);
  box.innerHTML = keys.map(k => {
    const f = scrField(k);
    return `<div class="scr-wrow">
      <label>${scrEsc(f ? f.label : k)}</label>
      <input type="range" min="0" max="100" value="${WEIGHTS[k]}"
             oninput="scrSetWeight('${k}', this.value)">
      <span class="pct" id="scr-w-${k}">${WEIGHTS[k]}%</span></div>`;
  }).join('');
  openModal('scr-modal-weights');
}
function scrSetWeight(k, v) {
  WEIGHTS[k] = parseFloat(v);
  document.getElementById('scr-w-' + k).textContent = v + '%';
}
function scrResetWeights() {
  WEIGHTS = Object.assign({}, META.composite_defaults);
  scrOpenWeights();
}

// ── run ─────────────────────────────────────────────────────────────────────
let scrSeq = 0;
async function scrRun(extra) {
  const seq = ++scrSeq;
  const payload = Object.assign({
    rules: RULES, composite: WEIGHTS,
    strategy: (document.getElementById('scr-strategy') || {}).value || 'LONGTERM',
    sort: document.getElementById('scr-sort').value, limit: 200,
  }, extra || {});
  document.getElementById('scr-livecount').textContent = '…';
  let data;
  try {
    const res = await fetch('/api/screen', {
      method: 'POST', body: JSON.stringify(payload),
    });
    data = await res.json();
  } catch (e) {
    document.getElementById('scr-results').innerHTML =
      '<div class="scr-empty">Request failed — is the server still running?</div>';
    return;
  }
  // A slow earlier request must not overwrite a newer result.
  if (seq !== scrSeq) return;
  if (!data.ok) { toast(data.message || 'Screen failed', 'err'); return; }
  LAST = data;
  scrRenderActionPicker(data);
  scrRenderBuyZonePicker(data);
  // A natural-language search comes back as real rules, so the pills and
  // every later edit operate on what the parser actually understood.
  if (extra && extra.query) RULES = data.rules;
  // A new result set makes any drill-down stale — the group it named may
  // not exist in these rows.
  resultFilter = null;
  scrRenderRules();
  scrRenderSummary(data);
  scrRenderResults(data);
}

// ── render ──────────────────────────────────────────────────────────────────
function scrRenderSummary(d) {
  document.getElementById('scr-livecount').textContent =
    d.count + ' / ' + d.universe;
  document.getElementById('scr-resulttitle').textContent =
    d.count === 0 ? 'No stocks matched'
                  : 'Found ' + d.count + ' stock' + (d.count === 1 ? '' : 's') +
                    (d.shown < d.count ? ' — showing top ' + d.shown : '');

  const s = d.summary || {};
  const tiles = [
    ['Avg Quality', s.avg_quality], ['Avg Health', s.avg_health],
    ['Avg EPS Growth', s.avg_eps_growth, '%'], ['Avg RS', s.avg_rs],
    ['Avg Conviction', s.avg_conviction],
    ['Avg Breakout', s.avg_breakout, '%'],
  ].filter(t => t[1] !== null && t[1] !== undefined);
  let html = '';
  if (d.count) {
    html = '<div class="scr-stats">' + tiles.map(t =>
      `<div class="scr-stat"><div class="v">${t[1]}${t[2] || ''}</div>
         <div class="l">${t[0]}</div></div>`).join('') +
      scrCountTile('above200', 'Above 200MA', s.above_200ma) +
      scrCountTile('buyzone', 'Buy Zone',
        (s.strong_buy_zone || 0) + (s.in_buy_zone || 0),
        `<span style="font-size:11px;color:#898781">(${s.strong_buy_zone || 0} strong)</span>`,
        'Buy Zone label from core/buy_zone.py — purely technical entry quality') +
      scrCountTile('watchlist', 'Watch List', s.watch_list || 0) +
      scrCountTile('earnings', 'Earnings ≤7d', s.earnings_soon) +
      scrStrategyTile(d) + scrActionTiles(d) + '</div>';
  }
  document.getElementById('scr-summary').innerHTML = html;

  // Coverage gaps, always shown when they exist: a rule on a field that
  // half the library lacks silently excludes those names, and that looks
  // exactly like a real rejection unless we say so.
  const miss = (d.missing || []).filter(m => m.rows > 0);
  let notes = miss.length
    ? '<div class="scr-note">⚠ Excluded for missing data: ' + miss.map(m =>
        `<b>${scrEsc(m.label)}</b> — no value for ${m.rows} of ${d.universe} tickers`)
        .join(' · ') + '. These are not rejections; the library has no data ' +
        'for them on this field.</div>'
    : '';
  // If the live index lost data and the snapshot is carrying the screen,
  // say so up front — the results are usable but not all of them are live.
  const rec = (d.summary || {}).recovered || 0;
  if (rec) {
    notes += `<div class="scr-note">↺ <b>${rec}</b> of ${d.count} result` +
      (d.count === 1 ? '' : 's') + ' came from the durable snapshot because ' +
      'the research index has no data for them. Values are real but as of ' +
      'their last good scan — each is badged with its date. Run a scan or ' +
      'Refresh Research to bring them current.</div>';
  }
  document.getElementById('scr-missing').innerHTML = notes;

  const ref = d.refine || [];
  document.getElementById('scr-refine').innerHTML = ref.length
    ? '<div class="scr-refine"><b>Refine:</b>' + ref.map((r, i) =>
        `<span>${scrEsc(r.why)}</span>
         <button class="btn secondary" style="padding:4px 10px"
           onclick='scrAdd(${JSON.stringify(r.conditions)}, false)'>${scrEsc(r.label)}</button>`)
        .join('') + '</div>'
    : '';
}

// Which score gated the actions below. Without it a red AVOID on a stock
// the screen deliberately selected reads as a contradiction rather than as
// "avoid for THIS purpose" — see the ALL case: swing 82, long-term 53.
function scrStrategyTile(d) {
  const label = {LONGTERM: 'Long-term', SWING: 'Swing', BALANCED: 'Balanced'}[d.strategy]
    || d.strategy || '—';
  const sub = {LONGTERM: 'gated on company quality',
               SWING: 'gated on setup quality',
               BALANCED: 'needs both'}[d.strategy] || '';
  return `<div class="scr-stat" style="background:#E6F1FB;border-color:#cfe2f5"
    title="${sub}"><div class="v" style="font-size:15px;color:#0C447C">${label}</div>
    <div class="l">Strategy</div></div>`;
}

// Decision counts, actionable first — the point of the layer is to shrink a
// long list to the few worth acting on, so the counts lead with those.
function scrActionTiles(d) {
  const counts = d.actions || {};
  const order = ['BUY NOW', 'BREAKOUT ENTRY', 'BUY ZONE', 'BUY ON PULLBACK',
                 'WATCH', 'WAIT', 'SPECULATIVE'];
  const shown = order.filter(a => counts[a]);
  if (!shown.length) return '';
  return shown.map(a => {
    const [bg, fg] = SCR_ACTION_TONE[a] || ['#f1efea', '#52514e'];
    const on = resultFilter && resultFilter.value === a;
    return `<div class="scr-stat" onclick="scrSetResultFilter('action', '${a}')"
      title="Click to show only these"
      style="background:${bg};cursor:pointer;border-color:${on ? fg : 'transparent'}">
      <div class="v" style="color:${fg}">${counts[a]}</div>
      <div class="l" style="color:${fg};opacity:.85">${scrEsc(a)}</div></div>`;
  }).join('');
}

// ── drill-down from the summary ────────────────────────────────────────────
// The tiles describe the current result set, so clicking one narrows the
// list to that subset rather than re-running the screen — the numbers and
// the rows below them stay the same population, which is the only way the
// count can be trusted to match what you then see.
let resultFilter = null;      // {kind, value} or null

const SCR_TILE_TESTS = {
  above200: r => r.above_200ma === true,
  // No value = both buy-zone tiers (what the summary tile counts); with a
  // value = that exact label, which is what the dropdown selects.
  buyzone:  (r, v) => v ? r.buy_zone_label === v
                        : (r.buy_zone_label === 'Buy Zone'
                           || r.buy_zone_label === 'Strong Buy Zone'),
  watchlist: r => r.buy_zone_label === 'Watch List',
  earnings: r => r.earnings_soon === true,
  action:   (r, v) => r.action === v,
};

function scrCountTile(kind, label, value, extra, title) {
  const on = resultFilter && resultFilter.kind === kind && !resultFilter.value;
  const dead = !value;      // nothing to drill into
  return `<div class="scr-stat${on ? ' on' : ''}"
    ${dead ? '' : `onclick="scrSetResultFilter('${kind}')"`}
    style="${dead ? '' : 'cursor:pointer'}${on ? ';border-color:#185FA5;background:#E6F1FB' : ''}"
    title="${title || (dead ? '' : 'Click to show only these')}">
    <div class="v">${value}${extra ? ' ' + extra : ''}</div>
    <div class="l">${label}</div></div>`;
}

function scrSetResultFilter(kind, value) {
  // A falsy kind clears — "Show all" and clicking the active tile again
  // both land here, and neither should leave a filter with no test behind.
  const same = resultFilter && resultFilter.kind === kind
               && resultFilter.value === value;
  resultFilter = (!kind || same) ? null : {kind, value};
  // The tiles and the two dropdowns are one control in three places, so
  // selecting in any of them clears the others rather than implying a
  // combined filter the engine never applied.
  for (const [id, kind] of [['scr-groupby', 'action'], ['scr-bzpick', 'buyzone']]) {
    const sel = document.getElementById(id);
    if (!sel) continue;
    const want = (resultFilter && resultFilter.kind === kind)
      ? (resultFilter.value || '') : '';
    sel.value = [...sel.options].some(o => o.value === want) ? want : '';
  }
  if (LAST) { scrRenderSummary(LAST); scrRenderResults(LAST); }
}

function scrFilteredResults(d) {
  if (!resultFilter) return d.results;
  const test = SCR_TILE_TESTS[resultFilter.kind];
  if (!test) return d.results;
  return d.results.filter(r => test(r, resultFilter.value));
}

function scrFilterLabel() {
  if (!resultFilter) return '';
  const names = {above200: 'Above 200MA', buyzone: 'Buy Zone',
                 watchlist: 'Watch List', earnings: 'Earnings ≤7d'};
  return resultFilter.value || names[resultFilter.kind] || resultFilter.kind;
}

// Options are the actions actually present in these results, with counts —
// listing every possible action would offer groups that are empty for this
// screen, and the count is the reason to pick one.
function scrRenderActionPicker(d) {
  const sel = document.getElementById('scr-groupby');
  if (!sel) return;
  const counts = d.actions || {};
  const order = ['BUY NOW', 'BREAKOUT ENTRY', 'BUY ZONE', 'BUY ON PULLBACK',
                 'WATCH', 'WAIT', 'SPECULATIVE', 'AVOID'];
  const current = sel.value;
  sel.innerHTML = '<option value="">All actions</option>' +
    order.filter(a => counts[a]).map(a =>
      `<option value="${a}">${scrEsc(a)} (${counts[a]})</option>`).join('');
  // Keep the selection if that action still exists in the new results.
  sel.value = [...sel.options].some(o => o.value === current) ? current : '';
}

// Ordered best-entry-first, not alphabetically — the ranking is the point
// of the label, and only the tiers present in these results are offered.
const SCR_BZ_ORDER = ['Strong Buy Zone', 'Buy Zone', 'Watch List',
                      'Hold / Monitor', 'Avoid'];

function scrRenderBuyZonePicker(d) {
  const sel = document.getElementById('scr-bzpick');
  if (!sel) return;
  const counts = {};
  for (const r of (d.results || [])) {
    if (r.buy_zone_label) counts[r.buy_zone_label] = (counts[r.buy_zone_label] || 0) + 1;
  }
  const current = sel.value;
  sel.innerHTML = '<option value="">All</option>' +
    SCR_BZ_ORDER.filter(k => counts[k]).map(k =>
      `<option value="${scrEsc(k)}">${scrEsc(k)} (${counts[k]})</option>`).join('');
  sel.value = [...sel.options].some(o => o.value === current) ? current : '';
}

function scrOnBuyZonePick(value) {
  scrSetResultFilter(value ? 'buyzone' : null, value || undefined);
}

function scrOnActionPick(value) {
  // Reuses the tile drill-down so the dropdown, the tiles and the banner
  // can never disagree about what is being shown.
  scrSetResultFilter(value ? 'action' : null, value || undefined);
}

function scrToggleView() {
  VIEW = VIEW === 'cards' ? 'table' : 'cards';
  document.getElementById('scr-viewbtn').textContent =
    VIEW === 'cards' ? 'Table view' : 'Card view';
  if (LAST) scrRenderResults(LAST);
}

function scrRenderResults(d) {
  const box = document.getElementById('scr-results');
  if (!d.results.length) {
    box.innerHTML = scrEmptyHelp(d);
    return;
  }
  const rows = scrFilteredResults(d);
  const banner = resultFilter
    ? `<div style="display:flex;gap:9px;align-items:center;background:#E6F1FB;
         color:#0C447C;border-radius:9px;padding:8px 12px;margin-bottom:10px;
         font-size:11.5px">Showing <b>${rows.length}</b> of ${d.results.length}
         — <b>${scrEsc(scrFilterLabel())}</b>
         <button class="btn secondary" style="font-size:10px;padding:3px 9px;
           margin-left:auto" onclick="scrSetResultFilter(null)">Show all</button></div>`
    : '';
  if (!rows.length) {
    box.innerHTML = banner + '<div class="scr-empty">No rows in this group.</div>';
    return;
  }
  // Sections only when showing every action — picking one already narrows
  // it, and a single-section header would be noise.
  const groupBy = (resultFilter && resultFilter.kind === 'action')
    ? '' : 'action';   // buy-zone picks stay grouped by action
  if (!groupBy || VIEW !== 'cards') {
    box.innerHTML = banner + (VIEW === 'cards'
      ? rows.map(scrCard).join('')
      : scrTable(Object.assign({}, d, {results: rows})));
    return;
  }
  box.innerHTML = banner + scrGrouped(rows, groupBy);
}

// Grouping keeps the current sort inside each section rather than
// re-sorting — the order you chose still means something within a group,
// and a section that reordered itself would make the sort control a lie.
const SCR_GROUPERS = {
  action:   r => r.action || 'No decision',
};

function scrGroupOrder(groupBy, keys) {
  // Actions run actionable-first, matching the engine's own ladder; the
  // rest are ordered by size, which is the only ordering that means
  // anything for a sector or a label.
  if (groupBy === 'action') {
    const rank = {};
    ['BUY NOW', 'BREAKOUT ENTRY', 'BUY ZONE', 'BUY ON PULLBACK',
     'WATCH', 'WAIT', 'SPECULATIVE', 'AVOID'].forEach((a, i) => { rank[a] = i; });
    return keys.slice().sort((a, b) =>
      (rank[a] ?? 99) - (rank[b] ?? 99) || a.localeCompare(b));
  }
  return keys;
}

function scrGrouped(rows, groupBy) {
  const fn = SCR_GROUPERS[groupBy];
  if (!fn) return rows.map(scrCard).join('');
  const buckets = {};
  for (const r of rows) (buckets[fn(r)] = buckets[fn(r)] || []).push(r);
  const keys = scrGroupOrder(groupBy, Object.keys(buckets));
  return keys.map(k => {
    const tone = groupBy === 'action'
      ? (SCR_ACTION_TONE[k] || ['#f1efea', '#52514e']) : ['#f1efea', '#52514e'];
    return `<div style="margin:14px 0 7px;display:flex;gap:9px;align-items:center">
        <span style="background:${tone[0]};color:${tone[1]};font-size:11.5px;
          font-weight:700;padding:4px 12px;border-radius:20px">${scrEsc(k)}</span>
        <span style="font-size:11px;color:#898781">${buckets[k].length}
          stock${buckets[k].length === 1 ? '' : 's'}</span>
        <span style="flex:1;height:1px;background:#e1e0d9"></span>
      </div>` + buckets[k].map(scrCard).join('');
  }).join('');
}

// An empty result is a question ("why?"), so answer it with the engine's
// per-condition stats instead of a shrug.
function scrEmptyHelp(d) {
  const stats = (d.stats || []).slice().sort((a, b) => a.alone - b.alone);
  if (!stats.length) return '<div class="scr-empty">No stocks matched.</div>';
  const rows = stats.map(s =>
    `<tr><td>${scrEsc(s.text)}</td>
       <td style="text-align:right">${s.alone}</td>
       <td style="text-align:right">${s.without}</td>
       <td style="text-align:right;color:#898781">${s.missing || 0}</td></tr>`).join('');
  const tightest = stats[0];
  return `<div class="scr-empty" style="text-align:left">
    <div style="font-size:13px;font-weight:600;margin-bottom:4px;color:#0b0b0b">
      No stocks matched all ${stats.length} conditions.</div>
    <div style="margin-bottom:12px">Tightest rule:
      <b style="color:#0b0b0b">${scrEsc(tightest.text)}</b> — only
      ${tightest.alone} of ${d.universe} tickers pass it on its own.</div>
    <table><thead><tr><th>Condition</th><th style="text-align:right">Matches alone</th>
      <th style="text-align:right">Screen without it</th>
      <th style="text-align:right">No data</th></tr></thead>
      <tbody>${rows}</tbody></table>
    <div style="margin-top:10px;font-size:11px">Remove or loosen the rule whose
      “screen without it” column is highest — that's the one costing you the most.</div>
  </div>`;
}

function scrNum(v, nd, unit) {
  if (v === null || v === undefined) return '—';
  return Number(v).toFixed(nd === undefined ? 1 : nd) + (unit || '');
}

function scrBig(v) {
  if (v === null || v === undefined) return '—';
  const a = Math.abs(v);
  for (const [cut, s] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M']])
    if (a >= cut) return '$' + (v / cut).toFixed(1) + s;
  return '$' + Math.round(v);
}

// ── decision layer (core/decision_engine.py) ────────────────────────────────
// The screener says which stocks match; these say what to do about each one.
// Both are shown: the match score is why it is on the list, the action is
// what the decision engine makes of it, and they answer different questions.
const SCR_ACTION_TONE = {
  'BUY NOW':         ['#E1F5EE', '#085041'],
  'BUY ZONE':        ['#E1F5EE', '#085041'],
  'BUY ON PULLBACK': ['#E1F5EE', '#085041'],
  'BREAKOUT ENTRY':  ['#E6F1FB', '#0C447C'],
  'WATCH':           ['#E6F1FB', '#0C447C'],
  'WAIT':            ['#FAEEDA', '#633806'],
  'SPECULATIVE':     ['#FAEEDA', '#633806'],
  'AVOID':           ['#FCEBEB', '#791F1F'],
};

// Both verdicts, labelled. "AVOID" alone is ambiguous — avoid owning it, or
// avoid trading it? They are frequently different answers about the same
// stock, and that difference is the useful part.
function scrOneBadge(label, action, icon, active, thin) {
  if (!action) return '';
  const [bg, fg] = SCR_ACTION_TONE[action] || ['#f1efea', '#52514e'];
  return `<span title="${label} verdict${active ? ' — the strategy in use' : ''}"
    style="background:${bg};color:${fg};font-size:10.5px;font-weight:700;
    padding:3px 9px;border-radius:20px;${active ? '' : 'opacity:.62;'}
    ${active ? 'box-shadow:0 0 0 1.5px ' + fg + '33;' : ''}">
    <span style="font-weight:600;opacity:.8">${label}</span>
    ${icon || ''} ${scrEsc(action)}${thin}</span>`;
}

function scrActionBadge(r) {
  if (!r.action) return '';
  // An action resting on thin data is marked, not hidden — the engine
  // already refuses to issue a buy in that case, but the caveat travels.
  const thin = r.decision_reliable === false
    ? ' <span title="Some scoring inputs are missing for this ticker">·⚠</span>' : '';
  const active = (LAST && LAST.strategy) || 'LONGTERM';
  return `<span style="display:inline-flex;gap:5px;align-items:center">` +
    scrOneBadge('Long-term', r.action_longterm, r.action_longterm_icon,
                active === 'LONGTERM', thin) +
    scrOneBadge('Swing', r.action_swing, r.action_swing_icon,
                active === 'SWING', thin) + `</span>`;
}

function scrDecisionRow(r) {
  if (!r.action) return '';
  const s = (label, v, title) => v == null ? '' :
    `<span title="${title}">${label} <b>${v}</b></span>`;
  const bits = [
    s('Inv', r.inv_score, 'Investment score — company quality, 0-100'),
    s('Swing', r.swing_dec, 'Swing score — setup quality, 0-100'),
    r.confluence == null ? '' :
      `<span title="Independent factors agreeing, out of 10">Confluence
        <b>${r.confluence}/10</b></span>`,
    r.earnings_risk && r.earnings_risk !== 'LOW' && r.earnings_risk !== 'UNKNOWN'
      ? `<span style="color:#8a6d1a">Earnings ${scrEsc(r.earnings_risk)}</span>` : '',
  ].filter(Boolean).join('');
  // "What would change this" is the point of a WATCH — without it the
  // verdict is just a label you have to re-derive tomorrow.
  const trig = (r.decision_triggers || []).length
    ? `<div style="font-size:10.5px;color:#0C447C;margin-top:3px">→ ${
        (r.decision_triggers || []).map(scrEsc).join(' · ')}</div>` : '';
  const risks = (r.decision_risks || []).length
    ? `<div style="font-size:10.5px;color:#8a6d1a;margin-top:3px">⚠ ${
        (r.decision_risks || []).map(scrEsc).join(' · ')}</div>` : '';
  return `<div class="scr-meta" style="margin-top:7px">${bits}</div>${trig}${risks}${
    scrPullback(r)}`;
}

// ── the pullback, priced (decision_engine.pullback_plan) ────────────────────
// "BUY ON PULLBACK" names a price you are not being offered yet, so the
// badge on its own is an instruction with the instruction missing. This
// panel is the answer to "a pullback to what, from what, and why wait" —
// every number comes off the row, and a level with no data is left out
// rather than filled in.
function scrPct(v, nd) {
  if (v === null || v === undefined) return '—';
  return (v > 0 ? '+' : '') + Number(v).toFixed(nd === undefined ? 1 : nd) + '%';
}

function scrPullback(r) {
  const p = r.pullback;
  if (!p) return '';
  const both = (p.strategies || []).length > 1;
  const who = both ? 'Both strategies say wait'
    : `${(p.strategies || ['The engine'])[0]} says wait`;

  const levels = (p.levels || []).map(l => `
    <tr>
      <td style="padding:2px 10px 2px 0;color:#0b0b0b;font-weight:600">${scrEsc(l.name)}</td>
      <td style="padding:2px 10px 2px 0;text-align:right">${
        l.price === null || l.price === undefined ? '—' : '$' + Number(l.price).toFixed(2)}</td>
      <!-- Deliberately not coloured by sign: being above the level is what
           you are waiting out here, so green-for-positive would read as
           approval of the thing the verdict is objecting to. -->
      <td style="padding:2px 10px 2px 0;text-align:right;color:#0b0b0b">${
        scrPct(l.pct)}</td>
      <td style="padding:2px 10px 2px 0;text-align:right;color:#52514e">${
        l.move === null || l.move === undefined ? '—' : scrPct(l.move)}</td>
      <td style="padding:2px 0;color:#898781">${scrEsc(l.note || '')}</td>
    </tr>`).join('');

  const ctx = (p.context || []).map(x =>
    `<span style="margin-right:12px">${scrEsc(x.label)}
      <b style="color:#0b0b0b">${scrEsc(x.value)}</b>${
      x.note ? ` <span style="color:#898781">(${scrEsc(x.note)})</span>` : ''}</span>`
  ).join('');

  // The gates it missed. On an extended name the wait is about price and
  // this is usually empty; on the catch-all pullback it IS the reason, and
  // leaving it out was what made that verdict unreadable.
  const gaps = (p.gaps || []).length ? `
    <div style="margin-top:6px;font-size:10.5px;color:#8a6d1a">
      Short of a full buy: ${(p.gaps || []).map(scrEsc).join(' · ')}</div>` : '';

  return `<details class="scr-pb" style="margin-top:6px">
    <summary style="cursor:pointer;font-size:11px;color:#085041;font-weight:600;
      list-style:none">⤵ ${scrEsc(who)} — ${scrEsc(p.headline)}</summary>
    <div style="margin-top:6px;padding:8px 10px;background:#f7f6f2;
      border-left:2px solid #0F6E56;border-radius:3px">
      <table style="font-size:11px;border-collapse:collapse;width:100%">
        <thead><tr style="color:#898781;font-size:10px;text-transform:uppercase">
          <th style="text-align:left;padding-bottom:3px">Level</th>
          <th style="text-align:right;padding-bottom:3px">Price</th>
          <th style="text-align:right;padding-bottom:3px" title="Signed distance of the current price from this level">Price is</th>
          <th style="text-align:right;padding-bottom:3px" title="Move from the current price needed to reach this level">Move to</th>
          <th style="text-align:left;padding-bottom:3px"></th>
        </tr></thead>
        <tbody>${levels || '<tr><td colspan="5" style="color:#898781">No moving-average levels on this row</td></tr>'}</tbody>
      </table>
      <div style="margin-top:7px;font-size:10.5px;color:#52514e">${ctx}</div>
      ${gaps}
    </div>
  </details>`;
}

function scrCard(r) {
  const why = (r.why || []).map(w =>
    `<div class="${w.passed ? '' : 'no'}">${w.passed ? '✓' : '✗'} ${scrEsc(w.text)}</div>`
  ).join('');
  const stars = r.conv_stars ? '★'.repeat(r.conv_stars) + '☆'.repeat(5 - r.conv_stars) : '';
  const meta = [
    ['Price', r.price === null ? '—' : '$' + scrNum(r.price, 2)],
    ['Quality', r.quality === null ? '—' : r.quality],
    ['Health', r.health === null ? '—' : r.health],
    ['Moat', r.moat === null ? '—' : r.moat + '/' + (r.moat_total || 4)],
    ['RS', r.rs_rank === null ? '—' : r.rs_rank],
    ['EPS gr', r.eps_growth === null ? '—' : scrNum(r.eps_growth, 0, '%')],
    ['Fwd P/E', r.forward_pe === null ? '—' : scrNum(r.forward_pe, 1)],
    ['Inst', r.inst_own === null ? '—' : scrNum(r.inst_own, 0, '%')],
  ].map(m => `<span>${m[0]} <b>${m[1]}</b></span>`).join('');
  const tags = [
    r.category, r.grade ? 'Grade ' + r.grade : '',
    r.in_buy_zone ? 'Buy Zone' : '', r.above_200ma ? '>200MA' : '',
    r.earnings_soon ? '⚠ earnings ≤7d' : '',
  ].filter(Boolean).map(t =>
    `<span class="chip" style="margin:0">${scrEsc(t)}</span>`).join(' ');
  // Recovered rows are real data from an earlier scan, not live values —
  // label them so a stale price is never read as a current one.
  const recovered = r.recovered
    ? `<span class="chip" style="margin:0;background:#FAEEDA;color:#633806"
         title="The live index had no data for this ticker; these values come from the durable snapshot, as of ${scrEsc(r.data_as_of || 'an earlier scan')}">
         ↺ as of ${scrEsc((r.data_as_of || '').slice(0, 16) || 'earlier scan')}</span>`
    : '';
  // The scan falls back to the ticker for LongName when yfinance has no
  // company name, so showing both would read "MU  MU".
  const name = (r.name && r.name !== r.ticker) ? r.name : '';
  return `<div class="scr-card">
    <div class="scr-cardtop">
      <a class="scr-tk" href="/research/${scrEsc(r.ticker)}.html">${scrEsc(r.ticker)}</a>
      <span style="font-size:11.5px;color:#898781;max-width:230px;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap">${scrEsc(name)}</span>
      ${tags}${recovered}
      <span style="font-size:11px;color:#8a6d1a">${stars}</span>
      ${scrActionBadge(r)}
      <div class="scr-ms"><div class="v">${r.match_score}</div>
        <div class="l">match</div></div>
    </div>
    ${scrDecisionRow(r)}
    <div class="scr-meta">${meta}</div>
    <div class="scr-why">${why}</div>
  </div>`;
}

// The table shows the fields the screen filtered on, highlighted — so the
// numbers you asked about are the numbers on screen.
function scrTable(d) {
  const cols = [];
  const seen = new Set();
  for (const p of (d.pills || [])) {
    if (seen.has(p.field)) continue;
    seen.add(p.field);
    const f = scrField(p.field);
    if (f) cols.push(f);
  }
  const head = cols.map(c => `<th>${scrEsc(c.label)}</th>`).join('');
  const rows = d.results.map(r => {
    const cells = cols.map(c => {
      const v = r[c.key];
      // Precision comes from the field registry — rounding Forward P/E to
      // a whole number turns 5.9 into "6" and loses the point of the filter.
      const txt = v === null || v === undefined ? '—'
        : (typeof v === 'boolean' ? (v ? 'Yes' : 'No')
          : (typeof v === 'number'
             ? (c.unit === '$' ? scrBig(v) : scrNum(v, c.decimals, c.unit))
             : scrEsc(v)));
      const hit = (r.matched_fields || []).includes(c.key);
      return `<td class="${hit ? 'hit' : ''}">${txt}</td>`;
    }).join('');
    return `<tr><td><a href="/research/${scrEsc(r.ticker)}.html"><b>${scrEsc(r.ticker)}</b></a></td>
      <td style="text-align:right;font-weight:600;color:#0F6E56">${r.match_score}</td>
      ${cells}<td>${scrEsc(r.category || '')}</td></tr>`;
  }).join('');
  return `<div class="scr-tablewrap"><table>
    <thead><tr><th>Ticker</th><th style="text-align:right">Match</th>
      ${head}<th>Category</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

// ── CSV export ──────────────────────────────────────────────────────────────
// Exports what the screen returned, not a fixed column set: the fields you
// filtered on lead, so the numbers that justify each row travel with it. The
// "Matched" column carries the engine's own per-condition text, which makes
// an exported screen self-explanatory once it's out of the app.
function scrCsvEscape(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

async function scrDownloadCsv() {
  if (!LAST || !LAST.results || !LAST.results.length) {
    toast('Nothing to export — this screen matched no stocks', 'err');
    return;
  }
  // The page renders a capped page of results; the file should be the whole
  // screen. Silently exporting the visible 200 of 540 would look complete.
  let results = LAST.results;
  if (LAST.count > results.length) {
    toast(`Fetching all ${LAST.count} matches…`);
    try {
      const res = await fetch('/api/screen', {
        method: 'POST',
        body: JSON.stringify({
          rules: RULES, composite: WEIGHTS,
          sort: document.getElementById('scr-sort').value,
          limit: LAST.count,
        }),
      });
      const full = await res.json();
      if (full.ok && full.results.length) results = full.results;
    } catch (e) { /* fall back to the page we already have */ }
    if (results.length < LAST.count) {
      toast(`Exporting ${results.length} of ${LAST.count} matches`, 'err');
    }
  }
  // Filtered fields first (deduped, in rule order), then the standard set.
  const filtered = [];
  const seen = new Set();
  for (const p of (LAST.pills || [])) {
    if (seen.has(p.field)) continue;
    seen.add(p.field);
    const f = scrField(p.field);
    if (f) filtered.push(f);
  }
  const standard = ['price', 'quality', 'health', 'moat', 'rs_rank', 'eps_growth',
                    'forward_pe', 'inst_own', 'market_cap', 'conviction',
                    'conv_stars', 'category', 'grade', 'buy_zone_label',
                    'breakout_probability', 'swing_score', 'rr', 'days_to_earnings']
    .filter(k => !seen.has(k)).map(scrField).filter(Boolean);
  const cols = [...filtered, ...standard];

  const header = ['Ticker', 'Name', 'Sector', 'Match score', 'Composite',
                  ...cols.map(c => c.label + (c.unit === '%' ? ' (%)' : '')),
                  'Data as of', 'Matched rules'];
  const lines = [header.map(scrCsvEscape).join(',')];
  for (const r of results) {
    const why = (r.why || []).filter(w => w.passed).map(w => w.text).join(' | ');
    lines.push([
      r.ticker, r.name || '', r.sector || '', r.match_score,
      r.composite === null || r.composite === undefined ? '' : r.composite,
      // Raw values, not the display strings — a CSV is for a spreadsheet,
      // and "$2.5T" or "—" would land as text in a numeric column.
      ...cols.map(c => {
        const v = r[c.key];
        if (v === null || v === undefined) return '';
        return typeof v === 'boolean' ? (v ? 'Yes' : 'No') : v;
      }),
      (r.data_as_of || '') + (r.recovered ? ' (from snapshot)' : ''),
      why,
    ].map(scrCsvEscape).join(','));
  }

  const label = scrScreenLabel();
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:]/g, '').replace('T', '_');
  const name = `screener_${label}_${stamp}.csv`;
  // BOM + CRLF so Excel reads ★ / ≥ / — as UTF-8 and splits rows properly.
  const blob = new Blob(['﻿' + lines.join('\r\n')], {type: 'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`${results.length} row(s) × ${header.length} column(s) → ${name}`, 'ok');
}

// A filename that says which screen this was — "screener_2026…csv" ten
// times in a downloads folder is useless.
function scrScreenLabel() {
  const saved = (META.saved || []).find(s =>
    JSON.stringify(s.rules) === JSON.stringify(RULES));
  if (saved) return scrSlug(saved.name);
  const preset = (META.presets || []).find(p =>
    JSON.stringify(p.conditions.map(c => c.field).sort()) ===
    JSON.stringify(RULES.items.filter(i => !i.items).map(i => i.field).sort()));
  if (preset) return scrSlug(preset.name);
  if (!RULES.items.length) return 'all';
  return scrSlug((LAST.pills || []).map(p => p.text).join('-')) || 'custom';
}

function scrSlug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '').slice(0, 48);
}

document.addEventListener('DOMContentLoaded', scrInit);
"""
