"""
SEC bulk 13F dataset discovery, download and cache.

WHY BULK AND NOT PER-FILER
--------------------------
The question this package answers — "which institutions hold calls or puts
on TICKER" — is a query across every filer at once, not a lookup of one
filer. Answering it from EDGAR one filer at a time means two requests per
institution (submissions index, then the information-table XML) across
~8,700 13F filers: ~17,000 requests, and at the SEC's 10 req/s ceiling
that is most of an hour per quarter.

The SEC publishes the same data as one quarterly zip of TSVs. One
request, ~95 MB, and it contains every filer's full information table.

THE COST OF THAT CHOICE: STALENESS
----------------------------------
The bulk zips are published on a lag AFTER the filing window closes, so
there is a stretch of ~6 weeks each quarter where individual 13Fs are on
EDGAR but no bulk file contains them yet. Concretely: Q2 filings are due
Aug 14, and the June-August zip lands around Sep 1. Ask this package for
data on Aug 22 and the newest COMPLETE quarter it can give you is Q1 —
even though Q2 filings have been public for a week.

That is a real limitation, not a bug, and `engine.py` reports the period
it actually served so the staleness is never silent. If you need the
current quarter before the zip exists, that is the per-filer path, and it
has to be scoped to a curated filer list to be practical.

FILING WINDOWS, NOT REPORT PERIODS
----------------------------------
Each zip is named for the range of dates on which filings were RECEIVED,
not the quarter they describe. `01mar2026-31may2026_form13f.zip` holds
everything filed in that window, which is mostly period-2026-03-31
reports (due May 15) plus late filings and amendments for older quarters.
So resolving a report period to a zip means asking which window contains
that period's DUE date, not its end date — see `dataset_for_period`.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
_BASE = "https://www.sec.gov"

CACHE_DIR = Path(__file__).resolve().parents[4] / "data" / "cache" / "13f"

# 13F is due 45 days after the quarter it reports on.
FILING_DEADLINE_DAYS = 45

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# Two naming schemes live side by side on the index page: the SEC switched
# from calendar quarters to explicit filing windows after 2023q4.
_RANGE_RE = re.compile(
    r"/files/structureddata/data/form-13f-data-sets/"
    r"(\d{2})([a-z]{3})(\d{4})-(\d{2})([a-z]{3})(\d{4})_form13f\.zip")
_QUARTER_RE = re.compile(
    r"/files/structureddata/data/form-13f-data-sets/"
    r"(\d{4})q([1-4])_form13f\.zip")


class SECAccessError(RuntimeError):
    """Raised when the SEC cannot or should not be called."""


@dataclass(frozen=True)
class Dataset:
    """One published zip, and the window of FILING dates it covers."""
    url: str
    start: _dt.date
    end: _dt.date

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    def covers(self, day: _dt.date) -> bool:
        return self.start <= day <= self.end


def user_agent() -> str:
    """The SEC requires a declared identity with a contact address on every
    request, and rejects generic agents. Nothing here is hardcoded: an
    address in a committed file is both a privacy leak and wrong the moment
    somebody else runs this.

    SEC_USER_AGENT wins; otherwise an address already configured for alerts
    is reused, since it is the same person either way.
    """
    explicit = os.environ.get("SEC_USER_AGENT", "").strip()
    if explicit:
        return explicit
    email = (os.environ.get("ALERT_EMAIL_TO")
             or os.environ.get("ALERT_EMAIL_FROM")
             or os.environ.get("RESEND_FROM_EMAIL") or "").strip()
    if email:
        return f"StockAnalysis 13F research ({email})"
    raise SECAccessError(
        "SEC requests need a contact address. Set SEC_USER_AGENT in .env, "
        "e.g. SEC_USER_AGENT='StockAnalysis 13F research (you@example.com)'.")


def _get(url: str, timeout: int = 120) -> bytes:
    # No Accept-Encoding: urllib does not transparently decompress, so
    # asking for gzip returns bytes that every parser downstream will read
    # as an empty page rather than as an error. The zip is already
    # compressed and the index page is small, so identity costs nothing.
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent(),
        "Host": "www.sec.gov",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_index(html: str) -> list[Dataset]:
    found: dict[str, Dataset] = {}
    for m in _RANGE_RE.finditer(html):
        d1, m1, y1, d2, m2, y2 = m.groups()
        try:
            start = _dt.date(int(y1), _MONTHS[m1], int(d1))
            end = _dt.date(int(y2), _MONTHS[m2], int(d2))
        except (KeyError, ValueError):
            continue
        url = _BASE + m.group(0)
        found[url] = Dataset(url, start, end)
    for m in _QUARTER_RE.finditer(html):
        year, q = int(m.group(1)), int(m.group(2))
        start = _dt.date(year, 3 * (q - 1) + 1, 1)
        end_month = 3 * q
        last = _dt.date(year + end_month // 12, end_month % 12 + 1, 1) - _dt.timedelta(days=1)
        url = _BASE + m.group(0)
        found[url] = Dataset(url, start, last)
    return sorted(found.values(), key=lambda d: d.end, reverse=True)


def available_datasets() -> list[Dataset]:
    """Every published zip, newest filing window first.

    Scraped rather than constructed, because the naming scheme has already
    changed once and a constructed URL would 404 silently against a future
    change. A 404 here should mean "not published yet", nothing else.
    """
    return _parse_index(_get(INDEX_URL, timeout=60).decode("utf-8", "replace"))


def quarter_end(day: _dt.date) -> _dt.date:
    """The report period a date falls in — 13F periods are quarter ends."""
    q_end_month = 3 * ((day.month - 1) // 3 + 1)
    nxt = _dt.date(day.year + q_end_month // 12, q_end_month % 12 + 1, 1)
    return nxt - _dt.timedelta(days=1)


def previous_quarter_end(period: _dt.date) -> _dt.date:
    """The quarter end before this one.

    Not `quarter_end(period - 1 day)`: for a date that is ALREADY a quarter
    end that returns the same date and any walk-backwards loop spins
    forever. Stepping off the front of the quarter is the only move that
    always lands in the previous one.
    """
    quarter_start = _dt.date(period.year, 3 * ((period.month - 1) // 3) + 1, 1)
    return quarter_start - _dt.timedelta(days=1)


def dataset_for_period(period: _dt.date,
                       datasets: list[Dataset] | None = None) -> Dataset | None:
    """The zip that should contain on-time filings for `period`.

    Returns None when that zip has not been published yet — the normal
    state for the most recent quarter, and the caller's cue to fall back a
    quarter rather than to report an error.
    """
    due = period + _dt.timedelta(days=FILING_DEADLINE_DAYS)
    for ds in (datasets if datasets is not None else available_datasets()):
        if ds.covers(due):
            return ds
    return None


def latest_complete_period(today: _dt.date | None = None,
                           datasets: list[Dataset] | None = None
                           ) -> tuple[_dt.date, Dataset]:
    """Newest report period for which a bulk zip actually exists.

    Walks backwards a quarter at a time. The first hit is the freshest
    complete picture available through this path; how far behind `today`
    it is, is exactly the staleness the module header describes.
    """
    today = today or _dt.date.today()
    datasets = datasets if datasets is not None else available_datasets()
    period = quarter_end(today)
    for _ in range(8):
        ds = dataset_for_period(period, datasets)
        if ds is not None:
            return period, ds
        period = previous_quarter_end(period)
    raise SECAccessError(
        "No published 13F dataset found for the last 8 quarters — the SEC "
        f"index at {INDEX_URL} may have changed shape.")


def ensure_dataset(ds: Dataset, cache_dir: Path | None = None,
                   force: bool = False) -> Path:
    """Download the zip unless it is already cached.

    Cached under data/cache/, which is gitignored: these are ~95 MB of
    re-downloadable public data and have no business in the repository.

    Written to a .part file and renamed on success, so an interrupted
    download can never be mistaken for a complete one on the next run.
    """
    cache_dir = cache_dir or CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / ds.filename
    if target.exists() and not force:
        return target
    part = target.with_suffix(".part")
    payload = _get(ds.url, timeout=600)
    part.write_bytes(payload)
    part.replace(target)
    return target
