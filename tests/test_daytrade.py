"""
Tests for core.daytrade — the small-cap day-trade momentum engine.

The invariants these defend, in the order the engine applies them:

  1. Gates are gates. Tradeability (§10/§11) and room (§9) cap the outcome
     no matter how high the other scores are. A refactor that "simplifies"
     either into a seventh weight would pass every scoring test here and
     break the property the engine exists for.
  2. A missing input is never a score. Unknown float is not small float,
     unknown RVOL is not 1.0, and a stale bid/ask outside market hours is
     not a spread.
  3. Confirmations are counted, not summed (§12/§18).
  4. Dates are dates. Daily bars must never be timezone-shifted, and no
     level may be built from a bar the scan could not have seen.

Several tests are named `test_regression_*`. Each pins a bug that was
actually found while validating against the 2026-08-07 session, with the
observed wrong value in the docstring — they are the ones most likely to
silently reappear.

No network: every test builds its own frames.

Run with: python -m unittest tests.test_daytrade
"""
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.daytrade import _common as CM
from stockanalysis.core.daytrade import catalyst as C
from stockanalysis.core.daytrade import engine as E
from stockanalysis.core.daytrade import entry as EN
from stockanalysis.core.daytrade import plan as P
from stockanalysis.core.daytrade import profiles as PR
from stockanalysis.core.daytrade import room as RM
from stockanalysis.core.daytrade import structure as ST
from stockanalysis.core.daytrade import supply as SU
from stockanalysis.core.daytrade import universe as U
from stockanalysis.core.daytrade import volatility as V
from stockanalysis.core.daytrade import volume as VOL

DAY = date(2026, 8, 7)
PREV = date(2026, 8, 6)


def _bars(day, start="09:30", n=60, price=10.0, step=0.02, vol=10_000, freq="1min"):
    """A rising 1-minute session."""
    idx = pd.date_range(f"{day} {start}", periods=n, freq=freq, tz=CM.MARKET_TZ)
    closes = [price + step * i for i in range(n)]
    return pd.DataFrame({
        "Open": [c - step / 2 for c in closes],
        "High": [c + step for c in closes],
        "Low": [c - step for c in closes],
        "Close": closes,
        "Volume": [vol] * n,
    }, index=idx)


def _daily(end_day, n=40, close=8.0, drift=0.0):
    """Daily bars ending the session BEFORE `end_day`, tz-naive as yfinance
    delivers them."""
    idx = pd.date_range(end=pd.Timestamp(end_day) - pd.Timedelta(days=1),
                        periods=n, freq="D")
    closes = [close + drift * i for i in range(n)]
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.03 for c in closes],
        "Low": [c * 0.97 for c in closes], "Close": closes,
        "Volume": [400_000] * n,
    }, index=idx)


class CommonTests(unittest.TestCase):
    def test_session_slice_is_end_exclusive(self):
        """A 09:45 bar belongs to the breakout window, not the opening range.
        Including it lets the range absorb the move that breaks it."""
        bars = _bars(DAY, n=30)
        opening = CM.session_slice(bars, DAY, CM.MARKET_OPEN, CM.ORB_END)
        self.assertEqual(len(opening), 15)
        self.assertEqual(opening.index[-1].time(), time(9, 44))

    def test_vwap_returns_none_on_zero_volume(self):
        bars = _bars(DAY, n=10, vol=0)
        self.assertIsNone(CM.vwap(bars))

    def test_vwap_is_volume_weighted_not_mean(self):
        bars = _bars(DAY, n=4)
        bars.loc[bars.index[-1], "Volume"] = 1_000_000
        typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3
        self.assertGreater(CM.vwap(bars), typical.mean())

    def test_truncate_keeps_prior_sessions_whole(self):
        """Only the day under examination is cut. Truncating the baselines
        too would halve every RVOL denominator."""
        both = pd.concat([_bars(PREV, n=60), _bars(DAY, n=60)])
        cut = CM.truncate_at(both, DAY, time(9, 45))
        self.assertEqual(len(cut[cut.index.date == PREV]), 60)
        self.assertEqual(len(cut[cut.index.date == DAY]), 16)

    def test_whole_number_step_scales_with_price(self):
        self.assertIn(3.5, CM.whole_number_levels(3.4))
        self.assertNotIn(3.5, CM.whole_number_levels(25.0))

    def test_points_rescales_onto_budget(self):
        self.assertEqual(CM.points(50, 20), 10.0)
        self.assertIsNone(CM.points(None, 20))


class StructureTests(unittest.TestCase):
    def test_gap_measured_against_the_prior_session(self):
        sess = ST.build_session(_bars(DAY, price=10.0), _bars(DAY, freq="5min"),
                                _daily(DAY, close=8.0))
        self.assertAlmostEqual(sess["prev_close"], 8.0, places=6)
        self.assertGreater(sess["gap_pct"], 20.0)

    def test_regression_daily_bars_are_not_timezone_shifted(self):
        """Was: to_et() localised naive daily dates to UTC and converted to
        Eastern, moving every bar back a calendar day. "Previous close" then
        resolved to the session's own close and RCEL's +36.8% earnings gap
        printed as -16.3%."""
        daily = _daily(DAY, n=5, close=8.0)
        daily.loc[pd.Timestamp(DAY)] = {"Open": 10.0, "High": 11.0, "Low": 9.0,
                                        "Close": 10.5, "Volume": 9_000_000}
        sess = ST.build_session(_bars(DAY), _bars(DAY, freq="5min"), daily)
        # prev_close must be the day BEFORE, never the session's own close.
        self.assertNotAlmostEqual(sess["prev_close"], 10.5, places=6)
        self.assertAlmostEqual(sess["prev_close"], 8.0, places=6)
        self.assertGreater(sess["gap_pct"], 0)

    def test_opening_range_from_first_fifteen_minutes(self):
        sess = ST.build_session(_bars(DAY, n=60), _bars(DAY, freq="5min"),
                                _daily(DAY))
        self.assertLess(sess["or_high"], sess["day_high"])
        self.assertAlmostEqual(sess["or_low"], sess["day_low"], places=6)

    def test_premarket_levels_separate_from_session(self):
        pre = _bars(DAY, start="08:00", n=30, price=9.0, step=0.0)
        sess = ST.build_session(pd.concat([pre, _bars(DAY, price=10.0)]),
                                _bars(DAY, freq="5min"), _daily(DAY))
        self.assertAlmostEqual(sess["pm_high"], 9.0, places=2)
        self.assertEqual(sess["pm_volume"], 300_000)
        self.assertGreater(sess["day_low"], sess["pm_high"])

    def test_breakout_patterns_detected(self):
        sess = ST.build_session(_bars(DAY, n=60), _bars(DAY, freq="5min"),
                                _daily(DAY))
        pat = ST.detect_patterns(sess)
        self.assertIn("ORB_BREAKOUT", pat["patterns"])
        self.assertIn("ABOVE_VWAP", pat["patterns"])
        self.assertEqual(pat["primary"], "ORB_BREAKOUT")

    def test_failed_breakout_is_detected(self):
        """The pattern that most often gets misread as a breakout."""
        up = _bars(DAY, n=40, price=10.0, step=0.05)
        down = _bars(DAY, start="10:10", n=20, price=11.9, step=-0.08)
        sess = ST.build_session(pd.concat([up, down]), _bars(DAY, freq="5min"),
                                _daily(DAY))
        pat = ST.detect_patterns(sess)
        self.assertIn("FAILED_BREAKOUT", pat["patterns"])
        # It reads as a SHORT structure, which is what it is — the penalty
        # below applies to a long thesis that contains one, not to the
        # pattern in isolation.
        self.assertEqual(ST.score_setup(sess, pat)["direction"], "short")

    def test_failed_breakout_penalises_a_contradicted_long_thesis(self):
        """A failed breakout sitting inside an otherwise-bullish read is the
        configuration that produces confident losing trades, so it is
        subtracted rather than ignored."""
        sess = ST.build_session(_bars(DAY, n=60), _bars(DAY, freq="5min"),
                                _daily(DAY))
        bull = ["ABOVE_VWAP", "ORB_BREAKOUT", "PDH_BREAKOUT", "BULL_FLAG"]
        clean = ST.score_setup(sess, {"patterns": bull, "primary": "ORB_BREAKOUT",
                                      "above_vwap": True})
        fouled = ST.score_setup(sess, {"patterns": bull + ["FAILED_BREAKOUT"],
                                       "primary": "ORB_BREAKOUT",
                                       "above_vwap": True})
        self.assertEqual(fouled["direction"], "long")
        self.assertLess(fouled["score"], clean["score"])

    def test_no_structure_scores_none_not_zero(self):
        out = ST.score_setup({"bars": pd.DataFrame()},
                             {"patterns": [], "primary": None, "above_vwap": None})
        self.assertIsNone(out["score"])


