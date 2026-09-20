"""
Tests for core/csp — the Cash-Secured Put engine.

No network. Every layer under test is a pure function of its inputs:
greeks from spot/strike/vol, returns from strike/premium/DTE, eligibility
from a long-term result dict, strike selection from a candidate list.
The chain fetch is the only networked part of the package and is not
exercised here — what is exercised is everything that decides a trade.

The invariants these defend:

  1. A rejected COMPANY can never become a trade, whatever the premium.
  2. `happy_to_own` is never quietly assumed — None means "cannot judge"
     and must not be treated as yes.
  3. A missing input reduces score COVERAGE; it never scores as zero,
     because zero is a claim and absent is not.
  4. IV Rank is withheld rather than invented while history is short.
  5. Greeks agree with put-call parity and known analytic values.

Run with: python -m unittest tests.test_csp
"""
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.csp import chain as C          # noqa: E402
from stockanalysis.core.csp import eligibility as EL   # noqa: E402
from stockanalysis.core.csp import greeks as G         # noqa: E402
from stockanalysis.core.csp import premium as PR       # noqa: E402
from stockanalysis.core.csp import score as SC         # noqa: E402
from stockanalysis.core.csp import strike as ST        # noqa: E402
from stockanalysis.core.csp import volatility as V     # noqa: E402


def lt_result(**over):
    """A minimal long-term engine result — high quality, undervalued."""
    base = {
        "ticker": "TEST", "name": "Test Co", "sector": "Technology",
        "price": 100.0, "action": "BUY ON 50 MA", "lt_score": 80,
        "quality": {"score": 88.0, "tier": "High Quality", "tier_icon": "🟢",
                    "coverage": 0.95, "reliable": True, "ownable": True},
        "valuation": {"band": "UNDERVALUED", "acceptable": True,
                      "method": "REVERSE_DCF", "confidence": "HIGH",
                      "headline": "cheap", "fair_value": None,
                      "implied_growth_pct": 5.0, "delivered_growth_pct": 15.0,
                      "growth_gap_pp": -10.0},
        "investment": {"status": "OWN", "why": "worth owning"},
        "trend": {"state": "CONFIRMED"},
        "pullback": {"stage": "STAGE2", "extended": False,
                     "levels": [
                         {"key": "50MA", "zone": "50MA", "name": "50 MA",
                          "price": 92.0, "held": True, "supporting": True},
                         {"key": "200MA", "zone": "200MA", "name": "200 MA",
                          "price": 85.0, "held": True, "supporting": False},
                     ],
                     "buy_zone": {"price": 93.0, "name": "volume shelf",
                                  "touches": 4, "actual_support": True}},
        "confluence": {"hits": [], "score": 60},
        "entries": [{"price": 93.0}],
        "days_to_earnings": 70.0,
        "earnings_risk": {"label": "🟢 Clear", "days": 70.0},
    }
    base.update(over)
    return base


def put_row(strike, bid, ask, oi=1000, vol=100, iv=0.30):
    mid = (bid + ask) / 2
    return {"strike": strike, "bid": bid, "ask": ask, "mid": mid,
            "last": mid, "spread": ask - bid,
            "spread_pct": round((ask - bid) / mid * 100, 1) if mid else None,
            "volume": float(vol), "open_interest": float(oi), "iv": iv,
            "iv_source": "chain", "in_the_money": False,
            "sellable": bid > 0}


