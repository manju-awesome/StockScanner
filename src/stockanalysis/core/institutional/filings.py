"""
Which filings count — amendment resolution over the 13F submission tables.

A quarter's zip is not a clean one-row-per-institution picture. The same
manager can appear several times for the same period, and treating those
as separate holders is the single easiest way to produce a number that is
confidently, invisibly wrong.

Three things have to be resolved:

1. 13F-NT is a NOTICE. It says "my holdings are reported by another
   manager" and carries no information table at all. Counting notices as
   filers inflates the holder count while contributing nothing; they are
   dropped, not zero-filled.

2. A RESTATEMENT amendment (13F-HR/A) REPLACES the original filing in
   full. Keeping both double-counts every position the manager did not
   change — which is most of them.

3. A NEW HOLDINGS amendment ADDS rows to the original, which stands. Here
   dropping the original is the error, and it loses real positions.

The SEC distinguishes 2 from 3 only via COVERPAGE.AMENDMENTTYPE, so that
column is load-bearing. When it is blank on something flagged as an
amendment — which happens — this treats it as a restatement, because
over-replacing loses data that was probably re-stated anyway, while
over-adding invents positions that never existed. Losing a row is
recoverable by reading the filing; a fabricated position is not.

A LATE filing for an older period sits in whatever zip it arrived in, so
every load is filtered by PERIODOFREPORT rather than trusting the zip's
name to imply a quarter.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# The information table is the big one; the rest are small enough to hold.
SUBMISSION = "SUBMISSION.tsv"
COVERPAGE = "COVERPAGE.tsv"
INFOTABLE = "INFOTABLE.tsv"

HOLDINGS_FORMS = {"13F-HR", "13F-HR/A"}

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def parse_sec_date(text: str) -> _dt.date | None:
    """SEC TSVs use DD-MON-YYYY. Returns None rather than raising: one
    unparseable date should cost that filing, not the whole quarter."""
    text = (text or "").strip().upper()
    parts = text.split("-")
    if len(parts) != 3:
        return None
    try:
        return _dt.date(int(parts[2]), _MONTHS[parts[1]], int(parts[0]))
    except (KeyError, ValueError):
        return None


@dataclass
class Filing:
    accession: str
    cik: str
    manager: str
    period: _dt.date | None
    filed: _dt.date | None
    form: str
    is_amendment: bool
    amendment_type: str
    additive: bool = False           # NEW HOLDINGS amendment: adds, replaces nothing


@dataclass
class FilingSet:
    """The resolved set of filings to read holdings from, plus what was
    dropped getting there — the drops are the audit trail."""
    period: _dt.date
    filings: dict[str, Filing] = field(default_factory=dict)
    superseded: list[str] = field(default_factory=list)
    notices: int = 0
    managers: int = 0

    @property
    def accessions(self) -> set[str]:
        return set(self.filings)


def _read_tsv(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        yield from csv.DictReader(stream, delimiter="\t")


def load_filings(zip_path: Path, period: _dt.date) -> FilingSet:
    """Resolve the filings that describe `period`, one per manager."""
    result = FilingSet(period=period)

    with zipfile.ZipFile(zip_path) as zf:
        subs: dict[str, dict] = {}
        for row in _read_tsv(zf, SUBMISSION):
            if parse_sec_date(row.get("PERIODOFREPORT", "")) != period:
                continue
            form = (row.get("SUBMISSIONTYPE") or "").strip().upper()
            if form not in HOLDINGS_FORMS:
                result.notices += 1
                continue
            subs[row["ACCESSION_NUMBER"]] = row

        covers: dict[str, dict] = {}
        for row in _read_tsv(zf, COVERPAGE):
            acc = row.get("ACCESSION_NUMBER")
            if acc in subs:
                covers[acc] = row

    # Group by manager. CIK is the identity, not the name: managers rename
    # themselves and file under variant spellings, and a name-keyed group
    # would split one institution into several holders.
    by_cik: dict[str, list[Filing]] = {}
    for acc, sub in subs.items():
        cover = covers.get(acc, {})
        is_amend = (cover.get("ISAMENDMENT") or "").strip().upper() == "Y"
        amend_type = (cover.get("AMENDMENTTYPE") or "").strip().upper()
        f = Filing(
            accession=acc,
            cik=(sub.get("CIK") or "").strip().lstrip("0") or "0",
            manager=(cover.get("FILINGMANAGER_NAME") or "").strip(),
            period=period,
            filed=parse_sec_date(sub.get("FILING_DATE", "")),
            form=(sub.get("SUBMISSIONTYPE") or "").strip().upper(),
            is_amendment=is_amend or (sub.get("SUBMISSIONTYPE", "").endswith("/A")),
            amendment_type=amend_type,
            additive=is_amend and amend_type == "NEW HOLDINGS",
        )
        by_cik.setdefault(f.cik, []).append(f)

    _EPOCH = _dt.date(1900, 1, 1)
    for cik, group in by_cik.items():
        group.sort(key=lambda f: (f.filed or _EPOCH, f.accession))
        additive = [f for f in group if f.additive]
        replacing = [f for f in group if not f.additive]

        # Of everything that claims to state the whole book, only the last
        # one does. Earlier ones are superseded whether they were the
        # original or an intermediate restatement.
        if replacing:
            keep = replacing[-1]
            result.filings[keep.accession] = keep
            result.superseded.extend(f.accession for f in replacing[:-1])
        for f in additive:
            result.filings[f.accession] = f

    result.managers = len(by_cik)
    return result
