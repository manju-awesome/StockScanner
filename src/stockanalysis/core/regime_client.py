"""
regime_client.py
=================
Thin client for the shared market-regime scorer that lives with the
`market-regime` skill at ~/.claude/skills/market-regime/. Both this project
and the SPY_DayTrader project call the same script, so the scoring rules can't
drift between the two dashboards — there is one implementation, not a copy
per repo.

The trade-off is a dependency on a path outside this repo. It degrades to a
clear error rather than a traceback, and REGIME_SCRIPT overrides the location.

The last successful read is cached to data/regime_latest.json so the
dashboard can render it without re-running a ~15s collection on every
page load.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
REGIME_CACHE_PATH = DATA_DIR / "regime_latest.json"

DEFAULT_SCRIPT = Path.home() / ".claude" / "skills" / "market-regime" / "regime_score.py"


def script_path() -> Path:
    return Path(os.environ.get("REGIME_SCRIPT", str(DEFAULT_SCRIPT)))


def load_cached() -> dict | None:
    if not REGIME_CACHE_PATH.exists():
        return None
    try:
        return json.loads(REGIME_CACHE_PATH.read_text())
    except (OSError, ValueError):
        return None


def run_regime(timeout: int = 300) -> dict:
    """Collect + score. Returns the report dict, or {"error": ...}. Never
    raises — the caller is a web request handler."""
    script = script_path()
    if not script.is_file():
        return {"error": f"regime scorer not found at {script}. Set REGIME_SCRIPT to override."}

    try:
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"regime collection timed out after {timeout}s"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    if proc.returncode != 0:
        return {"error": f"scorer exited {proc.returncode}: {(proc.stderr or '').strip()[-300:]}"}

    try:
        report = json.loads(proc.stdout)
    except ValueError:
        return {"error": f"scorer returned unparseable output: {proc.stdout[:200]}"}

    report["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        REGIME_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGIME_CACHE_PATH.write_text(json.dumps(report, indent=2))
    except OSError:
        pass  # a cache write failure shouldn't lose the result
    return report
