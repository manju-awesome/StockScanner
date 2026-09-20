"""
Tests for core/institutional — 13F institutional option positions.

The failure modes this defends against are all silent ones. Nothing here
crashes when it goes wrong; it produces a plausible number that is off by
100x, or double, or a quarter stale — and a plausible wrong number is
worse than no number at all.

Invariants defended here:

  1. Amendments resolve to ONE book per manager. A restatement replaces,
     a new-holdings amendment adds, and a notice contributes nothing.
     Getting this wrong double-counts real positions.
  2. The units gate separates contracts-reporting filers from
     shares-reporting ones by arithmetic against a reference price, and
     refuses to guess when a row matches neither convention.
  3. Walking back through quarters terminates. The obvious implementation
     of "previous quarter" spins forever on a date already at a quarter end.
  4. Name normalisation absorbs EDGAR's registrant suffixes and inverted
     word order, and a cached index built under older rules is detected
     rather than silently resolving nothing.
  5. A ticker whose name maps to several CUSIPs is reported ambiguous.
     Dual-class issuers must never be silently collapsed onto one class.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.institutional import cusip as CU      # noqa: E402
from stockanalysis.core.institutional import datasets as DS    # noqa: E402
from stockanalysis.core.institutional import filings as FL     # noqa: E402
from stockanalysis.core.institutional import engine as EN      # noqa: E402
from stockanalysis.core.institutional import holdings as HD    # noqa: E402

PERIOD = dt.date(2026, 3, 31)

SUB_COLS = ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK",
            "PERIODOFREPORT"]
COVER_COLS = ["ACCESSION_NUMBER", "ISAMENDMENT", "AMENDMENTTYPE",
              "FILINGMANAGER_NAME"]
INFO_COLS = ["ACCESSION_NUMBER", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP",
             "VALUE", "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL"]


def _tsv(rows, cols) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, delimiter="\t",
                       extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _sub(acc, cik, form="13F-HR", filed="15-MAY-2026", period="31-MAR-2026"):
    return {"ACCESSION_NUMBER": acc, "FILING_DATE": filed,
            "SUBMISSIONTYPE": form, "CIK": cik, "PERIODOFREPORT": period}


def _cover(acc, manager, amend="", amend_type=""):
    return {"ACCESSION_NUMBER": acc, "ISAMENDMENT": amend,
            "AMENDMENTTYPE": amend_type, "FILINGMANAGER_NAME": manager}


def _info(acc, cusip="67066G104", value=1744.0, shares=10.0, put_call="",
          issuer="NVIDIA CORPORATION", title="COM", ptype="SH"):
    return {"ACCESSION_NUMBER": acc, "NAMEOFISSUER": issuer,
            "TITLEOFCLASS": title, "CUSIP": cusip, "VALUE": value,
            "SSHPRNAMT": shares, "SSHPRNAMTTYPE": ptype, "PUTCALL": put_call}


class ZipFixture(unittest.TestCase):
    """Builds throwaway 13F zips shaped like the SEC's own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def make_zip(self, subs, covers, infos, name="test_form13f.zip") -> Path:
        path = self.tmp / name
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(FL.SUBMISSION, _tsv(subs, SUB_COLS))
            zf.writestr(FL.COVERPAGE, _tsv(covers, COVER_COLS))
            zf.writestr(FL.INFOTABLE, _tsv(infos, INFO_COLS))
        return path


