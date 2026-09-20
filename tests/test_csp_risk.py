"""
Tests for core/csp/risk.py and requirements.py — risk as a measurement.

The engine used to answer DTE and earnings with booleans. Both threw
away the information that decided the trade: a 36-day and a 59-day
expiry both "passed" a 21-60 window, and NTAP was rejected outright for
earnings 21 days out rather than told what would clear it.

Invariants defended here:

  1. Risk is a THIRD factor, multiplied — a superb company on a
     well-paid contract can still be a bad trade, and the score must be
     able to say so.
  2. Earnings inside the contract is a state (EVENT RISK), not a
     rejection and not a premium problem.
  3. ACCEPT does not mean "allow freely" — it demands the trade be
     strong enough to be paid for the gap risk, and falls back to the
     CONTROLLED penalty rather than to a free pass.
  4. An expiry that clears earnings is a SHORTER one, never a longer
     one. Every expiry past the print necessarily contains it.
  5. Every non-SELL state carries a number that would change it.

Run with: python -m unittest tests.test_csp_risk
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.csp import requirements as RQ   # noqa: E402
from stockanalysis.core.csp import risk as RK           # noqa: E402


class DTEPreference(unittest.TestCase):

    def test_preferred_band_scores_full_marks(self):
        for d in (30, 35, 45):
            self.assertEqual(RK.dte_fit(d)["score"], 100, d)

    def test_score_decays_with_distance_not_off_a_cliff(self):
        """46 days is not meaningfully worse than 45, and a hard edge
        would make the ranking jump for no reason."""
        edge = RK.dte_fit(45)["score"]
        just_past = RK.dte_fit(47)["score"]
        further = RK.dte_fit(55)["score"]
        self.assertEqual(edge, 100)
        self.assertLess(just_past, edge)
        self.assertLess(further, just_past)
        self.assertGreater(just_past, further)

    def test_beyond_hard_max_scores_zero(self):
        self.assertEqual(RK.dte_fit(75, hard_max=60)["score"], 0)

    def test_missing_dte_is_none_not_zero(self):
        self.assertIsNone(RK.dte_fit(None)["score"])


class EarningsDistance(unittest.TestCase):

    def test_six_bands(self):
        cases = [
            (60, 30, "CLEAN"), (34, 30, "LOW"), (21, 36, "EXPOSED"),
            (10, 36, "HIGH"), (3, 36, "IMMINENT"),
        ]
        for days, dte, key in cases:
            with self.subTest(days=days, dte=dte):
                self.assertEqual(RK.earnings_distance(days, dte)["key"], key)

    def test_ratio_is_scale_free(self):
        """Earnings 60% of the way to expiry is the same exposure on a
        20-day contract as on a 50-day one."""
        a = RK.earnings_distance(12, 20)["ratio"]
        b = RK.earnings_distance(30, 50)["ratio"]
        self.assertAlmostEqual(a, b, places=2)

    def test_unknown_earnings_is_not_treated_as_clear(self):
        out = RK.earnings_distance(None, 30)
        self.assertEqual(out["key"], "UNKNOWN")
        self.assertIsNone(out["inside"])
        self.assertIn("not as clear", out["detail"])

    def test_inside_flag_matches_the_arithmetic(self):
        self.assertTrue(RK.earnings_distance(21, 36)["inside"])
        self.assertFalse(RK.earnings_distance(40, 36)["inside"])


class EarningsPolicy(unittest.TestCase):

    INSIDE = None

    def setUp(self):
        self.INSIDE = RK.earnings_distance(21, 36)

    def test_avoid_blocks(self):
        g = RK.earnings_gate(self.INSIDE, "AVOID")
        self.assertFalse(g["allow"])

    def test_controlled_allows_with_a_penalty(self):
        g = RK.earnings_gate(self.INSIDE, "CONTROLLED")
        self.assertTrue(g["allow"])
        self.assertEqual(g["penalty_option"], RK.CONTROLLED_OPTION_PENALTY)

    def test_accept_requires_the_trade_to_pay_for_the_risk(self):
        """Invariant 3: ACCEPT is not a free pass."""
        weak = RK.earnings_gate(self.INSIDE, "ACCEPT", quality=80,
                                delta=-0.28, liquidity=50, adequacy=1.0)
        self.assertTrue(weak["allow"])          # visible…
        self.assertGreater(weak["penalty_option"], 0)   # …but penalised
        self.assertIn("ACCEPT not met", weak["why"])

    def test_accept_passes_a_genuinely_strong_trade(self):
        strong = RK.earnings_gate(self.INSIDE, "ACCEPT", quality=93,
                                  delta=-0.15, liquidity=90, adequacy=1.6)
        self.assertTrue(strong["allow"])
        self.assertEqual(strong["penalty_option"], 0)

    def test_policy_is_irrelevant_when_earnings_are_clear(self):
        clear = RK.earnings_distance(60, 30)
        for pol in RK.EARNINGS_POLICIES:
            g = RK.earnings_gate(clear, pol)
            self.assertTrue(g["allow"], pol)
            self.assertEqual(g["penalty_option"], 0, pol)


class MoveCushion(unittest.TestCase):

    def test_ratio_is_distance_over_expected_move(self):
        out = RK.move_cushion(118.0, 105.0, 118.0 * 0.042)
        self.assertAlmostEqual(out["ratio"], 2.62, places=1)
        self.assertAlmostEqual(out["distance_pct"], 11.0, places=0)

    def test_inside_the_expected_move_is_flagged(self):
        out = RK.move_cushion(100.0, 97.0, 6.0)
        self.assertLess(out["ratio"], 1.0)
        self.assertIn("inside", out["band"].lower())

    def test_no_expected_move_reports_distance_without_a_ratio(self):
        out = RK.move_cushion(100.0, 90.0, None)
        self.assertIsNone(out["ratio"])
        self.assertEqual(out["distance_pct"], 10.0)


class RiskScore(unittest.TestCase):

    def _risk(self, earn_key="CLEAN", cushion_ratio=2.0, assign=80,
              dte=35, liq=80):
        dist = {"CLEAN": RK.earnings_distance(60, 30),
                "EXPOSED": RK.earnings_distance(21, 36),
                "IMMINENT": RK.earnings_distance(3, 36)}[earn_key]
        cush = RK.move_cushion(100.0, 100.0 - cushion_ratio * 5.0, 5.0)
        return RK.risk_score(RK.dte_fit(dte), dist, cush,
                             {"score": assign}, liq)

    def test_clean_beats_exposed_beats_imminent(self):
        a = self._risk("CLEAN")["score"]
        b = self._risk("EXPOSED")["score"]
        c = self._risk("IMMINENT")["score"]
        self.assertGreater(a, b)
        self.assertGreater(b, c)

    def test_weakest_leg_is_named(self):
        out = self._risk("IMMINENT")
        self.assertEqual(out["weakest"], "earnings")

    def test_missing_inputs_reduce_coverage_not_the_score(self):
        out = RK.risk_score(RK.dte_fit(None),
                            RK.earnings_distance(60, 30),
                            RK.move_cushion(100.0, 90.0, 5.0),
                            {"score": 80}, None)
        self.assertIsNotNone(out["score"])
        self.assertLess(out["coverage"], 100)


class Combine(unittest.TestCase):

    def test_risk_is_a_third_factor(self):
        """Invariant 1: the same stock and option with worse risk must
        score lower."""
        good = RK.combine(98, 78, 88)
        bad = RK.combine(98, 78, 30)
        self.assertGreater(good, bad)

    def test_risk_is_damped_not_squared(self):
        """Risk modifies a trade that already passed both other gates,
        so a 70 risk must not scale the result to 0.70."""
        full = RK.combine(90, 90, 100)
        seventy = RK.combine(90, 90, 70)
        self.assertGreater(seventy, full * 0.8)

    def test_missing_risk_leaves_the_product_unchanged(self):
        self.assertEqual(RK.combine(90, 80, None), round(90 / 100 * 80))

    def test_neither_stock_nor_option_can_be_rescued(self):
        self.assertIsNone(RK.combine(None, 80, 90))
        self.assertLess(RK.combine(100, 20, 100), 25)


class Requirements(unittest.TestCase):

    def test_premium_requirement_names_a_dollar_figure(self):
        reqs = RQ.premium_requirements(
            {"yield_pct": 0.9, "annualised": 5.7},
            {"period_pct": 1.72},
            {}, {}, {"ratio": 0.86},
            {"strike": 100.0, "limit_price": 0.95})
        prem = next(r for r in reqs if r["field"] == "premium")
        self.assertEqual(prem["needs"], "$1.72")
        self.assertFalse(prem["met"])

    def test_alternatives_are_marked_met_when_satisfied(self):
        reqs = RQ.premium_requirements(
            {"yield_pct": 2.0, "annualised": 19.1},
            {"period_pct": 1.05},
            {}, {}, {"ratio": 1.4},
            {"strike": 100.0, "limit_price": 2.00})
        self.assertTrue(next(r for r in reqs
                             if r["field"] == "premium")["met"])
        self.assertTrue(next(r for r in reqs
                             if r["field"] == "annualised")["met"])

    def test_earnings_requirement_names_a_shorter_expiry_not_a_longer(self):
        """INVARIANT 4. Every expiry past the print contains it, so
        suggesting a later one sends you to sell the exact contract you
        were avoiding."""
        dist = RK.earnings_distance(21, 36)
        reqs = RQ.earnings_requirements(dist, ["2026-08-21 (8d)"])
        self.assertIn("8d", reqs[0]["needs"])
        self.assertNotIn("after", reqs[0]["needs"])

    def test_no_clean_expiry_says_wait_for_the_date(self):
        dist = RK.earnings_distance(5, 36)
        reqs = RQ.earnings_requirements(dist, [], wait_days=5)
        self.assertEqual(reqs[0]["field"], "wait")
        self.assertIn("5 days", reqs[0]["needs"])

    def test_summary_reads_as_alternatives(self):
        out = RQ.build("WAIT_IV", {
            "ret": {"yield_pct": 0.9, "annualised": 5.7},
            "req": {"period_pct": 1.72},
            "ratio": {"ratio": 0.86},
            "chosen": {"strike": 100.0, "limit_price": 0.95}})
        self.assertIn("OR", out["summary"])
        self.assertIn("needs", out["summary"])

    def test_blocking_requirement_wins_the_summary(self):
        out = RQ.build("EVENT_RISK", {
            "dist": RK.earnings_distance(21, 36),
            "clean_expiries": ["2026-08-21 (8d)"]})
        self.assertTrue(out["blocking"])
        self.assertIn("expiry", out["summary"])

    def test_no_requirements_for_a_clean_sell(self):
        self.assertEqual(RQ.build("SELL", {})["requirements"], [])


if __name__ == "__main__":
    unittest.main()
