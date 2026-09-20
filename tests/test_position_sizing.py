"""
Tests for core.longterm.position_sizing — risk-based share counts over the
Long-Term Buy Engine's verdict.

The invariants these defend, in the order the module applies them:

  1. Risk is measured from the PLANNED ENTRY, never from the current price.
     A pullback entry has a different risk/share than today's quote, and
     sizing off the quote silently over-sizes every resting order.
  2. The allocation cap is a second, independent limit. Risk sizing alone
     will happily ask for 115% of the account when the stop is tight, and the
     cap is the only thing that stops it.
  3. A missing stop is never a small stop. No defensible level below the
     entry means no position size — not an ATR guess dressed up as a plan.
  4. Shares always round DOWN. Rounding up breaks the risk ceiling by
     definition.

Run with: python -m unittest tests.test_position_sizing
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.longterm import position_sizing as PS

SPEC = {"capital": 100_000, "risk_pct": 2.0, "max_allocation_pct": 100.0}


def _settings(**kw):
    return PS.normalize_settings({**SPEC, **kw})


def _result(**kw):
    """An engine verdict shaped the way position_sizing reads it."""
    base = {
        "ticker": "TEST", "price": 100.0, "action": "BUY NOW",
        "trend": {"state": "CONFIRMED"},
        "pullback": {"zone": "EMA", "candidates": [
            {"name": "8 EMA", "zone": "EMA", "price": 97.0},
            {"name": "50 MA", "zone": "50MA", "price": 92.0},
            {"name": "200 MA", "zone": "200MA", "price": 80.0},
        ]},
        "targets": {"stop": 92.0, "stop_name": "50 MA", "ladder": [
            {"price": 120.0, "name": "R1", "rr": 3.5},
            {"price": 140.0, "name": "52W high", "rr": 6.0},
        ]},
        "entries": [],
    }
    base.update(kw)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# THE ARITHMETIC — §2, §5, §7
# ─────────────────────────────────────────────────────────────────────────────

class TestTheWorkedExample(unittest.TestCase):
    """The brief's own numbers: $100k, 2%, entry 592, stop 570."""

    def test_uncapped(self):
        z = PS.size_position(592.0, 570.0, _settings())
        self.assertEqual(z["risk_per_share"], 22.0)
        self.assertEqual(z["shares"], 90)              # floor(2000/22)
        self.assertEqual(z["position_value"], 53_280.0)
        self.assertEqual(z["allocation_pct"], 53.28)
        self.assertEqual(z["actual_risk"], 1_980.0)

    def test_the_20pct_cap_binds(self):
        # §7: min(risk-based 90, allocation-based floor(20000/592)=33)
        z = PS.size_position(592.0, 570.0, _settings(max_allocation_pct=20))
        self.assertEqual(z["risk_shares"], 90)
        self.assertEqual(z["allocation_shares"], 33)
        self.assertEqual(z["shares"], 33)
        self.assertEqual(z["bound_by"], "allocation")

    def test_the_cap_is_reported_as_oversized_with_the_uncapped_figure(self):
        # §6's example message quotes the allocation risk sizing WANTED
        # (53.3%), not the capped one the row ends up showing.
        s = _settings(max_allocation_pct=20)
        z = PS.size_position(592.0, 570.0, s)
        self.assertEqual(PS.classify(z, s), "OVERSIZED")
        self.assertEqual(z["risk_allocation_pct"], 53.3)
        self.assertIn("53.3%", PS._status_reason("OVERSIZED", z, s))


