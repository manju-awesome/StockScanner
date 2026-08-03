"""
data/watchlists.json stores AI sublists nested; every consumer reads a flat
{name: [tickers]} view. These pin the compat layer both ways.
Run with: python -m unittest tests.test_watchlists_nesting
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.reporting import research as R


class TestFlatten(unittest.TestCase):
    def test_parent_contributes_own_tickers_only(self):
        flat = R._flatten_watchlists(
            {"AI": {"_tickers": ["NVDA", "AMD"], "Power": ["VRT"]}})
        # ticking "AI" must not silently pull in children
        self.assertEqual(flat["AI"], ["NVDA", "AMD"])
        self.assertEqual(flat["AI: Power"], ["VRT"])

    def test_plain_lists_pass_through(self):
        flat = R._flatten_watchlists({"sp500": ["A", "B"]})
        self.assertEqual(flat["sp500"], ["A", "B"])

    def test_parent_with_no_own_tickers(self):
        flat = R._flatten_watchlists({"AI": {"Power": ["VRT"]}})
        self.assertEqual(flat["AI"], [])
        self.assertEqual(flat["AI: Power"], ["VRT"])


class TestNest(unittest.TestCase):
    def test_only_configured_parents_fold(self):
        nested = R._nest_watchlists(
            {"AI: Power": ["VRT"], "Sector: Technology": ["AAPL"]})
        self.assertIsInstance(nested["AI"], dict)
        # "Sector" isn't in NESTED_PARENTS — must stay a top-level flat list
        self.assertEqual(nested["Sector: Technology"], ["AAPL"])

    def test_child_before_parent(self):
        nested = R._nest_watchlists({"AI: Power": ["VRT"], "AI": ["NVDA"]})
        self.assertEqual(nested["AI"]["_tickers"], ["NVDA"])
        self.assertEqual(nested["AI"]["Power"], ["VRT"])

    def test_parent_before_child(self):
        nested = R._nest_watchlists({"AI": ["NVDA"], "AI: Power": ["VRT"]})
        self.assertEqual(nested["AI"]["_tickers"], ["NVDA"])
        self.assertEqual(nested["AI"]["Power"], ["VRT"])

    def test_already_nested_passes_through(self):
        src = {"AI": {"_tickers": ["NVDA"], "Power": ["VRT"]}}
        self.assertEqual(R._nest_watchlists(src), src)


class TestRoundTrip(unittest.TestCase):
    FLAT = {
        "AI": ["NVDA", "AMD"],
        "AI: Power": ["VRT", "CEG"],
        "AI: Optics": ["AAOI"],
        "sp500": ["AAPL", "MSFT"],
        "Sector: Technology": ["AAPL"],
        "52_week_high": ["ALL"],
    }

    def test_flat_survives_round_trip(self):
        self.assertEqual(
            R._flatten_watchlists(R._nest_watchlists(self.FLAT)), self.FLAT)

    def test_nested_survives_round_trip(self):
        nested = R._nest_watchlists(self.FLAT)
        self.assertEqual(
            R._nest_watchlists(R._flatten_watchlists(nested)), nested)

    def test_empty_child_survives(self):
        flat = {"AI": [], "AI: Power": []}
        self.assertEqual(
            R._flatten_watchlists(R._nest_watchlists(flat)), flat)


class TestLiveFile(unittest.TestCase):
    def test_live_file_ai_exposes_own_tickers_not_a_rollup(self):
        flat = R.load_watchlists()
        nested = R.load_watchlists_nested()
        self.assertIsInstance(nested.get("AI"), dict,
                              "live file should have AI nested")
        children = [k for k in flat if k.startswith("AI" + R.SUBLIST_SEP)]
        self.assertTrue(children, "expected AI sublists in the live file")
        # the flat "AI" is exactly the parent's own list, never a roll-up of
        # its children. (No assertion here that children are *absent* from
        # it — in the live file the curated AI list happens to be a superset
        # of every sublist, which is a property of the data, not the code.
        # TestFlatten.test_parent_contributes_own_tickers_only pins the
        # no-roll-up behaviour on a fixture where the two actually differ.)
        self.assertEqual(flat["AI"], list(nested["AI"]["_tickers"]))

    def test_live_file_children_match_nested(self):
        flat = R.load_watchlists()
        nested = R.load_watchlists_nested()
        for child, tickers in nested["AI"].items():
            if child == "_tickers":
                continue
            self.assertEqual(flat[f"AI{R.SUBLIST_SEP}{child}"], list(tickers))


if __name__ == "__main__":
    unittest.main()
