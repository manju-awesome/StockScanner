"""
Tests for the accumulation layer — core.longterm.thesis and
technicals.score_support_level.

These two exist because the engine's own gates answer a different question
from "may I add to this today", and the invariants below are the ones that
distinguish the two. In the order they matter:

  1. UNMEASURED never permits accumulation. The package rule everywhere else
     ("a missing input is never a satisfied condition") has teeth here,
     because the caller acts on `may_accumulate` and a permissive default
     would buy into a company nobody checked.
  2. A deep correction is not a breakdown. Price below a RISING 200 MA is
     the case the deepest rung exists to buy; below a FALLING one it is the
     case that stops the ladder. Same price, opposite instruction.
  3. One deterioration is not a thesis break. A company investing hard shows
     one leg bending; a company failing shows two.
  4. Support strength is a property of the LEVEL, not of today's price — so
     it is measurable for levels price has not reached, which is the only
     reason an accumulation ladder can rank its rungs in advance.
  5. Two EMAs at one price are one signal, not a cluster.
  6. Prose states failures as failures. Condition names are positive
     assertions, and a summary built from them reads as its own opposite.

Run with: python -m unittest tests.test_longterm_accumulation
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.longterm import technicals as T
from stockanalysis.core.longterm import thesis as TH


def _row(**kw):
    """A healthy compounder above a rising 200 MA — thesis INTACT. Every
    test mutates one thing."""
    base = {
        "Ticker": "TEST", "Current Price": 100.0,
        # business
        "Revenue_CAGR%": 18.0, "FCF_CAGR%": 15.0, "EPS_Growth%": 22.0,
        "OperatingMargin_Trend_pp": 1.5, "FCF_Years": 4,
        "FCF_Positive_Years": 4,
        # competitive
        "GrossMargin_Trend_pp": 0.8, "Inst_Own_Chg": 1.2,
        # structure
        "200MA": 80.0, "MA200_Slope%": 1.5, "ATR_Pct": 2.0,
        # levels
        "8EMA": 99.0, "21EMA": 98.5, "50MA": 92.0,
        "Prior_Breakout_Level": 92.3, "S1": 91.8,
        "Volume_Confirmation": True, "Touches": 180,
    }
    base.update(kw)
    return base


class TestUnmeasuredIsNotIntact(unittest.TestCase):

    def test_a_row_with_no_fundamentals_cannot_be_accumulated(self):
        bare = {"Ticker": "X", "Current Price": 100.0}
        th = TH.compute_thesis(bare)
        self.assertEqual(th["state"], "UNMEASURED")
        self.assertFalse(th["may_accumulate"])

    def test_one_measured_condition_is_not_enough_for_a_leg(self):
        # Only revenue survives; MIN_MEASURED is 2.
        row = _row(**{"FCF_CAGR%": None, "EPS_Growth%": None,
                      "OperatingMargin_Trend_pp": None, "FCF_Years": None,
                      "FCF_Positive_Years": None})
        business = next(l for l in TH.compute_thesis(row)["legs"]
                        if l["label"] == "Business")
        self.assertEqual(business["measured"], 1)
        self.assertEqual(business["state"], "UNMEASURED")

    def test_unmeasured_does_not_mask_a_break_elsewhere(self):
        # Business unknown, structure clearly broken -> BROKEN, not UNKNOWN.
        row = _row(**{"Revenue_CAGR%": None, "FCF_CAGR%": None,
                      "EPS_Growth%": None, "OperatingMargin_Trend_pp": None,
                      "FCF_Years": None, "FCF_Positive_Years": None,
                      "Current Price": 60.0, "MA200_Slope%": -4.0})
        th = TH.compute_thesis(row)
        self.assertEqual(th["state"], "BROKEN")
        self.assertFalse(th["may_accumulate"])


class TestDeepCorrectionIsNotBreakdown(unittest.TestCase):
    """Invariant 2 — the distinction the whole ladder turns on."""

    def test_below_a_rising_200ma_is_still_accumulable(self):
        th = TH.compute_thesis(_row(**{"Current Price": 70.0,
                                       "MA200_Slope%": 2.0}))
        structure = next(l for l in th["legs"] if l["label"] == "Structure")
        self.assertEqual(structure["state"], "STRAINED")
        self.assertTrue(th["may_accumulate"])
        self.assertIn("deep correction", structure["note"])

    def test_below_a_falling_200ma_stops_the_ladder(self):
        th = TH.compute_thesis(_row(**{"Current Price": 70.0,
                                       "MA200_Slope%": -4.0}))
        structure = next(l for l in th["legs"] if l["label"] == "Structure")
        self.assertEqual(structure["state"], "BROKEN")
        self.assertFalse(th["may_accumulate"])

    def test_the_two_cases_differ_only_in_the_slope(self):
        deep = TH.compute_thesis(_row(**{"Current Price": 70.0,
                                         "MA200_Slope%": 2.0}))
        broke = TH.compute_thesis(_row(**{"Current Price": 70.0,
                                          "MA200_Slope%": -4.0}))
        self.assertNotEqual(deep["may_accumulate"], broke["may_accumulate"])

    def test_a_flat_200ma_is_not_a_falling_one(self):
        # Deck measured -0.08% over 20 sessions — noise on an average built
        # to be slow. Without MA200_FALLING_PCT that reading alone tipped a
        # healthy business into BROKEN.
        th = TH.compute_thesis(_row(**{"Current Price": 70.0,
                                       "MA200_Slope%": -0.08}))
        self.assertTrue(th["may_accumulate"])

    def test_the_invalidation_level_sits_below_the_200ma(self):
        row = _row()
        price, buffer_pct = TH.structure_break_price(row)
        self.assertLess(price, row["200MA"])
        self.assertGreaterEqual(buffer_pct, TH.MIN_STRUCTURE_BUFFER_PCT)

    def test_the_invalidation_level_scales_with_volatility(self):
        calm, _ = TH.structure_break_price(_row(ATR_Pct=1.0))
        wild, _ = TH.structure_break_price(_row(ATR_Pct=6.0))
        self.assertGreater(calm, wild)


class TestOneDeteriorationIsNotABreak(unittest.TestCase):
    """Invariant 3 — Microsoft grew free cash flow +4% while revenue grew
    +16%, because capex went from $28B to $116B."""

    def test_a_single_broken_condition_is_strained_not_broken(self):
        th = TH.compute_thesis(_row(**{"FCF_CAGR%": -5.0}))
        self.assertEqual(th["state"], "STRAINED")
        self.assertTrue(th["may_accumulate"])

    def test_two_broken_conditions_in_one_leg_break_it(self):
        th = TH.compute_thesis(_row(**{"FCF_CAGR%": -5.0,
                                       "Revenue_CAGR%": -3.0}))
        self.assertEqual(th["state"], "BROKEN")
        self.assertFalse(th["may_accumulate"])

    def test_the_worst_leg_wins_rather_than_the_average(self):
        # Business and competitive perfect, structure gone. Averaging would
        # report this healthy.
        th = TH.compute_thesis(_row(**{"Current Price": 60.0,
                                       "MA200_Slope%": -5.0}))
        self.assertEqual(th["state"], "BROKEN")

    def test_flat_growth_is_not_a_deterioration(self):
        # The question is "has this stopped working", not "is this excellent".
        th = TH.compute_thesis(_row(**{"Revenue_CAGR%": 0.5,
                                       "FCF_CAGR%": 0.5}))
        self.assertEqual(th["state"], "INTACT")

    def test_a_maturing_compounder_is_not_a_broken_one(self):
        # Decelerating from 40% to 12% is still compounding. A ladder that
        # stops on deceleration stops on every business that grows up.
        th = TH.compute_thesis(_row(**{"Revenue_CAGR%": 12.0,
                                       "FCF_CAGR%": 9.0,
                                       "EPS_Growth%": 6.0}))
        self.assertEqual(th["state"], "INTACT")


class TestProseStatesFailuresAsFailures(unittest.TestCase):
    """Invariant 6 — the first draft printed "Thesis broken — Structure:
    Above the long-term floor, 200 MA rising"."""

    def test_the_headline_does_not_assert_the_conditions_that_failed(self):
        th = TH.compute_thesis(_row(**{"Current Price": 60.0,
                                       "MA200_Slope%": -5.0}))
        self.assertNotIn("Above the long-term floor", th["headline"])
        self.assertIn("falling", th["headline"])

    def test_a_broken_business_names_the_numbers(self):
        th = TH.compute_thesis(_row(**{"Revenue_CAGR%": -20.3,
                                       "FCF_CAGR%": -8.0}))
        self.assertIn("shrinking", th["headline"])
        self.assertIn("-20.3", th["headline"])

    def test_the_headline_gives_one_instruction_not_two(self):
        th = TH.compute_thesis(_row(**{"Current Price": 60.0,
                                       "MA200_Slope%": -5.0}))
        self.assertEqual(th["headline"].count("Stop adding"), 1)


