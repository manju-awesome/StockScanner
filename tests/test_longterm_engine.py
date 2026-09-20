"""
Tests for core.longterm — the Long-Term Buy Engine.

The invariants these defend, in the order the engine applies them:

  1. The hierarchy is structural. A perfect chart cannot rescue a company
     that failed the quality gate, because the technical gate is never
     reached. This is the property that distinguishes the engine from a
     weighted composite, and the one most likely to be broken by a
     well-meaning refactor that "simplifies" the ladder into a score.
  2. A missing input is never a satisfied condition. Unknown trend is not
     an intact trend; an unmeasured candle is not a confirmation.
  3. Support means price is ABOVE the level. A moving average price has
     fallen through is resistance, and counting it as support is how a
     screener recommends a breakdown.
  4. The reverse DCF's comparison survives negative growth on either side.

Run with: python -m unittest tests.test_longterm_engine
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.longterm import engine as E
from stockanalysis.core.longterm import quality as Q
from stockanalysis.core.longterm import technicals as T
from stockanalysis.core.longterm import valuation as V


def _row(**kw):
    """An elite company at a clean 8/21 EMA pullback with confirmation —
    the one shape that should reach BUY NOW. Every test mutates one thing."""
    base = {
        "Ticker": "TEST", "LongName": "Test Corp", "Sector": "Technology",
        "Industry": "Software - Infrastructure", "Current Price": 100.0,
        # fundamentals -> LQuality well above the 85 the EMA zone needs
        "Revenue": 25.0, "EPS_Growth%": 30.0, "ReturnOnEquity%": 30.0,
        "OperatingMargin%": 30.0, "GrossMargin%": 70.0, "FCF_Margin%": 25.0,
        "FCF_Positive": True, "DebtToEquity": 20.0, "CurrentRatio": 3.0,
        "TotalCash": 5e10, "TotalDebt": 1e10, "Inst_Own%": 65.0,
        "Inst_Own_Chg": 1.5, "FreeCashFlow": 2.0e10, "NetIncome": 2.0e10,
        "SharesOutstanding": 1.0e9, "Beta": 1.0, "MarketCap": 1.0e11,
        "FCF_CAGR%": 20.0, "FCF_Years": 4, "FCF_Positive_Years": 4,
        "Revenue_CAGR%": 20.0, "OperatingMargin_Trend_pp": 2.0,
        # trend: intact and rising
        "8EMA": 99.0, "21EMA": 98.5, "50MA": 92.0, "200MA": 80.0,
        "Above_200MA": True, "Price_vs_50MA%": 8.7, "Price_vs_200MA%": 25.0,
        "Pct_vs_8EMA": 1.0, "MA50_Slope%": 2.0, "MA200_Slope%": 1.5,
        "ATR_Pct": 2.0, "RSI_14": 55.0, "Dist_52W_High%": -8.0,
        "52W High": 108.0, "Prior_Breakout_Level": 99.2,
        # volume + confirmation
        "Vol_vs_20D": 0.7, "Pullback_Vol_Ratio": 0.7, "VolumeDryingUp": True,
        "Distribution_Days_25d": 1, "Reversal_Candle": "bullish engulfing",
        "RVOL": 1.3, "RS_Rank": 85.0, "RS": 8.0,
        "Days_To_Earnings": 40,
    }
    base.update(kw)
    return base


class TestHierarchyIsStructural(unittest.TestCase):
    """The core claim of the whole package."""

    def test_the_baseline_row_is_a_buy(self):
        # If this breaks, every "cannot reach a buy" test below is vacuous.
        r = E.evaluate(_row())
        self.assertEqual(r["action"], "BUY NOW")
        self.assertEqual(r["gate"], "confirmed")

    def test_a_perfect_chart_cannot_rescue_a_weak_company(self):
        # Chart, volume, trend and confirmation all untouched and ideal;
        # only the business is gutted.
        weak = _row(FCF_Positive=False, DebtToEquity=400.0,
                    CurrentRatio=0.6, TotalCash=1e8, TotalDebt=5e10,
                    OperatingMargin_Trend_pp=-8.0,
                    **{"Revenue": -5.0, "EPS_Growth%": -20.0,
                       "ReturnOnEquity%": 2.0, "OperatingMargin%": 1.0,
                       "GrossMargin%": 8.0, "FCF_Margin%": -5.0,
                       "FCF_CAGR%": -30.0})
        r = E.evaluate(weak)
        self.assertEqual(r["action"], "AVOID")
        self.assertEqual(r["gate"], "quality")
        self.assertLess(r["quality"]["score"], Q.MIN_OWNABLE)

    def test_quality_alone_cannot_reach_a_buy_when_overvalued(self):
        # An elite business whose price demands far more than it delivers.
        r = E.evaluate(_row(FreeCashFlow=2.0e8,
                            **{"FCF_CAGR%": 3.0, "Revenue_CAGR%": 3.0,
                               "Revenue": 3.0}))
        # Named, not bare: the business is fine, the price is not.
        self.assertEqual(r["action"], "OWN / WAIT FOR PRICE")
        self.assertEqual(r["gate"], "valuation")

    def test_broken_trend_watches_rather_than_avoids(self):
        # The business still passed gate 1, so this is a watchlist name whose
        # chart has to heal — not a company to discard.
        # BOTH averages must point down for a breakdown — leaving the
        # fixture's default rising 50 MA in place describes a recovery.
        r = E.evaluate(_row(Above_200MA=False,
                            **{"MA200_Slope%": -2.0, "MA50_Slope%": -2.0,
                               "200MA": 120.0, "50MA": 110.0,
                               "Price_vs_50MA%": -9.0}))
        self.assertEqual(r["trend"]["state"], "BROKEN")
        self.assertEqual(r["action"], "OWN / WAIT FOR TREND")
        self.assertEqual(r["gate"], "trend")

    def test_zone_quality_bars_come_from_the_framework(self):
        # 200 MA is the deep entry and demands 90, not 85.
        self.assertEqual(E.ZONE_MIN_QUALITY["200MA"], 90)
        self.assertEqual(E.ZONE_MIN_QUALITY["EMA"], 85)


class TestMissingInputsAreNotSatisfiedConditions(unittest.TestCase):
    def test_unknown_trend_slope_blocks_a_buy_without_failing_the_trend(self):
        r = E.evaluate(_row(**{"MA200_Slope%": None}))
        self.assertIsNone(r["trend"]["pass"])
        self.assertNotEqual(r["action"], "BUY NOW")

    def test_an_unmeasured_candle_is_not_a_confirmation(self):
        # No swing score and no meaningful risk/reward to stand in for it.
        r = E.evaluate(_row(Reversal_Candle=None, Swing_Score=None,
                            **{"Prior_Breakout_Level": 96.0, "S1": None,
                               "ATR_Pct": 4.0, "52W High": 108.0}))
        self.assertEqual(r["action"], "WATCH")
        self.assertEqual(r["gate"], "trigger")

    def test_no_reversal_candle_blocks_the_buy(self):
        r = E.evaluate(_row(Reversal_Candle="none"))
        self.assertEqual(r["gate"], "trigger")
        self.assertTrue(any("reversal" in b.lower() for b in r["blockers"]))

    def test_quality_renormalises_rather_than_zero_filling(self):
        full = Q.compute_lquality(_row())
        # Drop two factors entirely; the score must not collapse toward zero.
        partial = Q.compute_lquality(
            _row(Inst_Own_Chg=None, CurrentRatio=None, DebtToEquity=None,
                 TotalCash=None, TotalDebt=None, **{"Inst_Own%": None}))
        self.assertLess(partial["coverage"], full["coverage"])
        self.assertGreater(partial["score"], 70)

    def test_unreliable_coverage_withholds_the_tier_and_avoids(self):
        bare = {"Ticker": "X", "Current Price": 10.0, "GrossMargin%": 70.0}
        lq = Q.compute_lquality(bare)
        self.assertFalse(lq["reliable"])
        self.assertIsNone(lq["tier"])
        self.assertEqual(E.evaluate(bare)["gate"], "quality")


class TestSupportRequiresPriceAboveTheLevel(unittest.TestCase):
    def test_a_level_price_has_fallen_through_is_not_support(self):
        # 8 EMA above the price: that is resistance overhead.
        conf = T.compute_support_confluence(
            _row(**{"8EMA": 105.0, "21EMA": 104.0, "50MA": 103.0,
                    "200MA": 102.0, "Prior_Breakout_Level": 101.0}))
        self.assertEqual(conf["score"], 0)
        self.assertEqual(conf["hits"], [])

    def test_a_stock_falling_through_every_average_scores_no_support(self):
        # The failure mode the module exists to prevent: proximity to four
        # averages at once during a collapse reading as strong confluence.
        row = _row(**{"Current Price": 79.0, "8EMA": 99.0, "21EMA": 98.5,
                      "50MA": 92.0, "200MA": 80.0, "Pct_vs_8EMA": -20.2})
        self.assertEqual(T.compute_pullback(row)["zone"], "NONE")
        self.assertEqual(T.compute_support_confluence(row)["score"], 0)

    def test_the_breakdown_note_does_not_contradict_itself(self):
        """The distance is derived from the price and level just compared.

        Reading it from Price_vs_200MA% instead let a stale scan column
        produce "Below the 200 MA (+25.0%)" — a sentence that disagrees with
        itself inside eight words.
        """
        pull = T.compute_pullback(
            _row(**{"Current Price": 79.0, "200MA": 80.0,
                    "Price_vs_200MA%": 25.0, "MA200_Slope%": -1.0,
                    "MA50_Slope%": -1.0}))
        self.assertIn("-1.2%", pull["note"])
        self.assertNotIn("+25.0%", pull["note"])

    def test_a_lost_average_above_a_held_200ma_reads_as_resistance(self):
        # Below the short-term averages but still above the 200 MA: not a
        # breakdown, and the levels overhead are resistance, not support.
        pull = T.compute_pullback(
            _row(**{"Current Price": 90.0, "8EMA": 99.0, "21EMA": 98.5,
                    "50MA": 92.0, "200MA": 60.0, "Pct_vs_8EMA": -9.1,
                    "Prior_Breakout_Level": None, "ATR_Pct": 1.0}))
        self.assertEqual(pull["stage"], "STAGE3_DEEP")
        self.assertEqual(T.compute_support_confluence(
            _row(**{"Current Price": 90.0, "8EMA": 99.0, "21EMA": 98.5,
                    "50MA": 92.0, "200MA": 99.0, "S1": None,
                    "Prior_Breakout_Level": None}))["score"], 0)

    def test_confluence_counts_the_ema_pair_once(self):
        # 8 and 21 EMA are one short-term signal at two lookbacks.
        conf = T.compute_support_confluence(
            _row(**{"8EMA": 99.5, "21EMA": 99.0, "50MA": 60.0,
                    "200MA": 50.0, "S1": None, "Prior_Breakout_Level": None}))
        names = [h["name"] for h in conf["hits"]]
        self.assertEqual(names.count("8/21 EMA"), 1)

    def test_tolerance_scales_with_volatility(self):
        quiet = T._tolerance(_row(**{"ATR_Pct": 0.5}))
        wild = T._tolerance(_row(**{"ATR_Pct": 9.0}))
        self.assertLess(quiet, wild)
        self.assertGreaterEqual(quiet, T.MIN_TOLERANCE_PCT)
        self.assertLessEqual(wild, T.MAX_TOLERANCE_PCT)


class TestExtensionAndEarnings(unittest.TestCase):
    def test_extended_price_is_not_a_zone(self):
        r = E.evaluate(_row(**{"Pct_vs_8EMA": 9.0}))
        self.assertTrue(r["pullback"]["extended"])
        self.assertEqual(r["pullback"]["zone"], "NONE")
        self.assertNotEqual(r["action"], "BUY NOW")

    def test_extended_names_still_get_a_priced_entry(self):
        r = E.evaluate(_row(**{"Pct_vs_8EMA": 9.0}))
        self.assertTrue(r["entries"])
        self.assertTrue(all(e["price"] is not None for e in r["entries"]))
        self.assertTrue(any("$" in t for t in r["triggers"]))

    def test_imminent_earnings_blocks_every_buy(self):
        r = E.evaluate(_row(Days_To_Earnings=2))
        self.assertEqual(r["action"], "WAIT")
        self.assertEqual(r["gate"], "earnings")

    def test_defensive_regime_stops_buying_entirely(self):
        r = E.evaluate(_row(), regime="DEFENSIVE")
        self.assertEqual(r["action"], "WATCH")
        self.assertEqual(r["gate"], "regime")

    def test_selective_regime_raises_the_quality_bar(self):
        # Quality sitting between the normal bar and the bumped one.
        row = _row()
        base = Q.compute_lquality(row)["score"]
        self.assertGreaterEqual(base, 85)
        favorable = E.evaluate(row, regime="FAVORABLE")
        self.assertEqual(favorable["action"], "BUY NOW")


class TestReverseDCF(unittest.TestCase):
    def test_a_price_demanding_more_than_delivered_is_overvalued(self):
        v = V.compute_valuation(_row(FreeCashFlow=2.0e8,
                                     **{"FCF_CAGR%": 3.0,
                                        "Revenue_CAGR%": 3.0, "Revenue": 3.0}))
        self.assertEqual(v["band"], "OVERVALUED")
        self.assertFalse(v["acceptable"])

    def test_declining_business_priced_for_less_decline_is_overvalued(self):
        """The bug a ratio comparison cannot see.

        Implied -5%/yr against a company shrinking 10%/yr gives a ratio of
        -1.67, which sorts into "asks less than delivered" and reports a
        melting business as UNDERVALUED. The price is in fact demanding a
        better outcome than the company produces.
        """
        implied, delivered = -5.0, -10.0
        gap = implied - delivered
        tolerance = max(V.TOLERANCE_FLOOR_PP,
                        abs(delivered) * V.TOLERANCE_FRACTION)
        self.assertGreater(gap, tolerance)      # -> OVERVALUED, correctly

    def test_implausible_implied_growth_is_refused_at_any_delivered_rate(self):
        # A base-effect year must not license a price requiring 50%+ forever.
        v = V.compute_valuation(_row(FreeCashFlow=2.0e8,
                                     **{"FCF_CAGR%": 200.0,
                                        "Revenue_CAGR%": 200.0}))
        if v["implied_growth_pct"] is not None and \
                v["implied_growth_pct"] >= V.IMPLAUSIBLE_IMPLIED_GROWTH:
            self.assertEqual(v["band"], "OVERVALUED")
            self.assertEqual(V.valuation_sub_score(v), 0.0)

    def test_financials_are_priced_on_peers_not_cash_flow(self):
        blocked = V._dcf_blocked(_row(Sector="Financial Services"))
        self.assertIsNotNone(blocked)
        self.assertIn("free cash flow", blocked.lower())

    def test_negative_free_cash_flow_blocks_the_model(self):
        self.assertIsNotNone(V._dcf_blocked(_row(FreeCashFlow=-1e9)))

    def test_share_count_falls_back_to_market_cap(self):
        shares = V._shares(_row(SharesOutstanding=None, MarketCap=1.0e11,
                                **{"Current Price": 100.0}))
        self.assertAlmostEqual(shares, 1.0e9, delta=1.0)

    def test_the_implied_rate_means_what_the_headline_says(self):
        """Growth is held flat through the explicit window.

        Re-deriving the value at the solved rate must reproduce the market
        capitalisation; if the model faded growth internally the headline
        "requires X% every year for 5 years" would be false.
        """
        row = _row()
        discount, _ = V._discount_rate(1.0, V.DEFAULT_RISK_FREE)
        net_cash = row["TotalCash"] - row["TotalDebt"]
        market_equity = row["Current Price"] * row["SharesOutstanding"]
        g = V._implied_growth(market_equity, row["FreeCashFlow"], discount,
                              net_cash)
        self.assertIsNotNone(g)
        rebuilt = V._equity_value(row["FreeCashFlow"], g, discount, net_cash)
        self.assertAlmostEqual(rebuilt / market_equity, 1.0, places=3)


class TestQualityCalibration(unittest.TestCase):
    def test_moat_excludes_revenue_growth(self):
        # Growth is not a moat, and is already scored twice elsewhere.
        fields = [key for key, _label, _t in Q._MOAT_CHECKS]
        self.assertNotIn("Revenue", fields)

    def test_a_low_margin_leader_is_not_penalised_for_its_industry(self):
        """Absolute-only thresholds scored Walmart's moat 0 — a finding about
        retail accounting, not competitive position."""
        # Every figure below is beneath the absolute elite bars (60% gross,
        # 25% operating, 25% ROE), which is the normal state of a retailer.
        peers = [_row(Ticker=f"P{i}", Sector="Consumer Defensive",
                      Industry="Discount Stores", **{"GrossMargin%": 10.0 + i,
                                                     "OperatingMargin%": 2.0 + i * 0.1,
                                                     "ReturnOnEquity%": 10.0 + i})
                 for i in range(12)]
        leader = _row(Ticker="LEAD", Sector="Consumer Defensive",
                      Industry="Discount Stores", **{"GrossMargin%": 25.0,
                                                     "OperatingMargin%": 5.0,
                                                     "ReturnOnEquity%": 22.0})
        stats = Q.build_sector_stats(peers + [leader])
        scored, _ = Q._moat(leader, stats)
        self.assertGreater(scored, 0.0)
        # ...while the same company against absolute bars alone scores zero.
        bare, _ = Q._moat(leader, {})
        self.assertEqual(bare, 0.0)

    def test_zero_gross_margin_is_treated_as_not_reported(self):
        # Banks have no COGS line; reading the placeholder as a real 0%
        # scored JPMorgan down for a field it cannot have.
        with_zero, _ = Q._moat(_row(**{"GrossMargin%": 0.0,
                                       "OperatingMargin%": 50.0,
                                       "ReturnOnEquity%": 30.0}), {})
        self.assertEqual(with_zero, 100.0)

    def test_fcf_trend_separates_growing_from_shrinking(self):
        growing, _ = Q._free_cash_flow(_row(**{"FCF_CAGR%": 25.0}))
        shrinking, _ = Q._free_cash_flow(_row(**{"FCF_CAGR%": -20.0,
                                                 "FCF_Positive_Years": 2}))
        self.assertGreater(growing, shrinking)

    def test_margin_direction_adjusts_but_does_not_dominate(self):
        high_eroding, _ = Q._operating_margin(
            _row(**{"OperatingMargin%": 45.0, "OperatingMargin_Trend_pp": -3.0}))
        low_expanding, _ = Q._operating_margin(
            _row(**{"OperatingMargin%": 8.0, "OperatingMargin_Trend_pp": 3.0}))
        self.assertGreater(high_eroding, low_expanding)


class TestUniverseContext(unittest.TestCase):
    def test_sector_rs_rank_is_relative_to_the_sector(self):
        rows = [_row(Ticker=f"T{i}", Sector="Technology", RS=float(i))
                for i in range(10)]
        rows += [_row(Ticker=f"E{i}", Sector="Energy", RS=100.0 + i)
                 for i in range(10)]
        E.attach_sector_context(rows)
        # The weakest Energy name leads the market on raw RS but sits at the
        # bottom of its own group — §9's whole point.
        weakest_energy = next(r for r in rows if r["Ticker"] == "E0")
        self.assertEqual(weakest_energy["Sector_RS_Rank"], 0)
        self.assertGreater(weakest_energy["Sector_Strength_Rank"], 0)

    def test_evaluate_universe_sorts_buys_first(self):
        results = E.evaluate_universe([_row(Ticker="GOOD"),
                                       _row(Ticker="BAD",
                                            **{"Revenue": -10.0,
                                               "EPS_Growth%": -30.0,
                                               "ReturnOnEquity%": 1.0,
                                               "OperatingMargin%": 0.5,
                                               "GrossMargin%": 5.0,
                                               "FCF_Margin%": -10.0,
                                               "DebtToEquity": 500.0})])
        self.assertEqual(results[0]["ticker"], "GOOD")
        self.assertEqual(results[-1]["action"], "AVOID")


if __name__ == "__main__":
    unittest.main()


class TestTickerSearch(unittest.TestCase):
    """webapp.longterm_view.parse_tickers — the /longterm search box."""

    def setUp(self):
        from stockanalysis.webapp import longterm_view
        self.parse = longterm_view.parse_tickers

    def test_a_single_ticker(self):
        self.assertEqual(self.parse("NVDA"), ["NVDA"])

    def test_comma_separated(self):
        self.assertEqual(self.parse("NVDA,MSFT,AAPL"),
                         ["NVDA", "MSFT", "AAPL"])

    def test_lowercase_is_normalised(self):
        self.assertEqual(self.parse("nvda, msft"), ["NVDA", "MSFT"])

    def test_any_separator_works(self):
        # Pasted lists arrive as newlines, tabs, semicolons or bare spaces
        # depending on where they were copied from.
        self.assertEqual(self.parse("NVDA, MSFT\nAAPL\tGOOGL;AMD  META"),
                         ["NVDA", "MSFT", "AAPL", "GOOGL", "AMD", "META"])

    def test_share_classes_and_indices_survive(self):
        self.assertEqual(self.parse("BRK-B, BF.B, ^GSPC"),
                         ["BRK-B", "BF.B", "^GSPC"])

    def test_duplicates_collapse_keeping_first_position(self):
        self.assertEqual(self.parse("NVDA, MSFT, NVDA"), ["NVDA", "MSFT"])

    def test_empty_and_junk_yield_nothing(self):
        for junk in ("", "   ", ",,,", None):
            self.assertEqual(self.parse(junk), [])

    def test_a_pasted_column_is_capped(self):
        many = ", ".join(f"T{i}" for i in range(500))
        self.assertEqual(len(self.parse(many, limit=60)), 60)


class TestSearchPageWiring(unittest.TestCase):
    """The page renders the search without reaching the network.

    api.longterm() is stubbed because the point of these is the filtering and
    URL-state logic, not the engine — which the tests above already cover.
    """

    def setUp(self):
        from stockanalysis.webapp import api, longterm_view
        self.view = longterm_view
        self._real = api.longterm
        rows = [E.evaluate(_row(Ticker=t)) for t in ("NVDA", "MSFT", "AAPL")]
        rows[2]["action"], rows[2]["icon"] = "AVOID", "🔴"
        api.longterm = lambda override=None: {
            "rows": rows, "counts": {}, "regime": "FAVORABLE",
            "regime_note": "test", "risk_free_note": "test",
            "coverage": {"total": 3, "ma_slope": 3, "reversal": 3,
                         "statements": 3, "breakout": 3, "needs_rescan": False},
        }

    def tearDown(self):
        from stockanalysis.webapp import api
        api.longterm = self._real

    def test_search_narrows_to_the_named_tickers(self):
        body, _ = self.view.longterm_page({"q": ["nvda, msft"]})
        self.assertIn(">NVDA<", body)
        self.assertIn(">MSFT<", body)
        self.assertNotIn(">AAPL<", body)

    def test_unknown_tickers_are_named_not_silently_dropped(self):
        body, _ = self.view.longterm_page({"q": ["NVDA, ZZZZ"]})
        self.assertIn("Not in the research library", body)
        self.assertIn("ZZZZ", body)

    def test_a_single_result_opens_its_reasoning(self):
        body, _ = self.view.longterm_page({"q": ["NVDA"]})
        self.assertIn("<details open>", body)

    def test_several_results_stay_collapsed(self):
        body, _ = self.view.longterm_page({"q": ["NVDA, MSFT"]})
        self.assertNotIn("<details open>", body)

    def test_chips_preserve_the_search(self):
        # Clicking a chip used to drop the search entirely.
        body, _ = self.view.longterm_page({"q": ["NVDA, AAPL"]})
        self.assertIn("q=NVDA%2C+AAPL", body)

    def test_chip_counts_are_scoped_to_the_search(self):
        # Counting across the whole library would offer "Avoid 347" beside
        # two searched tickers and return an empty table when clicked.
        body, _ = self.view.longterm_page({"q": ["NVDA, MSFT"]})
        self.assertNotIn("Avoid 1", body)
        full, _ = self.view.longterm_page({})
        self.assertIn("Avoid 1", full)

    def test_no_search_still_renders_the_whole_library(self):
        body, _ = self.view.longterm_page({})
        for ticker in ("NVDA", "MSFT", "AAPL"):
            self.assertIn(f">{ticker}<", body)
        self.assertNotIn("Not in the research library", body)


class Test52WeekRange(unittest.TestCase):
    def test_position_in_the_range(self):
        rng = T.compute_52w_range(_row(**{"Current Price": 90.0,
                                          "52W High": 100.0, "52W Low": 50.0}))
        self.assertEqual(rng["position_pct"], 80.0)
        self.assertEqual(rng["high"], 100.0)
        self.assertEqual(rng["low"], 50.0)

    def test_both_endpoints_are_reported(self):
        """A stock 8% off its high and one 8% off its low are not the same
        purchase, and only having one endpoint cannot tell them apart."""
        rng = T.compute_52w_range(_row(**{"Current Price": 92.0,
                                          "52W High": 100.0, "52W Low": 50.0,
                                          "Dist_52W_High%": None,
                                          "Pct_From_52W_Low%": None}))
        self.assertAlmostEqual(rng["from_high_pct"], -8.0, places=1)
        self.assertAlmostEqual(rng["from_low_pct"], 84.0, places=1)

    def test_scan_columns_win_over_derivation(self):
        rng = T.compute_52w_range(_row(**{"Current Price": 92.0,
                                          "52W High": 100.0, "52W Low": 50.0,
                                          "Dist_52W_High%": -7.5}))
        self.assertEqual(rng["from_high_pct"], -7.5)

    def test_missing_range_degrades_to_none(self):
        rng = T.compute_52w_range({"Current Price": 10.0})
        self.assertIsNone(rng["position_pct"])
        self.assertIsNone(rng["high"])

    def test_a_degenerate_range_does_not_divide_by_zero(self):
        rng = T.compute_52w_range(_row(**{"Current Price": 50.0,
                                          "52W High": 50.0, "52W Low": 50.0}))
        self.assertIsNone(rng["position_pct"])


class TestSupportLadder(unittest.TestCase):
    def test_s1_to_s4_are_a_fixed_ladder(self):
        # Fixed, not "nearest support" — a column is only comparable down the
        # page if S3 means the 50 MA on every row.
        self.assertEqual([s for s, _k, _n in T.SUPPORT_SLOTS],
                         ["S1", "S2", "S3", "S4"])
        self.assertEqual([k for _s, k, _n in T.SUPPORT_SLOTS],
                         ["8EMA", "21EMA", "50MA", "200MA"])

    def test_by_level_carries_every_rung(self):
        p = T.compute_pullback(_row())
        for _slot, key, _name in T.SUPPORT_SLOTS:
            self.assertIn(key, p["by_level"])
            self.assertIsNotNone(p["by_level"][key]["distance_pct"])

    def test_held_follows_the_sign_not_the_tolerance(self):
        # Price far above the 200 MA still HOLDS it, even though it is too
        # far away to be a supporting entry.
        p = T.compute_pullback(_row(**{"Current Price": 100.0, "200MA": 50.0}))
        lv = p["by_level"]["200MA"]
        self.assertTrue(lv["held"])
        self.assertFalse(lv["supporting"])
        self.assertAlmostEqual(lv["distance_pct"], 100.0, places=1)

    def test_a_lost_level_reads_as_not_held(self):
        p = T.compute_pullback(_row(**{"Current Price": 90.0, "50MA": 100.0}))
        lv = p["by_level"]["50MA"]
        self.assertFalse(lv["held"])
        self.assertLess(lv["distance_pct"], 0)

    def test_a_missing_level_is_none_not_zero(self):
        p = T.compute_pullback(_row(**{"200MA": None}))
        self.assertIsNone(p["by_level"]["200MA"]["distance_pct"])
        self.assertIsNone(p["by_level"]["200MA"]["held"])


class TestSortableDraggableTable(unittest.TestCase):
    def setUp(self):
        from stockanalysis.webapp import longterm_view
        self.view = longterm_view
        self.html = longterm_view._row(E.evaluate(_row()))

    def test_columns_cover_both_verdicts_and_both_levels(self):
        keys = [c[0] for c in self.view._COLUMNS]
        for key in ("investment", "entry_score", "buyzone", "resistance",
                    "support"):
            self.assertIn(key, keys)

    def test_every_cell_carries_a_column_key_and_sort_value(self):
        # The JS finds cells by data-col rather than by index, so a reordered
        # table still sorts the right column.
        for key, _label, _align, _type in self.view._COLUMNS:
            self.assertIn(f'data-col="{key}"', self.html)
        self.assertEqual(self.html.count('data-sort='),
                         len(self.view._COLUMNS))

    def test_detail_row_spans_every_column(self):
        # A stale colspan silently narrows the reasoning panel when columns
        # are added.
        self.assertIn(f'colspan="{len(self.view._COLUMNS)}"', self.html)

    def test_rows_are_tagged_so_pairs_move_together(self):
        self.assertIn('data-main="1"', self.html)
        self.assertIn('data-detail="1"', self.html)

    def test_a_custom_order_reorders_the_cells(self):
        order = ["action", "ticker", "lt"]
        html = self.view._row(E.evaluate(_row()), order=order)
        positions = [html.index(f'data-col="{k}"') for k in order]
        self.assertEqual(positions, sorted(positions))

    def test_better_always_sorts_higher(self):
        # Descending on any ranked column must put the good ones on top.
        self.assertGreater(self.view._ACTION_RANK["BUY NOW"],
                           self.view._ACTION_RANK["AVOID"])
        self.assertGreater(self.view._BAND_RANK["UNDERVALUED"],
                           self.view._BAND_RANK["OVERVALUED"])
        self.assertGreater(self.view._TREND_RANK["CONFIRMED"],
                           self.view._TREND_RANK["BROKEN"])
        self.assertGreater(self.view._TREND_RANK["PARTIAL"],
                           self.view._TREND_RANK["IMPAIRED"])
        self.assertGreater(self.view._TREND_RANK["IMPAIRED"],
                           self.view._TREND_RANK["BROKEN"])

    def test_missing_values_emit_an_empty_sort_key(self):
        # The JS pins empty data-sort to the bottom in both directions; a "0"
        # here would rank a ticker with no 200 MA as the worst 200 MA support.
        html = self.view._row(E.evaluate(_row(**{"S1": None, "R1": None,
                                                 "8EMA": None, "21EMA": None,
                                                 "50MA": None, "200MA": None,
                                                 "Prior_Breakout_Level": None})))
        self.assertIn('data-col="buyzone" data-sort=""', html)

    # ── Resizable columns ───────────────────────────────────────────────────

    def test_every_cell_carries_a_width_target(self):
        # Widths are applied to this inner span, not to the <td> box: a width
        # on a table cell is a suggestion the layout algorithm ignores as soon
        # as the content is wider, which is exactly the dense columns worth
        # narrowing.
        for key, _label, _align, _type in self.view._COLUMNS:
            self.assertIn(f'data-colw="{key}"', self.html)

    def test_every_header_has_a_resize_handle(self):
        html = self.view.analysis_table([E.evaluate(_row())])
        self.assertEqual(html.count("col-resizer"), len(self.view._COLUMNS))
        # draggable="false" keeps a mousedown on the handle out of the
        # header's own drag-to-reorder.
        self.assertIn('class="col-resizer" draggable="false"', html)

    def test_the_header_style_does_not_set_position(self):
        # The regression this exists for: `position:relative` inline on every
        # <th> outranks the stylesheet, so the two pinned headers stopped
        # being `sticky` — and the `left` offset that does nothing to an
        # unscrolled sticky element became a relative shift, sliding TICKER
        # and PRICE right by the width of the frozen group while their data
        # cells stayed put. The header must take its position from PIN_CSS.
        self.assertNotIn("position:", self.view._TH)
        self.assertIn("#lt-table th { position: relative; }", self.view.PIN_CSS)
        self.assertIn("position: sticky", self.view.PIN_CSS)
        # The sticky rule has to come after (and be more specific than) the
        # blanket one, or the pinned columns lose their freeze.
        self.assertLess(self.view.PIN_CSS.index("th { position: relative; }"),
                        self.view.PIN_CSS.index("position: sticky"))


class TestBuyZoneLevel(unittest.TestCase):
    """technicals.compute_buy_zone_level — the price to work an order at."""

    def test_a_volume_confirmed_shelf_is_actual_support(self):
        bz = T.compute_buy_zone_level(
            _row(**{"Current Price": 100.0, "S1": 96.0, "Touches": 42,
                    "Volume_Confirmation": True}))
        self.assertTrue(bz["actual_support"])
        self.assertEqual(bz["source"], "volume_shelf")
        self.assertEqual(bz["price"], 96.0)
        self.assertAlmostEqual(bz["distance_pct"], -4.0, places=1)
        self.assertIn("42 touches", bz["label"])

    def test_an_unconfirmed_shelf_is_still_a_level_but_not_confirmed(self):
        bz = T.compute_buy_zone_level(
            _row(**{"Current Price": 100.0, "S1": 96.0, "Touches": 4,
                    "Volume_Confirmation": False}))
        self.assertEqual(bz["source"], "volume_shelf")
        self.assertFalse(bz["actual_support"])

    def test_it_falls_back_to_the_nearest_moving_average(self):
        bz = T.compute_buy_zone_level(
            _row(**{"Current Price": 100.0, "S1": None, "8EMA": 97.0,
                    "21EMA": 94.0, "50MA": 90.0, "200MA": 80.0,
                    "Prior_Breakout_Level": None}))
        self.assertEqual(bz["source"], "moving_average")
        self.assertEqual(bz["price"], 97.0)
        self.assertEqual(bz["label"], "8 EMA")

    def test_a_derived_level_never_claims_to_be_support(self):
        # The distinction the column exists to preserve: roughly a third of
        # the library has no tested level, and a moving average nobody has
        # defended must not be presented in the same voice as one they have.
        bz = T.compute_buy_zone_level(
            _row(**{"Current Price": 100.0, "S1": None, "50MA": 90.0}))
        self.assertFalse(bz["actual_support"])
        self.assertIn("not", bz["note"].lower())

    def test_a_level_above_the_price_is_not_a_buy_zone(self):
        # Overhead resistance, not somewhere to leave a bid.
        bz = T.compute_buy_zone_level(
            _row(**{"Current Price": 100.0, "S1": 110.0, "8EMA": 105.0,
                    "21EMA": 106.0, "50MA": 107.0, "200MA": 108.0,
                    "Prior_Breakout_Level": None}))
        self.assertIsNone(bz["price"])

    def test_it_is_attached_to_the_pullback_result(self):
        self.assertIn("buy_zone", T.compute_pullback(_row()))


class TestBuyZones(unittest.TestCase):
    """core.longterm.buy_zones — valuation decides whether, support decides when.

    The bug this module was rewritten around was not a wrong number. It was
    two true statements that contradicted each other in one panel:

        Preferred $386-395 — price is inside this band now
        No band qualifies: even the deepest support is an expensive price

    "Preferred" was built from the 50 MA and never meant the price was worth
    paying. So most of what is pinned here is that support alone can no
    longer create a buy zone, and that an empty result is a result.
    """

    def setUp(self):
        from stockanalysis.core.longterm import buy_zones as BZ
        from stockanalysis.webapp import longterm_view
        self.BZ = BZ
        self.view = longterm_view

    def _zones(self, **kw):
        return E.evaluate(_row(**kw), risk_free=0.042)["buy_zones"]

    # An elite business at a price the model cannot justify: AVGO's shape.
    def _expensive(self, **kw):
        base = {"Current Price": 392.99, "FreeCashFlow": 26.9e9,
                "SharesOutstanding": 4.76e9, "8EMA": 409.73, "21EMA": 402.62,
                "50MA": 390.33, "200MA": 368.25, "Prior_Breakout_Level": 351.84,
                "FCF_CAGR%": 24.4, "Revenue_CAGR%": 24.0, "ATR_Pct": 2.5}
        base.update(kw)
        return self._zones(**base)

    # ── the split ───────────────────────────────────────────────────────────

    def test_support_alone_no_longer_creates_a_buy_zone(self):
        # The whole point. Price sits ON the 50 MA and the model says the
        # price demands far more growth than the company delivers.
        zones = self._expensive()
        self.assertTrue(zones["technical"], "support should still be reported")
        self.assertEqual(zones["investment"], [],
                         "an expensive price produced a buy zone from support")
        self.assertEqual(zones["verdict"]["action"], "WAIT")

    def test_technical_zones_are_named_for_their_level_not_a_verdict(self):
        # "Preferred" on a 50 MA band was the original sin.
        labels = [z["label"] for z in self._expensive()["technical"]]
        for verdictish in ("Preferred", "Aggressive", "Excellent"):
            self.assertNotIn(verdictish, labels)
        self.assertIn("50 MA", labels)

    def test_every_support_zone_carries_what_that_price_would_earn(self):
        # So a level and its valuation can never be read in isolation.
        for zone in self._expensive()["technical"]:
            self.assertIn("value_zone", zone)
            self.assertIn("expected_cagr_pct", zone)
            self.assertEqual(zone["value_zone"], "Extreme")

    def test_the_two_curves_are_reported_even_when_they_never_meet(self):
        zones = self._expensive()
        self.assertIsNotNone(zones["fundamental"]["buy_below"])
        self.assertLess(zones["fundamental"]["buy_below"],
                        min(z["low"] for z in zones["technical"]),
                        "this fixture is meant to have no overlap")
        self.assertIn("No overlap",
                      zones["verdict"]["what_would_change"])

    # ── expected return ─────────────────────────────────────────────────────

    def test_paying_the_model_value_earns_the_discount_rate(self):
        # The identity the whole expected-return figure rests on.
        from stockanalysis.core.longterm import valuation as V
        row = _row()
        value = V.price_at_implied_growth(row, 10.0, 0.042)
        discount, _ = V._discount_rate(row.get("Beta"), 0.042)
        self.assertAlmostEqual(
            V.expected_cagr_at_price(row, value, 10.0, 0.042),
            discount * 100, places=0)

    def test_a_lower_price_earns_more(self):
        from stockanalysis.core.longterm import valuation as V
        row = _row()
        rates = [V.expected_cagr_at_price(row, p, 10.0, 0.042)
                 for p in (200.0, 150.0, 100.0, 80.0)]
        self.assertEqual(rates, sorted(rates))

    def test_the_return_hurdle_round_trips(self):
        from stockanalysis.core.longterm import valuation as V
        row = _row()
        price = V.price_for_expected_cagr(row, 15.0, 10.0, 0.042)
        self.assertAlmostEqual(
            V.expected_cagr_at_price(row, price, 10.0, 0.042), 15.0, places=0)

    def test_a_better_business_is_asked_for_a_higher_return(self):
        # Paying up for quality is only defensible if the quality delivers
        # the return, so the tier that justifies the premium clears the
        # taller bar rather than a lower one.
        self.assertGreater(self.BZ.hurdle_for(95), self.BZ.hurdle_for(85))
        self.assertGreater(self.BZ.hurdle_for(85), self.BZ.hurdle_for(72))

    # ── what may be projected forward ───────────────────────────────────────

    def test_cash_flow_may_not_outrun_revenue_in_the_projection(self):
        # Hasbro: free cash flow +51.8% a year while revenue fell 7.1%. Taken
        # at the FCF rate it valued a $96 stock at $696 and reported a 59%/yr
        # expected return.
        rate, note = self.BZ.projection_growth(
            {"FCF_CAGR%": 51.8, "Revenue_CAGR%": -7.1}, {})
        self.assertEqual(rate, -7.1)
        self.assertIn("cannot outrun revenue", note)

    def test_the_projection_is_capped(self):
        # Newmont: +88.5% off a depressed base, credited by the band at the
        # engine's 40% ceiling, which still valued a $118 stock at $1,007.
        rate, note = self.BZ.projection_growth(
            {"FCF_CAGR%": 88.5, "Revenue_CAGR%": 60.0}, {})
        self.assertEqual(rate, self.BZ.PROJECTION_CEILING_PCT)
        self.assertIn("ceiling", note)

    def test_the_projection_cap_is_below_what_the_band_will_underwrite(self):
        from stockanalysis.core.longterm import valuation as V
        # Projecting more growth than the engine refuses to underwrite in a
        # PRICE would be making exactly the forecast it declines to make.
        self.assertLess(self.BZ.PROJECTION_CEILING_PCT,
                        V.IMPLAUSIBLE_IMPLIED_GROWTH)

    def test_the_band_and_the_projection_use_different_legs_on_purpose(self):
        # Microsoft's shape: FCF +4% while revenue +16% on a capex cycle. The
        # band credits the faster leg so investment is not punished; the
        # projection takes the slower one because that is what you receive.
        rate, _ = self.BZ.projection_growth(
            {"FCF_CAGR%": 4.0, "Revenue_CAGR%": 16.0}, {})
        self.assertEqual(rate, 4.0)

    # ── the gate ────────────────────────────────────────────────────────────

    def test_an_unacceptable_band_blocks_the_zone_outright(self):
        zones = self._expensive()
        self.assertFalse(zones["fundamental"]["blocked"])
        self.assertEqual(zones["investment"], [])

    def test_a_zone_below_the_price_is_a_level_to_wait_for(self):
        # Having a qualifying zone is not the same as being able to act.
        for zone in self._zones()["investment"]:
            if not zone["reached"]:
                self.assertEqual(self._zones()["verdict"]["action"], "WAIT")

    def test_the_verdict_separates_unmeasured_from_failed(self):
        # "the valuation says no" and "there is no valuation" lead to
        # different decisions; the old output collapsed them.
        zones = self._zones(FreeCashFlow=None, **{"FCF_CAGR%": None,
                                                  "Revenue_CAGR%": None})
        by_name = {c["name"]: c for c in zones["verdict"]["checks"]}
        self.assertIsNone(by_name["Expected return"]["ok"])
        self.assertEqual(zones["investment"], [])

    def test_a_peer_priced_name_says_why_it_cannot_project(self):
        zones = self._zones(FreeCashFlow=None)
        blocked = zones["fundamental"]["blocked"]
        self.assertTrue(blocked)
        self.assertIn("cash-flow model", blocked)

    def test_every_check_appears_in_the_verdict(self):
        names = [c["name"] for c in self._expensive()["verdict"]["checks"]]
        for expected in ("Quality", "Long-term trend", "Technical support",
                         "Valuation", "Expected return", "Margin of safety"):
            self.assertIn(expected, names)

    def test_a_failing_check_is_named_in_what_would_change(self):
        verdict = self._expensive()["verdict"]
        self.assertTrue(verdict["failed"])
        self.assertTrue(verdict["what_would_change"])

    # ── volume is a confirmation, not a gate ────────────────────────────────

    def test_distribution_lowers_confidence_rather_than_deleting_the_zone(self):
        heavy = self.BZ._confidence({"confidence": "HIGH"},
                                    {"state": "CONFIRMED"}, {"score": 20},
                                    [{"tested": True}], [{"key": "preferred"}])
        clean = self.BZ._confidence({"confidence": "HIGH"},
                                    {"state": "CONFIRMED"}, {"score": 80},
                                    [{"tested": True}], [{"key": "preferred"}])
        self.assertIn("distributing", heavy["note"])
        self.assertGreater(clean["agree"], heavy["agree"])

    # ── technical zone geometry (carried over — still load-bearing) ──────────

    def test_a_zone_is_a_range_even_from_one_level(self):
        zone = next(z for z in self._zones()["technical"] if z["key"] == "200ma")
        self.assertGreater(zone["high"], zone["low"])

    def test_edges_are_rounded_to_prices_a_human_would_name(self):
        for zone in self._zones()["technical"]:
            step = 1.0 if zone["high"] >= 100 else 0.5
            for edge in (zone["low"], zone["high"]):
                self.assertAlmostEqual(edge % step, 0, places=6)

    def test_a_distant_level_is_excluded_rather_than_stretching_the_zone(self):
        zones = self._zones(**{"50MA": 92.0, "Prior_Breakout_Level": 70.0,
                               "ATR_Pct": 2.0})
        zone = next(z for z in zones["technical"] if z["key"] == "50ma")
        self.assertLess((zone["high"] / zone["low"] - 1) * 100, 11)
        self.assertIn("prior breakout", [e["name"] for e in zone["excluded"]])

    def test_the_ladder_is_in_price_order_even_when_labels_are_not(self):
        # MU: the 50 MA at $960 above the 8/21 EMA at $905-912.
        zones = self._zones(**{"Current Price": 971.66, "8EMA": 911.88,
                               "21EMA": 905.06, "50MA": 960.65,
                               "200MA": 552.44, "Prior_Breakout_Level": 818.54,
                               "ATR_Pct": 7.64})
        mids = [z["mid"] for z in zones["technical"]]
        self.assertEqual(mids, sorted(mids, reverse=True))
        self.assertIn("higher price", zones["inverted"])

    def test_an_inversion_note_claims_only_the_ordering(self):
        # MU's price was 6.6% ABOVE its 8 EMA — a round trip, not a pullback.
        note = self._zones(**{"Current Price": 971.66, "8EMA": 911.88,
                              "21EMA": 905.06, "50MA": 960.65,
                              "200MA": 552.44,
                              "Prior_Breakout_Level": 818.54})["inverted"]
        self.assertNotIn("pulled back", note)
        self.assertNotIn("broken", note)

    def test_a_fifty_under_two_hundred_is_called_what_it_is(self):
        note = self._zones(**{"8EMA": 99.0, "21EMA": 98.0, "50MA": 90.0,
                              "200MA": 110.0})["inverted"]
        self.assertIn("crossed under", note)

    def test_technical_and_fundamental_downside_are_different_numbers(self):
        # Confusing a tight stop for a margin of safety is the error the
        # split exists to prevent.
        zone = self._expensive()["technical"][0]
        self.assertIsNotNone(zone["downside_pct"])
        self.assertIsNotNone(zone["fundamental_downside_pct"])
        self.assertNotEqual(zone["downside_pct"],
                            zone["fundamental_downside_pct"])

    # ── wiring ──────────────────────────────────────────────────────────────

    def test_the_engine_attaches_them(self):
        self.assertIn("buy_zones", E.evaluate(_row()))

    def test_the_singular_buy_zone_still_means_the_s1_level(self):
        result = E.evaluate(_row())
        self.assertIn("price", result["pullback"]["buy_zone"])
        self.assertIn("technical", result["buy_zones"])

    def test_the_panel_renders_without_zones(self):
        self.assertIn("run a scan", self.view._buy_zone_panel({}))

    def test_every_row_gets_a_buy_zone_to_display(self):
        # 14 of 552 names have a qualifying investment zone. Showing nothing
        # for the other 538 made "no price qualifies today" look identical
        # to "no support below this" — GOOGL has no investment zone and a
        # 200 MA band at $328-334, and the band is worth knowing.
        zones = self._expensive()["display_zone"]
        self.assertIsNotNone(zones)
        self.assertLess(zones["low"], zones["high"])
        self.assertFalse(zones["qualifies"])
        self.assertEqual(zones["kind"], "technical")

    def test_a_qualifying_zone_is_marked_apart_from_a_support_band(self):
        # A support band is not a buy zone and the two must not look alike.
        zone = self.BZ._display_zone(
            [{"low": 10.0, "high": 12.0, "label": "Preferred",
              "support": "50 MA"}], [], 11.0)
        self.assertTrue(zone["qualifies"])
        self.assertEqual(zone["kind"], "investment")

    def test_the_displayed_band_is_the_one_the_verdict_cites(self):
        # TXN printed "support runs down to $227" in prose and "$277" in the
        # column — two numbers for one idea. The column shows the DEEPEST
        # tracked band, which is the one the sentence already names.
        technical = [{"low": 120.0, "high": 125.0, "label": "8 / 21 EMA",
                      "basis": "8 EMA", "distance_pct": 12.0},
                     {"low": 90.0, "high": 95.0, "label": "50 MA",
                      "basis": "50 MA", "distance_pct": -8.0},
                     {"low": 70.0, "high": 75.0, "label": "200 MA",
                      "basis": "200 MA", "distance_pct": -28.0}]
        zone = self.BZ._display_zone([], technical, price=100.0)
        self.assertEqual(zone["label"], "200 MA")
        self.assertEqual(zone["low"], 70.0)

    def test_the_column_and_the_verdict_quote_the_same_number(self):
        zones = self._expensive()
        low = zones["display_zone"]["low"]
        what = zones["verdict"]["what_would_change"]
        if "support runs down to" in what:
            self.assertIn(f"${low:,.0f}", what)

    def test_a_shrinking_cash_flow_suppresses_the_price_ladder(self):
        # TXN delivers -24%/yr, so the model discounts to $3.51 on a $279
        # stock and "buy below $3.40" reads as a target 99% below. It is the
        # arithmetic working correctly on an input that cannot carry a
        # five-year projection.
        zones = self._zones(**{"Current Price": 279.58,
                               "FreeCashFlow": 5.0e9,
                               "SharesOutstanding": 9.1e8,
                               "FCF_CAGR%": -24.0, "Revenue_CAGR%": -2.0})
        fund = zones["fundamental"]
        self.assertTrue(fund["not_a_target"])
        self.assertIn("shrinking cash flow", fund["caveat"])
        self.assertIn("shrinking cash flow",
                      zones["verdict"]["what_would_change"])
        self.assertNotIn("hurdle is not met until",
                         zones["verdict"]["what_would_change"])

    def test_the_cell_renders_for_a_name_with_no_qualifying_zone(self):
        # The AVGO shape: elite, supported, and priced past every hurdle.
        expensive = E.evaluate(_row(**{
            "Current Price": 392.99, "FreeCashFlow": 26.9e9,
            "SharesOutstanding": 4.76e9, "FCF_CAGR%": 24.4,
            "Revenue_CAGR%": 24.0, "8EMA": 409.73, "21EMA": 402.62,
            "50MA": 390.33, "200MA": 368.25,
            "Prior_Breakout_Level": 351.84}), risk_free=0.042)
        self.assertEqual(expensive["buy_zones"]["investment"], [])
        html, sort = self.view._buy_zone_cell(expensive)
        self.assertIn("$", html)
        self.assertIn("technical", html)
        self.assertIsNotNone(sort)

    def test_a_qualifying_zone_renders_without_the_technical_label(self):
        html, _sort = self.view._buy_zone_cell(
            E.evaluate(_row(), risk_free=0.042))
        self.assertNotIn("technical", html)

    def test_the_panel_never_shows_a_zone_beside_its_own_refusal(self):
        # The exact contradiction that prompted the rewrite.
        html = self.view._buy_zone_panel(
            E.evaluate(_row(**{"Current Price": 392.99,
                               "FreeCashFlow": 26.9e9,
                               "SharesOutstanding": 4.76e9,
                               "50MA": 390.33, "8EMA": 409.73,
                               "21EMA": 402.62, "200MA": 368.25,
                               "FCF_CAGR%": 24.4, "Revenue_CAGR%": 24.0}),
                       risk_free=0.042))
        self.assertIn("No investment buy zone", html)
        self.assertNotIn("Preferred $", html)


class TestPanelMarkupIsTableSafe(unittest.TestCase):
    """No panel may emit a bare <tr>/<td> outside a <table>.

    This is not pedantry about validity. Every panel is rendered inside a
    <td> of the main table, and the HTML5 tree builder hoists a stray <tr>
    up to the nearest table context — which is the OUTER table. Doing that
    ends #lt-table early and foster-parents every following row and cell out
    into <body>: the entire Long-Term page collapsed into a column of loose
    spans on exactly one such <tr>, emitted by reusing the _kv() helper
    (which is written for tables) inside a flex <div>.

    Tag-balance checks do not catch it — the markup is perfectly balanced.
    Python's HTMLParser does not catch it either, because it does not model
    the content model. Only this does.
    """

    def setUp(self):
        from stockanalysis.webapp import longterm_view
        self.view = longterm_view
        self.result = E.evaluate(_row(), risk_free=0.042)

    @staticmethod
    def _stray_table_tags(html):
        """Return row/cell tags that appear with no <table> open."""
        depth, stray = 0, []
        for match in re.finditer(r"<(/?)(table|tr|td|th|thead|tbody)\b", html):
            closing, tag = match.group(1), match.group(2)
            if tag == "table":
                depth += -1 if closing else 1
                continue
            if depth <= 0 and not closing:
                stray.append(tag)
        return stray

    def test_the_checker_would_have_caught_the_bug(self):
        # Guard the guard: a bare _kv() outside a table must be flagged.
        self.assertEqual(self._stray_table_tags(self.view._kv("Risk", "2%")),
                         ["tr", "td", "td"])
        self.assertEqual(
            self._stray_table_tags("<table><tr><td>ok</td></tr></table>"), [])

    def test_no_panel_emits_a_row_outside_a_table(self):
        panels = {
            "_swing_panel": self.view._swing_panel,
            "_buy_zone_panel": self.view._buy_zone_panel,
            "_profile_panel": self.view._profile_panel,
            "_sizing_panel": self.view._sizing_panel,
            "_detail": self.view._detail,
        }
        for name, fn in panels.items():
            with self.subTest(panel=name):
                self.assertEqual(self._stray_table_tags(fn(self.result)), [],
                                 f"{name} emits table markup outside a table")

    def test_the_whole_row_survives_as_one_table_row(self):
        # _row() legitimately IS table markup, so it is checked wrapped in
        # the table it actually lives in: what must not happen is a stray
        # row nested inside one of its cells.
        html = "<table>" + self.view._row(self.result) + "</table>"
        self.assertEqual(self._stray_table_tags(html), [])


class TestBusinessProfile(unittest.TestCase):
    """core.longterm.profile — what the company does, and where it stands.

    The load-bearing invariant is not the formatting: it is that a market-cap
    rank never gets to sound like a market-share claim. The computed layer
    ranks size across the names in this library, and the two come apart on
    exactly the interesting names — ASML is the sole EUV supplier and ranks
    #1 of 10 by cap at 37% of it. Only the curated overlay may say "monopoly".
    """

    def setUp(self):
        from stockanalysis.core.longterm import profile as PR
        from stockanalysis.webapp import longterm_view
        self.PR = PR
        self.view = longterm_view

    # ── the description ─────────────────────────────────────────────────────

    def test_it_trims_on_a_sentence_boundary(self):
        text = ("Alpha Corp makes widgets for industry. It also services "
                "them. A third sentence that should not appear.")
        out = self.PR.summarize(text)
        self.assertTrue(out.startswith("Alpha Corp makes widgets"))
        self.assertNotIn("third sentence", out)
        self.assertFalse(out.endswith(" It"))

    def test_a_company_suffix_is_not_a_sentence_end(self):
        # "N.V. provides" and "Inc. designs" continue in lower case, so the
        # split must not fire there — otherwise the description reads "ASML
        # Holding N.V."
        for name in ("ASML Holding N.V. provides lithography solutions.",
                     "Fortinet, Inc. provides cybersecurity worldwide.",
                     "Nomura Holdings, Ltd. operates as a financial firm."):
            self.assertEqual(self.PR.summarize(name), name)

    def test_an_abbreviation_before_a_capital_does_not_truncate_it(self):
        # "U.S. Bancorp" splits after "U.S." on the capital that follows;
        # the minimum-length guard is what keeps the description from
        # becoming the string "U.S.".
        out = self.PR.summarize("U.S. Bancorp provides banking services.")
        self.assertIn("Bancorp", out)

    def test_it_respects_the_character_budget(self):
        long_text = " ".join(["Alpha Corp makes widgets for industry."] * 40)
        self.assertLessEqual(len(self.PR.summarize(long_text)),
                             self.PR.MAX_CHARS + 1)

    def test_one_endless_sentence_is_cut_at_a_word(self):
        out = self.PR.summarize("Alpha " + "widget " * 200 + "end.")
        self.assertTrue(out.endswith("…"))
        self.assertNotIn("  ", out)

    def test_no_summary_is_none_not_an_empty_card(self):
        for empty in (None, "", "   "):
            self.assertIsNone(self.PR.summarize(empty))

    # ── the position ────────────────────────────────────────────────────────

    def _panel(self, profile):
        return self.view._profile_panel({"ticker": "TEST", "sector": "Technology",
                                         "profile": profile})

    def test_a_computed_rank_always_states_what_it_measured(self):
        html = self._panel({"peer_group": "Software", "peer_rank": 1,
                            "peer_count": 18, "peer_share_pct": 66.0,
                            "position_label": "Dominant",
                            "position_tier": "dominant"})
        self.assertIn("Dominant", html)
        self.assertIn("market cap", html)
        self.assertIn("not market share", html)

    def test_only_the_overlay_may_call_something_a_monopoly(self):
        # Nothing the computed layer produces should reach the page as an
        # unqualified structural claim.
        computed = self._panel({"peer_group": "Semiconductor Equipment",
                                "peer_rank": 1, "peer_count": 10,
                                "peer_share_pct": 37.0,
                                "position_label": "#1", "position_tier": "top2"})
        self.assertNotIn("●", computed)

        curated = self._panel({"structure": "EUV monopoly",
                               "structure_note": "sole supplier of EUV systems",
                               "peer_rank": 1, "peer_count": 10,
                               "position_label": "#1", "position_tier": "top2"})
        self.assertIn("EUV monopoly", curated)
        self.assertIn("●", curated)            # marked as a verified fact
        self.assertIn("sole supplier", curated)
        # The curated entry replaces the computed rank rather than sitting
        # beside it, or the page would make two different claims at once.
        self.assertNotIn("tracked industry peers", curated)

    def test_an_unrankable_name_says_so(self):
        html = self._panel({"peer_group": "Shell Companies", "peer_rank": None,
                            "peer_count": 0})
        self.assertIn("nothing in this library to rank it against", html)

    def test_the_panel_survives_a_row_with_no_profile(self):
        # Every unit test in this file builds results straight from the
        # engine, which does not attach one.
        html = self.view._profile_panel(E.evaluate(_row()))
        self.assertIn("No business description", html)

    # ── attachment ──────────────────────────────────────────────────────────

    def test_attach_gives_every_row_a_profile(self):
        results = [E.evaluate(_row(Ticker="AAA")), E.evaluate(_row(Ticker="BBB"))]
        raw = {"AAA": _row(Ticker="AAA", BusinessSummary="AAA makes things."),
               "BBB": _row(Ticker="BBB")}
        self.PR.attach(results, raw, entries=[])
        self.assertEqual(results[0]["profile"]["description"], "AAA makes things.")
        self.assertIsNone(results[1]["profile"]["description"])

    def test_rank_is_computed_against_the_whole_library(self):
        # Ranking a two-name subset against itself would report "#1 of 2" for
        # a name that is 3rd in the library. The full index is what the rank
        # has to be measured in.
        entries = [{"ticker": t, "sector": "Technology", "market_cap": cap,
                    "raw": {"Industry": "Software"}}
                   for t, cap in (("BIG", 9e11), ("MID", 5e11), ("AAA", 1e11))]
        results = [E.evaluate(_row(Ticker="AAA"))]
        self.PR.attach(results, {"AAA": _row(Ticker="AAA")}, entries)
        self.assertEqual(results[0]["profile"]["peer_rank"], 3)
        self.assertEqual(results[0]["profile"]["peer_count"], 3)

    def test_a_broken_index_does_not_break_the_page(self):
        results = [E.evaluate(_row(Ticker="AAA"))]
        self.PR.attach(results, {"AAA": _row(Ticker="AAA")},
                       entries=[{"ticker": "AAA", "market_cap": "not-a-number"}])
        self.assertIn("profile", results[0])


class TestColumns(unittest.TestCase):
    def setUp(self):
        from stockanalysis.webapp import longterm_view
        self.view = longterm_view

    def test_the_ma_distance_columns_moved_to_the_reasoning_panel(self):
        # Reference prices, not judgments — four columns of noise before a
        # name is worth looking at.
        keys = [c[0] for c in self.view._COLUMNS]
        for gone in ("s1", "s2", "s3", "s4"):
            self.assertNotIn(gone, keys)
        html = self.view._levels_panel(E.evaluate(_row()))
        for label in ("8 EMA", "21 EMA", "50 MA", "200 MA"):
            self.assertIn(label, html)

    def test_price_support_and_resistance_are_columns(self):
        keys = [c[0] for c in self.view._COLUMNS]
        for key in ("price", "buyzone", "resistance"):
            self.assertIn(key, keys)
        labels = {c[0]: c[1] for c in self.view._COLUMNS}
        self.assertEqual(labels["buyzone"], "S1 \u00b7 Support")
        self.assertEqual(labels["resistance"], "R1 \u00b7 Resistance")

    def test_the_company_and_timing_verdicts_are_separate_columns(self):
        # The whole point: "excellent company" and "poor entry" are
        # different findings and one blended score cannot hold both.
        keys = [c[0] for c in self.view._COLUMNS]
        self.assertIn("investment", keys)
        self.assertIn("entry_score", keys)
        self.assertNotIn("lt", keys)

    def test_every_column_still_renders_a_cell(self):
        html = self.view._row(E.evaluate(_row()))
        for key, _l, _a, _t in self.view._COLUMNS:
            self.assertIn(f'data-col="{key}"', html)
        self.assertIn(f'colspan="{len(self.view._COLUMNS)}"', html)

    def _conf(self, **kw):
        return T.compute_support_confluence(_row(**kw))

    def test_the_band_boundary_is_the_buy_gate(self):
        # "Adequate" starts exactly where the engine starts issuing buys.
        floors = {name: floor for floor, name in T.CONFLUENCE_BANDS}
        self.assertEqual(floors["🟡 Adequate"], E.MIN_CONFLUENCE_HITS)

    def test_label_follows_the_count_not_the_score(self):
        # Two hits worth 50 points (50 MA + 200 MA) and two worth 25
        # (8/21 EMA + key level) must carry the SAME label.
        # ATR 3% -> tolerance 4.5%, wide enough for both MAs to count.
        heavy = self._conf(**{"Current Price": 100.0, "8EMA": 120.0,
                              "21EMA": 121.0, "50MA": 99.0, "200MA": 98.0,
                              "S1": None, "Prior_Breakout_Level": None,
                              "ATR_Pct": 3.0})
        light = self._conf(**{"Current Price": 100.0, "8EMA": 99.5,
                              "21EMA": 99.0, "50MA": 60.0, "200MA": 50.0,
                              "S1": 99.4, "Volume_Confirmation": None,
                              "Prior_Breakout_Level": None, "ATR_Pct": 3.0})
        self.assertEqual(heavy["agreeing"], 2)
        self.assertEqual(light["agreeing"], 2)
        self.assertEqual(heavy["label"], light["label"])
        # ...while the score still separates them for ranking.
        self.assertGreater(heavy["score"], light["score"])

    def test_every_band_is_reachable(self):
        seen = set()
        for n in range(0, 6):
            label = next(name for floor, name in T.CONFLUENCE_BANDS
                         if n >= floor)
            seen.add(label)
        self.assertEqual(seen, {name for _f, name in T.CONFLUENCE_BANDS})



class TestResistanceLevel(unittest.TestCase):
    """technicals.compute_resistance_level — the first thing overhead."""

    def test_a_tested_r1_wins(self):
        rz = T.compute_resistance_level(
            _row(**{"Current Price": 100.0, "R1": 108.0, "S1": 99.0,
                    "Touches": 55}))
        self.assertTrue(rz["actual_resistance"])
        self.assertEqual(rz["price"], 108.0)
        self.assertAlmostEqual(rz["distance_pct"], 8.0, places=1)

    def test_it_falls_back_to_the_nearest_average_above_price(self):
        rz = T.compute_resistance_level(
            _row(**{"Current Price": 100.0, "R1": None, "8EMA": 104.0,
                    "21EMA": 110.0, "50MA": 120.0, "200MA": 130.0,
                    "52W High": 200.0}))
        self.assertEqual(rz["source"], "moving_average")
        self.assertEqual(rz["price"], 104.0)
        self.assertFalse(rz["actual_resistance"])

    def test_an_uptrend_falls_back_to_the_52_week_high(self):
        """Every average sits BELOW a stock in a clean uptrend.

        Without this rung the column is blank for exactly the names worth
        owning — NVDA, MSFT and GILD all have nothing overhead but the high.
        """
        rz = T.compute_resistance_level(
            _row(**{"Current Price": 100.0, "R1": None, "8EMA": 98.0,
                    "21EMA": 95.0, "50MA": 90.0, "200MA": 80.0,
                    "Prior_Breakout_Level": 85.0, "52W High": 112.0}))
        self.assertEqual(rz["source"], "52w_high")
        self.assertEqual(rz["price"], 112.0)
        self.assertFalse(rz["actual_resistance"])

    def test_nothing_overhead_at_new_highs(self):
        rz = T.compute_resistance_level(
            _row(**{"Current Price": 100.0, "R1": None, "8EMA": 98.0,
                    "21EMA": 95.0, "50MA": 90.0, "200MA": 80.0,
                    "Prior_Breakout_Level": None, "52W High": 100.0}))
        self.assertIsNone(rz["price"])

    def test_a_level_below_price_is_not_resistance(self):
        rz = T.compute_resistance_level(
            _row(**{"Current Price": 100.0, "R1": 90.0, "8EMA": 105.0,
                    "21EMA": 106.0, "50MA": 107.0, "200MA": 108.0}))
        self.assertEqual(rz["source"], "moving_average")

    def test_it_is_attached_to_the_pullback_result(self):
        self.assertIn("resistance", T.compute_pullback(_row()))


class TestTouchAttribution(unittest.TestCase):
    """Touches/Volume_Confirmation describe whichever level is NEAREST.

    core.key_levels writes them for `nearest = s1 if (price - s1) <= (r1 -
    price) else r1`. Crediting them to S1 unconditionally makes a support
    shelf look better tested than it is whenever resistance is closer —
    which is the real WDC case: 329 touches belong to R1 at $454.48, not to
    the $420.26 shelf.
    """

    def test_touches_go_to_whichever_level_is_nearer(self):
        # R1 nearer: 454.48 - 438.40 = 16.08 vs 438.40 - 420.26 = 18.14
        wdc = _row(**{"Current Price": 438.40, "S1": 420.26, "R1": 454.48,
                      "Touches": 329, "Volume_Confirmation": True})
        self.assertEqual(T._touches_belong_to(wdc), "R1")
        self.assertIsNone(T.compute_buy_zone_level(wdc)["touches"])
        self.assertEqual(T.compute_resistance_level(wdc)["touches"], 329)

    def test_support_keeps_the_touches_when_it_is_nearer(self):
        row = _row(**{"Current Price": 100.0, "S1": 99.0, "R1": 130.0,
                      "Touches": 42, "Volume_Confirmation": True})
        self.assertEqual(T._touches_belong_to(row), "S1")
        bz = T.compute_buy_zone_level(row)
        self.assertEqual(bz["touches"], 42)
        self.assertTrue(bz["actual_support"])

    def test_an_unowned_touch_count_never_confirms_the_shelf(self):
        # The bug this guards: a shelf reading "volume-confirmed" on the
        # strength of a resistance level's volume.
        wdc = _row(**{"Current Price": 438.40, "S1": 420.26, "R1": 454.48,
                      "Touches": 329, "Volume_Confirmation": True})
        self.assertFalse(T.compute_buy_zone_level(wdc)["actual_support"])


class TestLongTermScreening(unittest.TestCase):
    """core.longterm.screen — rules over the engine's own columns."""

    def setUp(self):
        from stockanalysis.core.longterm import screen as LS
        self.LS = LS
        self.results = [
            E.evaluate(_row(Ticker="ELITE")),
            E.evaluate(_row(Ticker="WEAK", FCF_Positive=False,
                            DebtToEquity=400.0, CurrentRatio=0.6,
                            **{"Revenue": -5.0, "EPS_Growth%": -20.0,
                               "ReturnOnEquity%": 2.0, "OperatingMargin%": 1.0,
                               "GrossMargin%": 8.0, "FCF_Margin%": -5.0})),
        ]

    def test_it_reuses_the_screener_engine_not_a_second_one(self):
        from stockanalysis.core import screener as S
        # Resolvable by the shared rule engine...
        self.assertIn("lquality", S.FIELD_BY_KEY)
        # ...but absent from the /screener picker, whose universe has no
        # values for these fields — every rule would read "no data".
        self.assertNotIn("lquality", {f.key for f in S.FIELDS})

    def test_flatten_turns_a_nested_verdict_into_scalars(self):
        flat = self.LS.flatten(self.results[0])
        for key in ("lquality", "lq_tier", "valuation_band", "trend_state",
                    "stage", "s1_price", "r1_price", "readiness", "action"):
            self.assertIn(key, flat)
        self.assertNotIsInstance(flat["lquality"], dict)
        self.assertIs(flat["_result"], self.results[0])

    def test_every_declared_field_is_produced_by_flatten(self):
        # A field in the picker that flatten never writes would read
        # "no data" for every row — an option that cannot match anything.
        flat = self.LS.flatten(self.results[0])
        for spec in self.LS.LONGTERM_FIELDS:
            self.assertIn(spec.src, flat, f"{spec.key} has no value")

    def test_a_numeric_rule_filters(self):
        kept, conds, _ = self.LS.apply_rules(self.results, ["lquality:gte:85"])
        self.assertEqual([r["ticker"] for r in kept], ["ELITE"])
        self.assertEqual(len(conds), 1)

    def test_rules_combine_with_and(self):
        kept, _, _ = self.LS.apply_rules(
            self.results, ["lquality:gte:85", "lq_tier:eq:Elite"])
        self.assertEqual([r["ticker"] for r in kept], ["ELITE"])

    def test_rules_combine_with_or(self):
        kept, _, _ = self.LS.apply_rules(
            self.results, ["lquality:gte:85", "lquality:lt:0"], op="OR")
        self.assertEqual([r["ticker"] for r in kept], ["ELITE"])

    def test_an_unparseable_rule_drops_itself(self):
        # A pasted URL with one bad rule must not error the page.
        for bad in ("nosuchfield:gte:5", "lquality:nosuchop:5",
                    "lquality:gte:notanumber", "garbage", ""):
            kept, conds, _ = self.LS.apply_rules(self.results, [bad])
            self.assertEqual(conds, [], bad)
            self.assertEqual(len(kept), len(self.results), bad)

    def test_a_bool_rule_reads_true_and_false(self):
        self.assertIs(self.LS.parse_rule("s1_tested:eq:true").value, True)
        self.assertIs(self.LS.parse_rule("s1_tested:eq:false").value, False)

    def test_between_needs_both_bounds(self):
        self.assertIsNone(self.LS.parse_rule("lquality:between:90"))
        cond = self.LS.parse_rule("lquality:between:90,100")
        self.assertEqual((cond.value, cond.value2), (90.0, 100.0))

    def test_a_rule_round_trips_through_its_url_text(self):
        from stockanalysis.webapp import longterm_view as V
        for text in ("lquality:gte:85", "lq_tier:eq:Elite",
                     "s1_tested:eq:true", "lquality:between:90,100",
                     "cluster:eq:Strong", "cluster_count:gte:3"):
            cond = self.LS.parse_rule(text)
            self.assertEqual(V._rule_text(cond), text)

    def test_stats_name_the_binding_constraint(self):
        _, _, stats = self.LS.apply_rules(
            self.results, ["lquality:gte:85", "lq_tier:eq:Elite"])
        self.assertEqual(len(stats), 2)
        for st in stats:
            for key in ("alone", "missing", "without", "label"):
                self.assertIn(key, st)

    # ── The buy-zone filters ────────────────────────────────────────────────
    # "Which names have a buy zone" was unanswerable without opening 552
    # reasoning panels. The distinction these pin is between a zone EXISTING
    # and the price being IN it — conflating them hides every name that is
    # one pullback away, which is most of the useful list.

    def _cheap(self, **kw):
        """A business whose price the model can justify."""
        base = {"Current Price": 60.0, "FreeCashFlow": 12.8e9,
                "SharesOutstanding": 2.0e9, "FCF_CAGR%": 8.0,
                "Revenue_CAGR%": 8.0, "8EMA": 59.5, "21EMA": 59.0,
                "50MA": 57.0, "200MA": 52.0, "Prior_Breakout_Level": 56.0}
        base.update(kw)
        return E.evaluate(_row(**base), risk_free=0.042)

    def _dear(self, **kw):
        """AVGO's shape: elite, supported, and priced past any hurdle."""
        base = {"Current Price": 392.99, "FreeCashFlow": 26.9e9,
                "SharesOutstanding": 4.76e9, "FCF_CAGR%": 24.4,
                "Revenue_CAGR%": 24.0, "8EMA": 409.73, "21EMA": 402.62,
                "50MA": 390.33, "200MA": 368.25,
                "Prior_Breakout_Level": 351.84}
        base.update(kw)
        return E.evaluate(_row(**base), risk_free=0.042)

    def test_buy_zone_fields_are_flattened(self):
        flat = self.LS.flatten(self._cheap())
        for key in ("buy_zone", "buy_zone_reached", "buy_verdict",
                    "value_zone", "expected_cagr", "buy_below",
                    "fair_value_gap", "model_value"):
            self.assertIn(key, flat)

    def test_an_expensive_name_has_no_buy_zone(self):
        self.assertIs(self.LS.flatten(self._dear())["buy_zone"], False)

    def test_having_a_zone_and_being_in_it_are_different_filters(self):
        flat = self.LS.flatten(self._cheap())
        self.assertIsNotNone(flat["buy_zone"])
        self.assertIsNotNone(flat["buy_zone_reached"])
        # A zone the price has not reached must still be findable — those are
        # the names worth a price alert.
        kept, _, _ = self.LS.apply_rules(
            [self._cheap(), self._dear()],
            ["buy_zone:eq:true", "buy_zone_reached:eq:false"])
        for result in kept:
            zones = result["buy_zones"]["investment"]
            self.assertTrue(zones)
            self.assertFalse(any(z["reached"] for z in zones))

    def test_no_buy_zone_data_is_not_reported_as_no_buy_zone(self):
        # "measured and rejected" and "never measured" must not both answer
        # a buy_zone:eq:false search.
        result = dict(self._cheap())
        result.pop("buy_zones")
        flat = self.LS.flatten(result)
        self.assertIsNone(flat["buy_zone"])
        self.assertIsNone(flat["buy_zone_reached"])
        for rule in ("buy_zone:eq:true", "buy_zone:eq:false"):
            kept, _, _ = self.LS.apply_rules([result], [rule])
            self.assertEqual(kept, [], rule)

    def test_the_buy_zone_filters_agree_with_the_engine(self):
        # A filter that disagreed with the panel it filters would be worse
        # than no filter.
        for result in (self._cheap(), self._dear()):
            flat = self.LS.flatten(result)
            zones = result["buy_zones"]["investment"]
            self.assertEqual(flat["buy_zone"], bool(zones))
            self.assertEqual(flat["buy_verdict"],
                             result["buy_zones"]["verdict"]["action"])

    def test_value_zone_uses_the_labels_the_panel_renders(self):
        from stockanalysis.core.longterm import buy_zones as BZ
        spec = self.LS.LONGTERM_FIELD_BY_KEY["value_zone"]
        self.assertEqual(spec.values, BZ.VALUE_ZONES)
        self.assertIn(self.LS.flatten(self._dear())["value_zone"], spec.values)

    # ── The level-cluster filter ────────────────────────────────────────────
    # The Support column has shown the cluster band since it was built; these
    # pin the filter that reads it. The invariant that matters is the one the
    # cluster exists for: it is NOT support confluence, and a filter that
    # quietly became direction-aware would look right on most rows and be
    # wrong on exactly the coiled-under-price names it was added to find.

    def _cluster_row(self, **kw):
        """Price at 100 with the 8/21/50 EMAs wound inside 2% of it."""
        return _row(**{"Current Price": 100.0, "8EMA": 99.5, "21EMA": 99.0,
                       "50MA": 98.6, "200MA": 80.0, "S1": 98.8, "R1": 130.0,
                       **kw})

    def test_cluster_values_carry_no_emoji(self):
        # The band label is "🟢 Strong cluster"; the filter value has to
        # survive a URL round trip and be typed to match.
        for value in self.LS.CLUSTER_VALUES:
            self.assertNotIn(" ", value.split(" ")[0])
            self.assertTrue(value.isascii(), value)
        self.assertIn("Strong", self.LS.CLUSTER_VALUES)

    def test_cluster_reads_the_band_the_table_shows(self):
        result = E.evaluate(self._cluster_row())
        flat = self.LS.flatten(result)
        self.assertEqual(flat["cluster"], "Strong")
        # Same source as the Support cell — the two can never disagree.
        self.assertIn(flat["cluster"], result["ma_cluster"]["label"])
        self.assertEqual(flat["cluster_count"], result["ma_cluster"]["count"])

    def test_a_strong_cluster_is_selectable(self):
        coiled = E.evaluate(self._cluster_row(Ticker="COILED"))
        spread = E.evaluate(_row(Ticker="SPREAD", **{
            "Current Price": 100.0, "8EMA": 90.0, "21EMA": 80.0,
            "50MA": 70.0, "200MA": 60.0, "S1": 65.0, "R1": 140.0}))
        kept, _, _ = self.LS.apply_rules([coiled, spread], ["cluster:eq:Strong"])
        self.assertEqual([r["ticker"] for r in kept], ["COILED"])

    def test_the_cluster_is_not_direction_aware(self):
        # Levels stacked ABOVE the price: nothing is holding it up, so support
        # confluence is weak — and the coil is still real. Reporting only the
        # first would describe a decision point as an absence.
        overhead = E.evaluate(_row(**{
            "Current Price": 100.0, "8EMA": 100.6, "21EMA": 101.0,
            "50MA": 101.4, "200MA": 80.0, "S1": 88.0, "R1": 101.8}))
        flat = self.LS.flatten(overhead)
        self.assertEqual(flat["cluster"], "Strong")
        self.assertLess(flat["confluence_hits"], flat["cluster_count"])

    def test_cluster_span_measures_how_tight_the_coil_is(self):
        tight = self.LS.flatten(E.evaluate(self._cluster_row()))
        loose = self.LS.flatten(E.evaluate(_row(**{
            "Current Price": 100.0, "8EMA": 101.8, "21EMA": 99.0,
            "50MA": 98.3, "200MA": 80.0, "S1": 98.2, "R1": 130.0})))
        self.assertLess(tight["cluster_span"], loose["cluster_span"])

    def test_no_cluster_data_is_not_reported_as_no_levels(self):
        # "the engine did not measure this" and "it measured zero levels
        # nearby" are different answers, and an eq rule must match neither.
        result = dict(E.evaluate(_row()))
        result["ma_cluster"] = {}
        result["pullback"] = {k: v for k, v in result["pullback"].items()
                              if k != "ma_cluster"}
        flat = self.LS.flatten(result)
        self.assertIsNone(flat["cluster"])
        self.assertIsNone(flat["cluster_count"])
        for rule in ("cluster:eq:Strong", "cluster:eq:No level nearby"):
            kept, _, _ = self.LS.apply_rules([result], [rule])
            self.assertEqual(kept, [], rule)

    def test_headroom_is_none_when_the_levels_do_not_straddle(self):
        # Support above the price, or resistance below it, would invert the
        # ratio's meaning rather than merely making it large.
        flat = self.LS.flatten(E.evaluate(
            _row(**{"Current Price": 100.0, "S1": 120.0, "R1": 130.0,
                    "8EMA": 121.0, "21EMA": 122.0, "50MA": 123.0,
                    "200MA": 124.0, "Prior_Breakout_Level": None})))
        self.assertIsNone(flat["headroom_ratio"])

    def test_headroom_is_the_ratio_when_they_do_straddle(self):
        flat = self.LS.flatten(E.evaluate(
            _row(**{"Current Price": 100.0, "S1": 90.0, "R1": 120.0,
                    "Prior_Breakout_Level": None})))
        # 20% of headroom against 10% of give-back.
        self.assertAlmostEqual(flat["headroom_ratio"], 2.0, places=1)


