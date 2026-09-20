"""
Tests for core.etf_profile — the fund-specific data the equity scan can't
produce (theme, holdings, expense ratio, AUM). Pure functions and cache I/O;
the one network entry point (fetch_profile) is exercised through a stub.
Run with: python -m unittest tests.test_etf_profile
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import etf_profile as EP


class TestIsEtfRow(unittest.TestCase):
    def test_sector_etf_is_the_classifier(self):
        self.assertTrue(EP.is_etf_row({"ticker": "SMH", "sector": "ETF"}))
        self.assertTrue(EP.is_etf_row({"ticker": "SMH", "sector": "etf"}))
        self.assertFalse(EP.is_etf_row({"ticker": "NVDA", "sector": "Technology"}))

    def test_falls_back_to_the_raw_scan_row(self):
        self.assertTrue(EP.is_etf_row({"ticker": "GLD", "raw": {"Sector": "ETF"}}))

    def test_empty_is_not_an_etf(self):
        self.assertFalse(EP.is_etf_row({}))
        self.assertFalse(EP.is_etf_row(None))

    def test_etf_tickers_lists_only_funds(self):
        entries = [{"ticker": "SMH", "sector": "ETF"},
                   {"ticker": "NVDA", "sector": "Technology"},
                   {"ticker": "GLD", "sector": "ETF"}]
        self.assertEqual(EP.etf_tickers(entries), ["GLD", "SMH"])


class TestAttachProfiles(unittest.TestCase):
    def _profiles(self):
        return {"SMH": {"category": "Technology", "family": "VanEck",
                        "expense_ratio": 0.35, "aum": 68098555904,
                        "price": 581.8, "yield_pct": 0.2,
                        "holdings": [{"ticker": "NVDA", "weight": 20.83}],
                        "top10_weight": 71.66}}

    def test_fund_fields_land_on_the_row(self):
        rows = [{"ticker": "SMH", "sector": "ETF"}]
        EP.attach_profiles(rows, self._profiles())
        self.assertTrue(rows[0]["etf"])
        self.assertEqual(rows[0]["etf_expense_ratio"], 0.35)
        self.assertEqual(rows[0]["etf_top10_weight"], 71.66)
        self.assertEqual(rows[0]["etf_holdings"][0]["ticker"], "NVDA")

    def test_equities_are_never_given_fund_fields(self):
        # An expense ratio on a stock row would be nonsense.
        rows = [{"ticker": "NVDA", "sector": "Technology"}]
        EP.attach_profiles(rows, {"NVDA": {"expense_ratio": 0.35}})
        self.assertNotIn("etf_expense_ratio", rows[0])

    def test_a_fund_with_no_profile_is_left_alone(self):
        rows = [{"ticker": "SMH", "sector": "ETF"}]
        EP.attach_profiles(rows, {})
        self.assertNotIn("etf_expense_ratio", rows[0])

    def test_an_existing_price_is_not_overwritten(self):
        rows = [{"ticker": "SMH", "sector": "ETF", "price": 999.0}]
        EP.attach_profiles(rows, self._profiles())
        self.assertEqual(rows[0]["price"], 999.0)


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_round_trip(self):
        EP.save_profiles(self.dir, {"SMH": {"expense_ratio": 0.35}})
        self.assertEqual(EP.load_profiles(self.dir)["SMH"]["expense_ratio"], 0.35)

    def test_missing_and_corrupt_files_read_empty(self):
        self.assertEqual(EP.load_profiles(self.dir), {})
        EP.profiles_path(self.dir).write_text("{ not json")
        self.assertEqual(EP.load_profiles(self.dir), {})

    def test_write_is_atomic_and_leaves_no_temp_file(self):
        EP.save_profiles(self.dir, {"SMH": {}})
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class _StubTicker:
    """Stands in for yfinance.Ticker."""

    def __init__(self, info=None, holdings=None, raises=False):
        self._info, self._holdings, self._raises = info, holdings, raises

    @property
    def info(self):
        if self._raises:
            raise RuntimeError("network down")
        return self._info

    @property
    def funds_data(self):
        class _FD:
            top_holdings = self._holdings
        return _FD()


class TestRefreshMergesRatherThanReplaces(unittest.TestCase):
    """A failed fetch must never blank a fund that was already known — the
    network failing is not evidence the fund stopped existing."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._orig = EP.fetch_profile

    def tearDown(self):
        EP.fetch_profile = self._orig

    def test_failure_keeps_the_previous_profile(self):
        EP.save_profiles(self.dir, {"SMH": {"expense_ratio": 0.35,
                                            "category": "Technology"}})
        EP.fetch_profile = lambda t: {"ticker": t, "error": "network down",
                                      "updated_at": "now"}
        result = EP.refresh_profiles(["SMH"], self.dir)
        saved = EP.load_profiles(self.dir)["SMH"]
        self.assertEqual(result["failed"], 1)
        self.assertEqual(saved["expense_ratio"], 0.35)      # survived
        self.assertIn("last_error", saved)

    def test_success_clears_a_previous_error_marker(self):
        EP.save_profiles(self.dir, {"SMH": {"last_error": "old",
                                            "last_error_at": "then"}})
        EP.fetch_profile = lambda t: {"ticker": t, "expense_ratio": 0.35}
        EP.refresh_profiles(["SMH"], self.dir)
        saved = EP.load_profiles(self.dir)["SMH"]
        self.assertNotIn("last_error", saved)
        self.assertEqual(saved["expense_ratio"], 0.35)

    def test_one_bad_ticker_does_not_abort_the_others(self):
        EP.fetch_profile = lambda t: ({"ticker": t, "error": "boom"}
                                      if t == "BAD" else
                                      {"ticker": t, "expense_ratio": 0.2})
        result = EP.refresh_profiles(["BAD", "SMH"], self.dir)
        self.assertEqual((result["ok"], result["failed"]), (1, 1))
        self.assertIn("SMH", EP.load_profiles(self.dir))

    def test_blank_tickers_are_ignored(self):
        EP.fetch_profile = lambda t: {"ticker": t}
        self.assertEqual(EP.refresh_profiles(["", "  "], self.dir)["requested"], 0)


