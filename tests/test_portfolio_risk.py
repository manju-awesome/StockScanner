"""
Tests for core/portfolio_risk.py + portfolio_risk_scores.py — the portfolio
risk report behind the Portfolio page's "Analyze Portfolio" button.

Focus is on the arithmetic that would be wrong *silently*: option delta signs,
the exposure denominator, and the bounded statistics. A risk report that
renders beautifully with an inverted short delta or a −108% drawdown is worse
than no report, because it looks authoritative.

No network: every test builds its own price frames.
"""

import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import portfolio_risk as pr            # noqa: E402
from stockanalysis.core import portfolio_risk_scores as prs    # noqa: E402


def price_frame(specs: dict, days: int = 400, seed: int = 7) -> pd.DataFrame:
    """Deterministic close frame. specs maps ticker -> (start, daily drift,
    daily vol)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=days)
    out = {}
    for ticker, (start, drift, vol) in specs.items():
        steps = rng.normal(drift, vol, days)
        out[ticker] = start * np.exp(np.cumsum(steps))
    return pd.DataFrame(out, index=idx)


class TestOptionDelta(unittest.TestCase):
    """Sign and magnitude of delta — the numbers that decide whether a hedge
    reads as a hedge or as more of the same bet."""

    def test_call_delta_between_zero_and_one(self):
        d = pr.bs_delta(100, 100, 0.25, 0.4, 0.04, is_call=True)
        self.assertTrue(0 < d < 1)
        self.assertAlmostEqual(d, 0.56, places=1)

    def test_put_delta_is_negative(self):
        d = pr.bs_delta(100, 100, 0.25, 0.4, 0.04, is_call=False)
        self.assertTrue(-1 < d < 0)

    def test_deep_itm_call_approaches_one(self):
        self.assertGreater(pr.bs_delta(300, 100, 0.5, 0.3, 0.04, True), 0.98)

    def test_deep_otm_call_approaches_zero(self):
        self.assertLess(pr.bs_delta(50, 200, 0.05, 0.3, 0.04, True), 0.01)

    def test_expired_contract_falls_back_to_intrinsic(self):
        """An expired-but-still-listed row must report the exposure it has,
        not raise and take the whole report down."""
        self.assertEqual(pr.bs_delta(100, 90, 0, 0.4, 0.04, True), 1.0)
        self.assertEqual(pr.bs_delta(100, 110, 0, 0.4, 0.04, True), 0.0)
        self.assertEqual(pr.bs_delta(100, 110, 0, 0.4, 0.04, False), -1.0)

    def test_degenerate_inputs_do_not_raise(self):
        for args in ((0, 100, 0.5, 0.3, 0.04, True),
                     (100, 0, 0.5, 0.3, 0.04, True),
                     (100, 100, 0.5, 0, 0.04, True),
                     (None, None, 0.5, 0.3, 0.04, True)):
            self.assertIsInstance(pr.bs_delta(*args), float)


class TestOptionExposureSigns(unittest.TestCase):
    """Long call and short call on the same strike must point opposite ways.
    This is the failure that would net a hedge into the position it hedges."""

    def setUp(self):
        self.closes = price_frame({"XYZ": (100.0, 0.0002, 0.02)})
        self.vols = {"XYZ": 40.0}
        self.exp = date.today() + timedelta(days=60)

    def _row(self, otype, side):
        opt = {"Underlying": "XYZ", "Type": otype, "Side": side, "Strike": 100.0,
               "Expiration": self.exp, "Contracts": 2.0, "Avg_Premium": 5.0,
               "Current_Premium": 6.0}
        return pr.build_option_rows([opt], self.closes, self.vols, 0.04)[0]

    def test_long_call_is_positive_exposure(self):
        self.assertGreater(self._row("call", "long")["Delta_Notional"], 0)

    def test_short_call_is_negative_exposure(self):
        self.assertLess(self._row("call", "short")["Delta_Notional"], 0)

    def test_long_put_is_negative_exposure(self):
        """A protective put reduces directional exposure. If this ever comes
        back positive, every concentration number involving options is wrong."""
        self.assertLess(self._row("put", "long")["Delta_Notional"], 0)

    def test_short_put_is_positive_exposure(self):
        self.assertGreater(self._row("put", "short")["Delta_Notional"], 0)

    def test_long_and_short_call_are_mirror_images(self):
        long_row, short_row = self._row("call", "long"), self._row("call", "short")
        self.assertAlmostEqual(long_row["Delta_Notional"],
                               -short_row["Delta_Notional"], places=4)

    def test_premium_at_risk_only_applies_to_longs(self):
        """A long option can lose its premium; a short one's risk is open-ended
        and premium says nothing about it, so reporting a number would lie."""
        self.assertAlmostEqual(self._row("call", "long")["Premium_At_Risk"], 1000.0)
        self.assertIsNone(self._row("call", "short")["Premium_At_Risk"])

    def test_market_value_sign_follows_side(self):
        self.assertGreater(self._row("call", "long")["Market_Value"], 0)
        self.assertLess(self._row("call", "short")["Market_Value"], 0)

    def test_zero_contract_rows_are_dropped(self):
        opt = {"Underlying": "XYZ", "Type": "call", "Side": "long", "Strike": 100.0,
               "Expiration": self.exp, "Contracts": 0.0, "Avg_Premium": 5.0,
               "Current_Premium": 6.0}
        self.assertEqual(pr.build_option_rows([opt], self.closes, self.vols, 0.04), [])


class TestPortfolioValueBasis(unittest.TestCase):
    """The denominator every weight divides by. Getting this from the wrong
    place made a $19k book read as 81% cash and cleared every limit."""

    def setUp(self):
        self._saved = prs.os.environ.pop("PORTFOLIO_VALUE", None)

    def tearDown(self):
        if self._saved is not None:
            prs.os.environ["PORTFOLIO_VALUE"] = self._saved
        else:
            prs.os.environ.pop("PORTFOLIO_VALUE", None)

    def test_defaults_to_invested_capital(self):
        value, basis = prs.resolve_portfolio_value(18000.0, 1300.0)
        self.assertAlmostEqual(value, 19300.0)
        self.assertIn("invested capital", basis)

    def test_explicit_env_value_wins(self):
        prs.os.environ["PORTFOLIO_VALUE"] = "250000"
        value, basis = prs.resolve_portfolio_value(18000.0, 1300.0)
        self.assertEqual(value, 250000.0)
        self.assertIn("PORTFOLIO_VALUE", basis)

    def test_does_not_fall_back_to_account_size(self):
        """ACCOUNT_SIZE is a per-trade risk-sizing constant, not a statement
        about what the account holds — using it as the denominator is the bug
        this test exists to prevent."""
        prs.os.environ["ACCOUNT_SIZE"] = "100000"
        value, _ = prs.resolve_portfolio_value(18000.0, 1300.0)
        self.assertNotEqual(value, 100000.0)

    def test_garbage_env_value_falls_back(self):
        prs.os.environ["PORTFOLIO_VALUE"] = "not a number"
        value, basis = prs.resolve_portfolio_value(18000.0, 1300.0)
        self.assertAlmostEqual(value, 19300.0)
        self.assertIn("invested capital", basis)

    def test_never_returns_zero(self):
        """An empty book must not produce a zero denominator — every weight
        downstream divides by it."""
        value, _ = prs.resolve_portfolio_value(0.0, 0.0)
        self.assertGreater(value, 0)


class TestStressBounds(unittest.TestCase):
    """Statistics that must stay inside their natural bounds."""

    def _stress(self, daily_vol, days=500, drift=0.0004):
        rng = np.random.default_rng(3)
        idx = pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=days)
        rets = pd.Series(rng.normal(drift, daily_vol, days), index=idx)
        return prs.stress_test(rets, pd.DataFrame(), {}, 100000.0, 100000.0)

    def test_expected_max_drawdown_cannot_exceed_100pct(self):
        """Regression: the original 1.75×σ formula returned −107.8% on a 62%
        vol book. A portfolio cannot lose more than everything."""
        for daily_vol in (0.01, 0.04, 0.08, 0.15):
            dd = self._stress(daily_vol)["expected_max_drawdown_pct"]
            self.assertGreater(dd, -100.0, f"daily vol {daily_vol}")
            self.assertLess(dd, 0.0)

    def test_expected_drawdown_grows_with_volatility(self):
        low = self._stress(0.01)["expected_max_drawdown_pct"]
        high = self._stress(0.06)["expected_max_drawdown_pct"]
        self.assertLess(high, low)

    def test_var_99_is_worse_than_var_95(self):
        s = self._stress(0.02)
        self.assertLess(s["var_99_pct"], s["var_95_pct"])

    def test_expected_shortfall_is_worse_than_var(self):
        """ES is the average loss *beyond* VaR, so it must be the larger loss."""
        s = self._stress(0.02)
        self.assertLess(s["expected_shortfall_95_pct"], s["var_95_pct"])
        self.assertLess(s["expected_shortfall_99_pct"], s["var_99_pct"])

    def test_negative_drift_reports_no_recovery_estimate(self):
        s = self._stress(0.02, drift=-0.005)
        self.assertIsNone(s["expected_recovery_months"])
        self.assertIn("negative", s["recovery_basis"])

    def test_negligible_drift_refuses_to_project_a_recovery(self):
        """A drift indistinguishable from zero produces arithmetic like "266
        months", which implies a precision the sample can't support.

        The series is de-meaned and given an exact tiny drift rather than
        drawn with one: over 500 samples at 2% vol, sampling noise on the mean
        is ~1000× the drift being tested, so asking the RNG for it would test
        the seed rather than the code.
        """
        rng = np.random.default_rng(11)
        idx = pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=500)
        raw = rng.normal(0, 0.02, 500)
        rets = pd.Series(raw - raw.mean() + 1e-6, index=idx)
        s = prs.stress_test(rets, pd.DataFrame(), {}, 100000.0, 100000.0)
        self.assertIsNone(s["expected_recovery_months"])
        self.assertIn("too weak", s["recovery_basis"])

    def test_short_history_refuses_rather_than_guessing(self):
        rng = np.random.default_rng(1)
        rets = pd.Series(rng.normal(0, 0.02, 20),
                         index=pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=20))
        s = prs.stress_test(rets, pd.DataFrame(), {}, 100000.0, 100000.0)
        self.assertIsNone(s["var_95_pct"])
        self.assertIn("not enough", s["note"])

    def test_cash_scales_the_loss(self):
        """A half-invested book takes half the hit — the stress numbers are
        stated against total portfolio value, not invested capital."""
        rng = np.random.default_rng(5)
        idx = pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=400)
        rets = pd.Series(rng.normal(0, 0.02, 400), index=idx)
        full = prs.stress_test(rets, pd.DataFrame(), {}, 100000.0, 100000.0)
        half = prs.stress_test(rets, pd.DataFrame(), {}, 100000.0, 50000.0)
        self.assertAlmostEqual(half["annual_vol_pct"], full["annual_vol_pct"] / 2, places=1)


class TestReturnMath(unittest.TestCase):

    def test_beta_of_a_series_against_itself_is_one(self):
        closes = price_frame({"A": (100.0, 0.0003, 0.02)})
        rets = pr.daily_returns(closes)["A"]
        self.assertAlmostEqual(pr.beta_against(rets, rets), 1.0, places=6)

    def test_beta_of_a_doubled_series_is_two(self):
        closes = price_frame({"A": (100.0, 0.0003, 0.02)})
        rets = pr.daily_returns(closes)["A"]
        self.assertAlmostEqual(pr.beta_against(rets * 2, rets), 2.0, places=6)

    def test_beta_returns_none_on_thin_overlap(self):
        closes = price_frame({"A": (100.0, 0.0003, 0.02)}, days=300)
        rets = pr.daily_returns(closes)["A"]
        self.assertIsNone(pr.beta_against(rets.head(10), rets))

    def test_correlation_of_identical_series_is_one(self):
        closes = price_frame({"A": (100.0, 0.0003, 0.02)})
        rets = pr.daily_returns(closes)["A"]
        self.assertAlmostEqual(pr.correlation(rets, rets), 1.0, places=6)

    def test_max_drawdown_is_negative_and_bounded(self):
        closes = price_frame({"A": (100.0, -0.001, 0.03)})
        dd, days = pr.max_drawdown(closes["A"])
        self.assertLess(dd, 0)
        self.assertGreater(dd, -100.0)

    def test_max_drawdown_of_a_monotonic_riser_is_zero(self):
        s = pd.Series(np.arange(1, 200, dtype=float),
                      index=pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=199))
        dd, _ = pr.max_drawdown(s)
        self.assertAlmostEqual(dd, 0.0, places=6)

    def test_tnx_is_treated_as_a_level_not_a_price(self):
        """^TNX quotes the yield directly in percent. Running pct_change on it
        would call a 25bp move "+6%" and wreck the rates scenario."""
        self.assertIn("TNX", pr.LEVEL_FACTORS)
        closes = pd.DataFrame(
            {"^TNX": [4.00, 4.25, 4.10]},
            index=pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=3))
        series = pr.factor_series(closes)["TNX"]
        self.assertAlmostEqual(series.iloc[1], 0.25, places=6)

    def test_risk_free_rate_does_not_divide_tnx_by_ten(self):
        closes = pd.DataFrame(
            {"^TNX": [4.20, 4.20, 4.20]},
            index=pd.bdate_range(end=pd.Timestamp("2026-07-24"), periods=3))
        self.assertAlmostEqual(pr.risk_free_rate(closes), 0.042, places=6)


class TestClassification(unittest.TestCase):

    def test_cap_buckets_separate_small_from_micro(self):
        """The limit sheet caps small (20%) and micro (10%) separately, so
        collapsing them makes the micro limit unenforceable."""
        self.assertEqual(pr.cap_bucket(3e12), "Mega")
        self.assertEqual(pr.cap_bucket(50e9), "Large")
        self.assertEqual(pr.cap_bucket(5e9), "Mid")
        self.assertEqual(pr.cap_bucket(1e9), "Small")
        self.assertEqual(pr.cap_bucket(1e8), "Micro")
        self.assertIsNone(pr.cap_bucket(None))

    def test_themes_come_from_explicit_membership(self):
        themes = pr.classify_themes("OKLO", {})
        self.assertIn("Nuclear", themes)

    def test_themes_fall_back_to_industry_keywords(self):
        themes = pr.classify_themes("ZZZZ", {"Industry": "Semiconductors",
                                             "Name": "Example Chip Co"})
        self.assertIn("Semiconductor", themes)

    def test_etf_resolves_to_a_sector_through_lookthrough(self):
        """An ETF with sector 'Unknown' would hide real concentration."""
        self.assertEqual(pr.resolve_sector("IGV", {}), "Technology")
        self.assertEqual(pr.resolve_sector("GLD", {}), "Commodity — Gold")

    def test_explicit_sector_wins_over_lookthrough(self):
        self.assertEqual(pr.resolve_sector("SPY", {"Sector": "Financial Services"}),
                         "Financial Services")


class TestJsonSafety(unittest.TestCase):
    """The report is cached as JSON; numpy scalars raise on json.dumps()."""

    def test_numpy_scalars_are_cast(self):
        import json
        payload = pr.jsonable({
            "f": np.float64(1.5), "i": np.int64(3), "b": np.bool_(True),
            "nested": [np.float32(2.5), {"deep": np.int32(9)}],
            "when": pd.Timestamp("2026-07-24"),
            "day": date(2026, 7, 24),
        })
        json.dumps(payload)                      # must not raise
        self.assertEqual(payload["i"], 3)
        self.assertIs(payload["b"], True)
        self.assertEqual(payload["day"], "2026-07-24")

    def test_nan_and_inf_become_none(self):
        """json.dumps happily writes NaN, which is invalid JSON and breaks
        JSON.parse in the browser."""
        payload = pr.jsonable({"a": float("nan"), "b": float("inf"),
                               "c": np.float64("nan")})
        self.assertIsNone(payload["a"])
        self.assertIsNone(payload["b"])
        self.assertIsNone(payload["c"])


class TestLimitsAndSizing(unittest.TestCase):

    def _holding(self, ticker, weight, **over):
        h = {"Ticker": ticker, "Weight_Pct": weight, "Exposure": weight * 100,
             "Sector": "Technology", "Industry": "Software", "Country": "United States",
             "Cap_Bucket": "Large", "Themes": ["AI"], "Strategy": "longterm",
             "Vol_90D": 30.0, "Beta_SPY": 1.0, "Market_Cap": 50e9, "Price": 100.0}
        h.update(over)
        return h

    def test_dust_is_bucketed_not_scored(self):
        """A 0.002-share stub shouldn't collect vol/trend penalties and land
        in the exit list next to a real position."""
        holdings = [self._holding("DUST", 0.05, Is_Dust=True, Vol_90D=120.0)]
        out = prs.position_sizing(holdings, [], [], [], {"DUST": {}})
        self.assertEqual(out[0]["Action"], "CLOSE")
        self.assertTrue(out[0]["Is_Dust"])

    def test_oversized_high_vol_broken_trend_is_trimmed_or_exited(self):
        holdings = [self._holding("BIG", 25.0, Vol_90D=95.0)]
        tech = {"BIG": {"Above_200MA": False, "Dist_200MA_Pct": -40.0,
                        "Trend": "Strong downtrend"}}
        out = prs.position_sizing(holdings, [], [], [], tech)
        self.assertIn(out[0]["Action"], ("TRIM", "EXIT"))

    def test_never_recommends_adding_past_the_cap(self):
        """The cap is the binding constraint whatever the other votes say."""
        holdings = [self._holding("GOOD", 30.0, ROE=35.0, Debt_Equity=20.0)]
        tech = {"GOOD": {"Above_200MA": True, "Trend": "Strong uptrend", "RSI": 45}}
        out = prs.position_sizing(holdings, [], [], [], tech)
        self.assertNotEqual(out[0]["Action"], "INCREASE")

    def test_missing_fundamentals_do_not_penalize(self):
        """A missing ROE is not a bad ROE — otherwise every ETF gets marked
        down for being an ETF."""
        holdings = [self._holding("ETF", 5.0, ROE=None, Debt_Equity=None,
                                  Revenue_Growth=None)]
        tech = {"ETF": {"Above_200MA": True, "Trend": "Uptrend", "RSI": 55}}
        out = prs.position_sizing(holdings, [], [], [], tech)
        self.assertEqual(out[0]["Action"], "MAINTAIN")

    def test_etf_pseudo_industry_is_not_flagged(self):
        """'ETF / fund' is a placeholder for a missing industry, not an
        industry — flagging it tells the user their funds are concentrated in
        being funds."""
        holdings = [self._holding("A", 50.0, Industry="ETF / fund")]
        conc = prs.concentration(holdings, {"invested_pct": 50, "cash_pct": 50})
        violations = prs.limit_violations(holdings, conc, {"cash_pct": 50})
        self.assertFalse([v for v in violations
                          if v["limit"] == "industry" and v["scope"] == "ETF / fund"])

    def test_global_is_not_a_country_risk(self):
        """A commodity ETF resolves to 'Global' and carries no single
        country's currency or policy risk."""
        holdings = [self._holding("GLD", 50.0, Country="Global")]
        conc = prs.concentration(holdings, {"invested_pct": 50, "cash_pct": 50})
        violations = prs.limit_violations(holdings, conc, {"cash_pct": 50})
        self.assertFalse([v for v in violations if v["limit"] == "country"])

    def test_cash_below_minimum_is_flagged(self):
        holdings = [self._holding("A", 99.0)]
        conc = prs.concentration(holdings, {"invested_pct": 99, "cash_pct": 1.0})
        violations = prs.limit_violations(holdings, conc, {"cash_pct": 1.0})
        self.assertTrue([v for v in violations if v["limit"] == "cash minimum"])

    def test_effective_n_reflects_concentration_not_position_count(self):
        """Twenty positions where one is 90% is not a diversified book."""
        holdings = ([self._holding("BIG", 90.0)]
                    + [self._holding(f"T{i}", 0.5) for i in range(20)])
        conc = prs.concentration(holdings, {"invested_pct": 100, "cash_pct": 0})
        self.assertEqual(conc["positions"], 21)
        self.assertLess(conc["effective_n"], 2.0)


