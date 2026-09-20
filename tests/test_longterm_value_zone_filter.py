"""
Tests for the "Earns" column and the value-zone filter on /longterm.

Both surface the fundamental ladder — what today's price would earn against
this business's own return hurdle — outside the detail drawer it used to be
locked inside.

Invariants defended here:

  1. "is any of" actually works on an enum rule. The operator was offered for
     every ENUM field but parse_rule left the value as a bare string, and the
     matcher compares a bare string whole — so the rule silently matched
     nothing while looking available.
  2. The rule pill reads as a sentence, not as a Python list repr.
  3. The Earns cell distinguishes the four states it can be in: banded,
     not modelled, ladder-not-a-target, and absent. Collapsing any of these
     into a blank cell turns "this method cannot answer" into "no verdict".
  4. A ladder built on shrinking cash flow is never printed as a price to
     wait for. The engine flags it; the cell must honour the flag.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.longterm import buy_zones as BZ     # noqa: E402
from stockanalysis.core.longterm import screen as LS        # noqa: E402
from stockanalysis.webapp import longterm_view as LV        # noqa: E402

BANDS = ["Fair", "Attractive", "Exceptional"]


def _text(html: str) -> str:
    """Rendered cell as plain text, for asserting on what a reader sees."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class TestValueZoneRule(unittest.TestCase):

    def test_in_operator_parses_a_comma_list(self):
        """Left unsplit, the matcher wraps the whole string as one
        alternative and the rule matches nothing at all."""
        cond = LS.parse_rule("value_zone:in:Fair,Attractive,Exceptional")
        self.assertIsNotNone(cond)
        self.assertEqual(cond.op, "in")
        self.assertEqual(cond.value, BANDS)

    def test_in_operator_tolerates_spacing(self):
        cond = LS.parse_rule("value_zone:in: Fair , Attractive ")
        self.assertEqual(cond.value, ["Fair", "Attractive"])

    def test_empty_list_is_rejected_not_matched(self):
        """An empty alternatives list would pass nothing; dropping the rule
        is the behaviour the rest of parse_rule already has for junk."""
        self.assertIsNone(LS.parse_rule("value_zone:in:"))
        self.assertIsNone(LS.parse_rule("value_zone:in: , "))

    def test_single_value_rules_still_parse(self):
        cond = LS.parse_rule("value_zone:eq:Exceptional")
        self.assertEqual(cond.value, "Exceptional")

    def test_every_band_is_a_declared_field_value(self):
        """The filter can only offer what value_zone() can produce."""
        for band in BANDS:
            with self.subTest(band=band):
                self.assertIn(band, BZ.VALUE_ZONES)

    def test_pill_reads_as_a_sentence(self):
        """spec.format() on a list renders its repr — brackets, quotes and
        all — in the one place the rule is shown back to the reader."""
        text = LS.describe(LS.parse_rule("value_zone:in:Fair,Attractive"))
        self.assertEqual(text, "Valuation zone today is Fair or Attractive")
        self.assertNotIn("[", text)
        self.assertNotIn("'", text)


class TestFairOrBetterPreset(unittest.TestCase):

    def test_preset_exists_and_uses_the_in_operator(self):
        self.assertEqual(LS.preset_rules("fair_or_better"),
                         ["value_zone:in:Fair,Attractive,Exceptional"])

    def test_preset_rule_parses(self):
        """A preset whose rule text does not parse screens nothing, and
        looks identical to a preset that legitimately matched nothing."""
        for text in LS.preset_rules("fair_or_better"):
            with self.subTest(text=text):
                self.assertIsNotNone(LS.parse_rule(text))

    def test_preset_is_registered_in_a_known_group(self):
        preset = LS.PRESET_BY_KEY["fair_or_better"]
        self.assertIn(preset["group"], LS.PRESET_GROUPS)


