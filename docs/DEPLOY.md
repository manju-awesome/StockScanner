# Deploying the Trading Workstation to a VPS

Target: Hetzner CX22 (~€3.79/mo, 2 vCPU / 4GB / 40GB) running Debian 12 or 13.
Identical steps work on Oracle Cloud Always Free (ARM) or any plain Linux box.

Result: `https://your-domain` → Caddy (TLS) → `127.0.0.1:8899` → this app,
with the scan scheduler running on ET and surviving reboots.

---

## Four facts about this app that shape the deployment

1. **It binds `127.0.0.1` unconditionally** (`webapp/app.py`, `ThreadingHTTPServer`).
   There is no `--host` flag. This is a feature here: the app can never be
   reached directly from the internet, only through the reverse proxy. No code
   change is needed or wanted.

2. **`data/` paths are derived from the repo root**, not from an env var
   (e.g. `reporting/research.py:718`). The whole checkout must sit on
   persistent storage. Trivially true on a VPS; on a container platform you
   would have to mount a volume at exactly that path.

3. **The scheduler runs on *system local time*, not ET.** `scheduler.py`
   registers jobs with `schedule.every().day.at(hhmm)` (lines 890, 941), which
   the `schedule` library compares against a naive `datetime.now()`. The
   surrounding code documents those times as Eastern. That assumption only
   holds if the machine's timezone *is* Eastern. A fresh VPS is UTC, so every
   scan would fire 4–5 hours off, silently. **Step 4 is not optional.**

4. **Automation pauses when nobody is signed in — turn that off for a VPS.**
   By default the scan scheduler and the SPY signal daemon only run while a
   login session is live, and pause on sign-out. Sessions are in-memory, so a
   reboot or a `systemctl restart` also counts as nobody signed in: with the
   default, the 07:00 pre-market brief does not send unless you happen to have
   the tool open. An always-on deployment wants the opposite. Tick **“Keep
   automation running when nobody is signed in”** under Automation →
   Scheduler, or set it before first boot:

   ```bash
   python3 -c "import sys; sys.path.insert(0,'src'); from stockanalysis.scheduling.schedule_config import save_settings; print(save_settings({'run_when_logged_out': True}))"
   ```

   It is stored in `data/schedule_config.json` under `_settings`, so it
   survives restarts and redeploys of the same checkout.

---

## Step 0 — Confirm the source is current (done)

Deploying from **`Researchstocks.git`**, not `StockScanner.git`.

That repo is rooted at `~/Documents`, so a clone contains:

```
<clone>/
├── Claude_projects/
│   ├── July01-2027/StockAnalysis_Version1/   <- the app
│   └── Findfarms/
├── Trading/
└── .vscode/
```

As of 2026-08-30 `main` and `origin/main` are both at `69f01ee` with a clean
working tree, and the app's 204 source files, `requirements.txt` and
`pyproject.toml` are all tracked. Nothing to push. Verify before deploying:

```bash
git -C ~/Documents fetch origin && git -C ~/Documents status -sb
```

The inner `StockAnalysis_Version1/.git` (pointing at `StockScanner.git`) is an
embedded repo whose files this repo tracks directly — there is no submodule,
so the clone gets plain files and you can ignore that repo entirely. Just
remember commits made *inside* it do not reach `Researchstocks.git`; commit
from `~/Documents` for anything you intend to deploy.

---

## Step 1 — Smoke-test yfinance from a cloud IP *before* you pay for a year

This is the one thing that can make the whole plan fail, and it costs an hour
to find out. Yahoo rate-limits datacenter IP ranges far more aggressively than
residential ones. Spin the box up hourly-billed, and before anything else:

```bash
python3 -c "import yfinance as yf; d=yf.download(['AAPL','MSFT','NVDA'],period='6mo',progress=False); print(d.shape); print(d.tail(2))"
```

Empty frames, `None`, or repeated rate-limit warnings mean this host's IP is
throttled. Destroy the box and try a different provider or region before
building anything on it. If it returns ~126 rows per ticker, continue.

---

## Step 2 — Provision

Create the server with your SSH public key attached (never a root password).
Debian 12/13, cheapest region that passed Step 1.

---

## Step 3 — Non-root user and firewall

```bash
adduser --disabled-password --gecos "" workstation
usermod -aG sudo workstation
rsync --archive --chown=workstation:workstation ~/.ssh /home/workstation/
apt update && apt install -y ufw
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

Port 8899 is deliberately **not** opened. The app listens only on loopback;
Caddy is the only thing that talks to it.

---

## Step 4 — Timezone (see fact #3 — do not skip)

```bash
sudo timedatectl set-timezone America/New_York && timedatectl
```

Verify `Time zone: America/New_York (EST, -0500)` before continuing. This is
what makes the scheduler's 09:30 mean 09:30 ET.

---

## Step 5 — Python, code, dependencies

```bash
sudo apt install -y python3 python3-venv python3-pip git
sudo mkdir -p /opt/workstation && sudo chown workstation:workstation /opt/workstation
```

As the `workstation` user. If the repo is private, generate a key first
(`ssh-keygen -t ed25519 -C deploy`) and add the public half to the repo's
**Settings → Deploy keys** as read-only:

```bash
git clone https://github.com/manju-awesome/Researchstocks.git /opt/workstation/repo
```

The repo root is `~/Documents`, so **the app is not at the clone root**. Every
path below uses:

```
APP_DIR = /opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1
```

```bash
cd /opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

