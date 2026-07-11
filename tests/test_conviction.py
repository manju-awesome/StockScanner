"""
Tests for core.conviction — synthetic rows, no network.
Run with: python -m unittest tests.test_conviction
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.conviction import (
    compute_conviction, attach_conviction, daily_opportunity,
    score_quality, score_setup, score_timing,
    recovery_stage, recovery_candidates,
)


def strong_row(**overrides) -> dict:
    """A near-perfect setup: quality + pattern + timing all green."""
    row = {
        "Ticker": "GOOD", "Category": "Momentum-Pullback", "Grade": "A",
        "Entry_Gate_Pass": True,
        # quality
        "EPS_Growth%": 40.0, "Revenue": 30.0, "FCF_Positive": True,
        "EarningsBeat": True, "Inst_Own%": 70.0, "Inst_Own_Chg": 1.5,
        "CANSLIM_Pass": True,
        # setup
        "RR_T2": 3.2, "ATR Shrinking": True, "BB_PctB": 0.15,
        "Pullback_Vol_Ratio": 0.7,
        # timing
        "RVOL_Intraday": 2.1, "Above_VWAP": True, "RSI_14": 50.0,
        "Gap%": 2.5, "ORB_Status": "above", "Pct_vs_8EMA": -1.0,
        "ADX_14": 30.0,
        # context
        "RS_Rank": 90, "Above_200MA": True, "Price_vs_200MA%": 20.0,
        "ATR_Pct": 2.5, "Days_To_Earnings": 40, "Swing_Pass": True,
    }
    row.update(overrides)
    return row


class TestComponentScores(unittest.TestCase):
    def test_all_perfect_scores_100(self):
        r = strong_row()
        self.assertEqual(score_quality(r), 100)
        self.assertEqual(score_setup(r), 100)
        self.assertEqual(score_timing(r), 100)

    def test_quality_ignores_chart(self):
        r = strong_row(Above_VWAP=False, RSI_14=90.0, RR_T2=None)
        self.assertEqual(score_quality(r), 100)

    def test_missing_data_scores_zero_not_crash(self):
        self.assertEqual(score_quality({}), 0)
        self.assertEqual(score_setup({}), 0)
        self.assertEqual(score_timing({}), 0)


class TestConviction(unittest.TestCase):
    def test_strong_row_is_ready_5_stars(self):
        c = compute_conviction(strong_row())
        self.assertEqual(c["Conv_Overall"], 100)
        self.assertEqual(c["Conv_Stars"], 5)
        self.assertEqual(c["Conv_Action"], "READY")
        self.assertEqual(c["Conv_Action_Reason"], "Buy now")

    def test_why_lists_positives_and_warnings(self):
        c = compute_conviction(strong_row(**{"Pct_vs_8EMA": 12.0}))
        marks = dict((t, m) for m, t in c["Conv_Why"])
        self.assertIn("Strong earnings (last report beat)", marks)
        self.assertTrue(any("Extended 12% above 8EMA" in t
                            for _, t in c["Conv_Why"]))

    def test_extended_forces_watch_pullback(self):
        c = compute_conviction(strong_row(**{"Pct_vs_8EMA": 12.0}))
        self.assertEqual(c["Conv_Action"], "WATCH")
        self.assertEqual(c["Conv_Action_Reason"], "Wait for pullback")
        self.assertIn(("EXTENDED", "warn"), c["Conv_Tags"])

    def test_earnings_blackout_forces_watch(self):
        c = compute_conviction(strong_row(Days_To_Earnings=3))
        self.assertEqual(c["Conv_Action"], "WATCH")
        self.assertIn("earnings", c["Conv_Action_Reason"].lower())

    def test_gate_failure_is_avoid(self):
        c = compute_conviction(strong_row(Entry_Gate_Pass=False,
                                          Entry_Gate_Reason="MarketCap<1B"))
        self.assertEqual(c["Conv_Action"], "AVOID")
        self.assertIn(("GATE FAILED", "bad"), c["Conv_Tags"])

    def test_below_200ma_is_avoid_with_bad_tag(self):
        c = compute_conviction(strong_row(Above_200MA=False))
        self.assertEqual(c["Conv_Action"], "AVOID")
        self.assertIn(("BELOW 200MA", "bad"), c["Conv_Tags"])

    def test_weak_row_low_stars(self):
        c = compute_conviction({"Ticker": "MEH", "Category": "Avoid",
                                "Entry_Gate_Pass": True})
        self.assertLessEqual(c["Conv_Stars"], 2)

    def test_attach_survives_error_rows(self):
        rows = [strong_row(), {"Ticker": "ERR"}]
        attach_conviction(rows)
        self.assertEqual(rows[1]["Conv_Action"], "AVOID")
        self.assertIn("Conv_Overall", rows[1])


def beaten_row(**overrides) -> dict:
    row = {"Ticker": "TURN", "Category": "Turnaround",
           "Dist_52W_High%": -50.0, "Price_vs_50MA%": -12.0,
           "Above_200MA": False, "RS_Rank": 35, "Pct_From_52W_Low%": 10.0,
           "EarningsBeat": False, "Revenue": -5.0, "FCF_Positive": False,
           "RSI_14": 40.0, "RVOL": 0.6, "50MA": 40.0, "21EMA": 36.0,
           "Current Price": 35.0, "Entry_Gate_Pass": True}
    row.update(overrides)
    return row


class TestRecovery(unittest.TestCase):
    def test_stage_bottoming(self):
        self.assertEqual(recovery_stage(beaten_row()), "Bottoming")

    def test_stage_recovering_on_50ma_proximity(self):
        self.assertEqual(recovery_stage(beaten_row(**{"Price_vs_50MA%": -3.0})),
                         "Recovering")

    def test_stage_confirmed_above_200ma(self):
        self.assertEqual(recovery_stage(beaten_row(Above_200MA=True)),
                         "Trend Confirmed")

    def test_candidates_filter_and_sort(self):
        rows = [beaten_row(Ticker="BOT"),
                beaten_row(Ticker="CONF", Above_200MA=True, RS_Rank=65,
                           EarningsBeat=True, Revenue=15.0),
                {"Ticker": "FINE", "Dist_52W_High%": -5.0},   # not beaten down
                {"Ticker": "ERR", "Category": "Error",
                 "Dist_52W_High%": -60.0}]
        cands = recovery_candidates(rows)
        tickers = [c["Ticker"] for c in cands]
        self.assertEqual(tickers, ["CONF", "BOT"])   # mature stage first
        conf = cands[0]
        self.assertIn("Earnings beat last quarter", conf["Rec_Why"])
        self.assertTrue(any("below 200 MA" in r.lower() or "cash flow" in r.lower()
                            for r in cands[1]["Rec_Risks"]))
        self.assertIn("50 MA reclaim", cands[1]["Rec_Entry"])

    def test_risks_always_stated_for_weak_names(self):
        c = recovery_candidates([beaten_row()])[0]
        self.assertGreaterEqual(len(c["Rec_Risks"]), 3)
        self.assertLessEqual(c["Rec_Stars"], 2)


class TestDailyOpportunity(unittest.TestCase):
    def _rows(self, n_strong):
        return attach_conviction(
            [strong_row(Ticker=f"S{i}") for i in range(n_strong)]
            + [{"Ticker": "W", "Category": "Avoid", "Entry_Gate_Pass": True}])

    def test_bullish_strong_scan_is_excellent(self):
        d = daily_opportunity(self._rows(5), {"regime": "Bullish"})
        self.assertGreaterEqual(d["score"], 80)
        self.assertEqual(d["stars"], 5)
        self.assertEqual(d["risk"], "LOW")
        self.assertIn("Excellent", d["label"])

    def test_defensive_regime_drags_score(self):
        bull = daily_opportunity(self._rows(5), {"regime": "Bullish"})
        bear = daily_opportunity(self._rows(5), {"regime": "Defensive"})
        self.assertEqual(bull["score"] - bear["score"], 35)
        self.assertEqual(bear["risk"], "HIGH")

    def test_empty_scan_is_defensive_day(self):
        d = daily_opportunity([], {"regime": "Neutral"})
        self.assertEqual(d["score"], 0)
        self.assertIn("Defensive day", d["label"])


if __name__ == "__main__":
    unittest.main()


class TestNewsSplice(unittest.TestCase):
    """update_news splice logic (pure part) — lives here to avoid a new file."""

    def test_splice_and_tags(self):
        from stockanalysis.reporting.research import (
            _splice_news, _tag_headline, NEWS_START, NEWS_END)
        # marker replacement
        page = f"<html>top{NEWS_START}old news{NEWS_END}bottom</html>"
        out = _splice_news(page, f"{NEWS_START}fresh{NEWS_END}")
        self.assertIn("fresh", out)
        self.assertNotIn("old news", out)
        self.assertEqual(out.count(NEWS_START), 1)
        # legacy page without markers → inserted before footer
        legacy = ('<html>body<p style="font-size:10px;color:#898781;'
                  'text-align:center">footer</p></html>')
        out2 = _splice_news(legacy, f"{NEWS_START}fresh{NEWS_END}")
        self.assertLess(out2.find("fresh"), out2.find("footer"))
        # headline classification
        self.assertIn("DEAL", _tag_headline("NVDA signs supply agreement"))
        self.assertIn("EARNINGS", _tag_headline("AMD earnings beat estimates"))
        self.assertIn("M&A", _tag_headline("Broadcom to acquire startup"))
        self.assertEqual(_tag_headline("Something unrelated"), ["NEWS"])