class TestSizingRules(unittest.TestCase):
    def test_shares_always_round_down(self):
        # floor(2000/30) = 66.67 -> 66, never 67: one more share breaks the
        # ceiling the whole model exists to hold.
        z = PS.size_position(100.0, 70.0, _settings())
        self.assertEqual(z["shares"], 66)
        self.assertLessEqual(z["actual_risk"], _settings()["max_dollar_risk"])

    def test_actual_risk_never_exceeds_the_budget(self):
        s = _settings()
        for entry, stop in ((100, 97), (592, 570), (33.33, 30.01), (7.5, 5.0)):
            z = PS.size_position(entry, stop, s)
            self.assertLessEqual(z["actual_risk"], s["max_dollar_risk"],
                                 f"{entry}/{stop} risked more than the budget")

    def test_allocation_never_exceeds_the_cap(self):
        s = _settings(max_allocation_pct=20)
        for entry, stop in ((100, 99.5), (592, 590), (12.0, 11.9)):
            z = PS.size_position(entry, stop, s)
            self.assertLessEqual(z["allocation_pct"], s["max_allocation_pct"])

    def test_a_wide_stop_shrinks_the_position(self):
        # §12.11: a high-quality name with a wide stop gets a SMALL position.
        # 90 points of risk against a $2,000 budget is 22 shares, not a
        # reason to move the stop closer.
        z = PS.size_position(100.0, 10.0, _settings())
        self.assertEqual(z["shares"], 22)
        self.assertLessEqual(z["actual_risk"], 2_000.0)

    def test_risk_per_share_above_the_whole_budget_buys_nothing(self):
        # One share would already breach the ceiling, so the answer is zero
        # shares and an invalid setup — never a rounded-up one.
        s = _settings()
        z = PS.size_position(3_000.0, 500.0, s)
        self.assertEqual(z["shares"], 0)
        self.assertEqual(PS.classify(z, s), "INVALID_SETUP")

    def test_tighter_stop_never_reduces_the_risk_based_size(self):
        s = _settings()
        wide = PS.size_position(100.0, 90.0, s)["risk_shares"]
        tight = PS.size_position(100.0, 98.0, s)["risk_shares"]
        self.assertGreater(tight, wide)


class TestSettings(unittest.TestCase):
    def test_derived_limits(self):
        s = _settings(max_allocation_pct=20)
        self.assertEqual(s["max_dollar_risk"], 2_000.0)
        self.assertEqual(s["max_position_value"], 20_000.0)

    def test_junk_falls_back_instead_of_raising(self):
        s = PS.normalize_settings({"capital": "not a number"})
        self.assertEqual(s["capital"], PS.env_defaults()["capital"])

    def test_values_are_clamped_not_trusted(self):
        s = PS.normalize_settings({"risk_pct": 999, "max_allocation_pct": 0,
                                   "atr_multiplier": -5})
        self.assertEqual(s["risk_pct"], PS.LIMITS["risk_pct"][1])
        self.assertEqual(s["max_allocation_pct"],
                         PS.LIMITS["max_allocation_pct"][0])
        self.assertEqual(s["atr_multiplier"], PS.LIMITS["atr_multiplier"][0])


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY — §3
# ─────────────────────────────────────────────────────────────────────────────

