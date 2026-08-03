"""
Competitive-position ranking for the Research Library's Position column.
Run with: python -m unittest tests.test_market_position
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import market_position as MP


def entry(ticker, cap, industry=None, sector="Technology"):
    return {"ticker": ticker, "market_cap": cap, "sector": sector,
            "raw": ({"Industry": industry} if industry else {})}


def group(caps, industry="Widgets"):
    return [entry(t, c, industry) for t, c in caps]


class TestRanking(unittest.TestCase):
    def test_rank_and_share(self):
        pos = MP.compute_peer_positions(
            group([("A", 60), ("B", 20), ("C", 15), ("D", 5)]))
        self.assertEqual(pos["A"]["peer_rank"], 1)
        self.assertEqual(pos["D"]["peer_rank"], 4)
        self.assertEqual(pos["A"]["peer_count"], 4)
        self.assertEqual(pos["A"]["peer_share_pct"], 60.0)

    def test_dominant_when_majority(self):
        pos = MP.compute_peer_positions(
            group([("A", 60), ("B", 20), ("C", 15), ("D", 5)]))
        self.assertEqual(pos["A"]["position_tier"], "dominant")
        self.assertEqual(pos["A"]["position_label"], "Dominant")

    def test_real_duopoly(self):
        # 45 + 35 = 80% top-2, and the runner-up is substantial
        pos = MP.compute_peer_positions(
            group([("A", 45), ("B", 35), ("C", 12), ("D", 8)]))
        self.assertEqual(pos["A"]["position_tier"], "duopoly")
        self.assertEqual(pos["B"]["position_tier"], "duopoly")
        self.assertEqual(pos["C"]["position_tier"], "rest")

    def test_giant_plus_alsoran_is_not_a_duopoly(self):
        # regression: MSFT 71% + ORCL 8% cleared the top-2 bar on the giant
        # alone and mislabelled the distant #2 a duopolist
        pos = MP.compute_peer_positions(
            group([("A", 71), ("B", 8), ("C", 7), ("D", 7), ("E", 7)]))
        self.assertEqual(pos["A"]["position_tier"], "dominant")
        self.assertEqual(pos["B"]["position_tier"], "top2")
        self.assertEqual(pos["B"]["position_label"], "#2")

    def test_small_group_gets_no_tier_label(self):
        # 3 peers is too thin to call concentration
        pos = MP.compute_peer_positions(group([("A", 80), ("B", 15), ("C", 5)]))
        self.assertEqual(pos["A"]["position_tier"], "rest")
        self.assertEqual(pos["A"]["position_label"], "#1")

    def test_groups_are_independent(self):
        rows = group([("A", 60), ("B", 40)], "Widgets") + \
               group([("X", 90), ("Y", 10)], "Gadgets")
        pos = MP.compute_peer_positions(rows)
        self.assertEqual(pos["A"]["peer_group"], "Widgets")
        self.assertEqual(pos["X"]["peer_group"], "Gadgets")
        self.assertEqual(pos["A"]["peer_count"], 2)


class TestGrouping(unittest.TestCase):
    def test_falls_back_to_sector(self):
        pos = MP.compute_peer_positions([
            entry("A", 10, None, "Healthcare"), entry("B", 5, None, "Healthcare")])
        self.assertEqual(pos["A"]["peer_group"], "Healthcare")
        self.assertTrue(pos["A"]["peer_group_is_sector"])

    def test_industry_preferred_over_sector(self):
        pos = MP.compute_peer_positions([
            entry("A", 10, "Widgets", "Tech"), entry("B", 5, "Widgets", "Tech")])
        self.assertEqual(pos["A"]["peer_group"], "Widgets")
        self.assertFalse(pos["A"]["peer_group_is_sector"])

    def test_unknown_sector_is_not_a_group(self):
        pos = MP.compute_peer_positions([entry("A", 10, None, "Unknown")])
        self.assertIsNone(pos["A"]["peer_group"])
        self.assertIsNone(pos["A"]["peer_rank"])

    def test_missing_cap_is_unranked_but_present(self):
        pos = MP.compute_peer_positions(
            [entry("A", None, "Widgets"), entry("B", 5, "Widgets"),
             entry("C", 3, "Widgets")])
        self.assertIn("A", pos)
        self.assertIsNone(pos["A"]["peer_rank"])   # no cap -> not ranked
        self.assertEqual(pos["B"]["peer_rank"], 1)
        self.assertEqual(pos["B"]["peer_count"], 2)

    def test_sole_tracked_peer_is_not_ranked(self):
        # "#1 of 1" reads like dominance but only means nothing else in the
        # library shares the industry
        pos = MP.compute_peer_positions([entry("A", 10, "Widgets")])
        self.assertIsNone(pos["A"]["peer_rank"])
        self.assertIsNone(pos["A"]["position_label"])
        self.assertEqual(pos["A"]["peer_group"], "Widgets")

    def test_non_numeric_cap_ignored(self):
        pos = MP.compute_peer_positions(
            [entry("A", "n/a", "Widgets"), entry("B", 5, "Widgets")])
        self.assertIsNone(pos["A"]["peer_rank"])


class TestOverlay(unittest.TestCase):
    def setUp(self):
        self._orig = MP.load_market_structure
        MP.load_market_structure = lambda: {
            "ASML": {"structure": "EUV monopoly", "note": "sole supplier"}}

    def tearDown(self):
        MP.load_market_structure = self._orig

    def test_overlay_attaches_alongside_computed_rank(self):
        pos = MP.compute_peer_positions(
            group([("AMAT", 60), ("ASML", 40)], "SemiEquip"))
        # computed rank is still reported; the UI prefers `structure`
        self.assertEqual(pos["ASML"]["peer_rank"], 2)
        self.assertEqual(pos["ASML"]["structure"], "EUV monopoly")
        self.assertEqual(pos["ASML"]["structure_note"], "sole supplier")
        self.assertIsNone(pos["AMAT"]["structure"])

    def test_overlay_reaches_unrankable_tickers(self):
        pos = MP.compute_peer_positions([entry("ASML", None, None, "Unknown")])
        self.assertEqual(pos["ASML"]["structure"], "EUV monopoly")


class TestAttach(unittest.TestCase):
    def test_attach_merges_into_rows(self):
        rows = group([("A", 60), ("B", 40)])
        MP.attach_peer_positions(rows)
        self.assertEqual(rows[0]["peer_rank"], 1)
        self.assertEqual(rows[1]["peer_rank"], 2)

    def test_attach_can_rank_against_a_wider_set(self):
        allrows = group([("A", 60), ("B", 30), ("C", 10)])
        subset = [dict(allrows[2])]           # just C
        MP.attach_peer_positions(subset, entries=allrows)
        # ranked against all three, not alone
        self.assertEqual(subset[0]["peer_rank"], 3)
        self.assertEqual(subset[0]["peer_count"], 3)


class TestLiveOverlayFile(unittest.TestCase):
    def test_ships_without_unverified_claims(self):
        # the file is documentation-only until the user curates it; a seeded
        # market-share claim would be an unverifiable assertion in a tool
        # that informs trades
        self.assertEqual(MP.load_market_structure(), {})


if __name__ == "__main__":
    unittest.main()
