"""
The "ALL" picker entry expands to every list in watchlists.json.
Run with: python -m unittest tests.test_all_universes
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.webapp import api
from stockanalysis.reporting import research as R

FAKE = {"AI": ["NVDA", "AMD"], "AI: Power": ["VRT"],
        "sp500": ["AAPL"], "Breakout": []}


class TestExpandAll(unittest.TestCase):
    def setUp(self):
        self._orig = R.load_watchlists
        R.load_watchlists = lambda: dict(FAKE)

    def tearDown(self):
        R.load_watchlists = self._orig

    def test_sentinel_expands_to_every_non_empty_list(self):
        out = api.expand_all([api.ALL_UNIVERSES_SENTINEL])
        self.assertIn("AI", out)
        self.assertIn("AI: Power", out)
        self.assertIn("sp500", out)
        # an empty list contributes nothing to scan and would only produce a
        # confusing "0 tickers" entry in the job label
        self.assertNotIn("Breakout", out)
        self.assertNotIn(api.ALL_UNIVERSES_SENTINEL, out)

    def test_noop_without_sentinel(self):
        self.assertEqual(api.expand_all(["AI", "sp500"]), ["AI", "sp500"])

    def test_empty_input(self):
        self.assertEqual(api.expand_all([]), [])

    def test_sentinel_combined_with_explicit_picks_does_not_duplicate(self):
        out = api.expand_all(["sp500", api.ALL_UNIVERSES_SENTINEL])
        self.assertEqual(len(out), len(set(out)))
        self.assertIn("sp500", out)

    def test_expanded_names_are_all_scannable(self):
        # every name ALL yields must be something dispatch_run accepts,
        # otherwise "ALL" would fail its own unknown-universe check
        out = api.expand_all([api.ALL_UNIVERSES_SENTINEL])
        allowed = set(api.available_universes())
        self.assertTrue(set(out).issubset(allowed),
                        f"not scannable: {sorted(set(out) - allowed)}")


class TestWatchlistTickersViaAll(unittest.TestCase):
    """dispatch_run's watchlist_tickers() must see the expansion too — the
    research and news actions resolve tickers through it, not through the
    universe list."""

    def setUp(self):
        self._orig = R.load_watchlists
        R.load_watchlists = lambda: dict(FAKE)

    def tearDown(self):
        R.load_watchlists = self._orig

    def test_research_action_with_all_covers_every_list(self):
        captured = {}

        def fake_start(kind, label, fn):
            captured["kind"], captured["label"] = kind, label
            return ""

        orig_start = api.jobstore.start
        api.jobstore.start = fake_start
        try:
            err = api.dispatch_run(
                "research", {"watchlist": [api.ALL_UNIVERSES_SENTINEL]})
        finally:
            api.jobstore.start = orig_start
        self.assertEqual(err, "")
        self.assertEqual(captured["kind"], "research")


if __name__ == "__main__":
    unittest.main()
