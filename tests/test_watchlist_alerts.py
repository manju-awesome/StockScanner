"""
Tests for core.watchlist_alerts — each condition function against synthetic
scan rows, plus the full scan_rows_for_alerts dedup lifecycle (MACD's
network fetch is monkeypatched out). No real yfinance calls anywhere here.
Run with: python -m unittest tests.test_watchlist_alerts
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import alerts
from stockanalysis.core import trade_proposals as tp
from stockanalysis.core import watchlist_alerts as wa

FLAT_ROW = {
    "Ticker": "MSFT", "RSI_14": 50, "Dist_to_Support%": 10, "Dist_to_Resistance%": 10,
    "Gap_Now%": 0.1, "RVOL": 0.9, "Price_vs_50MA%": -1,
}


class TestConditions(unittest.TestCase):
    def test_support_touch_fires_within_threshold(self):
        row = {**FLAT_ROW, "Dist_to_Support%": 1.0}
        self.assertIsNotNone(wa._support_touch(row))

    def test_support_touch_ignores_far_away(self):
        row = {**FLAT_ROW, "Dist_to_Support%": 8.0}
        self.assertIsNone(wa._support_touch(row))

    def test_breakdown_fires_when_below_support(self):
        row = {**FLAT_ROW, "Dist_to_Support%": -0.3}
        alert = wa._breakdown(row)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["priority"], "HIGH")

    def test_breakout_fires_when_above_resistance(self):
        row = {**FLAT_ROW, "Dist_to_Resistance%": -0.3, "Breakout_Probability": 80}
        alert = wa._breakout(row)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["confidence"], 80)

    def test_resistance_touch_does_not_fire_after_breakout(self):
        """Once price is through resistance (negative distance), that's a
        breakout, not a "touch" — the two conditions should be mutually
        exclusive on the same row."""
        row = {**FLAT_ROW, "Dist_to_Resistance%": -0.3}
        self.assertIsNone(wa._resistance_touch(row))
        self.assertIsNotNone(wa._breakout(row))

    def test_gap_requires_threshold(self):
        self.assertIsNone(wa._gap({**FLAT_ROW, "Gap_Now%": 1.5}))
        self.assertIsNotNone(wa._gap({**FLAT_ROW, "Gap_Now%": 3.5}))
        self.assertIsNotNone(wa._gap({**FLAT_ROW, "Gap_Now%": -4.0}))  # gap down too

    def test_volume_spike_requires_2x(self):
        self.assertIsNone(wa._volume_spike({**FLAT_ROW, "RVOL": 1.5}))
        self.assertIsNotNone(wa._volume_spike({**FLAT_ROW, "RVOL": 2.5}))

    def test_rsi_oversold_and_overbought_are_mutually_exclusive(self):
        oversold = {**FLAT_ROW, "RSI_14": 25}
        overbought = {**FLAT_ROW, "RSI_14": 75}
        self.assertIsNotNone(wa._rsi_oversold(oversold))
        self.assertIsNone(wa._rsi_overbought(oversold))
        self.assertIsNotNone(wa._rsi_overbought(overbought))
        self.assertIsNone(wa._rsi_oversold(overbought))

    def test_ema_crossover_proxy_requires_positive_pct(self):
        self.assertIsNone(wa._ema_crossover({**FLAT_ROW, "Price_vs_50MA%": -2}))
        self.assertIsNotNone(wa._ema_crossover({**FLAT_ROW, "Price_vs_50MA%": 3}))

    def test_52w_high_fires_at_or_above_the_high(self):
        self.assertIsNone(wa._52w_high({**FLAT_ROW, "Dist_52W_High%": -2.0}))
        self.assertIsNotNone(wa._52w_high({**FLAT_ROW, "Dist_52W_High%": 0.0}))
        self.assertIsNotNone(wa._52w_high({**FLAT_ROW, "Dist_52W_High%": 1.5}))

    def test_52w_low_fires_at_or_below_the_low(self):
        self.assertIsNone(wa._52w_low({**FLAT_ROW, "Pct_From_52W_Low%": 2.0}))
        self.assertIsNotNone(wa._52w_low({**FLAT_ROW, "Pct_From_52W_Low%": 0.0}))
        self.assertIsNotNone(wa._52w_low({**FLAT_ROW, "Pct_From_52W_Low%": -1.0}))

    def test_missing_fields_do_not_raise(self):
        bare_row = {"Ticker": "XYZ"}
        for condition in wa._CONDITIONS:
            if condition is wa._macd_crossover:
                continue
            self.assertIsNone(condition(bare_row))

    def test_flat_row_fires_nothing(self):
        for condition in wa._CONDITIONS:
            if condition is wa._macd_crossover:
                continue
            self.assertIsNone(condition(FLAT_ROW))

    def test_headline_does_not_repeat_the_ticker(self):
        """Regression test: every display site (Alerts card, log table,
        email digest, Pre-Market Brief) prepends a["ticker"] in front of
        a["headline"] — if headline also started with the ticker, every
        rendering would read e.g. "PWR PWR MACD is bearish"."""
        row = {**FLAT_ROW, "Ticker": "PWR", "RSI_14": 25, "Gap_Now%": 5.0,
              "Dist_to_Resistance%": -0.5, "Dist_to_Support%": -0.5, "RVOL": 3.0,
              "Price_vs_50MA%": 3.0}
        for condition in wa._CONDITIONS:
            if condition is wa._macd_crossover:
                continue
            alert = condition(row)
            if alert is not None:
                self.assertFalse(alert["headline"].startswith("PWR"),
                                 f"{condition.__name__} headline repeats the ticker: {alert['headline']!r}")


class TestDefaultAlertTickers(unittest.TestCase):
    def test_uses_only_the_watchlist_named_list(self):
        """Regression test: watchlists.json also holds broad reference
        universes (sp500, whole GICS sectors) for the Scanner/Research
        pages. The alert monitor must scan only the "watchlist" list, not
        every saved list, or a 10-minute scan silently balloons to 500+
        tickers."""
        import stockanalysis.reporting.research as research

        orig = research.load_watchlists
        research.load_watchlists = lambda: {
            "watchlist": ["NVDA", "AMD"],
            "sp500": [f"T{i}" for i in range(500)],
            "Sector: Technology": [f"S{i}" for i in range(100)],
        }
        try:
            tickers = wa.default_alert_tickers()
            self.assertEqual(tickers, ["NVDA", "AMD"])
        finally:
            research.load_watchlists = orig

    def test_missing_watchlist_key_returns_empty(self):
        import stockanalysis.reporting.research as research
        orig = research.load_watchlists
        research.load_watchlists = lambda: {"sp500": ["A", "B"]}
        try:
            self.assertEqual(wa.default_alert_tickers(), [])
        finally:
            research.load_watchlists = orig


class ScanRowsForAlertsTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_state = alerts.ALERTS_STATE_PATH
        self._orig_log = alerts.ALERTS_LOG_PATH
        self._orig_watchlists = alerts.WATCHLISTS_PATH
        self._orig_proposals = tp.TRADE_PROPOSALS_PATH
        self._orig_macd = wa._macd_state
        self._orig_send = alerts.send_alert_emails
        tmp_dir = Path(tempfile.mkdtemp())
        alerts.ALERTS_STATE_PATH = tmp_dir / "alerts_state.json"
        alerts.ALERTS_LOG_PATH = tmp_dir / "alerts_log.json"
        alerts.WATCHLISTS_PATH = tmp_dir / "watchlists.json"
        tp.TRADE_PROPOSALS_PATH = tmp_dir / "trade_proposals.json"
        wa._macd_state = lambda ticker: None  # no network in these tests
        alerts.send_alert_emails = lambda alerts_list: False

    def tearDown(self):
        alerts.ALERTS_STATE_PATH = self._orig_state
        alerts.ALERTS_LOG_PATH = self._orig_log
        alerts.WATCHLISTS_PATH = self._orig_watchlists
        tp.TRADE_PROPOSALS_PATH = self._orig_proposals
        wa._macd_state = self._orig_macd
        alerts.send_alert_emails = self._orig_send


class TestPutScoreAlerts(ScanRowsForAlertsTestCase):
    def _watchlist(self):
        import json
        try:
            return json.loads(alerts.WATCHLISTS_PATH.read_text())
        except OSError:
            return {}

    def test_score_at_or_above_threshold_fires_critical_and_mirrors(self):
        rows = [{"Ticker": "WOLF", "Put_Score": 9,
                 "Put_Reason": "below 200MA; RSI weak; heavy distribution"}]
        fired = wa.scan_rows_for_put_alerts(rows)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["dedup_key"], "WOLF:put_score_high")
        self.assertEqual(fired[0]["priority"], "CRITICAL")  # top email tier
        self.assertIn("score 9", fired[0]["headline"])
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], ["WOLF"])

    def test_score_below_threshold_does_not_fire(self):
        # threshold is inclusive at 9, so 8 is the highest non-alerting score
        self.assertEqual(wa.scan_rows_for_put_alerts(
            [{"Ticker": "WOLF", "Put_Score": 8}]), [])

    def test_exactly_nine_fires(self):
        fired = wa.scan_rows_for_put_alerts([{"Ticker": "WOLF", "Put_Score": 9}])
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["priority"], "CRITICAL")

    def test_call_score_below_threshold_does_not_fire(self):
        self.assertEqual(wa.scan_rows_for_option_alerts(
            [{"Ticker": "BE", "Call_Score": 8}]), [])

    def test_fires_once_then_resolves_when_score_drops(self):
        hot = [{"Ticker": "WOLF", "Put_Score": 9}]
        self.assertEqual(len(wa.scan_rows_for_put_alerts(hot)), 1)
        self.assertEqual(wa.scan_rows_for_put_alerts(hot), [])       # deduped
        wa.scan_rows_for_put_alerts([{"Ticker": "WOLF", "Put_Score": 4}])
        self.assertEqual(alerts.load_active(), {})                   # resolved
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], [])

    def test_watchlist_monitor_also_carries_the_condition(self):
        """The same condition is in _CONDITIONS, so the 10-minute monitor
        tracks it with the same dedup key as the main-scan hook."""
        fired = wa.scan_rows_for_alerts([{"Ticker": "WOLF", "Put_Score": 9}])
        self.assertIn("WOLF:put_score_high", {a["dedup_key"] for a in fired})
        # main-scan hook then sees it as already active — no double email
        self.assertEqual(wa.scan_rows_for_option_alerts(
            [{"Ticker": "WOLF", "Put_Score": 9}]), [])

    def test_call_score_fires_critical_with_strike_hint_and_mirrors(self):
        rows = [{"Ticker": "BE", "Call_Score": 9, "Call_Strength": "STRONG",
                 "Call_Reason": "IV compressing; earnings beat; base forming",
                 "Call_Strike_Hint": "ATM 60d"}]
        fired = wa.scan_rows_for_option_alerts(rows)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["dedup_key"], "BE:call_score_high")
        self.assertEqual(fired[0]["priority"], "CRITICAL")
        self.assertIn("turnaround", fired[0]["headline"])
        self.assertEqual(fired[0]["supporting_data"]["call_strike_hint"], "ATM 60d")
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], ["BE"])

    def test_call_score_at_threshold_does_not_fire(self):
        self.assertEqual(wa.scan_rows_for_option_alerts(
            [{"Ticker": "BE", "Call_Score": 8}]), [])

    def _a_plus_swing_row(self, ticker="AMD"):
        # Momentum-Pullback 30 + RS>=50 +20 + BB<=0.1 +18 + ATR shrink +12
        # + pullback vol +10 + above 200MA +8 = 98 >= 95 (A+ swing band)
        return {"Ticker": ticker, "Entry_Gate_Pass": True,
                "Category": "Momentum-Pullback", "RS": 70.0, "RSI_14": 46,
                "BB_PctB": 0.05, "Pullback_Vol_Ratio": 0.5,
                "ATR Shrinking": True, "Above_200MA": True, "ATR_Pct": 5.0,
                "Entry": 499.08, "Stop": 464.14, "Target": 522.07, "Grade": "A"}

    def test_a_plus_swing_setup_fires_high_with_plan_levels(self):
        fired = wa.scan_rows_for_option_alerts([self._a_plus_swing_row()])
        self.assertEqual(len(fired), 1)
        a = fired[0]
        self.assertEqual(a["dedup_key"], "AMD:a_plus_setup")
        self.assertEqual(a["priority"], "HIGH")
        self.assertIn("A+ swing setup", a["headline"])
        self.assertEqual(a["supporting_data"]["entry"], 499.08)
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], ["AMD"])

    def test_a_plus_longterm_needs_pass_flag_and_score(self):
        base = {"Ticker": "MSFT", "Entry_Gate_Pass": True, "Category": "Longterm Hold",
                "Investment_Score": 85}
        # score alone isn't enough — all six primary filters must be green
        self.assertEqual(wa.scan_rows_for_option_alerts([dict(base)]), [])
        fired = wa.scan_rows_for_option_alerts([{**base, "Investment_Pass": True}])
        self.assertEqual(len(fired), 1)
        self.assertIn("long-term", fired[0]["headline"])

    def test_below_band_setup_does_not_fire(self):
        row = self._a_plus_swing_row()
        row["RS"] = -20.0        # drops swing rank far below 95
        self.assertEqual(wa.scan_rows_for_option_alerts([row]), [])

    def test_a_plus_resolves_when_setup_decays(self):
        wa.scan_rows_for_option_alerts([self._a_plus_swing_row()])
        decayed = self._a_plus_swing_row()
        decayed.update({"Category": "Avoid"})     # gate: not rankable any more
        wa.scan_rows_for_option_alerts([decayed])
        self.assertEqual(alerts.load_active(), {})
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], [])

    def test_52w_high_fires_high_and_mirrors_via_main_scan_hook(self):
        rows = [{"Ticker": "AMD", "Dist_52W_High%": 0.2, "52W High": 521.58,
                "Current Price": 522.58}]
        fired = wa.scan_rows_for_option_alerts(rows)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["dedup_key"], "AMD:52w_high")
        self.assertEqual(fired[0]["priority"], "HIGH")
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], ["AMD"])

    def test_52w_low_resolves_when_price_recovers(self):
        low_row = [{"Ticker": "WOLF", "Pct_From_52W_Low%": -0.5, "52W Low": 1.10}]
        self.assertEqual(len(wa.scan_rows_for_option_alerts(low_row)), 1)
        recovered = [{"Ticker": "WOLF", "Pct_From_52W_Low%": 15.0}]
        wa.scan_rows_for_option_alerts(recovered)
        self.assertEqual(alerts.load_active(), {})
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], [])

    def test_watchlist_monitor_also_carries_52w_conditions(self):
        """Same dedup key from either hook — the 10-minute monitor and every
        category/ad-hoc scan share one alert state, so whichever scan spots
        the fresh 52-week extreme first is the one that fires."""
        fired = wa.scan_rows_for_alerts([{"Ticker": "AMD", "Dist_52W_High%": 0.0}])
        self.assertIn("AMD:52w_high", {a["dedup_key"] for a in fired})
        self.assertEqual(wa.scan_rows_for_option_alerts(
            [{"Ticker": "AMD", "Dist_52W_High%": 0.0}]), [])

    def test_put_and_call_on_different_tickers_both_fire_and_resolve(self):
        rows = [{"Ticker": "WOLF", "Put_Score": 9}, {"Ticker": "BE", "Call_Score": 10}]
        fired = wa.scan_rows_for_option_alerts(rows)
        self.assertEqual({a["dedup_key"] for a in fired},
                         {"WOLF:put_score_high", "BE:call_score_high"})
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], ["BE", "WOLF"])
        # both cool off -> both resolve and drop out of ALERT_TICKERS
        wa.scan_rows_for_option_alerts(
            [{"Ticker": "WOLF", "Put_Score": 2}, {"Ticker": "BE", "Call_Score": 3}])
        self.assertEqual(alerts.load_active(), {})
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], [])


class TestScanRowsForAlerts(ScanRowsForAlertsTestCase):
    def test_new_alerts_fire_once_and_resolve(self):
        rows = [{"Ticker": "NVDA", "Dist_to_Resistance%": -0.5, "Breakout_Probability": 70},
               {"Ticker": "TSLA", "RSI_14": 25}]
        first = wa.scan_rows_for_alerts(rows)
        self.assertEqual({a["dedup_key"] for a in first}, {"NVDA:breakout", "TSLA:rsi_oversold"})

        second = wa.scan_rows_for_alerts(rows)
        self.assertEqual(second, [])  # still true, already notified

        resolved_rows = [{"Ticker": "NVDA", "Dist_to_Resistance%": 5},
                        {"Ticker": "TSLA", "RSI_14": 55}]
        third = wa.scan_rows_for_alerts(resolved_rows)
        self.assertEqual(third, [])
        self.assertEqual(alerts.load_active(), {})

        fourth = wa.scan_rows_for_alerts(rows)
        self.assertEqual({a["dedup_key"] for a in fourth}, {"NVDA:breakout", "TSLA:rsi_oversold"})

    def test_rows_missing_ticker_are_skipped(self):
        rows = [{"RSI_14": 20}]  # no "Ticker" key
        self.assertEqual(wa.scan_rows_for_alerts(rows), [])

    def test_multiple_conditions_on_same_ticker_all_fire(self):
        rows = [{"Ticker": "AMD", "RSI_14": 20, "Gap_Now%": 5.0}]
        fired = {a["dedup_key"] for a in wa.scan_rows_for_alerts(rows)}
        self.assertEqual(fired, {"AMD:rsi_oversold", "AMD:gap"})


if __name__ == "__main__":
    unittest.main()
