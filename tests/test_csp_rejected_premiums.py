"""
Tests for pricing rejected names as REFERENCE.

The request behind this was "do not reject, give the CSP premiums, let the
user decide based on risk". What was built shows the premiums and keeps the
gate, and the difference matters:

  1. A rejection still rejects. Nothing priced here can turn a REJECT into a
     SELL, and no priced rejection enters the ranked opportunity list. That
     ordering — company gate before any chain — is the design decision the
     whole package rests on; what changed is that the rejection now arrives
     WITH the numbers instead of with blank columns.
  2. The budget is spent best-business-first. Rejections are ~90% of a run
     and each costs a chain round trip, so the cap is a time budget. Spent
     in quality order because "98/100 Elite, rejected on price" is what
     anyone means by "let me judge the premium myself"; a name that failed
     at LQuality 41 is not.
  3. "Best" means the richest FILLABLE strike. The richest quote on a board
     is usually a contract nobody will trade.
  4. The store must stop throwing the chain away. Rejected rows are slimmed
     on save, and `reference` was among the casualties — the row was
     re-fetched precisely to obtain it.
  5. The reason travels with the premium. A table of fat yields on rejected
     names without the rejection beside each one is the exact misreading
     this engine exists to refuse.

Run with: python -m unittest tests.test_csp_rejected_premiums
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.csp import engine as E
from stockanalysis.core.csp import store as CS
from stockanalysis.webapp import csp_view as CV


def _lt(ticker="CRDO", quality=98, tier="Elite", band="OVERVALUED"):
    """A long-term result the CSP gate will reject on valuation alone."""
    return {
        "ticker": ticker, "name": f"{ticker} Inc.", "price": 249.26,
        "sector": "Technology", "action": "WATCH",
        "quality": {"score": quality, "tier": tier, "coverage": 0.9},
        "valuation": {"band": band, "acceptable": False,
                      "headline": "Price requires 80% growth a year"},
        "trend": {"state": "CONFIRMED"}, "pullback": {"stage": "STAGE1_EMA"},
        "investment": {"status": "WATCHLIST"}, "confluence": {}, "rs": {},
    }


def _ref(best=True):
    return {"available": True, "expiry": "2026-09-18", "dte": 31,
            "atm_iv": 0.95, "quotes_live": True, "fillable": 8,
            "strikes": [{"strike": 240.0}],
            "best": ({"strike": 240.0, "delta": -0.386, "limit_price": 25.76,
                      "yield_pct": 10.73, "annualised": 126.4,
                      "breakeven": 214.24, "basis_vs_spot_pct": -14.0,
                      "distance_pct": -3.7, "liquidity": 52,
                      "spread_verdict": "EXCELLENT", "expiry": "2026-09-18",
                      "dte": 31, "delta_class": "High assignment risk"}
                     if best else None)}


class TheGateStillHolds(unittest.TestCase):
    """(1) Showing a premium is not offering a trade."""

    def test_a_rejected_name_stays_rejected_with_a_chain_attached(self):
        with mock.patch.object(E, "_reference_chain", return_value=_ref()):
            row = E.evaluate(_lt(), reference_chain=True)
        self.assertEqual(row["final"]["key"], "REJECT")
        self.assertIsNone(row["chosen"])
        self.assertIsNone(row["csp_score"])
        self.assertTrue(row["reference"]["available"])

    def test_no_premium_can_produce_a_score(self):
        """A REJECT has no csp_score at all, so it cannot be ranked."""
        with mock.patch.object(E, "_reference_chain", return_value=_ref()):
            row = E.evaluate(_lt(), reference_chain=True)
        self.assertIsNone(row.get("csp_score"))
        self.assertIsNone(row.get("stock_score"))

    def test_without_the_flag_no_chain_is_fetched(self):
        with mock.patch.object(E, "_reference_chain") as ref:
            row = E.evaluate(_lt())
        ref.assert_not_called()
        self.assertNotIn("reference", row)

    def test_the_quality_scalars_survive_for_the_reader(self):
        with mock.patch.object(E, "_reference_chain", return_value=_ref()):
            row = E.evaluate(_lt(), reference_chain=True)
        self.assertEqual(row["lquality"], 98)
        self.assertEqual(row["lq_tier"], "Elite")


class BudgetSpentOnTheBestBusinesses(unittest.TestCase):
    """(2) The cap is a time budget, spent in quality order."""

    def _run(self, results, **kw):
        # live_prices=False: these fixtures are invented tickers, and the
        # budget is what is under test, not the quote feed. Without it the
        # suite makes a real network call per case.
        kw.setdefault("live_prices", False)
        with mock.patch.object(E, "_reference_chain", return_value=_ref()):
            return E.evaluate_universe(results, **kw)

    def test_off_by_default(self):
        rows = self._run([_lt("A"), _lt("B")])
        self.assertTrue(all("reference" not in r for r in rows))

    def test_on_when_asked(self):
        rows = self._run([_lt("A"), _lt("B")], reference_rejected=True)
        self.assertTrue(all(r.get("reference") for r in rows))

    def test_the_cap_is_honoured(self):
        many = [_lt(f"T{i}", quality=90) for i in range(E.MAX_REFERENCE_CHAINS + 6)]
        rows = self._run(many, reference_rejected=True)
        priced = [r for r in rows if r.get("reference")]
        self.assertEqual(len(priced), E.MAX_REFERENCE_CHAINS)

    def test_the_budget_goes_to_the_best_businesses_first(self):
        results = ([_lt(f"LOW{i}", quality=41, tier="Reject")
                    for i in range(E.MAX_REFERENCE_CHAINS)]
                   + [_lt("ELITE", quality=98)])
        rows = self._run(results, reference_rejected=True)
        priced = {r["ticker"] for r in rows if r.get("reference")}
        self.assertIn("ELITE", priced)


class StorePreservesTheChain(unittest.TestCase):
    """(4) The row was re-fetched precisely to obtain this."""

    def test_reference_and_quality_survive_the_slimming(self):
        self.assertIn("reference", CS._REJECT_KEEP)
        self.assertIn("lquality", CS._REJECT_KEEP)
        self.assertIn("lq_tier", CS._REJECT_KEEP)

    def test_the_full_audit_marker_is_not_kept(self):
        """csp_page reads `eligibility` to mean 'the full audit is here'.
        Keeping it on every slimmed row would make that claim false."""
        self.assertNotIn("eligibility", CS._REJECT_KEEP)


class ThePricedRejectTable(unittest.TestCase):
    """(3) and (5): what the block shows, and what it shows beside it."""

    def _row(self, ticker="CRDO", best=True, why="overvalued — Price requires"):
        return {"ticker": ticker, "name": f"{ticker} Inc.",
                "sector": "Technology", "price": 249.26,
                "lquality": 98, "lq_tier": "Elite",
                "final": {"key": "REJECT", "action": "🔴 REJECT", "why": why},
                "reference": _ref(best)}

    def test_nothing_renders_without_a_priced_chain(self):
        self.assertEqual(CV._priced_rejects([self._row(best=False)]), "")
        self.assertEqual(CV._priced_rejects([]), "")

    def test_the_premium_is_shown(self):
        html = CV._priced_rejects([self._row()])
        for fragment in ("240.00", "25.76", "126.4", "214.24", "CRDO"):
            self.assertIn(fragment, html)

    def test_the_rejection_travels_with_it(self):
        """(5) Either half alone is misleading."""
        html = CV._priced_rejects([self._row()])
        self.assertIn("overvalued", html)
        self.assertIn("REFERENCE ONLY", html)
        self.assertIn("not recommendations", html)

    def test_quality_is_shown_beside_the_premium(self):
        html = CV._priced_rejects([self._row()])
        self.assertIn("Elite", html)
        self.assertIn("98", html)

    def test_best_business_first(self):
        rows = [self._row("LOW"), self._row("HIGH")]
        rows[0]["lquality"] = 41
        html = CV._priced_rejects(rows)
        self.assertLess(html.index("HIGH"), html.index("LOW"))

    def test_a_closed_market_is_called_out(self):
        row = self._row()
        row["reference"]["quotes_live"] = False
        self.assertIn("market closed", CV._priced_rejects([row]))

    def test_the_block_leads_the_grouped_list(self):
        html = CV._rejected_block([self._row()])
        self.assertIn("Rejected, priced anyway", html)
        self.assertLess(html.index("Rejected, priced anyway"),
                        html.index("hover a ticker"))

    def test_the_empty_case_says_how_to_get_them(self):
        plain = {"ticker": "X", "final": {"key": "REJECT",
                                          "why": "overvalued — too dear"}}
        html = CV._rejected_block([plain])
        self.assertNotIn("Rejected, priced anyway", html)
        self.assertIn("Price rejects", html)


if __name__ == "__main__":
    unittest.main()


class EarningsAcceptIsTwoPass(unittest.TestCase):
    """The ACCEPT policy was unreachable.

    `earnings_gate` is asked once by the expiry filter — before any chain
    exists — and once after a strike is priced. All four of ACCEPT's
    conditions are properties of a CONTRACT, so evaluating them in the
    first call meant passing four Nones: every condition failed, ACCEPT
    silently behaved as CONTROLLED, and the reason printed "delta 1.00 >
    0.2" about a contract that had not been selected.

    The rule that makes it unrepresentable now: no contract inputs, no
    verdict. Which pass you get is inferred from what you supplied.
    """

    from stockanalysis.core.csp import risk as RK

    INSIDE = {"inside": True, "detail": "earnings 9d out — inside"}
    CLEAR = {"inside": False, "detail": "earnings clear of expiry"}

    def test_the_expiry_filter_gets_admission_not_judgment(self):
        g = self.RK.earnings_gate(self.INSIDE, "ACCEPT")
        self.assertTrue(g["allow"])
        self.assertTrue(g["pending"])
        self.assertEqual(g["penalty_option"], 0)
        # The bug's fingerprint: a verdict about a contract that does not
        # exist yet.
        self.assertNotIn("delta 1.00", g["why"])
        self.assertNotIn("ACCEPT not met", g["why"])

    def test_supplying_a_contract_settles_it(self):
        g = self.RK.earnings_gate(self.INSIDE, "ACCEPT", quality=95,
                                  delta=-0.15, liquidity=90, adequacy=1.6)
        self.assertTrue(g["allow"])
        self.assertFalse(g.get("pending"))
        self.assertEqual(g["penalty_option"], 0)
        self.assertIn("accepted", g["why"])

    def test_a_weak_contract_falls_back_to_controlled(self):
        g = self.RK.earnings_gate(self.INSIDE, "ACCEPT", quality=72,
                                  delta=-0.19, liquidity=100, adequacy=1.58)
        self.assertTrue(g["allow"])
        self.assertGreater(g["penalty_option"], 0)
        # Names the ONE thing that actually failed, not all four.
        self.assertIn("quality 72", g["why"])
        self.assertNotIn("delta", g["why"])
        self.assertNotIn("liquidity", g["why"])

    def test_one_condition_short_is_reported_as_one_condition(self):
        """ADBE's live shape: 91 quality, 78 liquidity, 2.25x premium, and
        a delta of 0.233 against a 0.20 cap."""
        g = self.RK.earnings_gate(self.INSIDE, "ACCEPT", quality=91,
                                  delta=-0.233, liquidity=78, adequacy=2.25)
        self.assertIn("delta 0.23", g["why"])
        self.assertNotIn("quality", g["why"])

    def test_avoid_still_skips_the_expiry_outright(self):
        g = self.RK.earnings_gate(self.INSIDE, "AVOID")
        self.assertFalse(g["allow"])

    def test_controlled_needs_no_contract_to_decide(self):
        """It penalises unconditionally, so it settles on the first call."""
        g = self.RK.earnings_gate(self.INSIDE, "CONTROLLED")
        self.assertTrue(g["allow"])
        self.assertGreater(g["penalty_option"], 0)
        self.assertFalse(g.get("pending"))

    def test_no_policy_applies_when_the_print_is_clear_of_the_expiry(self):
        for pol in ("AVOID", "CONTROLLED", "ACCEPT"):
            g = self.RK.earnings_gate(self.CLEAR, pol)
            self.assertTrue(g["allow"], pol)
            self.assertEqual(g["penalty_option"], 0, pol)
            self.assertFalse(g.get("pending"), pol)

    def test_the_engine_settles_a_pending_gate(self):
        """The pending flag has to be acted on, or ACCEPT never resolves."""
        import inspect
        from stockanalysis.core.csp import engine as E
        src = inspect.getsource(E.evaluate)
        self.assertIn('gate.get("pending")', src)
        self.assertIn("settle=True", src)


class SpotIsLive(unittest.TestCase):
    """The CSP page priced everything against the research library's last
    scan, not a live quote.

    That is not cosmetic. Spot decides which strikes are out of the money,
    every greek, the expected move, the probability of profit and the
    basis discount — so a chain fetched seconds ago was being priced
    against a spot from days ago. Measured 2026-08-18 the library was 5.4%
    out on PODD and 2.6% on CRDO; a 5% error in spot does not shade a
    delta, it picks a different strike.

    Same asymmetry entry_alerts.py already states: stored levels, live
    price.
    """

    def test_the_stored_price_is_replaced_and_the_drift_recorded(self):
        rows = [{"ticker": "PODD", "price": 139.70, "quality": {"score": 90}}]
        with mock.patch.object(E, "live_spots",
                               return_value={"PODD": 147.29}), \
             mock.patch.object(E, "evaluate", side_effect=lambda r, *a, **k: r):
            out = E.evaluate_universe(rows)
        row = out[0]
        self.assertEqual(row["price"], 147.29)
        self.assertEqual(row["spot_source"], "live")
        self.assertEqual(row["spot_stored"], 139.70)
        self.assertAlmostEqual(row["spot_drift_pct"], 5.43, places=1)

    def test_an_unquotable_symbol_keeps_its_stored_price_and_says_so(self):
        """Never a stale number dressed as a fresh one."""
        rows = [{"ticker": "WEIRD", "price": 10.0, "quality": {"score": 90}}]
        with mock.patch.object(E, "live_spots", return_value={}), \
             mock.patch.object(E, "evaluate", side_effect=lambda r, *a, **k: r):
            out = E.evaluate_universe(rows)
        self.assertEqual(out[0]["price"], 10.0)
        self.assertEqual(out[0]["spot_source"], "stored")
        self.assertIsNone(out[0]["spot_drift_pct"])

    def test_quoting_can_be_turned_off(self):
        rows = [{"ticker": "PODD", "price": 139.70, "quality": {"score": 90}}]
        with mock.patch.object(E, "live_spots") as q, \
             mock.patch.object(E, "evaluate", side_effect=lambda r, *a, **k: r):
            E.evaluate_universe(rows, live_prices=False)
        q.assert_not_called()

    def test_it_is_on_by_default(self):
        """The default is the whole point — a scan the user just ran must
        not reprice against last week."""
        rows = [{"ticker": "PODD", "price": 139.70, "quality": {"score": 90}}]
        with mock.patch.object(E, "live_spots", return_value={}) as q, \
             mock.patch.object(E, "evaluate", side_effect=lambda r, *a, **k: r):
            E.evaluate_universe(rows)
        q.assert_called_once()

    def test_live_spots_never_raises_on_a_bad_feed(self):
        with mock.patch.dict("sys.modules", {"yfinance": None}):
            self.assertEqual(E.live_spots(["AAPL"]), {})
        self.assertEqual(E.live_spots([]), {})
        self.assertEqual(E.live_spots(None), {})

    def test_the_cell_distinguishes_live_from_stored(self):
        from stockanalysis.webapp import csp_view as CV
        live = CV._price_cell({"price": 100.0, "spot_source": "live",
                               "spot_drift_pct": 0.2})
        stored = CV._price_cell({"price": 100.0, "spot_source": "stored"})
        drifted = CV._price_cell({"price": 147.29, "spot_source": "live",
                                  "spot_drift_pct": 5.43,
                                  "spot_stored": 139.70})
        self.assertIn("live", live)
        self.assertIn("last scan", stored)
        # A big drift also means the company columns on that row were
        # computed from the old price.
        self.assertIn("rescan research", drifted)
        self.assertIn("139.70", drifted)

    def test_provenance_survives_the_store_s_slimming(self):
        for key in ("spot_source", "spot_stored", "spot_drift_pct"):
            self.assertIn(key, CS._REJECT_KEEP)


class SingleTickerQuotes(unittest.TestCase):
    """live_spots() silently found nothing for a one-ticker request.

    With group_by="ticker" yfinance returns a MultiIndex even for a single
    symbol, so keying the column lookup off len(chunk) meant every
    one-ticker call fell through to the stored price — and a per-row rescan
    and a small named scan are exactly one-ticker calls. ABBV was 3.2% out
    and reported itself as "stored"; priced correctly its verdict moved
    from REJECT to WAIT_IV.
    """

    def _frame(self, tickers, multi):
        import pandas as pd
        if multi:
            cols = pd.MultiIndex.from_product([tickers, ["Close", "Open"]])
            return pd.DataFrame([[10.0, 9.0] * len(tickers)], columns=cols)
        return pd.DataFrame({"Close": [10.0], "Open": [9.0]})

    def test_a_single_ticker_multiindex_frame_is_read(self):
        with mock.patch("yfinance.download",
                        return_value=self._frame(["ABBV"], multi=True)):
            self.assertEqual(E.live_spots(["ABBV"]), {"ABBV": 10.0})

    def test_a_flat_frame_is_also_read(self):
        with mock.patch("yfinance.download",
                        return_value=self._frame(["ABBV"], multi=False)):
            self.assertEqual(E.live_spots(["ABBV"]), {"ABBV": 10.0})

    def test_several_tickers_still_work(self):
        with mock.patch("yfinance.download",
                        return_value=self._frame(["A", "B"], multi=True)):
            self.assertEqual(E.live_spots(["A", "B"]), {"A": 10.0, "B": 10.0})

    def test_a_symbol_the_frame_lacks_is_absent_not_guessed(self):
        with mock.patch("yfinance.download",
                        return_value=self._frame(["A"], multi=True)):
            got = E.live_spots(["A", "MISSING"])
        self.assertIn("A", got)
        self.assertNotIn("MISSING", got)


class WatchlistScan(unittest.TestCase):
    """Running the CSP scan against a saved watchlist."""

    def _run(self, form):
        from stockanalysis.webapp import api
        captured = {}

        def fake_start(kind, label, fn):
            captured["label"] = label
            with mock.patch.object(api, "job_csp_scan") as job:
                job.return_value = "stub"
                fn(object())
                captured["call"] = job.call_args
            return ""

        with mock.patch.object(api.jobstore, "start", side_effect=fake_start), \
             mock.patch("stockanalysis.reporting.research.load_watchlists",
                        return_value={"AI": [f"T{i}" for i in range(250)],
                                      "small": ["AAA", "BBB"]}):
            err = api.dispatch_run("csp_scan", form)
        import inspect
        names = list(inspect.signature(api.job_csp_scan).parameters)
        bound = dict(zip(names, captured["call"][0]))
        bound.update(captured["call"][1])
        return err, captured["label"], bound

    def _form(self, **kw):
        form = {"action": ["csp_scan"], "tickers": [""], "min_dte": ["20"],
                "max_dte": ["45"], "limit": ["25"]}
        form.update({k: [v] for k, v in kw.items()})
        return form

    def test_a_picked_list_becomes_the_scan_set(self):
        err, label, bound = self._run(self._form(list="small"))
        self.assertEqual(err, "")
        self.assertEqual(bound["tickers"], ["AAA", "BBB"])
        self.assertIn("small", label)

    def test_the_whole_list_gets_chain_work_not_the_first_limit(self):
        """`limit` caps survivors and defaults to 25; a named set is a
        question about all of them."""
        _e, _l, bound = self._run(self._form(list="small"))
        self.assertGreaterEqual(bound["limit"], 2)

    def test_a_long_list_is_capped_and_the_label_says_so(self):
        from stockanalysis.webapp import api
        _e, label, bound = self._run(self._form(list="AI"))
        self.assertEqual(len(bound["tickers"]), api.MAX_LIST_SCAN)
        self.assertIn("first", label)
        self.assertIn("250", label)

    def test_a_typed_ticker_wins_over_the_picker(self):
        """Naming a symbol is the more specific instruction."""
        _e, _l, bound = self._run(self._form(list="AI", tickers="NVDA"))
        self.assertEqual(bound["tickers"], ["NVDA"])

    def test_no_list_and_no_tickers_is_still_a_full_scan(self):
        _e, label, bound = self._run(self._form())
        self.assertEqual(bound["tickers"], [])
        self.assertIn("eligible", label)


class ReferenceStrikeIsChosenByDelta(unittest.TestCase):
    """The reference strike was whichever one paid most, which is always
    the closest to the money.

    Measured over the live library that meant median delta 0.42, 104 of 112
    rows above 0.35, many barely 1% out of the money — near-coin-flip
    assignments. Worse, no two rows were comparable: each headline yield
    came from a different moneyness, so "121% annualised" and "30%
    annualised" were answering different questions.

    Anchoring every row to one delta makes the column a like-for-like
    reading, and the yield difference becomes a fact about the name.
    """

    def _c(self, delta, annualised=10.0, strike=None):
        return {"delta": -delta, "annualised": annualised,
                "strike": strike if strike is not None else round(delta * 100)}

    def test_yield_does_not_decide(self):
        pool = [self._c(0.45, 200.0), self._c(0.30, 20.0)]
        self.assertAlmostEqual(abs(E._pick_by_delta(pool)["delta"]), 0.30)

    def test_the_nearest_to_target_wins_inside_the_band(self):
        pool = [self._c(0.26), self._c(0.32), self._c(0.34)]
        self.assertAlmostEqual(abs(E._pick_by_delta(pool)["delta"]), 0.32)

    def test_a_tie_breaks_to_the_safer_side(self):
        """Between two equidistant from 0.30, the one further from
        assignment is the honest representative."""
        pool = [self._c(0.25), self._c(0.35)]
        self.assertAlmostEqual(abs(E._pick_by_delta(pool)["delta"]), 0.25)

    def test_the_band_is_preferred_over_a_closer_outlier(self):
        """A 0.27 inside the band beats nothing outside it."""
        pool = [self._c(0.27), self._c(0.46)]
        self.assertAlmostEqual(abs(E._pick_by_delta(pool)["delta"]), 0.27)

    def test_it_widens_once_when_the_band_is_empty(self):
        """$50 strike spacing can step straight past 0.25-0.35."""
        pool = [self._c(0.18), self._c(0.44)]
        got = abs(E._pick_by_delta(pool)["delta"])
        self.assertIn(round(got, 2), (0.18, 0.44))

    def test_nothing_usable_returns_none_rather_than_a_wrong_trade(self):
        self.assertIsNone(E._pick_by_delta([self._c(0.62), self._c(0.02)]))
        self.assertIsNone(E._pick_by_delta([]))
        self.assertIsNone(E._pick_by_delta(None))

    def test_a_contract_with_no_delta_is_skipped_not_crashed_on(self):
        pool = [{"annualised": 500.0}, self._c(0.30)]
        self.assertAlmostEqual(abs(E._pick_by_delta(pool)["delta"]), 0.30)

    def test_the_target_is_the_conventional_csp_delta(self):
        self.assertEqual(E.REFERENCE_TARGET_DELTA, 0.30)
        lo, hi = E.REFERENCE_DELTA_BAND
        self.assertLess(lo, E.REFERENCE_TARGET_DELTA)
        self.assertGreater(hi, E.REFERENCE_TARGET_DELTA)
        # 0.27 and 0.32 are the same trade — both must be inside.
        self.assertTrue(lo <= 0.27 <= hi and lo <= 0.32 <= hi)

    def test_the_etf_section_anchors_to_the_same_delta(self):
        """A fund and a stock in the two tables should be showing the same
        trade, not two that share a column heading."""
        from stockanalysis.core.csp import etf as ETF
        self.assertEqual(ETF.TARGET_DELTA, E.REFERENCE_TARGET_DELTA)
        self.assertTrue(ETF.DELTA_LO <= ETF.TARGET_DELTA <= ETF.DELTA_HI)
