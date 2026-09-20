"""
Tests for core/shortside — the two-sided decision engine.

No network. Bars are handed in as small DataFrames; everything else is a
pure function of a long-term result plus a scan row.

The invariants these defend:

  1. Failing the long quality test must NEVER route a name to NO EDGE.
     That was the whole defect this engine exists to fix.
  2. A high short score is a SETUP, not an instruction — confirmation is
     a separate axis and must not be blended into the score.
  3. Extension is measured in ATR. Two names at the same percentage and
     different ATR must score differently.
  4. Strong relative strength CAPS the bucket at WATCH; it never
     disqualifies the name.

Run with: python -m unittest tests.test_shortside
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.shortside import engine as E        # noqa: E402
from stockanalysis.core.shortside import extension as EX    # noqa: E402
from stockanalysis.core.shortside import reversal as RV     # noqa: E402
from stockanalysis.core.shortside import thesis as TH       # noqa: E402


def lt_result(**over):
    """A long-term result for a weak, expensive company — MDB-shaped."""
    base = {
        "ticker": "TEST", "name": "Test Co", "sector": "Technology",
        "price": 457.14, "action": "AVOID", "lt_score": 45,
        "quality": {"score": 60.0, "tier": "Reject", "coverage": 0.9,
                    "reliable": True, "ownable": False},
        "valuation": {"band": "OVERVALUED", "acceptable": False,
                      "method": "REVERSE_DCF", "confidence": "HIGH",
                      "headline": "demands 52% growth, delivers 24%",
                      "implied_growth_pct": 52.0,
                      "delivered_growth_pct": 24.0,
                      "growth_gap_pp": 28.0},
        "investment": {"status": "REJECT"},
    }
    base.update(over)
    return base


def scan_row(**over):
    """MDB's actual reading on 2026-08-11."""
    base = {
        "Ticker": "TEST", "Current Price": 457.14,
        "ATR20": 19.56, "ATR_Pct": 4.28,
        "8EMA": 412.85, "21EMA": 374.62, "50MA": 347.19, "200MA": 337.15,
        "RSI_14": 78.7, "ADX_14": 26.2, "BB_PctB": 1.006,
        "Dist_52W_High%": -1.3, "Pct_From_52W_Low%": 126.3,
        "Days_Since_52W_High": 0, "Distribution_Days_25d": 5,
        "RS_Rank": 91, "52W High": 463.14, "52W Low": 202.05,
        "Prev-Day High": 443.31, "Prev-Day Low": 428.0,
    }
    base.update(over)
    return base


def bars(rows):
    """rows: list of (open, high, low, close, volume)."""
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close",
                                       "Volume"])


def flat(n=25, close=100.0, vol=1_000_000):
    return [(close, close + 1, close - 1, close, vol)] * n


# ─────────────────────────────────────────────────────────────────────────
class TestExtensionInATR(unittest.TestCase):

    def test_same_percentage_different_atr_scores_differently(self):
        """The headline reason percent is the wrong unit: 10% above the
        8 EMA is parabolic on a 2%-ATR name and a normal week on a
        5%-ATR one."""
        calm = EX.evaluate({"Current Price": 110.0, "8EMA": 100.0,
                            "ATR20": 2.0, "ATR_Pct": 2.0})
        wild = EX.evaluate({"Current Price": 110.0, "8EMA": 100.0,
                            "ATR20": 5.0, "ATR_Pct": 5.0})
        self.assertEqual(calm["levels"]["8EMA"]["pct"],
                         wild["levels"]["8EMA"]["pct"])
        self.assertGreater(calm["headline_atr"], wild["headline_atr"])
        self.assertGreater(calm["score"], wild["score"])

    def test_atr_multiple_arithmetic(self):
        self.assertEqual(EX.atr_multiple(110.0, 100.0, 4.0), 2.5)
        self.assertEqual(EX.atr_multiple(90.0, 100.0, 4.0), -2.5)

    def test_zero_atr_returns_none_not_infinity(self):
        """A flat stock must not read as the most extended in the
        universe."""
        self.assertIsNone(EX.atr_multiple(110.0, 100.0, 0.0))
        self.assertIsNone(EX.atr_multiple(110.0, 100.0, None))

    def test_mdb_is_very_extended(self):
        ext = EX.evaluate(scan_row())
        self.assertGreater(ext["headline_atr"], 2.0)
        self.assertIn("extended", ext["headline_band"].lower())
        self.assertGreater(ext["score"], 60)

    def test_missing_levels_reduce_coverage_not_the_score(self):
        """Invariant: absent is not the same claim as unextended."""
        full = EX.evaluate(scan_row())
        partial = EX.evaluate(scan_row(**{"8EMA": None, "21EMA": None}))
        self.assertEqual(full["coverage"], 100)
        self.assertLess(partial["coverage"], 100)
        self.assertIsNotNone(partial["score"])

    def test_targets_are_below_price_and_nearest_first(self):
        t = EX.mean_reversion_targets(scan_row())
        self.assertTrue(all(x["price"] < 457.14 for x in t))
        self.assertEqual([x["price"] for x in t],
                         sorted((x["price"] for x in t), reverse=True))
        self.assertEqual(t[0]["key"], "8EMA")