class TestFetchProfileParsing(unittest.TestCase):
    def setUp(self):
        self._orig_import = EP.fetch_profile

    def _run(self, info, holdings=None, raises=False):
        import types
        stub = types.ModuleType("yfinance")
        stub.Ticker = lambda t: _StubTicker(info, holdings, raises)
        sys.modules["yfinance"] = stub
        try:
            return EP.fetch_profile("SMH")
        finally:
            sys.modules.pop("yfinance", None)

    def test_expense_ratio_is_left_as_a_percent(self):
        # netExpenseRatio arrives as 0.35 meaning 0.35%/yr — scaling it
        # would report a 35% fee.
        p = self._run({"netExpenseRatio": 0.35, "quoteType": "ETF"})
        self.assertEqual(p["expense_ratio"], 0.35)

    def test_yield_is_a_fraction_and_is_converted(self):
        # yield arrives as 0.002 meaning 0.2% — the opposite convention to
        # netExpenseRatio on the same payload.
        p = self._run({"yield": 0.002, "quoteType": "ETF"})
        self.assertEqual(p["yield_pct"], 0.2)

    def test_falls_back_to_the_annual_report_ratio(self):
        p = self._run({"annualReportExpenseRatio": 0.6, "quoteType": "ETF"})
        self.assertEqual(p["expense_ratio"], 0.6)

    def test_network_failure_returns_an_error_not_an_exception(self):
        p = self._run({}, raises=True)
        self.assertIn("error", p)
        self.assertEqual(p["ticker"], "SMH")

    def test_empty_info_is_an_error(self):
        self.assertIn("error", self._run({}))

    def test_change_percent_is_already_a_percent(self):
        # regularMarketChangePercent arrives as 1.83 meaning +1.83% — the
        # opposite convention to "yield" on the same payload, so it must not
        # be scaled.
        p = self._run({"quoteType": "ETF", "regularMarketChangePercent": 1.8283,
                       "regularMarketChange": 10.45})
        self.assertAlmostEqual(p["change_pct"], 1.8283, places=4)
        self.assertAlmostEqual(p["change_abs"], 10.45, places=2)

    def test_change_falls_back_to_price_minus_previous_close(self):
        p = self._run({"quoteType": "ETF", "regularMarketPrice": 581.93,
                       "previousClose": 571.48})
        self.assertAlmostEqual(p["change_pct"], 1.829, places=2)
        self.assertAlmostEqual(p["change_abs"], 10.45, places=2)

    def test_a_flat_previous_close_does_not_divide_by_zero(self):
        p = self._run({"quoteType": "ETF", "regularMarketPrice": 10.0,
                       "previousClose": 0})
        self.assertIsNone(p["change_pct"])

    def test_change_is_none_when_nothing_supports_it(self):
        p = self._run({"quoteType": "ETF", "netExpenseRatio": 0.4})
        self.assertIsNone(p["change_pct"])

    def test_the_quote_is_timestamped(self):
        p = self._run({"quoteType": "ETF", "regularMarketChangePercent": 1.0})
        self.assertTrue(p["quote_at"])

    def test_holdings_count_reflects_what_the_provider_published(self):
        # Yahoo returns only 5 holdings for some funds (DRAM). Reporting a
        # fixed "top 10" would assert completeness the data lacks.
        import pandas as pd
        df = pd.DataFrame({"Name": ["A", "B"], "Holding Percent": [0.2, 0.1]},
                          index=pd.Index(["AAA", "BBB"], name="Symbol"))
        p = self._run({"quoteType": "ETF"}, holdings=df)
        self.assertEqual(p["holdings_count"], 2)
        self.assertAlmostEqual(p["top10_weight"], 30.0, places=1)

    def test_asset_mix_is_converted_from_fractions(self):
        p = self._run({"quoteType": "ETF"})
        # the stub exposes no asset_classes, so the mix is simply empty
        self.assertEqual(p["asset_mix"], {})

    def test_no_holdings_is_valid_for_a_bullion_trust(self):
        p = self._run({"quoteType": "ETF", "netExpenseRatio": 0.4},
                      holdings=None)
        self.assertEqual(p["holdings"], [])
        self.assertNotIn("top10_weight", p)


