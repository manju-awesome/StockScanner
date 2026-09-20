"""
signal_state.py
================
Fire-once-until-resolved dedup store for UT Bot signals, adapted from
StockAnalysis_Version1's core/alerts.py reconcile pattern: a standing
condition should notify exactly once, not on every poll cycle that happens
to observe it.

Since signal_engine.buy/sell are already crossing-only events (true on
exactly the bar where the cross happens), the dedup key here only needs to
protect against re-processing the same bar twice (e.g. a daemon restart that
re-reads the last closed bar) — it does not need alerts.py's full
active/resolved reconciliation, just a seen-set keyed on the exact event.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data" / "spy"
SIGNAL_STATE_PATH = DATA_DIR / "signal_state.json"
MAX_SEEN = 500


def _load() -> dict:
    if not SIGNAL_STATE_PATH.exists():
        return {"current": {}, "seen_keys": []}
    try:
        state = json.loads(SIGNAL_STATE_PATH.read_text())
    except (OSError, ValueError):
        return {"current": {}, "seen_keys": []}
    state.setdefault("current", {})
    state.setdefault("seen_keys", [])
    return state


def _save(state: dict) -> None:
    SIGNAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIGNAL_STATE_PATH.write_text(json.dumps(state, indent=2))


def _dedup_key(signal: dict) -> str:
    return f"{signal['ticker']}:{signal['side']}:{signal['signal_time']}"


def get_state() -> dict:
    return _load()


def current_side(ticker: str = "SPY") -> str | None:
    state = _load()
    entry = state["current"].get(ticker)
    return entry["side"] if entry else None


def record_signal(signal: dict | None) -> dict | None:
    """Call with the output of signal_engine.latest_signal() every time a
    new bar closes. Returns the signal dict if this is a new event worth
    acting on (i.e. not already seen), else None."""
    if signal is None:
        return None

    state = _load()
    key = _dedup_key(signal)
    if key in state["seen_keys"]:
        return None

    state["seen_keys"].append(key)
    state["seen_keys"] = state["seen_keys"][-MAX_SEEN:]
    state["current"][signal["ticker"]] = {
        "side": signal["side"],
        "since": signal["signal_time"],
        "last_signal": signal,
    }
    _save(state)
    return signal
