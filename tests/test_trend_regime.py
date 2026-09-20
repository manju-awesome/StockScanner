"""
Tests for core.trend — the trend regime classification engine.

The synthetic frames below are the point. Every one of them is a chart
shape that a price-vs-one-average rule gets wrong, and the assertions are
the distinctions the engine exists to make:

    price above a RISING 200 vs above a FALLING one
    a pullback that holds the 50 vs one that loses it
    a single wick under the 200 vs an actual downtrend
    perfect alignment at a good price vs perfect alignment 5 ATR extended

Run with: python -m unittest tests.test_trend_regime
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.trend import engine as EN
from stockanalysis.core.trend import entry as ET
from stockanalysis.core.trend import filters as FL
from stockanalysis.core.trend import indicators as I
from stockanalysis.core.trend import regime as R
from stockanalysis.core.trend import score as SC


def frame(closes, vol=1_000_000, spread=0.01):
    """OHLCV frame from a close path. Highs/lows are a fixed fraction either
    side so ATR is stable and extension assertions are about the close."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    return pd.DataFrame({
        "Open": closes * (1 - spread / 2),
        "High": closes * (1 + spread),
        "Low": closes * (1 - spread),
        "Close": closes,
        "Volume": np.full(len(closes), float(vol)),
    }, index=idx)


# The wave matters. A perfectly smooth exponential has no local maxima, so
# it produces no swing pivots, no price structure and a score with the
# structure component permanently missing. Real charts oscillate around
# their trend; these fixtures do too, or they would be testing a shape that
# never occurs.
PERIOD = 21


def _wave(n, amp, phase=0.0):
    return 1 + amp * np.sin((np.arange(n) + phase) * 2 * np.pi / PERIOD)


def _end_high(n):
    """Phase that lands the LAST bar on the wave's peak.

    Without it the fixture's ending phase is an accident of `n`, and a
    trend fixture that happens to finish mid-oscillation is below its own
    8 EMA — so "clean uptrend" classifies as a pullback and the test is
    really asserting where the sine wave stopped.
    """
    return PERIOD / 4 - ((n - 1) % PERIOD)


def uptrend(n=520, start=50.0, daily=0.0022, amp=0.035, end_high=True):
    phase = _end_high(n) if end_high else 0.0
    return start * np.exp(np.arange(n) * daily) * _wave(n, amp, phase)


def downtrend(n=520, start=200.0, daily=-0.0022, amp=0.035):
    return start * np.exp(np.arange(n) * daily) * _wave(n, amp)


def classify(closes, **kw):
    ind = I.compute(frame(closes, **kw))
    return ind, R.classify(ind)


class TestLongTermRegime(unittest.TestCase):
    """Section 8: price position and 200 SMA slope, never price alone."""

    def test_above_a_rising_200_is_a_bull_regime(self):
        ind, v = classify(uptrend())
        self.assertEqual(v["long_term"]["regime"], R.BULLISH_LT)

    def test_below_a_falling_200_is_a_bear_regime(self):
        ind, v = classify(downtrend())
        self.assertEqual(v["long_term"]["regime"], R.BEARISH_LT)

    def test_above_a_falling_200_is_not_a_bull_regime(self):
        """The distinction the spec calls mandatory: a bounce inside a
        downtrend must not read the same as an uptrend."""
        path = np.concatenate([downtrend(470, 400.0, -0.0060),
                               uptrend(30, 24.0, 0.030, amp=0.01)])
        ind, v = classify(path)
        self.assertTrue(ind["price"] > ind["ma"]["SMA200"])
        self.assertEqual(I.slope_state(
            ind["slope"]["SMA200_5d_atr5"], "SMA200"), "falling")
        self.assertEqual(v["long_term"]["regime"], R.WEAK_BULL_LT)

    def test_below_a_rising_200_is_accumulation_not_a_bear(self):
        path = np.concatenate([uptrend(470, 50.0, 0.0015, amp=0.02),
                               downtrend(20, 101.2, -0.008, amp=0.006)])
        ind, v = classify(path)
        self.assertTrue(ind["price"] < ind["ma"]["SMA200"])
        self.assertEqual(v["long_term"]["regime"], R.ACCUMULATION_LT)