class TestPresets(unittest.TestCase):
    """Every preset must be able to return something, and say so when it
    cannot yet.

    Three features in this engine were first built with thresholds that were
    empty by construction — the forward DCF, the confluence gate and the
    readiness bands. A preset that returns nothing on every library is
    indistinguishable from a broken one, so the counts in `note` are checked
    against the real thing rather than trusted.
    """

    def setUp(self):
        from stockanalysis.core.longterm import screen as LS
        self.LS = LS

    def test_every_preset_parses_to_valid_conditions(self):
        for preset in self.LS.PRESETS:
            conds = [self.LS.parse_rule(r) for r in preset["rules"]]
            self.assertTrue(all(c is not None for c in conds),
                            f"{preset['key']} has an unparseable rule")

    def test_every_preset_rule_names_a_real_field(self):
        for preset in self.LS.PRESETS:
            for rule in preset["rules"]:
                key = rule.split(":")[0]
                self.assertIn(key, self.LS.LONGTERM_FIELD_BY_KEY,
                              f"{preset['key']} -> unknown field {key}")

    def test_enum_rules_use_a_declared_value(self):
        # "valuation_band:eq:CHEAP" would parse and silently match nothing.
        for preset in self.LS.PRESETS:
            for rule in preset["rules"]:
                key, op, _, value = (rule.split(":", 2) + [""])[:3] + [
                    rule.split(":", 2)[2] if rule.count(":") >= 2 else ""]
                spec = self.LS.LONGTERM_FIELD_BY_KEY.get(key)
                if spec and spec.kind == self.LS.ENUM and op in ("eq", "ne"):
                    self.assertIn(value, spec.values,
                                  f"{preset['key']}: {value} is not a "
                                  f"value of {key}")

    def test_keys_and_groups_are_well_formed(self):
        keys = [p["key"] for p in self.LS.PRESETS]
        self.assertEqual(len(keys), len(set(keys)), "duplicate preset key")
        for preset in self.LS.PRESETS:
            self.assertIn(preset["group"], self.LS.PRESET_GROUPS)
            for field in ("icon", "name", "desc", "rules"):
                self.assertTrue(preset.get(field), f"{preset['key']}.{field}")

    def test_preset_rules_looks_up_by_key(self):
        first = self.LS.PRESETS[0]
        self.assertEqual(self.LS.preset_rules(first["key"]), list(first["rules"]))
        self.assertEqual(self.LS.preset_rules("nope"), [])

    def test_a_preset_actually_filters(self):
        results = [E.evaluate(_row(Ticker="ELITE")),
                   E.evaluate(_row(Ticker="WEAK", **{"Revenue": -5.0,
                                                     "EPS_Growth%": -20.0,
                                                     "ReturnOnEquity%": 2.0,
                                                     "OperatingMargin%": 1.0,
                                                     "GrossMargin%": 8.0,
                                                     "FCF_Margin%": -5.0}))]
        kept, conds, _ = self.LS.apply_rules(
            results, self.LS.preset_rules("quality_at_discount"))
        self.assertEqual(len(conds), 3)
        self.assertNotIn("WEAK", [r["ticker"] for r in kept])

    def test_no_preset_stores_a_hardcoded_count(self):
        """Counts are computed, never written down.

        A recorded count is a claim about a moving target. Splitting
        STAGE4_BREAKDOWN into "confirmed" and "unconfirmed" silently took
        Fallen Quality from 10 matches to 0 while its label still said 10,
        so clicking it returned an empty table.
        """
        for preset in self.LS.PRESETS:
            self.assertNotIn("note", preset, f"{preset['key']} stores a count")

    def test_preset_counts_match_what_applying_the_rules_returns(self):
        """The number on the pill is the number you get when you click it."""
        results = [E.evaluate(_row(Ticker=f"T{i}")) for i in range(3)]
        results[0]["quality"]["score"] = 40      # fails the quality screens
        counts = self.LS.preset_counts(results)
        for preset in self.LS.PRESETS:
            kept, _, _ = self.LS.apply_rules(results, preset["rules"])
            self.assertEqual(counts[preset["key"]], len(kept), preset["key"])

    def test_presets_do_not_pin_themselves_to_a_stage_name(self):
        """Fallen Quality asks "is price below the 200 MA", not "is the
        stage called STAGE4_BREAKDOWN" — so redefining a stage cannot empty
        it again."""
        rules = self.LS.preset_rules("fallen_quality")
        self.assertNotIn("stage", " ".join(rules))
        self.assertIn("lt_pct_vs_200ma:lt:0", rules)

    def test_statement_dependent_presets_are_flagged(self):
        """The four screens that need the annual statements declare it.

        Their fields are 0% covered until a scan runs
        core.longterm.fundamentals, so they return nothing today — not
        because the rules are wrong but because the reverse DCF has no
        inputs. The flag is what lets the UI say that instead of showing a
        bare zero.
        """
        statement_fields = {"implied_growth", "delivered_growth",
                            "growth_gap", "valuation_method",
                            "valuation_confidence"}
        for preset in self.LS.PRESETS:
            uses = {r.split(":")[0] for r in preset["rules"]}
            needs = bool(uses & {"implied_growth", "delivered_growth",
                                 "growth_gap"}) or (
                "valuation_method" in uses)
            if needs:
                self.assertTrue(preset.get("needs_statements"),
                                f"{preset['key']} depends on statement data "
                                f"but is not flagged")