class TestEntry(unittest.TestCase):
    def test_buy_now_enters_at_the_market(self):
        e = PS.plan_entry(_result(action="BUY NOW"))
        self.assertEqual(e["price"], 100.0)
        self.assertEqual(e["type"], "Current Price")
        self.assertTrue(e["at_market"])

    def test_a_zone_action_enters_at_that_zone_s_level(self):
        e = PS.plan_entry(_result(action="BUY ON 8/21 EMA",
                                  pullback={"zone": "EMA", "candidates": [
                                      {"name": "8 EMA", "zone": "EMA", "price": 97.0},
                                      {"name": "50 MA", "zone": "50MA", "price": 92.0}]}))
        self.assertEqual(e["price"], 97.0)
        self.assertEqual(e["type"], "EMA Pullback")
        self.assertEqual(e["level_name"], "8 EMA")
        self.assertFalse(e["at_market"])

    def test_a_breakout_action_is_labelled_a_breakout_entry(self):
        e = PS.plan_entry(_result(
            action="BUY ON BREAKOUT RETEST",
            pullback={"zone": "BREAKOUT", "candidates": [
                {"name": "prior breakout", "zone": "BREAKOUT", "price": 96.0}]}))
        self.assertEqual((e["price"], e["type"]), (96.0, "Breakout Entry"))

    def test_the_zone_comes_from_the_action_not_from_where_price_sits(self):
        """AVGO: "BUY ON BREAKOUT RETEST" with pullback.zone == "NONE".

        `pullback.zone` answers "where is price now", which reads NONE for
        any extended name — so filtering candidates by it discarded the
        engine's explicit instruction and fell through to a different kind of
        entry entirely, taking R:R from 4.02 to 0.02.
        """
        e = PS.plan_entry(_result(
            action="BUY ON BREAKOUT RETEST",
            pullback={"zone": "NONE", "candidates": [
                {"name": "8 EMA", "zone": "EMA", "price": 97.0},
                {"name": "prior breakout", "zone": "BREAKOUT", "price": 96.0}]}))
        self.assertEqual(e["price"], 96.0)
        self.assertEqual(e["type"], "Breakout Entry")

    def test_a_tested_shelf_outranks_the_moving_average_ladder(self):
        """WDC: S1 defended 113 times 2.7% below, and the entry was reported
        as the 200 MA — a further 21% down.

        The shelf lives in `pullback.buy_zone`; the candidate ladder carries
        only the five moving-average levels, so an entry planner reading just
        the ladder cannot see the single most-tested price on the chart. The
        stop logic already consulted it, which is what made the asymmetry a
        bug rather than a policy.
        """
        e = PS.plan_entry(_result(
            action="DEEP PULLBACK — WAIT FOR SUPPORT",
            pullback={"zone": "NONE",
                      "buy_zone": {"price": 95.0, "actual_support": True,
                                   "source": "volume_shelf", "touches": 113,
                                   "label": "113 touches · volume-confirmed"},
                      "candidates": [
                          {"name": "200 MA", "zone": "200MA", "price": 80.0}]},
            entries=[{"name": "200 MA", "zone": "200MA", "price": 80.0}]))
        self.assertEqual(e["price"], 95.0)
        self.assertEqual(e["type"], "Support Entry")

    def test_a_derived_level_is_not_a_tested_shelf(self):
        # buy_zone also reports arithmetic on recent closes that nobody has
        # traded at. Entering there would be inventing a price.
        e = PS.plan_entry(_result(
            action="WAIT",
            pullback={"zone": "NONE",
                      "buy_zone": {"price": 95.0, "actual_support": False,
                                   "source": "moving_average"},
                      "candidates": []},
            entries=[{"name": "200 MA", "zone": "200MA", "price": 80.0}]))
        self.assertEqual(e["price"], 80.0)

    def test_an_at_market_verdict_beats_a_shelf_below(self):
        # BUY NOW means now. A tested shelf 5% down is not the instruction.
        e = PS.plan_entry(_result(
            action="BUY NOW",
            pullback={"zone": "EMA",
                      "buy_zone": {"price": 95.0, "actual_support": True,
                                   "source": "volume_shelf"},
                      "candidates": []}))
        self.assertEqual(e["price"], 100.0)
        self.assertTrue(e["at_market"])

    def test_a_non_buy_verdict_uses_the_engines_next_entry_level(self):
        e = PS.plan_entry(_result(
            action="WATCH",
            entries=[{"name": "50 MA", "zone": "50MA", "price": 92.0}]))
        self.assertEqual(e["price"], 92.0)
        self.assertEqual(e["type"], "Support Entry")

    def test_no_quote_is_no_entry_not_a_zero(self):
        e = PS.plan_entry(_result(price=None))
        self.assertIsNone(e["price"])
        self.assertEqual(e["type"], "No Valid Entry")

    def test_current_price_is_the_last_resort_and_says_so(self):
        e = PS.plan_entry(_result(action="WATCH", entries=[],
                                  pullback={"zone": None, "candidates": []}))
        self.assertEqual(e["price"], 100.0)
        self.assertIn("no tracked level", e["note"])


# ─────────────────────────────────────────────────────────────────────────────
# STOP — §4
# ─────────────────────────────────────────────────────────────────────────────