class TestLevelStrengthIsAboutTheLevel(unittest.TestCase):
    """Invariant 4 — compute_support_confluence scores today's price and
    returns zero for a level 13% below the tape. This has to score it."""

    def test_a_level_far_below_price_still_scores(self):
        row = _row(**{"Current Price": 200.0})       # every level far below
        scored = T.score_support_level(row, row["200MA"])
        self.assertIsNotNone(scored["score"])
        self.assertGreater(scored["score"], 0)

    def test_the_confluence_score_would_have_returned_nothing_there(self):
        # The contrast that justifies a second function existing.
        row = _row(**{"Current Price": 200.0})
        self.assertEqual(T.compute_support_confluence(row)["agreeing"], 0)
        self.assertGreater(T.score_support_level(row, row["200MA"])["score"], 0)

    def test_the_200ma_outranks_the_8ema_all_else_equal(self):
        row = _row(**{"8EMA": 100.0, "21EMA": 130.0, "50MA": 140.0,
                      "200MA": 150.0, "Prior_Breakout_Level": None,
                      "S1": None, "Volume_Confirmation": None})
        self.assertGreater(T.score_support_level(row, 150.0)["score"],
                           T.score_support_level(row, 100.0)["score"])

    def test_a_price_with_nothing_at_it_scores_zero_not_none(self):
        # "Nothing tracked is here" is a measurement, not an absence.
        scored = T.score_support_level(_row(), 12.34)
        self.assertEqual(scored["score"], 0)
        self.assertIsNone(scored["identity"])

    def test_a_missing_level_price_scores_none(self):
        self.assertIsNone(T.score_support_level(_row(), None)["score"])

    def test_identity_is_the_strongest_level_present_not_the_nearest(self):
        # 8 EMA sits marginally nearer, but the level IS the 50 MA.
        row = _row(**{"50MA": 100.0, "8EMA": 100.4, "21EMA": None,
                      "200MA": 60.0})
        self.assertEqual(T.score_support_level(row, 100.0)["identity"],
                         "50 MA")