class TestBelow200MASplitsByMeasuredSlope(unittest.TestCase):
    """A breakdown is price below a FALLING 200 MA — not below any 200 MA.

    Meta is the case that exposed it: below its 200 MA with a death cross
    but both slopes unscanned, reported as a confirmed trend breakdown on
    the strength of one inequality. Price under a RISING long-term average
    is a deep correction, and the two call for opposite actions.
    """

    def _below(self, **kw):
        base = {"Current Price": 90.0, "200MA": 100.0, "50MA": 95.0,
                "8EMA": 99.0, "21EMA": 98.0, "Pct_vs_8EMA": -9.1,
                "S1": None, "R1": None, "Prior_Breakout_Level": None}
        base.update(kw)
        return _row(**base)

    def test_unmeasured_slopes_are_unconfirmed_not_broken(self):
        pull = T.compute_pullback(self._below(**{"MA200_Slope%": None,
                                                 "MA50_Slope%": None}))
        self.assertEqual(pull["stage"], "STAGE4_UNCONFIRMED")
        self.assertIn("cannot be said", pull["note"])

    def test_a_falling_200ma_is_a_confirmed_breakdown(self):
        pull = T.compute_pullback(self._below(**{"MA200_Slope%": -2.0,
                                                 "MA50_Slope%": -3.0}))
        self.assertEqual(pull["stage"], "STAGE4_BREAKDOWN")

    def test_a_rising_200ma_is_a_deep_pullback(self):
        pull = T.compute_pullback(self._below(**{"MA200_Slope%": 1.5,
                                                 "MA50_Slope%": 1.0}))
        self.assertEqual(pull["stage"], "STAGE3_DEEP")

    def test_trend_is_impaired_not_broken_without_the_slope(self):
        trend = T.compute_trend(self._below(**{"MA200_Slope%": None,
                                               "MA50_Slope%": None,
                                               "Above_200MA": False}))
        self.assertEqual(trend["state"], "IMPAIRED")
        self.assertIs(trend["pass"], False)   # still not a trend to buy into
        # The summary names what failed AND that the slope is unread, rather
        # than asserting the slopes are missing on every impaired row.
        self.assertIn("unmeasured", trend["summary"])
        self.assertIn("200 MA", trend["summary"])

    def test_trend_is_broken_once_the_slope_confirms_it(self):
        trend = T.compute_trend(self._below(**{"MA200_Slope%": -2.0,
                                               "MA50_Slope%": -2.0,
                                               "Above_200MA": False}))
        self.assertEqual(trend["state"], "BROKEN")