class TestStop(unittest.TestCase):
    def test_a_tested_shelf_outranks_a_moving_average(self):
        r = _result(targets={"stop": 95.0, "stop_name": "volume shelf",
                             "ladder": []})
        st = PS.plan_stop(r, 100.0, _settings())
        self.assertEqual(st["price"], 95.0)
        self.assertEqual(st["method"], "tested level")

    def test_structural_levels_outrank_short_emas(self):
        # The 8 EMA sits nearer, and is a trading stop rather than long-term
        # invalidation — sizing against it demanded 115% of the account on
        # real NVDA data, which is what STRUCTURAL_ZONES exists to prevent.
        st = PS.plan_stop(_result(targets={"ladder": []}), 100.0, _settings())
        self.assertEqual(st["price"], 92.0)
        self.assertEqual(st["source"], "50 MA")
        self.assertEqual(st["method"], "structural level")

    def test_the_engine_stop_does_not_borrow_a_structural_zone(self):
        # ADBE: the engine's own stop WAS its 8 EMA. Stamping every engine
        # stop "SUPPORT" promoted it into the structural tier and reported a
        # 3.7% trading stop as long-term invalidation.
        r = _result(targets={"stop": 97.0, "stop_name": "8 EMA", "ladder": []})
        st = PS.plan_stop(r, 100.0, _settings())
        self.assertEqual(st["price"], 92.0)            # the 50 MA, not the EMA
        self.assertEqual(st["method"], "structural level")

    def test_a_volume_shelf_the_ladder_lacks_is_still_tested(self):
        # The engine stop earns the tested tier on its NAME, which is the
        # reason it is consulted at all — S1 shelves are not in the ladder.
        r = _result(targets={"stop": 96.0, "stop_name": "volume shelf",
                             "ladder": []})
        st = PS.plan_stop(r, 100.0, _settings())
        self.assertEqual((st["price"], st["method"]), (96.0, "tested level"))

    def test_a_level_inside_the_noise_band_is_not_a_stop(self):
        # MIN_STOP_PCT: a level 0.5% under the entry manufactures a huge
        # ratio out of something an ordinary session removes.
        r = _result(pullback={"zone": "EMA", "candidates": [
            {"name": "8 EMA", "zone": "EMA", "price": 99.5}]},
            targets={"ladder": []})
        st = PS.plan_stop(r, 100.0, _settings())
        self.assertNotEqual(st["price"], 99.5)

    def test_the_stop_is_measured_below_the_PLANNED_entry(self):
        # The engine's own stop (92) sits above a deep pullback entry of 85,
        # so it is not a stop for THIS trade and must not be reused.
        r = _result(pullback={"zone": "200MA", "candidates": [
            {"name": "50 MA", "zone": "50MA", "price": 92.0},
            {"name": "200 MA", "zone": "200MA", "price": 80.0}]})
        st = PS.plan_stop(r, 85.0, _settings())
        self.assertEqual(st["price"], 80.0)
        self.assertLess(st["price"], 85.0)

    def test_atr_fallback_only_when_nothing_structural_is_below(self):
        r = _result(pullback={"zone": None, "candidates": []},
                    targets={"ladder": []})
        st = PS.plan_stop(r, 100.0, _settings(), atr_pct=3.0)
        self.assertEqual(st["method"], "volatility band")
        self.assertAlmostEqual(st["price"], 94.0)      # 100 - 3% x 2.0

    def test_no_atr_fallback_under_a_broken_trend(self):
        # §8's META case: an ATR band under a broken trend is a number, not
        # an invalidation level.
        r = _result(pullback={"zone": None, "candidates": []},
                    targets={"ladder": []}, trend={"state": "BROKEN"})
        st = PS.plan_stop(r, 100.0, _settings(), atr_pct=3.0)
        self.assertIsNone(st["price"])

    def test_no_level_and_no_atr_means_no_stop(self):
        r = _result(pullback={"zone": None, "candidates": []},
                    targets={"ladder": []})
        st = PS.plan_stop(r, 100.0, _settings(), atr_pct=None)
        self.assertIsNone(st["price"])


# ─────────────────────────────────────────────────────────────────────────────
# TARGETS AND R-MULTIPLES — §10
# ─────────────────────────────────────────────────────────────────────────────

