"""
Tests for core.decision_engine — the three scores the decision layer is
built on.

The invariant these defend: a missing input never becomes a score. Absent
data is reported as reduced coverage, not as 0 (which reads as bad) and not
as 50 (which invents a measurement).
Run with: python -m unittest tests.test_decision_engine
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import decision_engine as DE


def _row(**kw):
    base = {
        "ticker": "TEST", "quality": 90, "health": 80, "moat": 3,
        "eps_growth": 30.0, "revenue_growth": 15.0, "forward_pe": 20.0,
        "rs_rank": 85, "above_200ma": True, "above_50ma": True,
        "swing_score": 80, "breakout_probability": 75.0, "rr": 3.0,
        "rvol": 1.5, "abs_vs_8ema": 1.0, "in_buy_zone": False,
        "category": "Momentum",
    }
    base.update(kw)
    return base


class TestInvestmentScore(unittest.TestCase):
    def test_a_strong_company_scores_high(self):
        r = DE.investment_score(_row(quality=95, health=90, moat=4,
                                     eps_growth=50, revenue_growth=25,
                                     forward_pe=15, rs_rank=90))
        self.assertGreaterEqual(r["score"], 85)
        self.assertIn(r["label"], ("Strong", "Exceptional"))
        self.assertEqual(r["coverage"], 1.0)

    def test_a_weak_company_scores_low(self):
        r = DE.investment_score(_row(quality=20, health=15, moat=0,
                                     eps_growth=-10, revenue_growth=-5,
                                     forward_pe=60, rs_rank=10,
                                     above_200ma=False))
        self.assertLess(r["score"], 30)
        self.assertEqual(r["label"], "Weak")

    def test_a_missing_field_reduces_coverage_rather_than_the_score(self):
        full = DE.investment_score(_row())
        partial = DE.investment_score(_row(moat=None))
        self.assertIn("Moat", partial["missing"])
        self.assertLess(partial["coverage"], 1.0)
        # Dropping a component the stock scored ~75 on shouldn't crater it
        self.assertGreater(partial["score"], full["score"] - 15)

    def test_no_data_at_all_scores_none_not_zero(self):
        r = DE.investment_score({})
        self.assertIsNone(r["score"])
        self.assertIsNone(r["label"])
        self.assertEqual(r["coverage"], 0.0)

    def test_negative_pe_is_not_treated_as_cheap(self):
        # A negative multiple means no earnings, which is not a bargain.
        cheap = DE.investment_score(_row(forward_pe=12))
        loss = DE.investment_score(_row(forward_pe=-30))
        self.assertIn("Valuation", loss["missing"])
        self.assertGreater(cheap["score"], loss["score"])

    def test_extreme_eps_growth_is_capped(self):
        # 21,000% EPS growth is an accounting artifact, not 350x the signal
        big = DE.investment_score(_row(eps_growth=21000))
        good = DE.investment_score(_row(eps_growth=60))
        self.assertEqual(big["score"], good["score"])


class TestSwingScore(unittest.TestCase):
    def test_a_clean_setup_scores_high(self):
        r = DE.swing_score(_row(swing_score=90, rs_rank=95,
                                breakout_probability=90, rr=4.0, rvol=2.5))
        self.assertGreaterEqual(r["score"], 85)
        self.assertIn(r["label"], ("A Setup", "A+ Setup"))

    def test_fundamentals_cannot_inflate_a_swing_score(self):
        # Perfect company, no setup: the swing score must stay low.
        r = DE.swing_score(_row(quality=100, health=100, moat=4,
                                eps_growth=200, swing_score=5, rs_rank=5,
                                breakout_probability=0, rr=0.5, rvol=0.3,
                                above_200ma=False, above_50ma=False))
        self.assertLess(r["score"], 30)
        self.assertEqual(r["label"], "Avoid")

    def test_falls_back_to_ema_distance_without_a_scan_swing_score(self):
        r = DE.swing_score(_row(swing_score=None, abs_vs_8ema=0.5))
        self.assertFalse(r["used_scan_swing"])
        self.assertNotIn("Setup quality", r["missing"])

    def test_missing_rr_and_breakout_are_reported(self):
        # The common case: R:R covers 207/545 rows, breakout 313/545.
        r = DE.swing_score(_row(rr=None, breakout_probability=None))
        self.assertIn("Risk/reward", r["missing"])
        self.assertIn("Breakout probability", r["missing"])
        self.assertLess(r["coverage"], 0.80)
        self.assertIsNotNone(r["score"])


class TestConfluence(unittest.TestCase):
    def test_counts_independent_confirmations(self):
        c = DE.confluence(_row(), regime_favorable=True)
        self.assertGreaterEqual(c["score"], 8)
        self.assertEqual(c["max"], 10)

    def test_correlated_signals_are_not_double_counted(self):
        # Quality and Moat are one business-strength check, not two.
        both = DE.confluence(_row(quality=95, moat=4))
        one = DE.confluence(_row(quality=95, moat=0))
        self.assertEqual(both["score"], one["score"])

    def test_unknown_regime_is_not_counted_either_way(self):
        c = DE.confluence(_row(), regime_favorable=None)
        self.assertIn("Market regime", c["unknown"])
        self.assertNotIn("Market regime", c["hits"])
        self.assertNotIn("Market regime", c["misses"])

    def test_measured_separates_unknown_from_failed(self):
        sparse = DE.confluence({"quality": 90}, regime_favorable=None)
        self.assertLess(sparse["measured"], 10)
        self.assertTrue(sparse["unknown"])

    def test_a_broken_stock_scores_low(self):
        c = DE.confluence(_row(quality=10, health=10, moat=0, eps_growth=-50,
                               rs_rank=5, above_200ma=False, above_50ma=False,
                               rr=0.4, rvol=0.3, breakout_probability=5,
                               abs_vs_8ema=25, category="Avoid",
                               in_buy_zone=False), regime_favorable=False)
        self.assertLessEqual(c["score"], 1)


class TestScoreRow(unittest.TestCase):
    def test_reliable_requires_coverage_on_both_scores(self):
        self.assertTrue(DE.score_row(_row())["reliable"])

    def test_sparse_data_is_not_reliable(self):
        r = DE.score_row({"ticker": "X", "quality": 90})
        self.assertFalse(r["reliable"])

    def test_returns_all_three_scores(self):
        r = DE.score_row(_row(), regime_favorable=True)
        self.assertIn("investment", r)
        self.assertIn("swing", r)
        self.assertIn("confluence", r)
        self.assertEqual(r["ticker"], "TEST")



class TestActions(unittest.TestCase):
    def _strong(self, **kw):
        base = dict(quality=90, health=85, moat=4, eps_growth=40,
                    rs_rank=92, swing_score=85, breakout_probability=85,
                    rr=3.5, rvol=1.8, abs_vs_8ema=1.0, above_200ma=True,
                    above_50ma=True, days_to_earnings=40)
        base.update(kw)          # kw wins; passing both would be a TypeError
        return _row(**base)

    def test_exactly_one_action_and_it_is_a_known_one(self):
        d = DE.decide(self._strong())
        self.assertIn(d["action"], DE.ACTIONS)
        self.assertNotIn("/", d["action"])          # no "BUY/WATCH"

    def test_strategy_decides_which_score_gates(self):
        # Strong company, poor setup: a long-term buy, not a swing entry.
        row = _row(quality=95, health=90, moat=4, eps_growth=50, rs_rank=88,
                   swing_score=20, breakout_probability=10, rr=2.5,
                   rvol=0.5, abs_vs_8ema=1.0, days_to_earnings=40)
        lt = DE.decide(row, strategy="LONGTERM")["action"]
        sw = DE.decide(row, strategy="SWING")["action"]
        self.assertNotEqual(lt, sw)

    def test_balanced_requires_both_scores(self):
        # max() would let this through; min() is the honest reading.
        row = _row(quality=20, health=25, moat=0, eps_growth=-5,
                   swing_score=90, rs_rank=95, rr=3.0, days_to_earnings=40)
        self.assertNotEqual(DE.decide(row, strategy="BALANCED")["action"],
                            "BUY NOW")

    def test_imminent_earnings_downgrades_a_buy(self):
        row = self._strong(days_to_earnings=1)
        d = DE.decide(row, strategy="SWING")
        self.assertEqual(d["action"], "WAIT")
        self.assertEqual(d["earnings_risk"], "CRITICAL")
        self.assertTrue(any("Earnings" in r for r in d["risks"]))

    def test_earnings_strategy_opt_in_restores_the_buy(self):
        row = self._strong(days_to_earnings=3)
        d = DE.decide(row, strategy="SWING", earnings_strategy=True)
        self.assertNotEqual(d["action"], "WAIT")

    def test_weak_quality_is_speculative_not_a_buy(self):
        row = self._strong(quality=30, health=35)
        self.assertEqual(DE.decide(row, strategy="SWING")["action"],
                         "SPECULATIVE")

    def test_negative_rr_is_disqualifying(self):
        self.assertEqual(DE.decide(self._strong(rr=0.4))["action"], "AVOID")

    def test_insufficient_data_never_yields_a_buy(self):
        d = DE.decide({"ticker": "X", "quality": 90})
        self.assertEqual(d["action"], "AVOID")
        self.assertFalse(d["reliable"])

    def test_extended_price_becomes_buy_on_pullback(self):
        # No breakout in progress — a genuine breakout IS extended, and is
        # entered on the breakout, so it must not be diverted to "pullback".
        # pct_vs_8ema is what decides direction; abs_vs_8ema alone can't
        # say whether 9% is a chase or a collapse.
        row = self._strong(pct_vs_8ema=9.0, abs_vs_8ema=9.0,
                           in_buy_zone=False,
                           breakout_probability=30, rvol=0.9)
        d = DE.decide(row, strategy="SWING")
        self.assertIn(d["action"], ("BUY ON PULLBACK", "WATCH"))
        self.assertTrue(d["triggers"])

    def test_watch_always_says_what_would_change_it(self):
        row = _row(quality=75, health=70, moat=2, eps_growth=18, rs_rank=72,
                   swing_score=66, breakout_probability=75, rr=1.4,
                   rvol=0.9, abs_vs_8ema=6.0, days_to_earnings=40)
        d = DE.decide(row, strategy="SWING")
        if d["action"] in ("WATCH", "WAIT"):
            self.assertTrue(d["triggers"])

    def test_defensive_regime_is_stricter_than_favorable(self):
        row = _row(quality=80, health=75, moat=3, eps_growth=25, rs_rank=82,
                   swing_score=78, breakout_probability=72, rr=2.2,
                   rvol=1.3, abs_vs_8ema=1.5, days_to_earnings=40)
        fav = DE.decide(row, strategy="SWING", regime="FAVORABLE")["action"]
        dfn = DE.decide(row, strategy="SWING", regime="DEFENSIVE")["action"]
        order = {a: i for i, a in enumerate(
            ["BUY NOW", "BREAKOUT ENTRY", "BUY ZONE", "BUY ON PULLBACK",
             "WATCH", "WAIT", "SPECULATIVE", "AVOID"])}
        self.assertGreaterEqual(order[dfn], order[fav])

    def test_earnings_risk_bands(self):
        self.assertEqual(DE.earnings_risk(1), "CRITICAL")
        self.assertEqual(DE.earnings_risk(5), "HIGH")
        self.assertEqual(DE.earnings_risk(10), "MEDIUM")
        self.assertEqual(DE.earnings_risk(40), "LOW")
        self.assertEqual(DE.earnings_risk(None), "UNKNOWN")

    def test_recovered_data_is_flagged_as_a_risk(self):
        row = self._strong(recovered=True, data_as_of="2026-08-05 16:45")
        self.assertTrue(any("snapshot" in r for r in
                            DE.decide(row)["risks"]))

if __name__ == "__main__":
    unittest.main()


class TestEmaDirection(unittest.TestCase):
    """abs_vs_8ema cannot tell above from below. Reading it in decide()
    reported WDC — 11% UNDER its 8 EMA — as "11.0% above it now", and
    offered a pullback that had already happened."""

    def _base(self, pct8, **kw):
        row = _row(quality=93, health=77, moat=3, eps_growth=31.0,
                   rs_rank=20, swing_score=42, breakout_probability=30,
                   rr=1.15, rvol=1.0, above_200ma=True, above_50ma=False,
                   in_buy_zone=False, days_to_earnings=40)
        row["pct_vs_8ema"] = pct8
        row["abs_vs_8ema"] = abs(pct8)
        row.update(kw)
        return row

    def test_below_the_ema_is_never_described_as_above(self):
        d = DE.decide(self._base(-11.0), strategy="LONGTERM")
        text = " ".join(d["triggers"])
        self.assertIn("below", text)
        self.assertNotIn("above it", text)

    def test_above_the_ema_is_described_as_above(self):
        d = DE.decide(self._base(7.0), strategy="LONGTERM")
        self.assertTrue(any("above" in t for t in d["triggers"]))

    def test_far_below_the_ema_is_not_offered_as_a_pullback_entry(self):
        # A pullback you can buy requires being extended above it first.
        d = DE.decide(self._base(-11.0, rr=3.0, rs_rank=85, swing_score=80),
                      strategy="LONGTERM")
        self.assertNotEqual(d["action"], "BUY ON PULLBACK")

    def test_extended_above_still_routes_to_pullback(self):
        d = DE.decide(self._base(7.0, rr=3.0, rs_rank=85, swing_score=80,
                                 breakout_probability=60, above_50ma=True),
                      strategy="LONGTERM")
        self.assertIn(d["action"], ("BUY ON PULLBACK", "WATCH"))


class TestPullbackPlan(unittest.TestCase):
    """BUY ON PULLBACK names a price it never showed. The badge said wait;
    it did not say wait for what, how far away it was, or — on the
    catch-all rule, which carried no note at all — why waiting was the
    verdict on a stock sitting right on its 8 EMA."""

    def _pb(self, **kw):
        row = _row(quality=88, health=80, moat=3, eps_growth=32.0,
                   revenue_growth=22.0, forward_pe=28.0, rs_rank=88,
                   swing_score=80, breakout_probability=62, rr=2.6,
                   rvol=1.1, price=140.0, ema8=130.2, ema21=126.1,
                   ma50=121.7, pct_vs_8ema=7.5, abs_vs_8ema=7.5,
                   pct_vs_21ema=11.0, pct_vs_50ma=15.0, dist_52w_high=-1.2,
                   high_52w=141.7, rsi=74, atr_pct=2.7, in_buy_zone=False,
                   buy_zone_label="Watch List", buy_zone_score=55,
                   days_to_earnings=40)
        row.update(kw)
        return row

    def test_every_pullback_verdict_carries_a_plan(self):
        for pct8 in (7.5, 0.6, -2.8):
            with self.subTest(pct8=pct8):
                d = DE.decide(self._pb(pct_vs_8ema=pct8, abs_vs_8ema=abs(pct8)),
                              strategy="LONGTERM")
                if d["action"] != "BUY ON PULLBACK":
                    continue
                self.assertIsNotNone(d["pullback"])
                self.assertTrue(d["pullback"]["headline"])

    def test_other_actions_carry_no_plan(self):
        d = DE.decide(_row(quality=20, health=15, rr=0.4), strategy="LONGTERM")
        self.assertEqual(d["action"], "AVOID")
        self.assertIsNone(d["pullback"])

    def test_the_entry_is_named_as_a_price_not_a_distance(self):
        d = DE.decide(self._pb(), strategy="LONGTERM")
        levels = {l["name"]: l for l in d["pullback"]["levels"]}
        self.assertAlmostEqual(levels["8 EMA"]["price"], 130.2, places=2)
        # Signed distance of price above the level, and the move down to it.
        self.assertAlmostEqual(levels["8 EMA"]["pct"], 7.5, places=1)
        self.assertLess(levels["8 EMA"]["move"], 0)
        self.assertTrue(any("$130.20" in t for t in d["triggers"]))

    def test_a_missing_level_price_is_derived_not_dropped(self):
        # The scan writes the distance on some rows and the level on others.
        # A row carrying only the distance still has to price the entry.
        d = DE.decide(self._pb(ema8=None), strategy="LONGTERM")
        ema8 = next(l for l in d["pullback"]["levels"] if l["name"] == "8 EMA")
        self.assertAlmostEqual(ema8["price"], 140.0 / 1.075, places=2)

    def test_a_level_with_no_data_at_all_is_omitted(self):
        d = DE.decide(self._pb(ema21=None, pct_vs_21ema=None),
                      strategy="LONGTERM")
        names = [l["name"] for l in d["pullback"]["levels"]]
        self.assertNotIn("21 EMA", names)
        self.assertIn("8 EMA", names)

    def test_extension_is_reported_in_units_of_the_stocks_own_range(self):
        # 7.5% above the 8 EMA is a chase on a quiet stock and a normal day
        # on a volatile one; the raw percentage cannot tell them apart.
        d = DE.decide(self._pb(), strategy="LONGTERM")
        self.assertAlmostEqual(d["pullback"]["stretch_atr"], 7.5 / 2.7, places=1)

    def test_a_stock_on_its_average_is_not_reported_as_stretched(self):
        d = DE.decide(self._pb(pct_vs_8ema=0.6, abs_vs_8ema=0.6),
                      strategy="LONGTERM")
        self.assertIsNone(d["pullback"]["stretch_atr"])

    def test_the_catch_all_pullback_names_the_gate_it_missed(self):
        # Not extended, so the wait is about the setup rather than the
        # price — and that used to be left entirely unsaid.
        d = DE.decide(self._pb(price=131.0, pct_vs_8ema=0.6, abs_vs_8ema=0.6,
                               rr=None), strategy="LONGTERM")
        self.assertEqual(d["action"], "BUY ON PULLBACK")
        self.assertEqual(d["pullback"]["reason"], "at_the_line")
        self.assertTrue(any("R:R unknown" in g for g in d["pullback"]["gaps"]))
        self.assertTrue(d["risks"])

    def test_a_pullback_already_under_way_says_so(self):
        d = DE.decide(self._pb(price=126.5, pct_vs_8ema=-2.8, abs_vs_8ema=2.8,
                               pct_vs_21ema=0.3, pct_vs_50ma=3.9, rr=3.1),
                      strategy="LONGTERM")
        self.assertEqual(d["action"], "BUY ON PULLBACK")
        self.assertEqual(d["pullback"]["reason"], "in_progress")
        # The 8 EMA is above the price now, so it is not offered as an entry.
        self.assertFalse(any("8 EMA at" in t for t in d["triggers"]))

    def test_a_row_with_no_8ema_reading_says_so_rather_than_pricing_one(self):
        d = DE.decide(self._pb(pct_vs_8ema=None, abs_vs_8ema=None, ema8=None,
                               rr=3.0), strategy="LONGTERM")
        if d["action"] == "BUY ON PULLBACK":
            self.assertEqual(d["pullback"]["reason"], "unknown")
            self.assertNotIn("8 EMA",
                             [l["name"] for l in d["pullback"]["levels"]])

    def test_the_gate_named_follows_the_strategy_asked_about(self):
        row = self._pb(price=131.0, pct_vs_8ema=0.6, abs_vs_8ema=0.6,
                       swing_score=74)
        lt = DE.decide(row, strategy="LONGTERM")
        sw = DE.decide(row, strategy="SWING")
        for d, expected in ((lt, "Investment score"), (sw, "Swing score")):
            if d["action"] == "BUY ON PULLBACK" and d["pullback"]["gaps"]:
                joined = " ".join(d["pullback"]["gaps"])
                if "score" in joined:
                    self.assertIn(expected, joined)

    def test_the_plan_is_json_safe(self):
        import json
        json.dumps(DE.decide(self._pb(), strategy="LONGTERM")["pullback"])


class TestEarningsGateAppliesToEveryBuy(unittest.TestCase):
    """The gate used to fire only when the score already cleared WATCH, so a
    low-scoring stock skipped it and reached a buy further down the ladder.
    MNST reported earnings the same day and still came out BUY ZONE under
    Swing, because its swing score of 56 was too low to trip the guard."""

    def _row_in_zone(self, **kw):
        row = _row(quality=93, health=100, moat=3, eps_growth=25.0,
                   rs_rank=85, swing_score=56, breakout_probability=40,
                   rr=1.5, rvol=1.0, abs_vs_8ema=1.0, pct_vs_8ema=-1.0,
                   in_buy_zone=True, days_to_earnings=0)
        row.update(kw)
        return row

    def test_a_low_scoring_stock_cannot_buy_through_the_gate(self):
        d = DE.decide(self._row_in_zone(), strategy="SWING")
        self.assertNotIn(d["action"],
                         ("BUY NOW", "BUY ZONE", "BUY ON PULLBACK",
                          "BREAKOUT ENTRY"))

    def test_a_high_scoring_stock_is_downgraded_to_wait(self):
        d = DE.decide(self._row_in_zone(), strategy="LONGTERM")
        self.assertEqual(d["action"], "WAIT")
        self.assertTrue(any("Earnings" in r for r in d["risks"]))

    def test_reporting_today_reads_as_today_not_in_0_days(self):
        d = DE.decide(self._row_in_zone(), strategy="LONGTERM")
        self.assertTrue(any("today" in r for r in d["risks"]))

    def test_the_opt_in_still_allows_an_earnings_trade(self):
        d = DE.decide(self._row_in_zone(), strategy="LONGTERM",
                      earnings_strategy=True)
        self.assertIn(d["action"], ("BUY ZONE", "BUY ON PULLBACK", "BUY NOW"))

    def test_buy_zone_respects_the_selected_strategy(self):
        # It tested the investment score whatever the strategy, so a swing
        # screen could return BUY ZONE on a setup scoring 56.
        row = self._row_in_zone(days_to_earnings=40)
        self.assertEqual(DE.decide(row, strategy="LONGTERM")["action"],
                         "BUY ZONE")
        self.assertNotEqual(DE.decide(row, strategy="SWING")["action"],
                            "BUY ZONE")


class TestLadderStructure(unittest.TestCase):
    """The rewrite exists to make the bug class unreachable, not just to
    fix the three instances of it. These assert the structure, so a new
    rule cannot reintroduce them."""

    def test_every_action_in_the_ladder_is_a_declared_action(self):
        for rule in DE.LADDER:
            with self.subTest(action=rule["action"]):
                self.assertIn(rule["action"], DE.ACTIONS)

    def test_the_ladder_always_reaches_a_verdict(self):
        # The last rule must be unconditional or a row could fall through.
        self.assertTrue(DE.LADDER[-1]["test"]({}))

    def test_every_buy_action_is_marked_buy(self):
        # `buy` is what makes the engine apply the earnings and
        # data-quality guards; an unmarked buy rule would bypass both.
        buys = {"BUY NOW", "BUY ZONE", "BUY ON PULLBACK", "BREAKOUT ENTRY"}
        for rule in DE.LADDER:
            with self.subTest(action=rule["action"]):
                if rule["action"] in buys:
                    self.assertTrue(rule["buy"])

    def test_no_buy_rule_can_fire_inside_the_earnings_window(self):
        # Structural, not per-branch: the engine skips every rule marked
        # buy, so this holds for rules that do not exist yet.
        buys = {"BUY NOW", "BUY ZONE", "BUY ON PULLBACK", "BREAKOUT ENTRY"}
        row = _row(quality=95, health=95, moat=4, eps_growth=50, rs_rank=95,
                   swing_score=90, breakout_probability=95, rr=4.0, rvol=2.0,
                   abs_vs_8ema=0.5, pct_vs_8ema=-0.5, in_buy_zone=True,
                   days_to_earnings=1)
        for strategy in DE.STRATEGIES:
            with self.subTest(strategy=strategy):
                self.assertNotIn(DE.decide(row, strategy=strategy)["action"],
                                 buys)

    def test_the_strategy_score_is_resolved_once(self):
        # Three separate branches once read the investment score while the
        # caller had asked for a swing decision. `best` now comes from one
        # place, so a swing decision cannot be gated on the company.
        row = _row(quality=95, health=95, moat=4, eps_growth=50,
                   swing_score=20, rs_rank=20, breakout_probability=10,
                   rr=3.0, rvol=0.5, in_buy_zone=True, days_to_earnings=40)
        ctx = DE._context(row, DE.score_row(row), "FAVORABLE", "SWING", False)
        self.assertEqual(ctx["best"], ctx["swing"])
        ctx_lt = DE._context(row, DE.score_row(row), "FAVORABLE", "LONGTERM", False)
        self.assertEqual(ctx_lt["best"], ctx_lt["inv"])

    def test_a_missing_input_never_satisfies_a_threshold(self):
        self.assertFalse(DE._gt(None, 50))
        self.assertTrue(DE._gt(50, 50))
