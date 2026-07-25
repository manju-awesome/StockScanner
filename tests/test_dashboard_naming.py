"""
Tests for dashboard.py's report_name -> filename behavior — offline, no
network (include_market_pulse=False, empty rows so no per-ticker rendering
paths that would need real scan data).
Run with: python -m unittest tests.test_dashboard_naming
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.reporting.dashboard import _safe_report_name, generate_dashboard


class TestSafeReportName(unittest.TestCase):
    def test_alnum_passes_through(self):
        self.assertEqual(_safe_report_name("sp500Report"), "sp500Report")

    def test_strips_punctuation_and_spaces(self):
        self.assertEqual(_safe_report_name("day trade!/Report"), "daytradeReport")

    def test_empty_or_punctuation_only_falls_back(self):
        self.assertEqual(_safe_report_name(""), "dashboard")
        self.assertEqual(_safe_report_name("../../"), "dashboard")


class TestGenerateDashboardFilename(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_default_prefix_is_dashboard(self):
        path = Path(generate_dashboard(
            [], output_dir=self.tmp, open_browser=False,
            include_market_pulse=False, include_portfolio=False))
        self.assertTrue(path.name.startswith("dashboard_"))
        self.assertTrue(path.name.endswith(".html"))

    def test_report_name_becomes_filename_prefix(self):
        path = Path(generate_dashboard(
            [], output_dir=self.tmp, open_browser=False,
            include_market_pulse=False, include_portfolio=False,
            report_name="sp500Report"))
        self.assertTrue(path.name.startswith("sp500Report_"))

    def test_timestamp_includes_seconds(self):
        path = Path(generate_dashboard(
            [], output_dir=self.tmp, open_browser=False,
            include_market_pulse=False, include_portfolio=False,
            report_name="daytradeReport"))
        # daytradeReport_YYYYMMDD_HHMMSS.html
        stem = path.stem  # strips .html
        ts_part = stem.split("_", 1)[1]  # "YYYYMMDD_HHMMSS"
        date_part, time_part = ts_part.split("_")
        self.assertEqual(len(date_part), 8)
        self.assertEqual(len(time_part), 6)   # HHMMSS, not just HHMM

    def test_unsafe_report_name_sanitised(self):
        path = Path(generate_dashboard(
            [], output_dir=self.tmp, open_browser=False,
            include_market_pulse=False, include_portfolio=False,
            report_name="../../etc/Report"))
        self.assertTrue(path.name.startswith("etcReport_"))
        # stayed inside output_dir (resolve() to normalize macOS's
        # /var -> /private/var symlink before comparing)
        self.assertEqual(path.parent.resolve(), self.tmp.resolve())


if __name__ == "__main__":
    unittest.main()