# ─────────────────────────────────────────────────────────────────────────
class TestGreeks(unittest.TestCase):

    def test_put_call_parity(self):
        """C - P = S*e^-qT - K*e^-rT. The put price must satisfy it."""
        s, k, t, r, sig = 100.0, 95.0, 0.25, 0.04, 0.30
        p = G.put_greeks(s, k, t, r, sig)["price"]
        d1 = (math.log(s / k) + (r + sig * sig / 2) * t) / (sig * math.sqrt(t))
        d2 = d1 - sig * math.sqrt(t)
        call = s * G.norm_cdf(d1) - k * math.exp(-r * t) * G.norm_cdf(d2)
        self.assertAlmostEqual(call - p, s - k * math.exp(-r * t), places=6)

    def test_atm_put_delta_near_half(self):
        g = G.put_greeks(100.0, 100.0, 0.25, 0.0, 0.20)
        self.assertLess(abs(g["delta"]), 0.55)
        self.assertGreater(abs(g["delta"]), 0.42)

    def test_put_delta_is_negative_and_otm_is_small(self):
        g = G.put_greeks(100.0, 80.0, 30 / 365, 0.04, 0.30)
        self.assertLess(g["delta"], 0)
        self.assertLess(abs(g["delta"]), 0.15)

    def test_degenerate_inputs_return_none_not_a_default(self):
        """Zero vol or expired: every greek is None, never a confident 0."""
        for kw in ({"sigma": 0.0}, {"t": 0.0}, {"s": 0.0}):
            args = {"s": 100.0, "k": 90.0, "t": 0.1, "r": 0.04, "sigma": 0.3}
            args.update(kw)
            g = G.put_greeks(**args)
            self.assertIsNone(g["delta"], kw)
            self.assertIsNone(g["prob_itm"], kw)

    def test_theta_is_per_day_and_negative_for_long_put(self):
        g = G.put_greeks(100.0, 95.0, 0.25, 0.04, 0.30)
        self.assertLess(g["theta"], 0)
        self.assertGreater(g["theta"], -1.0)     # per day, not per year

    def test_prob_above_breakeven_beats_prob_above_strike(self):
        """A CSP profits above BREAKEVEN, which is below the strike, so the
        probability of profit must exceed the probability of finishing
        above the strike."""
        p_strike = G.prob_above(100.0, 90.0, 30 / 365, 0.04, 0.30)
        p_be = G.prob_above(100.0, 88.5, 30 / 365, 0.04, 0.30)
        self.assertGreater(p_be, p_strike)

    def test_implied_vol_roundtrip(self):
        px = G.put_greeks(100.0, 92.0, 0.15, 0.04, 0.37)["price"]
        self.assertAlmostEqual(G.implied_vol(px, 100.0, 92.0, 0.15, 0.04),
                               0.37, places=3)


# ─────────────────────────────────────────────────────────────────────────
class TestChainHelpers(unittest.TestCase):

    def test_zero_bid_is_not_sellable(self):
        self.assertFalse(put_row(90, 0.0, 0.30)["sellable"])
        self.assertEqual(C.liquidity_score(put_row(90, 0.0, 0.30))["score"], 0)

    def test_third_friday_is_monthly(self):
        self.assertTrue(C.is_monthly("2026-09-18"))
        self.assertFalse(C.is_monthly("2026-09-04"))
        self.assertFalse(C.is_monthly("2026-09-25"))

    def test_monthlies_are_preferred_over_nearer_weeklies(self):
        """A weekly at 25 DTE must not beat the monthly at 38 DTE."""
        picked = C.pick_expiries(["2026-09-04", "2026-09-11", "2026-09-18"],
                                 20, 45, limit=1,
                                 today=__import__("datetime").date(2026, 8, 11))
        self.assertEqual(picked, ["2026-09-18"])

    def test_limit_price_sits_below_mid_on_a_wide_spread(self):
        row = put_row(90, 1.00, 1.60)
        limit = C.limit_price(row)
        self.assertLess(limit, row["mid"])
        self.assertGreater(limit, row["bid"])

    def test_limit_price_uses_mid_when_spread_is_tight(self):
        row = put_row(90, 1.98, 2.02)
        self.assertAlmostEqual(C.limit_price(row), 2.00, places=2)

    def test_liquidity_penalises_thin_oi_and_wide_spread(self):
        good = C.liquidity_score(put_row(90, 1.95, 2.00, oi=3000, vol=800))
        bad = C.liquidity_score(put_row(90, 0.05, 0.40, oi=5, vol=0))
        self.assertGreater(good["score"], 85)
        self.assertLess(bad["score"], 30)
        self.assertTrue(good["tradable"])
        self.assertFalse(bad["tradable"])

    def test_wide_spread_is_gated_not_merely_penalised(self):
        """A 13%-wide market on a deep chain must not score respectably:
        under the old additive scoring, strong open interest and volume
        carried it to 64/100."""
        row = put_row(105, 1.60, 1.83, oi=3000, vol=800)
        out = C.liquidity_score(row)
        self.assertEqual(out["spread_verdict"], "REJECT")
        self.assertFalse(out["tradable"])
        self.assertLessEqual(out["score"], 35)

    def test_spread_bands_match_the_specification(self):
        self.assertEqual(C.spread_verdict(4.0)[0], "EXCELLENT")
        self.assertEqual(C.spread_verdict(7.0)[0], "ACCEPTABLE")
        self.assertEqual(C.spread_verdict(9.0)[0], "CAUTION")
        self.assertEqual(C.spread_verdict(13.4)[0], "REJECT")

    def test_closed_market_spread_is_unknown_not_a_failed_gate(self):
        """Overnight quotes widen for reasons that have nothing to do with
        the fill at 10am. `tradable` must be None, never False."""
        row = put_row(105, 1.60, 1.83, oi=3000, vol=800)
        out = C.liquidity_score(row, quotes_live=False)
        self.assertEqual(out["spread_verdict"], "UNKNOWN")
        self.assertIsNone(out["tradable"])
        self.assertTrue(any("market closed" in n for n in out["notes"]))

    def test_year_fraction_positive_through_expiry_day(self):
        import datetime as dt
        t = C.year_fraction("2026-08-11", today=dt.date(2026, 8, 11))
        self.assertIsNotNone(t)
        self.assertGreater(t, 0)


