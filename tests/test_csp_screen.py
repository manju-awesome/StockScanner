"""
Tests for filtering and sorting the /csp page.

The invariants that make this worth having rather than a second screener:

  1. One number per concept. A column sorts on the SAME scalar the rule
     engine filters on, so `adequacy >= 1.5` and a click on "vs req." can
     never disagree. Sorting on rendered text would rank "$9.10" above
     "$1,234.50".
  2. Missing is not failing. Most rows have no contract at all, and
     core.screener treats None as "no data" — so a premium rule excludes
     them without ever claiming their premium was inadequate.
  3. "Rich" means against the hurdle, not against a flat yield. A 40%
     annualised on a name whose hurdle is 35% is thinner than a 14% on a
     name whose hurdle is 9%, so the presets screen on adequacy.
  4. Enum values are derived from the modules that own them. A hand-copied
     delta band would go stale silently: an ENUM value matching nothing
     just returns no rows.
  5. Quality reads on slimmed rows too. Rejections are pruned on save, so
     the flattener has to find LQuality by either route or a rule would
     silently only screen the un-slimmed half of the table.

Run with: python -m unittest tests.test_csp_screen
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.csp import screen as CS
from stockanalysis.core.csp import strike as ST
from stockanalysis.webapp import csp_view as CV


def _row(ticker="TSM", lq=92, band="FAIR", adequacy=1.6, delta=-0.17,
         strike=380.0, price=415.0, action="SELL", contract=True):
    row = {
        "ticker": ticker, "name": f"{ticker} Inc.", "sector": "Technology",
        "price": price, "spot_source": "live",
        "eligibility": {"quality_score": lq, "quality_tier": "Elite",
                        "valuation_band": band},
        "discount": {"margin_pct": 12.5, "growth_gap_pp": -8.0},
        "final": {"key": action, "action": action},
        "adequacy": {"ratio": adequacy},
        "returns": {"yield_pct": 1.29, "annualised": 15.2},
        "assignment": {"basis": 375.1},
        "earnings_distance": {"days": 58, "inside": False},
        "move_cushion": {"ratio": 0.82},
        "downside": {"technical_cushion_pct": 3.0, "per_atr": 1.4},
        "iv_vs_hv": {"ratio": 0.86, "verdict": "GOOD"},
        "stock_score": 76, "option_score": 67, "risk_score": 68,
        "csp_score": 46,
    }
    if contract:
        row["chosen"] = {"strike": strike, "delta": delta, "dte": 31,
                         "limit_price": 4.90, "liquidity": 100,
                         "spread_pct": 2.0, "spread_verdict": "EXCELLENT",
                         "open_interest": 8956,
                         "delta_class": "Conservative/Moderate"}
    return row


class Flatten(unittest.TestCase):
    def test_delta_is_absolute(self):
        """"delta <= 0.20" is what anyone means, not "<= -0.20"."""
        self.assertAlmostEqual(CS.flatten(_row(delta=-0.17))["delta"], 0.17)

    def test_otm_is_derived_from_strike_and_spot(self):
        fl = CS.flatten(_row(strike=380.0, price=400.0))
        self.assertAlmostEqual(fl["otm_pct"], 5.0)

    def test_a_row_with_no_contract_flattens_to_none_not_zero(self):
        """(2) None screens as 'no data'; zero would screen as 'terrible'."""
        fl = CS.flatten(_row(contract=False))
        for key in ("strike", "delta", "dte", "credit", "otm_pct",
                    "liquidity", "spread_pct"):
            self.assertIsNone(fl[key], key)
        self.assertFalse(fl["has_contract"])

    def test_quality_is_found_on_a_slimmed_rejection(self):
        """(5) store.save() prunes rejected rows to a handful of keys."""
        slim = {"ticker": "CRDO", "final": {"key": "REJECT"},
                "lquality": 98.0, "lq_tier": "Elite", "price": 249.0}
        fl = CS.flatten(slim)
        self.assertEqual(fl["lquality"], 98.0)
        self.assertEqual(fl["lq_tier"], "Elite")
        self.assertEqual(fl["action"], "REJECT")

    def test_the_full_row_route_still_works(self):
        self.assertEqual(CS.flatten(_row(lq=92))["lquality"], 92)


class Rules(unittest.TestCase):
    def test_the_users_example_screens_both_halves(self):
        """(3) rich premium AND medium quality."""
        rows = [_row("RICH_MED", lq=78, adequacy=1.8),
                _row("RICH_ELITE", lq=95, adequacy=1.8),
                _row("THIN_MED", lq=78, adequacy=0.9)]
        kept, conds, _st = CS.apply_rules(
            rows, ["adequacy:gte:1.5", "lquality:between:70,85"], "AND")
        self.assertEqual([r["ticker"] for r in kept], ["RICH_MED"])
        self.assertEqual(len(conds), 2)

    def test_a_premium_rule_excludes_contractless_rows_as_no_data(self):
        """(2) It must not claim their premium was inadequate."""
        rows = [_row("HAS", adequacy=2.0), _row("NONE", contract=False)]
        rows[1].pop("adequacy")
        kept, _c, stats = CS.apply_rules(rows, ["adequacy:gte:1.5"], "AND")
        self.assertEqual([r["ticker"] for r in kept], ["HAS"])
        self.assertEqual(stats[0]["missing"], 1)

    def test_an_unparseable_rule_drops_itself(self):
        self.assertIsNone(CS.parse_rule("nonsense:gte:9"))
        self.assertIsNone(CS.parse_rule("adequacy:notanop:9"))
        self.assertIsNone(CS.parse_rule("adequacy:gte:notanumber"))
        self.assertIsNone(CS.parse_rule(""))

    def test_no_rules_keeps_everything(self):
        rows = [_row("A"), _row("B")]
        kept, conds, stats = CS.apply_rules(rows, [], "AND")
        self.assertEqual(len(kept), 2)
        self.assertEqual((conds, stats), ([], []))

    def test_or_is_honoured(self):
        rows = [_row("A", adequacy=2.0, lq=50), _row("B", adequacy=0.5, lq=95)]
        kept, _c, _s = CS.apply_rules(
            rows, ["adequacy:gte:1.5", "lquality:gte:90"], "OR")
        self.assertEqual(sorted(r["ticker"] for r in kept), ["A", "B"])

    def test_describe_reads_as_a_sentence(self):
        c = CS.parse_rule("adequacy:gte:1.5")
        self.assertEqual(CS.describe(c), "Premium vs required ≥ 1.5×")
        c2 = CS.parse_rule("earnings_inside:eq:true")
        self.assertIn("is yes", CS.describe(c2))


class Presets(unittest.TestCase):
    def test_every_preset_parses_and_is_grouped(self):
        for p in CS.PRESETS:
            self.assertIn(p["group"], CS.PRESET_GROUPS, p["key"])
            for rule in p["rules"]:
                self.assertIsNotNone(CS.parse_rule(rule), f'{p["key"]}: {rule}')

    def test_preset_keys_are_unique(self):
        keys = [p["key"] for p in CS.PRESETS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_named_preset_exists_and_screens_both_halves(self):
        p = next(x for x in CS.PRESETS if x["key"] == "rich_medium_quality")
        self.assertIn("adequacy:gte:1.5", p["rules"])
        self.assertTrue(any(r.startswith("lquality:") for r in p["rules"]))

    def test_counts_are_computed_not_recorded(self):
        rows = [_row("A", adequacy=2.0), _row("B", adequacy=0.5)]
        counts = CS.preset_counts(rows)
        self.assertEqual(counts["rich_premium"], 1)
        for p in CS.PRESETS:
            self.assertNotIn("count", p, p["key"])


class DerivedEnums(unittest.TestCase):
    def test_delta_classes_come_from_strike_py(self):
        """(4) A hand-copied list would fail silently."""
        self.assertEqual(CS.DELTA_CLASSES,
                         tuple(n for _c, n in ST.DELTA_CLASSES))

    def test_a_real_delta_class_is_a_valid_filter_value(self):
        self.assertIn(ST.classify_delta(-0.17), CS.DELTA_CLASSES)


class Sorting(unittest.TestCase):
    def test_one_sort_key_per_column(self):
        self.assertEqual(len(CV._SORT_KEYS), len(CV._COLUMNS))

    def test_every_sort_key_is_a_real_field(self):
        """Ticker is the identity column, not a screenable metric — every
        other sort key has to be a field the rule engine also knows, or
        the two would be describing different numbers."""
        for key in CV._SORT_KEYS:
            if key is None or key == "ticker":
                continue
            self.assertIn(key, CS.CSP_FIELD_BY_KEY, key)

    def test_a_column_sorts_on_the_value_the_rules_filter_on(self):
        """(1) The whole point."""
        row = _row(adequacy=1.6)
        vals = CV._sort_values(row)
        flat = CS.flatten(row)
        idx = list(CV._COLUMNS).index("vs req.")
        self.assertEqual(vals[idx], flat["adequacy"])

    def test_label_columns_sort_on_a_rank_not_the_label(self):
        """Otherwise a first click on Action leads with the rejections."""
        sell = CV._sort_values(_row(action="SELL"))
        reject = CV._sort_values(_row(action="REJECT"))
        idx = list(CV._COLUMNS).index("Action")
        self.assertGreater(sell[idx], reject[idx])

        under = CV._sort_values(_row(band="UNDERVALUED"))
        over = CV._sort_values(_row(band="OVERVALUED"))
        vidx = list(CV._COLUMNS).index("Valuation")
        self.assertGreater(under[vidx], over[vidx])

    def test_unsortable_columns_emit_no_value(self):
        vals = CV._sort_values(_row())
        for name in ("Class", "Expiry"):
            self.assertIsNone(vals[list(CV._COLUMNS).index(name)])

    def test_a_contractless_row_sorts_blank_not_zero(self):
        """Blanks sink; zero would rank above a real 0.5x premium."""
        row = _row(contract=False)
        row.pop("adequacy")
        vals = CV._sort_values(row)
        self.assertIsNone(vals[list(CV._COLUMNS).index("vs req.")])

    def test_the_table_marks_summary_rows_for_the_grouping_sorter(self):
        html = CV._table([_row()])
        self.assertIn('data-main="1"', html)
        self.assertIn('id="csp-table"', html)
        self.assertIn("data-sort=", html)


if __name__ == "__main__":
    unittest.main()


class RejectBuckets(unittest.TestCase):
    """One classifier, two callers.

    The page groups rejections by the rule that fired and the engine
    spreads its reference-chain budget across those same groups. Two
    definitions would allocate the budget to buckets the page then drew
    differently, with nothing on screen to reveal it.
    """

    def test_each_rule_lands_in_its_own_bucket(self):
        cases = {
            "LQuality 62 is below the 70 floor": "Quality below the floor",
            "quality built on 40% of inputs": "Quality below the floor",
            "overvalued — Price requires 80% growth a year":
                "Overvalued",
            "Stage 4 markdown — support levels do not hold here":
                "Stage 4 markdown",
            "long-term trend broken — the discount is the thesis repricing":
                "Trend broken",
            "no listed options": "No listed options",
        }
        for why, bucket in cases.items():
            self.assertEqual(CS.reject_bucket(why), bucket, why)
            self.assertIn(bucket, CS.REJECT_BUCKETS)

    def test_the_strike_stage_rejection_is_a_liquidity_bucket(self):
        """28 live names said this and were filed under 'Other', which
        described the most interesting rejections there are — good company,
        right strike found, only the quote in the way — as nothing."""
        why = ("the right strike exists ($120.00, delta 0.15) but is quoted "
               "84% wide — too wide to fill reliably")
        self.assertEqual(CS.reject_bucket(why), "Options too illiquid")

    def test_overvalued_wins_over_quality_when_both_words_appear(self):
        self.assertEqual(
            CS.reject_bucket("overvalued — quality is fine but the price"),
            "Overvalued")

    def test_an_unknown_reason_is_other_not_a_crash(self):
        self.assertEqual(CS.reject_bucket("something new"), "Other")
        self.assertEqual(CS.reject_bucket(""), "Other")
        self.assertEqual(CS.reject_bucket(None), "Other")


class ReferenceBudget(unittest.TestCase):
    """(the fix) Spread across buckets, not down the quality ranking."""

    def _cands(self):
        return ([(f"Q{i}", "LQuality 40 is below the 70 floor", 40)
                 for i in range(400)]
                + [(f"V{i}", "overvalued — too dear", 95) for i in range(90)]
                + [(f"S{i}", "Stage 4 markdown", 80) for i in range(25)]
                + [("T1", "long-term trend broken", 70)])

    def test_every_bucket_is_reached_even_on_a_small_budget(self):
        picked = CS.spread_reference_budget(self._cands(), 8)
        buckets = {CS.reject_bucket(w) for t, w, _q in self._cands()
                   if t in picked}
        self.assertEqual(len(buckets), 4)

    def test_the_largest_category_is_not_starved(self):
        """Pure quality order gave the 400-name bucket zero fetches."""
        picked = CS.spread_reference_budget(self._cands(), 25)
        got = [t for t in picked if t.startswith("Q")]
        self.assertTrue(got, "the biggest bucket got nothing")

    def test_best_business_first_within_a_bucket(self):
        cands = [("LOW", "overvalued", 70), ("HIGH", "overvalued", 99)]
        self.assertEqual(CS.spread_reference_budget(cands, 1), {"HIGH"})

    def test_the_budget_is_honoured(self):
        self.assertEqual(len(CS.spread_reference_budget(self._cands(), 40)), 40)
        self.assertEqual(CS.spread_reference_budget(self._cands(), 0), set())
        self.assertEqual(CS.spread_reference_budget([], 50), set())

    def test_a_budget_past_the_candidates_stops_rather_than_looping(self):
        cands = [("A", "overvalued", 90), ("B", "Stage 4 markdown", 80)]
        self.assertEqual(CS.spread_reference_budget(cands, 500), {"A", "B"})


class RejectedTableIsSortable(unittest.TestCase):
    """Sorting the rejected premiums, so "which pays most" is one click.

    One table with a Category COLUMN rather than a table per category:
    "which rejected names pay the most" is a question across all of them,
    and separate tables made it a manual comparison between sorted lists.
    Sorting on Category reproduces the grouping.
    """

    def _priced(self, ticker="CRDO", ann=126.4, lq=98,
                why="overvalued — Price requires 80% growth"):
        return {"ticker": ticker, "name": f"{ticker} Inc.",
                "sector": "Technology", "price": 249.26,
                "lquality": lq, "lq_tier": "Elite",
                "spot_source": "live", "spot_drift_pct": 0.1,
                "final": {"key": "REJECT", "action": "🔴 REJECT", "why": why},
                "reference": {
                    "available": True, "expiry": "2026-09-18", "dte": 31,
                    "quotes_live": True, "fillable": 8, "strikes": [{}],
                    "best": {"strike": 240.0, "delta": -0.386,
                             "limit_price": 25.76, "yield_pct": 10.73,
                             "annualised": ann, "breakeven": 214.24,
                             "basis_vs_spot_pct": -14.0, "distance_pct": -3.7,
                             "liquidity": 52, "spread_verdict": "EXCELLENT",
                             "expiry": "2026-09-18", "dte": 31,
                             "delta_class": "High assignment risk"}}}

    def test_it_is_one_sortable_table_with_its_own_id(self):
        html = CV._priced_rejects([self._priced()])
        self.assertIn('id="csp-rejects"', html)
        self.assertIn('data-sortable="1"', html)
        # Its own sort state and note — the two tables answer different
        # questions and must not move each other.
        self.assertIn('id="csp-rejects-sort-note"', html)

    def test_every_header_is_clickable(self):
        html = CV._priced_rejects([self._priced()])
        self.assertEqual(html.count('class="csp-sort"'),
                         len(CV._PRICED_REJECT_COLUMNS))

    def test_the_premium_columns_carry_numeric_sort_values(self):
        html = CV._priced_rejects([self._priced(ann=126.4)])
        for value in ("126.4", "10.73", "25.76", "240.0", "98"):
            self.assertIn(f'data-sort="{value}"', html)

    def test_category_is_a_column_so_grouping_is_a_sort(self):
        html = CV._priced_rejects([
            self._priced("CRDO", why="overvalued — too dear"),
            self._priced("XYZ", why="LQuality 40 is below the 70 floor")])
        self.assertIn('data-sort="Overvalued"', html)
        self.assertIn('data-sort="Quality below the floor"', html)

    def test_the_category_tally_still_shows_what_the_budget_reached(self):
        html = CV._priced_rejects([
            self._priced("A", why="overvalued — too dear"),
            self._priced("B", why="overvalued — also dear"),
            self._priced("C", why="Stage 4 markdown")])
        self.assertIn("Overvalued <b>2</b>", html)
        self.assertIn("Stage 4 markdown <b>1</b>", html)


class PerRowRescan(unittest.TestCase):
    """A named scan of one ticker — the strongest form the engine offers.

    Live spot, fresh chain, full audit kept instead of slimmed, and the
    reference chain attached even when the gate rejects. One name is one
    chain, which is what makes a per-row button reasonable at all.
    """

    def test_the_button_is_on_both_tables(self):
        row = {"ticker": "NVDA", "sector": "Technology"}
        html = CV._ticker_cell(row)
        self.assertIn('class="csp-rescan"', html)
        self.assertIn('data-ticker="NVDA"', html)

    def test_it_can_be_suppressed(self):
        html = CV._ticker_cell({"ticker": "NVDA"}, rescan=False)
        self.assertNotIn("csp-rescan", html)
        self.assertIn("NVDA", html)

    def test_the_click_does_not_also_open_the_detail_panel(self):
        """The row itself has an onclick that toggles the audit."""
        html = CV._ticker_cell({"ticker": "NVDA"})
        self.assertIn("event.stopPropagation()", html)

    def test_the_handler_asks_for_a_chain_whatever_the_verdict(self):
        """Rescanning a REJECTED row is pointless without its premiums."""
        _body, js = CV.csp_page({})
        self.assertIn("function cspRescan", js)
        self.assertIn("reference_rejected", js)
        self.assertIn("'limit', '1'", js)

    def test_the_sorter_finds_its_table_rather_than_assuming_one(self):
        _body, js = CV.csp_page({})
        self.assertIn("th.closest('table')", js)
        self.assertIn("table[data-sortable]", js)
        self.assertNotIn("getElementById('csp-table')", js)


class SortableTablesAreWiredUp(unittest.TestCase):
    """The rejected table rendered correct data-sort values and still would
    not sort, because no row carried `data-main` — and cspSortGroups()
    collects on exactly that. The group list came back empty, the sort
    became a silent no-op on every column, and the table only LOOKED
    sorted by Quality because the server already orders it
    best-business-first.

    The lesson these tests encode: a sortable table needs BOTH halves, and
    the failure mode of the missing half is silence.
    """

    def _priced(self, ticker="CRDO"):
        """A FULLY populated row on purpose: the assertion below is that a
        row with all its data leaves no column without a sort value, which
        is what catches a header wired to nothing."""
        return {"ticker": ticker, "name": f"{ticker} Inc.",
                "sector": "Technology", "price": 249.26,
                "lquality": 98, "lq_tier": "Elite", "spot_source": "live",
                "dist_52w_high": -21.1,
                "buy_zone": {"low": 168.0, "high": 177.0, "label": "200 MA",
                             "kind": "technical", "distance_pct": -29.1,
                             "state": "ABOVE", "near": False},
                "final": {"key": "REJECT", "why": "overvalued — too dear"},
                "reference": {
                    "available": True, "expiry": "2026-09-18", "dte": 31,
                    "quotes_live": True, "fillable": 8, "strikes": [{}],
                    "best": {"strike": 240.0, "delta": -0.386,
                             "limit_price": 25.76, "yield_pct": 10.73,
                             "annualised": 126.4, "breakeven": 214.24,
                             "adequacy": 6.15, "required_pct": 1.68,
                             "basis_vs_spot_pct": -14.0, "distance_pct": -3.7,
                             "liquidity": 52, "spread_verdict": "EXCELLENT",
                             "expiry": "2026-09-18", "dte": 31,
                             "delta_class": "High assignment risk"}}}

    def _rows(self, html):
        """(has_data_main, [data-sort per cell]) for each tbody row."""
        from html.parser import HTMLParser

        class P(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows, self.cur, self.inb = [], None, False

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "tbody":
                    self.inb = True
                elif tag == "tr" and self.inb:
                    self.cur = [a.get("data-main") is not None, []]
                    self.rows.append(self.cur)
                elif tag == "td" and self.cur is not None:
                    self.cur[1].append(a.get("data-sort"))

            def handle_endtag(self, tag):
                if tag == "tbody":
                    self.inb = False

        p = P()
        p.feed(html)
        return p.rows

    def test_every_reject_row_is_marked_for_the_grouper(self):
        rows = self._rows(CV._priced_rejects([self._priced("A"),
                                              self._priced("B")]))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(is_main for is_main, _cells in rows),
                        "a row without data-main is invisible to the sorter")

    def test_every_column_has_a_sort_value_to_sort_on(self):
        rows = self._rows(CV._priced_rejects([self._priced()]))
        cells = rows[0][1]
        self.assertEqual(len(cells), len(CV._PRICED_REJECT_COLUMNS))
        # A column whose value is blank on every row cannot be sorted, and
        # the header would still invite the click.
        self.assertTrue(all(c not in (None, "") for c in cells),
                        f"blank sort values: {cells}")

    def test_the_main_table_is_marked_too(self):
        html = CV._table([{"ticker": "NVDA", "final": {"key": "SELL"},
                           "eligibility": {}, "price": 100.0}])
        self.assertIn('data-main="1"', html)

    def test_both_tables_opt_in_to_the_sorter(self):
        body, _js = CV.csp_page({})
        self.assertGreaterEqual(body.count('data-sortable="1"'), 2)

    def test_an_unmarked_table_still_sorts_via_the_fallback(self):
        """Belt and braces: the grouper must never return an empty list for
        a table that has rows, because that failure is silent."""
        _body, js = CV.csp_page({})
        self.assertIn("if (!seen)", js)
        self.assertIn("body.rows", js)


class BuyZoneAndFiftyTwoWeek(unittest.TestCase):
    """The long-term context, carried onto the CSP row.

    The pairing these fields exist for: a put is a paid limit order, so
    "the strike I would be assigned at is inside the zone the company
    engine would buy in" is the strongest thing a cash-secured put can
    claim — and until these were on the row, the two halves of that
    sentence lived on different pages.

    `strike_in_zone` and `in_buy_zone` are deliberately DIFFERENT tests.
    APH is the live shape: price $158 outside its $142-146 zone, strike
    $145 inside it. Assignment is where you actually end up.
    """

    def _row(self, price=158.0, low=142.0, high=146.0, strike=145.0,
             kind="technical", near=False, dist_52w=-11.0):
        r = {"ticker": "APH", "price": price,
             "final": {"key": "VERIFY"},
             "eligibility": {"quality_score": 88},
             "dist_52w_high": dist_52w,
             "buy_zone": {"low": low, "high": high, "label": "50 MA",
                          "kind": kind, "distance_pct": -9.3,
                          "state": "ABOVE", "near": near,
                          "qualifies": kind == "investment"}}
        if strike is not None:
            r["chosen"] = {"strike": strike, "delta": -0.2, "dte": 31,
                           "limit_price": 2.21}
        return r

    def test_strike_in_zone_and_price_in_zone_are_different_questions(self):
        fl = CS.flatten(self._row())
        self.assertTrue(fl["strike_in_zone"])
        self.assertFalse(fl["in_buy_zone"])

    def test_both_are_true_when_price_is_also_inside(self):
        fl = CS.flatten(self._row(price=144.0))
        self.assertTrue(fl["strike_in_zone"])
        self.assertTrue(fl["in_buy_zone"])

    def test_no_zone_flattens_to_none_not_false(self):
        """None screens as 'no data'; False would claim it was measured."""
        r = self._row()
        r["buy_zone"] = {}
        fl = CS.flatten(r)
        for key in ("in_buy_zone", "strike_in_zone", "buy_zone_low",
                    "buy_zone_gap", "buy_zone_kind"):
            self.assertIsNone(fl[key], key)

    def test_a_row_with_no_strike_cannot_say_the_strike_is_in_the_zone(self):
        fl = CS.flatten(self._row(strike=None))
        self.assertIsNone(fl["strike_in_zone"])
        self.assertFalse(fl["in_buy_zone"])

    def test_the_zone_kind_is_carried(self):
        self.assertEqual(CS.flatten(self._row(kind="investment"))
                         ["buy_zone_kind"], "investment")

    def test_distance_off_the_high_is_carried(self):
        self.assertEqual(CS.flatten(self._row(dist_52w=-34.4))
                         ["dist_52w_high"], -34.4)

    def test_the_filters_the_user_asked_for_all_resolve(self):
        for rule in ("near_buy_zone:eq:true", "strike_in_zone:eq:true",
                     "in_buy_zone:eq:true", "dist_52w_high:lte:-20",
                     "buy_zone_kind:eq:investment", "adequacy:gte:1.5",
                     "buy_zone_low:gte:100"):
            self.assertIsNotNone(CS.parse_rule(rule), rule)


class RejectedRowsCarryTheirPremium(unittest.TestCase):
    """A rejection's premium lives in reference.best, not in `chosen`.

    Without the fallback, "rich premium" returned nothing over exactly the
    rows whose premiums are richest — the rejections — because the
    flattener only looked where a chosen contract would be.
    """

    def _rejected(self, ann=121.5, adequacy=6.15):
        return {"ticker": "CRDO", "price": 243.0,
                "final": {"key": "REJECT", "why": "overvalued — too dear"},
                "lquality": 98,
                "reference": {"available": True, "best": {
                    "strike": 240.0, "delta": -0.38, "dte": 31,
                    "limit_price": 24.76, "yield_pct": 10.32,
                    "annualised": ann, "adequacy": adequacy,
                    "required_pct": 1.68, "liquidity": 60,
                    "delta_class": "High assignment risk"}}}

    def test_the_premium_is_found(self):
        fl = CS.flatten(self._rejected())
        self.assertTrue(fl["is_reference"])
        self.assertEqual(fl["strike"], 240.0)
        self.assertEqual(fl["annualised"], 121.5)
        self.assertEqual(fl["credit"], 24.76)

    def test_adequacy_is_measured_against_the_same_hurdle(self):
        """So "rich" means one thing across the whole table."""
        self.assertEqual(CS.flatten(self._rejected())["adequacy"], 6.15)

    def test_a_rich_premium_rule_now_reaches_rejections(self):
        kept, _c, _s = CS.apply_rules([self._rejected()],
                                      ["adequacy:gte:2.0"], "AND")
        self.assertEqual([r["ticker"] for r in kept], ["CRDO"])

    def test_a_chosen_contract_is_never_overridden_by_a_reference(self):
        row = self._rejected()
        row["chosen"] = {"strike": 999.0, "delta": -0.1, "dte": 31,
                         "limit_price": 1.0}
        fl = CS.flatten(row)
        self.assertFalse(fl["is_reference"])
        self.assertEqual(fl["strike"], 999.0)

    def test_no_contract_and_no_reference_stays_blank(self):
        fl = CS.flatten({"ticker": "X", "final": {"key": "REJECT"}})
        self.assertFalse(fl["is_reference"])
        self.assertIsNone(fl["strike"])
        self.assertIsNone(fl["annualised"])


class ColumnsMatchTheirHeaders(unittest.TestCase):
    """Adding a column means adding a cell, a header AND a sort key.

    Caught a real mismatch: two cells were added to the header list and
    only one to the row, so every column after it rendered under the wrong
    heading — a table that looks fine and is silently misaligned.
    """

    def _cells(self, html):
        import re
        row = re.search(r'<tr data-main="1".*?</tr>', html, re.S)
        return row.group(0).count("<td") if row else 0

    def test_the_main_table_lines_up(self):
        html = CV._table([{"ticker": "NVDA", "final": {"key": "SELL"},
                           "eligibility": {}, "price": 100.0}])
        self.assertEqual(self._cells(html), len(CV._COLUMNS))
        self.assertEqual(len(CV._SORT_KEYS), len(CV._COLUMNS))

    def test_the_rejected_table_lines_up(self):
        row = {"ticker": "CRDO", "price": 249.0, "lquality": 98,
               "lq_tier": "Elite", "dist_52w_high": -21.1,
               "buy_zone": {"low": 168.0, "high": 177.0, "kind": "technical",
                            "label": "200 MA", "distance_pct": -29.1},
               "final": {"key": "REJECT", "why": "overvalued"},
               "reference": {"available": True, "quotes_live": True,
                             "fillable": 1, "strikes": [{}],
                             "best": {"strike": 240.0, "delta": -0.38,
                                      "limit_price": 25.76, "yield_pct": 10.7,
                                      "annualised": 126.4, "adequacy": 6.15,
                                      "required_pct": 1.68, "dte": 31,
                                      "breakeven": 214.24, "liquidity": 52,
                                      "basis_vs_spot_pct": -14.0,
                                      "distance_pct": -3.7,
                                      "expiry": "2026-09-18",
                                      "spread_verdict": "EXCELLENT",
                                      "delta_class": "Aggressive"}}}
        self.assertEqual(self._cells(CV._priced_rejects([row])),
                         len(CV._PRICED_REJECT_COLUMNS))


class WatchlistPicker(unittest.TestCase):
    """Run a CSP scan on a saved watchlist, or view only that list.

    The picker does the same double duty the ticker box does — Filter
    narrows the stored snapshot with no network, Run scans exactly that
    list — because most of the time the question is "what did the last scan
    say about my watchlist", and re-scanning to answer it is absurd.
    """

    def test_the_options_come_from_the_shared_watchlists(self):
        """The same lists the Scanner and /longterm run against, so a name
        means one set of tickers on all three pages."""
        with mock.patch("stockanalysis.webapp.api.longterm_lists",
                        return_value={"watchlist": ["A", "B"],
                                      "daytrade": ["C"]}):
            html = CV._watchlist_options()
        self.assertIn('value="watchlist"', html)
        self.assertIn("watchlist (2)", html)
        self.assertIn("daytrade (1)", html)

    def test_the_active_list_stays_selected(self):
        with mock.patch("stockanalysis.webapp.api.longterm_lists",
                        return_value={"AI": ["A"]}):
            html = CV._watchlist_options("AI")
        self.assertIn('value="AI" selected', html)

    def test_unreadable_watchlists_do_not_take_the_page_down(self):
        with mock.patch("stockanalysis.webapp.api.longterm_lists",
                        side_effect=OSError("gone")):
            html = CV._watchlist_options()
        self.assertIn("— watchlist —", html)

    def test_missing_members_are_named_not_dropped(self):
        """A 25-name list rendering 19 rows with no explanation reads as a
        bug; the fix is only obvious once it is said."""
        html = CV._list_note("AI", ["FOO", "BAR"])
        self.assertIn("AI", html)
        self.assertIn("2 of its tickers are not in the last scan", html)
        self.assertIn("FOO", html)
        self.assertIn("Run CSP scan", html)

    def test_a_fully_covered_list_says_so_quietly(self):
        html = CV._list_note("AI", [])
        self.assertIn("AI", html)
        self.assertNotIn("not in the last scan", html)

    def test_no_list_renders_nothing(self):
        self.assertEqual(CV._list_note("", []), "")

    def test_the_filter_button_preserves_the_rest_of_the_query(self):
        """Every control writes to one query string and none may drop
        another's state — filtering used to discard active rules."""
        _body, js = CV.csp_page({})
        self.assertIn("new URLSearchParams(window.location.search)", js)
        self.assertIn("select[name=list]", js)


