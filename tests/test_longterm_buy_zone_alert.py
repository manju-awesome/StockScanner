"""
Tests for the buy-zone proximity reading and the Long-Term page's alert,
column order and CSV export.

The invariants here are the ones that decide whether the alert is worth
having at all:

  1. Proximity is measured against the SAME zone the Buy Zone column shows.
     Two numbers for one idea is the failure buy_zones._display_zone was
     written to avoid, and an alert measured off a different band would
     reintroduce it one level up.
  2. "Not measurable" is not "not close". A row with no zone, or no price,
     returns None rather than a state, because a caller that cannot tell
     those apart will report the second as the first.
  3. Through the zone is not approaching it. Price below the band is a
     distinct state — the level arrived and did not hold — and collapsing
     it into APPROACHING would describe a breakdown as an opportunity.
  4. The alert refuses names the engine has rejected. Measured live, 223 of
     657 names are at or within 5% of their zone and 166 of those are
     REJECT — an alert firing on a third of the library carries no
     information, which is the same reason entry_alerts.py requires a BUY
     verdict.
  5. What it withholds, it counts. The bar cites the excluded number and
     links to it, because a count the table beside it cannot reproduce is
     the one thing this page must not print.
  6. The CSV carries scalars, not rendered cells. A cell stacking two
     numbers becomes two columns, so what comes out is sortable.

Run with: python -m unittest tests.test_longterm_buy_zone_alert
"""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.longterm import buy_zones as BZ
from stockanalysis.webapp import longterm_view as V


def _row(low=94.0, high=97.0, price=100.0, kind="technical",
         label="200 MA", status="OWN", ticker="TEST", action="WATCH"):
    """One evaluated row, carrying only what the pieces under test read."""
    zone = None if low is None else {
        "low": low, "high": high, "kind": kind, "label": label,
        "basis": "200 MA", "qualifies": kind == "investment",
        "distance_pct": None,
    }
    return {
        "ticker": ticker, "name": f"{ticker} Inc.", "price": price,
        "action": action, "icon": "",
        "buy_zones": {"display_zone": zone},
        "investment": {"status": status},
        "quality": {}, "valuation": {}, "trend": {}, "pullback": {},
        "confluence": {}, "rs": {},
    }


class ZoneProximity(unittest.TestCase):
    """(1)–(3): what the reading says, and when it refuses to say anything."""

    def test_inside_the_band_is_in_zone_with_no_gap(self):
        p = BZ.zone_proximity(_row(price=96.0))
        self.assertEqual(p["state"], "IN_ZONE")
        self.assertEqual(p["gap_pct"], 0.0)
        self.assertTrue(p["near"])

    def test_boundaries_are_inside_the_zone(self):
        for price in (94.0, 97.0):
            self.assertEqual(BZ.zone_proximity(_row(price=price))["state"],
                             "IN_ZONE")

    def test_above_and_close_is_approaching(self):
        p = BZ.zone_proximity(_row(price=100.0))     # 3% above the top
        self.assertEqual(p["state"], "APPROACHING")
        self.assertTrue(p["near"])
        # Negative: the zone is BELOW price, which is the sign convention
        # every other distance on the page uses.
        self.assertLess(p["gap_pct"], 0)

    def test_above_and_far_is_not_near(self):
        p = BZ.zone_proximity(_row(price=200.0))
        self.assertEqual(p["state"], "ABOVE")
        self.assertFalse(p["near"])

    def test_threshold_is_honoured(self):
        row = _row(price=100.0)                       # 3.09% above
        self.assertTrue(BZ.zone_proximity(row, within_pct=5.0)["near"])
        self.assertFalse(BZ.zone_proximity(row, within_pct=2.0)["near"])

    def test_below_the_band_is_its_own_state(self):
        """(3) Through the level, not approaching it."""
        p = BZ.zone_proximity(_row(price=90.0))
        self.assertEqual(p["state"], "BELOW")
        self.assertFalse(p["near"])

    def test_no_zone_and_no_price_are_unmeasurable(self):
        """(2) None, not a state."""
        self.assertIsNone(BZ.zone_proximity(_row(low=None)))
        self.assertIsNone(BZ.zone_proximity(_row(price=None)))
        self.assertIsNone(BZ.zone_proximity(_row(price=0)))
        self.assertIsNone(BZ.zone_proximity({}))

    def test_a_live_price_overrides_the_stored_one(self):
        """The stale-levels/fresh-price asymmetry entry_alerts.py relies on."""
        row = _row(price=200.0)
        self.assertEqual(BZ.zone_proximity(row)["state"], "ABOVE")
        self.assertEqual(BZ.zone_proximity(row, price=96.0)["state"], "IN_ZONE")

    def test_qualifying_zones_are_flagged_as_such(self):
        self.assertFalse(BZ.zone_proximity(_row())["qualifies"])
        self.assertTrue(BZ.zone_proximity(_row(kind="investment"))["qualifies"])

    def test_measured_against_the_column_s_own_zone(self):
        """(1) The reading reads display_zone and nothing else."""
        row = _row(low=94.0, high=97.0, price=96.0)
        # A different band elsewhere on the result must not change the answer.
        row["pullback"] = {"buy_zone": {"price": 50.0, "distance_pct": -46.0}}
        p = BZ.zone_proximity(row)
        self.assertEqual((p["low"], p["high"]), (94.0, 97.0))
        self.assertEqual(p["state"], "IN_ZONE")


