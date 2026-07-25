"""
Tests for scheduling.schedule_config — the user-editable job schedule
behind the Automation page, and its wiring to scheduler.SCHEDULED_JOBS.
No network, no schedule library interaction; config I/O is redirected to a
temp file.
Run with: python -m unittest tests.test_schedule_config
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.scheduling import schedule_config as sc


class _TempConfig(unittest.TestCase):
    """Redirect CONFIG_PATH to a temp file so tests never touch data/."""

    def setUp(self):
        self._orig = sc.CONFIG_PATH
        self._tmp = tempfile.TemporaryDirectory()
        sc.CONFIG_PATH = Path(self._tmp.name) / "schedule_config.json"

    def tearDown(self):
        sc.CONFIG_PATH = self._orig
        self._tmp.cleanup()


class TestNormalizeSpec(unittest.TestCase):
    def test_daily_times_from_form_string(self):
        spec = sc.normalize_spec(
            {"enabled": "on", "type": "daily", "times": "9:5, 16:30,9:05"},
            {"type": "daily", "times": ["06:30"]})
        self.assertEqual(spec, {"enabled": True, "type": "daily",
                                "times": ["09:05", "16:30"]})  # padded, deduped, sorted

    def test_interval_minutes_from_form_string(self):
        spec = sc.normalize_spec(
            {"enabled": "on", "type": "interval", "minutes": " 15 "},
            {"type": "interval", "minutes": 10})
        self.assertEqual(spec, {"enabled": True, "type": "interval", "minutes": 15})

    def test_unchecked_checkbox_disables(self):
        spec = sc.normalize_spec(
            {"enabled": False, "type": "daily", "times": "06:30"},
            {"type": "daily", "times": ["06:30"]})
        self.assertFalse(spec["enabled"])

    def test_bad_time_raises_readable_error(self):
        with self.assertRaises(ValueError) as ctx:
            sc.normalize_spec({"type": "daily", "times": "25:99"},
                              {"type": "daily", "times": ["06:30"]})
        self.assertIn("25:99", str(ctx.exception))

    def test_empty_times_raises(self):
        with self.assertRaises(ValueError):
            sc.normalize_spec({"type": "daily", "times": " , "},
                              {"type": "daily", "times": []})

    def test_minutes_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            sc.normalize_spec({"type": "interval", "minutes": "0"},
                              {"type": "interval", "minutes": 10})
        with self.assertRaises(ValueError):
            sc.normalize_spec({"type": "interval", "minutes": "2000"},
                              {"type": "interval", "minutes": 10})

    def test_bad_type_raises(self):
        with self.assertRaises(ValueError):
            sc.normalize_spec({"type": "hourly"}, {"type": "daily", "times": ["06:30"]})


class TestLoadAndSave(_TempConfig):
    def test_load_without_file_returns_defaults_for_every_job(self):
        cfg = sc.load_config()
        self.assertEqual(set(cfg), set(sc.JOB_DEFS))
        self.assertEqual(cfg["premarket_brief"], {"enabled": True, "type": "daily",
                                                  "times": ["07:00"]})
        self.assertEqual(cfg["watchlist_alerts"], {"enabled": True, "type": "interval",
                                                   "minutes": 10})

    def test_save_job_persists_and_merges(self):
        sc.save_job("watchlist_alerts", {"enabled": "on", "type": "interval",
                                         "minutes": "30"})
        cfg = sc.load_config()
        self.assertEqual(cfg["watchlist_alerts"]["minutes"], 30)
        # everything else untouched
        self.assertEqual(cfg["premarket_brief"]["times"], ["07:00"])
        # and it survives a raw re-read of the file
        on_disk = json.loads(sc.CONFIG_PATH.read_text())
        self.assertEqual(on_disk["watchlist_alerts"]["minutes"], 30)

    def test_save_unknown_job_raises(self):
        with self.assertRaises(ValueError):
            sc.save_job("no_such_job", {"type": "daily", "times": "06:30"})

    def test_corrupt_file_falls_back_to_defaults(self):
        sc.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        sc.CONFIG_PATH.write_text("{not json")
        cfg = sc.load_config()
        self.assertEqual(cfg["premarket_brief"]["times"], ["07:00"])

    def test_invalid_entry_in_file_is_ignored(self):
        sc.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        sc.CONFIG_PATH.write_text(json.dumps(
            {"premarket_brief": {"enabled": True, "type": "daily", "times": ["99:99"]}}))
        cfg = sc.load_config()
        self.assertEqual(cfg["premarket_brief"]["times"], ["07:00"])   # default kept

    def test_disable_job(self):
        sc.save_job("friday_scan", {"type": "daily", "times": "16:45"})  # no "enabled"
        # form-style save with checkbox absent -> enabled False
        spec = sc.save_job("friday_scan", {"enabled": False, "type": "daily",
                                           "times": "16:45"})
        self.assertFalse(spec["enabled"])
        self.assertFalse(sc.load_config()["friday_scan"]["enabled"])


class TestSchedulerWiring(unittest.TestCase):
    def test_every_config_job_maps_to_a_scheduler_function(self):
        """JOB_DEFS (what the Automation page offers) and SCHEDULED_JOBS
        (what the scheduler can run) must stay in lockstep — a key present
        in one but not the other would either render an uneditable row or
        silently never register."""
        from stockanalysis.scheduling.scheduler import SCHEDULED_JOBS
        self.assertEqual(set(sc.JOB_DEFS), set(SCHEDULED_JOBS))

    def test_describe_spec(self):
        self.assertEqual(sc.describe_spec({"type": "interval", "minutes": 10}),
                         "every 10 min")
        self.assertEqual(sc.describe_spec({"type": "daily", "times": ["06:30", "10:00"]}),
                         "daily at 06:30, 10:00 ET")


if __name__ == "__main__":
    unittest.main()