class TestRebalancing(unittest.TestCase):
    """Trades have to be fillable. A dollar amount that can't be transacted
    with the instruments actually held is worse than no suggestion."""

    def _setup(self, equity, delta, contracts):
        holding = {
            "Ticker": "GLD", "Weight_Pct": 47.5, "Exposure": equity + delta,
            "Equity_Value": equity, "Option_Delta_Value": delta,
            "Option_Contracts": contracts, "Price": 390.0, "Strategy": "longterm",
            "Sector": "Commodity", "Industry": "ETF / fund", "Country": "Global",
            "Cap_Bucket": "Commodity", "Themes": ["Commodity"], "Vol_90D": 29.0,
            "Beta_SPY": 0.3, "Market_Cap": None,
        }
        sizing = [{"Ticker": "GLD", "Weight_Pct": 47.5, "Action": "EXIT",
                   "Rationale": "over cap", "Reasons": ["over cap"], "Score": -7}]
        conc = prs.concentration([holding], {"invested_pct": 47.5, "cash_pct": 52.5})
        return prs.rebalancing(sizing, [holding], conc, [],
                               {"cash_pct": 52.5}, 19556.0)

    def test_option_led_position_is_not_a_share_sale(self):
        """GLD was 0.7 shares plus 3 calls, and the report said 'SELL $9,297'.
        You cannot sell stock you don't hold."""
        trades = self._setup(equity=260.0, delta=9031.0, contracts=3)
        gld = next(t for t in trades if t["ticker"] == "GLD")
        self.assertEqual(gld["action"], "REDUCE Δ")
        self.assertIsNone(gld["shares"])
        self.assertIn("option delta", gld["detail"][0])

    def test_share_led_position_still_gets_a_share_count(self):
        trades = self._setup(equity=9000.0, delta=200.0, contracts=1)
        gld = next(t for t in trades if t["ticker"] == "GLD")
        self.assertEqual(gld["action"], "SELL")
        self.assertIsNotNone(gld["shares"])

    def test_closing_options_does_not_fund_new_buys(self):
        """Closing a long call returns its premium, not its delta notional —
        treating that as proceeds would buy with money the trade never made."""
        trades = self._setup(equity=260.0, delta=9031.0, contracts=3)
        self.assertFalse([t for t in trades if t["action"] == "BUY"])


