"""
Tests for core.premarket_brief — text/HTML rendering and the
save/load round-trip (including the datetime-in-econ_events JSON gotcha).
No network calls: generate_premarket_brief's own sub-fetches aren't
exercised here, only the pure rendering/persistence functions.
Run with: python -m unittest tests.test_premarket_brief
"""
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import premarket_brief as pb

SYNTHETIC_BRIEF = {
    "generated_at": "2026-07-18T07:00:00",
    "vix": {"level": 14.2, "change_pct": -2.1, "label": "Calm"},
    "futures": [{"label": "S&P fut", "price": 5600.0, "chg_pct": 0.3}],
    "macro": [{"label": "Gold", "price": 2400.0, "chg_pct": 0.5},
             {"label": "Bitcoin", "price": 65000.0, "chg_pct": -1.2}],
    "yield_10y": {"level_pct": 4.25, "change_bps": -3},
    "dollar_index": {"level": 104.1, "change_pct": 0.1},
    "fed": {},
    "econ_events": [{"title": "CPI", "impact": "High",
                    "when": datetime(2026, 7, 18, 8, 30), "forecast": "3.1%", "previous": "3.0%"}],
    "spy": {}, "qqq": {},
    "sectors_trending": [{"label": "Semis", "chg_pct": 1.8}],
    "sectors_lagging": [{"label": "Utilities", "chg_pct": -0.9}],
    "gainers": [{"ticker": "NVDA", "chg_pct": 3.1}],
    "losers": [{"ticker": "INTC", "chg_pct": -2.4}],
    "unusual_movers": [{"ticker": "NVDA", "chg_pct": 3.1}],
    "earnings_today": [{"ticker": "NFLX", "avg_abs_move_pct": 8.2}],
    "near_breakout": [{"ticker": "AMD", "headline": "AMD broke out above resistance"}],
}


class TestRenderText(unittest.TestCase):
    def test_includes_key_facts(self):
        text = pb.render_text(SYNTHETIC_BRIEF)
        for expected in ("VIX 14.20", "NVDA +3.1%", "INTC -2.4%", "NFLX", "AMD"):
            self.assertIn(expected, text)

    def test_handles_all_empty_sections_without_raising(self):
        empty = {"generated_at": "2026-07-18T07:00:00", "vix": {}, "futures": [], "macro": [],
                "yield_10y": {}, "dollar_index": {}, "fed": {}, "econ_events": [],
                "spy": {}, "qqq": {}, "sectors_trending": [], "sectors_lagging": [],
                "gainers": [], "losers": [], "unusual_movers": [], "earnings_today": [],
                "near_breakout": []}
        text = pb.render_text(empty)
        self.assertIn("Not financial advice", text)


class TestRenderHtml(unittest.TestCase):
    def test_includes_key_facts(self):
        html = pb.render_html(SYNTHETIC_BRIEF)
        for expected in ("NVDA", "NFLX", "AMD", "Semis"):
            self.assertIn(expected, html)

    def test_handles_all_empty_sections_without_raising(self):
        empty = {"generated_at": "2026-07-18T07:00:00", "vix": {}, "futures": [], "macro": [],
                "yield_10y": {}, "dollar_index": {}, "fed": {}, "econ_events": [],
                "spy": {}, "qqq": {}, "sectors_trending": [], "sectors_lagging": [],
                "gainers": [], "losers": [], "unusual_movers": [], "earnings_today": [],
                "near_breakout": []}
        html = pb.render_html(empty)  # must not raise
        self.assertIn("Pre-Market Brief", html)


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self._orig_path = pb.LATEST_BRIEF_PATH
        pb.LATEST_BRIEF_PATH = Path(tempfile.mkdtemp()) / "premarket_brief.json"

    def tearDown(self):
        pb.LATEST_BRIEF_PATH = self._orig_path

    def test_no_file_returns_none(self):
        self.assertIsNone(pb.load_latest_brief())

    def test_datetime_in_econ_events_does_not_break_json_round_trip(self):
        """Regression test: fetch_economic_events() returns real datetime
        objects in each event's "when" field, not preformatted strings —
        json.dumps must use default=str or this raises TypeError."""
        import json
        pb.LATEST_BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
        pb.LATEST_BRIEF_PATH.write_text(json.dumps(SYNTHETIC_BRIEF, default=str))
        loaded = pb.load_latest_brief()
        self.assertEqual(loaded["econ_events"][0]["title"], "CPI")
        self.assertIn("2026-07-18", loaded["econ_events"][0]["when"])

    def test_corrupt_file_returns_none_not_raise(self):
        pb.LATEST_BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
        pb.LATEST_BRIEF_PATH.write_text("{not valid json")
        self.assertIsNone(pb.load_latest_brief())


class TestDefaultWatchlistDelegation(unittest.TestCase):
    def test_delegates_to_watchlist_alerts_default(self):
        """The brief must use the same scoped-down default as the alert
        monitor (just the "watchlist" list), not every saved list."""
        from stockanalysis.core import watchlist_alerts as wa
        orig = wa.default_alert_tickers
        wa.default_alert_tickers = lambda: ["NVDA", "AMD"]
        try:
            self.assertEqual(pb._default_watchlist_tickers(), ["NVDA", "AMD"])
        finally:
            wa.default_alert_tickers = orig


if __name__ == "__main__":
    unittest.main()
