"""
Tests for core.alerts — the priority/dedup engine behind the Alerts feed.
All storage goes through temp files (module paths monkeypatched) and email
sending is monkeypatched to a no-op capture, so nothing here touches real
data files or the network.
Run with: python -m unittest tests.test_alerts
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import alerts


def _alert(dedup_key, priority="MEDIUM", ticker="NVDA"):
    return alerts.make_alert(
        dedup_key=dedup_key, category="watchlist", priority=priority, ticker=ticker,
        headline=f"{ticker} test alert", why_it_matters="testing",
        expected_impact="none", suggested_action="none",
        confidence=50, time_sensitivity="now")


class AlertsStorageTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_state = alerts.ALERTS_STATE_PATH
        self._orig_log = alerts.ALERTS_LOG_PATH
        self._orig_watchlists = alerts.WATCHLISTS_PATH
        tmp_dir = Path(tempfile.mkdtemp())
        alerts.ALERTS_STATE_PATH = tmp_dir / "alerts_state.json"
        alerts.ALERTS_LOG_PATH = tmp_dir / "alerts_log.json"
        alerts.WATCHLISTS_PATH = tmp_dir / "watchlists.json"

    def tearDown(self):
        alerts.ALERTS_STATE_PATH = self._orig_state
        alerts.ALERTS_LOG_PATH = self._orig_log
        alerts.WATCHLISTS_PATH = self._orig_watchlists


class TestMakeAlert(unittest.TestCase):
    def test_rejects_unknown_priority(self):
        with self.assertRaises(ValueError):
            alerts.make_alert(dedup_key="x", category="c", priority="URGENT!!",
                              headline="h", why_it_matters="w", expected_impact="e",
                              suggested_action="s", confidence=50, time_sensitivity="now")

    def test_confidence_is_clamped_to_0_100(self):
        a = alerts.make_alert(dedup_key="x", category="c", priority="LOW",
                              headline="h", why_it_matters="w", expected_impact="e",
                              suggested_action="s", confidence=150, time_sensitivity="now")
        self.assertEqual(a["confidence"], 100)
        b = alerts.make_alert(dedup_key="y", category="c", priority="LOW",
                              headline="h", why_it_matters="w", expected_impact="e",
                              suggested_action="s", confidence=-10, time_sensitivity="now")
        self.assertEqual(b["confidence"], 0)


class TestPriorityRank(unittest.TestCase):
    def test_critical_outranks_low(self):
        self.assertLess(alerts.priority_rank("CRITICAL"), alerts.priority_rank("LOW"))

    def test_unknown_priority_sorts_last(self):
        self.assertGreater(alerts.priority_rank("WHATEVER"), alerts.priority_rank("LOW"))


class TestDedupLifecycle(AlertsStorageTestCase):
    def setUp(self):
        super().setUp()
        # avoid any real network/email/Telegram attempts during these tests
        self._orig_send = alerts.send_alert_emails
        self._orig_send_telegram = alerts.send_alert_telegram
        alerts.send_alert_emails = lambda alerts_list: False
        alerts.send_alert_telegram = lambda alerts_list: False

    def tearDown(self):
        alerts.send_alert_emails = self._orig_send
        alerts.send_alert_telegram = self._orig_send_telegram
        super().tearDown()

    def test_new_condition_fires_once(self):
        a = _alert("NVDA:rsi_oversold")
        first = alerts.raise_alerts([a], {"NVDA:rsi_oversold"})
        second = alerts.raise_alerts([a], {"NVDA:rsi_oversold"})
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)  # still true, already notified

    def test_resolved_condition_clears_state(self):
        a = _alert("NVDA:rsi_oversold")
        alerts.raise_alerts([a], {"NVDA:rsi_oversold"})
        alerts.raise_alerts([], {"NVDA:rsi_oversold"})  # condition no longer true
        self.assertEqual(alerts.load_active(), {})

    def test_recurrence_after_resolution_fires_again(self):
        a = _alert("NVDA:rsi_oversold")
        alerts.raise_alerts([a], {"NVDA:rsi_oversold"})
        alerts.raise_alerts([], {"NVDA:rsi_oversold"})
        third = alerts.raise_alerts([a], {"NVDA:rsi_oversold"})
        self.assertEqual(len(third), 1)

    def test_unrelated_active_alert_is_untouched_by_other_checks(self):
        """A dedup_key not in checked_keys this cycle (a different ticker/
        condition scan) must survive even though it isn't in current_alerts."""
        a = _alert("NVDA:rsi_oversold")
        alerts.raise_alerts([a], {"NVDA:rsi_oversold"})
        # a completely different scan cycle checking only TSLA shouldn't
        # touch NVDA's still-active state
        alerts.raise_alerts([], {"TSLA:rsi_oversold"})
        self.assertIn("NVDA:rsi_oversold", alerts.load_active())

    def test_log_accumulates_new_alerts_only(self):
        a = _alert("NVDA:rsi_oversold")
        alerts.raise_alerts([a], {"NVDA:rsi_oversold"})
        alerts.raise_alerts([a], {"NVDA:rsi_oversold"})  # suppressed, not logged again
        self.assertEqual(len(alerts.load_log()), 1)

    def _watchlist(self):
        import json
        try:
            return json.loads(alerts.WATCHLISTS_PATH.read_text())
        except OSError:
            return {}

    def test_high_alert_ticker_mirrors_into_alert_watchlist(self):
        a = _alert("NVDA:earnings_T-0", priority="HIGH", ticker="NVDA")
        alerts.raise_alerts([a], {"NVDA:earnings_T-0"})
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], ["NVDA"])

    def test_medium_alert_is_not_mirrored(self):
        a = _alert("NVDA:earnings_T-5", priority="MEDIUM", ticker="NVDA")
        alerts.raise_alerts([a], {"NVDA:earnings_T-5"})
        self.assertEqual(self._watchlist().get(alerts.ALERT_WATCHLIST_KEY, []), [])

    def test_resolved_alert_drops_out_of_watchlist(self):
        a = _alert("NVDA:earnings_T-0", priority="HIGH", ticker="NVDA")
        alerts.raise_alerts([a], {"NVDA:earnings_T-0"})
        alerts.raise_alerts([], {"NVDA:earnings_T-0"})     # resolved
        self.assertEqual(self._watchlist()[alerts.ALERT_WATCHLIST_KEY], [])

    def test_email_grouping_maps_every_category(self):
        def g(**kw):
            return alerts._email_group(kw)
        self.assertEqual(g(category="earnings"), "Earnings")
        self.assertEqual(g(category="news"), "News Catalyst")
        self.assertEqual(g(category="put_setup"), "Options Setups")
        self.assertEqual(g(category="call_setup"), "Options Setups")
        self.assertEqual(g(category="watchlist", dedup_key="NVDA:gap"), "Day Trade")
        self.assertEqual(g(category="watchlist", dedup_key="NVDA:volume_spike"), "Day Trade")
        self.assertEqual(g(category="watchlist", dedup_key="NVDA:breakout"), "Swing Trades")
        self.assertEqual(g(category="watchlist", dedup_key="NVDA:rsi_oversold"), "Swing Trades")
        self.assertEqual(g(category="mystery"), "Other")

    def test_a_plus_files_under_longest_horizon(self):
        def ap(*strategies):
            return alerts._email_group({"category": "a_plus_setup",
                                        "supporting_data": {"strategies": list(strategies)}})
        self.assertEqual(ap("day (rank 85)"), "Day Trade")
        self.assertEqual(ap("swing (rank 98)", "day (rank 85)"), "Swing Trades")
        self.assertEqual(ap("long-term (score 85)", "swing (rank 98)"), "Long-Term Investment")

    def test_email_body_renders_grouped_sections_in_order(self):
        from stockanalysis.scanners import market_movers as mm
        captured = {}
        orig = mm.send_resend_email
        mm.send_resend_email = lambda subject, text, html, to=None: (
            captured.update(subject=subject, text=text, html=html) or True)
        try:
            # self._orig_send is the REAL send_alert_emails (setUp stubs the
            # module attr so lifecycle tests never email)
            sent = self._orig_send([
                _alert("AMD:a_plus_setup", priority="HIGH", ticker="AMD") | {
                    "category": "a_plus_setup",
                    "supporting_data": {"strategies": ["swing (rank 113)"]}},
                _alert("NVDA:earnings_T-0", priority="HIGH", ticker="NVDA") | {
                    "category": "earnings"},
                _alert("PSX:put_score_high", priority="HIGH", ticker="PSX") | {
                    "category": "put_setup"},
            ])
        finally:
            mm.send_resend_email = orig
        self.assertTrue(sent)
        text = captured["text"]
        self.assertIn("=== EARNINGS (1)", text)
        self.assertIn("=== SWING TRADES (1)", text)
        self.assertIn("=== OPTIONS SETUPS (1)", text)
        # section order follows EMAIL_GROUP_ORDER: earnings before swing before options
        self.assertLess(text.index("EARNINGS"), text.index("SWING TRADES"))
        self.assertLess(text.index("SWING TRADES"), text.index("OPTIONS SETUPS"))
        self.assertIn("Earnings 1", captured["subject"])
        self.assertIn("Swing Trades 1", captured["subject"])

    def test_low_alerts_expire_from_display_after_24h(self):
        from datetime import datetime, timedelta
        low = _alert("NVDA:macd_bullish", priority="LOW", ticker="NVDA")
        high = _alert("AMD:breakout", priority="HIGH", ticker="AMD")
        alerts.raise_alerts([low, high], {"NVDA:macd_bullish", "AMD:breakout"})

        fresh = alerts.active_display_alerts()
        self.assertEqual({a["dedup_key"] for a in fresh},
                         {"NVDA:macd_bullish", "AMD:breakout"})

        later = datetime.now() + timedelta(hours=25)
        aged = alerts.active_display_alerts(now=later)
        self.assertEqual({a["dedup_key"] for a in aged}, {"AMD:breakout"})
        # expired LOW stays in the persisted state (no refire clock-reset) …
        self.assertIn("NVDA:macd_bullish", alerts.load_active())
        # … and refiring the still-true condition stays suppressed
        refire = alerts.raise_alerts([low], {"NVDA:macd_bullish"})
        self.assertEqual(refire, [])

    def test_high_alerts_never_expire_from_display(self):
        from datetime import datetime, timedelta
        a = _alert("NVDA:earnings_T-0", priority="HIGH", ticker="NVDA")
        alerts.raise_alerts([a], {"NVDA:earnings_T-0"})
        later = datetime.now() + timedelta(days=10)
        self.assertEqual(len(alerts.active_display_alerts(now=later)), 1)

    def test_other_watchlist_keys_survive_the_sync(self):
        import json
        alerts.WATCHLISTS_PATH.write_text(json.dumps({"AI": ["NVDA", "AMD"]}))
        a = _alert("TSLA:breakout", priority="CRITICAL", ticker="TSLA")
        alerts.raise_alerts([a], {"TSLA:breakout"})
        wl = self._watchlist()
        self.assertEqual(wl["AI"], ["NVDA", "AMD"])        # untouched
        self.assertEqual(wl[alerts.ALERT_WATCHLIST_KEY], ["TSLA"])


