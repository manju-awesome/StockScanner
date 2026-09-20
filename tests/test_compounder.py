"""
Tests for core/compounder — the Future Compounder / Emerging Leader engine.

The invariants defended here are the ones that, if they broke, would turn
this engine back into an ordinary growth screen without anybody noticing —
because the score would still be a plausible-looking number.

  1. ACCELERATION IS LIKE-FOR-LIKE. A year-on-year rate is never compared
     against a multi-year CAGR. Doing so stamps every early hyper-growth
     company "decelerating" — the exact population the engine exists to
     find — and the bias is invisible in the output.

  2. ACCELERATION OUTWEIGHS LEVEL. A company bending 12% → 34% must score
     above one fading 60% → 45%. This is the brief's central instruction
     and the one a later "improvement" is most likely to undo.

  3. NOTHING IS REJECTED FOR THE LISTED CONDITIONS. Negative FCF, no
     profit, no coverage, tiny size, low institutional ownership — each is
     classified as a risk and the company still scores.

  4. MISSING IS NOT ZERO. An unmeasurable factor renormalises out; it never
     scores 0, which would read as "bad" rather than "unknown".

  5. LOW-CONFIDENCE TAM IS CAPPED. A speculative market's enormous CAGR
     must not outrank a real market's measured one.

  6. LOW PENETRATION SCORES ABOVE HIGH. The inversion that separates room
     to grow from a market already won.

  7. GRADUATES ARE LABELLED, NOT DROPPED. A company that compounded out of
     the market-cap band is the engine's success case.

Run with: python -m unittest tests.test_compounder
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.compounder import discovery as DISC    # noqa: E402
from stockanalysis.core.compounder import engine as CE         # noqa: E402
from stockanalysis.core.compounder import growth as GROW       # noqa: E402
from stockanalysis.core.compounder import leverage as LEV      # noqa: E402
from stockanalysis.core.compounder import position as POS      # noqa: E402
from stockanalysis.core.compounder import reinvestment as REINV  # noqa: E402
from stockanalysis.core.compounder import stage as STAGE       # noqa: E402
from stockanalysis.core.compounder import survivability as SURV  # noqa: E402
from stockanalysis.core.compounder import themes as TH         # noqa: E402


def company(**over):
    """A measurement dict shaped like fetch.fetch() output. Newest first."""
    base = {
        "ticker": "TEST", "name": "Test Co", "sector": "Technology",
        "market_cap": 4e9, "employees": 500, "price": 40.0,
        "revenue_annual": [400e6, 300e6, 220e6, 170e6],
        "revenue_quarterly": [120e6, 108e6, 96e6, 84e6, 80e6],
        "gross_annual": [200e6, 141e6, 99e6, 71e6],
        "operating_annual": [40e6, 18e6, 4e6, -8e6],
        "opex_annual": [160e6, 123e6, 95e6, 79e6],
        "rnd_annual": [70e6, 55e6, 44e6, 38e6],
        "net_income_annual": [30e6, 12e6, 1e6, -10e6],
        "fcf_annual": [25e6, 5e6, -12e6, -20e6],
        "ocf_annual": [45e6, 20e6, 2e6, -8e6],
        "capex_annual": [-20e6, -15e6, -14e6, -12e6],
        "shares_annual": [100e6, 97e6, 94e6, 90e6],
        "cash": 300e6, "debt": 50e6, "equity": 500e6,
        "inst_own": 45.0, "insider_own": 8.0, "analysts": 8.0,
        "analyst_trend": 2.0, "fwd_rev_growth_cy": 30.0,
        "fwd_rev_growth_ny": 25.0, "fwd_estimate_analysts": 8,
        "insider_buys": 2, "insider_sells": 3, "insider_net_value": 500000.0,
        "rs_3m": 12.0, "rs_12m": 30.0, "dist_52w_high": -8.0,
        "vol_expansion": 1.3, "above_200ma": True,
        "quarter_dates": [], "fiscal_dates": [], "errors": [],
        "gross_quarterly": [], "rnd_quarterly": [], "operating_quarterly": [],
        "shares_outstanding": 100e6, "industry": "Semiconductors",
    }
    base.update(over)
    return base


# ─────────────────────────────────────────────────────────────────────────
# 1 + 2 — acceleration
# ─────────────────────────────────────────────────────────────────────────

class Acceleration(unittest.TestCase):

    def test_never_compares_a_spot_rate_to_a_multi_year_cagr(self):
        """A company off a small base compounds at a rate no spot quarter can
        match. Comparing the two would stamp it 'decelerating' while it grows
        at triple digits — the bug this engine was explicitly built to avoid.
        """
        # $20M -> $850M in three years: a ~155% CAGR against 104% latest.
        rev = [850e6, 415e6, 90e6, 20e6]
        g = GROW.compute(company(revenue_annual=rev, revenue_quarterly=[]))
        self.assertIsNotNone(g["cagr_3y"])
        self.assertGreater(g["cagr_3y"], 120)
        # The comparison must be against the PRIOR YEAR's growth rate...
        self.assertEqual(g["accel_basis"], "annual")
        self.assertAlmostEqual(g["accel_against"], g["prior_annual_yoy"], 1)
        # ...and never against the CAGR.
        self.assertNotAlmostEqual(g["accel_against"], g["cagr_3y"], 1)

    def test_quarterly_basis_compares_yoy_against_yoy(self):
        g = GROW.compute(company())
        self.assertEqual(g["accel_basis"], "quarterly")
        self.assertEqual(g["accel_against"], g["annual_yoy"])
        self.assertAlmostEqual(
            g["accel_pp"], g["quarter_yoy"] - g["annual_yoy"], 1)

    def test_acceleration_outweighs_level(self):
        """The brief's central instruction: reward the bend, not the rate."""
        bending_up = GROW.compute(company(
            revenue_annual=[134e6, 100e6, 89e6, 80e6],   # 12% -> 34%
            revenue_quarterly=[]))
        fading = GROW.compute(company(
            revenue_annual=[290e6, 200e6, 125e6, 78e6],  # 60% -> 45%
            revenue_quarterly=[]))
        self.assertEqual(bending_up["state"], "ACCELERATING")
        self.assertGreater(
            bending_up["score"], fading["score"],
            "a company bending upward must outrank a faster one fading")

    def test_fade_from_a_high_base_is_named_separately(self):
        """240% -> 105% and 14% -> 4% are the same sign and different
        findings. The engine must not file them together."""
        fast = GROW.compute(company(
            revenue_annual=[820e6, 400e6, 118e6, 40e6], revenue_quarterly=[]))
        slow = GROW.compute(company(
            revenue_annual=[104e6, 100e6, 88e6, 80e6], revenue_quarterly=[]))
        self.assertEqual(fast["state"], "FADING FROM A HIGH BASE")
        self.assertTrue(fast["expected_fade"])
        self.assertEqual(slow["state"], "DECELERATING")
        self.assertFalse(slow["expected_fade"])

    def test_unmeasurable_acceleration_is_none_not_flat(self):
        """One data point cannot prove growth is flat."""
        g = GROW.compute(company(revenue_annual=[400e6],
                                 revenue_quarterly=[]))
        self.assertIsNone(g["accel_pp"])
        self.assertEqual(g["state"], "UNMEASURED")

    def test_forward_estimates_never_enter_the_score(self):
        """Consensus is an opinion. Changing it must not move the score."""
        bullish = GROW.compute(company(fwd_rev_growth_cy=10.0,
                                       fwd_rev_growth_ny=90.0))
        bearish = GROW.compute(company(fwd_rev_growth_cy=10.0,
                                       fwd_rev_growth_ny=-40.0))
        self.assertEqual(bullish["score"], bearish["score"])
        self.assertNotEqual(bullish["fwd_accel_pp"], bearish["fwd_accel_pp"])

    def test_cagr_refuses_to_cross_zero(self):
        """-$4M to $60M is a change of state, not a growth rate."""
        g = GROW.compute(company(revenue_annual=[60e6, 20e6, 2e6, -4e6],
                                 revenue_quarterly=[]))
        self.assertIsNone(g["cagr_3y"])


