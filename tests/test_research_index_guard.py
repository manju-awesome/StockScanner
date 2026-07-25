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


if __name__ == "__main__":
    unittest.main()
