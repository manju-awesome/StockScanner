"""
pages.py
=========
Page bodies for the Day Trade section of the trading workstation. Reads state
straight from the JSON files the daemon and trade_proposals module already own
under data/spy/ — no separate DB.

These render through stockanalysis.webapp.views, the same layout/typography/
colour system as every other workstation page, so the Day Trade tab doesn't
look like a bolted-on second app. The *logic* underneath (spydaytrader.core)
stays independent of stockanalysis.core — the only things shared are the
presentation helpers and the market-regime scorer, which both halves of the
project were already pointing at the same script for.
"""

from __future__ import annotations

from stockanalysis.webapp.pages import REGIME_JS, _regime_card
from stockanalysis.webapp.views import badge, card, empty, esc, fmt_money, tv_url

from spydaytrader.core import signal_state, trade_proposals, trading_journal

# Proposal statuses -> the workstation's badge tones (green/yellow/red/blue).
_STATUS_TONE = {
    "pending_review": "watch",
    "approved": "good",
    "placed": "info",
    "closed": "muted",
    "rejected": "bad",
    "blocked_daily_limit": "bad",
    "expired": "muted",
}
_TREND_TONE = {"bullish": "good", "bearish": "bad"}


def _tv_link(ticker: str, label: str | None = None) -> str:
    """5-minute chart — the timeframe these signals are computed on."""
    return (f'<a href="{tv_url(ticker, "5")}" target="_blank" rel="noopener" '
            f'title="Open {esc(ticker)} 5m chart on TradingView">'
            f'{esc(label or ticker)} ↗</a>')


def _tile(label: str, value: str, tone: str = "muted") -> str:
    colour = {"good": "#0F6E56", "bad": "#A32D2D"}.get(tone, "#0b0b0b")
    return (f'<div style="flex:1;min-width:120px">'
            f'<div style="font-size:11px;color:#898781">{esc(label)}</div>'
            f'<div style="font-size:18px;font-weight:600;color:{colour}">{value}</div></div>')


def _tiles(html: str) -> str:
    return f'<div style="display:flex;gap:16px;flex-wrap:wrap">{html}</div>'


# ─────────────────────────────────────────────────────────────────────────────
# DAY TRADE DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

def _signal_card() -> str:
    spy = signal_state.get_state()["current"].get("SPY")
    if not spy:
        return card("Signal state",
                    empty("No UT Bot signal recorded yet — the daemon fires one on "
                          "the first crossing bar during market hours."), icon="📶")

    last = spy["last_signal"]
    ticker = last.get("ticker", "SPY")
    # The side reads BUY/SELL and doubles as the chart link, so the obvious next
    # move after seeing a signal — go look at it — is one click.
    side_word = "BUY" if spy["side"] == "long" else "SELL"
    vix = last.get("vix")

    tiles = (
        _tile("Current UT Bot signal", _tv_link(ticker, f"{side_word} {ticker}"),
              "good" if spy["side"] == "long" else "bad")
        + _tile("Since", esc(spy["since"]))
        + _tile("SPY @ signal", fmt_money(last["spy_price"]))
        + _tile("HMA(31) trend", esc(last["hma_trend"]),
                _TREND_TONE.get(last["hma_trend"], "muted"))
        + _tile("200 SMA trend", esc(last["ma200_trend"]),
                _TREND_TONE.get(last["ma200_trend"], "muted"))
        + _tile("VIX @ signal", f"{vix:.2f}" if vix is not None else "—")
        + _tile("ORB high / low",
                f'{last["orb_high"]:.2f} / {last["orb_low"]:.2f}'
                if last["orb_high"] else "—")
    )
    return card("Signal state", _tiles(tiles), icon="📶")


def _proposal_counts_card() -> str:
    pending = trade_proposals.list_proposals(status="pending_review")
    approved = trade_proposals.list_proposals(status="approved")
    placed = trade_proposals.list_proposals(status="placed")
    pnl = trading_journal.realized_pnl_today()

    tiles = (
        _tile("Pending review", str(len(pending)), "watch" if pending else "muted")
        + _tile("Approved (awaiting order)", str(len(approved)),
                "good" if approved else "muted")
        + _tile("Placed (open)", str(len(placed)), "info" if placed else "muted")
        + _tile("Realized P&L today", fmt_money(pnl),
                "good" if pnl > 0 else ("bad" if pnl < 0 else "muted"))
    )
    return card("Proposals",
                _tiles(tiles)
                + '<div style="margin-top:12px;font-size:12px">'
                  '<a href="/daytrade/proposals">View proposals →</a></div>',
                icon="📋")


