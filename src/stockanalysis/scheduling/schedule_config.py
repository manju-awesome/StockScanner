"""
schedule_config.py
==================
User-editable schedule for the scheduler's recurring jobs, backed by
data/schedule_config.json. scheduler.py registers jobs from this config
instead of hardcoded times, and the Automation page edits it (enabled
flag + frequency per job) — a save re-registers the live schedule, no
restart needed.

Two trigger types per job:
    daily     — fire at fixed ET times, e.g. {"type": "daily", "times": ["06:30", "10:00"]}
    interval  — fire every N minutes,   e.g. {"type": "interval", "minutes": 10}

Jobs keep their own internal guards (weekday, market-hours, Friday-only,
VIX) regardless of trigger type, so moving e.g. the watchlist monitor to a
different cadence can't make it fire outside market hours.

JOB_DEFS is the registry of what CAN be scheduled (label/description/
default trigger, in display order); the JSON file stores the user's
overrides. Unknown keys in the file are ignored, missing keys fall back
to defaults — so adding a job here later needs no file migration.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "data" / "schedule_config.json"

# key -> {label, description, default spec}. Order = Automation page order.
# Defaults reproduce the schedule that used to be hardcoded in scheduler.py.
JOB_DEFS: dict[str, dict] = {
    "earnings_alerts": {
        "label": "Earnings alerts",
        "description": "Check watchlist tickers for imminent earnings (releases land pre-open/"
                       "post-close). Same scan as the Alerts page's \"Check Earnings Now\".",
        "default": {"enabled": True, "type": "daily", "times": ["06:30"]},
    },
    "premarket_brief": {
        "label": "Pre-market brief",
        "description": "Compose and email the morning macro/movers/earnings brief (weekdays). "
                       "Same as the Alerts page's \"Generate Brief Now\".",
        "default": {"enabled": True, "type": "daily", "times": ["07:00"]},
    },
    "watchlist_alerts": {
        "label": "Watchlist alert monitor",
        "description": "Condition scan over the watchlist + Research Library refresh; market "
                       "hours only (guard inside the job). Same as the Alerts page's "
                       "\"Scan Watchlist Now\".",
        "default": {"enabled": True, "type": "interval", "minutes": 10},
    },
    "news_alerts": {
        "label": "Breaking-news scan",
        "description": "Headline scan over the watchlist for market-moving news categories; "
                       "market hours only. Same as the Alerts page's \"Scan News Now\" "
                       "(previously bundled inside the watchlist monitor).",
        "default": {"enabled": True, "type": "interval", "minutes": 10},
    },
    "swing_premarket": {
        "label": "Swing scan (pre-market)",
        "description": "Multi-day setup scan (BB coil, ATR shrink) before the open.",
        "default": {"enabled": True, "type": "daily", "times": ["08:00"]},
    },
    "calls_premarket": {
        "label": "Calls scan (pre-market)",
        "description": "Call-candidate scan before the open.",
        "default": {"enabled": True, "type": "daily", "times": ["08:05"]},
    },
    "day_session_init": {
        "label": "Day session universe init",
        "description": "At the open: merge live movers into the day-trade universe for the day's scans.",
        "default": {"enabled": True, "type": "daily", "times": ["09:30"]},
    },
    "daytrade_scans": {
        "label": "Day-trade scans",
        "description": "Intraday momentum scans (RVOL/VWAP) on the day-session universe.",
        "default": {"enabled": True, "type": "daily",
                    "times": ["09:30", "10:00", "11:35"]},
    },
    "puts_midday": {
        "label": "Puts scan (midday)",
        "description": "Put-candidate scan on the watchlist.",
        "default": {"enabled": True, "type": "daily", "times": ["11:40"]},
    },
    "vix_adaptive_puts": {
        "label": "VIX-adaptive put scans",
        "description": "Checks VIX; when elevated, adds a 30-min put-scan loop for the afternoon.",
        "default": {"enabled": True, "type": "daily", "times": ["11:30"]},
    },
    "research_premarket": {
        "label": "Research refresh (pre-market)",
        "description": "Refresh every research page in the library against pre-open quotes. "
                       "Same as the Research page's \"Pre-market scan\" button.",
        "default": {"enabled": False, "type": "daily", "times": ["08:15"]},
    },
    "research_postmarket": {
        "label": "Research refresh (post-market)",
        "description": "Refresh every research page in the library against post-close quotes. "
                       "Same as the Research page's \"Post-market scan\" button.",
        "default": {"enabled": False, "type": "daily", "times": ["16:45"]},
    },
    "full_close": {
        "label": "Full scan (after close)",
        "description": "All-category scan of the full S&P universe after the close.",
        "default": {"enabled": True, "type": "daily", "times": ["16:30"]},
    },
    "friday_scan": {
        "label": "Friday weekend watchlist",
        "description": "Extended post-close scan generating the weekend watchlist (Fridays only, guard inside).",
        "default": {"enabled": True, "type": "daily", "times": ["16:45"]},
    },
    "nightly_cleanup": {
        "label": "Nightly output cleanup",
        "description": "Prune generated CSVs/dashboards/research pages older than CLEANUP_DAYS.",
        "default": {"enabled": True, "type": "daily", "times": ["23:30"]},
    },
}


def _parse_time(s: str) -> str:
    """Validate one 'HH:MM' string, returning it zero-padded ('9:5' -> '09:05')."""
    try:
        return datetime.strptime(s.strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ValueError(f"invalid time {s.strip()!r} — use 24h HH:MM, e.g. 09:30")


def normalize_spec(raw: dict, fallback: dict) -> dict:
    """
    Validate + canonicalize one job's spec. `raw` values may come straight
    from a web form (times as one comma-separated string, minutes as a
    string). Raises ValueError with a user-readable message on bad input.
    """
    enabled = raw.get("enabled")
    if isinstance(enabled, str):
        enabled = enabled.lower() in ("on", "true", "1", "yes")
    elif enabled is None:
        enabled = bool(fallback.get("enabled", True))

    jtype = raw.get("type") or fallback.get("type", "daily")
    if jtype not in ("daily", "interval"):
        raise ValueError(f"invalid type {jtype!r} — must be 'daily' or 'interval'")

    if jtype == "daily":
        times = raw.get("times")
        if times is None:
            times = fallback.get("times") or []
        if isinstance(times, str):
            times = [t for t in times.replace(";", ",").split(",") if t.strip()]
        times = sorted({_parse_time(t) for t in times})
        if not times:
            raise ValueError("a daily job needs at least one HH:MM time")
        return {"enabled": enabled, "type": "daily", "times": times}

    minutes = raw.get("minutes", fallback.get("minutes", 10))
    try:
        minutes = int(str(minutes).strip())
    except ValueError:
        raise ValueError(f"invalid minutes {minutes!r} — must be a whole number")
    if not 1 <= minutes <= 1440:
        raise ValueError("minutes must be between 1 and 1440")
    return {"enabled": enabled, "type": "interval", "minutes": minutes}


def load_config() -> dict[str, dict]:
    """Every job's effective spec: file overrides merged over JOB_DEFS
    defaults. Invalid/unknown file entries are dropped silently — the
    scheduler must come up on defaults, not crash on a hand-edited file."""
    try:
        saved = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    cfg = {}
    for key, meta in JOB_DEFS.items():
        spec = dict(meta["default"])
        override = saved.get(key)
        if isinstance(override, dict):
            try:
                spec = normalize_spec(override, meta["default"])
            except ValueError:
                pass
        cfg[key] = spec
    return cfg


def save_job(key: str, raw_spec: dict) -> dict:
    """Validate one job's new spec and persist it (other jobs untouched).
    Returns the normalized spec. Raises ValueError on bad input."""
    if key not in JOB_DEFS:
        raise ValueError(f"unknown job {key!r}")
    spec = normalize_spec(raw_spec, JOB_DEFS[key]["default"])
    cfg = load_config()
    cfg[key] = spec
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=1))
    return spec


def describe_spec(spec: dict) -> str:
    """One-line human description: 'daily at 06:30, 10:00 ET' / 'every 10 min'."""
    if spec["type"] == "interval":
        return f"every {spec['minutes']} min"
    return "daily at " + ", ".join(spec["times"]) + " ET"
