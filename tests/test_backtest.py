"""
Tests for the backtest package: resolve_long (the outcome rules shared with
the live tracker), the walk-forward engine on synthetic data, and the
refactored signal_tracker.update_outcomes.

No network. Run with:
    python -m unittest tests.test_backtest
"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.backtest.resolve import resolve_long


def bars(rows, start="2026-01-05"):
    """Build a daily OHLCV frame from (open, high, low, close) tuples."""
    idx = pd.bdate_range(start=start, periods=len(rows))
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c,
                         "Volume": [1e6] * len(rows)}, index=idx)


SIG = date(2026, 1, 5)          # a Monday — first bar of the frames above


class TestResolveLiveMode(unittest.TestCase):
    """require_trigger=False, tie='target' — the live tracker's conventions."""

    def resolve(self, prices, **kw):
        args = dict(entry=100.0, stop=95.0, target=110.0,
                    signal_date=SIG, asof_date=date(2026, 3, 1),
                    tie="target", require_trigger=False)
        args.update(kw)
        return resolve_long(prices, **args)

    def test_target_hit(self):
        prices = bars([(100, 102, 99, 101), (101, 111, 100, 110)])
        res = self.resolve(prices)
        self.assertEqual(res["outcome"], "target_hit")
        self.assertEqual(res["outcome_price"], 110.0)
        self.assertEqual(res["realized_r_multiple"], 2.0)   # +10 / risk 5

    def test_stop_hit(self):
        prices = bars([(100, 102, 99, 101), (101, 103, 94, 95)])
        res = self.resolve(prices)
        self.assertEqual(res["outcome"], "stop_hit")
        self.assertEqual(res["realized_r_multiple"], -1.0)

    def test_same_bar_tie_goes_to_target(self):
        prices = bars([(100, 111, 94, 105)])     # touches both on signal day
        res = self.resolve(prices)
        self.assertEqual(res["outcome"], "target_hit")

    def test_expiry(self):
        rows = [(100, 101, 99, 100)] * 30        # drifts nowhere for 30 bars
        res = self.resolve(bars(rows), expiry_days=20)
        self.assertEqual(res["outcome"], "expired_no_move")
        self.assertEqual(res["outcome_price"], 100.0)
        # exit must come from INSIDE the expiry window
        self.assertLessEqual(res["outcome_date"], SIG + timedelta(days=20))

    def test_still_open(self):
        prices = bars([(100, 101, 99, 100)] * 3)
        res = self.resolve(prices, asof_date=SIG + timedelta(days=4))
        self.assertEqual(res["outcome"], "open")

    def test_hit_after_expiry_window_does_not_count(self):
        # flat for 20 bars, target touched on bar 25 — expired, not target_hit
        rows = [(100, 101, 99, 100)] * 24 + [(100, 115, 99, 114)]
        res = self.resolve(bars(rows), expiry_days=20)
        self.assertEqual(res["outcome"], "expired_no_move")


class TestResolveTriggerMode(unittest.TestCase):
    """require_trigger=True, tie='stop' — the backtest's conventions."""

    def resolve(self, prices, **kw):
        args = dict(entry=105.0, stop=100.0, target=115.0,
                    signal_date=SIG, asof_date=date(2026, 3, 1),
                    tie="stop", require_trigger=True, trigger_window_bars=5)
        args.update(kw)
        return resolve_long(prices, **args)

    def test_signal_day_bar_cannot_trigger(self):
        # entry touched ONLY on the signal day itself — plan never fills
        rows = [(104, 106, 103, 104)] + [(104, 104.5, 103, 104)] * 6
        res = self.resolve(bars(rows))
        self.assertEqual(res["outcome"], "no_trigger")
        self.assertIsNone(res["fill_price"])

    def test_intrabar_trigger_fills_at_entry(self):
        rows = [(104, 104, 103, 104), (104, 106, 103.5, 105.5),
                (105.5, 116, 105, 115)]
        res = self.resolve(bars(rows))
        self.assertEqual(res["fill_price"], 105.0)
        self.assertEqual(res["outcome"], "target_hit")
        self.assertEqual(res["realized_r_multiple"], 2.0)   # (115-105)/5

    def test_gap_open_fills_at_open(self):
        rows = [(104, 104, 103, 104), (108, 110, 107, 109),
                (109, 116, 108, 115)]
        res = self.resolve(bars(rows))
        self.assertEqual(res["fill_price"], 108.0)           # not 105
        # R uses the ACTUAL fill: risk 8, reward 7
        self.assertEqual(res["realized_r_multiple"], round(7 / 8, 2))

    def test_no_trigger_within_window(self):
        rows = [(104, 104.5, 103, 104)] * 8                  # never reaches 105
        res = self.resolve(bars(rows))
        self.assertEqual(res["outcome"], "no_trigger")

    def test_same_bar_fill_and_stop_ties_to_stop(self):
        rows = [(104, 104, 103, 104), (104, 106, 99, 100)]   # fill then flush
        res = self.resolve(bars(rows))
        self.assertEqual(res["outcome"], "stop_hit")
        self.assertEqual(res["realized_r_multiple"], -1.0)

    def test_triggered_then_expired(self):
        rows = [(104, 104, 103, 104), (104, 106, 103.5, 105.5)] \
             + [(105.5, 106, 104.5, 105.5)] * 25
        res = self.resolve(bars(rows), expiry_days=20)
        self.assertEqual(res["outcome"], "expired_no_move")
        self.assertEqual(res["fill_price"], 105.0)
        self.assertEqual(res["outcome_price"], 105.5)