# ─────────────────────────────────────────────────────────────────────────
# 3 — classify, never reject
# ─────────────────────────────────────────────────────────────────────────

class ClassifyNotReject(unittest.TestCase):

    def test_cash_burning_unprofitable_uncovered_company_still_scores(self):
        r = CE.evaluate(company(
            fcf_annual=[-60e6, -45e6, -30e6, -20e6],
            ocf_annual=[-40e6, -30e6, -18e6, -10e6],
            operating_annual=[-70e6, -50e6, -35e6, -25e6],
            net_income_annual=[-65e6, -48e6, -33e6, -24e6],
            analysts=1.0, inst_own=8.0, market_cap=420e6))
        self.assertIsNotNone(r["score"])
        flags = {f["flag"] for f in r["risk_flags"]}
        self.assertIn("Negative free cash flow", flags)
        self.assertIn("Low institutional ownership", flags)
        self.assertIn("Minimal analyst coverage", flags)

    def test_every_rejectable_condition_appears_as_a_classified_risk(self):
        """The brief lists six conditions that must not cause rejection.
        Each must instead be visible on the card."""
        r = CE.evaluate(company(
            fcf_annual=[-60e6, -45e6, -30e6, -20e6],
            analysts=0.0, inst_own=3.0, market_cap=310e6))
        self.assertGreater(len(r["risk_flags"]), 0)
        for fl in r["risk_flags"]:
            self.assertIn(fl["level"], ("MATERIAL", "CLASSIFIED", "DATA"))
            self.assertTrue(fl.get("detail"),
                            f"{fl['flag']} carries no explanation")

    def test_no_valuation_term_exists_in_the_composite(self):
        """Valuation is deliberately absent — see engine.py. If a leg named
        for it ever appears, the engine has silently become a different one.
        """
        names = {n for n, _w in CE.WEIGHTS}
        self.assertNotIn("Valuation", names)
        for name in names:
            self.assertNotIn("valuation", name.lower())

    def test_weights_match_the_framework(self):
        self.assertEqual(sum(w for _n, w in CE.WEIGHTS), 100)
        expected = {"Secular TAM": 20, "Growth acceleration": 15,
                    "Moat formation": 15, "Market-share opportunity": 10,
                    "Operating leverage": 10, "Reinvestment / R&D": 10,
                    "Competitive position": 5, "Management": 5,
                    "Financial survivability": 5, "Market discovery": 5}
        self.assertEqual(dict(CE.WEIGHTS), expected)


