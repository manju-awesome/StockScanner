"""
Remove tickers the data source can no longer resolve from the Research
library — delisted, acquired or renamed symbols that come back empty from
every scan.

A ticker qualifies when no scan on disk ever obtained a price for it
(core.research_snapshot.has_quote). That is a deliberately strict test: a
symbol that fetched successfully even once keeps its data and stays, so a
single throttled scan can never prune a live name. The scan pipeline
categorizes these husks "Avoid" with reason "MarketCap<1B, Price<$5",
because the entry gate sees a missing price rather than a missing company —
which is why they can't simply be filtered out by category.

Four places have to be cleared or they grow back:

    research_index.json     the library itself
    research_snapshot.json  the durable copy — research_snapshot.merged()
                            unions index and snapshot keys, so a ticker left
                            here reappears in the library on the next read
    research/<T>.html       the generated page behind the "Open" link
    watchlists.json         membership, including the sp500 universe list —
                            leave that and the next sp500 scan re-adds the
                            ticker and regenerates its husk entry

Dry run by default; --apply performs the removal and writes a timestamped
backup of every file it touches to data/backups/ first.

    python3 scripts/prune_unlisted_tickers.py              # report only
    python3 scripts/prune_unlisted_tickers.py --apply
    python3 scripts/prune_unlisted_tickers.py --apply --keep-universe
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stockanalysis.core import research_snapshot as RS   # noqa: E402
from stockanalysis.reporting import research             # noqa: E402

OUTPUT_DIR = ROOT / "data" / "output"
INDEX_PATH = OUTPUT_DIR / "research_index.json"
RESEARCH_DIR = OUTPUT_DIR / "research"
BACKUP_DIR = ROOT / "data" / "backups"


def find_unlisted() -> list[str]:
    """Tickers in the library that no scan ever got a quote for."""
    index = json.loads(INDEX_PATH.read_text()) if INDEX_PATH.exists() else {}
    entries = RS.merged(index, RS.load(OUTPUT_DIR))
    return sorted(e["ticker"] for e in entries
                  if e.get("ticker") and not RS.has_quote(e))


def _backup(paths: list[Path], stamp: str) -> list[Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for p in paths:
        if p.exists():
            dest = BACKUP_DIR / f"{p.stem}_{stamp}{p.suffix}"
            shutil.copy2(p, dest)
            saved.append(dest)
    return saved


def prune(tickers: list[str], apply: bool, keep_universe: bool) -> dict:
    drop = set(tickers)
    report: dict = {"tickers": sorted(drop), "index": 0, "snapshot": 0,
                    "pages": 0, "watchlists": {}, "backups": []}
    if not drop:
        return report

    index = json.loads(INDEX_PATH.read_text()) if INDEX_PATH.exists() else {}
    snap = RS.load(OUTPUT_DIR)
    watchlists = research.load_watchlists()

    report["index"] = sum(1 for t in drop if t in index)
    report["snapshot"] = sum(1 for t in drop if t in snap)
    report["pages"] = sum(1 for t in drop
                          if (RESEARCH_DIR / f"{t}.html").exists())
    for name, tickers_in in watchlists.items():
        hit = [t for t in (tickers_in or []) if t in drop]
        # The universe lists decide what gets scanned next; --keep-universe
        # prunes the library but leaves them, in which case the tickers
        # return on the next scan of that list.
        if hit and not (keep_universe and name.lower() in ("sp500",)):
            report["watchlists"][name] = sorted(hit)

    if not apply:
        return report

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report["backups"] = [str(p) for p in _backup(
        [INDEX_PATH, RS.snapshot_path(OUTPUT_DIR),
         ROOT / "data" / "watchlists.json"], stamp)]

    for t in drop:
        index.pop(t, None)
        snap.pop(t, None)
        page = RESEARCH_DIR / f"{t}.html"
        if page.exists():
            page.unlink()

    INDEX_PATH.write_text(json.dumps(index, indent=1))
    RS.save(OUTPUT_DIR, snap)

    for name, hit in report["watchlists"].items():
        watchlists[name] = [t for t in (watchlists.get(name) or [])
                            if t not in set(hit)]
    if report["watchlists"]:
        research.save_watchlists(watchlists)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="perform the removal (default: report only)")
    ap.add_argument("--keep-universe", action="store_true",
                    help="don't remove them from the sp500 scan list "
                         "(they will come back on the next scan)")
    args = ap.parse_args()

    tickers = find_unlisted()
    if not tickers:
        print("Nothing to prune — every ticker in the library has a quote.")
        return 0

    report = prune(tickers, args.apply, args.keep_universe)
    verb = "Removed" if args.apply else "Would remove"
    print(f"{len(report['tickers'])} ticker(s) with no quote from any scan:")
    print("  " + " ".join(report["tickers"]))
    print()
    print(f"{verb}:")
    print(f"  research_index.json     {report['index']} entries")
    print(f"  research_snapshot.json  {report['snapshot']} entries")
    print(f"  research/*.html         {report['pages']} pages")
    for name, hit in sorted(report["watchlists"].items()):
        print(f"  watchlist {name!r}: {len(hit)}")
    if args.keep_universe:
        print("  (sp500 kept — these will return on the next sp500 scan)")
    if report["backups"]:
        print("\nBackups written:")
        for b in report["backups"]:
            print(f"  {b}")
    if not args.apply:
        print("\nDry run. Re-run with --apply to perform the removal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