class Tabs(unittest.TestCase):
    """Three lists, three tabs.

    They answer different questions — what to sell, what the funds pay,
    what was turned down and what it pays anyway — and stacked, the last
    sat below two summary cards and a hundred rows.
    """

    def _panes(self, html):
        import re
        return {m.group(1): "display:none" not in m.group(2)
                for m in re.finditer(
                    r'<div id="csp-pane-(\w+)" data-pane="\w+"([^>]*)>', html)}

    def test_opportunities_is_the_default(self):
        body, _js = CV.csp_page({})
        self.assertEqual([k for k, v in self._panes(body).items() if v],
                         ["live"])

    def test_each_tab_shows_exactly_its_own_pane(self):
        for key in ("live", "etf", "rejected"):
            body, _js = CV.csp_page({"tab": [key]})
            self.assertEqual([k for k, v in self._panes(body).items() if v],
                             [key], key)

    def test_an_unknown_tab_falls_back_rather_than_hiding_everything(self):
        """A pasted ?tab=nonsense left a header with nothing under it: the
        bar fell back for highlighting while every pane compared against
        the raw value and none matched."""
        body, _js = CV.csp_page({"tab": ["nonsense"]})
        visible = [k for k, v in self._panes(body).items() if v]
        self.assertEqual(visible, ["live"])

    def test_all_three_panes_are_rendered_so_the_swap_is_instant(self):
        body, _js = CV.csp_page({"tab": ["rejected"]})
        self.assertEqual(len(self._panes(body)), 3)

    def test_a_tab_is_a_link_carrying_the_whole_page_state(self):
        """Without JS it still navigates, and a view stays shareable."""
        import re
        body, _js = CV.csp_page({"tab": ["rejected"],
                                 "rule": ["adequacy:gte:2.0"],
                                 "list": ["watchlist"]})
        href = re.search(r'href="([^"]*)"\s+data-tab="etf"', body).group(1)
        self.assertIn("tab=etf", href)
        self.assertIn("list=watchlist", href)
        self.assertIn("rule=adequacy", href)

    def test_the_default_tab_is_not_written_into_the_url(self):
        import re
        body, _js = CV.csp_page({})
        href = re.search(r'href="([^"]*)"\s+data-tab="live"', body).group(1)
        self.assertNotIn("tab=", href)

    def test_the_click_handler_keeps_the_url_in_step(self):
        _body, js = CV.csp_page({})
        self.assertIn("function cspTab", js)
        self.assertIn("history.replaceState", js)
        # No panes on the page means the anchor must be allowed to navigate.
        self.assertIn("return true", js)


