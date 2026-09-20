"""
auth.py — password storage, sessions and login throttling
=========================================================
Stdlib-only authentication for the workstation web app, matching the rest of
the stack: no Flask-Login, no passlib, no session table in a database.

Three pieces, each small enough to audit in one sitting:

  passwords  PBKDF2-HMAC-SHA256, per-user random salt, stored in data/users.json
             as "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>". Verified
             with hmac.compare_digest so a wrong password can't be found one
             byte at a time. The plaintext is never written anywhere — not to
             the store, not to a log, not to an exception message.

  sessions   In-memory only: a dict of random 256-bit tokens. Restarting the
             server logs everybody out, which for a single-user local tool is
             a feature (no session file to steal, no revocation list to keep)
             rather than the limitation it would be for a hosted service.

  throttle   Failed attempts are counted per username and the account locks
             for a few minutes after MAX_FAILURES. Without this, a local
             attacker with a wordlist gets unlimited guesses at ~100ms each;
             with it they get five per lockout window.

Why hand-rolled PBKDF2 instead of bcrypt/argon2: those are better password
hashes, but they're C extensions this project doesn't otherwise need, and
`hashlib.pbkdf2_hmac` at 240k iterations is a standards-sanctioned KDF
(NIST SP 800-132) rather than a homemade one. The thing that would actually
be homemade crypto — inventing the hash construction — is exactly what this
avoids. If the project ever grows a dependency on argon2 for other reasons,
STORED_SCHEME makes the stored format self-describing so old hashes can be
recognised and upgraded on next login.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from stockanalysis.webapp.views import DATA_DIR

# Module-level so tests can point them at a temp dir (see tests/test_auth.py).
USERS_PATH = DATA_DIR / "users.json"

SCHEME       = "pbkdf2_sha256"
ITERATIONS   = 240_000      # ~100ms on this machine; re-tune, don't lower
SALT_BYTES   = 16
TOKEN_BYTES  = 32           # 256 bits of entropy in the session cookie

COOKIE_NAME  = "ws_session"
# Idle timeout, not absolute: a trading session that runs all day shouldn't
# drop you mid-scan, but a walked-away-from browser shouldn't stay open.
IDLE_TIMEOUT = 12 * 3600

# Whether something in front of this server terminates TLS — a Tailscale
# `serve`, a Cloudflare tunnel, a reverse proxy. Set WORKSTATION_BEHIND_TLS=1
# and the session cookie is marked Secure, so it is never sent in clear.
#
# Off by default, and that default is not laziness: this server speaks plain
# HTTP on loopback, and a Secure cookie there is never stored at all — the
# browser drops it, /login redirects back to /login, and the tool looks broken
# rather than locked. So the flag belongs to the deployment that earns it.
# The mirror-image trap is worth knowing before it costs an evening: with the
# flag ON, http://localhost:8899 can no longer sign in. Reach the tool through
# the HTTPS front door, or unset the flag.
#
# A module global rather than a read at each call, so a test can flip it the
# same way it points USERS_PATH at a temp dir.
BEHIND_TLS = (os.environ.get("WORKSTATION_BEHIND_TLS") or "").strip().lower() \
    in ("1", "true", "yes", "on")

MAX_FAILURES  = 5
LOCKOUT_SECS  = 300

_lock = threading.Lock()      # guards _sessions and _failures
_sessions: dict[str, dict] = {}
_failures: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# PASSWORD HASHING
# ─────────────────────────────────────────────────────────────────────────────

def hash_password(password: str, *, salt: bytes | None = None,
                  iterations: int = ITERATIONS) -> str:
    """Derive a storable hash string. A fresh random salt per call unless one
    is passed in (verification passes the stored salt back)."""
    if not password:
        raise ValueError("password must not be empty")
    salt = salt or secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{SCHEME}${iterations}${salt.hex()}${dk.hex()}"


def looks_hashed(stored) -> bool:
    """Whether a stored record is in the format verify_password can read.

    Exists because hand-editing users.json to put a plaintext password in is
    the obvious thing to try, it silently cannot work, and the failure
    surfaces as "Incorrect username or password" — which sends you off
    debugging the password you just typed instead of the file you just
    edited.
    """
    parts = str(stored or "").split("$")
    return len(parts) == 4 and parts[0] == SCHEME


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a candidate password against a stored hash.

    Returns False rather than raising on a malformed/unknown-scheme record:
    a corrupted users.json should fail the login, not 500 the login page."""
    try:
        scheme, iters, salt_hex, hash_hex = str(stored).split("$")
        if scheme != SCHEME:
            return False
        expected = bytes.fromhex(hash_hex)
        salt = bytes.fromhex(salt_hex)
        iterations = int(iters)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                             salt, iterations)
    return hmac.compare_digest(dk, expected)


