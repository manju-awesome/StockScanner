"""
A research-library refresh must raise the same high-conviction option
alerts a scan does — scan_universe has always hooked them, refresh_research
had not, so a setup first seen by a library refresh (including the
pre/post-market session scans) went unreported until the next full scan.
Run with: python -m unittest tests.test_research_option_alerts
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.reporting import research as R


class TestRefreshResearchRaisesOptionAlerts(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.rows = [{"Ticker": "WOLF", "Current Price": 10.0, "Put_Score": 9},
                     {"Ticker": "BE", "Current Price": 20.0, "Call_Score": 9}]

    def _run(self, seen):
        """refresh_research with the network/pipeline stubbed out — this is
        about the alert hook, not about metrics."""
        def fake_alerts(rows):
            seen.extend(r.get("Ticker") for r in rows)
            return []

        with mock.patch("stockanalysis.scanners.scan_universe.fetch_qqq_return",
                        return_value=0.0), \
             mock.patch("stockanalysis.core.metrics.get_metrics",
                        side_effect=lambda t, q: dict(
                            next(r for r in self.rows if r["Ticker"] == t))), \
             mock.patch("stockanalysis.scanners.scan_universe.categorize",
                        return_value=("Momentum", "", 0)), \
             mock.patch("stockanalysis.core.grade_signals.enrich_rows"), \
             mock.patch("stockanalysis.core.strategy_scores.attach_strategy_scores"), \
             mock.patch("stockanalysis.core.conviction.attach_conviction"), \
             mock.patch("stockanalysis.core.watchlist_alerts.scan_rows_for_option_alerts",
                        side_effect=fake_alerts), \
             mock.patch.object(R, "generate_research_pages", return_value={"WOLF", "BE"}):
            return R.refresh_research(["WOLF", "BE"], output_dir=self.out,
                                      charts=False, fetch_news=False)

    def test_hook_receives_the_refreshed_rows(self):
        seen = []
        self._run(seen)
        self.assertEqual(sorted(seen), ["BE", "WOLF"])

    def test_alert_failure_does_not_lose_the_refresh(self):
        """An alerting error must never cost the caller its pages."""
        with mock.patch("stockanalysis.scanners.scan_universe.fetch_qqq_return",
                        return_value=0.0), \
             mock.patch("stockanalysis.core.metrics.get_metrics",
                        side_effect=lambda t, q: dict(
                            next(r for r in self.rows if r["Ticker"] == t))), \
             mock.patch("stockanalysis.scanners.scan_universe.categorize",
                        return_value=("Momentum", "", 0)), \
             mock.patch("stockanalysis.core.grade_signals.enrich_rows"), \
             mock.patch("stockanalysis.core.strategy_scores.attach_strategy_scores"), \
             mock.patch("stockanalysis.core.conviction.attach_conviction"), \
             mock.patch("stockanalysis.core.watchlist_alerts.scan_rows_for_option_alerts",
                        side_effect=RuntimeError("alert backend down")), \
             mock.patch.object(R, "generate_research_pages", return_value={"WOLF", "BE"}):
            written = R.refresh_research(["WOLF", "BE"], output_dir=self.out,
                                         charts=False, fetch_news=False)
        self.assertEqual(written, {"WOLF", "BE"})


class TestThresholdIsCritical(unittest.TestCase):
    def test_module_constants(self):
        from stockanalysis.core import watchlist_alerts as wa
        self.assertEqual(wa.PUT_SCORE_ALERT_MIN, 9)
        self.assertEqual(wa.CALL_SCORE_ALERT_MIN, 9)

    def test_both_rules_emit_critical(self):
        from stockanalysis.core import watchlist_alerts as wa
        put = wa._put_score_high({"Ticker": "X", "Put_Score": 9})
        call = wa._call_score_high({"Ticker": "Y", "Call_Score": 10})
        self.assertEqual(put["priority"], "CRITICAL")
        self.assertEqual(call["priority"], "CRITICAL")

    def test_eight_is_below_the_bar(self):
        from stockanalysis.core import watchlist_alerts as wa
        self.assertIsNone(wa._put_score_high({"Ticker": "X", "Put_Score": 8}))
        self.assertIsNone(wa._call_score_high({"Ticker": "Y", "Call_Score": 8}))

    def test_critical_is_an_email_tier(self):
        from stockanalysis.core import alerts
        self.assertIn("CRITICAL", alerts.EMAIL_PRIORITIES)
        # and outranks HIGH in the digest ordering
        self.assertLess(alerts.priority_rank("CRITICAL"),
                        alerts.priority_rank("HIGH"))


if __name__ == "__main__":
    unittest.main()
