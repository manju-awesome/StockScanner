from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Daytrades.core import position_sizing  # noqa: E402


def test_suggested_contracts_basic():
    # $100k account, 1% risk = $1000 risk budget. Premium $2.00 -> $200/contract.
    # 1000 // 200 = 5 contracts by risk; cap at 25% ($25000) way above that.
    assert position_sizing.suggested_contracts(2.00, 100_000, 1.0, 25) == 5


def test_suggested_contracts_capped_by_max_position_pct():
    # Cheap premium would otherwise buy a lot of contracts by risk alone;
    # max_position_pct should cap it.
    # risk budget: 1% of 100k = 1000 -> by_risk = 1000 // (0.10*100) = 100
    # cap: 1% of 100k = 1000 -> by_cap = 1000 // 10 = 100 (same here, so use a tighter cap)
    contracts = position_sizing.suggested_contracts(0.10, 100_000, 5.0, 1.0)
    # by_risk: 5000 // 10 = 500; by_cap: 1000 // 10 = 100 -> capped at 100
    assert contracts == 100


def test_suggested_contracts_zero_on_bad_inputs():
    assert position_sizing.suggested_contracts(0, 100_000, 1.0, 25) == 0
    assert position_sizing.suggested_contracts(2.0, 0, 1.0, 25) == 0
    assert position_sizing.suggested_contracts(-1, 100_000, 1.0, 25) == 0


def test_daily_loss_limit_hit():
    assert position_sizing.daily_loss_limit_hit(-2100, 100_000, 2.0) is True
    assert position_sizing.daily_loss_limit_hit(-1900, 100_000, 2.0) is False
    assert position_sizing.daily_loss_limit_hit(500, 100_000, 2.0) is False


def test_premium_exit_hit_stop_and_target():
    assert position_sizing.premium_exit_hit(2.00, 1.29, stop_pct=-35, target_pct=60) == "stop"
    assert position_sizing.premium_exit_hit(2.00, 3.21, stop_pct=-35, target_pct=60) == "target"
    assert position_sizing.premium_exit_hit(2.00, 2.10, stop_pct=-35, target_pct=60) is None
