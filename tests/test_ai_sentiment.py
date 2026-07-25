"""
Tests for core.ai_sentiment — synthetic pulse/pulse_extra dicts, no network.
Run with: python -m unittest tests.test_ai_sentiment
"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.ai_sentiment import (
    compute_risk_score, compute_rotation, compute_ai_health,
    compute_macro_caution, compute_tier_status, compute_ai_sentiment,
    build_snapshot,
)

ET = ZoneInfo("America/New_York")


def basket(chgs, rsi=55.0):
    return [{"ticker": f"T{i}", "day_chg_pct": c, "above_20ema": c >= 0, "rsi_14": rsi}
            for i, c in enumerate(chgs)]


def pulse_extra(yield_bps=0, dxy_chg=0.0, soxx_above_20ma=True, nvda_vwap=True,
                basket_chgs=None):
    return {
        "yield": {"change_bps": yield_bps},
        "dxy": {"change_pct": dxy_chg},
        "soxx": {"above_20ma": soxx_above_20ma},
        "nvda_above_vwap": nvda_vwap,
        "basket": basket(basket_chgs if basket_chgs is not None else [1.0] * 9),
        "tier1": [], "tier2": [], "tier3": [],
    }


def pulse(vix_chg=0.0, spy_chg=0.0, qqq_chg=0.0, sectors=None, econ_events=None):
    return {
        "vix": {"change_pct": vix_chg},
        "spy": {"day_chg_pct": spy_chg},
        "qqq": {"day_chg_pct": qqq_chg},
        "sectors": sectors or [],
        "econ_events": econ_events or [],
    }


class TestRiskScore(unittest.TestCase):
    def test_all_negative_triggers_defensive(self):
        r = compute_risk_score(
            pulse_extra(yield_bps=10, dxy_chg=1.0, soxx_above_20ma=False,
                       nvda_vwap=False, basket_chgs=[-1.0] * 9),
            pulse(vix_chg=10.0, spy_chg=1.0, qqq_chg=0.5))
        # yield -2, dxy -1, vix -2, soxx -2, nasdaq-vs-spy -1, nvda -2, breadth +0
        self.assertEqual(r["score"], -10)
        self.assertEqual(r["label"], "Defensive")

    def test_all_positive_triggers_aggressive(self):
        r = compute_risk_score(
            pulse_extra(yield_bps=-10, dxy_chg=0.1, soxx_above_20ma=True,
                       nvda_vwap=True, basket_chgs=[1.0] * 9),
            pulse(vix_chg=-5.0, spy_chg=0.5, qqq_chg=1.0))
        # yield +2, dxy +0, vix +0, soxx +0, nasdaq-vs-spy +0, nvda +0, breadth +2
        self.assertEqual(r["score"], 4)
        self.assertEqual(r["label"], "Aggressive buying")

    def test_neutral_inputs_score_zero(self):
        r = compute_risk_score(
            pulse_extra(yield_bps=0, dxy_chg=0.0, soxx_above_20ma=True,
                       nvda_vwap=True, basket_chgs=[0.5] * 4 + [-0.5] * 5),
            pulse(vix_chg=0.0, spy_chg=0.5, qqq_chg=0.5))
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["label"], "Neutral")

    def test_missing_data_does_not_raise(self):
        r = compute_risk_score({}, {})
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["drivers"], [])


class TestRotation(unittest.TestCase):
    def test_defensive_up_and_semis_down_is_risk_off(self):
        sectors = [
            {"label": "Utilities", "chg_pct": 1.0}, {"label": "Staples", "chg_pct": 0.5},
            {"label": "Health", "chg_pct": 0.3}, {"label": "Semis", "chg_pct": -1.5},
        ]
        r = compute_rotation(sectors)
        self.assertEqual(r["state"], "Risk-Off")

    def test_growth_sectors_up_is_risk_on(self):
        sectors = [
            {"label": "Semis", "chg_pct": 1.2}, {"label": "Software", "chg_pct": 0.8},
            {"label": "Industr", "chg_pct": 0.4}, {"label": "Financials", "chg_pct": 0.6},
        ]
        r = compute_rotation(sectors)
        self.assertEqual(r["state"], "Risk-On")

    def test_ambiguous_is_mixed(self):
        sectors = [{"label": "Semis", "chg_pct": 0.3}, {"label": "Utilities", "chg_pct": -0.2}]
        r = compute_rotation(sectors)
        self.assertEqual(r["state"], "Mixed")

    def test_no_sector_data_is_unknown(self):
        r = compute_rotation([])
        self.assertEqual(r["state"], "Unknown")


class TestAIHealth(unittest.TestCase):
    def test_strong_trend_bands_above_80(self):
        h = compute_ai_health(basket([2.0] * 9, rsi=85.0))
        self.assertGreater(h["index"], 80)
        self.assertEqual(h["label"], "Strong AI trend")

    def test_broad_correction_bands_below_20(self):
        h = compute_ai_health(basket([-4.0] * 9, rsi=15.0))
        self.assertLess(h["index"], 20)
        self.assertEqual(h["label"], "Broad AI correction")

    def test_empty_basket_returns_no_data(self):
        h = compute_ai_health([])
        self.assertIsNone(h["index"])
        self.assertEqual(h["label"], "No data")

    def test_advance_decline_counts(self):
        h = compute_ai_health(basket([1.0, 1.0, -1.0]))
        self.assertEqual(h["advancers"], 2)
        self.assertEqual(h["decliners"], 1)


class TestMacroCaution(unittest.TestCase):
    def test_cpi_within_48h_triggers_caution(self):
        now = datetime(2026, 7, 16, 9, 0, tzinfo=ET)
        events = [{"title": "CPI m/m", "when": now + timedelta(hours=20), "impact": "High"}]
        m = compute_macro_caution(events, now=now)
        self.assertTrue(m["caution"])
        self.assertEqual(len(m["events"]), 1)

    def test_event_beyond_48h_does_not_trigger(self):
        now = datetime(2026, 7, 16, 9, 0, tzinfo=ET)
        events = [{"title": "FOMC Statement", "when": now + timedelta(hours=72), "impact": "High"}]
        m = compute_macro_caution(events, now=now)
        self.assertFalse(m["caution"])

    def test_non_macro_title_does_not_trigger(self):
        now = datetime(2026, 7, 16, 9, 0, tzinfo=ET)
        events = [{"title": "Earnings: Some Retailer", "when": now + timedelta(hours=5), "impact": "Medium"}]
        m = compute_macro_caution(events, now=now)
        self.assertFalse(m["caution"])


class TestTierStatus(unittest.TestCase):
    def test_all_red_flag(self):
        s = compute_tier_status([{"day_chg_pct": -1.0}, {"day_chg_pct": -0.5}])
        self.assertTrue(s["all_red"])

    def test_mixed_not_all_red(self):
        s = compute_tier_status([{"day_chg_pct": -1.0}, {"day_chg_pct": 0.5}])
        self.assertFalse(s["all_red"])


class TestCompositeSentiment(unittest.TestCase):
    def test_ai_specific_rotation_penalty_applies(self):
        risk = {"score": 0, "label": "Neutral", "drivers": []}
        rotation = {"state": "Mixed", "drivers": []}
        health = {"index": 50}
        tier_statuses = [{"all_red": True}, {"all_red": True}, {"all_red": True}]
        s = compute_ai_sentiment(risk, rotation, health, tier_statuses, market_flat=True)
        self.assertTrue(s["ai_specific_rotation"])

        s_no_flag = compute_ai_sentiment(risk, rotation, health, tier_statuses, market_flat=False)
        self.assertFalse(s_no_flag["ai_specific_rotation"])
        self.assertGreater(s_no_flag["score"], s["score"])

    def test_score_bands(self):
        risk = {"score": 4, "label": "Aggressive buying", "drivers": []}
        rotation = {"state": "Risk-On", "drivers": []}
        health = {"index": 90}
        s = compute_ai_sentiment(risk, rotation, health, [], market_flat=False)
        self.assertGreater(s["score"], 70)
        self.assertIn("Aggressive", s["label"])

        risk_bad = {"score": -10, "label": "Defensive", "drivers": []}
        rotation_bad = {"state": "Risk-Off", "drivers": []}
        health_bad = {"index": 10}
        s_bad = compute_ai_sentiment(risk_bad, rotation_bad, health_bad, [], market_flat=False)
        self.assertLess(s_bad["score"], 40)
        self.assertIn("Defensive", s_bad["label"])


class TestBuildSnapshot(unittest.TestCase):
    def test_builds_full_snapshot_without_raising(self):
        snap = build_snapshot(pulse_extra(), pulse())
        for key in ("sentiment", "risk", "rotation", "ai_health", "macro",
                   "tiers", "all_ai", "soxx", "yield", "dxy", "relative_strength",
                   "breadth", "momentum", "leadership", "leading_groups", "basket_v2"):
            self.assertIn(key, snap)
        self.assertIsInstance(snap["sentiment"]["score"], int)

    def test_handles_empty_inputs(self):
        snap = build_snapshot({}, {})
        self.assertIn("sentiment", snap)
        # every v2 component missing -> exact neutral 50
        self.assertEqual(snap["sentiment"]["score"], 50)
        self.assertEqual(len(snap["sentiment"]["missing"]), 6)


# ─────────────────────────────────────────────────────────────────────────────
# Sentiment Score v2 — supply-chain basket components
# ─────────────────────────────────────────────────────────────────────────────

from stockanalysis.core.ai_sentiment import (
    AI_BASKET, BASKET_WEIGHT, compute_breadth, compute_momentum,
    compute_rs_vs_qqq, compute_volume_participation, compute_macro_component,
    compute_news_earnings, compute_leadership, compute_leading_groups,
    compute_ai_sentiment_v2)


def _v2_snap(ticker, chg=1.0, chg5=3.0, chg20=8.0, rsi=65.0, vr=1.8,
             e20=True, e50=True, e200=True, hi=True):
    return {"ticker": ticker, "price": 100.0, "day_chg_pct": chg,
            "chg_5d_pct": chg5, "chg_20d_pct": chg20, "above_20ema": e20,
            "above_50ema": e50, "above_200ema": e200, "rsi_14": rsi,
            "high_20d": hi, "vol_ratio": vr}


def _v2_basket(**kw):
    return [_v2_snap(r["ticker"], **kw) for r in AI_BASKET]


class TestBasketDefinition(unittest.TestCase):
    def test_fifteen_names_with_spec_weights(self):
        self.assertEqual(len(AI_BASKET), 15)
        self.assertEqual(sum(BASKET_WEIGHT.values()), 108)   # spec's sum, normalized in use
        self.assertEqual(BASKET_WEIGHT["NVDA"], 15)

    def test_sector_group_pre_normalization_sums_match_spec(self):
        by_sector = {}
        for r in AI_BASKET:
            by_sector[r["sector"]] = by_sector.get(r["sector"], 0) + r["weight"]
        self.assertEqual(by_sector, {"Semiconductors": 53, "Networking/Optics": 15,
                                     "Power": 19, "Software/Cloud": 21})


class TestBreadth(unittest.TestCase):
    def test_all_conditions_true_scores_100(self):
        b = compute_breadth(_v2_basket())
        self.assertEqual(b["score"], 100.0)
        self.assertEqual(b["conditions"]["Above 200 EMA"], 100)

    def test_mixed_conditions_average(self):
        snaps = [_v2_snap("NVDA"), _v2_snap("AMD", e20=False, rsi=40, hi=False, vr=0.9)]
        b = compute_breadth(snaps)
        self.assertEqual(b["conditions"]["Above 20 EMA"], 50)
        self.assertEqual(b["conditions"]["RSI > 60"], 50)

    def test_empty_returns_none(self):
        self.assertIsNone(compute_breadth([])["score"])


class TestMomentumAndRS(unittest.TestCase):
    def test_positive_weighted_returns_score_above_50(self):
        m = compute_momentum(_v2_basket())
        self.assertGreater(m["score"], 50)
        self.assertEqual(m["wret_1d"], 1.0)   # uniform returns -> weighted avg equals it

    def test_nvda_weight_dominates(self):
        # NVDA (15/108) up big, everything else flat -> weighted 1d > 1%
        snaps = [_v2_snap(r["ticker"], chg=(10.0 if r["ticker"] == "NVDA" else 0.0))
                 for r in AI_BASKET]
        m = compute_momentum(snaps)
        self.assertAlmostEqual(m["wret_1d"], 10.0 * 15 / 108, places=2)

    def test_rs_spread_maps_5pts_per_pct(self):
        rs = compute_rs_vs_qqq(_v2_basket(chg20=10.0), qqq_chg_20d=6.0)
        self.assertEqual(rs["spread_pct"], 4.0)
        self.assertEqual(rs["score"], 70.0)
        self.assertIsNone(compute_rs_vs_qqq(_v2_basket(), None)["score"])


class TestVolumeMacroNews(unittest.TestCase):
    def test_up_on_volume_scores_high_down_scores_low(self):
        up = compute_volume_participation(_v2_basket(chg=2.0, vr=2.0))
        down = compute_volume_participation(_v2_basket(chg=-2.0, vr=2.0))
        self.assertEqual(up["score"], 100)
        self.assertEqual(down["score"], 0)

    def test_macro_calm_tape_scores_high_stress_low(self):
        calm = compute_macro_component(
            {"yield": {"change_bps": -6}, "dxy": {"change_pct": -0.4},
             "soxx": {"above_20ema": True}},
            {"vix": {"level": 13.0}, "qqq": {"day_chg_pct": 1.0}, "spy": {"day_chg_pct": 0.6}})
        stress = compute_macro_component(
            {"yield": {"change_bps": 12}, "dxy": {"change_pct": 0.9},
             "soxx": {"above_20ema": False}},
            {"vix": {"level": 30.0}, "qqq": {"day_chg_pct": -2.0}, "spy": {"day_chg_pct": -1.5}})
        self.assertGreater(calm["score"], 65)
        self.assertLess(stress["score"], 30)

    def test_news_earnings_uncertainty_drag(self):
        clear = compute_news_earnings({"NVDA": 30, "AMD": 45})
        soon = compute_news_earnings({"NVDA": 2, "AMD": 4, "MSFT": 1})
        self.assertEqual(clear["score"], 75)
        self.assertEqual(soon["score"], 30)      # 75 - 3*15
        self.assertIsNone(compute_news_earnings(None)["score"])


class TestLeadership(unittest.TestCase):
    def test_groups_sorted_strongest_first(self):
        snaps = [_v2_snap(r["ticker"],
                          chg=(2.0 if r["sector"] == "Power" else 0.5))
                 for r in AI_BASKET]
        lead = compute_leadership(snaps)
        self.assertEqual(lead[0]["sector"], "Power")
        self.assertEqual(len(lead), 4)

    def test_leading_group_warning_on_5d_rollover(self):
        snaps = [{"ticker": "SMCI", "day_chg_pct": -1.0, "chg_5d_pct": -4.0},
                 {"ticker": "DELL", "day_chg_pct": -0.5, "chg_5d_pct": -3.0}]
        groups = compute_leading_groups(snaps)
        hw = next(g for g in groups if g["group"] == "AI Hardware")
        self.assertTrue(hw["warning"])


class TestSentimentV2(unittest.TestCase):
    def _comp(self, score):
        return {"score": score, "drivers": []}

    def test_weights_apply(self):
        s = compute_ai_sentiment_v2(self._comp(100), self._comp(0), self._comp(0),
                                    self._comp(0), self._comp(0), self._comp(0))
        self.assertEqual(s["score"], 40)         # momentum alone = 40% weight

    def test_missing_component_contributes_neutral_50(self):
        s = compute_ai_sentiment_v2(self._comp(None), self._comp(80), self._comp(80),
                                    self._comp(80), self._comp(80), self._comp(80))
        self.assertIn("momentum", s["missing"])
        self.assertEqual(s["components"]["momentum"], 50)

    def test_ai_specific_rotation_penalty(self):
        args = [self._comp(60)] * 6
        base = compute_ai_sentiment_v2(*args)
        hit = compute_ai_sentiment_v2(*args, tier_statuses=[{"all_red": True}],
                                      market_flat=True)
        self.assertEqual(base["score"] - hit["score"], 15)
        self.assertTrue(hit["ai_specific_rotation"])


if __name__ == "__main__":
    unittest.main()
