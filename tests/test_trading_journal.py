"""
Tests for core.trading_journal — form parsing, R-multiple/return math, and
the aggregate analytics functions. All storage goes through a temp file
(module.JOURNAL_PATH is monkeypatched) so nothing touches the real
data/journal_trades.json. The AI coach call itself is not exercised here
(no network) beyond confirming it fails closed without ANTHROPIC_API_KEY.
Run with: python -m unittest tests.test_trading_journal
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import trading_journal as journal

LONG_FORM = {
    "ticker": ["nvda"], "direction": ["Long"], "date": ["2026-07-10"], "time": ["09:45"],
    "plan_setup_name": ["EMA Pullback"], "plan_htf_trend": ["uptrend"],
    "plan_entry": ["120"], "plan_stop": ["115"], "plan_target1": ["135"],
    "plan_risk_pct": ["1.0"],
    "exec_actual_entry": ["121"], "exec_actual_exit": ["133"],
    "exec_duration_minutes": ["90"], "exec_deviated_from_plan": ["on"],
    "psych_stress": ["3"], "psych_confidence": ["8"],
}

SHORT_LOSS_FORM = {
    "ticker": ["tsla"], "direction": ["Short"], "date": ["2026-07-11"], "time": ["10:15"],
    "plan_setup_name": ["Breakdown"],
    "plan_entry": ["250"], "plan_stop": ["255"], "plan_target1": ["235"],
    "exec_actual_entry": ["249"], "exec_actual_exit": ["258"],
    "exec_duration_minutes": ["30"], "exec_fomo": ["on"], "chk_impulsive": ["on"],
    "psych_stress": ["8"], "psych_confidence": ["3"],
}


class JournalStorageTestCase(unittest.TestCase):
    """Redirects journal.JOURNAL_PATH to a scratch file for the duration of
    each test so trades never land in the real project data directory."""

    def setUp(self):
        self._orig_path = journal.JOURNAL_PATH
        tmp_dir = Path(tempfile.mkdtemp())
        journal.JOURNAL_PATH = tmp_dir / "journal_trades.json"

    def tearDown(self):
        journal.JOURNAL_PATH = self._orig_path


class TestNewTradeFromForm(JournalStorageTestCase):
    def test_long_winner_computes_r_multiple_and_return(self):
        trade = journal.add_trade(LONG_FORM)
        self.assertEqual(trade["ticker"], "NVDA")
        result = trade["trade_result"]
        self.assertEqual(result["r_multiple"], 2.0)     # (133-121) / (121-115)... risk vs stop
        self.assertAlmostEqual(result["return_pct"], 9.92, places=2)
        self.assertEqual(trade["trade_plan"]["risk_reward"], 3.0)  # (135-120)/(120-115)

    def test_short_loser_sign_is_correct(self):
        trade = journal.add_trade(SHORT_LOSS_FORM)
        result = trade["trade_result"]
        # short: entered 249, exited 258 (price rose against the short) -> a loss
        self.assertLess(result["r_multiple"], 0)
        self.assertLess(result["pnl_per_share"], 0)

    def test_unchecked_checkboxes_default_false(self):
        trade = journal.add_trade(LONG_FORM)
        self.assertFalse(trade["execution"]["fomo"])
        self.assertTrue(trade["execution"]["deviated_from_plan"])

    def test_missing_numeric_fields_are_none_not_error(self):
        trade = journal.add_trade({"ticker": ["AAPL"]})
        self.assertIsNone(trade["trade_plan"]["entry"])
        self.assertIsNone(trade["trade_result"]["r_multiple"])


class TestPersistence(JournalStorageTestCase):
    def test_add_get_update_delete_roundtrip(self):
        trade = journal.add_trade(LONG_FORM)
        self.assertEqual(journal.get_trade(trade["id"])["ticker"], "NVDA")

        journal.update_trade(trade["id"], {"ai_feedback": {"overall_grade": "A"}})
        self.assertEqual(journal.get_trade(trade["id"])["ai_feedback"]["overall_grade"], "A")

        self.assertTrue(journal.delete_trade(trade["id"]))
        self.assertIsNone(journal.get_trade(trade["id"]))
        self.assertFalse(journal.delete_trade(trade["id"]))  # already gone


class TestEditExistingTrade(JournalStorageTestCase):
    def test_partial_trade_can_be_completed_later(self):
        """The common case this feature exists for: log just the plan while
        the trade is still open, then come back after it closes and fill in
        execution/psychology/lessons via the Edit button."""
        partial = journal.add_trade({
            "ticker": ["NVDA"], "direction": ["Long"],
            "plan_setup_name": ["EMA Pullback"], "plan_entry": ["120"], "plan_stop": ["115"],
        })
        self.assertIsNone(partial["trade_result"]["r_multiple"])  # no exit yet

        completed_form = {
            "ticker": ["NVDA"], "direction": ["Long"],
            "plan_setup_name": ["EMA Pullback"], "plan_entry": ["120"], "plan_stop": ["115"],
            "exec_actual_entry": ["121"], "exec_actual_exit": ["133"],
            "rev_worked": ["stuck to the plan"],
        }
        updated = journal.update_trade_from_form(partial["id"], completed_form)
        self.assertEqual(updated["id"], partial["id"])           # same record, not a duplicate
        self.assertEqual(updated["trade_result"]["r_multiple"], 2.0)
        self.assertEqual(updated["post_trade_review"]["what_worked"], "stuck to the plan")
        self.assertEqual(len(journal.load_trades()), 1)          # still just one trade

    def test_update_preserves_identity_and_existing_ai_feedback(self):
        trade = journal.add_trade(LONG_FORM)
        journal.update_trade(trade["id"], {"ai_feedback": {"overall_grade": "B"}})
        updated = journal.update_trade_from_form(trade["id"], {**LONG_FORM, "sector": ["Semis"]})
        self.assertEqual(updated["created_at"], trade["created_at"])
        self.assertEqual(updated["ai_feedback"]["overall_grade"], "B")
        self.assertEqual(updated["sector"], "Semis")

    def test_update_unknown_trade_id_returns_none(self):
        self.assertIsNone(journal.update_trade_from_form("does-not-exist", LONG_FORM))

    def test_trade_to_form_dict_round_trips_through_update(self):
        """Guards the Edit button's prefill mechanism: flattening a trade and
        resubmitting it unchanged should reproduce the same record."""
        trade = journal.add_trade(LONG_FORM)
        flat = journal.trade_to_form_dict(trade)
        self.assertEqual(flat["trade_id"], trade["id"])
        # simulate the browser re-submitting the prefilled form as-is
        as_form = {k: [str(v) if v is not None else ""] for k, v in flat.items() if k != "trade_id"}
        for bool_key in ("exec_deviated_from_plan",):
            if flat.get(bool_key):
                as_form[bool_key] = ["on"]
            else:
                as_form.pop(bool_key, None)
        updated = journal.update_trade_from_form(trade["id"], as_form)
        self.assertEqual(updated["trade_plan"]["setup_name"], "EMA Pullback")
        self.assertEqual(updated["trade_result"]["r_multiple"], 2.0)


class TestAggregateMetrics(JournalStorageTestCase):
    def setUp(self):
        super().setUp()
        journal.add_trade(LONG_FORM)       # winner, +2.0R
        journal.add_trade(SHORT_LOSS_FORM)  # loser
        self.trades = journal.load_trades()

    def test_win_rate_and_avg_r(self):
        m = journal.aggregate_metrics(self.trades)
        self.assertEqual(m["count"], 2)
        self.assertEqual(m["win_rate"], 50.0)
        self.assertGreater(m["avg_r"], 0)  # winner (+2.0R) outweighs the small loss

    def test_empty_trade_list_reports_zero_count(self):
        self.assertEqual(journal.aggregate_metrics([]), {"count": 0})

    def test_setup_and_time_breakdowns_cover_both_trades(self):
        setups = {b["key"] for b in journal.setup_performance(self.trades)}
        self.assertEqual(setups, {"EMA Pullback", "Breakdown"})
        tod = {b["key"] for b in journal.time_of_day_performance(self.trades)}
        self.assertEqual(tod, {"09:00", "10:00"})

    def test_deviated_from_plan_flag_not_confused_with_followed_plan(self):
        """Regression test: deviated_from_plan=True on the WINNING trade must
        show up as the violation "present" on that winner, not be silently
        inverted into looking like a violation on the loser."""
        violations = {v["violation"]: v for v in journal.rule_violation_stats(self.trades)}
        self.assertIn("Deviated from plan", violations)
        # the winner (LONG_FORM) is the one with the flag checked
        self.assertGreater(violations["Deviated from plan"]["avg_r_when_present"], 0)

    def test_rule_violation_stats_track_fomo_on_the_loser(self):
        violations = {v["violation"]: v for v in journal.rule_violation_stats(self.trades)}
        self.assertIn("FOMO", violations)
        self.assertLess(violations["FOMO"]["avg_r_when_present"], 0)

    def test_emotion_correlation_returns_a_row_per_scored_dimension(self):
        rows = journal.emotion_correlation(self.trades)
        dims = {r["dimension"] for r in rows}
        self.assertIn("stress", dims)
        self.assertIn("confidence", dims)


class TestCoachPromptAndReview(JournalStorageTestCase):
    def test_build_coach_prompt_includes_trade_facts(self):
        trade = journal.add_trade(LONG_FORM)
        prompt = journal.build_coach_prompt(trade)
        self.assertIn("NVDA", prompt)
        self.assertIn("Return JSON only", prompt)

    def test_run_ai_coach_review_fails_closed_without_api_key(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        trade = journal.add_trade(LONG_FORM)
        with self.assertRaises(RuntimeError):
            journal.run_ai_coach_review(trade)


if __name__ == "__main__":
    unittest.main()