class FiltersReachTheRejectedTab(unittest.TestCase):
    """The rules narrow every tab — it is one row set split by verdict.

    That is not obvious from a card reached through a tab, and a reader who
    does not know it will read a filtered list as the whole one.
    """

    def test_the_rejected_card_states_what_the_filters_did(self):
        body, _js = CV.csp_page({"tab": ["rejected"],
                                 "rule": ["adequacy:gte:2.0"]})
        self.assertIn("Filtered by", body)
        self.assertIn("Premium vs required", body)
        self.assertIn("across every tab", body)

    def test_no_note_without_rules(self):
        self.assertEqual(CV._reject_filter_note([], 10, 10), "")

    def test_the_bar_says_the_filter_spans_the_tabs(self):
        body, _js = CV.csp_page({"rule": ["adequacy:gte:2.0"]})
        self.assertIn("across all three tabs", body)

    def test_a_rule_actually_narrows_the_rejected_table(self):
        import re

        def rows(rules):
            body, _js = CV.csp_page({"tab": ["rejected"], "rule": rules})
            t = re.search(r'<table id="csp-rejects".*?</table>', body, re.S)
            return len(re.findall(r'<tr data-main="1"', t.group(0))) if t else 0

        wide = rows([])
        narrow = rows(["annualised:gte:100"])
        self.assertGreater(wide, narrow)
        self.assertGreater(narrow, 0)