class TestMACluster(unittest.TestCase):
    """Compression around the price — a different question from support."""

    def test_it_counts_levels_on_both_sides(self):
        # Support confluence only counts levels price sits ABOVE; a cluster
        # is about how tightly the averages are wound, either side.
        cluster = T.compute_ma_cluster(
            _row(**{"Current Price": 100.0, "8EMA": 99.4, "21EMA": 100.9,
                    "50MA": 101.3, "200MA": 140.0, "S1": 99.6, "R1": 100.2,
                    "Prior_Breakout_Level": None}))
        self.assertEqual(cluster["count"], 5)
        self.assertIn("Strong", cluster["label"])
        self.assertLess(cluster["span_pct"], 2.5)

    def test_a_far_flung_chart_has_no_cluster(self):
        cluster = T.compute_ma_cluster(
            _row(**{"Current Price": 100.0, "8EMA": 80.0, "21EMA": 70.0,
                    "50MA": 60.0, "200MA": 50.0, "S1": None, "R1": None,
                    "Prior_Breakout_Level": None}))
        self.assertEqual(cluster["count"], 0)

    def test_a_cluster_can_coexist_with_weak_support(self):
        """Meta: five levels inside 1.9%, only two of them beneath price.

        Both readings are correct, and reporting only the support score
        describes a tight coil as an absence.
        """
        row = _row(**{"Current Price": 592.10, "8EMA": 588.38,
                      "21EMA": 597.75, "50MA": 599.62, "200MA": 630.44,
                      "S1": 589.98, "R1": 593.55, "ATR_Pct": 3.88,
                      "Prior_Breakout_Level": None})
        cluster = T.compute_ma_cluster(row)
        conf = T.compute_support_confluence(row)
        self.assertGreaterEqual(cluster["count"], 4)
        self.assertLess(conf["score"], 50)