def _manual_scan_card() -> str:
    return card(
        "Manual test",
        '<div style="font-size:12px;color:#444441;margin-bottom:10px">'
        'Runs one signal-engine scan cycle immediately (bypasses the market-hours '
        'guard), useful for verifying the pipeline without waiting for a live bar '
        'close.</div>'
        '<button class="btn secondary" onclick="runSpyScan()">Run scan now</button>'
        ' <span id="spy-scan-status" style="font-size:12px;color:#898781"></span>'
        '<pre id="spy-scan-result" style="font-size:11px;color:#444441;'
        'background:#f5f4f0;border-radius:8px;padding:0;margin:10px 0 0;'
        'overflow-x:auto"></pre>',
        icon="🧪")


def daytrade_page() -> tuple[str, str]:
    body = (_regime_card() + _signal_card() + _proposal_counts_card()
            + _manual_scan_card())
    extra_js = REGIME_JS + """
function runSpyScan() {
  var s = document.getElementById('spy-scan-status');
  var out = document.getElementById('spy-scan-result');
  s.textContent = ' fetching bars…';
  fetch('/api/spy/scan', {method: 'POST'}).then(r => r.json()).then(d => {
    out.style.padding = '10px 12px';
    out.textContent = JSON.stringify(d, null, 2);
    s.textContent = '';
    if (d.error) { toast(d.error, 'err'); }
    else { toast(d.skipped ? ('Skipped: ' + d.skipped) : 'Scan cycle complete',
                 d.skipped ? '' : 'ok'); }
  }).catch(e => { s.textContent = ' failed: ' + e; });
}
"""
    return body, extra_js


# ─────────────────────────────────────────────────────────────────────────────
# PROPOSALS
# ─────────────────────────────────────────────────────────────────────────────

def _proposal_row(p: dict) -> str:
    ticker = p.get("ticker", "SPY")
    if p["kind"] == "entry":
        side_word = "BUY" if p["side"] == "long" else "SELL"
        colour = "#0F6E56" if p["side"] == "long" else "#A32D2D"
        detail = (f'<span style="color:{colour};font-weight:600">{side_word}</span> '
                  f'{_tv_link(ticker)} {p["spy_price_at_signal"]:.2f} · '
                  f'HMA {esc(p["hma_trend"])} · 200MA {esc(p["ma200_trend"])}')
    else:
        detail = (f'CLOSE {_tv_link(ticker)} · {esc(p.get("linked_entry_id", ""))} · '
                  f'reason: {esc(p.get("reason", ""))}')

    actions = ""
    if p["status"] in ("pending_review", "approved"):
        pid = esc(p["id"])
        actions = (f"""<button class="btn" onclick="spyDecide('{pid}','approved')">Approve</button> """
                   f"""<button class="btn secondary" onclick="spyDecide('{pid}','rejected')">Reject</button>""")

    status = p["status"]
    return (f'<tr><td style="font-family:ui-monospace,monospace;font-size:11px">'
            f'{esc(p["id"])}</td><td>{esc(p["kind"])}</td><td>{detail}</td>'
            f'<td>{badge(status.replace("_", " "), _STATUS_TONE.get(status, "muted"))}</td>'
            f'<td style="white-space:nowrap">{actions}</td></tr>')


def daytrade_proposals_page() -> tuple[str, str]:
    proposals = sorted(trade_proposals.list_proposals(status=None),
                       key=lambda p: p["created_at"], reverse=True)
    if proposals:
        table = ('<table><thead><tr><th>ID</th><th>Kind</th><th>Detail</th>'
                 '<th>Status</th><th>Action</th></tr></thead><tbody>'
                 + "".join(_proposal_row(p) for p in proposals) + "</tbody></table>")
    else:
        table = empty("No proposals yet.")

    body = card(
        "Trade proposals",
        '<div style="font-size:12px;color:#444441;margin-bottom:12px">'
        'Approving here only marks a proposal <code>approved</code> — it does not '
        'place any order. Placing or closing a trade always happens in a Claude Code '
        'session, with an explicit per-order confirmation, using the connected '
        'Robinhood MCP tools.</div>' + table,
        icon="📋")

    extra_js = """
function spyDecide(id, status) {
  fetch('/api/spy/proposals/decide', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: id, status: status})
  }).then(r => r.json()).then(d => {
    if (d.error) { toast(d.error, 'err'); return; }
    location.reload();
  }).catch(e => toast('Request failed: ' + e, 'err'));
}
"""
    return body, extra_js