# ─────────────────────────────────────────────────────────────────────────
# 4 — missing is not zero
# ─────────────────────────────────────────────────────────────────────────

class MissingIsNotZero(unittest.TestCase):

    def test_unmeasured_factor_renormalises_rather_than_scoring_zero(self):
        full = CE.evaluate(company())
        # Strip every R&D and capex input: the reinvestment leg becomes
        # unmeasurable. It must drop OUT of the blend, not enter it as 0.
        thin = CE.evaluate(company(rnd_annual=[], capex_annual=[],
                                   employees=None))
        self.assertLess(thin["coverage"], full["coverage"])
        legs = {c["name"] for c in thin["components"]}
        if "Reinvestment / R&D" not in legs:
            self.assertIn("Reinvestment / R&D", thin["missing"])

    def test_a_company_with_nothing_measurable_scores_none_not_zero(self):
        empty = CE.evaluate({"ticker": "NADA", "errors": []})
        self.assertIsNone(empty["score"])
        self.assertEqual(empty["tier"], "UNSCORED")

    def test_coverage_travels_with_every_score(self):
        r = CE.evaluate(company())
        self.assertIsNotNone(r["coverage"])
        self.assertIn(r["confidence"]["level"],
                      ("HIGH", "MEDIUM", "LOW", "NONE"))

    def test_watchlist_excludes_thin_coverage_but_reports_it(self):
        """A thin-coverage name is held off the list and NAMED — never
        silently dropped."""
        good = CE.evaluate(company(ticker="GOOD"))
        thin = dict(good, ticker="THIN", coverage=0.3, score=99)
        wl = CE.watchlist([good, thin])
        self.assertNotIn("THIN", [r["ticker"] for r in wl["rows"]])
        self.assertIn("THIN", [e["ticker"] for e in wl["excluded_thin"]])


# ─────────────────────────────────────────────────────────────────────────
# 5 — TAM confidence
# ─────────────────────────────────────────────────────────────────────────

