"""
Tests for the reverse-DCF headline in core/longterm/valuation.py.

The band, the gap and `acceptable` were always right. What was wrong was
the SENTENCE explaining them, and it was wrong in a way that made the
engine look broken to anyone reading it:

    NVDA — "Price requires 55% growth a year; the company delivers +194%
            — demands more growth than the company has delivered"

Both halves are individually true and together they read as nonsense.
The gap is computed against delivered growth CAPPED at
MAX_CREDIBLE_DELIVERED (40%), because no company sustains 194% for five
years — but the headline printed the raw rate beside a verdict derived
from the capped one. Ten of 381 names printed that contradiction.

The second defect is subtler. OVERVALUED has two independent causes: a
gap wider than tolerance, and an implied rate above
IMPLAUSIBLE_IMPLIED_GROWTH (35%). When only the second fired, the
sentence still claimed "demands more growth than the company has
delivered" — which for IDXX was flatly false, since its gap was -1.8,
meaning the price demanded LESS than delivered.

These tests pin the sentence to the arithmetic that produced it.

Run with: python -m unittest tests.test_valuation_headline
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# The distinctive part of the ceiling sentence. Plain "ceiling" also
# appears in the credited-rate clause ("the sustainable ceiling"), so
# matching on that alone conflates two different statements.
CEILING_PHRASE = "ceiling any forecast should carry"

from stockanalysis.core.longterm import valuation as V   # noqa: E402


def row(price, fcf, shares, revenue_cagr=None, fcf_cagr=None, **over):
    """A scan row with enough for the reverse DCF to run."""
    base = {
        "Ticker": "TEST", "Current Price": price,
        "FreeCashFlow": fcf, "MarketCap": price * shares,
        "FCF_CAGR%": fcf_cagr, "Revenue_CAGR%": revenue_cagr,
        "TotalCash": 0.0, "TotalDebt": 0.0, "Beta": 1.0,
        "Fundamentals_As_Of": "2026-01-31",
    }
    base.update(over)
    return base


class HeadlineConsistency(unittest.TestCase):
    """Whatever the sentence claims must match the numbers beside it."""

    def _val(self, **kw):
        return V._reverse_dcf(row(**kw), risk_free=0.046)

    def test_capped_delivered_is_shown_as_credited(self):
        """A headline may not print a delivered rate the gap did not use."""
        out = self._val(price=225.0, fcf=96.7e9, shares=24_221e6,
                        fcf_cagr=193.9, revenue_cagr=100.0)
        self.assertIsNotNone(out)
        if out["delivered_capped"]:
            self.assertIn("credited at", out["headline"])
            self.assertLessEqual(out["delivered_credited_pct"],
                                 V.MAX_CREDIBLE_DELIVERED)
            # The raw figure still appears — it is real, just not what
            # the gap was measured against.
            self.assertIn(f"{out['delivered_growth_pct']:+.0f}%",
                          out["headline"])

    def test_gap_is_computed_against_the_credited_rate(self):
        out = self._val(price=225.0, fcf=96.7e9, shares=24_221e6,
                        fcf_cagr=193.9, revenue_cagr=100.0)
        self.assertAlmostEqual(
            out["growth_gap_pp"],
            out["implied_growth_pct"] - out["delivered_credited_pct"],
            places=0)

    def test_demands_more_only_when_credited_is_below_implied(self):
        """The core contradiction: the sentence must not claim the price
        demands more than delivered while the comparison says otherwise."""
        out = self._val(price=225.0, fcf=96.7e9, shares=24_221e6,
                        fcf_cagr=193.9, revenue_cagr=100.0)
        if "demands more growth" in out["headline"]:
            self.assertLess(out["delivered_credited_pct"],
                            out["implied_growth_pct"])

    def test_ceiling_wording_names_the_rule_that_fired(self):
        """When the implausible-growth ceiling forces OVERVALUED and the
        gap is inside tolerance, the sentence must say so rather than
        assert a comparison that did not decide anything."""
        out = self._val(price=100.0, fcf=1.0e9, shares=200e6,
                        fcf_cagr=38.0, revenue_cagr=30.0)
        if out and out["implausible_growth"] and \
                out["growth_gap_pp"] <= out["tolerance_pp"]:
            self.assertIn(CEILING_PHRASE, out["headline"])
            self.assertNotIn("demands more growth", out["headline"])

    def test_band_still_follows_from_the_gap_and_the_ceiling(self):
        """This was a display defect, so the band must remain a pure
        function of the gap, the tolerance and the implausible-growth
        rule — asserting a specific band on synthetic inputs would pin
        the fixture, not the behaviour."""
        for kw in ({"fcf_cagr": 193.9, "revenue_cagr": 100.0},
                   {"fcf_cagr": 12.0, "revenue_cagr": 10.0},
                   {"fcf_cagr": -5.0, "revenue_cagr": 2.0}):
            for price in (50.0, 225.0, 900.0):
                out = self._val(price=price, fcf=5.0e9, shares=500e6, **kw)
                if not out:
                    continue
                gap, tol = out["growth_gap_pp"], out["tolerance_pp"]
                with self.subTest(price=price, **kw):
                    if out["implausible_growth"] or gap > tol:
                        self.assertEqual(out["band"], "OVERVALUED")
                    elif gap < -tol:
                        self.assertEqual(out["band"], "UNDERVALUED")
                    else:
                        self.assertEqual(out["band"], "FAIR")

    def test_uncapped_name_says_nothing_about_crediting(self):
        """A company inside the ceiling should read exactly as before."""
        out = self._val(price=100.0, fcf=5.0e9, shares=1_000e6,
                        fcf_cagr=12.0, revenue_cagr=10.0)
        if out and not out["delivered_capped"]:
            self.assertNotIn("credited at", out["headline"])


class UniverseInvariant(unittest.TestCase):
    """The property that must hold for every reverse-DCF row, checked on
    synthetic rows spanning the interesting corners rather than on live
    data, so this stays a unit test."""

    CASES = [
        # (fcf_cagr, revenue_cagr) — capped, uncapped, negative, at the
        # ceiling, and just under the implausible-implied threshold.
        (193.9, 100.0), (125.3, 80.0), (39.5, 30.0), (40.0, 35.0),
        (12.0, 10.0), (-5.0, 3.0), (0.0, 0.0),
    ]

    def test_no_headline_contradicts_its_own_numbers(self):
        for fcf_cagr, rev in self.CASES:
            for price in (50.0, 225.0, 900.0):
                out = V._reverse_dcf(
                    row(price=price, fcf=5.0e9, shares=500e6,
                        fcf_cagr=fcf_cagr, revenue_cagr=rev),
                    risk_free=0.046)
                if not out:
                    continue
                hl = out["headline"]
                imp = out["implied_growth_pct"]
                cred = out["delivered_credited_pct"]
                with self.subTest(fcf_cagr=fcf_cagr, price=price):
                    if "demands more growth" in hl:
                        self.assertLess(cred, imp, hl)
                    if "demands less growth" in hl:
                        self.assertGreater(cred, imp, hl)
                    if CEILING_PHRASE in hl:
                        self.assertGreaterEqual(
                            imp, V.IMPLAUSIBLE_IMPLIED_GROWTH, hl)


if __name__ == "__main__":
    unittest.main()
