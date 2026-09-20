"""
The Dashboard's Scan & Analyze card: scope resolution (api.analysis) and the
panel it renders (longterm_view.analysis_panel).
Run with: python -m unittest tests.test_scan_analyze
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.longterm import engine as E
from stockanalysis.webapp import api, longterm_view as LV
from stockanalysis.webapp import jobstore

FAKE_LISTS = {"daytrade": ["NVDA", "AMD"], "AI": ["NVDA", "MSFT", "GONE"]}

# A plausible scan row, run through the real engine to make the fixtures. A
# hand-written result dict would be a second, silent claim about the engine's
# output shape, and would keep rendering happily after the engine stopped
# emitting a field the table reads.
_RAW = {
    "Ticker": "TEST", "LongName": "Test Corp", "Sector": "Technology",
    "Industry": "Software - Infrastructure", "Current Price": 100.0,
    "Revenue": 25.0, "EPS_Growth%": 30.0, "ReturnOnEquity%": 30.0,
    "OperatingMargin%": 30.0, "GrossMargin%": 70.0, "FCF_Margin%": 25.0,
    "FCF_Positive": True, "DebtToEquity": 20.0, "CurrentRatio": 3.0,
    "TotalCash": 5e10, "TotalDebt": 1e10, "Inst_Own%": 65.0,
    "Inst_Own_Chg": 1.5, "FreeCashFlow": 2.0e10, "NetIncome": 2.0e10,
    "SharesOutstanding": 1.0e9, "Beta": 1.0, "MarketCap": 1.0e11,
    "FCF_CAGR%": 20.0, "FCF_Years": 4, "FCF_Positive_Years": 4,
    "Revenue_CAGR%": 20.0, "OperatingMargin_Trend_pp": 2.0,
    "8EMA": 99.0, "21EMA": 98.5, "50MA": 92.0, "200MA": 80.0,
    "Above_200MA": True, "Price_vs_50MA%": 8.7, "Price_vs_200MA%": 25.0,
    "Pct_vs_8EMA": 1.0, "MA50_Slope%": 2.0, "MA200_Slope%": 1.5,
    "ATR_Pct": 2.0, "RSI_14": 55.0, "Dist_52W_High%": -8.0,
    "52W High": 108.0, "Prior_Breakout_Level": 99.2,
    "Vol_vs_20D": 0.7, "Pullback_Vol_Ratio": 0.7, "VolumeDryingUp": True,
    "Distribution_Days_25d": 1, "Reversal_Candle": "bullish engulfing",
    "RVOL": 1.3, "RS_Rank": 85.0, "RS": 8.0, "Days_To_Earnings": 40,
}


def _row(ticker):
    return E.evaluate(dict(_RAW, Ticker=ticker, LongName=f"{ticker} Inc"),
                      regime="SELECTIVE")


class _Patched(unittest.TestCase):
    """api.analysis() reads the whole library through api.longterm(); these
    tests replace that with a fixed three-name universe so the assertions are
    about scope resolution, not about whatever the last real scan produced."""

    UNIVERSE = ("NVDA", "AMD", "MSFT")

    def setUp(self):
        self._longterm, self._lists = api.longterm, api.longterm_lists
        api.longterm = lambda override=None: {
            "rows": [_row(t) for t in self.UNIVERSE], "counts": {},
            "regime": "SELECTIVE", "regime_note": "test",
            "risk_free_note": "test", "coverage": {},
        }
        api.longterm_lists = lambda: {k: list(v) for k, v in FAKE_LISTS.items()}

    def tearDown(self):
        api.longterm, api.longterm_lists = self._longterm, self._lists


class TestScopeResolution(_Patched):
    def test_single_ticker(self):
        out = api.analysis("ticker", "nvda")
        self.assertEqual([r["ticker"] for r in out["rows"]], ["NVDA"])
        self.assertEqual(out["missing"], [])
        self.assertIn("NVDA", out["label"])

    def test_several_tickers_keep_the_order_they_were_typed(self):
        # Not engine ranking: someone comparing names they typed expects to
        # read them back the way they wrote them.
        out = api.analysis("ticker", "msft, nvda")
        self.assertEqual([r["ticker"] for r in out["rows"]], ["MSFT", "NVDA"])

    def test_unscanned_ticker_is_named_not_dropped(self):
        # A ticker missing from the table is indistinguishable from one that
        # scored badly unless it is called out.
        out = api.analysis("ticker", "NVDA ZZZZ")
        self.assertEqual([r["ticker"] for r in out["rows"]], ["NVDA"])
        self.assertEqual(out["missing"], ["ZZZZ"])

    def test_daytrade_scope_needs_no_value(self):
        out = api.analysis("daytrade", "")
        self.assertEqual([r["ticker"] for r in out["rows"]], ["NVDA", "AMD"])
        self.assertEqual(out["label"], "Day trade list")

    def test_watchlist_scope_reports_members_with_no_research_page(self):
        out = api.analysis("watchlist", "AI")
        self.assertEqual([r["ticker"] for r in out["rows"]], ["NVDA", "MSFT"])
        self.assertEqual(out["missing"], ["GONE"])

    def test_all_scope_returns_the_whole_universe(self):
        out = api.analysis("all", "")
        self.assertEqual(len(out["rows"]), len(self.UNIVERSE))
        self.assertEqual(out["matched"], len(self.UNIVERSE))

    def test_empty_ticker_is_an_error_not_an_empty_table(self):
        self.assertIn("error", api.analysis("ticker", "   "))

    def test_unknown_scope_and_unknown_list_are_errors(self):
        self.assertIn("error", api.analysis("bogus", ""))
        self.assertIn("error", api.analysis("watchlist", "nope"))

    def test_scope_defaults_to_all(self):
        self.assertEqual(api.analysis("", "")["label"], "All tickers")


class TestDisplayCap(_Patched):
    UNIVERSE = tuple(f"T{i}" for i in range(api.ANALYSIS_LIMIT + 5))

    def test_rows_are_capped_but_matched_reports_the_true_total(self):
        # "showing 60 of 552" is honest; "60 of 552 scored" would claim the
        # other 492 went unscored, which is not what happened.
        out = api.analysis("all", "")
        self.assertEqual(len(out["rows"]), api.ANALYSIS_LIMIT)
        self.assertEqual(out["matched"], len(self.UNIVERSE))
        self.assertIn("Capped at the first", LV.analysis_panel(out))

    def test_no_cap_note_when_everything_fits(self):
        out = api.analysis("ticker", "T1 T2")
        self.assertNotIn("Capped at the first", LV.analysis_panel(out))


class TestPanel(_Patched):
    def test_error_renders_as_a_message_not_a_table(self):
        html = LV.analysis_panel({"error": "no such list: nope"})
        self.assertIn("no such list: nope", html)
        self.assertNotIn("<table", html)

    def test_single_result_opens_its_reasoning(self):
        # One result means the reasoning IS the answer — don't make the user
        # click again to reach it.
        html = LV.analysis_panel(api.analysis("ticker", "NVDA"))
        self.assertIn("<details open>", html)

    def test_several_results_stay_collapsed(self):
        html = LV.analysis_panel(api.analysis("daytrade", ""))
        self.assertNotIn("<details open>", html)

    def test_missing_tickers_get_a_visible_note(self):
        html = LV.analysis_panel(api.analysis("watchlist", "AI"))
        self.assertIn("GONE", html)
        self.assertIn("no research page yet", html)

    def test_panel_carries_the_table_the_page_js_binds_to(self):
        html = LV.analysis_panel(api.analysis("daytrade", ""))
        self.assertIn('id="lt-table"', html)


class TestTableIsShared(unittest.TestCase):
    """The Long-Term page and the Dashboard panel must render the same cells
    — a second, simplified table would be the first thing to go stale."""

    def test_every_column_gets_a_header_and_a_cell(self):
        html = LV.analysis_table([_row("NVDA")])
        for key, label, _align, _type in LV._COLUMNS:
            self.assertIn(f'data-col="{key}"', html)
            self.assertIn(LV.esc(label), html)

    def test_empty_row_set_spans_the_real_column_count(self):
        # A hardcoded colspan silently under-spans the moment a column is
        # added, leaving the empty-state message wedged against the left edge.
        html = LV.analysis_table([], empty_msg="nothing here")
        self.assertIn(f'colspan="{len(LV._COLUMNS)}"', html)
        self.assertIn("nothing here", html)

    def test_table_js_is_callable_after_injection(self):
        # The panel is fetched into the Dashboard after load, so the sort and
        # column-drag handlers cannot be bound by a bare IIFE at parse time.
        self.assertIn("function initLtTable()", LV.TABLE_JS)
        self.assertIn("initLtTable();", LV.TABLE_JS)


class TestScanDispatch(unittest.TestCase):
    """The card's other half: each scope posts a /run form that dispatch_run
    resolves to the right set of tickers."""

    def setUp(self):
        self.started = {}
        self._start = jobstore.start
        jobstore.start = lambda kind, label, fn: (
            self.started.update(kind=kind, label=label), "")[1]

    def tearDown(self):
        jobstore.start = self._start

    def _run(self, **fields):
        form = {"action": ["research"], **{k: [v] for k, v in fields.items()}}
        self.assertEqual(api.dispatch_run("research", form), "")
        return self.started["label"]

    def test_ticker_scope(self):
        self.assertIn("NVDA", self._run(tickers="NVDA AMD"))

    def test_watchlist_scope(self):
        self.assertIn("daytrade", self._run(watchlist="daytrade"))

    def test_all_scope_label_does_not_leak_the_sentinel(self):
        # "__all__" is a wire value; printing it into the job tray tells the
        # user nothing about what is being refreshed.
        label = self._run(watchlist=api.ALL_UNIVERSES_SENTINEL)
        self.assertNotIn(api.ALL_UNIVERSES_SENTINEL, label)
        self.assertIn("all watchlists", label)


if __name__ == "__main__":
    unittest.main()