class QuickFilters(unittest.TestCase):
    """Numeric thresholds in one gesture.

    The full builder can express all of these, but it is a three-control
    sequence sitting above the tabs and "quality over 90 paying over 100%
    annualised" is two numbers. These write into the SAME query string, so
    a quick filter and a built rule are one object — same pill, same
    sentence, same removal.
    """

    def _boxes(self, html):
        import re
        return {m.group(1): m.group(3) for m in re.finditer(
            r'data-field="(\w+)" data-op="(\w+)" type="number" step="any" '
            r'value="([^"]*)"', html)}

    def test_every_declared_filter_gets_a_box(self):
        body, _js = CV.csp_page({})
        self.assertEqual(len(self._boxes(body)), len(CV._QUICK_FILTERS))

    def test_every_quick_field_is_a_real_screenable_field(self):
        for key, op, _label, _unit, _w, _hint in CV._QUICK_FILTERS:
            self.assertIn(key, CS.CSP_FIELD_BY_KEY, key)
            spec = CS.CSP_FIELD_BY_KEY[key]
            self.assertIn(op, CS.S.OPS_FOR_KIND[spec.kind], f"{key}:{op}")

    def test_the_users_example_prefills(self):
        body, _js = CV.csp_page({"rule": ["lquality:gte:90",
                                          "annualised:gte:100"]})
        b = self._boxes(body)
        self.assertEqual(b["lquality"], "90")
        self.assertEqual(b["annualised"], "100")

    def test_prefill_matches_on_field_not_on_operator(self):
        """A preset writes `gt` and this row writes `gte`; keying on the
        pair left the box blank beside an active rule, and Apply then added
        a SECOND rule on the same field."""
        body, _js = CV.csp_page({"rule": ["lquality:gt:90"]})
        self.assertEqual(self._boxes(body)["lquality"], "90")

    def test_untouched_boxes_stay_empty(self):
        body, _js = CV.csp_page({"rule": ["lquality:gte:90"]})
        self.assertEqual(self._boxes(body)["delta"], "")

    def test_a_float_does_not_grow_a_trailing_zero(self):
        body, _js = CV.csp_page({"rule": ["adequacy:gte:1.5"]})
        self.assertEqual(self._boxes(body)["adequacy"], "1.5")
        body, _js = CV.csp_page({"rule": ["lquality:gte:90"]})
        self.assertEqual(self._boxes(body)["lquality"], "90")

    def test_apply_owns_its_fields_and_leaves_the_rest_alone(self):
        _body, js = CV.csp_page({})
        self.assertIn("function cspQuick", js)
        self.assertIn("mine[r.split(':')[0]]", js)
        self.assertIn("function cspQuickClear", js)

    def test_the_named_preset_exists_and_matches_the_example(self):
        p = next((x for x in CS.PRESETS
                  if x["key"] == "elite_rich_reject"), None)
        self.assertIsNotNone(p)
        self.assertIn("lquality:gt:90", p["rules"])
        self.assertIn("annualised:gt:100", p["rules"])
        for rule in p["rules"]:
            self.assertIsNotNone(CS.parse_rule(rule), rule)


