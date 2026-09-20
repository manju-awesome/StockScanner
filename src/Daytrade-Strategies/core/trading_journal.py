"""
trading_journal.py
====================
Minimal closed-trade log — just enough to support the daily loss limit gate
(position_sizing.daily_loss_limit_hit) and a basic post-hoc record. Options
schema (strike/expiration/premium/contracts) rather than
stockanalysis.core.trading_journal's share-based one; the psychology/
rule-checklist fields from that journal are a separate, human-filled concern
and out of scope here.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

# data/spy/, not data/: stockanalysis.core.trading_journal owns
# data/journal_trades.json with the share-based schema described above. Same
# filename, different shape — the subdirectory keeps the two independent.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data" / "spy"
JOURNAL_PATH = DATA_DIR / "journal_trades.json"


def _load() -> list[dict]:
    if not JOURNAL_PATH.exists():
        return []
    try:
        return json.loads(JOURNAL_PATH.read_text())
    except (OSError, ValueError):
        return []


def _save(trades: list[dict]) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL_PATH.write_text(json.dumps(trades, indent=2))


def record_closed_trade(
    *,
    ticker: str,
    side: str,
    strike: float,
    expiration: str,
    option_type: str,
    contracts: int,
    premium_entry: float,
    premium_exit: float,
    entry_time: str,
    exit_time: str,
    reason: str,
) -> dict:
    pnl_dollars = round((premium_exit - premium_entry) * 100 * contracts, 2)
    trade = {
        "ticker": ticker,
        "side": side,
        "strike": strike,
        "expiration": expiration,
        "option_type": option_type,
        "contracts": contracts,
        "premium_entry": premium_entry,
        "premium_exit": premium_exit,
        "pnl_dollars": pnl_dollars,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "reason": reason,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    trades = _load()
    trades.append(trade)
    _save(trades)
    return trade


def list_trades() -> list[dict]:
    return _load()


def realized_pnl_today() -> float:
    today = date.today().isoformat()
    return round(sum(t["pnl_dollars"] for t in _load() if t.get("exit_time", "").startswith(today)), 2)