# ─────────────────────────────────────────────────────────────────────────
class TestEligibility(unittest.TestCase):

    def test_quality_undervalued_name_is_approved(self):
        self.assertEqual(EL.classify(lt_result())["status"], "CSP APPROVED")

    def test_low_quality_is_rejected(self):
        r = lt_result(quality={"score": 55.0, "tier": "Reject",
                               "tier_icon": "🔴", "coverage": 0.9,
                               "reliable": True, "ownable": False})
        out = EL.classify(r)
        self.assertEqual(out["status"], "CSP REJECTED")
        self.assertTrue(out["blockers"])

    def test_overvalued_is_rejected(self):
        r = lt_result(valuation={"band": "OVERVALUED", "acceptable": False,
                                 "method": "REVERSE_DCF", "confidence": "HIGH",
                                 "headline": "demands too much growth"})
        self.assertEqual(EL.classify(r)["status"], "CSP REJECTED")

    def test_stage_4_is_rejected_however_cheap(self):
        r = lt_result(pullback={**lt_result()["pullback"], "stage": "STAGE4"})
        out = EL.classify(r)
        self.assertEqual(out["status"], "CSP REJECTED")
        self.assertTrue(any("Stage 4" in b for b in out["blockers"]))

    def test_thin_coverage_is_rejected(self):
        r = lt_result(quality={"score": 90.0, "tier": "Elite",
                               "tier_icon": "💎", "coverage": 0.30,
                               "reliable": False, "ownable": True})
        self.assertEqual(EL.classify(r)["status"], "CSP REJECTED")

    def test_reverse_dcf_reports_growth_basis_not_a_made_up_fair_value(self):
        """The reverse DCF produces no fair-value price. The engine must
        say so rather than back-solving one to fill the column."""
        d = EL.discount_view(lt_result())
        self.assertIsNone(d["fair_value"])
        self.assertEqual(d["basis"], "growth")
        self.assertEqual(d["margin_pct"], 10.0)      # -(-10pp gap)


