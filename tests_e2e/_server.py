"""
_server.py — the workstation, pointed at a throwaway data directory
====================================================================
Run by the `workstation` fixture as a subprocess. It does what app.main()
does, minus the two things a test must not inherit:

  * the scheduler and the SPY daemon. Both are started by main(); both write
    to data/ on a timer. A test server that runs them would keep mutating the
    library underneath the assertions (and a forgotten one would keep doing it
    to the real library — see the "stale server clobbers the index" problem).
    This file never calls main(), so neither thread exists.

  * the real data directory. Every path the app reads is redirected here,
    before the modules that copy those constants at import time get imported.
    That ordering is the whole trick: views.py defines OUTPUT_DIR/DATA_DIR,
    and pages.py/auth.py bind them by value with `from .views import ...`, so
    views must be patched first and api.py — which keeps its OWN copy — has to
    be patched by name afterwards.

Two seams are stubbed so a page render cannot reach the network: the 10-year
Treasury quote (yfinance) and the market-regime lookup. Both already have
"unavailable" fallbacks in production; pinning them makes the verdict on a
fixture row reproducible instead of dependent on today's tape.

Usage (the fixture does this for you):
    python tests_e2e/_server.py --port 8123 --data-dir /tmp/xyz \
        --user e2e-tester --password ...
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# The sizing inputs every /longterm render is measured against. Fixed here so
# a share count in a test assertion means one thing forever.
RISK_SETTINGS = {"capital": 100_000.0, "risk_pct": 1.0,
                 "max_allocation_pct": 10.0, "atr_multiplier": 2.0}

REGIME = "SELECTIVE"
RISK_FREE = 0.042


def _redirect_paths(data_dir: Path, output_dir: Path) -> None:
    """Point every module-level path at the tmp dir. Order matters — see the
    module docstring."""
    from stockanalysis.webapp import views
    views.DATA_DIR = data_dir
    views.OUTPUT_DIR = output_dir

    from stockanalysis.webapp import api, auth
    from stockanalysis.webapp import app as app_mod
    from stockanalysis.reporting import research

    api.OUTPUT_DIR = output_dir                       # api.py keeps its own
    api.PROJECT_ROOT = data_dir.parent
    api.RISK_SETTINGS_PATH = data_dir / "risk_settings.json"
    auth.USERS_PATH = data_dir / "users.json"
    app_mod.OUTPUT_DIR = output_dir                   # static file root
    research.DEFAULT_OUTPUT_DIR = output_dir
    research.PROJECT_DATA_DIR = data_dir              # watchlists.json


def _stub_network() -> None:
    """No page render may make an outbound call."""
    from stockanalysis.webapp import api

    api.longterm_risk_free = lambda: (
        RISK_FREE, f"10Y Treasury {RISK_FREE * 100:.2f}% (test fixture)")
    api.longterm_regime = lambda override=None: (
        (str(override).upper() if override else REGIME), "test fixture")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    output_dir = data_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    if data_dir == (PROJECT_ROOT / "data").resolve():
        raise SystemExit("refusing to run the test server on the real data dir")

    _redirect_paths(data_dir, output_dir)
    _stub_network()

    (data_dir / "risk_settings.json").write_text(json.dumps(RISK_SETTINGS))
    (data_dir / "watchlists.json").write_text(json.dumps({"e2e": ["ELIT"]}))

    from stockanalysis.webapp import app as app_mod, auth
    auth.set_user_password(args.user, args.password)
    app_mod.AUTH_ENABLED = True

    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 app_mod.WorkstationHandler)
    # The fixture waits for this line before it lets a test run.
    print(f"READY http://127.0.0.1:{args.port} data={data_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