# ──────────────────── 1. amendment resolution ────────────────────────
class TestAmendmentResolution(ZipFixture):

    def test_restatement_replaces_original(self):
        """Both filings describe the whole book; only the later counts."""
        z = self.make_zip(
            [_sub("a-1", "0000111", filed="15-MAY-2026"),
             _sub("a-2", "0000111", form="13F-HR/A", filed="28-MAY-2026")],
            [_cover("a-1", "Big Fund"),
             _cover("a-2", "Big Fund", "Y", "RESTATEMENT")],
            [_info("a-1"), _info("a-2")])
        fs = FL.load_filings(z, PERIOD)
        self.assertEqual(fs.accessions, {"a-2"})
        self.assertEqual(fs.superseded, ["a-1"])

    def test_new_holdings_amendment_adds_to_original(self):
        """The original stands; the amendment carries only extra rows."""
        z = self.make_zip(
            [_sub("b-1", "0000222", filed="15-MAY-2026"),
             _sub("b-2", "0000222", form="13F-HR/A", filed="20-MAY-2026")],
            [_cover("b-1", "Adder LP"),
             _cover("b-2", "Adder LP", "Y", "NEW HOLDINGS")],
            [_info("b-1"), _info("b-2")])
        fs = FL.load_filings(z, PERIOD)
        self.assertEqual(fs.accessions, {"b-1", "b-2"})
        self.assertEqual(fs.superseded, [])

    def test_notices_carry_no_holdings(self):
        """13F-NT says 'another manager reports my book' — no info table."""
        z = self.make_zip([_sub("c-1", "0000333", form="13F-NT")],
                          [_cover("c-1", "Notice Only")], [])
        fs = FL.load_filings(z, PERIOD)
        self.assertEqual(fs.accessions, set())
        self.assertEqual(fs.notices, 1)

    def test_blank_amendment_type_replaces_rather_than_adds(self):
        """Ambiguous amendments must lose a row, never invent one."""
        z = self.make_zip(
            [_sub("d-1", "0000444", filed="15-MAY-2026"),
             _sub("d-2", "0000444", form="13F-HR/A", filed="25-MAY-2026")],
            [_cover("d-1", "Vague Capital"),
             _cover("d-2", "Vague Capital", "Y", "")],
            [_info("d-1"), _info("d-2")])
        self.assertEqual(FL.load_filings(z, PERIOD).accessions, {"d-2"})

    def test_other_periods_are_filtered_out(self):
        """Late filings sit in whatever zip they happened to arrive in."""
        z = self.make_zip(
            [_sub("e-1", "0000555", period="31-MAR-2026"),
             _sub("e-2", "0000666", period="31-DEC-2025")],
            [_cover("e-1", "Current"), _cover("e-2", "Late")],
            [_info("e-1"), _info("e-2")])
        self.assertEqual(FL.load_filings(z, PERIOD).accessions, {"e-1"})

    def test_same_name_two_ciks_stays_two_managers(self):
        """Identity is CIK, not the free-text name on the cover page."""
        z = self.make_zip(
            [_sub("f-1", "0000777"), _sub("f-2", "0000888")],
            [_cover("f-1", "Same Name LLC"), _cover("f-2", "Same Name LLC")],
            [_info("f-1"), _info("f-2")])
        self.assertEqual(len(FL.load_filings(z, PERIOD).accessions), 2)

    def test_unparseable_date_costs_one_filing_not_the_quarter(self):
        z = self.make_zip([_sub("g-1", "0000999", filed="NOT-A-DATE")],
                          [_cover("g-1", "Fund")], [_info("g-1")])
        fs = FL.load_filings(z, PERIOD)
        self.assertEqual(fs.accessions, {"g-1"})
        self.assertIsNone(fs.filings["g-1"].filed)


# ──────────────────────── 2. the units gate ──────────────────────────
class TestUnitsGate(unittest.TestCase):

    def test_shares_convention_passes_through(self):
        units, shares = HD.classify_units(1744.0, 10.0, reference=174.4)
        self.assertEqual(units, HD.UNITS_SHARES)
        self.assertEqual(shares, 10.0)

    def test_contracts_convention_is_rescaled(self):
        """100x the reference means SSHPRNAMT held contracts, not shares."""
        units, shares = HD.classify_units(174400.0, 10.0, reference=174.4)
        self.assertEqual(units, HD.UNITS_CONTRACTS)
        self.assertEqual(shares, 1000.0)

    def test_neither_convention_is_refused_not_guessed(self):
        """Real rows sit ~28,000x off the reference. No rescaling rule
        should rationalise those; they leave the totals entirely."""
        units, shares = HD.classify_units(4_862_190.0, 1.0, reference=174.4)
        self.assertEqual(units, HD.UNITS_UNRELIABLE)
        self.assertEqual(shares, 0.0)

    def test_missing_reference_takes_the_row_as_filed(self):
        """An unverifiable row is not evidence of an error — refusing all
        of them would delete every thinly-held name from the output."""
        units, shares = HD.classify_units(1744.0, 10.0, reference=None)
        self.assertEqual(units, HD.UNITS_SHARES)
        self.assertEqual(shares, 10.0)

    def test_zero_shares_is_never_reliable(self):
        units, shares = HD.classify_units(1744.0, 0.0, reference=174.4)
        self.assertEqual(units, HD.UNITS_UNRELIABLE)
        self.assertEqual(shares, 0.0)