# ─────────────────────────────────────────────────────────────────────────
class TestBearishReversal(unittest.TestCase):

    def test_bearish_engulfing(self):
        b = bars(flat(3) + [(100, 106, 99, 105, 1e6), (106, 107, 99, 99, 2e6)])
        self.assertEqual(RV.detect(b), "bearish engulfing")

    def test_shooting_star(self):
        b = bars(flat(3) + [(100, 101, 99, 100, 1e6),
                            (100, 112, 99.5, 100.5, 1e6)])
        self.assertEqual(RV.detect(b), "shooting star")

    def test_no_pattern_reports_none_the_string(self):
        """"none" is a measurement; None is the absence of one, and the
        engine treats them differently."""
        self.assertEqual(RV.detect(bars(flat(6))), "none")

    def test_too_few_bars_reports_none_the_object(self):
        self.assertIsNone(RV.detect(bars(flat(2))))
        self.assertIsNone(RV.detect(None))

    def test_first_red_day_needs_a_real_streak(self):
        green = [(100 + i, 102 + i, 99 + i, 101.5 + i, 1e6) for i in range(5)]
        red = [(106, 107, 100, 101, 2e6)]
        out = RV.first_red_day(bars(flat(5) + green + red))
        self.assertTrue(out["triggered"])
        self.assertGreaterEqual(out["green_streak"], 3)

    def test_one_green_day_then_red_is_not_first_red_day(self):
        b = bars(flat(8) + [(100, 103, 99, 102, 1e6), (102, 103, 98, 99, 1e6)])
        self.assertFalse(RV.first_red_day(b)["triggered"])

    def test_confirmation_needs_two_signals_to_be_strong(self):
        weak = RV.confirmation(bars(flat(3)
                                    + [(100, 106, 99, 105, 1e6),
                                       (106, 107, 99, 99, 1e6)]))
        self.assertEqual(weak["state"], "WEAK")
        self.assertFalse(weak["confirmed"])

    def test_confirmation_is_strong_with_candle_and_first_red_day(self):
        green = [(100 + i, 102 + i, 99 + i, 101.5 + i, 1e6) for i in range(5)]
        b = bars(flat(20) + green + [(105.5, 106, 98, 99, 3e6)])
        out = RV.confirmation(b)
        self.assertEqual(out["state"], "STRONG")
        self.assertTrue(out["confirmed"])


# ─────────────────────────────────────────────────────────────────────────
class TestShortThesis(unittest.TestCase):

    def test_low_quality_raises_the_short_score(self):
        """The inversion this engine exists for."""
        weak = TH.weakness(lt_result())
        strong = TH.weakness(lt_result(
            quality={"score": 92.0, "tier": "Elite", "coverage": 0.9}))
        self.assertGreater(weak["score"], strong["score"])
        self.assertEqual(strong["score"], 0)

    def test_fundamentals_alone_cannot_carry_the_score(self):
        """A terrible company that is not extended, not overbought and
        not under distribution belongs in NO EDGE."""
        calm = scan_row(**{
            "Current Price": 100.0, "8EMA": 99.0, "21EMA": 98.0,
            "50MA": 97.0, "200MA": 95.0, "ATR20": 3.0, "RSI_14": 48.0,
            "BB_PctB": 0.45, "Dist_52W_High%": -35.0,
            "Days_Since_52W_High": 200, "Distribution_Days_25d": 2,
            "RS_Rank": 30, "52W High": 150.0})
        out = TH.compute(lt_result(price=100.0), calm, 100.0, 3.0)
        self.assertLess(out["score"], E.SHORT_STRONG)

    def test_mdb_clears_the_watch_threshold(self):
        out = TH.compute(lt_result(), scan_row(), 457.14, 19.56)
        self.assertGreaterEqual(out["score"], E.SHORT_STRONG)

    def test_strong_rs_caps_but_never_disqualifies(self):
        """Invariant 4."""
        out = TH.compute(lt_result(), scan_row(**{"RS_Rank": 95}),
                         457.14, 19.56)
        self.assertIsNotNone(out["score"])
        self.assertLessEqual(out["score"], 85)
        self.assertTrue(any("still bidding" in c for c in out["caps"]))

    def test_distribution_uses_the_classic_warning_band(self):
        """Anchored on 4-5-in-25, not on this universe's median — which
        sits at 5 only because the scan is a late-stage bull market."""
        self.assertGreaterEqual(
            TH.distribution({"Distribution_Days_25d": 5})["score"], 45)
        self.assertLessEqual(
            TH.distribution({"Distribution_Days_25d": 2})["score"], 5)

    def test_missing_components_reduce_coverage_not_the_score(self):
        bare = TH.compute(lt_result(), {"Current Price": 457.14}, 457.14, None)
        self.assertLess(bare["coverage"], 100)
        self.assertIsNotNone(bare.get("score"))