class StrikeAtOrBelowTheZone(unittest.TestCase):
    """`strike_in_zone` missed the better case entirely.

    It required the strike to land INSIDE the band. A strike BELOW the zone
    puts you in cheaper than the engine's own buy price, which is strictly
    better — and read False. APP is the live shape: strike $300 against a
    $320-332 zone, 9.6% under the top, and it did not match the screen it
    most obviously belongs to.
    """

    def _row(self, strike, low=320.0, high=332.0, price=307.26):
        return {"ticker": "APP", "price": price,
                "final": {"key": "REJECT"},
                "eligibility": {"quality_score": 96},
                "chosen": {"strike": strike, "delta": -0.2, "dte": 31,
                           "limit_price": 5.0},
                "buy_zone": {"low": low, "high": high, "kind": "technical",
                             "label": "8 / 21 EMA", "distance_pct": 6.1}}

    def test_a_strike_below_the_zone_qualifies(self):
        f = CS.flatten(self._row(300.0))
        self.assertTrue(f["strike_at_or_below_zone"])
        self.assertFalse(f["strike_in_zone"])       # the old, narrower test

    def test_a_strike_inside_the_zone_also_qualifies(self):
        f = CS.flatten(self._row(325.0))
        self.assertTrue(f["strike_at_or_below_zone"])
        self.assertTrue(f["strike_in_zone"])

    def test_a_strike_above_the_zone_does_not(self):
        f = CS.flatten(self._row(400.0))
        self.assertFalse(f["strike_at_or_below_zone"])
        self.assertFalse(f["strike_in_zone"])

    def test_the_number_and_the_flag_cannot_disagree(self):
        """Signed against the TOP, so '<= 0' is exactly the flag."""
        for strike in (250.0, 300.0, 332.0, 333.0, 400.0):
            f = CS.flatten(self._row(strike))
            self.assertEqual(f["strike_at_or_below_zone"],
                             f["strike_vs_zone_pct"] <= 0, strike)

    def test_the_number_matches_the_live_reading(self):
        f = CS.flatten(self._row(300.0))
        self.assertAlmostEqual(f["strike_vs_zone_pct"], -9.64, places=1)

    def test_no_zone_or_no_strike_is_unmeasured(self):
        r = self._row(300.0)
        r["buy_zone"] = {}
        self.assertIsNone(CS.flatten(r)["strike_at_or_below_zone"])
        r2 = self._row(300.0)
        r2.pop("chosen")
        self.assertIsNone(CS.flatten(r2)["strike_at_or_below_zone"])

    def test_the_preset_screens_on_the_wider_test(self):
        p = next(x for x in CS.PRESETS if x["key"] == "strike_at_zone")
        self.assertEqual(p["rules"], ["strike_at_or_below_zone:eq:true"])
        kept, _c, _s = CS.apply_rules([self._row(300.0)], p["rules"], "AND")
        self.assertEqual([r["ticker"] for r in kept], ["APP"])

    def test_the_cell_calls_out_a_strike_under_the_zone(self):
        html = CV._buy_zone_cell(self._row(300.0), 300.0)
        self.assertIn("under the zone", html)
        self.assertIn("#0F6E56", html)              # green: it is the good case

    def test_the_cell_still_distinguishes_inside_from_under(self):
        self.assertIn("strike is inside",
                      CV._buy_zone_cell(self._row(325.0), 325.0))

    def test_it_is_reachable_from_the_quick_filter_row(self):
        keys = {k for k, _op, _l, _u, _w, _h in CV._QUICK_FILTERS}
        self.assertIn("strike_vs_zone_pct", keys)