class TestPrimaryRegime(unittest.TestCase):
    def test_clean_uptrend_is_a_strong_long_term_bull(self):
        _, v = classify(uptrend())
        self.assertEqual(v["regime"], "STRONG_LONG_TERM_BULL")

    def test_clean_downtrend_is_a_breakdown(self):
        _, v = classify(downtrend())
        self.assertEqual(v["regime"], "LONG_TERM_BREAKDOWN")

    def test_shallow_dip_in_an_uptrend_stays_a_pullback(self):
        """Below the 8 EMA, still above a rising 50 — a pullback, and
        specifically not any flavour of bearish."""
        path = np.concatenate([uptrend(505, 50.0, 0.0022, amp=0.02),
                               downtrend(8, 151.0, -0.004, amp=0.004)])
        ind, v = classify(path)
        self.assertTrue(ind["price"] > ind["ma"]["SMA50"])
        self.assertIn(v["regime"], ("HEALTHY_MOMENTUM_PULLBACK",
                                    "MOMENTUM_COOLING"))
        self.assertNotIn(v["regime"], R.BEARISH_REGIMES)

    def test_loss_of_the_50_above_a_rising_200_is_a_deep_pullback(self):
        path = np.concatenate([uptrend(480, 50.0, 0.0022, amp=0.02),
                               downtrend(20, 143.6, -0.006, amp=0.006)])
        ind, v = classify(path)
        self.assertTrue(ind["price"] < ind["ma"]["SMA50"])
        self.assertTrue(ind["price"] > ind["ma"]["SMA200"])
        self.assertEqual(v["regime"], "DEEP_PULLBACK_IN_BULL_TREND")

    def test_every_shape_lands_in_the_canonical_list(self):
        for name, path in (("up", uptrend()), ("down", downtrend()),
                           ("flat", np.full(520, 100.0)),
                           ("noisy", 100 + np.sin(np.arange(520) / 9) * 12)):
            with self.subTest(shape=name):
                _, v = classify(path)
                self.assertIn(v["regime"], R.REGIMES)


class TestReversalNeedsSomethingToReverse(unittest.TestCase):
    def test_an_established_uptrend_is_not_a_confirmed_reversal(self):
        """NVDA regression: one close under the 200 SMA is a wick, and read
        as prior weakness it labelled a year-old uptrend a reversal."""
        _, v = classify(uptrend())
        self.assertEqual(v["reversal"]["status"], "NONE")

    def test_reclaiming_the_stack_after_real_weakness_is_a_reversal(self):
        path = np.concatenate([downtrend(460, 300.0, -0.0040),
                               uptrend(45, 47.0, 0.020, amp=0.01)])
        ind, v = classify(path)
        self.assertGreaterEqual(ind["closes_below_200_60d"] or 0,
                                R.PRIOR_STATE_CLOSES)
        self.assertIn(v["reversal"]["status"], ("EARLY_BULLISH_REVERSAL",
                                                "CONFIRMED_BULLISH_REVERSAL"))


class TestScore(unittest.TestCase):
    def test_a_clean_uptrend_scores_in_the_strong_band(self):
        ind, v = classify(uptrend())
        s = SC.compute(ind, v)
        self.assertGreaterEqual(s["score"], 80)
        self.assertEqual(s["coverage"], 100)

    def test_a_falling_200_keeps_the_score_out_of_the_strong_bands(self):
        ind, v = classify(downtrend())
        s = SC.compute(ind, v)
        self.assertLessEqual(s["score"], SC.DECLINING_200_CAP)

    def test_the_cap_binds_on_a_bounce_inside_a_downtrend(self):
        """Section 10's hard rule. This name has a bullish short-term stack
        and rising fast averages — without the cap the weighted sum votes it
        into a band it has not earned, because the one component that should
        be decisive is only worth 25 of 100."""
        path = np.concatenate([downtrend(470, 400.0, -0.0060),
                               uptrend(30, 24.0, 0.030, amp=0.01)])
        ind, v = classify(path)
        s = SC.compute(ind, v)
        uncapped = sum(SC.WEIGHTS[k] * x for k, x in s["components"].items()
                       if x is not None)
        live = sum(SC.WEIGHTS[k] for k, x in s["components"].items()
                   if x is not None)
        self.assertGreater(uncapped / live * 100, SC.DECLINING_200_CAP)
        self.assertTrue(s["capped"])
        self.assertEqual(s["score"], SC.DECLINING_200_CAP)

    def test_missing_components_renormalise_instead_of_scoring_zero(self):
        ind, v = classify(uptrend(90))       # no 200 SMA at 90 bars
        s = SC.compute(ind, v)
        self.assertIsNone(ind["ma"]["SMA200"])
        self.assertLess(s["coverage"], 100)
        self.assertIsNotNone(s["score"])
        self.assertIsNone(s["components"]["long_term"])


class TestExtensionAndEntry(unittest.TestCase):
    """Section 13: the trend and the entry are different questions."""

    def test_a_parabolic_leader_is_still_a_bull_but_not_an_entry(self):
        path = np.concatenate([uptrend(480), uptrend(40, 145.0, 0.030)])
        ind, v = classify(path)
        s = SC.compute(ind, v)
        e = ET.evaluate(ind, v, s)
        self.assertIn(v["regime"], R.BULLISH_REGIMES)
        self.assertIn(ind["extension_status"], ("VERY_EXTENDED", "PARABOLIC"))
        self.assertEqual(e["status"], ET.DO_NOT_CHASE)
        self.assertIsNotNone(e["wait_for"])

    def test_extension_is_measured_in_atr_not_percent(self):
        """Same +8% above the 21 EMA, different daily ranges: the quiet name
        is extended and the volatile one is not."""
        quiet = I.compute(frame(uptrend(300), spread=0.004))
        wild = I.compute(frame(uptrend(300), spread=0.05))
        self.assertAlmostEqual(quiet["dist_pct"]["EMA21"],
                               wild["dist_pct"]["EMA21"], places=6)
        self.assertGreater(quiet["dist_atr"]["EMA21"], wild["dist_atr"]["EMA21"])