class TestTwoEmasAreOneSignal(unittest.TestCase):
    """Invariant 5 — scored without families the EMA rung measured out
    STRONGER than the 200 MA rung across the live library (median 70 vs 40),
    purely because its two members are always within tolerance."""

    def test_the_8_and_21_ema_do_not_corroborate_each_other(self):
        row = _row(**{"8EMA": 100.0, "21EMA": 100.2, "50MA": 60.0,
                      "200MA": 50.0, "Prior_Breakout_Level": None,
                      "S1": None, "Volume_Confirmation": None})
        scored = T.score_support_level(row, 100.0)
        self.assertEqual(scored["agreeing"], [])

    def test_independent_families_do_corroborate(self):
        row = _row(**{"8EMA": 100.0, "21EMA": 100.2, "50MA": 100.1,
                      "200MA": 50.0, "Prior_Breakout_Level": 99.9,
                      "S1": None, "Volume_Confirmation": None})
        scored = T.score_support_level(row, 100.0)
        # The 50 MA is the identity, so it is not also its own corroboration.
        self.assertEqual(scored["identity"], "50 MA")
        self.assertIn("prior breakout", scored["agreeing"])
        # The EMA pair contributes exactly one voice, named for its stronger
        # member — not two, and not zero.
        self.assertEqual([n for n in scored["agreeing"] if "EMA" in n],
                         ["21 EMA"])

    def test_a_lone_ema_is_weak_and_a_cluster_is_not(self):
        lone = _row(**{"8EMA": 100.0, "21EMA": None, "50MA": 60.0,
                       "200MA": 50.0, "Prior_Breakout_Level": None,
                       "S1": None, "Volume_Confirmation": None})
        cluster = _row(**{"8EMA": 100.0, "21EMA": 100.1, "50MA": 100.2,
                          "200MA": 99.8, "Prior_Breakout_Level": 100.3,
                          "S1": 100.0, "Volume_Confirmation": True})
        self.assertEqual(T.score_support_level(lone, 100.0)["label"],
                         "🔴 Weak")
        self.assertEqual(T.score_support_level(cluster, 100.0)["label"],
                         "🟢 Major")


class TestTouchCountIsNotScored(unittest.TestCase):
    """`Touches` counts 5-minute bars over ~20 days: 4 to 1,015 across the
    live library, median 181. A count whose scale is an artifact of the bar
    size cannot carry weight in a ten-year support score."""

    def test_touch_count_does_not_move_the_score(self):
        few = T.score_support_level(_row(Touches=4), 92.0)["score"]
        many = T.score_support_level(_row(Touches=1015), 92.0)["score"]
        self.assertEqual(few, many)

    def test_but_it_is_still_reported_as_context(self):
        self.assertEqual(T.score_support_level(_row(Touches=42), 92.0)
                         ["touches"], 42)

    def test_volume_confirmation_does_move_the_score(self):
        # The ratio-based reading is scale-free, so it is the one that counts.
        on = T.score_support_level(_row(Volume_Confirmation=True), 91.8)
        off = T.score_support_level(_row(Volume_Confirmation=False), 91.8)
        self.assertGreater(on["score"], off["score"])


if __name__ == "__main__":
    unittest.main()