class TamConfidence(unittest.TestCase):

    def test_low_confidence_theme_is_capped(self):
        quantum = TH.theme("quantum")
        sec = POS.secular(quantum)
        self.assertEqual(quantum["confidence"], "LOW")
        self.assertLessEqual(sec["score"], TH.CONFIDENCE_CEILING["LOW"])

    def test_a_speculative_market_cannot_outrank_a_measured_one(self):
        """Quantum's TAM CAGR beats every other theme arithmetically. The
        cap is what stops that becoming the top secular score."""
        quantum = POS.secular(TH.theme("quantum"))
        semicap = POS.secular(TH.theme("semicap"))
        self.assertGreater(TH.theme("quantum")["tam_cagr_5y"],
                           TH.theme("semicap")["tam_cagr_5y"])
        self.assertLess(quantum["score"], semicap["score"])

    def test_unmapped_ticker_reports_no_theme_rather_than_scoring_zero(self):
        sec = POS.secular(None)
        self.assertIsNone(sec["score"])
        self.assertIn("not mapped", sec["detail"])

    def test_every_theme_has_a_source_basis_and_as_of_date(self):
        for key, rec in TH.THEMES.items():
            self.assertTrue(rec.get("basis"), f"{key} has no source basis")
            self.assertTrue(rec.get("as_of"), f"{key} has no as-of date")
            self.assertTrue(rec.get("risk"), f"{key} names no risk")
            self.assertIn(rec.get("confidence"), ("HIGH", "MEDIUM", "LOW"))

    def test_every_universe_ticker_maps_to_a_real_theme(self):
        for ticker, key in TH.THEME_MEMBERS.items():
            self.assertIn(key, TH.THEMES, f"{ticker} maps to unknown {key}")


# ─────────────────────────────────────────────────────────────────────────
# 6 — low penetration is an asset
# ─────────────────────────────────────────────────────────────────────────

class PenetrationInversion(unittest.TestCase):

    def test_low_share_of_a_big_market_beats_high_share_of_a_small_one(self):
        theme = TH.theme("cybersecurity")
        g = {"latest": 30.0}
        tiny = POS.opportunity(theme, 200e6, g)      # ~0.09% of $225B
        large = POS.opportunity(theme, 40e9, g)      # ~17.8%
        self.assertLess(tiny["share_pct"], large["share_pct"])
        self.assertGreater(
            tiny["score"], large["score"],
            "room to grow must outrank a market already taken")

    def test_share_carries_its_own_caveat(self):
        opp = POS.opportunity(TH.theme("semicap"), 500e6, {"latest": 20.0})
        self.assertIn("part of the theme", opp["caveat"])


# ─────────────────────────────────────────────────────────────────────────
# 7 — cap band
# ─────────────────────────────────────────────────────────────────────────

class CapBand(unittest.TestCase):

    def test_graduate_is_labelled_not_dropped(self):
        band, note = CE.cap_band(55e9)
        self.assertEqual(band, "GRADUATED")
        self.assertIn("success case", note)

    def test_band_edges(self):
        self.assertEqual(CE.cap_band(299e6)[0], "BELOW BAND")
        self.assertEqual(CE.cap_band(300e6)[0], "IN BAND")
        self.assertEqual(CE.cap_band(20e9)[0], "IN BAND")
        self.assertEqual(CE.cap_band(20.1e9)[0], "GRADUATED")
        self.assertEqual(CE.cap_band(None)[0], "UNKNOWN")

    def test_a_graduated_company_still_scores(self):
        r = CE.evaluate(company(market_cap=55e9))
        self.assertIsNotNone(r["score"])


# ─────────────────────────────────────────────────────────────────────────
# Operating leverage and reinvestment
# ─────────────────────────────────────────────────────────────────────────

class OperatingLeverage(unittest.TestCase):

    def test_revenue_outgrowing_costs_scores_above_the_reverse(self):
        levered = LEV.compute(company())                     # opex grows slower
        bought = LEV.compute(company(opex_annual=[420e6, 250e6, 150e6, 79e6]))
        self.assertGreater(levered["leverage_ratio"], 1.0)
        self.assertLess(bought["leverage_ratio"], 1.0)
        self.assertGreater(levered["score"], bought["score"])

    def test_shrinking_cost_base_does_not_flip_the_ratio_negative(self):
        """A negative denominator would score the best case as the worst."""
        lev = LEV.compute(company(opex_annual=[70e6, 75e6, 78e6, 79e6]))
        self.assertIsNotNone(lev["leverage_ratio"])
        self.assertGreaterEqual(lev["leverage_ratio"], 1.0)

    def test_fcf_states_distinguish_improving_from_widening_burn(self):
        improving = LEV.compute(company(
            fcf_annual=[-10e6, -30e6, -55e6, -80e6]))
        widening = LEV.compute(company(
            fcf_annual=[-80e6, -55e6, -30e6, -10e6]))
        self.assertEqual(improving["fcf_state"], "IMPROVING")
        self.assertEqual(widening["fcf_state"], "WIDENING")

    def test_crossing_into_positive_is_flagged_as_inflected(self):
        lev = LEV.compute(company(fcf_annual=[15e6, -5e6, -20e6, -30e6]))
        self.assertEqual(lev["fcf_state"], "INFLECTED")


