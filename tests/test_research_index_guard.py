"""
Regression test: a failed-fetch "husk" row (yfinance throttled — no price,
categorized 'Avoid' via the entry gate) must NOT overwrite a good research
index entry or page. See reporting.research.generate_research_pages.
Run with: python -m unittest tests.test_research_index_guard
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.reporting import research


class TestFailedFetchGuard(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        good = {"AAPL": {"ticker": "AAPL", "price": 333.74, "category": "Momentum",
                         "updated_at": "2026-07-18 16:46:05", "raw": {}}}
        (self.out / research.INDEX_FILENAME).write_text(json.dumps(good))

    def _husk(self):
        # what get_metrics returns when Yahoo throttles every sub-fetch:
        # ticker present, price/cap None, gate fails, categorized Avoid
        return {"Ticker": "AAPL", "Current Price": None, "MarketCap": None,
                "Category": "Avoid",
                "Cat_Reason": "Entry gate failed: MarketCap<1B, Price<$5",
                "Conv_Overall": 5}

    def test_husk_row_does_not_overwrite_good_entry(self):
        written = research.generate_research_pages([self._husk()], self.out,
                                                   charts=False, fetch_news=False)
        self.assertEqual(written, set())    # no page written
        idx = json.loads((self.out / research.INDEX_FILENAME).read_text())
        self.assertEqual(idx["AAPL"]["price"], 333.74)          # entry intact
        self.assertEqual(idx["AAPL"]["category"], "Momentum")
        self.assertFalse((self.out / research.RESEARCH_DIRNAME / "AAPL.html").exists())

    def test_error_category_row_still_skipped(self):
        row = {"Ticker": "AAPL", "Category": "Error", "Current Price": 333.74}
        written = research.generate_research_pages([row], self.out,
                                                   charts=False, fetch_news=False)
        self.assertEqual(written, set())


class TestIndexFieldMerge(unittest.TestCase):
    """A lighter row must not reset fields it never carried — entries were
    previously replaced wholesale, so a watchlist scan blanked ~29 curated
    fields on a ticker that had good values seconds earlier."""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.rich = {
            "ticker": "AAPL", "price": 333.74, "category": "Momentum",
            "market_cap": 4_999_575_764_992, "put_score": 6, "rs_rank": 73,
            "week52_high": 343.67, "peg_ratio": 2.73, "r1": 350.0,
            "news_updated_at": "2026-07-28 09:00:00",
            "updated_at": "2026-07-28 16:46:05", "raw": {"Ticker": "AAPL"},
        }
        (self.out / research.INDEX_FILENAME).write_text(
            json.dumps({"AAPL": self.rich}))

    def _entry(self):
        return json.loads(
            (self.out / research.INDEX_FILENAME).read_text())["AAPL"]

    def _write(self, row):
        research._update_research_index(self.out, [row], {"AAPL"})

    def test_absent_keys_are_preserved(self):
        self._write({"Ticker": "AAPL", "Current Price": 341.0})
        e = self._entry()
        self.assertEqual(e["price"], 341.0)               # carried -> updated
        for k in ("market_cap", "put_score", "rs_rank",
                  "week52_high", "peg_ratio"):
            self.assertEqual(e[k], self.rich[k], f"{k} was reset")

    def test_explicit_none_overwrites(self):
        # the full pipeline emits R1=None when no resistance was found; that
        # must land, or stale levels pin forever
        self._write({"Ticker": "AAPL", "Current Price": 341.0, "R1": None})
        self.assertIsNone(self._entry()["r1"])

    def test_falsy_values_are_written(self):
        self._write({"Ticker": "AAPL", "Current Price": 341.0,
                     "Put_Score": 0, "CANSLIM_Pass": False})
        e = self._entry()
        self.assertEqual(e["put_score"], 0)
        self.assertIs(e["canslim_pass"], False)

    def test_other_jobs_keys_survive(self):
        self._write({"Ticker": "AAPL", "Current Price": 341.0})
        self.assertEqual(self._entry()["news_updated_at"],
                         "2026-07-28 09:00:00")

    def test_raw_merges_rather_than_replaces(self):
        research._update_research_index(
            self.out, [{"Ticker": "AAPL", "Current Price": 341.0,
                        "RSI_14": 55.0}], {"AAPL"})
        raw = self._entry()["raw"]
        self.assertEqual(raw["RSI_14"], 55.0)      # new key added
        self.assertEqual(raw["Ticker"], "AAPL")    # prior key kept


if __name__ == "__main__":
    unittest.main()
