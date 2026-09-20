"""
store.py — snapshot storage for the compounder scan.
====================================================
The page renders a stored run rather than scanning on request, for the same
reason /csp and StockDayTrade do: a full scan is six network calls per
company across the whole theme library, which is minutes of work — far past
any request timeout.

Staleness here is measured in WEEKS, not minutes
------------------------------------------------
This is the opposite of the CSP snapshot's problem. A $1.20 option bid is
wrong an hour later; a company's four-year gross margin trend is the same
next month. Every input this engine uses moves on a filing cadence — annual
statements once a year, quarterlies four times, insider filings within two
days of a trade — so a snapshot is fully valid for weeks and the only
genuinely fast-moving field is price-derived relative strength.

So the age note is graded in weeks, and it says which parts have gone stale
rather than declaring the whole run bad: after a month the fundamentals are
still exactly as filed and only the discovery leg has drifted. A page that
demanded a fresh scan daily would be lying about where the information
comes from.

The one real staleness risk is earnings season, when a quarter of the
library reports inside three weeks and the newest quarter — the input the
acceleration reading leans on hardest — is missing from the snapshot for
every name that has reported. `age_note` says so explicitly rather than
leaving the reader to work out the calendar.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parents[4] / "data" / "compounder"
_SNAPSHOT = _DIR / "compounder_snapshot.json"

# Age bands, in days, and what has actually decayed by each one.
FRESH_DAYS = 21
AGING_DAYS = 60


def _json_safe(obj):
    """numpy/pandas scalars break json.dumps(); floats and ints do not.

    This project has been bitten by it repeatedly and the statement frames
    these scores come off are full of numpy float64, so the conversion is
    done once here on the way to disk. `float('inf')` — which the runway
    field legitimately produces for a company with no burn — is mapped to
    None rather than written, because `Infinity` is not valid JSON and
    json.load accepts it back only by accident.
    """
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (bool, str)) or obj is None:
        return obj
    if hasattr(obj, "item"):                        # numpy scalar
        try:
            return _json_safe(obj.item())
        except (ValueError, AttributeError):
            return str(obj)
    if isinstance(obj, (int, float)):
        if obj != obj:                              # NaN
            return None
        if obj in (float("inf"), float("-inf")):
            return None
        return obj
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    return str(obj)


def save(rows, watchlist=None, meta=None, merge: bool = False) -> Path:
    """Write the run.

    `merge` unions by ticker rather than replacing, so a targeted re-scan of
    six names refreshes those six and leaves the rest of the library
    standing. A plain write would delete a hundred and twenty rows to update
    six, which is how a "refresh" quietly becomes a data loss.
    """
    _DIR.mkdir(parents=True, exist_ok=True)
    rows = list(rows or [])

    if merge:
        existing = load() or {}
        by_ticker = {r.get("ticker"): r for r in (existing.get("rows") or [])}
        for r in rows:
            by_ticker[r.get("ticker")] = r          # fresh rows win
        rows = list(by_ticker.values())
        rows.sort(key=lambda r: -(r.get("score") or 0))

    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "count": len(rows),
        "meta": meta or {},
        "rows": rows,
        # Recomputed on merge rather than carried over: a merged run has a
        # different top 20 than either input, and storing the old one would
        # show a watchlist that disagrees with the table beneath it.
        "watchlist": watchlist,
    }
    _SNAPSHOT.write_text(json.dumps(_json_safe(payload), indent=1))
    return _SNAPSHOT


def load() -> dict | None:
    if not _SNAPSHOT.exists():
        return None
    try:
        return json.loads(_SNAPSHOT.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def age_note(snapshot) -> tuple[str, str]:
    """(status, human sentence). Status is one of fresh / aging / stale.

    Says WHAT decayed, not just how old the file is — see the module
    docstring. The distinction matters because the honest answer for a
    six-week-old run is "the fundamentals are exactly as filed, the
    discovery leg has drifted", and a flat "stale" would send a reader to
    re-run a scan that would produce nearly identical numbers.
    """
    if not snapshot:
        return "stale", "No scan stored yet — run one to populate the page."
    try:
        when = _dt.datetime.fromisoformat(snapshot["generated_at"])
    except (KeyError, ValueError):
        return "stale", "Snapshot has no readable timestamp."

    days = (_dt.datetime.now() - when).days
    stamp = when.strftime("%d %b %Y, %H:%M")

    if days <= FRESH_DAYS:
        return "fresh", (f"Scanned {stamp} ({days}d ago). Every input is on "
                         f"a filing cadence, so this is current.")
    if days <= AGING_DAYS:
        return "aging", (
            f"Scanned {stamp} ({days}d ago). The statement-derived scores — "
            f"growth, margins, reinvestment, dilution — are still exactly as "
            f"filed. Relative strength and analyst coverage have drifted, and "
            f"any company that has reported since is missing its newest "
            f"quarter, which is the input acceleration leans on hardest.")
    return "stale", (
        f"Scanned {stamp} ({days}d ago) — most of the library has reported at "
        f"least one quarter since. The growth acceleration readings are the "
        f"first thing to distrust; re-scan before acting on the ranking.")