class Reinvestment(unittest.TestCase):

    def test_spending_that_outruns_revenue_scores_below_spending_that_does_not(self):
        productive = REINV.compute(company())
        wasteful = REINV.compute(company(
            rnd_annual=[220e6, 120e6, 70e6, 38e6]))       # R&D 5.8x, rev 2.4x
        self.assertGreater(productive["rnd_productivity"],
                           wasteful["rnd_productivity"])
        self.assertGreater(productive["score"], wasteful["score"])
        self.assertFalse(wasteful["productive"])

    def test_naked_intensity_does_not_beat_productivity(self):
        """A pre-revenue company spending 300% of revenue on R&D must not top
        the reinvestment leg."""
        sane = REINV.compute(company())
        burner = REINV.compute(company(
            revenue_annual=[10e6, 8e6, 6e6, 5e6],
            rnd_annual=[30e6, 18e6, 10e6, 5e6]))
        self.assertGreater(burner["rnd_pct"], sane["rnd_pct"])
        self.assertGreater(sane["score"], burner["score"])


# ─────────────────────────────────────────────────────────────────────────
# Survivability, discovery, stage
# ─────────────────────────────────────────────────────────────────────────

class Survivability(unittest.TestCase):

    def test_all_five_classes_are_reachable(self):
        self.assertEqual(
            SURV.compute(company(), LEV.compute(company()))["classification"],
            "SELF-FUNDED")

        short = company(cash=30e6, fcf_annual=[-40e6, -35e6, -30e6, -25e6])
        self.assertEqual(
            SURV.compute(short, LEV.compute(short))["classification"],
            "DISTRESSED")

        dilute = company(cash=70e6, fcf_annual=[-40e6, -35e6, -30e6, -25e6])
        self.assertEqual(
            SURV.compute(dilute, LEV.compute(dilute))["classification"],
            "HIGH DILUTION RISK")

        near = company(cash=800e6, fcf_annual=[-40e6, -60e6, -80e6, -90e6])
        self.assertEqual(
            SURV.compute(near, LEV.compute(near))["classification"],
            "NEAR SELF-FUNDED")

        dependent = company(cash=300e6,
                            fcf_annual=[-90e6, -60e6, -40e6, -25e6])
        self.assertEqual(
            SURV.compute(dependent, LEV.compute(dependent))["classification"],
            "CAPITAL DEPENDENT")

    def test_runway_is_reported_in_years(self):
        d = company(cash=200e6, fcf_annual=[-50e6, -40e6, -30e6, -20e6])
        s = SURV.compute(d, LEV.compute(d))
        self.assertAlmostEqual(s["runway_years"], 4.0, 1)

    def test_dilution_is_measured_from_share_counts_not_inferred(self):
        heavy = company(shares_annual=[180e6, 140e6, 110e6, 90e6])
        s = SURV.compute(heavy, LEV.compute(heavy))
        self.assertGreater(s["dilution_pct_yr"], 18)
        self.assertEqual(s["dilution_risk"]["level"], "SEVERE")

    def test_a_self_funded_company_can_still_be_diluting(self):
        d = company(shares_annual=[140e6, 122e6, 106e6, 90e6])
        s = SURV.compute(d, LEV.compute(d))
        self.assertEqual(s["classification"], "SELF-FUNDED")
        self.assertIn(s["dilution_risk"]["level"], ("HIGH", "SEVERE"))


