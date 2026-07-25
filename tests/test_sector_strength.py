"""
Tests for the market-scanner funnel: core.sector_strength (pure scoring) and
scanners.sector_filter's cache/fail-open orchestration. No network — ETF
returns are synthetic dicts and yfinance paths are monkeypatched out.
Run with: python -m unittest tests.test_sector_strength
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import sector_strength as ss
from stockanalysis.scanners import sector_filter as sf


def _returns(**overrides):
    """SPY flat, every sector flat, with per-ETF overrides {etf: (r1m, r3m)}."""
    out = {"SPY": {"r1m": 2.0, "r3m": 5.0}}
    for etf in ss.SECTOR_ETF.values():
        out[etf] = {"r1m": 2.0, "r3m": 5.0}
    for etf, (r1m, r3m) in overrides.items():
        out[etf] = {"r1m": r1m, "r3m": r3m}
    return out


class TestRankSectors(unittest.TestCase):
    def test_outperformer_ranks_first_laggard_last(self):
        ranked = ss.rank_sectors(_returns(XLK=(8.0, 15.0), XLU=(-1.0, 0.0)))
        self.assertEqual(ranked[0]["sector"], "Technology")
        self.assertEqual(ranked[-1]["sector"], "Utilities")
        self.assertGreater(ranked[0]["score"], 0)
        self.assertLess(ranked[-1]["score"], 0)

    def test_score_is_relative_to_spy_not_absolute(self):
        # everything up 10% but SPY up more → all scores negative
        rets = {"SPY": {"r1m": 12.0, "r3m": 20.0}}
        for etf in ss.SECTOR_ETF.values():
            rets[etf] = {"r1m": 10.0, "r3m": 10.0}
        self.assertTrue(all(r["score"] < 0 for r in ss.rank_sectors(rets)))

    def test_one_month_outweighs_three_month(self):
        # XLE: strong 1M weak 3M; XLF: weak 1M strong 3M, same magnitudes
        ranked = ss.rank_sectors(_returns(XLE=(7.0, 5.0), XLF=(2.0, 10.0)))
        by = {r["sector"]: r["score"] for r in ranked}
        self.assertGreater(by["Energy"], by["Financial Services"])

    def test_missing_sector_omitted_missing_spy_returns_empty(self):
        rets = _returns()
        del rets["XLV"]
        self.assertNotIn("Healthcare", [r["sector"] for r in ss.rank_sectors(rets)])
        del rets["SPY"]
        self.assertEqual(ss.rank_sectors(rets), [])


class TestSelectSectors(unittest.TestCase):
    def setUp(self):
        self.ranked = ss.rank_sectors(_returns(
            XLK=(8, 15), SMH=(9, 9), XLC=(6, 10), XLI=(5, 8), XLE=(4, 7),
            XLF=(3, 6), XLU=(-1, 0)))

    def test_regime_sets_selectivity(self):
        self.assertEqual(len(ss.select_sectors(self.ranked, "Bullish")), 5)
        self.assertEqual(len(ss.select_sectors(self.ranked, "Neutral")), 4)
        self.assertEqual(len(ss.select_sectors(self.ranked, "Defensive")), 3)

    def test_unknown_regime_falls_back_to_neutral_count(self):
        self.assertEqual(len(ss.select_sectors(self.ranked, "???")), 4)

    def test_keeps_the_strongest(self):
        kept = ss.select_sectors(self.ranked, "Defensive")
        self.assertIn("Technology", kept)
        self.assertNotIn("Utilities", kept)


class TestFilterUniverse(unittest.TestCase):
    def test_split_and_fail_open_for_unknown(self):
        sector_of = {"NVDA": "Technology", "XOM": "Energy", "NEWIPO": None}
        res = ss.filter_universe(["NVDA", "XOM", "NEWIPO"], sector_of,
                                 ["Technology"])
        self.assertEqual(res["kept"], ["NVDA", "NEWIPO"])   # unknown kept
        self.assertEqual(res["dropped"], ["XOM"])
        self.assertEqual(res["unknown"], ["NEWIPO"])

    def test_ticker_missing_from_map_is_kept(self):
        res = ss.filter_universe(["MYSTERY"], {}, ["Technology"])
        self.assertEqual(res["kept"], ["MYSTERY"])


class TestSectorFilterOrchestration(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self._orig_cache = sf.SECTOR_CACHE_PATH
        self._orig_output = sf.OUTPUT_DIR
        self._orig_fetch = sf.fetch_sector_returns
        sf.SECTOR_CACHE_PATH = tmp / "ticker_sectors.json"
        sf.OUTPUT_DIR = tmp / "output"
        sf.OUTPUT_DIR.mkdir()

    def tearDown(self):
        sf.SECTOR_CACHE_PATH = self._orig_cache
        sf.OUTPUT_DIR = self._orig_output
        sf.fetch_sector_returns = self._orig_fetch

    def test_funnel_fails_open_when_etf_data_unavailable(self):
        sf.fetch_sector_returns = lambda: None
        out = sf.apply_market_funnel(["NVDA", "XOM"])
        self.assertFalse(out["applied"])
        self.assertEqual(out["tickers"], ["NVDA", "XOM"])

    def test_sectors_harvested_from_scan_csv_and_cached(self):
        (sf.OUTPUT_DIR / "stock_scan_20260717_1600.csv").write_text(
            "Ticker,Sector\nNVDA,Technology\nXOM,Energy\n")
        result = sf.get_ticker_sectors(["NVDA", "XOM"])
        self.assertEqual(result, {"NVDA": "Technology", "XOM": "Energy"})
        # persisted: second call must not need the CSV
        (sf.OUTPUT_DIR / "stock_scan_20260717_1600.csv").unlink()
        cached = json.loads(sf.SECTOR_CACHE_PATH.read_text())
        self.assertEqual(cached["NVDA"], "Technology")
        self.assertEqual(sf.get_ticker_sectors(["NVDA"]), {"NVDA": "Technology"})


if __name__ == "__main__":
    unittest.main()