class TestScan(ZipFixture):

    def test_reference_price_comes_from_share_rows(self):
        """Share rows are numerous and consistent; option rows are not.
        A contracts-reporting option row must not drag the reference up."""
        infos = [_info("g-1", value=17440.0, shares=100.0) for _ in range(5)]
        infos.append(_info("g-1", value=1744000.0, shares=100.0, put_call="Call"))
        z = self.make_zip([_sub("g-1", "0000999")], [_cover("g-1", "Fund")], infos)
        scan = HD.scan(z, FL.load_filings(z, PERIOD).accessions)
        self.assertAlmostEqual(scan.reference_price["67066G104"], 174.4, places=4)
        self.assertEqual(scan.options[0].units, HD.UNITS_CONTRACTS)
        self.assertEqual(scan.options[0].underlying_shares, 10000.0)
        self.assertEqual(scan.options[0].contracts, 100.0)

    def test_only_option_rows_are_returned(self):
        z = self.make_zip([_sub("h-1", "0001000")], [_cover("h-1", "Fund")],
                          [_info("h-1"), _info("h-1", put_call="Put"),
                           _info("h-1", put_call="Call")])
        scan = HD.scan(z, FL.load_filings(z, PERIOD).accessions)
        self.assertEqual({r.put_call for r in scan.options}, {"Put", "Call"})
        self.assertEqual(scan.share_rows, 1)

    def test_rows_from_superseded_filings_never_reach_the_scan(self):
        """The dedupe in filings.py is only worth anything if holdings.py
        honours it — this is where double-counting would happen."""
        z = self.make_zip(
            [_sub("i-1", "0001100", filed="15-MAY-2026"),
             _sub("i-2", "0001100", form="13F-HR/A", filed="28-MAY-2026")],
            [_cover("i-1", "Fund"),
             _cover("i-2", "Fund", "Y", "RESTATEMENT")],
            [_info("i-1", put_call="Put"), _info("i-2", put_call="Put")])
        scan = HD.scan(z, FL.load_filings(z, PERIOD).accessions)
        self.assertEqual(len(scan.options), 1)
        self.assertEqual(scan.skipped_rows, 1)

    def test_declared_type_does_not_decide_units(self):
        """Option rows carry PRN while behaving exactly like share rows,
        so the arithmetic outranks the declared SSHPRNAMTTYPE."""
        infos = [_info("j-1", value=17440.0, shares=100.0) for _ in range(5)]
        infos.append(_info("j-1", value=17440.0, shares=100.0,
                           put_call="Call", ptype="PRN"))
        z = self.make_zip([_sub("j-1", "0001200")], [_cover("j-1", "Fund")], infos)
        scan = HD.scan(z, FL.load_filings(z, PERIOD).accessions)
        self.assertEqual(scan.options[0].units, HD.UNITS_SHARES)
        self.assertEqual(scan.options[0].underlying_shares, 100.0)


# ─────────────────── 3. quarter arithmetic terminates ────────────────
class TestQuarterArithmetic(unittest.TestCase):

    def test_previous_quarter_end_always_moves_backwards(self):
        for day, expected in [
            (dt.date(2026, 9, 30), dt.date(2026, 6, 30)),
            (dt.date(2026, 1, 1), dt.date(2025, 12, 31)),
            (dt.date(2026, 3, 31), dt.date(2025, 12, 31)),
            (dt.date(2026, 12, 31), dt.date(2026, 9, 30)),
        ]:
            with self.subTest(day=day):
                self.assertEqual(DS.previous_quarter_end(day), expected)

    def test_walking_back_from_a_quarter_end_terminates(self):
        """quarter_end(period - 1 day) returns `period` unchanged when it
        is already a quarter end, and the search loop never exits."""
        period = dt.date(2026, 9, 30)
        for _ in range(8):
            period = DS.previous_quarter_end(period)
        self.assertEqual(period, dt.date(2024, 9, 30))

    def test_quarter_end(self):
        for day, expected in [
            (dt.date(2026, 8, 22), dt.date(2026, 9, 30)),
            (dt.date(2026, 3, 31), dt.date(2026, 3, 31)),
            (dt.date(2026, 1, 5), dt.date(2026, 3, 31)),
        ]:
            with self.subTest(day=day):
                self.assertEqual(DS.quarter_end(day), expected)


