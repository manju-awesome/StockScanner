"""
store.py — the scan snapshot on disk
====================================
One JSON file, `data/output/stockdaytrade_scan.json`, written by whoever ran
the scan last (CLI or webapp job) and read by the page.

The webapp cannot run this scan inside a request. It screens the market,
pulls bars for 25 symbols and hits `.info` and news per ticker — one to two
minutes, far past any sane request timeout — so the page renders the last
snapshot and a button starts a background job that replaces it. That is the
same shape the Scanner and AI Sentiment pages already use.

Serialisation is here rather than in the CLI because both callers need the
identical shape, and two copies of "drop the DataFrames, cast the numpy
scalars" would drift the moment one of them gained a field. Two things must
happen before this reaches `json.dumps`:

  * the session carries live DataFrames (`bars`, `premarket_bars`,
    `opening_bars`) so the report can reach them — they are dropped, not
    serialised;
  * yfinance hands back numpy scalars and pandas Timestamps, which
    `json.dumps` refuses. `native()` casts them.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from stockanalysis.core.daytrade._common import native

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
SNAPSHOT = OUTPUT_DIR / "stockdaytrade_scan.json"
# Where the snapshot lived before the page was renamed from Small-Cap. Read
# as a fallback so an existing snapshot keeps rendering until the next scan
# replaces it; never written.
LEGACY_SNAPSHOT = OUTPUT_DIR / "daytrade_smallcap.json"

# Frames live on the session for the report's benefit and must never be
# serialised — they are megabytes each and json.dumps cannot encode them.
_FRAME_KEYS = ("bars", "premarket_bars", "opening_bars")


def serialisable(result: dict) -> dict:
    """A JSON-safe copy of a scan result."""
    out = {
        "asof": str(result.get("asof")),
        # Timezone-aware, because the writer and the reader are different
        # processes and need not agree on local time. The CLI runs in the
        # shell's timezone and the webapp is launched with
        # TZ=America/New_York, so a naive stamp made a scan that had just
        # finished read as "1.0 hours ago" — and the staleness banner is
        # load-bearing here, since an intraday level an hour old looks
        # identical to a live one and is worthless.
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "notes": list(result.get("notes") or []),
        "regime": native(result.get("regime")),
        "settings": native(result.get("settings")),
        # What was ASKED for, not what the rows resolved to. The page
        # preselects its controls from these, and inferring them from the
        # rows instead showed "Large cap / Market screen" after an auto
        # scan of the daytrade list — so hitting Run again would silently
        # have run a different scan than the one on screen.
        "profile_requested": result.get("profile_requested"),
        "universe": result.get("universe") or "",
        "rows": [],
    }
    for r in result.get("rows", []):
        row = dict(r)
        sess = {k: v for k, v in (row.get("session") or {}).items()
                if k not in _FRAME_KEYS}
        sess["asof"] = str(sess.get("asof"))
        row["session"] = sess
        row["asof"] = str(row.get("asof"))
        out["rows"].append(native(row))
    return out


def save(result: dict, path: Path | None = None) -> Path:
    """Write the snapshot atomically.

    Via a temp file and os.replace rather than a plain write_text, because
    the reader is a web page that can load at any instant. A direct write
    of a ~120 KB document is not atomic, and a page load landing mid-write
    reads a truncated file — observed during development as a scan that
    appeared to return 5 rows with 13 confirmations when it had actually
    produced 16 with 14. os.replace is atomic on POSIX, so a reader sees
    either the whole previous snapshot or the whole new one.
    """
    path = path or SNAPSHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(serialisable(result), indent=2, default=str))
        os.replace(tmp, path)
    except BaseException:
        # A failure between the write and the replace otherwise leaves a
        # full-size orphan next to the real snapshot — which is exactly
        # what a missing `import os` did here, silently, on every --save.
        tmp.unlink(missing_ok=True)
        raise
    return path


def load(path: Path | None = None) -> dict | None:
    """The last snapshot, or None when there is none / it is unreadable.

    An unreadable snapshot returns None rather than raising, so a truncated
    write (the page loading while a job is mid-save) shows the empty state
    for one refresh instead of a stack trace.

    Falls back to the pre-rename path when the current one is absent, so
    the page is not blank between the rename and the next scan.
    """
    candidates = [path] if path else [SNAPSHOT, LEGACY_SNAPSHOT]
    for candidate in candidates:
        try:
            return json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
    return None
