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
import os
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
# Categories that own their own notification channel and must not also
# appear in the general digest. core.longterm.entry_alerts sends its own mail
# under a fixed subject ("Longterm swing trades") so a standing list of
# orders to work stays filterable; without this exclusion the same alert
# would arrive twice under two different subjects.
SELF_EMAILING_CATEGORIES = ("longterm_entry",)

EMAIL_PRIORITIES = ("CRITICAL", "HIGH")  # tiers worth interrupting a workday for —
                                          # also gates the Telegram push digest below

# ── Notification mute ────────────────────────────────────────────────────────
# Which categories may PUSH (email + Telegram). Empty by default: the digest
# was the bulk of the inbox, and the three things worth interrupting a day for
# each own a dedicated channel that does not route through here —
#
#     Pre-Market Brief    core/premarket_brief.py
#     Market Movers       scanners/market_movers.email_movers()
#     Longterm swing …    core/longterm/entry_alerts.send_entry_email()
#
# This mutes the PUSH only. Every alert still lands on the Alerts page in
# full, still dedups, still logs — muting is about the inbox, not about
# throwing away the signal, and a category re-enabled below immediately
# starts pushing again with no other change.
#
# ALERT_NOTIFY_CATEGORIES overrides: a comma-separated list of categories,
# or "all" for the previous behaviour.
DEFAULT_NOTIFY_CATEGORIES: tuple[str, ...] = ()


def notify_categories() -> set[str] | None:
    """Categories allowed to push; None means "no restriction".

    Read per call rather than captured at import so the env var can be
    changed while the workstation is running — the scheduler lives in a
    long-running process and a restart to unmute would be a poor trade.
    """
    raw = os.environ.get("ALERT_NOTIFY_CATEGORIES")
    if raw is None:
        return set(DEFAULT_NOTIFY_CATEGORIES)
    if raw.strip().lower() == "all":
        return None
    return {c.strip() for c in raw.split(",") if c.strip()}


def notifiable(alerts: list[dict]) -> list[dict]:
    """The alerts a push channel is allowed to send, in one place so email
    and Telegram cannot drift on what counts as urgent or muted."""
    allowed = notify_categories()
    out = []
    for a in alerts:
        category = a.get("category")
        if category in SELF_EMAILING_CATEGORIES:
            continue                      # has its own channel — would double
        if a.get("priority") not in EMAIL_PRIORITIES:
            continue
        if allowed is not None and category not in allowed:
            continue
        out.append(a)
    return out


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
    urgent = notifiable(alerts)
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
    urgent = notifiable(alerts)
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


def alert_since(rec: dict) -> str:
    """When a condition became active. Falls back to the alert's own
    created_at, which is what `since` is seeded from — so a hand-edited or
    older state file still sorts rather than dropping to the bottom."""
    return rec.get("since") or (rec.get("alert") or {}).get("created_at") or ""


SORT_MODES = ("newest", "oldest", "priority")


def active_display_alerts(now: datetime | None = None,
                          sort: str = "newest") -> list[dict]:
    """Active alerts for the webapp feed, minus LOW alerts older than
    LOW_TTL_HOURS.

    sort:
      "newest"   most recently fired first (default — what the feed is for)
      "oldest"   longest-standing conditions first
      "priority" CRITICAL→LOW, newest first inside each tier

    Default is newest-first rather than priority-first because the feed
    answers "what just happened"; a week-old CRITICAL that has been read
    every day since shouldn't outrank an alert that fired five minutes ago.
    Priority ordering is still one dropdown away.

    Each returned alert carries a `since` key copied from its state record.
    The dicts are copies — the state file's alert payloads are what get
    written back on the next reconcile, and adding display fields to them
    would persist presentation data into the store.
    """
    recs = [r for r in load_active().values() if not _is_expired(r, now)]
    alerts = [{**r["alert"], "since": alert_since(r)} for r in recs]
    if sort == "priority":
        alerts.sort(key=lambda a: (priority_rank(a["priority"]),
                                   _neg_time_key(a.get("since"))))
    elif sort == "oldest":
        alerts.sort(key=lambda a: a.get("since") or "")
    else:
        alerts.sort(key=lambda a: a.get("since") or "", reverse=True)
    return alerts