class TestTwoVerdicts(unittest.TestCase):
    """Company verdict and timing verdict, reported separately."""

    def test_an_elite_business_at_a_bad_price_stays_a_core_holding(self):
        """A demanding price is a reason to wait, not to stop wanting it.

        Valuation is 10% of the company question; at 30% it dragged
        Nvidia at 98 quality out of the top tier, which is the opposite of
        useful — the waiting belongs on the entry side.
        """
        r = E.evaluate(_row(FreeCashFlow=2.0e8,
                            **{"FCF_CAGR%": 3.0, "Revenue_CAGR%": 3.0,
                               "Revenue": 3.0}))
        # Still an ownable tier despite the price — valuation costs about
        # ten points, so a 98 stays CORE and an 88 slips to OWN. What must
        # NOT happen is an elite business falling out of the ownable tiers
        # because it is expensive.
        self.assertIn(r["investment"]["status"], E.OWNABLE_TIERS)
        self.assertIn("price is demanding", r["investment"]["why"])
        self.assertIs(r["investment"]["priced_well"], False)
        self.assertGreaterEqual(r["quality"]["score"], 85)

    def test_quality_and_price_both_fine_is_a_core_holding(self):
        r = E.evaluate(_row())
        self.assertEqual(r["investment"]["status"], "CORE")
        self.assertIs(r["investment"]["priced_well"], True)

    def test_a_weak_business_is_avoid_whatever_the_chart(self):
        r = E.evaluate(_row(**{"Revenue": -5.0, "EPS_Growth%": -20.0,
                               "ReturnOnEquity%": 2.0, "OperatingMargin%": 1.0,
                               "GrossMargin%": 8.0, "FCF_Margin%": -5.0}))
        self.assertEqual(r["investment"]["status"], "REJECT")

    def test_the_two_verdicts_can_disagree(self):
        # The pairing the page exists to show: worth owning, not worth
        # buying today.
        r = E.evaluate(_row(FreeCashFlow=2.0e8,
                            **{"FCF_CAGR%": 3.0, "Revenue_CAGR%": 3.0,
                               "Revenue": 3.0}))
        self.assertIn(r["investment"]["status"], E.OWNABLE_TIERS)
        self.assertEqual(r["action"], "OWN / WAIT FOR PRICE")

    def test_five_component_scores_are_reported_separately(self):
        r = E.evaluate(_row())
        keys = [c["key"] for c in r["components"]]
        self.assertEqual(keys, ["quality", "valuation", "trend", "pullback",
                                "support"])

    def test_thesis_broken_needs_both_halves(self):
        weak = {"Revenue": -5.0, "EPS_Growth%": -20.0, "ReturnOnEquity%": 2.0,
                "OperatingMargin%": 1.0, "GrossMargin%": 8.0,
                "FCF_Margin%": -5.0}
        # Weak business, trend not measurably broken -> plain AVOID.
        self.assertEqual(E.evaluate(_row(**weak))["action"], "AVOID")
        # Weak business AND a falling 200 MA -> the numbers and tape agree.
        broken = dict(weak)
        broken.update({"Current Price": 90.0, "200MA": 100.0, "50MA": 95.0,
                       "MA200_Slope%": -2.0, "MA50_Slope%": -2.0,
                       "Above_200MA": False})
        self.assertEqual(E.evaluate(_row(**broken))["action"], "THESIS BROKEN")


