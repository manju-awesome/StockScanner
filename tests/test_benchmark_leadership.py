"""
Tests for core.market_regime.benchmark_leadership — which part of the market
is carrying the tape, read from the benchmark ETF profiles.

Distinct from compute_regime(), which scores VIX/SPY/QQQ/breadth into one
number. The same score can describe a broad rally or a narrow tech-led one,
and those call for different sizing.
Run with: python -m unittest tests.test_benchmark_leadership
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.market_regime import benchmark_leadership


def _p(**dists):
    """Profiles carrying only what the read uses: distance from the 200-day."""
    return {t: {"dist_ma200": d, "change_pct": 0.5, "ytd_return": 8.0}
            for t, d in dists.items()}


class TestLeadership(unittest.TestCase):
    def test_all_three_up_is_broad_risk_on(self):
        r = benchmark_leadership(_p(SPY=10, QQQ=12, DIA=9, IWM=13))
        self.assertEqual(r["verdict"], "Broad risk-on")
        self.assertEqual(r["tone"], "good")
        self.assertIn("Small caps confirm", r["detail"])

    def test_lagging_small_caps_qualify_a_risk_on_read(self):
        r = benchmark_leadership(_p(SPY=10, QQQ=12, DIA=9, IWM=-4))
        self.assertEqual(r["verdict"], "Broad risk-on")
        self.assertIn("not yet full", r["detail"])

    def test_tech_led_when_the_dow_lags(self):
        r = benchmark_leadership(_p(SPY=3, QQQ=12, DIA=-2, IWM=-5))
        self.assertEqual(r["verdict"], "Tech-led, narrow")
        self.assertEqual(r["tone"], "watch")

    def test_broad_risk_off(self):
        r = benchmark_leadership(_p(SPY=-6, QQQ=-8, DIA=-5, IWM=-12))
        self.assertEqual(r["verdict"], "Broad risk-off")
        self.assertEqual(r["tone"], "bad")

    def test_defensive_rotation_when_growth_breaks_but_blue_chips_hold(self):
        r = benchmark_leadership(_p(SPY=1, QQQ=-3, DIA=4, IWM=-2))
        self.assertEqual(r["verdict"], "Defensive rotation")

    def test_trend_not_todays_move_decides(self):
        # A red day on the Dow must not flip the verdict while it is still
        # well above its 200-day.
        profiles = _p(SPY=10, QQQ=12, DIA=9, IWM=13)
        profiles["DIA"]["change_pct"] = -1.8
        self.assertEqual(benchmark_leadership(profiles)["verdict"],
                         "Broad risk-on")

    def test_missing_benchmarks_are_named_not_assumed_neutral(self):
        r = benchmark_leadership(_p(SPY=10, QQQ=12))
        self.assertTrue(r["ok"])
        self.assertIn("DIA", r["missing"])
        self.assertIn("IWM", r["missing"])
        self.assertEqual(len(r["legs"]), 2)

    def test_too_little_data_refuses_a_verdict(self):
        r = benchmark_leadership(_p(SPY=10))
        self.assertFalse(r["ok"])
        self.assertIn("Refresh ETFs", r["detail"])

    def test_empty_profiles(self):
        r = benchmark_leadership({})
        self.assertFalse(r["ok"])

    def test_a_profile_with_no_200ma_counts_as_missing(self):
        profiles = _p(SPY=10, QQQ=12, DIA=9)
        profiles["IWM"] = {"change_pct": 1.0}          # no dist_ma200
        r = benchmark_leadership(profiles)
        self.assertIn("IWM", r["missing"])

    def test_legs_report_direction_and_distance(self):
        legs = {l["ticker"]: l for l in
                benchmark_leadership(_p(SPY=10, QQQ=-4, DIA=9, IWM=1))["legs"]}
        self.assertTrue(legs["SPY"]["above_200ma"])
        self.assertFalse(legs["QQQ"]["above_200ma"])
        self.assertEqual(legs["QQQ"]["dist_ma200"], -4)


if __name__ == "__main__":
    unittest.main()
