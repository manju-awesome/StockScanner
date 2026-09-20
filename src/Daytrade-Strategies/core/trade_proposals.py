"""
trade_proposals.py
===================
Adapted from StockAnalysis_Version1's core/trade_proposals.py for the 0DTE
options pipeline. This module NEVER places a trade — it only turns a
deduped UT Bot signal (core.signal_state) into a structured, persisted
proposal that a person, or the assistant acting on an explicit per-order
confirmation, reviews before anything is sent to a broker.

Lifecycle: pending_review -> approved -> placed
                           -> rejected
                           -> expired            (superseded by a new signal)
                           -> blocked_daily_limit (daily loss limit hit)

Two kinds of proposal:
  - "entry": a fresh UT Bot signal (long/short) — the thing to size and buy.
  - "exit":  a signal to close an already-`placed` entry, raised either by
    the opposite UT Bot signal firing (the daemon can detect this) or by a
    premium stop/target band being hit (requires a Robinhood-authenticated
    quote check — see README's "Known constraint").

sync_from_signal() and create_exit_proposal() are both idempotent: calling
them repeatedly with the same signal/entry never creates a duplicate.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# data/spy/, not data/: stockanalysis.core.trade_proposals owns
# data/trade_proposals.json with an incompatible share-based schema. Same
# filename, different shape — the subdirectory is what keeps the two engines
# independent while sharing one project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data" / "spy"
TRADE_PROPOSALS_PATH = DATA_DIR / "trade_proposals.json"

STATUSES = ("pending_review", "approved", "rejected", "placed", "closed", "expired",
            "blocked_daily_limit")
ACTIVE_STATUSES = ("pending_review", "approved")


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


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _entry_id(signal: dict) -> str:
    return f"{signal['ticker']}:{signal['side']}:{signal['signal_time']}"


def sync_from_signal(signal: dict, *, blocked_by_daily_limit: bool = False) -> dict | None:
    """Turns a deduped signal (already passed through signal_state.record_signal,
    so this is only called for genuinely new events) into a pending_review
    entry proposal. Returns the new proposal, or None if one already exists
    for this exact signal (idempotent re-call safety)."""
    proposal_id = _entry_id(signal)
    proposals = _load()
    if proposal_id in proposals:
        return None

    # Superseded entries: any other still-active entry proposal for this
    # ticker on the opposite side is no longer actionable once a new signal
    # has fired the other way.
    for pid, p in proposals.items():
        if (
            p["kind"] == "entry"
            and p["ticker"] == signal["ticker"]
            and p["side"] != signal["side"]
            and p["status"] in ACTIVE_STATUSES
        ):
            p["status"] = "expired"
            p["updated_at"] = _now()

    proposal = {
        "id": proposal_id,
        "kind": "entry",
        "ticker": signal["ticker"],
        "side": signal["side"],
        "signal_time": signal["signal_time"],
        "spy_price_at_signal": signal["spy_price"],
        "hma_trend": signal["hma_trend"],
        "ma200_trend": signal["ma200_trend"],
        "orb_breakout_aligned": signal["orb_breakout_aligned"],
        "vix": signal["vix"],
        "status": "blocked_daily_limit" if blocked_by_daily_limit else "pending_review",
        "created_at": _now(),
        "updated_at": _now(),
        "note": None,
        "order_details": None,
        "linked_entry_id": None,
    }
    proposals[proposal_id] = proposal
    _save(proposals)
    return proposal


def create_exit_proposal(entry_id: str, reason: str) -> dict | None:
    """Raises a close-position proposal for an already-`placed` entry.
    reason: "signal_flip" | "premium_stop" | "premium_target". Idempotent —
    won't create a second active exit proposal for the same entry."""
    proposals = _load()
    entry = proposals.get(entry_id)
    if not entry or entry["status"] != "placed":
        return None

    exit_id = f"{entry_id}:exit"
    existing = proposals.get(exit_id)
    if existing and existing["status"] in ACTIVE_STATUSES:
        return None

    proposal = {
        "id": exit_id,
        "kind": "exit",
        "ticker": entry["ticker"],
        "side": "close",
        "reason": reason,
        "linked_entry_id": entry_id,
        "signal_time": _now(),
        "status": "pending_review",
        "created_at": _now(),
        "updated_at": _now(),
        "note": None,
        "order_details": None,
    }
    proposals[exit_id] = proposal
    _save(proposals)
    return proposal


def list_proposals(status: str | None = "pending_review", kind: str | None = None) -> list[dict]:
    """All proposals, or only those matching `status`/`kind`. Pass
    status=None for every status."""
    proposals = list(_load().values())
    if status is not None:
        proposals = [p for p in proposals if p["status"] == status]
    if kind is not None:
        proposals = [p for p in proposals if p["kind"] == kind]
    return proposals


def get_proposal(proposal_id: str) -> dict | None:
    return _load().get(proposal_id)


def set_status(proposal_id: str, status: str, *, note: str | None = None, order_details: dict | None = None) -> dict | None:
    """Records a human decision (or the assistant's, on explicit per-order
    confirmation). This is the only function that advances a proposal past
    pending_review/approved — nothing downstream of it places an order on
    its own."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    proposals = _load()
    p = proposals.get(proposal_id)
    if not p:
        return None
    p["status"] = status
    p["updated_at"] = _now()
    if note is not None:
        p["note"] = note
    if order_details is not None:
        p["order_details"] = order_details
    _save(proposals)
    return p
