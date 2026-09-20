#!/usr/bin/env python3
"""
sync_broker_positions.py
========================
Writes broker-reported holdings into data/portfolio.csv and
data/options_positions.csv. Driven by the `get-portfolio` skill, which
fetches the positions through the Robinhood MCP tools and pipes the JSON
here; every merge rule and every number lives in core/broker_sync.py where
tests pin it, so a run is deterministic and reviewable.

This script only ever reads broker data and writes local CSVs. It cannot
place, modify or cancel an order — it has no broker session at all.

Usage
-----
    # what's on disk now (no writes)
    python3 scripts/sync_broker_positions.py show

    # dry run — prints the same report a real sync would, writes nothing
    python3 scripts/sync_broker_positions.py sync --dry-run \\
        --equities '<get_equity_positions JSON>' \\
        --options  '<get_option_positions JSON>'

    # real sync, with current option premiums from get_option_quotes
    python3 scripts/sync_broker_positions.py sync \\
        --equities '<json>' --options '<json>' \\
        --premiums '{"SPY260725C00601000": 1.23}' \\
        --account 1234

Payloads may also be given as @path/to/file.json, or "-" to read that
argument from stdin — a large positions payload doesn't belong in an argv
string.

Every write is preceded by a timestamped backup in data/backups/, because
portfolio.csv is hand-maintained state that took real work to build and a
bad payload should never be able to cost you it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stockanalysis.core import broker_sync  # noqa: E402
from stockanalysis.reporting import options_positions, portfolio  # noqa: E402

BACKUP_DIR = PROJECT_ROOT / "data" / "backups"


def _load_arg(value: str | None):
    """Accept inline JSON, @file, or "-" for stdin."""
    if not value:
        return None
    value = value.strip()
    if value == "-":
        return json.loads(sys.stdin.read())
    if value.startswith("@"):
        return json.loads(Path(value[1:]).expanduser().read_text())
    return json.loads(value)


def _rel(path: Path) -> str:
    """Project-relative path for display, falling back to the absolute one.
    Path.relative_to() raises rather than returning None for a path outside
    the root — and a cosmetic path label has no business aborting a sync."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _backup(path: Path, stamp: str) -> str | None:
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    return _rel(dest)


def cmd_show(args) -> dict:
    positions = portfolio.load_positions()
    options = options_positions.build_options_view(options_positions.load_options())
    held = [p for p in positions if (p.get("Shares") or 0)]
    return {
        "portfolio_csv": _rel(portfolio.PORTFOLIO_PATH),
        "options_csv": _rel(options_positions.OPTIONS_PATH),
        "equity_rows": len(positions),
        "equity_held": [{"ticker": p["Ticker"], "shares": p["Shares"],
                         "avg_cost": p["Avg_Cost"], "strategy": p["Strategy"],
                         "source": (p.get("_extra") or {}).get("Source", "manual")}
                        for p in held],
        "watchlist_rows": len(positions) - len(held),
        "options": [{"contract": o["Label"], "symbol": o.get("Option_Symbol"),
                     "contracts": o["Contracts"], "side": o["Side"],
                     "avg_premium": o["Avg_Premium"],
                     "current_premium": o["Current_Premium"],
                     "days_to_expiry": o["Days_To_Expiry"],
                     "gain_dollars": o["Gain_Dollars"],
                     "alerts": o["Alerts"]} for o in options],
        "options_totals": options_positions.options_totals(options),
    }


def cmd_sync(args) -> dict:
    # --equities/--options are repeatable so several accounts merge in one
    # pass; syncing them sequentially would make each run look like the
    # previous account's positions had been sold.
    equity_payloads = [_load_arg(v) for v in (args.equities or [])]
    option_payloads = [_load_arg(v) for v in (args.options or [])]
    instruments = broker_sync.index_instruments(_load_arg(args.instruments))
    premiums = _load_arg(args.premiums) or {}

    if not equity_payloads and not option_payloads:
        raise SystemExit("nothing to sync: pass --equities and/or --options")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    synced_at = datetime.now().isoformat(timespec="seconds")
    account = (args.account or "").strip()
    result: dict = {"synced_at": synced_at, "dry_run": bool(args.dry_run),
                    "account": account or None, "backups": []}

    if equity_payloads:
        broker_eq = broker_sync.normalize_equities(*equity_payloads)
        existing = portfolio.load_positions()
        merged, report = broker_sync.merge_equities(
            existing, broker_eq, account=account, synced_at=synced_at)
        report["broker_positions_seen"] = len(broker_eq)
        result["equities"] = report
        if not args.dry_run:
            backup = _backup(portfolio.PORTFOLIO_PATH, stamp)
            if backup:
                result["backups"].append(backup)
            portfolio.save_positions(merged)

    if option_payloads:
        broker_opt = broker_sync.normalize_options(*option_payloads,
                                                   instruments=instruments)
        existing_opts = options_positions.load_options()
        merged_opts, report = broker_sync.merge_options(
            existing_opts, broker_opt, premiums=premiums, account=account,
            synced_at=synced_at)
        report["broker_contracts_seen"] = len(broker_opt)
        result["options"] = report
        if not args.dry_run:
            backup = _backup(options_positions.OPTIONS_PATH, stamp)
            if backup:
                result["backups"].append(backup)
            options_positions.save_options(merged_opts)
        result["options_view"] = [
            {"contract": o["Label"], "contracts": o["Contracts"],
             "avg_premium": o["Avg_Premium"], "current_premium": o["Current_Premium"],
             "days_to_expiry": o["Days_To_Expiry"], "gain_dollars": o["Gain_Dollars"],
             "gain_pct": o["Gain_Pct"], "alerts": o["Alerts"]}
            for o in options_positions.build_options_view(merged_opts)]

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="print the current local portfolio state")

    p_sync = sub.add_parser("sync", help="merge broker holdings into the CSVs")
    p_sync.add_argument("--equities", action="append",
                        help="get_equity_positions JSON (or @file, or -). "
                             "Repeat once per account.")
    p_sync.add_argument("--options", action="append",
                        help="get_option_positions JSON (or @file, or -). "
                             "Repeat once per account.")
    p_sync.add_argument("--instruments",
                        help="get_option_instruments JSON (or @file) — supplies "
                             "the strike and call/put the positions payload omits")
    p_sync.add_argument("--premiums", help='{"<option_id>": <per-share premium>} JSON')
    p_sync.add_argument("--account", default="", help="account label to record (e.g. last 4)")
    p_sync.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")

    args = parser.parse_args()
    handler = {"show": cmd_show, "sync": cmd_sync}[args.command]
    print(json.dumps(handler(args), indent=2, default=str))


if __name__ == "__main__":
    main()
