"""
Tests for scripts/spy_check_premium_exits.py — the deterministic half of the
premium stop/target check that the scheduled Claude task drives.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from Daytrades.core import trade_proposals  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "spy_check_premium_exits", PROJECT_ROOT / "scripts" / "spy_check_premium_exits.py"
)
check_premium_exits = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_premium_exits)


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    """Never touch the real data/trade_proposals.json."""
    monkeypatch.setattr(trade_proposals, "TRADE_PROPOSALS_PATH", tmp_path / "trade_proposals.json")
    monkeypatch.setenv("OPTIONS_STOP_PCT", "-35")
    monkeypatch.setenv("OPTIONS_TARGET_PCT", "60")
    yield


def _placed_position(premium=2.00, side="long"):
    signal = {
        "ticker": "SPY", "side": side, "signal_time": "2026-07-24T10:05:00-04:00",
        "spy_price": 738.86, "hma_trend": "bullish", "ma200_trend": "bullish",
        "orb_breakout_aligned": True, "vix": 18.5,
    }
    proposal = trade_proposals.sync_from_signal(signal)
    trade_proposals.set_status(proposal["id"], "approved")
    trade_proposals.set_status(proposal["id"], "placed", order_details={
        "option_symbol": "SPY260724C00739000", "strike": 739.0,
        "expiration": "2026-07-24", "option_type": "call",
        "contracts": 4, "premium": premium,
    })
    return proposal["id"]


def test_list_open_is_empty_with_no_positions():
    assert check_premium_exits.cmd_list_open()["open_positions"] == []


def test_list_open_reports_contract_and_entry_premium():
    pid = _placed_position(premium=2.00)
    result = check_premium_exits.cmd_list_open()

    assert len(result["open_positions"]) == 1
    pos = result["open_positions"][0]
    assert pos["proposal_id"] == pid
    assert pos["option_symbol"] == "SPY260724C00739000"
    assert pos["entry_premium"] == 2.00
    assert pos["contracts"] == 4
    assert result["stop_pct"] == -35.0
    assert result["target_pct"] == 60.0


def test_evaluate_creates_exit_proposal_on_stop_breach():
    pid = _placed_position(premium=2.00)
    # 1.29 is -35.5% -> breaches the -35% stop.
    result = check_premium_exits.cmd_evaluate({pid: 1.29})

    record = result["evaluated"][0]
    assert record["breach"] == "stop"
    assert record["move_pct"] == pytest.approx(-35.5)
    assert record["exit_proposal_created"] == f"{pid}:exit"

    exits = trade_proposals.list_proposals(status="pending_review", kind="exit")
    assert len(exits) == 1
    assert exits[0]["reason"] == "premium_stop"


def test_evaluate_creates_exit_proposal_on_target_breach():
    pid = _placed_position(premium=2.00)
    result = check_premium_exits.cmd_evaluate({pid: 3.25})  # +62.5%

    record = result["evaluated"][0]
    assert record["breach"] == "target"
    exits = trade_proposals.list_proposals(status="pending_review", kind="exit")
    assert exits[0]["reason"] == "premium_target"


def test_evaluate_does_nothing_inside_the_band():
    pid = _placed_position(premium=2.00)
    result = check_premium_exits.cmd_evaluate({pid: 2.10})  # +5%

    record = result["evaluated"][0]
    assert record["breach"] is None
    assert record["exit_proposal_created"] is None
    assert trade_proposals.list_proposals(status="pending_review", kind="exit") == []


def test_evaluate_is_idempotent_across_repeated_runs():
    # A stop stays breached on every subsequent 5-minute run — the second
    # run must not pile up duplicate exit proposals.
    pid = _placed_position(premium=2.00)
    check_premium_exits.cmd_evaluate({pid: 1.20})
    second = check_premium_exits.cmd_evaluate({pid: 1.15})

    assert second["evaluated"][0]["exit_proposal_created"] == "already_exists"
    assert len(trade_proposals.list_proposals(status="pending_review", kind="exit")) == 1


def test_evaluate_reports_error_when_entry_premium_missing():
    signal = {
        "ticker": "SPY", "side": "long", "signal_time": "2026-07-24T10:05:00-04:00",
        "spy_price": 738.86, "hma_trend": "bullish", "ma200_trend": "bullish",
        "orb_breakout_aligned": True, "vix": 18.5,
    }
    proposal = trade_proposals.sync_from_signal(signal)
    trade_proposals.set_status(proposal["id"], "placed", order_details={"contracts": 4})

    result = check_premium_exits.cmd_evaluate({proposal["id"]: 1.00})
    assert "error" in result["evaluated"][0]
