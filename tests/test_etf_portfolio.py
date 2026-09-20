"""
Tests for core.etf_portfolio — look-through exposure across a book of ETFs.

The claim this file defends: sector exposure is exact (provider sector
weights cover the whole fund), single-stock look-through is a floor (only
disclosed holdings are visible), and the two are never conflated.
Run with: python -m unittest tests.test_etf_portfolio
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import etf_portfolio as PF


def _profiles():
    return {
        "SMH": {"ticker": "SMH", "category": "Technology",
                "theme_label": "Semiconductors",
                "sectors": {"technology": 100.0},
                "asset_mix": {"stock": 99.9},
                "top10_weight": 71.7,
                "holdings": [{"ticker": "NVDA", "name": "NVIDIA", "weight": 20.8}]},
        # Provider sector weights always sum to 100 — that completeness is
        # what makes the blend exact, so the fixtures mirror it.
        "VTI": {"ticker": "VTI", "category": "Large Blend",
                "sectors": {"technology": 36.07, "financial_services": 11.76,
                            "healthcare": 9.7, "industrials": 10.15,
                            "consumer_cyclical": 32.32},
                "asset_mix": {"stock": 99.3}, "top10_weight": 31.9,
                "holdings": [{"ticker": "NVDA", "name": "NVIDIA", "weight": 6.0}]},
        "VXUS": {"ticker": "VXUS", "category": "Foreign Large Blend",
                 "sectors": {"technology": 22.59, "financial_services": 22.16,
                             "industrials": 55.25},
                 "asset_mix": {"stock": 97.3}, "top10_weight": 14.4},
        "GLD": {"ticker": "GLD", "category": "Commodities Focused",
                "sectors": {}, "asset_mix": {"stock": 0.0, "other": 100.0},
                "holdings": []},
        "IWM": {"ticker": "IWM", "category": "Small Blend",
                "theme_label": "Small Blend",
                "sectors": {"healthcare": 20.04, "financial_services": 18.6,
                            "industrials": 61.36},
                "asset_mix": {"stock": 99.9}, "top10_weight": 3.2},
    }


class TestNormalize(unittest.TestCase):
    def test_scales_to_one_hundred(self):
        out = PF.normalize_allocations({"A": 30, "B": 20})
        self.assertAlmostEqual(sum(out.values()), 100.0)
        self.assertAlmostEqual(out["A"], 60.0)

    def test_drops_non_positive_and_junk(self):
        out = PF.normalize_allocations({"A": 50, "B": 0, "C": -5, "": 10,
                                        "D": "x"})
        self.assertEqual(list(out), ["A"])

    def test_empty_is_empty(self):
        self.assertEqual(PF.normalize_allocations({}), {})
        self.assertEqual(PF.normalize_allocations({"A": 0}), {})


class TestSectorExposure(unittest.TestCase):
    def test_blends_proportionally(self):
        # 50% of a 100%-tech fund + 50% of a 36.07%-tech fund
        out = PF.sector_exposure({"SMH": 50, "VTI": 50}, _profiles())
        tech = [s for s in out["sectors"] if s["sector"] == "Technology"][0]
        self.assertAlmostEqual(tech["pct"], 50 + 50 * 0.3607, places=1)

    def test_non_equity_is_bucketed_not_dropped(self):
        # Dropping GLD would renormalize SMH to 100% and overstate tech.
        out = PF.sector_exposure({"SMH": 50, "GLD": 50}, _profiles())
        by = {s["sector"]: s["pct"] for s in out["sectors"]}
        self.assertAlmostEqual(by["Technology"], 50.0, places=1)
        self.assertAlmostEqual(by[PF.NON_EQUITY], 50.0, places=1)

    def test_an_unknown_fund_is_reported_not_silently_ignored(self):
        out = PF.sector_exposure({"SMH": 50, "NOPE": 50}, _profiles())
        self.assertAlmostEqual(out["unknown"], 50.0, places=1)

    def test_sector_labels_are_humanised(self):
        out = PF.sector_exposure({"VTI": 100}, _profiles())
        labels = [s["sector"] for s in out["sectors"]]
        self.assertIn("Financial Services", labels)


class TestRoles(unittest.TestCase):
    def test_known_funds_use_the_default_table(self):
        p = _profiles()
        self.assertEqual(PF.role_of(p["VTI"], "VTI"), "Core")
        self.assertEqual(PF.role_of(p["SMH"], "SMH"), "Theme")
        self.assertEqual(PF.role_of(p["GLD"], "GLD"), "Commodity")

    def test_international_is_not_matched_on_theme_words(self):
        # VXUS's label is "Foreign Large Blend" — a naive search for
        # "international" finds nothing, which silently zeroed the alert.
        self.assertEqual(PF.role_of(_profiles()["VXUS"], "VXUS"),
                         "International")

    def test_a_user_override_wins(self):
        self.assertEqual(
            PF.role_of({"category": "Technology", PF.ROLE_KEY: "Core"}, "SMH"),
            "Core")

    def test_an_invalid_override_is_ignored(self):
        self.assertEqual(
            PF.role_of({"category": "Technology", PF.ROLE_KEY: "Bogus"}, "SMH"),
            "Theme")

    def test_inference_for_an_unknown_fund(self):
        self.assertEqual(
            PF.role_of({"category": "Foreign Large Value"}, "ZZZ"),
            "International")
        self.assertEqual(
            PF.role_of({"category": "x", "asset_mix": {"stock": 0.0}}, "ZZZ"),
            "Commodity")


class TestStockLookthrough(unittest.TestCase):
    def test_sums_the_same_company_across_funds(self):
        # NVDA at 20.8% of SMH and 6% of VTI
        out = PF.stock_lookthrough({"SMH": 50, "VTI": 50}, _profiles())
        nvda = [s for s in out if s["ticker"] == "NVDA"][0]
        self.assertAlmostEqual(nvda["pct"], 50 * .208 + 50 * .06, places=2)
        self.assertEqual(nvda["via"], ["SMH", "VTI"])

    def test_coverage_reports_how_much_is_actually_visible(self):
        # IWM discloses 3.2%, so a book of it is nearly invisible
        self.assertAlmostEqual(
            PF.disclosure_coverage({"IWM": 100}, _profiles()), 3.2, places=1)

    def test_coverage_blends_across_the_book(self):
        cov = PF.disclosure_coverage({"SMH": 50, "IWM": 50}, _profiles())
        self.assertAlmostEqual(cov, (71.7 + 3.2) / 2, places=1)


class TestAnalyze(unittest.TestCase):
    def test_flags_a_technology_heavy_book(self):
        r = PF.analyze({"SMH": 60, "VTI": 40}, _profiles())
        self.assertTrue(r["ok"])
        self.assertGreater(r["totals"]["technology"], 45)
        self.assertTrue(any(a["level"] == "red" for a in r["alerts"]))

    def test_a_balanced_book_passes_the_international_check(self):
        r = PF.analyze({"VTI": 60, "VXUS": 40}, _profiles())
        self.assertEqual(r["totals"]["international"], 40.0)
        intl = [a for a in r["alerts"] if "International" in a["text"]][0]
        self.assertEqual(intl["level"], "green")

    def test_no_core_holding_is_called_out(self):
        r = PF.analyze({"SMH": 50, "GLD": 50}, _profiles())
        self.assertTrue(any("core" in a["text"].lower() for a in r["alerts"]))

    def test_red_alerts_sort_first(self):
        r = PF.analyze({"SMH": 90, "GLD": 10}, _profiles())
        self.assertEqual(r["alerts"][0]["level"], "red")

    def test_empty_allocation_is_rejected_cleanly(self):
        self.assertFalse(PF.analyze({}, _profiles())["ok"])

    def test_allocations_are_echoed_normalised(self):
        r = PF.analyze({"SMH": 30, "VTI": 30}, _profiles())
        self.assertAlmostEqual(sum(a["pct"] for a in r["allocations"]), 100.0,
                               places=1)


if __name__ == "__main__":
    unittest.main()
