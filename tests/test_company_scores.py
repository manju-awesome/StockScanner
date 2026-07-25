"""
Tests for core.company_scores — Business Quality, Economic Moat, and
Financial Health, the research page's Company Overview proxies. Pure
functions, no network.
Run with: python -m unittest tests.test_company_scores
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.company_scores import (
    compute_business_quality, compute_economic_moat, compute_financial_health)


def _quality_row(**kw):
    base = {"GrossMargin%": None, "OperatingMargin%": None,
            "ReturnOnEquity%": None, "FCF_Margin%": None, "FCF_Positive": None,
            "Revenue": None, "EPS_Growth%": None, "DebtToEquity": None,
            "Inst_Own%": None}
    base.update(kw)
    return base


def _health_row(**kw):
    base = {"CurrentRatio": None, "QuickRatio": None, "DebtToEquity": None,
            "TotalCash": None, "TotalDebt": None, "FCF_Margin%": None,
            "FCF_Positive": None}
    base.update(kw)
    return base


class TestComputeBusinessQuality(unittest.TestCase):
    def test_franchise_quality_profile_scores_strong(self):
        # MSFT-like: fat margins, high ROE, growing, low leverage, held
        m = compute_business_quality(_quality_row(**{
            "GrossMargin%": 69.0, "OperatingMargin%": 45.0, "ReturnOnEquity%": 38.0,
            "FCF_Margin%": 20.0, "Revenue": 22.0, "EPS_Growth%": 25.0,
            "DebtToEquity": 40.0, "Inst_Own%": 74.0}))
        self.assertEqual(m["score"], 100)
        self.assertEqual(m["label"], "Strong")
        self.assertEqual(m["inputs_used"], 8)

    def test_commodity_profile_scores_weak(self):
        m = compute_business_quality(_quality_row(**{
            "GrossMargin%": 12.0, "OperatingMargin%": 4.0, "ReturnOnEquity%": 5.0,
            "FCF_Margin%": -3.0, "Revenue": -5.0, "EPS_Growth%": -10.0,
            "DebtToEquity": 250.0, "Inst_Own%": 10.0}))
        self.assertEqual(m["score"], 0)
        self.assertEqual(m["label"], "Weak")

    def test_market_cap_is_not_an_input(self):
        """Market cap should have zero influence on quality — it measures
        size, not the quality of the underlying financials."""
        row = _quality_row(**{"GrossMargin%": 60.0, "OperatingMargin%": 25.0,
                               "ReturnOnEquity%": 25.0, "FCF_Margin%": 15.0})
        row["MarketCap"] = 1e9
        m_small = compute_business_quality(row)
        row["MarketCap"] = 3e12
        m_large = compute_business_quality(row)
        self.assertEqual(m_small["score"], m_large["score"])
        self.assertNotIn("MarketCap", str(m_small["drivers"]))

    def test_fcf_margin_used_over_fcf_positive_when_both_present(self):
        m = compute_business_quality(_quality_row(**{
            "GrossMargin%": 60.0, "OperatingMargin%": 25.0, "ReturnOnEquity%": 25.0,
            "FCF_Margin%": 18.0, "FCF_Positive": True}))
        self.assertIn("FCF margin 18% (+12)", m["drivers"])

    def test_falls_back_to_fcf_positive_boolean(self):
        m = compute_business_quality(_quality_row(**{
            "GrossMargin%": 60.0, "OperatingMargin%": 25.0, "ReturnOnEquity%": 25.0,
            "FCF_Positive": True}))
        self.assertIn("FCF positive (+6)", m["drivers"])

    def test_insufficient_inputs_returns_no_score(self):
        m = compute_business_quality(_quality_row(**{"GrossMargin%": 60.0, "OperatingMargin%": 25.0}))
        self.assertIsNone(m["score"])
        self.assertIsNone(m["label"])
        self.assertIn("2 of 8", m["drivers"][0])

    def test_low_debt_to_equity_scores_full_points(self):
        m = compute_business_quality(_quality_row(**{
            "GrossMargin%": 60.0, "OperatingMargin%": 25.0, "ReturnOnEquity%": 25.0,
            "DebtToEquity": 30.0}))
        self.assertIn("Debt/Equity 30 (+12)", m["drivers"])


class TestComputeEconomicMoat(unittest.TestCase):
    def test_all_elite_scores_strong_signals(self):
        m = compute_economic_moat({"GrossMargin%": 69.0, "OperatingMargin%": 45.0,
                                    "ReturnOnEquity%": 38.0, "Revenue": 25.0})
        self.assertEqual(m["passed"], 4)
        self.assertEqual(m["total"], 4)
        self.assertEqual(m["label"], "Strong signals")

    def test_none_elite_scores_weak_signals(self):
        m = compute_economic_moat({"GrossMargin%": 30.0, "OperatingMargin%": -9.0,
                                    "ReturnOnEquity%": -6.0, "Revenue": 2.0})
        self.assertEqual(m["passed"], 0)
        self.assertEqual(m["label"], "Weak signals")

    def test_market_cap_and_institutional_ownership_are_not_checks(self):
        """Direct regression for the feedback that drove this redesign:
        market cap is not evidence of moat, and institutional ownership
        doesn't create one — neither should appear in the checklist."""
        m = compute_economic_moat({"GrossMargin%": 69.0, "OperatingMargin%": 45.0,
                                    "ReturnOnEquity%": 38.0, "Revenue": 25.0,
                                    "MarketCap": 3e12, "Inst_Own%": 90.0})
        names = [c["name"] for c in m["checks"]]
        self.assertNotIn("Market cap", " ".join(names))
        self.assertNotIn("Institutional", " ".join(names))
        self.assertEqual(m["total"], 4)  # only the 4 defined checks counted

    def test_insufficient_inputs_returns_no_label(self):
        m = compute_economic_moat({"GrossMargin%": 90.0})
        self.assertIsNone(m["label"])
        self.assertIsNone(m["passed"])

    def test_half_passing_is_moderate_signals(self):
        m = compute_economic_moat({"GrossMargin%": 69.0, "OperatingMargin%": 45.0,
                                    "ReturnOnEquity%": 5.0, "Revenue": 2.0})
        self.assertEqual(m["passed"], 2)
        self.assertEqual(m["label"], "Moderate signals")


