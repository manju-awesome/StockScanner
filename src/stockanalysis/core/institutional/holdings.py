"""
Reading the information table, and the units problem inside it.

WHAT A PUT OR CALL ROW ACTUALLY MEANS
-------------------------------------
13F reports LONG positions only. A row marked Put means the manager HELD
puts on the quarter-end date. It does not mean they sold puts, and there
is no row anywhere in this dataset for a written option or a short stock
position. So "institutions are loaded with puts on X" is a statement
about who BOUGHT protection or downside, and the other side of every one
of those trades is invisible here by design.

Read it as positioning, never as flow: it is one photograph taken on the
last day of the quarter, filed up to 45 days later. A position opened and
closed inside the quarter leaves no trace at all.

THE UNITS PROBLEM
-----------------
SSHPRNAMT is meant to be the number of UNDERLYING SHARES the option
covers. A meaningful minority of filers put the number of CONTRACTS there
instead, understating the position by exactly 100x. Both filings pass the
SEC's validation, and nothing in the row says which convention was used.

The tell is arithmetic. VALUE divided by SSHPRNAMT should come out near
the stock's quarter-end price. For the same CUSIP in the same quarter:

    median implied price over share rows :   174.4   <- the real price
    a well-formed option row             :   174.4   -> shares, as instructed
    a contracts-reporting option row     : 17440.0   -> exactly 100x

So the reference price comes from the SHARE rows, which are numerous and
overwhelmingly consistent, and every option row is then checked against
it. Rows at ~100x are rescaled; rows near 1x are taken as filed; rows at
neither are marked unreliable and kept OUT of totals rather than being
guessed at. In the sample quarter the worst outlier sat at ~28,000x the
reference, which no rescaling rule should ever be asked to rationalise.

SSHPRNAMTTYPE is deliberately NOT used to make this call. It is supposed
to say SH or PRN, but option rows carry PRN while behaving exactly like
share rows, so the declared type is less reliable than the arithmetic.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .filings import INFOTABLE

# How many implied prices to keep per CUSIP when deriving its reference.
# A median over the first 64 share rows is stable well past the precision
# this needs, and bounds memory across ~20k CUSIPs.
PRICE_SAMPLE = 64

# Tolerance bands around the reference price. Wide, because VALUE is
# quarter-end fair value and filers round, revalue and occasionally report
# option premium instead of underlying value. The bands only need to
# separate 1x from 100x, and anything that lands between them is a row
# nobody should be quietly rescaling.
SHARES_BAND = (0.25, 4.0)
CONTRACTS_BAND = (25.0, 400.0)

UNITS_SHARES = "shares"
UNITS_CONTRACTS = "contracts"
UNITS_UNRELIABLE = "unreliable"


@dataclass
class OptionRow:
    """One institution's long option position in one issuer."""
    accession: str
    cusip: str
    issuer: str
    title_of_class: str
    put_call: str                     # "Put" | "Call"
    value_usd: float                  # as filed
    shares_as_filed: float
    units: str                        # which convention the row used
    underlying_shares: float          # normalised; 0.0 when unreliable

    @property
    def contracts(self) -> float:
        return self.underlying_shares / 100.0

    @property
    def reliable(self) -> bool:
        return self.units != UNITS_UNRELIABLE


@dataclass
class ScanResult:
    options: list[OptionRow]
    reference_price: dict[str, float]
    share_rows: int = 0
    option_rows: int = 0
    skipped_rows: int = 0             # rows from superseded/other-period filings


def _f(text: str) -> float | None:
    try:
        v = float((text or "").strip())
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def classify_units(value_usd: float, shares: float,
                   reference: float | None) -> tuple[str, float]:
    """Decide what SSHPRNAMT meant on this row, and normalise it.

    With no reference price the row is taken as filed — an unverifiable
    row is not evidence of a 100x error, and marking every thinly-held
    CUSIP unreliable would delete the small-cap tail wholesale.
    """
    if shares <= 0:
        return UNITS_UNRELIABLE, 0.0
    if not reference or reference <= 0 or value_usd <= 0:
        return UNITS_SHARES, shares
    implied = value_usd / shares
    ratio = implied / reference
    if SHARES_BAND[0] <= ratio <= SHARES_BAND[1]:
        return UNITS_SHARES, shares
    if CONTRACTS_BAND[0] <= ratio <= CONTRACTS_BAND[1]:
        return UNITS_CONTRACTS, shares * 100.0
    return UNITS_UNRELIABLE, 0.0


def scan(zip_path: Path, accessions: set[str],
         cusips: set[str] | None = None) -> ScanResult:
    """Single streaming pass over the information table.

    One pass, not two: the table is ~400 MB and 3.8M rows, so option rows
    are buffered while share rows are sampled for their reference price,
    and the units call is made at the end once every reference is known.
    """
    raw_options: list[tuple] = []
    samples: dict[str, list[float]] = {}
    share_rows = option_rows = skipped = 0

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(INFOTABLE) as fh:
            stream = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
            for row in csv.DictReader(stream, delimiter="\t"):
                if row.get("ACCESSION_NUMBER") not in accessions:
                    skipped += 1
                    continue
                cusip = (row.get("CUSIP") or "").strip().upper()
                if cusips is not None and cusip not in cusips:
                    continue
                value = _f(row.get("VALUE"))
                shares = _f(row.get("SSHPRNAMT"))
                put_call = (row.get("PUTCALL") or "").strip()

                if not put_call:
                    share_rows += 1
                    bucket = samples.setdefault(cusip, [])
                    if len(bucket) < PRICE_SAMPLE and value and shares and shares > 0:
                        bucket.append(value / shares)
                    continue

                option_rows += 1
                raw_options.append((
                    row.get("ACCESSION_NUMBER"), cusip,
                    (row.get("NAMEOFISSUER") or "").strip(),
                    (row.get("TITLEOFCLASS") or "").strip(),
                    put_call.title(), value or 0.0, shares or 0.0,
                ))

    reference = {c: median(v) for c, v in samples.items() if v}

    options = []
    for acc, cusip, issuer, title, put_call, value, shares in raw_options:
        units, underlying = classify_units(value, shares, reference.get(cusip))
        options.append(OptionRow(
            accession=acc, cusip=cusip, issuer=issuer, title_of_class=title,
            put_call=put_call, value_usd=value, shares_as_filed=shares,
            units=units, underlying_shares=underlying))

    return ScanResult(options=options, reference_price=reference,
                      share_rows=share_rows, option_rows=option_rows,
                      skipped_rows=skipped)
