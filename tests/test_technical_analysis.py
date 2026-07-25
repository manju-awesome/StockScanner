"""
Tests for core.technical_analysis — the pure indicator/structure math behind
the AI Technicals feature. No network, no Claude calls.
Run with: python -m unittest tests.test_technical_analysis
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from stockanalysis.core import technical_analysis as ta


def _df(closes, highs=None, lows=None, vols=None):
    closes = pd.Series(closes, dtype=float)
    n = len(closes)
    return pd.DataFrame({
        "Open": closes.shift(1).fillna(closes.iloc[0]),
        "High": pd.Series(highs, dtype=float) if highs is not None else closes + 1,
        "Low": pd.Series(lows, dtype=float) if lows is not None else closes - 1,
        "Close": closes,
        "Volume": pd.Series(vols, dtype=float) if vols is not None else pd.Series([1e6] * n),
    })


class TestIndicators(unittest.TestCase):
    def test_rsi_extremes(self):
        rising = pd.Series(np.linspace(100, 200, 60))
        falling = pd.Series(np.linspace(200, 100, 60))
        self.assertGreater(float(ta._rsi(rising).iloc[-1]), 90)
        self.assertLess(float(ta._rsi(falling).iloc[-1]), 10)

    def test_macd_sign_follows_trend(self):
        rising = pd.Series(np.linspace(100, 200, 80))
        macd, signal = ta._macd(rising)
        self.assertGreater(float(macd.iloc[-1]), 0)

    def test_atr_positive_and_scales_with_range(self):
        calm = _df(np.full(40, 100.0), highs=np.full(40, 100.5), lows=np.full(40, 99.5))
        wild = _df(np.full(40, 100.0), highs=np.full(40, 105.0), lows=np.full(40, 95.0))
        self.assertLess(float(ta._atr(calm).iloc[-1]), float(ta._atr(wild).iloc[-1]))

    def test_fib_levels_bracket_range(self):
        df = _df(np.linspace(100, 200, 50))
        fib = ta._fib_levels(df)
        self.assertEqual(fib["range_low"], float(df["Low"].min()))
        self.assertEqual(fib["range_high"], float(df["High"].max()))
        self.assertGreater(fib["fib_0.382"], fib["fib_0.618"])  # retracement from the high

    def test_volume_profile_finds_heavy_node(self):
        # heavy volume clustered near 150
        closes = np.concatenate([np.linspace(100, 200, 80), np.full(40, 150.0)])
        vols = np.concatenate([np.full(80, 1e6), np.full(40, 2e7)])
        nodes = ta._volume_profile(_df(closes, vols=vols))
        self.assertTrue(nodes[0]["price_low"] <= 150 <= nodes[0]["price_high"])


class TestStructure(unittest.TestCase):
    def test_uptrend_reads_hh_hl(self):
        self.assertEqual(ta._structure([110, 120], [100, 108]), "uptrend (HH + HL)")

    def test_downtrend_reads_lh_ll(self):
        self.assertEqual(ta._structure([120, 110], [108, 100]), "downtrend (LH + LL)")

    def test_insufficient_swings(self):
        self.assertEqual(ta._structure([110], []), "insufficient swings")

    def test_swings_find_local_extremes(self):
        # a clean peak at 150 and trough at 90 inside the series
        closes = np.concatenate([np.linspace(100, 150, 20), np.linspace(150, 90, 20),
                                 np.linspace(90, 120, 20)])
        highs, lows = ta._swings(_df(closes, highs=closes + 0.5, lows=closes - 0.5))
        self.assertTrue(any(abs(h - 150.5) < 3 for h in highs))
        self.assertTrue(any(abs(l - 89.5) < 3 for l in lows))


class TestGracefulDegradation(unittest.TestCase):
    def test_no_api_key_returns_error_dict(self):
        import os
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            out = ta.analyze_ticker("AMD")
            self.assertIn("ANTHROPIC_API_KEY", out["error"])
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
