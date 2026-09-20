"""
leaders_store.py — the sector-leader scan snapshot on disk
==========================================================
One JSON file, `data/output/sector_leaders.json`, written by whoever ran the
scan last (CLI or webapp job) and read by the /leaders page.

The webapp cannot run this scan inside a request. It downloads two years of
daily bars for ~570 constituents plus twenty benchmarks and proxy ETFs, then
five-minute bars for the finalists — three to five minutes, far past any sane
request timeout. So the page renders the last snapshot and a button starts a
background job that replaces it, the same shape /stockdaytrade and /scanner
already use.

Serialisation lives here rather than in the scanner because the CLI and the
webapp job both need the identical shape. Two things have to happen before
this reaches json.dumps:

  * the metrics dicts carry live pandas Series (`swing_highs`, `swing_lows`)
    so the level builder can reach them — dropped, not serialised;
  * yfinance hands back numpy scalars, which json.dumps refuses.

The confirmation layer (news, fundamentals, earnings dates) is stored beside
the scan rather than inside it. It is a separate, slower pass over a handful
of finalists, it goes stale on a different clock — headlines age in hours,
20-day relative strength does not — and the page has to be able to say which
of the two it is showing you.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
SNAPSHOT = OUTPUT_DIR / "sector_leaders.json"

# Live pandas objects that exist for the scorers' benefit and must never be
# serialised — json.dumps cannot encode them and the page never reads them.
_DROP_KEYS = ("swing_highs", "swing_lows")


def native(o):
    """A JSON-safe copy: numpy scalars cast, pandas objects dropped."""
    if isinstance(o, dict):
        return {k: native(v) for k, v in o.items()
                if k not in _DROP_KEYS and not isinstance(v, (pd.Series, pd.DataFrame))}
    if isinstance(o, (list, tuple)):
        return [native(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        f = float(o)
        return None if f != f else f          # NaN is not valid JSON
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and o != o:
        return None
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def save(result: dict, confirmations: dict | None = None) -> Path:
    """Write the snapshot, carrying the human-authored half across.

    A scan replaces prices, scores and setups — that is the point. It must NOT
    replace the two things a scan did not produce:

      confirmations  news, fundamentals and earnings dates, which are fetched
                     on their own slower schedule and are usually still valid
      verdicts       someone's reading of those headlines, which is a
                     judgement, not a measurement, and is expensive to redo

    The first cut of this dropped both. The verdicts went first and it showed
    immediately: every confidence score fell back to news "unavailable" after
    a re-scan, so the top of the bullish table silently reordered and the
    page looked like the engine had changed its mind. Each verdict keeps its
    own `set_at`, so one recorded against older headlines is visible as
    stale rather than being thrown away here.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = native(result)
    prior = load() or {}

    merged = dict(prior.get("confirmations") or {})
    merged.update(native(confirmations or {}))
    payload["confirmations"] = merged
    payload["verdicts"] = dict(prior.get("verdicts") or {})
    if prior.get("confirmations_generated"):
        payload["confirmations_generated"] = prior["confirmations_generated"]

    payload["generated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    SNAPSHOT.write_text(json.dumps(payload, indent=1, default=str))
    return SNAPSHOT


def save_confirmations(confirmations: dict) -> Path | None:
    """Update only the confirmation half, leaving the scan untouched."""
    snap = load()
    if not snap:
        return None
    merged = dict(snap.get("confirmations") or {})
    merged.update(native(confirmations))
    snap["confirmations"] = merged
    snap["confirmations_generated"] = datetime.now().astimezone().isoformat(
        timespec="seconds")
    SNAPSHOT.write_text(json.dumps(snap, indent=1, default=str))
    return SNAPSHOT


# Valid news readings. "unavailable" is the default and is deliberately NOT
# the same as "neutral": nobody has looked yet, which is worth less than
# having looked and found nothing.
VERDICTS = ("confirms", "neutral", "mixed", "contradicts", "unavailable")


def save_verdict(ticker: str, direction: str, verdict: str,
                 note: str = "") -> dict | None:
    """Record a human reading of one name's headlines.

    Kept as an explicit, stored judgement rather than derived from a keyword
    lexicon. This project already has a lexicon scorer and its own docstring
    calls it "not real NLP" — running a bag of positive words over "halts
    trial after monitoring board recommendation" produces a number with no
    relationship to what the sentence says. So the page shows the headlines
    and someone decides; the decision is stored with a timestamp so a stale
    reading is visible as one.
    """
    if verdict not in VERDICTS:
        return None
    snap = load()
    if not snap:
        return None
    key = f"{str(ticker).upper()}:{direction}"
    verdicts = dict(snap.get("verdicts") or {})
    verdicts[key] = {"verdict": verdict, "note": note[:400],
                     "set_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    snap["verdicts"] = verdicts
    SNAPSHOT.write_text(json.dumps(snap, indent=1, default=str))
    return verdicts[key]


def verdict_for(snap: dict | None, ticker: str, direction: str) -> dict:
    v = ((snap or {}).get("verdicts") or {}).get(f"{str(ticker).upper()}:{direction}")
    return v or {"verdict": "unavailable", "note": "", "set_at": None}


def load() -> dict | None:
    if not SNAPSHOT.exists():
        return None
    try:
        return json.loads(SNAPSHOT.read_text())
    except (ValueError, OSError):
        return None


def age_hours(snap: dict | None, key: str = "generated") -> float | None:
    """Hours since the snapshot was written, or None if it cannot be read.

    Timezone-aware on both sides: the CLI runs in the shell's timezone and
    the webapp is launched with TZ=America/New_York, and a naive comparison
    made a scan that had just finished read as an hour old.
    """
    stamp = (snap or {}).get(key)
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    now = datetime.now().astimezone()
    if then.tzinfo is None:
        then = then.astimezone()
    return (now - then).total_seconds() / 3600.0