class RulePillsUseTheRightFieldSet(unittest.TestCase):
    """A rule pill read `adequacy` instead of "Premium vs required ≥ 2×".

    `_rule_builder` renders its own copy of the active-rule chips, and that
    internal call did not pass `mod` — so it fell back to the long-term
    field set, which has no `adequacy`, and `describe()` printed the raw
    key. The CSP page then showed each rule TWICE, once correctly from its
    own call and once wrong from the builder's, which is exactly the shape
    a duplicated render produces: two sources, one of them stale.
    """

    def _pills(self, html, page):
        import re
        return [re.sub(r"<[^>]+>", "", m.group(1)).strip()
                for m in re.finditer(
                    r'border-radius:6px">([^<]*)<a href="' + page + r'\?rule=',
                    html)]

    def test_a_csp_only_field_is_named_not_keyed(self):
        body, _js = CV.csp_page({"rule": ["action:eq:REJECT",
                                          "adequacy:gte:2"]})
        pills = self._pills(body, "/csp")
        self.assertIn("Premium vs required ≥ 2.0×", pills)
        self.assertNotIn("adequacy", pills)

    def test_each_rule_appears_exactly_once(self):
        body, _js = CV.csp_page({"rule": ["action:eq:REJECT",
                                          "adequacy:gte:2"]})
        self.assertEqual(len(self._pills(body, "/csp")), 2)

    def test_every_csp_field_describes_without_falling_back(self):
        """The bug was invisible for `action`, which both field sets have.
        Only a CSP-only key exposed it, so check them all."""
        for key, spec in CS.CSP_FIELD_BY_KEY.items():
            op = "eq" if spec.kind != CS.NUM else "gte"
            value = (spec.values[0] if spec.kind == CS.ENUM
                     else "true" if spec.kind == CS.BOOL else "1")
            cond = CS.parse_rule(f"{key}:{op}:{value}")
            self.assertIsNotNone(cond, key)
            text = CS.describe(cond)
            self.assertIn(spec.label, text, key)
            self.assertNotEqual(text.split()[0], key, key)

    def test_longterm_pills_are_unaffected(self):
        from stockanalysis.webapp import longterm_view as LV
        body, _js = LV.longterm_page({"rule": ["lquality:gte:85",
                                               "trend_state:ne:BROKEN"]})
        pills = self._pills(body, "/longterm")
        self.assertIn("LQuality ≥ 85", pills)
        self.assertIn("Trend state is not BROKEN", pills)
        self.assertEqual(len(pills), 2)


