"""
Snapshot storage for the institutional 13F engine.

The page renders a stored run rather than building one on request, for the
same reason /csp does: a run streams a 400 MB information table and can
download a 95 MB zip first, which is far past any request timeout.

The freshness question here is the opposite of CSP's, though, and the
store is shaped around that difference. A CSP snapshot goes stale in
hours, because it holds option prices. This one cannot go stale in any
useful sense: it holds a quarter-end photograph that will not change
again. Re-running it an hour later returns byte-identical numbers.

So `age_note` reports the age of the DATA, not the age of the run. A
snapshot generated this morning off the March quarter is 144 days old,
and saying "generated 2 hours ago" would be true, useless, and actively
misleading — it implies a freshness the underlying filings do not have.
The only thing that makes this snapshot obsolete is a new quarter's zip
being published, which `stale_for_period` answers directly.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parents[4] / "data" / "institutional"
_SNAPSHOT = _DIR / "institutional_snapshot.json"


def _json_safe(obj):
    """numpy/pandas scalars break json.dumps(); floats and ints do not.

    Nothing in this package touches pandas today, but every other store in
    the project grew this the hard way and a snapshot writer is exactly
    where it bites."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (bool, str)) or obj is None:
        return obj
    if hasattr(obj, "item"):                       # numpy scalar
        try:
            return _json_safe(obj.item())
        except (ValueError, AttributeError):
            return str(obj)
    if isinstance(obj, (int, float)):
        return None if obj != obj else obj         # NaN
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    return str(obj)


def save(payload: dict) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["generated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    tmp = _SNAPSHOT.with_suffix(".tmp")
    tmp.write_text(json.dumps(_json_safe(body), indent=1))
    tmp.replace(_SNAPSHOT)
    return _SNAPSHOT


def load() -> dict | None:
    if not _SNAPSHOT.exists():
        return None
    try:
        return json.loads(_SNAPSHOT.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def age_note(snapshot: dict | None) -> tuple[str, str]:
    """(text, colour) describing how old the DATA is.

    Deliberately not the age of the run. 13F is filed up to 45 days after
    the quarter it describes, and the bulk file lands weeks after that, so
    every reading of this page is looking at something months old. Burying
    that behind a recent generation timestamp would be the single most
    misleading thing this page could do.
    """
    if not snapshot:
        return "no snapshot yet", "#898781"
    period = snapshot.get("period")
    if not period:
        return "period unknown", "#b45309"
    try:
        age = (_dt.date.today() - _dt.date.fromisoformat(period)).days
    except ValueError:
        return "period unreadable", "#b45309"
    # One quarter behind is the floor, not a warning — it is what this
    # data source IS. Two quarters means a published zip was never picked
    # up, which is worth colouring.
    colour = "#898781" if age <= 135 else ("#0b0b0b" if age <= 225 else "#b45309")
    return f"positions as at {period} — {age} days ago", colour


def stale_for_period(snapshot: dict | None, latest_period: _dt.date) -> bool:
    """Whether a newer quarter has been published since this was built."""
    if not snapshot or not snapshot.get("period"):
        return True
    try:
        return _dt.date.fromisoformat(snapshot["period"]) < latest_period
    except ValueError:
        return True