class TestDatasetSelection(unittest.TestCase):
    Q1 = DS.Dataset("u/01mar2026-31may2026_form13f.zip",
                    dt.date(2026, 3, 1), dt.date(2026, 5, 31))
    Q4 = DS.Dataset("u/01dec2025-28feb2026_form13f.zip",
                    dt.date(2025, 12, 1), dt.date(2026, 2, 28))

    def test_resolves_on_filing_deadline_not_period_end(self):
        """Q1 filings are due May 15 and land in the March-May zip.
        Matching on period end would pick the previous quarter's file."""
        pool = [self.Q1, self.Q4]
        self.assertIs(DS.dataset_for_period(dt.date(2026, 3, 31), pool), self.Q1)
        self.assertIs(DS.dataset_for_period(dt.date(2025, 12, 31), pool), self.Q4)

    def test_unpublished_quarter_returns_none_not_an_error(self):
        """The current quarter lacking a zip is normal, and the caller's
        cue to fall back a quarter rather than to fail."""
        self.assertIsNone(DS.dataset_for_period(dt.date(2026, 6, 30), [self.Q1]))

    def test_index_parses_both_naming_schemes(self):
        html = ('<a href="/files/structureddata/data/form-13f-data-sets/'
                '01mar2026-31may2026_form13f.zip">a</a>'
                '<a href="/files/structureddata/data/form-13f-data-sets/'
                '2023q4_form13f.zip">b</a>')
        found = DS._parse_index(html)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].end, dt.date(2026, 5, 31))
        self.assertEqual(found[1].start, dt.date(2023, 10, 1))
        self.assertEqual(found[1].end, dt.date(2023, 12, 31))


# ──────────────────── 4. name normalisation ──────────────────────────
class TestNormalisation(unittest.TestCase):

    SAME = [
        ("NVIDIA CORPORATION", "NVIDIA Corp"),
        ("NVIDIA CORPORATION COM", "NVIDIA CORP COMMON STOCK"),
        ("AMERICAN TOWER CORP /MA/", "American Tower Corporation"),
        ("PULTEGROUP INC/MI/", "PulteGroup, Inc."),
        ("VERTEX PHARMACEUTICALS INC / MA", "Vertex Pharmaceuticals Inc"),
        ("HORTON D R INC /DE/", "D R Horton Inc"),
        ("HUNT J B TRANSPORT SERVICES INC", "J B Hunt Transport Services"),
        ("WELLS FARGO & COMPANY/MN", "Wells Fargo & Co"),
    ]

    def test_collapses_the_same_issuer(self):
        for a, b in self.SAME:
            with self.subTest(a=a):
                self.assertEqual(CU.normalise(a), CU.normalise(b))

    def test_keeps_different_issuers_apart(self):
        self.assertNotEqual(CU.normalise("MICROSOFT CORP"),
                            CU.normalise("MICROCHIP TECH INC"))

    def test_fund_words_are_not_global_noise(self):
        """An asset manager's stock shares a name with the funds it runs.
        Stripping FUND/ETF/TRUST globally collapses "BLACKROCK INC" into
        the same bucket as "BLACKROCK ETF TRUST", dominance falls under the
        threshold, and BLK stops resolving — measured at 5 operating
        companies lost (BLK, GS, TROW, VTV, VXUS) to recover 4 ETFs."""
        self.assertNotEqual(CU.normalise("BLACKROCK INC"),
                            CU.normalise("BLACKROCK ETF TRUST"))
        self.assertNotEqual(CU.normalise("T ROWE PRICE GROUP INC"),
                            CU.normalise("T ROWE PRICE ETF TRUST"))

    def test_fund_key_drops_wrapper_words(self):
        """The combined key must match a product name against the way it
        is filed: issuer "ISHARES TR", class "RUSSELL 2000 ETF"."""
        self.assertEqual(CU.normalise("ISHARES TR RUSSELL 2000 ETF", fund=True),
                         CU.normalise("iShares Russell 2000 ETF", fund=True))

    def test_survives_an_all_noise_name(self):
        """Stripping every token would key thousands of issuers to the
        empty string and collapse them into a single bucket."""
        self.assertNotEqual(CU.normalise("The Holdings Company Inc"), "")