class QuickToggles(unittest.TestCase):
    """Yes/no conditions in one click, composable with the number boxes.

    A number box cannot express "strike at or below the zone" without the
    reader knowing the sign convention, and the builder can but takes three
    controls. These are links carrying the whole query string, so they
    compose with the numeric row, survive without JavaScript, and every
    combination is a shareable URL.
    """

    def test_every_toggle_names_a_real_field_and_a_valid_value(self):
        for field, op, value, _label, _title in CV._QUICK_TOGGLES:
            spec = CS.CSP_FIELD_BY_KEY.get(field)
            self.assertIsNotNone(spec, field)
            self.assertIn(op, CS.S.OPS_FOR_KIND[spec.kind], f"{field}:{op}")
            cond = CS.parse_rule(f"{field}:{op}:{value}")
            self.assertIsNotNone(cond, f"{field}:{op}:{value}")
            if spec.kind == CS.ENUM:
                self.assertIn(value, spec.values, field)

    def test_all_of_them_render(self):
        body, _js = CV.csp_page({})
        for _f, _op, _v, label, _t in CV._QUICK_TOGGLES:
            self.assertIn(label, body)

    def _toggle_href(self, body, label):
        """The chip's own href.

        Matched on the LABEL: a preset pill can carry near-identical prose,
        and the two behave differently on purpose — a preset REPLACES the
        rule set, a toggle adds to it — so a matcher that cannot tell them
        apart is testing neither.
        """
        import re
        m = re.search(r'<a href="([^"]*)"[^>]*>' + re.escape(label) + r'</a>',
                      body)
        self.assertIsNotNone(m, f"no toggle chip labelled {label!r}")
        return m.group(1).replace("&amp;", "&")

    def test_an_inactive_toggle_adds_its_rule(self):
        href = self._toggle_href(CV.csp_page({})[0], "🎯 Strike at/below zone")
        self.assertIn("strike_at_or_below_zone", href)

    def test_an_active_toggle_removes_its_own_rule(self):
        body, _js = CV.csp_page({"rule": ["strike_at_or_below_zone:eq:true"]})
        href = self._toggle_href(body, "🎯 Strike at/below zone")
        self.assertNotIn("strike_at_or_below_zone", href)
        self.assertIn("#0C447C", body)          # and it is highlighted

    def test_a_toggle_keeps_the_other_rules(self):
        """A toggle composes; a preset replaces. This is the difference."""
        body, _js = CV.csp_page({"rule": ["annualised:gte:70"]})
        href = self._toggle_href(body, "🎯 Strike at/below zone")
        self.assertIn("annualised", href)
        self.assertIn("strike_at_or_below_zone", href)

    def test_the_threshold_is_the_users_not_a_hardcoded_one(self):
        """The point of the numeric row: 70, 90 and 100 are all reachable
        without editing a preset."""
        import re

        def n(rules):
            body, _js = CV.csp_page({"tab": ["rejected"], "rule": rules})
            t = re.search(r'<table id="csp-rejects".*?</table>', body, re.S)
            return len(re.findall(r'<tr data-main="1"', t.group(0))) if t else 0

        wide = n(["annualised:gte:70"])
        mid = n(["annualised:gte:90"])
        tight = n(["annualised:gte:100"])
        self.assertGreaterEqual(wide, mid)
        self.assertGreaterEqual(mid, tight)
        self.assertGreater(wide, 0)

    def test_a_toggle_and_a_threshold_compose(self):
        kept, conds, _st = CS.apply_rules(
            [{"ticker": "A", "final": {"key": "REJECT"},
              "price": 100.0,
              "chosen": {"strike": 90.0, "dte": 31, "limit_price": 5.0},
              "buy_zone": {"low": 95.0, "high": 99.0, "kind": "technical"},
              "returns": {"annualised": 120.0}}],
            ["annualised:gte:70", "strike_at_or_below_zone:eq:true"], "AND")
        self.assertEqual(len(conds), 2)
        self.assertEqual([r["ticker"] for r in kept], ["A"])