def _neg_time_key(since: str | None):
    """Sort key that puts newer timestamps first without reversing the whole
    tuple — lets priority stay ascending while time runs descending."""
    try:
        return -datetime.fromisoformat(since).timestamp() if since else 0.0
    except (ValueError, TypeError):
        return 0.0


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


# ─────────────────────────────────────────────────────────────────────────────
# PRUNING — delete alerts by date
#
# Two stores, two very different risk profiles:
#
#   alerts_log.json    append-only history. Pruning it loses a record and
#                      nothing else. Safe.
#   alerts_state.json  the live dedup set. Deleting a key here does NOT just
#                      remove a row — it tells the engine it has never seen
#                      that condition, so a condition that is still true will
#                      fire again on the next scan and (at CRITICAL/HIGH) send
#                      a fresh email and Telegram push. That is why pruning
#                      the active set is opt-in and reports how many of the
#                      keys it would drop are email-tier.
# ─────────────────────────────────────────────────────────────────────────────

def _cutoff(days: int | None = None, before: datetime | None = None,
            now: datetime | None = None) -> datetime:
    """Resolve --days / --before into one cutoff. Anything strictly older is
    eligible for deletion."""
    if before is not None:
        return before
    if days is None:
        raise ValueError("pass days= or before=")
    if days < 0:
        raise ValueError("days must be >= 0")
    return (now or datetime.now()) - timedelta(days=days)


def _older_than(stamp: str | None, cutoff: datetime) -> bool:
    """True when `stamp` is parseable and strictly older than the cutoff.

    An unparseable or missing timestamp is treated as NOT old enough — a
    delete tool that can't read a date must keep the record, not guess.
    """
    if not stamp:
        return False
    try:
        return datetime.fromisoformat(str(stamp)) < cutoff
    except (ValueError, TypeError):
        return False


def prune_log(days: int | None = None, before: datetime | None = None,
              dry_run: bool = False, now: datetime | None = None) -> dict:
    """Delete alert-log entries older than the cutoff.

    Returns {"removed", "kept", "total", "cutoff", "oldest_kept", "dry_run"}.
    """
    cutoff = _cutoff(days, before, now)
    entries = []
    if ALERTS_LOG_PATH.exists():
        try:
            entries = json.loads(ALERTS_LOG_PATH.read_text())
        except (OSError, ValueError):
            entries = []
    kept = [e for e in entries if not _older_than(e.get("created_at"), cutoff)]
    removed = len(entries) - len(kept)
    if removed and not dry_run:
        ALERTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ALERTS_LOG_PATH.write_text(json.dumps(kept, indent=2))
    stamps = [e.get("created_at") for e in kept if e.get("created_at")]
    return {
        "removed": removed, "kept": len(kept), "total": len(entries),
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "oldest_kept": min(stamps) if stamps else None,
        "dry_run": dry_run,
    }


def prune_active(days: int | None = None, before: datetime | None = None,
                 dry_run: bool = False, priorities: tuple[str, ...] | None = None,
                 now: datetime | None = None) -> dict:
    """Delete active-alert keys whose condition has been standing since before
    the cutoff.

    `priorities` restricts the purge to those tiers (e.g. ("LOW", "MEDIUM")),
    which is the safe way to use this: it clears stale low-grade noise without
    re-triggering the email/Telegram tiers.

    Returns a summary including `refire_risk` — how many removed keys are
    CRITICAL/HIGH and would therefore re-notify if their condition still
    holds on the next scan.
    """
    cutoff = _cutoff(days, before, now)
    active = load_active()
    total = len(active)
    doomed = {}
    for key, rec in active.items():
        if not _older_than(alert_since(rec), cutoff):
            continue
        if priorities and (rec.get("alert") or {}).get("priority") not in priorities:
            continue
        doomed[key] = rec

    refire = sum(1 for r in doomed.values()
                 if (r.get("alert") or {}).get("priority") in EMAIL_PRIORITIES)
    if doomed and not dry_run:
        for key in doomed:
            del active[key]
        save_active(active)
        # The ALERT_TICKERS watchlist is derived from the active set, so it
        # has to be rewritten here too — otherwise it keeps listing tickers
        # whose alerts were just deleted.
        _sync_alert_watchlist(active)
    return {
        "removed": len(doomed),
        "kept": total - len(doomed),
        "total": total,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "refire_risk": refire,
        "removed_keys": sorted(doomed),
        "dry_run": dry_run,
    }


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