class TestEmailBatching(unittest.TestCase):
    def test_only_critical_and_high_trigger_email(self):
        sent = {}

        def fake_send(subject, text, html, to=None):
            sent["subject"] = subject
            sent["text"] = text
            return True

        import stockanalysis.scanners.market_movers as mm
        orig = mm.send_resend_email
        mm.send_resend_email = fake_send
        try:
            batch = [_alert("A:x", priority="LOW"), _alert("B:y", priority="MEDIUM")]
            result = alerts.send_alert_emails(batch)
            self.assertFalse(result)
            self.assertNotIn("subject", sent)

            batch2 = [_alert("A:x", priority="LOW"), _alert("C:z", priority="CRITICAL")]
            result2 = alerts.send_alert_emails(batch2)
            self.assertTrue(result2)
            self.assertIn("1 alert", sent["subject"])
            self.assertIn("CRITICAL", sent["text"])
            self.assertNotIn("LOW", sent["text"])  # low-priority alert excluded from the batch
        finally:
            mm.send_resend_email = orig

    def test_no_urgent_alerts_returns_false_without_sending(self):
        self.assertFalse(alerts.send_alert_emails([_alert("A:x", priority="LOW")]))


class TestTelegramBatching(unittest.TestCase):
    def test_only_critical_and_high_trigger_telegram(self):
        sent = {}

        def fake_send(text):
            sent["text"] = text
            return True

        import stockanalysis.scanners.market_movers as mm
        orig = mm.send_telegram_message
        mm.send_telegram_message = fake_send
        try:
            batch = [_alert("A:x", priority="LOW"), _alert("B:y", priority="MEDIUM")]
            result = alerts.send_alert_telegram(batch)
            self.assertFalse(result)
            self.assertNotIn("text", sent)

            batch2 = [_alert("A:x", priority="LOW"), _alert("C:z", priority="CRITICAL")]
            result2 = alerts.send_alert_telegram(batch2)
            self.assertTrue(result2)
            self.assertIn("1 alert", sent["text"])
            self.assertIn("CRITICAL", sent["text"])
            self.assertNotIn("LOW", sent["text"])  # low-priority alert excluded from the batch
        finally:
            mm.send_telegram_message = orig

    def test_no_urgent_alerts_returns_false_without_sending(self):
        self.assertFalse(alerts.send_alert_telegram([_alert("A:x", priority="LOW")]))


if __name__ == "__main__":
    unittest.main()
