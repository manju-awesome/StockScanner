#!/usr/bin/env python3
"""
migrate_watchlists_nesting.py
=============================
One-off: rewrite data/watchlists.json with AI sublists nested under "AI",
and normalise the one sublist that never carried the "AI: " prefix
("AI Infrastructure" -> child "Infrastructure").

Before                              After
------                              -----
"AI": [...]                         "AI": {
"AI: Power": [...]                    "_tickers": [...],
"AI Infrastructure": [...]            "Power": [...],
                                      "Infrastructure": [...] }

The flat view every consumer reads is unchanged apart from the rename:
load_watchlists() still returns "AI" and "AI: Power". Safe to re-run.

    python scripts/migrate_watchlists_nesting.py --dry-run
    python scripts/migrate_watchlists_nesting.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.reporting.research import (          # noqa: E402
    NESTED_PARENTS, SUBLIST_SEP, PROJECT_DATA_DIR, WATCHLISTS_FILENAME,
    _flatten_watchlists, _nest_watchlists)

# Sublists whose stored name predates the "Parent: Child" convention.
RENAMES = {"AI Infrastructure": f"AI{SUBLIST_SEP}Infrastructure"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = PROJECT_DATA_DIR / WATCHLISTS_FILENAME
    if not path.exists():
        print(f"no watchlists file at {path}", file=sys.stderr)
        return 1

    raw = json.loads(path.read_text())
    flat = _flatten_watchlists(raw)
    before = {k: list(v) for k, v in flat.items()}

    renamed = []
    for old, new in RENAMES.items():
        if old in flat:
            if new in flat:
                print(f"  !! both {old!r} and {new!r} exist — merging into {new!r}")
                flat[new] = list(dict.fromkeys(list(flat[new]) + list(flat.pop(old))))
            else:
                flat[new] = flat.pop(old)
            renamed.append(f"{old} -> {new}")

    nested = _nest_watchlists(flat)

    # The whole point of the compat layer: the flat view must survive intact.
    roundtrip = _flatten_watchlists(nested)
    expected = dict(before)
    for old, new in RENAMES.items():
        if old in expected:
            expected[new] = expected.pop(old)
    if roundtrip != expected:
        only_rt = set(roundtrip) - set(expected)
        only_ex = set(expected) - set(roundtrip)
        diff = [k for k in set(roundtrip) & set(expected)
                if roundtrip[k] != expected[k]]
        print("ABORT — flat view would change:", file=sys.stderr)
        print(f"  added={sorted(only_rt)} removed={sorted(only_ex)} "
              f"changed={sorted(diff)}", file=sys.stderr)
        return 1

    children = {p: sorted(k for k in nested.get(p, {}) if k != "_tickers")
                for p in NESTED_PARENTS if isinstance(nested.get(p), dict)}
    print(f"lists (flat view) : {len(flat)}")
    for p, kids in children.items():
        own = len(nested[p].get("_tickers") or [])
        print(f"  {p}: {own} own ticker(s), {len(kids)} sublist(s)")
        for k in kids:
            print(f"     └─ {k} ({len(nested[p][k])})")
    if renamed:
        print("renamed          : " + ", ".join(renamed))
    print(f"flat view preserved: yes ({len(roundtrip)} lists)")

    if args.dry_run:
        print("\n--dry-run — nothing written")
        return 0

    backup = path.with_suffix(
        f".pre_nesting_{datetime.now():%Y%m%d_%H%M%S}.json")
    shutil.copy(path, backup)
    path.write_text(json.dumps(nested, indent=1))
    print(f"\nbackup  -> {backup.name}")
    print(f"written -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