class TestRecoveringTrend(unittest.TestCase):
    """A 50/200 inversion means opposite things either side of the 200 MA.

    Palantir: price $172.01, above its 200 MA at $152.28 and 30% above its
    50 MA at $132.60, with the 50 MA still under the 200 MA. Nothing is
    damaged — the 50 MA is a lagging average that has not yet climbed back
    through the 200 after an earlier decline. That is a golden cross pending,
    not a death cross just struck, and reporting it as "damaged" inverted the
    reading for 49 library rows.
    """

    def _pltr(self, **kw):
        base = {"Current Price": 172.01, "200MA": 152.28, "50MA": 132.60,
                "21EMA": 138.21, "8EMA": 149.26, "Above_200MA": True,
                "MA200_Slope%": None, "MA50_Slope%": None,
                "Price_vs_50MA%": 29.7}
        base.update(kw)
        return _row(**base)

    def test_above_the_200ma_with_a_pending_cross_is_recovering(self):
        trend = T.compute_trend(self._pltr())
        self.assertEqual(trend["state"], "RECOVERING")
        self.assertEqual(trend["structural_failed"], ["50 MA above 200 MA"])

    def test_recovering_does_not_eject_the_name_from_the_ladder(self):
        # Price is above the long-term average, so this is a trend awaiting
        # confirmation rather than one to walk away from.
        self.assertIsNone(T.compute_trend(self._pltr())["pass"])

    def test_below_the_200ma_is_still_impaired(self):
        trend = T.compute_trend(self._pltr(**{"Current Price": 140.0,
                                              "Above_200MA": False}))
        self.assertEqual(trend["state"], "IMPAIRED")
        self.assertIs(trend["pass"], False)

    def test_a_falling_200ma_does_not_cancel_the_recovery_reading(self):
        """A falling 200 MA is the footprint of the decline being recovered
        from, not evidence against the recovery.

        This asserted BROKEN until 2026-08-09. The reading was an accident of
        an exact list comparison: `structural_failed == ["50 MA above 200
        MA"]` matched only while the slopes were unscanned, so the moment
        Palantir's 200 MA slope was measured, "200 MA rising" joined the
        failed list and the branch written FOR Palantir stopped matching it.
        Demanding the 200 MA already be rising is demanding the recovery be
        over before calling it one.
        """
        trend = T.compute_trend(self._pltr(**{"MA200_Slope%": -2.0,
                                              "MA50_Slope%": -0.49}))
        self.assertEqual(trend["state"], "RECOVERING")
        self.assertIn("50 MA above 200 MA", trend["structural_failed"])
        self.assertIn("200 MA rising", trend["structural_failed"])

    def test_the_inversion_must_be_present_for_a_recovery_reading(self):
        """The other side of that boundary, which is what stops the widened
        rule swallowing its opposite.

        Price above the 200 MA with the 50 MA ALSO above it and only the
        long-term slope failing is a healthy structure beginning to roll
        over — deterioration, not repair. Dropping the inversion from the
        test collapsed the two shapes into one and read a topping name as a
        recovering one.
        """
        rolling_over = self._pltr(**{"50MA": 160.0,        # back above the 200
                                     "MA200_Slope%": -2.0,
                                     "MA50_Slope%": -0.1})
        trend = T.compute_trend(rolling_over)
        self.assertEqual(trend["structural_failed"], ["200 MA rising"])
        self.assertEqual(trend["state"], "BROKEN")

    def test_recovering_is_only_for_the_cross_not_any_failure(self):
        # Price below the 200 MA AND a crossed pair is not a recovery.
        trend = T.compute_trend(self._pltr(**{"Current Price": 100.0,
                                              "Above_200MA": False}))
        self.assertNotEqual(trend["state"], "RECOVERING")

    def test_the_ranking_places_recovering_between_partial_and_impaired(self):
        from stockanalysis.webapp import longterm_view as V
        rank = V._TREND_RANK
        self.assertGreater(rank["PARTIAL"], rank["RECOVERING"])
        self.assertGreater(rank["RECOVERING"], rank["IMPAIRED"])

    def test_every_state_has_an_icon_summary_and_rank(self):
        from stockanalysis.webapp import longterm_view as V
        for state in T.TREND_STATES:
            self.assertIn(state, T.TREND_ICONS)
            self.assertIn(state, T.TREND_SUMMARY)
            self.assertIn(state, V._TREND_RANK)


