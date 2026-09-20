"""
Publish the Future Compounder library into data/watchlists.json as a
scannable group, so /compounder's research ranking can be fed through the
main scanner, the Research Library and the backtester.

    Compounders              every company in the stored scan
    Compounders: Conviction  tier CONVICTION + STRONG CANDIDATE
    Compounders: Watchlist   the 10-Year Watchlist exactly

Why a script and not a one-time paste
--------------------------------------
The compounder snapshot is re-scanned periodically and its membership moves:
THEME_MEMBERS gains names, companies graduate past $20B, tiers change as
statements land. A list copied by hand is right for one week and quietly
wrong afterwards, and the failure is invisible — the scanner keeps returning
results for a group that no longer matches the page it was named after.
Re-running this is the whole maintenance story.

Why three lists rather than one
--------------------------------
141 names is past scan_universe.FOCUS_MIN_UNIVERSE (100), so a scan of the
full group with sector_focus="auto" gets the regime/sector funnel applied
and covers only part of it. That is correct behaviour for a big index
universe and wrong for a curated research list, so the two conviction
subsets exist to be scanned in full without thinking about it. Scanning the
parent is still fine — use sector_focus="off" when you want all 141.

The subsets are separate lists, never rolled up: research.NESTED_PARENTS
does not include "Compounders", so these stay flat "Parent: Child" keys.
tree_ordered_names() still groups them under the parent in every picker, and
ticking "Compounders" scans exactly its own 141 rather than 141 + subsets
counted twice.

Dry run by default; --apply writes the change after backing up
watchlists.json to data/backups/.

    python3 scripts/sync_compounder_watchlist.py            # report only
    python3 scripts/sync_compounder_watchlist.py --apply
    python3 scripts/sync_compounder_watchlist.py --apply --only Compounders
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stockanalysis.core.compounder import store as CS          # noqa: E402
from stockanalysis.reporting.research import (                 # noqa: E402
    PROJECT_DATA_DIR, WATCHLISTS_FILENAME, load_watchlists, save_watchlists)

PARENT = "Compounders"
CONVICTION_TIERS = ("CONVICTION", "STRONG CANDIDATE")


def _lists(snap: dict) -> dict[str, list[str]]:
    """The three lists, from the stored snapshot.

    Order is the snapshot's own — descending Future Compounder Score — so the
    list reads best-first in the picker rather than alphabetically, and
    de-duplicated defensively: a ticker appearing under two themes would
    otherwise be scanned twice.
    """
    rows = snap.get("rows") or []

    def dedupe(tickers):
        seen, out = set(), []
        for t in tickers:
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    watchlist_rows = (snap.get("watchlist") or {}).get("rows") or []
    return {
        PARENT: dedupe(r.get("ticker") for r in rows),
        f"{PARENT}: Conviction": dedupe(
            r.get("ticker") for r in rows if r.get("tier") in CONVICTION_TIERS),
        f"{PARENT}: Watchlist": dedupe(
            r.get("ticker") for r in watchlist_rows),
    }


def _backup() -> Path:
    src = PROJECT_DATA_DIR / WATCHLISTS_FILENAME
    dest_dir = PROJECT_DATA_DIR / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"watchlists_{stamp}.json"
    shutil.copy2(src, dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the change (default is a dry run)")
    ap.add_argument("--only", action="append", metavar="LIST",
                    help="sync only this list; repeatable")
    args = ap.parse_args()

    snap = CS.load()
    if not snap or not snap.get("rows"):
        print("No compounder snapshot stored — nothing to publish.")
        print("Run: python -m stockanalysis.core.compounder.scan")
        return 1

    wanted = _lists(snap)
    if args.only:
        missing = [n for n in args.only if n not in wanted]
        if missing:
            print(f"Unknown list(s): {', '.join(missing)}")
            print(f"Choose from: {', '.join(wanted)}")
            return 2
        wanted = {n: v for n, v in wanted.items() if n in args.only}

    current = load_watchlists()
    scanned = snap.get("scanned_at") or snap.get("generated_at") or "unknown"
    print(f"Compounder snapshot {scanned} — {len(snap['rows'])} companies\n")

    changed = False
    for name, tickers in wanted.items():
        before = list(current.get(name) or [])
        added = [t for t in tickers if t not in before]
        removed = [t for t in before if t not in tickers]
        if not before:
            print(f"  + {name}: new list, {len(tickers)} tickers")
        elif added or removed:
            print(f"  ~ {name}: {len(before)} -> {len(tickers)} "
                  f"(+{len(added)} / -{len(removed)})")
            if added:
                print(f"      added:   {', '.join(added[:12])}"
                      f"{' …' if len(added) > 12 else ''}")
            if removed:
                print(f"      removed: {', '.join(removed[:12])}"
                      f"{' …' if len(removed) > 12 else ''}")
        else:
            print(f"  = {name}: unchanged, {len(tickers)} tickers")
            continue
        changed = True
        current[name] = tickers

    if not changed:
        print("\nNothing to do — every list already matches the snapshot.")
        return 0

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0

    print(f"\nBacked up watchlists.json -> {_backup()}")
    save_watchlists(current)
    print("Written. The groups appear in the scanner's universe picker, the "
          "Research Library and the backtester without a restart.")
    print(f"\nNote: {PARENT} holds {len(wanted.get(PARENT, []))} names, past "
          f"the 100-name funnel threshold — scan it with sector_focus=\"off\" "
          f"for full coverage, or use the two subsets, which are under it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