class TestTargets(unittest.TestCase):
    def test_rr_is_recomputed_from_the_planned_entry(self):
        # The engine's ladder carries rr=3.5 for R1, measured from the
        # current price of 100 against the current-price stop. Entering at 98
        # with $5 of risk moves both ends of that ratio.
        t = PS.plan_target(_result(), entry_price=98.0, risk_per_share=5.0)
        self.assertEqual(t["price"], 120.0)
        self.assertEqual(t["rr"], 4.4)                 # (120-98)/5
        self.assertNotEqual(t["rr"], 3.5)

    def test_levels_between_the_entry_and_the_price_are_targets(self):
        # For a pullback entry the nearest resistance lives in the pullback
        # candidates, not the engine's ladder — missing it inflates R:R.
        t = PS.plan_target(_result(), entry_price=85.0, risk_per_share=5.0)
        rungs = {lv["name"]: lv["price"] for lv in t["ladder"]}
        self.assertEqual(rungs.get("50 MA"), 92.0)     # from the candidates
        self.assertEqual(rungs.get("R1"), 120.0)       # from the engine ladder
        self.assertEqual(t["nearest"]["price"], 92.0)

    def test_the_primary_target_is_the_nearest_level_clearing_2R(self):
        """Not simply the nearest level.

        An entry resting just under a cluster made the first thing above it
        the target, so a 1.3% pop became the trade's stated reward — META
        quoted 0.2R to a 21 EMA $7 away, WDC 0.4R, PLTR 0.3R. A level below
        the bar is a waypoint the trade has to get through, not a target.
        """
        t = PS.plan_target(_result(), entry_price=85.0, risk_per_share=5.0)
        self.assertEqual(t["price"], 97.0)             # 2.4R, not 92.0 at 1.4R
        self.assertTrue(t["reached_min_rr"])
        self.assertEqual(t["skipped"], 1)              # the 50 MA at 1.4R
        self.assertGreaterEqual(t["rr"], PS.TARGET_MIN_RR)

    def test_when_nothing_clears_the_bar_the_best_available_is_quoted(self):
        # "The most this chart offers is 1.7R" is a real answer. Falling back
        # to the nearest rung instead would report 0.4R for the same setup.
        r = _result(targets={"ladder": [{"price": 104.0, "name": "R1"}]},
                    pullback={"zone": None, "candidates": [
                        {"name": "8 EMA", "zone": "EMA", "price": 101.0}]})
        t = PS.plan_target(r, entry_price=100.0, risk_per_share=5.0)
        self.assertEqual(t["price"], 104.0)            # 0.8R, the furthest
        self.assertFalse(t["reached_min_rr"])
        self.assertEqual(t["best_rr"], t["rr"])

    def test_the_ladder_still_carries_every_rung(self):
        # The skipped levels are real resistance and stay visible.
        t = PS.plan_target(_result(), entry_price=85.0, risk_per_share=5.0)
        self.assertEqual([lv["price"] for lv in t["ladder"]],
                         [92.0, 97.0, 120.0, 140.0])

    def test_no_level_above_the_entry_means_no_rr(self):
        t = PS.plan_target(_result(targets={"ladder": []},
                                   pullback={"zone": None, "candidates": []}),
                           entry_price=100.0, risk_per_share=5.0)
        self.assertIsNone(t["rr"])

    def test_bands(self):
        self.assertEqual(PS.rr_label(3.1), "Excellent")
        self.assertEqual(PS.rr_label(2.0), "Good")
        self.assertEqual(PS.rr_label(1.6), "Acceptable")
        self.assertEqual(PS.rr_label(1.2), "Weak")
        self.assertIsNone(PS.rr_label(None))


# ─────────────────────────────────────────────────────────────────────────────
# THE WHOLE ASSESSMENT — §6, §8, §11
# ─────────────────────────────────────────────────────────────────────────────