class OverheadZoneIsNotAWin(unittest.TestCase):
    """"Strike under the zone" is trivially true when the whole band sits
    above spot, and would rank a collapsed stock top of a screen for cheap
    assignments. INTU: down 51.6%, through its 200 MA, band 28% overhead."""

    def _row(self, above):
        return {"ticker": "INTU", "price": 350.41,
                "final": {"key": "REJECT"},
                "eligibility": {"quality_score": 86},
                "chosen": {"strike": 350.0, "delta": -0.45, "dte": 31,
                           "limit_price": 24.16},
                "buy_zone": {"low": 442.0, "high": 453.0,
                             "kind": "investment", "label": "Preferred",
                             "distance_pct": 29.3, "above_spot": above}}

    def test_the_strike_flags_report_not_applicable(self):
        f = CS.flatten(self._row(above=True))
        self.assertIsNone(f["strike_at_or_below_zone"])
        self.assertIsNone(f["strike_in_zone"])
        self.assertTrue(f["zone_above_spot"])

    def test_a_normal_zone_still_answers(self):
        f = CS.flatten(self._row(above=False))
        self.assertTrue(f["strike_at_or_below_zone"])
        self.assertFalse(f["zone_above_spot"])

    def test_it_does_not_match_the_cheap_assignment_screen(self):
        p = next(x for x in CS.PRESETS if x["key"] == "strike_at_zone")
        kept, _c, _s = CS.apply_rules([self._row(above=True)],
                                      p["rules"], "AND")
        self.assertEqual(kept, [])

    def test_the_overhead_case_is_screenable_in_its_own_right(self):
        kept, _c, _s = CS.apply_rules([self._row(above=True)],
                                      ["zone_above_spot:eq:true"], "AND")
        self.assertEqual([r["ticker"] for r in kept], ["INTU"])

    def test_the_cell_says_the_price_is_below_the_band(self):
        html = CV._buy_zone_cell(self._row(above=True), 350.0)
        self.assertIn("price is below the whole band", html)
        self.assertNotIn("under the zone", html)