class Discovery(unittest.TestCase):

    def test_coverage_is_a_plateau_not_a_ladder(self):
        """35 analysts must not outscore 8 — the brief wants EARLY
        discovery, and more coverage past a point is a worse setup."""
        early = DISC.compute(company(analysts=8.0, inst_own=40.0))
        crowded = DISC.compute(company(analysts=35.0, inst_own=92.0))
        self.assertGreater(early["score"], crowded["score"])
        self.assertEqual(crowded["state"], "CROWDED")
        self.assertEqual(early["state"], "EARLY DISCOVERY")

    def test_low_coverage_is_not_a_zero(self):
        dark = DISC.compute(company(analysts=0.0, inst_own=4.0))
        self.assertEqual(dark["state"], "UNDISCOVERED")
        self.assertIsNotNone(dark["score"])

    def test_momentum_is_not_required(self):
        """A falling stock must still be scorable — the engine looks for
        companies BEFORE recognition."""
        weak = DISC.compute(company(rs_12m=-30.0, rs_3m=-15.0))
        self.assertIsNotNone(weak["score"])


class Stage(unittest.TestCase):

    def _stage(self, data):
        g = GROW.compute(data)
        lv = LEV.compute(data)
        sv = SURV.compute(data, lv)
        cp = POS.competitive(dict(data, ttm_revenue=g["ttm_revenue"]),
                             None, g, {"score": 60}, None)
        return STAGE.classify(data, g, lv, sv, cp)

    def test_tiny_fast_company_is_stage_one(self):
        st = self._stage(company(
            revenue_annual=[12e6, 6e6, 3e6, 1e6], revenue_quarterly=[]))
        self.assertEqual(st["stage"], 1)

    def test_large_slow_company_is_mature_not_leader(self):
        st = self._stage(company(
            revenue_annual=[4.2e9, 4.0e9, 3.9e9, 3.7e9],
            revenue_quarterly=[]))
        self.assertEqual(st["stage"], 5)

    def test_stage_is_not_a_band_on_the_score(self):
        """A Stage 2 company must be able to outscore a Stage 4 one — if
        stage were a band on the composite it could not."""
        early = CE.evaluate(company(
            ticker="ALAB",                                   # mapped theme
            revenue_annual=[120e6, 70e6, 40e6, 25e6],
            revenue_quarterly=[]))
        late = CE.evaluate(company(
            ticker="AMKR",
            revenue_annual=[3.1e9, 3.0e9, 2.95e9, 2.9e9],
            revenue_quarterly=[]))
        self.assertLessEqual(early["stage"]["stage"], 2)
        self.assertGreaterEqual(late["stage"]["stage"], 4)
        self.assertGreater(early["score"], late["score"])

    def test_transition_conditions_name_live_values(self):
        st = self._stage(company(
            revenue_annual=[12e6, 6e6, 3e6, 1e6], revenue_quarterly=[]))
        conds = st["next_stage"]["conditions"]
        self.assertTrue(conds)
        for c in conds:
            self.assertIn("current", c)
            self.assertIn("needed", c)
            self.assertNotEqual(c["current"], "")


# ─────────────────────────────────────────────────────────────────────────
# The assembled result
# ─────────────────────────────────────────────────────────────────────────

