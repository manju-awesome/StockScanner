"""
Tests for backtest.report — synthetic signal frames, no network.
Run with: python -m unittest tests.test_report
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.backtest.report import (
    build_report, equity_curve, sec_regime, to_html,
)


def make_signals() -> pd.DataFrame:
    rows = [
        # ticker, signal, category, grade, rr, outcome, fill, out_date, out_px, R
        ("AAA", "2026-01-05", "Momentum", "A", 2.5, "target_hit",
         "2026-01-06", "2026-01-12", 110.0, 2.0),
        ("BBB", "2026-01-07", "Momentum", "B", 1.8, "stop_hit",
         "2026-01-08", "2026-01-15", 95.0, -1.0),
        ("CCC", "2026-02-02", "VCP Setup", "B", 3.5, "target_hit",
         "2026-02-04", "2026-02-20", 120.0, 1.5),
        ("DDD", "2026-02-09", "Turnaround", "C", 2.2, "expired_no_move",
         "2026-02-10", "2026-03-02", 101.0, 0.1),
        ("EEE", "2026-03-02", "Momentum", "A", 4.0, "no_trigger",
         None, "2026-03-09", None, None),
        ("FFF", "2026-03-16", "Momentum", "B", 2.9, "open",
         "2026-03-17", None, None, None),
    ]
    df = pd.DataFrame(rows, columns=[
        "ticker", "signal_date", "category", "grade", "rr_t2", "outcome",
        "fill_date", "outcome_date", "outcome_price", "realized_r_multiple"])
    for col in ("signal_date", "fill_date", "outcome_date"):
        df[col] = pd.to_datetime(df[col])
    df["fill_price"] = df["fill_date"].notna().map({True: 100.0, False: None})
    df["entry"] = 100.0
    df["stop"] = 95.0
    return df


def make_qqq(uptrend: bool = True) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", "2026-04-01")
    base = np.linspace(100, 150, len(idx)) if uptrend \
        else np.linspace(150, 100, len(idx))
    return pd.DataFrame({"Close": base}, index=idx)


class TestReport(unittest.TestCase):

    def setUp(self):
        self.df = make_signals()

    def test_equity_curve_is_cumulative_by_resolution_date(self):
        curve = equity_curve(self.df)
        self.assertEqual(len(curve), 4)              # 4 resolved signals
        self.assertAlmostEqual(curve.iloc[-1], 2.0 - 1.0 + 1.5 + 0.1)
        self.assertTrue(curve.index.is_monotonic_increasing)

    def test_regime_split_uptrend_is_all_risk_on(self):
        text = sec_regime(self.df, make_qqq(uptrend=True))
        self.assertIn("risk-on", text)
        self.assertNotIn("risk-off", text)

    def test_build_report_contains_all_sections_and_numbers(self):
        text, sections = build_report(self.df, qqq=make_qqq(), spy=None)
        names = [n for n, _ in sections]
        for expected in ("Overview", "By category", "By grade",
                         "R:R-to-T2 buckets", "Market regime at signal",
                         "Equity curve (1% risk per signal)", "Monthly"):
            self.assertIn(expected, names)
        self.assertIn("Signals 6", text)
        self.assertIn("+2.6R", text)                 # total from equity curve

    def test_html_report_renders_svg_curve(self):
        _, sections = build_report(self.df)
        html = to_html("t", sections, equity_curve(self.df))
        self.assertIn("<svg", html)
        self.assertIn("+2.6R", html)
        self.assertIn("Assumptions", html)


if __name__ == "__main__":
    unittest.main()
