"""
Tests for reporting.portfolio — temp CSV files + synthetic scan rows.
Run with: python -m unittest tests.test_portfolio
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.reporting.portfolio import (
    load_positions, build_portfolio_view, portfolio_totals,
    allocation_summary, cap_bucket, PORTFOLIO_VALUE, SMALLCAP_MAX_PCT,
)

CSV = """Ticker,Shares,Avg_Cost,Entry_Date,Strategy,Stop,Target,Notes
NVDA,25,145.50,2026-05-12,longterm,,260,core AI
ARM,40,290.00,2026-07-08,swing,285.00,340,pullback add
HOOD,0,,,watch,,,waiting for base
,10,50,,swing,,,no ticker — skipped
BAD,5,10,,daytrayde,,,typo strategy -> watch
"""


def scan_row(ticker, price, **kw) -> dict:
    row = {"Ticker": ticker, "Current Price": price, "Category": "Momentum",
           "Grade": "B", "RS_Rank": 70, "Above_200MA": True,
           "Investment_Score": 80, "Investment_Pass": True,
           "Swing_Score": 50, "DayTrade_Score": 40, "Days_To_Earnings": 30}
    row.update(kw)
    return row


class TestLoadPositions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False)
        self.tmp.write(CSV)
        self.tmp.close()

    def test_load_parses_and_sanitises(self):
        pos = load_positions(self.tmp.name)
        self.assertEqual(len(pos), 4)              # blank-ticker row skipped
        nvda = pos[0]
        self.assertEqual(nvda["Ticker"], "NVDA")
        self.assertEqual(nvda["Shares"], 25.0)
        self.assertEqual(nvda["Avg_Cost"], 145.50)
        self.assertEqual(nvda["Strategy"], "longterm")
        bad = [p for p in pos if p["Ticker"] == "BAD"][0]
        self.assertEqual(bad["Strategy"], "watch")  # typo falls back to watch

    def test_missing_file_is_empty(self):
        self.assertEqual(load_positions("/nonexistent/portfolio.csv"), [])


class TestPortfolioView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False)
        self.tmp.write(CSV)
        self.tmp.close()
        self.positions = load_positions(self.tmp.name)

    def test_gain_join(self):
        rows = [scan_row("NVDA", 210.46), scan_row("ARM", 327.45),
                scan_row("HOOD", 112.61), scan_row("BAD", 8.0)]
        view = build_portfolio_view(self.positions, rows)
        nvda = [p for p in view if p["Ticker"] == "NVDA"][0]
        self.assertAlmostEqual(nvda["Gain_Dollars"], (210.46 - 145.50) * 25, places=2)
        self.assertAlmostEqual(nvda["Gain_Pct"], 44.65, places=1)
        hood = [p for p in view if p["Ticker"] == "HOOD"][0]
        self.assertTrue(hood["Is_Watch"])
        self.assertIsNone(hood["Gain_Dollars"])

    def test_days_held_and_risk(self):
        from datetime import date
        rows = [scan_row("ARM", 327.45)]
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "ARM"], rows)
        arm = view[0]
        self.assertEqual(arm["Days_Held"],
                         (date.today() - date(2026, 7, 8)).days)
        # risk = (price - stop) * shares
        self.assertAlmostEqual(arm["Risk"], (327.45 - 285.00) * 40, places=2)

    def test_next_action_priorities(self):
        # stop breach outranks everything
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "ARM"],
            [scan_row("ARM", 280.0)])
        self.assertTrue(view[0]["Next_Action"].startswith("EXIT"))
        # target hit → TRIM
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "ARM"],
            [scan_row("ARM", 345.0)])
        self.assertTrue(view[0]["Next_Action"].startswith("TRIM"))
        # clean position with big gain → raise stop
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "NVDA"],
            [scan_row("NVDA", 210.0)])
        self.assertIn("raise stop", view[0]["Next_Action"])
        # watch row with a ready setup → ENTER?
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "HOOD"],
            [scan_row("HOOD", 112.0, Swing_Pass=True)])
        self.assertTrue(view[0]["Next_Action"].startswith("ENTER?"))

    def test_stop_breach_alert(self):
        rows = [scan_row("ARM", 280.00)]           # below the 285 stop
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "ARM"], rows)
        self.assertTrue(any("below stop" in a for a in view[0]["Alerts"]))

    def test_longterm_degradation_alerts(self):
        rows = [scan_row("NVDA", 210.0, Above_200MA=False,
                         Investment_Pass=False,
                         Investment_Reason="PASSED: x | FAILED: RS_Rank>80")]
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "NVDA"], rows)
        alerts = " | ".join(view[0]["Alerts"])
        self.assertIn("below 200MA", alerts)
        self.assertIn("filters degraded", alerts)

    def test_swing_earnings_alert(self):
        rows = [scan_row("ARM", 300.0, Days_To_Earnings=3)]
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "ARM"], rows)
        self.assertTrue(any("earnings in 3d" in a for a in view[0]["Alerts"]))

    def test_missing_from_scan_flags_not_crashes(self):
        view = build_portfolio_view(self.positions, rows=[])
        self.assertTrue(all("not in today's scan" in p["Alerts"][0]
                            for p in view))

    def test_totals(self):
        rows = [scan_row("NVDA", 200.0), scan_row("ARM", 300.0),
                scan_row("HOOD", 100.0), scan_row("BAD", 8.0)]
        view = build_portfolio_view(self.positions, rows)
        t = portfolio_totals(view)
        self.assertEqual(t["positions"], 3)        # NVDA, ARM, BAD hold shares
        self.assertEqual(t["watching"], 1)
        self.assertAlmostEqual(
            t["total_value"], 200 * 25 + 300 * 40 + 8 * 5, places=2)


class TestAllocation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        self.tmp.write(CSV)
        self.tmp.close()
        self.positions = load_positions(self.tmp.name)

    def test_cap_buckets(self):
        self.assertEqual(cap_bucket(3e12), "Mega")
        self.assertEqual(cap_bucket(50e9), "Large")
        self.assertEqual(cap_bucket(5e9), "Mid")
        self.assertEqual(cap_bucket(8e8), "Small")
        self.assertIsNone(cap_bucket(None))

    def test_alloc_pct_against_portfolio_value(self):
        rows = [scan_row("NVDA", 200.0, MarketCap=3e12, Sector="Technology")]
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "NVDA"], rows)
        nvda = view[0]
        self.assertEqual(nvda["Cap"], "Mega")
        self.assertAlmostEqual(
            nvda["Alloc_Pct"], 200.0 * 25 / PORTFOLIO_VALUE * 100, places=1)

    def test_allocation_summary_and_smallcap_warning(self):
        rows = [scan_row("NVDA", 200.0, MarketCap=3e12, Sector="Technology"),
                scan_row("ARM", 300.0, MarketCap=1e9, Sector="Technology")]
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] in ("NVDA", "ARM")], rows)
        alloc = allocation_summary(view)
        caps = dict(alloc["caps"])
        small_pct = 300.0 * 40 / PORTFOLIO_VALUE * 100
        self.assertAlmostEqual(caps["Small"], round(small_pct, 1), places=1)
        if small_pct > SMALLCAP_MAX_PCT:
            self.assertTrue(any("Small-cap" in w for w in alloc["warnings"]))

    def test_totals_include_portfolio_value_and_cash(self):
        rows = [scan_row("NVDA", 200.0)]
        view = build_portfolio_view(
            [p for p in self.positions if p["Ticker"] == "NVDA"], rows)
        t = portfolio_totals(view)
        self.assertEqual(t["portfolio_value"], PORTFOLIO_VALUE)
        self.assertAlmostEqual(t["cash"], PORTFOLIO_VALUE - 200.0 * 25, places=2)


if __name__ == "__main__":
    unittest.main()
