"""
research_snapshot.py
=====================
A durable, union-merged copy of every research field this project has ever
computed per ticker — the store the Screener reads so a bad write to
research_index.json can't take data away.

Why this exists
---------------
research_index.json is rewritten in place by whatever process happens to run
a scan or a research refresh. On 2026-08-05 a server process that had been
running since Jul 14 executed its own scheduled scan with month-old code,
whose index entry had no "raw" key at all, and overwrote 494 of 558 entries
wholesale — every technical and fundamental field the Screener filters on
vanished in one write, leaving only the curated subset that old schema knew
about.

The merge in research._update_research_index already prevents this for code
running today. It cannot prevent it for a process started weeks ago that is
still holding the old function in memory, and there is no way to reach back
into that process. So the durable copy is kept in a *separate file* that:

  * only ever grows — apply() unions new values in field by field and never
    deletes a key, so a writer that "forgets" a field cannot erase it;
  * records when each ticker was last really updated, so a value recovered
    from an old scan is labelled stale rather than passed off as current;
  * is never written by the scan pipeline's entry-building code, so a
    regression there can't reach it.

Reading is deliberately asymmetric (see merged()): research_index.json wins
for any field it actually carries, because it is the live view; the snapshot
only supplies what the index is missing. That way fresh data always beats
remembered data, and remembered data only fills holes.

What this is NOT: a general-purpose history. It keeps the latest known value
per field, not a time series — the scan CSVs under data/output/ are the
historical record, and backfill_from_scans() reads them to rebuild this file
when it's missing or has gaps.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SNAPSHOT_FILENAME = "research_snapshot.json"

# Bookkeeping keys stored alongside the per-ticker payload. Prefixed so they
# can never collide with a scan column name.
_SEEN_AT = "_snapshot_seen_at"      # when this ticker last received values
_SOURCE = "_snapshot_source"        # what wrote them (scan file / pipeline)


def snapshot_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / SNAPSHOT_FILENAME


def load(output_dir: str | Path) -> dict:
    """Ticker -> {field: value, ...}. Missing or corrupt file reads as empty
    rather than raising: the snapshot is a safety net, and a safety net that
    takes the app down when it tears is worse than no net."""
    f = snapshot_path(output_dir)
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text())
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save(output_dir: str | Path, snap: dict) -> None:
    """Write atomically — a torn write here would defeat the whole point of
    keeping a second copy."""
    path = snapshot_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, indent=1, default=str))
    tmp.replace(path)


def _is_present(v: Any) -> bool:
    """Does this value carry information worth remembering?

    None and "" mean "this writer had nothing to say", and must not
    overwrite a good remembered value — that is the exact failure being
    defended against. NaN is included because it survives a CSV round-trip
    and compares false against everything, so it reads as data while
    behaving like a hole.
    """
    if v is None:
        return False
    if isinstance(v, str) and v.strip() in ("", "N/A", "nan", "None", "-"):
        return False
    if isinstance(v, float) and v != v:
        return False
    return True


def apply(snap: dict, ticker: str, values: dict,
          source: str = "", seen_at: str | None = None) -> bool:
    """Union `values` into `snap[ticker]`. Returns True if anything changed.

    Field-level, present-values-only: this is the "if a scan changes one
    value, only that value is overwritten; everything else stays" rule. A
    lighter row simply contributes fewer fields instead of truncating the
    ticker.
    """
    if not ticker or not values:
        return False
    prev = snap.get(ticker) or {}
    fresh = {k: v for k, v in values.items()
             if not str(k).startswith("_") and _is_present(v)}
    if not fresh:
        return False
    merged_entry = {**prev, **fresh}
    changed = merged_entry != prev
    if not changed:
        return False
    merged_entry[_SEEN_AT] = seen_at or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S")
    if source:
        merged_entry[_SOURCE] = source
    snap[ticker] = merged_entry
    return True


def record(output_dir: str | Path, rows: Iterable[dict],
           source: str = "pipeline") -> int:
    """Persist raw scan/research rows. Called after the index is updated, so
    the snapshot sees exactly what the pipeline produced.

    Never raises: this runs at the tail of the scan pipeline, and losing a
    snapshot update must never fail a scan that otherwise succeeded.
    """
    try:
        snap = load(output_dir)
        n = 0
        for row in rows:
            ticker = row.get("Ticker") or row.get("ticker")
            if ticker and apply(snap, str(ticker), row, source=source):
                n += 1
        if n:
            save(output_dir, snap)
        return n
    except Exception as e:                                  # pragma: no cover
        print(f"[Snapshot] update skipped ({e})")
        return 0


def has_quote(entry: dict) -> bool:
    """Did any scan ever obtain a real price for this ticker?

    The distinction matters because a failed fetch is not neutral in this
    pipeline: get_metrics() swallows per-field errors and returns a husk, so
    the row arrives with Sector "N/A" and no price, trips the entry gate
    ("MarketCap<1B, Price<$5") and comes out stamped Category=Avoid,
    Grade=X, AVOID, 1 star. That reads as "analysed and rejected" when the
    truth is "never fetched" — a verdict manufactured out of absent data.

    Callers use this to render and screen those tickers as unknown rather
    than as bad. Symbols that stop resolving at the data source (delisted,
    acquired, renamed) fail this permanently, which is why the state needs a
    name instead of being silently scored.
    """
    if not entry:
        return False
    raw = entry.get("raw") or {}
    return _is_present(entry.get("price")) or _is_present(raw.get("Current Price"))


def merged(index: dict, snap: dict) -> list[dict]:
    """Research-index entries with snapshot values filling the gaps.

    Returns entries in research_index.json's shape (curated keys plus
    "raw"), so callers — screener.build_universe, the Research page — need
    no special handling. Precedence is per field, not per entry: a live
    index value always wins, and the snapshot only supplies keys the index
    lacks. Tickers present only in the snapshot are included, so a ticker
    dropped from the index entirely still screens.
    """
    out: list[dict] = []
    for ticker in sorted(set(index) | set(snap)):
        entry = dict(index.get(ticker) or {})
        remembered = dict(snap.get(ticker) or {})
        seen_at = remembered.pop(_SEEN_AT, None)
        remembered.pop(_SOURCE, None)
        if remembered:
            raw = dict(entry.get("raw") or {})
            # Snapshot underneath: any field the live index carries wins.
            entry["raw"] = {**remembered, **raw}
            entry.setdefault("ticker", ticker)
            # Only claim recovery when the index really had nothing, so the
            # UI can flag restored rows without mislabelling healthy ones.
            if not (index.get(ticker) or {}).get("raw"):
                entry["recovered_from_snapshot"] = True
                entry["snapshot_seen_at"] = seen_at
        if entry:
            out.append(entry)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# BACKFILL — rebuild the snapshot from the scan CSVs already on disk
# ─────────────────────────────────────────────────────────────────────────────

_SPLIT_SUFFIXES = ("_daytrade", "_swing", "_longterm")

# Columns whose values are strings that happen to look numeric, or dates —
# coercing these would corrupt them (a ticker "600519", a date "20260805").
_KEEP_AS_TEXT = frozenset({
    "Ticker", "LongName", "Sector", "Industry", "Category", "Grade",
    "EarningsDate", "52W_High_Date", "Scan_Time", "Buy_Zone_Label",
    "Trend_Strength", "Call_Strength", "ORB_Status", "SizeFlag",
    # Long-term engine text columns. Reversal_Candle carries the literal
    # "none" for "measured, no pattern today", which has to survive as text:
    # the engine distinguishes it from a missing value, and that distinction
    # is what stops an unscanned row reading as a confirmed setup.
    "Reversal_Candle", "Fundamentals_As_Of",
})


def _coerce_csv_value(key: str, value: str):
    """CSV gives every column back as a string; the scoring functions that
    read these rows (company_scores, buy_zone, conviction) do numeric and
    truth tests on them.

    Two ways that bites if left alone: "45.0" >= 40 raises TypeError, and —
    quieter and worse — the string "False" is truthy, so a bool column round
    tripped through CSV would invert every check reading it directly.
    """
    if key in _KEEP_AS_TEXT:
        return value
    s = value.strip()
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        f = float(s)
    except ValueError:
        return value
    if f != f:                                   # NaN
        return value
    return int(f) if f.is_integer() and abs(f) < 1e15 and "." not in s else f


def _scan_files(output_dir: Path) -> list[Path]:
    """Full scans, newest first. The per-strategy splits are subsets of the
    full scan written alongside it, so including them would add no columns
    and just cost a parse."""
    files = [f for f in output_dir.glob("stock_scan_*.csv")
             if not any(f.stem.endswith(s) for s in _SPLIT_SUFFIXES)]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def backfill_from_scans(output_dir: str | Path, max_files: int = 60) -> dict:
    """Seed/repair the snapshot from stock_scan_*.csv history.

    Walks newest-first and takes the first value it finds for each
    ticker/field, so a ticker gets its most recent known data and older
    scans only fill what newer ones lack. Returns a summary for the caller
    to report.
    """
    out_dir = Path(output_dir)
    snap = load(out_dir)
    files = _scan_files(out_dir)[:max_files]
    tickers_touched: set[str] = set()
    fields_added = 0

    import csv
    for path in files:
        try:
            with path.open(newline="") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, ValueError):
            continue
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S")
        for row in rows:
            ticker = (row.get("Ticker") or "").strip()
            if not ticker:
                continue
            prev = dict(snap.get(ticker) or {})
            # Newest-first means anything already known is fresher than this
            # file, so only contribute the keys still missing.
            new_values = {k: _coerce_csv_value(k, v) for k, v in row.items()
                          if _is_present(v) and not _is_present(prev.get(k))}
            if not new_values:
                continue
            seen = prev.get(_SEEN_AT)
            if apply(snap, ticker, new_values, source=path.name,
                     seen_at=seen or row.get("Scan_Time") or stamp):
                tickers_touched.add(ticker)
                fields_added += len(new_values)
    if tickers_touched:
        save(out_dir, snap)
    return {"files_read": len(files), "tickers": len(tickers_touched),
            "fields": fields_added, "total_tickers": len(snap)}