class TestAssess(unittest.TestCase):
    def test_a_clean_setup_sizes_and_reports_every_figure(self):
        p = PS.assess(_result(), _settings(max_allocation_pct=100))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entry"]["price"], 100.0)
        self.assertEqual(p["stop"]["price"], 92.0)
        self.assertEqual(p["sizing"]["shares"], 250)   # floor(2000/8)
        self.assertEqual(p["sizing"]["actual_risk"], 2_000.0)
        self.assertIn("250 shares", p["summary"])

    def test_a_broken_trend_withholds_the_share_count(self):
        # §8's META requirement: N/A because the long-term trend is not
        # confirmed — NOT a position forced out of the risk budget.
        p = PS.assess(_result(trend={"state": "BROKEN"},
                              action="OWN / WAIT FOR TREND"), _settings())
        self.assertFalse(p["ok"])
        self.assertEqual(p["status"], "NOT_ACTIONABLE")
        self.assertIn("trend broken", p["reason"])
        self.assertNotIn("shares", p["sizing"])

    def test_a_blocked_name_still_gets_its_levels_priced(self):
        # PLTR is why: broken trend, but a prior breakout 5% below and four
        # more levels under that. Showing a blank row implied the engine had
        # no read on it, when it had a complete plan it was declining to size.
        p = PS.assess(_result(trend={"state": "BROKEN"},
                              action="OWN / WAIT FOR PRICE"), _settings())
        self.assertTrue(p["pending"])
        self.assertEqual(p["entry"]["price"], 100.0)
        self.assertEqual(p["stop"]["price"], 92.0)
        self.assertIsNotNone(p["target"]["rr"])
        self.assertEqual(p["sizing"]["risk_per_share"], 8.0)
        # ...but nothing that reads as permission to take it.
        for forbidden in ("shares", "position_value", "actual_risk"):
            self.assertNotIn(forbidden, p["sizing"])

    def test_the_trend_reason_names_the_checks_that_failed(self):
        # The reason must not claim there is no structure below the entry —
        # that was true of META and false of 76 of the 153 names the gate
        # catches, PLTR among them.
        p = PS.assess(_result(trend={"state": "BROKEN", "checks": [
            {"name": "200 MA rising", "ok": False, "required": True},
            {"name": "Price above 200 MA", "ok": True, "required": True},
        ]}, action="WATCH"), _settings())
        self.assertIn("200 MA rising", p["reason"])
        self.assertNotIn("Price above 200 MA", p["reason"])
        self.assertNotIn("no structure", p["reason"])

    def test_a_rejected_name_gets_nothing_priced(self):
        # AVOID is a different answer from "not yet": no entry, no ratio.
        p = PS.assess(_result(action="AVOID"), _settings())
        self.assertEqual(p["entry"], {})
        self.assertEqual(p["target"], {})
        self.assertFalse(p.get("pending"))

    def test_an_avoid_verdict_is_never_sized(self):
        p = PS.assess(_result(action="AVOID"), _settings())
        self.assertFalse(p["ok"])
        self.assertEqual(p["status"], "NOT_ACTIONABLE")

    def test_both_reasons_are_given_when_both_apply(self):
        # META: blocked on trend AND with no level far enough below to stop
        # against. Reporting only the second loses the one that has to change.
        p = PS.assess(_result(trend={"state": "BROKEN"}, action="WATCH",
                              pullback={"zone": None, "candidates": []},
                              targets={"ladder": []}), _settings())
        self.assertEqual(p["status"], "NO_STOP")
        self.assertIn("trend broken", p["reason"])
        self.assertIn("no level far enough below", p["reason"])

    def test_no_defensible_stop_is_NO_STOP_not_a_guess(self):
        p = PS.assess(_result(pullback={"zone": None, "candidates": []},
                              targets={"ladder": []}), _settings())
        self.assertEqual(p["status"], "NO_STOP")
        self.assertFalse(p["ok"])
        self.assertEqual(p["sizing"], {})

    def test_high_allocation_sits_between_normal_and_oversized(self):
        s = _settings(max_allocation_pct=20)
        # 16% of capital: inside the 20% cap, past the 75% warning line.
        z = {"shares": 10, "allocation_pct": 16.0, "bound_by": "risk",
             "risk_shares": 10, "allocation_shares": 99, "risk_per_share": 1.0,
             "actual_risk": 10.0, "risk_allocation_pct": 16.0}
        self.assertEqual(PS.classify(z, s), "HIGH_ALLOCATION")
        z["allocation_pct"] = 5.0
        self.assertEqual(PS.classify(z, s), "NORMAL")

    def test_grade_rates_the_setup_not_the_company(self):
        self.assertEqual(PS.position_grade(3.5, "NORMAL", "tested level"), "A")
        self.assertEqual(PS.position_grade(2.5, "NORMAL", "tested level"), "B")
        self.assertEqual(PS.position_grade(1.0, "NORMAL", "tested level"), "D")
        # capped and improvised stops both cost a grade
        self.assertEqual(PS.position_grade(3.5, "OVERSIZED", "tested level"), "B")
        self.assertEqual(PS.position_grade(3.5, "NORMAL", "volatility band"), "B")
        self.assertEqual(PS.position_grade(3.5, "NO_STOP", "tested level"), "—")

    def test_assess_never_raises_on_a_malformed_row(self):
        results = [{"ticker": "X"}]
        PS.attach(results, {}, _settings())
        self.assertIn("sizing_plan", results[0])
        self.assertFalse(results[0]["sizing_plan"]["ok"])