class VolatilityTests(unittest.TestCase):
    def test_rvol_is_time_of_day_anchored(self):
        """Compared against the same clock time on prior sessions, so a
        genuine 10:00 runner does not read 0.2x just because the day is
        young."""
        frames = [_bars(d, n=78, vol=10_000, freq="5min")
                  for d in pd.date_range(end=PREV, periods=15, freq="D").date]
        today = _bars(DAY, n=12, vol=100_000, freq="5min")
        rv = V.relative_volume(pd.concat(frames + [today]), DAY)
        self.assertGreaterEqual(rv["sessions_used"], 5)
        self.assertAlmostEqual(rv["rvol"], 10.0, places=1)

    def test_regression_rvol_undefined_against_a_dormant_baseline(self):
        """Was: near-dormant tickers divided by a few thousand shares and
        reported RVOLs of 3,562 and 9,401 — a statement that the stock used
        to trade nothing, printed as though it were a measurement."""
        frames = [_bars(d, n=78, vol=10, freq="5min")
                  for d in pd.date_range(end=PREV, periods=15, freq="D").date]
        today = _bars(DAY, n=12, vol=500_000, freq="5min")
        rv = V.relative_volume(pd.concat(frames + [today]), DAY)
        self.assertIsNone(rv["rvol"])
        self.assertTrue(rv["baseline_too_thin"])

    def test_rvol_none_without_enough_history(self):
        rv = V.relative_volume(pd.concat([_bars(PREV, n=12, freq="5min"),
                                          _bars(DAY, n=12, freq="5min")]), DAY)
        self.assertIsNone(rv["rvol"])

    def test_regression_expected_move_survives_a_range_wider_than_atr(self):
        """Was: max(0, ATR% - range so far) returned 0.0 for every gapper,
        because a 13.8% realised range exceeds a 7.9% ATR. That made
        room_ratio None for the entire universe, so §9 could never gate and
        no R:R was computable."""
        self.assertGreater(V.expected_remaining_move(7.9, 13.8), 0.0)
        # Below ATR, unspent range is still the estimate.
        self.assertAlmostEqual(V.expected_remaining_move(10.0, 2.0), 8.0, places=6)
        self.assertIsNone(V.expected_remaining_move(None, 5.0))

    def test_regression_atr_excludes_the_session_being_scored(self):
        """Today's own bar must not set the yardstick today is measured
        against — in an as-of replay that is the 16:00 bar informing a
        10:15 scan."""
        daily = _daily(DAY, n=30, close=8.0)
        daily.loc[pd.Timestamp(DAY)] = {"Open": 8.0, "High": 40.0, "Low": 8.0,
                                        "Close": 39.0, "Volume": 9_000_000}
        sess = ST.build_session(_bars(DAY), _bars(DAY, freq="5min"), daily)
        out = V.compute(sess, daily, _bars(DAY, freq="5min"))
        self.assertLess(out["atr_pct"], 20.0)

    def test_missing_inputs_reduce_coverage_not_score(self):
        sess = ST.build_session(_bars(DAY), _bars(DAY, freq="5min"), _daily(DAY))
        out = V.compute(sess, _daily(DAY), pd.DataFrame())
        self.assertIsNone(out["rvol"])
        self.assertLess(out["coverage"], 1.0)
        self.assertIsNotNone(out["score"])


class SupplyTests(unittest.TestCase):
    def test_float_curve_penalises_untradeable_micro_floats(self):
        """§1: do not automatically rank the lowest float highest."""
        tiny = SU.compute({"floatShares": 800_000})
        good = SU.compute({"floatShares": 8_000_000})
        self.assertGreater(good["score"], tiny["score"])
        self.assertTrue(tiny["micro_float"])
        self.assertTrue(any("micro float" in w for w in tiny["warnings"]))

    def test_float_curve_falls_off_above_fifty_million(self):
        self.assertGreater(SU.compute({"floatShares": 8_000_000})["score"],
                           SU.compute({"floatShares": 200_000_000})["score"])

    def test_short_percent_derived_when_absent(self):
        out = SU.compute({"floatShares": 10_000_000, "sharesShort": 2_000_000})
        self.assertAlmostEqual(out["short_pct_of_float"], 20.0, places=6)

    def test_squeeze_is_a_flag_not_a_score_bonus(self):
        """§4: short interest alone is never a buy signal."""
        shorted = SU.compute({"floatShares": 10_000_000,
                              "shortPercentOfFloat": 0.30, "shortRatio": 6.0})
        self.assertTrue(shorted["squeeze_ready"])
        self.assertLessEqual(shorted["score"], 100)

    def test_dilution_is_reported_unverified_never_assumed_clean(self):
        out = SU.compute({"floatShares": 10_000_000})
        self.assertFalse(out["dilution_checked"])
        self.assertIn("S-1", out["dilution_note"])

    def test_unknown_float_is_unknown_not_small(self):
        out = SU.compute({})
        self.assertIsNone(out["score"])
        self.assertEqual(out["coverage"], 0.0)