class TestOverlap(unittest.TestCase):

    def test_single_point_of_failure_is_named(self):
        holdings = [
            {"Ticker": "A", "Weight_Pct": 30.0, "Themes": ["AI"]},
            {"Ticker": "B", "Weight_Pct": 5.0, "Themes": ["AI"]},
        ]
        clusters = prs.overlap_analysis(holdings, ["A", "B"], [[1.0, 0.9], [0.9, 1.0]])
        self.assertEqual(clusters[0]["theme"], "AI")
        self.assertIn("A is", clusters[0]["single_point_of_failure"])

    def test_high_correlation_flags_duplicate_exposure(self):
        holdings = [
            {"Ticker": "A", "Weight_Pct": 10.0, "Themes": ["AI"]},
            {"Ticker": "B", "Weight_Pct": 10.0, "Themes": ["AI"]},
        ]
        clusters = prs.overlap_analysis(holdings, ["A", "B"], [[1.0, 0.9], [0.9, 1.0]])
        self.assertIsNotNone(clusters[0]["duplicate_exposure"])

    def test_lone_theme_member_is_not_a_cluster(self):
        holdings = [{"Ticker": "A", "Weight_Pct": 10.0, "Themes": ["AI"]}]
        self.assertEqual(prs.overlap_analysis(holdings, ["A"], [[1.0]]), [])


