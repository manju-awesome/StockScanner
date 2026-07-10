"""
Tests for compute_daily_metrics() — the pure, point-in-time core of
metrics.py that the backtest engine will call with historically-sliced bars.

No network: all frames are synthetic. Run with
    python -m unittest tests.test_metrics_point_in_time
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.metrics import DAILY_METRIC_KEYS, compute_daily_metrics


def make_daily(n: int = 300, seed: int = 7, end: str = "2026-07-08") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n, tz="America/New_York")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    spread = np.abs(rng.normal(0, 0.01, n))
    return pd.DataFrame({
        "Open":   close * (1 + rng.normal(0, 0.005, n)),
        "High":   close * (1 + spread),
        "Low":    close * (1 - spread),
        "Close":  close,
        "Volume": rng.integers(1_000_000, 50_000_000, n).astype(float),
    }, index=idx)


class TestComputeDailyMetrics(unittest.TestCase):

    def setUp(self):
        self.daily = make_daily()

    def test_empty_frame_returns_empty_dict(self):
        self.assertEqual(compute_daily_metrics(pd.DataFrame(), 5.0), {})

    def test_produces_expected_keys(self):
        row = compute_daily_metrics(self.daily, 5.0,
                                    asof_date=self.daily.index[-1].date())
        # Call_* keys are attached later by compute_call_candidate, not here
        expected = {k for k in DAILY_METRIC_KEYS if not k.startswith("Call_")}
        self.assertTrue(expected <= set(row),
                        f"missing: {expected - set(row)}")
        for k in ("52W High", "8EMA", "21EMA", "ATR20", "RSI_14", "RS"):
            self.assertIsNotNone(row[k], f"{k} unexpectedly None")

    def test_asof_on_last_bar_treats_it_as_live_session(self):
        # When the last bar carries the as-of date it is the in-progress
        # session, so prev-day levels must come from the bar before it.
        asof = self.daily.index[-1].date()
        row = compute_daily_metrics(self.daily, 5.0, asof_date=asof)
        prev_bar = self.daily.iloc[-2]
        self.assertEqual(row["Prev-Day High"], round(float(prev_bar["High"]), 2))
        self.assertEqual(row["Prev-Day Low"],  round(float(prev_bar["Low"]),  2))
        self.assertIsNotNone(row["Gap%"])   # today's open exists

    def test_asof_after_last_bar_uses_last_bar_as_prev_day(self):
        # Scanning before today's bar exists (or backtesting "next morning"):
        # the last bar is the most recent COMPLETED session.
        asof = self.daily.index[-1].date() + timedelta(days=1)
        row = compute_daily_metrics(self.daily, 5.0, asof_date=asof)
        last_bar = self.daily.iloc[-1]
        self.assertEqual(row["Prev-Day High"], round(float(last_bar["High"]), 2))
        self.assertIsNone(row["Gap%"])      # no bar for the as-of session yet

    def test_point_in_time_no_lookahead(self):
        # Metrics as of day D must be unaffected by anything after D:
        # spike a post-cut bar and verify the sliced result doesn't move.
        cut = self.daily.index[199]
        row_from_slice = compute_daily_metrics(
            self.daily.loc[:cut], 5.0, asof_date=cut.date())
        spiked = self.daily.copy()
        spiked.loc[spiked.index[-1], "High"] = 1e6
        row_spiked = compute_daily_metrics(
            spiked.loc[:cut], 5.0, asof_date=cut.date())
        self.assertEqual(row_from_slice, row_spiked)
        self.assertLess(row_spiked["52W High"], 1e6)

    def test_days_since_52w_high_uses_asof_not_wall_clock(self):
        daily = self.daily.copy()
        high_pos = 250
        daily.loc[daily.index[high_pos], "High"] = daily["High"].max() * 2
        asof = daily.index[-1].date()
        row = compute_daily_metrics(daily, 5.0, asof_date=asof)
        expected = (asof - daily.index[high_pos].date()).days
        self.assertEqual(row["Days_Since_52W_High"], expected)

    def test_current_price_falls_back_to_last_close(self):
        asof = self.daily.index[-1].date()
        row = compute_daily_metrics(self.daily, 5.0, asof_date=asof)
        last_close = float(self.daily["Close"].iloc[-1])
        self.assertAlmostEqual(row["Pct_vs_8EMA"],
                               round((last_close / row["8EMA"] - 1) * 100, 2))

    def test_canslim_uses_eps_growth_argument(self):
        asof = self.daily.index[-1].date()
        row = compute_daily_metrics(self.daily, 5.0, asof_date=asof,
                                    eps_growth=None)
        self.assertFalse(row["CANSLIM_Pass"])   # no fundamentals → can't pass

    def test_short_history_yields_none_mas(self):
        short = make_daily(n=40)
        row = compute_daily_metrics(short, 5.0,
                                    asof_date=short.index[-1].date())
        self.assertIsNone(row["200MA"])
        self.assertIsNone(row["50MA"])
        self.assertIsNone(row["RS"])            # < 63 bars
        self.assertIsNotNone(row["8EMA"])


if __name__ == "__main__":
    unittest.main()
