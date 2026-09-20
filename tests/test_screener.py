"""
Tests for core.screener — the Screener page's query engine: operators,
nested AND/OR/NOT, match scoring, missing-data accounting, the natural
language parser, and the presets. Pure functions, no network.
Run with: python -m unittest tests.test_screener
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import screener as S
from stockanalysis.core.screener import Condition, Group


def _row(**kw):
    """A screenable row straight out of build_universe(), all fields present
    so a test only has to state what it cares about."""
    base = {
        "ticker": "TEST", "name": "Test Co", "sector": "Technology",
        "quality": 90, "health": 80, "moat": 3, "moat_label": "Strong signals",
        "rs_rank": 85, "eps_growth": 30.0, "forward_pe": 20.0,
        "inst_own": 75.0, "market_cap": 5e10, "category": "Momentum",
        "above_200ma": True, "above_50ma": True, "abs_vs_8ema": 0.5,
        "pct_vs_8ema": 0.5, "abs_vs_50ma": 4.0, "in_buy_zone": False,
        "is_turnaround": False, "is_momentum": True, "canslim": True,
        "conv_stars": 4, "conviction": 78, "rr": 3.2, "breakout_probability": 85.0,
        "swing_score": 80, "rsi": 55.0,
    }
    base.update(kw)
    return base


def _c(field, op, value, **kw):
    return Condition(field=field, op=op, value=value, **kw)


class TestOperators(unittest.TestCase):
    def test_numeric_comparisons(self):
        row = _row(quality=90)
        for op, value, expected in (("gt", 89, True), ("gt", 90, False),
                                    ("gte", 90, True), ("lt", 91, True),
                                    ("lte", 90, True), ("eq", 90, True),
                                    ("ne", 90, False)):
            with self.subTest(op=op):
                self.assertIs(S.eval_condition(row, _c("quality", op, value)).passed,
                              expected)

    def test_between_is_inclusive_and_order_agnostic(self):
        row = _row(rsi=55.0)
        self.assertTrue(S.eval_condition(
            row, Condition("rsi", "between", 40, 60)).passed)
        # bounds given backwards still describe the same band
        self.assertTrue(S.eval_condition(
            row, Condition("rsi", "between", 60, 40)).passed)
        self.assertFalse(S.eval_condition(
            row, Condition("rsi", "between", 20, 40)).passed)

    def test_within_uses_absolute_distance(self):
        # 0.6% below the 8 EMA is within 1% of it
        self.assertTrue(S.eval_condition(
            _row(abs_vs_8ema=0.6), _c("abs_vs_8ema", "within", 1)).passed)
        self.assertFalse(S.eval_condition(
            _row(abs_vs_8ema=2.4), _c("abs_vs_8ema", "within", 1)).passed)

    def test_boolean_field_matches_requested_state(self):
        self.assertTrue(S.eval_condition(
            _row(above_200ma=True), _c("above_200ma", "eq", True)).passed)
        self.assertFalse(S.eval_condition(
            _row(above_200ma=False), _c("above_200ma", "eq", True)).passed)
        self.assertTrue(S.eval_condition(
            _row(above_200ma=False), _c("above_200ma", "eq", False)).passed)

    def test_enum_equality_and_membership(self):
        row = _row(category="Turnaround")
        self.assertTrue(S.eval_condition(row, _c("category", "eq", "Turnaround")).passed)
        self.assertFalse(S.eval_condition(row, _c("category", "eq", "Momentum")).passed)
        self.assertTrue(S.eval_condition(row, _c("category", "ne", "Avoid")).passed)
        self.assertTrue(S.eval_condition(
            row, _c("category", "in", ["Turnaround", "VCP Setup"])).passed)
        self.assertFalse(S.eval_condition(
            row, _c("category", "in", ["Momentum"])).passed)

    def test_negate_inverts_a_passing_condition(self):
        row = _row(quality=90)
        self.assertFalse(S.eval_condition(
            row, _c("quality", "gt", 80, negate=True)).passed)
        self.assertTrue(S.eval_condition(
            row, _c("quality", "gt", 95, negate=True)).passed)


class TestMissingData(unittest.TestCase):
    def test_missing_value_fails_and_is_flagged(self):
        r = S.eval_condition(_row(quality=None), _c("quality", "gt", 50))
        self.assertFalse(r.passed)
        self.assertTrue(r.missing)

    def test_negated_condition_on_missing_data_still_fails(self):
        # "NOT quality > 50" must not sweep in every row that simply has no
        # quality score — unknown is not the same as "doesn't exceed".
        r = S.eval_condition(_row(quality=None),
                             _c("quality", "gt", 50, negate=True))
        self.assertFalse(r.passed)
        self.assertTrue(r.missing)

    def test_nan_counts_as_missing_not_as_a_value(self):
        r = S.eval_condition(_row(quality=float("nan")), _c("quality", "gt", 50))
        self.assertTrue(r.missing)

    def test_screen_counts_missing_rows_per_field(self):
        rows = [_row(ticker="A", breakout_probability=None),
                _row(ticker="B", breakout_probability=None),
                _row(ticker="C", breakout_probability=90.0)]
        res = S.screen(rows, Group("AND", [_c("breakout_probability", "gt", 80)]))
        self.assertEqual(res.summary["count"], 1)
        self.assertEqual(res.missing_counts["breakout_probability"], 2)

    def test_condition_with_no_threshold_is_missing_not_matching(self):
        r = S.eval_condition(_row(), _c("quality", "gt", None))
        self.assertFalse(r.passed)
        self.assertTrue(r.missing)


class TestBooleanLogic(unittest.TestCase):
    def test_and_requires_every_condition(self):
        g = Group("AND", [_c("quality", "gt", 80), _c("rs_rank", "gt", 90)])
        self.assertFalse(S.eval_group(_row(quality=90, rs_rank=85), g))
        self.assertTrue(S.eval_group(_row(quality=90, rs_rank=95), g))

    def test_or_requires_only_one(self):
        g = Group("OR", [_c("quality", "gt", 95), _c("rs_rank", "gt", 80)])
        self.assertTrue(S.eval_group(_row(quality=50, rs_rank=85), g))
        self.assertFalse(S.eval_group(_row(quality=50, rs_rank=50), g))

    def test_nested_groups(self):
        # quality > 80 AND (turnaround OR momentum)
        g = Group("AND", [
            _c("quality", "gt", 80),
            Group("OR", [_c("is_turnaround", "eq", True),
                         _c("is_momentum", "eq", True)]),
        ])
        self.assertTrue(S.eval_group(
            _row(quality=90, is_turnaround=False, is_momentum=True), g))
        self.assertFalse(S.eval_group(
            _row(quality=90, is_turnaround=False, is_momentum=False), g))
        self.assertFalse(S.eval_group(
            _row(quality=50, is_turnaround=True, is_momentum=True), g))

    def test_negated_group(self):
        g = Group("AND", [_c("quality", "gt", 80)], negate=True)
        self.assertFalse(S.eval_group(_row(quality=90), g))
        self.assertTrue(S.eval_group(_row(quality=50), g))

    def test_empty_screen_matches_everything(self):
        self.assertTrue(S.eval_group(_row(), Group("AND", [])))


class TestScoring(unittest.TestCase):
    def test_clearing_by_more_scores_higher(self):
        cond = _c("quality", "gt", 90)
        near = S.match_score([S.eval_condition(_row(quality=91), cond)])
        far = S.match_score([S.eval_condition(_row(quality=100), cond)])
        self.assertGreater(far, near)

    def test_all_failing_scores_zero(self):
        r = S.eval_condition(_row(quality=10), _c("quality", "gt", 90))
        self.assertEqual(S.match_score([r]), 0)

    def test_weights_shift_the_score(self):
        results = [S.eval_condition(_row(quality=100), _c("quality", "gt", 90)),
                   S.eval_condition(_row(rs_rank=10), _c("rs_rank", "gt", 90))]
        heavy_quality = S.match_score(results, {"quality": 9.0, "rs_rank": 1.0})
        heavy_rs = S.match_score(results, {"quality": 1.0, "rs_rank": 9.0})
        self.assertGreater(heavy_quality, heavy_rs)

    def test_composite_respects_direction(self):
        # forward_pe is lower-is-better, so the cheaper name must score higher
        cheap = S.composite_score(_row(forward_pe=10.0), {"forward_pe": 100})
        rich = S.composite_score(_row(forward_pe=50.0), {"forward_pe": 100})
        self.assertGreater(cheap, rich)

    def test_composite_ignores_fields_with_no_data(self):
        # a missing weighted field must not drag the blend toward zero
        both = S.composite_score(_row(quality=90, rs_rank=90),
                                 {"quality": 50, "rs_rank": 50})
        one = S.composite_score(_row(quality=90, rs_rank=None),
                                {"quality": 50, "rs_rank": 50})
        self.assertEqual(both, one)

    def test_composite_is_none_when_nothing_is_scorable(self):
        self.assertIsNone(S.composite_score(_row(quality=None), {"quality": 100}))


class TestScreen(unittest.TestCase):
    def _universe(self):
        return [_row(ticker="AAA", quality=100, rs_rank=95),
                _row(ticker="BBB", quality=92, rs_rank=91),
                _row(ticker="CCC", quality=50, rs_rank=40)]

    def test_sorts_best_match_first(self):
        res = S.screen(self._universe(),
                       Group("AND", [_c("quality", "gt", 90)]))
        self.assertEqual([m["ticker"] for m in res.matches], ["AAA", "BBB"])
        self.assertEqual(res.total, 3)

    def test_limit_does_not_change_the_reported_count(self):
        # the summary counts the screen; the list is just the page shown
        res = S.screen(self._universe(),
                       Group("AND", [_c("quality", "gt", 40)]), limit=1)
        self.assertEqual(len(res.matches), 1)
        self.assertEqual(res.summary["count"], 3)

    def test_every_match_explains_itself(self):
        res = S.screen(self._universe(),
                       Group("AND", [_c("quality", "gt", 90)]))
        why = res.matches[0]["why"]
        self.assertTrue(all(w["passed"] for w in why))
        self.assertIn("Quality", why[0]["text"])
        self.assertIn("100", why[0]["text"])

    def test_summary_averages_only_over_matches(self):
        res = S.screen(self._universe(),
                       Group("AND", [_c("quality", "gt", 90)]))
        self.assertEqual(res.summary["count"], 2)
        self.assertEqual(res.summary["avg_quality"], 96.0)

    def test_sort_by_ticker_is_ascending(self):
        res = S.screen(self._universe(),
                       Group("AND", [_c("quality", "gt", 40)]), sort="ticker")
        self.assertEqual([m["ticker"] for m in res.matches],
                         ["AAA", "BBB", "CCC"])

    def test_rows_with_no_value_sort_last_not_first(self):
        rows = [_row(ticker="HAS", conviction=10),
                _row(ticker="NONE", conviction=None)]
        res = S.screen(rows, Group("AND", []), sort="conviction")
        self.assertEqual([m["ticker"] for m in res.matches], ["HAS", "NONE"])


class TestConditionStats(unittest.TestCase):
    def test_reports_what_each_rule_costs(self):
        rows = [_row(ticker="A", quality=95, rs_rank=50),
                _row(ticker="B", quality=95, rs_rank=99),
                _row(ticker="C", quality=10, rs_rank=99)]
        res = S.screen(rows, Group("AND", [_c("quality", "gt", 90),
                                           _c("rs_rank", "gt", 90)]))
        self.assertEqual(res.summary["count"], 1)
        by_field = {s["field"]: s for s in res.stats}
        self.assertEqual(by_field["quality"]["alone"], 2)
        self.assertEqual(by_field["rs_rank"]["alone"], 2)
        # dropping the RS rule leaves the two quality names
        self.assertEqual(by_field["rs_rank"]["without"], 2)

    def test_drops_the_right_rule_when_two_are_identical(self):
        # two value-identical conditions: removing one must leave the other
        rows = [_row(ticker="A", quality=95)]
        c1, c2 = _c("quality", "gt", 90), _c("quality", "gt", 90)
        res = S.screen(rows, Group("AND", [c1, c2]))
        self.assertEqual(len(res.stats), 2)
        self.assertTrue(all(s["without"] == 1 for s in res.stats))


class TestNaturalLanguage(unittest.TestCase):
    def _parsed(self, text):
        return {(c.field, c.op, c.value) for c in S.parse_query(text)}

    def test_price_near_an_ema_defaults_to_one_percent(self):
        self.assertEqual(self._parsed("price near 8 ema"),
                         {("abs_vs_8ema", "within", 1.0)})

    def test_explicit_tolerance_is_not_confused_with_the_ema_number(self):
        self.assertEqual(self._parsed("price within 2% of 8 ema"),
                         {("abs_vs_8ema", "within", 2.0)})
        self.assertEqual(self._parsed("price within 3% of 50ma"),
                         {("abs_vs_50ma", "within", 3.0)})

    def test_comparison_words(self):
        self.assertEqual(self._parsed("quality above 90"),
                         {("quality", "gt", 90.0)})
        self.assertEqual(self._parsed("forward pe under 25"),
                         {("forward_pe", "lt", 25.0)})
        self.assertEqual(self._parsed("rs at least 85"),
                         {("rs_rank", "gte", 85.0)})

    def test_bare_flags(self):
        self.assertEqual(self._parsed("turnaround stocks"),
                         {("is_turnaround", "eq", True)})
        self.assertEqual(self._parsed("golden cross"),
                         {("golden_cross", "eq", True)})

    def test_one_clause_can_carry_several_rules(self):
        self.assertEqual(self._parsed("buy zone stocks above 200ma"),
                         {("in_buy_zone", "eq", True),
                          ("above_200ma", "eq", True)})

    def test_multi_clause_query(self):
        self.assertEqual(
            self._parsed("institution ownership > 70% and eps growth > 30% "
                         "and rs > 90 and above 200ma"),
            {("inst_own", "gt", 70.0), ("eps_growth", "gt", 30.0),
             ("rs_rank", "gt", 90.0), ("above_200ma", "eq", True)})

    def test_moat_shorthand(self):
        self.assertEqual(self._parsed("AI stocks with moat 4/4"),
                         {("moat", "gte", 4.0)})

    def test_longest_phrase_wins(self):
        # "momentum pullback" must not degrade into plain "momentum"
        self.assertEqual(self._parsed("momentum pullback"),
                         {("is_momentum_pullback", "eq", True)})

    def test_negation(self):
        conds = S.parse_query("quality above 90, not turnaround")
        neg = [c for c in conds if c.negate]
        self.assertEqual(len(neg), 1)
        self.assertEqual(neg[0].field, "is_turnaround")

    def test_valuation_defaults_to_at_most(self):
        # "pe 25" means cheap, not expensive
        self.assertEqual(self._parsed("forward pe 25"),
                         {("forward_pe", "lte", 25.0)})
        self.assertEqual(self._parsed("quality 90"),
                         {("quality", "gte", 90.0)})

    def test_billions_shorthand(self):
        self.assertEqual(self._parsed("market cap above 10b"),
                         {("market_cap", "gt", 1e10)})

    def test_stars(self):
        self.assertEqual(self._parsed("conviction 4 stars"),
                         {("conv_stars", "gte", 4.0)})

    def test_gibberish_yields_nothing_rather_than_a_wrong_filter(self):
        self.assertEqual(S.parse_query("asdfgh qwerty"), [])
        self.assertEqual(S.parse_query(""), [])

    def test_later_mention_of_a_field_wins(self):
        self.assertEqual(self._parsed("quality above 90 and quality above 95"),
                         {("quality", "gt", 95.0)})


class TestPresets(unittest.TestCase):
    def test_every_preset_references_real_fields(self):
        for preset in S.PRESETS:
            for cond in preset["conditions"]:
                with self.subTest(preset=preset["key"], field=cond.field):
                    self.assertIn(cond.field, S.FIELD_BY_KEY)
                    self.assertIn(cond.op, S.OPERATORS)

    def test_preset_group_builds(self):
        g = S.preset_group("ai_leaders")
        self.assertIsInstance(g, Group)
        self.assertEqual(g.op, "AND")
        self.assertTrue(g.items)

    def test_unknown_preset_is_none(self):
        self.assertIsNone(S.preset_group("nope"))


class TestSerialization(unittest.TestCase):
    def test_group_round_trips(self):
        g = Group("AND", [
            _c("quality", "gt", 95),
            Group("OR", [_c("is_turnaround", "eq", True),
                         _c("is_vcp", "eq", True)]),
        ])
        back = S.group_from_json(S.group_to_json(g))
        row = _row(quality=100, is_turnaround=True, is_vcp=False)
        self.assertEqual(S.eval_group(row, g), S.eval_group(row, back))
        self.assertIsInstance(back.items[1], Group)

    def test_a_bare_condition_dict_is_accepted(self):
        g = S.group_from_json({"field": "quality", "op": "gt", "value": 90})
        self.assertEqual(len(g.items), 1)
        self.assertTrue(S.eval_group(_row(quality=95), g))

    def test_empty_json_is_a_match_everything_group(self):
        self.assertTrue(S.eval_group(_row(), S.group_from_json(None)))


class TestDescribe(unittest.TestCase):
    def test_pill_text(self):
        self.assertEqual(S.describe(_c("quality", "gt", 95)),
                         "Quality Score > 95")
        self.assertEqual(S.describe(_c("abs_vs_8ema", "within", 1)),
                         "Price within 1.00% of 8 EMA")
        self.assertEqual(S.describe(_c("above_200ma", "eq", True)),
                         "Above 200 MA")
        self.assertEqual(S.describe(_c("category", "eq", "Turnaround")),
                         "Category = Turnaround")

    def test_negated_pill_says_so(self):
        self.assertTrue(S.describe(_c("quality", "gt", 95, negate=True))
                        .startswith("NOT "))

    def test_market_cap_formats_as_dollars(self):
        self.assertEqual(S.describe(_c("market_cap", "gt", 1e10)),
                         "Market Cap > $10.0B")


class TestSuggest(unittest.TestCase):
    def test_prefix_offers_combinations(self):
        labels = [s["label"] for s in S.suggest("turn")]
        self.assertIn("Turnaround", labels)
        self.assertTrue(any("Quality" in l for l in labels))

    def test_buy_prefix(self):
        labels = [s["label"] for s in S.suggest("buy")]
        self.assertIn("Buy Zone", labels)

    def test_suggestions_carry_usable_conditions(self):
        for s in S.suggest("turn"):
            g = S.group_from_json({"op": "AND", "items": s["conditions"]})
            for item in g.items:
                self.assertIn(item.field, S.FIELD_BY_KEY)

    def test_empty_prefix_suggests_nothing(self):
        self.assertEqual(S.suggest(""), [])

    def test_anchor_matches_lead(self):
        # "qual" must lead with Quality, not with Turnaround just because
        # one Turnaround combo happens to be "Turnaround + Quality"
        labels = [s["label"] for s in S.suggest("qual")]
        self.assertTrue(labels[0].startswith("Quality"), labels)

    def test_matches_word_starts_not_substrings(self):
        # "rs" must not pull in "AI Leade-rs" / "Owne-rs-hip"
        labels = [s["label"] for s in S.suggest("rs")]
        self.assertTrue(labels[0].startswith("RS"), labels)
        self.assertFalse(any("Leaders" in l for l in labels), labels)

    def test_multi_word_prefix_still_matches(self):
        labels = [s["label"] for s in S.suggest("8 ema")]
        self.assertTrue(any("8 EMA" in l for l in labels), labels)


class TestRefineSuggestions(unittest.TestCase):
    def test_suggests_a_complement_to_the_active_screen(self):
        g = Group("AND", [_c("is_turnaround", "eq", True)])
        out = S.refine_suggestions(g)
        self.assertTrue(out)
        self.assertTrue(all(r["why"] for r in out))

    def test_never_suggests_a_field_already_filtered(self):
        g = Group("AND", [_c("is_turnaround", "eq", True),
                          _c("rs_rank", "gt", 80), _c("health", "gt", 70)])
        for r in S.refine_suggestions(g):
            for cond in r["conditions"]:
                self.assertNotIn(cond["field"], {"rs_rank", "health"})

    def test_empty_screen_gets_no_refinements(self):
        self.assertEqual(S.refine_suggestions(Group("AND", [])), [])


class TestBuildUniverse(unittest.TestCase):
    def test_derives_fields_from_a_raw_scan_row(self):
        rows = S.build_universe([{
            "ticker": "XYZ", "sector": "Technology",
            "raw": {"Ticker": "XYZ", "Current Price": 100.0, "8EMA": 99.0,
                    "50MA": 90.0, "200MA": 80.0, "Category": "Turnaround",
                    "Above_200MA": "True", "RSI_14": "25.0"},
        }])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertAlmostEqual(r["pct_vs_8ema"], 1.0101, places=3)
        self.assertAlmostEqual(r["abs_vs_8ema"], 1.0101, places=3)
        self.assertTrue(r["golden_cross"])       # 50MA > 200MA
        self.assertFalse(r["death_cross"])
        self.assertTrue(r["above_200ma"])        # string "True" coerced
        self.assertTrue(r["is_turnaround"])
        self.assertFalse(r["is_momentum"])
        self.assertTrue(r["is_oversold"])        # RSI 25

    def test_unknown_stays_unknown_rather_than_false(self):
        # No 50MA/200MA means the cross is unknown; reporting False would
        # make a data gap look like a bearish signal.
        rows = S.build_universe([{"ticker": "XYZ", "raw": {"Ticker": "XYZ"}}])
        r = rows[0]
        self.assertIsNone(r["golden_cross"])
        self.assertIsNone(r["death_cross"])
        self.assertIsNone(r["is_turnaround"])
        self.assertIsNone(r["is_oversold"])
        self.assertIsNone(r["is_breakout"])

    def test_rows_without_a_ticker_are_dropped(self):
        self.assertEqual(S.build_universe([{"raw": {}}]), [])

    def test_watchlist_membership(self):
        rows = S.build_universe([{"ticker": "AAA", "raw": {"Ticker": "AAA"}},
                                 {"ticker": "BBB", "raw": {"Ticker": "BBB"}}],
                                watchlist_tickers={"AAA"})
        flags = {r["ticker"]: r["is_watchlist"] for r in rows}
        self.assertTrue(flags["AAA"])
        self.assertFalse(flags["BBB"])


class TestFieldRegistry(unittest.TestCase):
    def test_keys_are_unique(self):
        keys = [f.key for f in S.FIELDS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_field_is_in_a_known_group(self):
        for f in S.FIELDS:
            self.assertIn(f.group, S.FIELD_GROUPS)

    def test_enum_fields_that_declare_values_use_valid_ones(self):
        for f in S.FIELDS:
            if f.kind == S.ENUM and f.values:
                self.assertTrue(all(isinstance(v, str) for v in f.values))

    def test_formatting(self):
        q = S.FIELD_BY_KEY["quality"]
        self.assertEqual(q.format(95), "95")
        self.assertEqual(q.format(None), "—")
        self.assertEqual(S.FIELD_BY_KEY["eps_growth"].format(30.28), "30.3%")
        self.assertEqual(S.FIELD_BY_KEY["market_cap"].format(2.5e12), "$2.5T")
        self.assertEqual(S.FIELD_BY_KEY["above_200ma"].format(True), "Yes")


class TestWatchlistField(unittest.TestCase):
    """Themes like "AI" have no scan field — the user's watchlist is what
    defines them, so membership is a first-class filter."""

    def _rows(self):
        return S.build_universe(
            [{"ticker": "NVDA", "raw": {"Ticker": "NVDA", "Current Price": 100}},
             {"ticker": "KO", "raw": {"Ticker": "KO", "Current Price": 50}}],
            watchlist_map={"AI": ["NVDA"], "Dividend": ["KO"],
                           "sp500": ["NVDA", "KO"]})

    def test_membership_matches(self):
        rows = self._rows()
        res = S.screen(rows, Group("AND", [_c("watchlist", "has", "AI")]))
        self.assertEqual([m["ticker"] for m in res.matches], ["NVDA"])

    def test_a_ticker_can_be_on_several_lists(self):
        nvda = [r for r in self._rows() if r["ticker"] == "NVDA"][0]
        self.assertEqual(nvda["watchlists"], ["AI", "sp500"])

    def test_not_a_member_is_a_real_answer_not_missing_data(self):
        ko = [r for r in self._rows() if r["ticker"] == "KO"][0]
        r = S.eval_condition(ko, _c("watchlist", "has", "AI"))
        self.assertFalse(r.passed)
        self.assertFalse(r.missing)

    def test_negation(self):
        rows = self._rows()
        res = S.screen(rows, Group("AND", [
            Condition("watchlist", "has", "AI", negate=True)]))
        self.assertEqual([m["ticker"] for m in res.matches], ["KO"])

    def test_pill_text(self):
        self.assertEqual(S.describe(_c("watchlist", "has", "AI")),
                         'On "AI" watchlist')


class TestPresetLibrary(unittest.TestCase):
    def test_every_preset_has_a_unique_key(self):
        keys = [p["key"] for p in S.PRESETS]
        self.assertEqual(len(keys), len(set(keys)), "duplicate preset key")

    def test_every_preset_declares_a_known_group(self):
        for p in S.PRESETS:
            with self.subTest(preset=p["key"]):
                self.assertIn(p["group"], S.PRESET_GROUPS)

    def test_every_preset_has_a_name_and_description(self):
        for p in S.PRESETS:
            with self.subTest(preset=p["key"]):
                self.assertTrue(p["name"] and p["desc"] and p["icon"])

    def test_no_preset_uses_an_unreachable_threshold(self):
        # Guards the calibration: the conviction composite tops out in the
        # high 70s and Conv_Stars never reaches 5, so a preset asking for
        # more would be an empty screen by construction.
        ceilings = {"conviction": 78, "conv_stars": 4, "moat": 4,
                    "quality": 100, "health": 100, "rs_rank": 99}
        for p in S.PRESETS:
            for c in p["conditions"]:
                cap = ceilings.get(c.field)
                if cap is None or c.op not in ("gt", "gte"):
                    continue
                with self.subTest(preset=p["key"], field=c.field):
                    self.assertLess(
                        c.value if c.op == "gte" else c.value, cap + 1,
                        f"{p['key']}: {c.field} {c.op} {c.value} exceeds the "
                        f"observed maximum {cap}")

    def test_valuation_screens_exclude_negative_multiples(self):
        # A bare "P/E < 25" admits every loss-making company, which is the
        # opposite of a value screen.
        for p in S.PRESETS:
            for c in p["conditions"]:
                if c.field in ("forward_pe", "peg_ratio"):
                    with self.subTest(preset=p["key"]):
                        self.assertEqual(
                            c.op, "between",
                            f"{p['key']} bounds {c.field} without a floor")
                        self.assertGreaterEqual(c.value, 0)

    def test_preset_groups_are_all_populated(self):
        used = {p["group"] for p in S.PRESETS}
        for g in S.PRESET_GROUPS:
            self.assertIn(g, used, f"group {g} has no presets")

    def test_there_are_enough_presets_to_be_worth_browsing(self):
        self.assertGreaterEqual(len(S.PRESETS), 30)


class TestDecisionFields(unittest.TestCase):
    """The decision-engine scores are exposed as screenable fields so
    presets can gate on them — with the coverage guard intact."""

    def _full(self, **kw):
        raw = {"Ticker": "AAA", "Current Price": 100.0, "8EMA": 99.0,
               "50MA": 90.0, "200MA": 80.0, "Above_200MA": "True",
               "GrossMargin%": 60.0, "OperatingMargin%": 30.0,
               "ReturnOnEquity%": 30.0, "FCF_Margin%": 25.0, "Revenue": 25.0,
               "EPS_Growth%": 40.0, "DebtToEquity": 30.0, "Inst_Own%": 80.0,
               "CurrentRatio": 2.0, "QuickRatio": 1.5, "TotalCash": 1e10,
               "TotalDebt": 1e9, "FCF_Positive": "True", "RS_Rank": 90,
               "Forward_PE": 18.0, "RVOL": 1.6, "Swing_Score": 85,
               "Breakout_Probability": 85.0, "RR_T2": 3.0}
        raw.update(kw)
        return {"ticker": "AAA", "sector": "Technology", "raw": raw}

    def test_scores_are_screenable(self):
        rows = S.build_universe([self._full()])
        self.assertIsNotNone(rows[0]["decision_investment"])
        self.assertIsNotNone(rows[0]["decision_swing"])
        self.assertIn("decision_investment", S.FIELD_BY_KEY)
        self.assertIn("decision_swing", S.FIELD_BY_KEY)

    def test_a_fund_gets_no_investment_score(self):
        # An ETF has no margins, moat or EPS — scoring it off RS and the
        # 200MA alone produced a 95 that outranked fully-measured companies.
        rows = S.build_universe([{
            "ticker": "CIBR", "sector": "ETF",
            "raw": {"Ticker": "CIBR", "Current Price": 100.0, "200MA": 80.0,
                    "Above_200MA": "True", "RS_Rank": 95}}])
        self.assertIsNone(rows[0]["decision_investment"])

    def test_the_two_ready_presets_exist_and_gate_at_80(self):
        for key in ("longterm_ready", "swing_ready"):
            preset = S.PRESET_BY_KEY[key]
            cond = preset["conditions"][0]
            with self.subTest(preset=key):
                self.assertEqual(cond.value, 80)
                self.assertEqual(cond.op, "gt")
                self.assertIn(cond.field, S.FIELD_BY_KEY)

    def test_ready_presets_select_on_the_right_score(self):
        self.assertEqual(S.PRESET_BY_KEY["longterm_ready"]["conditions"][0].field,
                         "decision_investment")
        self.assertEqual(S.PRESET_BY_KEY["swing_ready"]["conditions"][0].field,
                         "decision_swing")


if __name__ == "__main__":
    unittest.main()


class TestPresetStrategy(unittest.TestCase):
    """A preset expresses an intent; the action shown for its results has to
    be gated on the matching score or the page contradicts its own screen."""

    def test_the_ready_presets_declare_their_own_score(self):
        self.assertEqual(S.preset_strategy("longterm_ready"), "LONGTERM")
        self.assertEqual(S.preset_strategy("swing_ready"), "SWING")

    def test_setup_presets_default_to_swing(self):
        self.assertEqual(S.preset_strategy("todays_breakouts"), "SWING")
        self.assertEqual(S.preset_strategy("near_buy_zone"), "SWING")

    def test_quality_presets_default_to_longterm(self):
        self.assertEqual(S.preset_strategy("compounders"), "LONGTERM")
        self.assertEqual(S.preset_strategy("hedge_fund"), "LONGTERM")

    def test_every_preset_resolves_to_a_real_strategy(self):
        from stockanalysis.core.decision_engine import STRATEGIES
        for p in S.PRESETS:
            with self.subTest(preset=p["key"]):
                self.assertIn(S.preset_strategy(p["key"]), STRATEGIES)

    def test_an_unknown_key_falls_back_safely(self):
        self.assertEqual(S.preset_strategy("nope"), "LONGTERM")


class TestBuyZoneTiers(unittest.TestCase):
    def test_strong_buy_zone_counts_as_in_buy_zone(self):
        # Testing == "Buy Zone" excluded the better tier, so the Near Buy
        # Zone preset skipped exactly the names it should have led with.
        rows = S.build_universe([
            {"ticker": "AAA", "raw": {"Ticker": "AAA", "Current Price": 100.0,
                                      "Pct_vs_8EMA": -0.5, "Dist_52W_High%": -8.0,
                                      "Above_200MA": "True", "Price_vs_50MA%": 1.0,
                                      "Pullback_Vol_Ratio": 0.6,
                                      "VolumeDryingUp": "True", "RS_Rank": 95,
                                      "RSI_14": 50.0}}])
        self.assertEqual(rows[0]["buy_zone_label"], "Strong Buy Zone")
        self.assertTrue(rows[0]["in_buy_zone"])