class TestThemeNaming(unittest.TestCase):
    """The provider's category is too broad for a thematic fund (SMH and IGV
    are both "Technology"), so the user's own label wins — and must survive
    every re-fetch."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_display_prefers_the_user_label(self):
        self.assertEqual(
            EP.display_theme({"category": "Technology",
                              EP.THEME_KEY: "Semiconductors"}),
            "Semiconductors")

    def test_display_falls_back_to_the_provider_category(self):
        self.assertEqual(EP.display_theme({"category": "Technology"}),
                         "Technology")
        self.assertIsNone(EP.display_theme({}))

    def test_set_and_clear(self):
        EP.save_profiles(self.dir, {"SMH": {"category": "Technology"}})
        res = EP.set_theme(self.dir, "SMH", "Semiconductors")
        self.assertTrue(res["ok"])
        self.assertEqual(res["theme"], "Semiconductors")
        self.assertTrue(res["custom"])
        # blank resets to the provider category rather than blanking the cell
        res = EP.set_theme(self.dir, "SMH", "  ")
        self.assertEqual(res["theme"], "Technology")
        self.assertFalse(res["custom"])

    def test_a_refresh_cannot_overwrite_the_user_label(self):
        # This is why the label lives under its own key: refresh merges
        # {**prev, **fresh} and `fresh` never carries THEME_KEY.
        EP.save_profiles(self.dir, {"SMH": {"category": "Technology"}})
        EP.set_theme(self.dir, "SMH", "Semiconductors")
        orig = EP.fetch_profile
        EP.fetch_profile = lambda t: {"ticker": t, "category": "Technology",
                                      "expense_ratio": 0.35}
        try:
            EP.refresh_profiles(["SMH"], self.dir)
        finally:
            EP.fetch_profile = orig
        saved = EP.load_profiles(self.dir)["SMH"]
        self.assertEqual(saved[EP.THEME_KEY], "Semiconductors")
        self.assertEqual(EP.display_theme(saved), "Semiconductors")
        self.assertEqual(saved["expense_ratio"], 0.35)   # refresh still worked

    def test_labelling_a_fund_with_no_profile_yet(self):
        res = EP.set_theme(self.dir, "newetf", "Space")
        self.assertTrue(res["ok"])
        self.assertEqual(res["ticker"], "NEWETF")
        self.assertEqual(EP.load_profiles(self.dir)["NEWETF"][EP.THEME_KEY],
                         "Space")

    def test_blank_ticker_is_rejected(self):
        self.assertFalse(EP.set_theme(self.dir, "", "X")["ok"])

    def test_label_is_length_capped(self):
        res = EP.set_theme(self.dir, "SMH", "x" * 200)
        self.assertEqual(len(res["theme"]), 60)

    def test_attach_exposes_the_resolved_name_and_flag(self):
        rows = [{"ticker": "SMH", "sector": "ETF"}]
        EP.attach_profiles(rows, {"SMH": {"category": "Technology",
                                          EP.THEME_KEY: "Semiconductors"}})
        self.assertEqual(rows[0]["etf_theme_name"], "Semiconductors")
        self.assertTrue(rows[0]["etf_theme_custom"])


if __name__ == "__main__":
    unittest.main()