class TestQualityOverride(unittest.TestCase):
    """An LQuality 90+ business is priced through a trend objection.

    META is the case: quality 94, undervalued on a reverse DCF, and blank
    across every trading column because its 200 MA is falling. The override
    buys a volatility stop where no structural level survives — and nothing
    else. It never forgives a verdict that is not about the chart.
    """

    def _elite(self, **kw):
        base = {"quality": {"score": 94, "tier": "Elite"},
                "trend": {"state": "BROKEN", "checks": [
                    {"name": "200 MA rising", "ok": False, "structural": True,
                     "required": True}]},
                "action": "OWN / WAIT FOR TREND"}
        base.update(kw)
        return _result(**base)

    def test_an_elite_business_is_priced_through_a_broken_trend(self):
        p = PS.assess(self._elite(), _settings(max_allocation_pct=100))
        self.assertTrue(p["ok"])
        self.assertTrue(p["quality_override"])
        self.assertEqual(p["entry"]["price"], 100.0)
        self.assertEqual(p["stop"]["price"], 92.0)
        self.assertIsNotNone(p["target"]["rr"])

    def test_it_unlocks_a_volatility_stop_when_nothing_structural_survives(self):
        # META has no level far enough below its entry; without the override
        # the ATR band stays locked and the row reads NO_STOP.
        bare = self._elite(pullback={"zone": None, "candidates": []},
                           targets={"ladder": []})
        p = PS.assess(bare, _settings(), atr_pct=3.88)
        self.assertTrue(p["ok"])
        self.assertEqual(p["stop"]["method"], "volatility band")

        # ...and the same row without the quality still refuses.
        ordinary = dict(bare, quality={"score": 70, "tier": "Good"})
        self.assertFalse(PS.assess(ordinary, _settings(), atr_pct=3.88)["ok"])

    def test_the_warning_names_the_quality_and_the_failed_checks(self):
        p = PS.assess(self._elite(), _settings())
        self.assertIn("94", p["warning"])
        self.assertIn("200 MA rising", p["warning"])
        self.assertIn("not because", p["warning"])

    def test_below_the_bar_is_still_withheld(self):
        p = PS.assess(self._elite(quality={"score": 89, "tier": "High Quality"}),
                      _settings())
        self.assertFalse(p["ok"])
        self.assertFalse(p.get("quality_override"))

    def test_it_forgives_only_trend_objections(self):
        # AVOID and THESIS BROKEN are verdicts on the business, not the chart.
        for action in ("AVOID", "THESIS BROKEN"):
            p = PS.assess(self._elite(action=action), _settings())
            self.assertFalse(p.get("quality_override"), action)
            self.assertEqual(p["entry"], {}, action)
        # RESEARCH is "not enough is known", which quality cannot answer.
        p = PS.assess(self._elite(action="RESEARCH",
                                  trend={"state": "CONFIRMED"}), _settings())
        self.assertFalse(p.get("quality_override"))
        self.assertTrue(p.get("pending"))

    def test_the_override_still_respects_the_risk_and_allocation_caps(self):
        s = _settings(max_allocation_pct=20)
        p = PS.assess(self._elite(), s)
        self.assertLessEqual(p["sizing"]["actual_risk"], s["max_dollar_risk"])
        self.assertLessEqual(p["sizing"]["allocation_pct"],
                             s["max_allocation_pct"])

    def test_the_flag_is_carried_beside_the_status_not_instead_of_it(self):
        # A row is routinely both capped AND overridden; one field cannot
        # hold both facts.
        s = _settings(max_allocation_pct=1)
        p = PS.assess(self._elite(), s)
        self.assertTrue(p["quality_override"])
        self.assertEqual(p["status"], "OVERSIZED")


class TestPortfolioSummary(unittest.TestCase):
    def _sized(self, ticker, sector, value, risk, rr):
        return {"ticker": ticker, "sector": sector, "sizing_plan": {
            "ok": True, "sizing": {"position_value": value, "actual_risk": risk,
                                   "allocation_pct": value / 1000.0},
            "target": {"rr": rr}}}

    def test_aggregates_only_sizeable_rows(self):
        rows = [self._sized("A", "Tech", 20_000, 1_000, 3.0),
                self._sized("B", "Tech", 10_000, 500, 2.0),
                {"ticker": "C", "sector": "Tech",
                 "sizing_plan": {"ok": False}}]
        out = PS.portfolio_summary(rows, _settings())
        self.assertEqual(out["n_actionable"], 2)
        self.assertEqual(out["planned_capital"], 30_000)
        self.assertEqual(out["planned_risk"], 1_500)
        self.assertEqual(out["avg_rr"], 2.5)
        self.assertEqual(out["max_allocation"]["ticker"], "A")
        self.assertEqual(out["max_risk"]["ticker"], "A")

    def test_sector_concentration(self):
        rows = [self._sized("A", "Tech", 20_000, 1_000, 3.0),
                self._sized("B", "Energy", 5_000, 500, 2.0)]
        out = PS.portfolio_summary(rows, _settings())
        self.assertEqual(out["top_sector"], "Tech")
        self.assertEqual(out["top_sector_pct"], 20.0)

    def test_empty_view_is_zeroed_not_absent(self):
        out = PS.portfolio_summary([], _settings())
        self.assertEqual(out["n_actionable"], 0)
        self.assertEqual(out["planned_risk"], 0.0)
        self.assertIsNone(out["avg_rr"])


