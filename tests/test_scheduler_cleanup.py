"""
Tests for scheduler.cleanup_outputs — temp dirs, no network.
Run with: python -m unittest tests.test_scheduler_cleanup
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.scheduling.scheduler import cleanup_outputs


class TestCleanup(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "research").mkdir()
        old = time.time() - 10 * 86400
        self.files = {
            "stock_scan_old.csv": True,     # generated + old  -> removed
            "dashboard_old.html": True,
            "research/OLD.html":  True,
            "stock_scan_new.csv": False,    # generated + fresh -> kept
            "research/NEW.html":  False,
            "portfolio.csv":      True,     # user state -> kept even if old
            "scan_history.csv":   True,     # rolling log -> kept even if old
        }
        for name, is_old in self.files.items():
            f = self.dir / name
            f.write_text("x")
            if is_old:
                os.utime(f, (old, old))

    def test_removes_only_old_generated_files(self):
        n = cleanup_outputs(7, self.dir)
        self.assertEqual(n, 3)
        remaining = {str(p.relative_to(self.dir))
                     for p in self.dir.rglob("*") if p.is_file()}
        self.assertEqual(remaining, {"stock_scan_new.csv", "research/NEW.html",
                                     "portfolio.csv", "scan_history.csv"})

    def test_zero_days_removes_all_generated(self):
        n = cleanup_outputs(0, self.dir)
        self.assertEqual(n, 5)   # every generated file, old and new

    def test_empty_dir_is_fine(self):
        empty = Path(tempfile.mkdtemp())
        self.assertEqual(cleanup_outputs(7, empty), 0)


if __name__ == "__main__":
    unittest.main()
