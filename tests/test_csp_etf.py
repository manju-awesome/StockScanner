"""
Tests for core.csp.etf — cash-secured puts on funds.

The invariants are the ones that keep this section honest about what it
does and does not know:

  1. It never claims a quality verdict. There is no score out of 100 and
     no valuation on an ETF row, because nothing here could support one.
     The verdict is about the CONTRACT.
  2. Unknown is not failure, and it is not success either. A fund with no
     scan row gets no structure gate — but it also does not get a
     fabricated one. With an empty row compute_pullback reports AT_HIGHS,
     because nothing is below a price when nothing is known, and three
     funds in the live library hit exactly that path.
  3. Gates, not weights, in a fixed order. Structure, then liquidity, then
     premium. A fat credit never rescues an unfillable quote and neither
     rescues a fund below a falling 200 MA.
  4. Yield ranks only among fillable contracts. The highest annualised
     number in a chain is routinely a one-sided quote, and printing it as
     the headline is the most misleading thing this section could do.
  5. Overlap is a property of the SET. Two funds that are a third the same
     six companies are one position; that belongs in the summary, never in
     a row's verdict.

The chain is stubbed throughout — these test the reasoning, not yfinance.

Run with: python -m unittest tests.test_csp_etf
"""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.csp import etf as ETF

TODAY = dt.date(2026, 8, 18)
EXPIRY = "2026-09-18"          # 31 days out


def _put(strike, bid, ask, oi=5000, iv=0.25):
    """One chain row in the shape core.csp.chain.fetch_puts returns."""
    mid = (bid + ask) / 2
    return {"strike": strike, "bid": bid, "ask": ask, "mid": mid,
            "last": mid, "volume": 500.0, "open_interest": float(oi),
            "iv": iv, "iv_source": "chain", "sellable": bid > 0,
            "spread_pct": (ask - bid) / mid * 100 if mid else None}


def _chain(spot=100.0, **kw):
    """A normal, liquid chain around `spot`, one strike every $5.

    Spreads are ~1% of mid: `chain.liquidity_score` GATES on the spread
    (above 10% the contract is untradable at any open interest), so a
    fixture meant to exercise the premium gate has to clear the liquidity
    one first or every test below reads ILLIQUID.
    """
    out = []
    for k in range(int(spot * 0.80), int(spot * 1.05), 5):
        mid = 2.0 + (k / spot) * 2.0
        out.append(_put(k, round(mid * 0.995, 2), round(mid * 1.005, 2), **kw))
    return out


def _raw(price=100.0, ma200=90.0, ma50=95.0, rising=True):
    """A scan row with the columns a structure read needs."""
    return {
        "Ticker": "TEST", "Current Price": price,
        "200MA": ma200, "50MA": ma50, "21EMA": price * 0.99,
        "8EMA": price * 0.995, "ATR20": price * 0.02,
        "ATR_Pct": 2.0, "52W High": price * 1.10, "52W Low": price * 0.70,
        "MA200_Slope%": 5.0 if rising else -5.0,
        "MA50_Slope%": 4.0 if rising else -4.0,
    }


PROFILE = {"name": "Test Fund ETF", "category": "Technology",
           "family": "Test", "aum": 5.0e9, "expense_ratio": 0.35,
           "price": 100.0, "top10_weight": 60.0,
           "holdings": [{"ticker": "NVDA", "name": "NVIDIA", "weight": 20.0},
                        {"ticker": "MSFT", "name": "Microsoft", "weight": 8.0}]}


def _evaluate(raw=None, chain=None, profile=None, spot=100.0):
    with mock.patch.object(ETF.CH, "expiries", return_value=[EXPIRY]), \
         mock.patch.object(ETF.CH, "fetch_puts",
                           return_value=chain if chain is not None
                           else _chain(spot)):
        return ETF.evaluate("TEST", raw if raw is not None else _raw(spot),
                            profile if profile is not None else PROFILE,
                            risk_free=0.042, today=TODAY, spot=spot)


class NoCompanyVerdict(unittest.TestCase):
    """(1) The section makes no claim it cannot support."""

    def test_the_row_carries_no_quality_or_valuation(self):
        row = _evaluate()
        for forbidden in ("quality", "lquality", "valuation", "csp_score",
                          "stock_score", "margin_at_assignment", "eligibility"):
            self.assertNotIn(forbidden, row)

    def test_fund_facts_are_present_but_are_not_scores(self):
        row = _evaluate()
        self.assertEqual(row["category"], "Technology")
        self.assertEqual(row["aum"], 5.0e9)
        self.assertEqual(row["expense_ratio"], 0.35)
        # Context, not a component of any verdict.
        self.assertNotIn("score", row["final"])

    def test_the_verdict_key_is_one_of_the_declared_set(self):
        self.assertIn(_evaluate()["final"]["key"], ETF.VERDICTS)