class TestComputeFinancialHealth(unittest.TestCase):
    def test_fortress_balance_sheet_scores_strong(self):
        m = compute_financial_health(_health_row(**{
            "CurrentRatio": 2.5, "QuickRatio": 2.0, "DebtToEquity": 20.0,
            "TotalCash": 50e9, "TotalDebt": 10e9, "FCF_Margin%": 25.0}))
        self.assertEqual(m["score"], 100)
        self.assertEqual(m["label"], "Strong")

    def test_distressed_balance_sheet_scores_weak(self):
        m = compute_financial_health(_health_row(**{
            "CurrentRatio": 0.6, "QuickRatio": 0.3, "DebtToEquity": 300.0,
            "TotalCash": 0.0, "TotalDebt": 20e9, "FCF_Margin%": -10.0}))
        self.assertEqual(m["score"], 0)
        self.assertEqual(m["label"], "Weak")

    def test_net_cash_position_scores_full_points(self):
        m = compute_financial_health(_health_row(**{
            "CurrentRatio": 2.0, "QuickRatio": 1.5, "DebtToEquity": 40.0,
            "TotalCash": 20e9, "TotalDebt": 5e9}))
        self.assertIn("Cash $20.0B vs debt $5.0B (+20)", m["drivers"])

    def test_insufficient_inputs_returns_no_score(self):
        m = compute_financial_health(_health_row(**{"CurrentRatio": 1.0}))
        self.assertIsNone(m["score"])
        self.assertIn("1 of 5", m["drivers"][0])


if __name__ == "__main__":
    unittest.main()