class TestScreenableFields(unittest.TestCase):
    """The sizing layer is filterable — LQuality, Technical, Investment and
    R:R are the four independent readings a rule combines, and TSM (91 / 79 /
    CORE / 9R) is the shape the "3R Setup in Quality" preset is built for."""

    def _tsm(self, sized=True):
        """TSM's shape: LQuality 91, Technical 79, CORE, and a 5R target."""
        r = _result(
            ticker="TSM",
            quality={"score": 91, "tier": "Elite"},
            technical={"score": 79, "label": "Good"},
            investment={"score": 88, "status": "CORE"},
            pullback={"zone": "EMA", "candidates": [
                {"name": "50 MA", "zone": "50MA", "price": 92.0}]},
            targets={"stop": 92.0, "stop_name": "50 MA",
                     "ladder": [{"price": 140.0, "name": "52W high"}]})
        if sized:
            PS.attach([r], {}, _settings())
        return r

    def test_flatten_exposes_the_four_readings(self):
        from stockanalysis.core.longterm import screen as LS
        flat = LS.flatten(self._tsm())
        self.assertEqual(flat["lquality"], 91)
        self.assertEqual(flat["technical"], 79)
        self.assertEqual(flat["investment_status"], "CORE")
        self.assertEqual(flat["rr"], 5.0)          # (140-100)/8
        for key in ("lquality", "technical", "investment_status",
                    "investment_score", "rr", "position_grade",
                    "risk_status", "allocation_pct", "stop_pct"):
            self.assertIn(key, flat, key)
            self.assertIn(key, LS.LONGTERM_FIELD_BY_KEY, f"{key} not screenable")

    def test_position_fields_are_none_without_a_sizing_plan(self):
        # screen.py is core and must not assume api.longterm() ran. Every
        # position field flattens to None rather than raising.
        from stockanalysis.core.longterm import screen as LS
        flat = LS.flatten(self._tsm(sized=False))
        for key in ("rr", "position_grade", "risk_status", "allocation_pct"):
            self.assertIsNone(flat[key], key)
        # ...while the readings that come straight off the engine survive.
        self.assertEqual(flat["lquality"], 91)
        self.assertEqual(flat["technical"], 79)

    def test_the_tsm_rule_selects_on_all_three(self):
        from stockanalysis.core.longterm import screen as LS
        rows = [self._tsm()]
        rules = LS.preset_rules("three_r_quality")
        self.assertEqual(rules, ["lquality:gte:80", "technical:gte:75",
                                 "rr:gte:3"])
        kept, _c, _s = LS.apply_rules(rows, rules, "AND")
        self.assertEqual([r["ticker"] for r in kept], ["TSM"])
        # each condition must be able to reject on its own, or the preset is
        # really a one-field screen wearing three
        for i in range(len(rules)):
            tightened = list(rules)
            tightened[i] = tightened[i].rsplit(":", 1)[0] + ":999999"
            kept, _c, _s = LS.apply_rules(rows, tightened, "AND")
            self.assertEqual(kept, [], f"{rules[i]} does no work")

    def test_every_new_preset_parses_and_is_grouped(self):
        from stockanalysis.core.longterm import screen as LS
        for key in ("three_r_quality", "both_engines_agree",
                    "asymmetric_sized", "chart_disagrees",
                    "conviction_no_trade"):
            preset = LS.PRESET_BY_KEY[key]
            self.assertIn(preset["group"], LS.PRESET_GROUPS, key)
            for text in preset["rules"]:
                cond = LS.parse_rule(text)
                self.assertIsNotNone(cond, f"{key}: {text} does not parse")
                self.assertIn(cond.field, LS.LONGTERM_FIELD_BY_KEY, text)

    def test_the_position_group_is_offered_by_the_rule_builder(self):
        from stockanalysis.core.longterm import screen as LS
        self.assertIn("Position", LS.FIELD_GROUPS)
        groups = {f.group for f in LS.LONGTERM_FIELDS}
        self.assertTrue(groups.issubset(set(LS.FIELD_GROUPS)),
                        f"ungrouped: {groups - set(LS.FIELD_GROUPS)}")


if __name__ == "__main__":
    unittest.main()