class Assembled(unittest.TestCase):

    def test_every_narrative_block_is_present(self):
        r = CE.evaluate(company(ticker="ALAB"))
        nar = r["narrative"]
        for key in ("why_major", "what_has_to_go_right", "what_destroys_it",
                    "biggest_competitor", "catalysts", "metrics_to_monitor",
                    "current_stage", "stage_transition", "invalidation"):
            self.assertIn(key, nar)
            self.assertTrue(nar[key], f"{key} came back empty")

    def test_metrics_to_monitor_carry_current_values(self):
        """A monitoring list without current values is a list of words."""
        r = CE.evaluate(company(ticker="ALAB"))
        for m in r["narrative"]["metrics_to_monitor"]:
            self.assertIn("now", m)
            self.assertIn("watch_for", m)
            self.assertTrue(str(m["now"]))

    def test_result_carries_every_requested_output_field(self):
        r = CE.evaluate(company(ticker="ALAB"))
        self.assertIsNotNone(r["ticker"])
        self.assertIsNotNone(r["market_cap"])
        self.assertIsNotNone(r["theme_label"])
        self.assertIsNotNone(r["stage"]["label"])
        self.assertIsNotNone(r["opportunity"]["tam_now"])
        self.assertIsNotNone(r["growth"]["cagr_3y"])
        self.assertIsNotNone(r["leverage"]["gross_margin_now"])
        self.assertIsNotNone(r["moat"]["score"])
        self.assertIsNotNone(r["survivability"]["classification"])
        self.assertIsNotNone(r["management"]["score"])
        self.assertIsNotNone(r["discovery"]["score"])
        self.assertIsNotNone(r["score"])

    def test_evaluate_is_pure_and_needs_no_network(self):
        """Called twice on the same dict it must return the same score."""
        d = company(ticker="ALAB")
        self.assertEqual(CE.evaluate(d)["score"], CE.evaluate(d)["score"])

    def test_watchlist_ranks_on_score_alone(self):
        rows = []
        for i, sc in enumerate((90, 55, 72, 61)):
            r = CE.evaluate(company(ticker=f"T{i}"))
            rows.append(dict(r, score=sc, coverage=0.9))
        wl = CE.watchlist(rows)
        self.assertEqual([r["score"] for r in wl["rows"]], [90, 72, 61, 55])

    def test_mature_companies_are_kept_off_the_ten_year_list(self):
        """A Stage 5 company scoring 68 must not take a watchlist slot — the
        framework's own definition of MATURE is that the compounding is
        behind it. It stays in the full ranking and is named as excluded."""
        early = dict(CE.evaluate(company(ticker="EARLY")),
                     score=60, coverage=0.9, stage={"stage": 2,
                                                    "label": "VALIDATION",
                                                    "why": "x"})
        old = dict(CE.evaluate(company(ticker="OLD")),
                   score=80, coverage=0.9, stage={"stage": 5,
                                                  "label": "MATURE",
                                                  "why": "converged"})
        wl = CE.watchlist([early, old])
        self.assertEqual([r["ticker"] for r in wl["rows"]], ["EARLY"])
        self.assertIn("OLD", [e["ticker"] for e in wl["excluded_mature"]])

    def test_stage_breaks_ties_but_never_overrides_score(self):
        """§11 prioritises early stages; §12 ranks by score. Score wins on
        any real difference; stage decides only a tie."""
        def mk(t, score, stage):
            return dict(CE.evaluate(company(ticker=t)), score=score,
                        coverage=0.9,
                        stage={"stage": stage, "label": "X", "why": ""})

        # A tie: the earlier stage must come first.
        wl = CE.watchlist([mk("LATE", 70, 4), mk("EARLY", 70, 1)])
        self.assertEqual([r["ticker"] for r in wl["rows"]],
                         ["EARLY", "LATE"])
        # A real score gap: the later stage still wins.
        wl2 = CE.watchlist([mk("LATE", 80, 4), mk("EARLY", 70, 1)])
        self.assertEqual([r["ticker"] for r in wl2["rows"]],
                         ["LATE", "EARLY"])

    def test_theme_concentration_is_reported_not_corrected(self):
        rows = [dict(CE.evaluate(company(ticker="ALAB")),
                     score=90 - i, coverage=0.9) for i in range(10)]
        wl = CE.watchlist(rows)
        self.assertEqual(len(wl["rows"]), 10)        # nothing was capped out
        self.assertIsNotNone(wl["concentration_note"])


class Snapshot(unittest.TestCase):
    """The store must survive what this engine legitimately produces.

    A self-funded company's runway is genuinely infinite, and `Infinity` is
    not valid JSON — json.dumps writes it, and a strict reader rejects the
    file. That is a whole-page failure caused by a correct measurement, so
    it is pinned here.
    """

    def setUp(self):
        import tempfile
        from stockanalysis.core.compounder import store as CS
        self.CS = CS
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        # Never write to the real data directory from a test.
        self._orig = CS._SNAPSHOT
        CS._SNAPSHOT = Path(self._dir.name) / "snap.json"
        CS._DIR = Path(self._dir.name)
        self.addCleanup(setattr, CS, "_SNAPSHOT", self._orig)

    def test_infinite_runway_round_trips(self):
        import json
        d = company(fcf_annual=[25e6, 20e6, 15e6, 10e6])
        surv = SURV.compute(d, LEV.compute(d))
        self.assertEqual(surv["runway_years"], float("inf"))

        r = CE.evaluate(d)
        self.CS.save([r], CE.watchlist([r]))
        raw = self.CS._SNAPSHOT.read_text()
        self.assertNotIn("Infinity", raw)
        json.loads(raw)                        # strict parse must succeed
        self.assertIsNotNone(self.CS.load())

    def test_merge_does_not_delete_untouched_rows(self):
        """A six-name refresh must not drop the other hundred and twenty."""
        a = CE.evaluate(company(ticker="AAA"))
        b = CE.evaluate(company(ticker="BBB"))
        self.CS.save([a, b], None)
        c = CE.evaluate(company(ticker="CCC"))
        self.CS.save([c], None, merge=True)
        stored = {r["ticker"] for r in self.CS.load()["rows"]}
        self.assertEqual(stored, {"AAA", "BBB", "CCC"})

    def test_merged_row_replaces_rather_than_duplicates(self):
        a = CE.evaluate(company(ticker="AAA"))
        self.CS.save([a, CE.evaluate(company(ticker="BBB"))], None)
        self.CS.save([dict(a, score=11)], None, merge=True)
        rows = self.CS.load()["rows"]
        self.assertEqual(len([r for r in rows if r["ticker"] == "AAA"]), 1)
        self.assertEqual(
            next(r for r in rows if r["ticker"] == "AAA")["score"], 11)

    def test_age_note_says_what_decayed_not_just_how_old(self):
        import datetime as dt
        old = {"generated_at": (dt.datetime.now()
                                - dt.timedelta(days=35)).isoformat()}
        status, note = self.CS.age_note(old)
        self.assertEqual(status, "aging")
        self.assertIn("as filed", note)
        self.assertEqual(self.CS.age_note(None)[0], "stale")