`pip install -e .` is what makes `python -m stockanalysis.webapp.app` work.

`requirements.txt` pins no versions, so this box may get different library
versions than your Mac. Once it runs correctly, lock it:
`.venv/bin/pip freeze > requirements.lock.txt`

To update the deployment later, `git -C /opt/workstation/repo pull` then
`sudo systemctl restart workstation`.

---

## Step 6 — Secrets and private data

I checked what `Researchstocks.git` actually carries: **no secrets are in it.**
`.env`, `portfolio.csv`, `options_positions.csv` and `data/users.json` are all
untracked or excluded by the repo-root `.gitignore`, and `data/cache/` (99MB)
and `data/output/` (68MB) are excluded too — so the clone is small and the
regenerable directories rebuild themselves. Do not copy those two.

That leaves the same four items to copy by hand. `data/spy/` ships only its
`.gitkeep`, so the live 0DTE state is not in the clone either:

```bash
cd "/Users/manju/Documents/Claude_projects/July01-2027/StockAnalysis_Version1"
scp .env workstation@YOUR_IP:/opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1/.env
scp data/portfolio.csv data/options_positions.csv workstation@YOUR_IP:/opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1/data/
scp data/spy/*.json workstation@YOUR_IP:/opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1/data/spy/
```

Then lock the secrets file down:

```bash
chmod 600 /opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1/.env
```

`.env` is read by `python-dotenv` from the project root, so systemd does not
need an `EnvironmentFile` — which also avoids systemd's stricter parsing rules
for quotes and comments.

Review `.env` on the server afterwards: `ACCOUNT_SIZE` and
`ROBINHOOD_ACCOUNT_NUMBER` now live on a machine you don't physically control.

`data/users.json` is deliberately **not** copied — it is excluded at the repo
root because a committed PBKDF2 hash is an offline cracking target, and it is
per-machine state anyway. Step 7 creates a fresh one on the server.

---

## Step 7 — Create the login

`data/users.json` is not tracked, so the server has no accounts yet. First run
bootstraps one and prints a generated password **once**:

```bash
cd /opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1 && .venv/bin/python -m stockanalysis.webapp.app
```

Copy the password, Ctrl-C, then set your own:

```bash
.venv/bin/python -m stockanalysis.webapp.app --set-password
```

Use a strong, unique password. This is the only thing between the open
internet and your positions.

---

## Step 8 — systemd service

```bash
sudo tee /etc/systemd/system/workstation.service > /dev/null <<'EOF'
[Unit]
Description=Trading Workstation
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=workstation
WorkingDirectory=/opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1
Environment=WORKSTATION_BEHIND_TLS=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1/.venv/bin/python -m stockanalysis.webapp.app --port 8899
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now workstation
sudo systemctl status workstation
```

`WORKSTATION_BEHIND_TLS=1` is what makes `auth.py` mark the session cookie
`Secure`. Without it the cookie is sent over plaintext; with it set but *no*
HTTPS in front, the browser drops the cookie and `/login` just redirects back
to itself. So set it only once Caddy is actually serving TLS (Step 9) — or
expect exactly that symptom in between.

`PYTHONUNBUFFERED=1` is what makes `journalctl` show scan output as it
happens rather than in delayed chunks.

---

## Step 9 — Caddy and DNS

Point an `A` record for your domain at the server's IP first, then:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

The entire config:

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
workstation.yourdomain.com {
    reverse_proxy 127.0.0.1:8899
}
EOF

sudo systemctl reload caddy
```

Caddy obtains and renews the Let's Encrypt certificate automatically. There is
no certbot step and no renewal cron to forget.

---

## Step 10 — Verify

```bash
systemctl is-active workstation caddy          # both: active
curl -sI https://workstation.yourdomain.com/   # 200 or 302 to /login
journalctl -u workstation -n 50 --no-pager     # scheduler + SPY daemon lines
date                                            # must read EST/EDT, not UTC
```

In a browser: log in, then confirm the Dashboard renders and one scan runs
end to end. Watch `journalctl -u workstation -f` during it — this is where a
throttled yfinance shows up as empty results rather than an error.

---

## Step 11 — Backups

`data/` is the whole application state and it has no database to dump. The
irreplaceable parts are `data/csp/iv_history.json` (accumulates one IV
observation per ticker per run and **cannot** be backfilled — yfinance only
serves the current surface), `data/users.json`, `data/journal_trades.json`,
and your portfolio CSVs.

```bash
sudo tee /etc/cron.daily/workstation-backup > /dev/null <<'EOF'
#!/bin/sh
tar --exclude='data/cache' --exclude='data/output' \
    -czf /home/workstation/backup-$(date +\%F).tar.gz \
    -C /opt/workstation/repo/Claude_projects/July01-2027/StockAnalysis_Version1 data
find /home/workstation -name 'backup-*.tar.gz' -mtime +14 -delete
EOF
sudo chmod +x /etc/cron.daily/workstation-backup
```

That excludes the two regenerable directories, so the archive stays small.
Pull a copy off the box periodically — a backup on the same disk is not one.

---

## If Step 1 failed: yfinance is throttled

In rough order of cost:

1. Try a different provider/region — throttling is per IP range.
2. Keep the scanners on your Mac (residential IP) and host only the read-only
   UI, syncing `data/` up on a schedule.
3. Move to a paid data source with an API key.

Do not route Yahoo traffic through a proxy pool to evade the limit — that
breaks their terms and gets the IP banned outright.
