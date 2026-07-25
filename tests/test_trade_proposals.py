"""
Tests for core.trade_proposals — the bridge from an active a_plus_setup
alert to a persisted, human-reviewed trade proposal. All storage goes
through temp files (module paths monkeypatched); nothing here places an
order or touches the network.
Run with: python -m unittest tests.test_trade_proposals
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import alerts
from stockanalysis.core import trade_proposals as tp


def _aplus_alert(ticker="NVDA", entry=100.0, stop=95.0, target=115.0):
    return alerts.make_alert(
        dedup_key=f"{ticker}:a_plus_setup", category="a_plus_setup", priority="HIGH",
        ticker=ticker, headline=f"A+ swing setup ({ticker})",
        why_it_matters="Scored in the top band.",
        expected_impact="Highest-conviction tier.",
        suggested_action="Review before acting.",
        confidence=85, time_sensitivity="today",
        supporting_data={"strategies": ["swing (rank 96)"], "category": "VCP Setup",
                         "entry": entry, "stop": stop, "target": target,
                         "grade": "A+", "price": 101.5})


class TradeProposalsTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_state = alerts.ALERTS_STATE_PATH
        self._orig_log = alerts.ALERTS_LOG_PATH
        self._orig_watchlists = alerts.WATCHLISTS_PATH
        self._orig_proposals = tp.TRADE_PROPOSALS_PATH
        tmp_dir = Path(tempfile.mkdtemp())
        alerts.ALERTS_STATE_PATH = tmp_dir / "alerts_state.json"
        alerts.ALERTS_LOG_PATH = tmp_dir / "alerts_log.json"
        alerts.WATCHLISTS_PATH = tmp_dir / "watchlists.json"
        tp.TRADE_PROPOSALS_PATH = tmp_dir / "trade_proposals.json"

    def tearDown(self):
        alerts.ALERTS_STATE_PATH = self._orig_state
        alerts.ALERTS_LOG_PATH = self._orig_log
        alerts.WATCHLISTS_PATH = self._orig_watchlists
        tp.TRADE_PROPOSALS_PATH = self._orig_proposals


class TestSyncFromAlerts(TradeProposalsTestCase):
    def test_creates_pending_proposal_from_active_alert(self):
        alerts.raise_alerts([_aplus_alert()], {"NVDA:a_plus_setup"})
        new = tp.sync_from_alerts()
        self.assertEqual(len(new), 1)
        p = tp.list_proposals()[0]
        self.assertEqual(p["ticker"], "NVDA")
        self.assertEqual(p["status"], "pending_review")
        self.assertEqual(p["entry"], 100.0)
        self.assertEqual(p["stop"], 95.0)
        self.assertEqual(p["target"], 115.0)
        self.assertEqual(p["risk_per_share"], 5.0)
        self.assertEqual(p["reward_risk"], 3.0)

    def test_does_not_duplicate_already_tracked_proposal(self):
        alerts.raise_alerts([_aplus_alert()], {"NVDA:a_plus_setup"})
        tp.sync_from_alerts()
        tp.set_status("NVDA", "approved")
        # alert still active next cycle — re-raising is a no-op since dedup
        # already holds it, but sync should leave the approved proposal alone
        tp.sync_from_alerts()
        proposals = tp.list_proposals(status=None)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["status"], "approved")

    def test_expires_pending_proposal_when_alert_resolves(self):
        alerts.raise_alerts([_aplus_alert()], {"NVDA:a_plus_setup"})
        tp.sync_from_alerts()
        # condition no longer true this cycle -> reconcile clears the alert
        alerts.raise_alerts([], {"NVDA:a_plus_setup"})
        tp.sync_from_alerts()
        proposals = tp.list_proposals(status=None)
        self.assertEqual(proposals[0]["status"], "expired")

    def test_does_not_expire_already_approved_proposal(self):
        alerts.raise_alerts([_aplus_alert()], {"NVDA:a_plus_setup"})
        tp.sync_from_alerts()
        tp.set_status("NVDA", "approved")
        alerts.raise_alerts([], {"NVDA:a_plus_setup"})
        tp.sync_from_alerts()
        self.assertEqual(tp.list_proposals(status="approved")[0]["ticker"], "NVDA")


class TestSuggestedShares(unittest.TestCase):
    def test_sizes_to_risk_budget(self):
        # $10,000 equity, 1% risk = $100 budget, $5 risk/share -> 20 shares
        self.assertEqual(tp.suggested_shares(100.0, 95.0, 10_000), 20)

    def test_zero_on_invalid_inputs(self):
        self.assertEqual(tp.suggested_shares(100.0, 100.0, 10_000), 0)
        self.assertEqual(tp.suggested_shares(100.0, 95.0, 0), 0)


class TestSetStatus(TradeProposalsTestCase):
    def test_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            tp.set_status("NVDA", "yolo")

    def test_returns_none_for_unknown_ticker(self):
        self.assertIsNone(tp.set_status("ZZZZ", "approved"))


if __name__ == "__main__":
    unittest.main()