class TestFilters(unittest.TestCase):
    def _row(self, **kw):
        base = {
            "price": 100.0, "score": 85, "above_200": True,
            "alignment": "FULL_BULLISH_ALIGNMENT", "structure": "HH/HL",
            "regime": "STRONG_LONG_TERM_BULL",
            "ma": {"EMA8": 99.0, "EMA21": 97.0, "SMA50": 92.0, "SMA200": 80.0},
            "slope_state": {"EMA8": "rising", "EMA21": "rising",
                            "SMA50": "rising", "SMA200": "rising"},
            "dist_atr": {"EMA8": 0.4, "EMA21": 1.0, "SMA50": 2.6},
            "cross": {"sma50_over_sma200": True, "ema8_over_ema21": True},
            "long_term_regime": "BULLISH_LONG_TERM_REGIME",
            "closes_below_50_30d": 0,
        }
        base.update(kw)
        return base

    def test_leader_and_momentum_filters_match_a_clean_leader(self):
        row = self._row()
        self.assertTrue(FL.long_term_leaders(row))
        self.assertTrue(FL.momentum_leaders(row))

    def test_dip_filter_needs_price_back_at_the_21_ema(self):
        self.assertFalse(FL.buy_the_dip(self._row()))          # +3% above it
        self.assertTrue(FL.buy_the_dip(self._row(price=96.0)))  # under it

    def test_dip_filter_ranks_by_proximity_not_by_score(self):
        """The best dip is the closest one, not the strongest one — the
        distinction every generic 'bullish' ranking gets backwards."""
        near = self._row(price=96.0, score=72,
                         dist_atr={"EMA8": -0.3, "EMA21": -0.1, "SMA50": 0.9})
        far = self._row(price=95.0, score=95,
                        dist_atr={"EMA8": -1.9, "EMA21": -1.4, "SMA50": 0.4})
        ranked = FL.apply([far, near], "C")
        self.assertEqual([r["score"] for r in ranked], [72, 95])

    def test_early_reversal_rejects_a_pullback_inside_an_uptrend(self):
        """Regression from the first live run: four of six names matched
        filter D, all of them leaders that had merely dipped under the 50
        SMA and climbed back. A trend continuing is not a trend reversing."""
        continuing = self._row(long_term_regime="BULLISH_LONG_TERM_REGIME",
                               closes_below_50_30d=6)
        self.assertFalse(FL.early_reversal(continuing))

        turning = self._row(long_term_regime="PULLBACK_POSSIBLE_ACCUMULATION",
                            closes_below_50_30d=6)
        self.assertTrue(FL.early_reversal(turning))

    def test_early_reversal_needs_time_spent_below_the_50(self):
        never_below = self._row(long_term_regime="RECOVERY_WEAK_BULL_REGIME",
                                closes_below_50_30d=0)
        self.assertFalse(FL.early_reversal(never_below))

    def test_short_filter_needs_a_falling_50_not_just_a_low_price(self):
        row = self._row(price=88.0, regime="MIXED_TRANSITION",
                        ma={"EMA8": 89.0, "EMA21": 90.0, "SMA50": 93.0,
                            "SMA200": 80.0})
        self.assertFalse(FL.breakdown_short(row))
        row["slope_state"]["SMA50"] = "falling"
        self.assertTrue(FL.breakdown_short(row))


class TestRow(unittest.TestCase):
    def test_row_carries_every_output_column(self):
        row = EN.row_from_frame("TEST", frame(uptrend()))
        for key in ("regime", "score", "long_term_trend", "intermediate_trend",
                    "short_term_trend", "alignment", "ma_slopes", "structure",
                    "dist_8ema", "dist_21ema", "dist_50sma", "dist_200sma",
                    "adx", "rsi", "rvol", "confidence", "extension_status",
                    "pullback_status", "reversal_status", "entry_status",
                    "key_reason", "risk"):
            self.assertIn(key, row)
        self.assertEqual(len(EN.format_line(row).split(" | ")),
                         len(EN.LINE_COLUMNS))

    def test_an_empty_frame_does_not_raise(self):
        row = EN.row_from_frame("EMPTY", pd.DataFrame())
        self.assertEqual(row["regime"], "MIXED_TRANSITION")
        self.assertIsNone(row["score"])

    def test_a_short_frame_is_classified_without_a_long_term_read(self):
        row = EN.row_from_frame("YOUNG", frame(uptrend(40)))
        self.assertTrue(row["insufficient_history"])
        self.assertIsNone(row["ma"].get("SMA200"))
        self.assertIn(row["regime"], R.REGIMES)

    def test_row_is_json_serialisable(self):
        """yfinance hands back numpy scalars all the way through and
        json.dumps refuses every one of them."""
        import json
        row = EN.row_from_frame("TEST", frame(uptrend()))
        json.dumps(EN._clean(row))


if __name__ == "__main__":
    unittest.main()