class TestTrendSlopeSemantics(unittest.TestCase):
    """What the two slopes are allowed to decide.

    Nvidia drove both fixes here: freshly scanned, with a rising 200 MA and
    a cooling 50 MA, it was reported as a damaged trend AND told that its
    moving-average slopes were unmeasured — while carrying both of them.
    """

    def _nvda(self, **kw):
        base = {"Current Price": 223.96, "200MA": 193.80, "50MA": 206.05,
                "21EMA": 207.73, "8EMA": 212.82, "Above_200MA": True,
                "Price_vs_50MA%": 8.7,
                "MA200_Slope%": 1.22, "MA50_Slope%": -1.45}
        base.update(kw)
        return _row(**base)

    def test_a_cooling_50ma_cannot_break_a_rising_200ma(self):
        # An intermediate average rolling over inside a rising long-term one
        # is the ordinary shape of a buyable pullback.
        trend = T.compute_trend(self._nvda())
        self.assertEqual(trend["state"], "CONFIRMED")

    def test_but_it_still_costs_the_score(self):
        cooling = T.compute_trend(self._nvda())
        rising = T.compute_trend(self._nvda(**{"MA50_Slope%": 2.0}))
        self.assertLess(cooling["score"], rising["score"])
        self.assertIn("50 MA rising", cooling["failed"])

    def test_the_50ma_slope_is_not_structural(self):
        structural = {name for name, _w, s in T._TREND_CHECKS if s}
        self.assertIn("200 MA rising", structural)
        self.assertNotIn("50 MA rising", structural)

    def test_the_summary_never_claims_unmeasured_slopes_it_has(self):
        trend = T.compute_trend(self._nvda(**{"Above_200MA": False,
                                              "Current Price": 150.0,
                                              "MA200_Slope%": 1.0,
                                              "MA50_Slope%": -1.0}))
        self.assertEqual(trend["state"], "IMPAIRED")
        self.assertNotIn("unmeasured", trend["summary"])
        self.assertNotIn("without", trend["summary"])

    def test_a_breakdown_needs_both_averages_pointing_down(self):
        """Microsoft: 200 MA -2.01%, 50 MA +0.91%.

        A falling long-term average with the intermediate one already
        turning up is how every recovery begins; reading only the slower
        average calls it a breakdown.
        """
        recovering = T.compute_trend(self._nvda(**{"MA200_Slope%": -2.01,
                                                   "MA50_Slope%": 0.91}))
        self.assertEqual(recovering["state"], "RECOVERING")
        self.assertIn("50 MA is rising", recovering["summary"])

        broken = T.compute_trend(self._nvda(**{"MA200_Slope%": -1.75,
                                               "MA50_Slope%": -0.08}))
        self.assertEqual(broken["state"], "BROKEN")

    def test_recovering_covers_both_shapes_of_repair(self):
        # (a) price back above the 200 MA, averages still crossed
        crossed = T.compute_trend(
            _row(**{"Current Price": 172.01, "200MA": 152.28, "50MA": 132.60,
                    "21EMA": 138.21, "8EMA": 149.26, "Above_200MA": True,
                    "MA200_Slope%": None, "MA50_Slope%": None}))
        self.assertEqual(crossed["state"], "RECOVERING")
        # (b) the 50 MA turning up under a still-falling 200 MA
        turning = T.compute_trend(self._nvda(**{"MA200_Slope%": -3.84,
                                                "MA50_Slope%": 9.81}))
        self.assertEqual(turning["state"], "RECOVERING")


class TestListFilter(unittest.TestCase):
    """Watchlist / daytrade selection on the /longterm page."""

    def setUp(self):
        from stockanalysis.webapp import api, longterm_view
        self.view = longterm_view
        self.api = api
        self._real_lt = api.longterm
        self._real_lists = api.longterm_lists
        rows = [E.evaluate(_row(Ticker=t))
                for t in ("NVDA", "META", "AMD", "KO")]
        api.longterm = lambda override=None: {
            "rows": rows, "counts": {}, "regime": "FAVORABLE",
            "regime_note": "t", "risk_free_note": "t",
            "coverage": {"total": 4, "ma_slope": 4, "reversal": 4,
                         "statements": 4, "breakout": 4,
                         "needs_rescan": False}}
        api.longterm_lists = lambda: {
            "daytrade": ["NVDA", "META", "AMD", "ZZZZ"],
            "Longterm": ["KO"]}

    def tearDown(self):
        self.api.longterm = self._real_lt
        self.api.longterm_lists = self._real_lists

    def test_a_list_narrows_the_universe(self):
        body, _ = self.view.longterm_page({"list": ["daytrade"]})
        self.assertEqual(body.count('data-main="1"'), 3)
        self.assertNotIn(">KO<", body)

    def test_list_members_with_no_research_page_are_named(self):
        # Same rule as the ticker search: a 4-name list showing 3 rows must
        # say why, or it reads as data loss.
        body, _ = self.view.longterm_page({"list": ["daytrade"]})
        self.assertIn("no research page yet", body)
        self.assertIn("ZZZZ", body)

    def test_a_list_and_a_ticker_search_intersect(self):
        body, _ = self.view.longterm_page({"list": ["daytrade"],
                                           "q": ["NVDA, KO"]})
        # KO is not on the list, so it cannot survive the intersection.
        self.assertEqual(body.count('data-main="1"'), 1)
        self.assertIn(">NVDA<", body)

    def test_an_unknown_list_leaves_the_universe_alone(self):
        body, _ = self.view.longterm_page({"list": ["no-such-list"]})
        self.assertEqual(body.count('data-main="1"'), 4)

    def test_a_chosen_list_is_never_truncated(self):
        # Asking for "daytrade" and being shown the first few silently is
        # worse than a long page.
        body, _ = self.view.longterm_page({"list": ["daytrade"],
                                           "limit": ["1"]})
        self.assertEqual(body.count('data-main="1"'), 3)

    def test_the_list_survives_a_chip_or_preset_click(self):
        body, _ = self.view.longterm_page({"list": ["daytrade"]})
        self.assertIn("list=daytrade", body)

    def test_the_picker_offers_every_non_empty_list(self):
        body, _ = self.view.longterm_page({})
        for name in ("daytrade", "Longterm"):
            self.assertIn(f'<option value="{name}"', body)
        self.assertIn('<option value="">All tickers</option>', body)


class TestRowLimit(unittest.TestCase):
    def setUp(self):
        from stockanalysis.webapp import api, longterm_view
        self.view = longterm_view
        self.api = api
        self._real = api.longterm
        rows = [E.evaluate(_row(Ticker=f"T{i:03d}")) for i in range(120)]
        api.longterm = lambda override=None: {
            "rows": rows, "counts": {}, "regime": "FAVORABLE",
            "regime_note": "t", "risk_free_note": "t",
            "coverage": {"total": 120, "ma_slope": 120, "reversal": 120,
                         "statements": 120, "breakout": 120,
                         "needs_rescan": False}}

    def tearDown(self):
        self.api.longterm = self._real

    def test_the_default_limit_is_actually_the_default(self):
        """Clamping before defaulting made this ten for weeks.

        max(10, min(500, 0)) is 10, so `limit or DEFAULT_LIMIT` could never
        fire and the page showed ten rows while claiming a default of sixty.
        """
        body, _ = self.view.longterm_page({})
        self.assertEqual(body.count('data-main="1"'),
                         self.view.DEFAULT_LIMIT)

    def test_an_explicit_limit_is_honoured(self):
        body, _ = self.view.longterm_page({"limit": ["25"]})
        self.assertEqual(body.count('data-main="1"'), 25)

    def test_a_junk_limit_falls_back_rather_than_erroring(self):
        body, _ = self.view.longterm_page({"limit": ["banana"]})
        self.assertEqual(body.count('data-main="1"'),
                         self.view.DEFAULT_LIMIT)


class TestFCFMarginComesFromStatements(unittest.TestCase):
    """FCF margin must not inherit the broken .info["freeCashflow"].

    Alphabet reported a 5.1% FCF margin beside "compounding +7%/yr, positive
    in 4 of 4 years" — two lines that cannot both describe the same company.
    The margin was $22.7B/$445.9B from .info; the filed statements say
    $164.7B operating cash flow less $91.4B capex = $73.3B, a margin of 18%.
    """

    def setUp(self):
        from stockanalysis.core.longterm import fundamentals as F
        self.F = F

    def _frames(self, fcf, revenue):
        import pandas as pd
        cols = pd.to_datetime(["2025-12-31", "2024-12-31"])
        cash = pd.DataFrame([fcf], index=["Free Cash Flow"], columns=cols)
        inc = pd.DataFrame([revenue], index=["Total Revenue"], columns=cols)
        return cash, inc

    def test_margin_is_computed_from_the_statement_not_info(self):
        cash, inc = self._frames([73.3e9, 72.8e9], [402.5e9, 350.0e9])
        out = self.F.compute_fundamentals(cash, inc)
        self.assertAlmostEqual(out["FCF_Margin%"], 18.2, places=1)
        self.assertIs(out["FCF_Positive"], True)

    def test_both_sides_come_from_the_same_fiscal_year(self):
        # Pairing a statement numerator with a trailing-twelve-month
        # denominator quietly mixes periods.
        cash, inc = self._frames([100e9, 90e9], [500e9, 400e9])
        self.assertEqual(
            self.F.compute_fundamentals(cash, inc)["FCF_Margin%"], 20.0)

    def test_negative_free_cash_flow_reads_as_negative(self):
        cash, inc = self._frames([-5e9, 2e9], [100e9, 90e9])
        out = self.F.compute_fundamentals(cash, inc)
        self.assertIs(out["FCF_Positive"], False)
        self.assertLess(out["FCF_Margin%"], 0)
        self.assertEqual(out["FCF_Positive_Years"], 1)

    def test_no_statements_means_no_margin_rather_than_a_wrong_one(self):
        out = self.F.compute_fundamentals(None, None)
        self.assertIsNone(out["FCF_Margin%"])
        self.assertIsNone(out["FCF_Positive"])

    def test_metrics_no_longer_derives_the_margin_from_info(self):
        import inspect
        from stockanalysis.core import metrics
        src = inspect.getsource(metrics.get_metrics)
        # The only assignment left should be the None placeholder the
        # statements overwrite.
        self.assertNotIn('info.get("freeCashflow")', src)


class TestInvestmentTiersAndNamedWaits(unittest.TestCase):
    """Company tier is fundamentals-driven; the wait says what it waits for."""

    def test_valuation_is_a_tenth_of_the_company_question(self):
        # At 30% an expensive price pulled elite businesses out of the top
        # tier, which reads as "bad investment" when the finding is "bad
        # price". Waiting belongs to the entry side.
        self.assertEqual(E.INVESTMENT_WEIGHTS, (90, 10))

    def test_an_elite_business_survives_a_demanding_price(self):
        elite = _row(**{"EPS_Growth%": 60.0, "Revenue": 40.0,
                        "ReturnOnEquity%": 45.0, "OperatingMargin%": 45.0,
                        "GrossMargin%": 78.0, "FCF_Margin%": 35.0})
        priced = E.evaluate(elite)
        expensive = E.evaluate(dict(elite, FreeCashFlow=2.0e8,
                                    **{"FCF_CAGR%": 3.0, "Revenue_CAGR%": 3.0,
                                       "Revenue": 3.0}))
        self.assertEqual(priced["investment"]["status"], "CORE")
        self.assertIn(expensive["investment"]["status"], E.OWNABLE_TIERS)

    def test_the_four_tiers_are_ordered_and_reachable(self):
        floors = [f for f, _n in E.INVESTMENT_TIERS]
        self.assertEqual(floors, sorted(floors, reverse=True))
        for score, want in ((92, "CORE"), (80, "OWN"), (70, "WATCHLIST"),
                            (40, "REJECT")):
            self.assertEqual(E.investment_tier(score), want)

    def test_each_wait_names_what_it_is_waiting_for(self):
        core = {"status": "CORE"}
        self.assertEqual(E._wait_label(core, "price"), "OWN / WAIT FOR PRICE")
        self.assertEqual(E._wait_label(core, "trend"), "OWN / WAIT FOR TREND")
        self.assertEqual(E._wait_label(core, "entry"), "OWN / WAIT FOR ENTRY")

    def test_a_name_not_worth_owning_gets_no_own_label(self):
        weak = {"status": "REJECT"}
        for reason in ("price", "trend", "entry"):
            self.assertNotIn("OWN", E._wait_label(weak, reason))

    def test_every_action_has_a_colour_label_and_rank(self):
        from stockanalysis.webapp import longterm_view as V
        for action in E.ACTIONS:
            self.assertIn(action, V._ACTION_STYLE, action)
            self.assertIn(action, V._ACTION_SHORT, action)
            self.assertIn(action, V._ACTION_RANK, action)


class TestTrancheSizing(unittest.TestCase):
    """Size follows the entry score, and is stated as % of TARGET position."""

    def test_a_better_entry_earns_more_capital(self):
        sizes = [E.tranche_for(s) for s in (85, 75, 65, 55, 30)]
        self.assertEqual(sizes, [40, 28, 18, 8, 0])
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_a_weak_entry_gets_nothing(self):
        self.assertEqual(E.tranche_for(20), 0)

    def test_it_falls_back_to_the_zone_when_entry_is_unscored(self):
        self.assertEqual(E.tranche_for(None, "50MA"), E.ZONE_TRANCHE["50MA"])

    def test_the_ui_says_of_target_not_bare_tranche(self):
        # "28% tranche" is ambiguous between 28% of this holding and 28% of
        # the portfolio — a difference that matters enormously.
        from stockanalysis.webapp import longterm_view as V
        html = V._row(E.evaluate(_row()))
        self.assertNotIn("% tranche", html)
        if "of target" in html:
            self.assertIn("% of target", html)


class TestEarningsRisk(unittest.TestCase):
    def test_the_bands_run_from_clear_to_imminent(self):
        for days, want in ((45, "Clear"), (20, "Approaching"), (10, "Near"),
                           (3, "Imminent"), (0, "Imminent")):
            self.assertIn(want, E.earnings_risk(days)["label"])

    def test_a_passed_report_is_clear_not_imminent(self):
        self.assertIn("Clear", E.earnings_risk(-4)["label"])

    def test_unknown_stays_unknown(self):
        self.assertIn("Unknown", E.earnings_risk(None)["label"])

    def test_it_rides_along_with_every_verdict(self):
        self.assertIn("earnings_risk", E.evaluate(_row()))


class TestSwingSetupInEntry(unittest.TestCase):
    """The scan's Swing_Score as an independent read on entry timing.

    Brown & Brown argued for it: on its 8 EMA with a Momentum-Pullback
    classification, 6.8 R:R and RS 88, the swing engine calls it a buy while
    the long-term readiness sees only a moving average nearby. The swing
    score carries pattern work — category, risk/reward, ATR contraction,
    Bollinger position — that this engine has no other access to.
    """

    def test_a_real_swing_score_is_used(self):
        score, note = E.swing_signal(
            {"Swing_Score": 75, "Entry_Gate_Pass": True,
             "Category": "Momentum-Pullback", "Grade": "B", "RR_T2": 6.81})
        self.assertEqual(score, 75)
        self.assertIn("Momentum-Pullback", note)
        self.assertIn("R:R 6.8", note)

    def test_a_gate_zeroed_score_is_unmeasured_not_zero(self):
        """126 of 552 library rows carry a swing score of 0 because the
        scan's entry gate failed — an administrative zero, not a verdict.

        core.strategy_scores deliberately lets the INVESTMENT score survive
        a flat-ADX gate failure, since a 6-24 month accumulation does not
        need a trend already in place. Folding the zero into a long-term
        entry would punish exactly that case.
        """
        score, note = E.swing_signal({"Swing_Score": 0,
                                      "Entry_Gate_Pass": False})
        self.assertIsNone(score)
        self.assertIn("entry gate", note)

    def test_a_genuine_zero_with_the_gate_passing_still_counts(self):
        score, _ = E.swing_signal({"Swing_Score": 0, "Entry_Gate_Pass": True})
        self.assertEqual(score, 0)

    def test_an_absent_score_is_unmeasured(self):
        self.assertIsNone(E.swing_signal({})[0])

    def test_the_missing_leg_renormalises_rather_than_scoring_zero(self):
        legs = ({"score": 80, "state": "CONFIRMED"}, {"score": 80, "label": ""},
                {"score": 80, "label": ""}, {"score": 80, "label": ""})
        with_swing = E.entry_view(*legs, {"Swing_Score": 80,
                                          "Entry_Gate_Pass": True})
        gated = E.entry_view(*legs, {"Swing_Score": 0,
                                     "Entry_Gate_Pass": False})
        # Same four measured legs at 80 -> same score; the gated row must
        # not be dragged toward zero by an administrative blank.
        self.assertEqual(with_swing["score"], 80)
        self.assertEqual(gated["score"], 80)
        self.assertLess(gated["coverage"], with_swing["coverage"])

    def test_a_strong_swing_setup_lifts_the_entry_score(self):
        legs = ({"score": 40, "state": "PARTIAL"}, {"score": 40, "label": ""},
                {"score": 40, "label": ""}, {"score": 40, "label": ""})
        weak = E.entry_view(*legs, {"Swing_Score": 10, "Entry_Gate_Pass": True})
        strong = E.entry_view(*legs, {"Swing_Score": 90,
                                      "Entry_Gate_Pass": True})
        self.assertGreater(strong["score"], weak["score"])

    def test_the_weights_still_sum_to_one_hundred(self):
        self.assertEqual(sum(w for _n, w in E.ENTRY_WEIGHTS), 100)

    def test_the_top_tranche_band_is_reachable(self):
        # A 40%-of-target band that no score can reach is empty by
        # construction — the trap this engine has fallen into three times.
        perfect = E.entry_view(
            {"score": 100, "state": "CONFIRMED"}, {"score": 100, "label": ""},
            {"score": 100, "label": ""}, {"score": 100, "label": ""},
            {"Swing_Score": 100, "Entry_Gate_Pass": True})
        self.assertGreaterEqual(perfect["score"], 80)
        self.assertEqual(E.tranche_for(perfect["score"]), 40)


