"""
Tests for core.buy_zone — the Buy Zone Score, "is NOW a good price to enter".

Purely technical since 2026-08-07. It previously blended 70% fundamentals,
which meant it mostly restated how good the company was and could not
disagree with the investment scores. NVDA read 81 ("Buy Zone") at $222 while
+8.1% above its 50 MA and +4.8% above its 8 EMA — a superb business at a
poor entry.
Run with: python -m unittest tests.test_buy_zone
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.buy_zone import (
    compute_buy_zone, is_extended, EXTENDED_CAP)


def _row(**kw):
    """A healthy pullback: under the 8 EMA, 8% off the high, trend intact,
    volume drying up."""
    base = {
        "Pct_vs_8EMA": -1.0, "Dist_52W_High%": -8.0, "Above_200MA": True,
        "Price_vs_50MA%": 2.0, "Pullback_Vol_Ratio": 0.65,
        "VolumeDryingUp": True, "RS_Rank": 88, "RSI_14": 52.0,
    }
    base.update(kw)
    return base


class TestScoring(unittest.TestCase):
    def test_ideal_accumulation_profile_scores_a_buy_zone(self):
        r = compute_buy_zone(_row())
        self.assertGreaterEqual(r["score"], 80)
        self.assertIn(r["label"], ("Buy Zone", "Strong Buy Zone"))
        self.assertEqual(r["weight_covered"], 100)

    def test_a_broken_chart_scores_avoid(self):
        r = compute_buy_zone(_row(**{
            "Pct_vs_8EMA": -14.0, "Dist_52W_High%": -45.0,
            "Above_200MA": False, "Price_vs_50MA%": -18.0,
            "Pullback_Vol_Ratio": 1.8, "VolumeDryingUp": False,
            "RS_Rank": 8, "RSI_14": 28.0}))
        self.assertLess(r["score"], 60)
        self.assertEqual(r["label"], "Avoid")

    def test_fundamentals_no_longer_influence_the_score(self):
        # The whole point of the rewrite: a great business at a bad price
        # must not score well, and these fields must be ignored entirely.
        chase = _row(**{"Pct_vs_8EMA": 6.0, "Price_vs_50MA%": 12.0,
                        "RSI_14": 72.0, "RS_Rank": 35})
        plain = compute_buy_zone(chase)
        with_fundamentals = compute_buy_zone(dict(chase, **{
            "GrossMargin%": 90, "OperatingMargin%": 50, "ReturnOnEquity%": 60,
            "PEG_Ratio": 0.4, "Forward_PE": 12, "Inst_Own%": 90,
            "EarningsBeat": True}))
        self.assertEqual(plain["score"], with_fundamentals["score"])


class TestExtensionCap(unittest.TestCase):
    """Extended and 'good entry' cannot both be true."""

    def test_extended_above_the_8ema_cannot_be_a_buy_zone(self):
        r = compute_buy_zone(_row(Pct_vs_8EMA=6.0))
        self.assertLessEqual(r["score"], EXTENDED_CAP)
        self.assertNotIn(r["label"], ("Buy Zone", "Strong Buy Zone"))

    def test_extended_above_the_50ma_cannot_be_a_buy_zone(self):
        # NVDA's actual shape: modest pullback from the high, but stretched
        # over the 50-day.
        r = compute_buy_zone(_row(**{"Dist_52W_High%": -5.7,
                                     "Price_vs_50MA%": 9.0,
                                     "Pct_vs_8EMA": 2.0}))
        self.assertLessEqual(r["score"], EXTENDED_CAP)

    def test_the_cap_is_recorded_in_the_drivers(self):
        r = compute_buy_zone(_row(Pct_vs_8EMA=6.0))
        self.assertTrue(any("capped" in d for d in r["drivers"]))

    def test_a_price_under_the_averages_is_not_extended(self):
        self.assertFalse(is_extended(_row())[0])

    def test_the_cap_never_raises_a_low_score(self):
        low = compute_buy_zone(_row(**{
            "Pct_vs_8EMA": 9.0, "Above_200MA": False, "RS_Rank": 5,
            "RSI_14": 80.0, "Pullback_Vol_Ratio": 2.0,
            "VolumeDryingUp": False}))
        self.assertLess(low["score"], EXTENDED_CAP)


class TestEntryProximity(unittest.TestCase):
    def test_below_the_ema_beats_the_same_distance_above_it(self):
        # A pullback and a chase are not equivalent.
        below = compute_buy_zone(_row(Pct_vs_8EMA=-3.0))
        above = compute_buy_zone(_row(Pct_vs_8EMA=3.0))
        self.assertGreater(below["score"], above["score"])

    def test_right_at_the_high_scores_worse_than_a_healthy_pullback(self):
        at_high = compute_buy_zone(_row(**{"Dist_52W_High%": -1.0}))
        pullback = compute_buy_zone(_row(**{"Dist_52W_High%": -9.0}))
        self.assertGreater(pullback["score"], at_high["score"])


class TestMissingData(unittest.TestCase):
    def test_partial_data_still_scores_when_enough_weight_is_covered(self):
        r = compute_buy_zone({"Pct_vs_8EMA": -1.0, "Dist_52W_High%": -8.0,
                              "Above_200MA": True, "Price_vs_50MA%": 2.0})
        self.assertIsNotNone(r["score"])
        self.assertEqual(r["weight_covered"], 65)

    def test_too_little_data_scores_none_rather_than_guessing(self):
        r = compute_buy_zone({"RS_Rank": 90})
        self.assertIsNone(r["score"])
        self.assertIsNone(r["label"])

    def test_a_missing_factor_is_not_scored_as_zero(self):
        full = compute_buy_zone(_row())
        no_rsi = compute_buy_zone(_row(RSI_14=None))
        self.assertGreater(no_rsi["score"], full["score"] - 12)

    def test_empty_row(self):
        self.assertIsNone(compute_buy_zone({})["score"])


if __name__ == "__main__":
    unittest.main()
