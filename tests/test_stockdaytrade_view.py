"""
Tests for webapp/stockdaytrade_view.py — the StockDayTrade page.

The page renders a snapshot it did not write. That file can be hours old
and can have been written by any earlier version of the engine, so the
invariant these defend is:

    the page must render whatever it is handed, and never take the whole
    workstation page down because a field it wanted is missing.

A stored snapshot is a schema this module does not control. That is not a
hypothetical — a legacy snapshot lacking `risk_based_shares` crashed the
page with "unsupported format string passed to NoneType.__format__",
because `:,` on None is a TypeError.

No network: the store is pointed at fixtures on disk.

Run with: python -m unittest tests.test_stockdaytrade_view
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.daytrade import store
from stockanalysis.webapp import stockdaytrade_view as V


def _row(**over):
    """A fully-populated modern row. Tests remove fields from it."""
    row = {
        "ticker": "TEST", "name": "Test Corp", "price": 10.0,
        "profile": "small", "profile_label": "Small cap",
        "weights": {"catalyst": 20, "volatility": 15, "supply": 15, "volume": 15,
                    "setup": 15, "market": 10, "regime": 5, "room": 5},
        "asof": "2026-08-07", "is_live": False,
        "confluence": 82, "coverage": 0.9, "setup_score": 85, "tradeability": 78,
        "entry_score": 88, "entry_grade": "HIGH QUALITY", "chase_score": 1,
        "chase_label": "🟢 fine", "grade": "A", "grade_note": None,
        "decision": "🟢 A LONG", "action": "🔥 ENTER NOW",
        "action_why": "triggered, entry score 88",
        "n_confirmations": 11, "n_checks": 14, "squeeze_credit": False,
        "regime_adjustment": "RISK ON tape supports the long",
        "session": {"vwap": 9.8, "pm_high": 9.5, "prev_high": 9.2,
                    "asof": "2026-08-07"},
        "blocks": {"catalyst": 90, "volatility": 70, "supply": 60, "volume": 80,
                   "setup": 85, "market": 75, "regime": 70, "room": 80},
        "block_points": {"catalyst": 18.0, "volatility": 10.5, "supply": 9.0,
                         "volume": 12.0, "setup": 12.8, "market": 7.5,
                         "regime": 3.5, "room": 4.0},
        "volatility": {"gap_pct": 8.0, "rvol": 4.2, "atr_pct": 6.0,
                       "dollar_volume": 2.5e7, "expected_move_pct": 4.0,
                       "day_range_pct": 7.0},
        "supply": {"float_shares": 1.2e7, "dilution_note": "unverified"},
        "catalyst": {"type": "Earnings", "headline": "Q2 beat",
                     "age_hours": 3.0, "publisher": "X", "detail": ""},
        "volume": {"sequence": "expansion held"},
        "strength": {"vs_spy": 5.0, "vs_iwm": 4.0, "vs_sector": 3.0,
                     "sector_etf": "XBI", "diverging_from": []},
        "room": {"room_ratio": 2.0, "nearest": 11.0, "nearest_pct": 10.0,
                 "detail": "10% to 11.00", "blocked": False},
        "patterns": {"primary": "ORB_BREAKOUT"},
        "entry": {"score": 88, "entry_grade": "HIGH QUALITY", "chase_score": 1,
                  "chase_label": "🟢 fine", "chase_reasons": [],
                  "beyond_trigger_atr": 0.3, "vwap_distance_atr": 0.4,
                  "vwap_distance_pct": 2.0, "expected_move_consumed_pct": 60.0,
                  "candle": {"detail": "body 70%"},
                  "pullback": {"detail": "range 0.5x"}},
        "gate": {"checks": [{"name": "Spread acceptable", "ok": True,
                             "detail": "0.3%"}],
                 "failed": [], "unverified": [], "passed": True},
        "plan": {"actionable": True, "trigger": "break of 10.00",
                 "trigger_level": 10.0, "triggered": True, "entry": 10.05,
                 "stop": 9.70, "stop_basis": "VWAP at 9.80",
                 "risk_per_share": 0.35, "risk_pct_of_price": 3.5,
                 "target1": 10.75, "target1_basis": "measured move",
                 "target2": 11.30, "target2_basis": "3R", "rr": 2.0,
                 "rr_target2": 3.6, "rr_blended": 2.8,
                 "invalidation": "close below 9.70", "atr_5min": 0.2,
                 "stop_too_wide": False},
        "sizing": {"shares": 1400, "position_value": 14070.0,
                   "structural_risk_per_share": 0.35, "slippage_per_share": 0.02,
                   "true_risk_per_share": 0.37, "dollar_risk": 518.0,
                   "max_dollar_risk": 1000.0, "risk_budget_used_pct": 51.8,
                   "pct_of_capital": 14.07, "binding_constraint": "account risk",
                   "risk_is_binding": True, "risk_based_shares": 1400,
                   "position_liquidity_pct": 8.0, "risk_multiplier": 1.0},
        "confirmations": [{"name": "Fresh catalyst", "ok": True, "detail": "3h"},
                          {"name": "Room to next level", "ok": False,
                           "detail": "tight"}],
        "warnings": [],
    }
    row.update(over)
    return row


def _snapshot(rows, **over):
    snap = {"asof": "2026-08-07", "generated": "2026-08-09T18:00:00",
            "notes": ["market closed"], "regime": {
                "label": "RISK ON", "emoji": "🟢", "score": 6,
                "reasons": ["SPY above VWAP"], "unavailable": ["market breadth"]},
            "settings": {"risk_pct": 1.0}, "rows": rows}
    snap.update(over)
    return snap


class StoreTests(unittest.TestCase):
    """The snapshot file the page reads. Untested, `store.save` shipped with
    a missing `import os` and every --save silently exited 1."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "snap.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_then_load_round_trips(self):
        result = {"asof": "2026-08-07", "notes": ["n"], "regime": {"label": "RISK ON"},
                  "settings": {"risk_pct": 1.0}, "rows": [_row()]}
        store.save(result, self.path)
        back = store.load(self.path)
        self.assertEqual(back["asof"], "2026-08-07")
        self.assertEqual(back["rows"][0]["ticker"], "TEST")
        self.assertIn("generated", back)

    def test_save_drops_dataframes_and_leaves_no_temp_file(self):
        import pandas as pd
        row = _row()
        row["session"]["bars"] = pd.DataFrame({"Close": [1.0, 2.0]})
        store.save({"asof": "2026-08-07", "rows": [row]}, self.path)
        back = store.load(self.path)
        self.assertNotIn("bars", back["rows"][0]["session"])
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_load_returns_none_for_missing_or_corrupt(self):
        self.assertIsNone(store.load(self.path))
        self.assertIsNone(store.load(Path(self._tmp.name) / "nope.json"))

    def test_load_falls_back_to_the_pre_rename_path(self):
        """So the page is not blank between the Small-Cap rename and the
        next scan."""
        orig = (store.SNAPSHOT, store.LEGACY_SNAPSHOT)
        store.SNAPSHOT = self.path
        store.LEGACY_SNAPSHOT = Path(self._tmp.name) / "legacy.json"
        try:
            store.save({"asof": "legacy", "rows": []}, store.LEGACY_SNAPSHOT)
            self.assertEqual(store.load()["asof"], "legacy")
            # …and the current path wins once it exists.
            store.save({"asof": "current", "rows": []}, store.SNAPSHOT)
            self.assertEqual(store.load()["asof"], "current")
        finally:
            store.SNAPSHOT, store.LEGACY_SNAPSHOT = orig

    def test_save_leaves_no_orphan_when_serialisation_fails(self):
        """A failure between the write and the replace otherwise leaves a
        full-size orphan next to the real snapshot."""
        class Unserialisable:
            def __repr__(self):
                raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            store.save({"asof": "x", "rows": [{"bad": Unserialisable()}]}, self.path)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_save_replaces_atomically(self):
        """A reader must see the whole previous snapshot or the whole new
        one — a torn read reported 5 rows with 13 confirmations from a scan
        that produced 16 with 14."""
        store.save({"asof": "a", "rows": [_row()]}, self.path)
        store.save({"asof": "b", "rows": [_row(), _row()]}, self.path)
        back = store.load(self.path)
        self.assertEqual(back["asof"], "b")
        self.assertEqual(len(back["rows"]), 2)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (store.SNAPSHOT, store.LEGACY_SNAPSHOT)
        store.SNAPSHOT = Path(self._tmp.name) / "snap.json"
        # The legacy path is redirected too, or `load`'s fallback reaches
        # out of the temp directory and reads the developer's real
        # snapshot — which is how the empty-state tests started rendering
        # data they never wrote.
        store.LEGACY_SNAPSHOT = Path(self._tmp.name) / "legacy.json"

    def tearDown(self):
        store.SNAPSHOT, store.LEGACY_SNAPSHOT = self._orig
        self._tmp.cleanup()

    def _render(self, snapshot):
        store.SNAPSHOT.write_text(json.dumps(snapshot))
        body, _ = V.stockdaytrade_page()
        return body

    def test_renders_a_complete_row(self):
        body = self._render(_snapshot([_row()]))
        self.assertIn("TEST", body)
        self.assertIn("ENTER NOW", body)
        self.assertIn("Entry quality", body)
        self.assertIn("Execution gate", body)

    def test_empty_state_without_a_snapshot(self):
        body, _ = V.stockdaytrade_page()
        self.assertIn("No scan yet", body)

    def test_unreadable_snapshot_shows_the_empty_state(self):
        store.SNAPSHOT.write_text("{ this is not json")
        body, _ = V.stockdaytrade_page()
        self.assertIn("No scan yet", body)

    def test_regression_legacy_snapshot_without_risk_based_shares(self):
        """Was: `:,` applied to a missing `risk_based_shares` raised
        "unsupported format string passed to NoneType.__format__" and took
        the whole page down. A stored snapshot is a schema this module does
        not control."""
        sizing = dict(_row()["sizing"])
        sizing["risk_is_binding"] = False
        sizing.pop("risk_based_shares")
        body = self._render(_snapshot([_row(sizing=sizing)]))
        self.assertIn("TEST", body)

    def test_renders_a_row_missing_every_field_added_after_v1(self):
        """The general form of the same bug: a snapshot from before the
        entry/gate/action work must still render."""
        legacy = _row()
        for key in ("entry", "gate", "action", "action_why", "entry_score",
                    "entry_grade", "chase_score", "chase_label", "weights",
                    "profile", "profile_label"):
            legacy.pop(key, None)
        legacy["sizing"] = {"shares": 100, "position_value": 1000.0,
                            "binding_constraint": "account risk"}
        body = self._render(_snapshot([legacy]))
        self.assertIn("TEST", body)

    def test_renders_a_row_that_is_almost_entirely_empty(self):
        body = self._render(_snapshot([{"ticker": "BARE"}]))
        self.assertIn("BARE", body)

    def test_every_column_is_sortable(self):
        body = self._render(_snapshot([_row()]))
        for idx in range(len(V._COLS)):
            self.assertIn(f'onclick="sdtSort({idx})"', body)
        # One data-sort per cell per row, or a column silently sorts blank.
        self.assertEqual(body.count("data-sort="), len(V._COLS))

    def test_column_labels_are_unique(self):
        """'Setup' and 'Entry' each appeared twice — merely confusing to
        read, ambiguous once the headers are clickable."""
        labels = [c[0] for c in V._COLS]
        self.assertEqual(len(labels), len(set(labels)), labels)

    def test_sort_keys_are_raw_values_not_formatted_text(self):
        """`$9.4M` sorts above `$215.2M` as a string. Cells carry the raw
        number so the JS never parses display text."""
        body = self._render(_snapshot([_row()]))
        self.assertIn('data-sort="25000000.0"', body)   # dollar volume
        self.assertIn('data-sort="12000000.0"', body)   # float shares

    def test_missing_values_sort_as_blank_not_zero(self):
        """Unknown is not small — blanks must be distinguishable so the JS
        can keep them last in both directions."""
        self.assertEqual(V._sort_key(None), "")
        self.assertEqual(V._sort_key(0), "0.0")
        self.assertNotEqual(V._sort_key(None), V._sort_key(0))
        self.assertEqual(V._sort_key("ORB_BREAKOUT"), "orb_breakout")

    def test_action_column_sorts_by_urgency_not_alphabetically(self):
        """"AVOID" before "ENTER NOW" would be a nonsense ordering of a
        column whose whole point is urgency."""
        from stockanalysis.core.daytrade.engine import ACTION_RANK
        enter = V._sort_key(ACTION_RANK["🔥 ENTER NOW"])
        avoid = V._sort_key(ACTION_RANK["🔴 AVOID"])
        self.assertLess(float(enter), float(avoid))

    def test_open_air_room_sorts_above_every_finite_ratio(self):
        """Room is unbounded when nothing is overhead, so it must outrank
        a large ratio rather than read as a missing value."""
        room = dict(_row()["room"], nearest=None, room_ratio=None)
        body = self._render(_snapshot([_row(room=room)]))
        self.assertIn(f'data-sort="{float(V._OPEN_AIR_SORT)!r}"', body)

    def test_rank_ticker_and_direction_are_frozen_left(self):
        """23 columns on a 1620px table: scrolling right to read R:R or
        Chase otherwise leaves a row of numbers with no idea which stock
        they belong to or which way the trade goes, and a misattributed
        stop price is not cosmetic."""
        body = self._render(_snapshot([_row()]))
        self.assertEqual(sorted(V._FROZEN), [0, 1, 2])
        self.assertEqual([c[0] for c in V._COLS][:3], ["#", "Ticker", "Dir"])
        for offset in V._FROZEN.values():
            self.assertIn(f"position:sticky;left:{offset}px", body)
        # Offsets must be the running sum of the widths, or the columns
        # overlap when scrolled.
        self.assertEqual(V._FROZEN[1], V._COL_W[0])
        self.assertEqual(V._FROZEN[2], V._COL_W[0] + V._COL_W[1])

    def test_frozen_cells_are_opaque_and_layered_above_scrolled_ones(self):
        """A sticky cell is transparent by default — the scrolled columns
        slide visibly underneath it without an explicit background."""
        body = self._render(_snapshot([_row()]))
        frozen = [seg for seg in body.split("<td")[1:] if "position:sticky" in seg]
        self.assertEqual(len(frozen), len(V._FROZEN))
        for seg in frozen:
            self.assertIn("background:white", seg)
            self.assertIn("z-index:2", seg)
        # The corner headers are sticky in both axes, so they must outrank
        # both the plain headers and the frozen body cells.
        self.assertEqual(body.count("z-index:4"), len(V._FROZEN))
        # Exactly one divider, on the last frozen column.
        self.assertEqual(body.count("box-shadow:1px 0 0 #e1e0d9"), 2)

    def test_direction_is_shown_as_a_column(self):
        """A correct short reads as a bug — "why is STOP greater than
        entry" — unless the page says which way the trade goes."""
        long_body = self._render(_snapshot([_row(direction="long")]))
        self.assertIn("▲ LONG", long_body)
        short_body = self._render(_snapshot([_row(direction="short")]))
        self.assertIn("▼ SHORT", short_body)
        self.assertIn("stop sits above entry", short_body)

    def test_column_label_renamed_from_stock_to_cscore(self):
        """"Stock" said nothing about what the number was."""
        body = self._render(_snapshot([_row()]))
        labels = [c[0] for c in V._COLS]
        self.assertIn("CScore", labels)
        self.assertNotIn("Stock", labels)
        self.assertIn("confluence", dict((c[0], c[2]) for c in V._COLS)["CScore"])
        self.assertIn("CScore 82", body)

    def test_universe_dropdown_lists_the_screen_and_watchlists(self):
        opts = V._universe_options()
        self.assertIn("Market screen", opts)
        # daytrade leads the watchlists — burying this page's natural
        # companion under a run of sector lists makes the common case the
        # slowest to reach.
        names = re.findall(r'<option value="([^"]*)"', opts)
        if "daytrade" in names:
            self.assertEqual(names[1], "daytrade")

    def test_controls_preselect_what_was_actually_requested(self):
        """Was: preselection was inferred from the rows, so an auto scan of
        the daytrade list showed "Large cap / Market screen" — and hitting
        Run again would silently have run a different scan."""
        body = self._render(_snapshot([_row()], profile_requested="auto",
                                      universe="daytrade"))
        self.assertIn('value="auto" selected', body)
        self.assertIn('value="daytrade" selected', body)

    def test_controls_fall_back_for_snapshots_without_the_request(self):
        body = self._render(_snapshot([_row()]))
        self.assertIn("Market screen", body)

    def test_deleted_watchlist_is_named_rather_than_silently_dropped(self):
        opts = V._universe_options("a-list-that-was-renamed")
        self.assertIn("no longer in watchlists.json", opts)

    def test_money_never_rounds_a_risk_figure_to_zero(self):
        """$450 of risk printed as "$0K" — the two numbers a sizing
        decision rests on, both rounded away."""
        self.assertEqual(V._money(449.69), "$450")
        self.assertEqual(V._money(1000), "$1,000")
        self.assertEqual(V._money(25_000), "$25K")
        self.assertEqual(V._money(2.5e6), "$2.5M")
        self.assertEqual(V._money(None), V.DASH)

    def test_stale_snapshot_is_flagged_loudly(self):
        fresh = self._render(_snapshot([_row(is_live=True)],
                                       generated="2026-01-01T00:00:00"))
        self.assertIn("Stale intraday data", fresh)

    def test_regression_age_survives_a_timezone_difference(self):
        """Was: `generated` was naive local time, but the CLI writes it in
        the shell's timezone and the webapp runs under
        TZ=America/New_York — so a scan that had just finished read as
        "1.0 hours ago", and the staleness banner is load-bearing."""
        from datetime import datetime, timedelta, timezone
        just_now = datetime.now(timezone(timedelta(hours=-4)))
        text, status = V._age_note(just_now.isoformat(timespec="seconds"))
        self.assertEqual(status, "good")
        self.assertEqual(text, "just now")
        # A naive stamp still works, read as this process's local time.
        naive = datetime.now().isoformat(timespec="seconds")
        self.assertEqual(V._age_note(naive)[1], "good")
        self.assertEqual(V._age_note(None)[1], "bad")
        self.assertEqual(V._age_note("not a date")[1], "bad")


if __name__ == "__main__":
    unittest.main()
