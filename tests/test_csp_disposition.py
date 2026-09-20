"""
Tests for core.csp.disposition — what to DO about a name.

The invariants that make this a decision system rather than a second
scoring pass:

  1. It never overrides the gate. A premium cannot turn a quality failure
     into SELL_CSP, because letting a yield rescue a bad business is the
     exact failure the gate ordering exists to prevent. What a rejection
     gains is a DISPOSITION, not a promotion.
  2. Effective entry, not the strike, is what assignment costs. Every
     yield on the page is computed from the credit; this is the only
     figure saying what you would end up owning and at what price.
  3. Measured against the buy zone, the biggest premiums rank WORST. CRDO
     pays 121% annualised to buy a stock 22% above the price you said you
     wanted it at. That is the reordering the module exists for.
  4. Why a stock is down, not how far. ORCL (Stage 4, price inside its
     zone) and INOD (quality 93, trend repricing) are different
     situations; "down a lot" describes both and distinguishes nothing.
  5. A missing input never becomes a score — the package rule. The score
     renormalises over what it could measure and says which terms counted.

Run with: python -m unittest tests.test_csp_disposition
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.csp import disposition as D


def _row(quality=90, price=200.0, low=180.0, high=190.0, strike=185.0,
         credit=5.0, verdict="SELL", why="", adequacy=1.5, liquidity=80,
         above_spot=False, earnings_inside=False, trend="CONFIRMED"):
    row = {
        "ticker": "TEST", "price": price,
        "eligibility": {"quality_score": quality},
        "discount": {"growth_gap_pp": -5.0},
        "final": {"key": verdict, "why": why},
        "adequacy": {"ratio": adequacy},
        "earnings_distance": {"inside": earnings_inside, "days": 40},
        "liquidity": {"score": liquidity},
        "_lt": {"trend": {"state": trend}, "thesis": {"status": "INTACT"}},
    }
    if low is not None:
        row["buy_zone"] = {"low": low, "high": high, "kind": "technical",
                           "above_spot": above_spot}
    if strike is not None:
        row["chosen"] = {"strike": strike, "limit_price": credit,
                         "liquidity": liquidity, "dte": 31}
    return row


class EffectiveEntry(unittest.TestCase):
    """(2) Assignment costs the strike LESS the premium."""

    def test_it_is_strike_minus_credit(self):
        self.assertEqual(D.effective_entry(215.0, 7.55), 207.45)

    def test_the_users_worked_examples(self):
        for strike, credit, expect in ((215.0, 7.55, 207.45),
                                       (240.0, 24.76, 215.24),
                                       (540.0, 19.80, 520.20),
                                       (195.0, 5.0, 190.0)):
            self.assertEqual(D.effective_entry(strike, credit), expect)

    def test_no_credit_is_the_strike_not_a_crash(self):
        self.assertEqual(D.effective_entry(100.0, None), 100.0)

    def test_no_strike_is_unmeasurable(self):
        self.assertIsNone(D.effective_entry(None, 5.0))


class EntryVersusZone(unittest.TestCase):
    """(3) The reordering axis."""

    def test_entry_inside_the_zone_is_negative_and_flagged(self):
        d = D.compute(_row(strike=185.0, credit=5.0, low=180.0, high=190.0))
        self.assertEqual(d["effective_entry"], 180.0)
        self.assertTrue(d["entry_in_zone"])
        self.assertLess(d["entry_vs_zone_pct"], 0)

    def test_a_fat_premium_above_the_zone_still_reads_as_above(self):
        """CRDO: 121% annualised, entry $215.24 against a $168-177 zone."""
        d = D.compute(_row(quality=98, price=245.97, low=168.0, high=177.0,
                           strike=240.0, credit=24.76, verdict="REJECT"))
        self.assertEqual(d["effective_entry"], 215.24)
        self.assertFalse(d["entry_in_zone"])
        self.assertGreater(d["entry_vs_zone_pct"], 20)
        self.assertEqual(d["final_action"], "WAIT_FOR_BUY_ZONE")

    def test_distance_to_the_zone_is_measured_off_todays_price(self):
        d = D.compute(_row(price=219.74, low=193.0, high=197.0))
        self.assertAlmostEqual(d["dist_to_buy_zone_pct"], 10.35, places=1)

    def test_no_zone_means_no_claim(self):
        d = D.compute(_row(low=None, high=None))
        self.assertIsNone(d["entry_vs_zone_pct"])
        self.assertIsNone(d["entry_in_zone"])
        self.assertIsNone(d["dist_to_buy_zone_pct"])


class TheFiveStates(unittest.TestCase):
    """(1) and (4)."""

    def test_below_the_quality_floor_is_always_avoid(self):
        """(1) whatever the premium says."""
        d = D.compute(_row(quality=41, adequacy=9.0, strike=100.0,
                           credit=40.0, low=180.0, high=190.0))
        self.assertEqual(d["final_action"], "AVOID")
        self.assertIn("floor", d["why"])

    def test_no_quality_score_is_avoid_not_a_guess(self):
        self.assertEqual(D.compute(_row(quality=None))["final_action"],
                         "AVOID")

    def test_a_broken_thesis_is_avoid(self):
        row = _row(quality=95)
        row["_lt"]["thesis"] = {"status": "BROKEN"}
        self.assertEqual(D.compute(row)["final_action"], "AVOID")

    def test_a_repricing_trend_is_a_thesis_check(self):
        """INOD: quality 93, price in the zone, trend repricing."""
        d = D.compute(_row(quality=93, price=61.62, low=60.0, high=62.0,
                           strike=60.0, credit=4.36, trend="BROKEN",
                           verdict="REJECT", why="long-term trend broken"))
        self.assertEqual(d["final_action"], "THESIS_CHECK")
        self.assertEqual(d["thesis_status"], "REPRICING")

    def test_entry_in_the_zone_on_a_good_business_sells(self):
        d = D.compute(_row(quality=90, strike=185.0, credit=5.0,
                           low=180.0, high=190.0, adequacy=1.5))
        self.assertEqual(d["final_action"], "SELL_CSP")

    def test_a_thin_premium_waits_for_a_better_strike(self):
        d = D.compute(_row(strike=185.0, credit=5.0, adequacy=0.7))
        self.assertEqual(d["final_action"], "WAIT_FOR_BETTER_STRIKE")

    def test_earnings_inside_the_expiry_waits(self):
        d = D.compute(_row(strike=185.0, credit=5.0, earnings_inside=True))
        self.assertEqual(d["final_action"], "WAIT_FOR_BETTER_STRIKE")
        self.assertIn("earnings", d["why"])

    def test_price_in_the_zone_with_no_contract_buys_the_shares(self):
        """The 'stop waiting, just accumulate' case."""
        d = D.compute(_row(quality=90, price=185.0, low=180.0, high=190.0,
                           strike=None))
        self.assertEqual(d["final_action"], "BUY_NOW")

    def test_a_good_business_above_its_zone_waits_for_the_price(self):
        """NVDA: entry $207.45 against a $193-197 zone."""
        d = D.compute(_row(quality=98, price=219.74, low=193.0, high=197.0,
                           strike=215.0, credit=7.55, verdict="REJECT"))
        self.assertEqual(d["final_action"], "WAIT_FOR_BUY_ZONE")
        self.assertEqual(d["effective_entry"], 207.45)
        self.assertIn("above the top of the buy zone", d["why"])

    def test_an_overhead_zone_is_a_thesis_check_not_a_buy(self):
        d = D.compute(_row(quality=90, above_spot=True))
        self.assertEqual(d["final_action"], "THESIS_CHECK")
        self.assertIn("overhead", d["why"])

    def test_every_action_is_declared_and_labelled(self):
        for a in D.ACTIONS:
            self.assertIn(a, D.ACTION_LABEL)
            self.assertIn(a, D.ACTION_RANK)


class OwnershipScore(unittest.TestCase):
    """(5) A missing input never becomes a score."""

    def test_it_ranks_ownership_not_premium(self):
        rich_wrong_price = D.compute(_row(quality=98, price=245.97,
                                          low=168.0, high=177.0,
                                          strike=240.0, credit=24.76,
                                          adequacy=6.0))
        modest_right_price = D.compute(_row(quality=90, strike=185.0,
                                            credit=5.0, adequacy=1.2))
        self.assertGreater(modest_right_price["ownership_score"],
                           rich_wrong_price["ownership_score"])

    def test_missing_terms_drop_out_rather_than_scoring_zero(self):
        d = D.compute(_row(low=None, high=None, adequacy=None,
                           liquidity=None))
        self.assertIsNone(d["score_parts"]["entry"])
        self.assertIsNone(d["score_parts"]["premium"])
        self.assertIsNotNone(d["ownership_score"])

    def test_nothing_measurable_scores_none_not_zero(self):
        d = D.compute({"ticker": "X", "final": {}})
        self.assertIsNone(d["ownership_score"])

    def test_the_weights_are_the_declared_ones(self):
        self.assertEqual(sum(D.WEIGHTS.values()), 100)
        self.assertEqual(D.WEIGHTS["ownership"], 35)
        self.assertEqual(D.WEIGHTS["entry"], 25)
        # Premium is deliberately a minor term.
        self.assertLess(D.WEIGHTS["premium"], D.WEIGHTS["ownership"])
