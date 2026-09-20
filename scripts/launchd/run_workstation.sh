#!/bin/bash
# run_workstation.sh — start the workstation the way a launchd agent should
# =========================================================================
# Wraps `python app.py` with the three things launchd does not do for you:
#
#   1. One process, and only one. app.py starts the scan scheduler and the SPY
#      signal daemon as background threads, so a second copy means two
#      schedulers writing the same files on the same timer — the failure that
#      looks like the research index resetting itself for no reason. Both
#      guards below exit 0, not 1, so the KeepAlive in the plist treats "one
#      is already running" as success and does not spin.
#
#   2. Secure cookies. This script exists to be reached through a tunnel
#      (`tailscale serve`), which terminates HTTPS, so WORKSTATION_BEHIND_TLS
#      defaults to 1 here — see auth.BEHIND_TLS. Note what that costs:
#      http://localhost:8899 can no longer sign in while it is set. Run with
#      WORKSTATION_BEHIND_TLS=0 if you want the plain local browser back.
#
#   3. Staying awake. A tunnel to a sleeping Mac answers nothing, so the
#      server is started under `caffeinate -s` when it is available. Set
#      WORKSTATION_CAFFEINATE=0 to let the machine sleep normally.
#
# Secrets are NOT passed in here: the scheduler reads .env off disk itself
# (scheduling/scheduler.py), and a launchd plist is world-readable, which is
# the wrong place for an API key.
#
#   ./run_workstation.sh            # start (what launchd runs)
#   ./run_workstation.sh --check    # run every guard, print the plan, start nothing
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP="$PROJECT_ROOT/src/stockanalysis/webapp/app.py"

PORT="${PORT:-8899}"
export WORKSTATION_BEHIND_TLS="${WORKSTATION_BEHIND_TLS:-1}"
CAFFEINATE="${WORKSTATION_CAFFEINATE:-1}"

# The interpreter, resolvable without a login shell's PATH. Override with
# WORKSTATION_PYTHON when the version moves.
PYTHON="${WORKSTATION_PYTHON:-/Library/Frameworks/Python.framework/Versions/3.14/bin/python3}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || true)"

check_only=0
[[ "${1:-}" == "--check" ]] && check_only=1

say() { printf '[workstation] %s\n' "$*"; }

# ── guards ───────────────────────────────────────────────────────────────────
fail=0

if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
    say "no usable python3 (tried WORKSTATION_PYTHON, then PATH)"; fail=1
fi
if [[ ! -f "$APP" ]]; then
    say "app.py not found at $APP"; fail=1
fi

# Someone already on the port: almost always the copy you started by hand.
holder="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
if [[ -n "$holder" && "$holder" != "$$" ]]; then
    say "port $PORT is already served by PID $holder — leaving it alone."
    say "  $(ps -o command= -p "$holder" 2>/dev/null | cut -c1-120)"
    exit 0
fi

# A copy of THIS project's app.py running on some other port. Still a second
# scheduler, so still a refusal — and worth naming the PID, because the
# symptom otherwise shows up days later as data that rewrote itself.
#
# Matched on the tail of the path rather than on $APP, because how a process
# was started is not how it was written down: `python3 July01-2027/.../app.py`
# from the parent directory is the same server as the absolute-path form, and
# an absolute-path pattern silently misses it. (A `python app.py` run from
# inside webapp/ still slips through — the port guard above catches that one.)
project_tail="$(basename "$(dirname "$PROJECT_ROOT")")/$(basename "$PROJECT_ROOT")"
#
# Filtered down to processes that are actually a Python interpreter: the
# launcher that started the server (an `env TZ=... python app.py` wrapper)
# carries the same string on its command line without being a second server,
# and naming it would send you to kill the wrong PID.
others=""
for pid in $(pgrep -f "$project_tail/src/stockanalysis/webapp/app.py" \
             | grep -v "^$$\$" || true); do
    argv0="$(basename "$(ps -o command= -p "$pid" 2>/dev/null | awk '{print $1}')")"
    case "$argv0" in
        Python|python|python2|python3|python3.*) others="$others $pid" ;;
    esac
done
others="$(echo "$others" | xargs || true)"
if [[ -n "$others" ]]; then
    say "another app.py for this project is running (PID: $(echo "$others" | tr '\n' ' '))."
    say "each one runs its own scheduler; refusing to start a second."
    exit 0
fi

(( fail )) && exit 1

# ── go ───────────────────────────────────────────────────────────────────────
cmd=("$PYTHON" "$APP" "--port" "$PORT")
if [[ "$CAFFEINATE" != "0" ]] && command -v caffeinate >/dev/null; then
    # -s: keep the system awake while this process lives. Not -d — the display
    # has no reason to stay on for a tunnel.
    cmd=(caffeinate -s "${cmd[@]}")
fi

if (( check_only )); then
    say "python                 : $PYTHON"
    say "app                    : $APP"
    say "port                   : $PORT"
    say "WORKSTATION_BEHIND_TLS : $WORKSTATION_BEHIND_TLS (Secure cookies; localhost cannot sign in)"
    say "caffeinate             : $([[ ${cmd[0]} == caffeinate ]] && echo yes || echo no)"
    say "would exec             : ${cmd[*]}"
    say "guards passed; nothing started (--check)"
    exit 0
fi

say "starting on port $PORT (BEHIND_TLS=$WORKSTATION_BEHIND_TLS)"
exec "${cmd[@]}"
