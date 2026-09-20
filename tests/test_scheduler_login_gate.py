"""
Tests for the sign-out gate on the automation scheduler.

The point of the gate: nothing scans, emails or writes while the workstation
is unattended. The invariants worth pinning, in the order they'd hurt if
broken:

  1. With the gate on and nobody signed in, jobs are not allowed to run —
     that is the whole feature.
  2. Signing in allows them again, signing out pauses them again. A session
     that merely idled out counts as signed out.
  3. The "keep running when nobody is signed in" setting overrides the gate,
     because an always-on deployment has to send its 07:00 brief with no
     browser open.
  4. The gate is OFF unless the webapp turns it on. scheduler.py standalone
     and --no-auth create no sessions at all, so a gate that applied there
     would pause automation forever rather than gate it.
  5. Saving a job's cadence doesn't silently reset the setting (they share
     one file, and a job save rewrites it).
  6. A paused tick fires nothing, and resuming does NOT replay the backlog.
     `schedule` leaves a skipped job's next_run in the past, so a resume that
     only called run_pending() would email the 07:00 brief at 15:00 and run
     the close scan on top of it the moment you signed in.

Run with: python -m unittest tests.test_scheduler_login_gate
"""
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.scheduling import schedule_config
from stockanalysis.scheduling import scheduler
from stockanalysis.webapp import auth


class LoginGateCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

        # Never point these at the live data/ dir: the config file is the one
        # the running scheduler reads, and users.json is the real login.
        self._orig_cfg = schedule_config.CONFIG_PATH
        schedule_config.CONFIG_PATH = self.dir / "schedule_config.json"
        self._orig_users = auth.USERS_PATH
        auth.USERS_PATH = self.dir / "users.json"
        self._orig_iters = auth.ITERATIONS
        auth.ITERATIONS = 1_000          # see tests/test_auth.py
        self._orig_gate = scheduler.LOGIN_GATE

        auth._sessions.clear()
        auth._failures.clear()
        auth.set_user_password("trader", "correct horse battery")
        scheduler.set_login_gate(True)

    def tearDown(self):
        schedule_config.CONFIG_PATH = self._orig_cfg
        auth.USERS_PATH = self._orig_users
        auth.ITERATIONS = self._orig_iters
        scheduler.LOGIN_GATE = self._orig_gate
        auth._sessions.clear()
        auth._failures.clear()

    def _sign_in(self) -> str:
        token, err = auth.authenticate("trader", "correct horse battery")
        self.assertEqual(err, "")
        return token

    # 1 + 2 — the gate follows the session
    def test_paused_until_signed_in_and_again_after_signout(self):
        allowed, why = scheduler.automation_allowed()
        self.assertFalse(allowed)
        self.assertIn("signed in", why)

        token = self._sign_in()
        self.assertEqual(scheduler.automation_allowed(), (True, ""))

        auth.destroy_session(token)
        self.assertFalse(scheduler.automation_allowed()[0])

    def test_idled_out_session_does_not_hold_automation_open(self):
        token = self._sign_in()
        # Backdate the session past the idle timeout. Nothing else prunes it:
        # session_for() only ever sees the token in front of it, and a closed
        # browser never presents one again.
        auth._sessions[token]["seen"] = time.time() - auth.IDLE_TIMEOUT - 1
        self.assertFalse(scheduler.automation_allowed()[0])
        self.assertNotIn(token, auth._sessions)

    # 3 — the config override
    def test_run_when_logged_out_overrides_the_gate(self):
        self.assertFalse(scheduler.automation_allowed()[0])
        schedule_config.save_settings({"run_when_logged_out": "on"})
        self.assertEqual(scheduler.automation_allowed(), (True, ""))
        schedule_config.save_settings({"run_when_logged_out": "off"})
        self.assertFalse(scheduler.automation_allowed()[0])

    # 4 — off by default, for standalone/--no-auth
    def test_gate_off_means_always_allowed(self):
        scheduler.set_login_gate(False)
        self.assertEqual(scheduler.automation_allowed(), (True, ""))

    def test_broken_config_fails_open(self):
        schedule_config.CONFIG_PATH.write_text("{ not json")
        # Nobody is signed in, but an unreadable config must not be the reason
        # every scheduled job stops: load_settings falls back to defaults, so
        # the gate still answers from the session state rather than raising.
        self.assertFalse(scheduler.automation_allowed()[0])
        self._sign_in()
        self.assertTrue(scheduler.automation_allowed()[0])

    # 5 — jobs and settings share one file
    def test_job_save_preserves_settings_and_vice_versa(self):
        schedule_config.save_settings({"run_when_logged_out": True})
        schedule_config.save_job("premarket_brief",
                                 {"enabled": True, "type": "daily", "times": "07:15"})

        self.assertTrue(schedule_config.load_settings()["run_when_logged_out"])
        self.assertEqual(schedule_config.load_config()["premarket_brief"]["times"],
                         ["07:15"])

        # And the settings block is not mistaken for a job in either direction.
        saved = json.loads(schedule_config.CONFIG_PATH.read_text())
        self.assertIn(schedule_config.SETTINGS_KEY, saved)
        self.assertNotIn(schedule_config.SETTINGS_KEY, schedule_config.load_config())

        schedule_config.save_settings({"run_when_logged_out": False})
        self.assertEqual(schedule_config.load_config()["premarket_brief"]["times"],
                         ["07:15"])


    # 6 — the loop's pause/resume transitions
    def test_paused_tick_runs_nothing_and_resume_does_not_replay(self):
        import schedule

        fired = []
        schedule.clear()
        self.addCleanup(schedule.clear)
        job = schedule.every().day.at("00:00").do(lambda: fired.append(1))
        # Make it overdue, the way a 07:00 job is by mid-afternoon.
        job.next_run = datetime.now() - timedelta(hours=8)

        # Nobody signed in: the tick must not run it, and must not touch it.
        self.assertTrue(scheduler._tick(paused=False))
        self.assertEqual(fired, [])
        self.assertTrue(scheduler._tick(paused=True))
        self.assertEqual(fired, [])

        # Signing in resumes. The overdue job is re-registered at its next
        # real occurrence instead of firing for the window nobody was watching.
        self._sign_in()
        with mock.patch.object(scheduler, "register_jobs") as reg:
            self.assertFalse(scheduler._tick(paused=True))
        reg.assert_called_once()
        self.assertEqual(fired, [])
        self.assertEqual(schedule.jobs, [])   # cleared before re-registering


if __name__ == "__main__":
    unittest.main()
