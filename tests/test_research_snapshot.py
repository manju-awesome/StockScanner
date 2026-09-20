"""
Tests for core.research_snapshot — the durable copy of research fields that
keeps the Screener working when research_index.json is overwritten by a
process running older code (the 2026-08-05 incident: 494 of 558 entries lost
their "raw" block in one write).

The invariant every test here defends: the snapshot never shrinks, and a
writer with less data than last time cannot take fields away.
Run with: python -m unittest tests.test_research_snapshot
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import research_snapshot as RS


class TestApply(unittest.TestCase):
    def test_merges_field_by_field(self):
        snap = {}
        RS.apply(snap, "AAPL", {"Ticker": "AAPL", "Price": 100, "RSI_14": 55})
        RS.apply(snap, "AAPL", {"Ticker": "AAPL", "Price": 105})
        self.assertEqual(snap["AAPL"]["Price"], 105)      # changed value wins
        self.assertEqual(snap["AAPL"]["RSI_14"], 55)      # untouched survives

    def test_absent_field_does_not_delete(self):
        # The whole point: a lighter row contributes fewer fields, it does
        # not truncate the ticker.
        snap = {}
        RS.apply(snap, "AAPL", {"Price": 100, "GrossMargin%": 45.0})
        RS.apply(snap, "AAPL", {"Price": 101})
        self.assertEqual(snap["AAPL"]["GrossMargin%"], 45.0)

    def test_none_and_blank_never_overwrite(self):
        snap = {}
        RS.apply(snap, "AAPL", {"GrossMargin%": 45.0, "Category": "Momentum"})
        RS.apply(snap, "AAPL", {"GrossMargin%": None, "Category": ""})
        self.assertEqual(snap["AAPL"]["GrossMargin%"], 45.0)
        self.assertEqual(snap["AAPL"]["Category"], "Momentum")

    def test_nan_never_overwrites(self):
        snap = {}
        RS.apply(snap, "AAPL", {"RSI_14": 55.0})
        RS.apply(snap, "AAPL", {"RSI_14": float("nan")})
        self.assertEqual(snap["AAPL"]["RSI_14"], 55.0)

    def test_csv_placeholder_strings_are_treated_as_missing(self):
        snap = {}
        RS.apply(snap, "AAPL", {"Forward_PE": 30.0})
        RS.apply(snap, "AAPL", {"Forward_PE": "N/A"})
        self.assertEqual(snap["AAPL"]["Forward_PE"], 30.0)

    def test_a_totally_empty_row_is_a_no_op(self):
        snap = {"AAPL": {"Price": 100}}
        self.assertFalse(RS.apply(snap, "AAPL", {}))
        self.assertFalse(RS.apply(snap, "AAPL", {"Price": None}))
        self.assertEqual(snap["AAPL"]["Price"], 100)

    def test_records_when_it_last_saw_the_ticker(self):
        snap = {}
        RS.apply(snap, "AAPL", {"Price": 100}, seen_at="2026-08-05 16:30:00")
        self.assertEqual(snap["AAPL"][RS._SEEN_AT], "2026-08-05 16:30:00")

    def test_bookkeeping_keys_are_not_treated_as_data(self):
        snap = {}
        RS.apply(snap, "AAPL", {"Price": 100, "_internal": "x"})
        self.assertNotIn("_internal", snap["AAPL"])

    def test_no_ticker_is_ignored(self):
        snap = {}
        self.assertFalse(RS.apply(snap, "", {"Price": 100}))
        self.assertEqual(snap, {})


class TestMerged(unittest.TestCase):
    def test_snapshot_fills_a_wiped_entry(self):
        # Exactly the incident: the index entry kept its curated fields but
        # lost "raw" entirely.
        index = {"AAPL": {"ticker": "AAPL", "price": 311.0, "grade": "B"}}
        snap = {"AAPL": {"Pct_vs_8EMA": 1.5, "GrossMargin%": 45.0,
                         RS._SEEN_AT: "2026-08-05 16:30:00"}}
        out = RS.merged(index, snap)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["raw"]["Pct_vs_8EMA"], 1.5)
        self.assertEqual(out[0]["price"], 311.0)       # index field kept
        self.assertTrue(out[0]["recovered_from_snapshot"])
        self.assertEqual(out[0]["snapshot_seen_at"], "2026-08-05 16:30:00")

    def test_live_index_values_win_over_the_snapshot(self):
        index = {"AAPL": {"ticker": "AAPL", "raw": {"Price": 999.0}}}
        snap = {"AAPL": {"Price": 100.0, "RSI_14": 55.0}}
        out = RS.merged(index, snap)
        self.assertEqual(out[0]["raw"]["Price"], 999.0)   # fresh wins
        self.assertEqual(out[0]["raw"]["RSI_14"], 55.0)   # gap filled

    def test_healthy_rows_are_not_labelled_recovered(self):
        index = {"AAPL": {"ticker": "AAPL", "raw": {"Price": 999.0}}}
        snap = {"AAPL": {"RSI_14": 55.0}}
        self.assertNotIn("recovered_from_snapshot", RS.merged(index, snap)[0])

    def test_ticker_only_in_snapshot_still_appears(self):
        out = RS.merged({}, {"ZZZ": {"Price": 10.0}})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ticker"], "ZZZ")

    def test_bookkeeping_keys_never_leak_into_raw(self):
        snap = {"AAPL": {"Price": 100.0, RS._SEEN_AT: "x", RS._SOURCE: "y"}}
        raw = RS.merged({}, snap)[0]["raw"]
        self.assertNotIn(RS._SEEN_AT, raw)
        self.assertNotIn(RS._SOURCE, raw)

    def test_empty_inputs_are_safe(self):
        self.assertEqual(RS.merged({}, {}), [])


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_round_trip(self):
        snap = {}
        RS.apply(snap, "AAPL", {"Price": 100})
        RS.save(self.dir, snap)
        self.assertEqual(RS.load(self.dir)["AAPL"]["Price"], 100)

    def test_missing_file_reads_empty(self):
        self.assertEqual(RS.load(self.dir), {})

    def test_corrupt_file_reads_empty_rather_than_raising(self):
        # A safety net that crashes the app when it tears is worse than none.
        RS.snapshot_path(self.dir).write_text("{not json")
        self.assertEqual(RS.load(self.dir), {})

    def test_record_skips_rows_without_a_ticker(self):
        n = RS.record(self.dir, [{"Price": 1}, {"Ticker": "AAPL", "Price": 2}])
        self.assertEqual(n, 1)
        self.assertIn("AAPL", RS.load(self.dir))

    def test_record_survives_an_unwritable_directory(self):
        # Runs at the tail of a scan — it must never fail the scan.
        self.assertEqual(RS.record(Path("/nonexistent/nope"),
                                   [{"Ticker": "AAPL", "Price": 1}]), 0)


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _scan(self, name: str, rows: list[str]) -> None:
        (self.dir / name).write_text("\n".join(rows) + "\n")

    def test_rebuilds_from_scan_csvs(self):
        self._scan("stock_scan_20260101_0900.csv",
                   ["Ticker,Current Price,RSI_14", "AAPL,100,55"])
        summary = RS.backfill_from_scans(self.dir)
        self.assertEqual(summary["tickers"], 1)
        self.assertEqual(RS.load(self.dir)["AAPL"]["RSI_14"], 55)

    def test_newest_scan_wins_and_older_ones_only_fill_gaps(self):
        import os, time
        self._scan("stock_scan_20260101_0900.csv",
                   ["Ticker,Current Price,GrossMargin%", "AAPL,100,45"])
        time.sleep(0.01)
        self._scan("stock_scan_20260102_0900.csv",
                   ["Ticker,Current Price", "AAPL,200"])
        now = time.time()
        os.utime(self.dir / "stock_scan_20260101_0900.csv", (now - 100, now - 100))
        os.utime(self.dir / "stock_scan_20260102_0900.csv", (now, now))
        RS.backfill_from_scans(self.dir)
        entry = RS.load(self.dir)["AAPL"]
        self.assertEqual(entry["Current Price"], 200)     # newest wins
        self.assertEqual(entry["GrossMargin%"], 45)       # older fills gap

    def test_csv_strings_are_coerced_to_real_types(self):
        # CSV hands everything back as text, but the scoring functions that
        # read these rows do numeric and truth tests on them.
        self._scan("stock_scan_20260101_0900.csv",
                   ["Ticker,Current Price,Above_200MA,FCF_Positive,Category,EarningsDate",
                    "AAPL,311.5,True,False,Momentum,2026-10-29"])
        RS.backfill_from_scans(self.dir)
        e = RS.load(self.dir)["AAPL"]
        self.assertEqual(e["Current Price"], 311.5)
        self.assertIs(e["Above_200MA"], True)
        # the one that bites silently: "False" is a truthy string
        self.assertIs(e["FCF_Positive"], False)
        self.assertEqual(e["Category"], "Momentum")
        self.assertEqual(e["EarningsDate"], "2026-10-29")   # not a number

    def test_text_columns_that_look_numeric_stay_text(self):
        self._scan("stock_scan_20260101_0900.csv",
                   ["Ticker,52W_High_Date", "AAPL,20260805"])
        RS.backfill_from_scans(self.dir)
        self.assertEqual(RS.load(self.dir)["AAPL"]["52W_High_Date"], "20260805")

    def test_per_strategy_splits_are_skipped(self):
        self._scan("stock_scan_20260101_0900_swing.csv",
                   ["Ticker,Current Price", "ZZZ,1"])
        self.assertEqual(RS.backfill_from_scans(self.dir)["files_read"], 0)

    def test_unreadable_csv_does_not_abort_the_rest(self):
        (self.dir / "stock_scan_20260101_0900.csv").write_bytes(b"\xff\xfe bad")
        self._scan("stock_scan_20260102_0900.csv",
                   ["Ticker,Current Price", "AAPL,100"])
        self.assertEqual(RS.backfill_from_scans(self.dir)["tickers"], 1)

    def test_no_scans_is_a_no_op(self):
        self.assertEqual(RS.backfill_from_scans(self.dir)["tickers"], 0)


class TestClobberResistance(unittest.TestCase):
    """End-to-end: the incident, replayed."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_screener_keeps_its_fields_after_an_old_writer_wipes_the_index(self):
        good_raw = {"Ticker": "AAPL", "Current Price": 311.0, "8EMA": 308.0,
                    "Pct_vs_8EMA": 0.97, "GrossMargin%": 45.0,
                    "Category": "Momentum"}
        # 1. a healthy scan records the snapshot
        RS.record(self.dir, [good_raw], source="scan")

        # 2. a process running old code rewrites the index with a schema
        #    that has no "raw" key at all
        wiped_index = {"AAPL": {"ticker": "AAPL", "price": 311.0,
                                "grade": "B", "updated_at": "2026-08-05 16:49:01"}}

        # 3. the screener still sees every field it filters on
        entries = RS.merged(wiped_index, RS.load(self.dir))
        from stockanalysis.core.screener import build_universe, Group, Condition, screen
        rows = build_universe(entries)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["abs_vs_8ema"], 0.97, places=2)
        self.assertTrue(rows[0]["recovered"])

        res = screen(rows, Group("AND", [Condition("abs_vs_8ema", "within", 1)]))
        self.assertEqual(res.summary["count"], 1)
        self.assertEqual(res.summary["recovered"], 1)

    def test_index_and_snapshot_disagreeing_prefers_the_index(self):
        RS.record(self.dir, [{"Ticker": "AAPL", "Current Price": 100.0}])
        index = {"AAPL": {"ticker": "AAPL",
                          "raw": {"Ticker": "AAPL", "Current Price": 350.0}}}
        from stockanalysis.core.screener import build_universe
        rows = build_universe(RS.merged(index, RS.load(self.dir)))
        self.assertEqual(rows[0]["price"], 350.0)
        self.assertFalse(rows[0]["recovered"])


if __name__ == "__main__":
    unittest.main()


class TestHasQuote(unittest.TestCase):
    """A failed fetch must be distinguishable from a bad company: the entry
    gate stamps husk rows Avoid/1-star, which is a verdict made of absent
    data."""

    def test_price_on_the_entry(self):
        self.assertTrue(RS.has_quote({"ticker": "AAPL", "price": 311.0}))

    def test_price_only_in_raw(self):
        self.assertTrue(RS.has_quote({"raw": {"Current Price": 311.0}}))

    def test_husk_row_has_no_quote(self):
        # exactly the shape the 18 unresolvable symbols arrive in
        self.assertFalse(RS.has_quote({
            "ticker": "ANSS", "sector": "N/A", "category": "Avoid",
            "grade": "X", "conv_action": "AVOID", "conv_stars": 1,
            "price": None, "raw": {}}))

    def test_empty_and_missing(self):
        self.assertFalse(RS.has_quote({}))
        self.assertFalse(RS.has_quote(None))
        self.assertFalse(RS.has_quote({"price": ""}))