# A hash of a random string, computed once, to verify against when the
# username doesn't exist. Without it, an unknown username returns in
# microseconds while a known one takes ~100ms — which tells an attacker
# which usernames are real before they've guessed a single password.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# ─────────────────────────────────────────────────────────────────────────────
# USER STORE
# ─────────────────────────────────────────────────────────────────────────────

def load_users() -> dict[str, dict]:
    """Read the user store. Missing file = no users yet (first run)."""
    try:
        data = json.loads(USERS_PATH.read_text())
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        # A parse failure must not silently look like "no users", because the
        # caller's response to that is to bootstrap a brand-new account — i.e.
        # a read error would become a write that replaces the real one.
        raise RuntimeError(
            f"{USERS_PATH} exists but could not be read as JSON. Fix or remove "
            f"it; the server will not overwrite it.")
    return data.get("users", {}) if isinstance(data, dict) else {}


def save_users(users: dict[str, dict]) -> None:
    """Atomic write, owner-only permissions. Written to a temp file in the
    same directory and renamed, so an interrupted write can't leave a
    truncated store that locks the user out of their own tool."""
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"users": users}, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_PATH)


def set_user_password(username: str, password: str) -> None:
    """Create the user or replace their password. Any live sessions for that
    user are dropped — a password change should end sessions opened with the
    old one."""
    username = (username or "").strip()
    if not username:
        raise ValueError("username must not be empty")
    if len(password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    users = load_users()
    users[username] = {"password": hash_password(password),
                       "created": users.get(username, {}).get(
                           "created", time.strftime("%Y-%m-%d %H:%M:%S"))}
    save_users(users)
    with _lock:
        for tok in [t for t, s in _sessions.items() if s["username"] == username]:
            _sessions.pop(tok, None)
        _failures.pop(username, None)


def bootstrap_if_empty() -> tuple[str, str] | None:
    """First-run account creation. Returns (username, password) if it created
    one, else None.

    Credentials come from WORKSTATION_USER / WORKSTATION_PASSWORD when set;
    otherwise a random password is generated and returned for the caller to
    print once. There is deliberately no default password — a shipped
    "admin/admin" is the single most common way a tool like this ends up
    open, and a random one costs the user one copy-paste."""
    if load_users():
        return None
    username = os.environ.get("WORKSTATION_USER") or "admin"
    password = os.environ.get("WORKSTATION_PASSWORD") or secrets.token_urlsafe(12)
    set_user_password(username, password)
    return username, password


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN THROTTLING
# ─────────────────────────────────────────────────────────────────────────────

def _lockout_remaining(username: str) -> int:
    rec = _failures.get(username)
    if not rec:
        return 0
    return max(0, int(rec.get("until", 0) - time.time()))


def _record_failure(username: str) -> None:
    rec = _failures.setdefault(username, {"count": 0, "until": 0})
    rec["count"] += 1
    if rec["count"] >= MAX_FAILURES:
        rec["until"] = time.time() + LOCKOUT_SECS
        rec["count"] = 0


# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION
# ─────────────────────────────────────────────────────────────────────────────

def authenticate(username: str, password: str) -> tuple[str | None, str]:
    """Check credentials. Returns (session_token, "") on success, or
    (None, reason) on failure.

    The reason shown to the user is deliberately the same for a wrong
    username and a wrong password — "Incorrect username or password" — so the
    form can't be used to enumerate accounts. The lockout message is the one
    exception: hiding it would just look like the password stopped working."""
    username = (username or "").strip()

    with _lock:
        wait = _lockout_remaining(username)
    if wait:
        return None, f"Too many failed attempts. Try again in {wait // 60 + 1} min."

    users = load_users()
    record = users.get(username)

    # A record that isn't in the stored format is a broken store, not a wrong
    # password, and it is worth saying so plainly. This does reveal that the
    # account exists — which the generic message above deliberately avoids —
    # and that trade is made on purpose: the alternative is an unrecoverable
    # lockout whose only symptom points at the password you typed. On a
    # loopback tool whose store is readable by whoever can reach the port
    # anyway, a diagnosable failure is worth more than the enumeration
    # hardening it costs.
    if record and not looks_hashed(record.get("password")):
        print(f"[auth] '{username}' has a password that is not a "
              f"{SCHEME} hash — users.json was probably edited by hand. "
              f"Reset it with: python app.py --set-password")
        return None, (f"The stored password for '{username}' is not in the "
                      f"hashed format this file uses — a password cannot be "
                      f"written into users.json directly. Reset it with "
                      f"`python app.py --set-password`.")

    # Verify against the dummy hash for unknown users so both paths cost the
    # same ~100ms (see _DUMMY_HASH).
    ok = verify_password(password, record["password"] if record else _DUMMY_HASH)

    if not record or not ok:
        with _lock:
            _record_failure(username)
        return None, "Incorrect username or password."

    with _lock:
        _failures.pop(username, None)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = time.time()
        _sessions[token] = {"username": username, "created": now, "seen": now}
    return token, ""


def session_for(token: str | None) -> dict | None:
    """Resolve a cookie token to a live session, refreshing its idle clock.
    Returns None for unknown, expired or missing tokens."""
    if not token:
        return None
    now = time.time()
    with _lock:
        sess = _sessions.get(token)
        if not sess:
            return None
        if now - sess["seen"] > IDLE_TIMEOUT:
            _sessions.pop(token, None)
            return None
        sess["seen"] = now
        return dict(sess)


def destroy_session(token: str | None) -> None:
    if token:
        with _lock:
            _sessions.pop(token, None)


def _cookie_attrs() -> str:
    """The attributes every session cookie carries, set and cleared alike.

    One function for both because a cleared cookie has to be written with the
    SAME attributes as the one it replaces: a Set-Cookie whose flags disagree
    can be stored as a second cookie instead of overwriting the first, and a
    sign-out that leaves the old cookie in place is a sign-out that did not
    happen.
    """
    return "HttpOnly; SameSite=Lax; Path=/" + ("; Secure" if BEHIND_TLS else "")


def cookie_header(token: str) -> str:
    """Set-Cookie value for a fresh session.

    HttpOnly     — JavaScript can't read the token, so an XSS bug in a
                   research page can't exfiltrate the session.
    SameSite=Lax — a form POST from another site carries no cookie, which is
                   what makes the mutating endpoints (/run, /api/portfolio/*)
                   CSRF-resistant without a token on every form. Lax rather
                   than Strict so following a bookmark still lands you
                   logged in.
    Secure       — only when BEHIND_TLS says something in front is terminating
                   HTTPS. Absent on the loopback default on purpose: a Secure
                   cookie over plain HTTP is never stored, so setting it there
                   locks you out instead of protecting you.
    """
    return f"{COOKIE_NAME}={token}; {_cookie_attrs()}; Max-Age={IDLE_TIMEOUT}"


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; {_cookie_attrs()}; Max-Age=0"


def parse_cookie(header: str | None) -> str | None:
    """Pull our session token out of a raw Cookie header."""
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME:
            return value or None
    return None


def active_sessions() -> int:
    """How many live (non-idle-expired) sessions exist right now.

    Prunes expired ones as it counts, because nothing else does: session_for
    only ever looks at the one token in front of it, so a session belonging
    to a browser that was closed hours ago sits in the dict until someone
    presents its cookie again — which, for a closed browser, is never. The
    scheduler's login gate (see scheduler.LOGIN_GATE) reads this, and a
    gate that counts ghosts would keep automation running forever after the
    last real sign-out, which is exactly what it exists to prevent.
    """
    now = time.time()
    with _lock:
        for tok in [t for t, s in _sessions.items()
                    if now - s["seen"] > IDLE_TIMEOUT]:
            _sessions.pop(tok, None)
        return len(_sessions)


def anyone_signed_in() -> bool:
    """True while at least one session is live. Note what this is NOT: a
    record that a human logged in at some point. Sessions live in memory
    only (see the module docstring), so a server restart means nobody is
    signed in until someone signs in again."""
    return active_sessions() > 0
