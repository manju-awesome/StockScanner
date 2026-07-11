"""
Tests for core.market_regime — synthetic pulse dicts, no network.
Run with: python -m unittest tests.test_market_regime
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.market_regime import compute_regime, REGIME_SIZE_MULT


def pulse(vix=15.0, spy="STRONG", qqq="STRONG", breadth=70.0) -> dict:
    return {
        "vix": {"level": vix},
        "spy": {"strength": spy, "breadth_pct": breadth},
        "qqq": {"strength": qqq},
    }


class TestRegimeFromPulse(unittest.TestCase):
    def test_calm_strong_tape_is_bullish(self):
        r = compute_regime(pulse=pulse())
        # +2 VIX, +1 SPY, +1 QQQ, +1 breadth = 5
        self.assertEqual(r["regime"], "Bullish")
        self.assertEqual(r["score"], 5)
        self.assertEqual(r["multipliers"], REGIME_SIZE_MULT["Bullish"])
        self.assertEqual(r["source"], "market_pulse")

    def test_fear_weak_tape_is_defensive(self):
        r = compute_regime(pulse=pulse(vix=28.0, spy="WEAK", qqq="WEAK",
                                       breadth=30.0))
        # -2 -1 -1 -1 = -5
        self.assertEqual(r["regime"], "Defensive")
        self.assertEqual(r["multipliers"]["day"], 0.25)
        self.assertEqual(r["multipliers"]["longterm"], 0.5)

    def test_mixed_tape_is_neutral(self):
        r = compute_regime(pulse=pulse(vix=21.0, spy="STRONG", qqq="NEUTRAL",
                                       breadth=50.0))
        # -1 +1 +0 +0 = 0
        self.assertEqual(r["regime"], "Neutral")
        self.assertEqual(r["multipliers"]["longterm"], 1.0)
        self.assertEqual(r["multipliers"]["day"], 0.5)

    def test_drivers_are_explained(self):
        r = compute_regime(pulse=pulse(vix=28.0))
        self.assertTrue(any("VIX 28.0" in d for d in r["drivers"]))


class TestRegimeFallbacks(unittest.TestCase):
    def test_rows_breadth_fallback(self):
        rows = [{"Above_200MA": True}] * 8 + [{"Above_200MA": False}] * 2
        r = compute_regime(pulse=None, rows=rows)
        self.assertEqual(r["source"], "scan_breadth")
        self.assertEqual(r["regime"], "Neutral")   # capped at +1, below Bullish
        self.assertEqual(r["score"], 1)

    def test_weak_scan_breadth_stays_neutral_not_defensive(self):
        rows = [{"Above_200MA": False}] * 10
        r = compute_regime(pulse=None, rows=rows)
        self.assertEqual(r["score"], -1)            # capped; -1 > DEFENSIVE_MAX
        self.assertEqual(r["regime"], "Neutral")

    def test_too_few_rows_is_unknown(self):
        r = compute_regime(pulse=None, rows=[{"Above_200MA": True}] * 5)
        self.assertEqual(r["source"], "none")
        self.assertEqual(r["regime"], "Neutral")
        # unknown regime must NOT shrink sizing
        self.assertEqual(r["multipliers"]["day"], 1.0)

    def test_empty_pulse_falls_through(self):
        r = compute_regime(pulse={}, rows=None)
        self.assertEqual(r["source"], "none")


if __name__ == "__main__":
    unittest.main()
