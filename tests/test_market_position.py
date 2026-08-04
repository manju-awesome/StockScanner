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

    # Tier labels need >= MIN_PEERS_FOR_TIER peers, so these fixtures are
    # padded out with small names that don't move the concentration maths.
    TAIL = [("T%d" % i, 1) for i in range(6)]

    def test_dominant_when_majority(self):
        pos = MP.compute_peer_positions(
            group([("A", 300), ("B", 100), ("C", 60)] + self.TAIL))
        self.assertEqual(pos["A"]["position_tier"], "dominant")
        self.assertEqual(pos["A"]["position_label"], "Dominant")

    def test_real_duopoly(self):
        # 45 + 35 = 80% top-2, and the runner-up is substantial
        pos = MP.compute_peer_positions(
            group([("A", 450), ("B", 350), ("C", 120), ("D", 74)] + self.TAIL))
        self.assertEqual(pos["A"]["position_tier"], "duopoly")
        self.assertEqual(pos["B"]["position_tier"], "duopoly")
        self.assertEqual(pos["C"]["position_tier"], "rest")

    def test_giant_plus_alsoran_is_not_a_duopoly(self):
        # regression: MSFT 71% + ORCL 8% cleared the top-2 bar on the giant
        # alone and mislabelled the distant #2 a duopolist
        pos = MP.compute_peer_positions(
            group([("A", 710), ("B", 80), ("C", 70), ("D", 70)] + self.TAIL))
        self.assertEqual(pos["A"]["position_tier"], "dominant")
        self.assertEqual(pos["B"]["position_tier"], "top2")
        self.assertEqual(pos["B"]["position_label"], "#2")

    def test_thin_group_gets_a_rank_but_no_tier(self):
        # regression: APH read "Dominant /5" on a 5-name group, then flipped
        # between Duopoly and Dominant as peers came and went. A concentration
        # word over a handful of tracked names describes the sample, not the
        # market — so below MIN_PEERS_FOR_TIER only the bare rank shows.
        pos = MP.compute_peer_positions(
            group([("A", 51), ("B", 32), ("C", 10), ("D", 4), ("E", 3)]))
        self.assertEqual(pos["A"]["peer_share_pct"], 51.0)   # over the 50% bar
        self.assertEqual(pos["A"]["position_tier"], "rest")  # ...but too thin
        self.assertEqual(pos["A"]["position_label"], "#1")

    def test_tier_appears_once_the_group_is_deep_enough(self):
        caps = [("A", 51), ("B", 32), ("C", 10), ("D", 4), ("E", 3)]
        thin = MP.compute_peer_positions(group(caps))
        deep = MP.compute_peer_positions(group(caps + self.TAIL))
        self.assertEqual(thin["A"]["position_tier"], "rest")     # 5 peers
        self.assertEqual(thin["A"]["position_label"], "#1")
        # 11 peers: a tier is allowed. The 6 padding names add cap, so A lands
        # at 48.1% — under the 50% Dominant bar — while A+B reach 78.3% with a
        # 30.2% runner-up, which is a genuine Duopoly.
        self.assertEqual(deep["A"]["position_tier"], "duopoly")
        self.assertEqual(deep["B"]["position_tier"], "duopoly")

    def test_groups_are_independent(self):
        rows = group([("A", 60), ("B", 40)], "Widgets") + \
               group([("X", 90), ("Y", 10)], "Gadgets")
        pos = MP.compute_peer_positions(rows)
        self.assertEqual(pos["A"]["peer_group"], "Widgets")
        self.assertEqual(pos["X"]["peer_group"], "Gadgets")
        self.assertEqual(pos["A"]["peer_count"], 2)


class TestPeerNames(unittest.TestCase):
    def test_lists_the_other_members_in_rank_order(self):
        pos = MP.compute_peer_positions(
            group([("A", 60), ("B", 30), ("C", 10)]))
        self.assertEqual(pos["A"]["peer_names"], "B, C")
        self.assertEqual(pos["B"]["peer_names"], "A, C")
        self.assertEqual(pos["C"]["peer_names"], "A, B")

    def test_excludes_self(self):
        pos = MP.compute_peer_positions(group([("A", 60), ("B", 30)]))
        for t, p in pos.items():
            self.assertNotIn(t, p["peer_names"].split(", "))

    def test_unranked_ticker_has_empty_peer_names(self):
        pos = MP.compute_peer_positions([entry("A", None, None, "Unknown")])
        self.assertEqual(pos["A"]["peer_names"], "")

    def test_excludes_peers_with_no_market_cap(self):
        # an unranked name isn't in the group, so it can't be someone's peer
        pos = MP.compute_peer_positions(
            [entry("A", 60, "W"), entry("B", 30, "W"), entry("C", None, "W")])
        self.assertEqual(pos["A"]["peer_names"], "B")


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
