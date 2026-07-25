"""
Tests for scanners.market_movers._classify_headline / CATALYST_PATTERNS —
covers the two ordering bugs found while building the Breaking News
monitor (core.news_monitor) plus the two new categories added for it
(Executive Departure, Credit Downgrade).
Run with: python -m unittest tests.test_catalyst_classification
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.scanners.market_movers import _classify_headline


class TestClassifyHeadline(unittest.TestCase):
    def test_fda_approval_not_shadowed_by_product_launch(self):
        """Regression test: 'FDA approves new drug' used to match Product
        Launch's 'new drug' pattern first, since it was checked before FDA
        Approval in CATALYST_PATTERNS (first-match-wins)."""
        self.assertEqual(
            _classify_headline("FDA approves new drug for diabetes treatment"),
            "FDA Approval")

    def test_product_launch_still_works_without_fda_context(self):
        self.assertEqual(
            _classify_headline("Company launches new product platform"),
            "Product Launch")

    def test_executive_departure_with_words_between_ceo_and_verb(self):
        """Regression test: the original pattern required CEO and the
        resignation verb to be adjacent, missing phrasings like "CEO
        announces resignation" with a word in between."""
        self.assertEqual(
            _classify_headline("CEO announces resignation effective immediately"),
            "Executive Departure")

    def test_executive_departure_verb_before_ceo(self):
        self.assertEqual(
            _classify_headline("Company confirms CEO steps down amid controversy"),
            "Executive Departure")

    def test_credit_downgrade_not_shadowed_by_analyst_downgrade(self):
        """Regression test: 'Moody's downgrades...' used to match the
        generic Analyst Downgrade pattern first, since Credit Downgrade
        wasn't checked until after it."""
        self.assertEqual(
            _classify_headline("Moody's downgrades credit rating to junk status"),
            "Credit Downgrade")

    def test_credit_downgrade_verb_before_agency(self):
        self.assertEqual(
            _classify_headline("S&P cuts credit rating on weak cash flow"),
            "Credit Downgrade")

    def test_sp500_index_move_is_not_misclassified_as_credit_downgrade(self):
        """Regression test: the Credit Downgrade pattern must require the
        word "rating" present too, or "S&P 500 cuts losses" (an index move,
        nothing to do with a credit rating agency) would false-positive on
        just seeing "S&P" + "cuts"."""
        self.assertEqual(
            _classify_headline("S&P 500 cuts losses in late trading"),
            "General News")

    def test_analyst_downgrade_still_works(self):
        self.assertEqual(
            _classify_headline("Analyst downgrades stock to sell rating"),
            "Analyst Downgrade")

    def test_acquisition_still_classified(self):
        self.assertEqual(
            _classify_headline("Company announces acquisition of rival firm"),
            "M&A / Acquisition")

    def test_unmatched_headline_falls_back_to_general_news(self):
        self.assertEqual(
            _classify_headline("Company reports quarterly results"),
            "General News")


if __name__ == "__main__":
    unittest.main()