class StructureIsMeasuredOrAdmitted(unittest.TestCase):
    """(2) Unknown is neither failure nor a fabricated pass."""

    def test_a_fund_with_no_scan_row_reports_unmeasured(self):
        row = _evaluate(raw={})
        self.assertFalse(row["structure"]["measured"])
        self.assertIn("no scan row", row["structure"]["note"])
        # Specifically NOT the technicals' empty-input default.
        self.assertNotIn("stage", row["structure"])

    def test_an_unmeasured_fund_is_not_gated_on_structure(self):
        """Unknown is not failure — the package rule."""
        self.assertNotEqual(_evaluate(raw={})["final"]["key"], "STRUCTURE")

    def test_a_partial_row_is_still_unmeasured(self):
        raw = _raw()
        del raw["200MA"]
        self.assertFalse(_evaluate(raw=raw)["structure"]["measured"])

    def test_a_full_row_is_measured(self):
        st = _evaluate()["structure"]
        self.assertTrue(st["measured"])
        self.assertIsNotNone(st["trend_state"])


class GatesInOrder(unittest.TestCase):
    """(3) Structure, then liquidity, then premium."""

    def test_a_broken_trend_blocks_however_rich_the_premium(self):
        rich = [_put(k, 8.00, 8.05) for k in (80, 85, 90, 95)]
        row = _evaluate(raw=_raw(ma200=110.0, ma50=105.0, rising=False),
                        chain=rich)
        self.assertEqual(row["final"]["key"], "STRUCTURE")

    def test_a_wide_spread_blocks_however_rich_the_premium(self):
        wide = [_put(k, 1.0, 9.0) for k in (85, 90, 95)]
        row = _evaluate(chain=wide)
        self.assertEqual(row["final"]["key"], "ILLIQUID")

    def test_no_bid_is_illiquid_not_thin(self):
        dead = [_put(k, 0.0, 5.0) for k in (85, 90, 95)]
        self.assertEqual(_evaluate(chain=dead)["final"]["key"], "ILLIQUID")

    def test_thin_open_interest_is_illiquid(self):
        thin = [_put(k, 2.00, 2.01, oi=2) for k in (85, 90, 95)]
        self.assertEqual(_evaluate(chain=thin)["final"]["key"], "ILLIQUID")

    def test_a_tiny_credit_is_thin(self):
        tiny = [_put(k, 0.10, 0.101, oi=9000) for k in (85, 90, 95)]
        row = _evaluate(chain=tiny)
        self.assertEqual(row["final"]["key"], "THIN")
        self.assertIn("floor", row["final"]["why"])

    def test_a_liquid_well_paid_contract_sells(self):
        row = _evaluate()
        self.assertEqual(row["final"]["key"], "SELL")
        self.assertIsNotNone(row["contract"])

    def test_no_listed_options_is_no_contract(self):
        with mock.patch.object(ETF.CH, "expiries", return_value=[]):
            row = ETF.evaluate("TEST", _raw(), PROFILE, today=TODAY, spot=100.0)
        self.assertEqual(row["final"]["key"], "NO_CONTRACT")

    def test_a_dead_symbol_never_raises(self):
        with mock.patch.object(ETF.CH, "expiries",
                               side_effect=RuntimeError("boom")):
            row = ETF.evaluate("TEST", _raw(), PROFILE, today=TODAY, spot=100.0)
        self.assertEqual(row["final"]["key"], "NO_CONTRACT")
        self.assertIn("boom", row["final"]["why"])