class TestEarnsCell(unittest.TestCase):

    LADDER = [{"key": "fair", "label": "Fair", "price": 421.06, "cagr_pct": 12},
              {"key": "attractive", "label": "Attractive",
               "price": 368.93, "cagr_pct": 15},
              {"key": "exceptional", "label": "Exceptional",
               "price": 298.21, "cagr_pct": 18}]

    @staticmethod
    def _cell(fu):
        return LV._earns_cell({"buy_zones": {"fundamental": fu}})

    def test_banded_price_shows_zone_return_and_ladder(self):
        html, sort = self._cell({
            "zone": "Exceptional", "zone_icon": "🟢🟢", "intrinsic": 427.72,
            "fair_value_gap_pct": -36.0, "expected_cagr_now": 22.1,
            "hurdle_pct": 15.0, "ladder": self.LADDER})
        text = _text(html)
        self.assertIn("Exceptional", text)
        self.assertIn("+22.1%/yr", text)
        self.assertIn("hurdle 15%", text)
        self.assertIn("model $428", text)
        self.assertIn("Fair ≤$421", text)
        self.assertIn("Attr ≤$369", text)
        self.assertIn("Exc ≤$298", text)
        self.assertEqual(sort, 22.1)

    def test_sorts_on_expected_return_so_descending_is_better(self):
        good, _ = None, None
        _, hi = self._cell({"zone": "Exceptional", "expected_cagr_now": 22.1})
        _, lo = self._cell({"zone": "Extreme", "expected_cagr_now": -7.8})
        self.assertGreater(hi, lo)

    def test_unmodelled_says_so_rather_than_going_blank(self):
        """Priced off a peer multiple: the method cannot produce a verdict,
        which is not the same as not having one yet."""
        html, sort = self._cell({"blocked": "Expected return needs a "
                                            "cash-flow model."})
        self.assertIn("not modelled", _text(html))
        self.assertIsNone(sort)

    def test_shrinking_cash_flow_suppresses_the_ladder(self):
        """TXN's model returned $3.51 on a $279 stock. Printing "Exc ≤$3"
        beside it presents arithmetic as a level to wait for."""
        html, _ = self._cell({
            "zone": "Exceptional", "zone_icon": "🟢🟢", "intrinsic": 3.51,
            "expected_cagr_now": -4.0, "hurdle_pct": 12.0,
            "not_a_target": True, "caveat": "Projected growth is -24%/yr.",
            "ladder": [{"key": "fair", "label": "Fair",
                        "price": 3.40, "cagr_pct": 9}]})
        text = _text(html)
        self.assertIn("ladder not a target", text)
        self.assertNotIn("≤$3", text)

    def test_absent_fundamental_is_a_dash(self):
        html, sort = self._cell({})
        self.assertEqual(_text(html), "—")
        self.assertIsNone(sort)

    def test_caveat_reaches_the_hover_title(self):
        html, _ = self._cell({
            "zone": "Fair", "expected_cagr_now": 9.0, "hurdle_pct": 12.0,
            "caveat": "Projected growth is -24%/yr.", "ladder": self.LADDER})
        self.assertIn("Projected growth", html)


class TestEarnsColumnWiring(unittest.TestCase):

    def test_column_is_registered_with_a_header(self):
        keys = [c[0] for c in LV._COLUMNS]
        self.assertIn("earns", keys)
        self.assertEqual(len(LV._HEADERS), len(LV._COLUMNS))

    def test_column_sits_beside_valuation(self):
        """They answer adjacent questions and disagree often enough that
        seeing one without the other is misleading."""
        keys = [c[0] for c in LV._COLUMNS]
        self.assertEqual(keys.index("earns"), keys.index("valuation") + 1)

    def test_saved_order_key_was_bumped(self):
        """A saved order wins over the server's outright, so a new column
        added without bumping the key lands wherever the old layout left a
        gap — for exactly the users who use this table most."""
        _, js = LV.longterm_page({"q": "NOSUCHTICKER"})
        self.assertIn("lt.cols.v3", js)
        self.assertIn("removeItem('lt.cols.v2')", js)


if __name__ == "__main__":
    unittest.main()
