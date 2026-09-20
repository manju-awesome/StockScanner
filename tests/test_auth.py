"""
Tests for webapp login (webapp.auth).

The invariants worth pinning, in the order they'd hurt if broken:
  1. A plaintext password is never what's stored, and two users with the same
     password don't share a hash (per-user salt).
  2. A wrong password fails, and failing five times locks the account rather
     than granting an unlimited guess budget.
  3. A session token is unguessable, expires when idle, and dies on logout
     and on password change.
  4. A read error on users.json never gets mistaken for "no users yet" —
     that's the path where a bad read turns into a write that replaces the
     real account (the same failure shape as tests/test_saved_screens.py).

Run with: python -m unittest tests.test_auth
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.webapp import auth


class AuthCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._orig_path = auth.USERS_PATH
        auth.USERS_PATH = self.dir / "users.json"
        # Real PBKDF2 at 240k iterations is ~100ms per verify, and these tests
        # verify dozens of times. The construction under test is identical at
        # any iteration count; only the work factor changes.
        self._orig_iters = auth.ITERATIONS
        auth.ITERATIONS = 1_000
        auth._sessions.clear()
        auth._failures.clear()

    def tearDown(self):
        auth.USERS_PATH = self._orig_path
        auth.ITERATIONS = self._orig_iters
        auth._sessions.clear()
        auth._failures.clear()


class TestPasswordHashing(AuthCase):
    def test_hash_does_not_contain_the_password(self):
        stored = auth.hash_password("correct horse battery staple")
        self.assertNotIn("correct", stored)
        self.assertNotIn("staple", stored)

    def test_same_password_hashes_differently_per_user(self):
        a = auth.hash_password("same-password-here")
        b = auth.hash_password("same-password-here")
        self.assertNotEqual(a, b, "salt is not being applied per hash")
        self.assertTrue(auth.verify_password("same-password-here", a))
        self.assertTrue(auth.verify_password("same-password-here", b))

    def test_verify_rejects_wrong_password(self):
        stored = auth.hash_password("hunter2-hunter2")
        self.assertFalse(auth.verify_password("hunter2-hunter3", stored))
        self.assertFalse(auth.verify_password("", stored))

    def test_verify_survives_a_corrupt_record(self):
        for junk in ("", "not-a-hash", "bcrypt$1$aa$bb", "pbkdf2_sha256$x$y$z"):
            self.assertFalse(auth.verify_password("anything", junk))

    def test_stored_file_has_no_plaintext_and_is_owner_only(self):
        auth.set_user_password("manju", "a-real-password")
        text = auth.USERS_PATH.read_text()
        self.assertNotIn("a-real-password", text)
        self.assertEqual(auth.USERS_PATH.stat().st_mode & 0o777, 0o600)


class TestUserStore(AuthCase):
    def test_short_passwords_are_refused(self):
        with self.assertRaises(ValueError):
            auth.set_user_password("manju", "short")
        self.assertFalse(auth.load_users())

    def test_unreadable_store_is_not_read_as_empty(self):
        auth.USERS_PATH.write_text("{ this is not json")
        with self.assertRaises(RuntimeError):
            auth.load_users()

    def test_bootstrap_creates_one_account_then_stops(self):
        created = auth.bootstrap_if_empty()
        self.assertIsNotNone(created)
        self.assertIsNone(auth.bootstrap_if_empty(),
                          "bootstrap re-ran over an existing account")

    def test_bootstrap_password_is_random_not_a_default(self):
        first = auth.bootstrap_if_empty()[1]
        auth.USERS_PATH.unlink()
        second = auth.bootstrap_if_empty()[1]
        self.assertNotEqual(first, second)
        self.assertNotIn(first, ("admin", "password", "changeme"))


class TestAuthentication(AuthCase):
    def setUp(self):
        super().setUp()
        auth.set_user_password("manju", "correct-password")

    def test_right_password_returns_a_session(self):
        token, err = auth.authenticate("manju", "correct-password")
        self.assertTrue(token)
        self.assertEqual(err, "")
        self.assertEqual(auth.session_for(token)["username"], "manju")

    def test_wrong_password_and_unknown_user_are_indistinguishable(self):
        _, wrong_pw = auth.authenticate("manju", "nope")
        _, no_user = auth.authenticate("ghost", "nope")
        self.assertEqual(wrong_pw, no_user,
                         "error text lets an attacker enumerate usernames")

    def test_lockout_after_repeated_failures(self):
        for _ in range(auth.MAX_FAILURES):
            token, _ = auth.authenticate("manju", "wrong")
            self.assertIsNone(token)
        # Even the CORRECT password is refused while locked out — otherwise
        # the lockout only slows down an attacker who is already wrong.
        token, err = auth.authenticate("manju", "correct-password")
        self.assertIsNone(token)
        self.assertIn("Too many failed attempts", err)

    def test_a_success_clears_the_failure_count(self):
        for _ in range(auth.MAX_FAILURES - 1):
            auth.authenticate("manju", "wrong")
        self.assertTrue(auth.authenticate("manju", "correct-password")[0])
        for _ in range(auth.MAX_FAILURES - 1):
            auth.authenticate("manju", "wrong")
        self.assertTrue(auth.authenticate("manju", "correct-password")[0],
                        "old failures were still counting toward the lockout")


class TestHandEditedStore(AuthCase):
    """Putting a plaintext password into users.json is the obvious thing to
    try, it cannot work, and the failure used to be indistinguishable from a
    wrong password. These pin the diagnosis, not just the refusal."""

    def _write_plaintext(self):
        auth.USERS_PATH.write_text(json.dumps(
            {"users": {"manju": {"password": "manju@2026"}}}))

    def test_looks_hashed_tells_the_two_apart(self):
        self.assertTrue(auth.looks_hashed(auth.hash_password("a-password")))
        for junk in ("manju@2026", "", None, "pbkdf2_sha256$1$2",
                     "bcrypt$1$aa$bb"):
            self.assertFalse(auth.looks_hashed(junk), junk)

    def test_a_plaintext_record_never_authenticates(self):
        # Including — especially — with the exact string that was typed in.
        self._write_plaintext()
        self.assertIsNone(auth.authenticate("manju", "manju@2026")[0])

    def test_the_error_names_the_real_problem(self):
        self._write_plaintext()
        _, err = auth.authenticate("manju", "manju@2026")
        self.assertIn("users.json", err)
        self.assertIn("--set-password", err)
        self.assertNotIn("Incorrect username or password", err)

    def test_set_password_repairs_it(self):
        self._write_plaintext()
        auth.set_user_password("manju", "a-proper-password")
        self.assertTrue(auth.looks_hashed(
            auth.load_users()["manju"]["password"]))
        self.assertTrue(auth.authenticate("manju", "a-proper-password")[0])

    def test_a_broken_record_does_not_break_the_other_users(self):
        auth.set_user_password("real", "a-proper-password")
        users = auth.load_users()
        users["manju"] = {"password": "manju@2026"}
        auth.save_users(users)
        self.assertTrue(auth.authenticate("real", "a-proper-password")[0])


class TestSessions(AuthCase):
    def setUp(self):
        super().setUp()
        auth.set_user_password("manju", "correct-password")
        self.token = auth.authenticate("manju", "correct-password")[0]

    def test_tokens_are_long_and_unique(self):
        other = auth.authenticate("manju", "correct-password")[0]
        self.assertNotEqual(self.token, other)
        self.assertGreaterEqual(len(self.token), 32)

    def test_unknown_or_missing_token_has_no_session(self):
        self.assertIsNone(auth.session_for(None))
        self.assertIsNone(auth.session_for(""))
        self.assertIsNone(auth.session_for("made-up-token"))

    def test_logout_kills_the_session(self):
        auth.destroy_session(self.token)
        self.assertIsNone(auth.session_for(self.token))

    def test_idle_session_expires(self):
        auth._sessions[self.token]["seen"] = time.time() - auth.IDLE_TIMEOUT - 1
        self.assertIsNone(auth.session_for(self.token))

    def test_activity_pushes_the_idle_clock_out(self):
        auth._sessions[self.token]["seen"] = time.time() - auth.IDLE_TIMEOUT + 60
        self.assertIsNotNone(auth.session_for(self.token))   # refreshes 'seen'
        auth._sessions[self.token]["seen"] = time.time() - auth.IDLE_TIMEOUT + 60
        self.assertIsNotNone(auth.session_for(self.token))

    def test_password_change_signs_existing_sessions_out(self):
        auth.set_user_password("manju", "a-brand-new-password")
        self.assertIsNone(auth.session_for(self.token))


class TestCookies(AuthCase):
    def test_cookie_is_httponly_and_samesite(self):
        header = auth.cookie_header("tok123")
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Lax", header)
        self.assertIn("Path=/", header)

    def test_no_secure_flag_on_the_loopback_default(self):
        """The default is plain HTTP on 127.0.0.1, where a Secure cookie is
        never stored — the browser drops it and /login redirects to /login
        forever. Absent here is the working configuration, not an oversight."""
        self.assertNotIn("Secure", auth.cookie_header("tok123"))

    def test_secure_flag_when_something_in_front_terminates_tls(self):
        """WORKSTATION_BEHIND_TLS=1 — a tailscale serve, a tunnel, a proxy."""
        original = auth.BEHIND_TLS
        auth.BEHIND_TLS = True
        try:
            self.assertIn("Secure", auth.cookie_header("tok123"))
            # The rest must survive the flag: Secure protects the transport,
            # HttpOnly protects against XSS and SameSite against CSRF, and
            # turning on one is not a reason to have dropped another.
            self.assertIn("HttpOnly", auth.cookie_header("tok123"))
            self.assertIn("SameSite=Lax", auth.cookie_header("tok123"))
        finally:
            auth.BEHIND_TLS = original

    def test_clearing_a_cookie_matches_the_flags_it_replaces(self):
        """A Set-Cookie whose flags disagree with the stored cookie can be
        kept alongside it rather than overwriting it — which would make a
        sign-out that reports success leave the session cookie in place."""
        original = auth.BEHIND_TLS
        try:
            for behind_tls in (False, True):
                auth.BEHIND_TLS = behind_tls
                set_flags = auth.cookie_header("tok123").split("; ")[1:]
                clear_flags = auth.clear_cookie_header().split("; ")[1:]
                self.assertEqual(
                    [f for f in set_flags if not f.startswith("Max-Age")],
                    [f for f in clear_flags if not f.startswith("Max-Age")],
                    f"flags diverge with BEHIND_TLS={behind_tls}")
        finally:
            auth.BEHIND_TLS = original

    def test_parse_cookie_finds_our_token_among_others(self):
        raw = f"theme=dark; {auth.COOKIE_NAME}=abc123; other=1"
        self.assertEqual(auth.parse_cookie(raw), "abc123")

    def test_parse_cookie_on_junk_returns_none(self):
        self.assertIsNone(auth.parse_cookie(None))
        self.assertIsNone(auth.parse_cookie(""))
        self.assertIsNone(auth.parse_cookie("theme=dark"))
        self.assertIsNone(auth.parse_cookie(f"{auth.COOKIE_NAME}="))

    def test_clearing_cookie_expires_it(self):
        self.assertIn("Max-Age=0", auth.clear_cookie_header())


class TestRedirectSafety(unittest.TestCase):
    """The ?next= parameter comes off the URL, so it's attacker-controlled."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                               / "src" / "stockanalysis" / "webapp"))
        from stockanalysis.webapp.app import WorkstationHandler
        self.safe = WorkstationHandler._safe_next

    def test_local_paths_pass_through(self):
        for path in ("/", "/portfolio", "/research?ticker=NVDA"):
            self.assertEqual(self.safe(path), path)

    def test_offsite_targets_are_dropped(self):
        for evil in ("https://evil.example", "//evil.example",
                     "http://evil.example/x", "evil.example"):
            self.assertEqual(self.safe(evil), "/",
                             f"open redirect via {evil!r}")


if __name__ == "__main__":
    unittest.main()