class ContractSelection(unittest.TestCase):
    """(4) Fillable first, yield second."""

    def test_only_strikes_below_spot_are_considered(self):
        row = _evaluate()
        self.assertLess(row["contract"]["strike"], row["price"])
        for alt in row["alternatives"]:
            self.assertLess(alt["strike"], row["price"])

    def test_an_unfillable_higher_yield_does_not_win(self):
        # Both strikes sit inside the 0.15-0.35 delta band at these inputs
        # (96 -> 0.26, 94 -> 0.18), so the choice between them is decided
        # by fillability rather than by the band.
        chain = [
            _put(96, 0.05, 9.00, oi=9000),     # huge "yield", no market
            _put(94, 2.00, 2.01, oi=9000),     # real, fillable
        ]
        row = _evaluate(chain=chain)
        self.assertEqual(row["contract"]["strike"], 94)
        self.assertTrue(row["contract"]["tradable"])

    def test_an_all_untradable_band_still_reports_the_contract(self):
        """Showing the strike and saying why it cannot be sold beats a
        blank row that reads as 'this fund has no options'."""
        chain = [_put(96, 0.05, 9.00, oi=9000)]
        row = _evaluate(chain=chain)
        self.assertEqual(row["final"]["key"], "ILLIQUID")
        self.assertIsNotNone(row["contract"])
        self.assertFalse(row["contract"]["tradable"])

    def test_the_effective_basis_is_the_strike_less_the_credit(self):
        c = _evaluate()["contract"]
        self.assertAlmostEqual(c["effective_basis"],
                               c["strike"] - c["credit"], places=2)
        self.assertGreater(c["basis_discount_pct"], 0)

    def test_annualised_scales_the_period_yield_by_dte(self):
        row = _evaluate()
        c = row["contract"]
        self.assertAlmostEqual(c["annualised_pct"],
                               c["period_pct"] * 365 / row["dte"], places=1)

    def test_the_expiry_is_the_one_nearest_a_month_out(self):
        with mock.patch.object(ETF.CH, "expiries",
                               return_value=["2026-09-04", EXPIRY,
                                             "2026-09-25"]), \
             mock.patch.object(ETF.CH, "fetch_puts", return_value=_chain()):
            row = ETF.evaluate("TEST", _raw(), PROFILE, today=TODAY, spot=100.0)
        self.assertEqual(row["expiry"], EXPIRY)
        self.assertEqual(row["dte"], 31)

    def test_a_fund_with_no_price_is_not_priced(self):
        row = ETF.evaluate("TEST", {}, {}, today=TODAY)
        self.assertEqual(row["final"]["key"], "NO_CONTRACT")
        self.assertIsNone(row["contract"])


class Overlap(unittest.TestCase):
    """(5) A property of the set."""

    def _row(self, ticker, key, holdings):
        return {"ticker": ticker, "final": {"key": key},
                "holdings": holdings, "contract": {"annualised_pct": 20.0}}

    def test_a_holding_in_one_fund_is_not_overlap(self):
        rows = [self._row("A", "SELL", [{"ticker": "NVDA", "weight": 20.0}])]
        self.assertEqual(ETF.overlap(rows), [])

    def test_a_holding_in_two_funds_is(self):
        rows = [self._row("A", "SELL", [{"ticker": "NVDA", "weight": 20.0}]),
                self._row("B", "SELL", [{"ticker": "NVDA", "weight": 8.0}])]
        got = ETF.overlap(rows)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["holding"], "NVDA")
        self.assertEqual(len(got[0]["funds"]), 2)
        self.assertAlmostEqual(got[0]["total_weight"], 28.0)

    def test_funds_with_no_live_contract_are_excluded(self):
        """An overlap with a fund you cannot sell is not a position."""
        rows = [self._row("A", "SELL", [{"ticker": "NVDA", "weight": 20.0}]),
                self._row("B", "STRUCTURE", [{"ticker": "NVDA", "weight": 8.0}])]
        self.assertEqual(ETF.overlap(rows), [])

    def test_most_shared_first(self):
        rows = [self._row("A", "SELL", [{"ticker": "NVDA", "weight": 20.0},
                                        {"ticker": "MSFT", "weight": 5.0}]),
                self._row("B", "SELL", [{"ticker": "NVDA", "weight": 8.0},
                                        {"ticker": "MSFT", "weight": 5.0}]),
                self._row("C", "SELL", [{"ticker": "MSFT", "weight": 6.0}])]
        got = ETF.overlap(rows)
        self.assertEqual(got[0]["holding"], "MSFT")     # 3 funds beats 2


class UniverseRule(unittest.TestCase):
    def test_is_etf_defers_to_the_scan_s_own_classification(self):
        self.assertTrue(ETF.is_etf({"sector": "ETF"}))
        self.assertFalse(ETF.is_etf({"sector": "Technology"}))
        self.assertFalse(ETF.is_etf({}))


if __name__ == "__main__":
    unittest.main()
