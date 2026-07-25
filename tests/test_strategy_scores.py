"""
Tests for core.strategy_scores — synthetic rows, no network.
Run with: python -m unittest tests.test_strategy_scores
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.strategy_scores import (
    attach_rs_rank, attach_strategy_scores,
    score_investment, score_swing, score_day_trade, lt_entry_timing,
    MIN_RANK_UNIVERSE,
)


def lt_row(**overrides) -> dict:
    """A row that passes every long-term-growth primary filter."""
    row = {
        "Ticker": "LT", "RS_Rank": 92, "EPS_Growth%": 30.0, "Revenue": 25.0,
        "Above_200MA": True, "FCF_Positive": True, "EarningsBeat": True,
        "Inst_Own%": 65.0, "Inst_Own_Chg": 0.5,
        "Entry_Gate_Pass": True,
    }
    row.update(overrides)
    return row


def swing_row(**overrides) -> dict:
    """A row that passes every swing primary filter."""
    row = {
        "Ticker": "SW", "Category": "Momentum-Pullback", "RR_T2": 3.5,
        "ATR Shrinking": True, "RSI_14": 48.0, "BB_PctB": 0.15,
        "Pullback_Vol_Ratio": 0.7, "Above_200MA": True, "RS_Rank": 75,
        "Entry_Gate_Pass": True,
    }
    row.update(overrides)
    return row


def day_row(**overrides) -> dict:
    """A row that passes every day-trade primary filter."""
    row = {
        "Ticker": "DT", "Gap%": 3.1, "Gap_Now%": 3.4,
        "RVOL_Intraday": 2.2, "RVOL": 0.9, "Above_VWAP": True,
        "ORB_Status": "above", "ATR_Pct": 4.0, "ADX_14": 32.0, "RS_Rank": 85,
        "Entry_Gate_Pass": True,
    }
    row.update(overrides)
    return row


class TestRSRank(unittest.TestCase):
    def test_percentile_on_large_universe(self):
        rows = [{"RS": float(i)} for i in range(MIN_RANK_UNIVERSE + 5)]
        attach_rs_rank(rows)
        ranks = [r["RS_Rank"] for r in rows]
        self.assertEqual(ranks[0], 0)         # weakest RS → rank 0
        self.assertEqual(ranks[-1], 95)       # strongest → 24/25*99
        self.assertEqual(ranks, sorted(ranks))

    def test_small_universe_uses_fallback(self):
        rows = [{"RS": 25.0}, {"RS": -15.0}, {"RS": 2.0}]
        attach_rs_rank(rows)
        self.assertEqual(rows[0]["RS_Rank"], 90)   # absolute mapping
        self.assertEqual(rows[1]["RS_Rank"], 20)
        self.assertEqual(rows[2]["RS_Rank"], 60)

    def test_none_rs_stays_none(self):
        rows = [{"RS": None}, {"RS": 10.0}]
        attach_rs_rank(rows)
        self.assertIsNone(rows[0]["RS_Rank"])


class TestInvestmentScore(unittest.TestCase):
    def test_full_pass(self):
        score, ok, reason = score_investment(lt_row())
        self.assertTrue(ok)
        self.assertEqual(score, 100)
        self.assertNotIn("FAILED", reason)

    def test_one_filter_fail_still_scores(self):
        score, ok, reason = score_investment(lt_row(EarningsBeat=False))
        self.assertFalse(ok)
        self.assertEqual(score, 90)
        self.assertIn("earnings_beat", reason.split("FAILED: ")[1])

    def test_missing_data_fails_filter_not_crash(self):
        score, ok, reason = score_investment(lt_row(**{"EPS_Growth%": None}))
        self.assertFalse(ok)
        self.assertIn("EPS_growth>25%", reason.split("FAILED: ")[1])


class TestSwingScore(unittest.TestCase):
    def test_full_pass(self):
        score, ok, reason = score_swing(swing_row())
        self.assertTrue(ok)
        self.assertEqual(score, 100)

    def test_vcp_also_passes_setup_filter(self):
        _, ok, _ = score_swing(swing_row(Category="VCP Setup"))
        self.assertTrue(ok)

    def test_momentum_category_fails_setup_filter(self):
        _, ok, reason = score_swing(swing_row(Category="Momentum"))
        self.assertFalse(ok)
        self.assertIn("setup(MP/VCP)", reason.split("FAILED: ")[1])

    def test_low_rr_fails(self):
        _, ok, reason = score_swing(swing_row(RR_T2=1.8))
        self.assertFalse(ok)
        self.assertIn("RR_T2>=3", reason.split("FAILED: ")[1])

    def test_gate_fail_zeroes_score(self):
        score, ok, reason = score_swing(
            swing_row(Entry_Gate_Pass=False, Entry_Gate_Reason="MarketCap<1B"))
        self.assertEqual(score, 0)
        self.assertFalse(ok)
        self.assertIn("entry_gate_failed", reason)


class TestDayTradeScore(unittest.TestCase):
    def test_full_pass(self):
        score, ok, _ = score_day_trade(day_row())
        self.assertTrue(ok)
        self.assertEqual(score, 100)

    def test_prefers_intraday_rvol(self):
        # daily RVOL is 0.9 but time-adjusted is 2.2 — must not fail RVOL
        _, ok, reason = score_day_trade(day_row())
        self.assertNotIn("RVOL", reason.split("PASSED")[0])
        self.assertTrue(ok)

    def test_gap_down_magnitude_counts(self):
        # -3% gap is a valid day-trade (short) catalyst
        _, ok, _ = score_day_trade(day_row(**{"Gap%": -3.0}))
        self.assertTrue(ok)

    def test_orb_inside_fails_breakout_filter(self):
        _, ok, reason = score_day_trade(day_row(ORB_Status="inside"))
        self.assertFalse(ok)
        self.assertIn("ORB_breakout", reason.split("FAILED: ")[1])

    def test_gap_none_falls_back_to_gap_now(self):
        _, ok, _ = score_day_trade(day_row(**{"Gap%": None}))
        self.assertTrue(ok)   # Gap_Now% = 3.4 covers the filter


class TestLTEntryTiming(unittest.TestCase):
    def _t(self, **kw) -> str:
        base = {"Above_200MA": True, "Price_vs_200MA%": 15.0,
                "Dist_52W_High%": -20.0, "Category": "Avoid"}
        base.update(kw)
        return lt_entry_timing(base)

    def test_below_200ma_blocks_entry(self):
        self.assertIn("no new LT entry", self._t(Above_200MA=False))

    def test_pullback_category_is_favorable(self):
        self.assertIn("favorable add point",
                      self._t(Category="Momentum-Pullback"))

    def test_extended_leader_reads_tranche_only(self):
        # AMD-like: Avoid by swing tree, +92% above 200MA
        hint = self._t(**{"Price_vs_200MA%": 92.0, "Dist_52W_High%": -5.6})
        self.assertIn("extended", hint)
        self.assertIn("pullback", hint)

    def test_pullback_on_extended_name_keeps_warning(self):
        hint = self._t(Category="Momentum-Pullback",
                       **{"Price_vs_200MA%": 80.0})
        self.assertIn("favorable add point", hint)
        self.assertIn("extended", hint)

    def test_near_high_not_extended(self):
        hint = self._t(**{"Dist_52W_High%": -4.0})
        self.assertIn("near 52W high", hint)

    def test_missing_history(self):
        self.assertIn("insufficient history",
                      self._t(Above_200MA=None, **{"Price_vs_200MA%": None}))


class TestAttach(unittest.TestCase):
    def test_adds_all_columns_and_survives_error_rows(self):
        rows = [lt_row(RS=30.0), swing_row(RS=5.0),
                {"Ticker": "ERR", "Category": "Error"}]   # metrics-free row
        attach_strategy_scores(rows)
        for row in rows:
            for prefix in ("Investment", "Swing", "DayTrade"):
                self.assertIn(f"{prefix}_Score", row)
                self.assertIn(f"{prefix}_Pass", row)
                self.assertIn(f"{prefix}_Reason", row)
        err = rows[2]
        self.assertEqual(err["Swing_Score"], 0)
        self.assertFalse(err["Investment_Pass"])
        for row in rows:
            self.assertIn("LT_Entry_Timing", row)
            self.assertIn("Buy_Zone_Score", row)
            self.assertIn("Buy_Zone_Label", row)
        # metrics-free row has no factor data at all -> None, not a crash
        self.assertIsNone(err["Buy_Zone_Score"])


if __name__ == "__main__":
    unittest.main()