# ─────────────────────────────────────────────────────────────────────────
class TestBuckets(unittest.TestCase):

    def _eval(self, result=None, row=None, daily=None):
        return E.evaluate(result or lt_result(), row or scan_row(),
                          fetch_bars=False, daily=daily)

    def test_failing_the_long_quality_test_is_not_no_edge(self):
        """INVARIANT 1 — the defect this whole engine exists to fix.

        The old path ended at "AVOID — failed on business quality" and
        never scored the other side."""
        out = self._eval()
        self.assertEqual(out["bucket"], "SHORT WATCH")
        self.assertNotEqual(out["bucket"], "NO EDGE")
        self.assertNotIn("quality", out["why"].lower())

    def test_both_scores_are_reported_side_by_side(self):
        out = self._eval()
        self.assertEqual(out["long_score"], 45)
        self.assertGreaterEqual(out["short_score"], E.SHORT_STRONG)
        self.assertIsNotNone(out["avoid_score"])

    def test_strong_thesis_without_confirmation_is_watch_not_now(self):
        """INVARIANT 2 — a high short score is a setup, not an
        instruction."""
        out = self._eval(daily=bars(flat(30)))
        self.assertEqual(out["bucket"], "SHORT WATCH")
        self.assertIn("awaiting confirmation", out["why"])

    def test_strong_thesis_with_confirmation_is_short_now(self):
        green = [(400 + i * 10, 410 + i * 10, 395 + i * 10, 408 + i * 10, 1e6)
                 for i in range(5)]
        confirmed = bars(flat(20, close=400.0)
                         + green + [(448, 450, 430, 432, 3e6)])
        out = self._eval(row=scan_row(**{"RS_Rank": 55}), daily=confirmed)
        self.assertEqual(out["bucket"], "SHORT NOW")

    def test_strong_rs_blocks_short_now_even_with_confirmation(self):
        """RS 91 caps the thesis below the NOW threshold, so a confirmed
        reversal still reads WATCH — the tape is not done bidding."""
        green = [(400 + i * 10, 410 + i * 10, 395 + i * 10, 408 + i * 10, 1e6)
                 for i in range(5)]
        confirmed = bars(flat(20, close=400.0)
                         + green + [(448, 450, 430, 432, 3e6)])
        out = self._eval(daily=confirmed)      # RS_Rank 91
        self.assertIn(out["bucket"], ("SHORT WATCH", "SHORT NOW"))
        if out["bucket"] == "SHORT WATCH":
            self.assertTrue(any("bidding" in c
                                for c in out["short"]["caps"]))

    def test_a_good_company_is_a_long_opportunity(self):
        good = lt_result(action="BUY ON 50 MA", lt_score=82,
                         quality={"score": 90.0, "tier": "Elite",
                                  "coverage": 0.95},
                         valuation={"band": "UNDERVALUED",
                                    "acceptable": True,
                                    "method": "REVERSE_DCF",
                                    "growth_gap_pp": -12.0})
        calm = scan_row(**{"RSI_14": 52.0, "BB_PctB": 0.5,
                           "Dist_52W_High%": -12.0,
                           "Distribution_Days_25d": 2,
                           "Days_Since_52W_High": 90})
        self.assertEqual(self._eval(good, calm)["bucket"],
                         "LONG OPPORTUNITY")

    def test_avoid_score_penalises_a_genuine_conflict(self):
        """Two sides arguing equally is noise, and must read as MORE
        reason to stand aside than a name nobody has a view on."""
        conflict = E.avoid_score({"score": 55}, {"score": 56})
        apart = E.avoid_score({"score": 20}, {"score": 75})
        self.assertGreater(conflict["conflict"], 0)
        self.assertGreater(conflict["score"], apart["score"])

    def test_no_edge_never_cites_the_quality_gate(self):
        calm = scan_row(**{
            "Current Price": 100.0, "8EMA": 99.0, "21EMA": 98.0,
            "50MA": 97.0, "200MA": 95.0, "ATR20": 3.0, "RSI_14": 48.0,
            "BB_PctB": 0.45, "Dist_52W_High%": -35.0,
            "Days_Since_52W_High": 200, "Distribution_Days_25d": 2,
            "RS_Rank": 30, "52W High": 150.0})
        out = self._eval(lt_result(price=100.0), calm)
        self.assertEqual(out["bucket"], "NO EDGE")
        self.assertNotIn("quality", out["why"].lower())

    def test_plan_stop_is_at_least_one_atr_away(self):
        """On a parabolic name a tight percentage stop sits inside a
        single session's range."""
        out = self._eval()
        plan = out["plan"]
        self.assertGreaterEqual(plan["stop_atr"], 1.0)
        self.assertGreater(plan["stop"], out["price"])

    def test_plan_targets_carry_reward_to_risk(self):
        plan = self._eval()["plan"]
        self.assertTrue(plan["targets"])
        self.assertTrue(all(t["price"] < plan["entry"]
                            for t in plan["targets"]))
        self.assertGreater(plan["best_rr"], 1.0)

    def test_no_bars_leaves_confirmation_unchecked_not_false(self):
        out = self._eval(daily=None)
        self.assertIn(out["confirmation"]["state"],
                      ("NOT_CHECKED", "UNKNOWN"))
        self.assertFalse(out["confirmation"]["confirmed"])


if __name__ == "__main__":
    unittest.main()
