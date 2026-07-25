"""
Tests for core.news_monitor — the Breaking News monitor. All network calls
(_fetch_ticker_news) are monkeypatched to synthetic headlines; nothing
here touches yfinance or the real data files.
Run with: python -m unittest tests.test_news_monitor
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import alerts
from stockanalysis.core import news_monitor as nm


def _news_item(title, publisher="Reuters", when="2026-07-18", url="http://x"):
    return {"title": title, "publisher": publisher, "when": when, "url": url}


class NewsMonitorTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_state = alerts.ALERTS_STATE_PATH
        self._orig_log = alerts.ALERTS_LOG_PATH
        self._orig_watchlists = alerts.WATCHLISTS_PATH
        self._orig_send = alerts.send_alert_emails
        tmp_dir = Path(tempfile.mkdtemp())
        alerts.ALERTS_STATE_PATH = tmp_dir / "alerts_state.json"
        alerts.ALERTS_LOG_PATH = tmp_dir / "alerts_log.json"
        alerts.WATCHLISTS_PATH = tmp_dir / "watchlists.json"
        alerts.send_alert_emails = lambda alerts_list: False

        self._orig_fetch = None
        import stockanalysis.reporting.research as research
        self._research = research
        self._orig_fetch = research._fetch_ticker_news

    def tearDown(self):
        alerts.ALERTS_STATE_PATH = self._orig_state
        alerts.ALERTS_LOG_PATH = self._orig_log
        alerts.WATCHLISTS_PATH = self._orig_watchlists
        alerts.send_alert_emails = self._orig_send
        self._research._fetch_ticker_news = self._orig_fetch

    def _mock_news(self, mapping: dict):
        self._research._fetch_ticker_news = lambda ticker, limit=10: mapping.get(ticker, [])


class TestAlertWorthyFiltering(NewsMonitorTestCase):
    def test_high_impact_catalyst_fires(self):
        self._mock_news({"NVDA": [_news_item("NVDA announces acquisition of AI startup")]})
        new = nm.scan_news_for_alerts(["NVDA"])
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["priority"], "HIGH")
        self.assertIn("M&A / Acquisition", new[0]["headline"])

    def test_routine_news_is_ignored(self):
        self._mock_news({"MSFT": [_news_item("Company reports quarterly results in line with estimates")]})
        new = nm.scan_news_for_alerts(["MSFT"])
        self.assertEqual(new, [])

    def test_earnings_beat_miss_excluded_to_avoid_duplicate_alerting(self):
        """Regression test: Earnings Beat/Miss are deliberately excluded
        from ALERT_WORTHY_CATALYSTS — the dedicated Earnings Alert module
        already covers the earnings event with richer data."""
        self.assertNotIn("Earnings Beat", nm.ALERT_WORTHY_CATALYSTS)
        self.assertNotIn("Earnings Miss", nm.ALERT_WORTHY_CATALYSTS)
        self._mock_news({"AAPL": [_news_item("AAPL reports earnings beat on strong iPhone sales")]})
        new = nm.scan_news_for_alerts(["AAPL"])
        self.assertEqual(new, [])

    def test_analyst_upgrade_downgrade_excluded_as_too_routine(self):
        self._mock_news({"TSLA": [_news_item("Analyst downgrades TSLA to sell rating")]})
        new = nm.scan_news_for_alerts(["TSLA"])
        self.assertEqual(new, [])

    def test_missing_ticker_in_fetch_result_is_skipped_not_raised(self):
        self._mock_news({})  # ticker not in mapping at all
        new = nm.scan_news_for_alerts(["ZZZZ"])
        self.assertEqual(new, [])


class TestDedupLifecycle(NewsMonitorTestCase):
    def test_same_headline_does_not_refire(self):
        headline = "PWR announces CEO resignation amid controversy"
        self._mock_news({"PWR": [_news_item(headline)]})
        first = nm.scan_news_for_alerts(["PWR"])
        second = nm.scan_news_for_alerts(["PWR"])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)

    def test_headline_aging_out_of_window_clears_state(self):
        headline = "PWR announces CEO resignation amid controversy"
        self._mock_news({"PWR": [_news_item(headline)]})
        nm.scan_news_for_alerts(["PWR"])
        self.assertTrue(alerts.load_active())

        self._mock_news({"PWR": []})  # headline scrolled out of the recent-10 window
        nm.scan_news_for_alerts(["PWR"])
        self.assertEqual(alerts.load_active(), {})

    def test_two_different_headlines_both_fire(self):
        self._mock_news({"NVDA": [
            _news_item("NVDA announces acquisition of AI startup"),
            _news_item("NVDA CEO steps down unexpectedly"),
        ]})
        new = nm.scan_news_for_alerts(["NVDA"])
        self.assertEqual(len(new), 2)


if __name__ == "__main__":
    unittest.main()