class AlertSelection(unittest.TestCase):
    """(4)–(5): who the alert fires on, and what it admits to withholding."""

    def test_rejected_businesses_are_excluded_by_default(self):
        rows = [_row(ticker="GOOD", price=96.0, status="OWN"),
                _row(ticker="BAD", price=96.0, status="REJECT")]
        self.assertEqual([r["ticker"] for r, _p in V.zone_alerts(rows)],
                         ["GOOD"])

    def test_rejected_businesses_are_reachable_on_request(self):
        """(5) Withheld, not hidden — ?near=all has to be able to show them."""
        rows = [_row(ticker="GOOD", price=96.0, status="OWN"),
                _row(ticker="BAD", price=96.0, status="REJECT")]
        self.assertEqual(
            sorted(r["ticker"]
                   for r, _p in V.zone_alerts(rows, ownable_only=False)),
            ["BAD", "GOOD"])

    def test_names_far_from_a_zone_never_alert(self):
        self.assertEqual(V.zone_alerts([_row(price=500.0)]), [])

    def test_in_zone_sorts_above_approaching(self):
        rows = [_row(ticker="NEAR", price=100.0),
                _row(ticker="INSIDE", price=96.0)]
        self.assertEqual([r["ticker"] for r, _p in V.zone_alerts(rows)],
                         ["INSIDE", "NEAR"])

    def test_a_qualifying_zone_sorts_above_a_technical_one(self):
        rows = [_row(ticker="TECH", price=96.0, kind="technical"),
                _row(ticker="INV", price=96.0, kind="investment")]
        self.assertEqual([r["ticker"] for r, _p in V.zone_alerts(rows)],
                         ["INV", "TECH"])

    def test_nearer_first_within_a_state(self):
        rows = [_row(ticker="FAR", price=101.0),
                _row(ticker="CLOSE", price=98.0)]
        self.assertEqual([r["ticker"] for r, _p in V.zone_alerts(rows)],
                         ["CLOSE", "FAR"])


class AlertBar(unittest.TestCase):
    """What the rendered bar actually claims."""

    def _bar(self, rows, mode=""):
        return V._zone_alert_bar(rows, lambda **kw: "/longterm", mode)

    def test_empty_state_names_the_nearest_miss(self):
        """A measurement, not a broken filter."""
        html = self._bar([_row(ticker="AAA", price=130.0)])
        self.assertIn("Nothing in this view is within", html)
        self.assertIn("AAA", html)

    def test_the_withheld_count_is_printed(self):
        rows = ([_row(ticker="GOOD", price=96.0)]
                + [_row(ticker=f"BAD{i}", price=96.0, status="REJECT")
                   for i in range(3)])
        html = self._bar(rows)
        self.assertIn("3 more", html)
        self.assertIn("rejected", html)

    def test_the_pill_list_is_capped_and_says_so(self):
        rows = [_row(ticker=f"T{i}", price=96.0)
                for i in range(V.MAX_ZONE_PILLS + 5)]
        html = self._bar(rows)
        self.assertEqual(len(re.findall(r'href="#ltr-', html)),
                         V.MAX_ZONE_PILLS)
        self.assertIn("+5 more", html)

    def test_each_pill_carries_the_engine_s_own_verdict(self):
        """A list of tickers under an alert heading reads as a
        recommendation unless the verdict is on the same line."""
        html = self._bar([_row(ticker="AAA", price=96.0, action="WATCH")])
        self.assertIn("Watch", html)