class TestIndexCache(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_stale_cached_index_is_detected(self):
        """A v1 index resolves nothing under v2 rules — a total failure
        that looks exactly like a bad quarter unless it is caught."""
        path = self.tmp / "cusip_index.json"
        path.write_text(json.dumps({"version": 1, "names": {}, "display": {}}))
        with self.assertRaises(CU.StaleIndex):
            CU.load_index(path)

    def test_round_trip(self):
        path = self.tmp / "cusip_index.json"
        CU.build_name_index.display = {"67066G104": "NVIDIA CORPORATION"}
        CU.save_index({"nvidia": Counter({"67066G104": 10})}, path)
        index, display = CU.load_index(path)
        self.assertEqual(index["nvidia"]["67066G104"], 10)
        self.assertEqual(display["67066G104"], "NVIDIA CORPORATION")


# ─────────────────── 5. ticker resolution honesty ────────────────────
class TestResolution(unittest.TestCase):
    TICKERS = {"NVDA": "NVIDIA Corp", "GOOGL": "Alphabet Inc.",
               "GOOG": "Alphabet Inc."}
    ALPHABET = Counter({"02079K305": 8237, "02079K107": 7131})

    def test_dominant_name_resolves(self):
        index = {CU.normalise("NVIDIA Corp"): Counter({"67066G104": 8000,
                                                       "67066G999": 3})}
        r = CU.resolve("NVDA", index, self.TICKERS, {}, {})
        self.assertEqual(r.status, "resolved")
        self.assertEqual(r.cusip, "67066G104")
        self.assertGreater(r.confidence, 0.99)

    def test_dual_class_is_ambiguous_never_collapsed(self):
        """One company, one CIK, one name, two CUSIPs. Taking the bigger
        would attribute Class C's book to Class A."""
        index = {CU.normalise("Alphabet Inc."): self.ALPHABET}
        r = CU.resolve("GOOGL", index, self.TICKERS, {}, {})
        self.assertEqual(r.status, "ambiguous")
        self.assertIsNone(r.cusip)
        self.assertFalse(r.usable)
        self.assertEqual([c["cusip"] for c in r.candidates[:2]],
                         ["02079K305", "02079K107"])

    def test_override_pins_an_ambiguous_ticker(self):
        index = {CU.normalise("Alphabet Inc."): self.ALPHABET}
        r = CU.resolve("GOOG", index, self.TICKERS, {"GOOG": "02079K107"}, {})
        self.assertEqual(r.status, "override")
        self.assertEqual(r.cusip, "02079K107")
        self.assertTrue(r.usable)

    def test_too_few_rows_is_unknown_however_dominant(self):
        index = {CU.normalise("NVIDIA Corp"): Counter({"67066G104": 2})}
        self.assertEqual(
            CU.resolve("NVDA", index, self.TICKERS, {}, {}).status, "unknown")

    def test_fund_falls_back_to_the_combined_key(self):
        """ETFs are absent from the SEC's registrant file — that file lists
        REGISTRANTS, and SPY/IWM/QQQ are not among them. Without this path
        the whole index-positioning half of the data is unreachable."""
        combined = {CU.normalise("ISHARES TR RUSSELL 2000 ETF", fund=True):
                    Counter({"464287655": 4000})}
        r = CU.resolve("IWM", {}, {}, {}, {},
                       fund_names={"IWM": "iShares Russell 2000 ETF"},
                       combined_index=combined)
        self.assertEqual(r.status, "resolved")
        self.assertEqual(r.cusip, "464287655")

    def test_registrant_match_wins_over_a_fund_name(self):
        """An operating company must never be resolved by a fund name that
        happens to share tokens with it."""
        index = {CU.normalise("NVIDIA Corp"): Counter({"67066G104": 8000})}
        combined = {CU.normalise("NVIDIA FUND", fund=True):
                    Counter({"999999999": 9000})}
        r = CU.resolve("NVDA", index, self.TICKERS, {}, {},
                       fund_names={"NVDA": "NVIDIA FUND"},
                       combined_index=combined)
        self.assertEqual(r.cusip, "67066G104")

    def test_unlisted_ticker_is_unknown(self):
        self.assertEqual(CU.resolve("ZZZZ", {}, self.TICKERS, {}, {}).status,
                         "unknown")

    def test_documentation_keys_are_not_tickers(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cusip_overrides.json"
            path.write_text(json.dumps({"_comment": "notes",
                                        "GOOG": "02079K107"}))
            self.assertEqual(CU.load_overrides(path), {"GOOG": "02079K107"})


# ───────────── 6. the leaderboard shows its reinterpretations ────────
class TestLeaderboard(unittest.TestCase):
    """A rescale is a reading of the filing, not the filing as written.
    One rescaled row can outrank every honest row beneath it — the sample
    quarter has an Eversource put line that is 100% rescaled and lands in
    the global top fifteen on the strength of that single row."""

    @staticmethod
    def _row(cusip, put_call, shares, units):
        return HD.OptionRow(accession="x", cusip=cusip, issuer="ISSUER",
                            title_of_class="COM", put_call=put_call,
                            value_usd=1.0, shares_as_filed=shares / 100.0,
                            units=units, underlying_shares=shares)

    def _ctx(self, rows):
        by_cusip = {}
        for r in rows:
            by_cusip.setdefault(r.cusip, []).append(r)
        return {"by_cusip": by_cusip, "display": {}}

    def test_fully_rescaled_line_is_flagged(self):
        ctx = self._ctx([self._row("A", "Put", 1_000_000, HD.UNITS_CONTRACTS)])
        self.assertEqual(EN.leaderboard(ctx, "Put")[0]["rescaled_share"], 1.0)

    def test_untouched_line_is_not_flagged(self):
        ctx = self._ctx([self._row("B", "Put", 1_000_000, HD.UNITS_SHARES)])
        self.assertEqual(EN.leaderboard(ctx, "Put")[0]["rescaled_share"], 0.0)

    def test_mixed_line_reports_the_proportion(self):
        ctx = self._ctx([self._row("C", "Put", 750_000, HD.UNITS_CONTRACTS),
                         self._row("C", "Put", 250_000, HD.UNITS_SHARES)])
        self.assertEqual(EN.leaderboard(ctx, "Put")[0]["rescaled_share"], 0.75)

    def test_unreliable_rows_are_excluded_entirely(self):
        ctx = self._ctx([self._row("D", "Put", 100.0, HD.UNITS_SHARES),
                         self._row("D", "Put", 0.0, HD.UNITS_UNRELIABLE)])
        self.assertEqual(EN.leaderboard(ctx, "Put")[0]["contracts"], 1.0)

    def test_sides_do_not_bleed_into_each_other(self):
        ctx = self._ctx([self._row("E", "Put", 100.0, HD.UNITS_SHARES),
                         self._row("E", "Call", 900.0, HD.UNITS_SHARES)])
        self.assertEqual(EN.leaderboard(ctx, "Put")[0]["contracts"], 1.0)
        self.assertEqual(EN.leaderboard(ctx, "Call")[0]["contracts"], 9.0)

    def test_cusip_disambiguates_shared_issuer_names(self):
        """Every iShares ETF files as "ISHARES TR"; without the CUSIP the
        leaderboard is eight identical rows."""
        ctx = self._ctx([self._row("F1", "Put", 100.0, HD.UNITS_SHARES),
                         self._row("F2", "Put", 200.0, HD.UNITS_SHARES)])
        board = EN.leaderboard(ctx, "Put")
        self.assertEqual({r["cusip"] for r in board}, {"F1", "F2"})


if __name__ == "__main__":
    unittest.main()