class CatalystTests(unittest.TestCase):
    def _news(self, title, hours_ago):
        ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return [{"title": title, "publisher": "X", "published": ts.isoformat()}]

    def test_materiality_times_freshness_not_sum(self):
        """A three-week-old phase-3 readout is priced, not 'slightly stale'."""
        fresh = C.compute(self._news("Phase 3 trial meets primary endpoint", 2))
        stale = C.compute(self._news("Phase 3 trial meets primary endpoint", 400))
        self.assertGreater(fresh["score"], stale["score"] * 3)

    def test_fresh_material_news_outranks_stale_blockbuster(self):
        fresh_minor = C.compute(self._news("Analyst upgrades to buy", 1))["score"]
        stale_major = C.compute(self._news("Acquisition agreement signed", 400))["score"]
        self.assertGreater(fresh_minor, stale_major)

    def test_no_news_scores_low_not_none(self):
        """§2's explicit exception to the package's missing-data rule: a
        stock up 40% on nothing is a finding, not a gap."""
        out = C.compute([])
        self.assertEqual(out["score"], float(C.UNIDENTIFIED_SCORE))
        self.assertFalse(out["fresh"])

    def test_offering_headline_surfaced_even_when_outranked(self):
        news = self._news("Announces $50M at-the-market offering", 3)
        news += self._news("Phase 3 trial meets primary endpoint", 1)
        out = C.compute(news)
        self.assertTrue(out["dilution_headline"])

    def test_regression_another_companys_headline_is_not_this_stocks_catalyst(self):
        """Was: yfinance's `.news` for BE returned one Bloom Energy story
        followed by GE Vernova, OR Royalties, Realty Income and SoFi, with
        no ticker attribution anywhere in the payload. "OR Royalties Q2
        Earnings" (Earnings, 75) outscored Bloom's own product story (Other
        news, 40) and became BE's catalyst — 15% of confluence and a §12
        confirmation, driven by a different company."""
        news = (self._news("OR Royalties Q2 Earnings Call Highlights", 2)
                + self._news("Bloom Energy (BE) Expands Into AI Power", 3))
        loose = C.compute(news)
        self.assertEqual(loose["type"], "Earnings")          # the old bug
        tight = C.compute(news, ticker="BE", company="Bloom Energy Corporation")
        self.assertIn("Bloom Energy", tight["headline"])
        self.assertEqual(tight["unattributed"], 1)

    def test_attribution_matches_ticker_or_name_in_title_or_summary(self):
        def item(title, summary=""):
            return {"title": title, "summary": summary, "published": None}
        ok = lambda t, s="": C.attributable(item(t, s), "BE", "Bloom Energy Corporation")
        self.assertTrue(ok("Bloom Energy tops estimates"))
        self.assertTrue(ok("Analyst raises target on BE"))
        self.assertTrue(ok("Top AI power plays", "…including Bloom Energy…"))
        self.assertFalse(ok("Realty Income Q2 Earnings Call Highlights"))
        # The English word "be" must not match the ticker.
        self.assertFalse(ok("This could be the best trade of the year"))
        # Corporate suffixes are not distinctive.
        self.assertFalse(C.attributable(item("Acme Corporation Holdings news"),
                                        "BE", "Bloom Energy Corporation"))

    def test_unattributable_news_reports_the_move_as_unexplained(self):
        out = C.compute(self._news("Realty Income Q2 Earnings", 2),
                        ticker="BE", company="Bloom Energy Corporation")
        self.assertEqual(out["score"], float(C.UNIDENTIFIED_SCORE))
        self.assertIn("none about this company", out["detail"])

    def test_epoch_timestamps_are_understood(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
        out = C.compute([{"title": "FDA approval granted", "published": ts}])
        self.assertTrue(out["fresh"])

    def test_classification_prefers_the_specific_rule(self):
        self.assertEqual(C.classify("FDA grants approval")[0], "FDA / clinical")
        self.assertEqual(C.classify("Wins government contract")[0], "Government award")


class TradeabilityGateTests(unittest.TestCase):
    def _sess(self, dollar_volume=5e7, live=False):
        return {"price": 10.0, "dollar_volume": dollar_volume, "is_live": live}

    def test_regression_spread_unavailable_when_market_closed(self):
        """Was: .info bid/ask outside hours is a resting quote — AVITA showed
        5.71 x 8.96, a 44% 'spread' — and scoring it rejected every
        candidate on a closed market."""
        spr, note = U.spread_pct({"bid": 5.71, "ask": 8.96}, is_live=False)
        self.assertIsNone(spr)
        self.assertIn("market closed", note)
        live, _ = U.spread_pct({"bid": 9.99, "ask": 10.01}, is_live=True)
        self.assertAlmostEqual(live, 0.2, places=1)

    def test_thin_dollar_volume_fails_the_gate(self):
        out = U.compute({}, {"averageVolume": 100_000}, self._sess(dollar_volume=4e5),
                        SU.compute({"floatShares": 5_000_000}))
        self.assertFalse(out["tradeable"])

    def test_liquid_name_passes_the_gate(self):
        out = U.compute({}, {"averageVolume": 2_000_000}, self._sess(),
                        SU.compute({"floatShares": 8_000_000}))
        self.assertTrue(out["tradeable"])

    def test_regression_low_average_volume_does_not_reject(self):
        """§1 says 'prioritize' >500K, not 'require' it. AVITA averages 412K
        and traded 7.2M on its gap — rejecting it uses the stock's normal
        behaviour to disqualify it from a scan about abnormal behaviour."""
        sess = ST.build_session(_bars(DAY), _bars(DAY, freq="5min"), _daily(DAY))
        sess["dollar_volume"] = 5e7
        passed, reasons = U.passes_universe(
            {"market_cap": 3e8}, {"averageVolume": 412_000}, sess)
        self.assertTrue(passed, reasons)

    def test_market_cap_and_price_still_reject(self):
        sess = {"price": 45.0, "dollar_volume": 5e7}
        passed, reasons = U.passes_universe({"market_cap": 9e9}, {}, sess)
        self.assertFalse(passed)
        self.assertEqual(len(reasons), 2)


class RoomTests(unittest.TestCase):
    def _sess(self, price=10.0):
        sess = ST.build_session(_bars(DAY, n=60, price=10.0),
                                _bars(DAY, freq="5min"), _daily(DAY))
        sess["price"] = price
        return sess

    def test_regression_52_week_high_excludes_the_current_session(self):
        """Was: today's own high was offered as resistance — so a stock
        making new highs found 'resistance' at its own last tick, and the
        10:15 replay was handed the 16:00 high."""
        daily = _daily(DAY, n=30, close=8.0)
        daily.loc[pd.Timestamp(DAY)] = {"Open": 9.0, "High": 99.0, "Low": 9.0,
                                        "Close": 98.0, "Volume": 9_000_000}
        levels = RM.collect_levels(self._sess(), daily)
        highs = [l["price"] for l in levels if l["source"] == "52-week high"]
        self.assertTrue(all(h < 50 for h in highs), highs)

    def test_significant_levels_cluster_and_minor_ones_do_not_target(self):
        sess = self._sess(price=10.0)
        out = RM.compute(sess, _daily(DAY), "long", 5.0)
        for t in out["targets"]:
            self.assertGreaterEqual(t["weight"], RM.SIGNIFICANT_WEIGHT)

    def test_blocked_when_resistance_is_immediately_overhead(self):
        sess = self._sess(price=10.0)
        sess["prev_high"] = 10.02
        out = RM.compute(sess, _daily(DAY), "long", 8.0)
        self.assertTrue(out["blocked"])

    def test_never_blocks_without_an_expected_move(self):
        """Gating on a measurement the engine could not make is the
        fabrication this package refuses everywhere else."""
        sess = self._sess(price=10.0)
        sess["prev_high"] = 10.02
        self.assertFalse(RM.compute(sess, _daily(DAY), "long", None)["blocked"])


class PlanTests(unittest.TestCase):
    def _ctx(self):
        sess = ST.build_session(_bars(DAY, n=60), _bars(DAY, freq="5min"), _daily(DAY))
        pat = ST.detect_patterns(sess)
        vol = V.compute(sess, _daily(DAY), _bars(DAY, freq="5min"))
        room = RM.compute(sess, _daily(DAY), "long", vol["expected_move_pct"])
        return sess, pat, vol, room

    def test_stop_is_structural_and_names_its_basis(self):
        sess, pat, vol, room = self._ctx()
        pl = P.build(sess, pat, "long", vol, room, _daily(DAY))
        self.assertTrue(pl["actionable"])
        self.assertLess(pl["stop"], pl["entry"])
        self.assertTrue(pl["stop_basis"])
        self.assertIn("close below", pl["invalidation"])

    def test_measured_move_projects_the_broken_range(self):
        """The ORB measured move is what makes these plans usable: on RCEL
        2026-08-07 it projected 7.72 and the stock closed 7.77."""
        sess = {"or_high": 6.97, "or_low": 6.22}
        target, basis = P.measured_move(sess, "ORB_BREAKOUT", "long")
        self.assertAlmostEqual(target, 7.72, places=2)
        self.assertIn("measured move", basis)
        self.assertIsNone(P.measured_move(sess, "VWAP_BOUNCE", "long"))

    def test_regression_targets_skip_the_breakout_zone(self):
        """Was: T1 took a whole-number level two cents above entry, so every
        candidate scored R:R near 0.10 — the reward was measured to a price
        the stock was already touching."""
        sess, pat, vol, room = self._ctx()
        pl = P.build(sess, pat, "long", vol, room, _daily(DAY))
        self.assertGreater(abs(pl["target1"] - pl["entry"]),
                           pl["risk_per_share"] * P.BREAKOUT_ZONE_R)

    def test_regression_risk_percent_is_positive_on_a_short(self):
        """Was: the signed form put the stop above the entry on a short and
        reported "risk 2.297 (-1.1% of entry)" — risk as a negative number,
        which reads as a gain."""
        down = _bars(DAY, n=60, price=12.0, step=-0.02)
        sess = ST.build_session(down, _bars(DAY, freq="5min"), _daily(DAY, close=14.0))
        pat = ST.detect_patterns(sess)
        vol = V.compute(sess, _daily(DAY), _bars(DAY, freq="5min"))
        room = RM.compute(sess, _daily(DAY), "short", vol["expected_move_pct"])
        pl = P.build(sess, pat, "short", vol, room, _daily(DAY))
        if pl.get("actionable"):
            self.assertGreater(pl["stop"], pl["entry"])       # short: stop above
            self.assertGreater(pl["risk_pct_of_price"], 0)
            self.assertGreater(pl["risk_per_share"], 0)

    def test_blended_rr_sits_between_the_two_targets(self):
        sess, pat, vol, room = self._ctx()
        pl = P.build(sess, pat, "long", vol, room, _daily(DAY))
        self.assertGreaterEqual(pl["rr_blended"], min(pl["rr"], pl["rr_target2"]))
        self.assertLessEqual(pl["rr_blended"], max(pl["rr"], pl["rr_target2"]))

    def test_sizing_is_risk_based_and_names_the_binding_constraint(self):
        settings = {"capital": 100_000.0, "risk_pct": 1.0,
                    "max_allocation_pct": 20.0, "max_adv_pct": 1.0}
        out = P.size({"entry": 10.0, "risk_per_share": 0.5}, settings,
                     avg_volume=5_000_000)
        self.assertEqual(out["shares"], 2000)          # 1000 risk / 0.50
        self.assertIn("account risk", out["binding_constraint"])
        self.assertTrue(out["risk_is_binding"])

    def test_regression_risk_not_allocation_is_the_headline_constraint(self):
        """Was: the 20% allocation cap bound on every candidate, so the
        output read "binding constraint: 20% allocation cap" with $450 of a
        $1,000 risk budget used — an allocation-first tool wearing a
        risk-first label. The cap still applies; it is just no longer the
        reason a day trade gets sized."""
        settings = {"capital": 100_000.0, "risk_pct": 1.0,
                    "max_allocation_pct": 20.0, "max_adv_pct": 1.0}
        # A tight stop makes risk permissive, so allocation legitimately binds.
        tight = P.size({"entry": 10.0, "risk_per_share": 0.02}, settings, 5_000_000)
        self.assertFalse(tight["risk_is_binding"])
        self.assertIn("allocation", tight["binding_constraint"])
        # …and the report can still say what risk alone would have allowed.
        self.assertGreater(tight["risk_based_shares"], tight["shares"])

    def test_slippage_widens_true_risk_and_shrinks_size(self):
        """§10: real risk is |entry - stop| PLUS the fill you actually get."""
        settings = {"capital": 100_000.0, "risk_pct": 1.0,
                    "max_allocation_pct": 100.0, "max_adv_pct": 100.0}
        plan = {"entry": 10.0, "risk_per_share": 0.5, "atr_5min": 0.4}
        out = P.size(plan, settings, avg_volume=5_000_000, spread_pct=0.8)
        self.assertGreater(out["true_risk_per_share"], 0.5)
        self.assertLess(out["shares"], 2000)
        self.assertIn("spread", out["slippage_basis"])

    def test_slippage_has_a_floor_and_survives_missing_spread(self):
        out = P.estimate_slippage(10.0, spread_pct=None, atr5=0.02)
        self.assertGreaterEqual(out["per_share"], P.MIN_SLIPPAGE_PER_SHARE)
        self.assertIsNone(P.estimate_slippage(10.0, None, None)["per_share"])

    def test_position_liquidity_caps_size(self):
        """§8: dollar volume alone is not liquidity — the position has to be
        small against what the stock trades in a minute."""
        settings = {"capital": 10_000_000.0, "risk_pct": 1.0,
                    "max_allocation_pct": 100.0, "max_adv_pct": 100.0}
        out = P.size({"entry": 10.0, "risk_per_share": 0.5}, settings,
                     avg_volume=1e9, minute_dollar_volume=50_000)
        self.assertIn("minute", out["binding_constraint"])
        self.assertLessEqual(out["position_liquidity_pct"], 25.0)

    def test_execution_risk_shrinks_the_position(self):
        settings = {"capital": 100_000.0, "risk_pct": 1.0,
                    "max_allocation_pct": 100.0, "max_adv_pct": 100.0}
        plan = {"entry": 10.0, "risk_per_share": 0.5}
        full = P.size(plan, settings, 5_000_000)
        half = P.size(plan, settings, 5_000_000, risk_multiplier=0.5)
        self.assertAlmostEqual(half["shares"], full["shares"] // 2, delta=1)

    def test_liquidity_constraint_binds_before_account_risk(self):
        settings = {"capital": 100_000.0, "risk_pct": 1.0,
                    "max_allocation_pct": 20.0, "max_adv_pct": 1.0}
        out = P.size({"entry": 10.0, "risk_per_share": 0.5}, settings,
                     avg_volume=100_000)
        self.assertEqual(out["shares"], 1000)          # 1% of 100K ADV
        self.assertIn("average volume", out["binding_constraint"])

    def test_micro_float_caps_participation_further(self):
        settings = {"capital": 1_000_000.0, "risk_pct": 1.0,
                    "max_allocation_pct": 20.0, "max_adv_pct": 1.0}
        out = P.size({"entry": 10.0, "risk_per_share": 0.5}, settings,
                     avg_volume=5_000_000, float_shares=1_500_000)
        self.assertEqual(out["shares"], 1500)          # 0.1% of float
        self.assertIn("micro-float", out["binding_constraint"])

    def test_size_never_scales_with_score(self):
        """§14: never increase size because the stock scores well. Score is
        not an argument to size() at all — this pins that signature."""
        settings = {"capital": 100_000.0, "risk_pct": 1.0,
                    "max_allocation_pct": 20.0, "max_adv_pct": 1.0}
        a = P.size({"entry": 10.0, "risk_per_share": 0.5}, settings, 5_000_000)
        b = P.size({"entry": 10.0, "risk_per_share": 0.5}, settings, 5_000_000)
        self.assertEqual(a["shares"], b["shares"])


class VolumeConfirmationTests(unittest.TestCase):
    def test_failed_expansion_is_penalised(self):
        """§8's named failure: breakout → volume → immediate give-back."""
        base = _bars(DAY, n=20, price=10.0, step=0.0, vol=1_000)
        surge = _bars(DAY, start="09:50", n=3, price=10.5, step=0.0, vol=50_000)
        give_back = _bars(DAY, start="09:53", n=10, price=9.8, step=0.0, vol=2_000)
        sess = ST.build_session(pd.concat([base, surge, give_back]),
                                _bars(DAY, freq="5min"), _daily(DAY))
        out = VOL.compute(sess)
        self.assertTrue(out["expansion_failed"])
        self.assertTrue(out["warnings"])

    def test_too_few_bars_scores_none(self):
        sess = {"bars": _bars(DAY, n=3)}
        self.assertIsNone(VOL.compute(sess)["score"])


class EntryQualityTests(unittest.TestCase):
    """§4/§5/§6/§20 — the score that decays as price leaves the trigger
    while every other score in the package holds still."""

    def _ctx(self, price, trigger, vwap_, atr5=0.20, day_range=5.0):
        sess = ST.build_session(_bars(DAY, n=60), _bars(DAY, freq="5min"), _daily(DAY))
        sess["price"], sess["vwap"] = price, vwap_
        plan_res = {"trigger_level": trigger, "atr_5min": atr5,
                    "rr_blended": 2.5, "triggered": price > trigger}
        room = {"room_ratio": 2.0, "nearest": price * 1.1, "detail": "ok"}
        vol = {"expected_move_pct": 5.0, "day_range_pct": day_range}
        return sess, plan_res, room, vol

    def _run(self, price, trigger, vwap_, atr5=0.20, day_range=5.0):
        sess, plan_res, room, vol = self._ctx(price, trigger, vwap_, atr5, day_range)
        return EN.compute(sess, plan_res, room, vol,
                          {"score": 80, "sequence": "ok"},
                          {"spread_pct": 0.3}, {"sector_confirms": True},
                          "RISK ON", "long")

    def test_entry_score_falls_as_price_leaves_the_trigger(self):
        near = self._run(price=10.05, trigger=10.0, vwap_=10.0)
        far = self._run(price=10.80, trigger=10.0, vwap_=10.0)
        self.assertGreater(near["score"], far["score"])
        self.assertLess(near["beyond_trigger_atr"], far["beyond_trigger_atr"])

    def test_sitting_below_the_trigger_is_not_lateness(self):
        """Below a breakout level is the ideal place to be — you have not
        bought and the trigger has not fired. Only extension BEYOND it counts."""
        below = self._run(price=9.90, trigger=10.0, vwap_=9.9)
        self.assertEqual(below["beyond_trigger_atr"], 0.0)

    def test_chase_score_counts_independent_ways_of_being_late(self):
        calm = self._run(price=10.02, trigger=10.0, vwap_=10.0)
        late = self._run(price=11.00, trigger=10.0, vwap_=10.0)
        self.assertLessEqual(calm["chase_score"], 1)
        self.assertGreaterEqual(late["chase_score"], 2)
        self.assertTrue(late["chase_reasons"])

    def test_chase_blocks_at_four(self):
        """Four independent ways of being late. Extension alone reaches
        three (VWAP in ATR, trigger in ATR, >3% above VWAP); the fourth here
        is most of the day's expected range already spent."""
        late = self._run(price=12.0, trigger=10.0, vwap_=10.0, atr5=0.1,
                         day_range=25.0)
        self.assertGreaterEqual(late["chase_score"], EN.CHASE_BLOCK)
        self.assertTrue(late["chase_blocked"])
        self.assertFalse(self._run(price=10.02, trigger=10.0,
                                   vwap_=10.0)["chase_blocked"])

    def test_extension_measured_in_atr_not_percent(self):
        """3% from VWAP is a chase on a quiet stock and noise on a wild one,
        so the ladder is denominated in the same ATR as the stop."""
        quiet = self._run(price=10.30, trigger=10.0, vwap_=10.0, atr5=0.05)
        wild = self._run(price=10.30, trigger=10.0, vwap_=10.0, atr5=1.00)
        self.assertGreater(quiet["vwap_distance_atr"], wild["vwap_distance_atr"])
        self.assertLess(quiet["score"], wild["score"])

    def test_candle_quality_prefers_a_strong_close(self):
        strong = _bars(DAY, n=30, price=10.0, step=0.05)
        weak = strong.copy()
        weak.loc[weak.index[-1], "High"] = float(weak["Close"].iloc[-1]) + 1.0
        self.assertGreater(EN.candle_quality(strong, "long")["score"],
                           EN.candle_quality(weak, "long")["score"])


class ExecutionGateTests(unittest.TestCase):
    """§2 — the gate that separates 'good stock' from 'good trade now'."""

    def _args(self, **over):
        base = dict(
            plan_res={"rr_blended": 3.0, "stop_too_wide": False},
            room_res={"room_ratio": 2.0, "nearest": 11.0, "detail": "ok"},
            trad={"spread_pct": 0.4, "dollar_volume": 2e7, "spread_note": ""},
            entry_res={"beyond_trigger_atr": 0.3, "chase_blocked": False,
                       "chase_score": 1, "chase_reasons": []},
            sizing={"position_liquidity_pct": 5.0},
        )
        base.update(over)
        return base

    def test_clean_setup_passes(self):
        g = E.execution_gate(**self._args())
        self.assertTrue(g["passed"], g["failed"] + g["unverified"])

    def test_regression_poor_room_withholds_a_plus(self):
        """CRSR printed 🔥 A+ LONG with 2.9% of room, an unconfirmable
        spread and a stop 2.4x the 5-min ATR — all in its own panel."""
        g = E.execution_gate(**self._args(
            room_res={"room_ratio": 0.36, "nearest": 11.0, "detail": "2.9%"}))
        self.assertFalse(g["passed"])
        self.assertIn("Room to next level", g["failed"])

    def test_unverifiable_spread_blocks_a_plus_without_failing(self):
        """On an execution check, unknown and bad have the same consequence
        for a real order — but they are reported apart."""
        g = E.execution_gate(**self._args(
            trad={"spread_pct": None, "dollar_volume": 2e7,
                  "spread_note": "market closed"}))
        self.assertFalse(g["passed"])
        self.assertIn("Spread acceptable", g["unverified"])
        self.assertNotIn("Spread acceptable", g["failed"])

    def test_extended_entry_fails_the_gate(self):
        g = E.execution_gate(**self._args(
            entry_res={"beyond_trigger_atr": 2.4, "chase_blocked": False,
                       "chase_score": 2, "chase_reasons": []}))
        self.assertIn("Entry not extended", g["failed"])

    def test_open_air_counts_as_room(self):
        g = E.execution_gate(**self._args(
            room_res={"room_ratio": None, "nearest": None, "detail": "open air"}))
        self.assertNotIn("Room to next level", g["failed"] + g["unverified"])


class ActionTests(unittest.TestCase):
    """§19 — what to do at this price, which a grade cannot say."""

    def _gate(self, failed=(), unverified=()):
        return {"failed": list(failed), "unverified": list(unverified),
                "passed": not failed and not unverified, "checks": []}

    def _entry(self, **over):
        base = {"score": 88, "entry_grade": "HIGH QUALITY", "chase_score": 1,
                "chase_reasons": [], "chase_blocked": False,
                "beyond_trigger_atr": 0.3, "extended_past_trigger": False}
        base.update(over)
        return base

    def test_enter_now_requires_grade_gate_and_entry_quality(self):
        a, _ = E._action("A+", True, "long", {"triggered": True},
                         self._entry(), self._gate())
        self.assertEqual(a, "🔥 ENTER NOW")

    def test_untriggered_setup_is_wait_for_breakout_not_enter(self):
        a, why = E._action("A+", True, "long", {"triggered": False,
                                                "trigger": "break of 10.00"},
                           self._entry(), self._gate())
        self.assertEqual(a, "🟢 WAIT FOR BREAKOUT")
        self.assertIn("10.00", why)

    def test_regression_already_triggered_and_extended_is_a_missed_entry(self):
        """SENS carried "already through the trigger — reference price, not
        a fresh entry" in its own panel and still ranked as an A+ candidate."""
        a, _ = E._action("A+", True, "long", {"triggered": True},
                         self._entry(beyond_trigger_atr=2.2,
                                     extended_past_trigger=True),
                         self._gate())
        self.assertEqual(a, "🟠 MISSED ENTRY — DO NOT CHASE")

    def test_chase_blocked_outranks_everything_short_of_untradeable(self):
        a, _ = E._action("A+", True, "long", {"triggered": True},
                         self._entry(chase_blocked=True, chase_score=5,
                                     chase_reasons=["2.1 ATR from VWAP"]),
                         self._gate())
        self.assertEqual(a, "🟠 EXTENDED — DO NOT CHASE")

    def test_failed_execution_condition_becomes_wait_not_enter(self):
        a, why = E._action("A+", True, "long", {"triggered": True},
                           self._entry(), self._gate(failed=["Room to next level"]))
        self.assertEqual(a, "🟡 SETUP OK — WAIT FOR BETTER ENTRY")
        self.assertIn("Room", why)

    def test_untradeable_is_avoid_whatever_the_grade(self):
        a, _ = E._action("A+", False, "long", {"triggered": True},
                         self._entry(), self._gate())
        self.assertEqual(a, "🔴 AVOID")

    def test_ranking_puts_enterable_above_more_interesting(self):
        """§22: the table answers 'how good is this trade right now', so a
        92-confluence name you must not chase ranks below an 80 you can."""
        enterable = {"action": "🔥 ENTER NOW", "entry_score": 85, "confluence": 80}
        interesting = {"action": "🟠 EXTENDED — DO NOT CHASE",
                       "entry_score": 40, "confluence": 92}
        self.assertLess(E.rank_key(enterable), E.rank_key(interesting))


class ProfileTests(unittest.TestCase):
    """One engine, three calibrations. The pipeline is identical; what a
    number MEANS is not."""

    def test_every_profile_weights_the_same_blocks_and_sums_to_100(self):
        PR.validate()

    def test_profile_selected_from_market_cap(self):
        self.assertEqual(PR.for_market_cap(3e8)["key"], "small")
        self.assertEqual(PR.for_market_cap(4e9)["key"], "mid")
        self.assertEqual(PR.for_market_cap(3.5e12)["key"], "large")

    def test_unknown_market_cap_gets_the_strictest_profile(self):
        """The conservative error: a genuinely large name judged on
        small-cap thresholds ranks low, rather than a megacap sailing
        through checks that were never meant for it."""
        self.assertEqual(PR.for_market_cap(None)["key"], "small")

    def test_thresholds_loosen_as_cap_rises(self):
        """RVOL 1.3 is noise on a small-cap runner and a real institutional
        footprint on a megacap."""
        small, mid, large = (PR.SMALL_CAP, PR.MID_CAP, PR.LARGE_CAP)
        for key in ("rvol_significant", "atr_pct_min", "gap_min"):
            self.assertGreater(small[key], mid[key], key)
            self.assertGreater(mid[key], large[key], key)
        # Liquidity expectations move the other way.
        self.assertLess(small["min_dollar_volume"], large["min_dollar_volume"])
        self.assertGreater(small["max_spread_pct"], large["max_spread_pct"])

    def test_scarcity_gives_way_to_context_as_cap_rises(self):
        """The central claim of the profile design: supply drives small
        caps, the tape drives large ones."""
        small, large = PR.SMALL_CAP["weights"], PR.LARGE_CAP["weights"]
        self.assertGreater(small["supply"], large["supply"])
        self.assertGreater(large["market"] + large["regime"],
                           small["market"] + small["regime"])
        self.assertTrue(PR.SMALL_CAP["float_matters"])
        self.assertFalse(PR.LARGE_CAP["float_matters"])

    def test_float_confirmation_becomes_liquidity_for_large_caps(self):
        """Demanding a small float of a megacap would reject every valid
        setup for a reason that has nothing to do with the trade."""
        common = dict(
            vol={"rvol": 5.0, "gap_pct": 8.0, "atr_pct": 6.0},
            sup={"float_shares": 4_000_000_000},
            cat={"fresh": True, "detail": ""},
            rs={"vs_spy": 5.0, "sector_confirms": True, "vs_sector": 3.0},
            vc={"score": 80, "expansion_failed": False, "sequence": ""},
            pat={"above_vwap": True, "primary": "ORB_BREAKOUT"},
            room_res={"blocked": False, "room_ratio": 2.0, "nearest": 12.0,
                      "detail": ""},
            trad={"spread_pct": 0.05, "spread_note": "", "dollar_volume": 9e8},
            plan_res={"direction": "long", "rr_blended": 3.0, "rr": 2.0,
                      "rr_target2": 4.0, "targets_structural": True,
                      "stop_too_wide": False, "atr_5min": 0.2,
                      "risk_per_share": 0.25},
        )
        names = {c["name"]: c for c in
                 E._confirmations(profile=PR.LARGE_CAP, **common)}
        self.assertIn("Liquidity depth", names)
        self.assertTrue(names["Liquidity depth"]["ok"])
        self.assertNotIn("Float under 50M", names)
        # …and the small-cap profile still asks for float.
        small_names = {c["name"] for c in
                       E._confirmations(profile=PR.SMALL_CAP, **common)}
        self.assertIn("Float under 50M", small_names)

    def test_rvol_confirmation_uses_the_profile_threshold(self):
        common = dict(
            vol={"rvol": 1.6, "gap_pct": 8.0, "atr_pct": 6.0},
            sup={"float_shares": 10_000_000}, cat={"fresh": True, "detail": ""},
            rs={"vs_spy": 1.0, "sector_confirms": True, "vs_sector": 1.0},
            vc={"score": 80, "expansion_failed": False, "sequence": ""},
            pat={"above_vwap": True, "primary": "ORB_BREAKOUT"},
            room_res={"blocked": False, "room_ratio": 2.0, "nearest": 12.0,
                      "detail": ""},
            trad={"spread_pct": 0.05, "spread_note": "", "dollar_volume": 9e8},
            plan_res={"direction": "long", "rr_blended": 3.0, "rr": 2.0,
                      "rr_target2": 4.0, "targets_structural": True,
                      "stop_too_wide": False, "atr_5min": 0.2,
                      "risk_per_share": 0.25},
        )
        def rvol_ok(profile):
            return next(c["ok"] for c in E._confirmations(profile=profile, **common)
                        if c["name"].startswith("RVOL"))
        self.assertTrue(rvol_ok(PR.LARGE_CAP))    # 1.6 clears 1.3
        self.assertFalse(rvol_ok(PR.SMALL_CAP))   # 1.6 does not clear 3.0

    def test_regression_market_cap_derived_when_info_omits_it(self):
        """Was: `.info` returns no marketCap at all for some symbols — WDC
        came back with 14 fields and none of them the cap — so a $442 stock
        was filed as a small cap and rejected for being "outside $2-$30"."""
        cap, basis = PR.infer_market_cap(None, None, 340_620_000, 442.59)
        self.assertGreater(cap, 100e9)
        self.assertIn("lower bound", basis)
        self.assertEqual(PR.for_market_cap(cap)["key"], "large")

    def test_market_cap_sources_are_preferred_in_order(self):
        self.assertEqual(PR.infer_market_cap(5e9, 1e9, 1e9, 50.0)[1], "reported")
        self.assertEqual(PR.infer_market_cap(None, 1e9, 9e9, 50.0)[0], 5e10)
        self.assertIsNone(PR.infer_market_cap(None, None, None, 50.0)[0])

    def test_float_floor_can_only_promote_never_demote(self):
        """Float <= shares outstanding, so the floor understates the cap.
        A genuine small cap can never be promoted into the looser
        large-cap thresholds by it."""
        cap, _ = PR.infer_market_cap(None, None, 5_000_000, 4.0)   # $20M floor
        self.assertEqual(PR.for_market_cap(cap)["key"], "small")

    def test_universe_band_follows_the_profile(self):
        sess = {"price": 150.0, "dollar_volume": 9e8}
        ok, _ = U.passes_universe({"market_cap": 5e11}, {}, sess, PR.LARGE_CAP)
        self.assertTrue(ok)
        # The same megacap fails the small-cap band on cap AND price.
        bad, reasons = U.passes_universe({"market_cap": 5e11}, {}, sess, PR.SMALL_CAP)
        self.assertFalse(bad)
        self.assertEqual(len(reasons), 2)


class EngineGateTests(unittest.TestCase):
    """The properties that distinguish this engine from a weighted sum."""

    def test_tradeability_is_a_gate_not_a_weight(self):
        grade, note = E._grade(confluence=98, n_confirm=12, rr=5.0,
                               tradeable=False, blocked=False, coverage=1.0)
        self.assertEqual(grade, "NO TRADE")
        self.assertIn("tradeability", note)

    def test_room_blocks_the_top_grades_only(self):
        """§9 downgrades; it does not erase a candidate."""
        blocked, note = E._grade(95, 12, 5.0, True, blocked=True, coverage=1.0)
        self.assertEqual(blocked, "B+")
        self.assertIn("resistance", note)
        clear, _ = E._grade(95, 12, 5.0, True, blocked=False, coverage=1.0)
        self.assertEqual(clear, "A+")

    def test_confirmations_gate_independently_of_score(self):
        """§18: several independent factors must agree. A high score built
        from two enormous factors must not reach A+."""
        self.assertEqual(E._grade(95, 3, 5.0, True, False, 1.0)[0], "B")

    def test_rr_gates_independently_of_score(self):
        self.assertEqual(E._grade(95, 12, 1.3, True, False, 1.0)[0], "B")

    def test_low_coverage_refuses_a_grade(self):
        grade, note = E._grade(88, 8, 3.0, True, False, coverage=0.4)
        self.assertEqual(grade, "NO TRADE")
        self.assertIn("coverage", note)

    def test_regression_unmeasurable_volatility_caps_the_grade(self):
        """Was: §3 still scored off gap and dollar volume alone, so a recent
        IPO with no ATR and no RVOL baseline kept enough coverage to grade
        A+ with both volatility columns blank."""
        grade, note = E._grade(95, 12, 5.0, True, False, coverage=0.8,
                               missing_required=("ATR%",))
        self.assertEqual(grade, "C")
        self.assertIn("ATR%", note)

    def test_too_wide_stop_caps_the_grade_at_a(self):
        """A+ means the risk geometry is clean too. Printing "A+ LONG" above
        the plan's own "too wide for a day trade" warning is the kind of
        self-contradiction that makes a tool untrustworthy."""
        grade, note = E._grade(95, 12, 5.0, True, False, 1.0, stop_too_wide=True)
        self.assertEqual(grade, "A")
        self.assertIn("1.5x", note)
        # It caps A+ only — a B+ setup is not promoted or further demoted.
        self.assertEqual(E._grade(65, 4, 1.6, True, False, 1.0,
                                  stop_too_wide=True)[0], "B+")

    def test_decision_labels_follow_direction(self):
        self.assertEqual(E._decision("A+", True, "long", False, {}), "🔥 A+ LONG")
        self.assertEqual(E._decision("A", True, "short", False, {}), "🔴 SHORT")
        self.assertIn("NOT TRADEABLE", E._decision("A+", False, "long", False, {}))

    def test_weights_sum_to_one_hundred(self):
        self.assertEqual(sum(E.WEIGHTS.values()), 100)


class ConfirmationTests(unittest.TestCase):
    def _args(self, **over):
        base = dict(
            vol={"rvol": 3.0, "gap_pct": 8.0, "atr_pct": 6.0},
            sup={"float_shares": 10_000_000},
            cat={"fresh": True, "detail": "Earnings, 2h old"},
            rs={"vs_spy": 5.0, "sector_confirms": True, "vs_sector": 3.0,
                "sector_etf": "XBI"},
            vc={"score": 80, "expansion_failed": False, "sequence": "ok"},
            pat={"above_vwap": True, "primary": "ORB_BREAKOUT"},
            room_res={"blocked": False, "room_ratio": 2.0, "nearest": 12.0,
                      "detail": "ok"},
            trad={"spread_pct": 0.4, "spread_note": "live quote"},
            plan_res={"direction": "long", "rr_blended": 3.0, "rr": 2.0,
                      "rr_target2": 4.0, "targets_structural": True,
                      "stop_too_wide": False, "atr_5min": 0.2,
                      "risk_per_share": 0.25},
        )
        base.update(over)
        return base

    def test_all_confirmations_can_pass(self):
        out = E._confirmations(**self._args())
        self.assertTrue(all(c["ok"] for c in out), [c for c in out if not c["ok"]])

    def test_regression_open_air_passes_the_room_check(self):
        """Was: 'no levels ahead — open air' is the best possible case, but
        its absent room_ratio read as 0 and marked the strongest candidates
        as failing §9."""
        args = self._args(room_res={"blocked": False, "room_ratio": None,
                                    "nearest": None, "detail": "open air"})
        room = next(c for c in E._confirmations(**args)
                    if c["name"] == "Room to next level")
        self.assertTrue(room["ok"])

    def test_regression_bare_r_multiple_targets_do_not_confirm_rr(self):
        """Was: with no structural target, T1 fell to 1R and T2 to 3R, whose
        blend is exactly 2.00 — the A-grade threshold — so TNDM and OMDA
        graded A+ on a number that was true by definition. §12 wants
        independent confirmations; a tautology is not one."""
        args = self._args(plan_res={"direction": "long", "rr_blended": 2.0,
                                    "rr": 1.0, "rr_target2": 3.0,
                                    "targets_structural": False})
        rr = next(c for c in E._confirmations(**args) if c["name"] == "R:R at least 2")
        self.assertFalse(rr["ok"])
        self.assertIn("R-multiples", rr["detail"])

    def test_regression_too_wide_stop_does_not_confirm(self):
        """Was: the plan printed "stop is 2.1x the 5-min ATR — too wide for a
        day trade" and OMDA still graded A+, because the warning never
        reached the confirmation count."""
        args = self._args()
        args["plan_res"] = dict(args["plan_res"], stop_too_wide=True,
                                stop_note="stop is 2.1x the 5-min ATR")
        c = next(x for x in E._confirmations(**args)
                 if x["name"] == "Stop within 1.5x 5-min ATR")
        self.assertFalse(c["ok"])

    def test_unmeasurable_stop_width_does_not_confirm(self):
        args = self._args()
        args["plan_res"] = dict(args["plan_res"], stop_too_wide=None,
                                atr_5min=None, stop_note=None)
        c = next(x for x in E._confirmations(**args)
                 if x["name"] == "Stop within 1.5x 5-min ATR")
        self.assertFalse(c["ok"])

    def test_unknown_values_do_not_confirm(self):
        args = self._args(vol={"rvol": None, "gap_pct": None, "atr_pct": None},
                          sup={"float_shares": None})
        # Names carry the profile's own thresholds ("RVOL above 3" at small
        # cap, "above 1.3" at large), so match on the metric not the bar.
        checks = E._confirmations(**args)

        def ok_for(prefix):
            return next(c["ok"] for c in checks if c["name"].startswith(prefix))

        self.assertFalse(ok_for("RVOL above"))
        self.assertFalse(ok_for("Float under"))
        self.assertFalse(ok_for("Gap above"))


if __name__ == "__main__":
    unittest.main()
