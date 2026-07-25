"""
Tests for core.buy_zone — the "is this a good price to add" score, kept
separate from strategy_scores.Investment_Score's "is this worth owning"
question. Pure functions, no network.
Run with: python -m unittest tests.test_buy_zone
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.buy_zone import compute_buy_zone


def _full_row(**overrides) -> dict:
    """A high-quality name pulled back into the institutional accumulation
    zone: strong fundamentals, intact uptrend, cheap-ish valuation, light
    pullback volume, institutions adding, RS leader, beat last quarter."""
    row = {
        # fundamentals (business quality inputs)
        "GrossMargin%": 65.0, "OperatingMargin%": 30.0, "ReturnOnEquity%": 30.0,
        "FCF_Margin%": 18.0, "Revenue": 22.0, "EPS_Growth%": 28.0,
        "DebtToEquity": 40.0, "Inst_Own%": 55.0,
        # technical
        "Above_200MA": True, "Price_vs_50MA%": 2.0, "RS_Rank": 85, "ADX_14": 28.0,
        # valuation
        "PEG_Ratio": 0.9, "Forward_PE": 22.0,
        # pullback / volume
        "Dist_52W_High%": -9.0, "Pullback_Vol_Ratio": 0.6, "VolumeDryingUp": True,
        # institutional buying
        "Inst_Own_Chg": 3.0,
        # catalysts
        "EarningsBeat": True,
    }
    row.update(overrides)
    return row


class TestComputeBuyZone(unittest.TestCase):
    def test_ideal_accumulation_profile_scores_strong_buy_zone(self):
        result = compute_buy_zone(_full_row())
        self.assertGreaterEqual(result["score"], 90)
        self.assertEqual(result["label"], "Strong Buy Zone")
        self.assertEqual(result["weight_covered"], 100)

    def test_weak_profile_scores_avoid(self):
        row = _full_row(**{
            "GrossMargin%": 12.0, "OperatingMargin%": 4.0, "ReturnOnEquity%": 5.0,
            "FCF_Margin%": -3.0, "Revenue": -5.0, "EPS_Growth%": -10.0,
            "DebtToEquity": 250.0, "Inst_Own%": 10.0,
            "Above_200MA": False, "Price_vs_50MA%": -8.0, "RS_Rank": 20, "ADX_14": 15.0,
            "PEG_Ratio": 4.0,
            "Dist_52W_High%": -45.0, "Pullback_Vol_Ratio": 1.8, "VolumeDryingUp": False,
            "Inst_Own_Chg": -1.0,
            "EarningsBeat": False,
        })
        result = compute_buy_zone(row)
        self.assertLess(result["score"], 60)
        self.assertEqual(result["label"], "Avoid")

    def test_chasing_at_52w_high_scores_lower_than_moderate_pullback(self):
        at_high = compute_buy_zone(_full_row(**{"Dist_52W_High%": -0.5}))
        pulled_back = compute_buy_zone(_full_row(**{"Dist_52W_High%": -9.0}))
        self.assertLess(at_high["score"], pulled_back["score"])

    def test_deep_drawdown_scores_lower_than_moderate_pullback(self):
        deep = compute_buy_zone(_full_row(**{"Dist_52W_High%": -50.0}))
        pulled_back = compute_buy_zone(_full_row(**{"Dist_52W_High%": -9.0}))
        self.assertLess(deep["score"], pulled_back["score"])

    def test_missing_most_factors_returns_no_score(self):
        result = compute_buy_zone({"RS_Rank": 80})
        self.assertIsNone(result["score"])
        self.assertIsNone(result["label"])

    def test_partial_data_still_scores_when_weight_covered_is_enough(self):
        # Only fundamentals + technicals present. Inst_Own% and RS_Rank each
        # do double duty (Institutional Buying / Relative Strength are their
        # own factors on top of feeding Fundamentals / Technical Trend), so
        # covered weight is 30 + 20 + 5 (inst) + 5 (RS) = 60%. Valuation,
        # pullback, volume, and catalysts are all missing.
        row = {
            "GrossMargin%": 65.0, "OperatingMargin%": 30.0, "ReturnOnEquity%": 30.0,
            "FCF_Margin%": 18.0, "Revenue": 22.0, "EPS_Growth%": 28.0,
            "DebtToEquity": 40.0, "Inst_Own%": 55.0,
            "Above_200MA": True, "Price_vs_50MA%": 2.0, "RS_Rank": 85, "ADX_14": 28.0,
        }
        result = compute_buy_zone(row)
        self.assertIsNotNone(result["score"])
        self.assertEqual(result["weight_covered"], 60)

    def test_missing_peg_falls_back_to_forward_pe(self):
        row = _full_row(**{"PEG_Ratio": None, "Forward_PE": 12.0})
        result = compute_buy_zone(row)
        self.assertTrue(any("Fwd P/E" in d for d in result["drivers"]))

    def test_error_row_does_not_crash(self):
        result = compute_buy_zone({"Ticker": "ERR", "Category": "Error"})
        self.assertIsNone(result["score"])


if __name__ == "__main__":
    unittest.main()
