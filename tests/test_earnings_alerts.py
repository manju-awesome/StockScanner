"""
Tests for core.earnings_alerts — the market-cap/expected-move gate and the
T-5/T-1/T-0 tier lifecycle. All network calls (fetch_earnings_history,
analyze_earnings_sentiment, fetch_analyst_snapshot, market cap) are
monkeypatched; nothing here touches yfinance or the real data files.
Run with: python -m unittest tests.test_earnings_alerts
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import alerts
from stockanalysis.core import earnings_alerts as ea


class TestGate(unittest.TestCase):
    def test_small_cap_is_rejected_regardless_of_move_size(self):
        self.assertFalse(ea._passes_gate(500_000_000, 20.0, 20.0))

    def test_none_market_cap_is_rejected(self):
        self.assertFalse(ea._passes_gate(None, 20.0, 20.0))

    def test_large_cap_with_no_move_data_is_rejected(self):
        self.assertFalse(ea._passes_gate(5_000_000_000, None, None))

    def test_large_cap_with_big_expected_move_passes(self):
        self.assertTrue(ea._passes_gate(5_000_000_000, 7.0, None))

    def test_large_cap_with_big_historical_move_passes(self):
        self.assertTrue(ea._passes_gate(5_000_000_000, None, 8.0))

    def test_large_cap_with_small_moves_is_rejected(self):
        self.assertFalse(ea._passes_gate(5_000_000_000, 2.0, 3.0))

    def test_boundary_values_are_exclusive_gate_at_threshold(self):
        # exactly at MIN_MARKET_CAP should fail (gate uses > not >=)
        self.assertFalse(ea._passes_gate(ea.MIN_MARKET_CAP, 10.0, 10.0))
        self.assertTrue(ea._passes_gate(ea.MIN_MARKET_CAP + 1, ea.MIN_EXPECTED_MOVE_PCT, None))


class ScanEarningsTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_state = alerts.ALERTS_STATE_PATH
        self._orig_log = alerts.ALERTS_LOG_PATH
        self._orig_watchlists = alerts.WATCHLISTS_PATH
        self._orig_send = alerts.send_alert_emails
        self._orig_market_cap = ea._market_cap
        tmp_dir = Path(tempfile.mkdtemp())
        alerts.ALERTS_STATE_PATH = tmp_dir / "alerts_state.json"
        alerts.ALERTS_LOG_PATH = tmp_dir / "alerts_log.json"
        alerts.WATCHLISTS_PATH = tmp_dir / "watchlists.json"
        alerts.send_alert_emails = lambda alerts_list: False

        import stockanalysis.core.earnings_sentiment as es
        self._es = es
        self._orig_hist = es.fetch_earnings_history
        self._orig_sentiment = es.analyze_earnings_sentiment
        self._orig_analyst = es.fetch_analyst_snapshot

    def tearDown(self):
        alerts.ALERTS_STATE_PATH = self._orig_state
        alerts.ALERTS_LOG_PATH = self._orig_log
        alerts.WATCHLISTS_PATH = self._orig_watchlists
        alerts.send_alert_emails = self._orig_send
        ea._market_cap = self._orig_market_cap
        self._es.fetch_earnings_history = self._orig_hist
        self._es.analyze_earnings_sentiment = self._orig_sentiment
        self._es.fetch_analyst_snapshot = self._orig_analyst

    def _mock(self, hist_by_ticker: dict, market_cap_by_ticker: dict,
             sentiment: dict | None = None, analyst: dict | None = None):
        default_hist = {"days_to_earnings": None, "avg_abs_move_pct": None,
                       "next_earnings_date": None, "consensus_eps": None,
                       "beat_rate": None, "streak": None}
        self._es.fetch_earnings_history = lambda t: {**default_hist, **hist_by_ticker.get(t, {})}
        ea._market_cap = lambda t: market_cap_by_ticker.get(t)
        self._es.analyze_earnings_sentiment = lambda t: (sentiment or {
            "expected_move_pct": 7.0, "trading_bias": "Bullish",
            "risk_factors": ["elevated IV"], "confidence": 6.0,
        })
        self._es.fetch_analyst_snapshot = lambda t: (analyst or {"net_actions": 1})


class TestScanEarningsForAlerts(ScanEarningsTestCase):
    def test_large_cap_reporting_today_fires_t0(self):
        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 0, "avg_abs_move_pct": 8.5}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
        )
        new = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["dedup_key"], "NVDA:earnings_T-0")
        self.assertEqual(new[0]["priority"], "HIGH")
        self.assertIn("today", new[0]["headline"])

    def test_large_cap_reporting_tomorrow_fires_t1(self):
        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 1, "avg_abs_move_pct": 8.5}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
        )
        new = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(new[0]["dedup_key"], "NVDA:earnings_T-1")
        self.assertIn("tomorrow", new[0]["headline"])

    def test_small_cap_ticker_is_excluded(self):
        self._mock(
            hist_by_ticker={"TINY": {"days_to_earnings": 0, "avg_abs_move_pct": 12.0}},
            market_cap_by_ticker={"TINY": 500_000_000},
        )
        new = ea.scan_earnings_for_alerts(["TINY"])
        self.assertEqual(new, [])

    def test_ticker_outside_alert_window_is_excluded(self):
        self._mock(
            hist_by_ticker={"FARAWAY": {"days_to_earnings": 10, "avg_abs_move_pct": 9.0}},
            market_cap_by_ticker={"FARAWAY": 5_000_000_000},
        )
        new = ea.scan_earnings_for_alerts(["FARAWAY"])
        self.assertEqual(new, [])

    def test_five_days_out_fires_t5_as_medium(self):
        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 5, "avg_abs_move_pct": 8.5}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
        )
        new = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["dedup_key"], "NVDA:earnings_T-5")
        self.assertEqual(new[0]["priority"], "MEDIUM")
        self.assertIn("in 5 days", new[0]["headline"])

    def test_t5_fires_once_across_the_early_window(self):
        """One dedup key covers days 2-5: an alert raised at day 4 stays
        active and must not refire at day 3."""
        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 4, "avg_abs_move_pct": 8.5}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
        )
        day4 = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(len(day4), 1)
        self.assertEqual(day4[0]["dedup_key"], "NVDA:earnings_T-5")

        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 3, "avg_abs_move_pct": 8.5}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
        )
        day3 = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(day3, [])

    def test_full_lifecycle_t5_then_t1_then_t0(self):
        for days, expected_key in ((5, "NVDA:earnings_T-5"),
                                   (1, "NVDA:earnings_T-1"),
                                   (0, "NVDA:earnings_T-0")):
            self._mock(
                hist_by_ticker={"NVDA": {"days_to_earnings": days, "avg_abs_move_pct": 8.5}},
                market_cap_by_ticker={"NVDA": 3_000_000_000_000},
            )
            new = ea.scan_earnings_for_alerts(["NVDA"])
            self.assertEqual(len(new), 1)
            self.assertEqual(new[0]["dedup_key"], expected_key)

    def test_ticker_with_no_upcoming_earnings_is_excluded(self):
        self._mock(
            hist_by_ticker={"NOEARN": {"days_to_earnings": None}},
            market_cap_by_ticker={"NOEARN": 5_000_000_000},
        )
        new = ea.scan_earnings_for_alerts(["NOEARN"])
        self.assertEqual(new, [])

    def test_t1_and_t0_are_independent_dedup_keys_both_fire_across_two_days(self):
        """The same earnings event should still produce two separate
        notifications (T-1 heads-up, then T-0 day-of) since they're
        different dedup_keys — this is intentional, not a dedup bug."""
        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 1, "avg_abs_move_pct": 8.5}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
        )
        day1 = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(len(day1), 1)
        self.assertEqual(day1[0]["dedup_key"], "NVDA:earnings_T-1")

        # next day: same event, now T-0
        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 0, "avg_abs_move_pct": 8.5}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
        )
        day2 = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(len(day2), 1)
        self.assertEqual(day2[0]["dedup_key"], "NVDA:earnings_T-0")

    def test_repeated_scan_same_day_does_not_refire(self):
        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 0, "avg_abs_move_pct": 8.5}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
        )
        first = ea.scan_earnings_for_alerts(["NVDA"])
        second = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)

    def test_consensus_eps_and_analyst_revisions_surfaced_in_supporting_data(self):
        self._mock(
            hist_by_ticker={"NVDA": {"days_to_earnings": 0, "avg_abs_move_pct": 8.5, "consensus_eps": 1.05}},
            market_cap_by_ticker={"NVDA": 3_000_000_000_000},
            analyst={"net_actions": 3},
        )
        new = ea.scan_earnings_for_alerts(["NVDA"])
        self.assertEqual(new[0]["supporting_data"]["consensus_eps"], 1.05)
        self.assertEqual(new[0]["supporting_data"]["analyst_net_actions"], 3)


if __name__ == "__main__":
    unittest.main()
