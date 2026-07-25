"""
alerts.py
=========
The priority alert engine behind the "personal trading assistant" model: a
single Alert shape, a dedup/lifecycle store so a standing condition (e.g.
"NVDA is oversold") notifies exactly once and goes quiet again once it
resolves, and an email + Telegram digest for the tiers worth interrupting a
workday for.

Design mirrors the master prompt directly:
  - Priority: CRITICAL / HIGH / MEDIUM / LOW
  - CRITICAL and HIGH alerts batch into one immediate email and one Telegram
    message (not one send per alert — a busy professional's inbox/phone is
    exactly the noise this is supposed to cut down on)
  - MEDIUM/LOW alerts are surfaced in the webapp's Alerts feed only; the
    Pre-Market Brief and End-of-Day summary (separate, scheduled emails)
    are where routine/low-priority information belongs, per the spec
  - "Only notify once until condition changes": every alert carries a
    dedup_key (e.g. "NVDA:rsi_oversold"); raise_alerts() only returns the
    ones that are newly active this cycle, and clears keys whose condition
    no longer holds so a future recurrence fires fresh.

Explicitly out of scope here (no free/existing data source, confirmed with
the user): unusual options flow, dark pool activity, institutional buying.
Callers should not fabricate these — leave the condition unchecked rather
than approximate it from data that doesn't actually measure it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
ALERTS_STATE_PATH = DATA_DIR / "alerts_state.json"
ALERTS_LOG_PATH = DATA_DIR / "alerts_log.json"
WATCHLISTS_PATH = DATA_DIR / "watchlists.json"
ALERT_WATCHLIST_KEY = "ALERT_TICKERS"
MAX_LOG = 500

PRIORITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}
EMAIL_PRIORITIES = ("CRITICAL", "HIGH")  # tiers worth interrupting a workday for —
                                          # also gates the Telegram push digest below


def priority_rank(p: str) -> int:
    """Lower is more urgent — use as a sort key."""
    return _PRIORITY_RANK.get(p, len(PRIORITIES))


def make_alert(*, dedup_key: str, category: str, priority: str, headline: str,
              why_it_matters: str, expected_impact: str, suggested_action: str,
              confidence: int, time_sensitivity: str,
              supporting_data: dict | None = None, ticker: str | None = None) -> dict:
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}, got {priority!r}")
    return {
        "dedup_key": dedup_key,
        "category": category,
        "ticker": ticker,
        "priority": priority,
        "headline": headline,
        "why_it_matters": why_it_matters,
        "expected_impact": expected_impact,
        "suggested_action": suggested_action,
        "confidence": max(0, min(100, int(confidence))),
        "time_sensitivity": time_sensitivity,
        "supporting_data": supporting_data or {},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STATE — which conditions are currently active, so we alert once per
# occurrence rather than once per scan cycle
# ─────────────────────────────────────────────────────────────────────────────

def load_active() -> dict:
    if not ALERTS_STATE_PATH.exists():
        return {}
    try:
        return json.loads(ALERTS_STATE_PATH.read_text())
    except Exception:
        return {}


def save_active(active: dict) -> None:
    ALERTS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_STATE_PATH.write_text(json.dumps(active, indent=2))


def _append_log(alerts: list[dict]) -> None:
    if not alerts:
        return
    log = []
    if ALERTS_LOG_PATH.exists():
        try:
            log = json.loads(ALERTS_LOG_PATH.read_text())
        except Exception:
            log = []
    log = alerts + log
    ALERTS_LOG_PATH.write_text(json.dumps(log[:MAX_LOG], indent=2))


def load_log(limit: int = 100) -> list[dict]:
    if not ALERTS_LOG_PATH.exists():
        return []
    try:
        return json.loads(ALERTS_LOG_PATH.read_text())[:limit]
    except Exception:
        return []


def reconcile(current_alerts: list[dict], active: dict, still_checked_keys: set[str]) -> list[dict]:
    """Compares this scan's currently-true conditions (current_alerts) against
    the persisted active set. A dedup_key already active is suppressed
    (already notified, condition hasn't changed); a dedup_key that was
    active but isn't in still_checked_keys/current_alerts anymore is
    cleared (condition resolved — it'll fire fresh if it recurs). Returns
    only the alerts that are NEW this cycle. Mutates `active` in place;
    caller is responsible for save_active(active) afterwards.
    """
    current_keys = {a["dedup_key"] for a in current_alerts}

    # clear resolved conditions (checked this cycle, no longer true)
    for key in list(active.keys()):
        if key in still_checked_keys and key not in current_keys:
            del active[key]

    new_alerts = [a for a in current_alerts if a["dedup_key"] not in active]
    for a in new_alerts:
        active[a["dedup_key"]] = {"alert": a, "since": a["created_at"]}
    return new_alerts


# ── Email digest grouping ────────────────────────────────────────────────────
# The digest is organized by what the reader would DO with each alert, not by
# which module produced it. Order = decision urgency: risk events first
# (earnings, news), then actionable setups by horizon, options last.
EMAIL_GROUP_ORDER = ("Earnings", "News Catalyst", "Day Trade", "Swing Trades",
                     "Long-Term Investment", "Options Setups", "Other")

# watchlist conditions that are intraday events vs multi-day technicals
_DAY_TRADE_CONDITIONS = ("gap", "volume_spike")


def _email_group(a: dict) -> str:
    cat = a.get("category")
    if cat == "earnings":
        return "Earnings"
    if cat == "news":
        return "News Catalyst"
    if cat in ("put_setup", "call_setup"):
        return "Options Setups"
    if cat == "a_plus_setup":
        # one A+ alert can span horizons — file it under the longest one
        # (its headline still names every strategy that hit the band)
        strategies = " ".join((a.get("supporting_data") or {}).get("strategies") or [])
        if "long-term" in strategies:
            return "Long-Term Investment"
        if "swing" in strategies:
            return "Swing Trades"
        if "day" in strategies:
            return "Day Trade"
        return "Swing Trades"
    if cat == "watchlist":
        condition = (a.get("dedup_key") or "").split(":", 1)[-1]
        return "Day Trade" if condition in _DAY_TRADE_CONDITIONS else "Swing Trades"
    return "Other"


def send_alert_emails(alerts: list[dict]) -> bool:
    """Batches every CRITICAL/HIGH alert into a single digest email (never
    one email per alert) via the shared Resend sender, organized into major
    groups (Earnings / News Catalyst / Day Trade / Swing Trades / Long-Term
    Investment / Options Setups). Returns False without raising if there's
    nothing to send or no RESEND_API_KEY — callers can proceed either way."""
    urgent = [a for a in alerts if a["priority"] in EMAIL_PRIORITIES]
    if not urgent:
        return False

    from stockanalysis.scanners.market_movers import send_resend_email

    urgent.sort(key=lambda a: priority_rank(a["priority"]))
    groups: dict[str, list[dict]] = {}
    for a in urgent:
        groups.setdefault(_email_group(a), []).append(a)
    ordered = [(g, groups[g]) for g in EMAIL_GROUP_ORDER if g in groups]

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = " · ".join(f"{g} {len(items)}" for g, items in ordered)
    subject = f"[Trading Assistant] {len(urgent)} alert(s): {summary}"

    text_lines = [f"{len(urgent)} alert(s) — {date_str}", ""]
    html_sections = []
    for group, items in ordered:
        text_lines += [f"=== {group.upper()} ({len(items)}) " + "=" * max(0, 40 - len(group)), ""]
        rows_html = []
        for a in items:
            tag = f"[{a['priority']}]" + (f" {a['ticker']}" if a.get("ticker") else "")
            text_lines += [
                f"{tag} {a['headline']}",
                f"  Why it matters: {a['why_it_matters']}",
                f"  Expected impact: {a['expected_impact']}",
                f"  Suggested action: {a['suggested_action']}",
                f"  Confidence: {a['confidence']}%   Time sensitivity: {a['time_sensitivity']}",
                "",
            ]
            color = "#791F1F" if a["priority"] == "CRITICAL" else "#8a6d1a"
            rows_html.append(f"""
            <div style="border-left:3px solid {color};padding:8px 14px;margin-bottom:12px">
              <div style="font-weight:700;font-size:13px">{tag} {a['headline']}</div>
              <div style="font-size:12px;color:#444">Why it matters: {a['why_it_matters']}</div>
              <div style="font-size:12px;color:#444">Expected impact: {a['expected_impact']}</div>
              <div style="font-size:12px;color:#444">Suggested action: {a['suggested_action']}</div>
              <div style="font-size:11px;color:#898781">Confidence {a['confidence']}% · {a['time_sensitivity']}</div>
            </div>""")
        html_sections.append(
            f'<h3 style="font-size:13px;text-transform:uppercase;letter-spacing:.5px;'
            f'color:#52514e;border-bottom:1px solid #e1e0d9;padding-bottom:4px;'
            f'margin:18px 0 10px">{group} ({len(items)})</h3>' + "".join(rows_html))

    text_body = "\n".join(text_lines)
    html_body = (f'<div style="font-family:sans-serif;max-width:600px">'
                f'<h2 style="font-size:16px">{len(urgent)} alert(s) — {date_str}</h2>'
                + "".join(html_sections) + "</div>")

    return send_resend_email(subject, text_body, html_body)


def send_alert_telegram(alerts: list[dict]) -> bool:
    """Pushes every CRITICAL/HIGH alert as one Telegram message via the
    shared Bot API sender — same tier gate and same grouping/ordering as
    send_alert_emails, so both channels agree on what's urgent enough to
    interrupt. Returns False without raising if there's nothing to send or
    no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID — callers can proceed either way."""
    urgent = [a for a in alerts if a["priority"] in EMAIL_PRIORITIES]
    if not urgent:
        return False

    from stockanalysis.scanners.market_movers import send_telegram_message

    urgent.sort(key=lambda a: priority_rank(a["priority"]))
    groups: dict[str, list[dict]] = {}
    for a in urgent:
        groups.setdefault(_email_group(a), []).append(a)
    ordered = [(g, groups[g]) for g in EMAIL_GROUP_ORDER if g in groups]

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"<b>{len(urgent)} alert(s)</b> — {date_str}", ""]
    for group, items in ordered:
        lines.append(f"<b>{group.upper()} ({len(items)})</b>")
        for a in items:
            tag = f"[{a['priority']}]" + (f" {a['ticker']}" if a.get("ticker") else "")
            lines += [
                f"{tag} {a['headline']}",
                f"Why: {a['why_it_matters']}",
                f"Action: {a['suggested_action']}",
                f"Confidence {a['confidence']}% · {a['time_sensitivity']}",
                "",
            ]

    return send_telegram_message("\n".join(lines).strip())


LOW_TTL_HOURS = 24


def _is_expired(rec: dict, now: datetime | None = None) -> bool:
    """LOW alerts age out of the FEED after LOW_TTL_HOURS. They deliberately
    stay in the persisted active set — purging them there would let the
    still-true condition refire next cycle and reset its own clock, making
    the expiry meaningless. They just stop being displayed; reconcile still
    clears them for real the moment the condition turns false."""
    if rec["alert"].get("priority") != "LOW":
        return False
    try:
        since = datetime.fromisoformat(rec.get("since") or rec["alert"]["created_at"])
    except (ValueError, TypeError, KeyError):
        return False
    return (now or datetime.now()) - since > timedelta(hours=LOW_TTL_HOURS)


def active_display_alerts(now: datetime | None = None) -> list[dict]:
    """Active alerts for the webapp feed: priority-sorted, minus LOW alerts
    older than LOW_TTL_HOURS."""
    recs = [r for r in load_active().values() if not _is_expired(r, now)]
    return sorted((r["alert"] for r in recs),
                  key=lambda a: priority_rank(a["priority"]))


def _sync_alert_watchlist(active: dict) -> None:
    """Mirror every ticker with an active email-tier (CRITICAL/HIGH) alert
    into the ALERT_TICKERS watchlist in data/watchlists.json — the Scanner
    offers it as a universe and the Research Library as a filter category,
    so "what just alerted" is one click to re-scan or review.

    Fully derived state: rewritten from the active set on every reconcile,
    so tickers drop out when their alert resolves. Other watchlist keys are
    untouched; failure to write never blocks alerting."""
    tickers = sorted({
        rec["alert"]["ticker"].upper()
        for rec in active.values()
        if rec["alert"].get("ticker")
        and rec["alert"].get("priority") in EMAIL_PRIORITIES
    })
    try:
        try:
            watchlists = json.loads(WATCHLISTS_PATH.read_text())
        except (OSError, ValueError):
            watchlists = {}
        if watchlists.get(ALERT_WATCHLIST_KEY) == tickers:
            return
        watchlists[ALERT_WATCHLIST_KEY] = tickers
        WATCHLISTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        WATCHLISTS_PATH.write_text(json.dumps(watchlists, indent=1))
    except Exception as e:
        log.warning("ALERT_TICKERS watchlist sync failed: %s", e)


def raise_alerts(current_alerts: list[dict], checked_keys: set[str]) -> list[dict]:
    """The one entry point scanners should call: reconciles against the
    persisted active set, persists the update, emails anything CRITICAL/HIGH,
    mirrors email-tier tickers into the ALERT_TICKERS watchlist, and returns
    the newly-fired alerts (for logging/display)."""
    active = load_active()
    new_alerts = reconcile(current_alerts, active, checked_keys)
    save_active(active)
    _sync_alert_watchlist(active)
    _append_log(new_alerts)
    if new_alerts:
        send_alert_emails(new_alerts)
        send_alert_telegram(new_alerts)
    return new_alerts
