"""
Where the profile and the last computed snapshot live.

Two files, and the split is deliberate
----------------------------------------
`profile.json` is INPUT — the facts you entered. It is the only file a
human edits and the only one whose loss would cost anything.

`snapshot.json` is OUTPUT — everything the engine computed from that
input. It is fully derivable and exists so the page renders instantly and
so month-over-month comparisons (section 20) have something to compare
against.

Because the snapshot is derived, it also records the profile it came
from. A page showing a score computed from last month's balances while
the profile has since changed is worse than a page showing nothing, so
the view checks that stamp and says so.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parents[4] / "data" / "retirement"
_PROFILE = _DIR / "profile.json"
_SNAPSHOT = _DIR / "snapshot.json"
_HISTORY = _DIR / "history.jsonl"


def _json_safe(obj):
    """numpy scalars break json.dumps(); this project has been bitten by
    that more than once, so the conversion happens on the way to disk."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (bool, str)) or obj is None:
        return obj
    if hasattr(obj, "item") and not isinstance(obj, (int, float)):
        try:
            return _json_safe(obj.item())
        except (ValueError, AttributeError):
            return str(obj)
    if isinstance(obj, float):
        return None if obj != obj else (obj if abs(obj) != float("inf")
                                        else None)
    if isinstance(obj, int):
        return obj
    return str(obj)


def _read(path: Path):
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(_json_safe(payload), fh, indent=2)
    tmp.replace(path)


def load_profile() -> dict | None:
    return _read(_PROFILE)


def save_profile(raw: dict) -> None:
    raw = dict(raw)
    raw["updated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    _write(_PROFILE, raw)


def profile_path() -> Path:
    return _PROFILE


def load_snapshot() -> dict | None:
    return _read(_SNAPSHOT)


def save_snapshot(snapshot: dict) -> None:
    _write(_SNAPSHOT, snapshot)
    _append_history(snapshot)


def _append_history(snapshot: dict) -> None:
    """One line per run, holding only the handful of figures a monthly or
    annual review compares.

    Deliberately small. The full snapshot is large and mostly derived; a
    history file that stored all of it would be unreadable and would make
    "why did my retirement date move two years" harder to answer, not
    easier.
    """
    row = {
        "at": snapshot.get("generated_at"),
        "net_worth": (snapshot.get("balance") or {}).get("net_worth"),
        "retirement_assets": (snapshot.get("balance") or {}).get(
            "retirement_assets"),
        "target": snapshot.get("target_portfolio"),
        "funding_pct": snapshot.get("funding_pct"),
        "score": (snapshot.get("score") or {}).get("score"),
        "success_pct": (snapshot.get("monte_carlo") or {}).get("success_pct"),
        "earliest_age": snapshot.get("earliest_safe_age"),
        "retirement_age": snapshot.get("retirement_age"),
        "annual_spend": snapshot.get("annual_spend"),
    }
    _HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with _HISTORY.open("a") as fh:
        fh.write(json.dumps(_json_safe(row)) + "\n")


def history(limit: int = 60) -> list[dict]:
    if not _HISTORY.exists():
        return []
    rows = []
    with _HISTORY.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]
