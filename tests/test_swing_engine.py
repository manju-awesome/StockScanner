"""
Tests for core.swing.engine — the 3-to-20-day verdict.

The whole reason this engine exists is that it must be free to disagree with
the long-term one. So the first thing pinned here is the independence: a
mediocre business with a clean chart has to be able to score well, because
requiring LQuality 85 for a swing trade would delete exactly the trades this
engine was built to find.

After that, most of these are guards against the specific ways a technical
scanner flatters itself:
  - rewarding RSI for being high, when overbought is as often the end of a
    move as the middle
  - reading 1.6x volume as conviction without asking which way the day went
  - quoting R to a distant 52-week high with three resistances in between
  - calling a level "support" and treating that as a reason to buy

Run with: python -m unittest tests.test_swing_engine
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.swing import engine as SW


def _row(**kw):
    """A clean 21 EMA pullback inside an intact uptrend."""
    base = {
        "Ticker": "TEST", "Current Price": 100.0,
        "8EMA": 101.0, "21EMA": 99.8, "50MA": 95.0, "200MA": 85.0,
        "MA50_Slope%": 2.0, "MA200_Slope%": 1.5,
        "Prior_Breakout_Level": 94.0, "S1": 96.0, "R1": 104.0,
        "52W High": 108.0, "52W Low": 70.0, "Dist_52W_High%": -7.4,
        "Days_Since_52W_High": 12, "Pct_vs_8EMA": -1.0,
        "RSI_14": 58.0, "ADX_14": 28.0, "RS_Rank": 75.0,
        "Vol_vs_20D": 0.7, "Pullback_Vol_Ratio": 0.7, "VolumeDryingUp": True,
        "Distribution_Days_25d": 1, "ATR_Pct": 2.0, "Touches": 60,
        "Reversal_Candle": "none", "Volume_Confirmation": False,
        "Prev-Day Close": 99.5, "ATR Shrinking": False,
    }
    base.update(kw)
    return base


class TestIndependence(unittest.TestCase):
    """The point of a second engine."""

    def test_it_reads_no_company_input(self):
        # Nothing in a scan row that describes the BUSINESS may move the
        # swing score. If this fails, the two engines have been coupled.
        plain = SW.evaluate(_row(), regime="FAVORABLE")
        with_company = SW.evaluate(
            _row(**{"ReturnOnEquity%": 45.0, "FCF_Margin%": 30.0,
                    "Investment_Score": 95, "Revenue_CAGR%": 25.0,
                    "FreeCashFlow": 5e10}), regime="FAVORABLE")
        self.assertEqual(plain["score"], with_company["score"])

    def test_a_weak_business_can_still_be_an_excellent_swing(self):
        # A 55-quality name with a clean chart is exactly the trade the
        # long-term engine rejects and this one is for.
        result = SW.evaluate(_row(), regime="FAVORABLE")
        self.assertGreaterEqual(result["score"], 75)
        self.assertIn(result["grade"], ("A", "A+"))


class TestComponents(unittest.TestCase):

    def test_the_seven_components_carry_the_specified_weights(self):
        comps = {c["name"]: c["weight"]
                 for c in SW.evaluate(_row(), regime="FAVORABLE")["components"]}
        self.assertEqual(comps, {"Market regime": 15, "Trend": 20, "Setup": 20,
                                 "Momentum": 15, "Volume": 10,
                                 "Trigger quality": 10, "Trade quality": 10})
        self.assertEqual(sum(comps.values()), 100)

    def test_missing_data_lowers_coverage_rather_than_scoring_zero(self):
        result = SW.evaluate(_row(RSI_14=None, ADX_14=None, RS_Rank=None,
                                  **{"Pct_vs_8EMA": None}), regime="FAVORABLE")
        self.assertLess(result["coverage"], 1.0)
        self.assertIn("Momentum", result["missing"])
        self.assertIsNotNone(result["score"])

    def test_a_selling_tape_lowers_a_good_chart(self):
        strong = SW.evaluate(_row(), regime="FAVORABLE")["score"]
        weak = SW.evaluate(_row(), regime="DEFENSIVE")["score"]
        self.assertLess(weak, strong)


class TestMomentumIsBanded(unittest.TestCase):
    """The commonest scanner error is scoring RSI 78 above RSI 62."""

    def _mom(self, rsi):
        return SW.momentum_component(_row(RSI_14=rsi))[0]

    def test_overbought_does_not_outscore_healthy(self):
        self.assertGreater(self._mom(62), self._mom(78))

    def test_weak_rsi_scores_worst(self):
        self.assertLess(self._mom(38), self._mom(62))

    def test_extended_above_the_8_ema_is_not_rewarded(self):
        near = SW.momentum_component(_row(**{"Pct_vs_8EMA": 1.0}))[0]
        far = SW.momentum_component(_row(**{"Pct_vs_8EMA": 12.0}))[0]
        self.assertGreater(near, far)


class TestVolumeIsClassifiedByDirection(unittest.TestCase):
    """AVGO showed 1.63x average volume AND five distribution days. A ratio
    alone cannot tell those apart; the direction of the day can."""

    def test_expansion_on_a_down_day_is_supply(self):
        score, detail, kind = SW.volume_component(
            _row(**{"Vol_vs_20D": 1.8, "Current Price": 98.0,
                    "Prev-Day Close": 101.0}))
        self.assertEqual(kind, "selling")
        self.assertIn("DOWN day", detail)
        self.assertLess(score, 40)

    def test_the_same_ratio_on_an_up_day_is_conviction(self):
        score, _detail, kind = SW.volume_component(
            _row(**{"Vol_vs_20D": 1.8, "Current Price": 102.0,
                    "Prev-Day Close": 99.0}))
        self.assertEqual(kind, "breakout")
        self.assertGreater(score, 80)

    def test_contraction_into_a_pullback_is_healthy(self):
        _score, _detail, kind = SW.volume_component(
            _row(**{"Vol_vs_20D": 0.6, "VolumeDryingUp": True}))
        self.assertEqual(kind, "quiet_pullback")

    def test_distribution_reduces_the_score_but_does_not_delete_the_setup(self):
        clean = SW.evaluate(_row(**{"Distribution_Days_25d": 0}),
                            regime="FAVORABLE")
        heavy = SW.evaluate(_row(**{"Distribution_Days_25d": 6}),
                            regime="FAVORABLE")
        self.assertLess(heavy["score"], clean["score"])
        # Still a setup — volume confirms, it does not veto.
        self.assertEqual(heavy["setup"], clean["setup"])
        self.assertNotEqual(heavy["state"], "NO SETUP")


class TestSetupClassification(unittest.TestCase):

    def test_it_names_the_pattern_rather_than_nearness_to_support(self):
        self.assertEqual(SW.classify_setup(_row())[0], "pullback_21ema")

    def test_a_pullback_under_the_200ma_is_not_a_pullback(self):
        # It is a downtrend, and calling it a pullback is how a scanner
        # walks its user into falling knives.
        key, _why = SW.classify_setup(
            _row(**{"Current Price": 80.0, "200MA": 85.0, "21EMA": 80.2,
                    "RSI_14": 50.0}))
        self.assertNotEqual(key, "pullback_21ema")

    def test_a_lost_breakout_is_its_own_pattern(self):
        key, _why = SW.classify_setup(
            _row(**{"Current Price": 93.0, "Prior_Breakout_Level": 94.0,
                    "Days_Since_52W_High": 10}))
        self.assertEqual(key, "failed_breakout")

    def test_a_retest_needs_more_than_nearness_to_the_breakout_level(self):
        # Prior_Breakout_Level sits within 3% of price for 49% of the live
        # library, so proximity alone made the highest-scoring pattern the
        # commonest one.
        near_but_stale = _row(**{"Current Price": 94.5,
                                 "Prior_Breakout_Level": 94.0,
                                 "Days_Since_52W_High": 200,
                                 "Dist_52W_High%": -30.0})
        self.assertNotEqual(SW.classify_setup(near_but_stale)[0],
                            "breakout_retest")
        genuine = _row(**{"Current Price": 94.5, "Prior_Breakout_Level": 94.0,
                          "Days_Since_52W_High": 8, "Dist_52W_High%": -5.0})
        self.assertEqual(SW.classify_setup(genuine)[0], "breakout_retest")

    def test_no_setup_is_a_real_answer(self):
        key, _why = SW.classify_setup(
            _row(**{"Current Price": 100.0, "8EMA": 100.5, "21EMA": 92.0,
                    "50MA": 88.0, "200MA": 85.0, "Prior_Breakout_Level": 70.0,
                    "RSI_14": 55.0, "ATR Shrinking": False,
                    "Dist_52W_High%": -20.0}))
        self.assertEqual(key, "none")


class TestThePath(unittest.TestCase):
    """Don't price a trade to a distant 52-week high with three levels in
    between."""

    def test_the_first_resistance_grades_the_path(self):
        path = SW.resistance_path(
            _row(**{"R1": 100.5, "8EMA": 101.0, "21EMA": 101.5,
                    "52W High": 140.0}), entry=100.0, stop=90.0)
        self.assertEqual(path["quality"], "Poor")
        self.assertLess(path["first_r"], 0.5)

    def test_room_to_the_first_level_is_a_good_path(self):
        path = SW.resistance_path(
            _row(**{"R1": 120.0, "8EMA": 99.0, "21EMA": 98.0,
                    "52W High": 140.0}), entry=100.0, stop=90.0)
        self.assertEqual(path["quality"], "Good")

    def test_levels_at_the_same_price_are_one_obstacle(self):
        path = SW.resistance_path(
            _row(**{"R1": 110.0, "8EMA": 110.2, "21EMA": 130.0,
                    "52W High": 140.0}), entry=100.0, stop=90.0)
        names = [lvl["name"] for lvl in path["levels"]]
        self.assertTrue(any("/" in n for n in names),
                        "two names for one price counted twice")

    def test_the_path_is_measured_from_the_entry_not_todays_price(self):
        # A pullback triggers on reclaiming the 8 EMA, so the 8 EMA is part
        # of the entry rather than resistance above it.
        entry, note = SW.planned_entry(_row(), "pullback_21ema")
        self.assertEqual(entry, 101.0)
        self.assertIn("8 EMA", note)

    def test_levels_between_price_and_trigger_are_reported_separately(self):
        # Moving the entry up to the trigger hid the levels the stock must
        # climb through to get there — the reason a setup may never fire.
        row = _row(**{"Current Price": 100.0, "8EMA": 106.0, "R1": 102.0,
                      "21EMA": 104.0})
        path = SW.resistance_path(row, entry=106.0, stop=95.0, price=100.0)
        names = [c["name"] for c in path["to_clear"]]
        self.assertIn("R1 resistance", names)
        self.assertIn("21 EMA", names)


class TestStateAndPlan(unittest.TestCase):

    def test_an_extended_stock_is_not_a_ready_entry(self):
        result = SW.evaluate(_row(**{"Pct_vs_8EMA": 9.0}), regime="FAVORABLE")
        self.assertEqual(result["state"], "EXTENDED")
        self.assertIn("pullback", (result["trigger"] or "").lower())

    def test_a_fired_trigger_with_every_gate_passing_is_ready(self):
        # TRIGGERED was folded into READY: a trigger that has fired while a
        # gate still fails is not an entry, and two words for the same
        # moment invited reading the event as permission.
        result = SW.evaluate(
            _row(**{"Current Price": 101.0, "Prior_Breakout_Level": 100.0,
                    "Days_Since_52W_High": 8, "Dist_52W_High%": -5.0,
                    "R1": 115.0, "52W High": 130.0, "8EMA": 100.5,
                    "21EMA": 99.0, "Vol_vs_20D": 1.6,
                    "Reversal_Candle": "bullish engulfing",
                    "Volume_Confirmation": True}), regime="FAVORABLE")
        self.assertEqual(result["setup"], "breakout_retest")
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["action"], "enter")
        self.assertTrue(result["eligible"])

    def test_a_fired_trigger_on_a_blocked_path_is_not_ready(self):
        result = SW.evaluate(
            _row(**{"Current Price": 101.0, "Prior_Breakout_Level": 100.0,
                    "Days_Since_52W_High": 8, "Dist_52W_High%": -5.0,
                    "R1": 101.6, "52W High": 102.0, "8EMA": 100.5,
                    "21EMA": 99.0, "Vol_vs_20D": 1.6,
                    "Reversal_Candle": "bullish engulfing",
                    "Volume_Confirmation": True}), regime="FAVORABLE")
        self.assertEqual(result["setup"], "breakout_retest")
        self.assertEqual(result["state"], "APPROACHING")
        self.assertIn("path", result["state_why"].lower())

    def test_every_live_setup_carries_a_trigger(self):
        # APPROACHING with no trigger named is just a shrug.
        result = SW.evaluate(_row(), regime="FAVORABLE")
        self.assertIn(result["state"], ("READY", "NEAR READY", "APPROACHING"))
        self.assertTrue(result["trigger"])

    def test_a_time_stop_is_always_set(self):
        # A swing that stagnates must not quietly become an investment.
        ts = SW.evaluate(_row(), regime="FAVORABLE")["time_stop"]
        self.assertLess(ts["min_days"], ts["max_days"])
        self.assertLessEqual(ts["review_days"], ts["max_days"])

    def test_risk_scales_down_with_the_grade(self):
        self.assertGreater(SW.GRADE_RISK["A+"][1], SW.GRADE_RISK["B"][1])
        # A C setup gets no size at all rather than a small one.
        self.assertEqual(SW.GRADE_RISK["C"], (0.0, 0.0))

    def test_the_thesis_is_a_sentence_not_a_level_dump(self):
        thesis = SW.evaluate(_row(), regime="FAVORABLE")["thesis"]
        self.assertTrue(thesis.endswith("."))
        self.assertGreater(len(thesis.split()), 8)
        self.assertNotIn("  ", thesis)


class TestStateBands(unittest.TestCase):
    """The score and the word must never disagree. They were computed by
    different logic before, so a low score could still read READY."""

    def test_the_state_follows_the_score_band(self):
        for score, expected in ((92, "READY"), (78, "NEAR READY"),
                                (69, "APPROACHING"), (58, "DEVELOPING"),
                                (40, "AVOID")):
            state, why = SW.setup_state(_row(), "pullback_21ema",
                                        "quiet_pullback", {}, score=score)
            self.assertEqual(state, expected, f"score {score}")
            self.assertIn(str(score), why)

    def test_events_override_the_band(self):
        # A failed breakout is not a weak setup, it is a different one; and a
        # stock 9% over its 8 EMA is unenterable at any score.
        failed, _ = SW.setup_state(_row(), "failed_breakout", "neutral", {},
                                   score=95)
        self.assertEqual(failed, "FAILED")
        extended, _ = SW.setup_state(_row(**{"Pct_vs_8EMA": 9.0}),
                                     "pullback_21ema", "breakout", {}, score=95)
        self.assertEqual(extended, "EXTENDED")

    def test_every_state_carries_an_action(self):
        result = SW.evaluate(_row(), regime="FAVORABLE")
        self.assertTrue(result["action"])


class TestTwoStageTrigger(unittest.TestCase):
    """One trigger was too blunt: AVGO's asked for a 4.3% move before entry,
    against an 8.6% stop."""

    def test_a_pullback_offers_an_early_and_a_full_trigger(self):
        early, full, _chase = SW.triggers_for(
            _row(**{"Current Price": 392.99, "8EMA": 409.73, "21EMA": 402.62,
                    "50MA": 390.33}), "pullback_50ma")
        self.assertEqual(early["price"], 402.62)
        self.assertEqual(full["price"], 409.73)
        self.assertLess(early["price"], full["price"])
        self.assertIn("21 EMA", early["condition"])

    def test_each_stage_says_whether_it_has_fired(self):
        early, full, _c = SW.triggers_for(
            _row(**{"Current Price": 405.0, "8EMA": 409.73, "21EMA": 402.62,
                    "50MA": 390.33}), "pullback_50ma")
        self.assertTrue(early["met"])
        self.assertFalse(full["met"])

    def test_the_chase_cap_sits_above_the_full_trigger(self):
        _e, full, chase = SW.triggers_for(
            _row(**{"Current Price": 392.99, "8EMA": 409.73, "21EMA": 402.62,
                    "50MA": 390.33, "ATR_Pct": 4.08}), "pullback_50ma")
        self.assertGreater(chase["price"], full["price"])
        self.assertFalse(chase["exceeded"])

    def test_a_breakout_that_already_ran_is_missed_not_a_buy(self):
        # The trigger fires, but the move has happened without you: entering
        # here is a different, worse trade than the one that was analysed.
        # (A stock that gaps clear of ALL its levels classifies as "no setup"
        # instead, which is the other correct answer to the same event.)
        ran = _row(**{"Current Price": 112.0, "Prior_Breakout_Level": 100.0,
                      "Dist_52W_High%": -1.0, "Vol_vs_20D": 2.0,
                      "Days_Since_52W_High": 2, "ATR_Pct": 3.0,
                      "8EMA": 108.0, "21EMA": 104.0, "50MA": 98.0,
                      "200MA": 85.0, "Pct_vs_8EMA": 3.7})
        self.assertEqual(SW.classify_setup(ran)[0], "breakout")
        _e, _f, chase = SW.triggers_for(ran, "breakout")
        self.assertTrue(chase["exceeded"])
        result = SW.evaluate(ran, regime="FAVORABLE")
        self.assertEqual(result["state"], "MISSED")
        self.assertIn("retest", result["state_why"])


class TestInvalidationIsNotTheStop(unittest.TestCase):

    def test_the_thesis_fails_above_the_hard_stop(self):
        result = SW.evaluate(
            _row(**{"Current Price": 392.99, "50MA": 390.33, "8EMA": 409.73,
                    "21EMA": 402.62, "200MA": 368.25, "ATR_Pct": 4.08}),
            regime="SELECTIVE")
        inval, stop = result["invalidation"], result["stop"]
        self.assertIsNotNone(inval)
        self.assertGreater(inval["price"], stop,
                           "invalidation must sit above the hard stop")
        self.assertIn("volume", inval["condition"])

    def test_it_names_the_level_the_setup_is_built_on(self):
        inval = SW.invalidation_for(_row(), "pullback_21ema")
        self.assertIn("21 EMA", inval["condition"])


class TestTradeQualityIsNotJustReward(unittest.TestCase):
    """AVGO scored 100 on 2.39R while momentum sat at 36 — a number
    describing the reward available, not the quality of the trade."""

    def test_reward_is_one_leg_of_five(self):
        names = [n for n, _w in SW.PATH_WEIGHTS]
        self.assertIn("Target room", names)
        self.assertIn("Stop quality", names)
        self.assertIn("Resistance density", names)
        # Reward potential may not dominate.
        weights = dict(SW.PATH_WEIGHTS)
        self.assertLessEqual(weights["Target room"], 25)

    def test_a_wide_stop_lowers_trade_quality(self):
        path = SW.resistance_path(_row(**{"R1": 130.0, "8EMA": 99.0,
                                          "21EMA": 98.0, "52W High": 140.0}),
                                  entry=100.0, stop=90.0)
        tight, _d, _p = SW.trade_component(path, _row(**{"ATR_Pct": 5.0}),
                                           100.0, 95.0)
        wide, _d2, _p2 = SW.trade_component(path, _row(**{"ATR_Pct": 1.0}),
                                            100.0, 80.0)
        self.assertGreater(tight, wide)

    def test_level_strength_only_speaks_for_the_level_it_measured(self):
        # `Touches` counts tests of R1. Attaching it to a 52-week high 20%
        # away puts a real number on the wrong level.
        row = _row(**{"R1": 104.0, "52W High": 140.0, "Touches": 500,
                      "8EMA": 99.0, "21EMA": 98.0})
        path = SW.resistance_path(row, entry=110.0, stop=100.0)
        _score, _detail, parts = SW.trade_component(path, row, 110.0, 100.0)
        names = [c["name"] for c in parts.get("components", [])]
        self.assertNotIn("Level strength", names)


class TestRiskRespondsToTheTape(unittest.TestCase):

    def test_a_selective_market_caps_the_size(self):
        strong = SW.risk_for("A", regime_score=100.0)
        weak = SW.risk_for("A", regime_score=55.0)
        self.assertGreater(strong["max"], weak["max"])
        self.assertTrue(weak["capped_by_regime"])
        self.assertIn("market regime", weak["note"])

    def test_a_c_setup_gets_no_size_in_any_tape(self):
        self.assertEqual(SW.risk_for("C", regime_score=100.0)["max"], 0.0)


class TestGatesOverrideTheScore(unittest.TestCase):
    """The score ranks; the gates decide.

    BX is the case: momentum 95, trade quality 73, path Good, setup 0. On a
    weighted average that reads "50 — Avoid", which says the stock is
    undesirable. What the model found was a good chart with no pattern to
    trade today, and those need different words.
    """

    def _no_setup_row(self, **kw):
        base = {"Current Price": 100.0, "8EMA": 100.4, "21EMA": 92.0,
                "50MA": 88.0, "200MA": 80.0, "Prior_Breakout_Level": 70.0,
                "RSI_14": 64.0, "ADX_14": 27.0, "RS_Rank": 77.0,
                "Dist_52W_High%": -20.0, "ATR Shrinking": False,
                "MA200_Slope%": 1.0, "Days_Since_52W_High": 40}
        base.update(kw)
        return _row(**base)

    def test_no_setup_is_not_avoid(self):
        result = SW.evaluate(self._no_setup_row(), regime="FAVORABLE")
        self.assertEqual(result["setup"], "none")
        self.assertNotEqual(result["state"], "AVOID")
        self.assertIn(result["state"], ("WATCH", "NO SETUP", "NO TRADE"))
        self.assertNotIn("Avoid", str(result["grade"]))

    def test_a_good_chart_with_no_pattern_is_a_watchlist_name(self):
        result = SW.evaluate(self._no_setup_row(**{"50MA": 96.0, "21EMA": 98.0,
                                                   "200MA": 85.0}),
                             regime="FAVORABLE")
        self.assertEqual(result["state"], "WATCH")
        self.assertEqual(result["action"], "wait for a setup")

    def test_the_setup_gate_blocks_regardless_of_score(self):
        # Momentum and path cannot manufacture a trade with no pattern.
        result = SW.evaluate(self._no_setup_row(), regime="FAVORABLE")
        self.assertFalse(result["eligible"])
        self.assertNotIn(result["state"], ("READY", "NEAR READY"))

    def test_ready_requires_every_blocking_gate(self):
        # 77 names read READY while only 59 passed every gate, because the
        # path gate did not reach the state machine.
        result = SW.evaluate(_row(), regime="FAVORABLE")
        if result["state"] in ("READY", "NEAR READY"):
            self.assertTrue(result["eligible"])

    def test_advisory_checks_do_not_block(self):
        # An unconfirmed trigger is APPROACHING, not rejected.
        result = SW.evaluate(_row(), regime="FAVORABLE")
        advisory = [g for g in result["gates"] if not g["blocking"]]
        self.assertTrue(advisory)
        self.assertTrue(all(g["blocking"] for g in result["gates"]
                            if g["name"] in ("Trend", "Setup", "Market")))

    def test_breakdown_is_a_fact_not_a_score_threshold(self):
        # A trend SCORE of 45 is a mixed structure. Calling that a breakdown
        # said something the data did not — BX was above its 200 MA.
        above = SW.evaluate(self._no_setup_row(**{"Current Price": 100.0,
                                                  "200MA": 90.0, "50MA": 88.0,
                                                  "21EMA": 92.0}),
                            regime="FAVORABLE")
        self.assertNotEqual(above["state"], "BREAKDOWN")
        below = SW.evaluate(self._no_setup_row(**{"Current Price": 80.0,
                                                  "200MA": 90.0,
                                                  "MA200_Slope%": -2.0}),
                            regime="FAVORABLE")
        self.assertEqual(below["state"], "BREAKDOWN")
        self.assertIn("200 MA", below["state_why"])


class TestStopEfficiency(unittest.TestCase):
    """WDC priced a swing with a 29.4% stop. Position sizing held account
    risk at 1.9% and the trade was still a bad swing."""

    def test_the_band_penalises_wide_stops(self):
        self.assertEqual(SW.stop_efficiency(3.0), 100.0)
        self.assertEqual(SW.stop_efficiency(9.0), 60.0)
        self.assertEqual(SW.stop_efficiency(29.4), 0.0)

    def test_a_stop_past_the_ceiling_is_not_a_swing(self):
        gates = SW.evaluate_gates("pullback_21ema", 85.0, 80.0, 100.0,
                                  {"first_r": 2.0}, 29.4, None, None)
        stop_gate = next(g for g in gates if g["name"] == "Stop")
        self.assertFalse(stop_gate["ok"])
        self.assertTrue(stop_gate["blocking"])

    def test_a_wide_but_allowed_stop_is_flagged(self):
        gates = SW.evaluate_gates("pullback_21ema", 85.0, 80.0, 100.0,
                                  {"first_r": 2.0}, 15.0, None, None)
        stop_gate = next(g for g in gates if g["name"] == "Stop")
        self.assertTrue(stop_gate["ok"])
        self.assertIn("high-volatility", stop_gate["detail"])


class TestPathDetailsNameTheirPrices(unittest.TestCase):
    """A bare R multiple cannot be checked against a chart.

    "first resistance 0.20R away" is uninterpretable without the level it
    measures to and the entry it measures from — and showing the workings is
    the only reason these sub-scores are displayed at all.
    """

    def _parts(self, **kw):
        row = _row(**kw)
        entry, _n = SW.planned_entry(row, "pullback_21ema")
        stop, _s = SW._stop_for(row, "pullback_21ema")
        path = SW.resistance_path(row, entry, stop, row["Current Price"])
        _score, _d, parts = SW.trade_component(path, row, entry, stop)
        return {c["name"]: c["detail"] for c in parts["components"]}, path

    def test_every_leg_quotes_a_dollar_price(self):
        parts, _path = self._parts(**{"R1": 104.0, "52W High": 130.0,
                                      "Touches": 30})
        for name, detail in parts.items():
            self.assertIn("$", detail, f"{name} has no price in it")

    def test_resistance_clearance_names_the_level_and_the_entry(self):
        parts, _p = self._parts(**{"R1": 104.0, "52W High": 130.0})
        detail = parts["Resistance clearance"]
        self.assertIn("R1 resistance", detail)
        self.assertIn("$104.00", detail)
        self.assertIn("R from $", detail)

    def test_blocking_levels_are_named_not_counted(self):
        parts, _p = self._parts(**{"R1": 104.0, "52W High": 130.0})
        detail = parts["Resistance density"]
        self.assertIn("$", detail)
        self.assertNotEqual(detail, "1 level(s) between the entry and the target")

    def test_stop_quality_quotes_the_stop_and_the_entry(self):
        parts, _p = self._parts(**{"R1": 104.0, "52W High": 130.0})
        detail = parts["Stop quality"]
        self.assertIn("stop $", detail)
        self.assertIn("entry", detail)

    def test_level_strength_quotes_the_level_it_counted(self):
        parts, _p = self._parts(**{"R1": 104.0, "52W High": 130.0,
                                   "Touches": 30})
        self.assertIn("$104.00", parts["Level strength"])

    def test_the_path_note_names_the_level(self):
        _parts, path = self._parts(**{"R1": 104.0, "52W High": 130.0})
        self.assertIn("$", path["note"])


class TestEntryAndSizingAgreeWithTheLongTermPanel(unittest.TestCase):
    """The two panels priced the same stock differently on the same day —
    $279.44/$255.03 against $266.26/$239.50 — because each picked its own
    entry and stop by its own rules."""

    def test_a_defended_shelf_within_reach_becomes_the_entry(self):
        row = _row(**{"Current Price": 100.0, "S1": 99.0, "Touches": 40,
                      "Volume_Confirmation": True, "ATR_Pct": 2.0})
        entry, note = SW.planned_entry(row, "pullback_21ema")
        self.assertEqual(entry, 99.0)
        self.assertIn("tested shelf", note)
        self.assertIn("40 touches", note)

    def test_an_untested_shelf_is_not_an_entry(self):
        # `S1` can be arithmetic on recent closes. Treating that as a price
        # to work an order at invents a level.
        row = _row(**{"Current Price": 100.0, "S1": 99.0, "Touches": 2,
                      "Volume_Confirmation": False})
        shelf, reason = SW.tested_support(row, 100.0)
        self.assertIsNone(shelf)
        self.assertIn("not volume-confirmed", reason)
        entry, _note = SW.planned_entry(row, "pullback_21ema")
        self.assertNotEqual(entry, 99.0)

    def test_a_shelf_out_of_reach_is_a_different_trade(self):
        # A defended shelf 15% below is a real level and not THIS trade: the
        # entry falls through to the setup's own trigger instead.
        row = _row(**{"Current Price": 100.0, "S1": 85.0, "Touches": 40,
                      "ATR_Pct": 2.0})
        shelf, _r = SW.tested_support(row, 100.0)
        self.assertEqual(shelf, 85.0)
        entry, _note = SW.planned_entry(row, "pullback_21ema")
        self.assertNotEqual(entry, 85.0)

    def test_the_stop_prefers_a_defended_level_over_the_setup_anchor(self):
        row = _row(**{"Current Price": 100.0, "S1": 96.0, "Touches": 40,
                      "21EMA": 90.0, "ATR_Pct": 2.0})
        stop, note = SW._stop_for(row, "pullback_21ema", entry=100.0)
        self.assertIn("tested shelf", note)
        self.assertGreater(stop, 90.0)

    def test_the_stop_takes_the_nearest_structural_level(self):
        # Reaching for the setup's own anchor put MSFT's swing stop 18% down
        # at the 50 MA while a prior breakout sat 4.4% below.
        row = _row(**{"Current Price": 100.0, "S1": None, "21EMA": 97.0,
                      "50MA": 82.0, "Prior_Breakout_Level": 95.0,
                      "ATR_Pct": 2.0, "Touches": 0,
                      "Volume_Confirmation": False})
        stop, note = SW._stop_for(row, "pullback_50ma", entry=100.0)
        self.assertGreater(stop, 90.0, f"stop reached past a nearer level: {note}")

    def test_the_swing_is_sized_by_the_same_function_as_the_long_term_panel(self):
        from stockanalysis.core.longterm import position_sizing as PS
        settings = PS.normalize_settings({"capital": 100000, "risk_pct": 2.0})
        sized = SW.size_swing(100.0, 95.0, 0.75, settings)
        direct = PS.size_position(
            100.0, 95.0, PS.normalize_settings(
                dict(settings, risk_pct=0.75)))
        self.assertEqual(sized["shares"], direct["shares"])

    def test_the_swing_risk_budget_is_not_the_account_ceiling(self):
        # max_dollar_risk is DERIVED from capital x risk_pct, so overriding
        # the percentage alone left the swing sizing itself on the account's
        # 2% after all.
        from stockanalysis.core.longterm import position_sizing as PS
        settings = PS.normalize_settings({"capital": 100000, "risk_pct": 2.0})
        sized = SW.size_swing(100.0, 95.0, 0.5, settings)
        self.assertLessEqual(sized["actual_risk"], 500 + 1)
        self.assertEqual(sized["risk_pct_used"], 0.5)

    def test_no_size_without_a_risk_budget(self):
        from stockanalysis.core.longterm import position_sizing as PS
        settings = PS.normalize_settings({"capital": 100000})
        self.assertIsNone(SW.size_swing(100.0, 95.0, 0.0, settings))


class TestTargetLadder(unittest.TestCase):

    def test_the_52_week_high_is_last_not_the_headline(self):
        result = SW.evaluate(_row(), regime="FAVORABLE")
        targets = result["targets"]
        self.assertTrue(targets)
        labels = [t["label"] for t in targets]
        self.assertEqual(labels[0], "T1")
        last = targets[-1]
        self.assertEqual(last["basis"], "52-week high")
        # T1 must be nearer than the 52-week high.
        self.assertLess(targets[0]["price"], last["price"])

    def test_first_resistance_r_is_graded(self):
        self.assertEqual(SW.first_resistance_grade(2.5)[0], "🟢")
        self.assertEqual(SW.first_resistance_grade(1.4)[0], "🟡")
        self.assertEqual(SW.first_resistance_grade(0.6)[0], "🔴")


class TestAVGOShape(unittest.TestCase):
    """The worked example: elite company, expensive, 50 MA pullback with no
    confirmation. The swing engine must reach its own verdict."""

    def setUp(self):
        self.result = SW.evaluate(_row(**{
            "Ticker": "AVGO", "Current Price": 392.99, "8EMA": 409.73,
            "21EMA": 402.62, "50MA": 390.33, "200MA": 368.25,
            "MA50_Slope%": -3.03, "MA200_Slope%": 1.59, "RSI_14": 46.5,
            "RS_Rank": 11.0, "Vol_vs_20D": 1.63, "Pullback_Vol_Ratio": 0.99,
            "Distribution_Days_25d": 5, "ATR_Pct": 4.08, "Touches": 521,
            "52W High": 494.22, "Dist_52W_High%": -20.5,
            "Days_Since_52W_High": 90, "Prior_Breakout_Level": 351.84,
            "R1": 397.43, "S1": 391.33, "Pct_vs_8EMA": -4.09,
            "Prev-Day Close": 399.0, "Volume_Confirmation": False,
            "Reversal_Candle": "none", "ADX_14": 18.0}), regime="SELECTIVE")

    def test_it_is_a_fifty_ma_pullback_not_a_buy(self):
        self.assertEqual(self.result["setup"], "pullback_50ma")
        self.assertIn(self.result["state"], ("APPROACHING", "DEVELOPING"))
        self.assertNotIn(self.result["state"], ("READY", "NEAR READY"))

    def test_it_scores_in_the_middle_rather_than_high(self):
        # Intact long-term trend, no short-term momentum, selling volume.
        self.assertGreaterEqual(self.result["score"], 55)
        self.assertLess(self.result["score"], 80)

    def test_the_volume_is_read_as_selling(self):
        self.assertEqual(self.result["volume_kind"], "selling")

    def test_a_support_entry_has_nothing_to_clear(self):
        # The entry is now a limit at the tested shelf BELOW the price, so
        # there is no ladder of resistance to climb before reaching it. The
        # levels-to-clear list only applies when the entry is a trigger
        # above today's price.
        self.assertLessEqual(self.result["entry"], self.result["price"])
        self.assertEqual(self.result["path"]["to_clear"], [])
        self.assertIn("shelf", self.result["entry_note"])

    def test_the_trigger_matches_the_entry(self):
        # Buying a limit at the shelf and "waiting for a reclaim above" are
        # two different trades; the panel used to print both at once.
        early = self.result["triggers"]["early"]
        self.assertEqual(early["price"], self.result["entry"])
        self.assertIn("hold", early["condition"])
        self.assertIn("8 EMA", self.result["triggers"]["full"]["condition"])


if __name__ == "__main__":
    unittest.main()