class TestSwingConfirmsEntry(unittest.TestCase):
    """A strong swing setup can stand in for a reversal candle.

    The scan's Swing_Score above SWING_CONFIRMS already encodes a recognised
    setup, a risk/reward to target and volatility contraction — independent
    pattern work reaching the same conclusion the candle would. What it must
    NOT do is override a measured failure, or reach past the quality,
    valuation and trend gates.
    """

    def _at_level(self, **kw):
        # Prior breakout removed so the stop is not 0.8% away — otherwise
        # the risk/reward rule confirms first and this class tests nothing.
        # ATR 4% widens the tolerance to 6%, so the 8/21 EMA and the
        # breakout shelf both count and the row clears the confluence gate.
        # The shelf sits 4% down, far enough that risk/reward is 2:1 rather
        # than a manufactured 10:1 off a 0.8% stop — otherwise the R:R rule
        # confirms first and this class tests nothing.
        base = {"Reversal_Candle": None, "Swing_Score": 80,
                "Entry_Gate_Pass": True, "Category": "Momentum-Pullback",
                "Grade": "B", "RR_T2": 5.0, "Prior_Breakout_Level": 96.0,
                "S1": None, "ATR_Pct": 4.0, "52W High": 108.0}
        base.update(kw)
        return _row(**base)

    def test_it_rescues_an_unmeasured_candle(self):
        r = E.evaluate(self._at_level())
        self.assertEqual(r["action"], "BUY NOW")
        self.assertTrue(any("swing setup instead" in t for t in r["why"]),
                        "the reason the buy fired must survive the buy")

    def test_a_weak_swing_score_does_not(self):
        r = E.evaluate(self._at_level(**{"Swing_Score": 55}))
        self.assertNotEqual(r["action"], "BUY NOW")

    def test_it_cannot_override_a_measured_failure(self):
        # Overbought into the level is a reason the setup is wrong, not a
        # gap in what was measured.
        r = E.evaluate(self._at_level(**{"Reversal_Candle": "none",
                                         "RSI_14": 78.0}))
        self.assertNotEqual(r["action"], "BUY NOW")

    def test_distributing_volume_still_blocks(self):
        r = E.evaluate(self._at_level(**{"Vol_vs_20D": 2.4,
                                         "Pullback_Vol_Ratio": 2.2,
                                         "VolumeDryingUp": False,
                                         "Distribution_Days_25d": 9}))
        self.assertNotEqual(r["action"], "BUY NOW")

    def test_it_cannot_reach_past_the_quality_gate(self):
        # The hierarchy is structural. A perfect swing setup on a weak
        # business is still not a long-term buy.
        r = E.evaluate(self._at_level(**{"Revenue": -5.0, "EPS_Growth%": -20.0,
                                         "ReturnOnEquity%": 2.0,
                                         "OperatingMargin%": 1.0,
                                         "GrossMargin%": 8.0,
                                         "FCF_Margin%": -5.0}))
        self.assertEqual(r["gate"], "quality")
        self.assertIn(r["action"], ("AVOID", "THESIS BROKEN"))

    def test_it_cannot_reach_past_the_valuation_gate(self):
        r = E.evaluate(self._at_level(FreeCashFlow=2.0e8,
                                      **{"FCF_CAGR%": 3.0,
                                         "Revenue_CAGR%": 3.0,
                                         "Revenue": 3.0}))
        self.assertEqual(r["gate"], "valuation")

    def test_a_gate_zeroed_swing_cannot_confirm(self):
        r = E.evaluate(self._at_level(**{"Swing_Score": 0,
                                         "Entry_Gate_Pass": False}))
        self.assertNotEqual(r["action"], "BUY NOW")

    def test_the_threshold_matches_the_screener_card(self):
        # The Screener prints "Swing Score 75 (> 70)"; one bar, one number.
        self.assertEqual(E.SWING_CONFIRMS, 70)


class TestSwingColumn(unittest.TestCase):
    def test_swing_is_a_column(self):
        from stockanalysis.webapp import longterm_view as V
        self.assertIn("swing", [c[0] for c in V._COLUMNS])

    def test_the_column_leads_with_the_pure_technical_score(self):
        from stockanalysis.webapp import longterm_view as V
        r = E.evaluate(_row(**{"Swing_Score": 75, "Entry_Gate_Pass": True}))
        html, sort_value = V._swing_cell(r)
        self.assertEqual(sort_value, r["technical"]["score"])
        self.assertIn(str(r["technical"]["score"]), html)

    def test_the_scan_swing_rides_underneath_as_context(self):
        from stockanalysis.webapp import longterm_view as V
        html, _ = V._swing_cell(
            E.evaluate(_row(**{"Swing_Score": 75, "Entry_Gate_Pass": True})))
        self.assertIn("scan swing 75", html)

    def test_a_gate_zeroed_scan_swing_shows_a_dash(self):
        # "0" and "not scored" mean different things and must not look alike.
        from stockanalysis.webapp import longterm_view as V
        html, _ = V._swing_cell(
            E.evaluate(_row(**{"Swing_Score": 0, "Entry_Gate_Pass": False})))
        self.assertIn("scan swing —", html)


class TestTargetsAndRiskReward(unittest.TestCase):
    """Stop, T1, T2 — the trade priced so risk/reward is a number."""

    def _wdc(self, **kw):
        base = {"Current Price": 434.30, "S1": 422.50, "R1": 454.49,
                "8EMA": 491.46, "21EMA": 516.73, "50MA": 561.18,
                "200MA": 341.09, "52W High": 799.87, "ATR_Pct": 12.29,
                "Volume_Confirmation": True, "Touches": 300,
                "Prior_Breakout_Level": None}
        base.update(kw)
        return _row(**base)

    def test_targets_climb_the_real_level_ladder(self):
        t = T.compute_targets(self._wdc())
        self.assertEqual(t["stop"], 422.50)
        names = [lv["name"] for lv in t["ladder"]]
        self.assertTrue(names[0].startswith("R1"))
        self.assertIn("8 EMA", names)

    def test_both_targets_are_reported_because_they_differ_threefold(self):
        # 1.7:1 to R1, 4.8:1 to the 8 EMA — quoting only the nearer target
        # would understate the setup by a factor of three.
        t = T.compute_targets(self._wdc())
        self.assertLess(t["rr_t1"], 2.0)
        self.assertGreater(t["rr_t2"], 4.0)

    def test_a_level_below_the_price_is_never_a_target(self):
        t = T.compute_targets(self._wdc())
        for lv in t["ladder"]:
            self.assertGreater(lv["price"], 434.30)

    def test_no_support_means_no_trade_to_price(self):
        t = T.compute_targets(self._wdc(**{"S1": None, "8EMA": 500.0,
                                           "21EMA": 510.0, "50MA": 520.0,
                                           "200MA": 530.0}))
        self.assertIsNone(t["stop"])


class TestRiskRewardCanCarryAnEntry(unittest.TestCase):
    def _setup(self, **kw):
        base = {"Current Price": 100.0, "S1": 95.0, "R1": 130.0,
                "8EMA": 99.0, "21EMA": 98.0, "50MA": 97.0, "200MA": 80.0,
                "Prior_Breakout_Level": 96.0, "52W High": 140.0,
                "ATR_Pct": 4.0, "Reversal_Candle": None, "Swing_Score": None,
                "Volume_Confirmation": True, "Touches": 40}
        base.update(kw)
        return _row(**base)

    def test_a_strong_ratio_substitutes_for_the_trigger(self):
        r = E.evaluate(self._setup())
        self.assertEqual(r["action"], "BUY NOW")
        self.assertTrue(any("carries the entry" in w for w in r["why"]))

    def test_a_level_inside_the_noise_is_never_the_stop(self):
        """A shelf 0.8% under the price is not a stop.

        Risk/reward is a ratio, so a level ordinary noise removes before
        lunch produces a 30:1 setup out of nothing. The ladder skips past
        anything inside MIN_STOP_PCT and uses the next real level, rather
        than quoting a ratio measured against proximity.
        """
        t = T.compute_targets(self._setup(**{"S1": 99.2,
                                             "Prior_Breakout_Level": 99.2}))
        self.assertLessEqual(t["stop"], 100.0 * (1 - T.MIN_STOP_PCT / 100))
        self.assertGreaterEqual(abs(t["risk_pct"]), T.MIN_STOP_PCT)

    def test_no_level_far_enough_means_no_trade_to_price(self):
        t = T.compute_targets(self._setup(**{"S1": 99.6, "8EMA": 99.5,
                                             "21EMA": 99.4, "50MA": 99.3,
                                             "200MA": 99.2,
                                             "Prior_Breakout_Level": 99.1}))
        self.assertIsNone(t["stop"])

    def test_it_needs_the_quality_to_earn_it(self):
        r = E.evaluate(self._setup(**{"Revenue": -5.0, "EPS_Growth%": -20.0,
                                      "ReturnOnEquity%": 2.0,
                                      "OperatingMargin%": 1.0,
                                      "GrossMargin%": 8.0,
                                      "FCF_Margin%": -5.0}))
        self.assertNotEqual(r["action"], "BUY NOW")

    def test_it_cannot_reach_past_valuation_on_an_unconfirmed_trend(self):
        """The trigger override and the VALUATION override are separate.

        A 4:1 ratio substitutes for a reversal candle. Getting past a
        demanding price needs more — an elite business AND a confirmed
        trend AND a bigger ratio — so without the slopes this still stops.
        """
        r = E.evaluate(self._setup(FreeCashFlow=2.0e8,
                                   **{"FCF_CAGR%": 3.0, "Revenue_CAGR%": 3.0,
                                      "Revenue": 3.0, "MA200_Slope%": None,
                                      "MA50_Slope%": None}))
        self.assertNotEqual(r["trend"]["state"], "CONFIRMED")
        self.assertEqual(r["gate"], "valuation")


class TestPureTechnicalScore(unittest.TestCase):
    def test_it_shares_no_input_with_quality_or_valuation(self):
        base = _row()
        rich = E.evaluate(base)
        poor = E.evaluate(_row(**{"Revenue": -5.0, "EPS_Growth%": -20.0,
                                  "ReturnOnEquity%": 2.0,
                                  "OperatingMargin%": 1.0,
                                  "GrossMargin%": 8.0, "FCF_Margin%": -5.0}))
        # Same chart, opposite fundamentals -> identical technical score.
        self.assertEqual(rich["technical"]["score"], poor["technical"]["score"])

    def test_depth_has_an_optimal_zone(self):
        def tech(vs50):
            r = _row(**{"Price_vs_50MA%": vs50})
            return T.compute_technical_score(
                r, T.compute_trend(r), T.compute_pullback(r),
                T.compute_support_confluence(r), T.compute_pullback_volume(r),
                T.compute_targets(r))["score"]
        # A 10% pullback beats both a 1% one and a 35% collapse.
        self.assertGreater(tech(-10), tech(-1))
        self.assertGreater(tech(-10), tech(-35))

    def test_the_weights_sum_to_one_hundred(self):
        self.assertEqual(sum(w for _n, w in T.TECHNICAL_WEIGHTS), 100)


class TestValuationOverride(unittest.TestCase):
    """An exceptional risk/reward downgrades OVERVALUED to a warning.

    This is the one place the hierarchy bends, so every guard exists to make
    it bend rather than break. Valuation says what you pay for the business
    over years; risk/reward says what this entry costs if the thesis is
    wrong now. A 7:1 setup on an elite company in a confirmed uptrend risks
    a few percent to a defended level, and refusing it on a trailing
    cash-flow multiple treats a cyclical trough as permanent.
    """

    def _expensive(self, **kw):
        # Elite business, confirmed trend, priced far beyond what it delivers.
        base = {"Current Price": 100.0, "S1": 94.0, "R1": 112.0,
                "8EMA": 99.0, "21EMA": 98.0, "50MA": 96.0, "200MA": 80.0,
                "Prior_Breakout_Level": 95.0, "52W High": 160.0,
                "ATR_Pct": 4.0, "MA200_Slope%": 5.0, "MA50_Slope%": 2.0,
                "Above_200MA": True, "FreeCashFlow": 2.0e8,
                "FCF_CAGR%": 3.0, "Revenue_CAGR%": 3.0, "Revenue": 3.0,
                "EPS_Growth%": 60.0, "ReturnOnEquity%": 45.0,
                "OperatingMargin%": 45.0, "GrossMargin%": 78.0,
                "FCF_Margin%": 35.0}
        base.update(kw)
        return _row(**base)

    def test_it_lets_an_exceptional_setup_past_the_valuation_gate(self):
        r = E.evaluate(self._expensive())
        self.assertNotEqual(r["gate"], "valuation")
        self.assertTrue(any("overridden by" in w for w in r["why"]))

    def test_the_price_is_still_reported_as_demanding(self):
        # Downgraded to a warning, not waived — the card must still say the
        # price is demanding.
        r = E.evaluate(self._expensive())
        self.assertTrue(any("demanding" in b for b in r["blockers"]))

    def test_overvalued_is_never_listed_as_a_reason_to_buy(self):
        r = E.evaluate(self._expensive())
        self.assertFalse(any("Overvalued" in w for w in r["why"]),
                         "an overvalued reading is not an argument for the trade")

    def test_a_merely_good_ratio_does_not_qualify(self):
        # 4:1 carries a trigger; the valuation override asks for more.
        r = E.evaluate(self._expensive(**{"R1": 104.0, "52W High": 106.0,
                                          "8EMA": 103.0, "21EMA": 103.5,
                                          "50MA": 104.5}))
        self.assertEqual(r["gate"], "valuation")

    def test_an_unconfirmed_trend_does_not_qualify(self):
        # Not merely un-broken — CONFIRMED. Without the slopes the engine
        # cannot say the uptrend is intact, and that is exactly when a
        # demanding price should still stop it.
        r = E.evaluate(self._expensive(**{"MA200_Slope%": None,
                                          "MA50_Slope%": None}))
        self.assertEqual(r["gate"], "valuation")

    def test_a_lesser_business_does_not_qualify(self):
        # Ownable, but not elite — reaches the valuation gate and stops
        # there, because the override is for 85+ only.
        r = E.evaluate(self._expensive(**{"EPS_Growth%": 20.0,
                                          "ReturnOnEquity%": 20.0,
                                          "OperatingMargin%": 20.0,
                                          "GrossMargin%": 50.0,
                                          "FCF_Margin%": 12.0,
                                          "Revenue": 12.0}))
        self.assertGreaterEqual(r["quality"]["score"], Q.MIN_OWNABLE)
        self.assertLess(r["quality"]["score"], E.VALUATION_OVERRIDE_QUALITY)
        self.assertEqual(r["gate"], "valuation")

    def test_a_tight_stop_cannot_buy_the_override(self):
        r = E.evaluate(self._expensive(**{"S1": 99.4,
                                          "Prior_Breakout_Level": 99.3,
                                          "8EMA": 99.5, "21EMA": 99.6,
                                          "50MA": 99.7}))
        self.assertEqual(r["gate"], "valuation")

    def test_the_ratio_cannot_be_borrowed_from_a_distant_old_high(self):
        # Only the first three targets are eligible, so a collapsed name
        # cannot qualify on a 52-week high nobody expects it to see.
        t = T.compute_targets(self._expensive())
        eligible = (t["ladder"] or [])[:E.VALUATION_OVERRIDE_TARGETS]
        self.assertLessEqual(len(eligible), 3)

    def test_the_bar_sits_above_the_one_that_carries_a_trigger(self):
        self.assertGreater(E.VALUATION_OVERRIDE_RR, E.RR_CONFIRMS)