class SortAndFilterMarkup(unittest.TestCase):
    """The table sorts and filters client-side, so the correctness lives in
    the emitted attributes rather than in Python. What must hold:

      1. Sort values are the RAW numbers. "$48.47B" and "$310M" sort
         backwards as strings, and a "—" would sort between them.
      2. An unmeasured value emits an EMPTY attribute, never a 0 — the
         sorter uses emptiness to push it to the bottom in both directions.
      3. Filter options are built from values PRESENT in the rows, so a
         dropdown never offers a choice that returns nothing.
    """

    def setUp(self):
        from stockanalysis.webapp import compounder_view as V
        self.V = V
        self.rows = [CE.evaluate(company(ticker="ALAB")),
                     CE.evaluate(company(ticker="RKLB", market_cap=310e6))]

    def test_rows_carry_raw_numeric_sort_values(self):
        html = self.V._table(self.rows, detail=False, table_id="t")
        self.assertIn('data-cap="4000000000.0"', html)
        self.assertIn('data-cap="310000000.0"', html)
        # ...and never the formatted text.
        self.assertNotIn('data-cap="$4.00B"', html)

    def test_unmeasured_sorts_as_empty_not_zero(self):
        blind = CE.evaluate(company(ticker="X", revenue_annual=[],
                                    revenue_quarterly=[], gross_annual=[]))
        html = self.V._table([blind], detail=False, table_id="t")
        self.assertIn('data-gm=""', html)
        self.assertNotIn('data-gm="0"', html)

    def test_headers_are_sortable_with_a_declared_type(self):
        html = self.V._table(self.rows, detail=False, table_id="t")
        for key, kind in (("score", "num"), ("ticker", "text"),
                          ("cap", "num"), ("funding", "text")):
            self.assertIn(f"fcSort('t','{key}','{kind}')", html)

    def test_detail_rows_are_tagged_to_their_parent(self):
        """Sorting moves pairs; an untagged panel would end up under the
        wrong company."""
        html = self.V._table(self.rows, detail=True, table_id="t")
        self.assertIn('class="fc-detail" data-for="0"', html)
        self.assertIn('data-row="0"', html)

    def test_filter_options_come_only_from_values_present(self):
        bar = self.V._filter_bar(self.rows, "t")
        fundings = {(r.get("survivability") or {}).get("classification")
                    for r in self.rows}
        for absent in ({"SELF-FUNDED", "DISTRESSED", "HIGH DILUTION RISK"}
                       - fundings):
            self.assertNotIn(f'value="{absent}"', bar)

    def test_single_valued_facet_is_omitted(self):
        """A dropdown with one option filters nothing and is noise."""
        bar = self.V._filter_bar([self.rows[0]] * 3, "t")
        self.assertNotIn('data-attr="theme"', bar)

    def test_facet_counts_sum_to_the_row_count(self):
        rows = self.rows * 3
        vals = self.V._facet_values(rows, "stagelabel")
        self.assertEqual(sum(n for _v, n in vals), len(rows))


if __name__ == "__main__":
    unittest.main()
