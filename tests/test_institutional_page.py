"""
Tests for the /institutional page and the 13F skew signal.

The dataset behind this page is the most misreadable in the project: it is
long-only, months stale, and drawn from a population that is not the
options market. Almost everything asserted here defends a piece of framing
rather than a computation — because the computations are already tested in
test_institutional.py, and the way this data gets misused is by being
presented without its bounds.

Invariants defended here:

  1. The long-only caveat is on the page, above the numbers. Every version
     of this warning that lived at the bottom was a version people skipped.
  2. The skew reading never becomes a gate. It rides in `context`, never in
     `risks`, and never touches the action.
  3. Not-covered and no-position are different answers. A zeroed row would
     render as "no institution holds options here" while usually meaning
     "this name was not in the last run".
  4. A ratio built from two filers is not a pattern, and does not get a
     directional word.
  5. The page returns (body, js). Returning (title, body) renders the title
     string AS the page body — it type-checks and silently produces a page
     containing the words "Institutional Options" and nothing else.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import decision_engine as DE          # noqa: E402
from stockanalysis.core.institutional import signal as SG      # noqa: E402
from stockanalysis.webapp import app as APP                    # noqa: E402
from stockanalysis.webapp import institutional_view as IV      # noqa: E402
from stockanalysis.webapp.views import NAV                     # noqa: E402

SNAP = {
    "period": "2026-03-31",
    "dataset": "01mar2026-31may2026_form13f.zip",
    "generated_at": "2026-08-26T19:13:05",
    "ratio_basis": "Long option positions reported on Form 13F…",
    "coverage": {"managers": 8741, "filings_kept": 8762, "superseded": 106,
                 "notices": 1908, "option_rows": 116192,
                 "share_rows": 3123081, "unreliable_rows": 7327,
                 "rescaled_rows": 13155},
    "tickers": [
        {"ticker": "IWM", "issuer": "ISHARES TR", "cusip": "464287655",
         "cusip_status": "resolved", "cusip_confidence": 0.99,
         "put_call_ratio_contracts": 2.55, "rescaled_rows": 4,
         "unreliable_rows": 1, "reference_price": 231.4,
         "ratio_basis": "Long option positions…",
         "calls": {"holders": 78, "contracts": 1620571.0, "value_usd": 1.0,
                   "top": [{"manager": "JANE STREET GROUP, LLC",
                            "cik": "1", "contracts": 400000.0,
                            "underlying_shares": 4e7, "value_usd": 1.0,
                            "units": "shares"}]},
         "puts": {"holders": 168, "contracts": 4131648.0, "value_usd": 1.0,
                  "top": [{"manager": "CITADEL ADVISORS LLC", "cik": "2",
                           "contracts": 900000.0, "underlying_shares": 9e7,
                           "value_usd": 1.0, "units": "contracts"}]}},
        {"ticker": "GOOGL", "cusip_status": "ambiguous", "issuer": "ALPHABET INC",
         "error": "CUSIP for GOOGL is ambiguous.",
         "candidates": [{"cusip": "02079K305", "rows": 8237, "issuer": "ALPHABET INC"},
                        {"cusip": "02079K107", "rows": 7131, "issuer": "ALPHABET INC"}]},
    ],
    "leaderboard_put": [{"cusip": "30040W108", "issuer": "EVERSOURCE ENERGY",
                         "holders": 11, "contracts": 2223870.0,
                         "rescaled_share": 1.0}],
    "leaderboard_call": [{"cusip": "67066G104", "issuer": "NVIDIA CORPORATION",
                          "holders": 171, "contracts": 4703718.0,
                          "rescaled_share": 0.07}],
}


class TestSkewSignal(unittest.TestCase):

    def test_reading_carries_its_own_bounds(self):
        r = SG.skew("IWM", SNAP)
        self.assertTrue(r["context_only"])
        self.assertEqual(r["period"], "2026-03-31")
        self.assertTrue(r["basis"])

    def test_uncovered_ticker_is_none_not_zero(self):
        """A zeroed row reads as 'no institution holds options on this',
        which is a claim about the market, not about our coverage."""
        self.assertIsNone(SG.skew("ZZZZ", SNAP))

    def test_unresolved_ticker_is_none(self):
        self.assertIsNone(SG.skew("GOOGL", SNAP))

    def test_labels_are_about_filings_not_direction(self):
        """'Put-heavy' is a fact about filings. 'Bearish' would be an
        inference this data cannot support."""
        self.assertEqual(SG.label(2.55, 100), "put-heavy book")
        self.assertEqual(SG.label(0.50, 100), "call-heavy book")
        self.assertEqual(SG.label(1.00, 100), "balanced book")
        self.assertEqual(SG.label(None, 100), "not reported")

    def test_thin_holder_counts_get_no_directional_word(self):
        """A 3.0 ratio built from two filers says something about those two
        filers, not about institutional positioning."""
        self.assertEqual(SG.label(3.0, 2), "too few holders to read")

    def test_band_edges(self):
        self.assertEqual(SG.label(SG.PUT_HEAVY, 100), "put-heavy book")
        self.assertEqual(SG.label(SG.CALL_HEAVY, 100), "call-heavy book")
        self.assertEqual(SG.label(SG.PUT_HEAVY - 0.01, 100), "balanced book")

    def test_summary_names_the_staleness_and_the_long_only_bound(self):
        text = SG.summary(SG.skew("IWM", SNAP))
        self.assertIn("2026-03-31", text)
        self.assertIn("long positions only", text)

    def test_summary_of_nothing_is_empty(self):
        self.assertEqual(SG.summary(None), "")


class TestSignalIsNeverAGate(unittest.TestCase):

    def _verdict(self, reading):
        row = {"ticker": "IWM", "institutional": reading,
               "institutional_summary": SG.summary(reading)}
        return DE.decide(row)

    def test_skew_rides_in_context_not_risks(self):
        """`risks` is read as things arguing against the action. A put-heavy
        filing from last quarter argues nothing."""
        v = self._verdict(SG.skew("IWM", SNAP))
        self.assertTrue(any("13F" in c for c in v["context"]))
        self.assertFalse(any("13F" in r for r in v["risks"]))

    def test_skew_does_not_change_the_action(self):
        """Same row, opposite books, identical verdict."""
        put_heavy = dict(SG.skew("IWM", SNAP), put_call=4.0, label="put-heavy book")
        call_heavy = dict(SG.skew("IWM", SNAP), put_call=0.2, label="call-heavy book")
        self.assertEqual(self._verdict(put_heavy)["action"],
                         self._verdict(call_heavy)["action"])
        self.assertEqual(self._verdict(None)["action"],
                         self._verdict(put_heavy)["action"])

    def test_absent_reading_leaves_context_empty(self):
        self.assertEqual(self._verdict(None)["context"], [])


class TestPage(unittest.TestCase):

    def setUp(self):
        self._real = IV.IS.load
        IV.IS.load = lambda: SNAP
        self.addCleanup(lambda: setattr(IV.IS, "load", self._real))

    def test_returns_body_and_js_not_title_and_body(self):
        """(title, body) type-checks and silently renders the title AS the
        body — a page containing two words and nothing else."""
        body, js = IV.institutional_page({})
        self.assertEqual(js, "")
        self.assertTrue(body.lstrip().startswith("<"))
        self.assertGreater(len(body), 2000)

    def test_long_only_caveat_is_above_the_numbers(self):
        body, _ = IV.institutional_page({})
        self.assertIn("LONG positions only", body)
        self.assertLess(body.index("LONG positions only"),
                        body.index("Put / Call"))

    def test_unresolved_names_are_listed_not_dropped(self):
        """A missing ticker reads as 'no institution holds options on it'."""
        body, _ = IV.institutional_page({})
        self.assertIn("Not resolved to a CUSIP", body)
        self.assertIn("02079K305", body)

    def test_rescaled_share_is_shown_on_the_leaderboard(self):
        """One reinterpreted row can carry a name into the top fifteen."""
        body, _ = IV.institutional_page({})
        self.assertIn("EVERSOURCE ENERGY", body)
        self.assertIn("100%", body)

    def test_ticker_lookup_names_what_it_could_not_find(self):
        body, _ = IV.institutional_page({"tickers": ["IWM NOSUCH"]})
        self.assertIn("Not in this snapshot", body)
        self.assertIn("NOSUCH", body)

    def test_leaderboard_side_toggle(self):
        put, _ = IV.institutional_page({"side": ["Put"]})
        call, _ = IV.institutional_page({"side": ["Call"]})
        self.assertIn("EVERSOURCE", put)
        self.assertIn("NVIDIA", call)

    def test_junk_side_falls_back_rather_than_emptying_the_panel(self):
        body, _ = IV.institutional_page({"side": ["nonsense"]})
        self.assertIn("EVERSOURCE", body)

    def test_no_snapshot_explains_itself(self):
        IV.IS.load = lambda: None
        body, js = IV.institutional_page({})
        self.assertEqual(js, "")
        self.assertIn("No snapshot yet", body)
        self.assertIn("fetch_13f.py", body)


class TestWiring(unittest.TestCase):

    def test_route_is_registered(self):
        self.assertIn("/institutional", APP.ROUTES)
        key, title, fn = APP.ROUTES["/institutional"]
        self.assertEqual(key, "institutional")
        self.assertTrue(callable(fn))

    def test_route_accepts_the_query_dict(self):
        """Pages take no arguments unless they declare one; a page that
        reads ?tickers= but does not declare it is never handed them."""
        self.assertTrue(APP._wants_query(APP.ROUTES["/institutional"][2]))

    def test_nav_entry_exists_and_matches_the_route(self):
        entry = next((n for n in NAV if n[0] == "institutional"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry[1], "/institutional")

    def test_every_nav_key_has_a_route(self):
        routes = {v[0] for v in APP.ROUTES.values()}
        self.assertEqual([n[0] for n in NAV if n[0] not in routes], [])


if __name__ == "__main__":
    unittest.main()