class TestScenarios(unittest.TestCase):

    def test_shock_scales_with_factor_beta(self):
        """A 2-beta name must lose twice what a 1-beta name does in the same
        market shock — otherwise the scenario table is just the weights."""
        holdings = [
            {"Ticker": "HI", "Exposure": 1000.0, "Themes": [],
             "Factor_Betas": {"SPY": 2.0}},
            {"Ticker": "LO", "Exposure": 1000.0, "Themes": [],
             "Factor_Betas": {"SPY": 1.0}},
        ]
        spy10 = next(s for s in prs.scenarios(holdings, 10000.0)
                     if s["scenario"] == "SPY −10%")
        self.assertAlmostEqual(spy10["pnl"], -300.0, places=0)

    def test_missing_beta_is_reported_not_assumed(self):
        """A holding with no factor beta must show up as uncovered rather than
        silently contributing zero, which would understate the loss."""
        holdings = [{"Ticker": "NEW", "Exposure": 5000.0, "Themes": [],
                     "Factor_Betas": {}}]
        spy5 = next(s for s in prs.scenarios(holdings, 10000.0)
                    if s["scenario"] == "SPY −5%")
        self.assertIn("NEW", spy5["uncovered_tickers"])

    def test_theme_shock_applies_only_to_tagged_holdings(self):
        holdings = [
            {"Ticker": "AI1", "Exposure": 1000.0, "Themes": ["AI"], "Factor_Betas": {}},
            {"Ticker": "OTH", "Exposure": 1000.0, "Themes": ["Commodity"], "Factor_Betas": {}},
        ]
        ai = next(s for s in prs.scenarios(holdings, 10000.0)
                  if s["scenario"] == "AI stocks −20%")
        self.assertAlmostEqual(ai["pnl"], -200.0, places=0)