# ─────────────────────────────────────────────────────────────────────────
class TestReturnsAndAssignment(unittest.TestCase):

    def test_returns_arithmetic(self):
        r = SC.returns(strike=100.0, premium=2.00, dte_days=30)
        self.assertEqual(r["collateral"], 10000.0)
        self.assertEqual(r["premium_total"], 200.0)
        self.assertAlmostEqual(r["yield_pct"], 2.0, places=2)
        self.assertAlmostEqual(r["annualised"], 2.0 * 365 / 30, places=1)
        self.assertEqual(r["breakeven"], 98.0)

    def test_return_on_risk_exceeds_raw_yield(self):
        """Capital at risk is collateral net of the credit received."""
        r = SC.returns(100.0, 2.00, 30)
        self.assertGreater(r["return_on_risk"], r["yield_pct"])

    def test_assignment_passes_when_basis_is_below_support_and_spot(self):
        res = lt_result()
        a = SC.assignment(92.0, 2.0, res, EL.discount_view(res),
                          ST.support_levels(res))
        self.assertTrue(a["happy_to_own"])

    def test_assignment_is_none_when_nothing_can_be_judged(self):
        """No valuation, no levels, no entries: the answer is None, and
        None must not be read as yes."""
        res = lt_result(valuation={}, pullback={"levels": []}, entries=[],
                        price=None)
        a = SC.assignment(92.0, 2.0, res, EL.discount_view(res), [])
        self.assertIsNone(a["happy_to_own"])

    def test_unjudgeable_assignment_forces_a_rejection(self):
        """Invariant 2: anything short of a clear yes is a rejection."""
        info = SC.compute(lt_result(), EL.classify(lt_result()),
                          EL.discount_view(lt_result()),
                          {"fit": {"score": 90}, "prob_profit": 0.8,
                           "dte": 30},
                          SC.returns(92.0, 2.0, 30),
                          {"score": 60, "source": "IV Rank", "detail": ""},
                          {"score": 80, "tradable": True}, "FAVORABLE",
                          {"happy_to_own": None, "reason": "no data"})
        self.assertTrue(any("happy owning" in r for r in info["rejections"]))


