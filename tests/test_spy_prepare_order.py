"""
Tests for scripts/spy_prepare_order.py — the deterministic half of the
approval → order flow.

The properties worth pinning: a daily-limit breach must BLOCK rather than
quietly resize, recording a fill must persist the fields the premium
stop/target monitor depends on, and P&L must be computed from the recorded
entry premium rather than anything supplied at close time.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from Daytrades.core import trade_proposals, trading_journal  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "spy_prepare_order", PROJECT_ROOT / "scripts" / "spy_prepare_order.py")
prepare_order = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare_order)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_proposals, "TRADE_PROPOSALS_PATH", tmp_path / "proposals.json")
    monkeypatch.setattr(trading_journal, "JOURNAL_PATH", tmp_path / "journal.json")
    monkeypatch.setenv("ACCOUNT_SIZE", "100000")
    monkeypatch.setenv("RISK_PER_TRADE_PCT", "1.0")
    monkeypatch.setenv("MAX_POSITION_PCT", "25")
    monkeypatch.setenv("DAILY_LOSS_LIMIT_PCT", "2")
    monkeypatch.setenv("OPTIONS_STOP_PCT", "-35")
    monkeypatch.setenv("OPTIONS_TARGET_PCT", "60")
    yield


def _approved_entry(side="long"):
    signal = {
        "ticker": "SPY", "side": side, "signal_time": "2026-07-27T10:05:00-04:00",
        "spy_price": 739.0, "hma_trend": "bullish", "ma200_trend": "bullish",
        "orb_breakout_aligned": True, "vix": 15.2,
    }
    p = trade_proposals.sync_from_signal(signal)
    trade_proposals.set_status(p["id"], "approved")
    return p["id"]


def _place(pid, premium=2.00, contracts=5):
    return prepare_order.cmd_record_placed(SimpleNamespace(
        proposal_id=pid, option_symbol="SPY260727C00739000", strike=739.0,
        expiration="2026-07-27", option_type="call", contracts=contracts,
        premium=premium, limit_price=premium, order_id="ord-1", note=None))


def test_list_approved_maps_long_to_call_and_short_to_put():
    _approved_entry("long")
    entries = prepare_order.cmd_list_approved()["approved_entries"]
    assert entries[0]["option_type"] == "call"

    trade_proposals._save({})
    _approved_entry("short")
    entries = prepare_order.cmd_list_approved()["approved_entries"]
    assert entries[0]["option_type"] == "put"


def test_size_computes_contracts_and_bands():
    pid = _approved_entry()
    out = prepare_order.cmd_size(pid, 2.00)
    assert out["suggested_contracts"] == 5          # $1000 budget / $200 per contract
    assert out["total_cost"] == 1000.0
    assert out["stop_premium"] == 1.30
    assert out["target_premium"] == 3.20
    assert out["blocked_by_daily_limit"] is False


def test_size_refuses_a_proposal_that_is_not_approved():
    signal = {"ticker": "SPY", "side": "long", "signal_time": "2026-07-27T10:05:00-04:00",
              "spy_price": 739.0, "hma_trend": "bullish", "ma200_trend": "bullish",
              "orb_breakout_aligned": True, "vix": 15.2}
    p = trade_proposals.sync_from_signal(signal)   # still pending_review
    assert "error" in prepare_order.cmd_size(p["id"], 2.00)


def test_daily_loss_limit_blocks_rather_than_resizing():
    pid = _approved_entry()
    # Book a losing round trip worth more than 2% of a 100k account.
    trading_journal.record_closed_trade(
        ticker="SPY", side="long", strike=739.0, expiration="2026-07-27",
        option_type="call", contracts=10, premium_entry=3.00, premium_exit=0.50,
        entry_time="2026-07-27T09:40:00", exit_time=prepare_order.datetime.now().isoformat(),
        reason="premium_stop")

    out = prepare_order.cmd_size(pid, 2.00)
    assert out["blocked_by_daily_limit"] is True
    assert out["blocked_reason"]
    # Crucially the size is NOT silently shrunk — the caller must refuse.
    assert out["suggested_contracts"] == 5


def test_size_reports_why_it_returned_zero_contracts():
    pid = _approved_entry()
    out = prepare_order.cmd_size(pid, 50.00)   # $5000/contract vs a $1000 budget
    assert out["suggested_contracts"] == 0
    assert "risk budget" in out["zero_size_reason"]


def test_record_placed_persists_fields_the_premium_monitor_needs():
    pid = _approved_entry()
    result = _place(pid, premium=2.15, contracts=4)
    details = result["proposal"]["order_details"]
    assert result["proposal"]["status"] == "placed"
    # spy_check_premium_exits.py reads exactly these two.
    assert details["option_symbol"] == "SPY260727C00739000"
    assert details["premium"] == 2.15
    assert details["contracts"] == 4


def test_record_closed_journals_pnl_from_the_recorded_entry_premium():
    pid = _approved_entry()
    _place(pid, premium=2.00, contracts=4)

    out = prepare_order.cmd_record_closed(pid, 3.10, "premium_target")
    trade = out["trade"]
    assert trade["pnl_dollars"] == pytest.approx((3.10 - 2.00) * 100 * 4)
    assert trade_proposals.get_proposal(pid)["status"] == "closed"


def test_record_closed_accepts_the_exit_proposal_id_too():
    pid = _approved_entry()
    _place(pid, premium=2.00, contracts=2)
    exit_p = trade_proposals.create_exit_proposal(pid, "signal_flip")

    out = prepare_order.cmd_record_closed(exit_p["id"], 1.40, "signal_flip")
    assert out["trade"]["pnl_dollars"] == pytest.approx((1.40 - 2.00) * 100 * 2)
    assert trade_proposals.get_proposal(pid)["status"] == "closed"
    assert trade_proposals.get_proposal(exit_p["id"])["status"] == "placed"


def test_record_closed_refuses_when_entry_has_no_premium():
    pid = _approved_entry()
    trade_proposals.set_status(pid, "placed", order_details={"contracts": 3})
    out = prepare_order.cmd_record_closed(pid, 1.00, "manual")
    assert "no recorded premium" in out["error"]


def test_closed_is_a_valid_status():
    assert "closed" in trade_proposals.STATUSES
