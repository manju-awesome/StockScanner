"""
Tests for reporting.research._company_overview_html — the per-ticker
research page's company description + margin figures. No network calls;
operates on synthetic row dicts.
Run with: python -m unittest tests.test_company_overview
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.reporting.research import _company_overview_html

LONG_SUMMARY = (
    "Acme Corp designs and manufactures widgets for the industrial sector. "
    "The company sells through direct and third-party channels. "
    "It was founded in 1990 and is headquartered in Springfield."
)


class TestCompanyOverviewHtml(unittest.TestCase):
    def test_missing_summary_shows_fallback_not_empty_page(self):
        html = _company_overview_html({"Ticker": "ZZZZ"})
        self.assertIn("No company description available", html)

    def test_brief_description_trims_to_first_two_sentences(self):
        """"Brief" means brief — a multi-sentence boilerplate summary
        shouldn't dump the entire paragraph onto the research page."""
        html = _company_overview_html({"BusinessSummary": LONG_SUMMARY})
        self.assertIn("designs and manufactures widgets", html)
        self.assertIn("direct and third-party channels", html)
        self.assertNotIn("founded in 1990", html)  # third sentence, trimmed

    def test_margin_figures_are_shown_when_present(self):
        html = _company_overview_html({
            "BusinessSummary": LONG_SUMMARY,
            "GrossMargin%": 74.1, "OperatingMargin%": 65.6, "ReturnOnEquity%": 114.3,
        })
        self.assertIn("74.1%", html)
        self.assertIn("65.6%", html)
        self.assertIn("114.3%", html)

    def test_missing_margins_render_as_placeholder_not_crash(self):
        html = _company_overview_html({"BusinessSummary": LONG_SUMMARY})
        self.assertIn("—", html)

    def test_three_scores_shown_with_moat_as_proxy_not_verdict(self):
        """Regression guard, updated for core.company_scores: Business
        Quality and Financial Health are shown as 0-100 scores with
        visible drivers; Economic Moat is a checklist, never a fabricated
        Morningstar-style Wide/Narrow verdict, and the caveat that it's
        one input rather than a rating must stay."""
        html = _company_overview_html({
            "BusinessSummary": LONG_SUMMARY, "GrossMargin%": 62.0,
            "OperatingMargin%": 30.0, "ReturnOnEquity%": 28.0,
            "FCF_Margin%": 20.0, "Revenue": 22.0, "EPS_Growth%": 15.0,
            "DebtToEquity": 30.0, "Inst_Own%": 70.0,
            "CurrentRatio": 2.0, "QuickRatio": 1.5,
            "TotalCash": 20e9, "TotalDebt": 5e9,
        })
        self.assertIn("Business quality", html)
        self.assertIn("Economic moat", html)
        self.assertIn("Financial health", html)
        self.assertIn("Gross margin 62% (+18)", html)
        self.assertNotIn("Wide Moat", html)        # no qualitative verdict
        self.assertNotIn("Narrow Moat", html)
        self.assertIn("not a rating", html)

    def test_scores_degrade_gracefully_on_missing_inputs(self):
        html = _company_overview_html({
            "BusinessSummary": LONG_SUMMARY, "GrossMargin%": 90.0,
        })
        self.assertIn("only 1 of 8 inputs available", html)

    def test_employee_count_is_comma_formatted(self):
        html = _company_overview_html({
            "BusinessSummary": LONG_SUMMARY, "FullTimeEmployees": 42000,
        })
        self.assertIn("42,000", html)

    def test_industry_is_html_escaped(self):
        html = _company_overview_html({
            "BusinessSummary": LONG_SUMMARY, "Industry": "R&D <special>",
        })
        self.assertIn("R&amp;D", html)
        self.assertNotIn("<special>", html)


if __name__ == "__main__":
    unittest.main()
