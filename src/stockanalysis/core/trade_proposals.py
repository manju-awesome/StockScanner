"""
trade_proposals.py
===================
Bridges the alert engine's A+ setup detection (core.watchlist_alerts) to a
human-reviewed order. This module NEVER places a trade — it only turns an
active "a_plus_setup" alert into a structured, persisted proposal (entry,
stop, target, risk/share) that a person (or an assistant acting on their
explicit per-order confirmation) can review against live quotes before
deciding whether to submit anything to a broker.

Lifecycle: pending_review -> approved -> placed
                           -> rejected
                           -> expired   (alert resolved before being actioned)

sync_from_alerts() is idempotent and safe to call every scan cycle: it adds
new proposals for newly-firing A+ alerts, leaves already-actioned proposals
(approved/placed/rejected) untouched, and expires pending_review proposals
whose alert is no longer active.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from stockanalysis.core import alerts

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
TRADE_PROPOSALS_PATH = DATA_DIR / "trade_proposals.json"

STATUSES = ("pending_review", "approved", "rejected", "placed", "expired")

# Default risk budget used only as a sizing SUGGESTION shown during review —
# never used to place an order. A human (or the assistant, per explicit
# confirmation) can override the share count for any single proposal.
DEFAULT_RISK_PCT_PER_TRADE = 0.01  # 1% of account equity per trade


def _load() -> dict:
    if not TRADE_PROPOSALS_PATH.exists():
        return {}
    try:
        return json.loads(TRADE_PROPOSALS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save(proposals: dict) -> None:
    TRADE_PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRADE_PROPOSALS_PATH.write_text(json.dumps(proposals, indent=2))


def suggested_shares(entry: float, stop: float, account_equity: float,
                      risk_pct: float = DEFAULT_RISK_PCT_PER_TRADE) -> int:
    """Whole shares such that a stop-out loses ~risk_pct of account_equity.
    A suggestion for the reviewer, not an instruction — always shown next to
    the raw risk/share so it can be overridden."""
    risk_per_share = entry - stop
    if risk_per_share <= 0 or account_equity <= 0:
        return 0
    return max(0, int((account_equity * risk_pct) // risk_per_share))


def _from_alert(alert: dict) -> dict:
    sd = alert.get("supporting_data") or {}
    entry, stop, target = sd.get("entry"), sd.get("stop"), sd.get("target")
    return {
        "ticker": alert["ticker"],
        "category": sd.get("category"),
        "strategies": sd.get("strategies"),
        "grade": sd.get("grade"),
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_per_share": round(entry - stop, 2) if entry and stop else None,
        "reward_risk": (round((target - entry) / (entry - stop), 2)
                        if entry and stop and target and entry != stop else None),
        "price_at_alert": sd.get("price"),
        "status": "pending_review",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": None,
    }


def sync_from_alerts() -> list[dict]:
    """Reconciles trade_proposals.json against the currently-active
    a_plus_setup alerts. Returns the list of newly-created proposals (empty
    if nothing changed). Call this after every scan/monitor cycle that also
    calls watchlist_alerts.scan_rows_for_alerts / scan_rows_for_option_alerts."""
    active_aplus = {a["ticker"]: a for a in alerts.active_display_alerts()
                    if a.get("category") == "a_plus_setup" and a.get("ticker")}

    proposals = _load()
    new_proposals = []

    for ticker, alert in active_aplus.items():
        existing = proposals.get(ticker)
        if existing and existing["status"] != "expired":
            continue  # already tracked (pending/approved/rejected/placed) — leave it
        proposal = _from_alert(alert)
        proposals[ticker] = proposal
        new_proposals.append(proposal)

    # Expire pending_review proposals whose alert has resolved — they were
    # never actioned and the setup no longer holds.
    for ticker, p in proposals.items():
        if p["status"] == "pending_review" and ticker not in active_aplus:
            p["status"] = "expired"
            p["updated_at"] = datetime.now().isoformat(timespec="seconds")

    _save(proposals)
    if new_proposals:
        log.info("trade_proposals: %d new A+ proposal(s): %s",
                 len(new_proposals), ", ".join(p["ticker"] for p in new_proposals))
    return new_proposals


def list_proposals(status: str | None = "pending_review") -> list[dict]:
    """All proposals, or only those matching `status` (default: the ones
    still awaiting a human decision). Pass status=None for everything."""
    proposals = list(_load().values())
    if status is None:
        return proposals
    return [p for p in proposals if p["status"] == status]


def set_status(ticker: str, status: str, note: str | None = None) -> dict | None:
    """Records a human decision (or the assistant's, on explicit per-order
    confirmation) against a proposal. Never called automatically — this is
    the only function that moves a proposal out of pending_review, and
    nothing downstream of it places an order on its own."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    proposals = _load()
    p = proposals.get(ticker.upper())
    if not p:
        return None
    p["status"] = status
    p["note"] = note
    p["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save(proposals)
    return p
