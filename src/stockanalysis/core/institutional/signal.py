"""
13F put/call skew as a CONTEXT reading — never as a gate.

The rule this module exists to enforce
--------------------------------------
This signal is reported beside a verdict and never inside one. Same rule
the insider signal follows for LQuality, and for a stronger reason: three
independent properties of 13F make it unfit to move a buy or avoid.

  1. It is months old. A quarter-end photograph, filed up to 45 days later,
     published in bulk weeks after that. On the sample snapshot it is 148
     days behind. A daily decision must not pivot on a number from last
     season.
  2. It is structurally one-sided. Only LONG option positions are reported.
     A large put line means someone bought downside; nothing says who sold
     it. Treating "put-heavy" as bearish confirmation reads one half of a
     trade as if it were the whole market.
  3. Its population is not the options market. 13F filers are managers over
     $100M in US discretionary AUM. Market makers acting as such are out,
     and they are most of the volume — which is exactly why the top of
     every list here is Susquehanna, Jane Street and Citadel: the firms big
     enough to file AND holding inventory on the reporting date.

Any one of those would argue for context-only. Together they make a gate
built on this actively misleading, because the number looks precise: "put/
call 2.55" invites being trusted the way a live options ratio would be.

What it IS good for: noticing that a name you already like carries a large
reported downside book, and going to look at why. That is a prompt to
research, not an input to a score, and `label()` is worded to say so.
"""

from __future__ import annotations

import datetime as _dt

from . import store as ST

# Bands around 1.0. Wide on purpose: filer composition alone moves this
# ratio, and a 1.05 given a directional word is noise wearing a label.
PUT_HEAVY = 1.30
CALL_HEAVY = 0.77

# Below this many holders on the heavier side the ratio is one or two
# managers' positioning, not a pattern. A 3.0 ratio built from two filers
# says something about those two filers.
MIN_HOLDERS = 8

_CACHE: dict | None = None


def _snapshot() -> dict | None:
    global _CACHE
    if _CACHE is None:
        _CACHE = ST.load() or {}
    return _CACHE or None


def reset_cache() -> None:
    """For tests, and for a caller that has just rebuilt the snapshot."""
    global _CACHE
    _CACHE = None


def label(ratio: float | None, holders: int) -> str:
    """A word for the skew, phrased as reported positioning rather than as
    a market view. "Put-heavy" is a fact about filings; "bearish" would be
    an inference this data cannot support."""
    if ratio is None:
        return "not reported"
    if holders < MIN_HOLDERS:
        return "too few holders to read"
    if ratio >= PUT_HEAVY:
        return "put-heavy book"
    if ratio <= CALL_HEAVY:
        return "call-heavy book"
    return "balanced book"


def skew(ticker: str, snapshot: dict | None = None) -> dict | None:
    """The reported option book for one ticker, or None if not covered.

    None rather than a zeroed row: "no filer reported options on this name"
    and "this name was not in the last snapshot" are different facts, and a
    zero would render as the first while often meaning the second.
    """
    snap = snapshot if snapshot is not None else _snapshot()
    if not snap:
        return None
    ticker = str(ticker or "").upper().strip()
    row = next((r for r in snap.get("tickers") or []
                if r.get("ticker") == ticker), None)
    if row is None or row.get("error"):
        return None

    calls, puts = row.get("calls") or {}, row.get("puts") or {}
    ratio = row.get("put_call_ratio_contracts")
    holders = max(calls.get("holders") or 0, puts.get("holders") or 0)
    if not holders:
        return None

    age = None
    try:
        age = (_dt.date.today()
               - _dt.date.fromisoformat(snap["period"])).days
    except (KeyError, ValueError, TypeError):
        pass

    return {
        "ticker": ticker,
        "put_call": ratio,
        "label": label(ratio, holders),
        "call_holders": calls.get("holders") or 0,
        "put_holders": puts.get("holders") or 0,
        "call_contracts": calls.get("contracts") or 0.0,
        "put_contracts": puts.get("contracts") or 0.0,
        "period": snap.get("period"),
        "age_days": age,
        # Carried on every reading so the number cannot travel without the
        # sentence that bounds it.
        "basis": snap.get("ratio_basis") or "",
        "context_only": True,
    }


def summary(reading: dict | None) -> str:
    """One line for a verdict's context list."""
    if not reading:
        return ""
    ratio = reading.get("put_call")
    ratio_txt = f"{ratio:,.2f}" if ratio is not None else "n/a"
    age = reading.get("age_days")
    stamp = (f"{reading.get('period')}, {age}d old" if age is not None
             else str(reading.get("period") or ""))
    return (f"13F {reading['label']} — put/call {ratio_txt} across "
            f"{reading['put_holders']:,} put and {reading['call_holders']:,} "
            f"call holders ({stamp}, long positions only)")