class ColumnOrder(unittest.TestCase):
    def test_buy_zone_sits_immediately_after_price(self):
        keys = [c[0] for c in V._COLUMNS]
        self.assertEqual(keys[:3], ["ticker", "price", "buy_zone"])

    def test_every_column_has_exactly_one_definition(self):
        keys = [c[0] for c in V._COLUMNS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_near_names_sort_to_the_top_of_the_column(self):
        _html_near, sort_near = V._buy_zone_cell(_row(price=96.0))
        _html_far, sort_far = V._buy_zone_cell(_row(price=300.0))
        self.assertGreater(sort_near, sort_far)

    def test_the_cell_marks_a_name_at_its_zone(self):
        html, _ = V._buy_zone_cell(_row(price=96.0))
        self.assertIn("AT ZONE", html)
        html, _ = V._buy_zone_cell(_row(price=100.0))
        self.assertIn("NEAR", html)
        html, _ = V._buy_zone_cell(_row(price=300.0))
        self.assertNotIn("AT ZONE", html)
        self.assertNotIn("NEAR", html)


class CsvExport(unittest.TestCase):
    """(6): scalars, one per field, and nothing silently dropped."""

    def test_fields_are_unique_and_grouped_under_declared_groups(self):
        keys = [k for k, _h, _g, _d in V._CSV_FIELDS]
        self.assertEqual(len(keys), len(set(keys)))
        for _k, _h, group, _d in V._CSV_FIELDS:
            self.assertIn(group, V._CSV_GROUPS)

    def test_a_stacked_cell_becomes_separate_columns(self):
        keys = {k for k, _h, _g, _d in V._CSV_FIELDS}
        for k in ("zone_low", "zone_high", "zone_gap_pct",
                  "entry_price", "stop_price"):
            self.assertIn(k, keys)

    def test_the_row_carries_exactly_the_declared_fields(self):
        row = V._csv_row(_row(price=96.0))
        self.assertEqual(set(row), {k for k, _h, _g, _d in V._CSV_FIELDS})

    def test_zone_values_reach_the_row(self):
        row = V._csv_row(_row(price=96.0, kind="investment", label="50 MA"))
        self.assertEqual(row["zone_low"], 94.0)
        self.assertEqual(row["zone_high"], 97.0)
        self.assertEqual(row["zone_state"], "IN_ZONE")
        self.assertEqual(row["zone_kind"], "investment")
        self.assertEqual(row["zone_label"], "50 MA")

    def test_payload_rows_are_positional_and_match_the_field_list(self):
        html = V._csv_payload([_row(ticker="AAA"), _row(ticker="BBB")])
        raw = re.search(r'id="lt-csv-data">(.*?)</script>', html, re.S).group(1)
        data = json.loads(raw.replace("<\\/", "</"))
        self.assertEqual(len(data["rows"]), 2)
        for record in data["rows"]:
            self.assertIsInstance(record, list)
            self.assertEqual(len(record), len(data["fields"]))
        at = [f["key"] for f in data["fields"]].index("ticker")
        self.assertEqual([r[at] for r in data["rows"]], ["AAA", "BBB"])

    def test_the_payload_cannot_close_its_own_script_tag(self):
        row = _row(ticker="AAA")
        row["name"] = "</script><script>alert(1)</script>"
        html = V._csv_payload([row])
        self.assertNotIn("</script><script>", html)
        self.assertEqual(html.count("</script>"), 1)

    def test_floats_are_rounded(self):
        self.assertEqual(V._csv_scalar(91.400000000000006), 91.4)
        self.assertIsNone(V._csv_scalar(float("nan")))

    def test_the_picker_offers_every_field(self):
        html = V._csv_bar(5, filtered=False)
        for key, _h, _g, _d in V._CSV_FIELDS:
            self.assertIn(f'value="{key}"', html)

    def test_no_picker_without_rows(self):
        self.assertEqual(V._csv_bar(0, filtered=False), "")


if __name__ == "__main__":
    unittest.main()


def _full_row(ticker="TEST"):
    """A row carrying everything `_cells` reads.

    Bigger than `_row()` on purpose: the selection tests go through the cell
    renderer rather than around it, because the box living INSIDE the ticker
    cell is the design decision worth protecting — it is what keeps the tick
    on screen when the table is scrolled right, and a test that called
    `_select_box` directly would still pass if it were moved to a column of
    its own.
    """
    row = _row(ticker=ticker)
    row.update({
        "quality": {"score": 80, "tier": "High Quality"},
        "valuation": {"band": "FAIR", "band_icon": "", "band_label": "Fair"},
        "trend": {"state": "CONFIRMED", "icon": "", "summary": ""},
        "pullback": {"stage": "STAGE1_EMA", "zone": "EMA"},
        "rs": {"strong": True, "score": 70},
        "regime": "SELECTIVE", "lt_score": 70,
    })
    return row


class CspSelection(unittest.TestCase):
    """The hand-off to the CSP scan.

    The invariant worth protecting is the pre-warning: `data-elig` must be
    the CSP engine's OWN answer, not a restatement of its rules here. A
    second copy of "LQuality >= 70 and not Stage 4 and not overvalued"
    would drift the first time a threshold moved, and it would drift
    silently — the bar would keep promising a scan that the gate then
    throws away.
    """

    def _selectable(self, row=None):
        return V._cells(row or _full_row(), selectable=True)["ticker"][0]

    def test_the_box_is_absent_unless_asked_for(self):
        """The Dashboard renders through the same table and has no bar."""
        self.assertNotIn("lt-pick", V._cells(_full_row())["ticker"][0])
        self.assertIn("lt-pick", self._selectable())

    def test_the_box_rides_inside_the_pinned_ticker_cell(self):
        """Not a column of its own — ticker is pinned, so the tick stays on
        screen at any horizontal scroll."""
        cells = V._cells(_full_row(), selectable=True)
        self.assertIn("lt-pick", cells["ticker"][0])
        self.assertTrue(all("lt-pick" not in html
                            for key, (html, _s) in cells.items()
                            if key != "ticker"))
        self.assertIn("ticker", V.PINNED_COLUMNS)

    def test_the_sort_value_is_unaffected_by_the_box(self):
        """The CSV export orders rows off this attribute."""
        self.assertEqual(V._cells(_full_row("NVDA"),
                                  selectable=True)["ticker"][1], "NVDA")

    def test_the_box_carries_the_ticker_as_its_value(self):
        html = self._selectable(_full_row("NVDA"))
        self.assertIn('value="NVDA"', html)

    def test_eligibility_comes_from_the_csp_engine(self):
        row = _full_row()
        with mock.patch("stockanalysis.core.csp.eligibility.classify",
                        return_value={"status": "CSP REJECTED"}) as gate:
            self.assertIn('data-elig="0"', self._selectable(row))
        gate.assert_called_once()

    def test_an_accepted_company_is_flagged_eligible(self):
        with mock.patch("stockanalysis.core.csp.eligibility.classify",
                        return_value={"status": "CSP APPROVED"}):
            self.assertIn('data-elig="1"', self._selectable())

    def test_the_gate_is_asked_once_per_row(self):
        row = _full_row()
        with mock.patch("stockanalysis.core.csp.eligibility.classify",
                        return_value={"status": "CSP APPROVED"}) as gate:
            self._selectable(row)
            self._selectable(row)
        self.assertEqual(gate.call_count, 1)

    def test_a_broken_csp_package_does_not_break_the_page(self):
        """A page that will not render beats no pre-warning — but only
        just, so the fallback is permissive rather than silent-reject."""
        with mock.patch("stockanalysis.core.csp.eligibility.classify",
                        side_effect=ImportError("moved")):
            html = self._selectable()
        self.assertIn('data-elig="1"', html)

    def test_the_bar_ships_hidden_and_carries_its_controls(self):
        bar = V._csp_bar()
        self.assertIn('id="lt-csp-bar"', bar)
        self.assertIn("display:none", bar)
        for control in ("lt-csp-dte", "lt-csp-earn", "lt-csp-go",
                        "ltPickEligible()", "ltPickAll(0)"):
            self.assertIn(control, bar)

    def test_the_selection_cap_matches_the_scan_s_own(self):
        """parse_tickers truncates at 60 and the job caps survivors at 60;
        a bar offering more would lose the tail after paying for it."""
        self.assertEqual(V.MAX_CSP_SELECTION, 60)
        self.assertIn("LT_MAX_PICK = 60", V.EXPORT_JS)


class CspPreset(unittest.TestCase):
    def test_the_preset_exists_and_is_grouped(self):
        from stockanalysis.core.longterm import screen as LS
        preset = next((p for p in LS.PRESETS
                       if p["key"] == "put_candidates"), None)
        self.assertIsNotNone(preset)
        self.assertIn(preset["group"], LS.PRESET_GROUPS)

    def test_every_rule_parses(self):
        from stockanalysis.core.longterm import screen as LS
        preset = next(p for p in LS.PRESETS if p["key"] == "put_candidates")
        for rule in preset["rules"]:
            self.assertIsNotNone(LS.parse_rule(rule), rule)

    def test_the_rules_mirror_the_csp_gate(self):
        """Each rule has a counterpart in core.csp.eligibility. If one is
        dropped there, this is the test that notices."""
        from stockanalysis.core.csp import eligibility as EL
        from stockanalysis.core.longterm import screen as LS
        preset = next(p for p in LS.PRESETS if p["key"] == "put_candidates")
        rules = dict(r.rsplit(":", 1) for r in preset["rules"])
        self.assertGreaterEqual(float(rules["lquality:gte"]), EL.MIN_QUALITY)
        self.assertIn("valuation_band:ne", rules)
        self.assertIn("trend_state:ne", rules)
        # The collateral-efficiency floor the gate applies to price.
        self.assertEqual(float(rules["lt_price:gte"]), 15.0)


class ZoneAboveSpotIsNotABuyZone(unittest.TestCase):
    """INTU showed a buy zone of $442-453 against a price of $345.

    The stock was down 51.6% and had fallen straight through its 200 MA.
    That band was the shallowest support still clearing the value test, so
    `_investment_zones` made it the "Preferred" zone — and `_display_zone`
    took the top investment zone without asking whether price could reach
    it. The column then said "buy at $442" of a $345 stock.

    Two rules come out of it: the displayed zone must be one price can
    actually reach, and when none is, every consumer has to be able to say
    the band is overhead rather than treat "below it" as a win.
    """

    def _zones(self, price, investment=(), technical=()):
        return BZ._display_zone(list(investment), list(technical), price)

    def _band(self, low, high, label="200 MA"):
        return {"low": low, "high": high, "label": label, "basis": label,
                "support": label,
                "distance_pct": None}

    def test_an_investment_zone_above_spot_is_not_chosen(self):
        z = self._zones(345.0,
                        investment=[self._band(442.0, 453.0, "Preferred")],
                        technical=[self._band(442.0, 453.0),
                                   self._band(289.0, 297.0, "50 MA")])
        self.assertEqual((z["low"], z["high"]), (289.0, 297.0))
        self.assertEqual(z["kind"], "technical")
        self.assertFalse(z["above_spot"])

    def test_a_reachable_investment_zone_still_wins(self):
        z = self._zones(345.0,
                        investment=[self._band(300.0, 320.0, "Preferred")],
                        technical=[self._band(289.0, 297.0, "50 MA")])
        self.assertEqual(z["kind"], "investment")
        self.assertEqual((z["low"], z["high"]), (300.0, 320.0))

    def test_the_deepest_reachable_technical_band_is_taken(self):
        z = self._zones(345.0, technical=[self._band(442.0, 453.0),
                                          self._band(320.0, 338.0, "EMA"),
                                          self._band(289.0, 297.0, "50 MA")])
        self.assertEqual((z["low"], z["high"]), (289.0, 297.0))

    def test_when_every_band_is_overhead_it_says_so(self):
        """DECK and META are in this state — below all tracked support."""
        z = self._zones(92.18, technical=[self._band(103.0, 106.0),
                                          self._band(96.0, 99.5, "EMA")])
        self.assertTrue(z["above_spot"])
        self.assertIsNotNone(z["low"])      # still shown, just flagged

    def test_no_zone_at_all_is_still_none(self):
        self.assertIsNone(self._zones(100.0))

    def test_the_longterm_cell_does_not_dress_it_as_a_buy(self):
        row = {"price": 92.18, "investment": {}, "action": "WATCH",
               "buy_zones": {"display_zone": {
                   "low": 96.0, "high": 99.5, "kind": "investment",
                   "label": "50 MA", "distance_pct": 7.9,
                   "above_spot": True}}}
        html, _sort = V._buy_zone_cell(row)
        self.assertIn("price is below this band", html)
        # Not green, and not bold: it is not a qualifying buy.
        self.assertNotIn("#0F6E56", html)


class AccumulationLadder(unittest.TestCase):
    """The rungs below today's price, when the bands are all overhead.

    META is the case the ladder exists for: price $543.67 with its 8/21
    EMA at $576-589, its 50 MA at $588-601 and its 200 MA at $618-632 —
    three bands, every one above, and a Buy Zone column with nothing
    useful to say. What META does have is a volume shelf at $541.32 with
    180 touches, tested and defended, which never reached the band builder
    because `_technical_zones` only sees moving averages.
    """

    def _pullback(self, shelf=541.32, touches=180.0):
        return {"buy_zone": {"price": shelf, "touches": touches,
                             "volume_confirmed": True, "actual_support": True,
                             "label": f"{touches:.0f} touches · "
                                      f"volume-confirmed",
                             "note": "tested support"}}

    def _band(self, low, high, label):
        return {"low": low, "high": high, "mid": (low + high) / 2,
                "label": label, "basis": label}

    def _fund(self, *prices):
        return {"ladder": [{"key": k, "label": l, "price": p, "cagr_pct": c}
                           for (k, l, p, c) in prices]}

    def test_the_tested_shelf_becomes_zone_one(self):
        lad = BZ.accumulation_ladder(
            self._pullback(), [self._band(576, 589, "8 / 21 EMA")],
            self._fund(), 543.67)
        self.assertEqual(lad[0]["zone"], 1)
        self.assertEqual(lad[0]["price"], 541.32)
        self.assertEqual(lad[0]["source"], "volume_shelf")
        self.assertEqual(lad[0]["touches"], 180.0)

    def test_overhead_bands_are_not_rungs(self):
        lad = BZ.accumulation_ladder(
            self._pullback(), [self._band(576, 589, "8 / 21 EMA"),
                               self._band(618, 632, "200 MA")],
            self._fund(), 543.67)
        self.assertTrue(all(r["source"] != "moving_average" for r in lad))

    def test_value_rungs_fill_in_when_no_chart_level_is_left(self):
        """APP: every average overhead and no shelf — the value ladder is
        the only evidence still below the price."""
        lad = BZ.accumulation_ladder(
            {}, [self._band(320, 332, "8 / 21 EMA")],
            self._fund(("fair", "Fair", 254.20, 12.0),
                       ("attractive", "Attractive", 222.73, 15.0),
                       ("exceptional", "Exceptional", 180.03, 20.0)),
            311.68)
        self.assertEqual([r["label"] for r in lad],
                         ["Fair", "Attractive", "Exceptional"])
        self.assertTrue(all(r["source"] == "fundamental" for r in lad))

    def test_rungs_are_ordered_nearest_first(self):
        lad = BZ.accumulation_ladder(
            self._pullback(shelf=214.0), [self._band(193, 197, "200 MA"),
                                          self._band(206, 217, "50 MA")],
            self._fund(), 219.74)
        prices = [r["price"] for r in lad]
        self.assertEqual(prices, sorted(prices, reverse=True))
        self.assertEqual([r["zone"] for r in lad], [1, 2, 3][:len(lad)])

    def test_a_band_price_sits_inside_reports_zero_not_a_rise(self):
        """ORCL: price $144.30 inside a $142-148 band, mid $145. Reporting
        the mid made a level underfoot read '+0.5%'."""
        lad = BZ.accumulation_ladder(
            {}, [self._band(142, 148, "8 / 21 EMA")], self._fund(), 144.30)
        self.assertTrue(lad[0]["inside"])
        self.assertEqual(lad[0]["distance_pct"], 0.0)

    def test_two_rungs_at_one_price_are_one_rung(self):
        lad = BZ.accumulation_ladder(
            self._pullback(shelf=200.0), [self._band(199, 201, "50 MA")],
            self._fund(), 220.0)
        self.assertEqual(len(lad), 1)

    def test_it_is_capped(self):
        lad = BZ.accumulation_ladder(
            self._pullback(shelf=210.0),
            [self._band(190, 195, "a"), self._band(170, 175, "b"),
             self._band(150, 155, "c")],
            self._fund(("fair", "Fair", 140.0, 12.0)), 220.0)
        self.assertLessEqual(len(lad), BZ.MAX_LADDER_RUNGS)

    def test_nothing_below_price_is_an_empty_ladder(self):
        self.assertEqual(
            BZ.accumulation_ladder({}, [self._band(300, 310, "x")],
                                   self._fund(), 200.0), [])
        self.assertEqual(BZ.accumulation_ladder({}, [], {}, None), [])

    def test_the_display_zone_falls_to_the_ladder_when_all_bands_are_over(self):
        lad = [{"zone": 1, "price": 541.32, "low": 541.32, "high": 541.32,
                "label": "180 touches", "source": "volume_shelf",
                "distance_pct": -0.4, "why": "tested support"}]
        z = BZ._display_zone([], [self._band(576, 589, "8 / 21 EMA")],
                             543.67, lad)
        self.assertEqual(z["kind"], "ladder")
        self.assertEqual(z["low"], 541.32)
        self.assertFalse(z["above_spot"])

    def test_a_reachable_band_still_wins_over_the_ladder(self):
        lad = [{"zone": 1, "price": 100.0, "low": 100.0, "high": 100.0,
                "label": "shelf", "source": "volume_shelf",
                "distance_pct": -50.0, "why": ""}]
        z = BZ._display_zone([], [self._band(190, 195, "50 MA")], 200.0, lad)
        self.assertEqual(z["kind"], "technical")

    def test_the_column_shows_the_rungs(self):
        row = {"price": 543.67, "investment": {}, "action": "WATCH",
               "buy_zones": {
                   "display_zone": {"low": 541.32, "high": 541.32,
                                    "kind": "ladder", "label": "180 touches",
                                    "source": "volume_shelf",
                                    "distance_pct": -0.4},
                   "ladder": [
                       {"zone": 1, "price": 541.32, "low": 541.32,
                        "high": 541.32, "distance_pct": -0.4},
                       {"zone": 2, "price": 489.65, "low": 489.65,
                        "high": 489.65, "distance_pct": -9.9},
                       {"zone": 3, "price": 395.80, "low": 395.80,
                        "high": 395.80, "distance_pct": -27.2}]}}
        html, _sort = V._buy_zone_cell(row)
        self.assertIn("Z1", html)
        self.assertIn("Z2", html)
        self.assertIn("Z3", html)
        self.assertIn("tested shelf", html)


class LadderCoverage(unittest.TestCase):
    """Three rungs where three exist, and a reason where they do not.

    Prior breakout and the 52-week low are real levels the band builder
    never turns into bands — a breakout only joins the 50 MA band when it
    happens to sit near it, and a 52-week low is not a moving average at
    all. Adding them took full three-rung coverage from 72% to 89%.

    The remainder are NOT padded. The scan row also carries ORB low,
    pre-market low and the previous day's low; all are real prices and none
    is a place to accumulate a long-term position, so a ladder reaching for
    them to make three would be inventing structure to fill a column.
    """

    def test_the_prior_breakout_becomes_a_rung(self):
        lad = BZ.accumulation_ladder(
            {}, [], {}, 92.52,
            row={"Prior_Breakout_Level": 84.75, "52W Low": 63.51})
        self.assertEqual([r["label"] for r in lad],
                         ["Prior breakout", "52-week low"])

    def test_structural_levels_above_price_are_not_rungs(self):
        lad = BZ.accumulation_ladder(
            {}, [], {}, 50.0,
            row={"Prior_Breakout_Level": 84.75, "52W Low": 63.51})
        self.assertEqual(lad, [])

    def test_a_short_ladder_says_why(self):
        """ORCL: on its 21 EMA, the 52-week low 20% below, nothing between,
        and a peer-multiple valuation that produces no cash-flow rungs."""
        note = BZ._ladder_note(
            [{"zone": 1}, {"zone": 2}],
            {"ladder": [], "blocked": "Expected return needs a cash-flow "
                                      "model."},
            {"buy_zone": {"price": None}})
        self.assertIn("only 2", note)
        self.assertIn("cash-flow", note)
        self.assertIn("no tested shelf", note)

    def test_a_full_ladder_needs_no_note(self):
        self.assertIsNone(BZ._ladder_note(
            [{"zone": 1}, {"zone": 2}, {"zone": 3}],
            {"ladder": [{"price": 1}]}, {"buy_zone": {"price": 10.0}}))

    def test_intraday_levels_are_never_borrowed(self):
        """The row carries them; the ladder must not reach for them."""
        lad = BZ.accumulation_ladder(
            {}, [], {}, 142.79,
            row={"ORB_Low": 142.37, "Pre-Market Low": 141.10,
                 "Prev-Day Low": 145.50, "VWAP": 143.31,
                 "52W Low": 114.50})
        self.assertEqual([r["label"] for r in lad], ["52-week low"])


class DcaColumn(unittest.TestCase):
    """The DCA read is ADDITIVE — the Action verdict is untouched.

    The user's constraint was explicit: do not change current behaviour,
    just say when price has reached an accumulation rung on a business good
    enough to add to. So this reports a separate fact beside the verdict.
    META reads "OWN / WAIT FOR TREND" and "Zone 1 · 31% off the high" at
    once, and both are true.

    All three conditions are asked together because none is a finding
    alone: 477 of 657 names sit on Zone 1 at any moment, that rung usually
    being the 8/21 EMA, and a routine pullback is not a correction.
    """

    def _row(self, quality=94, off=-31.0, zone=1, source="volume_shelf",
             action="OWN / WAIT FOR TREND"):
        return {
            "ticker": "META", "name": "Meta", "price": 543.67,
            "action": action, "icon": "🟡", "dist_52w_high": off,
            "quality": {"score": quality, "tier": "Elite"},
            "valuation": {"band": "FAIR", "band_icon": "", "band_label": "Fair"},
            "trend": {"state": "BROKEN", "icon": "", "summary": ""},
            "pullback": {"stage": "STAGE4_BREAKDOWN", "zone": "NONE"},
            "confluence": {}, "rs": {"strong": False, "score": 10},
            "regime": "SELECTIVE", "investment": {}, "lt_score": 18,
            "buy_zones": {
                "display_zone": {"low": 541.32, "high": 541.32,
                                 "kind": "ladder", "label": "180 touches"},
                "ladder": [{"zone": 1, "price": 541.32, "low": 541.32,
                            "high": 541.32, "distance_pct": -0.4}],
                "zone_hit": ({"zone": zone, "label": "180 touches",
                              "source": source, "inside": False,
                              "distance_pct": -0.4} if zone else None)},
        }

    def test_quality_in_a_correction_on_a_rung_fires(self):
        _hit, note = V._dca_read(self._row())
        self.assertIn("Zone 1", note)
        self.assertIn("DCA tranche", note)

    def test_a_routine_pullback_does_not(self):
        """NVDA sits on Zone 1 at -7% off its high; that is not a
        correction."""
        _hit, note = V._dca_read(self._row(off=-7.0))
        self.assertEqual(note, "")

    def test_a_lesser_business_does_not(self):
        """ORCL is 58% off its high and sits on a rung — at quality 84 the
        note stays silent."""
        _hit, note = V._dca_read(self._row(quality=84, off=-58.0))
        self.assertEqual(note, "")

    def test_not_on_a_rung_shows_nothing(self):
        html, sort = V._dca_cell(self._row(zone=None))
        self.assertIn("—", html)
        self.assertIsNone(sort)

    def test_the_zone_still_shows_without_the_dca_note(self):
        """The rung is a fact about price and is reported either way."""
        html, _sort = V._dca_cell(self._row(quality=84, off=-58.0))
        self.assertIn("Zone 1", html)
        self.assertNotIn("DCA", html)

    def test_eligible_rows_sort_above_ineligible_ones(self):
        _h, hot = V._dca_cell(self._row())
        _h2, cold = V._dca_cell(self._row(quality=84))
        self.assertGreater(hot, cold)

    def test_the_action_verdict_itself_is_unchanged(self):
        """The whole constraint. The note is appended; the verdict is not
        rewritten, reordered or suppressed."""
        row = self._row(action="OWN / WAIT FOR TREND")
        cells = V._cells(row)
        html, sort = cells["action"]
        self.assertIn("OWN / WAIT FOR TREND", html)
        self.assertEqual(sort, V._ACTION_RANK.get("OWN / WAIT FOR TREND"))
        # …and the DCA note rides underneath it.
        self.assertIn("DCA tranche", html)

    def test_no_note_leaves_the_action_cell_exactly_as_it_was(self):
        plain = V._cells(self._row(off=-7.0))["action"][0]
        self.assertNotIn("DCA", plain)

    def test_the_column_is_declared_with_a_sort_key(self):
        keys = [c[0] for c in V._COLUMNS]
        self.assertIn("dca", keys)
        self.assertIn("dca", V._cells(self._row()))
