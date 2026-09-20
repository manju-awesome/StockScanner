# Running the workstation as a background service

For reaching the tool from another device: a launchd agent keeps `app.py`
running, and `tailscale serve` puts an HTTPS URL on your tailnet in front of
it. The server itself still binds `127.0.0.1` — nothing in here makes it
listen on a public interface, and nothing should.

```
run_workstation.sh                     what actually starts the server
com.stockanalysis.workstation.plist    the launchd agent that runs it
```

## Install

```bash
mkdir -p ~/Library/Logs/StockAnalysis
cp scripts/launchd/com.stockanalysis.workstation.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.stockanalysis.workstation.plist
```

Check the plan before you install anything — every guard runs, nothing starts:

```bash
./scripts/launchd/run_workstation.sh --check
```

## Day to day

| what | command |
|---|---|
| status | `launchctl print gui/$(id -u)/com.stockanalysis.workstation \| head -20` |
| restart (after a code change) | `launchctl kickstart -k gui/$(id -u)/com.stockanalysis.workstation` |
| stop and unload | `launchctl bootout gui/$(id -u)/com.stockanalysis.workstation` |
| logs | `tail -f ~/Library/Logs/StockAnalysis/workstation.log` |
| the tunnel's URL | `tailscale serve status` |

Edited the plist? Copy it to `~/Library/LaunchAgents/` again and `bootout` then
`bootstrap` — launchd reads its copy, not this repo.

## The three things this setup is actually about

**One process.** `app.py` starts the scan scheduler and the SPY signal daemon
as threads, so a second copy is a second scheduler writing the same files on
the same timer — which surfaces days later as a research index that reset
itself. The wrapper refuses to start if the port is taken or if another
`app.py` for this project is alive, and it names the PID so you kill the right
one. Both refusals exit 0, which is why the plist's `KeepAlive` is
`SuccessfulExit: false`: a refusal must not become a restart loop.

**Secure cookies, and what they cost.** The wrapper sets
`WORKSTATION_BEHIND_TLS=1`, which marks the session cookie `Secure` (see
`auth.BEHIND_TLS`). While that is set, **`http://localhost:8899` can no longer
sign in** — a browser will not store a Secure cookie sent over plain HTTP, so
`/login` just redirects back to `/login`. Reach the tool through the tailnet
HTTPS URL, or start it with `WORKSTATION_BEHIND_TLS=0` for local-only work.

**Staying awake.** A tunnel to a sleeping Mac answers nothing, so the server
runs under `caffeinate -s`. Set `WORKSTATION_CAFFEINATE=0` to let the machine
sleep normally. Caveat worth knowing before you rely on it: a MacBook with the
lid closed and no power/external display sleeps anyway, `caffeinate` or not.

## Tunnel, once the agent is up

```bash
tailscale serve --bg 8899
```

`serve` is tailnet-only. **`tailscale funnel` is the public-internet version —
don't use it here:** behind this login are your real positions
(`data/portfolio.csv`, `data/options_positions.csv`) and the SPY proposal
endpoint that approves order placement, and the app's own protection is one
password with a five-try lockout.
