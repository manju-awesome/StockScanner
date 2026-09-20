from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Daytrades.core import trade_proposals  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch, tmp_path):
    """Never touch the real data/trade_proposals.json during tests."""
    monkeypatch.setattr(trade_proposals, "TRADE_PROPOSALS_PATH", tmp_path / "trade_proposals.json")
    yield


def _signal(side="long", signal_time="2026-07-20T09:35:00"):
    return {
        "ticker": "SPY",
        "side": side,
        "signal_time": signal_time,
        "spy_price": 501.25,
        "hma_trend": "bullish",
        "ma200_trend": "bullish",
        "orb_breakout_aligned": True,
        "vix": 15.2,
    }


def test_sync_from_signal_creates_pending_review():
    proposal = trade_proposals.sync_from_signal(_signal())
    assert proposal["status"] == "pending_review"
    assert proposal["kind"] == "entry"
    assert proposal["side"] == "long"
    assert trade_proposals.list_proposals(status="pending_review") == [proposal]


def test_sync_from_signal_is_idempotent():
    first = trade_proposals.sync_from_signal(_signal())
    second = trade_proposals.sync_from_signal(_signal())
    assert first is not None
    assert second is None
    assert len(trade_proposals.list_proposals(status=None)) == 1


def test_sync_from_signal_expires_opposite_active_entry():
    long_proposal = trade_proposals.sync_from_signal(_signal(side="long", signal_time="2026-07-20T09:35:00"))
    trade_proposals.sync_from_signal(_signal(side="short", signal_time="2026-07-20T10:05:00"))

    refreshed = trade_proposals.get_proposal(long_proposal["id"])
    assert refreshed["status"] == "expired"


def test_blocked_by_daily_limit_sets_status_directly():
    proposal = trade_proposals.sync_from_signal(_signal(), blocked_by_daily_limit=True)
    assert proposal["status"] == "blocked_daily_limit"


def test_set_status_rejects_invalid_status():
    proposal = trade_proposals.sync_from_signal(_signal())
    with pytest.raises(ValueError):
        trade_proposals.set_status(proposal["id"], "not_a_real_status")


def test_approve_and_place_lifecycle():
    proposal = trade_proposals.sync_from_signal(_signal())
    trade_proposals.set_status(proposal["id"], "approved")
    assert trade_proposals.get_proposal(proposal["id"])["status"] == "approved"

    order_details = {"contract": "SPY260720C00500000", "premium": 2.15, "contracts": 4}
    placed = trade_proposals.set_status(proposal["id"], "placed", order_details=order_details)
    assert placed["status"] == "placed"
    assert placed["order_details"] == order_details


def test_create_exit_proposal_requires_placed_entry():
    proposal = trade_proposals.sync_from_signal(_signal())
    # Still pending_review, not placed -> no exit proposal should be raised.
    assert trade_proposals.create_exit_proposal(proposal["id"], "signal_flip") is None

    trade_proposals.set_status(proposal["id"], "approved")
    trade_proposals.set_status(proposal["id"], "placed", order_details={"premium": 2.0})

    exit_proposal = trade_proposals.create_exit_proposal(proposal["id"], "signal_flip")
    assert exit_proposal is not None
    assert exit_proposal["kind"] == "exit"
    assert exit_proposal["linked_entry_id"] == proposal["id"]


def test_create_exit_proposal_is_idempotent_while_active():
    proposal = trade_proposals.sync_from_signal(_signal())
    trade_proposals.set_status(proposal["id"], "approved")
    trade_proposals.set_status(proposal["id"], "placed", order_details={"premium": 2.0})

    first = trade_proposals.create_exit_proposal(proposal["id"], "signal_flip")
    second = trade_proposals.create_exit_proposal(proposal["id"], "premium_stop")
    assert first is not None
    assert second is None
