"""
position_sizing.py
====================
Pure arithmetic — sizing a 0DTE option trade by % of account risked as
premium (the whole premium is the risk, since a 0DTE contract can expire
worthless), the options equivalent of the entry/stop share-risk sizing in
StockAnalysis_Version1's trade_proposals.suggested_shares().
"""

from __future__ import annotations


def suggested_contracts(
    premium: float, account_size: float, risk_pct: float, max_position_pct: float
) -> int:
    """Whole contracts such that total premium paid is ~risk_pct of account
    equity, capped at max_position_pct of the account. A suggestion for the
    reviewer, not an instruction."""
    if premium <= 0 or account_size <= 0:
        return 0
    cost_per_contract = premium * 100
    risk_dollars = account_size * (risk_pct / 100)
    cap_dollars = account_size * (max_position_pct / 100)
    by_risk = int(risk_dollars // cost_per_contract)
    by_cap = int(cap_dollars // cost_per_contract)
    return max(0, min(by_risk, by_cap))


def daily_loss_limit_hit(realized_pnl_today: float, account_size: float, daily_loss_limit_pct: float) -> bool:
    """True once today's realized losses reach daily_loss_limit_pct of the
    account. Callers should block new proposals (not silently resize them)
    when this is true — matches the day-trading-prompts rule: "the answer is
    0 [contracts]", not a smaller size."""
    if account_size <= 0:
        return False
    return realized_pnl_today <= -abs(account_size * (daily_loss_limit_pct / 100))


def premium_exit_hit(entry_premium: float, current_premium: float, stop_pct: float, target_pct: float) -> str | None:
    """Returns "stop", "target", or None based on the % move on premium
    since entry. stop_pct is negative (e.g. -35), target_pct positive
    (e.g. 60)."""
    if entry_premium <= 0:
        return None
    move_pct = (current_premium - entry_premium) / entry_premium * 100
    if move_pct <= stop_pct:
        return "stop"
    if move_pct >= target_pct:
        return "target"
    return None