# ─────────────────────────────────────────────────────────────────────────
class TestStrikeSelection(unittest.TestCase):

    def _candidates(self, spot=100.0, t=35 / 365, iv=0.30):
        out = []
        for k in (98, 95, 92, 90, 88, 85, 80):
            g = G.put_greeks(spot, k, t, 0.04, iv)
            row = put_row(k, max(0.05, g["price"] * 0.95),
                          g["price"] * 1.05 + 0.05)
            be = k - row["mid"]
            out.append({**row, **g, "dte": 35, "liquidity": 70,
                        "liquidity_tradable": True,
                        "annualised": row["mid"] / k * (365 / 35) * 100,
                        "prob_profit": G.prob_above(spot, be, t, 0.04, iv)})
        return out

    def test_support_levels_are_below_spot_and_nearest_first(self):
        """S1/S2/S3 convention — reading order is proximity, not strength."""
        lv = ST.support_levels(lt_result())
        self.assertTrue(lv)
        self.assertTrue(all(x["price"] < 100.0 for x in lv))
        self.assertEqual([x["price"] for x in lv],
                         sorted((x["price"] for x in lv), reverse=True))

    def test_anchor_is_the_nearest_strong_level_not_the_strongest(self):
        """With a 50 MA at 92 and a 200 MA at 85, anchoring to the 200 MA
        would demand a 15%-OTM strike collecting almost no premium."""
        lv = ST.support_levels(lt_result())
        anchor = ST.anchor_level(lv)
        deepest = min(lv, key=lambda x: x["price"])
        self.assertGreater(anchor["price"], deepest["price"])
        self.assertGreaterEqual(anchor["confidence"],
                                ST.MIN_ANCHOR_CONFIDENCE)

    def test_chosen_strike_is_at_or_below_the_anchor_support(self):
        res = lt_result()
        sel = ST.select(self._candidates(), res, "FAVORABLE", 100.0)
        self.assertIsNotNone(sel["chosen"])
        self.assertLessEqual(sel["chosen"]["strike"], sel["anchor"]["price"])

    def test_delta_band_is_respected(self):
        res = lt_result()
        sel = ST.select(self._candidates(), res, "SELECTIVE", 100.0)
        lo, hi = sel["band"]
        self.assertTrue(lo <= abs(sel["chosen"]["delta"]) <= hi)

    def test_defensive_regime_demands_more_cushion(self):
        lo_f, hi_f = ST.delta_band(88, "FAVORABLE", False)
        lo_d, hi_d = ST.delta_band(88, "DEFENSIVE", False)
        self.assertLess(hi_d, hi_f)

    def test_unfillable_chain_yields_no_trade_not_a_bad_trade(self):
        """Invariant: a 0.01-bid market must not become a recommendation."""
        cands = [{**c, "liquidity": 5, "liquidity_tradable": False}
                 for c in self._candidates()]
        sel = ST.select(cands, lt_result(), "FAVORABLE", 100.0)
        self.assertIsNone(sel["chosen"])
        self.assertEqual(sel["blocked_on"], "liquidity")

    def test_liquidity_block_is_diagnosed_as_liquidity_not_delta(self):
        """Filtering liquidity before the delta band made the engine
        report "no strike in the delta band" for a spread problem."""
        cands = [{**c, "liquidity": 20, "liquidity_tradable": False,
                  "spread_pct": 21.0} for c in self._candidates()]
        sel = ST.select(cands, lt_result(), "FAVORABLE", 100.0)
        self.assertEqual(sel["blocked_on"], "liquidity")
        self.assertNotIn("delta band", sel["no_trade_reason"])

    def test_unassessable_spread_does_not_block(self):
        """Market closed: tradable is None, which must not filter."""
        cands = [{**c, "liquidity": 70, "liquidity_tradable": None}
                 for c in self._candidates()]
        sel = ST.select(cands, lt_result(), "FAVORABLE", 100.0)
        self.assertIsNotNone(sel["chosen"])

    def test_delta_classes_name_the_tradeoff(self):
        self.assertEqual(ST.classify_delta(-0.12), "Conservative")
        self.assertEqual(ST.classify_delta(-0.22), "Core CSP")
        self.assertEqual(ST.classify_delta(-0.29), "Aggressive")
        self.assertEqual(ST.classify_delta(-0.45), "High assignment risk")

    def test_no_strike_below_support_yields_no_trade(self):
        """Support far below every listed strike: NO TRADE, not the
        least-bad strike above the level."""
        res = lt_result(pullback={**lt_result()["pullback"],
                                  "levels": [{"key": "200MA", "zone": "200MA",
                                              "name": "200 MA", "price": 50.0,
                                              "held": True,
                                              "supporting": False}],
                                  "buy_zone": {"price": 50.0,
                                               "name": "shelf",
                                               "touches": 3,
                                               "actual_support": True}},
                        entries=[{"price": 50.0}])
        sel = ST.select(self._candidates(), res, "FAVORABLE", 100.0)
        self.assertIsNone(sel["chosen"])

    def test_richer_premium_does_not_beat_better_structure(self):
        """The whole thesis: a strike above support with a fat premium
        must lose to one below support with a thinner premium."""
        cands = self._candidates()
        for c in cands:
            if c["strike"] == 98:          # above the 92.0 support anchor
                c["annualised"] = 999.0
        sel = ST.select(cands, lt_result(), "FAVORABLE", 100.0)
        self.assertNotEqual(sel["chosen"]["strike"], 98)


