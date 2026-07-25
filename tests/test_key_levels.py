"""
Tests for compute_key_levels() — the Key Level Score / S1 / R1 engine.

No network: all frames are synthetic. Run with
    python -m unittest tests.test_key_levels
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.key_levels import (
    SCORE_WEIGHTS, KEY_LEVEL_DEFAULTS, compute_key_levels,
)

STRONG_LEVEL = 100.0
ISOLATED_LEVEL = 118.5


def make_daily(n: int = 300, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-07-08", periods=n, tz="America/New_York")
    close = 115 * np.exp(np.cumsum(rng.normal(0.0002, 0.015, n)))
    spread = np.abs(rng.normal(0, 0.008, n))
    return pd.DataFrame({
        "Open":   close * (1 + rng.normal(0, 0.004, n)),
        "High":   close * (1 + spread),
        "Low":    close * (1 - spread),
        "Close":  close,
        "Volume": rng.integers(1_000_000, 20_000_000, n).astype(float),
    }, index=idx)


def make_intraday(n_days: int = 20, bars_per_day: int = 78, seed: int = 11):
    """Gentle random walk around 115, with:
      - 8 well-spaced, engineered touches of STRONG_LEVEL (100.0), each a big
        volume spike followed by a strong 20-bar bounce away from the level.
      - 1 isolated touch of ISOLATED_LEVEL (118.5): ordinary volume, no
        follow-through — should NOT clear the score threshold.
    """
    n = n_days * bars_per_day
    idx = pd.date_range("2026-06-01 09:30", periods=n, freq="5min", tz="America/New_York")
    rng = np.random.default_rng(seed)

    close = 115 + np.cumsum(rng.normal(0, 0.05, n))
    close = np.clip(close, 108, 122)
    spread = np.abs(rng.normal(0.05, 0.02, n))
    high = close + spread
    low  = close - spread
    openp = close + rng.normal(0, 0.02, n)
    vol = rng.integers(50_000, 150_000, n).astype(float)

    touch_bars = np.linspace(80, n - 200, 8, dtype=int)
    horizon = 20
    for b in touch_bars:
        low[b], high[b] = STRONG_LEVEL, STRONG_LEVEL + 0.3
        close[b], openp[b] = STRONG_LEVEL + 0.15, STRONG_LEVEL + 0.25
        vol[b] = 500_000
        for k in range(1, horizon + 1):
            if b + k < n:
                bump = 0.15 * k
                close[b + k] = STRONG_LEVEL + bump
                high[b + k]  = close[b + k] + 0.1
                low[b + k]   = close[b + k] - 0.1
                openp[b + k] = close[b + k] - 0.05

    b2 = n - 60  # isolated, un-engineered touch — ordinary volume/follow-through
    low[b2], high[b2] = ISOLATED_LEVEL, ISOLATED_LEVEL + 0.2
    close[b2] = ISOLATED_LEVEL + 0.1

    return pd.DataFrame(
        {"Open": openp, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


class TestKeyLevelWeights(unittest.TestCase):

    def test_weights_sum_to_100_percent(self):
        self.assertAlmostEqual(sum(SCORE_WEIGHTS.values()), 1.0, places=9)

    def test_weights_match_spec(self):
        self.assertEqual(SCORE_WEIGHTS, {
            "touch": 0.25, "touch_volume": 0.25, "recency": 0.15,
            "atr_reaction": 0.10, "gap_earnings": 0.10,
            "ma_confluence": 0.05, "hvn_poc": 0.10,
        })


class TestComputeKeyLevels(unittest.TestCase):

    def setUp(self):
        self.daily = make_daily()
        self.intraday = make_intraday()
        self.price = 121.0  # above STRONG_LEVEL -> STRONG_LEVEL is a support candidate

    def test_returns_defaults_on_empty_intraday(self):
        result = compute_key_levels(
            "TEST", self.price, self.daily, None,
            atr20=2.0, ma50=99.5, ma200=112.0,
            prev_day_high=122.0, prev_day_low=118.0,
            prior_52w_high=130.0, prior_52w_low=95.0,
        )
        self.assertEqual(result, KEY_LEVEL_DEFAULTS)

    def test_returns_defaults_without_atr(self):
        result = compute_key_levels(
            "TEST", self.price, self.daily, self.intraday,
            atr20=None, ma50=110.0, ma200=112.0,
            prev_day_high=122.0, prev_day_low=118.0,
            prior_52w_high=130.0, prior_52w_low=95.0,
        )
        self.assertEqual(result, KEY_LEVEL_DEFAULTS)

    def test_never_raises_on_short_intraday(self):
        short = self.intraday.head(10)
        result = compute_key_levels(
            "TEST", self.price, self.daily, short,
            atr20=2.0, ma50=99.5, ma200=112.0,
            prev_day_high=122.0, prev_day_low=118.0,
            prior_52w_high=130.0, prior_52w_low=95.0,
        )
        self.assertEqual(result, KEY_LEVEL_DEFAULTS)

    def test_strong_repeated_level_qualifies_as_support(self):
        result = compute_key_levels(
            "TEST", self.price, self.daily, self.intraday,
            atr20=2.0, ma50=99.5, ma200=112.0,
            prev_day_high=122.0, prev_day_low=118.0,
            prior_52w_high=130.0, prior_52w_low=95.0,
        )
        self.assertIsNotNone(result["S1"])
        self.assertAlmostEqual(result["S1"], STRONG_LEVEL, delta=0.5)
        self.assertIsNotNone(result["Key_Level_Score"])
        self.assertGreaterEqual(result["Key_Level_Score"], 75.0)
        self.assertLessEqual(result["Key_Level_Score"], 100.0)
        self.assertGreaterEqual(result["Touches"], 3)
        self.assertTrue(result["Volume_Confirmation"])
        self.assertIsNotNone(result["Bounce_Probability"])
        self.assertGreaterEqual(result["Bounce_Probability"], 0.0)
        self.assertLessEqual(result["Bounce_Probability"], 100.0)
        # isolated, un-engineered single touch must not become S1/R1 itself
        self.assertNotAlmostEqual(result["S1"] or -1, ISOLATED_LEVEL, delta=0.5)
        if result["R1"] is not None:
            self.assertNotAlmostEqual(result["R1"], ISOLATED_LEVEL, delta=0.5)

    def test_distance_and_rr_consistent_with_price(self):
        result = compute_key_levels(
            "TEST", self.price, self.daily, self.intraday,
            atr20=2.0, ma50=99.5, ma200=112.0,
            prev_day_high=122.0, prev_day_low=118.0,
            prior_52w_high=130.0, prior_52w_low=95.0,
        )
        if result["S1"] is not None:
            expected = round((self.price - result["S1"]) / self.price * 100, 2)
            self.assertEqual(result["Dist_to_Support%"], expected)
        if result["S1"] is not None and result["R1"] is not None:
            expected_rr = round((result["R1"] - self.price) / (self.price - result["S1"]), 2)
            self.assertEqual(result["RR_to_Resistance"], expected_rr)


if __name__ == "__main__":
    unittest.main()
