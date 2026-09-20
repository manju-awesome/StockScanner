#!/usr/bin/env python3
"""
prepare_order.py
=================
The deterministic half of the approval → order flow. A Claude Code session
supplies what only a broker session can see (the live contract and its
premium); everything decided from those numbers — contract count, risk
gates, what gets written to disk — happens here, in tested Python, rather
than in a prompt.

This script CANNOT place an order. It has no broker access. It prepares the
numbers for one and records the outcome after the fact.

Subcommands:

  list-approved
      Proposals awaiting an order, entries and exits. Entries carry the
      sizing inputs; exits carry the entry they close.

  size --proposal-id ID --premium 2.15
      Contracts for that premium under the configured risk budget, plus the
      daily-loss-limit gate. Reports blocked=true rather than silently
      returning a smaller size.

  record-placed --proposal-id ID --option-symbol ... --strike ... \
                --expiration ... --option-type call --contracts 4 --premium 2.15
      Call ONLY after an order actually filled. Marks the proposal placed and
      writes order_details. option_symbol and premium are required because
      the premium stop/target check reads them — a position recorded without
      them cannot be monitored.

  record-closed --proposal-id ID --premium-exit 3.10
      Call ONLY after a closing order filled. Journals the round trip and
      moves the entry to closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from Daytrades.core import position_sizing, trade_proposals, trading_journal  # noqa: E402


def _cfg() -> dict:
    return {
        "account_size": float(os.environ.get("ACCOUNT_SIZE", 100_000)),
        "risk_pct": float(os.environ.get("RISK_PER_TRADE_PCT", 1.0)),
        "max_position_pct": float(os.environ.get("MAX_POSITION_PCT", 25)),
        "daily_loss_limit_pct": float(os.environ.get("DAILY_LOSS_LIMIT_PCT", 2)),
        "stop_pct": float(os.environ.get("OPTIONS_STOP_PCT", -35)),
        "target_pct": float(os.environ.get("OPTIONS_TARGET_PCT", 60)),
        "account_number": os.environ.get("ROBINHOOD_ACCOUNT_NUMBER") or None,
    }


def cmd_list_approved() -> dict:
    cfg = _cfg()
    entries, exits = [], []

    for p in trade_proposals.list_proposals(status="approved"):
        if p["kind"] == "entry":
            entries.append({
                "proposal_id": p["id"],
                "side": p["side"],
                "option_type": "call" if p["side"] == "long" else "put",
                "spy_price_at_signal": p["spy_price_at_signal"],
                "signal_time": p["signal_time"],
                "hma_trend": p.get("hma_trend"),
                "ma200_trend": p.get("ma200_trend"),
                "orb_breakout_aligned": p.get("orb_breakout_aligned"),
            })
        else:
            entry = trade_proposals.get_proposal(p.get("linked_entry_id") or "") or {}
            exits.append({
                "proposal_id": p["id"],
                "linked_entry_id": p.get("linked_entry_id"),
                "reason": p.get("reason"),
                "entry_order_details": entry.get("order_details"),
            })

    pnl_today = trading_journal.realized_pnl_today()
    blocked = position_sizing.daily_loss_limit_hit(
        pnl_today, cfg["account_size"], cfg["daily_loss_limit_pct"])

    return {
        "approved_entries": entries,
        "approved_exits": exits,
        "expiration_target": date.today().isoformat(),
        "strike_rule": "ATM — strike nearest the current SPY price",
        "config": cfg,
        "realized_pnl_today": pnl_today,
        "daily_loss_limit_hit": blocked,
        "note": ("Entries open a position (side=buy, position_effect=open). Exits close a "
                 "long (side=sell, position_effect=close)."),
    }


def cmd_size(proposal_id: str, premium: float) -> dict:
    cfg = _cfg()
    p = trade_proposals.get_proposal(proposal_id)
    if not p:
        return {"error": f"no proposal {proposal_id}"}
    if p["status"] != "approved":
        return {"error": f"proposal {proposal_id} is {p['status']}, expected approved"}

    contracts = position_sizing.suggested_contracts(
        premium, cfg["account_size"], cfg["risk_pct"], cfg["max_position_pct"])
    pnl_today = trading_journal.realized_pnl_today()
    blocked = position_sizing.daily_loss_limit_hit(
        pnl_today, cfg["account_size"], cfg["daily_loss_limit_pct"])

    total_cost = round(premium * 100 * contracts, 2)
    return {
        "proposal_id": proposal_id,
        "side": p["side"],
        "premium": premium,
        "suggested_contracts": contracts,
        "total_cost": total_cost,
        "cost_per_contract": round(premium * 100, 2),
        "pct_of_account": round(total_cost / cfg["account_size"] * 100, 2) if cfg["account_size"] else None,
        "risk_budget": round(cfg["account_size"] * cfg["risk_pct"] / 100, 2),
        "stop_premium": round(premium * (1 + cfg["stop_pct"] / 100), 2),
        "target_premium": round(premium * (1 + cfg["target_pct"] / 100), 2),
        "realized_pnl_today": pnl_today,
        "blocked_by_daily_limit": blocked,
        "blocked_reason": (f"daily loss limit of {cfg['daily_loss_limit_pct']}% hit "
                           f"(realized {pnl_today})") if blocked else None,
        "zero_size_reason": ("premium exceeds the entire risk budget — no whole contract fits"
                             if contracts == 0 and not blocked else None),
    }


def cmd_record_placed(args) -> dict:
    order_details = {
        "option_symbol": args.option_symbol,
        "strike": args.strike,
        "expiration": args.expiration,
        "option_type": args.option_type,
        "contracts": args.contracts,
        "premium": args.premium,
        "limit_price": args.limit_price if args.limit_price is not None else args.premium,
        "order_id": args.order_id,
        "placed_at": datetime.now().isoformat(timespec="seconds"),
    }
    updated = trade_proposals.set_status(args.proposal_id, "placed",
                                          note=args.note, order_details=order_details)
    if not updated:
        return {"error": f"no proposal {args.proposal_id}"}
    return {"recorded": "placed", "proposal": updated}


def cmd_record_closed(proposal_id: str, premium_exit: float, reason: str) -> dict:
    p = trade_proposals.get_proposal(proposal_id)
    if not p:
        return {"error": f"no proposal {proposal_id}"}

    # Accept either the exit proposal or the entry it closes.
    entry = p if p["kind"] == "entry" else trade_proposals.get_proposal(p.get("linked_entry_id") or "")
    if not entry:
        return {"error": f"could not resolve the entry for {proposal_id}"}
    details = entry.get("order_details") or {}
    if not details.get("premium"):
        return {"error": f"entry {entry['id']} has no recorded premium — cannot compute P&L"}

    trade = trading_journal.record_closed_trade(
        ticker=entry["ticker"],
        side=entry["side"],
        strike=details.get("strike"),
        expiration=details.get("expiration"),
        option_type=details.get("option_type"),
        contracts=details.get("contracts") or 0,
        premium_entry=details["premium"],
        premium_exit=premium_exit,
        entry_time=details.get("placed_at") or entry.get("updated_at"),
        exit_time=datetime.now().isoformat(timespec="seconds"),
        reason=reason,
    )
    trade_proposals.set_status(entry["id"], "closed", note=f"closed: {reason}")
    if p["kind"] == "exit":
        trade_proposals.set_status(p["id"], "placed", note=f"closed: {reason}")

    cfg = _cfg()
    pnl_today = trading_journal.realized_pnl_today()
    return {
        "recorded": "closed",
        "trade": trade,
        "realized_pnl_today": pnl_today,
        "daily_loss_limit_hit": position_sizing.daily_loss_limit_hit(
            pnl_today, cfg["account_size"], cfg["daily_loss_limit_pct"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-approved", help="proposals awaiting an order")

    s = sub.add_parser("size", help="contracts + risk gates for a premium")
    s.add_argument("--proposal-id", required=True)
    s.add_argument("--premium", required=True, type=float)

    r = sub.add_parser("record-placed", help="record a filled opening order")
    r.add_argument("--proposal-id", required=True)
    r.add_argument("--option-symbol", required=True)
    r.add_argument("--strike", required=True, type=float)
    r.add_argument("--expiration", required=True)
    r.add_argument("--option-type", required=True, choices=["call", "put"])
    r.add_argument("--contracts", required=True, type=int)
    r.add_argument("--premium", required=True, type=float)
    r.add_argument("--limit-price", type=float)
    r.add_argument("--order-id")
    r.add_argument("--note")

    c = sub.add_parser("record-closed", help="record a filled closing order")
    c.add_argument("--proposal-id", required=True)
    c.add_argument("--premium-exit", required=True, type=float)
    c.add_argument("--reason", default="manual")

    args = parser.parse_args()
    if args.command == "list-approved":
        out = cmd_list_approved()
    elif args.command == "size":
        out = cmd_size(args.proposal_id, args.premium)
    elif args.command == "record-placed":
        out = cmd_record_placed(args)
    else:
        out = cmd_record_closed(args.proposal_id, args.premium_exit, args.reason)

    print(json.dumps(out, indent=2, default=str))
    if isinstance(out, dict) and out.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