class TestScoreDirections(unittest.TestCase):
    """Health and risk run in opposite directions; swapping them would invert
    the entire report's conclusion."""

    def test_scale_clamps_and_orients(self):
        self.assertEqual(prs._scale(10, 10, 50), 100.0)
        self.assertEqual(prs._scale(50, 10, 50), 0.0)
        self.assertEqual(prs._scale(0, 10, 50), 100.0)     # clamped
        self.assertEqual(prs._scale(99, 10, 50), 0.0)      # clamped
        self.assertEqual(prs._scale(None, 10, 50), 50.0)   # no data = neutral

    def test_scale_works_when_good_is_above_bad(self):
        self.assertEqual(prs._scale(20, 20, 2), 100.0)
        self.assertEqual(prs._scale(2, 20, 2), 0.0)

    def test_risk_score_is_labelled_as_inverted(self):
        self.assertEqual(prs.LIMITS["single_position"], 8.0)
        self.assertIn("riskier", prs.risk_score(
            [], {"beta_weighted_exposure": 1.0, "top_5": 30, "sectors": [("Tech", 20)]},
            {"avg_pairwise": 0.4}, {}, [],
            {"annual_vol_pct": 20, "worst_historical_drawdown_pct": -20,
             "expected_shortfall_99_pct": -5}, [])["direction"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
