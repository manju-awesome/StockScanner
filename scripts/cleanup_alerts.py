#!/usr/bin/env python3
"""
cleanup_alerts.py — delete alerts by date
=========================================
Prunes the two alert stores in data/:

    alerts_log.json     append-only history shown in "Recent Alert Log"
    alerts_state.json   the live dedup set behind "Active Alerts"

The log is safe to prune: it is a record, and deleting a row loses only the
record. The active set is not, which is why it takes an explicit flag.

Why --active is opt-in
----------------------
alerts_state.json is what stops a standing condition from re-alerting every
scan cycle. Deleting a key tells the engine it has never seen that condition,
so if the condition is STILL TRUE the next scan raises it again — and at
CRITICAL/HIGH that means a fresh email and Telegram push. Pruning the active
set is therefore a notification decision, not just a disk-space one. The tool
reports how many keys carry that risk before it acts, and --priority LOW,MEDIUM
is the version that can't re-notify you.

Usage
-----
    # what would go, nothing written
    python3 scripts/cleanup_alerts.py --days 14 --dry-run

    # prune the log only (the safe default)
    python3 scripts/cleanup_alerts.py --days 14

    # also clear standing low-grade noise, no re-notification risk
    python3 scripts/cleanup_alerts.py --days 7 --active --priority LOW,MEDIUM

    # everything older than a fixed date, including email-tier keys
    python3 scripts/cleanup_alerts.py --before 2026-07-01 --active --yes

Exit code is 0 on success, 1 on bad arguments.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import alerts as alerts_mod  # noqa: E402


def _parse_before(value: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"{value!r} is not a date — use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Delete alerts older than a date from data/alerts_log.json "
                    "and (optionally) data/alerts_state.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage\n-----\n", 1)[-1])
    age = p.add_mutually_exclusive_group(required=True)
    age.add_argument("--days", type=int, metavar="N",
                     help="delete alerts older than N days")
    age.add_argument("--before", type=_parse_before, metavar="DATE",
                     help="delete alerts created before DATE (YYYY-MM-DD)")
    p.add_argument("--active", action="store_true",
                   help="ALSO prune data/alerts_state.json. Off by default: a "
                        "still-true condition whose key is deleted will fire "
                        "again, and CRITICAL/HIGH re-notify by email/Telegram.")
    p.add_argument("--log-only", action="store_true",
                   help="explicit opposite of --active (the default behaviour)")
    p.add_argument("--priority", metavar="LIST",
                   help="restrict the --active purge to these tiers, e.g. "
                        "'LOW,MEDIUM' — the version that cannot re-notify")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be deleted; write nothing")
    p.add_argument("--yes", "-y", action="store_true",
                   help="skip the confirmation prompt when --active would "
                        "remove CRITICAL/HIGH keys")
    args = p.parse_args()

    if args.active and args.log_only:
        p.error("--active and --log-only contradict each other")
    if args.days is not None and args.days < 0:
        p.error("--days must be >= 0")

    priorities = None
    if args.priority:
        priorities = tuple(x.strip().upper() for x in args.priority.split(",") if x.strip())
        unknown = [x for x in priorities if x not in alerts_mod.PRIORITIES]
        if unknown:
            p.error(f"unknown priority {', '.join(unknown)} — "
                    f"choose from {', '.join(alerts_mod.PRIORITIES)}")
        if not args.active:
            p.error("--priority only applies to --active (the log is pruned by date alone)")

    kw = {"days": args.days, "before": args.before}
    tag = " (dry run — nothing written)" if args.dry_run else ""

    log_res = alerts_mod.prune_log(dry_run=args.dry_run, **kw)
    print(f"Cutoff: anything before {log_res['cutoff']}{tag}")
    print(f"  log    {log_res['removed']:>4} removed · {log_res['kept']:>4} kept "
          f"(of {log_res['total']})"
          + (f" · oldest kept {log_res['oldest_kept']}" if log_res["oldest_kept"] else ""))

    if not args.active:
        preview = alerts_mod.prune_active(dry_run=True, priorities=priorities, **kw)
        if preview["removed"]:
            print(f"  active {preview['removed']:>4} eligible but NOT touched — "
                  f"pass --active to prune them")
        else:
            print("  active    0 eligible")
        return 0

    # --active: check the notification consequence before doing it
    preview = alerts_mod.prune_active(dry_run=True, priorities=priorities, **kw)
    if not preview["removed"]:
        print("  active    0 eligible — nothing to prune")
        return 0

    if preview["refire_risk"]:
        print(f"\n  ⚠  {preview['refire_risk']} of these are CRITICAL/HIGH. If their "
              f"condition is still true,\n     the next scan re-raises them and sends a "
              f"fresh email + Telegram push.\n     Use --priority LOW,MEDIUM to prune only "
              f"the tiers that can't notify.")
        if not (args.yes or args.dry_run):
            reply = input("\n  Proceed? [y/N] ").strip().lower()
            if reply not in ("y", "yes"):
                print("  aborted — nothing written")
                return 0

    res = alerts_mod.prune_active(dry_run=args.dry_run, priorities=priorities, **kw)
    scope = f" at {'/'.join(priorities)}" if priorities else ""
    print(f"  active {res['removed']:>4} removed{scope} · {res['kept']:>4} kept "
          f"(of {res['total']}){tag}")
    if res["removed_keys"]:
        shown = ", ".join(res["removed_keys"][:8])
        more = f" … +{len(res['removed_keys']) - 8} more" if len(res["removed_keys"]) > 8 else ""
        print(f"         {shown}{more}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
