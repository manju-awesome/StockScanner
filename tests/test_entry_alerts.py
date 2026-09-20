"""
Tests for core.longterm.entry_alerts — "price has arrived at the level you
planned to buy".

The invariants:

  1. An at-market entry is never an alert. "Within 1% of the current price"
     is true by construction for a BUY NOW verdict, and a condition that
     cannot fail to fire carries no information.
  2. A missing live quote is not an opinion. It must produce neither an alert
     nor a clear — otherwise a failed fetch silently resets the dedup state
     and the next cycle re-announces everything.
  3. The email owns its subject. It is excluded from the general digest so
     the same alert cannot arrive twice under two different headings.

Run with: python -m unittest tests.test_entry_alerts
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import alerts as A
from stockanalysis.core.longterm import entry_alerts as EA


def _row(ticker="NVDA", price=100.0, entry=99.5, stop=92.0, at_market=False,
         ok=True, rr=3.0, shares=120, action="BUY ON 50 MA"):
    return {
        "ticker": ticker, "price": price, "action": action,
        "sizing_plan": {
            "ok": ok,
            "entry": {"price": entry, "type": "Support Entry",
                      "level_name": "113 touches", "at_market": at_market},
            "stop": {"price": stop, "source": "50 MA"},
            "sizing": {"shares": shares, "position_value": shares * entry,
                       "actual_risk": shares * (entry - stop)},
            "target": {"rr": rr, "price": entry * 1.2},
            "grade": "B",
        },
    }


class TestEligibility(unittest.TestCase):
    def test_a_resting_entry_near_the_price_is_a_candidate(self):
        n = EA.near_entry(_row(price=100.0, entry=99.5))
        self.assertTrue(n["within"])
        self.assertAlmostEqual(n["gap_pct"], 0.5, places=2)

    def test_an_at_market_entry_is_never_eligible(self):
        # BUY NOW's entry IS the current price — this would fire forever.
        self.assertIsNone(EA.near_entry(_row(entry=100.0, at_market=True)))

    def test_an_unsized_plan_is_not_a_trade(self):
        self.assertIsNone(EA.near_entry(_row(ok=False)))

    def test_a_plan_without_a_stop_is_not_a_trade(self):
        row = _row()
        row["sizing_plan"]["stop"] = {}
        self.assertIsNone(EA.near_entry(row))

    def test_the_band_is_symmetric(self):
        # Drifting down onto the level and slipping just under it are the
        # same event to someone with an order to place.
        above = EA.near_entry(_row(price=100.8, entry=100.0))
        below = EA.near_entry(_row(price=99.2, entry=100.0))
        self.assertTrue(above["within"] and below["within"])
        self.assertGreater(above["gap_pct"], 0)
        self.assertLess(below["gap_pct"], 0)

    def test_outside_the_band_is_reported_not_dropped(self):
        # The caller needs "eligible but not close" distinguishable from
        # "not eligible" — the first still clears a stale alert.
        n = EA.near_entry(_row(price=120.0, entry=100.0))
        self.assertIsNotNone(n)
        self.assertFalse(n["within"])

    def test_candidates_only_lists_rows_carrying_a_resting_order(self):
        rows = [_row("AAA"), _row("BBB", at_market=True), _row("CCC", ok=False)]
        self.assertEqual(EA.candidates(rows), ["AAA"])

    def test_only_a_BUY_verdict_qualifies(self):
        """The engine has to be offering the trade, not merely have priced it.

        Every sized plan carries an entry, including WAIT and OWN/WAIT ones,
        and for most of those the entry is the nearest support shelf — which
        sits inside the band roughly half the time by construction. Measured
        live: 44 of 104 resting orders were within 1% and not one was a name
        the engine was offering to buy.
        """
        for action in ("WAIT", "OWN / WAIT FOR PRICE", "OWN / WAIT FOR TREND",
                       "WATCH", "AVOID"):
            self.assertIsNone(EA.near_entry(_row(action=action)), action)
        for action in ("BUY ON 50 MA", "BUY ON 8/21 EMA", "BUY ON SUPPORT",
                       "BUY ON BREAKOUT RETEST", "BUY ON 200 MA"):
            self.assertIsNotNone(EA.near_entry(_row(action=action)), action)


class TestScan(unittest.TestCase):
    """scan_for_alerts against a stubbed alert store and sender."""

    def setUp(self):
        self.raised, self.emailed = [], []
        self._raise, self._send = A.raise_alerts, EA.send_entry_email

        def fake_raise(current, checked):
            self.raised.append((list(current), set(checked)))
            return list(current)          # pretend all are new
        A.raise_alerts = fake_raise
        EA.send_entry_email = lambda readings: self.emailed.append(readings)

    def tearDown(self):
        A.raise_alerts, EA.send_entry_email = self._raise, self._send

    def test_only_names_inside_the_band_alert(self):
        rows = [_row("NEAR", price=100.0, entry=99.6),
                _row("FAR", price=100.0, entry=80.0)]
        EA.scan_for_alerts(rows, prices={"NEAR": 100.0, "FAR": 100.0})
        current, checked = self.raised[0]
        self.assertEqual([a["ticker"] for a in current], ["NEAR"])
        # ...but BOTH are checked, so FAR can clear a stale alert and fire
        # again on its next approach.
        self.assertEqual(checked,
                         {"longterm_entry:NEAR", "longterm_entry:FAR"})

    def test_a_missing_quote_is_neither_an_alert_nor_a_clear(self):
        rows = [_row("HAS", price=100.0, entry=99.6),
                _row("NONE", price=100.0, entry=99.6)]
        EA.scan_for_alerts(rows, prices={"HAS": 100.0})
        current, checked = self.raised[0]
        self.assertEqual([a["ticker"] for a in current], ["HAS"])
        self.assertNotIn("longterm_entry:NONE", checked)

    def test_the_live_price_overrides_the_stored_one(self):
        # Stored price says far away; the live quote says it has arrived.
        rows = [_row("NVDA", price=120.0, entry=100.0)]
        EA.scan_for_alerts(rows, prices={"NVDA": 100.4})
        current, _checked = self.raised[0]
        self.assertEqual(len(current), 1)
        self.assertAlmostEqual(
            current[0]["supporting_data"]["price"], 100.4, places=2)

    def test_an_email_goes_out_only_for_newly_fired_names(self):
        rows = [_row("AAA", price=100.0, entry=99.6)]
        EA.scan_for_alerts(rows, prices={"AAA": 100.0})
        self.assertEqual(len(self.emailed), 1)
        self.assertEqual([n["ticker"] for n in self.emailed[0]], ["AAA"])

    def test_nothing_in_the_band_sends_nothing(self):
        EA.scan_for_alerts([_row("AAA", price=100.0, entry=80.0)],
                           prices={"AAA": 100.0})
        self.assertEqual(self.emailed, [])


class TestEmail(unittest.TestCase):
    def setUp(self):
        import stockanalysis.scanners.market_movers as MM
        self.sent = {}
        self._real = MM.send_resend_email
        MM.send_resend_email = lambda s, t, h, to=None: (
            self.sent.update(subject=s, text=t, html=h) or True)
        self.MM = MM

    def tearDown(self):
        self.MM.send_resend_email = self._real

    def test_the_subject_is_fixed(self):
        # A standing list of orders to work has to be filterable, which a
        # digest subject naming its own contents is not.
        EA.send_entry_email([EA.near_entry(_row("NVDA"))])
        self.assertEqual(self.sent["subject"], "Longterm swing trades")
        self.assertEqual(EA.SUBJECT, "Longterm swing trades")

    def test_the_body_carries_the_whole_order(self):
        EA.send_entry_email([EA.near_entry(_row("NVDA", entry=99.5, stop=92.0))])
        for bit in ("NVDA", "99.50", "92.00", "120"):
            self.assertIn(bit, self.sent["text"])

    def test_closest_first(self):
        readings = [EA.near_entry(_row("FAR", price=100.9, entry=100.0)),
                    EA.near_entry(_row("NEAR", price=100.1, entry=100.0))]
        EA.send_entry_email(readings)
        self.assertLess(self.sent["text"].index("NEAR"),
                        self.sent["text"].index("FAR"))

    def test_no_readings_sends_nothing(self):
        self.assertFalse(EA.send_entry_email([]))
        self.assertEqual(self.sent, {})


class TestDigestExclusion(unittest.TestCase):
    """The other half of owning a subject: not also appearing in the digest."""

    def _alert(self):
        return A.make_alert(
            dedup_key="longterm_entry:NVDA", category=EA.CATEGORY,
            ticker="NVDA", priority="HIGH", headline="h", why_it_matters="w",
            expected_impact="e", suggested_action="s", confidence=80,
            time_sensitivity="t")

    def test_the_category_is_registered_as_self_emailing(self):
        self.assertIn(EA.CATEGORY, A.SELF_EMAILING_CATEGORIES)

    def test_it_is_excluded_from_both_digests(self):
        # False here means "nothing to send" — the alert was filtered out
        # before either sender did any work.
        self.assertFalse(A.send_alert_emails([self._alert()]))
        self.assertFalse(A.send_alert_telegram([self._alert()]))

    def test_the_exclusion_is_independent_of_the_mute(self):
        """Two different reasons a category does not reach the digest.

        The mute is a preference and can be lifted with one env var; the
        self-emailing exclusion is a correctness rule and must survive that,
        or unmuting would start delivering "Longterm swing trades" twice
        under two different subjects.
        """
        import os
        import stockanalysis.scanners.market_movers as MM
        real, seen = MM.send_resend_email, []
        MM.send_resend_email = lambda s, t, h, to=None: seen.append(s) or True
        prev = os.environ.get("ALERT_NOTIFY_CATEGORIES")
        os.environ["ALERT_NOTIFY_CATEGORIES"] = "all"
        try:
            other = A.make_alert(
                dedup_key="watchlist:NVDA:gap", category="watchlist",
                ticker="NVDA", priority="HIGH", headline="h",
                why_it_matters="w", expected_impact="e", suggested_action="s",
                confidence=80, time_sensitivity="t")
            # unmuted, an ordinary category reaches the digest...
            A.send_alert_emails([other])
            self.assertEqual(len(seen), 1)
            # ...and the self-emailing one still does not.
            self.assertFalse(A.send_alert_emails([self._alert()]))
            self.assertEqual(len(seen), 1)
        finally:
            MM.send_resend_email = real
            if prev is None:
                os.environ.pop("ALERT_NOTIFY_CATEGORIES", None)
            else:
                os.environ["ALERT_NOTIFY_CATEGORIES"] = prev

    def test_the_digest_is_muted_by_default(self):
        # DEFAULT_NOTIFY_CATEGORIES is empty: only the three channels that
        # bypass this module still send.
        self.assertEqual(A.DEFAULT_NOTIFY_CATEGORIES, ())
        watchlist = A.make_alert(
            dedup_key="watchlist:NVDA:gap", category="watchlist", ticker="NVDA",
            priority="CRITICAL", headline="h", why_it_matters="w",
            expected_impact="e", suggested_action="s", confidence=80,
            time_sensitivity="t")
        self.assertEqual(A.notifiable([watchlist]), [])
        self.assertFalse(A.send_alert_emails([watchlist]))
        self.assertFalse(A.send_alert_telegram([watchlist]))


class TestWiring(unittest.TestCase):
    def test_the_job_is_schedulable_and_has_a_default(self):
        from stockanalysis.scheduling import scheduler, schedule_config
        self.assertIn("longterm_entry_alerts", scheduler.SCHEDULED_JOBS)
        self.assertIn("longterm_entry_alerts", schedule_config.JOB_DEFS)
        cfg = schedule_config.JOB_DEFS["longterm_entry_alerts"]["default"]
        self.assertTrue(cfg["enabled"])

    def test_the_manual_trigger_is_dispatchable(self):
        from stockanalysis.webapp import api, jobstore
        started = {}
        real = jobstore.start
        jobstore.start = lambda kind, label, fn: (
            started.update(kind=kind, label=label), "")[1]
        try:
            err = api.dispatch_run("longterm_entry_scan",
                                   {"action": ["longterm_entry_scan"]})
        finally:
            jobstore.start = real
        self.assertEqual(err, "")
        self.assertEqual(started["kind"], "longterm_entry_scan")


if __name__ == "__main__":
    unittest.main()