class TestUpdateOutcomesUsesSharedRules(unittest.TestCase):
    """The live tracker resolves through resolve_long with legacy settings."""

    def test_update_outcomes_end_to_end(self):
        from stockanalysis.reporting import signal_tracker as st

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "signal_log.csv"
            pd.DataFrame([{
                "ticker": "FAKE", "category": "Momentum", "grade": "A",
                "signal_date": "2026-01-05",
                "entry_price": 100.0, "stop_price": 95.0,
                "t1_price": 110.0, "t2_price": 120.0, "rr_t2": 4.0,
                "outcome": "open", "outcome_date": None,
                "outcome_price": None, "realized_r_multiple": None,
            }]).to_csv(log_path, index=False)

            prices = bars([(100, 102, 99, 101), (101, 111, 100, 110)])
            orig = st._fetch_prices
            st._fetch_prices = lambda ticker, start: prices
            try:
                df = st.update_outcomes(log_path=log_path)
            finally:
                st._fetch_prices = orig

            self.assertEqual(df.loc[0, "outcome"], "target_hit")
            self.assertEqual(df.loc[0, "outcome_price"], 110.0)
            self.assertEqual(df.loc[0, "realized_r_multiple"], 2.0)  # (110-100)/5


class TestEngineOnSyntheticData(unittest.TestCase):
    """Walk-forward run on canned data: no network, deterministic."""

    @staticmethod
    def make_universe():
        n = 500
        idx = pd.bdate_range(end="2026-07-01", periods=n)
        rng = np.random.default_rng(11)

        # Steady uptrend near its highs — a Momentum candidate
        drift = np.cumsum(rng.normal(0.0015, 0.004, n))
        close = 100 * np.exp(drift)
        up = pd.DataFrame({
            "Open": close * 0.999, "High": close * 1.006,
            "Low": close * 0.994, "Close": close,
            "Volume": np.full(n, 5e6),
        }, index=idx)

        # $2 stock — fails the price gate every day, must yield nothing
        penny = up.copy()
        for col in ("Open", "High", "Low", "Close"):
            penny[col] = penny[col] / 100.0

        # Flat benchmark → RS ≈ the stock's own 3m return
        qc = 100 + rng.normal(0, 0.3, n).cumsum() * 0.1
        qqq = pd.DataFrame({"Open": qc, "High": qc * 1.002,
                            "Low": qc * 0.998, "Close": qc,
                            "Volume": np.full(n, 1e7)}, index=idx)
        return {"UPTREND": up, "PENNY": penny}, qqq

    def test_run_backtest(self):
        from stockanalysis.backtest.engine import run_backtest

        daily, qqq = self.make_universe()
        start, end = date(2026, 4, 1), date(2026, 7, 1)
        df = run_backtest(daily, qqq, start, end)

        self.assertFalse(df.empty, "uptrend ticker should emit signals")
        self.assertEqual(set(df["ticker"]), {"UPTREND"})
        self.assertTrue((df["category"] == "Momentum").all())
        self.assertTrue(df["outcome"].isin(
            ["open", "no_trigger", "target_hit", "stop_hit",
             "expired_no_move"]).all())
        # one open signal per (ticker, category): consecutive signal dates
        # must be at least expiry_days apart
        dates = sorted(df["signal_date"])
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        self.assertTrue(all(g >= 20 for g in gaps), f"cooldown violated: {gaps}")
        # no lookahead in resolution: outcomes only from bars after signal
        filled = df[df["fill_date"].notna()]
        self.assertTrue((filled["fill_date"] > filled["signal_date"]).all())


if __name__ == "__main__":
    unittest.main()
