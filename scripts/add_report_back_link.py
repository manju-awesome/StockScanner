#!/usr/bin/env python3
"""
add_report_back_link.py
=======================
Inject the "← Back to Dashboard" control into report HTML files that were
generated before reporting.dashboard grew one.

The Dashboard links to each report as /<name>Report_<ts>.html, so following
that link leaves the webapp with no way back. New reports carry the control
from the generator; this backfills the ones already on disk.

Idempotent — files already containing the marker are left alone.

    python scripts/add_report_back_link.py            # data/output, all reports
    python scripts/add_report_back_link.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.reporting.dashboard import (
    BACK_LINK_MARKER, BACK_TO_DASHBOARD_HTML)

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "output"
PATTERNS = ("*Report_*.html", "dashboard_*.html")


def inject(html: str) -> str | None:
    """Return the patched HTML, or None if it can't/needn't be patched."""
    if BACK_LINK_MARKER in html:
        return None
    idx = html.find("<body>")
    if idx == -1:
        return None
    cut = idx + len("<body>")
    return html[:cut] + "\n\n" + BACK_TO_DASHBOARD_HTML + "\n" + html[cut:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                    help="directory of generated reports (default: data/output)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args()

    if not args.dir.is_dir():
        print(f"not a directory: {args.dir}", file=sys.stderr)
        return 1

    files = sorted({f for pat in PATTERNS for f in args.dir.glob(pat)})
    if not files:
        print(f"no report files under {args.dir}")
        return 0

    patched = skipped = failed = 0
    for f in files:
        try:
            html = f.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  !! {f.name}: unreadable ({e})")
            failed += 1
            continue
        out = inject(html)
        if out is None:
            skipped += 1
            continue
        if not args.dry_run:
            try:
                f.write_text(out, encoding="utf-8")
            except Exception as e:
                print(f"  !! {f.name}: write failed ({e})")
                failed += 1
                continue
        print(f"  {'would patch' if args.dry_run else 'patched'} {f.name}")
        patched += 1

    verb = "would patch" if args.dry_run else "patched"
    print(f"\n{verb} {patched}, already had it {skipped}, failed {failed} "
          f"(of {len(files)} report file(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