# ─────────────────────────────────────────────────────────────────────────
class TestScoreAndVolatility(unittest.TestCase):

    def test_missing_inputs_reduce_coverage_not_the_score(self):
        """Invariant 3: absent is not zero."""
        res, el = lt_result(), EL.classify(lt_result())
        chosen = {"fit": {"score": 90}, "prob_profit": 0.8, "dte": 35}
        ret = SC.returns(92.0, 2.0, 35)
        ok = SC.compute(res, el, EL.discount_view(res), chosen, ret,
                        {"score": 70, "source": "IV Rank", "detail": ""},
                        {"score": 80, "tradable": True}, "FAVORABLE",
                        {"happy_to_own": True},
                        adq={"score": 70}, eff={"score": 70})
        missing = SC.compute(res, el, EL.discount_view(res), chosen, ret,
                             {"score": None, "source": None, "detail": ""},
                             {"score": None, "tradable": True}, "FAVORABLE",
                             {"happy_to_own": True},
                             adq={"score": 70}, eff={"score": 70})
        self.assertEqual(ok["coverage"], 100)
        self.assertLess(missing["coverage"], 100)
        self.assertGreater(missing["score"], ok["score"] - 12)

    def test_stock_and_option_are_scored_independently(self):
        """An excellent company must not lift a poor contract."""
        res = lt_result()
        stock = SC.stock_score(res, EL.discount_view(res),
                               {"fit": {"score": 95}},
                               {"confidence": 90})
        poor = SC.option_score({"score": 20}, {"score": 25},
                               {"score": 30}, {"score": 20},
                               {"prob_profit": 0.85})
        self.assertGreater(stock["score"], 80)
        self.assertLess(poor["score"], 40)

    def test_combined_score_is_multiplicative_not_additive(self):
        """98-quality company on a 35-quality contract must not print in
        the 70s the way a weighted sum did."""
        combined = SC.combine({"score": 98}, {"score": 35})
        self.assertLess(combined, 40)
        self.assertGreater(combined, 30)

    def test_neither_half_can_rescue_the_other(self):
        self.assertLess(SC.combine({"score": 100}, {"score": 20}), 25)
        self.assertLess(SC.combine({"score": 20}, {"score": 100}), 25)

    def test_excellent_stock_inadequate_option_waits_for_iv(self):
        """The headline failure mode: quality 98 and a textbook strike
        must not outvote a thin premium at 0.98x realised vol."""
        info = {"score": 34, "rejections": [], "components": [],
                "coverage": 100,
                "stock": {"score": 98}, "option": {"score": 40}}
        out = SC.final_action(info, EL.classify(lt_result()),
                              {"happy_to_own": True}, None,
                              ret={"annualised": 15.3},
                              iv_op={"score": 40, "source": "IV vs realised",
                                     "detail": "IV 43% vs realised 44% — 0.98x"},
                              adq={"ratio": 0.9},
                              liq={"spread_verdict": "ACCEPTABLE",
                                   "quotes_live": True})
        self.assertEqual(out["key"], "WAIT_IV")
        self.assertIn("inadequate option", out["why"])

    def test_headline_names_both_verdicts(self):
        info = {"stock": {"score": 98}, "option": {"score": 40}}
        line = SC.headline(info, {"why": ""})
        self.assertIn("EXCELLENT STOCK", line)
        self.assertIn("WEAK OPTION", line)

    def test_wide_spread_blocks_the_sell_even_with_a_great_stock(self):
        info = {"score": 60, "rejections": [], "components": [],
                "coverage": 100,
                "stock": {"score": 98}, "option": {"score": 50}}
        out = SC.final_action(info, EL.classify(lt_result()),
                              {"happy_to_own": True}, None,
                              ret={"annualised": 20.0},
                              iv_op={"score": 80, "detail": ""},
                              adq={"ratio": 1.4},
                              liq={"spread_verdict": "CAUTION",
                                   "spread_pct": 9.4, "quotes_live": True})
        self.assertEqual(out["key"], "WATCH")
        self.assertIn("spread", out["why"])

    def test_closed_market_downgrades_sell_to_verify(self):
        """A SELL asserts the contract is fillable; against a closed book
        that assertion cannot be made."""
        info = {"score": 74, "rejections": [], "components": [],
                "coverage": 100,
                "stock": {"score": 98}, "option": {"score": 67}}
        out = SC.final_action(info, EL.classify(lt_result()),
                              {"happy_to_own": True}, None,
                              ret={"annualised": 15.3},
                              iv_op={"score": 60, "detail": ""},
                              adq={"ratio": 1.33},
                              liq={"spread_verdict": "UNKNOWN",
                                   "quotes_live": False})
        self.assertEqual(out["key"], "VERIFY")

    def test_liquidity_block_names_liquidity_not_the_delta(self):
        """The diagnosis must not blame the delta band for a spread
        problem — that sends the user to wait for a price move that
        would not have helped."""
        info = {"score": None, "rejections": [], "components": [],
                "coverage": 60, "stock": {"score": 98},
                "option": {"score": None}}
        out = SC.final_action(info, EL.classify(lt_result()),
                              {"happy_to_own": None},
                              "the right strike exists but is 21% wide",
                              blocked_on="liquidity")
        self.assertEqual(out["key"], "REJECT")

    def test_level_block_waits_for_the_stock_not_for_iv(self):
        info = {"score": None, "rejections": [], "components": [],
                "coverage": 60, "stock": {"score": 90},
                "option": {"score": None}}
        out = SC.final_action(info, EL.classify(lt_result()),
                              {"happy_to_own": None},
                              "no strike sits below support",
                              blocked_on="level")
        self.assertEqual(out["key"], "WAIT_LEVEL")

    def test_event_priced_yield_is_rejected(self):
        res, el = lt_result(), EL.classify(lt_result())
        info = SC.compute(res, el, EL.discount_view(res),
                          {"fit": {"score": 90}, "prob_profit": 0.8,
                           "dte": 35},
                          SC.returns(92.0, 20.0, 35),      # ~227% annualised
                          {"score": 90, "source": "IV Rank", "detail": ""},
                          {"score": 80, "tradable": True}, "FAVORABLE",
                          {"happy_to_own": True})
        self.assertTrue(any("event pricing" in r for r in info["rejections"]))

    def test_iv_rank_is_withheld_until_history_is_long_enough(self):
        """Invariant 4: no invented rank from a short store."""
        with tempfile.TemporaryDirectory() as d:
            V._STORE = Path(d) / "iv.json"
            V.record("AAA", 0.35, today="2026-08-11")
            out = V.rank("AAA", 0.35)
            self.assertFalse(out["available"])
            self.assertIn("needs", out["reason"])
            self.assertIsNone(V.iv_opportunity(out, {"ratio": None})["score"])

    def test_iv_falls_back_to_realised_when_rank_is_unavailable(self):
        op = V.iv_opportunity({"available": False},
                              {"ratio": 1.4, "iv": 42.0, "hv": 30.0})
        self.assertEqual(op["source"], "IV vs realised")
        self.assertGreater(op["score"], 50)

    def test_iv_below_realised_scores_poorly(self):
        op = V.iv_opportunity({"available": False},
                              {"ratio": 0.79, "iv": 35.0, "hv": 45.0})
        self.assertLess(op["score"], 20)

    def test_realised_vol_is_annualised(self):
        closes = [100.0 * (1.01 if i % 2 else 0.99) for i in range(80)]
        hv = V.realised_vol(closes, window=30)
        self.assertIsNotNone(hv)
        self.assertGreater(hv, 0.05)


