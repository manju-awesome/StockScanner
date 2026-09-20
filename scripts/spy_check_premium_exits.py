#!/usr/bin/env python3
"""
check_premium_exits.py
=======================
The deterministic half of the premium-based stop/target check. The daemon
can't see live option premiums (no broker session), so a Claude Code session
with the Robinhood connector supplies the quotes — but all the arithmetic
and state mutation lives here, in tested Python, rather than in a prompt.

Two subcommands, designed to bracket a single MCP quote lookup:

  list-open
      Prints JSON: the placed entry proposals that need a premium check,
      each with the option contract to quote and the entry premium.
      Prints {"open_positions": []} when there's nothing to check — the
      scheduled task should stop there on most runs.

  evaluate --premiums '{"<proposal_id>": 1.23, ...}'
      Given current premiums, decides stop/target per position using
      core.position_sizing.premium_exit_hit and creates a pending_review
      exit proposal for each breach. Prints JSON describing what it did.
      Idempotent — create_exit_proposal won't duplicate an active exit.

This script NEVER places or closes an order. It only writes proposals.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from Daytrades.core import position_sizing, trade_proposals  # noqa: E402


def _bands() -> tuple[float, float]:
    return (
        float(os.environ.get("OPTIONS_STOP_PCT", -35)),
        float(os.environ.get("OPTIONS_TARGET_PCT", 60)),
    )


def cmd_list_open() -> dict:
    open_positions = []
    for entry in trade_proposals.list_proposals(status="placed", kind="entry"):
        details = entry.get("order_details") or {}
        open_positions.append({
            "proposal_id": entry["id"],
            "side": entry["side"],
            "option_symbol": details.get("option_symbol"),
            "strike": details.get("strike"),
            "expiration": details.get("expiration"),
            "option_type": details.get("option_type"),
            "contracts": details.get("contracts"),
            "entry_premium": details.get("premium"),
        })

    stop_pct, target_pct = _bands()
    return {
        "open_positions": open_positions,
        "stop_pct": stop_pct,
        "target_pct": target_pct,
    }


def cmd_evaluate(premiums: dict) -> dict:
    stop_pct, target_pct = _bands()
    results = []

    for entry in trade_proposals.list_proposals(status="placed", kind="entry"):
        proposal_id = entry["id"]
        if proposal_id not in premiums:
            continue

        details = entry.get("order_details") or {}
        entry_premium = details.get("premium")
        current_premium = premiums[proposal_id]

        if not entry_premium:
            results.append({
                "proposal_id": proposal_id,
                "error": "no entry premium recorded in order_details",
            })
            continue

        hit = position_sizing.premium_exit_hit(
            float(entry_premium), float(current_premium), stop_pct, target_pct
        )
        move_pct = round((float(current_premium) - float(entry_premium)) / float(entry_premium) * 100, 2)

        record = {
            "proposal_id": proposal_id,
            "entry_premium": entry_premium,
            "current_premium": current_premium,
            "move_pct": move_pct,
            "breach": hit,
            "exit_proposal_created": None,
        }

        if hit:
            exit_proposal = trade_proposals.create_exit_proposal(proposal_id, f"premium_{hit}")
            record["exit_proposal_created"] = exit_proposal["id"] if exit_proposal else "already_exists"

        results.append(record)

    return {"evaluated": results, "stop_pct": stop_pct, "target_pct": target_pct}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-open", help="list placed positions needing a premium check")
    ev = sub.add_parser("evaluate", help="evaluate supplied premiums against the stop/target bands")
    ev.add_argument("--premiums", required=True, help='JSON: {"<proposal_id>": <current_premium>, ...}')

    args = parser.parse_args()
    if args.command == "list-open":
        print(json.dumps(cmd_list_open(), indent=2))
    else:
        try:
            premiums = json.loads(args.premiums)
        except ValueError as exc:
            print(json.dumps({"error": f"--premiums must be valid JSON: {exc}"}))
            sys.exit(1)
        print(json.dumps(cmd_evaluate(premiums), indent=2))


if __name__ == "__main__":
    main()