# ─────────────────────────────────────────────────────────────────────────
class TestPremiumAdequacy(unittest.TestCase):

    def _req(self, **kw):
        args = dict(delta=-0.20, dte=38, risk_free=0.0468, quality=98,
                    valuation_margin=50.0, support_confidence=90,
                    regime="SELECTIVE")
        args.update(kw)
        return PR.required(**args)

    def test_hurdle_starts_from_the_risk_free_rate(self):
        req = self._req()
        self.assertGreater(req["annualised"], req["risk_free_pct"])

    def test_quality_discount_is_small_enough_not_to_double_count(self):
        """Quality, valuation and support are already the whole stock
        score. An earlier version shaved 55% off the hurdle for them and
        made a 1.59% yield read "well paid"."""
        elite = self._req()
        plain = self._req(quality=70, valuation_margin=0,
                          support_confidence=40)
        self.assertGreaterEqual(elite["want_factor"], 0.85)
        self.assertLess(elite["annualised"], plain["annualised"])
        # The whole discount must be modest, not decisive.
        self.assertLess(plain["annualised"] - elite["annualised"], 2.0)

    def test_the_nem_contract_does_not_read_as_well_paid(self):
        """1.59% over 38 days on an elite, deeply discounted name is a
        mediocre sale, and must not score as a strong one."""
        adq = PR.adequacy(1.59, self._req())
        self.assertLess(adq["ratio"], 1.5)
        self.assertNotEqual(adq["verdict"], "STRONG")

    def test_underpaid_contract_scores_far_below_the_pass_mark(self):
        adq = PR.adequacy(0.60, self._req())
        self.assertLess(adq["ratio"], 1.0)
        self.assertLess(adq["score"], 40)
        self.assertIsNotNone(adq["shortfall_pct"])

    def test_defensive_regime_raises_the_hurdle(self):
        self.assertGreater(self._req(regime="DEFENSIVE")["annualised"],
                           self._req(regime="FAVORABLE")["annualised"])

    def test_higher_delta_demands_more_premium(self):
        self.assertGreater(self._req(delta=-0.30)["annualised"],
                           self._req(delta=-0.12)["annualised"])

    def test_static_floor_binds_when_the_dynamic_hurdle_is_tiny(self):
        req = PR.required(-0.10, 31, 0.0, 99, 60.0, 95, "FAVORABLE")
        self.assertEqual(req["binding"], "static floor")
        self.assertGreaterEqual(req["period_pct"], 1.0)

    def test_capital_efficiency_compares_against_cash(self):
        eff = PR.capital_efficiency(15.3, 0.0468, 10500.0, 167.0, 38)
        self.assertAlmostEqual(eff["excess"], 15.3 - 4.68, places=1)
        self.assertGreater(eff["edge_dollars"], 0)
        self.assertLess(eff["cash_alternative"], 167.0)

    def test_below_cash_yield_is_flagged(self):
        eff = PR.capital_efficiency(3.0, 0.0468, 10000.0, 25.0, 30)
        self.assertLess(eff["excess"], 0)
        self.assertIn("Below cash", eff["label"])

    def test_premium_per_atr_is_unit_free(self):
        out = PR.per_unit_downside(1.67, 3.85, 16.0, 103.33, [])
        self.assertAlmostEqual(out["per_atr"], 0.43, places=2)
        self.assertLess(out["per_expected_move"], 0.2)

    def test_technical_cushion_measures_from_the_basis(self):
        out = PR.per_unit_downside(1.67, 3.85, 16.0, 103.33,
                                   [{"name": "200 MA", "price": 95.0}])
        self.assertGreater(out["technical_cushion_pct"], 0)
        self.assertEqual(out["cushion_level"], "200 MA")

    def test_margin_at_assignment_uses_growth_when_no_fair_value(self):
        """Reverse DCF yields no fair-value price — there is nothing to
        take a percentage of, and inventing one is not allowed."""
        out = PR.margin_at_assignment(103.33, EL.discount_view(lt_result()))
        self.assertEqual(out["basis_kind"], "growth")
        self.assertEqual(out["pct"], 10.0)

    def test_margin_at_assignment_uses_price_when_fair_value_exists(self):
        out = PR.margin_at_assignment(80.0, {"fair_value": 100.0})
        self.assertEqual(out["basis_kind"], "price")
        self.assertEqual(out["pct"], 20.0)

    def test_ideal_zone_is_emitted_even_with_no_qualifying_contract(self):
        """"Wait for IV expansion" is only actionable next to the premium
        that would make it a trade."""
        zone = PR.ideal_zone(116.31, [], {"price": 114.01, "name": "shelf"},
                             112.18, self._req(), 20, 45)
        self.assertLessEqual(zone["strike_high"], 112.18)
        self.assertGreater(zone["min_premium"], 0)
        self.assertGreater(zone["ideal_premium"], zone["min_premium"])


if __name__ == "__main__":
    unittest.main()
